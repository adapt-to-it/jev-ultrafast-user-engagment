# Jev Ultrafast → Engagement Readiness auditor per shop e-commerce

## Context
Il repo è un fork di `browser-use/jev-ultrafast`, un agente browser LLM con questo ciclo: pagina → elementi indicizzati → operazione + target → esecuzione. Le decisioni le prende TypeSafe; il testo dei campi lo scrive un modello OpenAI-compatible.

Vogliamo riusarlo per **misurare quanto uno shop è pronto all'engagement**:
- velocità
- disponibilità degli elementi chiave
- frizione nel percorso
- fattori psicologici (trust, trasparenza prezzo, persuasione genuina, dark pattern)

Tutti questi segnali vanno convertiti in KPI e indici 0–100 riproducibili.

Lo studio preliminare è fatto: tre ricerche su Sonnet (codice, metriche tecniche, psicologia → KPI) e il design architetturale su Fable. Questo piano è il risultato.

**Decisioni dell'utente**
1. Prima consegna = catalogo KPI + MVP funzionante.
2. Due modalità: audit deterministico multi-pagina (home, PLP, PDP, carrello, primo step del checkout; mai ordini) **più** journey dell'agente con un obiettivo in linguaggio naturale.
3. I giudizi LLM usano l'**abbonamento Claude**. Il tool gira dentro Claude Code come MCP server + plugin; l'API serve solo come fallback.
4. Profili device: mobile 390x844 con throttling stile Lighthouse, più desktop 1366x768.
5. Deleghe: ricerca e documentazione → **Sonnet**; implementazione → **Opus**; dubbi grossi → **Fable**.

**Fatti verificati che guidano il design**
- Claude Code **non** supporta MCP sampling. Quindi il server prepara task di giudizio, Claude Code (subagent Sonnet) giudica e rimanda i verdetti, il server li valida.
- `browser_harness` può collegarsi a un Chromium lanciato da noi (`BU_CDP_URL`). Però ha un buffer eventi di 500 (lossy per i byte di rete), un timeout IPC di 5 s e un marker nel titolo. Serve quindi un trasporto CDP diretto (`websockets`, già nel lock).
- Nel container c'è Chromium 141 in `/opt/pw-browsers`.
- Il tab in background attuale (`browser.py:23`) rende `visibilityState=hidden`: niente FCP/LCP. Gli audit useranno headless-new visibile.

## Architettura
**Teniamo CDP, niente Playwright.** Tutte le metriche sono primitive CDP o API in pagina, e Playwright aggirerebbe le guardie di freschezza e la regola "mai ritentare una mutazione".

**Modifiche minime al loop esistente**
- `jev_ultrafast/browser.py`: `Browser.__init__(url, *, transport=None, metrics=None, browser_context_id=None, background=True, navigate=True)` e `browser_operation(request, transport=None)`. Il percorso di default resta identico, così `tests/test_agent.py` e `scripts/measure_flights.py` continuano a funzionare.
- `jev_ultrafast/agent.py`: `Agent(url, goals, *, browser=None, policy=None, text_policy=None, ...)`. `predict` usa `(self.policy or choose)`, il fill usa `(self.text_policy or field_text)`. Il modello continua a emettere solo indici, mai selettori.

**Nuovo sottopacchetto `jev_ultrafast/engagement/`**

