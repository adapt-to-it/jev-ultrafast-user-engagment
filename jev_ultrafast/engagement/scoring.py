"""Versioned, deterministic scoring: KPI normalisation, sub-indices, DPR risk and the ERS readiness estimate.

Pure functions over Observations and anchors.json. A KPI that could not be assessed is excluded and reported with
its reason; it is never scored as 0. ERS is a readiness estimate from synthetic sessions, not measured engagement.
"""

import functools
import json
import math
import statistics
from pathlib import Path

from .judgments import observations_from_final, prepared
from .kpis import KPI_LIST, KPIS, Kpi
from .schemas import RISK_INDEX, SUB_INDICES

ANCHORS_PATH = Path(__file__).with_name("anchors.json")
NOT_APPLICABLE = "not_applicable"


def load_anchors(path=None) -> dict:
    return json.loads(Path(path or ANCHORS_PATH).read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _default_anchors() -> dict:
    return load_anchors()


def _number(value, *, allow_bool=False):
    if isinstance(value, bool):
        return float(value) if allow_bool else None
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _key(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value in (0, 1):
        return "true" if value else "false"
    return value if isinstance(value, str) else None


def _interpolate(points, x):
    points = sorted(points)
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def _maps_to_none(spec, value):
    mapping = spec.get("map")
    key = _key(value)
    return mapping is not None and key in mapping and mapping[key] is None


def normalize(kpi_id, value, anchors) -> float | None:
    """0..100 score for one aggregated KPI value, or None when it cannot be scored."""
    spec = anchors["kpis"].get(kpi_id)
    if not spec or value is None:
        return None
    if "map" in spec:
        key = _key(value)
        if key is None and isinstance(value, (int, float)):
            key = str(value)
        score = spec["map"].get(key) if key is not None else None
    elif "points" in spec:
        x = _number(value)
        score = None if x is None else _interpolate(spec["points"], x)
    else:
        return None
    return None if score is None else round(min(100.0, max(0.0, float(score))), 2)


def _wmedian(pairs):
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0.0
    for i, (value, weight) in enumerate(pairs):
        acc += weight
        if math.isclose(acc, total / 2, abs_tol=1e-9) and i + 1 < len(pairs):
            return (value + pairs[i + 1][0]) / 2
        if acc > total / 2:
            return value
    return pairs[-1][0]


def _tidy(value):
    if isinstance(value, float):
        value = round(value, 4)
        return int(value) if value.is_integer() and abs(value) < 1e15 else value
    return value


def _map_score(spec, value):
    """Anchor score of an enum/bool value, or None when it is unmapped or maps to null."""
    mapping = (spec or {}).get("map")
    if mapping is None:
        return None
    key = _key(value)
    if key is None and isinstance(value, (int, float)):
        key = str(value)
    score = mapping.get(key) if key is not None else None
    return _number(score)


def _anchor_score(spec, value):
    """Anchor score of one aggregated value (points or map), or None when the anchors cannot score it."""
    spec = spec or {}
    if "map" in spec:
        return _map_score(spec, value)
    if "points" in spec:
        x = _number(value)
        return None if x is None else _interpolate(spec["points"], x)
    return None


def _pooled(usable, kpi, stage_weights, spec) -> tuple:
    """(value, chosen observation or None) of the KPI's aggregate over usable rows; value None: no valid number."""
    mode = kpi.aggregate
    as_number = kpi.value_type == "confidence"  # a bool counts as 0/1 only for DPR confidences
    if mode == "first":  # the first value that the anchors can score (a null-mapped "none" never hides a real one)
        chosen = next((o for o in usable if _anchor_score(spec, o["value"]) is not None), usable[0])
        return chosen["value"], chosen
    if mode == "best":  # highest anchor score (values without anchors: largest); ties keep the earliest observation
        def rank(o):
            if spec and ("map" in spec or "points" in spec):
                score = _anchor_score(spec, o["value"])
            else:
                score = _number(o["value"], allow_bool=as_number)
            return -math.inf if score is None else score
        chosen = max(usable, key=rank)  # max() keeps the first of equal keys
        return chosen["value"], chosen
    values = [o["value"] for o in usable]
    if mode == "any":
        return any(bool(v) for v in values), None
    if mode == "all":
        return all(bool(v) for v in values), None
    numbers = [(n, o) for o in usable if (n := _number(o["value"], allow_bool=as_number)) is not None]
    if not numbers:
        return None, None
    xs = [n for n, _ in numbers]
    if mode == "sum":
        value = sum(xs)
    elif mode == "max":
        value = max(xs)
    elif mode == "min":
        value = min(xs)
    elif mode == "mean":
        value = statistics.fmean(xs)
    elif mode == "median":
        value = statistics.median(xs)
    elif mode == "wmedian":
        weights = stage_weights or {}
        value = _wmedian([(n, float(weights.get(o.get("stage"), 1))) for n, o in numbers])
    else:
        raise ValueError(f"Unknown aggregate {mode!r} for {kpi.id}")
    return _tidy(value), None


def _aggregate(observations, kpi, stage_weights=None, spec=None) -> tuple:
    """(value, assessed, reason, chosen observation or None, profile or None) for one KPI.

    Rows of two or more profiles are aggregated per profile (rows without a profile join every profile) and the value
    is the profile whose aggregate scores lowest on the anchors: a shopper uses one device, never the union of two.
    Profiles the anchors cannot score are ignored unless none can be scored (then all rows are pooled); DPR
    confidences are always pooled (max is already the worst). profile names the chosen profile only when it scores
    strictly lower than every other scored profile; ties keep the first profile in observation order, unnamed.
    """
    kpi = KPIS[kpi] if isinstance(kpi, str) else kpi
    rows = [o for o in observations if o.get("kpi_id", kpi.id) == kpi.id]
    usable = [o for o in rows if o.get("assessed", True) and o.get("value") is not None]
    if not usable:
        if not rows:
            return None, False, "no_observation", None, None
        reasons = [str(o.get("reason") or "not_assessable") for o in rows]
        real = [r for r in reasons if not r.startswith(NOT_APPLICABLE)]
        return None, False, (real or reasons)[0], None, None
    value, chosen = _pooled(usable, kpi, stage_weights, spec)
    profile = None
    names = list(dict.fromkeys(o["profile"] for o in usable if o.get("profile") is not None))
    if kpi.owner != RISK_INDEX and len(names) > 1:
        scored = []
        for name in names:
            group_value, group_chosen = _pooled([o for o in usable if o.get("profile") in (None, name)], kpi,
                                                stage_weights, spec)
            score = None if group_value is None else _anchor_score(spec, group_value)
            if score is not None:
                scored.append((score, group_value, group_chosen, name))
        if scored:
            worst = min(scored, key=lambda item: item[0])  # min() keeps the first of equal scores
            value, chosen = worst[1], worst[2]
            others = [item[0] for item in scored if item[3] != worst[3]]
            if others and all(worst[0] < other - 1e-9 for other in others):
                profile = worst[3]
    if value is None:
        return None, False, "invalid_value", None, None
    return value, True, None, chosen, profile


def aggregate(observations, kpi, stage_weights=None, spec=None) -> tuple:
    """(value, assessed, reason) for one KPI following kpis.Kpi.aggregate.

    spec is the KPI's anchors entry (default: the packaged anchors.json); it ranks first/best values and picks the
    worst profile when rows of two or more profiles are present.
    """
    kpi = KPIS[kpi] if isinstance(kpi, str) else kpi
    spec = _default_anchors()["kpis"].get(kpi.id) if spec is None else spec
    return _aggregate(observations, kpi, stage_weights, spec)[:3]


def _grade(coverage, anchors):
    for grade, minimum in sorted(anchors["grades"].items(), key=lambda item: -item[1]):
        if coverage + 1e-9 >= minimum:
            return grade
    return None


def _source(kpi: Kpi, rows, chosen=None):
    if chosen is not None:  # first/best: the value comes from one observation
        return "judged" if chosen.get("source") == "judged" else "deterministic"
    sources = {o.get("source") for o in rows if o.get("assessed", True) and o.get("value") is not None}
    if "judged" in sources or (not sources and kpi.judged):
        return "judged"
    return "deterministic"


def _kpi_scores(by_id, anchors, journey, judged):
    stage_weights = anchors.get("stage_weights", {})
    threshold = anchors.get("dpr_fired_confidence", 0.5)
    rows = {
        kpi.id: _aggregate(by_id.get(kpi.id, []), kpi, stage_weights, anchors["kpis"].get(kpi.id))
        for kpi in KPI_LIST
    }
    scores = []
    for kpi in KPI_LIST:
        value, assessed, reason, chosen, profile = rows[kpi.id]
        spec = anchors["kpis"].get(kpi.id, {})
        items = by_id.get(kpi.id, [])
        if assessed and kpi.judged and chosen is not None and reason is None:
            reason = chosen.get("reason")  # e.g. "no_snippets": absence read from pages without relevant text
        applicable = True
        if kpi.scope == "journey" and not journey:
            applicable, reason = False, reason if items else "no_journey"
        if kpi.judged and not judged:
            applicable, reason = False, reason if items else "not_judged"
        normalized = None
        if kpi.owner == RISK_INDEX:
            confidence = _number(value, allow_bool=True) if assessed else None
            value = None if confidence is None else _tidy(min(1.0, max(0.0, confidence)))
            assessed = assessed and value is not None
        elif assessed:
            normalized = normalize(kpi.id, value, anchors)
            if normalized is None and _maps_to_none(spec, value):
                applicable, assessed, reason = False, False, NOT_APPLICABLE
            elif normalized is None and ("map" in spec or "points" in spec):
                assessed, reason = False, "invalid_value"
            elif normalized is not None and kpi.credit_unless:
                signal, fired, *_ = rows[kpi.credit_unless]
                if fired and (_number(signal, allow_bool=True) or 0.0) >= threshold:
                    # a deceptive signal earns no credit: it scores as if absent (its penalty lives in DPR only)
                    normalized, reason = normalize(kpi.id, 0, anchors), f"credit_withheld:{kpi.credit_unless}"
        if not assessed and reason and reason.startswith(NOT_APPLICABLE):
            applicable = False
        scores.append(
            {
                "id": kpi.id,
                "value": value,
                "unit": kpi.unit,
                "profile": profile if assessed else None,
                "normalized": normalized,
                "owner": kpi.owner,
                "source": _source(kpi, items, chosen if assessed else None),
                "assessed": bool(assessed),
                "reason": reason,
                "observations": len(items),
                "provisional": bool(spec.get("provisional", True)),
                "weight": spec.get("weight", 0),
                "applicable": applicable,
            }
        )
    return scores


def _sub_index(members, anchors):
    total = sum(k["weight"] for k in members)
    scored = [k for k in members if k["assessed"] and k["normalized"] is not None]
    assessed_weight = sum(k["weight"] for k in scored)
    if not assessed_weight:
        return {"score": None, "coverage": 0.0, "grade": None, "llm_share": 0.0, "limiting_kpis": []}
    score = sum(k["weight"] * k["normalized"] for k in scored) / assessed_weight
    coverage = assessed_weight / total
    judged = sum(k["weight"] for k in scored if k["source"] == "judged")
    lowest = sorted(scored, key=lambda k: (k["normalized"], -k["weight"], k["id"]))
    return {
        "score": round(score, 1),
        "coverage": round(coverage, 3),
        "grade": _grade(coverage, anchors),
        "llm_share": round(judged / assessed_weight, 3),
        "limiting_kpis": [k["id"] for k in lowest if k["normalized"] < 100][: anchors.get("limiting_kpis", 3)],
        "_exact": score,
        "_coverage": coverage,
    }


def _signals(kpi_scores, by_id):
    signals = []
    for row in kpi_scores:
        if row["owner"] != RISK_INDEX or not row["assessed"] or not row["value"]:
            continue
        kpi = KPIS[row["id"]]
        usable = [o for o in by_id.get(row["id"], []) if o.get("assessed", True) and o.get("value") is not None]
        strongest = max(usable, key=lambda o: _number(o["value"], allow_bool=True) or 0.0, default={})
        signals.append(
            {
                "kpi_id": kpi.id,
                "description": kpi.description,
                "severity": kpi.severity,
                "confidence": row["value"],
                "risk": round(kpi.severity * row["value"], 4),
                "source": row["source"],
                "page_id": strongest.get("page_id"),
                "profile": strongest.get("profile"),
                "evidence": strongest.get("evidence") or {},
            }
        )
    return sorted(signals, key=lambda s: (-s["risk"], s["kpi_id"]))


def _dpr_summary(kpi_scores, signals) -> dict:
    """DprScore: noisy-OR risk plus its coverage over the applicable risk signals.

    coverage is the severity-weighted share of applicable DPR KPIs that were assessed; with coverage 0 the score 0
    means "no penalty applied", not "no risk".
    """
    risk = [k for k in kpi_scores if k["owner"] == RISK_INDEX and k["applicable"]]
    total = sum(KPIS[k["id"]].severity or 0 for k in risk)
    covered = sum(KPIS[k["id"]].severity or 0 for k in risk if k["assessed"])
    return {
        "score": dpr(signals),
        "signals": signals,
        "coverage": round(covered / total, 3) if total else 0.0,
        "assessed": sum(1 for k in risk if k["assessed"]),
        "applicable": len(risk),
        "unassessed": [{"kpi_id": k["id"], "reason": k["reason"] or "no_observation"}
                       for k in risk if not k["assessed"]],
    }


def dpr(signals) -> float:
    """Noisy-OR risk 0..100 over severity x confidence."""
    keep = 1.0
    for signal in signals:
        keep *= 1 - min(1.0, max(0.0, signal["severity"] * signal["confidence"]))
    return round(100 * (1 - keep), 2)


def ers(sub_indices, dpr_score, anchors) -> dict:
    weights = anchors["weights"]
    publish = anchors["publish"]
    total = sum(weights.values())
    covered = {n: sub_indices[n].get("_coverage", sub_indices[n]["coverage"]) for n in sub_indices}  # unrounded gates
    coverage = sum(weights[n] * covered[n] for n in weights) / total
    evidence = sum(weights[n] * covered[n] for n in weights)
    llm_share = sum(weights[n] * covered[n] * sub_indices[n]["llm_share"] for n in weights)
    llm_share = llm_share / evidence if evidence else 0.0
    present = {n: s.get("_exact", s["score"]) for n, s in sub_indices.items() if n in weights}
    present = {n: s for n, s in present.items() if s is not None}
    reasons = []
    if not present:
        reasons.append("nessun sotto-indice valutabile")
    if coverage + 1e-9 < publish["min_coverage"]:
        reasons.append(f"copertura complessiva {coverage:.0%} sotto il minimo {publish['min_coverage']:.0%}")
    for name in SUB_INDICES:
        cov = covered[name]
        if weights.get(name, 0) >= publish.get("major_weight", 15) and cov + 1e-9 < publish["min_major_coverage"]:
            reasons.append(f"copertura {name} {cov:.0%} sotto il minimo {publish['min_major_coverage']:.0%}")
    limiting = min(present, key=lambda n: (present[n], n)) if present else None
    result = {
        "score": None,
        "published": False,
        "grade": None,
        "coverage": round(coverage, 3),
        "llm_share": round(llm_share, 3),
        "reason": "; ".join(reasons) or None,
        "limiting_factor": limiting,
    }
    if reasons:
        return result
    floor = anchors["floor"]
    log_mean = sum(weights[n] * math.log(max(s, floor)) for n, s in present.items()) / sum(weights[n] for n in present)
    value = math.exp(log_mean) * (1 - anchors["dpr_penalty"] * dpr_score / 100)
    result.update(score=round(value, 1), published=True, grade=_grade(coverage, anchors))
    return result


def score(observations, anchors=None, *, journey=None, judged=None) -> dict:
    """ScoreOutput for a list of observations; journey/judged say whether those KPI families apply."""
    anchors = anchors or load_anchors()
    observations = [o for o in observations if o.get("kpi_id") in KPIS]
    if journey is None:
        journey = any(KPIS[o["kpi_id"]].scope == "journey" and o.get("assessed", True) for o in observations)
    if judged is None:
        judged = any(_requests_judgments(o) for o in observations)
    by_id = {}
    for observation in observations:
        by_id.setdefault(observation["kpi_id"], []).append(observation)
    kpi_scores = _kpi_scores(by_id, anchors, journey, judged)
    sub_indices = {}
    for name in SUB_INDICES:
        members = [k for k in kpi_scores if k["owner"] == name and k["weight"] > 0 and k["applicable"]]
        sub_indices[name] = _sub_index(members, anchors)
    signals = _signals(kpi_scores, by_id)
    risk = _dpr_summary(kpi_scores, signals)
    headline = ers(sub_indices, risk["score"], anchors)
    for sub in sub_indices.values():
        sub.pop("_exact", None)
        sub.pop("_coverage", None)
    return {
        "anchors_version": anchors["version"],
        "weights": dict(anchors["weights"]),
        "context": {"journey": bool(journey), "judged": bool(judged)},
        "kpis": kpi_scores,
        "sub_indices": sub_indices,
        "dpr": risk,
        "ers": headline,
        "top_risk_signals": signals[:10],
    }


def _from_judgments(observation) -> bool:
    kpi = KPIS.get(observation.get("kpi_id"))
    return observation.get("source") == "judged" or (kpi is not None and kpi.producer == "judgments")


def _requests_judgments(observation) -> bool:
    """A row that exists only once judgments were requested: any judgments row but the "not_judged" placeholder."""
    return _from_judgments(observation) and observation.get("reason") != "not_judged"


def run_observations(run) -> list:
    """Run observations with judged KPIs derived from the finalised judgments once judgments were requested.

    Every stored row of a judgments-produced KPI (judged rows and deterministic absence rows alike) is replaced, so
    storing observations_from_final() output in run["observations"] never duplicates it.
    """
    observations = list(run.get("observations") or [])
    if prepared(run):
        observations = [o for o in observations if not _from_judgments(o)] + observations_from_final(run)
    return observations


def _journey_rows(journey_run) -> list:
    """Journey-scope observations of a linked journey run, each carrying the journey's profile."""
    profile = (journey_run.get("journey") or {}).get("profile")
    rows = []
    for row in journey_run.get("observations") or []:
        kpi = KPIS.get(row.get("kpi_id"))
        if kpi is not None and kpi.scope == "journey":
            rows.append(row if row.get("profile") or not profile else {**row, "profile": profile})
    return rows


def score_run(run, anchors=None, *, journeys=()) -> dict:
    """RunScores: overall plus one ScoreOutput per profile (site/journey-level observations count in each).

    journeys: journey runs linked to this audit. Their journey-scope observations are appended, so journey KPIs apply
    overall and in the profiles that ran a journey; context.journey_runs lists their run ids. With several journeys
    for one profile, "first" KPIs take the earliest run in the given order. A run that is not a journey run (kind
    "journey" with a journey record) raises ValueError.
    """
    anchors = anchors or load_anchors()
    observations = run_observations(run)
    linked = list(journeys or ())
    for item in linked:
        if not isinstance(item, dict) or item.get("kind") != "journey" or not item.get("journey"):
            raise ValueError(f"not a journey run: {item.get('run_id') if isinstance(item, dict) else item!r}")
    for journey_run in linked:
        observations += _journey_rows(journey_run)
    journey_profiles = {(r.get("journey") or {}).get("profile") for r in (run, *linked) if r.get("journey")}
    journey_rows = [o for o in observations
                    if o.get("kpi_id") in KPIS and KPIS[o["kpi_id"]].scope == "journey" and o.get("assessed", True)]
    journey_profiles |= {o.get("profile") for o in journey_rows}
    journey = bool(journey_profiles)
    judged = prepared(run) or any(_requests_judgments(o) for o in observations)
    profiles = {o["profile"] for o in observations if o.get("profile")}
    profiles |= {p["profile"] for p in run.get("pages") or [] if p.get("profile")}
    profiles |= {p for p in journey_profiles if p}
    journey_runs = [j.get("run_id") for j in linked]

    def output(rows, has_journey):
        result = score(rows, anchors, journey=has_journey, judged=judged)
        result["context"]["journey_runs"] = list(journey_runs)
        return result

    return {
        "overall": output(observations, journey),
        "profiles": {
            profile: output(
                [o for o in observations if o.get("profile") in (None, profile)],
                profile in journey_profiles or None in journey_profiles,
            )
            for profile in sorted(profiles)
        },
    }
