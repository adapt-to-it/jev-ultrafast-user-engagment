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
from jev_ultrafast import model
from jev_ultrafast.browser import Browser, StalePage, fingerprint
from jev_ultrafast.engagement import friction, oracles
from jev_ultrafast.engagement.collectors import AuditError, PageCollector
from jev_ultrafast.engagement.friction import executed as step_executed
from jev_ultrafast.engagement.journey import (
    HOST_FINISH,
    MAX_STALE,
    NO_PROGRESS,
    TEXT_HELPER,
    TEXT_REFUSED,
    TEXT_WITHHELD,
    UNREAD,
    GuardRefused,
    HostPolicy,
    JourneyRunner,
    check_text,
    personal_text,
    resolve,
    text_model,
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


@pytest.mark.parametrize("text, what", [
    ("Via Roma 12, 20121 Milano", "a street address"), ("Viale Monza 5", "a street address"),
    ("221 Baker Street", "a street address"), ("nato il 12/03/1985", "a date"), ("12 marzo 1985", "a date"),
    ("CVV 123", "a card security code"), ("mario.rossi(at)gmail.com", "an email address"),
    ("mario.rossi at gmail dot com", "an email address"),
    # year-first, month-first and month-name-first dates; lowercase, comma and particle street forms
    ("1985-03-15", "a date"), ("03/15/1985", "a date"), ("1985/03/15", "a date"), ("March 15, 1985", "a date"),
    ("marzo 12, 1985", "a date"), ("Via Garibaldi, 12", "a street address"), ("via roma 12", "a street address"),
    ("Piazza del Duomo 1", "a street address"), ("Via dei Mille 5", "a street address"),
    ("Via dell'Indipendenza 5", "a street address"), ("Corso Vittorio Emanuele II 45", "a street address"),
    ("Corso di cucina 2024", None), ("materasso una piazza e mezza 160", None), ("pantalone largo taglia 42", None),
    ("maglia taglia 12/14 anni", None), ("tv 55 pollici 2024", None),
    # product words stay typeable (a name cannot be recognised: the field guard is the guarantee)
    ("Mario Rossi", None), ("Air Max 90", None), ("Xbox 360", None), ("corso di yoga 10", None),
    ("via col vento dvd", None), ("Street Fighter 6", None), ("running shoes at discount", None),
    ("tazza 12/24", None), ("calendario 2027", None), ("iPhone 15 Pro Max 256", None),
    # an email address with blanks around its @, or spelt with compatibility characters (read in NFKC form): the
    # fullwidth ＠ (U+FF20), the small ﹫ (U+FE6B), the fullwidth full stop ． (U+FF0E), fullwidth letters
    ("mario.rossi @ gmail.com", "an email address"), ("mario.rossi@ gmail.com", "an email address"),
    ("mario.rossi\t@ gmail.com", "an email address"), ("mario＠gmail.com", "an email address"),
    ("mario﹫gmail.com", "an email address"), ("mario@gmail．com", "an email address"),
    ("mario.rossi ＠ gmail．com", "an email address"), ("ｍａｒｉｏ＠ｇｍａｉｌ．ｃｏｍ", "an email address"),
    ("scarpe @ 50% di sconto", None), ("taglia 42 @ nike", None),
    ("４１１１ １１１１ １１１１ １１１１", "a card, phone or account number"),
])
def test_text_that_looks_like_an_address_a_date_or_a_security_code_is_refused(text, what):
    assert personal_text(text) == what
    if what:
        with pytest.raises(ValueError, match=f"looks like {what}"):
            host().set("TYPE_TEXT", "1", text)


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
    monkeypatch.setattr(oracles, "read_page", lambda browser, collector: ({"title": "Carrello"}, {"type": "cart"}))
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


def test_a_page_the_oracle_cannot_read_is_a_harness_failure_not_the_shops(monkeypatch):
    """audit.js fails on the cart page (PageCollector.collect keeps audit {} with the note), or on the final page with
    no visited cart page to fall back on: not assessable, never a failed goal. A visited cart still leads to it."""
    run = {"site": {"start_url": "https://shop.test/"}, "settings": {"locale": "it"}}
    browser = Mock(evaluate=Mock(return_value="https://shop.test/carrello"))
    unread = {"page_id": "desktop-verify-cart", "classification": {"type": "other"}, "audit": {},
              "notes": ["audit failed: x"]}
    collector = Mock(profile="desktop", collect=Mock(return_value=unread))
    monkeypatch.setattr(oracles, "read_page", lambda browser, collector: ({"title": "Carrello"}, {"type": "cart"}))
    cart_oracles = (("cart_not_empty", {}), ("cart_contains_item_under_price", {"max_price": 50}))
    for name, params in cart_oracles:
        result = oracles.verify(name, params, browser=browser, collector=collector, run=run, steps=[])
        assert result["passed"] is None and result["checks"]["not_assessable"] == "cart_unreadable", name
        assert result["page"] is unread and "unreadable" not in result["checks"]  # the page stays as evidence
    monkeypatch.setattr(oracles, "read_page", Mock(side_effect=AuditError("audit.js timed out")))
    for name, params in cart_oracles:
        result = oracles.verify(name, params, browser=browser, collector=collector, run=run, steps=[])
        assert result["passed"] is None and result["checks"]["not_assessable"] == "final_page_unreadable", name
        assert result["checks"]["cart_found"] is False and result["page"] is None
    cart = {"page_id": "desktop-verify-cart", "classification": {"type": "cart"},
            "audit": {"cart": {"line_items": [row("Brezza", 39.9, 1)], "subtotal_value": 39.9}}}
    monkeypatch.setattr(oracles, "_load", Mock(return_value=cart))
    visited = [{"url_before": "https://shop.test/carrello", "page_type": "cart"}]
    result = oracles.verify("cart_not_empty", {}, browser=browser, collector=collector, run=run, steps=visited)
    assert result["passed"] is True and result["checks"]["cart_source"] == "visited_cart_page"
    for name in ("pdp_reached", "search_results_shown"):  # the page oracles read the final page only
        result = oracles.verify(name, {}, browser=browser, collector=collector, run=run)
        assert result["passed"] is None and result["checks"]["not_assessable"] == "final_page_unreadable", name


def test_a_page_the_browser_did_not_load_is_not_audited():
    """Chrome's error page is no page of the shop: the oracle does not audit it (no spurious page type)."""
    run = {"site": {"start_url": "https://shop.test/"}, "settings": {"locale": "it"}}
    browser = Mock(evaluate=Mock(return_value="chrome-error://chromewebdata/"))
    collector = Mock(profile="desktop", audit=Mock(side_effect=AssertionError("Chrome's error page is not audited")))
    result = oracles.verify("pdp_reached", {}, browser=browser, collector=collector, run=run)
    assert result["passed"] is False and "not_assessable" not in result["checks"]
    assert result["checks"]["page_type"] == "other" and result["checks"]["same_site"] is False
    assert oracles.read_page(browser, collector)[1]["signals"] == ["navigation_error"]


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


def test_page_oracles_look_for_the_querys_own_words_in_what_the_page_shows(monkeypatch):
    """A function word is in any title, and a search URL echoes whatever was typed: neither finds the product."""
    run = {"site": {"start_url": "https://shop.test/"}}

    def verify(name, params, audit, kind):
        monkeypatch.setattr(oracles, "read_page", lambda browser, collector: (audit, {"type": kind}))
        return oracles.verify(name, params, browser=Mock(), collector=Mock(), run=run)

    case = {"url": "https://shop.test/p/9", "pdp": {"title": "Custodia per tablet 10 pollici"}}
    result = verify("pdp_reached", {"query": "zaino per laptop"}, case, "pdp")
    assert result["passed"] is False and result["checks"]["query_words_found"] == []
    assert verify("pdp_reached", {"query": "custodia per laptop"}, case, "pdp")["checks"]["query_words_found"] == [
        "custodia"]
    listing = {"url": "https://shop.test/search?q=zaino+impermeabile", "doc": {"h1s": ["I più venduti"]},
               "products": {"cards_count": 4, "cards": [{"title": "Borraccia termica"}, {"title": "Felpa"}]}}
    result = verify("search_results_shown", {"query": "zaino impermeabile"}, listing, "plp")
    assert result["passed"] is False and result["checks"]["query_words_found"] == []
    found = {**listing, "products": {"cards_count": 1, "cards": [{"title": "Zaino impermeabile 30 L"}]}}
    assert verify("search_results_shown", {"query": "zaino impermeabile"}, found, "plp")["passed"] is True
    empty = {**found, "filters": {"no_results_text": "Nessun risultato per «zaino impermeabile»"}}
    result = verify("search_results_shown", {}, empty, "plp")
    assert result["passed"] is False and result["checks"]["no_results_text"].startswith("Nessun risultato")


@pytest.mark.parametrize("kwargs", [
    {"oracle": "made_up"}, {"profile": "tablet"}, {"policy": "random"}, {"max_steps": 0}, {"max_steps": 500},
    {"optimal_steps": 0}, {"goal": " "}, {"url": "ftp://shop.test/"}, {"url": "https://stage:S3cret-pw@shop.test/"},
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


def assert_never_offered_a_pay_control(observations, requests):
    """requests: the shop_server requests of this test only (the log is session-wide)."""
    labels = [e["label"] for o in observations for e in o["elements"]]
    assert labels and not [label for label in labels if PAY.forbidden(label)]
    assert not [r for r in requests if r["method"] != "GET" or r["path"].startswith("/pay")]


def test_host_journey_adds_a_product_under_50_eur_and_the_oracle_confirms_it(journey, shop_server, chromium):
    make, store = journey
    runner = make()
    since = len(shop_server.requests)
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
    assert_never_offered_a_pay_control(seen, shop_server.requests[since:])

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
    since = len(shop_server.requests)
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
    assert_never_offered_a_pay_control(seen, shop_server.requests[since:])
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
    since = len(shop_server.requests)
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
    assert len([r for r in shop_server.requests[since:] if "add-to-cart=7" in r["path"]]) == 1
    assert any(w.startswith("left_shop: http://localhost:") for w in store.load(started["run_id"])["warnings"])


def test_a_checkout_on_another_site_is_still_the_checkout_boundary(journey, shop_server):
    make, store = journey
    runner = make()
    since = len(shop_server.requests)
    runner.start(shop_server.url("shop/resi.html"), "Vai alla cassa", oracle="cart_not_empty", profile="desktop")
    elsewhere = shop_server.url("shop/checkout.html").replace("127.0.0.1", "localhost")
    runner.tab.evaluate(f"document.body.prepend(Object.assign(document.createElement('a'), "
                        f"{{href: {json.dumps(elsewhere)}, textContent: 'Cassa del partner'}})), true")
    observation = act(runner, "WAIT")["observation"]
    result = act(runner, "CLICK", index_of(observation, r"^Cassa del partner$"))
    assert result["executed"] and result["status"] == "stopped_at_checkout_boundary"
    assert result["observation"]["url"] == elsewhere and result["observation"]["controls"] == []
    assert runner.finish()["status"] == "stopped_at_checkout_boundary"
    assert not [r for r in shop_server.requests[since:] if r["method"] != "GET" or r["path"].startswith("/pay")]


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
    assert moved["page_type"] == "other" and not step_executed(moved)  # the type of the page it left (resi.html)
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
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "unused-in-tests")  # TYPE_TEXT offered (never chosen): a plain failure
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
    append, observe, send = store.append_step, tab.observe, runner._send
    store.append_step = lambda rid, step: (events.append(("log", step["operation"])), append(rid, step))[1]
    tab.observe = lambda **kw: (events.append(("observe",)), observe(**kw))[1]
    runner._send = lambda *a, **kw: (events.append(("act",)), send(*a, **kw))[1]
    try:
        assert act(runner, "CLICK", index_of(observation, r"^Conta$"))["executed"]
    finally:
        del store.append_step, tab.observe, runner._send
    assert events == [("act",), ("log", "CLICK"), ("observe",)]


@pytest.mark.parametrize("error", [RuntimeError("connection lost after the input"), TimeoutError("no reply")])
def test_an_input_that_may_have_happened_is_logged_as_uncertain_and_never_sent_again(journey, shop_server, error):
    make, store = journey
    runner, run_id, observation = counter_page(make, shop_server)
    send = runner._send

    def act_then_fail(*args, **kwargs):
        send(*args, **kwargs)
        raise error

    runner._send = act_then_fail
    result = act(runner, "CLICK", index_of(observation, r"^Conta$"))
    del runner._send
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
    runner._send = Mock(side_effect=StalePage("moved"))  # the dispatch found the target changed: nothing was sent
    result = act(runner, "CLICK", index_of(observation, r"^Conta$"))
    del runner._send
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
    runner._send = Mock(side_effect=StalePage("moved"))
    result = runner.run_auto()
    assert result["status"] == "blocked" and result["steps"] == 0 and len(calls) == MAX_STALE
    assert all(s["flags"]["stale"] for s in store.read_steps(run_id)) and clicks(runner) == 0


@pytest.mark.parametrize("failure", ["malformed", "http", "credential"])
def test_an_infrastructure_failure_is_not_scored_as_the_sites(journey, shop_server, monkeypatch, failure):
    """A model answer the runner cannot read, a TypeSafe HTTP error (model.choose's own request, post_json faked), or
    a text helper whose provider refuses the key, stops the journey with an error: the journey's success is not
    assessable (never false), nothing escapes run_auto, and a TypeSafe call without a decision is counted (failed)."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "unused-in-tests")
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "unused-in-tests")
    if failure == "malformed":
        monkeypatch.setattr(loop, "choose", Mock(side_effect=KeyError("answers")))
    elif failure == "http":
        monkeypatch.setattr(model, "post_json", Mock(side_effect=RuntimeError(
            "Model provider returned HTTP 500; no action executed.")))
    else:
        monkeypatch.setattr(loop, "choose", stand_in(r"Cerca", [], operation="TYPE_TEXT"))
        refused = RuntimeError("Model provider returned HTTP 401; no action executed.")
        monkeypatch.setattr(loop, "field_text", Mock(side_effect=refused))
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
    decided = failure == "credential"  # TypeSafe answered; the text helper's failed call is no text call
    assert run["journey"]["model_calls"] == {"choose": int(decided), "text": 0, "stale_or_refused": 0,
                                             "failed": int(not decided), "model": "stand-in" if decided else None}


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
    since = len(shop_server.requests)
    started = runner.start(shop_server.url("shop/resi.html"), "Premi il pulsante", oracle="cart_not_empty",
                           profile="desktop")
    runner.tab.evaluate(BEACON_THEN_TOAST)
    observation = act(runner, "WAIT")["observation"]
    result = act(runner, "CLICK", index_of(observation, r"^Traccia e avvisa$"))
    metrics = result["step_metrics"]
    assert result["executed"] and metrics["mutations"] >= 1 and "dead_click" not in metrics["flags"], metrics
    assert "Fatto: avviso" in result["observation"]["text_excerpt"]
    assert any(r["path"] == "/api/beacon" for r in shop_server.requests[since:])
    step = store.read_steps(started["run_id"])[-1]
    assert not step["flags"]["dead_click"] and step["since"]["mutations"] >= 1


BROKEN = "http://127.0.0.1:1/shop/product.html?id=1"  # a port Chrome refuses (net::ERR_UNSAFE_PORT)
BROKEN_LINK = ("document.body.prepend(Object.assign(document.createElement('a'), "
               f"{{href: '{BROKEN}', textContent: 'Scheda rotta'}})), true")


def test_a_page_that_failed_to_load_is_a_navigation_error_not_another_site(journey, shop_server):
    """A page the browser did not load ends the journey (blocked) and leaves its outcome not assessable: the cause
    (DNS, TLS, a proxy, a dead host) cannot be verified from the run. The net error is recorded, not interpreted."""
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Apri la scheda", oracle="pdp_reached",
                           profile="desktop")
    runner.tab.evaluate(BROKEN_LINK)
    observation = act(runner, "WAIT")["observation"]
    result = act(runner, "CLICK", index_of(observation, r"^Scheda rotta$"))
    assert result["executed"] and result["status"] == "blocked" and result["observation"]["controls"] == []
    assert result["observation"]["url"].startswith("chrome-error://")
    assert result["observation"]["page_type"] == "other"  # Chrome's error page is not audited
    assert result["observation"]["guard_notes"][0].startswith("the page did not load (chrome-error://")
    step = store.read_steps(started["run_id"])[-1]
    assert step["flags"]["navigation_error"] and not step["flags"]["external_nav"] and not step["flags"]["dead_click"]
    assert step["guard_notes"][-1].startswith("navigation_error: net::ERR_") and BROKEN in step["guard_notes"][-1]
    finished = runner.finish()
    verification, friction_rows = finished["verification"], finished["friction"]
    checks = verification["checks"]
    assert finished["status"] == "blocked" and verification["passed"] is None
    assert checks["not_assessable"] == "navigation_error" and checks["oracle_passed"] is False
    assert checks["navigation_error"]["url"] == BROKEN and checks["navigation_error"]["error"].startswith("net::ERR_")
    assert checks["page_type"] == "other" and checks["same_site"] is False
    assert friction_rows["FAI.JOURNEY_SUCCESS"] is None and friction_rows["FAI.UNEXPECTED_NAV"] == 0
    run = store.load(started["run_id"])
    assert run["status"] == "partial"
    assert run["not_assessable"][-1] == {"stage": None, "profile": "desktop", "kpi_id": None,
                                         "reason": "navigation_error"}
    rows = {o["kpi_id"]: o for o in run["observations"]}
    assert rows["FAI.JOURNEY_SUCCESS"]["reason"] == "navigation_error"
    assert rows["FAI.DEAD_CLICK_RATE"]["value"] == 0.0  # the process KPIs stay assessed
    assert rows["FAI.BACKTRACK_RATE"]["evidence"] == {"visits": 1, "unique_pages": 1}  # the error page is no visit
    warnings = run["warnings"]
    assert any(w.startswith("navigation_error: the page did not load (chrome-error://") and BROKEN in w
               and "net::ERR_" in w for w in warnings)
    assert not [w for w in warnings if w.startswith("left_shop")]


def test_a_start_page_that_never_loaded_leaves_the_run_failed(journey, shop_server):
    make, store = journey
    runner = make()
    started = runner.start("http://127.0.0.1:9/nothing", "Trova un prodotto", oracle="pdp_reached",
                           profile="desktop")
    observation = started["observation"]
    assert started["status"] == "blocked" and observation["controls"] == []
    assert observation["url"].startswith("chrome-error://") and observation["page_type"] == "other"
    assert observation["guard_notes"][0].startswith("the page did not load (chrome-error://")
    finished = runner.finish()
    checks = finished["verification"]["checks"]
    assert finished["status"] == "blocked" and finished["verification"]["passed"] is None and finished["steps"] == 0
    assert checks["not_assessable"] == "navigation_error"
    assert checks["navigation_error"]["url"] == "http://127.0.0.1:9/nothing"
    assert checks["navigation_error"]["error"].startswith("net::ERR_")
    assert finished["friction"]["FAI.JOURNEY_SUCCESS"] is None
    run = store.load(started["run_id"])
    assert run["status"] == "failed" and run["not_assessable"][-1]["reason"] == "navigation_error"
    assert any(w.startswith("navigation_error: the page did not load") and "net::ERR_" in w for w in run["warnings"])
    assert runner.transport is None


def test_a_cart_filled_before_a_broken_link_still_counts(journey, shop_server):
    """A passing oracle counts whatever stopped the journey: the item was added and the cart visited before a link
    led to a page that did not load."""
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/product.html?id=3"), "Metti nel carrello la Brezza",
                           oracle="cart_not_empty", profile="desktop")
    observation = started["observation"]
    for pattern in (r"^Rifiuta tutti$", r"^Aggiungi al carrello$", r"^Vai al carrello$"):
        result = act(runner, "CLICK", index_of(observation, pattern))
        observation = result["observation"]
        assert result["executed"], pattern
    assert observation["page_type"] == "cart"
    runner.tab.evaluate(BROKEN_LINK)
    observation = act(runner, "WAIT")["observation"]
    assert act(runner, "CLICK", index_of(observation, r"^Scheda rotta$"))["status"] == "blocked"
    finished = runner.finish()
    checks = finished["verification"]["checks"]
    assert finished["verification"]["passed"] is True and checks["cart_source"] == "visited_cart_page"
    assert "not_assessable" not in checks and checks["navigation_error"]["error"].startswith("net::ERR_")
    assert finished["friction"]["FAI.JOURNEY_SUCCESS"] is True
    run = store.load(started["run_id"])
    assert run["status"] == "complete" and not run.get("not_assessable")


# a cart whose "Procedi" shows a loading view, then renders the address step in place at the same URL
SPA_CHECKOUT = """(() => { const b = Object.assign(document.createElement('button'), {type: 'button',
    textContent: 'Procedi'});
  b.onclick = () => { const main = document.getElementById('main');
    main.innerHTML = '<p aria-busy="true">Caricamento…</p>';
    setTimeout(() => { main.innerHTML = `<h1>Dati di spedizione</h1><form action="/pay" method="post">
      <label for="n">Nome</label><input id="n" name="firstname" autocomplete="given-name">
      <label for="s">Cognome</label><input id="s" name="lastname" autocomplete="family-name">
      <label for="e">Email</label><input id="e" name="email" type="email" autocomplete="email">
      <label for="a">Indirizzo</label><input id="a" name="address" autocomplete="street-address">
      <label for="c">CAP</label><input id="c" name="zip" autocomplete="postal-code">
      <button type="submit">Continua</button></form>`; }, 2500); };
  document.getElementById('main').prepend(b); return true; })()"""


def test_a_checkout_step_rendered_in_place_is_read_again_and_stops_the_run(journey, shop_server):
    """The checkout step renders at the same URL after the step's settle (while the host decides): its new form
    fields make the runner read the page type again, so the run stops at the boundary and its submit is never
    offered nor executed."""
    make, store = journey
    runner = make()
    since = len(shop_server.requests)
    started = runner.start(shop_server.url("shop/cart.html"), "Vai alla cassa", oracle="cart_not_empty",
                           profile="desktop")
    runner.tab.evaluate(SPA_CHECKOUT)
    observation = act(runner, "WAIT")["observation"]
    seen = [observation]
    result = act(runner, "CLICK", index_of(observation, r"^Procedi$"))
    observation = result["observation"]
    seen.append(observation)
    assert result["executed"] and result["status"] == "running" and "Caricamento" in observation["text_excerpt"]
    assert observation["page_type"] == "cart"  # the loading view
    time.sleep(3.0)  # the host decides; the address step renders meanwhile at the same URL
    result = act(runner, "WAIT")
    observation = result["observation"]
    seen.append(observation)
    assert not result["executed"] and result["status"] == "stopped_at_checkout_boundary"
    assert observation["page_type"] == "checkout" and observation["url"] == started["observation"]["url"]
    assert not [o for o in seen if index_of(o, r"^Continua$")
                or index_of(o, r"^(Nome|Cognome|Email|Indirizzo|CAP)$", "TYPE_TEXT")]
    raw = runner.tab.observe()  # the executor refuses the submit too, whatever a policy chose
    with pytest.raises(GuardRefused, match="checkout_boundary"):
        runner._act(next(a for a in raw["actions"] if a["label"] == "Continua"), raw, None)
    runner.finish()
    assert not [r for r in shop_server.requests[since:] if r["method"] != "GET" or r["path"].startswith("/pay")]


def test_controls_withheld_on_an_unread_page_leave_a_failed_goal_not_assessable(journey, shop_server, monkeypatch):
    """audit.js fails (a harness failure): the guard withholds the add-to-cart button, the host gives up, and the
    empty cart is not charged to the shop."""
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/product.html?id=3"), "Metti nel carrello la Brezza",
                           oracle="cart_not_empty", profile="desktop")
    observation = act(runner, "CLICK", index_of(started["observation"], r"^Rifiuta tutti$"))["observation"]
    real, failing = PageCollector.audit, {"on": True}

    def audit(collector, browser):
        if failing["on"]:
            raise AuditError("audit.js: TypeError: Array.prototype.map is not a function")
        return real(collector, browser)

    monkeypatch.setattr(PageCollector, "audit", audit)
    observation = act(runner, "CLICK", index_of(observation, r"^41$"))["observation"]
    assert observation["page_type"] is None and observation["guard_notes"][0] == UNREAD
    assert index_of(observation, r"^Aggiungi al carrello$") is None
    assert act(runner, "BLOCKED")["status"] == "blocked"
    failing["on"] = False  # the oracle reads the empty cart with a working audit.js
    finished = runner.finish()
    checks = finished["verification"]["checks"]
    assert finished["verification"]["passed"] is None and checks["not_assessable"] == "page_unreadable"
    assert checks["oracle_passed"] is False and finished["friction"]["FAI.JOURNEY_SUCCESS"] is None
    run = store.load(started["run_id"])
    assert run["status"] == "partial"
    assert any(w.startswith("page_unreadable: audit.js failed on ") and w.endswith("controls were withheld")
               for w in run["warnings"])


ECHO = """(() => { const q = document.getElementById('q'), out = document.createElement('p');
  out.id = 'echo'; document.body.prepend(out);
  q.addEventListener('input', () => { out.textContent = 'Cerchi: ' + q.value; }); return true; })()"""


def test_the_harness_freshness_check_is_not_site_time(journey, shop_server, monkeypatch):
    """Browser.act's freshness check (a snapshot.js read, hundreds of ms on a large throttled page) runs before the
    clock: execution_ms and the first response time hold the input and the site's answer only."""
    make, store = journey
    runner, run_id, _ = counter_page(make, shop_server)
    runner.tab.evaluate(ECHO)
    observation = act(runner, "WAIT")["observation"]
    fresh = Browser.fresh

    def slow(self, page, action=None):
        time.sleep(0.3)
        return fresh(self, page, action)

    monkeypatch.setattr(Browser, "fresh", slow)
    result = act(runner, "TYPE_TEXT", index_of(observation, r"Cerca", "TYPE_TEXT"), "scarpe")
    metrics = result["step_metrics"]
    assert result["executed"] and metrics["mutations"] >= 1, metrics
    assert metrics["execution_ms"] < 300 and metrics["first_response_ms"] < 300, metrics
    assert runner.tab.evaluate("document.getElementById('echo').textContent") == "Cerchi: scarpe"


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
    assert steps[1]["page_type"] == observation["page_type"] == "other"  # the type of the page it left
    assert finished["friction"]["FAI.UNEXPECTED_NAV"] == 1 and finished["steps"] == 1
    rows = {o["kpi_id"]: o for o in store.load(started["run_id"])["observations"]}
    assert rows["FAI.UNEXPECTED_NAV"]["evidence"]["between_steps"] == 1
    assert rows["FAI.BACKTRACK_RATE"]["evidence"] == {"visits": 2, "unique_pages": 2}


def test_a_page_that_failed_to_load_after_the_last_observation_is_found_at_finish(journey, shop_server):
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Apri la scheda", oracle="pdp_reached",
                           profile="desktop")
    act(runner, "WAIT")
    runner.tab.evaluate(f"location.href = '{BROKEN}', true")  # the page moves by itself; the host then finishes
    deadline = time.monotonic() + 10
    while not str(runner.tab.evaluate("location.href")).startswith("chrome-error://"):
        assert time.monotonic() < deadline
        time.sleep(0.1)
    finished = runner.finish(status="done")
    checks = finished["verification"]["checks"]
    assert finished["verification"]["passed"] is None and checks["not_assessable"] == "navigation_error"
    assert checks["navigation_error"] == {"url": BROKEN, "error": checks["navigation_error"]["error"]}
    assert str(checks["navigation_error"]["error"]).startswith("net::ERR_")
    steps = store.read_steps(started["run_id"])
    assert steps[-1]["operation"] == "NAVIGATION" and steps[-1]["flags"]["navigation_error"]
    run = store.load(started["run_id"])
    assert run["status"] == "partial" and run["not_assessable"][-1]["reason"] == "navigation_error"
    assert any(w.startswith("navigation_error: the page did not load (chrome-error://") for w in run["warnings"])


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


# ---------------------------------------------------------------- Jev pilot: text helper, model calls and timing

JEV = "https://api.typesafe.ai/v1/systemone"


def jev_env(monkeypatch, text_key=None):
    """Policy typesafe with fixed models; text_key: TEXT_MODEL_API_KEY (None: no text helper). No request leaves the
    machine: model.post_json is replaced by typesafe() in every test that drives the loop."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "unused-in-tests")
    for name in ("TYPESAFE_MODEL", "TEXT_MODEL", "TEXT_MODEL_BASE_URL", "TEXT_MODEL_REASONING", "TEXT_MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    if text_key:
        monkeypatch.setenv("TEXT_MODEL_API_KEY", text_key)


def jev_answer(ids, selected):
    """A schema-valid TypeSafe Choice answer (model.validate_choice) that picks selected among ids."""
    ids = list(ids)
    rest = round(0.1 / (len(ids) - 1), 6) if len(ids) > 1 else 0.0
    probabilities = {i: (0.9 if len(ids) > 1 else 1.0) if i == selected else rest for i in ids}
    return {"type": "choice", "choice": selected, "probabilities": probabilities, "confidence": 0.8}


def element(body, pattern, operation="CLICK"):
    """The index of the first element of a TypeSafe request whose label matches and that offers the operation."""
    return next((e["index"] for e in body["state"]["elements"]
                 if re.search(pattern, e["label"]) and operation in e["operations"]), None)


def typesafe(decide, requests, texts=None, text="scarpe da corsa"):
    """model.post_json standing in for TypeSafe and the text helper: it checks the body model.choose (model.field_text)
    built and answers schema-valid, so the real request building and validate_choice run. decide(body) returns
    (operation, element index or None); text is the helper's value."""
    def post(url, key, body):
        assert key == "unused-in-tests"
        if url.endswith("/chat/completions"):  # model.field_text: one OpenAI-compatible JSON request
            assert texts is not None and body["response_format"] == {"type": "json_object"}
            assert [m["role"] for m in body["messages"]] == ["system", "user"]
            context = json.loads(body["messages"][1]["content"])
            assert set(context) == {"goal", "field", "page", "recent_actions"}
            texts.append(context)
            return {"choices": [{"message": {"content": json.dumps({"text": text})}}],
                    "usage": {"prompt_tokens": 90, "completion_tokens": 6}}
        assert url == JEV and set(body) == {"model", "state", "questions"} and body["model"] == "jev-latest"
        state, questions = body["state"], body["questions"]
        assert set(state) == {"page", "elements", "recent_actions"} and set(state["page"]) == {"url", "title", "text"}
        operations = questions["operation"]["criteria"]
        assert {"DONE", "BLOCKED"} <= set(operations)
        assert len({json.dumps(q["instructions"]["goal"]) for q in questions.values()}) == 1  # one goal, every head
        indices = {e["index"] for e in state["elements"]}
        for name, question in questions.items():  # the operation head and one target head per offered operation
            assert question["type"] == "choice" and question["criteria"]
            if name != "operation":
                assert name.removesuffix("_target").upper() in operations
                assert {index.split(":")[0] for index in question["criteria"]} <= indices
        requests.append(body)
        operation, target = decide(body)
        answers = {"operation": jev_answer(operations, operation)}
        if target is not None:
            head = f"{operation.lower()}_target"
            answers[head] = jev_answer(questions[head]["criteria"], target)
        return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 1000, "output_tokens": 2}}
    return post


