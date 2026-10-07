"""Deception tests in the style of Mathur et al. 2019, for one profile, in fresh isolated contexts of that profile.

Raw results with their evidence; checks.py turns them into DPR confidences. Nothing is ever typed or bought: the only
actions are consent-banner controls (one "manage" click for the consent test, then the run's consent policy, which
never presses "manage" again on that document) and the close control of an interrupting overlay, all observed elements
that pass CheckoutGuard, executed once and logged to steps.jsonl with source "deception". Pages loaded here are not
funnel pages: their snapshots are stored as "<profile>-deception-<n>" and they never enter run["pages"]. A visit that
meets a bot challenge, Chrome's error page or no audit is no evidence: it is left out and named in result["errors"],
and a test it leaves without its base is not assessed (never a clean 0).

Consent state: product pages are compared with the funnel's in the consent state the funnel's tab ended with (its
accept or reject, funnel_choice). A context that has not made that choice before its product visits (context 2 always
starts without one; context 1 when the run's policy found no control for it on the layer "manage" opened) gets the
run's policy on a fresh landing on settings.url first: a new document, so nothing is sent twice. result
["consent_contexts"] records each context's choice and its landing.

Scope: persuasion items audit.js marks as inside a product card (in_card: a related-products or recently-viewed card)
or inside an overlay (overlay) are not the product's own and are left out. Without those flags, on a page that lists
product cards the scope of an item is unknown ("card_scope_unknown"): see low_stock.

- countdown: a time text (h:mm:ss) is a timer only when it ticks: read twice TICK_S apart within one visit, its
  remaining time drops by about the elapsed time (a race time or a video duration does not: "static"). The funnel's
  product page is loaded in two fresh contexts (visit records carry context 1 or 2) at least MIN_GAP_S apart: a
  countdown to a fixed deadline shows a remaining time smaller by the elapsed time (tolerance max(2 s, 20%)); one
  that is not restarted. The two visits' timers are paired by their text with the digits masked (by position only
  when each visit shows one timer and their words agree); every pair is compared and the worst is reported, and no
  pair leaves the test not assessed ("countdown_not_paired"): two different deadlines are never compared. This is
  Mathur's criterion (a time text that mutates). Known limits: a server-rendered countdown behind cached HTML looks
  the same as a restart (as in Mathur); timers that update less often than every ~1.5 s, timers that start only when
  scrolled into view, and timers drawn on a canvas or an image read as absent or static ("static" in the result),
  never as a risk; minute-resolution timers ("mm" only) are not parsed at all.
- low_stock: the "only N left" number on the two fresh visits (a change without a purchase), on other products of
  the listing (the same number on different products; variants of the funnel product, by sku or title, are not
  other products; N = 1 never counts, since unique items truthfully say "only 1 left" everywhere), and against the
  product's own structured data: a single Offer that says OutOfStock, SoldOut or Discontinued contradicts "only N
  left"; an Offer inventoryLevel (structured.inventory_level, when audit.js provides it) that differs from N
  contradicts it, one that equals N corroborates it (that product leaves the shared-number count). A visit whose
  number may be a product card's (card_scope_unknown) counts in neither the shared number nor the contradictions,
  and a change between two such visits counts only when both list the same cards in the same order (a carousel
  that changes between contexts is no change of stock). A statement the funnel's home or listing page also shows
  outside product cards (an announcement bar, a header: site_copy) is site copy, no product's stock claim: it is left
  out of every product visit (low_stock["site_copy"] lists it).
- sneak_into_basket: cart lines that cost something and are not the item the crawler added (probes["add_to_cart"]).
- prechecked_paid: paid options audit.js found pre-checked on the cart and checkout-entry pages, each with its add-on
  flag (insurance, protection, donation: the lexicon's "addon"); an empty cart shows none and is not assessed.
- consent: the first layer of the banner on a fresh landing (accept, reject, manage and close controls with their area
  and paint, and the kinds of all its buttons) and, when it offers no reject, whether one appears after clicking
  "manage" (clicks to reject: 1, 2 or None). second_layer_checked is False when the "manage" click did not run or the
  layer it opened could not be read (checks.py then only scores the certain part: no first-layer reject). On an
  Italian page an X/"Chiudi" close is the first-layer reject (reject_kind "close": the Garante's 2021 guidelines read
  closing the banner as a refusal; EDPB and ICO do not, so other languages keep it out).
- nagging: per overlay (crawler.overlay_key: kind and text), the funnel pages that showed a modal or blocking
  non-consent overlay again after the crawler had closed it on an earlier page of the same tab (a request repeated
  after the shopper said no; a tab that replaced a lost one, probes["tab"], starts with nothing closed, since a closed
  state kept in sessionStorage does not survive it), and whether such an overlay comes back after it was closed and
  the page was reloaded in the same context. Assessed only with a base: two funnel pages, or the reload test run.
  Known limits: the overlay's identity is its kind and the first 60 characters of its text with digits masked, so
  personalised copy or an A/B variant that changes the text between pages reads as another overlay (a missed
  signal, never a false one); a close whose effect could not be read (gone None) is no close.
"""

