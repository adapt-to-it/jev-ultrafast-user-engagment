"""Journeys: the host policy contract, the Agent's injection points, oracles, and journeys on the fixture shop.

Browser tests drive headless Chromium on the local fixture shop with a scripted host that picks indices by label from
the observation; they never call a paid API and skip when Chromium is missing.
"""

import json
import re
import threading
import time
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast.browser import StalePage, fingerprint
from jev_ultrafast.engagement import friction, oracles
from jev_ultrafast.engagement.collectors import AuditError, PageCollector
from jev_ultrafast.engagement.friction import executed as step_executed
from jev_ultrafast.engagement.journey import (
    HOST_FINISH,
    MAX_STALE,
    NO_PROGRESS,
    UNREAD,
    GuardRefused,
    HostPolicy,
    JourneyRunner,
    check_text,
    personal_text,
    resolve,
)
from jev_ultrafast.engagement.kpis import by_producer
from jev_ultrafast.engagement.lexicon import lexicon_for
from jev_ultrafast.engagement.safety import CheckoutGuard
from jev_ultrafast.engagement.settings import EngagementSettings
from jev_ultrafast.engagement.store import RunStore
from jev_ultrafast.engagement.transport import DirectTransport
from jev_ultrafast.model import choose
from jev_ultrafast.questions import MAX_STEPS

CHOOSE_KEYS = {"choice", "operation", "target", "confidence", "probabilities", "operation_probabilities",
               "target_probabilities", "target_confidence", "raw_answers", "model", "usage", "latency_ms", "request"}


