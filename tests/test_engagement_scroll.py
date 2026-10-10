"""Scrolling on the mobile profile (390x844) in a real Chromium, offline against tests/fixtures/shop: the crawler's
verified scroll to a control audit.js located (Tab.scroll_to, Tab.click), picking after an inner scroller or a sticky
control moved (shifted()), the audit read after a variant pick (UNSCROLL_JS), the top of the page for link lookups
(TOP_JS) and the core wheel scroll (browser.py)."""

import time

import pytest

from jev_ultrafast.browser import StalePage
from jev_ultrafast.engagement.collectors import PageCollector
from jev_ultrafast.engagement.crawler import TOP_JS, Crawl, Tab, _page_rect, own_controls, shifted
from jev_ultrafast.engagement.lexicon import lexicon_for
from jev_ultrafast.engagement.profiles import close_context, new_context
from jev_ultrafast.engagement.safety import CheckoutGuard
from jev_ultrafast.engagement.settings import EngagementSettings

OLD_SCROLL_JS = "window.scrollTo(0, Math.max(0, %s - innerHeight / 2)), scrollY"  # Tab.scroll_to before the fix
OLD_TOP_JS = "window.scrollTo({top: 0, left: 0, behavior: 'instant'}), scrollY"  # TOP_JS before the fix
ADD = "Aggiungi al carrello"


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


@pytest.mark.parametrize("page, scroll_y, dy", [("scroll-smooth.html", 444, 0), ("scroll-inner.html", 0, -1002)])
def test_a_control_below_the_viewport_is_brought_into_view_found_and_clicked(mobile, shop_server, page, scroll_y, dy):
    """The Mac case: on a cart with html {scroll-behavior: smooth} the checkout CTA's top sits 2 px above the
    viewport's bottom (audit.js rect x58 y842 w246 h47), so its centre is below it and snapshot.js does not list it;
    on an app shell the window never scrolls and a main element holds the content. scroll_to() moves the element
    itself (the inner scroller too), instantly, and says so (dy: how far the inner scroller moved it, 1424 - 422; 0
    for this CTA in the page flow when only the window moved); TOP_JS takes the page back to the top, the inner
    scroller included; the click finds the CTA and opens the checkout page."""
    cart, control = cart_of(mobile, shop_server, page)
    assert observed(mobile, control["label"]) == []
    done = mobile.scroll_to(control["rect"])
    assert done == {"scroll_y": pytest.approx(scroll_y, abs=2), "in_view": True, "via": "element", "dx": 0,
                    "dy": pytest.approx(dy, abs=2)}
    [cta] = observed(mobile, control["label"])  # read at once: no animation still under way
    assert 0 <= cta["rect"]["y"] + cta["rect"]["h"] / 2 < 844
    assert mobile.value(TOP_JS) == 0  # back to the top, the inner scroller too: click() scrolls by itself
    assert mobile.value("document.querySelector('main').scrollTop") == 0
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
    assert clicked["scroll"] == {"requested_y": 1424, "scroll_y": 0, "in_view": True, "via": "element", "dx": 0,
                                 "dy": -1002}
    cart, control = cart_of(mobile, shop_server, "scroll-smooth.html")
    clicked = mobile.click(cart, purpose="checkout_entry", stage="cart", labels=[control["label"]],
                           rect={**control["rect"], "y": 5000})
    assert clicked["reason"] == "control_not_found"
    assert clicked["scroll"] == {"requested_y": 5024, "scroll_y": 1825 - 844, "in_view": False, "via": "window",
                                 "dx": 0, "dy": 0}


def shell_of(tab, shop_server, query=""):
    """scroll-shell.html as a product page (query "?window": the same page scrolled by the window), its own
    add-to-cart as audit.js located it (centre below the fold) and the related products' in-card ones close below."""
    product = tab.load(shop_server.url(f"shop/scroll-shell.html{query}"), stage="pdp", page_id="mobile-pdp-1")
    audit = product["audit"]
    control = audit["pdp"]["add_to_cart"]
    assert product["classification"]["type"] == "pdp" and control["label"] == ADD and control["rect"]["y"] > 844
    related = [c for c in audit["ctas"] if c["label"] == ADD and c["in_card"]]
    assert len(related) == 3 and all(0 < c["rect"]["y"] - control["rect"]["y"] < 400 for c in related)
    return product, audit, control


