# Psychological and behavioral engagement KPIs for an automated e-commerce audit agent

Date of research: 2026-10-06. Nothing was written to disk.

Evidence legend:
- **Strong**: replicated or large-scale, or a normative standard that is directly measurable.
- **Moderate**: peer-reviewed or large industry study, but correlational or only indirectly transferable to shops.
- **Weak**: theory or practitioner heuristic, or contested evidence.

Verification tags:
- **[V]**: I read the primary source this session.
- **[S]**: I saw it only in search-engine summaries or secondary pages.
- **[M]**: from my own knowledge, not re-verified; check before relying on it.

---

## 0. Key conclusions

1. **Fogg's B=MAP is a useful scaffold, not a fitted equation.** Motivation cannot be observed from a page. Ability (friction) and Prompt (CTA salience) can. Motivation can only be proxied by the supply of motivators: social proof, genuine scarcity, value proposition and incentives.
2. **The strongest quantitative anchors are on the friction, price-transparency and trust side.** Baymard's checkout research gives numbers for each. Extra costs is the top abandonment reason at 40% (see section 4).
3. **Choice overload is contested.**
   - Scheibehenne et al. (2010) found a mean effect of about zero across 50 experiments.
   - Chernev et al. (2015) found a significant effect once moderators are modeled.
   - So do not penalize large assortments per se. Penalize large assortments that lack decision aids (filters, sorting, comparison, defaults).
4. **Dark-pattern detection has a published, replicable method.** Mathur et al. (2019) used a crawler, mutation observers, and repeated visits to separate genuine from deceptive timers and stock messages. Copy that protocol.
   - Countdown timers, low-stock messages and activity messages are common and often not deceptive.
   - They become a risk signal when they fail the "reload / revisit" test.
5. **Dark patterns work in the short term.** Luguri and Strahilevitz (2021) found 11.3% acceptance with no dark pattern, 25.8% with mild and 41.9% with aggressive patterns. A shop can therefore score well on raw conversion while carrying trust and regulatory risk. Treat dark patterns as a penalty, never as persuasion credit.
6. **Aggregate with a weighted geometric mean** (non-compensatory, consistent with B=MAP needing all three elements), plus a bounded dark-pattern penalty. Every signal gets exactly one "home" sub-index. Publish a coverage-based confidence grade.
7. **LLM reproducibility cannot rely on temperature 0 any more.** Anthropic's migration guide states that on Claude Sonnet 5.5 a non-default `temperature`, `top_p` or `top_k` returns HTTP 400 [V]. Reproducibility must come from:
   - closed-label structured outputs;
   - evidence quotes verified programmatically against the DOM;
   - multi-sample voting;
   - memoization keyed on a content hash;
   - pinned model and rubric versions.
8. **The score is "predicted friction / engagement readiness", not engagement.** Expect modest validity against real analytics. PURE, a comparable expert-rating method, correlates only r≈0.5–0.6 with perceived-usability questionnaires and not at all with task time or completion [V].

---

## 1. Constructs, observable indicators, extraction, scoring, evidence

Scoring anchors below are design priors, labeled as such, unless a published threshold exists. Calibrate them on a reference corpus (section 2).

### 1.1 Fogg Behavior Model (B = MAP)

