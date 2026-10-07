"""Friction KPIs from recorded journey steps: pure functions, synthetic and golden steps, no browser."""

import json
import math
from pathlib import Path

import pytest

from jev_ultrafast.engagement import friction
from jev_ultrafast.engagement.kpis import KPIS, by_producer
from jev_ultrafast.engagement.scoring import score

RUNS = Path(__file__).with_name("fixtures") / "runs"
SHOP = "https://shop.example"


def step(operation="CLICK", *, before="/", after=None, changed=True, node=1, role="link", settle=100.0,
         execution=10.0, decision=2000.0, flags=None, **since):
    base = {"first_response_ms": 50, "mutations": 3, "navigations": 0, "requests": 1, "errors": 0,
            "shifts_post_input": 0.0, "event_timing_max_ms": None}
    return {"operation": operation, "node": node if operation in friction.INTERACTIONS else None, "role": role,
            "kind": {"CLICK": "click", "TYPE_TEXT": "fill", "SELECT": "select"}.get(operation),
            "url_before": SHOP + before, "url_after": SHOP + (after or before), "page_changed": changed,
            "since": {**base, **since}, "settle_ms": settle, "execution_ms": execution,
            "decision_latency_ms": decision, "flags": dict(flags or {})}


def dead(changed=False, **kw):
    return step(changed=changed, **{"first_response_ms": None, "mutations": 0, "requests": 0, "navigations": 0, **kw})


def rows(steps, **kw):
    return {o["kpi_id"]: o for o in friction.metrics(steps, **kw)}


# ---------------------------------------------------------------- Lostness (Smith 1996)

@pytest.mark.parametrize("unique, visits, optimal, expected", [
    (3, 3, 3, 0.0),  # the minimum path, every page once: not lost at all
    (4, 6, 3, math.sqrt((4 / 6 - 1) ** 2 + (3 / 4 - 1) ** 2)),  # = 0.417: between 0.4 and 0.5, undetermined
    (7, 10, 5, math.sqrt((7 / 10 - 1) ** 2 + (5 / 7 - 1) ** 2)),  # = 0.414
    (5, 10, 3, math.sqrt((5 / 10 - 1) ** 2 + (3 / 5 - 1) ** 2)),  # = 0.640: lost (> 0.5)
    (4, 4, 4, 0.0),
    (6, 6, 3, 0.5),  # no revisit, but twice the pages needed: R/N - 1 = -0.5
])
def test_lostness_follows_smiths_formula_on_worked_examples(unique, visits, optimal, expected):
    assert friction.lostness(unique, visits, optimal) == pytest.approx(expected)


def test_lostness_thresholds_read_as_smith_does():
    assert friction.lostness(4, 5, 4) < 0.4  # one revisit on the minimum path: not lost
    assert friction.lostness(8, 16, 3) > 0.5  # half the visits are revisits, far beyond the minimum: lost


@pytest.mark.parametrize("args", [(0, 3, 3), (4, 3, 3), (3, 3, 0), (3.0, 3, 3), (True, 3, 3)])
def test_lostness_rejects_impossible_counts(args):
    with pytest.raises(ValueError):
        friction.lostness(*args)


def test_lostness_is_only_for_successful_journeys_and_stays_within_0_and_1():
    stuck = [dead(), dead(), dead()]  # three clicks on one dead button of the start page: S = N = 1
    for success, reason in ((False, "not_applicable"), (None, "no_verification")):
        row = rows(stuck, optimal_pages=3, success=success)["FAI.LOSTNESS"]  # (3/1 - 1)^2 would give 2.0
        assert not row["assessed"] and row["reason"].startswith(reason)
    detour = [step(before="/", after="/a"), step(before="/a", after="/")]
    assert not rows(detour, optimal_pages=4, success=False)["FAI.LOSTNESS"]["assessed"]
    # A shortcut: the goal on 2 pages where 4 were declared the minimum. Not lost; the declared R is not a minimum.
    shortcut = rows([step(before="/", after="/cart")], optimal_pages=4, success=True)["FAI.LOSTNESS"]
    assert shortcut["value"] == 0.0 and shortcut["evidence"] == {"unique_pages": 2, "visits": 2, "optimal": 2,
                                                                 "optimal_declared": 4}
    # Ten pages over a hundred visits, a one-page minimum: Smith's formula gives 1.273, the unit stops at 1.
    looping = [step(before=f"/p{i % 10}", after=f"/p{(i + 1) % 10}") for i in range(99)]
    row = rows(looping, optimal_pages=1, success=True)["FAI.LOSTNESS"]
    assert friction.lostness(10, 100, 1) == pytest.approx(1.273, abs=0.001)
    assert row["value"] == 1.0 and row["evidence"]["unclamped"] == pytest.approx(1.273, abs=0.001)


