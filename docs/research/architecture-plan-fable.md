I have everything I need from the code and from the external sources. Summary of the verified facts that shape the design, then the plan.

## Verified facts that drive the decisions

**browser_harness 0.1.13 (source read from GitHub main, consistent with the locked version):**
- The daemon resolves the Chrome endpoint in this order: `BU_CDP_WS` (direct ws URL), `BU_CDP_URL` (HTTP DevTools endpoint, resolved via `/json/version`, 30 s deadline), then local discovery via `DevToolsActivePort` files, then ports 9222/9223. So it **can** attach to a Chromium we launch ourselves with `--remote-debugging-port` and `--user-data-dir`. `ensure_daemon(name=..., env={"BU_CDP_URL": ...})` supports a separately named daemon.
- Caveats that matter for measurement: event buffer is `deque(maxlen=500)` (lossy for `Network.*` events on a real shop page load), the daemon prefixes `document.title` with a horse emoji unless `BH_TAB_MARKER=0`, IPC responses time out at 5 s (`_response_timeout` kwarg), the daemon enables Page/DOM/Runtime/Network only on its own session, and `Target.*` calls are routed browser-level. No headless launch support in `_launch_browser()`.
- `websockets==15.0.1` is already locked (transitive); it has a sync client (`websockets.sync.client.connect`, `recv(timeout=...)`).
- This container: Chromium 141 at `/opt/pw-browsers/chromium-1194/chrome-linux/chrome` (plus `chromium_headless_shell-1194`), Python 3.13, uv 0.11, node 22. No Chrome running, `.venv` absent.