def page(**changes):
    actions = [
        {"id": "e1", "kind": "fill", "label": "Cerca nel negozio", "role": "searchbox", "value": "", "node": 10},
        {"id": "e2", "kind": "click", "label": "Open Cerca nel negozio", "role": "searchbox", "value": "", "node": 10},
        {"id": "e3", "kind": "click", "label": "Aggiungi al carrello", "role": "button", "value": "", "node": 20},
        {"id": "e4", "kind": "select", "label": "Ordina per → Prezzo crescente", "role": "combobox", "value": "asc",
         "current_value": "Rilevanza", "node": 30},
        {"id": "e5", "kind": "select", "label": "Ordina per → Prezzo decrescente", "role": "combobox",
         "value": "desc", "current_value": "Rilevanza", "node": 30},
        {"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560},
        {"id": "wait", "kind": "wait", "label": "Wait for the page to update"},
    ]
    state = {"url": "https://shop.test/", "title": "Shop", "text": "Scarpe", "scroll": {"y": 0}, "actions": actions}
    state.update(changes)
    state["fingerprint"] = fingerprint(state)
    return state


def host(p=None):
    policy = HostPolicy()
    policy.offer(p or page())
    return policy


# ---------------------------------------------------------------- HostPolicy

def test_host_decision_has_the_choose_contract_and_is_consumed_once():
    policy = host()
    time.sleep(0.02)
    policy.set("CLICK", "2")
    decision = policy(page(), "goal", [])
    assert set(decision) == CHOOSE_KEYS
    assert decision["choice"] == "e3" and decision["operation"] == "CLICK" and decision["target"] == "2"
    assert decision["probabilities"] == {"e3": 1.0} and decision["confidence"] == 1.0
    assert decision["model"] == "host" and decision["latency_ms"] >= 20
    with pytest.raises(ValueError, match="not chosen"):
        policy(page(), "goal", [])


@pytest.mark.parametrize("operation, target, choice", [
    ("CLICK", 2, "e3"), ("click", " 2 ", "e3"), ("CLICK", "1", "e2"), ("SELECT", "3:2", "e5"),
    ("SCROLL_DOWN", None, "scroll_down"), ("WAIT", "", "wait"), ("DONE", None, "DONE"), ("BLOCKED", None, "BLOCKED"),
])
def test_host_may_pick_any_offered_operation_and_index(operation, target, choice):
    policy = host()
    policy.set(operation, target)
    assert policy(page(), "goal", [])["choice"] == choice


@pytest.mark.parametrize("operation, target", [
    ("CLICK", "9"), ("CLICK", "0"), ("CLICK", -1), ("SELECT", "3"), ("SELECT", "3:9"), ("SELECT", "2:1"),
    ("TYPE_TEXT", "2"), ("SCROLL_UP", None), ("WAIT", "2"), ("DONE", "1"), ("PRESS_ENTER", None), (None, "2"),
])
def test_host_cannot_pick_an_index_or_operation_the_page_does_not_offer(operation, target):
    with pytest.raises(ValueError):
        host().set(operation, target, "scarpe" if operation == "TYPE_TEXT" else None)


@pytest.mark.parametrize("target", [
    "#add", "button.btn-primary", "//button[1]", "e3", "2; alert(1)", "document.querySelector('a')", "a[href]",
    "2 OR 1=1", {"index": 2}, [2], 2.0, True, "3:2:1", "１",
])
def test_selector_like_targets_are_rejected(target):
    with pytest.raises(ValueError, match="not an offered element index"):
        host().set("CLICK", target)


def test_text_is_only_for_type_text_and_never_personal_data():
    with pytest.raises(ValueError, match="only accepted with TYPE_TEXT"):
        host().set("CLICK", "2", "scarpe")
    with pytest.raises(ValueError, match="non-empty"):
        host().set("TYPE_TEXT", "1")
    for text in ("mario.rossi@example.com", "4111 1111 1111 1111", "+39 333 123 4567", "IT60X0542811101000000123456",
                 "RSSMRA85T10A562S"):
        with pytest.raises(ValueError, match="personal or payment"):
            host().set("TYPE_TEXT", "1", text)
    policy = host()
    policy.set("TYPE_TEXT", "1", "scarpe da trail 42")
    assert policy(page(), "goal", [])["choice"] == "e1"
    assert policy.text({"goal": "goal"}) == ("scarpe da trail 42", {"model": "host", "latency_ms": 0, "usage": {}})
    assert personal_text("scarpe taglia 42") is None and check_text("2") == "2"
    assert personal_text("XR12ABCDEF12345") is None  # a product code, not an IBAN (fewer than ten digits)
    assert personal_text("AB12 CD34 567E F890 1GH2 34") == "an IBAN"  # ten digits or more, no long digit run


def test_a_choice_whose_index_now_names_another_element_is_stale():
    policy = host()
    policy.set("CLICK", "2")
    moved = page(actions=[{"id": "e1", "kind": "click", "label": "Novità", "role": "link", "value": "", "node": 99},
                          *page()["actions"]])
    with pytest.raises(StalePage):
        policy(moved, "goal", [])
    policy = host()
    policy.set("SCROLL_DOWN")
    with pytest.raises(StalePage):  # scrolled to the end meanwhile: the control is gone
        policy(page(actions=[a for a in page()["actions"] if a["id"] != "scroll_down"]), "goal", [])
    policy = host()
    policy.set("CLICK", "2")
    assert policy(page(text="a ticking countdown changed"), "goal", [])["choice"] == "e3"  # same element: fine


def test_a_choice_made_on_one_document_is_stale_on_another():
    """snapshot.js numbers the elements of every new document from 1: the same index, label and value on a page that
    loaded meanwhile (a redirect to the checkout) is another element."""
    shown = page(page_key=[1700000000123.5, "https://shop.test/"])
    policy = host(shown)
    policy.set("CLICK", "2")
    with pytest.raises(StalePage, match="Another page"):
        policy(page(url="https://shop.test/checkout", page_key=[1700000004567.25, "https://shop.test/checkout"]),
               "goal", [])
    policy.set("CLICK", "2")  # the same document after a pushState: still the same element
    moved = page(url="https://shop.test/#top", page_key=[1700000000123.5, "https://shop.test/#top"])
    assert policy(moved, "goal", [])["choice"] == "e3"


def test_a_choice_names_its_observation_and_a_repeated_delivery_keeps_the_clock():
    shown = page()
    policy = HostPolicy()
    policy.offer(shown, 1)
    time.sleep(0.02)
    policy.offer(shown, 1)  # the same observation read again (a status poll): the decision clock keeps running
    with pytest.raises(ValueError, match="not the latest"):
        policy.set("CLICK", "2", observation_id=0)
    policy.set("CLICK", "2", observation_id=1)
    assert policy(shown, "goal", [])["latency_ms"] >= 20
    policy.set("CLICK", "2", observation_id=1)
    policy.offer(page(text="after the click"), 2)  # a new observation: the earlier choice is gone
    with pytest.raises(ValueError, match="not chosen"):
        policy(page(text="after the click"), "goal", [])


def test_host_needs_a_delivered_observation():
    with pytest.raises(ValueError, match="No observation"):
        HostPolicy().set("WAIT")
    assert resolve(page(), "select", "3:1")[1:] == ("3:1", page()["actions"][3])


# ---------------------------------------------------------------- Agent injection points

def test_agent_uses_the_given_browser_policy_and_text_policy(monkeypatch):
    monkeypatch.setattr(loop, "Browser", Mock(side_effect=AssertionError("no Browser may be constructed")))
    monkeypatch.setattr(loop, "choose", Mock(side_effect=AssertionError("no TypeSafe call")))
    monkeypatch.setattr(loop, "field_text", Mock(side_effect=AssertionError("no text-model call")))
    p = page()
    browser = Mock(observe=Mock(return_value=p), fresh=Mock(return_value=True))
    policy = host(p)
    policy.set("TYPE_TEXT", "1", "scarpe")
    spy = Mock(wraps=policy)
    text_policy = Mock(wraps=policy.text)
    agent = loop.Agent("https://shop.test/", "Cerca scarpe", browser=browser, policy=spy, text_policy=text_policy)
    agent.command("predict")
    spy.assert_called_once_with(p, "Cerca scarpe", [])
    agent.command("act", {"fingerprint": p["fingerprint"]})
    browser.act.assert_called_once_with(p["actions"][0], p, text="scarpe")
    assert text_policy.call_count == 1 and text_policy.call_args.args[0]["field"]["label"] == "Cerca nel negozio"
    assert agent.state["history"][-1]["text_helper"] == "host"
    assert agent.state["text_calls"][-1]["value"] == "scarpe"


def test_agent_budgets_still_apply_and_a_given_browser_stays_open(monkeypatch):
    p = page()
    browser = Mock(observe=Mock(return_value=p), fresh=Mock(return_value=True))
    agent = loop.Agent("https://shop.test/", "goal", browser=browser, policy=Mock())
    agent.state["decisions"] = [{}] * (MAX_STEPS * 2)
    with pytest.raises(ValueError, match="budget"):
        agent.command("predict")
    agent.policy.assert_not_called()
    failing = Mock(observe=Mock(side_effect=RuntimeError("tab gone")))
    with pytest.raises(RuntimeError):
        loop.Agent("https://shop.test/", "goal", browser=failing)
    failing.close.assert_not_called()  # the caller owns a browser it passed in
    assert loop.Agent.policy is None and loop.choose is choose


# ---------------------------------------------------------------- oracles and start validation

@pytest.mark.parametrize("name, params", [
    ("cart_contains_item_under_price", {}), ("cart_contains_item_under_price", {"max_price": -5}),
    ("cart_contains_item_under_price", {"max_price": "50"}), ("cart_contains_item_under_price", {"max_price": True}),
    ("cart_not_empty", {"max_price": 5}), ("pdp_reached", {"query": ""}), ("unknown_oracle", {}),
    ("search_results_shown", {"query": 5}), ("cart_contains_item_under_price", {"max_price": float("nan")}),
])
def test_oracle_parameters_are_a_closed_set(name, params):
    with pytest.raises(ValueError):
        oracles.check_params(name, params)


def test_oracle_set_and_prices():
    assert set(oracles.ORACLES) == {"cart_contains_item_under_price", "cart_not_empty", "pdp_reached",
                                    "search_results_shown"}
    assert oracles.check_params("cart_contains_item_under_price", {"max_price": 50}) == {"max_price": 50}
    assert [oracles.parse_price(t) for t in ("49,90 €", "€1,299.00", "1.299,00 €", "12", "gratis")] == [
        49.9, 1299.0, 1299.0, 12.0, None]


def test_cart_page_is_found_without_the_agent():
    run = {"site": {"start_url": "https://shop.test/"}, "settings": {"locale": "it"}}
    final = ("https://shop.test/p/1", {"nav": {"cart_link": {"present": True, "href": "/carrello"}}}, {"type": "pdp"})
    assert oracles.find_cart(run, [], final) == ("https://shop.test/carrello", "cart_link")
    steps = [{"url_before": "https://shop.test/cart", "page_type": "cart"}]
    assert oracles.find_cart(run, steps, final) == ("https://shop.test/cart", "visited_cart_page")
    steps = [{"url_before": "https://shop.test/", "url_after": "https://shop.test/carrello/"}]
    assert oracles.find_cart(run, steps, None) == ("https://shop.test/carrello/", "visited_cart_url")
    elsewhere = ("https://shop.test/", {"nav": {"cart_link": {"present": True, "href": "https://pay.example/c"}}},
                 {"type": "home"})
    assert oracles.find_cart(run, [], elsewhere) == (None, None)


def test_a_cart_url_is_never_loaded_with_a_cart_action():
    assert oracles.clean_cart_url("https://shop.test/carrello/?add-to-cart=7&quantity=2#top") == (
        "https://shop.test/carrello/", ["add-to-cart", "quantity"])
    assert oracles.clean_cart_url("https://shop.test/cart?Remove_Item=ab12&_wpnonce=x&ref=menu") == (
        "https://shop.test/cart?ref=menu", ["Remove_Item", "_wpnonce"])
    assert oracles.clean_cart_url("https://shop.test/cart/add?id=5") == (None, [])  # the path itself adds
    assert oracles.clean_cart_url("https://shop.test/cart.html?dark=1") == ("https://shop.test/cart.html?dark=1", [])
    run = {"site": {"start_url": "https://shop.test/"}, "settings": {"locale": "it"}}
    final = ("https://shop.test/p/1", {"nav": {"cart_link": {"present": True, "href": "/carrello"}}}, {"type": "pdp"})
    added = [{"url_before": "https://shop.test/carrello/?add-to-cart=7", "page_type": "cart"}]
    assert oracles.find_cart(run, added, final) == (added[0]["url_before"], "visited_cart_page")  # loaded cleaned
    # a visited cart URL that keeps a query without its cart action comes after the shop's own cart link
    queried = [{"url_before": "https://shop.test/index.php?controller=cart&add=1&id_product=7", "page_type": "cart"}]
    assert oracles.find_cart(run, queried, final) == ("https://shop.test/carrello", "cart_link")
    assert oracles.find_cart(run, queried, None) == (queried[0]["url_before"], "visited_cart_page")
    assert oracles.find_cart(run, [{"url_before": "https://shop.test/p/1?add-to-cart=7"}], None) == (None, None)
    adding = [{"url_before": "https://shop.test/cart/add?id=5", "page_type": "cart"}]
    assert oracles.find_cart(run, adding, None) == (None, None)


def cart_verdict(monkeypatch, line_items, subtotal, max_price=50, *, oracle="cart_contains_item_under_price",
                 total=None, empty=False):
    """The cart oracle on an audit.js cart payload (the cart page found as the final page, read where it is)."""
    record = {"page_id": "desktop-verify-cart", "classification": {"type": "cart"},
              "audit": {"cart": {"line_items": line_items, "subtotal_value": subtotal, "total_value": total,
                                 "empty": empty}}}
    monkeypatch.setattr(oracles, "read_page", lambda browser, collector: ({}, {"type": "cart"}))
    monkeypatch.setattr(oracles, "_load", Mock(side_effect=AssertionError("the final cart page is not loaded again")))
    run = {"site": {"start_url": "https://shop.test/"}, "settings": {"locale": "it"}}
    browser = Mock(evaluate=Mock(return_value="https://shop.test/carrello?add-to-cart=7"))
    collector = Mock(profile="desktop", collect=Mock(return_value=record))
    params = {"max_price": max_price} if oracle == "cart_contains_item_under_price" else {}
    result = oracles.verify(oracle, params, browser=browser, collector=collector, run=run, steps=[])
    collector.collect.assert_called_once_with(browser, stage="extra", page_id="desktop-verify-cart")
    assert result["checks"]["cart_source"] == "final_page" and result["page"] is record
    return result["passed"], result["checks"]


def row(title, price, qty, text=None, addon=False):
    return {"title": title, "qty": qty, "price": f"{price:.2f} €".replace(".", ","), "price_value": price,
            "addon": addon, "row_text": text or f"{title} Taglia 42 {price:.2f} €".replace(".", ",") + " Quantità"}


def test_cart_prices_read_as_unit_prices_or_line_totals_by_the_carts_own_arithmetic(monkeypatch):
    # 60 EUR each, two of them, subtotal 120: the row shows the unit price. Nothing is under 50 EUR.
    passed, checks = cart_verdict(monkeypatch, [row("Scarpa da corsa Vento", 60.0, 2)], 120.0)
    assert not passed and checks["price_reading"] == "unit" and checks["item_list"][0]["price"] == 60.0
    # The fixture cart: line totals (39,90 x 2 = 79,80) plus a sneaked add-on line, subtotal 82,70.
    lines = [row("Brezza", 79.8, 2), row("Protezione spedizione Premium", 2.9, 1, addon=True)]
    passed, checks = cart_verdict(monkeypatch, lines, 82.7)
    assert passed and checks["price_reading"] == "line_total"
    assert checks["item_list"] == [{"title": "Brezza", "qty": 2, "price": 39.9}]
    passed, checks = cart_verdict(monkeypatch, [row("Brezza", 39.9, 1)], 39.9)
    assert passed and checks["price_reading"] == "unit"
    # No subtotal: the row's own amounts (2 x 39,90 = 79,80) settle it.
    passed, checks = cart_verdict(monkeypatch, [row("Brezza", 79.8, 2, text="Brezza 2 x 39,90 € 79,80 €")], None)
    assert passed and checks["price_reading"] == "line_total" and checks["item_list"][0]["price"] == 39.9


def test_an_ambiguous_cart_price_never_passes_on_a_guess(monkeypatch):
    """Only the unit reading (30 EUR each) would pass: not assessable, never a failure charged to the shop."""
    passed, checks = cart_verdict(monkeypatch, [row("Scarpa da corsa Vento", 60.0, 2)], None)
    assert passed is None and checks["not_assessable"] == "cart_price_ambiguous"
    assert checks["price_reading"] == "ambiguous" and checks["matching"] == 0
    assert checks["item_list"] == [{"title": "Scarpa da corsa Vento", "qty": 2, "price": 60.0,
                                    "price_candidates": [30.0, 60.0]}]
    # A subtotal neither reading explains (a paid option ticked in the cart) leaves the row ambiguous too.
    passed, checks = cart_verdict(monkeypatch, [row("Scarpa da corsa Vento", 60.0, 2)], 124.9)
    assert passed is None and checks["price_reading"] == "ambiguous"
    # Neither reading passes: a plain failure.
    passed, checks = cart_verdict(monkeypatch, [row("Scarpa da corsa Vento", 120.0, 2)], None)
    assert passed is False and "not_assessable" not in checks and checks["price_reading"] == "ambiguous"
    # A currency mark that prefixes the next amount does not make the quantity before it an amount.
    assert oracles._row_amounts("Vento Qtà 2 €60,00") == [60.0]
    assert oracles._row_amounts("Vento 2 x 39,90 € 79,80 €") == [39.9, 79.8]


def test_a_cart_the_audit_cannot_read_is_not_the_shops_failure(monkeypatch):
    """Rows without quantity or remove controls give audit.js no line items: a cart that shows a subtotal and is not
    marked empty is not assessable, never empty. An empty cart, add-ons only and prices read over the limit fail."""
    for oracle in ("cart_not_empty", "cart_contains_item_under_price"):
        passed, checks = cart_verdict(monkeypatch, [], 49.8, oracle=oracle, total=54.7)
        assert passed is None and checks["not_assessable"] == "cart_items_unreadable", oracle
        assert checks["subtotal"] == 49.8 and checks["items"] == 0 and "unreadable" not in checks
        passed, checks = cart_verdict(monkeypatch, [], None, oracle=oracle, total=54.7)  # a total only
        assert passed is None and checks["not_assessable"] == "cart_items_unreadable", oracle
        untitled = [{"title": "", "qty": 1, "price": "44,90 €", "price_value": 44.9, "row_text": "1 44,90 €"}]
        assert cart_verdict(monkeypatch, untitled, 44.9, oracle=oracle)[0] is None, oracle
        for line_items, subtotal, empty in (([], 44.9, True), ([], 0.0, False), ([], None, False),
                                            ([row("Protezione spedizione", 2.9, 1, addon=True)], 2.9, False)):
            passed, checks = cart_verdict(monkeypatch, line_items, subtotal, oracle=oracle, empty=empty)
            assert passed is False and "not_assessable" not in checks, (oracle, line_items, subtotal, empty)
    # a product row whose price was not read: no verdict on the price
    unpriced = {"title": "Scarpa da corsa Onda", "qty": 1, "price": None, "price_value": None,
                "row_text": "Scarpa da corsa Onda 1"}
    passed, checks = cart_verdict(monkeypatch, [unpriced], 44.9)
    assert passed is None and checks["not_assessable"] == "cart_price_unreadable"
    assert cart_verdict(monkeypatch, [unpriced], 44.9, oracle="cart_not_empty")[0] is True
    assert cart_verdict(monkeypatch, [unpriced, row("Brezza", 39.9, 1)], 84.8)[0] is True  # another row passes
    passed, checks = cart_verdict(monkeypatch, [row("Vento", 60.0, 1)], 60.0)  # read, and over the limit
    assert passed is False and "not_assessable" not in checks


def test_italian_cart_actions_are_never_loaded():
    assert oracles.clean_cart_url("https://shop.test/carrello/aggiungi?id=5") == (None, [])
    assert oracles.clean_cart_url("https://shop.test/carrello/svuota") == (None, [])
    assert oracles.clean_cart_url("https://shop.test/carrello?aggiungi=3&ref=menu") == (
        "https://shop.test/carrello?ref=menu", ["aggiungi"])
    assert oracles.clean_cart_url("https://shop.test/carrello?Quantit%C3%A0=2&qta=1&azione=rimuovi") == (
        "https://shop.test/carrello", ["Quantità", "azione", "qta"])
    run = {"site": {"start_url": "https://shop.test/"}, "settings": {"locale": "it"}}
    steps = [{"url_before": "https://shop.test/carrello/aggiungi?id=5", "page_type": "cart"},
             {"url_before": "https://shop.test/carrello?aggiungi=3", "page_type": "cart"}]
    assert oracles.find_cart(run, steps, None) == (steps[1]["url_before"], "visited_cart_page")  # loaded cleaned


def test_page_oracles_pass_on_the_shops_own_site_only(monkeypatch):
    run = {"site": {"start_url": "https://shop.test/", "final_url": "https://www.shop.test/"}}
    pages = {"pdp_reached": ({"pdp": {"title": "Scarpa da corsa Brezza", "price": {"value": 39.9}}}, "pdp"),
             "search_results_shown": ({"products": {"cards_count": 24, "cards": [{"title": "Brezza"}]}}, "plp")}
    for name, (audit, kind) in pages.items():
        for url, same in (("https://shop.test/p/3", True), ("https://m.shop.test/p/3", True),
                          ("https://partner.example/p/3", False)):
            read = ({**audit, "url": url}, {"type": kind})
            monkeypatch.setattr(oracles, "read_page", lambda browser, collector, read=read: read)
            result = oracles.verify(name, {"query": "brezza"}, browser=Mock(), collector=Mock(), run=run)
            assert result["passed"] is same and result["checks"]["same_site"] is same, (name, url)


@pytest.mark.parametrize("kwargs", [
    {"oracle": "made_up"}, {"profile": "tablet"}, {"policy": "random"}, {"max_steps": 0}, {"max_steps": 500},
    {"optimal_steps": 0}, {"goal": " "}, {"url": "ftp://shop.test/"},
    {"oracle": "cart_contains_item_under_price", "oracle_params": {}},
])
def test_start_validates_everything_before_opening_a_browser(tmp_path, kwargs):
    factory = Mock(side_effect=AssertionError("no browser for an invalid journey"))
    runner = JourneyRunner(EngagementSettings(url="https://shop.test/"), store=RunStore(tmp_path),
                           transport_factory=factory)
    args = {"url": "https://shop.test/", "goal": "Trova scarpe", "oracle": "cart_not_empty", **kwargs}
    with pytest.raises(ValueError):
        runner.start(args.pop("url"), args.pop("goal"), **args)
    assert RunStore(tmp_path).list_runs() == []


def test_typesafe_needs_its_key_and_is_never_called_in_tests(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    runner = JourneyRunner(EngagementSettings(url="https://shop.test/"), store=RunStore(tmp_path),
                           transport_factory=Mock(side_effect=AssertionError("no browser")))
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
        runner.start("https://shop.test/", "Trova scarpe", oracle="cart_not_empty", policy="typesafe")


# ---------------------------------------------------------------- journeys on the fixture shop (headless Chromium)

PAY = CheckoutGuard("http://127.0.0.1/", lexicon_for("it"))
INDEX = re.compile(r"^\d+(:\d+)?$")
EURO = re.compile(r"^(\d+),(\d\d) €$")


@pytest.fixture
def journey(chromium, shop_server, tmp_path, monkeypatch):
    monkeypatch.setattr(PageCollector, "net_quiet_s", 0.4)  # shorter settles for the start and verification pages
    monkeypatch.setattr(PageCollector, "lcp_quiet_s", 0.4)
    store = RunStore(tmp_path)
    settings = EngagementSettings(url=shop_server.url("shop/index.html"), screenshots=False)
    runners = []

    def make():
        runner = JourneyRunner(settings, store=store, transport_factory=lambda: DirectTransport(chromium.ws_url))
        runners.append(runner)
        return runner

    yield make, store
    for runner in runners:
        runner.close()


def act(runner, operation, target=None, text=None):
    """The host acts on the latest observation it was delivered (its observation_id)."""
    return runner.act(operation, target, text, observation_id=runner.observation()["observation_id"])


def index_of(observation, pattern, operation="CLICK"):
    return next((e["index"] for e in observation["elements"]
                 if re.search(pattern, e["label"], re.I) and operation in e["operations"]), None)


def priced_link(observation, limit):
    """A product link whose price (the next visible text line with a euro amount) is below the limit."""
    lines = [line.strip() for line in observation["text_excerpt"].split("\n")]
    for element in observation["elements"]:
        if element["role"] != "link" or element["label"] not in lines:
            continue
        at = lines.index(element["label"])
        for line in lines[at + 1:at + 4]:
            if (match := EURO.match(line)) and float(f"{match[1]}.{match[2]}") < limit:
                return element["index"]
    return None


def scripted_host(observation):
    """The test's host: consent first (reject), then category -> product under 50 EUR -> add -> cart -> DONE."""
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
    if kind == "cart":
        return "DONE", None
    return "BLOCKED", None


def assert_never_offered_a_pay_control(observations, shop_server):
    labels = [e["label"] for o in observations for e in o["elements"]]
    assert labels and not [label for label in labels if PAY.forbidden(label)]
    assert not [r for r in shop_server.requests if r["method"] != "GET" or r["path"].startswith("/pay")]


def test_host_journey_adds_a_product_under_50_eur_and_the_oracle_confirms_it(journey, shop_server, chromium):
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/index.html"),
                           "Aggiungi al carrello un paio di scarpe da corsa che costi meno di 50 euro",
                           oracle="cart_contains_item_under_price", oracle_params={"max_price": 50},
                           optimal_steps=5, optimal_pages=4)
    run_id, observation = started["run_id"], started["observation"]
    assert started["status"] == "running" and observation["page_type"] == "home"
    assert observation["observation_id"] == 1 and runner.observation()["observation_id"] == 1  # a read: the same one
    assert observation["controls"][-2:] == ["DONE", "BLOCKED"] and observation["budget_left"] == 40
    seen = [observation]

    with pytest.raises(ValueError, match="not an offered element index"):
        act(runner, "CLICK", "#add")  # a selector is refused before anything runs
    with pytest.raises(ValueError, match="not offered"):
        act(runner, "SCROLL_UP")

    # The page changes between observation and decision: index 1 now names another element. Nothing runs.
    consent = index_of(observation, r"^Rifiuta tutti$")
    runner.tab.evaluate("document.body.prepend(Object.assign(document.createElement('a'), "
                        "{href: '#novita', textContent: 'Novità'})), true")
    stale = act(runner, "CLICK", consent)
    assert stale["stale"] and not stale["executed"] and stale["observation"]["step"] == 0
    observation = stale["observation"]
    assert index_of(observation, r"^Rifiuta tutti$") != consent

    for _ in range(20):
        operation, target = scripted_host(observation)
        result = act(runner, operation, target)
        observation = result["observation"]
        seen.append(observation)
        if operation != "DONE":
            assert result["executed"] and result["step_metrics"]["settle_ms"] is not None, result
        if result["status"] != "running":
            break
    assert result["status"] == "done" and observation["page_type"] == "cart"

    finished = runner.finish()
    assert finished["status"] == "done" and finished["verification"]["passed"] is True
    checks = finished["verification"]["checks"]
    assert checks["oracle"] == "cart_contains_item_under_price" and checks["matching"] >= 1
    assert checks["cart_source"] == "final_page" and all(i["price"] < 50 for i in checks["item_list"])
    assert finished["friction"]["FAI.JOURNEY_SUCCESS"] is True and finished["friction"]["FAI.DEAD_CLICK_RATE"] == 0
    assert runner.transport is None and runner.tab is None  # context, transport and tab released

    steps = store.read_steps(run_id)
    assert finished["steps_path"].endswith("steps.jsonl") and len(steps) == finished["steps"] + 2  # + stale, DONE
    assert steps[0]["flags"]["stale"] and steps[0]["operation"] == "CLICK" and steps[0]["label"] == "Rifiuta tutti"
    assert steps[-1]["operation"] == "DONE"
    executed = steps[1:-1]
    assert [s["step"] for s in steps] == list(range(1, len(steps) + 1))
    assert {s["operation"] for s in executed} <= {"CLICK", "SCROLL_DOWN"}
    for s in executed:
        assert s["target"] is None or INDEX.match(s["target"]), s["target"]
        assert s["settle_ms"] is not None and s["execution_ms"] is not None and s["since"]
        assert s["policy"] == "host" and s["decision_latency_ms"] is not None
    labels = [s["label"] for s in executed if s["operation"] == "CLICK"]
    assert labels[0] == "Rifiuta tutti" and labels[1] == "Scarpe da corsa" and labels[-2:] == [
        "Aggiungi al carrello", "Vai al carrello"]
    add = executed[-2]
    assert add["url_before"] == add["url_after"] and not add["flags"]["dead_click"] and add["since"]["mutations"] >= 1
    assert executed[1]["flags"]["new_document"] and executed[1]["page_changed"]

    run = store.load(run_id)
    assert run["kind"] == "journey" and run["status"] == "complete" and run["journey"]["status"] == "done"
    assert run["journey"]["verification"]["passed"] is True and run["finished_at"]
    assert [o["kpi_id"] for o in run["observations"]] == [k.id for k in by_producer("friction")]
    time_on_task = next(o for o in run["observations"] if o["kpi_id"] == "FAI.TIME_ON_TASK_SITE")
    assert time_on_task["value"] == round(sum(s["execution_ms"] + s["settle_ms"] for s in executed))
    assert {p["page_id"] for p in run["pages"]} == {"mobile-journey-start", "mobile-verify-cart"}
    assert run["settings"]["device_profile"]["name"] == "mobile"
    assert_never_offered_a_pay_control(seen, shop_server)

    probe = DirectTransport(chromium.ws_url)
    try:  # nothing of the journey's isolated context is left in the browser
        pages = [t for t in probe.call("Target.getTargets")["targetInfos"] if t["type"] == "page"]
        assert not [t for t in pages if "/shop/" in t.get("url", "")]
    finally:
        probe.close()
    json.dumps(run)


def test_journey_stops_at_the_checkout_boundary_and_the_oracle_ignores_done(journey, shop_server):
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/product.html?id=3"), "Metti nel carrello la Brezza",
                           oracle="cart_not_empty", profile="desktop")
    observation, seen = started["observation"], [started["observation"]]
    for pattern in (r"^Rifiuta tutti$", r"^Aggiungi al carrello$", r"^Vai al carrello$", r"Procedi al checkout"):
        result = act(runner, "CLICK", index_of(observation, pattern))
        observation = result["observation"]
        seen.append(observation)
        assert result["executed"]
    assert result["status"] == "stopped_at_checkout_boundary" and observation["page_type"] == "checkout"
    assert observation["controls"] == [] and observation["guard_notes"]
    assert not [e for e in observation["elements"] if "TYPE_TEXT" in e["operations"] or "SELECT" in e["operations"]]
    assert {e["role"] for e in observation["elements"]} <= {"link"}  # only ways out of the checkout remain
    with pytest.raises(ValueError, match="stopped"):
        act(runner, "CLICK", "1")
    finished = runner.finish()
    # The agent never chose DONE; the cart is checked on its own page all the same.
    assert finished["status"] == "stopped_at_checkout_boundary" and finished["verification"]["passed"] is True
    assert finished["verification"]["checks"]["cart_source"] == "visited_cart_page"
    assert_never_offered_a_pay_control(seen, shop_server)
    assert store.load(started["run_id"])["journey"]["status"] == "stopped_at_checkout_boundary"


