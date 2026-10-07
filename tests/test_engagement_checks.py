"""checks.observations on synthetic runs (no browser): every checks/deception KPI and its branches."""

import copy
import json

import pytest

from jev_ultrafast.engagement import checks, deception, scoring
from jev_ultrafast.engagement.crawler import overlay_key
from jev_ultrafast.engagement.deception import (
    countdown_result,
    low_stock_result,
    overlay_pages,
    prechecked_paid,
    same_item,
    same_product,
    sneak_into_basket,
    ticking,
)
from jev_ultrafast.engagement.kpis import KPI_LIST, KPIS
from jev_ultrafast.engagement.schemas import STAGES

PRODUCED = [k for k in KPI_LIST if k.producer in ("checks", "deception")]
URL = "https://shop.example/"


def merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        out[key] = merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


BASE_AUDIT = {
    "lang": "it", "lexicon_lang": "it", "viewport": {"w": 390, "h": 844, "dpr": 3},
    "doc": {"h1s": ["Titolo"], "headings": {"h1": 1, "h2": 3}, "word_count": 100, "sentence_count": 8,
            "letter_count": 500, "text_sample": "Spedizione 4,90 euro, gratuita sopra i 59 euro."},
    "prices": [], "ctas": [], "jsonld": [], "meta": {},
    "search": {"present": True, "above_fold": True, "width": 300, "label": "Cerca"},
    "nav": {"categories": [], "breadcrumbs": {"present": False}, "generic_label_share": 0.1},
    "products": {"cards_count": 0, "main_group": 0, "cards": []},
    "filters": {"controls": 0, "sort": {"present": False}, "result_count_text": None, "pagination": "none"},
    "pdp": {"add_to_cart": {"present": False}, "price": None, "structured": None, "variant_selector": "none",
            "reviews": {}, "delivery_text": None, "shipping_text": None},
    "cart": {"line_items": [], "fees": [], "prechecked_paid": [], "checkout_cta": {"present": False}},
    "forms": {"visible": 0, "autocomplete_share": None, "labels_share": None},
    "overlays": [],
    "trust": {"contact": {"email": True}, "vat_id": "P.IVA 01234567897", "policy_count": 4,
              "payment_logos": ["visa", "paypal", "klarna"]},
    "persuasion": {"scarcity": [], "urgency": [], "reciprocity": [{"text": "Reso gratuito"}],
                   "free_shipping_threshold": [{"text": "Spedizione gratuita sopra i 59 €", "value": 59}],
                   "lowest_price_30d": [], "vat_statement": [{"text": "IVA inclusa"}]},
    "images": {"oversized_count": 0}, "targets": {"interactive": 20, "lt24": 1, "lt44": 5},
    "a11y": {"img_missing_alt": 0, "lang_missing": False}, "snippets": [],
}
CTA = {"present": True, "label": "Aggiungi al carrello", "above_fold": True, "contrast": 7.0,
       "rect": {"x": 16, "y": 400, "w": 358, "h": 48}, "area": 17184}
STAGE_AUDIT = {
    "home": {"overlays": [{"kind": "consent", "consent_like": True, "interrupting": True, "coverage": 0.28}]},
    "plp": {"products": {"cards_count": 24, "main_group": 24}, "nav": {"breadcrumbs": {"present": True}},
            "filters": {"controls": 5, "sort": {"present": True}, "result_count_text": "96 prodotti",
                        "pagination": "pagination"}, "doc": {"word_count": 20}},
    "pdp": {"pdp": {"add_to_cart": CTA, "title": "Scarpa Aurora",
                    "price": {"value": 49.9, "currency": "EUR", "above_fold": True, "text": "49,90 €"},
                    "structured": {"price": 49.9, "currency": "EUR", "has_return_policy": True, "has_shipping": True},
                    "variant_selector": "buttons", "variants": 5, "delivery_text": "Consegna in 2-4 giorni",
                    "shipping_text": "Spedizione 4,90 €", "reviews": {"rating": 4.5, "count": 128}},
            "ctas": [{"label": "Aggiungi al carrello", "primary_like": True, "above_fold": True}],
            "prices": [{"value": 69.9, "strikethrough": True}],
            "persuasion": {"lowest_price_30d": [{"text": "Prezzo più basso negli ultimi 30 giorni: 59,90 €"}]}},
    "cart": {"cart": {"line_items": [{"title": "Scarpa Aurora", "qty": 1, "price_value": 49.9}], "editable": True,
                      "subtotal_value": 49.9, "shipping_value": 4.9, "total_value": 54.8,
                      "fees": [{"label": "Spedizione", "value": 4.9}],
                      "checkout_cta": {**CTA, "label": "Procedi al checkout"}},
             "pdp": {"delivery_text": "Consegna stimata in 2-4 giorni"},
             "forms": {"visible": 2, "autocomplete_share": 0, "labels_share": 1, "guest_option": True},
             "ctas": [{"label": "Procedi al checkout", "primary_like": True, "above_fold": True}]},
    "checkout_entry": {"forms": {"visible": 13, "autocomplete_share": 0.846, "labels_share": 1, "guest_option": False,
                                 "password_present": False, "login_required": False},
                       "cart": {"shipping_value": 4.9, "total_value": 54.8, "fees": [{"label": "Spedizione",
                                                                                      "value": 4.9}]}},
}


def page(stage: str, profile: str = "mobile", n: int = 1, **override) -> dict:
    record = {
        "page_id": f"{profile}-{stage}-{n}", "stage": stage, "profile": profile, "url": URL + stage,
        "final_url": URL + stage, "classification": {"type": "checkout" if stage == "checkout_entry" else stage,
                                                    "signals": []},
        "vitals": {"ttfb": 400.04, "fcp": 1200, "lcp": 2000, "cls": 0.01234, "tbt_approx": 100, "loaf_count": 1,
                   "visibility_state_at_load": "visible"},
        "network": {"requests": 40, "bytes_transfer": 1_234_567, "bytes_js": 300_000, "third_party_share": 0.25,
                    "http_errors": 0, "mixed_content": 0, "source": "cdp"},
        "errors": {"console": 1, "page": 0, "samples": ["boom"]},
        "audit": merge(BASE_AUDIT, STAGE_AUDIT.get(stage, {})),
        "visual": {"colorfulness": 40.0, "edge_density": 0.05, "bytes_per_px": 0.1}, "probes": {}, "notes": [],
    }
    if stage == "home":
        record["probes"]["search_autocomplete"] = {"ran": True, "typed": "scarpe", "options": 5, "latency_ms": 40,
                                                   "window_ms": 800}
    if stage == "pdp":
        record["probes"]["add_to_cart"] = {"executed": True, "title": "Scarpa Aurora", "price": 49.9}
    return merge(record, override)


CLEAN_DECEPTION = {
    "countdown": {"assessed": True, "found": False, "page_id": "mobile-pdp-1", "stage": "pdp"},
    "low_stock": {"assessed": True, "found": False, "page_id": "mobile-pdp-1", "stage": "pdp"},
    "sneak_into_basket": {"assessed": True, "page_id": "mobile-cart-1", "stage": "cart", "unrequested": []},
    "prechecked_paid": {"assessed": True, "page_id": "mobile-cart-1", "stage": "cart", "items": []},
    "consent": {"assessed": True, "banner": True, "page_id": "mobile-home-1", "stage": "home",
                "first_layer": {"accept": {"area": 8400, "filled": True, "contrast": 6.4},
                                "reject": {"area": 8400, "filled": True, "contrast": 6.4}}},
    "nagging": {"assessed": True, "pages": [], "reappeared_after_close": None},
}


def run_of(pages, *, profiles=("mobile",), deception=None, not_assessable=()):
    return {"run_id": "r", "settings": {"profiles": list(profiles), "stages": list(STAGES)}, "pages": pages,
            "not_assessable": list(not_assessable),
            "deception": {p: copy.deepcopy(CLEAN_DECEPTION if deception is None else deception) for p in profiles}}


def full_run(profiles=("mobile",)):
    return run_of([page(stage, profile) for profile in profiles for stage in STAGES], profiles=profiles)


def rows(observations, kpi_id, **match):
    return [o for o in observations if o["kpi_id"] == kpi_id and all(o.get(k) == v for k, v in match.items())]


def one(observations, kpi_id, **match):
    found = rows(observations, kpi_id, **match)
    assert len(found) == 1, (kpi_id, match, found)
    return found[0]


# ---------------------------------------------------------------- shape


def test_every_checks_and_deception_kpi_is_observed_for_every_profile_with_its_unit():
    observations = checks.observations(full_run(("mobile", "desktop")))
    assert {o["kpi_id"] for o in observations} == {k.id for k in PRODUCED}
    for kpi in PRODUCED:
        for profile in ("mobile", "desktop"):
            mine = rows(observations, kpi.id, profile=profile)
            assert mine and all(o["unit"] == kpi.unit for o in mine), kpi.id
            assert any(o["assessed"] for o in mine), (kpi.id, mine)
            if kpi.scope == "page":
                assert {o["stage"] for o in mine} <= set(kpi.stages or STAGES), kpi.id
                assert all(o["page_id"] for o in mine if o["assessed"]), kpi.id
            elif kpi.producer == "checks":
                assert all(o["page_id"] is None for o in mine), kpi.id
    for o in observations:
        assert o["source"] == "deterministic"
        assert o["assessed"] or o["value"] is None
        kind = KPIS[o["kpi_id"]].value_type
        if o["assessed"] and kind == "bool":
            assert isinstance(o["value"], bool), o
        if o["assessed"] and kind in ("number", "confidence"):
            assert isinstance(o["value"], (int, float)) and not isinstance(o["value"], bool), o
    json.dumps(observations)


def test_a_complete_run_scores_and_publishes():
    run = full_run(("mobile", "desktop"))
    run["observations"] = checks.observations(run)
    overall = scoring.score_run(run)["overall"]
    assert overall["ers"]["published"] and overall["ers"]["grade"] == "A"
    assert overall["dpr"]["score"] == 0 and overall["dpr"]["assessed"] == 6
    deterministic = [k for k in overall["kpis"] if KPIS[k["id"]].producer in ("checks", "deception")]
    assert all(k["assessed"] for k in deterministic if k["applicable"]), [k["id"] for k in deterministic
                                                                          if not k["assessed"]]


@pytest.mark.parametrize("missing, published, low", [
    (("checkout_entry",), True, {}),
    (("cart", "checkout_entry"), True, {}),
    (("pdp", "cart", "checkout_entry"), False, {"PTI"}),
])
def test_an_incomplete_funnel_lowers_coverage_and_never_scores_zero(missing, published, low):
    """docs/engagement-kpi.md 3.5: a missing checkout never blocks publication; no product page does (PTI)."""
    reason = "add_to_cart_failed"
    deception = copy.deepcopy(CLEAN_DECEPTION)
    for key, stage in (("sneak_into_basket", "cart"), ("prechecked_paid", "cart"), ("countdown", "pdp"),
                       ("low_stock", "pdp")):
        if stage in missing:
            deception[key] = {"assessed": False, "reason": f"{stage}_not_reached"}
    run = run_of([page(s) for s in STAGES if s not in missing], deception=deception,
                 not_assessable=[{"stage": s, "profile": "mobile", "reason": reason} for s in missing])
    run["observations"] = checks.observations(run)
    assert not [o for o in run["observations"] if o["stage"] in missing and o["assessed"]]
    overall = scoring.score_run(run)["overall"]
    assert overall["ers"]["published"] is published
    for name in low:
        assert overall["sub_indices"][name]["coverage"] < 0.5
    for kpi in overall["kpis"]:
        if KPIS[kpi["id"]].stages and set(KPIS[kpi["id"]].stages) <= set(missing) and kpi["applicable"]:
            assert not kpi["assessed"] and kpi["reason"] == reason, kpi


