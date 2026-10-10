# Jev redesign survey: pilot, judge and crawler fallback (read-only)

Root: `/home/user/jev-ultrafast-user-engagment`. All paths below are relative to it. I changed no files and ran no tests.

**Diagnosis in one line.** Jev still exists in the repo but is unreachable from the plugin. The only way to use it as pilot is the CLI or `policy="typesafe"`. It is not used as judge or crawler fallback at all. Everything else (judgments, crawler) runs deterministic code or Claude subagents.

---

## 1. Judgments pipeline

### 1a. Rubrics (`jev_ultrafast/engagement/rubrics/*.json`)

All 7 have `version: "rubrics.v1"`, `grouping: {"per":"page","max_snippets":8}`, and a question containing "carattere per carattere" (a test asserts this). Snippet kinds come from `schemas.py:25-39`.

| id | kpi_id | snippet_kinds (order = priority) | stages | labels | no_quote_labels | label to value | absent_when_no_snippets |
|---|---|---|---|---|---|---|---|
| authority | MPI.AUTHORITY | authority | null (all) | present, weak, absent | absent | identity strings | yes |
| confirmshaming | DPR.CONFIRMSHAMING | modal_decline, consent | null | present, absent, unclear | absent, unclear | present 0.8, absent 0.0, unclear null | no |
| hidden_subscription | DPR.HIDDEN_SUBSCRIPTION | subscription, fee_line, cta | pdp, cart, checkout_entry | present, absent, unclear | absent, unclear | present 0.8, absent 0.0, unclear null | no |
| returns_clarity | TRI.RETURNS_CLARITY | returns_policy | home, plp, pdp, cart, checkout_entry | clear, vague, absent | absent | identity strings | yes |
| social_proof | MPI.SOCIAL_PROOF_RICH | testimonial | home, plp, pdp, cart | rich, basic, absent | absent | identity strings | yes |
| trick_questions | DPR.TRICK_QUESTIONS | checkbox_label | null | present, absent, unclear | absent, unclear | present 0.8, absent 0.0, unclear null | no |
| value_prop | CCL.VALUE_PROP_CLARITY | headline, cta | home, plp, pdp | clear, partial, unclear | none (`[]`) | identity strings | no |

- Each rubric file has the keys `id, version, kpi_id, snippet_kinds, stages, grouping, question, labels, no_quote_labels, values` (plus `absent_when_no_snippets` on three).
- Confidence-valued DPR KPIs get `mapped * agreement` (`judgments.py:484`).
- The golden run `tests/fixtures/runs/audit_complete.json` has 16 tasks: hidden_subscription 3, returns_clarity 3, value_prop 3, confirmshaming 2, social_proof 2, trick_questions 2, authority 1. All have `samples_required: 3`. Verdicts are j1..j3, all with model `claude-sonnet-5-5`.
- Which rubrics suit Jev (my reading of the label text, not a repo fact):
  - Operationally defined: returns_clarity (time window plus concrete condition), hidden_subscription ("importo e frequenza"), authority (named body or number), social_proof (author, date, verified).
  - Borderline: trick_questions ("doppie negazioni", "logica invertita").
  - Perception-type, so Claude: confirmshaming ("colpa, vergogna o ironia") and value_prop ("capisce subito ... e perché sceglierlo").

### 1b. `judgments.py`

- Constants: `RUBRICS_VERSION="rubrics.v1"` at `:20`, `MAX_SNIPPET_CHARS=600` at `:21`, `MIN_QUOTE_CHARS=8` at `:24`, `MIN_AGREEMENT=2/3` at `:25`, `HOST_PROMPT="host"` at `:26`.
- Functions:
  - `load_rubrics(directory=None)` at `:29`.
  - `_snippets(page, rubric)` at `:59`: takes the rubric's kinds, in kind order, up to 8, deduplicated, and re-sorted into page order.
  - `make_tasks(run, *, samples_required=3, rubrics=None)` at `:89`: one task per rubric and page. Identical snippet sets share a task via `context.pages`. `task_id = "{rubric}:{page_id}"`.
  - `skipped_rubrics` at `:138`; `ensure_tasks(run, *, samples_required=3)` at `:155`; `prepared(run)` at `:168`.
  - `_check(verdict, task)` at `:190`. A verdict must have a label in `task["labels"]`, a confidence in 0..1, and evidence whose snippet ids exist. Each quote is checked by `verbatim()` (`:46`), must be at least 8 chars or the whole snippet, and is truncated to 600 chars. Evidence may be empty only for `no_quote_labels`.
  - `submit(run, judge_id, model, verdicts, *, cache=True, prompt=None)` at `:238`. It stops at `samples_required` verdicts per task (`samples_complete`), rejects a repeated `(task_id, judge_id)` (`duplicate_judge`), and rejects a task already final (`task_already_final`).
  - `required_samples(task, override)` at `:299`: the override can only lower the requirement. It is global across tasks, not per task.
  - `finalize(run, *, samples_required=None)` at `:312`. Majority label; a tie or agreement below 2/3 gives `label=None` (uncertain); finality is terminal. Stored fields: `{task_id, kpi_id, label, agreement, samples, confidence, models, evidence}`. A task with fewer verdicts than required stays pending.
  - `absence_label` at `:398` and `_absence_rows` at `:415`: deterministic, code-owned rows for the three `absent_when_no_snippets` rubrics.
  - `observations_from_final(run)` at `:447`: every row has `source:"judged"`, and `evidence.models` comes from `final.models`.
