export const meta = {
  name: 'engagement-phase-d',
  description: 'WS5 MCP server + CLI + Claude Code plugin (Opus), WS6b docs (Sonnet), WS7 end-to-end integration (Opus), then a verified multi-lens review loop with fixes',
  phases: [
    { title: 'Interfaces', detail: 'WS5 service, MCP server, CLI, plugin, skills, judge subagent' },
    { title: 'Follow-ups', detail: 'WS1 audit.js gates/in-card flags/variants, WS4 report reasons and golden journey (Opus)' },
    { title: 'Docs', detail: 'WS6b README, AGENTS.md, design doc, catalogue reconciliation (Sonnet)' },
    { title: 'Integration', detail: 'WS7 end-to-end on fixtures and a real shop' },
    { title: 'Review', detail: 'multi-lens review, adversarial verification, fixes until dry' },
  ],
}

const SP = args.sp
const REPO = '/home/user/jev-ultrafast-user-engagment'
const NOTES = args.notes || ''

const COMMON = `You are one engineer in a team building an "engagement readiness" auditor for e-commerce shops on top of the Jev Ultrafast browser agent in ${REPO}.
Read FIRST: ${SP}/CONTRACTS.md (contracts, ownership, global rules, coordinator decisions), ${SP}/approved-plan.md (approved plan, Italian), and the real code in jev_ultrafast/engagement/ (source of truth over CONTRACTS.md where they differ). Design rationale: ${SP}/architecture-plan-fable.md (sections 2 and 6 cover MCP/plugin). KPI catalogue: docs/engagement-kpi.md.
Status notes from the coordinator: ${NOTES}
Never git commit/stash/checkout/reset. Put scratch scripts in ${SP}/scratch/. Tests must not call paid APIs or the public internet. Keep the repo's compact style (ruff line-length 120, rules E,F,I).`

const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    files: { type: 'array', items: { type: 'string' } },
    tests: { type: 'string' },
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

const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' }, line: { type: 'integer' }, title: { type: 'string' },
          problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
        },
        required: ['file', 'line', 'title', 'problem', 'evidence', 'fix', 'severity'],
      },
    },
    summary: { type: 'string' },
  },
  required: ['findings', 'summary'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: { real: { type: 'boolean' }, reasoning: { type: 'string' }, reproduction: { type: 'string' } },
  required: ['real', 'reasoning', 'reproduction'],
}

