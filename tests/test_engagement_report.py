import copy
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from jev_ultrafast.engagement import report
from jev_ultrafast.engagement.kpis import KPI_LIST
from jev_ultrafast.engagement.scoring import load_anchors, score_run

RUNS = Path(__file__).parent / "fixtures" / "runs"
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
    assert data["versions"] == {"schema": "engagement.v1", "anchors": "anchors.v1", "rubrics": "rubrics.v1",
                                "profiles": "profiles.v1", "report": "report.v1"}
    assert data["judgments"] == {"tasks": 16, "verdicts": 48, "final": 16, "models": ["claude-sonnet-5-5"]}
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
    for version in ("anchors.v1", "rubrics.v1", "profiles.v1", "engagement.v1"):
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
    assert "<code>DPR.COUNTDOWN_RESET</code>: nessuna osservazione" in html

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
    assert "basic" in row and "valore minimo ricavato da link, rating o badge" in row and "vale come assenza" not in row


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
    assert "superata" in html and "cart_contains_item_under_price" in html
    assert html.count("<li><b>") == 7 and "dead_click" in html and "Prezzo crescente" in html
    assert "Indice non pubblicato" in html and "copertura complessiva" in html
    assert data["headline"]["reason"].startswith(report.JOURNEY_ONLY)
    assert report.JOURNEY_ONLY.replace("'", "&#x27;") in html
    assert "<b>n/d</b>" in html or ">n/d<" in html
    assert build(run)[0]["journey"]["steps"] == 0


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

    def write_json(self, run_id, rel, payload):
        target = self.path(run_id) / rel
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return str(target)


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
