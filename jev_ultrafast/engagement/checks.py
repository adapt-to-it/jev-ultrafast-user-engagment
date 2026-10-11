"""Deterministic observations: every KPI of kpis.py whose producer is "checks" or "deception", from a run's data.

Rules (docs/engagement-kpi.md, section 3.5):
a. Page KPIs: one row per distinct page reached in the KPI's stages (any funnel stage when it has none); with
   settings.repeats > 1 a page and its repeats (PageRecord.repeat_of) give one row, the median of their numbers. Every
   stage of the KPI not reached in a profile gets a row with assessed=False, page_id None, the stage and the reason
   run["not_assessable"] records for it ("not_reached" when none). A page never gets a value it could not measure.
b. Site KPIs: one row per profile, page_id None, from the reached pages of their stages (evidence.stages_checked).
   A missing stage never makes a value worse: an "any" KPI with its own stages (PTI.SHIPPING_COST_PRE_CHECKOUT,
   PTI.VAT_STATED, TRI.JSONLD_*) that reads false while one of its stages gave no page is not assessed, with that
   stage's reason (evidence.stages_missing); true from any reached page stands. KPIs without stages (contacts, legal
   id) are site chrome on every page: false from the reached pages stands.
c. Comparisons need their base: PTI.FUNNEL_PRICE_DELTA and PTI.UNEXPLAINED_FEES need the product page and the cart;
   the checkout entry only extends the chain (a checkout page that cannot be read is skipped, evidence
   steps_skipped). An empty cart (crawler.cart_empty: no line and no total above zero, or the page says so) is no
   base, and neither are the cart KPIs that depend on its contents (CTA salience, delivery time, fees, shipping shown,
   editable lines): not assessed, "empty_cart". A cart with a total but no line audit.js could parse is not empty: its
   editable controls count when seen ("cart_lines_not_recognised" when not), and a checkout CTA audit.js did not
   recognise is "checkout_cta_not_found", never a salience of 0.
d. FAI.GUEST_CHECKOUT and FAI.FORCED_ACCOUNT (CONTRACTS "Checkout evidence"): when the checkout entry was reached and
   assessable it is the only evidence (guest = forms.guest_option or not forms.login_required, the account gate
   audit.js judges: a login box beside an address form is not a gate, a required registration password is; the
   password flags are evidence only); otherwise a cart with a guest control gives a row (guest true, forced false);
   neither: not assessed with the checkout stage's reason. Both aggregate with "any" (kpis.py): the checkout prevails
   and the two never both read true.
e. "not_applicable:*" only for what exists and does not apply (no variants, no strikethrough price, no form fields, a
   product that cannot be bought: crawler.unavailable() gives FAI.PDP_CTA_ABOVE_FOLD and the product-page
   CCL.CTA_SALIENCE "not_applicable:out_of_stock"; the load timings of a document that was not a cold navigation:
   "not_applicable:speculative_navigation" for one the shop's speculation rules fetched before the click,
   "not_applicable:same_document" for the document of an earlier record). An add-to-cart disabled only until a
   variant is picked counts as enabled for FAI.PDP_CTA_ABOVE_FOLD (evidence disabled_until_variant): the KPI is about
   reaching it without scrolling.
f. Producer-dependent scope: PTI.STRIKETHROUGH_LOWEST30 compares struck prices and lowest-30-days statements inside
   and outside product cards only when audit.js marks both (in_card); without the marks one statement on the page
   is enough (evidence statement_scope "unknown"), since a card statement and a page footnote cannot be told apart.
DPR confidences come from run["deception"][profile] (deception.py), one row per profile on the page the test is about;
a test whose page was not reached carries the stage's own not-assessable reason.
Units follow kpis.py and anchors.json: KB = 1000 bytes, % = 0..100; counts stay integers. Pages with stage "extra" are
not funnel pages.
"""

import math
import re
import statistics
from functools import lru_cache
from urllib.parse import urlsplit

from .crawler import cart_empty, unavailable, variant_gated
from .deception import same_item
from .kpis import KPI_LIST, Kpi
from .lexicon import compile_lexicon, lexicon_for
from .schemas import STAGES, Observation, PageRecord

CHECKS_VERSION = "checks.v2"  # v2: above the fold read at the element's centre (audit.js fold)
PRODUCERS = ("checks", "deception")
MIN_PROSE_WORDS = 30  # readability of fewer words is noise (listings and carts are mostly labels)
LARGE_ASSORTMENT = 24  # CCL.CHOICE_SUPPORT: the editorial threshold of the catalogue
PAGINATION = ("pagination", "load_more", "infinite", "none")
PAINT = ("fcp", "lcp", "cls", "tbt_approx", "loaf_count")
SALIENT_AREA = 10_000  # CCL.CTA_SALIENCE: CSS px² of a fully prominent control (about 225x44)
# a tax line of a cart summary ("Estimated tax", "IVA 22%", "MwSt.") is no seller fee: never an unexplained fee. The
# lexicon's "tax_line" key wins when it has one; a customs or duty charge ("Dazi e tasse doganali") is no tax line.
TAX_LINE = re.compile(r"\b(iva|vat|tax|taxes|sales\s+tax|imposta|imposte|tasse|mwst|ust|tva|btw|gst|hst)\b", re.I)
CUSTOMS_LINE = re.compile(r"\b(dazi|dazio|dogan\w*|customs|duty|duties|zoll\w*|douanes?|aduanas?)\b", re.I)
EMPTY_CART_BLIND = ("PTI.SHIPPING_COST_PRE_CHECKOUT",)  # site KPIs an empty cart page says nothing about