def test_a_done_choice_is_not_success_when_the_oracle_disagrees(journey, shop_server):
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/category.html"), "Aggiungi qualcosa al carrello",
                           oracle="cart_not_empty", profile="desktop", optimal_steps=3)
    result = act(runner, "DONE")
    assert result["status"] == "done" and not result["executed"]
    finished = runner.finish()
    assert finished["status"] == "done" and finished["verification"]["passed"] is False
    assert finished["friction"]["FAI.JOURNEY_SUCCESS"] is False
    rows = {o["kpi_id"]: o for o in store.load(started["run_id"])["observations"]}
    assert rows["FAI.ACTIONS_RATIO"]["reason"].startswith("not_applicable")
    assert store.read_steps(started["run_id"])[-1]["operation"] == "DONE"


def test_another_site_ends_the_journey_and_opened_tabs_are_closed(journey, shop_server):
    make, store = journey
    runner = make()
    # The query stands for a GET with a side effect: the runner never requests a page by itself, so it runs once.
    started = runner.start(shop_server.url("shop/resi.html?add-to-cart=7"), "Leggi la politica di resi",
                           oracle="pdp_reached", profile="desktop")
    elsewhere = shop_server.url("shop/index.html").replace("127.0.0.1", "localhost")  # another registrable domain
    runner.tab.evaluate(f"""(() => {{ const add = (text, attrs) => document.body.prepend(Object.assign(
        document.createElement('a'), {{textContent: text, ...attrs}}));
      add('Esterno', {{href: {json.dumps(elsewhere)}}});
      add('Nuova scheda', {{href: 'resi.html?tab=1', target: '_blank'}}); return true; }})()""")
    observation = act(runner, "WAIT")["observation"]  # look again: the new links are now offered

    tab = act(runner, "CLICK", index_of(observation, r"^Nuova scheda$"))
    assert tab["executed"] and tab["step_metrics"]["flags"].get("new_tab")
    assert "dead_click" not in tab["step_metrics"]["flags"]
    contexts = runner.transport.call("Target.getTargets")["targetInfos"]
    assert [t["targetId"] for t in contexts if t.get("browserContextId") == runner.context_id
            and t["type"] == "page"] == [runner.tab.target]

    away = act(runner, "CLICK", index_of(tab["observation"], r"^Esterno$"))
    assert away["executed"] and away["step_metrics"]["flags"]["external_nav"] and away["status"] == "blocked"
    assert away["observation"]["url"] == elsewhere and away["observation"]["controls"] == []  # where the link led
    assert away["observation"]["guard_notes"][0].startswith(f"left the shop for {elsewhere}")
    with pytest.raises(ValueError, match="stopped"):
        act(runner, "WAIT")

    finished = runner.finish()
    assert finished["status"] == "blocked" and finished["verification"]["passed"] is False  # not a product page
    friction = finished["friction"]
    assert friction["FAI.UNEXPECTED_NAV"] == 1 and friction["FAI.DEAD_CLICK_RATE"] == 0  # the tab; the link was chosen
    assert friction["FAI.BACKTRACK_RATE"] == 0  # the external page is one visit; nothing was revisited
    rows = {o["kpi_id"]: o for o in store.load(started["run_id"])["observations"]}
    assert rows["FAI.UNEXPECTED_NAV"]["evidence"] == {"new_tabs": 1, "spontaneous": 0, "between_steps": 0,
                                                      "chosen_external": 1}
    steps = store.read_steps(started["run_id"])
    assert [s["operation"] for s in steps] == ["WAIT", "CLICK", "CLICK"]
    assert steps[0]["execution_ms"] == 0.0  # the harness's WAIT pause is not site time
    assert steps[2]["url_after"] == elsewhere and steps[2]["flags"]["external_nav"]
    assert steps[1]["flags"]["unexpected_nav"] and not steps[2]["flags"]["unexpected_nav"]
    assert len([r for r in shop_server.requests if "add-to-cart=7" in r["path"]]) == 1
    assert any(w.startswith("left_shop: http://localhost:") for w in store.load(started["run_id"])["warnings"])


