# Engagement auditor: module contracts (read this before coding)

Repo: /home/user/jev-ultrafast-user-engagment (branch claude/nice-mccarthy-yf5hk1). Python 3.13 via `uv`.
Approved plan (Italian): research/approved-plan.md. Full architecture rationale: research/architecture-plan-fable.md.
Research: research/research-performance-elements-friction.md, research/research-psychology-indices.md.
Codebase map: research/codebase-map.md.

## Already done (by the coordinator; do not rewrite)
- `pyproject.toml`: deps `mcp>=2.3,<3` (v2 API: `from mcp.server import MCPServer`, `@server.tool(annotations=ToolAnnotations(...))`, `server.run()` stdio), `pillow`, `websockets` (15, has `websockets.sync.client.connect`), scripts `jev-engage = jev_ultrafast.engagement.cli:main`, `jev-engage-mcp = jev_ultrafast.engagement.mcp_server:main`. `uv sync` done. Do NOT edit pyproject.toml / uv.lock; if you need a dependency, say so in your final report.
- `jev_ultrafast/engagement/schemas.py`: all data shapes (TypedDicts). Treat as the contract. You MAY add optional keys (additive only) with a small Edit if you truly need one, and must list it in your final report. Never rename/remove.
- `jev_ultrafast/engagement/kpis.py`: KPI registry (89 ids, owner, unit, producer, aggregate, value_type, stages, severity, credit_unless). Same additive-only rule. Producers: `checks` (WS2), `deception` (WS2), `friction` (WS3), `judgments` (WS4).
- stubs `engagement/cli.py`, `engagement/mcp_server.py` (WS5 replaces them).