class NotAssessed(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _na(reason: str):
    raise NotAssessed(reason)


@lru_cache(maxsize=8)
def _lexicon(language: str) -> dict:
    return compile_lexicon(lexicon_for(language))


def _language(page: PageRecord) -> str:
    return (_audit(page).get("lexicon_lang") or _audit(page).get("lang") or "it")[:2].lower() or "it"


def _lx(page: PageRecord) -> dict:
    return _lexicon(_language(page))


def _tax_line(label: str, lx: dict) -> bool:
    if CUSTOMS_LINE.search(label):
        return False
    return bool((lx.get("tax_line") or TAX_LINE).search(label))


def _audit(page: PageRecord | None) -> dict:
    return (page or {}).get("audit") or {}


def _failed(page: PageRecord) -> None:
    """Chrome's own error page is not the shop's: nothing on it is measured."""
    if "navigation_error" in ((page.get("classification") or {}).get("signals") or []):
        _na("navigation_error")


def _dom(page: PageRecord) -> dict:
    """The page's audit, or not assessed when the navigation failed or audit.js could not run."""
    _failed(page)
    audit = _audit(page)
    if not audit.get("doc") and not audit.get("ctas"):
        _na("audit_unavailable")
    return audit


def _round(value, digits=1):
    return round(float(value), digits) if isinstance(value, (int, float)) and not isinstance(value, bool) else value


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _norm(text) -> str:
    return re.sub(r"\d+([.,]\d+)?", "#", " ".join(str(text or "").split()).casefold())


# ---------------------------------------------------------------- PERF (vitals, network, errors)
def _vital(key: str, digits: int = 1):
    """A load timing of the page's document. A document that was not a cold navigation has none to report: one the
    shop's speculation rules fetched before the click (vitals.speculative) or the document of an earlier record
    (vitals.soft_navigation) is "not_applicable", never a fast page."""
    def check(page):
        vitals = page.get("vitals") or {}
        if vitals.get("speculative"):
            _na("not_applicable:speculative_navigation")
        if vitals.get("soft_navigation"):
            _na("not_applicable:same_document")
        value = vitals.get(key)
        if value is None:
            if key in PAINT and vitals.get("visibility_state_at_load") not in (None, "visible"):
                _na("background_tab")
            _na("unavailable" if vitals else "vitals_unavailable")
        return (int(round(value)) if digits == 0 else _round(value, digits)), {}
    return check


def _network(key: str, scale: float | None = None, digits: int = 1):
    """A NetworkSummary number; scaled and rounded (KB, %) or, without scale, as recorded (counts stay integers)."""
    def check(page):
        network = page.get("network") or {}
        if network.get(key) is None:
            _na("network_unavailable")
        evidence = {"source": network.get("source")} if network.get("source") != "cdp" else {}
        return (network[key] if scale is None else _round(network[key] * scale, digits)), evidence
    return check


def _console_errors(page):
    errors = page.get("errors")
    if errors is None or errors.get("console") is None:
        _na("errors_unavailable")
    return errors["console"], {"samples": (errors.get("samples") or [])[:3]}


def _oversized(page):
    images = _dom(page).get("images") or {}
    count = images.get("oversized_count")
    return (count if count is not None else len(images.get("oversized") or [])), {}


# ---------------------------------------------------------------- FAI
def _filters(page):
    return _dom(page).get("filters") or {}


def _cta_above_fold(page):
    """The product's own add-to-cart, enabled, above the fold. A sold-out item has none to reach (not applicable); a
    control disabled only until a variant is picked is reachable (disabled_until_variant)."""
    audit = _dom(page)
    if unavailable(audit, _lx(page)):
        _na("not_applicable:out_of_stock")
    pdp = audit.get("pdp") or {}
    control = pdp.get("add_to_cart") or {}
    gated = control.get("enabled") is False and variant_gated(pdp)
    value = bool(control.get("present") and control.get("above_fold") and (control.get("enabled") is not False
                                                                          or gated))
    evidence = {"cta": control.get("label"), "rect_y": (control.get("rect") or {}).get("y")}
    return value, ({**evidence, "disabled_until_variant": True} if gated else evidence)


def _cart_editable(page):
    """Quantity or remove controls in the cart. audit.js finds them independently of the line rows, so on a cart with
    a total but no parsed line, controls seen count; none seen there is no evidence."""
    audit = _dom(page)
    cart = audit.get("cart") or {}
    if cart_empty(audit):
        _na("empty_cart")
    lines = len(cart.get("line_items") or [])
    if not lines and not cart.get("editable"):
        _na("cart_lines_not_recognised")
    return bool(cart.get("editable")), {"lines": lines}


def _forms(page):
    return _dom(page).get("forms") or {}


def _checkout_fields(page):
    visible = _forms(page).get("visible") or 0
    if not visible:
        _na("not_applicable:no_form_fields")
    return visible, {"required": _forms(page).get("required")}


def _share(key: str):
    def check(page):
        forms = _forms(page)
        if not forms.get("visible") or forms.get(key) is None:
            _na("not_applicable:no_form_fields")
        return _round(forms[key] * 100), {"fields": forms.get("visible")}
    return check


def _target_share(key: str):
    def check(page):
        targets = _dom(page).get("targets") or {}
        total = targets.get("interactive") or 0
        if not total:
            _na("not_applicable:no_interactive_targets")
        return _round(100 * (total - (targets.get(key) or 0)) / total), {"interactive": total, key: targets.get(key)}
    return check


def _a11y(page):
    a11y = _dom(page).get("a11y") or {}
    alt, lang = a11y.get("img_missing_alt") or 0, bool(a11y.get("lang_missing"))
    return alt + int(lang), {"img_missing_alt": alt, "lang_missing": lang}


def _interrupting(page) -> list[dict]:
    return [o for o in _dom(page).get("overlays") or [] if o.get("interrupting")]


def _overlay_count(page):
    overlays = _interrupting(page)
    return len(overlays), {"kinds": [o.get("kind") for o in overlays]}


def _overlay_coverage(page):
    overlays = _interrupting(page)
    return _round(100 * max([o.get("coverage") or 0 for o in overlays] or [0])), {"kinds": [o.get("kind") for o in
                                                                                         overlays]}


# ---------------------------------------------------------------- TRI
def _https(page):
    url = page.get("final_url") or page.get("url") or ""
    return urlsplit(url).scheme == "https", {"url": url[:200]}


def _reviews(page) -> dict:
    return (_dom(page).get("pdp") or {}).get("reviews") or {}


def _reviews_present(page):
    reviews = _reviews(page)
    present = reviews.get("count") or reviews.get("rating") is not None or bool(reviews.get("rating_text"))
    return bool(present), {"text": reviews.get("rating_text") or reviews.get("count_text")}


def _review_count(page):
    reviews = _reviews(page)
    if reviews.get("count") is not None:
        return int(reviews["count"]), {"source": reviews.get("source")}
    if reviews.get("rating") is not None or reviews.get("rating_text"):
        _na("review_count_unavailable")
    return 0, {}


def rating_band(rating: float) -> str:
    rating = round(rating, 1)
    if rating >= 4.8:
        return "4.8-5.0"
    if rating >= 4.0:
        return "4.0-4.7"
    if rating >= 3.5:
        return "3.5-3.9"
    return "<3.5"


def _rating_band(page):
    rating = _reviews(page).get("rating")
    if rating is None:
        _na("not_applicable:no_rating")
    if not 0 <= rating <= 5:
        _na("rating_scale_unknown")
    return rating_band(rating), {"rating": rating}


def _delivery(page):
    audit = _dom(page)
    if page.get("stage") == "cart" and cart_empty(audit):
        _na("empty_cart")  # an empty cart shows no delivery estimate to read
    text = (audit.get("pdp") or {}).get("delivery_text")
    return bool(text), {"text": str(text)[:160]} if text else {}


# ---------------------------------------------------------------- PTI
def _price_visible(page):
    price = (_dom(page).get("pdp") or {}).get("price") or {}
    return bool(price.get("value") is not None and price.get("above_fold")), {"text": price.get("text")}


def _price_match(page):
    pdp = _dom(page).get("pdp") or {}
    shown, structured = pdp.get("price") or {}, pdp.get("structured") or {}
    if structured.get("price") is None:
        _na("not_applicable:no_structured_price")
    if shown.get("value") is None:
        _na("not_applicable:no_visible_price")
    same_currency = not (shown.get("currency") and structured.get("currency")) or \
        shown["currency"] == structured["currency"]
    match = same_currency and abs(float(shown["value"]) - float(structured["price"])) <= 0.01
    return match, {"visible": shown.get("value"), "structured": structured.get("price"),
                   "currency": structured.get("currency")}


def strikethrough_ok(struck_main: int, struck_card: int, said_main: int, said_card: int) -> bool:
    """Omnibus art. 6a: every struck price needs its lowest-30-days statement. A statement outside the product cards
    (a page footnote) covers the cards; a card statement covers cards only; a struck price outside the cards needs a
    statement outside them."""
    return said_main >= struck_main and (struck_card == 0 or said_main >= 1 or said_card >= struck_card)


def _strikethrough(page):
    """Rule f. Every struck price needs a lowest-30-days statement; inside and outside the product cards are compared
    only when audit.js marks every struck price and statement with in_card (a statement's "count" is how many times
    its text was shown, default 1). Without the marks, one statement on the page is enough: audit.js merges equal
    texts, so per-card statements read as one, and a footnote cannot be told from a card's line. The struck prices
    are audit.js's price_counts, every price it read (`prices` keeps the first 80, statements included); a payload
    without them counts `prices`."""
    audit = _dom(page)
    tally = audit.get("price_counts") or {}
    if all(isinstance(tally.get(key), int) for key in ("strikethrough_in_card", "strikethrough_outside_cards")):
        struck = [{"in_card": card, "count": n} for card, n in ((False, tally["strikethrough_outside_cards"]),
                                                                (True, tally["strikethrough_in_card"])) if n > 0]
    else:
        struck = [p for p in audit.get("prices") or [] if p.get("strikethrough")]
    if not struck and (audit.get("pdp") or {}).get("strike_price"):
        struck = [{"in_card": False}]  # pdp.strike_price is the product's own (outside the cards)
    if not struck:
        _na("not_applicable:no_strikethrough_price")
    said = (audit.get("persuasion") or {}).get("lowest_price_30d") or []
    evidence = {"strikethrough_prices": sum(max(1, int(item.get("count") or 1)) for item in struck),
                "lowest_30d_statements": len(said)}
    if not said:
        return False, evidence
    if not all("in_card" in item for item in struck + said):
        return True, {**evidence, "statement_scope": "unknown"}

    def count(items, card: bool) -> int:
        return sum(max(1, int(item.get("count") or 1)) for item in items if bool(item.get("in_card")) is card)

    counts = [count(struck, False), count(struck, True), count(said, False), count(said, True)]
    return strikethrough_ok(*counts), {**evidence, "in_cards": {"strikethrough_prices": counts[1],
                                                                 "statements": counts[3]}}


# ---------------------------------------------------------------- CCL
def competing_ctas(ctas: list[dict]) -> list[dict]:
    """Primary-looking CTAs above the fold. The buttons of product cards repeat one action on every item: together
    they are one CTA (the first), not one per card."""
    shown = [c for c in ctas if c.get("primary_like") and c.get("above_fold")]
    return [c for c in shown if not c.get("in_card")] + [c for c in shown if c.get("in_card")][:1]


def cta_salience(control: dict, ctas: list[dict], viewport: dict) -> float:
    """0-100 composite of the stage's primary CTA (provisional, anchors editorial):

        salience = 100 * fold * (0.5 * C + 0.25 * A + 0.25 * U)
        C = clamp((contrast - 3) / 1.5)   text contrast: 3:1 (WCAG 1.4.11 minimum) 0, 4.5:1 (WCAG 1.4.3 AA) 1
        A = clamp(area / 10000 px²)       a control of about 225x44 CSS px or more is fully prominent (a fixed
                                          reference, so that a normal desktop CTA is not penalised by a wide viewport)
        U = 1 / n                         n = primary-looking CTAs above the fold (at least 1; product cards'
                                          buttons count once, competing_ctas): uniqueness
        fold = 1 above the fold, 0.5 below
    A unique above-fold AA button of 200x44 scores 97. A page without the CTA scores 0. viewport is kept for
    callers and evidence; the reference area does not depend on it.
    """
    if not control.get("present"):
        return 0.0
    contrast = control.get("contrast") or 1.0
    rect = control.get("rect") or {}
    area = control.get("area") or (rect.get("w") or 0) * (rect.get("h") or 0)
    competing = len(competing_ctas(ctas))
    score = 0.5 * _clamp((contrast - 3) / 1.5) + 0.25 * _clamp(area / SALIENT_AREA) + 0.25 / max(1, competing)
    return round(100 * (1.0 if control.get("above_fold") else 0.5) * score, 1)


def _salience(page):
    audit = _dom(page)
    if page.get("stage") == "cart":
        if cart_empty(audit):
            _na("empty_cart")  # no checkout CTA because nothing is in the cart: not a measurement
        control = (audit.get("cart") or {}).get("checkout_cta") or {}
        if not control.get("present"):
            _na("checkout_cta_not_found")  # a CTA audit.js did not recognise is a parse miss, not a salience of 0
    else:
        if unavailable(audit, _lx(page)):
            _na("not_applicable:out_of_stock")  # a sold-out item has no buying CTA to make salient
        pdp = audit.get("pdp") or {}
        control = pdp.get("add_to_cart") or {}
        if not control.get("present"):
            control = pdp.get("buy_now") or {}
    value = cta_salience(control, audit.get("ctas") or [], audit.get("viewport") or {})
    return value, {"cta": control.get("label"), "contrast": control.get("contrast"),
                   "above_fold": control.get("above_fold")}


def _primary_count(page):
    """Competing primary CTAs above the fold (competing_ctas: the buttons of product cards count once)."""
    ctas = _dom(page).get("ctas") or []
    labels = [c.get("label") for c in competing_ctas(ctas)]
    cards = sum(1 for c in ctas if c.get("primary_like") and c.get("above_fold") and c.get("in_card"))
    return len(labels), {"labels": labels[:6], **({"card_buttons": cards} if cards else {})}


def _choice_support(page):
    audit = _dom(page)
    products, filters = audit.get("products") or {}, audit.get("filters") or {}
    cards = max(products.get("main_group") or 0, products.get("cards_count") or 0)
    if cards < LARGE_ASSORTMENT:
        _na("not_applicable:small_assortment")
    supported = (filters.get("controls") or 0) > 0 or bool((filters.get("sort") or {}).get("present"))
    return supported, {"cards": cards, "filters": filters.get("controls"),
                       "sort": bool((filters.get("sort") or {}).get("present"))}


def syllables(word: str) -> int:
    """English syllable estimate: vowel groups, a silent final "e" dropped, at least one."""
    groups = len(re.findall(r"[aeiouy]+", word.lower()))
    if word.lower().endswith("e") and not word.lower().endswith(("le", "ee")) and groups > 1:
        groups -= 1
    return max(1, groups)


def readability(doc: dict, language: str) -> tuple[float, dict]:
    """Gulpease for Italian: 89 - 10 * letters/words + 300 * sentences/words (Lucisano and Piemontese).
    Flesch reading ease for English: 206.835 - 1.015 * words/sentences - 84.6 * syllables/words; syllables per word
    are estimated on the page's text sample (doc.syllable_count when audit.js provides it). Both clamped to 0-100."""
    words, sentences, letters = doc.get("word_count") or 0, doc.get("sentence_count") or 0, doc.get("letter_count")
    if words < MIN_PROSE_WORDS:
        _na("not_applicable:little_prose")
    sentences = max(1, sentences)
    if language == "it":
        if letters is None:
            _na("unavailable")
        return round(max(0.0, min(100.0, 89 - 10 * letters / words + 300 * sentences / words)), 1), {
            "index": "gulpease", "words": words, "sentences": sentences, "letters": letters}
    if language == "en":
        if doc.get("syllable_count"):
            per_word = doc["syllable_count"] / words
        else:
            sample = re.findall(r"[A-Za-z]+", doc.get("text_sample") or "")
            if not sample:
                _na("unavailable")
            per_word = sum(syllables(w) for w in sample) / len(sample)
        score = 206.835 - 1.015 * words / sentences - 84.6 * per_word
        return round(max(0.0, min(100.0, score)), 1), {"index": "flesch", "words": words, "sentences": sentences,
                                                        "syllables_per_word": round(per_word, 3)}
    _na("unsupported_language")


def _readability(page):
    audit = _dom(page)
    language = (audit.get("lexicon_lang") or audit.get("lang") or "")[:2].lower()
    return readability(audit.get("doc") or {}, language)


def _headings(page):
    headings = (_dom(page).get("doc") or {}).get("headings") or {}
    return headings.get("h1") == 1 and (headings.get("h2") or 0) >= 1, {"h1": headings.get("h1"),
                                                                        "h2": headings.get("h2")}


def visual_complexity(visual: dict) -> float:
    """0-100 composite from the screenshot (provisional, anchors editorial; Reinecke et al. 2013 feature families):
    100 * (0.40 * clamp(edge_density / 0.25) + 0.35 * clamp(bytes_per_px / 0.40) + 0.25 * clamp(colorfulness / 100)).
    Edges and compressibility carry the weight: Hasler-Suesstrunk colorfulness predicts perceived colorfulness, and is
    only a weak proxy of complexity."""
    return round(100 * (0.40 * _clamp((visual.get("edge_density") or 0) / 0.25)
                        + 0.35 * _clamp((visual.get("bytes_per_px") or 0) / 0.40)
                        + 0.25 * _clamp((visual.get("colorfulness") or 0) / 100)), 1)


def _visual(page):
    visual = page.get("visual") or {}
    if visual.get("edge_density") is None:
        _na("no_screenshot")
    keys = ("colorfulness", "edge_density", "bytes_per_px", "method")  # method: the screenshot re-encoding
    return visual_complexity(visual), {k: visual.get(k) for k in keys if k != "method" or visual.get(k)}


PAGE_CHECKS = {
    "PERF.TTFB": _vital("ttfb"),
    "PERF.FCP": _vital("fcp"),
    "PERF.LCP": _vital("lcp"),
    "PERF.CLS": _vital("cls", 4),
    "PERF.TBT_APPROX": _vital("tbt_approx"),
    "PERF.LOAF_COUNT": _vital("loaf_count", 0),
    "PERF.BYTES_TOTAL": _network("bytes_transfer", 1 / 1000),
    "PERF.BYTES_JS": _network("bytes_js", 1 / 1000),
    "PERF.REQUESTS": _network("requests"),
    "PERF.THIRD_PARTY_SHARE": _network("third_party_share", 100),
    "PERF.IMG_OVERSIZED": _oversized,
    "PERF.CONSOLE_ERRORS": _console_errors,
    "PERF.HTTP_ERRORS": _network("http_errors"),
    "FAI.PLP_FILTERS": lambda page: (_filters(page).get("controls") or 0, {}),
    "FAI.PLP_SORT": lambda page: (bool((_filters(page).get("sort") or {}).get("present")), {}),
    "FAI.PLP_RESULT_COUNT": lambda page: (bool(_filters(page).get("result_count_text")),
                                          {"text": _filters(page).get("result_count_text")}),
    "FAI.PLP_PAGINATION": lambda page: (_filters(page).get("pagination") if _filters(page).get("pagination")
                                        in PAGINATION else "none", {}),
    "FAI.BREADCRUMBS": lambda page: (bool(((_dom(page).get("nav") or {}).get("breadcrumbs") or {}).get("present")),
                                     {}),
    "FAI.PDP_CTA_ABOVE_FOLD": _cta_above_fold,
    "FAI.PDP_VARIANT_SELECTOR": lambda page: ((_dom(page).get("pdp") or {}).get("variant_selector") or "none",
                                              {"variants": (_dom(page).get("pdp") or {}).get("variants")}),
    "FAI.CART_EDITABLE": _cart_editable,
    "FAI.CHECKOUT_FIELDS": _checkout_fields,
    "FAI.AUTOCOMPLETE_ATTRS": _share("autocomplete_share"),
    "FAI.TARGET_SIZE_24": _target_share("lt24"),
    "FAI.TARGET_SIZE_44": _target_share("lt44"),
    "FAI.A11Y_BASIC": _a11y,
    "FAI.OVERLAY_INTERRUPTIONS": _overlay_count,
    "FAI.OVERLAY_COVERAGE": _overlay_coverage,
    "TRI.HTTPS": _https,
    "TRI.MIXED_CONTENT": _network("mixed_content"),
    "TRI.REVIEWS_PRESENT": _reviews_present,
    "TRI.REVIEW_COUNT": _review_count,
    "TRI.RATING_BAND": _rating_band,
    "TRI.DELIVERY_TIME_STATED": _delivery,
    "PTI.PRICE_VISIBLE_PDP": _price_visible,
    "PTI.PRICE_JSONLD_MATCH": _price_match,
    "PTI.STRIKETHROUGH_LOWEST30": _strikethrough,
    "CCL.CTA_SALIENCE": _salience,
    "CCL.PRIMARY_CTA_COUNT": _primary_count,
    "CCL.CHOICE_SUPPORT": _choice_support,
    "CCL.READABILITY": _readability,
    "CCL.HEADINGS": _headings,
    "CCL.FORM_LABELS": _share("labels_share"),
    "CCL.VISUAL_COMPLEXITY": _visual,
}


# ---------------------------------------------------------------- site KPIs: (pages of the KPI's stages) -> value
def _any(test):
    def check(pages, view, profile):
        hits = [p["page_id"] for p in pages if test(p)]
        return bool(hits), {"found_on": hits[:5]}
    return check


def _max(measure):
    def check(pages, view, profile):
        values = [(measure(p), p["page_id"]) for p in pages]
        values = [(v, pid) for v, pid in values if v is not None]
        if not values:
            _na("unavailable")
        value, page_id = max(values, key=lambda item: item[0])
        return value, {"page_id": page_id}
    return check


def _search(page) -> dict:
    return _dom(page).get("search") or {}


def _search_width(pages, view, profile):
    widths = [(_search(p).get("width") or 0, p["page_id"]) for p in pages if _search(p).get("present")]
    widths = [w for w in widths if w[0] > 0]
    if not widths:
        _na("not_applicable:no_search_field")
    width, page_id = max(widths)
    return round(width), {"page_id": page_id}


def _autocomplete(pages, view, profile):
    probes = [((p.get("probes") or {}).get("search_autocomplete"), p["page_id"]) for p in pages]
    probes = [(probe, pid) for probe, pid in probes if probe]
    ran = [(probe, pid) for probe, pid in probes if probe.get("ran") and probe.get("options") is not None]
    if ran:
        probe, page_id = max(ran, key=lambda item: item[0]["options"])
        return probe["options"] > 0, {"page_id": page_id, **{k: probe.get(k) for k in
                                                             ("typed", "options", "latency_ms", "window_ms")}}
    if not probes:
        _na("probe_not_run")
    reason = probes[0][0].get("reason") or "probe_failed"
    _na("not_applicable:no_search_field" if reason == "no_search_field" else f"probe:{reason}")


def _contact(page):
    contact = (_dom(page).get("trust") or {}).get("contact") or {}
    return any(contact.get(k) for k in ("email", "phone", "address"))


def _structured(key):
    return lambda page: bool(((_dom(page).get("pdp") or {}).get("structured") or {}).get(key))


def _shipping_shown(page) -> bool:
    """A shipping cost stated on the page: the product page's shipping line, the cart's shipping row or value. A line
    that leaves the cost to the checkout ("Spedizione calcolata al checkout") states none."""
    audit, lx = _dom(page), _lx(page)
    cart = audit.get("cart") or {}

    def stated(text) -> bool:
        text = " ".join(str(text or "").split())
        deferred = "shipping_deferred" in lx and lx["shipping_deferred"].search(text) and not (
            "free_shipping" in lx and lx["free_shipping"].search(text))
        return bool(text) and not deferred

    return bool(stated((audit.get("pdp") or {}).get("shipping_text")) or stated(cart.get("shipping_text"))
                or cart.get("shipping_value") is not None)


def _persuasion(page, key) -> list[dict]:
    return (_dom(page).get("persuasion") or {}).get(key) or []


def _distinct(pages, key, keep=lambda item: True) -> list[str]:
    texts = {}
    for page in pages:
        for item in _persuasion(page, key):
            if keep(item) and item.get("text"):
                texts.setdefault(_norm(item["text"]), str(item["text"])[:160])
    return list(texts.values())


def _scarcity(pages, view, profile):
    texts = _distinct(pages, "scarcity")
    limited = any(((_dom(p).get("pdp") or {}).get("structured") or {}).get("availability") == "LimitedAvailability"
                  for p in pages)
    return len(texts) + int(limited), {"texts": texts[:5], "limited_availability": limited}


def _urgency(pages, view, profile):
    """Urgency with a declared deadline. A time text the countdown test saw standing still (a race time, a video
    duration) is no countdown."""
    static = {_norm(t)[:150] for t in (view.deception(profile).get("countdown") or {}).get("static") or []}
    texts = _distinct(pages, "urgency", lambda u: (u.get("deadline") or u.get("countdown"))
                      and not (u.get("countdown") and _norm(str(u.get("text") or "")[:160])[:150] in static))
    evidence = {"texts": texts[:5]}
    return len(texts), ({**evidence, "static_times": sorted(static)[:3]} if static else evidence)


@lru_cache(maxsize=8)
def _benefits(language: str) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.I) for p in lexicon_for(language).get("reciprocity") or [])


