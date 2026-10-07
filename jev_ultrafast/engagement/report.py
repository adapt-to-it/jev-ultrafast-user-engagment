"""report.json and a self-contained Italian report.html (inline CSS, no external resources, escaped page text)."""

import base64
import io
import json
import os
import re
from html import escape

from .judgments import RUBRICS_VERSION, load_rubrics
from .kpis import KPIS
from .schemas import RISK_INDEX, SCHEMA_VERSION, SUB_INDICES
from .scoring import load_anchors, run_observations, score_run

REPORT_VERSION = "report.v1"
DISCLAIMER = (
    "Stima di engagement readiness ricavata da sessioni sintetiche: non è engagement misurato. "
    "Indica attrito previsto e segnali di rischio osservabili, non il comportamento di utenti reali."
)
JOURNEY_ONLY = "Run di solo journey: l'ERS richiede un audit; collegala a un audit con score_run"
WIDGET_CAVEAT = "possibile widget non letto (iframe o shadow DOM chiuso sulla pagina)"
DPR_NOT_ASSESSED = (
    "Rischio dark pattern non valutato: l'indice non include alcuna penalità ed è da leggere come limite superiore."
)
NAMES = {
    "PERF": "Prestazioni",
    "FAI": "Attrito previsto",
    "TRI": "Segnali di fiducia",
    "PTI": "Trasparenza di prezzi e costi",
    "CCL": "Chiarezza e carico cognitivo",
    "MPI": "Leve di persuasione genuine",
    "DPR": "Segnali di rischio dark pattern",
}
# Italian text of every reason code the producers emit (tests/test_engagement_report.py collects them from the
# producers' source and fails on one without a label here or under REASON_PREFIXES).
REASONS = {
    # scoring and generic
    "no_observation": "nessuna osservazione",
    "not_assessable": "non valutabile",
    "not_assessed": "non valutato",
    "not_applicable": "non applicabile",
    "invalid_value": "valore non interpretabile",
    "unavailable": "dato non disponibile",
    "error": "errore durante il test",
    # funnel stages (crawler) and pages
    "bot_challenge": "pagina di verifica anti-bot",
    "not_found": "pagina non trovata",
    "not_reached": "fase non raggiunta",
    "not_requested": "fase non richiesta",
    "checkout_boundary": "oltre il limite di sicurezza del checkout",
    "background_tab": "pagina non visibile durante il caricamento",
    "timeout": "tempo scaduto",
    "max_pages": "limite di pagine dell'audit raggiunto",
    "renderer_crashed": "la scheda del browser si è chiusa per un errore (crash del renderer)",
    "navigation_error": "pagina non caricata: errore di rete del browser",
    "audit_unavailable": "pagina non letta dall'audit",
    "guard_refused:url": "indirizzo escluso dalla guardia di sicurezza (altro sito o pagina di checkout)",
    "pdp_not_found": "scheda prodotto non trovata",
    "pdp_not_reached": "scheda prodotto non raggiunta",
    "home_not_reached": "home non raggiunta",
    "cart_not_found": "carrello non trovato",
    "cart_not_reached": "carrello non raggiunto",
    "no_cart_page": "carrello non raggiunto",
    "out_of_stock": "prodotto esaurito",
    "consent_blocking": "banner dei cookie bloccante: nessun click sotto il banner",
    "add_to_cart_not_found": "pulsante di aggiunta al carrello non trovato",
    "add_to_cart_failed": "aggiunta al carrello non riuscita",
    "variant_required": "serve una variante (taglia, colore) che non è stato possibile scegliere",
    "no_available_option": "nessuna variante disponibile",
    "empty_cart": "carrello vuoto",
    "checkout_cta_not_found": "pulsante per il checkout non riconosciuto",
    "checkout_click_failed": "click verso il checkout non riuscito",
    "no_navigation": "il click verso il checkout non ha aperto una nuova pagina",
    # page data read by the checks
    "vitals_unavailable": "metriche di caricamento non disponibili",
    "network_unavailable": "dati di rete non disponibili",
    "errors_unavailable": "errori della console non disponibili",
    "no_screenshot": "screenshot non disponibile",
    "unsupported_language": "lingua della pagina non supportata",
    "review_count_unavailable": "numero di recensioni non leggibile",
    "rating_scale_unknown": "scala del punteggio medio non riconosciuta",
    "no_product_price": "prezzo del prodotto non letto",
    "no_cart_total": "totale del carrello non letto",
    "cart_lines_not_recognised": "righe del carrello non riconosciute",
    "accept_not_recognised": "pulsante per accettare i cookie non riconosciuto",
    "probe_not_run": "test della ricerca non eseguito",
    "probe_failed": "test della ricerca non riuscito",
    "speculative_navigation": "pagina caricata in anticipo dal browser (prefetch o prerender), tempi non misurabili",
    "same_document": "cambio di pagina senza un nuovo documento",
    "no_form_fields": "nessun campo da compilare",
    "no_interactive_targets": "nessun elemento interattivo",
    "no_rating": "nessun punteggio medio",
    "no_structured_price": "nessun prezzo nei dati strutturati",
    "no_visible_price": "nessun prezzo visibile",
    "no_strikethrough_price": "nessun prezzo barrato",
    "small_assortment": "assortimento troppo piccolo",
    "little_prose": "testo troppo breve",
    # risk tests (deception)
    "deception_not_run": "test di rischio non eseguiti",
    "context_unavailable": "seconda sessione del browser non disponibile",
    "nothing_added": "nessun prodotto aggiunto al carrello",
    "added_item_not_recognised": "prodotto aggiunto non riconosciuto tra le righe del carrello",
    "single_page": "una sola pagina del percorso: nessun confronto tra pagine",
    "no_pages": "nessuna pagina del percorso",
    "product_page_not_visited": "scheda prodotto non rivisitata",
    "product_page_not_visited_twice": "scheda prodotto non rivisitata due volte",
    "ticking_not_verified": "conto alla rovescia non verificato: non è stato visto scorrere",
    "countdown_not_seen_on_revisit": "conto alla rovescia non visto alle nuove visite",
    "countdown_seen_once": "conto alla rovescia visto in una sola visita",
    "countdown_not_paired": "conti alla rovescia delle due visite non abbinabili",
    "stock_not_seen_on_revisit": "messaggio sulle scorte non visto alle nuove visite",
    "close_had_no_effect": "la chiusura del popup non ha avuto effetto",
    "close_not_verified": "chiusura del popup non verificata",
    "manage_had_no_effect": "il pulsante per gestire le preferenze non ha avuto effetto",
    "no_second_layer": "secondo livello del banner non letto",
    # crawler controls: consent, overlays, search probe
    "not_observed": "pagina non osservabile",
    "control_not_found": "controllo non trovato",
    "sent_earlier": "controllo già inviato a questa pagina: mai ripetuto",
    "uncertain_earlier": "un click precedente può aver raggiunto la pagina: mai ripetuto",
    "no_consent_banner": "nessun banner dei cookie",
    "policy_none": "nessuna scelta sul consenso richiesta",
    "not_blocking": "banner non bloccante, lasciato aperto",
    "no_accept_control": "nessun pulsante per accettare",
    "no_reject_control": "nessun pulsante per rifiutare",
    "no_close_control": "nessun pulsante di chiusura",
    "no_search_field": "nessun campo di ricerca",
    "no_category_word": "nessuna parola di categoria da cercare",
    "search_field_not_observed": "campo di ricerca non osservato",
    "unreadable": "risultato non leggibile",
    # journey and its independent verification
    "no_journey": "nessun percorso dell'agente in questa run",
    "no_verification": "verifica indipendente non eseguita",
    "journey_error": "percorso interrotto da un errore del sistema di prova, non del sito",
    "page_unreadable": "controlli trattenuti: la pagina non è stata letta dall'audit",
    "cart_unreadable": "pagina del carrello non leggibile dall'audit",
    "final_page_unreadable": "pagina finale non leggibile dall'audit",
    "cart_items_unreadable": "righe del carrello non leggibili",
    "cart_price_unreadable": "prezzo del prodotto nel carrello non leggibile",
    "cart_price_ambiguous": "prezzo nel carrello ambiguo tra prezzo unitario e totale di riga",
    # LLM judgments
    "not_judged": "giudizi non richiesti",
    "no_snippets": "nessun testo da giudicare",
    "no_audit": "pagina senza dati di audit",
    "judgment_pending": "giudizio in attesa",
    "judgment_uncertain": "giudizio incerto: accordo insufficiente tra i valutatori",
    "judgment_unclear": "i valutatori non hanno potuto decidere",
    "invalid_verdict": "verdetto non valido",
    "unknown_task": "task di giudizio sconosciuto",
    "samples_complete": "campioni del task già completi",
    "task_already_final": "task di giudizio già concluso",
    "duplicate_judge": "valutatore già registrato per il task",
}
# "<prefix><detail>" reasons: the prefix's text, then the detail's own label, or the detail as written when it is free
# text (an Italian sentence from the producer, an exception message). An identifier detail needs its own label.
REASON_PREFIXES = {
    "not_applicable:": "non applicabile",
    "no_measurement:": "non misurato",
    "limite superiore:": "limite superiore",
    "probe:": "test della ricerca non riuscito",
    "revisit_": "nuova visita non riuscita",
    "guard_refused:": "rifiutato dalla guardia di sicurezza",
    "not_executed:": "azione non eseguita: la pagina era cambiata",
    "failed:": "azione non riuscita",
    "error:": "errore imprevisto del sistema di prova",
}
REASON_CODE = re.compile(r"[a-z][a-z0-9_]*(?::[a-z0-9_]+)?")
STEP_FLAGS = {  # journey step flags (steps.jsonl) in the timeline
    "dead_click": "click senza effetto",
    "rage": "click ripetuti senza effetto",
    "backtrack": "ritorno a una pagina già vista",
    "guard_blocked": "bloccato dalla guardia di sicurezza",
    "stale": "pagina cambiata prima dell'azione: nulla eseguito",
    "external_nav": "navigazione verso un altro sito",
    "new_tab": "nuova scheda aperta",
    "new_document": "nuova pagina caricata",
    "unexpected_nav": "navigazione inattesa",
    "state_changed": "stato della pagina cambiato",
    "uncertain": "esecuzione non confermata",
    "navigation_error": "pagina non caricata",
    "follows_timeout": "dopo un assestamento scaduto",
    "session_gone": "scheda del browser persa",
}
TAGS = {
    "observed": ("Osservato", "tag-obs"),
    "inferred": ("Inferito", "tag-inf"),
    "missing": ("Non valutabile", "tag-na"),
    "skipped": ("Non applicabile", "tag-skip"),
    "info": ("Informativo", "tag-skip"),
}
THUMB_MAX_BYTES = 60_000
PROFILE_RULE = (
    "I KPI osservati su più profili sono aggregati per profilo e vale il profilo peggiore (quello con il punteggio più "
    "basso), indicato tra parentesi quando è strettamente peggiore degli altri; i segnali di rischio prendono la "
    "confidenza più alta. Il complessivo è quindi un caso peggiore KPI per KPI e un sotto-indice complessivo può "
    "stare sotto quello di ogni profilo: il confronto per profilo mostra i punteggi di ciascun device."
)

