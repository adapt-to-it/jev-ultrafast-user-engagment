import ast
import copy
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from jev_ultrafast import model as typesafe
from jev_ultrafast.engagement import judges, judgments, report, scoring
from jev_ultrafast.engagement.kpis import KPI_LIST
from jev_ultrafast.engagement.scoring import load_anchors, score_run

RUNS = Path(__file__).parent / "fixtures" / "runs"
PACKAGE = Path(report.__file__).parent
ATTACK = '"><script>alert(1)</script><img src=x onerror=alert(2)>'


def load(name):
    return json.loads((RUNS / f"{name}.json").read_text(encoding="utf-8"))


def steps():
    return [json.loads(line) for line in (RUNS / "journey_steps.jsonl").read_text(encoding="utf-8").splitlines()]


def build(run, **kwargs):
    return report.build_report(run, score_run(run), **kwargs)


class ResourceScan(HTMLParser):
    """Collects everything in real markup that would make a browser fetch or navigate (escaped text is inert)."""

    URL_ATTRS = {"src", "href", "srcset", "action", "poster", "data", "formaction", "background", "xlink:href"}
    TAGS = {"script", "link", "iframe", "object", "embed", "base", "frame", "audio", "video", "source", "track"}

    def __init__(self):
        super().__init__()
        self.found, self.in_style = [], False

    def handle_starttag(self, tag, attrs):
        if tag in self.TAGS:
            self.found.append(f"<{tag}>")
        self.in_style = tag == "style"
        for name, value in attrs:
            if name in self.URL_ATTRS and not (tag == "img" and name == "src" and value.startswith("data:image/")):
                self.found.append(f"{tag}[{name}]={value}")
            if name.startswith("on") or (name == "style" and re.search(r"url\(|@import", value or "", re.I)):
                self.found.append(f"{tag}[{name}]")

    def handle_endtag(self, tag):
        self.in_style = False

    def handle_data(self, data):
        if self.in_style and re.search(r"url\(|@import|https?:", data, re.I):
            self.found.append("style")


def external_references(html):
    scan = ResourceScan()
    scan.feed(html)
    return scan.found


def test_audit_report_content():
    run = load("audit_complete")
    data, html = build(run)
    overall = score_run(run)["overall"]
    assert data["report_version"] == "report.v1" and data["run_id"] == run["run_id"]
    assert data["headline"]["ers"] == overall["ers"]["score"] and data["headline"]["published"] is True
    assert data["headline"]["profiles"] == {
        p: s["ers"]["score"] for p, s in score_run(run)["profiles"].items()}
    anchors = load_anchors()["version"]  # versioned data: the report names the anchors it scored with
    assert data["versions"] == {"schema": "engagement.v1", "anchors": anchors, "rubrics": "rubrics.v2",
                                "profiles": "profiles.v1", "report": "report.v1"}
    # the golden run was judged without a TypeSafe key: Claude's three samples on every rubric, nothing escalated
    assert data["judgments"] == {"tasks": 16, "verdicts": 48, "final": 16, "models": ["claude-sonnet-5-5"],
                                 "by_model": {"claude-sonnet-5-5": 16}, "single_sample": 0, "escalated": 0,
                                 "escalated_final": 0, "jev_kpis": [], "jev_models": [], "claude_kpis": sorted(
                                     {t["kpi_id"] for t in run["judgments"]["tasks"]}), "claude_samples": {"3": 16}}
    assert data["model_calls"] == {} and "Richieste TypeSafe" not in html
    assert {r["kpi_id"] for r in data["not_assessable"] if r["kpi_id"]} == set()  # complete audit
    assert data["not_assessable"][0] == {"stage": "checkout_entry", "profile": "desktop", "kpi_id": None,
                                         "reason": "timeout"}
    assert len(data["pages"]) == 9 and data["pages"][0]["kb"] == 2300
    json.dumps(data)  # serialisable
    assert html.startswith("<!doctype html>") and '<html lang="it">' in html
    assert report.DISCLAIMER in html and "non è engagement misurato" in html
    assert f"{overall['ers']['score']:.1f}".replace(".", ",") in html
    for name in report.NAMES.values():
        assert name in html
    for item in KPI_LIST:
        assert item.id in html
    for tag in ("Osservato", "Inferito", "Non valutabile", "Non applicabile"):
        assert tag in html
    assert "credito non assegnato: segnale di rischio DPR.COUNTDOWN_RESET" in html
    assert "preferisco pagare di più" in html and "Aggiungi Protezione spedizione" in html  # risk evidence
    assert "Confronto per profilo" in html and '<th class="num">desktop</th><th class="num">mobile</th>' in html
    assert "prefers-color-scheme: dark" in html
    for version in (anchors, "rubrics.v2", "profiles.v1", "engagement.v1"):
        assert version in html
    assert "Percorso dell'agente" not in html
    assert data["journeys"] == [] and data["caveats"] == {}
    assert "da test deterministici" in html and "inferiti da valutatori LLM" in html
    # page KPIs are aggregated per profile: the headline value names the worst profile
    lcp, weight = report.fmt_value(2900, "ms"), report.fmt_value(2250, "KB")
    assert f"{lcp} (mobile)" in html and f"{weight} (desktop)" in html and "vale il profilo peggiore" in html
    # coverage is a confidence grade, never a bare "Grado"; the scope line says which families applied
    assert "Confidenza <b>A</b> (copertura 100 %)" in html and "Grado " not in html
    assert "Confidenza A (copertura" in html  # sub-index rows
    scope = "Ambito: audit deterministico · senza journey · con giudizi LLM · rischio dark pattern valutato 9/9"
    assert data["headline"]["scope"] == scope and scope in html
    assert data["headline"]["context"] == {"journey": False, "judged": True, "journey_runs": []}
    assert (data["headline"]["dpr"], data["headline"]["dpr_coverage"]) == (overall["dpr"]["score"], 1.0)
    assert (data["headline"]["dpr_assessed"], data["headline"]["dpr_applicable"]) == (9, 9)
    assert f"Rischio dark pattern <b>{report.fmt_score(overall['dpr']['score'])}</b>" in html
    assert "Segnali di rischio valutati: 9 su 9." in html and report.e(report.DPR_NOT_ASSESSED) not in html
    assert "<td class=\"num\">1,5</td>" in html and "<td class=\"num\">1.5</td>" not in html  # Italian decimals
    assert "<td class=\"num\">0,9</td>" in html  # DPR severities too
    assert "campioni dello stesso modello" in html and "Modelli dei valutatori: claude-sonnet-5-5" in html
    assert "la maggioranza di 3 valutatori, che sono campioni dello stesso modello" in html  # the real sample count
    # closed codes in Italian: stages and page types, run kind and status (report.json keeps the codes)
    assert "<li>fase primo step del checkout (desktop): tempo scaduto</li>" in html
    assert "<td>listing di categoria</td><td>listing</td>" in html and "<td>checkout_entry</td>" not in html
    assert "<td>primo step del checkout</td><td>checkout</td>" in html and "<td>pdp</td>" not in html
    assert "· tipo: audit deterministico · stato: parziale ·" in html and "stato partial" not in html
    assert (data["kind"], data["status"], data["pages"][1]["stage"]) == ("audit", "partial", "plp")


