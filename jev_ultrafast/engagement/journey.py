"""Agent journeys: one natural-language goal on a shop, driven by the host (Claude Code) or by TypeSafe.

The loop is the Jev agent's: page -> indexed elements -> operation + target -> execution. JourneyRunner gives the
Agent a guarded view of one tab in an isolated browser context with the device profile and vitals.js installed:

- Every observation passes CheckoutGuard. Forbidden controls (pay, place order, anything on a checkout page but a
  link away, actions on another site) and text fields the guard refuses (password, payment, personal data,
  newsletter: only search, quantity and coupon fields remain) are removed before any policy sees the page, and the
  executor checks the guard again right before input, and refuses any input once the latest observation is a
  checkout page, an anti-bot page or another site. A page whose audit fails (its type unknown) is handled as a
  checkout page until a later observation reads it: only links away, scrolling and waiting are offered and executed
  there. No policy is asked on a page where the run stops: when the Agent observes again right before deciding and
  lands on one, the run stops there without a decision (no model request carries it). The host only picks an
  offered index and operation (HostPolicy): never a selector, never code, and only on the observation it was shown.
  Every observation carries an observation_id and act() names the one its choice was made on: a choice on any other
  (a repeated or parallel tool call) executes nothing, and an index must still name the same element of the same
  document. Text that looks like personal or payment data is never typed.
- A browser mutation is never retried. After each action the runner waits until the page settles (no main-frame
  load, no request in flight, 0.5 s without page-side activity; at least 1 s when nothing visible answered, the
  dead-click window: a request holds the settle but is no answer), reads what changed since the action (vitals.js
  since(mark)) and appends the step to steps.jsonl before the next observation is read. A stale decision (StalePage)
  executes nothing and consumes no step. An input that may have happened (an error after it was sent) is logged as
  uncertain and never sent again. Each step is one record, appended once it is measured: a kill of the process
  during the settle loses at most that last record (a journey cannot be resumed, so nothing could be sent twice).
- Visible feedback is a mutation, a navigation, a layout shift the input caused or a change of what an action can
  change (friction.effect); a request is not, a tracking beacon included: it is evidence only. vitals.js learns a
  self-updating node (a countdown, a carousel) from changes more than 2 s after the latest action, so with decisions
  faster than that a ticker's change can still read as a click's feedback: dead clicks are under-reported, never
  invented (a v1 limit of vitals.js).
- The run stops at the first checkout page (stopped_at_checkout_boundary), on DONE or BLOCKED, on the step budget
  and on the shop's own anti-bot page (blocked; no evasion, the outcome is not assessable). A navigation to another
  site ends the journey (blocked, warning left_shop), unless the landing page is a checkout (boundary), whatever
  that site shows; so does a page that failed to load (chrome-error://, warning navigation_error). The runner never
  navigates by itself. A tab the page opens is flagged and closed. Unexpected navigation: a new tab, or a new
  document or another site after a step that was not a chosen CLICK or SELECT, or a page that navigated by itself
  between steps (while the policy decided): that one is logged as a NAVIGATION record, which executed nothing.
- The Agent's no-progress stop (three actions in a row that changed nothing) ends a typesafe run (blocked: the only
  brake on a model looping on a dead control). The host owns DONE and BLOCKED: it is told in the next observation's
  guard notes and chooses again, within the step budget, so repeated dead clicks stay countable (FAI.RAGE_EVENTS).
- finish() verifies the outcome with an independent oracle (oracles.py) on the actual page state, never on DONE,
  stores the friction observations (friction.py) in the run and always releases the context, the transport and a
  Chromium it launched. A host ends a running journey as "done" or "blocked" only; the other statuses are the
  runner's. A journey that ended in an error (harness, credentials, model provider) without a passing oracle is not
  assessable: its failure is not the site's; nor is an outcome the oracle cannot read without a guess.

Time attributed to the site is page-side (vitals.js clock): execution plus settle per step. Decision latency (host or
model, from the first delivery of an observation to the choice) and text generation are recorded apart and never
counted. Page records: the landing page and the cart page an oracle read are stored in run["pages"] (stage "extra")
as evidence; journey steps are not page audits.
"""

import os
import re
import threading
import time
from urllib.parse import urlsplit

from .. import agent as agent_loop
from ..agent import Agent
from ..browser import StalePage, session_gone
from ..model import action_space
from ..questions import MAX_STEPS
from . import friction, oracles
from .collectors import AuditError, PageCollector
from .judgments import RUBRICS_VERSION
from .lexicon import lexicon_for
from .pagetypes import classify
from .profiles import DEVICE_PROFILES, PROFILES_VERSION, close_context, new_context
from .safety import CheckoutGuard
from .schemas import JourneyRecord, JourneyStep
from .scoring import load_anchors
from .settings import EngagementSettings
from .store import RunStore, iso_now
from .transport import open_transport

POLICIES = ("host", "typesafe")
OPERATIONS = ("CLICK", "TYPE_TEXT", "SELECT", "SCROLL_UP", "SCROLL_DOWN", "WAIT", "DONE", "BLOCKED")
TARGETED = ("CLICK", "TYPE_TEXT", "SELECT")
TERMINAL = ("done", "blocked", "stopped_at_checkout_boundary", "budget_exhausted", "error")
HOST_FINISH = ("done", "blocked")  # what a host may end a running journey with; the others are the runner's
KIND_OPERATION = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT", "wait": "WAIT"}
INDEX = re.compile(r"^[0-9]{1,4}(:[0-9]{1,4})?$")  # "7", or "3:2" for the second option of select element 3
TEXT_EXCERPT = 3000
MAX_TEXT = 200
MAX_NOTES = 25
MAX_STALE = 10  # consecutive stale decisions before an automatic run gives up on an unstable page
PAGE_KEY_JS = "(() => { const c = window.__jevFast; return {key: c ? c.pageKey() : null}; })()"
PERSONAL_TEXT = {
    "an email address": re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]{2,}"),
    "a card, phone or account number": re.compile(r"(?:\d[\s./-]?){9,}"),
    "an IBAN": re.compile(r"\b[a-z]{2}\d{2}(?:\s?[a-z0-9]){11,30}\b", re.I),
    "a fiscal code": re.compile(r"\b[a-z]{6}\d{2}[a-z]\d{2}[a-z]\d{3}[a-z]\b", re.I),
}
SETTLE_JS = """(() => { const v = window.__jevVitals;
  return {href: location.href, ready: document.readyState, quiet: v ? v.quiet(%d) : null,
          activity: v ? v.activity() : null}; })()"""
ACTIVITY_KEYS = ("load_end", "lcp_at", "resource_at", "mutation_at", "shift_at")
VISIBLE_KEYS = ("load_end", "mutation_at")  # page-side activity that answers an action (a request does not)
WEB = ("http", "https")
FINISH_JS = "[performance.timeOrigin, location.href]"
NO_PROGRESS = "no progress: three actions in a row changed nothing; choose another element or BLOCKED"
UNREAD = ("page type unknown (the page audit failed): handled as a checkout page, only links away, scrolling and "
          "waiting are offered")
DRAINED = ("Page.", "Network.", "Runtime.", "Log.")  # Target.* events stay for the collector's frame watcher
LONG_LIVED = ("EventSource", "WebSocket")