- Cache:
  - `cache_dir` at `:514` (`$JEV_ENGAGEMENT_CACHE`); `judge_view` at `:519` (question, labels, no_quote_labels, snippet kind and text, page_type/stage/locale); `cache_key(view, model, prompt)` at `:533`; `cache_put` at `:551`; `cached_verdicts` at `:581`; `apply_cache` at `:608`.
  - The namespace is `prompt or "host"`; judges use `prompt_id(judge)` = `f"{backend}:{PROMPT_VERSION}"`.

### 1c. `judges.py`

- `Judge` Protocol at `:105-110`: attributes `judge_id`, `model`, `backend` (the cache namespace) and `judge(tasks: list[dict]) -> list[dict]`. It returns raw verdict dicts `{task_id, judge_id, model, label, confidence, evidence, rationale}`; `submit()` does the validation.
- Support code:
  - `INSTRUCTIONS` at `:33` (Italian) and `PROMPT_VERSION = sha256(INSTRUCTIONS)[:16]` at `:53`.
  - `verdict_schema(tasks)` at `:87` (task_id and label enums narrowed to the batch); `prompt_id` at `:113`; `task_payload` at `:123`; `parse_verdicts` at `:159`.
- Backends:
  - `ClaudeCliJudge` at `:181`, `backend="cli"`. It runs `claude -p` with no tools, `--safe-mode`, an empty cwd, and API-key env removed so the subscription login is used.
  - `AnthropicApiJudge` at `:269`, `backend="api"`.
  - `OpenAICompatibleJudge` at `:318`, `backend="openai"`.
  - `SamplingJudge` at `:352`, which raises `NotSupported`.
- `run_judges(run, judges, *, samples_required=None, batch_size=8, use_cache=True)` at `:365`. Per judge it applies the cache, skips tasks already answered, final or full, judges the rest in chunks, `submit`s, then `finalize`s.

### 1d. Who runs judgments today

- **Plugin (MCP):**
  - The server only prepares and validates: `get_judgment_tasks` (`mcp_server.py:233`, `service.py:860`), `submit_judgments` (`:253` / `service.py:921`), `finalize_judgments` (`:266` / `service.py:941`).
  - **`judge_with` is not exposed through MCP.** The `TOOLS` set in tests is exactly these 12 tools.
  - `skills/shop-readiness/SKILL.md:85-114` (step 3) has Claude launch 3 `jev-engagement:engagement-judge` subagents per page of tasks (`model: sonnet`, ids j1..j3). It then runs `submit_judgments` per judge, a top-up round of j4, j5... for open tasks, and `finalize_judgments`.
  - `agents/engagement-judge.md`: `model: sonnet`, `tools: get_judgment_tasks, ToolSearch`, `maxTurns: 6`. It returns verdict JSON.
  - Evidence is currently generated text copied by Claude.
- **CLI:**
  - `service.judge_with(run_id, backend, samples, *, model, batch_size)` at `service.py:955-997`, with `BACKENDS=("cli","api","openai")` at `:52` and the class map at `:961`.
  - It builds `samples` judges j1..jN of one backend, calls `ensure_tasks(samples_required=samples)`, runs `run_judges` on a snapshot, then merges the new verdicts back and finalizes.
  - CLI entry points: `audit --judge none|cli|api|openai` (`cli.py:147`, called at `:338-340`) and the `judge` subcommand (`:157-161`, `_judge` at `:393`).
