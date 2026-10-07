"""Independent journey outcome checks: a closed set of oracles that read the actual page state.

The agent's DONE is never evidence. Every oracle reads the shop again after the journey, and only the shop: a page on
another registrable domain than the one the start URL landed on never passes (checks["same_site"]).

The cart oracles read the cart's line items with audit.js. When the journey ended on the cart, they read that tab
where it is: the request that led there may have changed the cart (a GET add-to-cart link), so it is never sent
again, and the journey's single isolated tab is the cart's only writer, so the page as shown is the current state.
Otherwise they open the cart page in a new tab of the journey's browser context (same cookies and storage),
found without the agent: a page of the journey that the classifier recognised as the cart, a visited URL matching
the lexicon's cart URL patterns, or the cart link audit.js reads on the final page (same registrable domain only).
Query keys that name a cart action (add, add-to-cart, remove_item, quantity, aggiungi, ...) are dropped before the load
(checks["dropped_query_keys"]), a URL whose path names one (/cart/add) is never loaded, and a visited URL that keeps a
query after that comes after the shop's own cart link. The page oracles audit the journey's final page.

Each oracle returns {"passed": bool | None, "checks": {...}} with small, JSON-serialisable checks; passed is None only
together with checks["not_assessable"] (the outcome cannot be read without a guess). A cart oracle also returns
"page", the PageRecord of the cart page it read (evidence for run["pages"]). A harness read failure is never the
shop's: a cart page audit.js could not read at all is "cart_unreadable", and a final page it could not read is
"final_page_unreadable" (for a cart oracle, when no visited cart page leads to the cart either). A page the browser
did not load (chrome-error://) is not audited: it is no page of the shop (type "other", signal navigation_error). A
cart that shows a positive subtotal (or total) and is not marked empty, but whose rows audit.js could not read (rows
without quantity or remove controls), is "cart_items_unreadable"; a product row whose price could not be read, when no
other row passes, is "cart_price_unreadable". An empty cart (marked empty, a zero subtotal, or rows that are all
add-ons) and prices read above the limit are failures.

A cart row shows one price, either the unit price or the line total (unit price times quantity). The cart's own
arithmetic decides which (checks["price_reading"]): the row prices add up to the subtotal (line totals) or their
products with the quantities do (unit prices); without a subtotal, two amounts in the row where one is the other times
the quantity. Anything else is "ambiguous": the row price counts undivided, the larger reading, so an oracle never
passes on a guess; and when only the smaller reading would pass, the verdict is not assessable
("cart_price_ambiguous") rather than a failure charged to the shop.
"""

import math
import re
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from . import lexicon
from .lexicon import compile_lexicon, lexicon_for, registrable_domain
from .pagetypes import classify

# name -> {param: (required, kind)}; kind "number" (> 0) or "text" (non-empty, <= 200 chars)
PARAMS: dict[str, dict[str, tuple[bool, str]]] = {
    "cart_contains_item_under_price": {"max_price": (True, "number")},
    "cart_not_empty": {},
    "pdp_reached": {"query": (False, "text"), "max_price": (False, "number")},
    "search_results_shown": {"query": (False, "text")},
}
PRICE = re.compile(r"\d{1,3}(?:[.\s']\d{3})+(?:,\d{1,2})?(?!\d)"  # 1.299,00 or 1 299
                   r"|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?(?!\d)"  # 1,299.00
                   r"|\d+(?:[.,]\d{1,2})?")  # 49,90 or 49.90
WORD = re.compile(r"[^\W\d_]{3,}")
# words a query shares with any title ("zaino per laptop" is not found in "Custodia per tablet")
STOP_WORDS = frozenset().union(*lexicon.FUNCTION_WORDS.values(), "sotto sopra senza tra fra under over into".split())
CURRENCY = r"(?:[€$£]|\b(?:eur|euro|usd|gbp|chf)\b)"
# a currency mark directly followed by a number belongs to that number ("Qtà 2 €60,00": the 2 is a quantity)
CURRENCY_BEFORE = re.compile(CURRENCY + r"\s?$", re.I)
CURRENCY_AFTER = re.compile(r"^\s?" + CURRENCY + r"(?!\s?\d)", re.I)
MAX_ITEMS = 5
# query keys and path segments that make a GET change the cart (WooCommerce ?add-to-cart=7, ?remove_item=...,
# PrestaShop ?add=1, Shopify /cart/add): a cart URL is never loaded with them
CART_ACTION_KEYS = re.compile(rf"^(?:{lexicon.CART_ACTION_KEYS})$", re.I)
CART_ACTION_SEGMENTS = set(lexicon.CART_ACTION_SEGMENTS)


