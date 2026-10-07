// Page-side performance probe, installed with Page.addScriptToEvaluateOnNewDocument before any page script runs.
// Top frame only, once per document. Exposes window.__jevVitals:
//   read({resources}) -> schemas.Vitals (times in ms from navigation start; null when not observed). Its ttfb is
//                        navigation.responseStart, the collector's fallback: the collector prefers CDP Network timing,
//                        which includes DevTools network throttling and redirects
//   mark()            -> high-resolution epoch ms (performance.timeOrigin + now), valid across documents
//   since(mark)       -> schemas.StepSince for everything observed after the mark (a newer document counts as a
//                        navigation, and all of it is after the mark; so does a back-forward cache restore)
//   quiet(ms)         -> true when no resource, LCP, layout-shift entry or DOM mutation arrived in the last ms
//   activity()        -> raw timestamps the collector's settle loop reads
// activation_start (read and activity) is navigation.activationStart: above 0 for a prerendered document that was
// activated later. Nothing here subtracts it: the collector does not time such documents.
// Spontaneous changes. Something is spontaneous when it happens more than 2 s after the latest mark() before it
// (before the first mark: after the load event; nothing before load is spontaneous, the page is still loading).
// Tickers: a node with 3 spontaneous DOM changes (a clock, a carousel) is a ticker from then on; its mutations count
// in mutations_total but not as a response to an action, not in first_response_ms and not as activity. The document
// containers (html, head, body) never become tickers: widgets append to them on their own, and so do the toasts and
// drawers that answer an action. Pollers: a URL (origin + path, any query) fetched spontaneously 3 times (a
// heartbeat, a chat long-poll, a session-replay upload) is a poller from then on; its requests count in
// requests_total only. Changes and requests within 2 s of an action are responses by construction, so repeated
// actions never turn their own targets or endpoints into tickers or pollers.
(() => {
  if (window.top !== window || window.__jevVitals) return;
  const P = performance, now = () => P.now(), CAP = 2000;
  const s = {
    fcp: null, lcp: null, lcpAt: null, lcpEl: null, lcpEntries: 0,
    cls: 0, win: 0, winFirst: -1, winLast: -1, postInput: 0, shiftAt: null, shifts: [],
    events: [], eventMax: null, longTasks: [], loaf: 0, resources: [], resourceAt: null,
    errors: [], navs: [], muts: [], mutAt: null, mutTotal: 0, ticks: new WeakMap(), tickers: new WeakSet(),
    tickerCount: 0, polls: new Map(), pollers: new Set(), marks: [], lastMark: null, restoredAt: null,
    loadAt: document.readyState === 'complete' ? P.now() : null,
    hiddenBeforeLoad: document.visibilityState !== 'visible', visibilityAtStart: document.visibilityState,
  };
  const push = (list, item) => { list.push(item); if (list.length > CAP) list.splice(0, list.length - CAP); };
  const describe = e => {
    if (!e || !e.tagName) return null;
    const cls = typeof e.className === 'string' ? e.className.trim().split(/\s+/)[0] : '';
    return (e.tagName.toLowerCase() + (e.id ? '#' + e.id : cls ? '.' + cls : '')).slice(0, 60);
  };
  const observe = (type, fn, extra) => {
    try {
      if (!(PerformanceObserver.supportedEntryTypes || []).includes(type)) return;
      new PerformanceObserver(list => list.getEntries().forEach(fn)).observe({type, buffered: true, ...extra});
    } catch (e) { /* unsupported entry type options */ }
  };
  observe('paint', e => { if (e.name === 'first-contentful-paint') s.fcp = e.startTime; });
  observe('largest-contentful-paint', e => {
    s.lcp = e.renderTime || e.loadTime || e.startTime; s.lcpEl = describe(e.element); s.lcpAt = now(); s.lcpEntries++;
  });
  observe('layout-shift', e => {
    s.shiftAt = now();
    push(s.shifts, [e.startTime, e.value, e.hadRecentInput ? 1 : 0]);
    if (e.hadRecentInput) { s.postInput += e.value; return; }
    if (s.winFirst >= 0 && e.startTime - s.winLast < 1000 && e.startTime - s.winFirst < 5000) s.win += e.value;
    else { s.win = e.value; s.winFirst = e.startTime; }
    s.winLast = e.startTime;
    s.cls = Math.max(s.cls, s.win);
  });
  const onEvent = e => {
    push(s.events, [e.startTime, e.duration]);
    s.eventMax = Math.max(s.eventMax || 0, e.duration);
  };
  observe('event', onEvent, {durationThreshold: 16});
  observe('first-input', onEvent);
  observe('longtask', e => push(s.longTasks, [e.startTime, e.duration]));
  observe('long-animation-frame', () => { s.loaf++; });
  const SPONTANEOUS_MS = 2000, TICKS = 3;
  // Spontaneous: more than 2 s after the load event and after the latest mark before t; nothing is while loading.
  const spontaneous = t => {
    if (s.loadAt === null || t < s.loadAt) return false;
    let ref = s.loadAt;
    for (let i = s.marks.length - 1; i >= 0; i--) if (s.marks[i] <= t) { ref = Math.max(ref, s.marks[i]); break; }
    return t - ref > SPONTANEOUS_MS;
  };
  const pathKey = name => { try { const u = new URL(name); return u.origin + u.pathname; } catch (e) { return String(name); } };
  observe('resource', e => {
    const key = pathKey(e.name);
    let poll = s.pollers.has(key);
    if (!poll && spontaneous(e.startTime)) {
      const n = (s.polls.get(key) || 0) + 1;
      s.polls.set(key, n);
      if (n >= TICKS) { s.pollers.add(key); poll = true; }
    }
    if (!poll) s.resourceAt = now();
    push(s.resources, {
      name: String(e.name).slice(0, 300), type: e.initiatorType, start: Math.round(e.startTime),
      duration: Math.round(e.duration), transfer: e.transferSize || 0, encoded: e.encodedBodySize || 0,
      status: e.responseStatus || 0, blocking: e.renderBlockingStatus || null, poll,
    });
  });
  observe('visibility-state', e => {
    const nav = P.getEntriesByType('navigation')[0];
    if (e.name === 'hidden' && !(nav && nav.loadEventEnd > 0 && e.startTime > nav.loadEventEnd)) s.hiddenBeforeLoad = true;
  });
  document.addEventListener('visibilitychange', () => {
    const nav = P.getEntriesByType('navigation')[0];
    if (document.visibilityState === 'hidden' && !(nav && nav.loadEventEnd > 0)) s.hiddenBeforeLoad = true;
  });
  window.addEventListener('error', e => { if (e instanceof ErrorEvent) push(s.errors, now()); });
  window.addEventListener('unhandledrejection', () => push(s.errors, now()));
  for (const name of ['pushState', 'replaceState']) {
    const original = history[name];
    if (typeof original !== 'function') continue;
    history[name] = function (...args) { push(s.navs, now()); return original.apply(this, args); };
  }
  window.addEventListener('popstate', () => push(s.navs, now()));
  window.addEventListener('hashchange', () => push(s.navs, now()));
  // A back-forward cache restore shows a document again without loading it; its clock kept running meanwhile.
  window.addEventListener('pageshow', e => { if (e.persisted) { s.restoredAt = now(); push(s.navs, s.restoredAt); } });
  window.addEventListener('load', () => { s.loadAt = now(); });
  const containers = n => n === document.documentElement || n === document.head || n === document.body;
  try {
    new MutationObserver(records => {
      const t = now(), late = spontaneous(t);
      let fresh = 0;
      for (const r of records) {
        if (s.tickers.has(r.target)) continue;
        if (late && !containers(r.target)) {
          const n = (s.ticks.get(r.target) || 0) + 1;
          s.ticks.set(r.target, n);
          if (n >= TICKS) { s.tickers.add(r.target); s.tickerCount++; continue; }
        }
        fresh++;
      }
      s.mutTotal += records.length;
      push(s.muts, [t, fresh, records.length]);
      if (fresh) s.mutAt = t;
    }).observe(document, {childList: true, subtree: true, attributes: true, characterData: true});
  } catch (e) { /* no document yet */ }

  const round = v => v === null || v === undefined ? null : Math.round(v * 10) / 10;
  const nav = () => P.getEntriesByType('navigation')[0] || null;
  const read = (options = {}) => {
    const n = nav(), loadEnd = n && n.loadEventEnd > 0 ? n.loadEventEnd : null;
    const start = s.fcp ?? 0, end = (loadEnd ?? now()) + 3000;
    let longTotal = 0, tbt = 0;
    for (const [t, d] of s.longTasks) {
      longTotal += d;
      if (t >= start && t <= end) tbt += Math.max(0, d - 50);
    }
    const out = {
      ttfb: n ? round(n.responseStart) : null,
      fcp: round(s.fcp), lcp: round(s.lcp), lcp_element: s.lcpEl,
      cls: Math.round(s.cls * 10000) / 10000, cls_post_input: Math.round(s.postInput * 10000) / 10000,
      long_tasks_ms: round(longTotal), tbt_approx: round(tbt), loaf_count: s.loaf,
      dcl_ms: n && n.domContentLoadedEventEnd > 0 ? round(n.domContentLoadedEventEnd) : null,
      load_ms: round(loadEnd),
      visibility_state_at_load: s.hiddenBeforeLoad ? 'hidden' : s.visibilityAtStart,
      event_timing_max_ms: round(s.eventMax),
      js_errors: s.errors.length, activation_start: n ? round(n.activationStart || 0) : null,
    };
    if (options.resources) {
      out.resources = s.resources.slice(0, 500);
      if (n) out.resources.unshift({name: String(n.name).slice(0, 300), type: 'navigation', start: 0,
        duration: Math.round(n.duration), transfer: n.transferSize || 0, encoded: n.encodedBodySize || 0,
        status: n.responseStatus || 0, blocking: null});
    }
    return out;
  };
  const mark = () => { s.lastMark = now(); push(s.marks, s.lastMark); return P.timeOrigin + s.lastMark; };
  const since = mark => {
    const t = mark - P.timeOrigin, newDocument = t < 0, after = x => x >= t;
    const times = [];
    let mutations = 0, total = 0;
    for (const [at, n, all] of s.muts) {
      if (!after(at)) continue;
      mutations += n;
      total += all;
      if (n) times.push(at);
    }
    const navTimes = s.navs.filter(after), errors = s.errors.filter(after);
    const fetched = s.resources.filter(r => after(r.start)), resources = fetched.filter(r => !r.poll);
    times.push(...navTimes, ...resources.map(r => r.start));
    let shifts = 0, eventMax = null;
    for (const [at, v, input] of s.shifts) if (after(at) && input) shifts += v;
    for (const [at, d] of s.events) if (after(at)) eventMax = Math.max(eventMax || 0, d);
    const first = newDocument ? -t : times.length ? Math.min(...times) - t : null;
    return {
      first_response_ms: first === null ? null : Math.round(first),
      mutations, mutations_total: total, navigations: navTimes.length + (newDocument ? 1 : 0),
      requests: resources.length + (newDocument ? 1 : 0), requests_total: fetched.length + (newDocument ? 1 : 0),
      errors: errors.length,
      shifts_post_input: Math.round(shifts * 10000) / 10000, event_timing_max_ms: round(eventMax),
    };
  };
  const activity = () => {
    const n = nav();
    return {
      now: now(), time_origin: P.timeOrigin, ready_state: document.readyState,
      load_end: n && n.loadEventEnd > 0 ? n.loadEventEnd : null, fcp: s.fcp, lcp_at: s.lcpAt,
      resource_at: s.resourceAt, shift_at: s.shiftAt, mutation_at: s.mutAt, mutations: s.mutTotal,
      tickers: s.tickerCount, pollers: s.pollers.size, load_at: s.loadAt, last_mark: s.lastMark,
      restored_at: s.restoredAt, activation_start: n ? n.activationStart || 0 : null,
      visibility: document.visibilityState,
    };
  };
  const quiet = ms => {
    const t = now() - ms;
    return [s.resourceAt, s.lcpAt, s.shiftAt, s.mutAt].every(at => at === null || at < t);
  };
  Object.defineProperty(window, '__jevVitals', {value: Object.freeze({read, mark, since, quiet, activity}),
    configurable: false, enumerable: false, writable: false});
})();
