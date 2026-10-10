# Engagement readiness: design

How `jev_ultrafast/engagement/` turns the Jev browser loop into an auditor of e-commerce shops. This page describes what the code does; every number below is read from the code or from `anchors.json`. The Italian KPI catalogue ([engagement-kpi.md](engagement-kpi.md)) defines each KPI, its anchors and its sources. The loop of the original agent is in [design.md](design.md).

The output is an **engagement readiness** estimate from synthetic sessions: predicted friction and risk signals, never measured engagement. Nobody is observed and no conversion data is read.

## 1. What it is made of

Two ways to measure the same shop, feeding the same scores:

- **Audit.** A deterministic crawl of one shop per device profile: home, category listing (PLP), product page (PDP), cart, first checkout step. It never fills or submits anything there.
- **Journey.** One natural-language goal, driven through the original loop (page, indexed elements, operation and target, execution) by Jev (TypeSafe, policy `typesafe`, the default whenever `TYPESAFE_API_KEY` is set) or by the host (Claude Code), and verified afterwards by an independent oracle.

Judgments (seven closed-label questions about page texts, each rubric judged by Jev or by Claude) and scoring sit behind both.

**Who chooses what.** Jev is the TypeSafe choice model behind `model.choose`. It has three roles, each with the same discipline as the original loop: it picks an observed element index or an offered snippet id, never a selector, a URL or text, the code consumes only the head that matches the choice, and a browser mutation is never retried.

| Role | Requests | What is consumed | Without `TYPESAFE_API_KEY` |
| --- | --- | --- | --- |
| Journey pilot (`Agent` + `model.choose`, unchanged) | One per decision: the operation head and the operation-specific target heads | The chosen operation's target | The host pilots (`host`) |
| Crawler fallback (`crawler.jev_pick`, `model.choose` with a fixed generic goal) | One per lookup the lexicon missed, at most six per profile | A `CLICK` target over guard-filtered click elements | The stage is not assessable, as before |
| Judge of the rubrics routed to it (`judges.judge_with_jev`) | One per judged page: label head, reversed label head, evidence heads per label over the offered snippet ids | The chosen label's evidence head | Claude judges every rubric |

Claude (the host's subagents, or a fallback judge from the CLI) judges the perception rubrics (`judge: claude` in `rubrics/*.json`) and every task Jev escalates, and pilots a journey when there is no key or the caller asks for `host`. The split follows what the question is: closed, literal and operationally defined questions go to Jev; tone, pressure, perceived clarity and anything that needs nuanced reading stay with Claude. Jev reads text only.

| Layer | Files in `jev_ultrafast/engagement/` | Job |
| --- | --- | --- |
| Foundation | `settings.py`, `schemas.py`, `kpis.py`, `profiles.py`, `chrome.py`, `transport.py`, `store.py` | Run settings, data shapes, the KPI registry, device profiles, a Chromium of our own, CDP transports, run artifacts |
| Collection | `vitals.js`, `audit.js`, `lexicon.py`, `collectors.py`, `pagetypes.py` | One `PageRecord` per loaded page: timings, network, errors, a whole-document read, a page type, a screenshot |
| Audit | `crawler.py`, `deception.py`, `checks.py`, `safety.py`, `audit.py` | Funnel discovery (lexicon first, then the Jev fallback), dark-pattern tests, KPI observations, the checkout guard, the per-profile loop |
| Journey | `journey.py`, `friction.py`, `oracles.py`, edits in `agent.py` | `JourneyRunner` (Jev's loop or the host's), friction KPIs from recorded steps, independent verification, what deciding cost |
| Judgments and scores | `judgments.py`, `judges.py`, `rubrics/*.json`, `anchors.json`, `scoring.py`, `report.py` | Tasks and their routing, verdict validation, Jev's judge and the Claude fallback judges, normalisation and indices, JSON and HTML reports |
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
    checkout entry; CheckoutGuard;                       same profile; run["deception"])
    Jev fallback on a lexicon miss)
        └──────────────────────────┬───────────────────────────┘
                                   ▼
                      checks.observations(run) ─▶ run["observations"]   (producers: checks, deception)
                                   │
        judgments.make_tasks ─ judge_with_jev (Jev) ─ host or fallback judges (Claude) ─ submit ─ finalize ─▶ judged observations
                                   │
                    scoring.score_run(run, journeys=[...]) ─▶ run["scores"]
                                   │
                           report.write_report ─▶ report.json, report.html
```

Journey path:

```text
JourneyRunner.start ─▶ Agent(browser=<guarded tab>, policy=choose (Jev, `typesafe`) | HostPolicy (`host`))
   observation (CheckoutGuard-filtered elements, observation_id)
   act(operation, target, text, observation_id)  ─▶ executed once, logged to steps.jsonl BEFORE the next observation
   finish() ─▶ oracles.verify (independent, on the real page) ─▶ friction.metrics(steps) ─▶ run["observations"]