def test_lostness_counts_distinct_pages_and_visits_from_the_steps():
    steps = [step(before="/", after="/plp"), step(before="/plp", after="/pdp?id=1"),
             step(before="/pdp?id=1", after="/plp"), step(before="/plp", after="/pdp?id=2"),
             step(before="/pdp?id=2", after="/cart#top")]
    out = rows(steps, optimal_pages=4, optimal_steps=4, success=True)
    # visits: / plp pdp1 plp pdp2 cart = 6, distinct 5, minimum 4
    assert out["FAI.LOSTNESS"]["value"] == pytest.approx(round(friction.lostness(5, 6, 4), 3))
    assert out["FAI.LOSTNESS"]["evidence"] == {"unique_pages": 5, "visits": 6, "optimal": 4}
    assert out["FAI.BACKTRACK_RATE"]["value"] == pytest.approx(100 * 1 / 6, abs=0.1)


def test_lostness_and_ratio_are_not_applicable_without_a_known_minimum():
    out = rows([step(after="/plp")], success=True)
    for kpi_id in ("FAI.LOSTNESS", "FAI.ACTIONS_RATIO"):
        assert not out[kpi_id]["assessed"] and out[kpi_id]["reason"].startswith("not_applicable")


# ---------------------------------------------------------------- dead clicks, rage, backtrack

def test_dead_clicks_count_button_and_link_clicks_without_any_effect():
    unrolled = dead(node=8)
    del unrolled["role"]  # a step recorded without a role key counts
    steps = [
        dead(role="button"),  # dead
        dead(role="link", node=2),  # dead
        step(role="button", node=3, changed=False, mutations=2),  # a toast answered: alive
        dead(role="checkbox", node=4),  # not a button or link: outside the rate
        step(role="button", node=5, changed=False, mutations=0, requests=1),  # only a request (a beacon): dead
        dead(role="button", node=6, flags={"stale": True}),  # never executed
        dead(role="button", node=7, flags={"guard_blocked": True}),
        dead(role=None, node=9),  # a clickable control without a role (a date input): outside the rate
        unrolled,
    ]
    out = rows(steps)
    assert out["FAI.DEAD_CLICK_RATE"]["value"] == 80.0
    assert out["FAI.DEAD_CLICK_RATE"]["evidence"] == {"dead": 4, "clicks": 5, "unmeasured": 0}
    assert [friction.is_dead_click(s) for s in steps] == [True, True, False, False, True, False, False, False, True]


def test_a_request_is_not_visible_feedback():
    beacon = dead(requests=2, first_request_ms=12)  # a click-tracking beacon and nothing else
    assert friction.effect(beacon) is False and friction.is_dead_click(beacon)
    assert friction.effect(dead(navigations=1, first_response_ms=30)) is True  # a pushState is a visible change
    assert friction.rage_events([dead(node=9, requests=1) for _ in range(3)]) == 1