CSS = """
:root{--bg:#f7f6f2;--fg:#1c1c1e;--muted:#5d6166;--card:#ffffff;--line:#e2dfd6;--track:#ebe8e0;
--good:#2e7d5b;--mid:#b07a12;--bad:#b3392e;--accent:#2c5d8a;--tag:#eef1f4}
@media (prefers-color-scheme: dark){:root{--bg:#141517;--fg:#ececec;--muted:#a3a7ad;--card:#1d1f22;
--line:#33363b;--track:#2a2d31;--good:#5dbb8c;--mid:#e0ad45;--bad:#e8776c;--accent:#7fb0de;--tag:#2a2e33}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:26px;margin:4px 0 2px;overflow-wrap:anywhere}h2{font-size:19px;margin:32px 0 12px}
h3{font-size:16px;margin:0}
.eyebrow{text-transform:uppercase;letter-spacing:.08em;font-size:12px;color:var(--muted);margin:0}
.meta,.muted{color:var(--muted);font-size:13px;overflow-wrap:anywhere}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:12px 0}
.hero{display:grid;grid-template-columns:minmax(160px,220px) 1fr;gap:20px;align-items:center}
.big{font-size:56px;font-weight:700;line-height:1}.big small{font-size:18px;color:var(--muted);font-weight:500}
.facts{display:flex;flex-wrap:wrap;gap:8px 18px;margin:8px 0}.facts b{font-weight:600}
.note{border-left:3px solid var(--accent);padding:6px 10px;margin:10px 0;background:var(--tag);border-radius:4px}
.warn{border-left-color:var(--bad)}
.bars{display:grid;gap:10px}.row{display:grid;grid-template-columns:minmax(150px,240px) 1fr 56px;gap:10px;
align-items:center}.bar{height:12px;background:var(--track);border-radius:6px;overflow:hidden}
.bar span{display:block;height:100%;border-radius:6px}
.good{background:var(--good)}.mid{background:var(--mid)}.bad{background:var(--bad)}.none{background:transparent}
.score{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:6px 8px;
border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.scroll{overflow-x:auto}
.tag{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;font-weight:600;background:var(--tag);
white-space:nowrap}.tag-obs{color:var(--good)}.tag-inf{color:var(--accent)}.tag-na{color:var(--bad)}
.tag-skip{color:var(--muted)}
details{margin:10px 0}summary{cursor:pointer;font-weight:600}
blockquote{margin:6px 0;padding:4px 10px;border-left:3px solid var(--line);color:var(--fg);font-style:italic}
ol.timeline{padding-left:22px}ol.timeline li{margin:6px 0}
.thumb{max-width:120px;max-height:240px;border:1px solid var(--line);border-radius:6px}
code{font-size:12px;overflow-wrap:anywhere}footer{margin-top:40px;color:var(--muted);font-size:13px}
@media (max-width:640px){.hero{grid-template-columns:1fr}.row{grid-template-columns:1fr 48px}
.row .bar{grid-column:1/-1;grid-row:2}}
"""