# ---------------------------------------------------------------- rule a: pages and unreached stages


def test_perf_units_kb_percent_and_rounding():
    observations = checks.observations(full_run())
    assert one(observations, "PERF.BYTES_TOTAL", stage="home")["value"] == 1234.6  # 1 KB = 1000 bytes
    assert one(observations, "PERF.BYTES_JS", stage="home")["value"] == 300.0
    assert one(observations, "PERF.THIRD_PARTY_SHARE", stage="home")["value"] == 25.0  # percent, not a fraction
    assert one(observations, "PERF.CLS", stage="home")["value"] == 0.0123
    assert one(observations, "PERF.TTFB", stage="home")["value"] == 400.0
    assert one(observations, "PERF.REQUESTS", stage="home")["value"] == 40
    console = one(observations, "PERF.CONSOLE_ERRORS", stage="pdp")
    assert console["value"] == 1 and console["evidence"]["samples"] == ["boom"]


def test_paint_metrics_of_a_hidden_tab_and_other_documents_are_not_assessable_never_zero():
    hidden = page("home", vitals={"fcp": None, "lcp": None, "visibility_state_at_load": "hidden"})
    soft = page("plp", vitals={"ttfb": None, "lcp": None, "soft_navigation": True})
    failed = page("pdp", classification={"type": "other", "signals": ["navigation_error"]}, audit={})
    observations = checks.observations(run_of([hidden, soft, failed]))
    assert one(observations, "PERF.FCP", stage="home")["reason"] == "background_tab"
    assert one(observations, "PERF.TTFB", stage="home")["value"] == 400.0
    assert one(observations, "PERF.LCP", stage="plp")["reason"] == "not_applicable:same_document"
    for kpi_id in ("PERF.LCP", "PERF.REQUESTS", "PTI.PRICE_VISIBLE_PDP", "FAI.PDP_CTA_ABOVE_FOLD"):
        row = one(observations, kpi_id, stage="pdp")
        assert not row["assessed"] and row["value"] is None and row["reason"] == "navigation_error"
    empty = page("home", vitals=None, audit=None)
    observations = checks.observations(run_of([empty]))
    assert one(observations, "PERF.LCP", stage="home")["reason"] == "vitals_unavailable"
    assert one(observations, "CCL.HEADINGS", stage="home")["reason"] == "audit_unavailable"


def test_timings_of_a_document_that_was_not_a_cold_navigation_are_not_applicable():
    """Coordinator decision (b): a document the shop's speculation rules fetched before the click, or the document of
    an earlier record, has no load timings; its network still counts, and the KPI stays assessed on the other pages."""
    nulled = dict.fromkeys(("ttfb", "fcp", "lcp", "cls", "tbt_approx", "loaf_count"))
    prefetched = page("cart", vitals={**nulled, "speculative": "prefetch"})
    same = page("checkout_entry", vitals={**nulled, "soft_navigation": True})
    observations = checks.observations(run_of([page("home"), page("plp"), page("pdp"), prefetched, same]))
    for kpi_id in ("PERF.TTFB", "PERF.FCP", "PERF.LCP", "PERF.CLS", "PERF.TBT_APPROX", "PERF.LOAF_COUNT"):
        for stage, reason in (("cart", "speculative_navigation"), ("checkout_entry", "same_document")):
            row = one(observations, kpi_id, stage=stage)
            assert (row["assessed"], row["value"], row["reason"]) == (False, None, f"not_applicable:{reason}"), row
    assert one(observations, "PERF.BYTES_TOTAL", stage="cart")["value"] == 1234.6
    lcp = scoring.aggregate(rows(observations, "PERF.LCP"), KPIS["PERF.LCP"])
    assert lcp[:2] == (2000, True)
    run = run_of([page(stage, vitals={**nulled, "speculative": "prerender"}) for stage in STAGES])
    run["observations"] = checks.observations(run)
    kpi = next(k for k in scoring.score_run(run)["overall"]["kpis"] if k["id"] == "PERF.LCP")
    assert not kpi["applicable"] and not kpi["assessed"]  # no coverage lost, and never a fast page


def test_unreached_stages_get_one_unassessed_row_with_the_recorded_reason():
    run = run_of([page("home"), page("plp"), page("pdp")], not_assessable=[
        {"stage": "cart", "profile": "mobile", "reason": "add_to_cart_failed"}])
    observations = checks.observations(run)
    cart = one(observations, "FAI.CART_EDITABLE")
    assert cart == {**cart, "assessed": False, "page_id": None, "stage": "cart", "reason": "add_to_cart_failed"}
    assert one(observations, "FAI.CHECKOUT_FIELDS")["reason"] == "not_reached"  # nothing recorded for the stage
    lcp = rows(observations, "PERF.LCP")
    assert [o["stage"] for o in lcp if o["assessed"]] == ["home", "plp", "pdp"]
    assert {o["stage"]: o["reason"] for o in lcp if not o["assessed"]} == {"cart": "add_to_cart_failed",
                                                                          "checkout_entry": "not_reached"}
    vat = one(observations, "PTI.VAT_STATED")  # rule b: the reached stages decide
    assert vat["value"] is True and vat["evidence"]["stages_checked"] == ["pdp"]
    for kpi_id in ("PTI.FUNNEL_PRICE_DELTA", "PTI.UNEXPLAINED_FEES"):  # rule c: no cart, no comparison
        assert one(observations, kpi_id)["reason"] == "add_to_cart_failed"
    only_home = checks.observations(run_of([page("home")], not_assessable=[
        {"stage": "plp", "profile": "mobile", "reason": "bot_challenge"}]))
    assert one(only_home, "FAI.PLP_FILTERS")["reason"] == "bot_challenge"
    assert one(only_home, "TRI.JSONLD_SHIPPING")["reason"] == "not_reached"


def test_repeats_give_one_row_per_page_with_the_median():
    first = page("home")
    again = [page("home", n=n, vitals={"lcp": lcp}, repeat_of="mobile-home-1") for n, lcp in ((2, 3000), (3, 2600))]
    observations = checks.observations(run_of([first, *again]))
    lcp = one(observations, "PERF.LCP", stage="home")
    assert lcp["value"] == 2600 and lcp["page_id"] == "mobile-home-1"
    assert lcp["evidence"]["repeats"] == 3 and lcp["evidence"]["values"] == [2000, 3000, 2600]
    assert one(observations, "PERF.CONSOLE_ERRORS", stage="home")["value"] == 1  # sum KPIs: one row per page
    assert one(observations, "CCL.HEADINGS", stage="home")["value"] is True


def test_extra_pages_are_not_funnel_pages():
    rejected = page("plp", classification={"type": "other", "signals": []})
    rejected["stage"] = "extra"
    stray = page("plp", n=3, repeat_of="mobile-plp-1")  # a reload of the rejected candidate (older runs)
    observations = checks.observations(run_of([page("home"), rejected, page("plp", n=2), stray]))
    assert [o["page_id"] for o in rows(observations, "PERF.LCP") if o["assessed"]] == ["mobile-home-1",
                                                                                       "mobile-plp-2"]
    assert one(observations, "PERF.LCP", page_id="mobile-plp-2")["evidence"] == {}


# ---------------------------------------------------------------- rule d: guest checkout and forced account


def test_checkout_entry_decides_guest_and_forced_account_by_its_login_gate():
    gate = {"forms": {"guest_option": False, "password_present": True, "password_required": True,
                      "login_required": True}}
    no_guest_cart = {"forms": {"guest_option": False}}
    observations = checks.observations(run_of([page("cart", audit=no_guest_cart), page("checkout_entry", audit=gate)]))
    guest, forced = one(observations, "FAI.GUEST_CHECKOUT"), one(observations, "FAI.FORCED_ACCOUNT")
    assert (guest["value"], guest["stage"], forced["value"]) == (False, "checkout_entry", True)
    assert forced["evidence"] == {"guest_option": False, "login_required": True, "password_required": True,
                                  "password_present": True}
    plain = checks.observations(run_of([page("cart", audit=no_guest_cart), page("checkout_entry")]))  # no gate
    assert one(plain, "FAI.GUEST_CHECKOUT")["value"] is True and one(plain, "FAI.FORCED_ACCOUNT")["value"] is False
    # an optional password ("Crea una password (facoltativo)") forces no account
    optional = {"forms": {"password_present": True, "password_required": False, "login_required": False,
                          "guest_option": False}}
    rows_ = checks.observations(run_of([page("checkout_entry", audit=optional)]))
    assert one(rows_, "FAI.GUEST_CHECKOUT", stage="checkout_entry")["value"] is True
    assert one(rows_, "FAI.FORCED_ACCOUNT", stage="checkout_entry")["value"] is False
    # rule d reads the gate audit.js judges, never a password flag: a returning-customer login box beside the shipping
    # form (a required current-password, login_required false) is no gate; a required registration password is one
    # (audit.js sets login_required, even beside an address form); a guest option still wins
    split = {"password_present": True, "password_required": True, "login_required": False, "guest_option": False}
    registration = {**split, "login_required": True}
    for forms, forced in ((split, False), (registration, True), ({**registration, "guest_option": True}, False),
                          ({"password_present": True, "login_required": False}, False),
                          ({"password_present": False, "login_required": False}, False)):
        rows_ = checks.observations(run_of([page("checkout_entry", audit={"forms": forms})]))
        row = one(rows_, "FAI.FORCED_ACCOUNT", stage="checkout_entry")
        assert row["value"] is forced, forms
        assert one(rows_, "FAI.GUEST_CHECKOUT", stage="checkout_entry")["value"] is not forced, forms
        base = {"guest_option": False, "password_present": False, "login_required": False}  # STAGE_AUDIT's forms
        assert row["evidence"] == {**base, **forms}  # the flags audit.js read; an older audit has fewer


