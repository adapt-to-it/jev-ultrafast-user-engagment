# Stato del lavoro (10 ottobre 2026)

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

## Da fare (all'utente)
1. Prova live con la chiave TypeSafe in locale: `uv run python scripts/smoke_engagement.py` (fixture locale) e
   `--url <shop>`; poi committare `smoke_summary.json`. Solo dopo i numeri di Jev (latenze, accordo delle etichette,
   richieste) possono entrare nel README o nei documenti.
2. Decisione aperta: il plugin deve approvare da solo `get_judgment_tasks` per i giudici Claude in background?
3. Fase 2 del catalogo (proposta): calibrazione con GA4/Clarity, axe-core completo, CrUX, checkout oltre il primo
   step, ancore per lingua (Flesch per l'inglese).
