# Stato del lavoro (11 ottobre 2026)

## Fatto
- Catalogo KPI (`docs/engagement-kpi.md`), audit deterministico multi-pagina, journey, scoring, giudizi, report,
  server MCP, CLI `jev-engage`, plugin Claude Code.
- Ruoli Jev (fase E): Jev giudice delle rubriche oggettive (una richiesta per pagina, evidenza scelta fra gli snippet
  offerti, escalation a Claude sotto soglia), Jev pilota di default dei journey con `TYPESAFE_API_KEY` (il loop
  originale `Agent` + `model.choose`, solo avvolto per la misura), Jev riserva del crawler (una richiesta per stadio,
  al più sei per profilo), conteggio di chiamate e tempi, `scripts/smoke_engagement.py` per la prova live. Claude
  giudica confirmshaming e proposta di valore e le escalation di Jev.
- Revisioni per workstream con revisori indipendenti e regole Fable; documentazione allineata.
- Revisione finale verificata (10 ottobre), quattro giri:
  1. Intera feature e modifiche Jev, 4 lenti (riuso del loop Jev, sicurezza e regole di AGENTS.md, correttezza,
     validità delle misure e coerenza documenti/codice): 17 rilievi, 16 confermati da due verificatori avversariali e
     corretti (tra gli altri: richieste di testo fallite contate, passo del journey scritto subito dopo l'input e poi
     misurato, chiave di un browser `cdp:` mai salvata, controllo email più largo, smoke senza verdetti in cache,
     costi di un journey abbandonato, `anchors.v2` con due ancore provvisorie, "Svuota carrello" mai offerto a Jev,
     report e CLI in italiano coerente).
  2. Sulle correzioni del giro 1: 9 rilievi corretti (copertura arrotondata una volta sola, carrello vuoto letto da
     una frase e non da un controllo, chiave redatta prima del taglio del messaggio, smoke che giudica una copia
     quando l'audit ha già verdetti, runner rilasciato a fine journey, testi).
  3. Sulle correzioni del giro 2: 4 rilievi corretti (controllo "Empty cart" accanto ad altro testo, quota LLM
     complessiva arrotondata una volta sola, errori raggruppati nello smoke, README).
  4. Sulle correzioni del giro 3: 1 regressione corretta (frase di carrello vuoto con formattazione inline) e un test
     reso esplicito come guardia. Quarto giro raggiunto: il ciclo si chiude qui come da piano.
- Gate finale: `ruff` pulito, suite `pytest` completa, `node --check` sui quattro JS, `uv build`,
  `claude plugin validate .` e `--strict`, `jev_ultrafast/model.py` e `questions.py` invariati rispetto al fork.
- Dopo le prove sul Mac (11 ottobre):
  - test macOS (orario di avvio via `ps`, aree con tolleranza) e `smoke_summary.json` con percorsi relativi e scelte di
    Jev;
  - passo di consenso nei journey prima della prima osservazione (`settings.consent`, default `auto`, la catena
    dell'audit, `source: "consent"`, mai un'azione del pilota né Jev; niente click su una pagina di checkout; un click
    che porta su un altro sito rende l'esito non valutabile, `consent_left_shop`), esposto in servizio, MCP, CLI e smoke;
  - scroll del crawler verificato prima di cercare un controllo (istantaneo, sull'elemento stesso anche in uno
    scroller interno, diagnostica `scroll` nei probe), dopo il CTA del carrello non trovato su mobile;
  - above the fold = centro dell'elemento nella prima schermata (`checks.v2`);
  - la rotella di `browser.py` scorre anche su mobile (verificato, fissato da un test, nessuna modifica).
  - Revisione verificata in tre giri (20 rilievi confermati da due verificatori e corretti), poi gate completo.
- Prove live sul Mac (11 ottobre, pomeriggio e sera; artefatti locali, nessun numero qui finché non è committato uno
  `smoke_summary.json`):
  - lo scroll verificato fa raggiungere il checkout dello shop locale (`via checkout_cta`);
  - fixture: sul profilo mobile la categoria aveva più di due schermate di filtri prima dei prodotti e Jev, dopo uno
    scroll, rispondeva BLOCKED; ora i filtri sono chiusi dietro "Filtri" sui telefoni (desktop invariato);
  - con l'obiettivo generico "Aggiungi al carrello un prodotto" Jev risponde BLOCKED già sulla home in tutte le prove
    (limite di Jev, non un difetto del harness); con "Aggiungi al carrello un paio di scarpe da corsa" arriva al
    carrello e l'oracolo passa: è il nuovo obiettivo di default dello smoke sulla fixture.

## Da fare (all'utente)
1. Prova live con la chiave TypeSafe in locale: `uv run python scripts/smoke_engagement.py` (fixture locale) e
   `--url <shop>`; poi committare `smoke_summary.json`. Solo dopo i numeri di Jev (latenze, accordo delle etichette,
   richieste) possono entrare nel README o nei documenti.
2. Decisione aperta: il plugin deve approvare da solo `get_judgment_tasks` per i giudici Claude in background?
3. Fase 2 del catalogo (proposta): calibrazione con GA4/Clarity, axe-core completo, CrUX, checkout oltre il primo
   step, ancore per lingua (Flesch per l'inglese).
