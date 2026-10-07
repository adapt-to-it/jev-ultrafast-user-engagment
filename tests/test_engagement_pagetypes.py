"""Page-type classification: synthetic audit payloads (offline) and every fixture page in Chromium."""

import pytest

from jev_ultrafast.engagement.collectors import PageCollector
from jev_ultrafast.engagement.pagetypes import _home_path, classify
from jev_ultrafast.engagement.profiles import close_context, new_context
from jev_ultrafast.engagement.schemas import PAGE_TYPES
from jev_ultrafast.engagement.transport import DirectTransport

CART_ITEMS = ("localStorage.setItem('ps-cart', JSON.stringify([{id: 1, name: 'Scarpa da corsa Aurora', price: 49.9, "
              "size: '42', qty: 1}, {id: 7, name: 'Scarpa da corsa Onda', price: 44.9, size: '41', qty: 2}]))")


def test_an_empty_payload_is_other():
    result = classify({}, "https://shop.example/qualcosa")
    assert result["type"] == "other" and set(result["scores"]) == set(PAGE_TYPES)
    assert 0 <= result["confidence"] <= 1


@pytest.mark.parametrize("path, home", [
    ("/", True), ("", True), ("/it/", True), ("/en-gb", True), ("/index.html", True), ("/shop/index.html", True),
    ("/it/home.php", True), ("/scarpe/", False), ("/prodotto/scarpa-1", False), ("/shop/category.html", False),
    ("/us/", True), ("/tv", False), ("/pc", False), ("/tv/", False),  # a store country, not a two-letter category
])
def test_home_paths(path, home):
    assert _home_path(path) is home


@pytest.mark.parametrize("path, query, home", [
    ("/index.php", "route=checkout/checkout", False), ("/index.php", "route=product/category&path=20", False),
    ("/index.php", "route=common/home", True), ("/", "utm_source=newsletter&gclid=x", True), ("/", "s=scarpe", False),
    ("/", "page_id=12", False),
])
def test_a_query_that_routes_elsewhere_is_not_the_home_page(path, query, home):
    assert _home_path(path, query) is home


def test_checkout_routes_in_the_query_string():
    """OpenCart: every page is index.php, the route is in the query."""
    step = {"doc": {"h1s": ["Checkout"], "element_count": 400}, "nav": {"links": 30, "categories": [{}, {}, {}]},
            "forms": {"visible": 5, "password_present": True}}
    result = classify(step, "https://shop.example/index.php?route=checkout/checkout")
    assert result["type"] == "checkout" and result["scores"]["home"] == 0
    cart = {"cart": {"line_items": [{"title": "MacBook", "qty_editable": True}], "total_text": "Total: $602.00"},
            "doc": {"h1s": ["Shopping Cart"]}}
    result = classify(cart, "https://shop.example/index.php?route=checkout/cart")
    assert result["type"] == "cart" and "checkout:checkout URL pattern" not in result["signals"]
    listing = classify({"products": {"main_group": 12, "cards_count": 12}},
                       "https://shop.example/index.php?route=product/category&path=20")
    assert listing["type"] == "plp" and listing["scores"]["home"] == 0


@pytest.mark.parametrize("url", ["https://shop.example/content/pagamento.html", "https://shop.example/pages/payment",
                                 "https://shop.example/help/payment", "https://shop.example/checkout/cart/"])
def test_payment_information_pages_and_carts_are_not_checkout_urls(url):
    assert "checkout:checkout URL pattern" not in classify({}, url)["signals"]


def test_a_checkout_url_alone_is_not_a_checkout_page():
    """"/pagamento.html" may be a page about payment methods: the URL needs a form, a guest choice, a checkout heading
    or a pay control next to it. Order tracking and account order pages are no checkout URLs at all."""
    info = classify({"doc": {"h1s": ["Metodi di pagamento"], "element_count": 500}, "nav": {"links": 40}},
                    "https://shop.example/pagamento.html")
    assert info["type"] == "other" and "checkout:checkout URL pattern alone" in info["signals"]
    gate = classify({"doc": {"h1s": ["Accedi"]}, "forms": {"visible": 2, "password_present": True}},
                    "https://shop.example/pagamento.html")
    assert gate["type"] == "checkout" and "checkout:checkout URL pattern" in gate["signals"]
    for url in ("https://shop.example/ordine/stato?id=12", "https://shop.example/my-account/orders/1234",
                "https://shop.example/order-tracking"):
        assert not [s for s in classify({"forms": {"visible": 2}}, url)["signals"] if "URL" in s], url