- **Scoring and report consumption:**
  - `scoring.run_observations` (`scoring.py:493`) swaps in `observations_from_final(run)` once judgments are `prepared`.
  - `llm_share` is the weight of KPIs whose chosen row has `source=="judged"`, over assessed weight (`_source` at `scoring.py:263`, `_sub_index` at `:329-346`, `ers` at `:409-436`). It does not depend on the model, so Jev verdicts count as LLM share automatically.
  - `report.py:596-599` lists judge model names as `sorted({models in final})`.
  - The footer (`report.py:928-952`) hard-states "campioni dello stesso modello con lo stesso prompt" and "Modelli dei valutatori: ...". This is wrong once Jev and Claude are mixed.
  - `service._score_summary` and `get_report` expose `llm_share` (`service.py:1051`, `:1058`, `:1111`). `cli._print_score` prints it (`cli.py:270`).

### 1e. What a redesign touches (judging)

- A `JevJudge` fits the `Judge` Protocol with `backend="jev"` and its own prompt-version hash.
  - It needs a `systemone` request with one `choice` question per task over the labels, and a second `choice` over snippet ids for the evidence. The quote is then the verbatim snippet text, so `_check` passes by construction (it only needs `len >= min(8, len(snippet))`).
  - `confidence` and `probabilities` come directly from `validate_choice`.
  - The evidence question is skipped for `no_quote_labels`.
- Gaps in the current machinery:
  - `samples_required` is per run at creation (`make_tasks`) and `finalize` can only lower it globally. Jev (deterministic, so 1 sample) plus Claude for low-confidence or perception rubrics needs a per-rubric or per-task `samples_required` and an escalation path. `required_samples` (`:299`) and `submit`'s cap (`:264`) have to support that.
  - A Jev verdict that is later re-judged by Claude conflicts with `task_already_final` and with the global-override semantics.
  - Rubric JSON needs a routing field (for example `judge: "jev"|"host"`).
  - Bumping `RUBRICS_VERSION` (`judgments.py:20`) has this blast radius:
    - all 7 rubric JSONs;
    - `tests/fixtures/runs/audit_complete.json` (17 occurrences: 16 tasks plus the state);
    - `journey_run.json`;
    - `tests/test_engagement_report.py:73,94`.
  - Any new key copied into task dicts breaks `test_fixture_judgments_are_reproducible` (`tests/test_engagement_judgments.py:389`: `make_tasks(run) == state["tasks"]`) unless the golden file is regenerated.
- The MCP side needs a new tool (for example `judge_with_jev(run_id)`) for the plugin to reach a server-side judge.
  - `get_judgment_tasks` already marks `final` and `samples_submitted`, and the agent skips full or final tasks, so the Claude path naturally receives only the remaining tasks.
  - `TOOLS` in `tests/test_engagement_mcp.py:41`, the `allowed-tools` list in SKILL.md, and the exact set equality at `:170-178` all change.
- The report footer text and the `models` list must be conditional.

---

## 2. Journey pipeline

- **Policies and defaults today**
  - `journey.py:87`: `POLICIES=("host","typesafe")`. `JourneyRunner.start(..., policy="host", ...)` is at `:377`; policy validation is at `:389` and the key check at `:400-401` ("use policy 'host' inside Claude Code").
  - The chooser is `self.host or (lambda page, goal, history: agent_loop.choose(page, goal, history))` at `:417-421`, with `text_policy=self.host.text if self.host else None`, so the Agent falls back to `field_text`.
  - `service.run_journey(..., policy="host", ...)` at `service.py:591-677`.
    - host: returns `{run_id, status, observation}` and registers `_Live` (`:632-637`).
    - typesafe: spawns a thread running `run_auto()` then `finish()` (`:638-677`) and returns `{run_id, status:"running", policy, next:"wait_run"}`.
    - After the thread ends, `journey_finish(run_id)` returns the stored summary (`:730-733`), but `journey_act` on a typesafe run raises `_not_open` (`:679-688`).
  - MCP `run_journey` at `mcp_server.py:166-198`: `policy: Literal["host","typesafe"] = "host"` at `:178-180`.
  - CLI: `--policy choices=("typesafe",)` at `cli.py:125-126`, and `_journey_options` (`:222-230`) requires `TYPESAFE_API_KEY`, raising `NO_TYPESAFE` (`:25`). `["audit", SHOP, "--policy", "host"]` must exit 2 (`tests/test_engagement_cli.py:60`).
