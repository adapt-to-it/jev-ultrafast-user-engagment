# Engagement readiness: design

How `jev_ultrafast/engagement/` turns the Jev browser loop into an auditor of e-commerce shops. This page describes what the code does; every number below is read from the code or from `anchors.json`. The Italian KPI catalogue ([engagement-kpi.md](engagement-kpi.md)) defines each KPI, its anchors and its sources. The loop of the original agent is in [design.md](design.md).

The output is an **engagement readiness** estimate from synthetic sessions: predicted friction and risk signals, never measured engagement. Nobody is observed and no conversion data is read.

## 1. What it is made of

Two ways to measure the same shop, feeding the same scores:

- **Audit.** A deterministic crawl of one shop per device profile: home, category listing (PLP), product page (PDP), cart, first checkout step. It never fills or submits anything there.
- **Journey.** One natural-language goal, driven through the original loop (page, indexed elements, operation and target, execution) by the host (Claude Code) or by TypeSafe, and verified afterwards by an independent oracle.

Judgments (seven closed-label questions about page texts) and scoring sit behind both.

| Layer | Files in `jev_ultrafast/engagement/` | Job |
| --- | --- | --- |
| Foundation | `settings.py`, `schemas.py`, `kpis.py`, `profiles.py`, `chrome.py`, `transport.py`, `store.py` | Run settings, data shapes, the KPI registry, device profiles, a Chromium of our own, CDP transports, run artifacts |
| Collection | `vitals.js`, `audit.js`, `lexicon.py`, `collectors.py`, `pagetypes.py` | One `PageRecord` per loaded page: timings, network, errors, a whole-document read, a page type, a screenshot |
| Audit | `crawler.py`, `deception.py`, `checks.py`, `safety.py`, `audit.py` | Funnel discovery, dark-pattern tests, KPI observations, the checkout guard, the per-profile loop |
| Journey | `journey.py`, `friction.py`, `oracles.py`, edits in `agent.py` | `JourneyRunner`, friction KPIs from recorded steps, independent verification |
| Judgments and scores | `judgments.py`, `judges.py`, `rubrics/*.json`, `anchors.json`, `scoring.py`, `report.py` | Tasks, verdict validation, fallback judges, normalisation and indices, JSON and HTML reports |
| Interfaces | `service.py`, `mcp_server.py`, `cli.py`, `.claude-plugin/`, `skills/`, `agents/` | One service behind the MCP server, the CLI and the Claude Code plugin |

The original agent keeps its behaviour by default. `Browser(url, *, transport=None, metrics=None, browser_context_id=None, background=True, prepare=None, load_timeout=15)` keeps its Browser Harness default; an explicit transport, a browser context and a `prepare` hook are what the engagement code adds. `Agent(url, goals, *, browser=None, policy=None, text_policy=None, ...)` uses `choose` and `field_text` when `policy` and `text_policy` are `None` (it tests `is not None`, not truthiness), so TypeSafe stays the default policy and a host can take its place.

## 2. Data flow

```text
settings ─▶ open_transport ─▶ transport (direct CDP websocket, or Browser Harness daemon)
                                   │
          one isolated browser context per profile (+ extra ones for the deception tests)
                                   │
                              Browser (one tab)  ◀── apply_profile, vitals.js before any page script
                                   │
                            PageCollector.load_page / collect
                 settle ─ vitals ─ network + errors from CDP events ─ audit.js ─ classify ─ screenshot
                                   │
                                   ▼  PageRecord (run.json), snapshots/<page_id>.json, shots/<page_id>.jpg
        ┌──────────────────────────┴───────────────────────────┐
   crawler.discover_funnel                              deception.run_deception_tests
   (home, plp, pdp, add to cart, cart,                  (two further isolated contexts,
    checkout entry; CheckoutGuard)                       same profile; run["deception"])
        └──────────────────────────┬───────────────────────────┘
                                   ▼
                      checks.observations(run) ─▶ run["observations"]   (producers: checks, deception)
                                   │
        judgments.make_tasks ─ host or fallback judges ─ submit ─ finalize ─▶ judged observations
                                   │
                    scoring.score_run(run, journeys=[...]) ─▶ run["scores"]
                                   │
                           report.write_report ─▶ report.json, report.html
```

Journey path:

