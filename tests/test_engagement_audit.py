"""audit_shop end to end on the fixture shop (headless Chromium, local server only), the crawler's pieces and steps on
a scripted shop, and the deception visits on scripted tabs (no browser)."""

import copy
import functools
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jev_ultrafast.browser import StalePage
from jev_ultrafast.engagement import audit as audit_module
from jev_ultrafast.engagement import checks, deception
from jev_ultrafast.engagement.audit import audit_shop
from jev_ultrafast.engagement.collectors import PageCollector
from jev_ultrafast.engagement.crawler import (
    Crawl,
    Stop,
    Tab,
    category_word,
    discover_funnel,
    is_listing,
    is_product,
    listing_candidates,
    product_candidates,
    unavailable,
)
from jev_ultrafast.engagement.kpis import KPIS
from jev_ultrafast.engagement.lexicon import compile_lexicon, lexicon_all, lexicon_for
from jev_ultrafast.engagement.profiles import new_context
from jev_ultrafast.engagement.safety import GUARD_HREF, CheckoutGuard
from jev_ultrafast.engagement.schemas import STAGES
from jev_ultrafast.engagement.scoring import score_run
from jev_ultrafast.engagement.settings import EngagementSettings
from jev_ultrafast.engagement.store import RunStore
from jev_ultrafast.engagement.transport import DirectTransport

LX = compile_lexicon(lexicon_for("it"))
SHOP = "https://shop.example/"
DPR = [k for k in KPIS if k.startswith("DPR.") and KPIS[k].producer == "deception"]
PURPOSES = {"consent_reject", "consent_accept", "consent_manage", "consent_close", "search_probe", "dismiss_overlay",
            "select_variant", "add_to_cart", "guest_checkout", "checkout_entry"}
PAY = re.compile("|".join(f"(?:{p})" for key in ("pay_now", "place_order") for p in lexicon_all()[key]), re.I)


# ---------------------------------------------------------------- end to end on the fixture shop


