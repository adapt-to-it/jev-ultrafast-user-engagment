"""CDP transports behind one call/events shape: a direct WebSocket (lossless) or the Browser Harness daemon."""

import ast
import ipaddress
import json
import os
import re
import tempfile
import threading
import urllib.request
from collections import deque
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from .chrome import LaunchedChrome, find_chromium, launch_chromium

try:
    import fcntl
except ImportError:  # Windows: no daemon lock (see HarnessTransport).
    fcntl = None

# Native JavaScript dialogs block the call in flight (a click, a navigation), so the transport answers them as they
# open. Accept only what has no "yes" semantics: a site's question is never confirmed; beforeunload is ours to leave.
DIALOG_ACCEPT = {"alert": True, "beforeunload": True, "confirm": False, "prompt": False}
SESSION_GONE = -32001  # crdtp SessionNotFound: the session's tab was closed, detached or its renderer crashed
HARNESS_LOCK_DIR: str | None = None  # where HarnessTransport locks its daemon; None is the system temp dir


def _session_gone(message: str, event: str) -> dict:
    return {"code": SESSION_GONE, "message": message, "data": event}


class CdpError(RuntimeError):
    """CDP answered a command with an error object."""

    def __init__(self, method: str, error: dict):
        self.method, self.code, self.data = method, error.get("code"), error.get("data")
        detail = f" ({self.data})" if self.data else ""
        super().__init__(f"{method}: {error.get('message', error)}{detail}")


class CdpTransport(Protocol):
    """`session_id=None` is browser-level on DirectTransport and "the daemon's current tab" on HarnessTransport.

    Callers that need browser-level behaviour use `Target.*` methods or pass a session; never send sessionless
    page-level calls. `timeout` is this client's wait for the reply, in seconds: it shadows the CDP `timeout`
    parameter of Runtime.evaluate, Runtime.callFunctionOn and Debugger.evaluateOnCallFrame, which therefore cannot be
    sent through `**params` (bound such expressions in JavaScript instead).

    Both transports answer `Page.javascriptDialogOpening` on their own sessions as it arrives (DIALOG_ACCEPT) and keep
    the Opening/Closed events buffered for collectors; sessions need `Page.enable` for Chrome to report dialogs.

    A CdpError with code -32001 (SESSION_GONE) means this session will never answer: its tab closed or detached, or
    its renderer crashed (`data` names the event, "Inspector.targetCrashed" for a crash). Calls pending on it fail at
    once, and so does every later call on a crashed session; continue in a new tab (same context, same cookies).
    """

    kind: str
    lossy: bool

    def call(self, method: str, session_id: str | None = None, *, timeout: float = 30.0, **params) -> dict: ...

    def events(self, session_id: str | None = None, method_prefix: str | None = None) -> list[dict]: ...

    def discard(self, session_id: str) -> None: ...  # drop a closed session's buffered events

    def close(self) -> None: ...


def _take(buffer: deque, session_id, method_prefix) -> tuple[list[dict], deque]:
    """Split matching events out of buffer; non-matching ones stay in order."""
    matched, kept = [], deque(maxlen=buffer.maxlen)
    for event in buffer:
        ok = (session_id is None or event.get("session_id") == session_id) and (
            method_prefix is None or event["method"].startswith(method_prefix)
        )
        (matched if ok else kept).append(event)
    return matched, kept


def _harness_error(error) -> dict:
    """The daemon sends str(RuntimeError(cdp_error_dict)): read the dict repr back so CdpError.code is set."""
    if isinstance(error, dict):
        return error
    try:
        parsed = ast.literal_eval(error) if isinstance(error, str) and error.lstrip().startswith("{") else None
    except Exception:  # Not a literal: a daemon message such as "not_attached".
        parsed = None
    return parsed if isinstance(parsed, dict) else {"message": str(error)}