def test_the_checkout_entry_prevails_and_the_cart_guest_control_counts_only_without_it():
    """CONTRACTS "Checkout evidence": a reached checkout entry is the only evidence; the cart's guest control is used
    only when the checkout entry gave none. Both KPIs aggregate with "any", so they never both read true."""
    gate = {"forms": {"guest_option": False, "password_present": True, "login_required": True}}
    run = run_of([page("cart"), page("checkout_entry", audit=gate)])  # the cart offers "Continua come ospite"
    run["observations"] = checks.observations(run)
    guest = rows(run["observations"], "FAI.GUEST_CHECKOUT")
    forced = rows(run["observations"], "FAI.FORCED_ACCOUNT")
    assert [(o["stage"], o["value"]) for o in guest] == [("checkout_entry", False)]
    assert [(o["stage"], o["value"]) for o in forced] == [("checkout_entry", True)]
    site = {o["id"]: o["value"] for o in scoring.score_run(run)["overall"]["kpis"]
            if o["id"] in ("FAI.GUEST_CHECKOUT", "FAI.FORCED_ACCOUNT")}
    assert site == {"FAI.GUEST_CHECKOUT": False, "FAI.FORCED_ACCOUNT": True}
    no_guest_cart = page("cart", audit={"forms": {"guest_option": False}})
    for funnel in ([page("cart"), page("checkout_entry")], [no_guest_cart, page("checkout_entry", audit=gate)]):
        observations = checks.observations(run_of(funnel))
        values = [scoring.aggregate(rows(observations, k), KPIS[k])[0] for k in ("FAI.GUEST_CHECKOUT",
                                                                                 "FAI.FORCED_ACCOUNT")]
        assert values in ([True, False], [False, True])  # complementary on every combination
    # checkout not reached: the cart's guest control is conclusive on its own (no checkout row at all)
    reason = [{"stage": "checkout_entry", "profile": "mobile", "reason": "checkout_cta_not_found"}]
    observations = checks.observations(run_of([page("cart")], not_assessable=reason))
    assert [(o["stage"], o["value"], o["assessed"]) for o in rows(observations, "FAI.GUEST_CHECKOUT")] == [
        ("cart", True, True)]
    assert [(o["stage"], o["value"]) for o in rows(observations, "FAI.FORCED_ACCOUNT")] == [("cart", False)]
    # a checkout page that could not be read gives no evidence either: the cart's guest control stands
    unread = page("checkout_entry", classification={"type": "other", "signals": ["navigation_error"]}, audit={})
    observations = checks.observations(run_of([page("cart"), unread]))
    assert [(o["stage"], o["value"]) for o in rows(observations, "FAI.FORCED_ACCOUNT")] == [("cart", False)]
    # neither: not assessed with the checkout stage's reason; no guest mention in the cart is no evidence
    silent = checks.observations(run_of([page("cart", audit={"forms": {"guest_option": False}})],
                                        not_assessable=reason))
    assert [(o["stage"], o["assessed"], o["reason"]) for o in rows(silent, "FAI.FORCED_ACCOUNT")] == [
        ("checkout_entry", False, "checkout_cta_not_found")]
    nothing = checks.observations(run_of([page("pdp")], not_assessable=[
        {"stage": s, "profile": "mobile", "reason": "add_to_cart_failed"} for s in ("cart", "checkout_entry")]))
    assert {o["stage"]: o["reason"] for o in rows(nothing, "FAI.GUEST_CHECKOUT")} == {
        "checkout_entry": "add_to_cart_failed", "cart": "add_to_cart_failed"}


# ---------------------------------------------------------------- price transparency


DARK_CART = {"cart": {
    "line_items": [{"title": "Scarpa Aurora", "qty": 1, "price_value": 49.9},
                   {"title": "Protezione spedizione Premium", "qty": 1, "price_value": 2.9, "addon": True}],
    "subtotal_value": 52.8, "shipping_value": 6.9, "total_value": 66.1,
    "fees": [{"label": "Spedizione", "value": 6.9}, {"label": "Commissione di servizio", "value": 1.5},
             {"label": "Assicurazione sull'ordine", "value": 4.9}],
    "prechecked_paid": [{"label": "Assicurazione sull'ordine contro furto e smarrimento (+4,90 €)",
                         "price_text": "4,90 €", "price_value": 4.9}]}}
SILENT_PDP = {"pdp": {"shipping_text": None}, "cart": {"shipping_value": None}, "doc": {"text_sample": "Scarpa"},
              "persuasion": {"free_shipping_threshold": []}}


def test_funnel_price_delta_is_the_unexplained_increase_over_the_added_price():
    clean = checks.observations(full_run())
    assert one(clean, "PTI.FUNNEL_PRICE_DELTA")["value"] == 0
    assert one(clean, "PTI.UNEXPLAINED_FEES")["value"] == 0
    dark = run_of([page("pdp", audit=SILENT_PDP), page("cart", audit=DARK_CART), page("checkout_entry")])
    sneak = sneak_into_basket(dark["pages"][0], dark["pages"][1])
    dark["deception"]["mobile"]["sneak_into_basket"] = sneak
    observations = checks.observations(dark)
    delta = one(observations, "PTI.FUNNEL_PRICE_DELTA")
    assert delta["value"] == 18.6  # (66.10 - 6.90 shipping - 49.90) / 49.90
    assert [s["delta"] for s in delta["evidence"]["steps"]] == [18.6, 0.0]
    fees = one(observations, "PTI.UNEXPLAINED_FEES")
    # shipping never stated on the PDP and a service fee are unexplained; the pre-checked insurance and the sneaked
    # protection line are DPR signals, not counted twice
    assert fees["value"] == 2 and {f["label"] for f in fees["evidence"]["lines"]} == {"Spedizione",
                                                                                      "Commissione di servizio"}
    stated = run_of([page("pdp"), page("cart", audit=DARK_CART)])
    # shipping stated on the PDP; the cart's own pre-checked insurance is a DPR signal even without deception results
    assert one(checks.observations(stated), "PTI.UNEXPLAINED_FEES")["value"] == 1
    no_price = run_of([page("pdp", probes={"add_to_cart": {"price": None}}, audit={"pdp": {"price": None}}),
                       page("cart")])
    assert one(checks.observations(no_price), "PTI.FUNNEL_PRICE_DELTA")["reason"] == "no_product_price"


@pytest.mark.parametrize("audit, value, reason", [
    ({"prices": [], "pdp": {"strike_price": None}}, None, "not_applicable:no_strikethrough_price"),
    ({}, True, None),
    # audit.js without in_card marks: the scope of the two struck prices is unknown, one statement is enough
    ({"prices": [{"strikethrough": True}, {"strikethrough": True}]}, True, None),
    ({"persuasion": {"lowest_price_30d": []}}, False, None),
    ({"prices": [], "pdp": {"strike_price": {"text": "69,90 €"}}, "persuasion": {"lowest_price_30d": []}}, False,
     None),
])
def test_strikethrough_prices_need_the_lowest_price_statement(audit, value, reason):
    row = one(checks.observations(run_of([page("pdp", audit=audit)])), "PTI.STRIKETHROUGH_LOWEST30", stage="pdp")
    assert (row["value"], row["reason"]) == (value, reason)


@pytest.mark.parametrize("pdp, value, reason", [
    ({}, True, None),
    ({"structured": {"price": 39.9}}, False, None),
    ({"structured": {"price": 49.905}}, True, None),
    ({"structured": {"currency": "USD"}}, False, None),
    ({"structured": None}, None, "not_applicable:no_structured_price"),
    ({"price": None}, None, "not_applicable:no_visible_price"),
])
def test_structured_price_must_match_the_visible_one(pdp, value, reason):
    row = one(checks.observations(run_of([page("pdp", audit={"pdp": pdp})])), "PTI.PRICE_JSONLD_MATCH", stage="pdp")
    assert (row["value"], row["reason"]) == (value, reason)


# ---------------------------------------------------------------- page DOM checks


def test_page_dom_checks():
    run = run_of([
        page("home", audit={"targets": {"interactive": 40, "lt24": 2, "lt44": 10},
                            "a11y": {"img_missing_alt": 3, "lang_missing": True},
                            "overlays": [{"kind": "consent", "interrupting": True, "coverage": 0.283},
                                         {"kind": "newsletter", "interrupting": True, "coverage": 0.9},
                                         {"kind": "promo", "interrupting": False, "coverage": 0.05}]}),
        page("plp", audit={"products": {"cards_count": 12, "main_group": 12}}),
        page("pdp", audit={"pdp": {"variant_selector": "none", "reviews": {"rating": 4.84, "count": None,
                                                                           "rating_text": "4,85 su 5"}}}),
        page("cart", audit={"cart": {"line_items": [], "empty": True}}),
        page("checkout_entry", audit={"forms": {"visible": 0, "autocomplete_share": None, "labels_share": None}}),
    ])
    observations = checks.observations(run)
    assert one(observations, "FAI.TARGET_SIZE_24", stage="home")["value"] == 95.0
    assert one(observations, "FAI.TARGET_SIZE_44", stage="home")["value"] == 75.0
    assert one(observations, "FAI.A11Y_BASIC", stage="home")["value"] == 4
    interruptions = one(observations, "FAI.OVERLAY_INTERRUPTIONS", stage="home")
    assert interruptions["value"] == 2 and interruptions["evidence"]["kinds"] == ["consent", "newsletter"]
    assert one(observations, "FAI.OVERLAY_COVERAGE", stage="home")["value"] == 90.0
    assert one(observations, "FAI.OVERLAY_COVERAGE", stage="plp")["value"] == 0
    assert one(observations, "CCL.CHOICE_SUPPORT", stage="plp")["reason"] == "not_applicable:small_assortment"
    assert one(observations, "FAI.PDP_VARIANT_SELECTOR", stage="pdp")["value"] == "none"  # anchors: not applicable
    assert one(observations, "TRI.RATING_BAND", stage="pdp")["value"] == "4.8-5.0"
    assert one(observations, "TRI.REVIEWS_PRESENT", stage="pdp")["value"] is True
    assert one(observations, "TRI.REVIEW_COUNT", stage="pdp")["reason"] == "review_count_unavailable"
    assert one(observations, "FAI.CART_EDITABLE", stage="cart")["reason"] == "empty_cart"
    for kpi_id in ("FAI.CHECKOUT_FIELDS", "FAI.AUTOCOMPLETE_ATTRS", "CCL.FORM_LABELS"):
        assert one(observations, kpi_id, stage="checkout_entry")["reason"] == "not_applicable:no_form_fields"
    full = checks.observations(full_run())
    assert one(full, "FAI.CHECKOUT_FIELDS")["value"] == 13
    assert one(full, "FAI.AUTOCOMPLETE_ATTRS")["value"] == 84.6
    assert one(full, "CCL.FORM_LABELS", stage="cart")["value"] == 100
    assert one(full, "FAI.PLP_PAGINATION")["value"] == "pagination"
    assert one(full, "TRI.HTTPS", stage="home")["value"] is True
    assert one(full, "TRI.RATING_BAND")["value"] == "4.0-4.7"
    no_reviews = checks.observations(run_of([page("pdp", audit={"pdp": {"reviews": None}})]))
    assert one(no_reviews, "TRI.REVIEW_COUNT", stage="pdp")["value"] == 0
    assert one(no_reviews, "TRI.RATING_BAND", stage="pdp")["reason"] == "not_applicable:no_rating"


@pytest.mark.parametrize("rating, band", [(3.44, "<3.5"), (3.46, "3.5-3.9"), (3.96, "4.0-4.7"), (4.7, "4.0-4.7"),
                                          (4.76, "4.8-5.0"), (5, "4.8-5.0")])
