"""Golden snapshots of the TER 4 visual report layer.

For every session in the corpus this freezes the report view-model, the
self-contained HTML report and (for the shipped sample) each standalone SVG
chart. Tokenizer and embedder are pinned, so a diff means the mapper or a
renderer changed. Regenerate with ``TER_UPDATE_GOLDEN=1``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from ter.adapters.driving.reports import render_report_html, report_charts
from ter.adapters.driving.reports.ter3 import from_ter_result
from ter.domain.report import SessionReport
from ter_calculator.analyze_pipeline import analyze_session, default_analyze_args

from .conftest import CORPUS, assert_matches_snapshot, assert_matches_text_snapshot

pytestmark = pytest.mark.usefixtures("pinned_models")


def _report(name: str) -> SessionReport:
    return from_ter_result(analyze_session(default_analyze_args(str(CORPUS[name]))))


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_rounded(v) for v in value]
    return value


@pytest.mark.req("TER-RPT-001")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_report_view_model_matches_golden_snapshot(name: str) -> None:
    report = _report(name)
    projected = _rounded(dataclasses.asdict(report))
    projected["waste_by_type"] = _rounded(
        [dataclasses.asdict(w) for w in report.waste_by_type()]
    )
    assert_matches_snapshot(f"{name}.report", projected)


@pytest.mark.req("TER-RPT-002")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_html_report_matches_golden_snapshot(name: str) -> None:
    assert_matches_text_snapshot(
        f"report/{name}.html", render_report_html(_report(name))
    )


@pytest.mark.req("TER-RPT-002")
def test_sample_session_charts_match_golden_snapshots() -> None:
    charts = report_charts(_report("example_session"))
    assert {"key_metrics", "composition", "span_timeline", "phase_scores"} <= set(
        charts
    )
    for chart, svg in charts.items():
        assert_matches_text_snapshot(f"report/example_session/{chart}.svg", svg)
