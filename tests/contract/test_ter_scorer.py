"""Contract suite for the ``TerScorer`` port.

The offline TER 3 adapter and the fixed fake meet the same obligations:
a score in [0, 1], the same score on every call, a named method, and an
error for a session that does not exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ter.adapters.driven.embedders import HashingEmbedder
from ter.adapters.driven.in_memory import FixedTerScorer
from ter.adapters.driven.ter3 import Ter3Scorer
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.ports import TerScorer
from ter_calculator import embedding_cache

from golden.corpus import CORPUS

REFS = [str(CORPUS["rework_loop"]), str(CORPUS["example_session"])]


def _offline() -> TerScorer:
    return Ter3Scorer(RegexTokenizer(), HashingEmbedder())


def _fixed() -> TerScorer:
    return FixedTerScorer({ref: 0.5 for ref in REFS}, method="fixed for tests")


@pytest.fixture(params=[_offline, _fixed], ids=["ter3-offline", "fixed"])
def scorer(request: pytest.FixtureRequest) -> TerScorer:
    made: TerScorer = request.param()
    return made


@pytest.mark.req("TER-ANL-012")
def test_scores_are_bounded_repeatable_and_named(scorer: TerScorer) -> None:
    assert isinstance(scorer, TerScorer)
    assert scorer.method
    for ref in REFS:
        first = scorer.score(ref)
        assert 0.0 <= first <= 1.0
        assert scorer.score(ref) == first


@pytest.mark.req("TER-ANL-012")
def test_missing_session_raises(scorer: TerScorer, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scorer.score(str(tmp_path / "missing.jsonl"))


@pytest.mark.req("TER-ANL-012")
def test_offline_scorer_restores_ter3_seams() -> None:
    name = embedding_cache.DEFAULT_MODEL_NAME
    before_enc = embedding_cache._TIKTOKEN_ENC
    had = name in embedding_cache._MODEL_CACHE
    _offline().score(REFS[0])
    assert embedding_cache._TIKTOKEN_ENC is before_enc
    assert (name in embedding_cache._MODEL_CACHE) == had


@pytest.mark.req("TER-ANL-012")
def test_offline_scorer_matches_the_golden_ter() -> None:
    import json

    snapshot = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "golden"
            / "snapshots"
            / "rework_loop.default.json"
        ).read_text()
    )
    assert _offline().score(REFS[0]) == pytest.approx(
        snapshot["aggregate_ter"], abs=1e-6
    )


@pytest.mark.req("TER-ANL-012")
def test_scorer_needs_both_seams() -> None:
    with pytest.raises(ValueError, match="both"):
        Ter3Scorer(RegexTokenizer(), None)
    assert "embeddings" in Ter3Scorer().method