def test_rating_bands(rating, band):
    """Ratings are banded as shops display them, rounded to one decimal."""
    assert checks.rating_band(rating) == band


def test_readability_is_gulpease_for_italian_and_flesch_for_english():
    value, evidence = checks.readability({"word_count": 100, "sentence_count": 8, "letter_count": 500}, "it")
    assert value == round(89 - 10 * 5 + 300 * 0.08, 1) == 63.0 and evidence["index"] == "gulpease"
    value, evidence = checks.readability({"word_count": 100, "sentence_count": 5, "syllable_count": 150}, "en")
    assert value == round(206.835 - 1.015 * 20 - 84.6 * 1.5, 1) and evidence["index"] == "flesch"
    _, sampled = checks.readability({"word_count": 40, "sentence_count": 4, "text_sample": "the cat sat on a mat"},
                                    "en")
    assert sampled["syllables_per_word"] == 1.0
    assert [checks.syllables(w) for w in ("shopping", "make", "table", "free")] == [2, 1, 2, 1]
    for doc, language, reason in (({"word_count": 12}, "it", "not_applicable:little_prose"),
                                  ({"word_count": 80, "letter_count": 400}, "de", "unsupported_language")):
        with pytest.raises(checks.NotAssessed, match=reason):
            checks.readability(doc, language)
    observations = checks.observations(full_run())
    assert one(observations, "CCL.READABILITY", stage="home")["value"] == 63.0
    assert one(observations, "CCL.READABILITY", stage="plp")["reason"] == "not_applicable:little_prose"


def test_cta_salience_formula():
    viewport = {"w": 390, "h": 844}
    strong = {"present": True, "contrast": 4.5, "area": 10_000, "above_fold": True}  # WCAG AA, the reference area
    assert checks.cta_salience(strong, [{"primary_like": True, "above_fold": True}], viewport) == 100.0
    aa_button = {"present": True, "contrast": 4.6, "rect": {"w": 200, "h": 44}, "above_fold": True}
    for wide in ({"w": 390, "h": 844}, {"w": 1366, "h": 768}):  # the viewport does not change the area term
        assert checks.cta_salience(aa_button, [], wide) == 97.0 >= 90
    two = [{"primary_like": True, "above_fold": True}] * 2
    weak = {"present": True, "contrast": 3.75, "rect": {"w": 190, "h": 48}, "above_fold": False}
    expected = round(100 * 0.5 * (0.5 * 0.5 + 0.25 * (190 * 48) / 10_000 + 0.25 / 2), 1)
    assert checks.cta_salience(weak, two, viewport) == expected
    assert checks.cta_salience({**weak, "contrast": 2.09}, two, viewport) == round(
        100 * 0.5 * (0.25 * (190 * 48) / 10_000 + 0.25 / 2), 1)
    assert checks.cta_salience({"present": False}, two, viewport) == 0.0
    observations = checks.observations(full_run())
    assert one(observations, "CCL.CTA_SALIENCE", stage="pdp")["value"] == 100.0
    assert one(observations, "CCL.CTA_SALIENCE", stage="cart")["evidence"]["cta"] == "Procedi al checkout"
    assert one(observations, "CCL.PRIMARY_CTA_COUNT", stage="pdp")["value"] == 1


def test_visual_complexity_composite():
    assert checks.visual_complexity({"colorfulness": 100, "edge_density": 0.25, "bytes_per_px": 0.4}) == 100.0
    assert checks.visual_complexity({"colorfulness": 40, "edge_density": 0.05, "bytes_per_px": 0.1}) == 26.8
    observations = checks.observations(run_of([page("home", visual={"edge_density": None})]))
    assert one(observations, "CCL.VISUAL_COMPLEXITY", stage="home")["reason"] == "no_screenshot"


# ---------------------------------------------------------------- site checks


def test_search_checks_and_the_autocomplete_probe():
    observations = checks.observations(full_run())
    width = one(observations, "FAI.SEARCH_WIDTH")
    assert width["value"] == 300 and width["page_id"] is None and width["evidence"]["stages_checked"] == ["home"]
    auto = one(observations, "FAI.SEARCH_AUTOCOMPLETE")
    assert auto["value"] is True and auto["evidence"]["typed"] == "scarpe"
    cases = [({"ran": True, "options": 0}, False, None),
             ({"ran": False, "reason": "no_search_field"}, None, "not_applicable:no_search_field"),
             ({"ran": False, "reason": "guard_refused:personal_field"}, None, "probe:guard_refused:personal_field")]
    for probe, value, reason in cases:
        row = one(checks.observations(run_of([page("home", probes={"search_autocomplete": probe})])),
                  "FAI.SEARCH_AUTOCOMPLETE")
        assert (row["value"], row["reason"]) == (value, reason)
    hidden = run_of([page("home", audit={"search": {"present": False, "width": 0}})])
    assert one(checks.observations(hidden), "FAI.SEARCH_WIDTH")["reason"] == "not_applicable:no_search_field"
    assert one(checks.observations(hidden), "FAI.SEARCH_VISIBLE")["value"] is False


def test_persuasion_counts_distinct_messages():
    countdown = [{"text": "Termina tra 00:14:59", "countdown": True, "deadline": True}]
    scarcity = [{"text": "Solo 2 rimasti", "number": 2}]
    pages = [page("pdp", audit={"persuasion": {"urgency": countdown, "scarcity": scarcity,
                                               "reciprocity": [{"text": "Spedizione gratuita sopra i 59 €"},
                                                               {"text": "Campione omaggio"}]}}),
             page("pdp", n=2, audit={"persuasion": {"urgency": [{**countdown[0], "text": "Termina tra 00:14:51"},
                                                                {"text": "Affrettati", "deadline": False}],
                                                    "scarcity": [{"text": "Solo 3 rimasti", "number": 3}]},
                                     "pdp": {"structured": {"availability": "LimitedAvailability"}}})]
    observations = checks.observations(run_of(pages))
    assert one(observations, "MPI.URGENCY_SIGNALS")["value"] == 1  # ticking digits are one message
    assert one(observations, "MPI.SCARCITY_SIGNALS")["value"] == 2  # "Solo # rimasti" + LimitedAvailability
    reciprocity = one(observations, "MPI.RECIPROCITY")  # the free-shipping threshold lives in PTI
    assert reciprocity["value"] == 2 and set(reciprocity["evidence"]["texts"]) == {"Campione omaggio", "Reso gratuito"}
    site = one(checks.observations(full_run()), "CCL.INFO_SCENT_GENERIC")
    assert site["value"] == 10.0


# ---------------------------------------------------------------- DPR from deception results


@pytest.mark.parametrize("kpi_id, key, test, value", [
    ("DPR.COUNTDOWN_RESET", "countdown", {"assessed": True, "found": True, "reset": True, "decrease_s": 0,
                                          "tolerance_s": 2}, 1.0),
    ("DPR.COUNTDOWN_RESET", "countdown", {"assessed": True, "found": True, "reset": True, "decrease_s": 3,
                                          "tolerance_s": 2}, 0.8),
    ("DPR.COUNTDOWN_RESET", "countdown", {"assessed": True, "found": True, "reset": False, "decrease_s": 6,
                                          "tolerance_s": 2}, 0.0),
    ("DPR.FAKE_LOW_STOCK", "low_stock", {"assessed": True, "found": True, "same_number_products": 3}, 0.8),
    ("DPR.FAKE_LOW_STOCK", "low_stock", {"assessed": True, "found": True, "same_number_products": 2}, 0.4),
    ("DPR.FAKE_LOW_STOCK", "low_stock", {"assessed": True, "found": True, "decreased": True,
                                         "changed_between_visits": True, "same_number_products": 1}, 0.8),
    ("DPR.FAKE_LOW_STOCK", "low_stock", {"assessed": True, "found": True, "changed_between_visits": True,
                                         "same_number_products": 3}, 0.9),
    ("DPR.SNEAK_INTO_BASKET", "sneak_into_basket", {"assessed": True, "unrequested": [{"title": "Protezione",
                                                                                      "addon": True}]}, 1.0),
    ("DPR.SNEAK_INTO_BASKET", "sneak_into_basket", {"assessed": True, "unrequested": [{"title": "Calze"}]}, 0.8),
    ("DPR.PRECHECKED_PAID_ADDONS", "prechecked_paid", {"assessed": True, "items": [{"label": "Assicurazione",
                                                                                   "price_value": 4.9}]}, 1.0),
    ("DPR.CONSENT_ASYMMETRY", "consent", {"assessed": True, "banner": False}, 0.0),
    ("DPR.CONSENT_ASYMMETRY", "consent", {"assessed": True, "banner": True, "clicks_to_reject": None,
                                          "first_layer": {"accept": {"area": 100}, "reject": None}}, 1.0),
    ("DPR.CONSENT_ASYMMETRY", "consent", {"assessed": True, "banner": True, "clicks_to_reject": 2,
                                          "first_layer": {"accept": {"area": 100}, "reject": None}}, 0.8),
    ("DPR.CONSENT_ASYMMETRY", "consent", {"assessed": True, "banner": True, "first_layer": {
        "accept": {"area": 1000}, "reject": {"area": 200}}}, 0.8),
    ("DPR.CONSENT_ASYMMETRY", "consent", {"assessed": True, "banner": True, "first_layer": {
        "accept": {"area": 1000}, "reject": {"area": 400}}}, 0.6),
    ("DPR.CONSENT_ASYMMETRY", "consent", {"assessed": True, "banner": True, "first_layer": {
        "accept": {"area": 1000, "filled": True, "contrast": 6}, "reject": {"area": 900, "filled": False,
                                                                          "contrast": 2.5}}}, 0.3),
    ("DPR.NAGGING_OVERLAYS", "nagging", {"assessed": True, "repeated": {"newsletter": ["plp", "pdp"]}}, 0.6),
    ("DPR.NAGGING_OVERLAYS", "nagging", {"assessed": True, "repeated": {"newsletter": ["home", "plp", "pdp"]}}, 0.8),
    ("DPR.NAGGING_OVERLAYS", "nagging", {"assessed": True, "pages": [{}, {}, {}], "repeated": {}}, 0.0),
    ("DPR.NAGGING_OVERLAYS", "nagging", {"assessed": True, "pages": [], "reappeared_after_close": True}, 0.9),
    ("DPR.PRECHECKED_PAID_ADDONS", "prechecked_paid", {"assessed": True, "items": [
        {"label": "Iscrivimi alla newsletter e ricevi un buono da 5 €", "price_value": 5, "addon": False}]}, 0.0),
    ("DPR.CONSENT_ASYMMETRY", "consent", {"assessed": True, "banner": True, "reject_kind": "close",
                                          "clicks_to_reject": 1, "first_layer": {
                                              "accept": {"area": 6600}, "reject": None, "close": {"area": 576}}}, 0.8),
    ("DPR.CONSENT_ASYMMETRY", "consent", {"assessed": True, "banner": True, "reject_kind": "close",
                                          "clicks_to_reject": 1, "first_layer": {
                                              "accept": {"area": 6600}, "reject": None, "close": {"area": 3000}}}, 0.6),
    ("DPR.CONSENT_ASYMMETRY", "consent", {"assessed": True, "banner": True, "clicks_to_reject": None,
                                          "first_layer": {"accept": {"area": 6600}, "reject": None,
                                                          "close": {"area": 6600}}}, 1.0),
    # "manage" not explored (the click did not run, or its layer could not be read): only the certain part counts
    ("DPR.CONSENT_ASYMMETRY", "consent", {"assessed": True, "banner": True, "clicks_to_reject": None,
                                          "second_layer_checked": False, "second_layer_reason": "failed:TimeoutError",
                                          "first_layer": {"accept": {"area": 100}, "reject": None,
                                                          "manage": {"area": 50}}}, 0.8),
    ("DPR.CONSENT_ASYMMETRY", "consent", {"assessed": True, "banner": True, "clicks_to_reject": None,
                                          "second_layer_checked": True, "first_layer": {
                                              "accept": {"area": 100}, "reject": None, "manage": {"area": 50}}}, 1.0),
    ("DPR.FAKE_LOW_STOCK", "low_stock", {"assessed": True, "found": True, "same_number_products": 0,
                                         "contradictions": [{"signal": "availability"}]}, 0.6),
    ("DPR.FAKE_LOW_STOCK", "low_stock", {"assessed": True, "found": True, "same_number_products": 2,
                                         "contradictions": [{"signal": "inventory_level"}]}, 0.8),
    ("DPR.FAKE_LOW_STOCK", "low_stock", {"assessed": True, "found": True, "changed_between_visits": True,
                                         "contradictions": [{"signal": "availability"}]}, 0.9),
])
def test_deception_results_become_dpr_confidences(kpi_id, key, test, value):
    deception = copy.deepcopy(CLEAN_DECEPTION)
    deception[key] = {**test, "page_id": "mobile-pdp-1", "stage": "pdp"}
    row = one(checks.observations(run_of([page("pdp")], deception=deception)), kpi_id)
    assert row["assessed"] and row["value"] == value and row["page_id"] == "mobile-pdp-1"


