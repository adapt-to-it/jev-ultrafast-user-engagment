"""Shared data contracts for engagement-readiness runs.

Every workstream codes against these shapes. Everything is plain JSON-serialisable data so a run can be
stored, replayed, scored and reported without a browser. Fields may be added (optional) but never renamed.
"""

from typing import Literal, NotRequired, TypedDict

SCHEMA_VERSION = "engagement.v1"

Stage = Literal["home", "plp", "pdp", "cart", "checkout_entry"]
STAGES: tuple[str, ...] = ("home", "plp", "pdp", "cart", "checkout_entry")

PageType = Literal["home", "plp", "pdp", "cart", "checkout", "challenge", "other"]
PAGE_TYPES: tuple[str, ...] = ("home", "plp", "pdp", "cart", "checkout", "challenge", "other")

SubIndex = Literal["PERF", "FAI", "TRI", "PTI", "CCL", "MPI"]
SUB_INDICES: tuple[str, ...] = ("PERF", "FAI", "TRI", "PTI", "CCL", "MPI")
RISK_INDEX = "DPR"

ObservationSource = Literal["deterministic", "judged"]
RunKind = Literal["audit", "journey"]

# Snippet kinds emitted by audit.js and consumed by judgments.py (rubric routing).
SNIPPET_KINDS: tuple[str, ...] = (
    "headline",  # h1 / hero value proposition
    "cta",  # primary call to action labels
    "returns_policy",  # returns / refund text near PDP, cart or footer
    "shipping_policy",
    "scarcity",  # "only 3 left", "ultimi pezzi"
    "urgency",  # "ends soon", countdown text
    "consent",  # cookie / consent banner text with its buttons
    "modal_decline",  # decline / close labels inside modals (confirmshaming candidates)
    "checkbox_label",  # labels of checkboxes in cart / checkout / signup (trick questions, pre-checked add-ons)
    "subscription",  # recurring / subscription / membership wording near CTAs or totals
    "authority",  # certifications, awards, press, expert endorsements
    "testimonial",  # testimonials and review excerpts
    "fee_line",  # cart / checkout lines that add cost
)


# ---------------------------------------------------------------- page level


class Vitals(TypedDict, total=False):
    """Read from window.__jevVitals.read() (vitals.js). Times in ms relative to navigation start."""

    ttfb: float | None
    ttfb_source: NotRequired[str]  # "cdp" (main Document response timing) | "navigation_timing" (fallback)
    fcp: float | None
    lcp: float | None
    lcp_element: str | None  # short descriptor, e.g. "img.hero" (never used as a selector)
    cls: float | None  # web-vitals session-window CLS, excluding hadRecentInput shifts
    cls_post_input: float | None  # sum of shifts with hadRecentInput=true
    long_tasks_ms: float | None  # total long-task time
    tbt_approx: float | None  # sum(max(0, d-50)) over long tasks between FCP and load+3s
    loaf_count: int | None  # long-animation-frame entries
    dcl_ms: float | None
    load_ms: float | None
    visibility_state_at_load: str | None  # "visible" | "hidden"; paint metrics need "visible"
    event_timing_max_ms: float | None  # max Event Timing duration observed so far
    js_errors: int | None  # window.onerror + unhandledrejection
    settled_reason: str | None  # "quiet" | "timeout" | "load_only"
    soft_navigation: NotRequired[bool]  # True: same document as an earlier record (client-side route change or
    #                                     back-forward cache restore); its load timings are None
    speculative: NotRequired[str]  # "prefetch" | "prerender": the shop's speculation rules fetched the document before
    #                                the click (not a cold navigation); its load timings are None
    activation_start: NotRequired[float | None]  # navigation.activationStart (> 0: a prerendered document activated)
    resources: NotRequired[list[dict]]  # optional trimmed Resource Timing rows (fallback for lossy transports)


class NetworkSummary(TypedDict, total=False):
    requests: int
    bytes_transfer: int  # encoded bytes on the wire
    bytes_js: int
    bytes_img: int
    bytes_css: int
    bytes_font: int
    bytes_third_party: int
    third_party_share: float  # 0..1 of bytes_transfer from other registrable domains
    third_party_domains: list[str]
    by_type: dict[str, dict]  # {resource_type: {"requests": n, "bytes": n}}
    failed: int  # Network.loadingFailed (excluding cancelled/blocked by us)
    http_errors: int  # responses with status >= 400
    http_error_samples: list[dict]  # [{url, status}] at most 10
    mixed_content: int  # http:// subresources on an https document
    source: Literal["cdp", "resource_timing"]


class PageErrors(TypedDict, total=False):
    console: int  # console.error + Runtime.exceptionThrown
    page: int  # uncaught exceptions
    samples: list[str]  # at most 10 trimmed messages