**Framework** [V for B=MAP, S/M for details]
- Behavior occurs when Motivation, Ability and a Prompt converge at the same moment (https://www.behaviormodel.org/).
- Ability is "simplicity", set by your scarcest resource. Fogg's six factors are time, money, physical effort, mental effort, social deviance and non-routine. The chain is as strong as its weakest link (https://www.behaviormodel.org/ability, https://behaviordesign.stanford.edu/resources/fogg-behavior-model).
- Prompt types are spark, facilitator and signal. This is from Fogg's 2009 paper [M].

**Ability / friction (maps to the Friction/Ability Index, FAI)**

| Fogg factor | Observable indicators | Extraction | Scoring (priors) |
|---|---|---|---|
| Time | Steps from product page to payment page; clicks and keystrokes on the golden path; optional Keystroke-Level Model predicted time (Card, Moran & Newell operators [M]) | Playwright trace of the replayed path | Baymard: average checkout is 5.1 steps (2024). Prior: ≤3 steps = 100, 5 = 60, ≥8 = 0 |
| Physical effort | Visible required fields in guest checkout; `autocomplete` coverage (WCAG 1.3.5); correct `type`/`inputmode`; express-pay buttons (Apple Pay, Google Pay, PayPal); address lookup | DOM query; the axe-core autocomplete rule | Baymard: average 11.3 fields, "most sites need only 8"; field count matters more than step count [V: https://baymard.com/blog/checkout-flow-average-form-fields]. Prior: ≤8 = 100, 11–12 = 60, ≥20 = 0 |
| Forced registration | Guest checkout available (binary) | Flow trace | Baymard: 18% abandon because the site wanted account creation [V: https://baymard.com/lists/cart-abandonment-rate] |
| Mental effort | Labels bound to inputs; inline validation; error-message specificity; competing choices on checkout screens; leak links (header nav, banners) on checkout | axe-core "label" and "name" rules; DOM counts; LLM rubric for error-message clarity | Lighthouse-style severity weights for accessibility rules: critical 10, serious 7, moderate 3, minor 1 [V: Lighthouse `default-config.js`] |
| Social deviance | Intrusive required fields (phone, date of birth, forced account, fiscal code when not needed) | Field-name lexicon (IT/EN) | Weak; count only |
| Non-routine | Convention adherence: logo links home, cart icon in header, search box, standard button labels | DOM and accessibility-tree heuristics | Weak; light weight |
| Targets (Fitts) | Interactive-target size and spacing on the golden path | `getBoundingClientRect`. WCAG 2.2 SC 2.5.8 requires ≥24×24 CSS px (AA), with spacing, equivalent, inline, user-agent and essential exceptions. SC 2.5.5 recommends 44×44 (AAA) [V: https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html] | % of golden-path targets passing 24 px (and 44 px as a stretch) |
| Robustness | JS console errors; 4xx/5xx; broken links on the golden path | Playwright event listeners | Baymard: 17% abandon because of site errors or crashes. Stanford guideline 10 is "eliminate errors". Map to the Performance index to avoid double counting |

**Evidence:** Strong for checkout friction (Baymard). Moderate for the Fogg six-factor mapping, which is conceptual.

**Prompt (CTA clarity and salience)**

| Indicator | Extraction | Scoring |
|---|---|---|
| Exactly one visually dominant primary CTA per funnel stage (product page "Add to cart", cart "Checkout", checkout "Continue") | Computed styles, size and contrast vs competing buttons; count of primary-styled buttons in the viewport | 100 if one dominant CTA in the first viewport on desktop and mobile; subtract for competing primaries |
| CTA is above the fold, or sticky on mobile | `getBoundingClientRect().top < viewportHeight`; `position: sticky/fixed` | Binary per viewport |
| CTA contrast against its background and neighbors | WCAG 1.4.3 contrast (4.5:1 text) and 1.4.11 (3:1 non-text) [M]; ΔE (CIEDE2000) vs surroundings | Linear 3:1 → 0, 7:1 → 100 |
| CTA label specificity (verb-first, outcome-clear, not "Submit") | Lexicon first, then LLM binary (clear / unclear) with a quoted label | 0/100 per stage |

**Evidence:** Weak to moderate. Fogg's theory is well known, but the salience thresholds are practitioner conventions. Do not claim causal effects.

### 1.2 Cialdini's principles (motivation supply)

Seven principles [V: https://www.influenceatwork.com/7-principles-of-persuasion/]: reciprocity, scarcity, authority, consistency, liking, social proof, and unity (added later).

Score the "genuine, verifiable" version of each signal in the Persuasion/Motivation Index (MPI). Score the fake or deceptive version only in the Dark Pattern Risk index (section 1.6).

| Principle | On-page signals | Extraction | Evidence |
|---|---|---|---|
| Social proof | Rating value and review count (JSON-LD `aggregateRating` cross-checked against visible DOM); review recency; verified-purchase labels; photo reviews; third-party review widgets; "bestseller" badges; "N sold" counts | JSON-LD plus DOM diff; widget script-domain detection | Moderate to strong (observational). Spiegel Research Center: five reviews lift purchase likelihood 270% vs none, peak at 4.0–4.7 stars, and diminish after the first five [S: https://spiegel.medill.northwestern.edu/How-Online-Reviews-Influence-Sales]. Prior: ≥5 reviews on the product page, rating 4.0–4.7, recent reviews |
| Scarcity (genuine) | Stock state in `Offer.availability` (InStock / LimitedAvailability); stated end dates on promotions; limited editions | JSON-LD; regex | Moderate. Barton et al. 2022 meta-analysis: 131 studies, 416 effect sizes. Demand scarcity works best for utilitarian goods, supply scarcity for experiences, time scarcity for high-involvement goods [S: https://ideas.repec.Org/a/eee/jouret/v98y2022i4p741-758.html]. Mathur distinguishes a stated deadline (genuine-capable) from "ends soon" with no deadline (information hiding) [V] |
| Reciprocity | Free-shipping threshold bar, free samples or gifts, discount for newsletter signup, free guides | Lexicon (IT: "omaggio", "gratis", "campione") | Weak to moderate (proxy only) |
| Commitment / consistency | Wishlist, saved cart, progress indicator in checkout, low-commitment first steps (add to cart without login) | DOM presence | Weak |
| Authority | Certifications, expert or doctor endorsements, press mentions, regulatory identifiers | Lexicon plus image alt text; LLM binary on the snippet | Weak to moderate |
| Liking | Real people and team photos, brand story page, human imagery | DOM and `alt` presence; LLM rubric for tone | Weak |
| Unity | Community, loyalty programme, shared-identity language | Lexicon; LLM binary | Weak |

### 1.3 Cognitive load and choice architecture

**Hick's law.** Decision time increases with the number and complexity of choices (Hick and Hyman, 1952–53) [V: https://lawsofux.com/hicks-law/]. It was established for simple stimulus-response tasks. Its transfer to shopping is weak to moderate.

**Miller's 7±2.** Do not use it as a hard threshold. Modern work (Cowan) suggests about 4 chunks [V: https://lawsofux.com/millers-law/]. Weak.

**Choice overload: mixed evidence**
- Iyengar and Lepper (2000): of those who stopped at the jam stand, 3% bought with 24 options vs 30% with 6. More people stopped with 24 options (60% vs 40%) [V: https://faculty.washington.edu/jdb/345/345%20Articles/Iyengar%20%26%20Lepper%20(2000).pdf].
- Scheibehenne, Greifeneder and Todd (2010): 63 conditions from 50 experiments, N = 5,036, mean effect virtually zero with high variance [S: https://ideas.repec.org/a/oup/jconrs/v37y2010i3p409-425.html].
- Chernev, Böckenholt and Goodman (2015): 99 observations, N = 7,202. Overload becomes significant given four moderators: choice-set complexity, decision-task difficulty, preference uncertainty, and a minimize-effort goal [S: https://www.kellogg.northwestern.edu/academics-research/research/detail/2015/when-product-assortment-leads-to-choice-overload-a-conceptual/]. A 2024 review also covers moderators [S: https://pmc.ncbi.nlm.nih.gov/articles/PMC11111947/].

**Implication for measurement.** Compute a "Choice Architecture" score, not "fewer is better".

| Indicator | Extraction |
|---|---|
| Assortment size per listing page | Count of product cards on the page |
| Decision aids present on large listings | Filters, sort, search, comparison, "bestseller" or "recommended" badges, defaults |
| Option complexity | Variant dimensions per product page |
| Competing primary CTAs per viewport | Computed-style counts |

Scoring rule: penalize only when assortment is large and decision aids are absent. Evidence: Weak to moderate, because the research is contested.

**Cognitive load proxies** (feed the Clarity and Cognitive Load index, CCL)
- Words above the fold.
- Distinct fonts and colors.
- Interactive elements per viewport.
- Auto-rotating carousels and simultaneous animations.
- Popups per session.
- Visual complexity (section 1.7).
- Text readability (section 1.7).

**Information scent** (Pirolli and Card, 1999: users follow proximal cues such as link labels toward expected value [S: https://en.wikipedia.org/wiki/Information_foraging])

| Indicator | Extraction |
|---|---|
| Clicks from home to a product page, to the cart, and to return/shipping information | Replayed trace; minimum click depth per canonical task |
| Descriptive link text (not "click here"/"scopri di più") | Anchor-text lexicon plus LLM binary on a sample |
| Breadcrumbs; search box with autocomplete; useful empty-search page | DOM presence; one scripted test query |
| Label-to-goal semantic match for canonical tasks (e.g. "find the returns policy") | Embedding similarity or LLM with a closed label set |

Evidence: Moderate. The theory is strong, but concrete scoring varies.

**Visual hierarchy and F/Z patterns**
- NN/g's F-pattern study dates from 2006, with 232 users, and describes a "rough, general shape". The 2017 follow-up documented layer-cake, spotted, marking, bypassing and commitment patterns [V: https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content-discovered/; S: https://nngroup.com/articles/text-scanning-patterns-eyetracking/].
- Use this only as a placement heuristic: value proposition and primary CTA in the top-left or first viewport, with headings and bullets that start with key words.
- Do not score "conformance to an F".
- Evidence: Weak to moderate.

### 1.4 Trust, credibility and perceived-risk reduction

**Stanford Web Credibility Guidelines** [V: https://credibility.stanford.edu/guidelines/index.html]. The research base is a study of more than 4,500 people over three years (perceptions, not purchase behavior). The ten guidelines:
1. Verify information accuracy.
2. Show a legitimate organization.
3. Highlight expertise.
4. Show the real people behind the site.
5. Make contact easy.
6. Professional design.
7. Usability and utility.
8. Regular updates.
9. Limit promotional content, including pop-ups.
10. Eliminate errors.

**Fogg prominence-interpretation theory.** Credibility judgments depend on whether an element is noticed (prominence) and how it is interpreted [S: https://credibility.stanford.edu/publications.html]. Measure both. Prominence means rendered in the viewport on the product page or at checkout. Interpretation means a recognizable and real signal. In the Fogg et al. web-credibility study, a large share of free-text comments concerned visual design (I recall about 46%) [M, unverified].

**Baymard trust and risk numbers** [V: https://baymard.com/lists/cart-abandonment-rate]
- 19% did not trust the site with credit-card information.
- 13% found the returns policy unsatisfactory.
- 9% had too few payment methods.
- 20% found delivery too slow.

| Indicator (Trust and Risk-Reduction index, TRI) | Extraction | Evidence |
|---|---|---|
| Valid HTTPS everywhere on the golden path; no mixed content | Playwright response checks | Strong (baseline hygiene) |
| Contact page with phone, email, address; legal identifiers (VAT number / company registration) visible | Link and text lexicon; `tel:`/`mailto:`; Italian legal-notice patterns [M: Italian e-commerce law requires such identifiers, verify] | Moderate (Stanford guidelines 2, 4, 5) |
| Returns, shipping and privacy policy pages reachable in ≤2 clicks from the product page and checkout | Link discovery | Moderate (Baymard 13%) |
| Machine-readable policy: `hasMerchantReturnPolicy`, `shippingDetails` in JSON-LD; return window in days | JSON-LD parse. Google lists these as recommended Product/Offer properties [V: https://developers.google.com/search/docs/appearance/structured-data/merchant-listing] | Moderate |
| Recognized payment brands and trust seals near the payment area | Image alt, filename and script-domain lexicon (Visa, Mastercard, PayPal, Klarna, Trustpilot etc.) | Weak to moderate. Familiar brands (Visa, Mastercard, PayPal, Norton, Google) are trusted most; many people have no preference [S: https://baymard.com/research-articles/site-seal-trust, https://cxl.com/research-study/trust-seals/] |
| Copyright year / "last updated" recency; no obvious typos | Regex; optional LLM spell check | Weak (Stanford guideline 8) |
| Delivery time stated before checkout | Regex on the product page and cart | Moderate (Baymard 20%) |
| Risk reversal: money-back guarantee, free returns | Lexicon | Weak |

### 1.5 Price transparency, loss aversion and anchoring

**Hidden costs.** Baymard's current list page shows 40% "extra costs too high (shipping, tax, fees)" and 12% "couldn't see/calculate total order cost upfront" [V]. The 40% excludes "just browsing" respondents, who make up 42% of abandoners. Earlier surveys report higher figures on a different denominator: 47% (2023) and 48% (Feb 2024, n = 1,012 US adults) [S: https://www.emarketer.com/content/extra-costs-are-the-top-reason-consumers-abandon-online-carts]. A search summary also mentioned "39%" for 2025, which I could not confirm. Cite the 40% from the Baymard page and state the denominator.

**Price transparency index (PTI) indicators**

| Indicator | Extraction | Scoring |
|---|---|---|
| Total at checkout equals the cart total with itemized fees, and no unlabelled line item appears at the final step | Parse prices at the product page, cart and last pre-payment step; diff | 100 if Δ = 0 or fully itemized earlier; scale down by Δ% |
| Shipping cost or threshold visible before checkout (product page or cart) | Regex and DOM position | Binary |
| Tax/VAT stated as inclusive or exclusive | Lexicon | Binary |
| Reference (strikethrough) price: `<s>`/`<del>`/`line-through` with a discount | CSS and DOM | Detect only. Compliance requires the EU Omnibus statement of the "lowest price in the prior 30 days" |

**Anchoring and loss framing.** Detect strikethrough prices, "you save €X", "da €X", decoy tiers with a "most popular" badge, and free-shipping progress bars. Count them as persuasion (MPI) only if the reference price is verifiably legitimate. Evidence: Strong for anchoring in the lab. Loss aversion's real-world magnitude is debated [M].

**EU price-reduction rule** (Art. 6a Price Indication Directive, applicable from 28 May 2022 [S: https://sellforte.com/blog/omnibus-directive-how-does-it-impact-your-business, https://www.rpclegal.com/snapshots/consumer/spring-2022/european-commission-publishes-guidance-on-price-promotions-under-the-omnibus-directive/])
- The prior price is the lowest price applied in the previous 30 days.
- CJEU Case C-330/23 (Aldi Süd, 26 Sep 2024) confirmed that the percentage reduction must be computed from that 30-day lowest price [S: https://pagecrawl.io/blog/eu-omnibus-30-day-lowest-price-monitoring].
- An Italian lexicon check for "prezzo più basso degli ultimi 30 giorni" is deterministic.
- Verifying the true price history needs repeated crawls over time. That is a monitoring feature, not a single-visit audit.

### 1.6 Dark patterns as negative signals (Dark Pattern Risk index, DPR)

**Taxonomies**
- **Mathur et al. 2019** [V, from the PDF]. About 11K shopping sites, 53,180 product pages, 1,818 instances on 1,254 sites (~11.1%). 15 types in 7 categories. Counts below are instances / sites:
  - Sneaking: Sneak into Basket 7/7; Hidden Costs 5/5; Hidden Subscription 14/13.
  - Urgency: Countdown Timer 393/361; Limited-time Message 88/84.
  - Misdirection: Confirmshaming 169/164; Visual Interference 25/24; Trick Questions 9/9; Pressured Selling 67/62.
  - Social Proof: Activity Message 313/264; Testimonials of Uncertain Origin 12/12.
  - Scarcity: Low-stock Message 632/581; High-demand Message 47/43.
  - Obstruction: Hard to Cancel 31/31.
  - Forced Action: Forced Enrollment 6/6.
  - The authors say these are lower-bound counts. They also found 234 deceptive instances on 183 sites, and 22 third-party entities selling dark patterns as a service (https://arxiv.org/abs/1907.07032, https://doi.org/10.1145/3359183).
  - Their five characteristics are asymmetric, covert, deceptive, information-hiding and restrictive.
- **Brignull's deceptive.design** names more types: Nagging, Preselection, Fake Urgency, Fake Scarcity, Fake Social Proof, Comparison Prevention, Disguised Ads, Drip Pricing-style Hidden Costs and others [V: https://www.deceptive.design/types].
- **FTC, "Bringing Dark Patterns to Light" (Sep 2022)** exists, but I could only confirm the page, not the taxonomy details [V for existence: https://www.ftc.gov/reports/bringing-dark-patterns-light]. Search summaries mention design elements that induce false beliefs (fake timers, scarcity) and ones that hide or delay material information (fees) [S].

**Detection cookbook (replicate Mathur's methods)**

| Pattern | Deterministic check | Deception test | LLM role |
|---|---|---|---|
| Countdown timer | Time-format regex plus a text node that mutates every second (MutationObserver, as Mathur did) | Reload in a fresh context and after expiry. Deceptive if the timer resets with the same offer, or the offer remains valid after expiry (Mathur's two criteria: 157 deceptive instances on 140 sites) | None needed |
| Limited-time message | Regex ("ends soon", "solo per oggi") with no stated deadline | N/A. Information-hiding by definition [V] | Optional verification |
| Low-stock message | Regex ("only N left", "ultimi N pezzi") | Compare across visits and against `availability`. Mathur: 16 of 17 deceptive sites decremented on a schedule, 1 randomized; some used third-party scripts. Also flag a message on nearly all product pages | None |
| Activity / high-demand message ("N people viewing") | Regex plus third-party script-domain signatures | Re-visit: random or hard-coded values (Mathur: 29 deceptive instances on 20 sites) | Optional |
| Sneak into basket / pre-selected paid add-on | Diff cart line items vs what was added; `input:checked` near insurance, warranty, donation or card keywords | Deterministic | None |
| Hidden costs | Price diff across funnel (section 1.5) | Deterministic | None |
| Forced registration | Flow trace: payment unreachable without account | Deterministic | None |
| Visual interference / asymmetric choices | Computed contrast, size and position of "accept" vs "decline/reject" (cookie banners and modals) | Deterministic ratio | None |
| Consent-banner asymmetry | Reject-all on the first layer? Equal prominence? EDPB report: rejecting should not be harder than accepting [S: https://www.cnil.fr/en/edpb-adopts-final-report-outcome-cookie-banner-task-force]. Nouwens et al.: removing the opt-out from page 1 raised consent 22–23 pp [S: https://arxiv.org/abs/2001.02479v1] | Deterministic | None |
| Confirmshaming | Decline-button text inside modals | N/A | LLM binary with quoted text (Mathur: 169/164) |
| Trick questions | Checkbox label wording | N/A | LLM binary |
| Nagging | Count overlays and dialogs (`role=dialog`, fixed position covering a large viewport share), exit-intent popups, popups within N seconds | Count per session | None |
| Hard to cancel | Cancellation path in the account area | Needs an authenticated flow; usually out of scope | Policy-text LLM only |
| Pressured selling | Most expensive variant pre-selected | Deterministic | None |

**Effectiveness evidence** [V: https://academic.oup.com/jla/article/13/1/43/6180579]
- Acceptance of a dubious service: control 11.3%, mild dark patterns 25.8%, aggressive 41.9%.
- Less-educated subjects were more susceptible to mild patterns.
- Only aggressive patterns produced measurable backlash.
- Consequence: DPR must be a risk and trust signal. Never reward it.

**Automated-detection accuracy**
- AidUI (screenshot-based) reports P 0.66, R 0.67, F1 0.65 on 10 pattern types, with some above 0.82 [S: https://arxiv.org/abs/2303.06782v1].
- A GPT-4.1 vs experts study exists, but I could not extract the numbers [S: https://deceptive.design/articles/ux-experts-vs-ai-exploring-the-performance-of-large-language-models-and-humans-on-detecting-dark-patterns].
- Hence prefer DOM and behavioral tests over visual or LLM detection.

**Regulatory context** (for the "risk" label; not legal advice)
- **EU CPC sweep**, results published 30 Jan 2023 [S: https://ec.europa.eu/commission/presscorner/api/files/document/print/en/ip_23_418/IP_23_418_EN.pdf]:
  - 399 shops screened; 148 used at least one dark pattern.
  - 42 had fake countdown timers, 54 steered choices, 70 hid information, 23 hid subscription information.
- **DSA Art. 25** bans deceptive or manipulative interfaces for online platforms. It does not apply where the UCPD or GDPR already covers the practice, so it matters mainly for marketplaces [S: https://www.springlex.eu/en/packages/dsa/dsa-regulation/article-25/].
- **UCPD Annex I** blacklists falsely stating that a product is available only for a very limited time [M].
- **Withdrawal button.** Directive (EU) 2023/2673 applies from 19 June 2026 and requires an easy-to-find withdrawal function for online distance contracts [S: https://www.hoganlovells.com/en/publications/eu-consumer-protection-law-update-new-mandatory-withdrawal-button-what-online-traders-need-to-know]. It can now be audited for EU shops.
- **Digital Fairness Act.** A proposal was expected in autumn 2026 and covers dark patterns and subscription traps; I have not confirmed whether it has been published [S: https://electronlibre.info/articles/103953-digital-fairness-act-the-commission-wants-to-open-up-the-black-box-of-platform-design/].
- **European Accessibility Act.** Applies from 28 June 2025, covers e-commerce, exempts micro-enterprises [S: https://resignal.com/blog/the-eu-accessibility-act-what-ecommerce-websites-need-to-know-and-do/].

Wording: label output "dark-pattern risk signals", never "violations".

### 1.7 Emotional, aesthetic and readability

**First impressions and aesthetics**
- Lindgaard et al. (2006): judgments form within 50 ms [S: https://www.websiteoptimization.com/speed/tweak/blink/].
- Tractinsky et al. (2000): perceived aesthetics affected post-use perceived usability, while actual usability did not [S: https://academic.oup.com/iwc/article/13/2/127/898608].
- The aesthetic-usability effect is summarized at https://lawsofux.com/aesthetic-usability-effect/.
- Evidence: Strong for first-impression and perception effects; weak to moderate for conversion effects.

**Reinecke et al., CHI 2013** [V: https://kgajos.seas.harvard.edu/papers/reinecke13aesthetics.pdf]. Data: 450 websites, ratings collected at 500 ms exposure.

| Quantity | Fit | Predictors |
|---|---|---|
| Perceived colorfulness | R² = .78 | Hasler–Süsstrunk colorfulness (strongest, β = .58), saturation, number of image areas, quadtree leaves, text and non-text area, some color-name percentages |
| Perceived visual complexity | R² = .65 | Quadtree decomposition (number of leaves), space-based decomposition, and similar |
| Visual appeal after 500 ms | Adjusted R² = .48 | Models plus demographics (about half of the variance) |

How to use it:
- Compute the same features from a screenshot as "complexity" and "colorfulness" proxies. A Python implementation is feasible.
- Hasler–Süsstrunk formula [M]: M = √(σ²_rg + σ²_yb) + 0.3·√(μ²_rg + μ²_yb).
- These models predict perceived complexity and colorfulness, not conversion. Preferences vary by demographics and culture (Reinecke & Gajos 2014: https://iis.seas.harvard.edu/papers/reinecke14visual.pdf [S]). Flag extremes only; do not set an "ideal" complexity.

**Readability (Italian)**
- Gulpease index [S: https://www.preprints.org/manuscript/201811.0505, https://www.w3.org/WAI/RD/2012/easy-to-read/paper3]: 89 − 10·(letters/words) + 300·(sentences/words).
- Thresholds: <80 hard for primary-school-level readers, <60 hard for middle-school-level, <40 hard for high-school-level.
- Prior scoring for product copy: 80 → 100, 60 → 50, 40 → 0.
- Apply separately to the product description, shipping/returns policy and checkout copy. Legal text will naturally score lower; set a separate anchor.
- Evidence: Strong as a readability measure; weak as an engagement predictor.

**Tone** (pressure vs informative, empathic, jargon). LLM rubric only, binary per snippet with a quoted example. Weak.

---

## 2. Composite index design

### 2.1 Seven sub-indices

Each runs 0–100, higher is better, except DPR, which is risk.

| ID | Name | Contents (single-owner rule) | Prior weight in ERS | Det / LLM |
|---|---|---|---|---|
| FAI | Friction / Ability | Steps, fields, guest checkout, autocomplete, target size, axe label and name rules, form errors | 22% | ~95% / 5% |
| TRI | Trust and Risk Reduction | Section 1.4 | 20% | ~75% / 25% |
| PTI | Price and Cost Transparency | Section 1.5 | 15% | ~90% / 10% |
| CCL | Clarity and Cognitive Load | Prompt/CTA salience, choice architecture, information scent, readability, visual complexity | 15% | ~70% / 30% |
| PERF | Performance and Robustness | Lighthouse performance score (or CrUX field data), JS errors, failed requests | 18% | 100% / 0% |
| MPI | Persuasion / Motivation supply | Genuine social proof, scarcity, reciprocity, authority, liking, unity | 10% | ~65% / 35% |
| DPR | Dark Pattern Risk | Section 1.6 | Penalty only, not weighted | ~85% / 15% |

Naming note: "Speed Index" is already a Lighthouse metric. Call the sub-index "Performance" (PERF) to avoid confusion. The performance researcher owns its internals.

**Evidence-informed prior for weights.** Map Baymard's checkout-abandonment shares to sub-indices [V: https://baymard.com/lists/cart-abandonment-rate]. Shares are multi-select and sum above 100.

| Sub-index | Reasons mapped | Sum |
|---|---|---|
| PTI | Extra costs 40 + cannot see total 12 | 52 |
| TRI | Distrust with card 19 + returns 13 + too few payment methods 9 + slow delivery 20 | 61 |
| FAI | Account creation 18 + long or complex checkout 17 | 35 |
| PERF / robustness | Errors or crashes 17 | 17 |

Normalized, these give roughly TRI 37%, PTI 31%, FAI 21% and PERF 10%.
- This covers checkout only and excludes CCL, MPI and DPR.
- The proposed weights above blend this prior with Fogg's structure and Deloitte/Google's speed evidence (0.1 s mobile speed improvement = +8.4% retail conversion; 37 brands, 30M sessions; observational) [S: https://web.dev/case-studies/milliseconds-make-millions].
- They are priors, not estimates. Replace with fitted weights after calibration (section 5), and publish a weight-sensitivity analysis.

### 2.2 Normalization to 0–100

1. **Per-indicator mapping** with piecewise-linear functions using three anchors: poor → 0, acceptable → ~50–60, good → 100.
   - Use published thresholds where they exist: Core Web Vitals and Lighthouse bands, WCAG 24/44 px and 4.5:1, Baymard 8 fields, Gulpease 80/60/40, Spiegel review counts and rating band.
   - Otherwise use reference-corpus percentiles.
2. **Version the anchors.** Do not renormalize per batch, so scores stay comparable over time. Optionally also report a percentile against the corpus, as SUPR-Q does.
3. Binary indicators map to 0/100. LLM rubric items use closed labels {0, 1, 2} mapped to 0/50/100.
4. Reverse-score negatives.
5. Within a sub-index, use equal weights unless a severity weight exists (for example, Lighthouse a11y weights).

Lighthouse is the model for this approach. Its performance score is a weighted arithmetic mean of log-normal-curve metric scores, with bands 0–49, 50–89 and 90–100 (see section 3).

### 2.3 Aggregation

```
ERS_raw = Π_k (max(I_k, 10))^(w_k)        # weighted geometric mean over the 6 positive sub-indices
ERS     = ERS_raw × (1 − 0.30 × DPR/100)  # bounded dark-pattern penalty; coefficient is a prior
```

- Weights: FAI 22, TRI 20, PTI 15, CCL 15, PERF 18, MPI 10.
- Geometric aggregation is non-compensatory: a weak sub-index cannot be fully bought back by a strong one. This matches B=MAP, where all three elements must be present. The OECD/JRC handbook discusses this compensability trade-off [S: https://knowledge4policy.ec.europa.eu/sites/default/files/jrc47008_handbook_final.pdf, https://www.oecd.org/en/publications/handbook-on-constructing-composite-indicators-methodology-and-user-guide_9789264043466-en.html].
- Also report the radar profile and the "limiting factor" (the lowest sub-index).
- Critical blockers set a visible flag or cap (for example, checkout unreachable, or a confirmed deceptive timer). These caps are design choices, not empirical findings.
- **DPR aggregation** (noisy-OR):
  - `DPR = 100 × (1 − Π(1 − sev_p × conf_p))`.
  - Prior severities: 0.35–0.5 for confirmed deceptive patterns (resetting timer, fake stock, sneak-into-basket, pre-checked paid add-on); 0.10–0.15 for present but unproven patterns (timer present, low-stock message present, confirmshaming, nagging).

### 2.4 Avoiding double counting

- **One home per signal.** Keep a signal-to-index ownership table in the repo.
  - Forced registration belongs to FAI. It counts in DPR only if hidden or deceptive.
  - Hidden fees belong to PTI. DPR only flags a deceptive mechanism such as a sneak-in item.
  - JS errors belong to PERF, not FAI or TRI.
  - Contrast ratio belongs to CCL (CTA salience), not FAI.
  - The accessibility axe label and name rules belong to FAI. Do not also score "accessibility" elsewhere.
  - A countdown timer earns no MPI credit unless it passes the deception test; if it fails, it goes to DPR.
- **Correlation audit.** On a reference corpus of ≥100 shops, check indicator correlations. If |ρ| > ~0.8, merge or down-weight. Report Cronbach's α per sub-index and run a PCA, as the OECD/JRC handbook recommends.
- **Sensitivity.** Monte-Carlo perturb weights (±20%) and anchors (±5%) and report rank stability.

### 2.5 Reporting confidence

- **Per indicator:** method (deterministic / heuristic / LLM) and, for LLM items, vote agreement.
- **Per sub-index:** coverage (weight of observed indicators over total weight, with N/A excluded and weights renormalized) and the share of the score that is LLM-derived.
- **Overall:** a grade A/B/C plus an interval, for example "ERS 71 (65–76), confidence B".
  - Do not publish the index if coverage is below about 60%.
  - Never score an unreachable stage as 0. Mark "not assessable" and record why.
- **Agent failure is ambiguous.** Failure to reach checkout could be site friction or an agent limitation. Re-run with another seed or model and mark the item "unconfirmed" if the runs disagree.

### 2.6 Deterministic vs LLM-judged

**Deterministic** (Playwright, DOM, accessibility tree):
- Counts and sizes, computed styles and contrast, JSON-LD parsing, policy-link presence (IT/EN lexicon), price and cart arithmetic.
- Timer, stock and activity deception tests (reload and revisit), pre-checked inputs.
- Consent-banner symmetry, Gulpease, screenshot complexity and colorfulness.
- Lighthouse and CrUX, axe-core.

**LLM-judged, bounded.** Use only on extracted snippets, never whole pages:
- CTA label clarity, value-proposition clarity, tone.
- Confirmshaming, trick-question wording, testimonial authenticity cues.
- Semantic information-scent match, trust-cue interpretation.

**How well LLMs agree with experts**
- GPT-4o (Nielsen heuristics) found only 21.2% of the issues human experts found, plus 27 new ones, with hallucinated false positives [S: https://arxiv.org/abs/2506.16345v1].
- A study of 850+ evaluations over 30 websites reported pairwise κ ≈ 0.50 (84% exact agreement) for issue detection. For severity, weighted κ ≈ 0.63 but only 56% exact agreement [S; paper not confirmed, candidate https://arxiv.org/pdf/2507.02306].
- GPT-4 on UI mockups was generally accurate on poor designs, but worse on iterated, improved ones (Duan et al., CHI 2024) [S: https://people.eecs.berkeley.edu/~bjoern/papers/duan-heuristic-chi2024.pdf].
- Implication: use LLM presence and absence judgments, not severity scales. Weight LLM-derived scores lower in the sub-index and in the confidence grade.

**Reproducibility protocol**
1. **Separate discovery from measurement.** The LLM agent navigates and discovers URLs and selectors. Deterministic scripts then measure on the recorded, replayable trace.
2. **Fixed rubric** with versioned prompt text, a closed label set, and a rubric hash stored in each report.
3. **Structured outputs.**
   - Anthropic's `output_config.format` with a JSON schema uses constrained decoding [V: https://platform.claude.com/docs/en/build-with-claude/structured-outputs].
   - Numeric `minimum`/`maximum` and string-length constraints are not supported, so encode scales as enums and validate the range afterward.
4. **Evidence citation.** Each verdict must include {selector or node id, verbatim text snippet}. A verifier script checks that the snippet exists at that node in the stored DOM snapshot. Discard ungrounded verdicts.
5. **Temperature caveat.**
   - "Temperature 0" is unavailable on current Claude models: Sonnet 5.5 returns HTTP 400 for non-default sampling parameters [V: https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide]. A third-party migration note says Opus 4.7 behaves the same [S: https://openrouter.ai/docs/guides/evaluate-and-optimize/model-migrations/claude-4-7].
   - Where it is available, a June 2026 preprint reports that temperature 0 is necessary but not sufficient. At default temperature, items near the decision boundary flipped in up to about 50% of repeated runs. Even under forced greedy decoding, 1–2 of 7 borderline items stayed non-reproducible across 690 calls [S, preprint: https://huggingface.co/papers/2606.26185.md].
   - Its recommended mitigation that survives parameter deprecation is multiple grading epochs with variance reporting.
6. **Multi-sample voting.** Run n = 3–5 samples and take the majority. Report per-item agreement. Items under about 2/3 agreement are "unstable": exclude them or down-weight them.
7. **Memoization.** Cache LLM verdicts keyed on SHA-256 of the normalized snippet plus rubric version plus model snapshot id. An unchanged page then returns identical verdicts.
8. **Run conditions.** Fixed viewports (e.g. 1366×768 and 390×844), locale it-IT, fresh browser context, logged consent-banner handling, two timed visits for time-varying checks. Store DOM, screenshots and HAR with hashes.
9. **Safety.** Stop before payment submission; never place real orders.

---

## 3. Existing industry scoring systems (for comparison and borrowing)

| System | How it scores | Borrowable idea | Source |
|---|---|---|---|
| **Baymard UX benchmark** | 344 sites rated against 1,254 platform-specific guideline instances (desktop 442, mobile web 429, mobile app 383). The methodology page says 810 guidelines, so the counts are inconsistent. 7-point guideline rating (Adhered High/Low, Neutral, Issue resolved, Violated Low/High, N/A). Importance = severity (Interruption / Disruptive / Harmful) × frequency (A Few … All) → Detail / Impactful / Essential. "Weighted multi-parameter algorithm with self-healing normalization" (50% state-of-the-art definition, 50% distribution across the benchmarked sites). Five tiers: poor / mediocre / decent / good / perfect | Severity × frequency weighting; N/A excluded from the denominator; periodic re-normalization | [V] https://baymard.com/ux-benchmark, https://baymard.com/research/methodology |
| **Lighthouse performance** | Weights FCP 10, SI 10, LCP 25, TBT 30, CLS 25; INP present with weight 0. Metric scores via log-normal curves from HTTP Archive; bands 0–49 / 50–89 / 90–100. Per the docs, the 25th percentile maps to 50 and the 8th percentile to 90. The docs page still headlines "Lighthouse 10"; the weights on `main` are unchanged. npm latest is 13.5.0 (published 2026-09-18) | Curve-based normalization with control points; fixed public weights | [V] https://raw.githubusercontent.com/GoogleChrome/lighthouse/main/core/config/default-config.js, https://developer.chrome.com/docs/lighthouse/performance/performance-scoring, https://registry.npmjs.org/lighthouse |
| **Lighthouse accessibility** | Weight by axe impact: critical 10, serious 7, moderate 3, minor 1; manual audits 0. Includes `target-size` (7). v13.0.0 (2025-10-10) adjusted these weights | Impact-weighted rule scoring | [V] same config |
| **Lighthouse "agentic-browsing" category** | Present in current `main`: agent accessibility tree, WebMCP, llms.txt audits, fractional display. Not an engagement metric | Possible "agent-readiness" side check | [V] same config |
| **Google HEART** | Happiness, Engagement, Adoption, Retention, Task success, with a Goals-Signals-Metrics (GSM) process (Rodden, Hutchinson & Fu, CHI 2010) | Map our indices: Task success → FAI; Happiness → SUPR-Q-style appeal and trust proxies. Adoption and Retention are not observable by a synthetic agent | [S] https://www.productcompass.pm/p/the-google-heart-framework, https://ixd.prattsi.org/2018/12/googles-heart-framework-measuring-and-tracking-progress-towards-key-goals/ |
| **Contentsquare** | Frustration Score (rage clicks, excessive scrolling, slow loads, errors). Zone metrics: exposure rate (≥150 ms), attractiveness rate, click recurrence, time before first click. Vendor benchmark of 99B sessions on 6,500 sites; the vendor claims that 1.5 pp fewer rage clicks per page adds +1 page view (correlational, vendor claim). Hotjar signals are analogous (not re-fetched) | Observable proxies for frustration; exposure x click structure for CTA salience | [S] https://contentsquare.com/guides/digital-experience-benchmark/frustration/, https://contentsquare.com/guides/heatmaps/zone-based-heatmaps/ |
| **Microsoft Clarity** | Semantic signals: rage clicks, dead clicks (no feedback in reasonable time), excessive scrolling, quick backs, JavaScript and click errors, smart events, funnels | Use as calibration targets (section 5), and as ideas for synthetic probes such as dead-click tests | [V] https://learn.microsoft.com/en-us/clarity/insights/semantic-metrics |
| **PURE** | Experts rate each task step 1–3 (1 easy, 2 some cognitive load, 3 hard or some users likely fail). Task score is the sum of step ratings. Worst-case coloring: one red step makes the task and product red. At least two evaluators, then reconcile. r ≈ 0.5 with SEQ and SUS, r ≈ 0.6 with SUPR-Q; no significant correlation with task time or completion | Step-level, worst-case-aware scoring suits the golden path. Also a realistic validity ceiling (about 25–36% variance explained) | [V] https://measuringu.com/pure/, https://measuringu.com/wp-content/uploads/2016/05/CHI-pure2016.pdf |
| **Nielsen 10 heuristics** | Ten heuristics (page last updated 2024-01-30). Severity 0–4 (not a problem, cosmetic, minor, major, catastrophe), combining frequency, impact and persistence; Nielsen suggests the mean of three evaluators | Use as an LLM checklist, but collapse severity to presence or absence plus three levels (see section 2.6) | [V] https://www.nngroup.com/articles/ten-usability-heuristics/, https://www.nngroup.com/articles/how-to-rate-the-severity-of-usability-problems/ |
| **SUPR-Q** | 8 items across usability, credibility/trust, appearance and loyalty; scores reported as percentiles against a database of 200 websites (more than 10,000 participants; about 90% North American respondents) | Dimension structure closely matches FAI/CCL, TRI and CCL; percentile reporting | [V] https://measuringu.com/suprq/ |
| **GA4** | Engaged session = longer than 10 s, or ≥1 key event, or ≥2 page or screen views. Engagement rate = engaged sessions / sessions. Bounce rate = 1 − engagement rate | Calibration target | [V] https://support.google.com/analytics/answer/12195621 |

---

## 4. Baymard figures to quote

From https://baymard.com/lists/cart-abandonment-rate [V]. Percentages exclude "just browsing", which is 42% of abandoners. The average cart abandonment across 50 studies is 70.22%.

| Reason | Share |
|---|---|
| Extra costs too high | **40%** |
| Delivery too slow | 20% |
| Did not trust the site with card info | 19% |
| Site wanted account creation | 18% |
| Too long or complicated checkout | 17% |
| Website errors or crashes | 17% |
| Returns policy unsatisfactory | 13% |
| Couldn't see or calculate total cost upfront | 12% |
| Credit card declined | 10% |
| Insufficient payment methods | 9% |
| Unknown | 7% |

Note: figures are the percent of abandoners, multi-select, with no survey date or sample size given on the page. Other Baymard surveys give 47% (2023) and 48% (Feb 2024, n = 1,012) for extra costs on a different denominator (see section 1.5).

---

## 5. Validity caveats, KPI phrasing, calibration

### 5.1 What a synthetic agent cannot measure
- **Real emotion and intent**, and motivation in B=MAP. Only the supply of motivators is observable.
- **Individual and demographic differences.** Aesthetic preferences vary by age, education and culture (Reinecke & Gajos 2014 [S]).
- **Context outside the page:** brand familiarity, price competitiveness, traffic mix, offline reputation, post-purchase experience.
- **Variant and state effects:**
  - A/B variants and personalization (the agent sees one variant).
  - Geo and cookie state, bot-detection differences, consent-banner state.
  - Authenticated flows such as cancellation and account experience.
  - Per-session dynamic timers and stock values, which need repeat visits.
- **Agent realism.** LLM agent behavior differs from humans. UXAgent's authors found the simulated behavior data useful for iterating study designs but "not entirely realistic", and noted LLM-agent biases [S: https://arxiv.org/pdf/2504.09407].
- **Agent failure vs site friction** are confounded (section 2.5).
- **Detection limits.** Mathur's counts are lower bounds. Their crawler reached checkout on only 66 of 100 sampled pages and stopped before payment [V]. Expect the same blind spots at the payment step.

### 5.2 Naming and phrasing
- Overall: "**Engagement Readiness Score**" (structural, predicted). Never "engagement score" or "conversion score".
- Sub-index display names: "Predicted friction", "Persuasion cues present", "Trust signals observable", "Price-transparency signals", "Clarity and cognitive-load proxies", "Performance", "Dark-pattern risk signals (not a legal finding)".
- Each KPI line carries a type tag: Observed (deterministic fact), Inferred (LLM-judged), or Not assessable.
- Do not claim that fewer choices or a higher rating cause conversion. Say "associated with" and cite the source and its strength.

### 5.3 Calibrating against real analytics (optional)
- **Targets.**
  - GA4 per landing page or template: engagement rate, bounce rate, add-to-cart rate, begin-checkout to purchase rate (GA4 e-commerce events).
  - Clarity: rage-click %, dead-click %, excessive-scroll %, quick-back %, JavaScript/click errors.
  - Contentsquare frustration score if available.
- **Method.**
  - Join audited pages or sites to their behavioral metrics.
  - Compute Spearman ρ (index vs outcome) with bootstrap confidence intervals.
  - Control for traffic source, device, price tier and seasonality.
  - Hold out data.
  - Preregister hypotheses (for example, FAI should correlate negatively with checkout-start-to-purchase drop-off).
  - Then regress the outcome on sub-indices (ridge or logistic) to replace the prior weights, but only with enough sites; otherwise keep the priors.
- **Expectations.** Do not expect high correlations. PURE's published validity (r ≈ 0.5–0.6 with perceived-usability scales; none with task time or completion) is a realistic ceiling for expert-style scoring [V]. Reinecke's models explain about half the variance of perceived appeal in lab ratings, not of conversion [V].
- **Guard against circularity.** Do not tune anchors on the same data used to validate.
- **Report drift.** Track calibration quality per release of the rubric and anchors.

---

## 6. Verification status and discrepancies

**Read directly this session [V]:**
- Lighthouse `default-config.js` (weights, accessibility weights, INP weight 0, and the new agentic-browsing category) and the npm release dates.
- Mathur et al. PDF (taxonomy table, counts, deception criteria).
- Reinecke et al. PDF (regression tables).
- Iyengar and Lepper PDF.
- Baymard pages (cart-abandonment list, methodology, form-field blog, benchmark).
- PURE, SUPR-Q, Nielsen, WCAG 2.5.8, GA4, Clarity, Stanford guidelines, deceptive.design types, Luguri and Strahilevitz abstract.
- The Anthropic Sonnet 5.5 migration guide and structured-outputs pages.

**Search or secondary summaries only [S]:**
- Scheibehenne 2010, Chernev 2015, Barton 2022, Spiegel numbers.
- EU sweep, DSA Art. 25, Omnibus and CJEU C-330/23, the withdrawal button, the Digital Fairness Act timing.
- AidUI, the LLM heuristic-evaluation studies, the temperature-reproducibility preprint, UXAgent.
- Deloitte/Google, Contentsquare definitions, SUPR-Q database size, HEART.

**From memory, check before use [M]:**
- The Hasler–Süsstrunk formula and Fogg's spark/facilitator/signal prompt types.
- The Keystroke-Level Model operators and WCAG 1.4.3 / 1.4.11 thresholds.
- UCPD Annex I wording on "limited time" claims.
- Italian legal-identifier requirements for e-commerce sites.
- The Fogg credibility study's design-look share (about 46%).

**Discrepancies found:**
- A WebFetch summary of the Lighthouse GitHub releases page gave wrong dates. I used the npm registry instead: 13.0.0 on 2025-10-10, 13.5.0 on 2026-09-18.
- The Lighthouse docs page still says "Lighthouse 10", while the code on `main` carries the same weights.
- Baymard's extra-costs figure varies by survey and denominator (40% on the list page; 47–48% in the 2023 and 2024 surveys; an unconfirmed 39% for 2025). The "1,254 guidelines" and "810 guidelines" counts also conflict.
- The FTC taxonomy details were not extractable from the page I fetched.

---

## Source URLs (grouped)

- **Behavior and persuasion:**
  - https://www.behaviormodel.org/
  - https://www.behaviormodel.org/ability
  - https://behaviordesign.stanford.edu/resources/fogg-behavior-model
  - https://www.influenceatwork.com/7-principles-of-persuasion/
  - https://credibility.stanford.edu/guidelines/index.html
  - https://credibility.stanford.edu/publications.html
- **Cognition and choice:**
  - https://lawsofux.com/hicks-law/
  - https://lawsofux.com/millers-law/
  - https://lawsofux.com/aesthetic-usability-effect/
  - https://faculty.washington.edu/jdb/345/345%20Articles/Iyengar%20%26%20Lepper%20(2000).pdf
  - https://ideas.repec.org/a/oup/jconrs/v37y2010i3p409-425.html
  - https://www.kellogg.northwestern.edu/academics-research/research/detail/2015/when-product-assortment-leads-to-choice-overload-a-conceptual/
  - https://pmc.ncbi.nlm.nih.gov/articles/PMC11111947/
  - https://en.wikipedia.org/wiki/Information_foraging
  - https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content-discovered/
  - https://nngroup.com/articles/text-scanning-patterns-eyetracking/
  - https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html
- **Social proof and scarcity:**
  - https://spiegel.medill.northwestern.edu/How-Online-Reviews-Influence-Sales
  - https://ideas.repec.Org/a/eee/jouret/v98y2022i4p741-758.html
- **Aesthetics and readability:**
  - https://kgajos.seas.harvard.edu/papers/reinecke13aesthetics.pdf
  - https://iis.seas.harvard.edu/papers/reinecke14visual.pdf
  - https://www.websiteoptimization.com/speed/tweak/blink/
  - https://academic.oup.com/iwc/article/13/2/127/898608
  - https://www.preprints.org/manuscript/201811.0505
  - https://www.w3.org/WAI/RD/2012/easy-to-read/paper3
- **Baymard:**
  - https://baymard.com/lists/cart-abandonment-rate
  - https://baymard.com/blog/checkout-flow-average-form-fields
  - https://baymard.com/ux-benchmark
  - https://baymard.com/research/methodology
  - https://baymard.com/research-articles/site-seal-trust
  - https://cxl.com/research-study/trust-seals/
  - https://www.emarketer.com/content/extra-costs-are-the-top-reason-consumers-abandon-online-carts
- **Dark patterns and regulation:**
  - https://arxiv.org/abs/1907.07032
  - https://doi.org/10.1145/3359183
  - https://academic.oup.com/jla/article/13/1/43/6180579
  - https://www.deceptive.design/types
  - https://www.ftc.gov/reports/bringing-dark-patterns-light
  - https://ec.europa.eu/commission/presscorner/api/files/document/print/en/ip_23_418/IP_23_418_EN.pdf
  - https://www.springlex.eu/en/packages/dsa/dsa-regulation/article-25/
  - https://www.hoganlovells.com/en/publications/eu-consumer-protection-law-update-new-mandatory-withdrawal-button-what-online-traders-need-to-know
  - https://www.iubenda.com/en/blog/online-withdrawal-function-eu-directive-2023-2673/
  - https://sellforte.com/blog/omnibus-directive-how-does-it-impact-your-business
  - https://www.rpclegal.com/snapshots/consumer/spring-2022/european-commission-publishes-guidance-on-price-promotions-under-the-omnibus-directive/
  - https://pagecrawl.io/blog/eu-omnibus-30-day-lowest-price-monitoring
  - https://electronlibre.info/articles/103953-digital-fairness-act-the-commission-wants-to-open-up-the-black-box-of-platform-design/
  - https://www.cnil.fr/en/edpb-adopts-final-report-outcome-cookie-banner-task-force
  - https://arxiv.org/abs/2001.02479v1
  - https://resignal.com/blog/the-eu-accessibility-act-what-ecommerce-websites-need-to-know-and-do/
  - https://arxiv.org/abs/2303.06782v1
  - https://deceptive.design/articles/ux-experts-vs-ai-exploring-the-performance-of-large-language-models-and-humans-on-detecting-dark-patterns
- **Scoring systems and analytics:**
  - https://raw.githubusercontent.com/GoogleChrome/lighthouse/main/core/config/default-config.js
  - https://developer.chrome.com/docs/lighthouse/performance/performance-scoring
  - https://registry.npmjs.org/lighthouse
  - https://measuringu.com/pure/
  - https://measuringu.com/wp-content/uploads/2016/05/CHI-pure2016.pdf
  - https://measuringu.com/suprq/
  - https://www.nngroup.com/articles/ten-usability-heuristics/
  - https://www.nngroup.com/articles/how-to-rate-the-severity-of-usability-problems/
  - https://www.productcompass.pm/p/the-google-heart-framework
  - https://ixd.prattsi.org/2018/12/googles-heart-framework-measuring-and-tracking-progress-towards-key-goals/
  - https://contentsquare.com/guides/digital-experience-benchmark/frustration/
  - https://contentsquare.com/guides/heatmaps/zone-based-heatmaps/
  - https://learn.microsoft.com/en-us/clarity/insights/semantic-metrics
  - https://support.google.com/analytics/answer/12195621
  - https://developers.google.com/search/docs/appearance/structured-data/merchant-listing
  - https://web.dev/case-studies/milliseconds-make-millions
  - https://knowledge4policy.ec.europa.eu/sites/default/files/jrc47008_handbook_final.pdf
  - https://www.oecd.org/en/publications/handbook-on-constructing-composite-indicators-methodology-and-user-guide_9789264043466-en.html
- **LLM reproducibility and agents:**
  - https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide
  - https://platform.claude.com/docs/en/build-with-claude/structured-outputs
  - https://openrouter.ai/docs/guides/evaluate-and-optimize/model-migrations/claude-4-7
  - https://huggingface.co/papers/2606.26185.md
  - https://arxiv.org/pdf/2504.09407
  - https://arxiv.org/abs/2506.16345v1
  - https://people.eecs.berkeley.edu/~bjoern/papers/duan-heuristic-chi2024.pdf