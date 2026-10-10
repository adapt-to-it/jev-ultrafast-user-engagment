"""CheckoutGuard: forbidden labels, checkout boundary, protected fields, external pages (offline and on the fixture)."""

import pytest

from jev_ultrafast.browser import Browser
from jev_ultrafast.engagement.lexicon import lexicon_for, registrable_domain
from jev_ultrafast.engagement.profiles import new_context
from jev_ultrafast.engagement.safety import CheckoutGuard

START = "https://www.shop.example/it/"


@pytest.fixture
def guard():
    return CheckoutGuard(START, lexicon_for("it"))


def page(url=START + "prodotto/1", actions=(), text="", hrefs=None):
    """An observed page; hrefs maps node ids to the raw href snapshot.js reports in its guard tuple."""
    guards = {str(node): [node, "link", "", None, None, None, None, False, None, None, None, None, href, ""]
              for node, href in (hrefs or {}).items()}
    return {"url": url, "text": text, "actions": list(actions), "guards": guards}


def click(label, role="button", id="e1", node=1):
    return {"id": id, "node": node, "kind": "click", "role": role, "label": label}


def with_fields(observed, labels, scope="", kind="fill", first=11):
    """Add observed fill (or select) fields with these labels to a page; scope is the text of their form (snapshot.js
    guard tuple index 13)."""
    for node, label in enumerate(labels, first):
        observed["actions"].append({"id": f"e{node}", "node": node, "kind": kind, "role": "textbox", "label": label})
        observed["guards"][str(node)] = [node, "textbox", label, "", None, None, False, False, None, None, None, None,
                                         None, scope]
    return observed


@pytest.mark.parametrize("host, domain", [
    ("www.shop.example", "shop.example"), ("a.b.shop.co.uk", "shop.co.uk"), ("negozio.it", "negozio.it"),
    ("store.myshopify.com", "store.myshopify.com"), ("127.0.0.1", "127.0.0.1"), ("localhost", "localhost"),
    ("CDN.Shop.Example.", "shop.example"), ("", ""),
    # an unlisted ccTLD second level and shared hosts: each subdomain is another site
    ("shop.example.com.vn", "example.com.vn"), ("www.toko.co.id", "toko.co.id"), ("a.org.br", "a.org.br"),
    ("tramites.gob.mx", "tramites.gob.mx"), ("www.mionegozio.altervista.org", "mionegozio.altervista.org"),
    ("miobrand.webnode.it", "miobrand.webnode.it"), ("www.example.me", "example.me"), ("shop.co", "shop.co"),
])
def test_registrable_domain(host, domain):
    assert registrable_domain(host) == domain


@pytest.mark.parametrize("start, other", [
    ("https://mionegozio.altervista.org/", "https://altro-sito.altervista.org/"),
    ("https://miobrand.webnode.it/", "https://phishing.webnode.it/"),
    ("https://shop.example.com.vn/", "https://evil.com.vn/"), ("https://a.org.br/", "https://b.org.br/"),
])
def test_a_shared_suffix_is_not_one_site(start, other):
    guard = CheckoutGuard(start, lexicon_for("it"))
    assert not guard.same_site(other) and guard.same_site(start + "carrello")
    add = {"kind": "click", "role": "button", "label": "Aggiungi al carrello", "node": 1}
    assert guard.allowed_action(add, {"url": other + "p/1"}, "pdp") == (False, "external_page")


def test_same_site_compares_registrable_domains(guard):
    assert guard.same_site("https://shop.example/cart")
    assert guard.same_site("http://static.shop.example:8443/x.js")
    assert guard.same_site("about:blank")
    assert not guard.same_site("https://www.paypal.com/checkoutnow")
    assert not guard.same_site("https://shop.example.evil.test/")
    assert not guard.same_site("chrome-error://chromewebdata/")
    local = CheckoutGuard("http://127.0.0.1:8000/shop/index.html", lexicon_for("it"))
    assert local.same_site("http://127.0.0.1:9000/shop/cart.html")
    assert not local.same_site("http://127.0.0.2/")


