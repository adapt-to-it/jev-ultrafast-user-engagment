"""Predicted-friction KPIs of one agent journey, derived from its recorded steps (steps.jsonl) only.

Pure functions: the journey runner records raw facts per step (what was executed, what changed, the page-side
clock), and this module turns them into Observations for every KPI whose producer is "friction". A KPI that cannot
be computed is reported with assessed=False and a reason; one that does not exist for this journey (no clicks, no
known minimum path, a failed goal for goal-time KPIs) has a reason starting with "not_applicable", which keeps it out
of coverage. The agent's DONE is never evidence: success comes from the independent oracle (`success`).

Definitions (docs/engagement-kpi.md, sections 4.1, 4.2 and 5.3):
- executed steps: CLICK, TYPE_TEXT, SELECT, SCROLL_UP, SCROLL_DOWN and WAIT steps of the pilot that reached the
  browser (stale decisions and guard refusals are logged but never executed); interactions: CLICK, TYPE_TEXT, SELECT.
  The consent step's clicks before the first decision (records with source "consent", no step number) are no pilot's:
  no KPI here counts them. A tab the consent step opened is closed in that step (PageRecord.consent.closed_tabs),
  never charged to step 1 as new_tab. A reload the page starts after the consent step's settle is a NAVIGATION record
  like any navigation between steps; a consent step that left the tab on another page is no record here but
  PageRecord.consent.url_after with a consent_moved warning (journey.py, consent step): the pilot's steps then start
  from that page. One that left it on another site ends the journey before any pilot step: FAI.JOURNEY_SUCCESS is then
  not assessed (unverified "consent_left_shop", from JourneyRunner.finish()), never a failure of the shop.
- effect of a step: visible feedback, i.e. a mutation (self-updating tickers excluded) or a navigation since it
  (vitals.js since()), a tab it opened, another site or a page that failed to load, a layout shift the input caused
  (hadRecentInput), visible text that changed while nothing at all mutated (CSS-only feedback such as a :focus-within
  menu: no ticker can explain it), or a change of what an action can change (flags.state_changed: document, URL,
  scroll, viewport, field values; not the visible text, which a countdown changes by itself). A request is not
  feedback, a tracking beacon included: requests are evidence only. Without page-side counts the marker comparison
  (page_changed) decides; with neither the step is unmeasured. Known misses: a click whose only effect is a download
  or a focus change reads as dead; a CSS animation that changes the visible text hides a dead click (under-reported,
  never invented).
- measured interaction: one with page-side counts (since() read and available). The PERF KPIs use measured
  interactions only; interactions without any measurement give "no_measurement" (assessed=False, in coverage). A
  step whose record still carries flags.unobserved (its execution line: the process stopped before the step was
  measured) is an executed, unmeasured interaction.
- dead click: a CLICK on a button or link (or a step recorded without a role) without any effect. The rate counts
  measured clicks only.
- rage event: three or more consecutive interactions on the same node, none of which had an effect (WAIT steps in
  between neither count nor break the run); one event per run of such steps. With policy "typesafe" the Agent stops
  after three actions in a row that changed nothing, so the count is truncated there (the run's warning says so);
  the host policy is told and goes on.
- unexpected navigation: a step that opened a tab, or one that was not a chosen CLICK or SELECT (typing, scrolling,
  waiting) after which a new document loaded or another site showed, or a page that navigated by itself between
  steps (a NAVIGATION record: a redirect, a refresh or a script while the policy decided); a chosen click to another
  site is evidence only (it ends the journey, and the oracle accounts for it).
- page visits: the web URLs (fragment dropped, query sorted; not Chrome's error page) before and after each executed
  step and each NAVIGATION record, consecutive repeats collapsed; S = visits, N = distinct pages. Backtrack rate =
  (S - N) / S. Lostness (Smith 1996) = sqrt((N/S - 1)^2 + (R/N - 1)^2) with R the minimum number of distinct pages,
  start page included; only for a successful journey (a failed one is not a path to the goal), R capped at N (a
  shorter path than the declared minimum is not lost) and the result capped at 1.
- time on task (site): execution plus settle time of every executed step; decision latency (host or model), text
  generation and the harness's WAIT pause are excluded.
- post-input shifts: the sum of hadRecentInput layout shifts after the agent's interactions; action response: the
  median first response (first_response_ms) of in-document interactions that had a visible response (a mutation or
  a navigation; a new document's response is its FCP, measured on its own). Interactions without one are the
  dead-click KPIs' evidence, not a latency. vitals.js since() still counts request starts in first_response_ms
  (until it reports them apart as first_request_ms), so a step that requested and mutated reports the earlier of the
  two (evidence "request_inclusive"). The clock starts after the harness's freshness check (a snapshot.js read, not
  site time), right before the input is dispatched: a click's dispatch is a few ms, a TYPE_TEXT's (field click,
  select-all, insertText) a few tens.
- interaction latency (INP_SYNTH): the longest Event Timing duration of in-document clicks and typing, never below
  the 16 ms durationThreshold (shorter entries come only from vitals.js's first-input observer; an interaction
  without an entry lasted under 16 ms): 16 ms is then an upper bound.
"""

