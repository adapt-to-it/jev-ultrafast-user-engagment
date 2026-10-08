import copy
import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

from jev_ultrafast import model as typesafe
from jev_ultrafast.engagement import judges, judgments
from jev_ultrafast.engagement.kpis import KPI_LIST
from jev_ultrafast.engagement.schemas import SNIPPET_KINDS, STAGES
from jev_ultrafast.engagement.scoring import load_anchors

RUNS = Path(__file__).parent / "fixtures" / "runs"
NFD = "No grazie, preferisco pagare di più"


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    path = tmp_path / "judgment-cache"
    monkeypatch.setenv("JEV_ENGAGEMENT_CACHE", str(path))
    return path


def page(page_id, stage, snippets, profile="mobile"):
    return {
        "page_id": page_id,
        "stage": stage,
        "profile": profile,
        "url": f"https://shop.test/{stage}",
        "classification": {"type": stage},
        "audit": {"snippets": [{"snippet_id": f"s{i + 1}", "kind": k, "text": t} for i, (k, t) in enumerate(snippets)]},
    }


def mini_run():
    pdp = [("modal_decline", NFD), ("returns_policy", "Reso   gratuito entro 30 giorni"), ("cta", "Aggiungi")]
    return {
        "run_id": "run-1",
        "settings": {"locale": "it"},
        "pages": [
            page("mobile-pdp-1", "pdp", pdp),
            page("desktop-pdp-1", "pdp", [s for s in pdp if s[0] != "modal_decline"], profile="desktop"),
            page("mobile-cart-1", "cart", [("checkbox_label", "Non desidero non ricevere offerte")]),
        ],
    }


def verdict(task_id, label, quotes=(), confidence=0.8):
    return {"task_id": task_id, "label": label, "confidence": confidence,
            "evidence": [{"snippet_id": s, "quote": q} for s, q in quotes], "rationale": "test"}


def judged(run, task_id, labels, *, quote=None):
    optional = judgments.load_rubrics()[task_id.split(":")[0]]["no_quote_labels"]
    for i, label in enumerate(labels):
        quotes = [quote] if quote and label not in optional else []
        result = judgments.submit(run, f"j{i + 1}", "model-a", [verdict(task_id, label, quotes)])
        assert result["accepted"] == 1, result


# ---------------------------------------------------------------- rubrics and tasks


def test_rubrics_cover_every_judged_kpi():
    rubrics = judgments.load_rubrics()
    anchors = load_anchors()
    assert len(rubrics) == 7
    assert {r["kpi_id"] for r in rubrics.values()} == {k.id for k in KPI_LIST if k.judged}
    for rubric in rubrics.values():
        assert rubric["version"] == judgments.RUBRICS_VERSION
        assert set(rubric["snippet_kinds"]) <= set(SNIPPET_KINDS)
        assert rubric["stages"] is None or set(rubric["stages"]) <= set(STAGES)
        assert rubric["grouping"] == {"per": "page", "max_snippets": 8}
        assert set(rubric["labels"]) == set(rubric["values"])
        assert set(rubric["no_quote_labels"]) <= set(rubric["labels"])
        assert "carattere per carattere" in rubric["question"]
        assert rubric.get("absent_when_no_snippets", False) == (rubric["id"] in {"returns_clarity", "authority",
                                                                                  "social_proof"})
        if rubric["kpi_id"].startswith("DPR."):
            assert all(v is None or 0 <= v <= 1 for v in rubric["values"].values())
            assert rubric["values"]["unclear"] is None and rubric["values"]["absent"] == 0
        else:
            assert set(rubric["values"].values()) == set(anchors["kpis"][rubric["kpi_id"]]["map"])
    # three ordered labels: the middle one is the complement of the top one, so every mention fits exactly one label
    assert "non soddisfa 'clear'" in rubrics["returns_clarity"]["labels"]["vague"]
    assert "non soddisfa 'rich'" in rubrics["social_proof"]["labels"]["basic"]
    assert "recesso" in rubrics["returns_clarity"]["question"] and "recesso" in rubrics["returns_clarity"]["labels"][
        "absent"]
    # labels turn on text a judge can see, never on position or prominence it cannot see
    assert "'Reso gratuito entro 30 giorni' vale 'clear'" in rubrics["returns_clarity"]["labels"]["clear"]
    assert "Gestisci preferenze" in rubrics["confirmshaming"]["labels"]["unclear"]
    hidden = rubrics["hidden_subscription"]
    assert "posizione ed evidenza grafica non sono note" in hidden["question"]
    assert "prova gratuita 0,00 €" in hidden["labels"]["present"]
    assert "importo e frequenza" in hidden["labels"]["absent"]
    assert not any(word in text for text in hidden["labels"].values() for word in ("secondario", "vicino", "evidente"))
    # routing (rubrics.v2): Jev answers the operational rubrics, Claude the perception ones and Jev's escalations
    assert {r["id"]: r["judge"] for r in rubrics.values()} == {
        "returns_clarity": "jev", "hidden_subscription": "jev", "authority": "jev", "social_proof": "jev",
        "trick_questions": "jev", "confirmshaming": "claude", "value_prop": "claude"}
    for rubric in rubrics.values():
        evidence_heads = [label for label in rubric["labels"] if label not in rubric["no_quote_labels"]]
        assert rubric["judge"] in judgments.JUDGES and (rubric["judge"] == "claude" or 1 <= len(evidence_heads) <= 2)


def test_make_tasks_is_deterministic_trims_dedupes_and_skips():
    run = mini_run()
    run["pages"][2]["audit"]["snippets"] += [
        {"snippet_id": f"x{i}", "kind": "checkbox_label", "text": f"Opzione {i} " + "lungo " * (200 if i == 0 else 1)}
        for i in range(10)
    ]
    tasks = judgments.make_tasks(run)
    assert tasks == judgments.make_tasks(copy.deepcopy(run))
    ids = [t["task_id"] for t in tasks]
    assert ids == sorted(ids, key=lambda i: i.split(":")[0]) and len(ids) == len(set(ids))
    by_id = {t["task_id"]: t for t in tasks}
    returns = by_id["returns_clarity:mobile-pdp-1"]
    assert [p["page_id"] for p in returns["context"]["pages"]] == ["mobile-pdp-1", "desktop-pdp-1"]
    assert "returns_clarity:desktop-pdp-1" not in by_id
    assert returns["snippets"][0]["text"] == "Reso gratuito entro 30 giorni"  # whitespace collapsed
    assert by_id["confirmshaming:mobile-pdp-1"]["snippets"][0]["text"] == "No grazie, preferisco pagare di più"
    trick = by_id["trick_questions:mobile-cart-1"]["snippets"]
    assert len(trick) == 8 and all(len(s["text"]) <= 600 for s in trick) and max(len(s["text"]) for s in trick) == 600
    assert not any(t["rubric_id"] == "value_prop" and t["page_id"] == "mobile-cart-1" for t in tasks)  # stage filter
    for task in tasks:
        assert task["samples_required"] == 3 and task["run_id"] == "run-1"
        assert set(task["labels"]) == set(judgments.load_rubrics()[task["rubric_id"]]["labels"])
        assert task["no_quote_labels"] == judgments.load_rubrics()[task["rubric_id"]]["no_quote_labels"]
    assert "shipping_policy" not in judgments.load_rubrics()["returns_clarity"]["snippet_kinds"]
    skipped = {s["rubric_id"]: s for s in judgments.skipped_rubrics(run, tasks)}
    assert set(skipped) == {"authority", "social_proof"}
    assert skipped["authority"]["reason"] == "no_snippets" and skipped["authority"]["kpi_id"] == "MPI.AUTHORITY"


def test_snippet_limit_keeps_the_decisive_kinds():
    crowd = [("cta", f"Pulsante {i}") for i in range(9)] + [("subscription", "Rinnovo automatico ogni mese a 9,99 EUR"),
                                                            ("headline", "Runner X2"), ("fee_line", "Servizio 2 EUR")]
    tasks = {t["rubric_id"]: t for t in judgments.make_tasks({"run_id": "r", "pages": [page("mobile-pdp-1", "pdp",
                                                                                              crowd)]})}
    hidden = [s["kind"] for s in tasks["hidden_subscription"]["snippets"]]
    assert len(hidden) == 8 and hidden.count("cta") == 6 and hidden[-2:] == ["subscription", "fee_line"]  # page order
    value = tasks["value_prop"]["snippets"]
    assert len(value) == 8 and value[-1]["text"] == "Runner X2" and value[0]["text"] == "Pulsante 0"


def test_ensure_tasks_stores_once():
    run = mini_run()
    tasks = judgments.ensure_tasks(run, samples_required=2)
    state = run["judgments"]
    assert state["rubrics_version"] == "rubrics.v2" and state["verdicts"] == [] and state["final"] == []
    assert {s["rubric_id"] for s in state["skipped"]} == {"authority", "social_proof"}
    run["pages"].append(page("mobile-home-1", "home", [("authority", "Premio Netcomm 2024")]))
    assert judgments.ensure_tasks(run) is tasks and tasks[0]["samples_required"] == 2
    empty = {"run_id": "run-2", "pages": [page("mobile-plp-1", "plp", [])]}
    assert judgments.ensure_tasks(empty) == [] and not judgments.prepared({"judgments": {}})
    assert judgments.prepared(empty) and len(empty["judgments"]["skipped"]) == 7
    empty["pages"].append(page("mobile-home-1", "home", [("authority", "Premio Netcomm 2024")]))
    assert judgments.ensure_tasks(empty) == []  # stored once, even when empty
    seeded = {**mini_run(), "judgments": {"tasks": [], "verdicts": [], "final": []}}  # RunStore.new_run's seed
    assert not judgments.prepared(seeded)
    assert judgments.ensure_tasks(seeded) and judgments.prepared(seeded)  # the seed is no request: tasks are made


# ---------------------------------------------------------------- verdict validation


def test_submit_accepts_verbatim_quotes_after_normalisation():
    run = mini_run()
    judgments.ensure_tasks(run)
    quotes = ["preferisco pagare di più", "NO GRAZIE,   preferisco\npagare di PIÙ", "pagare di più"]
    for i, quote in enumerate(quotes):
        result = judgments.submit(run, f"j{i}", "model-a", [verdict("confirmshaming:mobile-pdp-1", "present",
                                                                    [("s1", quote)])])
        assert result["accepted"] == 1 and not result["rejected"]
    stored = run["judgments"]["verdicts"]
    assert [v["judge_id"] for v in stored] == ["j0", "j1", "j2"]
    assert stored[1]["evidence"][0]["quote"] == "No grazie, preferisco pagare di più"  # page spelling, not the judge's
    assert result["samples_complete"] == 1 and result["tasks_total"] == len(run["judgments"]["tasks"])


