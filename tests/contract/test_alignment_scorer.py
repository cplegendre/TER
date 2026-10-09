"""Contract suite for the ``AlignmentScorer`` port.

The domain's lexical default and the embedding adapter (offline, with the
hashing embedder) meet the same obligations: a score in [0, 1], the same
score on every call, 0 when either side has no terms, a top score for
identical terms, more for shared terms than for disjoint ones, and a
published name and rule.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ter.adapters.driven.alignment import EmbeddingAlignment
from ter.adapters.driven.embedders import HashingEmbedder
from ter.domain.lean import LEXICAL_ALIGNMENT, key_terms
from ter.ports import AlignmentScorer

pytestmark = pytest.mark.req("TER-ITN-004")

_MAKERS: dict[str, Callable[[], AlignmentScorer]] = {
    "lexical": lambda: LEXICAL_ALIGNMENT,
    "embedding-hashing": lambda: EmbeddingAlignment(HashingEmbedder()),
}

INTENT = key_terms("Add a mode function that raises ValueError on an empty list.")
ON = key_terms("def mode(xs): raise ValueError('empty list')")
OFF = key_terms("def variance(xs): mean of squared deviations")


@pytest.fixture(params=sorted(_MAKERS), ids=sorted(_MAKERS))
def scorer(request: pytest.FixtureRequest) -> AlignmentScorer:
    return _MAKERS[request.param]()


def test_is_the_port(scorer: AlignmentScorer) -> None:
    assert isinstance(scorer, AlignmentScorer)
    assert scorer.name and scorer.rule


def test_score_is_bounded_and_deterministic(scorer: AlignmentScorer) -> None:
    for activity in (ON, OFF, INTENT):
        first = scorer.score(INTENT, activity)
        assert 0.0 <= first <= 1.0
        assert scorer.score(INTENT, activity) == first


def test_empty_sides_score_zero(scorer: AlignmentScorer) -> None:
    assert scorer.score(frozenset(), ON) == 0.0
    assert scorer.score(INTENT, frozenset()) == 0.0


def test_identical_terms_score_top(scorer: AlignmentScorer) -> None:
    assert scorer.score(INTENT, INTENT) == pytest.approx(1.0, abs=1e-6)


def test_shared_terms_score_above_disjoint_terms(scorer: AlignmentScorer) -> None:
    assert scorer.score(INTENT, ON) > scorer.score(INTENT, OFF)