import math
import statistics
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .kpis import KPIS, by_producer
from .schemas import JourneyStep, Observation

INTERACTIONS = ("CLICK", "TYPE_TEXT", "SELECT")
EXECUTED = (*INTERACTIONS, "SCROLL_UP", "SCROLL_DOWN", "WAIT")
TRUSTED_INPUT = ("CLICK", "TYPE_TEXT")  # dispatched as trusted input events; SELECT sets the value from script
DEAD_ROLES = ("button", "link")
RAGE_RUN = 3
EVENT_TIMING_THRESHOLD_MS = 16  # vitals.js observes Event Timing with durationThreshold 16
NAVIGATION = "NAVIGATION"  # a record of a page that navigated by itself between steps (executed nothing)


def normalize_url(url: str | None) -> str | None:
    """The page a URL shows: scheme and host lowercased, fragment dropped, query keys sorted."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, ""))


def page_url(url: str | None) -> str | None:
    """normalize_url() of a web page (http, https); None for anything else: Chrome's error page (chrome-error://) or
    about:blank is no page of the shop."""
    try:
        web = urlsplit(url or "").scheme.lower() in ("http", "https")
    except ValueError:
        return None
    return normalize_url(url) if web else None


def pilot(step: dict) -> bool:
    """A record of the journey's pilot. A record with a source key is a click the harness made on its own account:
    the consent step before the first decision ("consent"), like the audit's crawler and deception clicks."""
    return "source" not in step


def executed(step: dict) -> bool:
    flags = step.get("flags") or {}
    return (pilot(step) and step.get("operation") in EXECUTED and not flags.get("stale")
            and not flags.get("guard_blocked"))


def interaction(step: dict) -> bool:
    return executed(step) and step.get("operation") in INTERACTIONS


def between_steps(step: dict) -> bool:
    """A navigation the journey did not make, seen between its steps (journey.py logs it as a NAVIGATION record)."""
    return step.get("operation") == NAVIGATION


def countable_click(step: dict) -> bool:
    """An executed CLICK on a button or a link; a step recorded without a role key counts (a recorded None does not:
    snapshot.js gives clickable controls such as date or range inputs no role)."""
    return (interaction(step) and step.get("operation") == "CLICK"
            and ("role" not in step or step["role"] in DEAD_ROLES))


def measured(step: dict) -> bool:
    """Page-side counts were read after the step (vitals.js since() ran and was available)."""
    since = step.get("since") or {}
    return bool(since) and since.get("available", True) is not False


