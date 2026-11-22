"""Behavior tests for provider data crossing the PC/device boundary."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from companion.usagekani.model import normalize_codex, normalize_claude, Store, validate_cached_provider
from companion.usagekani.server import make_server
from companion.usagekani.claude import ingest
from companion.usagekani.install import configure
from companion.usagekani.__main__ import bind_server, load_claude_cache


class ProviderTests(unittest.TestCase):
    def test_codex_selects_account_bucket_and_preserves_duration(self):
        value = normalize_codex({'rateLimitsByLimitId': {
            'other': {'primary': {'usedPercent': 99, 'windowDurationMins': 60, 'resetsAt': 2000}},
            'codex': {'primary': {'usedPercent': 16, 'windowDurationMins': 300, 'resetsAt': 2000},
                      'secondary': {'usedPercent': 26, 'windowDurationMins': 10080, 'resetsAt': 9000}}
        }}, 1000)
        self.assertEqual(value['status'], 'ok')
        self.assertEqual(value['windows'][0], {'used_pct': 16, 'duration_mins': 300, 'resets_at': 2000})
        self.assertEqual(value['windows'][1]['used_pct'], 26)

    def test_missing_invalid_and_expired_windows_are_not_zero(self):
        for window in [None, {'usedPercent': -1, 'resetsAt': 2000},
                       {'usedPercent': True, 'resetsAt': 2000},
                       {'usedPercent': float('nan'), 'resetsAt': 2000}]:
            with self.subTest(window=window):
                value = normalize_codex({'rateLimits': {'primary': window}}, 1000)
                self.assertIsNone(value['windows'][0])
                self.assertEqual(value['status'], 'waiting')

    def test_claude_missing_weekly_window_is_independent(self):
        value = normalize_claude({'rate_limits': {'five_hour': {'used_percentage': 50.5, 'resets_at': 2000}}}, 1000)
        self.assertEqual(value['windows'][0]['used_pct'], 50.5)
        self.assertEqual(value['windows'][0]['duration_mins'], 300)
        self.assertIsNone(value['windows'][1])

    def test_expired_window_is_preserved_for_refreshing_display(self):
        value = normalize_codex({'rateLimits': {'primary': {
            'usedPercent': 100, 'windowDurationMins': 300, 'resetsAt': 999}}}, 1000)
        self.assertEqual(value['windows'][0]['used_pct'], 100)
        self.assertEqual(value['windows'][0]['resets_at'], 999)

    def test_provider_failure_retains_values_and_observation_time(self):
        store = Store()
        store.update('codex', normalize_codex({'rateLimits': {'primary': {
            'usedPercent': 42, 'windowDurationMins': 300, 'resetsAt': 4000}}}, 1000))
        store.fail('codex', 'auth')
        value = store.snapshot(1400)
        self.assertEqual(value['codex']['observed_at'], 1000)
        self.assertEqual(value['codex']['windows'][0]['used_pct'], 42)
        self.assertEqual(value['codex']['status'], 'auth')
        self.assertEqual(value['claude']['status'], 'waiting')

    def test_old_report_is_stale_without_refreshing_its_timestamp(self):
        store = Store()
        store.update('claude', normalize_claude({'rate_limits': {'five_hour': {
            'used_percentage': 11, 'resets_at': 5000}}}, 1000))
        self.assertEqual(store.snapshot(1301)['claude']['status'], 'stale')
        self.assertEqual(store.snapshot(1500)['claude']['observed_at'], 1000)

    def test_status_line_writes_only_usage_and_ignores_missing_rate_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'claude.json'
            ingest({'rate_limits': {'five_hour': {'used_percentage': 25, 'resets_at': 4000}},
                    'session_id': 'private', 'transcript_path': 'private'}, path, 1000)
            first = path.read_text()
            self.assertNotIn('private', first)
            ingest({'session_id': 'new-session'}, path, 1100)
            self.assertEqual(path.read_text(), first)

    def test_idle_session_cannot_overwrite_newer_report_or_refresh_age(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'claude.json'
            a = {'session_id': 'a', 'rate_limits': {'five_hour': {'used_percentage': 10, 'resets_at': 9000}}}
            b = {'session_id': 'b', 'rate_limits': {'five_hour': {'used_percentage': 30, 'resets_at': 9000}}}
            ingest(a, path, 1000)
            ingest(b, path, 1100)
            ingest(a, path, 1401)
            ingest(b, path, 1500)
            self.assertEqual(json.loads(path.read_text())['observed_at'], 1100)
            self.assertEqual(json.loads(path.read_text())['windows'][0]['used_pct'], 30)

    def test_corrupt_claude_cache_cannot_poison_codex_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'claude.json'
            store = Store()
            good = normalize_claude({'rate_limits': {'five_hour': {'used_percentage': 30, 'resets_at': 9000}}}, 1000)
            for broken in ([], {'status': 'ok', 'observed_at': 1000},
                           {'status': 'ok', 'observed_at': 1000, 'windows': [{'used_pct': 'bad'}, None]}):
                path.write_text(json.dumps(broken))
                load_claude_cache(path, store, 1100)
                self.assertEqual(store.snapshot(1100)['claude']['status'], 'error')
                self.assertEqual(store.snapshot(1100)['codex']['status'], 'waiting')
                path.write_text(json.dumps(good))
                load_claude_cache(path, store, 1100)
                self.assertEqual(store.snapshot(1100)['claude']['status'], 'ok')


class InstallTests(unittest.TestCase):
    def test_install_preserves_settings_and_reuses_existing_pairing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / 'settings.json'
            settings.write_text(json.dumps({'permissions': {'allow': ['Read']}}))
            config = configure(root, root / 'state', settings, '192.168.1.10', 'codex.exe', 'python.exe')
            token = config['device_token']
            self.assertGreaterEqual(len(token), 32)
            installed = json.loads(settings.read_text())
            self.assertEqual(installed['permissions']['allow'], ['Read'])
            self.assertIn('claude_statusline.py', installed['statusLine']['command'])
            self.assertNotIn(token, settings.read_text())
            self.assertNotIn('\\', installed['statusLine']['command'])
            again = configure(root, root / 'state', settings, '192.168.1.10', 'codex.exe', 'python.exe')
            self.assertEqual(again['device_token'], token)

    def test_install_refuses_to_replace_unrelated_statusline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / 'settings.json'
            settings.write_text('{"statusLine":{"type":"command","command":"my-custom-line"}}')
            with self.assertRaises(ValueError):
                configure(root, root / 'state', settings, '192.168.1.10', 'codex.exe', 'python.exe')
            self.assertEqual(json.loads(settings.read_text())['statusLine']['command'], 'my-custom-line')

    def test_delayed_network_interface_retries_binding(self):
        stop = threading.Event()
        calls = []
        def delayed(host, port, token, store):
            calls.append(1)
            if len(calls) == 1:
                raise OSError('Interface not ready')
            return make_server(host, port, token, store)
        with self.assertLogs(level='WARNING'):
            server = bind_server({'host': '127.0.0.1', 'port': 0, 'device_token': 'token'}, Store(), stop,
                                 factory=delayed, retry_seconds=0.001)
        try:
            self.assertEqual(len(calls), 2)
            self.assertIsNotNone(server)
        finally:
            server.server_close()


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = make_server('127.0.0.1', 0, 'test-device-token', Store())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1/status'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_requires_device_token_and_serves_bounded_complete_json(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.url)
        self.assertEqual(error.exception.code, 401)
        request = urllib.request.Request(self.url, headers={'Authorization': 'Bearer test-device-token'})
        with urllib.request.urlopen(request) as response:
            body = response.read()
            self.assertEqual(int(response.headers['Content-Length']), len(body))
            self.assertIsNone(response.headers.get('Transfer-Encoding'))
            self.assertLess(len(body), 2048)
            value = json.loads(body)
            self.assertEqual(value['version'], 1)
            self.assertEqual(value['codex']['status'], 'waiting')
            self.assertEqual(value['claude']['status'], 'waiting')


if __name__ == '__main__':
    unittest.main()