- **`HostPolicy`** (`journey.py:222-299`)
  - It has the `choose()` contract: `choice, operation, target, confidence=1.0, probabilities{choice:1.0}, operation_probabilities, target_probabilities, target_confidence, raw_answers{}, model="host", usage{}, latency_ms, request{}`.
  - API: `offer(page, observation_id)` at `:243`, `set(operation, target, text, observation_id)` at `:249`, `__call__` at `:274`, `text()` at `:296`.
  - `resolve(page, operation, target)` at `:193` validates against `model.action_space`.
- **What the plugin skill does today**
  - SKILL.md step 2 (`:57-83`): Claude picks an oracle, calls `run_journey(url, goal, oracle, oracle_params, profile="mobile")`, and drives `journey_act` step by step following `skills/journey-driver/SKILL.md`. It then calls `journey_finish`.
  - Every step is therefore an MCP round trip plus Claude deliberation, which is the speed loss the user noticed.
  - `journey-driver` is a pure host-policy rulebook. The `policy` argument is never passed.
- **Timing fields**
  - Step record `decision_latency_ms` (`schemas.py:284`) is set at `journey.py:1062`, `:1232`, `:1247` via `_decision_latency()` (`:1207`). It equals the decision's `latency_ms` plus the summed `latency_ms` of text-helper calls in that attempt. NAVIGATION records get `None` (`:1154`).
  - For host: `latency_ms` runs from first delivery of the observation to `set()`. For typesafe: the wall time of `choose()` (`model.py:118,146`).
  - `friction.py:319` sums it as `decision_ms_excluded` and keeps it out of `FAI.TIME_ON_TASK_SITE`. The report states the exclusion (`report.py:~820`).
- **Model-call counting: there is none persisted.**
  - In memory only: `agent.state["decisions"]` (one per TypeSafe request, appended at `agent.py:88`), `agent.state["text_calls"]` (`agent.py:125`), and `history[i]["usage"]` (`:147`).
  - Nothing is written to `run.json`, `steps.jsonl`, or the service summary. `journey_record` has only `started_at` and `finished_at`.
  - AGENTS.md demands consistent model-call counts, so a redesign should persist `model_calls: {choose, text, judge, crawler}` and a timing breakdown (decision, text, site, wall), including for stale or guard-refused decisions, which still cost a request.
- **Key constraints for the Jev default**
  - Typesafe TYPE_TEXT needs `TEXT_MODEL_API_KEY`. A missing key raises ValueError (`model.py:163`), which the runner maps to status `error` and the oracle to `journey_error` (not assessable) (`journey.py:705-712`, `:607-608`).
  - The Agent's no-progress stop (3 unchanged actions) ends a typesafe run as `blocked` (`:687-689`). Under host it only inserts a note.
  - `MAX_STALE=10` (`:97`) applies to typesafe.
  - Nothing reads `.env` in the MCP server (`mcp_server.main` at `:339` does not call `cli.load_environment`).
  - `.claude-plugin/plugin.json` env passes only `BH_TAB_MARKER`, `UV_PROJECT_ENVIRONMENT` and `JEV_ENGAGEMENT_ARTIFACTS`. A test pins this dict exactly (`test_engagement_mcp.py:157`).
  - Whether Claude Code forwards `TYPESAFE_API_KEY` to a plugin's stdio server is unverified. If it does not, the key must be added to the env block (the test comment says `${VAR:-default}` does not work) or loaded from a file.
- **Where the default should flip**
  - `service.run_journey` and the MCP schema, for example `policy="auto"`: typesafe if `TYPESAFE_API_KEY` is set, else host. Persist the resolved value.
  - Do not flip `JourneyRunner.start`'s default. About 20 runner tests rely on it being `host`; one asserts `policy=="host"` on steps (`test_engagement_journey.py:595`).
  - SKILL.md step 2 must handle the typesafe result (poll `wait_run`, then `journey_finish`) and the `allowed-tools` list.
  - The CLI is already Jev-only.

---

## 3. Crawler (`engagement/crawler.py`)

**Stage discovery today** (deterministic; module doc `:1-63`):

