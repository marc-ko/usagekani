"""Read account quotas through Codex app-server, without creating a turn."""
import json
import queue
import subprocess
import threading
import time


class QuotaError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(status)


def read_limits(executable, timeout=25):
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen([executable, 'app-server', '--stdio'], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding='utf-8', creationflags=flags)
    messages = queue.Queue()

    def read_stdout():
        for line in process.stdout:
            try:
                messages.put(json.loads(line))
            except ValueError:
                pass
        messages.put(None)

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()

    def send(message):
        process.stdin.write(json.dumps(message) + '\n')
        process.stdin.flush()

    def receive(request_id):
        deadline = time.monotonic() + timeout
        while True:
            try:
                message = messages.get(timeout=max(0.001, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise QuotaError('error') from exc
            if message is None:
                raise QuotaError('error')
            if message.get('id') == request_id:
                if 'error' in message:
                    error = str(message['error']).lower()
                    raise QuotaError('auth' if any(s in error for s in ('auth', 'login', 'sign in', '401')) else 'error')
                return message.get('result', {})
            if time.monotonic() >= deadline:
                raise QuotaError('error')

    try:
        send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'usagekani', 'version': '0.1.0'}}})
        receive(1)
        send({'method': 'initialized'})
        send({'id': 2, 'method': 'account/rateLimits/read'})
        return receive(2)
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        reader.join(timeout=1)
        process.stdin.close()
        process.stdout.close()