```text
JourneyRunner.start ─▶ Agent(browser=<guarded tab>, policy=HostPolicy | choose)
   observation (CheckoutGuard-filtered elements, observation_id)
   act(operation, target, text, observation_id)  ─▶ executed once, logged to steps.jsonl BEFORE the next observation
   finish() ─▶ oracles.verify (independent, on the real page) ─▶ friction.metrics(steps) ─▶ run["observations"]
score_run(audit, journeys=[journey]) merges the journey-scope observations into the audit's scores
```

Facts that shape the code:

- Every page runs inside a context made by `profiles.new_context` (`Target.createBrowserContext`, `disposeOnDetach`), so storage and downloads stay isolated. One context per profile carries the whole funnel, because the cart lives in its cookies.
- `audit_shop` loops over the profiles: context, `PageCollector`, funnel, deception tests, context closed. Errors in one profile are recorded and the next profile still runs. Then `checks.observations(run)` writes the deterministic rows and the status is set: `complete` (every requested stage reached on every profile, no errors), `partial` (some funnel page collected, something missing) or `failed` (no funnel page).
- A bot challenge is evidence, not a funnel page: the funnel stops there and the remaining stages are `not_assessable` with reason `bot_challenge`.
- `steps.jsonl` is append-only and fsynced. Audits log the crawler's and the deception tests' clicks (`source`: `crawler` or `deception`); journeys log one record per step.

## 3. Interfaces

