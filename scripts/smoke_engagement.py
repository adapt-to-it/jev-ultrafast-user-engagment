"""Live smoke of Jev in the engagement auditor: crawler fallback, judge and journey pilot, with real TypeSafe calls.

Not run by pytest: it calls the paid TypeSafe API (and the text helper when TEXT_MODEL_API_KEY is set) with the keys
of ./.env, of the file named by JEV_ENGAGEMENT_ENV, or of the environment. It never places an order: the audit and the
journey stop at the first checkout page and never fill or submit it.

  uv run python scripts/smoke_engagement.py                 # fixture shop, all three roles, forced plp fallback
  uv run python scripts/smoke_engagement.py --url https://shop.example --goal "Aggiungi al carrello un prodotto" \\
      --oracle cart_not_empty
  uv run python scripts/smoke_engagement.py --skip journey  # --skip audit|judge|journey|score, repeatable
  uv run python scripts/smoke_engagement.py --repeat 3 --dump-requests /tmp/jev-requests

Fixture mode serves tests/fixtures on 127.0.0.1 (recording every request) and hides the shop's category links from the
lexicon crawler (crawler.listing_candidates returns nothing), so the listing page is reached only through Jev's pick;
Chromium gets a dead proxy, so nothing but the model requests leaves the machine. --url mode audits a real shop
unchanged.

Every TypeSafe and text-helper request goes through a recording wrapper of jev_ultrafast.model.post_json (and every
answer through model.validate_choice), so the printed counts, latencies and tokens are measured independently of what
the runs record, and the two are compared. --dump-requests writes each request with its answer (never the key).
--repeat N judges copies of the audit N more times (cache off, nothing stored) and prints the label agreement per task.
A smoke never reuses a cached Jev verdict: the judge runs with the cache off (judge_with_jev(use_cache=False)) in a
judge-cache directory of its own, new at every invocation, and the required check judge_reused_no_cached_verdict fails
if a verdict was reused all the same (summary judge.reused). An --audit-run that already holds verdicts or
escalations (judged with the cache on, or by Claude) is judged as a copy from scratch, cache off and nothing stored
(summary judge.judged_a_copy), so its earlier verdicts are never reported as this smoke's answers. --max-steps (1 to
60) and --repeat (0 or more) are checked before anything runs (exit 2).

Once TypeSafe refuses the key (HTTP 401 or 403: wrong, revoked or expired) the later TypeSafe phases (judge, repeat,
journey) are skipped and the check typesafe_key_accepted fails: they would only be refused again.

--audit-run RUN_ID (with --artifacts) judges and scores an earlier audit instead of a new one. Its journey runs on that
audit's own site, so score_run never merges another shop's journey into it: --url must be on the audit's host;
without --url the journey starts from the audit's start page, or from the fixture shop served again when the audit's
smoke_summary.json says a fixture smoke made it (its server and port are gone; nothing else is guessed).

Exit 0 when every check of the mode passed, 1 when one failed, 2 without TYPESAFE_API_KEY, with an --audit-run that
is no audit under --artifacts or with a --url on another host than that audit's. Timings are printed, never asserted.
smoke_summary.json (requests, tokens, p50/p95 latencies, per-rubric probabilities, checks) is written into the audit
run's directory (the journey's when the audit was skipped, the artifacts directory when there is no run).
"""

import argparse
import copy
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import traceback
from collections import Counter, defaultdict
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from jev_ultrafast import model
from jev_ultrafast.engagement import cli, crawler, judges, judgments, mcp_server
from jev_ultrafast.engagement.oracles import parse_params
from jev_ultrafast.engagement.schemas import STAGES
from jev_ultrafast.engagement.service import EngagementService
from jev_ultrafast.engagement.store import RunStore
from jev_ultrafast.questions import MAX_STEPS

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SYSTEMONE = judges.SYSTEMONE
FIXTURE_GOAL = "Aggiungi al carrello un prodotto"
SLOW_MS, BIG_TOKENS = 1500, 20_000  # a warning, never a failure
PHASES = ("audit", "judge", "repeat", "journey")
ASKS_TYPESAFE = ("judge", "repeat", "journey")  # skipped once TypeSafe refused the key (the audit asks only on a miss)
FIXTURE_PATH = "/shop/index.html"
SUMMARY = "smoke_summary.json"  # written into the audit's run directory: its audit_mode marks a fixture smoke's audit
FOREIGN_HOST = "journey runs on another host merged"  # service.score_run's warning: never acceptable in a smoke