@pytest.mark.parametrize("reached", [False, True])
def test_typesafe_without_a_text_helper_refuses_a_chosen_type_text(journey, shop_server, monkeypatch, reached):
    """No TEXT_MODEL_API_KEY: TYPE_TEXT stays offered (the loop as it is) until Jev chooses it; then nothing is typed
    and no text request leaves (a refused TYPE_TEXT attempt), fill actions are withheld from there on and the run goes
    on. A failed goal is then not the site's failure (text_helper_unavailable); a reached goal passes."""
    jev_env(monkeypatch)
    requests, texts = [], []

    def decide(body):
        if len(requests) == 1:
            return "TYPE_TEXT", element(body, r"Cerca", "TYPE_TEXT")
        return "DONE", None

    monkeypatch.setattr(model, "post_json", typesafe(decide, requests, texts))
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Cerca scarpe da corsa e mettine un paio nel carrello",
                           oracle="cart_not_empty", policy="typesafe", profile="desktop")
    run_id = started["run_id"]
    store.update(run_id, lambda run: run["journey"].update(policy_requested="auto"))  # the service's own key
    observation = started["observation"]
    assert TEXT_WITHHELD not in observation["guard_notes"]
    assert index_of(observation, r"Cerca", "TYPE_TEXT")  # offered until Jev chooses it
    if reached:  # the goal is reached without typing: an earlier tab of the shop filled the cart (localStorage)
        runner.tab.evaluate(f"localStorage.setItem('ps-cart', JSON.stringify({json.dumps(BREZZA)})), true")
    result = runner.run_auto()
    assert result["status"] == "done" and len(requests) == 2 and texts == []  # no chat-completions request
    assert runner.tab.evaluate("document.getElementById('q').value") == ""  # nothing typed
    first, second = requests
    assert "TYPE_TEXT" in first["questions"]["operation"]["criteria"] and "type_text_target" in first["questions"]
    assert "TYPE_TEXT" not in second["questions"]["operation"]["criteria"]  # the same page, fills withheld
    assert "type_text_target" not in second["questions"]
    assert not [e for e in second["state"]["elements"] if "TYPE_TEXT" in e["operations"]]
    assert second["state"]["page"]["url"] == first["state"]["page"]["url"]
    assert TEXT_WITHHELD in result["observation"]["guard_notes"]
    assert not index_of(result["observation"], r"Cerca", "TYPE_TEXT")
    steps = [s for s in store.read_steps(run_id) if s["operation"] != "NAVIGATION"]
    assert [(s["operation"], s["kind"], s["flags"].get("guard_blocked")) for s in steps] == [
        ("TYPE_TEXT", "fill", True), ("DONE", None, None)]
    assert re.search(r"Cerca", steps[0]["label"]) and steps[0]["guard_notes"] == [TEXT_WITHHELD]
    assert steps[0]["flags"] == {"stale": False, "guard_blocked": True, "text_withheld": True}  # why nothing ran
    assert not step_executed(steps[0]) and steps[0]["execution_ms"] is None
    finished = runner.finish()
    run = store.load(run_id)
    record, checks = run["journey"], finished["verification"]["checks"]
    assert checks["text_withheld_at"] == {"url": steps[0]["url_before"], "label": steps[0]["label"],
                                          "step": steps[0]["step"]}  # a fact of the run, whatever the verdict
    assert record["text_helper"] is None and record["policy_requested"] == "auto"  # merged, not overwritten
    assert record["model_calls"] == {"choose": 2, "text": 0, "stale_or_refused": 1, "failed": 0,
                                     "model": "jev-1.13.0"}
    warnings = [w for w in run["warnings"] if w.startswith("text_helper_unavailable: ")]
    assert len(warnings) == 1 and warnings[0].startswith("text_helper_unavailable: Jev chose TYPE_TEXT into 'Cerca")
    assert "nothing typed, fill actions withheld from here on" in warnings[0]
    success = next(o for o in run["observations"] if o["kpi_id"] == "FAI.JOURNEY_SUCCESS")
    if reached:
        assert finished["verification"]["passed"] is True and "not_assessable" not in checks
        assert success["assessed"] and success["value"] is True and run["status"] == "complete"
    else:
        assert finished["verification"]["passed"] is None and checks["not_assessable"] == "text_helper_unavailable"
        assert checks["oracle_passed"] is False  # the oracle's reading stays as evidence
        assert not success["assessed"] and success["reason"] == "text_helper_unavailable"
        assert run["status"] == "partial"


