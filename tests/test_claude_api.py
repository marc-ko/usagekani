import json
import subprocess
import unittest
from unittest.mock import patch

from companion.usagekani.claude_api import ApiError, normalize, fetch_usage, collect
from companion.usagekani.model import Store


SAMPLE = {'plan': 'pro', 'fetched_at': '2033-05-18T03:33:20Z',
          'five_hour': {'utilization': 20, 'resets_at': '2033-05-18T04:33:20.317780+00:00'},
          'seven_day': {'utilization': 15, 'resets_at': '2033-05-25T03:33:20Z'},
          'seven_day_sonnet': {'utilization': 99, 'resets_at': '2033-05-25T03:33:20Z'}}


class ClaudeAPITests(unittest.TestCase):
    def test_failure_diagnostic_keeps_http_cause_but_never_server_text_or_credentials(self):
        config = {'claude_usage_url': 'https://private.invalid/usage', 'claude_usage_key': 'private-test-key'}
        response = subprocess.CompletedProcess([], 0,
            '{"error":"private-test-key https://private.invalid/usage"}\n502', '')
        store = Store()
        with self.assertLogs(level='WARNING') as logs:
            with patch('companion.usagekani.claude_api.subprocess.run', return_value=response):
                self.assertFalse(collect(config, store, 2000000000))
        message = '\n'.join(logs.output)
        self.assertIn('claude', message)
        self.assertIn('http_502', message)
        self.assertNotIn('private-test-key', message)
        self.assertNotIn('private.invalid', message)
        self.assertEqual(store.snapshot(2000000000)['claude']['status'], 'error')

    def test_transport_diagnostics_distinguish_timeout_dns_and_bad_json(self):
        config = {'claude_usage_url': 'https://private.invalid/usage', 'claude_usage_key': 'private-test-key'}
        cases = [
            (subprocess.TimeoutExpired(['private-test-key'], 20), 'timeout'),
            (subprocess.CompletedProcess([], 6, '\n000', 'private-test-key'), 'curl_6'),
            (subprocess.CompletedProcess([], 0, 'private-test-key\n200', ''), 'invalid_json'),
            (subprocess.CompletedProcess([], 0, json.dumps(dict(SAMPLE, fetched_at=None))+'\n200', ''), 'invalid_response'),
        ]
        for result, cause in cases:
            with self.subTest(cause=cause), self.assertLogs(level='WARNING') as logs:
                options = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
                with patch('companion.usagekani.claude_api.subprocess.run', **options):
                    self.assertFalse(collect(config, Store(), 2000000000))
                message = '\n'.join(logs.output)
                self.assertIn(cause, message)
                self.assertNotIn('private-test-key', message)
                self.assertNotIn('private.invalid', message)

    def test_repeated_same_failure_is_quiet_and_recovery_is_logged_once(self):
        config = {'claude_usage_url': 'https://example.invalid/usage', 'claude_usage_key': 'test-key'}
        failure = subprocess.CompletedProcess([], 0, '{"error":"upstream"}\n500', '')
        success = subprocess.CompletedProcess([], 0, json.dumps(SAMPLE)+'\n200', '')
        store = Store()
        with self.assertLogs(level='INFO') as logs:
            with patch('companion.usagekani.claude_api.subprocess.run', side_effect=[failure, failure, success, success]):
                for expected in (False, False, True, True):
                    self.assertEqual(collect(config, store, 2000000000), expected)
        self.assertEqual(len(logs.output), 2)
        self.assertIn('http_500', logs.output[0])
        self.assertIn('recovered', logs.output[1])
        self.assertEqual(store.snapshot(2000000000)['claude']['status'], 'ok')

    def test_zero_usage_without_reset_is_idle_not_provider_failure(self):
        payload = dict(SAMPLE, five_hour={'utilization': 0, 'resets_at': None})
        value = normalize(payload, 2000000000)
        self.assertEqual(value['status'], 'ok')
        self.assertEqual(value['windows'][0], {'used_pct': 0, 'duration_mins': 300, 'resets_at': 0})
        self.assertEqual(value['windows'][1]['used_pct'], 15)
        store = Store()
        config = {'claude_usage_url': 'https://example.invalid/usage', 'claude_usage_key': 'test-key'}
        response = subprocess.CompletedProcess([], 0, json.dumps(payload)+'\n200', '')
        with patch('companion.usagekani.claude_api.subprocess.run', return_value=response):
            self.assertTrue(collect(config, store, 2000000000))
        self.assertEqual(store.snapshot(2000000000)['claude']['status'], 'ok')

    def test_missing_reset_with_nonzero_usage_is_not_invented_idle(self):
        with self.assertRaises(ApiError):
            normalize(dict(SAMPLE, five_hour={'utilization': 5, 'resets_at': None}), 2000000000)

    def test_maps_real_field_names_and_ignores_model_specific_windows(self):
        value = normalize(SAMPLE, 2000000000)
        self.assertEqual(value['observed_at'], 2000000000)
        self.assertEqual(value['windows'][0], {'used_pct': 20, 'duration_mins': 300, 'resets_at': 2000003600})
        self.assertEqual(value['windows'][1]['used_pct'], 15)
        self.assertEqual(value['windows'][1]['duration_mins'], 10080)

    def test_cached_response_keeps_source_timestamp_and_goes_stale(self):
        store = Store()
        store.update('claude', normalize(SAMPLE, 2000000000))
        store.update('claude', normalize(SAMPLE, 2000000301))
        value = store.snapshot(2000000301)['claude']
        self.assertEqual(value['observed_at'], 2000000000)
        self.assertEqual(value['status'], 'stale')

    def test_missing_window_is_unavailable_and_expired_window_is_retained(self):
        payload = dict(SAMPLE, seven_day=None)
        value = normalize(payload, 2000004000)
        self.assertIsNone(value['windows'][1])
        self.assertEqual(value['windows'][0]['resets_at'], 2000003600)

    def test_rejects_bad_observation_and_upstream_error_in_success_body(self):
        for payload in ([], {'error': 'token expired'}, dict(SAMPLE, fetched_at=None),
                        dict(SAMPLE, fetched_at='2033-05-18T03:33:20'),
                        dict(SAMPLE, fetched_at='2099-01-01T00:00:00Z')):
            with self.subTest(payload=payload), self.assertRaises(ApiError):
                normalize(payload, 2000000000)

    def test_invalid_utilization_does_not_become_zero(self):
        for used in ('20', -1, 101, True, float('nan')):
            with self.subTest(used=used), self.assertRaises(ApiError):
                normalize(dict(SAMPLE, five_hour={'utilization': used, 'resets_at': '2033-05-18T04:33:20Z'}), 2000000000)

    def test_bearer_is_stdin_only_and_https_redirects_are_not_followed(self):
        result = subprocess.CompletedProcess([], 0, json.dumps(SAMPLE)+'\n200', '')
        with patch('companion.usagekani.claude_api.subprocess.run', return_value=result) as run:
            value = fetch_usage('https://example.invalid/usage', 'private-test-key', curl='curl.exe')
        self.assertEqual(value['five_hour']['utilization'], 20)
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[1], '--disable')
        self.assertNotIn('private-test-key', ' '.join(arguments))
        self.assertNotIn('--location', arguments)
        self.assertIn('Authorization: Bearer private-test-key', run.call_args.kwargs['input'])

    def test_errors_preserve_last_reading_without_affecting_codex(self):
        config = {'claude_usage_url': 'https://example.invalid/usage', 'claude_usage_key': 'private-test-key'}
        for code, expected in ((401, 'auth'), (500, 'error'), (502, 'error')):
            store = Store()
            store.update('claude', normalize(SAMPLE, 2000000000))
            response = subprocess.CompletedProcess([], 0, '{"error":"upstream rejected"}\n'+str(code), '')
            with patch('companion.usagekani.claude_api.subprocess.run', return_value=response):
                self.assertFalse(collect(config, store, 2000000010))
            value = store.snapshot(2000000010)
            self.assertEqual(value['claude']['status'], expected)
            self.assertEqual(value['claude']['windows'][0]['used_pct'], 20)
            self.assertEqual(value['claude']['observed_at'], 2000000000)
            self.assertEqual(value['codex']['status'], 'waiting')

    def test_malformed_input_fails_one_poll_and_next_valid_poll_recovers(self):
        store = Store()
        config = {'claude_usage_url': 'https://[invalid', 'claude_usage_key': 'test-key'}
        self.assertFalse(collect(config, store, 2000000000))
        huge = dict(SAMPLE, five_hour={'utilization': 10 ** 350, 'resets_at': '2033-05-18T04:33:20Z'})
        config['claude_usage_url'] = 'https://example.invalid/usage'
        for payload, expected in ((huge, False), (SAMPLE, True)):
            response = subprocess.CompletedProcess([], 0, json.dumps(payload)+'\n200', '')
            with patch('companion.usagekani.claude_api.subprocess.run', return_value=response):
                self.assertEqual(collect(config, store, 2000000000), expected)
        self.assertEqual(store.snapshot(2000000000)['claude']['status'], 'ok')

    def test_auth_recovery_with_older_cached_reply_keeps_newer_reading(self):
        store = Store()
        newer = normalize(dict(SAMPLE, fetched_at='2033-05-18T03:35:00Z',
                               five_hour={'utilization': 63, 'resets_at': '2033-05-18T04:33:20Z'}), 2000000100)
        store.update('claude', newer)
        store.fail('claude', 'auth')
        config = {'claude_usage_url': 'https://example.invalid/usage', 'claude_usage_key': 'test-key'}
        response = subprocess.CompletedProcess([], 0, json.dumps(SAMPLE)+'\n200', '')
        with patch('companion.usagekani.claude_api.subprocess.run', return_value=response):
            self.assertTrue(collect(config, store, 2000000101))
        value = store.snapshot(2000000101)['claude']
        self.assertEqual(value['status'], 'ok')
        self.assertEqual(value['windows'][0]['used_pct'], 63)
        self.assertEqual(value['observed_at'], 2000000100)


if __name__ == '__main__':
    unittest.main()