score_run(audit, journeys=[journey]) merges the journey-scope observations into the audit's scores
```

Facts that shape the code:

- Every page runs inside a context made by `profiles.new_context` (`Target.createBrowserContext`, `disposeOnDetach`), so storage and downloads stay isolated. One context per profile carries the whole funnel, because the cart lives in its cookies.
- `audit_shop` loops over the profiles: context, `PageCollector`, funnel, deception tests, context closed. Errors in one profile are recorded and the next profile still runs. Then `checks.observations(run)` writes the deterministic rows and the status is set: `complete` (every requested stage reached on every profile, no errors), `partial` (some funnel page collected, something missing) or `failed` (no funnel page).
- A bot challenge is evidence, not a funnel page: the funnel stops there and the remaining stages are `not_assessable` with reason `bot_challenge`.
- `steps.jsonl` is append-only and fsynced. Audits log the crawler's and the deception tests' clicks (`source`: `crawler` or `deception`); journeys log each executed step twice, an execution line right after the input (`flags.unobserved`, before anything is read back) and a measurement line with the same `step` number once the settle is over; `RunStore.read_steps` keeps the last line per step number, so a process stopped during the settle leaves the execution line as the step's record (an executed, unmeasured action). Records without a `step` number (the crawler's and the deception tests') always append.
- TypeSafe requests are counted where they are made. `run["model_calls"]` holds `crawler_fallback` (`requests`, `latency_ms`, `input_tokens`, `model`, the lookups) and `judge_jev` (`requests`, `latency_ms`, `input_tokens`, `model`), merged by key and added up across calls; a journey keeps `model_calls`, `timing_ms` and `usage` in `run["journey"]` (section 9). A lookup that attempted a request counts even when it failed.

**The Jev fallback.** The lexicon crawl goes first and is reproducible. `discover_funnel(..., jev_fallback=True)` (`audit._audit_profile` passes `bool(os.environ.get("TYPESAFE_API_KEY"))`; off, nothing below runs) asks Jev where it finds nothing: the listing, product and cart links (`plp`, `pdp`, `cart`), and the add-to-cart, variant and checkout controls (`add_to_cart`, `select_variant`, `checkout_entry`). It asks the way the agent loop does, `model.choose(state, goal, [])` with a fixed generic English goal from `crawler.GOALS` (no page text and no site value in it), over the click actions `CheckoutGuard.filter_actions` allows that have an accessible name, are no field's click and fit the lookup's shape (`link_offer`, `add_offer`, `checkout_offer`); each lookup's own offer leaves out what it must never click for that stage (for example a quantity, coupon or add-on control, removing an item or emptying the cart, buying now, logging in). Only `operation == "CLICK"` and its target head are consumed; `DONE` or `BLOCKED` is "no element" (`jev_done`, `jev_blocked`) and a request or an answer that cannot be read is `jev_error`. A chosen link is loaded like a lexicon candidate (same site, no checkout URL, no cart-changing GET); a chosen element without a usable href is clicked once through `Tab.act`, so the guard, the never-resend rule and log-before-observe apply. The stage's own test (`is_listing`, `is_product`, `is_cart`, a checkout page) still decides, and a wrong pick becomes an `extra` page. One request per lookup (`Crawl.fallbacks`, a second miss sends nothing: `fallback_exhausted`), at most six per profile, none when `max_pages` leaves no room for the page it would lead to. Link lookups ask at the top of their start page (reloaded as `extra` when the tab has left it); consent, overlays, the search probe, repeats, the guest path and the deception tests are never asked. Each lookup is recorded in `PageRecord.probes["jev_fallback"]` (a list, on the page it was asked on: `stage`, `purpose`, `goal`, `model`, `latency_ms`, `usage`, `operation`, `target`, `label`, `href`, `probability`, `confidence`, `executed`, `reason`), in the page notes and, for a click, in `steps.jsonl` with note `jev_fallback`; `audit.py` adds them up in `run["model_calls"]["crawler_fallback"]` and warns "`<profile>: <stage> found through the Jev fallback: not reproducible across runs`" (or "`cart reached after a Jev pick (add_to_cart)`" when a click on the way was Jev's). The observation is viewport-only (`snapshot.js` offers elements whose centre is inside the viewport, at most 250), so a link below the fold cannot be chosen.

## 3. Interfaces

**Service.** `EngagementService` (`service.py`) is the one API. Audits run in a background thread (`start_audit` returns the run id at once, `wait_run` polls); host-driven journeys stay in memory between `journey_act` calls; a Jev-piloted journey runs in a background thread too (`run_journey` returns at once, `wait_run` polls, `journey_finish` returns the verdict). `resolve_policy` turns `policy="auto"` into `typesafe` when `TYPESAFE_API_KEY` is set and `host` otherwise; `JourneyRunner.start` itself keeps `host` as its default. Limits: one audit at a time per process (two audits would share CPU and network and skew each other's timings), three open journeys, a journey nobody drives for 10 minutes is closed.

**MCP server** (`jev-engage-mcp`, stdio). Thirteen tools. Each returns a compact summary and paths, except the judgment-task pages (at most 15 tasks or 36,000 characters of task text) and the journey observations (at most 3,000 characters of page text), which carry page text by design:

| Tool | Purpose |
| --- | --- |
| `audit_shop` | Start the audit, return `{run_id, status: "running"}` |
| `wait_run` | Block up to 110 s, return the run summary and `timed_out` |
| `get_run` | Run summary: pages, not assessable stages, judgment progress and escalations, `model_calls`, warnings, `server` (`typesafe_key`, `text_helper`: booleans and a model name, never a value) |
| `run_journey` | Open a journey. `policy` `auto` (default), `host` or `typesafe`: with Jev piloting it returns `{run_id, status: "running", policy, policy_requested, text_helper, next}` at once; with the host it returns the first observation |
| `journey_act` | One choice on the latest observation; `observation_id` is required (host journeys only) |
| `journey_finish` | Verify with the oracle, store friction KPIs, release the browser; returns `model_calls` and `timing_ms` |
| `judge_with_jev` | Jev judges the tasks of the rubrics routed to it (one request per page), escalates the rest, finalises; stops asking new pages after `JEV_BUDGET_S` (30 s) and reports `pages_left` and a `next` |
| `get_judgment_tasks` | A page of at most 15 judgment tasks (created on the first call); open ones are the Claude rubrics' and Jev's escalations |
| `submit_judgments` | Validate and store one judge's verdicts |
| `finalize_judgments` | Majority label per task |
| `score_run` | Scores, `report.json`, `report.html`; merges finished journeys |
| `get_report` | `summary`, `kpis` or `paths` of the written report |
| `list_runs` | Recent runs with ERS and confidence |

**CLI** (`jev-engage`): `audit`, `journey`, `judge`, `score`, `report`, `list`. Human output is Italian, `--json` prints the service's JSON. A CLI journey uses the `typesafe` policy only; the host policy exists only through MCP. `audit --judge jev` and `judge --backend jev` run Jev's judge (one sample per task); the tasks it leaves open wait for `judge --backend cli|api|openai`, and both stop with exit 1 before any browser starts when the key is missing. Exit status: 0 success, 1 failed run or step (a malformed or unknown run id included), 2 invalid arguments, 128 plus the signal number when interrupted, 141 when the reader of the output went away.

**Plugin** (the repository root is the plugin root). `.claude-plugin/plugin.json` declares the MCP server inline (`uv run --frozen --no-dev --project ${CLAUDE_PLUGIN_ROOT} jev-engage-mcp`, its virtual environment and artifacts under `${CLAUDE_PLUGIN_DATA}`), `.claude-plugin/marketplace.json` makes the repository installable, `skills/shop-readiness` orchestrates `/jev-engagement:shop-readiness <url> [goal]`, `skills/journey-driver` holds the host-policy rules and `agents/engagement-judge.md` is the Sonnet judge subagent.

**Keys.** They stay on the server. The MCP server reads its inherited environment plus the `KEY=value` file named by `JEV_ENGAGEMENT_ENV` (`cli.load_environment`, at start: a non-empty inherited variable wins over the file; quotes, an `export ` prefix and inline ` # comments` are understood; an unreadable file is logged by path, never by content). `plugin.json` sets it to `${CLAUDE_PLUGIN_DATA}/.env`. Whether Claude Code forwards the user's shell variables to a plugin's stdio server was not verified, so the file is the guaranteed path. The CLI reads `./.env`. `TYPESAFE_API_KEY` switches on the three Jev roles; `TEXT_MODEL_API_KEY` lets a Jev journey type (`TEXT_MODEL`, `TEXT_MODEL_BASE_URL`, `TEXT_MODEL_REASONING` as in `.env.example`); `TYPESAFE_MODEL` names the model (default `jev-latest`, an alias that moves on release: pin a version such as `jev-1.13.0` to let Jev's judge cache be read); `JEV_JUDGE_MIN_P` sets Jev's acceptance floor. Nothing writes a key.

