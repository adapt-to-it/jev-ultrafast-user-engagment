"""Page collection: one PageRecord per loaded page (vitals, network, errors, audit, page type, screenshot).

PageCollector owns the CDP domains of the pages it opens: it applies the device profile, enables Runtime/Log/Network,
installs vitals.js before any page script and buffers each page's events itself, so a record only holds what its own
document produced: the previous document keeps running until the new one commits, and its requests (another loader,
or sent before the new document's request) and console output (before Runtime.executionContextsCleared) in that
window are left out. Cross-site iframes run in their own renderer: a background thread attaches them as they appear
(paused until then), applies the profile's network, CPU and identity settings and resumes them, and their Network
events and errors (console errors, uncaught exceptions: a same-site frame's reach the page session anyway) join the
page's. A request or an error an old cross-site frame produces after the next click navigation started, before that
frame is detached at the commit, may still count for the new page. A collect() on the document of an earlier record (a
client-side route change, a back-forward cache restore) reports no load timings: they belong to that record.
Everything here is read-only; clicks and typing belong to the crawler and the journey runner.
"""

import base64
import io
import json
import math
import threading
import time
import weakref
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from ..browser import Browser, StalePage, capture_screenshot, session_gone
from .lexicon import effective_language, lexicon_for, registrable_domain
from .pagetypes import classify
from .profiles import DEVICE_PROFILES, NO_THROTTLING, apply_profile
from .schemas import PAGE_TYPES, AuditPayload, NetworkSummary, PageErrors, PageRecord, StepSince, Vitals
from .settings import LANGS

VITALS_JS = Path(__file__).with_name("vitals.js").read_text(encoding="utf-8")
AUDIT_JS = Path(__file__).with_name("audit.js").read_text(encoding="utf-8")

ACTIVITY = ("(() => { const v = window.__jevVitals; "
            "return v ? {vitals: true, ...v.activity()} : {vitals: false, ready_state: document.readyState}; })()")
RESOURCES = "window.__jevVitals ? window.__jevVitals.read({resources: true}).resources : []"
PAINT_KEYS = ("fcp", "lcp", "lcp_element", "cls", "tbt_approx", "loaf_count")  # need a visible, rendering page
# Timings that describe the shop's document: none of them is the shop's when Chrome shows its own error page.
NAVIGATION_KEYS = ("ttfb", "fcp", "lcp", "lcp_element", "cls", "cls_post_input", "long_tasks_ms", "tbt_approx",
                   "loaf_count", "dcl_ms", "load_ms", "event_timing_max_ms")
STREAMS = ("Network.webSocket", "Network.eventSource", "Network.reportingApi", "Network.trustTokenOperation")
LONG_LIVED = ("EventSource", "WebSocket")  # request types that stay open by design: they never hold the settle
FRAME_FILTER = [{"type": "iframe"}]  # Target.setAutoAttach: out-of-process iframes only (not workers, not popups)
# What a cross-site frame adds to its page besides Network events: its errors, as a same-site frame's reach the page
# session. Its execution-context events stay out (the page's commit is read from them).
FRAME_RUNTIME = ("Runtime.consoleAPICalled", "Runtime.exceptionThrown")
ASSET_BYTES = {"Script": "bytes_js", "Image": "bytes_img", "Stylesheet": "bytes_css", "Font": "bytes_font"}
EXTENSIONS = {"js": "Script", "mjs": "Script", "css": "Stylesheet", "woff": "Font", "woff2": "Font", "ttf": "Font",
              "otf": "Font", "png": "Image", "jpg": "Image", "jpeg": "Image", "gif": "Image", "webp": "Image",
              "avif": "Image", "svg": "Image", "ico": "Image"}
INITIATORS = {"navigation": "Document", "script": "Script", "img": "Image", "image": "Image", "fetch": "Fetch",
              "xmlhttprequest": "XHR", "beacon": "Ping", "video": "Media", "audio": "Media", "track": "TextTrack"}
EMPTY_SINCE: StepSince = {"first_response_ms": None, "mutations": 0, "mutations_total": 0, "navigations": 0,
                          "requests": 0, "requests_total": 0, "errors": 0, "shifts_post_input": 0.0,
                          "event_timing_max_ms": None}


class AuditError(RuntimeError):
    """audit.js threw inside the page."""


def _host(url: str | None) -> str:
    try:
        return urlsplit(url or "").hostname or ""
    except ValueError:
        return ""


def _document_request(params: dict, frame_id: str | None) -> bool:
    """A Network.requestWillBeSent that starts a main-frame document (its first hop, not a redirect)."""
    return (params.get("type") == "Document" and not params.get("redirectResponse")
            and params.get("requestId") == params.get("loaderId")
            and (frame_id is None or params.get("frameId") == frame_id))


def _document_start(events: list[dict], frame_id: str | None) -> int | None:
    """Index of the latest main-frame document request (earlier events belong to the previous document), or None."""
    start = None
    for i, event in enumerate(events):
        if event["method"] == "Network.requestWillBeSent" and _document_request(event["params"], frame_id):
            start = i
    return start