**Claude Code as MCP client (Oct 2026):** MCP sampling is **not** implemented (anthropics/claude-code#1785 open since June 2025; #31893 "spec compliance" closed as not planned; canimcp.dev lists sampling as unverified). Elicitation (URL mode) and structured output (`anthropic/outputSchema`) are supported. Tool output cap: 25,000 tokens by default (`MAX_MCP_OUTPUT_TOKENS`), text results >50k chars are spilled to disk; tool calls >2 min are auto-backgrounded. `claude -p` without `--bare` uses the subscription login; `--bare` requires `ANTHROPIC_API_KEY`; `--output-format json --json-schema` gives validated structured output. Plugin layout: `.claude-plugin/plugin.json`, `.mcp.json` at plugin root with `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_PLUGIN_DATA}` substitution, `skills/<name>/SKILL.md`, `agents/<name>.md`; dev loading via `claude --plugin-dir`; `claude plugin validate`.

**MCP Python SDK:** v2.3.0 is current (`from mcp.server import MCPServer`; `from mcp.types import ToolAnnotations`; return-type annotation becomes the output schema; `mcp.run()` defaults to stdio; v2 depends on `httpx2`, a different distribution than our `httpx`, so no conflict). v1.30 (FastMCP API) is still published in parallel.

---

# Implementation plan: Jev Ultrafast → Engagement Readiness auditor

## 1. Architecture decisions

### 1.1 Keep CDP; do not adopt Playwright
Every metric in the research list is a CDP primitive or an in-page API: `Page.addScriptToEvaluateOnNewDocument` (PerformanceObserver), `Network.enable`/`emulateNetworkConditions`/`setCacheDisabled`, `Emulation.setDeviceMetricsOverride`/`setCPUThrottlingRate`/`setUserAgentOverride`, `Target.createBrowserContext`, `Page.captureScreenshot`, `Runtime.exceptionThrown`, `Log.entryAdded`. Playwright would add a second action-execution path that bypasses the freshness guards in `browser.py` and the "never retry a mutation" rule in AGENTS.md, plus a ~100 MB dependency. What is missing is only a transport that is not lossy and a way to launch a browser we control. That is ~150 lines.

### 1.2 Transport layer with two implementations behind one `cdp()` shape
New `jev_ultrafast/engagement/transport.py`:

```python
class CdpTransport(Protocol):
    def call(self, method: str, session_id: str | None = None, timeout: float = 5.0, **params) -> dict: ...
    def events(self) -> list[dict]:           # drain; each {"method", "params", "session_id"}
    def close(self) -> None: ...

class DirectTransport(CdpTransport):
    """websockets.sync client to a ws:// DevTools URL; reader thread demultiplexes
    responses by id into per-call futures and appends events to an unbounded queue."""
    def __init__(self, ws_url: str, max_events: int = 200_000): ...

class HarnessTransport(CdpTransport):
    """Wraps browser_harness.helpers.cdp / drain_events; sets BH_TAB_MARKER=0
    before ensure_daemon(name=..., env=...). Lossy events (500 ring buffer): collectors
    fall back to in-page Resource Timing and mark network KPIs source='resource_timing'."""

def open_transport(settings) -> tuple[CdpTransport, "LaunchedChrome | None"]:
    # browser="auto": BU_CDP_WS/BU_CDP_URL set -> Direct to it;
    # elif find_chromium() -> launch own headless Chromium (Direct);
    # elif browser_harness importable -> Harness (user's Chrome).
    # browser="launch" | "harness" | "cdp:<url>" force one.
```

New `jev_ultrafast/engagement/chrome.py`: `find_chromium()` (order: `JEV_CHROME_PATH`, `CHROME_PATH`, `BH_CHROME_PATH`, PATH names, `/opt/pw-browsers/chromium-*/chrome-linux/chrome`, macOS/Windows app paths) and `launch_chromium(headless=True, window=(w,h), lang="it-IT", extra_args=JEV_CHROME_ARGS) -> LaunchedChrome(ws_url, process, user_data_dir)` using `--headless=new --remote-debugging-port=0 --user-data-dir=<tmp> --no-first-run --no-default-browser-check --disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows --window-size=W,H`; ws URL read from `<user_data_dir>/DevToolsActivePort` (poll ≤15 s). `close()` kills and removes the temp profile. Headless-new reports `document.visibilityState === "visible"`, which is required for FCP/LCP; this is the default for audits.

### 1.3 Minimal changes to the existing loop (keep it readable)
`jev_ultrafast/browser.py` (owned by WS0):
- `Browser.__init__(self, url, *, transport=None, metrics=None, browser_context_id=None, background=True, navigate=True)`. When `transport` is given, skip `ensure_daemon()`, route `self.call`/`close` through `transport.call`, create the target inside `browser_context_id`, apply `metrics` (default stays 1120x780 desktop so the demo is unchanged).
- `browser_operation(request, transport=None)`: `send = transport.call if transport else cdp`. Default path unchanged, so `tests/test_agent.py` and `scripts/measure_flights.py` (which monkeypatch module-level `cdp`) keep working.

`jev_ultrafast/agent.py` (owned by WS3): `Agent.__init__(url, goals, *, browser=None, policy=None, text_policy=None, record_dir=None, screenshots=False)`; `predict` uses `(self.policy or choose)(page, goal, history)`; the fill branch uses `(self.text_policy or field_text)(context)`. Nothing else changes. This gives host-driven decisions without touching the invariant "model emits only indices".

### 1.4 Subpackage layout

```
jev_ultrafast/engagement/
  __init__.py        public API: audit_shop(), JourneyRunner, score_run(), build_report(), RunStore
  settings.py        EngagementSettings (browser, profiles, locale, consent policy, budgets, artifacts dir, repeats)
  schemas.py         TypedDicts: PageRecord, AuditPayload, Vitals, NetworkSummary, Observation, JudgmentTask,
                     Verdict, ScoreOutput, JourneyStep (single source of truth for all workstreams)
  transport.py       CdpTransport, DirectTransport, HarnessTransport, open_transport
  chrome.py          find_chromium, launch_chromium
  profiles.py        DEVICE_PROFILES (versioned data) + apply_profile(transport, session, profile) + hygiene
  vitals.js          injected on new document; window.__jevVitals {read(), mark(), since(t), quiet(ms)}
  audit.js           (lexicon => {...}) one-call page extractor -> AuditPayload
  lexicon.py         LEXICON = {"it": {...}, "en": {...}} multilingual label/URL patterns (data, extensible)
  collectors.py      PageCollector: installs vitals.js, enables Network/Log/Runtime, accumulates events,
                     load_page(url) -> PageRecord (waits for load + network quiet, reads vitals, audit, screenshot)
  pagetypes.py       classify(audit_payload, url) -> {"type", "confidence", "signals"}
  crawler.py         discover_funnel(): home -> plp -> pdp -> (add to cart) -> cart -> checkout step 1, stop
  checks.py          deterministic KPIs from PageRecords -> list[Observation]
  deception.py       countdown reset, low-stock reload equality, sneak-into-basket diff, pre-checked add-ons,
                     consent asymmetry (second isolated context)
  audit.py           audit_shop(settings) orchestration per profile, writes run.json/snapshots
  safety.py          checkout boundary guard + forbidden-field/label rules (shared by crawler and journey)
  journey.py         JourneyRunner (host-driven or TypeSafe policy), step instrumentation -> steps.jsonl
  friction.py        derive friction KPIs from steps.jsonl (dead/rage/backtrack/overlay/lostness/time)
  oracles.py         closed set of independent outcome checks (cart_contains_item_under_price, pdp_reached, ...)
  judgments.py       task generation from snapshots, verdict validation (verbatim quote check), majority
                     aggregation, cache sha256(rubric_id+version+snippet)
  rubrics/*.json     closed-label rubrics, versioned (dark_patterns.v1.json, trust.v1.json, clarity.v1.json, persuasion.v1.json)
  judges.py          fallback judges outside the MCP host flow: ClaudeCliJudge (claude -p --json-schema),
                     AnthropicApiJudge, OpenAICompatibleJudge; SamplingJudge stub (disabled until CC supports it)
  scoring.py         normalize(), sub_indices(), dpr(), ers(), confidence()  — pure functions over run.json
  anchors.json       versioned anchors + weights + ownership table
  store.py           RunStore: artifacts/engagement/<host>/<ts>/ {run.json, steps.jsonl, snapshots/, shots/, report.json, report.html}
  report.py          build_report(run) -> report.json + self-contained report.html (inline CSS, no external assets)
  service.py         EngagementService: the API the MCP server and CLI both call (browser factory injectable for tests)
  mcp_server.py      MCPServer("jev-engagement") tools, stdio
  cli.py             `jev-engage audit|journey|judge|score|report|list`
.claude-plugin/plugin.json    plugin root = repository root (so ${CLAUDE_PLUGIN_ROOT} is the uv project)
.mcp.json
skills/shop-readiness/SKILL.md, skills/journey-driver/SKILL.md (user-invocable: false), skills/engagement-judge/SKILL.md
agents/engagement-judge.md
tests/fixtures/shop/{index,category,product,cart,checkout,dark,challenge}.html (+ assets)
tests/test_engagement_*.py
docs/engagement-kpi.md (Italian catalogue), docs/engagement-design.md
```

Dependencies (`pyproject.toml`, WS0): core adds `mcp>=2.3,<3`, `websockets>=13,<16` (direct import now), `pillow>=11,<13` moved from dev to core (visual complexity + report thumbnails). Scripts: `jev-engage`, `jev-engage-mcp`. Keep `browser-harness==0.1.13`.

### 1.5 Measurement hygiene (profiles.py, data not code)
```json
{"version": "profiles.v1",
 "mobile":  {"metrics": {"width": 390, "height": 844, "deviceScaleFactor": 3, "mobile": true},
             "network": {"latency": 150, "downloadThroughput": 209715, "uploadThroughput": 96000},
             "cpu_rate": 4, "user_agent": "<Chrome mobile UA without 'Headless'>", "touch": true},
 "desktop": {"metrics": {"width": 1366, "height": 768, "deviceScaleFactor": 1, "mobile": false},
             "network": null, "cpu_rate": 1, "user_agent": "<Chrome desktop UA without 'Headless'>"}}
```
(1.6 Mbps = 209,715 B/s; 750 Kbps = 96,000 B/s; Lighthouse's own slow-4G uses 1,638.4/675 Kbps, note it in the doc.) Per profile: `Target.createBrowserContext(disposeOnDetach=True)` → one context per profile for the funnel (cookies must persist for add-to-cart), `Network.setCacheDisabled(True)`, `Network.clearBrowserCache/Cookies` at context start, `Emulation.setFocusEmulationEnabled`, `Emulation.setTouchEmulationEnabled` for mobile, `Page.setLifecycleEventsEnabled`. `run.json` records `visibility_state_at_load` per page; if `hidden`, FCP/LCP are marked `not_assessable(reason="background_tab")` instead of 0.

### 1.6 Judgment protocol: host judges, then submits (sampling is the future hook)
Because Claude Code cannot serve `sampling/createMessage`, the tool never calls an LLM itself in the plugin flow. The server produces **closed-label tasks over extracted snippets**; the host (Claude Code, via the skill and three Sonnet judge subagents) returns verdicts; the server validates each evidence quote verbatim against the stored snapshot text, aggregates by majority, caches by `sha256(rubric_id|rubric_version|snippet_text)`, and only then scores. Fallbacks for CLI use: `claude -p --output-format json --json-schema` (subscription, no `--bare`), Anthropic Messages API, OpenAI-compatible endpoint. A `SamplingJudge` class exists behind the same `Judge` interface but raises `NotSupported` unless the client declares the sampling capability at runtime.

## 2. MCP server and plugin design

### 2.1 Tools (`mcp_server.py`, thin wrappers over `service.py`)
All tools return small dicts (summaries + paths); large payloads stay on disk. Annotations via `ToolAnnotations`.

| Tool | Input | Output | Annotations |
|---|---|---|---|
| `audit_shop` | `url`, `profiles=["mobile","desktop"]`, `stages=["home","plp","pdp","cart","checkout_entry"]`, `browser="auto"`, `locale="it"`, `consent="auto"`, `repeats=1`, `run_id=None` | `{run_id, artifacts_dir, status, pages:[{page_id, stage, profile, url, type, confidence, lcp_ms, cls, bytes}], not_assessable:[{stage, reason}], judgment_tasks: n, warnings[]}` | read_only=false, open_world=true, destructive=false |
| `get_run` | `run_id` | `{status, kind, site, pages_summary, journey_summary, judgments:{tasks, pending, final}, scores_available}` | read_only |
| `run_journey` | `url`, `goal`, `oracle`, `oracle_params={}`, `profile="mobile"`, `policy="host"`, `max_steps=40`, `browser="auto"` | `{run_id, status:"awaiting_decision", observation}` where `observation = {url, title, text_excerpt(≤3000), elements:[{index,label,role,value,operations,checked?}], controls:["WAIT","DONE","BLOCKED"], step, budget_left, guard_notes[]}` | open_world=true |
| `journey_act` | `run_id`, `operation` ∈ CLICK/TYPE_TEXT/SELECT/SCROLL_UP/SCROLL_DOWN/WAIT/DONE/BLOCKED, `target` (index or `"3:2"` for select options), `text` (only for TYPE_TEXT) | `{executed, stale:bool, page_changed, step_metrics:{first_response_ms, mutations, requests, errors, shifts}, status, observation}` | destructive=false (mutates a cart only) |
| `journey_finish` | `run_id`, `status` | `{verification:{passed, checks}, friction:{...}, steps_path}` | |
| `get_judgment_tasks` | `run_id`, `cursor=None`, `limit=15`, `rubric_id=None` | `{tasks:[JudgmentTask], next_cursor, remaining}` (snippets ≤600 chars; ≤15 tasks per page to stay far below 25k tokens) | read_only |
| `submit_judgments` | `run_id`, `judge_id`, `model`, `verdicts:[Verdict]` | `{accepted:n, rejected:[{task_id, reason}], tasks_remaining, samples_complete:n}` | |
| `finalize_judgments` | `run_id`, `samples_required=3` | `{final:n, disagreements:[task_id], pending:[task_id]}` | |
| `score_run` | `run_id` | `ScoreOutput` summary (sub-indices, ERS, coverage, grade, top 10 risk signals) | |
| `get_report` | `run_id`, `format="summary"|"kpis"|"paths"` | summary text/kpis table or `{report_html, report_json}` paths | read_only |
| `list_runs` | `host=None`, `limit=20` | `[{run_id, host, kind, created_at, ers}]` | read_only |

Resource (optional): `engagement://runs/{run_id}/report.json`.

Transport: stdio (`mcp.run()`), logging to stderr via `logging`. `.mcp.json`:

```json
{"mcpServers": {"engagement": {
  "command": "uv",
  "args": ["run", "--project", "${CLAUDE_PLUGIN_ROOT}", "jev-engage-mcp"],
  "env": {"BH_TAB_MARKER": "0", "JEV_ENGAGEMENT_ARTIFACTS": "${CLAUDE_PLUGIN_DATA}/runs"}}}}
```
(`JEV_ENGAGEMENT_ARTIFACTS` falls back to `./artifacts/engagement` when unset, e.g. CLI in the repo.)

### 2.2 Plugin layout (repo root is the plugin root)
- `.claude-plugin/plugin.json`: `{"name":"jev-engagement","version":"0.1.0","description":"Engagement-readiness audit of e-commerce shops: performance, element availability, friction, trust/persuasion/dark-pattern risk signals"}`.
- `skills/shop-readiness/SKILL.md` (`/jev-engagement:shop-readiness <url> [goal]`, `disable-model-invocation: true`, `argument-hint: [url] [goal]`): orchestration: 1) `audit_shop`; 2) if a goal is given, `run_journey` and drive it using the `journey-driver` rules; 3) page through `get_judgment_tasks`, spawn three `engagement-judge` subagents (model sonnet) per batch, each submits with `judge_id` j1..j3 and its model id; 4) `finalize_judgments`, `score_run`; 5) present the summary using only the vocabulary "risk signals" / "predicted friction" and link `report.html`. Includes the wording rule that ERS is a readiness estimate, not measured engagement.
- `skills/journey-driver/SKILL.md` (`user-invocable: false`): the host-driven policy rules, adapted from `questions.NEXT_ACTION`/`TARGET`: choose only offered indices; TYPE_TEXT values derive from the goal/page (e.g., a product word seen on the page), never personal data, never payment data; stop with DONE only when the oracle condition is visibly met; never click labels matching the pay/place-order lexicon; prefer rejecting consent when a reject control is offered.
- `agents/engagement-judge.md`: Sonnet subagent, no tools; input = tasks JSON; output = verdict JSON matching the rubric labels, evidence quotes copied verbatim.
- Verify in WS5 how plugin MCP tools are named in `allowed-tools` (`mcp__plugin_jev-engagement_engagement__*` per the components doc) and pre-approve the read-only ones in the skill frontmatter.