class Classification(TypedDict, total=False):
    type: str  # one of PAGE_TYPES
    confidence: float  # 0..1
    scores: dict[str, float]
    signals: list[str]


class Snippet(TypedDict):
    snippet_id: str  # unique within the page, e.g. "s12"
    kind: str  # one of SNIPPET_KINDS
    text: str  # verbatim visible text, whitespace-collapsed, <= 600 chars
    locator: NotRequired[str]  # human-readable descriptor for the report, never executed


class AuditPayload(TypedDict, total=False):
    """Output of audit.js: one Runtime.evaluate over the whole document (not only the viewport).

    Rects are page coordinates {x, y, w, h}; `above_fold` means rect.y < viewport height at scroll 0.
    Sections are dicts so audit.js can grow without breaking readers; readers must use .get().
    """

    url: str
    title: str
    lang: str
    viewport: dict  # {w, h, dpr}
    doc: dict  # {h1s, headings, word_count, sentence_count, letter_count, text_sample}
    jsonld: list[dict]  # parsed JSON-LD objects (flattened @graph), trimmed: no long prose, first image only, and
    #                     past ~30 KB only {@type, other keys: None}
    meta: dict  # {og_type, canonical, og_price_amount, og_price_currency, jsonld_types, microdata_products,
    #             microdata_main (schema.org Product itemscopes outside product cards), speculation_rules (bool)}
    prices: list[dict]  # [{text, value, currency, strikethrough, rect, above_fold, near_text}]
    ctas: list[dict]  # [{label, lexicon_hit, rect, above_fold, fg, bg, contrast, area, primary_like}]
    search: dict  # {present, above_fold, width, has_autocomplete_attr, rect}
    nav: dict  # {links, categories:[{label, href}], breadcrumbs, generic_label_share, cart_link}
    products: dict  # {cards_count, cards:[{title, href, price}], itemlist_jsonld}
    filters: dict  # {controls, sort, result_count_text, chips, pagination}
    pdp: dict  # {add_to_cart, add_to_cart_count (outside product cards), add_to_cart_all, stock_text, delivery_text,
    #            shipping_text, returns_text, images, zoom, variants, variant_selector,
    #            reviews:{count_text, rating_text}}
    cart: dict  # {line_items:[{title, qty, price}], subtotal_text, shipping_text, total_text, checkout_cta,
    #             editable, prechecked_paid:[{label, price_text}]}
    forms: dict  # {fields_total, required, visible, autocomplete_share, labels_share, cc_present, password_present,
    #              password_required (a visible password that is required or not labelled optional), guest_option,
    #              login_required}
    overlays: list[dict]  # [{kind, coverage, has_close, consent_like, accept_labels, reject_labels, manage_labels,
    #                         decline_labels, accept_area, reject_area, text_sample, interrupting, modal, blocking,
    #                         buttons:[{label (accessible name), text (visible), kind}]}]
    trust: dict  # {https, contact:{email, phone, address}, vat_id, policy_links:{returns, shipping, privacy, terms,
    #              contact}, payment_logos, badges}
    persuasion: dict  # {scarcity:[{text, number}], urgency:[{text, countdown, remaining_s}], reciprocity:[...],
    #                   authority:[...], free_shipping_threshold:[...], lowest_price_30d:[...], vat_statement:[...]}
    images: dict  # {count, oversized:[{src, natural_w, display_w}], lazy_share, missing_alt}
    targets: dict  # {interactive, lt24, lt44, lt48}
    a11y: dict  # {img_missing_alt, inputs_missing_label, lang_missing, iframes, shadow_roots_open, shadow_roots_closed}
    snippets: list[Snippet]
    lexicon_lang: NotRequired[str]  # the page's effective language (lexicon.effective_language): the lexicon audit.js
    #                                 and the classifier used, and the language of its text


class PageRecord(TypedDict, total=False):
    page_id: str  # "<profile>-<stage>-<n>", unique within a run
    stage: str  # one of STAGES, or "extra"
    profile: str  # "mobile" | "desktop"
    url: str  # requested URL
    final_url: str
    status_code: int | None
    classification: Classification
    vitals: Vitals
    network: NetworkSummary
    errors: PageErrors
    audit: AuditPayload
    screenshot: str | None  # path relative to the run directory, e.g. "shots/<page_id>.jpg"
    snapshot: str | None  # path relative to the run directory, e.g. "snapshots/<page_id>.json"
    loaded_ms: float | None  # wall time from navigation to settle
    visual: dict  # {colorfulness, edge_density, bytes_per_px} computed from the screenshot (collectors.py)
    probes: dict  # active probes run by the crawler, e.g. {"search_autocomplete": {typed, options, latency_ms}}
    consent: dict  # {"choice": "reject"|"accept"|"none", "reason": str} when the crawler handled a consent banner
    notes: list[str]
    repeat_of: NotRequired[str]  # settings.repeats > 1: page_id of the first load of the same URL (checks.py reports
    #                              the median over a page and its repeats, one row per distinct page)