@pytest.mark.parametrize("label", [
    "Paga ora", "PAGA ORA", "Paga", "Conferma e paga", "Conferma il pagamento", "Paga 49,90 €", "Paga con PayPal",
    "Conferma l'ordine", "Conferma l’ordine", "Conferma ordine", "Completa l'acquisto", "Completa acquisto",
    "Invia ordine", "Ordine con obbligo di pagamento", "Place order", "Place my order", "Pay now", "Pay €49.90",
    "Confirm payment", "Complete purchase", "Buy with Apple Pay", "Conferma il tuo ordine", "Effettua il tuo ordine",
    "Completa il tuo ordine", "Completa il tuo acquisto", "Invia il mio ordine", "Conferma il mio acquisto",
    "Conferma e acquista", "Confermo l'ordine", "Termina l'acquisto", "Finish order", "Finalize order",
    "Confirm and buy", "Buy with PayPal", "Buy with Shop Pay", "Acquista con PayPal", "Concludi ordine",
])
@pytest.mark.parametrize("page_type", ["pdp", "cart", "checkout", "other", None])
def test_pay_and_place_order_controls_are_refused_on_every_page(guard, label, page_type):
    ok, reason = guard.allowed_action(click(label), page(), page_type)
    assert not ok and reason.startswith("forbidden_label:")
    ok, reason = guard.allowed_action(click(label, role="link"), page(), page_type)
    assert not ok


@pytest.mark.parametrize("label, page_type", [
    ("Pagamenti sicuri", "home"), ("Metodi di pagamento", "other"), ("Pagamento sicuro garantito", "pdp"),
    ("Paga in 3 rate con Klarna", "pdp"), ("Acquista ora", "pdp"), ("Aggiungi al carrello", "pdp"),
    ("Procedi al pagamento", "cart"), ("Procedi al checkout", "cart"), ("Il mio ordine", "home"),
    ("Riepilogo ordine", "cart"), ("Accetta tutti", "home"), ("Rifiuta tutti", "home"),
    ("Paga con Klarna in 3 rate", "pdp"), ("Paga 3 rate da 16,63 €", "pdp"), ("Il tuo ordine", "cart"),
    ("Pay in 3 interest-free installments", "pdp"),
])
def test_informational_and_funnel_controls_stay_allowed(guard, label, page_type):
    assert guard.allowed_action(click(label, role="link"), page(), page_type) == (True, None)
    assert guard.allowed_action(click(label), page(), page_type) == (True, None)


def test_checkout_page_allows_only_scrolling_waiting_and_leaving(guard):
    checkout = page(START + "checkout", hrefs={1: "../carrello", 2: "/it/termini.html", 3: "#", 4: "javascript:void(0)",
                                               5: "checkout?step=2", 6: "/it/checkout/pagamento",
                                               7: "https://www.paypal.com/help"})
    assert guard.allowed_action(click("Torna al carrello", role="link"), checkout, "checkout") == (True, None)
    assert guard.allowed_action(click("Termini e condizioni di vendita", role="link", node=2), checkout, "checkout")[0]
    assert guard.allowed_action(click("Aiuto", role="link", node=7), checkout, "checkout") == (True, None)
    for label in ("Acquista", "Ordina", "Compra", "Purchase", "Concludi"):  # link-shaped submits lead nowhere
        for node in (3, 4, 5):
            assert guard.allowed_action(click(label, role="link", node=node), checkout, "checkout") == (
                False, "checkout_link_not_navigation"), (label, node)
    assert guard.allowed_action(click("Pagamento", role="link", node=6), checkout, "checkout") == (
        False, "checkout_submit")  # the next checkout step is not a way out
    assert guard.allowed_action(click("Torna al carrello", role="link", node=9), checkout, "checkout") == (
        False, "checkout_link_unverified")  # no observed href
    assert guard.allowed_action(click("Completa il tuo ordine", role="link"), checkout, "checkout")[0] is False
    assert guard.allowed_action(click("Il tuo ordine: dati di spedizione", role="link"), checkout, "checkout") == (
        False, "checkout_submit")
    assert guard.allowed_action({"id": "scroll_down", "kind": "scroll"}, checkout, "checkout") == (True, None)
    assert guard.allowed_action({"id": "wait", "kind": "wait"}, checkout, "checkout") == (True, None)
    assert guard.allowed_action(click("Continua"), checkout, "checkout") == (False, "checkout_submit")
    assert guard.allowed_action(click("Continua", role="link"), checkout, "checkout")[0] is False
    assert guard.allowed_action(click("Hai già un account? Accedi", role="link"), checkout, "checkout")[0] is False
    assert guard.allowed_action(click("Spedizione express", role="radio"), checkout, "checkout")[0] is False
    fill = {"id": "e3", "node": 3, "kind": "fill", "role": "textbox", "label": "Note"}
    assert guard.allowed_action(fill, checkout, "checkout") == (False, "checkout_form")
    select = {"id": "e4", "node": 4, "kind": "select", "role": "combobox", "label": "Paese → Italia", "value": "IT"}
    assert guard.allowed_action(select, checkout, "checkout") == (False, "checkout_form")