@pytest.mark.parametrize("query, dy", [("", -1009), ("?window", 0)])
def test_after_an_inner_scroll_the_click_and_the_in_card_filter_compare_in_the_audits_frame(mobile, shop_server,
                                                                                          query, dy):
    """An app shell (main scrolls, the window never does): scroll_to() moves main by 1009 px, so every observed
    control of main sits 1009 px above where audit.js measured it. Picking by nearness to audit.js's rect with the
    window's scroll alone ranked the first related product's same-label button (285 px below the CTA in the viewport)
    nearer than the CTA itself and added that product; the in-card filter of the Jev offers (add_offer) matched none of
    the related buttons and offered them. With the shift taken back (shifted()) both compare in the audit's frame, as
    on the same page scrolled by the window (dy 0)."""
    product, audit, control = shell_of(mobile, shop_server, query)
    done = mobile.scroll_to(control["rect"])
    assert done == {"scroll_y": pytest.approx(1009 if query else 0, abs=2), "in_view": True, "via": "element",
                    "dx": 0, "dy": pytest.approx(dy, abs=2)}
    page = mobile.observe()
    assert len(observed(mobile, ADD)) == 4  # the CTA and the three related buttons are all in the viewport
    view = shifted(page, done)
    assert ("shift" in view) is bool(dy) and "shift" not in page  # a copy: act() gets the observation itself
    crawl = Crawl(mobile.collector, EngagementSettings(url=shop_server.url("shop/index.html")), profile="mobile",
                  guard=mobile.guard, jev_fallback=True)
    crawl.tab = mobile
    offer = crawl.add_offer(product, audit)
    in_card = own_controls([c for c in audit["ctas"] if c["in_card"]], audit)
    buttons = sorted((a for a in view["actions"] if a["label"] == ADD), key=lambda a: (a["rect"]["y"], a["rect"]["x"]))
    assert [in_card(a, view) for a in buttons] == [False, True, True, True]
    assert [offer(a, view) for a in buttons] == [True, False, False, False]  # Jev is offered the page's own only
    assert Tab.pick(view, mobile.lexicon(product), labels=[ADD], key="add_to_cart", near=control["rect"]) is buttons[0]

    assert mobile.value(TOP_JS) == 0
    clicked = mobile.click(product, purpose="add_to_cart", stage="pdp", labels=[ADD], key="add_to_cart",
                           rect=control["rect"], clear=True, offer=offer)
    assert (clicked["executed"], clicked["reason"]) == (True, None) and "stale_reobserved" not in clicked
    assert mobile.value("document.title") == "MAIN"  # the page's own add-to-cart, not a related product's


PAGE_RECT = ("(s => { const r = document.querySelector(s).getBoundingClientRect(); return {x: Math.round(r.left + "
             "scrollX), y: Math.round(r.top + scrollY), w: Math.round(r.width), h: Math.round(r.height)}; })(%s)")


def test_a_sticky_control_moves_with_the_window_and_that_move_is_its_shift(mobile, shop_server):
    """dx, dy are the scrolled-to element's page position now minus audit.js's rect, not an inner scroller's move
    alone: a checkout CTA stuck in a position: sticky bottom bar keeps its viewport place while scrollIntoView moves the
    window, so its page position moves with the window (dy = the window's move, via "element") although no inner
    scroller exists. That shift is what keeps the stuck CTA in the audit's frame: picking by nearness to its rect
    without it takes the in-flow link of the same label above it."""
    mobile.load(shop_server.url("shop/scroll-sticky.html"), stage="cart", page_id="mobile-cart-1")
    rect = mobile.value(PAGE_RECT % "'.bar a'")  # page coordinates at scroll 0, as audit.js measures them
    assert mobile.value("scrollY") == 0 and rect["y"] + rect["h"] / 2 < 844  # stuck at the viewport's bottom
    done = mobile.scroll_to(rect)
    assert (done["via"], done["in_view"], done["dx"]) == ("element", True, 0)
    assert done["scroll_y"] > 0 and done["dy"] == pytest.approx(done["scroll_y"], abs=2)  # the window's move
    assert mobile.value("[...document.querySelectorAll('body *')].every(e => !e.scrollTop && !e.scrollLeft)")
    page = mobile.observe()
    # the in-flow link (page y 648) above the bar (stuck at viewport y 789 with the window at 391: page y 1180)
    inline, stuck = sorted((a for a in page["actions"] if a["label"] == "Procedi all'acquisto"),
                           key=lambda a: a["rect"]["y"])
    assert stuck["rect"]["y"] == pytest.approx(rect["y"], abs=2)  # its viewport place did not change
    lx = mobile.lexicon(None)
    assert Tab.pick(page, lx, labels=["Procedi all'acquisto"], near=rect) is inline  # unshifted: the wrong one
    assert Tab.pick(shifted(page, done), lx, labels=["Procedi all'acquisto"], near=rect) is stuck
    assert _page_rect(shifted(page, done), stuck)["y"] == pytest.approx(rect["y"], abs=2)