def benefit(text: str, patterns) -> str:
    """The reciprocity pattern a text matches (the longest match): one benefit worded several ways ("Reso gratuito",
    "Resi gratuiti entro 30 giorni") is one motivator. A text no pattern matches stands for itself."""
    best = None
    for index, pattern in enumerate(patterns):
        found = pattern.search(text)
        if found and (best is None or len(found.group(0)) > best[0]):
            best = (len(found.group(0)), index)
    return f"pattern:{best[1]}" if best else _norm(text)


def _reciprocity(pages, view, profile):
    """Distinct benefits offered for free (returns, gifts, samples, a first-order discount); the free-shipping
    threshold lives in PTI. As in audit.js, a block's free-shipping wording is left out before its benefit is read:
    "Spedizione gratuita sopra 49 € · Resi gratuiti" offers free returns, "Spedizione gratis sopra 49 €" nothing."""
    benefits: dict[str, str] = {}
    for page in pages:
        patterns, free_shipping = _benefits(_language(page)), _lx(page).get("free_shipping")
        for item in _persuasion(page, "reciprocity"):
            text = " ".join(str(item.get("text") or "").split())
            rest = free_shipping.sub(" ", text, count=1) if free_shipping else text
            if text and any(pattern.search(rest) for pattern in patterns):
                benefits.setdefault(benefit(rest, patterns), text[:160])
    texts = list(benefits.values())
    return len(texts), {"texts": texts[:5]}