def test_a_checkout_on_another_site_is_still_the_checkout_boundary(journey, shop_server):
    make, store = journey
    runner = make()
    runner.start(shop_server.url("shop/resi.html"), "Vai alla cassa", oracle="cart_not_empty", profile="desktop")
    elsewhere = shop_server.url("shop/checkout.html").replace("127.0.0.1", "localhost")
    runner.tab.evaluate(f"document.body.prepend(Object.assign(document.createElement('a'), "
                        f"{{href: {json.dumps(elsewhere)}, textContent: 'Cassa del partner'}})), true")
    observation = act(runner, "WAIT")["observation"]
    result = act(runner, "CLICK", index_of(observation, r"^Cassa del partner$"))
    assert result["executed"] and result["status"] == "stopped_at_checkout_boundary"
    assert result["observation"]["url"] == elsewhere and result["observation"]["controls"] == []
    assert runner.finish()["status"] == "stopped_at_checkout_boundary"
    assert not [r for r in shop_server.requests if r["method"] != "GET" or r["path"].startswith("/pay")]


REDIRECT_TO_CHECKOUT = "setTimeout(() => { location.href = 'checkout.html'; }, 1200), true"


def test_a_choice_never_runs_on_a_checkout_page_that_loaded_meanwhile(journey, shop_server):
    """The page moves itself to the checkout while the host decides: the choice is not carried over to it, and the run
    stops at the boundary without any input there."""
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Torna alla home", oracle="cart_not_empty",
                           profile="desktop")
    target = index_of(started["observation"], r"^Passo Svelto$")
    runner.tab.evaluate(REDIRECT_TO_CHECKOUT)
    time.sleep(3.5)  # the host deliberates; the checkout loads meanwhile (the redirect fires after 1.2 s)
    result = act(runner, "CLICK", target)
    assert not result["executed"] and result["status"] == "stopped_at_checkout_boundary"
    assert result["observation"]["page_type"] == "checkout" and runner.tab.evaluate("location.pathname").endswith(
        "/checkout.html")
    steps = store.read_steps(started["run_id"])  # the Agent saw the checkout before asking the host's choice
    moved, attempt = steps  # the redirect the page made by itself, then the host's choice that never ran
    assert moved["operation"] == "NAVIGATION" and moved["flags"]["new_document"] and moved["flags"]["unexpected_nav"]
    assert moved["url_before"].endswith("/resi.html") and moved["url_after"].endswith("/checkout.html")
    assert moved["page_type"] == "checkout" and not step_executed(moved)
    assert attempt["flags"]["stale"] and not step_executed(attempt)
    assert attempt["guard_notes"] == ["checkout_boundary: the page changed since the observation; nothing ran"]
    finished = runner.finish()
    assert finished["friction"]["FAI.UNEXPECTED_NAV"] == 1 and finished["steps"] == 0