## Global rules (from AGENTS.md + plan)
- Never let any model emit selectors or code; agents choose observed element indices only. Code-owned lexicon matching of observed elements is fine. No site-specific plans or hardcoded per-site values.
- Never retry a browser mutation (click/fill/select/add-to-cart). Log execution before observing its result.
- Never place orders, never submit checkout forms, never type into payment (`cc-*`), password, or personal-data fields; stop at the first checkout page.
- Tests must not call paid APIs or the public internet. Browser tests use local fixtures + headless Chromium and are skipped when Chromium is missing.
- Vocabulary in user-facing text: "engagement readiness", "predicted friction", "risk signals". Never "measured engagement", never "violations".
- Style: match the repo (compact, readable, few comments, ruff line-length 120, rules E,F,I). Python >=3.12.
- Only edit files you own (see the workstream table). Run `uv run ruff check <your files>` and `uv run pytest <your test files> tests/test_agent.py -q` (other workstreams edit in parallel; do not run the whole suite as a gate, and do not "fix" files you don't own: report problems instead).
- Chromium in this container: `/opt/pw-browsers/chromium-1194/chrome-linux/chrome` (v141). We run as root: Chromium needs `--no-sandbox` when `os.geteuid() == 0`.

## WS0 Foundation (owner files)
`engagement/{settings,transport,chrome,profiles,store}.py`, edits in `jev_ultrafast/browser.py`, `tests/conftest.py`, `tests/test_engagement_foundation.py`, `tests/fixtures/basic.html`.

```python
# transport.py
class CdpError(RuntimeError): ...                     # CDP replied with {"error": ...}
class CdpTransport(Protocol):
    kind: str            # "direct" | "harness"
    lossy: bool          # True when events may be dropped (harness ring buffer)
    def call(self, method: str, session_id: str | None = None, *, timeout: float = 30.0, **params) -> dict: ...
    def events(self, session_id: str | None = None, method_prefix: str | None = None) -> list[dict]: ...
        # drain matching buffered events [{"method","params","session_id"}]; non-matching events stay buffered
    def close(self) -> None: ...
class DirectTransport:   # websockets.sync client, reader thread, id->future, unbounded deque (cap max_events)
    def __init__(self, ws_url: str, *, max_events: int = 200_000): ...
class HarnessTransport:  # wraps browser_harness.helpers.cdp/drain_events; sets BH_TAB_MARKER=0; lossy=True
    def __init__(self, *, name: str = "jev-engagement", env: dict | None = None): ...
def open_transport(browser: str = "auto", *, headless: bool = True, window=(1366, 768), lang: str = "it-IT")
    -> tuple[CdpTransport, "LaunchedChrome | None"]
    # "auto": BU_CDP_WS -> Direct; BU_CDP_URL -> resolve /json/version -> Direct; find_chromium() -> launch + Direct;
    #         else Harness. "launch" | "harness" | "cdp:<ws-or-http-url>" force one.

# chrome.py
def find_chromium() -> str | None   # JEV_CHROME_PATH, CHROME_PATH, BH_CHROME_PATH, PATH names, /opt/pw-browsers/chromium-*/chrome-linux/chrome, mac/win paths
@dataclass
class LaunchedChrome: ws_url: str; process: subprocess.Popen; user_data_dir: str; def close(self) -> None
def launch_chromium(*, path=None, headless=True, window=(1366, 768), lang="it-IT", extra_args=()) -> LaunchedChrome
    # --headless=new --remote-debugging-port=0 --user-data-dir=<tmp> --no-first-run --no-default-browser-check
    # --disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows
    # --window-size=W,H --lang=... (+ --no-sandbox as root, --disable-dev-shm-usage); ws url from DevToolsActivePort

# profiles.py
PROFILES_VERSION = "profiles.v1"
DEVICE_PROFILES: dict[str, dict]   # "mobile": 390x844 dpr3 mobile touch, latency 150ms, down 209715 B/s, up 96000 B/s, cpu 4x,
                                   # Chrome-mobile UA without "Headless"; "desktop": 1366x768 dpr1, no throttling, desktop UA
def new_context(transport) -> str                    # Target.createBrowserContext(disposeOnDetach=True)
def close_context(transport, context_id) -> None
def apply_profile(transport, session_id, profile: str | dict, *, cache_disabled=True, lang="it-IT") -> dict
    # Network.enable, setCacheDisabled, emulateNetworkConditions, Emulation.setDeviceMetricsOverride,
    # setUserAgentOverride(acceptLanguage), setTouchEmulationEnabled, setCPUThrottlingRate, setFocusEmulationEnabled
    # returns the applied config (stored in run settings)

# browser.py (minimal, keep default behaviour and existing tests untouched)
class Browser:
    def __init__(self, url, *, transport=None, metrics=None, browser_context_id=None, background=True,
                 prepare=None, load_timeout=15): ...
        # transport None -> current behaviour (ensure_daemon + module cdp). prepare(browser) runs after attach and
        # metrics, before Page.navigate (collectors inject vitals.js / apply profiles there).
    def navigate(self, url, *, load_timeout=15) -> None   # Page.navigate + readyState poll (no retry)
    # self.transport, self.session, self.target; call()/close() route through transport when present
def browser_operation(request, transport=None): ...       # send = transport.call if transport else cdp

# settings.py
@dataclass
class EngagementSettings:
    url: str
    profiles: list[str] = ["mobile", "desktop"]
    stages: list[str] = list(STAGES)
    browser: str = "auto"; headless: bool = True; locale: str = "it"; consent: str = "auto"  # auto|reject|accept|none
    repeats: int = 1; max_pages: int = 12; settle_timeout_s: float = 20.0
    artifacts_dir: str | None = None; screenshots: bool = True
    def to_dict(self) -> dict

# store.py
def artifacts_root(explicit: str | None = None) -> Path   # explicit, else $JEV_ENGAGEMENT_ARTIFACTS, else ./artifacts/engagement
RUN_ID_RE = r"^\d{8}T\d{6}\d{6}Z_(audit|journey)_[a-z0-9.-]+$"
class RunStore:
    def __init__(self, root: str | Path | None = None): ...
    def new_run(self, kind: str, start_url: str, settings: dict, *, now: datetime | None = None) -> str
        # run_id = f"{UTC %Y%m%dT%H%M%S%f}Z_{kind}_{host_slug}", dir = root/host_slug/run_id, writes run.json (RunRecord)
    def path(self, run_id: str) -> Path           # validates run_id (no traversal)
    def load(self, run_id: str) -> RunRecord
    def save(self, run_id: str, run: RunRecord) -> None   # atomic (tmp + os.replace)
    def update(self, run_id: str, fn: Callable[[RunRecord], None]) -> RunRecord   # load, mutate, save under a lock
    def append_step(self, run_id: str, step: dict) -> None   # steps.jsonl, flush + fsync
    def read_steps(self, run_id: str) -> list[dict]
    def write_json(self, run_id: str, rel: str, payload) -> str     # e.g. "snapshots/<page_id>.json"
    def write_bytes(self, run_id: str, rel: str, data: bytes) -> str  # e.g. "shots/<page_id>.jpg"
    def list_runs(self, host: str | None = None, limit: int = 20) -> list[dict]   # newest first, small summaries

# tests/conftest.py fixtures
shop_server   # session-scoped ThreadingHTTPServer on 127.0.0.1:0 serving tests/fixtures/ (shop pages live in
              # tests/fixtures/shop/, owned by WS1); records every request (method, path, body) in .requests;
              # helper .url(path) -> "http://127.0.0.1:<port>/<path>"; POSTs answered 200 with a JSON body.
chromium      # session-scoped: find_chromium() or pytest.skip; launch_chromium(); yields LaunchedChrome; closes
transport     # function-scoped DirectTransport(chromium.ws_url); closed after the test
```

## WS1 Collectors (owner files)
`engagement/{vitals.js,audit.js,lexicon.py,collectors.py,pagetypes.py,safety.py}`, `tests/fixtures/shop/**`, `tests/test_engagement_collectors.py`, `tests/test_engagement_pagetypes.py`, `tests/test_engagement_safety.py`.
(safety.py moved here from WS2 because both WS2 and WS3 import it; its contract is listed under WS2.)
Collectors also fill PageRecord.visual {colorfulness (Hasler-Susstrunk), edge_density, bytes_per_px} from the screenshot with Pillow.

```python
# lexicon.py: data, JS-compatible regex source strings (case-insensitive), IT + EN, extensible
LEXICON: dict[str, dict[str, list[str]]]   # {"it": {...}, "en": {...}}
def lexicon_for(locale: str) -> dict[str, list[str]]   # merged locale + "en", keys below
# keys: add_to_cart, buy_now, checkout, cart, search, filters, sort, result_count, load_more, shipping, free_shipping,
# returns, delivery, vat, lowest_price_30d, scarcity, urgency, reciprocity, authority, contact, legal_id,
# payment_brands, pay_now (forbidden), place_order (forbidden), guest, login, register, consent_accept, consent_reject,
# consent_manage, newsletter, subscription, generic_link, decline, category_url, product_url, cart_url, checkout_url

# vitals.js: IIFE installed with Page.addScriptToEvaluateOnNewDocument; idempotent; defines window.__jevVitals:
#   read() -> Vitals (schemas.Vitals), mark() -> number (performance.now()), since(t) -> StepSince,
#   quiet(ms) -> bool (no new resource / LCP / layout-shift entries and no mutations in the last ms)
# audit.js: an expression `(lexicon) => AuditPayload`, evaluated as f"({AUDIT_JS})({json.dumps(lexicon)})"
#   reads the whole document (+ open shadow roots), returns schemas.AuditPayload incl. snippets (SNIPPET_KINDS)
#   forms.{guest_option,login_required,password_present} are per-page booleans filled on every page type (cart and
#   checkout_entry included); checks.py decides which page is evidence (doc 3.5 rule d)

# collectors.py
VITALS_JS: str; AUDIT_JS: str
class PageCollector:
    def __init__(self, transport, store: RunStore, run_id: str, *, profile: str, locale: str = "it",
                 screenshots: bool = True, settle_timeout_s: float = 20.0, context_id: str | None = None): ...
    def open(self, url: str) -> Browser          # Browser(url, transport=..., browser_context_id=..., background=False,
                                                 #   prepare=<apply_profile + enable Network/Runtime/Log/Page + install vitals>)
    def load_page(self, browser, url: str, *, stage: str, page_id: str) -> PageRecord   # navigate, settle, collect
    def collect(self, browser, *, stage: str, page_id: str, requested_url: str | None = None) -> PageRecord
        # settle (load + network quiet 1.5s + LCP quiet 2s, cap settle_timeout_s), read vitals, drain events into
        # NetworkSummary/PageErrors, run audit.js, classify, screenshot -> store, snapshot -> store
    def audit(self, browser) -> AuditPayload
    def vitals(self, browser) -> Vitals
    def mark(self, browser) -> float ; def since(self, browser, mark: float) -> StepSince   # used by journeys

# pagetypes.py
def classify(audit: AuditPayload, url: str) -> Classification   # home/plp/pdp/cart/checkout/challenge/other
```
Fixture shop (static HTML, no external URLs, Italian copy, served by `shop_server`): `index.html` (home: search, nav
categories, consent banner with accept-only first layer, footer policies/P.IVA/contacts/payment logos),
`category.html` (PLP: >=24 product cards, filters, sort, result count, breadcrumbs, pagination),
`product.html` (PDP: JSON-LD Product/Offer/AggregateRating/hasMerchantReturnPolicy/shippingDetails, visible price
matching JSON-LD, strikethrough with Omnibus 30-day text, add-to-cart above fold, size buttons, stock, delivery,
shipping, returns, reviews), `product-dark.html` (countdown that restarts on every load, "solo 2 rimasti" low-stock,
confirmshaming newsletter modal "No grazie, preferisco pagare di più", price mismatch), `cart.html` (line items,
subtotal, shipping, total, quantity/remove, checkout CTA, guest option, an unrequested "Protezione spedizione" line
item and a pre-checked paid add-on checkbox, appears for `?dark=1`), `checkout.html` (step 1: 14 fields, no guest
option, password field, "Paga ora" button, form POST to /pay which tests assert is never hit), `challenge.html`
(CAPTCHA-like interstitial). Add-to-cart may be simulated with JS + localStorage + link to cart.html.

## WS2 Deterministic audit (owner files)
`engagement/{crawler,checks,deception,audit}.py`, `tests/test_engagement_checks.py`, `tests/test_engagement_audit.py` (safety.py is written by WS1; contract below).
The crawler also runs the search-autocomplete probe on home (type a word taken from an observed category label into the observed search field, wait <= 800 ms for visible role=option entries, record PageRecord.probes.search_autocomplete) and handles consent (settings.consent; record PageRecord.consent).

```python
# safety.py
class CheckoutGuard:
    def __init__(self, start_url: str, lexicon: dict, *, stop_at: str = "checkout_entry"): ...
    def same_site(self, url: str) -> bool                               # registrable-domain comparison
    def allowed_action(self, action: dict, page: dict, page_type: str | None) -> tuple[bool, str | None]
        # refuses: labels matching pay_now/place_order (any page); any submit/click on checkout pages except
        # navigation away; actions when page is external
    def filter_actions(self, page: dict, page_type: str | None) -> tuple[list[dict], list[str]]   # (allowed, notes)
    def allow_text(self, browser, action: dict) -> tuple[bool, str | None]
        # inspects the observed node (type/autocomplete/name/id) via window.__jevFast.nodes.get(node): refuses
        # password, cc-*, email/tel/name/address autocomplete tokens, iban, fiscal code; allows search-like fields
    def should_stop(self, page_type: str | None) -> bool

# crawler.py  (generic; no per-site plans)
def discover_funnel(collector: PageCollector, settings, *, profile: str, guard: CheckoutGuard)
    -> tuple[list[PageRecord], list[NotAssessable]]
    # home -> best PLP candidate (nav/category links scored by lexicon + URL patterns + JSON-LD) -> first card that
    # classifies as PDP -> click observed add-to-cart (Browser.observe/act; element chosen by lexicon; never retried)
    # -> cart (cart link / cart_url pattern) -> optional checkout_entry (click observed checkout CTA; never fill/submit)
    # challenge pages -> NotAssessable(reason="bot_challenge") for the remaining stages

# deception.py  (second isolated context, same profile)
def run_deception_tests(transport, settings, pages: list[PageRecord], *, profile: str, store, run_id) -> dict
    # returns raw results stored in run["deception"]; checks.py turns them into DPR observations:
    # countdown reset on reload, low-stock numbers across reloads/products, sneak-into-basket diff,
    # pre-checked paid add-ons, consent accept/reject asymmetry, nagging overlay counts

# checks.py
def observations(run: RunRecord) -> list[Observation]   # every producer=="checks" or "deception" KPI it can assess
    # emits assessed=False with reason when a stage is missing (never value 0 for "not found page")

# audit.py
def audit_shop(settings: EngagementSettings, *, store: RunStore | None = None, transport_factory=None,
               progress: Callable[[str], None] | None = None) -> str   # run_id
    # per profile: new context, PageCollector, discover_funnel, deception tests, close context;
    # then run["observations"] = checks.observations(run) (deterministic only); status complete|partial|failed
```

## WS3 Journey (owner files)
edits in `jev_ultrafast/agent.py`, `engagement/{journey,friction,oracles}.py`, `tests/test_engagement_journey.py`, `tests/test_engagement_friction.py`.

```python
# agent.py: Agent(url, goals, *, browser=None, policy=None, text_policy=None, record_dir=None, screenshots=False)
#   predict uses (self.policy or choose)(page, goal, history); fill uses (self.text_policy or field_text)(context)
#   all existing behaviour/tests unchanged.
# journey.py
class HostPolicy:   # decision dict with the choose() contract: choice, operation, target, confidence,
                    # probabilities{choice: 1.0}, operation_probabilities, target_probabilities, latency_ms, usage, model
    def set(self, operation: str, target: str | None, text: str | None = None) -> None   # validated against action_space
    def __call__(self, page, goal, history) -> dict
    def text(self, context) -> tuple[str, dict]
class JourneyRunner:
    def __init__(self, settings, *, store: RunStore | None = None, transport_factory=None): ...
    def start(self, url, goal, *, oracle: str, oracle_params: dict | None = None, profile="mobile",
              policy="host", max_steps=40, optimal_steps: int | None = None) -> dict   # {run_id, status, observation}
    def observation(self) -> dict   # {url, title, text_excerpt<=3000, elements:[{index,label,role,value,operations,...}],
                                    #  controls, step, budget_left, guard_notes}
    def act(self, operation: str, target: str | None = None, text: str | None = None) -> dict
        # {executed, stale, page_changed, step_metrics, status, observation}; StalePage -> stale=True, no step consumed
    def run_auto(self) -> dict      # policy "typesafe": loop until done/blocked/budget (needs TYPESAFE_API_KEY)
    def finish(self, status: str | None = None) -> dict   # verification via oracles, friction observations into run
# friction.py
def lostness(unique_pages: int, total_visits: int, optimal: int) -> float
def metrics(steps: list[JourneyStep], *, optimal_steps: int | None = None, success: bool | None = None)
    -> list[Observation]    # all producer=="friction" KPIs
# oracles.py
ORACLES: dict[str, Callable]   # "cart_contains_item_under_price"(max_price), "cart_not_empty", "pdp_reached",
                               # "search_results_shown"(query?); each -> {"passed": bool, "checks": {...}}
def verify(name, params, *, browser, collector, run) -> dict   # independent of the agent's DONE
```

## WS4 Judgments, scoring, report (owner files)
`engagement/{judgments,judges,scoring,report}.py`, `engagement/anchors.json`, `engagement/rubrics/*.json`, `tests/test_engagement_scoring.py`, `tests/test_engagement_judgments.py`, `tests/test_engagement_report.py`, `tests/fixtures/runs/*.json` (golden runs).

```python
# anchors.json: {"version": "anchors.v1", "weights": {"FAI":22,"TRI":20,"PTI":15,"CCL":15,"PERF":18,"MPI":10},
#   "dpr_penalty": 0.30, "floor": 10, "publish": {"min_coverage": 0.6, "min_major_coverage": 0.5},
#   "grades": {"A":0.85,"B":0.70,"C":0.60}, "stage_weights": {"pdp":2,"plp":2},
#   "kpis": {"<id>": {"weight": w, "direction": "lower|higher|bool|enum|negative_bool", "points": [[v, s], ...],
#            "map": {...}, "provisional": bool, "source": "..."}}}  -- exactly the ids of kpis.KPIS (DPR ids carry no points)
# scoring.py (pure)
def load_anchors(path=None) -> dict
def normalize(kpi_id, value, anchors) -> float | None
def aggregate(observations, kpi) -> tuple[value, assessed, reason]
def score(observations: list[Observation], anchors=None) -> ScoreOutput
def score_run(run: RunRecord, anchors=None) -> RunScores        # overall + per profile (+ site/journey level obs)
# judgments.py
def load_rubrics() -> dict
def make_tasks(run: RunRecord, *, samples_required=3) -> list[JudgmentTask]   # from pages[*].audit.snippets
def submit(run, judge_id: str, model: str, verdicts: list[Verdict]) -> dict    # validates verbatim quotes, mutates run
def finalize(run, *, samples_required=3) -> dict                              # majority, ties -> uncertain
def observations_from_final(run) -> list[Observation]                         # judged KPIs
# judges.py: Judge protocol + ClaudeCliJudge (claude -p --output-format json --json-schema), AnthropicApiJudge,
#   OpenAICompatibleJudge, SamplingJudge (raises NotSupported). Tests use fakes only.
# report.py
def build_report(run: RunRecord, scores: RunScores) -> tuple[dict, str]   # report.json dict, self-contained HTML
def write_report(store, run_id) -> dict   # {"report_json": path, "report_html": path}
```

## WS5 MCP + CLI + plugin (later)
`engagement/{service,mcp_server,cli}.py`, `engagement/__init__.py` exports, `.claude-plugin/plugin.json`, `.mcp.json`, `skills/**`, `agents/**`, `tests/test_engagement_mcp.py`.
Tools: audit_shop, get_run, run_journey, journey_act, journey_finish, get_judgment_tasks, submit_judgments, finalize_judgments, score_run, get_report, list_runs.


## Coordinator decisions after phase A (binding for WS1-WS3)
- Units: KB = bytes / 1000 (anchors.json conventions are the source of truth); % values are 0-100.
- Checkout evidence: when checkout_entry was reached, checks.py takes FAI.GUEST_CHECKOUT and FAI.FORCED_ACCOUNT evidence from the checkout_entry page(s) only; cart-page rows for these two KPIs are emitted only when checkout_entry was not reached (or was not assessable).
- audit.js forms.login_required = true only for a blocking gate: a login/registration form (password field or required account creation) with no visible guest path on that page. An optional "Hai già un account? Accedi" / "Already have an account? Sign in" link does NOT set it. Pin both cases with fixtures.
- Real APIs from phase A (read the code): browser.capture_screenshot(transport, session, ...) for screenshots outside observe (stall retry); CdpError carries .data, -32001 = session gone (crashed renderers: open a new tab in the same context and record NotAssessable reason "renderer_crashed"); HarnessTransport holds an exclusive per-daemon lock until close() (always close transports); transport.discard(session_id) after closing a target; chrome.close_all()/sweep_stale_profiles(); scoring.score_run(run, journeys=[journey_run, ...]) merges journey runs into an audit; report.write_report(store, run_id, journey_run_ids=...); judgments.make_tasks/submit/finalize/observations_from_final; JudgmentState.skipped; DprScore coverage fields.
- Always run engagement pages inside profiles.new_context() contexts (downloads and storage stay isolated).


## Coordinator decisions after phase B/C (real APIs; binding for WS5 and docs)
- JourneyRunner (journey.py): start(url, goal, *, oracle, oracle_params=None, profile="mobile", policy="host", max_steps=40,
  optimal_steps=None, optimal_pages=None) -> {run_id, status, observation}; observation() -> {observation_id, url, title,
  page_type, text_excerpt, elements, controls, step, budget_left, guard_notes, status[, omitted_elements]};
  act(operation, target=None, text=None, *, observation_id: int) -> {executed, stale, refused, page_changed, step_metrics,
  status, observation} (observation_id REQUIRED: the id of the latest delivered observation; any other id returns
  stale=True and executes nothing; never resend after stale, read the returned observation and choose again);
  run_auto() for policy "typesafe"; finish(status: "done"|"blocked"|None) -> {run_id, status, verification, friction,
  steps, steps_path}; close() for an abandoned journey. HostPolicy.offer(page, observation_id) / set(operation, target,
  text, observation_id). friction.metrics(steps, *, optimal_steps, optimal_pages, success, profile, unverified).
  oracles.verify(..., steps=); cart oracles return "page" too. MCP journey_act must take observation_id (required).
- steps.jsonl may hold operation "NAVIGATION" records (self-navigation between steps; not an action).
- Crawler checkout entry: via the cart's guest control when forms.guest_option is true and an observed `guest` control
  leads to a same-site checkout URL (or has no href), else via the observed checkout CTA; probes.checkout_entry.via in
  {guest, checkout_cta}; FAI.CHECKOUT_FIELDS and rule d are measured on the step so reached.
- PageCollector.close(browser) closes a page (tab, frames, sessions, events); load_page(browser, href, ...) reaches stage
  pages; vitals.speculative / soft_navigation records -> not_applicable:speculative_navigation / same_document.
- Notes owed to docs and WS5 are collected in research/owed-notes.md.

- Rule d / login gate (Fable ruling, binding for WS1 audit.js and docs): a required registration password is a gate even beside an
  address form; a current-password login box beside an entry path is not; rule d consumes forms.guest_option and
  forms.login_required only.
- Coordinator acknowledges the test-only edits to WS0/WS1/WS3 test files that scoped shop_server.requests per test.
- Journey verification: checks.navigation_error = {url, error} whenever the journey ended on a page that did not load; then,
  without a passing oracle, passed None with not_assessable navigation_error and a run.not_assessable record; run status failed
  when the start page itself did not load and nothing executed, else partial. passed None with not_assessable page_unreadable
  when the guard withheld controls of a page whose audit failed and the oracle did not pass. NAVIGATION records carry the
  page_type of the page they left.


## Jev roles: contracts as built (WS-A judge, WS-B pilot, WS-C crawler, WS-D surface, WS-E docs)

Jev (TypeSafe's choice model) is again the default journey pilot, the judge of the operational rubrics and the audit
crawler's fallback; Claude judges the perception rubrics and Jev's escalations and pilots without a key. The real code
is the source of truth; this section was written from it (design: research/jev-roles-design-fable.md; binding
decision: research/jev-roles-decision.md). Every role goes through the original `model.choose` / `post_json` /
`validate_choice` / `action_space`; none re-implements them, and `model.py`, `questions.py`, `agent.py` and
`snapshot.js` are unchanged.

Rules that hold for every Jev role: Jev picks an observed element index, a label or an offered snippet id, never a
selector, URL or text; the code consumes only the head that matches the choice; a browser mutation is never retried
and execution is logged before its result is observed; tests monkeypatch `jev_ultrafast.model.post_json` (or the
agent's `choose`) with fakes that validate the request body; no live Jev number is published until
`scripts/smoke_engagement.py` has run and its `smoke_summary.json` is committed.

### Judgments (judgments.py, judges.py, rubrics/*.json)

```python
# rubrics: every rubrics/*.json has "version": "rubrics.v2" and "judge": "jev" | "claude"
#   jev:    returns_clarity, hidden_subscription, authority, social_proof, trick_questions
#   claude: confirmshaming, value_prop            (perception: tone, perceived clarity)
# judgments.py
RUBRICS_VERSION = "rubrics.v2"; JUDGES = ("jev", "claude")
JEV_BACKEND = "jev"; JEV_JUDGE_ID = "jev"         # submit() reserves judge_id "jev" to Jev's cache namespace
JEV_PROMPT_PREFIX = "jev:"                          # prompt_id(JevJudge) = "jev:" + request fingerprint
RETRY_REASONS = ("request_failed", "invalid_response")   # escalations without a Jev reading: Jev may try again
def routing(task, rubrics=None) -> "jev" | "claude"      # read from the rubric at judge time, never copied into tasks;
                                                         # a task of another rubrics version or unknown rubric -> "claude"
def settle(run, task_ids, *, samples_required=1) -> int  # lowers samples_required of open tasks, never raises, never final
def retryable(task) -> bool                              # no escalation, or one in RETRY_REASONS
def escalate(run, escalations: dict) -> int              # task["escalation"] = {from, model, label, probability, reason, at}
                                                         # keeps samples_required; no verdict stored; final/permanent ones untouched
# required_samples / finalize: a final task keeps its stored result whatever samples_required a later call asks;
# run_judges and service.judge_with check required_samples only for tasks that are not final.
# JudgmentTask.escalation.reason: low_probability | position_flip | no_evidence (Jev's reading, permanent) |
#   a submit() rejection reason | request_failed | invalid_response (label/probability None, retried)

# judges.py
SYSTEMONE = "https://api.typesafe.ai/v1/systemone"; JEV_MIN_P = 0.6
def jev_min_probability(value=None) -> float            # value, else env JEV_JUDGE_MIN_P, else 0.6; clamped 0.5..0.95
class JevJudge:                                          # Judge protocol: judge_id, model, backend = "jev", judge(tasks)
    def __init__(self, judge_id="jev", model=None, *, api_key=None, min_probability=None, post=None)
        # model: arg, else env TYPESAFE_MODEL, else "jev-latest"; api_key: arg, else env TYPESAFE_API_KEY;
        # post(url, key, body) defaults to jev_ultrafast.model.post_json (looked up at call time)
    def request(self, tasks) -> dict                     # the systemone body for ONE page's tasks (pure)
    def read(self, task, answers, model) -> (verdict | None, escalation | None)
    def judge(self, tasks, *, budget_s=None) -> list     # accepted verdicts; fills .escalations {task_id: ...}, .calls
                                                         # [{page_id, tasks, model, usage, latency_ms}], .errors, .pages_left
def jev_tasks(run, rubrics=None) -> list                 # routed to Jev, not final, no verdict from any judge, retryable
def jev_prompt_version() -> str                          # sha256[:16] of request() for a fixed synthetic page
def judge_with_jev(run, judge=None, *, use_cache=True, budget_s=None) -> dict
    # {"available", "reason" (None | "no_typesafe_key"), "requests", "latency_ms", "input_tokens", "model", "judged",
    #  "reused", "escalated" {reason: n}, "errors", "finalize", "open_tasks", "pages_left"}
    # without a key: available False, nothing changes. Writes run["model_calls"]["judge_jev"] =
    # {requests, latency_ms, input_tokens, model}, added up across calls. run_judges() refuses a Jev judge.
```

Jev request (one per page; tasks grouped by `page_id`; state has no URL and no run id):

```text
state:     {"page": {page_type, stage, locale}, "tasks": {<rubric_id or task_id>: {"snippets": {<snippet_id>: {kind, text}}}}}
questions: label:<task_id>               choice, criteria = the rubric's labels (label -> description), instructions
                                         {question, snippets: "`tasks.<key>.snippets`", rules: JEV_JUDGE_RULES}
           label_rev:<task_id>           the same with the labels in reversed order (position-bias check)
           evidence:<task_id>:<label>    for each label NOT in no_quote_labels: choice, criteria = the snippet ids (null)
                                         plus a "none" option (JEV_NONE), instructions {premise: "Assume the answer is
                                         '<label>': <description>", question, rules: JEV_EVIDENCE_RULES}
read order: label (p = probability of the choice) -> p >= min_probability, else escalate low_probability (reversed head
            NOT read) -> label_rev must choose the same label, else position_flip -> for a quote label only
            evidence:<task_id>:<chosen label> must pick a snippet, else no_evidence. Verdict: judge_id "jev", model =
            the versioned id of the answer, confidence = p, evidence = [{snippet_id, quote: the snippet's whole text}].
```

Cache: namespace `jev:<jev_prompt_version()>`; Jev's cache is read only for a pinned model (`jev-<version>`) and a cached
sample counts only at or above today's floor. Scoring is unchanged (`observations_from_final`, `llm_share`): a Jev
final has `samples` 1, `agreement` 1.0, `confidence` p, `models` [the versioned id]; a confidence-valued DPR KPI is
`0.8 x agreement`. `report.py`: `judgments` gains `by_model`, `single_sample`, `escalated`, `escalated_final`,
`jev_kpis`, `jev_models`, `claude_kpis`; the footer names who judged what and `report["model_calls"]` is printed as
"Richieste TypeSafe: ...". `tests/fixtures/runs/*` are `rubrics.v2`.

### Journey pilot (journey.py, service.py)

```python
# journey.py (JourneyRunner.start keeps policy="host"; POLICIES = ("host", "typesafe") unchanged)
def text_model() -> str | None       # TEXT_MODEL (default TEXT_HELPER) if TEXT_MODEL_API_KEY is set, else None
# policy "typesafe" = Agent + model.choose (one request per decision) + model.field_text for TYPE_TEXT, untouched.
# No TEXT_MODEL_API_KEY: TYPE_TEXT stays offered; a TYPE_TEXT Jev chooses is refused before any input (step flags
#   guard_blocked + text_withheld, note TEXT_WITHHELD, warning "text_helper_unavailable: ..."), then fill actions are
#   withheld; checks.text_withheld_at = {url, label, step[, refusal]}. A guard-refused text -> "text_refused".
# verification.checks.not_assessable (no passing oracle) may be "text_helper_unavailable" or "text_refused".
# journey record (additive keys of schemas.JourneyRecord), written by finish() / close():
#   policy (resolved), policy_requested (service writes it), text_helper (model name | None | "host"),
#   model_calls {choose, text, stale_or_refused, failed[, model]}, timing_ms {decision, text, site, wall},
#   usage {input_tokens, output_tokens} ({} for the host)
# service.py
POLICIES = ("auto", "host", "typesafe"); BACKENDS = ("cli", "api", "openai", "jev")
def resolve_policy(policy) -> "host" | "typesafe"        # auto -> typesafe iff TYPESAFE_API_KEY is set
def server_keys() -> {"typesafe_key": bool, "text_helper": str | None}   # never a value; get_run()["server"]
EngagementService.run_journey(url, goal, oracle, oracle_params=None, profile="mobile", policy="auto", max_steps=40, ...)
    # typesafe: background thread (run_auto then finish) -> {run_id, status: "running", policy, policy_requested,
    #   text_helper, next: "wait_run(...)"}; wait=True returns the finish summary. host: {run_id, status, observation,
    #   policy, policy_requested}. journey_act on a typesafe run raises "not open".
    # _finish_summary / journey_finish: policy, policy_requested, text_helper, model_calls, timing_ms, usage, and a
    #   `next` that names a host journey when Jev could not take a single decision (nothing executed).
EngagementService.judge_with_jev(run_id, *, model=None, use_cache=True, budget_s=None) -> dict
    # {run_id, backend: "jev", available, typesafe_key, reason, model, samples: 1, requests, latency_ms, input_tokens,
    #  accepted, reused, escalated {reason: n}, errors, errors_total, error_groups [{error, pages, tasks}],
    #  rejected_total, per_task [{task_id, rubric_id, label, probability, evidence, final, escalated}], finalize,
    #  open_tasks, pages_left, next}; `next` names judge_with_jev only while never-asked pages remain AND the call
    #  lowered pages_left, else get_judgment_tasks. Runs under RunStore.update (the run lock) for at most
    #  budget_s + one request. judge_with(run_id, "jev", ...) delegates to it (samples ignored).
get_judgment_tasks items: {task_id, rubric_id, samples_submitted, final, judge, escalation?: {from, reason}} ; page keys
    open_tasks, missing_samples. get_run(): model_calls (crawler_fallback, judge_jev), server, judgments {escalated,
    escalated_reasons}, journey brief with the pilot fields.
```

### Crawler fallback (crawler.py, audit.py)

```python
GOALS = {plp, pdp, cart, add_to_cart, select_variant, checkout_entry}   # fixed generic English goals, no page text/site values
discover_funnel(collector, settings, *, profile, guard, progress=None, jev_fallback=False)   # audit passes bool(env TYPESAFE_API_KEY)
def jev_pick(page, guard, page_type, goal, accept=None) -> (action | None, probe_fields)
    # one jev_ultrafast.model.choose(state, goal, []) over guard.filter_actions click actions that have an accessible
    # name, are no field's click and pass `accept`; consumes only operation == "CLICK" and its target;
    # reasons: no_click_actions (nothing asked) | jev_error | jev_done | jev_blocked
Tab.chooser(record, page, purpose, labels, accept) -> (action | None, probe | None)   # asked by Tab.click on a lexicon miss
Crawl.fallbacks: {purpose: 1}  # one request per lookup, at most 6 per profile; a second miss -> fallback_exhausted
NO_REQUEST = (no_click_actions, fallback_exhausted, not_observed, max_pages, consent_blocking)  # reasons given before any request
PageRecord.probes["jev_fallback"]: list of {stage, purpose, goal, model, latency_ms, usage, operation, target, label, href,
    probability, confidence, executed, reason[, url, reload, effect, ...]} on the page the lookup was asked on; page note
    "jev_fallback <purpose>: ..."; a chosen click is logged with note "jev_fallback" (purpose open_<stage> for links).
run["model_calls"]["crawler_fallback"] = {requests, latency_ms, input_tokens, model, stages: [{profile, page_id, stage,
    purpose, operation, label, probability, executed, reason}]}  # requests = lookups that attempted one (not NO_REQUEST)
run["warnings"]: "<profile>: <stage> found through the Jev fallback: not reproducible across runs" and
    "<profile>: <stage> reached after a Jev pick (<lookups>): not reproducible across runs"
```

Stays deterministic: consent, overlays, search probe, repeats, the guest path, deception tests. The funnel's own tests
(`is_listing`, `is_product`, `is_cart`, a checkout page) decide whatever Jev picked; CheckoutGuard filters the offer and
checks again in `Tab.act`; the observation is viewport-only (snapshot.js: centre inside the viewport, at most 250).

### Surface: MCP, CLI, plugin, smoke (mcp_server.py, cli.py, .claude-plugin/plugin.json, skills, agents)

- MCP: 13 tools (`audit_shop, wait_run, get_run, run_journey, journey_act, journey_finish, judge_with_jev,
  get_judgment_tasks, submit_judgments, finalize_judgments, score_run, get_report, list_runs`).
  `run_journey(policy: Literal["auto", "host", "typesafe"] = "auto")`. `judge_with_jev(run_id)` calls
  `service.judge_with_jev(run_id, budget_s=JEV_BUDGET_S)` with `JEV_BUDGET_S = 30` (budget + three 25 s attempts +
  backoff stays below `MAX_WAIT_S` 110 and the host's 2-minute tool timeout).
- Keys: `mcp_server.load_keys()` loads the `KEY=value` file named by `JEV_ENGAGEMENT_ENV` with
  `cli.load_environment(path)` (a non-empty inherited variable wins; `export `, quotes, inline ` # comments`, a BOM and
  non-UTF-8 bytes are handled; an unreadable file is logged by path, never by content), then returns `server_keys()`.
  `plugin.json` env: `BH_TAB_MARKER`, `UV_PROJECT_ENVIRONMENT`, `JEV_ENGAGEMENT_ARTIFACTS`, `JEV_ENGAGEMENT_ENV` =
  `${CLAUDE_PLUGIN_DATA}/.env`. Whether Claude Code forwards the user's shell variables to a plugin server is not
  verified; the file is the guaranteed path. The CLI reads `./.env` (`main()`).
- CLI: `audit --judge none|cli|api|openai|jev`; `judge --backend cli|api|openai|jev` (default `cli`); `--policy`
  stays `typesafe` only (`audit URL --policy host` exits 2). Without `TYPESAFE_API_KEY`, `--judge jev` /
  `--backend jev` raise `Failure(NO_JEV)` (exit 1) before any browser starts; the crawler fallback is on whenever the
  key is set (no other switch). `audit --judge jev` prints the open tasks and the `judge --backend cli` + `score` hint.
- Skill `shop-readiness`: reads `get_run().server`; `run_journey(policy="auto")`; `wait_run` + `journey_finish` for a Jev
  journey (host re-run only when Jev could not decide); step 3.0 `judge_with_jev` loop; Claude judges only pages with
  `missing_samples` > 0; output block adds "Modelli: pilota ..., giudici ..., richieste TypeSafe ..." and "Passati a
  Claude". Agent `engagement-judge`: skips final tasks (Jev's); an escalated task is judged like any open one.
- `scripts/smoke_engagement.py` (not run by pytest, paid): `--url`, `--goal`, `--oracle`, `--oracle-param K=V`,
  `--profile`, `--browser`, `--headed`, `--locale`, `--max-steps`, `--artifacts`, `--audit-run RUN_ID`,
  `--skip audit|judge|journey|score`, `--repeat N`, `--dump-requests DIR`. Exit 0 all required checks pass, 1 a check
  fails, 2 without `TYPESAFE_API_KEY` or with a bad `--audit-run`/`--url`. Writes `smoke_summary.json` (requests,
  tokens, p50/p95 latency, per-rubric probabilities, checks) into the audit run directory. Fixture mode hides the
  lexicon's category links (`crawler.listing_candidates` -> []) and gives Chromium a dead proxy.

### Tests (offline)

New tests patch `jev_ultrafast.model.post_json` (judge, crawler fallback; `monkeypatch.setenv("TYPESAFE_API_KEY", ...)`)
or `agent.choose` (journey) with fakes that validate the request body and return `{"model": "jev-1.13.0", "answers":
{...}, "usage": {...}}`; `tests/test_engagement_audit.py`, `_cli.py` and `_mcp.py` scrub the model keys from the
environment with autouse fixtures (so a developer's real key never turns host journeys into paid runs or makes an audit
ask Jev); the judgments tests pass `api_key` explicitly or set/delete the key per test; `tests/conftest.py` has no global
scrub.
`tests/test_engagement_docs.py` is unchanged (no KPI change); the judged KPIs' rows in docs/engagement-kpi.md section 4
name their judge in the producer column.
