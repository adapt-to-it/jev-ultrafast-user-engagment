"""Collectors: lexicon portability, pure summaries, and PageCollector on the fixture shop in headless Chromium."""

import io
import json
import math
import re
import shutil
import socket
import subprocess
import threading
import time

import pytest
from conftest import FIXTURES
from PIL import Image, ImageDraw

from jev_ultrafast.engagement.collectors import (
    AUDIT_JS,
    VITALS_JS,
    PageCollector,
    _document_start,
    document_events,
    network_summary,
    page_errors,
    resource_summary,
    visual_metrics,
)
from jev_ultrafast.engagement.lexicon import LEXICON, compile_lexicon, effective_language, lexicon_all, lexicon_for
from jev_ultrafast.engagement.pagetypes import classify
from jev_ultrafast.engagement.profiles import close_context, new_context
from jev_ultrafast.engagement.schemas import SNIPPET_KINDS, AuditPayload
from jev_ultrafast.engagement.store import RunStore
from jev_ultrafast.engagement.transport import DirectTransport

CONTRACT_KEYS = (
    "add_to_cart buy_now checkout cart search filters sort result_count load_more shipping free_shipping returns "
    "delivery vat lowest_price_30d scarcity urgency reciprocity authority contact legal_id payment_brands pay_now "
    "place_order guest login register consent_accept consent_reject consent_manage newsletter subscription "
    "generic_link decline category_url product_url cart_url checkout_url").split()
CART_ITEMS = ("localStorage.setItem('ps-cart', JSON.stringify([{id: 1, name: 'Scarpa da corsa Aurora', price: 49.9, "
              "size: '42', qty: 1}, {id: 7, name: 'Scarpa da corsa Onda', price: 44.9, size: '41', qty: 2}]))")


# ---------------------------------------------------------------- lexicon

def test_lexicon_has_every_contract_key_in_both_languages():
    for language in ("it", "en"):
        missing = [k for k in CONTRACT_KEYS if not LEXICON[language].get(k)]
        assert not missing, (language, missing)
    merged = lexicon_for("it-IT")
    assert merged["pay_now"][0] == LEXICON["it"]["pay_now"][0]  # the locale first, English merged after
    assert set(LEXICON["en"]["pay_now"]) <= set(merged["pay_now"])
    assert lexicon_for("de") == lexicon_for("en")


def test_the_effective_language_is_the_pages_when_the_lexicon_has_it_and_the_guard_reads_every_language():
    assert [effective_language(declared, "it") for declared in ("en-US", "it-IT", "de", "", None, " EN ")] == [
        "en", "it", "it", "it", "it", "en"]
    assert effective_language("fr", "en-GB") == "en"
    italian = "Il prezzo include la consegna e il reso gratuito per tutti gli ordini, anche con il ritiro in negozio."
    english = "The price includes delivery and free returns for all orders, and you can pick it up at our store."
    assert effective_language("en-US", "it", italian) == "it" and effective_language("en-US", "it", english) == "en"
    assert effective_language("it", "en", english) == "en" and effective_language("it", "en", italian) == "it"
    assert effective_language("en", "it", "Scarpa con suola") == "en"  # too few words to overrule the declaration
    every = lexicon_all()
    assert set(LEXICON["it"]["pay_now"]) | set(LEXICON["en"]["pay_now"]) == set(every["pay_now"])
    assert set(every) == set(LEXICON["it"]) | set(LEXICON["en"])


def test_lexicon_patterns_are_portable_between_python_and_javascript():
    for language, keys in LEXICON.items():
        for key, patterns in keys.items():
            for source in patterns:
                re.compile(source, re.I)
                assert "(?P" not in source and "(?i" not in source and "\\p{" not in source, source
                for match in re.finditer(r"\\b", source):  # JS word boundaries are ASCII-only
                    around = source[max(0, match.start() - 1)] + source[match.end():match.end() + 1]
                    assert all(ord(c) < 128 for c in around), (language, key, source)
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    script = ("const L=" + json.dumps(LEXICON) + ";const bad=[];for(const [l,d] of Object.entries(L))"
              "for(const [k,ps] of Object.entries(d))for(const p of ps){try{new RegExp(p,'i')}catch(e){bad.push(p)}}"
              "process.stdout.write(JSON.stringify(bad))")
    assert json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout) == []


@pytest.mark.parametrize("key, text, expected", [
    ("pay_now", "Pagamenti sicuri", False), ("pay_now", "Metodi di pagamento", False), ("pay_now", "Paga ora", True),
    ("place_order", "Conferma l'ordine", True), ("place_order", "Riepilogo ordine", False),
    ("returns", "Prezzi sorpresi", False), ("returns", "Resi e rimborsi", True), ("vat", "attiva inclusa", False),
    ("legal_id", "P.IVA 01234567897", True), ("scarcity", "Solo 2 rimasti!", True), ("scarcity", "Disponibile", False),
    ("scarcity", "Solo 1 rimasto!", True), ("scarcity", "Rimasta solo 1 taglia", True),
    # account gate words (Fable ruling on rule d): creating a password, not entering one; a new customer registers
    ("password_new", "Crea una password", True), ("password_new", "Conferma password", True),
    ("password_new", "Inserisci la password", False), ("password_new", "Password", False),
    ("password_new", "Create a password", True), ("password_new", "Re-enter password", True),
    ("register", "Nuovo cliente?", True), ("register", "New customer", True), ("register", "Già registrato?", False),
    ("password_new", "Inserisci la tua password", False), ("password_new", "Inserisci la tua nuova password", True),
    # "Utenti registrati" heads a login box; a login-or-register box is a login box (Fable ruling 6)
    ("register", "Utenti registrati", False), ("register", "Clienti già registrati", False),
    ("register", "Registrati", True), ("register", "Crea account", True), ("login", "Accedi o registrati", True),
    ("login", "Accedi / Registrati", True), ("login", "Registrati oppure accedi", True), ("login", "Accedi ora", False),
    ("guest", "Non creare un account cliente", True), ("register", "Non creare un account cliente", False),
    ("guest", "Procedi senza creare un account", True), ("guest", "Acquista senza aprire un account", True),
    ("guest", "Check out without creating an account", True), ("guest", "Do not create a customer account", True),
    ("guest", "Don't create an account", True), ("login", "Entra", True), ("login", "Entra nel negozio", False),
    ("subtotal", "Somma parziale", True),
    # guest wording its own sentence denies (ws1f review 3); "Non creare un account" is still a guest choice
    ("guest_negated", "Non è possibile acquistare senza creare un account", True),
    ("guest_negated", "Senza registrazione non è possibile procedere", True),
    ("guest_negated", "L'acquisto come ospite non è disponibile", True),
    ("guest_negated", "You cannot check out without creating an account", True),
    ("guest_negated", "Guest checkout is not available", True), ("guest_negated", "Non creare un account", False),
    ("guest_negated", "Acquista senza registrazione, non è necessario un account", False),
    ("guest_negated", "Se non puoi accedere, continua senza account", False),
    ("guest_negated", "No account required to check out", False),
    ("guest_negated", "Guest checkout is enabled", False),
    ("tax_line", "IVA 22%", True), ("tax_line", "MwSt.", True), ("tax_line", "Contributo imballo", False),
    ("address_field", "Indirizzo", True), ("address_field", "Indirizzo email", False), ("address_field", "CAP", True),
    ("address_field", "Email address", False), ("address_field", "Street address", True),
    ("choose_option", "Scegli un'opzione...", True), ("choose_option", "-- Seleziona --", True),
    ("choose_option", "Nero", False), ("choose_option", "Choose a size", True),
    ("generic_link", "Scopri di più", True), ("generic_link", "Scopri la collezione", False),
    ("guest", "Continua come ospite", True), ("guest", "Hai già un account? Accedi", False),
    ("delivery", "Spedizione in 24 ore", True), ("delivery", "Spedizione in 24/48h", True),
    ("delivery", "Spedizione gratuita", False), ("payment_field", "Il titolare del trattamento è Rossi S.r.l.", False),
    ("payment_field", "Titolare della carta", True), ("personal_field", "Spedizione via corriere", False),
    ("quantity", "Quantita", True), ("coupon", "Hai un codice sconto?", True),
    # a stated delivery time or date, not where or how a parcel goes
    ("delivery", "Consegna in tutta Italia", False), ("delivery", "Consegna da 4,90 €", False),
    ("delivery", "Prodotti spediti in confezione regalo", False), ("delivery", "Consegna in 2-3 giorni", True),
    ("delivery", "Ricevilo entro giovedì", True), ("delivery", "Consegna prevista il 12/10", True),
    ("delivery", "Ships in a gift box", False), ("delivery", "Ships in 2 days", True),
    ("delivery", "Delivered in a gift box", False), ("delivery", "Estimated delivery 3-5 business days", True),
    # "only necessary" is a rejection of the optional cookies
    ("consent_accept", "Accetta tutti", True), ("consent_reject", "Accetta tutti", False),
    ("consent_accept", "Accetta solo i necessari", False), ("consent_reject", "Accetta solo i necessari", True),
    ("consent_reject", "Accetta i cookie necessari", True), ("consent_accept", "Accept all cookies", True),
    ("consent_accept", "Accept only essential cookies", False),
    ("consent_reject", "Accept only essential cookies", True),
    ("consent_reject", "Accept necessary cookies", True), ("consent_reject", "Accept all cookies", False),
    # invitations to log in are not a gate
    ("login_gate", "Accedi per completare l'acquisto più velocemente", True),
    ("login_optional", "Accedi per completare l'acquisto più velocemente", True),
    ("login_optional", "Have an account? Log in to check out faster.", True),
    ("login_optional", "Accedi per continuare", False),
    # forbidden labels
    ("place_order", "Conferma e acquista", True), ("place_order", "Confermo l'ordine", True),
    ("place_order", "Termina l'acquisto", True), ("place_order", "Finish order", True),
    ("place_order", "Finalize order", True), ("place_order", "Confirm and buy", True),
    ("pay_now", "Buy with PayPal", True), ("pay_now", "Buy with Shop Pay", True),
    ("pay_now", "Acquista con PayPal", True),
    ("pay_now", "Acquista con Klarna in 3 rate", False), ("pay_now", "Pay in 3 interest-free installments", False),
    # URL keys read the path and the query
    ("checkout_url", "/index.php?route=checkout/checkout", True), ("cart_url", "/index.php?route=checkout/cart", True),
    ("checkout_url", "/index.php?route=checkout/cart", False), ("info_url", "/content/pagamento.html", True),
    ("info_url", "/pages/payment.html", True), ("info_url", "/checkout/", False),
    ("info_url", "/ordine/stato?id=12", True), ("info_url", "/my-account/orders/", True),
    ("info_url", "/account/login?checkout_url=/checkouts/c1", False),  # Shopify's account gate before checkout
    # consent labels of Italian CMPs (Cookiebot, Complianz, legacy bars)
    ("consent_reject", "Nega", True), ("consent_reject", "Non acconsento", True),
    ("consent_accept", "Non acconsento", False), ("consent_accept", "Acconsento", True),
    ("consent_accept", "Sì, acconsento", True), ("consent_accept", "Sì, accetto", True),
    ("consent_accept", "Ok, ho capito", True), ("consent_reject", "Prosegui senza accettare", True),
    ("consent_reject", "Disagree", True), ("consent_reject", "I do not agree", True),
    ("consent_accept", "I do not agree", False), ("consent_reject", "Only allow essential cookies", True),
    ("consent_accept", "OK, got it", True), ("consent_reject", "Negozi", False),
    # a cost row labelled by its delivery is shipping; a delivery time is not
    ("shipping", "Consegna", True), ("shipping", "Costo consegna:", True), ("shipping", "Consegna a domicilio", True),
    ("shipping", "Consegna in 24 ore", False), ("shipping", "Standard delivery", True),
    ("shipping", "Delivery", True), ("shipping", "Next-day delivery", True), ("shipping", "Delivery in 2 days", False),
    # the cart's way into the checkout
    ("checkout", "Checkout →", True), ("checkout", "Checkout securely", True), ("checkout", "Checkout sicuro", True),
    ("checkout", "Checkout (2)", True), ("checkout", "Checkout with PayPal", False),
    # site utilities are not categories; a "Cookie" or a "Tote Bag" category is
    ("utility", "Cookie", False), ("utility", "Tote Bag", False), ("utility", "Informativa sui cookie", True),
    ("utility", "Cookie policy", True), ("utility", "Bag (2)", True),
    # a search that found nothing, never a cart counter
    ("no_results", "Nessun prodotto trovato per «zaino»", True), ("no_results", "Carrello (0 articoli)", False),
    ("no_results", "Nessun prodotto nel carrello", False), ("no_results", "Sorry, we couldn't find any match", True),
    ("no_results", "0 items", False), ("no_results", "24 risultati", False),
])
def test_lexicon_samples(key, text, expected):
    assert bool(compile_lexicon(lexicon_for("it"))[key].search(text)) is expected


# ---------------------------------------------------------------- pure summaries

def event(method, session="s1", **params):
    return {"method": method, "params": params, "session_id": session}


def request(rid, url, kind="Script", **extra):
    return event("Network.requestWillBeSent", requestId=rid, loaderId="L2", frameId="F", type=kind,
                 request={"url": url, **extra})


def test_network_summary_counts_hops_bytes_errors_third_parties_and_mixed_content():
    events = [
        event("Network.requestWillBeSent", requestId="L1", loaderId="L1", frameId="F", type="Document",
              request={"url": "https://old.example/"}),
        event("Network.loadingFinished", requestId="L1", encodedDataLength=999),
        event("Network.requestWillBeSent", requestId="L2", loaderId="L2", frameId="F", type="Document",
              request={"url": "https://shop.example/"}),
        event("Network.requestWillBeSent", requestId="L2", loaderId="L2", frameId="F", type="Document",
              request={"url": "https://www.shop.example/"}, redirectResponse={"status": 301, "encodedDataLength": 200}),
        event("Network.responseReceived", requestId="L2", type="Document", frameId="F",
              response={"status": 200, "url": "https://www.shop.example/"}),
        event("Network.loadingFinished", requestId="L2", encodedDataLength=10_000),
        request("r1", "https://cdn.shop.example/app.js"),
        event("Network.loadingFinished", requestId="r1", encodedDataLength=5_000),
        request("r2", "https://tracker.example.net/t.js"),
        event("Network.loadingFinished", requestId="r2", encodedDataLength=3_000),
        request("r3", "http://img.shop.example/a.jpg", "Image"),
        event("Network.responseReceived", requestId="r3", type="Image", response={"status": 404}),
        event("Network.loadingFinished", requestId="r3", encodedDataLength=500),
        request("r4", "https://shop.example/font.woff2", "Font"),
        event("Network.dataReceived", requestId="r4", encodedDataLength=700),
        request("r5", "https://shop.example/x.css", "Stylesheet"),
        event("Network.loadingFailed", requestId="r5", canceled=True, type="Stylesheet"),
        request("r6", "https://shop.example/api", "XHR"),
        event("Network.loadingFailed", requestId="r6", errorText="net::ERR"),
        request("r7", "data:image/png;base64,AAAA", "Image"),
    ]
    start = _document_start(events, "F")
    assert events[start]["params"]["requestId"] == "L2"
    summary = network_summary(events[start:], "https://www.shop.example/")
    assert summary["requests"] == 8  # the 301 hop counts, the data: URL does not
    assert summary["bytes_transfer"] == 200 + 10_000 + 5_000 + 3_000 + 500 + 700
    assert summary["bytes_js"] == 8_000 and summary["bytes_img"] == 500 and summary["bytes_font"] == 700
    assert summary["bytes_third_party"] == 3_000 and summary["third_party_domains"] == ["example.net"]
    assert summary["third_party_share"] == round(3_000 / summary["bytes_transfer"], 4)
    assert summary["http_errors"] == 1 and summary["http_error_samples"] == [{"url": "http://img.shop.example/a.jpg",
                                                                              "status": 404}]
    assert summary["failed"] == 1 and summary["mixed_content"] == 1 and summary["source"] == "cdp"
    assert summary["by_type"]["document"] == {"requests": 2, "bytes": 10_200}


def test_document_events_leave_out_what_the_previous_document_did_while_the_next_one_loaded():
    def sent(rid, url, loader, frame="F", kind="Fetch", at=100.0):
        return event("Network.requestWillBeSent", requestId=rid, loaderId=loader, frameId=frame, type=kind,
                     timestamp=at, request={"url": url})

    events = [
        sent("o1", "https://shop.example/old.js", "L2", at=99.0),
        sent("L3", "https://shop.example/next", "L3", kind="Document", at=100.0),
        sent("x1", "https://shop.example/ping", "L2", at=100.01),  # the old page, before the commit
        event("Runtime.consoleAPICalled", type="error", args=[{"type": "string", "value": "old page tick"}]),
        sent("x2", "https://ads.example/a.js", "I1", frame="F2", kind="Script", at=100.02),  # an old iframe
        event("Network.responseReceived", requestId="L3", type="Document", frameId="F",
              response={"status": 200, "url": "https://shop.example/next",
                        "timing": {"requestTime": 100.005, "receiveHeadersStart": 1.0, "receiveHeadersEnd": 155.0}}),
        event("Runtime.executionContextsCleared"),
        sent("x3", "https://shop.example/late", "L2", at=100.3),  # a late event of the old loader
        sent("n1", "https://shop.example/app.js", "L3", kind="Script", at=100.31),
        sent("n2", "https://shop.example/frame", "I2", frame="F3", kind="Document", at=100.32),  # a new iframe
        event("Runtime.consoleAPICalled", type="error", args=[{"type": "string", "value": "new page"}]),
    ]
    own = document_events(events, "F")
    assert own["start"] == 1 and own["ttfb"] == 160.0 and own["failure"] is None
    sent_ids = [e["params"]["requestId"] for e in own["network"] if e["method"] == "Network.requestWillBeSent"]
    assert sent_ids == ["L3", "n1", "n2"]
    assert page_errors(own["runtime"]) == {"console": 1, "page": 0, "samples": ["new page"]}
    assert own["prefetched"] is False
    assert document_events(events[2:], "F") == {"network": events[2:], "runtime": events[2:], "start": None,
                                                "ttfb": None, "failure": None, "prefetched": False}
    # A document the shop's speculation rules prefetched: the response comes from the prefetch cache, its timing
    # starts before the navigation's request (no TTFB).
    prefetched = [sent("L4", "https://shop.example/next", "L4", kind="Document", at=200.0),
                  event("Network.responseReceived", requestId="L4", type="Document", frameId="F",
                        response={"status": 200, "url": "https://shop.example/next", "fromPrefetchCache": True,
                                  "timing": {"requestTime": 199.9, "receiveHeadersEnd": 1.3}})]
    own = document_events(prefetched, "F")
    assert own["prefetched"] is True and own["ttfb"] is None


def test_a_failed_document_request_keeps_its_first_error_and_the_favicon_probe_is_no_page_error():
    events = [
        event("Network.requestWillBeSent", requestId="L1", loaderId="L1", frameId="F", type="Document",
              timestamp=5.0, request={"url": "http://127.0.0.1:9/"}),
        event("Network.loadingFailed", requestId="L1", errorText="net::ERR_CONNECTION_REFUSED", canceled=False),
        event("Network.loadingFailed", requestId="L1", errorText="net::ERR_ABORTED", canceled=True),
    ]
    own = document_events(events, "F")
    assert own["failure"] == "net::ERR_CONNECTION_REFUSED" and own["ttfb"] is None
    assert network_summary(own["network"], "chrome-error://chromewebdata/")["failed"] == 1
    favicon = [event("Network.requestWillBeSent", requestId="r1", loaderId="L1", frameId="F", type="Other",
                     initiator={"type": "other"}, request={"url": "https://shop.example/favicon.ico"}),
               event("Network.responseReceived", requestId="r1", type="Other", response={"status": 404}),
               event("Network.loadingFinished", requestId="r1", encodedDataLength=300)]
    summary = network_summary(favicon, "https://shop.example/")
    assert summary["requests"] == 1 and summary["http_errors"] == 0


def test_resource_timing_summary_for_lossy_transports():
    rows = [{"name": "http://127.0.0.1:1/shop/", "type": "navigation", "transfer": 9000, "status": 200},
            {"name": "http://127.0.0.1:1/shop/shop.js", "type": "script", "transfer": 4000, "status": 200},
            {"name": "http://127.0.0.1:1/shop/shop.css?v=2", "type": "link", "transfer": 1000, "status": 200},
            {"name": "https://cdn.other.example/x.png", "type": "css", "transfer": 0, "status": 0},
            {"name": "http://127.0.0.1:1/missing.png", "type": "img", "transfer": 300, "status": 404}]
    summary = resource_summary(rows, "http://127.0.0.1:1/shop/")
    assert summary["source"] == "resource_timing" and summary["requests"] == 5
    assert summary["bytes_transfer"] == 14_300 and summary["bytes_js"] == 4000 and summary["bytes_css"] == 1000
    assert summary["third_party_domains"] == ["other.example"] and summary["http_errors"] == 1


def test_page_errors_count_exceptions_and_console_errors_but_not_network_log_lines():
    events = [
        event("Runtime.exceptionThrown",
              exceptionDetails={"text": "Uncaught", "exception": {"description": "TypeError: x"}}),
        event("Runtime.consoleAPICalled", type="error", args=[{"type": "string", "value": "boom"}]),
        event("Runtime.consoleAPICalled", type="log", args=[{"type": "string", "value": "fine"}]),
        event("Log.entryAdded", entry={"level": "error", "source": "network", "text": "404"}),
        event("Log.entryAdded", entry={"level": "error", "source": "security", "text": "Mixed Content"}),
        event("Log.entryAdded", entry={"level": "warning", "source": "javascript", "text": "deprecated"}),
    ]
    assert page_errors(events) == {"console": 3, "page": 1, "samples": ["TypeError: x", "boom", "Mixed Content"]}


def jpeg(image):
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def test_visual_metrics_separate_flat_busy_and_colourful_screens():
    flat = visual_metrics(jpeg(Image.new("RGB", (300, 200), (128, 128, 128))))
    assert flat["colorfulness"] < 1 and flat["edge_density"] < 0.01
    stripes = Image.new("RGB", (300, 200), "white")
    draw = ImageDraw.Draw(stripes)
    for x in range(0, 300, 6):
        draw.line([(x, 0), (x, 200)], fill="black", width=2)
    busy = visual_metrics(jpeg(stripes))
    assert busy["edge_density"] > 0.2 and busy["bytes_per_px"] > flat["bytes_per_px"]
    colour = Image.new("RGB", (300, 200), (230, 20, 30))
    ImageDraw.Draw(colour).rectangle([150, 0, 300, 200], fill=(20, 40, 230))
    assert visual_metrics(jpeg(colour))["colorfulness"] > 80
    scaled = visual_metrics(jpeg(Image.new("RGB", (900, 600), (128, 128, 128))), dpr=3)
    assert (scaled["width"], scaled["height"]) == (300, 200)