def test_a_request_is_no_response_latency_with_todays_vitals():
    """vitals.js since() today puts request starts into first_response_ms: a beacon-only click reports 5 ms. It is a
    dead click, so it has no response latency; the live click's 400 ms is the KPI."""
    beacon = dead(requests=1, first_response_ms=5)
    assert friction.is_dead_click(beacon)
    alone = rows([beacon])
    assert alone["FAI.DEAD_CLICK_RATE"]["value"] == 100.0
    assert not alone["PERF.ACTION_RESPONSE_MS"]["assessed"]
    assert alone["PERF.ACTION_RESPONSE_MS"]["reason"].startswith("not_applicable: nessuna interazione nella stessa "
                                                                 "pagina ha ricevuto una risposta visibile")
    out = rows([beacon, step(node=2, mutations=2, requests=0, first_response_ms=400)])["PERF.ACTION_RESPONSE_MS"]
    assert out["value"] == 400.0 and out["evidence"]["responded"] == 1 and out["evidence"]["request_inclusive"] == 0
    # a step that requested and mutated: the earlier of the two until vitals.js reports first_request_ms apart
    both = rows([step(requests=1, mutations=3, first_response_ms=5)])["PERF.ACTION_RESPONSE_MS"]
    assert both["value"] == 5.0 and both["evidence"]["request_inclusive"] == 1
    later = rows([step(requests=1, mutations=3, first_response_ms=800, first_request_ms=5)])["PERF.ACTION_RESPONSE_MS"]
    assert later["value"] == 800.0 and later["evidence"]["request_inclusive"] == 0


def test_effect_is_what_an_action_can_change_not_text_that_changes_by_itself():
    ticker = {"state_changed": False}  # a countdown changed the visible text (page_changed), nothing else changed
    assert friction.is_dead_click(dead(changed=True, flags=ticker, mutations_total=4))
    assert friction.is_dead_click(dead(changed=True, flags=ticker))  # no total recorded: the text proves nothing
    scrolled = dead(changed=True, flags={"state_changed": True})  # a back-to-top link: scroll only
    assert not friction.is_dead_click(scrolled) and friction.effect(scrolled) is True
    assert friction.effect(dead(changed=True, mutations=1, flags=ticker)) is True
    assert friction.effect(dead(changed=True, flags={"state_changed": False, "new_tab": True})) is True
    # Without page-side counts the marker comparison decides; with neither the click is unmeasured.
    blind = {"available": False}
    assert friction.effect(dead(changed=False, **blind)) is False
    assert friction.effect(dead(changed=None, **blind)) is None
    assert friction.effect(dead(changed=True, flags={"state_changed": None})) is True  # older steps: page_changed
    rage = [dead(node=4, changed=True, flags=ticker, mutations_total=2) for _ in range(3)]
    assert friction.rage_events(rage) == 1


def test_css_only_feedback_is_an_effect():
    """A :focus-within menu opens without any DOM mutation: the click moved the layout (a shift with recent input), or
    the visible text changed while nothing at all mutated, which no ticker can explain."""
    menu = {"state_changed": False}
    shifted = dead(changed=True, flags=menu, shifts_post_input=0.0521, mutations_total=0)
    assert friction.effect(shifted) is True and not friction.is_dead_click(shifted)
    assert friction.effect(dead(changed=False, flags=menu, shifts_post_input=0.01, mutations_total=3)) is True
    revealed = dead(changed=True, flags=menu, mutations_total=0)
    assert friction.effect(revealed) is True and not friction.is_dead_click(revealed)
    # a ticker mutated meanwhile: the changed text is the ticker's, the click stays dead
    assert friction.is_dead_click(dead(changed=True, flags=menu, mutations_total=6))
    out = rows([shifted, revealed, dead(node=3, flags=menu, mutations_total=0)])
    assert out["FAI.DEAD_CLICK_RATE"]["value"] == pytest.approx(33.3) and out["FAI.RAGE_EVENTS"]["value"] == 0


def test_dead_click_rate_counts_measured_clicks_only():
    out = rows([dead(), dead(node=2, changed=None, available=False)])["FAI.DEAD_CLICK_RATE"]
    assert out["value"] == 100.0 and out["evidence"] == {"dead": 1, "clicks": 1, "unmeasured": 1}
    out = rows([dead(changed=None, available=False)])["FAI.DEAD_CLICK_RATE"]
    assert not out["assessed"] and out["reason"].startswith("no_measurement")


def test_a_journey_without_clicks_has_no_dead_click_rate():
    out = rows([step("SCROLL_DOWN", role=None), step("TYPE_TEXT", role="searchbox")])
    assert out["FAI.DEAD_CLICK_RATE"]["reason"].startswith("not_applicable")


