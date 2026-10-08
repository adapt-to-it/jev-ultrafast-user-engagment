"""The MCP server through the real protocol: tool listing and annotations, the audit -> judgments -> score -> report
flow (a stand-in audit that stores the golden run's pages, so no browser), a real audit and a host-driven journey on
the fixture shop (headless Chromium; skipped without it), error handling, idle journeys and the stdio entry point.

Clients talk JSON-RPC to the server: in-process over memory streams (mode="legacy") or over stdio to the installed
jev-engage-mcp script. Nothing calls a paid API or the public internet; judges are scripted, and Jev (TypeSafe) is a
stand-in for jev_ultrafast.model.post_json that checks every request body and answers like systemone. The model keys
of the developer's environment are removed for every test (with them, policy "auto" would pay for real journeys).
"""

import copy
import importlib.metadata
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters

from jev_ultrafast import model as typesafe
from jev_ultrafast.engagement import audit as audit_module
from jev_ultrafast.engagement import journey as journey_module
from jev_ultrafast.engagement import judges, judgments, mcp_server
from jev_ultrafast.engagement.chrome import find_chromium
from jev_ultrafast.engagement.collectors import PageCollector
from jev_ultrafast.engagement.mcp_server import RESULT_CHARS, build_server
from jev_ultrafast.engagement.service import TASK_PAGE, TASK_PAGE_CHARS, EngagementService, _failed, _JobStore
from jev_ultrafast.engagement.store import RunStore, iso_now
from jev_ultrafast.engagement.transport import DirectTransport

ROOT = Path(__file__).resolve().parents[1]
RUNS = Path(__file__).with_name("fixtures") / "runs"
GOLDEN = json.loads((RUNS / "audit_complete.json").read_text(encoding="utf-8"))
SHOP = GOLDEN["site"]["start_url"]
TOOLS = {"audit_shop", "wait_run", "get_run", "run_journey", "journey_act", "journey_finish", "judge_with_jev",
         "get_judgment_tasks", "submit_judgments", "finalize_judgments", "score_run", "get_report", "list_runs"}
READ_ONLY = {"wait_run", "get_run", "get_report", "list_runs"}  # get_judgment_tasks creates the tasks once
PLUGIN_TOOL = "mcp__plugin_jev-engagement_engagement__"  # mcp__plugin_<plugin>_<server>__<tool>
OPEN_WORLD = {"audit_shop", "run_journey", "journey_act", "journey_finish", "judge_with_jev"}  # judge: asks TypeSafe
KEYS = ("TYPESAFE_API_KEY", "TYPESAFE_MODEL", "TEXT_MODEL_API_KEY", "TEXT_MODEL", "TEXT_MODEL_BASE_URL",
        "JEV_ENGAGEMENT_ENV", "JEV_JUDGE_MIN_P")
SYSTEMONE = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.13.0"


@pytest.fixture(autouse=True)
def no_model_keys(monkeypatch):
    """No test inherits a real key: policy "auto" would turn host journeys into paid TypeSafe runs, the audit would
    ask Jev, and the stdio servers (env copied from os.environ) would load them too."""
    for name in KEYS:  # setenv first, so teardown restores the original even after a test's env file set it
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


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
    # a page is never saved to a file by Claude Code (the judges cannot read files): see the worst-case page test
    assert by_name["get_judgment_tasks"].meta["anthropic/maxResultSizeChars"] == RESULT_CHARS
    assert by_name["audit_shop"].input_schema["properties"]["profiles"]["anyOf"][0]["minItems"] == 1
    assert "never a selector" in by_name["journey_act"].input_schema["properties"]["target"]["description"].lower()
    policy = by_name["run_journey"].input_schema["properties"]["policy"]
    assert policy["default"] == "auto" and policy["enum"] == ["auto", "host", "typesafe"]
    assert by_name["judge_with_jev"].input_schema["required"] == ["run_id"]
    for phrase in ("never measured engagement", "predicted friction", "risk signals", "never place orders",
                   "offered element index"):
        assert phrase in instructions


