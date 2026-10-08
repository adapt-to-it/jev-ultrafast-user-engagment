"""Closed-label LLM judgments over page snippets: tasks, verdict validation, majority finalisation and a cache.

Each rubric names its judge ("judge": "jev" | "claude"). Jev (TypeSafe, judges.judge_with_jev) answers the operational
rubrics with one sample per task, chosen labels and evidence chosen among the offered snippet ids; a task Jev is not
sure about is escalated and judged like a Claude task. Claude (the MCP host's subagents, or a fallback judge from
judges.py) answers the perception rubrics and the escalations with three samples. Every evidence quote must be a
verbatim substring of the snippet it cites.
"""

import hashlib
import json
import math
import os
import re
import statistics
import unicodedata
from collections import Counter
from pathlib import Path

from .kpis import KPIS
from .store import iso_now

RUBRICS_DIR = Path(__file__).with_name("rubrics")
RUBRICS_VERSION = "rubrics.v2"
JUDGES = ("jev", "claude")  # rubric["judge"]: "jev" = Jev first, Claude for the tasks Jev escalates
JEV_JUDGE_ID = "jev"  # judge_id of Jev's verdicts (judges.JevJudge)
RETRY_REASONS = ("request_failed", "invalid_response")  # escalations without a Jev reading: Jev may try again
MAX_SNIPPET_CHARS = 600
MAX_RATIONALE_CHARS = 280
MAX_QUOTES = 8
MIN_QUOTE_CHARS = 8  # shorter quotes must be the whole snippet
MIN_AGREEMENT = 2 / 3
HOST_PROMPT = "host"  # cache namespace of verdicts submitted by the MCP host, which judges with its own prompt


def load_rubrics(directory=None) -> dict:
    rubrics = {}
    for path in sorted(Path(directory or RUBRICS_DIR).glob("*.json")):
        rubric = json.loads(path.read_text(encoding="utf-8"))
        rubrics[rubric["id"]] = rubric
    return rubrics


def collapse(text) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text)).split())


def normalize_text(text) -> str:
    """Dedup key: NFC, whitespace collapsed, casefolded (never used to verify quotes)."""
    return collapse(text).casefold()


def verbatim(quote, text) -> str | None:
    """The snippet's own spelling of quote, or None.

    Both sides are NFC-normalised and whitespace-collapsed; letter case may differ, but the returned string is always
    the page text, so a stored quote never shows the judge's spelling.
    """
    needle = collapse(quote)
    if not needle:
        return None
    match = re.search(re.escape(needle), collapse(text), re.IGNORECASE)
    return match.group(0) if match else None


def _snippets(page, rubric) -> list:
    """Up to max_snippets of the rubric's kinds, listed in page order.

    The limit applies in the rubric's kind order (decisive kinds first), so a crowd of CTAs never pushes the
    subscription or fee text out of a task.
    """
    kinds = list(rubric["snippet_kinds"])
    limit = rubric.get("grouping", {}).get("max_snippets", 8)
    candidates = []
    for index, snippet in enumerate((page.get("audit") or {}).get("snippets") or []):
        if isinstance(snippet, dict) and snippet.get("kind") in kinds:
            text = collapse(snippet.get("text") or "")[:MAX_SNIPPET_CHARS].strip()
            if text:
                candidates.append((kinds.index(snippet["kind"]), index, snippet, text))
    chosen, texts = [], set()
    for _, index, snippet, text in sorted(candidates, key=lambda c: c[:2]):
        if len(chosen) >= limit:
            break
        if normalize_text(text) not in texts:
            texts.add(normalize_text(text))
            chosen.append((index, snippet, text))
    items = []
    for index, snippet, text in sorted(chosen, key=lambda c: c[0]):
        item = {"snippet_id": str(snippet.get("snippet_id") or f"s{index}"), "kind": snippet["kind"], "text": text}
        if snippet.get("locator"):
            item["locator"] = collapse(snippet["locator"])[:120]
        items.append(item)
    return items


