"""Observer-only hook: never answer questions, approve tools, or emit context."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import tomllib

from .attention import apply_event
from .claude import state_dir
from .process_identity import session_owner


def handle(provider, payload, path, *, owner=None, event_ms=None, ignore_permissions=False):
    if not isinstance(payload, dict):
        return
    session = payload.get('session_id'); event = payload.get('hook_event_name')
    if not isinstance(session, str) or not session or len(session) > 512:
        return
    event_ms = time.time_ns()//1000000 if event_ms is None else event_ms
    if event in ('UserPromptSubmit', 'SessionEnd', 'Interrupt', 'Stop', 'StopFailure'):
        apply_event(path, provider, session, 'session', action='clear_session', event_ms=event_ms)
        return
    if event in ('Elicitation', 'ElicitationResult'):
        request = payload.get('elicitation_id') or payload.get('request_id')
        if isinstance(request, str) and request:
            apply_event(path, provider, session, 'elicitation:'+request,
                        action='raise' if event == 'Elicitation' else 'clear',
                        owner=owner, event_ms=event_ms)
        return
    tool = payload.get('tool_name', '')
    use_id = payload.get('tool_use_id')
    arguments = payload.get('tool_input', {})
    fingerprint = hashlib.sha256(json.dumps([tool, arguments], sort_keys=True).encode()).hexdigest()
    permission_id = 'permission:'+fingerprint
    if event == 'PermissionRequest':
        if ignore_permissions or payload.get('permission_mode') in ('bypassPermissions', 'dontAsk', 'auto'):
            return
        apply_event(path, provider, session, permission_id, kind='approval', owner=owner, event_ms=event_ms)
    elif event == 'PreToolUse' and tool in ('AskUserQuestion', 'ExitPlanMode') and isinstance(use_id, str):
        apply_event(path, provider, session, 'tool:'+use_id, owner=owner, event_ms=event_ms,
                    kind='approval' if tool == 'ExitPlanMode' else 'question')
    elif event in ('PostToolUse', 'PostToolUseFailure'):
        if isinstance(use_id, str):
            apply_event(path, provider, session, 'tool:'+use_id, action='clear', event_ms=event_ms)
        apply_event(path, provider, session, permission_id, action='clear', event_ms=event_ms)


def main():
    # Fail open with no stdout/stderr; hooks must never alter the agent's flow.
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('--provider', choices=('codex', 'claude'), required=True)
        args = parser.parse_args()
        raw = sys.stdin.buffer.read(131073)
        if len(raw) > 131072:
            return
        payload = json.loads(raw)
        owner = session_owner(args.provider)
        ignore = False
        if args.provider == 'codex':
            try:
                config = tomllib.loads((Path.home()/'.codex/config.toml').read_text(encoding='utf-8-sig'))
                ignore = config.get('approvals_reviewer') in ('auto_review', 'guardian_subagent')
            except (OSError, ValueError):
                ignore = True  # Unknown approval mode is not proof of human input.
        handle(args.provider, payload, state_dir()/'attention.json', owner=owner, ignore_permissions=ignore)
    except (Exception, SystemExit):
        pass


if __name__ == '__main__':
    main()
