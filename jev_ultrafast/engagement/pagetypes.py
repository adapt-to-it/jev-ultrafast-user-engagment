"""Page-type classification from one AuditPayload and its URL: additive evidence per type, never site-specific.

Each type collects weighted signals (structured data, observed controls, lexicon hits, URL patterns); the highest
score wins when it reaches MIN_SCORE, otherwise the page is "other". Confidence combines the winning score with its
margin over the runner-up, so two close candidates give a low confidence even when both scores are high.

Words are matched in the page's effective language (lexicon.effective_language): the collector passes it as locale
(and records it as audit["lexicon_lang"]), so audit.js and the classifier read a page with the same lexicon. A
checkout URL alone is not a checkout page: it needs a form, a guest choice or a checkout control or heading too (a
"/pagamento.html" information page has none). Personal-data fields on a contact page are not a checkout.
"""

import re
from functools import lru_cache
from urllib.parse import parse_qsl, urlsplit

from .lexicon import compile_lexicon, effective_language, lexicon_for
from .schemas import PAGE_TYPES, AuditPayload, Classification

MIN_SCORE = 0.3
BASELINE_OTHER = 0.2
CAPTCHA_FRAMES = re.compile(r"captcha|challenges?\.|turnstile|arkoselabs|funcaptcha|perimeterx|datadome", re.I)
# A locale prefix is a language code (optionally with a region) or a store country: "/it/", "/en-gb", "/us/";
# "/tv" or "/pc" is a category.
LOCALES = ("it|en|de|fr|es|pt|nl|pl|sv|da|fi|no|nb|nn|cs|sk|hu|ro|bg|el|hr|sl|sr|ru|uk|tr|ar|he|ja|zh|ko|lt|lv|et|"
           "ca|eu|gl|ga|is|mt|lb|us|gb|ch|at|be|au|ie|br|mx|int")
HOME_PATH = re.compile(rf"^/?((({LOCALES})([-_][a-z]{{2}})?)/?)?((index|home|default)\.(html?|php|aspx?))?$", re.I)
# Query keys that do not change which page is shown (tracking, language); any other key routes somewhere else
# ("index.php?route=checkout/checkout", "/?page_id=12", "/?s=scarpe") unless it names the home ("route=common/home").
NEUTRAL_QUERY = re.compile(r"^(utm_[a-z_]+|gclid|fbclid|msclkid|dclid|srsltid|mc_[a-z]+|ref|lang|language|locale|"
                           r"currency|country|_ga|_gl)$", re.I)
HOME_ROUTE = re.compile(r"(^|/)(home|index)$", re.I)


@lru_cache(maxsize=8)
def _lexicon(language: str) -> dict[str, re.Pattern]:
    return compile_lexicon(lexicon_for(language))


def _home_path(path: str, query: str = "") -> bool:
    """"/", "/it/", "/en-gb", "/index.html" and a directory's index page ("/shop/index.html"), without a query that
    routes elsewhere ("/index.php?route=checkout/checkout" is not a home page)."""
    if any(not NEUTRAL_QUERY.match(key) and not HOME_ROUTE.search(value)
           for key, value in parse_qsl(query or "", keep_blank_values=True)):
        return False
    if HOME_PATH.match(path or "/"):
        return True
    last = (path or "").rstrip("/").rsplit("/", 1)[-1]
    return bool(re.fullmatch(r"(index|home|default)\.(html?|php|aspx?)", last, re.I))