class GuardRefused(Exception):
    """CheckoutGuard refused an action right before input; nothing was executed."""


class _NotVerified(Exception):
    """The outcome cannot be verified (an anti-bot page): no oracle runs."""


class _Stopped(Exception):
    """The Agent observed a page where the run stops (checkout, anti-bot page, another site): no policy is asked."""


def _web(url: str | None) -> bool:
    try:
        return urlsplit(url or "").scheme in WEB
    except ValueError:
        return False


def _document(page: dict):
    """The document an observation shows (snapshot.js page_key[0], performance.timeOrigin), or None."""
    key = page.get("page_key")
    origin = key[0] if isinstance(key, list) and key else None
    return origin if isinstance(origin, (int, float)) and not isinstance(origin, bool) else None


def personal_text(text: str | None) -> str | None:
    """What the text looks like when it looks like personal or payment data, else None. An IBAN holds at least ten
    digits: a product code such as "XR12ABCDEF12345" is not one."""
    for what, pattern in PERSONAL_TEXT.items():
        for match in pattern.finditer(text or ""):
            if what != "an IBAN" or sum(c.isdigit() for c in match.group(0)) >= 10:
                return what
    return None


def check_text(text) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("TYPE_TEXT needs a non-empty text")
    if len(text) > MAX_TEXT:
        raise ValueError(f"TYPE_TEXT text is limited to {MAX_TEXT} characters")
    if what := personal_text(text):
        raise ValueError(f"Refused text: it looks like {what}; journeys never type personal or payment data "
                         "(search by a product word instead)")
    return text


def _index(target) -> str | None:
    if isinstance(target, int) and not isinstance(target, bool):
        return str(target) if target >= 1 else None
    if isinstance(target, str) and INDEX.match(target.strip()):
        return target.strip()
    return None


def resolve(page: dict, operation, target=None) -> tuple[str, str | None, dict | None]:
    """(operation, target index, observed action) for a choice on this page's action space (model.action_space).

    Only offered operations and indices are accepted: a selector, an action id, a coordinate or anything else raises
    ValueError. CLICK, TYPE_TEXT and SELECT need an index of their own target head ("3:2" picks the second option of
    select element 3); WAIT, SCROLL_UP, SCROLL_DOWN, DONE and BLOCKED take none.
    """
    _, targets, controls = action_space(page.get("actions") or [])
    op = operation.strip().upper() if isinstance(operation, str) else None
    if op not in OPERATIONS:
        raise ValueError(f"Unknown operation {operation!r}; use one of {', '.join(OPERATIONS)}")
    if op in TARGETED:
        if op not in targets:
            raise ValueError(f"{op} is not offered on this page")
        key = _index(target)
        if key is None or key not in targets[op]:
            offered = list(targets[op])
            shown = ", ".join(offered[:12]) + (", ..." if len(offered) > 12 else "")
            raise ValueError(f"{op} target {target!r} is not an offered element index for {op} (offered: {shown})")
        return op, key, targets[op][key]
    if target not in (None, ""):
        raise ValueError(f"{op} takes no target")
    if op in ("DONE", "BLOCKED"):
        return op, None, None
    if op not in controls:
        raise ValueError(f"{op} is not offered on this page")
    return op, None, controls[op]


class HostPolicy:
    """The host as the Agent's policy, with the choose() decision contract.

    offer(page, observation_id) marks the observation the host receives (delivering the same one again changes
    nothing); set(operation, target, text, observation_id) validates the host's choice against that observation's
    action space and starts nothing; the Agent then calls the policy with its current page, where the chosen index
    must still name the same element of the same document (else StalePage: choose again; snapshot.js numbers the
    elements of every new document from 1). latency_ms runs from the observation's first delivery to set(). text()
    hands the TYPE_TEXT value to the Agent's fill step.
    """

    model = "host"

    def __init__(self):
        self._page = None
        self._offered_id = None
        self._offered_at = time.perf_counter()
        self._choice = None
        self._text = None
        self.last: dict | None = None  # the latest validated choice: {operation, target, label, latency_ms}

    def offer(self, page: dict, observation_id: int | None = None) -> None:
        if observation_id is not None and observation_id == self._offered_id and page is self._page:
            return  # the same observation again (a status read): its choice and its clock stand
        self._page, self._offered_id, self._offered_at, self._choice, self._text = (
            page, observation_id, time.perf_counter(), None, None)

    def set(self, operation: str, target: str | None = None, text: str | None = None,
            observation_id: int | None = None) -> None:
        self._choice = None
        if self._page is None:
            raise ValueError("No observation has been delivered yet")
        if observation_id is not None and observation_id != self._offered_id:
            raise ValueError(f"Observation {observation_id} is not the latest delivered one ({self._offered_id})")
        op, key, action = resolve(self._page, operation, target)
        if op == "TYPE_TEXT":
            text = check_text(text)
        elif text not in (None, ""):
            raise ValueError("text is only accepted with TYPE_TEXT")
        self._choice = {"operation": op, "target": key, "identity": self._identity(self._page, action),
                        "latency_ms": round((time.perf_counter() - self._offered_at) * 1000)}
        self.last = {"operation": op, "target": key, "label": action.get("label") if action else None,
                     "latency_ms": self._choice["latency_ms"]}
        self._text = text if op == "TYPE_TEXT" else None

    @staticmethod
    def _identity(page: dict, action: dict | None):
        """The document (performance.timeOrigin, snapshot.js page_key[0]) and the element a choice refers to."""
        key = page.get("page_key")
        document = key[0] if isinstance(key, list) and key else None
        return document, None if action is None else (action.get("node"), action.get("label"), action.get("value"))

    def __call__(self, page: dict, goal: str, history: list) -> dict:
        choice, self._choice = self._choice, None  # consumed once
        if choice is None:
            raise ValueError("The host has not chosen an operation for this observation")
        op, key = choice["operation"], choice["target"]
        try:
            _, _, action = resolve(page, op, key)
        except ValueError:
            raise StalePage("The page changed since the observation; choose again.") from None
        document, element = self._identity(page, action)
        if document != choice["identity"][0]:
            raise StalePage("Another page loaded since the observation; choose again.")
        if element != choice["identity"][1]:
            raise StalePage(f"Element {key} changed since the observation; choose again.")
        selected = action["id"] if action is not None else op
        return {
            "choice": selected, "operation": op, "target": key, "confidence": 1.0, "probabilities": {selected: 1.0},
            "operation_probabilities": {op: 1.0}, "target_probabilities": {key: 1.0} if key else {},
            "target_confidence": 1.0 if key else None, "raw_answers": {}, "model": self.model, "usage": {},
            "latency_ms": choice["latency_ms"], "request": {},
        }

    def text(self, context: dict) -> tuple[str, dict]:
        if not self._text:
            raise ValueError("TYPE_TEXT needs text from the host; nothing typed.")
        return self._text, {"model": self.model, "latency_ms": 0, "usage": {}}