### 2.3 Journey decision model: recommendation
MVP default inside Claude Code: **host-driven** (`policy="host"`). Rationale: no paid keys; reuses `model.action_space` and `Browser.act` guards unchanged; the host only emits an index; decision latency is excluded from site-attributable time because the step clock is browser-side (`performance.now()` marks). Cost: 2–6 s per step of host latency and non-reproducible decisions; therefore repeated runs for medians use `policy="typesafe"` from the CLI (`TYPESAFE_API_KEY` + `TEXT_MODEL_API_KEY`), which stays available unchanged. Both are the same `JourneyRunner`; the policy is a callable with the `choose()` return contract (`choice, operation, target, confidence, probabilities, latency_ms, usage, model`).

## 3. KPI list for MVP

Vocabulary: `det` = deterministic, `jud` = judged by host LLM on snippets, `jny` = journey only. Owner = the single sub-index that consumes it (ownership table lives in `anchors.json`). Units in brackets.

**PERF (weight 18)** per page and profile, aggregated as median across funnel pages (PDP and PLP weighted 2×):
PERF.TTFB [ms, det], PERF.FCP [ms, det], PERF.LCP [ms, det], PERF.CLS [score, det], PERF.CLS_POST_INPUT [score, det, sum of `hadRecentInput` shifts after our actions, jny], PERF.TBT_APPROX [ms, det, long-task excess over 50 ms until load+3 s], PERF.LOAF_COUNT [n, det], PERF.INP_SYNTH [ms, det, jny, max event-timing duration of our synthetic interactions, labelled "interaction latency", not CrUX-comparable], PERF.BYTES_TOTAL [KB, det], PERF.BYTES_JS [KB, det], PERF.REQUESTS [n, det], PERF.THIRD_PARTY_SHARE [%, det, eTLD+1 approximation], PERF.IMG_OVERSIZED [n, det, natural ≥2× display], PERF.CONSOLE_ERRORS [n, det], PERF.HTTP_ERRORS [n, det, 4xx/5xx], PERF.ACTION_RESPONSE_MS [ms, det, jny, median act→first mutation/response].