@pytest.fixture(scope="module")
def quick():
    """Shorter settle windows; 1 s still sees the dark shop's newsletter modal (800 ms) and promo bar (1.2 s)."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(PageCollector, "net_quiet_s", 1.0)
        patch.setattr(PageCollector, "lcp_quiet_s", 1.0)
        yield


def audited(chromium, shop_server, root, path, **settings):
    store = RunStore(root)
    before = len(shop_server.requests)
    messages = []
    run_id = audit_shop(EngagementSettings(url=shop_server.url(path), **settings), store=store,
                        transport_factory=lambda: DirectTransport(chromium.ws_url), progress=messages.append)
    return {"store": store, "run_id": run_id, "run": store.load(run_id), "steps": store.read_steps(run_id),
            "requests": shop_server.requests[before:], "messages": messages}


@pytest.fixture(scope="module")
def clean(chromium, shop_server, tmp_path_factory, quick):
    return audited(chromium, shop_server, tmp_path_factory.mktemp("clean"), "shop/index.html")


@pytest.fixture(scope="module")
def dark(chromium, shop_server, tmp_path_factory, quick):
    return audited(chromium, shop_server, tmp_path_factory.mktemp("dark"), "shop/index.html?dark=1",
                   profiles=["desktop"])


def dpr(result, profile):
    return {o["kpi_id"]: o for o in result["run"]["observations"] if o["kpi_id"] in DPR and o["profile"] == profile}


def test_the_clean_audit_reaches_every_stage_on_both_profiles(clean):
    run = clean["run"]
    assert run["status"] == "complete" and not run["errors"] and not run["not_assessable"] and run["finished_at"]
    for profile in ("mobile", "desktop"):
        assert [(p["page_id"], p["classification"]["type"]) for p in run["pages"] if p["profile"] == profile] == [
            (f"{profile}-{stage}-1", "checkout" if stage == "checkout_entry" else stage) for stage in STAGES]
        assert run["settings"]["applied_profiles"][profile]["name"] == profile
    settings = run["settings"]
    assert settings["anchors_version"] == "anchors.v1" and settings["profiles_version"] == "profiles.v1"
    assert settings["browser"]["transport"] == "direct" and settings["browser"]["product"]
    assert any("mobile: add to cart" in m for m in clean["messages"]) and clean["messages"][-1] == "done: complete"
    assert clean["store"].path(clean["run_id"]).joinpath("snapshots", "mobile-pdp-1.json").exists()


def test_the_audit_never_submits_pays_or_types_personal_data(clean, dark):
    for result in (clean, dark):
        assert not [r for r in result["requests"] if r["method"] == "POST" or r["path"].startswith("/pay")]
        steps = result["steps"]
        assert steps and {s["purpose"] for s in steps} <= PURPOSES
        assert all(s["status"] == "executed" and not PAY.search(s["label"]) for s in steps)
        assert all(s["operation"] == "CLICK" or s["purpose"] == "search_probe" for s in steps)
        assert not [s for s in steps if "checkout.html" in s["url_before"]]  # nothing done on the checkout page
        assert {s["source"] for s in steps} == {"crawler", "deception"}  # the deception contexts' own clicks
        assert {s["purpose"] for s in steps if s["source"] == "deception"} <= {
            "consent_reject", "consent_accept", "consent_manage", "consent_close", "dismiss_overlay"}
        for profile in result["run"]["settings"]["profiles"]:
            mine = [s["purpose"] for s in steps if s["profile"] == profile]
            assert mine.count("add_to_cart") == 1, mine
            assert mine.count("checkout_entry") + mine.count("guest_checkout") == 1, mine
            assert [p["stage"] for p in result["run"]["pages"] if p["profile"] == profile][-1] == "checkout_entry"


def test_consent_choice_search_probe_and_added_item_are_recorded(clean, dark):
    home = next(p for p in clean["run"]["pages"] if p["page_id"] == "mobile-home-1")
    assert home["consent"]["choice"] == "reject" and home["consent"]["label"] == "Rifiuta tutti"
    probe = home["probes"]["search_autocomplete"]
    assert probe["ran"] and probe["typed"] == "scarpe" and probe["options"] > 0 and probe["latency_ms"] <= 800
    product = next(p for p in clean["run"]["pages"] if p["page_id"] == "desktop-pdp-1")
    added = product["probes"]["add_to_cart"]
    assert added["executed"] and (added["title"], added["price"]) == ("Scarpa da corsa Aurora", 49.9)
    assert probe["method"] == "insertText" and isinstance(probe["typing_ms"], int)
    dark_home = dark["run"]["pages"][0]  # auto: no first-layer reject, so "manage" and the reject behind it
    assert dark_home["consent"] == {**dark_home["consent"], "choice": "reject", "via": "manage", "clicks": 2,
                                    "blocking": True, "reject_offered": False}
    assert [s["purpose"] for s in dark["steps"] if s["stage"] == "home"][:2] == ["consent_manage", "consent_reject"]
    dark_listing = next(p for p in dark["run"]["pages"] if p["stage"] == "plp")
    assert [(c["kind"], c["executed"]) for c in dark_listing["probes"]["dismiss_overlays"]] == [("newsletter", True)]
    dark_product = next(p for p in dark["run"]["pages"] if p["stage"] == "pdp")
    closed = dark_product["probes"]["add_to_cart"]["overlays"]
    assert [(c["kind"], c["executed"]) for c in closed] == [("newsletter", True)]
    assert dark_product["probes"]["add_to_cart"]["cart_empty"] is False


def test_dark_patterns_fire_on_the_dark_shop_and_stay_silent_on_the_clean_one(clean, dark):
    for profile in ("mobile", "desktop"):
        quiet = dpr(clean, profile)
        assert set(quiet) == set(DPR) and all(o["assessed"] and o["value"] == 0 for o in quiet.values()), quiet
    loud = dpr(dark, "desktop")
    assert all(o["assessed"] and o["value"] >= 0.5 for o in loud.values()), loud
    assert loud["DPR.COUNTDOWN_RESET"]["evidence"]["elapsed_s"] >= deception.MIN_GAP_S
    assert loud["DPR.FAKE_LOW_STOCK"]["evidence"]["same_number_products"] == 3
    assert [line["title"] for line in loud["DPR.SNEAK_INTO_BASKET"]["evidence"]["unrequested"]] == [
        "Protezione spedizione Premium"]
    assert loud["DPR.CONSENT_ASYMMETRY"]["evidence"]["clicks_to_reject"] == 2
    assert loud["DPR.NAGGING_OVERLAYS"]["evidence"]["reappeared_after_close"] is True
    repeated = loud["DPR.NAGGING_OVERLAYS"]["evidence"]["repeated"]  # one overlay: its kind and text (overlay_key)
    assert [(key.split(":")[0], pages) for key, pages in repeated.items()] == [
        ("newsletter", ["desktop-plp-1", "desktop-pdp-1"])]
    tests = dark["run"]["deception"]["desktop"]
    assert tests["errors"] == [] and [v["context"] for v in tests["low_stock"]["visits"]] == [1, 2]


def test_the_clean_shop_gets_a_published_readiness_score_and_the_dark_one_a_lower_one(clean, dark):
    scores = score_run(clean["run"])
    assert scores["overall"]["ers"]["published"] and scores["overall"]["ers"]["score"] > 60
    for result, guest, via in ((clean, True, "guest"), (dark, False, "checkout_cta")):
        # the clean cart's "Continua come ospite" leads to the guest step; the dark cart has only the account step
        account = {k["id"]: k["value"] for k in score_run(result["run"])["overall"]["kpis"]
                   if k["id"] in ("FAI.GUEST_CHECKOUT", "FAI.FORCED_ACCOUNT")}
        assert account == {"FAI.GUEST_CHECKOUT": guest, "FAI.FORCED_ACCOUNT": not guest}, account
        carts = [p for p in result["run"]["pages"] if p["stage"] == "cart"]
        assert {p["probes"]["checkout_entry"]["via"] for p in carts} == {via}
    assert scores["overall"]["dpr"]["score"] == 0 and set(scores["profiles"]) == {"mobile", "desktop"}
    worse = score_run(dark["run"])["overall"]
    assert worse["ers"]["published"] and worse["ers"]["score"] < scores["overall"]["ers"]["score"]
    withheld = {k["id"]: k["reason"] for k in worse["kpis"] if k["id"] in ("MPI.SCARCITY_SIGNALS",
                                                                          "MPI.URGENCY_SIGNALS")}
    assert withheld == {"MPI.SCARCITY_SIGNALS": "credit_withheld:DPR.FAKE_LOW_STOCK",
                        "MPI.URGENCY_SIGNALS": "credit_withheld:DPR.COUNTDOWN_RESET"}


def test_a_bot_challenge_makes_every_stage_not_assessable(chromium, shop_server, tmp_path, quick):
    result = audited(chromium, shop_server, tmp_path, "shop/challenge.html", profiles=["desktop"])
    run = result["run"]
    assert run["status"] == "failed" and [p["stage"] for p in run["pages"]] == ["extra"]
    assert {(n["stage"], n["reason"]) for n in run["not_assessable"]} == {(s, "bot_challenge") for s in STAGES}
    assert not [o for o in run["observations"] if o["assessed"]] and not result["steps"]
    assert not score_run(run)["overall"]["ers"]["published"]


def test_max_pages_and_requested_stages_bound_the_funnel(chromium, shop_server, tmp_path, quick):
    result = audited(chromium, shop_server, tmp_path, "shop/index.html", profiles=["desktop"], max_pages=2,
                     stages=["home", "plp", "pdp"])
    run = result["run"]
    assert [p["stage"] for p in run["pages"]] == ["home", "plp"] and run["status"] == "partial"
    assert {n["stage"]: n["reason"] for n in run["not_assessable"]} == {
        "pdp": "max_pages", "cart": "not_requested", "checkout_entry": "not_requested"}
    assert {s["purpose"] for s in result["steps"]} <= {"consent_reject", "search_probe"}


# ---------------------------------------------------------------- audit orchestration without a browser


class FakeTransport:
    kind, lossy = "fake", False

    def __init__(self, fail_on=None):
        self.fail_on, self.closed, self.calls = fail_on, False, []

    def call(self, method, session_id=None, *, timeout=30.0, **params):
        self.calls.append(method)
        if method == self.fail_on:
            raise RuntimeError(f"{method} refused")
        return {"product": "FakeChrome/1"} if method == "Browser.getVersion" else {}

    def events(self, session_id=None, method_prefix=None):
        return []

    def close(self):
        self.closed = True


class FakeChrome:
    closed = False

    def close(self):
        self.closed = True


def test_a_failing_browser_leaves_a_failed_run_and_nothing_open(tmp_path):
    transport, chrome = FakeTransport(fail_on="Target.createBrowserContext"), FakeChrome()
    store = RunStore(tmp_path)
    run_id = audit_shop(EngagementSettings(url=SHOP), store=store, transport_factory=lambda: (transport, chrome))
    run = store.load(run_id)
    assert run["status"] == "failed" and "Target.createBrowserContext refused" in run["errors"][0]
    assert transport.closed and chrome.closed and run["finished_at"]
    assert run["settings"]["browser"]["product"] == "FakeChrome/1"

    def broken():
        raise TimeoutError("no browser")
    run = store.load(audit_shop(EngagementSettings(url=SHOP), store=store, transport_factory=broken))
    assert run["status"] == "failed" and "no browser" in run["errors"][0]


def test_a_context_that_does_not_close_keeps_the_collected_pages(tmp_path, monkeypatch):
    page = {"page_id": "mobile-home-1", "profile": "mobile", "stage": "home", "url": SHOP, "final_url": SHOP,
            "classification": {"type": "home", "signals": []}, "audit": {"doc": {"word_count": 40}}}

    def stuck(transport, context):
        raise TimeoutError("Target.disposeBrowserContext")

    class Collector:
        applied_profile = None

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(audit_module, "new_context", lambda transport: "ctx")
    monkeypatch.setattr(audit_module, "close_context", stuck)
    monkeypatch.setattr(audit_module, "PageCollector", Collector)
    monkeypatch.setattr(audit_module, "discover_funnel", lambda *a, **k: ([dict(page)], [
        {"stage": s, "profile": "mobile", "kpi_id": None, "reason": "not_requested"} for s in STAGES[1:]]))
    monkeypatch.setattr(audit_module, "run_deception_tests", lambda *a, **k: {"version": "x", "errors": []})
    store = RunStore(tmp_path)
    run = store.load(audit_shop(EngagementSettings(url=SHOP, profiles=["mobile"], stages=["home"]), store=store,
                                transport_factory=FakeTransport))
    assert [p["page_id"] for p in run["pages"]] == ["mobile-home-1"] and run["status"] == "complete"
    assert run["warnings"] == ["mobile: browser context not closed: TimeoutError: Target.disposeBrowserContext"]


# ---------------------------------------------------------------- crawler pieces


GUARD = CheckoutGuard(SHOP, lexicon_for("it"))


def record(audit, url=SHOP, kind="home"):
    return {"page_id": "p", "url": url, "final_url": url, "classification": {"type": kind}, "audit": audit}


def test_listing_candidates_are_category_links_ranked_by_evidence():
    home = record({"nav": {"categories": [
        {"label": "Accedi", "href": "/account", "visible": True},
        {"label": "Novità", "href": "/novita", "visible": True},
        {"label": "Scarpe", "href": "/categoria/scarpe", "visible": True},
        {"label": "Carrello", "href": "/carrello", "visible": True},
        {"label": "Altro sito", "href": "https://other.example/c/x", "visible": True},
        {"label": "Home", "href": SHOP, "visible": True},
        {"label": "Saldi", "href": "/saldi/", "visible": False},
        {"label": "Scarpe", "href": "/categoria/scarpe#top", "visible": True},
        {"label": "Pagamento", "href": "/checkout", "visible": True},
    ]}, "jsonld": [{"@type": "SiteNavigationElement", "name": ["Borse"], "url": ["/collezioni/borse"]}]})
    assert listing_candidates(home, GUARD, LX) == [
        f"{SHOP}categoria/scarpe", f"{SHOP}collezioni/borse", f"{SHOP}saldi/", f"{SHOP}novita"]


def test_product_candidates_come_from_cards_then_item_lists():
    listing = record({"products": {"cards": [{"href": "/p/1"}, {"href": "/p/1"}, {"href": "https://x.example/p/2"},
                                             {"href": "/p/3"}]},
                      "jsonld": [{"@type": "ItemList", "itemListElement": [{"url": "/p/4"}, {"item": {"url": "/p/5"}},
                                                                           {"item": "/p/6"}]}]},
                     url=f"{SHOP}c/scarpe", kind="plp")
    assert product_candidates(listing, GUARD) == [f"{SHOP}p/{n}" for n in (1, 3, 4, 5, 6)]
    assert product_candidates(None, GUARD) == []


def test_the_probe_word_comes_from_a_category_label():
    labels = ["Il mio account", "Tè e tisane", "Scarpe da corsa"]
    audit = {"nav": {"categories": [{"label": label} for label in labels]}}
    assert category_word(audit, LX) == "tisane"
    assert category_word({"nav": {"categories": [{"label": "Accedi"}]}}, LX) is None


def test_page_kinds_accept_listings_and_products_by_their_evidence():
    assert is_listing(record({}, kind="plp"))
    assert is_listing(record({"products": {"main_group": 8}}, kind="other"))
    assert not is_listing(record({"products": {"main_group": 8}}, kind="pdp"))
    buyable = {"pdp": {"add_to_cart": {"present": True}, "price": {"value": 10}}}
    assert is_product(record(buyable, kind="other")) and not is_product(record(buyable, kind="cart"))
    assert not is_product(record({"pdp": {"add_to_cart": {"present": True}}}, kind="other"))


def test_pick_prefers_the_exact_label_then_the_lexicon_then_the_nearest():
    page = {"scroll": {"y": 100}, "actions": [
        {"id": "e1", "kind": "click", "label": "Aggiungi al carrello", "rect": {"y": 700, "h": 40}},
        {"id": "e2", "kind": "click", "label": "Aggiungi al carrello", "rect": {"y": 300, "h": 40}},
        {"id": "e3", "kind": "click", "label": "Aggiungi Scarpa Aurora al carrello", "rect": {"y": 0, "h": 40}},
        {"id": "e4", "kind": "fill", "label": "Cerca", "rect": {"y": 0, "h": 40}},
    ]}
    assert Tab.pick(page, LX, labels=["aggiungi al  CARRELLO"], key="add_to_cart", near={"y": 380, "h": 40})["id"] \
        == "e2"
    assert Tab.pick(page, LX, labels=["Acquista"], key="add_to_cart")["id"] == "e1"
    assert Tab.pick(page, LX, labels=["Cerca"], kinds=("fill",))["id"] == "e4"
    assert Tab.pick(page, LX, labels=["Paga ora"]) is None


class FakeBrowser:
    session, target = "s1", "t1"

    def __init__(self):
        self.acted = []

    def act(self, action, page, text=None):
        self.acted.append(action["id"])


class FakeCollector:
    store = run_id = None
    locale = "it"

    def __init__(self):
        self.transport = FakeTransport()


def test_tab_actions_pass_the_guard_before_the_browser():
    tab = Tab(FakeCollector(), GUARD, profile="mobile")
    tab.browser = FakeBrowser()
    page = {"url": f"{SHOP}cart", "actions": [], "guards": {}}
    pay = {"id": "e1", "kind": "click", "label": "Paga ora", "role": "button", "node": 1}
    refused = tab.act(pay, page, "cart", purpose="checkout_entry", stage="cart")
    assert refused == (False, "guard_refused:forbidden_label:pay_now")
    outside = {"url": "https://other.example/x", "actions": [], "guards": {}}
    add = {"id": "e2", "kind": "click", "label": "Aggiungi al carrello", "role": "button", "node": 2}
    assert tab.act(add, outside, "pdp", purpose="add_to_cart", stage="pdp")[1] == "guard_refused:external_page"
    assert tab.browser.acted == []
    assert tab.act(add, {**page, "url": f"{SHOP}p/1"}, "pdp", purpose="add_to_cart", stage="pdp") == (True, None)
    assert tab.browser.acted == ["e2"]


class FailingCollector(FakeCollector):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def open(self, url):
        raise self.error


@pytest.mark.parametrize("error, reason", [(TimeoutError("slow"), "timeout"),
                                           (KeyError("boom"), "error: KeyError: 'boom'")])
def test_a_funnel_that_cannot_start_marks_every_stage(error, reason):
    settings = EngagementSettings(url=SHOP, stages=["home", "plp", "pdp", "cart"])
    pages, missing = discover_funnel(FailingCollector(error), settings, profile="mobile", guard=GUARD)
    assert pages == []
    assert {m["stage"]: m["reason"] for m in missing} == {"home": reason, "plp": reason, "pdp": reason,
                                                          "cart": reason, "checkout_entry": "not_requested"}


# ---------------------------------------------------------------- crawler steps on a scripted shop (no browser)


def button(label, kind):
    return {"label": label, "text": label, "kind": kind, "area": 4000}


def banner(*buttons, blocking=True):
    return {"kind": "consent", "consent_like": True, "interrupting": blocking, "modal": blocking,
            "blocking": blocking, "coverage": 0.9 if blocking else 0.1, "buttons": list(buttons)}


NEWSLETTER = {"kind": "newsletter", "interrupting": True, "modal": True, "coverage": 0.6,
              "buttons": [button("Chiudi", "close"), button("Iscriviti", "other")]}


class ScriptedShop:
    """One page of a fake shop: the overlays it shows, other observed controls, and what a click does (a consent or
    close button removes its overlay; layers maps a label to the overlay that replaces it, e.g. "manage")."""

    def __init__(self, overlays=(), controls=(), lang="it", layers=None, pdp=None):
        self.overlays, self.controls, self.lang = [copy.deepcopy(o) for o in overlays], list(controls), lang
        self.layers, self.pdp = layers or {}, pdp or {}

    def audit(self):
        return {"lexicon_lang": self.lang, "doc": {"word_count": 10}, "overlays": copy.deepcopy(self.overlays),
                "pdp": copy.deepcopy(self.pdp)}

    def actions(self):
        labels = [b["label"] for o in self.overlays for b in o["buttons"]] + self.controls
        return [{"id": f"e{i}", "kind": "click", "label": label, "role": "button", "node": i}
                for i, label in enumerate(labels)]

    def react(self, label):
        for overlay in self.overlays:
            if any(b["label"] == label and b["kind"] != "other" for b in overlay["buttons"]):
                self.overlays.remove(overlay)
                if label in self.layers:
                    self.overlays.append(copy.deepcopy(self.layers[label]))
                return


class ScriptedBrowser:
    session, target = "s1", "t1"

    def __init__(self, shop):
        self.shop, self.acted, self.stale = shop, [], 0

    def observe(self, screenshot=True):
        return {"url": SHOP, "actions": self.shop.actions(), "guards": {}, "scroll": {"y": 0}}

    def act(self, action, page, text=None):
        if self.stale:  # nothing sent, as browser.act raises before any Input.dispatch*
            self.stale -= 1
            raise StalePage("Target changed or is covered. Observe again.")
        self.acted.append(action["label"])
        self.shop.react(action["label"])


class ScriptedCollector(FakeCollector):
    applied_profile = None

    def __init__(self, shop):
        super().__init__()
        self.shop, self.browser = shop, ScriptedBrowser(shop)

    def open(self, url):
        return self.browser

    def close(self, browser):
        pass

    def audit(self, browser):
        return self.shop.audit()

    def load_page(self, browser, url, *, stage, page_id):
        return {"page_id": page_id, "profile": "mobile", "stage": stage, "url": url, "final_url": url,
                "classification": {"type": stage, "signals": []}, "audit": self.shop.audit()}


@pytest.fixture
def scripted(monkeypatch):
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "quiet")

    def make(shop, policy="auto"):
        tab = Tab(ScriptedCollector(shop), GUARD, profile="mobile", policy=policy)
        tab.browser = tab.collector.browser
        return tab, {"page_id": "mobile-home-1", "url": SHOP, "classification": {"type": "home"},
                     "audit": shop.audit()}
    return make


ACCEPT_MANAGE = banner(button("Accetta tutti", "accept"), button("Personalizza", "manage"))
SECOND_LAYER = banner(button("Salva preferenze", "manage"), button("Rifiuta tutti", "reject"))


@pytest.mark.parametrize("policy", ["reject", "auto"])
def test_consent_without_a_first_layer_reject_goes_through_manage(scripted, policy):
    tab, home = scripted(ScriptedShop([ACCEPT_MANAGE], layers={"Personalizza": SECOND_LAYER}), policy)
    result = tab.consent(home, policy, stage="home")
    assert (result["choice"], result["via"], result["clicks"]) == ("reject", "manage", 2)
    assert tab.browser.acted == ["Personalizza", "Rifiuta tutti"]


def test_an_italian_x_close_is_a_reject_and_reject_never_accepts(scripted):
    with_x = banner(button("Accetta tutti", "accept"), button("Chiudi", "close"))
    tab, home = scripted(ScriptedShop([with_x]), "reject")
    result = tab.consent(home, "reject", stage="home")
    assert (result["choice"], result["via"], tab.browser.acted) == ("reject", "close", ["Chiudi"])
    english = banner(button("Accept all", "accept"), button("Close", "close"))
    tab, home = scripted(ScriptedShop([english], lang="en"), "reject")
    result = tab.consent(home, "reject", stage="home")
    assert (result["choice"], result["reason"], tab.browser.acted) == ("none", "no_reject_control", [])
    tab, home = scripted(ScriptedShop([english], lang="en"), "auto")
    assert tab.consent(home, "auto", stage="home")["choice"] == "accept" and tab.browser.acted == ["Accept all"]
    quiet = banner(button("Accept all", "accept"), blocking=False)
    tab, home = scripted(ScriptedShop([quiet], lang="en"), "auto")
    assert tab.consent(home, "auto", stage="home")["reason"] == "not_blocking" and tab.browser.acted == []


def test_a_stale_page_is_observed_once_more_and_never_more(scripted):
    tab, home = scripted(ScriptedShop(controls=["Aggiungi al carrello"]))
    tab.browser.stale = 1
    clicked = tab.click(home, purpose="add_to_cart", stage="pdp", labels=["Aggiungi al carrello"], clear=True)
    assert clicked["executed"] and clicked["stale_reobserved"] == 1 and tab.browser.acted == ["Aggiungi al carrello"]
    # the same control on the same document is never sent again, executed or not
    again = tab.click(home, purpose="add_to_cart", stage="pdp", labels=["Aggiungi al carrello"])
    assert (again["executed"], again["reason"], tab.browser.acted) == (False, "sent_earlier", ["Aggiungi al carrello"])
    tab, home = scripted(ScriptedShop(controls=["Aggiungi al carrello"]))
    tab.browser.stale = 3
    clicked = tab.click(home, purpose="add_to_cart", stage="pdp", labels=["Aggiungi al carrello"])
    assert not clicked["executed"] and clicked["reason"].startswith("not_executed:") and tab.browser.stale == 1


def crawl_of(shop, **settings):
    collector = ScriptedCollector(shop)
    crawl = Crawl(collector, EngagementSettings(url=SHOP, **settings), profile="mobile", guard=GUARD)
    crawl.tab.browser = collector.browser
    return crawl


def test_home_closes_a_newsletter_before_the_consent_and_the_probe(scripted):
    shop = ScriptedShop([banner(button("Accetta tutti", "accept"), button("Rifiuta tutti", "reject"), blocking=False),
                         NEWSLETTER])
    crawl = crawl_of(shop)
    home = crawl.home()
    assert crawl.tab.browser.acted == ["Chiudi", "Rifiuta tutti"]
    assert home["probes"]["dismiss_overlays"] == [{"kind": "newsletter", "key": "newsletter", "executed": True,
                                                   "reason": None, "label": "Chiudi", "gone": True}]
    assert (home["consent"]["choice"], home["consent"]["via"]) == ("reject", "first_layer")
    assert home["probes"]["search_autocomplete"]["reason"] == "no_search_field"


def test_no_click_under_a_blocking_banner_that_reject_leaves(scripted):
    pdp = {"add_to_cart": {"present": True, "label": "Aggiungi al carrello"}}
    shop = ScriptedShop([banner(button("Accetta e continua", "accept"))], controls=["Aggiungi al carrello"], pdp=pdp)
    crawl = crawl_of(shop, consent="reject")
    product = {"page_id": "mobile-pdp-1", "url": SHOP, "classification": {"type": "pdp"}, "audit": shop.audit()}
    with pytest.raises(Stop) as stop:
        crawl.add_to_cart(product)
    assert (stop.value.stage, stop.value.reason) == ("cart", "consent_blocking") and crawl.tab.browser.acted == []
    assert product["probes"]["add_to_cart"]["reason"] == "consent_blocking"


@pytest.mark.parametrize("group, acted, reason", [
    ({"label": "Taglia", "kind": "buttons", "selected": False, "first_available": "41"}, ["41", "Aggiungi"], None),
    ({"label": "Taglia", "kind": "buttons", "selected": True, "first_available": "40"}, ["Aggiungi"], None),
    ({"label": "Taglia", "kind": "buttons", "options": ["40", "41"]}, ["Aggiungi"], None),  # older audit.js
    ({"label": "Taglia", "kind": "buttons", "selected": False, "first_available": None}, [], "variant_required"),
])
def test_a_required_variant_is_picked_once_before_adding(scripted, group, acted, reason):
    pdp = {"add_to_cart": {"present": True, "label": "Aggiungi"}, "price": {"value": 49.9}, "variant_groups": [group]}
    shop = ScriptedShop(controls=["40", "41", "Aggiungi"], pdp=pdp)
    crawl = crawl_of(shop)
    product = {"page_id": "mobile-pdp-1", "url": SHOP, "classification": {"type": "pdp"}, "audit": shop.audit()}
    if reason:
        with pytest.raises(Stop, match=reason):
            crawl.add_to_cart(product)
    else:
        assert crawl.add_to_cart(product) == "quiet"
    assert crawl.tab.browser.acted == acted
    if group.get("selected") is False:
        assert product["probes"]["add_to_cart"]["variant"]["executed"] is (reason is None)


class MapCollector(FakeCollector):
    applied_profile = None

    def __init__(self, pages):
        super().__init__()
        self.pages, self.loads = pages, []

    def open(self, url):
        return FakeBrowser()

    def close(self, browser):
        pass

    def load_page(self, browser, url, *, stage, page_id):
        self.loads.append(url)
        kind, audit = self.pages[url]
        return {"page_id": page_id, "profile": "mobile", "stage": stage, "url": url, "final_url": url,
                "classification": {"type": kind, "signals": []}, "audit": copy.deepcopy(audit)}


SITE = {
    SHOP: ("home", {"nav": {"categories": [{"label": "Novità", "href": "/categoria/novita", "visible": True},
                                           {"label": "Scarpe", "href": "/categoria/scarpe", "visible": True}]},
                    "doc": {"word_count": 50}}),
    f"{SHOP}categoria/novita": ("other", {"doc": {"word_count": 50}, "products": {"cards_count": 0}}),
    f"{SHOP}categoria/scarpe": ("plp", {"doc": {"word_count": 50}, "filters": {"controls": 4},
                                        "products": {"main_group": 24, "cards": [{"href": "/prodotto/guida"},
                                                                                 {"href": "/prodotto/aurora"}]}}),
    f"{SHOP}prodotto/guida": ("other", {"doc": {"word_count": 50}}),
    f"{SHOP}prodotto/aurora": ("pdp", {"doc": {"word_count": 50}, "pdp": {"add_to_cart": {"present": True}}}),
}


def test_repeats_reload_only_accepted_pages():
    collector = MapCollector(SITE)
    settings = EngagementSettings(url=SHOP, stages=["home", "plp", "pdp"], repeats=2, profiles=["mobile"])
    pages, missing = discover_funnel(collector, settings, profile="mobile", guard=GUARD)
    assert [(p["page_id"], p["stage"], p.get("repeat_of")) for p in pages] == [
        ("mobile-home-1", "home", None), ("mobile-home-2", "home", "mobile-home-1"),
        ("mobile-plp-1", "extra", None), ("mobile-plp-2", "plp", None), ("mobile-plp-3", "plp", "mobile-plp-2"),
        ("mobile-pdp-1", "extra", None), ("mobile-pdp-2", "pdp", None), ("mobile-pdp-3", "pdp", "mobile-pdp-2")]
    stages = {p["page_id"]: p["stage"] for p in pages}
    assert all(stages[p["repeat_of"]] == p["stage"] in STAGES for p in pages if p.get("repeat_of"))
    assert collector.loads.count(f"{SHOP}categoria/novita") == 1 and collector.loads.count(f"{SHOP}prodotto/guida") == 1
    assert {m["stage"]: m["reason"] for m in missing} == {"cart": "not_requested", "checkout_entry": "not_requested"}
    run = {"settings": {"profiles": ["mobile"]}, "pages": pages, "not_assessable": missing, "deception": {}}
    filters = [o for o in checks.observations(run) if o["kpi_id"] == "FAI.PLP_FILTERS"]
    assert [(o["page_id"], o["value"], o["evidence"]["repeats"]) for o in filters] == [("mobile-plp-2", 4, 2)]


def test_repeats_leave_one_page_per_later_stage_in_the_budget():
    cards = {"main_group": 24, "cards": [{"href": "/prodotto/aurora"}]}
    direct = {**SITE, SHOP: ("home", {"nav": {"categories": [{"label": "Scarpe", "href": "/categoria/scarpe"}]}}),
              f"{SHOP}categoria/scarpe": ("plp", {"products": cards})}
    settings = EngagementSettings(url=SHOP, stages=["home", "plp", "pdp"], repeats=5, max_pages=8)
    pages, missing = discover_funnel(MapCollector(direct), settings, profile="mobile", guard=GUARD)
    assert [p["stage"] for p in pages] == ["home"] * 5 + ["plp"] * 2 + ["pdp"]
    assert pages[5]["notes"] == ["1 of 4 repeats loaded (max_pages or a failed load)"]
    assert not [m for m in missing if m["stage"] in ("home", "plp", "pdp")]


# ---------------------------------------------------------------- deception visits on a scripted tab (no browser)


def pdp_record(page_id, countdown=None, stock=None, kind="pdp", title="Scarpa da corsa Aurora"):
    urgency = [{"text": f"Termina tra {countdown}", "countdown": True, "remaining_s": int(countdown.split(":")[-1]) +
                60 * int(countdown.split(":")[-2])}] if countdown else []
    scarcity = [{"text": f"Solo {stock} rimasti", "number": stock}] if stock else []
    return {"page_id": page_id, "profile": "mobile", "stage": "pdp", "url": f"{SHOP}p/1", "final_url": f"{SHOP}p/1",
            "classification": {"type": kind, "signals": []},
            "audit": {"doc": {"word_count": 30}, "pdp": {"title": title},
                      "persuasion": {"urgency": urgency, "scarcity": scarcity}}}


class ScriptedVisits:
    """Deception tabs whose product loads come from a script: a list of records in load order (home loads get a
    plain home page), and fresh audits from a second list (the countdown's second reading)."""

    def __init__(self, loads, audits=()):
        self.loads, self.audits = list(loads), list(audits)

    def live(self):
        script = self

        class Live(deception._Live):
            def tab(self):
                tab = ScriptedVisitTab(script)
                self.tabs.append(tab)
                return tab
        return Live


class ScriptedVisitTab:
    def __init__(self, script):
        self.script = script

    def load(self, url, *, stage, page_id):
        if url == SHOP:
            return {"page_id": page_id, "classification": {"type": "home"}, "audit": {"doc": {"word_count": 30}}}
        return {**self.script.loads.pop(0), "page_id": page_id}

    def fresh_audit(self):
        return self.script.audits.pop(0) if self.script.audits else {}

    def dismiss(self, audit, record, *, stage):
        return []

    def lost(self):
        pass

    def close(self):
        pass


@pytest.fixture
def no_waits(monkeypatch):
    monkeypatch.setattr(deception, "MIN_GAP_S", 0.0)
    monkeypatch.setattr(deception, "TICK_S", 0.0)


def deception_run(monkeypatch, script):
    monkeypatch.setattr(deception, "_Live", script.live())
    funnel = [{"page_id": "mobile-home-1", "profile": "mobile", "stage": "home", "url": SHOP,
               "audit": {"doc": {"word_count": 30}}},
              pdp_record("mobile-pdp-1", countdown="00:14:58", stock=2)]
    return deception.run_deception_tests(None, EngagementSettings(url=SHOP), funnel, profile="mobile", guard=GUARD)


def test_revisits_met_by_a_bot_challenge_are_not_a_clean_result(monkeypatch, no_waits):
    challenge = pdp_record("x", kind="challenge")
    result = deception_run(monkeypatch, ScriptedVisits([challenge, challenge]))
    for key in ("countdown", "low_stock"):
        assert (result[key]["assessed"], result[key]["reason"]) == (False, "revisit_bot_challenge"), result[key]
    assert [e.split(" (")[0] for e in result["errors"]] == ["product visit 1: bot_challenge",
                                                           "product visit 2: bot_challenge"]
    run = {"settings": {"profiles": ["mobile"]}, "pages": [], "not_assessable": [], "deception": {"mobile": result}}
    rows = {o["kpi_id"]: o for o in checks.observations(run) if o["kpi_id"].startswith("DPR.")}
    assert not rows["DPR.COUNTDOWN_RESET"]["assessed"] and not rows["DPR.FAKE_LOW_STOCK"]["assessed"]


def test_only_a_ticking_countdown_is_compared_between_visits(monkeypatch, no_waits):
    ticking_audit = pdp_record("a", countdown="00:14:57")["audit"]
    result = deception_run(monkeypatch, ScriptedVisits(
        [pdp_record("v1", countdown="00:14:58", stock=2), pdp_record("v2", countdown="00:14:58", stock=2)],
        [ticking_audit] * 3))  # the second reading of each visit (and the nagging test's look)
    assert result["countdown"]["found"] is True and result["countdown"]["first"]["remaining_s"] == 898
    assert [v["context"] for v in result["low_stock"]["visits"]] == [1, 2]
    static_audit = pdp_record("a", countdown="00:14:58")["audit"]
    result = deception_run(monkeypatch, ScriptedVisits(
        [pdp_record("v1", countdown="00:14:58"), pdp_record("v2", countdown="00:14:58")],
        [static_audit] * 3))
    assert result["countdown"] == {"assessed": True, "found": False, "page_id": "mobile-pdp-1", "stage": "pdp",
                                   "static": ["Termina tra 00:14:58"]}


# ---------------------------------------------------------------- phase B: uncertain clicks, consent gating, status


class CoveredBrowser(ScriptedBrowser):
    """covered: label -> overlay kind that covers it (StalePage, nothing sent, while such an overlay is shown);
    uncertain: labels whose click raises after it may have reached the page. sent lists every click sent."""

    def __init__(self, shop, *, covered=None, uncertain=()):
        super().__init__(shop)
        self.covered, self.uncertain, self.sent = covered or {}, set(uncertain), []

    def act(self, action, page, text=None):
        label = action["label"]
        if any(o["kind"] == self.covered.get(label) for o in self.shop.overlays):
            raise StalePage("Target changed or is covered. Observe again.")
        self.sent.append(label)
        if label in self.uncertain:
            raise TimeoutError("Input.dispatchMouseEvent timed out")
        self.acted.append(label)
        self.shop.react(label)


def covered_crawl(shop, **browser):
    crawl = crawl_of(shop)
    crawl.tab.browser = crawl.collector.browser = CoveredBrowser(shop, **browser)
    return crawl


def test_a_close_that_may_have_reached_the_page_is_never_sent_again(scripted):
    """The add-to-cart is covered by the newsletter; its close timed out (it may have been delivered): the second
    attempt after clear_way does not send it again, and the covered add-to-cart fails instead of being forced."""
    pdp = {"add_to_cart": {"present": True, "label": "Aggiungi al carrello"}}
    shop = ScriptedShop([NEWSLETTER], controls=["Aggiungi al carrello"], pdp=pdp)
    crawl = covered_crawl(shop, covered={"Aggiungi al carrello": "newsletter"}, uncertain={"Chiudi"})
    product = {"page_id": "mobile-pdp-1", "url": SHOP, "classification": {"type": "pdp"}, "audit": shop.audit()}
    with pytest.raises(Stop, match="add_to_cart_failed"):
        crawl.add_to_cart(product)
    assert crawl.tab.browser.sent == ["Chiudi"]
    probe = product["probes"]["add_to_cart"]  # the re-observe's clear_way pass is recorded too
    assert [(o["executed"], o["reason"]) for o in probe["overlays"]] == [(False, "failed:TimeoutError"),
                                                                         (False, "uncertain_earlier")]
    assert probe["reason"].startswith("not_executed:") and probe["stale_reobserved"] == 1
    assert crawl.tab.act({"label": "Chiudi"}, {"url": SHOP}, "pdp", purpose="dismiss_overlay", stage="pdp") == (
        False, "uncertain_earlier")


@pytest.mark.parametrize("browser, sent, closes", [
    ({"covered": {"Chiudi": "consent"}}, ["Rifiuta tutti", "Chiudi"], [False, True]),  # nothing sent: once more
    ({"uncertain": {"Chiudi"}}, ["Chiudi", "Rifiuta tutti"], [False]),  # may have been delivered: never again
])
def test_home_closes_again_only_what_the_banner_kept_from_being_sent(scripted, browser, sent, closes):
    shop = ScriptedShop([banner(button("Accetta tutti", "accept"), button("Rifiuta tutti", "reject")), NEWSLETTER])
    crawl = covered_crawl(shop, **browser)
    home = crawl.home()
    assert crawl.tab.browser.sent == sent and home["consent"]["choice"] == "reject"
    assert [c["executed"] for c in home["probes"]["dismiss_overlays"]] == closes


def bar(*buttons, **extra):
    """A small consent bar: not modal, under 15% of the viewport, the page centre reachable."""
    return {"kind": "consent", "consent_like": True, "interrupting": False, "modal": False, "blocking": False,
            "coverage": 0.08, "buttons": list(buttons), **extra}


def test_auto_opens_manage_only_for_a_banner_in_the_way(scripted):
    small = bar(button("Accetta tutti", "accept"), button("Personalizza", "manage"))
    layers = {"Personalizza": SECOND_LAYER}
    tab, home = scripted(ScriptedShop([small], layers=layers), "auto")
    result = tab.consent(home, "auto", stage="home")
    assert (result["choice"], result["reason"], result["interrupting"], tab.browser.acted) == (
        "none", "not_blocking", False, [])
    tab, home = scripted(ScriptedShop([small], layers=layers), "reject")  # the user asked to reject: full chain
    assert tab.consent(home, "reject", stage="home")["via"] == "manage"
    assert tab.browser.acted == ["Personalizza", "Rifiuta tutti"]
    modal = {**small, "modal": True, "interrupting": True, "coverage": 0.3}  # in the way, the centre still reachable
    tab, home = scripted(ScriptedShop([modal], layers=layers), "auto")
    result = tab.consent(home, "auto", stage="home")
    assert (result["choice"], result["via"], result["clicks"], result["interrupting"]) == ("reject", "manage", 2, True)
    with_x = bar(button("Accetta tutti", "accept"), button("Personalizza", "manage"), button("Chiudi", "close"))
    tab, home = scripted(ScriptedShop([with_x], layers=layers), "auto")
    result = tab.consent(home, "auto", stage="home")
    assert (result["choice"], result["via"], result["clicks"], tab.browser.acted) == ("reject", "close", 1, ["Chiudi"])


@pytest.mark.parametrize("policy, acted, choice, reason", [
    ("auto", ["Personalizza", "Accetta tutti"], "accept", "no reject path and the banner blocks the page"),
    ("reject", ["Personalizza"], "none", "no_reject_control"),
])
def test_after_manage_the_layer_shown_decides_and_its_close_is_no_refusal(scripted, policy, acted, choice, reason):
    """A first layer in the way (modal, not blocking) opens a blocking preference centre with no reject: auto accepts
    there (the funnel would stop otherwise), reject leaves it; the centre's "Chiudi" is "back", never a refusal."""
    first = {**bar(button("Accetta tutti", "accept"), button("Personalizza", "manage")), "modal": True,
             "interrupting": True, "coverage": 0.3}
    centre = banner(button("Accetta tutti", "accept"), button("Salva preferenze", "manage"), button("Chiudi", "close"))
    tab, home = scripted(ScriptedShop([first], layers={"Personalizza": centre}), policy)
    result = tab.consent(home, policy, stage="home")
    assert tab.browser.acted == acted and (result["choice"], result["reason"]) == (choice, reason)
    assert result["via"] == "manage" if choice == "accept" else "via" not in result
    assert result["blocking"] is True  # the layer shown last


class ConsentTab:
    """A deception tab for the consent test: the landing shows banner; the "manage" click returns clicked; fresh
    audits come from audits (then {})."""

    def __init__(self, landing, clicked, audits=()):
        self.landing, self.clicked, self.audits, self.clicks = landing, clicked, list(audits), []

    def load(self, url, *, stage, page_id):
        return {"page_id": page_id, "classification": {"type": "home", "signals": []}, "audit": self.landing}

    def click(self, record, **kwargs):
        self.clicks.append(kwargs)
        return {"origin": 1.0, "label": "Manage", **self.clicked}

    def await_effect(self, origin, **kwargs):
        return "quiet"

    def fresh_audit(self):
        return self.audits.pop(0) if self.audits else {}

    def consent(self, record, policy, *, stage, after_manage=False):
        self.after_manage = after_manage
        return {}


def consent_audit(*buttons):
    return {"lexicon_lang": "en", "doc": {"word_count": 30}, "overlays": [banner(*buttons)]}


LANDING = consent_audit(button("Accept all", "accept"), button("Manage", "manage"))


@pytest.mark.parametrize("clicked, audits, checked, reason, value", [
    ({"executed": False, "reason": "failed:TimeoutError"}, [], False, "failed:TimeoutError", 0.8),
    ({"executed": False, "reason": "guard_refused:x"}, [], False, "guard_refused:x", 0.8),
    ({"executed": True, "reason": None}, [{}], False, "no_second_layer", 0.8),
    ({"executed": True, "reason": None}, [LANDING], False, "manage_had_no_effect", 0.8),
    ({"executed": True, "reason": None}, [consent_audit(button("Save", "manage"))], True, None, 1.0),
    ({"executed": True, "reason": None}, [consent_audit(button("Reject all", "reject"))], True, None, 0.8),
])
def test_a_manage_layer_that_was_not_read_is_scored_on_the_certain_part_only(clicked, audits, checked, reason, value):
    live = deception._Live(None, EngagementSettings(url=SHOP), GUARD, profile="mobile", store=None, run_id=None,
                           say=lambda message: None, errors=[])
    home = {"page_id": "mobile-home-1", "stage": "home", "audit": LANDING}
    result = deception._consent(live, ConsentTab(LANDING, clicked, audits), home)
    assert result["second_layer_checked"] is checked and result.get("second_layer_reason") == reason
    assert result["manage_click"]["executed"] is clicked["executed"]
    run = {"settings": {"profiles": ["mobile"]}, "pages": [], "not_assessable": [],
           "deception": {"mobile": {"consent": result}}}
    row = next(o for o in checks.observations(run) if o["kpi_id"] == "DPR.CONSENT_ASYMMETRY")
    assert row["value"] == value
    if not checked:
        assert row["evidence"]["manage_click"]["executed"] is clicked["executed"]


def test_nagging_without_a_base_is_not_assessed(monkeypatch):
    monkeypatch.setattr(deception, "_Live", ScriptedVisits([]).live())
    home = {"page_id": "mobile-home-1", "profile": "mobile", "stage": "home", "url": SHOP,
            "audit": {"doc": {"word_count": 30}}}
    settings = EngagementSettings(url=SHOP)
    result = deception.run_deception_tests(None, settings, [home], profile="mobile", guard=GUARD)
    nagging = result["nagging"]
    assert (nagging["assessed"], nagging["stage"], nagging["stage_missing"], nagging["pages_checked"]) == (
        False, "plp", True, 1)
    stopped = [{"stage": s, "profile": "mobile", "reason": "bot_challenge"} for s in STAGES[1:]]
    run = {"settings": {"profiles": ["mobile"]}, "pages": [home], "not_assessable": stopped,
           "deception": {"mobile": result}}
    row = next(o for o in checks.observations(run) if o["kpi_id"] == "DPR.NAGGING_OVERLAYS")
    assert (row["assessed"], row["reason"]) == (False, "bot_challenge")
    nothing = deception.run_deception_tests(None, settings, [], profile="mobile", guard=GUARD)["nagging"]
    assert (nothing["assessed"], nothing["stage"], nothing["reason"]) == (False, "home", "no_pages")


def test_a_stage_the_funnel_only_passes_through_does_not_make_the_run_partial():
    settings = EngagementSettings(url=SHOP, profiles=["mobile"], stages=["home", "pdp"])
    pages = [{"profile": "mobile", "stage": stage} for stage in ("home", "pdp")]
    missing = [{"stage": "plp", "profile": "mobile", "reason": "not_found"},
               *({"stage": s, "profile": "mobile", "reason": "not_requested"} for s in ("cart", "checkout_entry"))]
    assert audit_module._status({"pages": pages, "not_assessable": missing, "errors": []}, settings) == "complete"
    lost = [*missing, {"stage": "pdp", "profile": "mobile", "reason": "not_found"}]
    assert audit_module._status({"pages": pages[:1], "not_assessable": lost, "errors": []}, settings) == "partial"


class CartShop(ScriptedShop):
    """A cart page: a guest option (forms.guest_option) and a recognised checkout CTA."""

    def audit(self):
        return {**super().audit(), "forms": {"guest_option": True},
                "cart": {"line_items": [{"title": "Scarpa", "price_value": 49.9}], "total_value": 49.9,
                         "checkout_cta": {"present": True, "label": "Procedi al checkout"}}}


class LinkBrowser(ScriptedBrowser):
    """Observed controls with the href snapshot.js reports (CheckoutGuard's guard tuple)."""

    def __init__(self, shop, hrefs):
        super().__init__(shop)
        self.hrefs = hrefs

    def observe(self, screenshot=True):
        page = super().observe(screenshot)
        page["guards"] = {str(a["node"]): [None] * GUARD_HREF + [self.hrefs.get(a["label"])] for a in page["actions"]}
        return page


class CheckoutCollector(ScriptedCollector):
    def collect(self, browser, *, stage, page_id):
        return {"page_id": page_id, "profile": "mobile", "stage": stage, "url": f"{SHOP}checkout",
                "final_url": f"{SHOP}checkout", "classification": {"type": "checkout", "signals": []},
                "audit": {"doc": {"word_count": 10}}}


@pytest.mark.parametrize("controls, hrefs, effects, acted, via", [
    (["Continua come ospite", "Procedi al checkout"], {"Continua come ospite": "/checkout?guest=1"},
     {"Continua come ospite": "navigated"}, ["Continua come ospite"], "guest"),
    (["Traccia gli ordini come ospite", "Procedi al checkout"], {"Traccia gli ordini come ospite": "/ordini/traccia"},
     {"Procedi al checkout": "navigated"}, ["Procedi al checkout"], "checkout_cta"),  # not a checkout URL
    (["Continua come ospite", "Procedi al checkout"], {}, {"Continua come ospite": "quiet",
                                                         "Procedi al checkout": "navigated"},
     ["Continua come ospite", "Procedi al checkout"], "checkout_cta"),  # a toggle: the checkout CTA follows
])
def test_the_checkout_entry_follows_the_cart_guest_path_when_there_is_one(monkeypatch, controls, hrefs, effects,
                                                                          acted, via):
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: effects.get(self.browser.acted[-1], "quiet"))
    shop = CartShop(controls=controls)
    collector = CheckoutCollector(shop)
    collector.browser = LinkBrowser(shop, hrefs)
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD)
    crawl.tab.browser = collector.browser
    cart = {"page_id": "mobile-cart-1", "url": f"{SHOP}cart", "final_url": f"{SHOP}cart",
            "classification": {"type": "cart"}, "audit": shop.audit()}
    crawl.checkout(cart)
    assert collector.browser.acted == acted and cart["probes"]["checkout_entry"]["via"] == via
    assert [p["stage"] for p in crawl.pages] == ["checkout_entry"]
    guest = cart["probes"]["checkout_entry"]["guest"]
    if guest.get("executed"):  # where the guest control led: its observed href (None: a control without one)
        assert guest["href"] == hrefs.get("Continua come ospite")


