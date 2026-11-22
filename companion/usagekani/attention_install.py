"""Merge observer hooks without replacing unrelated hooks or notify handlers."""
import argparse
import json
from pathlib import Path
import shlex
import shutil
import sys

from .claude import state_dir


def install_hooks(root, directory, claude_settings, codex_hooks, python, *, codex_manual=False):
    directory.mkdir(parents=True, exist_ok=True)
    script = (root/'companion/attention_hook.py').as_posix()
    for provider, path, events in [
        ('claude', claude_settings, {'PreToolUse':'.*',
            'PermissionRequest':'.*', 'PostToolUse':'.*', 'PostToolUseFailure':'.*',
            'UserPromptSubmit':None, 'Stop':None, 'StopFailure':None, 'SessionEnd':None,
            'Elicitation':None, 'ElicitationResult':None}),
        ('codex', codex_hooks, {'PermissionRequest':'.*', 'PostToolUse':'.*',
            'PreToolUse':'.*','UserPromptSubmit':None, 'Interrupt':None, 'SessionEnd':None,'Stop':None})]:
        if provider == 'codex' and not codex_manual:
            continue
        data = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        if not isinstance(data, dict) or not isinstance(data.get('hooks', {}), dict):
            raise ValueError('Existing hook configuration must be an object')
        backup = directory/f'{provider}-settings-before-attention.json'
        if path.exists() and not backup.exists():
            shutil.copyfile(path, backup)
        hooks = data.setdefault('hooks', {})
        command = shlex.join([Path(python).as_posix(), script, '--provider', provider])
        for event, matcher in events.items():
            groups = hooks.setdefault(event, [])
            if not isinstance(groups, list):
                raise ValueError('Existing hook event must be an array')
            # Remove only this exact Usagekani entry point, preserving all peers.
            for group in groups:
                if isinstance(group, dict) and isinstance(group.get('hooks'), list):
                    group['hooks'] = [hook for hook in group['hooks'] if not
                        (isinstance(hook, dict) and hook.get('command')==command
                         and hook.get('type')=='command' and set(hook)<= {'type','command','timeout'})]
            groups[:] = [group for group in groups if not isinstance(group,dict) or group.get('hooks')]
            group = {'hooks':[{'type':'command', 'command':command, 'timeout':3}]}
            if matcher is not None:
                group['matcher'] = matcher
            groups.append(group)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.usagekani.tmp')
        temporary.write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
        temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--codex-manual', action='store_true',
                        help='Install optional Codex permission hooks; review/trust with /hooks before use')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    install_hooks(root, state_dir(), Path.home()/'.claude/settings.json',
                  Path.home()/'.codex/hooks.json', Path(sys.executable), codex_manual=args.codex_manual)
    print('Usagekani observer hooks installed; existing status line and notify settings preserved.')
    if args.codex_manual:
        print('Review/trust the new Codex hooks in /hooks. No hook-trust bypass is configured.')


if __name__ == '__main__':
    main()