def _is_local(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    try:
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class DirectTransport:
    """One WebSocket to a DevTools endpoint. A reader thread routes replies by id and buffers every event."""

    kind = "direct"
    lossy = False

    def __init__(self, ws_url: str, *, max_events: int = 200_000, connect_timeout: float = 10.0):
        self.ws_url = ws_url
        self.dropped = 0  # events discarded because the buffer reached max_events
        self._ws = connect(
            ws_url, max_size=None, compression=None, ping_interval=None, close_timeout=2,
            open_timeout=connect_timeout, proxy=None if _is_local(ws_url) else True,
        )
        self._lock = threading.Lock()
        self._waiters: dict[int, list] = {}  # id -> [threading.Event, reply, session id]
        self._events: deque = deque(maxlen=max_events)
        self._discarded: set[str] = set()  # closed pages: their late events are not buffered
        self._crashed: dict[str, dict] = {}  # session -> error: a crashed renderer never answers
        self._next_id = 0
        self._closed = False
        self._reader = threading.Thread(target=self._read, name="cdp-reader", daemon=True)
        self._reader.start()

    def call(self, method: str, session_id: str | None = None, *, timeout: float = 30.0, **params) -> dict:
        waiter = [threading.Event(), None, session_id]
        with self._lock:
            if self._closed:
                raise ConnectionError(f"CDP connection is closed ({method})")
            if session_id in self._crashed:
                raise CdpError(method, self._crashed[session_id])
            self._next_id += 1
            message_id = self._next_id
            self._waiters[message_id] = waiter
        message = {"id": message_id, "method": method, "params": params}
        if session_id:
            message["sessionId"] = session_id
        try:
            self._ws.send(json.dumps(message))
            if not waiter[0].wait(timeout):
                raise TimeoutError(f"{method} got no CDP reply within {timeout:g} s")
        except ConnectionClosed as exc:
            raise ConnectionError(f"CDP connection closed during {method}") from exc
        finally:
            with self._lock:
                self._waiters.pop(message_id, None)
        reply = waiter[1]
        if reply is None:
            raise ConnectionError(f"CDP connection closed during {method}")
        if "error" in reply:
            raise CdpError(method, reply["error"])
        return reply.get("result", {})

    def events(self, session_id: str | None = None, method_prefix: str | None = None) -> list[dict]:
        with self._lock:
            matched, self._events = _take(self._events, session_id, method_prefix)
        return matched

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._discarded.add(session_id)
            self._crashed.pop(session_id, None)
            self._events = _take(self._events, session_id, None)[1]

    def close(self) -> None:
        self._shutdown()
        try:
            self._ws.close()
        except Exception:
            pass
        if threading.current_thread() is not self._reader:
            self._reader.join(timeout=3)

    def _read(self):
        try:
            for raw in self._ws:
                message = json.loads(raw)
                if "id" in message:
                    with self._lock:
                        waiter = self._waiters.get(message["id"])
                    if waiter:
                        waiter[1] = message
                        waiter[0].set()
                elif "method" in message:
                    event = {"method": message["method"], "params": message.get("params", {}),
                             "session_id": message.get("sessionId")}
                    self._react(event)
                    with self._lock:
                        if event["session_id"] in self._discarded:
                            continue
                        if len(self._events) == self._events.maxlen:
                            self.dropped += 1
                        self._events.append(event)
        except (ConnectionClosed, OSError, ValueError):
            pass
        finally:
            self._shutdown()

    def _react(self, event):
        """Reader-thread duties; never self.call() here, its reply would arrive on this very thread."""
        method, params, session = event["method"], event["params"], event["session_id"]
        if method == "Page.javascriptDialogOpening" and session:
            with self._lock:
                self._next_id += 1
                message_id = self._next_id
            answer = {"accept": DIALOG_ACCEPT.get(params.get("type"), False)}
            try:  # No waiter: the reply is dropped. websockets.sync serializes concurrent sends.
                self._ws.send(json.dumps({"id": message_id, "sessionId": session,
                                          "method": "Page.handleJavaScriptDialog", "params": answer}))
            except (ConnectionClosed, OSError):
                pass
        elif method == "Target.detachedFromTarget":  # Chrome never answers calls pending on a closed tab.
            self._fail(params.get("sessionId"), _session_gone("Session with given id not found.", method))
        elif method == "Inspector.targetCrashed" and session:  # Nor any call on a crashed renderer.
            self._fail(session, _session_gone("Target crashed.", method), crashed=True)
        elif method == "Inspector.targetReloadedAfterCrash" and session:
            with self._lock:
                self._crashed.pop(session, None)

    def _fail(self, session_id, error, crashed=False):
        if not session_id:
            return
        with self._lock:  # One critical section: a call either sees the crash or is pending and failed here.
            if crashed and session_id not in self._discarded:
                self._crashed[session_id] = error
            waiters = [w for w in self._waiters.values() if w[2] == session_id and not w[0].is_set()]
            for waiter in waiters:
                waiter[1] = {"error": error}
        for waiter in waiters:
            waiter[0].set()

    def _shutdown(self):
        with self._lock:
            self._closed = True
            waiters, self._waiters = list(self._waiters.values()), {}
        for waiter in waiters:
            waiter[0].set()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _lock_daemon(name: str):
    """Hold an exclusive lock on the named daemon (pid written inside), or raise naming the process holding it."""
    if fcntl is None:
        return None
    # Daemons are per user (their sockets live under HOME), so are their locks. O_NOFOLLOW: a shared temp dir.
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    path = Path(HARNESS_LOCK_DIR or tempfile.gettempdir()) / f"jev-harness-{safe}-{os.getuid()}.lock"
    lock = os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600), "r+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = lock.read().strip() or "unknown"
        lock.close()
        who = f"pid {holder}" + (" (this process)" if holder == str(os.getpid()) else "")
        raise RuntimeError(f"Browser Harness daemon {name!r} is in use by {who}; "
                           "use browser='launch' or 'cdp:<url>', or wait") from None
    except BaseException:
        lock.close()
        raise
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    return lock