CART = START + "carrello/"


def entry(label="Concludi ordine", href="/it/pagamento/", role="link", kind="click", url=CART, fields=()):
    action = {"id": "e1", "node": 1, "kind": kind, "role": role, "label": label}
    return action, with_fields(page(url, [action], hrefs={1: href} if href is not None else None), fields)


def test_a_place_order_label_on_the_carts_link_into_the_checkout_is_the_checkout_entry(guard):
    """Legacy WooCommerce Italian: "Concludi ordine" is the cart's "Proceed to checkout" link (architect ruling 8)."""
    assert guard.allowed_action(*entry(), "cart") == (True, None)
    assert guard.allowed_action(*entry(href="https://shop.example/checkout"), "cart") == (True, None)  # same site
    assert guard.allowed_action(*entry(label="Concludi l'ordine", href="../checkout/"), "cart") == (True, None)
    refused = [
        entry(href="#"), entry(href="javascript:void(0)"), entry(href=None),  # link-shaped submits, no href
        entry(href="/it/carrello/?x=1"),  # not a checkout URL
        entry(href="https://pay.other.example/pagamento/"),  # another site
        entry(role="button"), entry(kind="select"),  # a control, not a link
        entry(label="Paga ora"), entry(label="Conferma e paga"),  # paying is never an entry
        entry(url=START + "pagamento/", href="/it/pagamento/step-2"),  # already on a checkout page
        entry(fields=["Numero della carta", "CVV"]),  # a one-page checkout that looks like a cart
        entry(href="/it/pagamento/", url=START + "pagamento/"),  # the same path
    ]
    for action, observed in refused:
        assert guard.allowed_action(action, observed, "cart")[0] is False, (action, observed["guards"])
    assert guard.allowed_action(*entry(), "checkout")[0] is False
    assert guard.allowed_action(*entry(), "cart") == (True, None)


def test_checkout_urls_and_card_fields_make_a_checkout_page(guard):
    assert guard.is_checkout(page(START + "checkout/step-1"), None)
    assert guard.is_checkout(with_fields(page(START + "x"), ["Numero della carta", "CVV", "Scadenza"]), "other")
    assert guard.is_checkout(with_fields(page(START + "x"), ["Numero carta", "Titolare", "Scadenza"]), "pdp")
    assert guard.is_checkout(with_fields(page(START + "x"), ["CVC"]), "pdp")  # a field labelled CVC is one
    assert guard.is_checkout(with_fields(page(START + "x"), ["MM/AA"], kind="select",
                                         scope="Carta di credito Numero della carta Scadenza CVV"), "pdp")
    assert not guard.is_checkout(with_fields(page(START + "x"), ["Data di scadenza dell'offerta: 31/10"]), None)
    assert guard.is_checkout(page(START + "checkout/step-1"), "cart")  # a one-page checkout that looks like a cart
    assert guard.is_checkout(page(START + "cassa"), "cart") and guard.is_checkout(page(START + "checkout/"), "cart")
    assert not guard.is_checkout(page("https://shop.example/checkout/cart/"), "cart")  # Magento's cart
    assert not guard.is_checkout(page("https://shop.example/checkout/cart"), None)  # Shopware's cart
    assert guard.is_checkout(with_fields(page(START + "carrello"), ["Codice sconto"],
                                         scope="Titolare della carta Codice di sicurezza CVC"), "cart")
    # OpenCart routes in the query string; payment information pages are content, not checkout steps.
    assert guard.is_checkout(page("https://shop.example/index.php?route=checkout/checkout"), "home")
    assert guard.should_stop("home", page("https://shop.example/index.php?route=checkout/checkout"))
    assert not guard.is_checkout(page("https://shop.example/index.php?route=checkout/cart"), "cart")
    for info in ("content/pagamento.html", "pages/payment.html", "help/payment"):
        assert not guard.is_checkout(page("https://shop.example/" + info), "other"), info