def test_profile_rule_in_the_kpi_caption_matches_the_scores():
    run = load("audit_complete")
    scores = score_run(run)
    _, html = report.build_report(run, scores)
    assert report.PROFILE_RULE in html
    overall = {k["id"]: k for k in scores["overall"]["kpis"]}
    profiles = {p: {k["id"]: k for k in s["kpis"]} for p, s in scores["profiles"].items()}
    checked = 0
    for item in KPI_LIST:
        rows = [profiles[p][item.id] for p in profiles]
        if item.owner == "DPR" or item.credit_unless or not all(r["normalized"] is not None for r in rows):
            continue
        worst = min(r["normalized"] for r in rows)
        assert overall[item.id]["normalized"] == worst, item.id  # the worst profile, whatever the aggregate
        named = overall[item.id]["profile"]
        strict = [p for p in profiles if profiles[p][item.id]["normalized"] == worst]
        assert named == (strict[0] if len(strict) == 1 else None), item.id
        checked += 1
    assert checked > 40
    filters = overall["FAI.PLP_FILTERS"]  # a max: pooling both profiles would have shown desktop's 6
    assert (filters["value"], filters["normalized"], filters["profile"]) == (2, 40, "mobile")
    assert "<td class=\"num\">2 (mobile)</td>" in html
    width = overall["FAI.SEARCH_WIDTH"]  # both profiles score 100: a tie names no profile
    assert width["profile"] is None and f"{report.fmt_value(width['value'], 'px')}</td>" in html
    risk = [p for p in profiles if p != "overall"]
    assert all(report.dpr_text(scores["profiles"][p]["dpr"]) in html for p in risk)
    assert "(parziale: 8/9 segnali)" in report.dpr_text(scores["profiles"]["desktop"]["dpr"])


def test_unassessed_dark_pattern_risk_is_never_shown_as_zero():
    journey = load("journey_run")
    data, html = build(journey, steps=steps())
    assert "Rischio dark pattern <b>0,0</b>" not in html and "Rischio dark pattern <b>n/d</b>" in html
    assert data["headline"]["dpr"] is None and data["headline"]["dpr_coverage"] == 0
    assert "Nessun test di rischio eseguito" in html and "Nessun segnale di rischio rilevato" not in html
    assert "rischio dark pattern non valutato" in data["headline"]["scope"]
    missing = {r["kpi_id"]: r["reason"] for r in data["not_assessable"] if (r["kpi_id"] or "").startswith("DPR.")}
    assert set(missing) == {"DPR.COUNTDOWN_RESET", "DPR.FAKE_LOW_STOCK", "DPR.SNEAK_INTO_BASKET",
                            "DPR.PRECHECKED_PAID_ADDONS", "DPR.CONSENT_ASYMMETRY", "DPR.NAGGING_OVERLAYS"}
    assert set(missing.values()) == {"no_observation"}  # judged risk signals are not applicable without judgments
    assert report.e(report.JOURNEY_AUDIT_KPIS.format(70)) in html  # a journey-only run folds the audit KPIs
    assert "<code>DPR.COUNTDOWN_RESET</code>: nessuna osservazione" not in html

    audit = load("audit_complete")  # deception tests and judgments never ran
    for row in audit["observations"]:
        if row["kpi_id"].startswith("DPR."):
            row.update(assessed=False, value=None, reason="timeout")
    audit["judgments"] = {}
    data, html = build(audit)
    assert data["headline"]["published"] is True and data["headline"]["dpr"] is None
    assert "Rischio dark pattern <b>n/d</b>" in html and report.e(report.DPR_NOT_ASSESSED) in html
    assert "nessun segnale valutato: nessuna penalità applicata" in html
    assert {r["kpi_id"] for r in data["not_assessable"] if (r["kpi_id"] or "").startswith("DPR.")} == {
        "DPR.COUNTDOWN_RESET", "DPR.FAKE_LOW_STOCK", "DPR.SNEAK_INTO_BASKET", "DPR.PRECHECKED_PAID_ADDONS",
        "DPR.CONSENT_ASYMMETRY", "DPR.NAGGING_OVERLAYS"}

    partial = load("audit_complete")
    partial["observations"] = [o for o in partial["observations"] if o["kpi_id"] != "DPR.COUNTDOWN_RESET"]
    data, html = build(partial)
    overall = score_run(partial)["overall"]["dpr"]
    assert overall["assessed"] == 8 and 0 < overall["coverage"] < 1
    assert f"Rischio dark pattern <b>{report.fmt_score(overall['score'])} (parziale: 8/9 segnali)</b>" in html
    assert "<code>DPR.COUNTDOWN_RESET</code>: nessuna osservazione" in html


def test_journey_only_report_folds_the_unobserved_audit_kpis():
    run = load("journey_run")
    run["not_assessable"] = [{"stage": None, "profile": "mobile", "kpi_id": None, "reason": "navigation_error"}]
    for row in run["observations"]:
        if row["kpi_id"] == "FAI.LOSTNESS":
            row.update(assessed=False, value=None, reason="no_measurement: nessuna pagina visitata")
    data, html = build(run, steps=steps())
    audit = [r for r in data["not_assessable"] if r["reason"] == "no_observation"]
    assert len(audit) == 70 and {r["kpi_id"] for r in audit} == {
        k.id for k in KPI_LIST if k.producer in ("checks", "deception")}  # report.json keeps every row
    section = html[html.index("<h2>Non valutabile</h2>"):]
    section = section[: section.index("</section>")]
    assert section.count("<li>") == 3 and report.e(report.JOURNEY_AUDIT_KPIS.format(70)) in section
    assert "<code>FAI.LOSTNESS</code>: non misurato: nessuna pagina visitata" in section
    assert "<li>percorso (mobile): pagina non caricata: errore di rete del browser</li>" in section
    audit_run = load("audit_complete")  # an audit run never folds
    audit_run["observations"] = [o for o in audit_run["observations"] if o["kpi_id"] != "PERF.LCP"]
    _, html = build(audit_run)
    assert "<code>PERF.LCP</code>: nessuna osservazione" in html and "KPI dell&#x27;audit (" not in html


def test_evidence_pages_unmeasured_weight_and_step_labels():
    run = load("journey_run")
    run["pages"] = [
        {"page_id": "mobile-journey-start", "profile": "mobile", "stage": "extra", "url": "https://shop.example/",
         "classification": {"type": "home"}, "vitals": {"lcp": 1800, "cls": 0.02},
         "network": {"requests": 40, "bytes_transfer": 1_200_000}, "notes": ["journey start page"]},
        {"page_id": "mobile-verify-cart", "profile": "mobile", "stage": "extra", "url": "https://shop.example/cart",
         "classification": {"type": "cart"}, "vitals": {}, "network": {"requests": 0, "bytes_transfer": 0},
         "notes": ["journey verification: cart_contains_item_under_price"]},
    ]
    journey_steps = steps()
    journey_steps.insert(1, {**journey_steps[0], "step": 2, "operation": "SCROLL_DOWN", "target": None,
                             "label": "Scroll down", "page_changed": False, "flags": {}})
    journey_steps[-1]["label"] = "Done"
    data, html = build(run, steps=journey_steps)
    assert [(p["stage"], p["kb"], p["notes"]) for p in data["pages"]] == [
        ("extra", 1200, ["journey start page"]),
        ("extra", None, ["journey verification: cart_contains_item_under_price"])]  # read in place: never measured
    section = html[html.index("<h2>Pagine analizzate</h2>"):]
    section = section[: section.index("</section>")]
    rows = section.split("<tr>")[2:]
    assert all("<td>nessuna (solo evidenza)<div class=\"muted\">journey " in r for r in rows)
    assert "<td>home</td>" in rows[0] and "<td>carrello</td>" in rows[1] and "fuori dal funnel" not in html
    assert f"<td class='num'>{report.fmt_value(1200, 'KB')}</td>" in rows[0]
    assert "<td class='num'>—</td><td></td></tr>" in rows[1] and "KB" not in rows[1]
    assert "pagina conservata come prova" in section and "come registrate" in section
    assert "<li><b>Scorrimento in giù</b> <div" in html and "Scroll down" not in html
    assert "<li><b>Fine dichiarata dall&#x27;agente</b> <div" in html and "Done" not in html
    assert "<li><b>Click</b> [4] Uomo<div" in html  # an element's label is kept
    _, html = build(load("audit_complete"))  # funnel pages without notes: no legend
    assert "solo evidenza" not in html and "come registrate" not in html