def make_tasks(run, *, samples_required=3, rubrics=None) -> list:
    """One task per rubric and page; identical snippet sets (e.g. mobile and desktop) share one task.

    A shared task keeps the first page's task_id; context.pages lists every page it covers, while context.page_type,
    stage and url describe the first page only.
    """
    rubrics = rubrics or load_rubrics()
    locale = (run.get("settings") or {}).get("locale", "it")
    tasks, seen = [], {}
    for rubric_id in sorted(rubrics):
        rubric = rubrics[rubric_id]
        for page in run.get("pages") or []:
            stages = rubric.get("stages")
            if stages and page.get("stage") not in stages:
                continue
            snippets = _snippets(page, rubric)
            if not snippets:
                continue
            ref = {"page_id": page["page_id"], "profile": page.get("profile"), "stage": page.get("stage")}
            key = (rubric_id, tuple(normalize_text(s["text"]) for s in snippets))
            if key in seen:
                seen[key]["context"]["pages"].append(ref)
                continue
            task = {
                "task_id": f"{rubric_id}:{page['page_id']}",
                "run_id": run.get("run_id"),
                "rubric_id": rubric_id,
                "rubric_version": rubric["version"],
                "kpi_id": rubric["kpi_id"],
                "page_id": page["page_id"],
                "profile": page.get("profile"),
                "question": rubric["question"],
                "labels": dict(rubric["labels"]),
                "no_quote_labels": list(rubric.get("no_quote_labels") or []),
                "snippets": snippets,
                "context": {
                    "page_type": (page.get("classification") or {}).get("type"),
                    "stage": page.get("stage"),
                    "locale": locale,
                    "url": page.get("final_url") or page.get("url"),
                    "pages": [ref],
                },
                "samples_required": samples_required,
            }
            seen[key] = task
            tasks.append(task)
    return tasks


def skipped_rubrics(run, tasks=None, rubrics=None) -> list:
    """Rubrics with no task, with the not-assessed reason recorded for the report."""
    rubrics = rubrics or load_rubrics()
    tasks = make_tasks(run, rubrics=rubrics) if tasks is None else tasks
    covered = {t["rubric_id"] for t in tasks}
    return [
        {
            "rubric_id": rubric_id,
            "kpi_id": rubrics[rubric_id]["kpi_id"],
            "reason": "no_snippets",
            "detail": "nessuno snippet di tipo " + ", ".join(rubrics[rubric_id]["snippet_kinds"]),
        }
        for rubric_id in sorted(rubrics)
        if rubric_id not in covered
    ]


def ensure_tasks(run, *, samples_required=3) -> list:
    """Create and store tasks once (with skipped rubrics and the rubrics version); later calls return them."""
    state = run.setdefault("judgments", {})
    if not prepared(run):
        rubrics = load_rubrics()
        state["tasks"] = make_tasks(run, samples_required=samples_required, rubrics=rubrics)
        state["skipped"] = skipped_rubrics(run, state["tasks"], rubrics)
        state["rubrics_version"] = RUBRICS_VERSION
    state.setdefault("verdicts", [])
    state.setdefault("final", [])
    return state["tasks"]


def routing(task, rubrics=None) -> str:
    """"jev" or "claude": the judge the task's rubric names, read at judge time (never copied into tasks). A task made
    under another rubrics version, or of an unknown rubric, goes to Claude."""
    rubric = (rubrics or load_rubrics()).get(task.get("rubric_id")) or {}
    if rubric.get("version") != task.get("rubric_version") or rubric.get("judge") not in JUDGES:
        return "claude"
    return rubric["judge"]


def settle(run, task_ids, *, samples_required=1) -> int:
    """Lower samples_required of the given open tasks (a Jev verdict is one sample); never raises it, never touches a
    final task. Returns how many tasks changed; finalize() then decides them as usual."""
    state = run.get("judgments") or {}
    final = {f["task_id"] for f in state.get("final") or []}
    wanted, changed = set(task_ids), 0
    for task in state.get("tasks") or []:
        if task["task_id"] in wanted and task["task_id"] not in final and task.get("samples_required", 3) > (
                samples_required):
            task["samples_required"] = samples_required
            changed += 1
    return changed