def test_privacy_banners_and_best_before_dates_are_not_payment_forms(guard):
    pdp = with_fields(page(START + "prodotto/1", [click("Aggiungi al carrello"), click("Accetta tutti", id="e2")]),
                      ["Quantità"], scope="Mozzarella di bufala 250 g Scadenza: 12/10/2026 Quantità Aggiungi")
    pdp["text"] = ("Utilizziamo i cookie. Il titolare del trattamento è Rossi S.r.l.\nAccetta tutti\n"
                   "Aggiungi al carrello")
    assert not guard.is_checkout(pdp, "pdp")
    assert [a["id"] for a in guard.filter_actions(pdp, "pdp")[0]] == ["e1", "e2", "e11"]
    footer = with_fields(page(START + "prodotto/1"), ["La tua email"],
                         scope="Bonifico bancario IBAN IT60X0542811101000000123456 Scadenza 31/12 Iscriviti")
    assert not guard.is_checkout(footer, "pdp")


def test_the_viewport_text_is_never_checkout_evidence(guard):
    """Architect ruling 5: snapshot.js text is the viewport only; a description or a footer may name a CVV."""
    pdp = with_fields(page(START + "prodotto/felpa", [click("Aggiungi al carrello")]), ["Quantità"],
                      scope="Felpa in tessuto CVC (cotone e poliestere) Quantità Aggiungi al carrello")
    pdp["text"] = ("Felpa in tessuto CVC\nNon ti chiederemo mai il CVV né il numero della carta\n"
                   "Codice di sicurezza del capo: CSC-12\nAggiungi al carrello")
    assert not guard.is_checkout(pdp, "pdp") and not guard.should_stop("pdp", pdp)
    assert guard.allowed_action(click("Aggiungi al carrello"), pdp, "pdp") == (True, None)
    gift = page(START + "prodotto/carta-regalo", [click("Aggiungi al carrello")],
                text="Carta regalo: il numero della carta e il codice di sicurezza arrivano via email")
    assert not guard.is_checkout(gift, "pdp") and guard.allowed_action(click("Aggiungi al carrello"), gift, "pdp")[0]


def test_external_pages_allow_nothing_but_scrolling(guard):
    external = page("https://www.paypal.com/checkoutnow", [click("Accedi"), {"id": "wait", "kind": "wait"}])
    allowed, notes = guard.filter_actions(external, None)
    assert [a["id"] for a in allowed] == ["wait"]
    assert any("www.paypal.com" in note for note in notes)


def test_filter_actions_keeps_allowed_actions_in_order_and_explains_refusals(guard):
    actions = [click("Aggiungi al carrello", id="e1"), click("Paga ora", id="e2"),
               {"id": "e3", "node": 3, "kind": "fill", "role": "textbox", "label": "Email"},
               {"id": "e4", "node": 4, "kind": "fill", "role": "searchbox", "label": "Cerca prodotti"}]
    allowed, notes = guard.filter_actions(page(actions=actions), "pdp")
    assert [a["id"] for a in allowed] == ["e1", "e4"]
    assert notes == ["e2 'Paga ora': forbidden_label:pay_now", "e3 'Email': personal_field"]