**FAI Friction/Ability (22):**
FAI.SEARCH_VISIBLE [bool, det, visible input above fold], FAI.SEARCH_WIDTH [px, det], FAI.SEARCH_AUTOCOMPLETE [bool, det, probe: type a word taken from an observed category label, wait ≤800 ms for listbox options], FAI.PLP_FILTERS [n, det], FAI.PLP_SORT [bool, det], FAI.PLP_RESULT_COUNT [bool, det], FAI.PLP_PAGINATION [enum, det], FAI.BREADCRUMBS [bool, det], FAI.PDP_CTA_ABOVE_FOLD [bool, det], FAI.PDP_VARIANTS [n, det], FAI.CART_EDITABLE [bool, det, qty/remove controls], FAI.CHECKOUT_FIELDS [n, det, visible required fields on step 1], FAI.GUEST_CHECKOUT [bool|unknown, det], FAI.FORCED_ACCOUNT [bool, det], FAI.AUTOCOMPLETE_ATTRS [%, det], FAI.TARGET_SIZE_24 / _44 [%, det], FAI.A11Y_BASIC [n, det: images without alt, inputs without label, missing lang; axe later], FAI.JOURNEY_SUCCESS [bool, det, jny, oracle], FAI.ACTIONS_TO_GOAL [n, jny], FAI.ACTIONS_RATIO [N/R, jny, R from the crawler path when the goal maps to the funnel], FAI.TIME_ON_TASK_SITE [ms, jny, sum of act→settle windows, excludes decision latency], FAI.DEAD_CLICK_RATE [%, jny], FAI.RAGE_EVENTS [n, jny, same node ≥3 consecutive without change], FAI.BACKTRACK_RATE [%, jny], FAI.LOSTNESS [0–1, jny, only when R known], FAI.OVERLAY_INTERRUPTIONS [n + coverage % + clicks to dismiss, det+jny], FAI.UNEXPECTED_NAV [n, jny, new targets/external hosts].

**TRI Trust & risk reduction (20):**
TRI.HTTPS [bool], TRI.MIXED_CONTENT [n], TRI.CONTACT_INFO [bool, email/phone/address regex], TRI.LEGAL_ID [bool, P.IVA/VAT regex], TRI.POLICY_LINKS [set: returns/shipping/privacy/terms visible on home + returns page resolved ≤2 clicks], TRI.JSONLD_RETURN_POLICY [bool], TRI.JSONLD_SHIPPING [bool], TRI.PAYMENT_LOGOS [n], TRI.REVIEWS_PRESENT [bool, aggregateRating or visible rating], TRI.REVIEW_COUNT [n], TRI.RATING_BAND [enum, Spiegel 4.0–4.7 best], TRI.DELIVERY_TIME_STATED [bool], TRI.RETURNS_CLARITY [jud, labels clear/vague/absent on returns snippet].

**PTI Price/cost transparency (15):**
PTI.PRICE_VISIBLE_PDP [bool], PTI.PRICE_JSONLD_MATCH [bool, visible vs offers.price], PTI.SHIPPING_COST_PRE_CHECKOUT [bool, PDP or cart], PTI.FREE_SHIPPING_THRESHOLD [bool], PTI.VAT_STATED [bool, "IVA inclusa"/"incl. VAT"], PTI.FUNNEL_PRICE_DELTA [%, PDP → cart → checkout step 1 total minus declared shipping], PTI.STRIKETHROUGH_LOWEST30 [bool|n/a, Omnibus statement present when a strikethrough price exists], PTI.UNEXPLAINED_FEES [n, det, cart/checkout lines not present on PDP].

**CCL Clarity & cognitive load (15):**
CCL.CTA_SALIENCE [score, det: contrast ratio, area, uniqueness of lexicon-matched primary CTA above fold], CCL.PRIMARY_CTA_COUNT_ABOVE_FOLD [n], CCL.CHOICE_LOAD [penalty only: PLP items ≥24 with filters+sort == 0], CCL.INFO_SCENT_GENERIC [%, det, nav links with generic labels], CCL.READABILITY [Gulpease for lang=it, Flesch for en], CCL.HEADINGS [h1==1 and ≥1 h2], CCL.FORM_LABELS [%], CCL.VISUAL_COMPLEXITY [colorfulness + edge density + compressibility from screenshot, provisional anchors], CCL.VALUE_PROP_CLARITY [jud on headline/CTA snippets].

