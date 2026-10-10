# Map of `jev-ultrafast-user-engagment` (read-only; nothing modified)

The repo is a tiny fork: 3 commits on upstream `browser-use/jev-ultrafast`, about 1,700 lines of Python plus one 107-line JS file. Current branch is `claude/nice-mccarthy-yf5hk1`, with `main` and `origin` at `adapt-to-it/jev-ultrafast-user-engagment`. I could not run anything here: `browser_harness` is not installed, there is no `.venv` and no Chrome. Python 3.13, `uv` and node 22 are present. I could not read browser-harness source, so its API is described only from how this repo uses it.

## 1. README.md and AGENTS.md

**Purpose.** A browser agent with a dynamic, indexed action space. One natural-language goal goes in. Each cycle reads the page into a numbered element table, and TypeSafe's "Jev" model picks an operation plus a target element in one network round trip. A small second LLM writes text only for `TYPE_TEXT`. The headline claim is Zürich to London on Google Flights in 7.1 s.

**Claims** (README.md:118-126, `docs/performance.md`):
- 7,073 ms recorded run.
- Six alternating runs, median 9.450 s to 7.092 s (25% faster); CDP calls 1,092 to 101.
- Wikipedia task in 2.798 s; local hotel fixture in 1.896 s.
- Explicitly "not a general reliability benchmark".

**Limits stated in the README** (these matter for e-commerce): shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling and arbitrary keyboard widgets are unsupported. Owned tabs share the user's existing Chrome profile.

**How to run:**
- Server and web UI: `uv sync; cp .env.example .env; uv run jev`, then open `http://127.0.0.1:8766`. The `jev` script is `jev_ultrafast.demo:main`.
- Chrome attaches through Browser Harness. Run `uv run browser-harness --doctor` and allow remote debugging in Chrome.
- Library: `from jev_ultrafast import Agent; with Agent(url, goal) as a: for state in a.run(): ...` (README.md:73-82).
- Script: `uv run --env-file .env python examples/run.py --url URL --goal '...'` (`--goal` is repeatable).
- Flights example: `examples/flights.py [--output DIR] [--keep-open]`.

**Env vars** (`.env.example`; used in `model.py` and `demo.py`):

| Variable | Use | Default |
|---|---|---|
| `TYPESAFE_API_KEY` | Required. Read as `os.environ[...]` at `model.py:119`, so it raises `KeyError` if missing | none |
| `TYPESAFE_MODEL` | Decision model | `jev-latest` (`model.py:108`) |
| `TEXT_MODEL_API_KEY` | Required only for `TYPE_TEXT` | none |
| `TEXT_MODEL_BASE_URL` | OpenAI-compatible endpoint | code default `https://api.deepseek.com/v1` (`model.py:164`); `.env.example` sets OpenRouter |
| `TEXT_MODEL` | Text model | code default `deepseek-chat`; `.env.example` sets `inception/mercury-2.5` |
| `TEXT_MODEL_REASONING` | `none` gives `{"reasoning":{"enabled":False}}` (`model.py:166-168`) | unset |
| `TYPESAFE_DEMO_PORT` | Inspector port | `8766` (`demo.py:16`) |

`.env` is parsed only by `demo.load_environment()` (`demo.py:23-29`, `os.environ.setdefault`, no quote stripping). It is called from `demo.main` and `scripts/smoke.py`. Library and examples need `--env-file`.

**Providers:**
- TypeSafe: `POST https://api.typesafe.ai/v1/systemone` (hardcoded, `model.py:119`), Bearer auth.
- Text helper: any OpenAI-compatible `/chat/completions` endpoint. README names OpenRouter with `inception/mercury-2.5`; Gemini, GLM and DeepSeek also work.
- There is no Anthropic or OpenAI SDK. HTTP goes through `httpx.Client(http2=True, timeout=25)` (`model.py:12`) with 3 attempts on 429/503/529.