# ---------------------------------------------------------------- the fixture shop (as tests/conftest.py serves it)


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURES), **kwargs)

    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        self.server.requests.append({"method": "GET", "path": self.path})
        super().do_GET()

    def do_HEAD(self):
        self.server.requests.append({"method": "HEAD", "path": self.path})
        super().do_HEAD()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.requests.append({"method": "POST", "path": self.path})
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ShopServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.requests: list[dict] = []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.server_port}/{path}"


# ---------------------------------------------------------------- the recording wrapper of every model request


class Recorder:
    """Wraps model.post_json and model.validate_choice (the engagement code looks both up at call time)."""

    def __init__(self, dump: Path | None):
        self.phase, self.calls, self.invalid, self.dump = "setup", [], [], dump
        self._post, self._validate = model.post_json, model.validate_choice
        self._lock = threading.Lock()
        if dump:
            dump.mkdir(parents=True, exist_ok=True)

    def install(self):
        model.post_json, model.validate_choice = self.post, self.validate

    def uninstall(self):
        model.post_json, model.validate_choice = self._post, self._validate

    def post(self, url, key, body):
        started, result, error = time.perf_counter(), None, None
        try:
            result = self._post(url, key, body)
            return result
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            latency = round((time.perf_counter() - started) * 1000)
            usage = result.get("usage") if isinstance(result, dict) and isinstance(result.get("usage"), dict) else {}
            kind = "typesafe" if url == SYSTEMONE else "text"
            with self._lock:
                call = {"n": len(self.calls) + 1, "phase": self.phase, "kind": kind, "latency_ms": latency,
                        "input_tokens": usage.get("input_tokens") or usage.get("prompt_tokens") or 0,
                        "questions": len(body.get("questions") or {}) if kind == "typesafe" else None,
                        "what": _what(body, kind), "model": result.get("model") if isinstance(result, dict) else None,
                        "error": error}
                self.calls.append(call)
            if self.dump:  # the key travels in a header and is never written
                record = {"url": url, **call, "request": body, "response": result}
                path = self.dump / f"{call['n']:03d}-{call['phase']}-{kind}.json"
                path.write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    def validate(self, answer, ids):
        try:
            return self._validate(answer, ids)
        except ValueError:
            with self._lock:
                self.invalid.append({"phase": self.phase, "after_call": len(self.calls),
                                     "answer": str(answer)[:300]})
            raise

    def of(self, phase: str, kind: str = "typesafe") -> list[dict]:
        return [c for c in self.calls if c["phase"] == phase and c["kind"] == kind]


def _what(body: dict, kind: str) -> str:
    """One line about a request: the judged page and its heads, or the page Jev chose on."""
    state = body.get("state") if isinstance(body.get("state"), dict) else {}
    if kind != "typesafe":
        return "text helper"
    if "tasks" in state:
        page = state.get("page") or {}
        return f"judge {page.get('page_type')}/{page.get('stage')}: {len(state['tasks'])} task"
    page = state.get("page") or {}
    return f"choose on {str(page.get('url') or '')[:80]} ({len(state.get('elements') or [])} elements)"


# ---------------------------------------------------------------- helpers


def quantile(values, q):
    """Nearest-rank quantile (None for no values)."""
    values = sorted(v for v in values if isinstance(v, (int, float)))
    return values[max(0, math.ceil(q * len(values)) - 1)] if values else None


def stats(calls: list[dict]) -> dict:
    latencies, tokens = [c["latency_ms"] for c in calls], [c["input_tokens"] for c in calls]
    return {"requests": len(calls), "errors": sum(1 for c in calls if c["error"]),
            "latency_ms": {"p50": quantile(latencies, 0.5), "p95": quantile(latencies, 0.95),
                           "max": max(latencies, default=None), "sum": sum(latencies)},
            "input_tokens": {"sum": sum(tokens), "max": max(tokens, default=None)}}


def say(message: str) -> None:
    print(f"  … {message}", file=sys.stderr, flush=True)


def heading(text: str) -> None:
    print(f"\n== {text}", flush=True)