def _tax_added(totals: dict, lx: dict) -> float:
    """Tax lines added on top of the subtotal and shipping (a US "Estimated tax"); an included tax ("di cui IVA",
    "VAT included") adds nothing."""
    tax = sum(fee.get("value") or 0 for fee in totals.get("fees") or [] if _tax_line(str(fee.get("label") or ""), lx)
              and not ("vat" in lx and lx["vat"].search(str(fee.get("label") or ""))))
    total, subtotal = totals.get("total_value"), totals.get("subtotal_value")
    if not tax or total is None or subtotal is None:
        return 0.0
    return tax if total - tax >= subtotal + (totals.get("shipping_value") or 0) - 0.01 else 0.0


def _funnel_delta(pages, view, profile):
    """Largest unexplained increase (%) of cart and checkout totals, minus their declared shipping and a tax added on
    top, over the price of the item added on the product page (times its cart quantity). Without a total the subtotal
    is compared as it is (it holds no shipping). Rule c: the cart is the base; a checkout page that cannot be read only
    shortens the chain (steps_skipped)."""
    product, cart, checkout = (view.first(profile, s) for s in ("pdp", "cart", "checkout_entry"))
    if product is None:
        _na(view.reason("pdp", profile))
    if cart is None:
        _na(view.reason("cart", profile))
    added = (product.get("probes") or {}).get("add_to_cart") or {}
    base = added.get("price") if added.get("price") is not None else ((_dom(product).get("pdp") or {}).get("price")
                                                                      or {}).get("value")
    if not base:
        _na("no_product_price")
    title = added.get("title") or (_dom(product).get("pdp") or {}).get("title")
    lines = (_dom(cart).get("cart") or {}).get("line_items") or []
    qty = next((line.get("qty") for line in lines if same_item(line.get("title"), title) and line.get("qty")), 1)
    steps, empty, skipped = [], 0, []
    for page in (cart, checkout):
        if page is None:
            continue
        try:
            audit = _dom(page)  # an unreadable cart ends the KPI
        except NotAssessed as na:
            if page is cart:
                raise
            skipped.append({"stage": page["stage"], "page_id": page.get("page_id"), "reason": na.reason})
            continue
        if cart_empty(audit):
            empty += 1
            continue
        totals = audit.get("cart") or {}
        if totals.get("total_value") is not None:
            total, shipping = totals["total_value"], totals.get("shipping_value") or 0
            tax = _tax_added(totals, _lx(page))
        elif totals.get("subtotal_value") is not None:
            total, shipping, tax = totals["subtotal_value"], 0, 0.0
        else:
            continue
        expected = float(base) * qty
        delta = max(0.0, (total - shipping - tax - expected) / expected * 100)
        step = {"stage": page["stage"], "total": total, "shipping": shipping, "delta": round(delta, 1)}
        steps.append({**step, "tax": tax} if tax else step)
    if not steps:
        _na("empty_cart" if empty else "no_cart_total")
    evidence = {"product_price": base, "qty": qty, "steps": steps}
    return max(s["delta"] for s in steps), ({**evidence, "steps_skipped": skipped} if skipped else evidence)


