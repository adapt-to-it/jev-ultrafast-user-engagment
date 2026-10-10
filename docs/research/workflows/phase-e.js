export const meta = {
  name: 'engagement-phase-e-jev-roles',
  description: 'Bring Jev back as journey pilot, rubric judge and crawler fallback (Opus), Claude for perception rubrics; reviews with Fable rulings, docs (Sonnet), gate and verified final review',
  phases: [
    { title: 'Implement', detail: 'WS-A judge, WS-B pilot, WS-C crawler fallback (parallel), then WS-D surface' },
    { title: 'Review', detail: 'independent reviews per workstream, Fable rulings, fixes' },
    { title: 'Docs', detail: 'README, AGENTS.md, design doc, KPI catalogue, CONTRACTS (Sonnet)' },
    { title: 'Final', detail: 'gate and adversarially verified review of the Jev changes' },
  ],
}

const SP = args.sp
const REPO = '/home/user/jev-ultrafast-user-engagment'

const COMMON = `You are one engineer in a team bringing TypeSafe's Jev back as the fast navigator and judge of the engagement-readiness auditor in ${REPO}.
Read FIRST: ${SP}/jev-roles-decision.md (user decisions, binding: reuse the original Jev loop as it is), ${SP}/jev-roles-design-fable.md (the approved design: follow it, section numbers below refer to it), ${SP}/typesafe-api.md (API facts and limits), ${SP}/jev-code-survey.md (code map), ${SP}/CONTRACTS.md (end sections). The real code is the source of truth.
Rules: jev_ultrafast/model.py, questions.py, agent.py and snapshot.js stay as they are (reuse model.choose, post_json, validate_choice, action_space; do not fork or re-implement them). Jev only chooses observed indices or offered snippet ids, never selectors or text. Never retry a browser mutation; log execution before observing. Tests never call paid APIs or the public internet: stand-ins monkeypatch jev_ultrafast.model.post_json (or loop.choose) with fakes that validate the request body and return schema-valid answers. Other engineers edit other files in parallel: edit only files you own, never git commit/stash/checkout/reset. Scratch scripts in ${SP}/scratch/. Repo style: compact, ruff line-length 120, rules E,F,I.`

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
    blocking: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' } }, required: ['file', 'problem', 'evidence', 'fix'] } },
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
    findings: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, line: { type: 'integer' }, title: { type: 'string' }, problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' }, severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] } }, required: ['file', 'line', 'title', 'problem', 'evidence', 'fix', 'severity'] } },
    summary: { type: 'string' },
  },
  required: ['findings', 'summary'],
}
const VERDICT_SCHEMA = { type: 'object', properties: { real: { type: 'boolean' }, reasoning: { type: 'string' }, reproduction: { type: 'string' } }, required: ['real', 'reasoning', 'reproduction'] }