def test_a_guest_click_that_may_have_navigated_stops_the_funnel(scripted):
    shop = CartShop(controls=["Continua come ospite", "Procedi al checkout"])
    crawl = covered_crawl(shop, uncertain={"Continua come ospite"})
    cart = {"page_id": "mobile-cart-1", "url": f"{SHOP}cart", "classification": {"type": "cart"},
            "audit": shop.audit()}
    with pytest.raises(Stop, match="checkout_click_failed"):
        crawl.checkout(cart)
    assert crawl.tab.browser.sent == ["Continua come ospite"]  # the checkout CTA is not clicked on an unknown page


# ---------------------------------------------------------------- phase C: one click per control and document, error
# pages, cart acceptance, sold-out products, page budget, consent on a small bar


class StuckShop(ScriptedShop):
    """A shop whose stuck controls execute but change nothing (a close that does not close, a "manage" that opens
    nothing)."""

    def __init__(self, *args, stuck=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.stuck = set(stuck)

    def react(self, label):
        if label not in self.stuck:
            super().react(label)


def pdp_of(shop):
    return {"page_id": "mobile-pdp-1", "url": SHOP, "final_url": SHOP, "classification": {"type": "pdp"},
            "audit": shop.audit()}


def test_a_close_without_effect_is_not_sent_again_by_a_later_clear_way(scripted):
    """The newsletter's "Chiudi" executes but the newsletter stays and covers the add-to-cart: the stale re-observe's
    clear_way pass does not send "Chiudi" again on the same document, and says so in the probe."""
    pdp = {"add_to_cart": {"present": True, "label": "Aggiungi al carrello"}}
    shop = StuckShop([NEWSLETTER], controls=["Aggiungi al carrello"], pdp=pdp, stuck={"Chiudi"})
    crawl = covered_crawl(shop, covered={"Aggiungi al carrello": "newsletter"})
    product = pdp_of(shop)
    with pytest.raises(Stop, match="add_to_cart_failed"):
        crawl.add_to_cart(product)
    assert crawl.tab.browser.sent == ["Chiudi"]
    closes = [(o["executed"], o["reason"], o.get("gone")) for o in product["probes"]["add_to_cart"]["overlays"]]
    assert closes == [(True, None, False), (False, "sent_earlier", None)]


def test_home_closes_again_only_the_overlays_whose_close_was_never_sent(scripted):
    """A promo whose close executes without effect and a newsletter covered by the consent banner: after the consent
    click only the newsletter's decline is tried again; the promo's "Chiudi" is never sent twice."""
    promo = {"kind": "promo", "interrupting": True, "modal": True, "coverage": 0.5, "text_sample": "Saldi",
             "buttons": [button("Chiudi", "close")]}
    news = {**NEWSLETTER, "text_sample": "Iscriviti", "buttons": [button("No grazie", "decline")]}
    consent = {**bar(button("Accetta", "accept"), button("Rifiuta", "reject")), "interrupting": True,
               "coverage": 0.2}
    shop = StuckShop([promo, news, consent], stuck={"Chiudi"})
    crawl = covered_crawl(shop, covered={"No grazie": "consent"})
    home = crawl.home()
    assert crawl.tab.browser.sent == ["Chiudi", "Rifiuta", "No grazie"]
    assert [(d["key"], d["executed"]) for d in home["probes"]["dismiss_overlays"]] == [
        ("promo: saldi", True), ("newsletter: iscriviti", False), ("newsletter: iscriviti", True)]


def test_a_close_that_worked_frees_its_label_for_another_control(scripted):
    """Two overlays and a small Italian bar whose X is labelled "Chiudi" like their closes: each close made its
    overlay go away, so the next "Chiudi" on the same document is another control, not a repeat."""
    promo = {**NEWSLETTER, "kind": "promo", "text_sample": "Saldi"}
    shop = ScriptedShop([NEWSLETTER, promo, bar(button("Accetta tutti", "accept"), button("Chiudi", "close"))])
    crawl = covered_crawl(shop)
    home = crawl.home()
    assert crawl.tab.browser.sent == ["Chiudi", "Chiudi", "Chiudi"]
    assert [(d["kind"], d["gone"]) for d in home["probes"]["dismiss_overlays"]] == [("newsletter", True),
                                                                                  ("promo", True)]
    assert (home["consent"]["choice"], home["consent"]["via"]) == ("reject", "close")


@pytest.mark.parametrize("policy, sent", [("reject", ["Manage options"]), ("auto", ["Manage options", "Accept all"])])
def test_the_consent_test_never_presses_manage_twice(scripted, policy, sent):
    """deception._consent with a real Tab: "manage" has no visible effect; the run's policy applied afterwards does
    not press it again (auto then accepts the blocking banner, reject leaves it)."""
    landing = banner(button("Accept all", "accept"), button("Manage options", "manage"))
    shop = StuckShop([landing], lang="en", stuck={"Manage options"})
    tab, _ = scripted(shop, policy)
    live = deception._Live(None, EngagementSettings(url=SHOP, consent=policy), GUARD, profile="mobile", store=None,
                           run_id=None, say=lambda message: None, errors=[])
    live.load = lambda tab, url: {"page_id": "mobile-deception-1", "url": url, "classification": {"type": "home"},
                                  "audit": shop.audit()}
    result = deception._consent(live, tab, {"page_id": "mobile-home-1", "stage": "home", "audit": shop.audit()})
    assert tab.browser.acted == sent and result["second_layer_reason"] == "manage_had_no_effect"


class ErrorPageCollector(CheckoutCollector):
    """The checkout click lands on Chrome's error page."""

    def collect(self, browser, *, stage, page_id):
        return {**super().collect(browser, stage=stage, page_id=page_id), "audit": {},
                "classification": {"type": "other", "signals": ["navigation_error"]}}


def test_a_click_that_ends_on_an_error_page_never_counts_as_its_stage(monkeypatch):
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "navigated")
    shop = CartShop(controls=["Procedi al checkout"])
    collector = ErrorPageCollector(shop)
    crawl = Crawl(collector, EngagementSettings(url=SHOP, profiles=["mobile"]), profile="mobile", guard=GUARD)
    crawl.tab.browser = collector.browser
    funnel = [{"page_id": f"mobile-{s}-1", "profile": "mobile", "stage": s} for s in STAGES[:-1]]
    cart = {**funnel[-1], "url": f"{SHOP}cart", "classification": {"type": "cart"}, "audit": shop.audit()}
    with pytest.raises(Stop) as stop:
        crawl.checkout(cart)
    crawl.stop(stop.value)
    error = crawl.pages[-1]
    assert error["stage"] == "extra" and error["notes"] == ["checkout_entry: the click led to Chrome's error page"]
    assert [(m["stage"], m["reason"]) for m in crawl.missing] == [("checkout_entry", "navigation_error")]
    run = {"pages": funnel + crawl.pages, "not_assessable": crawl.missing, "errors": []}
    assert audit_module._status(run, EngagementSettings(url=SHOP, profiles=["mobile"])) == "partial"
    assert "checkout_entry" not in deception._funnel(run["pages"], "mobile")  # no funnel page for the DPR tests


