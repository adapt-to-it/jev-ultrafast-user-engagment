"""audit_shop end to end on the fixture shop (headless Chromium, local server only), the crawler's pieces and steps on
a scripted shop, and the deception visits on scripted tabs (no browser)."""

import copy
import functools
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from jev_ultrafast import model
from jev_ultrafast.browser import StalePage
from jev_ultrafast.engagement import audit as audit_module
from jev_ultrafast.engagement import checks, deception
from jev_ultrafast.engagement import crawler as crawler_module
from jev_ultrafast.engagement.audit import audit_shop
from jev_ultrafast.engagement.collectors import PageCollector
from jev_ultrafast.engagement.crawler import (
    GOALS,
    Crawl,
    Stop,
    Tab,
    category_word,
    discover_funnel,
    is_listing,
    is_product,
    jev_pick,
    landed_guard,
    listing_candidates,
    own_controls,
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


@pytest.fixture(scope="module", autouse=True)
def no_typesafe_key():
    """No audit here asks Jev unless a test sets the key with a stand-in: a developer's real key is never used."""
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("TYPESAFE_API_KEY", raising=False)
        yield


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
    assert settings["anchors_version"] == "anchors.v2" and settings["profiles_version"] == "profiles.v1"
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
    assert not is_listing(record({"products": {"main_group": 8, "itemlist_jsonld": 8}}, kind="home"))
    buyable = {"pdp": {"add_to_cart": {"present": True}, "price": {"value": 10}}}
    assert is_product(record(buyable, kind="other")) and not is_product(record(buyable, kind="cart"))
    assert not is_product(record(buyable, kind="home"))
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
    close button removes its overlay; layers maps a label to the overlay that replaces it, e.g. "manage"). Each label
    keeps one place on the page (no scroll): audit.js's button rect and the observed action's rect."""

    def __init__(self, overlays=(), controls=(), lang="it", layers=None, pdp=None):
        self.overlays, self.controls, self.lang = [copy.deepcopy(o) for o in overlays], list(controls), lang
        self.layers, self.pdp = layers or {}, pdp or {}
        self.places: dict[str, dict] = {}

    def place(self, label):
        return dict(self.places.setdefault(label, {"x": 10, "y": 40 * len(self.places), "w": 100, "h": 30}))

    def audit(self):
        overlays = copy.deepcopy(self.overlays)
        for b in (b for o in overlays for b in o["buttons"]):
            b.setdefault("rect", self.place(b["label"]))
        return {"lexicon_lang": self.lang, "doc": {"word_count": 10}, "overlays": overlays,
                "pdp": copy.deepcopy(self.pdp)}

    def actions(self):
        labels = [b["label"] for o in self.overlays for b in o["buttons"]] + self.controls
        return [{"id": f"e{i}", "kind": "click", "label": label, "role": "button", "node": i,
                 "rect": self.place(label)} for i, label in enumerate(labels)]

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


class LandingMapCollector(MapCollector):
    """MapCollector whose URLs may land elsewhere: {url: final_url}."""

    def __init__(self, pages, lands):
        super().__init__(pages)
        self.lands = lands

    def load_page(self, browser, url, *, stage, page_id):
        loaded = super().load_page(browser, self.lands.get(url, url), stage=stage, page_id=page_id)
        return {**loaded, "url": url}


@pytest.mark.parametrize("kind", ["home", "plp"])
def test_a_category_link_that_lands_on_the_home_page_is_no_listing(kind):
    """An empty or seasonal category sent back to "/": the home page (whatever its type, even with a product grid)
    is not the listing; the next candidate is."""
    grid = {"main_group": 8, "cards": [{"href": "/prodotto/aurora"}]}
    site = {**SITE, f"{SHOP}?utm_source=nav": (kind, {"doc": {"word_count": 50}, "products": grid})}
    collector = LandingMapCollector(site, {f"{SHOP}categoria/novita": f"{SHOP}?utm_source=nav"})
    settings = EngagementSettings(url=SHOP, stages=["home", "plp"], profiles=["mobile"])
    pages, missing = discover_funnel(collector, settings, profile="mobile", guard=GUARD)
    assert [(p["url"], p["stage"]) for p in pages] == [
        (SHOP, "home"), (f"{SHOP}categoria/novita", "extra"), (f"{SHOP}categoria/scarpe", "plp")]
    assert pages[1]["notes"] == ["plp candidate rejected: it landed on the home page"]
    assert not [m for m in missing if m["stage"] in ("home", "plp")]


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


class ConsentVisits(ScriptedVisits):
    """Scripted visits whose tabs record each application of the consent policy: (page_id, policy)."""

    def __init__(self, loads, outcome):
        super().__init__(loads)
        self.outcome, self.applied = outcome, []

    def live(self):
        script = self

        class ConsentTab(ScriptedVisitTab):
            def consent(self, record, policy, *, stage, after_manage=False):
                script.applied.append((record["page_id"], policy))
                return script.outcome

        class Live(deception._Live):
            def tab(self):
                tab = ConsentTab(script)
                self.tabs.append(tab)
                return tab
        return Live


@pytest.mark.parametrize("made, landings", [({"choice": "accept", "via": "first_layer"}, [2, 4]),
                                            ({"choice": "none", "reason": "not_blocking"}, [])])
def test_revisits_are_made_in_the_consent_state_of_the_funnel(monkeypatch, no_waits, made, landings):
    """The funnel accepted the banner: context 1 (whose consent test made no choice) and context 2 (which starts with
    none) get the run's policy on a fresh landing before their product visits. A funnel that made no choice: none."""
    script = ConsentVisits([pdp_record("v1", stock=2), pdp_record("v2", stock=2)],
                           {"choice": "accept", "reason": "policy accept"})
    monkeypatch.setattr(deception, "_Live", script.live())
    funnel = [{"page_id": "mobile-home-1", "profile": "mobile", "stage": "home", "url": SHOP, "consent": made,
               "audit": {"doc": {"word_count": 30}}}, pdp_record("mobile-pdp-1", stock=2)]
    result = deception.run_deception_tests(None, EngagementSettings(url=SHOP, consent="accept"), funnel,
                                           profile="mobile", guard=GUARD)
    assert script.applied == [(f"mobile-deception-{n}", "accept") for n in landings]
    choice = "accept" if landings else None
    assert result["consent_contexts"] == [
        {"context": n, "funnel_choice": choice, "choice": choice or "none",
         **({"landing": f"mobile-deception-{landing}", "reason": "policy accept"} if landings else {})}
        for n, landing in zip((1, 2), landings or (None, None))]
    assert result["errors"] == [] and [v["context"] for v in result["low_stock"]["visits"]] == [1, 2]
    # a context whose consent test already made the funnel's choice loads nothing more
    live = deception._Live(None, EngagementSettings(url=SHOP), GUARD, profile="mobile", store=None, run_id=None,
                           say=print, errors=[])
    live.load = None  # never called
    assert deception._aligned(live, None, context=1, choice="reject", made="reject") == {
        "context": 1, "funnel_choice": "reject", "choice": "reject"}


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


class OriginTransport(FakeTransport):
    """The document's performance.timeOrigin, except the reads numbered in failing: a busy main thread (the mobile
    profile's 4x CPU throttle) times Runtime.evaluate out, so Tab.value reads None."""

    def __init__(self, failing):
        super().__init__()
        self.failing, self.reads = set(failing), 0

    def call(self, method, session_id=None, *, timeout=30.0, **params):
        if params.get("expression") != "performance.timeOrigin":
            return super().call(method, session_id, timeout=timeout, **params)
        self.reads += 1
        if self.reads in self.failing:
            raise TimeoutError("Runtime.evaluate timed out")
        return {"result": {"value": 1700000000123.4}}


@pytest.mark.parametrize("failing", [(), (1,), (3,)])
def test_a_document_whose_origin_could_not_be_read_never_gets_a_control_twice(scripted, failing):
    """The newsletter's "Chiudi" executes without effect and the add-to-cart is covered: the stale re-observe's
    clear_way pass reads the origin again. An origin read that timed out, on either click, is no new document."""
    pdp = {"add_to_cart": {"present": True, "label": "Aggiungi al carrello"}}
    shop = StuckShop([NEWSLETTER], controls=["Aggiungi al carrello"], pdp=pdp, stuck={"Chiudi"})
    crawl = covered_crawl(shop, covered={"Aggiungi al carrello": "newsletter"})
    crawl.tab.transport = OriginTransport(failing)
    product = pdp_of(shop)
    with pytest.raises(Stop, match="add_to_cart_failed"):
        crawl.add_to_cart(product)
    assert crawl.tab.transport.reads == 4 and crawl.tab.browser.sent == ["Chiudi"]  # read 3: the second "Chiudi"
    closes = [(o["executed"], o["reason"]) for o in product["probes"]["add_to_cart"]["overlays"]]
    assert closes == [(True, None), (False, "sent_earlier")]
    # another known origin at the same URL is another document, unless the stored one was never read
    assert crawl.tab.was_sent(SHOP, 1.0, "chiudi") is (1 in failing)
    assert crawl.tab.was_sent(SHOP, None, "chiudi") and not crawl.tab.was_sent(SHOP, None, "iscriviti")


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
    assert error["stage"] == "extra"
    assert error["notes"] == ["checkout_entry: the click did not load a page (navigation error)"]
    assert [(m["stage"], m["reason"]) for m in crawl.missing] == [("checkout_entry", "navigation_error")]
    run = {"pages": funnel + crawl.pages, "not_assessable": crawl.missing, "errors": []}
    assert audit_module._status(run, EngagementSettings(url=SHOP, profiles=["mobile"])) == "partial"
    assert "checkout_entry" not in deception._funnel(run["pages"], "mobile")  # no funnel page for the DPR tests


def test_an_add_to_cart_that_ends_on_an_error_page_is_no_empty_cart(monkeypatch):
    """The cart was never reached: the cart DPR tests carry the stage's navigation_error, never "empty_cart"."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "navigated")
    pdp = {"add_to_cart": {"present": True, "label": "Aggiungi al carrello"}, "price": {"value": 49.9}}
    shop = ScriptedShop(controls=["Aggiungi al carrello"], pdp=pdp)
    collector = ErrorPageCollector(shop)
    crawl = Crawl(collector, EngagementSettings(url=SHOP, profiles=["mobile"]), profile="mobile", guard=GUARD)
    crawl.tab.browser = collector.browser
    product = {**pdp_of(shop), "profile": "mobile", "stage": "pdp"}
    with pytest.raises(Stop) as stop:
        crawl.cart(product)
    crawl.stop(stop.value)
    error = "cart: the click did not load a page (navigation error)"
    assert [(p["stage"], p["notes"]) for p in crawl.pages] == [("extra", [error])]
    assert {m["stage"]: m["reason"] for m in crawl.missing} == {"cart": "navigation_error",
                                                                "checkout_entry": "navigation_error"}
    pages = [product, *crawl.pages]
    funnel = deception._funnel(pages, "mobile")
    tests = {"sneak_into_basket": deception.sneak_into_basket(product, funnel.get("cart")),
             "prechecked_paid": deception.prechecked_paid(funnel.get("cart"), funnel.get("checkout_entry"))}
    run = {"settings": {"profiles": ["mobile"]}, "pages": pages, "not_assessable": crawl.missing,
           "deception": {"mobile": tests}}
    rows = [o for o in checks.observations(run)
            if o["kpi_id"] in ("DPR.SNEAK_INTO_BASKET", "DPR.PRECHECKED_PAID_ADDONS")]
    assert [(o["assessed"], o["stage"], o["reason"]) for o in rows] == [(False, "cart", "navigation_error")] * 2


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


class LandingCollector(ScriptedCollector):
    """The add-to-cart click navigates to a page the classifier calls "other", with the audit given."""

    def __init__(self, shop, landed):
        super().__init__(shop)
        self.landed = landed

    def collect(self, browser, *, stage, page_id):
        url = f"{SHOP}ordine/riepilogo"
        return {"page_id": page_id, "profile": "mobile", "stage": stage, "url": url, "final_url": url,
                "classification": {"type": "other", "signals": []}, "audit": self.landed}


@pytest.mark.parametrize("landed, stage", [(CartShop().audit(), "cart"), ({"doc": {"word_count": 20}}, "extra")])
def test_a_cart_opened_by_the_add_to_cart_is_accepted_on_its_evidence(monkeypatch, landed, stage):
    """The navigated branch accepts the page as the URL branch does (is_cart: lines and a total make a cart whatever
    its classification); a page without cart evidence is kept as "extra" and the cart link is looked for."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "navigated")
    shop = CartLinkShop("#")  # a mini-cart toggle: no cart URL to fall back on
    collector = LandingCollector(shop, landed)
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD)
    crawl.tab.browser = collector.browser
    product = pdp_of(shop)
    if stage == "cart":
        assert crawl.cart(product)["stage"] == "cart"
        assert (product["probes"]["add_to_cart"]["cart_lines"], product["probes"]["add_to_cart"]["cart_empty"]) == (
            1, False)
    else:
        with pytest.raises(Stop, match="not_found"):
            crawl.cart(product)
    assert [(p["page_id"], p["stage"]) for p in crawl.pages] == [("mobile-cart-1", stage)]
    assert collector.browser.acted == ["Aggiungi al carrello"]


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


# ---------------------------------------------------------------- review round 0: cart actions, landing site, guest
# control identity, transport errors, site-copy scarcity


def test_cart_actions_are_never_loaded_as_pages(chromium, shop_server, tmp_path, quick, no_waits):
    """WooCommerce loop cards whose "?add-to-cart=N" link carries the card's longest name (its aria-label), and a
    mini-cart remove link before the header's cart link: no GET of either reaches the shop, and the funnel goes on."""
    result = audited(chromium, shop_server, tmp_path, "woo/index.html", profiles=["desktop"],
                     stages=["home", "plp", "pdp", "cart"])
    run = result["run"]
    assert [r["path"] for r in result["requests"] if re.search(r"add-to-cart|remove_item", r["path"])] == []
    stages = {p["stage"]: p["url"] for p in run["pages"]}
    assert stages["pdp"].endswith("/woo/prodotto.html?id=11") and stages["cart"].endswith("/woo/carrello.html")
    plp = next(p for p in run["pages"] if p["stage"] == "plp")
    assert [c["href"].rsplit("/", 1)[1] for c in plp["audit"]["products"]["cards"]] == [
        f"prodotto.html?id={n}" for n in range(11, 17)]
    pdp = next(p for p in run["pages"] if p["stage"] == "pdp")
    assert pdp["audit"]["nav"]["cart_link"]["href"].endswith("/woo/carrello.html")
    # WooCommerce's <button name="add-to-cart" value="11">: observed by its text, not by its value
    assert (pdp["probes"]["add_to_cart"]["executed"], pdp["probes"]["add_to_cart"]["label"]) == (
        True, "Aggiungi al carrello")


def test_product_candidates_never_include_a_cart_action():
    listing = record({"products": {"cards": [{"href": "?add-to-cart=11"}, {"href": "/prodotto/felpa/"},
                                             {"href": "/cart/add?id=5"}, {"href": "/p/2?quantity=1"}]}},
                     url=f"{SHOP}negozio/", kind="plp")
    assert product_candidates(listing, GUARD) == [f"{SHOP}prodotto/felpa/"]