def test_enum_values_are_shown_in_italian():
    enums = {k: set(v["map"]) for k, v in load_anchors()["kpis"].items() if v.get("direction") == "enum"}
    assert {k: set(v) for k, v in report.VALUE_LABELS.items()} == enums
    _, html = build(load("audit_complete"))
    for kpi_id, text in (("FAI.PLP_PAGINATION", "pulsante «carica altri»"), ("FAI.PDP_VARIANT_SELECTOR", "pulsanti"),
                         ("TRI.RATING_BAND", "4,0-4,7"), ("TRI.RETURNS_CLARITY", "chiara")):
        row = html[html.index(f"<code>{kpi_id}</code>"):]
        assert f'<td class="num">{report.e(text)}' in row[: row.index("</tr>")]
    assert report.kpi_value({"id": "FAI.PLP_PAGINATION", "value": "brand_new", "unit": "enum"}) == "brand_new"
    assert report.kpi_value({"id": "FAI.PLP_PAGINATION", "value": ["x"], "unit": "enum"}) == "['x']"


def test_single_profile_report_does_not_name_the_profile():
    run = load("audit_complete")
    run["pages"] = [p for p in run["pages"] if p["profile"] == "mobile"]
    run["observations"] = [o for o in run["observations"] if o.get("profile") in (None, "mobile")]
    run["judgments"] = {}
    assert list(score_run(run)["profiles"]) == ["mobile"]
    _, html = build(run)
    assert f"{report.fmt_value(2900, 'ms')}</td>" in html and "(mobile)" not in html and "profilo peggiore" not in html


def test_method_text_follows_the_anchors():
    run = load("audit_complete")
    _, html = build(run)
    assert "× (1 − 0,30 × DPR/100)" in html and "≥ 60 %" in html and "≥ 50 %" in html and "fino al 30 %" in html
    anchors = load_anchors()
    anchors.update(dpr_penalty=0.4, floor=5, publish={"min_coverage": 0.65, "min_major_coverage": 0.55,
                                                      "major_weight": 12})
    data, html = report.build_report(run, score_run(run, anchors), anchors=anchors)
    assert "× (1 − 0,40 × DPR/100)" in html and "(minimo 5)" in html and "≥ 65 %" in html and "≥ 55 %" in html
    assert "peso ≥ 12" in html and "fino al 40 %" in html and "0,30" not in html.split("<footer>")[1]
    assert data["method"]["dpr_penalty"] == 0.4


def test_absence_caveat_for_unread_widgets():
    run = load("audit_complete")
    for page in run["pages"]:
        page["audit"]["snippets"] = [s for s in page["audit"]["snippets"] if s["kind"] != "testimonial"]
        page["audit"]["a11y"] = {"iframes": 1 if page["stage"] == "pdp" else 0, "shadow_roots_closed": 0}
    run["judgments"]["tasks"] = [t for t in run["judgments"]["tasks"] if t["rubric_id"] != "social_proof"]
    run["judgments"]["final"] = [f for f in run["judgments"]["final"] if not f["task_id"].startswith("social_proof")]
    data, html = build(run)
    social = next(k for k in score_run(run)["overall"]["kpis"] if k["id"] == "MPI.SOCIAL_PROOF_RICH")
    assert (social["value"], social["source"], social["reason"]) == ("absent", "deterministic", "no_snippets")
    assert data["caveats"] == {"MPI.SOCIAL_PROOF_RICH": report.WIDGET_CAVEAT}
    assert "vale come assenza" in html and report.WIDGET_CAVEAT in html


def test_absence_reason_follows_the_value_read_without_llm():
    run = load("audit_complete")
    for page in run["pages"]:
        page["audit"]["snippets"] = [s for s in page["audit"]["snippets"] if s["kind"] != "testimonial"]
        if page["stage"] == "pdp":
            page["audit"]["pdp"] = {**page["audit"].get("pdp", {}), "reviews": {"count_text": "37 recensioni"}}
    run["judgments"] = {}
    from jev_ultrafast.engagement import judgments

    judgments.ensure_tasks(run)
    run["judgments"]["final"] = [{"task_id": t["task_id"], "kpi_id": t["kpi_id"], "label": None, "agreement": 0.34,
                                  "samples": 3, "confidence": 0.5, "models": ["m"], "evidence": []}
                                 for t in run["judgments"]["tasks"]]
    _, html = build(run)
    row = html[html.index("<code>MPI.SOCIAL_PROOF_RICH</code>"):]
    row = row[: row.index("</tr>")]
    assert '<td class="num">di base</td>' in row and "valore minimo ricavato da link, rating o badge" in row
    assert "vale come assenza" not in row


def test_report_has_no_external_resources():
    assert len(external_references('<img src="https://x/y.png"><script></script><p style="background:url(a)">')) == 3
    assert external_references("<p>&lt;img src=x&gt; https://example.com</p>") == []
    _, html = build(load("audit_complete"))
    assert external_references(html) == []
    _, journey_html = build(load("journey_run"), steps=steps())
    assert external_references(journey_html) == []


def test_thumbnails_must_be_inline_data_uris():
    run = load("audit_complete")
    data_uri = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="
    _, html = build(run, thumbnails={"mobile-home-1": data_uri, "mobile-pdp-1": "https://cdn.example/x.jpg",
                                     "mobile-cart-1": "javascript:alert(1)"})
    assert data_uri in html and "cdn.example" not in html and "javascript:" not in html
    assert external_references(html) == []


def test_page_derived_text_is_escaped():
    run = load("audit_complete")
    run["site"]["host"] = "shop<b>bold</b>.example"
    run["pages"][0]["final_url"] = "https://shop.example/" + ATTACK
    run["warnings"].append(ATTACK)
    run["not_assessable"].append({"stage": ATTACK, "profile": "mobile", "kpi_id": None, "reason": ATTACK})
    for row in run["observations"]:
        if row["kpi_id"] == "DPR.PRECHECKED_PAID_ADDONS":
            row["evidence"] = {"label": ATTACK, "quotes": [ATTACK]}
        if row["kpi_id"] == "PERF.LCP":
            row.update(assessed=False, value=None, reason=ATTACK)
    run["judgments"]["final"][0]["evidence"] = [{"snippet_id": "s4", "quote": ATTACK}]
    journey = load("journey_run")
    journey["journey"]["goal"] = ATTACK
    journey_steps = steps()
    journey_steps[0]["label"] = ATTACK
    journey_steps[0]["url_after"] = ATTACK
    for html in (build(run)[1], build(journey, steps=journey_steps)[1]):
        assert "<script" not in html and "<img" not in html and "<b>bold" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert external_references(html) == []


def test_journey_report_timeline_and_unpublished_index():
    run = load("journey_run")
    data, html = build(run, steps=steps())
    assert data["journey"]["steps"] == 7 and data["journey"]["verification"]["passed"] is True
    assert data["headline"]["published"] is False and data["headline"]["ers"] is None
    assert "Percorso dell'agente" in html and run["journey"]["goal"] in html
    assert "Verifica indipendente (prodotto nel carrello entro il prezzo massimo) <b>superata</b>" in html
    assert html.count("<li><b>") == 7 and "segnali: click senza effetto" in html and "Prezzo crescente" in html
    assert "dead_click" not in html  # step flags are shown in Italian
    assert "Indice non pubblicato" in html and "copertura complessiva" in html
    assert data["headline"]["reason"].startswith(report.JOURNEY_ONLY)
    assert report.JOURNEY_ONLY.replace("'", "&#x27;") in html
    assert "<b>n/d</b>" in html or ">n/d<" in html
    assert build(run)[0]["journey"]["steps"] == 0
    assert "· tipo: journey (percorso dell&#x27;agente) · stato: completa ·" in html


def test_report_vocabulary():
    for run, kwargs in ((load("audit_complete"), {}), (load("journey_run"), {"steps": steps()})):
        _, html = build(run, **kwargs)
        lowered = html.lower()
        assert "violazion" not in lowered and "violation" not in lowered
        assert lowered.count("engagement misurato") == lowered.count("non è engagement misurato")
        assert "segnali di rischio" in lowered and "attrito previsto" in lowered