# ---------------------------------------------------------------- PageCollector on the fixture shop

def browser_pages(chromium, shop_server, store, run_id, profile, pages, *, quiet=None):
    """Load pages in order in one tab of a new context; returns ({name: PageRecord}, {name: [paths]}, collector)."""
    transport = DirectTransport(chromium.ws_url)
    context = new_context(transport)
    collector = PageCollector(transport, store, run_id, profile=profile, context_id=context)
    if quiet is not None:
        collector.net_quiet_s = collector.lcp_quiet_s = quiet
    records, paths = {}, {}
    browser = None
    try:
        for i, name in enumerate(pages):
            url = shop_server.url("shop/" + name.split("#")[0])
            if name.endswith("#seed"):
                browser.evaluate(CART_ITEMS)
            before = len(shop_server.requests)
            if browser is None:
                browser = collector.open(url)
            records[name] = collector.load_page(browser, url, stage="x", page_id=f"{profile}-x-{i + 1}")
            paths[name] = [r["path"] for r in shop_server.requests[before:]]
    finally:
        if browser is not None:
            browser.close()
        close_context(transport, context)
        transport.close()
    return records, paths, collector


DESKTOP_PAGES = ["index.html", "category.html", "product-dark.html", "product.html", "cart.html#seed",
                 "cart.html?dark=1", "checkout.html", "checkout.html?guest=1", "challenge.html"]


@pytest.fixture(scope="module")
def run(tmp_path_factory, chromium, shop_server):
    store = RunStore(tmp_path_factory.mktemp("runs"))
    run_id = store.new_run("audit", shop_server.url("shop/index.html"), {})
    records, paths, collector = browser_pages(chromium, shop_server, store, run_id, "desktop", DESKTOP_PAGES)
    return {"store": store, "run_id": run_id, "records": records, "paths": paths, "collector": collector}


@pytest.fixture(scope="module")
def mobile(chromium, shop_server):
    records, _, collector = browser_pages(chromium, shop_server, None, None, "mobile",
                                          ["index.html", "product.html", "product-dark.html"])
    return {"records": records, "collector": collector}


@pytest.fixture
def lab(chromium, shop_server):
    """A fresh tab on a plain fixture page for in-page experiments (short settle windows)."""
    transport = DirectTransport(chromium.ws_url)
    context = new_context(transport)
    collector = PageCollector(transport, None, None, profile="desktop", context_id=context, screenshots=False)
    collector.net_quiet_s = collector.lcp_quiet_s = 0.3
    browser = collector.open(shop_server.url("shop/resi.html"))
    yield collector, browser
    browser.close()
    close_context(transport, context)
    transport.close()


def test_vitals_are_measured_on_a_visible_page_after_a_quiet_settle(run):
    for name, record in run["records"].items():
        vitals = record["vitals"]
        assert vitals["visibility_state_at_load"] == "visible", name
        assert vitals["settled_reason"] == "quiet", name
        assert vitals["ttfb_source"] == "cdp" and vitals["ttfb"] < 150, name  # local server, no throttling
        for key in ("ttfb", "fcp", "lcp", "dcl_ms", "load_ms"):
            assert vitals[key] is not None and vitals[key] >= 0, (name, key)
        assert vitals["fcp"] <= vitals["lcp"] and vitals["lcp_element"]
        assert vitals["js_errors"] == (1 if name == "product-dark.html" else 0)
        assert record["status_code"] == 200 and record["final_url"].endswith(name.split("#")[0])
        assert 0 < record["loaded_ms"] < 20_000
        assert record["classification"]["type"] in ("home", "plp", "pdp", "cart", "checkout", "challenge", "other")


def test_open_then_load_page_loads_the_first_url_once(run):
    assert run["paths"]["index.html"].count("/shop/index.html") == 1


def test_network_bytes_match_the_served_files(run):
    for name in ("index.html", "product-dark.html", "category.html"):
        record, served = run["records"][name], run["paths"][name]
        existing = [p for p in served if (FIXTURES / p.lstrip("/").split("?")[0]).is_file()]
        size = sum((FIXTURES / p.lstrip("/").split("?")[0]).stat().st_size for p in existing)
        network = record["network"]
        assert network["requests"] == len(served), (name, served)
        assert size <= network["bytes_transfer"] <= size + 600 * len(served), name  # plus response headers
        assert network["bytes_js"] >= (FIXTURES / "shop/shop.js").stat().st_size
        assert network["third_party_share"] == 0 and network["bytes_third_party"] == 0
        assert network["source"] == "cdp" and network["mixed_content"] == 0


def test_errors_are_counted_on_the_page_that_threw_and_do_not_leak_into_the_next(run):
    dark, clean = run["records"]["product-dark.html"], run["records"]["product.html"]  # loaded in this order
    assert dark["errors"]["page"] == 1 and dark["errors"]["console"] == 2
    assert any("Widget recensioni" in s for s in dark["errors"]["samples"])
    assert dark["network"]["http_errors"] == 1
    assert dark["network"]["http_error_samples"][0]["url"].endswith("/img/badge-garanzia.png")
    assert clean["errors"] == {"console": 0, "page": 0, "samples": []}
    assert clean["network"]["http_errors"] == 0 and clean["network"]["requests"] == len(run["paths"]["product.html"])


def test_layout_shift_of_the_dark_page_and_none_on_clean_pages(run):
    assert run["records"]["product-dark.html"]["vitals"]["cls"] > 0.1
    for name in ("index.html", "category.html", "product.html", "cart.html#seed", "checkout.html"):
        assert run["records"][name]["vitals"]["cls"] < 0.01, name


def test_an_injected_late_shift_is_measured_and_input_shifts_are_kept_apart(lab):
    collector, browser = lab
    record = collector.collect(browser, stage="x", page_id="p1")
    assert record["vitals"]["cls"] == 0
    browser.evaluate("""(() => { const d = document.createElement('div'); d.style.height = '320px';
      d.textContent = 'Banner'; document.body.prepend(d); })()""")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not collector.vitals(browser)["cls"]:
        time.sleep(0.1)
    vitals = collector.vitals(browser)
    assert vitals["cls"] > 0.1 and vitals["cls_post_input"] == 0


def test_every_audit_section_is_present_and_bounded(run):
    for name, record in run["records"].items():
        audit = record["audit"]
        assert set(AuditPayload.__annotations__) <= set(audit), (name, set(AuditPayload.__annotations__) - set(audit))
        assert len(json.dumps(audit)) < 120_000, name
        assert len(audit["ctas"]) <= 40 and len(audit["prices"]) <= 80 and len(audit["products"]["cards"]) <= 40
        assert audit["viewport"] == {**audit["viewport"], "w": 1366, "h": 768, "dpr": 1}
        assert audit["lang"] == "it" and not audit["truncated"]
        for overlay in audit["overlays"]:  # one threshold for every consumer: a modal, or 15% of the viewport
            assert overlay["interrupting"] == (overlay["modal"] or overlay["coverage"] >= 0.15), (name, overlay)


def test_snippets_cover_every_kind_with_stable_short_verbatim_texts(run):
    kinds = set()
    for name, record in run["records"].items():
        snippets = record["audit"]["snippets"]
        assert len(snippets) <= 40
        assert [s["snippet_id"] for s in snippets] == [f"s{i}" for i in range(1, len(snippets) + 1)]
        for s in snippets:
            assert s["kind"] in SNIPPET_KINDS and 0 < len(s["text"]) <= 600, s
            assert s["text"] == " ".join(s["text"].split()), s
            if s["kind"] in ("returns_policy", "shipping_policy"):  # a bare policy link is trust.policy_links
                assert len(s["text"].split()) >= 4, s
        kinds |= {s["kind"] for s in snippets}
    assert kinds == set(SNIPPET_KINDS)


def test_snippets_are_substrings_of_the_rendered_text(lab, shop_server):
    collector, browser = lab
    browser.evaluate(CART_ITEMS)
    for name in ("index.html", "product-dark.html", "product.html", "cart.html?dark=1", "checkout.html"):
        record = collector.load_page(browser, shop_server.url("shop/" + name), stage="x", page_id=name)
        body = browser.evaluate("document.body.innerText.replace(/\\s+/g, ' ').trim()")
        for s in record["audit"]["snippets"]:
            assert s["text"] in body, (name, s)


def test_screenshots_snapshots_and_visual_metrics_are_stored(run):
    directory = run["store"].path(run["run_id"])
    for name, record in run["records"].items():
        assert record["screenshot"] == f"shots/{record['page_id']}.jpg"
        assert (directory / record["screenshot"]).stat().st_size > 1000
        snapshot = json.loads((directory / record["snapshot"]).read_text())
        assert snapshot["page_id"] == record["page_id"] and snapshot["audit"]["url"] == record["final_url"]
        visual = record["visual"]
        assert (visual["width"], visual["height"]) == (1366, 768)
        for key in ("colorfulness", "edge_density", "bytes_per_px"):
            assert math.isfinite(visual[key]) and visual[key] >= 0, (name, key)


def test_home_audit(run):
    audit = run["records"]["index.html"]["audit"]
    assert audit["search"]["present"] and audit["search"]["above_fold"] and audit["search"]["has_autocomplete_attr"]
    assert {"Trail", "Abbigliamento", "Accessori", "Offerte"} <= {c["label"] for c in audit["nav"]["categories"]}
    consent = [o for o in audit["overlays"] if o["kind"] == "consent"]
    assert len(consent) == 1 and consent[0]["accept_labels"] and consent[0]["reject_labels"]
    assert consent[0]["accept_area"] == consent[0]["reject_area"] and not consent[0]["blocking"]
    trust = audit["trust"]
    assert trust["contact"] == {**trust["contact"], "email": True, "phone": True, "address": True}
    assert "01234567897" in trust["vat_id"] and trust["policy_count"] == 4
    assert {"visa", "mastercard", "paypal", "klarna", "satispay"} <= set(trust["payment_logos"])
    persuasion = audit["persuasion"]
    assert persuasion["free_shipping_threshold"][0]["value"] == 59
    assert persuasion["reciprocity"] and persuasion["authority"] and persuasion["vat_statement"]
    assert not persuasion["scarcity"] and not persuasion["urgency"]
    assert audit["doc"]["headings"]["h1"] == 1 and audit["doc"]["word_count"] > 100
    assert audit["meta"]["jsonld_types"] == {"Organization": 1, "WebSite": 1}  # read through @graph


def test_listing_audit(run):
    audit = run["records"]["category.html"]["audit"]
    products = audit["products"]
    assert products["cards_count"] == 24 and products["main_group"] == 24 and products["itemlist_jsonld"] == 24
    assert all(c["href"] and c["price_value"] for c in products["cards"])
    assert products["cards"][0] == {**products["cards"][0], "title": "Scarpa da corsa Aurora", "price_value": 49.9}
    filters = audit["filters"]
    assert filters["controls"] == 5 and filters["sort"]["present"] and filters["pagination"] == "pagination"
    assert "96 prodotti" in filters["result_count_text"]
    assert audit["nav"]["breadcrumbs"]["present"]
    assert audit["doc"]["prose_blocks"] <= 2 and audit["doc"]["word_count"] < 40  # card titles are not prose
    assert audit["forms"]["visible"] == 1 and audit["forms"]["fields"][0]["tag"] == "select"  # sort only, not facets


def test_product_audit(run, mobile):
    audit = run["records"]["product.html"]["audit"]
    pdp = audit["pdp"]
    assert pdp["add_to_cart"]["present"] and pdp["add_to_cart"]["above_fold"] and pdp["add_to_cart"]["contrast"] >= 4.5
    assert pdp["price"]["value"] == pdp["structured"]["price"] == 49.9 and pdp["strike_price"]["value"] == 69.9
    assert pdp["structured"] == {**pdp["structured"], "has_return_policy": True, "has_shipping": True,
                                 "availability": "InStock"}
    assert pdp["reviews"]["rating"] == 4.5 and pdp["reviews"]["count"] == 128
    assert pdp["variant_selector"] == "buttons" and pdp["variants"] == 5
    sizes = pdp["variant_groups"][0]  # 42 is pressed: nothing for the crawler to pick
    assert (sizes["label"], sizes["selected"], sizes["first_available"]) == ("Taglia", True, "40") and sizes["rect"]
    assert pdp["delivery_text"] and pdp["shipping_text"] and pdp["returns_text"] and pdp["stock_text"]
    assert audit["persuasion"]["lowest_price_30d"][0]["value"] == 59.9
    assert audit["persuasion"]["lowest_price_30d"][0] == {**audit["persuasion"]["lowest_price_30d"][0],
                                                          "in_card": False, "overlay": False, "count": 1}
    assert {p["in_card"] for p in audit["prices"]} == {False}
    assert audit["targets"]["lt24"] == 0 and audit["images"]["oversized_count"] == 0
    assert audit["a11y"]["img_missing_alt"] == 0 and [o["kind"] for o in audit["overlays"]] == ["consent"]
    phone = mobile["records"]["product.html"]["audit"]
    assert phone["viewport"] == {**phone["viewport"], "w": 390, "h": 844, "dpr": 3}
    assert phone["pdp"]["add_to_cart"]["above_fold"] and phone["pdp"]["price"]["above_fold"]


def test_dark_product_audit(run, mobile):
    audit = run["records"]["product-dark.html"]["audit"]
    pdp = audit["pdp"]
    assert pdp["price"]["value"] == 49.9 and pdp["structured"]["price"] == 39.9  # visible and structured disagree
    assert pdp["strike_price"] and not audit["persuasion"]["lowest_price_30d"]
    assert not pdp["structured"]["has_return_policy"] and not pdp["returns_text"] and not pdp["shipping_text"]
    assert pdp["buy_now"]["present"] and pdp["add_to_cart"]["contrast"] < 3
    assert audit["persuasion"]["scarcity"][0]["number"] == 2
    countdown = [u for u in audit["persuasion"]["urgency"] if u["countdown"]]
    assert countdown and 840 <= countdown[0]["remaining_s"] <= 900
    kinds = {o["kind"]: o for o in audit["overlays"]}
    assert kinds["consent"]["accept_labels"] and not kinds["consent"]["reject_labels"] and kinds["consent"]["modal"]
    assert "No grazie, preferisco pagare di più" in kinds["newsletter"]["decline_labels"]
    assert kinds["newsletter"]["blocking"] and kinds["newsletter"]["coverage"] > 0.9
    assert kinds["newsletter"]["interrupting"] and kinds["consent"]["interrupting"]
    assert audit["images"]["oversized_count"] == 1 and audit["a11y"]["img_missing_alt"] == 1
    assert audit["targets"]["lt24"] >= 4
    declines = {s["text"] for s in audit["snippets"] if s["kind"] == "modal_decline"}
    assert "No grazie, preferisco pagare di più" in declines
    phone = mobile["records"]["product-dark.html"]["audit"]
    assert not phone["pdp"]["add_to_cart"]["above_fold"]  # pushed down by the late promo bar


def test_cart_audits(run):
    clean = run["records"]["cart.html#seed"]["audit"]
    cart = clean["cart"]
    assert [(i["title"], i["qty"], i["price_value"]) for i in cart["line_items"]] == [
        ("Scarpa da corsa Aurora", 1, 49.9), ("Scarpa da corsa Onda", 2, 89.8)]
    assert cart["editable"] and cart["checkout_cta"]["present"]
    assert (cart["subtotal_value"], cart["shipping_value"], cart["total_value"]) == (139.7, 0, 139.7)
    assert not cart["prechecked_paid"] and cart["paid_options"][0]["price_value"] == 3.0
    assert clean["forms"]["guest_option"] and not clean["forms"]["login_required"]
    dark = run["records"]["cart.html?dark=1"]["audit"]["cart"]
    assert [i["title"] for i in dark["line_items"]][-1] == "Protezione spedizione Premium"
    assert dark["line_items"][-1]["addon"] and dark["prechecked_paid"][0]["price_value"] == 4.9
    assert {"Commissione di servizio", "Spedizione"} <= {f["label"] for f in dark["fees"]}
    assert dark["total_value"] == round(139.7 + 2.9 + 6.9 + 1.5 + 4.9, 2)
    fee_lines = [s["text"] for s in run["records"]["cart.html?dark=1"]["audit"]["snippets"] if s["kind"] == "fee_line"]
    assert any("Commissione di servizio" in t for t in fee_lines)
    assert any("Protezione spedizione" in t for t in fee_lines)
    assert not run["records"]["cart.html?dark=1"]["audit"]["forms"]["guest_option"]


def test_checkout_audits_pin_the_account_gate(run):
    gate = run["records"]["checkout.html"]["audit"]
    forms = gate["forms"]
    assert forms["visible"] == 14 and forms["password_present"] and forms["login_required"]
    assert not forms["guest_option"] and not forms["cc_present"]
    assert forms["labels_share"] == 1 and forms["autocomplete_share"] == round(11 / 13, 3)
    assert any(c["label"] == "Paga ora" and c["lexicon_hit"] == "pay_now" for c in gate["ctas"])
    optional = run["records"]["checkout.html?guest=1"]["audit"]["forms"]  # only "Hai già un account? Accedi"
    assert optional["visible"] == 13 and not optional["password_present"]
    assert not optional["login_required"] and not optional["guest_option"]


def test_mobile_profile_is_applied(mobile):
    collector = mobile["collector"]
    assert collector.applied_profile["name"] == "mobile" and collector.applied_profile["touch"]
    assert "Mobile" in collector.applied_profile["user_agent"]
    record = mobile["records"]["index.html"]
    assert record["profile"] == "mobile" and record["visual"]["width"] == 390
    assert record["audit"]["overlays"][0]["coverage"] > 0.15 and record["audit"]["overlays"][0]["interrupting"]
    for name, page in mobile["records"].items():  # TTFB includes the emulated 150 ms round trip
        assert page["vitals"]["ttfb"] >= 150 and page["vitals"]["ttfb_source"] == "cdp", name
        assert page["vitals"]["fcp"] >= page["vitals"]["ttfb"], name


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not (value := predicate()) and time.monotonic() < deadline:
        time.sleep(0.1)
    return value


def test_mark_and_since_follow_actions_and_navigations(lab, shop_server):
    collector, browser = lab
    collector.load_page(browser, shop_server.url("shop/product-dark.html"), stage="pdp", page_id="p1")
    # The countdown changes every second on its own: its third change more than 2 s after load makes it a ticker.
    assert wait_for(lambda: browser.evaluate("window.__jevVitals.activity().tickers") == 1)
    mark = collector.mark(browser)
    time.sleep(1.2)
    quiet = collector.since(browser, mark)  # the countdown keeps ticking: a ticker, not a response
    assert quiet["mutations"] == 0 and quiet["first_response_ms"] is None and quiet["navigations"] == 0
    assert quiet["mutations_total"] >= 1  # evidence that only a self-updating node changed
    mark = collector.mark(browser)
    browser.evaluate("document.querySelectorAll('.sizes button')[0].click()")
    clicked = collector.since(browser, mark)
    assert clicked["mutations"] >= 1 and clicked["first_response_ms"] is not None and clicked["navigations"] == 0
    browser.evaluate("history.pushState({}, '', '?step=2')")
    assert collector.since(browser, mark)["navigations"] == 1
    mark = collector.mark(browser)
    collector.load_page(browser, shop_server.url("shop/product.html"), stage="pdp", page_id="p2")
    moved = collector.since(browser, mark)
    assert moved["navigations"] >= 1 and moved["requests"] >= 1 and moved["first_response_ms"] > 0
    assert moved["first_request_ms"] == moved["first_response_ms"]  # the new document's own request


def test_repeated_actions_and_late_toasts_are_responses_not_tickers(lab, shop_server):
    collector, browser = lab
    collector.load_page(browser, shop_server.url("shop/product.html"), stage="pdp", page_id="p1")
    for i in range(5):  # every size click flips aria-pressed on all five buttons
        mark = collector.mark(browser)
        browser.evaluate(f"document.querySelectorAll('.sizes button')[{i}].click()")
        since = collector.since(browser, mark)
        assert since["mutations"] >= 5 and since["first_response_ms"] is not None, (i, since)
    for count in range(1, 4):  # the cart badge changes on every add-to-cart
        mark = collector.mark(browser)
        browser.evaluate("document.querySelector('#add').click()")
        since = collector.since(browser, mark)
        assert since["mutations"] >= 1 and since["first_response_ms"] is not None, (count, since)
        assert browser.evaluate("document.querySelector('[data-cart-count]').textContent") == str(count)
    time.sleep(2.3)  # past the response window of the last action
    mark = collector.mark(browser)
    browser.evaluate("document.body.append(Object.assign(document.createElement('aside'), {textContent: 'Fatto'}))")
    since = collector.since(browser, mark)
    assert since["mutations"] == since["mutations_total"] == 1 and since["first_response_ms"] is not None
    assert browser.evaluate("window.__jevVitals.activity().tickers") == 0


def test_the_previous_document_does_not_leak_into_the_next_record(chromium, shop_server):
    transport = DirectTransport(chromium.ws_url)
    context = new_context(transport)
    collector = PageCollector(transport, None, None, profile="mobile", context_id=context, screenshots=False)
    collector.net_quiet_s = collector.lcp_quiet_s = 0.3
    noisy = ("setInterval(() => { console.error('old page tick'); setTimeout(() => { throw new Error('old'); });"
             " fetch('img/nope.png?' + Math.random()); }, 10)")
    browser = collector.open(shop_server.url("shop/resi.html"))
    try:
        collector.collect(browser, stage="x", page_id="p1")
        browser.evaluate(noisy)
        time.sleep(0.3)
        before = len(shop_server.requests)
        record = collector.load_page(browser, shop_server.url("shop/product.html"), stage="pdp", page_id="p2")
        served = [r["path"] for r in shop_server.requests[before:] if "nope.png" not in r["path"]]
        assert record["errors"] == {"console": 0, "page": 0, "samples": []}
        assert record["network"]["requests"] == len(served) and record["network"]["http_errors"] == 0
        browser.evaluate(noisy)  # the same through a script navigation, collected without load_page
        time.sleep(0.2)
        before = len(shop_server.requests)
        browser.evaluate("location.href = 'cart.html'")
        record = collector.collect(browser, stage="cart", page_id="p3")
        served = [r["path"] for r in shop_server.requests[before:] if "nope.png" not in r["path"]]
        assert record["final_url"].endswith("/shop/cart.html") and record["status_code"] == 200
        assert record["errors"] == {"console": 0, "page": 0, "samples": []}
        assert record["network"]["requests"] == len(served) and record["network"]["http_errors"] == 0
    finally:
        browser.close()
        close_context(transport, context)
        transport.close()