**MPI Persuasion supply (10), genuine only:**
MPI.SCARCITY_SIGNALS [n, det lexicon, credited only if DPR.FAKE_LOW_STOCK not triggered], MPI.URGENCY_SIGNALS [n, det, credited only if DPR.COUNTDOWN_RESET not triggered], MPI.RECIPROCITY [n, det: free shipping/returns/gift], MPI.AUTHORITY [jud: certifications/press/badges on snippets], MPI.SOCIAL_PROOF_RICH [jud: UGC/testimonials beyond ratings].

**DPR Dark-pattern risk (penalty), severity × confidence, noisy-OR:**
DPR.COUNTDOWN_RESET [det, severity 0.9], DPR.FAKE_LOW_STOCK [det+jud, 0.6], DPR.SNEAK_INTO_BASKET [det, 0.9], DPR.PRECHECKED_PAID_ADDONS [det, 0.6], DPR.CONSENT_ASYMMETRY [det, 0.3], DPR.NAGGING_OVERLAYS [det, 0.3], DPR.CONFIRMSHAMING [jud, 0.6], DPR.TRICK_QUESTIONS [jud, 0.6], DPR.HIDDEN_SUBSCRIPTION [jud, 0.9].

**Deferred (document in the catalogue as "fase 2"):** axe-core full violations, Speed Index, real INP (CrUX API join), search result relevance, checkout steps beyond step 1, PLP/PDP image zoom quality, GA4/Clarity calibration join, Lighthouse-style simulated throttling, screenshot-based CTA detection, multi-language beyond it/en, repeat-visit (warm cache) performance.

## 4. Scoring (versioned data, pure functions)

`anchors.json` (version `anchors.v1`): per KPI `{"unit", "owner", "source", "direction", "points": [[value, score], ...], "weight"}` piecewise-linear; booleans map to `{true:100,false:0}` unless overridden; enums map explicitly. Initial anchors (published thresholds where they exist, editorial otherwise and flagged `"provisional": true`): LCP [2500→100, 4000→50, 8000→0]; FCP [1800,3000,6000]; TTFB [800,1800,3600]; CLS [0.1→100, 0.25→50, 0.5→0]; TBT_APPROX [200,600,1500]; INP_SYNTH [200,500,1000]; CHECKOUT_FIELDS [8→100, 12→60, 16→20, 20→0]; READABILITY Gulpease [40→0, 60→60, 80→100]; REVIEW_COUNT [0→0, 5→70, 20→100]; RATING_BAND {4.0–4.7:100, 4.8–5.0:70, 3.5–3.9:50, <3.5:0}; TARGET_SIZE_24 [70→0, 95→100]; LOSTNESS [0→100, 0.42→50, 1→0]; DEAD_CLICK_RATE [0→100, 10→60, 30→0]; ACTIONS_RATIO [1→100, 2→50, 3→0]; ACTION_RESPONSE_MS [100→100, 1000→60, 3000→20, 10000→0]; BYTES_TOTAL [1000→100, 2500→60, 5000→20, 8000→0] (provisional); THIRD_PARTY_SHARE [20→100, 50→50, 80→0] (provisional).

`scoring.py`:
```python
def normalize(kpi_id, value, anchors) -> float | None
def sub_index(obs: list[Observation], name, anchors) -> {"score", "coverage", "grade", "llm_share"}   # weighted mean over assessed KPIs, weights renormalized
def dpr(signals) -> {"score": 100*(1 - prod(1 - s*c)), "signals": [...]}
def ers(subs, dpr_score, weights={"FAI":22,"TRI":20,"PTI":15,"CCL":15,"PERF":18,"MPI":10}) -> {"score"|None, "published", "reason", "coverage", "llm_share", "grade"}
    # exp(sum(w*ln(max(S,10)))/sum(w)) * (1 - 0.30*dpr/100); publish only if overall coverage >= 0.60
    # and every sub-index with weight >= 15 has coverage >= 0.50; unreachable stages -> not assessable, never 0
```
Grades: A ≥85% coverage, B ≥70%, C ≥60%, below → unpublished. `llm_share` = judged weight / assessed weight.

## 5. Data contracts (schemas.py; all JSON-serializable, `schema_version: "engagement.v1"`)

- **run.json**: `{run_id, schema_version, kind: "audit"|"journey", site:{host, start_url}, created_at, settings:{profiles, browser:{mode, product, headless}, locale, consent, anchors_version, rubrics_version, profiles_version}, pages:[PageRecord], journey: JourneyRecord|null, observations:[Observation], judgments:{tasks:[], verdicts:[], final:[]}, scores: ScoreOutput|null, status, warnings:[], errors:[]}`
- **PageRecord**: `{page_id, stage, profile, url, final_url, status_code, classification:{type, confidence, signals}, vitals:{ttfb, fcp, lcp, lcp_element, cls, cls_post_input, long_tasks_ms, tbt_approx, loaf_count, dcl_ms, load_ms, visibility_state_at_load, settled_reason}, network:{requests, bytes_transfer, bytes_js, bytes_img, bytes_third_party, third_party_share, by_type{}, failed, http_errors, mixed_content, source:"cdp"|"resource_timing"}, errors:{console, page}, audit: AuditPayload, screenshot: "shots/<page_id>.jpg"|null, snapshot: "snapshots/<page_id>.json"}`
- **AuditPayload** (output of audit.js, one call): `{url, title, lang, viewport, doc:{h1s, headings, word_count, sentence_count, letter_count, text_sample}, jsonld:[...], meta:{og_type, canonical}, prices:[{text, value, currency, strikethrough, rect, near_text}], ctas:[{label, lexicon_hit, rect, above_fold, fg, bg, contrast, area}], search:{present, above_fold, width, listbox_present}, nav:{links, categories:[{label, href}], breadcrumbs, generic_label_share}, products:{cards_count, cards:[{title, href, price}]}, filters:{controls, sort, result_count_text, chips, pagination}, pdp:{add_to_cart, stock_text, delivery_text, shipping_text, returns_text, images, variants, reviews:{count_text, rating_text}}, cart:{line_items:[{title, qty, price}], subtotal_text, shipping_text, total_text, checkout_cta, prechecked_paid:[...]}, forms:{fields_total, required, visible, autocomplete_share, labels_share, cc_present, password_present}, overlays:[{kind, coverage, has_close, consent_like, accept_labels, reject_labels, text_sample}], trust:{https, contact:{email, phone, address}, vat_id, policy_links:{returns, shipping, privacy, terms, contact}, payment_logos, badges}, persuasion:{scarcity:[{text, number}], urgency:[{text, countdown, remaining_s}], reciprocity:[], authority:[]}, images:{count, oversized:[], lazy_share}, targets:{interactive, lt24, lt44, lt48}, a11y:{img_missing_alt, inputs_missing_label, lang_missing, iframes, shadow_roots}, snippets:[{snippet_id, kind, text, locator}]}`
- **Observation**: `{kpi_id, value, unit, source:"deterministic"|"judged", page_id|null, profile|null, assessed: bool, reason|null, evidence:{...}}`
- **JudgmentTask**: `{task_id, run_id, rubric_id, rubric_version, page_id, question, labels:{label: description}, snippets:[{snippet_id, text}], context:{page_type, locale}, samples_required}`
- **Verdict**: `{task_id, judge_id, model, label, confidence (0..1), evidence:[{snippet_id, quote}], rationale (≤280 chars)}`; rejected when label not in rubric, quote not a verbatim substring of the snippet, confidence out of range, or task already final.
- **JourneyStep (steps.jsonl)**: `{step, t_wall, operation, target, label, kind, node, text_len, decision_latency_ms, policy, url_before, url_after, page_changed, since:{first_response_ms, mutations, navigations, requests, errors, shifts_post_input, event_timing_max_ms}, overlay:{present, coverage}, flags:{dead_click, rage, backtrack, guard_blocked}, status}`
- **ScoreOutput**: `{anchors_version, weights, kpis:[{id, value, unit, normalized, owner, source, assessed, reason}], sub_indices:{FAI:{score, coverage, grade, llm_share}, ...}, dpr:{score, signals}, ers:{score, published, grade, coverage, llm_share, reason}, top_risk_signals:[...]}`