def test_typesafe_is_never_asked_on_a_checkout_that_loaded_meanwhile(journey, shop_server, monkeypatch):
    """The Agent observes again right before deciding and finds a checkout: the run stops at the boundary before any
    model request (no TypeSafe call carries the checkout page), and nothing is clicked there."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "unused-in-tests")
    calls = []
    monkeypatch.setattr(loop, "choose", stand_in(r"^Torna al carrello$", calls))
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Torna al carrello", oracle="cart_not_empty",
                           policy="typesafe", profile="desktop")
    runner.tab.evaluate(REDIRECT_TO_CHECKOUT)
    time.sleep(3.5)
    result = runner.run_auto()
    assert result["status"] == "stopped_at_checkout_boundary" and result["steps"] == 0 and calls == []
    assert runner.tab.evaluate("location.pathname").endswith("/checkout.html")  # the link was not followed
    # no decision was made: the only record is the page's own redirect
    assert [s["operation"] for s in store.read_steps(started["run_id"])] == ["NAVIGATION"]


def test_an_anti_bot_page_makes_the_journey_not_assessable(journey, shop_server):
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/challenge.html"), "Trova scarpe", oracle="cart_not_empty",
                           profile="desktop")
    assert started["status"] == "blocked" and started["observation"]["page_type"] == "challenge"
    finished = runner.finish()
    assert finished["verification"] == {"passed": None, "checks": {"oracle": "cart_not_empty",
                                                                   "not_assessable": "bot_challenge"}}
    run = store.load(started["run_id"])
    success = next(o for o in run["observations"] if o["kpi_id"] == "FAI.JOURNEY_SUCCESS")
    assert not success["assessed"] and success["reason"] == "bot_challenge"
    assert run["not_assessable"][-1]["reason"] == "bot_challenge" and run["status"] == "partial"
    assert any(w.startswith("bot_challenge") for w in run["warnings"])


def test_run_auto_drives_the_typesafe_loop_with_a_local_stand_in(journey, shop_server, monkeypatch):
    """No TypeSafe request: model.choose is replaced by a local function with the same contract."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "unused-in-tests")
    calls = []

    def fake_choose(state, goal, history):
        calls.append(state["url"])
        elements, targets, _ = loop.action_space(state["actions"])
        consent = next((i for i, a in targets.get("CLICK", {}).items() if a["label"] == "Rifiuta tutti"), None)
        operation, target = ("CLICK", consent) if consent else ("DONE", None)
        choice = targets["CLICK"][target]["id"] if consent else "DONE"
        return {"choice": choice, "operation": operation, "target": target, "confidence": 0.9,
                "probabilities": {choice: 0.9}, "latency_ms": 250, "usage": {}, "model": "stand-in"}

    monkeypatch.setattr(loop, "choose", fake_choose)
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Rifiuta i cookie", oracle="search_results_shown",
                           policy="typesafe", profile="desktop")
    with pytest.raises(ValueError, match="host policy"):
        act(runner, "WAIT")
    result = runner.run_auto()
    assert result["status"] == "done" and result["steps"] == 1 and len(calls) == 2
    steps = store.read_steps(started["run_id"])
    assert [(s["operation"], s["policy"]) for s in steps] == [("CLICK", "typesafe"), ("DONE", "typesafe")]
    assert steps[0]["decision_latency_ms"] == 250 and steps[0]["label"] == "Rifiuta tutti"
    assert runner.finish()["verification"]["passed"] is False  # DONE on a policy page is not a search result


# ---------------------------------------------------------------- the runner's safety paths (fixture shop)

COUNTER = """(() => { const box = document.createElement('article'), button = document.createElement('button');
  button.textContent = 'Conta'; button.onclick = () => { window.__clicks = (window.__clicks || 0) + 1; };
  box.append(button); document.body.prepend(box); return true; })()"""


def stand_in(pattern, calls, operation="CLICK"):
    """model.choose's contract, decided locally: the first offered target whose label matches."""
    def choose(state, goal, history):
        calls.append(state["url"])
        _, targets, _ = loop.action_space(state["actions"])
        target = next(i for i, a in targets[operation].items() if re.search(pattern, a["label"]))
        choice = targets[operation][target]["id"]
        return {"choice": choice, "operation": operation, "target": target, "confidence": 0.9,
                "probabilities": {choice: 0.9}, "latency_ms": 10, "usage": {}, "model": "stand-in"}
    return choose


def counter_page(make, shop_server, **kwargs):
    """A journey on the policy page with a click counter button (one WAIT step to see it)."""
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Premi Conta", oracle="cart_not_empty",
                           profile="desktop", **kwargs)
    runner.tab.evaluate(COUNTER)
    if kwargs.get("policy") == "typesafe":
        return runner, started["run_id"], None
    observation = act(runner, "WAIT")["observation"]
    return runner, started["run_id"], observation


def clicks(runner):
    return runner.tab.evaluate("window.__clicks || 0")