def test_the_guard_refuses_forbidden_labels_in_every_language():
    """Architect ruling 6: the guard joins the run's lexicon with every language's, so a run's locale never lets a
    pay or place-order control through."""
    english = CheckoutGuard(START, lexicon_for("en"))
    for label in ("Paga ora", "Conferma l'ordine", "Concludi ordine", "Place order"):
        assert english.allowed_action(click(label), page(), "pdp")[0] is False, label
    assert english.is_checkout(page(START + "cassa/"), None)
    assert english.allow_text(FieldBrowser(field(label="Codice fiscale")), FILL) == (False, "personal_field")


def test_stop_at_the_first_checkout_page_or_the_cart():
    assert CheckoutGuard(START, lexicon_for("it")).should_stop("checkout")
    assert not CheckoutGuard(START, lexicon_for("it")).should_stop("cart")
    one_page = page(START + "checkout/")  # its URL, whatever the classifier says
    assert CheckoutGuard(START, lexicon_for("it")).should_stop("cart", one_page)
    assert not CheckoutGuard(START, lexicon_for("it")).should_stop("cart", page(START + "carrello"))
    cart = CheckoutGuard(START, lexicon_for("it"), stop_at="cart")
    assert cart.should_stop("cart") and cart.should_stop("checkout") and not cart.should_stop("pdp")
    with pytest.raises(ValueError):
        CheckoutGuard(START, lexicon_for("it"), stop_at="payment")


class FieldBrowser:
    """Answers the guard's single read of the observed field."""

    def __init__(self, info):
        self.info, self.calls = info, []

    def evaluate(self, expression):
        self.calls.append(expression)
        return self.info


def field(**info):
    base = {"tag": "input", "type": "text", "autocomplete": "", "name": "", "inputmode": "", "id": "",
            "placeholder": "", "aria": "",
            "label": "", "role": "", "in_search": False, "form_action": "", "form_password": False,
            "form_payment": False}
    return {**base, **info}


FILL = {"id": "e5", "node": 5, "kind": "fill", "role": "textbox", "label": "campo"}


@pytest.mark.parametrize("info, reason", [
    (field(type="password", name="pwd"), "protected_field:password"),
    (field(type="email", name="newsletter"), "protected_field:email"),
    (field(type="tel"), "protected_field:tel"),
    (field(autocomplete="cc-number"), "payment_field"),
    (field(autocomplete="section-ship shipping given-name"), "personal_field:given-name"),
    (field(autocomplete="postal-code"), "personal_field:postal-code"),
    (field(name="codice_fiscale"), "personal_field"),
    (field(label="IBAN"), "payment_field"),
    (field(name="card-number"), "payment_field"),
    (field(label="Numero della carta"), "payment_field"),
    (field(label="Indirizzo"), "personal_field"),
    (field(placeholder="Il tuo nome"), "personal_field"),
    (field(name="coupon", form_password=True), "account_or_payment_form"),
    (field(name="f1", placeholder="mario.rossi@example.com"), "personal_field"),
    (field(name="f2", inputmode="tel"), "personal_field"),
    (field(type="search", autocomplete="email"), "personal_field:email"),
    (field(name="FNAME", placeholder="Mario"), "personal_field"),
    (field(name="LNAME", placeholder="Rossi"), "personal_field"),
    (field(name="name"), "personal_field"),
    (field(name="billing_address_1"), "personal_field"),
    (field(label="Post code"), "personal_field"),
    (field(label="Town"), "personal_field"),
    (field(label="Telephone"), "personal_field"),
    (field(label="Surname"), "personal_field"),
    (field(name="order_email", label="Cerca il tuo ordine"), "personal_field"),
    (field(name="name", label="Come ti chiami?"), "personal_field"),
    (field(label="Posta elettronica"), "personal_field"),
    (field(label="Via e numero"), "personal_field"),
    (field(label="Località"), "personal_field"),
    (field(label="Comune di residenza"), "personal_field"),
    (field(label="Recapito telefonico"), "personal_field"),
    (field(name="EMAIL_ADDRESS"), "personal_field"),
    (field(placeholder="Iscriviti alla newsletter"), "personal_field:newsletter"),
    (field(name="f3", form_text="newsletter-signup"), "personal_field:newsletter"),
    (field(tag="textarea", name="review", label="La tua opinione"), "field_not_allowed"),
    (field(tag="textarea", name="note", label="Note per il corriere"), "field_not_allowed"),
    (field(name="f4"), "field_not_allowed"),
    (field(type="number", name="f5"), "field_not_allowed"),  # an unlabelled number may be a postal code
    (field(label="Scrivi un messaggio"), "field_not_allowed"),
    # weak search markup (a short name, a search form action) does not outweigh personal words in the label
    (field(name="k", form_action="/cerca", label="Il tuo nome"), "personal_field"),
    # a search box by its markup that asks for contact data
    (field(name="q", label="Cerca il tuo ordine (email)"), "personal_field"),
    (field(name="search", placeholder="Cerca per nome o indirizzo email"), "personal_field"),
    (field(type="search", placeholder="Search by phone number"), "personal_field"),
])
def test_typing_into_protected_fields_is_refused(guard, info, reason):
    assert guard.allow_text(FieldBrowser(info), FILL) == (False, reason)