## 6. Key runtime designs

**vitals.js** (installed with `Page.addScriptToEvaluateOnNewDocument`, buffered observers): navigation timing (TTFB), paint, LCP (last candidate), CLS with web-vitals session windows (1 s gap / 5 s max) and a separate `hadRecentInput` sum, event-timing with `durationThreshold: 16` keyed by `interactionId`, `longtask`, `long-animation-frame`, `resource` entries (transferSize, encodedBodySize, initiatorType, responseStatus, renderBlockingStatus), visibilitychange log, `window.onerror`/`unhandledrejection` counters, history pushState/replaceState/popstate hook, MutationObserver counter with first-mutation timestamp. API: `read()`, `mark() -> performance.now()`, `since(t)`, `quiet(ms)` (no new resource/LCP entries for ms). Settle rule in `collectors.load_page`: load event + network quiet 1.5 s + LCP quiet 2 s, hard cap 20 s; `settled_reason` recorded.

**Network accounting**: with `DirectTransport`, `Network.requestWillBeSent/responseReceived/loadingFinished/loadingFailed` (bytes = `encodedDataLength`, third-party by eTLD+1 using a small ccTLD second-level table, mixed content = `http://` subresource on an `https` document). With `HarnessTransport`, resource timing only (`source: "resource_timing"`, lower coverage).

**crawler.discover_funnel** (generic, no site plans): home → PLP candidates scored from nav/category links (lexicon + URL patterns `/c/`, `/categoria/`, `/collections/`, JSON-LD SiteNavigationElement) → visit the first that classifies as PLP (≥4 product cards or ItemList) → PDP = first card link that classifies as PDP (JSON-LD Product/Offer or price + add-to-cart CTA) → click the observed add-to-cart CTA via `Browser.act` (the element comes from `snapshot.js`, matched by lexicon label) → cart via cart link/icon or `/cart` pattern → extract line items → optional checkout step 1 (click checkout CTA; capture fields, guest option; never submit, never fill). `pagetypes.classify` returns scores for home/plp/pdp/cart/checkout/challenge/other; "challenge" (CAPTCHA/anti-bot) makes downstream stages `not_assessable(reason="bot_challenge")`.

**deception.py** (second isolated context, same profile): reload PDP and compare countdown `remaining_s` (reset within ±5 s → DPR.COUNTDOWN_RESET), compare low-stock numbers across the two fresh visits (identical text on reload is fine; a decrement without a purchase is a signal, equal numbers across two unrelated PDPs is a second signal), cart line items vs items added (sneak-into-basket), pre-checked checkboxes with price text, consent banner accept vs reject controls (presence, size ratio, clicks-to-reject if a "manage" path exists: ≤2 extra clicks counted).

**safety.py**: `CheckoutGuard.filter(page, actions)` removes click targets whose label matches the pay lexicon (`paga`, `acquista ora`, `conferma ordine`, `place order`, `pay now`, `buy now`, `completa l'acquisto`) once the page classifies as checkout, refuses TYPE_TEXT into fields with `autocomplete` `cc-*` or `type=password`, refuses any action when a payment-provider iframe is focused, and stops the journey with `stopped_at_checkout_boundary` when page type is `checkout` and `stop_at` (default `"checkout_entry"`) is reached. Also host allowlist: navigation to another registrable domain is flagged `FAI.UNEXPECTED_NAV`, and actions are refused on external hosts.

**journey.py**: `JourneyRunner(settings).start(url, goal, oracle, policy)` builds `Browser(url, transport=..., metrics=profile)` wrapped by `PageCollector`, then `Agent(url, goal, browser=browser, policy=policy, text_policy=text_policy)`. The loop is `predict` → `mark = evaluate("__jevVitals.mark()")` → `act` → `since(mark)` read at the next step boundary (so late responses are credited) → `steps.jsonl` append (before observing, consistent with AGENTS.md). Host policy: `HostPolicy.set(operation, target, text)` then `agent.command("predict")`/`("act")`; `StalePage` returns `stale: true` and a fresh observation without consuming a step. Dead click = kind click, `page_changed is False`, `since.mutations == 0`, `since.requests == 0`, no navigation. Oracle evaluated by `oracles.py` on a fresh observation plus, for cart oracles, an explicit visit to the cart page discovered during the journey.

**judgments.py**: tasks are generated per snippet kind from `AuditPayload.snippets` (cta, scarcity, urgency, consent, checkbox_label, policy_summary, headline, fee_line) against rubric files; verdict validation checks `quote in snippet.text` verbatim (after NFC normalization and whitespace collapse); aggregation = majority label with mean confidence of the agreeing samples, ties → `uncertain` (not assessed); cache in `~/.cache/jev-engagement/judgments/<sha>.json` keyed by model as well.

