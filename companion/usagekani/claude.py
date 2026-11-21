"""Claude Code status-line bridge. Never stores transcripts or credentials."""
import json
import hashlib
import os
import sys
import time
from pathlib import Path
from .model import normalize_claude, validate_cached_provider


def state_dir():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Usagekani'


def ingest(payload, path, now):
    limits = payload.get('rate_limits')
    if not isinstance(limits, dict) or not any(k in limits for k in ('five_hour', 'seven_day')):
        return
    value = normalize_claude(payload, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix('.lock')
    deadline = time.monotonic() + 1
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 10:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                return
            time.sleep(0.01)
    try:
        _ordered_write(payload, value, path)
    finally:
        lock.unlink(missing_ok=True)


def _atomic_write(path, value):
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    try:
        temporary.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _ordered_write(payload, value, path):
    sources_path = path.with_suffix('.sources.json')
    try:
        sources = json.loads(sources_path.read_text(encoding='utf-8'))
        if not isinstance(sources, dict):
            sources = {}
    except (FileNotFoundError, ValueError):
        sources = {}
    session = hashlib.sha256(str(payload.get('session_id', 'unknown')).encode()).hexdigest()[:16]
    fingerprint = hashlib.sha256(json.dumps(value['windows'], sort_keys=True).encode()).hexdigest()
    if sources.get(session) == fingerprint:
        return  # A redraw is not a fresh upstream quota observation.
    sources.pop(session, None)
    sources[session] = fingerprint
    _atomic_write(sources_path, dict(list(sources.items())[-64:]))
    try:
        previous = validate_cached_provider(json.loads(path.read_text(encoding='utf-8')), value['observed_at'])
    except (FileNotFoundError, ValueError, TypeError):
        previous = {}
    if isinstance(previous, dict):
        if previous.get('observed_at', 0) > value['observed_at'] or previous.get('windows') == value['windows']:
            return
        for old, new in zip(previous.get('windows', []), value['windows']):
            if isinstance(old, dict) and new and old.get('resets_at') == new['resets_at'] and old.get('used_pct', 0) > new['used_pct']:
                return  # Another session's older snapshot of the same window.
    _atomic_write(path, value)


def main():
    try:
        payload = json.load(sys.stdin)
        ingest(payload, state_dir() / 'claude.json', int(time.time()))
        # Useful terminal output as well as the cube feed; no existing statusline
        # is overwritten by the installer without an explicit merge decision.
        value = normalize_claude(payload, int(time.time()))
        parts = [f'{label}: {w["used_pct"]:g}%' for label, w in zip(('5h', '7d'), value['windows']) if w]
        print('Claude | ' + ' | '.join(parts) if parts else 'Claude | Usagekani waiting')
    except (ValueError, OSError, TypeError, AttributeError):
        print('Claude | Usagekani unavailable')


if __name__ == '__main__':
    main()