def test_rage_needs_three_unchanged_actions_on_the_same_node():
    same = [dead(node=9), dead(node=9), dead(node=9)]
    assert friction.rage_events(same) == 1
    assert friction.rage_flags(same) == [False, False, True]
    assert friction.rage_events([*same, dead(node=9), dead(node=9)]) == 1  # one run, one event
    assert friction.rage_events([dead(node=9), dead(node=9), dead(node=8)]) == 0
    waits = [dead(node=9), step("WAIT", role=None), dead(node=9), step("WAIT", role=None), dead(node=9)]
    assert friction.rage_events(waits) == 1  # waiting in between does not break the run
    changed = [dead(node=9), step(node=9, changed=True), dead(node=9)]
    assert friction.rage_events(changed) == 0
    assert friction.rage_events([*same, step("SCROLL_DOWN", role=None), *same]) == 2


def test_backtrack_rate_is_revisits_over_visits():
    steps = [step(before="/", after="/a"), step(before="/a", after="/"), step(before="/", after="/b")]
    out = rows(steps)
    assert out["FAI.BACKTRACK_RATE"]["value"] == 25.0  # visits / a / b: S = 4, N = 3
    assert friction.visits(steps) == [f"{SHOP}/", f"{SHOP}/a", f"{SHOP}/", f"{SHOP}/b"]


def test_urls_differing_only_by_fragment_or_query_order_are_one_page():
    assert friction.normalize_url("HTTPS://Shop.Example/p?b=2&a=1#reviews") == "https://shop.example/p?a=1&b=2"


def test_a_page_that_did_not_load_is_no_visit_and_no_dead_click():
    """Chrome's error page is not a page of the shop: no visit, no backtrack; the click that led there had an effect,
    and a navigation_error leaves the goal KPIs not assessed with that reason, the process KPIs assessed."""
    broken = {**step(before="/a"), "url_after": "chrome-error://chromewebdata/",
              "flags": {"navigation_error": True}, **{"since": {"mutations": 0, "navigations": 0, "requests": 1}}}
    steps = [step(before="/", after="/a"), broken]
    assert friction.visits(steps) == [f"{SHOP}/", f"{SHOP}/a"]
    assert friction.page_url("chrome-error://chromewebdata/") is None and friction.page_url("about:blank") is None
    assert friction.effect(broken) is True and not friction.is_dead_click(broken)
    out = rows(steps, success=None, unverified="navigation_error")
    assert out["FAI.BACKTRACK_RATE"]["evidence"] == {"visits": 2, "unique_pages": 2}
    for kpi_id in ("FAI.JOURNEY_SUCCESS", "FAI.ACTIONS_TO_GOAL", "FAI.TIME_ON_TASK_SITE", "FAI.LOSTNESS"):
        assert not out[kpi_id]["assessed"] and out[kpi_id]["reason"] == "navigation_error", kpi_id
    assert out["FAI.DEAD_CLICK_RATE"]["value"] == 0.0 and out["FAI.UNEXPECTED_NAV"]["value"] == 0


# ---------------------------------------------------------------- time, responses, interaction latency

def test_time_on_task_counts_site_time_and_excludes_decision_latency():
    steps = [step(settle=400, execution=20, decision=5000), step("SCROLL_DOWN", role=None, settle=0, execution=15,
                                                                 decision=3000),
             step(settle=None, execution=30, decision=4000), {"operation": "DONE", "decision_latency_ms": 2500}]
    out = rows(steps, success=True)
    assert out["FAI.TIME_ON_TASK_SITE"]["value"] == 465
    assert out["FAI.TIME_ON_TASK_SITE"]["evidence"] == {"steps": 3, "decision_ms_excluded": 14500,
                                                        "unsettled_steps": 1, "settle_timeouts": 0}
    steps[0]["settle_reason"] = "timeout"
    assert rows(steps, success=True)["FAI.TIME_ON_TASK_SITE"]["evidence"]["settle_timeouts"] == 1
    assert out["FAI.ACTIONS_TO_GOAL"]["value"] == 2  # interactions only: no scroll, wait or DONE