const WS5 = {
  key: 'ws5', phase: 'Interfaces', model: 'opus', reviewModel: 'opus',
  owned: 'jev_ultrafast/engagement/{service,mcp_server,cli,__init__}.py, .claude-plugin/plugin.json, .mcp.json, skills/**, agents/**, tests/test_engagement_mcp.py, tests/test_engagement_cli.py',
  prompt: `${COMMON}

YOUR WORKSTREAM: WS5 MCP server + CLI + Claude Code plugin. Everything else is implemented; do not edit other files (report needed changes).
Requirements:
- service.py EngagementService(store=None, transport_factory=None, clock=None): the single API used by both MCP and CLI.
  * audit: start_audit(url, profiles, stages, browser, locale, consent, repeats) runs audit.audit_shop in a background thread and returns {run_id, status:"running"} immediately (run_id known up front: create it via the store and pass it in, or adapt with a small wrapper; read audit.py); wait_run(run_id, timeout_s<=110) blocks until done or timeout and returns get_run(); get_run(run_id) returns a compact summary (status, pages summary, not_assessable, judgments counts, scores availability, warnings) well under 25k tokens. A synchronous audit_shop(wait=True) path is used by the CLI.
  * journey: run_journey(url, goal, oracle, oracle_params, profile, policy="host", max_steps, browser, optimal_steps, optimal_pages) -> {run_id, status, observation}; journey_act(run_id, operation, target, text, observation_id) -> per JourneyRunner.act (observation_id REQUIRED, see the "Coordinator decisions after phase B/C" section of CONTRACTS.md); journey_finish(run_id, status) -> verification + friction summary. Live JourneyRunner objects kept in memory with an idle timeout (e.g. 10 min) that finishes them with status "abandoned" and closes browsers/transports (HarnessTransport lock!). Shutdown closes everything.
  * judgments: get_judgment_tasks(run_id, cursor, limit<=15, rubric_id) creating tasks on first call (judgments.make_tasks), paginated, snippets <= 600 chars; submit_judgments(run_id, judge_id, model, verdicts); finalize_judgments(run_id, samples_required=3).
  * score_run(run_id, journey_run_ids=()) -> computes scoring.score_run (merging journeys), stores run["scores"], writes report via report.write_report, returns a compact summary (ERS, grade, coverage, sub-indices, DPR, top risk signals, limiting factor, report paths). get_report(run_id, format="summary"|"kpis"|"paths"). list_runs(host, limit).
  * judge_with(run_id, backend="cli"|"api"|"openai", samples=3) for the CLI path using judges.py (ClaudeCliJudge uses the Claude subscription via \`claude -p\`).
- mcp_server.py: MCPServer("jev-engagement", instructions=...) from the installed mcp 2.3 SDK (inspect the package in .venv to use the real API: decorator @server.tool(annotations=ToolAnnotations(...)), structured outputs from return type annotations, server.run() stdio). Tools: audit_shop, wait_run, get_run, run_journey, journey_act, journey_finish, get_judgment_tasks, submit_judgments, finalize_judgments, score_run, get_report, list_runs. Clear docstrings (the host reads them): explain the readiness vocabulary, that only offered element indices can be chosen, that judgments must quote snippets verbatim, and the safety rules. Annotations: readOnlyHint for get_*/list/wait, openWorldHint for audit/journey, destructiveHint False everywhere. Logging to stderr only (stdout is the protocol). main() entry point; graceful shutdown closes browsers.
- cli.py (argparse, entry point jev-engage): audit URL [--profiles mobile,desktop] [--stages ...] [--browser auto|launch|harness|cdp:URL] [--locale it] [--consent auto] [--repeats N] [--judge none|cli|api|openai] [--goal TEXT --oracle NAME --oracle-param k=v ... --policy typesafe] ; journey URL --goal ... --oracle ... --policy typesafe ; judge RUN_ID --backend cli|api|openai ; score RUN_ID [--journey RUN_ID ...] ; report RUN_ID ; list [--host H]. Prints compact human output (Italian) and the report path; --json for machine output. Exit codes non-zero on failure. Host-driven journeys are only for MCP; the CLI uses policy typesafe (needs TYPESAFE_API_KEY) and says so clearly when missing.
- __init__.py: export the public API lazily (audit_shop, JourneyRunner, EngagementService, score_run, build_report, RunStore, EngagementSettings) without importing heavy modules at package import time.
- Plugin (repo root = plugin root): .claude-plugin/plugin.json {name "jev-engagement", version "0.1.0", description, author}; .mcp.json {"mcpServers": {"engagement": {"command": "uv", "args": ["run", "--project", "\${CLAUDE_PLUGIN_ROOT}", "jev-engage-mcp"], "env": {"BH_TAB_MARKER": "0", "JEV_ENGAGEMENT_ARTIFACTS": "\${CLAUDE_PLUGIN_DATA}/runs"}}}}; skills/shop-readiness/SKILL.md (user-invocable, argument-hint "[url] [goal]", disable-model-invocation true): step-by-step orchestration: audit_shop -> wait_run polling -> optional run_journey driven per journey-driver rules -> journey_finish -> get_judgment_tasks pages -> spawn three engagement-judge subagents per batch (model sonnet) each submitting with judge_id j1..j3 and its model id -> finalize_judgments -> score_run (with the journey run) -> present results in Italian with the mandatory vocabulary and the report path; never place orders; never type personal data. skills/journey-driver/SKILL.md (user-invocable false): host policy rules adapted from jev_ultrafast/questions.py (always pass the observation_id of the latest observation; on stale=true read the returned observation and choose again, never resend; page_type "other" with a "the page did not load" note means the run stopped; choose only offered indices/operations, TYPE_TEXT values from the goal or visible page words only, never personal/payment data, DONE only when the goal is visibly met, prefer rejecting consent, stop at checkout). agents/engagement-judge.md: Sonnet subagent with no tools that returns verdict JSON exactly matching the task labels with verbatim quotes. Look up the current plugin/skill/agent frontmatter format in the Claude Code docs (https://code.claude.com/docs/en/plugins and /skills, /sub-agents; use WebFetch) and how plugin MCP tools are named for allowed-tools; then run \`claude plugin validate ${REPO}\` (the claude CLI is installed) and fix every error.
- Tests: tests/test_engagement_mcp.py exercises every tool through the real MCP protocol in-process or via a stdio client from the mcp SDK against \`uv run jev-engage-mcp\` with a fake transport factory or the local fixture shop + headless Chromium (skip without Chromium): list_tools names/annotations, audit -> wait -> judgments (fake verdicts that quote snippets) -> finalize -> score -> report paths; journey via host policy on the fixture shop; error handling (unknown run id, invalid target, selector-like target rejected). tests/test_engagement_cli.py: argparse paths and an end-to-end CLI audit on the fixture shop (served by the conftest shop_server) with --judge none, checking the report files exist.
Run ruff, your tests, the whole suite once at the end (\`uv run pytest -q\`), and \`claude plugin validate ${REPO}\`.`,
  lenses: [
    { key: 'protocol', text: 'MCP/CLI behaviour: start the server over stdio with the mcp SDK client in a scratch script and drive a complete session on the fixture shop (served locally) exactly as the shop-readiness skill describes, including concurrent tool calls, long audits (wait_run timeouts), journey idle timeout, unknown/invalid inputs, and shutdown (no leaked Chromium, no stdout pollution). Check tool output sizes stay far below 25k tokens. Run the CLI end to end. Report reproducible defects only.' },
    { key: 'plugin', text: 'Plugin and skills quality: validate with `claude plugin validate`, read the official Claude Code plugin/skills/subagent docs (WebFetch https://code.claude.com/docs/en/plugins-reference, /skills, /sub-agents) and check frontmatter fields, ${CLAUDE_PLUGIN_ROOT}/${CLAUDE_PLUGIN_DATA} usage, allowed-tools naming of plugin MCP tools, that the skill instructions are complete, unambiguous and enforce the safety rules and vocabulary, that judges are told to quote verbatim and use only closed labels, and that a user can actually install and run the plugin from this repo (document any manual step). Report concrete problems only.' },
  ],
}