## 4. Contracts

`schemas.py` holds the shapes as `TypedDict`s (`SCHEMA_VERSION = "engagement.v1"`). Fields may be added, optional and never renamed. Everything is plain JSON, so a run can be stored, replayed, scored and reported without a browser. `kpis.py` holds the registry of 89 KPIs; `anchors.json` holds their weights and anchors; tests assert that both describe the same ids.

| Shape | What it is |
| --- | --- |
| `RunRecord` | `run.json`: `run_id`, `kind` (`audit` or `journey`), `site`, `settings` (profiles, stages, browser, locale, consent, versions of anchors, rubrics, profiles, checks and deception), `pages`, `not_assessable`, `deception`, `journey`, `model_calls` (TypeSafe requests outside a journey, section 2), `observations`, `judgments`, `scores`, `status` (`created`, `running`, `complete`, `partial`, `failed`), `warnings`, `errors` |
| `PageRecord` | One loaded page: `page_id` (`<profile>-<stage>-<n>`), `stage` (a funnel stage or `extra` for pages that are evidence only), `classification`, `vitals`, `network`, `errors`, `audit` (the `AuditPayload`), `screenshot`, `snapshot`, `visual`, `probes` (among them `jev_fallback`, section 2), `consent`, `notes` |
| `Observation` | `{kpi_id, value, unit, source (deterministic or judged), page_id, profile, stage, assessed, reason, evidence}`. `assessed: false` means "not assessable" and carries a reason; it is never scored as 0 |
| `JudgmentTask` | `{task_id, rubric_id, rubric_version, kpi_id, page_id, question, labels, no_quote_labels, snippets, context, samples_required, escalation?}`. `samples_required` starts at 3 and `judgments.settle` lowers it to 1 for a task Jev accepted (never raises it); `escalation` is `{from: "jev", model, label, probability, reason, at}` on a task Jev passed to Claude |
| `Verdict` | `{task_id, judge_id, model, label, confidence, evidence: [{snippet_id, quote}], rationale}`. The `judge_id` `jev` is reserved to Jev's own namespace (`submit` refuses it from any other judge), which is how the report credits a final to Jev |
| `FinalJudgment` | `{task_id, kpi_id, label (null = uncertain), agreement, samples, confidence, models, evidence}` |
| `ScoreOutput` | `{anchors_version, weights, context: {journey, judged, journey_runs}, kpis, sub_indices, dpr, ers, top_risk_signals}`; `RunScores` is `{overall, profiles}` |
| `JourneyRecord` / `JourneyStep` | The goal, oracle, policy (`policy` resolved, `policy_requested`), `text_helper` and verification of a journey run, and what deciding cost (`model_calls`, `timing_ms`, `usage`, section 9); a step's execution and measurement lines in `steps.jsonl` (operation, target index, timings, `since` counters, flags) |