def _unexplained_fees(pages, view, profile):
    """Cost lines of the cart and checkout entry absent from the product page: shipping is explained by any shipping
    statement on the product page, another fee by its words appearing there (text sample, snippets and doc.words,
    every distinct word of the page). Lines the DPR tests already count (pre-checked add-ons, items sneaked into the
    basket) and tax lines are left out (one home per signal; a tax is no seller fee). An empty cart has no fees."""
    product, cart, checkout = (view.first(profile, s) for s in ("pdp", "cart", "checkout_entry"))
    if product is None:
        _na(view.reason("pdp", profile))
    if cart is None:
        _na(view.reason("cart", profile))
    if cart_empty(_dom(cart)) and (checkout is None or cart_empty(_audit(checkout))):
        _na("empty_cart")
    audit = _dom(product)
    text = " ".join([(audit.get("doc") or {}).get("text_sample") or "",
                     *(s.get("text") or "" for s in audit.get("snippets") or [])]).casefold()
    words = {str(w).casefold() for w in (audit.get("doc") or {}).get("words") or []}
    shipping_told = _shipping_shown(product) or bool(_persuasion(product, "free_shipping_threshold"))
    dec = view.deception(profile)
    counted = [i.get("label") for i in (dec.get("prechecked_paid") or {}).get("items") or []]
    counted += [i.get("title") for i in (dec.get("sneak_into_basket") or {}).get("unrequested") or []]
    for page in (cart, checkout):
        counted += [i.get("label") for i in (_audit(page).get("cart") or {}).get("prechecked_paid") or []]
    unexplained = {}
    for page in (cart, checkout):
        lx = _lx(page) if page else {}
        for fee in (_audit(page).get("cart") or {}).get("fees") or []:
            label = " ".join(str(fee.get("label") or "").split())
            if not label or not (fee.get("value") or 0) > 0 or any(same_item(label, c) for c in counted if c):
                continue
            if _tax_line(label, lx):
                continue
            if "shipping" in lx and lx["shipping"].search(label):
                explained = shipping_told
            else:
                needed = re.findall(r"[^\W\d_]{4,}", label.casefold())
                explained = label.casefold() in text or bool(needed) and all(w in text or w in words for w in needed)
            if not explained:
                unexplained.setdefault(_norm(label), {"label": label[:80], "value": fee.get("value"),
                                                      "stage": page["stage"]})
    return len(unexplained), {"lines": list(unexplained.values())[:6], "shipping_stated_on_pdp": shipping_told}


