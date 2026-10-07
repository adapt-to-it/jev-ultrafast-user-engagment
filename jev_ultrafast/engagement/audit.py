"""audit_shop: the deterministic multi-page audit of one shop, per device profile.

For every profile: a new isolated browser context, a PageCollector, the funnel (crawler.discover_funnel), the
deception tests in further fresh contexts (deception.run_deception_tests), then the context is closed. The run
(RunStore) receives pages, not_assessable, deception, warnings and errors as they are produced, and finally the
deterministic observations (checks.observations) and a status: "complete" when every requested stage was reached on
every profile, "partial" when funnel pages were collected but something is missing, "failed" when none was.
A Chromium this function launched is always closed, and so is the transport, whatever happens.
"""

from collections.abc import Callable

from . import checks
from .collectors import PageCollector
from .crawler import discover_funnel
from .deception import DECEPTION_VERSION, run_deception_tests
from .judgments import RUBRICS_VERSION
from .lexicon import lexicon_for
from .profiles import PROFILES_VERSION, close_context, new_context
from .safety import CheckoutGuard
from .schemas import STAGES
from .scoring import load_anchors
from .settings import EngagementSettings
from .store import RunStore, iso_now
from .transport import open_transport

ACCEPTED = ("not_requested",)  # reasons that do not make a run partial


def _settings_snapshot(settings: EngagementSettings) -> dict:
    return {
        "profiles": list(settings.profiles), "stages": list(settings.stages),
        "browser": {"mode": settings.browser, "product": None, "headless": settings.headless, "transport": None},
        "locale": settings.locale, "consent": settings.consent, "repeats": settings.repeats,
        "max_pages": settings.max_pages, "settle_timeout_s": settings.settle_timeout_s,
        "screenshots": settings.screenshots, "anchors_version": load_anchors()["version"],
        "rubrics_version": RUBRICS_VERSION, "profiles_version": PROFILES_VERSION,
        "checks_version": checks.CHECKS_VERSION, "deception_version": DECEPTION_VERSION, "applied_profiles": {},
    }


def _open(settings: EngagementSettings, transport_factory):
    """(transport, launched Chromium or None)."""
    if transport_factory is None:
        return open_transport(settings.browser, headless=settings.headless, lang=settings.lang)
    opened = transport_factory()
    return opened if isinstance(opened, tuple) else (opened, None)


def _status(run: dict, settings: EngagementSettings) -> str:
    """failed: no funnel page (a bot challenge alone is evidence, not a funnel page). complete: every requested stage
    reached on every profile, without errors; a stage the funnel only passes through (a listing between a requested
    home and product page) does not make the run partial."""
    if not any(p.get("stage") in STAGES for p in run.get("pages") or []):
        return "failed"
    requested = [s for s in STAGES if s in settings.stages]
    missing = [m for m in run.get("not_assessable") or [] if m.get("reason") not in ACCEPTED
               and m.get("stage") in requested]
    reached = {(p.get("profile"), p.get("stage")) for p in run["pages"]}
    complete = all((profile, stage) in reached for profile in settings.profiles for stage in requested)
    return "complete" if complete and not missing and not run.get("errors") else "partial"


def audit_shop(settings: EngagementSettings, *, store: RunStore | None = None, transport_factory=None,
               progress: Callable[[str], None] | None = None) -> str:
    """Run the audit and return its run_id. transport_factory() returns a CdpTransport (or (transport, launched
    Chromium)); without it the transport comes from settings.browser (transports.open_transport)."""
    store = store or RunStore(settings.artifacts_dir)
    say = progress or (lambda message: None)
    run_id = store.new_run("audit", settings.url, _settings_snapshot(settings))
    store.update(run_id, lambda run: run.update(status="running"))
    transport = launched = None
    try:
        say(f"opening the browser ({settings.browser})")
        transport, launched = _open(settings, transport_factory)
        try:
            product = transport.call("Browser.getVersion").get("product")
        except (RuntimeError, TimeoutError):
            product = None

        def browser_info(run):
            run["settings"]["browser"].update(product=product, transport=getattr(transport, "kind", None))
            if getattr(transport, "lossy", False):
                run["warnings"].append("the transport may drop CDP events: network KPIs come from Resource Timing")
        store.update(run_id, browser_info)
        for profile in settings.profiles:
            try:
                _audit_profile(settings, profile, transport, store, run_id, say)
            except Exception as exc:  # e.g. no browser context: the next profile may still work
                message = f"{profile}: {type(exc).__name__}: {str(exc)[:300]}"
                store.update(run_id, lambda run: run["errors"].append(message))
        say("deterministic observations")
        store.update(run_id, lambda run: run.update(observations=checks.observations(run)))
    except Exception as exc:  # recorded in the run; the caller gets the run_id either way
        message = f"audit: {type(exc).__name__}: {str(exc)[:300]}"
        store.update(run_id, lambda run: run["errors"].append(message))
    finally:
        for closer in (transport, launched):
            try:
                if closer is not None:
                    closer.close()
            except Exception:  # never mask the run's outcome; Chromium is killed by LaunchedChrome.close()
                pass

    def finish(run):
        run.update(status=_status(run, settings), finished_at=iso_now())
    store.update(run_id, finish)
    say(f"done: {store.load(run_id)['status']}")
    return run_id


def _audit_profile(settings, profile, transport, store, run_id, say) -> None:
    guard = CheckoutGuard(settings.url, lexicon_for(settings.locale))
    context = new_context(transport)
    closing = None
    try:
        collector = PageCollector(transport, store, run_id, profile=profile, locale=settings.locale,
                                  screenshots=settings.screenshots, settle_timeout_s=settings.settle_timeout_s,
                                  context_id=context)
        pages, missing = discover_funnel(collector, settings, profile=profile, guard=guard, progress=say)
    finally:
        try:
            close_context(transport, context)
        except (RuntimeError, TimeoutError, OSError) as exc:  # the collected pages stay; the context goes with Chrome
            closing = f"{profile}: browser context not closed: {type(exc).__name__}: {str(exc)[:160]}"

    def save_funnel(run):
        reached = {p["stage"] for p in pages if p["stage"] in STAGES}
        run["pages"] += pages
        run["not_assessable"] += [m for m in missing if m["stage"] not in reached]
        if collector.applied_profile:
            run["settings"]["applied_profiles"][profile] = collector.applied_profile
        if closing:
            run["warnings"].append(closing)
        for item in missing:
            if item["reason"].startswith("error"):
                run["errors"].append(f"{profile}: {item['stage']}: {item['reason']}")
            elif item["reason"] not in ACCEPTED and item["stage"] not in reached:
                run["warnings"].append(f"{profile}: {item['stage']} not assessable ({item['reason']})")

    store.update(run_id, save_funnel)
    say(f"{profile}: deception tests")
    try:
        result = run_deception_tests(transport, settings, pages, profile=profile, store=store, run_id=run_id,
                                     guard=guard, progress=say)
    except Exception as exc:
        result = {"version": DECEPTION_VERSION, "profile": profile, "errors": [f"{type(exc).__name__}: {exc}"[:300]]}

    def save_deception(run):
        run["deception"][profile] = result
        run["warnings"] += [f"{profile}: deception test error: {error}" for error in result.get("errors") or []]
    store.update(run_id, save_deception)