def test_the_plugin_runs_its_own_server_and_names_only_tools_it_lists(service):
    """The plugin's server lives in plugin.json with the exact placeholders (Claude Code substitutes only
    ${CLAUDE_PLUGIN_ROOT} and ${CLAUDE_PLUGIN_DATA}; a ${VAR:-default} form goes through plain environment expansion
    and always takes the default). No project .mcp.json at the root: it would be read as the plugin's server file
    and as a second, project-scoped server. Every tool the skills and the judge agent name is one the server lists."""
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    server = manifest["mcpServers"]["engagement"]
    assert server["command"] == "uv"
    assert server["args"] == ["run", "--frozen", "--no-dev", "--project", "${CLAUDE_PLUGIN_ROOT}", "jev-engage-mcp"]
    assert server["env"] == {"BH_TAB_MARKER": "0", "UV_PROJECT_ENVIRONMENT": "${CLAUDE_PLUGIN_DATA}/.venv",
                             "JEV_ENGAGEMENT_ARTIFACTS": "${CLAUDE_PLUGIN_DATA}/engagement",
                             "JEV_ENGAGEMENT_ENV": "${CLAUDE_PLUGIN_DATA}/.env"}  # keys: the server reads this file
    assert ":-" not in json.dumps(manifest) and not (ROOT / ".mcp.json").exists()
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert [(p["name"], p["source"]) for p in marketplace["plugins"]] == [(manifest["name"], "./")]

    async def scenario(client):
        return {t.name for t in (await client.list_tools()).tools}
    listed = session(build_server(service()), scenario)
    named = {}
    for path in [*ROOT.glob("skills/**/*.md"), *ROOT.glob("agents/**/*.md")]:
        text = path.read_text(encoding="utf-8")
        assert "mcp__engagement__" not in text, path  # the project-scoped name is gone with .mcp.json
        named[path.relative_to(ROOT).as_posix()] = set(re.findall(r"mcp__plugin_[\w-]+", text))
    assert named["agents/engagement-judge.md"] == {PLUGIN_TOOL + "get_judgment_tasks"}
    assert named["skills/shop-readiness/SKILL.md"] == {PLUGIN_TOOL + tool for tool in TOOLS}
    for path, names in named.items():
        assert all(n.startswith(PLUGIN_TOOL) and n.removeprefix(PLUGIN_TOOL) in listed for n in names), path
    front = (ROOT / "skills" / "shop-readiness" / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    allowed = re.findall(r"^  - (\S+)$", front.split("allowed-tools:")[1], re.M)
    assert set(allowed) == {PLUGIN_TOOL + tool for tool in listed}


def test_the_plugin_server_and_package_share_one_version(service):
    """plugin.json's version (what `claude plugin list` and bug reports show, and what decides whether `claude plugin
    update` installs anything) equals pyproject's; the server reports the installed package's version."""
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    project = re.search(r'^version = "([^"]+)"$', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)[1]
    assert manifest["version"] == project

    async def scenario(client):
        return client.server_info.version
    assert session(build_server(service()), scenario) == importlib.metadata.version("jev-ultrafast") == project


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
    with pytest.raises(ValueError, match="the run being scored"):
        svc.score_run(run_id, [run_id])
    results, errors = [], []

    def score():
        try:
            results.append(svc.score_run(run_id))
        except Exception as exc:  # report.html is rewritten by each call: they must not overlap
            errors.append(exc)
    threads = [threading.Thread(target=score) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert not errors and len(results) == 6
    scored = results[-1]
    assert "senza giudizi LLM" in scored["scope"] and Path(scored["report"]["report_html"]).is_file()
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


def test_a_page_of_non_latin_snippets_holds_fewer_characters(service, monkeypatch):
    """Cyrillic or CJK text takes more tokens per character than Latin: such a character counts three toward
    TASK_PAGE_CHARS, so a page of them stays as far below the tool output limit as a Latin page."""
    monkeypatch.setattr(audit_module, "audit_shop", stand_in_audit())
    svc = service()
    run_id = svc.audit_shop(SHOP, wait=True)["run_id"]
    svc.get_judgment_tasks(run_id, brief=True)

    def page_of(words):
        def fill(run):
            for i, task in enumerate(run["judgments"]["tasks"]):
                task["snippets"] = [{"snippet_id": f"s{i}-{k}", "kind": "body", "text": (words * 200)[:600]}
                                    for k in range(8)]
        svc.store.update(run_id, fill)
        return svc.get_judgment_tasks(run_id, 0)
    latin, cjk = page_of("parole "), page_of("购物车价格")
    assert len(latin["tasks"]) >= 3 * len(cjk["tasks"]) >= 3
    assert sum(len(s["text"]) for t in cjk["tasks"] for s in t["snippets"]) <= TASK_PAGE_CHARS // 3
    assert svc.get_judgment_tasks(run_id, 0, brief=True)["next_cursor"] == cjk["next_cursor"]


def test_a_page_of_the_longest_tasks_citing_every_rubric_stays_below_the_hosts_file_threshold(service, monkeypatch):
    """Claude Code saves a tool result longer than 50,000 characters to a file unless the tool declares a higher
    anthropic/maxResultSizeChars; a judge cannot read such a file. Worst case: task text up to TASK_PAGE_CHARS (8
    snippets of 600 characters, full of characters JSON escapes) plus every rubric's question and labels on one page."""
    monkeypatch.setattr(audit_module, "audit_shop", stand_in_audit())
    svc = service()
    run_id = svc.audit_shop(SHOP, wait=True)["run_id"]
    svc.get_judgment_tasks(run_id, brief=True)
    rubric_ids = sorted(judgments.load_rubrics())

    def inflate(run):
        tasks = run["judgments"]["tasks"]
        while len(tasks) < 2 * TASK_PAGE:
            tasks.append({**copy.deepcopy(tasks[-1]), "task_id": f"{tasks[-1]['task_id']}-{len(tasks)}"})
        for i, task in enumerate(tasks):
            text = (f'Testo "lungo" {i}: ' + 'parole "della" pagina \\ ' * 40)[:600]
            task["rubric_id"] = rubric_ids[i % len(rubric_ids)]  # the first tasks cite every rubric, one snippet each
            task["snippets"] = [{"snippet_id": f"s{i}-{k}", "kind": "subscription_terms", "text": text}
                                for k in range(1 if i < len(rubric_ids) else 8)]
    svc.store.update(run_id, inflate)

    async def scenario(client):
        return (await client.call_tool("get_judgment_tasks", {"run_id": run_id})).content[0].text
    text = session(build_server(svc), scenario)
    page = json.loads(text)
    assert set(page["rubrics"]) == set(rubric_ids) and page["next_cursor"] < TASK_PAGE  # cut by size
    assert len(text) < 50_000 < RESULT_CHARS, len(text)


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
        self.store.update(self.run_id, lambda run: run.update(status="running", journey={  # the landing page is read
            "goal": goal, "oracle": oracle, "status": "running", "verification": None}, pages=[{"page_id": "start"}]))
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


def test_journey_finish_shows_every_check_of_the_verdict(service, monkeypatch):
    """The host reads why a journey was not assessable: navigation_error stays {url, error} and a cart's item_list
    keeps its items, however many checks the oracle recorded."""
    checks = {"oracle": "cart_contains_item_under_price", "cart_found": True, "cart_source": "visited",
              "cart_url": "https://shop.example/cart", "page_type": "cart", "items": 1, "price_reading": "unit",
              "subtotal": 39.9, "total": 44.8, "cart_empty": False, "max_price": 50.0, "matching": 1,
              "item_list": [{"title": "Scarpa da corsa", "qty": 1, "price": 39.9}], "oracle_passed": False,
              "navigation_error": {"url": "https://shop.example/cart", "error": "net::ERR_CONNECTION_RESET"},
              "not_assessable": "navigation_error"}

    class Unloaded(FakeRunner):
        def finish(self, status=None):
            result = super().finish(status)
            verification = {"passed": None, "checks": copy.deepcopy(checks)}
            self.store.update(self.run_id, lambda run: run["journey"].update(verification=verification))
            return {**result, "verification": verification}
    monkeypatch.setattr(journey_module, "JourneyRunner", Unloaded)
    svc = service()
    run_id = svc.run_journey(SHOP, "Trova scarpe", "cart_contains_item_under_price", {"max_price": 50})["run_id"]
    for summary in (svc.journey_finish(run_id, "blocked"), svc.journey_finish(run_id)):  # live, then read back
        assert summary["verification"] == {"passed": None, "checks": checks}


class FailingFinish(FakeRunner):
    """A runner that stops by itself (DONE) and whose finish() raises (an oracle, friction or save error)."""

    def run_auto(self):
        self.status = "done"

    def finish(self, status=None):
        self.calls.append(("finish", status))
        raise KeyError("boom")


def test_a_journey_whose_finish_fails_records_why_whatever_closed_it(service, monkeypatch, caplog):
    """close() has already left the run partial: the error still reaches the run and the log, with a warning that
    nothing was verified, and a journey that stopped by itself is not called idle."""
    monkeypatch.setattr(journey_module, "JourneyRunner", FailingFinish)
    now = [0.0]
    svc = service(clock=lambda: now[0], idle_timeout_s=600, reap_interval_s=3600)
    auto = svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty", policy="typesafe", wait=True)["run_id"]
    host = svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty")["run_id"]
    with pytest.raises(KeyError, match="boom"):
        svc.journey_finish(host, "done")
    reaped = svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty")["run_id"]
    svc.journey_act(reaped, "DONE", observation_id=1)
    now[0] = 700
    assert svc.reap() == [reaped]
    for run_id in (auto, host, reaped):
        run = svc.store.load(run_id)
        assert run["status"] == "partial" and run["errors"] == ["journey finish: KeyError: 'boom'"], run_id
        assert "closed without verification: journey finish failed" in run["warnings"]
        assert not any(w.startswith("abandoned") for w in run["warnings"])
    assert svc._jobs[auto].progress == ["journey finish: KeyError: 'boom'"]
    assert caplog.text.count("finishing journey") == 3


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
    """An interrupted audit stays failed, is released (owner.json gone) by the time its job is done, and is never
    judged or scored: whatever its thread still wrote came from a run that did not finish."""
    gate = threading.Event()
    monkeypatch.setattr(audit_module, "audit_shop", stand_in_audit(gate))
    disown = EngagementService._disown
    monkeypatch.setattr(EngagementService, "_disown", lambda self, run_id: (time.sleep(0.05), disown(self, run_id)))
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
    assert not (svc.store.path(run_id) / "owner.json").exists()  # released before done is set, however slow
    with pytest.raises(RuntimeError, match="shutting down"):
        svc.audit_shop(SHOP)
    for refused in (lambda: svc.get_judgment_tasks(run_id), lambda: svc.score_run(run_id),
                    lambda: svc.finalize_judgments(run_id)):
        with pytest.raises(ValueError, match=r"was interrupted \(interrupted: the server shut down .*run the audit "
                                             r"again"):
            refused()
    assert svc.store.load(run_id)["scores"] is None and not (svc.store.path(run_id) / "report.json").exists()


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
    assert time.monotonic() - started < 2  # two run.json writes, no join, no lock wait, no browser call
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


def test_sealed_runs_keep_their_mark_whatever_their_threads_write_after_the_kill(service, monkeypatch):
    """The signal path: seal() (no I/O) before the browsers are killed, abort() after. Threads that react to the kill
    at once write their lost browser and their own end before abort() runs: the audit still ends failed and
    interrupted (not partial from a half-killed funnel), the typesafe journey abandoned (its late verdict dropped).
    The marks are written jobs first, then the open journeys."""
    killed = threading.Event()

    def audit_shop(settings, *, store, transport_factory=None, progress=None):  # audit.py once its browser is gone
        run_id = store.new_run("audit", settings.url, {})
        store.update(run_id, lambda run: run.update(status="running", pages=copy.deepcopy(GOLDEN["pages"][:3])))
        assert killed.wait(20)
        store.update(run_id, lambda run: run["errors"].append("desktop: ConnectionError: CDP connection is closed"))
        store.update(run_id, lambda run: run.update(status="partial", finished_at=iso_now()))
        return run_id

    class AutoRunner(FakeRunner):
        def run_auto(self):
            assert killed.wait(20)
            raise ConnectionError("CDP connection is closed")
    monkeypatch.setattr(audit_module, "audit_shop", audit_shop)
    monkeypatch.setattr(journey_module, "JourneyRunner", AutoRunner)
    svc = service()
    journey = svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty")["run_id"]
    auto = svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty", policy="typesafe")["run_id"]
    audit = svc.audit_shop(SHOP)["run_id"]
    reason = "the server was stopped (signal 2)"
    svc.seal(reason)
    assert svc.store.load(audit)["status"] == svc.store.load(journey)["status"] == "running"  # no I/O yet
    with pytest.raises(RuntimeError, match="shutting down"):
        svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty")
    killed.set()  # _kill_browsers()
    assert svc._jobs[audit].done.wait(10) and svc._jobs[auto].done.wait(10)  # both ended before abort()
    marked = []
    mark = svc._mark
    monkeypatch.setattr(svc, "_mark", lambda run_id, fn: (marked.append(run_id), mark(run_id, fn)))
    svc.abort(reason)
    assert set(marked[:2]) == {audit, auto} and marked[2:] == [journey]
    run = svc.store.load(audit)
    assert run["status"] == "failed" and run["pages"]
    assert set(run["errors"]) == {f"interrupted: {reason} while the run was in progress",
                                  "desktop: ConnectionError: CDP connection is closed"}
    for run_id in (auto, journey):
        run = svc.store.load(run_id)
        assert run["status"] == "partial" and run["journey"]["status"] == "abandoned", run_id
        assert run["journey"]["verification"] is None and run["observations"] == []
        assert f"abandoned: {reason}; closed without verification" in run["warnings"]
    dropped = "verification discarded: the oracle ran after the journey was interrupted"
    assert dropped in svc.store.load(auto)["warnings"]


def test_a_sealed_job_store_marks_every_write_of_its_run(tmp_path):
    """run.json has two writers, RunStore.update and RunStore.save: once sealed, both keep the mark on the job's run
    and leave every other run alone."""
    store = RunStore(tmp_path)
    run_id, other = store.new_run("audit", SHOP, {}), store.new_run("audit", SHOP, {})
    job = _JobStore(store, run_id)
    job.seal(_failed("interrupted: test"))
    for target in (run_id, other):
        run = store.load(target)
        run.update(status="complete", finished_at=iso_now())
        job.save(target, run)
    assert store.load(run_id)["status"] == "failed" and store.load(run_id)["errors"] == ["interrupted: test"]
    assert store.load(other)["status"] == "complete"
    job.update(run_id, lambda run: run.update(status="partial"))
    assert store.load(run_id)["status"] == "failed"


def test_a_run_that_never_started_is_not_waited_for(service):
    """A process that died between creating a run and recording itself as its owner leaves a "created" run without
    owner.json: once it is clearly stale it is marked failed; a fresh one is left alone."""
    svc = service()
    stale = svc.store.new_run("audit", SHOP, {}, now=datetime.now(UTC) - timedelta(minutes=5))
    fresh = svc.store.new_run("audit", SHOP, {})
    started = time.monotonic()
    waited = svc.wait_run(stale, 30)
    assert time.monotonic() - started < 5 and not waited["timed_out"] and waited["status"] == "failed"
    assert waited["errors"][0].startswith("interrupted: the run never started")
    assert svc.get_run(fresh)["status"] == "created"


def test_runs_of_a_process_that_died_are_marked_failed_not_waited_for(service, monkeypatch):
    if os.name != "posix":
        pytest.skip("owner liveness is only checked on POSIX")
    monkeypatch.setattr(journey_module, "JourneyRunner", FakeRunner)
    dead = int(subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True,
                              text=True, check=True).stdout)
    store = RunStore(service().store.root)

    def orphan(kind, owner, pages=()):
        run_id = store.new_run(kind, SHOP, {})
        store.update(run_id, lambda run: run.update(status="running", pages=list(pages), journey={
            "goal": "g", "oracle": "cart_not_empty", "status": "running", "verification": None}
            if kind == "journey" else None))
        store.write_json(run_id, "owner.json", owner)
        return run_id
    here = socket.gethostname()
    audit = orphan("audit", {"owner_pid": dead, "owner_start": None, "host": here})
    journey = orphan("journey", {"owner_pid": dead, "owner_start": None, "host": here})  # its start page never read
    landed = orphan("journey", {"owner_pid": dead, "owner_start": None, "host": here}, pages=[{"page_id": "start"}])
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
    # the same terminal state as a signal leaves (abandoned; the run partial, failed when it holds no page)
    assert svc.store.load(journey)["status"] == "failed"
    with pytest.raises(ValueError, match="still running"):
        svc.score_run(alive)
    assert sorted(svc.recover_orphans()) == sorted([reused, landed])  # a live pid with another start time: reused
    run = svc.store.load(landed)
    assert run["status"] == "partial" and run["journey"]["status"] == "abandoned" and run["finished_at"]
    assert run["errors"] == [f"interrupted: owning process {dead} is gone"]
    assert f"abandoned: owning process {dead} is gone; closed without verification" in run["warnings"]
    assert svc.get_run(alive)["status"] == svc.get_run(elsewhere)["status"] == "running"
    assert not any((svc.store.path(r) / "owner.json").exists() for r in (audit, journey, reused, landed))
    with pytest.raises(ValueError, match="was interrupted"):
        svc.score_run(audit, [landed])  # an interrupted audit is never scored
    finished = store.new_run("audit", SHOP, {})
    store.update(finished, lambda run: run.update(status="complete", finished_at=iso_now()))
    scored = svc.score_run(finished, [landed])
    assert any("ended abandoned without verification" in w for w in scored["warnings"])


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


@pytest.mark.parametrize("name", ["SIGTERM", "SIGHUP"])
def test_sigterm_ends_the_stdio_server_at_once(tmp_path, name):
    """The stdio reader blocks on stdin until the host closes it; SIGTERM (the host) or SIGHUP (the terminal running
    the host closed) still ends the server, cleaning up, with status 0 (the default action of either would kill it)."""
    script = Path(sys.executable).with_name("jev-engage-mcp")
    if not script.exists() or not hasattr(signal, name):
        pytest.skip(f"needs the installed jev-engage-mcp and {name}")
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
        server.send_signal(getattr(signal, name))
        try:
            code = server.wait(10)
        except subprocess.TimeoutExpired:
            code = f"still running 10 s after {name}"
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


@pytest.mark.parametrize("names", [("SIGINT", "SIGTERM"), ("SIGHUP",)], ids=["host", "hangup"])
def test_the_hosts_stop_sequence_kills_the_browsers_and_marks_the_runs(tmp_path, shop_server, names):
    """Claude Code stops a stdio server with SIGINT, SIGTERM 100 ms later and SIGKILL 400 ms after that (to the pid
    only); closing the terminal that runs Claude Code sends SIGHUP to its process group, the server included. With an
    audit running and a journey open, the server must end inside that window, leave no Chromium behind (the browsers
    run in their own sessions) and leave both runs finished, not "running" for good."""
    script = Path(sys.executable).with_name("jev-engage-mcp")
    if not script.exists() or find_chromium() is None or not os.path.isdir("/proc/self") \
            or not all(hasattr(signal, name) for name in names):
        pytest.skip("needs the installed jev-engage-mcp, Chromium, /proc and " + ", ".join(names))
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
        for i, name in enumerate(names):  # the next signal 100 ms later, SIGKILL 500 ms after the first
            server.send_signal(getattr(signal, name))
            last = i == len(names) - 1
            try:
                server.wait(0.5 - (time.monotonic() - started) if last else 0.1)
                break
            except subprocess.TimeoutExpired:
                if last:
                    raise  # the host would have sent SIGKILL
        assert server.returncode == 0 and time.monotonic() - started < 0.5, server.returncode
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
    run = store.load(journey)  # the signal's mark or, when it had no time, the replay at the next start: one state
    assert run["status"] == "partial" and run["journey"]["status"] == "abandoned" and run["finished_at"]
    assert not fresh.wait_run(audit, 5)["timed_out"] and not list(runs.glob("*/*/owner.json"))


def test_get_run_page_rows_show_no_weight_for_a_page_read_in_place():
    """report._pages' rule (WS7): the cart an oracle read where the journey ended recorded no request, so its weight
    was never measured: kb is None (the CLI prints "n/d KB"), never 0."""
    read_in_place = {"page_id": "mobile-verify-cart", "network": {"bytes_transfer": 0, "requests": 0}}
    loaded = {"page_id": "mobile-home-1", "network": {"bytes_transfer": 36_400, "requests": 4}}
    assert EngagementService._page_row(read_in_place)["kb"] is None
    assert EngagementService._page_row(loaded)["kb"] == 36
    assert EngagementService._page_row({"page_id": "x"})["kb"] is None


# ---------------------------------------------------------------- review round 0: judge_with merges, finalize default


def scripted_verdicts(judge, tasks):
    return [{"task_id": t["task_id"], "label": sorted(t["labels"])[0], "confidence": 0.8,
             "rationale": "Citazione sufficiente.",
             "evidence": [{"snippet_id": t["snippets"][0]["snippet_id"], "quote": t["snippets"][0]["text"][:30]}]}
            for t in tasks]


def test_judge_with_keeps_what_another_writer_stored_while_its_judges_ran(service, monkeypatch):
    """The judges run outside the run's lock (claude -p takes minutes); a host's submit_judgments on the same
    artifacts meanwhile is merged with, never overwritten."""
    from jev_ultrafast.engagement import judges

    monkeypatch.setattr(audit_module, "audit_shop", stand_in_audit())
    svc, host = service(), service()
    run_id = svc.audit_shop(SHOP, wait=True)["run_id"]
    page = host.get_judgment_tasks(run_id)
    hosted = []

    def judge(self, tasks):
        if not hosted:  # the host submits while the CLI judge is still working
            hosted.append(host.submit_judgments(run_id, "host-1", "host-model", verdicts_for(page, "j1")[:1]))
        return scripted_verdicts(self, tasks)
    monkeypatch.setattr(judges.ClaudeCliJudge, "judge", judge)
    result = svc.judge_with(run_id, "cli", 1, model="claude-test", batch_size=4)
    assert hosted[0]["accepted"] == 1 and result["rejected_total"] == 0
    state = svc.store.load(run_id)["judgments"]
    assert [v["task_id"] for v in state["verdicts"] if v["judge_id"] == "host-1"] == [page["tasks"][0]["task_id"]]
    assert {v["task_id"] for v in state["verdicts"] if v["judge_id"] == "j1"} == {t["task_id"] for t in state["tasks"]}
    assert result["accepted"] == len(state["tasks"]) and len(state["final"]) == len(state["tasks"])


def test_finalize_by_default_keeps_each_tasks_own_sample_count(service, monkeypatch):
    """Tasks created by `judge --samples 1` need one verdict: finalize_judgments() without a count finalizes what the
    judge left pending once it has its one verdict, and never asks for 3."""
    from jev_ultrafast.engagement import judges

    monkeypatch.setattr(audit_module, "audit_shop", stand_in_audit())
    svc = service()
    run_id = svc.audit_shop(SHOP, wait=True)["run_id"]
    calls = []

    def flaky(self, tasks):
        calls.append(len(tasks))
        if len(calls) > 1:
            raise judges.JudgeError("transient: rate limited")
        return scripted_verdicts(self, tasks)
    monkeypatch.setattr(judges.ClaudeCliJudge, "judge", flaky)
    result = svc.judge_with(run_id, "cli", 1, model="claude-test", batch_size=4)
    assert result["errors"] and result["finalize"]["pending"] > 0
    state = svc.store.load(run_id)["judgments"]
    assert {t["samples_required"] for t in state["tasks"]} == {1}
    pending = [t for t in state["tasks"] if t["task_id"] not in {f["task_id"] for f in state["final"]}]
    page = svc.get_judgment_tasks(run_id, limit=TASK_PAGE)
    late = [t for t in page["tasks"] if t["task_id"] == pending[0]["task_id"]]
    assert svc.submit_judgments(run_id, "host-1", "host-model", verdicts_for({**page, "tasks": late}, "j1"))[
        "accepted"] == 1

    async def scenario(client):
        return await call(client, "finalize_judgments", run_id=run_id)
    final = session(build_server(svc), scenario)
    assert final["final"] == len(state["final"]) + 1 and final["pending_total"] == len(pending) - 1
    assert svc.finalize_judgments(run_id)["pending_total"] == len(pending) - 1


# ---------------------------------------------------------------- Jev: pilot by default, judge, keys on the server


def choice(ids, selected, p=0.9):
    """A schema-valid systemone choice answer (model.validate_choice accepts it)."""
    ids = list(ids)
    rest = (1 - p) / (len(ids) - 1)
    return {"type": "choice", "choice": selected, "probabilities": {i: p if i == selected else rest for i in ids},
            "confidence": (p - 1 / len(ids)) / (1 - 1 / len(ids))}


class JevJudgeStandIn:
    """jev_ultrafast.model.post_json for the Jev judge: checks each request like systemone would (one page per
    request, choice heads only, at most 255 options, label heads over the rubric's labels, evidence heads over the
    offered snippet ids plus none, no URL or run id in the state) and answers with the first label. script: {task_id:
    {"p", "rev", "evidence"}} for the answers that must escalate."""

    def __init__(self, tasks: dict, script: dict, run_id: str):
        self.tasks, self.script, self.run_id, self.pages = tasks, script, run_id, []

    def __call__(self, url, key, body):
        assert url == SYSTEMONE and key == "test-key" and set(body) == {"model", "state", "questions"}
        assert "url" not in body["state"]["page"] and self.run_id not in json.dumps(body)
        task_ids = {":".join(qid.split(":")[1:3]) for qid in body["questions"]}
        assert len({self.tasks[t]["page_id"] for t in task_ids}) == 1  # one request per page
        self.pages.append(self.tasks[next(iter(task_ids))]["page_id"])
        answers = {}
        for qid, question in body["questions"].items():
            kind, task_id = qid.split(":")[0], ":".join(qid.split(":")[1:3])
            task, plan = self.tasks[task_id], self.script.get(task_id, {})
            assert question["type"] == "choice" and 2 <= len(question["criteria"]) <= 255
            labels = list(task["labels"])
            if kind == "label":
                assert list(question["criteria"]) == labels
                answers[qid] = choice(labels, labels[0], plan.get("p", 0.9))
            elif kind == "label_rev":
                assert list(question["criteria"]) == labels[::-1]
                answers[qid] = choice(labels, plan.get("rev", labels[0]), plan.get("p", 0.9))
            else:
                snippets = [s["snippet_id"] for s in task["snippets"]]
                assert kind == "evidence" and list(question["criteria"])[:-1] == snippets
                answers[qid] = choice(question["criteria"], plan.get("evidence", snippets[0]), 0.8)
        return {"model": JEV_MODEL, "answers": answers, "usage": {"input_tokens": 1200, "output_tokens": 0}}


ESCALATE = {"trick_questions:mobile-cart-1": {"p": 0.55}, "trick_questions:mobile-checkout_entry-1": {"p": 0.55},
            "social_proof:mobile-pdp-1": {"rev": "basic"},
            "hidden_subscription:mobile-checkout_entry-1": {"evidence": "none"}}


def test_jev_judges_its_rubrics_and_claude_judges_what_it_leaves(service, monkeypatch):
    """judge_with_jev through the protocol: without a key nothing changes; with one, one request per page, the
    accepted tasks are final with one sample, and the Claude rubrics plus Jev's escalations stay open for the host's
    judges, who never see Jev's label. The judge's requests are counted in the run."""
    monkeypatch.setattr(audit_module, "audit_shop", stand_in_audit())
    svc = service()
    run_id = svc.audit_shop(SHOP, wait=True)["run_id"]
    server = build_server(svc)

    async def unavailable(client):
        return await call(client, "judge_with_jev", run_id=run_id), await call(client, "get_run", run_id=run_id)
    result, run = session(server, unavailable)
    assert result["available"] is False and result["typesafe_key"] is False and result["requests"] == 0
    assert run["judgments"] == {"prepared": False} and run["server"] == {"typesafe_key": False, "text_helper": None}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    rubrics = judgments.load_rubrics()
    tasks = {t["task_id"]: t for t in judgments.make_tasks(svc.store.load(run_id))}
    jev_tasks = {t for t in tasks if judgments.routing(tasks[t], rubrics) == "jev"}
    stand_in = JevJudgeStandIn(tasks, ESCALATE, run_id)
    monkeypatch.setattr(typesafe, "post_json", stand_in)

    async def judged(client):
        first = await call(client, "judge_with_jev", run_id=run_id)
        again = await call(client, "judge_with_jev", run_id=run_id)  # nothing left for Jev: no request
        pages, cursor = [], 0
        while cursor is not None:
            page = await call(client, "get_judgment_tasks", run_id=run_id, cursor=cursor)
            pages.append(page)
            cursor = page["next_cursor"]
        return first, again, pages, await call(client, "get_run", run_id=run_id)
    first, again, pages, run = session(server, judged)
    jev_pages = {tasks[t]["page_id"] for t in jev_tasks}
    assert first["available"] and first["requests"] == len(jev_pages) == len(stand_in.pages) == 4
    assert sorted(stand_in.pages) == sorted(jev_pages) and first["model"] == JEV_MODEL
    assert first["accepted"] == len(jev_tasks) - len(ESCALATE) == 7 and first["input_tokens"] == 4 * 1200
    assert first["escalated"] == {"low_probability": 2, "position_flip": 1, "no_evidence": 1}
    readings = {r["task_id"]: r for r in first["per_task"]}
    assert set(readings) == jev_tasks and readings["social_proof:mobile-pdp-1"]["escalated"] == "position_flip"
    assert readings["returns_clarity:mobile-pdp-1"] == {
        "task_id": "returns_clarity:mobile-pdp-1", "rubric_id": "returns_clarity", "label": "clear",
        "probability": 0.9, "evidence": [tasks["returns_clarity:mobile-pdp-1"]["snippets"][0]["snippet_id"]],
        "final": True, "escalated": None}
    open_ids = {t for t in tasks if t not in jev_tasks} | set(ESCALATE)
    assert first["open_tasks"] == len(open_ids) == 9
    assert again["requests"] == 0 and again["accepted"] == 0 and len(stand_in.pages) == 4
    items = {t["task_id"]: t for page in pages for t in page["tasks"]}
    for task_id, item in items.items():
        assert item["judge"] == judgments.routing(tasks[task_id], rubrics)
        assert item["final"] is (task_id not in open_ids), task_id
        if task_id in ESCALATE:  # why, never Jev's label: the host's judges stay independent
            assert item["escalation"] == {"from": "jev", "reason": readings[task_id]["escalated"]}
        else:
            assert "escalation" not in item
    assert pages[0]["open_tasks"] == 9 and run["judgments"]["escalated"] == 4
    assert run["model_calls"]["judge_jev"] == {"requests": 4, "latency_ms": first["latency_ms"],
                                               "input_tokens": 4800, "model": JEV_MODEL}
    assert run["server"] == {"typesafe_key": True, "text_helper": None}

    for judge in ("j1", "j2", "j3"):
        for page in pages:
            open_page = {**page, "tasks": [t for t in page["tasks"] if not t["final"]]}
            result = svc.submit_judgments(run_id, judge, "claude-sonnet-test", verdicts_for(open_page, "j1"))
            assert result["rejected_total"] == 0 and result["accepted"] == len(open_page["tasks"])
    final = svc.finalize_judgments(run_id)
    assert final["final"] == len(tasks) and final["pending_total"] == 0
    state = svc.store.load(run_id)["judgments"]
    models = {f["task_id"]: f["models"] for f in state["final"]}
    assert all(models[t] == [JEV_MODEL] for t in jev_tasks - set(ESCALATE))
    assert all(models[t] == ["claude-sonnet-test"] for t in open_ids)
    scored = svc.score_run(run_id)
    assert scored["ers"]["llm_share"] > 0 and "con giudizi LLM" in scored["scope"]


def test_judge_with_jev_then_claude_backend_keeps_jev_finals(service, monkeypatch):
    """The CLI path: judge --backend jev, then judge --backend cli --samples 3 judges only what Jev left open; a task
    Jev settled with one sample never trips the three-sample requirement."""
    monkeypatch.setattr(audit_module, "audit_shop", stand_in_audit())
    svc = service()
    run_id = svc.audit_shop(SHOP, wait=True)["run_id"]
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    tasks = {t["task_id"]: t for t in judgments.make_tasks(svc.store.load(run_id))}
    monkeypatch.setattr(typesafe, "post_json", JevJudgeStandIn(tasks, ESCALATE, run_id))
    jev = svc.judge_with(run_id, "jev", 3)
    assert jev["backend"] == "jev" and jev["samples"] == 1 and jev["accepted"] == 7 and jev["open_tasks"] == 9
    seen = []

    def judge(self, batch):
        seen.extend(t["task_id"] for t in batch)
        return scripted_verdicts(self, batch)
    monkeypatch.setattr(judges.ClaudeCliJudge, "judge", judge)
    claude = svc.judge_with(run_id, "cli", 3, model="claude-test")
    assert claude["rejected_total"] == 0 and claude["finalize"]["pending"] == 0
    assert len(seen) == 3 * 9 and len(set(seen)) == 9  # three samples of each open task, nothing Jev settled
    state = svc.store.load(run_id)["judgments"]
    assert len(state["final"]) == len(tasks)
    assert sum(1 for f in state["final"] if f["models"] == [JEV_MODEL] and f["samples"] == 1) == 7


class PilotRunner(FakeRunner):
    """FakeRunner that records the policy it was started with, and a typesafe run_auto (Jev chose until DONE)."""

    def start(self, url, goal, *, oracle, policy="host", **kwargs):
        self.policy = policy
        started = super().start(url, goal, oracle=oracle)
        self.store.update(self.run_id, lambda run: run["journey"].update(policy=policy, text_helper=None))
        if policy == "typesafe":
            return {"run_id": self.run_id, "status": "running"}
        return started

    def run_auto(self):
        self.calls.append(("run_auto", None))
        self.status = "done"

    def finish(self, status=None):
        result = super().finish(status)
        costs = {"model_calls": {"choose": 4, "text": 0, "stale_or_refused": 0},
                 "timing_ms": {"decision": 900, "text": 0, "site": 2100, "wall": 3500}}
        self.store.update(self.run_id, lambda run: run["journey"].update(costs))
        return result


def test_run_journey_auto_lets_jev_pilot_when_the_server_has_its_key(service, monkeypatch):
    """policy "auto" (the default): without TYPESAFE_API_KEY the host drives (observation returned); with it Jev
    pilots in the background: next names wait_run then journey_finish, journey_act is refused, and journey_finish
    returns the verdict with the pilot and what deciding cost. Both runs record what was requested."""
    monkeypatch.setattr(journey_module, "JourneyRunner", PilotRunner)
    FakeRunner.instances = []  # FakeRunner.__init__ records every runner there
    svc = service()
    server = build_server(svc)

    async def host(client):
        return await call(client, "run_journey", url=SHOP, goal="Trova scarpe", oracle="cart_not_empty")
    hosted = session(server, host)
    assert hosted["policy"] == "host" and hosted["policy_requested"] == "auto" and "observation" in hosted
    assert FakeRunner.instances[-1].policy == "host"
    assert svc.store.load(hosted["run_id"])["journey"]["policy_requested"] == "auto"
    svc.journey_finish(hosted["run_id"], "done")

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")

    async def jev(client):
        started = await call(client, "run_journey", url=SHOP, goal="Trova scarpe", oracle="cart_not_empty")
        run_id = started["run_id"]
        waited = await call(client, "wait_run", run_id=run_id, timeout_s=20)
        with pytest.raises(ToolFailed, match=r"policy typesafe by itself .*wait_run"):
            await call(client, "journey_act", run_id=run_id, operation="WAIT", observation_id=1)
        finished = await call(client, "journey_finish", run_id=run_id)
        explicit = await call(client, "run_journey", url=SHOP, goal="Trova scarpe", oracle="cart_not_empty",
                              policy="host")
        return started, waited, finished, explicit
    started, waited, finished, explicit = session(server, jev)
    run_id = started["run_id"]
    assert started["status"] == "running" and started["policy"] == "typesafe" and "observation" not in started
    assert started["policy_requested"] == "auto" and started["text_helper"] is None
    assert started["next"] == f"wait_run('{run_id}') until the journey finished, then journey_finish('{run_id}')"
    assert FakeRunner.instances[1].policy == "typesafe" and ("run_auto", None) in FakeRunner.instances[1].calls
    assert not waited["timed_out"] and waited["next"] == f"journey_finish('{run_id}')"
    assert waited["journey"]["policy"] == "typesafe" and waited["journey"]["policy_requested"] == "auto"
    assert finished["verification"]["passed"] is True and finished["policy"] == "typesafe"
    assert finished["model_calls"] == {"choose": 4, "text": 0, "stale_or_refused": 0}
    assert finished["timing_ms"]["decision"] == 900 and finished["text_helper"] is None
    assert explicit["policy"] == "host" and explicit["policy_requested"] == "host"
    with pytest.raises(ValueError, match="policy must be one of"):
        svc.run_journey(SHOP, "Trova scarpe", "cart_not_empty", policy="random")


def jev_pilot(calls: list):
    """jev_ultrafast.model.post_json for model.choose on the fixture shop: checks the request (the loop's own
    operation and target heads over observed indices) and picks like the test's host: reject consent, category, a
    product, add to cart, the cart, DONE."""
    def post(url, key, body):
        assert url == SYSTEMONE and key == "test-key" and set(body) == {"model", "state", "questions"}
        operations = body["questions"]["operation"]["criteria"]
        assert {"CLICK", "DONE", "BLOCKED"} <= set(operations)
        assert {q[:-len("_target")].upper() for q in body["questions"] if q != "operation"} <= set(operations)
        elements = {e["label"]: e["index"] for e in body["state"]["elements"] if "CLICK" in e["operations"]}
        page = body["state"]["page"]["url"]
        calls.append(page)
        wanted = next((label for label in ("Rifiuta tutti",) if label in elements), None)
        if wanted is None and "index.html" in page:
            wanted = "Scarpe da corsa"
        elif wanted is None and "category.html" in page:
            wanted = next((label for label in elements if label.startswith("Scarpa da corsa")), None)
        elif wanted is None and "product.html" in page:
            wanted = next((label for label in ("Vai al carrello", "Aggiungi al carrello") if label in elements), None)
        operation = "DONE" if "cart.html" in page else "CLICK" if wanted in elements else "SCROLL_DOWN"
        answers = {"operation": choice(operations, operation, 0.9)}
        if operation == "CLICK":
            targets = body["questions"]["click_target"]["criteria"]
            assert set(targets) <= {e["index"] for e in body["state"]["elements"]}  # observed indices only
            answers["click_target"] = choice(targets, elements[wanted], 0.9)
        return {"model": JEV_MODEL, "answers": answers, "usage": {"input_tokens": 3000, "output_tokens": 0}}
    return post


def test_jev_pilots_a_journey_on_the_fixture_shop_by_default(chromium, shop_server, service, monkeypatch):
    """The original loop as the default pilot: run_journey without a policy, with TYPESAFE_API_KEY on the server and no
    text helper, runs Agent + model.choose (one request per decision) in the background; journey_finish verifies the
    cart independently and reports decisions equal to the requests the stand-in received."""
    monkeypatch.setattr(PageCollector, "net_quiet_s", 0.8)
    monkeypatch.setattr(PageCollector, "lcp_quiet_s", 0.8)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    calls = []
    monkeypatch.setattr(typesafe, "post_json", jev_pilot(calls))
    svc = service(transport_factory=lambda: DirectTransport(chromium.ws_url))
    server = build_server(svc)
    url = shop_server.url("shop/index.html")
    since = len(shop_server.requests)

    async def scenario(client):
        started = await call(client, "run_journey", url=url, goal="Aggiungi al carrello un prodotto",
                             oracle="cart_not_empty", profile="desktop")
        assert started["policy"] == "typesafe" and started["next"].startswith("wait_run(")
        for _ in range(5):
            waited = await call(client, "wait_run", run_id=started["run_id"], timeout_s=60)
            if not waited["timed_out"]:
                break
        return await call(client, "journey_finish", run_id=started["run_id"])
    finished = session(server, scenario)
    assert finished["verification"]["passed"] is True, finished
    assert finished["status"] == "done" and finished["policy"] == "typesafe" and finished["text_helper"] is None
    assert finished["model_calls"]["choose"] == len(calls) >= 4 and finished["model_calls"]["text"] == 0
    assert finished["model_calls"].get("failed", 0) == 0
    assert finished["timing_ms"]["decision"] >= 0 and finished["steps"] >= 3
    steps = svc.store.read_steps(finished["run_id"])
    assert {s.get("policy") for s in steps if s.get("operation") != "NAVIGATION"} <= {"typesafe"}
    assert "TYPE_TEXT" not in {s.get("operation") for s in steps}
    requests = shop_server.requests[since:]
    assert not [r for r in requests if r["method"] != "GET" or r["path"].startswith("/pay")]


def test_the_env_file_reaches_the_server_without_overriding_its_environment(tmp_path, monkeypatch):
    """JEV_ENGAGEMENT_ENV (plugin.json: ${CLAUDE_PLUGIN_DATA}/.env) is read at start with setdefault semantics: a
    variable the server inherited wins; the host learns which keys were found, never a value."""
    env = tmp_path / "keys.env"
    env.write_text("# keys of the plugin\nTYPESAFE_API_KEY=ts-from-file\nexport TEXT_MODEL_API_KEY='tm-from-file'\n"
                   "TEXT_MODEL=file-model\n", encoding="utf-8")
    monkeypatch.setenv("JEV_ENGAGEMENT_ENV", str(env))
    monkeypatch.setenv("TEXT_MODEL", "inherited-model")
    assert mcp_server.load_keys() == {"typesafe_key": True, "text_helper": "inherited-model"}
    assert os.environ["TYPESAFE_API_KEY"] == "ts-from-file" and os.environ["TEXT_MODEL_API_KEY"] == "tm-from-file"
    monkeypatch.setenv("JEV_ENGAGEMENT_ENV", str(tmp_path / "missing.env"))
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.delenv("TEXT_MODEL_API_KEY")
    assert mcp_server.load_keys() == {"typesafe_key": False, "text_helper": None}  # a missing file is no error

    script = Path(sys.executable).with_name("jev-engage-mcp")
    if not script.exists():
        pytest.skip("jev-engage-mcp is not installed in this environment")
    runs = tmp_path / "runs"
    run_id = RunStore(runs).new_run("audit", SHOP, {})
    child = {**os.environ, "JEV_ENGAGEMENT_ARTIFACTS": str(runs), "JEV_ENGAGEMENT_ENV": str(env),
             "TEXT_MODEL": "inherited-model"}

    async def main():
        async with Client(StdioServerParameters(command=str(script), env=child)) as client:
            result = await client.call_tool("get_run", {"run_id": run_id})
            return result.content[0].text
    text = anyio.run(main)
    assert json.loads(text)["server"] == {"typesafe_key": True, "text_helper": "inherited-model"}
    assert "ts-from-file" not in text and "tm-from-file" not in text