**Service.** `EngagementService` (`service.py`) is the one API. Audits run in a background thread (`start_audit` returns the run id at once, `wait_run` polls); host-driven journeys stay in memory between `journey_act` calls. Limits: one audit at a time per process (two audits would share CPU and network and skew each other's timings), three open journeys, a journey nobody drives for 10 minutes is closed.

**MCP server** (`jev-engage-mcp`, stdio). Twelve tools. Each returns a compact summary and paths, except the judgment-task pages (at most 15 tasks or 36,000 characters of task text) and the journey observations (at most 3,000 characters of page text), which carry page text by design:

| Tool | Purpose |
| --- | --- |
| `audit_shop` | Start the audit, return `{run_id, status: "running"}` |
| `wait_run` | Block up to 110 s, return the run summary and `timed_out` |
| `get_run` | Run summary: pages, not assessable stages, judgment progress, warnings |
| `run_journey` | Open a journey, return the first observation |
| `journey_act` | One choice on the latest observation; `observation_id` is required |
| `journey_finish` | Verify with the oracle, store friction KPIs, release the browser |
| `get_judgment_tasks` | A page of at most 15 judgment tasks (created on the first call) |
| `submit_judgments` | Validate and store one judge's verdicts |
| `finalize_judgments` | Majority label per task |
| `score_run` | Scores, `report.json`, `report.html`; merges finished journeys |
| `get_report` | `summary`, `kpis` or `paths` of the written report |
| `list_runs` | Recent runs with ERS and confidence |

**CLI** (`jev-engage`): `audit`, `journey`, `judge`, `score`, `report`, `list`. Human output is Italian, `--json` prints the service's JSON. A CLI journey uses the `typesafe` policy only; the host policy exists only through MCP. Exit status: 0 success, 1 failed run or step (a malformed or unknown run id included), 2 invalid arguments, 128 plus the signal number when interrupted, 141 when the reader of the output went away.

**Plugin** (the repository root is the plugin root). `.claude-plugin/plugin.json` declares the MCP server inline (`uv run --frozen --no-dev --project ${CLAUDE_PLUGIN_ROOT} jev-engage-mcp`, its virtual environment and artifacts under `${CLAUDE_PLUGIN_DATA}`), `.claude-plugin/marketplace.json` makes the repository installable, `skills/shop-readiness` orchestrates `/jev-engagement:shop-readiness <url> [goal]`, `skills/journey-driver` holds the host-policy rules and `agents/engagement-judge.md` is the Sonnet judge subagent.

## 4. Contracts

`schemas.py` holds the shapes as `TypedDict`s (`SCHEMA_VERSION = "engagement.v1"`). Fields may be added, optional and never renamed. Everything is plain JSON, so a run can be stored, replayed, scored and reported without a browser. `kpis.py` holds the registry of 89 KPIs; `anchors.json` holds their weights and anchors; tests assert that both describe the same ids.

| Shape | What it is |
| --- | --- |
| `RunRecord` | `run.json`: `run_id`, `kind` (`audit` or `journey`), `site`, `settings` (profiles, stages, browser, locale, consent, versions of anchors, rubrics, profiles, checks and deception), `pages`, `not_assessable`, `deception`, `journey`, `observations`, `judgments`, `scores`, `status` (`created`, `running`, `complete`, `partial`, `failed`), `warnings`, `errors` |
| `PageRecord` | One loaded page: `page_id` (`<profile>-<stage>-<n>`), `stage` (a funnel stage or `extra` for pages that are evidence only), `classification`, `vitals`, `network`, `errors`, `audit` (the `AuditPayload`), `screenshot`, `snapshot`, `visual`, `probes`, `consent`, `notes` |
| `Observation` | `{kpi_id, value, unit, source (deterministic or judged), page_id, profile, stage, assessed, reason, evidence}`. `assessed: false` means "not assessable" and carries a reason; it is never scored as 0 |
| `JudgmentTask` | `{task_id, rubric_id, rubric_version, kpi_id, page_id, question, labels, no_quote_labels, snippets, context, samples_required}` |
| `Verdict` | `{task_id, judge_id, model, label, confidence, evidence: [{snippet_id, quote}], rationale}` |
| `FinalJudgment` | `{task_id, kpi_id, label (null = uncertain), agreement, samples, confidence, models, evidence}` |
| `ScoreOutput` | `{anchors_version, weights, context: {journey, judged, journey_runs}, kpis, sub_indices, dpr, ers, top_risk_signals}`; `RunScores` is `{overall, profiles}` |
| `JourneyRecord` / `JourneyStep` | The goal, oracle, policy and verification of a journey run; one step per `steps.jsonl` line (operation, target index, timings, `since` counters, flags) |

`AuditPayload` is the output of one `Runtime.evaluate` of `audit.js` over the whole document, open shadow roots included (the indexed controls of `snapshot.js`, which the clicks use, are narrower: section 11). Sections are dictionaries and readers use `.get()`, so `audit.js` can grow without breaking them. It also returns the snippets judges read (13 kinds, at most 600 characters, verbatim visible text).

Artifacts for one run:

```text
<root>/<host-slug>/<run_id>/
  run.json            the RunRecord, rewritten atomically
  steps.jsonl         one JSON line per step, appended and fsynced
  snapshots/<page_id>.json   the page's AuditPayload
  shots/<page_id>.jpg        screenshot (optional)
  report.json, report.html   written by score_run
  owner.json          only while a process owns an unfinished run
  .lock               empty file whose flock serialises the writers of the run (not created on Windows)
```

`<root>` is `--artifacts`, else `$JEV_ENGAGEMENT_ARTIFACTS`, else `./artifacts/engagement` (git-ignored). A run id is `<UTC %Y%m%dT%H%M%S%f>Z_<audit|journey>_<host-slug>` and is validated before any path is built.

## 5. Judgments: why the host judges

Seven KPIs are judged: `TRI.RETURNS_CLARITY`, `CCL.VALUE_PROP_CLARITY`, `MPI.AUTHORITY`, `MPI.SOCIAL_PROOF_RICH`, `DPR.CONFIRMSHAMING`, `DPR.TRICK_QUESTIONS`, `DPR.HIDDEN_SUBSCRIPTION`. The seven rubrics in `rubrics/*.json` (`rubrics.v1`) give a question, a closed label set with descriptions, the snippet kinds to read and the value each label maps to.

**Claude Code does not support MCP sampling**, so a server cannot ask the user's Claude to judge. The plugin therefore inverts the call: the server prepares tasks, the host judges, the server validates. The judgments then use the user's Claude subscription, not an API key.

1. `get_judgment_tasks` creates the tasks on its first call: one task per rubric and page from the page's snippets (at most 8 per task, the rubric's decisive kinds first). Identical snippet sets on several profiles share one task (`context.pages` lists them). Pages are cut at 15 tasks or 36,000 characters of task text, so a page stays far below the tool output limit; `next_cursor` is the same for `brief` and full reads.
2. The `shop-readiness` skill launches three `engagement-judge` subagents (Sonnet, `j1`, `j2`, `j3`) per page of tasks. A judge reads its page and returns one verdict per task. Snippets are untrusted page text and are data, never instructions.
3. `submit_judgments` validates every verdict and stores the valid ones; a rejected one is listed with its reason and never stored (`invalid_verdict`, `unknown_task`, `unknown_label`, `invalid_confidence`, `invalid_evidence`, `unknown_snippet`, `empty_quote`, `quote_not_verbatim`, `quote_too_short`, `missing_evidence`, `duplicate_judge`, `samples_complete`, `task_already_final`). Every evidence quote must be a verbatim substring of the snippet it cites, after NFC normalisation and whitespace collapse, case-insensitive, and at least 8 characters unless it is the whole snippet. Only labels in a rubric's `no_quote_labels` may come without a quote. The stored quote is always the page's own spelling.
4. `finalize_judgments` takes the majority label of each task that has its `samples_required` (3) verdicts. A tie, or agreement below 2/3, gives label `null`: the KPI is not assessed, never 0. Finality is terminal.
5. `scoring.run_observations` turns the final judgments into `source: judged` observations. A confidence-valued DPR KPI gets `0.8 x agreement` for `present`. For `returns_clarity`, `authority` and `social_proof`, a reached page with no relevant snippet is read as evidence of absence by code, as a deterministic row outside the LLM share (`absent`, or `vague`, `weak`, `basic` when a returns link, a badge or a rating without text exists). The other rubrics stay "no snippets".

