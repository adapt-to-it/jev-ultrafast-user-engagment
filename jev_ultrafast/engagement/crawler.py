"""Generic funnel discovery for one profile: home -> PLP -> PDP -> add to cart -> cart -> checkout entry, then stop.

No per-site plans and no hardcoded values: every choice comes from the collected AuditPayload (navigation categories,
product cards, the add-to-cart, cart and checkout controls audit.js recognised with the lexicon), from the lexicon's URL
patterns and from the elements Browser.observe() reports. A discovered URL is loaded with a GET (load_page), never one
whose GET changes the cart (a cart-action query key or path segment: oracles.clean_cart_url); anything
else is Browser.act() on one observed element, consulted with CheckoutGuard first, executed once and never retried,
and logged to steps.jsonl before its result is observed. A StalePage means nothing was sent: the page is cleared of
late overlays, observed again and the action is tried once more (stale_reobserved in the probe); a second StalePage is
a failure. Every control sent is remembered by the tab and never sent again on the same document (its URL,
performance.timeOrigin and label; an origin that could not be read matches every document at that URL; reason
"sent_earlier"), so a click without a visible effect is not repeated by a later clear-the-way pass (a close whose
overlay went away frees its label: the same label elsewhere is another control); a click whose Browser.act() failed
after it may have reached the page (CDP timeout or error, a transport that closed: that one stops the funnel) is
never sent again on that page URL (reason "uncertain_earlier"). Nothing is ever typed but the search probe's word,
and the run stops at the first checkout page without filling or submitting anything there. A click that ends on
Chrome's error page keeps that page as "extra" evidence and stops the funnel ("navigation_error"); a cart URL that
does not lead to a cart (a "#" mini-cart link, a login redirect) is "extra" too and stops it ("not_found"). A load
Chrome aborted (a 204 answer, a download: the tab keeps the previous document) is a failed load like its error page,
and a listing candidate that lands on the home page is no listing: both are "extra" and the next candidate is tried.

Overlays: after a home or listing page is accepted (and its repeats loaded), its interrupting non-consent overlays
(modal or blocking newsletter, promo) are closed with their observed close or decline control, as a shopper would
(that overlay's own control, located by its audit.js rect: never a cart line's "×" with the same label), and
the closes are recorded in PageRecord.probes["dismiss_overlays"] with the overlay's key (overlay_key) and whether a
fresh audit shows it gone; the product and cart pages do the same before their click
(probes["add_to_cart"]["overlays"], probes["checkout_entry"]["overlays"]).

Consent (settings.consent), each control clicked at most once: "accept" clicks accept. "reject", and "auto" on a banner
that interrupts or blocks the page (modal, 15% of the viewport or more, or covering its centre), click a first-layer
reject, else "manage" and a reject on the layer it opens, else, on an Italian page, the first layer's X/"Chiudi"
(Garante 2021: closing keeps only the technical cookies; closing a preference centre is "back", not a refusal); then
"auto" accepts a banner whose current layer blocks the page and "reject" leaves it. "auto" on a small bar that neither
interrupts nor blocks clicks at most one control: a first-layer reject, else the Italian X, else nothing
("not_blocking"); it never opens "manage" (deception.py probes "manage" in its own context). On such a bar the X
follows a reject only when nothing reached the page (not sent, not observed, refused by the guard): a reject that may
have been delivered (failed, or sent earlier) ends the chain, under any policy. Consent controls are matched by the
banner's own labels (exactly, else by containment) among the banner's own controls (their audit.js rects), never by
a lexicon word or a shared label anywhere else on the page. Known residual: a
small non-Italian bar with no reject that overlaps a mobile sticky CTA is left alone. The funnel never clicks under a
blocking banner (cart and checkout stop with "consent_blocking"); "none" leaves the banner alone. The choice is recorded
in PageRecord.consent of the page that showed it, with the clicks it took, the path (via), whether the banner
interrupts and whether its current layer blocks.

Search probe (home): a word taken from an observed category label is typed into the observed search field (CheckoutGuard
allow_text decides first); visible role=option entries that appear within 800 ms of the end of typing are recorded in
PageRecord.probes["search_autocomplete"]. The product page is the first candidate that classifies as a buyable PDP:
a candidate unavailable() flags (a single schema.org Offer out of stock, sold out or discontinued; a disabled
add-to-cart with no variant group that could enable it; no add-to-cart and out-of-stock wording) is kept as "extra"
and the next one is tried; when every candidate is unavailable the first is kept as the product page, nothing is
clicked and the cart stage gets reason "out_of_stock". Before adding to the cart, a variant group without a selected
option gets its first available option (one code-owned pick). What was added is recorded in
PageRecord.probes["add_to_cart"] of the product page (deception.py compares it with the cart lines). From the cart, the
checkout entry is reached the way a first-time buyer would: through the cart's own guest path when it offers one
(forms.guest_option and an observed "guest" control leading to a checkout URL; probes["checkout_entry"]["guest"]
records its href), else through its checkout CTA (probes["checkout_entry"]["via"]). No click is sent when max_pages
leaves no room for the page it leads to.

A stage that cannot be reached is recorded as NotAssessable with its reason, and so is every later stage (with the
reason that stopped the funnel); a bot challenge stops the funnel with reason "bot_challenge". A page loaded in a tab
that replaced a lost one (timeout, crash) records the tab's number in probes["tab"] (2, 3, ...): per-tab state such as
sessionStorage starts over there.

Jev fallback (discover_funnel(jev_fallback=True): audit.py turns it on when TYPESAFE_API_KEY is set; off, nothing of
this runs and nothing above changes): where the lexicon finds no listing link, no product link, no cart link, or no
add-to-cart, variant or checkout control, Jev is asked once, the way the agent loop asks it (jev_ultrafast.model.choose
with a fixed generic goal, GOALS), over the observed click actions CheckoutGuard allows, minus what the lookup's own
offer leaves out (never a control the lexicon reads as removing, buying now, logging in...: Crawl.link_offer,
add_offer, the checkout CTA's and the variant option's filters); only a CLICK and its target head are consumed, so Jev
picks an observed element index, never a selector or text. A link's href is then loaded like a lexicon candidate
(same filters), an element without a usable href is clicked once; the stage's own test still decides (is_listing,
is_product, is_cart; for the checkout CTA Jev chose: a checkout page type, a checkout URL or an account gate, else the
page is "extra" and the stage "checkout_cta_not_found"), and the funnel goes on deterministically. Link lookups ask on
the top of their start page (reloaded as "extra" when the tab has left it; under a blocking consent banner only links
are offered: no click under the banner), click lookups where the lexicon missed (the add-to-cart by the product's
price). One request per lookup (a GOALS key) and profile, at most 6 per profile: a second miss sends nothing
("fallback_exhausted"); a control Jev chose that met a stale page is picked again by its label on the one re-observe,
without a request. Nothing is asked when max_pages has no room for the page it would lead to ("max_pages"), and the
NotAssessable reasons of a lookup that found nothing are those without the fallback. The guest path, consent, overlays,
the search probe, repeats and the deception tests are never asked. Every lookup is recorded in
PageRecord.probes["jev_fallback"] (a list) of the page it was asked on, with a note; a click it chose is logged with
note "jev_fallback".
"""

import re
import time
from datetime import UTC, datetime
from functools import lru_cache
from urllib.parse import urljoin, urlsplit, urlunsplit

from .. import model
from ..browser import StalePage, session_gone
from .collectors import AuditError
from .lexicon import compile_lexicon, lexicon_for
from .oracles import clean_cart_url
from .pagetypes import _home_path
from .safety import GUARD_HREF, CheckoutGuard
from .schemas import STAGES, NotAssessable, PageRecord

CANDIDATES = 3  # listing and product candidates tried at most
PROBE_WINDOW_MS = 800
NAV_EVENTS = ("Page.frameRequestedNavigation", "Page.frameStartedLoading", "Page.frameNavigated")
WORD = re.compile(r"[^\W\d_]{4,}")
SCROLL_JS = "window.scrollTo(0, Math.max(0, %s - innerHeight / 2)), scrollY"
# latency from the end of typing (since = when this evaluation starts); options already there count as 0 ms
OPTIONS_JS = """((since, before, windowMs) => new Promise(resolve => {
  const count = () => [...document.querySelectorAll('[role="option"]')].filter(e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight &&
      e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
  }).length;
  let first = true;
  const tick = () => {
    const n = count() - before, at = performance.now() - since;
    if (n > 0 || at >= windowMs) {
      resolve({options: Math.max(0, n), latency_ms: n > 0 ? (first ? 0 : Math.round(at)) : null, t0: since});
    } else { first = false; setTimeout(tick, 20); }
  };
  tick();
}))(%s, %s, %s)"""
COUNT_OPTIONS_JS = OPTIONS_JS % ("performance.now()", 0, 0)
CONSENT_KEYS = {"accept": "consent_accept", "reject": "consent_reject", "manage": "consent_manage", "close": None}
UNSENT = ("not_executed:", "not_observed", "control_not_found")  # click reasons that mean nothing reached the page
DELIVERED = ("failed:", "uncertain_earlier", "sent_earlier")  # click reasons that mean it may have reached the page
GONE = ("OutOfStock", "SoldOut", "Discontinued")  # schema.org availability of an item that cannot be bought
_READ = object()  # Tab.act: read performance.timeOrigin now
GOALS = {  # the Jev fallback's goals: fixed, generic, English; no site values (select_variant: an observed label)
    "plp": "Open a product category listing page of this shop: a page that lists several products of one category. "
           "Do not open the cart, the checkout, an account page or an information page.",
    "pdp": "Open the page of one single product (its product page with a price and an add-to-cart control).",
    "cart": "Open the shopping cart page: the page listing the items already added to the cart. Do not add or remove "
            "items.",
    "add_to_cart": "Add the product shown on this page to the shopping cart with the one control that does it. Do not "
                   "buy now and do not pay.",
    "select_variant": "Select the product option '{option}' on this page.",
    "checkout_entry": "Go from this cart page to the checkout (its first step). Do not remove items, change "
                      "quantities or select options. Do not pay and do not place an order.",
}
SERVES = {"plp": "plp", "pdp": "pdp", "cart": "cart", "add_to_cart": "cart", "select_variant": "cart",
          "checkout_entry": "checkout_entry"}  # the funnel stage a lookup serves
