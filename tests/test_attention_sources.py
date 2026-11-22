import json
import tempfile
import unittest
import io
import sqlite3
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
        self.hook('PreToolUse',tool_name='Bash',tool_use_id='exec-1',tool_input=command)
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
            sources=lambda:[('thread', path)], owner=lambda:self.owner, start_at_end=False,
            clock=lambda:2000000001, **extra)

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

    def test_reply_before_ask_and_lost_checkpoint_cannot_resurrect_question(self):
        path=self.root/'rollout.jsonl'; path.touch(); watcher=self.watcher(path)
        self.append(path,self.reply()); self.append(path,self.ask()); watcher.poll()
        self.assertIsNone(self.snapshot()['codex'])
        self.append(path,self.ask(call='other')); watcher.poll()
        self.append(path,self.reply(call='other'))
        (self.root/'checkpoint.json').write_text('['*5000)
        watcher=CodexWatcher(self.root,self.state,self.root/'checkpoint.json',
            sources=lambda:[('thread',path)],owner=lambda:self.owner,clock=lambda:2000000001)
        watcher.poll(); self.assertIsNone(self.snapshot()['codex'])

    def test_new_rollout_during_companion_downtime_is_read_with_valid_checkpoint(self):
        path=self.root/'first.jsonl'; path.touch(); self.watcher(path).poll()
        second=self.root/'second.jsonl'; self.append(second,self.ask())
        watcher=CodexWatcher(self.root,self.state,self.root/'checkpoint.json',
            sources=lambda:[('new-thread',second)],owner=lambda:self.owner,clock=lambda:2000000001)
        watcher.poll(); self.assertEqual(self.snapshot()['codex']['count'],1)

    def test_sync_answer_recovers_with_lost_call_checkpoint(self):
        path=self.root/'rollout.jsonl';path.touch();watcher=self.watcher(path)
        self.append(path,dict(self.ask(),name='request_user_input'));watcher.poll()
        self.append(path,{'type':'function_call_output','call_id':'call-one','output':'{"answers":{}}'})
        (self.root/'checkpoint.json').unlink()
        watcher=CodexWatcher(self.root,self.state,self.root/'checkpoint.json',
            sources=lambda:[('thread',path)],owner=lambda:self.owner,clock=lambda:2000000001)
        watcher.poll();self.assertIsNone(self.snapshot()['codex'])

    def test_lifecycle_cleanup_prevents_late_tool_start_or_permission_from_reviving(self):
        payload={'session_id':'session','hook_event_name':'PreToolUse','tool_name':'AskUserQuestion','tool_use_id':'closed','tool_input':{}}
        handle('claude',payload,self.state,owner=self.owner,event_ms=2000000000000)
        handle('claude',dict(payload,hook_event_name='Stop'),self.state,owner=self.owner,event_ms=2000000000100)
        handle('claude',payload,self.state,owner=self.owner,event_ms=2000000000200)
        self.assertIsNone(self.snapshot()['claude'])

    def test_unreadable_rollout_lease_expires_and_old_process_events_are_not_rebound(self):
        path=self.root/'rollout.jsonl'; path.touch(); watcher=self.watcher(path)
        self.append(path,self.ask()); watcher.poll(); path.unlink(); watcher.poll()
        self.assertIsNone(read_attention(self.state,2000000040,alive=lambda owner:True)['codex'])
        other=self.root/'old-process.jsonl'; self.append(other,self.ask(call='old'))
        watcher=CodexWatcher(self.root,self.state,self.root/'second-checkpoint.json',
            sources=lambda:[('old-thread',other)],owner=lambda:self.owner,start_at_end=False,
            clock=lambda:2000000010,generation_start=lambda owner:2000000005000)
        watcher.poll()
        self.assertEqual(read_attention(self.state,2000000010,alive=lambda owner:True)['codex']['count'],1)

    def test_discovery_excludes_unrelated_cli_and_automatic_review_threads(self):
        sessions=self.root/'sessions'; sessions.mkdir()
        db=sqlite3.connect(self.root/'state_5.sqlite')
        db.execute('CREATE TABLE threads(id TEXT, rollout_path TEXT, archived INTEGER, updated_at INTEGER, originator TEXT, thread_source TEXT)')
        for name,origin,source in [('desktop','Codex Desktop','user'),('cli','codex-tui','user'),('guardian','Codex Desktop','guardian_review')]:
            path=sessions/(name+'.jsonl'); self.append(path,self.ask(call=name))
            db.execute('INSERT INTO threads VALUES(?,?,0,1,?,?)',(name,str(path),origin,source))
        db.commit();db.close()
        watcher=CodexWatcher(self.root,self.state,self.root/'checkpoint.json',owner=lambda:self.owner,
            start_at_end=False,clock=lambda:2000000001)
        watcher.poll();self.assertEqual(self.snapshot()['codex']['count'],1)

    def test_replayed_claude_tool_start_after_completion_stays_cleared(self):
        payload={'session_id':'session','hook_event_name':'PreToolUse','tool_name':'AskUserQuestion','tool_use_id':'unique','tool_input':{}}
        handle('claude',payload,self.state,owner=self.owner,event_ms=2000000000000)
        handle('claude',dict(payload,hook_event_name='PostToolUse'),self.state,owner=self.owner,event_ms=2000000000100)
        handle('claude',payload,self.state,owner=self.owner,event_ms=2000000000200)
        self.assertIsNone(self.snapshot()['claude'])

    def test_identical_concurrent_permissions_clear_only_matching_invocation(self):
        command={'command':'same-command'}
        for call in ('first','second'):
            self.hook('PreToolUse',tool_name='Bash',tool_use_id=call,tool_input=command)
            self.hook('PermissionRequest',tool_name='Bash',tool_input=command)
        self.assertEqual(self.snapshot()['claude']['count'],2)
        self.hook('PostToolUse',tool_name='Bash',tool_use_id='first',tool_input=command)
        self.assertEqual(self.snapshot()['claude']['count'],1)

    def test_large_tool_response_still_delivers_completion_metadata(self):
        from companion.usagekani.hook_input import read_hook_payload
        command={'command':'same-command'}
        self.hook('PreToolUse',tool_name='Bash',tool_use_id='large',tool_input=command)
        self.hook('PermissionRequest',tool_name='Bash',tool_input=command)
        raw=json.dumps({'tool_response':'x'*200000,'session_id':'session','hook_event_name':'PostToolUse',
            'tool_name':'Bash','tool_use_id':'large','tool_input':command})
        payload=read_hook_payload(io.StringIO(raw))
        self.assertNotIn('tool_response',payload)
        handle('claude',payload,self.state,owner=self.owner,event_ms=2000000000100)
        self.assertIsNone(self.snapshot()['claude'])

    def test_anonymous_elicitation_forms_are_counted_per_server_and_mode(self):
        self.hook('Elicitation',mcp_server_name='server',mode='form')
        self.hook('Elicitation',mcp_server_name='server',mode='form')
        self.hook('ElicitationResult',mcp_server_name='server',mode='form')
        self.assertEqual(self.snapshot()['claude']['count'],1)
        self.hook('ElicitationResult',mcp_server_name='server',mode='form')
        self.assertIsNone(self.snapshot()['claude'])

    def test_optional_codex_lifecycle_cleanup_keeps_unanswered_questions(self):
        from companion.usagekani.attention import apply_event
        apply_event(self.state,'codex','session','question',owner=self.owner,event_ms=2000000000000)
        handle('codex',{'session_id':'session','hook_event_name':'UserPromptSubmit'},self.state,
            owner=self.owner,event_ms=2000000000100)
        self.assertEqual(self.snapshot()['codex']['kind'],'question')

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

    def test_installer_keeps_composite_user_wrapper_and_adds_codex_stop_cleanup(self):
        settings=self.root/'settings.json'
        command=f'C:/Python/python.exe {self.root.as_posix()}/companion/attention_hook.py --provider claude && audit'
        custom={'hooks':[{'type':'command','command':command}]}
        settings.write_text(json.dumps({'hooks':{'Stop':[custom]}}))
        install_hooks(self.root,self.root/'local',settings,self.root/'hooks.json',Path('C:/Python/python.exe'),codex_manual=True)
        self.assertIn(custom,json.loads(settings.read_text())['hooks']['Stop'])
        self.assertIn('Stop',json.loads((self.root/'hooks.json').read_text())['hooks'])


if __name__ == '__main__': unittest.main()