def effect(step: dict) -> bool | None:
    """Did the step have visible feedback (see the module docstring)? None when it could not be measured."""
    since, flags = step.get("since") or {}, step.get("flags") or {}
    if flags.get("new_tab") or flags.get("external_nav") or flags.get("navigation_error"):
        return True
    counted = measured(step)
    if counted and (since.get("mutations") or since.get("navigations")):  # a request alone is not feedback
        return True
    if counted and (since.get("shifts_post_input") or 0) > 0:  # movement the input caused (hadRecentInput)
        return True
    if counted and step.get("page_changed") is True and since.get("mutations_total") == 0:
        return True  # the visible text changed and nothing mutated at all (CSS-only feedback): no ticker explains it
    if counted and flags.get("state_changed") is not None:
        return bool(flags["state_changed"])
    changed = step.get("page_changed")  # no page-side counts, or a step recorded before flags.state_changed
    return None if changed is None else bool(changed)


def is_dead_click(step: dict) -> bool:
    """A click on a button or link without any visible effect (effect() is False); a request alone does not count."""
    return countable_click(step) and effect(step) is False


def rage_flags(steps: list[dict]) -> list[bool]:
    """Per step: True when it is the third (or later) consecutive interaction without effect on the same node."""
    flags, node, streak = [], None, 0
    for step in steps:
        if not executed(step) or step.get("operation") == "WAIT":
            flags.append(False)
            continue
        same = interaction(step) and step.get("node") is not None and effect(step) is False
        if same and step.get("node") == node:
            streak += 1
        elif same:
            node, streak = step.get("node"), 1
        else:
            node, streak = None, 0
        flags.append(streak >= RAGE_RUN)
    return flags


def rage_events(steps: list[dict]) -> int:
    """Runs of RAGE_RUN or more consecutive interactions without effect on one node: one event per run."""
    events, previous = 0, False
    for step, flag in zip(steps, rage_flags(steps)):
        if not executed(step) or step.get("operation") == "WAIT":
            continue
        events += flag and not previous
        previous = flag
    return events


def visits(steps: list[dict]) -> list[str]:
    """Page visits in order: the URL before and after every executed step and every navigation between steps,
    consecutive repeats collapsed; only web pages (a page that failed to load is not one of the shop)."""
    sequence: list[str] = []
    for step in steps:
        if not executed(step) and not between_steps(step):
            continue
        for url in (step.get("url_before"), step.get("url_after")):
            page = page_url(url)
            if page and (not sequence or sequence[-1] != page):
                sequence.append(page)
    return sequence


def lostness(unique_pages: int, total_visits: int, optimal: int) -> float:
    """Smith (1996): L = sqrt((N/S - 1)^2 + (R/N - 1)^2), N distinct pages, S visits, R minimum pages.

    0 is a perfect path; Smith reads L < 0.4 as not lost and L > 0.5 as lost.
    """
    n, s, r = unique_pages, total_visits, optimal
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (n, s, r)) or n < 1 or s < n or r < 1:
        raise ValueError("lostness needs 1 <= unique_pages <= total_visits and optimal >= 1")
    return math.sqrt((n / s - 1) ** 2 + (r / n - 1) ** 2)


def _row(kpi_id: str, value=None, *, profile=None, reason=None, evidence=None, assessed=None) -> Observation:
    if assessed is None:
        assessed = value is not None
    return {"kpi_id": kpi_id, "value": value if assessed else None, "unit": KPIS[kpi_id].unit,
            "source": "deterministic", "page_id": None, "profile": profile, "stage": None, "assessed": assessed,
            "reason": reason, "evidence": evidence or {}}


def _visible_response(step: dict) -> float | None:
    """first_response_ms of a step that had a visible response (a mutation or a navigation), else None: a request
    alone is no response, whatever vitals.js put into first_response_ms."""
    since = step.get("since") or {}
    value = since.get("first_response_ms")
    if value is None or not (since.get("mutations") or since.get("navigations")):
        return None
    return float(value)