@pytest.mark.parametrize("text_key", [None, "unused-in-tests"])
def test_a_missing_text_helper_never_excuses_a_failure_jev_did_not_type_for(journey, shop_server, monkeypatch,
                                                                            text_key):
    """The add-to-cart button is dead and Jev never chooses TYPE_TEXT: with or without a text helper the goal failed
    on the site (FAI.JOURNEY_SUCCESS assessed false), although the page offers a search field."""
    jev_env(monkeypatch, text_key)
    requests, texts = [], []

    def decide(body):
        if consent := element(body, r"^Rifiuta tutti$"):
            return "CLICK", consent
        picked = sum(1 for b in requests if not element(b, r"^Rifiuta tutti$"))
        if picked == 1:  # the button loses its listener; WAIT observes the page again
            runner.tab.evaluate("document.getElementById('add').replaceWith(document.getElementById('add')"
                                ".cloneNode(true)), true")
            return "WAIT", None
        if picked == 2:
            return "CLICK", element(body, r"^Aggiungi al carrello$")
        return "BLOCKED", None

    monkeypatch.setattr(model, "post_json", typesafe(decide, requests, texts))
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/product.html"), "Aggiungi questo prodotto al carrello",
                           oracle="cart_not_empty", policy="typesafe", profile="desktop")
    assert runner.run_auto()["status"] == "blocked" and texts == []
    assert all(element(body, r"Cerca", "TYPE_TEXT") for body in requests)  # TYPE_TEXT offered on every request
    finished = runner.finish()
    run = store.load(started["run_id"])
    steps = store.read_steps(started["run_id"])
    assert ("CLICK", "Aggiungi al carrello") in [(s["operation"], s["label"]) for s in steps if step_executed(s)]
    assert finished["verification"]["passed"] is False
    assert "not_assessable" not in finished["verification"]["checks"]
    success = next(o for o in run["observations"] if o["kpi_id"] == "FAI.JOURNEY_SUCCESS")
    assert success["assessed"] and success["value"] is False and finished["friction"]["FAI.JOURNEY_SUCCESS"] is False
    assert not [w for w in run["warnings"] if w.startswith("text_helper_unavailable")]
    assert not [s for s in steps if s["flags"].get("guard_blocked")]
    assert "text_withheld_at" not in finished["verification"]["checks"]