@pytest.mark.parametrize("cart_link, links", [
    (f"{SHOP}carrello/?remove_item=3f2a&_wpnonce=9c1", {}),  # audit.js read before the fix: the query is dropped
    (None, {"Rimuovi dal carrello": "/cart/change?line=1&quantity=0", "Svuota carrello": "/carrello/svuota",
            "Aggiungi al carrello: “Felpa”": "?add-to-cart=12", "Vai al carrello": "/carrello/"}),
])
def test_the_cart_url_is_never_a_cart_action(cart_link, links):
    shop = CartLinkShop(cart_link) if cart_link else ScriptedShop(controls=list(links))
    collector = ScriptedCollector(shop)
    collector.browser = LinkBrowser(shop, links)
    collector.browser.observe = lambda screenshot=True, observe=collector.browser.observe: {
        **observe(screenshot), "actions": [{**a, "role": "link"} for a in observe(screenshot)["actions"]]}
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD)
    crawl.tab.browser = collector.browser
    product = {"page_id": "mobile-pdp-1", "url": f"{SHOP}prodotto/felpa/", "final_url": f"{SHOP}prodotto/felpa/",
               "classification": {"type": "pdp"}, "audit": shop.audit()}
    assert crawl.cart_url(product) == f"{SHOP}carrello/"


class LandingSiteCollector(ScriptedCollector):
    """The start URL redirects to another registrable domain (SHOP), where the shop's pages and controls are."""

    def load_page(self, browser, url, *, stage, page_id):
        return {**super().load_page(browser, url, stage=stage, page_id=page_id), "final_url": SHOP}


def test_the_guard_is_anchored_where_the_start_url_lands(scripted):
    shop = ScriptedShop([banner(button("Accetta tutti", "accept"), button("Rifiuta tutti", "reject"))])
    collector = LandingSiteCollector(shop)
    crawl = Crawl(collector, EngagementSettings(url="https://brand.example/"), profile="mobile",
                  guard=CheckoutGuard("https://brand.example/", lexicon_for("it")))
    crawl.tab.browser = collector.browser
    home = crawl.home()
    assert home["consent"]["choice"] == "reject" and collector.browser.acted == ["Rifiuta tutti"]
    assert crawl.guard is crawl.tab.guard and crawl.guard.same_site(f"{SHOP}p/1")
    assert landed_guard(GUARD, home, "it") is GUARD  # already the landing site
    assert landed_guard(GUARD, {"final_url": "chrome-error://chromewebdata/"}, "it") is GUARD