def jev_readings(run: dict) -> list[dict]:
    """Per task Jev read: rubric, label, probability, evidence ids, final, escalation reason."""
    state = run.get("judgments") or {}
    verdicts = {v["task_id"]: v for v in state.get("verdicts") or [] if v.get("judge_id") == judgments.JEV_JUDGE_ID}
    final = {f["task_id"] for f in state.get("final") or []}
    rows = []
    for task in state.get("tasks") or []:
        verdict, escalation = verdicts.get(task["task_id"]), task.get("escalation") or {}
        if verdict is None and escalation.get("from") != "jev":
            continue
        rows.append({"task_id": task["task_id"], "rubric_id": task["rubric_id"],
                     "label": (verdict or escalation).get("label"),
                     "probability": verdict["confidence"] if verdict else escalation.get("probability"),
                     "evidence": [e["snippet_id"] for e in (verdict or {}).get("evidence") or []],
                     "final": task["task_id"] in final, "escalated": escalation.get("reason")})
    return rows


def per_rubric(rows: list[dict]) -> dict:
    """Mean label probability, tasks and escalations per rubric: the signal for the rubrics' Italian criteria."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["rubric_id"]].append(row)
    out = {}
    for rubric, items in sorted(grouped.items()):
        ps = [r["probability"] for r in items if isinstance(r["probability"], (int, float))]
        out[rubric] = {"tasks": len(items), "accepted": sum(1 for r in items if not r["escalated"]),
                       "escalated": dict(Counter(r["escalated"] for r in items if r["escalated"])),
                       "mean_p": round(sum(ps) / len(ps), 3) if ps else None}
    return out


# ---------------------------------------------------------------- phases


def run_audit(service, url, args, fixture: bool, summary: dict) -> str | None:
    hidden = " with the plp lexicon hidden: the listing must come from Jev" if fixture else ""
    heading(f"Audit ({args.profile}){hidden}")
    original = crawler.listing_candidates
    if fixture:
        crawler.listing_candidates = lambda *a, **k: []
    try:
        audited = service.audit_shop(url, profiles=[args.profile], browser=args.browser, locale=args.locale,
                                     wait=True, progress=say, headless=not args.headed)
    finally:
        crawler.listing_candidates = original
    run_id = audited["run_id"]
    run = service.store.load(run_id)
    reached = {page.get("stage") for page in run.get("pages") or [] if page.get("profile") == args.profile}
    missing = {n.get("stage"): n.get("reason") for n in run.get("not_assessable") or [] if n.get("stage")}
    print(f"run {run_id}: {run.get('status')}, {len(run.get('pages') or [])} pages")
    for stage in STAGES:
        print(f"  {stage:15} {'reached' if stage in reached else 'not reached: ' + str(missing.get(stage))}")
    probes = [(page.get("page_id"), probe) for page in run.get("pages") or []
              for probe in (page.get("probes") or {}).get("jev_fallback") or []]
    for page_id, probe in probes:
        print(f"  jev_fallback {probe.get('purpose'):15} on {page_id}: {probe.get('operation')} "
              f"'{probe.get('label')}' p={probe.get('probability')} executed={probe.get('executed')} "
              f"reason={probe.get('reason')} {probe.get('latency_ms')} ms "
              f"{(probe.get('usage') or {}).get('input_tokens')} tokens")
    if not probes:
        print("  jev_fallback: not asked (the lexicon found every stage)")
    print(f"  model_calls: {json.dumps(run.get('model_calls') or {}, ensure_ascii=False)}")
    for warning in [w for w in run.get("warnings") or [] if "Jev" in w]:
        print(f"  warning: {warning}")
    summary["audit"] = {"run_id": run_id, "status": run.get("status"), "reached": sorted(reached),
                        "not_reached": missing, "jev_fallback": [{"page_id": p, **probe} for p, probe in probes],
                        "model_calls": run.get("model_calls") or {}}
    return run_id


def run_judge(service, run_id, summary: dict) -> dict:
    heading("Jev judge (one TypeSafe request per page, cache off)")
    stored = (service.store.load(run_id).get("judgments") or {})
    earlier = len(stored.get("verdicts") or []) + sum(1 for t in stored.get("tasks") or [] if t.get("escalation"))
    if earlier:  # an --audit-run judged before (cache on, or by Claude): its verdicts are not this smoke's answers
        print(f"  the audit already holds {earlier} verdicts or escalations: Jev judges a copy of it from scratch "
              "(cache off, nothing stored), so every reading below is a live answer")
        run = copy.deepcopy(service.store.load(run_id))
        run["judgments"], run["model_calls"] = {}, {}
        judge = judges.JevJudge()
        raw = judges.judge_with_jev(run, judge, use_cache=False)
        judged = {**raw, "model": raw.get("model") or judge.model, "accepted": raw["judged"], "error_groups": []}
    else:
        judged = service.judge_with_jev(run_id, use_cache=False)  # every verdict is a live answer, never a cached one
        run = service.store.load(run_id)
    rows = jev_readings(run)  # this call's readings only: the run (or its copy) held no verdict before it
    in_run = sum(1 for row in rows if not row["escalated"])
    print(f"available={judged['available']} model={judged['model']} requests={judged['requests']} "
          f"latency={judged['latency_ms']} ms input_tokens={judged['input_tokens']} accepted={judged['accepted']} "
          f"(in the run {in_run}) reused={judged.get('reused')} escalated={judged['escalated']} "
          f"open_tasks={judged['open_tasks']}")
    for row in rows:
        verdict = f"evidence {','.join(row['evidence']) or '-'}" if not row["escalated"] else \
            f"escalated: {row['escalated']}"
        print(f"  {row['task_id']:45} {str(row['label']):9} p={row['probability']} {verdict}")
    for group in judged.get("error_groups") or []:
        print(f"  error on {group['pages']} pages, {group['tasks']} tasks: {group['error']}")
    rubrics = per_rubric(rows)
    for rubric, line in rubrics.items():
        print(f"  {rubric:20} mean p={line['mean_p']} accepted {line['accepted']}/{line['tasks']} "
              f"escalated {line['escalated'] or '-'}")
    rubric_map = judgments.load_rubrics()
    jev_tasks = [t for t in run["judgments"].get("tasks") or [] if judgments.routing(t, rubric_map) == "jev"]
    final = {f["task_id"] for f in run["judgments"].get("final") or []}
    summary["judge"] = {**{k: judged.get(k) for k in ("available", "model", "requests", "latency_ms", "input_tokens",
                                                      "accepted", "reused", "escalated", "open_tasks", "errors",
                                                      "error_groups")},
                        "accepted_in_run": in_run, "judged_a_copy": bool(earlier), "earlier": earlier,
                        "per_task": rows, "per_rubric": rubrics, "jev_tasks": len(jev_tasks),
                        "jev_tasks_open": [t["task_id"] for t in jev_tasks
                                           if t["task_id"] not in final and not t.get("escalation")]}
    return judged


def run_repeats(service, run_id, times: int, summary: dict) -> None:
    heading(f"Jev judge repeated {times} times on copies (cache off, nothing stored)")
    labels = defaultdict(list)
    for row in summary["judge"]["per_task"]:
        labels[row["task_id"]].append(row["label"])
    for _ in range(times):
        run = copy.deepcopy(service.store.load(run_id))
        run["judgments"], run["model_calls"] = {}, {}
        judges.judge_with_jev(run, judges.JevJudge(), use_cache=False)
        for row in jev_readings(run):
            labels[row["task_id"]].append(row["label"])
    agreement = {}
    for task_id, seen in sorted(labels.items()):
        top = Counter(seen).most_common(1)[0][1]
        agreement[task_id] = round(top / len(seen), 3)
        if top < len(seen):
            print(f"  {task_id:45} labels {seen}")
    stable = sum(1 for value in agreement.values() if value == 1)
    print(f"  identical labels on {stable}/{len(agreement)} tasks over {times + 1} runs")
    summary["repeat"] = {"runs": times + 1, "agreement": agreement, "stable": stable}


def run_journey(service, url, args, params, summary: dict) -> dict:
    heading(f"Journey (policy auto): {args.goal!r}, oracle {args.oracle}{f' {params}' if params else ''}")
    finished = service.run_journey(url, args.goal, args.oracle, params, profile=args.profile, policy="auto",
                                   max_steps=args.max_steps, browser=args.browser, locale=args.locale, wait=True,
                                   headless=not args.headed)
    run_id = finished["run_id"]
    run = service.store.load(run_id)
    journey = run.get("journey") or {}
    for step in service.store.read_steps(run_id):
        flags = [k for k, v in (step.get("flags") or {}).items() if v]
        print(f"  {step.get('step')!s:>3} {step.get('operation'):10} {str(step.get('label') or '')[:50]:50} "
              f"{step.get('page_type') or '-':9} decision {step.get('decision_latency_ms')} ms {' '.join(flags)}")
    verification = journey.get("verification") or {}
    print(f"run {run_id}: journey {journey.get('status')}, policy {journey.get('policy')} "
          f"(requested {journey.get('policy_requested')}), text helper {journey.get('text_helper')}")
    print(f"  model_calls {journey.get('model_calls')} timing_ms {journey.get('timing_ms')} "
          f"usage {journey.get('usage')}")
    print(f"  verification passed={verification.get('passed')} "
          f"not_assessable={(verification.get('checks') or {}).get('not_assessable')}")
    summary["journey"] = {"run_id": run_id, "status": journey.get("status"), "run_status": run.get("status"),
                          **{k: journey.get(k) for k in ("policy", "policy_requested", "text_helper", "model_calls",
                                                         "timing_ms", "usage")},
                          "verification": {"passed": verification.get("passed"),
                                           "not_assessable": (verification.get("checks") or {}).get("not_assessable")}}
    return summary["journey"]


def run_score(service, audit_id, journey_id, summary: dict) -> None:
    heading("Scores")
    scored = service.score_run(audit_id, [journey_id] if journey_id else [])
    ers = scored.get("ers") or {}
    print(f"ERS {ers.get('score')} (published {ers.get('published')}), {scored.get('confidence')}, llm_share "
          f"{ers.get('llm_share')}")
    for warning in scored.get("warnings") or []:
        print(f"  warning: {warning}")
    print(f"report: {(scored.get('report') or {}).get('report_html')}")
    summary["score"] = {"ers": ers, "confidence": scored.get("confidence"), "report": scored.get("report"),
                        "warnings": scored.get("warnings") or []}


# ---------------------------------------------------------------- checks


def checks(summary: dict, recorder: Recorder, shop: ShopServer | None, fixture: bool) -> list[dict]:
    """(name, ok: True/False/None for not run, required in this mode, detail)."""
    out = []

    def check(name, ok, required, detail=""):
        out.append({"name": name, "ok": ok, "required": bool(required and ok is not None), "detail": detail})

    audit, judge, journey, score = (summary.get(k) for k in ("audit", "judge", "journey", "score"))
    if audit:
        check("audit_finished", audit["status"] in ("complete", "partial"), True, audit["status"])
        plp = [p for p in audit["jev_fallback"] if p.get("purpose") == "plp" and p.get("executed")]
        check("plp_through_jev_fallback", bool(plp) and "plp" in audit["reached"], fixture,
              f"{len(plp)} executed plp lookups, plp {'reached' if 'plp' in audit['reached'] else 'not reached'}")
        check("cart_reached", "cart" in audit["reached"], fixture, audit["not_reached"].get("cart") or "")
        sent = len(recorder.of("audit"))
        recorded = ((audit["model_calls"] or {}).get("crawler_fallback") or {}).get("requests", 0)
        check("crawler_calls_match_the_run", sent == recorded, True, f"sent {sent}, run records {recorded}")
    if judge:
        check("jev_judge_accepted_a_verdict", judge["accepted_in_run"] > 0, fixture,  # live answers only: an
              f"{judge['accepted_in_run']} accepted in the run ({judge['accepted']} by this call)")  # --audit-run's
        # earlier verdicts are never counted, the smoke then judges a copy of the audit from scratch
        check("jev_tasks_settled_or_escalated", judge["available"] and not judge["jev_tasks_open"], True,
              ", ".join(judge["jev_tasks_open"][:5]))
        sent = len(recorder.of("judge"))
        check("judge_calls_match_the_run", sent == judge["requests"], True,
              f"sent {sent}, judge reports {judge['requests']}")
        reused = judge.get("reused") or 0  # cached verdicts: no live answer behind them (the judge runs cache off)
        check("judge_reused_no_cached_verdict", reused == 0, True, f"{reused} cached verdicts reused")
    if journey:
        calls = journey.get("model_calls") or {}
        check("journey_piloted_by_jev", journey.get("policy") == "typesafe" and (calls.get("choose") or 0) > 0, True,
              f"policy {journey.get('policy')}, {calls.get('choose')} decisions")
        check("journey_verified_by_the_oracle", journey["verification"]["passed"] is True, fixture,
              f"passed {journey['verification']['passed']} ({journey['verification']['not_assessable']})")
        sent, texts = len(recorder.of("journey")), len(recorder.of("journey", "text"))
        recorded = (calls.get("choose") or 0) + (calls.get("failed") or 0)  # failed: no valid decision came back
        written = (calls.get("text") or 0) + (calls.get("text_failed") or 0)  # text_failed: no usable value came back
        check("journey_calls_match_the_run", sent == recorded and texts == written, True,
              f"sent {sent} TypeSafe + {texts} text, run records {calls.get('choose')} decisions + "
              f"{calls.get('failed') or 0} failed + {calls.get('text')} text + {calls.get('text_failed') or 0} "
              "text failed")
    if score:
        foreign = [w for w in score["warnings"] if w.startswith(FOREIGN_HOST)]
        check("journey_on_the_audit_host", not foreign, True, "; ".join(foreign))
    refused = key_refused(recorder)
    check("typesafe_key_accepted", refused is None, True,
          f"{refused['error']} at request {refused['n']} ({refused['phase']}): check TYPESAFE_API_KEY; the later "
          "TypeSafe phases were skipped" if refused else "")
    failed = [c for c in recorder.calls if c["error"] and c["kind"] == "typesafe" and c["phase"] != "repeat"]
    check("typesafe_answers_valid", not recorder.invalid and not failed, True,
          f"{len(recorder.invalid)} invalid answers, {len(failed)} failed requests")
    if shop is not None:
        bad = [r for r in shop.requests if r["method"] not in ("GET", "HEAD") or r["path"].startswith("/pay")]
        check("no_order_or_payment_request", not bad, True, json.dumps(bad[:3]))
    return out


# ---------------------------------------------------------------- main


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", type=cli._shop_url, help="a real shop's home page (default: the fixture shop)")
    parser.add_argument("--goal", default=FIXTURE_GOAL, help=f"journey goal (default: {FIXTURE_GOAL!r})")
    parser.add_argument("--oracle", choices=cli.ORACLES, default="cart_not_empty")
    parser.add_argument("--oracle-param", action="append", default=[], type=cli._pair, metavar="K=V")
    parser.add_argument("--profile", choices=("mobile", "desktop"), default="mobile")
    parser.add_argument("--browser", default="auto", help="auto | launch | harness | cdp:<DevTools URL>")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--locale", default="it")
    parser.add_argument("--max-steps", type=cli._integer(1, MAX_STEPS), default=30)  # checked before any request
    parser.add_argument("--artifacts", help="run directory root (default: a new temporary directory)")
    parser.add_argument("--audit-run", metavar="RUN_ID", help="judge and score this audit of --artifacts instead")
    parser.add_argument("--skip", action="append", default=[], choices=("audit", "judge", "journey", "score"))
    parser.add_argument("--repeat", type=cli._integer(0), default=0, metavar="N",
                        help="judge N more copies (label agreement)")
    parser.add_argument("--dump-requests", type=Path, metavar="DIR", help="write each request and answer as JSON")
    return parser.parse_args(argv)


def load_audit_run(args) -> tuple[dict | None, str | None]:
    """(the --audit-run audit, None) or (None, why it cannot be judged and scored): the run must be an audit stored
    under --artifacts, as the default artifacts directory is a new empty one. (None, None) without --audit-run."""
    if not args.audit_run:
        return None, None
    if not args.artifacts:
        return None, "--audit-run needs --artifacts (the directory that holds that run)"
    try:
        run = RunStore(Path(args.artifacts).resolve()).load(args.audit_run)
    except (OSError, ValueError) as exc:
        return None, f"--audit-run: {exc} in --artifacts {args.artifacts}"
    if run.get("kind") != "audit":
        return None, f"--audit-run: {args.audit_run} is a {run.get('kind')} run, not an audit"
    return run, None


def audit_mode(args, run_id: str) -> str | None:
    """How the audit was made, from the smoke_summary.json a smoke wrote into its run directory: "fixture" (the fixture
    shop this script served), "url", or None (no smoke made it: the CLI, the plugin, or a smoke stopped before writing).
    The record, never the URL's shape: two shops on 127.0.0.1 differ only by port, which run["site"]["host"] lacks."""
    try:
        marker = json.loads((RunStore(Path(args.artifacts).resolve()).path(run_id) / SUMMARY).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return (marker.get("audit_mode") or marker.get("mode")) if isinstance(marker, dict) else None


def journey_site(args, audit: dict | None) -> tuple[bool, str | None, str | None]:
    """(fixture, url, problem): where the journey runs (url None: the fixture shop served here). Without --audit-run,
    the fixture shop unless --url names a shop. With it, score_run merges the journey into that audit, so the journey
    runs on the audit's own site: --url must be on the audit's host (problem otherwise); without --url it starts from
    the fixture shop served again iff the audit's smoke_summary.json says a fixture smoke made it (that server and its
    port are gone), else from the audit's start page (a fixture audit without the record fails there, visibly)."""
    if audit is None:
        return args.url is None, args.url, None
    site = audit.get("site") or {}
    host, start = str(site.get("host") or "").lower(), str(site.get("start_url") or "")
    if args.url:
        if (urlsplit(args.url).hostname or "") != host:
            return False, args.url, (f"--url {args.url} is not on {host or 'an unknown host'}, the host of --audit-run "
                                     f"{args.audit_run}: its journey would be scored into another shop's audit (pass "
                                     "a URL of that shop, or --skip journey)")
        return False, args.url, None
    if audit_mode(args, audit.get("run_id") or args.audit_run) == "fixture":
        return True, None, None
    return False, start, None


def fresh_cache(artifacts: Path) -> Path:
    """A new, empty judge-cache directory under the artifacts for this invocation only (judge-cache-XXXXXXXX): a
    rerun with the same --artifacts never finds the verdicts of an earlier one."""
    artifacts.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="judge-cache-", dir=artifacts))