const WS6B = {
  key: 'ws6b', phase: 'Docs', model: 'sonnet', reviewModel: 'sonnet',
  owned: 'README.md, AGENTS.md, docs/engagement-design.md, docs/engagement-kpi.md',
  prompt: `${COMMON}

YOUR WORKSTREAM: WS6b documentation (Sonnet). Owned: README.md, AGENTS.md, docs/engagement-design.md (new), docs/engagement-kpi.md (reconcile).
- README.md: keep the original agent sections accurate, and add a clear section "Engagement readiness per shop e-commerce" (Italian or English consistent with the README; the README is English, so write the section in English with Italian terms where they are UI strings) describing what it measures (the sub-indices and ERS, readiness not engagement), how to run it: CLI (\`uv run jev-engage audit https://shop.example --profiles mobile,desktop\`), as a Claude Code plugin (install from the repo, /jev-engagement:shop-readiness), MCP tools list, artifacts layout, judgments via the Claude subscription (host judges) with API fallbacks, safety (never orders, never personal data, stops at checkout), limits (bot detection, iframes/shadow DOM, consent, synthetic-agent validity) and the checks list. No performance or accuracy claims without a committed artifact; follow AGENTS.md "keep README claims consistent".
- AGENTS.md: keep the original rules and add concise engagement rules: host-driven policy chooses only observed indices (TypeSafe remains the default for the original agent), no orders/form submissions/personal data, vocabulary (readiness, predicted friction, risk signals), anchors/rubrics/profiles are versioned data (bump the version when values change), one owner index per KPI (kpis.py + anchors.json must match), evidence-based claims, and the full checks list (ruff, pytest, node --check on static/app.js, snapshot.js, engagement/vitals.js, engagement/audit.js, uv build, claude plugin validate .).
- docs/engagement-design.md (English): architecture and data flow (transport -> Browser -> PageCollector -> crawler/deception/checks -> observations -> judgments -> scoring -> report; journey path), contracts (RunRecord, PageRecord, Observation, JudgmentTask/Verdict, ScoreOutput), judgment protocol (why host judges: no MCP sampling in Claude Code), measurement hygiene, safety guard, scoring maths, limits and phase-2 list. Derive everything from the real code.
- Apply every item owed to WS6b in ${SP}/owed-notes.md (verify each against the current code first; the code wins).
- docs/engagement-kpi.md: reconcile every KPI row with the implemented producers (checks.py, deception.py, friction.py, judgments rubrics) and anchors.json values (exact numbers and units), fix any drift; keep Italian.
Verify every command you document actually works (run them against the local fixture shop: \`uv run python -m http.server -d tests/fixtures 8000\` in the background, then the CLI with --judge none), and every file path exists.`,
  lenses: [
    { key: 'consistency', text: 'Documentation truthfulness: check every claim, command, path, tool name, KPI number and unit in README.md, AGENTS.md, docs/engagement-design.md and docs/engagement-kpi.md against the real code and anchors.json (run the documented commands against the local fixture shop). Flag claims without evidence, outdated statements about the original agent, and vocabulary violations.' },
  ],
}