@pytest.mark.parametrize("helper", ["none", "typed", "refused"])
def test_refusal_on_home_then_dead_add_to_cart_on_pdp(journey, shop_server, monkeypatch, helper):
    """The pin of ruling 2/5. Jev tries the search box once on the home page, then reaches the product by clicks and
    its add-to-cart button is dead (the site's defect; typing plays no part in it). With a text helper that types, the
    failure is the site's. Without one, or when the guard refuses the helper's value at input (an EAN reads as an
    account number), the pilot's chosen path was denied, so the failed outcome is confounded on whichever page it
    failed: not assessable (text_helper_unavailable, text_refused), never blamed on the site, while the refused
    attempt, checks.text_withheld_at and the dead clicks (FAI.DEAD_CLICK_RATE, computed whatever the verdict) stay
    recorded."""
    jev_env(monkeypatch, None if helper == "none" else "unused-in-tests")
    requests, texts, seen = [], [], set()

    def decide(body):
        url = body["state"]["page"]["url"]
        if consent := element(body, r"^Rifiuta tutti$"):
            return "CLICK", consent
        if "index.html" in url and "search" not in seen:
            seen.add("search")
            return "TYPE_TEXT", element(body, r"Cerca", "TYPE_TEXT")
        if "index.html" in url:
            return "CLICK", element(body, r"^Scarpe da corsa$")
        if "category.html" in url:
            return "CLICK", element(body, r"^Scarpa da corsa Brezza$")
        if "product.html" in url and "dead" not in seen:  # the button loses its listener; WAIT observes again
            seen.add("dead")
            runner.tab.evaluate("document.getElementById('add').replaceWith(document.getElementById('add')"
                                ".cloneNode(true)), true")
            return "WAIT", None
        added = sum(1 for r in body["state"]["recent_actions"] if r["action"] == "Aggiungi al carrello")
        if "product.html" in url and added < 2:
            return "CLICK", element(body, r"^Aggiungi al carrello$")
        return "BLOCKED", None

    value = "8001234567890" if helper == "refused" else "scarpe da corsa"
    monkeypatch.setattr(model, "post_json", typesafe(decide, requests, texts, text=value))
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/index.html"), "Aggiungi al carrello la scarpa Brezza",
                           oracle="cart_not_empty", policy="typesafe", profile="desktop")
    assert runner.run_auto()["status"] == "blocked"
    finished = runner.finish()
    run, steps = store.load(started["run_id"]), store.read_steps(started["run_id"])
    checks, rows = finished["verification"]["checks"], {o["kpi_id"]: o for o in run["observations"]}
    typed = [s for s in steps if s["operation"] == "TYPE_TEXT"]
    assert len(typed) == 1 and typed[0]["url_before"].endswith("index.html")
    dead = [s for s in steps if step_executed(s) and s["label"] == "Aggiungi al carrello"]
    assert [(s["flags"]["dead_click"], "product.html" in s["url_before"]) for s in dead] == [(True, True)] * 2
    rate = rows["FAI.DEAD_CLICK_RATE"]
    assert rate["assessed"] and rate["evidence"]["dead"] == 2 and rate["value"] > 0
    success = rows["FAI.JOURNEY_SUCCESS"]
    if helper == "typed":  # the same path with the search typed: the dead button is the site's failure
        assert len(texts) == 1 and step_executed(typed[0]) and "text_withheld" not in typed[0]["flags"]
        assert finished["verification"]["passed"] is False
        assert "not_assessable" not in checks and "text_withheld_at" not in checks
        assert success["assessed"] and success["value"] is False
    else:
        reason = "text_refused" if helper == "refused" else "text_helper_unavailable"
        assert len(texts) == (helper == "refused")  # the refused value was paid for
        assert not step_executed(typed[0]) and typed[0]["flags"]["text_withheld"]
        assert finished["verification"]["passed"] is None and checks["not_assessable"] == reason
        assert checks["oracle_passed"] is False  # the oracle's reading stays as evidence
        assert checks["text_withheld_at"]["url"].endswith("index.html")
        refusal = {"refusal": "personal_text: looks like a card, phone or account number"} if texts else {}
        assert checks["text_withheld_at"] == {"url": typed[0]["url_before"], "label": typed[0]["label"],
                                              "step": typed[0]["step"], **refusal}
        assert not success["assessed"] and success["reason"] == reason


