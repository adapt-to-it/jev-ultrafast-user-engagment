export const meta = {
  name: 'engagement-phase-bc',
  description: 'Build WS1 collectors+safety, then WS2 deterministic audit and WS3 journey in parallel (Opus), each with independent reviews, Fable rulings and fixes',
  phases: [
    { title: 'Collectors', detail: 'WS1 vitals.js, audit.js, lexicon, collectors, pagetypes, safety, fixture shop' },
    { title: 'Audit', detail: 'WS2 crawler, deception, checks, audit_shop' },
    { title: 'Journey', detail: 'WS3 agent hooks, journey runner, friction, oracles' },
  ],
}

const SP = args.sp
const REPO = '/home/user/jev-ultrafast-user-engagment'
const PHASE_A = args.phaseA || ''

const COMMON = `You are one implementer in a team building an "engagement readiness" auditor for e-commerce shops on top of the Jev Ultrafast browser agent in ${REPO}.
Read FIRST: ${SP}/CONTRACTS.md (module contracts, file ownership, global rules). Then ${SP}/approved-plan.md (approved plan, Italian).
Design rationale with more detail: ${SP}/architecture-plan-fable.md. Research: ${SP}/research-performance-elements-friction.md and ${SP}/research-psychology-indices.md. Codebase map: ${SP}/codebase-map.md.
Shared contracts: ${REPO}/jev_ultrafast/engagement/schemas.py and kpis.py. Already implemented and reviewed (read the real code, it is the source of truth over CONTRACTS.md where they differ): settings.py, transport.py, chrome.py, profiles.py, store.py, browser.py hooks, tests/conftest.py fixtures (shop_server, chromium, transport), scoring.py, judgments.py, judges.py, report.py, anchors.json, rubrics/, docs/engagement-kpi.md.
Phase A notes: ${PHASE_A}
Other implementers may be editing OTHER files in the same working tree right now: edit only files you own, never git commit/stash/checkout/reset, never run formatters over the whole repo. Put scratch scripts in ${SP}/scratch/.
Run your own tests plus tests/test_agent.py and tests/test_engagement_foundation.py (e.g. \`cd ${REPO} && uv run pytest tests/test_x.py tests/test_agent.py -q\`) and \`uv run ruff check <your files>\`. Browser tests use headless Chromium + the local fixture server only (never the public internet) and skip when Chromium is missing.`

const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    files: { type: 'array', items: { type: 'string' } },
    tests: { type: 'string', description: 'exact test command(s) run and their pass/fail counts' },
    contract_changes: { type: 'array', items: { type: 'string' } },
    open_issues: { type: 'array', items: { type: 'string' } },
    design_doubts: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
  required: ['files', 'tests', 'contract_changes', 'open_issues', 'design_doubts', 'summary'],
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    blocking: {
      type: 'array',
      items: {
        type: 'object',
        properties: { file: { type: 'string' }, problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } },
        required: ['file', 'problem', 'evidence', 'fix'],
      },
    },
    minor: { type: 'array', items: { type: 'string' } },
    design_doubts: { type: 'array', items: { type: 'string' } },
    tests_run: { type: 'string' },
    summary: { type: 'string' },
  },
  required: ['blocking', 'minor', 'design_doubts', 'tests_run', 'summary'],
}