**Build and test** (README.md:130-136, AGENTS.md:16):

```
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
uv run python scripts/check_guards.py   # real local Chrome, no model calls
```

**AGENTS.md rules that constrain the repurpose:**
- Keep the loop small: page, indexed elements, operation + target, execution.
- No site-specific plans or hardcoded field values.
- Never let the model emit selectors or code.
- Never retry a browser mutation.
- Log execution before observing its result.
- Tests must not call paid APIs.
- Verify outcomes independently; `DONE` is not proof.
- Keep README claims, examples, raw evidence and model-call counts consistent.
- Do not commit or push unless the user asks.

## 2. Layout

```
jev_ultrafast/
  __init__.py     exports Agent, Browser (6 lines)
  agent.py        Agent class: state dict + command("tick|predict|act") loop (174)
  browser.py      CDP session via browser_harness, observe/fresh/act, StalePage (194)
  snapshot.js     in-page IIFE: indexed controls, visible text, freshness guards (107)
  model.py        TypeSafe request builder/validator, action_space, text helper (198)
  questions.py    prompts NEXT_ACTION / TARGET / TEXT_VALUE, MAX_STEPS=60 (26)
  demo.py         loopback HTTP server + JSON API for the inspector (145)
  static/         index.html, app.js (245), style.css (737), fixture.html (local "Forma" site)
examples/         run.py (generic CLI), flights.py (Google Flights task + verify())
scripts/          smoke.py, check_guards.py, measure_flights.py, record_flights.py,
                  render_demo.py, render_fixture.py
tests/            test_agent.py (320 lines, only test file)
docs/             design.md, performance.md, performance-prepared.md, launch-draft.md,
                  measurement JSONs, demo.mp4/gif, images
```

There is no `evidence/` directory. `artifacts/` is gitignored and written at runtime. There is no packaged logging or metrics module.

Script roles:
- `smoke.py`: paid live smoke test against the local fixture.
- `check_guards.py`: real-Chrome freshness and execution regressions, no LLM.
- `measure_flights.py`: monkeypatches `browser_module.cdp` to time every CDP method.
- `record_flights.py`: CDP screencast capture.
- `render_*.py`: ffmpeg/PIL video rendering, with macOS font paths hardcoded.

## 3. Browser layer

**Library.** Not Playwright. It uses `browser_harness` 0.1.13 (`from browser_harness.admin import ensure_daemon`, `from browser_harness.helpers import cdp`, `browser.py:9-10`). browser-harness depends on `cdp-use`, `fetch-use`, `pillow` and `websockets` (uv.lock:19-31). It attaches to the user's already-running Chrome over remote debugging through a daemon.

**Page load** (`browser.py:20-33`):

```python
ensure_daemon()
self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
self.call("Emulation.setFocusEmulationEnabled", enabled=True)
self.call("Page.navigate", url=url)
# poll document.readyState == "complete" every 20 ms, up to 15 s; on timeout it just proceeds silently
```

Observations:
- The viewport is hardcoded desktop, 1120x780.
- There is no `Page.enable`, `Network.enable` or lifecycle events.
- Load timing is not recorded anywhere.
- The tab is in the background, which may affect paint-based metrics; this is worth verifying before trusting LCP/FCP.

**Observe** (`browser.py:44-86` and `browser_operation`, `browser.py:188-194`):
- One `Runtime.evaluate(READ_STATE)` per snapshot.
- Up to 10 retries with 20 ms sleeps on `StalePage`.
- Before the read, `Browser.after_input` triggers a 2-frame / 50 ms wait, or up to 200 ms for combobox autocomplete.
- `fingerprint()` is a sha256 of `url, text, actions, scroll` (`browser.py:115-117`).
- A JPEG screenshot (quality 72, base64) is added only if `screenshot=True`. It is off by default in the library and on in the inspector.
- No accessibility tree is used any more. Commit 452c1ad replaced `Accessibility.getFullAXTree` with the direct DOM read. The `AX tree` string left in `index.html` is stale text.