@pytest.mark.parametrize("trigger, reached", [("value", False), ("field", True)])
def test_typesafe_text_the_guard_refuses_at_input_withholds_fills(journey, shop_server, monkeypatch, trigger,
                                                                  reached):
    """With a text helper, Jev's TYPE_TEXT that the guard refuses at input (the helper's value, a 13-digit EAN from
    the goal, reads as an account number; or the field turned to email after the observation) is denied like a missing
    helper: Jev has no notes channel, so fills are withheld and the next request differs (no TYPE_TEXT head) instead of
    the same request until MAX_STALE. A failed goal is then not the site's failure (text_refused); a reached one
    passes."""
    jev_env(monkeypatch, "unused-in-tests")
    requests, texts = [], []

    def decide(body):
        if field := element(body, r"Cerca", "TYPE_TEXT"):
            if trigger == "field":  # after the observation, before the input: the guard reads the field again there
                runner.tab.evaluate("document.getElementById('q').setAttribute('autocomplete', 'email'), true")
            return "TYPE_TEXT", field
        return "BLOCKED", None

    value = "8001234567890" if trigger == "value" else "scarpe da corsa"
    monkeypatch.setattr(model, "post_json", typesafe(decide, requests, texts, text=value))
    make, store = journey
    runner = make()
    started = runner.start(shop_server.url("shop/resi.html"), "Cerca il prodotto con codice EAN 8001234567890",
                           oracle="cart_not_empty", policy="typesafe", profile="desktop")
    run_id = started["run_id"]
    if reached:  # the goal is reached without typing: an earlier tab of the shop filled the cart (localStorage)
        runner.tab.evaluate(f"localStorage.setItem('ps-cart', JSON.stringify({json.dumps(BREZZA)})), true")
    result = runner.run_auto()
    assert result["status"] == "blocked" and len(requests) == 2 and len(texts) == 1  # Jev's own BLOCKED
    assert runner.tab.evaluate("document.getElementById('q').value") == ""  # nothing typed
    first, second = requests
    assert json.dumps(first, sort_keys=True) != json.dumps(second, sort_keys=True)
    assert "type_text_target" in first["questions"] and "type_text_target" not in second["questions"]
    assert not [e for e in second["state"]["elements"] if "TYPE_TEXT" in e["operations"]]
    assert second["state"]["page"]["url"] == first["state"]["page"]["url"]  # the same page, fills withheld
    notes = result["observation"]["guard_notes"]
    assert TEXT_REFUSED in notes and TEXT_WITHHELD not in notes
    refusal = ("personal_text: looks like a card, phone or account number" if trigger == "value"
               else "personal_field:email")
    steps = [s for s in store.read_steps(run_id) if s["operation"] != "NAVIGATION"]
    assert [(s["operation"], s["kind"]) for s in steps] == [("TYPE_TEXT", "fill"), ("BLOCKED", None)]
    assert steps[0]["flags"] == {"stale": False, "guard_blocked": True, "text_withheld": True}
    assert steps[0]["guard_notes"] == [f"refused by the checkout guard: {refusal}"] and not step_executed(steps[0])
    finished = runner.finish()
    run = store.load(run_id)
    checks = finished["verification"]["checks"]
    assert checks["text_withheld_at"] == {"url": steps[0]["url_before"], "label": steps[0]["label"],
                                          "step": steps[0]["step"], "refusal": refusal}
    assert run["journey"]["model_calls"] == {"choose": 2, "text": 1, "stale_or_refused": 1, "failed": 0,
                                             "model": "jev-1.13.0"}
    assert not [w for w in run["warnings"] if w.startswith(("stopped:", "text_helper_unavailable"))]
    warnings = [w for w in run["warnings"] if w.startswith("text_refused: ")]
    assert len(warnings) == 1 and warnings[0].startswith("text_refused: Jev's text for 'Cerca")
    assert warnings[0].endswith(f"was refused ({refusal}); nothing typed, fill actions withheld from here on")
    success = next(o for o in run["observations"] if o["kpi_id"] == "FAI.JOURNEY_SUCCESS")
    if reached:
        assert finished["verification"]["passed"] is True and "not_assessable" not in checks
        assert success["assessed"] and success["value"] is True
    else:
        assert finished["verification"]["passed"] is None and checks["not_assessable"] == "text_refused"
        assert checks["oracle_passed"] is False  # the oracle's reading stays as evidence
        assert not success["assessed"] and success["reason"] == "text_refused"