const WS = {
  A: {
    key: 'wsA', model: 'opus', owned: 'jev_ultrafast/engagement/{judges,judgments,report}.py, jev_ultrafast/engagement/rubrics/*.json, jev_ultrafast/engagement/schemas.py (additive keys only), tests/test_engagement_judgments.py, tests/test_engagement_report.py, tests/fixtures/runs/*',
    prompt: 'WS-A Jev judge: implement design decisions D1-D3 and section 1 (request shape built like model.choose: one systemone request per page with label head, reversed-order label head and per-label evidence heads over offered snippet ids; consumption with JEV_JUDGE_MIN_P escalation; settle/escalate/routing helpers; judge_with_jev driver and summary; run["model_calls"]["judge_jev"]), section 2.1-2.2 (rubric field "judge", rubrics.v2, routing table: jev for returns_clarity, hidden_subscription, authority, social_proof, trick_questions; claude for confirmshaming, value_prop), section 1.4 report changes (judgments.by_model, conditional footer, journey card pilot line when model_calls/timing_ms exist), the additive schema keys listed in section 7 (RunRecord.model_calls, JourneyRecord model_calls/timing_ms/usage/text_helper/policy_requested, JudgmentTask.escalation) FIRST so WS-B/C/D can rely on them, run_judges/judge_with paths not raising on Jev-settled tasks, report.REASONS label for text_helper_unavailable, golden fixtures regenerated under rubrics.v2 with a diff explanation, and the WS-A tests in section 6.',
    lens: 'Judge correctness: build requests from real fixture audit runs (audit_shop on the local fixture shop) and inspect them: one request per page, questions/criteria exactly from the rubric, evidence heads only for quote labels, no URLs/run ids in the state, sizes vs the 32k/64k limits in typesafe-api.md; feed adversarial fake answers (low p, position flip, none evidence, invalid probabilities, HTTP errors) and verify escalation, verbatim evidence, samples_required lowering only, finalize and scoring/report output, cache namespace, Claude rubrics untouched and still 3 samples. Report reproducible defects only.',
  },
  B: {
    key: 'wsB', model: 'opus', owned: 'jev_ultrafast/engagement/journey.py, tests/test_engagement_journey.py',
    prompt: 'WS-B Jev pilot: implement section 3.2 (D7: without TEXT_MODEL_API_KEY a typesafe journey withholds fill actions before Jev sees the page, notes it, and ends not_assessable text_helper_unavailable when the oracle does not pass) and section 3.3 (D8: journey model_calls, timing_ms, usage, text_helper persisted at finish() and close()), keeping the typesafe path as the original Agent + model.choose loop, JourneyRunner.start signature and POLICIES unchanged, plus the WS-B tests in section 6.',
    lens: 'Pilot invariants: verify with stand-in choose functions on the fixture shop (headless Chromium) that the typesafe journey is exactly the original Agent loop (no duplicated decision logic), fill actions are withheld only without a text key, the text_helper_unavailable rule matches the design, model_calls/timing_ms/usage are correct by hand (stale decisions counted, decision latency excluded from site time), host journeys unchanged. Report reproducible defects only.',
  },
  C: {
    key: 'wsC', model: 'opus', owned: 'jev_ultrafast/engagement/{crawler,audit}.py, tests/test_engagement_audit.py',
    prompt: 'WS-C crawler fallback: implement section 4 (D9): Tab.chooser hook on Tab.click misses and fallback_link for plp/pdp/cart URL discovery, jev_pick asking jev_ultrafast.model.choose with the fixed generic GOALS over the guard-filtered click-only observation (consume only CLICK), one request per stage, probes["jev_fallback"], run["model_calls"]["crawler_fallback"], the reproducibility warning, discover_funnel(..., jev_fallback=False) enabled in audit only when TYPESAFE_API_KEY is set, unchanged behaviour without a key, plus the WS-C tests in section 6.',
    lens: 'Fallback safety and reuse: verify on the fixture shop with a fake post_json that the fallback goes through model.choose unchanged, only guard-filtered click actions reach Jev, CheckoutGuard still refuses pay/place-order and checkout-page actions, nothing is retried (count Input.dispatchMouseEvent per stage), one request per stage, acceptance tests still decide the stage, probes/model_calls/warnings are correct, and without a key the run is byte-for-byte equivalent in behaviour (no post_json calls). Report reproducible defects only.',
  },
  D: {
    key: 'wsD', model: 'opus', owned: 'jev_ultrafast/engagement/{service,mcp_server,cli}.py, .claude-plugin/plugin.json, skills/**, agents/**, scripts/smoke_engagement.py, tests/test_engagement_mcp.py, tests/test_engagement_cli.py',
    prompt: 'WS-D surface: implement D6 (policy "auto" resolved in service.run_journey and as MCP default; typesafe journeys from MCP run in the background with wait_run then journey_finish), D10 (JEV_ENGAGEMENT_ENV env file loaded with setdefault by mcp_server.main, plugin.json env entry, get_run()["server"] = {typesafe_key, text_helper}), D11 (MCP tool judge_with_jev, CLI --judge jev and judge --backend jev, service BACKENDS += jev), section 3.4/3.5 skill and judge-agent text, and section 5 scripts/smoke_engagement.py (the live smoke the user runs locally with their key: fixture mode with forced plp fallback and real --url mode, --skip, --dump-requests, --repeat, smoke_summary.json, pass/fail rules; never orders), plus the WS-D tests in section 6. WS-A/B/C landed their parts just before you: read their code and use their real APIs.',
    lens: 'Surface: drive the MCP server over stdio (mcp SDK client) and the CLI with stand-in post_json (env TYPESAFE_API_KEY set to a dummy, a fake injected via a test hook) through the whole flow: audit with crawler fallback, judge_with_jev, Claude-only open tasks, run_journey auto -> typesafe in background -> wait_run -> journey_finish, score_run, report; without a key auto -> host and judge_with_jev available false. Check skill/agent instructions are consistent with the tools, claude plugin validate passes, and run scripts/smoke_engagement.py --help plus a dry run with a fake key + stand-in to confirm it fails cleanly (it must never call the real API in this container). Report reproducible defects only.',
  },
}

function reviewPrompt(ws, impl) {
  return `${COMMON}

You are an independent, skeptical REVIEWER for ${ws.key} (owned files: ${ws.owned}). Do NOT edit repository files.
Implementer report: ${JSON.stringify(impl ?? {}).slice(0, 6000)}
Lens: ${ws.lens}
"blocking" = real defects with concrete evidence and a precise fix; "design_doubts" = only significant questions for the architect.`
}

