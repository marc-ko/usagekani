"""Configure only Usagekani-owned state; preserve unrelated Claude settings."""
import argparse
import ipaddress
import json
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from .claude import state_dir


def configure(root, directory, settings_path, host, codex_executable, python_executable):
    address = ipaddress.ip_address(host)
    if not address.is_private or address.is_loopback or address.is_link_local:
        raise ValueError('Select a private LAN interface address')
    settings = json.loads(settings_path.read_text(encoding='utf-8-sig')) if settings_path.exists() else {}
    script = root / 'companion' / 'claude_statusline.py'
    command = shlex.join([Path(python_executable).as_posix(), script.as_posix()])
    legacy_command = subprocess.list2cmdline([str(python_executable), str(script)])
    existing = settings.get('statusLine', {}).get('command', '')
    if existing and existing not in (command, legacy_command):
        raise ValueError('An unrelated Claude status line exists; preserve it and configure a wrapper manually')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'config.json'
    config = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    config.update(host=host, port=8765, codex_executable=str(codex_executable))
    config.setdefault('device_token', secrets.token_hex(24))
    path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    if settings_path.exists() and not (directory / 'claude-settings-before.json').exists():
        shutil.copyfile(settings_path, directory / 'claude-settings-before.json')
    settings['statusLine'] = {'type': 'command', 'command': command}
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host')
    args = parser.parse_args()
    host = args.host
    if not host:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect(('192.0.2.1', 80))  # Route lookup only: no packets sent.
            host = route.getsockname()[0]
    codex = shutil.which('codex') or str(Path.home() / 'AppData/Local/Programs/OpenAI/Codex/bin/codex.exe')
    root = Path(__file__).resolve().parents[2]
    configure(root, state_dir(), Path.home() / '.claude/settings.json', host, codex, sys.executable)
    print('Usagekani configured; provider credentials remain on this computer.')


if __name__ == '__main__':
    main()
