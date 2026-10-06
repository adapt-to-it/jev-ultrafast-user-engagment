"""docs/engagement-kpi.md repeats ids, anchors, flags and severities of kpis.py and anchors.json: keep them in step.

anchors.json wins on any difference, so a failure here means the document needs realigning.
"""

import re
from pathlib import Path

import pytest

from jev_ultrafast.engagement import scoring
from jev_ultrafast.engagement.kpis import KPI_LIST

DOC = Path(__file__).parents[1] / "docs" / "engagement-kpi.md"
ANCHORS = scoring.load_anchors()
ROW = re.compile(r"^\| `((?:PERF|FAI|TRI|PTI|CCL|MPI|DPR)\.[A-Z0-9_]+)` \|")
PAIR = re.compile(r"`([^`→]+)→([^`]+)`")
UNITS = {"score": "punteggio", "count": "conteggio", "ratio": "rapporto"}


def catalogue_rows():
    """{kpi id: cells} for the rows of section 4 (the catalogue), in document order."""
    text = DOC.read_text(encoding="utf-8")
    section = text.split("\n## 4. ", 1)[1].split("\n## 5. ", 1)[0]
    rows = {}
    for line in section.splitlines():
        match = ROW.match(line)
        if match:
            assert match.group(1) not in rows, f"{match.group(1)} appears twice in section 4"
            rows[match.group(1)] = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
    return rows


ROWS = catalogue_rows()
SCORED = [k for k in KPI_LIST if k.owner != "DPR"]


def test_catalogue_lists_every_kpi_once_in_registry_order():
    assert list(ROWS) == [k.id for k in KPI_LIST]
    for kpi_id, cells in ROWS.items():
        assert len(cells) == (6 if kpi_id.startswith("DPR.") else 7), kpi_id


@pytest.mark.parametrize("kpi", SCORED, ids=lambda k: k.id)
def test_anchor_points_and_maps_match_anchors_json(kpi):
    spec = ANCHORS["kpis"][kpi.id]
    pairs = PAIR.findall(ROWS[kpi.id][4])
    if "points" in spec:
        assert {(float(v), float(s)) for v, s in pairs} == {(float(v), float(s)) for v, s in spec["points"]}
    elif "map" in spec:
        shown = {key: None if "non applicabile" in score else float(score) for key, score in pairs}
        assert shown == {key: None if score is None else float(score) for key, score in spec["map"].items()}
    else:
        assert not pairs


@pytest.mark.parametrize("kpi", KPI_LIST, ids=lambda k: k.id)
def test_published_or_provisional_label_matches_flag(kpi):
    label = "Provv." if ANCHORS["kpis"][kpi.id]["provisional"] else "Pubbl."
    assert ROWS[kpi.id][4].startswith(label)


@pytest.mark.parametrize("kpi", [k for k in KPI_LIST if k.owner == "DPR"], ids=lambda k: k.id)
def test_dpr_severity_matches_registry(kpi):
    assert float(ROWS[kpi.id][3].replace(",", ".")) == kpi.severity


@pytest.mark.parametrize("kpi", SCORED, ids=lambda k: k.id)
def test_unit_and_stages_match_registry(kpi):
    cells = ROWS[kpi.id]
    assert cells[3] == UNITS.get(kpi.unit, kpi.unit)
    if kpi.scope == "journey":
        stages = "journey"
    elif kpi.stages is None:
        stages = "tutti"
    else:
        stages = ", ".join("checkout" if s == "checkout_entry" else s for s in kpi.stages)
    assert cells[5] == stages