const WS1F = {
  key: 'ws1f', phase: 'Follow-ups', model: 'opus', reviewModel: 'opus',
  owned: 'jev_ultrafast/engagement/{audit.js,vitals.js,lexicon.py,collectors.py,pagetypes.py,safety.py}, tests/fixtures/shop/**, tests/test_engagement_collectors.py, tests/test_engagement_pagetypes.py, tests/test_engagement_safety.py',
  prompt: `${COMMON}

YOUR WORKSTREAM: WS1 follow-ups (items other workstreams are waiting for). Read ${SP}/owed-notes.md (sections fix:ws2:rev6, fix:ws3:r4 and the WS1 items in the earlier sections) and implement in your owned files:
1. Login gate rules per the Fable ruling (CONTRACTS.md "Rule d / login gate"): forms.login_required true for gate rule B (a registration password outside overlays whatever the entry path: autocomplete new-password, a second password in the same form, or password_new/register wording; current-password never) and rule C (no entry path; a registration-worded form without address fields is not one). Add lexicon key password_new and extend register with "nuovo cliente"/"new customer". Pin fixtures for the split layout (login box beside the shipping form: login_required False, password_required True) and the email-only "Crea un account" beside a login box (login_required True); flip the collectors test around the split fixture accordingly.
2. in_card / overlay flags on persuasion items (scarcity, urgency, lowest_price_30d, reciprocity, authority) and on prices[], and stop merging identical per-card statements, so that checks.py/deception.py can compare inside vs outside product cards (their code paths exist and are unit-tested but inactive; run tests/test_engagement_checks.py and tests/test_engagement_audit.py and confirm those paths now activate on the fixtures, reporting any WS2 follow-up needed instead of editing WS2 files).
3. variant_groups[].selected and first_available in pdp so the crawler's select_variant can work.
4. Cart totals in <dl><dt>Subtotale</dt><dd>..</dd><dt>Totale</dt><dd>..</dd></dl> (and table/grid label-value layouts) read correctly into subtotal_value/total_value.
5. vitals.js since(): report first_request_ms separately and keep first_response_ms for the first visible response (mutation/navigation) only, per ruling 8; friction.py already handles first_request_ms (check tests/test_engagement_friction.py still passes and that request_inclusive evidence goes away for new steps).
Keep payload bounds, verbatim snippets and the no-site-specific rule. Gate: ruff, node --check on the JS files, your tests plus tests/test_engagement_checks.py tests/test_engagement_audit.py tests/test_engagement_journey.py tests/test_engagement_friction.py (other workstreams edit other files in parallel; report, do not fix, failures in files you do not own).`,
  lenses: [
    { key: 'gates', text: 'Verify each follow-up on real headless Chromium with adversarial pages in the scratch dir (Magento/Shopify/WooCommerce-like checkout layouts: guest + login box, forced registration, email-only account creation, optional "Accedi" link; product pages with related-product cards carrying scarcity/urgency/prices; variant swatches with a preselected option and sold-out options; cart totals in dl/table/grid layouts), that login_required/password_required follow the ruling exactly, in_card flags are right, the WS2 inside/outside-card logic now activates correctly on the fixtures, first_request_ms/first_response_ms are separated, and nothing regresses. Report reproducible defects only.' },
  ],
}