async function reviewLoop(ws, impl, rounds) {
  let last = impl
  let doubts = (impl && impl.design_doubts) || []
  const history = []
  for (let round = 0; round < rounds; round++) {
    const r = await agent(reviewPrompt(ws, last), { label: `review:${ws.key}:${round}`, phase: 'Review', model: 'opus', schema: REVIEW_SCHEMA })
    if (!r) break
    doubts = doubts.concat(r.design_doubts)
    let rulings = null
    if (doubts.length) {
      rulings = await agent(`You are the senior architect who wrote ${SP}/jev-roles-design-fable.md. Context: ${COMMON}

${ws.key} (owned files: ${ws.owned}) raised these design doubts:
${doubts.map((d, i) => `${i + 1}. ${d}`).join('\n')}
Read the code and give a concise, decisive ruling for each, keeping the design and the user's decisions stable unless a change is clearly necessary. Do not edit files.`, { label: `fable:${ws.key}:${round}`, phase: 'Review', model: 'fable' })
      doubts = []
    }
    history.push({ round, blocking: r.blocking.length, minor: r.minor.length, rulings: !!rulings })
    log(`${ws.key} review ${round}: ${r.blocking.length} blocking, ${r.minor.length} minor${rulings ? ', Fable rulings' : ''}`)
    if (!r.blocking.length && !rulings) break
    const fixed = await agent(`${COMMON}

You are the implementer for ${ws.key} (owned files: ${ws.owned}). The reviewer found the problems below${rulings ? ' and the architect issued rulings' : ''}. Verify each; fix every real one in your owned files with tests (explain rejected ones in open_issues). Apply the rulings. Re-run ruff and your tests.
Blocking: ${JSON.stringify(r.blocking).slice(0, 14000)}
Minor (fix if cheap and correct): ${JSON.stringify(r.minor).slice(0, 4000)}
${rulings ? 'Architect rulings: ' + String(rulings).slice(0, 8000) : ''}`, { label: `fix:${ws.key}:${round}`, phase: 'Review', model: ws.model, schema: IMPL_SCHEMA })
    if (fixed) { last = fixed; doubts = fixed.design_doubts || [] }
  }
  return { key: ws.key, impl: last, history }
}

function implPrompt(ws) {
  return `${COMMON}

YOUR WORKSTREAM (owned files: ${ws.owned}): ${ws.prompt}
Finish with \`uv run ruff check <your files>\` and your tests plus tests/test_agent.py; report exact counts.`
}

phase('Implement')
const [implA, implB, implC] = await Promise.all(['A', 'B', 'C'].map(k => agent(implPrompt(WS[k]), { label: `impl:${WS[k].key}`, phase: 'Implement', model: WS[k].model, schema: IMPL_SCHEMA })))
const reviewsABC = Promise.all([reviewLoop(WS.A, implA, 3), reviewLoop(WS.B, implB, 3), reviewLoop(WS.C, implC, 3)])
const implD = await agent(implPrompt(WS.D), { label: 'impl:wsD', phase: 'Implement', model: 'opus', schema: IMPL_SCHEMA })
const [rA, rB, rC] = await reviewsABC
const rD = await reviewLoop(WS.D, implD, 3)

phase('Docs')
const docs = await agent(`${COMMON}

YOUR WORKSTREAM: WS-E docs (Sonnet). Owned: README.md, AGENTS.md, docs/engagement-design.md, docs/engagement-kpi.md, ${SP}/CONTRACTS.md. Apply design section 7 row E: the AGENTS.md rule extending "TypeSafe chooses..." to judging (one request per judged page, label head + per-label evidence heads over offered snippet ids, consume only the chosen label's evidence head; Jev picks only observed indices and offered snippet ids; Claude judges perception rubrics and Jev's escalations); README: Jev is the default pilot when TYPESAFE_API_KEY is set, who judges what (Jev vs Claude and why), keys and the env file JEV_ENGAGEMENT_ENV, judge_with_jev, the crawler fallback, the live smoke procedure (scripts/smoke_engagement.py) and its limits; NO live Jev numbers (none were run). Update the design doc and the KPI catalogue sections on judgments, journeys and reproducibility; update CONTRACTS.md with the new APIs. Verify every command/path against the code and run the offline commands you document.`, { label: 'docs:wsE', phase: 'Docs', model: 'sonnet', schema: IMPL_SCHEMA })
const docsReview = await agent(`${COMMON}

Independent reviewer (do not edit files): check README.md, AGENTS.md, docs/engagement-design.md, docs/engagement-kpi.md against the real code for every claim, command, tool name, env var and rubric routing; flag any live-Jev performance claim (none was run), outdated statements and vocabulary violations.`, { label: 'review:docs', phase: 'Docs', model: 'sonnet', schema: REVIEW_SCHEMA })
let docsFix = null
if (docsReview && docsReview.blocking.length) {
  docsFix = await agent(`${COMMON}

Fix these documentation problems in README.md, AGENTS.md, docs/engagement-design.md, docs/engagement-kpi.md (verify each against the code first): ${JSON.stringify(docsReview.blocking).slice(0, 12000)}`, { label: 'fix:docs', phase: 'Docs', model: 'sonnet', schema: IMPL_SCHEMA })
}