def test_clean_deception_results_are_assessed_zero_and_missing_ones_are_not_assessed():
    observations = checks.observations(full_run())
    for kpi in (k for k in PRODUCED if k.producer == "deception"):
        row = one(observations, kpi.id)
        assert row["assessed"] and row["value"] == 0.0, row
    deception = {**CLEAN_DECEPTION, "countdown": {"assessed": False, "reason": "pdp_not_reached"}}
    deception.pop("nagging")
    observations = checks.observations(run_of([page("home")], deception=deception))
    assert one(observations, "DPR.COUNTDOWN_RESET")["reason"] == "pdp_not_reached"
    assert one(observations, "DPR.NAGGING_OVERLAYS")["reason"] == "deception_not_run"
    no_tests = run_of([page("home")])
    no_tests["deception"] = {}
    assert one(checks.observations(no_tests), "DPR.SNEAK_INTO_BASKET")["reason"] == "deception_not_run"


# ---------------------------------------------------------------- deception raw results (pure functions)


def visit(t, countdown=None, stock=None, url="https://shop.example/p1", page_id="v"):
    return {"page_id": page_id, "url": url, "t": t,
            "countdowns": [{"text": "x", "remaining_s": countdown}] if countdown is not None else [],
            "stock": [{"text": "Solo N rimasti", "number": stock}] if stock is not None else []}


def test_countdown_reset_compares_the_decrease_with_the_elapsed_time():
    assert countdown_result(visit(0, 900), visit(6, 900), page_id="p")["reset"] is True
    assert countdown_result(visit(0, 900), visit(6, 894), page_id="p")["reset"] is False  # a fixed deadline
    assert countdown_result(visit(0, 900), visit(30, 880), page_id="p")["reset"] is True  # moved by 10 s
    assert countdown_result(visit(0), visit(6), page_id="p") == {"assessed": True, "found": False, "page_id": "p",
                                                                 "stage": "pdp"}
    assert countdown_result(visit(0, 900), visit(6), page_id="p")["reason"] == "countdown_seen_once"
    assert countdown_result(None, visit(6, 900), page_id="p")["assessed"] is False


def test_low_stock_numbers_across_visits_and_products():
    same = low_stock_result(visit(0, stock=2), visit(6, stock=2), [visit(8, stock=2, url="https://shop.example/p2"),
                                                                    visit(9, stock=2, url="https://shop.example/p3")],
                            page_id="p")
    assert same["same_number_products"] == 3 and not same["changed_between_visits"]
    dropping = low_stock_result(visit(0, stock=5), visit(6, stock=4), [], page_id="p")
    assert dropping["changed_between_visits"] and dropping["decreased"]
    quiet = low_stock_result(visit(0), visit(6), [visit(8, url="https://shop.example/p2")], page_id="p")
    assert quiet["found"] is False and quiet["same_number_products"] == 0


def test_items_are_matched_by_their_words():
    assert same_item("Scarpa da corsa Aurora", "Scarpa da corsa Aurora - taglia 42")
    assert same_item("Assicurazione sull'ordine", "Assicurazione sull'ordine contro furto (+4,90 €)")
    assert not same_item("Spedizione", "Protezione spedizione Premium")
    assert not same_item("Scarpa da corsa Aurora", "Scarpa da trail Vetta")
    assert not same_item("", "Scarpa")


def test_sneak_into_basket_needs_the_added_item_in_the_cart():
    product = page("pdp")
    assert sneak_into_basket(product, None)["reason"] == "no_cart_page"
    assert sneak_into_basket(page("pdp", probes={"add_to_cart": {"executed": False}}), page("cart"))["reason"] == \
        "nothing_added"
    stranger = page("cart", audit={"cart": {"line_items": [{"title": "Altro", "price_value": 9}]}})
    assert sneak_into_basket(product, stranger)["reason"] == "added_item_not_recognised"
    dark = sneak_into_basket(product, page("cart", audit=DARK_CART))
    assert [line["title"] for line in dark["unrequested"]] == ["Protezione spedizione Premium"]
    assert sneak_into_basket(product, page("cart"))["unrequested"] == []


# ---------------------------------------------------------------- empty cart, fees, taxes, strikethrough, counts


EMPTY_CART = {"cart": {"line_items": [], "empty": True, "fees": [], "checkout_cta": {"present": False},
                       "total_value": None, "subtotal_value": None, "shipping_value": None},
              "pdp": {"delivery_text": None}}


def test_an_empty_cart_is_no_base_for_contents_kpis():
    """An add-to-cart click that added nothing (e.g. a size had to be chosen): the empty cart cannot show fees, a
    checkout CTA or a delivery estimate; those rows are not assessed, never a perfect or a zero score."""
    empty = page("cart", audit=EMPTY_CART)
    run = run_of([page("home"), page("plp"), page("pdp"), empty], not_assessable=[
        {"stage": "checkout_entry", "profile": "mobile", "reason": "empty_cart"}])
    run["deception"]["mobile"]["prechecked_paid"] = prechecked_paid(empty, None)
    run["deception"]["mobile"]["sneak_into_basket"] = sneak_into_basket(run["pages"][2], empty)
    observations = checks.observations(run)
    for kpi_id, match in (("PTI.UNEXPLAINED_FEES", {}), ("PTI.FUNNEL_PRICE_DELTA", {}),
                          ("CCL.CTA_SALIENCE", {"stage": "cart"}), ("TRI.DELIVERY_TIME_STATED", {"stage": "cart"}),
                          ("FAI.CART_EDITABLE", {"stage": "cart"}), ("DPR.PRECHECKED_PAID_ADDONS", {}),
                          ("DPR.SNEAK_INTO_BASKET", {})):
        row = one(observations, kpi_id, **match)
        assert not row["assessed"] and row["value"] is None and row["reason"] == "empty_cart", row
    assert one(observations, "CCL.CTA_SALIENCE", stage="pdp")["assessed"]
    assert one(observations, "TRI.DELIVERY_TIME_STATED", stage="pdp")["value"] is True
    # a cart whose lines audit.js could not read but that shows a total is not empty
    unread = page("cart", audit={"cart": {"line_items": [], "total_value": 54.8, "subtotal_value": 49.9,
                                          "shipping_value": 4.9, "fees": []}})
    assert one(checks.observations(run_of([page("pdp"), unread])), "PTI.UNEXPLAINED_FEES")["value"] == 0


def test_fee_words_are_looked_up_in_every_word_of_the_product_page():
    """doc.text_sample is cut at 1000 characters: a fee explained lower on the page is found in doc.words."""
    cart = page("cart", audit={"cart": {"fees": [{"label": "Contributo ambientale RAEE", "value": 1.0}]}})
    told = page("pdp", audit={"doc": {"words": ["contributo", "ambientale", "raee", "scarpa"]}})
    assert one(checks.observations(run_of([told, cart])), "PTI.UNEXPLAINED_FEES")["value"] == 0
    untold = one(checks.observations(run_of([page("pdp"), cart])), "PTI.UNEXPLAINED_FEES")
    assert untold["value"] == 1 and untold["evidence"]["lines"][0]["label"] == "Contributo ambientale RAEE"


def test_taxes_are_no_unexplained_fee_and_a_tax_added_on_top_is_no_price_increase():
    english = {"lexicon_lang": "en", "lang": "en"}
    us_cart = page("cart", audit={**english, "cart": {
        "line_items": [{"title": "Scarpa Aurora", "qty": 1, "price_value": 49.9}], "subtotal_value": 49.9,
        "shipping_value": 4.9, "total_value": 58.79, "fees": [{"label": "Shipping", "value": 4.9},
                                                              {"label": "Estimated tax", "value": 3.99}]}})
    observations = checks.observations(run_of([page("pdp"), us_cart]))
    assert one(observations, "PTI.UNEXPLAINED_FEES")["value"] == 0
    delta = one(observations, "PTI.FUNNEL_PRICE_DELTA")
    assert delta["value"] == 0 and delta["evidence"]["steps"][0]["tax"] == 3.99
    included = page("cart", audit={"cart": {  # "di cui IVA": already in the total, nothing to subtract
        "line_items": [{"title": "Scarpa Aurora", "qty": 1, "price_value": 49.9}], "subtotal_value": 49.9,
        "shipping_value": 4.9, "total_value": 64.8, "fees": [{"label": "Spedizione", "value": 4.9},
                                                             {"label": "di cui IVA 22%", "value": 11.69}]}})
    assert one(checks.observations(run_of([page("pdp"), included])), "PTI.FUNNEL_PRICE_DELTA")["value"] == 20.0
    subtotal_only = page("cart", audit={"cart": {"total_value": None, "subtotal_value": 59.9, "shipping_value": 4.9}})
    step = one(checks.observations(run_of([page("pdp"), subtotal_only])), "PTI.FUNNEL_PRICE_DELTA")
    assert step["value"] == 20.0 and step["evidence"]["steps"][0]["shipping"] == 0  # no shipping in a subtotal


