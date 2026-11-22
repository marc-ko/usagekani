import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path

from companion.usagekani.attention import apply_event, read_attention
from companion.usagekani.model import Store


class AttentionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'attention.json'
        self.owner = {'pid': 123, 'started': 456}

    def emit(self, session='session', request='question', action='raise',
             kind='question', event_ms=1000000, provider='codex'):
        apply_event(self.path, provider, session, request, action=action,
                    kind=kind, owner=self.owner, event_ms=event_ms)

    def snapshot(self, now=1001, alive=lambda owner: True):
        return read_attention(self.path, now, alive=alive)

    def test_tracks_only_explicit_requests_and_clears_only_matching_request(self):
        self.assertEqual(self.snapshot(), {'codex': None, 'claude': None})
        self.emit(request='first')
        self.emit(session='another', request='second')
        self.emit(provider='claude', request='third', kind='approval')
        self.assertEqual(self.snapshot()['codex'], {'kind': 'question', 'count': 2, 'observed_at': 1001})
        self.emit(request='first', action='clear', event_ms=1000100)
        self.assertEqual(self.snapshot()['codex']['count'], 1)
        self.assertEqual(self.snapshot()['claude']['kind'], 'approval')
        self.emit(session='another', action='clear_session', event_ms=1000200)
        self.assertIsNone(self.snapshot()['codex'])
        self.assertIsNotNone(self.snapshot()['claude'])

    def test_delayed_raise_cannot_resurrect_resolved_request_or_reset_session(self):
        self.emit(action='clear', event_ms=1000200)
        self.emit(event_ms=1000100)
        self.assertIsNone(self.snapshot()['codex'])
        self.emit(action='clear_session', event_ms=1000300)
        self.emit(request='late-callback', event_ms=1000250)
        self.assertIsNone(self.snapshot()['codex'])
        self.emit(request='new-question', event_ms=1000400)
        self.assertEqual(self.snapshot()['codex']['count'], 1)

    def test_duplicate_replay_is_idempotent_and_persisted_requests_survive_restart(self):
        self.emit()
        self.emit()
        self.assertEqual(self.snapshot()['codex']['count'], 1)
        self.assertEqual(read_attention(self.path, 5000, alive=lambda owner: owner == self.owner)
                         ['codex']['count'], 1)

    def test_dead_or_reused_owner_cannot_show_persisted_alert(self):
        self.emit()
        self.assertIsNone(self.snapshot(alive=lambda owner: owner == {'pid': 123, 'started': 999})['codex'])
        self.assertIsNone(self.snapshot(alive=lambda owner: False)['codex'])

    def test_persisted_state_contains_no_raw_question_session_or_request_text(self):
        self.emit(session='private-session', request='private-question')
        text = self.path.read_text()
        self.assertNotIn('private-session', text)
        self.assertNotIn('private-question', text)
        self.assertIn('question', text)

    def test_concurrent_writers_preserve_distinct_requests(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda i: self.emit(request=str(i)), range(20)))
        self.assertEqual(self.snapshot()['codex']['count'], 20)

    def test_bounded_requests_and_corrupt_or_oversized_state_are_safe(self):
        for i in range(80):
            self.emit(request=str(i), event_ms=1000000+i)
        self.assertLessEqual(self.snapshot()['codex']['count'], 64)
        self.assertLess(self.path.stat().st_size, 65536)
        for value in ('not json', '[]', '{"requests":[]}', 'x'*65537):
            self.path.write_text(value)
            self.assertEqual(self.snapshot(), {'codex': None, 'claude': None})
        self.emit()
        self.assertEqual(self.snapshot()['codex']['count'], 1)

    def test_quota_error_does_not_erase_attention_and_attention_does_not_forge_quota(self):
        store = Store()
        self.emit()
        store.set_attention(self.snapshot())
        store.fail('codex')
        result = store.snapshot(1001)['codex']
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['windows'], [None, None])
        self.assertEqual(result['attention']['kind'], 'question')
        store.set_attention({'codex': None, 'claude': None})
        self.assertIsNone(store.snapshot(1001)['codex']['attention'])


if __name__ == '__main__':
    unittest.main()