def retryable(task) -> bool:
    """True when the task carries no escalation, or one without a Jev reading (RETRY_REASONS: the request failed or
    the answer was invalid), which Jev may replace. low_probability, position_flip and no_evidence are Jev's reading
    of the task: permanent."""
    escalation = task.get("escalation")
    return escalation is None or isinstance(escalation, dict) and escalation.get("reason") in RETRY_REASONS


def escalate(run, escalations: dict) -> int:
    """Mark open tasks as passed to Claude: task["escalation"] = {from, model, label, probability, reason, at}.

    No verdict is stored and samples_required is kept, so the host judges the task like any open one. A final task
    and a task escalated with Jev's reading are left as they are; an escalation without a reading (retryable) is
    replaced. Returns how many tasks were marked."""
    state = run.get("judgments") or {}
    final = {f["task_id"] for f in state.get("final") or []}
    marked = 0
    for task in state.get("tasks") or []:
        reading = escalations.get(task["task_id"])
        if isinstance(reading, dict) and task["task_id"] not in final and retryable(task):
            task["escalation"] = {"from": reading.get("from") or "jev",
                                  **{k: reading.get(k) for k in ("model", "label", "probability", "reason")},
                                  "at": iso_now()}
            marked += 1
    return marked


def prepared(run) -> bool:
    """True once judgments were requested for the run (ensure_tasks ran), even when no page had a snippet. The empty
    seed of RunStore.new_run ({"tasks": [], "verdicts": [], "final": []}, no rubrics_version) is no request."""
    state = run.get("judgments") or {}
    return "rubrics_version" in state or "skipped" in state or bool(state.get("tasks"))


# ---------------------------------------------------------------- verdicts


