import copy
import json
import math
import os
from pathlib import Path

import pytest

from jev_ultrafast.engagement import judgments, scoring
from jev_ultrafast.engagement.judgments import load_rubrics
from jev_ultrafast.engagement.kpis import KPI_LIST, KPIS
from jev_ultrafast.engagement.schemas import SUB_INDICES

RUNS = Path(__file__).parent / "fixtures" / "runs"
ANCHORS = scoring.load_anchors()


def load(name):
    return json.loads((RUNS / f"{name}.json").read_text(encoding="utf-8"))


def ob(kpi_id, value, **extra):
    row = {"kpi_id": kpi_id, "value": value, "source": "deterministic", "assessed": True,
           "page_id": None, "profile": None, "stage": None, "reason": None, "evidence": {}}
    row.update(extra)
    return row


def kpi(output, kpi_id):
    return next(k for k in output["kpis"] if k["id"] == kpi_id)


def full_observations():
    """The audit fixture's deterministic observations: every page/site KPI assessed."""
    return [o for o in load("audit_complete")["observations"]]


# ---------------------------------------------------------------- anchors


def test_anchors_cover_exactly_the_kpi_registry():
    assert ANCHORS["version"] == "anchors.v1"
    assert set(ANCHORS["kpis"]) == set(KPIS)
    assert set(ANCHORS["weights"]) == set(SUB_INDICES)
    assert sum(ANCHORS["weights"].values()) == 100
    assert ANCHORS["publish"] == {"min_coverage": 0.6, "min_major_coverage": 0.5, "major_weight": 15}


def test_anchor_entries_are_well_formed():
    rubric_values = {r["kpi_id"]: set(r["values"]) for r in load_rubrics().values()}
    for item in KPI_LIST:
        spec = ANCHORS["kpis"][item.id]
        assert isinstance(spec["provisional"], bool) and spec["source"].strip(), item.id
        assert spec["weight"] >= 0, item.id
        if item.owner == "DPR":
            assert spec["direction"] == "risk" and spec["weight"] == 0 and "points" not in spec and "map" not in spec
            assert item.severity is not None
            continue
        if spec["weight"] == 0:
            assert "points" not in spec and "map" not in spec, f"{item.id}: informational KPIs carry no anchors"
            continue
        if item.value_type == "number":
            points = spec["points"]
            xs = [x for x, _ in points]
            ys = [y for _, y in points]
            assert xs == sorted(xs) and len(set(xs)) == len(xs), item.id
            assert all(0 <= y <= 100 for y in ys), item.id
            expected = sorted(ys, reverse=spec["direction"] == "lower")
            assert spec["direction"] in {"lower", "higher"} and ys == expected, item.id
        elif item.value_type == "bool":
            assert set(spec["map"]) == {"true", "false"}, item.id
            assert (spec["map"]["true"] > spec["map"]["false"]) == (spec["direction"] == "bool"), item.id
        else:
            assert spec["direction"] == "enum" and spec["map"], item.id
        if item.judged:
            assert set(spec["map"]) == rubric_values[item.id], item.id


def test_published_anchors_and_provisional_flags():
    kpis = ANCHORS["kpis"]
    assert kpis["PERF.LCP"]["points"] == [[2500, 100], [4000, 50], [8000, 0]]
    assert kpis["PERF.CLS"]["points"][:2] == [[0.1, 100], [0.25, 50]]
    assert [8, 100] in kpis["FAI.CHECKOUT_FIELDS"]["points"] and [11.3, 60] in kpis["FAI.CHECKOUT_FIELDS"]["points"]
    assert kpis["CCL.READABILITY"]["points"] == [[40, 0], [60, 60], [80, 100]]
    assert [0.4, 60] in kpis["FAI.LOSTNESS"]["points"] and [0.5, 40] in kpis["FAI.LOSTNESS"]["points"]
    assert [5, 70] in kpis["TRI.REVIEW_COUNT"]["points"]
    assert kpis["TRI.RATING_BAND"]["map"]["4.0-4.7"] == 100
    assert [x for x, _ in kpis["PERF.ACTION_RESPONSE_MS"]["points"]] == [100, 1000, 3000, 10000]
    assert kpis["FAI.ACTIONS_TO_GOAL"]["weight"] == 0
    for published in ("PERF.LCP", "PERF.CLS", "FAI.CHECKOUT_FIELDS", "CCL.READABILITY", "FAI.LOSTNESS"):
        assert kpis[published]["provisional"] is False
    for editorial in ("PERF.BYTES_TOTAL", "CCL.VISUAL_COMPLEXITY", "FAI.DEAD_CLICK_RATE", "TRI.RETURNS_CLARITY"):
        assert kpis[editorial]["provisional"] is True
    # a published band transferred to a different quantity, or a guideline the research does not verify, is editorial
    for transferred in ("PERF.CLS_POST_INPUT", "PERF.INP_SYNTH", "PERF.TBT_APPROX", "FAI.CART_EDITABLE",
                        "PTI.VAT_STATED"):
        assert kpis[transferred]["provisional"] is True, transferred


# ---------------------------------------------------------------- normalize


@pytest.mark.parametrize(
    "kpi_id, value, expected",
    [
        ("PERF.LCP", 1000, 100),
        ("PERF.LCP", 2500, 100),
        ("PERF.LCP", 3250, 75),
        ("PERF.LCP", 4000, 50),
        ("PERF.LCP", 6000, 25),
        ("PERF.LCP", 99_999, 0),
        ("PERF.CLS", 0.175, 75),
        ("CCL.READABILITY", 70, 80),
        ("CCL.READABILITY", 10, 0),
        ("FAI.CHECKOUT_FIELDS", 11.3, 60),
        ("FAI.LOSTNESS", 0.25, 75),
        ("TRI.REVIEW_COUNT", 100, 100),
        ("FAI.SEARCH_VISIBLE", True, 100),
        ("FAI.SEARCH_VISIBLE", False, 0),
        ("FAI.FORCED_ACCOUNT", True, 0),
        ("FAI.FORCED_ACCOUNT", False, 100),
        ("TRI.RATING_BAND", "4.8-5.0", 70),
        ("TRI.RETURNS_CLARITY", "vague", 40),
    ],
)
def test_normalize_interpolates_and_clamps(kpi_id, value, expected):
    assert scoring.normalize(kpi_id, value, ANCHORS) == pytest.approx(expected)


@pytest.mark.parametrize(
    "kpi_id, value",
    [
        ("PERF.LCP", None),
        ("PERF.LCP", float("nan")),
        ("PERF.LCP", float("inf")),
        ("PERF.LCP", "2500"),
        ("PERF.LCP", True),
        ("TRI.RATING_BAND", "5 stelle"),
        ("FAI.PDP_VARIANT_SELECTOR", "none"),
        ("FAI.ACTIONS_TO_GOAL", 6),
        ("DPR.COUNTDOWN_RESET", 0.9),
        ("NOT.A_KPI", 1),
    ],
)
def test_normalize_returns_none_when_not_scorable(kpi_id, value):
    assert scoring.normalize(kpi_id, value, ANCHORS) is None