# jev_fallback reasons given before any request (nothing was asked); a reason given after one is never one of these
NO_REQUEST = ("no_click_actions", "fallback_exhausted", "not_observed", "max_pages", "consent_blocking")


def _norm(text) -> str:
    return " ".join(str(text or "").split()).casefold()


@lru_cache(maxsize=8)
def _lexicon(language: str) -> dict:
    return compile_lexicon(lexicon_for(language))


def _mask(text) -> str:
    return re.sub(r"\d+", "#", _norm(text))


def _url(href, base: str = "") -> str | None:
    """Absolute http(s) URL without its fragment, or None."""
    try:
        parts = urlsplit(urljoin(base, str(href or "").strip()))
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def _href(page: dict, action: dict) -> str | None:
    """The raw href snapshot.js observed on the action's element (CheckoutGuard's guard tuple), or None."""
    guard = (page.get("guards") or {}).get(str(action.get("node")))
    return guard[GUARD_HREF] if isinstance(guard, list) and len(guard) > GUARD_HREF and guard[GUARD_HREF] else None


def _page_rect(page: dict, action: dict) -> dict | None:
    """An observed action's rect (viewport coordinates) in page coordinates at the page's scroll, for pick(near=)."""
    r = action.get("rect") or {}
    if not isinstance(r.get("y"), (int, float)):
        return None
    return {"y": r["y"] + ((page.get("scroll") or {}).get("y") or 0), "h": r.get("h") or 0}


def unlabelled(lx: dict, *keys: str):
    """accept(action, page): an action whose label none of the lexicon keys reads (a Jev lookup's offer leaves the
    controls that remove, buy now, check out, log in... out)."""
    def accept(action: dict, page: dict) -> bool:
        label = " ".join(str(action.get("label") or "").split())
        return not any(key in lx and lx[key].search(label) for key in keys)
    return accept


def _type(record: PageRecord | None) -> str | None:
    return ((record or {}).get("classification") or {}).get("type")


def _audit(record: PageRecord | None) -> dict:
    return (record or {}).get("audit") or {}


def failed_navigation(record: PageRecord) -> bool:
    return "navigation_error" in ((record.get("classification") or {}).get("signals") or [])


def consent_banner(audit: dict) -> dict | None:
    return next((o for o in audit.get("overlays") or [] if o.get("consent_like") or o.get("kind") == "consent"), None)


def blocking_banner(audit: dict) -> bool:
    banner = consent_banner(audit)
    return bool(banner and banner.get("blocking"))


def interrupting(overlay: dict) -> bool:
    """A modal or blocking non-consent overlay (newsletter, promo, age gate): what a shopper closes first."""
    return bool(overlay.get("interrupting") and (overlay.get("modal") or overlay.get("blocking"))
                and not (overlay.get("consent_like") or overlay.get("kind") == "consent"))


def overlay_key(overlay: dict) -> str:
    """One overlay across pages and audits: its kind and the start of its text, digits masked (a ticking promo stays
    one overlay; two promos of the catch-all kind "promo" are two)."""
    text = _mask(overlay.get("text_sample"))[:60].strip()
    return f"{overlay.get('kind')}: {text}" if text else str(overlay.get("kind"))


def cart_empty(audit: dict) -> bool:
    """No item in the cart: the page says so, or it lists no line and no total above zero. A statement about the cart's
    content, not about audit.js's line parser (a total above zero proves content even when no line was parsed)."""
    cart = audit.get("cart") or {}
    if cart.get("empty"):
        return True
    if cart.get("line_items"):
        return False
    return not any((cart.get(key) or 0) > 0 for key in ("total_value", "subtotal_value"))


def button_labels(overlay: dict | None, *kinds: str) -> list[str]:
    return [label for kind in kinds for b in (overlay or {}).get("buttons") or [] if b.get("kind") == kind
            for label in (b.get("label"), b.get("text")) if label]


def own_controls(buttons, audit: dict):
    """accept(action, page) for Tab.pick: only an observed action that is one of these audit.js buttons (its centre
    inside one of their rects), never another control with the same label elsewhere on the page (a cart line's "×"
    before a newsletter's "×"). audit.js rects are page coordinates at the audit's scroll (viewport.scroll_y); an
    observed rect is in viewport coordinates: it matches at the page's scroll now (a box in the page flow) or at the
    audit's scroll (a fixed overlay, which keeps its viewport place while the page scrolls). A button without a rect
    matches nothing."""
    rects = [b["rect"] for b in buttons or [] if isinstance(b, dict) and isinstance(b.get("rect"), dict)]
    then = (audit.get("viewport") or {}).get("scroll_y") or 0

    def inside(x: float, y: float, r: dict) -> bool:
        return (r.get("x") or 0) - 2 <= x <= (r.get("x") or 0) + (r.get("w") or 0) + 2 and \
            (r.get("y") or 0) - 2 <= y <= (r.get("y") or 0) + (r.get("h") or 0) + 2

    def accept(action: dict, page: dict) -> bool:
        r = action.get("rect") or {}
        if not isinstance(r.get("x"), (int, float)) or not isinstance(r.get("y"), (int, float)):
            return False
        x, y = r["x"] + (r.get("w") or 0) / 2, r["y"] + (r.get("h") or 0) / 2
        now = (page.get("scroll") or {}).get("y") or 0
        return any(inside(x, y + scroll, rect) for rect in rects for scroll in (now, then))
    return accept


def is_listing(record: PageRecord) -> bool:
    """A listing: classified as one, or an unclassified page with four product cards or more. A home page is not,
    whatever it features (a category link that sends the shopper back home has no listing)."""
    kind, products = _type(record), _audit(record).get("products") or {}
    if kind == "plp":
        return True
    big = (products.get("main_group") or 0) >= 4 or (products.get("itemlist_jsonld") or 0) >= 4
    return big and kind not in ("home", "pdp", "cart", "checkout", "challenge")


def is_product(record: PageRecord) -> bool:
    kind, pdp = _type(record), _audit(record).get("pdp") or {}
    if kind == "pdp":
        return True
    buyable = (pdp.get("add_to_cart") or {}).get("present") and (pdp.get("price") or pdp.get("structured"))
    return bool(buyable) and kind not in ("home", "plp", "cart", "checkout", "challenge")


def is_cart(record: PageRecord) -> bool:
    """A cart page: classified as one (or as the checkout a cart URL may redirect to), or an unclassified page with
    cart evidence (lines, a total, an empty-cart message). A product page, a listing or a login page is not."""
    kind, cart = _type(record), _audit(record).get("cart") or {}
    if kind in ("cart", "checkout"):
        return True
    evidence = cart.get("line_items") or cart.get("empty") or any(
        (cart.get(key) or 0) > 0 for key in ("total_value", "subtotal_value"))
    return bool(evidence) and kind not in ("home", "plp", "pdp", "challenge")


def variant_gated(pdp: dict) -> bool:
    """A variant group that could still enable the add-to-cart: one whose option is not selected yet, or one whose
    selection audit.js does not report (unknown counts as gated)."""
    return any(isinstance(g, dict) and g.get("selected") is not True for g in pdp.get("variant_groups") or [])


def unavailable(audit: dict, lx: dict | None = None) -> str | None:
    """Why a product page's item cannot be bought, or None: "availability" (a single schema.org Offer OutOfStock,
    SoldOut or Discontinued), "cta_disabled" (a disabled add-to-cart and no variant group that could enable it),
    "stock_text" (no add-to-cart and out-of-stock wording that is not scarcity: "quasi esaurito" is buyable)."""
    pdp = audit.get("pdp") or {}
    structured, control = pdp.get("structured") or {}, pdp.get("add_to_cart") or {}
    if structured.get("offers") == 1 and structured.get("availability") in GONE:
        return "availability"
    if control.get("present") and control.get("enabled") is False and not variant_gated(pdp):
        return "cta_disabled"
    text = " ".join(str(pdp.get("stock_text") or "").split())
    if not control.get("present") and text:
        lx = lx if lx is not None else _lexicon(audit.get("lexicon_lang") or "it")
        if "out_of_stock" in lx and lx["out_of_stock"].search(text) and not (
                "scarcity" in lx and lx["scarcity"].search(text)):
            return "stock_text"
    return None


def category_word(audit: dict, lx: dict) -> str | None:
    """The first word (4+ letters) of an observed category label that is not a utility word ("Accedi", "Carrello")."""
    utility = lx.get("utility")
    for category in (audit.get("nav") or {}).get("categories") or []:
        label = str(category.get("label") or "")
        if utility is not None and utility.search(label):
            continue
        for word in WORD.findall(label):
            return word.casefold()
    return None


def listing_candidates(record: PageRecord, guard, lx: dict) -> list[str]:
    """Category links of the page ranked by evidence: a category URL pattern, visibility, a SiteNavigationElement;
    utility, cart, checkout, product and content links never qualify."""
    audit, here = _audit(record), _url(record.get("final_url") or record.get("url"))
    categories = (audit.get("nav") or {}).get("categories") or []
    links = [(c.get("label"), c.get("href"), c.get("visible")) for c in categories]
    for item in audit.get("jsonld") or []:
        if isinstance(item, dict) and "SiteNavigationElement" in str(item.get("@type")):
            links += [(name, url, None) for name, url in zip(_list(item.get("name")), _list(item.get("url")))]
    scored = []
    for index, (label, href, visible) in enumerate(links):
        url = _url(href, here or "")
        if not url or url == here or not guard.same_site(url) or guard.is_checkout({"url": url}, None):
            continue
        parts = urlsplit(url)
        path = parts.path + ("?" + parts.query if parts.query else "")
        if any(key in lx and lx[key].search(path) for key in ("cart_url", "product_url", "info_url")):
            continue
        if "utility" in lx and lx["utility"].search(str(label or "")):
            continue
        evidence = 3 * bool("category_url" in lx and lx["category_url"].search(path)) + bool(visible)
        scored.append((-evidence - (visible is None), index, url))  # a SiteNavigationElement entry counts as visible
    return list(dict.fromkeys(url for *_, url in sorted(scored)))