**report.py**: `report.json` (= ScoreOutput + run summary) and a self-contained `report.html` (inline CSS, base64 thumbnails ≤ 60 KB each, KPI table with anchors version, sub-index bars, risk-signal list with evidence quotes, journey timeline, not-assessable list with reasons, methodology footer with "readiness estimate, not measured engagement").

## 7. Workstreams for Opus subagents (file ownership, order, contracts)

Order: **Phase A** (parallel): WS0, WS4, WS6-draft. **Phase B**: WS1 (needs WS0), then WS2 ∥ WS3 (need WS0+WS1 interfaces; they can begin against the JSON contracts and fixture payloads). **Phase C**: WS5 (needs WS2/3/4), then WS7 integration. Each file has exactly one owner; sequenced handoffs noted.

| WS | Owner files | Depends on | Deliverable contract |
|---|---|---|---|
| **WS0 Foundation** | `engagement/{__init__,settings,schemas,transport,chrome,profiles,store}.py`, `jev_ultrafast/browser.py` (kwargs only), `pyproject.toml` (deps + both script entries, with stub `cli.py`/`mcp_server.py` printing "not implemented" that WS5 replaces), `tests/test_engagement_transport.py`, `tests/conftest.py` (fixture HTTP server + `chromium` skip fixture) | none | `open_transport()`, `launch_chromium()`, `apply_profile()`, `RunStore`, TypedDicts in `schemas.py`; existing `uv run pytest` still green |
| **WS1 Collectors** | `engagement/{vitals.js,audit.js,lexicon.py,collectors.py,pagetypes.py}`, `tests/fixtures/shop/*`, `tests/test_engagement_collectors.py`, `tests/test_engagement_pagetypes.py` | WS0 | `PageCollector.load_page(url) -> PageRecord`, `classify()`; fixture pages exercise every AuditPayload field incl. dark-pattern cases |
| **WS2 Deterministic audit** | `engagement/{crawler,checks,deception,audit,safety}.py`, `tests/test_engagement_checks.py`, `tests/test_engagement_crawler.py` | WS0, WS1 | `audit_shop(settings) -> run_id` writing run.json; `checks.observations(run) -> list[Observation]` tested on fixture payload JSON; crawler test on fixture shop with Chromium |
| **WS3 Journey** | `jev_ultrafast/agent.py` (kwargs), `engagement/{journey,friction,oracles}.py`, `tests/test_engagement_journey.py`, `tests/test_engagement_friction.py` | WS0, WS1 (`__jevVitals.since`), WS2's `safety.py` interface (import only) | `JourneyRunner` with `start/observation/act/finish`, host and TypeSafe policies; `friction.metrics(steps) -> list[Observation]` on recorded steps.jsonl fixtures; `tests/test_agent.py` untouched and green |
| **WS4 Judgments, scoring, report** | `engagement/{judgments,judges,scoring,report}.py`, `engagement/anchors.json`, `engagement/rubrics/*.json`, `tests/test_engagement_scoring.py`, `tests/test_engagement_judgments.py`, `tests/test_engagement_report.py` | `schemas.py` only | pure functions; golden run.json fixtures → golden report.json; `judges.ClaudeCliJudge` tested with a fake `claude` executable on PATH |
| **WS5 MCP + CLI + plugin** | `engagement/{service,mcp_server,cli}.py`, `.claude-plugin/plugin.json`, `.mcp.json`, `skills/**`, `agents/**`, `tests/test_engagement_mcp.py` | WS2, WS3, WS4 | tools as in §2.1; tests call tool functions with a `FakeBrowserFactory` and golden runs; `claude plugin validate .` passes |
| **WS6 Docs (Sonnet)** | `docs/engagement-kpi.md` (Italian: per KPI id, definizione, unità, fonte/soglie con riferimenti, deterministico/giudicato, sub-indice proprietario, limiti), `docs/engagement-design.md`, `README.md`, `AGENTS.md` (add engagement rules: no orders, no personal data, labels "risk signals/predicted friction", anchors versioned, claims backed by run artifacts) | this plan; reconcile KPI ids with `anchors.json` after WS4 | consistent with AGENTS.md rule on claims (no performance claims without an artifact) |
| **WS7 Integration (Opus, after all)** | no new files; runs fixtures end-to-end, one real shop audit in headless mode, fixes cross-workstream mismatches via the owning files | all | `uv run ruff check .`, `uv run pytest`, `node --check` on the three JS files, `uv build`, `claude plugin validate .` |

Contracts the subagents must not change unilaterally: `schemas.py`, `anchors.json` KPI ids, `AuditPayload` field names, the `choose()` decision dict, the tool names in §2.1.

## 8. Testing strategy (offline, no paid APIs)