def test_execution_is_logged_before_its_result_is_observed(journey, shop_server):
    make, store = journey
    runner, run_id, observation = counter_page(make, shop_server)
    events, tab = [], runner.tab
    append, observe, tab_act = store.append_step, tab.observe, tab.act
    store.append_step = lambda rid, step: (events.append(("log", step["operation"])), append(rid, step))[1]
    tab.observe = lambda **kw: (events.append(("observe",)), observe(**kw))[1]
    tab.act = lambda *a, **kw: (events.append(("act",)), tab_act(*a, **kw))[1]
    try:
        assert act(runner, "CLICK", index_of(observation, r"^Conta$"))["executed"]
    finally:
        del store.append_step, tab.observe, tab.act
    assert events == [("act",), ("log", "CLICK"), ("observe",)]


@pytest.mark.parametrize("error", [RuntimeError("connection lost after the input"), TimeoutError("no reply")])
def test_an_input_that_may_have_happened_is_logged_as_uncertain_and_never_sent_again(journey, shop_server, error):
    make, store = journey
    runner, run_id, observation = counter_page(make, shop_server)
    tab_act = runner.tab.act

    def act_then_fail(*args, **kwargs):
        tab_act(*args, **kwargs)
        raise error

    runner.tab.act = act_then_fail
    result = act(runner, "CLICK", index_of(observation, r"^Conta$"))
    del runner.tab.act
    assert result["executed"] and not result["stale"] and result["status"] == "running" and clicks(runner) == 1
    steps = store.read_steps(run_id)
    assert [s["operation"] for s in steps] == ["WAIT", "CLICK"] and steps[1]["flags"]["uncertain"]
    assert steps[1]["guard_notes"][0].startswith("execution not confirmed")
    history = runner.agent.state["history"]
    assert history[-1]["uncertain"] and history[-1]["action"] == "Conta"  # the policy sees the possible input
    after = act(runner, "WAIT")
    assert after["executed"] and clicks(runner) == 1 and after["observation"]["step"] == 3
    finished = runner.finish()
    assert finished["status"] == "blocked"
    assert "finished by the host without DONE/BLOCKED" in store.load(run_id)["warnings"]


def test_a_stale_decision_executes_nothing_and_consumes_no_step(journey, shop_server):
    make, store = journey
    runner, run_id, observation = counter_page(make, shop_server)
    runner.tab.act = Mock(side_effect=StalePage("moved"))
    result = act(runner, "CLICK", index_of(observation, r"^Conta$"))
    del runner.tab.act
    assert result["stale"] and not result["executed"] and result["observation"]["step"] == 1 and clicks(runner) == 0
    assert store.read_steps(run_id)[-1]["flags"]["stale"]
    with pytest.raises(ValueError, match="not chosen"):
        runner.agent.command("predict")  # the consumed choice cannot run again


def test_the_guard_inspects_a_field_again_right_before_typing(journey, shop_server):
    make, store = journey
    runner, run_id, observation = counter_page(make, shop_server)
    field = index_of(observation, r"Cerca", "TYPE_TEXT")
    runner.tab.evaluate("document.getElementById('q').setAttribute('autocomplete', 'email'), true")
    result = act(runner, "TYPE_TEXT", field, "scarpe")
    assert not result["executed"] and result["refused"] == "personal_field:email" and result["status"] == "running"
    assert runner.tab.evaluate("document.getElementById('q').value") == ""
    assert store.read_steps(run_id)[-1]["flags"]["guard_blocked"]


def test_the_step_budget_stops_the_journey(journey, shop_server):
    make, store = journey
    runner, run_id, observation = counter_page(make, shop_server, max_steps=2)
    result = act(runner, "CLICK", index_of(observation, r"^Conta$"))
    assert result["executed"] and result["status"] == "budget_exhausted" and result["observation"]["controls"] == []
    with pytest.raises(ValueError, match="stopped"):
        act(runner, "WAIT")
    assert clicks(runner) == 1 and store.read_steps(run_id)[-1]["status"] == "budget_exhausted"


def test_run_auto_gives_up_on_a_page_that_never_holds_still(journey, shop_server, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "unused-in-tests")
    calls = []
    monkeypatch.setattr(loop, "choose", stand_in(r"^Conta$", calls))
    make, store = journey
    runner, run_id, _ = counter_page(make, shop_server, policy="typesafe")
    runner.tab.act = Mock(side_effect=StalePage("moved"))
    result = runner.run_auto()
    assert result["status"] == "blocked" and result["steps"] == 0 and len(calls) == MAX_STALE
    assert all(s["flags"]["stale"] for s in store.read_steps(run_id)) and clicks(runner) == 0