SITE_CHECKS = {
    "FAI.SEARCH_VISIBLE": _any(lambda p: bool(_search(p).get("present") and _search(p).get("above_fold"))),
    "FAI.SEARCH_WIDTH": _search_width,
    "FAI.SEARCH_AUTOCOMPLETE": _autocomplete,
    "TRI.CONTACT_INFO": _any(_contact),
    "TRI.LEGAL_ID": _any(lambda p: bool((_dom(p).get("trust") or {}).get("vat_id"))),
    "TRI.POLICY_LINKS": _max(lambda p: (_dom(p).get("trust") or {}).get("policy_count")),
    "TRI.JSONLD_RETURN_POLICY": _any(_structured("has_return_policy")),
    "TRI.JSONLD_SHIPPING": _any(_structured("has_shipping")),
    "TRI.PAYMENT_LOGOS": _max(lambda p: len((_dom(p).get("trust") or {}).get("payment_logos") or [])),
    "PTI.SHIPPING_COST_PRE_CHECKOUT": _any(_shipping_shown),
    "PTI.FREE_SHIPPING_THRESHOLD": _any(lambda p: bool(_persuasion(p, "free_shipping_threshold"))),
    "PTI.VAT_STATED": _any(lambda p: bool(_persuasion(p, "vat_statement"))),
    "PTI.FUNNEL_PRICE_DELTA": _funnel_delta,
    "PTI.UNEXPLAINED_FEES": _unexplained_fees,
    "CCL.INFO_SCENT_GENERIC": _max(lambda p: None if (_dom(p).get("nav") or {}).get("generic_label_share") is None
                                   else _round(100 * _dom(p)["nav"]["generic_label_share"])),
    "MPI.SCARCITY_SIGNALS": _scarcity,
    "MPI.URGENCY_SIGNALS": _urgency,
    "MPI.RECIPROCITY": _reciprocity,
}


# ---------------------------------------------------------------- DPR confidences from deception results
def _countdown(test):
    if not test.get("found"):  # no timer, or only time texts that do not tick
        return 0.0, ({"countdown": False, "static": test["static"][:3]} if test.get("static") else {"countdown": False})
    evidence = {k: test.get(k) for k in ("first", "second", "elapsed_s", "decrease_s", "tolerance_s")}
    for key in ("static", "pairs"):
        if test.get(key):
            evidence[key] = test[key][:3]
    if not test.get("reset"):
        return 0.0, evidence
    # no decrease at all (or an increase): the timer restarted; a smaller decrease: the deadline moved anyway
    return (1.0 if test["decrease_s"] <= test["tolerance_s"] else 0.8), evidence


def _low_stock(test):
    """A number that changes without a purchase (0.8 decreasing, 0.6 otherwise); the same number (N > 1) on 3+
    different products (0.8) or on 2 (0.4, a coincidence is plausible); a contradiction with the product's own
    structured data: an inventoryLevel that differs from N (0.8), a single Offer out of stock, sold out or discontinued
    (0.6). Two independent signals (a change, a number on 3+ products, a contradiction): 0.9."""
    if not test.get("found"):
        return 0.0, {"low_stock_message": False}
    changed = 0.8 if test.get("decreased") else 0.6 if test.get("changed_between_visits") else 0.0
    same = test.get("same_number_products") or 0
    shared = 0.8 if same >= 3 else 0.4 if same == 2 else 0.0
    contradicted = max((0.8 if c.get("signal") == "inventory_level" else 0.6
                        for c in test.get("contradictions") or []), default=0.0)
    strong = [signal for signal in (changed, shared if shared >= 0.8 else 0.0, contradicted) if signal]
    value = 0.9 if len(strong) >= 2 else max(changed, shared, contradicted)
    evidence = {k: test.get(k) for k in ("visits", "products", "same_number_products", "changed_between_visits")}
    for key in ("contradictions", "single_unit_excluded", "single_unit_products", "corroborated", "revisit_failure",
                "card_scope_unknown", "change_scope_unknown", "site_copy"):
        if test.get(key):
            evidence[key] = test[key]
    return value, evidence