`AuditPayload` is the output of one `Runtime.evaluate` of `audit.js` over the whole document, open shadow roots included (the indexed controls of `snapshot.js`, which the clicks use, are narrower: section 11). Sections are dictionaries and readers use `.get()`, so `audit.js` can grow without breaking them. It also returns the snippets judges read (13 kinds, at most 600 characters, verbatim visible text).

Artifacts for one run:

```text
<root>/<host-slug>/<run_id>/
  run.json            the RunRecord, rewritten atomically
  steps.jsonl         JSON lines, appended and fsynced (a journey step: execution line, then measurement line)
  snapshots/<page_id>.json   the page's AuditPayload
  shots/<page_id>.jpg        screenshot (optional)
  report.json, report.html   written by score_run
  owner.json          only while a process owns an unfinished run
  .lock               empty file whose flock serialises the writers of the run (not created on Windows)
```

`<root>` is `--artifacts`, else `$JEV_ENGAGEMENT_ARTIFACTS`, else `./artifacts/engagement` (git-ignored). A run id is `<UTC %Y%m%dT%H%M%S%f>Z_<audit|journey>_<host-slug>` and is validated before any path is built.

## 5. Judgments: Jev, Claude and why

Seven KPIs are judged: `TRI.RETURNS_CLARITY`, `CCL.VALUE_PROP_CLARITY`, `MPI.AUTHORITY`, `MPI.SOCIAL_PROOF_RICH`, `DPR.CONFIRMSHAMING`, `DPR.TRICK_QUESTIONS`, `DPR.HIDDEN_SUBSCRIPTION`. The seven rubrics in `rubrics/*.json` (`rubrics.v2`) give a question, a closed label set with descriptions, the snippet kinds to read, the value each label maps to and the judge that answers it (`"judge": "jev" | "claude"`). The routing is read from the rubric when judging, never copied into the tasks.

| Rubric | `judge` | Why |
| --- | --- | --- |
| `returns_clarity` | `jev` | A time window plus one concrete condition: literal, operationally defined |
| `hidden_subscription` | `jev` | Recurring-charge wording against a CTA or cost line that presents the purchase as free or single |
| `authority` | `jev` | A named body, prize or certification number against a generic claim |
| `social_proof` | `jev` | Specific text plus one authenticity element; a rating alone does not count |
| `trick_questions` | `jev` | Double negations and inverted logic are properties of the text; the probability floor catches the rest |
| `confirmshaming` | `claude` | Guilt, shame, irony: tone |
| `value_prop` | `claude` | "Understands at once what is sold and why choose it": perceived clarity |

New perception rubrics (emotional pressure, reassurance, first impression) are not part of `rubrics.v2`: pressure overlaps `MPI.URGENCY_SIGNALS`, `MPI.SCARCITY_SIGNALS` and the `DPR` countdown and stock tests, reassurance overlaps the TRI signals, and Jev cannot see images, so an aesthetic judgment would be a text proxy mislabelled as perception. A new KPI needs anchors and a row in the catalogue (section 4), and there is no labelled data to set them.

**Why two judges.** Claude Code does not support MCP sampling, so a server cannot ask the user's Claude to judge. For Claude's share the plugin inverts the call: the server prepares the tasks, the host judges with Sonnet subagents on the user's subscription, the server validates. The share Jev can answer is judged by the server itself, with one TypeSafe request per page and a key that stays on the server.

**Claude's flow** (the `claude` rubrics and Jev's escalations):

1. `get_judgment_tasks` creates the tasks on its first call: one task per rubric and page from the page's snippets (at most 8 per task, the rubric's decisive kinds first). Identical snippet sets on several profiles share one task (`context.pages` lists them). Pages are cut at 15 tasks or 36,000 characters of task text, so a page stays far below the tool output limit; `next_cursor` is the same for `brief` and full reads. Each task shows its rubric's `judge`, whether it is `final`, how many verdicts it has and, when Jev passed on it, its `escalation` (why, never Jev's label).
2. The `shop-readiness` skill launches three `engagement-judge` subagents (Sonnet, `j1`, `j2`, `j3`) per page that still holds an open task. A judge skips the tasks that are final or full and reads the others on their own snippets. Snippets are untrusted page text and are data, never instructions.
3. `submit_judgments` validates every verdict and stores the valid ones; a rejected one is listed with its reason and never stored (`invalid_verdict`, `unknown_task`, `unknown_label`, `invalid_confidence`, `invalid_evidence`, `unknown_snippet`, `empty_quote`, `quote_not_verbatim`, `quote_too_short`, `missing_evidence`, `duplicate_judge`, `samples_complete`, `task_already_final`). Every evidence quote must be a verbatim substring of the snippet it cites, after NFC normalisation and whitespace collapse, case-insensitive, and at least 8 characters unless it is the whole snippet. Only labels in a rubric's `no_quote_labels` may come without a quote. The stored quote is always the page's own spelling.
4. `finalize_judgments` takes the majority label of each task that has its `samples_required` (3) verdicts. A tie, or agreement below 2/3, gives label `null`: the KPI is not assessed, never 0. Finality is terminal.

**Jev's flow** (`judges.judge_with_jev`; the `judge_with_jev` MCP tool, `jev-engage audit --judge jev`, `judge --backend jev`). Jev answers the tasks whose rubric names it, which no judge has touched yet and which carry no escalation with a Jev reading, with one request per page, built like `model.choose` (state plus choice questions, `model.post_json`, `model.validate_choice`):