def landed_home(record: PageRecord, home: PageRecord) -> bool:
    """record shows the home page: the home record's final URL, or a home path ("/", "/it/", "/index.html")."""
    final = _url(record.get("final_url"))
    if final is None:
        return False
    parts = urlsplit(final)
    return final == _url(home.get("final_url")) or _home_path(parts.path, parts.query)


def landed_guard(guard, record: PageRecord | None, locale: str):
    """The guard of the shop the start URL landed on (JourneyRunner anchors its guard the same way): when the home
    page's final URL is a web URL on another registrable domain than guard's, a new CheckoutGuard anchored there; else
    guard itself (a failed load, a Chrome error page)."""
    final = _url((record or {}).get("final_url"))
    if final is None or guard.same_site(final):
        return guard
    return CheckoutGuard(final, lexicon_for(locale), stop_at=getattr(guard, "stop_at", "checkout_entry"))


def _list(value) -> list:
    return value if isinstance(value, list) else [value] if value is not None else []


def product_candidates(record: PageRecord | None, guard) -> list[str]:
    """Product card links in page order, then the page's JSON-LD ItemList entries; never a URL whose GET changes the
    cart (a cart-action query key or path segment: clean_cart_url)."""
    audit, here = _audit(record), _url((record or {}).get("final_url") or (record or {}).get("url"))
    hrefs = [card.get("href") for card in (audit.get("products") or {}).get("cards") or []]
    for item in audit.get("jsonld") or []:
        if isinstance(item, dict) and "ItemList" in str(item.get("@type")):
            for entry in _list(item.get("itemListElement")):
                inner = entry.get("item") if isinstance(entry, dict) else None
                if isinstance(entry, dict):
                    hrefs.append(entry.get("url") or (inner.get("url") if isinstance(inner, dict) else inner))
    urls = []
    for href in hrefs:
        url = _url(href, here or "")
        if url and url != here and guard.same_site(url) and not guard.is_checkout({"url": url}, None) and \
                clean_cart_url(url) == (url, []):
            urls.append(url)
    return list(dict.fromkeys(urls))


def jev_pick(page: dict, guard, page_type: str | None, goal: str, accept=None) -> tuple[dict | None, dict]:
    """Ask Jev which observed element to click for goal: one jev_ultrafast.model.choose request (the agent loop's own:
    operation head and target heads) over the click actions guard.filter_actions allows (accept: only those it
    accepts); only a CLICK and its target are consumed, DONE or BLOCKED is no element. (the action, the answer's
    record) or (None, the record with its reason: "no_click_actions" (nothing asked), "jev_error" (with the time it
    took, no usage), "jev_done", "jev_blocked")."""
    allowed = [a for a in guard.filter_actions(page, page_type)[0] if a.get("kind") == "click"
               and (accept is None or accept(a, page))]
    if not allowed:
        return None, {"reason": "no_click_actions"}
    state = {**page, **{key: page.get(key) or "" for key in ("url", "title", "text")}, "actions": allowed}
    started = time.perf_counter()
    try:
        decision = model.choose(state, goal, [])
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:  # HTTP or connection; an invalid answer
        return None, {"reason": "jev_error", "error": f"{type(exc).__name__}: {str(exc)[:160]}",
                      "latency_ms": round((time.perf_counter() - started) * 1000)}  # no usage: no answer
    meta = {key: decision.get(key) for key in ("operation", "target", "confidence", "target_confidence", "model",
                                               "usage", "latency_ms")}
    if decision["operation"] != "CLICK":
        return None, {**meta, "reason": f"jev_{str(decision['operation']).lower()}"}
    action = next((a for a in allowed if a.get("id") == decision["choice"]), None)
    if action is None:
        return None, {**meta, "reason": "jev_error"}
    return action, {**meta, "label": str(action.get("label") or "")[:120], "href": _href(page, action),
                    "probability": (decision.get("probabilities") or {}).get(decision["choice"]), "reason": None}