def test_an_audit_whose_start_url_redirects_to_another_site_measures_that_site(chromium, shop_server, tmp_path, quick,
                                                                               no_waits):
    target = shop_server.url("shop/index.html")

    class Redirect(SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        store = RunStore(tmp_path)
        run_id = audit_shop(EngagementSettings(url=f"http://localhost:{server.server_port}/go", profiles=["desktop"],
                                               stages=["home", "plp", "pdp"]),
                            store=store, transport_factory=lambda: DirectTransport(chromium.ws_url))
    finally:
        server.shutdown()
        server.server_close()
    run = store.load(run_id)
    assert run["status"] == "complete" and run["site"]["final_url"] == target and not run["warnings"]
    home = next(p for p in run["pages"] if p["stage"] == "home")
    assert (home["consent"]["choice"], home["consent"]["via"]) == ("reject", "first_layer")
    assert run["deception"]["desktop"]["consent"]["applied"]["choice"] == "reject"


GUEST = "Continua come ospite"


@pytest.mark.parametrize("first_href", ["https://partner.example/guest-orders", "/account/guest-order-lookup"])
def test_the_guest_path_clicks_the_control_it_verified(monkeypatch, first_href):
    """Two controls share the guest label: an earlier link off site or to a content page is never the one clicked."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "navigated")
    shop = CartShop(controls=[GUEST, GUEST, "Procedi al checkout"])
    collector = CheckoutCollector(shop)
    collector.browser = LinkBrowser(shop, {})
    observe = collector.browser.observe

    def links(screenshot=True):
        page = observe(screenshot)
        page["actions"][0]["role"] = "link"
        page["guards"][str(page["actions"][0]["node"])][GUARD_HREF] = first_href
        return page
    collector.browser.observe = links
    clicked = []
    collector.browser.act = lambda action, page, text=None: clicked.append(action["id"])
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD)
    crawl.tab.browser = collector.browser
    cart = {"page_id": "mobile-cart-1", "url": f"{SHOP}cart", "final_url": f"{SHOP}cart",
            "classification": {"type": "cart"}, "audit": shop.audit()}
    crawl.checkout(cart)
    probe = cart["probes"]["checkout_entry"]
    assert clicked == ["e1"] and probe["via"] == "guest" and probe["guest"]["href"] is None


class ClosingBrowser(ScriptedBrowser):
    """The input reaches the page, then the transport closes without a reply."""

    def act(self, action, page, text=None):
        self.acted.append(action["label"])
        raise ConnectionError("Browser Harness daemon 'jev-engagement' closed the connection without a reply")


def test_a_click_whose_transport_closed_is_logged_uncertain_and_stops_the_funnel(tmp_path):
    store = RunStore(tmp_path)
    run_id = store.new_run("audit", SHOP, {})
    collector = ScriptedCollector(ScriptedShop(controls=["Aggiungi al carrello"]))
    collector.store, collector.run_id = store, run_id
    tab = Tab(collector, GUARD, profile="mobile")
    tab.browser = ClosingBrowser(collector.shop)
    page = {"url": f"{SHOP}p/1", "actions": collector.shop.actions(), "guards": {}}
    with pytest.raises(ConnectionError):
        tab.act(page["actions"][0], page, "pdp", purpose="add_to_cart", stage="pdp", origin=1.0)
    steps = store.read_steps(run_id)
    assert [(s["purpose"], s["status"]) for s in steps] == [("add_to_cart", "error")]
    assert steps[0]["note"].startswith("ConnectionError: ")
    assert (f"{SHOP}p/1", "aggiungi al carrello") in tab.uncertain and tab.was_sent(f"{SHOP}p/1", 1.0,
                                                                                    "aggiungi al carrello")
    assert tab.act(page["actions"][0], page, "pdp", purpose="add_to_cart", stage="pdp") == (False, "uncertain_earlier")
    assert tab.browser.acted == ["Aggiungi al carrello"]


def stocked(page_id, url, title, text="Solo 3 rimasti"):
    record = pdp_record(page_id, title=title)
    record["audit"]["persuasion"]["scarcity"] = [{"text": text, "number": 3, "in_card": False, "overlay": False}]
    return {**record, "url": url, "final_url": url}


@pytest.mark.parametrize("banner_text, fired", [("Solo 3 rimasti", False), (None, True)])
def test_a_scarcity_line_shown_on_every_page_is_site_copy(monkeypatch, no_waits, banner_text, fired):
    """The same statement on the home page (an announcement bar) is no product's stock claim; without it on the home
    page, the same number on every product is the low-stock signal."""
    others = [f"{SHOP}p/{n}" for n in (2, 3, 4)]
    scarcity = [{"text": banner_text, "number": 3, "in_card": False, "overlay": False}] if banner_text else []
    home = {"page_id": "mobile-home-1", "profile": "mobile", "stage": "home", "url": SHOP, "final_url": SHOP,
            "classification": {"type": "home"},
            "audit": {"doc": {"word_count": 30}, "persuasion": {"scarcity": scarcity},
                      "products": {"cards": [{"href": url} for url in others]}}}
    product = {**stocked("mobile-pdp-1", f"{SHOP}p/1", "Felpa Bosco"), "profile": "mobile", "stage": "pdp"}
    loads = [stocked("v1", f"{SHOP}p/1", "Felpa Bosco"), stocked("v2", f"{SHOP}p/1", "Felpa Bosco"),
             *(stocked(f"o{n}", url, f"Maglia {n}") for n, url in enumerate(others))]
    monkeypatch.setattr(deception, "_Live", ScriptedVisits(loads).live())
    result = deception.run_deception_tests(None, EngagementSettings(url=SHOP), [home, product], profile="mobile",
                                           guard=GUARD)
    low = result["low_stock"]
    assert low["assessed"] and (low["same_number_products"] == 3) is fired  # the product and OTHER_PRODUCTS
    assert low.get("site_copy") == ([banner_text] if banner_text else None)
    assert (checks._low_stock(low)[0] >= 0.5) is fired


# ---------------------------------------------------------------- review round 1: landing home, aborted loads


class DetourHandler(SimpleHTTPRequestHandler):
    """The fixture shop with two category links before the real one: one redirected to the home page (an empty or
    seasonal category), one answered 204 (Chrome aborts the load and keeps the previous document)."""

    root = Path(__file__).with_name("fixtures") / "shop"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.root), **kwargs)

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/category.html?c=nuovi"):
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path.startswith("/category.html?c=vuota") or self.path == "/vuoto":
            self.send_response(204)
            self.end_headers()
        elif self.path in ("/", "/index.html"):
            data = (self.root / "index.html").read_text(encoding="utf-8").replace(
                '<li><a href="category.html">Scarpe da corsa</a></li>',
                '<li><a href="category.html?c=nuovi">Novità</a></li><li><a href="category.html?c=vuota">Saldi</a></li>'
                '<li><a href="category.html">Scarpe da corsa</a></li>').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            super().do_GET()


@pytest.fixture
def detour_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DetourHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()


def test_category_links_that_land_home_or_abort_are_skipped_for_the_real_listing(transport, detour_site, quick):
    url = detour_site + "index.html"
    settings = EngagementSettings(url=url, profiles=["desktop"], stages=["home", "plp"], consent="none",
                                  settle_timeout_s=8)
    collector = PageCollector(transport, None, None, profile="desktop", screenshots=False, settle_timeout_s=8,
                              context_id=new_context(transport))
    pages, missing = discover_funnel(collector, settings, profile="desktop",
                                     guard=CheckoutGuard(url, lexicon_for("it")))
    shown = [(p["url"].removeprefix(detour_site), p["stage"], p["classification"]["type"]) for p in pages]
    assert shown == [("index.html", "home", "home"), ("category.html?c=nuovi", "extra", "home"),
                     ("category.html?c=vuota", "extra", "other"), ("category.html", "plp", "plp")]
    aborted = pages[2]
    assert "navigation_error" in aborted["classification"]["signals"] and not aborted["audit"]
    assert "navigation_error: net::ERR_ABORTED" in aborted["notes"]
    assert not [m for m in missing if m["stage"] in ("home", "plp")]


def test_a_start_url_chrome_aborts_is_a_failed_home_load(transport, detour_site, quick):
    """A fresh tab whose start URL answers 204 stays on about:blank: a navigation error, not an empty home page."""
    settings = EngagementSettings(url=detour_site + "vuoto", profiles=["desktop"], stages=["home", "plp"],
                                  settle_timeout_s=8)
    collector = PageCollector(transport, None, None, profile="desktop", screenshots=False, settle_timeout_s=8,
                              context_id=new_context(transport))
    pages, missing = discover_funnel(collector, settings, profile="desktop",
                                     guard=CheckoutGuard(settings.url, lexicon_for("it")))
    assert [(p["stage"], p["final_url"]) for p in pages] == [("extra", "about:blank")]
    assert "navigation_error: net::ERR_ABORTED" in pages[0]["notes"]
    assert {m["stage"]: m["reason"] for m in missing if m["stage"] in ("home", "plp")} == {
        "home": "navigation_error", "plp": "navigation_error"}


CART_WITH_SLIDE_IN = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Carrello - Negozio</title>
<style>body{font-family:sans-serif;margin:20px} td{padding:8px}</style></head><body>
<h1>Il tuo carrello</h1>
<table class="cart"><tr class="cart-item"><td class="name">Scarpa Aurora</td><td class="price">49,90 €</td>
<td><input type="number" value="1" aria-label="Quantità" min="1"></td>
<td><button class="remove" onclick="fetch('/cart/remove',{method:'POST'});this.closest('tr').remove()">×</button></td>
</tr></table>
<p>Subtotale: 49,90 €</p><p>Totale: 49,90 €</p><p><a class="checkout" href="checkout.html">Procedi al checkout</a></p>
<div role="dialog" aria-label="Newsletter" style="position:fixed;right:10px;bottom:10px;width:300px;height:170px;
background:#fff;border:1px solid #000;padding:8px">
<p>Iscriviti alla newsletter e ricevi il 10% di sconto sul primo ordine</p>
<button class="close" onclick="this.closest('[role=dialog]').remove()">×</button></div>
</body></html>"""


class PostLog(QuietHandler):
    posts: list = []

    def do_POST(self):
        self.posts.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def test_an_overlay_close_never_clicks_another_control_with_its_label(transport, tmp_path, quick):
    """A cart line's "×" comes before a newsletter slide-in's own "×" in the document: the close goes to the slide-in
    (it goes away) and the cart is left untouched (no remove request reaches the shop)."""
    (tmp_path / "cart.html").write_text(CART_WITH_SLIDE_IN, encoding="utf-8")
    PostLog.posts = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(PostLog, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/cart.html"
    store = RunStore(tmp_path / "runs")
    run_id = store.new_run("audit", url, {})
    collector = PageCollector(transport, store, run_id, profile="desktop", screenshots=False, settle_timeout_s=8,
                              context_id=new_context(transport))
    tab = Tab(collector, CheckoutGuard(url, lexicon_for("it")), profile="desktop")
    try:
        record = tab.load(url, stage="cart", page_id="desktop-cart-1")
        page = tab.observe()
        assert [a["label"] for a in page["actions"]].count("×") == 2  # the same label twice on the page
        closed = tab.dismiss(record["audit"], record, stage="cart")
        assert [(c["kind"], c["executed"], c["gone"]) for c in closed] == [("newsletter", True, True)]
        assert tab.value("document.querySelectorAll('tr.cart-item').length") == 1
        steps = store.read_steps(run_id)
        assert [(s["purpose"], s["label"]) for s in steps] == [("dismiss_overlay", "×")]
    finally:
        tab.close()
        server.shutdown()
        server.server_close()
    assert PostLog.posts == []


def test_own_controls_match_an_overlay_button_at_either_scroll():
    audit = {"viewport": {"scroll_y": 300},
             "overlays": [{"buttons": [{"label": "×", "kind": "close", "rect": {"x": 900, "y": 1000, "w": 20,
                                                                                 "h": 20}}]}]}
    accept = own_controls(audit["overlays"][0]["buttons"], audit)
    fixed = {"label": "×", "rect": {"x": 900, "y": 700, "w": 20, "h": 20}}  # viewport y = 1000 - 300
    elsewhere = {"label": "×", "rect": {"x": 300, "y": 700, "w": 20, "h": 20}}
    assert accept(fixed, {"scroll": {"y": 0}}) and accept(fixed, {"scroll": {"y": 300}})
    assert accept({"label": "×", "rect": {"x": 900, "y": 500, "w": 20, "h": 20}}, {"scroll": {"y": 500}})  # in flow
    assert not accept(elsewhere, {"scroll": {"y": 300}}) and not accept({"label": "×"}, {"scroll": {"y": 0}})
    assert not own_controls([{"label": "×"}], audit)(fixed, {"scroll": {"y": 0}})  # no rect: nothing matches


# ---------------------------------------------------------------- Jev fallback: stand-ins for model.post_json (the
# real model.choose builds each request and validate_choice reads each answer; no paid API is ever called)


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


class Jev:
    """Stand-in for model.post_json. Every request must be the agent loop's own (model.choose: an operation head
    offering CLICK, DONE and BLOCKED, one click_target head over the observed elements, all click-only and named, none
    that pays and none on a checkout page) with one of the fallback's fixed goals, verbatim. It answers per lookup (a
    GOALS key): the label of the element to click, "DONE" or "BLOCKED"; a label that was not offered is answered
    BLOCKED (Jev can only choose an offered index) and recorded in missed."""

    def __init__(self, **answers):
        self.answers, self.bodies, self.asked, self.missed = answers, [], [], []

    def __call__(self, url, key, body):
        assert url == "https://api.typesafe.ai/v1/systemone" and key == "test-key"
        self.bodies.append(copy.deepcopy(body))
        questions, elements = body["questions"], body["state"]["elements"]
        assert set(questions) == {"operation", "click_target"} and body["state"]["recent_actions"] == []
        operations, targets = questions["operation"]["criteria"], questions["click_target"]["criteria"]
        assert set(operations) == {"CLICK", "DONE", "BLOCKED"} and set(targets) == {e["index"] for e in elements}
        assert elements and all(e["operations"] == ["CLICK"] for e in elements)
        assert not [e for e in elements if PAY.search(e["label"])] and "checkout" not in body["state"]["page"]["url"]
        assert not [e for e in elements if e["label"].casefold() in (e.get("role"), f"open {e.get('role')}")]
        goal = questions["operation"]["instructions"]["goal"]
        assert questions["click_target"]["instructions"]["goal"] == goal
        purpose = next(k for k, text in GOALS.items() if goal == text)
        self.asked.append(purpose)
        answer = self.answers[purpose]
        usage = {"input_tokens": 900, "output_tokens": 0}
        if answer not in ("DONE", "BLOCKED") and not [e for e in elements if e["label"] == answer]:
            self.missed.append(answer)
            answer = "BLOCKED"
        if answer in ("DONE", "BLOCKED"):
            return {"model": "jev-1.13.0", "answers": {"operation": choice(operations, answer)}, "usage": usage}
        index = next(e["index"] for e in elements if e["label"] == answer)
        return {"model": "jev-1.13.0", "usage": usage,
                "answers": {"operation": choice(operations, "CLICK"), "click_target": choice(targets, index)}}


@pytest.fixture
def jev(monkeypatch):
    def make(**answers):
        monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
        monkeypatch.delenv("TYPESAFE_MODEL", raising=False)
        stand_in = Jev(**answers)
        monkeypatch.setattr(model, "post_json", stand_in)
        return stand_in
    return make


def never(*args, **kwargs):
    raise AssertionError("no TypeSafe request may be sent")


def jev_audit(chromium, shop_server, tmp_path, monkeypatch, **settings):
    """The fixture shop (desktop) with a lexicon that finds no category link; deception tests left out (not tested)."""
    monkeypatch.setattr(crawler_module, "listing_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(audit_module, "run_deception_tests", lambda *args, **kwargs: {"version": "x", "errors": []})
    return audited(chromium, shop_server, tmp_path, "shop/index.html", profiles=["desktop"], **settings)


FIELDS = ("stage", "purpose", "operation", "label", "probability", "executed", "reason")


def test_a_listing_the_lexicon_misses_is_jev_s_pick_and_the_funnel_goes_on(chromium, shop_server, tmp_path, quick,
                                                                         monkeypatch, jev):
    stand_in = jev(plp="Scarpe da corsa")
    result = jev_audit(chromium, shop_server, tmp_path, monkeypatch)
    run = result["run"]
    assert run["status"] == "complete" and not run["errors"] and not run["not_assessable"]
    assert [(p["page_id"], p["stage"]) for p in run["pages"]] == [(f"desktop-{s}-1", s) for s in STAGES]
    home, listing = run["pages"][:2]
    assert listing["url"] == shop_server.url("shop/category.html") and listing["classification"]["type"] == "plp"
    [probe] = home["probes"]["jev_fallback"]
    assert {k: probe[k] for k in FIELDS} == {"stage": "plp", "purpose": "plp", "operation": "CLICK",
                                             "label": "Scarpe da corsa", "probability": 1.0, "executed": True,
                                             "reason": None}
    assert (probe["goal"], probe["model"], probe["href"], probe["url"]) == (GOALS["plp"], "jev-1.13.0",
                                                                           "category.html", listing["url"])
    assert any(note.startswith("jev_fallback plp: ") for note in home["notes"])
    assert stand_in.asked == ["plp"] and stand_in.bodies[0]["state"]["page"]["url"] == shop_server.url(
        "shop/index.html")
    assert run["model_calls"] == {"crawler_fallback": {
        "requests": 1, "latency_ms": probe["latency_ms"], "input_tokens": 900, "model": "jev-1.13.0",
        "stages": [{"profile": "desktop", "page_id": "desktop-home-1", **{k: probe[k] for k in FIELDS}}]}}
    assert [w for w in run["warnings"] if "Jev" in w] == [
        "desktop: plp found through the Jev fallback: not reproducible across runs"]
    steps = result["steps"]  # a link Jev chose is loaded with a GET: no click; the rest of the funnel is the lexicon's
    assert {s["purpose"] for s in steps} <= PURPOSES and not [s for s in steps if s["note"]]
    assert not [r for r in result["requests"] if r["method"] == "POST" or r["path"].startswith("/pay")]


@pytest.mark.parametrize("key", [True, False])
def test_a_listing_jev_does_not_find_is_not_found_as_before(chromium, shop_server, tmp_path, quick, monkeypatch, jev,
                                                            key):
    """Jev answers BLOCKED (no element): the stage keeps its reason. Without TYPESAFE_API_KEY nothing is asked and
    nothing of the fallback is recorded."""
    stand_in = jev(plp="BLOCKED") if key else None
    if not key:
        monkeypatch.setattr(model, "post_json", never)
    run = jev_audit(chromium, shop_server, tmp_path, monkeypatch, stages=["home", "plp"])["run"]
    assert run["status"] == "partial" and [p["stage"] for p in run["pages"]] == ["home"]
    assert [(m["stage"], m["reason"]) for m in run["not_assessable"]] == [("plp", "not_found")] + [
        (s, "not_requested") for s in STAGES[2:]]
    assert not [w for w in run["warnings"] if "Jev" in w]
    home = run["pages"][0]
    if key:
        [probe] = home["probes"]["jev_fallback"]
        assert (probe["operation"], probe["executed"], probe["reason"]) == ("BLOCKED", False, "jev_blocked")
        assert stand_in.asked == ["plp"] and run["model_calls"]["crawler_fallback"]["requests"] == 1
    else:
        assert "jev_fallback" not in home["probes"] and "model_calls" not in run
        assert not [n for n in home.get("notes") or [] if "jev" in n.casefold()]


class SportaShop(ScriptedShop):
    """A product page whose add-to-cart audit.js did not recognise ("Mettilo nella sporta"), beside a related
    product's "Aggiungi al carrello"; the cart link "Vedi la sporta" is no lexicon word either."""

    def __init__(self):
        super().__init__(controls=["Aggiungi al carrello", "Mettilo nella sporta", "Vedi la sporta"],
                         pdp={"add_to_cart": {"present": False}, "price": {"value": 49.9}})


class StepLog:
    def __init__(self, events):
        self.events = events

    def append_step(self, run_id, step):
        self.events.append(("step", step["purpose"], step["note"]))


class SportaCollector(ScriptedCollector):
    """The cart page: the GET of its link, or the page its click navigated to; steps go to events."""

    def __init__(self, shop, hrefs, events):
        super().__init__(shop)
        self.browser = LinkBrowser(shop, hrefs)
        self.store, self.run_id = StepLog(events), "run"

    def collect(self, browser, *, stage, page_id):
        return self.load_page(browser, f"{SHOP}sporta", stage=stage, page_id=page_id)


@pytest.mark.parametrize("href", ["/sporta", None])
def test_an_add_to_cart_and_a_cart_link_the_lexicon_misses_are_jev_s_picks(monkeypatch, jev, href):
    """audit.js found no add-to-cart: no lexicon pick (the related product's "Aggiungi al carrello" is not this
    product's control), Jev's pick, clicked once and logged before its effect is observed. No cart link either: Jev's
    pick on the page the tab shows, its href loaded, or (no href) clicked once and its navigation collected."""
    events = []

    def effect(tab, origin, **kwargs):
        events.append(("effect", tab.browser.acted[-1]))
        return "navigated" if tab.browser.acted[-1] == "Vedi la sporta" else "quiet"
    monkeypatch.setattr(Tab, "await_effect", effect)
    stand_in = jev(add_to_cart="Mettilo nella sporta", cart="Vedi la sporta")
    shop = SportaShop()
    collector = SportaCollector(shop, {"Vedi la sporta": href} if href else {}, events)
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD, jev_fallback=True)
    crawl.tab.browser = collector.browser
    product = pdp_of(shop)
    cart = crawl.cart(product)
    assert (cart["stage"], cart["url"]) == ("cart", f"{SHOP}sporta") and crawl.pages == [cart]
    assert collector.browser.acted == ["Mettilo nella sporta"] + ([] if href else ["Vedi la sporta"])
    assert stand_in.asked == ["add_to_cart", "cart"]
    assert [(p["purpose"], p["stage"], p["label"], p["executed"], p["reason"]) for p in
            product["probes"]["jev_fallback"]] == [("add_to_cart", "cart", "Mettilo nella sporta", True, None),
                                                   ("cart", "cart", "Vedi la sporta", True, None)]
    added = product["probes"]["add_to_cart"]
    assert added["executed"] and added["jev_fallback"] and added["label"] == "Mettilo nella sporta"
    clicks = [("step", "add_to_cart", "jev_fallback"), ("effect", "Mettilo nella sporta")]
    assert events == clicks + ([] if href else [("step", "open_cart", "jev_fallback"), ("effect", "Vedi la sporta")])


def test_without_the_fallback_an_unrecognised_add_to_cart_stops_as_before(monkeypatch):
    monkeypatch.setattr(model, "post_json", never)
    shop = SportaShop()
    crawl = crawl_of(shop)
    product = pdp_of(shop)
    with pytest.raises(Stop, match="add_to_cart_failed"):
        crawl.cart(product)
    assert crawl.tab.chooser is None and crawl.tab.browser.acted == []
    assert product["probes"]["add_to_cart"]["reason"] == "add_to_cart_not_found" and "jev_fallback" not in product[
        "probes"]


class TwinBrowser(ScriptedBrowser):
    """Two controls labelled alike ("Mettilo nella sporta" by the price and in a sticky bar); the first act meets a
    stale page; the re-observed page lists them in the other order, or (gone) without them."""

    def __init__(self, shop, gone=False):
        super().__init__(shop)
        self.observed, self.gone, self.stale = 0, gone, 1

    def observe(self, screenshot=True):
        self.observed += 1
        twins = [{"id": f"e{node}", "kind": "click", "label": "Mettilo nella sporta", "role": "button", "node": node,
                  "rect": {"x": 10, "y": y, "w": 100, "h": 30}} for node, y in ((8, 300), (9, 900))]
        actions = twins if self.observed == 1 else [] if self.gone else twins[::-1]
        return {"url": SHOP, "actions": actions, "guards": {}, "scroll": {"y": 0}}

    def act(self, action, page, text=None):
        super().act(action, page, text)
        self.acted[-1] = action["id"]


@pytest.mark.parametrize("gone", [False, True])
def test_jev_s_control_that_met_a_stale_page_is_picked_again_by_its_label_without_a_request(monkeypatch, jev, gone):
    """Nothing was sent (StalePage): the one re-observe picks Jev's element again by its label, the one nearest to
    where it was (e8, although the page now lists e9 first), with no second request; its outcome updates the same
    probe. Gone from the re-observed page: no click, "control_not_found", and the add-to-cart probe does not claim
    that no control was found. A click that is no lookup (an overlay's close) is never asked."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "quiet")
    stand_in = jev(add_to_cart="Mettilo nella sporta")
    shop = SportaShop()
    collector = ScriptedCollector(shop)
    collector.browser = TwinBrowser(shop, gone=gone)
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD, jev_fallback=True)
    crawl.tab.browser = collector.browser
    product = pdp_of(shop)
    if gone:
        with pytest.raises(Stop, match="add_to_cart_failed"):
            crawl.add_to_cart(product)
    else:
        assert crawl.add_to_cart(product) == "quiet"
    assert stand_in.asked == ["add_to_cart"] and collector.browser.acted == ([] if gone else ["e8"])
    [probe] = product["probes"]["jev_fallback"]
    added = product["probes"]["add_to_cart"]
    assert (probe["executed"], probe["reason"], probe["stale_reobserved"]) == (
        (False, "control_not_found", 1) if gone else (True, None, 1))
    assert (added["executed"], added["reason"], added["jev_fallback"], added["stale_reobserved"]) == (
        not gone, "control_not_found" if gone else None, True, 1)
    closed = crawl.tab.click(product, purpose="dismiss_overlay", stage="pdp", labels=["Chiudi"])
    assert closed["reason"] == "control_not_found" and "jev_fallback" not in closed
    assert stand_in.asked == ["add_to_cart"] and len(product["probes"]["jev_fallback"]) == 1


@pytest.mark.parametrize("rect", [True, False])
def test_a_variant_option_the_lexicon_misses_is_jev_s_pick_of_the_option_audit_js_named(monkeypatch, jev, rect):
    """Jev is offered only the control inside the option's audit.js rect ("Taglia 40 EU" never), with the lookup's
    fixed goal (ruling 3: no observed label in the request; the option stays in the variant probe); a group without a
    rect offers nothing: no request, and the variant stays required."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "quiet")
    stand_in = jev(select_variant="Taglia 41 EU")
    if not rect:
        monkeypatch.setattr(model, "post_json", never)
    group = {"label": "Taglia", "kind": "buttons", "selected": False, "first_available": "41"}
    pdp = {"add_to_cart": {"present": True, "label": "Aggiungi"}, "price": {"value": 49.9}, "variant_groups": [group]}
    shop = ScriptedShop(controls=["Taglia 40 EU", "Taglia 41 EU", "Aggiungi"], pdp=pdp)
    if rect:
        shop.pdp["variant_groups"][0]["rect"] = shop.place("Taglia 41 EU")
    collector = ScriptedCollector(shop)
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD, jev_fallback=True)
    crawl.tab.browser = collector.browser
    product = pdp_of(shop)
    if not rect:
        with pytest.raises(Stop, match="variant_required"):
            crawl.add_to_cart(product)
        [asked] = product["probes"]["jev_fallback"]
        assert (asked["purpose"], asked["executed"], asked["reason"]) == ("select_variant", False, "no_click_actions")
        assert collector.browser.acted == [] and not product["probes"]["add_to_cart"]["variant"]["executed"]
        return
    assert crawl.add_to_cart(product) == "quiet"  # the add-to-cart audit.js named is the lexicon's: no request
    assert collector.browser.acted == ["Taglia 41 EU", "Aggiungi"] and stand_in.asked == ["select_variant"]
    goal = stand_in.bodies[0]["questions"]["operation"]["instructions"]["goal"]  # ruling 3: fixed, no page text
    assert goal == GOALS["select_variant"] and "41" not in goal and "Taglia" not in goal
    assert [e["label"] for e in stand_in.bodies[0]["state"]["elements"]] == ["Taglia 41 EU"]
    variant = product["probes"]["add_to_cart"]["variant"]
    assert variant["executed"] and variant["jev_fallback"] and "jev_fallback" not in product["probes"]["add_to_cart"]
    assert (variant["group"], variant["label"]) == ("Taglia", "41")
    [asked] = product["probes"]["jev_fallback"]
    assert (asked["purpose"], asked["stage"], asked["label"], asked["executed"]) == (
        "select_variant", "cart", "Taglia 41 EU", True)


def test_a_checkout_cta_the_lexicon_misses_is_jev_s_pick_among_what_the_guard_allows(monkeypatch, jev):
    """The cart offers a guest path (forms.guest_option) but no control the lexicon reads as "guest": that is never a
    Jev lookup (rule d needs the lexicon's "guest" control); the checkout CTA is, over the clicks the guard allows
    minus removing or emptying the cart, logging in, registering and adding ("Paga ora", "Rimuovi", "Accedi",
    "Registrati" never, nor the cart's bare "Svuota")."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "navigated")
    stand_in = jev(checkout_entry="Concludi")
    shop = CartShop(controls=["Paga ora", "Rimuovi", "Accedi", "Registrati", "Svuota", "Concludi"])
    collector = CheckoutCollector(shop)
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD, jev_fallback=True)
    crawl.tab.browser = collector.browser
    cart = {"page_id": "mobile-cart-1", "url": f"{SHOP}cart", "final_url": f"{SHOP}cart",
            "classification": {"type": "cart"}, "audit": shop.audit()}
    crawl.checkout(cart)
    assert collector.browser.acted == ["Concludi"] and [p["stage"] for p in crawl.pages] == ["checkout_entry"]
    assert [e["label"] for e in stand_in.bodies[0]["state"]["elements"]] == ["Concludi"]
    assert stand_in.asked == ["checkout_entry"] and set(GOALS) == {"plp", "pdp", "cart", "add_to_cart",
                                                                 "select_variant", "checkout_entry"}
    probe = cart["probes"]["checkout_entry"]
    assert probe["via"] == "checkout_cta" and probe["jev_fallback"] and probe["guest"]["reason"] == "control_not_found"
    [asked] = cart["probes"]["jev_fallback"]
    assert (asked["purpose"], asked["stage"], asked["executed"], asked["reason"], asked["goal"]) == (
        "checkout_entry", "checkout_entry", True, None, GOALS["checkout_entry"])
    assert crawl.chooser(cart, crawl.tab.observe(), "guest_checkout", ["Continua come ospite"]) == (None, None)
    assert stand_in.asked == ["checkout_entry"]


def test_jev_is_offered_only_the_clicks_the_guard_allows_and_only_a_click_is_consumed(monkeypatch, jev):
    page = {"url": f"{SHOP}carrello", "title": "Carrello", "text": "Il tuo carrello", "guards": {}, "actions": [
        {"id": "e1", "kind": "fill", "label": "Cerca", "role": "searchbox", "node": 1},
        {"id": "e2", "kind": "click", "label": "Paga ora", "role": "button", "node": 2},
        {"id": "e3", "kind": "click", "label": "Vai alla cassa", "role": "link", "node": 3},
        {"id": "scroll_down", "kind": "scroll", "label": "Scroll down"},
    ]}
    stand_in = jev(checkout_entry="DONE")
    action, answer = jev_pick(page, GUARD, "cart", GOALS["checkout_entry"])
    assert action is None and (answer["operation"], answer["reason"], answer["model"]) == ("DONE", "jev_done",
                                                                                         "jev-1.13.0")
    assert [(e["label"], e["operations"]) for e in stand_in.bodies[0]["state"]["elements"]] == [
        ("Vai alla cassa", ["CLICK"])]

    def invalid(url, key, body):  # a target that was not offered: validate_choice refuses it, nothing is clicked
        operations = body["questions"]["operation"]["criteria"]
        return {"model": "jev-1.13.0", "answers": {"operation": choice(operations, "CLICK"),
                                                   "click_target": choice(["1", "9"], "9")}}
    monkeypatch.setattr(model, "post_json", invalid)
    action, answer = jev_pick(page, GUARD, "cart", GOALS["checkout_entry"])
    assert action is None and answer["reason"] == "jev_error" and "Invalid TypeSafe" in answer["error"]

    def down(url, key, body):
        raise RuntimeError("Model provider returned HTTP 422; no action executed.")
    monkeypatch.setattr(model, "post_json", down)
    failed = jev_pick(page, GUARD, "cart", GOALS["checkout_entry"])[1]
    assert failed["reason"] == "jev_error" and isinstance(failed["latency_ms"], int) and "usage" not in failed
    monkeypatch.setattr(model, "post_json", never)  # a checkout page offers no click: nothing is asked
    checkout = {**page, "url": f"{SHOP}checkout", "actions": [
        *page["actions"][:2], {"id": "e4", "kind": "click", "label": "Continua", "role": "button", "node": 4}]}
    assert jev_pick(checkout, GUARD, "checkout", GOALS["checkout_entry"]) == (None, {"reason": "no_click_actions"})


class JevSiteBrowser(FakeBrowser):
    """Observes the links of the page its collector loaded last (label -> href)."""

    def __init__(self, collector):
        super().__init__()
        self.collector = collector

    def observe(self, screenshot=True):
        url = self.collector.loads[-1]
        links = self.collector.links.get(url, {})
        return {"url": url, "title": "", "text": "", "scroll": {"y": 0},
                "actions": [{"id": f"e{n}", "kind": "click", "label": label, "role": "link", "node": n}
                            for n, label in enumerate(links)],
                "guards": {str(n): [None] * GUARD_HREF + [href] for n, href in enumerate(links.values())}}


class JevSiteCollector(MapCollector):
    def __init__(self, pages, links):
        super().__init__(pages)
        self.links = links

    def open(self, url):
        return JevSiteBrowser(self)

    def audit(self, browser):  # audit.js on the page loaded last
        return copy.deepcopy(self.pages[self.loads[-1]][1])


def test_a_product_the_lexicon_misses_is_asked_on_the_listing_reloaded():
    """No product card leads to a product page: the tab has left the listing, which is reloaded as "extra" for one
    question; Jev's link is loaded and accepted as the product page by is_product."""
    listing = f"{SHOP}categoria/scarpe"
    site = {SHOP: ("home", {"nav": {"categories": [{"label": "Scarpe", "href": "/categoria/scarpe",
                                                    "visible": True}]}}),
            listing: ("plp", {"products": {"main_group": 8, "cards": [{"href": "/prodotto/guida"}]}}),
            f"{SHOP}prodotto/guida": ("other", {}), f"{SHOP}p/aurora": ("pdp", {})}
    collector = JevSiteCollector(site, {listing: {"Guida alle taglie": "/prodotto/guida",
                                                  "Scarpa Aurora": "/p/aurora"}})
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TYPESAFE_API_KEY", "test-key")
        stand_in = Jev(pdp="Scarpa Aurora")
        patch.setattr(model, "post_json", stand_in)
        pages, missing = discover_funnel(collector, EngagementSettings(url=SHOP, stages=["home", "plp", "pdp"]),
                                         profile="mobile", guard=GUARD, jev_fallback=True)
    assert [(p["page_id"], p["stage"], p["url"]) for p in pages] == [
        ("mobile-home-1", "home", SHOP), ("mobile-plp-1", "plp", listing),
        ("mobile-pdp-1", "extra", f"{SHOP}prodotto/guida"), ("mobile-extra-1", "extra", listing),
        ("mobile-pdp-2", "pdp", f"{SHOP}p/aurora")]
    assert {m["stage"]: m["reason"] for m in missing} == {"cart": "not_requested", "checkout_entry": "not_requested"}
    assert stand_in.asked == ["pdp"] and stand_in.bodies[0]["state"]["page"]["url"] == listing
    reloaded = pages[3]
    assert "reloaded for the Jev fallback (pdp)" in reloaded["notes"]
    [probe] = reloaded["probes"]["jev_fallback"]
    assert (probe["label"], probe["href"], probe["url"], probe["executed"]) == (
        "Scarpa Aurora", "/p/aurora", f"{SHOP}p/aurora", True)


@pytest.mark.parametrize("key", [True, False])
def test_the_audit_asks_jev_only_with_a_key_and_adds_up_its_requests(tmp_path, monkeypatch, key):
    """audit_shop turns the fallback on when TYPESAFE_API_KEY is set; its requests add up across profiles
    (a lookup that asked nothing is no request) and a stage reached through it gets a warning."""
    asked = []

    def funnel(collector, settings, *, profile, guard, progress=None, jev_fallback=False):
        asked.append(jev_fallback)
        home, listing = ({"page_id": f"{profile}-{stage}-1", "profile": profile, "stage": stage, "url": SHOP,
                          "final_url": SHOP, "classification": {"type": stage, "signals": []}, "audit": {},
                          "probes": {}} for stage in ("home", "plp"))
        if jev_fallback:
            home["probes"]["jev_fallback"] = [
                {"stage": "plp", "purpose": "plp", "operation": "CLICK", "label": "Scarpe", "probability": 0.9,
                 "executed": True, "reason": None, "model": "jev-1.13.0", "latency_ms": 120,
                 "usage": {"input_tokens": 1000}}]
            listing["probes"]["jev_fallback"] = [
                {"stage": "pdp", "purpose": "pdp", "operation": "CLICK", "label": "Guida", "probability": 0.7,
                 "executed": True, "reason": "not_found", "model": "jev-1.13.0", "latency_ms": 80,
                 "usage": {"input_tokens": 500}},
                {"stage": "pdp", "purpose": "pdp", "executed": False, "reason": "fallback_exhausted"}]
        return [home, listing], [{"stage": s, "profile": profile, "kpi_id": None, "reason": "not_requested"}
                                 for s in STAGES[2:]]

    class Collector:
        applied_profile = None

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(audit_module, "new_context", lambda transport: "ctx")
    monkeypatch.setattr(audit_module, "close_context", lambda transport, context: None)
    monkeypatch.setattr(audit_module, "PageCollector", Collector)
    monkeypatch.setattr(audit_module, "discover_funnel", funnel)
    monkeypatch.setattr(audit_module, "run_deception_tests", lambda *a, **k: {"version": "x", "errors": []})
    monkeypatch.setattr(model, "post_json", never)
    if key:
        monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    store = RunStore(tmp_path)
    run = store.load(audit_shop(EngagementSettings(url=SHOP, profiles=["mobile", "desktop"], stages=["home", "plp"]),
                                store=store, transport_factory=FakeTransport))
    assert asked == [key, key] and run["status"] == "complete"
    if not key:
        assert "model_calls" not in run and run["warnings"] == []
        return
    calls = run["model_calls"]["crawler_fallback"]
    assert (calls["requests"], calls["latency_ms"], calls["input_tokens"], calls["model"]) == (4, 400, 3000,
                                                                                               "jev-1.13.0")
    assert [(s["profile"], s["page_id"], s["purpose"], s["reason"]) for s in calls["stages"]] == [
        (profile, f"{profile}-{page}-1", purpose, reason) for profile in ("mobile", "desktop")
        for page, purpose, reason in (("home", "plp", None), ("plp", "pdp", "not_found"),
                                      ("plp", "pdp", "fallback_exhausted"))]
    assert run["warnings"] == [f"{profile}: plp found through the Jev fallback: not reproducible across runs"
                               for profile in ("mobile", "desktop")]


# ---------------------------------------------------------------- Jev fallback: what each lookup is offered, where it
# may lead, and the page budget


class OfferShop(ScriptedShop):
    """A product page whose add-to-cart audit.js missed ("Mettilo nella sporta"), beside what neither lookup may be
    offered: a related product's in-card "Aggiungi al carrello", "Compra ora", "Rimuovi", "Procedi al checkout", and
    links to other pages (the header cart, a breadcrumb, the cart, a checkout URL); "Prendilo" adds through a
    cart-action link."""

    HREFS = {"Carrello": "/carrello", "Scarpe": "/categoria/scarpe", "Vedi la sporta": "/sporta",
             "Avanti": "/checkout", "Prendilo": "/?add-to-cart=7"}

    def __init__(self):
        super().__init__(controls=["Aggiungi al carrello", "Compra ora", "Rimuovi", "Procedi al checkout", "Carrello",
                                   "Scarpe", "Mettilo nella sporta", "Vedi la sporta", "Avanti", "Prendilo"],
                         pdp={"add_to_cart": {"present": False}, "price": {"value": 49.9}})

    def audit(self):
        return {**super().audit(), "ctas": [
            {"label": "Aggiungi al carrello", "in_card": True, "rect": self.place("Aggiungi al carrello")},
            {"label": "Compra ora", "in_card": False, "rect": self.place("Compra ora")}]}


def test_the_add_to_cart_and_cart_lookups_are_never_offered_what_would_not_add_or_open_the_cart(monkeypatch, jev):
    """add_to_cart: no in-card CTA, no buy-now, checkout or remove control, no link to another page whose GET changes
    no cart (a cart-action link stays). cart: no control the lexicon reads as adding, buying now, removing or checking
    out, no link to a checkout URL (a "buy now" that adds and jumps to the checkout is never offered)."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "quiet")
    stand_in = jev(add_to_cart="Mettilo nella sporta", cart="Vedi la sporta")
    shop = OfferShop()
    collector = SportaCollector(shop, OfferShop.HREFS, [])
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD, jev_fallback=True)
    crawl.tab.browser = collector.browser
    product = pdp_of(shop)
    cart = crawl.cart(product)
    assert cart["url"] == f"{SHOP}sporta" and collector.browser.acted == ["Mettilo nella sporta"]
    assert stand_in.asked == ["add_to_cart", "cart"]
    offered = [[e["label"] for e in body["state"]["elements"]] for body in stand_in.bodies]
    assert offered == [["Mettilo nella sporta", "Prendilo"],
                       ["Carrello", "Scarpe", "Mettilo nella sporta", "Vedi la sporta", "Prendilo"]]


def test_the_cart_says_whether_it_lists_the_item_added():
    product = {"probes": {"add_to_cart": {"title": "Scarpa da corsa Aurora"}}}

    def recognised(lines, title="Scarpa da corsa Aurora"):
        product["probes"]["add_to_cart"]["title"] = title
        Crawl.contents(product, {"audit": {"cart": {"line_items": lines}}})
        return product["probes"]["add_to_cart"]["item_recognised"]

    assert recognised([{"title": "Calzini"}, {"title": "Scarpa da corsa Aurora - 42"}]) is True
    assert recognised([{"title": "Calzini"}]) is False
    assert recognised([]) is None and recognised([{"title": "", "qty": 1}]) is None
    assert recognised([{"title": "Calzini"}], title=None) is None


class ArrivalCollector(ScriptedCollector):
    """The page the checkout click opened: (url, page type, audit)."""

    def __init__(self, shop, arrival):
        super().__init__(shop)
        self.arrival = arrival

    def collect(self, browser, *, stage, page_id):
        url, kind, audit = self.arrival
        return {"page_id": page_id, "profile": "mobile", "stage": stage, "url": url, "final_url": url,
                "classification": {"type": kind, "signals": []}, "audit": audit}


class NoCtaCart(ScriptedShop):
    def audit(self):
        return {**super().audit(), "forms": {"guest_option": False},
                "cart": {"line_items": [{"title": "Scarpa", "price_value": 49.9}], "total_value": 49.9,
                         "checkout_cta": {"present": False}}}


@pytest.mark.parametrize("arrival, reached", [
    ((SHOP, "home", {"doc": {"word_count": 10}}), False),  # "Continua gli acquisti": back to the shop
    ((f"{SHOP}account/accedi", "other", {"forms": {"login_required": True}}), True),  # an account gate
    ((f"{SHOP}checkout/indirizzo", "other", {"doc": {"word_count": 10}}), True),  # a checkout URL
])
def test_the_checkout_entry_jev_s_control_opened_must_be_a_checkout(monkeypatch, jev, arrival, reached):
    """A checkout control Jev chose is no evidence by its label: the page it opened must be a checkout page, a checkout
    URL or an account gate; else that page is "extra", checkout_entry is "checkout_cta_not_found" (as a lexicon miss)
    and no warning says it was found through Jev."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "navigated")
    stand_in = jev(checkout_entry="Continua gli acquisti")
    shop = NoCtaCart(controls=["Continua gli acquisti", "Rimuovi", "Accedi", "Registrati", "Aggiungi al carrello"])
    crawl = Crawl(ArrivalCollector(shop, arrival), EngagementSettings(url=SHOP), profile="mobile", guard=GUARD,
                  jev_fallback=True)
    crawl.tab.browser = crawl.collector.browser
    cart = {"page_id": "mobile-cart-1", "profile": "mobile", "stage": "cart", "url": f"{SHOP}cart",
            "final_url": f"{SHOP}cart", "classification": {"type": "cart"}, "audit": shop.audit()}
    if reached:
        crawl.checkout(cart)
    else:
        with pytest.raises(Stop) as stop:
            crawl.checkout(cart)
        crawl.stop(stop.value)
    assert stand_in.asked == ["checkout_entry"] and crawl.tab.browser.acted == ["Continua gli acquisti"]
    assert [e["label"] for e in stand_in.bodies[0]["state"]["elements"]] == ["Continua gli acquisti"]
    [page] = crawl.pages
    [asked] = cart["probes"]["jev_fallback"]
    assert (page["page_id"], page["stage"]) == ("mobile-checkout_entry-1", "checkout_entry" if reached else "extra")
    assert (asked["executed"], asked["reason"]) == (True, None if reached else "not_found")
    assert crawl.missing == ([] if reached else [{"stage": "checkout_entry", "profile": "mobile", "kpi_id": None,
                                                  "reason": "checkout_cta_not_found"}])
    if not reached:
        assert "checkout_entry candidate rejected: classified as home" in page["notes"]
    run = {"warnings": []}
    stages = {"home", "plp", "pdp", "cart"} | ({"checkout_entry"} if reached else set())
    audit_module._jev_fallbacks(run, "mobile", [cart, page], stages)
    assert run["model_calls"]["crawler_fallback"]["requests"] == 1
    assert run["warnings"] == (["mobile: checkout_entry found through the Jev fallback: not reproducible across runs"]
                               if reached else [])


@pytest.mark.parametrize("key", [False, True])
def test_a_full_page_budget_asks_nothing_and_keeps_the_reasons_of_a_run_without_jev(monkeypatch, key):
    """max_pages is spent (home, a rejected category): the plp and pdp lookups would need a reload and a page, so
    nothing is asked ("max_pages", no request) and every stage keeps the reason it has without the fallback."""
    site = {SHOP: ("home", {"nav": {"categories": [{"label": "Scarpe", "href": "/categoria/scarpe",
                                                    "visible": True}]}}),
            f"{SHOP}categoria/scarpe": ("other", {})}
    collector = JevSiteCollector(site, {SHOP: {"Scarpe": "/categoria/scarpe"}})
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(model, "post_json", never)
    pages, missing = discover_funnel(collector, EngagementSettings(url=SHOP, max_pages=2), profile="mobile",
                                     guard=GUARD, jev_fallback=key)
    assert [(p["page_id"], p["stage"]) for p in pages] == [("mobile-home-1", "home"), ("mobile-plp-1", "extra")]
    assert [(m["stage"], m["reason"]) for m in missing] == [("plp", "not_found"), ("pdp", "not_found"),
                                                            ("cart", "pdp_not_found"),
                                                            ("checkout_entry", "pdp_not_found")]
    probes = pages[0].get("probes", {}).get("jev_fallback")
    assert ([(p["purpose"], p["reason"], p["executed"]) for p in probes] if key else probes) == (
        [("plp", "max_pages", False), ("pdp", "max_pages", False)] if key else None)


@pytest.mark.parametrize("links, reached", [({"Menu": None, "Scarpe": "/categoria/scarpe"}, True),
                                            ({"Menu": None, "Apri il catalogo": "#"}, False)])
def test_under_a_blocking_consent_banner_a_link_lookup_offers_links_only(monkeypatch, links, reached):
    """The home page keeps a blocking consent banner (policy "none"): the plp lookup offers only links to another
    page, loaded with a GET; a control that would be clicked ("Menu", "#") is never offered, and with none left
    nothing is asked ("consent_blocking")."""
    site = {SHOP: ("home", {"overlays": [banner(button("Accetta tutti", "accept"))]}),
            f"{SHOP}categoria/scarpe": ("plp", {"products": {"main_group": 8}})}
    collector = JevSiteCollector(site, {SHOP: links})
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    stand_in = Jev(plp="Scarpe")
    monkeypatch.setattr(model, "post_json", stand_in if reached else never)
    pages, missing = discover_funnel(collector, EngagementSettings(url=SHOP, stages=["home", "plp"], consent="none"),
                                     profile="mobile", guard=GUARD, jev_fallback=True)
    reload = pages[1]
    [probe] = reload["probes"]["jev_fallback"]
    assert (reload["page_id"], reload["stage"]) == ("mobile-extra-1", "extra")
    assert [p["stage"] for p in pages] == ["home", "extra"] + (["plp"] if reached else [])
    assert {m["stage"]: m["reason"] for m in missing}.get("plp") == (None if reached else "not_found")
    if reached:
        assert [e["label"] for e in stand_in.bodies[0]["state"]["elements"]] == ["Scarpe"]
        assert (probe["label"], probe["executed"], probe["reason"]) == ("Scarpe", True, None)
    else:
        assert (probe["executed"], probe["reason"]) == (False, "consent_blocking")


def test_a_jev_link_click_that_times_out_leaves_the_cart_its_reason_and_a_new_tab_for_the_next_load(monkeypatch,
                                                                                                    jev):
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "navigated" if self.browser.acted[-1] == (
        "Vedi la sporta") else "quiet")
    jev(add_to_cart="Mettilo nella sporta", cart="Vedi la sporta")
    shop = SportaShop()
    collector = SportaCollector(shop, {}, [])

    def stuck(browser, *, stage, page_id):
        raise TimeoutError("the document never settled")
    collector.collect = stuck
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD, jev_fallback=True)
    crawl.tab.browser = collector.browser
    product = pdp_of(shop)
    with pytest.raises(Stop) as stop:
        crawl.cart(product)
    assert (stop.value.stage, stop.value.reason, stop.value.then) == ("cart", "not_found", "cart_not_found")
    assert crawl.tab.browser is None and collector.browser.acted == ["Mettilo nella sporta", "Vedi la sporta"]
    assert [(p["purpose"], p["executed"], p["reason"]) for p in product["probes"]["jev_fallback"]] == [
        ("add_to_cart", True, None), ("cart", True, "timeout")]