def test_goal_kpis_need_a_verified_success():
    failed = rows([step()], success=False, optimal_steps=1)
    for kpi_id in ("FAI.ACTIONS_TO_GOAL", "FAI.ACTIONS_RATIO", "FAI.TIME_ON_TASK_SITE"):
        assert not failed[kpi_id]["assessed"] and failed[kpi_id]["reason"].startswith("not_applicable")
    assert failed["FAI.JOURNEY_SUCCESS"]["value"] is False and failed["FAI.JOURNEY_SUCCESS"]["assessed"]
    unknown = rows([step()], success=None, optimal_steps=1)
    assert unknown["FAI.JOURNEY_SUCCESS"]["reason"] == "no_verification"
    assert not unknown["FAI.ACTIONS_RATIO"]["assessed"] and unknown["FAI.ACTIONS_RATIO"]["reason"] == "no_verification"
    ok = rows([step(), step(node=2), step("SELECT", node=3)], success=True, optimal_steps=2)
    assert ok["FAI.ACTIONS_RATIO"]["value"] == 1.5


def test_action_response_interaction_latency_and_post_input_shifts():
    steps = [step(first_response_ms=80, event_timing_max_ms=24, shifts_post_input=0.02),
             step(first_response_ms=200, event_timing_max_ms=None, shifts_post_input=0.15),
             step(first_response_ms=120, event_timing_max_ms=310, navigations=1, after="/next",
                  flags={"new_document": True}),  # the click's timing stayed in the old document
             step("SELECT", first_response_ms=None, event_timing_max_ms=None)]
    out = rows(steps)
    # In-document feedback only: the new document's first response (120) is its own request, not feedback.
    assert out["PERF.ACTION_RESPONSE_MS"]["value"] == 140.0  # median of 80 and 200
    assert out["PERF.ACTION_RESPONSE_MS"]["evidence"] == {"interactions": 3, "responded": 2, "request_inclusive": 2,
                                                          "new_documents_excluded": 1, "unmeasured": 0}
    assert out["PERF.INP_SYNTH"]["value"] == 24.0
    assert out["PERF.CLS_POST_INPUT"]["value"] == 0.17  # the sum after every interaction: 0.02 + 0.15
    assert out["PERF.CLS_POST_INPUT"]["evidence"] == {"interactions": 4, "max_per_action": 0.15, "unmeasured": 0}
    below = rows([step(event_timing_max_ms=None)])["PERF.INP_SYNTH"]
    assert below["value"] == 16 and below["assessed"] and below["evidence"]["below_threshold"]
    bounded = rows([step(event_timing_max_ms=None), step(node=2, event_timing_max_ms=0)])["PERF.INP_SYNTH"]
    assert bounded["value"] == 16 and bounded["evidence"] == {"interactions": 2, "timed": 1, "unmeasured": 0,
                                                              "below_threshold": True}
    # vitals.js's first-input entry is not bound by the 16 ms threshold: a shorter one reads as the same bound as none
    lone = rows([step(event_timing_max_ms=0)])["PERF.INP_SYNTH"]
    assert lone["value"] == 16 and lone["reason"].startswith("limite superiore") and lone["evidence"]["below_threshold"]
    assert rows([step(event_timing_max_ms=40), step(node=2, event_timing_max_ms=0)])["PERF.INP_SYNTH"]["value"] == 40
    navigations = rows([step(event_timing_max_ms=None, flags={"new_document": True})])
    for kpi_id in ("PERF.INP_SYNTH", "PERF.ACTION_RESPONSE_MS"):
        assert not navigations[kpi_id]["assessed"] and navigations[kpi_id]["reason"].startswith("not_applicable")
    # No visible response: no latency and no worst value (that is FAI.DEAD_CLICK_RATE's), and no coverage loss for a
    # journey whose in-page steps were typing only (a value change is not a mutation).
    for silent in (rows([dead()]), rows([dead("TYPE_TEXT", role="searchbox")])):
        row = silent["PERF.ACTION_RESPONSE_MS"]
        assert not row["assessed"] and row["reason"].startswith("not_applicable: nessuna interazione nella stessa "
                                                                "pagina ha ricevuto una risposta visibile")
        assert row["evidence"] == {"interactions": 1, "responded": 0, "unmeasured": 0}


PERF = ("PERF.CLS_POST_INPUT", "PERF.INP_SYNTH", "PERF.ACTION_RESPONSE_MS")


