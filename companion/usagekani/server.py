"""Small authenticated LAN endpoint compatible with ESP8266 AT HTTP."""
import hmac
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_server(host, port, token, store):
    if not token:
        raise ValueError('A device token is required')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def do_GET(self):
            supplied = self.headers.get('Authorization', '').encode('utf-8')
            expected = ('Bearer ' + token).encode('utf-8')
            if not hmac.compare_digest(supplied, expected):
                self.send_error(401)
                return
            if self.path != '/v1/status':
                self.send_error(404)
                return
            body = json.dumps(store.snapshot(int(time.time())), separators=(',', ':'), allow_nan=False).encode('ascii')
            if len(body) > 2048:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # Never log addresses or pairing headers.

    return ThreadingHTTPServer((host, port), Handler)