const WS4F = {
  key: 'ws4f', phase: 'Follow-ups', model: 'opus', reviewModel: 'opus',
  owned: 'jev_ultrafast/engagement/{report,scoring,judgments}.py, jev_ultrafast/engagement/anchors.json, tests/test_engagement_report.py, tests/test_engagement_scoring.py, tests/fixtures/runs/**, tests/test_engagement_friction.py (only the strict xfail marker)',
  prompt: `${COMMON}

YOUR WORKSTREAM: WS4 follow-ups.
1. report.REASONS: give clear Italian labels to EVERY not-assessed / not-applicable / verification reason any producer can emit. Grep the producers (checks.py, deception.py, crawler.py, friction.py, journey.py, oracles.py, judgments.py, audit.py, collectors.py) for reason strings, list them, and add labels (the owed-notes.md sections fix:ws3:r4 and the WS2 completion list name navigation_error, journey_error, page_unreadable, cart_unreadable, final_page_unreadable, cart_items_unreadable, cart_price_unreadable, cart_price_ambiguous, accept_not_recognised, countdown_not_paired, sent_earlier, out_of_stock, audit_unavailable, not_requested, ...). Add a test that collects reason literals from the producers' source (simple regex over string literals passed as reason=/reason strings) and asserts each has a label or is covered by a documented generic prefix rule, so new reasons cannot ship unlabeled.
2. Regenerate the golden journey run in tests/fixtures/runs (journey_run.json / journey_steps.jsonl / journey_run.scores.json) so its observations are produced by friction.metrics from its steps (the stale values: FAI.DEAD_CLICK_RATE 20.0, FAI.LOSTNESS 0.0, PERF.ACTION_RESPONSE_MS 120.0), diff old vs new scores and explain changes, then remove the strict xfail on tests/test_engagement_friction.py test_golden_journey_observations_are_the_producers.
3. Run scoring.score_run on a real fixture audit (audit_shop on the local fixture shop, clean and ?dark, both profiles) merged with a fixture journey run and check the report renders every reason in Italian, coverage/grades are sensible, and DPR fires only on the dark shop; fix scoring/report issues you find.
Gate: ruff, your tests and tests/test_engagement_friction.py tests/test_engagement_docs.py.`,
  lenses: [
    { key: 'report', text: 'Verify: every reason any producer emits has an Italian label (write your own independent grep), the golden journey now matches friction.metrics and the xfail is gone, the report of a real fixture audit+journey reads correctly in Italian with correct vocabulary (readiness, predicted friction, risk signals; never "engagement misurato"/"violazioni"), no external resources, scores match a hand computation for a sample of KPIs. Report reproducible defects only.' },
  ],
}

function reviewPrompt(ws, lens, impl) {
  return `${COMMON}

You are an independent, skeptical REVIEWER for workstream ${ws.key} (owned files: ${ws.owned}). Do NOT edit repository files; scratch scripts go in ${SP}/scratch/.
Implementer report: ${JSON.stringify(impl ?? {}).slice(0, 6000)}
Review lens: ${lens.text}
"blocking" = real defects with concrete evidence and a precise fix. "minor" = nits. "design_doubts" = only significant questions needing an architect. Default to fewer, verified findings.`
}