def _confidence(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if 0 <= value <= 1 else None


def _no_quote_labels(task) -> list:
    if "no_quote_labels" in task:
        return task["no_quote_labels"]
    return (load_rubrics().get(task.get("rubric_id")) or {}).get("no_quote_labels", [])


def _check(verdict, task):
    """(clean verdict fields, None) or (None, reason) for a verdict on a known task."""
    label = verdict.get("label")
    if not isinstance(label, str) or label not in task["labels"]:
        return None, "unknown_label"
    confidence = _confidence(verdict.get("confidence"))
    if confidence is None:
        return None, "invalid_confidence"
    evidence = verdict.get("evidence") or []
    if not isinstance(evidence, list):
        return None, "invalid_evidence"
    texts = {s["snippet_id"]: s["text"] for s in task["snippets"]}
    quotes = []
    for item in evidence:  # every quote is checked; only the first MAX_QUOTES are stored
        snippet_id = item.get("snippet_id") if isinstance(item, dict) else None
        if not isinstance(snippet_id, str) or snippet_id not in texts:
            return None, "unknown_snippet"
        quote = item.get("quote")
        if not isinstance(quote, str) or not collapse(quote):
            return None, "empty_quote"
        found = verbatim(quote, texts[snippet_id])
        if found is None:
            return None, "quote_not_verbatim"
        if len(found) < min(MIN_QUOTE_CHARS, len(collapse(texts[snippet_id]))):
            return None, "quote_too_short"
        quotes.append({"snippet_id": snippet_id, "quote": found[:MAX_SNIPPET_CHARS]})
    quotes = quotes[:MAX_QUOTES]
    if not quotes and label not in _no_quote_labels(task):
        return None, "missing_evidence"
    rationale = verdict.get("rationale")
    rationale = collapse(rationale)[:MAX_RATIONALE_CHARS] if isinstance(rationale, str) else ""
    return {"label": label, "confidence": confidence, "evidence": quotes, "rationale": rationale}, None


def progress(run) -> dict:
    state = run.get("judgments") or {}
    counts = Counter(v["task_id"] for v in state.get("verdicts") or [])
    final = {f["task_id"] for f in state.get("final") or []}
    tasks = state.get("tasks") or []
    complete = {t["task_id"] for t in tasks if counts[t["task_id"]] >= t.get("samples_required", 3)}
    return {
        "tasks_total": len(tasks),
        "samples_complete": len(complete),
        "tasks_remaining": sum(1 for t in tasks if t["task_id"] not in complete | final),
        "final": len(final),
    }


def submit(run, judge_id: str, model: str, verdicts, *, cache=True, prompt=None) -> dict:
    """Validate and store one judge's verdicts. Rejected verdicts are reported, never stored.

    A task takes at most its samples_required verdicts (later ones: "samples_complete"), so a final label depends
    only on the first samples. prompt names the judge prompt for the cache (default: HOST_PROMPT).
    """
    judge_id, model = str(judge_id or "").strip(), str(model or "").strip()
    if not judge_id or not model:
        raise ValueError("submit needs a judge_id and a model id")
    state = run.setdefault("judgments", {})
    tasks = {t["task_id"]: t for t in state.get("tasks") or []}
    final = {f["task_id"] for f in state.get("final") or []}
    stored = state.setdefault("verdicts", [])
    seen = {(v["task_id"], v["judge_id"]) for v in stored}
    counts = Counter(v["task_id"] for v in stored)
    accepted, rejected = 0, []
    if not isinstance(verdicts, (list, tuple)):
        verdicts = [] if verdicts is None else [verdicts]
    for index, verdict in enumerate(verdicts):
        task_id = verdict.get("task_id") if isinstance(verdict, dict) else None
        task_id = task_id if isinstance(task_id, str) else None
        task = tasks.get(task_id)
        if not isinstance(verdict, dict):
            clean, reason = None, "invalid_verdict"
        elif task is None:
            clean, reason = None, "unknown_task"
        elif counts[task_id] >= task.get("samples_required", 3):
            clean, reason = None, "samples_complete"
        elif task_id in final:
            clean, reason = None, "task_already_final"
        elif (task_id, judge_id) in seen:
            clean, reason = None, "duplicate_judge"
        else:
            clean, reason = _check(verdict, task)
        if reason:
            rejected.append({"index": index, "task_id": task_id, "reason": reason})
            continue
        record = {"task_id": task_id, "judge_id": judge_id, "model": model, **clean}
        stored.append(record)
        seen.add((task_id, judge_id))
        counts[task_id] += 1
        accepted += 1
        if cache:
            cache_put(task, record, prompt)
    return {"accepted": accepted, "rejected": rejected, **progress(run)}


# ---------------------------------------------------------------- finalisation


def _merge_evidence(verdicts):
    merged, seen = [], set()
    for verdict in verdicts:
        for item in verdict.get("evidence") or []:
            key = (item["snippet_id"], normalize_text(item["quote"]))
            if key not in seen:
                seen.add(key)
                merged.append(dict(item))
    return merged[:5]


def required_samples(task, override=None) -> int:
    """Verdicts a task needs before finalisation; an override may only lower the task's own samples_required."""
    own = task.get("samples_required", 3)
    if override is None:
        return own
    if isinstance(override, bool) or not isinstance(override, int) or override < 1:
        raise ValueError("samples_required must be a positive integer")
    if override > own:
        raise ValueError(f"samples_required {override} exceeds {own} for {task.get('task_id')}: "
                         "create tasks with a larger samples_required")
    return override


def finalize(run, *, samples_required=None) -> dict:
    """Majority label per task with enough verdicts; ties or agreement < 2/3 -> uncertain.

    Each task needs its own samples_required verdicts; samples_required may lower that for every open task (early
    finalisation) but never raise it (ValueError). Finality is terminal: a task finalised by an earlier call keeps its
    stored result (submit() refuses further verdicts for it), whatever samples_required a later call uses, so a final
    task settled by Jev (one sample) never fails a later finalize(samples_required=3).
    """
    state = run.setdefault("judgments", {})
    tasks = state.get("tasks") or []
    existing = {f["task_id"]: f for f in state.get("final") or []}
    required = {t["task_id"]: required_samples(t, samples_required) for t in tasks if t["task_id"] not in existing}
    by_task = {}
    for verdict in state.get("verdicts") or []:
        by_task.setdefault(verdict["task_id"], []).append(verdict)
    finals, pending, uncertain, disagreements = [], [], [], []
    for task in tasks:
        stored = existing.get(task["task_id"])
        if stored is not None:
            finals.append(stored)
            if stored.get("label") is None:
                uncertain.append(task["task_id"])
            if stored.get("agreement", 1) < 1:
                disagreements.append(task["task_id"])
            continue
        verdicts = by_task.get(task["task_id"], [])
        if not verdicts or len(verdicts) < required[task["task_id"]]:
            pending.append(task["task_id"])
            continue
        ranked = Counter(v["label"] for v in verdicts).most_common()
        top_label, top = ranked[0]
        agreement = top / len(verdicts)
        tie = len(ranked) > 1 and ranked[1][1] == top
        label = None if tie or agreement + 1e-9 < MIN_AGREEMENT else top_label
        agreeing = [v for v in verdicts if v["label"] == label] if label else verdicts
        if label is None:
            uncertain.append(task["task_id"])
        if agreement < 1:
            disagreements.append(task["task_id"])
        finals.append(
            {
                "task_id": task["task_id"],
                "kpi_id": task["kpi_id"],
                "label": label,
                "agreement": round(agreement, 4),
                "samples": len(verdicts),
                "confidence": round(statistics.fmean(v["confidence"] for v in agreeing), 4),
                "models": sorted({v["model"] for v in verdicts}),
                "evidence": _merge_evidence(agreeing) if label else [],
            }
        )
    state["final"] = finals
    return {
        "final": len(finals),
        "decided": len(finals) - len(uncertain),
        "uncertain": uncertain,
        "disagreements": disagreements,
        "pending": pending,
    }


def _row(kpi, value, *, source, ref=None, assessed, reason, evidence) -> dict:
    ref = ref or {}
    return {
        "kpi_id": kpi.id,
        "value": value,
        "unit": kpi.unit,
        "source": source,
        "page_id": ref.get("page_id"),
        "profile": ref.get("profile"),
        "stage": ref.get("stage"),
        "assessed": assessed,
        "reason": reason,
        "evidence": evidence,
    }


def _jsonld_types(audit) -> set:
    types, keys = set(), set()
    for item in audit.get("jsonld") or []:
        if isinstance(item, dict):
            kind = item.get("@type")
            types |= {str(t) for t in (kind if isinstance(kind, list) else [kind]) if t}
            keys |= set(item)
    return types | keys


def absence_label(rubric_id: str, audit: dict) -> tuple:
    """(label, basis) for a reached page with no snippet for the rubric; code-owned, no LLM involved."""
    if rubric_id == "social_proof":
        reviews = (audit.get("pdp") or {}).get("reviews") or {}
        if reviews.get("count_text") or reviews.get("rating_text"):
            return "basic", "rating_visible"
        if _jsonld_types(audit) & {"AggregateRating", "Review", "aggregateRating", "review"}:
            return "basic", "rating_jsonld"
    elif rubric_id == "returns_clarity":
        if ((audit.get("trust") or {}).get("policy_links") or {}).get("returns"):
            return "vague", "returns_link_only"
    elif rubric_id == "authority":
        if (audit.get("trust") or {}).get("badges") or (audit.get("persuasion") or {}).get("authority"):
            return "weak", "badge_without_text"
    return "absent", None


def _absence_rows(run, rubric_id, rubric, kpi, covered_pages, unresolved) -> list:
    """Page-level rows for reached pages in the rubric's stages that yielded no task: evidence of absence."""
    stages = rubric.get("stages")
    pages = [p for p in run.get("pages") or [] if not stages or p.get("stage") in stages]
    pages = [p for p in pages if p.get("page_id") not in covered_pages]
    checked = sum(1 for p in pages if p.get("audit"))
    rows = []
    for page in pages:
        ref = {"page_id": page.get("page_id"), "profile": page.get("profile"), "stage": page.get("stage")}
        audit = page.get("audit")
        evidence = {"rubric_id": rubric_id, "reason": "no_snippets"}
        if not audit:
            rows.append(_row(kpi, None, source="deterministic", ref=ref, assessed=False, reason="no_audit",
                             evidence=evidence))
            continue
        if (page.get("classification") or {}).get("type") == "challenge":
            rows.append(_row(kpi, None, source="deterministic", ref=ref, assessed=False, reason="bot_challenge",
                             evidence=evidence))
            continue
        label, basis = absence_label(rubric_id, audit)
        a11y = audit.get("a11y") or {}
        evidence.update(label=label, basis=basis, pages_checked=checked, url=page.get("final_url") or page.get("url"),
                        iframes=a11y.get("iframes") or 0, shadow_roots_closed=a11y.get("shadow_roots_closed") or 0)
        if unresolved:  # a pending or uncertain judgment on another page must not be overridden by absence
            rows.append(_row(kpi, None, source="deterministic", ref=ref, assessed=False, reason=unresolved,
                             evidence=evidence))
        else:
            rows.append(_row(kpi, rubric["values"][label], source="deterministic", ref=ref, assessed=True,
                             reason="no_snippets", evidence=evidence))
    return rows


def observations_from_final(run) -> list:
    """Judged KPI observations: one per KPI per page covered by a task, plus what rubrics without tasks imply.

    Rubrics flagged absent_when_no_snippets (returns, authority, social proof) read a reached page without any
    relevant snippet as evidence of absence: a deterministic, assessed row (a link or rating without text may lift it
    to the rubric's middle label). Other rubrics without tasks stay not assessed ("no_snippets"). Before judgments are
    requested every judged KPI is "not_judged".
    """
    rubrics = load_rubrics()
    state = run.get("judgments") or {}
    tasks = state.get("tasks") or []
    finals = {f["task_id"]: f for f in state.get("final") or []}
    observations, covered, unresolved = [], {}, {}
    for task in tasks:
        rubric = rubrics.get(task["rubric_id"])
        kpi = KPIS.get(task["kpi_id"])
        if rubric is None or kpi is None:
            continue
        final = finals.get(task["task_id"])
        value, assessed, reason = None, False, None
        evidence = {"task_id": task["task_id"], "rubric_id": task["rubric_id"]}
        if final is None:
            reason = "judgment_pending"
        else:
            evidence.update(
                label=final["label"],
                agreement=final["agreement"],
                samples=final["samples"],
                models=final["models"],
                quotes=[q["quote"] for q in final.get("evidence") or []],
            )
            mapped = rubric["values"].get(final["label"]) if final["label"] is not None else None
            if final["label"] is None:
                reason = "judgment_uncertain"
            elif mapped is None:
                reason = "judgment_unclear"
            else:
                value = round(mapped * final["agreement"], 3) if kpi.value_type == "confidence" else mapped
                assessed = True
        if reason == "judgment_pending" or (reason == "judgment_uncertain" and task["rubric_id"] not in unresolved):
            unresolved[task["rubric_id"]] = reason
        pages = task.get("context", {}).get("pages") or [
            {"page_id": task["page_id"], "profile": task.get("profile"), "stage": task.get("context", {}).get("stage")}
        ]
        covered.setdefault(task["rubric_id"], set()).update(ref.get("page_id") for ref in pages)
        for ref in pages:
            observations.append(_row(kpi, value, source="judged", ref=ref, assessed=assessed, reason=reason,
                                     evidence=dict(evidence)))
    ready = prepared(run)
    for rubric_id in sorted(rubrics):
        rubric = rubrics[rubric_id]
        kpi = KPIS[rubric["kpi_id"]]
        rows = []
        if ready and rubric.get("absent_when_no_snippets"):
            rows = _absence_rows(run, rubric_id, rubric, kpi, covered.get(rubric_id, set()),
                                 unresolved.get(rubric_id))
        observations += rows
        if rubric_id not in covered and not rows:
            observations.append(_row(kpi, None, source="judged", assessed=False,
                                     reason="no_snippets" if ready else "not_judged",
                                     evidence={"rubric_id": rubric_id}))
    return observations


# ---------------------------------------------------------------- cache


def cache_dir() -> Path:
    explicit = os.environ.get("JEV_ENGAGEMENT_CACHE")
    return Path(explicit) if explicit else Path.home() / ".cache" / "jev-engagement" / "judgments"


def judge_view(task) -> dict:
    """Everything a judge sees for a task except ids: question, labels, snippet kinds and texts, page context."""
    context = task.get("context") or {}
    return {
        "rubric_id": task["rubric_id"],
        "rubric_version": task["rubric_version"],
        "question": task["question"],
        "labels": task["labels"],
        "no_quote_labels": list(_no_quote_labels(task)),
        "snippets": [{"kind": s.get("kind"), "text": s["text"]} for s in task["snippets"]],
        "context": {k: context.get(k) for k in ("page_type", "stage", "locale")},
    }


def cache_key(view: dict, model: str, prompt=None) -> str:
    """sha256 over the model id, the judge prompt id and the judge-visible task view.

    Any change to the judge input (task, model, instructions or judge backend) misses the cache.
    """
    payload = json.dumps([model, prompt or HOST_PROMPT, view], ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def task_cache_key(task, model: str, prompt=None) -> str:
    return cache_key(judge_view(task), model, prompt)


def _cache_path(task, model, prompt=None) -> Path:
    return cache_dir() / f"{task_cache_key(task, model, prompt)}.json"


def cache_put(task, verdict, prompt=None) -> None:
    """Remember one validated sample (best effort; an unwritable cache never fails a submission)."""
    path = _cache_path(task, verdict["model"], prompt)
    try:
        entry = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        entry = None
    entry = entry or {"rubric_id": task["rubric_id"], "rubric_version": task["rubric_version"],
                      "model": verdict["model"], "prompt": prompt or HOST_PROMPT, "samples": []}
    if any(s.get("judge_id") == verdict["judge_id"] for s in entry["samples"]):
        return
    position = {s["snippet_id"]: i for i, s in enumerate(task["snippets"])}
    entry["samples"].append(
        {
            "judge_id": verdict["judge_id"],
            "label": verdict["label"],
            "confidence": verdict["confidence"],
            "rationale": verdict.get("rationale", ""),
            "evidence": [{"snippet": position[e["snippet_id"]], "quote": e["quote"]} for e in verdict["evidence"]],
        }
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def cached_verdicts(task, model: str, prompt=None) -> list:
    try:
        entry = json.loads(_cache_path(task, model, prompt).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    snippets = task["snippets"]
    verdicts = []
    for sample in entry.get("samples") or []:
        try:
            evidence = [{"snippet_id": snippets[e["snippet"]]["snippet_id"], "quote": e["quote"]}
                        for e in sample.get("evidence") or []]
        except (IndexError, KeyError, TypeError):
            continue
        verdicts.append(
            {
                "task_id": task["task_id"],
                "judge_id": sample.get("judge_id"),
                "model": model,
                "label": sample.get("label"),
                "confidence": sample.get("confidence"),
                "evidence": evidence,
                "rationale": sample.get("rationale", ""),
            }
        )
    return verdicts


def apply_cache(run, model: str, *, judge_ids=None, prompt=None) -> dict:
    """Submit cached samples (same model and judge prompt) for tasks that are not final yet; returns the reuse count."""
    state = run.get("judgments") or {}
    final = {f["task_id"] for f in state.get("final") or []}
    by_judge = {}
    for task in state.get("tasks") or []:
        if task["task_id"] in final:
            continue
        for verdict in cached_verdicts(task, model, prompt):
            if judge_ids is None or verdict["judge_id"] in judge_ids:
                by_judge.setdefault(verdict["judge_id"], []).append(verdict)
    reused = 0
    for judge_id in sorted(j for j in by_judge if isinstance(j, str) and j):
        reused += submit(run, judge_id, model, by_judge[judge_id], cache=False, prompt=prompt)["accepted"]
    return {"reused": reused, **progress(run)}
