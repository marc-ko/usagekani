"""Read an explicitly configured Claude usage exporter over verified HTTPS.

Provider URL and bearer key belong in local companion settings, never firmware.
Windows curl uses the OS certificate store; the bundled MinGW Python's CA store
does not trust the tested endpoint. No certificate checks are disabled.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlsplit

from .model import normalize_claude


class ApiError(RuntimeError):
    def __init__(self, status='error'):
        self.status = status
        super().__init__(status)


def _epoch(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ApiError()
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Timezone is required')
        epoch = int(parsed.astimezone(timezone.utc).timestamp())
        if not 946684800 <= epoch <= 4102444800:
            raise ValueError('Timestamp out of bounds')
        return epoch
    except (ValueError, OverflowError) as error:
        raise ApiError() from error


def normalize(payload, now):
    if not isinstance(payload, dict) or 'error' in payload:
        raise ApiError()
    observed = _epoch(payload.get('fetched_at'))
    if observed > now + 60 or not any(k in payload for k in ('five_hour', 'seven_day')):
        raise ApiError()
    limits = {}
    for key in ('five_hour', 'seven_day'):
        window = payload.get(key)
        if window is None:
            continue
        if not isinstance(window, dict):
            raise ApiError()
        limits[key] = {'used_percentage': window.get('utilization'),
                       'resets_at': _epoch(window.get('resets_at'))}
    result = normalize_claude({'rate_limits': limits}, observed)
    for key, window in zip(('five_hour', 'seven_day'), result['windows']):
        if key in limits and window is None:
            raise ApiError()
    return result


def fetch_usage(url, key, *, curl=None):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or not isinstance(key, str) or not key
            or any(c in key for c in '\r\n')):
        raise ApiError()
    executable = curl
    if executable is None:
        executable = (str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/curl.exe')
                      if os.name == 'nt' else shutil.which('curl'))
    if not executable:
        raise ApiError()
    # Headers are passed on stdin, never in process listings or query strings.
    escaped = key.replace('\\', '\\\\').replace('"', '\\"')
    configuration = 'header = "Authorization: Bearer ' + escaped + '"\n'
    try:
        response = subprocess.run(
            [executable, '--disable', '--silent', '--show-error', '--proto', '=https', '--max-time', '15',
             '--max-filesize', '65536', '--config', '-', '--write-out', '\n%{http_code}', url],
            input=configuration, capture_output=True, text=True, encoding='utf-8',
            timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if response.returncode:
            raise ApiError()
        body, code = response.stdout.rsplit('\n', 1)
        if code != '200':
            raise ApiError('auth' if code == '401' else 'error')
        return json.loads(body)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise ApiError() from error


def collect(config, store, now=None):
    try:
        now = int(time.time()) if now is None else now
        value = normalize(fetch_usage(config['claude_usage_url'], config['claude_usage_key']), now)
        store.update('claude', value)
        return True
    except (ApiError, KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        store.fail('claude', error.status if isinstance(error, ApiError) else 'error')
        return False
