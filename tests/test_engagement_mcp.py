"""The MCP server through the real protocol: tool listing and annotations, the audit -> judgments -> score -> report
flow (a stand-in audit that stores the golden run's pages, so no browser), a real audit and a host-driven journey on
the fixture shop (headless Chromium; skipped without it), error handling, idle journeys and the stdio entry point.

Clients talk JSON-RPC to the server: in-process over memory streams (mode="legacy") or over stdio to the installed
jev-engage-mcp script. Nothing calls a paid API or the public internet; judges are scripted.
"""

import copy
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters

from jev_ultrafast.engagement import audit as audit_module
from jev_ultrafast.engagement import journey as journey_module
from jev_ultrafast.engagement.chrome import find_chromium
from jev_ultrafast.engagement.collectors import PageCollector
from jev_ultrafast.engagement.mcp_server import build_server
from jev_ultrafast.engagement.service import TASK_PAGE, TASK_PAGE_CHARS, EngagementService
from jev_ultrafast.engagement.store import RunStore, iso_now
from jev_ultrafast.engagement.transport import DirectTransport

RUNS = Path(__file__).with_name("fixtures") / "runs"
GOLDEN = json.loads((RUNS / "audit_complete.json").read_text(encoding="utf-8"))
SHOP = GOLDEN["site"]["start_url"]
TOOLS = {"audit_shop", "wait_run", "get_run", "run_journey", "journey_act", "journey_finish", "get_judgment_tasks",
         "submit_judgments", "finalize_judgments", "score_run", "get_report", "list_runs"}
READ_ONLY = {"wait_run", "get_run", "get_report", "list_runs"}  # get_judgment_tasks creates the tasks once
OPEN_WORLD = {"audit_shop", "run_journey", "journey_act", "journey_finish"}


class ToolFailed(Exception):
    pass


def session(server, scenario):
    """Run `scenario(client)` against the server over JSON-RPC (in-memory streams)."""
    async def main():
        async with Client(server, mode="legacy") as client:
            return await scenario(client)
    return anyio.run(main)


async def call(client, name, **arguments):
    result = await client.call_tool(name, arguments)
    text = result.content[0].text if result.content else ""
    if result.is_error:
        raise ToolFailed(text)
    assert json.loads(text) == result.structured_content  # the text block is the same JSON, compact
    return result.structured_content


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_ENGAGEMENT_CACHE", str(tmp_path / "cache"))  # judge samples never reach ~/.cache
    services = []

    def make(**kwargs):
        made = EngagementService(RunStore(tmp_path / "runs"), **kwargs)
        services.append(made)
        return made
    yield make
    for made in services:
        made.shutdown(join_s=1)


def stand_in_audit(gate: threading.Event | None = None):
    """audit.audit_shop's contract (creates its run with store.new_run, returns the run_id) with the golden run's
    pages and observations instead of a browser."""
    def audit_shop(settings, *, store, transport_factory=None, progress=None):
        run_id = store.new_run("audit", settings.url, {"profiles": settings.profiles, "locale": settings.locale})
        store.update(run_id, lambda run: run.update(status="running"))
        if progress:
            progress("stand-in funnel")
        if gate is not None:
            assert gate.wait(20)

        def fill(run):
            run.update({key: copy.deepcopy(GOLDEN[key]) for key in ("pages", "not_assessable", "deception",
                                                                      "observations")})
            run.update(status="partial", finished_at=iso_now())
        store.update(run_id, fill)
        return run_id
    return audit_shop


def verdicts_for(page: dict, judge: str) -> list[dict]:
    """Scripted verdicts that quote each task's first snippet verbatim; j3 disagrees on the first task."""
    out = []
    for i, task in enumerate(page["tasks"]):
        labels = sorted(page["rubrics"][task["rubric_id"]]["labels"])
        label = labels[-1] if judge == "j3" and i == 0 else labels[0]
        snippet = task["snippets"][0]
        out.append({"task_id": task["task_id"], "label": label, "confidence": 0.7,
                    "evidence": [{"snippet_id": snippet["snippet_id"], "quote": snippet["text"][:40]}],
                    "rationale": "Il testo citato basta a decidere."})
    return out


# ---------------------------------------------------------------- listing