@pytest.mark.parametrize(
    "item, reason",
    [
        (verdict("confirmshaming:mobile-pdp-1", "present", [("s1", "preferisco spendere di più")]),
         "quote_not_verbatim"),
        (verdict("confirmshaming:mobile-pdp-1", "present", [("s1", "preferisco pagare di più...")]),
         "quote_not_verbatim"),
        (verdict("confirmshaming:mobile-pdp-1", "maybe", [("s1", "No grazie")]), "unknown_label"),
        (verdict("confirmshaming:mobile-pdp-1", "present", [("s9", "No grazie")]), "unknown_snippet"),
        (verdict("confirmshaming:mobile-pdp-1", "present", [("s1", "   ")]), "empty_quote"),
        (verdict("confirmshaming:mobile-pdp-1", "present"), "missing_evidence"),
        (verdict("value_prop:mobile-pdp-1", "unclear"), "missing_evidence"),
        (verdict("confirmshaming:mobile-pdp-1", "absent", confidence=1.2), "invalid_confidence"),
        (verdict("confirmshaming:mobile-pdp-1", "absent", confidence=-0.1), "invalid_confidence"),
        (verdict("confirmshaming:mobile-pdp-1", "absent", confidence=True), "invalid_confidence"),
        (verdict("confirmshaming:mobile-pdp-1", "absent", confidence="0.5"), "invalid_confidence"),
        (verdict("confirmshaming:mobile-pdp-1", "absent", confidence=float("nan")), "invalid_confidence"),
        ({**verdict("confirmshaming:mobile-pdp-1", "absent"), "evidence": "No grazie"}, "invalid_evidence"),
        (verdict("authority:mobile-pdp-1", "absent"), "unknown_task"),
        ("present", "invalid_verdict"),
        ({**verdict("confirmshaming:mobile-pdp-1", "absent"), "task_id": ["x"]}, "unknown_task"),
        ({**verdict("confirmshaming:mobile-pdp-1", "absent"), "task_id": {"a": 1}}, "unknown_task"),
        ({**verdict("confirmshaming:mobile-pdp-1", "absent"),
          "evidence": [{"snippet_id": ["s1"], "quote": "No grazie"}]}, "unknown_snippet"),
        ({**verdict("confirmshaming:mobile-pdp-1", "absent"), "evidence": [["s1", "No grazie"]]}, "unknown_snippet"),
        (verdict("confirmshaming:mobile-pdp-1", "present", [("s1", "e")]), "quote_too_short"),
        (verdict("confirmshaming:mobile-pdp-1", "present", [("s1", "pagare")]), "quote_too_short"),
        (verdict("confirmshaming:mobile-pdp-1", "present", [("s1", "ｐａｇａｒｅ di più")]), "quote_not_verbatim"),
        (verdict("confirmshaming:mobile-pdp-1", "present", [("s1", "pa\u200bgare di più")]), "quote_not_verbatim"),
        (verdict("confirmshaming:mobile-pdp-1", "present", [("s1", "di più Reso")]), "quote_not_verbatim"),
        (verdict("confirmshaming:mobile-pdp-1", "present", [("s1", "No grazie"), ("s1", "invented text")]),
         "quote_not_verbatim"),
        (verdict("confirmshaming:mobile-pdp-1", "present", [("s1", "No grazie")] * 8 + [("s1", "e resta uno sfigato")]),
         "quote_not_verbatim"),  # every quote is checked, not only the stored ones
        (verdict("confirmshaming:mobile-pdp-1", "present", [("s1", "No grazie")] * 8 + [("zzz", "No grazie")]),
         "unknown_snippet"),
    ],
)
def test_submit_rejects_invalid_verdicts(item, reason):
    run = mini_run()
    judgments.ensure_tasks(run)
    result = judgments.submit(run, "j1", "model-a", [item])
    assert result["accepted"] == 0 and result["rejected"][0]["reason"] == reason
    assert run["judgments"]["verdicts"] == []


def test_short_snippets_can_be_quoted_whole():
    run = {"run_id": "r", "pages": [page("mobile-pdp-1", "pdp", [("modal_decline", "Chiudi"), ("cta", "Compra")])]}
    judgments.ensure_tasks(run)
    accepted = judgments.submit(run, "j1", "m", [verdict("confirmshaming:mobile-pdp-1", "present", [("s1", "chiudi")])])
    assert accepted["accepted"] == 1 and run["judgments"]["verdicts"][0]["evidence"][0]["quote"] == "Chiudi"
    partial = judgments.submit(run, "j2", "m", [verdict("confirmshaming:mobile-pdp-1", "present", [("s1", "Chiud")])])
    assert partial["rejected"][0]["reason"] == "quote_too_short"
    odd = {**verdict("confirmshaming:mobile-pdp-1", "absent"), "rationale": {"x": 1}}
    weird = judgments.submit(run, "j3", "m", [odd])
    assert weird["accepted"] == 1 and run["judgments"]["verdicts"][-1]["rationale"] == ""


def test_only_the_first_quotes_are_stored():
    run = mini_run()
    judgments.ensure_tasks(run)
    quotes = [("s1", "No grazie"), ("s1", "preferisco pagare")] * 5
    assert judgments.submit(run, "j1", "m", [verdict("confirmshaming:mobile-pdp-1", "present", quotes)])["accepted"]
    assert len(run["judgments"]["verdicts"][0]["evidence"]) == judgments.MAX_QUOTES


def test_samples_are_capped_and_finalize_only_lowers_the_requirement():
    run = mini_run()
    judgments.ensure_tasks(run)
    task_id = "confirmshaming:mobile-pdp-1"
    judged(run, task_id, ["absent"] * 3)
    late = judgments.submit(run, "j4", "m", [verdict(task_id, "present", [("s1", "No grazie")])])
    assert late["accepted"] == 0 and late["rejected"][0]["reason"] == "samples_complete"
    assert judgments.submit(run, "j1", "m", [verdict(task_id, "absent")])["rejected"][0]["reason"] == "samples_complete"
    assert judgments.submit(run, "j4", "m", [verdict("nope", "absent")])["rejected"][0]["reason"] == "unknown_task"
    assert [v["judge_id"] for v in run["judgments"]["verdicts"]] == ["j1", "j2", "j3"]
    for bad in (4, 0, True, 2.5):
        with pytest.raises(ValueError):
            judgments.finalize(run, samples_required=bad)
    assert run["judgments"]["final"] == []  # a refused override changes nothing
    assert judgments.finalize(run)["decided"] == 1
    assert judgments.submit(run, "j9", "m", [verdict(task_id, "absent")])["rejected"][0]["reason"] == "samples_complete"
    other = "returns_clarity:mobile-pdp-1"
    judged(run, other, ["clear"] * 2, quote=("s2", "Reso gratuito entro 30 giorni"))
    assert judgments.finalize(run, samples_required=2)["decided"] == 2  # early finalisation of a short run
    assert judgments.submit(run, "j3", "m", [verdict(other, "absent")])["rejected"][0]["reason"] == "task_already_final"


def test_finality_is_sticky_across_finalize_calls():
    run = mini_run()
    judgments.ensure_tasks(run)
    shaming, returns = "confirmshaming:mobile-pdp-1", "returns_clarity:mobile-pdp-1"
    trick = "trick_questions:mobile-cart-1"
    judged(run, shaming, ["present", "present"], quote=("s1", "pagare di più"))
    judged(run, returns, ["clear", "vague"], quote=("s2", "Reso gratuito entro 30 giorni"))  # an early tie
    early = judgments.finalize(run, samples_required=2)
    assert early["decided"] == 1 and early["uncertain"] == [returns]
    before = copy.deepcopy(run["judgments"]["final"])
    again = judgments.finalize(run)  # the default requirement (3) must not reopen early finals
    assert run["judgments"]["final"] == before
    assert shaming not in again["pending"] and returns not in again["pending"] and trick in again["pending"]
    assert again["decided"] == 1 and again["uncertain"] == [returns] and again["final"] == 2
    late = judgments.submit(run, "j3", "m", [verdict(shaming, "absent"), verdict(returns, "absent")])
    assert [r["reason"] for r in late["rejected"]] == ["task_already_final"] * 2 and late["accepted"] == 0
    judged(run, trick, ["present"] * 3, quote=("s1", "non ricevere"))
    third = judgments.finalize(run)
    assert third["final"] == 3 and run["judgments"]["final"][:2] == before
    labels = {f["task_id"]: f["label"] for f in run["judgments"]["final"]}
    assert labels == {shaming: "present", returns: None, trick: "present"}


def test_absent_labels_need_no_quote_and_rationale_is_trimmed():
    run = mini_run()
    judgments.ensure_tasks(run)
    item = {**verdict("confirmshaming:mobile-pdp-1", "absent"), "rationale": "x" * 500}
    assert judgments.submit(run, "j1", "model-a", [item])["accepted"] == 1
    assert len(run["judgments"]["verdicts"][0]["rationale"]) == 280
    with pytest.raises(ValueError):
        judgments.submit(run, "", "model-a", [item])


def test_duplicate_judge_is_rejected_within_and_across_batches():
    run = mini_run()
    judgments.ensure_tasks(run)
    item = verdict("confirmshaming:mobile-pdp-1", "absent")
    first = judgments.submit(run, "j1", "model-a", [item, item])
    assert first["accepted"] == 1 and first["rejected"] == [
        {"index": 1, "task_id": "confirmshaming:mobile-pdp-1", "reason": "duplicate_judge"}]
    assert judgments.submit(run, "j1", "model-b", [item])["rejected"][0]["reason"] == "duplicate_judge"
    assert judgments.submit(run, "j2", "model-a", [item])["accepted"] == 1


# ---------------------------------------------------------------- finalisation