def test_the_recorded_text_helper_is_the_model_field_text_sends(monkeypatch):
    """journey.TEXT_HELPER pins model.field_text's default TEXT_MODEL, and text_model() reads TEXT_MODEL as field_text
    does (an empty value included): the record names the model the text request goes to. Offline: post_json is a
    fake that captures the body."""
    bodies = []

    def post(url, key, body):
        assert url == "https://api.deepseek.com/v1/chat/completions" and key == "unused-in-tests"
        bodies.append(body)
        return {"choices": [{"message": {"content": json.dumps({"text": "x"})}}]}

    monkeypatch.setattr(model, "post_json", post)
    for name in ("TEXT_MODEL", "TEXT_MODEL_BASE_URL", "TEXT_MODEL_REASONING", "TEXT_MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert text_model() is None  # no key: no text model, nothing is asked for
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "unused-in-tests")
    context = {"goal": "Cerca scarpe", "field": {"label": "Cerca"}, "page": {"title": "", "text": ""},
               "recent_actions": []}
    for value in (None, "another-model", ""):
        if value is None:
            monkeypatch.delenv("TEXT_MODEL", raising=False)
        else:
            monkeypatch.setenv("TEXT_MODEL", value)
        assert model.field_text(context)[0] == "x"
        assert bodies[-1]["model"] == text_model() == (TEXT_HELPER if value is None else value)