# ---------------------------------------------------------------- aggregate


def test_weighted_median_uses_stage_weights():
    rows = [ob("PERF.LCP", 1000, stage="home"), ob("PERF.LCP", 2000, stage="cart"),
            ob("PERF.LCP", 3000, stage="checkout_entry"), ob("PERF.LCP", 9000, stage="pdp")]
    assert scoring.aggregate(rows, "PERF.LCP", ANCHORS["stage_weights"]) == (3000, True, None)
    assert scoring.aggregate(rows, "PERF.LCP", {}) == (2500, True, None)


def test_aggregate_modes():
    responses = [ob("PERF.ACTION_RESPONSE_MS", v) for v in (100, 300, 200)]
    assert scoring.aggregate(responses, KPIS["PERF.ACTION_RESPONSE_MS"]) == (200, True, None)
    errors = [ob("PERF.CONSOLE_ERRORS", 1, profile="mobile"), ob("PERF.CONSOLE_ERRORS", 2, profile="mobile"),
              ob("PERF.CONSOLE_ERRORS", 4, profile="desktop"), ob("PERF.CONSOLE_ERRORS", 1)]
    assert scoring.aggregate(errors, "PERF.CONSOLE_ERRORS") == (5, True, None)  # worst profile + site-level
    assert scoring.aggregate([ob("FAI.SEARCH_VISIBLE", False), ob("FAI.SEARCH_VISIBLE", True)], "FAI.SEARCH_VISIBLE")[0]
    assert not scoring.aggregate([ob("TRI.HTTPS", True), ob("TRI.HTTPS", False)], "TRI.HTTPS")[0]
    assert scoring.aggregate([ob("FAI.PLP_PAGINATION", "infinite"), ob("FAI.PLP_PAGINATION", "pagination")],
                             "FAI.PLP_PAGINATION")[0] == "infinite"
    assert scoring.aggregate([ob("FAI.PLP_FILTERS", 2), ob("FAI.PLP_FILTERS", 6)], "FAI.PLP_FILTERS")[0] == 6
    assert scoring.aggregate([ob("FAI.AUTOCOMPLETE_ATTRS", 80), ob("FAI.AUTOCOMPLETE_ATTRS", 57.1)],
                             "FAI.AUTOCOMPLETE_ATTRS")[0] == 57.1


def test_best_aggregate_picks_the_highest_scoring_value_in_any_order():
    spec = ANCHORS["kpis"]["MPI.SOCIAL_PROOF_RICH"]
    rows = [ob("MPI.SOCIAL_PROOF_RICH", "basic", page_id="home"), ob("MPI.SOCIAL_PROOF_RICH", "rich", page_id="pdp"),
            ob("MPI.SOCIAL_PROOF_RICH", "absent", page_id="cart")]
    for order in (rows, rows[::-1], rows[1:] + rows[:1]):
        assert scoring.aggregate(order, "MPI.SOCIAL_PROOF_RICH", spec=spec) == ("rich", True, None)
    tie = [ob("TRI.RETURNS_CLARITY", "vague", page_id="a"), ob("TRI.RETURNS_CLARITY", "vague", page_id="b")]
    assert scoring._aggregate(tie, KPIS["TRI.RETURNS_CLARITY"], spec=ANCHORS["kpis"]["TRI.RETURNS_CLARITY"])[3][
        "page_id"] == "a"  # ties keep the earliest observation
    unknown = [ob("TRI.RETURNS_CLARITY", "boh"), ob("TRI.RETURNS_CLARITY", "absent")]
    assert scoring.aggregate(unknown, "TRI.RETURNS_CLARITY", spec=ANCHORS["kpis"]["TRI.RETURNS_CLARITY"])[0] == "absent"
    assert KPIS["TRI.RETURNS_CLARITY"].aggregate == "best" and KPIS["MPI.AUTHORITY"].aggregate == "best"


def test_first_aggregate_skips_values_mapped_to_not_applicable():
    rows = [ob("FAI.PDP_VARIANT_SELECTOR", "none", profile="mobile"),
            ob("FAI.PDP_VARIANT_SELECTOR", "buttons", profile="desktop")]
    spec = ANCHORS["kpis"]["FAI.PDP_VARIANT_SELECTOR"]
    assert scoring.aggregate(rows, "FAI.PDP_VARIANT_SELECTOR", spec=spec)[0] == "buttons"
    variant = kpi(scoring.score(rows, ANCHORS), "FAI.PDP_VARIANT_SELECTOR")
    assert (variant["value"], variant["normalized"], variant["applicable"]) == ("buttons", 100, True)
    only_none = kpi(scoring.score(rows[:1], ANCHORS), "FAI.PDP_VARIANT_SELECTOR")
    assert only_none["applicable"] is False and only_none["reason"] == "not_applicable"


def test_page_aggregates_take_the_worst_profile():
    rows = [ob("PERF.LCP", 2000, profile="mobile", stage="home"), ob("PERF.LCP", 3000, profile="mobile", stage="pdp"),
            ob("PERF.LCP", 1200, profile="desktop", stage="home"), ob("PERF.LCP", 1500, profile="desktop", stage="pdp")]
    spec = ANCHORS["kpis"]["PERF.LCP"]
    assert scoring._aggregate(rows, KPIS["PERF.LCP"], ANCHORS["stage_weights"], spec)[::4] == (3000, "mobile")
    assert scoring._aggregate(rows[::-1], KPIS["PERF.LCP"], ANCHORS["stage_weights"], spec)[::4] == (3000, "mobile")
    lcp = kpi(scoring.score(rows, ANCHORS), "PERF.LCP")
    assert (lcp["value"], lcp["profile"], lcp["normalized"]) == (3000, "mobile", pytest.approx(83.33, abs=0.01))
    # rows without a profile join every profile (higher is better here: the lower share is the worse one)
    shared = [ob("FAI.TARGET_SIZE_24", 98, profile="mobile"), ob("FAI.TARGET_SIZE_24", 96, profile="desktop"),
              ob("FAI.TARGET_SIZE_24", 60)]
    assert scoring._aggregate(shared, KPIS["FAI.TARGET_SIZE_24"], spec=ANCHORS["kpis"]["FAI.TARGET_SIZE_24"])[::4] == (
        78, "desktop")
    # ties on the anchors (both 100, or both clamped to 0) keep the first profile's value and name no profile
    tie = [ob("PERF.LCP", 1000, profile="desktop"), ob("PERF.LCP", 900, profile="mobile")]
    assert scoring._aggregate(tie, KPIS["PERF.LCP"], spec=spec)[::4] == (1000, None)
    clamped = [ob("PERF.LCP", 8500, profile="desktop"), ob("PERF.LCP", 9000, profile="mobile")]
    assert scoring._aggregate(clamped, KPIS["PERF.LCP"], spec=spec)[::4] == (8500, None)
    assert kpi(scoring.score(clamped, ANCHORS), "PERF.LCP")["profile"] is None
    responses = [ob("PERF.ACTION_RESPONSE_MS", 300, profile="mobile"), ob("PERF.ACTION_RESPONSE_MS", 900,
                                                                          profile="desktop")]
    assert scoring.aggregate(responses, "PERF.ACTION_RESPONSE_MS") == (900, True, None)  # median per profile
    pooled = [ob("PERF.LCP", 1000), ob("PERF.LCP", 3000)]
    assert scoring._aggregate(pooled, KPIS["PERF.LCP"], spec=spec)[::4] == (2000, None)
    assert kpi(scoring.score([ob("TRI.HTTPS", True, profile="mobile")], ANCHORS), "TRI.HTTPS")["profile"] is None