- **home**: `Crawl.home()` at `:918` does `visit("home", settings.url)`, closes overlays, applies the consent policy, and runs the search probe.
- **plp**: `Crawl.listing()` at `:944-964` takes `listing_candidates` (`:278`, `audit.nav.categories` plus SiteNavigationElement JSON-LD, ranked by `category_url` pattern and visibility; utility, cart, checkout, product and info links excluded). It tries up to `CANDIDATES=3` (`:79`) via `visit("plp", url)` (a GET through `load_page`). It accepts a result when `is_listing` (`:212`) and it is not the home page. Otherwise `mark("plp", "not_found")` at `:963` and it returns None. This failure is soft: products then come from home.
- **pdp**: `Crawl.product()` at `:966-992` uses `product_candidates` (`:326`, `products.cards[].href` plus ItemList JSON-LD, `clean_cart_url` filtered) from the listing then home, with the same 3-candidate limit. It accepts when `is_product` (`:222`) and `unavailable()` is None. **If none qualifies: `raise Stop("pdp", last_error, then="pdp_not_found")` at `:992`**, a hard stop that makes all later stages not assessable.
- **cart**: `Crawl.cart()` at `:994-1009`.
  - `add_to_cart()` (`:1020-1057`) clicks the PDP's audit.js control through `Tab.click(labels=[control.label], key="add_to_cart", rect=...)`.
  - If the click navigated, `after_click("cart")` is used. Otherwise `cart_url()` (`:1081-1106`) picks audit.js `nav.cart_link` or an observed link matching the `cart` lexicon, and `visit("cart", url)` loads it; `is_cart` (`:230`) must hold.
  - Failure points, all `Stop`:
    - `add_to_cart_failed` (`:1047` no control present; `:1055` click not executed);
    - `variant_required` (`:1070`, `:1077`);
    - `Stop("cart","not_found",then="cart_not_found")` at `:1002` (no cart URL) and `:1008` (not a cart);
    - `out_of_stock`, `consent_blocking`.
- **checkout_entry**: `Crawl.checkout()` at `:1108-1147`.
  - `guest_path()` (`:1154`) runs only when `forms.guest_option` is true.
  - Otherwise it clicks the checkout CTA via `Tab.click(labels=[checkout_cta.label], key="checkout")`. `checkout_cta_not_found` is raised at `:1139` when the reason is `control_not_found`. `no_navigation` is at `:1146`.
- `Stop` is defined at `:757`; `Crawl.stop` (`:786`) marks the stage and every later stage as NotAssessable; `mark` at `:782`.
- `Tab.act()` (`:498-534`) runs `guard.allowed_action`, never re-sends a control, and logs a step with a `purpose` before observing. `Tab.log` (`:479`) writes the `source` field and labels the operation `TYPE_TEXT` if the kind is fill, else `CLICK` (so a scroll would be mislabelled).

**Cleanest hook for a Jev fallback**

1. **Primary hook: `Tab.click()` at `:536-570`.**
   - The miss point is `action = self.pick(...)` returning None, then `return {**result, "reason": "control_not_found"}` at `:562-563`.
   - Add an optional `fallback` callable (or a `Tab.chooser`) there.
   - It receives `(page, allowed_click_actions, purpose, stage)` and returns one of the offered actions or None. `act()` then runs as usual, so `Tab.act`'s guard, never-resend, and log-before-observe rules all apply.
   - This covers add-to-cart, select_variant, checkout CTA and guest control with one change.
   - Offer only `guard.filter_actions(page, page_type)[0]` restricted to `kind=="click"`. `JourneyRunner._observe` (`journey.py:866-882`) filters the same way.
   - `Browser.observe()` offers only elements whose centre is inside the viewport, at most 250 (`snapshot.js:60`, `:100-101`). `Tab.scroll_to(rect)` (`:434`) needs a rect that audit.js may not have in a failure case. A bounded SCROLL_DOWN step (a scroll action is already in `actions`) or an observe after scrolling may be needed.
2. **Link-discovery stages (plp, pdp, cart URL):**
   - These load URLs, not clicks.
   - Jev should pick an observed element index; code resolves its href with `_href(page, action)` (`:132`) and then `visit(stage, url)` as for any candidate. The existing `same_site`, `is_checkout` and `clean_cart_url` guards (`:814-816`, `:1104`) still apply.
   - Insert at three points:
     - `listing()` just before `self.mark("plp", ...)` (`:963`);
     - `product()` before the `raise Stop` (`:992`);
     - `cart_url()` / `cart()` when `url is None` (`:1001`), where Jev picks the cart link.
   - Acceptance stays deterministic: `is_listing`, `is_product`, `is_cart`. Jev only proposes, and a wrong pick becomes an "extra" page via `demote`.