phase('Final')
const gate = await agent(`${COMMON}

Final gate: run \`uv run ruff check .\`, \`uv run pytest -q -p no:cacheprovider\`, \`node --check\` on jev_ultrafast/static/app.js, jev_ultrafast/snapshot.js, jev_ultrafast/engagement/vitals.js and jev_ultrafast/engagement/audit.js, \`uv build --out-dir ${SP}/scratch/dist\`, \`claude plugin validate ${REPO}\`, \`git -C ${REPO} diff 1231850 --stat -- jev_ultrafast/model.py jev_ultrafast/questions.py\` (must be empty). Fix failures at the root cause (any file, minimal) and re-run. Report exact results.`, { label: 'gate', phase: 'Final', model: 'opus', schema: IMPL_SCHEMA })

const LENSES = [
  { key: 'reuse', text: 'Reuse of the original Jev loop and request style: any duplicated or re-implemented decision logic, prompts or request building; more than one TypeSafe request per decision/page/stage; Jev emitting anything but offered ids.' },
  { key: 'safety', text: 'Safety and AGENTS.md: retried mutations, guard bypass in the crawler fallback or typesafe journeys, typing without a text helper, keys leaking into logs/reports/run.json, tests reaching paid APIs or the internet, the smoke script ever placing orders.' },
  { key: 'correctness', text: 'Correctness of escalation, finalize/scoring with single Jev samples, report wording, model_calls/timing accounting, MCP/CLI behaviour with and without keys, background typesafe journeys.' },
]
const seen = new Set()
const confirmed = []
for (let round = 0; round < 2; round++) {
  const found = (await parallel(LENSES.map(l => () => agent(`${COMMON}

Independent reviewer (round ${round}; do not edit files; reproduce before reporting). Review the Jev-roles changes (\`git -C ${REPO} diff HEAD --stat\` plus the files in the design section 7). Lens: ${l.text} Already handled (skip): ${JSON.stringify([...seen]).slice(0, 5000)}`, { label: `find:${l.key}:${round}`, phase: 'Final', model: 'opus', schema: FINDINGS_SCHEMA })))).filter(Boolean).flatMap(r => r.findings)
  const fresh = found.filter(f => !seen.has(`${f.file}:${f.title}`))
  fresh.forEach(f => seen.add(`${f.file}:${f.title}`))
  if (!fresh.length) { log(`final review ${round}: nothing new`); break }
  const verified = (await parallel(fresh.map(f => () => parallel([0, 1].map(i => () => agent(`${COMMON}

Adversarially verify (verifier ${i + 1}): try to REFUTE this reported problem by reading the code and reproducing it. Default to real=false if you cannot reproduce it.
Finding: ${JSON.stringify(f)}`, { label: `verify:${f.file.split('/').pop()}:${i}`, phase: 'Final', model: 'opus', schema: VERDICT_SCHEMA }))).then(vs => ({ f, votes: vs.filter(Boolean) }))))).filter(Boolean)
  const real = verified.filter(v => v.votes.filter(x => x.real).length >= 2).map(v => v.f)
  log(`final review ${round}: ${fresh.length} new, ${real.length} confirmed`)
  if (!real.length) break
  confirmed.push(...real)
  await agent(`${COMMON}

Fix these verified problems (any file, minimal, root cause, with tests), then make \`uv run ruff check .\`, \`uv run pytest -q -p no:cacheprovider\`, node --check on the four JS files, \`uv build --out-dir ${SP}/scratch/dist\` and \`claude plugin validate ${REPO}\` pass:
${JSON.stringify(real).slice(0, 30000)}`, { label: `fix:final:${round}`, phase: 'Final', model: 'opus', schema: IMPL_SCHEMA })
}

return { rA, rB, rC, rD, docs, docsFix, gate, confirmed }