def document_events(events: list[dict], frame_id: str | None) -> dict:
    """The events of the latest main-frame document and what its own Document request tells.

    Returns {"network", "runtime", "start", "ttfb", "failure", "prefetched"}: network = the events from the Document
    request on, without the requests the previous document started meanwhile (another loader before the commit, the
    old main frame loader at any time, or any request sent before the Document request, e.g. by an old cross-site
    frame whose events were drained later); runtime = the events after the commit (Runtime.executionContextsCleared),
    so console output and exceptions of the unloading page are not counted; ttfb = first request hop to the final
    response headers in ms, from CDP timing (DevTools network throttling included; navigation.responseStart is not);
    failure = errorText of a Document request that failed (not merely canceled); prefetched = the document came from
    the prefetch cache (the shop's speculation rules fetched it before the click).
    """
    start = _document_start(events, frame_id)
    if start is None:
        return {"network": events, "runtime": events, "start": None, "ttfb": None, "failure": None,
                "prefetched": False}
    tail, doc = events[start:], events[start]["params"]
    rid, loader = doc.get("requestId"), doc.get("loaderId")
    commit = next((i for i, e in enumerate(tail) if e["method"] == "Runtime.executionContextsCleared"), None)
    if commit is None:
        commit = next((i for i, e in enumerate(tail) if e["method"] == "Runtime.executionContextCreated"
                       and ((e["params"].get("context") or {}).get("auxData") or {}).get("frameId") == frame_id), None)
    stale, sent = set(), doc.get("timestamp")
    for i, event in enumerate(tail):
        p = event["params"]
        if event["method"] != "Network.requestWillBeSent" or p.get("requestId") == rid or p.get("loaderId") == loader:
            continue
        if ((commit is not None and i < commit) or (p.get("loaderId") and p.get("frameId") == frame_id)
                or (sent is not None and p.get("timestamp") is not None and p["timestamp"] < sent)):
            stale.add(p.get("requestId"))
    ttfb = failure = None
    prefetched = False
    for event in tail:
        p = event["params"]
        if p.get("requestId") != rid:
            continue
        if event["method"] == "Network.responseReceived":
            response = p.get("response") or {}
            prefetched = prefetched or bool(response.get("fromPrefetchCache"))
            timing = response.get("timing") or {}
            headers = timing.get("receiveHeadersEnd")  # receiveHeadersStart precedes the emulated latency
            if timing.get("requestTime") is not None and headers is not None and headers >= 0 and doc.get("timestamp"):
                value = (timing["requestTime"] + headers / 1000 - doc["timestamp"]) * 1000
                ttfb = round(value, 1) if value >= 0 else None
        elif event["method"] == "Network.loadingFailed" and not p.get("canceled") and failure is None:
            failure = p.get("errorText") or "failed"
    return {"network": [e for e in tail if e["params"].get("requestId") not in stale],
            "runtime": tail[commit:] if commit is not None else tail, "start": start, "ttfb": ttfb, "failure": failure,
            "prefetched": prefetched}


def network_summary(events: list[dict], document_url: str | None) -> NetworkSummary:
    """Requests, encoded bytes and errors from Network.* events. Each redirect hop counts as a request.

    A request keeps its first real failure (a later "canceled" event for the same request does not hide it). The
    browser's own /favicon.ico probe is a request but not an HTTP error of the page.
    """
    requests: dict[str, dict] = {}
    for event in events:
        method, p = event["method"], event["params"]
        rid = p.get("requestId")
        if method == "Network.requestWillBeSent":
            url = (p.get("request") or {}).get("url", "")
            if url.startswith(("data:", "blob:", "about:")):
                continue
            if rid in requests and p.get("redirectResponse"):
                hop = p["redirectResponse"]
                requests[rid]["hops"].append({"url": requests[rid]["url"], "status": hop.get("status"),
                                              "bytes": hop.get("encodedDataLength") or 0})
                requests[rid]["url"] = url
                continue
            requests[rid] = {"url": url, "type": p.get("type") or "Other", "status": None, "bytes": 0, "data": 0,
                             "done": False, "failed": None, "hops": [],
                             "mixed": (p.get("request") or {}).get("mixedContentType"),
                             "browser": (p.get("initiator") or {}).get("type") == "other"
                             and urlsplit(url).path == "/favicon.ico"}
        elif rid not in requests:
            continue
        elif method == "Network.responseReceived":
            response = p.get("response") or {}
            requests[rid].update(status=response.get("status"), type=p.get("type") or requests[rid]["type"],
                                 url=response.get("url") or requests[rid]["url"])
        elif method == "Network.dataReceived":
            requests[rid]["data"] += p.get("encodedDataLength") or 0
        elif method == "Network.loadingFinished":
            requests[rid].update(bytes=p.get("encodedDataLength") or 0, done=True)
        elif method == "Network.loadingFailed":
            if not requests[rid]["failed"] or requests[rid]["failed"]["canceled"]:
                requests[rid]["failed"] = {"canceled": bool(p.get("canceled")), "blocked": p.get("blockedReason")}
    rows = []
    for r in requests.values():
        rows += [{**hop, "type": r["type"], "mixed": r["mixed"]} for hop in r["hops"]]
        rows.append({"url": r["url"], "type": r["type"], "status": r["status"], "mixed": r["mixed"],
                     "bytes": r["bytes"] if r["done"] else r["data"], "browser": r["browser"],
                     "failed": bool(r["failed"] and not r["failed"]["canceled"])})
    return _summarize(rows, document_url, "cdp")


def resource_summary(resources: list[dict], document_url: str | None) -> NetworkSummary:
    """Fallback for lossy transports: Resource Timing rows from vitals.js.

    transferSize is 0 for cross-origin resources without Timing-Allow-Origin, so bytes are a lower bound.
    """
    rows = []
    for r in resources:
        url = r.get("name") or ""
        if url.startswith(("data:", "blob:")):
            continue
        ext = url.split("?")[0].split("#")[0].rsplit(".", 1)[-1].lower() if "." in url.rsplit("/", 1)[-1] else ""
        kind = INITIATORS.get(r.get("type") or "") or EXTENSIONS.get(ext) or "Other"
        rows.append({"url": url, "type": kind, "status": r.get("status") or None, "bytes": r.get("transfer") or 0,
                     "mixed": None, "failed": False})
    return _summarize(rows, document_url, "resource_timing")


