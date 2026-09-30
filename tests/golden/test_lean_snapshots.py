"""Golden snapshots of the L2 explanation for every corpus session.

Freezes the findings, classifications, value stream, scorecard and evidence
graph (``<name>.lean.json``), the A3 view-model (``<name>.a3.json``) and the
rendered A3 page (``report/<name>.a3.html``). TER inside the A3 is the
offline, pinned TER 3 score, so it matches ``<name>.default.json``. A diff is
a behaviour change: regenerate with ``TER_UPDATE_GOLDEN=1`` and review it.
"""

from __future__ import annotations

import json

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.embedders import HashingEmbedder
from ter.adapters.driven.ter3 import Ter3Scorer
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.reports import render_a3_html
from ter.application import ExplainedSession, ExplainSession

from .conftest import (
    CORPUS,
    SNAPSHOT_DIR,
    assert_matches_snapshot,
    assert_matches_text_snapshot,
)


def _explained(name: str) -> ExplainedSession:
    use_case = ExplainSession(
        ClaudeCodeJsonlSource(),
        RegexTokenizer(),
        Ter3Scorer(RegexTokenizer(), HashingEmbedder()),
    )
    return use_case(CORPUS[name])


@pytest.mark.req("TER-LEN-008")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_lean_analysis_matches_golden_snapshot(name: str) -> None:
    analysis = _explained(name).analysis
    assert_matches_snapshot(f"{name}.lean", json.loads(json.dumps(analysis.as_dict())))


@pytest.mark.req("TER-LEN-008", "TER-RPT-003")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_a3_view_model_matches_golden_snapshot(name: str) -> None:
    a3 = _explained(name).a3
    assert_matches_snapshot(f"{name}.a3", json.loads(json.dumps(a3.as_dict())))


@pytest.mark.req("TER-LEN-008", "TER-RPT-004")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_a3_html_matches_golden_snapshot(name: str) -> None:
    assert_matches_text_snapshot(
        f"report/{name}.a3.html", render_a3_html(_explained(name).a3)
    )


@pytest.mark.req("TER-ANL-012")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_a3_ter_is_the_frozen_ter3_score(name: str) -> None:
    ter = _explained(name).analysis.scorecard.ter
    frozen = json.loads(
        (SNAPSHOT_DIR / f"{name}.default.json").read_text(encoding="utf-8")
    )
    assert ter is not None
    assert ter.value == pytest.approx(frozen["aggregate_ter"], abs=1e-6)