def test_jevs_element_is_remembered_in_the_audits_frame():
    """The page rect click() keeps for Jev's element (the stale re-observe picks its label nearest to it) is taken
    in the frame pick() compares in: the observed y plus the window's scroll, the inner scroller's shift taken back.
    No shift (scroll_to read none, or only the window moved an element in the page flow): the observation is used as
    it is."""
    page = {"scroll": {"y": 0}, "actions": []}
    view = shifted(page, {"scroll_y": 0, "in_view": True, "via": "element", "dx": 0, "dy": -1009})
    assert view == {**page, "shift": {"x": 0, "y": -1009}} and _page_rect(view, {"rect": {"y": 398, "h": 48}}) == {
        "y": 1407, "h": 48}
    assert shifted(page, {"dx": 0, "dy": 0}) is page and shifted(page, None) is page


def test_the_stale_retry_scrolls_to_the_same_element_and_still_takes_the_shift_back(mobile, shop_server):
    """click()'s second attempt after a StalePage scrolls to the same audit.js rect again. Once main has moved, no
    element sits at that rect any more: SCROLL_JS reuses the element the first scroll matched on this document
    (window.__jevScrollTarget), so the retry says where the control really is (via "element", in view, the whole
    shift since the audit) and picks the page's own button again; the window fallback said "not in view" and, with
    no shift, picked the related product's button."""
    product, audit, control = shell_of(mobile, shop_server)
    first = mobile.scroll_to(control["rect"])
    assert mobile.scroll_to(control["rect"]) == first == {"scroll_y": 0, "in_view": True, "via": "element", "dx": 0,
                                                          "dy": -1009}
    assert mobile.value("document.querySelector('[data-scroll-target]')") is None  # no DOM attribute
    real, sent = mobile.browser.act, []

    def stale_once(action, page, text=None):  # nothing sent the first time, as Browser.act's freshness check
        sent.append(action["label"])
        if len(sent) == 1:
            raise StalePage("Page changed since this decision. Observe again.")
        return real(action, page, text=text)

    mobile.browser.act = stale_once
    clicked = mobile.click(product, purpose="add_to_cart", stage="pdp", labels=[ADD], key="add_to_cart",
                           rect=control["rect"], clear=True)
    assert (clicked["executed"], clicked["stale_reobserved"], sent) == (True, 1, [ADD, ADD])
    assert mobile.value("document.title") == "MAIN"
    mobile.value("location.reload()")  # a new document starts without the memo: the rect finds the element again
    deadline = time.monotonic() + 5.0
    while mobile.value("document.readyState") != "complete" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert mobile.value("window.__jevScrollTarget === undefined") is True
    assert mobile.scroll_to(control["rect"]) == first


# the page re-renders its add-to-cart before Browser.act's freshness check: a new node of the same size where the old
# one was (a clone: cloneNode copies no listener, so the clone gets the MAIN one), or the same node with a new label
CLONE_ADD = ("(() => { const a = document.getElementById('add'), b = a.cloneNode(true); a.replaceWith(b); "
             "b.addEventListener('click', () => { document.title = 'MAIN'; }); return true; })()")
RELABEL_ADD = ("(() => { const a = document.getElementById('add'); a.textContent = 'Aggiungi'; "
               "a.style.width = '200px'; return true; })()")