class HarnessTransport:
    """The Browser Harness daemon attached to the user's Chrome. Its event ring buffer holds 500 events (lossy).

    It speaks the daemon protocol that browser_harness.helpers.cdp/drain_events use, addressed to our own named
    daemon. Without a session id, non-Target methods run on the daemon's current tab rather than the browser.
    A background thread drains the daemon every `poll_interval` seconds, keeps only the events of sessions this
    transport attached (never the user's own tab) and answers their native dialogs.

    One consumer at a time: a drain empties the daemon's buffer for whoever asks, so a second transport on the same
    daemon would take this one's events and leave its dialogs unanswered. The constructor therefore holds an
    exclusive lock (`jev-harness-<name>-<uid>.lock` in the temp dir) until close(), and raises RuntimeError while
    another transport, in this process or another, holds it. Without fcntl (Windows) there is no lock: run one at a
    time.
    """

    kind = "harness"
    lossy = True

    def __init__(self, *, name: str = "jev-engagement", env: dict | None = None, max_events: int = 200_000,
                 poll_interval: float | None = 0.25):
        # Private daemon IPC: relies on the exact browser-harness==0.1.13 pin; re-verify the wire shape if it moves.
        from browser_harness import _ipc
        from browser_harness.admin import ensure_daemon

        self.name = name
        self.dropped = 0  # events discarded because the buffer reached max_events
        self._ipc = _ipc
        self._lock = threading.Lock()
        self._drain_lock = threading.Lock()  # keeps drained batches in arrival order
        self._events: deque = deque(maxlen=max_events)
        self._sessions: set[str] = set()
        self._crashed: dict[str, dict] = {}
        self._closed = False
        self._stop = threading.Event()
        self._daemon_lock = _lock_daemon(name)
        try:  # The title marker would change document.title on audited pages.
            ensure_daemon(name=name, env={"BH_TAB_MARKER": "0", **(env or {})})
        except BaseException:
            self._release()
            raise
        self._poller = None
        if poll_interval:
            self._poller = threading.Thread(target=self._poll, args=(poll_interval,), name="harness-events",
                                            daemon=True)
            self._poller.start()

    def _send(self, request: dict, timeout: float) -> dict:
        if self._closed:
            raise ConnectionError("Harness transport is closed")
        sock, token = self._ipc.connect(self.name, timeout=5.0)
        try:
            sock.settimeout(timeout)
            reply = self._ipc.request(sock, token, request)
        finally:
            sock.close()
        if not isinstance(reply, dict) or not ({"result", "error", "events"} & reply.keys()):
            # _ipc.request() reads EOF as {}: a daemon that died mid-request must not look like an empty result.
            raise ConnectionError(f"Browser Harness daemon {self.name!r} closed the connection without a reply")
        return reply

    def call(self, method: str, session_id: str | None = None, *, timeout: float = 30.0, **params) -> dict:
        with self._lock:
            crashed = self._crashed.get(session_id) if session_id else None
        if crashed:
            raise CdpError(method, crashed)
        reply = self._send({"method": method, "params": params, "session_id": session_id}, timeout)
        if "error" in reply:
            raise CdpError(method, _harness_error(reply["error"]))
        result = reply.get("result")
        result = result if isinstance(result, dict) else {}
        if method == "Target.attachToTarget" and result.get("sessionId"):
            with self._lock:
                self._sessions.add(result["sessionId"])
        return result

    def _pump(self) -> None:
        """Move the daemon's events into the local buffer and answer dialogs on our sessions."""
        with self._drain_lock:
            reply = self._send({"meta": "drain_events"}, 30.0)
            if "error" in reply:  # e.g. "unauthorized": not an empty buffer.
                raise CdpError("drain_events", _harness_error(reply["error"]))
            drained = reply.get("events") or []
            with self._lock:
                ours = [e for e in drained if isinstance(e, dict) and e.get("session_id") in self._sessions]
                for event in ours:
                    if len(self._events) == self._events.maxlen:
                        self.dropped += 1
                    self._events.append(event)
                    if event.get("method") == "Inspector.targetCrashed":
                        self._crashed[event["session_id"]] = _session_gone("Target crashed.", event["method"])
                    elif event.get("method") == "Inspector.targetReloadedAfterCrash":
                        self._crashed.pop(event["session_id"], None)
            for event in ours:
                if event.get("method") == "Page.javascriptDialogOpening":
                    accept = DIALOG_ACCEPT.get((event.get("params") or {}).get("type"), False)
                    try:  # Our own socket per request: no deadlock with a call blocked on this dialog.
                        self._send({"method": "Page.handleJavaScriptDialog", "params": {"accept": accept},
                                    "session_id": event["session_id"]}, 5.0)
                    except (OSError, ValueError):
                        pass  # Already closed by the page, or the daemon is gone; callers see that themselves.

    def _poll(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                self._pump()
            except Exception:  # A daemon restart must not kill the poller; events() reports errors to callers.
                pass

    def events(self, session_id: str | None = None, method_prefix: str | None = None) -> list[dict]:
        self._pump()
        with self._lock:
            matched, self._events = _take(self._events, session_id, method_prefix)
        return matched

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._sessions.discard(session_id)  # Late events of a closed page are dropped when drained.
            self._crashed.pop(session_id, None)
            self._events = _take(self._events, session_id, None)[1]

    def close(self) -> None:
        self._closed = True  # The daemon stays up for reuse, as Browser Harness intends; it owns no browser.
        self._stop.set()
        if self._poller is not None and threading.current_thread() is not self._poller:
            self._poller.join(timeout=3)
        self._release()

    def _release(self) -> None:
        lock, self._daemon_lock = self._daemon_lock, None
        if lock is not None:
            lock.close()  # Closing releases the flock; the file stays, so a waiting process never locks a stale inode.


def resolve_ws_url(url: str, timeout: float = 10.0) -> str:
    """Accept a ws(s):// DevTools URL or an http(s):// endpoint resolved through /json/version."""
    if url.startswith(("ws://", "wss://")):
        return url
    handlers = [urllib.request.ProxyHandler({})] if _is_local(url) else []
    with urllib.request.build_opener(*handlers).open(url.rstrip("/") + "/json/version", timeout=timeout) as reply:
        return json.load(reply)["webSocketDebuggerUrl"]


def open_transport(
    browser: str = "auto", *, headless: bool = True, window=(1366, 768), lang: str = "it-IT"
) -> tuple[CdpTransport, LaunchedChrome | None]:
    """auto: BU_CDP_WS, then BU_CDP_URL, then a launched local Chromium, then Browser Harness."""
    if browser.startswith("cdp:"):
        return DirectTransport(resolve_ws_url(browser[4:])), None
    if browser == "harness":
        return HarnessTransport(), None
    if browser not in ("auto", "launch"):
        raise ValueError(f"Unknown browser mode {browser!r}: use auto, launch, harness or cdp:<url>")
    if browser == "auto" and (url := os.environ.get("BU_CDP_WS")):
        return DirectTransport(url), None
    if browser == "auto" and (url := os.environ.get("BU_CDP_URL")):
        return DirectTransport(resolve_ws_url(url)), None
    path = find_chromium()
    if path is None and browser == "auto":
        return HarnessTransport(), None
    chrome = launch_chromium(path=path, headless=headless, window=window, lang=lang)
    try:
        return DirectTransport(chrome.ws_url), chrome
    except BaseException:
        chrome.close()
        raise