@pytest.mark.parametrize(
    "value, unit, text",
    [(None, "ms", "—"), (True, "bool", "sì"), (False, "bool", "no"), (2500, "ms", "2 500 ms"),
     (12.5, "%", "12,5 %"), (0.533, "confidence", "0,53"), (37, "count", "37"), ("4.0-4.7", "enum", "4.0-4.7")],
)
def test_value_formatting(value, unit, text):
    assert report.fmt_value(value, unit) == text


def test_audit_report_shows_linked_journeys():
    audit, journey = load("audit_complete"), load("journey_run")
    scores = score_run(audit, journeys=[journey])
    data, html = report.build_report(audit, scores, journeys=[{"run": journey, "steps": steps()}])
    assert data["headline"]["published"] is True and data["journey"] is None
    assert data["journeys"][0]["run_id"] == journey["run_id"] and data["journeys"][0]["steps"] == 7
    assert "Percorso dell'agente (run collegata)" in html and journey["run_id"] in html
    assert html.count("<li><b>") == 7 and external_references(html) == []


class FakeStore:
    def __init__(self, root, run, steps=None, others=()):
        self.root = Path(root)
        self.run = run
        self.steps = steps or []
        self.runs = {r["run_id"]: (r, s) for r, s in others}
        self.path(run["run_id"]).mkdir(parents=True)

    def load(self, run_id):
        if run_id in self.runs:
            return copy.deepcopy(self.runs[run_id][0])
        if run_id != self.run["run_id"]:
            raise FileNotFoundError(run_id)
        return copy.deepcopy(self.run)

    def path(self, run_id):
        return self.root / run_id

    def read_steps(self, run_id):
        return list(self.runs[run_id][1]) if run_id in self.runs else list(self.steps)

    def write_json(self, run_id, rel, payload):  # like RunStore: the path relative to the run directory
        (self.path(run_id) / rel).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return rel

    def write_bytes(self, run_id, rel, data):
        (self.path(run_id) / rel).write_bytes(data)
        return rel


def test_write_report_with_fake_store(tmp_path):
    from PIL import Image

    run = load("audit_complete")
    run["pages"][0]["screenshot"] = "shots/mobile-home-1.png"
    run["pages"][1]["screenshot"] = "shots/missing.png"
    store = FakeStore(tmp_path, run)
    (store.path(run["run_id"]) / "shots").mkdir()
    Image.new("RGB", (390, 1600), (40, 120, 90)).save(store.path(run["run_id"]) / "shots" / "mobile-home-1.png")
    paths = report.write_report(store, run["run_id"])
    assert set(paths) == {"report_json", "report_html"}
    assert paths["report_json"] == str(store.path(run["run_id"]) / "report.json")  # absolute, like report_html
    saved = json.loads(Path(paths["report_json"]).read_text(encoding="utf-8"))
    html = Path(paths["report_html"]).read_text(encoding="utf-8")
    assert saved["run_id"] == run["run_id"] and saved["headline"]["ers"] == score_run(run)["overall"]["ers"]["score"]
    assert Path(paths["report_html"]).parent == store.path(run["run_id"])
    assert html.count('src="data:image/jpeg;base64,') == 1 and external_references(html) == []
    thumb = re.search(r'src="(data:image/jpeg;base64,[^"]+)"', html).group(1)
    assert len(thumb) < report.THUMB_MAX_BYTES * 4 // 3 + 64
    assert not list(store.path(run["run_id"]).glob("*.tmp"))

    journey = load("journey_run")
    journey_store = FakeStore(tmp_path / "j", journey, steps())
    html = Path(report.write_report(journey_store, journey["run_id"])["report_html"]).read_text(encoding="utf-8")
    assert html.count("<li><b>") == 7


def test_write_report_returns_absolute_paths_with_the_real_store(tmp_path):
    from jev_ultrafast.engagement.store import RunStore

    store = RunStore(tmp_path)
    run = load("audit_complete")
    run_id = store.new_run("audit", run["site"]["start_url"], run["settings"])
    store.save(run_id, {**run, "run_id": run_id})
    paths = report.write_report(store, run_id)
    for key, name in (("report_json", "report.json"), ("report_html", "report.html")):
        assert Path(paths[key]).is_absolute() and Path(paths[key]) == store.path(run_id) / name
        assert Path(paths[key]).is_file()
    assert "<h1>shop.example</h1>" in Path(paths["report_html"]).read_text(encoding="utf-8")
    assert not [p.name for p in store.path(run_id).iterdir() if p.name.startswith(".report") or p.suffix == ".tmp"]
    assert json.loads(Path(paths["report_json"]).read_text(encoding="utf-8"))["run_id"] == run_id


def test_write_report_links_journeys_from_arguments_or_stored_scores(tmp_path):
    audit, journey = load("audit_complete"), load("journey_run")
    store = FakeStore(tmp_path / "a", audit, others=[(journey, steps())])
    paths = report.write_report(store, audit["run_id"], journey_run_ids=[journey["run_id"]])
    saved = json.loads(Path(paths["report_json"]).read_text(encoding="utf-8"))
    assert saved["scores"]["overall"]["context"]["journey_runs"] == [journey["run_id"]]
    assert saved["journeys"][0]["steps"] == 7 and saved["headline"]["published"] is True
    stored = copy.deepcopy(audit)
    stored["scores"] = score_run(audit, journeys=[journey])
    store = FakeStore(tmp_path / "b", stored, others=[(journey, steps())])
    saved = json.loads(Path(report.write_report(store, audit["run_id"])["report_json"]).read_text(encoding="utf-8"))
    assert saved["journeys"][0]["run_id"] == journey["run_id"]
    missing = FakeStore(tmp_path / "c", audit)
    saved = json.loads(Path(report.write_report(missing, audit["run_id"], journey_run_ids=["gone"])["report_json"])
                       .read_text(encoding="utf-8"))
    assert saved["journeys"] == [] and any("gone" in w for w in saved["warnings"])


# ---------------------------------------------------------------- reason labels

PRODUCERS = ("checks.py", "deception.py", "crawler.py", "friction.py", "journey.py", "oracles.py", "judgments.py",
             "audit.py", "collectors.py", "scoring.py", "safety.py")
# f-string heads whose detail is another producer's code: the guard's refusals behind crawler.Tab.act's
# f"guard_refused:{why}" and, for the search field, checks._autocomplete's f"probe:{reason}"
HEAD_DETAILS = {"guard_refused:": "safety.py", "probe:guard_refused:": "safety.py"}
REASON_NAMES = {"reason", "then", "unverified"}  # keywords and variables that hold a reason
REASON_KEYS = {"reason", "not_assessable", "unreadable"}  # dict keys and subscripts that hold one
REASON_ARGS = {"_na": 0, "NotAssessed": 0, "Stop": 1, "_missing": 1, "mark": 1}  # call: index of the reason argument
NOT_REASONS = {"quiet", "load_only"}  # settle outcomes (vitals.settled_reason, step settle_reason), never a reason
NOT_TEMPLATES = {"personal_text:"}  # a guard refusal note the journey host reads, never a KPI or stage reason