@pytest.mark.parametrize(
    "kpi_id, mobile, desktop, worst",
    [
        ("FAI.PLP_FILTERS", 2, 6, 2),  # max of a higher-is-better count: pooling would show desktop's 6
        ("FAI.SEARCH_VISIBLE", False, True, False),  # any: hidden mobile search is not rescued by desktop
        ("FAI.FORCED_ACCOUNT", False, True, True),  # any on a negative bool: the forced account counts
        ("PTI.FUNNEL_PRICE_DELTA", 0, 4, 4),  # max of a lower-is-better percentage
        ("FAI.AUTOCOMPLETE_ATTRS", 80, 60, 60),  # min
        ("PERF.CONSOLE_ERRORS", 1, 4, 4),  # sum
        ("FAI.PLP_PAGINATION", "load_more", "infinite", "infinite"),  # first
        ("TRI.RETURNS_CLARITY", "vague", "clear", "vague"),  # best within a profile, worst across profiles
    ],
)
def test_every_aggregate_takes_the_worst_profile(kpi_id, mobile, desktop, worst):
    rows = [ob(kpi_id, mobile, profile="mobile"), ob(kpi_id, desktop, profile="desktop")]
    for order in (rows, rows[::-1]):
        value, assessed, _, _, profile = scoring._aggregate(order, KPIS[kpi_id], spec=ANCHORS["kpis"][kpi_id])
        assert assessed and value == worst
        assert profile == ("mobile" if worst == mobile else "desktop")
    scored = kpi(scoring.score(rows, ANCHORS, judged=True), kpi_id)
    alone = [kpi(scoring.score([r], ANCHORS, judged=True), kpi_id)["normalized"] for r in rows]
    assert scored["normalized"] == min(alone) and scored["profile"] == profile


def test_worst_profile_ignores_unscorable_profiles_and_pools_risk():
    best = [ob("TRI.RETURNS_CLARITY", "clear", profile="mobile", page_id="m-home"),
            ob("TRI.RETURNS_CLARITY", "vague", profile="mobile", page_id="m-cart"),
            ob("TRI.RETURNS_CLARITY", "absent", profile="desktop", page_id="d-home"),
            ob("TRI.RETURNS_CLARITY", "vague", profile="desktop", page_id="d-cart")]
    value, _, _, chosen, profile = scoring._aggregate(best, KPIS["TRI.RETURNS_CLARITY"],
                                                      spec=ANCHORS["kpis"]["TRI.RETURNS_CLARITY"])
    assert (value, chosen["page_id"], profile) == ("vague", "d-cart", "desktop")  # the worst profile's own row
    shared = [ob("FAI.SEARCH_VISIBLE", True), ob("FAI.SEARCH_VISIBLE", False, profile="mobile"),
              ob("FAI.SEARCH_VISIBLE", False, profile="desktop")]
    assert scoring.aggregate(shared, "FAI.SEARCH_VISIBLE") == (True, True, None)  # any: the site-level row joins both
    risk = [ob("DPR.CONSENT_ASYMMETRY", 0.7, profile="mobile"), ob("DPR.CONSENT_ASYMMETRY", 0.2, profile="desktop")]
    assert scoring._aggregate(risk, KPIS["DPR.CONSENT_ASYMMETRY"], spec=ANCHORS["kpis"]["DPR.CONSENT_ASYMMETRY"])[
        ::4] == (0.7, None)  # DPR: pooled max, no profile
    invalid = [ob("FAI.PLP_FILTERS", "many", profile="mobile"), ob("FAI.PLP_FILTERS", 3, profile="desktop")]
    assert scoring.aggregate(invalid, "FAI.PLP_FILTERS") == (3, True, None)
    nothing = [ob("FAI.PLP_FILTERS", "many", profile="mobile"), ob("FAI.PLP_FILTERS", "few", profile="desktop")]
    assert scoring.aggregate(nothing, "FAI.PLP_FILTERS") == (None, False, "invalid_value")


def test_any_false_resting_on_an_unobserved_stage_is_not_conclusive():
    """Ruling 5: an "any" False counts only when every stage of the KPI was observed in that profile."""
    def delivery(profile, pdp, cart, reason="add_to_cart_failed"):
        rows = [ob("TRI.DELIVERY_TIME_STATED", pdp, profile=profile, stage="pdp")]
        return rows + [ob("TRI.DELIVERY_TIME_STATED", cart, profile=profile, stage="cart") if cart is not None else
                       ob("TRI.DELIVERY_TIME_STATED", None, profile=profile, stage="cart", assessed=False,
                          reason=reason)]

    assert scoring.aggregate(delivery("mobile", False, None), "TRI.DELIVERY_TIME_STATED") == (
        None, False, "add_to_cart_failed")  # never an assessed False from the product page alone
    assert scoring.aggregate(delivery("mobile", True, None), "TRI.DELIVERY_TIME_STATED") == (True, True, None)
    assert scoring.aggregate(delivery("mobile", False, None, "not_applicable: x"), "TRI.DELIVERY_TIME_STATED") == (
        False, True, None)  # a stage that does not apply is no gap
    mixed = delivery("mobile", False, None) + delivery("desktop", False, False)
    assert scoring._aggregate(mixed, KPIS["TRI.DELIVERY_TIME_STATED"],
                              spec=ANCHORS["kpis"]["TRI.DELIVERY_TIME_STATED"])[:3] == (False, True, None)
    complete = delivery("mobile", True, None) + delivery("desktop", False, False)
    assert scoring._aggregate(complete, KPIS["TRI.DELIVERY_TIME_STATED"], spec=ANCHORS["kpis"][
        "TRI.DELIVERY_TIME_STATED"])[::4] == (False, "desktop")  # the observed desktop False is the worst profile
    output = scoring.score(delivery("mobile", False, None) + delivery("desktop", False, None, "timeout"), ANCHORS)
    row = kpi(output, "TRI.DELIVERY_TIME_STATED")
    assert (row["assessed"], row["normalized"], row["reason"]) == (False, None, "add_to_cart_failed")