const WS1 = {
  key: 'ws1', phase: 'Collectors', model: 'opus', reviewModel: 'opus',
  owned: 'jev_ultrafast/engagement/{vitals.js,audit.js,lexicon.py,collectors.py,pagetypes.py,safety.py}, tests/fixtures/shop/**, tests/test_engagement_collectors.py, tests/test_engagement_pagetypes.py, tests/test_engagement_safety.py',
  prompt: `${COMMON}

YOUR WORKSTREAM: WS1 Collectors + safety. Implement the WS1 section of CONTRACTS.md plus safety.py (contract under WS2).
Details:
- lexicon.py: generic multilingual data (Italian first, English always merged), JS-compatible regex sources, no per-site values. Include the keys listed in the contract. Payment/forbidden labels (pay_now, place_order) must be conservative (e.g. "paga ora", "conferma (e paga|ordine)", "completa (l')?acquisto", "acquista ora" only on checkout pages? decide and document), and guard against false positives like "Pagamenti sicuri" text.
- vitals.js: installed via Page.addScriptToEvaluateOnNewDocument (runs in every frame: install only in the top frame), idempotent, buffered PerformanceObservers for paint, largest-contentful-paint, layout-shift (session windows: gap 1 s, max 5 s, excluding hadRecentInput; separate post-input sum), event (durationThreshold 16) + first-input, longtask, long-animation-frame (guard support), resource; navigation timing for TTFB/DCL/load; visibilityState at load; window error + unhandledrejection counters; history pushState/replaceState/popstate hooks; a MutationObserver counter with timestamps (start as soon as document.documentElement exists). API read()/mark()/since(t)/quiet(ms) per schemas.Vitals and schemas.StepSince. Keep it dependency-free and cheap.
- audit.js: one evaluation over the whole document (not just the viewport) plus open shadow roots; returns schemas.AuditPayload with every section; rects in page coordinates; above_fold = rect.y < innerHeight at load scroll; JSON-LD parsing tolerant of @graph/arrays/invalid JSON; price parsing for "€ 1.234,56", "1,234.56 EUR", "49,90 €" etc.; CTA salience inputs (fg/bg color with ancestor background resolution, WCAG contrast, area); overlays (fixed/sticky/dialog/aria-modal covering > 15% viewport) with consent-like detection and accept/reject/manage labels + areas; trust (contacts, VAT id regex incl. Italian P.IVA 11 digits, policy links by lexicon/href), persuasion (scarcity with numbers, urgency with countdown detection: a text node matching time formats, plus remaining_s), forms (visible fields, required, autocomplete share, labels share, password/cc presence, guest option text), target sizes (lt24/lt44/lt48 over visible interactive elements, excluding inline text links per WCAG 2.5.8), a11y basics, images (oversized: natural >= 2x displayed css px * dpr), readability inputs (word/sentence/letter counts over main content text), and snippets (SNIPPET_KINDS in schemas.py, verbatim visible text, whitespace-collapsed, <= 600 chars, stable snippet ids). Never return huge payloads (cap lists, e.g. 60 links, 40 cards, 40 snippets).
- collectors.py per contract: PageCollector.open() builds Browser(url, transport=..., browser_context_id=..., background=False, prepare=...) where prepare applies the device profile (profiles.apply_profile), enables Page/Runtime/Log/Network, Page.setLifecycleEventsEnabled, and installs vitals.js; load_page/collect: drain stale events before navigating, settle (load + network quiet 1.5 s + LCP quiet 2 s, cap settle_timeout_s; record settled_reason), read vitals, build NetworkSummary from Network.* events of this session (requestWillBeSent/responseReceived/loadingFinished/loadingFailed; bytes = encodedDataLength; third-party by registrable domain with a small multi-part public-suffix table; mixed content; http errors with samples) or from Resource Timing when transport.lossy, PageErrors from Runtime.exceptionThrown / consoleAPICalled(error) / Log.entryAdded(error), audit.js, classification, screenshot (jpeg, viewport) + visual metrics with Pillow (Hasler-Susstrunk colorfulness, edge density, jpeg bytes per pixel) when screenshots are enabled, snapshot JSON of the audit payload written via the store. Paint metrics are reported None (not 0) when visibility_state_at_load != "visible". mark()/since() helpers for journeys.
- pagetypes.py: classify() scoring home/plp/pdp/cart/checkout/challenge/other from AuditPayload + URL patterns + JSON-LD (Product/Offer -> pdp, ItemList or >=4 cards -> plp, line items + checkout CTA -> cart, many address fields/payment -> checkout, captcha/challenge text and tiny DOM -> challenge, root path + nav -> home), with confidence and the signals used.
- safety.py: CheckoutGuard per the WS2 contract section (same_site by registrable domain, allowed_action, filter_actions, allow_text inspecting the observed node through window.__jevFast, should_stop). Unit tests with synthetic pages + a Chromium test on the fixture checkout page.
- Fixture shop under tests/fixtures/shop/ exactly as described in CONTRACTS.md (index, category, product, product-dark, cart (with ?dark=1 variant), checkout, challenge) with Italian copy, no external URLs, realistic markup (JSON-LD, consent banner, footer policies, P.IVA, payment logos as inline SVG/alt text). Make the clean pages genuinely good and the dark pages genuinely dark so downstream tests can assert both directions.
- Tests: collectors against Chromium (vitals present and visible on fixtures, CLS from an injected late shift, network bytes match served file sizes within a tolerance, third-party share 0 on local fixtures, console error counted from a fixture that throws, audit fields on every fixture page, snippets kinds present, visual metrics), pagetypes on every fixture page (correct type), safety rules.
Finish with ruff, node --check on both JS files, and your tests.`,
  lenses: [
    { key: 'browser', text: 'Measurement correctness in a real browser: run the fixtures through PageCollector with headless Chromium under both device profiles (scratch script), and verify by independent means (e.g. direct Runtime.evaluate of performance entries, file sizes on disk) that TTFB/FCP/LCP/CLS/long tasks, byte counts, request counts, error counts and visibility are right, that the mobile profile really applies (viewport, UA, throttling slows loads), that settle does not hang or cut early, that events from a previous page do not leak into the next PageRecord, and that vitals.js does not break pages or install twice. Report reproducible defects only.' },
    { key: 'heuristics', text: 'Heuristic robustness of audit.js / pagetypes.py / lexicon.py / safety.py: craft adversarial variants of the fixture pages in the scratch dir (English copy, prices in different formats, add-to-cart as a link, shadow DOM, consent banners with reject in a second layer, CTA as an image, huge pages) and check outputs; check that nothing is site-specific, that payload sizes are bounded, that snippets are verbatim visible text, that safety never allows typing into password/cc/email/name/address fields or clicking pay/place-order controls and does not block innocent controls (e.g. a "Pagamenti sicuri" info link). Report concrete defects with the HTML that triggers them.' },
  ],
}