- **Unit (always run):** `scoring.py` (piecewise-linear interpolation at and beyond anchors, direction handling, renormalized weights, geometric mean with floor 10, DPR noisy-OR monotonicity, publish/unpublish rules, not-assessable ≠ 0), `judgments.py` (verbatim quote validation incl. NFC/whitespace, unknown label rejection, majority/tie behaviour, cache key stability), `friction.py` (dead click, rage, backtrack, lostness formula against Smith's examples, time-on-task excludes decision latency), `checks.py` on JSON fixture payloads (price mismatch, funnel delta, Omnibus statement, checkout field count), `pagetypes.classify` on fixture payloads, `safety.py` label and field rules, `report.py` golden HTML contains no external URLs.
- **Browser integration (skip when `find_chromium()` is None):** `tests/conftest.py` starts `http.server` on 127.0.0.1:0 serving `tests/fixtures/shop/` (index, category, product with JSON-LD + strikethrough price + countdown that resets, cart with a sneaked item and a pre-checked paid add-on, checkout step 1 with 14 fields and no guest option, consent banner with accept-only, a "challenge" page). Tests: vitals.js reports FCP/LCP/CLS in headless (asserts `visibility_state_at_load == "visible"`), network bytes via CDP match the served file sizes, audit.js fields, crawler reaches cart and stops at checkout step 1 without submitting (server asserts no POST), deception tests fire on the dark fixture and stay silent on the clean one, journey host-driven run with a scripted policy reaches the oracle, guard blocks the "Paga ora" button.
- **MCP tests:** call `service.EngagementService` methods and the tool functions with a fake browser factory and golden runs; check pagination sizes (<15 tasks, <600 chars/snippet) and that `get_report` returns paths, not HTML.
- **Harness transport:** unit test with a fake `browser_harness.helpers` module (monkeypatched `cdp`/`drain_events`); no daemon in CI.
- **Checks:** keep `uv run ruff check .`, `uv run pytest`, add `node --check jev_ultrafast/engagement/vitals.js` and `audit.js` to the documented check list, `uv build`.

## 9. Risks and open doubts (flagged)

1. **Paint metrics in background/hidden tabs.** `Target.createTarget(background=True)` in the user's Chrome yields `visibilityState: hidden`; FCP/LCP are not reported. Audits therefore default to the self-launched headless Chromium; attaching to the user's Chrome is for journeys on bot-protected sites, and perf KPIs are then marked not assessable unless the page was visible. WS1 must record `visibility_state_at_load` and the test asserts it.
2. **browser_harness attach.** Works via `BU_CDP_URL`, but the 500-event ring buffer, 5 s IPC timeout, and the title marker make it unsuitable for byte accounting; `DirectTransport` is the primary path. Chrome 147+ disables `/json/version` on the default profile; our launched Chromium uses a dedicated `--user-data-dir`, so unaffected.
3. **Bot detection / CAPTCHA.** Headless Chromium is often challenged (Cloudflare, Akamai). Mitigations: UA override without "HeadlessChrome", `Accept-Language`, `--headless=new`; classify challenge pages; offer `browser="harness"` (real profile) for journeys. No evasion beyond that.
4. **Consent banners.** Nearly universal on Italian shops; they distort first-page bytes and block clicks. Setting `consent="auto"`: measure the landing page as-is, then reject if a reject control is offered, else accept only when the overlay blocks the funnel; record the choice in run.json and as DPR.CONSENT_ASYMMETRY evidence.
5. **Shadow DOM and iframes.** `snapshot.js` ignores both; `audit.js` will read open shadow roots and count closed roots/iframes; reviews widgets (Trustpilot etc.) and payment forms in cross-origin iframes are blind spots → partial coverage, reported as such.
6. **axe-core licensing.** MPL-2.0 is file-level copyleft and could be vendored unmodified with its license file, but MVP uses a built-in a11y subset and an optional pinned download with sha256 into `~/.cache/jev-engagement/vendor/`; decide vendoring later.
7. **Checkout safety.** The pay-label lexicon is it/en only; the hard rules (no `cc-*`/password typing, stop at first checkout page, no form submission in audit mode, no account creation) are the real guard. Add-to-cart does mutate server state (cart sessions); acceptable, documented.
8. **Judgment independence.** Three Sonnet subagents with the same prompt are a stability check, not independent raters; report `llm_share` and model ids, and keep snippets closed-label.
9. **Throttled measurement variance.** CPU throttling in a container and real networks produce spread; `repeats` parameter with median reporting; a single run is labelled as such.
10. **Tool duration.** A two-profile audit takes ~2–4 min; Claude Code auto-backgrounds after 2 min and returns a notification. The skill must poll `get_run`. Consider splitting `audit_shop` per profile if this is awkward in practice.
11. **MCP SDK v2 freshness.** `mcp` 2.x changed names and depends on `httpx2`; if it causes install trouble in `uv sync`, fall back to `mcp>=1.30,<2` (`from mcp.server.fastmcp import FastMCP`) — `mcp_server.py` is the only file affected.
12. **Visual complexity anchors** are editorial (no published thresholds); mark provisional and keep CCL weight small until calibration against GA4/Clarity.

## 10. Later phases (not in MVP)
GA4/Clarity calibration join (`calibration.py` using the Claude Code connectors: engagement rate, add_to_cart→begin_checkout→purchase, rage/dead clicks, quick backs by page path), axe-core full, CrUX API real-user CWV, multi-run statistics UI in the inspector (`demo.py` untouched for now), additional locales, warm-cache runs, Speed Index.

### Critical Files for Implementation
- /home/user/jev-ultrafast-user-engagment/jev_ultrafast/browser.py (transport/metrics/context injection; keeps guards and default path intact)
- /home/user/jev-ultrafast-user-engagment/jev_ultrafast/agent.py (`browser`, `policy`, `text_policy` kwargs for host-driven journeys)
- /home/user/jev-ultrafast-user-engagment/jev_ultrafast/engagement/transport.py (DirectTransport over websockets + HarnessTransport behind one `cdp()` shape)
- /home/user/jev-ultrafast-user-engagment/jev_ultrafast/engagement/schemas.py (the shared contracts every workstream codes against)
- /home/user/jev-ultrafast-user-engagment/jev_ultrafast/engagement/scoring.py with anchors.json (versioned anchors, ownership table, ERS/DPR/confidence)

Sources consulted: [browser-harness SKILL.md](https://cdn.jsdelivr.net/gh/browser-use/browser-harness@main/SKILL.md), [browser-harness daemon.py](https://cdn.jsdelivr.net/gh/browser-use/browser-harness@main/src/browser_harness/daemon.py), [browser-harness admin.py](https://cdn.jsdelivr.net/gh/browser-use/browser-harness@main/src/browser_harness/admin.py), [claude-code#1785 MCP sampling](https://github.com/anthropics/claude-code/issues/1785), [claude-code#31893 MCP spec gaps](https://github.com/anthropics/claude-code/issues/31893), [canimcp Claude Code matrix](https://canimcp.dev/client/claude-code/), [Claude Code MCP docs](https://code.claude.com/docs/en/mcp), [Claude Code plugins: create](https://code.claude.com/docs/en/plugins/create), [Claude Code plugins: components](https://code.claude.com/docs/en/plugins/components), [Claude Code skills](https://code.claude.com/docs/en/skills), [Claude Code headless / claude -p](https://code.claude.com/docs/en/headless), [MCP Python SDK v2 migration](https://py.sdk.modelcontextprotocol.io/migration), [MCP Python SDK structured output](https://py.sdk.modelcontextprotocol.io/servers/structured-output/index.md), [MCP Python SDK tools](https://py.sdk.modelcontextprotocol.io/servers/tools/index.md), [mcp on PyPI](https://pypi.org/pypi/mcp/json), [MPL-2.0 vs MIT compatibility](https://fossa.com/resources/license-compliance-tools/license-compatibility-checker/mit-vs-mpl-2-0/).