@pytest.mark.parametrize("answer", ["Scarpe da corsa", "Procedi al checkout"])
def test_the_checkout_lookup_is_offered_buttons_and_checkout_links_only(chromium, shop_server, tmp_path, quick,
                                                                        monkeypatch, jev, answer):
    """Ruling 4 on the fixture cart, its checkout CTA and guest path hidden from the lexicon (audit.js saw no CTA: Jev
    is asked at the top of the cart): the offer is positive on shape. The header (logo, search, cart link, categories),
    the cart lines' product links, quantity fields, "Rimuovi" and the gift box are never offered; the links to
    checkout.html are. A header category ("Scarpe da corsa") is no answer Jev can give (BLOCKED): nothing is clicked,
    checkout_entry is "checkout_cta_not_found" with no extra page and no warning. The checkout link Jev picks is
    clicked once and opens the checkout entry, found through the Jev fallback."""
    pick = Tab.pick
    monkeypatch.setattr(Tab, "pick", staticmethod(lambda page, lx, **kw: None if kw.get("key") == "checkout"
                                                  else pick(page, lx, **kw)))

    def no_guest(self, cart, audit, probe):
        audit.setdefault("cart", {})["checkout_cta"] = {"present": False}
        probe["guest"] = {"executed": False, "reason": "control_not_found"}
    monkeypatch.setattr(Crawl, "guest_path", no_guest)
    monkeypatch.setattr(audit_module, "run_deception_tests", lambda *args, **kwargs: {"version": "x", "errors": []})
    stand_in = jev(checkout_entry=answer)
    result = audited(chromium, shop_server, tmp_path, "shop/index.html", profiles=["desktop"])
    run = result["run"]
    assert stand_in.asked == ["checkout_entry"] and run["model_calls"]["crawler_fallback"]["requests"] == 1
    elements = stand_in.bodies[0]["state"]["elements"]
    offered = [e["label"] for e in elements]
    assert "Procedi al checkout" in offered and set(offered) <= {"Procedi al checkout", "Continua come ospite"}
    assert {e["role"] for e in elements} <= {"button", "link"}
    reached = answer == "Procedi al checkout"
    assert stand_in.missed == ([] if reached else ["Scarpe da corsa"])
    assert tuple(p["stage"] for p in run["pages"]) == STAGES[:4] + (("checkout_entry",) if reached else ())
    cart = run["pages"][3]
    [probe] = cart["probes"]["jev_fallback"]
    jev_steps = [(s["purpose"], s["label"]) for s in result["steps"] if s["note"]]
    if reached:
        assert run["status"] == "complete" and not run["not_assessable"]
        assert (probe["executed"], probe["reason"]) == (True, None) and run["pages"][-1]["url"].startswith(
            shop_server.url("shop/checkout.html"))
        assert jev_steps == [("checkout_entry", "Procedi al checkout")]
        assert [w for w in run["warnings"] if "Jev" in w] == [
            "desktop: checkout_entry found through the Jev fallback: not reproducible across runs"]
    else:
        assert run["status"] == "partial" and [(m["stage"], m["reason"]) for m in run["not_assessable"]] == [
            ("checkout_entry", "checkout_cta_not_found")]
        assert (probe["operation"], probe["executed"], probe["reason"]) == ("BLOCKED", False, "jev_blocked")
        assert jev_steps == [] and not [w for w in run["warnings"] if "Jev" in w]
        assert cart["probes"]["checkout_entry"]["reason"] == "control_not_found"
    assert not [r for r in result["requests"] if r["method"] == "POST" or r["path"].startswith("/pay")]