const WS2 = {
  key: 'ws2', phase: 'Audit', model: 'opus', reviewModel: 'opus',
  owned: 'jev_ultrafast/engagement/{crawler,checks,deception,audit}.py, tests/test_engagement_checks.py, tests/test_engagement_audit.py',
  prompt: `${COMMON}

YOUR WORKSTREAM: WS2 Deterministic audit. Implement the WS2 section of CONTRACTS.md (crawler, deception, checks, audit). safety.py, collectors.py, pagetypes.py, lexicon.py already exist (WS1): use them, do not edit them; report needed changes.
Details:
- crawler.discover_funnel: generic funnel discovery per contract; consent handling per settings.consent (auto: measure landing as-is, then click an observed reject control if offered, otherwise accept only if the overlay blocks the funnel; record PageRecord.consent); search-autocomplete probe on home (see CONTRACTS.md); PLP candidate scoring; PDP from product cards; add-to-cart via Browser.observe() + Browser.act() on the observed element chosen by lexicon (never retried; if it fails, mark later stages not assessable with a reason); cart; checkout_entry by clicking the observed checkout CTA (never fill, never submit; stop there); guard (CheckoutGuard) consulted before every action; bot challenge -> NotAssessable for remaining stages; max_pages respected; page ids "<profile>-<stage>-<n>"; record what was added to the cart (title, price) for sneak-into-basket.
- deception.run_deception_tests in a second isolated context with the same profile: countdown reset (two visits >= 5 s apart in fresh contexts: reset if the second remaining_s is not smaller by about the elapsed time), low-stock (same "only N left" number on different products, or a number that changes between fresh visits without a purchase), sneak-into-basket (cart lines not added by us that add cost), pre-checked paid add-ons (from audit.cart.prechecked_paid), consent asymmetry (first layer has accept but no reject, or reject area much smaller / needs extra clicks), nagging overlays (non-consent overlays on >= 2 pages or reappearing after close). Return raw results with evidence; never place orders.
- checks.observations(run): produce Observations for every KPI whose producer is "checks" or "deception" in kpis.py, following each KPI's unit, value_type and stages; per page where scope=="page", site-level where scope=="site"; assessed=False with a reason when the needed stage/page is missing or the data is unavailable (e.g. paint metrics when the tab was hidden); PERF from vitals/network/errors (KB = bytes/1024, % = 0..100), READABILITY = Gulpease for Italian pages (89 - 10*letters/words + 300*sentences/words) and Flesch reading ease for English, CTA salience formula documented in code, PTI.FUNNEL_PRICE_DELTA from PDP price vs cart/checkout totals minus declared shipping, DPR confidences from deception results.
- audit.audit_shop: orchestrates per profile (open_transport from settings.browser unless transport_factory is given; new isolated context per profile; collector; discover_funnel; deception tests; close contexts; close launched Chromium), writes pages/not_assessable/deception/observations/status/warnings into the run via RunStore, never leaves Chromium running on errors, progress callback messages, settings snapshot including profiles_version/anchors_version.
- Tests: checks on synthetic PageRecord JSON (no browser) covering every KPI branch; an end-to-end audit_shop on the fixture shop with Chromium (both profiles) asserting stages reached, checkout POST never hit (shop_server.requests), DPR signals fire on the dark variant and stay silent on the clean one, and scoring.score_run(run) produces a published ERS for the clean shop.
Finish with ruff and your tests.`,
  lenses: [
    { key: 'e2e', text: 'End-to-end correctness: run audit_shop on the fixture shop (clean and dark) under both profiles via a scratch script, inspect run.json, and verify independently every stage, KPI value and DPR result against the fixture HTML (compute expected values by hand for a sample of KPIs). Verify no checkout/pay POST, no retries of mutations (count Input.dispatchMouseEvent per action if useful), Chromium is closed afterwards, and not-assessable handling never yields 0 scores. Report reproducible defects only.' },
    { key: 'generality', text: 'Generality and rules: look for site-specific assumptions (hardcoded paths, class names, fixture-only shortcuts) in crawler/checks/deception, for KPIs in kpis.py with producer checks/deception that are never emitted or emitted with wrong units/value types/stages, for deception tests that could fire on honest shops (false positives) or that mutate state unsafely, and for any violation of AGENTS.md (selectors from models, retried mutations, orders, personal data). Report concrete problems only.' },
  ],
}