class Tab:
    """One tab of a profile's browser context: page loads through the collector, and observed actions that pass
    CheckoutGuard, executed once and logged to steps.jsonl before their result is observed. The deception tests use
    their own Tab in their own context (source "deception" in steps.jsonl). policy is the consent policy clear_way()
    applies (settings.consent). sent holds (url, timeOrigin, label) of every control sent to a document (timeOrigin
    None when it could not be read: was_sent), uncertain (url, label) of clicks that may have reached the page although
    Browser.act() failed: neither is sent again there. opened counts the browser tabs opened (a lost tab is replaced
    by a new one). chooser (Crawl.chooser with the Jev fallback on, else None): click() asks it when pick() finds no
    control, chooser(record, page, purpose, labels, accept) -> (an observed action or None, its probe or None)."""

    def __init__(self, collector, guard, *, profile: str, policy: str = "auto", source: str = "crawler"):
        self.collector, self.guard, self.profile, self.policy = collector, guard, profile, policy
        self.source = source
        self.transport = collector.transport
        self.browser = None
        self.opened = 0
        self._lexicons: dict[str, dict] = {}
        self.sent: set[tuple] = set()
        self.last_sent: tuple | None = None  # the key of the last control sent
        self.uncertain: set[tuple[str, str]] = set()
        self.after_dismiss: dict | None = None  # the fresh audit dismiss() read after its closes
        self.chooser = None

    # ---------------------------------------------------------------- pages
    def lexicon(self, record: PageRecord | None) -> dict:
        language = self.language(record)
        if language not in self._lexicons:
            self._lexicons[language] = _lexicon(language)
        return self._lexicons[language]

    def language(self, record: PageRecord | None) -> str:
        return _audit(record).get("lexicon_lang") or self.collector.locale

    def load(self, url: str, *, stage: str, page_id: str) -> PageRecord:
        """Open the tab on url, or navigate it there (once, never retried); TimeoutError and CDP errors propagate."""
        if self.browser is None:
            self.browser = self.collector.open(url)
            self.opened += 1
        return self.collector.load_page(self.browser, url, stage=stage, page_id=page_id)

    def collect(self, *, stage: str, page_id: str) -> PageRecord:
        """The document a click navigated to."""
        return self.collector.collect(self.browser, stage=stage, page_id=page_id)

    def lost(self) -> None:
        """Forget a tab whose renderer or session is gone; the next load opens a new tab in the same context."""
        browser, self.browser = self.browser, None
        if browser is not None:
            try:
                self.collector.close(browser)
            except (RuntimeError, OSError, TimeoutError):
                pass

    def close(self) -> None:
        self.lost()

    def value(self, expression: str, *, timeout: float = 5.0, promise: bool = False):
        """A read-only evaluation; None while the document is replaced or without a tab. A gone session raises."""
        if self.browser is None:
            return None
        try:
            response = self.transport.call("Runtime.evaluate", self.browser.session, timeout=timeout,
                                           expression=expression, returnByValue=True, awaitPromise=promise)
        except TimeoutError:
            return None
        except RuntimeError as exc:
            if session_gone(exc):
                raise
            return None
        if response.get("exceptionDetails"):
            return None
        return response.get("result", {}).get("value")

    def fresh_audit(self) -> dict:
        """audit.js on the current document (read-only), for rects and overlays as they are now; {} on failure."""
        if self.browser is None:
            return {}
        try:
            return self.collector.audit(self.browser) or {}
        except (AuditError, StalePage, TimeoutError):
            return {}

    def observe(self) -> dict | None:
        if self.browser is None:
            return None
        try:
            return self.browser.observe(screenshot=False)
        except StalePage:
            return None

    def scroll_to(self, rect: dict | None) -> None:
        """Bring a page-coordinate rect (audit.js) to mid-viewport: observe() lists elements in the viewport only."""
        if rect and isinstance(rect.get("y"), (int, float)):
            self.value(SCROLL_JS % float(rect["y"] + (rect.get("h") or 0) / 2))

    # ---------------------------------------------------------------- actions
    @staticmethod
    def pick(page: dict, lx: dict, *, labels=(), key: str | None = None, kinds=("click",), roles=None,
             near: dict | None = None, strict: bool = False, accept=None) -> dict | None:
        """The observed action whose label (for a select option: the option's label) equals one of labels
        (casefolded), else one whose label matches the lexicon key; several: the one closest to near (a
        page-coordinate rect). strict (an overlay's own controls): no lexicon match anywhere on the page, only a label
        that contains one of labels or is contained in one ("Rifiuta" and "Rifiuta tutti i cookie"). accept(action,
        page): only actions it accepts are candidates (the control a caller verified, not another one with its
        label)."""
        actions = [a for a in page.get("actions") or [] if a.get("kind") in kinds
                   and (roles is None or a.get("role") in roles) and (accept is None or accept(a, page))]
        wanted = {_norm(label) for label in labels if _norm(label)}

        def named(a) -> bool:
            label = str(a.get("label") or "")
            return _norm(label) in wanted or (a.get("kind") == "select" and _norm(label.rsplit("→", 1)[-1]) in wanted)

        def related(a) -> bool:
            label = _norm(a.get("label"))
            return len(label) >= 3 and any(len(w) >= 3 and (w in label or label in w) for w in wanted)

        found = [a for a in actions if named(a)]
        if not found and strict:
            found = [a for a in actions if related(a)]
        elif not found and key and key in lx:
            found = [a for a in actions if lx[key].search(" ".join(str(a.get("label") or "").split()))]
        if not found:
            return None
        if near and isinstance(near.get("y"), (int, float)):
            target = near["y"] + (near.get("h") or 0) / 2
            scroll = (page.get("scroll") or {}).get("y") or 0

            def distance(a):
                r = a.get("rect") or {}
                return abs((r.get("y") or 0) + (r.get("h") or 0) / 2 + scroll - target)

            return min(found, key=distance)
        return found[0]

    def log(self, action: dict, page: dict, *, purpose: str, stage: str, status: str, text: str | None = None,
            note: str | None = None) -> None:
        store, run_id = self.collector.store, self.collector.run_id
        if store is None or not run_id:
            return
        store.append_step(run_id, {
            "source": self.source, "profile": self.profile, "stage": stage, "purpose": purpose,
            "operation": "TYPE_TEXT" if action.get("kind") == "fill" else "CLICK", "target": action.get("id"),
            "label": str(action.get("label") or "")[:120], "kind": action.get("kind"), "role": action.get("role"),
            "node": action.get("node"), "text_len": len(text) if text is not None else None,
            "url_before": page.get("url"), "status": status, "note": note,
            "t_wall": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        })

    def was_sent(self, url: str, origin, label: str) -> bool:
        """label was sent to this document, or may have been: an origin that could not be read (None: a busy page's
        Runtime.evaluate timed out) matches every document at url, on either side."""
        return any(u == url and lb == label and (o == origin or o is None or origin is None) for u, o, lb in self.sent)

    def act(self, action: dict, page: dict, page_type: str | None, *, purpose: str, stage: str,
            text: str | None = None, origin=_READ, note: str | None = None) -> tuple[bool, str | None]:
        """Guard, then one Browser.act(). (executed, reason). A StalePage means nothing was sent: not executed. A
        control sent to a document (page URL, origin = its performance.timeOrigin, label) is never sent to it again:
        "sent_earlier" (an unknown origin counts as the same document: was_sent); an act that failed after it may
        have reached the page is never sent again on that page URL: "uncertain_earlier". note goes to the step
        ("jev_fallback": Jev chose the control)."""
        url, label = str(page.get("url") or ""), _norm(action.get("label"))
        if (url, label) in self.uncertain:
            return False, "uncertain_earlier"
        if origin is _READ:
            origin = self.value("performance.timeOrigin")
        sent = (url, origin, label)
        if self.was_sent(url, origin, label):
            return False, "sent_earlier"
        ok, why = self.guard.allowed_action(action, page, page_type)
        if ok and action.get("kind") == "fill":
            ok, why = self.guard.allow_text(self.browser, action, page_type)
        if not ok:
            return False, f"guard_refused:{why}"
        self.transport.events(self.browser.session, "Page.frame")  # older navigation events are not this action's
        self.last_sent = sent
        try:
            self.browser.act(action, page, text=text)
        except StalePage as exc:
            return False, f"not_executed:{str(exc)[:80]}"
        except (RuntimeError, OSError) as exc:  # may have reached the page (a CDP error or timeout, a transport
            # that closed after the input): logged, never sent again
            self.uncertain.add((url, label))
            self.sent.add(sent)
            self.log(action, page, purpose=purpose, stage=stage, status="error", text=text,
                     note="; ".join(filter(None, (note, f"{type(exc).__name__}: {str(exc)[:200]}"))))
            if isinstance(exc, ConnectionError) or (isinstance(exc, RuntimeError) and session_gone(exc)):
                raise  # a gone session or a closed transport stops the funnel
            return False, f"failed:{type(exc).__name__}"
        self.sent.add(sent)
        self.log(action, page, purpose=purpose, stage=stage, status="executed", text=text, note=note)
        return True, None

    def click(self, record: PageRecord, *, purpose: str, stage: str, labels=(), key: str | None = None,
              rect: dict | None = None, roles=None, kinds=("click",), clear: bool = False,
              strict: bool = False, accept=None, offer=None) -> dict:
        """Observe, pick (accept: pick()), guard, act once. {"executed", "reason", "label", "href", "origin"}, href:
        the observed href of the control picked. Nothing sent (StalePage, or a
        page that could not be observed): once more after clear_way() when clear (late overlays), with
        "stale_reobserved": 1 and what clear_way() did in "cleared"; a second StalePage is a failure. A control
        already sent to this document is not sent again ("sent_earlier"), nor one whose earlier click on this page
        failed after it may have been delivered ("uncertain_earlier"). No control picked: self.chooser (the Jev
        fallback) may choose one among the observed clicks that accept and offer (the lookup's own filter, for Jev's
        offer only) both take ("jev_fallback": True; never for a select). Jev's element that met a stale page is
        picked again on the re-observed page by its label (the one nearest to where it was), without a second
        request, and its outcome updates the same probe; gone: "control_not_found"."""
        result: dict = {"executed": False, "reason": "not_observed"}
        jev, chosen = None, None  # Jev's probe, and its element's (label, page rect), once Jev chose one

        def offered(action: dict, page: dict) -> bool:
            return (accept is None or accept(action, page)) and (offer is None or offer(action, page))

        for attempt in range(2):
            if attempt:
                result["stale_reobserved"] = 1
                if jev is not None:
                    jev["stale_reobserved"] = 1
                if clear:
                    audit, cleared = self.clear_way(record, stage=stage)
                    if cleared:
                        result["cleared"] = cleared
                    if blocking_banner(audit):
                        return {**result, "reason": "consent_blocking"}  # never a click under a blocking banner
            if rect:
                self.scroll_to(rect)
            page = self.observe()
            if page is None:
                continue
            if chosen is None:
                action = self.pick(page, self.lexicon(record), labels=labels, key=key, near=rect, roles=roles,
                                   kinds=kinds, strict=strict, accept=accept)
                if action is None and self.chooser is not None and "click" in kinds:
                    action, probe = self.chooser(record, page, purpose, labels, offered)
                    if action is not None:
                        jev, chosen = probe, (str(action.get("label") or ""), _page_rect(page, action))
            else:  # the stale re-observe of Jev's element: its label again, never a new request
                action = self.pick(page, self.lexicon(record), labels=[chosen[0]], near=chosen[1], accept=offered)
                if action is None:
                    jev.update(executed=False, reason="control_not_found")
            if action is None:
                return {**result, "reason": "control_not_found"}
            origin = self.value("performance.timeOrigin")
            executed, reason = self.act(action, page, _type(record), purpose=purpose, stage=stage, origin=origin,
                                        note="jev_fallback" if jev is not None else None)
            if jev is not None:
                jev.update(executed=executed, reason=reason)
                result["jev_fallback"] = True
            result.update(executed=executed, reason=reason, label=str(action.get("label") or "")[:120],
                          href=_href(page, action), origin=origin)
            if executed or not str(reason).startswith("not_executed:"):
                break
        return result

    def await_effect(self, origin, *, expect_navigation: bool, timeout: float) -> str:
        """After a click: "navigated" (a new document), "quiet" (no navigation and 800 ms without DOM, resource or
        layout activity) or "timeout". A click that starts a navigation is waited for until its document commits."""
        started, navigating = time.monotonic(), expect_navigation
        deadline = started + timeout
        while time.monotonic() < deadline:
            for event in self.transport.events(self.browser.session, "Page.frame"):
                if event["method"] in NAV_EVENTS and event["params"].get("frameId") == self.browser.target:
                    navigating = True
            current = self.value("performance.timeOrigin", timeout=2.0)
            if isinstance(current, (int, float)) and isinstance(origin, (int, float)) and abs(current - origin) > 0.5:
                return "navigated"
            if not navigating and time.monotonic() - started >= 0.8 and self.value(
                    "window.__jevVitals ? window.__jevVitals.quiet(800) : true", timeout=2.0):
                return "quiet"
            time.sleep(0.1)
        return "timeout"

    # ---------------------------------------------------------------- steps
    def consent(self, record: PageRecord, policy: str, *, stage: str, after_manage: bool = False) -> dict:
        """Apply the consent policy to the banner audit.js saw on the page (module docstring); PageRecord.consent
        shape: {policy, choice, reason, blocking, interrupting, reject_offered, clicks, via, label, failed}. blocking
        is the layer shown last (the one "manage" opened, when it was clicked). after_manage: the caller already
        clicked "manage" (deception.py), so the layer shown is what it opened and "manage" is not pressed again."""
        audit = _audit(record)
        layer = consent_banner(audit)
        result: dict = {"policy": policy}
        if layer is None:
            return {**result, "choice": "none", "reason": "no_consent_banner"}
        interrupts = bool(layer.get("interrupting") or layer.get("modal") or (layer.get("coverage") or 0) >= 0.15)
        result.update(blocking=bool(layer.get("blocking")), interrupting=interrupts,
                      reject_offered=bool(button_labels(layer, "reject")))
        if policy == "none":
            return {**result, "choice": "none", "reason": "policy_none"}
        clicks, failed = 0, []

        def press(kind: str, current: dict) -> dict:
            nonlocal clicks
            own = own_controls([b for b in current.get("buttons") or [] if b.get("kind") == kind], audit)
            clicked = self.click(record, purpose=f"consent_{kind}", stage=stage, labels=button_labels(current, kind),
                                 key=CONSENT_KEYS[kind], strict=True, accept=own)
            if clicked["executed"]:
                clicks += 1
                self.await_effect(clicked["origin"], expect_navigation=False, timeout=5.0)
            else:
                failed.append(f"{kind}:{clicked['reason']}")
            return clicked

        def chosen(choice: str, via: str, reason: str, clicked: dict) -> dict:
            return {**result, "choice": choice, "via": via, "clicks": clicks, "reason": reason,
                    "label": clicked.get("label")}

        def unchosen(reason: str) -> dict:
            return {**result, "choice": "none", "clicks": clicks, "reason": reason, **({"failed": failed}
                                                                                       if failed else {})}

        def delivered() -> bool:
            """A click that may have reached a banner not in the way ends the chain: no second mutation for it."""
            return bool(failed) and failed[-1].split(":", 1)[1].startswith(DELIVERED) and not (
                interrupts or result["blocking"])

        if policy == "accept":
            if button_labels(layer, "accept") and (clicked := press("accept", layer))["executed"]:
                return chosen("accept", "first_layer", "policy accept", clicked)
            return unchosen(failed[-1].split(":", 1)[1] if failed else "no_accept_control")
        via = "manage" if after_manage else "first_layer"
        if button_labels(layer, "reject") and (clicked := press("reject", layer))["executed"]:
            return chosen("reject", via, "reject offered" if via == "first_layer" else "reject behind manage", clicked)
        if delivered():
            return unchosen(failed[-1].split(":", 1)[1])
        # "manage" only where the banner is in the funnel's way, or when the user asked to reject
        if not after_manage and (policy == "reject" or interrupts or result["blocking"]) and button_labels(
                layer, "manage") and press("manage", layer)["executed"]:
            audit = self.fresh_audit()
            via, layer = "manage", consent_banner(audit) or {}
            result["blocking"] = bool(layer.get("blocking"))  # the layer shown now decides
            if button_labels(layer, "reject") and (clicked := press("reject", layer))["executed"]:
                return chosen("reject", via, "reject behind manage", clicked)
        if delivered():
            return unchosen(failed[-1].split(":", 1)[1])
        # the first layer's X only: closing a preference centre is "back", not a refusal
        if via == "first_layer" and self.language(record)[:2] == "it" and button_labels(layer, "close") and (
                clicked := press("close", layer))["executed"]:
            return chosen("reject", "close", "banner closed: the Garante 2021 guidelines read it as a refusal", clicked)
        if policy == "auto" and result["blocking"] and button_labels(layer, "accept") and (
                clicked := press("accept", layer))["executed"]:
            return chosen("accept", via, "no reject path and the banner blocks the page", clicked)
        if failed:  # a control that was there but could not be clicked (covered, stale, refused)
            return unchosen(failed[-1].split(":", 1)[1])
        reason = "not_blocking" if policy == "auto" and not result["blocking"] else (
            "no_accept_control" if policy == "auto" else "no_reject_control")
        return unchosen(reason)

    def dismiss(self, audit: dict, record: PageRecord, *, stage: str) -> list[dict]:
        """Close every modal or blocking non-consent overlay with its observed close (else decline) control, that
        overlay's own (own_controls: never a control elsewhere on the page with the same label). After an
        executed close a fresh audit tells whether the overlay is gone (its overlay_key no longer listed; None when
        the audit failed): a label shared by two overlays may have closed the other one. The last such audit is kept
        in self.after_dismiss. A close whose overlay is gone went away with it: its label is free again on this
        document (another overlay's "Chiudi" is another control); one without a visible effect is never sent again."""
        done = []
        self.after_dismiss = None
        for overlay in audit.get("overlays") or []:
            if not interrupting(overlay):
                continue
            entry = {"kind": overlay.get("kind"), "key": overlay_key(overlay)}
            labels = button_labels(overlay, "close", "decline")
            if not labels:
                done.append({**entry, "executed": False, "reason": "no_close_control"})
                continue
            own = own_controls([b for b in overlay.get("buttons") or [] if b.get("kind") in ("close", "decline")],
                               audit)
            clicked = self.click(record, purpose="dismiss_overlay", stage=stage, labels=labels, accept=own)
            entry.update(executed=clicked["executed"], reason=clicked["reason"], label=clicked.get("label"))
            done.append(entry)
            if clicked["executed"]:
                sent = self.last_sent
                self.await_effect(clicked["origin"], expect_navigation=False, timeout=4.0)
                after = self.fresh_audit()
                self.after_dismiss = after or None
                left = {overlay_key(o) for o in (after or {}).get("overlays") or [] if interrupting(o)}
                entry["gone"] = (entry["key"] not in left) if after else None
                if entry["gone"]:
                    self.sent.discard(sent)
        return done

    def clear_way(self, record: PageRecord, policy: str | None = None, *, stage: str) -> tuple[dict, list[dict]]:
        """A fresh audit with a blocking consent banner handled (policy, default self.policy) and blocking overlays
        closed."""
        audit = self.fresh_audit() or _audit(record)
        handled = []
        if blocking_banner(audit) and not record.get("consent"):
            record["consent"] = self.consent({**record, "audit": audit}, policy or self.policy, stage=stage)
            handled.append({"kind": "consent", **record["consent"]})
            audit = self.fresh_audit() or audit
        dismissed = self.dismiss(audit, record, stage=stage)
        if any(d["executed"] for d in dismissed):
            audit = self.after_dismiss or audit
        return audit, handled + dismissed

    def probe_search(self, record: PageRecord, *, stage: str) -> dict:
        """Type a category word into the observed search field; visible role=option entries within 800 ms of the end
        of typing. Browser.act types with one Input.insertText (an "input" event, no key events): widgets that listen
        to keyup only show nothing (a known false negative, method "insertText")."""
        audit, lx = _audit(record), self.lexicon(record)
        search = audit.get("search") or {}
        if not search.get("present"):
            return {"ran": False, "reason": "no_search_field"}
        word = category_word(audit, lx)
        if word is None:
            return {"ran": False, "reason": "no_category_word"}
        out: dict = {"ran": False, "typed": word, "method": "insertText"}
        executed, reason, action, before, mark = False, "not_observed", None, {}, None
        for attempt in range(2):
            if attempt:
                out["stale_reobserved"] = 1
                if blocking_banner(self.clear_way(record, stage=stage)[0]):
                    return {**out, "reason": "consent_blocking"}
            self.scroll_to(search.get("rect"))
            page = self.observe()
            if page is None:
                continue
            action = self.pick(page, lx, labels=[search.get("label")], key="search", kinds=("fill",),
                               near=search.get("rect"))
            if action is None:
                return {**out, "reason": "search_field_not_observed"}
            before = self.value(COUNT_OPTIONS_JS, promise=True) or {}
            mark = self.value("performance.now()")
            executed, reason = self.act(action, page, _type(record), purpose="search_probe", stage=stage, text=word)
            if executed or not str(reason).startswith("not_executed:"):
                break
        if not executed:
            return {**out, "reason": reason}
        out.update(ran=True, field=str(action.get("label") or "")[:80], window_ms=PROBE_WINDOW_MS)
        found = self.value(OPTIONS_JS % ("performance.now()", int(before.get("options") or 0), PROBE_WINDOW_MS),
                           timeout=5.0, promise=True)
        if not isinstance(found, dict):
            return {**out, "options": None, "reason": "unreadable"}
        typed_at = found.get("t0")
        typing = round(typed_at - mark) if isinstance(typed_at, (int, float)) and isinstance(mark, (int, float)) \
            else None
        return {**out, "options": found.get("options") or 0, "latency_ms": found.get("latency_ms"),
                "typing_ms": typing}