@pytest.mark.parametrize("failure", ["malformed", "credential"])
def test_an_infrastructure_failure_is_not_scored_as_the_sites(journey, shop_server, monkeypatch, failure):
    """A model answer the runner cannot read, or TYPE_TEXT without the text model's key, stops the journey with an
    error: the journey's success is not assessable (never false), and nothing escapes run_auto."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "unused-in-tests")
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    if failure == "malformed":
        monkeypatch.setattr(loop, "choose", Mock(side_effect=KeyError("answers")))
    else:
        monkeypatch.setattr(loop, "choose", stand_in(r"Cerca", [], operation="TYPE_TEXT"))
    make, store = journey
    runner, run_id, _ = counter_page(make, shop_server, policy="typesafe")
    result = runner.run_auto()
    assert result["status"] == "error" and result["steps"] == 0
    finished = runner.finish()
    assert finished["verification"]["passed"] is None
    assert finished["verification"]["checks"]["not_assessable"] == "journey_error"
    assert finished["verification"]["checks"]["oracle_passed"] is False  # the oracle's reading stays as evidence
    run = store.load(run_id)
    success = next(o for o in run["observations"] if o["kpi_id"] == "FAI.JOURNEY_SUCCESS")
    assert not success["assessed"] and success["reason"] == "journey_error" and run["status"] == "partial"
    assert runner.transport is None


TICKER = """(() => { const p = document.createElement('p'); let n = 0;
  setInterval(() => { p.textContent = 'Offerta: ' + (++n); }, 250); document.body.prepend(p);
  const box = document.createElement('article'), button = document.createElement('button');
  button.textContent = 'Non fa nulla'; box.append(button); document.body.prepend(box); return true; })()"""


def test_dead_clicks_and_rage_are_found_on_a_page_with_a_ticking_text(journey, shop_server):
    """A countdown changes the visible text all the time; a button that does nothing is still a dead click."""
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Premi il pulsante", oracle="cart_not_empty",
                           profile="desktop")
    runner.tab.evaluate(TICKER)
    observation = act(runner, "WAIT")["observation"]
    time.sleep(4.0)  # vitals.js recognises the ticking node: three spontaneous changes, 2 s after the last action
    target = index_of(observation, r"^Non fa nulla$")
    for _ in range(3):
        result = act(runner, "CLICK", target)
        assert result["executed"] and result["page_changed"], result  # the visible text did change
    steps = store.read_steps(started["run_id"])[1:]
    assert [s["flags"]["dead_click"] for s in steps] == [True, True, True]
    assert [s["flags"]["rage"] for s in steps] == [False, False, True]
    assert all(s["flags"]["state_changed"] is False and s["since"]["mutations"] == 0 for s in steps)
    finished = runner.finish(status="done")
    assert finished["friction"]["FAI.DEAD_CLICK_RATE"] == 100.0 and finished["friction"]["FAI.RAGE_EVENTS"] == 1


def show_cart(runner, lines):
    """The test stands in for an earlier tab: it fills the fixture cart (localStorage) and reloads the journey tab."""
    runner.tab.evaluate(f"localStorage.setItem('ps-cart', JSON.stringify({json.dumps(lines)})), location.reload(), "
                        "true")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if lines[0]["name"] in (runner.tab.evaluate("document.querySelector('[data-cart]')?.innerText || ''") or ""):
            return
        time.sleep(0.1)
    raise AssertionError("the cart did not render")


BREZZA = [{"id": 3, "name": "Brezza", "price": 39.9, "size": "42", "qty": 2}]


def test_the_cart_oracle_reads_line_totals_on_the_fixture_cart(journey, shop_server):
    make, store = journey
    runner = make()
    runner.start(shop_server.url("shop/cart.html?dark=1"), "Due paia di Brezza nel carrello",
                 oracle="cart_contains_item_under_price", oracle_params={"max_price": 40}, profile="desktop")
    show_cart(runner, BREZZA)
    finished = runner.finish(status="done")
    checks = finished["verification"]["checks"]
    assert finished["verification"]["passed"] is True and checks["price_reading"] == "line_total"
    assert checks["item_list"] == [{"title": "Brezza", "qty": 2, "price": 39.9}]  # 79,80 EUR on the row
    assert checks["cart_source"] == "final_page"


def requests_for(shop_server, since, fragment):
    return [r["path"] for r in shop_server.requests[since:] if fragment in r["path"]]


def test_the_cart_oracle_never_sends_a_cart_action_url_again(journey, shop_server):
    """A GET that adds to the cart (WooCommerce ?add-to-cart=7) is sent once, by the journey: the final cart page is
    read where it is, and a visited one is loaded without its cart-action keys."""
    make, store = journey
    since = len(shop_server.requests)
    runner = make()
    runner.start(shop_server.url("shop/cart.html?add-to-cart=7"), "Metti la Brezza nel carrello",
                 oracle="cart_not_empty", profile="desktop")
    show_cart(runner, BREZZA)  # the reload is the test's: the journey sent the URL once
    finished = runner.finish(status="done")
    assert finished["verification"]["passed"] is True
    assert finished["verification"]["checks"]["cart_source"] == "final_page"
    assert len(requests_for(shop_server, since, "add-to-cart=7")) == 2  # the journey's load and the test's reload

    since = len(shop_server.requests)
    runner = make()
    started = runner.start(shop_server.url("shop/cart.html?add-to-cart=7&quantity=2"), "Metti la Brezza nel carrello",
                           oracle="cart_not_empty", profile="desktop")
    show_cart(runner, BREZZA)
    observation = act(runner, "WAIT")["observation"]
    assert act(runner, "CLICK", index_of(observation, r"^Passo Svelto$"))["executed"]  # away from the cart
    finished = runner.finish(status="done")
    checks = finished["verification"]["checks"]
    assert finished["verification"]["passed"] is True and checks["cart_source"] == "visited_cart_page"
    assert checks["dropped_query_keys"] == ["add-to-cart", "quantity"]
    assert checks["cart_url"] == shop_server.url("shop/cart.html")
    assert len(requests_for(shop_server, since, "add-to-cart=7")) == 2  # the journey's load and the test's reload
    assert requests_for(shop_server, since, "/shop/cart.html") == [
        "/shop/cart.html?add-to-cart=7&quantity=2", "/shop/cart.html?add-to-cart=7&quantity=2", "/shop/cart.html"]
    assert {p["page_id"] for p in store.load(started["run_id"])["pages"]} == {"desktop-journey-start",
                                                                               "desktop-verify-cart"}


def test_the_same_act_sent_twice_executes_once(journey, shop_server):
    """A retried or parallel tool call names an observation that is no longer the latest: nothing runs."""
    make, store = journey
    runner, run_id, observation = counter_page(make, shop_server)
    target, shown = index_of(observation, r"^Conta$"), observation["observation_id"]
    first = runner.act("CLICK", target, observation_id=shown)
    again = runner.act("CLICK", target, observation_id=shown)
    assert first["executed"] and not again["executed"] and again["stale"] and clicks(runner) == 1
    assert again["observation"]["observation_id"] == first["observation"]["observation_id"] == shown + 1
    assert [s["operation"] for s in store.read_steps(run_id)] == ["WAIT", "CLICK"]
    with pytest.raises(ValueError, match="observation_id"):
        runner.act("WAIT", observation_id="2")
    latest, results = again["observation"]["observation_id"], []
    calls = [threading.Thread(target=lambda: results.append(runner.act("CLICK", target, observation_id=latest)))
             for _ in range(2)]
    for call in calls:
        call.start()
    for call in calls:
        call.join()
    assert sorted(r["executed"] for r in results) == [False, True] and clicks(runner) == 2


def test_a_host_ends_a_journey_as_done_or_blocked_only(journey, shop_server):
    make, store = journey
    runner = make()
    runner.start(shop_server.url("shop/resi.html"), "Leggi i resi", oracle="cart_not_empty", profile="desktop")
    for status in ("error", "stopped_at_checkout_boundary", "budget_exhausted", "running"):
        with pytest.raises(ValueError, match="status must be one of"):
            runner.finish(status=status)
    assert runner.status == "running" and runner.tab is not None and HOST_FINISH == ("done", "blocked")
    finished = runner.finish(status="done")
    assert finished["status"] == "done" and finished["verification"]["passed"] is False


@pytest.mark.parametrize("path, oracle, kind", [("challenge.html", "cart_not_empty", "challenge"),
                                                ("product.html?id=3", "pdp_reached", "pdp")])
def test_another_sites_page_is_never_the_shops(journey, shop_server, path, oracle, kind):
    """Another site's anti-bot page leaves the shop (not the shop's challenge), and its product page is not the
    shop's product page."""
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Apri la Brezza", oracle=oracle, profile="desktop")
    elsewhere = shop_server.url(f"shop/{path}").replace("127.0.0.1", "localhost")
    runner.tab.evaluate(f"document.body.prepend(Object.assign(document.createElement('a'), "
                        f"{{href: {json.dumps(elsewhere)}, textContent: 'Partner'}})), true")
    observation = act(runner, "WAIT")["observation"]
    result = act(runner, "CLICK", index_of(observation, r"^Partner$"))
    assert result["executed"] and result["status"] == "blocked" and result["observation"]["page_type"] == kind
    finished = runner.finish()
    checks = finished["verification"]["checks"]
    assert finished["verification"]["passed"] is False and "not_assessable" not in checks
    if oracle == "pdp_reached":
        assert checks["page_type"] == "pdp" and checks["same_site"] is False
    run = store.load(started["run_id"])
    assert any(w.startswith("left_shop: http://localhost:") for w in run["warnings"])
    assert not [w for w in run["warnings"] if w.startswith("bot_challenge")] and not run.get("not_assessable")
    success = next(o for o in run["observations"] if o["kpi_id"] == "FAI.JOURNEY_SUCCESS")
    assert success["assessed"] and success["value"] is False


def test_an_error_after_the_input_still_reports_the_executed_step(journey, shop_server):
    """A plain ValueError while the page after a click is read: the click was logged, so act() says it executed, and
    the journey stops with an error (its own state is not trusted) without sending anything again."""
    make, store = journey
    runner, run_id, observation = counter_page(make, shop_server)
    filter_actions, failures = runner.guard.filter_actions, []

    def fail_once(*args, **kwargs):
        if not failures:
            failures.append(1)
            raise ValueError("unreadable page")
        return filter_actions(*args, **kwargs)

    runner.guard.filter_actions = fail_once
    result = act(runner, "CLICK", index_of(observation, r"^Conta$"))
    assert result["executed"] and result["status"] == "error" and clicks(runner) == 1
    steps = store.read_steps(run_id)
    assert [s["operation"] for s in steps] == ["WAIT", "CLICK"] and step_executed(steps[1])
    assert result["step_metrics"]["settle_ms"] is not None


def test_an_outcome_the_oracle_cannot_read_is_not_the_shops_failure(journey, shop_server, monkeypatch):
    verdict = {"passed": None, "checks": {"oracle": "cart_contains_item_under_price",
                                          "not_assessable": "cart_price_ambiguous"}}
    monkeypatch.setattr(oracles, "verify", lambda *args, **kwargs: json.loads(json.dumps(verdict)))
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Due paia sotto i 50 euro",
                           oracle="cart_contains_item_under_price", oracle_params={"max_price": 50}, profile="desktop")
    finished = runner.finish(status="done")
    assert finished["verification"] == verdict and finished["friction"]["FAI.JOURNEY_SUCCESS"] is None
    run = store.load(started["run_id"])
    rows = {o["kpi_id"]: o for o in run["observations"]}
    assert rows["FAI.JOURNEY_SUCCESS"]["reason"] == "cart_price_ambiguous" and run["status"] == "partial"
    assert rows["FAI.ACTIONS_TO_GOAL"]["reason"] == "cart_price_ambiguous"


# ---------------------------------------------------------------- fail closed, feedback, navigation, no progress

STEP_ONE = """(() => { const form = Object.assign(document.createElement('form'), {method: 'post', action: '/pay2'});
  form.innerHTML = '<label>Indirizzo <input name="indirizzo"></label><label>Codice sconto <input name="coupon"></label>'
    + '<button type="submit">Continua</button>';
  document.body.prepend(form); return true; })()"""


def test_a_page_the_audit_cannot_read_is_handled_as_a_checkout_page(journey, shop_server, monkeypatch):
    """A checkout step outside the URL lexicon and without payment fields (an address form with a 'Continua' submit)
    is recognised only by the audit: while it fails, the page is handled as a checkout page, so its submit is never
    offered nor executed and nothing is typed; a later observation reads the page again."""
    make, store = journey
    runner, run_id, observation = counter_page(make, shop_server)
    runner.tab.evaluate(STEP_ONE)
    observation = act(runner, "WAIT")["observation"]
    assert index_of(observation, r"^Continua$") and index_of(observation, r"Codice sconto", "TYPE_TEXT")
    real, failing = PageCollector.audit, {"on": True}

    def audit(collector, browser):
        if failing["on"]:
            raise AuditError("audit.js timed out")
        return real(collector, browser)

    monkeypatch.setattr(PageCollector, "audit", audit)
    since = len(shop_server.requests)
    result = act(runner, "CLICK", index_of(observation, r"^Conta$"))  # a click: the page is audited again, and fails
    observation = result["observation"]
    assert result["executed"] and result["status"] == "running" and observation["page_type"] is None
    assert observation["guard_notes"][0] == UNREAD
    assert index_of(observation, r"^Continua$") is None and index_of(observation, r"^Conta$") is None
    assert not [e for e in observation["elements"] if {"TYPE_TEXT", "SELECT"} & set(e["operations"])]
    assert index_of(observation, r"^Scarpe da corsa$") and "WAIT" in observation["controls"]  # links away remain
    raw = runner.tab.observe()  # the executor refuses the submit too, whatever a policy chose
    submit = next(a for a in raw["actions"] if a["label"] == "Continua")
    with pytest.raises(GuardRefused, match="checkout_submit"):
        runner._act(submit, raw, None)
    assert act(runner, "WAIT")["observation"]["page_type"] is None  # every observation tries again
    failing["on"] = False
    observation = act(runner, "WAIT")["observation"]
    assert observation["page_type"] == "other" and index_of(observation, r"^Continua$")
    assert UNREAD not in observation["guard_notes"]
    assert not [r for r in shop_server.requests[since:] if r["method"] != "GET"] and clicks(runner) == 1