def _summarize(rows: list[dict], document_url: str | None, source: str) -> NetworkSummary:
    first_party = registrable_domain(_host(document_url))
    https = (document_url or "").startswith("https:")
    by_type: dict[str, dict] = {}
    totals = dict.fromkeys(ASSET_BYTES.values(), 0)
    third_bytes, third_domains, errors, total = 0, set(), [], 0
    for row in rows:
        size = int(row.get("bytes") or 0)
        total += size
        bucket = by_type.setdefault(row["type"].lower(), {"requests": 0, "bytes": 0})
        bucket["requests"] += 1
        bucket["bytes"] += size
        if row["type"] in ASSET_BYTES:
            totals[ASSET_BYTES[row["type"]]] += size
        domain = registrable_domain(_host(row["url"]))
        if domain and first_party and domain != first_party:
            third_bytes += size
            third_domains.add(domain)
        if (row.get("status") or 0) >= 400 and not row.get("browser"):
            errors.append({"url": row["url"][:200], "status": row["status"]})
    return {
        "requests": len(rows), "bytes_transfer": total, **totals, "bytes_third_party": third_bytes,
        "third_party_share": round(third_bytes / total, 4) if total else 0.0,
        "third_party_domains": sorted(third_domains)[:20], "by_type": by_type,
        "failed": sum(1 for row in rows if row.get("failed")), "http_errors": len(errors),
        "http_error_samples": errors[:10],
        "mixed_content": sum(1 for row in rows if https and (
            row["url"].startswith("http:") or row.get("mixed") in ("blockable", "optionally-blockable"))),
        "source": source,
    }


def page_errors(events: list[dict]) -> PageErrors:
    """console = console.error/assert + uncaught exceptions + error-level log entries; page = uncaught exceptions.

    Log entries from the network source are left out: failed responses are PERF.HTTP_ERRORS (one home per signal).
    """
    console = page = 0
    samples: list[str] = []
    for event in events:
        method, p = event["method"], event["params"]
        message = None
        if method == "Runtime.exceptionThrown":
            details = p.get("exceptionDetails") or {}
            message = (details.get("exception") or {}).get("description") or details.get("text") or "exception"
            page += 1
        elif method == "Runtime.consoleAPICalled" and p.get("type") in ("error", "assert"):
            message = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args") or []) or p["type"]
        elif method == "Log.entryAdded":
            entry = p.get("entry") or {}
            if entry.get("level") != "error" or entry.get("source") == "network":
                continue
            message = entry.get("text") or "log error"
        else:
            continue
        console += 1
        if len(samples) < 10:
            samples.append(" ".join(str(message).split())[:200])
    return {"console": console, "page": page, "samples": samples}


def visual_metrics(jpeg: bytes, dpr: float = 1.0) -> dict:
    """Screenshot statistics at CSS-pixel scale (comparable across device pixel ratios).

    colorfulness: Hasler & Süsstrunk (2003), sigma_rgyb + 0.3 * mu_rgyb with rg = R - G, yb = (R + G) / 2 - B.
    edge_density: share of pixels whose Laplacian response (Pillow FIND_EDGES on luminance) is at least 48.
    bytes_per_px: JPEG size at quality 75 divided by pixels, a compressibility proxy.
    """
    from PIL import Image, ImageChops, ImageFilter, ImageStat

    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    if dpr and dpr > 1:
        image = image.resize((max(1, round(image.width / dpr)), max(1, round(image.height / dpr))), Image.BILINEAR)
    r, g, b = image.split()
    rg = ImageStat.Stat(ImageChops.subtract(r, g, scale=2.0, offset=128))  # (R - G) / 2 + 128
    yb = ImageStat.Stat(ImageChops.subtract(ImageChops.add(r, g, scale=2.0), b, scale=2.0, offset=128))
    mean_rg, mean_yb = 2 * (rg.mean[0] - 128), 2 * (yb.mean[0] - 128)
    colorfulness = math.hypot(2 * rg.stddev[0], 2 * yb.stddev[0]) + 0.3 * math.hypot(mean_rg, mean_yb)
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v >= 48 else 0)
    if edges.width > 2 and edges.height > 2:
        edges = edges.crop((1, 1, edges.width - 1, edges.height - 1))  # the filter's own frame is not an edge
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=75)
    return {"colorfulness": round(colorfulness, 2), "edge_density": round(ImageStat.Stat(edges).mean[0] / 255, 4),
            "bytes_per_px": round(buffer.tell() / (image.width * image.height), 4),
            "width": image.width, "height": image.height}


def _watch_frames(ref, wake: threading.Event) -> None:
    """The frame watcher's loop. It holds its collector weakly and ends with the collector's last page, its transport,
    or any error (the collector starts a new watcher when it next needs one). It polls every frame_poll_s while a page
    loads or settles and every frame_idle_s otherwise; a navigation wakes it at once."""
    pruned = time.monotonic()
    while (collector := ref()) is not None:
        prune = time.monotonic() - pruned >= collector.frame_prune_s
        try:
            alive = collector._pump_frames(prune=prune)
        except Exception:  # a closed transport (ConnectionError) or anything unexpected
            alive = False
        if prune:
            pruned = time.monotonic()
        if not collector._keep_watching(alive):
            return
        pause = collector.frame_poll_s if collector._busy() else collector.frame_idle_s
        del collector
        wake.wait(pause)
        wake.clear()


