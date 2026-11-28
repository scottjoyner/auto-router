from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from reachability import missing_services, service_reachable


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTPServer callback
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: object) -> None:
        return


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_service_reachable_detects_closed_port() -> None:
    port = _free_port()

    assert service_reachable(f"http://127.0.0.1:{port}", timeout=0.1) is False
    assert missing_services({"closed": f"http://127.0.0.1:{port}"}, timeout=0.1) == ["closed"]


def test_service_reachable_detects_in_process_http_server() -> None:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        assert service_reachable(url, timeout=1.0) is True
        assert missing_services({"local": url}, timeout=1.0) == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)