| Area | File |
|---|---|
| Fondamenta | `settings.py`, `schemas.py` (TypedDict condivisi, `schema_version: "engagement.v1"`), `transport.py` (`CdpTransport`, `DirectTransport` su websockets con coda eventi illimitata, `HarnessTransport`, `open_transport()`), `chrome.py` (`find_chromium`, `launch_chromium` headless-new con profilo temporaneo), `profiles.py` (profili device versionati come dati + igiene: contesto isolato per profilo, cache disabilitata, throttling CPU/rete, UA senza "Headless"), `store.py` (`RunStore` in `artifacts/engagement/<host>/<ts>/`, con `run.json`, `steps.jsonl`, `snapshots/`, `shots/`, `report.json`, `report.html`) |
| Raccolta | `vitals.js` (iniettato con `Page.addScriptToEvaluateOnNewDocument`: TTFB, FCP, LCP, CLS con session window più CLS post-input, event timing, longtask, LoAF, resource, errori, history, mutation counter; espone `mark()`, `since()`, `quiet()`), `audit.js` (una sola chiamata, documento intero, restituisce `AuditPayload`), `lexicon.py` (IT/EN estendibile), `collectors.py` (`PageCollector.load_page(url) -> PageRecord`), `pagetypes.py` (`classify()` → home/plp/pdp/cart/checkout/challenge/other) |
| Audit | `crawler.py` (`discover_funnel()` generico via link, JSON-LD e pattern URL, senza piani per sito), `checks.py` (KPI deterministici → `Observation`), `deception.py` (test Mathur in un secondo contesto isolato), `safety.py` (`CheckoutGuard`: niente click su "paga/conferma ordine", niente digitazione in `cc-*`/password, stop al primo step del checkout, blocco degli host esterni), `audit.py` (`audit_shop()`) |
| Journey | `journey.py` (`JourneyRunner` con policy `host` o `typesafe`; `steps.jsonl` scritto prima dell'observe), `friction.py`, `oracles.py` (verifiche indipendenti a set chiuso, es. `cart_contains_item_under_price`) |
| Giudizi e score | `rubrics/*.v1.json` (etichette chiuse), `judgments.py` (task da snippet ≤600 caratteri, citazione verbatim verificata sullo snapshot, maggioranza su 3, cache `sha256(rubric+versione+snippet+modello)`), `judges.py` (fallback CLI: `claude -p --json-schema` con abbonamento, API Anthropic, OpenAI-compatible), `anchors.json` (ancore, pesi e tabella di ownership versionati), `scoring.py`, `report.py` (JSON + HTML autocontenuto) |
| Interfacce | `service.py` (`EngagementService` con browser factory iniettabile), `mcp_server.py`, `cli.py` (`jev-engage audit\|journey\|judge\|score\|report\|list`) |

**Plugin Claude Code** (la root del repo è la root del plugin)
- `.claude-plugin/plugin.json`
- `.mcp.json`: `uv run --project ${CLAUDE_PLUGIN_ROOT} jev-engage-mcp`, artefatti in `${CLAUDE_PLUGIN_DATA}/runs`
- `skills/shop-readiness/SKILL.md`: orchestrazione `/jev-engagement:shop-readiness <url> [goal]`
- `skills/journey-driver/SKILL.md`: regole della policy host
- `agents/engagement-judge.md`: subagent Sonnet senza tool

**Tool MCP** (stdio, SDK `mcp>=2.3`, fallback `mcp>=1.30` FastMCP). Restituiscono sintesi e path, mai payload grandi.
- `audit_shop`, `get_run`
- `run_journey`, `journey_act`, `journey_finish`
- `get_judgment_tasks` (paginato a 15), `submit_judgments`, `finalize_judgments`
- `score_run`, `get_report`, `list_runs`

**Journey**: in Claude Code il default è `policy="host"`. Claude Code sceglie operazione e indice tra quelli osservati, e il tempo di decisione resta escluso dal tempo attribuibile al sito. Per le run ripetute da CLI c'è `policy="typesafe"`.

**Dipendenze**: `mcp`, `websockets` diretto, `pillow` spostato in core. Nuovi script `jev-engage` e `jev-engage-mcp`.

## KPI MVP e indici
Il catalogo completo, con ID, unità, ancore e fonti, va in `docs/engagement-kpi.md` e in `anchors.json`. Ogni segnale appartiene a un solo indice.

| Indice | Peso | KPI principali |
|---|---|---|
| **PERF** (velocità e robustezza) | 18 | TTFB, FCP, LCP, CLS, CLS post-input, TBT approssimato, LoAF, INP sintetico (dichiarato "latenza click agente"), byte totali e JS, richieste, quota terze parti, immagini sovradimensionate, errori console/HTTP, tempo di risposta alle azioni |
| **FAI** (frizione/ability) | 22 | search visibile/larghezza/autocomplete, filtri/sort/conteggio PLP, breadcrumb, CTA PDP above fold, varianti, carrello editabile, campi checkout (Baymard: 8 ideale, 11.3 medio), guest checkout, account forzato, attributi autocomplete, target 24/44 px, a11y base. Dal journey: successo da oracolo, azioni, rapporto azioni/minimo, tempo-sito, dead click, rage, backtrack, Lostness, overlay, navigazioni inattese |
| **TRI** (trust/rischio) | 20 | HTTPS e mixed content, contatti, P.IVA, link policy (resi ≤2 click), JSON-LD resi/spedizione, loghi pagamento, recensioni (conteggio, banda 4.0–4.7), tempi di consegna; *giudicato:* chiarezza resi |
| **PTI** (trasparenza prezzo) | 15 | prezzo visibile, coerenza JSON-LD/visibile, spedizione prima del checkout, soglia spedizione gratuita, IVA dichiarata, delta prezzo PDP→cart→checkout, dicitura Omnibus 30 gg sui prezzi barrati, costi non spiegati |
| **CCL** (chiarezza/carico cognitivo) | 15 | salienza CTA (contrasto, area, unicità), numero di CTA primarie, carico di scelta (penalizza solo assortimenti ≥24 senza filtri/sort), etichette generiche, Gulpease/Flesch, heading, label dei form, complessità visiva (provvisoria); *giudicato:* chiarezza della value proposition |
| **MPI** (persuasione genuina) | 10 | scarsità e urgenza accreditate solo se superano il test di inganno, reciprocità; *giudicati:* autorità, social proof ricca |
| **DPR** (rischio dark pattern, solo penalità) | — | noisy-OR severità×confidenza: timer che si resetta (0.9), finto low-stock (0.6), sneak-into-basket (0.9), add-on a pagamento pre-selezionati (0.6), asimmetria del cookie banner (0.3), overlay insistenti (0.3); *giudicati:* confirmshaming, trick question, abbonamento nascosto |

**Formula**

ERS = exp(Σ wᵢ·ln(max(Sᵢ, 10)) / Σ w) × (1 − 0.30·DPR/100)

- Normalizzazione piecewise-lineare con ancore pubblicate dove esistono: CWV, WCAG, Baymard, Gulpease 40/60/80, Spiegel. Le altre ancore sono marcate `provisional`.
- Copertura e grado: A ≥85%, B ≥70%, C ≥60%; sotto il 60% l'ERS non viene pubblicato.
- Una pagina non raggiungibile è "non valutabile", mai 0. Ogni KPI riporta `llm_share`.
- Linguaggio obbligatorio: "readiness", "predicted friction", "risk signals". Mai "engagement misurato" né "violazioni".

**Rimandato a fase 2**: axe-core completo, Speed Index, INP reale da CrUX, checkout oltre lo step 1, calibrazione con GA4 + Clarity (connettori già disponibili), altre lingue, cache calda.

## Esecuzione: workstream e modelli
Le firme dei contratti sono in `schemas.py`, `anchors.json`, `AuditPayload`, il dict di `choose()` e i nomi dei tool. Nessun subagent li cambia da solo.

| Fase | WS | Modello | File di proprietà | Dipende da |
|---|---|---|---|---|
| A | WS6a: catalogo KPI | Sonnet | `docs/engagement-kpi.md` (italiano: definizione, formula, unità, soglie, fonti, det/giudicato, indice proprietario, limiti) | i tre report di ricerca, salvati prima nello scratchpad |
| A | WS0: fondamenta | Opus | `engagement/{__init__,settings,schemas,transport,chrome,profiles,store}.py`, kwargs in `browser.py`, `pyproject.toml` (deps, script, stub cli/mcp), `tests/conftest.py` (server HTTP per fixture + skip se manca Chromium), `tests/test_engagement_transport.py` | — |
| A | WS4: giudizi, score, report | Opus | `engagement/{judgments,judges,scoring,report}.py`, `anchors.json`, `rubrics/*`, test relativi | solo `schemas.py` (si scrive per primo in WS0, oppure si concorda prima) |
| B | WS1: collector | Opus | `vitals.js`, `audit.js`, `lexicon.py`, `collectors.py`, `pagetypes.py`, `tests/fixtures/shop/*` (index, category, product, cart, checkout, dark, challenge), test | WS0 |
| B | WS2: audit deterministico | Opus | `crawler.py`, `checks.py`, `deception.py`, `audit.py`, `safety.py`, test | WS0, WS1 |
| B | WS3: journey | Opus | kwargs in `agent.py`, `journey.py`, `friction.py`, `oracles.py`, test | WS0, WS1, interfaccia di `safety.py` |
| C | WS5: MCP, CLI, plugin | Opus | `service.py`, `mcp_server.py`, `cli.py`, `.claude-plugin/`, `.mcp.json`, `skills/`, `agents/`, `tests/test_engagement_mcp.py` | WS2, WS3, WS4 |
| C | WS6b: documentazione | Sonnet | `docs/engagement-design.md`, README, AGENTS.md (regole engagement: niente ordini, niente dati personali, vocabolario, ancore versionate, claim solo con artefatti; i check includono i nuovi JS) | WS5 |
| C | WS7: integrazione | Opus | nessun file nuovo: end-to-end sulle fixture, un audit reale headless, correzioni nei file del proprietario | tutti |
| — | Dubbi bloccanti | **Fable** | per esempio: pesi e ancore se i dati reali li smentiscono, policy del consenso, bot detection | — |

Io coordino: lancio i workstream in parallelo dove la tabella lo consente, rivedo ogni diff contro i contratti ed eseguo i check. Alla fine faccio **commit e push** su `claude/nice-mccarthy-yf5hk1` (nessuna PR, salvo tua richiesta).

## Verifica
- **Check del repo**:
  - `uv sync`, `uv run ruff check .`, `uv run pytest`
  - `node --check` su `jev_ultrafast/static/app.js`, `jev_ultrafast/snapshot.js`, `engagement/vitals.js`, `engagement/audit.js`
  - `uv build`
- **Unit test offline**, senza API a pagamento:
  - score: interpolazione, media geometrica, DPR, soglie di pubblicazione, "non valutabile ≠ 0"
  - giudizi: citazione verbatim, etichette ignote, pareggi, cache
  - friction: dead/rage/backtrack/Lostness, tempo che esclude la decisione
  - `checks` e `classify` su payload JSON di fixture
  - guardia di sicurezza
  - report senza URL esterni
- **Integrazione con Chromium reale** (`/opt/pw-browsers`; i test vengono saltati se manca) su fixture servite in locale:
  - `visibility_state_at_load == "visible"` e FCP/LCP/CLS presenti
  - i byte CDP coincidono con le dimensioni dei file
  - il crawler arriva al carrello e si ferma allo step 1 del checkout; il server verifica che non arrivi nessun POST di pagamento
  - i test di inganno scattano sulla fixture `dark` e tacciono su quella pulita
  - un journey host con policy scriptata raggiunge l'oracolo; la guardia blocca "Paga ora"
- **Test MCP** con browser factory fittizia: paginazione ≤15 task, `get_report` restituisce path; `claude plugin validate .`.
- **End-to-end manuale**: `uv run jev-engage audit --url <shop reale>` in headless produce `report.html`; poi lo stesso flusso dallo skill del plugin in Claude Code, con giudizi via subagent Sonnet.

## Rischi principali
- **Bot detection e CAPTCHA**: rilevati come pagina `challenge` e dichiarati "non valutabili". Nessuna evasione oltre UA e lingua; per i journey si può usare `browser="harness"` sul Chrome reale.
- **Cookie banner**: misuriamo l'atterraggio così com'è, poi rifiutiamo se c'è un controllo di rifiuto. La scelta viene registrata.
- **iframe e shadow DOM chiusi** (widget recensioni, pagamenti): coperti solo in parte, e lo dichiariamo.
- **Durata dei tool**: un audit su due profili dura 2–4 minuti e Claude Code lo manda in background; lo skill fa polling con `get_run`.
- **Giudici**: tre Sonnet non sono rater indipendenti. Servono come controllo di stabilità e restano dichiarati in `llm_share`.