class PageCollector:
    """Collects PageRecords for one profile inside one browser context.

    open(url) creates the tab (profile applied, vitals.js installed) and loads url; follow it with collect(), or with
    load_page(browser, url) for the same url, which reuses that load. Later pages: load_page() for a URL, collect()
    after a click that navigated. Settling waits for the load event, 1.5 s without network activity and no request
    in flight, and 2 s without a new LCP candidate, capped at settle_timeout_s from the navigation. Post-load polling
    of one URL path is not activity; a request in flight holds the settle for at most inflight_max_age_s (a long-poll,
    a stream or a hung API must not cost the whole cap; the record then says so in notes), and EventSource and
    WebSocket connections never hold it. A request whose response headers arrived and that then got no data for
    net_quiet_s (a fetch whose body the page never reads, a keepalive ping) stops holding it silently. Requests of the
    previous document (sent before the latest main-frame document request, or by its loader) never hold it: an old
    renderer that goes away never reports their end.
    Every page timing is a cold navigation: prerendering is disallowed, and a document the shop's speculation rules
    prefetched before a click gets no load timings (vitals.speculative, a "speculative_navigation" note).
    close(browser) closes a tab and forgets its frames at once.
    """

    net_quiet_s = 1.5
    poll_gap_s = 0.5
    lcp_quiet_s = 2.0
    inflight_max_age_s = 5.0
    poll_s = 0.1
    frame_poll_s = 0.02
    frame_idle_s = 0.25
    frame_prune_s = 2.0

    def __init__(self, transport, store, run_id, *, profile: str, locale: str = "it", screenshots: bool = True,
                 settle_timeout_s: float = 20.0, context_id: str | None = None):
        self.transport, self.store, self.run_id = transport, store, run_id
        self.profile, self.locale, self.lang = profile, locale, LANGS.get(locale, locale)
        self.lexicon = lexicon_for(locale)  # the run locale's; each page is audited in its effective language
        self._lexicons: dict[str, str] = {}  # effective language -> lexicon JSON for audit.js
        self.screenshots, self.settle_timeout_s, self.context_id = screenshots, settle_timeout_s, context_id
        self.applied_profile: dict | None = None  # what apply_profile() set, for the run settings
        self._pages: dict[str, dict] = {}  # session -> {"events", "started", "url", "fresh", "origins"}
        self._frames = threading.Lock()  # guards _roots, _children, _loading and _watcher
        self._pump = threading.Lock()  # one drain of the frame sessions at a time keeps their events in order
        self._roots: dict[str, str] = {}  # page session -> target id, for pages whose cross-site frames we attach
        self._children: dict[str, dict] = {}  # frame session -> {"root", "target", "events", "stale", "detached"}
        self._loading: set[str] = set()  # page sessions between a navigation and the end of its settle
        self._watcher: threading.Thread | None = None
        self._wake = threading.Event()  # wakes an idle watcher when a page starts loading

    # ---------------------------------------------------------------- pages
    def open(self, url: str) -> Browser:
        state: dict = {}

        def prepare(browser):
            self.applied_profile = apply_profile(self.transport, browser.session, self.profile, lang=self.lang)
            try:  # every page timing is a cold navigation: no prerendered document is ever activated
                browser.call("Page.setPrerenderingAllowed", isAllowed=False)
                self.applied_profile["prerendering"] = "disallowed"
            except (RuntimeError, TimeoutError) as exc:
                if isinstance(exc, RuntimeError) and session_gone(exc):
                    raise
                self.applied_profile["prerendering"] = "unsupported"  # older Chrome: vitals.speculative still tells
            browser.call("Runtime.enable")
            browser.call("Log.enable")
            browser.call("Page.setLifecycleEventsEnabled", enabled=True)
            browser.call("Page.addScriptToEvaluateOnNewDocument", source=VITALS_JS)
            self._attach_frames(browser)
            self._loading_started(browser.session)
            self._drain(browser)  # setup events are not the page's
            state["started"] = time.monotonic()

        browser = Browser(url, transport=self.transport, browser_context_id=self.context_id, background=False,
                          prepare=prepare, load_timeout=self.settle_timeout_s)
        self._pages[browser.session] = {"events": [], "started": state.get("started"), "url": url, "fresh": True,
                                        "origins": {}}
        return browser

    def close(self, browser: Browser) -> None:
        """Close the tab and forget it at once: its buffered events, its cross-site frames and their sessions."""
        session = getattr(browser, "session", None)
        try:
            browser.close()
        finally:
            with self._frames:
                self._roots.pop(session, None)
                self._loading.discard(session)
                gone = [child for child, entry in self._children.items() if entry["root"] == session]
                for child in gone:
                    del self._children[child]
            for child in gone:
                if discard := getattr(self.transport, "discard", None):
                    discard(child)
            self._pages.pop(session, None)

    def load_page(self, browser: Browser, url: str, *, stage: str, page_id: str) -> PageRecord:
        """Navigate (once, never retried) and collect. A TimeoutError or CdpError from the navigation propagates."""
        page = self._page(browser)
        if page.get("fresh") and page.get("url") == url:
            return self.collect(browser, stage=stage, page_id=page_id, requested_url=url)
        self._drain(browser)  # stale events of the previous page
        self._reset_frames(browser.session)
        self._ensure_watcher()
        self._loading_started(browser.session)
        page.update(events=[], started=time.monotonic(), url=url, fresh=True)
        result = browser.navigate(url, load_timeout=self.settle_timeout_s)
        record = self.collect(browser, stage=stage, page_id=page_id, requested_url=url)
        notes = record.setdefault("notes", [])
        if (result or {}).get("errorText") and not any(n.startswith("navigation_error") for n in notes):
            notes.append(f"navigation_error: {result['errorText']}")
        return record

    def collect(self, browser: Browser, *, stage: str, page_id: str, requested_url: str | None = None) -> PageRecord:
        """A navigation that failed (Chrome's own error page) gives a record without the shop's timings, audit or
        visual metrics: classification "other" with the signal "navigation_error", and the failure in notes. The
        document of an earlier record (a client-side route change, a back-forward cache restore, no navigation at
        all) gives a record without load timings (vitals.soft_navigation, a "same_document" note): network and errors
        then count what happened since the previous record. So does a document the shop's speculation rules fetched
        before the click (vitals.speculative "prefetch" or "prerender", a "speculative_navigation" note): its timings
        are not a cold navigation, and a prefetch ran outside the profile's network emulation. On a throttled profile
        TTFB comes from CDP only: Navigation Timing misses the emulated latency."""
        page = self._page(browser)
        started = page.get("started")
        notes: list[str] = []
        reason, activity, hung = self._settle(browser, page["events"], started or time.monotonic())
        loaded_ms = round((time.monotonic() - started) * 1000, 1) if started is not None else None
        vitals = self.vitals(browser) or {}
        lossy = getattr(self.transport, "lossy", False)
        resources = self._evaluate(browser, RESOURCES) if lossy else None
        page["events"].extend(self._drain(browser))
        own = document_events(page["events"], browser.target)
        events = own["network"]
        if not vitals:
            notes.append("vitals unavailable: vitals.js was not running in this document")
        vitals["settled_reason"] = reason
        if hung:
            notes.append(f"settle: {len(hung)} request{'s' if len(hung) > 1 else ''} still in flight "
                         f"(> {self.inflight_max_age_s:g} s): {hung[0][:200]}")
        origin = (activity or {}).get("time_origin") or self._evaluate(browser, "performance.timeOrigin")
        origin = round(origin, 3) if isinstance(origin, (int, float)) else None
        earlier = page["origins"].get(origin) if origin is not None else None  # {"page_id", "until"}
        request = page["events"][own["start"]]["params"].get("requestId") if own["start"] is not None else None
        speculative = None
        if earlier is None and (vitals.get("activation_start") or 0) > 0:
            speculative = "prerender"
        elif earlier is None and own["start"] is not None and own["prefetched"]:
            speculative = "prefetch"
        if earlier is not None:  # the same document as an earlier record: no load timings (below)
            resources = [r for r in resources or [] if (r.get("start") or 0) >= earlier["until"]] if lossy else None
        elif speculative:
            pass  # no load timings (below)
        elif own["ttfb"] is not None and (not lossy or request == self._frame_loader(browser)):
            vitals.update(ttfb=own["ttfb"], ttfb_source="cdp")
        elif (self.applied_profile or {}).get("network") and vitals.get("ttfb") is not None:
            vitals["ttfb"] = None  # navigation.responseStart leaves out the emulated round trip
            notes.append("ttfb unavailable: " + ("the transport may drop CDP events" if lossy else
                                                 "no CDP timing for this document's request")
                         + " and Navigation Timing misses the emulated network latency")
        elif vitals.get("ttfb") is not None:
            vitals["ttfb_source"] = "navigation_timing"
        href = self._evaluate(browser, "location.href")
        failure = own["failure"] or ("error page" if str(href or "").startswith("chrome-error:") else None)
        audit: AuditPayload = {}
        if failure:
            vitals.update(dict.fromkeys(NAVIGATION_KEYS))
            vitals.pop("ttfb_source", None)
            notes.append(f"navigation_error: {failure}")
        else:
            try:
                audit = self.audit(browser)
            except (AuditError, StalePage, TimeoutError) as exc:
                notes.append(f"audit failed: {str(exc)[:200]}")
        final_url = audit.get("url") or href or requested_url
        network = (resource_summary(resources or [], final_url) if lossy
                   else network_summary(events, final_url))
        if lossy:
            notes.append("network from Resource Timing: the transport may drop CDP events")
        if getattr(self.transport, "dropped", 0):
            notes.append(f"transport dropped {self.transport.dropped} events")
        if earlier is not None and not failure:
            vitals.update(dict.fromkeys((*NAVIGATION_KEYS, "js_errors")), soft_navigation=True)
            vitals.pop("ttfb_source", None)
            loaded_ms = None
            notes.append(f"same_document: client-side route change or back-forward cache restore of "
                         f"{earlier['page_id']}; its load timings belong to that record, network and errors count "
                         "what happened since the previous record")
        elif speculative and not failure:
            vitals.update(dict.fromkeys(NAVIGATION_KEYS), speculative=speculative)
            vitals.pop("ttfb_source", None)
            loaded_ms = None
            notes.append(f"speculative_navigation: the shop's speculation rules {speculative}ed this document before "
                         "the click, so it was not a cold navigation (a prefetch also runs outside the profile's "
                         "network emulation); its load timings are not reported")
        elif vitals.get("visibility_state_at_load") not in (None, "visible"):
            vitals.update(dict.fromkeys(PAINT_KEYS))
            notes.append("paint metrics unavailable: the page was hidden while loading")
        if loaded_ms is None and earlier is None and not speculative and activity and own["start"] is not None:
            loaded_ms = round(activity.get("now") or 0, 1)  # a new document after a click: its own clock at settle
        if (audit.get("meta") or {}).get("speculation_rules"):
            notes.append("speculation_rules: this page declares speculation rules; the documents it prefetches are "
                         "fetched outside its record and are not counted in its network")
        language = audit.get("lexicon_lang") or self.locale
        classification = ({"type": "other", "confidence": 0.0, "scores": dict.fromkeys(PAGE_TYPES, 0.0),
                           "signals": ["navigation_error"]} if failure
                          else classify(audit, final_url or "", locale=language))
        record: PageRecord = {
            "page_id": page_id, "stage": stage, "profile": self.profile, "url": requested_url or final_url,
            "final_url": final_url, "status_code": self._status(events, browser.target),
            "classification": classification, "vitals": vitals, "network": network,
            "errors": page_errors(own["runtime"]), "audit": audit, "screenshot": None, "snapshot": None,
            "loaded_ms": loaded_ms, "visual": {}, "notes": notes,
        }
        if self.screenshots:
            self._screenshot(browser, record, (audit.get("viewport") or {}).get("dpr") or 1, metrics=not failure)
        if self.store is not None and self.run_id and audit:
            record["snapshot"] = self.store.write_json(self.run_id, f"snapshots/{page_id}.json", {
                "page_id": page_id, "url": record["url"], "final_url": final_url,
                "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "audit": audit})
        if origin is not None:  # the record that loaded this document, and how far (document ms) records cover it
            seen = page["origins"].setdefault(origin, {"page_id": page_id, "until": 0.0})
            seen["until"] = max(seen["until"], (activity or {}).get("now") or 0.0)
        page.update(events=[], started=None, fresh=False)
        return record

    # ---------------------------------------------------------------- reads
    def language(self, browser: Browser) -> str:
        """The page's effective language: its declared <html lang> when the lexicon has it, else the run locale's."""
        declared = self._evaluate(browser, "document.documentElement ? document.documentElement.lang : ''")
        return effective_language(declared if isinstance(declared, str) else None, self.locale)

    def audit(self, browser: Browser) -> AuditPayload:
        """One audit.js evaluation with the lexicon of the page's effective language (recorded as lexicon_lang).
        AuditError when it throws; a gone session (CdpError -32001) propagates."""
        language = self.language(browser)
        if language not in self._lexicons:
            self._lexicons[language] = json.dumps(lexicon_for(language))
        expression = f"({AUDIT_JS})({self._lexicons[language]})"
        for attempt in range(3):
            try:
                response = self.transport.call("Runtime.evaluate", browser.session, timeout=45, expression=expression,
                                               returnByValue=True)
            except RuntimeError as exc:
                if session_gone(exc):
                    raise
                text = str(exc)  # e.g. the execution context was destroyed by a navigation
            else:
                details = response.get("exceptionDetails")
                if not details:
                    value = response.get("result", {}).get("value") or {}
                    if value:
                        value["lexicon_lang"] = language
                    return value
                text = (details.get("exception") or {}).get("description") or details.get("text") or "audit.js failed"
            if attempt == 2 or "context" not in text.lower():
                raise AuditError(text.splitlines()[0][:300])
            time.sleep(0.3)  # the document was replaced during the read
        return {}

    def vitals(self, browser: Browser) -> Vitals:
        return self._evaluate(browser, "window.__jevVitals ? window.__jevVitals.read() : null") or {}

    def mark(self, browser: Browser) -> float:
        """An epoch timestamp in ms (performance.timeOrigin + now): since() accepts it across document navigations."""
        value = self._evaluate(browser, "window.__jevVitals ? window.__jevVitals.mark() : null")
        return float(value) if value is not None else time.time() * 1000

    def since(self, browser: Browser, mark: float) -> StepSince:
        self._ensure_watcher()
        deadline = time.monotonic() + 1.0
        while True:
            value = self._evaluate(browser, f"window.__jevVitals ? window.__jevVitals.since({float(mark)!r}) : null")
            if value is not None or time.monotonic() >= deadline:
                break
            time.sleep(0.05)  # a new document is still starting
        return value if value is not None else {**EMPTY_SINCE, "available": False}

    # ---------------------------------------------------------------- internals
    def _page(self, browser: Browser) -> dict:
        return self._pages.setdefault(browser.session, {"events": [], "started": None, "url": None, "fresh": False,
                                                        "origins": {}})

    def _evaluate(self, browser: Browser, expression: str, timeout: float = 10.0):
        """A read-only evaluation; None while the document is being replaced. A gone session raises."""
        try:
            response = self.transport.call("Runtime.evaluate", browser.session, timeout=timeout, expression=expression,
                                           returnByValue=True)
        except TimeoutError:
            return None
        except RuntimeError as exc:
            if session_gone(exc):
                raise
            return None
        if response.get("exceptionDetails"):
            return None
        return response.get("result", {}).get("value")

    def _frame_loader(self, browser: Browser) -> str | None:
        """The main frame's current loader id (lossy transports: is the Document request we saw the current one?)."""
        try:
            return ((browser.call("Page.getFrameTree").get("frameTree") or {}).get("frame") or {}).get("loaderId")
        except (RuntimeError, TimeoutError) as exc:
            if isinstance(exc, RuntimeError) and session_gone(exc):
                raise
            return None

    def _drain(self, browser: Browser) -> list[dict]:
        """The page session's buffered events, then the Network events of its cross-site frames."""
        events = self.transport.events(browser.session)
        for event in events:
            if event["method"] in ("Target.attachedToTarget", "Target.detachedFromTarget"):
                self._frame_event(browser.session, event)
        return events + self._frame_events(browser.session)

    def _settle(self, browser: Browser, events: list[dict], started: float) -> tuple[str, dict | None, list[str]]:
        """Returns (reason, the last activity() read, URLs of requests still in flight past inflight_max_age_s).

        A request holds the settle while younger than inflight_max_age_s (its age from CDP timestamps, so a request
        sent during a long navigation is old when first seen); then, like EventSource and WebSocket connections and
        polling, its events are not activity. A request whose response headers arrived and that then got no data for
        net_quiet_s is released silently (a body the page never reads: Chrome reports neither data nor its end).
        Polling: after the load event, a request for a URL path (any query) that a post-load request already asked
        for at least poll_gap_s earlier (a heartbeat, an analytics ping); a burst of requests to one path is still
        activity. The previous document's requests never hold it, with the rules document_events() applies: sent
        before the latest main-frame document request, by another main-frame loader, or by another loader before
        the commit (an old renderer that goes away reports no end for them).
        """
        deadline = started + self.settle_timeout_s
        holding: dict[str, float] = {}  # request -> monotonic time it was sent
        heard: dict[str, float] = {}  # held request whose response arrived -> monotonic time of its latest data
        released: set[str] = set()  # requests whose events no longer count: polling, long-lived, too old, stale
        hung: dict[str, str] = {}  # released for age and not finished -> URL
        urls: dict[str, str] = {}
        kinds: dict[str, str | None] = {}
        stamps: dict[str, float] = {}  # request -> CDP timestamp of its first hop
        first_sent: dict[tuple[str, str], float] = {}  # (host, path) -> CDP timestamp of its first post-load request
        document: dict = {}  # the latest main-frame document request: {"sent", "loader", "request", "committed"}
        loaded = False
        last_network, seen, activity, latest = time.monotonic(), 0, None, None

        def stale(p: dict) -> bool:
            if not document or p.get("requestId") == document["request"] or p.get("loaderId") == document["loader"]:
                return False
            sent = p.get("timestamp")
            return ((document["sent"] is not None and isinstance(sent, (int, float)) and sent < document["sent"])
                    or (bool(p.get("loaderId")) and p.get("frameId") == browser.target) or not document["committed"])

        self._loading_started(browser.session)
        try:
            while True:
                self._ensure_watcher()
                events.extend(self._drain(browser))
                now = time.monotonic()
                fresh = events[seen:]
                seen = len(events)
                batch = [e for e in fresh if e["method"].startswith("Network.") and not e["method"].startswith(STREAMS)]
                stamps_now = [e["params"]["timestamp"] for e in batch
                              if isinstance(e["params"].get("timestamp"), (int, float))]
                latest = max([latest, *stamps_now]) if latest is not None else max(stamps_now, default=None)
                for event in fresh:
                    method, p = event["method"], event["params"]
                    if method in ("Runtime.executionContextsCleared", "Runtime.executionContextCreated"):
                        context = (p.get("context") or {}).get("auxData") or {}
                        if document and (method.endswith("Cleared") or context.get("frameId") == browser.target):
                            document["committed"] = True  # the new document replaced the old one
                        continue
                    if not method.startswith("Network.") or method.startswith(STREAMS):
                        continue
                    rid = p.get("requestId")
                    if method == "Network.requestWillBeSent" and _document_request(p, browser.target):
                        sent = p.get("timestamp") if isinstance(p.get("timestamp"), (int, float)) else None
                        document = {"sent": sent, "loader": p.get("loaderId"), "request": rid, "committed": False}
                        for other in list(holding) + list(hung):  # what the previous document still had in flight
                            if other != rid and (sent is None or stamps.get(other, sent) < sent or other in hung):
                                holding.pop(other, None)
                                heard.pop(other, None)
                                hung.pop(other, None)
                                released.add(other)
                    if rid in released:
                        if method in ("Network.loadingFinished", "Network.loadingFailed"):
                            hung.pop(rid, None)
                        continue
                    if method == "Network.requestWillBeSent":
                        if rid in holding:  # a redirect hop
                            last_network = now
                            continue
                        url, sent = (p.get("request") or {}).get("url", ""), p.get("timestamp")
                        if stale(p):
                            released.add(rid)
                            continue
                        if loaded and p.get("type") != "Document":
                            parts = urlsplit(url)
                            key = (parts.netloc, parts.path)
                            if (sent or 0.0) - first_sent.setdefault(key, sent or 0.0) >= self.poll_gap_s:
                                released.add(rid)
                                continue
                        last_network = now
                        if p.get("type") in LONG_LIVED:
                            released.add(rid)
                            continue
                        age = latest - sent if isinstance(sent, (int, float)) and latest is not None else 0.0
                        holding[rid], urls[rid], kinds[rid] = now - max(0.0, age), url, p.get("type")
                        if isinstance(sent, (int, float)):
                            stamps[rid] = sent
                    elif method in ("Network.loadingFinished", "Network.loadingFailed"):
                        heard.pop(rid, None)
                        if holding.pop(rid, None) is not None:
                            last_network = now
                    elif method in ("Network.dataReceived", "Network.responseReceived") and rid in holding:
                        last_network = heard[rid] = now
                for rid, sent_at in list(holding.items()):
                    if now - sent_at >= self.inflight_max_age_s:
                        del holding[rid]
                        released.add(rid)
                        hung[rid] = urls.get(rid, "")
                    elif rid in heard and kinds.get(rid) != "Document" and now - heard[rid] >= self.net_quiet_s:
                        del holding[rid]  # headers (and whatever body came) arrived, then silence: not loading
                        released.add(rid)
                remaining = max(0.5, min(5.0, deadline - now))
                activity = self._evaluate(browser, ACTIVITY, timeout=remaining)
                complete = activity and not activity.get("vitals") and activity.get("ready_state") == "complete"
                loaded = bool(activity and (activity.get("load_end") is not None or complete))
                network_quiet = now - last_network >= self.net_quiet_s and not holding
                reason = None
                if activity and activity.get("vitals"):
                    load_end = activity.get("load_end")
                    lcp_ref = activity.get("lcp_at") if activity.get("lcp_at") is not None else load_end
                    if (load_end is not None and network_quiet
                            and (activity.get("now") or 0) - (lcp_ref or 0) >= self.lcp_quiet_s * 1000):
                        reason = "quiet"
                elif activity and activity.get("ready_state") == "complete" and network_quiet:
                    reason = "load_only"
                if reason is None and time.monotonic() >= deadline:
                    reason = "timeout"
                if reason:
                    return reason, activity, list(hung.values())
                time.sleep(self.poll_s)
        finally:
            with self._frames:
                self._loading.discard(browser.session)

    # ---------------------------------------------------------------- cross-site frames
    def _attach_frames(self, browser: Browser) -> None:
        """Auto-attach the page's out-of-process iframes, paused until the watcher has set them up. Lossy transports
        (the Browser Harness daemon) only forward events of sessions they attached themselves: not there."""
        if getattr(self.transport, "lossy", False):
            return
        try:
            browser.call("Target.setAutoAttach", autoAttach=True, waitForDebuggerOnStart=True, flatten=True,
                         filter=FRAME_FILTER)
        except RuntimeError as exc:
            if session_gone(exc):
                raise
            return
        with self._frames:
            self._roots[browser.session] = browser.target
        self._ensure_watcher()

    def _ensure_watcher(self) -> None:
        """Start the frame watcher when pages with frames exist and none runs (it ends on any error): a dead watcher
        would leave every new cross-site iframe paused."""
        with self._frames:
            if not self._roots or (self._watcher is not None and self._watcher.is_alive()):
                return
            self._watcher = threading.Thread(target=_watch_frames, args=(weakref.ref(self), self._wake),
                                             name="jev-frames", daemon=True)
            self._watcher.start()

    def _keep_watching(self, alive: bool) -> bool:
        with self._frames:
            if alive and self._roots:
                return True
            self._watcher = None
            return False

    def _loading_started(self, session: str) -> None:
        """The page starts a navigation or a settle: the watcher polls fast until the settle ends."""
        with self._frames:
            self._loading.add(session)
        self._wake.set()

    def _busy(self) -> bool:
        with self._frames:
            return any(session in self._roots for session in self._loading)

    def _pump_frames(self, *, prune: bool = False) -> bool:
        """One watcher round: attach and resume new frames, buffer the frames' events. False: nothing left."""
        with self._frames:
            sessions = [*self._roots, *(c for c, v in self._children.items() if not v["detached"])]
        for session in sessions:
            for event in self.transport.events(session, "Target."):
                self._frame_event(session, event)
        self._pump_children()
        if prune:
            self._prune_frames()
        with self._frames:
            return bool(self._roots)

    def _frame_event(self, parent: str, event: dict) -> None:
        params = event["params"]
        child = params.get("sessionId")
        if not child:
            return
        if event["method"] == "Target.attachedToTarget":
            info = params.get("targetInfo") or {}
            with self._frames:
                if child in self._children:
                    return
                root = parent if parent in self._roots else (self._children.get(parent) or {}).get("root")
                self._children[child] = {"root": root, "target": info.get("targetId"), "events": [], "stale": False,
                                         "detached": False}
            self._adopt(child, iframe=info.get("type") == "iframe")
        elif event["method"] == "Target.detachedFromTarget":
            with self._frames:
                if child in self._children:
                    self._children[child]["detached"] = True

    def _adopt(self, child: str, *, iframe: bool) -> None:
        """The profile's network, cache, CPU, touch and identity settings on a new frame session, then resume it."""
        applied = self.applied_profile or {}
        calls: list[tuple[str, dict]] = []
        if iframe:
            named = DEVICE_PROFILES.get(self.profile) if isinstance(self.profile, str) else None
            calls += [("Network.enable", {}),
                      ("Network.setCacheDisabled", {"cacheDisabled": applied.get("cache_disabled", True)}),
                      ("Network.emulateNetworkConditions", {"offline": False,
                                                             **(applied.get("network") or NO_THROTTLING)})]
            if applied.get("user_agent"):
                identity = {"userAgent": applied["user_agent"], "acceptLanguage": applied.get("accept_language", ""),
                            "platform": (named or {}).get("platform", "")}
                if applied.get("ua_metadata"):
                    identity["userAgentMetadata"] = applied["ua_metadata"]
                calls.append(("Emulation.setUserAgentOverride", identity))
            if (applied.get("cpu_rate") or 1) != 1:
                calls.append(("Emulation.setCPUThrottlingRate", {"rate": applied["cpu_rate"]}))
            if applied.get("touch"):
                calls.append(("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5}))
            calls += [("Runtime.enable", {}), ("Log.enable", {})]  # its errors count with the page's (PERF)
            calls.append(("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True,
                                                   "filter": FRAME_FILTER}))
        try:
            for method, params in calls:
                try:
                    self.transport.call(method, child, timeout=5, **params)
                except (RuntimeError, TimeoutError) as exc:
                    if isinstance(exc, RuntimeError) and session_gone(exc):
                        return
        finally:
            try:
                self.transport.call("Runtime.runIfWaitingForDebugger", child, timeout=5)
            except (RuntimeError, TimeoutError):
                pass

    def _pump_children(self) -> None:
        with self._pump:
            with self._frames:
                children = list(self._children)
            for child in children:
                events = [*self.transport.events(child, "Network."), *self.transport.events(child, "Log."),
                          *(e for e in self.transport.events(child, "Runtime.") if e["method"] in FRAME_RUNTIME)]
                if not events:
                    continue
                with self._frames:
                    entry = self._children.get(child)
                    if entry is not None and not entry["stale"]:
                        entry["events"].extend(events)

    def _frame_events(self, root: str) -> list[dict]:
        """Take the buffered Network and error events of root's frames; forget frames that are gone."""
        self._pump_children()
        out, gone = [], []
        with self._frames:
            for child, entry in list(self._children.items()):
                if entry["root"] != root:
                    continue
                out += entry["events"]
                entry["events"] = []
                if entry["detached"]:
                    gone.append(child)
                    del self._children[child]
        for child in gone:
            self.transport.discard(child)
        return out

    def _reset_frames(self, root: str) -> None:
        """Before a navigation: the current frames belong to the previous document."""
        with self._frames:
            for entry in self._children.values():
                if entry["root"] == root:
                    entry.update(stale=True, events=[])

    def _prune_frames(self) -> None:
        """Forget closed pages and their frames (Browser.close() does not tell the collector)."""
        try:
            infos = self.transport.call("Target.getTargets", timeout=5).get("targetInfos") or []
        except (RuntimeError, TimeoutError):
            return
        live = {info.get("targetId") for info in infos}
        gone = []
        with self._frames:
            for session, target in list(self._roots.items()):
                if target not in live:
                    del self._roots[session]
                    self._loading.discard(session)
            for child, entry in list(self._children.items()):
                if entry["root"] not in self._roots:
                    gone.append(child)
                    del self._children[child]
                elif entry["target"] not in live:
                    entry["detached"] = True  # its buffered events still go to the next record
        for child in gone:
            self.transport.discard(child)

    @staticmethod
    def _status(events: list[dict], frame_id: str | None) -> int | None:
        status = None
        for event in events:
            p = event["params"]
            if (event["method"] == "Network.responseReceived" and p.get("type") == "Document"
                    and (frame_id is None or p.get("frameId") == frame_id)):
                status = (p.get("response") or {}).get("status")
        return status

    def _screenshot(self, browser: Browser, record: PageRecord, dpr: float, *, metrics: bool = True) -> None:
        try:
            data = capture_screenshot(self.transport, browser.session, format="jpeg", quality=70)["data"]
        except (TimeoutError, RuntimeError, KeyError) as exc:
            if isinstance(exc, RuntimeError) and session_gone(exc):
                raise
            record["notes"].append(f"screenshot failed: {str(exc)[:120]}")
            return
        jpeg = base64.b64decode(data)
        try:
            if metrics:
                record["visual"] = visual_metrics(jpeg, dpr)
        except (OSError, ValueError) as exc:
            record["notes"].append(f"visual metrics failed: {str(exc)[:120]}")
        if self.store is not None and self.run_id:
            record["screenshot"] = self.store.write_bytes(self.run_id, f"shots/{record['page_id']}.jpg", jpeg)