def check_params(name: str, params: dict | None) -> dict:
    """Validated oracle parameters (ValueError for an unknown oracle, a missing, unknown or malformed parameter)."""
    if name not in PARAMS:
        raise ValueError(f"Unknown oracle {name!r}; use one of {sorted(PARAMS)}")
    params = dict(params or {})
    spec = PARAMS[name]
    unknown = sorted(set(params) - set(spec))
    if unknown:
        raise ValueError(f"Oracle {name} takes no parameter {unknown[0]!r}")
    for key, (required, kind) in spec.items():
        value = params.get(key)
        if value is None:
            if required:
                raise ValueError(f"Oracle {name} needs {key}")
            params.pop(key, None)
        elif kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))
                                   or not math.isfinite(value) or value <= 0):
            raise ValueError(f"{name}.{key} must be a positive number")
        elif kind == "text" and (not isinstance(value, str) or not value.strip() or len(value) > 200):
            raise ValueError(f"{name}.{key} must be a short non-empty text")
    return params


def parse_params(name: str, params: dict | None) -> dict:
    """check_params() of parameters given as text (CLI K=V pairs, MCP strings): a numeric parameter may be "50" or
    "49,90"."""
    params = dict(params or {})
    for key, (_, kind) in PARAMS.get(name, {}).items():
        if kind == "number" and isinstance(params.get(key), str):
            try:
                number = float(params[key].strip().replace(",", "."))
            except ValueError:
                continue  # check_params says what is wrong
            params[key] = int(number) if number.is_integer() else number
    return check_params(name, params)


def parse_price(text) -> float | None:
    """'1.299,00 €' -> 1299.0, '€49.90' -> 49.9, '49,90' -> 49.9; None without a number."""
    match = PRICE.search(str(text or ""))
    if not match:
        return None
    raw = re.sub(r"[\s']", "", match.group(0))
    if re.search(r",\d{1,2}$", raw):
        raw = raw.replace(".", "").replace(",", ".")
    elif re.search(r"\.\d{1,2}$", raw):
        raw = raw.replace(",", "")
    else:
        raw = raw.replace(".", "").replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _web(url: str | None) -> bool:
    try:
        return urlsplit(url or "").scheme in ("http", "https")
    except ValueError:
        return False


def _same_site(url: str | None, start_url: str) -> bool:
    try:
        here, start = urlsplit(url or ""), urlsplit(start_url or "")
    except ValueError:
        return False
    return (here.scheme in ("http", "https") and bool(here.hostname)
            and registrable_domain(here.hostname) == registrable_domain(start.hostname))


def _base(run: dict) -> str:
    site = run.get("site") or {}
    return site.get("final_url") or site.get("start_url") or ""  # the site the start URL landed on


def read_page(browser, collector) -> tuple[dict, dict]:
    """(AuditPayload, Classification) of the browser's current page, read now. A page the browser did not load
    (chrome-error://) is not audited: ({"url": ...}, type "other" with the signal "navigation_error")."""
    href = browser.evaluate("location.href") or ""
    if not _web(href):
        return {"url": href}, {"type": "other", "confidence": 0.0, "signals": ["navigation_error"]}
    audit = collector.audit(browser) or {}
    url = audit.get("url") or href
    return audit, classify(audit, url, locale=audit.get("lexicon_lang") or collector.locale)


