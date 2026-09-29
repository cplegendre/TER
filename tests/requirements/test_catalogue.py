"""The shipped requirement catalogue parses, lints clean and is cited correctly.

Fast static checks that run on every pytest invocation; the CI gate
(`ter-req trace`) additionally checks that verified requirements have
passing tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ter.adapters.driven.requirements_yaml import load_catalogue
from ter.adapters.driving.req_cli import scan_citations
from ter.domain import Maturity
from ter.domain.requirements import backward_trace, lint_catalogue

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = load_catalogue(ROOT / "requirements")

# Requirements the L0 foundation already cites from its tests.
L0_BASELINE = {
    "TER-ANL-000",
    "TER-ANL-001",
    "TER-ANL-002",
    "TER-SRC-002",
    "TER-SRC-004",
    "TER-ARC-001",
    "TER-INT-001",
}
STARTER_PLANNED = {
    "TER-OBS-003",
    "TER-OBS-004",
    "TER-ANL-010",
    "TER-ANL-020",
    "TER-ANL-021",
    "TER-EVD-003",
    "TER-INT-007",
    "TER-INT-009",
    "TER-RTE-001",
    "TER-EXP-001",
}


@pytest.mark.req("TER-REQ-001")
def test_every_requirement_is_valid_ears() -> None:
    issues = lint_catalogue(CATALOGUE.requirements, CATALOGUE.vocabulary)
    assert not issues, "\n".join(map(str, issues))


@pytest.mark.req("TER-REQ-003")
def test_every_req_marker_cites_a_known_requirement() -> None:
    citations = scan_citations(ROOT / "tests")
    assert citations, "the scan found no req markers at all"
    unknown = backward_trace(CATALOGUE.requirements, citations)
    assert not unknown, unknown


def test_baseline_and_starter_requirements_are_catalogued() -> None:
    by_id = {r.id: r for r in CATALOGUE.requirements}
    assert L0_BASELINE | STARTER_PLANNED <= set(by_id)
    assert all(
        by_id[rid].verified and by_id[rid].level is Maturity.MEASURED
        for rid in L0_BASELINE
    )


def test_every_level_above_l0_has_a_requirement() -> None:
    levels = {r.level for r in CATALOGUE.requirements}
    assert levels == set(Maturity)


def test_verified_requirements_are_cited_by_some_test() -> None:
    citations = scan_citations(ROOT / "tests")
    uncited = [
        r.id for r in CATALOGUE.requirements if r.verified and r.id not in citations
    ]
    assert not uncited
