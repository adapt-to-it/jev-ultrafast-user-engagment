"""audit_shop: the deterministic multi-page audit of one shop, per device profile.

For every profile: a new isolated browser context, a PageCollector, the funnel (crawler.discover_funnel), the
deception tests in further fresh contexts (deception.run_deception_tests), then the context is closed. The run
(RunStore) receives pages, not_assessable, deception, warnings and errors as they are produced, and finally the
deterministic observations (checks.observations) and a status: "complete" when every requested stage was reached on
every profile, "partial" when funnel pages were collected but something is missing, "failed" when none was.
The shop is where the start URL lands: a home page on another registrable domain re-anchors CheckoutGuard there for
the funnel and the deception tests (crawler.landed_guard), and run["site"]["final_url"] records it.
With TYPESAFE_API_KEY set, the funnel asks Jev for a lookup the lexicon missed (crawler: discover_funnel(jev_fallback);
one request per lookup, at most 6 per profile); its requests add up in run["model_calls"]["crawler_fallback"]
{requests, latency_ms, input_tokens, model, stages} and a stage reached that way gets the warning "<profile>: <stage>
found through the Jev fallback: not reproducible across runs". Without the key nothing of this exists.
A Chromium this function launched is always closed, and so is the transport, whatever happens.
"""

import os
from collections.abc import Callable

from . import checks
from .collectors import PageCollector
from .crawler import NO_REQUEST, discover_funnel, landed_guard
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
        pages, missing = discover_funnel(collector, settings, profile=profile, guard=guard, progress=say,
                                         jev_fallback=bool(os.environ.get("TYPESAFE_API_KEY")))
    finally:
        try:
            close_context(transport, context)
        except (RuntimeError, TimeoutError, OSError) as exc:  # the collected pages stay; the context goes with Chrome
            closing = f"{profile}: browser context not closed: {type(exc).__name__}: {str(exc)[:160]}"
    # the shop is where the start URL landed (the funnel re-anchored its own guard there): the deception tests too
    home = next((p for p in pages if p.get("stage") == "home"), None)
    landed = landed_guard(guard, home, settings.locale)

    def save_funnel(run):
        reached = {p["stage"] for p in pages if p["stage"] in STAGES}
        if landed is not guard:
            run["site"].setdefault("final_url", home["final_url"])  # oracles read cart links against this site
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
        _jev_fallbacks(run, profile, pages, reached)

    store.update(run_id, save_funnel)
    say(f"{profile}: deception tests")
    try:
        result = run_deception_tests(transport, settings, pages, profile=profile, store=store, run_id=run_id,
                                     guard=landed, progress=say)
    except Exception as exc:
        result = {"version": DECEPTION_VERSION, "profile": profile, "errors": [f"{type(exc).__name__}: {exc}"[:300]]}

    def save_deception(run):
        run["deception"][profile] = result
        run["warnings"] += [f"{profile}: deception test error: {error}" for error in result.get("errors") or []]
    store.update(run_id, save_deception)


def _tokens(usage) -> int:
    value = usage.get("input_tokens") if isinstance(usage, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _jev_fallbacks(run: dict, profile: str, pages: list[dict], reached: set[str]) -> None:
    """The profile's Jev fallback lookups (PageRecord.probes["jev_fallback"]) added to run["model_calls"]
    ["crawler_fallback"], and a warning per stage reached through one: another run may get another answer. Nothing
    when there was none. requests counts the lookups that attempted one (every reason but crawler.NO_REQUEST, so a
    "jev_error" counts, a TypeSafe key gone mid-run included); post_json's own retries of a 429, 503 or 529 are part
    of that one request. latency_ms adds up their time (a "jev_error" too); input_tokens comes from the answers' usage
    only (a failed request has none)."""
    asked = [(page, probe) for page in pages for probe in (page.get("probes") or {}).get("jev_fallback") or []
             if isinstance(probe, dict)]
    if not asked:
        return
    calls = run.get("model_calls") if isinstance(run.get("model_calls"), dict) else {}
    before = calls.get("crawler_fallback") if isinstance(calls.get("crawler_fallback"), dict) else {}
    sent = [probe for _, probe in asked if probe.get("reason") not in NO_REQUEST]
    calls["crawler_fallback"] = {
        "requests": (before.get("requests") or 0) + len(sent),
        "latency_ms": (before.get("latency_ms") or 0) + sum(p.get("latency_ms") or 0 for p in sent),
        "input_tokens": (before.get("input_tokens") or 0) + sum(_tokens(p.get("usage")) for p in sent),
        "model": next((p["model"] for p in reversed(sent) if p.get("model")), None) or before.get("model"),
        "stages": [*(before.get("stages") or []), *({"profile": profile, "page_id": page.get("page_id"),
                                                     **{k: probe.get(k) for k in ("stage", "purpose", "operation",
                                                                                  "label", "probability", "executed",
                                                                                  "reason")}}
                                                    for page, probe in asked)],
    }
    run["model_calls"] = calls
    for stage in dict.fromkeys(p["stage"] for _, p in asked if p.get("executed") and p.get("stage") in reached):
        run["warnings"].append(f"{profile}: {stage} found through the Jev fallback: not reproducible across runs")