const WS3 = {
  key: 'ws3', phase: 'Journey', model: 'opus', reviewModel: 'opus',
  owned: 'jev_ultrafast/agent.py (kwargs only), jev_ultrafast/engagement/{journey,friction,oracles}.py, tests/test_engagement_journey.py, tests/test_engagement_friction.py',
  prompt: `${COMMON}

YOUR WORKSTREAM: WS3 Journey. Implement the WS3 section of CONTRACTS.md. collectors.py, safety.py, lexicon.py exist (WS1) and crawler/checks are being written in parallel by WS2 (do not import crawler internals; if you need cart discovery for an oracle, implement a small helper in oracles.py using collectors + lexicon).
Details:
- agent.py: add browser=None, policy=None, text_policy=None keyword arguments only; Agent uses the given Browser instead of constructing one; predict calls (self.policy or choose)(page, goal, history); the fill branch calls (self.text_policy or field_text)(context); everything else unchanged so tests/test_agent.py passes untouched and model-call budgets still apply.
- journey.HostPolicy: validates operation/target against model.action_space for the current page (the host can only pick offered indices and operations, never selectors); produces a decision dict with the full choose() contract (probabilities {choice: 1.0} etc., latency_ms measured from observation delivery to set(), model "host"); text for TYPE_TEXT is provided with set(); text() returns it.
- JourneyRunner per contract: launches/attaches via open_transport (or transport_factory), isolated context, device profile, PageCollector-installed vitals.js, Agent(url, goal, browser=..., policy=..., text_policy=...); start() returns the first observation (filtered by CheckoutGuard: guarded actions are removed and listed in guard_notes; refuse TYPE_TEXT into protected fields via guard.allow_text); act(): mark = collector.mark(), agent.command("predict"), agent.command("act"), then step metrics via collector.since(mark), settle_ms; append the JourneyStep to steps.jsonl BEFORE the post-action observation is used (log execution before observing); StalePage -> stale=True, step not consumed, fresh observation; DONE/BLOCKED end the run; stop automatically at the checkout boundary (status stopped_at_checkout_boundary) and on budget; external navigation flagged and not followed further. run_auto() for policy "typesafe" (uses model.choose; only when TYPESAFE_API_KEY is set; never in tests). finish(): oracle verification (independent of DONE), friction observations stored into run["observations"], journey record, status; always closes contexts and launched Chromium.
- friction.metrics: dead clicks (click with page_changed False, mutations 0, requests 0, navigations 0), rage events (same node >= 3 consecutive acts without change), backtrack rate (revisits of URLs), Lostness (Smith 1996: sqrt((N/S-1)^2 + (R/N-1)^2) when optimal R known), actions ratio, time on task site-attributable (sum of settle_ms + execution, excluding decision_latency_ms), action response median, INP_SYNTH (max event_timing), CLS_POST_INPUT, unexpected navigation, journey success from verification; assessed=False with reasons when not computable.
- oracles.py: closed set per contract, each independent of the agent's DONE and reading actual page state (e.g. cart contents parsed by audit.js on the real cart page).
- Tests: HostPolicy validation (unknown index, wrong operation, selector-like targets rejected), friction formulas on synthetic steps (Lostness against Smith's worked examples), agent kwargs (fake browser + fake policy), and a Chromium journey on the fixture shop with a scripted policy that picks indices by label from the observation (index.html -> category -> product under 50 EUR -> add to cart -> cart), oracle passes, steps.jsonl written, pay button never offered and checkout POST never hit.
Finish with ruff and your tests.`,
  lenses: [
    { key: 'loop', text: 'Loop invariants and safety: verify (by reading code and running scratch scenarios with fake browsers and the fixture shop) that the model/host can only choose observed indices and offered operations, mutations are never retried (including after StalePage and errors), the step is appended to steps.jsonl before the post-action observation, decision latency is excluded from site-attributable time, checkout boundary and guard work, budgets are enforced, oracles do not trust DONE, browsers/Chromium are always closed, and tests/test_agent.py behaviour is unchanged. Report reproducible defects only.' },
    { key: 'metrics', text: 'Friction metric correctness: recompute dead/rage/backtrack/Lostness/actions ratio/time-on-task/response/INP_SYNTH/CLS_POST_INPUT by hand for synthetic step sequences and for a real fixture journey (scratch script), check units and value types against kpis.py and anchors.json, and check that every friction-producer KPI is emitted (assessed or with a reason). Report concrete numeric discrepancies.' },
  ],
}