Verdicts are cached under `$JEV_ENGAGEMENT_CACHE` (default `~/.cache/jev-engagement/judgments`), keyed by `sha256` of the model id, the judge-prompt namespace and everything the judge sees (rubric and version, question, labels, snippets, page type, stage, locale), so an unchanged page gets identical judgments.

**Fallbacks** (`judges.py`, used by `jev-engage judge` and `jev-engage audit --judge`): `cli` runs `claude -p --output-format json --json-schema` without `--bare`, so the subscription login is used (it removes `ANTHROPIC_API_KEY` from the child's environment, runs with no tools and in an empty directory); `api` calls the Anthropic Messages API; `openai` calls an OpenAI-compatible endpoint. `SamplingJudge` exists behind the same interface and raises `NotSupported` until a client declares sampling. Every verdict from every backend goes through the same `submit` validation.

**What three judges are.** Three samples of the same model with the same prompt are a stability check, not three independent raters. The report says so and shows the models used; `llm_share` states how much of each index rests on judgments.

## 6. Measurement hygiene

- **Cold navigations.** Cache disabled, a fresh context per profile, every page loaded by URL (`Page.navigate`). Prerendering is disallowed. A document the shop's speculation rules prefetched before a click is not a cold navigation: its load timings are `not_applicable:speculative_navigation`. The document of an earlier record (a client-side route change, a back-forward cache restore) is `not_applicable:same_document`.
- **Visible pages.** Chromium is launched headless-new, where `visibilityState` is `visible`; a hidden page would paint nothing. If a page loads hidden, its paint metrics are not assessable (`background_tab`), not 0.
- **Device profiles** (`profiles.v1`). `mobile`: 390x844, DPR 3, touch, 150 ms latency, 209,715 B/s down, 96,000 B/s up, CPU 4x slower, a Chrome-on-Android user agent with matching client hints. `desktop`: 1366x768, DPR 1, no throttling, a Chrome-on-Windows user agent. No `Headless` in the identity, `Accept-Language` follows the locale. The profiles approximate Lighthouse's mobile preset; they do not replicate it and the scores are not comparable with Lighthouse or PageSpeed Insights.
- **Event capture.** `DirectTransport` (a websocket to a Chromium the tool launches or to `BU_CDP_WS`/`BU_CDP_URL`) keeps up to 200,000 events (`max_events`; `dropped` counts any it had to discard, and a page record notes `transport dropped N events`) and is not marked `lossy`. The Browser Harness transport drains a daemon whose ring buffer holds 500 events and is marked `lossy`: network KPIs then come from Resource Timing (`source: resource_timing`), which has a lower byte count for cross-origin resources. `auto` takes a browser named by `BU_CDP_WS` or `BU_CDP_URL` first, then a Chromium it launches, and falls back to the harness only when it finds none.
- **TTFB** is the CDP timing of the main Document request, from its first `requestWillBeSent` to the final `receiveHeadersEnd`, so redirects and the emulated latency are inside it. On a throttled profile there is no Navigation Timing fallback.
- **Settle.** A page is collected once the load event has fired, the network has been quiet for 1.5 s and the LCP for 2 s, or at the cap `settle_timeout_s` (20 s). The reason is recorded (`quiet`, `timeout`, `load_only`). A request whose response arrived and then got no data for 1.5 s is released; requests of the previous document never hold the settle; a request in flight past 5 s stops holding it and is noted. Long-lived connections and a page's own polling never hold it.
- **Units.** KB is bytes / 1000 and `%` is 0-100 everywhere. Counts stay integers.
- **Repeats.** `repeats` (1-5) loads each page again and reports medians per page.
- **Never retry a browser mutation.** An action is executed once. A stale page executes nothing and is observed again. A control already sent on a document is never sent again; a click that may have reached the page is never resent on that URL. Execution is logged before its result is observed.

## 7. Safety guard

`safety.CheckoutGuard` is consulted by the crawler, the deception tests and the journey runner, and again right before input.

- Never click a control whose label pays or confirms an order (`pay_now`, `place_order` in the lexicon, matched in every language). One exception: a same-site link whose observed href is a checkout URL (legacy Italian WooCommerce "Concludi ordine" means "Proceed to checkout").
- Typing is default-deny. Never into password, payment (`cc-*`, card, CVV, IBAN), personal-data (name, email, phone, address, fiscal code, birth date) or newsletter fields, nor into a form with a password or payment field, nor on a checkout page. Only search fields, labelled quantity fields and coupon fields remain. The one text an audit types is a word from an observed category label into the observed search field.
- A page is a checkout page when the classifier says so, when its observed fields ask for payment data, or when its URL is a checkout URL that is not a cart or a content page. The run stops at the first one. On a checkout page only scrolling, waiting and links that lead away are allowed.
- Nothing is done on a page of another registrable domain than the one the start URL lands on (a start URL that redirects to another domain anchors the audit and the journey there; the audit records it in `site.final_url`).
- A URL whose GET would change the cart (a cart-action query key such as `?add-to-cart=7` or `?remove_item=`, or a path such as `/cart/change` or `/carrello/svuota`) is never loaded: not as a product candidate, not as the cart page. A cart link that carries such keys is loaded without them.
- Text from a judge or a host never becomes a selector or code: the model chooses an operation and an observed element index, nothing else. A choice names the `observation_id` it was made on; any other id executes nothing.

Besides loading pages, an audit sends clicks on observed controls only: a variant choice when the product needs one, the add-to-cart, the links and buttons that lead to the cart and to the checkout entry (the cart's own guest-checkout control included), consent and overlay controls, and one typed search word. The one deliberate change to shop data is the add-to-cart, which puts an item into the session's own cart. The other clicks are navigation or interface choices, but on some platforms a checkout button or a guest control posts and opens a checkout session on the shop's side, and a consent choice can set cookies. Run it on shops you own or may test.

## 8. Scoring maths

`scoring.py` is pure functions over observations and `anchors.json` (`anchors.v1`).

**Per KPI.** Each KPI has a weight, a direction and either piecewise-linear `points` `[[value, score], ...]` (clamped to the first and last score) or a `map` for booleans and labels (a null score means not applicable). `provisional` marks anchors that are editorial rather than published; the flag changes no score. Rows are aggregated with the KPI's rule from `kpis.py` (`wmedian` with PDP and PLP weighted 2, `median`, `max`, `min`, `mean`, `sum`, `any`, `all`, `first`, `best`). A KPI observed on two or more profiles is aggregated per profile and the overall value is the profile that scores lowest: a shopper uses one device. An `any` KPI leaves out a profile whose false rests on an unobserved page, so a missing stage never makes a KPI worse.

**Sub-index.** The weighted mean of the assessed KPIs, weights renormalised:

```text
S_i = sum(w_k * n_k) / sum(w_k)       over assessed KPIs k of sub-index i, n_k in 0..100
coverage_i = assessed weight / applicable weight
```

Journey KPIs are not applicable when no journey ran, judged KPIs when judgments were not requested, and a KPI with reason `not_applicable...` leaves coverage; none of these lowers it. `llm_share` is judged weight over assessed weight.

**DPR** is a penalty, never an index with a weight:

```text
DPR = 100 * (1 - prod_p (1 - severity_p * confidence_p))       noisy-OR over assessed risk signals
```

Severities live in `kpis.py` (0.9 countdown reset, sneak into basket, hidden subscription; 0.6 fake low stock, pre-checked paid add-ons, confirmshaming, trick questions; 0.3 consent asymmetry, nagging). A signal not assessed adds no risk, so DPR coverage is reported beside it. `MPI.SCARCITY_SIGNALS` and `MPI.URGENCY_SIGNALS` score as absent (reason `credit_withheld:<DPR id>`) when their DPR signal fired with confidence of at least 0.5.

**ERS.**

```text
ERS_raw = exp( sum_i W_i * ln(max(S_i, 10)) / sum_i W_i )       W: FAI 22, TRI 20, PTI 15, CCL 15, PERF 18, MPI 10
ERS     = ERS_raw * (1 - 0.30 * DPR / 100)
```

The sum runs over sub-indices that have a score. The geometric mean is non-compensatory and the floor of 10 stops one empty index from zeroing everything.

**Publication.** The ERS is published only when overall coverage (the weighted mean of the sub-indices' coverage) is at least 60 % and every sub-index with weight at least 15 (all but MPI) has coverage of at least 50 %. Otherwise `ers.reason` says why, in Italian. The grade is a **coverage grade**: A at 85 %, B at 70 %, C at 60 %. The report, the CLI's `score` and `report` output and the `score_run` and `get_report` summaries write "Confidenza A (copertura 96 %)", never a bare grade (the data field is `grade`, a bare letter in every JSON output, the `list_runs` rows included; the one-line `jev-engage list` shows `ERS 86,7 (A)`, the only place in human-readable text where a letter stands alone), and an "Ambito" line says which families applied (audit only or with journey, with or without judgments, how much of the risk check ran).

A worked example with invented sub-index scores (FAI 70, TRI 80, PTI 60, CCL 75, PERF 55, MPI 40) gives a weighted arithmetic mean of 65.6 and `ERS_raw` 64.3; with PERF 20 the arithmetic mean is 59.3 and `ERS_raw` 53.6, which is the non-compensatory effect. One confirmed countdown reset (DPR 90) multiplies the first by 0.73. These numbers reproduce with `scoring.dpr` and `scoring.ers`; they are an illustration, not data of a shop.

**Versions.** `anchors.v1`, `rubrics.v1`, `profiles.v1`, `checks.v1`, `deception.v1`, `report.v1` and `engagement.v1`. `run.json` carries `schema_version` and its `settings` snapshot the anchors, rubrics, profiles, checks and deception versions (not the report's); `report.json` has `report_version` and a `versions` block with the schema, anchors, rubrics, profiles and report versions (not the checks or deception versions). Anchors, weights, rubrics and profiles are data: change a value, bump its version, and keep `docs/engagement-kpi.md` in step (`tests/test_engagement_docs.py` enforces ids, anchor points, units, stages and severities).

## 9. Journeys

- **Policy `host`** (default in Claude Code): the host picks the operation and an element index from the observation. `HostPolicy` produces a decision with the same shape as `choose()` (`probabilities: {choice: 1.0}`), validated against the observation's action space. The host's decision time is recorded as `decision_latency_ms` and excluded from the time attributed to the shop. Decisions are not reproducible.
- **Policy `typesafe`**: TypeSafe chooses (needs `TYPESAFE_API_KEY`, plus `TEXT_MODEL_API_KEY` for text fields). It is what `jev-engage audit --goal ... --oracle ...` uses, and what repeated runs need.
- **One tab per journey** in an isolated context with the device profile. Every observation is filtered by the guard before any policy sees it; text goes only into search, quantity and coupon fields, and text that looks like an email, a phone, card or account number, an IBAN, a fiscal code, a date, a street address or a card security code is refused (a best-effort net: a name cannot be told from a product word, so the field guard is the guarantee). The run stops at the first checkout page (`stopped_at_checkout_boundary`), on DONE or BLOCKED, on the step budget (`max_steps`, at most 60) and on an anti-bot page.
- **Step records** keep what the page did after the action: first visible response, mutations, navigations, requests, errors, post-input layout shifts, Event Timing. The time attributed to the shop is the page-side clock from just before input to settle, with the decision excluded. A page that navigated by itself between two steps becomes a `NAVIGATION` record.
- **Oracles** (`oracles.py`) read the real page after the journey and never trust DONE: `cart_contains_item_under_price`, `cart_not_empty`, `pdp_reached`, `search_results_shown`. Without a passing oracle a journey is not assessable when it ended in a harness error, on a page the browser did not load, after the guard withheld controls of an unreadable page, or when the outcome cannot be read without a guess.
- **Friction KPIs** (`friction.py`) are pure functions of `steps.jsonl`: success from the oracle, actions, actions over the declared minimum, time on the site, dead clicks, rage events, backtracking, Lostness (Smith 1996), unexpected navigations, post-input layout shift, interaction latency and action response.

## 10. Process lifecycle

Whatever stops a run (a signal, a server shutdown, a dead process, the idle reaper) leaves one terminal state: an audit becomes `failed` with an `interrupted: ...` error, a journey becomes `abandoned` and its run `partial` (or `failed` when it holds no page). A run still marked in progress by a process that no longer exists is closed the next time a service starts. The MCP server seals the runs in progress, kills its browsers and writes the marks inside the window Claude Code allows between SIGINT, SIGTERM and SIGKILL. Judgments and scores refuse an interrupted audit. A browser the tool launched is always closed, with its temporary profile.

## 11. Limits

- **Anti-bot pages.** A challenge or CAPTCHA page makes the later stages not assessable. The user agent and its client hints are the only identity set; nothing else about the browser is touched and no challenge is solved. For a shop that blocks automation, a journey can use `browser: harness` (the user's own Chrome), whose paint metrics may be not assessable.
- **iframes and shadow DOM.** `audit.js` reads open shadow roots. The controls the crawler clicks (variant choice, add-to-cart, consent and overlay buttons, the cart's guest control and checkout CTA) and the journeys offer come from `snapshot.js`, which does not enter shadow roots, so listing and product pages (and the cart, once the add-to-cart has been clicked, when only its link sits in a shadow root) are still loaded by their URL, but a step that must click a control in a shadow root, such as the add-to-cart, ends the funnel there with a reason (for example `add_to_cart_failed` or `checkout_cta_not_found`) and the later stages are not assessable. Cross-origin iframes (review widgets, payment forms) and closed shadow roots are only partly covered; the audit counts them (`a11y.iframes`, `a11y.shadow_roots_closed`) and the report flags judged KPIs that were read as absent beside such widgets.
- **Consent.** The landing is measured as it is; the banner is then handled by the `consent` policy (`auto` rejects when a reject control exists, accepts only when the banner blocks the funnel). The choice is recorded and a banner changes the first page's bytes.
- **Synthetic validity.** An agent is not a shopper. A host's choices vary between runs, three judges are one model, one variant of any A/B test is seen from one place with a cold cache on an emulated device, and lab data is not field data. Use the scores for ranking issues and for relative comparisons; absolute values are not validated against real behaviour.
- **Scope.** First checkout step only. Italian and English lexicons; the page's declared language picks the one used, unless its visible text reads as the run's language (an Italian shop whose theme declares `lang="en"` is read in Italian). The funnel is found through generic evidence (links, JSON-LD, URL patterns), so unusual shops may stop early and report why.
- **Not published without evidence.** No accuracy or speed claim is made for the auditor. The committed golden runs in `tests/fixtures/runs/` pin the data shapes and the scoring arithmetic, not the correlation of a score with real outcomes.

**Phase 2** (not built): full axe-core, Speed Index, CrUX and real INP, LCP sub-parts, checkout beyond its first step, calibration against GA4 and Microsoft Clarity, more lexicons (DE, FR, ES), warm-cache runs, recalibrated DPR severities, coverage over the whole catalogue, percentiles and confidence intervals over a corpus, infinite-scroll detection for `FAI.PLP_PAGINATION`, more deception tests and rubrics, and MCP sampling judges once clients support them. Details and sources are in section 8 of the KPI catalogue.

## 12. Checks

```bash
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
node --check jev_ultrafast/engagement/vitals.js
node --check jev_ultrafast/engagement/audit.js
uv build
claude plugin validate .
claude plugin validate --strict .claude-plugin/plugin.json
```

Tests are offline and never call a paid API. Browser tests launch a local Chromium against `tests/fixtures/shop/` and are skipped when none is found; judges, models and the MCP host are faked.