class ShapedCart(NoCtaCart):
    """A cart page audit.js read no checkout CTA on, with controls of every shape: links (HREFS) to an account page,
    a category, mail and phone, another site's checkout, the shop's checkout; a paid add-on's box and a quantity
    (ROLES); a toggle button carrying a checked state; a nameless button; buttons and script links."""

    CONTROLS = ["Area riservata", "Il tuo account", "Scarpe da corsa", "Confezione regalo (+3,00 €)", "Pezzi",
                "Imballo ecologico", "Applica codice sconto", "Scrivici", "Chiamaci", "Cassa partner", "button",
                "Vai avanti", "Concludi", "Continua", "Avanti"]
    HREFS = {"Area riservata": "/area-riservata", "Il tuo account": "/account", "Scarpe da corsa": "/categoria/scarpe",
             "Scrivici": "mailto:aiuto@shop.example", "Chiamaci": "tel:+390212345678",
             "Cassa partner": "https://partner.example/checkout", "Vai avanti": "/checkout.html", "Continua": "#",
             "Avanti": "javascript:void(0)"}
    ROLES = {"Confezione regalo (+3,00 €)": "checkbox", "Pezzi": "spinbutton"}
    OFFERED = ["Vai avanti", "Concludi", "Continua", "Avanti"]

    def __init__(self):
        super().__init__(controls=self.CONTROLS)

    def actions(self):
        actions = super().actions()
        for action in actions:
            action["role"] = self.ROLES.get(action["label"], "link" if action["label"] in self.HREFS else "button")
            if action["label"] in ("Confezione regalo (+3,00 €)", "Imballo ecologico"):
                action["checked"] = "false"
        return actions


class ShapedBrowser(CoveredBrowser):
    """ShapedCart's controls with their hrefs (CheckoutGuard's guard tuple)."""

    def observe(self, screenshot=True):
        page = super().observe(screenshot)
        page["guards"] = {str(a["node"]): [None] * GUARD_HREF + [self.shop.HREFS.get(a["label"])]
                          for a in page["actions"]}
        return page


ACCOUNT = (f"{SHOP}area-riservata", "other", {"forms": {"login_required": True}})  # the shop's account login page