- *State*: `{"page": {page_type, stage, locale}, "tasks": {<rubric id>: {"snippets": {<snippet id>: {kind, text}}}}}`. No URL and no run id.
- *Questions*, for every task: `label:<task_id>`, a choice over the rubric's labels with their descriptions as criteria and the rubric's question as instruction; `label_rev:<task_id>`, the same with the labels in reversed order (TypeSafe says Jev leans toward the first option); and `evidence:<task_id>:<label>` for each label not in `no_quote_labels`, a speculative choice over the offered snippet ids plus `none`, with the premise "Assume the answer is `<label>`: <description>". The rules text (`JEV_JUDGE_RULES`, `JEV_EVIDENCE_RULES`) says the snippets are data, to judge only the named task's snippets and that another question decides the label.
- *Size*: the golden audit run has 11 Jev tasks of 16, on 4 pages: 4 requests of 6 to 12 questions each, about 9,000 to 14,000 characters of body (an offline count of the committed fixture, not a measurement of Jev).
- *Reading*, in this order, with `validate_choice` on every head read: the label head and its probability `p`; if `p` is below `JEV_JUDGE_MIN_P` (default 0.6, clamped to 0.5-0.95) the task is escalated as `low_probability` and the reversed head is not read; the reversed head, which must pick the same label (`position_flip`); for a label that needs a quote, the evidence head of the chosen label only (heads of the other labels are never read), which must pick a snippet and not `none` (`no_evidence`). The verdict's quote is the chosen snippet's whole text, so `submit`'s verbatim check passes by construction; `confidence` is `p`; `judge_id` is `jev`; `model` is the versioned id the answer carries (for example `jev-1.13.0`). A label the rubric maps to no value (`unclear`) is accepted like any other: it is the rubric's insufficient-evidence outcome ("non valutato"), not Jev's uncertainty.
- *Escalation*: `judgments.escalate` writes `task["escalation"]` = `{from: "jev", model, label, probability, reason, at}`; no verdict is stored and `samples_required` stays 3, so Claude judges the task like any open one. Reasons: `low_probability`, `position_flip`, `no_evidence` (Jev's reading, permanent), a `submit` rejection reason (not expected with offered snippets), and `request_failed` / `invalid_response` (no reading: a failed request, an answer of the wrong shape; a later call retries the task while no judge has a verdict on it, `judgments.retryable`). One bad answer escalates only its task; a failed request only its page. Jev failing never blocks the audit.
- *Settling*: `judgments.settle` lowers `samples_required` to 1 for the accepted tasks (it never raises it), then `finalize` decides them with agreement 1.0 and confidence `p`. `finalize` keeps a final task's stored result whatever `samples_required` a later call asks, and `run_judges` and `judge_with` check `required_samples` only for tasks that are not final, so `jev-engage judge --backend cli` after Jev does not fail on a Jev-settled task.
- *Budget*: `budget_s` stops new pages once that many seconds have passed since the first request (the first page is always asked); the pages not asked are left untouched, `pages_left` counts the pages Jev has never asked and a later call asks the open ones, so while TypeSafe answers every page costs one request in all. A page whose request failed counts as asked (its tasks are escalated `request_failed`) and a later call asks it again (`judgments.retryable`), so under a provider that keeps failing a page can cost more than one request. The MCP tool passes `JEV_BUDGET_S` = 30 s and the service owns the end of the loop, whatever order `judges.py` asks pages in: `next` names `judge_with_jev` again only while pages are left and the call lowered `pages_left`; otherwise it names `get_judgment_tasks`, also when a call asked none of the never-asked pages (the budget went to a page that failed), so the host's judges take every task still open, those of the pages Jev never asked included. A call runs inside the run's lock for at most the budget plus one request, so verdicts the host submits meanwhile wait and are kept.
- *Accounting*: `run["model_calls"]["judge_jev"]` adds up requests, latency and input tokens across calls; the summary returns `available` (false, and nothing changes, without a key), `requests`, `latency_ms`, `input_tokens`, `model`, `judged`, `reused`, `escalated` by reason, `errors`, `finalize`, `open_tasks`, `pages_left`.

**What the score reads.** `scoring.run_observations` turns the final judgments into `source: judged` observations; Jev's and Claude's count alike, and `llm_share` is unchanged. A confidence-valued DPR KPI gets `0.8 x agreement` for `present`: agreement is 1.0 for a Jev sample by construction, so one Jev `present` is worth what three agreeing Claude samples are, and a 2/3 Claude split is worth less. That asymmetry is the price of settling a task with one sample; the probability floor and the reversed-order check are what stand in for the missing votes. For `returns_clarity`, `authority` and `social_proof`, a reached page with no relevant snippet is read as evidence of absence by code, as a deterministic row outside the LLM share (`absent`, or `vague`, `weak`, `basic` when a returns link, a badge or a rating without text exists). The other rubrics stay "no snippets".

**Cache.** Verdicts are cached under `$JEV_ENGAGEMENT_CACHE` (default `~/.cache/jev-engagement/judgments`), keyed by `sha256` of the model id, the judge-prompt namespace and everything the judge sees (rubric and version, question, labels, snippets, page type, stage, locale), so an unchanged page gets identical judgments. The namespaces never mix: `host` for the MCP host, `<backend>:<instructions hash>` for each fallback, `jev:<fingerprint>` for Jev, where the fingerprint is the hash of the request `JevJudge.request` builds for a fixed synthetic page, so a change to the rules text, the question ids, the state layout or the heads misses the cache with no manual bump. Jev's cache is read only for a pinned model id (`jev-<version>`), because an alias moves on release, and a cached sample counts only at or above today's floor.

**Fallbacks** (`judges.py`, used by `jev-engage judge` and `jev-engage audit --judge`): `cli` runs `claude -p --output-format json --json-schema` without `--bare`, so the subscription login is used (it removes `ANTHROPIC_API_KEY` from the child's environment, runs with no tools and in an empty directory); `api` calls the Anthropic Messages API; `openai` calls an OpenAI-compatible endpoint. `SamplingJudge` exists behind the same interface and raises `NotSupported` until a client declares sampling. Every verdict from every backend goes through the same `submit` validation. `run_judges` refuses a Jev judge: Jev goes through `judge_with_jev`. Jev judges first: it takes only tasks no other judge has touched, because a mix of samples could tie.

**What the judges are.** Three Claude samples of the same model with the same prompt are a stability check, not three independent raters. A Jev verdict is one sample plus an order check, not a second opinion. The report says so: with Jev finals its footer names Jev's rubrics and model, then Claude's rubrics and the tasks Jev passed on; Claude's finals are described by their real sample counts (the majority of N samples, or one sample with no majority after `judge --samples 1`); the footer lists the TypeSafe requests (`Richieste TypeSafe: fallback del crawler N · giudice Jev N`). `llm_share` states how much of each index rests on judgments, and `report.json` `judgments` adds `by_model`, `single_sample`, `escalated` and `claude_samples` (Claude's finals by sample count).

**What is not known yet.** The criteria are the rubrics' Italian labels, sent as written. TypeSafe documents that non-English text works "not equally well", and says Jev reads literally, miscounts and can be steered by page text. How often it escalates, how stable its labels are and whether a rubric needs English criteria (a versioned change) is for `scripts/smoke_engagement.py` to show; no live number is published here.

## 6. Measurement hygiene

- **Cold navigations.** Cache disabled, a fresh context per profile, every page loaded by URL (`Page.navigate`). Prerendering is disallowed. A document the shop's speculation rules prefetched before a click is not a cold navigation: its load timings are `not_applicable:speculative_navigation`. The document of an earlier record (a client-side route change, a back-forward cache restore) is `not_applicable:same_document`.
- **Visible pages.** Chromium is launched headless-new, where `visibilityState` is `visible`; a hidden page would paint nothing. If a page loads hidden, its paint metrics are not assessable (`background_tab`), not 0.
- **Device profiles** (`profiles.v1`). `mobile`: 390x844, DPR 3, touch, 150 ms latency, 209,715 B/s down, 96,000 B/s up, CPU 4x slower, a Chrome-on-Android user agent with matching client hints. `desktop`: 1366x768, DPR 1, no throttling, a Chrome-on-Windows user agent. No `Headless` in the identity, `Accept-Language` follows the locale. The profiles approximate Lighthouse's mobile preset; they do not replicate it and the scores are not comparable with Lighthouse or PageSpeed Insights.
- **Event capture.** `DirectTransport` (a websocket to a Chromium the tool launches or to `BU_CDP_WS`/`BU_CDP_URL`) keeps up to 200,000 events (`max_events`; `dropped` counts any it had to discard, and a page record notes `transport dropped N events`) and is not marked `lossy`. The Browser Harness transport drains a daemon whose ring buffer holds 500 events and is marked `lossy`: network KPIs then come from Resource Timing (`source: resource_timing`), which has a lower byte count for cross-origin resources. `auto` takes a browser named by `BU_CDP_WS` or `BU_CDP_URL` first, then a Chromium it launches, and falls back to the harness only when it finds none.
- **TTFB** is the CDP timing of the main Document request, from its first `requestWillBeSent` to the final `receiveHeadersEnd`, so redirects and the emulated latency are inside it. On a throttled profile there is no Navigation Timing fallback.
- **Settle.** A page is collected once the load event has fired, the network has been quiet for 1.5 s and the LCP for 2 s, or at the cap `settle_timeout_s` (20 s). The reason is recorded (`quiet`, `timeout`, `load_only`). A request whose response arrived and then got no data for 1.5 s is released; requests of the previous document never hold the settle; a request in flight past 5 s stops holding it and is noted. Long-lived connections and a page's own polling never hold it.
- **Units.** KB is bytes / 1000 and `%` is 0-100 everywhere. Counts stay integers.
- **Repeats.** `repeats` (1-5) loads each page again and reports medians per page.
- **Reproducibility of model choices.** Only the lexicon crawl, the checks and the oracles repeat by construction. TypeSafe has no seed or temperature parameter and documents its answers as stable, not identical. Jev's judge narrows the variance with closed labels, a probability floor, a reversed-order head and a cache keyed on the model id (read only for a pinned version); a stage found through the crawler fallback is flagged "not reproducible across runs"; a journey's choices, Jev's or the host's, are not seeded either, so repeat a journey from the CLI and read the medians. Every run records the versioned model id Jev answered with (`journey.model_calls.model`, `model_calls.judge_jev.model`, `model_calls.crawler_fallback.model`, the verdicts' `model`), which is what a comparison between runs needs. Whether Jev's labels hold still on real pages is measured by `scripts/smoke_engagement.py --repeat`, not assumed.
- **Never retry a browser mutation.** An action is executed once. A stale page executes nothing and is observed again. A control already sent on a document is never sent again; a click that may have reached the page is never resent on that URL. Execution is logged before its result is observed: a journey step's execution line is appended right after the input, its measurement line (same `step` number) after the settle.

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

`scoring.py` is pure functions over observations and `anchors.json` (`anchors.v2`).

**Per KPI.** Each KPI has a weight, a direction and either piecewise-linear `points` `[[value, score], ...]` (clamped to the first and last score) or a `map` for booleans and labels (a null score means not applicable). `provisional` marks anchors that are editorial, or a published threshold applied to a different quantity (`FAI.CHECKOUT_FIELDS`, `CCL.READABILITY`, the INP bands on the agent's clicks); the flag changes no score. Rows are aggregated with the KPI's rule from `kpis.py` (`wmedian` with PDP and PLP weighted 2, `median`, `max`, `min`, `mean`, `sum`, `any`, `all`, `first`, `best`). A KPI observed on two or more profiles is aggregated per profile and the overall value is the profile that scores lowest: a shopper uses one device. An `any` KPI leaves out a profile whose false rests on an unobserved page, so a missing stage never makes a KPI worse.

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

**Versions.** `anchors.v2`, `rubrics.v2`, `profiles.v1`, `checks.v1`, `deception.v1`, `report.v1` and `engagement.v1`. `anchors.v2` is `anchors.v1` with `FAI.CHECKOUT_FIELDS` and `CCL.READABILITY` flagged provisional (published thresholds transferred to a different quantity); no point or weight changed, so v1 and v2 scores compare. `rubrics.v2` is the version that added each rubric's `judge` routing; a change to the routing or to a rubric's labels bumps it. `run.json` carries `schema_version` and its `settings` snapshot the anchors, rubrics, profiles, checks and deception versions (not the report's); `report.json` has `report_version` and a `versions` block with the schema, anchors, rubrics, profiles and report versions (not the checks or deception versions). Anchors, weights, rubrics (their `judge` routing included) and profiles are data: change a value, bump its version, and keep `docs/engagement-kpi.md` in step (`tests/test_engagement_docs.py` enforces ids, anchor points, units, stages and severities).

## 9. Journeys

- **Policies.** `run_journey(policy)` takes `auto` (the MCP and service default), `typesafe` or `host`. `service.resolve_policy` turns `auto` into `typesafe` when `TYPESAFE_API_KEY` is set and into `host` otherwise; the journey record keeps both, `policy_requested` and the resolved `policy`. `JourneyRunner.start` keeps `host` as its default, validates the policy and refuses `typesafe` without the key. A CLI journey (`jev-engage journey`, `audit --goal`) is `typesafe`.
- **Policy `typesafe` (Jev).** The original loop, not a copy of it: `Agent` with `model.choose`, one TypeSafe request per decision carrying the operation head and the operation-specific target heads, the consumed target being the chosen operation's, the `questions.py` rules, the TYPE_TEXT handoff to the text LLM and its cache rule, the `Browser` freshness guards. The runner only wraps it for measurement: marks, `steps.jsonl`, guard-filtered observations, the oracle. In the service it runs in a background thread (`run_auto`, then `finish`); `journey_act` is refused on it, and the caller polls `wait_run` and reads `journey_finish`. Its budgets: `max_steps` executed steps (at most 60), at most 120 decisions (the Agent's `MAX_STEPS * 2`), `MAX_STALE` = 10 decisions in a row that executed nothing, and the Agent's no-progress stop (three actions in a row that changed nothing end the run as `blocked`).
- **Text under Jev.** Values for TYPE_TEXT come from the text LLM (`TEXT_MODEL_API_KEY`, `journey.text_helper` names the model). Without the key nothing is typed and no text is guessed: TYPE_TEXT stays offered, and a TYPE_TEXT Jev chooses is refused before any input (a logged attempt with flags `guard_blocked` and `text_withheld`, note and warning `text_helper_unavailable`, `checks.text_withheld_at` = `{url, label, step}`), after which fill actions are withheld from Jev for the rest of the run, because Jev has no notes channel and would send the same request again. The guard refusing the field or the text at input is handled the same way (`text_refused`). If the oracle then does not pass, the journey is not assessable with `text_helper_unavailable` or `text_refused`: the pilot's chosen path was denied, so a failed outcome is not the shop's. A journey whose pilot never chose to type is assessed as any other.
- **What deciding costs.** Written after every decision and again by `finish()` (by `close()` for an abandoned run; the service's shutdown or abandon mark also adds a decision whose step was still running), apart from the shop's time: `journey.model_calls` = `{choose: decisions (TypeSafe requests answered with a valid choice, or host choices), text: text helper calls that returned a value, text_failed: text helper calls that brought no usable text (an HTTP error, no value; 0 for the host), stale_or_refused: decisions that executed nothing, failed: TypeSafe decisions without a valid choice, model: the versioned TypeSafe model of the latest decision}`, `journey.timing_ms` = `{decision, text (failed text calls included), site (execution plus settle of the executed steps), wall}` and `journey.usage` = `{input_tokens, output_tokens}` (`{}` for the host). The report's journey card shows the pilot, the decisions and their time ("escluse dal tempo del sito") and the site time.
- **When Jev cannot decide.** If a Jev journey ends in `error` before its first step because TypeSafe gave no decision (a refused key, an unreachable provider: `model_calls.choose` 0, `failed` above 0, no step), nothing was executed, so the `shop-readiness` skill starts one `host` journey with the same goal; it never starts a second one after Jev executed a step.
- **Policy `host`** (without a key, or on request): the host picks the operation and an element index from the observation. `HostPolicy` produces a decision with the same shape as `choose()` (`probabilities: {choice: 1.0}`), validated against the observation's action space. The host's decision time is recorded as `decision_latency_ms` and excluded from the time attributed to the shop. Decisions are not reproducible.
- **One tab per journey** in an isolated context with the device profile. Every observation is filtered by the guard before any policy sees it; text goes only into search, quantity and coupon fields, and text that looks like an email, a phone, card or account number, an IBAN, a fiscal code, a date, a street address or a card security code is refused (a best-effort net: a name cannot be told from a product word, so the field guard is the guarantee). The run stops at the first checkout page (`stopped_at_checkout_boundary`), on DONE or BLOCKED, on the step budget (`max_steps`, at most 60) and on an anti-bot page.
- **Step records** keep what the page did after the action: first visible response, mutations, navigations, requests, errors, post-input layout shifts, Event Timing. The time attributed to the shop is the page-side clock from just before input to settle, with the decision excluded. A page that navigated by itself between two steps becomes a `NAVIGATION` record. Each executed step is written twice with the same `step` number: the execution line right after the input (`flags.unobserved`: `url_after`, `since` and the settle still empty) and the measurement line after the settle; `read_steps` returns the last one, so a step whose measurement never came counts as an action (`friction.executed`) and never as measured (dead clicks and the PERF interaction KPIs read measured interactions only; with none they are `no_measurement`).
- **Oracles** (`oracles.py`) read the real page after the journey and never trust DONE: `cart_contains_item_under_price`, `cart_not_empty`, `pdp_reached`, `search_results_shown`. Without a passing oracle a journey is not assessable when it ended in a harness error, on a page the browser did not load, after the guard withheld controls of an unreadable page, after Jev chose TYPE_TEXT and no text helper could write it (`text_helper_unavailable`) or the guard refused the text it wrote (`text_refused`), or when the outcome cannot be read without a guess.
- **Friction KPIs** (`friction.py`) are pure functions of `steps.jsonl`: success from the oracle, actions, actions over the declared minimum, time on the site, dead clicks, rage events, backtracking, Lostness (Smith 1996), unexpected navigations, post-input layout shift, interaction latency and action response.

## 10. Process lifecycle

Whatever stops a run (a signal, a server shutdown, a dead process, the idle reaper) leaves one terminal state: an audit becomes `failed` with an `interrupted: ...` error, a journey becomes `abandoned` and its run `partial` (or `failed` when it holds no page). A run still marked in progress by a process that no longer exists is closed the next time a service starts. The MCP server seals the runs in progress, kills its browsers and writes the marks inside the window Claude Code allows between SIGINT, SIGTERM and SIGKILL. Judgments and scores refuse an interrupted audit. A browser the tool launched is always closed, with its temporary profile.

## 11. Limits

- **Anti-bot pages.** A challenge or CAPTCHA page makes the later stages not assessable. The user agent and its client hints are the only identity set; nothing else about the browser is touched and no challenge is solved. For a shop that blocks automation, a journey can use `browser: harness` (the user's own Chrome), whose paint metrics may be not assessable.
- **iframes and shadow DOM.** `audit.js` reads open shadow roots. The controls the crawler clicks (variant choice, add-to-cart, consent and overlay buttons, the cart's guest control and checkout CTA) and the journeys offer come from `snapshot.js`, which does not enter shadow roots, so listing and product pages (and the cart, once the add-to-cart has been clicked, when only its link sits in a shadow root) are still loaded by their URL, but a step that must click a control in a shadow root, such as the add-to-cart, ends the funnel there with a reason (for example `add_to_cart_failed` or `checkout_cta_not_found`) and the later stages are not assessable. Cross-origin iframes (review widgets, payment forms) and closed shadow roots are only partly covered; the audit counts them (`a11y.iframes`, `a11y.shadow_roots_closed`) and the report flags judged KPIs that were read as absent beside such widgets.
- **Consent.** The landing is measured as it is; the banner is then handled by the `consent` policy (`auto` rejects when a reject control exists, accepts only when the banner blocks the funnel). The choice is recorded and a banner changes the first page's bytes.
- **Synthetic validity.** An agent is not a shopper. A host's choices vary between runs (Jev's have no seed either), Claude's three judges are one model, a Jev verdict is one sample, one variant of any A/B test is seen from one place with a cold cache on an emulated device, and lab data is not field data. Use the scores for ranking issues and for relative comparisons; absolute values are not validated against real behaviour.
- **Jev.** It reads text only, so it judges no image and no aesthetic. Its accuracy on the rubrics' Italian criteria, its escalation rate and the latency of 10-20k-token requests are untested until the live smoke runs; TypeSafe limits a request to 64k tokens and its state plus longest question to 32k. Page text is untrusted and TypeSafe says Jev is not trained for adversarial content: a hostile snippet can mislead a label or a pick, and the damage is bounded by what the code lets Jev choose (offered labels, snippet ids and guard-allowed elements), the probability floor, the reversed-order head, the stages' own tests and `CheckoutGuard`. The crawler fallback sees the viewport only (at most 250 elements) and asks once per lookup. The key reaches a plugin's server through the inherited environment or the `JEV_ENGAGEMENT_ENV` file; the first is not verified.
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

Tests are offline and never call a paid API. Browser tests launch a local Chromium against `tests/fixtures/shop/` and are skipped when none is found; judges, models and the MCP host are faked. The Jev tests replace `jev_ultrafast.model.post_json` (or the agent's `choose`) with stand-ins that check the request body (state, question ids, heads, no URL or run id in the state, only click actions in a crawler lookup) and answer with schema-valid choices, so the real request builders, `validate_choice` and the readers run. The one live path is `scripts/smoke_engagement.py` (README, "Live smoke (Jev)"): it is paid, outside the checks above, run by the user, and its `smoke_summary.json` is the artifact any Jev claim must cite.
