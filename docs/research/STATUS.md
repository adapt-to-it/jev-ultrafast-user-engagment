# Stato del lavoro (pausa dell'8 ottobre 2026)

## Fatto
- Catalogo KPI (`docs/engagement-kpi.md`), audit deterministico multi-pagina, journey, scoring, giudizi, report,
  server MCP, CLI `jev-engage`, plugin Claude Code.
- Ruoli Jev (fase E): Jev giudice delle rubriche oggettive (una richiesta per pagina, evidenza scelta fra gli snippet
  offerti, escalation a Claude sotto soglia), Jev pilota di default dei journey con `TYPESAFE_API_KEY`, Jev riserva del
  crawler (una richiesta per stadio), conteggio di chiamate e tempi, `scripts/smoke_engagement.py` per la prova live.
  Claude giudica confirmshaming e proposta di valore e le escalation di Jev.
- Revisioni: giri con revisori indipendenti e regole Fable su ogni workstream; documentazione aggiornata.
- Gate finale: `ruff` pulito, 1921 test passati, `node --check` sui quattro JS, `uv build`, `claude plugin validate`
  (anche `--strict`), `jev_ultrafast/model.py` e `questions.py` invariati rispetto al fork.

## Da fare
1. Revisione finale verificata delle modifiche Jev (lenti: riuso del loop originale, sicurezza, correttezza), con
   verifica avversariale a due voti e fix fino a esaurimento; poi gate completo e commit pulito.
2. Prova live con la chiave TypeSafe in locale (utente): `uv run python scripts/smoke_engagement.py` (fixture locale) e
   `--url <shop>`; i numeri entrano nel README solo dopo questa prova.
3. Decisione aperta: il plugin deve approvare da solo `get_judgment_tasks` per i giudici Claude in background?
4. Fase 2 del catalogo: calibrazione con GA4/Clarity, axe-core completo, CrUX, checkout oltre il primo step.
