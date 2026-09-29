"""Forward, backward and point traces, and the Markdown coverage report."""

from __future__ import annotations

import pytest

from ter.adapters.driving.req_report import bar, point_grid, render_markdown
from ter.domain import Maturity
from ter.domain.requirements import (
    LevelCoverage,
    Requirement,
    TestOutcome,
    backward_trace,
    forward_trace,
    level_coverage,
    point_trace,
    trace,
)


def _req(
    rid: str, level: str, status: str, points: list[int] | None = None
) -> Requirement:
    return Requirement.from_mapping(
        {
            "id": rid,
            "pattern": "ubiquitous",
            "text": "TER shall count tokens | exactly.",
            "level": level,
            "status": status,
            "rationale": "Because.",
            "source_points": points or [],
        }
    )


CATALOGUE = [
    _req("TER-AAA-001", "L0", "verified", [1, 2]),
    _req("TER-AAA-002", "L0", "verified"),
    _req("TER-AAA-003", "L1", "verified", [200]),
    _req("TER-AAA-004", "L2", "planned"),
]
PASS = TestOutcome("tests/a.py::t", "passed")
FAIL = TestOutcome("tests/b.py::t", "failed")


@pytest.mark.req("TER-REQ-002")
def test_forward_trace_flags_verified_requirements_without_a_passing_test() -> None:
    results = {"TER-AAA-001": [FAIL, PASS], "TER-AAA-002": [FAIL, FAIL]}
    gaps = forward_trace(CATALOGUE, results, Maturity.MEASURED)
    assert [g.requirement.id for g in gaps] == ["TER-AAA-002"]
    assert gaps[0].reason == "2 citing test(s), none passed (failed)"


@pytest.mark.req("TER-REQ-002")
def test_forward_trace_only_enforces_levels_at_or_below_the_gate() -> None:
    results = {"TER-AAA-001": [PASS], "TER-AAA-002": [PASS]}
    assert forward_trace(CATALOGUE, results, Maturity.MEASURED) == []
    gaps = forward_trace(CATALOGUE, results, Maturity.LEARNING)
    assert [g.requirement.id for g in gaps] == ["TER-AAA-003"]
    assert gaps[0].reason == "no test cites it"


@pytest.mark.req("TER-REQ-003")
def test_backward_trace_names_tests_citing_unknown_ids() -> None:
    citations = {"TER-AAA-001": ["t1"], "TER-ZZZ-999": ["tests/x.py:3"]}
    assert backward_trace(CATALOGUE, citations) == [("TER-ZZZ-999", ("tests/x.py:3",))]


def test_point_trace_lists_uncovered_points() -> None:
    uncovered = point_trace(CATALOGUE, total=5)
    assert uncovered == [3, 4, 5]
    assert len(point_trace(CATALOGUE)) == 197


def test_level_coverage_counts_each_level() -> None:
    rows = {
        row.level: row for row in level_coverage(CATALOGUE, {"TER-AAA-001": [PASS]})
    }
    assert len(rows) == len(Maturity)
    l0 = rows[Maturity.MEASURED]
    assert (l0.total, l0.verified, l0.traced, l0.planned) == (2, 2, 1, 0)
    assert l0.ratio == 0.5
    assert rows[Maturity.EXPLAINED].planned == 1
    assert rows[Maturity.LEARNING].ratio == 0.0


def test_trace_reports_promotable_planned_requirements() -> None:
    results = {
        "TER-AAA-001": [PASS],
        "TER-AAA-002": [PASS],
        "TER-AAA-004": [PASS],
        "TER-NEW-001": [PASS],
    }
    report = trace(CATALOGUE, results, Maturity.MEASURED)
    assert report.promotable == ("TER-AAA-004",)
    assert [rid for rid, _ in report.unknown_ids] == ["TER-NEW-001"]
    assert not report.ok


def test_bar_shows_traced_verified_and_planned_cells() -> None:
    assert bar(LevelCoverage(Maturity.MEASURED, 4, 3, 2), width=8) == "████▓▓░░"
    assert bar(LevelCoverage(Maturity.MEASURED, 0, 0, 0), width=4) == "░░░░"


def test_point_grid_marks_covered_points() -> None:
    assert point_grid([2, 4], total=5, per_row=3) == "  1 ■ · ■\n  4 · ■"


def test_markdown_report_has_bars_charts_and_failures() -> None:
    results = {"TER-AAA-001": [PASS], "TER-ZZZ-001": [PASS], "TER-AAA-004": [PASS]}
    report = trace(CATALOGUE, results, Maturity.MEASURED)
    text = render_markdown(CATALOGUE, results, report)
    assert "**Gate L0 Measured: FAIL**" in text
    assert "| L0 Measured (gate) |" in text
    assert "```mermaid" in text and "pie showData" in text
    assert "**TER-AAA-002** (L0): no test cites it" in text
    assert "**TER-ZZZ-001** is cited but not in the catalogue" in text
    assert "## Ready to promote" in text
    assert "3 of 200 vision points" in text
    assert "### L2 Explained" in text and "### L3" not in text
    assert "count tokens \\| exactly" in text


def test_markdown_report_without_results() -> None:
    report = trace(CATALOGUE, {}, Maturity.MEASURED)
    text = render_markdown(CATALOGUE, {}, report, with_results=False)
    assert "no test results supplied" in text
    assert "| – |" in text


def test_passing_report_says_pass() -> None:
    results = {"TER-AAA-001": [PASS], "TER-AAA-002": [PASS]}
    report = trace(CATALOGUE, results, Maturity.MEASURED)
    assert report.ok
    assert "PASS" in render_markdown(CATALOGUE, results, report)