def _sneak(test):
    lines = test.get("unrequested") or []
    if not lines:
        return 0.0, {"added": test.get("added")}
    return (1.0 if any(line.get("addon") for line in lines) else 0.8), {"added": test.get("added"),
                                                                        "unrequested": lines[:5]}


def _prechecked(test):
    """A priced pre-checked add-on (insurance, protection, donation): 1.0. Other priced pre-checked boxes (a newsletter
    voucher) are evidence only: they are no paid add-on."""
    priced = [i for i in test.get("items") or [] if (i.get("price_value") or 0) > 0 or i.get("price_text")]
    addons = [i for i in priced if i.get("addon", True) is not False]
    evidence = {"items": addons[:5]}
    if len(addons) < len(priced):
        evidence["other_prechecked"] = [i for i in priced if i not in addons][:5]
    return (1.0 if addons else 0.0), evidence


def _consent(test):
    """No reject on the first layer: 1.0 when the layer "manage" opens was read and has none either, 0.8 when it has
    one (an extra click) or could not be read (second_layer_checked False: the certain part is the missing first-layer
    reject). A reject smaller than a quarter of the accept: 0.8, than half: 0.6; equal size but only accept filled and
    the reject lower in contrast: 0.3. EDPB: refusing must not be harder than accepting. On an Italian page
    (reject_kind "close") the banner's X/"Chiudi" is the reject, scored by the same size ladder (Garante 2021). No
    accept control recognised: an informational notice (only a close control) is 0; a banner with other controls is
    not assessed ("accept_not_recognised": an "OK" the lexicon misses is a parse miss, not a balanced banner)."""
    layer = test.get("first_layer") or {}
    if not test.get("banner"):
        return 0.0, {"banner": False}
    accept, reject = layer.get("accept"), layer.get("reject")
    if reject is None and test.get("reject_kind") == "close":
        reject = layer.get("close")
    evidence = {"accept": (accept or {}).get("label"), "reject": (reject or {}).get("label"),
                "clicks_to_reject": test.get("clicks_to_reject")}
    if test.get("reject_kind"):
        evidence["reject_kind"] = test["reject_kind"]
    if accept is None:
        kinds = layer.get("kinds")
        notice = set(kinds) == {"close"} if kinds is not None else not (layer.get("reject") or layer.get("manage"))
        if not notice:
            _na("accept_not_recognised")
        return 0.0, {**evidence, "notice": True}
    if reject is None:
        if test.get("clicks_to_reject") == 2:
            return 0.8, evidence
        if test.get("second_layer_checked") is False:
            evidence.update(second_layer_checked=False, second_layer_reason=test.get("second_layer_reason"),
                            manage_click=test.get("manage_click"))
            return 0.8, evidence
        return 1.0, evidence
    ratio = (reject.get("area") or 0) / max(1, accept.get("area") or 0)
    evidence["area_ratio"] = round(ratio, 2)
    if ratio < 0.25:
        return 0.8, evidence
    if ratio < 0.5:
        return 0.6, evidence
    styled = accept.get("filled") and not reject.get("filled") and (reject.get("contrast") or 0) < (
        accept.get("contrast") or 0)
    return (0.3 if styled else 0.0), evidence


def _nagging(test):
    """Brignull's nagging is a repeated request: an overlay back after being closed and the page reloaded: 0.9; the
    same overlay kind shown again on later pages after it was closed, on 3+ pages in all: 0.8, on 2: 0.6. Different
    kinds on different pages, or an overlay shown until closed once, are interruptions (FAI.OVERLAY_INTERRUPTIONS),
    not nagging: 0."""
    repeated = test.get("repeated") or {}
    most = max((len(pages) for pages in repeated.values()), default=0)
    evidence = {"pages": (test.get("pages") or [])[:6], "repeated": repeated,
                "reappeared_after_close": test.get("reappeared_after_close")}
    if test.get("reappeared_after_close"):
        return 0.9, evidence
    return (0.8 if most >= 3 else 0.6 if most == 2 else 0.0), evidence


DPR_CHECKS = {
    "DPR.COUNTDOWN_RESET": ("countdown", _countdown),
    "DPR.FAKE_LOW_STOCK": ("low_stock", _low_stock),
    "DPR.SNEAK_INTO_BASKET": ("sneak_into_basket", _sneak),
    "DPR.PRECHECKED_PAID_ADDONS": ("prechecked_paid", _prechecked),
    "DPR.CONSENT_ASYMMETRY": ("consent", _consent),
    "DPR.NAGGING_OVERLAYS": ("nagging", _nagging),
}


# ---------------------------------------------------------------- run view and rows
class _View:
    def __init__(self, run):
        self.run = run
        pages = [p for p in run.get("pages") or [] if p.get("stage") in STAGES and p.get("page_id")]
        heads = {p["page_id"]: p["stage"] for p in pages if not p.get("repeat_of")}
        # a repeat counts only with its head: the reload of a rejected candidate is no funnel page
        self.pages = [p for p in pages if not p.get("repeat_of") or heads.get(p["repeat_of"]) == p["stage"]]
        settings = run.get("settings") or {}
        self.profiles = list(dict.fromkeys([*(settings.get("profiles") or []),
                                            *(p.get("profile") for p in self.pages if p.get("profile")),
                                            *((run.get("deception") or {}).keys())]))
        self.reasons: dict[tuple, str] = {}
        for item in run.get("not_assessable") or []:
            self.reasons.setdefault((item.get("stage"), item.get("profile")), item.get("reason") or "not_assessable")

    def reason(self, stage: str, profile: str) -> str:
        return self.reasons.get((stage, profile)) or self.reasons.get((stage, None)) or "not_reached"

    def groups(self, profile: str, stages) -> list[list[PageRecord]]:
        """Pages of the profile in the stages: each distinct page with its repeats, in run order."""
        groups: dict[str, list] = {}
        for page in self.pages:
            if page.get("profile") == profile and page["stage"] in stages:
                groups.setdefault(page.get("repeat_of") or page["page_id"], []).append(page)
        return list(groups.values())

    def first(self, profile: str, stage: str) -> PageRecord | None:
        return next((g[0] for g in self.groups(profile, (stage,))), None)

    def deception(self, profile: str) -> dict:
        return (self.run.get("deception") or {}).get(profile) or {}


def _row(kpi: Kpi, value, *, profile, page=None, stage=None, assessed=True, reason=None, evidence=None) -> Observation:
    return {"kpi_id": kpi.id, "value": value if assessed else None, "unit": kpi.unit, "source": "deterministic",
            "page_id": (page or {}).get("page_id"), "profile": profile,
            "stage": (page or {}).get("stage") or stage, "assessed": assessed, "reason": reason,
            "evidence": evidence or {}}


