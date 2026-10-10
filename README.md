<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

# Jev Ultrafast ⚡

> [!IMPORTANT]
> **The Browser Use Cloud waitlist is open.** Get early access to ultrafast browser agents in the cloud.
> **[Join the waitlist →](https://browser-use.com/ultrafast?utm_source=github&utm_medium=readme&utm_campaign=jev-ultrafast)**

**A browser agent with a dynamic, indexed action space.**

Give it one goal. [TypeSafe's Jev](https://docs.typesafe.ai/introduction) picks an operation and an element. A small LLM writes text only when the operation is `TYPE_TEXT`.

**Zürich → London on Google Flights in 7.1 seconds.** One natural-language goal, actual text generation, and loading waits included.

<a href="docs/demo.mp4"><img src="docs/demo.gif" alt="A real Google Flights search at 1× speed, with generated city names and dynamic operation/target decisions" width="100%" /></a>

[Watch the MP4](docs/demo.mp4) · [Measurements](docs/performance.md) · [Read the loop](jev_ultrafast/agent.py) · [Audit a shop's engagement readiness](#engagement-readiness-per-shop-e-commerce)

## The action space

Every observation produces a new element table:

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, and `BLOCKED`. Only supported operations and targets are offered.

```text
                      one TypeSafe request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
                                   │
                    CLICK [7] ─────┤──→ browser
                TYPE_TEXT [3] ─────┘
                          ↓
                   small LLM → text → browser
```

Target questions are speculative. If the operation is `CLICK`, only `click_target` can execute. Two decisions, **one network round trip**. Each target head contains only compatible elements. Native dropdown choices carry an observed element/option index.

There are no site-specific action scripts or prepared field strings in the policy. The Flights example supplies a goal and independently verifies the outcome. The screenshot renderer adds labels afterward; it does not drive the browser.

## Try it

```bash
git clone https://github.com/adapt-to-it/jev-ultrafast-user-engagment.git
cd jev-ultrafast-user-engagment
uv sync
cp .env.example .env
# Add TYPESAFE_API_KEY and TEXT_MODEL_API_KEY.
uv run jev
```

Open **http://127.0.0.1:8766** and click **Start demo → Run automatically**. The inspector shows numbered elements, operation probabilities, target probabilities, and executed actions. **Choose next** pauses before execution.

Chrome connects through [Browser Harness](https://github.com/browser-use/browser-harness), installed by `uv sync`. Run `uv run browser-harness --doctor` if it needs connecting. Allow remote debugging in Chrome when prompted.

`TEXT_MODEL_API_KEY` is an OpenRouter key in the example configuration. The current demo uses `inception/mercury-2.5` with reasoning disabled. Gemini, GLM, and DeepSeek can also use the OpenAI-compatible text helper; configure the appropriate model, endpoint, and reasoning setting.

## Use the library

```python
from jev_ultrafast import Agent

with Agent(
    "https://www.google.com/travel/flights?hl=en",
    "Find one-way flights from Zurich to London on September 20, 2026, "
    "for one adult in economy. Stop when matching flight options are visible.",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

Run with `uv run --env-file .env python your_script.py`. The same policy can run a different task:

```bash
uv run --env-file .env python examples/run.py \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.'
```

`uv run --env-file .env python examples/flights.py --keep-open` performs the flight search, checks the actual route/date/results, and saves its trace. It does not select or book a flight.

## Why it moves

- **One request per decision cycle.** Operation and target heads share the same observed state.
- **No screenshots in the default agent loop.** Jev consumes structured state. The inspector opts into screenshots; the video uses a separate continuous screencast.
- **One browser call per snapshot.** Read visible controls, their names, values, and text atomically. Keep references to the actual DOM nodes.
- **Validate the selected target.** Clicks check the document, form values, target, and nearby context. Animation alone does not force another prediction. Resolve current geometry and reject covered controls before input.
- **Wait for useful state.** After typing into a combobox, wait for visible suggestions, capped at 200 ms. Other interactions get at most two animation frames or 50 ms. These reads happen after execution is logged.
- **Keep hidden tabs rendering.** Focus emulation prevents background animation throttling without switching Chrome's visible tab.
- **Send visible text.** Offscreen article bodies and footers do not fill the model context.
- **Reuse an interrupted text request.** A generated value survives a stale-page retry only if the entire text-helper input is unchanged.

Every executed target is resolved from an observed node. The executor rechecks page freshness and click occlusion. Model output never becomes selectors, coordinates, shell commands, or executable JavaScript. Text-helper output must parse as a small JSON object before typing.

## Small enough to read

| File | Job |
| --- | --- |
| [agent.py](jev_ultrafast/agent.py) | The complete loop and text-helper handoff |
| [snapshot.js](jev_ultrafast/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
| [browser.py](jev_ultrafast/browser.py) | Browser connection, current geometry, execution |
| [model.py](jev_ultrafast/model.py) | Dynamic operation/target heads and text generation |
| [questions.py](jev_ultrafast/questions.py) | Model instructions |
| [demo.py](jev_ultrafast/demo.py) | Local inspector |
| [engagement/](jev_ultrafast/engagement) | Engagement-readiness auditor for e-commerce shops, built on this loop ([design](docs/engagement-design.md)) |

## Evidence and limits

The current video is a **7,073 ms** Google Flights run. Timing starts after initial page observation and includes model calls, generated text, browser work, stale decisions, and loading waits. A fresh independent check verifies the one-way setting, Zürich, London, September 20, 2026, and visible flight options. The video plays at 1×, with no opening hold and a 0.5-second final hold.

In six alternating runs with identical models and settings, both versions passed **3/3**. Median task time went from **9.450 s → 7.092 s**, a **25% reduction**; median browser protocol calls went from **1,092 → 101**. This is three repeats of one task on one browser profile, not a general reliability benchmark.

The same policy opened the requested Wikipedia article in **2.798 s** and passed a local hotel search/filter task in **1.896 s**. Runs, failures, source hashes, and measurement boundaries are in [performance.md](docs/performance.md).

A `DONE` choice still requires independent outcome verification. The DOM reader handles common HTML and ARIA controls, not the full accessible-name specification. Shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling, and arbitrary keyboard widgets remain outside this MVP (the engagement audit reads a little more of the page than the agent can click; see its limits below). Owned tabs of the default agent share the existing Chrome profile; engagement audits and journeys use isolated browser contexts.

## Engagement readiness per shop e-commerce

The same loop, pointed at an online shop, produces an **engagement readiness** estimate: how ready a shop is to be used all the way to the cart, from synthetic sessions. It is not measured engagement. Nobody is observed and no conversion data is read; the result is *predicted friction* and *risk signals*, ordered by priority, not a diagnosis and not a legal finding.

### What it measures

Every KPI belongs to exactly one index. The reports are in Italian; these are the names they use.

| Index | Report name | Weight | What it covers |
| --- | --- | --- | --- |
| PERF | Prestazioni | 18 | Core Web Vitals and other timings, page weight, requests, third parties, errors, response to the agent's actions |
| FAI | Attrito previsto | 22 | Search, listing controls, product page, cart, checkout fields, guest checkout, forced account, target size, overlays; from a journey: success, actions, time on the site, dead clicks, rage, backtracking, Lostness |
| TRI | Segnali di fiducia | 20 | HTTPS, contacts, legal id, policy links, payment methods, reviews, delivery times; judged: returns clarity |
| PTI | Trasparenza di prezzi e costi | 15 | Visible and consistent price, shipping and VAT before checkout, price change along the funnel, Omnibus wording, unexplained fees |
| CCL | Chiarezza e carico cognitivo | 15 | CTA salience, choice support, readability, headings, form labels; judged: value proposition |
| MPI | Leve di persuasione genuine | 10 | Genuine scarcity, urgency and reciprocity; judged: authority, rich social proof |
| DPR | Segnali di rischio dark pattern | penalty up to 30 % | Resetting countdown, inconsistent low stock, sneak into basket, pre-checked paid add-ons, consent asymmetry, nagging overlays; judged: confirmshaming, trick questions, hidden subscription |

The **ERS** (Engagement Readiness Score) is the weighted geometric mean of the six indices (floor 10 per index), times `1 - 0.30 x DPR / 100`. A page that could not be reached, or a KPI that could not be read, is "non valutabile" and never counts as 0. The ERS is published only with enough coverage (at least 60 % overall and 50 % in every index but MPI), and its grade is a **coverage grade**: A at 85 %, B at 70 %, C at 60 %. "Confidenza A (copertura 96 %)" means the assessed KPIs carried 96 % of the applicable weight, not that the score is high. Every report also shows the share of the score that rests on LLM judgments and an "Ambito" line saying which families applied (audit only or with a journey, with or without judgments). The 89 KPIs, their units, anchors, sources and limits are in [docs/engagement-kpi.md](docs/engagement-kpi.md); the maths and the contracts are in [docs/engagement-design.md](docs/engagement-design.md).

Two modes feed the same scores:

- **Audit.** Per device profile (`mobile` 390x844 with throttling, `desktop` 1366x768), a deterministic crawl: home, category listing, product page, add one item to the cart, cart, first checkout step. Dark-pattern tests then run in fresh isolated contexts. The lexicon crawler goes first; where it finds nothing, Jev is asked once which observed element to click ([the crawler fallback](#the-crawler-fallback)).
- **Journey.** One natural-language goal driven through the original loop (page, indexed elements, operation and target, execution), by Jev (the `typesafe` policy) whenever `TYPESAFE_API_KEY` is set and by Claude Code (the `host` policy) otherwise, and checked afterwards by an independent oracle on the real page (`cart_contains_item_under_price`, `cart_not_empty`, `pdp_reached`, `search_results_shown`). A DONE choice is not proof.

### Who does what: Jev and Claude

Jev, TypeSafe's choice model, navigates and judges what can be asked as a choice, keeping the round trips few: one request per decision, per judged page and per crawler lookup. Claude takes what needs reading between the lines. Without `TYPESAFE_API_KEY` everything falls back to Claude.

| Job | Who | How | Without the TypeSafe key |
| --- | --- | --- | --- |
| Pilot a journey | Jev | The original loop (`Agent` + `model.choose`): one request carries the operation head and the target heads, and only the chosen operation's target is consumed | Claude Code pilots (`host`): one MCP round trip and one deliberation per step |
| Find the next funnel stage where the lexicon finds nothing | Jev, as a fallback | The same `model.choose`, with a fixed generic goal such as "open the shopping cart page", over the observed click elements the checkout guard allows; only a `CLICK` target is read | The stage is "non valutabile", as before |
| Judge the operational rubrics: returns clarity, hidden subscription, authority, social proof, trick questions | Jev | One request per judged page, one sample per task: a label head, the same head with the labels reversed, and an evidence head per label over the offered snippet ids | Claude's judges, three samples |
| Judge the perception rubrics (confirmshaming, value proposition) and whatever Jev is not sure about | Claude | Three Sonnet samples through Claude Code on your subscription, majority vote | The same |

Why this split. Jev is built for narrow, literal, closed-label questions answered in one request. TypeSafe's own notes on jev-1.13 say it reads instructions literally, is weak at counting and arithmetic, leans toward the first option and generates no text. That suits closed questions over literal wording (a return window plus a concrete condition, a named certifying body, recurring-charge wording), which go to Jev: it only picks a label and a snippet id, code copies that snippet's own text as the evidence, and a reversed-order head checks the lean toward the first option. Tone, guilt, pressure and perceived clarity need nuanced reading and stay with Claude. Jev never writes text, selectors or URLs, and its uncertainty is handled by escalating to Claude, not by averaging.

### Run it from the command line

```bash
uv sync
uv run jev-engage audit https://shop.example --profiles mobile,desktop
```

The audit needs a Chromium or Chrome. With `--browser auto` (the default) it takes, in this order:

1. a browser you started yourself, named by `BU_CDP_WS` or `BU_CDP_URL`. These win over everything: unset them if they are left over from a Browser Harness setup and you want a fresh browser;
2. a Chromium it launches itself, the first found of the executable named by `JEV_CHROME_PATH`, `CHROME_PATH` or `BH_CHROME_PATH`, a Chrome or Chromium on the `PATH`, a Playwright Chromium on Linux (under `PLAYWRIGHT_BROWSERS_PATH`, `/opt/pw-browsers` or `~/.cache/ms-playwright`), and Chrome in its usual macOS or Windows application folder;
3. your own Chrome through Browser Harness, only when step 2 finds nothing.

`--browser launch` skips step 1 and fails when no Chromium is found; `harness` and `cdp:<url>` choose the others explicitly. To try it without touching a real shop, serve the test fixtures:

```bash
uv run python -m http.server --bind 127.0.0.1 -d tests/fixtures 8000 &
uv run jev-engage audit http://127.0.0.1:8000/shop/index.html --profiles mobile,desktop
```

The output has this shape (Italian, numbers elided; progress lines go to stderr):

```text
Run <run id>: completa (<n> pagine)
  <profile>  <stage>  <page type>  LCP <ms> ms · CLS <n> · <n> KB        one line per page
Engagement readiness (stima da sessioni sintetiche, non engagement misurato)
  ERS <score> · Confidenza <grade> (copertura <n> %) · quota LLM <share>
  Ambito: audit deterministico · senza journey · senza giudizi LLM · rischio dark pattern valutato 6/6
  Prestazioni  <score>  Confidenza <grade> (copertura <n> %)  limitanti: <KPI ids>
  ...                                                        one line per index, six in all
  Segnali di rischio dark pattern: <DPR score>
  fattore limitante: <the lowest index>
Rapporto: <artifacts>/<host>/<run id>/report.html
```

| Command | What it does |
| --- | --- |
| `jev-engage audit URL` | Audit one shop. `--profiles mobile,desktop`, `--stages home,plp,pdp,cart,checkout_entry`, `--consent auto\|reject\|accept\|none`, `--repeats 1-5`, `--max-pages N`, `--locale it\|en` |
| `jev-engage audit URL --goal "..." --oracle cart_not_empty` | Add a journey piloted by Jev (policy `typesafe`; needs `TYPESAFE_API_KEY`, plus `TEXT_MODEL_API_KEY` for text fields); `--oracle-param max_price=50`, `--optimal-steps`, `--optimal-pages`, `--journey-profile` |
| `jev-engage audit URL --judge jev\|cli\|api\|openai` | Also run the LLM judgments (see [Who judges what](#who-judges-what)). `jev` needs `TYPESAFE_API_KEY`: Jev judges the rubrics routed to it and leaves the rest open; `cli` (your Claude subscription), `api` and `openai` are the Claude-side backends; `--samples 1-5`, `--judge-model` |
| `jev-engage journey URL --goal ... --oracle ...` | A `typesafe` (Jev) journey on its own |
| `jev-engage judge RUN_ID [--backend cli\|api\|openai\|jev] [--samples N] [--model M]`, `score RUN_ID [--journey RUN_ID]`, `report RUN_ID [--format summary\|kpis\|paths]`, `list [--host H]` | Work on stored runs; `judge` defaults to `--backend cli` |

With `TYPESAFE_API_KEY` in `./.env` or the environment, `audit` also lets the crawler ask Jev where the lexicon finds nothing (at most six requests per profile); there is no other switch, so unset the key for a purely deterministic audit. `--judge jev` and `judge --backend jev` stop with exit status 1, before any browser starts, when the key is missing.

Every command takes `--artifacts DIR` (default `$JEV_ENGAGEMENT_ARTIFACTS`, else `./artifacts/engagement`) and `--json`. Only `audit` and `journey` also take `--browser auto|launch|harness|cdp:<url>`, `--headed`, `--locale` and `--chrome-arg=--proxy-server=...` (repeatable, for a launched Chromium; `JEV_CHROME_ARGS`, a shell-quoted string such as `JEV_CHROME_ARGS="--proxy-server=http://proxy:3128"`, adds the same extras to every Chromium the CLI or the MCP server launches). Without `--judge` the judged KPIs are simply not applicable and do not lower the coverage. Exit status: 0 on success, 1 when the run failed or a run id is malformed or unknown, 2 for invalid arguments (a URL that is not http or https included), 128 plus the signal number when interrupted (the browser is closed and the run marked failed), 141 when the reader of the output went away (for example `| head`).

### Run it as a Claude Code plugin

The repository root is a Claude Code plugin (`jev-engagement`) with an MCP server, a `shop-readiness` skill, a `journey-driver` skill and an `engagement-judge` subagent. Install it from a clone:

```bash
claude plugin marketplace add <path to this repository>
claude plugin install jev-engagement@jev-engagement-local
```

(A GitHub `owner/repo` source works once `.claude-plugin/marketplace.json` is on the repository's default branch.) The install copies the clone as it is into `~/.claude/plugins/cache`, so a clone that holds a `.venv` copies it too (about 100 MB): install from a fresh clone, or run `claude --plugin-dir <path to this repository>` for one session, from any directory, which uses the clone in place. Then, in Claude Code:

```text
/jev-engagement:shop-readiness https://shop.example
/jev-engagement:shop-readiness https://shop.example Aggiungi al carrello un paio di scarpe da corsa sotto i 50 euro
```

The skill audits the shop, optionally runs a shopping journey (piloted by Jev when the server holds a TypeSafe key, else driven step by step by Claude Code), lets Jev judge the operational rubrics and three Sonnet judges read the rest (the perception rubrics and whatever Jev passed on), scores everything and presents the result in Italian with the path of `report.html`, the models that ran and the number of TypeSafe requests.

**Keys for Jev.** Jev needs `TYPESAFE_API_KEY`, and `TEXT_MODEL_API_KEY` to type into search or quantity fields (see `.env.example`). The MCP server reads the environment it inherits plus the `KEY=value` file named by `JEV_ENGAGEMENT_ENV`, which `plugin.json` sets to `${CLAUDE_PLUGIN_DATA}/.env`: the `.env` next to the `engagement` folder that `list_runs` reports as `root` (with `--plugin-dir`: `~/.claude/plugins/data/jev-engagement-inline/.env`). A variable the server inherited with a value wins over the file; a quoted value and an inline ` # comment` are understood. The server reads the file only when it starts, so reconnect it (`/mcp`) or restart Claude Code after adding a key. This repository did not verify whether Claude Code forwards your shell's variables to a plugin's server, so the file is the sure path. Keep it readable by you only. `get_run` returns `server: {typesafe_key, text_helper}` (booleans and the text model's name, never a value) and the skill tells you from it who will pilot and judge. Nothing ever writes a key.

Prerequisites: `uv` on the `PATH` that Claude Code sees (a GUI-launched app on macOS may lack `~/.local/bin`), and Chrome or Chromium (set `JEV_CHROME_PATH` otherwise; without any, the server falls back to your Chrome through Browser Harness). The first session installs the server's dependencies into the plugin's data directory; on a slow network raise `MCP_TIMEOUT` so Claude Code waits for it. The background judge subagents may ask permission for the tool that reads their tasks: allow `mcp__plugin_jev-engagement_engagement__get_judgment_tasks` in your permission settings to avoid the prompt. Runs are stored in `${CLAUDE_PLUGIN_DATA}/engagement` (with `--plugin-dir`: `~/.claude/plugins/data/jev-engagement-inline/engagement`; `list_runs` reports the `root`) and are deleted on uninstall unless you pass `--keep-data`.

MCP tools (server `engagement`, stdio). Each returns a compact summary and file paths, except the judgment-task pages (up to 15 tasks or 36,000 characters of task text) and the journey observations (up to 3,000 characters of page text), which carry page text by design:

| Tool | Purpose |
| --- | --- |
| `audit_shop` | Start the audit in the background and return its `run_id` |
| `wait_run` | Wait up to 110 s for a run; call again while `timed_out` is true |
| `get_run` | Run summary: pages, not assessable stages with reasons, judgment progress and escalations, warnings, the TypeSafe requests the run made (`model_calls`), `server` (which keys it holds) |
| `run_journey` | Open a journey. `policy` `auto` (default): Jev pilots by itself when the server has `TYPESAFE_API_KEY` (then `wait_run`, then `journey_finish`), else you get the first observation and drive it with `journey_act`; `host` and `typesafe` force one |
| `journey_act` | One operation and one offered element index on the latest observation; `observation_id` is required and a stale one executes nothing |
| `journey_finish` | Verify with the oracle, store the friction KPIs, close the browser; returns what deciding cost (`model_calls`, `timing_ms`) |
| `judge_with_jev` | Jev judges the rubrics routed to it, one request per page, then finalises; `available` is false without a key. A call asks no new page after about 30 s: call it again while its `next` names it |
| `get_judgment_tasks` | A page of at most 15 judgment tasks with their rubrics; the tasks still open are the perception rubrics' and Jev's escalations |
| `submit_judgments` | Validate and store one judge's verdicts |
| `finalize_judgments` | Majority label per task |
| `score_run` | Compute the scores, write `report.json` and `report.html`, merge finished journeys |
| `get_report` | The summary, the KPI table or the file paths of a written report |
| `list_runs` | Recent runs with their ERS and coverage grade (`grade`) |

### Where the results go

```text
<artifacts>/<host>/<run id>/
  run.json           pages, observations, judgments, scores, warnings, model_calls (the full record)
  steps.jsonl        every executed step: an execution line before its result is observed, then a
                     measurement line with the same step number
  snapshots/         one audit payload per page
  shots/             optional screenshots
  report.json        the report as data
  report.html        self-contained Italian report: no external resources
```

### Who judges what

Seven KPIs are LLM judgments over short page snippets with closed labels. Each rubric in `rubrics/*.json` (`rubrics.v2`) names its judge:

| Rubric (KPI) | Judge | Why |
| --- | --- | --- |
| `returns_clarity` (`TRI.RETURNS_CLARITY`) | Jev | A time window plus one concrete condition: literal wording |
| `hidden_subscription` (`DPR.HIDDEN_SUBSCRIPTION`) | Jev | Recurring-charge wording against a CTA that presents the purchase as free or single |
| `authority` (`MPI.AUTHORITY`) | Jev | A named body, prize or certification number against a generic claim |
| `social_proof` (`MPI.SOCIAL_PROOF_RICH`) | Jev | Specific text plus one authenticity element (author, date, verified purchase, photo) |
| `trick_questions` (`DPR.TRICK_QUESTIONS`) | Jev | Double negations and inverted logic are properties of the text; the probability floor catches the rest |
| `confirmshaming` (`DPR.CONFIRMSHAMING`) | Claude | Guilt, shame or irony: tone |
| `value_prop` (`CCL.VALUE_PROP_CLARITY`) | Claude | Whether a first-time visitor understands at once what is sold and why to choose it: perceived clarity |

**Jev's judge** (`judges.judge_with_jev`, the `judge_with_jev` MCP tool, `--judge jev`). Tasks are grouped by page and a page costs one `systemone` request, built like `model.choose` (state plus choice questions, `post_json`, `validate_choice`). For every task it carries a label head (the rubric's closed labels and descriptions as the criteria), the same head with the labels in reversed order (Jev leans toward the first option) and, for each label that needs a quote, a speculative evidence head ("Assume the answer is `clear`: which snippet states it literally?") whose options are the offered snippet ids plus `none`. Code reads the label head, then the reversed head, and only the evidence head of the chosen label. A verdict is accepted when the chosen label's probability is at least 0.6 (`JEV_JUDGE_MIN_P`, clamped to 0.5-0.95), the reversed head agrees and, for a label that needs a quote, the evidence head picked a snippet and not `none`. The stored quote is that snippet's whole text, so it is verbatim by construction; Jev never writes text.

Anything else is **escalated** to Claude: no verdict is stored, `task["escalation"]` records Jev's reading (`low_probability`, `position_flip`, `no_evidence`) or the failure (`request_failed`, `invalid_response`, which a later call retries), and the task keeps its three required samples. An accepted task is settled with one sample (agreement 1.0, confidence = the label's probability), so the scoring is unchanged. TypeSafe documents repeated identical calls as largely identical, so three Jev samples would add little; their cookbooks also show borderline items flipping, and whether answers hold still on your pages is what `--repeat` in the smoke checks. A Jev failure never blocks the audit: the page's tasks stay open for Claude.

**Claude's judges** take the tasks of the `claude` rubrics and Jev's escalations, three samples each, and a label needs at least two of the three to agree. Claude Code does not support MCP sampling, so the plugin inverts the call: the server prepares the tasks, **the host judges** with Sonnet subagents on your Claude subscription, and the server validates and scores the verdicts. No API key is involved. Outside the plugin, `--judge cli` runs `claude -p` with your subscription login, and `--judge api` (`ANTHROPIC_API_KEY`) and `--judge openai` (`TEXT_MODEL_API_KEY`, `TEXT_MODEL_BASE_URL`) are the fallbacks that use keys. Every evidence quote, Jev's or Claude's, must appear verbatim in the snippet it cites, except for the labels a rubric exempts (`absent`, and `unclear` in the three dark-pattern rubrics; the value-proposition rubric exempts none).

**Order.** Jev judges only tasks no other judge has touched (a mix of samples could tie), so run it first: `judge_with_jev` in the plugin, `--judge jev` and then `jev-engage judge RUN_ID --backend cli` and `jev-engage score RUN_ID` outside it. Until the open tasks are judged the score does not count them. Verdicts are cached under `$JEV_ENGAGEMENT_CACHE` (default `~/.cache/jev-engagement/judgments`); Jev's cache is read only when `TYPESAFE_MODEL` pins a version (for example `jev-1.13.0`), because an alias such as `jev-latest` moves on release.

**In the report.** Jev's verdicts count in `llm_share` like Claude's. The footer names who judged what (Jev's rubrics and model, Claude's rubrics and the tasks Jev passed on) and how many TypeSafe requests the run made. Three samples of one model are a stability check, not three independent raters; a Jev verdict is one sample plus an order check, not a second opinion.

**What is not known yet.** The rubrics' criteria are Italian and are sent as written, while TypeSafe says non-English text works "not equally well". How well Jev reads them, how often it escalates and how stable its labels are is for the live smoke below to show; nothing is claimed here.

### Who pilots a journey

`run_journey` takes `policy`: `auto` (the MCP default), `typesafe` or `host`. `auto` resolves to `typesafe` when the server holds `TYPESAFE_API_KEY` and to `host` otherwise; the journey record keeps both (`policy_requested`, `policy`). From the CLI a journey is always `typesafe`.

- **Jev (`typesafe`).** The original agent loop, unchanged: one TypeSafe request per decision carries the operation head and the target heads, and only the chosen operation's target is consumed. The engagement code wraps it for measurement only (marks, `steps.jsonl`, guard filtering, the oracle). In the plugin it runs in the background: `wait_run` until it finished, then `journey_finish`. The step budget is `max_steps` (default 40, at most 60).
- **Claude Code (`host`).** The host chooses each step with `journey_act`, following the `journey-driver` rules. Every step is an MCP round trip plus Claude's deliberation, and the choices are not reproducible. If Jev cannot take a single decision (for example a refused key), the skill starts one host journey instead; nothing was executed, so no browser action is repeated.
- **Text.** Values for `TYPE_TEXT` come from the text LLM (`TEXT_MODEL_API_KEY`). Without it nothing is typed and no text is guessed: `TYPE_TEXT` stays offered, and if Jev chooses it the attempt is refused before any input (a logged step with `text_withheld`, warning `text_helper_unavailable`), and fill actions are withheld from Jev from then on. If the oracle then fails, the journey is "non valutabile" (`text_helper_unavailable`): the pilot's chosen path was denied, so the failure is not the shop's. A text the checkout guard refuses is treated the same way (`text_refused`).
- **What deciding costs** is recorded apart from the shop's time: `journey.model_calls` (`choose` decisions, `text` helper calls, `stale_or_refused` decisions that executed nothing, `failed` TypeSafe calls without a decision, the versioned `model`), `journey.timing_ms` (`decision`, `text`, `site`, `wall`) and `journey.usage` (input and output tokens). `FAI.TIME_ON_TASK_SITE` never includes the decision time. The report's journey card shows the pilot and these figures.

### The crawler fallback

The crawl is deterministic first: the lexicon, JSON-LD and URL patterns find the category, product, cart and checkout paths, so a repeated audit repeats. Where they find nothing (no listing, product or cart link, or no add-to-cart, variant or checkout control) and `TYPESAFE_API_KEY` is set, Jev is asked once:

- the way the loop asks it: one `model.choose` request with a fixed generic goal (`crawler.GOALS`, for example "Open the shopping cart page ... Do not add or remove items"; no page text, no site value) over the observed click elements the checkout guard allows and that have an accessible name;
- only a `CLICK` target is consumed. `DONE` and `BLOCKED` mean "no element" (`jev_done`, `jev_blocked`), and the stage fails with the reason it would have had without the fallback;
- a link is loaded like a lexicon candidate (same site, no checkout URL, no cart-changing GET); an element without a usable href is clicked once, never retried, and logged before its result is observed. The stage's own test still decides: `is_listing`, `is_product`, `is_cart`, a checkout page;
- one request per lookup and at most six per profile (listing, product, cart, add to cart, variant, checkout entry); nothing is asked when `max_pages` leaves no room for the page it would lead to. Consent, overlays, the search probe, repeats, the guest path and the deception tests are never asked.

Every lookup is recorded: `probes.jev_fallback` on the page it was asked on (chosen label, probability, latency, tokens, reason), `run.model_calls.crawler_fallback`, and a warning "found through the Jev fallback: not reproducible across runs", which the report translates. The observation is viewport-only (`snapshot.js` offers elements whose centre is inside the viewport, at most 250), so a link below the fold is invisible to Jev: the lookup ends as `jev_blocked` (no suitable element) or in a pick the stage's own test rejects, and the stage stays "non valutabile". Link lookups ask at the top of their start page.

### Live smoke (Jev)

Automated tests are offline: they replace `jev_ultrafast.model.post_json` (or the agent's `choose`) with fakes that validate the request body and answer with schema-valid choices, so the real request builders and `validate_choice` run. The one script that calls TypeSafe for real is started by you; it prints what it measured and writes `smoke_summary.json`, and you decide what to claim. This README publishes no Jev result: none is committed yet.

```bash
cp .env.example .env     # TYPESAFE_API_KEY (and TEXT_MODEL_API_KEY for text fields); .env is git-ignored
uv run python scripts/smoke_engagement.py                    # fixture shop: crawler fallback, judge, journey, score
uv run python scripts/smoke_engagement.py --url https://shop.example --goal "Aggiungi al carrello un prodotto" --oracle cart_not_empty
uv run python scripts/smoke_engagement.py --skip journey     # --skip audit|judge|journey|score, repeatable
uv run python scripts/smoke_engagement.py --repeat 3 --dump-requests /tmp/jev-requests
```

It reads `./.env`, the file named by `JEV_ENGAGEMENT_ENV` or the environment, and exits 2 without `TYPESAFE_API_KEY`. It needs a Chromium like the audit does. What it exercises:

1. **Audit (crawler fallback).** Fixture mode serves `tests/fixtures/` on 127.0.0.1 and hides the shop's category links from the lexicon, so the listing page can only come from Jev's pick; Chromium gets a dead proxy, so only the model requests leave the machine. With `--url` a real shop is audited unchanged (and one item goes into its cart, as in any audit).
2. **Judge.** Jev judges the audit: per task the rubric, label, probability, evidence id or escalation reason; per request the latency and input tokens; per rubric the mean probability, the number accepted and the escalations by reason. `--repeat N` judges N more copies (cache off, nothing stored) and prints the label agreement per task.
3. **Journey.** Jev pilots `--goal` (default "Aggiungi al carrello un prodotto", oracle `cart_not_empty`): steps, `model_calls`, `timing_ms`, usage and the oracle's verdict.
4. **Score.** The ERS, the "Confidenza" line, `llm_share` and the report path.

Every TypeSafe and text-helper request goes through a recording wrapper of `post_json`, so the printed counts, latencies and tokens are measured independently and compared with what the runs record. It then prints the checks and exits 0 when all required ones pass, 1 otherwise: the audit finished, the crawler, judge and journey request counts match the runs', every TypeSafe answer validated, the key was accepted (after an HTTP 401 or 403 the later TypeSafe phases are skipped), Jev piloted the journey and every Jev task was settled or escalated. In fixture mode it also requires the listing to come through the fallback, the cart to be reached, at least one Jev verdict accepted, the oracle to pass and no request but GET and HEAD to reach the shop (no order, no payment). `--url` mode relaxes those fixture-specific checks. Timings are printed, never asserted; it warns above 1.5 s or 20,000 input tokens per request. `--dump-requests DIR` writes each request with its answer (never the key), so the labels can be read against the page.

Limits of the smoke: it is paid and not part of the checks; fixture mode forces only the listing lookup, so the product, cart, add-to-cart and checkout lookups run only if the lexicon misses them; one fixture shop (invented, with known truths) shows how Jev reads those snippets, not how accurate it is in general; latencies include your machine's network; Jev's output has no seed, so a repeat that agrees once is evidence of stability on those tasks only.

### Safety

- It never places an order, never submits a checkout or payment form, and stops at the first checkout page.
- It never types into personal-data, payment or password fields. The only text an audit types is a word from a category label into the shop's search box. In a journey the host chooses the text (under Jev, the text LLM writes it): only search, quantity and coupon fields take it, and the server refuses text that looks like an email, a phone, card or account number, an IBAN, a fiscal code, a date, a street address or a card security code. A name cannot be told from a product word, so the journey-driver skill forbids it; the field guard is the guarantee, the text check a best-effort net.
- Labels that pay or confirm an order are never clicked (the one exception is a same-site link to a checkout URL, a navigation into the step where the run stops; a button with the same label stays refused), and nothing is done on another site.
- Adding one item to the cart is the one deliberate change to shop data: it puts an item into the session's own cart. The audit also clicks other observed controls that are not meant to change anything: a variant choice, the cart's guest control and checkout button, consent and overlay buttons. On some platforms a checkout button or a guest control posts and opens a checkout session on the shop's side, and a consent choice can set cookies. Use it on shops you own or may test.
- Page text and judge output are data: neither becomes a selector, a URL or code. A host chooses only an element index the latest observation offered; Jev chooses only an operation and an observed element index, a label and an offered snippet id. Jev reads untrusted page text and TypeSafe says it is not trained for adversarial content, so a hostile page can try to steer a label or a click; what it cannot do is make Jev write text, or reach a control the checkout guard withholds. The probability floor, the order check and the stages' own tests narrow the rest; they do not remove it.

### Limits

- Bot detection and CAPTCHAs: a challenge page makes the later stages "non valutabile". The browser identity is an ordinary user agent with matching client hints; nothing is evaded.
- Shadow DOM and iframes: `audit.js` reads open shadow roots, but the controls the crawler clicks and the journeys offer come from `snapshot.js`, which does not enter shadow roots. Listing and product pages (and the cart, once the add-to-cart has been clicked, when only its link sits in a shadow root) are still loaded by their URL, but a step that must click a control in a shadow root, such as the add-to-cart, ends the funnel there with a reason (for example `add_to_cart_failed` or `checkout_cta_not_found`) and the later stages are "non valutabile". Cross-origin iframes (review and payment widgets) and closed shadow roots are only partly read.
- Consent banners change the first page and can block clicks; the landing is measured as it is and the choice is recorded.
- It is a synthetic agent: a host's choices vary between runs (so may Jev's), one variant of any A/B test is seen from one place with a cold cache on an emulated device, and lab timings are not field data. The mobile profile approximates Lighthouse's preset and its scores are not comparable with Lighthouse or PageSpeed Insights.
- Italian and English lexicons; first checkout step only; one audit at a time per server process.
- Jev reads text only (no image input), and its accuracy on the rubrics' Italian criteria is untested until the live smoke runs. Request size matters: TypeSafe limits a request's state plus its longest question to 32k tokens, and a judged page carries up to eight 600-character snippets per task.
- A stage found through the crawler fallback is flagged "not reproducible across runs", and Jev's journey choices have no seed either: repeat a journey from the CLI to get medians. Only the deterministic crawl repeats by construction. The fallback sees the viewport only.
- With `host` (no TypeSafe key) every journey step is an MCP round trip plus Claude's deliberation; that time is recorded apart (`decision_latency_ms`) and never counted as the shop's.
- A shop behind HTTP authentication cannot be audited: a start URL with `user:password@` is refused, since the credentials would reach the run, its progress messages and the shared report.
- No accuracy, speed or predictive-validity claim is made without a committed artifact. The golden runs in `tests/fixtures/runs/` pin the data shapes and the scoring arithmetic, not a correlation with real outcomes. The catalogue lists what a calibration against GA4 and Clarity would need.

## Development

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

Tests are offline; the engagement browser tests launch a local Chromium against `tests/fixtures/shop/` and skip when none is found, and the Jev tests use stand-ins for `jev_ultrafast.model.post_json` (see [Live smoke](#live-smoke-jev)). `uv run python scripts/check_guards.py` checks real controls in a local browser without model calls. Live examples, `scripts/smoke_engagement.py` and the recording scripts make paid API calls. `scripts/record_flights.py <new-folder>` captures original browser timestamps; `scripts/render_demo.py <recording-folder>` renders that verified run at 1× and crops out the Google account strip. Credentials and raw traces stay ignored.

---

[Browser Use](https://github.com/browser-use/browser-use) · [Browser Harness](https://github.com/browser-use/browser-harness) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)