3. **Injection and gating.**
   - Pass a chooser into `Crawl` / `discover_funnel` (`:765`, `:1192`), or add a setting. `EngagementSettings` (`settings.py:25-38`) has a validated `TYPES` table to extend.
   - Default off in tests. If the chooser is enabled by the mere presence of `TYPESAFE_API_KEY`, existing crawler tests that assert exact `Stop` reasons will call the paid API on a developer machine (see section 5).
   - Each Jev choice should be recorded in `PageRecord.probes` (for example `probes["jev_fallback"] = {stage, reason, choice_label, model, latency_ms}`) and in `steps.jsonl` with a distinct purpose.

---

## 4. `model.py` helpers (reusable for new TypeSafe questions)

- **`post_json(url, key, body)`**, `model.py:15-27`
  - Module-level `CLIENT = httpx.Client(http2=True, timeout=25)` (`:12`).
  - Up to 3 attempts on 429, 529 or 503 with 0.5·2^n backoff (retrying a model request is fine; retrying a browser mutation is not).
  - A transport error or HTTP error raises `RuntimeError("Model ... no action executed.")`.
- **`validate_choice(answer, ids)`**, `:30-45`
  - Requires `choice in ids`, `set(probabilities)==set(ids)`, every number a finite float in 0..1, `abs(sum-1)<0.02`, and `probabilities[choice]` being the maximum.
  - Otherwise `ValueError("Invalid TypeSafe response; no action executed.")`.
  - `ids` may be a dict or set.
- **`action_space(actions)`**, `:48-78`
  - Returns `(elements, targets{OP:{index:action}}, controls{ID:action})`. One index per DOM node. SELECT options are `"3:2"`. Scroll, wait and other non-click kinds go to `controls`.
  - This is the "indices, not selectors" contract the crawler fallback can reuse.
- **`choose(state, goal, history)`**, `:81-148`
  - One request to `https://api.typesafe.ai/v1/systemone`, key `TYPESAFE_API_KEY`, model `$TYPESAFE_MODEL` (default `jev-latest`).
  - `questions = {"operation": {"type":"choice","criteria":{op: description-string}, "instructions":{"goal","rules":NEXT_ACTION}}, "<op>_target": {"type":"choice","criteria":{index:{element,current_value,role,...}}, "instructions":{"goal","operation","rules":[NEXT_ACTION,TARGET]}}}`.
  - The response answers are read as `result["answers"][name]` = `{choice, confidence, probabilities}`, plus `result["model"]` and `result["usage"]`.
  - Only the head matching the chosen operation is validated and consumed.
  - It also returns `latency_ms` and the whole `request` body.
- **Rules text** is in `questions.py`: `NEXT_ACTION` at `:3` (goal-advancing, mentions DONE and WAIT), `TARGET` at `:16`, `TEXT_VALUE` at `:21`, `MAX_STEPS=60` at `:26`.
- **Reuse for new questions**
  - A generic "ask" helper built on `post_json` and `validate_choice` would serve both the crawler fallback (one `click_target`-style head over guard-filtered observed elements, with page-stage rules instead of `NEXT_ACTION`) and the judge (label head plus snippet-id head per task, many tasks per request).
  - Calling `choose()` directly for the crawler is possible but wrong-shaped: it exposes DONE/BLOCKED/WAIT and goal-advancing rules.
  - Request-size and per-request question limits are not stated anywhere in the repo. Confirm them against the TypeSafe docs before batching 8 tasks (16 heads) into one request.
- **How tests stub it**
  - Low level: `monkeypatch.setattr(model, "post_json", post)` with a `post(_url, _key, body)` that returns `{"model": "test", "answers": {...}}` built with `choice(ids, selected)` (`tests/test_agent.py:73-139`). It also requires `monkeypatch.setenv("TYPESAFE_API_KEY", "test")`.
  - Agent level: `monkeypatch.setattr(loop, "choose", fake)`, where `loop` is `jev_ultrafast.agent`. The fake takes `(state, goal, history)` and returns the `choose` contract. Examples are `stand_in(pattern, calls, operation)` at `tests/test_engagement_journey.py:823` and `fake_choose` at `:788`.
  - Text helper: `monkeypatch.setattr(loop, "field_text", helper)`.
  - Because `journey.py:419` resolves `agent_loop.choose` at call time, patching `agent.choose` works for runner tests. A new TypeSafe helper must also be resolved at call time, or go through `model.post_json`, to stay patchable.

---

## 5. Tests that would need to change

**Cross-cutting (do first):**