def _request_inclusive(step: dict) -> bool:
    """vitals.js without first_request_ms counts request starts in first_response_ms: with a request, the value may
    be the request's start rather than the visible change."""
    since = step.get("since") or {}
    return "first_request_ms" not in since and bool(since.get("requests"))


def _new_document(step: dict) -> bool:
    flags = step.get("flags") or {}
    if "new_document" in flags:
        return bool(flags["new_document"])
    return bool((step.get("since") or {}).get("navigations")) and step.get("url_after") != step.get("url_before")


def unexpected_nav(step: dict) -> bool:
    """A tab the step opened, or a new document or another site after a step that was not a chosen CLICK or SELECT
    (TYPE_TEXT never submits: a navigation after typing, scrolling or waiting is the page's doing), or a navigation
    between steps. One seen right after a step whose settle timed out (flags.follows_timeout) may be that step's late
    navigation: a visit, not counted."""
    flags = step.get("flags") or {}
    if between_steps(step) and flags.get("follows_timeout"):
        return False
    return bool(flags.get("new_tab")) or (step.get("operation") not in ("CLICK", "SELECT") and (
        _new_document(step) or bool(flags.get("external_nav"))))


def metrics(steps: list[JourneyStep], *, optimal_steps: int | None = None, optimal_pages: int | None = None,
            success: bool | None = None, profile: str | None = None,
            unverified: str = "no_verification") -> list[Observation]:
    """One Observation per friction KPI (kpis.by_producer("friction")), in registry order.

    success: the oracle's verdict (None: not verified, with `unverified` as the reason, e.g. "bot_challenge").
    """
    steps = [s for s in steps if isinstance(s, dict)]
    done = [s for s in steps if executed(s)]
    acts = [s for s in done if s.get("operation") in INTERACTIONS]
    seen = [s for s in acts if measured(s)]  # interactions with page-side counts
    unmeasured = len(acts) - len(seen)
    row = {}
    none_executed = "not_applicable: nessuna azione eseguita"
    none_interacted = "not_applicable: nessuna interazione con elementi della pagina"
    no_measurement = "no_measurement: le interazioni sono state eseguite ma il loro effetto non è stato misurato"
    blind = {"interactions": len(seen), "unmeasured": unmeasured} if acts else None

    # PERF: what the site did right after the agent's input
    shifts = [float((s.get("since") or {}).get("shifts_post_input") or 0.0) for s in seen]
    row["PERF.CLS_POST_INPUT"] = (
        _row("PERF.CLS_POST_INPUT", round(sum(shifts), 4), profile=profile,
             evidence={"interactions": len(shifts), "max_per_action": round(max(shifts), 4),
                       "unmeasured": unmeasured})
        if shifts else _row("PERF.CLS_POST_INPUT", profile=profile, reason=no_measurement if acts else none_interacted,
                            evidence=blind))
    timed = [s for s in seen if s.get("operation") in TRUSTED_INPUT and not _new_document(s)]
    durations = [float(v) for s in timed if (v := (s.get("since") or {}).get("event_timing_max_ms")) is not None]
    if timed:
        # Event Timing reports interactions of at least 16 ms (only vitals.js's first-input entry can be shorter): an
        # interaction without an entry, or with a shorter one, lasted under 16 ms, so 16 ms bounds it
        longest = max(durations, default=0.0)
        below = longest < EVENT_TIMING_THRESHOLD_MS
        evidence = {"interactions": len(timed), "timed": len(durations), "unmeasured": unmeasured}
        row["PERF.INP_SYNTH"] = _row(
            "PERF.INP_SYNTH", round(max(longest, EVENT_TIMING_THRESHOLD_MS), 1), profile=profile,
            reason="limite superiore: nessuna interazione ha raggiunto i 16 ms della soglia Event Timing" if below
            else None, evidence={**evidence, "below_threshold": True} if below else evidence)
    else:
        row["PERF.INP_SYNTH"] = _row("PERF.INP_SYNTH", profile=profile, evidence=blind, reason=(
            "not_applicable: nessuna interazione misurabile con Event Timing nella stessa pagina" if seen
            else no_measurement if acts else none_interacted))
    # in-document feedback only: a new document's visible response is its FCP (PERF.FCP), not its first request
    in_document = [s for s in seen if not _new_document(s)]
    answered = [s for s in in_document if _visible_response(s) is not None]
    responses = [_visible_response(s) for s in answered]
    if responses:
        row["PERF.ACTION_RESPONSE_MS"] = _row(
            "PERF.ACTION_RESPONSE_MS", round(statistics.median(responses), 1), profile=profile,
            evidence={"interactions": len(in_document), "responded": len(responses),
                      "request_inclusive": sum(1 for s in answered if _request_inclusive(s)),
                      "new_documents_excluded": len(seen) - len(in_document), "unmeasured": unmeasured})
    else:  # no latency without a response: the missing feedback is FAI.DEAD_CLICK_RATE's, never a worst value here
        reason = ("not_applicable: nessuna interazione nella stessa pagina ha ricevuto una risposta visibile "
                  "(vedi FAI.DEAD_CLICK_RATE)" if in_document else
                  "not_applicable: nessuna interazione nella stessa pagina (le navigazioni sono misurate da FCP e LCP)"
                  if seen else no_measurement if acts else none_interacted)
        row["PERF.ACTION_RESPONSE_MS"] = _row(
            "PERF.ACTION_RESPONSE_MS", profile=profile, reason=reason,
            evidence={"interactions": len(in_document), "responded": 0, "unmeasured": unmeasured} if acts else None)

    # FAI: outcome, effort and disorientation
    row["FAI.JOURNEY_SUCCESS"] = (
        _row("FAI.JOURNEY_SUCCESS", bool(success), profile=profile, evidence={"source": "oracle"})
        if isinstance(success, bool) else _row("FAI.JOURNEY_SUCCESS", profile=profile, reason=unverified))
    goal_reason = (None if success is True else unverified if success is None
                   else "not_applicable: obiettivo non raggiunto secondo l'oracolo (vedi FAI.JOURNEY_SUCCESS)")
    counts = {op: sum(1 for s in done if s.get("operation") == op) for op in EXECUTED}
    if goal_reason:
        for kpi_id in ("FAI.ACTIONS_TO_GOAL", "FAI.ACTIONS_RATIO", "FAI.TIME_ON_TASK_SITE"):
            row[kpi_id] = _row(kpi_id, profile=profile, reason=goal_reason)
    else:
        row["FAI.ACTIONS_TO_GOAL"] = _row("FAI.ACTIONS_TO_GOAL", len(acts), profile=profile, evidence=counts)
        if isinstance(optimal_steps, int) and not isinstance(optimal_steps, bool) and optimal_steps >= 1:
            row["FAI.ACTIONS_RATIO"] = _row("FAI.ACTIONS_RATIO", round(len(acts) / optimal_steps, 2),
                                            profile=profile, evidence={"actions": len(acts), "optimal": optimal_steps})
        else:
            row["FAI.ACTIONS_RATIO"] = _row("FAI.ACTIONS_RATIO", profile=profile,
                                            reason="not_applicable: percorso minimo non noto")
        site = [float(s.get("execution_ms") or 0.0) + float(s.get("settle_ms") or 0.0) for s in done]
        excluded = sum(float(s.get("decision_latency_ms") or 0.0) for s in steps)
        row["FAI.TIME_ON_TASK_SITE"] = _row(
            "FAI.TIME_ON_TASK_SITE", round(sum(site)), profile=profile,
            evidence={"steps": len(done), "decision_ms_excluded": round(excluded),
                      "unsettled_steps": sum(1 for s in done if s.get("settle_ms") is None),
                      "settle_timeouts": sum(1 for s in done if s.get("settle_reason") == "timeout")})

    every_click = [s for s in acts if countable_click(s)]
    clicks = [s for s in every_click if effect(s) is not None]
    dead = sum(1 for s in clicks if is_dead_click(s))
    if clicks:
        row["FAI.DEAD_CLICK_RATE"] = _row("FAI.DEAD_CLICK_RATE", round(100.0 * dead / len(clicks), 1),
                                          profile=profile, evidence={"dead": dead, "clicks": len(clicks),
                                                                     "unmeasured": len(every_click) - len(clicks)})
    else:
        row["FAI.DEAD_CLICK_RATE"] = _row("FAI.DEAD_CLICK_RATE", profile=profile, reason=(
            "no_measurement: l'effetto dei click non è stato misurato" if every_click
            else "not_applicable: nessun click su pulsanti o link"))
    judged = [s for s in acts if effect(s) is not None]
    row["FAI.RAGE_EVENTS"] = (
        _row("FAI.RAGE_EVENTS", rage_events(steps), profile=profile,
             evidence={"interactions": len(acts), "unmeasured": len(acts) - len(judged)}) if judged
        else _row("FAI.RAGE_EVENTS", profile=profile, reason=no_measurement if acts else none_interacted))
    pages = visits(steps)
    total, unique = len(pages), len(set(pages))
    if total:
        row["FAI.BACKTRACK_RATE"] = _row("FAI.BACKTRACK_RATE", round(100.0 * (total - unique) / total, 1),
                                         profile=profile, evidence={"visits": total, "unique_pages": unique})
    else:
        row["FAI.BACKTRACK_RATE"] = _row("FAI.BACKTRACK_RATE", profile=profile, reason=none_executed)
    if goal_reason:  # a failed path is not a path to the goal: (R/N - 1)^2 would be unbounded on it
        row["FAI.LOSTNESS"] = _row("FAI.LOSTNESS", profile=profile, reason=goal_reason)
    elif not total:
        row["FAI.LOSTNESS"] = _row("FAI.LOSTNESS", profile=profile, reason=none_executed)
    elif isinstance(optimal_pages, int) and not isinstance(optimal_pages, bool) and optimal_pages >= 1:
        optimal = min(optimal_pages, unique)  # reaching the goal on fewer pages shows the declared minimum is not one
        value = lostness(unique, total, optimal)
        evidence = {"unique_pages": unique, "visits": total, "optimal": optimal}
        if optimal != optimal_pages:
            evidence["optimal_declared"] = optimal_pages
        if value > 1:
            evidence["unclamped"] = round(value, 3)
        row["FAI.LOSTNESS"] = _row("FAI.LOSTNESS", round(min(value, 1.0), 3), profile=profile, evidence=evidence)
    else:
        row["FAI.LOSTNESS"] = _row("FAI.LOSTNESS", profile=profile,
                                   reason="not_applicable: numero minimo di pagine non noto")
    moved = [s for s in steps if between_steps(s)]
    unexpected = [s for s in [*done, *moved] if unexpected_nav(s)]
    tabs = sum(1 for s in unexpected if (s.get("flags") or {}).get("new_tab"))
    chosen = sum(1 for s in done if s.get("operation") in ("CLICK", "SELECT")
                 and (s.get("flags") or {}).get("external_nav") and not unexpected_nav(s))
    row["FAI.UNEXPECTED_NAV"] = (
        _row("FAI.UNEXPECTED_NAV", len(unexpected), profile=profile,
             evidence={"new_tabs": tabs, "spontaneous": len(unexpected) - tabs,
                       "between_steps": sum(1 for s in moved if unexpected_nav(s)), "chosen_external": chosen})
        if done or moved else _row("FAI.UNEXPECTED_NAV", profile=profile, reason=none_executed))
    return [row[kpi.id] for kpi in by_producer("friction")]