def test_finalize_majority_ties_and_low_agreement():
    run = mini_run()
    tasks = {t["task_id"]: t for t in judgments.ensure_tasks(run)}
    tasks["trick_questions:mobile-cart-1"]["samples_required"] = 4
    tasks["hidden_subscription:mobile-pdp-1"]["samples_required"] = 5
    quote = ("s1", "No grazie")
    judged(run, "confirmshaming:mobile-pdp-1", ["present", "present", "absent"], quote=quote)
    judged(run, "returns_clarity:mobile-pdp-1", ["clear", "vague", "absent"], quote=("s2", "entro 30 giorni"))
    judged(run, "trick_questions:mobile-cart-1", ["present", "present", "absent", "absent"],
           quote=("s1", "non ricevere"))
    judged(run, "hidden_subscription:mobile-pdp-1", ["present", "present", "present", "absent", "absent"],
           quote=("s3", "Aggiungi"))
    judged(run, "value_prop:mobile-pdp-1", ["clear", "clear"], quote=("s3", "Aggiungi"))
    summary = judgments.finalize(run)
    finals = {f["task_id"]: f for f in run["judgments"]["final"]}
    shaming = finals["confirmshaming:mobile-pdp-1"]
    assert (shaming["label"], shaming["agreement"], shaming["samples"]) == ("present", 0.6667, 3)
    assert shaming["confidence"] == 0.8 and shaming["models"] == ["model-a"]
    assert shaming["evidence"] == [{"snippet_id": "s1", "quote": "No grazie"}]
    assert finals["returns_clarity:mobile-pdp-1"]["label"] is None  # three-way split
    assert finals["trick_questions:mobile-cart-1"]["label"] is None  # 2-2 tie
    assert finals["hidden_subscription:mobile-pdp-1"]["label"] is None  # 3/5 < 2/3
    assert "value_prop:mobile-pdp-1" not in finals  # 2 samples < 3 required
    assert summary["pending"] == ["value_prop:mobile-pdp-1"]
    assert set(summary["uncertain"]) == {"returns_clarity:mobile-pdp-1", "trick_questions:mobile-cart-1",
                                         "hidden_subscription:mobile-pdp-1"}
    assert summary["decided"] == 1 and "confirmshaming:mobile-pdp-1" in summary["disagreements"]
    late = judgments.submit(run, "j9", "model-a", [verdict("confirmshaming:mobile-pdp-1", "absent")])
    assert late["rejected"][0]["reason"] == "samples_complete"  # a full budget is reported before finality
    assert judgments.finalize(run, samples_required=2)["decided"] == 2  # value_prop now decided
    assert judgments.finalize(run, samples_required=2)["final"] == len(run["judgments"]["final"])


def test_finalize_follows_each_task_samples_required():
    run = mini_run()
    tasks = judgments.ensure_tasks(run, samples_required=1)
    judgments.submit(run, "j1", "m", [verdict("confirmshaming:mobile-pdp-1", "absent")])
    assert judgments.progress(run)["samples_complete"] == 1
    summary = judgments.finalize(run)
    assert summary["final"] == 1 and "confirmshaming:mobile-pdp-1" not in summary["pending"]
    assert len(summary["pending"]) == len(tasks) - 1


def test_observations_from_final_map_labels_through_rubrics():
    run = mini_run()
    judgments.ensure_tasks(run)
    judged(run, "confirmshaming:mobile-pdp-1", ["present", "present", "absent"], quote=("s1", "pagare di più"))
    judged(run, "returns_clarity:mobile-pdp-1", ["clear"] * 3, quote=("s2", "Reso gratuito"))
    judged(run, "trick_questions:mobile-cart-1", ["unclear"] * 3)
    judged(run, "value_prop:mobile-pdp-1", ["clear", "partial", "unclear"], quote=("s3", "Aggiungi"))
    judgments.finalize(run)
    rows = judgments.observations_from_final(run)
    by = {}
    for row in rows:
        by.setdefault(row["kpi_id"], []).append(row)
    shaming = by["DPR.CONFIRMSHAMING"][0]
    assert shaming["value"] == pytest.approx(0.8 * 0.6667, abs=1e-3) and shaming["source"] == "judged"
    assert shaming["evidence"]["quotes"] == ["pagare di più"] and shaming["unit"] == "confidence"
    returns = [r for r in by["TRI.RETURNS_CLARITY"] if r["source"] == "judged"]
    assert [(r["page_id"], r["profile"], r["value"]) for r in returns] == [
        ("mobile-pdp-1", "mobile", "clear"), ("desktop-pdp-1", "desktop", "clear")]
    assert by["DPR.TRICK_QUESTIONS"][0]["reason"] == "judgment_unclear" and not by["DPR.TRICK_QUESTIONS"][0]["assessed"]
    assert by["CCL.VALUE_PROP_CLARITY"][0]["reason"] == "judgment_uncertain"
    assert by["DPR.HIDDEN_SUBSCRIPTION"][0]["reason"] == "judgment_pending"
    # reached pages without relevant text: deterministic evidence of absence (no LLM), one row per page
    authority = by["MPI.AUTHORITY"]
    assert [(r["page_id"], r["value"], r["source"], r["assessed"], r["reason"]) for r in authority] == [
        (p, "absent", "deterministic", True, "no_snippets") for p in ("mobile-pdp-1", "desktop-pdp-1", "mobile-cart-1")]
    assert authority[0]["evidence"] == {"rubric_id": "authority", "reason": "no_snippets", "label": "absent",
                                        "basis": None, "pages_checked": 3, "url": "https://shop.test/pdp",
                                        "iframes": 0, "shadow_roots_closed": 0}
    assert [r["page_id"] for r in by["TRI.RETURNS_CLARITY"]][2:] == ["mobile-cart-1"]
    assert by["TRI.RETURNS_CLARITY"][2]["value"] == "absent" and by["TRI.RETURNS_CLARITY"][2]["assessed"]
    assert {r["value"] for r in by["MPI.SOCIAL_PROOF_RICH"]} == {"absent"}
    untouched = judgments.observations_from_final(mini_run())
    assert {r["reason"] for r in untouched} == {"not_judged"} and len(untouched) == 7


def test_fixture_judgments_are_reproducible():
    run = json.loads((RUNS / "audit_complete.json").read_text(encoding="utf-8"))
    state = run["judgments"]
    assert judgments.make_tasks(run) == state["tasks"]
    assert state["rubrics_version"] == judgments.RUBRICS_VERSION and state["skipped"] == []
    replay = copy.deepcopy(run)
    replay["judgments"] = {"tasks": state["tasks"], "verdicts": [], "final": []}
    for judge_id in ("j1", "j2", "j3"):
        mine = [v for v in state["verdicts"] if v["judge_id"] == judge_id]
        assert judgments.submit(replay, judge_id, "claude-sonnet-5-5", mine, cache=False)["rejected"] == []
    judgments.finalize(replay)
    assert replay["judgments"]["final"] == state["final"]


@pytest.mark.parametrize(
    "rubric_id, audit, label",
    [
        ("social_proof", {}, "absent"),
        ("social_proof", {"pdp": {"reviews": {"count_text": "37 recensioni"}}}, "basic"),
        ("social_proof", {"jsonld": [{"@type": "Product", "aggregateRating": {"ratingValue": 4.5}}]}, "basic"),
        ("social_proof", {"jsonld": [{"@type": ["AggregateRating"]}]}, "basic"),
        ("returns_clarity", {"trust": {"policy_links": {"returns": False}}}, "absent"),
        ("returns_clarity", {"trust": {"policy_links": {"returns": "/resi"}}}, "vague"),
        ("authority", {"trust": {"badges": []}}, "absent"),
        ("authority", {"trust": {"badges": ["Trusted Shops"]}}, "weak"),
    ],
)
def test_absence_labels_are_code_owned(rubric_id, audit, label):
    found, _ = judgments.absence_label(rubric_id, audit)
    assert found == label and found in judgments.load_rubrics()[rubric_id]["values"]


# ---------------------------------------------------------------- cache


def test_cache_key_covers_the_whole_judge_input(cache, monkeypatch):
    task = judgments.ensure_tasks(mini_run())[1]
    key = judgments.task_cache_key(task, "model-a")
    assert len(key) == 64 and key == judgments.task_cache_key(copy.deepcopy(task), "model-a")
    assert key == judgments.task_cache_key(task, "model-a", judgments.HOST_PROMPT)
    cli, api = (judges.prompt_id(judges.ClaudeCliJudge()), judges.prompt_id(judges.AnthropicApiJudge(api_key="k")))
    assert len({key, *(judgments.task_cache_key(task, "model-a", p) for p in (cli, api))}) == 3
    assert cli.endswith(judges.PROMPT_VERSION)  # instructions are part of the namespace
    assert (cli, api) == (f"cli:{judges.PROMPT_VERSION}", f"api:{judges.PROMPT_VERSION}")  # stable backend ids

    class RenamedCliJudge(judges.ClaudeCliJudge):
        pass

    assert judges.prompt_id(RenamedCliJudge()) == cli  # a class rename keeps the cache
    assert judges.prompt_id(judges.OpenAICompatibleJudge(api_key="k")) == f"openai:{judges.PROMPT_VERSION}"
    renamed = copy.deepcopy(task)
    renamed.update(task_id="other", run_id="run-9", page_id="desktop-x", profile="desktop")
    renamed["snippets"][0]["snippet_id"] = "s99"
    renamed["context"]["url"] = "https://elsewhere.test/"
    assert judgments.task_cache_key(renamed, "model-a") == key  # ids and URLs are not judge input
    edits = [
        lambda t: t.update(question=t["question"] + " "),
        lambda t: t["labels"].update(absent=t["labels"]["absent"] + "."),
        lambda t: t.update(no_quote_labels=[]),
        lambda t: t["snippets"][0].update(text=t["snippets"][0]["text"] + "!"),
        lambda t: t["snippets"].reverse(),
        lambda t: t["snippets"][0].update(kind="fee_line"),
        lambda t: t["context"].update(stage="cart"),
        lambda t: t["context"].update(locale="en"),
        lambda t: t["context"].update(page_type="other"),
        lambda t: t.update(rubric_version="rubrics.v2"),
    ]
    keys = {key, judgments.task_cache_key(task, "model-b")}
    for edit in edits:
        changed = copy.deepcopy(task)
        changed["snippets"].append({"snippet_id": "s9", "kind": "cta", "text": "Altro"})
        edit(changed)
        keys.add(judgments.task_cache_key(changed, "model-a"))
    assert len(keys) == len(edits) + 2
    assert judgments.cache_dir() == cache
    monkeypatch.delenv("JEV_ENGAGEMENT_CACHE")
    assert judgments.cache_dir() == Path.home() / ".cache" / "jev-engagement" / "judgments"


def test_cache_replays_identical_verdicts(cache):
    run = mini_run()
    judgments.ensure_tasks(run)
    judged(run, "confirmshaming:mobile-pdp-1", ["present", "present", "absent"], quote=("s1", "pagare di più"))
    judgments.finalize(run)
    assert len(list(cache.glob("*.json"))) == 1
    fresh = mini_run()
    fresh["pages"][0]["audit"]["snippets"][0]["snippet_id"] = "s77"  # same text, new id
    judgments.ensure_tasks(fresh)
    assert judgments.apply_cache(fresh, "model-a")["reused"] == 3
    assert judgments.apply_cache(fresh, "model-a")["reused"] == 0
    assert judgments.apply_cache(fresh, "model-b")["reused"] == 0
    assert fresh["judgments"]["verdicts"][0]["evidence"] == [{"snippet_id": "s77", "quote": "pagare di più"}]
    judgments.finalize(fresh)
    strip = [{k: v for k, v in f.items() if k != "evidence"} for f in fresh["judgments"]["final"]]
    assert strip == [{k: v for k, v in f.items() if k != "evidence"} for f in run["judgments"]["final"]]
    changed = mini_run()
    changed["pages"][0]["audit"]["snippets"][0]["text"] = "No grazie, preferisco pagare di più adesso"
    judgments.ensure_tasks(changed)
    assert judgments.apply_cache(changed, "model-a")["reused"] == 0