@pytest.mark.parametrize("struck, said, value", [
    ([False], [(False, 1)], True),
    ([True] * 5, [], False),  # five struck card prices, no statement anywhere
    ([True] * 5, [(False, 1)], True),  # a page footnote covers the cards
    ([True] * 2, [(True, 2)], True),  # one statement per card (one text shown twice: count 2)
    ([True] * 2, [(True, 1)], False),
    ([False, True], [(True, 1)], False),  # a struck price outside the cards needs a statement outside them
    ([False, True, True], [(False, 1)], True),
])
def test_strikethrough_statements_cover_cards_through_a_footnote(struck, said, value):
    """The in_card contract (audit.js marks struck prices and statements): compared inside and outside the cards."""
    audit = {"prices": [{"strikethrough": True, "in_card": card} for card in struck],
             "persuasion": {"lowest_price_30d": [{"text": "Prezzo più basso negli ultimi 30 giorni", "in_card": card,
                                                  "count": count} for card, count in said]}}
    row = one(checks.observations(run_of([page("plp", audit=audit)])), "PTI.STRIKETHROUGH_LOWEST30", stage="plp")
    assert row["value"] is value and row["evidence"]["strikethrough_prices"] == len(struck)
    assert "statement_scope" not in row["evidence"]


# what audit.js sends today on a listing of six struck card prices (no in_card marks; equal statements merged)
CAPTURED_STRUCK = [{"text": f"6{n},90 €", "value": 60.9 + n, "currency": "EUR", "strikethrough": True,
                    "kind": "strike", "overlay": False, "area": "main"} for n in range(6)]


@pytest.mark.parametrize("said, value", [
    ([{"text": "Prezzo più basso negli ultimi 30 giorni", "value": None}], True),  # per-card statements, merged
    ([{"text": "* Il prezzo barrato è il prezzo più basso negli ultimi 30 giorni.", "value": None}], True),
    ([], False),
])
def test_strikethrough_without_card_marks_needs_one_statement_on_the_page(said, value):
    audit = {"prices": CAPTURED_STRUCK, "persuasion": {"lowest_price_30d": said}}
    row = one(checks.observations(run_of([page("plp", audit=audit)])), "PTI.STRIKETHROUGH_LOWEST30", stage="plp")
    assert row["value"] is value
    assert row["evidence"].get("statement_scope") == ("unknown" if said else None)


def test_counts_stay_integers():
    observations = checks.observations(full_run())
    for kpi_id in ("PERF.REQUESTS", "PERF.HTTP_ERRORS", "TRI.MIXED_CONTENT", "PERF.LOAF_COUNT"):
        value = one(observations, kpi_id, stage="home")["value"]
        assert type(value) is int, (kpi_id, value)
    again = [page("home"), page("home", n=2, repeat_of="mobile-home-1",
                                network={"requests": 40, "http_errors": 0, "bytes_transfer": 1, "bytes_js": 1,
                                         "third_party_share": 0, "mixed_content": 0, "source": "cdp"})]
    errors = one(checks.observations(run_of(again)), "PERF.HTTP_ERRORS", stage="home")
    assert errors["value"] == 0 and type(errors["value"]) is int and errors["evidence"]["repeats"] == 2


def test_a_dpr_test_without_its_page_carries_the_stage_reason():
    run = run_of([page("home"), page("plp"), page("pdp")], not_assessable=[
        {"stage": "cart", "profile": "mobile", "reason": "add_to_cart_failed"}])
    run["deception"]["mobile"]["sneak_into_basket"] = sneak_into_basket(run["pages"][2], None)
    run["deception"]["mobile"]["prechecked_paid"] = prechecked_paid(None, None)
    observations = checks.observations(run)
    for kpi_id in ("DPR.SNEAK_INTO_BASKET", "DPR.PRECHECKED_PAID_ADDONS"):
        row = one(observations, kpi_id)
        assert (row["assessed"], row["stage"], row["reason"]) == (False, "cart", "add_to_cart_failed")


def test_a_time_text_that_does_not_tick_is_no_urgency():
    race = [{"text": "Usata nella maratona di Berlino chiusa in 2:01:09", "countdown": True, "deadline": True,
             "remaining_s": 7269}]
    run = run_of([page("pdp", audit={"persuasion": {"urgency": race}})])
    assert one(checks.observations(run), "MPI.URGENCY_SIGNALS")["value"] == 1  # no deception verdict: counted
    run["deception"]["mobile"]["countdown"] = {"assessed": True, "found": False, "page_id": "mobile-pdp-1",
                                               "stage": "pdp", "static": [race[0]["text"]]}
    observations = checks.observations(run)
    assert one(observations, "MPI.URGENCY_SIGNALS")["value"] == 0
    assert one(observations, "DPR.COUNTDOWN_RESET")["value"] == 0.0


# ---------------------------------------------------------------- deception: ticking, revisits, nagging


def test_a_countdown_ticks_and_a_static_time_does_not():
    first = [{"text": "Termina tra 00:14:58", "remaining_s": 898}, {"text": "Record 2:01:09", "remaining_s": 7269}]
    later = [{"text": "Termina tra 00:14:56", "remaining_s": 896}, {"text": "Record 2:01:09", "remaining_s": 7269}]
    tick, static = ticking(first, later, 1.9)
    assert [c["remaining_s"] for c in tick] == [898] and [c["remaining_s"] for c in static] == [7269]
    assert ticking(first[:1], [], 1.9) == ([], first[:1])  # gone on the second reading: not a timer


def test_revisits_that_show_nothing_leave_the_tests_not_assessed():
    funnel = visit(0, countdown=898, stock=2)
    blank = [visit(0), visit(12)]
    assert countdown_result(*blank, page_id="p", funnel=funnel)["reason"] == "countdown_not_seen_on_revisit"
    assert low_stock_result(*blank, [], page_id="p", funnel=funnel)["reason"] == "stock_not_seen_on_revisit"
    assert countdown_result(None, visit(12), page_id="p", reason="revisit_bot_challenge")["reason"] == \
        "revisit_bot_challenge"
    static = {**visit(0), "static_countdowns": [{"text": "Record 2:01:09", "remaining_s": 7269}]}
    assert countdown_result(static, visit(12), page_id="p", funnel=funnel) == {
        "assessed": True, "found": False, "page_id": "p", "stage": "pdp", "static": ["Record 2:01:09"]}
    unverified = {**visit(0), "unverified_countdowns": [{"text": "x", "remaining_s": 898}]}
    assert countdown_result(unverified, visit(12), page_id="p")["reason"] == "ticking_not_verified"


def test_variants_of_the_funnel_product_are_not_other_products():
    base = {**visit(0, stock=2), "title": "Scarpa da corsa Aurora", "sku": None}
    variant = {**visit(8, stock=2, url="https://shop.example/p1?color=red"), "title": "Scarpa da corsa Aurora - Rosso"}
    other = {**visit(9, stock=2, url="https://shop.example/p2"), "title": "Scarpa da corsa Onda"}
    assert same_product(base, variant) and not same_product(base, other)
    assert same_product({"sku": "PS-1", "title": "A"}, {"sku": "ps-1", "title": "B"})
    assert not same_product({"sku": "PS-1", "title": "A"}, {"sku": "PS-2", "title": "A"})
    result = low_stock_result(base, base, [variant, other], page_id="p")
    assert result["same_number_products"] == 2 and result["products_checked"] == 2


def overlay(kind, **extra):
    return {"kind": kind, "interrupting": True, "modal": True, "coverage": 0.9, **extra}


def test_nagging_counts_an_overlay_kind_shown_again_after_it_was_closed():
    closed = {"dismiss_overlays": [{"kind": "newsletter", "executed": True}]}
    honest = [page("home", audit={"overlays": [overlay("newsletter")]}, probes=closed),
              page("plp", audit={"overlays": []}), page("pdp", audit={"overlays": []})]
    assert overlay_pages(honest, "mobile")["repeated"] == {}  # shown until closed once: no repetition
    never_closed = [page(s, audit={"overlays": [overlay("newsletter")]}) for s in ("home", "plp", "pdp")]
    assert overlay_pages(never_closed, "mobile")["repeated"] == {}
    nagging = [page("home", audit={"overlays": [overlay("newsletter")]}, probes=closed),
               page("plp", audit={"overlays": [overlay("newsletter")]}, probes=closed),
               page("pdp", audit={"overlays": [overlay("newsletter")]})]
    found = overlay_pages(nagging, "mobile")
    assert found["repeated"] == {"newsletter": ["mobile-home-1", "mobile-plp-1", "mobile-pdp-1"]}
    mixed = [page("home", audit={"overlays": [overlay("age")]}, probes={"dismiss_overlays": [
        {"kind": "age", "executed": True}]}), page("pdp", audit={"overlays": [overlay("newsletter")]})]
    assert overlay_pages(mixed, "mobile")["repeated"] == {}  # different kinds: interruptions, not nagging
    for pages, value in ((honest, 0.0), (never_closed, 0.0), (nagging, 0.8)):
        dec = copy.deepcopy(CLEAN_DECEPTION)
        dec["nagging"] = {"assessed": True, **overlay_pages(pages, "mobile"), "reappeared_after_close": False}
        assert one(checks.observations(run_of(pages, deception=dec)), "DPR.NAGGING_OVERLAYS")["value"] == value


def test_prechecked_options_keep_their_addon_flag_and_need_a_cart_with_contents():
    cart = page("cart", audit={"cart": {"prechecked_paid": [
        {"label": "Assicurazione sull'ordine", "price_value": 4.9, "addon": True},
        {"label": "Iscrivimi alla newsletter e ricevi un buono da 5 €", "price_value": 5, "addon": False}]}})
    result = prechecked_paid(cart, None)
    assert [i["addon"] for i in result["items"]] == [True, False]
    dec = copy.deepcopy(CLEAN_DECEPTION)
    dec["prechecked_paid"] = result
    row = one(checks.observations(run_of([cart], deception=dec)), "DPR.PRECHECKED_PAID_ADDONS")
    assert row["value"] == 1.0 and [i["label"] for i in row["evidence"]["other_prechecked"]] == [
        "Iscrivimi alla newsletter e ricevi un buono da 5 €"]
    assert prechecked_paid(page("cart", audit=EMPTY_CART), None)["reason"] == "empty_cart"
    assert prechecked_paid(page("cart", audit=EMPTY_CART), page("checkout_entry"))["assessed"]


def test_a_revisit_that_is_not_the_product_page_is_not_evidence():
    challenge = {"page_id": "d-1", "classification": {"type": "challenge", "signals": []},
                 "audit": {"doc": {"word_count": 20}}}
    error = {"page_id": "d-2", "classification": {"type": "other", "signals": ["navigation_error"]}, "audit": {}}
    assert deception.visit_failure(challenge) == "bot_challenge"
    assert deception.visit_failure(error) == "navigation_error"
    assert deception.visit_failure({"classification": {"type": "pdp"}, "audit": {}}) == "audit_unavailable"
    assert deception.visit_failure(page("pdp")) is None


# ---------------------------------------------------------------- missing stages, unparsed carts, refined signals


SILENT = {"pdp": {"shipping_text": None}, "persuasion": {"vat_statement": []}}


