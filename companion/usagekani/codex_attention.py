"""Observe explicit desktop question/reply records, never infer from prose."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from .attention import apply_event
from .process_identity import desktop_owner


def _key(text):
    return hashlib.sha256(text.encode()).hexdigest()


class CodexWatcher:
    def __init__(self, home, state_path, checkpoint_path, *, sources=None,
                 owner=desktop_owner, start_at_end=True):
        self.home = Path(home); self.state_path = Path(state_path)
        self.checkpoint_path = Path(checkpoint_path); self.sources = sources
        self.owner = owner; self.first = True; self.start_at_end = start_at_end
        self.cursor = {}; self.calls = {}; self.cached_sources = []
        self.next_discovery = 0
        self.since = time.time_ns()//1000000 if start_at_end else 0
        try:
            raw = self.checkpoint_path.read_bytes()
            if len(raw) <= 65536:
                saved = json.loads(raw)
                if isinstance(saved, dict) and saved.get('version') == 1:
                    self.cursor = {k:v for k,v in saved.get('cursor', {}).items()
                        if isinstance(k,str) and re.fullmatch('[0-9a-f]{64}',k) and isinstance(v,dict)
                        and type(v.get('offset')) is int and 0 <= v['offset'] < 2**50
                        and type(v.get('discard')) is bool}
                    self.calls = {k:v for k,v in saved.get('calls', {}).items()
                        if isinstance(k,str) and re.fullmatch('[0-9a-f]{64}',k) and isinstance(v,list)
                        and 1 <= len(v) <= 12 and all(type(i) is int and 0 <= i < 12 for i in v)}
                    if type(saved.get('since')) is int and saved['since'] >= 0:
                        self.since = saved['since']
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self.cursor = dict(list(self.cursor.items())[-128:])
        self.calls = dict(list(self.calls.items())[-128:])

    def _sources(self):
        if self.sources:
            return self.sources()
        if time.monotonic() < self.next_discovery:
            return self.cached_sources
        self.next_discovery = time.monotonic()+2
        try:
            db = sqlite3.connect((self.home/'state_5.sqlite').as_uri()+'?mode=ro', uri=True, timeout=0.2)
            try:
                rows = db.execute('SELECT id, rollout_path, archived FROM threads ORDER BY updated_at DESC LIMIT 128').fetchall()
            finally:
                db.close()
            sources = []
            for session, filename, archived in rows:
                if archived:
                    if _key(session) in self.cursor:
                        apply_event(self.state_path, 'codex', session, 'session', action='clear_session')
                    continue
                path = Path(filename).resolve()
                if path.is_relative_to((self.home/'sessions').resolve()) and len(sources) < 64:
                    sources.append((session, path))
            self.cached_sources = sources
        except (OSError, ValueError, sqlite3.Error):
            pass
        return self.cached_sources

    def _record(self, session, record, owner):
        try:
            stamp = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                return
            event_ms = int(stamp.timestamp()*1000)
            if event_ms < self.since:
                return
            payload = record.get('payload', {})
            if not isinstance(payload, dict) or record.get('type') != 'response_item':
                return
            kind = payload.get('type'); name = payload.get('name', '').split('.')[-1]
            call = payload.get('call_id')
            if kind == 'function_call' and name in ('request_user_input', 'request_user_input_async') and isinstance(call, str):
                arguments = payload.get('arguments', {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                questions = arguments.get('questions')
                if not isinstance(questions, list) or not 1 <= len(questions) <= 12:
                    return
                self.calls[_key(session+'/'+call)] = list(range(len(questions)))
                for i in range(len(questions)):
                    apply_event(self.state_path, 'codex', session, f'question:{call}:{i}', owner=owner, event_ms=event_ms)
            elif kind == 'function_call_output' and isinstance(call, str):
                pending = self.calls.get(_key(session+'/'+call))
                if pending:
                    try:
                        output = json.loads(payload.get('output', '{}'))
                    except (ValueError, TypeError):
                        output = {}
                    if isinstance(output, dict) and output.get('accepted') is True:
                        return
                    for i in pending:
                        apply_event(self.state_path, 'codex', session, f'question:{call}:{i}', action='clear', event_ms=event_ms)
                    del self.calls[_key(session+'/'+call)]
            elif kind == 'message' and payload.get('role') == 'user':
                for part in payload.get('content', []):
                    if not isinstance(part, dict) or not isinstance(part.get('text'), str):
                        continue
                    match = re.fullmatch(r'\s*<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>\s*', part['text'], re.S)
                    if not match:
                        continue
                    answers = json.loads(match[1])
                    if not isinstance(answers, list) or len(answers) > 12:
                        continue
                    for answer in answers:
                        identity = json.loads(answer.get('questionItemId', 'null'))
                        if (not isinstance(identity, list) or len(identity) != 3
                                or identity[0] != 'request_user_input_async' or not isinstance(identity[1], str)
                                or type(identity[2]) is not int):
                            continue
                        call, i = identity[1:]; call_key = _key(session+'/'+call)
                        pending = self.calls.get(call_key, [])
                        if i in pending:
                            apply_event(self.state_path, 'codex', session, f'question:{call}:{i}', action='clear', event_ms=event_ms)
                            pending.remove(i)
                            if not pending:
                                self.calls.pop(call_key, None)
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            return

    def poll(self):
        owner = self.owner()
        if owner is None:
            return
        changed = False
        for session, filename in self._sources():
            key = _key(session); path = Path(filename)
            try:
                size = path.stat().st_size
                if key not in self.cursor:
                    self.cursor[key] = {'offset': size if self.first and self.start_at_end else 0, 'discard': False}
                    changed = True
                cursor = self.cursor[key]
                if cursor['offset'] > size:
                    cursor.update(offset=0, discard=False)
                with path.open('rb') as source:
                    source.seek(cursor['offset']); budget = 1048576
                    while budget > 0:
                        line = source.readline(min(262144, budget)); budget -= len(line)
                        if not line:
                            break
                        complete = line.endswith(b'\n')
                        if not complete and len(line) < 262144:
                            break  # Leave the offset at the start of an unfinished record.
                        if not cursor['discard'] and complete:
                            try:
                                self._record(session, json.loads(line), owner)
                            except (ValueError, UnicodeError, RecursionError):
                                pass
                        cursor.update(offset=source.tell(), discard=not complete)
                        changed = True
            except OSError:
                continue
        self.first = False
        if changed:
            self.cursor = dict(list(self.cursor.items())[-128:])
            self.calls = dict(list(self.calls.items())[-128:])
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.checkpoint_path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'version':1, 'since':self.since, 'cursor':self.cursor, 'calls':self.calls}), encoding='utf-8')
            temporary.replace(self.checkpoint_path)