def _measure(kpi: Kpi, group: list[PageRecord], profile: str) -> Observation:
    head, results, reasons = group[0], [], []
    for page in group:
        try:
            _failed(page)
            results.append(PAGE_CHECKS[kpi.id](page))
        except NotAssessed as na:
            reasons.append(na.reason)
    if not results:
        return _row(kpi, None, profile=profile, page=head, assessed=False, reason=reasons[0])
    value, evidence = results[0]
    numbers = [v for v, _ in results if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if len(group) > 1:
        if len(numbers) == len(results):
            value = statistics.median(numbers)
            if isinstance(value, float) and value.is_integer() and all(isinstance(n, int) for n in numbers):
                value = int(value)  # counts stay integers
            value = _round(value, 4) if isinstance(value, float) and not value.is_integer() else value
        evidence = {**evidence, "repeats": len(group), "values": [v for v, _ in results]}
    return _row(kpi, value, profile=profile, page=head, evidence=evidence)


def _page_rows(kpi: Kpi, profile: str, view: _View) -> list[Observation]:
    if kpi.id in ("FAI.GUEST_CHECKOUT", "FAI.FORCED_ACCOUNT"):
        return _account_rows(kpi, profile, view)
    stages = kpi.stages or STAGES
    groups = view.groups(profile, stages)
    rows = [_measure(kpi, group, profile) for group in groups]
    reached = {group[0]["stage"] for group in groups}
    rows += [_row(kpi, None, profile=profile, stage=stage, assessed=False, reason=view.reason(stage, profile))
             for stage in stages if stage not in reached]
    return rows


def _account_rows(kpi: Kpi, profile: str, view: _View) -> list[Observation]:
    """Rule d. A reached, assessable checkout entry is the only evidence: guest when it offers a guest option or has no
    account gate (forms.login_required; audit.js judges it: a login box beside an address form is not a gate, a
    required registration password is). The password flags it read are evidence, not part of the decision; a flag an
    older audit lacks is left out of evidence. Otherwise a cart with a guest control gives a row (guest true, forced
    false), conclusive on its own. Neither: not assessed with the checkout stage's reason (a checkout page that could
    not be read keeps its own), plus the cart's when it was not reached. A cart row decided without a readable
    checkout page says why in evidence.checkout_entry: the page that could not be read and its reason, or page_id None
    and the stage's reason when no checkout page was kept. Both KPIs aggregate with "any" (kpis.py): the checkout
    prevails, so they never both read true."""
    forced = kpi.id == "FAI.FORCED_ACCOUNT"
    rows, failed = [], []
    for page in (g[0] for g in view.groups(profile, ("checkout_entry",))):
        try:
            forms = _forms(page)
        except NotAssessed as na:
            failed.append(_row(kpi, None, profile=profile, page=page, assessed=False, reason=na.reason))
            continue
        guest = bool(forms.get("guest_option")) or not forms.get("login_required")
        rows.append(_row(kpi, (not guest) if forced else guest, profile=profile, page=page, evidence={
            k: bool(forms[k]) for k in ("guest_option", "login_required", "password_required", "password_present")
            if k in forms}))
    if rows:
        return rows + failed
    carts = [g[0] for g in view.groups(profile, ("cart",))]
    # why the cart decides: the checkout page that could not be read, or the reason no checkout page was kept
    checkout = {"page_id": failed[0]["page_id"], "reason": failed[0]["reason"]} if failed else {
        "page_id": None, "reason": view.reason("checkout_entry", profile)}
    evidence = {"guest_option": True, "checkout_entry": checkout}
    rows = [_row(kpi, not forced, profile=profile, page=page, evidence=dict(evidence)) for page in carts
            if (_audit(page).get("forms") or {}).get("guest_option")]
    if rows:
        return rows
    rows = failed or [_row(kpi, None, profile=profile, stage="checkout_entry", assessed=False,
                           reason=view.reason("checkout_entry", profile))]
    if not carts:
        rows.append(_row(kpi, None, profile=profile, stage="cart", assessed=False, reason=view.reason("cart", profile)))
    return rows


def _site_row(kpi: Kpi, profile: str, view: _View) -> Observation:
    stages = kpi.stages or STAGES
    pages = [g[0] for g in view.groups(profile, stages)]
    if not pages:
        stage = stages[0]
        return _row(kpi, None, profile=profile, stage=stage, assessed=False, reason=view.reason(stage, profile))
    usable, reasons = [], {}
    for page in pages:
        try:
            audit = _dom(page)
            if kpi.id in EMPTY_CART_BLIND and page["stage"] == "cart" and cart_empty(audit):
                _na("empty_cart")
            usable.append(page)
        except NotAssessed as na:
            reasons.setdefault(page["stage"], na.reason)
    try:
        if not usable:
            _na(next(iter(reasons.values())))
        value, evidence = SITE_CHECKS[kpi.id](usable, view, profile)
    except NotAssessed as na:
        return _row(kpi, None, profile=profile, assessed=False, reason=na.reason,
                    evidence={"stages_checked": sorted({p["stage"] for p in pages}, key=STAGES.index)})
    checked = sorted({p["stage"] for p in usable}, key=STAGES.index)
    evidence = {**evidence, "stages_checked": checked}
    if kpi.aggregate == "any" and kpi.stages and value is False:  # rule b: false needs every stage of the KPI
        missing = [stage for stage in STAGES if stage in kpi.stages and stage not in checked]
        if missing:
            reason = reasons.get(missing[0]) or view.reason(missing[0], profile)
            return _row(kpi, None, profile=profile, stage=missing[0], assessed=False, reason=reason,
                        evidence={**evidence, "stages_missing": missing})
    return _row(kpi, value, profile=profile, evidence=evidence)


def _deception_row(kpi: Kpi, profile: str, view: _View) -> Observation:
    key, measure = DPR_CHECKS[kpi.id]
    test = view.deception(profile).get(key)
    if not test:
        return _row(kpi, None, profile=profile, assessed=False, reason="deception_not_run")
    page = {"page_id": test.get("page_id"), "stage": test.get("stage")}
    if not test.get("assessed"):
        reason = test.get("reason") or "not_assessed"
        if test.get("stage_missing") and test.get("stage"):  # rule a: the stage's own reason
            reason = view.reason(test["stage"], profile)
        return _row(kpi, None, profile=profile, page=page, assessed=False, reason=reason)
    try:
        value, evidence = measure(test)
    except NotAssessed as na:
        return _row(kpi, None, profile=profile, page=page, assessed=False, reason=na.reason)
    return _row(kpi, round(value, 3), profile=profile, page=page, evidence=evidence)


def observations(run) -> list[Observation]:
    """Every checks/deception KPI for every profile of the run (see the module docstring for the rules)."""
    view = _View(run)
    rows: list[Observation] = []
    for kpi in KPI_LIST:
        if kpi.producer not in PRODUCERS:
            continue
        for profile in view.profiles:
            if kpi.producer == "deception":
                rows.append(_deception_row(kpi, profile, view))
            elif kpi.scope == "site":
                rows.append(_site_row(kpi, profile, view))
            else:
                rows += _page_rows(kpi, profile, view)
    for row in rows:
        value = row.get("value")
        if isinstance(value, float) and not math.isfinite(value):
            row.update(value=None, assessed=False, reason="invalid_value")
    return rows