class Stop(Exception):
    """The funnel cannot go on: stage gets reason, later stages get then (default: reason)."""

    def __init__(self, stage: str, reason: str, then: str | None = None):
        super().__init__(f"{stage}: {reason}")
        self.stage, self.reason, self.then = stage, reason, then or reason


class Crawl:
    def __init__(self, collector, settings, *, profile: str, guard, progress=None, jev_fallback: bool = False):
        self.collector, self.settings, self.profile, self.guard = collector, settings, profile, guard
        self.say = progress or (lambda message: None)
        self.tab = Tab(collector, guard, profile=profile, policy=settings.consent)
        self.pages: list[PageRecord] = []
        self.missing: list[NotAssessable] = []
        self.counts: dict[str, int] = {}
        deepest = max(STAGES.index(s) for s in settings.stages)
        self.wanted = STAGES[:deepest + 1]
        self.last_error = "not_found"
        self.jev = jev_fallback
        self.fallbacks: dict[str, int] = {}  # Jev lookups asked, per GOALS key: one request each
        self.tab.chooser = self.chooser if jev_fallback else None

    # ---------------------------------------------------------------- bookkeeping
    def page_id(self, stage: str) -> str:
        self.counts[stage] = self.counts.get(stage, 0) + 1
        return f"{self.profile}-{stage}-{self.counts[stage]}"

    def mark(self, stage: str, reason: str) -> None:
        if not any(m["stage"] == stage for m in self.missing):
            self.missing.append({"stage": stage, "profile": self.profile, "kpi_id": None, "reason": reason})

    def stop(self, error: Stop) -> None:
        later = False
        for stage in self.wanted:
            if stage == error.stage:
                later = True
                if not any(p["stage"] == stage for p in self.pages):
                    self.mark(stage, error.reason)
            elif later:
                self.mark(stage, error.then)

    def room(self, stage: str) -> None:
        if len(self.pages) >= self.settings.max_pages:
            raise Stop(stage, "max_pages")

    def keep(self, record: PageRecord, stage: str) -> PageRecord:
        """Record a collected page; a bot challenge is kept as evidence (stage "extra") and stops the funnel."""
        self.pages.append(record)
        if self.tab.opened > 1:  # a tab that replaced a lost one: per-tab state (sessionStorage) starts over
            record.setdefault("probes", {})["tab"] = self.tab.opened
        if _type(record) == "challenge":
            record["stage"] = "extra"
            record.setdefault("notes", []).append(f"bot challenge instead of the {stage} page")
            raise Stop(stage, "bot_challenge")
        return record

    def visit(self, stage: str, url: str) -> PageRecord | None:
        """Load a discovered same-site URL (never a checkout URL) as stage; None when the load failed (last_error)."""
        self.room(stage)
        if not self.guard.same_site(url) or self.guard.is_checkout({"url": url}, None):
            self.last_error = "guard_refused:url"
            return None
        self.say(f"{self.profile}: {stage} {url}")
        try:
            record = self.tab.load(url, stage=stage, page_id=self.page_id(stage))
        except TimeoutError:
            self.last_error = "timeout"
            self.tab.lost()
            return None
        except RuntimeError as exc:
            self.last_error = "renderer_crashed" if session_gone(exc) else "navigation_error"
            self.tab.lost()
            return None
        self.keep(record, stage)
        if failed_navigation(record):
            self.last_error = "navigation_error"
            self.errored(record, stage, "the load")
            return None
        return record

    @staticmethod
    def errored(record: PageRecord, stage: str, what: str) -> None:
        """A failed navigation (Chrome's own error page, or one aborted while the tab kept the previous document) is
        evidence, never the stage's page."""
        record["stage"] = "extra"
        record.setdefault("notes", []).append(f"{stage}: {what} did not load a page (navigation error)")

    def repeat(self, record: PageRecord, stage: str, url: str) -> None:
        """settings.repeats > 1: reload an accepted home, listing or product page before any interaction (a reload
        changes no shop state); PageRecord.repeat_of. One page of max_pages stays for each later stage."""
        wanted = max(0, self.settings.repeats - 1)
        later = len(self.wanted) - self.wanted.index(stage) - 1
        loaded = 0
        for _ in range(wanted):
            if len(self.pages) + later >= self.settings.max_pages:
                break
            try:
                again = self.tab.load(url, stage=stage, page_id=self.page_id(stage))
            except (TimeoutError, RuntimeError):
                self.tab.lost()
                break
            again["repeat_of"] = record["page_id"]
            self.keep(again, stage)
            if failed_navigation(again):
                self.errored(again, stage, "a repeat load")
                break
            loaded += 1
        if loaded < wanted:
            record.setdefault("notes", []).append(f"{loaded} of {wanted} repeats loaded (max_pages or a failed load)")

    def close_overlays(self, record: PageRecord, stage: str, audit: dict | None = None) -> list[dict]:
        """Close the page's interrupting non-consent overlays as a shopper would; recorded in the page's probes."""
        closed = self.tab.dismiss(_audit(record) if audit is None else audit, record, stage=stage)
        if closed:
            record.setdefault("probes", {}).setdefault("dismiss_overlays", []).extend(closed)
        return closed

    def after_click(self, stage: str) -> PageRecord:
        """The document a click navigated to, kept as stage (the caller checked room() before the click)."""
        self.room(stage)
        try:
            record = self.tab.collect(stage=stage, page_id=self.page_id(stage))
        except TimeoutError as exc:
            raise Stop(stage, "timeout") from exc
        except RuntimeError as exc:
            self.tab.lost()
            raise Stop(stage, "renderer_crashed" if session_gone(exc) else "navigation_error") from exc
        self.keep(record, stage)
        if failed_navigation(record):
            self.errored(record, stage, "the click")
            raise Stop(stage, "navigation_error")
        return record

    @staticmethod
    def demote(record: PageRecord, stage: str) -> None:
        record["stage"] = "extra"
        record.setdefault("notes", []).append(f"{stage} candidate rejected: classified as {_type(record)}")

    # ---------------------------------------------------------------- Jev fallback
    def ask(self, record: PageRecord, page: dict | None, purpose: str, goal: str, accept=None, *,
            refused: str | None = None) -> tuple[dict | None, dict]:
        """One Jev request for the lookup purpose (a GOALS key) on the observed page (jev_pick), recorded in record's
        probes["jev_fallback"] and notes; a lookup already asked sends nothing ("fallback_exhausted"), nor one the
        caller refused before asking (refused: a NO_REQUEST reason such as "max_pages"). (the action Jev chose or
        None, its probe: {stage, purpose, goal, model, latency_ms, usage, operation, target, label, href,
        probability, confidence, executed, reason})."""
        if self.fallbacks.get(purpose):
            action, meta = None, {"reason": "fallback_exhausted"}
        elif refused:
            action, meta = None, {"reason": refused}
        elif page is None:
            action, meta = None, {"reason": "not_observed"}
        else:
            self.say(f"{self.profile}: Jev fallback ({purpose})")
            action, meta = jev_pick(page, self.guard, _type(record), goal, accept)
        self.fallbacks[purpose] = self.fallbacks.get(purpose, 0) + 1
        probe = {"stage": SERVES[purpose], "purpose": purpose, "goal": goal, **meta, "executed": False}
        record.setdefault("probes", {}).setdefault("jev_fallback", []).append(probe)
        chose = f"Jev chose '{probe['label']}'" if action is not None else f"no element ({meta['reason']})"
        record.setdefault("notes", []).append(f"jev_fallback {purpose}: the lexicon found nothing; {chose}")
        return action, probe

    def chooser(self, record: PageRecord, page: dict, purpose: str, labels=(), accept=None) -> tuple[dict | None,
                                                                                                   dict | None]:
        """Tab.chooser: Jev's pick for a click lookup the lexicon missed (add to cart, variant option, checkout CTA;
        select_variant names the option audit.js reported); any other click (the guest path included: rule d needs a
        control the lexicon reads as "guest") is never asked."""
        if purpose not in ("add_to_cart", "select_variant", "checkout_entry"):
            return None, None
        goal = GOALS[purpose].format(option=next((str(label) for label in labels if label), ""))
        return self.ask(record, page, purpose, goal, accept)

    def fallback_link(self, stage: str, start: PageRecord, *, home: PageRecord | None = None,
                      here: bool = False) -> PageRecord | None:
        """With the Jev fallback on, the page of stage ("plp", "pdp", "cart") reached through the element Jev picks on
        start's page (GOALS[stage]), else None (the caller keeps its own reason; only what stops the funnel anywhere
        stops it here: a bot challenge, a lost session). No room in max_pages for the page (and for the reload of
        start when needed): nothing asked (probe reason "max_pages"). The tab goes back to start (reloaded as "extra"
        when it has left it, unless here: the current document is start) and to its top; one observation, one request
        over the offer (link_offer). The element's href is loaded like a lexicon candidate (visit(): same site, no
        checkout, no cart-action URL); one without a usable href ("#", a script, a button) is clicked once and its
        navigation collected; under a blocking consent banner only links are offered (a GET, never a click under the
        banner; none: "consent_blocking", nothing asked). The page is then accepted by the stage's own test
        (accept_link)."""
        if not self.jev or self.fallbacks.get(stage):
            return None
        asked = start
        url = start.get("final_url") or start.get("url") or ""
        reload = not here and _url(self.tab.value("location.href")) != _url(url)
        if len(self.pages) + 1 + reload > self.settings.max_pages:  # no page for it: the caller's reason stands
            self.ask(start, None, stage, GOALS[stage], refused="max_pages")
            return None
        if reload:
            try:
                asked = self.tab.load(url, stage="extra", page_id=self.page_id("extra"))
            except (TimeoutError, RuntimeError):
                self.tab.lost()
                return None
            self.keep(asked, stage)
            asked.setdefault("notes", []).append(f"reloaded for the Jev fallback ({stage})")
            if failed_navigation(asked):
                self.errored(asked, stage, "the reload")
                return None
            self.close_overlays(asked, "extra")
        blocked = blocking_banner(self.tab.fresh_audit() or _audit(asked))
        self.tab.value("window.scrollTo(0, 0), scrollY")
        page = self.tab.observe()
        action, probe = self.ask(asked, page, stage, GOALS[stage], self.link_offer(stage, asked, blocked))
        if blocked and probe["reason"] == "no_click_actions":
            probe["reason"] = "consent_blocking"
        if action is None:
            return None
        here_url = page.get("url") or url
        href = str(probe.get("href") or "").strip()
        target = _url(href, here_url)
        if href and target is None and not href.lower().startswith("javascript:"):  # mailto:, tel:, ...: no page
            probe["reason"] = "guard_refused:url"
            return None
        if target is not None and target != _url(here_url):  # "#" resolves to the page itself: clicked instead
            cleaned, dropped = clean_cart_url(target)
            if cleaned is None or (dropped and stage != "cart"):  # a GET that could change the cart is never loaded
                probe["reason"] = "guard_refused:url"
                return None
            probe["url"] = target = cleaned  # the cart link without its cart-action keys (cart_url())
            record = self.visit(stage, target)
            if record is None:
                probe["reason"] = self.last_error
                return None
            probe["executed"] = True
        else:  # max_pages has room for its page (checked above); the offer had no such element under a banner
            origin = self.tab.value("performance.timeOrigin")
            executed, reason = self.tab.act(action, page, _type(asked), purpose=f"open_{stage}",
                                            stage=start.get("stage") or stage, origin=origin, note="jev_fallback")
            probe.update(executed=executed, reason=reason)
            if not executed:
                return None
            probe["effect"] = self.tab.await_effect(origin, expect_navigation=True,
                                                    timeout=self.settings.settle_timeout_s)
            if probe["effect"] != "navigated":
                probe["reason"] = "no_navigation"
                return None
            try:
                record = self.after_click(stage)
            except Stop as error:
                if error.reason == "bot_challenge":
                    raise
                if error.reason == "timeout":  # a stuck tab: the next load opens a new one (as visit() does)
                    self.tab.lost()
                probe["reason"] = error.reason  # a failed navigation: the stage keeps the caller's reason
                return None
            target = _url(record.get("final_url"))
            if target is None or clean_cart_url(target) != (target, []) or not self.guard.same_site(target):
                target = None  # no repeat through a GET of that URL
        if self.accept_link(stage, record, target, home):
            return record
        probe["reason"] = "not_found"  # the page is kept as "extra" with the reason in its notes
        return None

    def link_offer(self, stage: str, record: PageRecord, blocked: bool):
        """accept(action, page) for a link lookup's offer: under a blocking consent banner (blocked) only a link to
        another page (loaded with a GET); for the cart never a control the lexicon reads as adding, buying now,
        removing or checking out, nor a link to a checkout URL (cart_url()'s rule), so a "buy now" that adds and
        jumps to the checkout is never offered."""
        no_action = unlabelled(self.tab.lexicon(record), "add_to_cart", "buy_now", "remove", "checkout")

        def accept(action: dict, page: dict) -> bool:
            href = _href(page, action)
            target = _url(href, page.get("url") or "") if href else None
            if blocked and (target is None or target == _url(page.get("url"))):
                return False
            if stage == "cart":
                return no_action(action, page) and not (target and self.guard.is_checkout({"url": target}, None))
            return True
        return accept

    def add_offer(self, record: PageRecord, audit: dict):
        """accept(action, page) for the add-to-cart lookup's offer: never a control inside a product card's CTA (a
        related product's "Aggiungi"), one the lexicon reads as buying now, checking out or removing, nor a link to
        another page whose GET changes no cart (header cart link, breadcrumbs); a cart-action link stays."""
        in_card = own_controls([c for c in audit.get("ctas") or [] if isinstance(c, dict) and c.get("in_card")], audit)
        named = unlabelled(self.tab.lexicon(record), "buy_now", "checkout", "remove")

        def accept(action: dict, page: dict) -> bool:
            if in_card(action, page) or not named(action, page):
                return False
            href = _href(page, action)
            target = _url(href, page.get("url") or "") if href else None
            return target is None or target == _url(page.get("url")) or clean_cart_url(target) != (target, [])
        return accept

    def accept_link(self, stage: str, record: PageRecord, url: str | None, home: PageRecord | None) -> bool:
        """The page Jev's element led to, accepted by the same test as a lexicon candidate: a listing that is not the
        home page (repeats, overlays closed), a product page (unavailable: kept as the product page, as the last
        candidate is), a cart page; else demoted to "extra". url: repeats reload it (None: no repeats)."""
        if stage == "plp" and home is not None and landed_home(record, home):
            record["stage"] = "extra"
            record.setdefault("notes", []).append("plp candidate rejected: it landed on the home page")
            return False
        if not {"plp": is_listing, "pdp": is_product, "cart": is_cart}[stage](record):
            self.demote(record, stage)
            return False
        if stage == "pdp" and (signal := unavailable(_audit(record), self.tab.lexicon(record))):
            record.setdefault("notes", []).append(f"pdp candidate unavailable: {signal}")
        if stage != "cart" and url:
            self.repeat(record, stage, url)
        if stage == "plp":
            self.close_overlays(record, "plp")
        return True

    # ---------------------------------------------------------------- funnel
    def run(self) -> None:
        try:
            home = self.home()
            listing = self.listing(home) if "plp" in self.wanted else None
            if "pdp" not in self.wanted:
                return
            product = self.product(listing, home)
            if "cart" not in self.wanted:
                return
            cart = self.cart(product)
            if "checkout_entry" in self.wanted:
                self.checkout(cart)
        except Stop as error:
            self.stop(error)
        except (RuntimeError, TimeoutError) as exc:  # a session lost or a browser stuck mid-step
            if isinstance(exc, RuntimeError) and not session_gone(exc):
                raise
            self.tab.lost()
            stage = next((s for s in self.wanted if not any(p["stage"] == s for p in self.pages)), self.wanted[-1])
            self.stop(Stop(stage, "timeout" if isinstance(exc, TimeoutError) else "renderer_crashed"))
        finally:
            for stage in STAGES[len(self.wanted):]:
                self.mark(stage, "not_requested")

    def home(self) -> PageRecord:
        """The landing page: the guard re-anchored where the start URL landed (landed_guard), repeats, then its
        interrupting overlays closed, the consent policy applied and the search probe run."""
        record = self.visit("home", self.settings.url)
        if record is None:
            raise Stop("home", self.last_error)
        self.guard = self.tab.guard = landed_guard(self.guard, record, self.settings.locale)
        self.repeat(record, "home", self.settings.url)
        closed = self.close_overlays(record, "home")
        current = self.tab.after_dismiss if any(c["executed"] for c in closed) else None
        record["consent"] = self.tab.consent({**record, "audit": current or _audit(record)}, self.settings.consent,
                                             stage="home")
        # a close the banner kept from being sent (covered, stale, not observed) is tried again once it is handled,
        # for those overlays only; a close that was sent (with or without effect) or may have been delivered never is
        unsent = {c["key"] for c in closed if str(c.get("reason")).startswith(UNSENT)}
        if record["consent"].get("clicks") and unsent:
            fresh = self.tab.fresh_audit()
            self.close_overlays(record, "home", {**fresh, "overlays": [
                o for o in fresh.get("overlays") or [] if overlay_key(o) in unsent]})
        probes = record.setdefault("probes", {})
        if record["consent"].get("choice") == "none" and record["consent"].get("blocking"):
            probes["search_autocomplete"] = {"ran": False, "reason": "consent_blocking"}
        else:
            probes["search_autocomplete"] = self.tab.probe_search(record, stage="home")
        return record

    def listing(self, home: PageRecord) -> PageRecord | None:
        """The first category link that leads to a listing; none: Jev's pick on the home page (fallback_link), else
        plp is not assessable and products come from home. A link that lands on the home page (an empty or seasonal
        category redirected to "/") is no listing, however the home page is classified: the next candidate is
        tried."""
        loaded = False
        for url in listing_candidates(home, self.guard, self.tab.lexicon(home))[:CANDIDATES]:
            record = self.visit("plp", url)
            if record is None:
                continue
            loaded = True
            if landed_home(record, home):
                record["stage"] = "extra"
                record.setdefault("notes", []).append("plp candidate rejected: it landed on the home page")
                continue
            if is_listing(record):
                self.repeat(record, "plp", url)
                self.close_overlays(record, "plp")
                return record
            self.demote(record, "plp")
        reason = "not_found" if loaded or not self.counts.get("plp") else self.last_error
        if (record := self.fallback_link("plp", home, home=home)) is not None:
            return record
        self.mark("plp", reason)
        return None

    def product(self, listing: PageRecord | None, home: PageRecord) -> PageRecord:
        """The first candidate that is a buyable product page. Unavailable ones (unavailable()) are kept as "extra";
        when every product candidate is unavailable, the first one is the product page (PDP KPIs are still measured;
        add_to_cart() then stops with "out_of_stock" before any click). No product page at all: Jev's pick on the
        listing (else home) page (fallback_link)."""
        urls = product_candidates(listing, self.guard) + product_candidates(home, self.guard)
        self.last_error = "not_found"
        fallback = None
        for url in list(dict.fromkeys(urls))[:CANDIDATES]:
            record = self.visit("pdp", url)
            if record is None:
                continue
            if not is_product(record):
                self.demote(record, "pdp")
                continue
            signal = unavailable(_audit(record), self.tab.lexicon(record))
            if signal is None:
                self.repeat(record, "pdp", url)
                return record
            record["stage"] = "extra"
            record.setdefault("notes", []).append(f"pdp candidate unavailable: {signal}")
            fallback = fallback or (record, url)
        if fallback is not None:
            record, url = fallback
            record["stage"] = "pdp"
            self.repeat(record, "pdp", url)
            return record
        reason = self.last_error
        if (record := self.fallback_link("pdp", listing or home, home=home)) is not None:
            return record
        raise Stop("pdp", reason, then="pdp_not_found")

    def cart(self, product: PageRecord) -> PageRecord:
        """Add to cart, then the cart page: the page the click opened, else the cart link's page (cart_url), else
        Jev's pick on the page the tab shows (fallback_link)."""
        current = product
        if self.add_to_cart(product) == "navigated":  # e.g. a shop that opens the cart after adding
            record = self.after_click("cart")
            if is_cart(record):  # the same acceptance as a cart URL's page: its cart link is that page again
                return self.contents(product, record)
            self.demote(record, "cart")
            current = record
        url = self.cart_url(product)
        if url is None:
            if (record := self.fallback_link("cart", current, here=True)) is not None:
                return self.contents(product, record)
            raise Stop("cart", "not_found", then="cart_not_found")
        record = self.visit("cart", url)
        if record is None:
            raise Stop("cart", self.last_error, then="cart_not_reached")
        if not is_cart(record):  # a "#" mini-cart link, a login redirect: no cart page
            self.demote(record, "cart")
            raise Stop("cart", "not_found", then="cart_not_found")
        return self.contents(product, record)

    @staticmethod
    def contents(product: PageRecord, cart: PageRecord) -> PageRecord:
        """An executed add-to-cart click is no proof that something was added: the cart lines say it. item_recognised:
        a cart line names the item added (deception.same_item with its title); None when no line title or no item
        title could be read."""
        from .deception import same_item  # deception imports this module

        probe = (product.get("probes") or {}).get("add_to_cart")
        if probe is not None:
            lines = (_audit(cart).get("cart") or {}).get("line_items") or []
            titles = [line["title"] for line in lines if isinstance(line, dict) and line.get("title")]
            probe.update(cart_lines=len(lines), cart_empty=cart_empty(_audit(cart)), item_recognised=any(
                same_item(title, probe.get("title")) for title in titles) if titles and probe.get("title") else None)
        return cart

    def add_to_cart(self, product: PageRecord) -> str:
        """Click the observed add-to-cart control once (after picking a required variant). Returns the click's effect
        ("navigated", "quiet", "timeout"). A control audit.js did not find is a Jev lookup when the fallback is on
        (no lexicon pick then: a related product's "Aggiungi al carrello" is not this product's control), asked by
        the product's price (the add-to-cart sits by it) over add_offer()."""
        self.say(f"{self.profile}: add to cart")
        probe: dict = {"executed": False, "page_id": product["page_id"], "url": product.get("final_url")}
        product.setdefault("probes", {})["add_to_cart"] = probe
        lx = self.tab.lexicon(product)
        if signal := unavailable(_audit(product), lx):  # sold out at load (or a fallback the tab has left): no click
            probe.update(reason="out_of_stock", signal=signal)
            raise Stop("cart", "out_of_stock")
        self.room("cart")  # no click for a page there is no room for
        audit, cleared = self.tab.clear_way(product, stage="pdp")
        probe["overlays"] = cleared
        if blocking_banner(audit):  # never click under a consent banner that blocks the page
            probe["reason"] = "consent_blocking"
            raise Stop("cart", "consent_blocking")
        if signal := unavailable(audit, lx):  # sold out since the page was loaded: no click on a dead control
            probe.update(reason="out_of_stock", signal=signal)
            raise Stop("cart", "out_of_stock")
        audit = self.select_variant(product, audit, probe)
        pdp = audit.get("pdp") or {}
        control = pdp.get("add_to_cart") or {}
        price = pdp.get("price") or {}
        probe.update(title=pdp.get("title") or (pdp.get("structured") or {}).get("name"), price=price.get("value"),
                     currency=price.get("currency"))
        present = bool(control.get("present"))
        if not present and not self.jev:
            probe["reason"] = "add_to_cart_not_found"
            raise Stop("cart", "add_to_cart_failed")
        rect = control.get("rect") or (None if present else price.get("rect"))  # Jev is asked by the price
        clicked = self.tab.click(product, purpose="add_to_cart", stage="pdp", labels=[control.get("label")] if present
                                 else (), key="add_to_cart" if present else None, rect=rect, clear=True,
                                 offer=self.add_offer(product, audit) if self.jev else None)
        probe.update(executed=clicked["executed"], reason=clicked["reason"], label=clicked.get("label"))
        probe["overlays"] += clicked.get("cleared") or []
        if clicked.get("stale_reobserved"):
            probe["stale_reobserved"] = 1
        if clicked.get("jev_fallback"):
            probe["jev_fallback"] = True
        if not clicked["executed"]:
            if not present and clicked["reason"] == "control_not_found" and not clicked.get("jev_fallback"):
                probe["reason"] = "add_to_cart_not_found"  # neither audit.js nor Jev found one
            raise Stop("cart", "consent_blocking" if clicked["reason"] == "consent_blocking" else "add_to_cart_failed")
        probe["effect"] = self.tab.await_effect(clicked["origin"], expect_navigation=False, timeout=8.0)
        return probe["effect"]

    def select_variant(self, product: PageRecord, audit: dict, probe: dict) -> dict:
        """A variant group with no selected option (audit.js: selected false) gets its first available option, one
        observed click or select, never retried; the audit is then read again (the price may change). Jev's lookup
        (the fallback on, a click the lexicon missed) is offered only the controls inside that option's audit.js rect
        (own_controls; no rect: nothing offered, nothing asked)."""
        groups = (audit.get("pdp") or {}).get("variant_groups") or []
        group = next((g for g in groups if isinstance(g, dict) and g.get("selected") is False), None)
        if group is None:
            return audit
        option = group.get("first_available")
        variant = probe["variant"] = {"group": group.get("label"), "label": option, "executed": False}
        if not option:
            variant["reason"] = "no_available_option"
            raise Stop("cart", "variant_required")
        kinds = ("select",) if group.get("kind") == "select" else ("click",)
        clicked = self.tab.click(product, purpose="select_variant", stage="pdp", labels=[option], kinds=kinds,
                                 rect=group.get("rect"), clear=True,
                                 offer=own_controls([group], audit) if self.jev else None)
        variant.update(executed=clicked["executed"], reason=clicked["reason"])
        if clicked.get("jev_fallback"):
            variant["jev_fallback"] = True
        probe["overlays"] += clicked.get("cleared") or []
        if not clicked["executed"]:
            raise Stop("cart", "consent_blocking" if clicked["reason"] == "consent_blocking" else "variant_required")
        self.tab.await_effect(clicked["origin"], expect_navigation=False, timeout=5.0)
        return self.tab.fresh_audit() or audit

    def cart_url(self, product: PageRecord) -> str | None:
        """The cart link audit.js found (now, else at load), else an observed link labelled as the cart (not one that
        adds or removes: "Aggiungi al carrello", "Rimuovi dal carrello"); never the product page itself (a "#"
        mini-cart toggle resolves to it), never a URL whose GET changes the cart: its cart-action query keys are
        dropped and a cart-action path is skipped (clean_cart_url). A candidate without a query comes first."""
        base = product.get("final_url") or product.get("url") or ""
        hrefs = []
        for audit in (self.tab.fresh_audit(), _audit(product)):
            link = (audit.get("nav") or {}).get("cart_link") or {}
            if link.get("present"):
                hrefs.append(link.get("href"))
        page = self.tab.observe() or {}
        lx = self.tab.lexicon(product)
        for action in page.get("actions") or []:
            label = " ".join(str(action.get("label") or "").split())
            if action.get("role") == "link" and "cart" in lx and lx["cart"].search(label) and not any(
                    key in lx and lx[key].search(label) for key in ("add_to_cart", "buy_now", "remove")):
                hrefs.append(_href(page, action))
        here = {_url(u) for u in (base, product.get("url"), page.get("url")) if u}
        urls = []
        for href in hrefs:
            url = _url(href, page.get("url") or base)
            url = clean_cart_url(url)[0] if url else None
            if url and url not in here and self.guard.same_site(url) and not self.guard.is_checkout({"url": url}, None):
                urls.append(url)
        return min(urls, key=lambda u: bool(urlsplit(u).query), default=None)

    def checkout(self, cart: PageRecord) -> None:
        if self.guard.should_stop(_type(cart)):
            raise Stop("checkout_entry", "checkout_boundary")
        loaded = _audit(cart)
        if (loaded.get("doc") or loaded.get("ctas")) and cart_empty(loaded):  # an unread page proves nothing
            raise Stop("checkout_entry", "empty_cart")
        self.room("checkout_entry")  # no click for a page there is no room for
        self.say(f"{self.profile}: checkout entry")
        audit, cleared = self.tab.clear_way(cart, stage="cart")
        probe: dict = {"executed": False, "overlays": cleared}
        cart.setdefault("probes", {})["checkout_entry"] = probe
        if blocking_banner(audit):
            probe["reason"] = "consent_blocking"
            raise Stop("checkout_entry", "consent_blocking")
        effect = self.guest_path(cart, audit, probe)
        if effect == "navigated":
            probe.update(executed=True, via="guest", effect="navigated", label=probe["guest"].get("label"))
            self.entered()
            return
        if effect == "timeout" or str(probe.get("guest", {}).get("reason")).startswith(DELIVERED):
            raise Stop("checkout_entry", "no_navigation" if effect else "checkout_click_failed")  # page state unknown
        if effect == "quiet":  # a guest toggle on the cart page: the checkout CTA follows
            audit = self.tab.fresh_audit() or audit
        control = (audit.get("cart") or {}).get("checkout_cta") or {}
        if not control.get("present"):
            control = {}  # audit.js saw none: any observed control that the lexicon reads as "checkout"
        offer = unlabelled(self.tab.lexicon(cart), "login", "register", "remove", "add_to_cart") if self.jev else None
        clicked = self.tab.click(cart, purpose="checkout_entry", stage="cart", labels=[control.get("label")],
                                 key="checkout", rect=control.get("rect"), clear=True, offer=offer)
        probe.update({k: v for k, v in clicked.items() if k not in ("origin", "cleared")}, via="checkout_cta")
        probe["overlays"] += clicked.get("cleared") or []
        if not clicked["executed"]:
            raise Stop("checkout_entry", {"control_not_found": "checkout_cta_not_found",
                                          "consent_blocking": "consent_blocking"}.get(clicked["reason"],
                                                                                      "checkout_click_failed"))
        effect = self.tab.await_effect(clicked["origin"], expect_navigation=True,
                                       timeout=self.settings.settle_timeout_s)
        probe["effect"] = effect
        if effect != "navigated":
            raise Stop("checkout_entry", "no_navigation")
        record = self.entered()
        if clicked.get("jev_fallback") and not self.arrived(record):  # Jev's control led elsewhere: no checkout
            self.demote(record, "checkout_entry")
            jev = next(p for p in reversed(cart["probes"]["jev_fallback"]) if p.get("purpose") == "checkout_entry")
            jev["reason"] = probe["reason"] = "not_found"
            raise Stop("checkout_entry", "checkout_cta_not_found")

    def entered(self) -> PageRecord:
        """The page the checkout click opened, kept as checkout_entry; the lexicon's control (its label is the
        evidence) keeps whatever page it opened, with a note when it is not classified as a checkout."""
        record = self.after_click("checkout_entry")
        if not self.guard.should_stop(_type(record)):
            record.setdefault("notes", []).append(f"checkout entry classified as {_type(record)}")
        return record

    def arrived(self, record: PageRecord) -> bool:
        """The page Jev's checkout control opened is the checkout entry: the checkout page type, a checkout URL, or
        an account gate (forms.login_required: a checkout that asks for an account first)."""
        kind = _type(record)
        return self.guard.should_stop(kind) or self.guard.is_checkout({"url": record.get("final_url") or ""}, kind) \
            or bool((_audit(record).get("forms") or {}).get("login_required"))

    def guest_path(self, cart: PageRecord, audit: dict, probe: dict) -> str | None:
        """When the cart offers a guest path (forms.guest_option), take it as a first-time buyer would: one observed
        control the lexicon reads as "guest" (a link only when it leads to a same-site checkout URL, so an order
        tracking link for guests is never taken). The click goes to such a control only, never to another control
        with its label (an earlier off-site "Continua come ospite" link); probe["guest"]["href"] is the href of the
        control clicked. Its effect ("navigated", "quiet", "timeout"), or None when no such control was clicked;
        recorded in probe["guest"]. The checkout entry it reaches is rule d's evidence."""
        if not (audit.get("forms") or {}).get("guest_option"):
            return None
        lx = self.tab.lexicon(cart)

        def qualifies(action: dict, page: dict) -> bool:
            label = " ".join(str(action.get("label") or "").split())
            if action.get("kind") != "click" or "guest" not in lx or not lx["guest"].search(label):
                return False
            href = _href(page, action)
            if href is None:
                return True
            url = _url(href, page.get("url") or "")
            return bool(url and self.guard.same_site(url) and self.guard.is_checkout({"url": url}, None))

        page = self.tab.observe() or {}
        labels = [str(a.get("label") or "") for a in page.get("actions") or [] if qualifies(a, page)]
        if not labels:
            probe["guest"] = {"executed": False, "reason": "control_not_found"}
            return None
        clicked = self.tab.click(cart, purpose="guest_checkout", stage="cart", labels=labels[:1], accept=qualifies,
                                 clear=True)
        probe["guest"] = {k: v for k, v in clicked.items() if k not in ("origin", "cleared")}
        probe["overlays"] += clicked.get("cleared") or []
        if not clicked["executed"]:
            return None
        effect = self.tab.await_effect(clicked["origin"], expect_navigation=False,
                                       timeout=self.settings.settle_timeout_s)
        probe["guest"]["effect"] = effect
        return effect