function fixPrompt(ws, blocking, minor, rulings) {
  return `${COMMON}

You are the implementer for workstream ${ws.key} (owned files: ${ws.owned}). Reviewers found the problems below${rulings ? ' and the architect (Fable) issued rulings' : ''}. Verify each; fix every real one in your owned files (explain rejected ones in open_issues). Re-run ruff and tests.
Blocking: ${JSON.stringify(blocking).slice(0, 12000)}
Minor (fix if cheap and correct): ${JSON.stringify(minor).slice(0, 4000)}
${rulings ? 'Architect rulings: ' + String(rulings).slice(0, 8000) : ''}`
}

function fablePrompt(scope, doubts) {
  return `You are the senior architect (big design doubts are escalated to you). Context: ${COMMON}

${scope} raised these design doubts:
${doubts.map((d, i) => `${i + 1}. ${d}`).join('\n')}
Read the relevant code and documents, then give a concise, decisive ruling for each (what to do and why), keeping the approved plan and contracts stable unless a change is clearly necessary. Do not edit files.`
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
      rulings = await agent(fablePrompt(`Workstream ${ws.key} (owned files: ${ws.owned})`, doubts), { label: `fable:${ws.key}:${round}`, phase: ws.phase, model: 'fable' })
      doubts = []
    }
    history.push({ round, blocking: blocking.length, minor: minor.length, rulings: rulings ? String(rulings).slice(0, 3000) : null })
    log(`${ws.key} round ${round}: ${blocking.length} blocking, ${minor.length} minor${rulings ? ', Fable rulings issued' : ''}`)
    if (!blocking.length && !rulings) break
    const fixed = await agent(fixPrompt(ws, blocking, minor, rulings), { label: `fix:${ws.key}:${round}`, phase: ws.phase, model: ws.model, schema: IMPL_SCHEMA })
    if (fixed) { last = fixed; doubts = fixed.design_doubts || [] }
  }
  return { key: ws.key, impl: last, history }
}

// ---------------------------------------------------------------- WS5 then docs
const [ws5, ws1f, ws4f] = await Promise.all([buildWS(WS5), buildWS(WS1F), buildWS(WS4F)])
const ws6b = await buildWS(WS6B)

// ---------------------------------------------------------------- WS7 integration
phase('Integration')
const integration = await agent(`${COMMON}

YOUR TASK: WS7 end-to-end integration (you may edit ANY file to fix cross-workstream mismatches, but keep each fix minimal and list every file you touched with the reason).
1. Run the complete checks: \`uv run ruff check .\`, \`uv run pytest -q\`, \`node --check\` on jev_ultrafast/static/app.js, jev_ultrafast/snapshot.js, jev_ultrafast/engagement/vitals.js, jev_ultrafast/engagement/audit.js, \`uv build\`, \`claude plugin validate ${REPO}\`. Fix failures at the root cause.
2. End-to-end on the local fixture shop (serve tests/fixtures/ with python -m http.server on a free port): CLI audit with both profiles and --judge none; then the full MCP flow through a stdio client (audit -> wait -> host-driven journey to add a product under 50 EUR to the cart -> finish -> judgment tasks -> fake verdicts quoting snippets for 3 judges -> finalize -> score with the journey -> report). Open report.html with headless Chromium (Page.captureScreenshot to ${SP}/scratch/report.png) and check it renders, values are sane, vocabulary rules hold, nothing says "engagement misurato"/"violazioni".
3. Real-world smoke (best effort, do not fail the task if the network policy blocks it): the container has an HTTPS proxy in $HTTPS_PROXY whose CA is already in the browser NSS store. Launch with JEV_CHROME_ARGS or the chrome.py mechanism for extra args set to --proxy-server=$HTTPS_PROXY (check how chrome.py reads extra args; add a documented env hook if missing) and run \`uv run jev-engage audit <a public Italian or EU e-commerce home page> --profiles mobile --judge none\` against one or two well-known shops (pick ones unlikely to block bots, e.g. a Shopify demo/Italian Shopify store). Record what worked, what was not assessable and why (bot challenge, consent, iframes), timings, and any crash. Do not attempt to evade bot detection. Never place orders.
4. Fix every defect found (root cause, minimal, with tests where feasible) and re-run all checks.
Return what you verified, the artifacts produced (paths under ${SP}/scratch or artifacts/), the real-shop findings, and the files you changed.`, { label: 'integration', phase: 'Integration', model: 'opus', schema: IMPL_SCHEMA })