**Element structure** (`snapshot.js:61-81`). Each entry in `page["actions"]`:

```js
base = {node: identity(e), role: rname, label: name(e) || rname, rect:{x,y,w,h}}   // viewport-relative rect
// optional: checked / selected / expanded (from aria-*, or e.checked for checkbox/radio)
// kind: 'click' | 'fill' | 'select'; value (string);
// for <select>: one action per non-selected option: {..., kind:'select', value:o.value, current_value, label:'<name> → <option label>'}
// editable fields additionally emit {..., kind:'click', label:'Open <name>'}
// afterwards id='e1'.. is assigned; control entries are appended:
//   {id:'scroll_down'|'scroll_up', kind:'scroll', label, delta:±560} and {id:'wait', kind:'wait', label}
```

Page-level return (`snapshot.js:105-106`):

```js
{url, title, w: innerWidth, h: innerHeight, text, scroll:{y, height}, actions, marker, page_key, guards, omitted_actions}
```

The selector matches `a[href], button, input, textarea, select, summary, [contenteditable=true]` plus `[role=button|link|checkbox|radio|switch|tab|menuitem|menuitemradio|option|gridcell|combobox|textbox|searchbox|spinbutton]`.

What is and is not captured:

| Item | Status |
|---|---|
| Role, label | Captured |
| Label resolution | Handles `aria-labelledby`, `aria-label`, `<label>`, `alt`, child text, `title`, `placeholder`; not the full accname algorithm |
| Bounding box | Captured as `rect`, viewport-relative |
| Visibility | Filtered, not reported. `checkVisibility({checkOpacity, checkVisibilityCSS})` and not under `aria-hidden` / `inert` (`snapshot.js:10-11`) |
| Enabled | Filtered, not reported. Disabled and `aria-disabled` elements are dropped (`:57`) |
| In-viewport | Only elements whose centre is inside the viewport are kept (`:59`); below-the-fold elements never appear |
| Cap | 250 elements, with the overflow count in `omitted_actions` (`:99-100`) |
| Excluded input types | `password`, `file`, `hidden` |
| Not captured | tag name, `href`, id/class, images, headings, prices, non-interactive elements, computed styles, z-index/overlays, shadow DOM, iframes |
| Visible text | TreeWalker over visible in-viewport text nodes, joined by `\n`, capped at 6,000 characters (`:82-92`), with no per-text geometry |

Freshness helpers:
- `window.__jevFast` caches node identity: a `WeakMap` of ids and a `Map` of live nodes.
- `cache.pageKey()` and `cache.guard(e)` support `Browser.fresh()` (`browser.py:88-98`).
- `fresh(page)` for a non-click re-runs the whole `snapshot.js` as `MARKER`, which is costly.

**What the model sees** (`model.action_space`, `model.py:48-78`): `rect` and `node` are dropped. It builds:

```
element = {role, value, checked?, selected?, expanded?, index:"1".., label (before " → "), operations:[...], options:[...] for select}
```

## 4. Decision layer

**Flow** (`model.choose`, `model.py:81-148`): `action_space()` yields elements, `targets` (per-operation dicts keyed by index) and `controls`. One request body is posted (`model.py:107-117`):

```python
body = {"model": TYPESAFE_MODEL or "jev-latest",
        "state": {"page": {url, title, text},                       # text = visible viewport text
                  "elements": [...],
                  "recent_actions": [last 10 of {action, kind, text, page_changed}]},
        "questions": {"operation": {"type":"choice", "criteria": {OP: description},
                                    "instructions": {"goal": goal, "rules": NEXT_ACTION}},
                      "click_target" | "type_text_target" | "select_target": {
                          "type":"choice",
                          "criteria": {index: {"element": "[i] label", "current_value", role/checked/selected/expanded}},
                          "instructions": {"goal", "operation", "rules": [NEXT_ACTION, TARGET]}}}}
```