class NotAssessable(TypedDict, total=False):
    stage: str
    profile: str | None
    kpi_id: str | None
    reason: str  # e.g. "bot_challenge", "not_found", "checkout_boundary", "background_tab", "timeout"


# ---------------------------------------------------------------- observations and judgments


class Observation(TypedDict, total=False):
    kpi_id: str  # id from kpis.KPIS
    value: float | int | bool | str | None
    unit: str
    source: ObservationSource
    page_id: str | None  # None for site-level / journey-level observations
    profile: str | None
    stage: str | None
    assessed: bool  # False -> "not assessable" (never scored as 0)
    reason: str | None  # why not assessed, or a short note
    evidence: dict  # small, JSON-serialisable: quotes, counts, urls; no screenshots


class JudgmentTask(TypedDict, total=False):
    task_id: str  # "<rubric_id>:<page_id>:<snippet_id>" or similar, unique within the run
    run_id: str
    rubric_id: str
    rubric_version: str
    kpi_id: str
    page_id: str
    profile: str
    question: str
    labels: dict[str, str]  # closed label set {label: description}
    no_quote_labels: list[str]  # labels that may be returned without evidence quotes
    snippets: list[Snippet]
    context: dict  # {page_type, locale, url}
    samples_required: int


class EvidenceQuote(TypedDict):
    snippet_id: str
    quote: str  # must be a verbatim substring of the snippet text (after NFC + whitespace collapse)


class Verdict(TypedDict, total=False):
    task_id: str
    judge_id: str  # e.g. "j1"
    model: str  # model id reported by the host
    label: str  # must be one of the task labels
    confidence: float  # 0..1
    evidence: list[EvidenceQuote]
    rationale: str  # <= 280 chars


class FinalJudgment(TypedDict, total=False):
    task_id: str
    kpi_id: str
    label: str | None  # None -> uncertain (tie / insufficient agreement), not assessed
    agreement: float  # share of samples agreeing with the majority label
    samples: int
    confidence: float
    models: list[str]
    evidence: list[EvidenceQuote]


class JudgmentState(TypedDict, total=False):
    rubrics_version: str
    tasks: list[JudgmentTask]
    verdicts: list[Verdict]
    final: list[FinalJudgment]
    skipped: list[dict]  # rubrics without tasks: [{rubric_id, kpi_id, reason, detail}]


# ---------------------------------------------------------------- journey


class StepSince(TypedDict, total=False):
    """window.__jevVitals.since(mark) read after an action; all counts are since the mark."""

    first_response_ms: float | None  # first mutation / navigation / request after the action
    mutations: int  # records of non-ticker nodes (see vitals.js)
    mutations_total: NotRequired[int]  # every record, self-updating (ticker) nodes included; evidence only
    navigations: int  # history pushState/replaceState/popstate + document navigations + back-forward cache restores
    requests: int  # requests of non-poller URLs (see vitals.js)
    requests_total: NotRequired[int]  # every request, a page's own polling included; evidence only
    errors: int
    shifts_post_input: float
    event_timing_max_ms: float | None


class JourneyStep(TypedDict, total=False):
    step: int
    t_wall: str  # ISO-8601 UTC
    operation: str  # CLICK | TYPE_TEXT | SELECT | SCROLL_UP | SCROLL_DOWN | WAIT | DONE | BLOCKED
    target: str | None  # observed element index, never a selector
    label: str | None
    kind: str | None  # click | fill | select | scroll | wait
    node: int | None
    text_len: int | None  # length of typed text (the text itself is not stored in steps)
    decision_latency_ms: float | None  # excluded from site-attributable time
    policy: str  # "host" | "typesafe" | "scripted"
    url_before: str
    url_after: str | None
    page_changed: bool | None
    since: StepSince
    settle_ms: float | None  # act -> next observation ready (site-attributable)
    overlay: dict  # {present, coverage}
    flags: dict  # {dead_click, rage, backtrack, guard_blocked, stale, external_nav}
    status: str  # agent status after this step
    guard_notes: list[str]
    role: NotRequired[str | None]  # observed role of the target (dead clicks count button and link clicks only)
    page_type: NotRequired[str | None]  # classification of the page the action was taken on
    execution_ms: NotRequired[float | None]  # input dispatch (site-attributable, with settle_ms)
    settle_reason: NotRequired[str | None]  # "quiet" | "timeout"
    # flags may also hold new_tab, new_document, unexpected_nav and uncertain (an input whose execution was not
    # confirmed); steps with flags.stale or flags.guard_blocked executed nothing and are not counted as actions
    # operation "NAVIGATION": the page navigated by itself between two steps (no input was sent); since is {}, it is
    # not counted as an action, and its flags (new_document, external_nav, backtrack, unexpected_nav, follows_timeout)
    # feed FAI.UNEXPECTED_NAV and the visit sequence (backtrack, Lostness)