def test_interactions_whose_measurement_failed_are_not_measured_as_best_values():
    """A renderer that crashed right after a click leaves since == {}: nothing was measured, so no best-band value
    (16 ms, 0 shifts) and no claim that the site did not answer."""
    crashed = {**dead(changed=None, flags={"session_gone": True}), "since": {}}
    blind = dead(changed=None, available=False)
    for steps in ([crashed], [blind], [crashed, blind]):
        out = rows(steps)
        for kpi_id in (*PERF, "FAI.DEAD_CLICK_RATE", "FAI.RAGE_EVENTS"):
            assert not out[kpi_id]["assessed"] and out[kpi_id]["reason"].startswith("no_measurement"), kpi_id
    # One measured click beside one that was not: values from the measured one, the other counted as unmeasured.
    out = rows([step(event_timing_max_ms=24, shifts_post_input=0.01), {**dead(node=2, changed=None), "since": {}}])
    assert out["PERF.INP_SYNTH"]["value"] == 24.0 and out["PERF.INP_SYNTH"]["evidence"]["unmeasured"] == 1
    assert out["PERF.CLS_POST_INPUT"]["value"] == 0.01 and out["PERF.CLS_POST_INPUT"]["evidence"]["interactions"] == 1
    assert out["PERF.ACTION_RESPONSE_MS"]["evidence"]["unmeasured"] == 1
    assert out["FAI.DEAD_CLICK_RATE"]["evidence"] == {"dead": 0, "clicks": 1, "unmeasured": 1}


def test_unexpected_navigation_counts_new_tabs_and_navigations_nobody_chose():
    steps = [
        step(after="/partner", flags={"external_nav": True, "new_document": True}),  # a chosen link: evidence only
        step(),
        step(flags={"new_tab": True}),  # a tab the page opened: unexpected
        step("TYPE_TEXT", role="searchbox", after="/search", flags={"new_document": True}),  # typing never submits
        step("SCROLL_DOWN", role=None, flags={"external_nav": True}),  # an ad redirect while scrolling
        step("SELECT", role="combobox", after="/plp?sort=price", flags={"new_document": True}),  # chosen
        step("WAIT", role=None, after="/promo", navigations=1),  # an older step without the new_document flag
    ]
    assert [friction.unexpected_nav(s) for s in steps] == [False, False, True, True, True, False, True]
    out = rows(steps)
    assert out["FAI.UNEXPECTED_NAV"]["value"] == 4
    assert out["FAI.UNEXPECTED_NAV"]["evidence"] == {"new_tabs": 1, "spontaneous": 3, "between_steps": 0,
                                                     "chosen_external": 1}


def navigation(before, after, **flags):
    """A NAVIGATION record: the page moved by itself between steps (journey.py)."""
    return {"operation": "NAVIGATION", "url_before": SHOP + before, "url_after": SHOP + after, "since": {},
            "page_changed": True, "flags": {"new_document": True, "external_nav": False, **flags}}


def test_a_page_that_navigates_by_itself_between_steps_is_unexpected_and_visited():
    steps = [step(before="/", after="/", mutations=1), navigation("/", "/privacy"),
             {"operation": "CLICK", "url_before": SHOP + "/privacy", "flags": {"stale": True}, "since": {}},
             navigation("/privacy", "/cat"), step(before="/cat", after="/p1", navigations=1,
                                                   flags={"new_document": True})]
    assert not friction.executed(steps[1]) and friction.unexpected_nav(steps[1])
    assert friction.visits(steps) == [f"{SHOP}/", f"{SHOP}/privacy", f"{SHOP}/cat", f"{SHOP}/p1"]
    out = rows(steps, success=True, optimal_steps=2, optimal_pages=2)
    assert out["FAI.UNEXPECTED_NAV"]["value"] == 2
    assert out["FAI.UNEXPECTED_NAV"]["evidence"] == {"new_tabs": 0, "spontaneous": 2, "between_steps": 2,
                                                     "chosen_external": 0}
    assert out["FAI.ACTIONS_TO_GOAL"]["value"] == 2 and out["FAI.TIME_ON_TASK_SITE"]["evidence"]["steps"] == 2
    assert out["FAI.LOSTNESS"]["evidence"] == {"unique_pages": 4, "visits": 4, "optimal": 2}
    # right after a step whose settle timed out, a new document may be that step's late navigation: a visit only
    late = navigation("/cat", "/p1", follows_timeout=True)
    assert not friction.unexpected_nav(late)
    # nothing executed, but the page redirected itself: still counted
    alone = rows([navigation("/", "/promo")])
    assert alone["FAI.UNEXPECTED_NAV"]["value"] == 1 and alone["FAI.BACKTRACK_RATE"]["value"] == 0.0