def clean_cart_url(url: str) -> tuple[str | None, list[str]]:
    """(the URL without its fragment and its cart-action query keys, the keys dropped); None when the path itself
    names a cart action: loading it could change the cart."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None, []
    if {s.lower() for s in (parts.path or "").split("/") if s} & CART_ACTION_SEGMENTS:
        return None, []
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    dropped = sorted({k for k, _ in pairs if CART_ACTION_KEYS.match(k)})
    query = urlencode([(k, v) for k, v in pairs if k not in dropped]) if dropped else parts.query
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, "")), dropped


def find_cart(run: dict, steps: list[dict], current: tuple[str, dict, dict] | None) -> tuple[str | None, str | None]:
    """(cart URL, how it was found) or (None, None). current: (url, audit, classification) of the final page.

    "final_page" is read in place. Any other URL is one clean_cart_url() can load: visited cart pages and cart URLs
    (newest first) whose query is empty without its cart-action keys, then the final page's cart link, then the
    visited ones that keep a query."""
    start = _base(run)
    locale = (run.get("settings") or {}).get("locale") or "it"
    cart_url = compile_lexicon(lexicon_for(locale)).get("cart_url")
    if current and (current[2] or {}).get("type") == "cart" and _same_site(current[0], start):
        return current[0], "final_page"
    visited = [(s["url_before"], "visited_cart_page") for s in reversed(steps)
               if s.get("page_type") == "cart" and s.get("url_before")]
    for url in [u for s in reversed(steps) for u in (s.get("url_after"), s.get("url_before")) if u]:
        parts = urlsplit(clean_cart_url(url)[0] or "")  # a cart action in the query does not make a cart page
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        if cart_url is not None and parts.netloc and cart_url.search(path):
            visited.append((url, "visited_cart_url"))
    visited = [(u, how) for u, how in visited if _same_site(u, start) and clean_cart_url(u)[0]]
    link = []
    if current:
        found = ((current[1] or {}).get("nav") or {}).get("cart_link") or {}
        href = urljoin(current[0] or start, found.get("href") or "") if found.get("present") else None
        if href and _same_site(href, start) and clean_cart_url(href)[0]:
            link = [(href, "cart_link")]
    plain = [(u, how) for u, how in visited if not urlsplit(clean_cart_url(u)[0]).query]
    queried = [(u, how) for u, how in visited if (u, how) not in plain]
    return next(iter([*plain, *link, *queried]), (None, None))


def _load(collector, url: str, page_id: str) -> dict:
    """The page at url, read in a new tab of the journey's context (closed afterwards)."""
    tab = collector.open(url)
    try:
        return collector.collect(tab, stage="extra", page_id=page_id, requested_url=url)
    finally:
        collector.close(tab)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.05, 0.01 * max(abs(a), abs(b)))


def _amount(value, text) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return parse_price(text)


def _row_amounts(text: str) -> list[float]:
    """Currency-marked amounts in a cart row (a symbol or code next to it, or two decimals): never a bare quantity."""
    text, found = str(text or ""), []
    for match in PRICE.finditer(text):
        marked = CURRENCY_BEFORE.search(text[:match.start()]) or CURRENCY_AFTER.search(text[match.end():])
        if marked or re.search(r"[.,]\d{2}$", match.group(0)):
            value = parse_price(match.group(0))
            if value is not None and value > 0 and value not in found:
                found.append(value)
    return found


def _cart_reading(rows: list[tuple[dict, float | None, int]], subtotal) -> str | None:
    """"line_total" or "unit" when the subtotal settles how every row price reads; None when it does not."""
    if not isinstance(subtotal, (int, float)) or isinstance(subtotal, bool) or not rows:
        return None
    if any(price is None for _, price, _ in rows) or all(qty == 1 for _, _, qty in rows):
        return None
    totals = _close(sum(price for _, price, _ in rows), subtotal)
    units = _close(sum(price * qty for _, price, qty in rows), subtotal)
    return "line_total" if totals and not units else "unit" if units and not totals else None


def _unit(item: dict, price: float, qty: int, reading: str | None) -> tuple[float, str, list[float] | None]:
    """(unit price, how the row price reads, both candidates when ambiguous) for a row with qty > 1."""
    if reading == "line_total":
        return round(price / qty, 2), "line_total", None
    if reading == "unit":
        return price, "unit", None
    amounts = _row_amounts(item.get("row_text"))
    low, high = (min(amounts), max(amounts)) if len(amounts) >= 2 else (None, None)
    if low is not None and _close(high, low * qty) and (_close(price, low) or _close(price, high)):
        return low, "unit" if _close(price, low) else "line_total", None
    return price, "ambiguous", [round(price / qty, 2), price]


