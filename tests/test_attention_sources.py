import json
import tempfile
import unittest
from pathlib import Path

from companion.usagekani.attention import read_attention
from companion.usagekani.attention_hook import handle
from companion.usagekani.codex_attention import CodexWatcher
from companion.usagekani.attention_install import install_hooks


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.state = self.root/'attention.json'
        self.owner = {'pid': 123, 'started': 456}

    def snapshot(self):
        return read_attention(self.state, 2000000010, alive=lambda owner: owner == self.owner)

    def hook(self, event, **extra):
        payload = dict(session_id='session', hook_event_name=event, **extra)
        handle('claude', payload, self.state, owner=self.owner, event_ms=2000000000000)

    def test_claude_questions_and_matching_completion_without_transcript_storage(self):
        self.hook('PreToolUse', tool_name='Bash', tool_use_id='ordinary', tool_input={'command':'private-command'})
        self.assertIsNone(self.snapshot()['claude'])
        self.hook('PreToolUse', tool_name='AskUserQuestion', tool_use_id='q1', tool_input={'questions':['private-question']})
        self.hook('PreToolUse', tool_name='AskUserQuestion', tool_use_id='q2', tool_input={})
        self.assertEqual(self.snapshot()['claude']['count'], 2)
        self.hook('PostToolUse', tool_name='AskUserQuestion', tool_use_id='q1', tool_input={'questions':['private-question']})
        self.assertEqual(self.snapshot()['claude']['count'], 1)
        self.assertNotIn('private-question', self.state.read_text())
        self.hook('SessionEnd')
        self.assertIsNone(self.snapshot()['claude'])

    def test_permission_request_matches_tool_result_and_auto_permissions_are_ignored(self):
        command = {'command':'private-command'}
        self.hook('PermissionRequest', permission_mode='bypassPermissions', tool_name='Bash', tool_input=command)
        self.assertIsNone(self.snapshot()['claude'])
        self.hook('PermissionRequest', permission_mode='default', tool_name='Bash', tool_input=command)
        self.assertEqual(self.snapshot()['claude']['kind'], 'approval')
        self.hook('PostToolUse', tool_name='Bash', tool_use_id='exec-1', tool_input=command)
        self.assertIsNone(self.snapshot()['claude'])
        self.assertNotIn('private-command', self.state.read_text())

    def test_idle_notifications_and_turn_completion_do_not_raise_attention(self):
        for event, extra in [('Notification', {'notification_type':'idle_prompt'}), ('Stop', {})]:
            self.hook(event, **extra)
        self.assertIsNone(self.snapshot()['claude'])

    def test_elicitation_response_clears_only_matching_form(self):
        self.hook('Elicitation', elicitation_id='form-one')
        self.hook('Elicitation', elicitation_id='form-two')
        self.hook('ElicitationResult', elicitation_id='form-one')
        self.assertEqual(self.snapshot()['claude']['count'], 1)
        self.hook('ElicitationResult', elicitation_id='form-two')
        self.assertIsNone(self.snapshot()['claude'])

    def watcher(self, path, **extra):
        return CodexWatcher(self.root, self.state, self.root/'checkpoint.json',
            sources=lambda:[('thread', path)], owner=lambda:self.owner, start_at_end=False, **extra)

    def append(self, path, payload, timestamp='2033-05-18T03:33:20Z'):
        with path.open('a', encoding='utf-8') as output:
            output.write(json.dumps({'timestamp':timestamp,'type':'response_item','payload':payload})+'\n')

    def ask(self, call='call-one', count=1):
        return {'type':'function_call','name':'request_user_input_async','call_id':call,
                'arguments':json.dumps({'questions':[{'title':'private-question'}]*count})}

    def reply(self, call='call-one', index=0):
        data=[{'questionItemId':json.dumps(['request_user_input_async',call,index]),'answer':'private-answer'}]
        return {'type':'message','role':'user','content':[{'type':'input_text','text':
            '<send_user_message_question_reply>'+json.dumps(data)+'</send_user_message_question_reply>'}]}

    def test_codex_async_accepted_is_not_an_answer_and_replies_clear_individual_questions(self):
        path=self.root/'rollout.jsonl'; path.touch(); watcher=self.watcher(path)
        self.append(path,self.ask(count=2)); watcher.poll()
        self.append(path,{'type':'function_call_output','call_id':'call-one','output':'{"accepted":true}'})
        watcher.poll(); self.assertEqual(self.snapshot()['codex']['count'],2)
        self.append(path,self.reply(index=0)); watcher.poll()
        self.assertEqual(self.snapshot()['codex']['count'],1)
        self.append(path,self.reply(index=1)); watcher.poll()
        self.assertIsNone(self.snapshot()['codex'])
        self.assertNotIn('private-',self.state.read_text())

    def test_codex_partial_line_checkpoint_restart_and_replay(self):
        path=self.root/'rollout.jsonl'; path.touch(); watcher=self.watcher(path)
        raw=json.dumps({'timestamp':'2033-05-18T03:33:20Z','type':'response_item','payload':self.ask()})+'\n'
        path.write_text(raw[:70],encoding='utf-8'); watcher.poll()
        self.assertIsNone(self.snapshot()['codex'])
        with path.open('a',encoding='utf-8') as output:output.write(raw[70:])
        watcher.poll(); self.assertEqual(self.snapshot()['codex']['count'],1)
        watcher=self.watcher(path)
        self.append(path,self.reply()); watcher.poll()
        self.assertIsNone(self.snapshot()['codex'])
        # A rewind/replay must not resurrect the previously answered request.
        path.write_text(raw,encoding='utf-8'); watcher.poll()
        self.assertIsNone(self.snapshot()['codex'])

    def test_existing_questions_before_first_monitor_start_are_not_replayed(self):
        path=self.root/'rollout.jsonl'; self.append(path,self.ask())
        watcher=CodexWatcher(self.root,self.state,self.root/'checkpoint.json',
            sources=lambda:[('thread',path)],owner=lambda:self.owner)
        watcher.poll(); self.assertIsNone(self.snapshot()['codex'])

    def test_installer_preserves_other_hooks_statusline_and_is_idempotent(self):
        settings=self.root/'settings.json'
        original={'statusLine':{'command':'keep-me'},'hooks':{'Stop':[{'hooks':[{'type':'command','command':'existing-hook'}]}]}}
        settings.write_text(json.dumps(original),encoding='utf-8')
        install_hooks(self.root,self.root/'local',settings,self.root/'hooks.json',Path('C:/Python/python.exe'),codex_manual=False)
        first=json.loads(settings.read_text())
        self.assertEqual(first['statusLine'],original['statusLine'])
        self.assertIn(original['hooks']['Stop'][0],first['hooks']['Stop'])
        install_hooks(self.root,self.root/'local',settings,self.root/'hooks.json',Path('C:/Python/python.exe'),codex_manual=False)
        self.assertEqual(json.loads(settings.read_text()),first)
        self.assertFalse((self.root/'hooks.json').exists())
        self.assertTrue((self.root/'local/claude-settings-before-attention.json').exists())


if __name__ == '__main__': unittest.main()