// ---------------------------------------------------------------- final review loop
phase('Review')
const LENSES = [
  { key: 'correctness', text: 'Correctness bugs in jev_ultrafast/engagement/** and the browser.py/agent.py changes: wrong logic, edge cases, error handling, resource leaks (Chromium, transports, threads), concurrency in the MCP service.' },
  { key: 'safety', text: 'Safety and AGENTS.md compliance: any path where a model/host can emit selectors or code, retried browser mutations, execution not logged before observation, checkout/pay/order submission, typing personal or payment data, leaving the start site, tests calling paid APIs or the public internet, credentials exposure, XSS in the report, path traversal in run ids.' },
  { key: 'measurement', text: 'Measurement validity: KPI producers vs kpis.py/anchors.json/docs (units, stages, aggregation, not-assessable handling), vitals/network accounting, deception tests that could produce false positives on honest shops, scoring maths, vocabulary rules in user-facing text.' },
  { key: 'simplicity', text: 'Simplicity and repo fit: dead code, duplicated helpers across modules, needless abstraction, over-long functions that hide bugs, inconsistent naming, missing tests for important branches. Only report items with a concrete, low-risk fix.' },
]
const seen = new Set()
const confirmed = []
let dry = 0
for (let round = 0; round < 3 && dry < 1; round++) {
  const found = (await parallel(LENSES.map(l => () => agent(`${COMMON}

You are an independent code reviewer (round ${round}). Review the whole engagement feature: \`git -C ${REPO} diff 1231850 --stat\` lists every change since the fork point; read the code. Lens: ${l.text}
Do not edit repository files (scratch scripts in ${SP}/scratch/ are fine; reproduce before reporting). Report only real, reproducible problems with exact file and line. Already known/handled (do not repeat): ${JSON.stringify([...seen]).slice(0, 6000)}`, { label: `find:${l.key}:${round}`, phase: 'Review', model: 'opus', schema: FINDINGS_SCHEMA })))).filter(Boolean).flatMap(r => r.findings)
  const fresh = found.filter(f => !seen.has(`${f.file}:${f.title}`))
  fresh.forEach(f => seen.add(`${f.file}:${f.title}`))
  if (!fresh.length) { dry++; log(`review round ${round}: nothing new`); break }
  const verified = (await parallel(fresh.map(f => () =>
    parallel([0, 1].map(i => () => agent(`${COMMON}

Adversarially verify this reported problem (verifier ${i + 1}). Try to REFUTE it: read the code, reproduce it with a scratch script or test. Default to real=false if you cannot reproduce or it is not actually a problem.
Finding: ${JSON.stringify(f)}`, { label: `verify:${f.file.split('/').pop()}:${i}`, phase: 'Review', model: 'opus', schema: VERDICT_SCHEMA })))
      .then(vs => ({ f, votes: vs.filter(Boolean) }))
  ))).filter(Boolean)
  const real = verified.filter(v => v.votes.filter(x => x.real).length >= 2).map(v => ({ ...v.f, reproduction: v.votes.map(x => x.reproduction).join(' | ').slice(0, 1500) }))
  log(`review round ${round}: ${fresh.length} new findings, ${real.length} confirmed`)
  if (!real.length) { dry++; continue }
  confirmed.push(...real)
  await agent(`${COMMON}

Fix these verified problems (you may edit any file; keep fixes minimal and at the root cause; add or adjust tests; then run \`uv run ruff check .\`, \`uv run pytest -q\`, node --check on the four JS files, \`uv build\` and \`claude plugin validate ${REPO}\`, and make them all pass):
${JSON.stringify(real).slice(0, 30000)}`, { label: `fix:review:${round}`, phase: 'Review', model: 'opus', schema: IMPL_SCHEMA })
}

return { ws5, ws1f, ws4f, ws6b, integration, confirmed }