def test_an_any_site_kpi_that_reads_false_needs_every_one_of_its_stages():
    """Rule b: a missing stage never makes a value worse; true from any reached page stands."""
    reasons = [{"stage": s, "profile": "mobile", "reason": "add_to_cart_failed"} for s in ("cart", "checkout_entry")]
    observations = checks.observations(run_of([page("home"), page("pdp", audit=SILENT)], not_assessable=reasons))
    shipping = one(observations, "PTI.SHIPPING_COST_PRE_CHECKOUT")
    assert (shipping["assessed"], shipping["value"], shipping["reason"], shipping["stage"]) == (
        False, None, "add_to_cart_failed", "cart")
    assert shipping["evidence"]["stages_missing"] == ["cart"] and shipping["evidence"]["stages_checked"] == ["pdp"]
    assert one(observations, "PTI.VAT_STATED")["evidence"]["stages_missing"] == ["cart", "checkout_entry"]
    stated = checks.observations(run_of([page("home"), page("pdp")], not_assessable=reasons))
    assert one(stated, "PTI.SHIPPING_COST_PRE_CHECKOUT")["value"] is True
    assert one(stated, "PTI.VAT_STATED")["value"] is True
    silent_cart = {"cart": {"shipping_value": None}, "persuasion": {"vat_statement": []}}
    seen = run_of([page("pdp", audit=SILENT), page("cart", audit=silent_cart),
                   page("checkout_entry", audit={"persuasion": {"vat_statement": []}})])
    assert one(checks.observations(seen), "PTI.VAT_STATED")["value"] is False  # every stage seen: a finding
    assert one(checks.observations(seen), "PTI.SHIPPING_COST_PRE_CHECKOUT")["value"] is False
    no_contact = run_of([page("home", audit={"trust": {"contact": {"email": False}}})], not_assessable=reasons)
    assert one(checks.observations(no_contact), "TRI.CONTACT_INFO")["value"] is False  # site chrome: false stands
    empty = checks.observations(run_of([page("pdp", audit=SILENT), page("cart", audit=EMPTY_CART)]))
    assert one(empty, "PTI.SHIPPING_COST_PRE_CHECKOUT")["reason"] == "empty_cart"  # an empty cart says nothing


def test_a_cart_with_a_total_but_no_parsed_line_is_not_empty():
    unread = {"cart": {"line_items": [], "total_value": 54.8, "subtotal_value": 49.9, "editable": True,
                       "checkout_cta": {"present": False}}}
    observations = checks.observations(run_of([page("pdp"), page("cart", audit=unread)]))
    assert one(observations, "FAI.CART_EDITABLE", stage="cart")["value"] is True  # controls seen count
    salience = one(observations, "CCL.CTA_SALIENCE", stage="cart")  # a CTA audit.js did not recognise is no 0
    assert (salience["assessed"], salience["reason"]) == (False, "checkout_cta_not_found")
    assert one(observations, "TRI.DELIVERY_TIME_STATED", stage="cart")["assessed"]
    unseen = checks.observations(run_of([page("cart", audit=merge(unread, {"cart": {"editable": False}}))]))
    assert one(unseen, "FAI.CART_EDITABLE", stage="cart")["reason"] == "cart_lines_not_recognised"
    assert sneak_into_basket(page("pdp"), page("cart", audit=unread))["reason"] == "cart_lines_not_recognised"
    assert sneak_into_basket(page("pdp"), page("cart", audit=EMPTY_CART))["reason"] == "empty_cart"
    no_cta = checks.observations(run_of([page("cart", audit={"cart": {"checkout_cta": {"present": False}}})]))
    assert one(no_cta, "CCL.CTA_SALIENCE", stage="cart")["reason"] == "checkout_cta_not_found"


def stocked(number, url, **structured):
    return {**visit(0, stock=number, url=url), "title": url.rsplit("/", 1)[-1], **structured}


P = [f"https://shop.example/p{n}" for n in range(1, 4)]


def low_stock_value(result) -> tuple:
    dec = copy.deepcopy(CLEAN_DECEPTION)
    dec["low_stock"] = {**result, "page_id": "mobile-pdp-1"}
    row = one(checks.observations(run_of([page("pdp")], deception=dec)), "DPR.FAKE_LOW_STOCK")
    return row["value"], row["evidence"]


def test_low_stock_leaves_single_items_out_and_reads_the_shop_structured_data():
    unique = low_stock_result(stocked(1, P[0]), stocked(1, P[0]), [stocked(1, P[1]), stocked(1, P[2])], page_id="p")
    assert unique["same_number_products"] == 0 and unique["single_unit_excluded"]
    value, evidence = low_stock_value(unique)  # vintage, art, refurbished: "solo 1 disponibile" everywhere is true
    assert value == 0.0 and len(evidence["single_unit_products"]) == 3
    sold_out = stocked(2, P[0], offers=1, availability="OutOfStock")
    result = low_stock_result(sold_out, sold_out, [], page_id="p")
    assert [c["signal"] for c in result["contradictions"]] == ["availability"]
    assert low_stock_value(result)[0] == 0.6
    for consistent in ({"offers": 1, "availability": "InStock"}, {"offers": 1, "availability": "LimitedAvailability"},
                       {"offers": 3, "availability": "OutOfStock"}):  # with variants the first offer is not the page's
        quiet = stocked(2, P[0], **consistent)
        assert low_stock_result(quiet, quiet, [], page_id="p")["contradictions"] == []
    counted = stocked(2, P[0], offers=1, inventory_level=7)
    assert [c["signal"] for c in low_stock_result(counted, counted, [], page_id="p")["contradictions"]] == [
        "inventory_level"]
    corroborated = stocked(3, P[0], inventory_level=3)  # its number is the shop's own: out of the shared count
    result = low_stock_result(corroborated, corroborated, [stocked(3, P[1]), stocked(3, P[2])], page_id="p")
    assert result["same_number_products"] == 2 and result["corroborated"][0]["url"] == P[0]
    assert low_stock_value(result)[0] == 0.4
    dropping = low_stock_result(stocked(5, P[0], offers=1, availability="SoldOut"),
                                stocked(4, P[0], offers=1, availability="SoldOut"), [], page_id="p")
    assert low_stock_value(dropping)[0] == 0.9  # two independent signals
    one_visit = low_stock_result(None, stocked(2, P[0]), [], page_id="p", reason="revisit_bot_challenge")
    assert one_visit["assessed"] and low_stock_value(one_visit)[1]["revisit_failure"] == "revisit_bot_challenge"


def test_a_static_time_beside_a_ticking_countdown_is_no_urgency():
    race = {"text": "Record 2:01:09", "countdown": True, "deadline": True, "remaining_s": 7269}
    timer = {"text": "Termina tra 00:14:58", "countdown": True, "deadline": True, "remaining_s": 898}
    run = run_of([page("pdp", audit={"persuasion": {"urgency": [timer, race]}})])
    test = countdown_result({**visit(0, 898), "static_countdowns": [race]}, visit(12, 886), page_id="mobile-pdp-1")
    assert test["found"] and not test["reset"] and test["static"] == ["Record 2:01:09"]
    run["deception"]["mobile"]["countdown"] = test
    urgency = one(checks.observations(run), "MPI.URGENCY_SIGNALS")
    assert urgency["value"] == 1 and urgency["evidence"]["texts"] == ["Termina tra 00:14:58"]


def test_reciprocity_counts_benefits_not_their_wordings():
    texts = [{"text": "Reso gratuito"}, {"text": "Resi gratuiti entro 30 giorni"}, {"text": "Reso sempre gratis"},
             {"text": "Campione omaggio"}]
    row = one(checks.observations(run_of([page("home", audit={"persuasion": {"reciprocity": texts}})])),
              "MPI.RECIPROCITY")
    assert row["value"] == 2 and row["evidence"]["texts"] == ["Reso gratuito", "Campione omaggio"]
    assert checks.benefit("Testo  libero", ()) == "testo libero"


def test_a_customs_charge_is_no_tax_and_other_locales_taxes_are():
    line = [{"title": "Scarpa Aurora", "qty": 1, "price_value": 49.9}]
    customs = page("cart", audit={"cart": {"line_items": line, "subtotal_value": 49.9, "shipping_value": 4.9,
                                           "total_value": 62.8, "fees": [{"label": "Spedizione", "value": 4.9},
                                                                         {"label": "Dazi e tasse doganali",
                                                                          "value": 8.0}]}})
    observations = checks.observations(run_of([page("pdp"), customs]))
    fees = one(observations, "PTI.UNEXPLAINED_FEES")
    assert fees["value"] == 1 and fees["evidence"]["lines"][0]["label"] == "Dazi e tasse doganali"
    assert one(observations, "PTI.FUNNEL_PRICE_DELTA")["value"] == 16.0  # (62.80 - 4.90 - 49.90) / 49.90
    german = page("cart", audit={"cart": {"line_items": line, "subtotal_value": 49.9, "shipping_value": 4.9,
                                          "total_value": 64.29, "fees": [{"label": "Spedizione", "value": 4.9},
                                                                         {"label": "zzgl. MwSt. 19%",
                                                                          "value": 9.49}]}})
    observations = checks.observations(run_of([page("pdp"), german]))
    assert one(observations, "PTI.UNEXPLAINED_FEES")["value"] == 0
    assert one(observations, "PTI.FUNNEL_PRICE_DELTA")["value"] == 0


def test_nagging_is_the_same_overlay_the_crawler_closed_never_a_bar_or_another_promo():
    sale = overlay("promo", text_sample="Saldi -30% su tutto! Iscriviti", buttons=[{"kind": "close", "label": "x"}])
    closed = {"dismiss_overlays": [{"kind": "promo", "key": overlay_key(sale), "executed": True, "gone": True}]}
    app_bar = {"kind": "promo", "interrupting": True, "modal": False, "blocking": False, "coverage": 0.18,
               "text_sample": "Scarica la nostra app"}  # non-modal: the crawler never closes it
    bars = [page("home", audit={"overlays": [sale]}, probes=closed),
            *(page(s, audit={"overlays": [app_bar]}) for s in ("plp", "pdp", "cart"))]
    assert overlay_pages(bars, "mobile")["repeated"] == {}
    other = overlay("promo", text_sample="Spedizione gratuita solo oggi")
    assert overlay_pages([bars[0], page("plp", audit={"overlays": [other]})], "mobile")["repeated"] == {}
    again = overlay("promo", text_sample="Saldi -40% su tutto! Iscriviti")  # the same overlay, digits masked
    found = overlay_pages([bars[0], page("plp", audit={"overlays": [again]})], "mobile")
    assert found["repeated"] == {overlay_key(sale): ["mobile-home-1", "mobile-plp-1"]}
    missed = {"dismiss_overlays": [{**closed["dismiss_overlays"][0], "gone": False}]}  # the click closed another one
    assert overlay_pages([page("home", audit={"overlays": [sale]}, probes=missed),
                          page("plp", audit={"overlays": [sale]})], "mobile")["repeated"] == {}


# ---------------------------------------------------------------- phase C: partial chains, card scope, timer pairs