function reviewPrompt(ws, lens, impl) {
  return `${COMMON}

You are an independent, skeptical REVIEWER for workstream ${ws.key} (owned files: ${ws.owned}). Do NOT edit any repository file; you may write scratch scripts in ${SP}/scratch/.
Implementer report: ${JSON.stringify(impl ?? {}).slice(0, 6000)}
Review lens: ${lens.text}
"blocking" = real defects that must be fixed (wrong behaviour, contract mismatch, failing/missing essential tests, rule violations), each with concrete evidence (command output, line refs) and a precise fix. "minor" = style/nits. "design_doubts" = only genuinely significant design questions that need an architect's ruling. Default to fewer, verified findings.`
}

function fixPrompt(ws, blocking, minor, rulings) {
  return `${COMMON}

You are the implementer for workstream ${ws.key} (owned files: ${ws.owned}). Independent reviewers found the problems below${rulings ? ' and an architect (Fable) issued rulings on open design doubts' : ''}. Verify each finding yourself; fix every real one in your owned files (if a finding is wrong, explain why in open_issues). Apply the architect rulings. Re-run ruff and your tests at the end.
Blocking findings: ${JSON.stringify(blocking).slice(0, 12000)}
Minor findings (fix if cheap and correct): ${JSON.stringify(minor).slice(0, 4000)}
${rulings ? 'Architect rulings: ' + String(rulings).slice(0, 8000) : ''}`
}

function fablePrompt(ws, doubts) {
  return `You are the senior architect (big design doubts are escalated to you). Context: ${COMMON}

Workstream ${ws.key} (owned files: ${ws.owned}) raised these design doubts:
${doubts.map((d, i) => `${i + 1}. ${d}`).join('\n')}
Read the relevant code and documents, then give a concise, decisive ruling for each doubt (what to do, and why), keeping the approved plan and contracts stable unless a change is clearly necessary. If a ruling changes a shared contract (schemas.py, kpis.py, CONTRACTS.md), say exactly what to change and flag it as CONTRACT CHANGE. Do not edit files yourself.`
}

async function buildWS(ws) {
  const impl = await agent(ws.prompt, { label: `impl:${ws.key}`, phase: ws.phase, model: ws.model, schema: IMPL_SCHEMA })
  let last = impl
  let doubts = (impl && impl.design_doubts) || []
  const history = []
  for (let round = 0; round < 3; round++) {
    const reviews = (await parallel(ws.lenses.map(l => () =>
      agent(reviewPrompt(ws, l, last), { label: `review:${ws.key}:${l.key}:${round}`, phase: ws.phase, model: ws.reviewModel, schema: REVIEW_SCHEMA })
    ))).filter(Boolean)
    const blocking = reviews.flatMap(r => r.blocking)
    const minor = reviews.flatMap(r => r.minor)
    doubts = doubts.concat(reviews.flatMap(r => r.design_doubts))
    let rulings = null
    if (doubts.length) {
      rulings = await agent(fablePrompt(ws, doubts), { label: `fable:${ws.key}:${round}`, phase: ws.phase, model: 'fable' })
      doubts = []
    }
    history.push({ round, blocking: blocking.length, minor: minor.length, rulings: rulings ? String(rulings).slice(0, 3000) : null, summaries: reviews.map(r => r.summary) })
    log(`${ws.key} round ${round}: ${blocking.length} blocking, ${minor.length} minor${rulings ? ', Fable rulings issued' : ''}`)
    if (!blocking.length && !rulings) break
    const fixed = await agent(fixPrompt(ws, blocking, minor, rulings), { label: `fix:${ws.key}:${round}`, phase: ws.phase, model: ws.model, schema: IMPL_SCHEMA })
    if (fixed) {
      last = fixed
      doubts = fixed.design_doubts || []
    }
  }
  return { key: ws.key, impl: last, history }
}

const ws1 = await buildWS(WS1)
const rest = await Promise.all([buildWS(WS2), buildWS(WS3)])
return [ws1, ...rest]