- `tests/conftest.py` has no guard against `TYPESAFE_API_KEY`. Only `test_engagement_cli.py` has an autouse `delenv` (`:25-30`).
- Several tests run host journeys or real subprocess servers with `env={**os.environ, ...}`:
  - `test_engagement_mcp.py:825-897` (in-process host journey);
  - `:994` (the stop-sequence test calls `run_journey` without policy against a real subprocess server);
  - `:465-546`, `:571`, `:625`, `:666` (FakeRunner, default policy).
- With "auto" resolution, a developer who has the key (the user will) would silently turn these into paid typesafe runs. Add an autouse fixture that deletes `TYPESAFE_API_KEY` and `TEXT_MODEL_API_KEY` in `conftest.py`, and scrub them from the subprocess `env` dicts.

**Jev default pilot:**

| File and test | Why it changes |
|---|---|
| `test_engagement_mcp.py:388-415` (`test_errors_reach_the_host_as_messages`) | asserts `policy:"typesafe"` without the key raises "TYPESAFE_API_KEY" (`:404-405`, `:417`). A default "auto" falls back to host, so keep the explicit-typesafe case and add the auto cases. |
| `test_engagement_mcp.py:119-146` (tool listing) | `run_journey` schema and description (policy enum) if "auto" is added. |
| `test_engagement_mcp.py:148-178` (plugin test) | exact `server["env"]` dict (`:157`), SKILL `allowed-tools` equals `TOOLS` (`:170-178`) when env keys or tools change. |
| `test_engagement_mcp.py:460-590` (`FakeRunner`, whose `start(**kwargs)` swallows policy) | needs a case asserting the resolved policy. |
| `test_engagement_mcp.py:825`, `:961-1044` | key scrubbing, as above. |
| `test_engagement_cli.py:25-30`, `:43-52`, `:60`, `:82-88` | CLI parser and "needs typesafe" messages. They stay valid only if the CLI keeps `--policy choices=("typesafe",)`. |
| `test_engagement_journey.py:441-460` | start-validation, including `policy:"random"` and the typesafe-needs-key case. Stays valid if the runner default and validation are unchanged. |
| `test_engagement_journey.py:595` | asserts `policy=="host"` on steps from a runner started without a policy. Stays valid only if the runner default is unchanged. |
| `tests/fixtures/runs/journey_run.json:37`, `journey_steps.jsonl` | golden `policy:"host"`. Only change if a persisted `model_calls` or timing field is added to the journey record. |

Also add Jev-pilot tests using `loop.choose` stand-ins (`journey.py:419` already supports this), including the TYPE_TEXT-without-`TEXT_MODEL_API_KEY` path that ends in `journey_error`.

**Jev judge backend:**

| File and test | Why it changes |
|---|---|
| `test_engagement_judgments.py:66-99` (`test_rubrics_cover_every_judged_kpi`) | pins `version == RUBRICS_VERSION` for all rubrics, `grouping`, label/value sets, `len(rubrics)==7`. Adding a routing key is fine; a version bump touches all rubrics. |
| `test_engagement_judgments.py:389` (golden reproducibility) | any new field copied into tasks, or a changed `samples_required`, breaks `make_tasks(run)==state["tasks"]`. Regenerate `audit_complete.json`. |
| `test_engagement_judgments.py:424-466` (`test_cache_key_covers_the_whole_judge_input`) | `prompt_id` namespaces for `cli` and `api` (`:430-437`) are asserted; add `jev:{PROMPT_VERSION}`. |
| `test_engagement_judgments.py:242-264` and `:344` (finalize, `samples_required` cap, sticky finality) | change if per-rubric or per-task samples or escalation is introduced. |
| `test_engagement_judgments.py:583-735` | a new Jev judge test with a `post_json` or `choose`-level stub, not `fake_claude`. |
| `test_engagement_cli.py:120-170` | `--judge` and `--backend` choices if `jev` is added. |
| `test_engagement_mcp.py:41`, `:119`, `:195-272`, `:1065-1119` | `TOOLS` if a Jev-judge tool is added, and the judge_with merge tests. |
| `test_engagement_report.py:114` | asserts `"campioni dello stesso modello"` and `"Modelli dei valutatori: claude-sonnet-5-5"`, which change if the footer becomes conditional. |
| `test_engagement_scoring.py:572`, `:709` | `llm_share` expectations. They should be unaffected unless the definition changes. |

**Crawler fallback:**

