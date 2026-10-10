"""Scrolling on the mobile profile (390x844) in a real Chromium, offline against tests/fixtures/shop: the crawler's
verified scroll to a control audit.js located (Tab.scroll_to, Tab.click) and the core wheel scroll (browser.py)."""

import time

import pytest

from jev_ultrafast.engagement.collectors import PageCollector
from jev_ultrafast.engagement.crawler import Tab
from jev_ultrafast.engagement.lexicon import lexicon_for
from jev_ultrafast.engagement.profiles import close_context, new_context
from jev_ultrafast.engagement.safety import CheckoutGuard

OLD_SCROLL_JS = "window.scrollTo(0, Math.max(0, %s - innerHeight / 2)), scrollY"  # Tab.scroll_to before the fix
TOP_JS = "[window, ...document.querySelectorAll('main')].forEach(s => s.scrollTo({top: 0, behavior: 'instant'}))"


@pytest.fixture
def mobile(transport, shop_server):
    """A Tab on the mobile profile in its own context (short settle windows); closed with its context."""
    context = new_context(transport)
    collector = PageCollector(transport, None, None, profile="mobile", context_id=context, screenshots=False)
    collector.net_quiet_s = collector.lcp_quiet_s = 0.3
    tab = Tab(collector, CheckoutGuard(shop_server.url("shop/index.html"), lexicon_for("it")), profile="mobile")
    yield tab
    tab.close()
    close_context(transport, context)


def cart_of(tab, shop_server, page):
    """The page loaded as a cart, and its checkout CTA as audit.js located it at load (page coordinates)."""
    cart = tab.load(shop_server.url(f"shop/{page}"), stage="cart", page_id="mobile-cart-1")
    control = cart["audit"]["cart"]["checkout_cta"]
    assert control["present"] and control["rect"]["y"] + control["rect"]["h"] / 2 > 844  # centre below the fold
    return cart, control


def observed(tab, label):
    return [a for a in (tab.observe() or {}).get("actions") or [] if a.get("label") == label]


@pytest.mark.parametrize("page, scroll_y", [("scroll-smooth.html", 444), ("scroll-inner.html", 0)])
def test_a_control_below_the_viewport_is_brought_into_view_found_and_clicked(mobile, shop_server, page, scroll_y):
    """The Mac case: on a cart with html {scroll-behavior: smooth} the checkout CTA's top sits 2 px above the
    viewport's bottom (audit.js rect x58 y842 w246 h47), so its centre is below it and snapshot.js does not list it;
    on an app shell the window never scrolls and a main element holds the content. scroll_to() moves the element
    itself (the inner scroller too), instantly, and says so; the click finds the CTA and opens the checkout page."""
    cart, control = cart_of(mobile, shop_server, page)
    assert observed(mobile, control["label"]) == []
    done = mobile.scroll_to(control["rect"])
    assert done == {"scroll_y": pytest.approx(scroll_y, abs=2), "in_view": True, "via": "element"}
    [cta] = observed(mobile, control["label"])  # read at once: no animation still under way
    assert 0 <= cta["rect"]["y"] + cta["rect"]["h"] / 2 < 844
    mobile.value(TOP_JS)  # back to the top: click() scrolls by itself
    assert observed(mobile, control["label"]) == []
    clicked = mobile.click(cart, purpose="checkout_entry", stage="cart", labels=[control["label"]], key="checkout",
                           rect=control["rect"], clear=True)
    assert (clicked["executed"], clicked["reason"], clicked["label"]) == (True, None, control["label"])
    assert "scroll" not in clicked and clicked["href"] == "checkout.html"
    assert mobile.await_effect(clicked["origin"], expect_navigation=True, timeout=10.0) == "navigated"
    assert mobile.value("location.pathname") == "/shop/checkout.html"


@pytest.mark.parametrize("page", ["scroll-smooth.html", "scroll-inner.html"])
def test_the_old_window_scroll_left_the_cta_outside_the_viewport(mobile, shop_server, page):
    """Why the Mac audit said control_not_found. On the smooth page window.scrollTo(0, y) only starts an animation:
    it returns with the page still at scrollY 0, the CTA's centre still below the viewport, and observe() followed at
    once (how far the animation got by then depends on the machine, so the test pins the state the old code returned
    in). On the app shell the window cannot scroll at all, however long one waits."""
    _, control = cart_of(mobile, shop_server, page)
    rect = control["rect"]
    assert mobile.value(OLD_SCROLL_JS % (rect["y"] + rect["h"] / 2)) == 0
    if page == "scroll-inner.html":
        time.sleep(0.5)
        assert mobile.value("scrollY") == 0 and observed(mobile, control["label"]) == []


def test_a_control_not_found_after_its_scroll_says_where_the_scroll_left_the_page(mobile, shop_server):
    """The scroll worked, the label did not match: "control_not_found" with the scroll that preceded the observation
    (requested_y: the page y of the rect's centre). A rect no element has (a control gone since the audit) scrolls the
    window as far as it goes and says the rect is not in view."""
    cart, control = cart_of(mobile, shop_server, "scroll-inner.html")
    clicked = mobile.click(cart, purpose="checkout_entry", stage="cart", labels=["Svuota il carrello"],
                           rect=control["rect"])
    assert (clicked["executed"], clicked["reason"]) == (False, "control_not_found")
    assert clicked["scroll"] == {"requested_y": 1424, "scroll_y": 0, "in_view": True, "via": "element"}
    cart, control = cart_of(mobile, shop_server, "scroll-smooth.html")
    clicked = mobile.click(cart, purpose="checkout_entry", stage="cart", labels=[control["label"]],
                           rect={**control["rect"], "y": 5000})
    assert clicked["reason"] == "control_not_found"
    assert clicked["scroll"] == {"requested_y": 5024, "scroll_y": 1825 - 844, "in_view": False, "via": "window"}


@pytest.mark.parametrize("profile", ["mobile", "desktop"])
def test_the_core_wheel_scroll_moves_the_page_on_both_profiles(transport, shop_server, profile):
    """browser.py's scroll action (one mouseWheel at x=550, y=650, from the original agent) scrolls a listing on the
    mobile profile too, although x=550 is outside its 390 px viewport."""
    context = new_context(transport)
    collector = PageCollector(transport, None, None, profile=profile, context_id=context, screenshots=False)
    browser = collector.open(shop_server.url("shop/category.html"))
    try:
        page = browser.observe(screenshot=False)
        assert page["scroll"]["y"] == 0
        [down] = [a for a in page["actions"] if a["id"] == "scroll_down"]
        browser.act(down, page)
        deadline = time.monotonic() + 5.0
        while browser.evaluate("scrollY") == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert browser.evaluate("scrollY") > 0
    finally:
        collector.close(browser)
        close_context(transport, context)