def test_a_failed_navigation_is_not_audited_as_a_fast_page(lab):
    collector, browser = lab
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]  # closed again: nothing listens there
    record = collector.load_page(browser, f"http://127.0.0.1:{port}/", stage="home", page_id="p1")
    assert record["final_url"].startswith("chrome-error://") and record["status_code"] is None
    assert record["classification"]["type"] == "other" and record["classification"]["signals"] == ["navigation_error"]
    for key in ("ttfb", "fcp", "lcp", "cls", "tbt_approx", "loaf_count", "dcl_ms", "load_ms"):
        assert record["vitals"][key] is None, key
    assert record["network"]["failed"] == 1 and record["audit"] == {} and record["visual"] == {}
    assert [n for n in record["notes"] if n.startswith("navigation_error")] == [
        "navigation_error: net::ERR_CONNECTION_REFUSED"]


def test_polling_after_load_does_not_hold_the_settle(lab, shop_server):
    collector, browser = lab
    collector.net_quiet_s, collector.settle_timeout_s = 1.0, 6.0
    browser.call("Page.addScriptToEvaluateOnNewDocument", source=(  # a heartbeat whose answer nobody reads
        "addEventListener('load', () => setInterval(() => fetch('img/shoe-a.svg?hb=' + Math.random()), 600))"))
    started = time.monotonic()
    record = collector.load_page(browser, shop_server.url("shop/product.html"), stage="pdp", page_id="p1")
    assert record["vitals"]["settled_reason"] == "quiet" and time.monotonic() - started < 5
    assert not [n for n in record["notes"] if n.startswith("settle:")]
    assert record["network"]["requests"] <= 12


def test_long_tasks_and_event_timing_of_a_trusted_click(lab):
    collector, browser = lab
    collector.collect(browser, stage="x", page_id="p1")
    browser.evaluate("setTimeout(() => { const t = performance.now(); while (performance.now() - t < 300) {} }, 10)")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not collector.vitals(browser)["long_tasks_ms"]:
        time.sleep(0.1)
    vitals = collector.vitals(browser)
    assert vitals["long_tasks_ms"] >= 290 and vitals["tbt_approx"] >= 240 and vitals["loaf_count"] >= 1
    browser.evaluate("""(() => { const b = document.createElement('button'); b.textContent = 'Lento';
      b.style.cssText = 'position:fixed;top:10px;left:10px;width:120px;height:50px;z-index:99';
      b.onclick = () => {
        const t = performance.now(); while (performance.now() - t < 150) {} b.textContent = 'Fatto'; };
      document.body.append(b); })()""")
    mark = collector.mark(browser)
    for kind in ("mousePressed", "mouseReleased"):
        collector.transport.call("Input.dispatchMouseEvent", browser.session, type=kind, x=60, y=35, button="left",
                                 clickCount=1)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not collector.since(browser, mark)["event_timing_max_ms"]:
        time.sleep(0.1)
    since = collector.since(browser, mark)
    assert since["event_timing_max_ms"] >= 140 and since["first_response_ms"] >= 140 and since["mutations"] >= 1


def test_vitals_script_installs_once_and_only_in_the_top_frame(lab):
    collector, browser = lab
    assert browser.evaluate(f"(() => {{ const before = window.__jevVitals; {VITALS_JS}; "
                            "return before === window.__jevVitals; })()") is True
    browser.evaluate("""(() => { const f = document.createElement('iframe'); f.id = 'probe';
      f.srcdoc = '<p>frame</p>'; document.body.append(f); })()""")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and browser.evaluate(
            "document.getElementById('probe').contentDocument?.readyState") != "complete":
        time.sleep(0.05)
    assert browser.evaluate("typeof document.getElementById('probe').contentWindow.__jevVitals") == "undefined"


def test_paint_metrics_are_not_reported_for_a_page_hidden_while_loading(lab, monkeypatch):
    collector, browser = lab
    hidden = {"ttfb": 5, "fcp": 900, "lcp": 1200, "lcp_element": "h1", "cls": 0, "tbt_approx": 0, "loaf_count": 0,
              "visibility_state_at_load": "hidden", "load_ms": 50}
    monkeypatch.setattr(collector, "vitals", lambda browser: dict(hidden))
    record = collector.collect(browser, stage="x", page_id="p1")
    assert record["vitals"]["fcp"] is None and record["vitals"]["lcp"] is None and record["vitals"]["cls"] is None
    assert record["vitals"]["ttfb_source"] == "cdp" and 0 <= record["vitals"]["ttfb"] < 150  # the network still tells
    assert any("hidden" in note for note in record["notes"])


class LossyTransport:
    """A DirectTransport that says it may drop events, like the Browser Harness daemon."""

    kind, lossy = "harness", True

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)


class DroppingTransport(LossyTransport):
    """A lossy transport that lost the main document's request."""

    def events(self, session_id=None, method_prefix=None):
        return [e for e in self.inner.events(session_id, method_prefix) if not (
            e["method"] == "Network.requestWillBeSent" and e["params"].get("type") == "Document")]


def lossy_record(chromium, shop_server, transport, profile):
    context = new_context(transport)
    collector = PageCollector(transport, None, None, profile=profile, context_id=context, screenshots=False)
    collector.net_quiet_s = collector.lcp_quiet_s = 0.3
    browser = collector.open(shop_server.url("shop/index.html"))
    try:
        return collector.collect(browser, stage="home", page_id="p1")
    finally:
        browser.close()
        close_context(transport, context)
        transport.inner.close()


def test_lossy_transports_fall_back_to_resource_timing(chromium, shop_server):
    record = lossy_record(chromium, shop_server, LossyTransport(DirectTransport(chromium.ws_url)), "desktop")
    network = record["network"]
    assert network["source"] == "resource_timing" and network["requests"] == 6 and network["bytes_js"] > 10_000
    assert any("Resource Timing" in note for note in record["notes"])
    # The Document request it did see is the main frame's current loader: its CDP timing stands.
    assert record["vitals"]["ttfb_source"] == "cdp" and record["vitals"]["ttfb"] is not None
    record = lossy_record(chromium, shop_server, DroppingTransport(DirectTransport(chromium.ws_url)), "desktop")
    assert record["vitals"]["ttfb_source"] == "navigation_timing" and record["vitals"]["ttfb"] is not None
    # Throttled: navigation.responseStart would leave out the emulated 150 ms round trip.
    record = lossy_record(chromium, shop_server, DroppingTransport(DirectTransport(chromium.ws_url)), "mobile")
    assert record["vitals"]["ttfb"] is None and "ttfb_source" not in record["vitals"]
    assert any(note.startswith("ttfb unavailable") for note in record["notes"])


def test_audit_reads_shadow_dom_json_ld_variants_and_price_formats(lab):
    collector, browser = lab
    browser.evaluate(r"""(() => {
      const box = document.createElement('section');
      box.innerHTML = `<p><span class="a">€ 1.234,56</span></p><p><span>1,234.56 EUR</span></p>
        <p><span>49,90 €</span></p><p><span>£12.50</span></p><p><span>1&nbsp;299,00&nbsp;€</span></p>
        <p><span>24,50 euro</span></p>
        <p><button><img alt="Aggiungi al carrello" src="img/shoe-a.svg" width="40"></button></p>`;
      document.querySelector('main').append(box);
      const host = document.createElement('buy-box');
      host.attachShadow({mode: 'open'}).innerHTML = '<button>Aggiungi al carrello</button><span>19,90 €</span>';
      document.querySelector('main').append(host);
      for (const src of ['{"@context":"https://schema.org","@graph":[{"@type":"Product","name":"A","offers":' +
          '{"@type":"Offer","price":"19.90","priceCurrency":"EUR"}},{"@type":"BreadcrumbList","itemListElement":[]}]}',
          '[{"@type":"Organization","name":"B"}]', '{"@type": "WebSite", "name": "C",}', '{not json']) {
        const s = document.createElement('script'); s.type = 'application/ld+json'; s.textContent = src;
        document.head.append(s);
      }
    })()""")
    audit = collector.audit(browser)
    values = {p["text"]: (p["value"], p["currency"]) for p in audit["prices"]}
    assert values["€ 1.234,56"] == (1234.56, "EUR") and values["1,234.56 EUR"] == (1234.56, "EUR")
    assert values["49,90 €"] == (49.9, "EUR") and values["£12.50"] == (12.5, "GBP")
    assert values["1 299,00 €"] == (1299.0, "EUR") and values["19,90 €"] == (19.9, "EUR")
    assert values["24,50 euro"] == (24.5, "EUR")
    assert sum(c["lexicon_hit"] == "add_to_cart" for c in audit["ctas"]) == 2  # shadow button and image button
    assert audit["a11y"]["shadow_roots_open"] == 1
    assert any(c["lexicon_hit"] == "add_to_cart" for c in audit["ctas"])
    types = audit["meta"]["jsonld_types"]
    assert types["Product"] == 1 and types["Organization"] == 1 and types["WebSite"] == 1
    assert audit["meta"]["jsonld_errors"] == 1 and audit["pdp"]["structured"]["price"] == 19.9


def test_audit_reads_prices_snippets_and_links_as_a_shopper_sees_them(lab):
    collector, browser = lab
    browser.evaluate(r"""(() => {
      for (const a of document.querySelectorAll('a')) if (/carrello|cart/i.test(a.textContent + a.href)) a.remove();
      const box = document.createElement('section');
      box.innerHTML = `<p><span>49,- €</span></p><p><span>CHF 1'299.00</span></p><p><span>49<sup>90</sup> €</span></p>
        <p><span>1’250,00 €</span></p>
        <p><button aria-label="Aggiungi Scarpa Aurora al carrello">Aggiungi</button></p>
        <div class="promo">Spedizione gra<b>tis</b> su tutti gli ordini<p>Il paragrafo annidato resta a parte.</p></div>
        <h3>Caffè Maestro 250 g</h3><p><a href="p.html?id=9">Maestro pasticcere</a></p>
        <p><a href="http://smartcart.example/collezione">Collezione estiva</a></p>`;
      document.querySelector('main').append(box);
    })()""")
    audit = collector.audit(browser)
    values = {p["text"]: p["value"] for p in audit["prices"]}
    assert values["49,- €"] == 49 and values["CHF 1'299.00"] == 1299 and values["49,90 €"] == 49.9
    assert values["1’250,00 €"] == 1250
    ctas = [s["text"] for s in audit["snippets"] if s["kind"] == "cta"]
    assert ctas == ["Aggiungi"]  # the visible text, not the accessible name
    texts = json.dumps(audit, ensure_ascii=False)
    assert "Spedizione gratis su tutti gli ordini" in texts and "gra tis" not in texts
    logos = set(audit["trust"]["payment_logos"])
    assert "maestro" not in logos and {"visa", "paypal"} <= logos
    assert not audit["nav"]["cart_link"]["present"]  # "cart." in a host name is not a cart URL


def test_a_login_box_in_the_header_is_not_an_account_gate(lab, shop_server):
    collector, browser = lab
    collector.load_page(browser, shop_server.url("shop/checkout.html?guest=1"), stage="checkout_entry", page_id="p1")
    browser.evaluate("""document.querySelector('header .bar').insertAdjacentHTML('beforeend',
      '<form class="login"><label>Password <input type="password" name="pwd" required></label></form>')""")
    forms = collector.audit(browser)["forms"]
    assert not forms["password_present"] and not forms["login_required"]
    browser.evaluate("""document.body.insertAdjacentHTML('beforeend', '<div role="dialog" aria-modal="true" ' +
      'style="position:fixed;inset:0;background:#fff"><h2>Accedi per continuare</h2>' +
      '<label>Password <input type="password" name="pwd2" required></label></div>')""")
    forms = collector.audit(browser)["forms"]
    assert forms["password_present"] and forms["login_required"]  # a blocking login dialog is a gate


def test_links_are_read_beyond_the_element_cap(lab):
    collector, browser = lab
    before = collector.audit(browser)["trust"]
    browser.evaluate("""(() => { const d = document.createElement('div');
      d.innerHTML = '<span></span>'.repeat(41000); document.querySelector('main').append(d); })()""")
    audit = collector.audit(browser)
    assert audit["truncated"] and audit["trust"]["policy_count"] == before["policy_count"] == 4
    assert audit["trust"]["policy_links"]["returns"]["href"].endswith("/shop/resi.html")


def test_audit_stays_bounded_on_a_huge_page(lab):
    collector, browser = lab
    browser.evaluate("""(() => { const ul = document.createElement('ul'); let html = '';
      for (let i = 0; i < 4000; i++) html += `<li><a href="p.html?id=${i}"><img alt="P${i}" width="40" height="40"
        src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></a><h3><a href="p.html?id=${i}">Prodotto ${i}</a></h3>
        <span>${(i % 90) + 10},90 €</span> <a href="p.html?id=${i}#info">Scopri di più</a></li>`;
      ul.innerHTML = html; document.querySelector('main').append(ul); })()""")
    started = time.monotonic()
    audit = collector.audit(browser)
    assert time.monotonic() - started < 15
    assert audit["products"]["cards_count"] >= 300 and len(audit["products"]["cards"]) == 40
    assert len(audit["prices"]) == 80 and len(audit["ctas"]) <= 40 and len(audit["snippets"]) <= 40
    assert audit["nav"]["generic_label_share"] > 0.3
    assert len(json.dumps(audit)) < 200_000


def test_audit_expression_is_a_single_function_of_the_lexicon():
    assert AUDIT_JS.lstrip().startswith("//") and "(lexicon) =>" in AUDIT_JS
    assert "window.__jevVitals" in VITALS_JS and "addEventListener('click'" not in AUDIT_JS


# ---------------------------------------------------------------- vitals: tickers, pollers, back-forward cache

def test_appends_to_the_body_never_make_it_a_ticker(lab):
    collector, browser = lab
    collector.collect(browser, stage="x", page_id="p1")
    time.sleep(2.2)  # widgets that append to the body more than 2 s after load: spontaneous changes
    for i in range(3):
        browser.evaluate(f"document.body.append(Object.assign(document.createElement('div'), {{textContent: 'w{i}'}}))")
        time.sleep(0.1)
    mark = collector.mark(browser)
    browser.evaluate("document.body.append(Object.assign(document.createElement('aside'), {textContent: 'Fatto'}))")
    since = collector.since(browser, mark)
    assert since["mutations"] == 1 and since["first_response_ms"] is not None
    assert browser.evaluate("window.__jevVitals.activity().tickers") == 0


def test_a_pages_own_polling_is_not_a_response(lab, shop_server):
    collector, browser = lab
    browser.call("Page.addScriptToEvaluateOnNewDocument", source=(
        "addEventListener('load', () => setInterval(() => fetch('img/shoe-a.svg?beat=' + Math.random())"
        ".then(r => r.text()), 700))"))
    collector.load_page(browser, shop_server.url("shop/contatti.html"), stage="x", page_id="p1")
    # The third heartbeat more than 2 s after load makes its URL a poller.
    assert wait_for(lambda: browser.evaluate("window.__jevVitals.activity().pollers") == 1)
    mark = collector.mark(browser)
    time.sleep(1.5)  # an action that changed nothing
    idle = collector.since(browser, mark)
    assert idle["requests"] == 0 and idle["first_response_ms"] is None and idle["requests_total"] >= 1
    assert idle["first_request_ms"] is None
    mark = collector.mark(browser)
    browser.evaluate("fetch('img/shoe-b.svg?add=1').then(r => r.text())")  # what an action fetches still counts
    assert wait_for(lambda: collector.since(browser, mark)["requests"] == 1)
    fetched = collector.since(browser, mark)  # ruling 8: a request alone is no visible response
    assert fetched["first_request_ms"] is not None and fetched["first_response_ms"] is None


def test_a_request_before_the_visible_change_is_reported_apart(lab, shop_server):
    """Ruling 8: first_response_ms is the first mutation or navigation; the click's beacon is first_request_ms."""
    collector, browser = lab
    collector.load_page(browser, shop_server.url("shop/contatti.html"), stage="x", page_id="p1")
    mark = collector.mark(browser)
    browser.evaluate("fetch('img/shoe-a.svg?beacon=1').then(r => r.text()); setTimeout(() => document.body.append("
                     "Object.assign(document.createElement('p'), {textContent: 'Fatto'})), 400)")
    assert wait_for(lambda: (lambda s: s["mutations"] >= 1 and s["requests"] >= 1)(collector.since(browser, mark)))
    since = collector.since(browser, mark)
    assert since["first_request_ms"] < 300 and since["first_response_ms"] >= 380, since