def test_a_journey_that_executed_nothing_reports_process_kpis_as_not_applicable():
    out = rows([{"operation": "DONE", "decision_latency_ms": 900}], success=False)
    assert out["FAI.JOURNEY_SUCCESS"]["assessed"]
    for kpi_id, row in out.items():
        if kpi_id != "FAI.JOURNEY_SUCCESS":
            assert not row["assessed"] and row["reason"].startswith("not_applicable"), kpi_id


# ---------------------------------------------------------------- contract with kpis.py, anchors.json, scoring

def test_every_friction_kpi_once_with_its_registry_unit_and_scorable():
    steps = [step(after="/plp"), step(before="/plp", after="/pdp"), step(before="/pdp", after="/cart")]
    observations = friction.metrics(steps, optimal_steps=3, optimal_pages=4, success=True, profile="mobile")
    assert [o["kpi_id"] for o in observations] == [k.id for k in by_producer("friction")]
    for o in observations:
        assert o["unit"] == KPIS[o["kpi_id"]].unit and o["source"] == "deterministic"
        assert o["profile"] == "mobile" and o["page_id"] is None and o["assessed"], o
        json.dumps(o)
    scored = {k["id"]: k for k in score(observations)["kpis"]}
    for o in observations:
        if KPIS[o["kpi_id"]].id != "FAI.ACTIONS_TO_GOAL":  # informational, no anchor
            assert scored[o["kpi_id"]]["normalized"] is not None, o["kpi_id"]
    assert scored["FAI.LOSTNESS"]["normalized"] == 100.0 and scored["FAI.JOURNEY_SUCCESS"]["normalized"] == 100.0


def golden():
    run = json.loads((RUNS / "journey_run.json").read_text(encoding="utf-8"))
    steps = [json.loads(line) for line in (RUNS / "journey_steps.jsonl").read_text(encoding="utf-8").splitlines()]
    journey = run["journey"]
    out = rows(steps, optimal_steps=journey["optimal_steps"], optimal_pages=5,
               success=journey["verification"]["passed"], profile=journey["profile"])
    return run, out


# The hand-written golden run predates the producer: it counts 4 clicks for 5 CLICK steps, leaves the start page out
# of Lostness and takes the median response over new documents too. Its observations must be regenerated from
# friction.metrics (tests/fixtures/runs is WS4's); then the strict xfail below passes and must be removed.
GOLDEN_STALE = {"FAI.DEAD_CLICK_RATE": 20.0, "FAI.LOSTNESS": 0.0, "PERF.ACTION_RESPONSE_MS": 120.0}


def test_golden_journey_steps_reproduce_the_golden_friction_observations():
    run, out = golden()
    for row in run["observations"]:
        if row["kpi_id"] not in GOLDEN_STALE:
            assert out[row["kpi_id"]]["value"] == row["value"], row["kpi_id"]
    assert {k: out[k]["value"] for k in GOLDEN_STALE} == GOLDEN_STALE
    assert out["FAI.DEAD_CLICK_RATE"]["evidence"] == {"dead": 1, "clicks": 5, "unmeasured": 0}
    assert out["FAI.LOSTNESS"]["evidence"] == {"unique_pages": 5, "visits": 5, "optimal": 5}  # each page once


@pytest.mark.xfail(strict=True, reason="golden journey_run.json observations are not friction.metrics output yet")
def test_golden_journey_observations_are_the_producers():
    run, out = golden()
    assert {o["kpi_id"]: o["value"] for o in run["observations"]} == {k: out[k]["value"] for k in out}