def test_any_rule_on_a_negative_bool_follows_the_evidence_not_the_polarity():
    """FAI.FORCED_ACCOUNT is "any" with True bad: a conclusive cart False (rule d) in one profile is assessed even when
    the other profile's checkout timed out; beside an unassessed checkout row of its own profile it is withheld."""
    cart = ob("FAI.FORCED_ACCOUNT", False, profile="mobile", stage="cart")
    checkout = ob("FAI.FORCED_ACCOUNT", None, profile="desktop", stage="checkout_entry", assessed=False,
                  reason="timeout")
    for rows in ([cart, checkout], [checkout, cart]):
        assert scoring.aggregate(rows, "FAI.FORCED_ACCOUNT") == (False, True, None)
    same = [cart, {**checkout, "profile": "mobile"}]
    assert scoring.aggregate(same, "FAI.FORCED_ACCOUNT") == (None, False, "timeout")
    assert kpi(scoring.score([cart, checkout], ANCHORS), "FAI.FORCED_ACCOUNT")["normalized"] == 100
    assert scoring.aggregate([ob("FAI.FORCED_ACCOUNT", True, profile="mobile", stage="cart"), {
        **checkout, "profile": "mobile"}], "FAI.FORCED_ACCOUNT") == (True, True, None)  # True is always conclusive


def test_bools_are_numbers_only_for_risk_confidences():
    lcp = kpi(scoring.score([ob("PERF.LCP", True)], ANCHORS), "PERF.LCP")
    assert (lcp["value"], lcp["assessed"], lcp["reason"]) == (None, False, "invalid_value")
    assert scoring.aggregate([ob("PERF.CONSOLE_ERRORS", False)], "PERF.CONSOLE_ERRORS")[2] == "invalid_value"
    risk = kpi(scoring.score([ob("DPR.SNEAK_INTO_BASKET", True)], ANCHORS), "DPR.SNEAK_INTO_BASKET")
    assert (risk["value"], risk["assessed"]) == (1, True)


def test_aggregate_reports_why_nothing_was_assessed():
    na = ob("PERF.LCP", None, assessed=False, reason="not_applicable: x")
    timeout = ob("PERF.LCP", None, assessed=False, reason="timeout")
    assert scoring.aggregate([], "PERF.LCP") == (None, False, "no_observation")
    assert scoring.aggregate([na, timeout], "PERF.LCP") == (None, False, "timeout")
    assert scoring.aggregate([na], "PERF.LCP") == (None, False, "not_applicable: x")
    assert scoring.aggregate([ob("PERF.LCP", None)], "PERF.LCP")[1] is False
    assert scoring.aggregate([ob("PERF.LCP", "fast")], "PERF.LCP") == (None, False, "invalid_value")
    assert scoring.aggregate([timeout, ob("PERF.LCP", 2000)], "PERF.LCP") == (2000, True, None)


# ---------------------------------------------------------------- sub-indices, coverage, publishing


def test_not_assessable_is_never_scored_as_zero():
    fast = [ob("PERF.LCP", 2500), ob("PERF.FCP", None, assessed=False, reason="background_tab")]
    output = scoring.score(fast, ANCHORS)
    assert output["sub_indices"]["PERF"]["score"] == 100
    assert kpi(output, "PERF.FCP")["normalized"] is None and kpi(output, "PERF.FCP")["reason"] == "background_tab"
    assert 0 < output["sub_indices"]["PERF"]["coverage"] < 1
    assert output["ers"]["score"] is None and output["ers"]["published"] is False


def test_journey_and_judged_kpis_count_only_when_applicable():
    base = full_observations()
    audit = scoring.score(base, ANCHORS, journey=False, judged=False)
    assert audit["sub_indices"]["FAI"]["coverage"] == 1
    assert kpi(audit, "FAI.JOURNEY_SUCCESS")["applicable"] is False
    assert kpi(audit, "FAI.JOURNEY_SUCCESS")["reason"] == "no_journey"
    assert kpi(audit, "TRI.RETURNS_CLARITY")["reason"] == "not_judged"
    with_journey = scoring.score(base, ANCHORS, journey=True, judged=False)
    assert with_journey["sub_indices"]["FAI"]["coverage"] < 1
    assert kpi(with_journey, "FAI.JOURNEY_SUCCESS")["reason"] == "no_observation"
    with_tasks = scoring.score(base, ANCHORS, journey=False, judged=True)
    assert with_tasks["sub_indices"]["TRI"]["coverage"] == pytest.approx(16.5 / 18.5, abs=1e-3)
    assert scoring.score(base, ANCHORS)["context"] == {"journey": False, "judged": False}
    # unassessed placeholders (no journey, judgments never requested) do not make those families applicable
    placeholders = [ob("FAI.JOURNEY_SUCCESS", None, assessed=False, reason="no_journey"),
                    ob("TRI.RETURNS_CLARITY", None, source="judged", assessed=False, reason="not_judged")]
    inferred = scoring.score(base + placeholders, ANCHORS)
    assert inferred["context"] == {"journey": False, "judged": False}
    assert inferred["sub_indices"]["FAI"] == audit["sub_indices"]["FAI"]


def test_score_run_ignores_stored_not_judged_placeholders():
    run = load("audit_complete")
    run["judgments"] = {}
    alone = scoring.score_run(run)
    run["observations"] += judgments.observations_from_final(run)  # a caller that stored the derived rows
    stored = scoring.score_run(run)
    assert stored["overall"]["context"]["judged"] is False
    returns = kpi(stored["overall"], "TRI.RETURNS_CLARITY")
    assert (returns["applicable"], returns["reason"]) == (False, "not_judged")
    for scope in ("overall", "mobile", "desktop"):
        a = alone["overall"] if scope == "overall" else alone["profiles"][scope]
        b = stored["overall"] if scope == "overall" else stored["profiles"][scope]
        assert a["sub_indices"] == b["sub_indices"] and a["ers"] == b["ers"], scope
    # a deterministic absence row exists only once judgments were requested
    absence = ob("MPI.AUTHORITY", "absent", reason="no_snippets", profile="mobile")
    assert scoring.score([absence], ANCHORS)["context"]["judged"] is True


def test_not_applicable_kpis_leave_coverage_untouched():
    rows = [ob("FAI.PDP_VARIANT_SELECTOR", "none"), ob("FAI.PDP_CTA_ABOVE_FOLD", True),
            ob("PTI.STRIKETHROUGH_LOWEST30", None, assessed=False, reason="not_applicable: nessun prezzo barrato"),
            ob("PTI.PRICE_VISIBLE_PDP", True)]
    output = scoring.score(rows, ANCHORS)
    variant = kpi(output, "FAI.PDP_VARIANT_SELECTOR")
    assert variant["applicable"] is False and variant["reason"] == "not_applicable"
    assert kpi(output, "PTI.STRIKETHROUGH_LOWEST30")["applicable"] is False
    weights = ANCHORS["kpis"]
    pti_total = sum(weights[k.id]["weight"] for k in KPI_LIST if k.owner == "PTI") - weights[
        "PTI.STRIKETHROUGH_LOWEST30"]["weight"]
    assert output["sub_indices"]["PTI"]["coverage"] == pytest.approx(2 / pti_total, abs=1e-3)