class PlainServer:
    """A local server without Cache-Control: no-store, so its pages may enter the back-forward cache, with an
    EventSource stream that sends a message every 0.3 s and /slow?ms=N, which answers after N ms. fhost.html embeds
    fwidget.html from localhost: another site, so another renderer. workers.html starts a dedicated worker (which
    fetches a page), a shared worker and a service worker."""

    PAGES = {
        "/a.html": "<!doctype html><html lang=it><title>A</title><h1>Pagina A</h1><p>Prima pagina.</p>"
                   "<a href='b.html'>Vai a B</a>",
        "/b.html": "<!doctype html><html lang=it><title>B</title><h1>Pagina B</h1><p>Seconda pagina.</p>",
        "/live.html": "<!doctype html><html lang=it><title>Live</title><h1>Disponibilità in tempo reale</h1>"
                      "<p id=s>…</p><script>new EventSource('stream').onmessage = m => "
                      "{ document.getElementById('s').textContent = m.data; };</script>",
        "/fhost.html": "<!doctype html><html lang=it><title>Recensioni</title><a id=t href='b.html' "
                       "style='position:fixed;top:0;left:0;width:200px;height:60px;background:#fff;z-index:9'>"
                       "Vai a B</a><h1 style='margin-top:80px'>Le vostre recensioni</h1>"
                       "<iframe id=w width=400 height=200></iframe>"
                       "<script>document.getElementById('w').src = location.href.replace('//127.0.0.1:', "
                       "'//localhost:').replace('fhost.html', 'fwidget.html');</script>",
        "/fwidget.html": "<!doctype html><html lang=it><title>Widget</title><p>Widget recensioni</p><script>"
                         "console.error('widget: recensioni non disponibili');"
                         "setTimeout(() => { throw new Error('widget rotto'); });</script>",
        "/workers.html": "<!doctype html><html lang=it><title>Lavoratori</title><h1>Ricerca veloce</h1><script>"
                         "window.state = {};"
                         "new Worker('w.js').onmessage = m => { window.state.worker = m.data; };"
                         "const shared = new SharedWorker('sh.js');"
                         "shared.port.onmessage = m => { window.state.shared = m.data; };"
                         "navigator.serviceWorker.register('sw.js').then(() => navigator.serviceWorker.ready)"
                         ".then(() => { window.state.service = 'ready'; });</script>",
        "/w.js": "fetch('b.html?from=worker').then(r => r.text()).then(t => postMessage('fetched ' + t.length));",
        "/sh.js": "onconnect = e => e.ports[0].postMessage('connected');",
        "/sw.js": "self.addEventListener('install', () => self.skipWaiting());",
    }

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        pages = self.PAGES

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    try:
                        for i in range(200):
                            self.wfile.write(f"data: {i} pezzi\n\n".encode())
                            self.wfile.flush()
                            time.sleep(0.3)
                    except OSError:
                        pass
                    return
                if path == "/slow":
                    time.sleep(int(self.path.split("ms=")[-1].split("&")[0]) / 1000)
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    try:
                        self.wfile.write(b"ok")
                    except OSError:
                        pass
                    return
                body = pages.get(path, "").encode()
                self.send_response(200 if body else 404)
                self.send_header("Content-Type", "text/javascript" if path.endswith(".js") else
                                 "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, path):
        return f"http://127.0.0.1:{self.server.server_port}/{path}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def plain():
    server = PlainServer()
    yield server
    server.close()


def test_a_back_forward_cache_restore_is_a_navigation_and_has_no_load_timings_of_its_own(lab, plain):
    collector, browser = lab
    first = collector.load_page(browser, plain.url("a.html"), stage="x", page_id="a1")
    assert first["vitals"]["load_ms"] is not None and not first["vitals"].get("soft_navigation")
    collector.load_page(browser, plain.url("b.html"), stage="x", page_id="b1")
    mark = collector.mark(browser)
    browser.evaluate("history.back()")
    assert wait_for(lambda: str(browser.evaluate("location.href") or "").endswith("/a.html"))
    # The profile, vitals.js and the frame auto-attach leave pages eligible for the back-forward cache.
    assert wait_for(lambda: browser.evaluate("window.__jevVitals.activity().restored_at") is not None)
    since = collector.since(browser, mark)
    record = collector.collect(browser, stage="x", page_id="a2")
    assert since["navigations"] == 1 and since["first_response_ms"] is not None and since["mutations"] == 0
    assert record["final_url"].endswith("/a.html") and record["vitals"]["settled_reason"] == "quiet"
    # The same document as record a1: its load timings are a1's.
    assert record["vitals"]["soft_navigation"] is True and record["loaded_ms"] is None
    for key in ("ttfb", "fcp", "lcp", "cls", "load_ms", "dcl_ms", "tbt_approx"):
        assert record["vitals"][key] is None, key
    assert [n for n in record["notes"] if n.startswith("same_document:")] == [
        "same_document: client-side route change or back-forward cache restore of a1; its load timings belong to "
        "that record, network and errors count what happened since the previous record"]


def test_a_client_side_route_change_has_no_load_timings_of_its_own(lab, shop_server):
    collector, browser = lab
    home = collector.load_page(browser, shop_server.url("shop/index.html"), stage="home", page_id="home")
    assert home["vitals"]["lcp"] is not None
    browser.evaluate("""(() => { const a = document.createElement('a'); a.href = 'product.html'; a.id = 'spa';
      a.textContent = 'Scarpa Aurora';
      a.style.cssText = 'position:fixed;top:0;left:0;width:200px;height:60px;z-index:99999;background:#fff';
      a.onclick = async e => { e.preventDefault();
        const doc = new DOMParser().parseFromString(await (await fetch('product.html')).text(), 'text/html');
        document.querySelector('main').replaceWith(doc.querySelector('main')); document.title = doc.title;
        history.pushState({}, '', 'product.html'); };
      document.body.append(a); })()""")
    mark = collector.mark(browser)
    for kind in ("mousePressed", "mouseReleased"):
        collector.transport.call("Input.dispatchMouseEvent", browser.session, type=kind, x=50, y=20, button="left",
                                 clickCount=1)
    assert wait_for(lambda: str(browser.evaluate("location.href") or "").endswith("/product.html"))
    assert collector.since(browser, mark)["navigations"] == 1
    record = collector.collect(browser, stage="pdp", page_id="pdp")
    vitals = record["vitals"]
    assert record["final_url"].endswith("/shop/product.html") and record["classification"]["type"] == "pdp"
    assert vitals["soft_navigation"] is True and "ttfb_source" not in vitals and record["status_code"] is None
    for key in ("ttfb", "fcp", "lcp", "lcp_element", "cls", "load_ms", "dcl_ms", "tbt_approx", "loaf_count"):
        assert vitals[key] is None, key
    assert any(n.startswith("same_document:") and "home" in n for n in record["notes"])
    assert 1 <= record["network"]["requests"] < home["network"]["requests"] + 3  # the route's fetch and its images


# ---------------------------------------------------------------- cross-site frames

def test_cross_site_frames_are_counted_and_throttled_with_the_page(chromium, shop_server):
    transport = DirectTransport(chromium.ws_url)
    context = new_context(transport)
    collector = PageCollector(transport, None, None, profile="mobile", context_id=context, screenshots=False)
    collector.net_quiet_s = collector.lcp_quiet_s = 0.3
    before = len(shop_server.requests)
    browser = collector.open(shop_server.url("shop/frames.html"))
    try:
        record = collector.collect(browser, stage="x", page_id="p1")
        served = [r["path"] for r in shop_server.requests[before:]]
        frames = [c for c, v in collector._children.items() if v["root"] == browser.session]
        assert frames, "the cross-site iframe was not attached"
        inner = transport.call("Runtime.evaluate", frames[0], returnByValue=True, expression=(
            "JSON.stringify({ua: navigator.userAgent, slowest: Math.min(...performance.getEntriesByType('resource')"
            ".map(r => r.duration))})"))
        frame = json.loads(inner["result"]["value"])
    finally:
        browser.close()
        close_context(transport, context)
        transport.close()
    network = record["network"]
    assert "/shop/category.html" in served and served.count("/shop/shop.js") == 1
    assert network["requests"] == len(served), served
    assert network["by_type"]["script"]["bytes"] >= (FIXTURES / "shop/shop.js").stat().st_size
    assert network["third_party_domains"] == ["localhost"] and network["bytes_third_party"] > 20_000
    assert "Mobile" in frame["ua"] and frame["slowest"] >= 140  # the profile's 150 ms round trip in the frame too


# ---------------------------------------------------------------- settle: requests in flight

class HeldRequests:
    """A transport that pauses requests matching a pattern (CDP Fetch) and lets each go after `delay` s, or never."""

    def __init__(self, inner, delay):
        self.inner, self.delay, self.held = inner, delay, []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def events(self, session_id=None, method_prefix=None):
        events = self.inner.events(session_id, method_prefix)
        for event in [e for e in events if e["method"] == "Fetch.requestPaused"]:
            self.held.append(event["params"]["request"]["url"])
            if self.delay is not None:
                threading.Timer(self.delay, self.inner.call, args=("Fetch.continueRequest", event["session_id"]),
                                kwargs={"requestId": event["params"]["requestId"]}).start()
        return [e for e in events if e["method"] != "Fetch.requestPaused"]


def held_listing(chromium, shop_server, delay):
    transport = HeldRequests(DirectTransport(chromium.ws_url), delay)
    context = new_context(transport)
    collector = PageCollector(transport, None, None, profile="desktop", context_id=context, screenshots=False,
                              settle_timeout_s=15)
    collector.lcp_quiet_s = 0.5
    browser = collector.open(shop_server.url("shop/resi.html"))
    try:
        collector.collect(browser, stage="x", page_id="p0")
        browser.call("Fetch.enable", patterns=[{"urlPattern": "*api/products.json*", "requestStage": "Request"}])
        started = time.monotonic()
        record = collector.load_page(browser, shop_server.url("shop/listing-api.html"), stage="plp", page_id="p1")
        return record, time.monotonic() - started, transport.held
    finally:
        browser.close()
        close_context(transport, context)
        transport.inner.close()


def test_a_slow_api_holds_the_settle_until_the_listing_exists(chromium, shop_server):
    record, took, held = held_listing(chromium, shop_server, 2.5)
    assert held and held[0].endswith("api/products.json")
    assert record["vitals"]["settled_reason"] == "quiet" and took >= 2.5
    assert record["audit"]["products"]["cards_count"] == 24 and record["classification"]["type"] == "plp"
    assert not [n for n in record["notes"] if n.startswith("settle:")]


def test_a_request_that_never_ends_stops_holding_after_five_seconds(chromium, shop_server):
    record, took, held = held_listing(chromium, shop_server, None)
    assert held and record["vitals"]["settled_reason"] == "quiet"
    assert 5 <= took < 5 + 1.5 + 2.5
    assert record["audit"]["products"]["cards_count"] == 0
    assert [n for n in record["notes"] if n.startswith("settle:")] == [
        f"settle: 1 request still in flight (> 5 s): {shop_server.url('shop/api/products.json')}"]


def test_an_event_stream_does_not_hold_the_settle(lab, plain):
    collector, browser = lab
    collector.net_quiet_s, collector.settle_timeout_s = 1.0, 12.0
    started = time.monotonic()
    record = collector.load_page(browser, plain.url("live.html"), stage="x", page_id="p1")
    assert record["vitals"]["settled_reason"] == "quiet" and time.monotonic() - started < 4
    assert "pezzi" in browser.evaluate("document.getElementById('s').textContent")
    assert not [n for n in record["notes"] if n.startswith("settle:")]


def test_a_fetch_whose_body_is_never_read_does_not_hold_the_settle(lab, shop_server):
    """Architect ruling 1: Chrome reports no end for a response nobody reads (a fire-and-forget ping); once its headers
    came and no data followed for net_quiet_s it stops holding, silently, well before the 5 s cap."""
    collector, browser = lab
    collector.net_quiet_s, collector.settle_timeout_s = 1.0, 12.0
    browser.call("Page.addScriptToEvaluateOnNewDocument", source="fetch('img/shoe-a.svg?ping=1')")
    started = time.monotonic()
    record = collector.load_page(browser, shop_server.url("shop/contatti.html"), stage="x", page_id="p1")
    assert record["vitals"]["settled_reason"] == "quiet" and time.monotonic() - started < 4
    assert not [n for n in record["notes"] if n.startswith("settle:")]


def frame_collector(chromium, profile="desktop"):
    transport = DirectTransport(chromium.ws_url)
    context = new_context(transport)
    collector = PageCollector(transport, None, None, profile=profile, context_id=context, screenshots=False)
    return transport, context, collector


def test_requests_of_the_previous_document_do_not_hold_the_next_settle(chromium, plain):
    """A cross-site frame's request in flight when the page navigates never ends (its renderer goes away): it is the
    previous document's, so it neither holds the next record's settle nor shows in its notes or network."""
    transport, context, collector = frame_collector(chromium)
    browser = collector.open(plain.url("fhost.html"))
    try:
        first = collector.collect(browser, stage="x", page_id="p1")
        frames = [c for c, v in collector._children.items() if v["root"] == browser.session]
        assert frames, "the cross-site iframe was not attached"
        transport.call("Runtime.evaluate", frames[0], expression="fetch('/slow?ms=8000'); 1")
        time.sleep(0.5)  # the widget's request is in flight
        for kind in ("mousePressed", "mouseReleased"):
            transport.call("Input.dispatchMouseEvent", browser.session, type=kind, x=60, y=20, button="left",
                           clickCount=1)
        started = time.monotonic()
        record = collector.collect(browser, stage="x", page_id="p2")
        took = time.monotonic() - started
    finally:
        collector.close(browser)
        close_context(transport, context)
        transport.close()
    assert record["final_url"].endswith("/b.html") and record["vitals"]["settled_reason"] == "quiet"
    assert took < 4, took  # net quiet 1.5 s and LCP quiet 2 s, not the 5 s cap
    assert not [n for n in record["notes"] if n.startswith("settle:")]
    assert record["network"]["requests"] == 1 and not record["network"]["third_party_domains"]
    # A cross-site frame's errors count with its page, as a same-site frame's do.
    assert first["errors"]["page"] >= 1 and first["errors"]["console"] >= 2
    assert any("recensioni non disponibili" in sample for sample in first["errors"]["samples"])
    assert record["errors"]["console"] == 0


def test_workers_run_and_their_scripts_do_not_hold_the_settle(chromium, plain):
    """waitForDebuggerOnStart pauses every worker a page starts, even one the auto-attach filter leaves out: each is
    attached and resumed. A dedicated worker's requests count with the page; a worker's own script, whose end is
    reported on the worker's session (a shared worker's on none the collector reads), never holds the settle."""
    transport, context, collector = frame_collector(chromium)
    browser = collector.open(plain.url("workers.html"))
    try:
        started = time.monotonic()
        record = collector.collect(browser, stage="x", page_id="p1")
        took = time.monotonic() - started
        assert wait_for(lambda: len(browser.evaluate("window.state") or {}) == 3, timeout=5)
        state = browser.evaluate("window.state")
    finally:
        collector.close(browser)
        close_context(transport, context)
        transport.close()
    size = len(PlainServer.PAGES["/b.html"].encode())
    assert state == {"worker": f"fetched {size}", "shared": "connected", "service": "ready"}
    assert record["vitals"]["settled_reason"] == "quiet" and took < 4.5, took
    assert not [n for n in record["notes"] if n.startswith("settle:")]
    assert record["network"]["by_type"]["fetch"]["requests"] == 1  # the dedicated worker's own request


def test_a_dead_frame_watcher_is_started_again(chromium, shop_server):
    """Architect ruling 2: the watcher ends on any error, and the collector starts a new one when it needs it; a dead
    watcher would leave every new cross-site iframe paused (and the page's load event waiting)."""
    transport, context, collector = frame_collector(chromium)
    collector.net_quiet_s = collector.lcp_quiet_s = 0.3
    browser = collector.open(shop_server.url("shop/resi.html"))
    session = browser.session
    try:
        collector.collect(browser, stage="x", page_id="p0")
        pump = collector._pump_frames

        def crash(**kwargs):
            collector._pump_frames = pump
            raise RuntimeError("the watcher crashed")

        collector._pump_frames = crash
        assert wait_for(lambda: collector._watcher is None or not collector._watcher.is_alive())
        started = time.monotonic()
        record = collector.load_page(browser, shop_server.url("shop/frames.html"), stage="x", page_id="p1")
        took = time.monotonic() - started
        frames = [c for c, v in collector._children.items() if v["root"] == session]
        assert not collector._busy()  # idle between records: the watcher polls slowly
    finally:
        collector.close(browser)
    try:
        assert session not in collector._roots and session not in collector._pages
        assert not [c for c, v in collector._children.items() if v["root"] == session]
    finally:
        close_context(transport, context)
        transport.close()
    assert frames and record["vitals"]["settled_reason"] == "quiet" and took < 8
    assert record["network"]["third_party_domains"] == ["localhost"]


def test_a_click_into_a_prefetched_document_reports_no_load_timings(chromium, shop_server):
    """Architect ruling 4: a page timing is a cold navigation. Prerendering is disallowed; a document the shop's
    speculation rules prefetched before the click (outside the profile's network emulation) gets no load timings,
    and load_page() of the same URL, a browser navigation, is a cold load."""
    transport, context, collector = frame_collector(chromium, profile="mobile")
    target = "/shop/resi.html?speculation=1"
    since = len(shop_server.requests)  # the log is session-wide: only this test's prefetch counts
    browser = collector.open(shop_server.url("shop/speculation.html"))
    try:
        assert collector.applied_profile["prerendering"] == "disallowed"
        first = collector.collect(browser, stage="pdp", page_id="p1")
        assert wait_for(lambda: any(r["path"] == target for r in shop_server.requests[since:]))  # the prefetch
        time.sleep(0.5)  # its response reaches the prefetch cache
        before = len(shop_server.requests)
        for kind in ("mousePressed", "mouseReleased"):
            transport.call("Input.dispatchMouseEvent", browser.session, type=kind, x=60, y=20, button="left",
                           clickCount=1)
        assert wait_for(lambda: str(browser.evaluate("location.href") or "").endswith("speculation=1"))
        record = collector.collect(browser, stage="cart", page_id="p2")
        clicked = [r["path"] for r in shop_server.requests[before:]]
        cold = collector.load_page(browser, shop_server.url(target.lstrip("/")), stage="cart", page_id="p3")
        served = [r["path"] for r in shop_server.requests[before:]]
    finally:
        collector.close(browser)
        close_context(transport, context)
        transport.close()
    assert first["audit"]["meta"]["speculation_rules"] is True
    assert any(n.startswith("speculation_rules:") for n in first["notes"])
    vitals = record["vitals"]
    assert record["final_url"].endswith(target) and target not in clicked  # served from the prefetch cache
    assert vitals["speculative"] == "prefetch" and record["loaded_ms"] is None and "ttfb_source" not in vitals
    for key in ("ttfb", "fcp", "lcp", "lcp_element", "cls", "tbt_approx", "dcl_ms", "load_ms"):
        assert vitals[key] is None, key
    assert [n for n in record["notes"] if n.startswith("speculative_navigation:")]
    assert served.count(target) == 1 and "speculative" not in cold["vitals"]
    assert cold["vitals"]["ttfb_source"] == "cdp" and cold["vitals"]["ttfb"] >= 140  # the profile's 150 ms RTT
    assert cold["vitals"]["lcp"] is not None and cold["vitals"]["activation_start"] == 0


# ---------------------------------------------------------------- audit.js on reviewed markup

def page_audit(lab, html, path=None, *, light_dom=True):
    """Replace the lab page's document with html (scripts run), optionally at another same-origin path; audit it.
    light_dom: every quote is in body.innerText (which does not reach into shadow roots)."""
    collector, browser = lab
    browser.call("Page.setDocumentContent", frameId=browser.target, html=html)
    if path:
        browser.evaluate(f"history.replaceState(null, '', {json.dumps(path)})")
    audit = collector.audit(browser)
    body = browser.evaluate("document.body.innerText.replace(/\\s+/g, ' ').trim()")
    for s in audit["snippets"] if light_dom else ():  # every quote is text the shopper reads in one piece
        assert s["text"] in body, s
    return audit


CONFIRMSHAMING = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Borsa Luna</title>
<style>.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}</style></head>
<body><header><a href="/">Negozio</a></header><main><h1>Borsa Luna in pelle</h1>
<div class="a-price"><span class="sr">49,90 €</span><span
aria-hidden="true"><span>49<span>,</span></span><span>90</span><span>€</span></span></div>
<div class="box">Resi gratuiti entro 30 giorni <p>(vedi dettagli)</p> per tutti gli ordini spediti in Italia.</div>
<p><button style="background:#000;color:#fff;padding:14px 30px">Aggiungi al carrello</button></p></main>
<div role="dialog" aria-modal="true"
style="position:fixed;inset:20% 20%;background:#fff;border:1px solid #000;padding:20px">
<h2>Iscriviti alla newsletter e ottieni il 10% di sconto sul tuo primo ordine</h2>
<button aria-label="Chiudi finestra">×</button><button aria-label="Chiudi">No grazie, preferisco pagare di più</button>
<a href="#" aria-label="Rifiuta offerta">Non mi interessa risparmiare</a></div></body></html>"""


def no_results_page(heading: str, block: str) -> str:
    cards = "".join(f'<li class="product-card"><a href="/p/{i}"><img src="data:," width="160" height="160" alt="">'
                    f'<h3>{name}</h3></a><span class="price">{19 + i},90 €</span></li>'
                    for i, name in enumerate(("Borraccia termica", "Felpa con cappuccio", "Calzini sportivi",
                                              "Cappello di lana", "Guanti touch", "Fascia running")))
    return (f'<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Ricerca</title></head><body>'
            f'<header><a href="/">Negozio</a> <a href="/carrello">Carrello (0 articoli)</a></header><main>'
            f'<h1>{heading}</h1><p>Prova con altre parole.</p><h2>{block}</h2><ul class="products">{cards}</ul>'
            f'</main></body></html>')


@pytest.mark.parametrize("heading, block, expected", [
    ("0 risultati per «zaino impermeabile»", "Ti potrebbero interessare", "0 risultati per «zaino impermeabile»"),
    ("La tua ricerca non ha prodotto risultati", "I più venduti", "La tua ricerca non ha prodotto risultati"),
    ("No results for “waterproof backpack”", "You may also like", "No results for “waterproof backpack”"),
    ("Risultati per «borraccia»", "Prodotti trovati", None),  # the header's "0 articoli" is a cart, not a search
])
def test_a_no_results_statement_is_read_above_a_grid_of_recommendations(lab, monkeypatch, heading, block,
                                                                         expected):
    from jev_ultrafast.engagement import oracles

    audit = page_audit(lab, no_results_page(heading, block), "/search?q=zaino+impermeabile")
    assert audit["filters"]["no_results_text"] == expected
    assert classify(audit, audit["url"])["type"] == "plp" and audit["products"]["cards_count"] == 6
    monkeypatch.setattr(oracles, "read_page", lambda browser, collector: (audit, classify(audit, audit["url"])))
    run = {"site": {"start_url": audit["url"]}}
    for params in ({}, {"query": "zaino impermeabile"}):
        result = oracles.verify("search_results_shown", params, browser=None, collector=None, run=run)
        assert result["passed"] is (expected is None and not params), (heading, params)  # never the URL's echo


def test_the_buttons_of_product_cards_are_marked_and_count_once(lab):
    from test_engagement_checks import one, page, run_of

    from jev_ultrafast.engagement import checks

    button = '<button style="background:#333;color:#fff;border:0;padding:10px 16px">Aggiungi</button>'
    cards = "".join(f'<li class="card"><a href="/p/{n}.html"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" '
                    f'width="120" height="90" alt="Calza {n}">Calza sportiva {n}</a><span class="price">{9 + n},90 €'
                    f'</span>{button}</li>' for n in range(3))
    html = ('<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Scarpa Aurora</title></head><body>'
            '<main style="display:grid;grid-template-columns:2fr 1fr"><div><h1>Scarpa Aurora</h1><p>89,90 €</p>'
            '<button style="background:#060;color:#fff;border:0;padding:14px 40px;font-size:18px">Aggiungi al '
            f'carrello</button></div><aside><h2>Completa il look</h2><ul>{cards}</ul></aside></main></body></html>')
    audit = page_audit(lab, html, "/scarpa-aurora")
    shown = [(c["label"], c["in_card"]) for c in audit["ctas"] if c["primary_like"] and c["above_fold"]]
    assert sorted(shown) == [("Aggiungi", True)] * 3 + [("Aggiungi al carrello", False)]
    row = one(checks.observations(run_of([page("pdp", audit=audit)])), "CCL.PRIMARY_CTA_COUNT", stage="pdp")
    assert row["value"] == 2 and row["evidence"]["card_buttons"] == 3


def test_overlay_buttons_are_read_by_their_visible_text(lab):
    audit = page_audit(lab, CONFIRMSHAMING)
    overlay = audit["overlays"][0]
    assert overlay["kind"] == "newsletter" and overlay["has_close"] and not overlay["reject_labels"]
    assert overlay["decline_labels"] == ["No grazie, preferisco pagare di più", "Non mi interessa risparmiare"]
    assert [b["label"] for b in overlay["buttons"]][:2] == ["Chiudi finestra", "Chiudi"]  # accessible names kept
    declines = [s["text"] for s in audit["snippets"] if s["kind"] == "modal_decline"]
    assert declines == ["No grazie, preferisco pagare di più", "Non mi interessa risparmiare"]  # never "×" nor aria


def test_screen_reader_text_is_hidden_and_nested_blocks_split_quotes(lab):
    audit = page_audit(lab, CONFIRMSHAMING)
    assert [p["value"] for p in audit["prices"]] == [49.9]  # the clipped 1x1 px copy is not a second price
    assert audit["pdp"]["price"]["rect"]["w"] > 2
    quotes = json.dumps(audit["snippets"], ensure_ascii=False)
    assert "Resi gratuiti entro 30 giorni per tutti" not in quotes  # never joined across "(vedi dettagli)"
    assert {"Resi gratuiti entro 30 giorni"} <= {s["text"] for s in audit["snippets"] if s["kind"] == "returns_policy"}
    assert audit["pdp"]["delivery_text"] is None  # "spediti in Italia" states no delivery time


@pytest.mark.parametrize("accept, reject, manage, banner", [
    ("Accetta tutti", "Accetta solo i necessari", "Personalizza", "Usiamo i cookie per migliorare la tua esperienza."),
    ("Accept all cookies", "Accept only essential cookies", "Cookie settings", "We use cookies to improve the shop."),
])
def test_only_necessary_cookie_buttons_are_rejections(lab, accept, reject, manage, banner):
    audit = page_audit(lab, f"""<!doctype html><html><head><meta charset="utf-8"><title>Negozio</title></head>
      <body><main><h1>Negozio</h1><p>Benvenuto.</p></main>
      <div style="position:fixed;bottom:0;left:0;right:0;background:#eee;padding:10px"><p>{banner}</p>
      <button style="background:#0a0;color:#fff;padding:12px 30px">{accept}</button>
      <button style="padding:4px">{reject}</button><button>{manage}</button></div></body></html>""")
    consent = audit["overlays"][0]
    assert consent["kind"] == "consent"
    assert (consent["accept_labels"], consent["reject_labels"], consent["manage_labels"]) == ([accept], [reject],
                                                                                              [manage])
    assert consent["accept_area"] > consent["reject_area"]
    assert all(b["rect"]["w"] > 0 and b["rect"]["y"] > 0 for b in consent["buttons"])  # deception scrolls to manage


GUEST_CHECKOUT = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Checkout</title></head>
<body><header><a href="/">Negozio</a></header><main><h1>Checkout</h1>
<p>Hai già un account? <a href="/account/login">Accedi per completare l'acquisto più velocemente</a></p>
<form method="post" action="/checkout/save">
<label>Email <input type="email" name="email" autocomplete="email"></label>
<label>Nome <input name="firstname" autocomplete="given-name"></label>
<label>Cognome <input name="lastname" autocomplete="family-name"></label>
<label>Indirizzo <input name="street" autocomplete="address-line1"></label>
<label>CAP <input name="postcode" autocomplete="postal-code"></label>
<button type="submit">Continua</button></form></main></body></html>"""