def _amounts_shown(cart: dict) -> bool:
    """The cart page shows something to pay: a positive subtotal, or without one a positive total."""
    def number(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    subtotal, total = cart.get("subtotal_value"), cart.get("total_value")
    return subtotal > 0 if number(subtotal) else number(total) and total > 0


def _cart(ctx: dict) -> tuple[dict, list[dict], dict | None]:
    """(checks, product line items, PageRecord) of the cart page found for this journey. checks["unreadable"] (popped
    by the oracles) names what could not be read: "final_page_unreadable" (no cart found, and the final page, which
    may be the cart or link to it, unread), "cart_unreadable" (the cart page's audit failed) or "cart_items_unreadable"
    (a non-empty cart whose product rows could not be read)."""
    browser, collector = ctx["browser"], ctx["collector"]
    try:
        audit, classification = read_page(browser, collector)
        current = (browser.evaluate("location.href"), audit, classification) if audit else None
    except (RuntimeError, ValueError, OSError):
        current = None  # the final page is unreadable: the visited pages still lead to the cart
    url, source = find_cart(ctx["run"], ctx["steps"], current)
    if url is None:
        return {"cart_found": False, **({"unreadable": "final_page_unreadable"} if current is None else {})}, [], None
    checks = {"cart_found": True, "cart_source": source}
    page_id = f"{collector.profile}-verify-cart"
    if source == "final_page":  # read where it is: the request that led here is never sent again
        record = collector.collect(browser, stage="extra", page_id=page_id)
    else:
        url, dropped = clean_cart_url(url)
        if dropped:
            checks["dropped_query_keys"] = dropped
        record = _load(collector, url, page_id)
    if not record.get("audit"):  # audit.js failed on the cart page (its notes say why): a harness failure
        checks.update(cart_url=url[:200], page_type=(record.get("classification") or {}).get("type"),
                      unreadable="cart_unreadable")
        return checks, [], record
    cart = (record.get("audit") or {}).get("cart") or {}
    rows = []  # every line, add-ons included: they count in the subtotal
    for item in cart.get("line_items") or []:
        qty = item.get("qty") if isinstance(item.get("qty"), int) and item["qty"] > 0 else 1
        rows.append((item, _amount(item.get("price_value"), item.get("price")), qty))
    reading = _cart_reading(rows, cart.get("subtotal_value"))
    items, readings, untitled = [], set(), 0
    for item, price, qty in rows:
        title = " ".join(str(item.get("title") or "").split())
        if item.get("addon"):
            continue  # an add-on line (insurance, protection, gift wrap) is not a product
        if len(title) < 2:
            untitled += 1  # a row without a readable name: a product audit.js could not read
            continue
        entry = {"title": title[:60], "qty": qty, "price": price}
        if price is not None and qty > 1:
            entry["price"], how, candidates = _unit(item, price, qty, reading)
            readings.add(how)
            if candidates:
                entry["price_candidates"] = candidates
        items.append(entry)
    checks.update(cart_url=url[:200], page_type=(record.get("classification") or {}).get("type"), items=len(items),
                  price_reading=next((r for r in ("ambiguous", "line_total") if r in readings), "unit"),
                  subtotal=cart.get("subtotal_value"), total=cart.get("total_value"),
                  cart_empty=bool(cart.get("empty")))
    if not items and (untitled or (not rows and not cart.get("empty") and _amounts_shown(cart))):
        checks["unreadable"] = "cart_items_unreadable"
    return checks, items, record


def cart_contains_item_under_price(params: dict, ctx: dict) -> dict:
    checks, items, record = _cart(ctx)
    unreadable = checks.pop("unreadable", None)
    limit = float(params["max_price"])
    matching = [i for i in items if i["price"] is not None and i["price"] <= limit]
    checks.update(max_price=limit, matching=len(matching), item_list=items[:MAX_ITEMS])
    passed = bool(matching)
    if not passed and unreadable in ("cart_unreadable", "final_page_unreadable"):
        passed, checks["not_assessable"] = None, unreadable  # a harness read failure, never the shop's
    elif not passed and any(min(i.get("price_candidates") or [math.inf]) <= limit for i in items):
        passed, checks["not_assessable"] = None, "cart_price_ambiguous"  # only the unit reading would pass: no guess
    elif not passed and any(i["price"] is None for i in items):
        passed, checks["not_assessable"] = None, "cart_price_unreadable"  # a product whose price was not read
    elif not passed and unreadable:
        passed, checks["not_assessable"] = None, unreadable
    return {"passed": passed, "checks": checks, "page": record}


def cart_not_empty(params: dict, ctx: dict) -> dict:
    checks, items, record = _cart(ctx)
    unreadable = checks.pop("unreadable", None)
    checks.update(item_list=items[:MAX_ITEMS])
    if not items and unreadable:  # nothing read, or an amount to pay without a readable product row: no guess
        return {"passed": None, "checks": {**checks, "not_assessable": unreadable}, "page": record}
    return {"passed": bool(items), "checks": checks, "page": record}


def _words(text: str) -> set[str]:
    return {w.lower() for w in WORD.findall(text or "")}


def _query_words(query: str) -> set[str]:
    """The query's own words: its function words only when it has nothing else."""
    words = _words(query)
    return (words - STOP_WORDS) or words


def _final_page(ctx: dict) -> tuple[dict, dict, dict]:
    """(audit, classification, checks) of the journey's final page; checks hold its URL and whether it is the shop's,
    or not_assessable "final_page_unreadable" when audit.js could not read it (a harness failure: no guess)."""
    try:
        audit, classification = read_page(ctx["browser"], ctx["collector"])
        error = None if audit else "audit.js returned nothing"
    except (RuntimeError, ValueError, OSError) as exc:
        audit, classification, error = {}, {}, f"{type(exc).__name__}: {str(exc)[:200]}"
    if error:
        return {}, {}, {"not_assessable": "final_page_unreadable", "read_error": error}
    url = audit.get("url") or ctx["browser"].evaluate("location.href") or ""
    return audit, classification, {"url": url[:200], "page_type": classification.get("type"),
                                   "same_site": _same_site(url, _base(ctx["run"]))}


def pdp_reached(params: dict, ctx: dict) -> dict:
    audit, classification, checks = _final_page(ctx)
    if checks.get("not_assessable"):
        return {"passed": None, "checks": checks}
    pdp = audit.get("pdp") or {}
    title = pdp.get("title") or " ".join((audit.get("doc") or {}).get("h1s") or []) or audit.get("title") or ""
    checks["title"] = title[:80]
    passed = classification.get("type") == "pdp" and checks["same_site"]
    if params.get("query"):
        hit = _query_words(params["query"]) & _words(f"{title} {audit.get('title') or ''}")
        checks["query_words_found"] = sorted(hit)
        passed = passed and bool(hit)
    if params.get("max_price") is not None:
        price = (pdp.get("price") or {}).get("value")
        checks.update(price=price, max_price=params["max_price"])
        passed = passed and isinstance(price, (int, float)) and price <= params["max_price"]
    return {"passed": bool(passed), "checks": checks}


def search_results_shown(params: dict, ctx: dict) -> dict:
    """A listing of the shop with product cards and no "no results" statement (a no-results page often shows a grid
    of recommendations). The query is looked for in what the page shows, its headings and card titles, never in the
    URL, which echoes whatever was typed."""
    audit, classification, checks = _final_page(ctx)
    if checks.get("not_assessable"):
        return {"passed": None, "checks": checks}
    products = audit.get("products") or {}
    cards = products.get("cards") or []
    checks["results"] = products.get("cards_count") or 0
    passed = classification.get("type") == "plp" and checks["results"] >= 1 and checks["same_site"]
    if no_results := (audit.get("filters") or {}).get("no_results_text"):
        checks["no_results_text"] = str(no_results)[:120]
        passed = False
    if params.get("query"):
        haystack = " ".join([*((audit.get("doc") or {}).get("h1s") or []),
                             *(str(c.get("title") or "") for c in cards[:20])])
        hit = _query_words(params["query"]) & _words(haystack)
        checks["query_words_found"] = sorted(hit)
        passed = passed and bool(hit)
    return {"passed": bool(passed), "checks": checks}


ORACLES: dict[str, Callable[[dict, dict], dict]] = {
    "cart_contains_item_under_price": cart_contains_item_under_price,
    "cart_not_empty": cart_not_empty,
    "pdp_reached": pdp_reached,
    "search_results_shown": search_results_shown,
}


def verify(name: str, params: dict | None, *, browser, collector, run: dict, steps: list[dict] | None = None) -> dict:
    """Run one oracle on the actual page state; independent of the agent's DONE. Raises on an unknown oracle or
    malformed parameters; browser errors propagate to the caller. passed is True, False, or None together with
    checks["not_assessable"]."""
    params = check_params(name, params)
    ctx = {"browser": browser, "collector": collector, "run": run, "steps": list(steps or [])}
    result = ORACLES[name](params, ctx)
    result["checks"] = {"oracle": name, **(result.get("checks") or {})}
    passed = result.get("passed")
    result["passed"] = None if passed is None and result["checks"].get("not_assessable") else bool(passed)
    return result
