"""Shared offline fixtures: a local HTTP server for tests/fixtures/ and a headless Chromium we own."""

import json
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from jev_ultrafast.engagement.chrome import find_chromium, launch_chromium
from jev_ultrafast.engagement.transport import DirectTransport

FIXTURES = Path(__file__).with_name("fixtures")
# Chrome's own background traffic (sync, GCM, time, Gaia) must not reach the internet during tests. A dead proxy
# blocks every non-loopback request; 127.0.0.1 bypasses proxies implicitly, so shop_server still works.
OFFLINE_ARGS = ("--proxy-server=http://127.0.0.1:9",)


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURES), **kwargs)

    def log_message(self, *args):
        pass

    def _record(self, body=b""):
        self.server.requests.append({
            "method": self.command,
            "path": self.path,
            "body": body.decode("utf-8", "replace"),
            "headers": dict(self.headers),
        })

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def guess_type(self, path):
        kind = super().guess_type(path)
        text = kind.startswith("text/") or kind in ("application/javascript", "application/json")
        return f"{kind}; charset=utf-8" if text else kind

    def _delay(self):
        """A "delay=<ms>" query key holds the answer that long (at most 10 s): a slow image, a slow consent POST."""
        value = (parse_qs(urlsplit(self.path).query).get("delay") or ["0"])[0]
        if value.isdigit():
            time.sleep(min(int(value), 10000) / 1000)

    def do_GET(self):
        self._record()
        self._delay()
        super().do_GET()

    def do_HEAD(self):
        self._record()
        super().do_HEAD()

    def do_POST(self):
        self._record(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        self._delay()
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ShopServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.requests: list[dict] = []  # {"method", "path", "body", "headers"} in arrival order

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.server_port}/{path.lstrip('/')}"


@pytest.fixture(scope="session")
def shop_server():
    server = ShopServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session")
def chromium():
    path = find_chromium()
    if path is None:
        pytest.skip("Chromium not found; set JEV_CHROME_PATH to run browser tests")
    chrome = launch_chromium(path=path, extra_args=OFFLINE_ARGS)
    yield chrome
    chrome.close()


@pytest.fixture
def transport(chromium):
    t = DirectTransport(chromium.ws_url)
    before = {info["targetId"] for info in t.call("Target.getTargets")["targetInfos"]}
    yield t
    # Close pages a test left open so tests stay independent within the shared browser. Contexts made with
    # disposeOnDetach=True (profiles.new_context) go away when t closes.
    try:
        for info in t.call("Target.getTargets")["targetInfos"]:
            if info["type"] == "page" and info["targetId"] not in before:
                t.call("Target.closeTarget", targetId=info["targetId"])
    except (RuntimeError, OSError):
        pass
    t.close()