def test_typesafe_journey_records_model_calls_timing_and_usage(journey, shop_server, monkeypatch):
    """One TypeSafe request per decision (one that went stale included) and one text helper call: model_calls,
    timing_ms and usage at finish() agree with the steps and the stand-in's own counts."""
    jev_env(monkeypatch, text_key="unused-in-tests")
    requests, texts = [], []

    def decide(body):
        if len(requests) <= 2:  # the first click goes stale before its input, the second one runs
            return "CLICK", element(body, r"^Conta$")
        if len(requests) == 3:
            return "TYPE_TEXT", element(body, r"Cerca", "TYPE_TEXT")
        return "DONE", None

    monkeypatch.setattr(model, "post_json", typesafe(decide, requests, texts))
    make, store = journey
    runner, run_id, _ = counter_page(make, shop_server, policy="typesafe")
    send, sent = runner._send, []

    def stale_once(action, text):
        sent.append(action.get("label"))
        if len(sent) == 1:
            raise StalePage("moved")  # the dispatch found the target changed: nothing was sent
        return send(action, text)

    runner._send = stale_once
    result = runner.run_auto()
    del runner._send
    assert result["status"] == "done" and clicks(runner) == 1 and len(requests) == 4 and len(texts) == 1
    assert runner.tab.evaluate("document.getElementById('q').value") == "scarpe da corsa"
    runner.finish()
    record = store.load(run_id)["journey"]
    steps = [s for s in store.read_steps(run_id) if s["operation"] != "NAVIGATION"]
    assert [(s["operation"], bool(s["flags"].get("stale"))) for s in steps] == [
        ("CLICK", True), ("CLICK", False), ("TYPE_TEXT", False), ("DONE", False)]
    assert record["text_helper"] == "deepseek-chat"
    assert record["model_calls"] == {"choose": 4, "text": 1, "stale_or_refused": 1, "failed": 0,
                                     "model": "jev-1.13.0"}
    assert record["usage"] == {"input_tokens": 4000, "output_tokens": 8}
    timing = record["timing_ms"]
    assert set(timing) == {"decision", "text", "site", "wall"}
    assert timing["decision"] + timing["text"] == round(sum(s["decision_latency_ms"] for s in steps))
    site = sum(s["execution_ms"] + s["settle_ms"] for s in steps if step_executed(s))
    assert timing["site"] == round(site) and 0 <= timing["site"] <= timing["wall"]