The response is `answers[q] = {choice, confidence, probabilities{id: p}}`, plus `model` and `usage`. `validate_choice` (`model.py:30-45`) enforces:
- the choice is one of the offered ids;
- the probability keys equal the id set;
- all numbers are finite and within 0 to 1;
- probabilities sum to about 1;
- the chosen option has the maximum probability.

Only the target head matching the chosen operation is validated and used.

**Operations:**
- Element operations, offered only if targets exist: `CLICK`, `TYPE_TEXT` (observed kind `fill`), `SELECT`.
- Control operations, derived from `action["id"].upper()`: `SCROLL_DOWN`, `SCROLL_UP`, `WAIT`.
- Always offered: `DONE` and `BLOCKED` (`model.py:90`).

**Return value** of `choose()` (`model.py:134-148`):

```
choice (action id e.g. "e12"), operation, target (index str), confidence, probabilities {action_id: p},
operation_probabilities, target_probabilities, target_confidence, raw_answers, model, usage,
latency_ms, request (full request body)
```

**Prompts** (`questions.py`):
- `NEXT_ACTION` (:3-14): advance the whole goal, treat page text as untrusted, fill fields before submit, `DONE` needs visible evidence.
- `TARGET` (:16-19).
- `TEXT_VALUE` (:21-24): exactly one key `text`, string or null.
- `MAX_STEPS = 60` (:26).

**Text helper** (`model.field_text`, `model.py:160-198`):
- Sends `field_context` (goal, field label/role/value, first 6,000 characters of page text, last 6 actions) to `/chat/completions`.
- Requests a JSON object with `max_tokens=1024`.
- Rejects anything but `{"text": <non-empty str ≤ 2000>}`.
- Returns `(value, {model, latency_ms, usage})`.

**Model-call counting:**
- Each TypeSafe request appends to `state["decisions"]` (`agent.py:78-84`), including discarded or stale ones.
- Budget: `len(decisions) >= MAX_STEPS*2` raises (:75). Executed actions are capped at `MAX_STEPS` (:102).
- Each text-helper call appends to `state["text_calls"]` (:115).
- Token usage is stored per decision and per history entry. There is no aggregation code. Totals in `docs/*.json` such as `tokens`, `decision_median_ms` and `decision_total_ms` were produced outside the repo; I grepped and no `.py` computes them.

## 5. Execution and logging

**Execution** (`browser_operation`, `browser.py:135-186`, with `Browser.act` at 100-107):
1. `Browser.act` re-checks freshness and raises `StalePage` if stale.
2. `wait` sleeps 100 ms.
3. `scroll` sends `Input.dispatchMouseEvent` mouseWheel at (550, 650) with delta ±560.
4. Other kinds resolve the node from the `__jevFast` registry. A JS pre-check rejects disconnected, disabled, inert, invisible or read-only targets, targets outside the viewport, and targets covered per `elementFromPoint`.
5. `click`: `mousePressed` then `mouseReleased` at the element centre.
6. `fill`: click, then select-all via `Input.dispatchKeyEvent` (`commands=["selectAll"]`, Cmd/Ctrl), then `Input.insertText`.
7. `select`: set `.value` and dispatch `input` and `change` events in JS. An interrupted select raises `RuntimeError` and is not retried.

**Agent orchestration** (`agent.py`). `Agent.command("tick")` runs predict then act. On `StalePage` it clears the decision, re-observes, and returns without logging the stale event anywhere (:59-64). `Agent.run()` is a generator that yields `snapshot()` per tick (:163-165). The loop stops on `done` or `blocked`. `blocked` also fires after 3 consecutive non-wait actions with `page_changed is False` (:153-158).

**In-memory state** (`agent.py:27-41`):

```python
dict(browser, goal, page, decision, history=[], status="ready", plan=[task], plan_index=0,
     decisions=[], text_calls=[], elapsed_ms=0, started_at=None, record=bool(record_dir))
```