def e(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def _it(number, digits=1) -> str:
    text = f"{number:,.{digits}f}"
    return text.replace(",", " ").replace(".", ",")


def fmt_value(value, unit) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "sì" if value else "no"
    if isinstance(value, (int, float)):
        if unit in {"ms", "KB", "px"}:
            return f"{_it(value, 0)} {unit}"
        if unit == "%":
            return f"{_it(value, 1)} %"
        if unit in {"confidence", "score", "0-1", "ratio"}:
            return _it(value, 2)
        return _it(value, 0) if float(value).is_integer() else _it(value, 2)
    return str(value)


def fmt_score(value) -> str:
    return "n/d" if value is None else _it(value, 1)


def fmt_share(value) -> str:
    return "n/d" if value is None else f"{round(value * 100):d} %"


def fmt_ratio(value) -> str:
    """0.3 -> "0,30" for formulas."""
    return _it(value, 2)


def fmt_number(value) -> str:
    """Weights and severities: 2 -> "2", 1.5 -> "1,5", 0.25 -> "0,25"."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "" if value is None else str(value)
    return _it(value, 0) if float(value).is_integer() else _it(value, 2).rstrip("0")


def fmt_confidence(grade, coverage) -> str:
    """Coverage grade as the report must name it: "A (copertura 96 %)", never a bare grade."""
    return f"{grade or 'n/d'} (copertura {fmt_share(coverage)})"


def dpr_text(dpr) -> str:
    """Dark-pattern risk for display: "n/d" when no risk signal was assessed, the share when only some were."""
    dpr = dpr or {}
    coverage = dpr.get("coverage")
    if coverage is not None and coverage <= 0:
        return "n/d"
    text = fmt_score(dpr.get("score"))
    if coverage is not None and coverage < 1:
        text += f" (parziale: {dpr.get('assessed', 0)}/{dpr.get('applicable', 0)} segnali)"
    return text


def scope_line(output, kind="audit") -> str:
    """"Ambito: ..." for a ScoreOutput: which KPI families applied and how much of the risk check ran."""
    context = (output or {}).get("context") or {}
    dpr = (output or {}).get("dpr") or {}
    parts = ["journey senza audit"] if kind == "journey" else [
        "audit deterministico", "con journey" if context.get("journey") else "senza journey"]
    parts.append("con giudizi LLM" if context.get("judged") else "senza giudizi LLM")
    if dpr.get("coverage") is not None:
        if dpr["coverage"] <= 0:
            parts.append("rischio dark pattern non valutato")
        else:
            parts.append(f"rischio dark pattern valutato {dpr.get('assessed', 0)}/{dpr.get('applicable', 0)}")
    return "Ambito: " + " · ".join(parts)


def reason_label(reason) -> str | None:
    """Italian text for a reason code (REASONS, REASON_PREFIXES), or None when the code has none."""
    reason = "" if reason is None else str(reason)
    if reason in REASONS:
        return REASONS[reason]
    if reason.startswith("credit_withheld:"):
        return f"credito non assegnato: segnale di rischio {reason.split(':', 1)[1]}"
    for prefix, label in REASON_PREFIXES.items():
        if reason.startswith(prefix):
            detail = reason[len(prefix):].strip(" :_")
            if not detail:
                return label
            text = reason_label(detail)
            if text is None and not REASON_CODE.fullmatch(detail):
                text = detail  # free text: a sentence the producer wrote in Italian, an exception message
            return None if text is None else f"{label}: {text}"
    return None


def reason_text(reason, assessed=False, absent=True) -> str:
    """Italian text for a reason (the code itself when it has no label); absent=False: an assessed no_snippets value
    above the rubric's absent label."""
    if not reason:
        return ""
    reason = str(reason)
    if assessed and reason == "no_snippets":
        if not absent:
            return "nessun testo da giudicare: valore minimo ricavato da link, rating o badge (verifica senza LLM)"
        return "nessun testo pertinente sulle pagine raggiunte: vale come assenza (verifica senza LLM)"
    return reason_label(reason) or reason


STAGE_WARNING = re.compile(r"(?P<profile>[\w.-]+): (?P<stage>\w+) not assessable \((?P<reason>.+)\)")
CODE_WARNING = re.compile(r"(?P<code>[a-z]+(?:_[a-z]+)+): (?P<detail>.+)")


def warning_text(warning) -> str:
    """A run warning for the Italian report: the stage and reason-code patterns of audit.py and journey.py in
    Italian (the producer's detail kept as written), any other technical message as written."""
    warning = str(warning)
    if match := STAGE_WARNING.fullmatch(warning):
        return f"{match['profile']}: fase {match['stage']} non valutabile: {reason_text(match['reason'])}"
    if (match := CODE_WARNING.fullmatch(warning)) and match["code"] in REASONS:
        return f"{REASONS[match['code']]} ({match['detail']})"
    return warning


def _is_absent(kpi_id, value) -> bool:
    """True when value is the absent label of the rubric that judges kpi_id (or no rubric judges it)."""
    rubric = next((r for r in load_rubrics().values() if r["kpi_id"] == kpi_id), None)
    return rubric is None or value == (rubric.get("values") or {}).get("absent")


def band(score) -> str:
    if score is None:
        return "none"
    return "good" if score >= 80 else "mid" if score >= 50 else "bad"


def kpi_tag(row) -> tuple:
    if not row.get("applicable", True):
        return TAGS["skipped"]
    if not row["assessed"]:
        return TAGS["missing"]
    if not row.get("weight") and row["owner"] != RISK_INDEX:
        return TAGS["info"]
    return TAGS["inferred"] if row["source"] == "judged" else TAGS["observed"]


def _versions(run, scores) -> dict:
    settings = run.get("settings") or {}
    judgments = run.get("judgments") or {}
    return {
        "schema": run.get("schema_version") or SCHEMA_VERSION,
        "anchors": (scores.get("overall") or {}).get("anchors_version") or settings.get("anchors_version"),
        "rubrics": judgments.get("rubrics_version") or settings.get("rubrics_version") or RUBRICS_VERSION,
        "profiles": settings.get("profiles_version"),
        "report": REPORT_VERSION,
    }


def _pages(run) -> list:
    rows = []
    for page in run.get("pages") or []:
        vitals, network = page.get("vitals") or {}, page.get("network") or {}
        rows.append(
            {
                "page_id": page.get("page_id"),
                "profile": page.get("profile"),
                "stage": page.get("stage"),
                "url": page.get("final_url") or page.get("url"),
                "type": (page.get("classification") or {}).get("type"),
                "lcp_ms": vitals.get("lcp"),
                "cls": vitals.get("cls"),
                "kb": round(network["bytes_transfer"] / 1000) if network.get("bytes_transfer") is not None else None,
                "screenshot": page.get("screenshot"),
            }
        )
    return rows


def _not_assessable(run, overall) -> list:
    rows = [
        {"stage": n.get("stage"), "profile": n.get("profile"), "kpi_id": n.get("kpi_id"), "reason": n.get("reason")}
        for n in run.get("not_assessable") or []
    ]
    rows += [
        {"stage": None, "profile": None, "kpi_id": k["id"], "reason": k["reason"]}
        for k in overall.get("kpis") or []
        if k.get("applicable", True) and not k["assessed"]
    ]
    return rows


JOURNEY_FIELDS = ("goal", "oracle", "oracle_params", "profile", "policy", "status", "verification", "optimal_steps",
                  "started_at", "finished_at")


def _journey_summary(run, steps) -> dict:
    summary = {k: (run.get("journey") or {}).get(k) for k in JOURNEY_FIELDS}
    summary.update(run_id=run.get("run_id"), steps=len(steps))
    return summary


def _caveats(run, overall) -> dict:
    """{kpi_id: note} when a KPI's value is absence read from pages with iframes or closed shadow roots."""
    kpis = overall.get("kpis") or []
    from_absence = {k["id"] for k in kpis if k.get("assessed") and k.get("reason") == "no_snippets"}
    notes = {}
    for row in run_observations(run):
        evidence = row.get("evidence") or {}
        if row.get("kpi_id") in from_absence and row.get("assessed") and row.get("reason") == "no_snippets" and (
            evidence.get("iframes") or evidence.get("shadow_roots_closed")
        ):
            notes[row["kpi_id"]] = WIDGET_CAVEAT
    return notes


def build_report(run, scores, *, steps=None, journeys=None, thumbnails=None, anchors=None) -> tuple[dict, str]:
    """(report.json dict, HTML string).

    steps: this run's journey steps; journeys: linked journey runs as [{"run": RunRecord, "steps": [...]}] (the ones
    merged by score_run(journeys=...)); thumbnails: {page_id: data:image/... URI}; anchors: defaults to anchors.json.
    """
    anchors = anchors or load_anchors()
    overall = scores.get("overall") or {}
    journey = run.get("journey") or None
    steps = list(steps if steps is not None else (journey or {}).get("steps") or [])
    linked = [(item.get("run") or {}, list(item.get("steps") or [])) for item in journeys or [] if item]
    ers = overall.get("ers") or {}
    risk = overall.get("dpr") or {}
    state = run.get("judgments") or {}
    reason = ers.get("reason")
    if run.get("kind") == "journey" and not ers.get("published"):
        reason = f"{JOURNEY_ONLY} ({reason})" if reason else JOURNEY_ONLY
    report = {
        "report_version": REPORT_VERSION,
        "run_id": run.get("run_id"),
        "kind": run.get("kind"),
        "site": run.get("site") or {},
        "status": run.get("status"),
        "created_at": run.get("created_at"),
        "finished_at": run.get("finished_at"),
        "versions": _versions(run, scores),
        "disclaimer": DISCLAIMER,
        "headline": {
            "ers": ers.get("score"),
            "published": ers.get("published", False),
            "grade": ers.get("grade"),
            "coverage": ers.get("coverage"),
            "llm_share": ers.get("llm_share"),
            "dpr": None if risk.get("coverage") is not None and risk["coverage"] <= 0 else risk.get("score"),
            "dpr_coverage": risk.get("coverage"),
            "dpr_assessed": risk.get("assessed"),
            "dpr_applicable": risk.get("applicable"),
            "context": dict(overall.get("context") or {}),
            "scope": scope_line(overall, run.get("kind")),
            "reason": reason,
            "limiting_factor": ers.get("limiting_factor"),
            "sub_indices": {n: (overall.get("sub_indices") or {}).get(n, {}).get("score") for n in SUB_INDICES},
            "profiles": {p: (s.get("ers") or {}).get("score") for p, s in (scores.get("profiles") or {}).items()},
        },
        "scores": scores,
        "risk_signals": overall.get("top_risk_signals") or [],
        "pages": _pages(run),
        "not_assessable": _not_assessable(run, overall),
        "journey": None,
        "journeys": [_journey_summary(r, s) for r, s in linked],
        "caveats": _caveats(run, overall),
        "judgments": {
            **{key: len(state.get(key) or []) for key in ("tasks", "verdicts", "final")},
            "models": sorted({m for f in state.get("final") or [] for m in f.get("models") or []}),
        },
        "method": {
            "dpr_penalty": anchors["dpr_penalty"],
            "floor": anchors["floor"],
            "publish": dict(anchors["publish"]),
        },
        "warnings": list(run.get("warnings") or []),
    }
    if journey:
        report["journey"] = _journey_summary(run, steps)
    thumbs = {k: v for k, v in (thumbnails or {}).items() if isinstance(v, str) and v.startswith("data:image/")}
    return report, render_html(run, scores, report, steps, thumbs, [s for _, s in linked])


# ---------------------------------------------------------------- HTML


def _bar(score, css=None) -> str:
    width = 0 if score is None else max(0.0, min(100.0, score))
    return f'<div class="bar"><span class="{css or band(score)}" style="width:{width:.1f}%"></span></div>'


def _hero(report, overall) -> str:
    h = report["headline"]
    site = report["site"]
    lines = [
        '<section class="card hero">',
        f'<div><p class="eyebrow">Engagement readiness</p><div class="big">{e(fmt_score(h["ers"]))}'
        "<small> / 100</small></div></div><div>",
        '<div class="facts">',
        f"<span>Confidenza <b>{e(h['grade'] or 'n/d')}</b> (copertura {e(fmt_share(h['coverage']))})</span>",
        f"<span>Quota inferita da LLM <b>{e(fmt_share(h['llm_share']))}</b></span>",
        f"<span>Rischio dark pattern <b>{e(dpr_text(overall.get('dpr')))}</b></span>",
    ]
    if h["limiting_factor"]:
        limiting = NAMES.get(h["limiting_factor"], h["limiting_factor"])
        lines.append(f"<span>Fattore limitante <b>{e(limiting)}</b></span>")
    lines.append("</div>")
    lines.append(f'<p class="meta">{e(h["scope"])}</p>')
    lines.append(f'<p class="note">{e(DISCLAIMER)}</p>')
    if not h["published"]:
        lines.append(f'<p class="note warn">Indice non pubblicato: {e(h["reason"] or "copertura insufficiente")}.</p>')
    elif h["dpr_coverage"] is not None and h["dpr_coverage"] <= 0:
        lines.append(f'<p class="note warn">{e(DPR_NOT_ASSESSED)}</p>')
    lines.append(f'<p class="meta">{e(site.get("start_url"))}</p></div></section>')
    return "\n".join(lines)


def _sub_indices(overall, method) -> str:
    subs = overall.get("sub_indices") or {}
    weights = overall.get("weights") or {}
    rows = []
    for name in SUB_INDICES:
        sub = subs.get(name) or {}
        limiting = ", ".join(sub.get("limiting_kpis") or [])
        detail = (
            f"Confidenza {e(fmt_confidence(sub.get('grade'), sub.get('coverage')))} · peso "
            f"{e(fmt_number(weights.get(name)))} · inferito {e(fmt_share(sub.get('llm_share')))}"
        )
        if limiting:
            detail += f" · limitano: {e(limiting)}"
        rows.append(
            f'<div class="row"><div><h3>{e(NAMES[name])} <span class="muted">{e(name)}</span></h3>'
            f'<div class="muted">{detail}</div></div>{_bar(sub.get("score"))}'
            f'<div class="score">{e(fmt_score(sub.get("score")))}</div></div>'
        )
    risk = overall.get("dpr") or {}
    coverage = risk.get("coverage")
    shown = None if coverage is not None and coverage <= 0 else risk.get("score")
    detail = f"rischio 0-100, più alto è peggio; riduce l'indice fino al {fmt_share(method['dpr_penalty'])}"
    if coverage is not None and coverage <= 0:
        detail += " · nessun segnale valutato: nessuna penalità applicata"
    elif coverage is not None:
        detail += f" · segnali valutati {risk.get('assessed', 0)}/{risk.get('applicable', 0)}"
        if coverage < 1:
            detail += " (parziale)"
    rows.append(
        f'<div class="row"><div><h3>{e(NAMES["DPR"])} <span class="muted">DPR</span></h3>'
        f'<div class="muted">{e(detail)}</div></div>'
        f'{_bar(shown, "bad" if shown is not None else None)}<div class="score">{e(fmt_score(shown))}</div></div>'
    )
    return '<section><h2>Sotto-indici</h2><div class="card bars">' + "\n".join(rows) + "</div></section>"


def _profiles(scores) -> str:
    profiles = scores.get("profiles") or {}
    if not profiles:
        return ""
    names = sorted(profiles)
    ers = {p: profiles[p].get("ers") or {} for p in names}
    head = "".join(f'<th class="num">{e(p)}</th>' for p in names)
    rows = [
        "<tr><td>Indice (ERS)</td>"
        + "".join(f'<td class="num">{e(fmt_score(ers[p].get("score")))}</td>' for p in names)
        + "</tr>",
        "<tr><td>Confidenza</td>"
        + "".join(f'<td class="num">{e(fmt_confidence(x.get("grade"), x.get("coverage")))}</td>' for x in ers.values())
        + "</tr>",
    ]
    for name in SUB_INDICES:
        values = [((profiles[p].get("sub_indices") or {}).get(name) or {}).get("score") for p in names]
        cells = "".join(f'<td class="num">{e(fmt_score(v))}</td>' for v in values)
        rows.append(f"<tr><td>{e(NAMES[name])}</td>{cells}</tr>")
    rows.append(
        "<tr><td>Rischio dark pattern</td>"
        + "".join(f'<td class="num">{e(dpr_text(profiles[p].get("dpr")))}</td>' for p in names)
        + "</tr>"
    )
    note = "Ogni profilo usa le proprie pagine più le osservazioni a livello di sito."
    if len(names) > 1:
        note += (" Il punteggio complessivo prende per ogni KPI il profilo peggiore, quindi un sotto-indice "
                 "complessivo può stare sotto quello di ogni profilo.")
    return (
        '<section><h2>Confronto per profilo</h2><div class="card scroll"><table><thead><tr><th></th>'
        f"{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        f'<p class="muted">{e(note)}</p></div></section>'
    )


def _evidence(evidence) -> str:
    if not isinstance(evidence, dict) or not evidence:
        return ""
    parts = [f"<blockquote>{e(q)}</blockquote>" for q in evidence.get("quotes") or [] if q]
    rest = {k: v for k, v in evidence.items() if k not in {"quotes", "task_id", "rubric_id", "models"}}
    if rest:
        text = json.dumps(rest, ensure_ascii=False, sort_keys=True, default=str)
        parts.append(f"<div class='muted'><code>{e(text[:400])}</code></div>")
    return "".join(parts)


def _risk_scope(risk) -> str:
    coverage = risk.get("coverage")
    if coverage is None:
        return ""
    assessed, applicable = risk.get("assessed", 0), risk.get("applicable", 0)
    text = f"Segnali di rischio valutati: {assessed} su {applicable}"
    return text + ("; gli altri sono elencati tra i non valutabili." if assessed < applicable else ".")


def _risks(overall) -> str:
    risk = overall.get("dpr") or {}
    signals = risk.get("signals") or []
    coverage = risk.get("coverage")
    if not signals and coverage is not None and coverage <= 0:
        body = ('<p class="muted">Nessun test di rischio eseguito: nessun segnale è stato valutato, quindi l\'indice '
                "non include alcuna penalità. Non significa assenza di rischio.</p>")
    elif not signals:
        found = "Nessun segnale di rischio rilevato"
        if coverage is not None:
            found += f" tra i {risk.get('assessed', 0)} valutati su {risk.get('applicable', 0)}"
        body = f'<p class="muted">{e(found)}.</p>'
        if coverage is not None and coverage < 1:
            body += '<p class="muted">Gli altri segnali sono elencati tra i non valutabili.</p>'
    else:
        items = []
        for s in signals:
            origin = "inferito da valutatori LLM" if s.get("source") == "judged" else "test deterministico"
            where = " · ".join(e(x) for x in (s.get("profile"), s.get("page_id")) if x)
            items.append(
                f'<div class="card"><h3>{e(s.get("description") or s["kpi_id"])} <span class="muted">'
                f'{e(s["kpi_id"])}</span></h3><div class="muted">gravità {e(_it(s["severity"], 2))} · confidenza '
                f'{e(_it(s["confidence"], 2))} · {e(origin)}{" · " + where if where else ""}</div>'
                f'{_evidence(s.get("evidence"))}</div>'
            )
        judged = sum(1 for s in signals if s.get("source") == "judged")
        body = (
            f'<p class="muted">{len(signals) - judged} da test deterministici, {judged} inferiti da valutatori LLM. '
            "Il rischio combina i segnali con un OR rumoroso: oltre il primo segnale forte cresce poco, l'ordine "
            f"qui sotto distingue i casi. {e(_risk_scope(risk))}</p>" + "".join(items)
        )
    return (
        '<section><h2>Segnali di rischio</h2><p class="muted">Segnali osservabili di possibili dark pattern, '
        "non accertamenti legali.</p>" + body + "</section>"
    )


def _journey(journey, steps, *, linked=False) -> str:
    if not journey:
        return ""
    verification = journey.get("verification") or {}
    passed = verification.get("passed")
    why = (verification.get("checks") or {}).get("not_assessable") if passed is None else None
    outcome = "superata" if passed else "non superata" if passed is False else "non valutabile" if why else (
        "non eseguita")
    items = []
    for step in steps:
        flags = ", ".join(STEP_FLAGS.get(k, k) for k, v in sorted((step.get("flags") or {}).items()) if v)
        target = f" [{step['target']}]" if step.get("target") not in (None, "") else ""
        changed, response = step.get("page_changed"), (step.get("since") or {}).get("first_response_ms")
        detail = [
            f"cambio pagina: {'sì' if changed else 'no'}" if changed is not None else "",
            f"risposta {fmt_value(response, 'ms')}" if response is not None else "",
            f"assestamento {fmt_value(step.get('settle_ms'), 'ms')}" if step.get("settle_ms") is not None else "",
            f"segnali: {flags}" if flags else "",
        ]
        items.append(
            f"<li><b>{e(step.get('operation'))}</b>{e(target)} {e(step.get('label') or '')}"
            f'<div class="muted">{e(" · ".join(d for d in detail if d))}</div>'
            f'<div class="muted">{e(step.get("url_after") or step.get("url_before") or "")}</div></li>'
        )
    timeline = '<p class="muted">Nessun passo registrato.</p>'
    if items:
        timeline = f'<ol class="timeline">{"".join(items)}</ol>'
    checks = verification.get("checks")
    checks_html = ""
    if checks:
        text = json.dumps(checks, ensure_ascii=False, sort_keys=True, default=str)[:600]
        checks_html = f'<div class="muted"><code>{e(text)}</code></div>'
    unverified = f'<p class="note warn">Verifica non valutabile: {e(reason_text(why))}.</p>' if why else ""
    heading = "Percorso dell'agente (run collegata)" if linked else "Percorso dell'agente"  # static, code-owned
    source = f'<p class="meta">Run journey <code>{e(journey.get("run_id"))}</code></p>' if linked else ""
    return (
        f'<section><h2>{heading}</h2><div class="card">{source}'
        f"<p><b>Obiettivo:</b> {e(journey.get('goal'))}</p>"
        f'<div class="facts"><span>Profilo <b>{e(journey.get("profile"))}</b></span>'
        f"<span>Politica <b>{e(journey.get('policy'))}</b></span><span>Stato <b>{e(journey.get('status'))}</b></span>"
        f"<span>Verifica indipendente ({e(journey.get('oracle'))}) <b>{e(outcome)}</b></span></div>"
        f"{unverified}{checks_html}{timeline}"
        '<p class="muted">Il tempo di decisione del modello è escluso dai tempi attribuiti al sito.</p></div></section>'
    )


def _kpi_table(overall, caveats, show_profile=False) -> str:
    rows_by_owner = {}
    for row in overall.get("kpis") or []:
        rows_by_owner.setdefault(row["owner"], []).append(row)
    sections = []
    for owner in (*SUB_INDICES, RISK_INDEX):
        rows = rows_by_owner.get(owner) or []
        if not rows:
            continue
        body = []
        for row in rows:
            kpi = KPIS.get(row["id"])
            label, css = kpi_tag(row)
            absent = row.get("reason") != "no_snippets" or _is_absent(row["id"], row.get("value"))
            reason = reason_text(row.get("reason"), row["assessed"], absent)
            if caveats.get(row["id"]):
                reason += f"; {caveats[row['id']]}"
            note = f'<div class="muted">{e(reason)}</div>' if reason else ""
            provisional = ' <span class="muted" title="ancora editoriale">*</span>' if row.get("provisional") else ""
            third = row.get("weight") if owner != RISK_INDEX else (kpi.severity if kpi else None)
            value = fmt_value(row.get("value"), row.get("unit"))
            if show_profile and row.get("profile"):
                value += f" ({row['profile']})"
            body.append(
                f"<tr><td><code>{e(row['id'])}</code>{provisional}<div class='muted'>"
                f"{e(kpi.description if kpi else '')}</div></td>"
                f'<td class="num">{e(value)}</td>'
                f'<td class="num">{e(fmt_score(row.get("normalized")))}</td>'
                f'<td class="num">{e(fmt_number(third))}</td>'
                f'<td><span class="tag {css}">{e(label)}</span>{note}</td></tr>'
            )
        heading = "gravità" if owner == RISK_INDEX else "peso"
        sections.append(
            f"<details{' open' if owner != RISK_INDEX else ''}><summary>{e(NAMES[owner])} ({e(owner)})</summary>"
            '<div class="scroll"><table><thead><tr><th>KPI</th><th class="num">Valore</th><th class="num">Normalizzato'
            f'</th><th class="num">{heading}</th><th>Fonte</th></tr></thead><tbody>{"".join(body)}</tbody></table>'
            "</div></details>"
        )
    return (
        '<section><h2>KPI</h2><div class="card"><p class="muted">Osservato: misurato in modo deterministico. '
        "Inferito: giudizio LLM su testi della pagina con citazioni verificate. Non valutabile: escluso dal "
        "punteggio, mai contato come 0. * ancora editoriale (provvisoria)."
        + (f" {PROFILE_RULE}" if show_profile else "")
        + "</p>"
        + "".join(sections)
        + "</div></section>"
    )


def _pages_html(report, thumbs) -> str:
    rows = []
    for page in report["pages"]:
        thumb = thumbs.get(page["page_id"])
        image = f'<img class="thumb" alt="{e(page["page_id"])}" src="{e(thumb)}">' if thumb else ""
        rows.append(
            f"<tr><td>{e(page['profile'])}</td><td>{e(page['stage'])}</td><td>{e(page['type'])}</td>"
            f"<td><code>{e(page['url'])}</code></td><td class='num'>{e(fmt_value(page['lcp_ms'], 'ms'))}</td>"
            f"<td class='num'>{e(fmt_value(page['cls'], 'score'))}</td><td class='num'>"
            f"{e(fmt_value(page['kb'], 'KB'))}</td><td>{image}</td></tr>"
        )
    if not rows:
        return ""
    return (
        '<section><h2>Pagine analizzate</h2><div class="card scroll"><table><thead><tr><th>Profilo</th>'
        '<th>Fase</th><th>Tipo</th><th>URL</th><th class="num">LCP</th><th class="num">CLS</th><th class="num">Peso'
        "</th><th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _missing(report) -> str:
    rows = report["not_assessable"]
    if not rows:
        return ""
    items = []
    for row in rows:
        what = row.get("kpi_id") or f"fase {row.get('stage')}"
        where = " · ".join(str(x) for x in (row.get("profile"), row.get("stage") if row.get("kpi_id") else None) if x)
        where = f" ({e(where)})" if where else ""
        items.append(f"<li><code>{e(what)}</code>{where}: {e(reason_text(row.get('reason')))}</li>")
    return (
        '<section><h2>Non valutabile</h2><div class="card"><p class="muted">Questi elementi non entrano nel '
        f"punteggio e riducono la copertura.</p><ul>{''.join(items)}</ul></div></section>"
    )


def _footer(report, overall) -> str:
    v = report["versions"]
    m = report["method"]
    publish = m["publish"]
    models = report["judgments"].get("models") or []
    models = f" Modelli dei valutatori: {e(', '.join(models))}." if models else ""
    return (
        "<footer><h2>Metodo</h2>"
        "<p>Ogni KPI è normalizzato 0-100 con ancore lineari a tratti versionate; ogni sotto-indice è la media "
        "pesata dei KPI valutati, con i pesi rinormalizzati. ERS = media geometrica pesata dei sotto-indici "
        f"(minimo {e(_it(m['floor'], 0))}) × (1 − {e(fmt_ratio(m['dpr_penalty']))} × DPR/100); "
        "DPR = 100 × (1 − ∏(1 − gravità × confidenza)) sui segnali valutati: un segnale non valutato non aggiunge "
        "rischio, e senza segnali valutati il rischio è n/d. L'indice è pubblicato solo con copertura complessiva ≥ "
        f"{e(fmt_share(publish['min_coverage']))} e copertura ≥ {e(fmt_share(publish['min_major_coverage']))} per "
        f"ogni sotto-indice con peso ≥ {e(_it(publish.get('major_weight', 15), 0))}. La confidenza A, B o C è un "
        "grado di copertura, non di qualità del punteggio. I giudizi LLM usano etichette chiuse, citazioni verificate "
        "parola per parola sul testo della pagina e la maggioranza di più valutatori, che sono campioni dello stesso "
        "modello con lo stesso prompt: la maggioranza controlla la stabilità del giudizio, non un accordo tra "
        f"valutatori indipendenti.{models}</p>"
        f"<p>{e(DISCLAIMER)}</p>"
        f"<p>Run <code>{e(report['run_id'])}</code> · tipo {e(report['kind'])} · stato {e(report['status'])} · "
        f"creata {e(report['created_at'])}</p>"
        f"<p>Versioni: schema {e(v['schema'])} · ancore {e(v['anchors'])} · rubriche {e(v['rubrics'])} · "
        f"profili {e(v['profiles'] or 'n/d')} · report {e(v['report'])} · giudizi: "
        f"{e(report['judgments']['final'])} finali su {e(report['judgments']['tasks'])} task</p></footer>"
    )


def render_html(run, scores, report, steps, thumbs, linked_steps=()) -> str:
    overall = scores.get("overall") or {}
    site = report["site"]
    host = site.get("host") or site.get("start_url") or "negozio"
    warnings = "".join(f"<li>{e(warning_text(w))}</li>" for w in report["warnings"])
    parts = [
        "<!doctype html>",
        '<html lang="it"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        f"<title>{e('Engagement readiness · ' + str(host))}</title>",
        f"<style>{CSS}</style></head><body><main>",
        f'<header><p class="eyebrow">Report di engagement readiness</p><h1>{e(host)}</h1>',
        f'<p class="meta">Run {e(report["run_id"])} · {e(report["created_at"])} · {e(report["kind"])}</p></header>',
        _hero(report, overall),
        _sub_indices(overall, report["method"]),
        _profiles(scores),
        _risks(overall),
        _journey(report.get("journey"), steps),
        *(_journey(j, s, linked=True) for j, s in zip(report.get("journeys") or [], linked_steps)),
        _kpi_table(overall, report.get("caveats") or {}, len(scores.get("profiles") or {}) > 1),
        _pages_html(report, thumbs),
        _missing(report),
        f'<section><h2>Avvisi</h2><div class="card"><p class="muted">Messaggi della raccolta; quelli senza traduzione '
        f'sono riportati come registrati.</p><ul>{warnings}</ul></div></section>' if warnings else "",
        _footer(report, overall),
        "</main></body></html>",
    ]
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------- files


def thumbnail(path, *, width=240, height=480) -> str | None:
    """Small base64 JPEG data URI for a stored screenshot, or None."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((width, height))
            for quality in (70, 50, 35):
                buffer = io.BytesIO()
                image.save(buffer, "JPEG", quality=quality, optimize=True)
                if buffer.tell() <= THUMB_MAX_BYTES:
                    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:  # a broken screenshot only drops its thumbnail
        return None
    return None


def write_report(store, run_id, *, journey_run_ids=None) -> dict:
    """Score the stored run and write report.json + report.html into its directory.

    journey_run_ids: journey runs to merge (see scoring.score_run). When None, the ones recorded by the last stored
    score (run["scores"]["overall"]["context"]["journey_runs"]) are used.
    """
    if store is None:
        from .store import RunStore

        store = RunStore()
    run = store.load(run_id)
    if journey_run_ids is None:
        context = ((run.get("scores") or {}).get("overall") or {}).get("context") or {}
        journey_run_ids = context.get("journey_runs") or []
    journeys = []
    for journey_id in journey_run_ids:
        try:
            journey_run = store.load(journey_id)
        except (OSError, ValueError, KeyError):
            run.setdefault("warnings", []).append(f"run journey collegata non leggibile: {journey_id}")
            continue
        steps = store.read_steps(journey_id) if hasattr(store, "read_steps") else []
        journeys.append({"run": journey_run, "steps": steps})
    scores = score_run(run, journeys=[j["run"] for j in journeys])
    run_dir = store.path(run_id)
    steps = store.read_steps(run_id) if run.get("journey") and hasattr(store, "read_steps") else None
    thumbs = {}
    for page in run.get("pages") or []:
        if page.get("screenshot") and (run_dir / page["screenshot"]).is_file():
            uri = thumbnail(run_dir / page["screenshot"])
            if uri:
                thumbs[page["page_id"]] = uri
    report, html = build_report(run, scores, steps=steps, journeys=journeys, thumbnails=thumbs)
    json_path = store.write_json(run_id, "report.json", report)
    html_path = run_dir / "report.html"
    tmp = html_path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(html, encoding="utf-8")
    os.replace(tmp, html_path)
    return {"report_json": str(json_path), "report_html": str(html_path)}