def discover_funnel(collector, settings, *, profile: str, guard, progress=None,
                    jev_fallback: bool = False) -> tuple[list[PageRecord], list[NotAssessable]]:
    """Walk the funnel up to the deepest stage of settings.stages for one profile, inside collector's context.

    Returns (pages, not_assessable). Rejected listing or product candidates, failed loads and bot challenges are kept
    with stage "extra"; settings.repeats > 1 reloads the accepted home, listing and product pages
    (PageRecord.repeat_of). jev_fallback: Jev picks the element of a lookup the lexicon missed (module docstring;
    TYPESAFE_API_KEY needed); off, no request is ever made. An unexpected error ends the funnel with reason
    "error: ..." for the stages left. The tab is closed before returning.
    """
    crawl = Crawl(collector, settings, profile=profile, guard=guard, progress=progress, jev_fallback=jev_fallback)
    try:
        crawl.run()
    except Exception as exc:  # keep what was collected; the stages left get the error as their reason
        stage = next((s for s in crawl.wanted if not any(p["stage"] == s for p in crawl.pages)), crawl.wanted[-1])
        crawl.stop(Stop(stage, f"error: {type(exc).__name__}: {str(exc)[:160]}"))
    finally:
        crawl.tab.close()
    return crawl.pages, crawl.missing