class JourneyRecord(TypedDict, total=False):
    goal: str
    oracle: str
    oracle_params: dict
    profile: str
    policy: str
    max_steps: int
    status: str  # running | done | blocked | stopped_at_checkout_boundary | budget_exhausted | error
    verification: dict | None  # {"passed": bool | None, "checks": {...}}; None only with checks["not_assessable"]
    #                            ("bot_challenge", "journey_error", "cart_price_ambiguous", "cart_items_unreadable",
    #                            "cart_price_unreadable") or checks["error"]
    steps_path: str  # "steps.jsonl"
    optimal_steps: int | None  # R for Lostness / actions ratio, when known
    started_at: str
    finished_at: str | None
    optimal_pages: NotRequired[int | None]  # Lostness R: minimum distinct pages, start page included (optimal_steps
    #                                         is then the minimum number of interactions, for FAI.ACTIONS_RATIO)


# ---------------------------------------------------------------- scores


class KpiScore(TypedDict, total=False):
    id: str
    value: float | int | bool | str | None  # aggregated raw value
    unit: str
    normalized: float | None  # 0..100, None when not assessed
    owner: str  # sub-index id or "DPR"
    source: ObservationSource
    assessed: bool
    reason: str | None
    observations: int
    provisional: bool  # anchor is editorial, not published
    weight: float  # weight inside the owner sub-index (0 = informational or DPR)
    applicable: bool  # False: excluded from coverage (no journey, no judgments, or reason "not_applicable...")
    profile: str | None  # the profile whose aggregate is the value, when it scores strictly worse than every other
    #                      profile's; None otherwise (single profile, tie, pooled, site-level without profiles)


class SubIndexScore(TypedDict, total=False):
    score: float | None
    coverage: float  # 0..1 assessed weight / total weight
    grade: str | None  # "A" | "B" | "C" | None
    llm_share: float  # 0..1 judged weight / assessed weight
    limiting_kpis: list[str]  # lowest normalized KPIs


class DprScore(TypedDict, total=False):
    score: float  # 0..100 over the assessed signals; 0 with coverage 0 means "no penalty applied", not "no risk"
    signals: list[dict]  # [{kpi_id, severity, confidence, evidence}]
    coverage: float  # 0..1, severity-weighted share of applicable DPR KPIs that were assessed
    assessed: int
    applicable: int  # deterministic DPR KPIs unless reported not_applicable; judged ones only when context.judged
    unassessed: list[dict]  # [{kpi_id, reason}]


class ErsScore(TypedDict, total=False):
    score: float | None
    published: bool
    grade: str | None
    coverage: float
    llm_share: float
    reason: str | None
    limiting_factor: str | None  # lowest sub-index


class ScoreOutput(TypedDict, total=False):
    anchors_version: str
    weights: dict[str, float]
    context: dict  # {"journey": bool, "judged": bool, "journey_runs": [run_id]}: applicable KPI families and the
    #                journey runs merged by scoring.score_run(journeys=...)
    kpis: list[KpiScore]
    sub_indices: dict[str, SubIndexScore]
    dpr: DprScore
    ers: ErsScore
    top_risk_signals: list[dict]


class RunScores(TypedDict, total=False):
    overall: ScoreOutput
    profiles: dict[str, ScoreOutput]  # same scoring restricted to one profile (+ site-level observations)


# ---------------------------------------------------------------- run


class RunRecord(TypedDict, total=False):
    run_id: str
    schema_version: str
    kind: str  # "audit" | "journey"
    site: dict  # {host, start_url}
    created_at: str  # ISO-8601 UTC
    finished_at: str | None
    settings: dict  # {profiles, stages, browser:{mode, product, headless}, locale, consent, anchors_version,
    #                  rubrics_version, profiles_version, repeats}
    pages: list[PageRecord]
    not_assessable: list[NotAssessable]
    deception: dict  # raw deception-test results (deception.py), consumed by checks.py
    journey: JourneyRecord | None
    observations: list[Observation]
    judgments: JudgmentState
    scores: RunScores | None
    status: str  # created | running | complete | partial | failed
    warnings: list[str]
    errors: list[str]