def test_a_contact_form_is_not_a_checkout():
    contact = {"doc": {"h1s": ["Contattaci"], "element_count": 400}, "nav": {"links": 30},
               "forms": {"address_fields": 4, "visible": 5}}
    assert classify(contact, "https://shop.example/contatti")["type"] == "other"
    english = {**contact, "doc": {"h1s": ["Contact us"]}}
    assert classify(english, "https://shop.example/pages/contact")["type"] == "other"
    shipping = {**contact, "doc": {"h1s": ["Dati di spedizione"]}}
    assert classify(shipping, "https://shop.example/checkout/")["type"] == "checkout"


def test_the_effective_language_picks_the_lexicon():
    """The collector passes the page's effective language; without it the declared lang decides, and a language the
    lexicon lacks falls back to Italian (the run default)."""
    cart = {"doc": {"h1s": ["Carrello"]}, "cart": {"empty": True}}
    assert classify({**cart, "lang": "de"}, "https://shop.example/x")["type"] == "cart"
    italian_url = {"doc": {"h1s": ["Shopping"]}, "forms": {"visible": 3, "address_fields": 3}}
    italian = classify(italian_url, "https://shop.example/cassa/", locale="it")
    assert "checkout:checkout URL pattern" in italian["signals"]
    english = classify({**italian_url, "lexicon_lang": "en"}, "https://shop.example/cassa/")
    assert "checkout:checkout URL pattern" not in english["signals"]


def test_microdata_product_with_a_related_products_grid_is_a_product_page():
    audit = {"meta": {"microdata_products": 1, "microdata_main": 1, "jsonld_types": {}},
             "doc": {"h1s": ["Borsa Luna in pelle"], "element_count": 600}, "nav": {"links": 40},
             "pdp": {"add_to_cart": {"present": True, "above_fold": True}, "add_to_cart_count": 1,
                     "price": {"above_fold": True}, "variant_selector": "select"},
             "products": {"main_group": 12, "cards_count": 12}}
    result = classify(audit, "https://shop.example/borsa-luna.html")
    assert result["type"] == "pdp" and "pdp:microdata Product" in result["signals"]
    listing = {**audit, "meta": {"microdata_products": 24, "microdata_main": 0}, "pdp": {"add_to_cart_count": 24}}
    assert classify(listing, "https://shop.example/borse.html")["type"] == "plp"  # microdata on every card


def test_structured_product_with_an_add_to_cart_is_a_product_page():
    audit = {"meta": {"og_type": "product", "jsonld_types": {"Product": 1, "Offer": 1}},
             "doc": {"h1s": ["Scarpa"], "element_count": 900}, "nav": {"links": 40},
             "pdp": {"structured": {"price": 49.9}, "add_to_cart": {"present": True, "above_fold": True},
                     "add_to_cart_count": 1, "price": {"above_fold": True}, "variant_selector": "buttons"}}
    result = classify(audit, "https://shop.example/p/scarpa-aurora")
    assert result["type"] == "pdp" and result["confidence"] > 0.7
    assert "pdp:JSON-LD Product" in result["signals"]


def test_a_grid_of_cards_with_filters_is_a_listing_even_with_product_jsonld():
    audit = {"meta": {"jsonld_types": {"Product": 12}}, "products": {"main_group": 24, "cards_count": 24},
             "filters": {"controls": 4, "sort": {"present": True}, "result_count_text": "24 prodotti"}}
    assert classify(audit, "https://shop.example/scarpe")["type"] == "plp"


