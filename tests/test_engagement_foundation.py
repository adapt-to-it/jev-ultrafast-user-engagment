"""Foundation of the engagement auditor: settings, run store, profiles, CDP transports and the owned Chromium."""

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import OFFLINE_ARGS
from websockets.sync.server import serve

from jev_ultrafast import browser as browser_module
from jev_ultrafast.browser import Browser, StalePage, browser_operation
from jev_ultrafast.engagement import transport as transport_module
from jev_ultrafast.engagement.chrome import find_chromium, launch_chromium
from jev_ultrafast.engagement.profiles import (
    DEVICE_PROFILES,
    PROFILES_VERSION,
    apply_profile,
    close_context,
    new_context,
)
from jev_ultrafast.engagement.schemas import SCHEMA_VERSION, STAGES
from jev_ultrafast.engagement.settings import EngagementSettings
from jev_ultrafast.engagement.store import RUN_ID_RE, RunStore, artifacts_root, host_slug
from jev_ultrafast.engagement.transport import CdpError, DirectTransport, HarnessTransport, open_transport

NOW = datetime(2026, 10, 6, 12, 30, 15, 123456, tzinfo=UTC)

# ---------------------------------------------------------------- settings


def test_settings_defaults_and_round_trip():
    s = EngagementSettings("https://shop.example/")
    assert s.profiles == ["mobile", "desktop"] and s.stages == list(STAGES)
    assert (s.browser, s.headless, s.locale, s.consent, s.repeats) == ("auto", True, "it", "auto", 1)
    assert s.lang == "it-IT"
    data = s.to_dict()
    assert json.loads(json.dumps(data)) == data
    assert EngagementSettings.from_dict({**data, "unknown": 1}) == s


@pytest.mark.parametrize("change", [
    {"url": "ftp://shop.example/"}, {"url": "javascript:alert(1)"}, {"url": None}, {"profiles": ["tablet"]},
    {"profiles": []}, {"profiles": "mobile"}, {"profiles": ["mobile", "mobile"]}, {"profiles": [{}]}, {"stages": []},
    {"stages": ["payment"]}, {"consent": "maybe"}, {"browser": "firefox"}, {"browser": {"mode": "launch"}},
    {"locale": ""}, {"locale": "it IT"}, {"repeats": 0}, {"repeats": "2"}, {"repeats": True}, {"headless": "no"},
    {"settle_timeout_s": 0}, {"settle_timeout_s": float("nan")}, {"settle_timeout_s": float("inf")},
    {"artifacts_dir": 3}, {"browser": "cdp:"}, {"browser": "cdp:localhost:9222"}, {"browser": "cdp:ftp://host/"},
])
def test_settings_reject_invalid_values(change):
    with pytest.raises(ValueError):
        EngagementSettings(**{"url": "https://shop.example/", **change})


def test_settings_accept_a_devtools_endpoint():
    for browser in ("cdp:ws://127.0.0.1:9222/devtools/browser/x", "cdp:http://127.0.0.1:9222"):
        assert EngagementSettings("https://shop.example/", browser=browser).browser == browser


def test_settings_read_the_run_record_shape():
    stored = {"profiles": ["mobile"], "stages": ["home", "pdp"], "locale": "en", "repeats": 1,
              "browser": {"mode": "launch", "product": "Chromium/141", "headless": False},
              "anchors_version": "anchors.v1", "profiles_version": "profiles.v1"}
    s = EngagementSettings.from_dict(stored, url="https://shop.example/")
    assert (s.url, s.browser, s.headless, s.profiles, s.stages, s.lang) == (
        "https://shop.example/", "launch", False, ["mobile"], ["home", "pdp"], "en-US")
    assert EngagementSettings("https://shop.example/", profiles=("desktop",)).profiles == ["desktop"]
    with pytest.raises(ValueError, match="no url"):
        EngagementSettings.from_dict(stored)


# ---------------------------------------------------------------- store


def test_new_run_writes_a_created_run_record(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.new_run("audit", "https://Shop.Example:8443/it/", {"profiles": ["mobile"]}, now=NOW)
    assert run_id == "20261006T123015123456Z_audit_shop.example-8443"
    assert re.fullmatch(RUN_ID_RE, run_id)
    assert store.path(run_id) == tmp_path.resolve() / "shop.example-8443" / run_id
    run = store.load(run_id)
    assert run["schema_version"] == SCHEMA_VERSION and run["status"] == "created" and run["kind"] == "audit"
    assert run["site"] == {"host": "shop.example", "start_url": "https://Shop.Example:8443/it/"}
    assert run["created_at"] == "2026-10-06T12:30:15.123456Z" and run["settings"] == {"profiles": ["mobile"]}
    assert run["pages"] == [] and run["observations"] == [] and run["scores"] is None
    with pytest.raises(ValueError):
        store.new_run("crawl", "https://shop.example/", {})


def test_runs_in_the_same_microsecond_get_distinct_ids(tmp_path):
    store = RunStore(tmp_path)
    ids = {store.new_run("journey", "https://shop.example/", {}, now=NOW) for _ in range(3)}
    assert len(ids) == 3 and all(re.fullmatch(RUN_ID_RE, i) for i in ids)


@pytest.mark.parametrize("value, slug", [
    ("https://www.Shop.example/x?y=1", "www.shop.example"),
    ("http://127.0.0.1:8000/", "127.0.0.1-8000"),
    ("https://münchen.example/", "xn--mnchen-3ya.example"),
    ("shop.example", "shop.example"),
    ("http://../", "unknown"),
])
def test_host_slug(value, slug):
    assert host_slug(value) == slug


def test_a_long_host_still_makes_a_run(tmp_path):
    host = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61])  # 253 characters, the longest valid name
    slug = host_slug(f"https://{host}:8443/")
    assert len(slug) <= 200 and not slug.endswith((".", "-"))
    run_id = RunStore(tmp_path).new_run("journey", f"https://{host}:8443/", {}, now=NOW)
    assert re.fullmatch(RUN_ID_RE, run_id) and RunStore(tmp_path).load(run_id)["site"]["host"] == host


@pytest.mark.parametrize("run_id", [
    "../../etc", "20261006T123015123456Z_audit_..", "20261006T123015123456Z_audit_.hidden",
    "20261006T123015123456Z_crawl_shop.example", "20261006T123015123456Z_audit_shop/../x", "", None,
    "20261006T123015123456Z_audit_shop.example\n", "\uff12\uff10\uff12\uff161006T123015123456Z_audit_shop.example",
])
def test_run_paths_reject_traversal(tmp_path, run_id):
    with pytest.raises(ValueError):
        RunStore(tmp_path).path(run_id)