def test_tools_carry_names_annotations_and_the_rules_the_host_reads(service):
    server = build_server(service())

    async def scenario(client):
        return client.instructions, (await client.list_tools()).tools
    instructions, tools = session(server, scenario)
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        hints = t.annotations
        assert hints.destructive_hint is False, t.name
        assert hints.read_only_hint is (t.name in READ_ONLY), t.name
        assert hints.open_world_hint is (t.name in OPEN_WORLD), t.name
        assert t.output_schema["type"] == "object" and t.description, t.name
    by_name = {t.name: t for t in tools}
    assert "observation_id" in by_name["journey_act"].input_schema["required"]
    assert "never resend" in by_name["journey_act"].description
    assert "verbatim" in by_name["get_judgment_tasks"].description
    assert by_name["get_judgment_tasks"].input_schema["properties"]["limit"]["maximum"] == TASK_PAGE
    assert by_name["get_judgment_tasks"].annotations.idempotent_hint is True
    assert [t.name for t in tools if (t.meta or {}).get("anthropic/alwaysLoad")] == ["get_judgment_tasks"]
    assert by_name["audit_shop"].input_schema["properties"]["profiles"]["anyOf"][0]["minItems"] == 1
    assert "never a selector" in by_name["journey_act"].input_schema["properties"]["target"]["description"].lower()
    for phrase in ("never measured engagement", "predicted friction", "risk signals", "never place orders",
                   "offered element index"):
        assert phrase in instructions


# ---------------------------------------------------------------- audit -> judgments -> score -> report