**Per-executed-action history record** (`agent.py:121-140`):

```python
{"step", "action"(label), "kind", "choice"(action id), "probability", "confidence", "latency_ms",
 "text", "text_helper"(model), "text_latency_ms", "operation", "target",
 "page_changed"(None then set after observe), "url", "usage",
 "executed_ms", "elapsed_ms"}          # relative ms since first predict
```

After the post-action observe (:144-148) it updates `page_changed`, `url` and `elapsed_ms`.

**Other records:**
- `state["decisions"][i]`: the full `choose()` dict plus `fingerprint` and `elapsed_ms`. It is large because it carries `request` and `raw_answers`.
- `state["text_calls"][i]`: `{model, latency_ms, usage, field, value}`.

**What is not logged:**
- Wall-clock timestamps. Only `perf_counter`-relative ms; `started_at` is a raw `perf_counter` float.
- Stale or re-observe events.
- Observe or act durations.
- Network, console or navigation timing.
- `DONE` / `BLOCKED` as history rows. They appear only in `decisions`, and `plan_index = int(selected == "DONE")`.

**Where artifacts go.** There is no run directory or JSONL writer. The only persistence is:
- `record_dir`: `000000.jpg` plus `{elapsed_ms:06d}.jpg` per action (`agent.py:42-44`, `149-152`). The inspector enables it with `record` and writes to `artifacts/frames` (`demo.py:60`).
- Scripts dump `json.dumps(agent.snapshot(), indent=2)`:
  - `examples/flights.py:54-59` writes `state.json` (with `verification`) and `session.json` under `artifacts/flights/latest`.
  - `scripts/smoke.py:23-53` writes `artifacts/dynamic/fixture/<UTC ts>/state.json` and `summary.json`.
  - `measure_flights.py` adds `cdp` per-method counts and ms, `source_hashes`, `task_hash`, `configuration`, `browser_version` and `final_page`.
- UI "Export trace" downloads `typesafe-browser-trace.json` (state minus the screenshot, `app.js:217-235`).

## 6. Existing timing and metrics

**Timing**
- `agent.py`: a `time.perf_counter()` clock starts at the first predict (:68-69). It excludes initial navigation and the first observation (:23).
- `model.py:118,169`: per-request `latency_ms` for TypeSafe and for the text helper.
- `browser.py:29-33`: the 15 s readyState poll, unrecorded.
- `snapshot.js:44,97` reads `performance.timeOrigin`, but only as part of the freshness key. No `performance.getEntries`, `PerformanceObserver`, `Performance.getMetrics`, `Network.*` or tracing exists anywhere.
- `scripts/measure_flights.py:30-41` wraps `browser_module.cdp` to record per-method call counts and time. This is the only instrumentation precedent.
- `scripts/record_flights.py:11,39-45` uses `drain_events()` to consume CDP events (`Page.screencastFrame`) with `event["method"]`, `event["session_id"]`, `event["params"]`. This is the existing pattern for subscribing to CDP events.

**Reporting.** Only hand-assembled JSON and markdown in `docs/` (`measurement.json`, `flights-measurement.json`, `full-speed-measurement.json`, `flights-prepared-measurement.json`, `performance*.md`). The summary fields (`decision_median_ms`, `decision_total_ms`, `tokens`, and so on) have no generating code in the repo.

**Independent verification** (`examples/flights.py:18-38`): `verify(page) -> {"passed": bool, "checks": {...}, "visible_flights": [...]}`. This is the template for outcome checks.

## 7. Web server and UI

**Server** (`demo.py`). It is stdlib `ThreadingHTTPServer` on `127.0.0.1:8766` with one global `AGENT` and a non-blocking global `LOCK`.