- `tests/test_engagement_audit.py:45` (`PURPOSES` set) and `:105`, `:110` assert every step purpose is in a closed set, and `source` is `{"crawler","deception"}`. A new Jev purpose must be added.
- Existing failure-path tests pin exact `Stop` reasons and must stay off the fallback by default:
  - `:379` (`test_a_funnel_that_cannot_start_marks_every_stage`);
  - `:1186`, `:1206`, `:1250` (`test_the_cart_stage_needs_a_cart`), `:1278`;
  - `:1344`, `:1360`, `:1377`;
  - `:1708`, `:1725`.
- Reusable test scaffolding for the new tests (no browser needed):
  - `ScriptedShop`, `ScriptedBrowser` and `ScriptedCollector` (`:~420-475`), with the `scripted` fixture and `monkeypatch.setattr(Tab, "await_effect", ...)`;
  - `FakeCollector`, `CartShop`, `CartLinkShop` and `LoginCollector` for the cart cases;
  - `Crawl(collector, EngagementSettings(...), profile=..., guard=GUARD)` with `crawl.tab.browser = collector.browser`.
- A Jev fallback test would inject a stub chooser that returns an offered action, one that returns None, and one that returns an index not offered, and assert the choice is guard-checked and logged before observing.

**Docs and README claims to keep consistent (per AGENTS.md):**

- `README.md`:
  - `:11`, `:49` (one TypeSafe request, "Two decisions, one network round trip");
  - `:152` (journey by host or typesafe);
  - `:192`, `:215` (the skill "drives a shopping journey with Claude Code as the policy, has three Sonnet judges");
  - `:226` (`run_journey` host or typesafe);
  - `:248-252` ("Who judges": "the host judges", "A label needs at least two of three samples").
- `docs/engagement-design.md`:
  - `:12`, `:76`, `:86-88`, `:93`;
  - `:129-145` (section 5, including the "Three samples of the same model ... report says so" claim);
  - `:205-215` ("Policy `host` (default in Claude Code)").
- `docs/engagement-kpi.md` is checked by `tests/test_engagement_docs.py` (ids, anchors, units, stages, severities).
- README states "Tests are offline"; the live smoke is a separate script (see below).

**Existing live-smoke scaffolding.** `scripts/smoke.py` is a single-agent smoke (travel fixture, `Agent` with TypeSafe plus text model). It runs on `http://127.0.0.1:8766/fixture.html`, not the engagement fixture shop. `scripts/check_guards.py` uses no model. The requested smoke command for Jev pilot, judge and crawler fallback does not exist yet.

---

## Risks and open facts to resolve before coding

1. **Key delivery to the plugin server is unverified.** `plugin.json` env passes no `TYPESAFE_API_KEY`, `TEXT_MODEL_API_KEY` or `TEXT_MODEL_BASE_URL`, and the MCP server never loads `.env`. A plugin-side "auto" resolves to `host` forever if the key is not forwarded.
2. **TYPE_TEXT cost and failure.** Every Jev TYPE_TEXT decision adds a text-LLM call and fails the journey when `TEXT_MODEL_API_KEY` is missing (error, then `journey_error`). Search goals are the common case.
3. **Per-request limits unknown.** Nothing in the repo states how many `questions` or how large a `systemone` body is allowed. `choose()` sends at most 1 + 3 heads.
4. **No persisted model-call counts or timing breakdown.** The user's speed claim cannot be demonstrated from stored runs, and README and AGENTS.md require consistent counts.
5. **Mixed judges break the report footer and the "same model" claim**, as noted in section 1e.
6. **`Tab.log` labels non-fill actions as `CLICK`**, so a Jev-chosen scroll in the crawler would be recorded as a click unless `log` is extended.

## Files read

- `jev_ultrafast/engagement/`:
  - rubrics, `judgments.py`, `judges.py`;
  - `service.py`, `mcp_server.py`, `cli.py`;
  - `journey.py`, `crawler.py`, `safety.py`;
  - `audit.py`, `settings.py`, `scoring.py`, `report.py`, `schemas.py`.
- `jev_ultrafast/`: `model.py`, `questions.py`, `agent.py`, `browser.py`, `snapshot.js`.
- `.claude-plugin/plugin.json`, `skills/shop-readiness/SKILL.md`, `skills/journey-driver/SKILL.md`, `agents/engagement-judge.md`.
- `tests/conftest.py` and the judgments, mcp, cli, audit, journey and agent test files.
- `README.md`, `docs/engagement-design.md`, `scripts/smoke.py`.