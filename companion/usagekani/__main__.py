"""Run with python -m companion.usagekani [--config PATH]."""
import argparse
import json
import logging
import threading
import time
from .claude import state_dir
from .codex import QuotaError, read_limits
from .model import Store, normalize_codex, validate_cached_provider
from .server import make_server


def load_claude_cache(path, store, now):
    try:
        value = validate_cached_provider(json.loads(path.read_text(encoding='utf-8')), now)
        store.update('claude', value)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, KeyError):
        store.fail('claude')


def bind_server(config, store, stop, *, factory=make_server, retry_seconds=5):
    while not stop.is_set():
        try:
            return factory(config['host'], config.get('port', 8765), config['device_token'], store)
        except OSError:
            logging.warning('LAN interface unavailable; retrying bind')
            stop.wait(retry_seconds)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(state_dir() / 'config.json'))
    parser.add_argument('--once', action='store_true', help='Print a sanitized Codex quota sample and exit')
    args = parser.parse_args()
    with open(args.config, encoding='utf-8-sig') as source:
        config = json.load(source)
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=directory / 'companion.log', level=logging.INFO,
                        format='%(asctime)s %(message)s')
    store = Store()
    if args.once:
        print(json.dumps(normalize_codex(read_limits(config['codex_executable']), int(time.time()))))
        return
    stop = threading.Event()

    def codex_loop():
        while not stop.is_set():
            try:
                store.update('codex', normalize_codex(read_limits(config['codex_executable']), int(time.time())))
            except QuotaError as error:
                store.fail('codex', error.status)
            except (OSError, ValueError, TypeError):
                store.fail('codex')
            stop.wait(60)

    def claude_loop():
        while not stop.is_set():
            load_claude_cache(directory / 'claude.json', store, int(time.time()))
            stop.wait(2)

    for collect in (codex_loop, claude_loop):
        threading.Thread(target=collect, daemon=True).start()
    server = bind_server(config, store, stop)
    if server is None:
        return
    logging.info('Usagekani companion started')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