@pytest.mark.parametrize("info", [
    field(type="search", name="q"), field(name="q"), field(role="searchbox"), field(in_search=True, name="term"),
    field(type="search", placeholder="Cerca per nome o modello"), field(placeholder="Cerca un prodotto"),
    field(name="s", autocomplete="off"), field(name="coupon", label="Codice sconto"),
    field(name="dwfrm_cart_couponCode", placeholder="Inserisci il codice promozionale"),
    field(label="Gift card or discount code"), field(type="number", name="qty_1", label="Quantità"),
    field(type="number", name="cart[items][0][qty]"), field(inputmode="numeric", aria="Quantity"),
])
def test_typing_is_allowed_only_into_search_quantity_and_coupon_fields(guard, info):
    assert guard.allow_text(FieldBrowser(info), FILL) == (True, None)


def test_allow_text_refuses_without_a_field_or_on_checkout(guard):
    assert guard.allow_text(FieldBrowser(field(type="search")), FILL, "checkout") == (False, "checkout_form")
    assert guard.allow_text(FieldBrowser(None), FILL) == (False, "node_not_found")
    assert guard.allow_text(FieldBrowser(field()), {**FILL, "kind": "click"}) == (False, "not_a_text_field")
    assert guard.allow_text(FieldBrowser(field()), {**FILL, "node": "document.body"}) == (False, "invalid_node")
    browser = FieldBrowser(field(type="search"))
    guard.allow_text(browser, FILL)
    assert browser.calls[0].endswith("})(5)")  # the observed node id, never a selector


# ---------------------------------------------------------------- the fixture shop in Chromium

TALL = {"width": 1280, "height": 3000, "deviceScaleFactor": 1, "mobile": False}  # every control is observable


def observe(transport, url):
    context = new_context(transport)
    browser = Browser(url, transport=transport, browser_context_id=context, background=False, metrics=TALL)
    return browser, browser.observe(screenshot=False)


def test_guard_on_the_fixture_checkout(shop_server, transport):
    since = len(shop_server.requests)  # the log is session-wide: earlier tests' requests (a beacon POST) are not ours
    url = shop_server.url("shop/checkout.html")
    guard = CheckoutGuard(shop_server.url("shop/index.html"), lexicon_for("it"))
    browser, state = observe(transport, url)
    try:
        labels = {a["label"]: a for a in state["actions"]}
        assert "Paga ora" in labels and any(a["kind"] == "fill" for a in state["actions"])
        allowed, notes = guard.filter_actions(state, "checkout")
        kept = {a["label"] for a in allowed}
        assert {"Torna al carrello", "Termini e condizioni di vendita"} <= kept
        assert "Paga ora" not in kept and "Acquista" not in kept and "Accedi" not in kept  # a link that submits
        assert any("'Acquista': checkout_link_not_navigation" in note for note in notes)
        assert not [a for a in allowed if a["kind"] in ("fill", "select")]
        assert any("forbidden_label:pay_now" in note for note in notes)
        email = next(a for a in state["actions"] if a["kind"] == "fill" and a["label"] == "Email")
        assert guard.allow_text(browser, email) == (False, "protected_field:email")
        name = next(a for a in state["actions"] if a["kind"] == "fill" and a["label"] == "Nome")
        assert guard.allow_text(browser, name) == (False, "personal_field:given-name")
        fiscal = next(a for a in state["actions"] if a["kind"] == "fill" and a["label"] == "Codice fiscale")
        assert guard.allow_text(browser, fiscal) == (False, "personal_field")
        assert guard.should_stop("checkout")
    finally:
        browser.close()
    assert not [r for r in shop_server.requests[since:] if r["method"] == "POST"]


