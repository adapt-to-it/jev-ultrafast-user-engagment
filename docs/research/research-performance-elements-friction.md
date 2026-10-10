# Research catalogue: objective technical and structural UX metrics for an LLM-driven Playwright e-commerce audit agent

Scope is performance, element availability and interaction friction. I wrote no files. Where a figure came from a secondary source, or a primary page could not be opened, I say so. The main caveats are listed at the end.

---

## A. PAGE SPEED / PERFORMANCE

### A1. Metric definitions, thresholds and capture

Thresholds are the 75th percentile of page loads, segmented by mobile and desktop. They come from [web.dev/articles/vitals](https://web.dev/articles/vitals) and the per-metric pages, all fetched. LCP, INP and CLS thresholds are unchanged. I saw SEO blogs claiming 2026 "composite/site-wide scoring" changes. I could not confirm those on web.dev, so I treat them as unverified.

| Metric | Definition / formula | Good / Needs improvement / Poor | Playwright / Chromium capture | Unit |
|---|---|---|---|---|
| **LCP** | Render time of the largest image, text block or video in the viewport, relative to navigation start ([web.dev/articles/lcp](https://web.dev/articles/lcp)) | ≤2.5 s / ≤4.0 s / >4.0 s | `web-vitals/attribution` `onLCP(cb,{reportAllChanges:true})`, or a `PerformanceObserver` for `largest-contentful-paint` with `buffered:true`. Attribution gives `target`, `url`, `timeToFirstByte`, `resourceLoadDelay`, `resourceLoadDuration`, `elementRenderDelay`. | ms |
| LCP sub-parts | TTFB, load delay, load duration, render delay ([web.dev/articles/optimize-lcp](https://web.dev/articles/optimize-lcp)) | Ideal split ~40% / <10% / ~40% / <10% | Same attribution object | % of LCP |
| **INP** | Latency of click, tap and keypress interactions, from input to next paint. Computed as the highest interaction, ignoring one outlier per 50 interactions ([web.dev/articles/inp](https://web.dev/articles/inp)). Latency = input delay + processing duration + presentation delay ([web.dev/articles/optimize-inp](https://web.dev/articles/optimize-inp)). | ≤200 ms / ≤500 ms / >500 ms | `onINP` attribution (`interactionTarget`, `inputDelay`, `processingDuration`, `presentationDelay`, `longAnimationFrameEntries`). Or a raw `event` observer with `durationThreshold` (default 104 ms, rounded to 8 ms, minimum 16 ms, per [MDN](https://developer.mozilla.org/en-US/docs/Web/API/PerformanceObserver/observe)). | ms |
| **CLS** | Largest session-window sum of impact fraction × distance fraction. A window has gaps under 1 s and a maximum of 5 s. Shifts within 500 ms of discrete input get `hadRecentInput` and are excluded ([web.dev/articles/cls](https://web.dev/articles/cls)). | ≤0.1 / ≤0.25 / >0.25 | `layout-shift` observer (buffered) or `onCLS`. Also keep a second raw sum that includes `hadRecentInput` shifts (see C1). | unitless |
| **TTFB** | Navigation start to first response byte. Includes redirects, DNS, TLS ([web.dev/articles/ttfb](https://web.dev/articles/ttfb)). | ≤0.8 s / ≤1.8 s / >1.8 s (rough guide) | `performance.getEntriesByType('navigation')[0].responseStart` | ms |
| **FCP** | First text, image, SVG or non-white canvas painted ([web.dev/articles/fcp](https://web.dev/articles/fcp)) | ≤1.8 s / ≤3.0 s / >3.0 s | `paint` entry `first-contentful-paint` | ms |
| **TBT** | Sum of (task duration − 50 ms) for tasks over 50 ms between FCP and TTI. Lab proxy for INP ([web.dev/articles/tbt](https://web.dev/articles/tbt)). | Target <200 ms on mobile. The 200/600 ms Lighthouse bands are from memory, not re-verified. | `longtask` observer (buffered) plus FCP. TTI is deprecated, so approximate with `load` + idle. Label it "TBT-approx". | ms |
| **Speed Index** | Visual progress, computed from video frames ([Chrome docs](https://developer.chrome.com/docs/lighthouse/performance/speed-index)) | Mobile ≤3.4 s / ≤5.8 s / >5.8 s | Needs a filmstrip. Run Lighthouse separately via CDP, or sample screenshots. Defer to a later phase. | ms |
| **Long Animation Frames** | Frames over 50 ms, with `blockingDuration` and per-script attribution. Chromium 123+ only ([Chrome docs](https://developer.chrome.com/docs/web-platform/long-animation-frames)). | None official | `PerformanceObserver` for `long-animation-frame`. Better blame than `longtask`. | count, ms |
| **Page weight** | Sum of encoded bytes | Lighthouse targets <1,600 KiB and flags >5,000 KiB ([Chrome docs](https://developer.chrome.com/docs/lighthouse/performance/total-byte-weight)). HTTP Archive 2025 median home page: 2,862 KB desktop, 2,559 KB mobile ([Web Almanac 2025](https://almanac.httparchive.org/en/2025/page-weight)). | `request.sizes()` (response body + header bytes) or CDP `Network.loadingFinished.encodedDataLength`. Prefer these to Resource Timing `transferSize`, which is 0 for cross-origin resources without Timing-Allow-Origin. That is general browser behaviour, not re-fetched. | KB |
| **Request count** | Count of `requestfinished` events | Median 77 desktop / 72 mobile (Web Almanac 2025) | `page.on('request'/'requestfinished')` | count |
| **JS bytes** | Sum over `resourceType()==='script'` | Median 697 KB desktop / 632 KB mobile (Web Almanac 2025) | Same as page weight | KB |
| **Third-party share** | (requests or bytes from other registrable domains) ÷ total | 92% of pages use at least one third party. Scripts are 30.5% of third-party requests ([Web Almanac 2024](https://almanac.httparchive.org/en/2024/third-parties)). | Compare eTLD+1 of each request with the first-party domain | % |
| **Image optimization** | Format (AVIF/WebP vs JPEG/PNG), `naturalWidth` ÷ rendered width, `loading=lazy` on the LCP image, missing width/height | Google 2017: 70% of mobile pages were over 1 MB and 30% could save over 250 KB by compression ([Think with Google PDF](https://www.thinkwithgoogle.com/_qs/documents/57/mobile-page-speed-new-industry-benchmarks.pdf), which I read locally) | `page.evaluate` over `document.images`, plus network content-type | % / ratio |
| **Action response time** | Time from agent action to first DOM, URL or network change | NN/g limits: 0.1 s feels instantaneous, 1 s keeps flow of thought, 10 s loses attention ([NN/g](https://www.nngroup.com/articles/response-times-3-important-limits/)) | Custom, using a MutationObserver and Playwright events | ms |

Lighthouse (v10+) weights are FCP 10%, Speed Index 10%, LCP 25%, TBT 30%, CLS 25% ([Chrome docs](https://developer.chrome.com/docs/lighthouse/performance/performance-scoring)). The mobile throttling preset is 150 ms RTT, 1.6 Mbps down, 750 Kbps up, 4× CPU ([Lighthouse docs](https://github.com/GoogleChrome/lighthouse/blob/main/docs/throttling.md)).

### A2. Capture notes and lab vs field caveats

- **Basic capture.** Use `context.newCDPSession(page)`, which is Chromium-only ([Playwright docs](https://playwright.dev/docs/api/class-cdpsession)).
- **web-vitals library.** It gives CLS, FCP, INP, LCP and TTFB. Import the attribution build from `web-vitals/attribution`, or load the IIFE build (`web-vitals.iife.js`). Callbacks give `value`, `rating`, `delta`, `entries` and `navigationType` ([README](https://github.com/GoogleChrome/web-vitals)).
- **When callbacks fire.** CLS and INP fire when visibility changes to hidden. INP never reports without an interaction. So in a session, use `reportAllChanges:true`, or navigate away at the end to force the flush.
- **Throttling.** Apply via CDP `Emulation.setCPUThrottlingRate` and `Network.emulateNetworkConditions` to approximate Lighthouse mobile. This is standard CDP; I did not re-fetch the CDP docs.
- **Lab vs field.** Lab runs are cold-cache, from a single location and device, with no personalization or real interaction. Field data "is what you should use to prioritize" ([web.dev](https://web.dev/articles/lab-and-field-data-differences)).
- **INP in lab.** Lighthouse "cannot measure INP" without a user ([web.dev/articles/vitals](https://web.dev/articles/vitals)). Lighthouse multi-step user flows are Puppeteer-only ([docs](https://github.com/GoogleChrome/lighthouse/blob/main/docs/user-flows.md)).
- **INP from an agent.** An agent that performs real clicks can produce INP-like numbers. My reading, to verify on your stack: Playwright sends trusted CDP input events, so Event Timing entries should appear.
- **Why agent INP is optimistic.** Agent actions are spaced by seconds of LLM latency, so the main thread is mostly idle. Real users click while the page is still loading. Report it as "interaction latency of agent clicks", not as CrUX-comparable INP.
- **Field baseline.** Pair lab runs with CrUX or PageSpeed Insights field data for the same origin. I did not re-verify the CrUX docs.

### A3. Published evidence linking speed to conversion and engagement

All of these are company, vendor or Google-commissioned publications unless noted. Most are observational.

| Study | Finding | Design | Source |
|---|---|---|---|
| **Deloitte Digital / 55 for Google, "Milliseconds Make Millions" (2020)** | A 0.1 s improvement raised retail conversion +8.4% and spend +9.2%. PLP→PDP rose +3.2%, PDP→add-to-basket +9.1%, basket→checkout +3.9%, checkout→order +4.7%. Luxury PDP→add-to-basket rose +40.1%. | 37 brand sites, 30M+ sessions, 30 days at end of 2019. The 0.1 s was applied to four metrics at once (First Meaningful Paint, Estimated Input Latency, observed load, max server latency/TTFB). Two of those metrics are now deprecated. | [web.dev case study](https://web.dev/case-studies/milliseconds-make-millions) |
| **Google/SOASTA (2017)** | Load time 1 s→7 s raised mobile bounce probability by 113%. Elements 400→6,000 cut conversion probability by 95%. The average mobile landing page took 22 s to load fully on 3G. Neural-net prediction accuracy was 90%. | 900,000 landing pages, emulated 3G | [Think with Google](https://www.thinkwithgoogle.com/_qs/documents/57/mobile-page-speed-new-industry-benchmarks.pdf), read locally |
| Google/DoubleClick (2016) | 53% of mobile visits are abandoned if a page takes over 3 s | Google Analytics, n=3,700 sites | Same PDF, footnote 2 |
| Google/SOASTA, other intervals | The frequently cited "+32% (1→3 s), +90% (1→5 s), +106% (1→6 s), +123% (1→10 s)" came only from secondary summaries. The primary text I extracted only has +113% for 1→7 s. | | **Unverified** |
| **Akamai State of Online Retail Performance (Spring 2017)** | A 100 ms delay cut conversion by 7%. A 2 s delay raised bounce by 103%. Conversion peaked at 12.8% on desktop at 1.8 s and 3.3% on mobile at 2.7 s. | About 10 billion visits, one month, from the report PDF and press coverage | [Akamai PDF](https://www.akamai.com/site/ja/documents/analyst-report/akamai-state-of-online-retail-performance-spring-2017.pdf), [MediaPost](https://www.mediapost.com/publications/article/306009/three-seconds-and-youre-out.html) |
| **Portent (2022)** | B2C e-commerce conversion was 3.05% at 1 s, 1.68% at 2 s, 1.12% at 3 s, 0.67% at 4 s. | 6 e-commerce and 14 B2B sites, 100M+ page views. Small number of sites. | [Portent](https://www.portent.com/blog/analytics/research-site-speed-hurting-everyone.htm) |
| **Vodafone (2021)** | LCP improved 31% (8.3 s→5.7 s). Sales rose +8%, lead-to-visit +15%, cart-to-visit +11%. | 50/50 A/B test of a single landing page on paid traffic. Optimized page was functionally identical. | [web.dev](https://web.dev/case-studies/vodafone) |
| **Rakuten 24** | Revenue per visitor +53.37%, conversion +33.13%, AOV +15.20%, exit rate −35.12%. | Month-long 50/50 A/B test. Mobile load was 0.4 s faster. | [web.dev](https://web.dev/case-studies/rakuten) |
| **Farfetch** | Conversion fell 1.3% per +100 ms of LCP. Exit rate improved 3.1% per −0.01 CLS. Conversion rose 2.8% per −1 s TTI. | RUM correlations with web-vitals, plus A/B tests with +1–5% conversion | [web.dev](https://web.dev/case-studies/farfetch) |
| **redBus** | INP improved 72% on the search page and sales rose +7%. | RUM, p95 | [web.dev](https://web.dev/case-studies/redbus-inp) |

Caveats:
- Correlational RUM studies (Farfetch, Portent, Akamai, SOASTA) cannot isolate causality.
- The two A/B tests (Vodafone, Rakuten) are stronger but are each a single page.
- All are publicly promoted by the companies involved.
- Treat "+X% per 100 ms" as directional, not as a transferable elasticity.

---

## B. ELEMENT AVAILABILITY / PAGE STRUCTURE

### B1. Evidence base

Baymard figures change by benchmark year and definition, so cite the year with any number.

**Checkout and cart**
- Average documented cart abandonment is 70.22% across 50 studies. Stated reasons, among non-browsers ([Baymard](https://baymard.com/lists/cart-abandonment-rate)):
  - 40% extra costs too high
  - 20% delivery too slow
  - 19% security concerns
  - 18% forced account creation
  - 17% checkout too long or complicated
  - 17% errors or crashes
  - 13% returns policy
  - 12% could not see total cost
  - 10% card declined
  - 9% too few payment options
- The average checkout has 11.3 form fields (2024), down from 11.8 (2021) and 12.7 (2019). Baymard says "most sites need only 8" ([Baymard](https://baymard.com/blog/checkout-flow-average-form-fields)). Field count affects usability "far more than the number of steps".
- Another Baymard page counts "form elements" instead: 23.48 typical vs 12–14 optimal. I did not reconcile this against the "form fields" definition above, so fix one counting rule in your code.
- Further checkout stats from the Baymard 2025 benchmark (180+ sites) ([Baymard](https://baymard.com/blog/ecommerce-checkout-usability)):

| Finding | Share of sites |
|---|---|
| Fail to make guest checkout most prominent | 47% (an older Baymard figure was 62%) |
| Do not delay account creation until after checkout | 42% |
| Accept only one payment method | 21% |
| Lack inline validation | 31% |
| Lack address lookup | 55% |
| Use "shipping speed" instead of "delivery date" | 41% |
| Mediocre or worse overall | 64% |

**Product page**
- 52% of desktop and 62% of mobile sites are mediocre or worse ([Baymard](https://baymard.com/blog/current-state-ecommerce-product-page-ux)).
- Specific gaps:

| Gap | Share of sites |
|---|---|
| No total-order-cost estimate | 67% |
| No return policy on the PDP | 44% |
| Size selection not via buttons | 57% |
| No "in scale" images | 37% |
| No price per unit | 81% |

- A secondary Baymard summary says 56% of users first inspect images, and an older study says 64% look for shipping costs on the PDP. I could not open those primary pages.
- NN/g product page must-haves ([NN/g](https://www.nngroup.com/articles/ecommerce-product-pages/)):
  - name
  - images with enlarge
  - price including extra charges
  - clear options
  - availability
  - add-to-cart with confirmation
  - concise description
- NN/g nice-to-haves: reviews, multiple images, video/zoom, related products.

**Listing pages and search**
- Product lists ([Baymard](https://baymard.com/blog/ecommerce-product-lists-report-and-benchmark)):

| Finding | Share of sites |
|---|---|
| Have serious issues | 80% |
| Do not offer all 5 essential filter types | 57% |
| Do not allow all 4 essential sorts (price, rating, best-selling, newest) | 64% |
| Violate "back button" expectations | 59% |
| Do not show an applied-filters overview | 28% |

- Search ([Baymard](https://baymard.com/blog/ecommerce-search-report-and-benchmark)):

| Finding | Share of sites |
|---|---|
| Provide autocomplete | 80% (only 19% at the top tier) |
| Lack misspelling-tolerant suggestions | 69% |
| Lack an explicit mobile submit button | 21% |
| Mediocre or worse on search | 56% |

- Homepage and category navigation: 58% of desktop and 67% of mobile sites are mediocre to poor, and 60% of hover dropdown implementations are poor. Both figures, plus the 300–500 ms hover-delay recommendation, are **secondary** (search snippets).
- NN/g on search ([NN/g](https://www.nngroup.com/articles/search-visible-and-simple/)):
  - Use a visible type-in field at the top of the page, not a link.
  - Make it wide enough for a typical query.
  - Changing a search link to a visible box raised search usage by 91%.
  - First-query success was 51%, second 32%, third 18%.
- NN/g on breadcrumbs ([NN/g](https://www.nngroup.com/articles/breadcrumbs/)):
  - They suit deep hierarchies and e-commerce.
  - Show hierarchy, not history.
  - Mobile tap targets should be at least 1 cm × 1 cm.
- NN/g on scrolling and attention: users spend 57% of viewing time above the fold and 74% in the first two screenfuls (120 participants, 130k fixations) ([NN/g](https://www.nngroup.com/articles/scrolling-and-attention/); figures from search summary).

### B2. Detection heuristics (non-site-specific)

All outputs should be `{present, evidence (selector or text), in_viewport_at_load, bbox}`. Use multilingual regexes. Your sample goal mentions "€", so include IT/DE/FR/ES forms (carrello, warenkorb, panier, carrito).

| Page type | Element | Heuristic |
|---|---|---|
| **PDP** | Primary CTA | `getByRole('button',{name:/add to (cart\|basket\|bag)\|buy now\|aggiungi al carrello\|in den warenkorb\|ajouter au panier\|añadir/i})`, `form[action*=cart]`, `[name=add-to-cart]`. Check visible, enabled, and `bbox.top < innerHeight` for above-the-fold. |
| | Price | JSON-LD `offers.price` / `priceCurrency` ([Google Product docs](https://developers.google.com/search/docs/appearance/structured-data/product-snippet)), `[itemprop=price]`, Open Graph `product:price:amount` (Meta/Pinterest convention, secondary source), currency-symbol regex near the `<h1>`. Compare structured and visible price. A mismatch is a trust defect. |
| | Stock | JSON-LD `availability` (InStock, OutOfStock, BackOrder, PreOrder, LimitedAvailability…), text patterns ("sold out", "esaurito"), disabled CTA. NN/g wants stock shown per variant up front. |
| | Shipping, delivery date, returns | JSON-LD `shippingDetails` and `hasMerchantReturnPolicy` ([merchant listing docs](https://developers.google.com/search/docs/appearance/structured-data/merchant-listing)), text patterns ("free shipping", "consegna entro", "30-day returns"), links to a returns page. |
| | Images | Count of visible `<img>` of at least ~200 px in the gallery region. JSON-LD `image[]` length. Zoom affordance: `cursor:zoom-in`, buttons with "zoom/enlarge". Thumbnails present. |
| | Reviews | `aggregateRating.ratingValue` and `reviewCount`, `[itemprop=ratingValue]`, star widgets (aria-label "4.5 out of 5"). |
| | Variant selectors | `<select>` vs button/radio groups. Baymard prefers buttons for sizes. |
| | Trust badges | `img[alt]` or filename matching visa/mastercard/paypal/klarna/trustpilot. Heuristic only. |
| **PLP** | Product grid | Repeated sibling nodes each containing img + price + link, or JSON-LD `ItemList`. Card completeness: price, image, rating. |
| | Filters and sort | `[role=complementary]` or `aside` with at least 3 checkboxes, or a "Filter/Filtri" button. Sort: `<select>` whose options match price/rating/new/best-selling. Applied-filter chips. Result count text. |
| | Pagination vs infinite scroll | `nav[aria-label*=pagination]`, `rel=next`, or document height growth on scroll. |
| **Home / global** | Search box | `input[type=search]`, `[role=search]`, `input[name=q\|s\|query]`, placeholder regex. Record width in px and whether it is above the fold. |
| | Autocomplete | Type 3 characters, then check `role=combobox`, `aria-autocomplete`, `[role=listbox]`/`[role=option]` count, and latency. |
| | Navigation and breadcrumbs | `nav[aria-label*=breadcrumb i]`, `BreadcrumbList` JSON-LD. Cart link or icon: `a[href*=cart\|basket\|carrello]` or aria-label. |
| **Cart** | Line items, subtotal, shipping and tax estimate, checkout CTA, promo field (hidden by default is better), guest-checkout option | Role/text patterns. Check for an estimated total cost, since Baymard flags missing totals as an abandonment cause. |
| **Checkout** | Field count | Visible, enabled `input`/`select`/`textarea`, excluding hidden, submit, button and honeypots. Compare with Baymard's 11.3 average and ~8 ideal. |
| | Steps | Count of distinct URLs or step indicators from cart to confirmation. |
| | Guest checkout, forced account | Text ("guest", "ospite", "gast", "continue without account"). A password field before payment means forced account creation. |
| | Autofill and validation | Proportion of fields with `autocomplete` tokens (`email`, `given-name`, `street-address`, `cc-number`). Check `aria-invalid` toggling on blur and `[role=alert]` messages. Required vs optional marking. |
| | Payment options | Count of labelled payment radios or icons. Check that total cost is visible before payment. |

Safety rule: the agent should stop before the final "place order" click, or run against a test-mode shop.

### B3. Accessibility signals

| Signal | Definition and threshold | Capture |
|---|---|---|
| **Automated violations** | `@axe-core/playwright` `AxeBuilder.withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze()` returns `violations` with impact ([Playwright docs](https://playwright.dev/docs/accessibility-testing)). Count by impact (critical, serious). | Per page type |
| Coverage caveat | Deque found automation detects about 57% of issues by volume (2,000+ audits, 13,000+ pages). Vendor study. ([Deque](https://www.deque.com/blog/automated-testing-study-identifies-57-percent-of-digital-accessibility-issues/)) | |
| **Target size** | WCAG 2.2 SC 2.5.8 (AA): at least 24×24 CSS px, with exceptions for spacing (24 px circles must not overlap), equivalent control, inline, user-agent and essential ([W3C](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)). 2.5.5 (AAA) is 44×44. web.dev recommends 48×48 px with 8 px spacing ([web.dev](https://web.dev/articles/accessible-tap-targets)). | `getBoundingClientRect()` over visible interactive elements in a mobile viewport. Exclude inline text links, apply the spacing exception. Report % compliant at 24, 44 and 48. |
| **Contrast, alt, labels** | axe `color-contrast`, `image-alt`, `label`. | axe |
| Benchmarks | WebAIM Million 2026: 95.9% of home pages fail WCAG 2. Average 56.1 errors per page. Low contrast 83.9%, missing alt 53.1%, missing labels 51%, empty links 46.3%, empty buttons 30.6%. Shopping sites average 71.0 errors per page. ([WebAIM](https://webaim.org/projects/million/)) | |
| Accessibility tree | `locator.ariaSnapshot()` returns a YAML role/name tree ([Playwright](https://playwright.dev/docs/aria-snapshots)). Useful as agent input and for role-based detection. | |

---

## C. INTERACTION FRICTION FROM SYNTHETIC SESSIONS

### C1. Proposed agent-side metrics

Thresholds marked "design choice" have no published standard. Industry vendors keep exact values proprietary.

| Metric | Definition / formula | Playwright measurement | Unit | Benchmark / source |
|---|---|---|---|---|
| **Task success rate** | Successful runs ÷ runs. Judge by an oracle (cart contains the item, order-confirmation page, expected DOM state), not LLM self-report. | Post-run assertion | % | Mean across 1,189 usability tasks was 78%. >92% is top quartile, <49% bottom quartile ([MeasuringU](https://measuringu.com/task-completion/)). |
| **Actions to goal / path efficiency** | Optimal steps ÷ actual steps, with optimal from a human-validated golden path | Count agent actions (click, type, navigate, scroll) | ratio | Do not use a "3 clicks" rule. Porter's study (44 users, 620 tasks, 8,000+ clicks) found no more quitting at 3 clicks than at 12 ([UIE](https://articles.centercentre.com/?p=17)). |
| **Time on task (site-attributable)** | Total time − LLM inference time. Split into wait-for-site and action-execution. | Timestamps around each LLM call and each action | s | Only the site-attributable part is comparable with humans. |
| **Revisit / backtrack rate** | (S − N) ÷ S, where S = total page visits and N = unique pages. Plus count of history-back events. | `framenavigated`, URL set | % / count | None standard |
| **Lostness** | See C2 | Needs R (minimum pages) | 0–1 | Below 0.4 not lost, above 0.5 lost |
| **Dead click rate** | Clicks followed by no DOM mutation, no URL change, no focus change and no network request within T | MutationObserver and `page.on('request')` around each click | % of clicks | Clarity: "no feedback in a reasonable amount of time" ([MS Learn](https://learn.microsoft.com/en-us/clarity/insights/semantic-metrics)). FullStory: nothing changes "within a few seconds", exact values proprietary. T = 1–3 s is a design choice. Count only clicks on elements with button/link role, to avoid false positives from exploratory clicks. |
| **Rage-click analogue** | Contentsquare: at least 3 clicks on one element in under 2 s ([Contentsquare](https://contentsquare.com/blog/rage-clicks-what-are-they-and-how-to-avoid-them/), from search summary). An agent acts every few seconds, so a time window will not fire. | Redefine as "same target acted on at least 3 times within N steps with no state change" | count | My inference. Clarity's rule is "repeated clicks/taps in a specific area… that do not result in any change on the page" ([Clarity blog](https://clarity.microsoft.com/blog/rage-clicks-user-behavior/)). |
| **Quick back** | Return to the previous page in less than a dwell threshold | Back navigation timing | count | Clarity does not publish its threshold. |
| **Excessive scrolling** | Vertical scroll above the expected average | `scrollY` deltas | px | Relative to your own baseline |
| **Overlay interruptions** | Count of overlays (cookie, newsletter, promo, login, chat, age gate), time to dismiss, clicks to dismiss, viewport coverage %. | `[role=dialog]`, `[aria-modal=true]`, `dialog[open]`, fixed or sticky high-z elements covering over ~15% of the viewport. Playwright's "element intercepts pointer events" retry count is a robust occlusion signal. | count, s, % | NN/g: avoid modals for newsletters and checkout flows ([NN/g](https://www.nngroup.com/articles/modal-nonmodal-dialog/)). Google exempts legally required cookie and age dialogs but penalizes intrusive interstitials ([Google](https://developers.google.com/search/docs/appearance/avoid-intrusive-interstitials)). For named CMPs, [DuckDuckGo autoconsent](https://github.com/duckduckgo/autoconsent) has Playwright-tested detection rules. |
| **Layout instability during interaction** | Sum of `layout-shift` values including those with `hadRecentInput=true`. Plus "target moved" count: bounding box at decision time vs click time. | Raw `layout-shift` observer. Playwright's actionability check waits for a stable box, so count "not stable" retries. | score, count | CLS excludes shifts within 500 ms of input, so post-click shifts need a separate metric. |
| **Unexpected navigation** | Navigation or new tab not triggered by a link or button the agent chose | `context.on('page')`, `framenavigated` with no preceding click on an anchor | count | None |
| **Errors encountered** | `pageerror`, `console.error`, 4xx/5xx responses, JS error after a click (Clarity "click errors"), form validation messages (`aria-invalid`, `[role=alert]`) | `page.on('pageerror'/'console'/'response')` | count | Baymard: 17% abandon due to errors or crashes. |
| **State preservation after Back** | After PDP→Back, are filters, page number and scroll preserved? | Compare URL, applied filters, `scrollY` | boolean | Baymard: 59% of sites violate back-button expectations. |
| **Search effectiveness** | Zero-result rate, reformulations per task, autocomplete latency | Wrap agent search actions | % / count | NN/g: 51% first-query success, falling on retries |

### C2. Lostness formula (Smith 1996)

L = √[ (N/S − 1)² + (R/N − 1)² ]

- N is the number of unique pages visited.
- S is the total page visits including revisits.
- R is the minimum number of pages required.
- L runs from 0 (perfectly efficient) to 1 (completely lost).
- Thresholds: below 0.4 not lost, 0.4–0.5 indeterminate, above 0.5 lost. In a replication, 91% of scorers under 0.4 were not lost and 89% over 0.5 were lost ([MeasuringU](https://measuringu.com/lostness/)).

Verification status:
- The formula is quoted from secondary summaries. I could not open Smith's original paper ([OUP](https://academic.oup.com/iwc/article/8/4/365/686010)).
- MeasuringU's text rendering of the formula differed from the commonly cited form above.
- A 2020 Frontiers paper uses a different variant, (S−N)/(R−N), unbounded above.
- Use Smith's original form for comparability.

### C3. Industry metric definitions

| Framework | Definition | Source |
|---|---|---|
| **Microsoft Clarity** | Rage clicks, dead clicks, excessive scrolling, quick backs, JS errors, click errors | [MS Learn](https://learn.microsoft.com/en-us/clarity/insights/semantic-metrics) |
| **Contentsquare frustration score** | A 1–100 session or pageview score. Session bands (secondary source): 0–35, 36–65, over 66. Signals: rage clicks, repeated field interactions, cart revisits, slow loads, fast backs. Algorithm not published. | [Contentsquare](https://contentsquare.com/platform/capabilities/frustration-score/) |
| **Google HEART** | Happiness, Engagement, Adoption, Retention, Task success, with a Goals–Signals–Metrics process (Rodden, Hutchinson, Fu, CHI 2010) | [Google Research](https://research.google.com/pubs/archive/36299.pdf) |
| **SUS** | 10 items, 0–100. Average 68. 80.3+ is an A. Below 51 is an F. | [MeasuringU](https://measuringu.com/sus/) |
| **SEQ** | One 7-point item after each task. Average about 5.3–5.6. Correlates r≈0.5 with time and completion. | [MeasuringU](https://measuringu.com/seq10/) |

HEART mapping for synthetic sessions:
- **Task success:** measurable directly.
- **Engagement:** measurable as actions, pages and depth per session.
- **Happiness, Adoption, Retention:** not validly measurable. LLM-rated SUS or SEQ is subject to sycophancy.

### C4. Literature on LLM agents as synthetic users

| Work | Finding | Validity relevance |
|---|---|---|
| **UXAgent** (Lu et al., CHI EA 2025; [arXiv 2504.09407](https://arxiv.org/abs/2504.09407), [2502.12561](https://arxiv.org/pdf/2502.12561)) | Persona generator, dual-loop agent (fast and slow), browser connector. Simulates thousands of users on a shopping site. | UX researchers (5 in the framework paper, 16 in the demo version) praised it but raised concerns. It is for pre-testing a study design, not replacing one. |
| **AgentA/B** ([arXiv 2504.09723](https://arxiv.org/abs/2504.09723), [ACM DL](https://dl.acm.org/doi/10.1145/3772363.3799039)) | 1,000 agents on Amazon.com. A reduced filter list produced more purchases and filter actions, matching the direction of a parallel human experiment. | Agents were more goal-directed and took shorter action sequences than 1M human users. Directional agreement, not absolute agreement. |
| **Can LLM Agents Simulate Multi-Turn Human Behavior?** ([ACL 2026](https://aclanthology.org/2026.acl-long.2034.pdf)) | 31,865 real shopping sessions. Prompt-based LLMs reach about 11.86% accuracy at predicting the human's next action. Fine-tuning and reasoning help modestly. | Weak action-level fidelity. |
| **AMUSER** ([arXiv 2609.22971](https://arxiv.org/abs/2609.22971)) | Turns agent simulation traces into prioritized UX recommendations. NDCG@3 0.758 vs 0.359 for text-only, at 89% lower cost. | Evaluated by expert annotation and LLM-as-judge, not against real user outcomes. |
| **PerceptUI** ([arXiv 2606.05697](https://arxiv.org/abs/2606.05697)) | Persona-conditioned answers to UI questions. Claims "human-level realism". | Abstract only, no limitations stated. Requires trained models. |
| **Lost in Simulation** ([arXiv 2601.17087](https://arxiv.org/abs/2601.17087)) | LLM-simulated users (conversational) are unreliable proxies. Agent success varies by up to 9 points depending on which LLM plays the user. Miscalibrated on hard and moderate tasks. Worse for AAVE and Indian-English speakers. | The opposite direction (simulated users evaluating agents), but it shows simulator choice moves results. |
| **WebArena** ([arXiv 2307.13854](https://arxiv.org/abs/2307.13854)) | GPT-4 agent 14.41% vs human 78.24% (812 tasks) | Early 2023 result. Agent capability, not site quality, limits success. |
| **Online-Mind2Web** ([arXiv 2504.01382](https://arxiv.org/abs/2504.01382)) | 300 tasks across 136 live sites. Most agents completed about 30%. The WebJudge LLM judge agrees with humans about 85% of the time. | Reported progress was over-optimistic. An LLM judge adds about 15% disagreement. |
| **Pop-up attacks** (Zhang, Yu, Yang, ACL 2025; [arXiv 2411.02391](https://arxiv.org/pdf/2411.02391)) | Adversarial pop-ups got agents to click them 86% of the time and cut task success by 47%. Instructions to ignore pop-ups did not help. | Agents react to overlays differently from humans. |
| **NN/g on synthetic users** ([NN/g](https://www.nngroup.com/articles/synthetic-users/)) | Sycophancy, unrealistically high completion, shallow insights. Use for hypotheses only. | |
| Park et al. 2024 ([arXiv 2411.10109](https://arxiv.org/abs/2411.10109)) | Agents built from interviews replicated 1,052 people's survey answers 85% as accurately as the people replicated themselves two weeks later. | Attitudinal survey fidelity with rich real data. Does not validate navigation behaviour. |

Validity concerns, mostly my synthesis from the above:
1. **Perception mismatch.** The agent reads the DOM, accessibility tree or screenshots, not human vision. Contrast, visual hierarchy and above-the-fold prominence need explicit geometric checks.
2. **Optimistic paths.** Agents are goal-directed and do not browse or get distracted. Use them for relative comparisons.
3. **LLM latency inflates time-on-task.** It also masks site latency, so decompose time as in C1.
4. **Failure confounding.** A failed run may be the agent's limit, not the site's. Run the same agent on reference sites or a golden path to normalize.
5. **Non-determinism.** Repeat each goal and persona several times. Report medians and spread.
6. **Judge noise.** LLM judges disagree with humans about 15% of the time. Prefer programmatic oracles.
7. **Self-reported satisfaction is unreliable.** Per NN/g and the sycophancy findings, do not use LLM-rated SUS or SEQ as a headline metric.
8. **Different content.** Bot detection, CAPTCHAs and consent walls can serve the agent different pages. Log them as events.
9. **Calibrate against humans.** Compare against field data (CrUX) and known human-found issues before trusting absolute numbers.

---

## D. Prioritized shortlist (24 metrics)

### MVP (robust, cheap, non-site-specific)

| # | Metric | Section |
|---|---|---|
| 1 | Task success rate (oracle-verified) | C1 |
| 2 | Path efficiency / actions to goal, with revisit and backtrack rate | C1 |
| 3 | Site-attributable time on task | C1 |
| 4 | LCP with sub-parts, per page type | A1 |
| 5 | CLS, plus post-input shift sum | A1, C1 |
| 6 | Interaction latency of agent clicks (INP-like) | A1 |
| 7 | TTFB | A1 |
| 8 | Transfer weight, request count, JS bytes | A1 |
| 9 | Third-party share (requests, bytes, domains) | A1 |
| 10 | Page errors, console errors, failed requests, click errors | C1 |
| 11 | Dead click rate | C1 |
| 12 | Overlay interruptions (count, time and clicks to dismiss, occlusion retries) | C1 |
| 13 | Element-availability checklist per page type | B2 |
| 14 | Structured-data completeness and price consistency (JSON-LD Product/Offer) | B2 |
| 15 | Checkout friction snapshot (field count, steps, guest option, forced account, payment methods, autofill coverage, total cost visible) | B1, B2 |
| 16 | axe-core violations by impact, plus tap-target compliance at 24, 44 and 48 px | B3 |

### Later phase

| # | Metric | Why later |
|---|---|---|
| 17 | Lostness L | Needs a known minimum path R per goal |
| 18 | FCP and TBT-approx / LoAF blocking time | TBT needs a TTI approximation |
| 19 | Image optimization score | Several sub-checks |
| 20 | Search effectiveness (zero results, reformulations, autocomplete latency) | Depends on agent search behaviour |
| 21 | Validation and error-message encounters and recovery | Needs form interactions |
| 22 | State preservation after Back | Needs scripted back navigation |
| 23 | Speed Index / full Lighthouse run | Separate filmstrip tooling |
| 24 | Repeat-action loop (rage-click analogue) and unexpected navigation or new-tab count | Thresholds are a design choice |

Cross-cutting requirements: repeat each goal and persona N times, throttle CPU and network consistently, store evidence (selector, text, bounding box) with every boolean, and compare against a CrUX baseline.

---

## Source caveats

- **Vendor or company-published:** Deloitte/Google, Google/SOASTA, Akamai, Portent, Vodafone, Rakuten, Farfetch, redBus, Deque, Contentsquare, FullStory.
- **Secondary only (primary not opened):**
  - the Google/SOASTA 32/90/106/123% bounce figures
  - Contentsquare score bands and the 3-clicks-in-2-seconds rage definition
  - NN/g scrolling and attention numbers
  - Baymard "56% inspect images" and "64% look for shipping costs on the PDP"
  - Baymard homepage/category and hover-menu numbers
  - the Lostness formula text
- **Baymard inconsistencies across pages:** abandonment reasons, guest-checkout share and form-field counts differ between Baymard pages and years. Cite with the year and definition.
- **Not fetched:** Lighthouse TBT 200/600 ms bands, CrUX documentation, CDP throttling docs. Treat as from memory.
- **Mismatched cache file:** a locally cached PDF whose filename suggested the Think with Google report turned out to be an unrelated arXiv paper. I re-extracted the Google/SOASTA text from the correct file before using it.