def reason_literals(source):
    """(codes, f-string heads) a producer writes in a reason position: reason=/then=/unverified= keywords, the
    "reason"/"not_assessable"/"unreadable" keys, variables of those names, self.last_error, _na()/NotAssessed()/
    Stop()/_missing()/mark() arguments, the reason of an (executed, reason) return and what reason() methods return.
    Conditional expressions, `or` chains, local variables and `{...}.get(key, default)` maps are followed: literals
    and simple assignments, never reasons read from data (record["consent"]["reason"]); a producer that ever surfaces
    such a reason (the consent sentences of PageRecord.consent) must map it to a code first."""
    tree = ast.parse(source)
    assigned = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, []).append(node.value)
    codes, heads, followed = set(), set(), set()

    def collect(expr):
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            codes.add(expr.value)
        elif isinstance(expr, ast.JoinedStr) and expr.values and isinstance(expr.values[0], ast.Constant):
            heads.add(expr.values[0].value)
        elif isinstance(expr, ast.IfExp):
            collect(expr.body)
            collect(expr.orelse)
        elif isinstance(expr, ast.BoolOp):
            for value in expr.values:
                collect(value)
        elif isinstance(expr, ast.Name) and expr.id not in followed:
            followed.add(expr.id)
            for value in assigned.get(expr.id, []):
                collect(value)
        elif isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "get":
            for value in expr.func.value.values if isinstance(expr.func.value, ast.Dict) else []:
                collect(value)
            for value in expr.args[1:]:
                collect(value)

    def holds_reason(target):
        return (isinstance(target, ast.Name) and target.id in REASON_NAMES) or (
            isinstance(target, ast.Attribute) and target.attr == "last_error") or (
            isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
            and target.slice.value in REASON_KEYS)

    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in REASON_NAMES:
            collect(node.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in REASON_KEYS:
                    collect(value)
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name in REASON_ARGS:
                for value in node.args[REASON_ARGS[name]:][:2]:  # Stop(stage, reason, then)
                    collect(value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                pairs = [(target, node.value)]
                if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    pairs = list(zip(target.elts, node.value.elts))
                for left, right in pairs:
                    if holds_reason(left):
                        collect(right)
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple) and len(node.value.elts) == 2:
            flag = node.value.elts[0]
            if isinstance(flag, ast.Constant) and isinstance(flag.value, bool):
                collect(node.value.elts[1])
        elif isinstance(node, ast.FunctionDef) and node.name == "reason":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and inner.value is not None:
                    collect(inner.value)
    return codes, heads


def verdict_rejections():
    """judgments.submit()'s rejection codes, the (None, "code") pairs of _check() and submit(): the judge host reads
    them (MCP rejected_reasons), the report never shows them."""
    found = set()
    for node in ast.walk(ast.parse((PACKAGE / "judgments.py").read_text(encoding="utf-8"))):
        pair = node.value if isinstance(node, (ast.Return, ast.Assign)) else None
        if (isinstance(pair, ast.Tuple) and len(pair.elts) == 2 and isinstance(pair.elts[0], ast.Constant)
                and pair.elts[0].value is None and isinstance(pair.elts[1], ast.Constant)):
            found.add(pair.elts[1].value)
    return found


def producer_reasons():
    codes, heads = {}, {}
    for name in PRODUCERS:
        found, templates = reason_literals((PACKAGE / name).read_text(encoding="utf-8"))
        for code in found:
            codes.setdefault(code, []).append(name)
        for head in templates:
            heads.setdefault(head, []).append(name)
    return codes, heads


def test_reason_collector_reads_every_reason_position():
    source = '''
def check(page, test, clicked):
    _na("audit_unavailable")
    row = _row(kpi, None, reason="no_rating" if page else "x_else")
    out = {"assessed": False, "reason": test.get("reason") or "not_assessed"}
    raise Stop("cart", "out_of_stock", then="cart_not_found")
    raise Stop("checkout_entry", {"control_not_found": "checkout_cta_not_found"}.get(clicked, "click_failed"))
    self.last_error = "navigation_error"
    passed, checks["not_assessable"] = None, "cart_price_ambiguous"
    reason = f"revisit_{failure}"
    hint = "a_label_not_a_reason"
    return False, "sent_earlier"
'''
    codes, heads = reason_literals(source)
    assert codes == {"audit_unavailable", "no_rating", "x_else", "not_assessed", "out_of_stock", "cart_not_found",
                     "checkout_cta_not_found", "click_failed", "navigation_error", "cart_price_ambiguous",
                     "sent_earlier"}
    assert heads == {"revisit_"}


def test_every_producer_reason_has_an_italian_label():
    codes, heads = producer_reasons()
    assert {"navigation_error", "journey_error", "page_unreadable", "out_of_stock", "not_requested",
            "countdown_not_paired", "sent_earlier"} <= set(codes)  # the collector still reads the producers
    unlabelled = {code: files for code, files in codes.items()
                  if code not in NOT_REASONS | verdict_rejections() and report.reason_label(code) is None}
    assert unlabelled == {}, "add these reasons to report.REASONS (or a REASON_PREFIXES rule)"
    loose = {head: files for head, files in heads.items() if not head.startswith(tuple(NOT_TEMPLATES))
             and report.reason_label(head + "Dettaglio dinamico") is None}
    assert loose == {}, "add a report.REASON_PREFIXES rule for these f-string reasons"
    for head, source in HEAD_DETAILS.items():  # an identifier detail composes only when it has its own label
        found, templates = reason_literals((PACKAGE / source).read_text(encoding="utf-8"))
        details = found | {template + "some_value" for template in templates}  # f"protected_field:{type}"
        assert {"personal_field", "checkout_submit", "protected_field:some_value"} <= details
        unlabelled = sorted(d for d in details if report.reason_label(head + d) is None)
        assert unlabelled == [], f"add report.REASONS (or REASON_VALUES) labels for these {head} details"


def test_verdict_rejections_have_no_report_label():
    rejections = verdict_rejections()
    assert {"invalid_verdict", "duplicate_judge", "unknown_label", "quote_not_verbatim", "missing_evidence"} <= (
        rejections)
    assert sorted(code for code in rejections if report.reason_label(code) is not None) == []  # none, not some


def test_reason_code_warnings_have_an_italian_label():
    heads = set()  # the journey's "<code>: <detail>" run warnings (report.warning_text)
    for node in ast.walk(ast.parse((PACKAGE / "journey.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "_warn" and node.args:
            first = node.args[0]
            first = first.values[0] if isinstance(first, ast.JoinedStr) and first.values else first
            text = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else ""
            if match := report.CODE_WARNING.match(text + "detail"):
                heads.add(match["code"])
    assert {"bot_challenge", "left_shop", "navigation_error", "renderer_crashed", "page_unreadable"} <= heads
    assert sorted(head for head in heads if head not in report.REASONS) == []
    for head in heads:
        assert report.warning_text(f"{head}: detail") == f"{report.REASONS[head]} (detail)"


@pytest.mark.parametrize("name", ["audit_complete", "journey_run"])
def test_golden_run_reasons_are_producer_shaped(name):
    run = load(name)
    reasons = {row.get("reason") for row in [*run["observations"], *run["not_assessable"]]} - {None}
    assert sorted(r for r in reasons if report.reason_label(r) is None) == []  # a code no producer writes


@pytest.mark.parametrize("reason, text", [
    ("navigation_error", "pagina non caricata: errore di rete del browser"),
    ("not_applicable:out_of_stock", "non applicabile: prodotto esaurito"),
    ("not_applicable: nessuna azione eseguita", "non applicabile: nessuna azione eseguita"),
    ("no_measurement: effetto non misurato", "non misurato: effetto non misurato"),
    ("probe:no_category_word", "test della ricerca non riuscito: nessuna parola di categoria da cercare"),
    ("probe:failed:TimeoutError", "test della ricerca non riuscito: azione non riuscita: TimeoutError"),
    ("revisit_navigation_error", "nuova visita non riuscita: pagina non caricata: errore di rete del browser"),
    ("error: KeyError: 'x'", "errore imprevisto del sistema di prova: KeyError: 'x'"),
    ("credit_withheld:DPR.FAKE_LOW_STOCK", "credito non assegnato: segnale di rischio DPR.FAKE_LOW_STOCK"),
    ("probe:guard_refused:personal_field", "test della ricerca non riuscito: rifiutato dalla guardia di sicurezza: "
                                           "campo per dati personali: mai compilato"),
    ("probe:guard_refused:protected_field:password", "test della ricerca non riuscito: rifiutato dalla guardia di "
                                                     "sicurezza: campo protetto di tipo password: mai compilato"),
    ("guard_refused:personal_field:address-line1", "rifiutato dalla guardia di sicurezza: campo per dati personali "
                                                   "(address-line1): mai compilato"),
    ("guard_refused:forbidden_label:pay_now", "rifiutato dalla guardia di sicurezza: pulsante di pagamento: "
                                              "mai premuto"),
])
def test_reason_text_composes_prefixes(reason, text):
    assert report.reason_text(reason) == text and report.reason_label(reason) == text


def test_unknown_reason_codes_have_no_label_and_show_as_written():
    for code in ("brand_new_reason", "not_applicable:brand_new", "probe:brand_new", "revisit_brand_new",
                 "probe:guard_refused:brand_new", "probe:guard_refused:brand:new_code", "not_applicable:a:b:c"):
        assert report.reason_label(code) is None and report.reason_text(code) == code


def test_journey_verification_reason_is_shown_in_italian():
    run = load("journey_run")
    run["journey"]["verification"] = {"passed": None, "checks": {"oracle": "cart_not_empty",
                                                                 "not_assessable": "cart_price_ambiguous"}}
    _, html = build(run, steps=steps())
    assert "Verifica indipendente (prodotto nel carrello entro il prezzo massimo) <b>non valutabile</b>" in html
    assert "Verifica non valutabile: prezzo nel carrello ambiguo tra prezzo unitario e totale di riga." in html


@pytest.mark.parametrize("warning, text", [
    ("mobile: checkout_entry not assessable (navigation_error)",
     "mobile: fase primo step del checkout non valutabile: pagina non caricata: errore di rete del browser"),
    ("desktop: cart not assessable (error: KeyError: 'x')",
     "desktop: fase carrello non valutabile: errore imprevisto del sistema di prova: KeyError: 'x'"),
    ("desktop: brand_new not assessable (timeout)", "desktop: fase brand_new non valutabile: tempo scaduto"),
    ("bot_challenge: an anti-bot page interrupted the journey (no evasion is attempted)",
     "pagina di verifica anti-bot (an anti-bot page interrupted the journey (no evasion is attempted))"),
    ("finished by the host without DONE/BLOCKED", "finished by the host without DONE/BLOCKED"),
    ("left_shop: https://pay.example/", "percorso uscito dal negozio verso un altro sito (https://pay.example/)"),
    ("brand_new_code: detail", "brand_new_code: detail"),
    ("mobile: cart reached after a Jev pick (add_to_cart): not reproducible across runs",  # audit.py
     "mobile: fase carrello raggiunta dopo un elemento scelto da Jev con il fallback (aggiunta al carrello): non "
     "riproducibile tra run diverse"),
    ("desktop: cart reached after a Jev pick (select_variant, add_to_cart): not reproducible across runs",
     "desktop: fase carrello raggiunta dopo elementi scelti da Jev con il fallback (scelta della variante, "
     "aggiunta al carrello): non riproducibile tra run diverse"),
    ("mobile: checkout_entry reached after a Jev pick (brand_new): not reproducible across runs",
     "mobile: fase primo step del checkout raggiunta dopo un elemento scelto da Jev con il fallback (brand_new): non "
     "riproducibile tra run diverse"),
    ("text_helper_unavailable: Jev chose TYPE_TEXT into 'Cerca' on https://shop.example/; no TEXT_MODEL_API_KEY",
     "aiuto testuale non disponibile: TYPE_TEXT scelto da Jev e rifiutato (nessuna chiave per l'aiuto testuale) (Jev "
     "chose TYPE_TEXT into 'Cerca' on https://shop.example/; no TEXT_MODEL_API_KEY)"),
])
def test_known_warning_patterns_are_shown_in_italian(warning, text):
    assert report.warning_text(warning) == text
    run = load("audit_complete")
    run["warnings"] = [warning]
    assert report.e(text) in build(run)[1]


# ---------------------------------------------------------------- Jev and Claude judges, Jev as pilot


def jev_choice(ids, selected, p=0.9):
    ids = list(ids)
    rest = (1 - p) / (len(ids) - 1)
    return {"choice": selected, "probabilities": {i: p if i == selected else rest for i in ids},
            "confidence": (p - 1 / len(ids)) / (1 - 1 / len(ids))}


def mixed_run(monkeypatch, tmp_path, claude_judges=("j1", "j2", "j3")):
    """The golden audit judged again: Jev answers its rubrics with the golden majority label (a request stand-in, no
    network) and flips the reversed head on the golden's uncertain task, which Claude's three golden samples then
    decide; Claude's rubrics keep their golden samples. claude_judges ("j1",): one Claude sample per task, finalised
    early (judge --samples 1)."""
    run = load("audit_complete")
    golden = run["judgments"]
    finals = {f["task_id"]: f for f in golden["final"]}
    run["judgments"] = {}

    def post(url, key, body):
        assert url == judges.SYSTEMONE and key == "test-key" and "https://" not in json.dumps(body["state"])
        answers = {}
        for qid, question in body["questions"].items():
            kind, rubric, page, *label = qid.split(":")
            final, labels = finals[f"{rubric}:{page}"], list(question["criteria"])
            chosen = final["label"] or list(body["questions"][f"label:{rubric}:{page}"]["criteria"])[0]
            if kind == "label":
                answers[qid] = jev_choice(labels, chosen)
            elif kind == "label_rev":
                answers[qid] = jev_choice(labels, chosen if final["label"] else labels[0])
            elif label == [chosen]:
                answers[qid] = jev_choice(labels, (final["evidence"] or [{"snippet_id": labels[0]}])[0]["snippet_id"])
        return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 900, "output_tokens": 0}}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("JEV_ENGAGEMENT_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(typesafe, "post_json", post)
    summary = judges.judge_with_jev(run, use_cache=False)
    open_tasks = set(summary["finalize"]["pending"])
    for judge_id in claude_judges:
        mine = [v for v in golden["verdicts"] if v["judge_id"] == judge_id and v["task_id"] in open_tasks]
        assert judgments.submit(run, judge_id, "claude-sonnet-5-5", mine, cache=False)["rejected"] == []
    assert judgments.finalize(run, samples_required=len(claude_judges))["pending"] == []
    return run, summary


def test_report_names_who_judged_with_jev_and_claude(monkeypatch, tmp_path):
    run, summary = mixed_run(monkeypatch, tmp_path)
    assert summary["requests"] == 4 and summary["escalated"] == {"position_flip": 1}  # four pages hold Jev tasks
    data, html = build(run)
    jev_kpis = ["DPR.HIDDEN_SUBSCRIPTION", "DPR.TRICK_QUESTIONS", "MPI.AUTHORITY", "MPI.SOCIAL_PROOF_RICH",
                "TRI.RETURNS_CLARITY"]
    assert data["judgments"] == {"tasks": 16, "verdicts": 10 + 6 * 3, "final": 16,
                                 "models": ["claude-sonnet-5-5", "jev-1.13.0"],
                                 "by_model": {"claude-sonnet-5-5": 6, "jev-1.13.0": 10}, "single_sample": 10,
                                 "escalated": 1, "escalated_final": 1, "jev_kpis": jev_kpis,
                                 "jev_models": ["jev-1.13.0"],
                                 "claude_kpis": ["CCL.VALUE_PROP_CLARITY", "DPR.CONFIRMSHAMING"],
                                 "claude_samples": {"3": 6}}
    assert data["model_calls"]["judge_jev"]["requests"] == 4 and "<p>Richieste TypeSafe: giudice Jev 4.</p>" in html
    sentence = (f"I giudizi di {', '.join(jev_kpis)} sono scelte del modello TypeSafe Jev (jev-1.13.0) fra etichette "
                "chiuse, con l'evidenza scelta fra gli snippet offerti e un controllo di stabilità a ordine invertito, "
                "un campione per task; i giudizi di CCL.VALUE_PROP_CLARITY, DPR.CONFIRMSHAMING (e il task che Jev ha "
                "passato a Claude) sono la maggioranza di 3 campioni dello stesso modello con lo stesso prompt, con "
                "citazioni verificate parola per parola sul testo della pagina: la maggioranza controlla la "
                "stabilità, non un accordo fra valutatori indipendenti. Modelli dei valutatori: claude-sonnet-5-5, "
                "jev-1.13.0.")
    assert report.e(sentence) in html and "che sono campioni dello stesso modello" not in html
    rows = {(r["kpi_id"], r["page_id"]): r for r in judgments.observations_from_final(run) if r["source"] == "judged"}
    assert rows[("MPI.SOCIAL_PROOF_RICH", "mobile-home-1")]["reason"] == "judgment_uncertain"  # Claude's samples
    assert rows[("DPR.TRICK_QUESTIONS", "mobile-cart-1")]["value"] == 0.8  # one Jev sample: agreement 1.0
    assert rows[("DPR.TRICK_QUESTIONS", "mobile-cart-1")]["evidence"]["models"] == ["jev-1.13.0"]
    assert score_run(run)["overall"]["ers"]["llm_share"] > 0  # Jev's verdicts are judged rows like Claude's

    only_jev = copy.deepcopy(run)  # no Claude final at all: the sentence ends after Jev's part
    state = only_jev["judgments"]
    claude = {t["task_id"] for t in state["tasks"] if judgments.routing(t) == "claude" or t.get("escalation")}
    state["final"] = [f for f in state["final"] if f["task_id"] not in claude]
    state["tasks"] = [t for t in state["tasks"] if t["task_id"] not in claude]
    _, html = build(only_jev)
    assert report.e("un campione per task. Modelli dei valutatori: claude-sonnet-5-5, jev-1.13.0.") not in html
    assert report.e("ordine invertito, un campione per task. Modelli dei valutatori: jev-1.13.0.") in html

    escalated_only = copy.deepcopy(run)  # Claude judged only Jev's escalation
    state = escalated_only["judgments"]
    state["final"] = [f for f in state["final"] if judgments.routing(
        next(t for t in state["tasks"] if t["task_id"] == f["task_id"])) == "jev"]
    _, html = build(escalated_only)
    assert report.e("; il giudizio del task che Jev ha passato a Claude è la maggioranza di 3 campioni") in html

    pending = copy.deepcopy(run)  # Claude has not judged yet: the escalation is pending, no Claude sample exists
    state = pending["judgments"]
    state["final"] = [f for f in state["final"] if f["task_id"] not in claude]
    escalated = [t["task_id"] for t in state["tasks"] if t.get("escalation")]
    state["verdicts"] = [v for v in state["verdicts"] if v["judge_id"] == judges.JEV_JUDGE_ID]
    assert len(escalated) == 1 and escalated[0] not in {f["task_id"] for f in state["final"]}
    data, html = build(pending)
    assert (data["judgments"]["escalated"], data["judgments"]["escalated_final"]) == (1, 0)
    assert report.e("ordine invertito, un campione per task. Modelli dei valutatori: jev-1.13.0.") in html
    assert "passato a Claude" not in html and "maggioranza di 3 campioni" not in html
    some = copy.deepcopy(run)  # Claude's rubrics decided, the escalation still pending: only Claude's KPIs named
    state = some["judgments"]
    state["final"] = [f for f in state["final"] if f["task_id"] not in escalated]
    _, html = build(some)
    assert report.e("; i giudizi di CCL.VALUE_PROP_CLARITY, DPR.CONFIRMSHAMING sono la maggioranza") in html
    assert "passato a Claude" not in html


def test_journey_card_names_the_pilot_and_its_decision_time():
    run = load("journey_run")
    _, html = build(run, steps=steps())
    assert "pilota:" not in html  # a run recorded before model calls were persisted
    run["journey"].update(policy="typesafe", policy_requested="auto", text_helper="deepseek-chat",
                          model_calls={"choose": 7, "text": 1, "stale_or_refused": 1, "failed": 2},
                          timing_ms={"decision": 3240, "text": 410, "site": 12430, "wall": 18000},
                          usage={"input_tokens": 61000, "output_tokens": 0})
    data, html = build(run, steps=steps())
    line = ("pilota: Jev (TypeSafe) · decisioni 7 (3,2 s, escluse dal tempo del sito) · decisioni non riuscite 2 · "
            "testi generati 1, deepseek-chat · tempo del sito 12,4 s")  # a failed TypeSafe call is no decision
    assert f'<p class="muted">{report.e(line)}</p>' in html
    assert data["journey"]["model_calls"]["choose"] == 7 and data["journey"]["policy_requested"] == "auto"
    assert data["journey"]["timing_ms"]["site"] == 12430 and data["journey"]["text_helper"] == "deepseek-chat"
    run["journey"]["model_calls"].update(text=0, text_failed=2)  # sent, but no usable text came back
    _, html = build(run, steps=steps())
    assert report.e("decisioni non riuscite 2 · richieste di testo senza testo 2 · tempo del sito") in html
    run["journey"].update(policy="host", text_helper=None, model_calls={"choose": 5, "text": 0, "failed": 0},
                          timing_ms={})
    _, html = build(run, steps=steps())
    assert f'<p class="muted">{report.e("pilota: agente host · decisioni 5")}</p>' in html


def test_text_helper_and_jev_fallback_reasons_are_shown_in_italian():
    run = load("journey_run")
    run["journey"]["verification"] = {"passed": None, "checks": {"oracle": "cart_not_empty",
                                                                 "not_assessable": "text_helper_unavailable"}}
    _, html = build(run, steps=steps())
    assert ("Verifica non valutabile: aiuto testuale non disponibile: TYPE_TEXT scelto da Jev e rifiutato (nessuna "
            "chiave per l&#x27;aiuto testuale).") in html  # TYPE_TEXT stays offered: Jev's pick of it is refused
    run["journey"]["verification"]["checks"]["not_assessable"] = "text_refused"  # the guard refused Jev's text
    _, html = build(run, steps=steps())
    assert "Verifica non valutabile: testo rifiutato dalla guardia di sicurezza: TYPE_TEXT non più offerto." in html
    warning = "mobile: plp found through the Jev fallback: not reproducible across runs"
    text = ("mobile: fase listing di categoria trovata con il fallback Jev (elemento scelto dal modello): non "
            "riproducibile tra run diverse")
    assert report.warning_text(warning) == text and report.warning_text("mobile: plp found through the Jev "
                                                                        "fallback") == text
    for code in ("jev_blocked", "jev_done", "jev_error", "no_click_actions", "fallback_exhausted"):
        assert report.reason_label(code).startswith("fallback Jev")
    assert report.reason_label("jev_" + "Dettaglio dinamico") == "fallback Jev: Dettaglio dinamico"  # f"jev_{op}"


def test_claude_finals_are_described_by_their_real_sample_counts(monkeypatch, tmp_path):
    """judge --samples 1 (or finalize_judgments(samples_required=1)) leaves Claude's finals with one sample each: the
    footer never calls them a majority, with or without Jev, and names the single ones beside a majority."""
    run = load("audit_complete")
    state = run["judgments"]
    tasks = [t["task_id"] for t in state["tasks"]]
    state["final"], three = [], set(tasks[: len(tasks) // 2])
    state["verdicts"] = [v for v in state["verdicts"] if v["judge_id"] == "j1" or v["task_id"] in three]
    judgments.finalize(run, samples_required=1)
    data, html = build(run)
    single = len(tasks) - len(three)
    assert data["judgments"]["claude_samples"] == {"1": single, "3": len(three)}
    assert data["judgments"]["single_sample"] == single
    assert report.e(f"la maggioranza di 3 valutatori, che sono campioni dello stesso modello con lo stesso prompt: la "
                    "maggioranza controlla la stabilità del giudizio, non un accordo tra valutatori indipendenti "
                    f"({single} giudizi di un solo campione, senza maggioranza).") in html

    state["final"], state["verdicts"] = [], [v for v in state["verdicts"] if v["judge_id"] == "j1"]
    judgments.finalize(run, samples_required=1)
    data, html = build(run)
    assert data["judgments"]["claude_samples"] == {"1": len(tasks)}
    assert report.e("I giudizi LLM usano etichette chiuse e citazioni verificate parola per parola sul testo della "
                    "pagina, con un solo campione del modello per task: senza maggioranza non c'è alcun controllo "
                    "della stabilità del giudizio, né un accordo tra valutatori indipendenti.") in html
    assert "maggioranza di" not in html and "più valutatori" not in html

    run, _ = mixed_run(monkeypatch, tmp_path, claude_judges=("j1",))  # Jev's rubrics, then one Claude sample each
    data, html = build(run)
    assert data["judgments"]["claude_samples"] == {"1": 6} and data["judgments"]["escalated_final"] == 1
    assert report.e("; i giudizi di CCL.VALUE_PROP_CLARITY, DPR.CONFIRMSHAMING (e il task che Jev ha passato a "
                    "Claude) sono di un solo campione, senza maggioranza (nessun controllo di stabilità), con "
                    "citazioni verificate parola per parola sul testo della pagina. Modelli dei valutatori:") in html
    assert "maggioranza di" not in html


def test_report_names_who_judged_before_any_final_by_the_protocol():
    run = load("audit_complete")
    run["judgments"]["final"] = []  # tasks and verdicts, nothing finalised yet: no sample count to report
    _, html = build(run)
    assert "la maggioranza di più valutatori, che sono campioni dello stesso modello" in html


def coverage_of(output, name):
    """The unrounded coverage of a sub-index, recomputed from the KPI rows as scoring._sub_index does."""
    members = [k for k in output["kpis"] if k["owner"] == name and k["weight"] > 0 and k["applicable"]]
    assessed = sum(k["weight"] for k in members if k["assessed"] and k["normalized"] is not None)
    return assessed / sum(k["weight"] for k in members)


def test_one_share_formatter_never_rounds_up_to_a_threshold():
    assert report.fmt_share is scoring.fmt_share  # report, CLI and ers() reasons share it
    anchors = load_anchors()
    assert scoring._stored(0.84953, anchors) == scoring._stored(0.8495, anchors) == 0.849  # never 0.85: grade A
    assert scoring._stored(0.4996, anchors) == 0.499 and scoring._stored(0.18519, anchors) == 0.185
    assert scoring._stored(0.9467, anchors) == 0.947  # no threshold in reach: rounded as before
    # one rounding from the share to the whole percentage shown, never two: 18.452 % is 18 %, 18.519 % is 19 %
    assert scoring._three(0.18452) == 0.184 and scoring._three(0.89474) == 0.894 and scoring._three(0.18519) == 0.185
    assert scoring.fmt_share(scoring._three(0.17451)) == "17 %" and scoring.fmt_share(scoring._three(0.1755)) == "18 %"
    grid = [i / 100_000 for i in range(100_001)]
    assert all(scoring.fmt_share(scoring._stored(v, anchors)) == scoring.fmt_share(v) for v in grid
               if not any(v < t <= round(v, 2) + 1e-9 for t in (0.5, 0.6, 0.7, 0.85)))
    assert report.fmt_confidence("B", 0.8495) == report.fmt_confidence("B", 0.849) == "B (copertura 84 %)"
    assert report.fmt_confidence("A", 0.85) == "A (copertura 85 %)"
    assert [scoring.fmt_share(v) for v in (0.185, 0.699, 0.7, 0.57, 0.596, 0.59, 0.499, 0.5, 1, 0, None)] == [
        "19 %", "69 %", "70 %", "57 %", "59,6 %", "59 %", "49,9 %", "50 %", "100 %", "0 %", "n/d"]  # halves up
    assert scoring.fmt_share(0.649, below=0.65) == "64,9 %"  # a threshold of the caller's anchors


def test_a_coverage_just_under_grade_a_reads_84_percent_with_grade_b():
    run = load("audit_complete")  # the reviewer's case: these KPIs timed out, the coverage is 84.95 %
    dropped = {"FAI.CHECKOUT_FIELDS", "FAI.GUEST_CHECKOUT", "FAI.FORCED_ACCOUNT", "FAI.AUTOCOMPLETE_ATTRS",
               "FAI.CART_EDITABLE", "FAI.BREADCRUMBS", "FAI.PLP_RESULT_COUNT", "PTI.SHIPPING_COST_PRE_CHECKOUT",
               "PTI.FUNNEL_PRICE_DELTA", "PTI.STRIKETHROUGH_LOWEST30"}
    for row in run["observations"]:
        if row["kpi_id"] in dropped:
            row.update(assessed=False, value=None, reason="timeout")
    scores = score_run(run)
    overall, weights = scores["overall"], scores["overall"]["weights"]
    exact = sum(weights[n] * coverage_of(overall, n) for n in weights) / sum(weights.values())
    assert 0.8495 <= exact < 0.85 and overall["ers"]["grade"] == "B"
    assert overall["ers"]["coverage"] == 0.849  # stored rounded down: 0.85 would read as grade A
    data, html = report.build_report(run, scores)
    assert "Confidenza <b>B</b> (copertura 84 %)" in html and "(copertura 85 %)" not in html
    assert data["headline"]["coverage"] == 0.849


def test_the_headline_llm_share_is_rounded_once_from_the_unrounded_sub_index_shares():
    """The ERS llm_share combines the sub-indices' unrounded shares (TRI 0.1212, CCL 0.1905 here), never their stored
    three decimals, which would add up to 0.1049 and read as 10 % instead of the exact 10.5 %, 11 %."""
    run = load("audit_complete")
    for row in run["observations"]:
        if row["kpi_id"] in {"FAI.CART_EDITABLE", "FAI.FORCED_ACCOUNT", "TRI.CONTACT_INFO"}:
            row.update(assessed=False, value=None, reason="timeout")
    scores = score_run(run)
    overall = scores["overall"]
    exact = [0.0, 0.0]
    for name, weight in overall["weights"].items():
        members = [k for k in overall["kpis"] if k["owner"] == name and k["weight"] > 0 and k["applicable"]]
        scored = [k for k in members if k["assessed"] and k["normalized"] is not None]
        total = sum(k["weight"] for k in members)
        exact[0] += weight * sum(k["weight"] for k in scored if k["source"] == "judged") / total
        exact[1] += weight * sum(k["weight"] for k in scored) / total
    assert exact[0] / exact[1] == pytest.approx(0.10503, abs=1e-5) and overall["ers"]["llm_share"] == 0.105
    assert all("_llm_share" not in sub for sub in overall["sub_indices"].values())
    _, html = report.build_report(run, scores)
    assert "Quota inferita da LLM <b>11 %</b>" in html


def test_a_coverage_reads_the_same_in_the_unpublished_reason_and_its_card():
    """A major sub-index at 2.5 / 13.5 = 18.52 %: 19 % in the reason and on the card (catalogue 3.5), never 18 %."""
    run = load("audit_complete")
    kept = {"FAI.CHECKOUT_FIELDS", "FAI.BREADCRUMBS"}  # weights 2 + 0.5
    excluded = {"FAI.PDP_CTA_ABOVE_FOLD", "FAI.SEARCH_VISIBLE", "FAI.FORCED_ACCOUNT", "FAI.GUEST_CHECKOUT",
                "FAI.PLP_FILTERS", "FAI.PLP_PAGINATION"}  # weights 9 out of 22.5: 13.5 stay applicable
    for row in run["observations"]:
        if row["kpi_id"].startswith("FAI.") and row["kpi_id"] not in kept:
            reason = "not_applicable:out_of_stock" if row["kpi_id"] in excluded else "timeout"
            row.update(assessed=False, value=None, reason=reason)
    scores = score_run(run)
    overall = scores["overall"]
    assert coverage_of(overall, "FAI") == pytest.approx(2.5 / 13.5) and overall["sub_indices"]["FAI"]["coverage"] == (
        0.185)
    assert "Attrito previsto (19 %)" in overall["ers"]["reason"] and not overall["ers"]["published"]
    _, html = report.build_report(run, scores)
    assert report.e("Attrito previsto (19 %)") in html and "Attrito previsto (18 %)" not in html
    card = html.split("<h3>Attrito previsto ", 1)[1].split("</div>", 1)[0]
    assert "Confidenza n/d (copertura 19 %)" in card