def classify(audit: AuditPayload, url: str, *, locale: str | None = None) -> Classification:
    audit = audit or {}
    language = locale or audit.get("lexicon_lang") or effective_language(audit.get("lang"), "it")
    lx = _lexicon(language.split("-")[0].lower() or "it")
    parts = urlsplit(url or "")
    path = parts.path or "/"
    full = path + ("?" + parts.query if parts.query else "")  # URL patterns read the path and the query
    doc, meta, nav = audit.get("doc") or {}, audit.get("meta") or {}, audit.get("nav") or {}
    pdp, products, filters = audit.get("pdp") or {}, audit.get("products") or {}, audit.get("filters") or {}
    cart, forms = audit.get("cart") or {}, audit.get("forms") or {}
    types = meta.get("jsonld_types") or {}
    title = audit.get("title") or ""
    heading = " ".join(doc.get("h1s") or [])
    sample = doc.get("text_sample") or ""
    scores = dict.fromkeys(PAGE_TYPES, 0.0)
    scores["other"] = BASELINE_OTHER
    signals: list[str] = []

    def add(kind: str, weight: float, why: str) -> None:
        scores[kind] += weight
        signals.append(f"{kind}:{why}")

    def hit(key: str, text: str) -> bool:
        return bool(text and key in lx and lx[key].search(text))

    # challenge: anti-bot wording, a captcha frame, a tiny document
    tiny = (doc.get("element_count") or 0) < 150 and (nav.get("links") or 0) < 6 and not products.get("cards_count")
    if hit("challenge", " ".join((title, heading, sample[:600]))):
        add("challenge", 0.5, "challenge wording")
        if tiny:
            add("challenge", 0.3, "tiny document")
    if any(CAPTCHA_FRAMES.search(host) for host in doc.get("frames") or []):
        add("challenge", 0.4, "captcha frame")

    # product detail
    product_count = (types.get("Product") or 0) + (types.get("ProductGroup") or 0)
    if 1 <= product_count <= 2 and pdp.get("structured"):
        add("pdp", 0.45, "JSON-LD Product")
    if "product" in (meta.get("og_type") or "").lower():
        add("pdp", 0.2, "og:type product")
    add_to_cart = pdp.get("add_to_cart") or {}
    if add_to_cart.get("present") and (pdp.get("add_to_cart_count") or 1) <= 2:
        add("pdp", 0.25, "add-to-cart control")
        if add_to_cart.get("above_fold"):
            add("pdp", 0.1, "add-to-cart above the fold")
    if (pdp.get("price") or {}).get("above_fold") and heading:
        add("pdp", 0.1, "main price above the fold")
    if pdp.get("variant_selector") not in (None, "none"):
        add("pdp", 0.1, "variant selector")
    if not product_count and 1 <= (meta.get("microdata_main") or 0) <= 2:
        add("pdp", 0.3, "microdata Product")
    if hit("product_url", full):
        add("pdp", 0.15, "product URL pattern")

    # listing
    main_group = products.get("main_group") or 0
    if main_group >= 12:
        add("plp", 0.45, f"{main_group} product cards")
    elif main_group >= 4:
        add("plp", 0.3, f"{main_group} product cards")
    if (products.get("itemlist_jsonld") or 0) >= 4:
        add("plp", 0.35, "JSON-LD ItemList")
    if product_count >= 3:
        add("plp", 0.3, "several JSON-LD products")
    if (filters.get("controls") or 0) >= 2:
        add("plp", 0.15, "filters")
    if (filters.get("sort") or {}).get("present"):
        add("plp", 0.1, "sort control")
    if filters.get("result_count_text"):
        add("plp", 0.1, "result count")
    if hit("category_url", full):
        add("plp", 0.15, "category URL pattern")

    # cart
    items = cart.get("line_items") or []
    if any(i.get("qty_editable") or i.get("removable") for i in items):
        add("cart", 0.35, f"{len(items)} editable line items")
    if (cart.get("checkout_cta") or {}).get("present"):
        add("cart", 0.3, "checkout call to action")
    if cart.get("total_text") or cart.get("subtotal_text"):
        add("cart", 0.15, "order totals")
    if cart.get("empty"):
        add("cart", 0.45, "empty-cart message")
    cart_url = hit("cart_url", full)
    if cart_url:
        add("cart", 0.3, "cart URL pattern")
    if hit("cart", heading or title) and not hit("add_to_cart", heading or title):
        add("cart", 0.2, "cart heading")

    # checkout (first step)
    address = forms.get("address_fields") or 0
    checkout_url = hit("checkout_url", full) and not cart_url and not hit("info_url", full)  # Magento /checkout/cart/
    contact = (hit("contact", heading) or hit("contact", path)) and not checkout_url
    if contact:
        pass  # a contact form asks for a name, an email and a phone too
    elif address >= 4:
        add("checkout", 0.4, f"{address} personal-data fields")
    elif address >= 2 and (forms.get("visible") or 0) >= 4:
        add("checkout", 0.2, f"{address} personal-data fields")
    if forms.get("cc_present"):
        add("checkout", 0.3, "payment fields")
    checkout_heading = hit("checkout_title", heading) or hit("checkout_title", title)
    pay_control = any(c.get("lexicon_hit") in ("pay_now", "place_order") for c in audit.get("ctas") or [])
    if checkout_url:
        steps = ((forms.get("visible") or 0) > 0 or forms.get("password_present") or forms.get("cc_present")
                 or forms.get("guest_option") or checkout_heading or pay_control)
        add("checkout", 0.35 if steps else 0.2, "checkout URL pattern" if steps else "checkout URL pattern alone")
    if checkout_heading:
        add("checkout", 0.2, "checkout heading")
    if pay_control:
        add("checkout", 0.25, "pay or place-order control")

    # home
    if _home_path(path, parts.query):
        add("home", 0.45, "root path")
        if len(nav.get("categories") or []) >= 3:
            add("home", 0.1, "category navigation")
        if (types.get("WebSite") or types.get("Organization")) and not product_count:
            add("home", 0.1, "WebSite/Organization JSON-LD")

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, top), (_, second) = ranked[0], ranked[1]
    if top < MIN_SCORE:
        best = "other"
    confidence = min(1.0, top) * (0.5 + 0.5 * (1 - second / top)) if top > 0 else 0.0
    return {"type": best, "confidence": round(confidence, 3), "scores": {k: round(v, 3) for k, v in scores.items()},
            "signals": signals[:24]}
