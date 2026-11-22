"""Normalize provider quotas without confusing missing data with zero usage."""
import copy
import math
import threading


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _window(value, now, *, claude=False, duration=None):
    if not isinstance(value, dict):
        return None
    used = value.get('used_percentage' if claude else 'usedPercent')
    reset = value.get('resets_at' if claude else 'resetsAt')
    minutes = duration if claude else value.get('windowDurationMins')
    if not (_number(used) and 0 <= used <= 100 and _number(reset) and 0 <= reset <= 4102444800
            and (reset != 0 or used == 0)
            and _number(minutes) and 0 < minutes <= 525600):
        return None
    return {'used_pct': round(used, 1), 'duration_mins': int(minutes), 'resets_at': int(reset)}


def _provider(windows, now):
    return {'status': 'ok' if any(w is not None for w in windows) else 'waiting',
            'observed_at': int(now), 'windows': windows}


def normalize_codex(result, now):
    buckets = result.get('rateLimitsByLimitId') or {}
    # Never silently select a model-specific bucket for the account-wide display.
    quota = buckets.get('codex') or result.get('rateLimits') or {}
    if not isinstance(quota, dict) or quota.get('limitId', 'codex') != 'codex':
        quota = {}
    return _provider([_window(quota.get(key), now) for key in ('primary', 'secondary')], now)


def normalize_claude(payload, now):
    quota = payload.get('rate_limits') or {}
    if not isinstance(quota, dict):
        quota = {}
    return _provider([_window(quota.get(key), now, claude=True, duration=duration)
                      for key, duration in (('five_hour', 300), ('seven_day', 10080))], now)


def validate_cached_provider(value, now):
    if not isinstance(value, dict) or value.get('status') not in ('ok', 'waiting'):
        raise ValueError('Invalid provider status')
    observed = value.get('observed_at')
    windows = value.get('windows')
    if not (_number(observed) and int(observed) == observed and 0 <= observed <= now + 60
            and isinstance(windows, list) and len(windows) == 2):
        raise ValueError('Invalid provider observation')
    normalized = []
    for item in windows:
        if item is None:
            normalized.append(None)
            continue
        if not isinstance(item, dict):
            raise ValueError('Invalid cached quota')
        window = _window({'usedPercent': item.get('used_pct'), 'resetsAt': item.get('resets_at'),
                          'windowDurationMins': item.get('duration_mins')}, now)
        if window is None:
            raise ValueError('Invalid cached quota')
        normalized.append(window)
    return _provider(normalized, observed)


class Store:
    def __init__(self):
        self._lock = threading.Lock()
        self._values = {p: _provider([None, None], 0) for p in ('codex', 'claude')}

    def update(self, provider, value):
        with self._lock:
            if value['observed_at'] >= self._values[provider]['observed_at']:
                self._values[provider] = copy.deepcopy(value)
            elif value['status'] in ('ok', 'waiting'):
                # A successful cached response resolves a collection error,
                # while newer known quota values and their age remain intact.
                self._values[provider]['status'] = (
                    'ok' if any(w is not None for w in self._values[provider]['windows']) else 'waiting')

    def fail(self, provider, status='error'):
        with self._lock:
            self._values[provider]['status'] = status

    def snapshot(self, now):
        with self._lock:
            result = copy.deepcopy(self._values)
        for provider in result.values():
            if provider['status'] == 'ok' and now - provider['observed_at'] > 300:
                provider['status'] = 'stale'
        return {'version': 1, 'now': int(now), **result}