Endpoints:
- GET: `/`, `/app.js`, `/style.css`, `/fixture.html?scenario=travel|research`, `/demo.mp4`, `/api/state`. Static files get `__TOKEN__` substituted (`demo.py:81-102`).
- POST (`demo.py:104-125`):
  - `/api/reset` `{scenario: travel|research|flights, goal, record?}` creates an Agent. `flights` opens Google Flights; the others open the fixture. It always passes `screenshots=True`.
  - `/api/predict`.
  - `/api/act` `{fingerprint}`.
  - `/api/tick`.

Security and error handling:
- Checks `Host == 127.0.0.1:PORT`, `X-Demo-Token`, and an `Origin` allow-list.
- Body must be under 8,192 bytes, and a second concurrent request gets 409.
- `ValueError`, `RuntimeError` and `TimeoutError` return 400. Anything else returns 500.

There is no streaming (no SSE or WebSocket). Every response is the full `response_state()` (`demo.py:32-34`), including the base64 screenshot. The client drives the loop itself: Run automatically calls `/api/tick` repeatedly, or predict, a 450 ms pause, then act when Slow motion is on (`app.js:169-187`).

**UI** (`static/index.html`, `app.js`):
- Task form with a scenario selector.
- Live screenshot with numbered target overlays.
- Next-action panel: decision time, target confidence, operation, operation-probability chips.
- Element table with probability bars.
- Plan list.
- "Decision trail" with step, action, generated text, `latency_ms`, probability and "Page changed / No change observed" (`app.js:127-135`).
- "What the model sees" JSON, which is `d.request` (the last TypeSafe request).
- Export trace button.

## 8. Tests

**Framework.** `pytest>=8.4,<9`, `testpaths=["tests"]`, no `conftest.py`, no fixture files. There are 28 collected cases, which I derived by counting parametrisations (6 + 4 + 4 + 2 for the parametrised tests, plus 14 single tests).

**Mocking.** `monkeypatch.setattr(model, "post_json", ...)` replaces the HTTP layer. `monkeypatch.setattr(browser, "cdp", Mock(...))` replaces CDP. `Mock` browsers are used for the agent. Importing the package still imports `browser_harness`, so it must be installed, but Chrome is not needed.

**Helpers.**
- `page()` (:15): a synthetic page state with `fingerprint()`.
- `choice(ids, selected)` (:32) and `decision(action)` (:36).
- `runner` fixture (:160-178): `Agent.__new__(Agent)` with a Mock browser (`fresh`, `observe`) and a pre-seeded `state`.

**Test groups:**
- Choice validation: `test_invalid_choice_is_rejected`, parametrised over 6 mutations.
- Action space: one index per node, with per-operation heads.
- One-request behaviour: all heads in one request, only the matching head executes, click cannot consume a text target.
- Text helper: reuse only for identical context, rejects invalid values, missing credential stops.
- Staleness: a stale decision is consumed before any mutation; observation is one atomic read.
- Dropdown interruption: an interrupted select cannot be retried.
- Flight verification: `test_flight_verification_rejects_wrong_trip` imports `examples.flights.verify`, which works only because the editable install puts the repo root on the path. This is my inference.

**Typical test** (`tests/test_agent.py:99-114`):

```python
def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {"model": "test", "answers": {
            "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
            "type_text_target": choice(["1"], "1"),
            "click_target": choice(["1", "2", "999"], "999")}}
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])
```

**Gaps.** There are no tests for `demo.py`, `snapshot.js` (covered only by the real-Chrome `scripts/check_guards.py` plus `node --check`), or the examples' CLIs.

## 9. pyproject.toml

```toml
[project] name="jev-ultrafast" version="0.1.0" requires-python=">=3.12"
dependencies = ["browser-harness==0.1.13", "httpx[http2]>=0.28,<1"]
[project.scripts] jev = "jev_ultrafast.demo:main"
[build-system] requires=["hatchling"]
[dependency-groups] dev = ["pytest>=8.4,<9", "ruff>=0.14,<1", "pillow>=11,<13"]
[tool.ruff] line-length = 120
[tool.ruff.lint] select = ["E", "F", "I"]
[tool.pytest.ini_options] testpaths = ["tests"]
```