# ---------------------------------------------------------------- judges


def answer_for(tasks, label="absent"):
    return {"verdicts": [{"task_id": t["task_id"], "label": label, "confidence": 0.9, "evidence": [],
                          "rationale": "ok"} for t in tasks]}


FAKE_CLAUDE = r"""
import json, os, sys

args = sys.argv[1:]
prompt = sys.stdin.read()
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as log:
    api_env = [k for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if k in os.environ]
    log.write(json.dumps({"args": args, "prompt": prompt, "cwd": os.getcwd(), "files": os.listdir("."),
                          "api_env": api_env}) + "\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "structured")
if mode == "fail":
    sys.stderr.write("boom")
    sys.exit(2)
verdicts = []
for task in json.loads(prompt.split("TASK:\n", 1)[1])["tasks"]:
    first = task["snippets"][0]
    label = "absent" if "absent" in task["labels"] else sorted(task["labels"])[0]
    evidence = [{"snippet_id": first["snippet_id"], "quote": first["text"]}]
    verdicts.append({"task_id": task["task_id"], "label": label, "confidence": 0.9, "evidence": evidence,
                     "rationale": "ok"})
if mode == "error":
    result = {"type": "result", "is_error": True, "result": "not logged in"}
elif mode == "text":
    result = {"type": "result", "is_error": False, "result": "```json\n" + json.dumps({"verdicts": verdicts}) + "\n```"}
else:
    result = {"type": "result", "is_error": False, "result": "", "structured_output": {"verdicts": verdicts}}
print(json.dumps(result))
"""


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    script.write_text(f"#!{sys.executable}\n" + FAKE_CLAUDE, encoding="utf-8")
    script.chmod(0o755)
    log = tmp_path / "claude.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.delenv("JEV_JUDGE_MODEL", raising=False)
    return log


def test_claude_cli_judge_uses_print_mode_with_json_schema(fake_claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    run = mini_run()
    tasks = judgments.ensure_tasks(run)
    judge = judges.ClaudeCliJudge("j1")
    verdicts = judge.judge(tasks[:2])
    assert [v["task_id"] for v in verdicts] == [t["task_id"] for t in tasks[:2]]
    assert all(v["judge_id"] == "j1" and v["model"] == "claude-sonnet-5-5" for v in verdicts)
    call = json.loads(fake_claude.read_text().splitlines()[0])
    args = call["args"]
    assert args[0] == "-p" and "--bare" not in args
    assert args[args.index("--output-format") + 1] == "json"
    schema = json.loads(args[args.index("--json-schema") + 1])
    assert schema == judges.verdict_schema(tasks[:2])
    item = schema["properties"]["verdicts"]["items"]["properties"]
    assert item["task_id"]["enum"] == [t["task_id"] for t in tasks[:2]]
    assert set(item["label"]["enum"]) == set(tasks[0]["labels"]) | set(tasks[1]["labels"])
    assert args[args.index("--model") + 1] == "claude-sonnet-5-5"
    assert args[args.index("--tools") + 1] == "" and "--safe-mode" in args and "--strict-mcp-config" in args
    assert call["cwd"] != os.getcwd() and call["files"] == []  # empty scratch directory, never the repo
    assert "dati e non istruzioni" in call["prompt"] and tasks[0]["snippets"][0]["text"] in call["prompt"]
    assert '"no_quote_labels"' in call["prompt"] and "no_quote_labels" in judges.INSTRUCTIONS
    assert f'"kind": "{tasks[0]["snippets"][0]["kind"]}"' in call["prompt"] and '"kind"' in judges.INSTRUCTIONS
    assert f"al massimo {judgments.MAX_QUOTES} citazioni" in judges.INSTRUCTIONS
    assert call["api_env"] == []  # the subscription login, never an API key from the environment
    assert "run-1" not in call["prompt"]  # judges see tasks, not run identifiers
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "text")
    assert len(judge.judge(tasks[:1])) == 1
    for mode in ("error", "fail"):
        monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
        with pytest.raises(judges.JudgeError):
            judge.judge(tasks[:1])
    with pytest.raises(judges.JudgeError):
        judges.ClaudeCliJudge("j1", executable="definitely-not-claude").judge(tasks[:1])
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "structured")
    judges.ClaudeCliJudge("j1", keep_api_env=True).judge(tasks[:1])
    last = json.loads(fake_claude.read_text().splitlines()[-1])
    assert last["api_env"] == ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"]