class CartLinkShop(ScriptedShop):
    def __init__(self, href, **kwargs):
        super().__init__(controls=["Aggiungi al carrello"], pdp={"add_to_cart": {"present": True,
                                                                               "label": "Aggiungi al carrello"}})
        self.href = href

    def audit(self):
        return {**super().audit(), "nav": {"cart_link": {"present": True, "href": self.href}}}


class LoginCollector(ScriptedCollector):
    def load_page(self, browser, url, *, stage, page_id):
        return {"page_id": page_id, "profile": "mobile", "stage": stage, "url": url, "final_url": url,
                "classification": {"type": "other", "signals": []}, "audit": {"doc": {"word_count": 20}}}


@pytest.mark.parametrize("href, pages", [("#", []), ("/account/login?next=cart", [("mobile-cart-1", "extra")])])
def test_the_cart_stage_needs_a_cart(monkeypatch, href, pages):
    """A "#" mini-cart toggle resolves to the product page itself and is no cart link; a cart URL that shows a login
    page is kept as "extra". Neither is measured as the cart."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "quiet")
    shop = CartLinkShop(href)
    collector = LoginCollector(shop)
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD)
    crawl.tab.browser = collector.browser
    with pytest.raises(Stop) as stop:
        crawl.cart(pdp_of(shop))
    assert (stop.value.stage, stop.value.reason, stop.value.then) == ("cart", "not_found", "cart_not_found")
    assert [(p["page_id"], p["stage"]) for p in crawl.pages] == pages


def test_a_small_bar_gets_no_second_click_after_a_reject_that_may_have_landed(scripted):
    """Ruling 6: on a bar that neither interrupts nor blocks, the X follows a reject only when nothing reached the
    page."""
    small = bar(button("Accetta tutti", "accept"), button("Rifiuta tutti", "reject"), button("Chiudi", "close"))
    tab, home = scripted(ScriptedShop([small]))
    tab.browser = tab.collector.browser = CoveredBrowser(tab.collector.shop, uncertain={"Rifiuta tutti"})
    result = tab.consent(home, "auto", stage="home")
    assert tab.browser.sent == ["Rifiuta tutti"] and result["failed"] == ["reject:failed:TimeoutError"]
    assert (result["choice"], result["reason"]) == ("none", "failed:TimeoutError")
    tab, home = scripted(ScriptedShop([small]))
    tab.browser = tab.collector.browser = CoveredBrowser(tab.collector.shop, covered={"Rifiuta tutti": "consent"})
    result = tab.consent(home, "auto", stage="home")  # covered: never sent, so the X is tried
    assert tab.browser.sent == ["Chiudi"] and (result["choice"], result["via"]) == ("reject", "close")


def test_consent_controls_are_picked_by_the_banner_labels_only():
    page = {"actions": [{"id": "e1", "kind": "click", "label": "Impostazioni"},
                        {"id": "e2", "kind": "click", "label": "Personalizza le scelte"}]}
    assert Tab.pick(page, LX, labels=["Personalizza"], key="consent_manage", strict=True)["id"] == "e2"
    assert Tab.pick(page, LX, labels=["Gestisci opzioni"], key="consent_manage", strict=True) is None
    assert Tab.pick(page, LX, labels=["Gestisci opzioni"], key="consent_manage")["id"] == "e1"  # lexicon anywhere


@pytest.mark.parametrize("pdp, signal", [
    ({"structured": {"offers": 1, "availability": "OutOfStock"}}, "availability"),
    ({"structured": {"offers": 3, "availability": "OutOfStock"}}, None),  # variants: the first offer is not the page's
    ({"add_to_cart": {"present": True, "enabled": False}}, "cta_disabled"),
    ({"add_to_cart": {"present": True, "enabled": False}, "variant_groups": [{"label": "Taglia"}]}, None),
    ({"add_to_cart": {"present": True, "enabled": False}, "variant_groups": [{"selected": False}]}, None),
    ({"add_to_cart": {"present": True, "enabled": False}, "variant_groups": [{"selected": True}]}, "cta_disabled"),
    ({"add_to_cart": {"present": False}, "stock_text": "Articolo esaurito"}, "stock_text"),
    ({"add_to_cart": {"present": False}, "stock_text": "Quasi esaurito, ordina ora"}, None),  # scarcity: buyable
    ({"add_to_cart": {"present": True}, "stock_text": "Esaurito in taglia 38"}, None),
])
def test_unavailable_reads_only_the_product_s_own_signals(pdp, signal):
    assert unavailable({"lexicon_lang": "it", "pdp": pdp}) == signal


SOLD_OUT = {"doc": {"word_count": 50}, "pdp": {"add_to_cart": {"present": True},
                                                "structured": {"offers": 1, "availability": "SoldOut"}}}


@pytest.mark.parametrize("second, pdp, deepest", [
    (("pdp", {"doc": {"word_count": 50}, "pdp": {"add_to_cart": {"present": True}}}), "mobile-pdp-2", "pdp"),
    (("pdp", SOLD_OUT), "mobile-pdp-1", "cart"),
])
def test_a_sold_out_product_is_skipped_and_kept_only_as_the_last_resort(second, pdp, deepest):
    site = {**SITE, f"{SHOP}categoria/scarpe": ("plp", {"products": {"main_group": 24, "cards": [
        {"href": "/prodotto/esaurito"}, {"href": "/prodotto/aurora"}]}}),
        f"{SHOP}prodotto/esaurito": ("pdp", SOLD_OUT), f"{SHOP}prodotto/aurora": second}
    settings = EngagementSettings(url=SHOP, stages=["home", "plp", deepest])
    pages, missing = discover_funnel(MapCollector(site), settings, profile="mobile", guard=GUARD)
    product = next(p for p in pages if p["stage"] == "pdp")
    sold_out = next(p for p in pages if p["page_id"] == "mobile-pdp-1")
    assert product["page_id"] == pdp and sold_out["notes"] == ["pdp candidate unavailable: availability"]
    if pdp == "mobile-pdp-1":  # every candidate sold out: measured, nothing clicked, the cart is out of stock
        assert product["probes"]["add_to_cart"] == {**product["probes"]["add_to_cart"], "executed": False,
                                                    "reason": "out_of_stock", "signal": "availability"}
        assert {m["stage"]: m["reason"] for m in missing} == {"cart": "out_of_stock",
                                                              "checkout_entry": "not_requested"}


def test_no_click_is_sent_for_a_page_there_is_no_room_for(scripted):
    pdp = {"add_to_cart": {"present": True, "label": "Aggiungi al carrello"}}
    shop = ScriptedShop(controls=["Aggiungi al carrello"], pdp=pdp)
    crawl = crawl_of(shop, max_pages=3)
    crawl.pages = [{"stage": s} for s in ("home", "plp", "pdp")]
    with pytest.raises(Stop, match="max_pages"):
        crawl.add_to_cart(pdp_of(shop))
    cart_shop = CartShop(controls=["Continua come ospite", "Procedi al checkout"])
    full = crawl_of(cart_shop, max_pages=4)
    full.pages = [{"stage": s} for s in ("home", "plp", "pdp", "cart")]
    cart = {"page_id": "mobile-cart-1", "url": f"{SHOP}cart", "classification": {"type": "cart"},
            "audit": cart_shop.audit()}
    with pytest.raises(Stop, match="max_pages"):
        full.checkout(cart)
    assert crawl.tab.browser.acted == [] and full.tab.browser.acted == []


def test_an_empty_cart_without_an_empty_message_is_empty(scripted):
    shop = ScriptedShop(controls=["Procedi al checkout"])
    crawl = crawl_of(shop)
    cart = {"page_id": "mobile-cart-1", "url": f"{SHOP}cart", "classification": {"type": "cart"},
            "audit": {"doc": {"word_count": 20}, "cart": {"line_items": [], "total_value": 0}}}
    with pytest.raises(Stop, match="empty_cart"):
        crawl.checkout(cart)
    assert crawl.tab.browser.acted == []


def test_a_page_in_a_tab_that_replaced_a_lost_one_says_so():
    class Flaky(MapCollector):
        def load_page(self, browser, url, *, stage, page_id):
            if url.endswith("novita"):
                self.loads.append(url)
                raise TimeoutError("load")
            return super().load_page(browser, url, stage=stage, page_id=page_id)

    settings = EngagementSettings(url=SHOP, stages=["home", "plp"])
    pages, _ = discover_funnel(Flaky(SITE), settings, profile="mobile", guard=GUARD)
    assert [(p["page_id"], (p.get("probes") or {}).get("tab")) for p in pages] == [
        ("mobile-home-1", None), ("mobile-plp-2", 2)]


# ---------------------------------------------------------------- producer-dependent rules on the real audit.js


def _card(n, body):
    return (f'<li class="product-card"><a href="/p/{n}.html"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" '
            f'width=150 height=150 alt="Articolo {n}"><h3>Articolo {n}</h3></a>{body}</li>')


def _listing(statement_per_card: bool, footnote: bool) -> str:
    omnibus = '<p class="omnibus">Prezzo più basso negli ultimi 30 giorni</p>' if statement_per_card else ""
    cards = "".join(_card(n, f'<p class="price"><span>4{n},90 €</span> <del>6{n},90 €</del>{"*" * footnote}</p>'
                              f"{omnibus}<button>Aggiungi al carrello</button>") for n in range(6))
    note = "<p>* Il prezzo barrato è il prezzo più basso negli ultimi 30 giorni.</p>" if footnote else ""
    return (f'<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Scarpe</title></head><body><main>'
            f"<h1>Scarpe da corsa</h1><h2>Risultati</h2><ul>{cards}</ul>{note}</main></body></html>")


def _product_page(n: int) -> str:
    related = "".join(_card(7 + i, '<p class="price"><span>9,90 €</span></p>'
                                   + ('<p class="stock">Solo 3 rimasti</p>' if i == 0 else "")) for i in range(4))
    jsonld = ('{"@context": "https://schema.org", "@type": "Product", "name": "Scarpa da corsa modello %d", "sku": '
              '"SKU-%d", "offers": {"@type": "Offer", "price": "5%d.90", "priceCurrency": "EUR", "availability": '
              '"https://schema.org/InStock"}}') % (n, n, n)
    return (f'<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Scarpa {n}</title>'
            f'<script type="application/ld+json">{jsonld}</script></head><body><main>'
            f'<h1>Scarpa da corsa modello {n}</h1><p class="price"><span>5{n},90 €</span></p>'
            f"<p>Disponibile. Consegna in 2-4 giorni lavorativi. Una scarpa leggera e reattiva per allenamenti "
            f"quotidiani su strada e su pista.</p><button>Aggiungi al carrello</button>"
            f"<section><h2>Potrebbe interessarti anche</h2><ul>{related}</ul></section></main></body></html>")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def scope_site(tmp_path_factory):
    root = tmp_path_factory.mktemp("scope_site")
    pages = {"cards.html": _listing(True, False), "footnote.html": _listing(False, True),
             "silent.html": _listing(False, False), **{f"p{n}.html": _product_page(n) for n in (1, 2, 3)}}
    for name, html in pages.items():
        (root / name).write_text(html, encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()


def test_card_scope_rules_hold_on_what_audit_js_really_sends(transport, scope_site, tmp_path, quick):
    """Omnibus statements per card or as a footnote are compliant, none is not; an honest product page whose related
    card says "Solo 3 rimasti" is no fake low stock, whether audit.js marks card items (in_card) or not."""
    store = RunStore(tmp_path)
    run_id = store.new_run("audit", scope_site, {})
    collector = PageCollector(transport, store, run_id, profile="desktop", screenshots=False,
                              context_id=new_context(transport))
    browser = collector.open(scope_site + "cards.html")
    try:
        listings = {name: collector.load_page(browser, scope_site + f"{name}.html", stage="plp",
                                              page_id=f"desktop-plp-{n}")
                    for n, name in enumerate(("cards", "footnote", "silent"), 1)}
        visits = [deception._product(collector.load_page(browser, scope_site + f"p{n}.html", stage="extra",
                                                         page_id=f"desktop-deception-{i}"), 10.0 * i, 1 + (i > 0))
                  for i, n in enumerate((1, 1, 2, 3))]
    finally:
        collector.close(browser)
    for name, value in (("cards", True), ("footnote", True), ("silent", False)):
        rows = [o for o in checks.observations({"settings": {"profiles": ["desktop"]}, "pages": [listings[name]],
                                                "not_assessable": [], "deception": {}})
                if o["kpi_id"] == "PTI.STRIKETHROUGH_LOWEST30" and o["stage"] == "plp"]
        assert [row["value"] for row in rows] == [value], (name, rows)
    result = deception.low_stock_result(visits[0], visits[1], visits[2:], page_id="desktop-pdp-1")
    assert result["same_number_products"] == 0 and not result["contradictions"]
    assert checks._low_stock(result)[0] == 0.0