def test_invitations_to_log_in_faster_are_not_an_account_gate(lab):
    forms = page_audit(lab, GUEST_CHECKOUT, "/checkout/")["forms"]
    assert not forms["password_present"] and not forms["login_required"] and forms["visible"] == 5
    forms = page_audit(lab, GUEST_CHECKOUT.replace(
        "Hai già un account? <a href=\"/account/login\">Accedi per completare l'acquisto più velocemente</a>",
        'Have an account? <a href="/account/login">Log in</a> to check out faster.'), "/checkout/")["forms"]
    assert not forms["login_required"]
    gate = page_audit(lab, GUEST_CHECKOUT.replace("Hai già un account?", "Accedi per continuare. Hai già un account?")
                      .replace('<label>Email <input type="email"', '<label>Password <input type="password" required>'
                               '</label><label>Email <input type="email"'), "/checkout/")["forms"]
    assert gate["password_present"] and gate["login_required"]  # a login form holding every field is a gate


WOO_CART = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Carrello – Bottega Rossi</title></head>
<body><header><a href="/">Bottega Rossi</a></header><main><article><h1>Carrello</h1>
<form class="woocommerce-cart-form" action="/carrello/" method="post"><table class="shop_table cart">
<thead><tr><th></th><th>Prodotto</th><th>Prezzo</th><th>Quantità</th><th>Subtotale</th></tr></thead>
<tr class="cart_item"><td>
<a href="/carrello/?remove_item=abc" class="remove" aria-label="Rimuovi questo elemento">×</a></td>
<td><a href="/prodotto/borsa-luna/">Borsa Luna</a></td><td>129,00 €</td>
<td><label for="q1">Borsa Luna quantità</label><input type="number" id="q1" name="cart[abc][qty]" value="1"></td>
<td>129,00 €</td></tr>
<tr class="cart_item"><td>
<a href="/carrello/?remove_item=def" class="remove" aria-label="Rimuovi questo elemento">×</a></td>
<td><a href="/prodotto/protezione/">Protezione spedizione</a></td><td>2,90 €</td>
<td><label for="q2">Protezione spedizione quantità</label>
<input type="number" id="q2" name="cart[def][qty]" value="1"></td>
<td>2,90 €</td></tr></table><button type="submit" name="update_cart">Aggiorna carrello</button></form>
<div class="cart_totals"><h2>Totale carrello</h2><table><tr><th>Subtotale</th><td>131,90 €</td></tr>
<tr><th>Spedizione</th><td>Spedizione gratuita</td></tr><tr><th>Totale</th><td>131,90 €</td></tr></table>
<div class="wc-proceed-to-checkout"><a href="/pagamento/" class="checkout-button"
style="background:#333;color:#fff;padding:14px 30px;display:inline-block">Concludi ordine</a></div></div>
</article></main></body></html>"""


def test_a_woocommerce_cart_row_and_its_checkout_entry(lab):
    audit = page_audit(lab, WOO_CART, "/carrello/")
    cart = audit["cart"]
    assert [(i["title"], i["addon"]) for i in cart["line_items"]] == [("Borsa Luna", False),
                                                                     ("Protezione spedizione", True)]
    # "Concludi ordine" on a link into a checkout URL is the way into the checkout, not a place-order control.
    assert cart["checkout_cta"] == {**cart["checkout_cta"], "present": True, "label": "Concludi ordine", "tag": "a"}
    result = classify(audit, audit["url"])
    assert result["type"] == "cart" and not any(s.startswith("checkout:") for s in result["signals"])
    button = page_audit(lab, WOO_CART.replace('<a href="/pagamento/" class="checkout-button"', '<button')
                        .replace("Concludi ordine</a>", "Concludi ordine</button>"), "/carrello/")
    assert not button["cart"]["checkout_cta"]["present"]
    assert any(c["label"] == "Concludi ordine" and c["lexicon_hit"] == "place_order" for c in button["ctas"])


def test_a_row_of_policy_links_is_no_policy_statement(lab):
    audit = page_audit(lab, """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Scarpa</title></head>
      <body><main><h1>Scarpa Aurora</h1><p>49,90 €</p><button>Aggiungi al carrello</button></main>
      <footer><div class="links"><a href="/resi">Resi e rimborsi</a> | <a href="/spedizioni">Spedizioni e consegne</a>
      | <a href="/privacy">Privacy</a> | <a href="/termini">Termini e condizioni</a></div></footer></body></html>""")
    assert not [s for s in audit["snippets"] if s["kind"] in ("returns_policy", "shipping_policy")]
    assert audit["trust"]["policy_links"]["returns"]["label"] == "Resi e rimborsi"


BORSA_LUNA = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Borsa Luna | Pelletteria</title>
<style>.rel{display:grid;grid-template-columns:repeat(6,1fr);gap:6px}.card{border:1px solid #ddd}</style></head>
<body><header><a href="/">Pelletteria Rossi</a><nav><a href="/borse.html">Borse</a></nav></header>
<main itemscope itemtype="https://schema.org/Product"><h1 itemprop="name">Borsa Luna in pelle</h1>
<div itemprop="offers" itemscope itemtype="https://schema.org/Offer"><span class="price">129,00 €</span>
<meta itemprop="price" content="129.00"><meta itemprop="priceCurrency" content="EUR"></div>
<label>Colore <select name="super_attribute[93]"><option>Scegli un'opzione...</option><option>Nero</option>
<option>Cuoio</option></select></label>
<button type="submit" style="background:#1979c3;color:#fff;padding:14px 30px">Aggiungi al carrello</button>
<p>Disponibile. Spedizione in 24/48 ore.</p><h2>Potrebbe piacerti anche</h2><div class="rel" id="rel"></div></main>
<script>
for (let i = 0; i < 12; i++) document.getElementById('rel').insertAdjacentHTML('beforeend', `<div class="card">
  <a href="/borsa-${i}.html"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width="100" height="100"
  alt="Borsa ${i}"></a><a href="/borsa-${i}.html">Borsa ${i}</a><span class="price">${60 + i},00 €</span>
  <button>Aggiungi al carrello</button></div>`);
</script></body></html>"""


def test_a_microdata_product_page_with_related_products_is_a_product_page(lab):
    audit = page_audit(lab, BORSA_LUNA, "/borsa-luna.html")
    pdp = audit["pdp"]
    assert pdp["add_to_cart_count"] == 1 and pdp["add_to_cart_all"] == 13 and pdp["add_to_cart"]["above_fold"]
    assert audit["meta"]["microdata_products"] == audit["meta"]["microdata_main"] == 1
    assert audit["products"]["main_group"] == 12
    group = pdp["variant_groups"][0]
    assert {k: v for k, v in group.items() if k != "rect"} == {
        "label": "Colore", "kind": "select", "options": ["Scegli un'opzione...", "Nero", "Cuoio"], "selected": False,
        "first_available": "Nero"}
    assert audit["forms"]["fields"][0]["label"] == "Colore"  # not the options of the select inside the label
    result = classify(audit, audit["url"])
    assert result["type"] == "pdp" and "pdp:microdata Product" in result["signals"]


VARIANT_PAGE = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Maglia Onda</title>
<style>.gone{text-decoration:line-through}.sr{position:absolute;opacity:0;width:1px;height:1px}</style></head>
<body><main><h1>Maglia Onda</h1><p><span>29,90 €</span></p>GROUP
<button style="background:#000;color:#fff;padding:14px 30px">Aggiungi al carrello</button></main></body></html>"""


@pytest.mark.parametrize("group, selected, first", [
    ('<label>Taglia <select><option value="">Scegli una taglia</option><option disabled>S</option>'
     "<option>M - Esaurito</option><option>L</option></select></label>", False, "L"),
    ('<label>Taglia <select><option disabled selected>Taglia</option><option>M</option></select></label>', False, "M"),
    ("<label>Taglia <select><option>M</option><option>L</option></select></label>", True, "M"),
    ('<fieldset><legend>Taglia</legend><button disabled>40</button><button class="gone">41</button>'
     "<button>42</button></fieldset>", False, "42"),  # no state anywhere: nothing chosen
    ('<fieldset><legend>Taglia</legend><button class="size is-selected">40</button><button class="size">41</button>'
     "</fieldset>", True, "40"),
    ('<fieldset><legend>Colore</legend><input type="radio" name="c" id="c1"><label for="c1">Nero</label>'
     '<input type="radio" name="c" id="c2" checked><label for="c2">Rosso</label></fieldset>', True, "Nero"),
    ('<div aria-label="Colore"><input class="sr" type="radio" name="c" id="c1" disabled><label for="c1">Nero</label>'
     '<input class="sr" type="radio" name="c" id="c2"><label for="c2">Rosso</label></div>', False, "Rosso"),
    # the placeholder repeats the label without its colon; a sold-out default is no choice while another is free
    ('<label for="t">Taglia:</label> <select id="t"><option value="0">Taglia</option><option value="1">M</option>'
     "</select>", False, "M"),
    ("<label>Taglia <select><option>S - Esaurito</option><option>M</option></select></label>", False, "M"),
    ("<label>Taglia <select><option>S - Esaurito</option><option>M - Esaurito</option></select></label>", True, None),
    ('<fieldset><legend>Taglia</legend><button><s>S</s></button><button>M</button></fieldset>', False, "M"),
    # Magento 2 swatches: options with aria-checked, a disabled one marked by its class
    ('<div><span>Taglia</span><div role="listbox"><div class="swatch-option disabled" role="option" '
     'aria-checked="false" aria-label="S">S</div><div class="swatch-option" role="option" aria-checked="false" '
     'aria-label="M">M</div></div></div>', False, "M"),
    # Dawn: a checked option sold out by its hidden radio's class alone is no choice
    ('<fieldset><legend>Taglia</legend><input class="sr disabled" type="radio" name="t" id="t0" checked>'
     '<label for="t0">S</label><input class="sr" type="radio" name="t" id="t1"><label for="t1">M</label></fieldset>',
     False, "M"),
])
def test_variant_groups_say_whether_an_option_is_chosen_and_which_one_is_free(lab, group, selected, first):
    """crawler.select_variant picks first_available once when selected is False (a placeholder, no pressed, checked
    or selected option); disabled, struck-through and out-of-stock options are never offered."""
    groups = page_audit(lab, VARIANT_PAGE.replace("GROUP", group), "/maglia-onda")["pdp"]["variant_groups"]
    assert len(groups) == 1 and (groups[0]["selected"], groups[0]["first_available"]) == (selected, first), groups
    assert groups[0]["rect"]["w"] > 0


def omnibus_listing(per_card: bool, footnote: bool) -> str:
    cards = "".join(
        f'<li><a href="/p/{n}.html"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width=150 height=150 '
        f'alt="Articolo {n}"><h3>Articolo {n}</h3></a><p><span>4{n},90 €</span> <del>6{n},90 €</del></p>'
        + ('<p>Prezzo più basso negli ultimi 30 giorni</p>' if per_card else "")
        + ('<p>Solo 3 rimasti</p>' if n == 0 else "") + "<button>Aggiungi al carrello</button></li>" for n in range(6))
    note = "<p>Il prezzo barrato è il prezzo più basso negli ultimi 30 giorni.</p>" if footnote else ""
    return (f'<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Scarpe</title></head><body><main>'
            f"<h1>Scarpe da corsa</h1><p>Solo 2 rimasti nel nostro magazzino centrale.</p><ul>{cards}</ul>{note}"
            f'</main><div role="dialog" aria-modal="true" style="position:fixed;inset:20%;background:#fff">'
            f"<p>Offerta valida solo oggi: solo 4 rimasti!</p><button>Chiudi</button></div></body></html>")


def test_persuasion_items_and_prices_say_whether_a_product_card_or_an_overlay_shows_them(lab):
    """Producer-dependent scope (checks rule f, deception low_stock): each statement and price carries in_card and
    overlay; a statement shown on every card is one item with count, and a page footnote with the same words is
    another."""
    audit = page_audit(lab, omnibus_listing(True, True), "/scarpe")
    said = audit["persuasion"]["lowest_price_30d"]
    assert [(i["text"], i["in_card"], i["overlay"], i["count"]) for i in said] == [
        ("Prezzo più basso negli ultimi 30 giorni", True, False, 6),
        ("Il prezzo barrato è il prezzo più basso negli ultimi 30 giorni.", False, False, 1)]
    struck = [p for p in audit["prices"] if p["strikethrough"]]
    assert len(struck) == 6 and all(p["in_card"] and not p["overlay"] for p in struck)
    scarce = {i["text"]: (i["in_card"], i["overlay"], i["number"]) for i in audit["persuasion"]["scarcity"]}
    assert scarce == {"Solo 2 rimasti nel nostro magazzino centrale.": (False, False, 2),
                      "Solo 3 rimasti": (True, False, 3), "Offerta valida solo oggi: solo 4 rimasti!": (False, True, 4)}
    footnote_only = page_audit(lab, omnibus_listing(False, True), "/scarpe")["persuasion"]["lowest_price_30d"]
    assert [(i["in_card"], i["count"]) for i in footnote_only] == [(False, 1)]


def test_per_card_statements_past_the_item_cap_still_count_and_keep_the_footnote(lab):
    """Each place (cards, page, overlay) keeps 10 items; the place's last item counts the rest, so 12 cards whose
    Omnibus lines each quote their own price add up to 12 and the footnote after them is still read (checks rule f)."""
    cards = "".join(
        f'<li><a href="/p/{n}.html"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width=150 height=150 '
        f'alt="Articolo {n}"><h3>Articolo {n}</h3></a><p><span>{40 + n},90 €</span> <del>{60 + n},90 €</del></p>'
        f"<p>Prezzo più basso negli ultimi 30 giorni: {45 + n},90 €</p><button>Aggiungi al carrello</button></li>"
        for n in range(12))
    audit = page_audit(lab, f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Scarpe</title></head>
      <body><main><h1>Scarpe da corsa</h1><ul>{cards}</ul>
      <p>Il prezzo barrato è il prezzo più basso negli ultimi 30 giorni.</p></main></body></html>""", "/scarpe")
    said = audit["persuasion"]["lowest_price_30d"]
    in_cards = [i for i in said if i["in_card"]]
    assert len(in_cards) == 10 and sum(i["count"] for i in in_cards) == 12
    assert [(i["text"], i["count"]) for i in said if not i["in_card"]] == [
        ("Il prezzo barrato è il prezzo più basso negli ultimi 30 giorni.", 1)]
    assert sum(1 for p in audit["prices"] if p["strikethrough"] and p["in_card"]) == 12


def test_footer_trust_details_survive_a_truncated_text_walk(lab):
    audit = page_audit(lab, """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Tutti</title></head>
      <body><header><a href="/">Negozio</a></header><main><h1>Tutti i prodotti</h1><ul id="u"></ul></main>
      <footer><p>Rossi S.r.l. – P.IVA 01234567897 – Via Roma 10, 20100 Milano (MI)</p>
      <p>Email: info@rossi.example – Tel. 02 1234567</p></footer>
      <script>let h = ''; for (let i = 0; i < 2500; i++) h += `<li><a href="/prodotto/${i}"><img alt="P${i}" width="40"
        height="40" src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></a><h3><a href="/prodotto/${i}">Prodotto ${i}</a>
        </h3><p>Colore nero</p><span>${(i % 90) + 10},90 €</span></li>`;
      document.getElementById('u').innerHTML = h;</script></body></html>""")
    trust = audit["trust"]
    assert audit["truncated"] and trust["vat_id"] and "01234567897" in trust["vat_id"]
    assert trust["contact"] == {**trust["contact"], "email": True, "phone": True, "address": True}


def test_a_prechecked_add_on_priced_in_another_table_cell(lab):
    cart = page_audit(lab, """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Your cart</title></head>
      <body><main><h1>Your cart</h1><table><tr><th>Product</th><th>Qty</th><th>Price</th></tr>
      <tr><td><a href="/products/trail-x">Trail Runner X</a></td><td><select aria-label="Quantity"><option>1</option>
      <option selected>2</option></select> <a href="/cart/change?line=1&quantity=0">Remove</a></td><td>$99.98</td></tr>
      <tr><td><label><input type="checkbox" checked> Shipping protection</label></td><td></td><td>$2.98</td></tr>
      </table><div><span>Subtotal</span> <span>$102.96</span></div>
      <button style="background:#000;color:#fff;padding:14px 40px">Check out</button>
      <p>Have an account? <a href="/account/login">Log in</a> to check out faster.</p></main></body></html>""",
                      "/cart")["cart"]
    assert cart["prechecked_paid"] == [{"label": "Shipping protection", "price_text": "$2.98", "price_value": 2.98,
                                        "checked": True, "addon": True}]


def test_a_single_offers_inventory_level_is_read(lab):
    """Owed to WS2 (deception.stock_contradictions): Offer.inventoryLevel, a QuantitativeValue or a number, of a
    product with a single offer; with several offers (variants), or one AggregateOffer summing them, no level is the
    page's."""
    def product(offers) -> str:
        ld = json.dumps({"@context": "https://schema.org", "@type": "Product", "name": "Borsa Luna", "offers": offers})
        return (f'<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Borsa Luna</title><script '
                f'type="application/ld+json">{ld}</script></head><body><main><h1>Borsa Luna</h1><p>129,00 €</p>'
                "<p>Solo 3 rimasti</p></main></body></html>")

    offer = {"@type": "Offer", "price": "129.00", "priceCurrency": "EUR",
             "inventoryLevel": {"@type": "QuantitativeValue", "value": 3}}
    aggregate = {"@type": "AggregateOffer", "lowPrice": "129.00", "highPrice": "149.00", "offerCount": 4,
                 "priceCurrency": "EUR", "inventoryLevel": {"@type": "QuantitativeValue", "value": 40}}
    levels = [page_audit(lab, product(o), "/borsa")["pdp"]["structured"]["inventory_level"] for o in (
        offer, {**offer, "inventoryLevel": "7"}, [offer, {**offer, "price": "139.00"}],
        {k: v for k, v in offer.items() if k != "inventoryLevel"}, aggregate,
        {**offer, "offerCount": 3})]
    assert levels == [3, 7, None, None, None, None]