def test_audit_judgments_score_and_report_through_the_protocol(service, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(audit_module, "audit_shop", stand_in_audit(gate))
    svc = service()
    server = build_server(svc)

    async def scenario(client):
        started = await call(client, "audit_shop", url=SHOP, profiles=["mobile", "desktop"])
        run_id = started["run_id"]
        assert started["status"] == "running" and Path(started["artifacts_dir"]).is_dir()
        early = await call(client, "wait_run", run_id=run_id, timeout_s=0.2)
        assert early["timed_out"] and early["live"] and early["status"] == "running"
        for _ in range(100):
            if (await call(client, "get_run", run_id=run_id)).get("progress") == "stand-in funnel":
                break
            await anyio.sleep(0.05)
        else:
            raise AssertionError("the audit's progress never reached get_run")
        with pytest.raises(ToolFailed, match="still running"):
            await call(client, "get_judgment_tasks", run_id=run_id)
        gate.set()
        done = await call(client, "wait_run", run_id=run_id, timeout_s=20)
        assert not done["timed_out"] and done["status"] == "partial" and done["pages_total"] == len(GOLDEN["pages"])
        assert done["judgments"] == {"prepared": False} and done["scores"] == {"available": False}
        assert len(json.dumps(done)) < 20_000

        brief = await call(client, "get_judgment_tasks", run_id=run_id, brief=True)
        total = brief["total"]
        assert total > 5 and "snippets" not in brief["tasks"][0] and "rubrics" not in brief
        seen, cursor, pages = [], 0, []
        while cursor is not None:
            page = await call(client, "get_judgment_tasks", run_id=run_id, cursor=cursor, limit=5)
            assert len(page["tasks"]) <= 5 and set(page["rubrics"]) == {t["rubric_id"] for t in page["tasks"]}
            assert all(len(s["text"]) <= 600 for t in page["tasks"] for s in t["snippets"])
            seen += [t["task_id"] for t in page["tasks"]]
            pages.append(page)
            cursor = page["next_cursor"]
        assert len(seen) == len(set(seen)) == total
        with pytest.raises(ToolFailed):  # the schema caps a page at 15 tasks
            await call(client, "get_judgment_tasks", run_id=run_id, limit=50)

        first = pages[0]["tasks"][0]
        bad = {"task_id": first["task_id"], "label": sorted(pages[0]["rubrics"][first["rubric_id"]]["labels"])[0],
               "confidence": 0.9, "evidence": [{"snippet_id": first["snippets"][0]["snippet_id"],
                                                "quote": "testo che la pagina non contiene affatto"}],
               "rationale": "x"}
        refused = await call(client, "submit_judgments", run_id=run_id, judge_id="j1", model="claude-sonnet-test",
                             verdicts=[bad, {"task_id": "made:up", "label": "present", "confidence": 1}])
        assert refused["accepted"] == 0
        assert refused["rejected_reasons"] == {"quote_not_verbatim": 1, "unknown_task": 1}
        for judge in ("j1", "j2", "j3"):
            for page in pages:
                result = await call(client, "submit_judgments", run_id=run_id, judge_id=judge,
                                    model="claude-sonnet-test", verdicts=verdicts_for(page, judge))
                assert result["accepted"] == len(page["tasks"]) and result["rejected_total"] == 0
        final = await call(client, "finalize_judgments", run_id=run_id)
        assert final["final"] == final["decided"] == total and final["pending"] == []
        assert final["disagreements"] == [page["tasks"][0]["task_id"] for page in pages]  # j3 vs j1 + j2

        scored = await call(client, "score_run", run_id=run_id)
        assert scored["confidence"].startswith("Confidenza ") and "con giudizi LLM" in scored["scope"]
        assert set(scored["sub_indices"]) == {"PERF", "FAI", "TRI", "PTI", "CCL", "MPI"}
        assert scored["ers"]["llm_share"] > 0 and "non engagement misurato" in scored["vocabulary"]
        for path in scored["report"].values():
            assert Path(path).is_absolute() and Path(path).is_file()
        summary = await call(client, "get_report", run_id=run_id)
        kpis = await call(client, "get_report", run_id=run_id, format="kpis")
        paths = await call(client, "get_report", run_id=run_id, format="paths")
        assert summary["scope"] == scored["scope"] and summary["judgments"]["final"] == total
        assert len(kpis["kpis"]) > 80 and paths["exists"] and paths["report_html"] == scored["report"]["report_html"]
        listed = await call(client, "list_runs", host="shop.example")
        assert [r["run_id"] for r in listed["runs"]] == [run_id] and listed["runs"][0]["ers"] == scored["ers"]["score"]
        again = await call(client, "get_run", run_id=run_id)
        assert again["scores"]["available"] and again["judgments"]["final"] == total
        return [len(json.dumps(x)) for x in (summary, kpis, scored, *pages)]
    sizes = session(server, scenario)
    assert max(sizes) < 40_000  # every result stays far below the host's 25k-token tool output limit


def test_an_audit_without_judgments_is_scored_as_unjudged(service, monkeypatch):
    monkeypatch.setattr(audit_module, "audit_shop", stand_in_audit())
    svc = service()
    run_id = svc.audit_shop(SHOP, wait=True)["run_id"]
    scored = svc.score_run(run_id)
    assert "senza giudizi LLM" in scored["scope"]
    assert "judgments" in svc.store.load(run_id) and svc.get_run(run_id)["judgments"] == {"prepared": False}
    with pytest.raises(ValueError, match="get_judgment_tasks first"):
        svc.finalize_judgments(run_id)


def test_brief_and_full_pages_share_their_cursors_when_snippets_are_long(service, monkeypatch):
    """The orchestrator pages with brief=True and each judge reads the full page at the same cursor: with long
    snippets a full page ends before 15 tasks, and the brief page must end at the same task."""
    monkeypatch.setattr(audit_module, "audit_shop", stand_in_audit())
    svc = service()
    run_id = svc.audit_shop(SHOP, wait=True)["run_id"]
    total = svc.get_judgment_tasks(run_id, brief=True)["total"]

    def inflate(run):  # 8 snippets of 600 characters per task, the limits of grouping and judgments
        for i, task in enumerate(run["judgments"]["tasks"]):
            text = f"Testo lungo {i} " + "parole della pagina " * 40
            task["snippets"] = [{"snippet_id": f"s{i}-{k}", "kind": "body", "text": text[:600]} for k in range(8)]
    svc.store.update(run_id, inflate)

    def walk(brief):
        cursors, seen, cursor = [], [], 0
        while cursor is not None:
            page = svc.get_judgment_tasks(run_id, cursor, brief=brief)
            cursors.append(cursor)
            seen += [t["task_id"] for t in page["tasks"]]
            cursor = page["next_cursor"]
        return cursors, seen
    brief, full = walk(True), walk(False)
    assert brief == full and len(full[1]) == len(set(full[1])) == total
    assert len(full[0]) > -(-total // TASK_PAGE)  # some pages were cut by size, not by count
    first = svc.get_judgment_tasks(run_id, 0)
    assert len(json.dumps(first["tasks"], ensure_ascii=False)) <= TASK_PAGE_CHARS + 1000
    page = svc.get_judgment_tasks(run_id, 0, brief=True)
    assert page["missing_samples"] == 3 and page["open_tasks"] == total
    snippet = first["tasks"][0]["snippets"][0]
    verdict = {"task_id": first["tasks"][0]["task_id"], "label": sorted(first["rubrics"][first["tasks"][0][
        "rubric_id"]]["labels"])[0], "confidence": 0.8, "rationale": "Basta il testo.",
        "evidence": [{"snippet_id": snippet["snippet_id"], "quote": snippet["text"][:30]}]}
    assert svc.submit_judgments(run_id, "j1", "m", [verdict])["accepted"] == 1
    again = svc.get_judgment_tasks(run_id, 0, brief=True)
    assert again["next_cursor"] == page["next_cursor"] and again["tasks"][0]["samples_submitted"] == 1


def test_errors_reach_the_host_as_messages(service, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    server = build_server(service())
    missing = "20260101T000000000000Z_audit_nowhere.example"

    async def scenario(client):
        messages = {}
        for name, arguments in (
            ("get_run", {"run_id": missing}),
            ("get_run", {"run_id": "../../etc"}),
            ("journey_act", {"run_id": missing, "operation": "CLICK", "target": "1", "observation_id": 1}),
            ("journey_act", {"run_id": missing, "operation": "CLICK", "target": "1"}),
            ("journey_act", {"run_id": missing, "operation": "HOVER", "observation_id": 1}),
            ("audit_shop", {"url": "ftp://shop.example/"}),
            ("audit_shop", {"url": SHOP, "profiles": []}),
            ("audit_shop", {"url": SHOP, "stages": []}),
            ("run_journey", {"url": "https://shop.example/", "goal": "Trova scarpe", "oracle": "cart_not_empty",
                             "policy": "typesafe"}),
            ("get_report", {"run_id": missing}),
        ):
            with pytest.raises(ToolFailed) as failure:
                await call(client, name, **arguments)
            messages[(name, json.dumps(arguments))] = str(failure.value)
        return list(messages.values())
    unknown, invalid, act_unknown, no_id, bad_op, bad_url, no_profiles, no_stages, typesafe, report = session(
        server, scenario)
    assert "Unknown run" in unknown and "Invalid run id" in invalid and "Unknown run" in act_unknown
    assert "observation_id" in no_id and "operation" in bad_op and "http(s)" in bad_url
    assert "profiles" in no_profiles and "stages" in no_stages
    assert "TYPESAFE_API_KEY" in typesafe and "Unknown run" in report


# ---------------------------------------------------------------- idle journeys


class FakeRunner:
    """JourneyRunner's surface without a browser: start/act/finish/close and a status."""

    instances: list = []

    def __init__(self, settings, *, store, transport_factory=None):
        self.store, self.status, self.run_id = store, "created", None
        self.calls = []
        FakeRunner.instances.append(self)

    def start(self, url, goal, *, oracle, **kwargs):
        self.run_id = self.store.new_run("journey", url, {})
        self.store.update(self.run_id, lambda run: run.update(status="running", journey={
            "goal": goal, "oracle": oracle, "status": "running", "verification": None}))
        self.status = "running"
        return {"run_id": self.run_id, "status": "running", "observation": {"observation_id": 1}}

    def act(self, operation, target=None, text=None, *, observation_id):
        self.calls.append(("act", operation))
        if operation == "DONE":
            self.status = "done"
        return {"executed": operation != "DONE", "stale": False, "status": self.status}

    def finish(self, status=None):
        if status not in (None, "done", "blocked"):
            raise ValueError("status must be one of ('done', 'blocked')")
        self.calls.append(("finish", status))
        verification = {"passed": True, "checks": {}}
        self.store.update(self.run_id, lambda run: (run.update(status="complete"), run["journey"].update(
            status=self.status, verification=verification)))
        return {"run_id": self.run_id, "status": self.status, "verification": verification, "steps": 1}

    def close(self):
        self.calls.append(("close", None))
        self.store.update(self.run_id, lambda run: run.update(status="partial"))


def test_idle_journeys_are_abandoned_or_finished_and_shutdown_closes_the_rest(service, monkeypatch):
    monkeypatch.setattr(journey_module, "JourneyRunner", FakeRunner)
    FakeRunner.instances = []
    now = [0.0]
    svc = service(clock=lambda: now[0], idle_timeout_s=600, reap_interval_s=3600)
    idle = svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty")["run_id"]
    stopped = svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty")["run_id"]
    busy = svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty")["run_id"]
    with pytest.raises(RuntimeError, match="journeys are open"):
        svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty")
    svc.journey_act(stopped, "DONE", observation_id=1)  # the runner stopped by itself; nobody called finish
    now[0] = 500
    svc.journey_act(busy, "SCROLL_DOWN", observation_id=1)
    assert svc.reap() == []
    now[0] = 700
    assert sorted(svc.reap()) == sorted([idle, stopped])
    runner_idle, runner_stopped, runner_busy = FakeRunner.instances
    assert runner_idle.calls == [("close", None)] and ("finish", None) in runner_stopped.calls
    run = svc.store.load(idle)
    assert run["journey"]["status"] == "abandoned" and run["finished_at"]
    assert any(w.startswith("abandoned: no journey_act for 10 min") for w in run["warnings"])
    assert svc.store.load(stopped)["journey"]["verification"]["passed"] is True
    with pytest.raises(ValueError, match="not open in this server"):
        svc.journey_act(idle, "CLICK", "1", observation_id=1)
    assert svc.journey_finish(stopped)["verification"]["passed"] is True  # read back from the run
    svc.shutdown(join_s=1)
    assert runner_busy.calls[-1] == ("close", None) and svc.store.load(busy)["journey"]["status"] == "abandoned"
    assert svc.reap(force=True) == []


def test_journey_act_needs_the_observation_id_and_a_bad_finish_keeps_the_journey(service, monkeypatch):
    monkeypatch.setattr(journey_module, "JourneyRunner", FakeRunner)
    svc = service()
    run_id = svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty")["run_id"]
    with pytest.raises(ValueError, match="observation_id is required"):
        svc.journey_act(run_id, "SCROLL_DOWN")
    with pytest.raises(ValueError, match="status must be"):
        svc.journey_finish(run_id, "abandoned")
    assert svc.journey_act(run_id, "SCROLL_DOWN", observation_id=1)["executed"]  # still open
    assert svc.journey_finish(run_id, "done")["verification"]["passed"] is True


def test_a_journey_being_reaped_stays_open_until_it_is_closed(service, monkeypatch):
    """The reaper holds a stopped journey while its oracle runs: journey_finish waits and reads the verdict back."""
    gate, entered = threading.Event(), threading.Event()

    class SlowFinish(FakeRunner):
        def finish(self, status=None):
            entered.set()
            assert gate.wait(10)
            return super().finish(status)
    monkeypatch.setattr(journey_module, "JourneyRunner", SlowFinish)
    now = [0.0]
    svc = service(clock=lambda: now[0], idle_timeout_s=4, reap_interval_s=3600)
    run_id = svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty")["run_id"]
    svc.journey_act(run_id, "DONE", observation_id=1)
    now[0] = 10
    reaper = threading.Thread(target=svc.reap)
    reaper.start()
    assert entered.wait(5)
    finished = {}
    host = threading.Thread(target=lambda: finished.update(svc.journey_finish(run_id)))
    host.start()
    time.sleep(0.2)
    assert not finished and run_id in svc._journeys  # still registered while the reaper closes it
    gate.set()
    reaper.join(5)
    host.join(5)
    assert finished["verification"]["passed"] is True and run_id not in svc._journeys
    assert "finished by the server: no journey_act for 4 s" in svc.store.load(run_id)["warnings"]
    assert not (svc.store.path(run_id) / "owner.json").exists()


def test_an_interrupted_audit_stays_failed_whatever_its_thread_writes_later(service, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(audit_module, "audit_shop", stand_in_audit(gate))
    svc = service()
    run_id = svc.audit_shop(SHOP)["run_id"]
    assert json.loads((svc.store.path(run_id) / "owner.json").read_text())["owner_pid"] == os.getpid()
    with pytest.raises(RuntimeError, match="already running"):
        svc.audit_shop(SHOP)  # one audit at a time: two would skew each other's timings
    svc.shutdown(join_s=0.2)
    assert svc.store.load(run_id)["status"] == "failed"
    gate.set()  # the stand-in now stores its pages and status "partial", as audit.py's finish() would
    svc._jobs[run_id].done.wait(10)
    run = svc.store.load(run_id)
    assert run["status"] == "failed" and run["pages"] and run["errors"][0].startswith("interrupted: the server shut")
    assert not (svc.store.path(run_id) / "owner.json").exists()
    with pytest.raises(RuntimeError, match="shutting down"):
        svc.audit_shop(SHOP)


def test_abort_marks_the_runs_at_once_without_touching_a_browser(service, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(audit_module, "audit_shop", stand_in_audit(gate))
    monkeypatch.setattr(journey_module, "JourneyRunner", FakeRunner)
    FakeRunner.instances = []
    svc = service()
    journey = svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty")["run_id"]
    audit = svc.audit_shop(SHOP)["run_id"]
    for run_id, peer in ((journey, audit), (audit, journey)):
        assert f"concurrent run {peer} in this process: timings may be skewed" in svc.store.load(run_id)["warnings"]
    started = time.monotonic()
    svc.abort("the server was stopped (signal 2)")
    assert time.monotonic() - started < 0.5
    assert FakeRunner.instances[0].calls == []  # no close(), no finish(): the browsers are already gone
    svc.journey_act(journey, "SCROLL_DOWN", observation_id=1)  # a step still in flight writes after the mark
    FakeRunner.instances[0].store.update(journey, lambda run: run["journey"].update(status="running"))
    gate.set()
    svc._jobs[audit].done.wait(10)
    run = svc.store.load(journey)
    assert run["status"] == "partial" and run["journey"]["status"] == "abandoned"
    assert "abandoned: the server was stopped (signal 2); closed without verification" in run["warnings"]
    run = svc.store.load(audit)
    assert run["status"] == "failed" and "interrupted: the server was stopped (signal 2)" in run["errors"][0]


def test_runs_of_a_process_that_died_are_marked_failed_not_waited_for(service, monkeypatch):
    if os.name != "posix":
        pytest.skip("owner liveness is only checked on POSIX")
    monkeypatch.setattr(journey_module, "JourneyRunner", FakeRunner)
    dead = int(subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True,
                              text=True, check=True).stdout)
    store = RunStore(service().store.root)

    def orphan(kind, owner):
        run_id = store.new_run(kind, SHOP, {})
        store.update(run_id, lambda run: run.update(status="running", journey={
            "goal": "g", "oracle": "cart_not_empty", "status": "running", "verification": None}
            if kind == "journey" else None))
        store.write_json(run_id, "owner.json", owner)
        return run_id
    here = socket.gethostname()
    audit = orphan("audit", {"owner_pid": dead, "owner_start": None, "host": here})
    journey = orphan("journey", {"owner_pid": dead, "owner_start": None, "host": here})
    reused = orphan("audit", {"owner_pid": os.getpid(), "owner_start": -1, "host": here})
    alive = orphan("audit", {"owner_pid": os.getpid(), "owner_start": None, "host": here})
    elsewhere = orphan("audit", {"owner_pid": dead, "owner_start": None, "host": here + ".other"})
    svc = service()
    started = time.monotonic()
    waited = svc.wait_run(audit, 30)
    assert time.monotonic() - started < 5 and not waited["timed_out"] and waited["status"] == "failed"
    assert waited["errors"] == [f"interrupted: owning process {dead} is gone"]
    with pytest.raises(ValueError, match=r"not open in this server \(status abandoned\)"):
        svc.journey_finish(journey)
    assert svc.store.load(journey)["status"] == "failed"
    with pytest.raises(ValueError, match="still running"):
        svc.score_run(alive)
    assert svc.recover_orphans() == [reused]  # a live pid with another start time is a reused pid
    assert svc.get_run(alive)["status"] == svc.get_run(elsewhere)["status"] == "running"
    assert not any((svc.store.path(r) / "owner.json").exists() for r in (audit, journey, reused))
    scored = svc.score_run(audit, [journey])
    assert any("without verification" in w for w in scored["warnings"])


def test_wait_run_ends_when_the_call_is_cancelled_or_the_service_stops(service, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(audit_module, "audit_shop", stand_in_audit(gate))
    svc = service()
    run_id = svc.audit_shop(SHOP)["run_id"]
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    started = time.monotonic()
    assert svc.wait_run(run_id, 100, cancel=cancel)["timed_out"] and time.monotonic() - started < 2
    threading.Timer(0.2, svc._stop.set).start()
    assert svc.wait_run(run_id, 100)["timed_out"] and time.monotonic() - started < 4
    gate.set()


# ---------------------------------------------------------------- the fixture shop (headless Chromium)

EURO = re.compile(r"^(\d+),(\d\d) €$")


def index_of(observation, pattern, operation="CLICK"):
    return next((e["index"] for e in observation["elements"]
                 if re.search(pattern, e["label"], re.I) and operation in e["operations"]), None)


def priced_link(observation, limit):
    lines = [line.strip() for line in observation["text_excerpt"].split("\n")]
    for element in observation["elements"]:
        if element["role"] == "link" and element["label"] in lines:
            at = lines.index(element["label"])
            for line in lines[at + 1:at + 4]:
                if (match := EURO.match(line)) and float(f"{match[1]}.{match[2]}") < limit:
                    return element["index"]
    return None


def host_choice(observation):
    """The test's host: reject consent, then category -> product under 50 EUR -> add -> cart -> DONE."""
    if consent := index_of(observation, r"^Rifiuta tutti$"):
        return "CLICK", consent
    kind = observation["page_type"]
    if kind == "home":
        return "CLICK", index_of(observation, r"^Scarpe da corsa$")
    if kind == "plp":
        product = priced_link(observation, 50)
        return ("CLICK", product) if product else ("SCROLL_DOWN", None)
    if kind == "pdp":
        for pattern in (r"^Vai al carrello$", r"^Aggiungi al carrello$"):
            if found := index_of(observation, pattern):
                return "CLICK", found
        return "SCROLL_DOWN", None
    return ("DONE", None) if kind == "cart" else ("BLOCKED", None)


def test_audit_and_host_journey_on_the_fixture_shop(chromium, shop_server, service, monkeypatch):
    monkeypatch.setattr(PageCollector, "net_quiet_s", 0.8)
    monkeypatch.setattr(PageCollector, "lcp_quiet_s", 0.8)
    svc = service(transport_factory=lambda: DirectTransport(chromium.ws_url))
    server = build_server(svc)
    url = shop_server.url("shop/index.html")
    since = len(shop_server.requests)

    async def scenario(client):
        started = await call(client, "audit_shop", url=url, profiles=["desktop"])
        audit = started["run_id"]
        for _ in range(5):
            done = await call(client, "wait_run", run_id=audit, timeout_s=110)
            if not done["timed_out"]:
                break
        assert done["status"] == "complete" and not done["errors"], done
        assert [p["type"] for p in done["pages"]] == ["home", "plp", "pdp", "cart", "checkout"]

        journey = await call(client, "run_journey", url=url, profile="desktop", oracle="cart_contains_item_under_price",
                             goal="Aggiungi al carrello un paio di scarpe da corsa che costi meno di 50 euro",
                             oracle_params={"max_price": "50"}, optimal_steps=5, optimal_pages=4)
        run_id, observation = journey["run_id"], journey["observation"]
        assert journey["status"] == "running" and observation["observation_id"] == 1
        first = observation["observation_id"]
        with pytest.raises(ToolFailed, match="not offered|not an offered"):
            await call(client, "journey_act", run_id=run_id, operation="SELECT", target="1:1", observation_id=first)
        for target, message in (("#add-to-cart", "not an offered element index"),
                                ("button.add", "not an offered element index"), ("999", "offered")):
            with pytest.raises(ToolFailed, match=message):
                await call(client, "journey_act", run_id=run_id, operation="CLICK", target=target,
                           observation_id=first)
        with pytest.raises(ToolFailed, match="personal or payment data"):
            field = next(e["index"] for e in observation["elements"] if "TYPE_TEXT" in e["operations"])
            await call(client, "journey_act", run_id=run_id, operation="TYPE_TEXT", target=field,
                       text="mario.rossi@example.com", observation_id=first)
        operation, target = host_choice(observation)
        acted = await call(client, "journey_act", run_id=run_id, operation=operation, target=target,
                           observation_id=first)
        assert acted["executed"] and not acted["stale"] and acted["observation"]["step"] == 1
        replay = await call(client, "journey_act", run_id=run_id, operation=operation, target=target,
                            observation_id=first)  # a repeated call: nothing runs, the latest observation returns
        assert replay["stale"] and not replay["executed"] and replay["observation"]["step"] == 1
        observation = replay["observation"]
        for _ in range(20):
            operation, target = host_choice(observation)
            result = await call(client, "journey_act", run_id=run_id, operation=operation, target=target,
                                observation_id=observation["observation_id"])
            observation = result["observation"]
            if result["status"] != "running":
                break
        assert result["status"] == "done" and observation["page_type"] == "cart"
        finished = await call(client, "journey_finish", run_id=run_id)
        assert finished["verification"]["passed"] is True and finished["friction"]["FAI.JOURNEY_SUCCESS"] is True
        with pytest.raises(ToolFailed, match="not open in this server"):
            await call(client, "journey_act", run_id=run_id, operation="WAIT", observation_id=99)

        tasks = await call(client, "get_judgment_tasks", run_id=audit)
        assert 0 < len(tasks["tasks"]) <= TASK_PAGE
        for judge in ("j1", "j2", "j3"):
            submitted = await call(client, "submit_judgments", run_id=audit, judge_id=judge, model="claude-sonnet-test",
                                   verdicts=verdicts_for(tasks, judge))
            assert submitted["rejected_total"] == 0
        await call(client, "finalize_judgments", run_id=audit)
        scored = await call(client, "score_run", run_id=audit, journey_run_ids=[run_id])
        assert "con journey" in scored["scope"] and scored["journey_runs"] == [run_id]
        assert Path(scored["report"]["report_html"]).is_file()
        return scored
    session(server, scenario)
    requests = shop_server.requests[since:]
    assert not [r for r in requests if r["method"] != "GET" or r["path"].startswith("/pay")]


# ---------------------------------------------------------------- stdio


def test_the_installed_stdio_server_speaks_the_protocol(tmp_path):
    script = Path(sys.executable).with_name("jev-engage-mcp")
    if not script.exists():
        pytest.skip("jev-engage-mcp is not installed in this environment")
    env = {**os.environ, "JEV_ENGAGEMENT_ARTIFACTS": str(tmp_path / "runs"), "JEV_ENGAGEMENT_CACHE": str(tmp_path)}

    async def main():
        async with Client(StdioServerParameters(command=str(script), env=env)) as client:
            tools = await client.list_tools()
            listed = await call(client, "list_runs")
            return {t.name for t in tools.tools}, listed
    names, listed = anyio.run(main)
    assert names == TOOLS and listed == {"root": str((tmp_path / "runs").resolve()), "runs": []}


def test_sigterm_ends_the_stdio_server_at_once(tmp_path):
    """The stdio reader blocks on stdin until the host closes it; SIGTERM still ends the server, cleaning up."""
    script = Path(sys.executable).with_name("jev-engage-mcp")
    if not script.exists():
        pytest.skip("jev-engage-mcp is not installed in this environment")
    env = {**os.environ, "JEV_ENGAGEMENT_ARTIFACTS": str(tmp_path / "runs")}
    log = tmp_path / "stderr.log"
    with open(log, "wb") as stderr:
        server = subprocess.Popen([str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, env=env)
    try:
        hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
        server.stdin.write((json.dumps(hello) + "\n").encode())
        server.stdin.flush()
        reply = server.stdout.readline()
        assert json.loads(reply)["result"]["serverInfo"]["name"] == "jev-engagement", (reply, log.read_text())
        server.send_signal(signal.SIGTERM)
        try:
            code = server.wait(10)
        except subprocess.TimeoutExpired:
            code = "still running 10 s after SIGTERM"
        assert code == 0, (code, log.read_text()[-2000:])
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()
        for stream in (server.stdin, server.stdout):
            stream.close()


def _processes() -> dict[int, tuple[int, int, str]]:
    """pid -> (parent pid, process group, state) from /proc."""
    found = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                fields = Path(f"/proc/{entry}/stat").read_text().rpartition(")")[2].split()
            except OSError:
                continue
            found[int(entry)] = (int(fields[1]), int(fields[2]), fields[0])
    return found


def test_the_hosts_stop_sequence_kills_the_browsers_and_marks_the_runs(tmp_path, shop_server):
    """Claude Code stops a stdio server with SIGINT, SIGTERM 100 ms later and SIGKILL 400 ms after that (to the pid
    only). With an audit running and a journey open, the server must end inside that window, leave no Chromium
    behind (the browsers run in their own sessions) and leave both runs finished, not "running" for good."""
    script = Path(sys.executable).with_name("jev-engage-mcp")
    if not script.exists() or find_chromium() is None or not os.path.isdir("/proc/self"):
        pytest.skip("needs the installed jev-engage-mcp, Chromium and /proc")
    runs = tmp_path / "runs"
    env = {**os.environ, "JEV_ENGAGEMENT_ARTIFACTS": str(runs), "JEV_ENGAGEMENT_CACHE": str(tmp_path / "cache"),
           "JEV_CHROME_ARGS": "--proxy-server=http://127.0.0.1:9"}
    log = open(tmp_path / "stderr.log", "wb")
    server = subprocess.Popen([str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, env=env)

    def rpc(number, method, params):
        server.stdin.write((json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": params})
                            + "\n").encode())
        server.stdin.flush()
        return json.loads(server.stdout.readline())

    def tool(number, name, **arguments):
        reply = rpc(number, "tools/call", {"name": name, "arguments": arguments})
        assert not reply["result"].get("isError"), reply
        return json.loads(reply["result"]["content"][0]["text"])

    groups = set()
    try:
        rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "test", "version": "1"}})
        server.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        server.stdin.flush()
        url = shop_server.url("shop/index.html")
        journey = tool(2, "run_journey", url=url, goal="Metti un prodotto nel carrello", oracle="cart_not_empty",
                       profile="desktop")["run_id"]
        audit = tool(3, "audit_shop", url=url, profiles=["desktop"])["run_id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            browsers = [pid for pid, (parent, _, _) in _processes().items() if parent == server.pid]
            if len(browsers) >= 2:  # the journey's Chromium and the audit's
                break
            time.sleep(0.1)
        groups = {_processes()[pid][1] for pid in browsers}
        assert len(groups) >= 2, browsers
        started = time.monotonic()
        server.send_signal(signal.SIGINT)
        try:
            server.wait(0.1)
        except subprocess.TimeoutExpired:
            server.send_signal(signal.SIGTERM)
            server.wait(0.4)  # TimeoutExpired here: the host would have sent SIGKILL
        assert server.returncode == 0 and time.monotonic() - started < 0.5
        deadline = time.monotonic() + 3
        while (left := [pid for pid, (_, group, state) in _processes().items() if group in groups and state != "Z"]) \
                and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not left, left
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()
        for group in groups:  # never leave a browser behind, even when the test failed
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for stream in (server.stdin, server.stdout):
            stream.close()
        log.close()
    store = RunStore(runs)
    fresh = EngagementService(store)  # what a restarted server does with a run the handler had no time to mark
    fresh.recover_orphans()
    run = store.load(audit)
    assert run["status"] == "failed", run  # the audit thread may still record the browser it lost, after the mark
    assert any(e.startswith("interrupted:") for e in run["errors"]), run["errors"]
    assert f"concurrent run {journey} in this process: timings may be skewed" in run["warnings"]
    run = store.load(journey)
    assert run["status"] in ("partial", "failed") and run["journey"]["status"] == "abandoned" and run["finished_at"]
    assert not fresh.wait_run(audit, 5)["timed_out"] and not list(runs.glob("*/*/owner.json"))