def test_an_unreadable_checkout_page_does_not_cancel_the_cart_comparison():
    """Rule c: the cart is the base, the checkout entry only extends the chain."""
    unread = page("checkout_entry", audit={})
    unread["audit"] = {}  # audit.js failed there
    run = run_of([page("pdp", audit=SILENT_PDP), page("cart", audit=DARK_CART), unread])
    delta = one(checks.observations(run), "PTI.FUNNEL_PRICE_DELTA")
    assert delta["assessed"] and delta["value"] == 18.6
    assert delta["evidence"]["steps_skipped"] == [{"stage": "checkout_entry", "page_id": "mobile-checkout_entry-1",
                                                   "reason": "audit_unavailable"}]
    unread_cart = page("cart", audit={})
    unread_cart["audit"] = {}
    gone = one(checks.observations(run_of([page("pdp"), unread_cart, page("checkout_entry")])),
               "PTI.FUNNEL_PRICE_DELTA")
    assert (gone["assessed"], gone["reason"]) == (False, "audit_unavailable")  # no base, no comparison


def pdp_visit(page_id, items, cards=(), t=0.0, **structured):
    audit = {"pdp": {"title": page_id, "structured": structured}, "persuasion": {"scarcity": items},
             "products": {"cards": [{"href": href} for href in cards], "cards_count": len(cards)}}
    return deception._product({"page_id": page_id, "url": f"{URL}{page_id}", "audit": audit}, t)


SHARED_CARD = [{"text": "Solo 3 rimasti", "number": 3}]  # a related product's card, no in_card mark


def test_a_low_stock_number_that_may_be_a_card_s_is_never_a_risk_signal():
    visits = [pdp_visit(f"p{n}", SHARED_CARD, cards=["/p7", "/p8", "/p9", "/p10"]) for n in (1, 1, 2, 3)]
    assert visits[0]["card_scope_unknown"] and visits[0]["cards"] == ["/p7", "/p8", "/p9", "/p10"]
    result = low_stock_result(visits[0], visits[1], visits[2:], page_id="p")
    assert result["same_number_products"] == 0 and [p["url"] for p in result["card_scope_unknown"]] == [
        f"{URL}p1", f"{URL}p2", f"{URL}p3"]
    value, evidence = checks._low_stock(result)
    assert value == 0.0 and evidence["card_scope_unknown"]
    # the product's own structured data cannot contradict a card's number
    sold_out = pdp_visit("p1", SHARED_CARD, cards=["/p7"], offers=1, availability="OutOfStock")
    assert low_stock_result(sold_out, sold_out, [], page_id="p")["contradictions"] == []
    # a carousel that changes between the contexts is no change of stock; the same cards changing number is
    moved = low_stock_result(pdp_visit("p1", SHARED_CARD, cards=["/p7"]),
                             pdp_visit("p1", [{"text": "Solo 5 rimasti", "number": 5}], cards=["/p8"], t=12),
                             [], page_id="p")
    assert not moved["changed_between_visits"] and moved["change_scope_unknown"]
    same = low_stock_result(pdp_visit("p1", [{"text": "Solo 5 rimasti", "number": 5}], cards=["/p7"]),
                            pdp_visit("p1", SHARED_CARD, cards=["/p7"], t=12), [], page_id="p")
    assert same["changed_between_visits"] and same["decreased"]
    # with audit.js's in_card mark the card's item is left out; the product's own is kept and scored
    marked = [{**SHARED_CARD[0], "in_card": True}, {"text": "Solo 2 rimasti", "number": 2, "in_card": False}]
    own = pdp_visit("p1", marked, cards=["/p7"])
    assert own["stock"] == [{"text": "Solo 2 rimasti", "number": 2}] and "card_scope_unknown" not in own
    only_card = pdp_visit("p1", [{**SHARED_CARD[0], "in_card": True}], cards=["/p7"])
    assert only_card["stock"] == [] and low_stock_result(only_card, only_card, [], page_id="p")["found"] is False
    no_cards = pdp_visit("p1", SHARED_CARD)  # a page without cards has no card to be unsure about
    assert "card_scope_unknown" not in no_cards and no_cards["stock"][0]["number"] == 3


def timer(text, remaining):
    return {"text": text, "remaining_s": remaining}


SALE, CUTOFF = "I saldi finiscono tra {}", "Ordina entro {} per la consegna domani"


def test_countdowns_are_compared_timer_by_timer_and_never_across_deadlines():
    first = {**visit(0), "countdowns": [timer(SALE.format("00:59:50"), 3590)]}
    second = {**visit(12), "countdowns": [timer(CUTOFF.format("02:00:00"), 7200), timer(SALE.format("00:59:38"), 3578)]}
    result = countdown_result(first, second, page_id="p")
    assert result["found"] and not result["reset"] and result["decrease_s"] == 12
    assert result["second"]["text"] == SALE.format("00:59:38")
    dec = copy.deepcopy(CLEAN_DECEPTION)
    dec["countdown"] = result
    assert one(checks.observations(run_of([page("pdp")], deception=dec)), "DPR.COUNTDOWN_RESET")["value"] == 0.0
    # two different deadlines, one per visit: no pair, not assessed
    lone = {**visit(12), "countdowns": [timer(CUTOFF.format("02:00:00"), 7200)]}
    unpaired = countdown_result(first, lone, page_id="p")
    assert (unpaired["assessed"], unpaired["reason"]) == (False, "countdown_not_paired")
    # one timer each whose words agree (the text around it changed a little): paired by position
    reworded = {**visit(12), "countdowns": [timer("Saldi: finiscono tra 00:59:38", 3578)]}
    assert countdown_result(first, reworded, page_id="p")["reset"] is False
    # several pairs: the worst is reported, every pair is in the evidence
    both = {**visit(0), "countdowns": [timer(CUTOFF.format("02:00:00"), 7200), timer(SALE.format("00:59:50"), 3590)]}
    restarted = {**visit(12), "countdowns": [timer(CUTOFF.format("01:59:48"), 7188),
                                             timer(SALE.format("00:59:50"), 3590)]}
    worst = countdown_result(both, restarted, page_id="p")
    assert worst["reset"] and worst["decrease_s"] == 0 and len(worst["pairs"]) == 2
    dec["countdown"] = worst
    row = one(checks.observations(run_of([page("pdp")], deception=dec)), "DPR.COUNTDOWN_RESET")
    assert row["value"] == 1.0 and len(row["evidence"]["pairs"]) == 2


def test_a_cart_row_decided_because_the_checkout_was_unreadable_says_why():
    unread = page("checkout_entry", classification={"type": "other", "signals": ["navigation_error"]}, audit={})
    observations = checks.observations(run_of([page("cart"), unread]))
    for kpi_id, value in (("FAI.GUEST_CHECKOUT", True), ("FAI.FORCED_ACCOUNT", False)):
        rows_ = rows(observations, kpi_id)
        assert [(o["stage"], o["value"]) for o in rows_] == [("cart", value)]
        assert rows_[0]["evidence"] == {"guest_option": True, "checkout_entry": {
            "page_id": "mobile-checkout_entry-1", "reason": "navigation_error"}}
    plain = rows(checks.observations(run_of([page("cart")])), "FAI.GUEST_CHECKOUT")
    assert plain[0]["evidence"] == {"guest_option": True, "checkout_entry": {"page_id": None, "reason": "not_reached"}}
    # a checkout click that ended on Chrome's error page keeps that page as "extra": the stage's reason says why
    error = page("checkout_entry", classification={"type": "other", "signals": ["navigation_error"]}, audit={})
    error["stage"] = "extra"
    missing = [{"stage": "checkout_entry", "profile": "mobile", "reason": "navigation_error"}]
    for kpi_id, value in (("FAI.GUEST_CHECKOUT", True), ("FAI.FORCED_ACCOUNT", False)):
        rows_ = rows(checks.observations(run_of([page("cart"), error], not_assessable=missing)), kpi_id)
        assert [(o["stage"], o["value"]) for o in rows_] == [("cart", value)]
        assert rows_[0]["evidence"]["checkout_entry"] == {"page_id": None, "reason": "navigation_error"}


@pytest.mark.parametrize("pdp, value, reason, gated", [
    ({"structured": {"offers": 1, "availability": "OutOfStock"}}, None, "not_applicable:out_of_stock", None),
    ({"add_to_cart": {**CTA, "enabled": False}, "variant_groups": None}, None, "not_applicable:out_of_stock", None),
    ({"add_to_cart": {**CTA, "enabled": False}, "variant_groups": [{"label": "Taglia", "kind": "buttons"}]}, True,
     None, True),  # disabled until a size is picked: reachable
    ({"add_to_cart": {**CTA, "enabled": True}}, True, None, None),
])
def test_a_sold_out_product_has_no_cta_to_reach_and_a_variant_gate_is_no_obstacle(pdp, value, reason, gated):
    observations = checks.observations(run_of([page("pdp", audit={"pdp": pdp})]))
    row = one(observations, "FAI.PDP_CTA_ABOVE_FOLD", stage="pdp")
    assert (row["value"], row["reason"], row["evidence"].get("disabled_until_variant")) == (value, reason, gated)
    salience = one(observations, "CCL.CTA_SALIENCE", stage="pdp")
    assert salience["reason"] == reason and (salience["value"] is None) is (reason is not None)


def test_a_banner_whose_accept_was_not_recognised_is_not_scored():
    landing = {"overlays": [{"kind": "consent", "consent_like": True, "buttons": [
        {"label": "Ho capito", "kind": "other", "area": 6000}, {"label": "Rifiuta", "kind": "reject", "area": 6000}]}]}
    layer = deception._layer(landing)
    assert layer["accept"] is None and layer["kinds"] == ["other", "reject"]
    dec = copy.deepcopy(CLEAN_DECEPTION)
    dec["consent"] = {"assessed": True, "banner": True, "page_id": "mobile-home-1", "stage": "home",
                      "first_layer": layer}
    row = one(checks.observations(run_of([page("home")], deception=dec)), "DPR.CONSENT_ASYMMETRY")
    assert (row["assessed"], row["reason"], row["page_id"]) == (False, "accept_not_recognised", "mobile-home-1")
    dec["consent"]["first_layer"] = {**layer, "reject": None, "kinds": ["close"]}  # a notice with only a close
    assert one(checks.observations(run_of([page("home")], deception=dec)), "DPR.CONSENT_ASYMMETRY")["value"] == 0.0


def test_an_overlay_closed_in_a_lost_tab_does_not_repeat_in_the_next_one():
    closed = {"dismiss_overlays": [{"kind": "newsletter", "key": "newsletter", "executed": True, "gone": True}]}
    pages = [page("home", audit={"overlays": [overlay("newsletter")]}, probes=closed),
             page("plp", audit={"overlays": [overlay("newsletter")]}, probes={"tab": 2}),
             page("pdp", audit={"overlays": [overlay("newsletter")]}, probes={"tab": 2})]
    assert overlay_pages(pages, "mobile")["repeated"] == {}  # sessionStorage starts over in the new tab
    pages[1]["probes"]["dismiss_overlays"] = closed["dismiss_overlays"]
    assert overlay_pages(pages, "mobile")["repeated"] == {"newsletter": ["mobile-plp-1", "mobile-pdp-1"]}