@pytest.mark.parametrize("answer, effect, uncertain, reason, stage", [
    ("Area riservata", "navigated", False, "jev_blocked", None),  # an account link is no answer Jev can give
    ("Vai avanti", "navigated", False, None, "checkout_entry"),  # a same-site checkout link with an unknown label
    ("Concludi", "quiet", False, "no_navigation", None),  # Jev's click opened no page
    ("Concludi", "timeout", False, "no_navigation", None),
    ("Concludi", "navigated", True, "failed:TimeoutError", None),  # may have been delivered: never sent again
])
def test_the_checkout_cta_jev_chooses_is_a_button_or_a_checkout_link_and_must_open_the_checkout(
        monkeypatch, jev, answer, effect, uncertain, reason, stage):
    """Ruling 4: the checkout lookup's offer is positive on shape: a button or a link, clicked or leading to a
    same-site checkout URL, and no label the lexicon reads as an account, a coupon or an add-on. Never an account page
    behind a header link (rule d would read it as a forced account), a category, mail, phone or another site's link, a
    checkbox (a paid gift box), a quantity, a toggle carrying a checked state or a nameless button. A Jev click that
    opened no page or was not delivered is no checkout CTA: checkout_entry keeps the lexicon's reason
    ("checkout_cta_not_found", never "no_navigation" or "checkout_click_failed"), the probe says what happened, and no
    warning claims the stage."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: effect)
    stand_in = jev(checkout_entry=answer)
    shop = ShapedCart()
    arrival = (f"{SHOP}checkout.html", "checkout", {"doc": {"word_count": 10}}) if answer == "Vai avanti" else ACCOUNT
    collector = ArrivalCollector(shop, arrival)
    collector.browser = ShapedBrowser(shop, uncertain={answer} if uncertain else ())
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD, jev_fallback=True)
    crawl.tab.browser = collector.browser
    cart = {"page_id": "mobile-cart-1", "profile": "mobile", "stage": "cart", "url": f"{SHOP}cart",
            "final_url": f"{SHOP}cart", "classification": {"type": "cart"}, "audit": shop.audit()}
    if stage:
        crawl.checkout(cart)
    else:
        with pytest.raises(Stop) as stop:
            crawl.checkout(cart)
        assert (stop.value.stage, stop.value.reason) == ("checkout_entry", "checkout_cta_not_found")
    assert [e["label"] for e in stand_in.bodies[0]["state"]["elements"]] == ShapedCart.OFFERED
    assert stand_in.asked == ["checkout_entry"] and stand_in.missed == ([answer] if answer not in ShapedCart.OFFERED
                                                                        else [])
    assert collector.browser.sent == ([] if answer not in ShapedCart.OFFERED else [answer])
    assert [(p["page_id"], p["stage"]) for p in crawl.pages] == ([("mobile-checkout_entry-1", stage)] if stage else [])
    [asked] = cart["probes"]["jev_fallback"]
    probe = cart["probes"]["checkout_entry"]
    assert (asked["executed"], asked["reason"]) == (answer in ShapedCart.OFFERED and not uncertain, reason)
    if reason == "no_navigation":
        assert asked["effect"] == probe["effect"] == effect and probe["reason"] == "no_navigation"
    run = {"warnings": []}
    audit_module._jev_fallbacks(run, "mobile", [cart, *crawl.pages], {"home", "plp", "pdp", "cart", *filter(None,
                                                                                                          [stage])})
    assert run["model_calls"]["crawler_fallback"]["requests"] == 1
    assert run["warnings"] == (["mobile: checkout_entry found through the Jev fallback: not reproducible across runs"]
                               if stage else [])


# ---------------------------------------------------------------- Jev fallback: the architect's remaining rulings


def test_an_add_to_cart_audit_js_did_not_find_is_asked_by_the_product_s_price(monkeypatch, jev):
    """Ruling 1: no add-to-cart in audit.js and no rect for it: the tab scrolls to the price (the add-to-cart sits by
    it; observe() lists the viewport only) before the one observation Jev is asked on."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "quiet")
    scrolled = []
    monkeypatch.setattr(Tab, "scroll_to", lambda self, rect: scrolled.append(rect))
    stand_in = jev(add_to_cart="Mettilo nella sporta")
    shop = SportaShop()
    price = shop.pdp["price"]["rect"] = {"x": 10, "y": 700, "w": 80, "h": 20}
    crawl = Crawl(ScriptedCollector(shop), EngagementSettings(url=SHOP), profile="mobile", guard=GUARD,
                  jev_fallback=True)
    crawl.tab.browser = crawl.collector.browser
    product = pdp_of(shop)
    assert crawl.add_to_cart(product) == "quiet" and crawl.tab.browser.acted == ["Mettilo nella sporta"]
    assert scrolled == [price] and stand_in.asked == ["add_to_cart"]


@pytest.mark.parametrize("failure, href", [("navigation_error", None), ("renderer_crashed", None),
                                           ("bot_challenge", None), ("bot_challenge", "/sporta")])