import re
import time

from ..browser import StalePage
from .collectors import AuditError, PageCollector
from .crawler import (
    GONE,
    Tab,
    cart_empty,
    consent_banner,
    failed_navigation,
    interrupting,
    overlay_key,
    own_controls,
    product_candidates,
)
from .lexicon import lexicon_for
from .profiles import close_context, new_context
from .safety import CheckoutGuard
from .schemas import STAGES, PageRecord

DECEPTION_VERSION = "deception.v1"
MIN_GAP_S = 10.0  # between the two fresh visits of the countdown and low-stock tests
TICK_S = 1.5  # between the two readings of a countdown within one visit
OTHER_PRODUCTS = 2
FAILURES = (TimeoutError, RuntimeError, StalePage, AuditError, OSError)


def _audit(record: PageRecord | None) -> dict:
    return (record or {}).get("audit") or {}


def _norm(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def _mask(text) -> str:
    return re.sub(r"\d+", "#", _norm(text))


def _tokens(text) -> set[str]:
    return set(re.findall(r"[^\W_]{2,}", str(text or "").casefold()))


def same_item(a, b) -> bool:
    """Two labels name the same item: equal, the words of one (at least two) all in the other ("Scarpa Aurora" and
    "Scarpa Aurora - taglia 42"), or most of their words shared. One shared word alone ("Spedizione" and "Protezione
    spedizione Premium") is not enough."""
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return False
    if x == y:
        return True
    tx, ty = _tokens(x), _tokens(y)
    small, big = sorted((tx, ty), key=len)
    if len(small) >= 2 and small <= big:
        return True
    return bool(tx and ty) and len(tx & ty) / len(tx | ty) >= 0.6


def same_product(a: dict, b: dict) -> bool:
    """Two product visits show one product: the same URL, the same sku, or the same title (a variant title extends
    the other: "Scarpa Aurora" and "Scarpa Aurora - Rosso"). Similar names of a catalogue are different products."""
    if a.get("url") and a.get("url") == b.get("url"):
        return True
    if a.get("sku") and b.get("sku"):
        return _norm(a["sku"]) == _norm(b["sku"])
    x, y = _norm(a.get("title")), _norm(b.get("title"))
    return bool(x and y) and (x == y or x.startswith(y + " ") or y.startswith(x + " "))


def visit_failure(record: PageRecord) -> str | None:
    """Why a loaded page shows nothing of the shop's page: a bot challenge, Chrome's error page, no audit."""
    if ((record.get("classification") or {}).get("type")) == "challenge":
        return "bot_challenge"
    if failed_navigation(record):
        return "navigation_error"
    audit = _audit(record)
    if not audit.get("doc") and not audit.get("ctas"):
        return "audit_unavailable"
    return None


def _missing(stage: str, reason: str) -> dict:
    """A test whose funnel page was not reached: checks.py reports the stage's own not-assessable reason."""
    return {"assessed": False, "reason": reason, "stage": stage, "stage_missing": True}


def _funnel(pages: list[PageRecord], profile: str) -> dict[str, PageRecord]:
    """The profile's first page of each funnel stage (repeats and "extra" pages left out)."""
    funnel = {}
    for page in _pages(pages, profile):
        funnel.setdefault(page.get("stage"), page)
    return funnel


def _pages(pages: list[PageRecord], profile: str) -> list[PageRecord]:
    return [p for p in pages if p.get("profile") == profile and p.get("stage") in STAGES and not p.get("repeat_of")]


def funnel_choice(funnel: dict[str, PageRecord]) -> str | None:
    """The consent choice the funnel's tab made (accept or reject, on the first page that recorded one); None."""
    for stage in STAGES:
        choice = ((funnel.get(stage) or {}).get("consent") or {}).get("choice")
        if choice in ("accept", "reject"):
            return choice
    return None


def _layer(audit: dict) -> dict | None:
    """The consent banner's controls: the largest accept, reject, manage and close buttons with their area and paint."""
    banner = consent_banner(audit)
    if banner is None:
        return None

    def biggest(kind):
        buttons = [b for b in banner.get("buttons") or [] if b.get("kind") == kind]
        if not buttons:
            return None
        b = max(buttons, key=lambda item: item.get("area") or 0)
        keys = ("label", "text", "area", "contrast", "bg_contrast", "filled", "font_px", "rect")
        return {k: b.get(k) for k in keys if k != "rect" or b.get(k)}

    kinds = sorted({str(b.get("kind")) for b in banner.get("buttons") or []})
    return {"accept": biggest("accept"), "reject": biggest("reject"), "manage": biggest("manage"),
            "close": biggest("close"), "kinds": kinds, "blocking": bool(banner.get("blocking")),
            "modal": bool(banner.get("modal")), "coverage": banner.get("coverage"),
            "text": str(banner.get("text_sample") or "")[:200]}


def _own(items: list[dict], audit: dict) -> tuple[list[dict], bool]:
    """(items of the product itself, scope unknown). audit.js flags items inside a product card (in_card) or an
    overlay (overlay): those are left out. Without the in_card flag an item of a page that lists product cards may be
    a card's: the scope is unknown (a page without cards has none to be unsure about)."""
    if all("in_card" in item for item in items):
        return [i for i in items if not i.get("in_card") and not i.get("overlay")], False
    products = audit.get("products") or {}
    return [i for i in items if not i.get("overlay")], bool(products.get("cards") or products.get("cards_count"))


def _countdowns(audit: dict) -> list[dict]:
    timers = [u for u in (audit.get("persuasion") or {}).get("urgency") or []
              if u.get("countdown") and isinstance(u.get("remaining_s"), (int, float))]
    return [{"text": str(u.get("text") or "")[:160], "remaining_s": u["remaining_s"]} for u in _own(timers, audit)[0]]


def _product(record: PageRecord, at: float, context: int | None = None) -> dict:
    audit = _audit(record)
    persuasion, pdp = audit.get("persuasion") or {}, audit.get("pdp") or {}
    structured = pdp.get("structured") or {}
    scarce = [s for s in persuasion.get("scarcity") or [] if isinstance(s.get("number"), (int, float))
              and not isinstance(s.get("number"), bool)]
    stock, unknown = _own(scarce, audit)
    found = {
        "page_id": record.get("page_id"), "url": record.get("final_url") or record.get("url"), "t": round(at, 3),
        "context": context, "title": pdp.get("title") or structured.get("name"), "sku": structured.get("sku"),
        "availability": structured.get("availability"), "offers": structured.get("offers"),
        "inventory_level": structured.get("inventory_level"), "countdowns": _countdowns(audit),
        "stock": [{"text": str(s.get("text") or "")[:160], "number": s["number"]} for s in stock],
    }
    if unknown and stock:  # the number may be a related product card's
        found.update(card_scope_unknown=True, cards=[c.get("href") for c in (audit.get("products") or {}).get(
            "cards") or [] if isinstance(c, dict)])
    return found


def site_copy(funnel: dict[str, PageRecord]) -> set[str]:
    """The scarcity statements the funnel's home and listing pages show outside product cards and overlays: site copy
    (an announcement bar, a header line shown on every page), no product's own stock claim."""
    return {_norm(str(s.get("text") or "")[:160]) for stage in ("home", "plp")
            for s in (_audit(funnel.get(stage)).get("persuasion") or {}).get("scarcity") or []
            if s.get("text") and not s.get("in_card") and not s.get("overlay")}


def without_site_copy(found: dict | None, copy: set[str]) -> dict | None:
    """A product visit without its stock statements that are site copy (site_copy); their texts go to "site_copy"."""
    shared = [s for s in (found or {}).get("stock") or [] if _norm(s["text"]) in copy]
    if not shared:
        return found
    return {**found, "stock": [s for s in found["stock"] if s not in shared], "site_copy": [s["text"] for s in shared]}


def ticking(first: list[dict], second: list[dict], elapsed: float) -> tuple[list[dict], list[dict]]:
    """(ticking, static) countdowns of a first reading: ticking when the second reading of the same visit, elapsed
    seconds later, shows a remaining time smaller by at least 1 s and at most elapsed + 2 s. Readings are paired by
    their text with the digits masked, else by position."""
    later: dict[str, list[dict]] = {}
    for item in second:
        later.setdefault(_mask(item["text"]), []).append(item)
    tick, static = [], []
    for index, item in enumerate(first):
        twins = later.get(_mask(item["text"])) or []
        twin = twins.pop(0) if twins else (second[index] if index < len(second) else None)
        drop = item["remaining_s"] - twin["remaining_s"] if twin else None
        (tick if drop is not None and 1 <= drop <= elapsed + 2 else static).append(item)
    return tick, static


def _closable(audit: dict) -> list[str]:
    """Keys of the overlays the crawler closes (modal or blocking, not consent): the only ones a repeat is about; a
    non-modal bar is an interruption (FAI.OVERLAY_INTERRUPTIONS) the shopper was never asked to close."""
    return list(dict.fromkeys(overlay_key(o) for o in audit.get("overlays") or [] if interrupting(o)))


# ---------------------------------------------------------------- tests on funnel pages (no browser)
def sneak_into_basket(product: PageRecord | None, cart: PageRecord | None) -> dict:
    if cart is None:
        return _missing("cart", "no_cart_page")
    added = ((product or {}).get("probes") or {}).get("add_to_cart") or {}
    if not added.get("executed"):
        return {"assessed": False, "reason": "nothing_added", "page_id": cart.get("page_id"), "stage": "cart"}
    lines = [{"title": str(i.get("title") or "")[:120], "qty": i.get("qty"), "price_value": i.get("price_value"),
              "addon": bool(i.get("addon"))} for i in (_audit(cart).get("cart") or {}).get("line_items") or []]
    if not lines:  # an empty cart, or one whose lines audit.js could not read although it shows a total
        reason = "empty_cart" if cart_empty(_audit(cart)) else "cart_lines_not_recognised"
        return {"assessed": False, "reason": reason, "page_id": cart.get("page_id"), "stage": "cart"}
    ours = [line for line in lines if same_item(line["title"], added.get("title"))]
    if not ours:
        return {"assessed": False, "reason": "added_item_not_recognised", "page_id": cart.get("page_id"),
                "stage": "cart", "added": {"title": added.get("title"), "price": added.get("price")},
                "lines": lines[:10]}
    unrequested = [line for line in lines if line not in ours and (line["price_value"] or 0) > 0]
    return {"assessed": True, "page_id": cart.get("page_id"), "stage": "cart",
            "added": {"title": added.get("title"), "price": added.get("price")}, "lines": lines[:10],
            "unrequested": unrequested}


def prechecked_paid(cart: PageRecord | None, checkout: PageRecord | None) -> dict:
    """Paid options pre-checked on the cart and checkout-entry pages; addon tells an add-on (insurance, protection,
    donation) from other priced boxes (a newsletter voucher)."""
    if cart is None and checkout is None:
        return _missing("cart", "no_cart_page")
    if checkout is None and cart_empty(_audit(cart)):
        return {"assessed": False, "reason": "empty_cart", "page_id": cart.get("page_id"), "stage": "cart"}
    items = []
    for record in (cart, checkout):
        for item in (_audit(record).get("cart") or {}).get("prechecked_paid") or []:
            items.append({"label": str(item.get("label") or "")[:160], "price_text": item.get("price_text"),
                          "price_value": item.get("price_value"), "addon": item.get("addon"),
                          "page_id": record.get("page_id"), "stage": record.get("stage")})
    first = cart or checkout
    return {"assessed": True, "page_id": first.get("page_id"), "stage": first.get("stage"), "items": items}


def _closes(page: PageRecord) -> list[str]:
    """Keys of the non-consent overlays the crawler closed on the page: an executed close or decline click after which
    the overlay was gone (records without the check count by their executed click)."""
    probes = page.get("probes") or {}
    entries = list(probes.get("dismiss_overlays") or [])
    for key in ("add_to_cart", "checkout_entry"):
        entries += (probes.get(key) or {}).get("overlays") or []
    return [e.get("key") or str(e.get("kind")) for e in entries if isinstance(e, dict) and e.get("executed")
            and e.get("kind") != "consent" and e.get("gone", True)]


def overlay_pages(pages: list[PageRecord], profile: str) -> dict:
    """Funnel pages of the profile that showed an overlay the crawler closes ("pages"), and per overlay (overlay_key:
    kind and text) the pages that showed it again after the crawler had closed it on an earlier page of the same tab,
    that page first ("repeated"). An overlay that stays closed once closed, or that was never closed, repeats nothing;
    neither does a different overlay of the same kind, nor one closed in a tab the crawler lost (probes["tab"])."""
    shown, closed, repeated, tab = [], {}, {}, 1
    for page in _pages(pages, profile):
        if ((page.get("probes") or {}).get("tab") or 1) != tab:  # a new tab: nothing closed there yet
            tab, closed = (page.get("probes") or {}).get("tab") or 1, {}
        keys = _closable(_audit(page))
        if keys:
            shown.append({"page_id": page.get("page_id"), "stage": page.get("stage"), "overlays": keys})
        for key in keys:
            if key in closed:
                repeated.setdefault(key, [closed[key]]).append(page.get("page_id"))
        for key in _closes(page):
            closed.setdefault(key, page.get("page_id"))
    return {"pages": shown, "repeated": repeated, "closed_on": closed}


def pair_countdowns(first: list[dict], second: list[dict]) -> list[tuple[dict, dict]]:
    """The timers of two visits that are one timer: the same text with the digits masked, in order; by position only
    when each visit shows a single timer and their words agree (same_item). Unrelated deadlines are never paired."""
    later: dict[str, list[dict]] = {}
    for item in second:
        later.setdefault(_mask(item["text"]), []).append(item)
    pairs = []
    for item in first:
        twins = later.get(_mask(item["text"]))
        if twins:
            pairs.append((item, twins.pop(0)))
    if not pairs and len(first) == len(second) == 1 and same_item(_mask(first[0]["text"]), _mask(second[0]["text"])):
        pairs.append((first[0], second[0]))
    return pairs


def countdown_result(first: dict | None, second: dict | None, *, page_id: str | None, funnel: dict | None = None,
                     reason: str | None = None) -> dict:
    """Reset when the second remaining time of a timer is not smaller by about the elapsed time (tolerance: 2 s or
    20%). Only ticking countdowns count, paired across the visits by pair_countdowns(); the worst pair is reported,
    every pair is in "pairs", and no pair leaves the test not assessed ("countdown_not_paired"). funnel is the funnel
    product page (its countdowns, unverified): one the revisits did not show at all leaves the test not assessed. Time
    texts that did not tick are listed as "static" (no countdown: no urgency credit and no risk)."""
    if first is None or second is None:
        return {"assessed": False, "reason": reason or "product_page_not_visited_twice", "page_id": page_id,
                "stage": "pdp"}
    a, b = first["countdowns"], second["countdowns"]
    static = list(dict.fromkeys(c["text"] for v in (first, second) for c in v.get("static_countdowns") or []))
    if not a and not b:
        unverified = [c for v in (first, second) for c in v.get("unverified_countdowns") or []]
        if not static and unverified:
            return {"assessed": False, "reason": "ticking_not_verified", "page_id": page_id, "stage": "pdp"}
        if not static and (funnel or {}).get("countdowns"):
            return {"assessed": False, "reason": "countdown_not_seen_on_revisit", "page_id": page_id, "stage": "pdp",
                    "funnel": funnel["countdowns"][:1]}
        out = {"assessed": True, "found": False, "page_id": page_id, "stage": "pdp"}
        return {**out, "static": static[:5]} if static else out
    if not a or not b:
        return {"assessed": False, "reason": "countdown_seen_once", "page_id": page_id, "stage": "pdp",
                "first": a[:1], "second": b[:1]}
    pairs = pair_countdowns(a, b)
    if not pairs:
        return {"assessed": False, "reason": "countdown_not_paired", "page_id": page_id, "stage": "pdp",
                "first": a[:3], "second": b[:3]}
    elapsed = second["t"] - first["t"]
    tolerance = max(2.0, 0.2 * elapsed)
    compared = [{"first": x, "second": y, "decrease_s": round(x["remaining_s"] - y["remaining_s"], 1),
                 "reset": x["remaining_s"] - y["remaining_s"] < elapsed - tolerance} for x, y in pairs]
    worst = min(compared, key=lambda pair: pair["decrease_s"])
    out = {"assessed": True, "found": True, "page_id": page_id, "stage": "pdp", "first": worst["first"],
           "second": worst["second"], "elapsed_s": round(elapsed, 1), "decrease_s": worst["decrease_s"],
           "tolerance_s": round(tolerance, 1), "reset": worst["reset"]}
    if len(compared) > 1:
        out["pairs"] = compared[:5]
    return {**out, "static": static[:5]} if static else out


def _number(value) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def stock_contradictions(product: dict) -> tuple[list[dict], bool]:
    """(contradictions, corroborated) of a product's "only N left" with its own structured data: a single Offer whose
    availability is OutOfStock, SoldOut or Discontinued ("availability"); an Offer inventoryLevel that differs from N
    ("inventory_level"; equal: corroborated). InStock and LimitedAvailability are consistent with a low-stock message;
    with several offers (variants) the first offer's availability is not the page's."""
    number = product["stock"][0]["number"]
    entry = {"url": product.get("url"), "title": product.get("title"), "number": number}
    found, level = [], _number(product.get("inventory_level"))
    if level is not None and level != number:
        found.append({**entry, "signal": "inventory_level", "inventory_level": level})
    if product.get("offers") == 1 and product.get("availability") in GONE and number >= 1:
        found.append({**entry, "signal": "availability", "availability": product["availability"]})
    return found, level is not None and level == number


def low_stock_result(first: dict | None, second: dict | None, others: list[dict], *, page_id: str | None,
                     funnel: dict | None = None, reason: str | None = None) -> dict:
    """The number on the two visits and on other products; funnel is the funnel product page: a number it showed and
    no revisit did leaves the test not assessed. The shared-number count leaves out N = 1 (unique items say "only 1
    left" truthfully) and products whose structured inventoryLevel corroborates their number. A visit whose number may
    be a product card's (card_scope_unknown) is left out of the shared number and the contradictions
    ("card_scope_unknown" lists it), and a change between the visits counts only when both list the same cards in the
    same order ("change_scope_unknown" otherwise). reason (a revisit that failed) is kept in the result when the other
    visit was enough."""
    if first is None and second is None:
        return {"assessed": False, "reason": reason or "product_page_not_visited", "page_id": page_id, "stage": "pdp"}
    visits = [{"page_id": v["page_id"], "context": v.get("context"),
               "number": v["stock"][0]["number"] if v["stock"] else None,
               "text": v["stock"][0]["text"] if v["stock"] else None,
               **({"card_scope_unknown": True} if v.get("card_scope_unknown") else {})} for v in (first, second) if v]
    numbers = [v["number"] for v in visits]
    if (funnel or {}).get("stock") and all(n is None for n in numbers):
        return {"assessed": False, "reason": "stock_not_seen_on_revisit", "page_id": page_id, "stage": "pdp",
                "visits": visits}
    changed = len(visits) == 2 and None not in numbers and numbers[0] != numbers[1]
    # a carousel of related cards that differs between the contexts is no change of this product's stock
    unsure = changed and (first.get("card_scope_unknown") or second.get("card_scope_unknown")) and (
        first.get("cards") != second.get("cards"))
    changed = changed and not unsure
    base = second or first
    distinct = [v for v in others if not same_product(v, base)]
    products, unknown = [], []  # the funnel product (latest visit) and the other products, one entry each
    for v in [base, *distinct]:
        if v["stock"] and not any(same_product(v, p) for p in products + unknown):
            (unknown if v.get("card_scope_unknown") else products).append(v)
    contradictions, corroborated, single = [], [], []
    for p in products:
        found, confirmed = stock_contradictions(p)
        contradictions += found
        if confirmed:
            corroborated.append(p)
        if p["stock"][0]["number"] == 1:
            single.append(p)
    counts = [p["stock"][0]["number"] for p in products if p not in single and p not in corroborated]
    common = max(set(counts), key=counts.count) if counts else None
    listed = [{"url": p["url"], "title": p.get("title"), "number": p["stock"][0]["number"]} for p in products]
    out = {
        "assessed": True, "page_id": page_id, "stage": "pdp", "found": any(n is not None for n in numbers) or
        bool(products or unknown), "visits": visits, "changed_between_visits": changed,
        "decreased": changed and numbers[1] < numbers[0], "products_checked": 1 + len(distinct),
        "products": listed, "same_number_products": counts.count(common) if common is not None else 0,
        "number": common, "contradictions": contradictions,
    }
    if single:
        out.update(single_unit_excluded=True, single_unit_products=[listed[products.index(p)] for p in single])
    if corroborated:
        out["corroborated"] = [listed[products.index(p)] for p in corroborated]
    if unknown:
        out["card_scope_unknown"] = [{"url": p["url"], "title": p.get("title"), "number": p["stock"][0]["number"]}
                                     for p in unknown]
    if unsure:
        out["change_scope_unknown"] = True
    if reason:
        out["revisit_failure"] = reason
    return out


# ---------------------------------------------------------------- live tests
class _Live:
    def __init__(self, transport, settings, guard, *, profile, store, run_id, say, errors):
        self.transport, self.settings, self.guard, self.profile = transport, settings, guard, profile
        self.store, self.run_id, self.say, self.errors = store, run_id, say, errors
        self.contexts: list[str] = []
        self.tabs: list[Tab] = []
        self.count = 0

    def tab(self) -> Tab:
        """A tab in a new isolated context of the profile."""
        context = new_context(self.transport)
        self.contexts.append(context)
        collector = PageCollector(self.transport, self.store, self.run_id, profile=self.profile,
                                  locale=self.settings.locale, screenshots=False,
                                  settle_timeout_s=self.settings.settle_timeout_s, context_id=context)
        tab = Tab(collector, self.guard, profile=self.profile, policy=self.settings.consent, source="deception")
        self.tabs.append(tab)
        return tab

    def load(self, tab: Tab, url: str) -> PageRecord:
        self.count += 1
        self.say(f"{self.profile}: deception visit {url}")
        return tab.load(url, stage="extra", page_id=f"{self.profile}-deception-{self.count}")

    def visit(self, tab: Tab, url: str, *, context: int, label: str,
              tick: bool = False) -> tuple[dict | None, PageRecord | None, str | None]:
        """One product visit: (product, record, None), or (None, record or None, failure) when the page shows nothing
        of the shop (the failure goes to errors). tick: its countdowns are read again TICK_S later."""
        try:
            record = self.load(tab, url)
        except FAILURES as exc:
            self.errors.append(f"{label}: {type(exc).__name__}: {str(exc)[:160]}")
            tab.lost()
            return None, None, "error"
        failure = visit_failure(record)
        if failure:
            self.errors.append(f"{label}: {failure} ({record.get('page_id')})")
            return None, record, failure
        found = _product(record, time.monotonic(), context)
        if tick and found["countdowns"]:
            time.sleep(TICK_S)
            started = time.monotonic()
            audit = tab.fresh_audit()
            elapsed = (started + time.monotonic()) / 2 - found["t"]
            if audit:
                found["countdowns"], found["static_countdowns"] = ticking(found["countdowns"], _countdowns(audit),
                                                                          elapsed)
            else:
                found["countdowns"], found["unverified_countdowns"] = [], found["countdowns"]
        return found, record, None

    def close(self) -> None:
        for tab in self.tabs:
            tab.close()
        for context in self.contexts:
            try:
                close_context(self.transport, context)
            except FAILURES as exc:  # the results stay; Chromium drops the context with the transport
                self.errors.append(f"close context: {type(exc).__name__}: {str(exc)[:160]}")


def run_deception_tests(transport, settings, pages: list[PageRecord], *, profile: str, store=None, run_id=None,
                        guard=None, progress=None) -> dict:
    """Raw deception-test results for one profile (run["deception"][profile]); checks.py reads them."""
    funnel = _funnel(pages, profile)
    product = funnel.get("pdp")
    guard = guard or CheckoutGuard(settings.url, lexicon_for(settings.locale))
    say = progress or (lambda message: None)
    nagging = {"assessed": True, **overlay_pages(pages, profile), "pages_checked": len(_pages(pages, profile)),
               "reappeared_after_close": None}
    result = {
        "version": DECEPTION_VERSION, "profile": profile, "errors": [],
        "sneak_into_basket": sneak_into_basket(product, funnel.get("cart")),
        "prechecked_paid": prechecked_paid(funnel.get("cart"), funnel.get("checkout_entry")),
        "nagging": nagging, "consent_contexts": [],
        "consent": _missing("home", "home_not_reached"),
        "countdown": _missing("pdp", "pdp_not_reached"),
        "low_stock": _missing("pdp", "pdp_not_reached"),
    }
    live = _Live(transport, settings, guard, profile=profile, store=store, run_id=run_id, say=say,
                 errors=result["errors"])
    try:
        _live_tests(live, result, funnel)
    finally:
        live.close()
    nag = result["nagging"]
    if nag["pages_checked"] < 2 and nag.get("reappeared_after_close") is None:
        # no base: a repeat needs two funnel pages, or the product page closed and reloaded
        stage = next((s for s in STAGES if s not in funnel), "home")
        result["nagging"] = {**nag, **_missing(stage, "single_page" if funnel else "no_pages")}
    return result


def _live_tests(live: _Live, result: dict, funnel: dict[str, PageRecord]) -> None:
    home, listing, product = funnel.get("home"), funnel.get("plp"), funnel.get("pdp")
    product_url = product and (product.get("final_url") or product.get("url"))
    if home is None and not product_url:
        return
    try:
        tab = live.tab()
    except FAILURES as exc:  # no context: the live tests stay not assessed (their pages were reached)
        live.errors.append(f"context: {type(exc).__name__}: {str(exc)[:160]}")
        if home is not None:
            result["consent"] = {"assessed": False, "reason": "context_unavailable", "page_id": home.get("page_id"),
                                 "stage": "home"}
        if product_url:
            result["countdown"] = result["low_stock"] = {"assessed": False, "reason": "context_unavailable",
                                                         "page_id": product.get("page_id"), "stage": "pdp"}
        return
    if home is not None:
        try:
            result["consent"] = _consent(live, tab, home)
        except FAILURES as exc:
            live.errors.append(f"consent: {type(exc).__name__}: {str(exc)[:160]}")
            result["consent"] = {"assessed": False, "reason": "error", "page_id": home.get("page_id"),
                                 "stage": "home"}
            tab.lost()
    if not product_url:
        return
    choice, contexts = funnel_choice(funnel), result["consent_contexts"]
    made = ((result["consent"].get("applied") or {}).get("choice")) if home is not None else None
    contexts.append(_aligned(live, tab, context=1, choice=choice, made=made))
    page_id, failures, others = product.get("page_id"), [], []
    first, record, failure = live.visit(tab, product_url, context=1, label="product visit 1", tick=True)
    failures.append(failure)
    if first is not None:
        try:
            result["nagging"].update(_reappears(live, tab, record, product_url))
        except FAILURES as exc:
            live.errors.append(f"nagging reappear test: {type(exc).__name__}: {str(exc)[:160]}")
            tab.lost()
    second = None
    try:
        tab = live.tab()
    except FAILURES as exc:
        live.errors.append(f"context 2: {type(exc).__name__}: {str(exc)[:160]}")
        failures.append("context_unavailable")
    else:
        contexts.append(_aligned(live, tab, context=2, choice=choice, made=None))
        if first is not None:
            time.sleep(max(0.0, MIN_GAP_S - (time.monotonic() - first["t"])))
        second, _, failure = live.visit(tab, product_url, context=2, label="product visit 2", tick=True)
        failures.append(failure)
        base = second or first or {"url": product_url}
        for url in [u for u in product_candidates(listing or home, live.guard) if u != product_url][:OTHER_PRODUCTS]:
            other, _, _ = live.visit(tab, url, context=2, label="other product")
            if other is not None and not same_product(other, base):
                others.append(other)
    reason = next((f"revisit_{f}" for f in failures if f), None)
    reference = _product(product, 0.0)
    result["countdown"] = countdown_result(first, second, page_id=page_id, funnel=reference, reason=reason)
    copy = site_copy(funnel)  # the same statement on the home or listing page is no stock claim of a product
    first, second, reference = (without_site_copy(v, copy) for v in (first, second, reference))
    others = [without_site_copy(v, copy) for v in others]
    result["low_stock"] = low_stock_result(first, second, others, page_id=page_id, funnel=reference, reason=reason)
    left_out = sorted({t for v in (first, second, *others) if v for t in v.get("site_copy") or []})
    if left_out:
        result["low_stock"]["site_copy"] = left_out


def _consent(live: _Live, tab: Tab, home: PageRecord) -> dict:
    """The banner of a fresh landing; without a first-layer reject, one click on "manage" and a look for a reject
    (second_layer_checked False with second_layer_reason when the click did not run, or the layer it opened could
    not be read or did not change). On an Italian page an X/"Chiudi" close on the first layer is the reject
    (reject_kind "close")."""
    record = live.load(tab, live.settings.url)
    failure = visit_failure(record)
    if failure:  # the funnel's own landing is the evidence; "manage" cannot be tried on this page
        live.errors.append(f"consent visit: {failure} ({record.get('page_id')})")
    first = (None if failure else _layer(_audit(record))) or _layer(_audit(home))
    language = (_audit(record).get("lexicon_lang") or _audit(home).get("lexicon_lang") or live.settings.locale)[:2]
    result = {"assessed": True, "page_id": home.get("page_id"), "stage": "home", "banner": first is not None,
              "first_layer": first, "visit": record.get("page_id"), "language": language}
    if first is None:
        return result
    result["reject_first_layer"] = first["reject"] is not None
    result["clicks_to_reject"] = 1 if first["reject"] else None
    if first["reject"] is None and first.get("close") and language == "it":
        result.update(reject_kind="close", clicks_to_reject=1)
    elif first["reject"] is None and first["manage"] is not None:
        if failure:
            return {"assessed": False, "reason": f"revisit_{failure}", "page_id": home.get("page_id"),
                    "stage": "home", "first_layer": first}
        manage = first["manage"]
        clicked = tab.click(record, purpose="consent_manage", stage="home", key="consent_manage",
                            labels=[manage["label"], manage["text"]], rect=manage.get("rect"), strict=True,
                            accept=own_controls([manage], _audit(record)))
        result["manage_click"] = {k: clicked.get(k) for k in ("executed", "reason", "label")}
        second, unread = None, clicked["reason"]
        if clicked["executed"]:
            tab.await_effect(clicked["origin"], expect_navigation=False, timeout=4.0)
            second = _layer(tab.fresh_audit())
            result["second_layer"] = second
            unread = "no_second_layer" if second is None else "manage_had_no_effect" if second == first else None
        # only a second layer that was read can show there is no reject anywhere
        result["second_layer_checked"] = unread is None
        if unread is not None:
            result["second_layer_reason"] = unread
        elif second["reject"] is not None:
            result["clicks_to_reject"] = 2
    # apply the run's consent policy so the product pages of this context are not covered by the banner; "manage"
    # is never pressed twice (the layer shown is what it opened, or it had no effect)
    current = tab.fresh_audit()
    if current and not failure:
        applied = tab.consent({**record, "audit": current}, live.settings.consent, stage="home",
                              after_manage=bool((result.get("manage_click") or {}).get("executed")))
        result["applied"] = {"choice": applied.get("choice"), "reason": applied.get("reason")}
    return result


def _aligned(live: _Live, tab: Tab, *, context: int, choice: str | None, made: str | None) -> dict:
    """Bring a context to the funnel's consent choice before its product visits (module docstring): when the funnel
    chose accept or reject and the context has not (made: the choice its consent test ended with), a fresh landing on
    settings.url gets the run's policy. {context, funnel_choice, choice[, landing, reason]}."""
    out = {"context": context, "funnel_choice": choice, "choice": made or "none"}
    if choice is None or made == choice:
        return out
    try:
        record = live.load(tab, live.settings.url)
    except FAILURES as exc:
        live.errors.append(f"consent landing {context}: {type(exc).__name__}: {str(exc)[:160]}")
        tab.lost()
        return {**out, "reason": "error"}
    out["landing"] = record.get("page_id")
    if failure := visit_failure(record):
        live.errors.append(f"consent landing {context}: {failure} ({record.get('page_id')})")
        return {**out, "reason": f"revisit_{failure}"}
    try:
        applied = tab.consent(record, live.settings.consent, stage="home")
    except FAILURES as exc:
        live.errors.append(f"consent landing {context}: {type(exc).__name__}: {str(exc)[:160]}")
        tab.lost()
        return {**out, "reason": "error"}
    return {**out, "choice": applied.get("choice"), "reason": applied.get("reason")}


def _reappears(live: _Live, tab: Tab, record: PageRecord, url: str) -> dict:
    """Close the overlays of the page the crawler closes (modal or blocking, not consent), check that they went away
    (Tab.dismiss: gone), reload the page in the same context: does the same overlay (overlay_key) come back?"""
    audit = tab.fresh_audit() or _audit(record)
    keys = _closable(audit)
    if not keys:
        return {"reappear_test": {"overlays": [], "page_id": record.get("page_id")}}
    closed = tab.dismiss(audit, record, stage="pdp")
    test = {"overlays": keys, "closed": closed, "page_id": record.get("page_id")}
    executed = [c for c in closed if c.get("executed")]
    if not executed:
        return {"reappear_test": test}
    gone = [c["key"] for c in executed if c.get("gone")]
    if not gone:  # the close did not close it (or that could not be read): a reload proves nothing
        reason = "close_had_no_effect" if any(c.get("gone") is False for c in executed) else "close_not_verified"
        return {"reappear_test": {**test, "reason": reason}}
    again = live.load(tab, url)
    failure = visit_failure(again)
    if failure:
        live.errors.append(f"nagging reload: {failure} ({again.get('page_id')})")
        return {"reappear_test": {**test, "reload_page_id": again.get("page_id"), "reason": f"revisit_{failure}"}}
    test.update(after_reload=_closable(_audit(again)), reload_page_id=again.get("page_id"))
    return {"reappear_test": test, "reappeared_after_close": any(key in test["after_reload"] for key in gone)}
