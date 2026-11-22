"""Bounded, persistent, text-free pending requests shared by observer hooks."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import time

from .process_identity import owner_alive

MAX_REQUESTS = 64
MAX_BYTES = 65536


def _empty():
    return {'version': 1, 'requests': {}, 'closed': {}, 'sessions': {}}


def _identity(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError('Invalid attention identifier')
    return hashlib.sha256(value.encode()).hexdigest()


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**64


def _owner(value):
    return (isinstance(value, dict) and set(value) == {'pid', 'started'}
            and _integer(value['pid']) and value['pid'] < 2**32 and _integer(value['started']))


def _load(path):
    try:
        with path.open('rb') as source:
            raw = source.read(MAX_BYTES+1)
        if len(raw) > MAX_BYTES:
            return _empty()
        state = json.loads(raw)
        if (not isinstance(state, dict) or state.get('version') != 1
                or any(not isinstance(state.get(k), dict) for k in ('requests', 'closed', 'sessions'))):
            return _empty()
        result = _empty()
        for key, entry in list(state['requests'].items())[:MAX_REQUESTS]:
            if (not re.fullmatch('[0-9a-f]{64}', key) or not isinstance(entry, dict)
                    or entry.get('provider') not in ('codex', 'claude')
                    or entry.get('kind') not in ('question', 'approval')
                    or not isinstance(entry.get('session'), str)
                    or not re.fullmatch('[0-9a-f]{64}', entry['session'])
                    or not _integer(entry.get('event_ms')) or not _owner(entry.get('owner'))):
                continue
            result['requests'][key] = {k: entry[k] for k in ('provider', 'kind', 'session', 'event_ms', 'owner')}
        for name, limit in (('closed', 128), ('sessions', 64)):
            result[name] = {key: value for key, value in list(state[name].items())[:limit]
                            if re.fullmatch('[0-9a-f]{64}', key) and _integer(value)}
        return result
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return _empty()


@contextmanager
def _lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix('.lock')
    deadline = time.monotonic()+2
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time()-lock.stat().st_mtime > 10:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError('Attention state busy')
            time.sleep(0.01)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def apply_event(path, provider, session, request, *, action='raise', kind='question', owner=None, event_ms=None):
    path = Path(path)
    event_ms = time.time_ns()//1000000 if event_ms is None else event_ms
    if provider not in ('codex', 'claude') or action not in ('raise', 'clear', 'clear_session') or not _integer(event_ms):
        raise ValueError('Invalid attention event')
    if action == 'raise' and (kind not in ('question', 'approval') or not _owner(owner)):
        raise ValueError('Invalid attention owner or kind')
    session_key = _identity(provider+'/'+session)
    request_key = _identity(session_key+'/'+request)
    with _lock(path):
        state = _load(path)
        if action == 'raise':
            entry = state['requests'].get(request_key)
            barrier = max(state['closed'].get(request_key, 0), state['sessions'].get(session_key, 0))
            if event_ms <= state['closed'].get(request_key, 0) or event_ms < barrier or (entry and event_ms < entry['event_ms']):
                return
            state['requests'][request_key] = {'provider': provider, 'session': session_key,
                'kind': kind, 'owner': dict(owner), 'event_ms': event_ms}
        elif action == 'clear':
            state['closed'][request_key] = max(event_ms, state['closed'].get(request_key, 0))
            entry = state['requests'].get(request_key)
            if entry and entry['event_ms'] <= event_ms:
                del state['requests'][request_key]
        else:
            state['sessions'][session_key] = max(event_ms, state['sessions'].get(session_key, 0))
            state['requests'] = {key: entry for key, entry in state['requests'].items()
                                 if entry['session'] != session_key or entry['event_ms'] > event_ms}
        state['requests'] = dict(sorted(state['requests'].items(), key=lambda item: item[1]['event_ms'])[-MAX_REQUESTS:])
        for name, limit in (('closed', 128), ('sessions', 64)):
            state[name] = dict(sorted(state[name].items(), key=lambda item: item[1])[-limit:])
        temporary = path.with_name(path.name+f'.{os.getpid()}.tmp')
        try:
            temporary.write_text(json.dumps(state, separators=(',', ':')), encoding='utf-8')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def read_attention(path, now, *, alive=owner_alive):
    result = {'codex': None, 'claude': None}
    state = _load(Path(path))
    for provider in result:
        entries = [entry for entry in state['requests'].values() if entry['provider'] == provider
                   and entry['event_ms'] <= (now+60)*1000 and alive(entry['owner'])]
        if entries:
            result[provider] = {'kind': 'question' if any(e['kind'] == 'question' for e in entries) else 'approval',
                                'count': len(entries), 'observed_at': int(now)}
    return result