def drop(observations, owner=None, ids=()):
    return [o for o in observations if KPIS[o["kpi_id"]].owner != owner and o["kpi_id"] not in ids]


def test_grades_and_publish_rules():
    assert [scoring._grade(c, ANCHORS) for c in (1, 0.85, 0.849, 0.7, 0.6, 0.599)] == ["A", "A", "B", "B", "C", None]
    full = full_observations()
    output = scoring.score(full, ANCHORS)
    assert output["ers"]["published"] and output["ers"]["grade"] == "A" and output["ers"]["coverage"] == 1

    no_mpi = scoring.score(drop(full, "MPI"), ANCHORS)  # weight 10 < 15: not a major sub-index
    assert no_mpi["ers"]["published"] and no_mpi["ers"]["coverage"] == 0.9 and no_mpi["ers"]["grade"] == "A"
    assert no_mpi["sub_indices"]["MPI"]["score"] is None

    no_pti = scoring.score(drop(full, "PTI"), ANCHORS)  # coverage 0.85 overall, but a major sub-index is empty
    assert no_pti["ers"]["coverage"] == 0.85
    assert no_pti["ers"]["score"] is None and not no_pti["ers"]["published"]
    assert no_pti["ers"]["reason"] == "copertura sotto il minimo del 50 % per Trasparenza di prezzi e costi (0 %)"

    partial = scoring.score(drop(drop(full, "MPI"), ids=("PERF.LCP", "PERF.CLS", "PERF.TBT_APPROX")), ANCHORS)
    assert partial["sub_indices"]["PERF"]["coverage"] == pytest.approx(8 / 15, abs=1e-3)
    assert partial["ers"]["coverage"] == pytest.approx(0.816, abs=1e-3)
    assert partial["ers"]["published"] and partial["ers"]["grade"] == "B"

    sparse = scoring.score(drop(drop(full, "MPI"), "CCL"), ANCHORS)
    assert sparse["ers"]["coverage"] == 0.75 and not sparse["ers"]["published"]  # CCL weight 15 is major

    thin = [o for o in full if KPIS[o["kpi_id"]].owner in {"PERF", "TRI"}]
    thin_output = scoring.score(thin, ANCHORS)
    assert thin_output["ers"]["coverage"] == pytest.approx(0.38, abs=0.01)
    assert thin_output["ers"]["reason"] == (  # Italian names and percentages, as the report writes them
        "copertura complessiva 38 % sotto il minimo del 60 %; copertura sotto il minimo del 50 % per Attrito previsto "
        "(0 %), Trasparenza di prezzi e costi (0 %), Chiarezza e carico cognitivo (0 %)")
    assert thin_output["ers"]["score"] is None
    assert thin_output["ers"]["limiting_factor"] in {"PERF", "TRI"}


def test_ers_is_weighted_geometric_mean_with_floor_and_dpr_penalty():
    scores = {"PERF": 80, "FAI": 50, "TRI": 90, "PTI": 70, "CCL": 60, "MPI": 5}
    subs = {n: {"score": s, "coverage": 1.0, "llm_share": 0.0} for n, s in scores.items()}
    weights = ANCHORS["weights"]
    raw = math.exp(sum(weights[n] * math.log(max(s, 10)) for n, s in scores.items()) / 100)
    assert scoring.ers(subs, 0, ANCHORS)["score"] == round(raw, 1)
    assert scoring.ers(subs, 20, ANCHORS)["score"] == round(raw * (1 - 0.3 * 0.2), 1)
    assert scoring.ers(subs, 0, ANCHORS)["limiting_factor"] == "MPI"
    compensated = dict(subs, MPI={"score": 100, "coverage": 1.0, "llm_share": 0.0},
                       FAI={"score": 10, "coverage": 1.0, "llm_share": 0.0})
    arithmetic = sum(weights[n] * s["score"] for n, s in compensated.items()) / 100
    assert scoring.ers(compensated, 0, ANCHORS)["score"] < arithmetic  # non-compensatory


def test_credit_unless_withholds_mpi_credit_only_for_confident_signals():
    def urgency(confidence):
        rows = [ob("MPI.URGENCY_SIGNALS", 2)]
        if confidence is not None:
            rows.append(ob("DPR.COUNTDOWN_RESET", confidence))
        return kpi(scoring.score(rows, ANCHORS), "MPI.URGENCY_SIGNALS")

    assert urgency(None)["normalized"] == 100
    assert urgency(0.49)["normalized"] == 100
    fired = urgency(0.5)
    no_signal = scoring.normalize("MPI.URGENCY_SIGNALS", 0, ANCHORS)
    assert no_signal == 30  # a deceptive signal earns no credit, but no extra penalty: DPR already charges it
    assert fired["normalized"] == no_signal and fired["reason"] == "credit_withheld:DPR.COUNTDOWN_RESET"
    assert fired["assessed"] is True and fired["value"] == 2


def test_publish_gates_use_unrounded_coverage():
    subs = {n: {"score": 80, "coverage": 1.0, "llm_share": 0.0} for n in ANCHORS["weights"]}
    subs["PTI"] = {"score": 80, "coverage": round(0.4996, 3), "_coverage": 0.4996, "llm_share": 0.0}
    result = scoring.ers(subs, 0, ANCHORS)
    assert result["published"] is False and "Trasparenza di prezzi e costi (49,9 %)" in result["reason"]  # not "50 %"
    subs["PTI"]["_coverage"] = 0.5
    assert scoring.ers(subs, 0, ANCHORS)["published"] is True


def test_dpr_reports_its_coverage_and_never_reads_unassessed_as_zero():
    full = full_observations()
    output = scoring.score(full, ANCHORS)  # deterministic risk signals only: judged ones do not apply
    risk = output["dpr"]
    assert (risk["assessed"], risk["applicable"], risk["coverage"], risk["unassessed"]) == (6, 6, 1.0, [])
    judged = scoring.score(full, ANCHORS, judged=True)["dpr"]
    assert (judged["assessed"], judged["applicable"]) == (6, 9)
    assert judged["coverage"] == pytest.approx(3.6 / 5.7, abs=1e-3)  # severity-weighted
    assert {u["kpi_id"]: u["reason"] for u in judged["unassessed"]} == {
        "DPR.CONFIRMSHAMING": "no_observation", "DPR.TRICK_QUESTIONS": "no_observation",
        "DPR.HIDDEN_SUBSCRIPTION": "no_observation"}
    clean = [o for o in full if KPIS[o["kpi_id"]].owner != "DPR"]
    none = scoring.score(clean, ANCHORS)
    assert none["dpr"]["score"] == 0 and none["dpr"]["coverage"] == 0 and none["dpr"]["assessed"] == 0
    assert len(none["dpr"]["unassessed"]) == none["dpr"]["applicable"] == 6
    assert none["ers"]["score"] > output["ers"]["score"]  # no penalty applied: an upper bound, not "no risk"
    skipped = clean + [ob("DPR.COUNTDOWN_RESET", None, assessed=False, reason="not_applicable: nessun countdown")]
    applicable = scoring.score(skipped, ANCHORS)["dpr"]
    assert applicable["applicable"] == 5
    assert "DPR.COUNTDOWN_RESET" not in {u["kpi_id"] for u in applicable["unassessed"]}
    journey = scoring.score_run(load("journey_run"))
    assert journey["overall"]["dpr"]["coverage"] == 0 == journey["profiles"]["mobile"]["dpr"]["coverage"]