def key_refused(recorder: Recorder) -> dict | None:
    """The first TypeSafe request refused with HTTP 401 or 403 (a wrong, revoked or expired key), else None."""
    return next((c for c in recorder.calls if c["kind"] == "typesafe" and c["error"]
                 and re.search(r"HTTP 40[13]\b", c["error"])), None)


def main(argv=None) -> int:
    args = parse_args(argv)
    audit, problem = load_audit_run(args)
    fixture, url, wrong_site = journey_site(args, audit)
    if problem or (wrong_site and "journey" not in args.skip):
        print(problem or wrong_site, file=sys.stderr)
        return 2
    try:
        cli.load_environment()  # ./.env, then the plugin's env file when JEV_ENGAGEMENT_ENV names one
    except (OSError, ValueError) as exc:  # the reason, never the file's content
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else type(exc).__name__
        print(f"./.env not read ({reason}): the environment's variables stay", file=sys.stderr)
    keys = mcp_server.load_keys()
    if not keys["typesafe_key"]:
        print("TYPESAFE_API_KEY is missing (./.env, the JEV_ENGAGEMENT_ENV file or the environment): nothing to "
              "smoke.", file=sys.stderr)
        return 2
    try:
        params = parse_params(args.oracle, dict(args.oracle_param))
    except ValueError as exc:
        print(f"--oracle-param: {exc}", file=sys.stderr)
        return 2
    artifacts = Path(args.artifacts or tempfile.mkdtemp(prefix="jev-smoke-")).resolve()
    cache = fresh_cache(artifacts)  # a smoke never reuses a cached verdict: new and empty at every invocation
    os.environ["JEV_ENGAGEMENT_CACHE"] = str(cache)
    if fixture:
        os.environ.setdefault("JEV_CHROME_ARGS", "--proxy-server=http://127.0.0.1:9")  # only model requests go out
    print(f"TypeSafe model {os.environ.get('TYPESAFE_MODEL') or 'jev-latest'}; text helper "
          f"{keys['text_helper'] or 'missing (TEXT_MODEL_API_KEY): a TYPE_TEXT Jev chooses is refused'}")
    print(f"artifacts: {artifacts} (judge cache {cache.name}, not read)")

    shop = ShopServer() if fixture else None
    url = shop.url(FIXTURE_PATH.lstrip("/")) if fixture else url
    print(f"mode: {'fixture' if fixture else 'url'} {url}"
          f"{f' (the journey of audit {args.audit_run})' if args.audit_run else ''}")
    recorder = Recorder(args.dump_requests)
    recorder.install()
    service = EngagementService(RunStore(artifacts))
    mode = "fixture" if fixture else "url"
    summary = {"mode": mode, "audit_mode": audit_mode(args, args.audit_run) if args.audit_run else mode, "url": url,
               "started_at": datetime.now(UTC).isoformat(),
               "typesafe_model": os.environ.get("TYPESAFE_MODEL") or "jev-latest", "text_helper": keys["text_helper"],
               "artifacts": str(artifacts), "judge_cache": str(cache), "errors": {}, "skipped": {}}
    audit_id, journey_id = args.audit_run, None

    def phase(name, fn):
        if name in ASKS_TYPESAFE and (refused := key_refused(recorder)):  # every request would be refused too
            summary["skipped"][name] = f"TypeSafe refused the key ({refused['error']}) at request {refused['n']}"
            print(f"  {name} skipped: {summary['skipped'][name]}", file=sys.stderr)
            return None
        recorder.phase = name
        try:
            return fn()
        except Exception as exc:  # a phase that breaks fails the smoke and the others still run
            summary["errors"][name] = f"{type(exc).__name__}: {exc}"
            print(f"  {name} failed: {summary['errors'][name]}", file=sys.stderr)
            traceback.print_exc(limit=4)
            return None
        finally:
            recorder.phase = "setup"

    try:
        if "audit" not in args.skip and not args.audit_run:
            audit_id = phase("audit", lambda: run_audit(service, url, args, fixture, summary))
        if audit_id and "judge" not in args.skip:
            if phase("judge", lambda: run_judge(service, audit_id, summary)) and args.repeat > 0:
                phase("repeat", lambda: run_repeats(service, audit_id, args.repeat, summary))
        if "journey" not in args.skip:
            journey = phase("journey", lambda: run_journey(service, url, args, params, summary))
            journey_id = (journey or {}).get("run_id")
        if audit_id and "score" not in args.skip:
            phase("score", lambda: run_score(service, audit_id, journey_id, summary))
    finally:
        recorder.uninstall()
        service.shutdown()
        if shop is not None:
            shop.shutdown()
            shop.server_close()

    heading("Requests (measured by the wrapper)")
    summary["requests"] = {}
    for name in PHASES:
        for kind in ("typesafe", "text"):
            calls = recorder.of(name, kind)
            if not calls:
                continue
            line = summary["requests"][f"{name}/{kind}"] = stats(calls)
            print(f"  {name:8} {kind:8} {line['requests']:3} requests, {line['errors']} errors, latency p50 "
                  f"{line['latency_ms']['p50']} ms p95 {line['latency_ms']['p95']} ms, input tokens "
                  f"{line['input_tokens']['sum']} (max {line['input_tokens']['max']})")
    for call in recorder.of("judge"):  # one per judged page
        print(f"  judge request {call['n']}: {call['what']}, {call['questions']} heads, {call['latency_ms']} ms, "
              f"{call['input_tokens']} input tokens{', ' + call['error'] if call['error'] else ''}")
    for call in recorder.calls:
        if call["latency_ms"] > SLOW_MS or (call["input_tokens"] or 0) > BIG_TOKENS:
            print(f"  warning: request {call['n']} ({call['phase']}, {call['what']}) took {call['latency_ms']} ms "
                  f"with {call['input_tokens']} input tokens")
    summary["calls"] = recorder.calls
    summary["invalid_answers"] = recorder.invalid

    heading("Checks")
    summary["checks"] = checks(summary, recorder, shop, fixture)
    for name, error in summary["errors"].items():
        summary["checks"].append({"name": f"{name}_ran", "ok": False, "required": True, "detail": error})
    for item in summary["checks"]:
        mark = "PASS" if item["ok"] else "FAIL" if item["required"] else "info"
        print(f"  {mark:4} {item['name']:32} {item['detail']}")
    summary["passed"] = all(item["ok"] for item in summary["checks"] if item["required"])
    store = RunStore(artifacts)
    target = audit_id or journey_id
    summary = json.loads(json.dumps(summary, ensure_ascii=False, default=str))
    if target and store.path(target).is_dir():  # audit_mode keeps the audit's own record across --audit-run smokes
        store.write_json(target, SUMMARY, summary)
        where = store.path(target) / SUMMARY
    else:  # no run, or one that is not under --artifacts
        artifacts.mkdir(parents=True, exist_ok=True)
        where = artifacts / SUMMARY
        where.write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\n{'PASSED' if summary['passed'] else 'FAILED'}; summary: {where}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
