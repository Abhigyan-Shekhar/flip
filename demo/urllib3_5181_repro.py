"""Actual loopback HTTP upload regression; execute only in the demo container."""

import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urllib3


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_PUT(self):
        self.respond()

    def do_GET(self):
        self.respond()

    def respond(self):
        body = b""
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            while True:
                count = int(self.rfile.readline().split(b";", 1)[0].strip(), 16)
                if not count:
                    while self.rfile.readline().strip():
                        pass
                    break
                body += self.rfile.read(count)
                self.rfile.read(2)
        else:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.received.append(body)
        redirect = self.path == "/start"
        self.send_response(self.server.redirect_code if redirect else 200)
        if redirect:
            self.send_header("Location", "/echo")
        self.send_header("Content-Length", "0" if redirect else str(len(body)))
        self.end_headers()
        if not redirect:
            self.wfile.write(body)


OBSERVATIONS = []


class RedirectTests(unittest.TestCase):
    def upload(self, status, offset, file=False, bare_pool=False):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        server.received, server.redirect_code = [], status
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        body = tempfile.TemporaryFile() if file else io.BytesIO()
        original = b"PREFIXPAYLOAD-DATA"
        body.write(original)
        body.seek(offset)
        port = server.server_address[1]
        pool = urllib3.HTTPConnectionPool("127.0.0.1", port) if bare_pool else urllib3.PoolManager()
        try:
            response = pool.urlopen(
                "PUT", "/start" if bare_pool else f"http://127.0.0.1:{port}/start",
                body=body, retries=urllib3.Retry(total=2, redirect=2), timeout=3,
            )
            expected = b"" if status == 303 else original[offset:]
            row = {
                "redirect": status, "offset": offset, "file": file, "bare_pool": bare_pool,
                "received_lengths": [len(value) for value in server.received],
                "expected_echo_length": len(expected), "actual_echo_length": len(response.data),
                "http_status": response.status,
            }
            OBSERVATIONS.append(row)
            self.assertEqual(response.status, 200)
            self.assertEqual(len(server.received), 2)
            self.assertEqual(server.received[0], original[offset:])
            self.assertEqual(response.data, expected, row)
        finally:
            pool.close() if bare_pool else pool.clear()
            body.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_303_control(self):
        self.upload(303, 6)

    def test_bare_pool_control(self):
        self.upload(307, 6, bare_pool=True)


def case(status, offset, file):
    return lambda self: self.upload(status, offset, file)


for code in (307, 308):
    for position in (0, 6):
        for use_file in (False, True):
            setattr(
                RedirectTests, f"test_{code}_{position}_{use_file}", case(code, position, use_file)
            )


if __name__ == "__main__":
    outcome = unittest.main(argv=["urllib3-5181"], exit=False).result
    print(json.dumps({
        "tests": outcome.testsRun, "failures": len(outcome.failures),
        "errors": len(outcome.errors), "observations": OBSERVATIONS,
    }, sort_keys=True))
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