def test_dpr_noisy_or_is_monotonic():
    assert scoring.dpr([]) == 0
    assert scoring.dpr([{"severity": 0.9, "confidence": 1.0}]) == 90
    pair = [{"severity": 0.9, "confidence": 0.6}, {"severity": 0.3, "confidence": 0.6}]
    assert scoring.dpr(pair) == pytest.approx(100 * (1 - (1 - 0.54) * (1 - 0.18)))
    previous = -1
    for confidence in (0, 0.2, 0.5, 0.8, 1):
        value = scoring.dpr([{"severity": 0.6, "confidence": confidence}, {"severity": 0.3, "confidence": 0.5}])
        assert value >= previous
        previous = value
    signals = []
    previous = 0
    for item in [k for k in KPI_LIST if k.owner == "DPR"]:
        signals.append({"severity": item.severity, "confidence": 0.5})
        assert scoring.dpr(signals) >= previous
        previous = scoring.dpr(signals)
    assert previous < 100

    full = full_observations()
    clean = [o for o in full if KPIS[o["kpi_id"]].owner != "DPR"]
    ers = []
    for confidence in (0.0, 0.4, 0.8, 1.0):
        output = scoring.score(clean + [ob("DPR.SNEAK_INTO_BASKET", confidence)], ANCHORS)
        assert output["dpr"]["score"] == pytest.approx(100 * 0.9 * confidence)
        ers.append(output["ers"]["score"])
    assert ers == sorted(ers, reverse=True) and ers[0] > ers[-1]


# ---------------------------------------------------------------- runs


def compact(scores):
    def view(output):
        return {
            "ers": output["ers"],
            "dpr": output["dpr"]["score"],
            "dpr_coverage": [output["dpr"]["coverage"], output["dpr"]["assessed"], output["dpr"]["applicable"]],
            "signals": [[s["kpi_id"], s["confidence"]] for s in output["dpr"]["signals"]],
            "sub_indices": output["sub_indices"],
            "kpis": {k["id"]: [k["value"], k["normalized"], k["assessed"], k["applicable"], k["reason"], k["profile"]]
                     for k in output["kpis"]},
        }

    return json.loads(json.dumps({"overall": view(scores["overall"]),
                                  "profiles": {p: view(s) for p, s in scores["profiles"].items()}}))