BEACON_THEN_TOAST = """(() => { const b = document.createElement('button'); b.textContent = 'Traccia e avvisa';
  b.onclick = () => { navigator.sendBeacon('/api/beacon', 'x');
    setTimeout(() => { const t = document.createElement('p'); t.textContent = 'Fatto: avviso'; document.body.prepend(t);
    }, 700); };
  document.body.prepend(b); return true; })()"""


def test_a_beacon_does_not_cut_the_dead_click_window_short(journey, shop_server):
    """A click-tracking request is no answer: the settle still waits for the toast that comes 700 ms later."""
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Premi il pulsante", oracle="cart_not_empty",
                           profile="desktop")
    runner.tab.evaluate(BEACON_THEN_TOAST)
    observation = act(runner, "WAIT")["observation"]
    result = act(runner, "CLICK", index_of(observation, r"^Traccia e avvisa$"))
    metrics = result["step_metrics"]
    assert result["executed"] and metrics["mutations"] >= 1 and "dead_click" not in metrics["flags"], metrics
    assert "Fatto: avviso" in result["observation"]["text_excerpt"]
    assert any(r["path"] == "/api/beacon" for r in shop_server.requests)
    step = store.read_steps(started["run_id"])[-1]
    assert not step["flags"]["dead_click"] and step["since"]["mutations"] >= 1


def test_a_page_that_failed_to_load_is_a_navigation_error_not_another_site(journey, shop_server):
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Apri la scheda", oracle="pdp_reached",
                           profile="desktop")
    runner.tab.evaluate("document.body.prepend(Object.assign(document.createElement('a'), "
                        "{href: 'http://127.0.0.1:1/shop/product.html?id=1', textContent: 'Scheda rotta'})), true")
    observation = act(runner, "WAIT")["observation"]
    result = act(runner, "CLICK", index_of(observation, r"^Scheda rotta$"))
    assert result["executed"] and result["status"] == "blocked" and result["observation"]["controls"] == []
    assert result["observation"]["url"].startswith("chrome-error://")
    assert result["observation"]["guard_notes"][0].startswith("the page did not load (chrome-error://")
    step = store.read_steps(started["run_id"])[-1]
    assert step["flags"]["navigation_error"] and not step["flags"]["external_nav"] and not step["flags"]["dead_click"]
    finished = runner.finish()
    assert finished["friction"]["FAI.UNEXPECTED_NAV"] == 0
    warnings = store.load(started["run_id"])["warnings"]
    assert any(w.startswith("navigation_error: the page did not load (chrome-error://") for w in warnings)
    assert not [w for w in warnings if w.startswith("left_shop")]


def test_a_navigation_after_the_last_observation_is_logged_at_finish(journey, shop_server):
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Leggi i resi", oracle="pdp_reached", profile="desktop")
    observation = act(runner, "WAIT")["observation"]
    runner.tab.evaluate("location.href = 'index.html', true")  # the page moves by itself; the host then finishes
    deadline = time.monotonic() + 10
    while not str(runner.tab.evaluate("location.pathname + document.readyState")).endswith("index.htmlcomplete"):
        assert time.monotonic() < deadline
        time.sleep(0.1)
    finished = runner.finish(status="blocked")
    steps = store.read_steps(started["run_id"])
    assert [s["operation"] for s in steps] == ["WAIT", "NAVIGATION"]
    assert steps[1]["url_before"] == observation["url"] and steps[1]["url_after"].endswith("/shop/index.html")
    assert finished["friction"]["FAI.UNEXPECTED_NAV"] == 1 and finished["steps"] == 1
    rows = {o["kpi_id"]: o for o in store.load(started["run_id"])["observations"]}
    assert rows["FAI.UNEXPECTED_NAV"]["evidence"]["between_steps"] == 1
    assert rows["FAI.BACKTRACK_RATE"]["evidence"] == {"visits": 2, "unique_pages": 2}


SECOND_COUNTER = """(() => { const box = document.createElement('article'), button = document.createElement('button');
  button.textContent = 'Altro'; button.onclick = () => { window.__other = (window.__other || 0) + 1; };
  box.append(button); document.body.prepend(box); return true; })()"""


def test_the_host_is_told_about_no_progress_and_keeps_choosing(journey, shop_server):
    """The Agent's no-progress stop is a demo safeguard: the host owns DONE and BLOCKED, so three dead clicks leave the
    run running with a note, and repeated dead clicks stay countable."""
    make, store = journey
    runner, run_id, _ = counter_page(make, shop_server)
    runner.tab.evaluate(SECOND_COUNTER)
    observation = act(runner, "WAIT")["observation"]
    for n in range(3):
        result = act(runner, "CLICK", index_of(observation, r"^Conta$"))
        observation = result["observation"]
        assert result["executed"] and result["status"] == "running"
        assert (observation["guard_notes"][:1] == [NO_PROGRESS]) is (n == 2)
    assert observation["controls"][-2:] == ["DONE", "BLOCKED"]
    assert act(runner, "CLICK", index_of(observation, r"^Conta$"))["status"] == "running"  # a fourth: the same run
    steps = store.read_steps(run_id)
    assert friction.rage_events(steps) == 1 and [s["flags"]["rage"] for s in steps[-4:]] == [False, False, True, True]
    for _ in range(3):
        observation = act(runner, "CLICK", index_of(runner.observation(), r"^Altro$"))["observation"]
    assert observation["status"] == "running" and clicks(runner) == 4
    finished = runner.finish(status="blocked")
    assert finished["friction"]["FAI.RAGE_EVENTS"] == 2 and finished["friction"]["FAI.DEAD_CLICK_RATE"] == 100.0
    warnings = store.load(run_id)["warnings"]
    assert warnings.count("no progress: three actions in a row changed nothing (the host was told and went on)") == 1
    assert not [w for w in warnings if w.startswith("agent stopped")]


def test_typesafe_still_stops_after_three_actions_that_changed_nothing(journey, shop_server, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "unused-in-tests")
    calls = []
    monkeypatch.setattr(loop, "choose", stand_in(r"^Conta$", calls))
    make, store = journey
    runner, run_id, _ = counter_page(make, shop_server, policy="typesafe")
    result = runner.run_auto()
    assert result["status"] == "blocked" and result["steps"] == 3 and len(calls) == 3 and clicks(runner) == 3
    finished = runner.finish()
    assert finished["friction"]["FAI.RAGE_EVENTS"] == 1  # truncated at the Agent's stop
    assert "agent stopped: three actions in a row changed nothing" in store.load(run_id)["warnings"]


def test_the_agent_budget_is_read_from_its_counters_not_from_an_error_message(journey, shop_server, monkeypatch):
    make, store = journey
    runner, run_id, _ = counter_page(make, shop_server)
    runner.agent.state["decisions"] = [{}] * (MAX_STEPS * 2)
    result = act(runner, "WAIT")
    assert not result["executed"] and result["status"] == "budget_exhausted"
    monkeypatch.setenv("TYPESAFE_API_KEY", "unused-in-tests")
    monkeypatch.setattr(loop, "choose", Mock(side_effect=ValueError("the provider's token budget is exceeded")))
    runner, run_id, _ = counter_page(make, shop_server, policy="typesafe")
    assert runner.run_auto()["status"] == "error"


def test_finish_after_a_failed_start_keeps_the_run_failed(tmp_path):
    store = RunStore(tmp_path)
    runner = JourneyRunner(EngagementSettings(url="https://shop.test/"), store=store,
                           transport_factory=Mock(side_effect=OSError("no browser")))
    with pytest.raises(OSError):
        runner.start("https://shop.test/", "Trova scarpe", oracle="cart_not_empty")
    finished = runner.finish()
    assert finished["status"] == "error" and finished["verification"] is None and finished["friction"] == {}
    run = store.load(finished["run_id"])
    assert run["status"] == "failed" and not run.get("observations") and run["journey"]["verification"] is None
    assert runner.finish() is finished


def test_finish_releases_the_browser_when_the_steps_file_cannot_be_read(journey, shop_server, monkeypatch):
    make, store = journey
    runner, run_id, _ = counter_page(make, shop_server)
    monkeypatch.setattr(store, "read_steps", Mock(side_effect=OSError("torn file")))
    finished = runner.finish(status="blocked")
    assert runner.transport is None and runner.tab is None and finished["steps"] == 1
    assert finished["friction"]["FAI.UNEXPECTED_NAV"] == 0  # from the in-memory steps
    run = store.load(run_id)
    assert any(w.startswith("steps.jsonl unreadable") for w in run["warnings"])