The license is MIT and the lockfile is `uv.lock`. There is no mypy or pyright and no CI config. I did not run ruff. `.gitignore` covers `.env`, `.venv`, `artifacts/` and `dist/`.

## 10. Extension points

### (a) Performance capture (Web Vitals, navigation, network)

- **`Browser.__init__` (`browser.py:21-33`) is the main hook.** Before `Page.navigate`, enable what is missing: `Page.enable`, `Page.setLifecycleEventsEnabled`, `Network.enable`, `Performance.enable`.
- **Inject a metrics collector early.** Use `Page.addScriptToEvaluateOnNewDocument` to start `PerformanceObserver` for `largest-contentful-paint`, `layout-shift`, `paint`, `event`/INP, `longtask` and `resource`. Keep it in a sibling JS file loaded like `READ_STATE = Path(__file__).with_name(...).read_text()` (`browser.py:13`).
- **Record the load timeline.** Replace the silent 15 s `readyState` poll with a recorded navigation timeline (navigationStart, DOMContentLoaded, load) and an explicit timeout flag.
- **Collect CDP events** with `drain_events()`, as `scripts/record_flights.py:36-48` does, for `Network.requestWillBeSent` and `Network.loadingFinished`. A natural home is a new `Browser.metrics()` or `Browser.drain()` method.
- **Per-step collection.** `browser_operation`'s observe branch (`browser.py:188-194`) already does one `Runtime.evaluate` per snapshot. Piggy-backing a `performance` payload there would add no round trip. Otherwise add a separate `Browser.vitals()` called in `Agent.command` right after each `observe()` (`agent.py:62,71,142`).
- **Per-call timing.** `scripts/measure_flights.py:30-41` shows the pattern of wrapping `cdp`. That wrapper can be lifted into `Browser.call` to log per-step CDP durations.
- **Measurement hygiene.** Four things currently work against clean measurements:
  - the shared Chrome profile (warm cache and cookies);
  - the hardcoded 1120x780 non-mobile viewport (`browser.py:25`);
  - `background=True` plus `setFocusEmulationEnabled`, which may distort paint-based vitals;
  - the agent clock deliberately excludes the initial load (`agent.py:23`, `68-69`).

  Expect to add options for `Network.setCacheDisabled`, `Network.emulateNetworkConditions`, `Emulation.setCPUThrottlingRate`, a mobile profile, an isolated `Target.createBrowserContext`, and a foreground tab. `Browser(url)` has no kwargs yet, so give it a profile or config argument.

### (b) Per-page element audit (CTA, prices, trust signals, forms)

- **New `audit.js`** next to `snapshot.js`, evaluated by a new `Browser.audit()` using `self.evaluate(...)`. The existing snapshot is unsuitable on its own: it is viewport-only, capped at 250 elements, and has no tag, `href`, price or heading data. Do not reuse `page["actions"]`. The audit should walk the whole document (including below the fold) and output:
  - Geometry: bounding boxes in page coordinates plus a fold flag (`rect.y < innerHeight`).
  - CTA candidates: button and link text, size, contrast, position.
  - Prices: regex plus JSON-LD `Product`/`Offer`/`AggregateRating` and `itemprop`.
  - Trust and persuasion signals: reviews and ratings, badges, return policy, payment icons, scarcity and urgency text, social proof.
  - Forms: field counts, required flags, autocomplete attributes, labels.
  - Overlays and consent banners.