@pytest.mark.parametrize("name", ["audit_complete", "journey_run"])
def test_golden_scores(name):
    """Regenerate with JEV_UPDATE_GOLDEN=1 after a deliberate anchors/scoring change."""
    actual = compact(scoring.score_run(load(name)))
    path = RUNS / f"{name}.scores.json"
    if os.environ.get("JEV_UPDATE_GOLDEN") == "1":
        path.write_text(json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    assert actual == json.loads(path.read_text(encoding="utf-8"))


def test_score_run_is_deterministic_and_pure():
    run = load("audit_complete")
    before = copy.deepcopy(run)
    first = scoring.score_run(run)
    second = scoring.score_run(copy.deepcopy(run))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert run == before


def test_audit_fixture_scores():
    scores = scoring.score_run(load("audit_complete"))
    overall = scores["overall"]
    assert overall["ers"]["published"] and overall["ers"]["grade"] == "A"
    assert overall["context"] == {"journey": False, "judged": True, "journey_runs": []}
    lcp = kpi(overall, "PERF.LCP")  # weighted median per profile (PDP/PLP count twice); the worst profile wins
    assert (lcp["value"], lcp["profile"]) == (2900, "mobile") == (kpi(scores["profiles"]["mobile"], "PERF.LCP")[
        "value"], "mobile")
    assert (kpi(overall, "PERF.TBT_APPROX")["value"], kpi(overall, "PERF.TBT_APPROX")["profile"]) == (380, "mobile")
    assert (kpi(overall, "PERF.BYTES_TOTAL")["value"], kpi(overall, "PERF.BYTES_TOTAL")["profile"]) == (2250, "desktop")
    assert kpi(overall, "TRI.LEGAL_ID")["profile"] is None and kpi(overall, "FAI.CHECKOUT_FIELDS")["profile"] is None
    assert kpi(overall, "MPI.URGENCY_SIGNALS")["reason"] == "credit_withheld:DPR.COUNTDOWN_RESET"
    assert kpi(overall, "MPI.SCARCITY_SIGNALS")["normalized"] == 80  # FAKE_LOW_STOCK 0.3 stays below 0.5
    assert overall["sub_indices"]["TRI"]["llm_share"] == pytest.approx(2 / 18.5, abs=1e-3)
    assert overall["sub_indices"]["MPI"]["score"] == pytest.approx(610 / 7, abs=0.05)  # withheld urgency = 30
    assert overall["sub_indices"]["FAI"]["limiting_kpis"][0] == "FAI.PDP_CTA_ABOVE_FOLD"
    returns = kpi(overall, "TRI.RETURNS_CLARITY")
    assert (returns["value"], returns["source"], returns["normalized"]) == ("clear", "judged", 100)
    assert kpi(overall, "MPI.SOCIAL_PROOF_RICH")["value"] == "rich"  # home task uncertain, pdp decided
    authority = kpi(overall, "MPI.AUTHORITY")  # home "present" beats the absence read on pages without text
    assert (authority["value"], authority["source"], authority["reason"]) == ("present", "judged", None)
    assert authority["observations"] > 2
    assert kpi(overall, "DPR.HIDDEN_SUBSCRIPTION")["value"] == pytest.approx(0.533)
    signals = [s["kpi_id"] for s in overall["top_risk_signals"]]
    assert signals[0] == "DPR.COUNTDOWN_RESET" and "DPR.CONFIRMSHAMING" in signals
    assert "DPR.SNEAK_INTO_BASKET" not in signals  # assessed at 0: no signal
    assert overall["dpr"]["score"] == pytest.approx(
        100 * (1 - math.prod(1 - s["severity"] * s["confidence"] for s in overall["dpr"]["signals"])), abs=0.01)


def test_profiles_share_site_level_observations():
    scores = scoring.score_run(load("audit_complete"))
    assert sorted(scores["profiles"]) == ["desktop", "mobile"]
    mobile, desktop, overall = scores["profiles"]["mobile"], scores["profiles"]["desktop"], scores["overall"]
    assert kpi(desktop, "FAI.CHECKOUT_FIELDS")["reason"] == "timeout"
    assert kpi(mobile, "FAI.CHECKOUT_FIELDS")["value"] == 14 == kpi(overall, "FAI.CHECKOUT_FIELDS")["value"]
    assert kpi(mobile, "FAI.PDP_CTA_ABOVE_FOLD")["value"] is False
    assert kpi(desktop, "FAI.PDP_CTA_ABOVE_FOLD")["value"] is True
    assert kpi(overall, "FAI.PDP_CTA_ABOVE_FOLD")["value"] is False
    for output in (mobile, desktop):
        assert kpi(output, "TRI.POLICY_LINKS")["observations"] == 1  # profile None: counted everywhere
        assert kpi(output, "TRI.RETURNS_CLARITY")["value"] == "clear"  # one task covers both profiles
    assert kpi(mobile, "DPR.CONFIRMSHAMING")["value"] == 0.8
    shaming = kpi(desktop, "DPR.CONFIRMSHAMING")  # desktop: only the consent banner, without a reject option
    assert (shaming["assessed"], shaming["reason"]) == (False, "judgment_unclear")
    assert desktop["dpr"]["unassessed"] == [{"kpi_id": "DPR.CONFIRMSHAMING", "reason": "judgment_unclear"}]
    assert mobile["dpr"]["unassessed"] == [{"kpi_id": "DPR.NAGGING_OVERLAYS", "reason": "timeout"}]
    assert overall["dpr"]["coverage"] == 1 > max(mobile["dpr"]["coverage"], desktop["dpr"]["coverage"])
    assert desktop["ers"]["coverage"] < 1 == mobile["ers"]["coverage"]
    assert mobile["ers"]["score"] != desktop["ers"]["score"]


def test_judged_observations_derive_from_final_judgments():
    run = load("audit_complete")
    derived = scoring.run_observations(run)
    stored = copy.deepcopy(run)
    stored["observations"] = derived  # e.g. a caller that saved the merged rows: never duplicated
    assert scoring.run_observations(stored) == derived
    assert scoring.score_run(stored) == scoring.score_run(run)
    run["observations"].append({"kpi_id": "TRI.RETURNS_CLARITY", "value": "absent", "source": "judged",
                                "assessed": True, "profile": None, "page_id": None, "stage": None})
    assert kpi(scoring.score_run(run)["overall"], "TRI.RETURNS_CLARITY")["value"] == "clear"
    run["judgments"] = {}
    stale = scoring.score_run(run)["overall"]
    assert stale["context"]["judged"] is True and kpi(stale, "TRI.RETURNS_CLARITY")["value"] == "absent"


# ---------------------------------------------------------------- judged KPIs: order, absence, preparation


def judged_run(pages, labels=None):
    """A run with judgments prepared and every task decided unanimously; labels: {task_id: (label, quote)}."""
    run = {"run_id": "r", "settings": {"locale": "it"}, "pages": pages, "observations": []}
    tasks = judgments.ensure_tasks(run)
    labels = labels or {}
    for judge in ("j1", "j2", "j3"):
        batch = []
        for task in tasks:
            label, quote = labels.get(task["task_id"], (None, None))
            if label is None:
                label = "absent" if "absent" in task["labels"] else "unclear"
            if label not in task["no_quote_labels"]:
                quote = quote or task["snippets"][0]["text"]
            evidence = [{"snippet_id": task["snippets"][0]["snippet_id"], "quote": quote}] if quote else []
            batch.append({"task_id": task["task_id"], "label": label, "confidence": 0.9, "evidence": evidence})
        assert not judgments.submit(run, judge, "m", batch, cache=False)["rejected"]
    judgments.finalize(run)
    return run


def shop_page(page_id, stage, snippets, profile="mobile", **audit):
    return {"page_id": page_id, "stage": stage, "profile": profile, "url": f"https://shop.test/{page_id}",
            "classification": {"type": stage},
            "audit": {"snippets": [{"snippet_id": f"s{i + 1}", "kind": k, "text": t}
                                   for i, (k, t) in enumerate(snippets)], **audit}}


RETURNS = "Reso gratuito entro 30 giorni: etichetta prepagata, rimborso in 5 giorni"


@pytest.fixture(autouse=True)
def judgment_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_ENGAGEMENT_CACHE", str(tmp_path / "cache"))


def test_the_store_seed_is_no_judgment_request():
    run = load("audit_complete")
    run["judgments"] = {"tasks": [], "verdicts": [], "final": []}  # RunStore.new_run: judgments never requested
    run["observations"] = [o for o in run["observations"] if KPIS[o["kpi_id"]].producer != "judgments"]
    overall = scoring.score_run(run)["overall"]
    assert overall["context"]["judged"] is False
    returns = kpi(overall, "TRI.RETURNS_CLARITY")
    assert (returns["applicable"], returns["assessed"], returns["reason"]) == (False, False, "not_judged")
    assert overall["dpr"]["applicable"] == 6  # the judged risk signals do not apply without judgments


def test_judged_site_kpis_do_not_depend_on_page_order():
    home = shop_page("mobile-home-1", "home", [("shipping_policy", "Spedizione gratuita sopra 49 euro"),
                                               ("testimonial", "4,6/5 su 1.284 recensioni")])
    pdp = shop_page("mobile-pdp-1", "pdp", [("returns_policy", RETURNS),
                                            ("testimonial", "«Leggerissime» — Giulia R., acquisto verificato")])
    labels = {"returns_clarity:mobile-pdp-1": ("clear", "Reso gratuito entro 30 giorni"),
              "social_proof:mobile-home-1": ("basic", "4,6/5 su 1.284 recensioni"),
              "social_proof:mobile-pdp-1": ("rich", "Giulia R., acquisto verificato")}
    results = []
    for pages in ([home, pdp], [pdp, home]):
        run = judged_run(copy.deepcopy(pages), labels)
        assert "returns_clarity:mobile-home-1" not in {t["task_id"] for t in run["judgments"]["tasks"]}
        overall = scoring.score_run(run)["overall"]
        results.append([(kpi(overall, k)["value"], kpi(overall, k)["normalized"])
                        for k in ("TRI.RETURNS_CLARITY", "MPI.SOCIAL_PROOF_RICH")])
    assert results[0] == results[1] == [("clear", 100), ("rich", 100)]


def returns_outcome(snippets, **audit):
    pages = [shop_page("mobile-home-1", "home", [("headline", "Scarpe da corsa")], **audit),
             shop_page("mobile-pdp-1", "pdp", snippets, **audit)]
    label = {"returns_clarity:mobile-pdp-1": snippets and ("vague", "Resi facili")}
    output = scoring.score_run(judged_run(pages, {k: v for k, v in label.items() if v}))["overall"]
    return kpi(output, "TRI.RETURNS_CLARITY"), output["sub_indices"]["TRI"]


def test_missing_returns_text_scores_no_better_than_vague_text():
    vague, vague_tri = returns_outcome([("returns_policy", "Resi facili")])
    missing, missing_tri = returns_outcome([])
    linked, _ = returns_outcome([], trust={"policy_links": {"returns": True}}, a11y={"iframes": 2})
    assert (vague["value"], vague["source"]) == ("vague", "judged")
    assert (missing["value"], missing["normalized"], missing["source"]) == ("absent", 0, "deterministic")
    assert missing["assessed"] and missing["reason"] == "no_snippets" and missing["applicable"]
    assert missing["normalized"] <= vague["normalized"] and missing_tri["coverage"] == vague_tri["coverage"]
    assert missing_tri["llm_share"] == 0 < vague_tri["llm_share"]
    assert (linked["value"], linked["source"]) == ("vague", "deterministic")  # a returns link without details
    rows = [o for o in judgments.observations_from_final(judged_run(
        [shop_page("mobile-pdp-1", "pdp", [], a11y={"iframes": 2, "shadow_roots_closed": 1})]))
        if o["kpi_id"] == "TRI.RETURNS_CLARITY"]
    assert rows[0]["evidence"]["iframes"] == 2 and rows[0]["evidence"]["shadow_roots_closed"] == 1


def test_absence_is_not_read_while_a_judgment_is_unresolved_or_unreachable():
    pages = [shop_page("mobile-home-1", "home", [("returns_policy", "Resi facili")]),
             shop_page("mobile-pdp-1", "pdp", [("headline", "Runner X2")])]
    run = {"run_id": "r", "settings": {}, "pages": pages, "observations": []}
    judgments.ensure_tasks(run)
    pending = kpi(scoring.score_run(run)["overall"], "TRI.RETURNS_CLARITY")
    assert pending["assessed"] is False and pending["reason"] == "judgment_pending"
    blocked = judged_run([shop_page("mobile-pdp-1", "pdp", []), {**shop_page("mobile-cart-1", "cart", []),
                                                                   "classification": {"type": "challenge"}},
                          {"page_id": "mobile-plp-1", "stage": "plp", "profile": "mobile"}])
    reasons = {o["page_id"]: (o["assessed"], o["reason"]) for o in judgments.observations_from_final(blocked)
               if o["kpi_id"] == "TRI.RETURNS_CLARITY"}
    assert reasons == {"mobile-pdp-1": (True, "no_snippets"), "mobile-cart-1": (False, "bot_challenge"),
                       "mobile-plp-1": (False, "no_audit")}


def test_unrelated_judgment_tasks_do_not_change_coverage():
    base = load("audit_complete")
    for page in base["pages"]:
        page["audit"]["snippets"] = []
    outputs = []
    for extra in ([], [("modal_decline", "No grazie, preferisco pagare di più")]):
        run = copy.deepcopy(base)
        run["pages"][2]["audit"]["snippets"] = [{"snippet_id": "s1", "kind": k, "text": t} for k, t in extra]
        run["judgments"] = {}
        judgments.ensure_tasks(run)
        assert len(run["judgments"]["tasks"]) == len(extra)
        outputs.append(scoring.score_run(run)["overall"])
    for name in ("TRI", "CCL", "MPI"):
        assert outputs[0]["sub_indices"][name]["coverage"] == outputs[1]["sub_indices"][name]["coverage"], name
    assert outputs[0]["context"]["judged"] is True
    assert kpi(outputs[0], "TRI.RETURNS_CLARITY")["value"] == "absent"
    assert kpi(outputs[0], "CCL.VALUE_PROP_CLARITY")["reason"] == "no_snippets"  # not assessable, not absent
    unprepared = copy.deepcopy(base)
    unprepared["judgments"] = {}
    assert kpi(scoring.score_run(unprepared)["overall"], "TRI.RETURNS_CLARITY")["reason"] == "not_judged"


# ---------------------------------------------------------------- journeys linked to an audit


def test_score_run_merges_linked_journeys_per_profile():
    audit, journey = load("audit_complete"), load("journey_run")
    alone = scoring.score_run(audit)
    linked = scoring.score_run(audit, journeys=[journey])
    overall, mobile, desktop = linked["overall"], linked["profiles"]["mobile"], linked["profiles"]["desktop"]
    assert overall["context"] == {"journey": True, "judged": True, "journey_runs": [journey["run_id"]]}
    assert mobile["context"]["journey"] is True and desktop["context"]["journey"] is False
    assert kpi(overall, "FAI.JOURNEY_SUCCESS")["value"] is True
    assert kpi(mobile, "FAI.DEAD_CLICK_RATE")["normalized"] == 30
    assert kpi(desktop, "FAI.JOURNEY_SUCCESS")["applicable"] is False
    assert desktop["sub_indices"]["FAI"] == alone["profiles"]["desktop"]["sub_indices"]["FAI"]
    assert mobile["sub_indices"]["FAI"]["score"] != alone["profiles"]["mobile"]["sub_indices"]["FAI"]["score"]
    assert overall["ers"]["published"] is True
    for wrong in (audit, {**journey, "kind": "audit"}, {**journey, "journey": None}, None):
        with pytest.raises(ValueError, match="not a journey run"):
            scoring.score_run(audit, journeys=[wrong])


def test_journey_observations_imply_a_journey_even_without_a_record():
    run = load("audit_complete")
    run["observations"] += [o for o in load("journey_run")["observations"] if o["kpi_id"] == "FAI.JOURNEY_SUCCESS"]
    scores = scoring.score_run(run)
    assert scores["overall"]["context"]["journey"] is True
    assert kpi(scores["overall"], "FAI.JOURNEY_SUCCESS")["applicable"] is True
    assert scores["profiles"]["mobile"]["context"]["journey"] is True
    assert scores["profiles"]["desktop"]["context"]["journey"] is False


def test_journey_fixture_scores():
    scores = scoring.score_run(load("journey_run"))
    overall = scores["overall"]
    assert list(scores["profiles"]) == ["mobile"]
    assert overall["ers"]["published"] is False and overall["ers"]["score"] is None
    assert "copertura complessiva" in overall["ers"]["reason"]
    assert kpi(overall, "FAI.JOURNEY_SUCCESS")["normalized"] == 100
    assert kpi(overall, "FAI.LOSTNESS")["normalized"] == 100  # five pages, each visited once: no detour
    dead = kpi(overall, "FAI.DEAD_CLICK_RATE")  # 1 dead click of 5 (friction.metrics over journey_steps.jsonl)
    assert (dead["value"], dead["normalized"]) == (20.0, 30)
    info = kpi(overall, "FAI.ACTIONS_TO_GOAL")
    assert info["assessed"] and info["normalized"] is None and info["weight"] == 0
    assert kpi(overall, "TRI.RETURNS_CLARITY")["applicable"] is False
    assert overall["sub_indices"]["FAI"]["score"] is not None