def test_guard_on_the_fixture_cart_allows_only_the_quantity_field(shop_server, transport):
    guard = CheckoutGuard(shop_server.url("shop/index.html"), lexicon_for("it"))
    browser, _ = observe(transport, shop_server.url("shop/index.html"))
    try:
        browser.evaluate("localStorage.setItem('ps-cart', JSON.stringify([{id: 1, name: 'Scarpa da corsa Aurora', "
                         "price: 49.9, size: '42', qty: 1}]))")
        browser.navigate(shop_server.url("shop/cart.html"))
        state = browser.observe(screenshot=False)
        quantity = next(a for a in state["actions"] if a["kind"] == "fill" and a["label"] == "Quantità")
        assert not guard.is_checkout(state, "cart") and not guard.should_stop("cart", state)
        assert guard.allowed_action(quantity, state, "cart") == (True, None)
        assert guard.allow_text(browser, quantity, "cart") == (True, None)
        checkout = next(a for a in state["actions"] if a["label"] == "Procedi al checkout")
        assert guard.allowed_action(checkout, state, "cart") == (True, None)
    finally:
        browser.close()


def test_guard_on_the_fixture_home_allows_search_and_information(shop_server, transport):
    guard = CheckoutGuard(shop_server.url("shop/index.html"), lexicon_for("it"))
    browser, state = observe(transport, shop_server.url("shop/index.html"))
    try:
        allowed, notes = guard.filter_actions(state, "home")
        kept = {a["label"] for a in allowed}
        assert {"Accetta tutti", "Rifiuta tutti", "Scopri la collezione"} <= kept
        assert notes == []
        search = next(a for a in allowed if a["kind"] == "fill")
        assert guard.allow_text(browser, search, "home") == (True, None)
        browser.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        footer = browser.observe(screenshot=False)
        allowed, notes = guard.filter_actions(footer, "home")
        assert "Pagamenti sicuri" in {a["label"] for a in allowed} and notes == []
    finally:
        browser.close()


WOO_CART = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Carrello – Bottega Rossi</title></head>
<body><main><h1>Carrello</h1><table><tr><td><a href="/prodotto/borsa-luna/">Borsa Luna</a></td><td>129,00 €</td></tr>
</table><p>Totale 129,00 €</p>
<a href="/pagamento/" class="checkout-button" style="display:inline-block;padding:14px 30px">Concludi ordine</a>
<a href="#" style="display:inline-block;padding:14px 30px">Concludi l'ordine</a>
<button type="button">Conferma l'ordine</button></main></body></html>"""


def test_guard_allows_the_observed_checkout_entry_link_of_a_woocommerce_cart(shop_server, transport):
    guard = CheckoutGuard(shop_server.url("shop/index.html"), lexicon_for("it"))
    browser, _ = observe(transport, shop_server.url("shop/resi.html"))
    try:
        browser.call("Page.setDocumentContent", frameId=browser.target, html=WOO_CART)
        browser.evaluate("history.replaceState(null, '', '/carrello/')")
        state = browser.observe(screenshot=False)
        allowed, notes = guard.filter_actions(state, "cart")
        kept = {a["label"] for a in allowed}
        assert "Concludi ordine" in kept  # its observed href is the checkout: the way in
        assert "Concludi l'ordine" not in kept and "Conferma l'ordine" not in kept  # "#" link and a button
        assert sum("forbidden_label:place_order" in note for note in notes) == 2
    finally:
        browser.close()