def test_host_journey_records_its_choices_and_no_model_calls(journey, shop_server, monkeypatch):
    """Host: the validated choices are the decisions (stale and refused ones counted), the host's text is no model
    call and there is no token usage. Without a text helper key the host is still offered TYPE_TEXT."""
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    make, store = journey
    runner, run_id, observation = counter_page(make, shop_server)  # one WAIT
    assert TEXT_WITHHELD not in observation["guard_notes"]
    assert act(runner, "CLICK", index_of(observation, r"^Conta$"))["executed"]
    runner._send = Mock(side_effect=StalePage("moved"))
    assert act(runner, "CLICK", index_of(observation, r"^Conta$"))["stale"]
    del runner._send
    field = index_of(runner.observation(), r"Cerca", "TYPE_TEXT")
    runner.tab.evaluate("document.getElementById('q').setAttribute('autocomplete', 'email'), true")
    refused = act(runner, "TYPE_TEXT", field, "scarpe")
    assert refused["refused"] == "personal_field:email"  # a host is told and chooses again: no fill is withheld
    assert TEXT_REFUSED not in refused["observation"]["guard_notes"]
    finished = runner.finish(status="done")
    record = store.load(run_id)["journey"]
    assert record["text_helper"] == "host" and record["usage"] == {}
    assert record["model_calls"] == {"choose": 4, "text": 0, "stale_or_refused": 2, "failed": 0}  # no model key
    steps = store.read_steps(run_id)
    assert "text_withheld_at" not in finished["verification"]["checks"]
    assert [s["flags"] for s in steps if s["flags"].get("guard_blocked")] == [{"stale": False, "guard_blocked": True}]
    assert record["timing_ms"]["text"] == 0
    assert record["timing_ms"]["decision"] == round(sum(s["decision_latency_ms"] or 0 for s in steps))


def test_close_stores_the_cost_of_an_abandoned_journey(journey, shop_server):
    make, store = journey
    runner, run_id, _ = counter_page(make, shop_server)  # one WAIT
    runner.close()
    run = store.load(run_id)
    record = run["journey"]
    assert run["status"] == "partial" and record["status"] == "error" and record["verification"] is None
    assert record["model_calls"] == {"choose": 1, "text": 0, "stale_or_refused": 0, "failed": 0}
    assert record["usage"] == {}
    assert 0 <= record["timing_ms"]["site"] <= record["timing_ms"]["wall"]