def test_a_jev_link_that_fails_keeps_the_stage_s_reason_unless_it_stops_every_funnel(monkeypatch, jev, failure,
                                                                                    href):
    """Ruling 4: a page Jev's clicked element did not load (Chrome's error page, a crashed renderer) is a soft
    failure: the probe says why, the cart keeps its reason without the fallback ("not_found"); a bot challenge (behind
    a click or a link's GET) stops the funnel as anywhere else, and the probe says so."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "navigated" if self.browser.acted[-1] == (
        "Vedi la sporta") else "quiet")
    jev(add_to_cart="Mettilo nella sporta", cart="Vedi la sporta")
    shop = SportaShop()
    collector = SportaCollector(shop, {"Vedi la sporta": href} if href else {}, [])

    def collect(browser, *, stage, page_id):
        if failure == "renderer_crashed":
            raise RuntimeError("Session with given id not found.")
        kind, signals = ("challenge", []) if failure == "bot_challenge" else ("other", ["navigation_error"])
        return {"page_id": page_id, "profile": "mobile", "stage": stage, "url": f"{SHOP}sporta",
                "final_url": f"{SHOP}sporta", "classification": {"type": kind, "signals": signals}, "audit": {}}
    collector.collect = collect
    collector.load_page = lambda browser, url, *, stage, page_id: collect(browser, stage=stage, page_id=page_id)
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD, jev_fallback=True)
    crawl.tab.browser = collector.browser
    product = pdp_of(shop)
    with pytest.raises(Stop) as stop:
        crawl.cart(product)
    expected = ("cart", "bot_challenge", "bot_challenge") if failure == "bot_challenge" else (
        "cart", "not_found", "cart_not_found")
    assert (stop.value.stage, stop.value.reason, stop.value.then) == expected
    assert collector.browser.acted == ["Mettilo nella sporta"] + ([] if href else ["Vedi la sporta"])
    assert [(p["purpose"], p["executed"], p["reason"]) for p in product["probes"]["jev_fallback"]] == [
        ("add_to_cart", True, None), ("cart", not href, failure)]
    assert (crawl.tab.browser is None) is (failure == "renderer_crashed")
    assert [p["stage"] for p in crawl.pages] == ([] if failure == "renderer_crashed" else ["extra"])


@pytest.mark.parametrize("sold_out", [False, True])
def test_a_product_jev_picked_is_accepted_like_a_lexicon_candidate(sold_out):
    """Ruling 5: the product page Jev's link led to is accepted by is_product; an unavailable one is kept as the
    product page (as the last lexicon candidate is): its KPIs are measured and the cart is "out_of_stock", with no
    click. Ruling 6: Jev is asked on the observed page as the agent loop sends it (url, title, text, elements)."""
    listing = f"{SHOP}categoria/scarpe"
    site = {SHOP: ("home", {"nav": {"categories": [{"label": "Scarpe", "href": "/categoria/scarpe",
                                                    "visible": True}]}}),
            listing: ("plp", {"products": {"main_group": 8, "cards": [{"href": "/prodotto/guida"}]}}),
            f"{SHOP}prodotto/guida": ("other", {}), f"{SHOP}p/aurora": ("pdp", SOLD_OUT if sold_out else {})}
    collector = JevSiteCollector(site, {listing: {"Guida alle taglie": "/prodotto/guida",
                                                  "Scarpa Aurora": "/p/aurora"}})
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TYPESAFE_API_KEY", "test-key")
        stand_in = Jev(pdp="Scarpa Aurora")
        patch.setattr(model, "post_json", stand_in)
        stages = ["home", "plp", "pdp", "cart"] if sold_out else ["home", "plp", "pdp"]
        pages, missing = discover_funnel(collector, EngagementSettings(url=SHOP, stages=stages), profile="mobile",
                                         guard=GUARD, jev_fallback=True)
    product = pages[-1]
    assert (product["page_id"], product["stage"], product["url"]) == ("mobile-pdp-2", "pdp", f"{SHOP}p/aurora")
    assert stand_in.asked == ["pdp"] and stand_in.bodies[0]["state"]["page"] == {"url": listing, "title": "",
                                                                                   "text": ""}
    if sold_out:
        assert product["notes"] == ["pdp candidate unavailable: availability"]
        assert {k: product["probes"]["add_to_cart"][k] for k in ("executed", "reason", "signal")} == {
            "executed": False, "reason": "out_of_stock", "signal": "availability"}
        assert {m["stage"]: m["reason"] for m in missing} == {"cart": "out_of_stock", "checkout_entry": "not_requested"}
    else:
        assert not product.get("notes") and {m["stage"]: m["reason"] for m in missing} == {
            "cart": "not_requested", "checkout_entry": "not_requested"}


def test_jev_is_asked_on_the_observed_page_as_the_agent_loop_sends_it(monkeypatch, jev):
    """Ruling 6: the state is the observed page's url, title and text as observed, no recent actions; only the
    click actions the guard allows are elements."""
    stand_in = jev(checkout_entry="Vai alla cassa")
    page = {"url": f"{SHOP}carrello", "title": "Carrello", "text": "Il tuo carrello: 1 articolo", "guards": {},
            "scroll": {"y": 0}, "actions": [{"id": "e3", "kind": "click", "label": "Vai alla cassa", "role": "link",
                                             "node": 3}]}
    action, answer = jev_pick(page, GUARD, "cart", GOALS["checkout_entry"])
    assert action["id"] == "e3" and (answer["label"], answer["probability"], answer["reason"]) == (
        "Vai alla cassa", 1.0, None)
    state = stand_in.bodies[0]["state"]
    assert state["page"] == {"url": f"{SHOP}carrello", "title": "Carrello", "text": "Il tuo carrello: 1 articolo"}
    assert state["recent_actions"] == [] and [e["label"] for e in state["elements"]] == ["Vai alla cassa"]


def test_one_request_per_lookup_and_a_second_miss_sends_nothing(monkeypatch, jev):
    """Rulings 2 and 9: the budget is one request per lookup (a GOALS key: at most 6 per profile); asking the same
    lookup again sends nothing ("fallback_exhausted"), is recorded, and counts no request."""
    stand_in = jev(checkout_entry="DONE")
    shop = NoCtaCart(controls=["Vai alla cassa"])
    crawl = Crawl(ScriptedCollector(shop), EngagementSettings(url=SHOP), profile="mobile", guard=GUARD,
                  jev_fallback=True)
    crawl.tab.browser = crawl.collector.browser
    cart = {"page_id": "mobile-cart-1", "stage": "cart", "url": f"{SHOP}cart", "final_url": f"{SHOP}cart",
            "classification": {"type": "cart"}, "audit": shop.audit()}
    first = crawl.chooser(cart, crawl.tab.observe(), "checkout_entry")
    second = crawl.chooser(cart, crawl.tab.observe(), "checkout_entry")
    assert first[0] is None and first[1]["reason"] == "jev_done" and second == (None, cart["probes"]["jev_fallback"][1])
    assert second[1]["reason"] == "fallback_exhausted" and stand_in.asked == ["checkout_entry"] and len(GOALS) == 6
    run = {"warnings": []}
    audit_module._jev_fallbacks(run, "mobile", [cart], {"home", "plp", "pdp", "cart"})
    assert run["model_calls"]["crawler_fallback"]["requests"] == 1 and run["warnings"] == []


def test_failed_requests_count_with_their_time_and_lookups_that_asked_nothing_do_not():
    """Ruling 8: a "jev_error" is a request (its time counts, it has no tokens); a lookup refused before asking
    (crawler.NO_REQUEST: max_pages, consent_blocking, not_observed, ...) is none."""
    page = {"page_id": "mobile-home-1", "probes": {"jev_fallback": [
        {"stage": "plp", "purpose": "plp", "executed": False, "reason": "jev_error", "latency_ms": 25000,
         "error": "RuntimeError: Model connection failed; no action executed."},
        {"stage": "pdp", "purpose": "pdp", "executed": False, "reason": "max_pages"},
        {"stage": "cart", "purpose": "cart", "executed": False, "reason": "consent_blocking"},
        {"stage": "cart", "purpose": "add_to_cart", "executed": False, "reason": "not_observed"}]}}
    run = {"warnings": [], "model_calls": {"judge_jev": {"requests": 2}}}
    audit_module._jev_fallbacks(run, "mobile", [page], {"home"})
    assert run["model_calls"]["judge_jev"] == {"requests": 2}
    calls = run["model_calls"]["crawler_fallback"]
    assert (calls["requests"], calls["latency_ms"], calls["input_tokens"], calls["model"]) == (1, 25000, 0, None)
    assert [s["reason"] for s in calls["stages"]] == ["jev_error", "max_pages", "consent_blocking", "not_observed"]


class FlakyJevSiteCollector(JevSiteCollector):
    """The listing loads once; its reload for the Jev fallback fails (failure: an exception to raise, or the page
    type it lands on)."""

    def __init__(self, pages, links, failure):
        super().__init__(pages, links)
        self.failure = failure

    def load_page(self, browser, url, *, stage, page_id):
        if stage == "extra" and isinstance(self.failure, Exception):
            raise self.failure
        record = super().load_page(browser, url, stage=stage, page_id=page_id)
        if stage == "extra":
            record["classification"]["type"] = self.failure
        return record


@pytest.mark.parametrize("failure, why, stops", [(TimeoutError("the reload never settled"), "timeout", False),
                                                  (RuntimeError("Session with given id not found."),
                                                   "renderer_crashed", False),
                                                  ("challenge", "bot_challenge", True)])
def test_a_reload_that_fails_asks_nothing_and_keeps_the_stage_s_reason(monkeypatch, failure, why, stops):
    """The probe of a lookup whose reload failed says "not_observed" (no request: it counts none) and why in
    probe["reload"]; the stage keeps its reason, unless the reload met a bot challenge, which stops the funnel."""
    listing = f"{SHOP}categoria/scarpe"
    site = {SHOP: ("home", {"nav": {"categories": [{"label": "Scarpe", "href": "/categoria/scarpe",
                                                    "visible": True}]}}),
            listing: ("plp", {"products": {"main_group": 8, "cards": [{"href": "/prodotto/guida"}]}}),
            f"{SHOP}prodotto/guida": ("other", {})}
    collector = FlakyJevSiteCollector(site, {listing: {"Scarpa Aurora": "/p/aurora"}}, failure)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(model, "post_json", never)
    pages, missing = discover_funnel(collector, EngagementSettings(url=SHOP, stages=["home", "plp", "pdp"]),
                                     profile="mobile", guard=GUARD, jev_fallback=True)
    assert [(p["page_id"], p["stage"]) for p in pages] == [("mobile-home-1", "home"), ("mobile-plp-1", "plp"),
                                                           ("mobile-pdp-1", "extra")] + (
        [("mobile-extra-1", "extra")] if stops else [])
    assert {m["stage"]: m["reason"] for m in missing}["pdp"] == ("bot_challenge" if stops else "not_found")
    [probe] = pages[-1 if stops else 1]["probes"]["jev_fallback"]
    assert (probe["purpose"], probe["executed"], probe["reason"], probe["reload"]) == ("pdp", False, "not_observed",
                                                                                      why)
    run = {"warnings": []}
    audit_module._jev_fallbacks(run, "mobile", pages, {"home", "plp"})
    assert run["model_calls"]["crawler_fallback"]["requests"] == 0


def test_a_link_lookup_never_offers_a_click_that_would_change_the_cart(monkeypatch):
    """A link lookup opens a page: a link to another page is offered (a GET), and so is a control that would be
    clicked ("Menu"), unless the lexicon reads it as adding, buying now, removing or checking out (a listing's in-card
    "Aggiungi al carrello" button, a "#" "Compra ora")."""
    site = {SHOP: ("home", {}), f"{SHOP}categoria/scarpe": ("plp", {"products": {"main_group": 8}})}
    collector = JevSiteCollector(site, {SHOP: {"Aggiungi al carrello": None, "Compra ora": "#", "Menu": None,
                                               "Scarpe": "/categoria/scarpe"}})
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    stand_in = Jev(plp="Scarpe")
    monkeypatch.setattr(model, "post_json", stand_in)
    pages, _ = discover_funnel(collector, EngagementSettings(url=SHOP, stages=["home", "plp"]), profile="mobile",
                               guard=GUARD, jev_fallback=True)
    assert [e["label"] for e in stand_in.bodies[0]["state"]["elements"]] == ["Menu", "Scarpe"]
    assert [p["stage"] for p in pages] == ["home", "extra", "plp"]


def test_a_link_lookup_offers_only_links_it_would_load():
    """A link Jev picks is loaded with a GET: the offer holds only what visit() and fallback_link would load. Never a
    cart-action GET for a listing or a product (a WooCommerce card's "Aggiungi al carrello" -> ?add-to-cart=, a
    cart-action path), mailto:, tel:, another site or a checkout URL; for the cart a cart-action link stays (its keys
    are dropped, cart_url()'s rule) unless the lexicon reads it as adding. A control without an href is a click."""
    links = {"Aggiungi al carrello": "?add-to-cart=1", "Prendilo": "/?add-to-cart=7", "Svuotalo": "/carrello/svuota",
             "Scrivici": "mailto:aiuto@shop.example", "Chiamaci": "tel:+390212345678",
             "Partner": "https://other.example/scarpe", "Cassa": "/checkout", "Scarpe": "/categoria/scarpe",
             "Aurora": "/prodotto/aurora", "Menu": None, "Rimuovi": None}
    page = {"url": f"{SHOP}categoria/tutte",
            "actions": [{"id": f"e{n}", "kind": "click", "label": label, "role": "link" if href else "button",
                         "node": n} for n, (label, href) in enumerate(links.items())],
            "guards": {str(n): [None] * GUARD_HREF + [href] for n, href in enumerate(links.values())}}
    crawl = crawl_of(ScriptedShop())
    listing = record({}, url=page["url"], kind="plp")
    offered = {stage: [a["label"] for a in page["actions"] if crawl.link_offer(stage, listing, False)(a, page)]
               for stage in ("plp", "pdp", "cart")}
    assert offered == {"plp": ["Scarpe", "Aurora", "Menu"], "pdp": ["Scarpe", "Aurora", "Menu"],
                       "cart": ["Prendilo", "Scarpe", "Aurora", "Menu"]}
    blocked = [a["label"] for a in page["actions"] if crawl.link_offer("pdp", listing, True)(a, page)]
    assert blocked == ["Scarpe", "Aurora"]  # under a blocking consent banner: no click


@pytest.mark.parametrize("answers", [{"model": "jev-1.13.0", "answers": None}, {"model": "jev-1.13.0", "answers": []},
                                     {"model": "jev-1.13.0", "answers": "CLICK"}, {"answers": {}}, ["CLICK"], None])
def test_an_answer_of_the_wrong_shape_is_a_failed_request_and_the_funnel_goes_on(monkeypatch, answers):
    """TypeSafe answering HTTP 200 with a body model.choose cannot read ("answers" null, a list or a string; no
    model; no object at all) is a "jev_error" like an HTTP error: counted as a request with its time, the lookup finds
    nothing, the stage keeps its reason without the fallback and the funnel goes on (never "error: AttributeError"
    for every stage left)."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(model, "post_json", lambda url, key, body: copy.deepcopy(answers))
    site = {SHOP: ("home", {"products": {"cards": [{"href": "/p/aurora"}]}}), f"{SHOP}p/aurora": ("pdp", {})}
    collector = JevSiteCollector(site, {SHOP: {"Scarpe": "/categoria/scarpe"}})
    pages, missing = discover_funnel(collector, EngagementSettings(url=SHOP, stages=["home", "plp", "pdp"]),
                                     profile="mobile", guard=GUARD, jev_fallback=True)
    assert [(p["page_id"], p["stage"]) for p in pages] == [("mobile-home-1", "home"), ("mobile-extra-1", "extra"),
                                                           ("mobile-pdp-1", "pdp")]
    assert {m["stage"]: m["reason"] for m in missing} == {"plp": "not_found", "cart": "not_requested",
                                                          "checkout_entry": "not_requested"}
    [probe] = pages[1]["probes"]["jev_fallback"]
    assert (probe["purpose"], probe["executed"], probe["reason"]) == ("plp", False, "jev_error")
    assert isinstance(probe["latency_ms"], int) and "usage" not in probe and probe["error"]
    run = {"warnings": []}
    audit_module._jev_fallbacks(run, "mobile", pages, {"home", "pdp"})
    assert run["model_calls"]["crawler_fallback"]["requests"] == 1 and run["warnings"] == []


@pytest.mark.parametrize("purpose", sorted(GOALS))
def test_a_control_without_an_accessible_name_is_never_offered(monkeypatch, jev, purpose):
    """Ruling 1: snapshot.js labels a nameless control with its bare role ("button", "link"; "Open textbox" for a
    field's click): Jev chooses by label, so such a control is never offered, in any lookup; with nothing else
    offered nothing is asked."""
    stand_in = jev(**{purpose: "Scarpe"})
    page = {"url": SHOP, "title": "", "text": "", "guards": {}, "actions": [
        {"id": "e1", "kind": "click", "label": "button", "role": "button", "node": 1},
        {"id": "e2", "kind": "click", "label": "Link", "role": "link", "node": 2},
        {"id": "e3", "kind": "click", "label": "Open textbox", "role": "textbox", "node": 3},
        {"id": "e4", "kind": "click", "label": "Scarpe", "role": "link", "node": 4}]}
    action, answer = jev_pick(page, GUARD, "home", GOALS[purpose])
    assert action["id"] == "e4" and [e["label"] for e in stand_in.bodies[0]["state"]["elements"]] == ["Scarpe"]
    nameless = {**page, "actions": page["actions"][:3]}
    assert jev_pick(nameless, GUARD, "home", GOALS[purpose]) == (None, {"reason": "no_click_actions"})
    assert stand_in.asked == [purpose]


def test_an_unlabelled_add_to_cart_icon_is_not_found(monkeypatch):
    """Ruling 1 for the add-to-cart: an icon button without an accessible name is no guess Jev may make (a stray
    line in the funnel cart would be a false delta): nothing is asked, nothing is clicked, add_to_cart_not_found."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(model, "post_json", never)
    shop = ScriptedShop(controls=["button"], pdp={"add_to_cart": {"present": False}, "price": {"value": 49.9}})
    crawl = Crawl(ScriptedCollector(shop), EngagementSettings(url=SHOP), profile="mobile", guard=GUARD,
                  jev_fallback=True)
    crawl.tab.browser = crawl.collector.browser
    product = pdp_of(shop)
    with pytest.raises(Stop, match="add_to_cart_failed"):
        crawl.add_to_cart(product)
    assert crawl.tab.browser.acted == [] and product["probes"]["add_to_cart"]["reason"] == "add_to_cart_not_found"
    [asked] = product["probes"]["jev_fallback"]
    assert (asked["purpose"], asked["executed"], asked["reason"]) == ("add_to_cart", False, "no_click_actions")


def test_only_a_pick_that_led_to_its_stage_gets_the_jev_warning():
    """Ruling 2: the warning claims the stage was found through Jev, so it needs an executed pick whose probe holds
    no failure reason; a pick that executed and then failed ("not_found", "no_navigation") is listed in stages with
    its reason, even when the stage was reached another way."""
    page = {"page_id": "mobile-cart-1", "probes": {"jev_fallback": [
        {"stage": "pdp", "purpose": "pdp", "executed": True, "reason": "not_found", "latency_ms": 90},
        {"stage": "checkout_entry", "purpose": "checkout_entry", "executed": True, "reason": "no_navigation"},
        {"stage": "cart", "purpose": "cart", "executed": True, "reason": None, "latency_ms": 80}]}}
    run = {"warnings": []}
    audit_module._jev_fallbacks(run, "mobile", [page], set(STAGES))
    assert run["warnings"] == ["mobile: cart found through the Jev fallback: not reproducible across runs"]
    assert [(s["stage"], s["reason"]) for s in run["model_calls"]["crawler_fallback"]["stages"]] == [
        ("pdp", "not_found"), ("checkout_entry", "no_navigation"), ("cart", None)]


# ---------------------------------------------------------------- Jev fallback: round 5 rulings and fixes


def cart_of(shop):
    return {"page_id": "mobile-cart-1", "profile": "mobile", "stage": "cart", "url": f"{SHOP}cart",
            "final_url": f"{SHOP}cart", "classification": {"type": "cart"}, "audit": shop.audit()}


class CrossSellCart(NoCtaCart):
    """A cart page audit.js read no checkout CTA on, whose "Acquista ora" sits in a cross-sell product card (audit.js's
    in-card CTA)."""

    def audit(self):
        return {**super().audit(), "ctas": [{"label": "Acquista ora", "in_card": True,
                                             "rect": self.place("Acquista ora")}]}


GATE = (f"{SHOP}accedi", "other", {"forms": {"login_required": True}})  # a same-site login wall
UTILITY_CART = ["Svuota carrello", "Aggiorna carrello", "Calcola spedizione", "Il tuo account",
                "Procedi alla spedizione", "Conferma carrello", "Checkout senza account"]
PAYPAL = "https://www.paypal.com/checkoutnow?token=EC-1"


@pytest.mark.parametrize("controls, answer, arrival, offered, reached", [
    (UTILITY_CART, "Procedi alla spedizione", (f"{SHOP}checkout/spedizione", "checkout", {"doc": {"word_count": 10}}),
     UTILITY_CART[4:], True),  # rulings 1 and 6: a utility label offered only when it also reads as a way on
    (["Acquista ora", "Ordina adesso"], "Ordina adesso", GATE, ["Ordina adesso"],
     True),  # ruling 5b: a same-site login wall is rule d's gate, whatever the CTA's label
    (["Acquista ora", "Ordina adesso"], "Ordina adesso", (PAYPAL, "checkout", {"forms": {"login_required": True}}),
     ["Ordina adesso"], False),  # ruling 5a: Jev's arrival off the shop's site is no checkout entry
])
def test_the_checkout_lookup_offers_the_ways_on_and_keeps_only_an_arrival_on_the_shop_s_site(
        monkeypatch, jev, controls, answer, arrival, offered, reached):
    """Rulings 1/6 and 5: the checkout lookup never offers a utility control ("Svuota carrello", "Aggiorna carrello",
    "Calcola spedizione", "Il tuo account") unless the lexicon also reads it as a way on ("Procedi alla spedizione",
    "Conferma carrello", "Checkout senza account"), nor a cross-sell card's "Acquista ora"; a top-level "Ordina adesso"
    is offered. The page Jev's control opened must be on the shop's own site: a hosted pay page or another site's login
    is demoted with a note naming its host, checkout_entry is "checkout_cta_not_found" and no warning claims it."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "navigated")
    stand_in = jev(checkout_entry=answer)
    shop = CrossSellCart(controls=controls)
    crawl = Crawl(ArrivalCollector(shop, arrival), EngagementSettings(url=SHOP), profile="mobile", guard=GUARD,
                  jev_fallback=True)
    crawl.tab.browser = crawl.collector.browser
    cart = cart_of(shop)
    if reached:
        crawl.checkout(cart)
    else:
        with pytest.raises(Stop) as stop:
            crawl.checkout(cart)
        assert (stop.value.stage, stop.value.reason) == ("checkout_entry", "checkout_cta_not_found")
    assert [e["label"] for e in stand_in.bodies[0]["state"]["elements"]] == offered
    assert stand_in.asked == ["checkout_entry"] and crawl.tab.browser.acted == [answer]
    [page] = crawl.pages
    [asked] = cart["probes"]["jev_fallback"]
    assert (page["stage"], asked["executed"], asked["reason"]) == (
        ("checkout_entry", True, None) if reached else ("extra", True, "not_found"))
    if not reached:
        assert page["notes"] == ["checkout_entry candidate rejected: on another site (www.paypal.com)"]
        assert cart["probes"]["checkout_entry"]["reason"] == "not_found"
    run = {"warnings": []}
    audit_module._jev_fallbacks(run, "mobile", [cart, page], {"home", "plp", "pdp", "cart"} | (
        {"checkout_entry"} if reached else set()))
    assert run["model_calls"]["crawler_fallback"]["requests"] == 1
    assert run["warnings"] == (["mobile: checkout_entry found through the Jev fallback: not reproducible across runs"]
                               if reached else [])


@pytest.mark.parametrize("purpose", sorted(GOALS))
def test_a_field_s_click_is_never_offered(jev, purpose):
    """Ruling 4: snapshot.js adds a click twin ("Open <name>") to every text field, on the field's node: it only
    focuses the field, so no lookup is offered it (it would spend the lookup's one request). Detected by node, not by
    role: a custom role=combobox (a div, no fill twin) is a variant or sort control and stays."""
    stand_in = jev(**{purpose: "Scarpe"})
    page = {"url": SHOP, "title": "", "text": "", "guards": {}, "actions": [
        {"id": "e1", "kind": "fill", "label": "Cerca", "role": "searchbox", "node": 1},
        {"id": "e2", "kind": "click", "label": "Open Cerca", "role": "searchbox", "node": 1},
        {"id": "e3", "kind": "click", "label": "Scarpe", "role": "link", "node": 3}]}
    action, _ = jev_pick(page, GUARD, "home", GOALS[purpose])
    assert action["id"] == "e3" and [e["label"] for e in stand_in.bodies[0]["state"]["elements"]] == ["Scarpe"]
    sort = {**page, "actions": [*page["actions"], {"id": "e4", "kind": "click", "label": "Ordina per",
                                                   "role": "combobox", "node": 4}]}
    jev_pick(sort, GUARD, "home", GOALS[purpose])
    assert [e["label"] for e in stand_in.bodies[1]["state"]["elements"]] == ["Scarpe", "Ordina per"]
    assert stand_in.asked == [purpose, purpose]


def test_the_cart_and_add_to_cart_lookups_offer_only_the_shape_of_a_control_that_opens_or_adds():
    """A product page with an open mini-cart: a click the cart lookup or the add-to-cart lookup is offered is a button
    or a link with no checked state and no quantity, coupon or add-on label, as the checkout lookup's. A paid add-on
    Jev ticked would be in the cart deception.py reads (prechecked_paid, sneak_into_basket would blame the shop)."""
    controls = [("Aumenta quantità", "button", None, {}), ("Diminuisci quantità", "button", None, {}),
                ("Protezione spedizione Premium (+2,90 €)", "checkbox", None, {"checked": "false"}),
                ("Garanzia estesa 2 anni (+29,00 €)", "checkbox", None, {"checked": "false"}),
                ("Imballo ecologico", "button", None, {"checked": "false"}),
                ("Applica codice sconto", "button", None, {}),
                ("Descrizione", "tab", None, {}), ("Rimuovi", "button", None, {}),
                ("Mettilo nella sporta", "button", None, {}), ("Vedi la sporta", "link", "/sporta", {})]
    page = {"url": f"{SHOP}p/aurora", "scroll": {"y": 0},
            "actions": [{"id": f"e{n}", "kind": "click", "label": label, "role": role, "node": n, **extra}
                        for n, (label, role, _, extra) in enumerate(controls)],
            "guards": {str(n): [None] * GUARD_HREF + [href] for n, (_, _, href, _) in enumerate(controls)}}
    crawl = crawl_of(ScriptedShop())
    product = record({}, url=page["url"], kind="pdp")
    offered = {stage: [a["label"] for a in page["actions"] if crawl.link_offer(stage, product, False)(a, page)]
               for stage in ("plp", "pdp", "cart")}
    assert offered == {stage: ["Mettilo nella sporta", "Vedi la sporta"] for stage in ("plp", "pdp", "cart")}
    assert [a["label"] for a in page["actions"] if crawl.add_offer(product, {})(a, page)] == ["Mettilo nella sporta"]


# Controls that empty the whole cart, and the ways to the cart and on that every offer keeps (per page language; the
# Italian lexicon merges English).
CLEARING = {"it": ["Svuota carrello", "Svuota il carrello", "Svuota", "Azzera il carrello", "Empty cart"],
            "en": ["Empty cart", "Clear cart", "Empty basket", "Clear bag", "Clear basket", "Clear your cart", "Empty"]}
OPENING = {"it": ["Vai al carrello", "Visualizza carrello"], "en": ["View cart", "Go to cart"]}
WAY_ON = {"it": "Concludi", "en": "Continue to shipping"}


@pytest.mark.parametrize("lang", ["it", "en"])
def test_no_lookup_offers_a_control_that_empties_the_cart(lang):
    """A control that empties the cart ("Svuota carrello", a cart's bare "Svuota", "Empty cart", "Clear bag") is read
    as removing (crawler.REMOVING, the lexicon's "clear_cart") and never offered to Jev: not by the cart lookup (a
    mini-cart's "Svuota carrello" pick would empty the cart the audit then reads), the add-to-cart lookup or the
    checkout lookup, as a button or as a link whose cart-action keys the cart lookup would drop. The cart lookup still
    offers the way to the cart ("Vai al carrello", "View cart") and the checkout lookup the way on. The guard allows
    every one of them: the offers leave them out."""
    lexicon = compile_lexicon(lexicon_for(lang))
    assert all(lexicon["clear_cart"].search(label) for label in CLEARING[lang])
    assert not [label for label in [*OPENING[lang], WAY_ON[lang], "Svuotamento magazzino", "Aggiorna carrello",
                                    "Update cart", "Clear filters"] if lexicon["clear_cart"].search(label)]
    controls = [(label, "button", None) for label in [*CLEARING[lang], *OPENING[lang], WAY_ON[lang]]] + [
        (label, "link", "/carrello/" if label in OPENING[lang] else "/carrello/?empty_cart=1")
        for label in [*CLEARING[lang], *OPENING[lang]]]
    page = {"url": f"{SHOP}p/aurora", "scroll": {"y": 0},
            "actions": [{"id": f"e{n}", "kind": "click", "label": label, "role": role, "node": n}
                        for n, (label, role, _) in enumerate(controls)],
            "guards": {str(n): [None] * GUARD_HREF + [href] for n, (_, _, href) in enumerate(controls)}}
    in_cart = {**page, "url": f"{SHOP}carrello"}
    assert GUARD.filter_actions(page, "pdp")[0] == page["actions"]
    assert GUARD.filter_actions(in_cart, "cart")[0] == page["actions"]
    crawl = crawl_of(ScriptedShop(lang=lang))
    product = record({"lexicon_lang": lang}, url=page["url"], kind="pdp")
    cart = record({"lexicon_lang": lang}, url=in_cart["url"], kind="cart")

    def offered(accept, on):
        return [(a["label"], a["role"]) for a in on["actions"] if accept(a, on)]

    for accept, on in ((crawl.link_offer("cart", product, False), page), (crawl.add_offer(product, {}), page),
                       (crawl.checkout_offer(cart, {}), in_cart)):
        assert not [label for label, _ in offered(accept, on) if label in CLEARING[lang]]
    assert offered(crawl.link_offer("cart", product, False), page) == [
        *((label, "button") for label in [*OPENING[lang], WAY_ON[lang]]), *((label, "link") for label in OPENING[lang])]
    assert offered(crawl.checkout_offer(cart, {}), in_cart) == [(WAY_ON[lang], "button")]


@pytest.mark.parametrize("lang", ["it", "en"])
def test_the_cart_lookup_never_asks_jev_about_a_control_that_empties_the_cart(jev, lang):
    """An open mini-cart with an "empty the cart" button: the cart lookup's one request offers the way to the cart
    only, and Jev's pick is that one; with nothing else on the page nothing is asked (no request, no click)."""
    clear, view = CLEARING[lang][0], OPENING[lang][0]
    stand_in = jev(cart=view)
    crawl = crawl_of(ScriptedShop(lang=lang))
    product = record({"lexicon_lang": lang}, url=f"{SHOP}p/aurora", kind="pdp")
    drawer = {"url": f"{SHOP}p/aurora", "title": "Aurora", "text": "", "guards": {}, "actions": [
        {"id": "e1", "kind": "click", "label": clear, "role": "button", "node": 1},
        {"id": "e2", "kind": "click", "label": view, "role": "button", "node": 2}]}
    action, probe = jev_pick(drawer, GUARD, "pdp", GOALS["cart"], crawl.link_offer("cart", product, False))
    assert [e["label"] for e in stand_in.bodies[0]["state"]["elements"]] == [view]
    assert (action["label"], probe["label"], probe["reason"]) == (view, view, None)
    alone = {**drawer, "actions": drawer["actions"][:1]}
    assert jev_pick(alone, GUARD, "pdp", GOALS["cart"], crawl.link_offer("cart", product, False)) == (
        None, {"reason": "no_click_actions"})
    assert stand_in.asked == ["cart"]


class LateBannerCart(NoCtaCart):
    """A blocking consent banner with "Accetta tutti" only shows up once the first click met a stale page."""

    stale_seen = False

    def audit(self):
        audit = super().audit()
        if self.stale_seen:
            audit["overlays"] = [banner(button("Accetta tutti", "accept"))]
        return audit


class StaleOnceBrowser(ScriptedBrowser):
    def act(self, action, page, text=None):
        if not self.shop.stale_seen:
            self.shop.stale_seen = True
            raise StalePage("covered")
        return super().act(action, page, text)


@pytest.mark.parametrize("label, key", [("Procedi al checkout", False), ("Vai avanti", True)])
def test_a_blocking_banner_on_the_re_observe_stops_any_checkout_click_as_consent_blocking(monkeypatch, jev, label,
                                                                                          key):
    """The checkout CTA (the lexicon's, or Jev's pick) met a stale page; on the one re-observe a blocking banner that
    "reject" cannot clear is up: no click, and checkout_entry is "consent_blocking" whoever chose the control. Jev's
    probe keeps its own outcome (nothing sent, a request asked: it counts)."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "navigated")
    stand_in = jev(checkout_entry=label)
    shop = LateBannerCart(controls=[label])
    collector = ScriptedCollector(shop)
    collector.browser = StaleOnceBrowser(shop)
    crawl = Crawl(collector, EngagementSettings(url=SHOP, consent="reject"), profile="mobile", guard=GUARD,
                  jev_fallback=key)
    crawl.tab.browser = collector.browser
    cart = cart_of(shop)
    with pytest.raises(Stop) as stop:
        crawl.checkout(cart)
    assert (stop.value.stage, stop.value.reason) == ("checkout_entry", "consent_blocking")
    assert collector.browser.acted == [] and cart["probes"]["checkout_entry"]["reason"] == "consent_blocking"
    assert stand_in.asked == (["checkout_entry"] if key else [])
    if key:
        [asked] = cart["probes"]["jev_fallback"]
        assert (asked["executed"], asked["reason"], asked["stale_reobserved"]) == (False, "not_executed:covered", 1)
        run = {"warnings": []}
        audit_module._jev_fallbacks(run, "mobile", [cart], {"home", "plp", "pdp", "cart"})
        assert run["model_calls"]["crawler_fallback"]["requests"] == 1 and run["warnings"] == []


def failing_collect(failure, url=f"{SHOP}cassa"):
    """collect() for a click whose page failed: an exception to raise, or the page type it lands on."""
    def collect(browser, *, stage, page_id):
        if failure == "timeout":
            raise TimeoutError("the document never settled")
        if failure == "renderer_crashed":
            raise RuntimeError("Session with given id not found.")
        kind, signals = ("challenge", []) if failure == "bot_challenge" else ("other", ["navigation_error"])
        return {"page_id": page_id, "profile": "mobile", "stage": stage, "url": url, "final_url": url,
                "classification": {"type": kind, "signals": signals}, "audit": {}}
    return collect


@pytest.mark.parametrize("failure", ["navigation_error", "timeout", "renderer_crashed", "bot_challenge"])
def test_what_stops_the_funnel_after_jev_s_checkout_click_is_in_its_probe(monkeypatch, jev, failure):
    """Jev's checkout click navigated, but its page did not load (Chrome's error page, a timeout, a crashed renderer)
    or was a bot challenge: the probe says why (never the executed, reasonless shape of a pick that worked). A page
    that did not load is no checkout: "checkout_cta_not_found", the lexicon's reason; a bot challenge stops every
    funnel with its own reason."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "navigated")
    stand_in = jev(checkout_entry="Vai avanti")
    shop = NoCtaCart(controls=["Vai avanti"])
    collector = ScriptedCollector(shop)
    collector.collect = failing_collect(failure)
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD, jev_fallback=True)
    crawl.tab.browser = collector.browser
    cart = cart_of(shop)
    with pytest.raises(Stop) as stop:
        crawl.checkout(cart)
    assert (stop.value.stage, stop.value.reason) == (
        "checkout_entry", "bot_challenge" if failure == "bot_challenge" else "checkout_cta_not_found")
    assert stand_in.asked == ["checkout_entry"] and collector.browser.acted == ["Vai avanti"]
    [asked] = cart["probes"]["jev_fallback"]
    assert (asked["executed"], asked["reason"]) == (True, failure)
    if failure != "bot_challenge":
        assert cart["probes"]["checkout_entry"]["reason"] == failure
    assert [p["stage"] for p in crawl.pages] == ([] if failure in ("timeout", "renderer_crashed") else ["extra"])
    run = {"warnings": []}
    audit_module._jev_fallbacks(run, "mobile", [cart, *crawl.pages], {"home", "plp", "pdp", "cart"})
    assert [s["reason"] for s in run["model_calls"]["crawler_fallback"]["stages"]] == [failure]
    assert run["warnings"] == [] and run["model_calls"]["crawler_fallback"]["requests"] == 1


def test_an_error_page_after_jev_s_add_to_cart_is_in_its_probe(monkeypatch, jev):
    """Jev's add-to-cart click navigated to Chrome's error page: the cart stage is "navigation_error" (as after the
    lexicon's click) and the add-to-cart lookup's probe says so."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "navigated")
    jev(add_to_cart="Mettilo nella sporta")
    shop = SportaShop()
    collector = ScriptedCollector(shop)
    collector.collect = failing_collect("navigation_error")
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD, jev_fallback=True)
    crawl.tab.browser = collector.browser
    product = pdp_of(shop)
    with pytest.raises(Stop) as stop:
        crawl.cart(product)
    assert (stop.value.stage, stop.value.reason) == ("cart", "navigation_error")
    [asked] = product["probes"]["jev_fallback"]
    assert (asked["purpose"], asked["executed"], asked["reason"]) == ("add_to_cart", True, "navigation_error")
    assert [p["stage"] for p in crawl.pages] == ["extra"]


class GoneBrowser(LinkBrowser):
    """A click on one of the gone labels reaches the page, then the session or the transport goes."""

    def __init__(self, shop, hrefs, gone, error):
        super().__init__(shop, hrefs)
        self.gone, self.error = gone, error

    def act(self, action, page, text=None):
        if action["label"] in self.gone:
            self.acted.append(action["label"])
            raise self.error
        return super().act(action, page, text)


@pytest.mark.parametrize("purpose", ["add_to_cart", "cart"])
@pytest.mark.parametrize("error", [ConnectionError("the transport closed"),
                                   RuntimeError("Session with given id not found.")])
def test_a_jev_click_whose_session_or_transport_went_is_in_its_probe(monkeypatch, jev, purpose, error):
    """Tab.act raises after Jev's click may have reached the page (a closed transport, a gone session): the funnel
    stops, and the lookup's probe says "failed:<error>" (executed False, as Tab.act's own failure), never the picked
    shape with no reason. A click lookup (the add-to-cart) and a link lookup clicking an element without href (the
    cart)."""
    monkeypatch.setattr(Tab, "await_effect", lambda self, origin, **kw: "quiet")
    jev(add_to_cart="Mettilo nella sporta", cart="Vedi la sporta")
    shop = SportaShop()
    collector = SportaCollector(shop, {}, [])
    gone = "Mettilo nella sporta" if purpose == "add_to_cart" else "Vedi la sporta"
    collector.browser = GoneBrowser(shop, {}, {gone}, error)
    crawl = Crawl(collector, EngagementSettings(url=SHOP), profile="mobile", guard=GUARD, jev_fallback=True)
    crawl.tab.browser = collector.browser
    product = pdp_of(shop)
    with pytest.raises(type(error)):
        crawl.cart(product)
    probes = {p["purpose"]: (p["executed"], p["reason"]) for p in product["probes"]["jev_fallback"]}
    assert probes[purpose] == (False, f"failed:{type(error).__name__}") and collector.browser.acted[-1] == gone
    if purpose == "cart":
        assert probes["add_to_cart"] == (True, None)


def test_the_jev_warning_says_whether_the_stage_s_own_lookup_opened_it():
    """A cart reached through the lexicon's cart link after Jev picked the variant option and the add-to-cart is
    "reached after a Jev pick" naming those lookups; "found through the Jev fallback" only when the stage's own lookup
    opened its page."""
    picks = [{"stage": "cart", "purpose": purpose, "executed": True, "reason": None}
             for purpose in ("select_variant", "add_to_cart")]
    run = {"warnings": []}
    audit_module._jev_fallbacks(run, "mobile", [{"page_id": "mobile-pdp-1", "probes": {"jev_fallback": picks}}],
                                set(STAGES))
    assert run["warnings"] == [
        "mobile: cart reached after a Jev pick (select_variant, add_to_cart): not reproducible across runs"]
    picks.append({"stage": "cart", "purpose": "cart", "executed": True, "reason": None})
    run = {"warnings": []}
    audit_module._jev_fallbacks(run, "mobile", [{"page_id": "mobile-pdp-1", "probes": {"jev_fallback": picks}}],
                                set(STAGES))
    assert run["warnings"] == ["mobile: cart found through the Jev fallback: not reproducible across runs"]