def test_artifact_files_stay_inside_the_run(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.new_run("audit", "https://shop.example/", {}, now=NOW)
    assert store.write_json(run_id, "snapshots/home.json", {"a": 1}) == "snapshots/home.json"
    assert store.write_bytes(run_id, "shots/home.jpg", b"\xff\xd8") == "shots/home.jpg"
    assert json.loads((store.path(run_id) / "snapshots/home.json").read_text()) == {"a": 1}
    for rel in ("../escape.json", "/tmp/x.json", "a/../../b.json", "C:\\x.json", "a\\b.json", "", None, ".",
                "run.json", "Run.JSON", "steps.jsonl", ".lock", "shots/.hidden.jpg"):
        with pytest.raises(ValueError):
            store.write_json(run_id, rel, {})
        with pytest.raises(ValueError):
            store.write_bytes(run_id, rel, b"")
    assert not (tmp_path / "escape.json").exists()
    assert store.load(run_id)["status"] == "created" and (store.path(run_id) / ".lock").read_bytes() == b""


def test_save_is_atomic_and_update_is_serialized(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.new_run("audit", "https://shop.example/", {}, now=NOW)

    def add(i):
        store.update(run_id, lambda run: run["warnings"].append(i))

    threads = [threading.Thread(target=add, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(store.load(run_id)["warnings"]) == list(range(16))
    assert sorted(p.name for p in store.path(run_id).iterdir()) == [".lock", "run.json"]  # No temporary files left.
    with pytest.raises(KeyError):
        store.update(run_id, lambda run: run["missing"])
    assert store.load(run_id)["status"] == "created"


def test_steps_are_appended_and_a_torn_last_line_is_ignored(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.new_run("journey", "https://shop.example/", {}, now=NOW)
    assert store.read_steps(run_id) == []
    for i in range(3):
        store.append_step(run_id, {"step": i, "operation": "CLICK"})
    with open(store.path(run_id) / "steps.jsonl", "a") as f:
        f.write('{"step": 3, "oper')
    assert [s["step"] for s in store.read_steps(run_id)] == [0, 1, 2]
    store.append_step(run_id, {"step": 4})  # A later append ends the torn line instead of joining it.
    assert [s["step"] for s in store.read_steps(run_id)] == [0, 1, 2, 4]


UPDATER = """
import sys
from jev_ultrafast.engagement.store import RunStore
store, run_id, worker = RunStore(sys.argv[1]), sys.argv[2], int(sys.argv[3])
for i in range(int(sys.argv[4])):
    store.update(run_id, lambda run: run["warnings"].append(worker * 1000 + i))
"""


@pytest.mark.skipif(os.name != "posix", reason="flock serializes processes on POSIX only")
def test_update_is_serialized_across_processes(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.new_run("audit", "https://shop.example/", {}, now=NOW)
    workers = [subprocess.Popen([sys.executable, "-c", UPDATER, str(tmp_path), run_id, str(w), "20"],
                                cwd=Path(__file__).parents[1]) for w in range(4)]
    assert [w.wait(60) for w in workers] == [0, 0, 0, 0]
    assert sorted(store.load(run_id)["warnings"]) == [w * 1000 + i for w in range(4) for i in range(20)]


def test_list_runs_newest_first_with_host_filter(tmp_path):
    store = RunStore(tmp_path)
    a = store.new_run("audit", "https://a.example/", {}, now=datetime(2026, 1, 1, tzinfo=UTC))
    b = store.new_run("journey", "https://b.example/", {}, now=datetime(2026, 3, 1, tzinfo=UTC))
    c = store.new_run("audit", "https://a.example/", {}, now=datetime(2026, 2, 1, tzinfo=UTC))
    store.update(c, lambda run: run.update(status="complete", scores={"overall": {"ers": {"score": 71.5}}}))
    assert [r["run_id"] for r in store.list_runs()] == [b, c, a]
    assert [r["run_id"] for r in store.list_runs(host="https://a.example/x")] == [c, a]
    assert [r["run_id"] for r in store.list_runs(limit=1)] == [b]
    summary = store.list_runs(host="a.example")[0]
    assert summary["status"] == "complete" and summary["ers"] == 71.5 and summary["host"] == "a.example"
    (store.path(a) / "run.json").write_text("{")
    assert store.list_runs(host="a.example")[1]["status"] == "unreadable"


def test_writers_never_create_an_unknown_run(tmp_path):
    store = RunStore(tmp_path)
    missing = "20260101T000000000000Z_audit_nothere.example"
    for write in (lambda: store.save(missing, {"run_id": missing}), lambda: store.append_step(missing, {"step": 0}),
                  lambda: store.write_json(missing, "snapshots/home.json", {}),
                  lambda: store.write_bytes(missing, "shots/home.jpg", b""),
                  lambda: store.update(missing, lambda run: None), lambda: store.load(missing)):
        with pytest.raises(FileNotFoundError, match="Unknown run"):
            write()
    assert list(tmp_path.iterdir()) == [] and store.list_runs() == []


def test_list_runs_matches_a_host_on_any_port_unless_one_is_given(tmp_path):
    store = RunStore(tmp_path)
    local = store.new_run("audit", "http://127.0.0.1:8000/", {}, now=datetime(2026, 1, 1, tzinfo=UTC))
    plain = store.new_run("audit", "http://127.0.0.1/", {}, now=datetime(2026, 2, 1, tzinfo=UTC))
    store.new_run("audit", "http://127.0.0.10:8000/", {}, now=datetime(2026, 3, 1, tzinfo=UTC))
    assert [r["run_id"] for r in store.list_runs(host="127.0.0.1")] == [plain, local]
    assert [r["run_id"] for r in store.list_runs(host="http://127.0.0.1:8000/x")] == [local]
    assert store.list_runs(host="127.0.0.1:9000") == [] and RunStore(tmp_path / "none").list_runs("a.example") == []


def test_artifacts_root_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_ENGAGEMENT_ARTIFACTS", str(tmp_path / "env"))
    assert artifacts_root() == tmp_path / "env"
    assert artifacts_root(str(tmp_path / "explicit")) == tmp_path / "explicit"
    monkeypatch.delenv("JEV_ENGAGEMENT_ARTIFACTS")
    assert artifacts_root() == Path("artifacts/engagement").resolve()


# ---------------------------------------------------------------- profiles


class FakeTransport:
    kind, lossy = "fake", False

    def __init__(self, product="HeadlessChrome/150.0.7000.1"):
        self.product = product
        self.calls = []

    def call(self, method, session_id=None, *, timeout=30.0, **params):
        self.calls.append((method, session_id, params))
        if method == "Browser.getVersion":
            return {"product": self.product, "userAgent": f"Mozilla/5.0 (X11) {self.product} Safari/537.36"}
        if method == "Target.createBrowserContext":
            return {"browserContextId": "ctx-1"}
        if method == "Target.disposeBrowserContext":
            raise CdpError(method, {"code": -32000, "message": "Failed to find context"})
        return {}

    def params(self, method):
        return next(p for m, _, p in self.calls if m == method)


def test_mobile_profile_calls_in_a_valid_order():
    t = FakeTransport()
    applied = apply_profile(t, "s1", "mobile")
    methods = [m for m, _, _ in t.calls]
    assert methods.index("Network.enable") < methods.index("Network.emulateNetworkConditions")
    assert {s for m, s, _ in t.calls if m != "Browser.getVersion"} == {"s1"}
    assert t.params("Network.setCacheDisabled") == {"cacheDisabled": True}
    assert t.params("Network.emulateNetworkConditions") == {
        "offline": False, "latency": 150, "downloadThroughput": 209715, "uploadThroughput": 96000}
    assert t.params("Emulation.setDeviceMetricsOverride") == {
        "width": 390, "height": 844, "deviceScaleFactor": 3, "mobile": True}
    assert t.params("Emulation.setCPUThrottlingRate") == {"rate": 4}
    assert t.params("Emulation.setTouchEmulationEnabled") == {"enabled": True, "maxTouchPoints": 5}
    assert t.params("Emulation.setFocusEmulationEnabled") == {"enabled": True}
    ua = t.params("Emulation.setUserAgentOverride")
    assert "Headless" not in ua["userAgent"] and "Chrome/150.0.0.0 Mobile" in ua["userAgent"]
    assert ua["acceptLanguage"] == "it-IT,it,en" and ua["userAgentMetadata"]["mobile"] is True
    assert applied["version"] == PROFILES_VERSION and applied["user_agent"] == ua["userAgent"]
    assert applied["ua_metadata"] == ua["userAgentMetadata"] and applied["ua_metadata"]["fullVersion"] == "150.0.0.0"
    assert applied["network"]["latency"] == 150 and applied["cpu_rate"] == 4 and applied["cache_disabled"] is True


def test_desktop_profile_is_not_throttled():
    t = FakeTransport()
    applied = apply_profile(t, "s1", "desktop", cache_disabled=False, lang="en-US")
    assert t.params("Network.emulateNetworkConditions") == {
        "offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1}
    assert t.params("Emulation.setCPUThrottlingRate") == {"rate": 1}
    # A fresh page has no touch emulation; turning it "off" would reset headless Chromium's mouse to none.
    assert "Emulation.setTouchEmulationEnabled" not in [m for m, _, _ in t.calls]
    assert t.params("Emulation.setDeviceMetricsOverride") == {
        "width": 1366, "height": 768, "deviceScaleFactor": 1, "mobile": False, "screenWidth": 1366, "screenHeight": 768}
    assert t.params("Emulation.setUserAgentOverride")["acceptLanguage"] == "en-US,en"
    assert applied["network"] is None and applied["metrics"]["deviceScaleFactor"] == 1


def test_profile_data_uses_ordinary_chrome_user_agents():
    for profile in DEVICE_PROFILES.values():
        assert "Headless" not in profile["user_agent"] and re.search(r"Chrome/\d+\.0\.0\.0", profile["user_agent"])
    assert "Mobile" in DEVICE_PROFILES["mobile"]["user_agent"] and "Windows" in DEVICE_PROFILES["desktop"]["user_agent"]


def test_contexts_are_disposable_once():
    t = FakeTransport()
    assert new_context(t) == "ctx-1"
    assert t.params("Target.createBrowserContext") == {"disposeOnDetach": True}
    close_context(t, "ctx-1")  # A context that is already gone is not an error.


# ---------------------------------------------------------------- direct transport against a fake endpoint


@pytest.fixture
def fake_cdp():
    def handler(ws):
        dialogs = []  # ids of "Dialog" calls blocked until the client answers the dialog
        for raw in ws:
            m = json.loads(raw)
            session = m.get("sessionId")
            if m["method"] == "Fail":
                ws.send(json.dumps({"id": m["id"], "error": {"code": -32601, "message": "nope"}}))
            elif m["method"] == "Emit":
                for method, session in (("Network.a", "s1"), ("Page.b", "s1"), ("Network.c", "s2"), ("Page.d", "s1")):
                    ws.send(json.dumps({"method": method, "params": {"n": method}, "sessionId": session}))
                ws.send(json.dumps({"id": m["id"], "result": {}}))
            elif m["method"] == "Detach":  # The tab closes: Chrome never answers calls pending on it.
                ws.send(json.dumps({"method": "Target.detachedFromTarget",
                                    "params": {"sessionId": session, "targetId": "t1"}}))
            elif m["method"] == "Crash":
                ws.send(json.dumps({"method": "Inspector.targetCrashed", "params": {}, "sessionId": session}))
            elif m["method"] == "Revive":  # Someone else reloaded the crashed tab.
                ws.send(json.dumps({"method": "Inspector.targetReloadedAfterCrash", "params": {},
                                    "sessionId": m["params"]["session"]}))
                ws.send(json.dumps({"id": m["id"], "result": {}}))
            elif m["method"] == "Dialog":  # A click that opens a native dialog returns only once it is answered.
                dialogs.append(m["id"])
                ws.send(json.dumps({"method": "Page.javascriptDialogOpening", "sessionId": session,
                                    "params": {"type": m["params"]["type"], "message": "Sei sicuro?", "url": "x"}}))
            elif m["method"] == "Page.handleJavaScriptDialog":
                ws.send(json.dumps({"id": m["id"], "result": {}}))
                ws.send(json.dumps({"method": "Page.javascriptDialogClosed", "sessionId": session,
                                    "params": {"result": m["params"]["accept"], "userInput": ""}}))
                ws.send(json.dumps({"id": dialogs.pop(0), "result": {"answer": m["params"], "session": session}}))
            elif m["method"] != "Hang":
                ws.send(json.dumps({"id": m["id"], "result": {"params": m["params"], "session": session}}))

    with serve(handler, "127.0.0.1", 0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"ws://127.0.0.1:{server.socket.getsockname()[1]}"
        server.shutdown()


def test_direct_transport_replies_errors_and_timeouts(fake_cdp):
    with DirectTransport(fake_cdp) as t:
        assert t.kind == "direct" and t.lossy is False
        assert t.call("Echo", "s9", x=1) == {"params": {"x": 1}, "session": "s9"}
        with pytest.raises(CdpError, match="nope") as error:
            t.call("Fail")
        assert isinstance(error.value, RuntimeError) and error.value.code == -32601
        with pytest.raises(TimeoutError):
            t.call("Hang", timeout=0.2)
        results = []
        workers = [threading.Thread(target=lambda i=i: results.append(t.call("Echo", i=i)["params"]["i"]))
                   for i in range(20)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        assert sorted(results) == list(range(20))


def test_direct_transport_drains_only_matching_events(fake_cdp):
    with DirectTransport(fake_cdp) as t:
        t.call("Emit")
        assert [e["method"] for e in t.events("s1", "Network.")] == ["Network.a"]
        assert t.events("s1", "Network.") == []
        assert [e["method"] for e in t.events(method_prefix="Network.")] == ["Network.c"]
        rest = t.events()
        assert [(e["method"], e["session_id"]) for e in rest] == [("Page.b", "s1"), ("Page.d", "s1")]
        assert rest[0]["params"] == {"n": "Page.b"}


def test_direct_transport_bounds_its_event_buffer(fake_cdp):
    with DirectTransport(fake_cdp, max_events=3) as t:
        t.call("Emit")
        assert [e["method"] for e in t.events()] == ["Page.b", "Network.c", "Page.d"] and t.dropped == 1


def test_direct_transport_discards_a_closed_sessions_events(fake_cdp):
    with DirectTransport(fake_cdp) as t:
        t.call("Emit")
        t.discard("s1")
        assert [(e["method"], e["session_id"]) for e in t.events()] == [("Network.c", "s2")]
        t.call("Emit")  # Late events of the closed page are not buffered either.
        assert [(e["method"], e["session_id"]) for e in t.events()] == [("Network.c", "s2")]


def test_direct_transport_fails_calls_pending_on_a_closed_or_crashed_page_at_once(fake_cdp):
    with DirectTransport(fake_cdp) as t:
        other = []

        def wait_on_another_session():
            try:
                t.call("Hang", "s2", timeout=1.5)
            except Exception as exc:
                other.append(exc)

        bystander = threading.Thread(target=wait_on_another_session)
        bystander.start()
        for method, text in (("Detach", "Session with given id not found. (Target.detachedFromTarget)"),
                             ("Crash", "Target crashed. (Inspector.targetCrashed)")):
            started = time.monotonic()
            with pytest.raises(CdpError, match=re.escape(f"{method}: {text}")) as error:
                t.call(method, "s1", timeout=10)
            assert time.monotonic() - started < 1 and error.value.code == -32001
            assert error.value.data == text.split("(")[1].rstrip(")")
        bystander.join()
        assert [type(e) for e in other] == [TimeoutError]  # A call pending on another session keeps waiting.
        assert [e["method"] for e in t.events()] == ["Target.detachedFromTarget", "Inspector.targetCrashed"]


def test_direct_transport_fails_every_later_call_on_a_crashed_page_at_once(fake_cdp):
    with DirectTransport(fake_cdp) as t:
        with pytest.raises(CdpError):
            t.call("Crash", "s1", timeout=3)
        for _ in range(2):  # Chrome would never answer: no 30 s wait per call, and nothing is sent.
            with pytest.raises(CdpError, match=r"Runtime.evaluate: Target crashed. \(Inspector.targetCrashed\)") as e:
                t.call("Runtime.evaluate", "s1", expression="1")
            assert e.value.code == -32001
        assert t.call("Echo", "s2") == {"params": {}, "session": "s2"}  # Other pages are unaffected.
        t.call("Revive", session="s1")
        wait_until(lambda: "s1" not in t._crashed)
        assert t.call("Echo", "s1")["session"] == "s1"
        with pytest.raises(CdpError):
            t.call("Crash", "s3", timeout=3)
        t.discard("s3")
        assert t._crashed == {}


@pytest.mark.parametrize("kind, accept", [("alert", True), ("beforeunload", True), ("confirm", False),
                                          ("prompt", False), ("unknown", False)])
def test_direct_transport_answers_native_dialogs(fake_cdp, kind, accept):
    with DirectTransport(fake_cdp) as t:
        reply = t.call("Dialog", "s1", timeout=3, type=kind)  # Would hang without the reader's answer.
        assert reply == {"answer": {"accept": accept}, "session": "s1"}
        assert transport_module.DIALOG_ACCEPT.get(kind, False) is accept
        events = t.events("s1", "Page.javascriptDialog")
        assert [(e["method"], e["params"].get("type"), e["params"].get("result")) for e in events] == [
            ("Page.javascriptDialogOpening", kind, None), ("Page.javascriptDialogClosed", None, accept)]


def test_direct_transport_close_wakes_waiters_and_is_idempotent(fake_cdp):
    t = DirectTransport(fake_cdp)
    errors = []

    def wait():
        try:
            t.call("Hang", timeout=10)
        except Exception as exc:
            errors.append(exc)

    waiter = threading.Thread(target=wait)
    waiter.start()
    time.sleep(0.1)
    started = time.monotonic()
    t.close()
    t.close()
    waiter.join(3)
    assert time.monotonic() - started < 3 and isinstance(errors[0], ConnectionError)
    with pytest.raises(ConnectionError):
        t.call("Echo")


# ---------------------------------------------------------------- harness transport and transport selection


@pytest.fixture
def fake_daemon(monkeypatch, tmp_path):
    """browser_harness IPC replaced by a daemon that holds `pending` events until drained and logs requests."""
    import browser_harness._ipc as ipc
    import browser_harness.admin as admin

    daemon = type("FakeDaemon", (), {})()
    daemon.started, daemon.sent, daemon.pending, daemon.eof, daemon.refuse = [], [], [], False, None
    daemon.lock_dir = str(tmp_path)  # Not the real temp dir: parallel test runs and real audits keep their locks.
    monkeypatch.setattr(transport_module, "HARNESS_LOCK_DIR", daemon.lock_dir)
    lock = threading.Lock()

    class Sock:
        def settimeout(self, value):
            pass

        def close(self):
            pass

    def request(_sock, _token, req):
        with lock:
            daemon.sent.append(req)
            if daemon.eof:
                return {}  # What _ipc.request() returns when the daemon closes the socket without answering.
            if daemon.refuse:
                return {"error": daemon.refuse}
            if req.get("meta") == "drain_events":
                out, daemon.pending[:] = list(daemon.pending), []
                return {"events": out}
        if req["method"] == "Target.attachToTarget":
            return {"result": {"sessionId": "s1"}}
        if req["method"] == "Bad.method":
            return {"error": "{'code': -32601, 'message': \"'Bad.method' wasn't found\"}"}  # str(cdp error dict)
        if req["method"] == "Gone.method":
            return {"error": "not_attached"}
        return {"result": {"ok": True}}

    daemon.requests = lambda method: [r for r in list(daemon.sent) if r.get("method") == method]
    monkeypatch.setattr(admin, "ensure_daemon", lambda **kwargs: daemon.started.append(kwargs))
    monkeypatch.setattr(ipc, "connect", lambda name, timeout=1.0: (Sock(), None))
    monkeypatch.setattr(ipc, "request", request)
    return daemon


def test_harness_transport_uses_its_own_daemon_without_title_marker(fake_daemon):
    t = HarnessTransport()
    try:
        assert fake_daemon.started == [{"name": "jev-engagement", "env": {"BH_TAB_MARKER": "0"}}]
        assert t.kind == "harness" and t.lossy is True
        assert t.call("Page.navigate", "s1", url="https://shop.example/") == {"ok": True}
        assert fake_daemon.requests("Page.navigate") == [
            {"method": "Page.navigate", "params": {"url": "https://shop.example/"}, "session_id": "s1"}]
        with pytest.raises(CdpError, match="Bad.method: 'Bad.method' wasn't found") as error:
            t.call("Bad.method")
        assert error.value.code == -32601
        with pytest.raises(CdpError, match="not_attached") as error:
            t.call("Gone.method")
        assert error.value.code is None
    finally:
        t.close()


def test_harness_transport_keeps_only_its_own_sessions_events(fake_daemon):
    t = HarnessTransport()
    try:
        assert t.call("Target.attachToTarget", targetId="t1", flatten=True) == {"sessionId": "s1"}
        fake_daemon.pending += [{"method": "Network.a", "params": {}, "session_id": "s1"},
                                {"method": "Page.b", "params": {}, "session_id": "s1"},
                                {"method": "Page.user", "params": {}, "session_id": "users-own-tab"},
                                {"method": "Target.targetInfoChanged", "params": {}, "session_id": None}]
        assert [e["method"] for e in t.events("s1", "Page.")] == ["Page.b"]
        assert [e["method"] for e in t.events()] == ["Network.a"]  # Kept locally after the first drain.
        t.discard("s1")
        fake_daemon.pending.append({"method": "Page.late", "params": {}, "session_id": "s1"})
        assert t.events() == []  # A closed page's late events are dropped too.
    finally:
        t.close()


def test_harness_transport_answers_dialogs_of_its_own_pages_in_the_background(fake_daemon):
    t = HarnessTransport(poll_interval=0.01)
    try:
        t.call("Target.attachToTarget", targetId="t1", flatten=True)
        fake_daemon.pending += [
            {"method": "Page.javascriptDialogOpening", "params": {"type": "confirm"}, "session_id": "s1"},
            {"method": "Page.javascriptDialogOpening", "params": {"type": "alert"}, "session_id": "users-own-tab"}]
        wait_until(lambda: fake_daemon.requests("Page.handleJavaScriptDialog"))
        time.sleep(0.05)
        assert fake_daemon.requests("Page.handleJavaScriptDialog") == [
            {"method": "Page.handleJavaScriptDialog", "params": {"accept": False}, "session_id": "s1"}]
        assert [e["params"]["type"] for e in t.events(method_prefix="Page.javascriptDialog")] == ["confirm"]
    finally:
        t.close()
    assert not t._poller.is_alive()
    drains = len(fake_daemon.requests(None))
    time.sleep(0.05)
    assert len(fake_daemon.requests(None)) == drains  # close() stops polling.


def test_harness_transport_reports_a_daemon_that_hung_up_or_refused(fake_daemon):
    t = HarnessTransport(poll_interval=None)
    fake_daemon.eof = True
    with pytest.raises(ConnectionError, match="without a reply"):
        t.call("Runtime.evaluate", "s1", expression="1")
    with pytest.raises(ConnectionError):
        t.events()
    fake_daemon.eof, fake_daemon.refuse = False, "unauthorized"
    with pytest.raises(CdpError, match="drain_events: unauthorized"):  # Not an empty buffer.
        t.events()
    t.close()


def test_harness_transport_fails_later_calls_on_a_crashed_page_at_once(fake_daemon):
    t = HarnessTransport(poll_interval=None)
    try:
        t.call("Target.attachToTarget", targetId="t1", flatten=True)
        fake_daemon.pending.append({"method": "Inspector.targetCrashed", "params": {}, "session_id": "s1"})
        assert [e["method"] for e in t.events()] == ["Inspector.targetCrashed"]
        with pytest.raises(CdpError, match=r"Target crashed. \(Inspector.targetCrashed\)") as error:
            t.call("Runtime.evaluate", "s1", expression="1")
        assert error.value.code == -32001 and fake_daemon.requests("Runtime.evaluate") == []
        t.discard("s1")
        assert t.call("Runtime.evaluate", "s1", expression="1") == {"ok": True}
    finally:
        t.close()


def test_one_harness_transport_per_daemon_at_a_time(fake_daemon):
    first = HarnessTransport(poll_interval=None)
    try:
        with pytest.raises(RuntimeError, match=rf"'jev-engagement' is in use by pid {os.getpid()} \(this process\)"):
            HarnessTransport(poll_interval=None)  # It would drain the first one's events and dialogs.
        assert len(fake_daemon.started) == 1
        HarnessTransport(name="jev-other", poll_interval=None).close()  # Another daemon is another lock.
    finally:
        first.close()
    first.close()
    HarnessTransport(poll_interval=None).close()  # close() released the lock.


HARNESS_HOLDER = """
import sys
import browser_harness.admin as admin
from jev_ultrafast.engagement import transport
admin.ensure_daemon = lambda **kwargs: None
transport.HARNESS_LOCK_DIR = sys.argv[1]
t = transport.HarnessTransport(poll_interval=None)
print("locked", flush=True)
sys.stdin.read()
"""


@pytest.mark.skipif(transport_module.fcntl is None, reason="the daemon lock needs fcntl")
def test_harness_transport_refuses_a_daemon_another_process_uses(fake_daemon):
    holder = subprocess.Popen([sys.executable, "-c", HARNESS_HOLDER, fake_daemon.lock_dir], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True, cwd=Path(__file__).parents[1])
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(RuntimeError, match=rf"in use by pid {holder.pid}; use browser='launch' or 'cdp:<url>'"):
            HarnessTransport(poll_interval=None)
        assert fake_daemon.started == []
    finally:
        holder.stdin.close()
        holder.wait(10)
    HarnessTransport(poll_interval=None).close()  # The lock goes with the process, even without close().


@pytest.fixture
def selection(monkeypatch):
    made = []

    class FakeDirect:
        def __init__(self, url):
            if url == "ws://broken":
                raise ConnectionRefusedError(url)
            made.append(("direct", url))

    class FakeHarness:
        def __init__(self):
            made.append(("harness",))

    class FakeChrome:
        ws_url = "ws://launched"
        closed = False

        def close(self):
            FakeChrome.closed = True

    def launch(**kwargs):
        made.append(("launch", kwargs))
        return FakeChrome()

    for key in ("BU_CDP_WS", "BU_CDP_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(transport_module, "DirectTransport", FakeDirect)
    monkeypatch.setattr(transport_module, "HarnessTransport", FakeHarness)
    monkeypatch.setattr(transport_module, "launch_chromium", launch)
    monkeypatch.setattr(transport_module, "find_chromium", lambda: "/opt/chrome")
    monkeypatch.setattr(transport_module, "resolve_ws_url", lambda url: f"ws://resolved/{url}")
    return made, FakeChrome


def test_auto_prefers_cdp_environment(selection, monkeypatch):
    made, _ = selection
    monkeypatch.setenv("BU_CDP_URL", "http://127.0.0.1:9222")
    assert open_transport()[1] is None and made == [("direct", "ws://resolved/http://127.0.0.1:9222")]
    monkeypatch.setenv("BU_CDP_WS", "ws://env")
    open_transport()
    assert made[-1] == ("direct", "ws://env")


def test_auto_launches_chromium_then_falls_back_to_harness(selection, monkeypatch):
    made, FakeChrome = selection
    _, chrome = open_transport(headless=False, window=(390, 844), lang="en-US")
    assert isinstance(chrome, FakeChrome)
    assert made == [("launch", {"path": "/opt/chrome", "headless": False, "window": (390, 844), "lang": "en-US"}),
                    ("direct", "ws://launched")]
    monkeypatch.setattr(transport_module, "find_chromium", lambda: None)
    assert open_transport()[1] is None and made[-1] == ("harness",)


def test_forced_modes(selection, monkeypatch):
    made, _ = selection
    monkeypatch.setenv("BU_CDP_WS", "ws://env")
    open_transport("cdp:ws://forced/devtools")
    open_transport("cdp:http://127.0.0.1:9333")
    open_transport("harness")
    open_transport("launch")
    assert made[:3] == [("direct", "ws://resolved/ws://forced/devtools"),
                        ("direct", "ws://resolved/http://127.0.0.1:9333"), ("harness",)]
    assert made[3][0] == "launch" and made[4] == ("direct", "ws://launched")
    with pytest.raises(ValueError):
        open_transport("firefox")


def test_launched_chromium_is_closed_when_connecting_fails(selection, monkeypatch):
    _, FakeChrome = selection
    FakeChrome.ws_url = "ws://broken"
    with pytest.raises(ConnectionRefusedError):
        open_transport("launch")
    assert FakeChrome.closed


@pytest.mark.skipif(os.name != "posix", reason="uses a shell script as a fake browser")
def test_failed_launch_reports_stderr_and_removes_the_profile(monkeypatch, tmp_path):
    from jev_ultrafast.engagement import chrome as chrome_module

    fake = tmp_path / "fake-chrome"
    fake.write_text("#!/bin/sh\necho 'cannot open display' >&2\nexit 3\n")
    fake.chmod(0o755)
    profile = tmp_path / "profile"
    monkeypatch.setattr(chrome_module.tempfile, "mkdtemp", lambda prefix: str(profile.mkdir() or profile))
    with pytest.raises(RuntimeError, match="exited with code 3(.|\n)*cannot open display"):
        launch_chromium(path=str(fake))
    assert not profile.exists()


def test_find_chromium_honours_explicit_path(monkeypatch, tmp_path):
    chrome = tmp_path / "chrome"
    chrome.write_text("#!/bin/sh\n")
    chrome.chmod(0o755)
    monkeypatch.setenv("JEV_CHROME_PATH", str(chrome))
    assert find_chromium() == str(chrome)


def test_find_chromium_prefers_the_newest_playwright_build_and_warns_on_a_bad_setting(monkeypatch, tmp_path):
    from jev_ultrafast.engagement import chrome as chrome_module

    for build in ("chromium-1181", "chromium-1194", "chromium-tip-of-tree-1400"):
        path = tmp_path / build / "chrome-linux" / "chrome"
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    for key in ("CHROME_PATH", "BH_CHROME_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JEV_CHROME_PATH", str(tmp_path / "missing-chrome"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(chrome_module.shutil, "which", lambda name: None)
    with pytest.warns(UserWarning, match="JEV_CHROME_PATH"):
        assert find_chromium() == str(tmp_path / "chromium-1194" / "chrome-linux" / "chrome")


# ---------------------------------------------------------------- Browser.navigate on the default (harness) path


def fake_harness_cdp(evaluate):
    calls = []

    def cdp(method, session_id=None, **params):
        calls.append(method)
        if method == "Target.createTarget":
            return {"targetId": "t1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "s1"}
        if method == "Runtime.evaluate":
            return evaluate()
        return {}

    return cdp, calls


@pytest.fixture
def harness_browser(monkeypatch):
    monkeypatch.setattr(browser_module, "ensure_daemon", lambda: None)

    def install(evaluate):
        cdp, calls = fake_harness_cdp(evaluate)
        monkeypatch.setattr(browser_module, "cdp", cdp)
        return calls

    return install


def test_browser_fails_at_once_when_its_session_is_gone(harness_browser):
    def evaluate():
        raise RuntimeError("Session with given id not found.")

    calls = harness_browser(evaluate)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="Session with given id not found"):
        Browser("https://shop.example/")
    assert time.monotonic() - started < 1 and calls.count("Runtime.evaluate") == 1
    assert calls.count("Page.navigate") == 1 and calls[-1] == "Target.closeTarget"  # Not retried, tab not leaked.
    assert "Page.enable" not in calls  # The default (harness) path sends the same calls as before.


@pytest.mark.parametrize("error", [CdpError("Runtime.evaluate", {"code": -32001, "message": "Reworded: tab gone"}),
                                   RuntimeError("{'code': -32001, 'message': 'Reworded: tab gone'}"),
                                   RuntimeError("not_attached")])
def test_a_gone_session_is_fatal_whatever_its_wording(harness_browser, error):
    def evaluate():
        raise error

    calls = harness_browser(evaluate)
    with pytest.raises(RuntimeError):
        Browser("https://shop.example/")
    assert calls.count("Runtime.evaluate") == 1


@pytest.mark.parametrize("error", [RuntimeError("cdp_disconnected"), RuntimeError("daemon is shutting down"),
                                   CdpError("Runtime.evaluate", {"message": "cdp_disconnected"})])
def test_errors_chrome_did_not_send_are_fatal_at_once(harness_browser, error):
    def evaluate():
        raise error

    calls = harness_browser(evaluate)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="cdp_disconnected|shutting down"):
        Browser("https://shop.example/")
    assert time.monotonic() - started < 1 and calls.count("Runtime.evaluate") == 1  # Not polled for 15 s.


def test_browser_tolerates_a_redirect_during_the_ready_poll(harness_browser):
    replies = iter([RuntimeError("{'code': -32000, 'message': 'Execution context was destroyed.'}"),
                    RuntimeError("{'code': -32000, 'message': 'Cannot find default execution context'}"),
                    {"exceptionDetails": {}},
                    {"result": {"value": "loading"}}, {"result": {"value": "complete"}}])

    def evaluate():
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    calls = harness_browser(evaluate)
    Browser("https://shop.example/")
    assert calls.count("Runtime.evaluate") == 5 and calls.count("Page.navigate") == 1


def test_unknown_navigation_errors_are_tolerated_until_a_document_answers(harness_browser):
    replies = iter([RuntimeError("{'code': -32000, 'message': 'Some wording a future Chrome uses'}"),
                    CdpError("Runtime.evaluate", {"code": -32000, "message": "Inspected target navigated or closed"}),
                    {"result": {"value": "complete"}}])

    def evaluate():
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    calls = harness_browser(evaluate)
    Browser("https://shop.example/")
    assert calls.count("Runtime.evaluate") == 3


def test_browser_raises_when_no_document_ever_answers(harness_browser):
    def evaluate():
        raise RuntimeError("{'code': -32000, 'message': 'Cannot find default execution context'}")

    harness_browser(evaluate)
    with pytest.raises(RuntimeError, match="default execution context"):
        Browser("https://shop.example/", load_timeout=0.2)


def test_browser_returns_at_the_deadline_once_a_document_was_seen(harness_browser):
    replies = iter([{"result": {"value": "interactive"}}])

    def evaluate():
        reply = next(replies, None)
        if reply is None:
            raise StalePage("Document changed during evaluation")
        return reply

    harness_browser(evaluate)
    assert Browser("https://shop.example/", load_timeout=0.2).session == "s1"  # Collectors own settle.


STATE = {"url": "https://shop.example/", "text": "", "actions": [], "scroll": 0}


def test_default_path_observes_with_the_same_calls(monkeypatch):
    calls = []

    def cdp(method, session_id=None, **params):
        calls.append((method, session_id, params))
        return {"result": {"value": dict(STATE)}} if method == "Runtime.evaluate" else {"data": "jpeg"}

    monkeypatch.setattr(browser_module, "cdp", cdp)
    assert browser_operation({"operation": "observe", "session": "s1"})["screenshot"] == "jpeg"
    assert calls[1] == ("Page.captureScreenshot", "s1", {"format": "jpeg", "quality": 72}) and len(calls) == 2


@pytest.mark.parametrize("stalls, shots", [(0, [5]), (1, [5, 25]), (2, [5, 25])])
def test_a_stalled_screenshot_is_asked_for_once_more_on_a_transport(stalls, shots):
    class ShotTransport:
        def __init__(self):
            self.shots = []

        def call(self, method, session_id=None, *, timeout=30.0, **params):
            if method == "Runtime.evaluate":
                return {"result": {"value": dict(STATE)}}
            assert (method, session_id, params) == ("Page.captureScreenshot", "s1", {"format": "jpeg", "quality": 72})
            self.shots.append(timeout)
            if len(self.shots) <= stalls:
                raise TimeoutError("Page.captureScreenshot got no CDP reply")
            return {"data": "jpeg"}

    t = ShotTransport()
    request = {"operation": "observe", "session": "s1", "screenshot": True}
    if stalls < 2:
        assert browser_operation(request, t)["screenshot"] == "jpeg"
    else:
        with pytest.raises(TimeoutError):  # Read-only, so asked twice; never more.
            browser_operation(request, t)
    assert t.shots == shots


class PageTransport:
    """A transport whose page answers readyState with `ready()`; records each call's timeout."""

    kind, lossy = "fake", False

    def __init__(self, ready, close_error=None):
        self.ready, self.close_error, self.calls, self.discarded = ready, close_error, [], []

    def call(self, method, session_id=None, *, timeout=30.0, **params):
        self.calls.append((method, timeout))
        if method == "Target.createTarget":
            return {"targetId": "t1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "s1"}
        if method == "Target.closeTarget" and self.close_error:
            raise self.close_error
        if method == "Runtime.evaluate":
            return self.ready(timeout)
        return {}

    def discard(self, session_id):
        self.discarded.append(session_id)


def test_navigation_calls_wait_no_longer_than_the_load_timeout():
    def busy(timeout):
        time.sleep(timeout)  # A renderer that never answers.
        raise TimeoutError(f"Runtime.evaluate got no CDP reply within {timeout:g} s")

    t = PageTransport(busy)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        Browser("https://shop.example/", transport=t, load_timeout=1)
    assert time.monotonic() - started < 2
    methods = [m for m, _ in t.calls]
    assert methods[:3] == ["Target.createTarget", "Target.attachToTarget", "Page.enable"]
    assert methods.count("Runtime.evaluate") == 1 and methods[-1] == "Target.closeTarget" and t.discarded == ["s1"]
    assert all(timeout <= 1 for m, timeout in t.calls if m in ("Page.navigate", "Runtime.evaluate"))


def test_navigation_returns_at_the_deadline_when_the_renderer_stalls_after_answering():
    replies = iter([{"result": {"value": "interactive"}}])

    def ready(timeout):
        if (reply := next(replies, None)) is None:
            time.sleep(timeout)
            raise TimeoutError("Runtime.evaluate got no CDP reply")
        return reply

    started = time.monotonic()
    assert Browser("https://shop.example/", transport=PageTransport(ready), load_timeout=0.6).target == "t1"
    assert time.monotonic() - started < 1.5


def test_close_tolerates_a_tab_that_is_already_gone():
    def complete(timeout):
        return {"result": {"value": "complete"}}

    gone = CdpError("Target.closeTarget", {"code": -32602, "message": "No target with given id found"})
    t = PageTransport(complete, close_error=gone)
    b = Browser("https://shop.example/", transport=t)
    b.close()
    b.close()
    assert b.target is None and t.discarded == ["s1"] and [m for m, _ in t.calls].count("Target.closeTarget") == 1
    t.close_error = ConnectionError("CDP connection is closed")
    b = Browser("https://shop.example/", transport=t)
    with pytest.raises(ConnectionError):
        b.close()
    assert b.target is None and t.discarded == ["s1", "s1"]


# ---------------------------------------------------------------- real Chromium (skipped when missing)


def targets(transport):
    return transport.call("Target.getTargets")["targetInfos"]


def wait_until(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.02)


def test_direct_transport_talks_to_chromium(chromium, transport):
    version = transport.call("Browser.getVersion")
    assert "Chrome/" in version["product"] and version["protocolVersion"]
    port = re.search(r":(\d+)/", chromium.ws_url)[1]
    assert transport_module.resolve_ws_url(f"http://127.0.0.1:{port}") == chromium.ws_url


@pytest.mark.parametrize("background", [False, True])
def test_browser_loads_fixture_through_transport(shop_server, transport, background):
    b = Browser(shop_server.url("basic.html"), transport=transport, background=background)
    assert b.evaluate("document.visibilityState") == "visible"  # Paint metrics need a visible page.
    assert b.evaluate("[innerWidth, innerHeight, document.title]") == [1120, 780, "Pagina di prova"]
    assert b.evaluate("navigator.language") == "it-IT"  # launch_chromium(lang="it-IT") without a profile.
    page = b.observe(screenshot=True)
    labels = [a["label"] for a in page["actions"]]
    assert {"Cerca prodotti", "Cerca", "Pagina successiva"} <= set(labels) and page["screenshot"]
    assert any(a["kind"] == "fill" for a in page["actions"]) and any(a["kind"] == "select" for a in page["actions"])
    target = b.target
    b.close()
    b.close()
    assert b.target is None
    wait_until(lambda: all(t["targetId"] != target for t in targets(transport)))  # Closing is asynchronous.


def test_browser_acts_through_the_transport(shop_server, transport):
    b = Browser(shop_server.url("basic.html"), transport=transport, background=False)

    def action(kind, label):
        page = b.observe(screenshot=False)
        return next(a for a in page["actions"] if a["kind"] == kind and a["label"] == label), page

    field, page = action("fill", "Cerca prodotti")
    assert b.act(field, page, text="scarpe") == {"executed": field["id"]}
    button, page = action("click", "Cerca")
    b.act(button, page)
    b.observe(screenshot=False)
    assert b.evaluate("document.getElementById('out').textContent") == "Cerca: scarpe"
    link, page = action("click", "Pagina successiva")
    b.act(link, page)

    def search():
        try:
            return b.evaluate("location.search")
        except (StalePage, RuntimeError):
            return None  # The link's navigation is replacing the document.

    wait_until(lambda: search() == "?page=2")
    assert b.observe(screenshot=True)["screenshot"]
    b.close()


def test_navigate_reports_unreachable_hosts_and_the_test_browser_is_offline(shop_server, transport):
    b = Browser(shop_server.url("basic.html"), transport=transport, background=False)
    result = b.navigate("http://shop.invalid/", load_timeout=5)
    assert result["errorText"] == "net::ERR_PROXY_CONNECTION_FAILED"  # Only loopback bypasses the dead proxy.
    b.close()


class _Stalling(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path in ("/hang.png", "/slow"):
            self.server.release.wait(30)  # Never answered while the test runs.
            return
        body = b"<!doctype html><p>x</p><img src='/hang.png'>"  # The load event never fires.
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def stalling_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stalling)
    server.daemon_threads, server.release = True, threading.Event()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.release.set()
    server.shutdown()
    server.server_close()


def test_navigate_fails_at_once_when_the_tab_is_closed_during_load(transport, stalling_server):
    b = Browser("about:blank", transport=transport, background=False)
    timer = threading.Timer(0.7, lambda: transport.call("Target.closeTarget", targetId=b.target))
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(CdpError, match="not found"):
            b.navigate(f"{stalling_server}/", load_timeout=10)
        assert time.monotonic() - started < 5
    finally:
        timer.join()


def test_navigation_waits_no_longer_than_the_load_timeout(transport, stalling_server):
    before = {t["targetId"] for t in targets(transport)}
    started = time.monotonic()
    with pytest.raises(TimeoutError):  # The server never answers, so Page.navigate never does either.
        Browser(f"{stalling_server}/slow", transport=transport, background=False, load_timeout=1.5)
    assert time.monotonic() - started < 2.5
    wait_until(lambda: {t["targetId"] for t in targets(transport) if t["type"] == "page"} <= before)  # Tab closed.
    b = Browser("about:blank", transport=transport, background=False)
    busy = "data:text/html,<script>const s = Date.now(); while (Date.now() - s < 6000) {}</script><p>x</p>"
    started = time.monotonic()
    try:
        b.navigate(busy, load_timeout=1.5)  # Returns if a poll read the document before the script blocked it.
    except TimeoutError:
        pass  # Its renderer never answered.
    assert time.monotonic() - started < 2.5
    b.close()


def test_prepare_runs_before_navigation_and_network_events_are_kept(shop_server, transport):
    def prepare(browser):
        browser.call("Network.enable")
        browser.call("Page.enable")
        browser.call("Page.addScriptToEvaluateOnNewDocument", source="window.__prepared = document.readyState")

    url = shop_server.url("basic.html?events=1")
    metrics = DEVICE_PROFILES["mobile"]["metrics"]
    b = Browser(url, transport=transport, background=False, prepare=prepare, metrics=metrics)
    assert b.evaluate("window.__prepared") == "loading"
    assert b.evaluate("[innerWidth, devicePixelRatio]") == [390, 3]
    page_events = transport.events(b.session, "Page.")
    assert any(e["method"] == "Page.loadEventFired" for e in page_events)
    network = transport.events(b.session, "Network.")  # Still buffered after draining Page events.
    request = next(e for e in network if e["method"] == "Network.requestWillBeSent")
    assert request["params"]["request"]["url"] == url
    finished = [e for e in network if e["method"] == "Network.loadingFinished"]
    assert finished and finished[0]["params"]["encodedDataLength"] > 0
    b.navigate(shop_server.url("basic.html?page=2"))
    assert b.evaluate("location.search") == "?page=2"
    b.close()


def test_browser_contexts_do_not_share_storage(shop_server, transport):
    first, second = new_context(transport), new_context(transport)
    url = shop_server.url("basic.html")
    a = Browser(url, transport=transport, browser_context_id=first, background=False)
    a.evaluate("document.cookie = 'cart=1; path=/'; localStorage.setItem('cart', '1')")
    same = Browser(url, transport=transport, browser_context_id=first, background=False)
    other = Browser(url, transport=transport, browser_context_id=second, background=False)
    assert same.evaluate("[document.cookie, localStorage.getItem('cart')]") == ["cart=1", "1"]
    assert other.evaluate("[document.cookie, localStorage.getItem('cart')]") == ["", None]
    for context in (first, second):
        close_context(transport, context)
    wait_until(lambda: all(t.get("browserContextId") not in (first, second) for t in targets(transport)))


def test_mobile_profile_reaches_the_page_and_the_server(shop_server, transport):
    context = new_context(transport)
    b = Browser("about:blank", transport=transport, browser_context_id=context, background=False,
                prepare=lambda browser: apply_profile(transport, browser.session, "mobile"))
    url = shop_server.url("basic.html?profile=mobile")
    since = len(shop_server.requests)
    b.navigate(url)
    state = b.evaluate("[innerWidth, devicePixelRatio, navigator.userAgent, navigator.language,"
                       " navigator.maxTouchPoints, document.visibilityState]")
    assert state[:2] == [390, 3] and "Headless" not in state[2] and state[3:] == ["it-IT", 5, "visible"]
    headers = next(r["headers"] for r in shop_server.requests[since:] if r["path"].endswith("profile=mobile"))
    assert "Headless" not in headers["User-Agent"] and "Headless" not in headers.get("sec-ch-ua", "")
    assert headers["Accept-Language"].startswith("it-IT,it;q=0.9")
    timed_fetch = f"(async t => {{ await fetch({json.dumps(url)}); return performance.now() - t; }})(performance.now())"
    elapsed = b.call("Runtime.evaluate", expression=timed_fetch, awaitPromise=True, returnByValue=True)
    elapsed = elapsed["result"]["value"]
    assert elapsed >= 140  # 150 ms emulated round trip.
    close_context(transport, context)


def test_native_dialogs_never_block_the_page(transport):
    page = """<button onclick="window.answer = confirm('Aggiungere la protezione?')">Conferma</button>
    <script>alert('Benvenuto'); addEventListener('beforeunload', e => { e.preventDefault(); e.returnValue = ''; });
    </script>"""
    started = time.monotonic()
    b = Browser("data:text/html," + urllib.parse.quote(page), transport=transport, background=False, load_timeout=5)
    assert time.monotonic() - started < 3  # An unanswered alert would stall the load until the deadline.
    button = next(a for a in b.observe(screenshot=False)["actions"] if a["label"] == "Conferma")
    b.act(button, b.observe(screenshot=False))  # Returns: the confirm opened inside this click was answered.
    assert b.evaluate("window.answer") is False  # A site's question is never confirmed.
    b.navigate("data:text/html,<p>seconda</p>", load_timeout=5)  # The click's user activation arms beforeunload.
    assert b.evaluate("document.body.textContent") == "seconda"
    dialogs = []

    def answers():
        dialogs.extend(transport.events(b.session, "Page.javascriptDialog"))  # Kept for collectors to record.
        opened = [e["params"]["type"] for e in dialogs if e["method"] == "Page.javascriptDialogOpening"]
        return opened, [e["params"]["result"] for e in dialogs if e["method"] == "Page.javascriptDialogClosed"]

    wait_until(lambda: answers() == (["alert", "confirm", "beforeunload"], [True, False, True]))
    b.close()


@pytest.mark.parametrize("profile, expected", [("desktop", [True, True, False, False, 0, 1366, 768]),
                                               ("mobile", [False, False, True, True, 5, 390, 844])])
def test_profiles_present_the_pointer_and_screen_of_their_device(transport, profile, expected):
    context = new_context(transport)
    b = Browser("about:blank", transport=transport, browser_context_id=context, background=False,
                prepare=lambda browser: apply_profile(transport, browser.session, profile))
    b.navigate("data:text/html,<p>x</p>")
    media = [f"matchMedia('({q})').matches" for q in ("hover: hover", "pointer: fine", "pointer: coarse",
                                                         "hover: none")]
    state = b.evaluate(f"[{', '.join(media)}, navigator.maxTouchPoints, screen.width, screen.height]")
    assert state == expected  # Hover menus render on desktop as they do for a mouse user.
    close_context(transport, context)


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="process table check needs /proc")
def test_launch_and_close_leave_no_process_and_no_temp_files(chromium):
    from jev_ultrafast.engagement import chrome as chrome_module

    chrome = launch_chromium(path=find_chromium(), extra_args=OFFLINE_ARGS)

    def owned():
        pids = []
        for entry in Path("/proc").iterdir():
            try:
                if entry.name.isdigit() and chrome.user_data_dir in (entry / "cmdline").read_text():
                    pids.append(int(entry.name))
            except OSError:
                pass
        return pids

    assert chrome.process.pid in owned() and chrome in chrome_module._OPEN
    owner = json.loads((Path(chrome.user_data_dir) / "owner.json").read_text())
    assert (owner["owner_pid"], owner["chrome_pid"]) == (os.getpid(), chrome.process.pid)
    # Chromium's singleton socket dir is in the profile, not the system temp dir a signal would leave it in.
    wait_until(lambda: os.path.islink(Path(chrome.user_data_dir) / "SingletonSocket"))
    singleton = Path(os.readlink(Path(chrome.user_data_dir) / "SingletonSocket")).parent
    assert singleton.is_relative_to(chrome.user_data_dir)
    chrome.close()
    deadline = time.monotonic() + 5
    while owned() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert owned() == [] and not os.path.exists(chrome.user_data_dir) and not singleton.exists()
    assert chrome not in chrome_module._OPEN
    chrome.close()


class _Attachment(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", "attachment; filename=listino.pdf")
        self.send_header("Content-Length", "8")
        self.end_headers()
        self.wfile.write(b"%PDF-1.4")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="HOME decides Chromium's Downloads folder on Linux")
def test_downloads_and_crash_reports_stay_in_the_temporary_profile(chromium, monkeypatch, tmp_path):
    path = find_chromium()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))  # Without the fix the attachment would land in this home, not the real one.
    for key in ("XDG_CONFIG_HOME", "XDG_DOWNLOAD_DIR", "CHROME_CONFIG_HOME"):
        monkeypatch.delenv(key, raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Attachment)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    chrome = launch_chromium(path=path, extra_args=OFFLINE_ARGS)
    try:
        with DirectTransport(chrome.ws_url) as t:
            b = Browser("about:blank", transport=t, background=False)  # The default context, as journeys use it.
            result = b.navigate(f"http://127.0.0.1:{server.server_port}/listino", load_timeout=5)
            assert result["isDownload"] is True
            saved = Path(chrome.user_data_dir) / "downloads" / "listino.pdf"
            wait_until(lambda: saved.exists() and saved.read_bytes() == b"%PDF-1.4", timeout=5)
            b.close()
    finally:
        chrome.close()
        server.shutdown()
        server.server_close()
    # ~/.pki is NSS's shared certificate store, which some Chromium builds create at startup; it holds no site data.
    assert [p.relative_to(home) for p in home.rglob("*") if p.relative_to(home).parts[0] != ".pki"] == []
    assert not os.path.exists(chrome.user_data_dir)


@pytest.mark.skipif(not Path("/proc/self").is_dir(), reason="stale-browser detection needs /proc")
def test_sweep_removes_only_profiles_whose_owner_died(tmp_path):
    from jev_ultrafast.engagement.chrome import sweep_stale_profiles

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()

    def profile(name, owner_pid=None, chrome_pid=None, age=0.0):
        path = tmp_path / f"jev-chrome-{name}"
        path.mkdir()
        if owner_pid is not None:
            (path / "owner.json").write_text(json.dumps({"owner_pid": owner_pid, "chrome_pid": chrome_pid,
                                                         "created": time.time()}))
        os.utime(path, (time.time() - age, time.time() - age))
        return path

    def sleeper(*args):
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", *map(str, args)],
                                start_new_session=True)

    orphan_dir = tmp_path / "jev-chrome-orphan"
    orphan = sleeper(orphan_dir)  # Its command line names the profile, as a browser's --user-data-dir does.
    unrelated = sleeper()  # A process that reused a dead browser's pid.
    in_use_dir = tmp_path / "jev-chrome-in-use"
    user = sleeper(f"--user-data-dir={in_use_dir}")
    try:
        profile("orphan", owner_pid=dead.pid, chrome_pid=orphan.pid)
        profile("reused-pid", owner_pid=dead.pid, chrome_pid=unrelated.pid)
        profile("live-owner", owner_pid=os.getpid(), chrome_pid=dead.pid)
        profile("launching", age=60)  # No owner.json yet: a launch in progress.
        profile("old-unowned", age=3600)
        profile("in-use", age=3600)
        (tmp_path / "not-ours").mkdir()
        removed = sweep_stale_profiles(str(tmp_path))
        assert sorted(Path(p).name for p in removed) == [
            "jev-chrome-old-unowned", "jev-chrome-orphan", "jev-chrome-reused-pid"]
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "jev-chrome-in-use", "jev-chrome-launching", "jev-chrome-live-owner", "not-ours"]
        assert orphan.wait(5) == -9 and unrelated.poll() is None and user.poll() is None
        assert sweep_stale_profiles(str(tmp_path / "missing")) == []
    finally:
        for process in (orphan, unrelated, user):
            process.kill()
            process.wait()