def test_cart_signals_and_an_empty_cart():
    cart = {"cart": {"line_items": [{"title": "Scarpa", "qty_editable": True}], "total_text": "Totale 49,90 €",
                     "checkout_cta": {"present": True}}, "doc": {"h1s": ["Il tuo carrello"]}}
    assert classify(cart, "https://shop.example/carrello")["type"] == "cart"
    empty = {"cart": {"line_items": [], "empty": True}, "doc": {"h1s": ["Carrello"]}}
    assert classify(empty, "https://shop.example/cart")["type"] == "cart"


def test_address_and_payment_fields_are_a_checkout():
    audit = {"forms": {"address_fields": 8, "visible": 12, "cc_present": True}, "doc": {"h1s": ["Dati di spedizione"]},
             "ctas": [{"lexicon_hit": "place_order"}], "cart": {"total_text": "Totale 49,90 €"}}
    result = classify(audit, "https://shop.example/checkout/step-1")
    assert result["type"] == "checkout" and result["scores"]["checkout"] > result["scores"]["cart"]


def test_challenge_wording_on_a_tiny_document_is_a_challenge():
    audit = {"title": "Just a moment...", "doc": {"h1s": [], "text_sample": "Checking your browser before accessing",
                                                  "element_count": 12}, "nav": {"links": 0}}
    assert classify(audit, "https://shop.example/")["type"] == "challenge"
    framed = {"doc": {"frames": ["www.google.com/recaptcha"], "element_count": 2000}, "nav": {"links": 80}}
    assert classify(framed, "https://shop.example/x")["scores"]["challenge"] >= 0.4


def test_root_path_with_featured_cards_stays_home():
    audit = {"products": {"main_group": 4, "cards_count": 4}, "nav": {"categories": [{}, {}, {}, {}]},
             "meta": {"jsonld_types": {"WebSite": 1}}}
    assert classify(audit, "https://shop.example/it/")["type"] == "home"


def test_english_pages_use_the_merged_lexicon():
    audit = {"lang": "en", "cart": {"empty": True}, "doc": {"h1s": ["Your bag"]}}
    assert classify(audit, "https://shop.example/bag")["type"] == "cart"


# ---------------------------------------------------------------- every fixture page in Chromium

@pytest.fixture(scope="module")
def records(chromium, shop_server):
    """Desktop PageRecords of the fixture shop, loaded in one isolated context (short settle windows)."""
    transport = DirectTransport(chromium.ws_url)
    context = new_context(transport)
    collector = PageCollector(transport, None, None, profile="desktop", screenshots=False, context_id=context,
                              settle_timeout_s=10)
    collector.net_quiet_s = collector.lcp_quiet_s = 0.3
    pages = ["index.html", "category.html", "product.html", "product.html?id=7", "product-dark.html", "cart.html",
             "cart.html?seeded", "cart.html?dark=1", "checkout.html", "checkout.html?guest=1", "challenge.html",
             "resi.html"]
    out = {}
    browser = collector.open(shop_server.url("shop/index.html"))
    try:
        for i, name in enumerate(pages):
            if name == "cart.html?seeded":
                browser.evaluate(CART_ITEMS)
            out[name] = collector.load_page(browser, shop_server.url("shop/" + name), stage="x", page_id=f"p{i}")
    finally:
        browser.close()
        close_context(transport, context)
        transport.close()
    return out


@pytest.mark.parametrize("name, expected", [
    ("index.html", "home"), ("category.html", "plp"), ("product.html", "pdp"), ("product.html?id=7", "pdp"),
    ("product-dark.html", "pdp"), ("cart.html", "cart"), ("cart.html?seeded", "cart"), ("cart.html?dark=1", "cart"),
    ("checkout.html", "checkout"), ("checkout.html?guest=1", "checkout"), ("challenge.html", "challenge"),
    ("resi.html", "other"),
])
def test_fixture_pages_are_classified(records, name, expected):
    result = records[name]["classification"]
    assert result["type"] == expected, result
    if expected != "other":
        assert result["confidence"] >= 0.4
        assert result["signals"]