@pytest.mark.parametrize("render", [CLONE_ADD, RELABEL_ADD], ids=["new_node", "new_label"])
def test_the_stale_retry_finds_a_re_rendered_control_where_the_first_scroll_left_it(mobile, shop_server, render):
    """A StalePage often means the page re-rendered the control: a new node in its place, or the same node with
    another label and size. After main moved, the memo's node is gone (or no longer audit.js's size) and no element
    sits at audit.js's rect any more: the retry's scroll fell back to the window (via "window", not in view, no
    shift), picking compared the observation in the wrong frame and the first related product's button was added
    (title RELATED1). SCROLL_JS keeps the memo's node while it is connected, whatever its size, and else looks for
    audit.js's size where the first scroll left the control (the memo's shift): the retry scrolls the control
    itself, the whole shift since the audit is taken back and the page's own button is clicked."""
    product, audit, control = shell_of(mobile, shop_server)
    first = mobile.scroll_to(control["rect"])
    assert first == {"scroll_y": 0, "in_view": True, "via": "element", "dx": 0, "dy": -1009}
    real, sent = mobile.browser.act, []

    def stale_once(action, page, text=None):  # the control is re-rendered and the freshness check says so
        sent.append(action["label"])
        if len(sent) == 1:
            assert mobile.value(render) is True
            raise StalePage("Page changed since this decision. Observe again.")
        return real(action, page, text=text)

    mobile.browser.act = stale_once
    labels = [ADD, "Aggiungi"] if render == RELABEL_ADD else [ADD]
    clicked = mobile.click(product, purpose="add_to_cart", stage="pdp", labels=labels, key="add_to_cart",
                           rect=control["rect"], clear=True)
    assert (clicked["executed"], clicked["stale_reobserved"]) == (True, 1)
    assert mobile.value("document.title") == "MAIN"  # the page's own add-to-cart, not RELATED1
    again = mobile.scroll_to(control["rect"])  # what the retry's scroll read: the control, where it is
    assert {k: again[k] for k in ("in_view", "via", "dx")} == {"in_view": True, "via": "element", "dx": 0}
    assert again["dy"] == pytest.approx(-1009, abs=2) and again["scroll_y"] == 0


@pytest.mark.parametrize("query", ["", "?window"])
def test_the_audit_read_after_a_variant_pick_stays_in_the_load_audits_frame(mobile, shop_server, query):
    """An app shell whose size group (no option selected) sits below the first screen, with a loose add-on line
    "Calze tecniche abbinate 9,90 €" (in no card: a page price for audit.js) right below it. select_variant()'s
    scroll_to() moves main by about 1000 px to pick a size, and the audit read again after the pick (the price may
    change) takes the window's scroll back, not main's: every element of main read about 1000 px higher than at
    scroll 0, the add-on line "above the fold" and the product's own 49,90 € not, so the add-on became pdp.price and
    the add-to-cart probe's price (the base of PTI.FUNNEL_PRICE_DELTA: a false +404 % on a 49,90 € cart). The audit
    is now read with every element scroller back at 0, as on the same page scrolled by the window."""
    product = mobile.load(shop_server.url(f"shop/scroll-variant.html{query}"), stage="pdp", page_id="mobile-pdp-1")
    pdp = product["audit"]["pdp"]
    assert product["classification"]["type"] == "pdp"
    assert (pdp["price"]["value"], pdp["price"]["above_fold"]) == (49.9, True)
    [group] = [g for g in pdp["variant_groups"] if g.get("selected") is False]
    assert group["rect"]["y"] > 844 and group["first_available"] == "40"
    crawl = Crawl(mobile.collector, EngagementSettings(url=shop_server.url("shop/index.html")), profile="mobile",
                  guard=mobile.guard)
    crawl.tab = mobile
    crawl.add_to_cart(product)
    probe = product["probes"]["add_to_cart"]
    assert (probe["variant"]["label"], probe["variant"]["executed"], probe["executed"]) == ("40", True, True)
    assert (probe["price"], probe["title"]) == (49.9, "Scarpa da corsa Aurora")
    assert mobile.value("document.title") == "MAIN"
    assert mobile.value("document.querySelector('.sizes [aria-pressed=\"true\"]').textContent") == "40"


def test_a_link_lookup_asks_at_the_top_of_an_app_shell(mobile, shop_server):
    """Crawl.fallback_link asks Jev at the top of its page (TOP_JS). After scroll_to() moved main, resetting the
    window alone left main scrolled and the header link inside it ("Il mio carrello") out of the observation; TOP_JS
    resets every element scroller too, instantly."""
    _, _, control = shell_of(mobile, shop_server)
    assert observed(mobile, "Il mio carrello")
    mobile.scroll_to(control["rect"])
    assert not observed(mobile, "Il mio carrello")
    mobile.value(OLD_TOP_JS)
    assert not observed(mobile, "Il mio carrello")  # the window was at the top already
    assert mobile.value(TOP_JS) == 0
    assert mobile.value("document.querySelector('main').scrollTop") == 0 and observed(mobile, "Il mio carrello")


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