- **Reuse helpers** from `snapshot.js` such as `visible()`, `role()`, `name()`. They are IIFE-local, so either duplicate them or build one shared reader.
- **Standalone use already has precedent.** `Browser` is exported (`__init__.py`) and used without any model in `scripts/check_guards.py:18-21` (`Browser(url)`, `observe(screenshot=False)`, `evaluate(...)`). A pure audit or crawl mode needs no LLM and avoids the paid-API test restriction.
- **Agent-driven mode.** Call the audit once per distinct URL or `page_key` after `observe()` in `Agent.command` (`agent.py:142-148`, where `page_changed` is computed). Store it under a new `state["audits"]` or inside the `history` row, and dedupe by `page["url"]`.
- **LLM classification (optional).** `model.choose()` builds `questions` of `{"type":"choice","criteria":...,"instructions":...}`. A parallel `classify()` could reuse `post_json` and `validate_choice` to tag elements by persuasion factor with probabilities. Whether TypeSafe's `choice` question is suited to arbitrary classification is untested here.
- **Known blind spots for e-commerce:** cookie banners and payment widgets in iframes or shadow DOM are invisible; pop-up tabs are unsupported; `snapshot.js:10` drops anything under `aria-hidden` or `inert`.

### (c) Post-run KPI computation and reports

- **Data source.** `Agent.snapshot()` (`agent.py:46-50`) returns `history`, `decisions`, `text_calls`, `elapsed_ms`, `status` and `page`. A new `jev_ultrafast/kpi.py` (`summarize(state) -> dict`) and `report.py` (HTML/Markdown/JSON) would sit beside `agent.py`. They are not included in any current module. Add an entry under `[project.scripts]` if you want a CLI.
- **Derivable today** (nothing computes these now):
  - Decision latency median and total from `decisions[*].latency_ms`.
  - Action counts and wait counts from `history[*].kind`.
  - Time per step from consecutive `executed_ms`.
  - Time to goal from `elapsed_ms`.
  - Token and cost totals from `usage`.
  - Page-changed rate from `history[*].page_changed`.
  - A friction proxy from `status=="blocked"` and the 3-no-change rule.
  - UI clarity proxies from `confidence`, `target_confidence` and `target_probabilities` (entropy of the target distribution).
  - Element availability from `len(actions)` and `omitted_actions`.
- **Signals to add** (so they are logged, not just swallowed):
  - Stale or re-observe counts (`StalePage` is silently caught at `agent.py:59-64`).
  - `DONE` and `BLOCKED` as history rows.
  - Wall-clock ISO timestamps (the current clock is relative).
  - Per-step `observe_ms`, `act_ms` and `predict_ms`.
  - The audit and vitals payloads from (a) and (b).
- **Run store.** Follow the `artifacts/<scenario>/<UTC ts>/` convention from `scripts/smoke.py:23`. Append `steps.jsonl` from `Agent.command("act")` right after the history append (`agent.py:121`) and again after the observe update (`agent.py:144`), which satisfies "log execution before observing". Write `run.json` and `report.html` at the end.
- **Success and KPI checks.** Mirror `examples/flights.py:verify()` as a pluggable per-task predicate returning `{passed, checks}`; this keeps DONE from being treated as proof.
- **UI hooks:** a `GET /api/report` branch in `Handler.do_GET` (`demo.py:81-102`) plus a panel or Export button next to `app.js:217-235`. The server already returns full state on each call, so reports can be rendered client-side from `state`. The `scenario` allow-list (`demo.py:48`) and the hardcoded flights URL (`demo.py:55`) would need generalising for arbitrary shops.
- **Docs:** `docs/performance.md` plus the JSONs are the template for reporting format. AGENTS.md requires README claims and evidence to stay consistent if you change them.

### Cross-cutting constraints for planning

- **Prompts target task completion.** `NEXT_ACTION` (`questions.py:3-14`) is tuned to finish a goal such as a search. For measurement runs (browse, add to cart, checkout) you will probably want an alternate rules string or goal template. The AGENTS.md "no site-specific plans" rule still applies to the policy itself.
- **Budgets:** `MAX_STEPS=60` and 120 decisions are fixed in `questions.py` and `agent.py`.
- **Single global agent** in the demo server, serialised by a lock, so no parallel runs through the UI. Parallelism would have to go through the library or scripts.
- **Shared Chrome profile and the remote-debugging prerequisite** are unchanged by the above.