def test_json_ld_in_the_payload_is_bounded_but_keeps_types_and_keys(lab):
    product = {"@context": "https://schema.org", "@type": "Product", "name": "Scarpa", "description": "x" * 2000,
               "image": [f"https://cdn.example/{i}.jpg" for i in range(10)],
               "offers": {"@type": "Offer", "price": "49.90", "priceCurrency": "EUR"},
               "review": [{"@type": "Review", "reviewBody": "Ottima " * 200, "author": {"@type": "Person", "name": "A"},
                           "reviewRating": {"@type": "Rating", "ratingValue": 5}} for _ in range(12)]}
    scripts = "".join(f'<script type="application/ld+json">{json.dumps({**product, "sku": str(i)})}</script>'
                      for i in range(20))
    audit = page_audit(lab, f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Lista</title>{scripts}
      </head><body><main><h1>Lista</h1></main></body></html>""")
    size = len(json.dumps(audit["jsonld"]))
    assert len(audit["jsonld"]) == 20 and size < 40_000
    first, last = audit["jsonld"][0], audit["jsonld"][-1]
    assert first["description"] is None and first["image"] == "https://cdn.example/0.jpg"
    assert first["review"][0]["reviewBody"] is None and first["offers"]["price"] == "49.90"
    assert last["@type"] == "Product" and "review" in last and "offers" in last  # past the budget: type and keys


# ---------------------------------------------------------------- audit.js: prices, quotes, overlays, gates (round 3)

FLEUR = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<title>Eau de Parfum Fleur 50 ml | Profumeria</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product",
"name":"Eau de Parfum Fleur 50 ml",
"offers":{"@type":"Offer","price":"89.00","priceCurrency":"EUR","availability":"https://schema.org/InStock"}}</script>
</head><body><header><a href="/">Profumeria</a> <a href="/carrello">Carrello</a></header><main>
<h1>Eau de Parfum Fleur 50 ml</h1><p>Liqueur de Fleur, Amateur 2 edizione. Coppa Euro 2024: il profumo dei campioni.</p>
<div style="margin-top:200px"><span class="price">89,00 €</span></div>
<button style="background:#000;color:#fff;padding:14px 40px">Aggiungi al carrello</button></main></body></html>"""


def test_currency_letters_inside_words_are_not_prices(lab):
    """"Fleur 50 ml", "Amateur 2", "Euro 2024": EUR inside a word, or before a year, is no currency."""
    audit = page_audit(lab, FLEUR)
    assert audit["pdp"]["price"]["value"] == 89 == audit["pdp"]["structured"]["price"]
    assert [p["value"] for p in audit["prices"]] == [89]
    names = ["Fleur 100 ml", "Liqueur 50 ml", "Douceur 100 ml", "Splendeur 50 ml", "Couleur 100 ml", "Chauffeur 24h"]
    cards = "".join(f'<li><a href="/prodotto/p{i}"><h3>Eau de Parfum {names[i % 6]}</h3></a>'
                    f'<span class="price">{60 + i},00 €</span></li>' for i in range(16))
    listing = page_audit(lab, f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
      <title>Profumi donna</title></head><body><header><a href="/">Profumeria</a></header><main>
      <h1>Profumi donna</h1><p>16 prodotti</p>
      <ul style="display:grid;grid-template-columns:repeat(4,1fr);list-style:none">{cards}</ul></main></body></html>""",
                         "/profumi-donna/")
    assert listing["products"]["cards_count"] == 16
    assert [c["price_value"] for c in listing["products"]["cards"][:2]] == [60, 61]
    assert classify(listing, "https://shop.example/profumi-donna/")["type"] == "plp"


FLAT_PDP = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Borsa Luna | Bottega</title></head>
<body><header><a href="/">Bottega</a> <a href="/carrello">Carrello</a></header><main>
<h1>Borsa Luna in pelle</h1><p class="price">129,00 €</p>
<button style="background:#630;color:#fff;padding:14px 40px">Aggiungi al carrello</button>
<p>Resi gratuiti entro 30 giorni dalla consegna, rimborso completo sul metodo di pagamento originale.</p>
<p>Spedizione gratuita in 24/48 ore in tutta Italia per ordini sopra i 50 €.</p>
<p>Pelle conciata al vegetale, cuciture a mano, fodera in cotone biologico, tracolla regolabile.</p>
<p>Dimensioni 30 x 22 x 10 cm, peso 650 grammi, chiusura con zip in ottone e due tasche interne.</p>
<h2 style="font-size:40px">Recensioni dei clienti</h2>
<p>"Bellissima, la pelle è morbida e profuma di cuoio vero. Arrivata in due giorni." – Giulia, Milano</p>
<button>Scrivi una recensione</button>
<h2>Prodotti correlati</h2><p>Altre borse della stessa collezione in pelle conciata al vegetale.</p>
</main><footer><a href="/resi">Resi</a> <a href="/privacy">Privacy</a></footer></body></html>"""


def test_a_testimonial_heading_scopes_its_own_section_on_a_flat_page(lab):
    """On a flat page every block is a child of <main>: a review heading quotes the blocks up to the next heading, not
    the description, the policies or the buttons, and those keep their own kinds."""
    audit = page_audit(lab, FLAT_PDP)
    kinds = {}
    for s in audit["snippets"]:
        kinds.setdefault(s["kind"], []).append(s["text"])
    assert kinds["testimonial"] == ['"Bellissima, la pelle è morbida e profuma di cuoio vero. Arrivata in due giorni." '
                                    '– Giulia, Milano']
    assert kinds["returns_policy"] == ["Resi gratuiti entro 30 giorni dalla consegna, rimborso completo sul metodo di "
                                       "pagamento originale."]
    assert kinds["shipping_policy"][0].startswith("Spedizione gratuita in 24/48 ore")
    assert kinds["headline"] == ["Borsa Luna in pelle"]  # neither the review heading nor a policy line


SCREEN_READER = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Negozio</title>
<style>.visually-hidden{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;
margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
.vh{position:absolute;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap}
.x{width:32px;height:32px}</style></head>
<body><header><a href="/">Negozio</a></header>
<main><h1><span class="visually-hidden">Pagina prodotto: </span>Scarpa Aurora</h1><p class="price">49,90 €</p>
<button style="background:#000;color:#fff;padding:14px 40px"><span class="vh">Scarpa Aurora - </span>Aggiungi al
carrello</button>
<h2>Recensioni dei clienti</h2><blockquote>Comodissime, le uso tutti i giorni per correre.
<span class="vh">Recensione verificata dal sistema antifrode</span></blockquote></main>
<div role="dialog" aria-modal="true"
style="position:fixed;inset:20% 20%;background:#fff;padding:20px;border:1px solid #000">
<button class="x"><span class="visually-hidden">No grazie, preferisco pagare di più</span>✕</button>
<h2>Iscriviti alla newsletter</h2><p>Ricevi il 10% di sconto sul tuo primo ordine.</p>
<input type="email" placeholder="La tua email"><button>Iscriviti</button></div>
<div style="position:fixed;left:0;right:0;bottom:0;background:#fff;padding:16px;border-top:1px solid #ccc">
<p>Usiamo cookie per analisi e profilazione pubblicitaria.</p>
<button style="background:#06c;color:#fff;padding:12px 30px">Accetta tutti</button>
<button style="padding:12px 30px">Rifiuta<span class="vh"> e perdi tutti i vantaggi</span></button></div>
</body></html>"""


def test_quotes_leave_out_text_hidden_for_screen_readers(lab):
    """Bootstrap's .visually-hidden (1 px) and clip-only hiding: a judge must never quote words nobody sees."""
    audit = page_audit(lab, SCREEN_READER)
    quotes = json.dumps(audit["snippets"], ensure_ascii=False)
    for hidden in ("Pagina prodotto", "Scarpa Aurora -", "preferisco", "perdi tutti i vantaggi", "verificata"):
        assert hidden not in quotes, hidden
    by_kind = {s["kind"]: s["text"] for s in reversed(audit["snippets"])}
    assert by_kind["headline"] == "Scarpa Aurora" and by_kind["cta"] == "Aggiungi al carrello"
    assert by_kind["testimonial"] == "Comodissime, le uso tutti i giorni per correre."
    newsletter, consent = sorted(audit["overlays"], key=lambda o: o["kind"] != "newsletter")
    assert newsletter["kind"] == "newsletter" and newsletter["has_close"] and newsletter["decline_labels"] == []
    assert "modal_decline" not in by_kind  # the close button shows only "✕"
    assert consent["kind"] == "consent" and consent["reject_labels"] == ["Rifiuta"]


SECOND_LAYER = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Negozio</title></head>
<body><header><a href="/">Negozio</a></header><main><h1>Benvenuto</h1><p>Scopri le novità della stagione.</p></main>
<div class="cmp" style="position:fixed;left:0;right:0;bottom:0;background:#fff;padding:16px;overflow:hidden">
<p>Usiamo cookie per analisi e profilazione pubblicitaria.</p>
<button style="background:#06c;color:#fff;padding:12px 30px">Accetta tutti</button>
<button style="padding:12px 30px">Personalizza</button>
<div style="max-height:0;overflow:hidden"><p>Preferenze dettagliate</p>
<button style="padding:12px 30px">Rifiuta tutti</button>
<button>Salva</button></div>
<div style="position:absolute;top:0;left:0;transform:translateX(120vw)"><button style="padding:12px 30px">Rifiuta i
cookie non necessari</button></div></div></body></html>"""


def test_a_collapsed_or_parked_second_layer_is_not_the_first_layer(lab):
    """A reject button in a collapsed panel, slid off screen, or under the first layer is not a first-layer reject."""
    audit = page_audit(lab, SECOND_LAYER)
    consent = audit["overlays"][0]
    assert consent["kind"] == "consent" and consent["accept_labels"] == ["Accetta tutti"]
    assert consent["reject_labels"] == [] and consent["manage_labels"] == ["Personalizza"]
    text = [s["text"] for s in audit["snippets"] if s["kind"] == "consent"][0]
    assert "Rifiuta" not in text and "Preferenze dettagliate" not in text
    stacked = page_audit(lab, """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Negozio</title>
      </head><body><main><h1>Benvenuto</h1></main><div style="position:fixed;left:0;right:0;bottom:0;height:140px">
      <div style="position:absolute;inset:0;z-index:1">
      <button style="margin:20px;padding:12px 30px">Rifiuta tutto</button></div>
      <div style="position:relative;z-index:2;background:#fff;height:140px"><p>Usiamo cookie per analisi.</p>
      <button>Accetta tutti</button> <button>Personalizza</button></div></div></body></html>""")["overlays"][0]
    assert stacked["accept_labels"] == ["Accetta tutti"] and stacked["reject_labels"] == []  # under the first layer


STICKY_HEADER = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Negozio Sport</title>
<style>body{margin:0} header{position:sticky;top:0;background:#fff;z-index:10}
.top{background:#111;color:#fff;padding:12px}
.main{display:flex;gap:12px;padding:24px} nav{padding:16px 20px;display:flex;gap:16px}</style></head><body>
<header><div class="top">Spedizione gratuita sopra 49 € · Resi gratuiti entro 30 giorni</div>
<div class="main"><a href="/">SportShop</a><form role="search" action="/search"><input type="search" name="q"
placeholder="Cerca prodotti" style="width:300px;padding:12px"></form><a href="/account">Accedi</a>
<a href="/carrello">Carrello (0)</a></div>
<nav><a href="/uomo/">Uomo</a><a href="/donna/">Donna</a><a href="/bambini/">Bambini</a>
<a href="/saldi/">Saldi</a></nav></header>
<div style="position:fixed;bottom:0;left:0;right:0;height:140px;background:#eee"><nav><a href="/">Home</a>
<a href="/uomo/">Uomo</a><a href="/carrello">Carrello</a></nav></div>
<main><h1>Nuova collezione autunno</h1><p>Scarpe da corsa e abbigliamento tecnico della nuova stagione.</p>
<a href="/uomo/" style="display:inline-block;background:#e30;color:#fff;padding:14px 40px">Scopri la collezione</a>
<div style="height:1500px"></div></main>
<div style="position:sticky;bottom:0;background:#fff;padding:12px"><p>Usiamo i cookie per migliorare il negozio.</p>
<button>Accetta</button> <button>Rifiuta</button></div>
<footer><a href="/resi">Resi</a> P.IVA 01234567890</footer></body></html>"""


def test_the_sites_own_sticky_header_and_bottom_navigation_are_no_overlays(lab):
    audit = page_audit(lab, STICKY_HEADER)
    assert [o["kind"] for o in audit["overlays"]] == ["consent"]  # a sticky consent bar still is one
    assert audit["overlays"][0]["reject_labels"] == ["Rifiuta"]
    assert any(c["label"] == "Scopri la collezione" for c in audit["ctas"])


@pytest.mark.parametrize("buttons, accept, reject", [
    ("<button>Nega</button><button>Personalizza</button><button>Consenti tutti</button>", ["Consenti tutti"], ["Nega"]),
    ("<button>Acconsento</button><button>Non acconsento</button>", ["Acconsento"], ["Non acconsento"]),
    ('<a href="/privacy">Maggiori informazioni</a> <button>Ok, ho capito</button>', ["Ok, ho capito"], []),
    ("<button>Sì, accetto</button><button>Prosegui senza accettare</button>", ["Sì, accetto"],
     ["Prosegui senza accettare"]),
])
def test_consent_labels_of_italian_cmps(lab, buttons, accept, reject):
    audit = page_audit(lab, f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Negozio</title></head>
      <body><main><h1>Negozio</h1><p>Benvenuto.</p></main><div id="CybotCookiebotDialog"
      style="position:fixed;left:0;right:0;bottom:0;background:#eee;padding:10px"><p>Questo sito utilizza i cookie
      per personalizzare contenuti e annunci.</p>{buttons}</div></body></html>""")
    consent = audit["overlays"][0]
    assert consent["kind"] == "consent" and consent["accept_labels"] == accept and consent["reject_labels"] == reject


def test_a_consent_banner_whose_fixed_box_is_a_shadow_host(lab):
    audit = page_audit(lab, """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Negozio</title></head>
      <body><main><h1>Negozio</h1><p>Benvenuto.</p></main>
      <div id="cmp" style="position:fixed;left:0;right:0;bottom:0;z-index:9"></div><script>
      document.getElementById('cmp').attachShadow({mode: 'open'}).innerHTML =
        '<div role="dialog" aria-label="Consenso" style="background:#fff;padding:16px">' +
        '<p>Usiamo cookie per analisi e profilazione.</p>' +
        '<button>Accetta tutti</button> <button>Rifiuta tutti</button></div>';</script></body></html>""",
                       light_dom=False)
    consent = audit["overlays"][0]
    assert consent["kind"] == "consent" and consent["reject_labels"] == ["Rifiuta tutti"]
    assert any(s["kind"] == "consent" and "Usiamo cookie" in s["text"] for s in audit["snippets"])


def test_delivery_cost_rows_and_checkout_labels_of_a_basket(lab):
    basket = """<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><title>Your basket | TeaShop</title>
      </head>
      <body><header><a href="/">TeaShop</a> <a href="/basket">Basket (1)</a></header><main><h1>Your basket</h1>
      <table><tr><td><a href="/products/earl-grey">Earl Grey Loose Leaf 250g</a></td><td><input type="number" value="2"
      aria-label="Quantity"></td><td>£12.00</td><td><button>Remove</button></td></tr></table>
      <div><div><span>Subtotal</span> <span>£12.00</span></div>
      <div><span>Standard delivery</span> <span>£3.95</span></div>
      <div><span>Total</span> <span>£15.95</span></div></div>
      <a href="/checkout" style="display:inline-block;background:#063;color:#fff;padding:14px 40px">Checkout →</a>
      </main></body></html>"""
    cart = page_audit(lab, basket, "/basket")["cart"]
    assert cart["shipping_value"] == 3.95 and cart["shipping_text"] == "Standard delivery £3.95"
    assert cart["checkout_cta"]["present"] and cart["checkout_cta"]["label"] == "Checkout →"
    italian = basket.replace("Standard delivery", "Consegna").replace("£3.95", "4,90 €").replace('"en-GB"', '"it"')
    assert page_audit(lab, italian, "/basket")["cart"]["shipping_value"] == 4.9


LABEL_VALUE_CART = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Carrello</title></head>
<body><header><a href="/">Negozio</a></header><main><h1>Carrello</h1>
<div class="line"><a href="/p/aurora">Scarpa da corsa Aurora</a> <label>Quantità <input type="number" value="1"></label>
<span>44,90 €</span> <button>Rimuovi</button></div>
<section><h2>Riepilogo</h2>TOTALS</section>
<a href="/checkout" style="display:inline-block;background:#063;color:#fff;padding:14px 40px">Procedi al checkout</a>
</main></body></html>"""


@pytest.mark.parametrize("totals", [
    "<dl><dt>Subtotale</dt><dd>44,90 €</dd><dt>Spedizione</dt><dd>4,90 €</dd><dt>Totale</dt><dd>49,80 €</dd></dl>",
    '<dl><dt>Subtotale</dt><dd><span class="amount">44,90 €</span></dd><dt>Spedizione</dt><dd><span class="amount">'
    '4,90 €</span></dd><dt>Totale</dt><dd><strong class="amount">49,80 €</strong></dd></dl>',
    '<div style="display:grid;grid-template-columns:1fr auto"><span>Subtotale</span><span>44,90 €</span>'
    "<span>Spedizione</span><span>4,90 €</span><span>Totale</span><span>49,80 €</span></div>",
    "<table><thead><tr><th>Subtotale</th><th>Spedizione</th><th>Totale</th></tr></thead>"
    "<tbody><tr><td>44,90 €</td><td>4,90 €</td><td>49,80 €</td></tr></tbody></table>",
    "<table><tr><th>Subtotale</th><td>44,90 €</td></tr><tr><th>Spedizione</th><td>4,90 €</td></tr>"
    "<tr><th>Totale</th><td>49,80 €</td></tr></table>",
    # WooCommerce's totals table: the total's cell also holds the included tax
    '<table class="shop_table"><tr><th>Subtotale</th><td><span class="amount"><bdi>44,90&nbsp;<span>€</span></bdi>'
    '</span></td></tr><tr><th>Spedizione</th><td>Tariffa unica: <span class="amount">4,90&nbsp;€</span><p>Spedizione a '
    '<strong>Milano</strong>.</p></td></tr><tr><th>Totale</th><td><strong><span class="amount">49,80&nbsp;€</span>'
    '</strong> <small>(include <span class="amount">8,98&nbsp;€</span> IVA)</small></td></tr></table>',
    "<dl><dt>Subtotale</dt><dd>44,90 €</dd><dt>Spedizione</dt><dd>4,90 €</dd><dt>Totale</dt><dd><span>49,80 €</span> "
    "<small>(IVA inclusa: 8,98 €)</small></dd></dl>",
    # inline labels with no space before their amounts: quoted as shown ("Spedizione:4,90 €")
    "<p><span>Subtotale:</span><span>44,90 €</span><br><span>Spedizione:</span><span>4,90 €</span><br>"
    "<span>Totale:</span><span>49,80 €</span></p>",
])
def test_cart_totals_in_label_value_layouts(lab, totals):
    """A definition list, a grid of label and value boxes or a table: each amount takes its own label (the r4 review's
    <dl> read subtotal 49.8 and no total); a value cell that also holds a tax note keeps the label before it. Every
    fee line is quoted verbatim (page_audit checks each snippet against the page's text)."""
    cart = page_audit(lab, LABEL_VALUE_CART.replace("TOTALS", totals), "/carrello")["cart"]
    assert (cart["subtotal_value"], cart["shipping_value"], cart["total_value"]) == (44.9, 4.9, 49.8)
    assert [line["price_value"] for line in cart["line_items"]] == [44.9]
    assert "Spedizione" in cart["shipping_text"] and [f["value"] for f in cart["fees"]] == [4.9]
    assert cart["total_text"].startswith("Totale")
    if totals.startswith("<dl><dt>Subtotale</dt><dd>44,90 €</dd>") and "IVA" not in totals:
        assert (cart["subtotal_text"], cart["total_text"]) == ("Subtotale 44,90 €", "Totale 49,80 €")
    if "IVA" in totals:
        assert "8,98" in cart["total_text"]  # the cell as shown, note included
    if "<p><span>" in totals:
        assert cart["fees"][0]["row_text"] == "Spedizione:4,90 €"
    if "<thead>" in totals:  # a column header is quoted with its row of amounts, as the page reads them
        assert cart["fees"][0]["row_text"] == "Subtotale Spedizione Totale 44,90 € 4,90 € 49,80 €"


@pytest.mark.parametrize("totals, subtotal_text, shipping_text, total_text", [
    # Shopware: "Somma parziale", asterisks, a net amount and the tax after the total
    ("<dl><dt>Somma parziale</dt><dd>44,90 €*</dd><dt>Costi di spedizione</dt><dd>4,90 €*</dd><dt>Importo totale</dt>"
     "<dd>49,80 €</dd><dt>Importo netto</dt><dd>40,82 €</dd><dt>più IVA 22%</dt><dd>8,98 €</dd></dl>",
     "Somma parziale 44,90 €*", "Costi di spedizione 4,90 €*", "Importo totale 49,80 €"),
    # a value cell holding the amount and a note without an amount, after it or before it
    ("<dl><dt>Subtotale</dt><dd><span>44,90 €</span></dd><dt>Spedizione</dt><dd><span>4,90 €</span></dd><dt>Totale</dt>"
     "<dd><span>49,80 €</span> <small>IVA inclusa</small></dd></dl>",
     "Subtotale 44,90 €", "Spedizione 4,90 €", "Totale 49,80 € IVA inclusa"),
    ("<div><div>Subtotale</div><div>44,90 €</div><div>Spedizione</div><div>4,90 €</div><div>Totale</div>"
     "<div><small>IVA incl.</small> <b>49,80 €</b></div></div>",
     "Subtotale 44,90 €", "Spedizione 4,90 €", "Totale IVA incl. 49,80 €"),
    # a row headed by its own <th> with the tax in a cell of its own: the tax is not the total
    ("<table><tr><th>Subtotale</th><td>44,90 €</td><td></td></tr><tr><th>Spedizione</th><td>4,90 €</td><td></td></tr>"
     "<tr><th>Totale</th><td>49,80 €</td><td>(IVA 8,98 €)</td></tr></table>",
     "Subtotale 44,90 €", "Spedizione 4,90 €", "Totale 49,80 €"),
    # WooCommerce's shipping cell with the calculator toggle (href="#")
    ('<table><tr><th>Subtotale</th><td>44,90 €</td></tr><tr><th>Spedizione</th><td>Tariffa unica: <span>4,90 €</span>'
     '<p>Spedizione a <strong>Milano</strong>.</p><form><a href="#">Cambia indirizzo</a></form></td></tr>'
     "<tr><th>Totale</th><td>49,80 €</td></tr></table>",
     "Subtotale 44,90 €", "Tariffa unica: 4,90 € Spedizione a Milano. Cambia indirizzo", "Totale 49,80 €"),
    # ws1f review 3: a tax note that also names shipping or a fee word ("tasse") says what the total includes
    ("<dl><dt>Subtotale</dt><dd><span>44,90 €</span></dd><dt>Spedizione</dt><dd><span>4,90 €</span></dd><dt>Totale</dt>"
     "<dd><span>49,80 €</span> <small>IVA e spedizione incluse</small></dd></dl>",
     "Subtotale 44,90 €", "Spedizione 4,90 €", "Totale 49,80 € IVA e spedizione incluse"),
    ("<dl><dt>Subtotale</dt><dd><span>44,90 €</span></dd><dt>Spedizione</dt><dd><span>4,90 €</span></dd><dt>Totale</dt>"
     "<dd><span>49,80 €</span> <small>(IVA inclusa, spedizione esclusa)</small></dd></dl>",
     "Subtotale 44,90 €", "Spedizione 4,90 €", "Totale 49,80 € (IVA inclusa, spedizione esclusa)"),
    ("<dl><dt>Subtotale</dt><dd><span>44,90 €</span></dd><dt>Spedizione</dt><dd><span>4,90 €</span></dd><dt>Totale</dt>"
     "<dd><span>49,80 €</span> <small>(tasse incl.)</small></dd></dl>",
     "Subtotale 44,90 €", "Spedizione 4,90 €", "Totale 49,80 € (tasse incl.)"),
    # ws1f review 3 (td_tax_cell): a row labelled by a first <td> with words, the tax in a cell of its own
    ("<table><tr><td>Subtotale</td><td>44,90 €</td><td></td></tr><tr><td>Spedizione</td><td>4,90 €</td><td></td></tr>"
     "<tr><td>Totale</td><td>49,80 €</td><td>(IVA 8,98 €)</td></tr></table>",
     "Subtotale 44,90 €", "Spedizione 4,90 €", "Totale 49,80 €"),
    # ws1f review 3 (woo_ship_label): WooCommerce's single shipping method, its price inside a <label>
    ('<table class="shop_table"><tr><th>Subtotale</th><td>44,90 €</td></tr><tr class="shipping"><th>Spedizione</th>'
     '<td><ul id="shipping_method"><li><input type="hidden" name="shipping_method[0]" value="flat_rate:1"><label '
     'for="shipping_method_0_flat_rate1">Tariffa unica: <span class="amount"><bdi>4,90&nbsp;<span>€</span></bdi></span>'
     '</label></li></ul><p>Spedizione a <strong>Milano</strong>.</p><form><a href="#">Cambia indirizzo</a></form></td>'
     "</tr><tr><th>Totale</th><td>49,80 €</td></tr></table>",
     "Subtotale 44,90 €", "Spedizione Tariffa unica: 4,90 €", "Totale 49,80 €"),
])
def test_cart_totals_beside_notes_tax_cells_and_toggles(lab, totals, subtotal_text, shipping_text, total_text):
    """ws1f review 2: a note without an amount in the value cell, a tax amount in a cell of its own, a toggle link in
    the shipping cell and Shopware's "Somma parziale" still give each summary line its own amount. ws1f review 3
    (architect ruling 3): only words that change what an amount is (threshold, instalments, unit, 30-day low) keep a
    tax note's amount from its label; a note naming what the total includes or leaves out does not."""
    cart = page_audit(lab, LABEL_VALUE_CART.replace("TOTALS", totals), "/carrello")["cart"]
    assert (cart["subtotal_value"], cart["shipping_value"], cart["total_value"]) == (44.9, 4.9, 49.8)
    texts = (cart["subtotal_text"], cart["shipping_text"], cart["total_text"])
    assert texts == (subtotal_text, shipping_text, total_text)
    assert [f["value"] for f in cart["fees"]] == [4.9]


R = '<li><input type="radio" name="sm" id="m{0}"{1}><label for="m{0}">{2}</label></li>'


@pytest.mark.parametrize("methods, shipping, options", [
    (R.format(1, " checked", "Tariffa unica: 4,90 €") + R.format(2, "", "Express: 9,90 €"), 4.9, [9.9]),
    (R.format(1, "", "Tariffa unica: 4,90 €") + R.format(2, " checked", "Express: 9,90 €"), 9.9, [4.9]),
    (R.format(1, " checked", "Ritiro in negozio") + R.format(2, "", "Tariffa unica: 4,90 €"), None, [4.9]),
    (R.format(1, "", "Tariffa unica: 4,90 €") + R.format(2, "", "Express: 9,90 €"), None, [4.9, 9.9]),
    ('<li><label><input type="checkbox" name="insured"> Spedizione assicurata: 2,00 €</label></li>', None, [2.0]),
])
def test_the_chosen_shipping_method_is_the_shipping_cost(lab, methods, shipping, options):
    """ws1f review 3 (woo_ship_label): in a cell headed "Spedizione", the checked (or only) method's price is the
    shipping cost, quoted with its header; an unchosen method and a checkbox add-on stay options, and a free method
    chosen leaves no cost read."""
    totals = ('<table><tr><th>Subtotale</th><td>44,90 €</td></tr><tr><th>Spedizione</th><td><ul>' + methods +
              "</ul></td></tr><tr><th>Totale</th><td>49,80 €</td></tr></table>")
    audit = page_audit(lab, LABEL_VALUE_CART.replace("TOTALS", totals), "/carrello")
    cart = audit["cart"]
    assert (cart["subtotal_value"], cart["shipping_value"], cart["total_value"]) == (44.9, shipping, 49.8)
    assert [(f["label"], f["value"]) for f in cart["fees"]] == ([("Spedizione", shipping)] if shipping else [])
    assert [p["value"] for p in audit["prices"] if p["kind"] == "option"] == options
    assert [s["locator"] for s in audit["snippets"] if s["kind"] == "fee_line"] == (
        ["cost line: Spedizione"] if shipping else [])


def test_a_condition_beside_an_amount_is_not_a_tax_note(lab):
    """Negative pin for the tax-note rule: "Spedizione | Gratuita sopra 49,00 €" states a threshold, so the amount
    keeps its own words and is never read as the shipping cost."""
    totals = ("<dl><dt>Subtotale</dt><dd><span>44,90 €</span></dd><dt>Spedizione</dt><dd>Gratuita sopra <span>"
              "49,00 €</span></dd><dt>Totale</dt><dd><span>44,90 €</span> <small>IVA inclusa</small></dd></dl>")
    cart = page_audit(lab, LABEL_VALUE_CART.replace("TOTALS", totals), "/carrello")["cart"]
    assert (cart["subtotal_value"], cart["shipping_value"], cart["total_value"]) == (44.9, None, 44.9)
    assert cart["fees"] == []


def test_a_product_line_beside_label_value_totals_keeps_its_own_kind(lab):
    """Architect ruling 5, negative pin: a product line of boxes (unit price, quantity "2", line total) pairs its line
    total with "2", which names no total, so the line is never read as the summary total."""
    line = ('<div class="line"><a href="/p/aurora">Scarpa da corsa Aurora</a> <span>22,45 €</span> <span>2</span> '
            "<span>44,90 €</span></div>")
    html = re.sub(r'<div class="line">.*?</div>', line, LABEL_VALUE_CART, flags=re.S).replace(
        "TOTALS", "<dl><dt>Subtotale</dt><dd>44,90 €</dd><dt>Spedizione</dt><dd>4,90 €</dd><dt>Totale</dt>"
        "<dd>49,80 €</dd></dl>")
    audit = page_audit(lab, html, "/carrello")
    assert [p["kind"] for p in audit["prices"] if p["value"] in (22.45, 44.9)][:2] == ["price", "price"]
    cart = audit["cart"]
    assert (cart["subtotal_value"], cart["shipping_value"], cart["total_value"]) == (44.9, 4.9, 49.8)
    assert cart["total_text"] == "Totale 49,80 €"


LOGIN_MODAL = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Checkout</title></head>
<body><main><h1>Checkout</h1><p>Il tuo ordine: Borsa Luna, 129,00 €</p></main>
<div role="dialog" aria-modal="true" style="position:fixed;inset:10%;background:#fff;padding:20px">
<h2>Accedi per continuare</h2><form><label>Email <input type="email" name="email" required></label>
<label>Password <input type="password" name="password" required></label><button>Accedi</button></form>
<a href="/registrati">Registrati</a> GUEST</div></body></html>"""


def test_a_login_dialog_with_its_own_guest_exit_is_no_gate(lab):
    """Architect ruling 3: "Accedi / Registrati / Continua come ospite" in one dialog offers a guest path; guest
    wording in another overlay (a newsletter) does not."""
    guest_exit = LOGIN_MODAL.replace("GUEST", "<button>Continua come ospite</button>")
    guest = page_audit(lab, guest_exit, "/checkout/")["forms"]
    assert guest["password_present"] and guest["password_required"]
    assert guest["guest_option"] and not guest["login_required"]
    gate = page_audit(lab, LOGIN_MODAL.replace("GUEST", ""), "/checkout/")["forms"]
    assert gate["password_required"] and not gate["guest_option"] and gate["login_required"]
    newsletter = page_audit(lab, LOGIN_MODAL.replace("GUEST", "") + """<div role="dialog"
      style="position:fixed;bottom:0;left:0;right:0;background:#eee;padding:10px"><p>Iscriviti alla newsletter</p>
      <button>Ordina come ospite e
      iscriviti</button></div>""", "/checkout/")["forms"]
    assert newsletter["login_required"] and not newsletter["guest_option"]


def test_a_registration_split_over_two_forms_and_an_optional_password(lab):
    """Rule d reads the checkout entry as guest_option || !login_required (Fable ruling, gate B): a required
    registration password is a gate even when the email sits in another form; password_required tells an optional
    password apart, and an optional one is no gate."""
    split = page_audit(lab, """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Checkout</title></head>
      <body><main><h1>Checkout</h1><form><label>Email <input type="email" name="email" required></label></form>
      <form><label>Crea una password <input type="password" name="password" required></label>
      <button>Continua</button></form></main></body></html>""", "/checkout/")["forms"]
    assert split["password_present"] and split["password_required"] and split["login_required"]
    optional = page_audit(lab, GUEST_CHECKOUT.replace(
        '<button type="submit">', '<label>Crea una password (facoltativo) <input type="password" name="pw"></label>'
        '<button type="submit">'), "/checkout/")["forms"]
    assert optional["password_present"] and not optional["password_required"] and not optional["login_required"]


@pytest.mark.parametrize("password, gate", [
    ('<label>Crea una password <input type="password" name="pw" required></label>', True),  # wording
    ('<label>Password <input type="password" name="pw" autocomplete="new-password" required></label>', True),
    ('<label>Password <input type="password" name="pw" required></label>'  # a second password in its form
     '<label>Ripeti <input type="password" name="pw2" required></label>', True),
    ('<label>Password <input type="password" name="pw" autocomplete="current-password" required></label>', False),
    ('<label>Password <input type="password" name="pw" required></label>', False),  # unknown: an entry path is beside
])
def test_a_registration_password_beside_a_delivery_form_is_a_gate(lab, password, gate):
    """Gate B: a registration password outside overlays is a gate whatever the entry path; current-password never
    is, and a password of unknown purpose beside an address form falls to gate C (no gate)."""
    form = '<form method="post" action="/checkout/save">'
    forms = page_audit(lab, GUEST_CHECKOUT.replace(form, f"<form>{password}<button>Continua</button></form>{form}"),
                       "/checkout/")["forms"]
    assert forms["password_required"] and not forms["guest_option"] and forms["login_required"] is gate


@pytest.mark.parametrize("fixture, gate", [("checkout-accedi.html", False), ("autenticazione.html", True)])
def test_a_login_box_beside_a_delivery_form_is_no_gate_and_beside_a_registration_form_is(lab, fixture, gate):
    """Fable ruling on rule d. site2: a returning-customer login box beside the delivery form (login_required False,
    password_required True). S2: an email-only "Crea un account" beside a login box and no guest option: the email
    starts a registration, so no way in without an account (gate C)."""
    forms = page_audit(lab, (FIXTURES / "shop" / fixture).read_text(encoding="utf-8"), "/checkout/")["forms"]
    assert forms["password_present"] and forms["password_required"] and not forms["guest_option"]
    assert forms["login_required"] is gate
    if not gate:  # "Nuovo cliente" above the delivery form names no registration form: its address fields are a way in
        html = (FIXTURES / "shop" / fixture).read_text(encoding="utf-8").replace("Dati di spedizione", "Nuovo cliente")
        assert not page_audit(lab, html, "/checkout/")["forms"]["login_required"]


S2_CREATE = ('<form method="post" action="/autenticazione?create" id="create-account_form">\n'
             "        <h3>Crea un account</h3>")


@pytest.mark.parametrize("before, after, gate, guest", [
    # Fable ruling 3: the heading alone in a title box right above the form names it
    (S2_CREATE, '<div class="box-title"><h3>Crea un account</h3></div><form method="post" action="/autenticazione">',
     True, False),
    # the email-only form named by its submit alone ("Crea account", Shopify's Italian wording)
    (S2_CREATE, '<form method="post" action="/autenticazione">', True, False),
    # a guest choice inside the registration form (Shopware 6) is a way in without an account
    ('<button class="btn btn-primary" type="submit" name="SubmitCreate">',
     '<label><input type="checkbox" name="guest"> Non creare un account cliente</label>'
     '<button class="btn btn-primary" type="submit" name="SubmitCreate">', False, True),
])
def test_how_an_email_only_registration_start_is_named(lab, before, after, gate, guest):
    """S2 variants: the email-only form is a registration start (no way in, gate C) when a title box above it or its
    own submit says so; a guest checkbox is a guest option."""
    html = (FIXTURES / "shop" / "autenticazione.html").read_text(encoding="utf-8")
    assert before in html
    html = html.replace(before, after)
    if after.startswith('<div class="box-title">'):
        html = html.replace('name="SubmitCreate">Crea un account', 'name="SubmitCreate">Invia')
    elif after.startswith("<form"):
        html = html.replace('name="SubmitCreate">Crea un account', 'name="SubmitCreate">Crea account')
    forms = page_audit(lab, html, "/checkout/")["forms"]
    assert (forms["login_required"], forms["guest_option"]) == (gate, guest)


@pytest.mark.parametrize("box", [
    '<form method="post" action="/login"><h2>Utenti registrati</h2><label>Email <input type="email" name="le" '
    'required></label><label>Password <input type="password" name="lp" required></label><button type="submit">Entra'
    "</button></form>",
    '<form method="post" action="/login"><h2>Clienti registrati</h2><label>Email <input type="email" name="le" '
    'required></label><label>Password <input type="password" name="lp" required></label><button type="submit">'
    "Accedi ora</button></form>",
])
def test_a_registered_customers_login_box_beside_a_delivery_form_is_no_gate(lab, box):
    """Review finding (gate B): "Utenti registrati" and "Clienti registrati" (a participle) head a login box, not a
    registration, whatever its submit says; beside the delivery form it is no gate, in the main column or an aside."""
    form = '<form method="post" action="/checkout/save">'
    aside = GUEST_CHECKOUT.replace("</main>", f"</main><aside>{box}</aside>")
    for html in (GUEST_CHECKOUT.replace(form, box + form), aside):
        forms = page_audit(lab, html, "/checkout/")["forms"]
        assert forms["password_required"] and not forms["guest_option"] and not forms["login_required"]


def test_a_login_or_register_box_is_a_login_box(lab):
    """Fable ruling 6 (fixture c20): an "Accedi o registrati" box asks a returning customer for a current password,
    so beside the delivery form it is no gate; alone, with no other way in, it is one (gate C); with "Continua come
    ospite" it offers a guest path. A new-password token still makes it a registration (gate B)."""
    html = (FIXTURES / "shop" / "checkout-accedi-registrati.html").read_text(encoding="utf-8")
    forms = page_audit(lab, html, "/checkout/")["forms"]
    assert forms["password_required"] and not forms["guest_option"] and not forms["login_required"]
    alone = re.sub(r'<section class="delivery">.*?</section>', "", html, flags=re.S)
    assert page_audit(lab, alone, "/checkout/")["forms"]["login_required"]
    guest = page_audit(lab, alone.replace("</form>", "</form><button>Continua come ospite</button>"), "/checkout/")
    assert guest["forms"]["guest_option"] and not guest["forms"]["login_required"]
    placeholder = html.replace("<h2>Accedi o registrati</h2>", "").replace(
        '<input type="password" name="login_password" required>',
        '<input type="password" name="login_password" placeholder="Password (accedi o registrati)" required>')
    assert not page_audit(lab, placeholder, "/checkout/")["forms"]["login_required"]
    new = html.replace('name="login_password" required', 'name="login_password" autocomplete="new-password" required')
    assert page_audit(lab, new, "/checkout/")["forms"]["login_required"]


def test_each_page_is_audited_in_its_declared_language_and_long_metadata_is_cut(lab):
    """Architect ruling 6: <html lang> picks the lexicon when the lexicon has that language, else the run locale."""
    collector, browser = lab
    english = page_audit(lab, """<!doctype html><html lang="en-US"><head><meta charset="utf-8"><title>Basket</title>
      </head><body><main><h1>Your basket is empty</h1></main></body></html>""", "/basket")
    assert english["lexicon_lang"] == "en" and english["cart"]["empty"]
    title, canonical = "Scarpe " * 100, "https://shop.example/" + "x" * 1000
    german = page_audit(lab, f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><title>{title}</title>
      <link rel="canonical" href="{canonical}"></head><body><main><h1>Il carrello è vuoto</h1></main></body></html>""")
    assert german["lexicon_lang"] == "it" and german["cart"]["empty"]
    assert len(german["title"]) <= 300 and len(german["meta"]["canonical"]) <= 300


# ---------------------------------------------------------------- ws1f review 2: account gate, cards, price counts

def login_box(heading: str, label: str, submit: str, placeholder: str = "") -> str:
    attr = f' placeholder="{placeholder}"' if placeholder else ""
    return (f'<div><h2>{heading}</h2><form method="post" action="/login"><label>Email <input type="email" name="le">'
            f'</label><label>{label} <input type="password" name="lp"{attr}></label><button type="submit">{submit}'
            "</button></form></div>")


@pytest.mark.parametrize("box, gate", [
    (login_box("Hai già un account?", "Password scelta in fase di registrazione", "Accedi"), False),
    (login_box("Accedi", "Password", "Accedi", "La password della registrazione"), False),
    (login_box("Hai già effettuato la registrazione?", "Password", "Entra"), False),  # "Entra" is a login submit
    # the same registration words with no login wording anywhere in the box still read as a registration (gate B)
    (login_box("I tuoi dati", "Password scelta in fase di registrazione", "Continua"), True),
    # ws1f review 3 (reg_social): a social login button does not make a registration form a login box
    (login_box("Registrati", "Password scelta per la registrazione", "Crea account").replace(
        "</button>", "</button><button>Accedi con Google</button>"), True),
    # a login-or-register box is a login box (Fable ruling 6): its registration-worded label is a current password
    (login_box("Accedi o registrati", "Password scelta in fase di registrazione", "Continua"), False),
])
def test_registration_words_inside_a_login_box_beside_a_delivery_form_are_no_gate(lab, box, gate):
    """ws1f review 2 (g17, g18, g09): a current-password login box beside the delivery form is no gate (Fable ruling
    on rule d), even when its password's label or placeholder or its heading mentions the registration. A login box
    is one whose words name a login and no registration only: "Registrati" / "Crea account" beside "Accedi con
    Google" is a registration form, and its registration password a gate (ws1f review 3)."""
    form = '<form method="post" action="/checkout/save">'
    forms = page_audit(lab, GUEST_CHECKOUT.replace(form, box + form), "/checkout/")["forms"]
    assert forms["password_required"] and not forms["guest_option"] and forms["login_required"] is gate


WOO_LOGIN_OPEN = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Pagamento</title></head>
<body><main><h1>Pagamento</h1><div>Sei già cliente? <a href="#" class="showlogin">Clicca qui per accedere</a></div>
<form class="woocommerce-form-login" method="post"><label for="u">Nome utente o indirizzo email *</label>
<input name="username" id="u" autocomplete="username"><label for="p">Password *</label>
<input type="password" id="p" name="password" autocomplete="current-password"><button type="submit" name="login">Accedi
</button></form><form name="checkout" method="post" action="/checkout/"><h3>Dettagli di fatturazione</h3>
<label>Nome *<input name="billing_first_name" autocomplete="given-name"></label>
<label>Via e numero *<input name="billing_address_1" autocomplete="address-line1"></label>
<label>CAP *<input name="billing_postcode" autocomplete="postal-code"></label>
<label>Indirizzo email *<input type="email" name="billing_email" autocomplete="email"></label>
<p><label><input type="checkbox" name="createaccount"> <span>Creare un account?</span></label></p>
<div style="display:none"><label for="ap">Crea la password dell'account *</label>
<input type="password" id="ap" name="account_password" autocomplete="new-password"></div>
<button type="button" id="place_order">Effettua ordine</button></form></main></body></html>"""


def test_a_hidden_account_password_leaves_the_billing_form_a_way_in(lab):
    """ws1f review 2 (g03): WooCommerce's checkout form holds the hidden "Creare un account?" password; its billing
    fields are still a way in, so the expanded login form above it is no gate. Architect ruling 2 (ws1f review 3):
    a hidden registration password (new-password, or "Crea la password" wording) never makes its form a password
    form, so a digital-goods billing form without address fields is a way in too; a form that hides another
    password and holds no address field (a two-step login) stays a password form."""
    forms = page_audit(lab, WOO_LOGIN_OPEN, "/checkout/")["forms"]
    assert forms["password_required"] and not forms["guest_option"] and not forms["login_required"]
    digital = re.sub(r"<label>(Via e numero|CAP) \*<input [^>]*></label>\n", "", WOO_LOGIN_OPEN)
    assert "address-line1" not in digital and "postal-code" not in digital
    forms = page_audit(lab, digital, "/checkout/")["forms"]
    assert forms["password_required"] and not forms["guest_option"] and not forms["login_required"]
    wording = digital.replace(' autocomplete="new-password"', "")  # the label alone says it is a new password
    assert not page_audit(lab, wording, "/checkout/")["forms"]["login_required"]
    two_step = page_audit(lab, """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Accesso</title>
      </head><body><main><h1>Accedi per continuare</h1><form method="post" action="/login"><label>Email <input
      type="email" name="email" required></label><input type="password" name="pw" style="display:none">
      <button type="submit">Continua</button></form></main></body></html>""", "/checkout/")["forms"]
    assert not two_step["password_present"] and two_step["login_required"]


def test_a_guest_checkbox_in_english_is_a_guest_option(lab):
    """ws1f review 2 (g15): Shopware's English "Do not create a customer account" is a guest path, like its Italian
    twin; the registration password beside it is then no forced account."""
    forms = page_audit(lab, """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Checkout</title></head>
      <body><main><h1>Checkout</h1><form method="post" action="/account/register"><h2>I am a new customer</h2>
      <label><input type="checkbox" name="guest"> Do not create a customer account</label>
      <label>Email address <input type="email" name="email" autocomplete="email" required></label>
      <label>Password <input type="password" name="password" autocomplete="new-password" required></label>
      <label>Street address <input name="street" autocomplete="address-line1" required></label>
      <button type="submit">Continue</button></form></main></body></html>""", "/checkout/")["forms"]
    assert forms["password_required"] and forms["guest_option"] and not forms["login_required"]


SPA_PAGE = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Checkout</title></head><body>
<header><a href="/">Negozio</a> <form role="search" action="/cerca"><input type="search" name="q"></form></header>
<main>BODY</main></body></html>"""


@pytest.mark.parametrize("body, gate", [
    # a login box with no <form> and no other way in (gate C)
    ('<h1>Accedi per continuare</h1><div class="login"><h2>Accedi</h2><label for="e">Email</label><input id="e" '
     'type="email" autocomplete="email"><label for="p">Password</label><input id="p" type="password" '
     'autocomplete="current-password"><button>Accedi</button></div><p><a href="/register">Registrati</a></p>', True),
    # a registration box with no <form>: named by the heading above it and its button (gate B)
    ('<h1>Crea il tuo account</h1><div class="reg"><label for="e">Email</label><input id="e" type="email"><label '
     'for="p">Password</label><input id="p" type="password"><button>Registrati</button></div>', True),
    # a form-less login box beside a form-less delivery section: the delivery fields are a way in
    ('<h1>Checkout</h1><div class="login"><h2>Hai già un account?</h2><label>Email <input type="email"></label>'
     '<label>Password <input type="password" required></label><button>Accedi</button></div><div class="ship"><h2>'
     'Spedizione</h2><label>Email <input type="email" autocomplete="email"></label><label>Nome <input '
     'autocomplete="given-name"></label><label>Indirizzo <input autocomplete="address-line1"></label><button>Continua'
     "</button></div>", False),
])
def test_account_boxes_without_a_form_element(lab, body, gate):
    """ws1f review 2 (g06, g07): a single-page app's account box has no <form>; the box around the shown password
    (another field and a button, no address field) counts as one."""
    forms = page_audit(lab, SPA_PAGE.replace("BODY", body), "/checkout/")["forms"]
    assert forms["password_required"] and not forms["guest_option"] and forms["login_required"] is gate


SPA_SHIP = ('<div class="ship"><h2>Spedizione</h2><label>Nome <input autocomplete="given-name"></label><label>'
            'Indirizzo <input autocomplete="address-line1"></label><label>CAP <input autocomplete="postal-code">'
            "</label></div>")
SPA_CONTACT = ('<h1>Checkout</h1><div class="step"><h2>Contatti</h2><label>Email <input type="email" '
               'autocomplete="email"></label><div class="pw"><label>Password <input type="password" '
               'autocomplete="current-password"></label><button>Accedi</button></div><button>Continua</button></div>')


@pytest.mark.parametrize("body, gate", [
    # architect ruling 1: a newsletter email beside a form-less login box is no way to enter one's details
    ('<h1>Il mio account</h1><div class="wrap"><div class="login"><label>Password <input type="password"></label>'
     '<button>Accedi</button></div><div class="nl"><label>Email <input type="email" autocomplete="email"></label>'
     "<button>Iscriviti alla newsletter</button></div></div>", True),
    # architect ruling 7: the email of the login's own box belongs to the login, whatever "Continua" does
    (SPA_CONTACT, True),
    # ... and the delivery section beside it is a way in
    (SPA_CONTACT + SPA_SHIP, False),
])
def test_what_a_form_less_login_box_holds_and_what_it_stands_beside(lab, body, gate):
    """Architect rulings 1 and 7 (ws1f review 3). The box of a form-less login password (at most 4 levels up, below
    main, another field plus a button, no form or address field) stands in for the login's form: an email-only
    newsletter box beside it in one wrapper falls in that box and is no entry path, so the page is a gate (the rare
    gap: a newsletter <form> in main beside a login form would count as an entry path; footers are excluded). A field
    in the same form or box as the login password is the login's field: a contact step whose email sits above an
    inline login password is a gate until a delivery section or guest wording offers another way in (Magento-like
    "Sign in or continue as guest" pages say so, and the guest lexicon takes precedence)."""
    forms = page_audit(lab, SPA_PAGE.replace("BODY", body), "/checkout/")["forms"]
    assert forms["password_required"] and not forms["guest_option"] and forms["login_required"] is gate


NEGATED_GUEST = """<!doctype html><html lang="LANG"><head><meta charset="utf-8"><title>Checkout</title></head>
<body><main><h1>Checkout</h1><p>NOTE</p><form method="post"><h2>REG</h2><label>Email <input type="email" name="e"
required></label><label>Password <input type="password" name="p" required></label><button type="submit">SUBMIT</button>
</form><form><label>Nome <input autocomplete="given-name"></label><label>Indirizzo <input autocomplete="address-line1">
</label><button>Continua</button></form></main></body></html>"""


@pytest.mark.parametrize("lang, note, guest", [
    ("it", "Per completare l'ordine è necessario registrarsi: non è possibile acquistare senza creare un account.",
     False),
    ("it", "Non è possibile acquistare senza registrazione.", False),
    ("en", "You cannot check out without creating an account.", False),
    ("it", "Non è possibile modificare l'ordine senza account. Puoi comunque continuare come ospite.", True),
    ("it", "Acquista senza registrazione, non è necessario un account.", True),
])
def test_guest_wording_its_sentence_denies_is_no_guest_exit(lab, lang, note, guest):
    """ws1f review 3: a forced-account page that states its rule ("non è possibile acquistare senza creare un
    account") offers no guest exit, so its registration password beside the delivery form stays a gate (gate B);
    the denial is read per sentence, and guest wording in another sentence still counts."""
    words = ("Registrati", "Crea account") if lang == "it" else ("Register", "Create account")
    html = (NEGATED_GUEST.replace("LANG", lang).replace("NOTE", note).replace("REG", words[0])
            .replace("SUBMIT", words[1]))
    forms = page_audit(lab, html, "/checkout/")["forms"]
    assert forms["password_required"] and forms["guest_option"] is guest and forms["login_required"] is not guest


@pytest.mark.parametrize("body, gate", [
    # the heading right above the form (its sibling) names it
    ('<h1>Registrati</h1><form method="post"><label>Email <input type="email" name="e"></label><label>Password <input '
     'type="password" name="p" required></label><button type="submit">Continua</button></form>', True),
    # the nearest heading before the form inside an ancestor that holds no other form names it
    ('<h1>Registrati</h1><div class="box"><form method="post"><label>Email <input type="email" name="e"></label><label>'
     'Password <input type="password" name="p" required></label><button type="submit">Continua</button></form></div>',
     True),
    # an email-only registration start with no password and no gate wording is no gate
    ('<h1>Registrati</h1><div class="box"><form method="post"><label>Email <input type="email" name="e"></label>'
     '<button type="submit">Continua</button></form></div>', False),
])
def test_a_form_without_a_heading_of_its_own_is_named_by_the_nearest_one_above_it(lab, body, gate):
    """Fable ruling 2 (ws1f doubts): a page whose only form sits under "Registrati" is a registration page; the
    heading alone never makes a gate (gate B still needs a required or unlabelled password)."""
    forms = page_audit(lab, SPA_PAGE.replace("BODY", body), "/checkout/")["forms"]
    assert forms["password_required"] is gate and forms["login_required"] is gate and not forms["guest_option"]


def product_page(own: str, cards: int = 6) -> str:
    related = "".join(
        f'<div class="card"><a href="/p/{n}"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width="150" '
        f'height="100" alt="p{n}"><h3>Prodotto {n}</h3></a><p><span>{20 + n},90 €</span> <del>{30 + n},90 €</del></p>'
        f"<p>Prezzo più basso negli ultimi 30 giorni: {25 + n},90 €</p><p>Solo 2 rimasti!</p></div>"
        for n in range(cards))
    return ('<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Scarpa Run</title><style>.cards{display:'
            'flex;gap:8px}.card{width:180px}</style></head><body><header><a href="/">Negozio</a></header><main>'
            f'<div class="cards">{own}{related}</div></main></body></html>')


@pytest.mark.parametrize("cards", [6, 2])
def test_a_products_own_block_beside_its_related_cards_is_not_a_card(lab, cards):
    """ws1f review 2 (p2): the product's own block (it holds the h1) sits in the same parent as the related cards;
    its price, scarcity and lowest-30-days line are the page's own (in_card False), as deception._own reads them.
    ws1f review 3 (own2): beside only two related cards the group keeps those two, so the page price stays the own
    block's (before, the group of two was dropped and the h1-distance pick took a related card's 20,90 €)."""
    own = ('<div class="product"><a href="/guida">Guida alle taglie</a><h1>Scarpa Run</h1><p><span>49,90 €</span> '
           "<del>69,90 €</del></p><p>Prezzo più basso negli ultimi 30 giorni: 59,90 €</p><p><strong>Solo 3 rimasti!"
           "</strong></p><button>Aggiungi al carrello</button></div>")
    audit = page_audit(lab, product_page(own, cards), "/scarpa-run")
    assert audit["products"]["cards_count"] == cards and audit["pdp"]["price"]["value"] == 49.9
    assert [(p["value"], p["in_card"]) for p in audit["prices"] if p["strikethrough"]][0] == (69.9, False)
    assert [(i["number"], i["in_card"]) for i in audit["persuasion"]["scarcity"]] == [(3, False), (2, True)]
    said = audit["persuasion"]["lowest_price_30d"]
    assert [i["in_card"] for i in said] == [False] + [True] * cards
    assert classify(audit, audit["url"])["type"] == "pdp"


def sale_listing(cards: int, statements: int) -> str:
    """A listing of struck card prices; the first `statements` cards carry their own lowest-30-days statement."""
    items = "".join(
        f'<li><a href="/p/{n}.html"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width=150 height=100 '
        f'alt="Articolo {n}"><h3>Articolo {n}</h3></a><p><span>{20 + n},90 €</span> <del>{30 + n},90 €</del></p>'
        + (f"<p>Prezzo più basso negli ultimi 30 giorni: {25 + n},90 €</p>" if n < statements else "") + "</li>"
        for n in range(cards))
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Saldi</title></head>
      <body><main><h1>Saldi</h1><p>{cards} prodotti</p><ul>{items}</ul></main></body></html>"""


def test_price_counts_cover_every_price_past_the_payload_cut(lab):
    """Fable ruling 5: `prices` keeps 80 items in document order, so a 48-card sale listing drops its last cards'
    struck prices; price_counts counts every price read, struck ones inside and outside the cards apart."""
    audit = page_audit(lab, sale_listing(48, 0), "/saldi")
    assert len(audit["prices"]) == 80 and sum(p["strikethrough"] for p in audit["prices"]) == 40
    assert audit["price_counts"] == {"total": 96, "strikethrough_in_card": 48, "strikethrough_outside_cards": 0}
    assert audit["products"]["cards_count"] == 48


@pytest.mark.parametrize("statements, compliant", [(48, True), (40, False), (30, False)])
def test_struck_prices_past_the_payload_cut_still_need_their_statement(lab, statements, compliant):
    """The statements' prices fill the 80-item cut too: PTI.STRIKETHROUGH_LOWEST30 counts every struck price."""
    from test_engagement_checks import one, page, run_of

    from jev_ultrafast.engagement import checks

    audit = page_audit(lab, sale_listing(48, statements), "/saldi")
    assert audit["price_counts"]["strikethrough_in_card"] == 48
    assert sum(p["strikethrough"] for p in audit["prices"]) < 48  # the cut left some out
    row = one(checks.observations(run_of([page("plp", audit=audit)])), "PTI.STRIKETHROUGH_LOWEST30", stage="plp")
    assert row["value"] is compliant and row["evidence"]["in_cards"]["strikethrough_prices"] == 48


def test_an_open_variant_dropdown_on_a_listing_is_reported_and_the_page_stays_a_listing(lab):
    """Fable ruling 3: role="option" items under a "Taglia" label in main are a variant group candidate; on a
    listing (>= 12 cards) the group is reported but the page still classifies as a listing."""
    cards = "".join(
        f'<li><a href="/p/{n}.html"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width=150 height=100 '
        f'alt="Articolo {n}"><h3>Articolo {n}</h3></a><p><span>{20 + n},90 €</span></p></li>' for n in range(14))
    audit = page_audit(lab, f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Scarpe</title></head>
      <body><main><h1>Scarpe da corsa</h1><div><span>Taglia</span><div role="listbox"><div role="option"
      aria-selected="false">40</div><div role="option" aria-selected="false">41</div><div role="option"
      aria-selected="false">42</div></div></div><p>14 prodotti</p><ul>{cards}</ul></main></body></html>""", "/scarpe")
    groups = audit["pdp"]["variant_groups"]
    assert [(g["label"], g["options"], g["selected"]) for g in groups] == [("Taglia", ["40", "41", "42"], False)]
    assert audit["products"]["cards_count"] == 14 and classify(audit, audit["url"])["type"] == "plp"


def test_only_a_rendered_payment_frame_is_a_payment_field(lab):
    """WS7 real-shop smoke: Klarna's web SDK loads a display:none backend_bridge_iframe on every page; counted as a
    payment field it made editorial pages "checkout" (+0.3 with no other signal). A rendered card frame still counts."""
    page = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Autunno Inverno</title></head>
      <body><main><h1>La collezione autunno inverno</h1><p>Scarpe fatte a mano in Italia, pensate per durare.</p>
      </main>FRAME<iframe id="klarna-communication-iframedefault" style="display:none"
      src="/klarna/web-sdk/v1/backend_bridge_iframe.html"></iframe></body></html>"""
    audit = page_audit(lab, page.replace("FRAME", ""), "/pages/autunno-inverno")
    assert audit["forms"]["payment_iframes"] == 0 and not audit["forms"]["cc_present"]
    assert classify(audit, audit["url"])["type"] != "checkout"
    card = '<iframe title="Secure card payment input frame" src="/stripe/elements-inner-card.html" width=300 height=40>'
    audit = page_audit(lab, page.replace("FRAME", card + "</iframe>"), "/pages/autunno-inverno")
    assert audit["forms"]["payment_iframes"] == 1 and audit["forms"]["cc_present"]


def test_an_iso_code_after_a_dollar_amount_names_its_currency(lab):
    """WS7 real-shop smoke (Shopify's Dawn demo): "$485.00 CAD" was read as USD, so PTI.PRICE_JSONLD_MATCH failed
    against the JSON-LD's CAD. Only a currency code counts: "$5 OFF" stays a dollar amount."""
    audit = page_audit(lab, """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Small Naomi</title>
      <script type="application/ld+json">{"@context": "https://schema.org", "@type": "Product", "name": "Small Naomi",
      "offers": {"@type": "Offer", "price": "485.00", "priceCurrency": "CAD"}}</script></head>
      <body><main><h1>Small Naomi</h1><p><span>$485.00 CAD</span></p><p>$5 OFF your first order</p>
      <button style="background:#000;color:#fff;padding:14px 30px">Add to cart</button></main></body></html>""",
                       "/products/small-naomi")
    assert (audit["pdp"]["price"]["value"], audit["pdp"]["price"]["currency"]) == (485, "CAD")
    assert audit["pdp"]["structured"]["currency"] == "CAD"
    assert {(p["value"], p["currency"]) for p in audit["prices"]} == {(485, "CAD"), (5, "USD")}


# ---------------------------------------------------------------- review round 0: cart-action links, shipping cost


WOO_CARD = """<li class="product"><a href="/prodotto/{slug}/" class="woocommerce-LoopProduct-link"><img
  src="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22200%22 height=%22200%22/%3E" alt=""
  width="200" height="200"><h2>{title}</h2><span class="price">29,90&nbsp;&euro;</span></a><a href="?add-to-cart={n}"
  class="button add_to_cart_button" aria-label="Aggiungi al carrello: &ldquo;{title}&rdquo;" rel="nofollow">Aggiungi al
  carrello</a></li>"""
WOO_LISTING = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Maglieria</title></head><body>
<div id="cart-drawer" hidden><a href="/carrello/?remove_item=3f2a&amp;_wpnonce=9c1"
aria-label="Rimuovi questo articolo">×</a></div>
<header><a href="/">Bottega</a> <a href="/carrello/">Carrello (1)</a></header><main><h1>Maglieria</h1>
<ul class="products">{cards}</ul></main></body></html>"""


def test_a_card_link_is_never_a_cart_action_nor_the_cart_link_a_remove_link(lab):
    """WooCommerce's "?add-to-cart=N" link carries the card's longest name (its aria-label); a hidden mini-cart remove
    link comes before the header's cart link. Neither is a product or cart link: a GET of either changes the cart."""
    titles = ["Felpa", "Gonna", "Borsa", "Cappello", "Sciarpa", "Guanti"]
    cards = "".join(WOO_CARD.format(slug=t.lower(), title=t, n=n) for n, t in enumerate(titles, 11))
    audit = page_audit(lab, WOO_LISTING.format(cards=cards), "/negozio/")
    hrefs = [c["href"] for c in audit["products"]["cards"]]
    assert [h.split("/", 3)[3] for h in hrefs] == [f"prodotto/{t.lower()}/" for t in titles]
    assert [c["title"] for c in audit["products"]["cards"]] == titles
    assert audit["nav"]["cart_link"]["href"].endswith("/carrello/")


@pytest.mark.parametrize("line, shown", [
    ("Spedizione 4,90 €, gratuita per ordini sopra i 59 €", True),
    ("Spedizione gratuita in tutta Italia", True),
    ("Imposte incluse. Spese di spedizione calcolate al momento del pagamento.", False),
    ("Tax included. Shipping calculated at checkout.", False),
    ("Shipping country: United States", False),
])
def test_a_product_page_shipping_line_is_a_cost_or_nothing(lab, line, shown):
    """PTI.SHIPPING_COST_PRE_CHECKOUT reads pdp.shipping_text: an amount or free-shipping wording; a cost left to the
    checkout, or a shipping word alone, is none."""
    html = f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Felpa Bosco</title></head><body>
      <header><a href="/">Bottega</a></header><main><h1>Felpa Bosco</h1><p>29,90 €</p>
      <button>Aggiungi al carrello</button><p>{line}</p></main></body></html>"""
    assert bool(page_audit(lab, html, "/prodotto/felpa/")["pdp"]["shipping_text"]) is shown


@pytest.mark.parametrize("text, scarce", [
    ("Rimasti solo 3 giorni di saldi!", False), ("Rimaste solo 4 ore", False), ("Rimasti solo 30 giorni", False),
    ("Rimasti solo 3 pezzi", True), ("Rimasti solo 3!", True), ("Ne sono rimasti solo 2 in magazzino", True),
    ("Only 3 days left!", False), ("Only 3 left in stock", True),
])
def test_a_remaining_time_is_no_scarcity(text, scarce):
    assert bool(compile_lexicon(lexicon_for("it"))["scarcity"].search(text)) is scarce


STEPPER_CART = """<!doctype html><html lang="it"><head><meta charset="utf-8"><title>Carrello</title></head><body>
<main><h1>Il tuo carrello</h1><ul class="cart-items">
<li class="cart-item"><a href="/p/1">TITLE</a> <span>VARIANT</span>
<span class="stepper"><button aria-label="Diminuisci">−</button> <span>1</span> <button aria-label="Aumenta">+</button>
</span> <span class="price">ROW</span> <button>Rimuovi</button></li></ul>
<dl class="totals"><dt>Subtotale</dt><dd>129,00 €</dd><dt>Spedizione</dt><dd>4,90 €</dd><dt>Costo di gestione</dt>
<dd>15,00 €</dd><dt>Totale</dt><dd>148,90 €</dd></dl>
<a href="/checkout" style="display:inline-block;background:#000;color:#fff;padding:14px 40px">Procedi al checkout</a>
</main></body></html>"""


@pytest.mark.parametrize("title, variant, row, qty", [
    ("Scarpa Air Max 90", "Taglia 42", "129,00 €", None), ("Console Xbox 360 Slim", "Nera", "129,00 €", None),
    ("Orologio Fenix 7 Pro", "Grigio", "129,00 €", None), ("Tavolo Rovere", "120 x 80 cm", "129,00 €", None),
    ("Scarpa Pegasus", "Taglia 42", "43,00 € × 3", 3), ("Scarpa Pegasus", "Taglia 42", "3 × 43,00 €", 3),
    ("Scarpa Pegasus", "Taglia 42 × 3", "129,00 €", None),  # a number times a number reads as a size
])
def test_a_number_in_the_product_name_is_no_cart_quantity(lab, title, variant, row, qty):
    """A row without a quantity field: only a multiplication sign that is its own token outside the title is a
    quantity ("Air Max 90" is no quantity of 90, "120 x 80 cm" is a size); the oracles read None as one."""
    html = STEPPER_CART.replace("TITLE", title).replace("VARIANT", variant).replace("ROW", row)
    cart = page_audit(lab, html, "/carrello")["cart"]
    assert [(line["title"], line["qty"]) for line in cart["line_items"]] == [(title, qty)]


OFF_CANVAS = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Scarpa Aurora</title>
<style>body{margin:0;font-family:sans-serif}.drawer{position:fixed;top:0;height:100%;width:85%;background:#fff}
.menu{left:0;transform:translateX(-100%)}.cartd{right:0;transform:translateX(100%)}
.btn{display:block;background:#c00;color:#fff;border:0;padding:16px 20px;font-size:18px;margin:12px}</style></head>
<body><header><button aria-label="Menu">☰</button> <a href="/carrello" aria-label="Carrello">🛒</a></header>
<nav class="drawer menu" aria-label="Menu principale"><form role="search" action="/cerca"><input type="search" name="q"
placeholder="Cerca" style="width:260px"></form><a href="/donna">Donna</a> <a href="/uomo">Uomo</a></nav>
<aside class="drawer cartd"><h2>Carrello</h2><p>Scarpa Aurora 89,00 €</p><a class="btn" href="/checkout">Procedi al
checkout</a><a class="btn" href="/carrello">Vai al carrello</a></aside>
<main><h1>Scarpa Aurora</h1><div class="price">89,00 €</div><button class="btn">Aggiungi al carrello</button>
<p>Scarpa da corsa leggera con suola ammortizzata, adatta a corse lunghe su strada e su sterrato.</p></main>
</body></html>"""


def test_closed_off_canvas_drawers_are_not_on_the_first_screen(lab):
    """A menu and a cart drawer parked beside the viewport (translateX(±100%)): their search field is not shown, their
    buttons are no CTAs and their links no tap targets; open, they count."""
    audit = page_audit(lab, OFF_CANVAS, "/p/aurora")
    assert (audit["search"]["present"], audit["search"]["hidden_inputs"]) == (False, 1)
    primary = [c["label"] for c in audit["ctas"] if c["primary_like"] and c["above_fold"]]
    assert primary == ["Aggiungi al carrello"] and audit["pdp"]["add_to_cart"]["present"]
    assert not any(c["label"].startswith(("Procedi", "Vai al")) for c in audit["ctas"])
    assert audit["targets"]["interactive"] == 3  # the menu button, the cart icon, the add-to-cart
    opened = page_audit(lab, OFF_CANVAS.replace("translateX(-100%)", "none").replace("translateX(100%)", "none"),
                        "/p/aurora")
    assert opened["search"]["present"] and opened["search"]["above_fold"]
    assert opened["targets"]["interactive"] > 3


DECLARED_EN = """<!doctype html><html lang="en-US"><head><meta charset="utf-8"><title>Scarpa Aurora</title></head><body>
<main><h1>Scarpa da corsa Aurora</h1><div class="price">89,00 €</div><button>Aggiungi al carrello</button>
<p>Spedizione gratuita sopra 50 €. Consegna prevista entro 3 giorni lavorativi. Il prezzo include l'IVA.</p>
<p>Una scarpa leggera con la suola ammortizzata, per le corse lunghe su strada e per gli allenamenti di tutti i giorni.
Resi gratuiti entro 30 giorni dalla consegna, anche in negozio.</p></main></body></html>"""


def test_an_italian_page_whose_theme_declares_english_is_read_in_italian(lab):
    """A theme default lang="en-US" on Italian copy: the page is audited with the Italian lexicon (which includes
    English), so its add-to-cart and shipping are found; English copy under the same declaration stays English."""
    audit = page_audit(lab, DECLARED_EN, "/p/aurora")
    assert audit["lang"].startswith("en") and audit["lexicon_lang"] == "it"
    assert audit["pdp"]["add_to_cart"]["present"] and audit["pdp"]["shipping_text"]
    english = DECLARED_EN.split("<main>")[0] + """<main><h1>Aurora running shoe</h1><div class="price">89,00 €</div>
<button>Add to cart</button><p>Free shipping over 50 €. Delivery within 3 working days. The price includes VAT.</p>
<p>A light shoe with a cushioned sole for long road runs and for all the training you do every day. You can return it
for free within 30 days of delivery, and at our store too.</p></main></body></html>"""
    assert page_audit(lab, english, "/p/aurora")["lexicon_lang"] == "en"