class _GuardedBrowser:
    """The journey tab as the Agent sees it: guarded observations, guarded and measured actions."""

    def __init__(self, runner: "JourneyRunner", tab):
        self._runner, self._tab = runner, tab

    def __getattr__(self, name):
        return getattr(self._tab, name)

    def fresh(self, page, action=None):
        return self._tab.fresh(page, action)

    def act(self, action, page, text=None):
        return self._runner._act(action, page, text)

    def observe(self, screenshot=False):
        return self._runner._observe(screenshot)

    def close(self):
        pass  # the runner owns the tab, its context and the transport


class JourneyRunner:
    """One journey: start() -> observation() / act() (host) or run_auto() (typesafe) -> finish()."""

    min_wait_s = 0.2  # every action: time for a navigation or a request to start
    dead_window_s = 1.0  # an action nothing answered yet: how long to wait for a late answer
    quiet_ms = 500  # page-side quiet window (vitals.js quiet())
    net_quiet_s = 0.3
    inflight_max_s = 3.0  # a request in flight holds the settle at most this long
    poll_s = 0.05
    step_timeout_s = 10.0

    def __init__(self, settings, *, store: RunStore | None = None, transport_factory=None):
        if isinstance(settings, dict):
            settings = EngagementSettings.from_dict(settings)
        self.settings: EngagementSettings = settings
        self.store = store or RunStore(settings.artifacts_dir)
        self.transport_factory = transport_factory
        self.run_id: str | None = None
        self.status = "created"
        self.journey: JourneyRecord = {}
        self.steps: list[JourneyStep] = []
        self.transport = self.chrome = self.context_id = self.collector = self.tab = self.proxy = None
        self.agent: Agent | None = None
        self.guard: CheckoutGuard | None = None
        self.host: HostPolicy | None = None
        self.policy = None
        self._lock = threading.RLock()
        self._pending: dict | None = None
        self._attempt: dict = {}
        self._raw: dict[str, dict] = {}
        self._page_type: str | None = None
        self._overlay = {"present": False, "coverage": 0.0}
        self._audited: str | None = None
        self._unread = False  # the latest observation's audit failed: its page is handled as a checkout page
        self._notes: list[str] = []
        self._boundary = False
        self._challenge = False  # an anti-bot page: no evasion, the journey is not assessable
        self._left: str | None = None  # the URL of another site the tab is on: the journey ends there
        self._broken: str | None = None  # a page that did not load (chrome-error://): the journey ends there
        self._seen: dict | None = None  # {url, document} the journey last saw: a change with no step is the page's
        self._no_progress = False
        self._executed = 0
        self._visited: set[str] = set()
        self._stale_run = 0
        self._observation_id = 0  # the observation the host may act on; every step delivers a new one
        self._result: dict | None = None

    # ---------------------------------------------------------------- lifecycle
    def start(self, url: str, goal: str, *, oracle: str, oracle_params: dict | None = None, profile: str = "mobile",
              policy: str = "host", max_steps: int = 40, optimal_steps: int | None = None,
              optimal_pages: int | None = None) -> dict:
        """Open url in a fresh context and deliver the first observation: {run_id, status, observation}.

        optimal_steps: the minimum number of interactions for the goal (FAI.ACTIONS_RATIO); optimal_pages: the minimum
        number of distinct pages, start page included (Lostness R). Unknown minimums leave those KPIs not applicable.
        """
        with self._lock:
            if self.run_id is not None:
                raise ValueError("This runner already ran a journey; create a new JourneyRunner")
            params = oracles.check_params(oracle, oracle_params)
            if policy not in POLICIES:
                raise ValueError(f"policy must be one of {POLICIES}")
            if profile not in DEVICE_PROFILES:
                raise ValueError(f"profile must be one of {list(DEVICE_PROFILES)}")
            if not isinstance(goal, str) or not goal.strip():
                raise ValueError("Supply a goal")
            for name, value in (("optimal_steps", optimal_steps), ("optimal_pages", optimal_pages)):
                if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                    raise ValueError(f"{name} must be a positive integer")
            if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 1 <= max_steps <= MAX_STEPS:
                raise ValueError(f"max_steps must be an integer from 1 to {MAX_STEPS}")
            if policy == "typesafe" and not os.environ.get("TYPESAFE_API_KEY"):
                raise ValueError("policy 'typesafe' needs TYPESAFE_API_KEY; use policy 'host' inside Claude Code")
            parts = urlsplit(url) if isinstance(url, str) else None
            if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
                raise ValueError(f"Expected an http(s) start URL, got {url!r}")
            self.policy = policy
            self.run_id = self.store.new_run("journey", url, self._settings_record(profile))
            self.journey = {
                "goal": goal.strip(), "oracle": oracle, "oracle_params": params, "profile": profile, "policy": policy,
                "max_steps": max_steps, "status": "running", "verification": None, "steps_path": "steps.jsonl",
                "optimal_steps": optimal_steps, "optimal_pages": optimal_pages, "started_at": iso_now(),
                "finished_at": None,
            }
            self.store.update(self.run_id, lambda run: run.update(journey=dict(self.journey), status="running"))
            self.status = "running"
            try:
                self._launch(url, profile)
                self.host = HostPolicy() if policy == "host" else None
                # TypeSafe is looked up at call time: model.choose through the Agent's module, like the Agent does
                chooser = self.host or (lambda page, goal, history: agent_loop.choose(page, goal, history))
                self.agent = Agent(url, goal, browser=self.proxy, policy=self._gated(chooser),
                                   text_policy=self.host.text if self.host else None)
            except BaseException as exc:
                self._error(exc)
                self._close()
                self._save(run_status="failed")
                raise
            self._stop_checks()
            self._save()
            self._observation_id = 1
            return {"run_id": self.run_id, "status": self.status, "observation": self.observation()}

    def _settings_record(self, profile: str) -> dict:
        s = self.settings
        return {"profiles": [profile], "browser": {"mode": s.browser, "product": None, "headless": s.headless},
                "locale": s.locale, "consent": "none", "anchors_version": load_anchors()["version"],
                "rubrics_version": RUBRICS_VERSION, "profiles_version": PROFILES_VERSION, "repeats": 1,
                "settle_timeout_s": s.settle_timeout_s, "screenshots": s.screenshots}

    def _launch(self, url: str, profile: str) -> None:
        s = self.settings
        factory = self.transport_factory or (lambda: open_transport(s.browser, headless=s.headless, lang=s.lang))
        opened = factory()
        self.transport, self.chrome = opened if isinstance(opened, tuple) else (opened, None)
        self.context_id = new_context(self.transport)
        self.collector = PageCollector(self.transport, self.store, self.run_id, profile=profile, locale=s.locale,
                                       screenshots=s.screenshots, settle_timeout_s=s.settle_timeout_s,
                                       context_id=self.context_id)
        self.tab = self.collector.open(url)
        landing = self.collector.collect(self.tab, stage="extra", page_id=f"{profile}-journey-start",
                                         requested_url=url)
        landing.setdefault("notes", []).append("journey start page")
        arrived = landing.get("final_url") if str(landing.get("final_url") or "").startswith("http") else url
        self.guard = CheckoutGuard(arrived, lexicon_for(s.locale))  # the shop is where the start URL lands
        if landing.get("audit"):  # the first observation reuses this read of the page
            self._audited, self._page_type = landing.get("final_url"), (landing.get("classification") or {}).get("type")
            self._overlay = self._overlays(landing["audit"])
        try:
            product = self.transport.call("Browser.getVersion").get("product")
        except (RuntimeError, OSError):
            product = None

        def record(run):
            run["pages"].append(landing)
            run["settings"]["browser"]["product"] = product
            run["settings"]["device_profile"] = self.collector.applied_profile
            if not CheckoutGuard(url, {}).same_site(arrived):
                run["site"]["final_url"] = arrived  # oracles compare cart links with the site the journey ran on

        self.store.update(self.run_id, record)
        self.proxy = _GuardedBrowser(self, self.tab)

    def close(self) -> None:
        """Release the browser without verification (finish() does both). An unfinished run is left partial."""
        with self._lock:
            self._close()
            if self.run_id is not None and self._result is None and self.agent is not None:
                if self.status == "running":
                    self.status = "error"
                    self._warn("closed before finish(): no verification")
                self._save(run_status="partial")
                self._result = {"run_id": self.run_id, "status": self.status, "verification": None}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _close(self) -> None:
        closers = [
            lambda: self.collector.close(self.tab) if self.collector is not None and self.tab is not None else None,
            lambda: close_context(self.transport, self.context_id) if self.context_id else None,
            lambda: self.transport.close() if self.transport is not None else None,
            lambda: self.chrome.close() if self.chrome is not None else None,
        ]
        for closer in closers:
            try:
                closer()
            except Exception:  # closing is best effort: every resource gets its chance
                pass
        self.tab = self.context_id = self.transport = self.chrome = None

    # ---------------------------------------------------------------- host interface
    def observation(self) -> dict:
        """What the host sees: observation_id (what act() names), url, title, page type, visible text, offered
        elements and controls, budget, notes. Reading it again before the next act() delivers the same observation."""
        with self._lock:
            if self.agent is None:
                raise ValueError("Start a journey first")
            page = self.agent.state["page"]
            elements, _, controls = action_space(page.get("actions") or [])
            if self.host is not None:
                self.host.offer(page, self._observation_id)
            running = self.status == "running"
            out = {
                "observation_id": self._observation_id, "url": page.get("url"), "title": page.get("title"),
                "page_type": self._page_type,
                "text_excerpt": (page.get("text") or "")[:TEXT_EXCERPT], "elements": elements,
                "controls": [*controls, "DONE", "BLOCKED"] if running else [], "step": self._executed,
                "budget_left": max(0, self.journey["max_steps"] - self._executed),
                "guard_notes": self._notes[:MAX_NOTES], "status": self.status,
            }
            if page.get("omitted_actions"):
                out["omitted_elements"] = page["omitted_actions"]
            return out

    def act(self, operation: str, target: str | None = None, text: str | None = None, *,
            observation_id: int) -> dict:
        """Execute the host's choice once: {executed, stale, refused, page_changed, step_metrics, status,
        observation}. observation_id names the observation the choice was made on: on any other than the latest
        delivered one (a repeated or parallel call) nothing runs and the result is stale with the current
        observation. An invalid choice raises ValueError before anything runs."""
        with self._lock:
            self._require_running()
            if self.host is None:
                raise ValueError("act() drives the host policy; this journey runs with run_auto()")
            if isinstance(observation_id, bool) or not isinstance(observation_id, int):
                raise ValueError("act() needs the observation_id of the observation the choice was made on")
            if observation_id != self._observation_id:
                return {"executed": False, "stale": True, "refused": None, "page_changed": None,
                        "step_metrics": None, "status": self.status, "observation": self.observation()}
            self.host.set(operation, target, text, observation_id=observation_id)
            self.agent.pending_text = None  # the host supplies each value: no cached text from a stale attempt
            return self._step()

    def run_auto(self) -> dict:
        """Policy "typesafe": TypeSafe chooses until DONE, BLOCKED, the checkout boundary or the budget."""
        with self._lock:
            if self.policy != "typesafe":
                raise ValueError("run_auto() drives the typesafe policy; the host policy uses act()")
            while self.status == "running":
                self._step()
            return {"run_id": self.run_id, "status": self.status, "steps": self._executed,
                    "observation": self.observation()}

    def finish(self, status: str | None = None) -> dict:
        """Verify the outcome with the oracle (independent of DONE), store friction observations, release everything.
        status: "done" or "blocked" for a journey the host ends while it is still running (default "blocked"); the
        runner detects the other terminal statuses itself (a host that aborts calls close())."""
        with self._lock:
            if self.run_id is None:
                raise ValueError("Start a journey first")
            if self._result is not None:
                return self._result
            if status is not None and status not in HOST_FINISH:
                raise ValueError(f"status must be one of {HOST_FINISH}; the runner sets the others itself")
            if self.agent is None:  # start() failed: nothing ran and nothing can be verified; the run stays failed
                self._close()
                self._result = {"run_id": self.run_id, "status": self.status, "verification": None, "friction": {},
                                "steps": 0, "steps_path": str(self.store.path(self.run_id) / "steps.jsonl")}
                return self._result
            if self.status == "running":
                self.status = status or "blocked"
                if status is None:
                    self._warn("finished by the host without DONE/BLOCKED")
            verification, page = {"passed": None, "checks": {"oracle": self.journey["oracle"]}}, None
            steps = list(self.steps)
            try:
                self._moved_since_observed()  # a navigation after the last observation is logged before the read
                steps = self._recorded_steps()
                if self._challenge:
                    verification["checks"]["not_assessable"] = "bot_challenge"
                    raise _NotVerified
                if self.tab is None:
                    raise RuntimeError("the journey's browser is not available")
                result = oracles.verify(self.journey["oracle"], self.journey["oracle_params"], browser=self.tab,
                                        collector=self.collector, run=self.store.load(self.run_id), steps=steps)
                page = result.pop("page", None)
                verification = {"passed": result["passed"], "checks": result["checks"]}
            except _NotVerified:
                pass
            except Exception as exc:  # the verdict stays unknown; the run records why
                verification["checks"]["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            finally:
                self._close()
            checks = verification["checks"]
            if self.status == "error" and verification["passed"] is not True and not self._challenge:
                # a harness, credential or model failure stopped the journey: not the site's failure (the oracle's
                # reading stays as evidence); a passing oracle still counts as success
                if verification["passed"] is False:
                    checks["oracle_passed"] = False
                elif checks.get("not_assessable"):
                    checks["oracle_not_assessable"] = checks["not_assessable"]
                verification["passed"], checks["not_assessable"] = None, "journey_error"
            passed = verification["passed"]
            unverified = checks.get("not_assessable") or "no_verification"
            observations = friction.metrics(steps, optimal_steps=self.journey.get("optimal_steps"),
                                            optimal_pages=self.journey.get("optimal_pages"), success=passed,
                                            profile=self.journey["profile"], unverified=unverified)
            self.journey.update(verification=verification, finished_at=iso_now())
            run_status = "complete" if passed is not None and self.status != "error" else "partial"
            if page:
                page.setdefault("notes", []).append(f"journey verification: {self.journey['oracle']}")
            self._save(run_status=run_status, observations=observations, page=page, finished=True,
                       not_assessable={"stage": None, "profile": self.journey["profile"], "kpi_id": None,
                                       "reason": "bot_challenge"} if self._challenge else None)
            self._result = {
                "run_id": self.run_id, "status": self.status, "verification": verification,
                "friction": {o["kpi_id"]: o["value"] if o["assessed"] else None for o in observations},
                "steps": self._executed, "steps_path": str(self.store.path(self.run_id) / "steps.jsonl"),
            }
            return self._result

    # ---------------------------------------------------------------- one decision
    def _require_running(self) -> None:
        if self.agent is None:
            raise ValueError("Start a journey first")
        if self.status != "running":
            raise ValueError(f"This journey has stopped ({self.status}); call finish()")

    def _step(self) -> dict:
        self._attempt = {"decision": None, "texts": len(self.agent.state["text_calls"]), "logged": len(self.steps),
                         "executed": self._executed}
        try:
            stale, refused = self._decide_and_act()
            self._stop_checks()
            self._save()
        finally:
            self._observation_id += 1  # used up whatever happened: the host chooses on the observation returned here
        ran = [s for s in self.steps[self._attempt["logged"]:] if friction.executed(s)]
        last = ran[-1] if ran else None
        return {
            "executed": last is not None, "stale": stale, "refused": refused,
            "page_changed": last.get("page_changed") if last else None,
            "step_metrics": self._metrics(last) if last else None, "status": self.status,
            "observation": self.observation(),
        }

    def _decide_and_act(self) -> tuple[bool, str | None]:
        """One decision through the Agent, executed at most once: (stale, guard refusal)."""
        agent = self.agent
        try:
            agent.command("predict")
            self._attempt["decision"] = agent.state["decision"]
            agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})
            decision = self._attempt["decision"]
            if decision["choice"] in ("DONE", "BLOCKED"):
                self.status = "done" if decision["choice"] == "DONE" else "blocked"
                self._log_decision(decision)
            elif agent.state["status"] == "blocked" and self.host is not None:
                # the Agent's no-progress stop is a demo safeguard: the host owns DONE/BLOCKED, so it is told and
                # chooses again (the step budget bounds the run); its repeated dead clicks stay countable
                agent.state["status"] = "ready"
                self._notes.insert(0, NO_PROGRESS)
                if not self._no_progress:
                    self._no_progress = True
                    self._warn("no progress: three actions in a row changed nothing (the host was told and went on)")
            elif agent.state["status"] == "blocked":  # typesafe: the only brake on a model looping on a dead control
                self.status = "blocked"
                self._warn("agent stopped: three actions in a row changed nothing")
        except _Stopped as exc:  # _stop_checks() names the status
            if self.host is not None:  # the host chose on the page before this one: nothing ran
                self._log_attempt(stale=True, note=f"{exc}: the page changed since the observation; nothing ran")
        except StalePage as exc:
            return self._recover(exc, stale=True)["stale"], None
        except GuardRefused as exc:
            self._log_attempt(guard_blocked=True, note=f"refused by the checkout guard: {exc}")
            agent.state["status"] = "ready"
            self._stale_run += 1
            if self.policy == "typesafe" and self._stale_run >= MAX_STALE:
                self.status = "blocked"
                self._warn(f"stopped: {MAX_STALE} decisions in a row executed nothing")
            return False, str(exc)
        except (RuntimeError, OSError) as exc:
            self._recover(exc, stale=False)
        except ValueError as exc:
            if self._pending is None and not self._ran():
                # budgets, an invalid model answer, a missing text credential: nothing executed
                budget = len(agent.state["decisions"]) >= MAX_STEPS * 2 or len(agent.state["history"]) >= MAX_STEPS
                self.status = "budget_exhausted" if budget else "error"
                self._warn(f"stopped: {exc}")
            else:  # after the input (the page after it could not be read): log what ran, then stop
                self._unexpected(exc)
        except Exception as exc:  # anything else (a malformed model answer): log what ran, then stop
            self._unexpected(exc)
        return False, None

    def _ran(self) -> bool:
        """Did this attempt execute (and log) an action? A NAVIGATION or stale record is not one."""
        return self._executed > self._attempt["executed"]

    def _unexpected(self, exc: Exception) -> None:
        """An exception the runner does not know: log what ran, then stop (its own state is not trusted)."""
        self._recover(exc, stale=False)
        if self.status == "running":
            self.status = "error"
            self._warn(f"stopped after an unexpected error: {type(exc).__name__}: {str(exc)[:200]}")

    def _stopping(self) -> str | None:
        """Why the run stops on the latest observation, if it does: no policy is asked and no input is sent there."""
        for stop, reason in ((self._challenge, "bot_challenge"), (self._boundary, "checkout_boundary"),
                             (self._left is not None, "left_shop"), (self._broken is not None, "navigation_error")):
            if stop:
                return reason
        return None

    def _gated(self, policy):
        def decide(page, goal, history):
            if reason := self._stopping():
                raise _Stopped(reason)
            return policy(page, goal, history)
        return decide

    def _stop_checks(self) -> None:
        """The run stops by itself on an anti-bot page, at the checkout boundary (on any site), on another site, on a
        page that did not load and on the step budget, in this order."""
        if self.status == "running" and self._challenge:
            self.status = "blocked"
            self._warn("bot_challenge: an anti-bot page interrupted the journey (no evasion is attempted)")
        if self.status == "running" and self._boundary:
            self.status = "stopped_at_checkout_boundary"
        if self.status == "running" and self._left is not None:
            self.status = "blocked"
            self._warn(f"left_shop: {self._left[:200]}")
        if self.status == "running" and self._broken is not None:
            self.status = "blocked"
            self._warn(f"navigation_error: the page did not load ({self._broken[:200]})")
        if self.status == "running" and self._executed >= self.journey["max_steps"]:
            self.status = "budget_exhausted"

    def _recover(self, exc: Exception, *, stale: bool) -> dict:
        """After an exception inside the Agent: was anything executed? Re-observe, or stop with an error."""
        executed = self._pending is not None or self._ran()
        uncertain = self._pending if self._pending is not None and self._pending.get("uncertain") else None
        if isinstance(exc, RuntimeError) and session_gone(exc):
            if self._pending is not None:
                self._complete_step()
            self.status = "error"
            self._warn(f"renderer_crashed: {str(exc)[:200]}")
            return {"executed": executed}
        if not executed and not stale:
            self.status = "error"  # a model or browser failure before any input
            self._warn(f"stopped before execution: {type(exc).__name__}: {str(exc)[:200]}")
            return {"executed": False}
        if not executed:
            self._stale_run += 1
            self._log_attempt(stale=True, note=str(exc)[:200])
            if self.policy == "typesafe" and self._stale_run >= MAX_STALE:
                self.status = "blocked"
                self._warn(f"stopped: {MAX_STALE} decisions in a row executed nothing (the page never held still)")
        try:
            self._reobserve()
        except Exception as error:  # the page cannot be read again: the journey stops here
            self.status = "error"
            self._warn(f"observation failed: {type(error).__name__}: {str(error)[:200]}")
        if uncertain is not None:
            self._remember(uncertain)
        return {"executed": executed, "stale": not executed}

    def _remember(self, pending: dict) -> None:
        """An input that may have happened enters the Agent's history like an executed one (marked uncertain), so
        the policy's recent actions and the Agent's no-progress stop see it."""
        action, page, now = pending["action"], pending["page"], self.agent.state["page"]
        decision, history = self._decision(), self.agent.state["history"]
        history.append({"step": len(history) + 1, "action": action.get("label"), "kind": action.get("kind"),
                        "choice": action.get("id"), "operation": decision.get("operation"),
                        "target": decision.get("target"), "text": pending.get("text"),
                        "page_changed": now.get("fingerprint") != page.get("fingerprint"), "url": now.get("url"),
                        "uncertain": True})

    def _reobserve(self) -> None:
        for attempt in range(3):
            try:
                self.agent.state["page"] = self.proxy.observe(screenshot=False)
                self.agent.state["decision"] = None
                if self.agent.state["status"] not in ("done", "blocked"):
                    self.agent.state["status"] = "ready"
                return
            except StalePage:
                if attempt == 2:
                    raise
                self._settle(time.monotonic(), self.min_wait_s, mark=None)

    # ---------------------------------------------------------------- guarded execution
    def _act(self, action: dict, page: dict, text: str | None):
        # The Agent may have observed again right before this input (its freshness check): on a checkout page, an
        # anti-bot page or another site nothing more is sent, whatever was chosen and on whichever page.
        if reason := self._stopping():
            raise GuardRefused(reason)
        raw = self._raw.get(page.get("fingerprint")) or page
        page_type = self._guarded_type()
        ok, reason = self.guard.allowed_action(action, raw, page_type)
        if ok and action.get("kind") == "fill":
            ok, reason = self.guard.allow_text(self.tab, action, page_type)
            if ok and (what := personal_text(text)):
                ok, reason = False, f"personal_text: looks like {what}"
        if not ok:
            raise GuardRefused(reason)
        self._drain()  # what happened during the decision is not this action's
        pending = {"action": action, "page": page, "text": text, "text_len": len(text) if text is not None else None,
                   "page_type": self._page_type, "overlay": dict(self._overlay), "mark": self.collector.mark(self.tab)}
        started = time.perf_counter()

        def elapsed() -> float:  # a WAIT is the harness's own pause (Browser.act sleeps), not site time
            return 0.0 if action.get("kind") == "wait" else round((time.perf_counter() - started) * 1000, 1)

        try:
            result = self.tab.act(action, page, text=text)
        except StalePage:
            raise  # rejected before any input
        except (RuntimeError, OSError) as exc:  # the input may have happened: never retried, logged as uncertain
            pending.update(execution_ms=elapsed(), uncertain=f"{type(exc).__name__}: {str(exc)[:200]}",
                           finished=time.monotonic())
            self._pending = pending
            raise
        pending.update(execution_ms=elapsed(), finished=time.monotonic())
        self._pending = pending
        return result

    def _guarded_type(self) -> str | None:
        """The page type the guard applies: a page whose audit failed is handled as a checkout page (fail closed)."""
        return "checkout" if self._unread else self._page_type

    def _observe(self, screenshot: bool = False) -> dict:
        completed = self._pending is not None
        if completed:
            self._complete_step()  # execution is logged before its result is observed
        raw = self.tab.observe(screenshot=screenshot)
        self._read_page(raw)
        page_type = self._guarded_type()
        allowed, notes = self.guard.filter_actions(raw, page_type)
        kept = []
        for action in allowed:
            if action.get("kind") == "fill":
                ok, reason = self.guard.allow_text(self.tab, action, page_type)
                if not ok:
                    notes.append(f"{action.get('id')} '{str(action.get('label') or '')[:40]}': {reason}")
                    continue
            kept.append(action)
        url = raw.get("url") or ""
        web = _web(url)
        self._left = url if web and not self.guard.same_site(url) else None
        self._broken = None if web else url  # chrome-error:// and the like: the page did not load, nothing to leave
        if self._left is not None:
            notes.insert(0, f"left the shop for {url[:120]}: the journey stopped here")
        elif self._broken is not None:
            notes.insert(0, f"the page did not load ({url[:120]}): the journey stopped here")
        elif self._unread:
            notes.insert(0, UNREAD)
        self._notes = notes
        self._boundary = self.guard.should_stop(self._page_type, raw)
        # another site's anti-bot page is not the shop's: the journey left the shop (left_shop), the oracle still runs
        self._challenge = self._challenge or (self._page_type == "challenge" and self._left is None and web)
        timed_out = completed and bool(self.steps) and self.steps[-1].get("settle_reason") != "quiet"
        self._moved(url, _document(raw), self._page_type, follows_timeout=timed_out)
        self._raw = {**dict(list(self._raw.items())[-3:]), raw["fingerprint"]: raw}
        return {**raw, "actions": kept}

    def _read_page(self, raw: dict) -> None:
        """Page type and overlays of the observed page: audit.js once per URL, again after a click or select, and at
        every observation while it fails (the page is unread meanwhile: the guard handles it as a checkout page)."""
        url = raw.get("url")
        if url == self._audited:
            return
        try:
            audit = self.collector.audit(self.tab) or {}
        except (AuditError, StalePage, TimeoutError, RuntimeError) as exc:
            if isinstance(exc, RuntimeError) and session_gone(exc):
                raise
            audit = {}
        if not audit:
            self._audited, self._unread, self._page_type = None, True, None
            self._overlay = {"present": False, "coverage": 0.0}
            return
        self._audited, self._unread = url, False
        self._page_type = classify(audit, url or "", locale=audit.get("lexicon_lang") or self.settings.locale)["type"]
        self._overlay = self._overlays(audit)

    @staticmethod
    def _overlays(audit: dict) -> dict:
        overlays = [o for o in audit.get("overlays") or [] if o.get("interrupting")]
        return {"present": bool(overlays),
                "coverage": round(100 * max((o.get("coverage") or 0 for o in overlays), default=0), 1)}

    # ---------------------------------------------------------------- measuring one step
    def _drain(self) -> None:
        for prefix in DRAINED:
            self.transport.events(self.tab.session, prefix)

    def _read(self, expression: str, timeout: float = 5.0):
        try:
            response = self.transport.call("Runtime.evaluate", self.tab.session, timeout=timeout,
                                           expression=expression, returnByValue=True)
        except TimeoutError:
            return None
        except RuntimeError as exc:
            if session_gone(exc):
                raise
            return None
        if response.get("exceptionDetails"):
            return None
        return response.get("result", {}).get("value")

    def _settle(self, started: float, min_wait: float, *, mark: float | None, answer: bool = False) -> dict:
        """Wait until the page settles after an action (see the module docstring). Returns the last page read with
        "reason" ("quiet" | "timeout") and "waited_ms". mark: the action's epoch mark (None: nothing to wait for);
        answer: the action expects an answer (an interaction), so an unanswered one waits out the dead-click window."""
        session, frame = self.tab.session, self.tab.target
        deadline = started + min(self.step_timeout_s, self.settings.settle_timeout_s)
        loading, responded, inflight, seen = None, mark is None, {}, {}  # loading: since when the main frame loads
        last_net, state = started, None
        while True:
            now = time.monotonic()
            for event in self.transport.events(session, "Page."):
                if event["params"].get("frameId") != frame:
                    continue
                if event["method"] == "Page.frameStartedLoading":
                    loading, responded = now, True
                elif event["method"] == "Page.frameStoppedLoading":
                    loading = None
            for event in self.transport.events(session, "Network."):
                p, method = event["params"], event["method"]
                rid = p.get("requestId")
                if method == "Network.requestWillBeSent":
                    url = (p.get("request") or {}).get("url", "")
                    path = url.split("?")[0]
                    seen[path] = seen.get(path, 0) + 1
                    if p.get("type") in LONG_LIVED or url.startswith(("data:", "blob:")) or seen[path] > 2:
                        continue  # streams and repeated polling of one path do not hold the settle
                    inflight.setdefault(rid, now)
                    last_net = now  # it holds the settle, but a request (a beacon) answers nothing
                elif method in ("Network.loadingFinished", "Network.loadingFailed"):
                    if inflight.pop(rid, None) is not None:
                        last_net = now
                elif rid in inflight:
                    last_net = now
            inflight = {rid: at for rid, at in inflight.items() if now - at < self.inflight_max_s}
            for prefix in ("Runtime.", "Log."):
                self.transport.events(session, prefix)  # journeys read the page-side clock instead
            state = self._read(SETTLE_JS % self.quiet_ms, timeout=max(0.5, min(5.0, deadline - now)))
            if mark is not None and not responded and state:
                responded = self._last_activity(state, mark, VISIBLE_KEYS) is not None
            wait = min_wait if responded or not answer else max(min_wait, self.dead_window_s)
            complete = bool(state) and state.get("ready") == "complete"
            # a loaded document past inflight_max_s is not loading, whatever a lossy transport failed to report
            loads = loading is not None and not (complete and now - loading >= self.inflight_max_s)
            if (now - started >= wait and not loads and not inflight and now - last_net >= self.net_quiet_s
                    and complete and state.get("quiet") is not False):
                reason = "quiet"
            elif time.monotonic() >= deadline:
                reason = "timeout"
            else:
                time.sleep(self.poll_s)
                continue
            return {**(state or {}), "reason": reason, "waited_ms": round((time.monotonic() - started) * 1000, 1)}

    @staticmethod
    def _last_activity(state: dict, mark: float, keys: tuple[str, ...] = ACTIVITY_KEYS) -> float | None:
        """Epoch ms of the latest page-side activity of these kinds after the mark (vitals.js clock), or None."""
        activity = state.get("activity") or {}
        origin = activity.get("time_origin")
        if not isinstance(origin, (int, float)):
            return None
        ends = [origin + t for key in keys
                if isinstance(t := activity.get(key), (int, float)) and origin + t >= mark]
        return max(ends) if ends else None

    def _complete_step(self) -> None:
        """Settle, measure and log the pending action. Whatever fails while measuring, the step is logged."""
        p, self._pending = self._pending, None
        action, page = p["action"], p["page"]
        url_before = page.get("url")
        step: JourneyStep = {
            "step": len(self.steps) + 1, "t_wall": iso_now(), "operation": self._operation(action),
            "target": (self._attempt.get("decision") or {}).get("target"), "label": action.get("label"),
            "kind": action.get("kind"), "role": action.get("role"), "node": action.get("node"),
            "text_len": p["text_len"], "decision_latency_ms": self._decision_latency(), "policy": self.policy,
            "url_before": url_before, "url_after": None, "page_changed": None, "page_type": p["page_type"],
            "since": {}, "execution_ms": p.get("execution_ms"), "settle_ms": None, "settle_reason": None,
            "overlay": p["overlay"], "flags": {}, "status": self.status, "guard_notes": [],
        }
        flags = {"dead_click": False, "rage": False, "backtrack": False, "guard_blocked": False, "stale": False,
                 "external_nav": False, "new_tab": False, "new_document": False, "unexpected_nav": False,
                 "state_changed": None}
        if p.get("uncertain"):
            flags["uncertain"] = True
            step["guard_notes"].append(f"execution not confirmed: {p['uncertain']}")
        self._seen = None  # what the step led to, once measured: the next observation is compared with it
        try:
            settle = self._settle(p["finished"], self.min_wait_s, mark=p["mark"],
                                  answer=step["operation"] in friction.INTERACTIONS)
            step["settle_reason"] = settle["reason"]
            step["since"] = self.collector.since(self.tab, p["mark"])
            url_after = settle.get("href") or self._read("location.href") or url_before
            step["url_after"] = url_after
            end = self._last_activity(settle, p["mark"])
            done = p["mark"] + (p.get("execution_ms") or 0.0)
            step["settle_ms"] = round(min(max(0.0, end - done), settle["waited_ms"]), 1) if end is not None else 0.0
            origin = (settle.get("activity") or {}).get("time_origin")
            flags["new_document"] = isinstance(origin, (int, float)) and origin > p["mark"] - 1
            self._seen = {"url": url_after, "document": origin if isinstance(origin, (int, float)) else None}
            # what the action can change (document, URL, scroll, viewport, field values), unlike visible text that a
            # countdown or a carousel changes by itself: the dead-click and rage evidence
            key = self._read(PAGE_KEY_JS)
            flags["state_changed"] = key.get("key") != page.get("page_key") if isinstance(key, dict) else None
            try:
                step["page_changed"] = not self.tab.fresh(page)
            except (StalePage, RuntimeError):
                step["page_changed"] = None
            opened = self._close_new_tabs()
            if opened:
                flags["new_tab"] = True
                step["guard_notes"] += [f"closed a tab the page opened: {u[:120]}" for u in opened]
            flags["external_nav"] = _web(url_after) and not self.guard.same_site(url_after)
            if not _web(url_after):
                flags["navigation_error"] = True  # the page did not load (chrome-error://): not another site
        except (RuntimeError, OSError, ValueError) as exc:
            if isinstance(exc, RuntimeError) and session_gone(exc):
                flags["session_gone"] = True
            step["guard_notes"].append(f"measurement failed: {type(exc).__name__}: {str(exc)[:200]}")
        operation = step["operation"]
        flags["unexpected_nav"] = friction.unexpected_nav({**step, "flags": flags})
        flags["dead_click"] = friction.is_dead_click({**step, "flags": flags})
        flags["rage"] = friction.rage_flags([*self.steps, {**step, "flags": flags}])[-1]
        before, after = friction.normalize_url(url_before), friction.normalize_url(step["url_after"])
        flags["backtrack"] = bool(after and after != before and after in self._visited)
        self._visited.update(u for u in (before, after) if u)
        step["flags"] = flags
        self._executed += 1
        self._stale_run = 0
        if self._executed >= self.journey["max_steps"] and self.status == "running":
            step["status"] = "budget_exhausted"
        self._log(step)
        if operation in ("CLICK", "SELECT"):
            self._audited = None  # the page may have changed type or opened an overlay: audit it again

    def _moved(self, url: str, document, page_type: str | None, *, follows_timeout: bool = False) -> None:
        """Log a navigation the journey did not make: a new document (or another site) since what the journey last
        saw, with no step of its own in between (a redirect, a refresh or a script while the policy decided). It
        executed nothing (operation NAVIGATION); right after a step whose settle timed out it may be that step's late
        navigation (flags.follows_timeout: a visit, not an unexpected navigation)."""
        seen, self._seen = self._seen, {"url": url, "document": document}
        if not seen or not url:
            return
        known = isinstance(document, (int, float)) and isinstance(seen["document"], (int, float))
        new_document = known and document != seen["document"]
        external = _web(url) and not self.guard.same_site(url)
        if not new_document and not (external and self.guard.same_site(seen["url"] or "")):
            return
        before, after = friction.normalize_url(seen["url"]), friction.normalize_url(url)
        flags = {"new_document": new_document, "external_nav": external,
                 "backtrack": bool(after and after != before and after in self._visited)}
        if not _web(url):
            flags["navigation_error"] = True
        if follows_timeout:
            flags["follows_timeout"] = True
        flags["unexpected_nav"] = friction.unexpected_nav({"operation": friction.NAVIGATION, "flags": flags})
        self._visited.update(u for u in (before, after) if u)
        note = ("a new document right after a step whose settle timed out: possibly that step's late navigation"
                if follows_timeout else "the page navigated by itself between steps; nothing was executed")
        self._log({"step": len(self.steps) + 1, "t_wall": iso_now(), "operation": friction.NAVIGATION,
                   "target": None, "label": None, "kind": None, "role": None, "node": None, "text_len": None,
                   "decision_latency_ms": None, "policy": self.policy, "url_before": seen["url"], "url_after": url,
                   "page_changed": True, "page_type": page_type, "since": {}, "execution_ms": None,
                   "settle_ms": None, "settle_reason": None, "overlay": dict(self._overlay), "flags": flags,
                   "status": self.status, "guard_notes": [note]})

    def _moved_since_observed(self) -> None:
        """At finish(): a navigation since the latest observation (the host finished without acting again)."""
        if self.tab is None:
            return
        try:
            value = self._read(FINISH_JS)
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], str):
                self._moved(value[1], value[0] if isinstance(value[0], (int, float)) else None, None)
        except Exception:  # best effort: the oracle reads the page as it is either way
            pass

    def _recorded_steps(self) -> list[dict]:
        """steps.jsonl as stored; the in-memory records (the same steps, kept after each write) if it cannot be read."""
        try:
            return self.store.read_steps(self.run_id)
        except (OSError, ValueError) as exc:
            self._warn(f"steps.jsonl unreadable, the in-memory steps were used: {type(exc).__name__}: {str(exc)[:200]}")
            return list(self.steps)

    def _close_new_tabs(self) -> list[str]:
        if not self.context_id:
            return []
        infos = self.transport.call("Target.getTargets", timeout=5).get("targetInfos") or []
        opened = [i for i in infos if i.get("type") == "page" and i.get("browserContextId") == self.context_id
                  and i.get("targetId") != self.tab.target]
        for info in opened:
            try:
                self.transport.call("Target.closeTarget", targetId=info["targetId"], timeout=5)
            except (RuntimeError, OSError):
                pass
        return [info.get("url") or "" for info in opened]

    @staticmethod
    def _operation(action: dict) -> str:
        if action.get("kind") == "scroll":
            return "SCROLL_UP" if action.get("id") == "scroll_up" else "SCROLL_DOWN"
        return KIND_OPERATION.get(action.get("kind"), str(action.get("kind") or "").upper())

    def _decision(self) -> dict:
        """This attempt's decision; for a host choice the Agent never turned into a decision (stale), the choice."""
        if self._attempt.get("decision"):
            return self._attempt["decision"]
        return dict(self.host.last or {}) if self.host is not None else {}

    def _decision_latency(self) -> float | None:
        """Policy latency plus text generation: excluded from site-attributable time."""
        decision = self._decision()
        latency = decision.get("latency_ms")
        texts = (self.agent.state["text_calls"] if self.agent else [])[self._attempt.get("texts", 0):]
        text_ms = sum(t.get("latency_ms") or 0 for t in texts)
        return None if latency is None and not texts else round((latency or 0) + text_ms, 1)

    def _metrics(self, step: dict) -> dict:
        since = step.get("since") or {}
        keys = ("first_response_ms", "first_request_ms", "mutations", "navigations", "requests", "errors",
                "shifts_post_input", "event_timing_max_ms")
        return {**{k: since.get(k) for k in keys}, "execution_ms": step.get("execution_ms"),
                "settle_ms": step.get("settle_ms"), "settle_reason": step.get("settle_reason"),
                "flags": {k: v for k, v in (step.get("flags") or {}).items() if v}}

    # ---------------------------------------------------------------- records
    def _log(self, step: JourneyStep) -> None:
        self.store.append_step(self.run_id, step)
        self.steps.append(step)

    def _log_decision(self, decision: dict) -> None:
        page = self.agent.state["page"]
        self._log({"step": len(self.steps) + 1, "t_wall": iso_now(), "operation": decision["operation"],
                   "target": None, "label": None, "kind": None, "role": None, "node": None, "text_len": None,
                   "decision_latency_ms": self._decision_latency(), "policy": self.policy,
                   "url_before": page.get("url"), "url_after": page.get("url"), "page_changed": None,
                   "page_type": self._page_type, "since": {}, "execution_ms": None, "settle_ms": None,
                   "settle_reason": None, "overlay": dict(self._overlay), "flags": {}, "status": self.status,
                   "guard_notes": []})

    def _log_attempt(self, *, stale: bool = False, guard_blocked: bool = False, note: str = "") -> None:
        """A decision that executed nothing (stale page or guard refusal): logged, not counted as a step."""
        decision = self._decision()
        page = self.agent.state["page"]
        action = next((a for a in page.get("actions") or [] if a.get("id") == decision.get("choice")), {})
        self._log({"step": len(self.steps) + 1, "t_wall": iso_now(), "operation": decision.get("operation"),
                   "target": decision.get("target"), "label": action.get("label") or decision.get("label"),
                   "kind": action.get("kind"),
                   "role": action.get("role"), "node": action.get("node"), "text_len": None,
                   "decision_latency_ms": self._decision_latency(), "policy": self.policy,
                   "url_before": page.get("url"), "url_after": None, "page_changed": None,
                   "page_type": self._page_type, "since": {}, "execution_ms": None, "settle_ms": None,
                   "settle_reason": None, "overlay": dict(self._overlay),
                   "flags": {"stale": stale, "guard_blocked": guard_blocked}, "status": self.status,
                   "guard_notes": [note] if note else []})

    def _warn(self, message: str) -> None:
        self.journey.setdefault("notes", []).append(message)

    def _error(self, exc: BaseException) -> None:
        self.status = "error"
        self._warn(f"{type(exc).__name__}: {str(exc)[:300]}")

    def _save(self, *, run_status: str | None = None, observations=None, page=None, finished: bool = False,
              not_assessable: dict | None = None) -> None:
        self.journey["status"] = self.status
        notes = list(self.journey.get("notes") or [])

        def update(run):
            if not_assessable:
                run.setdefault("not_assessable", []).append(not_assessable)
            run["journey"] = {k: v for k, v in self.journey.items() if k != "notes"}
            run["warnings"] = list(dict.fromkeys([*run.get("warnings", []), *notes]))
            if run_status:
                run["status"] = run_status
            if observations is not None:
                run["observations"] = observations
            if page:
                run["pages"].append(page)
            if finished:
                run["finished_at"] = self.journey["finished_at"]

        self.store.update(self.run_id, update)
