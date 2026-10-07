// One read-only pass over the whole document (light DOM plus open shadow roots), evaluated as
// `(AUDIT_JS)(lexicon)` where lexicon is lexicon.lexicon_for(locale). Returns schemas.AuditPayload.
// Conventions: rects are page coordinates {x, y, w, h} in CSS px; above_fold means rect.y < innerHeight at scroll 0;
// shares and coverage are fractions 0..1; every list is capped; texts are visible text, whitespace-collapsed.
// Nothing here clicks, types or changes the page, and field values are never read (only quantities in cart rows).
// Words come from the lexicon; the only fixed tokens are formats (prices, times, dates) and generic markup names.
(lexicon) => {
  const T0 = performance.now();
  const LX = {};
  for (const [key, list] of Object.entries(lexicon || {})) {
    const ok = (list || []).filter(s => { try { new RegExp(s, 'i'); return true; } catch (e) { return false; } });
    if (ok.length) LX[key] = new RegExp(ok.map(s => '(?:' + s + ')').join('|'), 'i');
  }
  const hit = (key, t) => !!(t && LX[key] && LX[key].test(t));
  const found = (key, t) => (t && LX[key] ? t.match(LX[key]) : null);
  const source = key => LX[key] ? LX[key].source : '(?!)';
  const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
  const cut = (s, n) => { s = clean(s); return s.length > n ? s.slice(0, n).trim() : s; };
  const share = (part, all) => all ? Math.round(part / all * 1000) / 1000 : null;
  const VW = innerWidth, VH = innerHeight, SX = scrollX, SY = scrollY, DPR = devicePixelRatio || 1;
  const base = {url: location.href, title: cut(document.title, 300), lang: (document.documentElement?.getAttribute('lang') || '').trim(),
    viewport: {w: VW, h: VH, dpr: DPR, scroll_y: Math.round(SY), doc_h: document.documentElement?.scrollHeight || 0}};
  if (!document.body) return {...base, snippets: [], truncated: true};

  // ------------------------------------------------------------------ inventory and geometry
  const MAX_ELEMENTS = 40000, ALL = [], roots = [];
  let openRoots = 0, truncated = false;
  const walk = root => {
    roots.push(root);
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    for (let n = it.nextNode(); n; n = it.nextNode()) {
      if (ALL.length >= MAX_ELEMENTS) { truncated = true; return; }
      ALL.push(n);
      if (n.shadowRoot) { openRoots++; walk(n.shadowRoot); }
    }
  };
  walk(document.body);
  const parentOf = e => e.parentElement || (e.parentNode && e.parentNode.host) || null;
  const closest = (e, sel) => { for (let n = e; n; n = parentOf(n)) if (n.matches && n.matches(sel)) return n; return null; };
  const within = (e, c) => { for (let n = e; n; n = parentOf(n)) if (n === c) return true; return false; };
  const memo = fn => { const m = new Map(); return e => { if (!m.has(e)) m.set(e, fn(e)); return m.get(e); }; };
  const style = memo(e => getComputedStyle(e));
  const rectOf = memo(e => e.getBoundingClientRect());
  // Screen-reader-only text (a 1x1 px clipped box) is not what a shopper sees: 2 px is the smallest visible box, and
  // nothing inside a box clipped to nothing (clip: rect(0 0 0 0), clip-path: inset(50%)) or inside a box under 2 px
  // whose overflow is not visible (a collapsed panel) is visible either; a fixed box escapes its ancestors' clipping.
  const clippedBox = memo(e => {
    const st = style(e), r = rectOf(e);
    if (/absolute|fixed/.test(st.position) && /^rect/.test(st.clip || '')) {
      const v = (st.clip.match(/-?[\d.]+/g) || []).map(parseFloat);
      if (v.length === 4 && (v[1] - v[3] <= 1 || v[2] - v[0] <= 1)) return true;
    }
    if (/inset\(\s*(50|100)%/.test(st.clipPath || '')) return true;
    return (r.width < 2 || r.height < 2) && (st.overflowX !== 'visible' || st.overflowY !== 'visible') && st.display !== 'contents';
  });
  const srOnly = memo(e => !!e && e !== document.body && e !== document.documentElement &&
    (clippedBox(e) || (style(e).position !== 'fixed' && srOnly(parentOf(e)))));
  const visible = memo(e => {
    if (!e || !e.isConnected) return false;
    const r = rectOf(e);
    if (r.width < 2 || r.height < 2) return false;
    if (e.checkVisibility && !e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false;
    return !srOnly(e);
  });
  const box = e => { const r = rectOf(e); return {x: Math.round(r.left + SX), y: Math.round(r.top + SY), w: Math.round(r.width), h: Math.round(r.height)}; };
  const fold = e => rectOf(e).top + SY < VH;
  const area = e => { const r = rectOf(e); return Math.round(r.width * r.height); };
  const text = e => clean(e.innerText !== undefined ? e.innerText : e.textContent);
  // A <label> that wraps its control: the label's own words, not the options of the select inside it.
  const labelText = (l, control) => {
    if (!control || !l.contains(control)) return text(l);
    let out = '';
    const it = document.createTreeWalker(l, NodeFilter.SHOW_TEXT);
    for (let t = it.nextNode(); t; t = it.nextNode()) {
      const p = t.parentElement;
      if (p && !control.contains(p) && !p.closest('select,option,textarea,script,style') && visible(p)) out += ' ' + t.nodeValue;
    }
    return clean(out);
  };
  const name = memo(e => {
    if (!e) return '';
    const doc = e.getRootNode && e.getRootNode().getElementById ? e.getRootNode() : document;
    const by = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
      .map(id => doc.getElementById(id)).filter(Boolean).map(text).filter(Boolean).join(' ');
    if (by) return by;
    const aria = clean(e.getAttribute('aria-label'));
    if (aria) return aria;
    if (e.labels && e.labels.length) { const l = [...e.labels].map(x => labelText(x, e)).filter(Boolean).join(' '); if (l) return l; }
    if (e.tagName === 'INPUT' && ['button', 'submit', 'reset'].includes(e.type)) return clean(e.value);
    if (e.tagName === 'INPUT' && e.type === 'image') return clean(e.alt);
    if (e.tagName === 'IMG') return clean(e.alt);
    if (!['INPUT', 'SELECT', 'TEXTAREA'].includes(e.tagName)) {
      const t = text(e);
      if (t) return t;
      const g = e.querySelector('img[alt],[aria-label],svg title');
      if (g) return clean(g.getAttribute('alt') || g.getAttribute('aria-label') || g.textContent);
    }
    return clean(e.getAttribute('title')) || clean(e.getAttribute('placeholder')) || '';
  });
  const AREAS = [['header,[role="banner"]', 'header', true], ['nav,[role="navigation"],[role="menubar"]', 'nav', false],
    ['footer,[role="contentinfo"]', 'footer', true], ['aside,[role="complementary"]', 'aside', false]];
  const areaOf = memo(e => {
    for (const [sel, label, pageLevel] of AREAS) {
      const c = closest(e, sel);
      if (c && !(pageLevel && parentOf(c) && closest(parentOf(c), 'main,article,[role="main"]'))) return label;
    }
    return 'main';
  });

  // ------------------------------------------------------------------ text blocks
  const INLINE = new Set(['inline', 'contents']);
  const blockOf = memo(e => {
    for (let n = e; n; n = parentOf(n)) if (n === document.body || !INLINE.has(style(n).display)) return n;
    return document.body;
  });
  // The text of an element as the shopper reads it, for every quote (snippets, overlay and control texts): text nodes
  // whose holder is visible (never screen-reader-only or collapsed boxes), open shadow roots and slotted content in
  // render order, a space where innerText breaks a line (block boxes, <br>), CSS text-transform applied. onScreen:
  // also leave out boxes parked beside the viewport (a second layer slid off screen).
  const shownHolder = p => { while (p && style(p).display === 'contents') p = parentOf(p); return !!p && visible(p); };
  const transform = (p, v) => {
    const t = style(p).textTransform;
    return t === 'uppercase' ? v.toUpperCase() : t === 'lowercase' ? v.toLowerCase()
      : t === 'capitalize' ? v.replace(/(^|[\s(«"'])(\p{L})/gu, (m, a, b) => a + b.toUpperCase()) : v;
  };
  const visText = (root, onScreen = false) => {
    let out = '';
    const off = e => { if (!onScreen) return false; const r = rectOf(e); return r.width > 0 && (r.right <= 0 || r.left >= VW); };
    const rec = n => {
      const slotted = n.tagName === 'SLOT' ? n.assignedNodes({flatten: true}) : [];
      for (const c of n.shadowRoot ? n.shadowRoot.childNodes : slotted.length ? slotted : n.childNodes) {
        if (out.length > 4000) return;
        if (c.nodeType === 3) {
          const h = c.parentElement || (c.parentNode && c.parentNode.host);
          if (h && /\S/.test(c.nodeValue) && shownHolder(h) && !off(h)) out += transform(h, c.nodeValue);
          else if (h) out += c.nodeValue.replace(/\S+/g, '');  // keep a hidden word's surrounding spaces only
        } else if (c.nodeType === 1 && !/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(c.tagName)) {
          if (c.tagName === 'BR') { out += ' '; continue; }
          if (srOnly(c) || off(c)) continue;
          const block = !/^(inline|contents|ruby)/.test(style(c).display);  // innerText's line breaks
          if (block) out += ' ';
          rec(c);
          if (block) out += ' ';
        }
      }
    };
    if (root && root.nodeType === 1 && !srOnly(root)) rec(root);
    return clean(out);
  };
  const blockMap = new Map(), MAX_BLOCKS = 6000;
  const addText = t => {
    const v = t.nodeValue;
    if (!v || !/\S/.test(v)) return;
    const p = t.parentElement || (t.parentNode && t.parentNode.host);
    if (!p || p.closest('script,style,noscript,template,title') || !visible(p)) return;
    const b = blockOf(p);
    if (!blockMap.has(b)) blockMap.set(b, {parts: [], holders: new Set()});
    blockMap.get(b).parts.push(v);
    blockMap.get(b).holders.add(p);
  };
  for (const root of roots) {
    const it = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let t = it.nextNode(); t; t = it.nextNode()) {
      if (blockMap.size >= MAX_BLOCKS) { truncated = true; break; }
      addText(t);
    }
  }
  // Trust details (VAT id, address, contacts) sit in the footer, at the end of the document: read it even when the
  // walk above stopped early.
  if (blockMap.size >= MAX_BLOCKS) {
    for (const f of document.querySelectorAll('footer,[role="contentinfo"]')) {
      const it = document.createTreeWalker(f, NodeFilter.SHOW_TEXT);
      for (let t = it.nextNode(); t && blockMap.size < MAX_BLOCKS + 400; t = it.nextNode()) addText(t);
    }
  }
  const nested = new Set();
  for (const b of blockMap.keys()) for (let n = parentOf(b); n && !nested.has(n); n = parentOf(n)) nested.add(n);
  // A block that contains other blocks keeps its own inline text only, as runs: inline children join without a space
  // ("Gra<b>tis</b>" stays "Gratis"), a <br> is a word break, and a nested block ends a run (its text belongs to that
  // block), so every run is text the shopper reads in one piece. When that light-DOM reading misses some of the
  // block's text (shadow roots, slots), the parts join with spaces in one run.
  const inlineRuns = (el, v) => {
    const runs = [{text: '', holders: new Set()}];
    const rec = n => {
      for (const c of n.childNodes) {
        if (c.nodeType === 3) {
          if (c.parentElement && visible(c.parentElement)) { runs[runs.length - 1].text += c.nodeValue; runs[runs.length - 1].holders.add(c.parentElement); }
          continue;
        }
        if (c.nodeType !== 1 || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(c.tagName)) continue;
        if (c.tagName === 'BR') runs[runs.length - 1].text += ' ';
        else if (blockOf(c) === c) runs.push({text: '', holders: new Set()});
        else rec(c);
      }
    };
    rec(el);
    return letters(runs.map(r => r.text).join('')) === letters(v.parts.join('')) ? runs
      : [{text: v.parts.join(' '), holders: v.holders}];
  };
  const letters = t => t.replace(/\s+/g, '').length;
  // holders: the elements whose own text nodes make up the block (they may sit in an open shadow root);
  // link: the share of its letters that are link text (a row of footer links is navigation, not a statement)
  const linkShare = holders => {
    let all = 0, linked = 0;
    for (const h of holders) {
      const n = [...h.childNodes].filter(c => c.nodeType === 3).reduce((k, c) => k + c.nodeValue.replace(/\s+/g, '').length, 0);
      all += n;
      if (closest(h, 'a[href]')) linked += n;
    }
    return all ? linked / all : 0;
  };
  const blocks = [];
  for (const [el, v] of blockMap) {
    // innerText keeps CSS text-transform; it also holds clipped screen-reader text, which the runs leave out
    const shown = !nested.has(el) && text(el);
    const runs = shown && letters(shown) === letters(v.parts.join('')) ? [{text: shown, holders: v.holders}]
      : inlineRuns(el, v);
    for (const r of runs) {
      const t = cut(r.text, 2000);
      if (t) blocks.push({el, holders: [...r.holders], text: t, link: linkShare(r.holders)});
    }
  }
  const words = t => t.split(/\s+/).filter(w => /\p{L}/u.test(w));
  for (const b of blocks) { b.area = areaOf(b.el); b.words = words(b.text).length; }
  const h1 = ALL.filter(e => e.tagName === 'H1' && visible(e));

  // ------------------------------------------------------------------ colours and contrast
  const parseColor = c => {
    const m = c && c.match(/^rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[\s,/]+/).filter(Boolean).map(parseFloat);
    return p.length >= 3 ? [p[0], p[1], p[2], p.length > 3 ? p[3] : 1] : null;
  };
  const blend = (top, under) => [0, 1, 2].map(i => top[i] * top[3] + under[i] * (1 - top[3]));
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return Math.round((Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05) * 100) / 100; };
  const hex = c => c ? '#' + c.slice(0, 3).map(v => Math.round(v).toString(16).padStart(2, '0')).join('') : null;
  const background = memo(e => {
    const layers = [];
    let image = false;
    for (let n = e; n; n = parentOf(n)) {
      const st = style(n), c = parseColor(st.backgroundColor);
      if (st.backgroundImage && st.backgroundImage !== 'none') image = true;
      if (c && c[3] > 0) { layers.push(c); if (c[3] >= 0.99) break; }
    }
    let out = [255, 255, 255];
    for (let i = layers.length - 1; i >= 0; i--) out = blend(layers[i], out);
    return {rgb: out, image};
  });
  const textHolder = memo(e => {
    const it = document.createTreeWalker(e, NodeFilter.SHOW_TEXT);
    for (let t = it.nextNode(); t; t = it.nextNode()) if (/\S/.test(t.nodeValue) && t.parentElement) return t.parentElement;
    return e;
  });
  const paint = memo(e => {
    const holder = textHolder(e), bg = background(holder), fg = parseColor(style(holder).color);
    const own = parseColor(style(e).backgroundColor), ownImage = style(e).backgroundImage !== 'none';
    const parent = parentOf(e) ? background(parentOf(e)).rgb : [255, 255, 255];
    const fill = own && own[3] > 0 ? blend(own, parent) : null;
    const bgContrast = fill ? ratio(fill, parent) : 1;
    return {
      fg: hex(fg), bg: hex(bg.rgb), bg_image: bg.image, contrast: fg ? ratio(blend(fg, bg.rgb), bg.rgb) : null,
      bg_contrast: bgContrast, filled: !!((own && own[3] >= 0.5 && bgContrast >= 1.2) || ownImage),
      font_px: parseFloat(style(holder).fontSize) || null, font_weight: parseInt(style(holder).fontWeight, 10) || null,
    };
  });

  // ------------------------------------------------------------------ JSON-LD and meta
  const raw = [];
  let jsonldErrors = 0;
  for (const s of document.querySelectorAll('script[type="application/ld+json" i]')) {
    const src = s.textContent || '';
    let v;
    try { v = JSON.parse(src); } catch (e) {
      try {
        v = JSON.parse(src.replace(/^\s*(<!--|\/\/\s*<!\[CDATA\[)/, '').replace(/(-->|\/\/\s*\]\]>)\s*$/, '')
          .replace(/,\s*([}\]])/g, '$1').replace(/[\u0000-\u001f]+/g, ' '));
      } catch (e2) { jsonldErrors++; continue; }
    }
    const add = x => {
      if (Array.isArray(x)) x.forEach(add);
      else if (x && typeof x === 'object') {
        if (Array.isArray(x['@graph'])) { x['@graph'].forEach(add); if (!x['@type']) return; }
        raw.push(x);
      }
    };
    add(v);
  }
  // The payload keeps JSON-LD's types, keys and short values: long prose (descriptions, review bodies) and image
  // lists are dropped, and past JSONLD_BUDGET serialized characters an object keeps only its @type and keys.
  const PROSE = new Set(['description', 'reviewBody', 'articleBody', 'text', 'disambiguatingDescription']);
  const JSONLD_BUDGET = 30000;
  const trim = (v, d, k) => {
    if (d > 6 || PROSE.has(k)) return null;
    if (k === 'image' || k === 'photo') v = Array.isArray(v) ? v[0] : v;
    if (typeof v === 'string') return v.length > 300 ? v.slice(0, 300) : v;
    if (Array.isArray(v)) return v.slice(0, 12).map(x => trim(x, d + 1));
    if (v && typeof v === 'object') {
      const o = {};
      for (const [key, x] of Object.entries(v).slice(0, 40)) if (key !== '@graph') o[key] = trim(x, d + 1, key);
      return o;
    }
    return v;
  };
  let jsonldSize = 0;
  const jsonld = raw.slice(0, 20).map(o => {
    const t = trim(o, 0), size = JSON.stringify(t).length;
    if (jsonldSize + size <= JSONLD_BUDGET) { jsonldSize += size; return t; }
    const stub = {};
    for (const key of Object.keys(o).slice(0, 40)) if (key !== '@graph') stub[key] = key === '@type' ? trim(o[key], 1) : null;
    return stub;
  });
  const typesOf = o => { const t = o && o['@type']; return (Array.isArray(t) ? t : [t]).filter(Boolean).map(x => String(x).replace(/^.*[/#]/, '')); };
  const nodes = [];
  const visit = (x, d) => {
    if (d > 8 || nodes.length > 400) return;
    if (Array.isArray(x)) x.forEach(y => visit(y, d + 1));
    else if (x && typeof x === 'object') { nodes.push(x); Object.values(x).forEach(y => visit(y, d + 1)); }
  };
  raw.forEach(o => visit(o, 0));
  const typeCount = {};
  for (const n of nodes) for (const t of typesOf(n)) typeCount[t] = (typeCount[t] || 0) + 1;
  const ofType = t => nodes.filter(n => typesOf(n).includes(t));
  const num = v => { if (v === null || v === undefined || v === '') return null; const f = parseFloat(String(v).replace(/\s/g, '').replace(/,(\d{1,2})$/, '.$1').replace(/,/g, '')); return Number.isFinite(f) ? f : null; };
  const first = v => Array.isArray(v) ? v[0] : v;
  const product = ofType('Product')[0] || ofType('ProductGroup')[0] || null;
  let structured = null;
  if (product) {
    const offers = [].concat(product.offers || [], ...(product.hasVariant ? [].concat(product.hasVariant).map(v => v.offers || []) : []));
    const offer = offers.find(o => o && (o.price !== undefined || o.lowPrice !== undefined || o.priceSpecification)) || offers[0] || {};
    const spec = first(offer.priceSpecification) || {};
    const rating = product.aggregateRating || {};
    structured = {
      name: cut(first(product.name), 160), sku: cut(first(product.sku), 60),
      price: num(offer.price ?? offer.lowPrice ?? spec.price), currency: offer.priceCurrency || spec.priceCurrency || null,
      availability: offer.availability ? String(first(offer.availability)).replace(/^.*[/#]/, '') : null,
      rating: num(rating.ratingValue),
      review_count: num(rating.reviewCount ?? rating.ratingCount) ?? (product.review ? [].concat(product.review).length : null),
      has_return_policy: !!(product.hasMerchantReturnPolicy || offers.some(o => o && o.hasMerchantReturnPolicy)),
      has_shipping: !!(offers.some(o => o && o.shippingDetails) || product.shippingDetails), offers: offers.length,
    };
  }
  const itemList = ofType('ItemList')[0];
  const itemListCount = itemList ? (num(itemList.numberOfItems) ?? [].concat(itemList.itemListElement || []).length) : 0;
  const crumbList = ofType('BreadcrumbList')[0];
  const metaContent = sel => { const m = document.querySelector(sel); return m ? cut(m.getAttribute('content') || m.getAttribute('href'), 300) : null; };
  const meta = {
    og_type: metaContent('meta[property="og:type"]'), canonical: metaContent('link[rel="canonical"]'),
    og_price_amount: metaContent('meta[property="product:price:amount"],meta[property="og:price:amount"]'),
    og_price_currency: metaContent('meta[property="product:price:currency"],meta[property="og:price:currency"]'),
    description: cut(metaContent('meta[name="description"]'), 300), robots: metaContent('meta[name="robots"]'),
    viewport: metaContent('meta[name="viewport"]'), jsonld_types: typeCount, jsonld_errors: jsonldErrors,
    itemprop_price: metaContent('meta[itemprop="price"],[itemprop="price"][content]'),
    speculation_rules: !!document.querySelector('script[type="speculationrules" i]'),  // informational
  };

  // ------------------------------------------------------------------ overlays
  // A control's visible text, not its accessible name (aria-label, alt): what the shopper reads, and what snippets quote.
  const shownText = e => cut(e.labels && e.labels.length ? [...e.labels].map(l => l.contains(e) ? labelText(l, e) : visText(l)).filter(Boolean).join(' ')
    : e.tagName === 'INPUT' ? (['button', 'submit', 'reset'].includes(e.type) ? e.value : '') : visText(e), 80);
  const wordy = t => /\p{L}{2}/u.test(t || '');  // more than a glyph such as "×"
  const BTN = 'button,a[href],input[type="submit"],input[type="button"],input[type="image"],[role="button"]';
  const shadowHosts = ALL.filter(e => e.shadowRoot);
  // The controls (or fields) of a box, open shadow roots included.
  const inside = (c, sel) => shadowHosts.some(h => h === c || c.contains(h))
    ? ALL.filter(e => e.matches(sel) && e !== c && within(e, c)) : [...c.querySelectorAll(sel)];
  // A control of an overlay that the shopper can use on its first layer: in the viewport (vertically too, unless a
  // panel scrolls it into view), at least half of it left by every clipping ancestor up to the overlay, and not
  // covered by another part of the same overlay (a second layer stacked under the first). Covered by another overlay
  // or its backdrop is still usable once that one is closed.
  const reachable = (b, o) => {
    const r = rectOf(b);
    if (r.right <= 0 || r.left >= VW) return false;
    let x0 = r.left, y0 = r.top, x1 = r.right, y1 = r.bottom, scrolls = false;
    for (let n = parentOf(b); n; n = parentOf(n)) {
      const st = style(n);
      if (/auto|scroll/.test(st.overflowX + ' ' + st.overflowY)) scrolls = true;
      else if (st.overflowX !== 'visible' || st.overflowY !== 'visible') {
        const q = rectOf(n);
        x0 = Math.max(x0, q.left); y0 = Math.max(y0, q.top); x1 = Math.min(x1, q.right); y1 = Math.min(y1, q.bottom);
      }
      if (n === o || st.position === 'fixed') break;
    }
    if (!scrolls && (r.bottom <= 0 || r.top >= VH)) return false;
    if (Math.max(0, x1 - x0) * Math.max(0, y1 - y0) < 0.5 * r.width * r.height) return false;
    const cx = (Math.max(x0, 0) + Math.min(x1, VW)) / 2, cy = (Math.max(y0, 0) + Math.min(y1, VH)) / 2;
    if (cx < 0 || cy < 0 || cx >= VW || cy >= VH) return true;
    const scope = b.getRootNode && b.getRootNode().elementFromPoint ? b.getRootNode() : document;
    const top = scope.elementFromPoint(cx, cy);
    return !top || within(top, b) || within(b, top) || !within(top, o);
  };
  // The site's own chrome is no overlay: a sticky or fixed header, a fixed bar that holds the navigation or the search.
  const SITE_CHROME = 'nav,[role="navigation"],[role="search"],search,input[type="search"]';
  // Reject before accept: "Accetta solo i necessari" / "Accept only essential cookies" reject the optional cookies.
  const kindOf = label => hit('consent_reject', label) ? 'reject' : hit('consent_accept', label) ? 'accept'
    : hit('consent_manage', label) ? 'manage' : hit('decline', label) ? 'decline' : hit('close', label) ? 'close' : 'other';
  const candidates = [];
  for (const e of ALL) {
    const dialog = e.matches('dialog[open],[role="dialog"],[role="alertdialog"],[aria-modal="true"]');
    const pos = style(e).position;
    if (pos !== 'fixed' && pos !== 'sticky' && !dialog) continue;
    if (!dialog && (closest(e, 'header,[role="banner"]') || e.querySelector(SITE_CHROME))) continue;
    if (!visible(e)) continue;
    const r = rectOf(e);
    const ix = Math.max(0, Math.min(r.right, VW) - Math.max(r.left, 0)), iy = Math.max(0, Math.min(r.bottom, VH) - Math.max(r.top, 0));
    // A sticky box sits in the page flow: it is an overlay only as a consent bar stuck to the viewport.
    if (ix * iy > 0) candidates.push({el: e, coverage: ix * iy / (VW * VH), dialog, sticky: pos === 'sticky' && !dialog});
  }
  const outer = candidates.filter(c => !candidates.some(o => o !== c && within(c.el, o.el)));
  const probe = [[VW / 2, VH / 2], [VW / 2, VH / 3], [VW / 3, VH * 0.6]].map(([x, y]) => document.elementFromPoint(x, y));
  const overlays = [], backdrops = [];
  for (const c of outer) {
    const t = visText(c.el, true), controls = inside(c.el, BTN).filter(b => visible(b) && reachable(b, c.el));
    if (!t && !controls.length) { if (c.coverage >= 0.3 && !c.sticky) backdrops.push(c); continue; }
    // kind from the visible text first (the accessible name of "No grazie, preferisco pagare di più" may be "Chiudi")
    const buttons = controls.slice(0, 12).map(b => {
      const label = cut(name(b), 80), shown = shownText(b), byText = kindOf(shown);
      return {label, text: shown, kind: byText !== 'other' ? byText : kindOf(label), area: area(b), ...paint(b)};
    });
    const consentLike = hit('consent_banner', t) && buttons.some(b => ['accept', 'reject', 'manage'].includes(b.kind));
    if (!(c.dialog || c.coverage >= 0.15 || (consentLike && c.coverage >= 0.01)) || (c.sticky && !consentLike)) continue;
    const fields = inside(c.el, 'input,select,textarea').filter(visible);
    const kind = consentLike ? 'consent' : fields.some(f => f.type === 'password') ? 'login' : hit('age_gate', t) ? 'age'
      : hit('newsletter', t) ? 'newsletter' : hit('chat', t) ? 'chat' : hit('checkout', t) && hit('cart', t) ? 'cart' : 'promo';
    const labels = k => buttons.filter(b => b.kind === k).map(b => wordy(b.text) ? b.text : b.label);
    const maxArea = k => Math.max(0, ...buttons.filter(b => b.kind === k).map(b => b.area));
    overlays.push({
      kind, el: c.el, coverage: c.coverage, modal: c.dialog || c.el.matches('[aria-modal="true"],dialog'),
      has_close: buttons.some(b => b.kind === 'close' || (b.kind === 'decline' && !consentLike)),
      consent_like: consentLike, accept_labels: labels('accept'), reject_labels: labels('reject'),
      manage_labels: labels('manage'), decline_labels: labels('decline'), accept_area: maxArea('accept'),
      reject_area: maxArea('reject'), manage_area: maxArea('manage'), buttons,
      blocking: probe.some(p => p && within(p, c.el)), text_sample: cut(t, 300), fields: fields.length,
    });
  }
  for (const o of overlays) {
    if (!(o.modal || o.coverage >= 0.15)) continue;  // the same test as interrupting(), before backdrops count
    for (const b of backdrops) { o.coverage = Math.max(o.coverage, b.coverage); if (probe.some(p => p && within(p, b.el))) o.blocking = true; }
  }
  // An interruption takes the page away from the shopper: a modal, or an overlay over at least 15% of the viewport.
  // Smaller consent-like bars are still listed (the crawler needs their buttons) but do not interrupt.
  const interrupting = o => !!(o.modal || o.coverage >= 0.15);
  const inOverlay = memo(e => overlays.some(o => within(e, o.el)));
  for (const b of blocks) b.overlay = inOverlay(b.el);

  // ------------------------------------------------------------------ prices
  const CUR = '(?:€|US\\$|\\$|£|(?<![a-zà-ÿ])(?:euros?|EUR|USD|GBP|CHF)(?![a-zà-ÿ]))';  // not "Fleur 50 ml"
  // Thousands: dot, comma, (thin) space or apostrophe (CHF 1'299.00); decimals: 1-2 digits or a dash ("49,-").
  const NUM = '(?:\\d{1,3}(?:[.,\\u00a0\\u202f\\u2009 \'\u2019]\\d{3})+(?:[.,](?:\\d{1,2}|[-\u2013]{1,2}))?' +
    '|\\d+(?:[.,](?:\\d{1,2}|[-\u2013]{1,2}))?)';
  // "Euro 2024" before a year is a tournament, not a price.
  const PRICE = new RegExp('(?:(?!euros?\\s?(?:19|20)\\d\\d(?![\\d.,]))' + CUR + '\\s?' + NUM + '|' + NUM + '\\s?' + CUR + ')', 'i');
  const CURRENCY = {'€': 'EUR', eur: 'EUR', euro: 'EUR', euros: 'EUR', '$': 'USD', 'us$': 'USD', usd: 'USD', '£': 'GBP',
    gbp: 'GBP', chf: 'CHF'};
  const parseNum = s => {
    s = s.replace(/[\u00a0\u202f\u2009 '\u2019]/g, '').replace(/[.,][-\u2013]+$/, '');
    const c = s.lastIndexOf(','), d = s.lastIndexOf('.');
    let dec = -1;
    if (c >= 0 && d >= 0) dec = Math.max(c, d);
    else if (Math.max(c, d) >= 0 && s.length - Math.max(c, d) - 1 !== 3) dec = Math.max(c, d);
    const int = (dec >= 0 ? s.slice(0, dec) : s).replace(/[.,]/g, ''), frac = dec >= 0 ? s.slice(dec + 1) : '';
    const v = parseFloat(int + (frac ? '.' + frac : ''));
    return Number.isFinite(v) ? v : null;
  };
  const parsePrice = t => {
    const m = t && t.match(PRICE);
    if (!m) return null;
    const cur = m[0].match(new RegExp(CUR, 'i'));
    return {match: m[0], value: parseNum(m[0].match(new RegExp(NUM))[0]),
      currency: cur ? CURRENCY[cur[0].toLowerCase()] || cur[0].toUpperCase() : null};
  };
  const struck = e => {
    for (let n = e, i = 0; n && i < 4; n = parentOf(n), i++) {
      if (/^(DEL|S|STRIKE)$/.test(n.tagName) || /line-through/.test(style(n).textDecorationLine || '')) return true;
    }
    return false;
  };
  const priceEls = new Set();
  for (const b of blocks) {
    if (!/\d/.test(b.text) || !PRICE.test(b.text)) continue;
    for (const holder of b.holders) {
      if (!/\d|€|\$|£/.test(holder.textContent || '')) continue;
      for (let e = holder, i = 0; e && i < 5; e = parentOf(e), i++) {
        const tx = clean(e.textContent);
        if (tx.length > 60) break;
        if (PRICE.test(tx)) { if (visible(e)) priceEls.add(e); break; }
      }
    }
  }
  const ownText = e => clean([...e.childNodes].filter(n => n.nodeType === 3).map(n => n.nodeValue).join(' '));
  // "49<sup>90</sup> €": superscript cents are decimals, not more digits of the integer part.
  const priceString = e => {
    if (!e.querySelector('sup')) return clean(e.textContent);
    let out = '';
    const it = document.createTreeWalker(e, NodeFilter.SHOW_TEXT);
    for (let t = it.nextNode(); t; t = it.nextNode()) {
      const v = t.nodeValue || '';
      if (t.parentElement && t.parentElement.tagName === 'SUP' && /^\s*\d{1,2}\s*$/.test(v) && /\d$/.test(out.trimEnd())) {
        out = out.trimEnd() + ',' + v.trim();
      } else out += v;
    }
    return clean(out);
  };
  const priceText = new Map();  // leaf-most price elements; a container with its own price text keeps that text only
  const containers = new Set();
  for (const e of priceEls) for (let n = parentOf(e), i = 0; n && i < 8; n = parentOf(n), i++) if (priceEls.has(n)) containers.add(n);
  for (const e of priceEls) {
    if (!containers.has(e)) priceText.set(e, priceString(e));
    else if (PRICE.test(ownText(e))) priceText.set(e, ownText(e));
  }
  const leafPrices = [...priceText.keys()];
  const rowOf = memo(e => {
    let row = e;
    for (let n = parentOf(e), i = 0; n && i < 4; n = parentOf(n), i++) {
      const t = text(n);
      if (t.length > 160) break;
      row = n;
      if (t.length > (priceText.get(e) || clean(e.textContent)).length + 2) break;
    }
    return row;
  });
  const prices = [];
  for (const e of leafPrices.slice(0, 600)) {
    const t = priceText.get(e), p = parsePrice(t);
    if (!p || p.value === null) continue;
    const row = rowOf(e), rowText = row === e ? t : text(row), label = cut(rowText.replace(p.match, ' '), 100);
    const summaryRow = rowText.length <= 100 && !(row.querySelector && row.querySelector('a[href]'));
    const strike = struck(e), option = !!(closest(e, 'label') || (row.querySelector && row.querySelector('input[type="checkbox"],input[type="radio"]')));
    const kind = strike ? 'strike' : option ? 'option' : hit('free_shipping', label) ? 'threshold' : hit('installments', label + ' ' + t) ? 'installment'
      : hit('unit_price', t + ' ' + label) ? 'unit' : hit('lowest_price_30d', label) ? 'lowest30'
      : summaryRow && hit('subtotal', label) ? 'subtotal' : summaryRow && hit('total', label) ? 'total'
      : summaryRow && hit('shipping', label) ? 'shipping' : summaryRow && hit('fee', label) ? 'fee' : 'price';
    prices.push({el: e, row, text: cut(t, 60), value: p.value, currency: p.currency, strikethrough: strike, kind, label,
      rect: box(e), above_fold: fold(e), near_text: cut(text(blockOf(parentOf(e) || e)), 200),
      itemprop: !!closest(e, '[itemprop="price"]'), overlay: inOverlay(e), area: areaOf(e)});
  }

  // ------------------------------------------------------------------ calls to action
  const allButtons = ALL.filter(e => e.matches(BTN) && visible(e));
  const CTA_KEYS = ['add_to_cart', 'buy_now', 'checkout', 'pay_now', 'place_order', 'load_more', 'consent_accept',
    'consent_reject', 'newsletter', 'register', 'login', 'search', 'cart'];
  const PRIMARY_KEYS = new Set(['add_to_cart', 'buy_now', 'checkout']);
  // "Concludi ordine" on a same-site link into a checkout URL is the cart's way into the checkout (legacy WooCommerce
  // Italian), not a control that places an order: lexicon_hit "checkout". Buttons and href-less controls stay place_order.
  // Same site: the registrable domain, as CheckoutGuard compares it (www.shop.it and checkout.shop.it are one site).
  const siteOf = host => {
    host = String(host || '').toLowerCase().replace(/\.$/, '');
    if (!host.includes('.') || /^[\d.]+$|:/.test(host)) return host;
    const l = host.split('.');
    const n = /\.(myshopify\.com|github\.io|netlify\.app|vercel\.app|herokuapp\.com|wixsite\.com|pages\.dev|web\.app)$/.test(host)
      || (l.length > 2 && l[l.length - 1].length === 2 && /^(co|com|org|net|ac|gov|ne|or|ltd|plc|me|gv)$/.test(l[l.length - 2])) ? 3 : 2;
    return l.slice(-n).join('.');
  };
  const checkoutLink = e => {
    if (e.tagName !== 'A') return false;
    try {
      const u = new URL(e.getAttribute('href') || '', location.href), part = u.pathname + u.search;
      return /^https?:$/.test(u.protocol) && siteOf(u.hostname) === siteOf(location.hostname) &&
        (u.host !== location.host || u.pathname !== location.pathname) &&
        hit('checkout_url', part) && !hit('cart_url', part) && !hit('info_url', part);
    } catch (x) { return false; }
  };
  const ctaKey = memo(e => {
    const label = name(e);
    if (label.length > 60) return null;
    const key = CTA_KEYS.find(k => hit(k, label) && !(k === 'cart' && hit('add_to_cart', label))) || null;
    return key === 'place_order' && checkoutLink(e) ? 'checkout' : key;
  });
  const ctaOf = e => {
    const label = cut(name(e), 80), p = paint(e), a = area(e);
    return {el: e, label, lexicon_hit: ctaKey(e), tag: e.tagName.toLowerCase(), rect: box(e), above_fold: fold(e), area: a, ...p,
      primary_like: p.filled && a >= 900 && label.length >= 2 && label.length <= 40,
      disabled: e.matches(':disabled,[aria-disabled="true"]'), overlay: inOverlay(e)};
  };
  const ctaPool = [...allButtons.filter(e => ctaKey(e) && !inOverlay(e)), ...allButtons.filter(e => !ctaKey(e) && fold(e) && !inOverlay(e))]
    .slice(0, 150).map(ctaOf);
  const order = c => (c.lexicon_hit && PRIMARY_KEYS.has(c.lexicon_hit) ? 0 : c.lexicon_hit ? 1 : c.primary_like ? 2 : 3);
  const ctas = [...ctaPool].sort((a, b) => order(a) - order(b) || (b.above_fold - a.above_fold) || a.rect.y - b.rect.y).slice(0, 40);
  const best = list => list.filter(c => !c.disabled).sort((a, b) => (b.above_fold - a.above_fold) || b.area - a.area)[0] || list[0] || null;
  const ctaSummary = c => c ? {present: true, label: c.label, above_fold: c.above_fold, rect: c.rect, enabled: !c.disabled, tag: c.tag,
    contrast: c.contrast, area: c.area} : {present: false};

  // ------------------------------------------------------------------ product cards
  const sameDoc = href => href.split('#')[0] === location.href.split('#')[0];
  const linkOk = a => { const h = (a.getAttribute('href') || '').trim(); return !!h && !/^(#|javascript:|mailto:|tel:)/i.test(h); };
  const cardPrices = prices.filter(p => p.kind === 'price' && p.value > 0 && !p.overlay && p.area !== 'footer');
  const count = new Map();
  for (const p of cardPrices) for (let a = parentOf(p.el), d = 0; a && d < 14; a = parentOf(a), d++) count.set(a, (count.get(a) || 0) + 1);
  const groups = new Map();
  for (const p of cardPrices) {
    let card = p.el;
    for (let a = parentOf(p.el), d = 0; a && d < 14; card = a, a = parentOf(a), d++) {
      if ((count.get(a) || 0) >= 2) {
        if (!groups.has(a)) groups.set(a, new Set());
        groups.get(a).add(card);
        break;
      }
    }
  }
  const cardLink = memo(c => [...c.querySelectorAll('a[href]')].filter(a => linkOk(a) && !sameDoc(a.href))
    .sort((x, y) => name(y).length - name(x).length)[0] || (c.tagName === 'A' && linkOk(c) ? c : null));
  let cardGroups = [...groups.values()].map(set => [...set]).filter(list => {
    const tags = {};
    list.forEach(c => { tags[c.tagName] = (tags[c.tagName] || 0) + 1; });
    return list.length >= 3 && Math.max(...Object.values(tags)) >= list.length * 0.6 && list.filter(cardLink).length >= list.length * 0.6;
  });
  if (!cardGroups.length) {
    const parents = new Map();
    for (const img of ALL.filter(e => e.tagName === 'IMG' && visible(e) && !inOverlay(e) && areaOf(e) === 'main')) {
      let item = closest(img, 'a[href]') || img;
      for (let n = parentOf(item), d = 0; n && d < 4; item = n, n = parentOf(n), d++) {
        if (n.childElementCount >= 4) { if (!parents.has(n)) parents.set(n, new Set()); parents.get(n).add(item); break; }
      }
    }
    cardGroups = [...parents.values()].map(s => [...s]).filter(l => l.length >= 4 && l.filter(cardLink).length >= l.length * 0.75);
  }
  cardGroups.sort((a, b) => b.length - a.length);
  const cardsFlat = cardGroups.flat(), cardSet = new Set(cardsFlat);
  const inCard = memo(e => { for (let n = e; n; n = parentOf(n)) if (cardSet.has(n)) return true; return false; });
  const cardData = c => {
    const link = cardLink(c), heading = c.querySelector('h1,h2,h3,h4,h5,h6,[itemprop="name"]');
    const price = prices.find(p => p.kind === 'price' && within(p.el, c)), img = c.querySelector('img');
    return {title: cut(heading ? text(heading) : link ? name(link) : img ? img.alt : '', 120), href: link ? cut(link.href, 300) : null,
      price: price ? price.text : null, price_value: price ? price.value : null, image: !!img, above_fold: fold(c)};
  };
  const microdata = ALL.filter(e => /schema\.org\/(Product|ProductGroup)$/i.test((e.getAttribute('itemtype') || '').trim()));
  meta.microdata_products = microdata.length;
  meta.microdata_main = microdata.filter(e => !inCard(e)).length;  // a product page's own item, not its related cards
  const products = {
    cards_count: cardsFlat.length, main_group: cardGroups[0] ? cardGroups[0].length : 0,
    groups: cardGroups.slice(0, 5).map(g => ({count: g.length, above_fold: g.filter(fold).length})),
    cards: cardsFlat.slice(0, 40).map(cardData), itemlist_jsonld: itemListCount, jsonld_products: typeCount.Product || 0,
  };

  // ------------------------------------------------------------------ filters region (facets beside a listing)
  const choiceCount = new Map();
  for (const i of ALL.filter(e => e.matches('input[type="checkbox"],input[type="radio"],[role="checkbox"]'))) {
    for (let a = parentOf(i), d = 0; a && d < 12; a = parentOf(a), d++) choiceCount.set(a, (choiceCount.get(a) || 0) + 1);
  }
  const regionName = r => [clean(r.getAttribute('aria-label')), r.id, typeof r.className === 'string' ? r.className : '',
    clean((r.querySelector('h1,h2,h3,h4,legend,summary') || {}).textContent)].join(' ');
  const cardAncestors = new Set();
  for (const c of cardsFlat) for (let n = parentOf(c); n && !cardAncestors.has(n); n = parentOf(n)) cardAncestors.add(n);
  const regionOk = r => (choiceCount.get(r) || 0) >= 3 && !cardAncestors.has(r) && !h1.some(h => within(h, r)) &&
    r !== document.body && !r.matches('main,[role="main"]');
  const namedRegions = [...choiceCount.keys()].filter(r => regionOk(r) && (hit('filters', regionName(r)) || r.matches('aside,[role="complementary"]')));
  const filterRegion = namedRegions.filter(r => !namedRegions.some(o => o !== r && within(r, o)))[0] || null;

  // ------------------------------------------------------------------ fields
  const FIELD = 'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="reset"]):not([type="image"]),select,textarea';
  const allFields = ALL.filter(e => e.matches(FIELD));
  const labelled = e => !!(clean(e.getAttribute('aria-label')) || (e.getAttribute('aria-labelledby') || '').trim() ||
    (e.labels && [...e.labels].some(l => text(l))) || clean(e.getAttribute('title')));
  const searchScore = memo(e => {
    if (!e.matches('input:not([type]),input[type="text"],input[type="search"],[role="searchbox"],[role="combobox"]')) return 0;
    const form = e.form || closest(e, 'form');
    return (e.type === 'search' ? 3 : 0) + (e.getAttribute('role') === 'searchbox' ? 2 : 0) +
      (closest(e, '[role="search"],search') ? 2 : 0) + (/^(q|s|query|search|keywords?|k|term|searchterm)$/i.test(e.name || '') ? 2 : 0) +
      (hit('search', name(e) + ' ' + clean(e.getAttribute('placeholder'))) ? 2 : 0) +
      (form && hit('search', form.getAttribute('action') || '') ? 1 : 0) + (hit('search', (e.name || '') + ' ' + (e.id || '')) ? 1 : 0);
  });
  const isSearch = e => searchScore(e) >= 2;
  const fieldLabel = e => cut(name(e), 80);
  const tokens = e => (e.getAttribute('autocomplete') || '').toLowerCase().split(/\s+/).filter(Boolean);
  const AUTOFILL = /^(name|honorific-prefix|given-name|additional-name|family-name|honorific-suffix|nickname|username|new-password|current-password|one-time-code|organization-title|organization|street-address|address-line[123]|address-level[1-4]|country|country-name|postal-code|cc-[a-z-]+|transaction-[a-z]+|language|bday(-[a-z]+)?|sex|tel(-[a-z-]+)?|email|impp|url|photo)$/;
  const PERSONAL = /^(name|given-name|additional-name|family-name|street-address|address-line[123]|address-level[1-4]|country|country-name|postal-code|tel(-[a-z-]+)?|email|bday(-[a-z]+)?)$/;
  const visibleFields = allFields.filter(visible);
  const pageFields = visibleFields.filter(e => !e.disabled && !isSearch(e) && !inOverlay(e) &&
    !['header', 'nav', 'footer'].includes(areaOf(e)) && !(filterRegion && within(e, filterRegion)));
  const radioSeen = new Set();
  const counted = pageFields.filter(e => {
    if (e.type !== 'radio' || !e.name) return true;
    const key = (e.form ? e.form.id || 'form' : 'page') + ':' + e.name;
    if (radioSeen.has(key)) return false;
    radioSeen.add(key);
    return true;
  });
  const autofillable = counted.filter(e => !['checkbox', 'radio', 'range', 'color', 'file'].includes(e.type));
  const passwords = visibleFields.filter(e => e.type === 'password' && !['header', 'nav', 'footer'].includes(areaOf(e)) &&
    (!inOverlay(e) || overlays.some(o => (o.modal || o.blocking) && within(e, o.el))));
  const ccFields = allFields.filter(e => tokens(e).some(t => t.startsWith('cc-')) || hit('payment_field', fieldLabel(e) + ' ' + (e.name || '') + ' ' + (e.id || '')));
  const payFrames = ALL.filter(e => e.tagName === 'IFRAME' && /stripe|adyen|braintree|checkout\.com|paypal|nexi|worldpay|klarna|satispay|mollie|squareup|cardinal/i
    .test((e.getAttribute('src') || '') + ' ' + (e.getAttribute('title') || '') + ' ' + (e.name || '')));
  const shortTexts = blocks.filter(b => b.text.length <= 200 && !b.overlay);
  // A guest exit counts on the page, or inside the login dialog that asks for the password ("Accedi / Registrati /
  // Continua come ospite"); guest wording in another overlay (consent, newsletter) does not.
  const loginOverlays = overlays.filter(o => o.kind === 'login'), inLogin = e => loginOverlays.some(o => within(e, o.el));
  const guestOption = allButtons.some(b => (!inOverlay(b) || inLogin(b)) && hit('guest', name(b))) ||
    visibleFields.some(f => ['radio', 'checkbox'].includes(f.type) && hit('guest', fieldLabel(f))) || shortTexts.some(b => hit('guest', b.text)) ||
    blocks.some(b => b.overlay && b.text.length <= 200 && inLogin(b.el) && hit('guest', b.text));
  const gatePasswords = passwords.filter(e => !e.disabled && (e.required || e.getAttribute('aria-required') === 'true' ||
    !hit('optional', fieldLabel(e) + ' ' + clean(e.getAttribute('placeholder')))));
  // A gate: a required password in a blocking dialog, or a required password / gate wording on a page that offers no
  // other way to enter one's details (a personal-data field outside the form that holds the password). Invitations
  // to log in for speed ("Accedi per completare l'acquisto più velocemente", "Log in to check out faster"), link
  // texts and "Hai già un account?" prompts are not gate wording.
  const passwordForm = e => { const f = e.form || closest(e, 'form'); return !!(f && f.querySelector('input[type="password"]')); };
  const entryPath = pageFields.some(e => !passwordForm(e) && (e.type === 'email' || tokens(e).some(t => PERSONAL.test(t)) ||
    hit('personal_field', fieldLabel(e))));
  const gateText = shortTexts.some(b => hit('login_gate', b.text) && !hit('login_optional', b.text) &&
    !b.holders.every(h => closest(h, 'a[href],button')));
  const loginGate = gatePasswords.some(inOverlay) || ((gatePasswords.some(e => !inOverlay(e)) || gateText) && !entryPath);
  const forms = {
    fields_total: allFields.length, visible: counted.length, visible_all: visibleFields.length,
    required: counted.filter(e => e.required || e.getAttribute('aria-required') === 'true').length,
    autocomplete_share: share(autofillable.filter(e => tokens(e).some(t => AUTOFILL.test(t))).length, autofillable.length),
    labels_share: share(visibleFields.filter(labelled).length, visibleFields.length),
    cc_present: ccFields.length > 0 || payFrames.length > 0, payment_iframes: payFrames.length,
    password_present: passwords.length > 0, password_required: gatePasswords.length > 0, guest_option: guestOption,
    login_required: !guestOption && loginGate,
    address_fields: counted.filter(e => tokens(e).some(t => PERSONAL.test(t)) || hit('personal_field', fieldLabel(e))).length,
    search_fields: visibleFields.filter(isSearch).length,
    fields: counted.slice(0, 40).map(e => ({tag: e.tagName.toLowerCase(), type: e.type || null, name: cut(e.name || e.id, 40),
      autocomplete: cut(e.getAttribute('autocomplete'), 40) || null, label: fieldLabel(e), has_label: labelled(e),
      required: !!(e.required || e.getAttribute('aria-required') === 'true')})),
  };

  // ------------------------------------------------------------------ search and navigation
  const searchInputs = allFields.filter(isSearch).sort((a, b) => searchScore(b) - searchScore(a) || visible(b) - visible(a));
  const shown = searchInputs.find(visible);
  const search = shown ? {present: true, above_fold: fold(shown), width: Math.round(rectOf(shown).width),
    has_autocomplete_attr: !!(shown.getAttribute('aria-autocomplete') || shown.getAttribute('role') === 'combobox' ||
      shown.getAttribute('list') || shown.getAttribute('aria-controls') || shown.getAttribute('aria-owns')),
    rect: box(shown), label: fieldLabel(shown), hidden_inputs: searchInputs.length - 1, toggle: false}
    : {present: false, above_fold: false, width: 0, has_autocomplete_attr: false, rect: null, hidden_inputs: searchInputs.length,
      toggle: allButtons.some(b => fold(b) && hit('search', name(b)))};
  const sameSite = href => { try { return new URL(href, location.href).host === location.host; } catch (e) { return false; } };
  // URL lexicon patterns read the path and query only: a host such as "smartcart.example" says nothing.
  const urlPart = href => { try { const u = new URL(href, location.href); return u.pathname + u.search; } catch (e) { return ''; } };
  // Links come from the whole document even when the element inventory was truncated (policy and contact links sit
  // at the end of the page, in the footer).
  const anchors = truncated ? [...new Set([...ALL.filter(e => e.tagName === 'A'), ...document.querySelectorAll('a[href]')])]
    : ALL.filter(e => e.tagName === 'A');
  const links = anchors.filter(linkOk);
  const visibleLinks = links.filter(visible);
  const humanPath = href => { try { const u = new URL(href, location.href); return decodeURIComponent(u.pathname + ' ' + u.search).replace(/[-_/.?=&+]+/g, ' '); } catch (e) { return ''; } };
  const navLinks = links.filter(a => closest(a, 'nav,header,[role="navigation"],[role="menubar"],[role="menu"]'));
  const homeLink = href => { try { return /^\/?$|\/(index|home|default)\.(html?|php|aspx?)$/i.test(new URL(href).pathname); } catch (e) { return false; } };
  const pagerNav = a => { const n = closest(a, 'nav,[role="navigation"]'); const l = n ? clean(n.getAttribute('aria-label')) : ''; return hit('pagination', l) || hit('breadcrumb', l); };
  const categories = [];
  for (const a of navLinks) {
    const label = cut(name(a), 60), href = a.href;
    if (!label || label.split(' ').length > 5 || !sameSite(href) || sameDoc(href) || homeLink(href) || pagerNav(a) || hit('utility', label) ||
        hit('generic_link', label) || hit('pagination', label) || categories.some(c => c.href === cut(href, 300)) ||
        closest(a, 'footer,[role="contentinfo"]') || /^\d+$/.test(label)) continue;
    categories.push({label, href: cut(href, 300), visible: visible(a), url_hit: hit('category_url', urlPart(href))});
    if (categories.length >= 30) break;
  }
  const crumbsEl = ALL.find(e => e.matches('nav,ol,ul,div,p') && visible(e) && (hit('breadcrumb', clean(e.getAttribute('aria-label'))) ||
    /breadcrumb/i.test((e.getAttribute('itemtype') || '') + ' ' + (e.id || '') + ' ' + (typeof e.className === 'string' ? e.className : ''))));
  const crumbNodes = crumbsEl ? [...crumbsEl.querySelectorAll('li')] : [];
  const crumbItems = (crumbNodes.length ? crumbNodes : crumbsEl ? [...crumbsEl.querySelectorAll('a,span[itemprop="name"]')] : []).map(text).filter(Boolean);
  const generic = visibleLinks.filter(a => hit('generic_link', name(a)));
  const cartLink = links.find(a => !hit('add_to_cart', name(a)) && (hit('cart', name(a)) || hit('cart_url', urlPart(a.href))));
  const nav = {
    links: visibleLinks.length, nav_links: navLinks.length, categories,
    breadcrumbs: {present: !!(crumbsEl || crumbList), source: crumbsEl ? 'dom' : crumbList ? 'jsonld' : null,
      items: (crumbItems.length ? crumbItems : crumbList ? [].concat(crumbList.itemListElement || [])
        .map(i => clean(i.name || (i.item && i.item.name) || '')) : []).slice(0, 8).map(t => cut(t, 60))},
    generic_label_share: share(generic.length, visibleLinks.filter(a => name(a)).length),
    generic_examples: [...new Set(generic.map(a => cut(name(a), 40)))].slice(0, 5),
    cart_link: cartLink ? {present: true, href: cut(cartLink.href, 300), label: cut(name(cartLink), 60)} : {present: false},
  };

  // ------------------------------------------------------------------ listing controls
  const sortSelect = visibleFields.find(e => e.tagName === 'SELECT' && ([...e.options].filter(o => hit('sort', o.text)).length >= 2 || hit('sort', name(e))));
  const sortButton = allButtons.find(b => hit('sort', name(b)) && name(b).length <= 40);
  const facets = filterRegion ? (filterRegion.querySelectorAll('fieldset,details,[role="group"]').length ||
    filterRegion.querySelectorAll('h2,h3,h4,h5,legend,summary').length) : 0;
  const filterToggle = allButtons.find(b => hit('filters', name(b)) && name(b).length <= 40);
  const pagerLinks = visibleLinks.filter(a => /^\d+$/.test(name(a)));
  const pagination = allButtons.some(b => hit('load_more', name(b))) ? 'load_more'
    : (document.querySelector('link[rel="next"],a[rel="next"]') || ALL.some(e => e.matches('nav') && hit('pagination', clean(e.getAttribute('aria-label')))) ||
       visibleLinks.some(a => hit('pagination', name(a))) || pagerLinks.length >= 2) ? 'pagination' : 'none';
  const countBlock = blocks.find(b => b.text.length <= 120 && !b.overlay && hit('result_count', b.text));
  const filters = {
    controls: Math.max(facets, filterToggle ? 1 : 0), facets, toggle: !!filterToggle,
    inputs: filterRegion ? filterRegion.querySelectorAll('input,select').length : 0,
    sort: {present: !!(sortSelect || sortButton), kind: sortSelect ? 'select' : sortButton ? 'button' : null,
      options: sortSelect ? [...sortSelect.options].map(o => cut(o.text, 40)).slice(0, 10) : []},
    result_count_text: countBlock ? cut(countBlock.text, 120) : null,
    chips: filterRegion ? allButtons.filter(b => hit('remove', name(b)) && within(b, filterRegion)).length : 0, pagination,
  };

  // ------------------------------------------------------------------ product page
  const mainPrices = prices.filter(p => p.kind === 'price' && !inCard(p.el) && !p.overlay && !['footer', 'nav', 'header'].includes(p.area));
  const anchorY = h1[0] ? box(h1[0]).y : 0;
  const mainPrice = [...mainPrices].sort((a, b) => (b.above_fold - a.above_fold) || Math.abs(a.rect.y - anchorY) - Math.abs(b.rect.y - anchorY))[0] || null;
  const strikeNear = mainPrice ? prices.filter(p => p.kind === 'strike' && !inCard(p.el))
    .sort((a, b) => Math.abs(a.rect.y - mainPrice.rect.y) - Math.abs(b.rect.y - mainPrice.rect.y))[0] : null;
  const firstText = (key, extra) => {
    const pick = blocks.filter(b => !b.overlay && b.text.length <= 300 && !inCard(b.el) && ['main', 'aside', 'header'].includes(b.area) &&
      hit(key, b.text) && (!extra || extra(b)));
    const b = pick.find(x => x.area === 'main') || pick[0];
    return b ? cut(b.text, 200) : null;
  };
  const optionLike = e => e.matches('input[type="radio"],[role="radio"],button,[role="button"],[aria-pressed],label') && name(e).length <= 14;
  const variantGroups = [];
  for (const sel of visibleFields.filter(e => e.tagName === 'SELECT' && !e.disabled && !isSearch(e) && e !== sortSelect)) {
    const label = fieldLabel(sel) + ' ' + clean(parentOf(sel)?.firstElementChild?.textContent || '');
    if (hit('variant', label) && sel.options.length >= 2) variantGroups.push({label: cut(fieldLabel(sel), 60), kind: 'select',
      options: [...sel.options].map(o => cut(o.text, 30)).filter(Boolean).slice(0, 12)});
  }
  const hiddenRadioLabels = ALL.filter(e => e.matches('input[type="radio"]') && !visible(e) && e.labels).map(e => [...e.labels].find(visible)).filter(Boolean);
  const byParent = new Map();
  for (const e of [...visibleFields, ...allButtons, ...hiddenRadioLabels].filter(e => optionLike(e) && !inCard(e) && !inOverlay(e) && areaOf(e) === 'main')) {
    const p = parentOf(e);
    if (!byParent.has(p)) byParent.set(p, []);
    if (!byParent.get(p).includes(e)) byParent.get(p).push(e);
  }
  for (const [p, items] of byParent) {
    if (items.length < 2 || (filterRegion && within(p, filterRegion))) continue;
    const g = parentOf(p);
    const label = [clean(p.getAttribute('aria-label')), p.tagName === 'FIELDSET' ? clean(p.querySelector('legend')?.textContent) : '',
      cut(p.previousElementSibling?.textContent, 40), g && g.firstElementChild !== p ? cut(g.firstElementChild?.textContent, 40) : '']
      .find(t => t && hit('variant', t));
    if (label) variantGroups.push({label: cut(label, 60), kind: 'buttons', options: items.map(e => cut(name(e), 20)).slice(0, 12)});
  }
  let rating = null, reviewCount = null, ratingText = null, countText = null;
  for (const b of blocks.filter(x => !x.overlay && !inCard(x.el) && x.text.length <= 200 && hit('review', x.text) && /\d/.test(x.text))) {
    const scale = found('rating_scale', b.text);
    const r = b.text.match(/(\d(?:[.,]\d{1,2})?)\s*\/\s*5\b/) || (scale ? b.text.slice(0, scale.index).match(/(\d(?:[.,]\d{1,2})?)\s*$/) : null);
    if (r && rating === null) { const v = parseFloat(r[1].replace(',', '.')); if (v >= 0 && v <= 5) { rating = v; ratingText = cut(b.text, 160); } }
    for (const m of b.text.matchAll(/(\d[\d.,]*)\s*\)?\s+([^\s\d]+(?:\s+[^\s\d]+)?)/g)) {
      if (reviewCount === null && hit('review', m[2]) && !/^\d[.,]\d$/.test(m[1])) { reviewCount = parseInt(m[1].replace(/[.,]/g, ''), 10); countText = cut(b.text, 160); }
    }
  }
  const microRating = document.querySelector('[itemprop="ratingValue"]'), microCount = document.querySelector('[itemprop="reviewCount"],[itemprop="ratingCount"]');
  const reviews = structured && (structured.rating !== null || structured.review_count !== null)
    ? {rating: structured.rating, count: structured.review_count, rating_text: ratingText, count_text: countText, source: 'jsonld'}
    : microRating ? {rating: num(microRating.getAttribute('content') || microRating.textContent),
      count: num(microCount && (microCount.getAttribute('content') || microCount.textContent)), rating_text: ratingText, count_text: countText, source: 'microdata'}
    : {rating, count: reviewCount, rating_text: ratingText, count_text: countText, source: rating !== null || reviewCount !== null ? 'text' : null};
  // The product's own add-to-cart, not those of a related-products grid (cards); a listing has only card buttons.
  const atcList = ctaPool.filter(c => c.lexicon_hit === 'add_to_cart'), atcOwn = atcList.filter(c => !inCard(c.el));
  const atcMain = atcOwn.length ? atcOwn : atcList;
  const gallery = ALL.filter(e => e.tagName === 'IMG' && visible(e) && !inCard(e) && areaOf(e) === 'main' && rectOf(e).width >= 150 && rectOf(e).height >= 150);
  const pdp = {
    title: h1[0] ? cut(text(h1[0]), 160) : null, add_to_cart: ctaSummary(best(atcMain)), add_to_cart_count: atcMain.length,
    add_to_cart_all: atcList.length,
    buy_now: ctaSummary(best(ctaPool.filter(c => c.lexicon_hit === 'buy_now'))),
    price: mainPrice ? {text: mainPrice.text, value: mainPrice.value, currency: mainPrice.currency, above_fold: mainPrice.above_fold,
      rect: mainPrice.rect, itemprop: mainPrice.itemprop} : null,
    strike_price: strikeNear ? {text: strikeNear.text, value: strikeNear.value} : null,
    stock_text: firstText('stock'), delivery_text: firstText('delivery'),
    shipping_text: firstText('shipping', b => /\d/.test(b.text) || hit('free_shipping', b.text)) || firstText('shipping'),
    returns_text: firstText('returns'), images: gallery.length,
    zoom: gallery.some(e => /zoom/.test(style(e).cursor + ' ' + style(parentOf(e) || e).cursor)) || allButtons.some(b => hit('zoom', name(b))),
    variant_selector: variantGroups.some(g => g.kind === 'buttons') ? 'buttons' : variantGroups.length ? 'select' : 'none',
    variants: variantGroups.reduce((n, g) => n + g.options.length, 0), variant_groups: variantGroups.slice(0, 4), reviews, structured,
  };

  // ------------------------------------------------------------------ cart
  const qtyControls = visibleFields.filter(e => !inCard(e) && !isSearch(e) && (e.type === 'number' || e.getAttribute('role') === 'spinbutton' ||
    /qty|quant/i.test((e.name || '') + ' ' + (e.id || '')) || hit('quantity', fieldLabel(e)) ||
    (e.tagName === 'SELECT' && e.options.length > 1 && [...e.options].every(o => /^\s*\d+\s*$/.test(o.text)))));
  const removeControls = allButtons.filter(b => !inOverlay(b) && hit('remove', name(b)) && !(filterRegion && within(b, filterRegion)));
  const rows = [];
  for (const ctl of [...qtyControls, ...removeControls].slice(0, 60)) {
    const own = name(ctl);
    for (let n = parentOf(ctl), d = 0; n && d < 8; n = parentOf(n), d++) {
      const rest = text(n).replace(own, ' ').replace(PRICE, ' ').replace(/[\d\s.,€$£%:x×()-]/g, '');
      if (prices.some(p => p.kind !== 'strike' && within(p.el, n)) && rest.length >= 4) {
        if (!rows.includes(n)) rows.push(n);
        break;
      }
    }
  }
  const atcEls = atcList.map(c => c.el);
  const lineRows = rows.filter(r => !rows.some(o => o !== r && within(o, r)) && !atcEls.some(a => within(a, r)));
  const qtyText = t => { const m = found('quantity', t); const after = m ? t.slice(m.index + m[0].length).match(/^\W{0,3}(\d+)/) : t.match(/[x×]\s*(\d+)\b/); return after ? parseInt(after[1], 10) : null; };
  const lineItems = lineRows.slice(0, 20).map(r => {
    // the product link: not the remove link, which comes first in a WooCommerce row ("×", aria-label "Rimuovi ...")
    const link = [...r.querySelectorAll('a[href]')].find(a => !removeControls.includes(a) && !hit('remove', name(a)) && wordy(name(a)));
    const heading = r.querySelector('h2,h3,h4,h5,[itemprop="name"]');
    const rowPrices = prices.filter(p => p.kind !== 'strike' && within(p.el, r)), qty = qtyControls.find(q => within(q, r));
    const fallback = blocks.filter(b => within(b.el, r) && !PRICE.test(b.text) && b.words >= 1).sort((a, b) => b.text.length - a.text.length)[0];
    const title = cut(heading ? text(heading) : link && name(link) ? name(link) : fallback ? fallback.text : '', 120);
    const q = qty ? parseInt(qty.value, 10) : qtyText(text(r));
    const last = rowPrices[rowPrices.length - 1];
    return {title, qty: Number.isFinite(q) ? q : null, price: last ? last.text : null, price_value: last ? last.value : null,
      removable: removeControls.some(b => within(b, r)), qty_editable: !!qty, addon: hit('addon', title), row_text: cut(text(r), 200)};
  });
  const summaryPrices = prices.filter(p => !p.overlay && !inCard(p.el) && !lineRows.some(r => within(p.el, r)));
  const lastOf = kind => summaryPrices.filter(p => p.kind === kind).slice(-1)[0] || null;
  const subtotal = lastOf('subtotal'), total = lastOf('total'), shipRow = lastOf('shipping');
  const freeShip = blocks.filter(b => !b.overlay && !inCard(b.el) && b.text.length <= 40 && hit('shipping', b.text))
    .map(b => ({text: text(rowOf(b.el))})).find(r => r.text.length <= 120 && hit('free_shipping', r.text) && !/\d/.test(r.text));
  const checkboxes = ALL.filter(e => e.matches('input[type="checkbox"],[role="checkbox"]') && !inOverlay(e) && !(filterRegion && within(e, filterRegion)) &&
    (visible(e) || (e.labels && [...e.labels].some(visible))));
  const isChecked = e => e.tagName === 'INPUT' ? e.checked : e.getAttribute('aria-checked') === 'true';
  const paidOption = e => {
    const label = fieldLabel(e);
    let row = rowOf(e), rowText = row ? text(row) : label, p = parsePrice(label) || parsePrice(rowText);
    const line = p ? null : closest(e, 'tr,li,[role="row"]');  // the price in a sibling table cell
    if (line && text(line).length <= 200) { row = line; rowText = text(line); p = parsePrice(rowText); }
    return {label: cut(label || rowText, 160), price_text: p ? p.match : null, price_value: p ? p.value : null, checked: isChecked(e),
      addon: hit('addon', label + ' ' + rowText)};
  };
  const options = checkboxes.map(paidOption).filter(o => o.price_value !== null && o.price_value > 0);
  const fees = summaryPrices.filter(p => ['shipping', 'fee'].includes(p.kind)).slice(0, 10)
    .map(p => ({label: p.label, price_text: p.text, value: p.value, row_text: cut(text(p.row), 200)}));
  const cart = {
    line_items: lineItems, subtotal_text: subtotal ? cut(text(subtotal.row), 120) : null, subtotal_value: subtotal ? subtotal.value : null,
    shipping_text: shipRow ? cut(text(shipRow.row), 120) : freeShip ? cut(freeShip.text, 120) : null,
    shipping_value: shipRow ? shipRow.value : freeShip ? 0 : null,
    total_text: total ? cut(text(total.row), 120) : null, total_value: total ? total.value : null,
    checkout_cta: ctaSummary(best(ctaPool.filter(c => c.lexicon_hit === 'checkout'))),
    editable: lineItems.some(i => i.qty_editable || i.removable),
    prechecked_paid: options.filter(o => o.checked).slice(0, 10), paid_options: options.filter(o => !o.checked).slice(0, 10),
    fees, empty: blocks.some(b => !b.overlay && b.text.length <= 200 && hit('empty_cart', b.text)),
  };

  // ------------------------------------------------------------------ trust
  const pageBlocks = blocks.filter(b => !b.overlay);
  const emailRe = /[\w.+-]+@[\w-]+(?:\.[\w-]+)+/, phoneRe = /(?:\+\d{1,3}[\s.]?)?(?:\(?\d{2,4}\)?[\s./-]?){2,4}\d{2,4}/;
  const mailto = ALL.find(e => e.tagName === 'A' && /^mailto:/i.test(e.getAttribute('href') || ''));
  const telLink = ALL.find(e => e.tagName === 'A' && /^tel:/i.test(e.getAttribute('href') || ''));
  const emailBlock = pageBlocks.find(b => emailRe.test(b.text));
  const phoneBlock = pageBlocks.find(b => hit('phone', b.text) && (b.text.match(phoneRe) || [''])[0].replace(/\D/g, '').length >= 6);
  const addressBlock = pageBlocks.find(b => b.text.length <= 300 && hit('address', b.text));
  const legal = pageBlocks.map(b => found('legal_id', b.text)).find(Boolean);
  const policy = {};
  for (const key of ['returns', 'shipping', 'privacy', 'terms', 'contact']) {
    const a = links.find(l => { const label = cut(name(l), 80); return label.length <= 60 && !hit('generic_link', label) && (hit(key, label) || hit(key, humanPath(l.href))); });
    policy[key] = a ? {label: cut(name(a), 60), href: cut(a.href, 300)} : null;
  }
  const brandTexts = [];
  for (const e of ALL) {
    if (e.tagName === 'IMG') brandTexts.push([e.getAttribute('alt'), e.getAttribute('title'), (e.getAttribute('src') || '').split(/[/?#]/).slice(-2).join(' ')].join(' '));
    else if (e.tagName.toLowerCase() === 'svg') brandTexts.push((e.getAttribute('aria-label') || '') + ' ' + clean(e.querySelector('title')?.textContent));
    else if (e.hasAttribute('aria-label') || e.hasAttribute('title')) brandTexts.push((e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('title') || ''));
    if (typeof e.className === 'string' && /pay|card|cc-|logo|brand|icon/i.test(e.className)) brandTexts.push(e.className.replace(/[-_]/g, ' '));
  }
  for (const b of blocks) {  // short labels such as "Visa", not product names ("Caffè Maestro") in cards or headings
    if (b.text.length <= 40 && !inCard(b.el) && !b.holders.every(h => closest(h, 'h1,h2,h3,h4,h5,h6,a[href]'))) brandTexts.push(b.text);
  }
  const brands = [];
  for (const src of lexicon.payment_brands || []) {
    let re;
    try { re = new RegExp(src, 'i'); } catch (e) { continue; }
    const m = brandTexts.map(t => t && t.match(re)).find(Boolean);
    if (m) brands.push(m[0].toLowerCase().replace(/[^a-z]/g, ''));
  }
  const trust = {
    https: location.protocol === 'https:',
    contact: {email: !!(mailto || emailBlock), phone: !!(telLink || phoneBlock), address: !!addressBlock,
      samples: [emailBlock && cut(emailBlock.text.match(emailRe)[0], 80), phoneBlock && cut(phoneBlock.text, 80),
        addressBlock && cut(addressBlock.text, 120)].filter(Boolean)},
    vat_id: legal ? cut(legal[0], 60) : null, policy_links: policy,
    policy_count: ['returns', 'shipping', 'privacy', 'terms'].filter(k => policy[k]).length,
    payment_logos: [...new Set(brands)],
    badges: [...new Set(brandTexts.filter(t => t && t.length <= 120 && hit('trust_badge', t)).map(t => cut(found('trust_badge', t)[0], 40)))].slice(0, 10),
  };

  // ------------------------------------------------------------------ persuasion
  const TIME = new RegExp('(?:(?<d>\\d{1,3})\\s*(?:' + source('day_unit') + ')\\.?\\s*:?\\s*)?(?<h>\\d{1,2})\\s*[:hH]\\s*(?<m>\\d{2})\\s*[:mM]\\s*(?<s>\\d{2})(?!\\d)', 'i');
  const countdowns = [];
  for (const b of blocks) {
    if (!/\d\s*[:hH]\s*\d/.test(b.text)) continue;
    for (const holder of b.holders) {
      if (!/\d/.test(holder.textContent || '')) continue;
      for (let e = holder, i = 0; e && i < 4; e = parentOf(e), i++) {
        const tx = clean(e.textContent);
        if (tx.length > 60) break;
        const m = tx.match(TIME);
        if (m && visible(e)) {
          if (!countdowns.some(c => within(e, c.el) || within(c.el, e))) {
            const g = m.groups;
            countdowns.push({el: e, remaining_s: (+g.d || 0) * 86400 + (+g.h) * 3600 + (+g.m) * 60 + (+g.s)});
          }
          break;
        }
      }
    }
  }
  const containerText = e => { let c = e; for (let n = parentOf(e), i = 0; n && i < 3; n = parentOf(n), i++) { if (text(n).length > 220) break; c = n; } return cut(text(c), 200); };
  const DATE = /\b\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\b|\b\d{1,2}[:.]\d{2}\b/;
  const persuasion = {scarcity: [], urgency: [], reciprocity: [], authority: [], free_shipping_threshold: [], lowest_price_30d: [], vat_statement: []};
  const seen = new Set();
  const add = (key, item) => { const k = key + ':' + item.text; if (persuasion[key].length < 10 && !seen.has(k)) { seen.add(k); persuasion[key].push(item); } };
  for (const c of countdowns.slice(0, 5)) add('urgency', {text: containerText(c.el), countdown: true, remaining_s: c.remaining_s, deadline: true});
  for (const b of blocks) {
    if (b.text.length > 400) continue;
    const t = cut(b.text, 200), scarce = found('scarcity', b.text), fs = found('free_shipping', b.text);
    if (scarce) { const n = (scarce[0].match(/\d+/) || [])[0]; add('scarcity', {text: t, number: n ? parseInt(n, 10) : null}); }
    if (hit('urgency', b.text) && !countdowns.some(c => within(c.el, b.el))) add('urgency', {text: t, countdown: false, remaining_s: null, deadline: DATE.test(b.text)});
    if (hit('reciprocity', fs ? b.text.replace(fs[0], ' ') : b.text)) add('reciprocity', {text: t});
    if (hit('authority', b.text)) add('authority', {text: t});
    if (fs) { const p = parsePrice(b.text.slice(fs.index)); add('free_shipping_threshold', {text: t, value: p ? p.value : null}); }
    if (hit('lowest_price_30d', b.text)) { const p = parsePrice(b.text); add('lowest_price_30d', {text: t, value: p ? p.value : null}); }
    if (hit('vat', b.text)) add('vat_statement', {text: t});
  }

  // ------------------------------------------------------------------ images, targets, accessibility
  const imgs = ALL.filter(e => e.tagName === 'IMG'), shownImgs = imgs.filter(visible);
  const oversized = shownImgs.filter(i => {
    const w = rectOf(i).width, src = i.currentSrc || i.src || '';
    return i.complete && i.naturalWidth > 0 && w >= 1 && !/\.svg(\?|#|$)|^data:image\/svg/i.test(src) && i.naturalWidth >= 2 * w * DPR;
  });
  const decorative = i => ['presentation', 'none'].includes(i.getAttribute('role')) || i.getAttribute('aria-hidden') === 'true';
  const missingAlt = shownImgs.filter(i => !i.hasAttribute('alt') && !decorative(i)).length;
  const images = {count: shownImgs.length, total: imgs.length, oversized_count: oversized.length,
    oversized: oversized.slice(0, 20).map(i => ({src: cut(i.currentSrc || i.src, 160), natural_w: i.naturalWidth, display_w: Math.round(rectOf(i).width)})),
    lazy_share: share(imgs.filter(i => i.loading === 'lazy').length, imgs.length), missing_alt: missingAlt};
  const TARGET = 'a[href],button,input:not([type="hidden"]),select,textarea,summary,[role="button"],[role="link"],[role="checkbox"],[role="radio"],[role="switch"],[role="tab"],[role="menuitem"],[role="option"]';
  const inlineLink = e => e.tagName === 'A' && style(e).display === 'inline' && text(blockOf(e)).length > text(e).length + 10;
  const targets = ALL.filter(e => e.matches(TARGET) && visible(e) && !inlineLink(e)).slice(0, 1500).map(e => {
    const r = rectOf(e);
    return {w: r.width, h: r.height, cx: r.left + r.width / 2 + SX, cy: r.top + r.height / 2 + SY, x0: r.left + SX, y0: r.top + SY};
  });
  const small = t => t.w < 24 || t.h < 24;
  const distRect = (t, o) => Math.hypot(Math.max(o.x0 - t.cx, 0, t.cx - (o.x0 + o.w)), Math.max(o.y0 - t.cy, 0, t.cy - (o.y0 + o.h)));
  let lt24 = 0;
  for (const t of targets.filter(small).slice(0, 400)) {
    if (targets.some(o => o !== t && (small(o) ? Math.hypot(o.cx - t.cx, o.cy - t.cy) < 24 : distRect(t, o) < 12))) lt24++;
  }
  const targetSizes = {interactive: targets.length, lt24, lt24_raw: targets.filter(small).length,
    lt44: targets.filter(t => t.w < 44 || t.h < 44).length, lt48: targets.filter(t => t.w < 48 || t.h < 48).length};
  const a11y = {
    img_missing_alt: missingAlt, inputs_missing_label: visibleFields.filter(e => !labelled(e)).length, lang_missing: !base.lang,
    iframes: ALL.filter(e => e.tagName === 'IFRAME').length, shadow_roots_open: openRoots,
    shadow_roots_closed: ALL.filter(e => e.tagName.includes('-') && !e.shadowRoot && !e.childElementCount && visible(e)).length,
    buttons_missing_name: allButtons.filter(b => !name(b)).length,
  };

  // ------------------------------------------------------------------ document and readability
  const headings = {};
  for (const e of ALL) if (/^H[1-6]$/.test(e.tagName) && visible(e)) headings[e.tagName.toLowerCase()] = (headings[e.tagName.toLowerCase()] || 0) + 1;
  // Prose: running text of at least 8 words outside product cards (shorter fragments are labels and captions).
  const prose = blocks.filter(b => b.area === 'main' && !b.overlay && b.words >= 8 && !inCard(b.el) &&
    !/^(BUTTON|A|LABEL|OPTION|SELECT|TH)$/.test(b.el.tagName));
  let wordCount = 0, sentenceCount = 0, letterCount = 0;
  for (const b of prose) {
    const w = words(b.text);
    wordCount += w.length;
    letterCount += w.reduce((n, x) => n + (x.match(/\p{L}/gu) || []).length, 0);
    sentenceCount += (b.text.match(/[.!?…]+(?=\s|$)/g) || []).length || 1;
  }
  const doc = {
    h1s: h1.slice(0, 5).map(e => cut(text(e), 160)), headings,
    outline: ALL.filter(e => /^H[1-3]$/.test(e.tagName) && visible(e)).slice(0, 30).map(e => ({level: +e.tagName[1], text: cut(text(e), 100)})),
    word_count: wordCount, sentence_count: sentenceCount, letter_count: letterCount, prose_blocks: prose.length,
    text_sample: cut(blocks.map(b => b.text).join(' '), 1000), element_count: ALL.length, text_blocks: blocks.length,
    frames: [...new Set(ALL.filter(e => e.tagName === 'IFRAME').map(e => { try { return new URL(e.src, location.href).host; } catch (x) { return ''; } }).filter(Boolean))].slice(0, 10),
  };

  // ------------------------------------------------------------------ snippets for closed-label judgments
  const snippets = [];
  const locator = e => (e.tagName.toLowerCase() + (areaOf(e) !== 'main' ? ' in ' + areaOf(e) : '') + (inOverlay(e) ? ' (overlay)' : '')).slice(0, 120);
  const snip = (kind, t, loc) => {
    t = clean(t);
    if (t.length > 600) { const s = t.slice(0, 600), sp = s.lastIndexOf(' '); t = (sp > 500 ? s.slice(0, sp) : s).trim(); }
    if (!t || snippets.length >= 40 || snippets.some(s => s.kind === kind && s.text === t)) return;
    snippets.push({snippet_id: 's' + (snippets.length + 1), kind, text: t, locator: loc});
  };
  const reviewHeads = ALL.filter(e => /^H[2-4]$/.test(e.tagName) && visible(e) && hit('testimonial_heading', text(e)));
  // A testimonial heading's section: its following siblings up to the next heading of the same or a higher level (or
  // a box that holds one). A heading alone in a title box (or with a short rating line) takes the box's following
  // siblings instead. Never the whole parent: on a flat page that is the description, the policies and the buttons.
  const level = e => +e.tagName[1];
  const headsSection = (n, lv) => (/^H[1-6]$/.test(n.tagName) && level(n) <= lv) ||
    !!n.querySelector([1, 2, 3, 4, 5, 6].filter(i => i <= lv).map(i => 'h' + i).join(','));
  const sectionOf = h => {
    const out = [], lv = level(h);
    const take = first => { for (let n = first; n; n = n.nextElementSibling) { if (headsSection(n, lv)) break; out.push(n); } };
    take(h.nextElementSibling);
    const p = parentOf(h);
    if (p && p.nextElementSibling && !p.matches('main,body,[role="main"]') && out.reduce((k, e) => k + text(e).length, 0) <= 60) {
      take(p.nextElementSibling);
    }
    return out;
  };
  const reviewScope = new Set(reviewHeads.flatMap(sectionOf));
  const reviewish = e => {
    if (closest(e, 'blockquote,[itemprop="review"],[itemprop="reviewBody"]')) return true;
    for (let n = e; n; n = parentOf(n)) if (reviewScope.has(n)) return true;
    return false;
  };
  const pickBlocks = (cap, test) => blocks.filter(b => !b.overlay && b.area !== 'nav' && test(b))
    .sort((a, b) => ((b.words >= 5) - (a.words >= 5)) || ((a.area === 'footer') - (b.area === 'footer'))).slice(0, cap);
  // A value proposition, not a section heading, a review or a policy line (those have their own kinds).
  const headlineOk = b => !hit('testimonial_heading', b.text) && !reviewish(b.el) && !closest(b.el, BTN) &&
    !['returns', 'shipping', 'free_shipping', 'delivery'].some(k => hit(k, b.text));
  h1.slice(0, 2).forEach(e => snip('headline', visText(e), 'h1'));
  const bodyFont = parseFloat(style(document.body).fontSize) || 16;
  const hero = blocks.find(b => !b.overlay && b.area === 'main' && fold(b.el) && b.words >= 3 && b.el.tagName !== 'H1' && !h1.some(h => within(b.el, h)) &&
    parseFloat(style(textHolder(b.el)).fontSize) >= bodyFont * 1.4 && !PRICE.test(b.text) && headlineOk(b));
  if (hero) snip('headline', hero.text, 'hero ' + hero.el.tagName.toLowerCase());
  if (h1[0] && parentOf(h1[0])) {
    const next = blocks.filter(b => (h1[0].compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING) && !within(b.el, h1[0])).slice(0, 4);
    const after = next.find(b => !b.overlay && within(b.el, parentOf(h1[0])) && b.words >= 5 && fold(b.el) && !PRICE.test(b.text) &&
      !/^H[1-6]$/.test(b.el.tagName) && headlineOk(b));
    if (after && after.text.length <= 300) snip('headline', after.text, 'under h1');
  }
  ctas.filter(c => !c.overlay && ((c.lexicon_hit && PRIMARY_KEYS.has(c.lexicon_hit)) || (c.primary_like && c.above_fold && !c.lexicon_hit)))
    .slice(0, 4).forEach(c => { const t = shownText(c.el); if (t.length >= 2) snip('cta', t, c.tag + ' cta'); });
  const policyText = b => b.words >= 4 && b.link < 0.6 && !/^H[1-6]$/.test(b.el.tagName) && !reviewish(b.el) && !inCard(b.el);
  pickBlocks(3, b => policyText(b) && hit('returns', b.text)).forEach(b => snip('returns_policy', b.text, locator(b.el)));
  pickBlocks(3, b => policyText(b) && (hit('shipping', b.text) || hit('free_shipping', b.text) || hit('delivery', b.text)))
    .forEach(b => snip('shipping_policy', b.text, locator(b.el)));
  persuasion.scarcity.slice(0, 3).forEach(s => snip('scarcity', s.text, 'scarcity text'));
  persuasion.urgency.slice(0, 3).forEach(s => snip('urgency', s.text, s.countdown ? 'countdown' : 'urgency text'));
  overlays.filter(o => o.kind === 'consent').slice(0, 2).forEach(o => snip('consent', visText(o.el, true), 'consent overlay'));
  // Only visible wording is quoted: a bare "×" says nothing, and its aria-label is not on screen.
  overlays.filter(o => o.kind !== 'consent').forEach(o => o.buttons.filter(b => ['decline', 'close'].includes(b.kind) && wordy(b.text))
    .slice(0, 3).forEach(b => snip('modal_decline', b.text, o.kind + ' overlay')));
  checkboxes.filter(e => !['header', 'nav'].includes(areaOf(e))).slice(0, 6).forEach(e => {
    const l = shownText(e);
    if (l.length > 2) snip('checkbox_label', l, 'checkbox' + (isChecked(e) ? ' (checked)' : ''));
  });
  pickBlocks(3, b => b.link < 0.6 && hit('subscription', b.text)).forEach(b => snip('subscription', b.text, locator(b.el)));
  persuasion.authority.slice(0, 3).forEach(s => snip('authority', s.text, 'authority text'));
  if (reviews.rating_text) snip('testimonial', reviews.rating_text, 'rating summary');
  const reviewBoxes = ALL.filter(e => e.matches('blockquote,[itemprop="review"]') && visible(e) && !inOverlay(e) && !inCard(e));
  for (const c of reviewScope) if (!/^H[1-6]$/.test(c.tagName) && !c.matches(BTN) && visible(c) && !inOverlay(c)) reviewBoxes.push(c);
  const quotes = new Map(reviewBoxes.map(e => [e, visText(e)]));
  reviewBoxes.filter((e, i) => reviewBoxes.indexOf(e) === i && !reviewBoxes.some(o => o !== e && within(e, o)) && words(quotes.get(e)).length >= 4)
    .slice(0, 4).forEach(e => snip('testimonial', quotes.get(e), 'review'));
  if (lineItems.length || cart.total_text) {
    fees.slice(0, 5).forEach(f => snip('fee_line', f.row_text, 'cost line'));
    lineItems.filter(i => i.addon).slice(0, 3).forEach(i => snip('fee_line', i.row_text, 'line item'));
  }

  const strip = list => list.map(({el, row, overlay, area: a, ...rest}) => rest);
  return {
    ...base, doc, jsonld, meta,
    prices: strip(prices).slice(0, 80), ctas: strip(ctas), search, nav, products, filters, pdp, cart, forms,
    overlays: overlays.slice(0, 8).map(({el, ...o}) => ({...o, coverage: Math.round(o.coverage * 1000) / 1000,
      interrupting: interrupting(o)})),
    trust, persuasion, images, targets: targetSizes, a11y, snippets, truncated, audit_ms: Math.round(performance.now() - T0),
  };
}