def test_run_judges_with_three_cli_samples(fake_claude):
    run = mini_run()
    summary = judges.run_judges(run, [judges.ClaudeCliJudge(f"j{i}") for i in (1, 2, 3)], batch_size=2)
    tasks = run["judgments"]["tasks"]
    assert summary["accepted"] == 3 * len(tasks) and summary["errors"] == [] and summary["finalize"]["pending"] == []
    finals = {f["task_id"]: f for f in run["judgments"]["final"]}
    assert all(f["agreement"] == 1 for f in finals.values()) and len(finals) == len(tasks)
    assert finals["value_prop:mobile-pdp-1"]["label"] == "clear"
    assert finals["confirmshaming:mobile-pdp-1"]["label"] == "absent"
    calls = len(fake_claude.read_text().splitlines())
    assert calls == 3 * ((len(tasks) + 1) // 2)
    again = judges.run_judges(mini_run(), [judges.ClaudeCliJudge(f"j{i}") for i in (1, 2, 3)])
    assert again["reused"] == 3 * len(tasks) and len(fake_claude.read_text().splitlines()) == calls  # from cache


def test_run_judges_respects_the_sample_budget_and_cache_namespaces(fake_claude):
    run = mini_run()
    judges.run_judges(run, [judges.ClaudeCliJudge(f"j{i}") for i in (1, 2, 3, 4)], use_cache=False)
    tasks = run["judgments"]["tasks"]
    assert len(fake_claude.read_text().splitlines()) == 3  # the fourth judge has nothing left to judge
    assert len(run["judgments"]["verdicts"]) == 3 * len(tasks)
    still_open = mini_run()
    judgments.ensure_tasks(still_open)
    with pytest.raises(ValueError, match="larger samples_required"):
        judges.run_judges(still_open, [judges.ClaudeCliJudge("j5")], samples_required=5)
    assert len(fake_claude.read_text().splitlines()) == 3  # refused before any judge ran
    done = judges.run_judges(mini_run() | {"judgments": copy.deepcopy(run["judgments"])},
                             [judges.ClaudeCliJudge("j5")], samples_required=5)
    assert done["accepted"] == 0 and done["finalize"]["final"] == len(tasks)  # final tasks keep their result
    assert len(fake_claude.read_text().splitlines()) == 3
    host = mini_run()
    judgments.ensure_tasks(host)
    judged(host, "confirmshaming:mobile-pdp-1", ["absent"] * 3)  # host verdicts, cached under HOST_PROMPT
    fresh = mini_run()
    judgments.ensure_tasks(fresh)
    assert judgments.apply_cache(fresh, "model-a")["reused"] == 3
    cli = judges.run_judges(mini_run(), [judges.ClaudeCliJudge("j1", model="model-a")])
    assert cli["reused"] == 0  # a different judge prompt never reuses host samples


def test_run_judges_records_errors_and_propagates_not_supported(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail")
    run = mini_run()
    summary = judges.run_judges(run, [judges.ClaudeCliJudge("j1")])
    assert summary["errors"] and summary["accepted"] == 0 and summary["finalize"]["final"] == 0
    with pytest.raises(judges.NotSupported):
        judges.SamplingJudge().judge([])
    with pytest.raises(judges.NotSupported):
        judges.run_judges(mini_run(), [judges.SamplingJudge()])


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_anthropic_api_judge_request(monkeypatch):
    monkeypatch.delenv("JEV_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr(judges.time, "sleep", lambda s: None)
    run = mini_run()
    tasks = judgments.ensure_tasks(run)[:2]
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(529, json={"type": "error"})
        body = json.loads(request.content)
        return httpx.Response(200, json={"model": body["model"], "stop_reason": "end_turn",
                                         "content": [{"type": "text", "text": json.dumps(answer_for(tasks))}]})

    judge = judges.AnthropicApiJudge("j1", api_key="test-key", client=mock_client(handler))
    verdicts = judge.judge(tasks)
    assert [v["label"] for v in verdicts] == ["absent", "absent"] and verdicts[0]["model"] == "claude-sonnet-5-5"
    request = seen[-1]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.anthropic.com/v1/messages" and len(seen) == 2
    assert request.headers["x-api-key"] == "test-key" and request.headers["anthropic-version"] == "2023-06-01"
    assert body["model"] == "claude-sonnet-5-5"
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": judges.verdict_schema(tasks)}
    assert not {"temperature", "top_p", "top_k"} & set(body)
    assert body["fallbacks"] == "default" and "server-side-fallback" in request.headers["anthropic-beta"]
    monkeypatch.setenv("JEV_JUDGE_MODEL", "claude-opus-5-5")
    other = judges.AnthropicApiJudge("j2", api_key="k", base_url="http://proxy.test", client=mock_client(handler))
    assert other.model == "claude-opus-5-5"
    other.judge(tasks)
    assert "fallbacks" not in json.loads(seen[-1].content)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(judges.JudgeError):
        judges.AnthropicApiJudge("j3", client=mock_client(handler)).judge(tasks)
    refusal = mock_client(lambda r: httpx.Response(200, json={"stop_reason": "refusal", "content": []}))
    with pytest.raises(judges.JudgeError):
        judges.AnthropicApiJudge("j4", api_key="k", client=refusal).judge(tasks)
    broken = mock_client(lambda r: httpx.Response(400, json={"error": {}}))
    with pytest.raises(judges.JudgeError):
        judges.AnthropicApiJudge("j5", api_key="k", client=broken).judge(tasks)


def test_openai_compatible_judge(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "oa-key")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://llm.test/v1/")
    monkeypatch.setenv("TEXT_MODEL", "small-model")
    tasks = judgments.ensure_tasks(mini_run())[:1]
    seen = []

    def handler(request):
        seen.append(request)
        content = json.dumps(answer_for(tasks, "unclear"))
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    verdicts = judges.OpenAICompatibleJudge("j1", client=mock_client(handler)).judge(tasks)
    assert verdicts[0]["label"] == "unclear" and verdicts[0]["model"] == "small-model"
    assert str(seen[0].url) == "https://llm.test/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer oa-key"
    assert json.loads(seen[0].content)["response_format"] == {"type": "json_object"}
    gateway = mock_client(lambda r: httpx.Response(200, text="<html>gateway</html>"))
    with pytest.raises(judges.JudgeError, match="invalid JSON"):
        judges.OpenAICompatibleJudge("j1", client=gateway).judge(tasks)
    odd = mock_client(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": 42}}]}))
    with pytest.raises(judges.JudgeError):
        judges.OpenAICompatibleJudge("j1", client=odd).judge(tasks)
    listed = mock_client(lambda r: httpx.Response(200, json=[1, 2]))
    with pytest.raises(judges.JudgeError):
        judges.AnthropicApiJudge("j1", api_key="k", client=listed).judge(tasks)
    monkeypatch.delenv("TEXT_MODEL_API_KEY")
    with pytest.raises(judges.JudgeError):
        judges.OpenAICompatibleJudge("j1", client=mock_client(handler)).judge(tasks)


def test_parse_verdicts_ignores_unknown_tasks_and_rejects_bad_payloads():
    tasks = judgments.ensure_tasks(mini_run())[:1]
    payload = {"verdicts": [{"task_id": "other", "label": "absent"}, {"task_id": tasks[0]["task_id"], "label": "x"}]}
    assert [v["task_id"] for v in judges.parse_verdicts(payload, tasks, "j1", "m")] == [tasks[0]["task_id"]]
    for bad in (None, [], {"verdicts": "no"}):
        with pytest.raises(judges.JudgeError):
            judges.parse_verdicts(bad, tasks, "j1", "m")
    with pytest.raises(judges.JudgeError):
        judges.json_from_text("no json here")
    with pytest.raises(judges.JudgeError):
        judges.json_from_text(None)
    odd = {"verdicts": [{"task_id": ["x"]}, {"task_id": {"a": 1}}, {"task_id": 7}, "text"]}
    assert judges.parse_verdicts(odd, tasks, "j1", "m") == []


def test_run_judges_survives_malformed_judge_output():
    class Sloppy:
        judge_id, model = "j1", "m"

        def judge(self, chunk):
            first = answer_for(chunk[:1])["verdicts"][0]
            return [{"task_id": {"a": 1}}, "nonsense", {**first, "model": ["not", "a", "model"]},
                    {"task_id": next(t for t in chunk[1:] if "absent" in t["labels"])["task_id"], "label": "absent",
                     "confidence": 0.5, "evidence": [{"snippet_id": ["s1"], "quote": "x"}]}]

    run = mini_run()
    summary = judges.run_judges(run, [Sloppy()], use_cache=False, batch_size=100)
    assert summary["accepted"] == 1 and run["judgments"]["verdicts"][0]["model"] == "m"
    assert {r["reason"] for r in summary["rejected"]} == {"unknown_task", "unknown_snippet"}


# ---------------------------------------------------------------- Jev judge (TypeSafe stand-in, no network)

JEV_MODEL = "jev-1.13.0"
PDP_JEV = ("hidden_subscription:mobile-pdp-1", "returns_clarity:mobile-pdp-1")
CART_JEV = "trick_questions:mobile-cart-1"
CLAUDE_TASKS = {"confirmshaming:mobile-pdp-1", "value_prop:mobile-pdp-1"}


def choice(ids, selected, p=0.9):
    """A schema-valid systemone choice answer (model.validate_choice accepts it)."""
    ids = list(ids)
    rest = (1 - p) / (len(ids) - 1)
    return {"type": "choice", "choice": selected, "probabilities": {i: p if i == selected else rest for i in ids},
            "confidence": (p - 1 / len(ids)) / (1 - 1 / len(ids))}


class FakeTypeSafe:
    """Stand-in for jev_ultrafast.model.post_json: checks each request body, then answers like systemone.

    script: {task_id: {"label", "p", "rev" (the reversed head's label), "evidence" (snippet id or "none"),
    "invalid": True, "probabilities": any JSON value put in place of the label head's probabilities (null, a list)}};
    a task outside the script gets its last label (absent/unclear: no quote). Evidence heads of labels not chosen get
    an invalid answer, so reading one would fail. fail_pages: page ids whose request raises error (default:
    post_json's RuntimeError for an HTTP 529 after its retries).
    """

    def __init__(self, script=None, *, fail_pages=(), model=JEV_MODEL, error=None):
        self.script, self.fail_pages, self.model, self.bodies = script or {}, set(fail_pages), model, []
        self.error = error or RuntimeError("Model provider returned HTTP 529; no action executed.")

    def __call__(self, url, key, body):
        assert url == "https://api.typesafe.ai/v1/systemone" and key == "test-key"
        assert set(body) == {"model", "state", "questions"} and body["questions"] and isinstance(body["model"], str)
        text = json.dumps(body, ensure_ascii=False)
        assert "https://" not in text and "run-1" not in text  # no URLs, no run ids
        assert set(body["state"]) == {"page", "tasks"} and set(body["state"]["page"]) == {"page_type", "stage",
                                                                                           "locale"}
        for qid, question in body["questions"].items():
            assert set(question) == {"type", "criteria", "instructions"} and question["type"] == "choice"
            assert 2 <= len(question["criteria"]) <= 255 and isinstance(question["instructions"], dict)
            assert all(v is None or isinstance(v, str) for v in question["criteria"].values())
            assert question["instructions"]["rules"] in (judges.JEV_JUDGE_RULES, judges.JEV_EVIDENCE_RULES)
        self.bodies.append(copy.deepcopy(body))
        if {qid.split(":")[2] for qid in body["questions"]} & self.fail_pages:
            raise self.error
        answers = {}
        for qid, question in body["questions"].items():
            kind, rubric, page, *label = qid.split(":")
            labels = list(body["questions"][f"label:{rubric}:{page}"]["criteria"])
            plan = {"label": labels[-1], "p": 0.9, **self.script.get(f"{rubric}:{page}", {})}
            if kind == "label":
                answers[qid] = choice(labels, plan["label"], plan["p"])
                if plan.get("invalid"):
                    answers[qid]["probabilities"][plan["label"]] = 0.99  # no longer sums to 1
                if "probabilities" in plan:  # valid JSON of the wrong shape
                    answers[qid]["probabilities"] = copy.deepcopy(plan["probabilities"])
            elif kind == "label_rev":
                answers[qid] = choice(question["criteria"], plan.get("rev", plan["label"]), plan["p"])
            elif label == [plan["label"]]:
                answers[qid] = choice(question["criteria"], plan.get("evidence") or next(iter(question["criteria"])),
                                      0.8)
            else:
                answers[qid] = {"choice": "not-offered"}  # a head nobody may read
        return {"model": self.model, "answers": answers, "usage": {"input_tokens": len(text), "output_tokens": 0}}


@pytest.fixture
def jev(monkeypatch):
    """install(script, **kwargs) patches model.post_json with a FakeTypeSafe and returns it."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    for name in ("TYPESAFE_MODEL", "JEV_JUDGE_MIN_P"):
        monkeypatch.delenv(name, raising=False)

    def install(script=None, **kwargs):
        fake = FakeTypeSafe(script, **kwargs)
        monkeypatch.setattr(typesafe, "post_json", fake)
        return fake

    return install


ACCEPT = {
    "returns_clarity:mobile-pdp-1": {"label": "clear", "evidence": "s2"},
    "hidden_subscription:mobile-pdp-1": {"label": "absent", "p": 0.8},
    CART_JEV: {"label": "present", "p": 0.75, "evidence": "s1"},
}


def test_jev_request_is_one_systemone_body_per_page(jev):
    fake = jev(ACCEPT)
    run = mini_run()
    judges.judge_with_jev(run)
    assert len(fake.bodies) == 2  # two pages hold Jev tasks: one request each, never one per task
    pdp, cart = fake.bodies
    assert pdp["model"] == "jev-latest"
    assert pdp["state"] == {
        "page": {"page_type": "pdp", "stage": "pdp", "locale": "it"},
        "tasks": {"hidden_subscription": {"snippets": {"s3": {"kind": "cta", "text": "Aggiungi"}}},
                  "returns_clarity": {"snippets": {"s2": {"kind": "returns_policy",
                                                          "text": "Reso gratuito entro 30 giorni"}}}},
    }  # Claude's rubrics (confirmshaming, value_prop) are not in Jev's request
    returns, hidden = PDP_JEV[1], PDP_JEV[0]
    assert set(pdp["questions"]) == {f"label:{hidden}", f"label_rev:{hidden}", f"evidence:{hidden}:present",
                                     f"label:{returns}", f"label_rev:{returns}", f"evidence:{returns}:clear",
                                     f"evidence:{returns}:vague"}  # no evidence head for no-quote labels
    rubric = judgments.load_rubrics()["returns_clarity"]
    label, reverse = pdp["questions"][f"label:{returns}"], pdp["questions"][f"label_rev:{returns}"]
    assert label["criteria"] == rubric["labels"] and list(label["criteria"]) == ["clear", "vague", "absent"]
    assert list(reverse["criteria"]) == ["absent", "vague", "clear"] and reverse["criteria"] == rubric["labels"]
    assert label["instructions"] == reverse["instructions"] == {
        "question": rubric["question"], "snippets": "`tasks.returns_clarity.snippets`",
        "rules": judges.JEV_JUDGE_RULES}
    evidence = pdp["questions"][f"evidence:{returns}:vague"]
    assert evidence["criteria"] == {"s2": None, "none": judges.JEV_NONE}  # offered snippet ids only
    assert evidence["instructions"]["premise"] == f"Assume the answer is 'vague': {rubric['labels']['vague']}"
    assert "`tasks.returns_clarity.snippets`" in evidence["instructions"]["question"]
    assert set(cart["state"]["tasks"]) == {"trick_questions"} and cart["state"]["page"]["stage"] == "cart"
    task = {**judgments.ensure_tasks(mini_run())[0], "snippets": [{"snippet_id": "none", "kind": "cta",
                                                                    "text": "Nessuno"}]}
    odd = judges.JevJudge(api_key="k").request([task])["questions"]
    assert all("_none" in q["criteria"] and "none" in q["criteria"] for k, q in odd.items() if k.startswith("evid"))


def test_jev_accepts_settles_and_finalizes_with_one_sample(jev, monkeypatch):
    fake = jev(ACCEPT)
    run = mini_run()
    summary = judges.judge_with_jev(run)
    assert summary["available"] and summary["reason"] is None and summary["requests"] == 2
    assert summary["judged"] == 3 and summary["escalated"] == {} and summary["errors"] == []
    assert summary["model"] == JEV_MODEL and summary["input_tokens"] == sum(len(json.dumps(b, ensure_ascii=False))
                                                                             for b in fake.bodies)
    assert summary["open_tasks"] == 2 and set(summary["finalize"]["pending"]) == CLAUDE_TASKS
    state = run["judgments"]
    assert {v["task_id"] for v in state["verdicts"]} == {*PDP_JEV, CART_JEV}
    assert all(v["judge_id"] == "jev" and v["model"] == JEV_MODEL for v in state["verdicts"])
    tasks = {t["task_id"]: t for t in state["tasks"]}
    assert {t: tasks[t]["samples_required"] for t in tasks} == {**dict.fromkeys((*PDP_JEV, CART_JEV), 1),
                                                                 **dict.fromkeys(CLAUDE_TASKS, 3)}
    assert not any("escalation" in t or "judge" in t for t in tasks.values())  # routing is never copied in tasks
    finals = {f["task_id"]: f for f in state["final"]}
    assert finals["returns_clarity:mobile-pdp-1"] == {
        "task_id": "returns_clarity:mobile-pdp-1", "kpi_id": "TRI.RETURNS_CLARITY", "label": "clear",
        "agreement": 1.0, "samples": 1, "confidence": 0.9, "models": [JEV_MODEL],
        "evidence": [{"snippet_id": "s2", "quote": "Reso gratuito entro 30 giorni"}]}  # the whole offered snippet
    assert (finals[CART_JEV]["label"], finals[CART_JEV]["confidence"]) == ("present", 0.75)
    assert finals[CART_JEV]["evidence"][0]["quote"] == "Non desidero non ricevere offerte"
    assert finals["hidden_subscription:mobile-pdp-1"]["evidence"] == []
    cart = next(v for v in state["verdicts"] if v["task_id"] == CART_JEV)
    assert cart["rationale"] == "Jev: p=0.75, evidenza p=0.80, ordine inverso concorde"
    assert run["model_calls"] == {"judge_jev": {"requests": 2, "latency_ms": summary["latency_ms"],
                                                "input_tokens": summary["input_tokens"], "model": JEV_MODEL}}
    rows = {r["kpi_id"]: r for r in judgments.observations_from_final(run) if r["source"] == "judged"}
    assert rows["DPR.TRICK_QUESTIONS"]["value"] == 0.8 and rows["DPR.TRICK_QUESTIONS"]["evidence"]["samples"] == 1
    assert rows["TRI.RETURNS_CLARITY"]["value"] == "clear" and rows["TRI.RETURNS_CLARITY"]["evidence"]["models"] == [
        JEV_MODEL]
    late = judgments.submit(run, "j1", "model-a", [verdict(CART_JEV, "absent")])
    assert late["rejected"][0]["reason"] == "samples_complete"  # Claude cannot reopen a Jev final
    again = judges.judge_with_jev(run)  # nothing left for Jev: no request, the counts add up
    assert again["requests"] == 0 and len(fake.bodies) == 2 and run["model_calls"]["judge_jev"]["requests"] == 2
    monkeypatch.setenv("TYPESAFE_MODEL", "jev-preview")
    assert judges.JevJudge().model == "jev-preview" and judges.JevJudge(model="jev-1.13.0").model == "jev-1.13.0"


@pytest.mark.parametrize("plan, reason", [
    ({"label": "clear", "p": 0.5, "evidence": "s2"}, "low_probability"),
    ({"label": "clear", "rev": "vague", "evidence": "s2"}, "position_flip"),
    ({"label": "clear", "evidence": "none"}, "no_evidence"),
    ({"label": "clear", "evidence": "s2", "invalid": True}, "invalid_response"),
    ({"label": "clear", "rev": "not-offered", "evidence": "s2"}, "invalid_response"),  # a malformed reversed head
    ({"label": "clear", "p": 0.5, "rev": "not-offered", "evidence": "s2"}, "low_probability"),  # p is read first:
])  # a low p is Jev's reading (permanent) whatever the reversed head holds, never a retryable invalid answer
def test_jev_escalations_leave_the_task_to_claude(jev, plan, reason):
    task_id = "returns_clarity:mobile-pdp-1"
    jev({**ACCEPT, task_id: plan})
    run = mini_run()
    summary = judges.judge_with_jev(run)
    assert summary["escalated"] == {reason: 1} and summary["judged"] == 2
    assert bool(summary["errors"]) == (reason == "invalid_response")
    task = next(t for t in run["judgments"]["tasks"] if t["task_id"] == task_id)
    escalation = task["escalation"]
    assert task["samples_required"] == 3 and escalation["from"] == "jev" and escalation["reason"] == reason
    assert escalation["model"] == JEV_MODEL and escalation["at"].endswith("Z")
    assert (escalation["label"], escalation["probability"]) == ((None, None) if reason == "invalid_response" else (
        "clear", plan.get("p", 0.9)))
    assert task_id not in {v["task_id"] for v in run["judgments"]["verdicts"]}  # Jev's reading is no verdict
    assert task_id in summary["finalize"]["pending"] and summary["open_tasks"] == 3
    retried = [t["task_id"] for t in judges.jev_tasks(run)]  # Jev's reading is never asked again; an invalid
    assert retried == ([task_id] if reason == "invalid_response" else [])  # answer is, until a judge answers
    judged(run, task_id, ["vague"] * 3, quote=("s2", "entro 30 giorni"))  # then Claude's three samples decide it
    judgments.finalize(run)
    final = next(f for f in run["judgments"]["final"] if f["task_id"] == task_id)
    assert (final["label"], final["samples"], final["agreement"], final["models"]) == ("vague", 3, 1.0, ["model-a"])


def test_jev_asks_no_new_page_after_its_budget_and_the_next_call_goes_on(jev, monkeypatch):
    """budget_s bounds the run lock a service holds: no new page once it passed, the first page always; an unasked
    page is untouched (no verdict, no escalation, no call) and counted in pages_left; the next call asks it."""
    fake = jev(ACCEPT)
    run = mini_run()
    run["pages"].append(page("desktop-cart-1", "cart", [("checkbox_label", "Non voglio smettere di ricevere offerte")],
                             profile="desktop"))
    tasks = {t["task_id"]: t for t in judgments.ensure_tasks(run)}
    jev_pages = {t["page_id"] for t in tasks.values() if judgments.routing(t) == "jev"}
    assert len(jev_pages) == 3

    def slow(url, key, body):
        time.sleep(0.05)  # past the budget: no other page is started in this call
        return fake(url, key, body)
    monkeypatch.setattr(typesafe, "post_json", slow)
    first = judges.judge_with_jev(run, budget_s=0.01)
    assert first["requests"] == 1 and first["pages_left"] == len(jev_pages) - 1 == 2 and len(fake.bodies) == 1
    assert first["escalated"] == {} and first["errors"] == []
    asked = {":".join(qid.split(":")[1:3]) for qid in fake.bodies[0]["questions"]}  # its task ids
    state = run["judgments"]
    assert {v["task_id"] for v in state["verdicts"]} == {f["task_id"] for f in state["final"]} == asked
    assert not any("escalation" in t for t in state["tasks"])  # an unasked page is no failure
    assert all(t["samples_required"] == 3 for t in state["tasks"] if t["task_id"] not in asked)
    assert first["open_tasks"] == len(tasks) - len(asked)
    calls = [judges.judge_with_jev(run, budget_s=0.01)]
    while calls[-1]["pages_left"]:
        calls.append(judges.judge_with_jev(run, budget_s=0.01))
    assert [(c["requests"], c["pages_left"]) for c in calls] == [(1, 1), (1, 0)]
    pages = [qid.split(":")[2] for body in fake.bodies for qid in list(body["questions"])[:1]]
    assert sorted(pages) == sorted(jev_pages)  # every page asked exactly once
    assert run["model_calls"]["judge_jev"]["requests"] == len(jev_pages) == len(fake.bodies)
    jev_ids = {t for t in tasks if judgments.routing(tasks[t]) == "jev"}
    assert {f["task_id"] for f in run["judgments"]["final"]} == jev_ids and calls[-1]["open_tasks"] == len(CLAUDE_TASKS)
    assert judges.judge_with_jev(run, budget_s=0)["requests"] == 0  # nothing left to ask
    fresh = judges.judge_with_jev(mini_run(), budget_s=0)  # budget 0: the first page is still asked
    assert (fresh["requests"], fresh["pages_left"]) == (1, 1)
    unbounded = judges.judge_with_jev(mini_run())  # no budget: every page in one call
    assert (unbounded["requests"], unbounded["pages_left"]) == (2, 0)
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert judges.judge_with_jev(mini_run(), budget_s=1)["pages_left"] == 0  # no key: nothing asked, nothing left


def test_jev_threshold_comes_from_the_environment_and_is_clamped(jev, monkeypatch):
    for raw, expected in (("0.7", 0.7), ("2", 0.95), ("0.1", 0.5), ("soon", 0.6), ("nan", 0.6), ("", 0.6)):
        monkeypatch.setenv("JEV_JUDGE_MIN_P", raw)
        assert judges.JevJudge(api_key="k").min_probability == expected
    assert judges.JevJudge(api_key="k", min_probability=0.8).min_probability == 0.8
    monkeypatch.setenv("JEV_JUDGE_MIN_P", "0.85")
    jev(ACCEPT)
    summary = judges.judge_with_jev(mini_run())
    assert summary["escalated"] == {"low_probability": 2} and summary["judged"] == 1  # p 0.8 and 0.75 < 0.85


def test_a_failed_jev_request_escalates_its_page_and_never_blocks(jev):
    fake = jev(ACCEPT, fail_pages={"mobile-cart-1"})
    run = mini_run()
    summary = judges.judge_with_jev(run)
    assert summary["requests"] == 2 and len(fake.bodies) == 2 and summary["judged"] == 2
    assert summary["escalated"] == {"request_failed": 1}
    assert summary["errors"] == [{"page_id": "mobile-cart-1", "task_id": CART_JEV,
                                  "error": "Model provider returned HTTP 529; no action executed."}]
    task = next(t for t in run["judgments"]["tasks"] if t["task_id"] == CART_JEV)
    assert task["samples_required"] == 3 and task["escalation"]["reason"] == "request_failed"
    assert CART_JEV in summary["finalize"]["pending"]  # open for Claude
    assert {f["task_id"] for f in run["judgments"]["final"]} == set(PDP_JEV)


def test_a_body_that_is_not_json_escalates_its_page_and_keeps_the_rest(jev):
    # post_json returns response.json(): a 2xx page that is not JSON (a proxy's HTML) raises json.JSONDecodeError
    jev(ACCEPT, fail_pages={"mobile-cart-1"}, error=json.JSONDecodeError("Expecting value", "<html>", 0))
    run = mini_run()
    summary = judges.judge_with_jev(run)  # the second page fails: the first page's verdicts are still stored
    assert summary["judged"] == 2 and {v["task_id"] for v in run["judgments"]["verdicts"]} == set(PDP_JEV)
    assert summary["escalated"] == {"request_failed": 1} and summary["errors"][0]["task_id"] == CART_JEV
    assert summary["requests"] == run["model_calls"]["judge_jev"]["requests"] == 2  # both requests are counted
    task = next(t for t in run["judgments"]["tasks"] if t["task_id"] == CART_JEV)
    assert task["escalation"]["reason"] == "request_failed" and task["samples_required"] == 3
    assert {f["task_id"] for f in run["judgments"]["final"]} == set(PDP_JEV) and summary["open_tasks"] == 3


@pytest.mark.parametrize("probabilities", [None, [0.9, 0.05, 0.05], "0.9", 0.9])
def test_a_jev_answer_of_the_wrong_shape_escalates_only_its_task(jev, probabilities):
    # model.validate_choice catches KeyError, TypeError and ValueError: probabilities null or a list raises
    # AttributeError there. The bad head is on the first page: the cart page is still asked and everything is kept.
    task_id = "returns_clarity:mobile-pdp-1"
    fake = jev({**ACCEPT, task_id: {"label": "clear", "evidence": "s2", "probabilities": probabilities}})
    run = mini_run()
    summary = judges.judge_with_jev(run)
    assert summary["escalated"] == {"invalid_response": 1} and summary["judged"] == 2
    assert len(fake.bodies) == summary["requests"] == run["model_calls"]["judge_jev"]["requests"] == 2  # 2 pages
    assert summary["input_tokens"] == run["model_calls"]["judge_jev"]["input_tokens"] > 0
    assert {v["task_id"] for v in run["judgments"]["verdicts"]} == {PDP_JEV[0], CART_JEV}  # the rest is stored
    assert [(e["page_id"], e["task_id"]) for e in summary["errors"]] == [("mobile-pdp-1", task_id)]
    assert summary["errors"][0]["error"].startswith("Invalid TypeSafe response")
    task = next(t for t in run["judgments"]["tasks"] if t["task_id"] == task_id)
    assert task["samples_required"] == 3 and task["escalation"]["reason"] == "invalid_response"
    assert (task["escalation"]["label"], task["escalation"]["probability"]) == (None, None)
    assert {f["task_id"] for f in run["judgments"]["final"]} == {PDP_JEV[0], CART_JEV}
    assert summary["open_tasks"] == 3 and [t["task_id"] for t in judges.jev_tasks(run)] == [task_id]
    fake = jev(ACCEPT)  # retryable: a later call asks that task again and settles it
    fixed = judges.judge_with_jev(run)
    assert fixed["judged"] == 1 and fixed["escalated"] == {} and len(fake.bodies) == 1
    assert "escalation" not in task and task["samples_required"] == 1
    assert run["model_calls"]["judge_jev"]["requests"] == 3


def test_jev_accepts_a_confident_unclear_as_not_assessed_and_never_escalates_it(jev):
    # Ruling: unclear is the rubric's insufficient-evidence label ("non valutato"), not Jev's uncertainty: a label at
    # p >= the floor that the reversed head confirms is a verdict, and Claude does not re-judge it.
    hidden = PDP_JEV[0]
    fake = jev({**ACCEPT, hidden: {"label": "unclear", "p": 0.9}, CART_JEV: {"label": "unclear", "p": 0.9}})
    run = mini_run()
    summary = judges.judge_with_jev(run)
    assert summary["escalated"] == {} and summary["judged"] == 3 and summary["requests"] == 2
    assert set(summary["finalize"]["pending"]) == CLAUDE_TASKS  # only Claude's own rubrics stay open
    state = run["judgments"]
    tasks, finals = {t["task_id"]: t for t in state["tasks"]}, {f["task_id"]: f for f in state["final"]}
    for task_id in (hidden, CART_JEV):
        assert [v["label"] for v in state["verdicts"] if v["task_id"] == task_id] == ["unclear"]  # one verdict
        assert tasks[task_id]["samples_required"] == 1 and "escalation" not in tasks[task_id]
        final = finals[task_id]
        assert (final["label"], final["samples"], final["agreement"], final["confidence"]) == ("unclear", 1, 1.0, 0.9)
        assert final["evidence"] == [] and final["models"] == [JEV_MODEL]
    rows = [r for r in judgments.observations_from_final(run) if r["source"] == "judged"
            and r["kpi_id"] in ("DPR.HIDDEN_SUBSCRIPTION", "DPR.TRICK_QUESTIONS")]
    assert {r["kpi_id"] for r in rows} == {"DPR.HIDDEN_SUBSCRIPTION", "DPR.TRICK_QUESTIONS"}
    assert {(r["assessed"], r["reason"], r["value"]) for r in rows} == {(False, "judgment_unclear", None)}
    assert judges.jev_tasks(run) == [] and judges.judge_with_jev(run)["requests"] == 0 and len(fake.bodies) == 2


def test_jev_retries_a_failed_request_until_a_judge_answers(jev):
    jev(ACCEPT, fail_pages={"mobile-cart-1"})
    run = mini_run()
    judges.judge_with_jev(run)
    jev(ACCEPT, fail_pages={"mobile-cart-1"}, error=RuntimeError("Model provider returned HTTP 401; no action "
                                                                  "executed."))
    again = judges.judge_with_jev(run)  # no reading of Jev's: the task is asked again, the marker is refreshed
    assert again["requests"] == 1 and again["escalated"] == {"request_failed": 1}
    assert [t["task_id"] for t in judges.jev_tasks(run)] == [CART_JEV]
    fake = jev(ACCEPT)  # TypeSafe answers at last
    fixed = judges.judge_with_jev(run)
    assert len(fake.bodies) == 1 and set(fake.bodies[0]["state"]["tasks"]) == {"trick_questions"}
    assert fixed["judged"] == 1 and fixed["escalated"] == {} and fixed["open_tasks"] == 2
    task = next(t for t in run["judgments"]["tasks"] if t["task_id"] == CART_JEV)
    assert "escalation" not in task and task["samples_required"] == 1
    assert not any("escalation" in t for t in run["judgments"]["tasks"])  # 0 escalations left
    final = next(f for f in run["judgments"]["final"] if f["task_id"] == CART_JEV)
    assert (final["label"], final["samples"], final["models"]) == ("present", 1, [JEV_MODEL])
    assert run["model_calls"]["judge_jev"]["requests"] == 2 + 1 + 1

    jev(ACCEPT, fail_pages={"mobile-cart-1"})
    other = mini_run()
    judges.judge_with_jev(other)
    judged(other, CART_JEV, ["absent"])  # Claude started on it: from now on only Claude's samples decide it
    fake = jev(ACCEPT)
    assert judges.judge_with_jev(other)["requests"] == 0 and fake.bodies == []
    task = next(t for t in other["judgments"]["tasks"] if t["task_id"] == CART_JEV)
    assert task["escalation"]["reason"] == "request_failed" and task["samples_required"] == 3


def test_jev_judge_id_is_reserved_and_jev_settles_only_the_verdicts_it_stored_itself(jev):
    run = mini_run()
    tasks = {t["task_id"]: t for t in judgments.ensure_tasks(run)}
    spoof = verdict(CART_JEV, "absent", confidence=0.3)  # a host submitting under Jev's judge_id
    for prompt in (None, judgments.HOST_PROMPT, judges.prompt_id(judges.ClaudeCliJudge())):
        with pytest.raises(ValueError, match="reserved for TypeSafe Jev"):  # the report would credit it to Jev
            judgments.submit(run, " jev ", "claude-sonnet-5-5", [spoof], prompt=prompt)
    assert run["judgments"]["verdicts"] == []
    host = {"task_id": CART_JEV, "judge_id": "j1", "model": "claude-sonnet-5-5", "label": "absent",
            "confidence": 0.3, "evidence": [], "rationale": ""}
    for judge_id in ("jev", "j1"):  # samples cached under the host's namespace before the id was reserved
        judgments.cache_put(tasks[CART_JEV], {**host, "judge_id": judge_id}, judgments.HOST_PROMPT)
    fresh = mini_run()
    judgments.ensure_tasks(fresh)
    assert judgments.apply_cache(fresh, "claude-sonnet-5-5")["reused"] == 1  # j1's; Jev's id is skipped, no error
    assert [v["judge_id"] for v in fresh["judgments"]["verdicts"]] == ["j1"]
    run["judgments"]["verdicts"].append({**spoof, "judge_id": "jev", "model": "claude-sonnet-5-5"})  # stored before
    fake = jev(ACCEPT, fail_pages={"mobile-pdp-1"})
    summary = judges.judge_with_jev(run)
    assert len(fake.bodies) == 1 and summary["escalated"] == {"request_failed": 2}  # the cart task was not asked
    task = next(t for t in run["judgments"]["tasks"] if t["task_id"] == CART_JEV)
    assert task["samples_required"] == 3 and "escalation" not in task  # one host sample decides nothing
    assert CART_JEV in summary["finalize"]["pending"] and run["judgments"]["final"] == []


def test_a_rejected_jev_verdict_keeps_its_reading_and_is_not_retried(jev, monkeypatch):
    jev(ACCEPT)
    read = judges.JevJudge.read

    def misquote(self, task, answers, model):
        verdict, escalation = read(self, task, answers, model)
        if verdict and verdict["evidence"]:
            verdict["evidence"] = [{**verdict["evidence"][0], "quote": "non nel testo della pagina"}]
        return verdict, escalation

    monkeypatch.setattr(judges.JevJudge, "read", misquote)
    run = mini_run()
    summary = judges.judge_with_jev(run)
    assert summary["judged"] == 1 and summary["escalated"] == {"quote_not_verbatim": 2}
    task = next(t for t in run["judgments"]["tasks"] if t["task_id"] == CART_JEV)
    assert {k: task["escalation"][k] for k in ("label", "probability", "reason")} == {
        "label": "present", "probability": 0.75, "reason": "quote_not_verbatim"}
    assert task["samples_required"] == 3 and judges.jev_tasks(run) == []


def test_jev_keeps_out_of_tasks_another_judge_started(jev):
    fake = jev({**ACCEPT, "returns_clarity:mobile-pdp-1": {"label": "clear", "p": 0.5}})
    judge = judges.JevJudge()
    assert judges.judge_with_jev(mini_run(), judge)["escalated"] == {"low_probability": 1}
    other = mini_run()
    judgments.ensure_tasks(other)
    for task_id in (*PDP_JEV, CART_JEV):  # Claude started on every Jev task: a mix of samples would tie
        assert judgments.submit(other, "j1", "model-a", [verdict(task_id, "absent")])["accepted"] == 1
    summary = judges.judge_with_jev(other, judge)  # the same judge: its last call's escalations stay out
    assert summary["requests"] == 0 and summary["escalated"] == {} and len(fake.bodies) == 2
    assert not any("escalation" in t for t in other["judgments"]["tasks"])
    assert all(t["samples_required"] == 3 for t in other["judgments"]["tasks"])
    failing = jev(ACCEPT, fail_pages={"mobile-pdp-1", "mobile-cart-1"})
    run = mini_run()
    summary = judges.judge_with_jev(run)
    assert summary["requests"] == 2 and summary["model"] is None and summary["escalated"] == {"request_failed": 3}
    assert run["model_calls"]["judge_jev"]["model"] is None and len(failing.bodies) == 2


def test_jev_without_a_key_changes_nothing(jev, monkeypatch):
    fake = jev(ACCEPT)
    monkeypatch.delenv("TYPESAFE_API_KEY")
    run = mini_run()
    before = copy.deepcopy(run)
    summary = judges.judge_with_jev(run)
    assert summary["available"] is False and summary["reason"] == "no_typesafe_key" and summary["requests"] == 0
    assert run == before and fake.bodies == []  # every task stays with Claude's three samples
    with pytest.raises(judges.JudgeError, match="TYPESAFE_API_KEY"):
        judges.JevJudge().judge(judgments.ensure_tasks(mini_run()))


def test_jev_cache_namespace_and_pinned_model_reuse(jev, monkeypatch):
    namespace = judges.prompt_id(judges.JevJudge(api_key="k"))
    assert namespace == f"jev:{judges.JEV_PROMPT_VERSION}" and judges.JEV_PROMPT_VERSION != judges.PROMPT_VERSION
    assert namespace not in {judges.prompt_id(judges.ClaudeCliJudge()), judgments.HOST_PROMPT}
    fake = jev(ACCEPT)
    monkeypatch.setenv("TYPESAFE_MODEL", JEV_MODEL)  # pinned: cached samples are valid for that exact model
    first = mini_run()
    assert judges.judge_with_jev(first)["requests"] == 2
    again = mini_run()
    summary = judges.judge_with_jev(again)
    assert summary["reused"] == 3 and summary["requests"] == 0 and len(fake.bodies) == 2
    assert again["judgments"]["final"] == first["judgments"]["final"]
    assert again["model_calls"]["judge_jev"]["requests"] == 0 and first["model_calls"]["judge_jev"]["requests"] == 2
    monkeypatch.setenv("JEV_JUDGE_MIN_P", "0.85")  # a higher floor: cached p 0.8 and 0.75 are not reused
    assert judges.judge_with_jev(mini_run())["reused"] == 1 and len(fake.bodies) == 4
    monkeypatch.delenv("JEV_JUDGE_MIN_P")
    monkeypatch.setenv("TYPESAFE_MODEL", "jev-latest")  # an alias moves on release: never read from the cache
    assert judges.judge_with_jev(mini_run())["reused"] == 0 and len(fake.bodies) == 6
    assert judges.judge_with_jev(mini_run(), use_cache=False)["reused"] == 0


def test_jev_namespace_is_a_fingerprint_of_its_request(monkeypatch):
    """Ruling: no manual bump. The namespace hashes the body request() builds for a fixed page, without sort_keys, so
    a text constant, a question id, the state layout or the order of a head's options moves it; the environment not."""
    base = judges.jev_prompt_version()
    assert base == judges.JEV_PROMPT_VERSION == judges.JevJudge.prompt_version
    assert judgments.JEV_PROMPT_PREFIX == f"{judgments.JEV_BACKEND}:" and judges.JevJudge.backend == "jev"
    assert judges.prompt_id(judges.JevJudge(api_key="k")) == f"{judgments.JEV_PROMPT_PREFIX}{base}"
    with monkeypatch.context() as patch:
        patch.setattr(judges, "JEV_NONE", "None of the snippets states it.")
        assert judges.jev_prompt_version() != base
    request = judges.JevJudge.request

    def edited(change):
        def build(self, tasks):
            body = request(self, tasks)
            change(body)
            return body
        with monkeypatch.context() as patch:
            patch.setattr(judges.JevJudge, "request", build)
            return judges.jev_prompt_version()

    def rename(body):  # a question key
        body["questions"] = {k.replace("label_rev:", "label_reversed:"): v for k, v in body["questions"].items()}

    def unreverse(body):  # the reversed head back in the label head's order: same keys and values
        for qid, question in body["questions"].items():
            if qid.startswith("label_rev:"):
                question["criteria"] = dict(reversed(question["criteria"].items()))

    def layout(body):  # one more key in the state
        body["state"]["page"]["viewport"] = None
    assert len({base, edited(rename), edited(unreverse), edited(layout)}) == 4
    for name, value in (("TYPESAFE_MODEL", "jev-2.0.0"), ("TYPESAFE_API_KEY", "other"), ("JEV_JUDGE_MIN_P", "0.9")):
        monkeypatch.setenv(name, value)
    assert judges.jev_prompt_version() == base


def test_jev_cache_keeps_the_more_confident_answer(jev, monkeypatch):
    assert judges.prompt_id(judges.JevJudge(api_key="k")).startswith(judgments.JEV_PROMPT_PREFIX)
    monkeypatch.setenv("TYPESAFE_MODEL", JEV_MODEL)
    jev(ACCEPT)
    judges.judge_with_jev(mini_run())  # cached: returns_clarity p 0.9, hidden_subscription 0.8, the cart 0.75
    monkeypatch.setenv("JEV_JUDGE_MIN_P", "0.85")  # 0.8 and 0.75 are no longer reused
    fake = jev({task_id: {**plan, "p": 0.95} for task_id, plan in ACCEPT.items()})
    raised = judges.judge_with_jev(mini_run())
    assert raised["reused"] == 1 and raised["judged"] == 2 and len(fake.bodies) == 2
    again = mini_run()  # the accepted 0.95 answers replaced the samples under the floor: nothing is asked again
    summary = judges.judge_with_jev(again)
    assert summary["reused"] == 3 and summary["requests"] == 0 and len(fake.bodies) == 2
    assert {f["task_id"]: f["confidence"] for f in again["judgments"]["final"]} == {
        "returns_clarity:mobile-pdp-1": 0.9, PDP_JEV[0]: 0.95, CART_JEV: 0.95}
    task = next(t for t in again["judgments"]["tasks"] if t["task_id"] == CART_JEV)
    sample = next(v for v in again["judgments"]["verdicts"] if v["task_id"] == CART_JEV)
    namespace = judges.prompt_id(judges.JevJudge())
    judgments.cache_put(task, {**sample, "confidence": 0.9}, namespace)  # a less confident answer never replaces
    assert [v["confidence"] for v in judgments.cached_verdicts(task, JEV_MODEL, namespace)] == [0.95]
    for confidence in (0.5, 0.99):  # outside Jev's namespace the first sample of a judge stays: replays repeat it
        judgments.cache_put(task, {**sample, "confidence": confidence}, judgments.HOST_PROMPT)
    assert [v["confidence"] for v in judgments.cached_verdicts(task, JEV_MODEL, judgments.HOST_PROMPT)] == [0.5]


def test_claude_judges_only_what_jev_left_and_never_trips_on_settled_tasks(jev, fake_claude):
    jev({**ACCEPT, "returns_clarity:mobile-pdp-1": {"label": "clear", "evidence": "none"}})
    run = mini_run()
    judges.judge_with_jev(run)
    assert judgments.finalize(run, samples_required=3)["final"] == 2  # Jev's finals hold one sample: no ValueError
    summary = judges.run_judges(run, [judges.ClaudeCliJudge(f"j{i}") for i in (1, 2, 3)], samples_required=3)
    claude = CLAUDE_TASKS | {"returns_clarity:mobile-pdp-1"}  # Claude's rubrics and the escalated task
    prompts = [json.loads(line)["prompt"] for line in fake_claude.read_text().splitlines()]
    sent = {t["task_id"] for p in prompts for t in json.loads(p.split("TASK:\n", 1)[1])["tasks"]}
    assert sent == claude and summary["accepted"] == 9 and summary["finalize"]["pending"] == []
    finals = {f["task_id"]: f for f in run["judgments"]["final"]}
    assert {t: f["samples"] for t, f in finals.items()} == {**dict.fromkeys(claude, 3), "hidden_subscription:"
                                                            "mobile-pdp-1": 1, CART_JEV: 1}
    with pytest.raises(ValueError, match="judge_with_jev"):
        judges.run_judges(mini_run(), [judges.JevJudge(api_key="k")])


def test_routing_settle_and_escalate_helpers():
    run = mini_run()
    tasks = {t["task_id"]: t for t in judgments.ensure_tasks(run)}
    rubrics = judgments.load_rubrics()
    assert {t: judgments.routing(task, rubrics) for t, task in tasks.items()} == {
        **dict.fromkeys((*PDP_JEV, CART_JEV), "jev"), **dict.fromkeys(CLAUDE_TASKS, "claude")}
    old = {**tasks[CART_JEV], "rubric_version": "rubrics.v1"}  # made under another version: Claude, as then
    assert judgments.routing(old) == "claude" and judgments.routing({**old, "rubric_id": "nope"}) == "claude"
    judged(run, "confirmshaming:mobile-pdp-1", ["absent"] * 3)
    judgments.finalize(run)
    ids = [CART_JEV, "confirmshaming:mobile-pdp-1", "unknown"]
    assert judgments.settle(run, ids) == 1 and judgments.settle(run, ids) == 0  # never twice, never a final task
    assert tasks[CART_JEV]["samples_required"] == 1 and tasks["confirmshaming:mobile-pdp-1"]["samples_required"] == 3
    assert judgments.settle(run, [CART_JEV], samples_required=2) == 0  # never raised
    reading = {"model": JEV_MODEL, "label": "clear", "probability": 0.55, "reason": "low_probability", "x": 1}
    marked = judgments.escalate(run, {PDP_JEV[1]: reading, "confirmshaming:mobile-pdp-1": reading, "unknown": {}})
    assert marked == 1 and set(tasks[PDP_JEV[1]]["escalation"]) == {"from", "model", "label", "probability",
                                                                    "reason", "at"}
    assert judgments.escalate(run, {PDP_JEV[1]: {**reading, "reason": "position_flip"}}) == 0  # the first one stays
    assert tasks[PDP_JEV[1]]["escalation"]["reason"] == "low_probability"
    failed = {"model": JEV_MODEL, "label": None, "probability": None, "reason": "request_failed"}
    assert judgments.escalate(run, {PDP_JEV[0]: failed}) == 1 and judgments.retryable(tasks[PDP_JEV[0]])
    assert judgments.escalate(run, {PDP_JEV[0]: {**failed, "reason": "invalid_response"}}) == 1  # replaced
    assert judgments.escalate(run, {PDP_JEV[0]: reading}) == 1  # Jev's reading replaces a failed request ...
    assert judgments.escalate(run, {PDP_JEV[0]: failed}) == 0  # ... and is never replaced by one
    assert tasks[PDP_JEV[0]]["escalation"]["reason"] == "low_probability" and not judgments.retryable(tasks[PDP_JEV[0]])
    assert judgments.retryable(tasks[CART_JEV]) and judgments.RETRY_REASONS == ("request_failed", "invalid_response")
