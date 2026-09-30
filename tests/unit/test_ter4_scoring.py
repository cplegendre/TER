"""Unit and property-style tests for the TER 4 scoring domain."""

from __future__ import annotations

import random

import pytest

from ter.domain import (
    DEFAULT_PHASE_WEIGHTS,
    PHASES,
    EfficiencyScore,
    ScoredSpan,
    score_spans,
    validate_phase_weights,
)


def _random_spans(seed: int, count: int) -> list[ScoredSpan]:
    rng = random.Random(seed)
    return [
        ScoredSpan(
            phase=rng.choice(PHASES),
            tokens=rng.randint(0, 500),
            aligned=rng.random() < 0.6,
        )
        for _ in range(count)
    ]


def _random_weights(seed: int) -> dict[str, float]:
    rng = random.Random(seed)
    raw = [rng.random() + 1e-9 for _ in PHASES]
    total = sum(raw)
    return {phase: value / total for phase, value in zip(PHASES, raw)}


SEEDS = range(40)


class TestExamples:
    def test_empty_input_scores_one_with_no_tokens(self) -> None:
        score = score_spans([])
        assert score == EfficiencyScore(
            phase_scores=dict.fromkeys(PHASES, 1.0),
            aggregate=1.0,
            raw_ratio=1.0,
            total_tokens=0,
            aligned_tokens=0,
        )
        assert score.waste_tokens == 0

    def test_phase_scores_and_weighted_aggregate(self) -> None:
        spans = [
            ScoredSpan("reasoning", 100, True),
            ScoredSpan("tool_use", 100, False),
            ScoredSpan("generation", 60, True),
            ScoredSpan("generation", 40, False),
        ]
        score = score_spans(spans)
        assert score.phase_scores == {
            "reasoning": 1.0,
            "tool_use": 0.0,
            "generation": 0.6,
        }
        assert score.aggregate == pytest.approx(0.3 * 1.0 + 0.4 * 0.0 + 0.3 * 0.6)
        assert score.raw_ratio == 0.5333
        assert (score.total_tokens, score.aligned_tokens, score.waste_tokens) == (
            300,
            160,
            140,
        )

    def test_phase_scores_are_rounded_to_four_places(self) -> None:
        score = score_spans(
            [ScoredSpan("reasoning", 1, True), ScoredSpan("reasoning", 2, False)]
        )
        assert score.phase_scores["reasoning"] == 0.3333
        assert score.raw_ratio == 0.3333

    def test_empty_weights_mean_the_defaults(self) -> None:
        spans = _random_spans(7, 20)
        assert score_spans(spans, {}) == score_spans(spans, DEFAULT_PHASE_WEIGHTS)

    def test_custom_weights_are_applied(self) -> None:
        spans = [ScoredSpan("reasoning", 10, True), ScoredSpan("tool_use", 10, False)]
        weights = {"reasoning": 0.5, "tool_use": 0.1, "generation": 0.4}
        assert score_spans(spans, weights).aggregate == pytest.approx(0.9)

    def test_custom_phases(self) -> None:
        score = score_spans(
            [ScoredSpan("plan", 4, True), ScoredSpan("act", 4, False)],
            weights={"plan": 0.5, "act": 0.5},
            phases=("plan", "act"),
        )
        assert score.phase_scores == {"plan": 1.0, "act": 0.0}
        assert score.aggregate == 0.5

    def test_unknown_phase_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown phase"):
            score_spans([ScoredSpan("planning", 5, True)])

    def test_negative_tokens_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            score_spans([ScoredSpan("reasoning", -1, True)])

    @pytest.mark.parametrize(
        "weights, message",
        [
            ({"reasoning": 0.5, "tool_use": 0.5}, "No weight for phase"),
            ({"reasoning": -0.1, "tool_use": 0.6, "generation": 0.5}, ">= 0"),
            ({"reasoning": float("nan"), "tool_use": 0.5, "generation": 0.5}, ">= 0"),
        ],
    )
    def test_invalid_weights_are_rejected(
        self, weights: dict[str, float], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            score_spans([], weights)
        with pytest.raises(ValueError, match=message):
            validate_phase_weights(weights)


class TestPhaseWeightSums:
    def test_default_weights_sum_to_one(self) -> None:
        validate_phase_weights(DEFAULT_PHASE_WEIGHTS)
        assert sum(DEFAULT_PHASE_WEIGHTS.values()) == pytest.approx(1.0)

    @pytest.mark.parametrize("total", [0.5, 0.98, 1.02, 2.0])
    def test_weights_off_one_are_rejected(self, total: float) -> None:
        weights = {phase: total / len(PHASES) for phase in PHASES}
        with pytest.raises(ValueError, match="sum to 1.0"):
            validate_phase_weights(weights)

    def test_weights_within_tolerance_are_accepted(self) -> None:
        validate_phase_weights({"reasoning": 0.3, "tool_use": 0.4, "generation": 0.305})

    def test_weights_are_applied_as_given_not_normalised(self) -> None:
        spans = _random_spans(3, 30)
        doubled = {phase: 2 * w for phase, w in DEFAULT_PHASE_WEIGHTS.items()}
        base = score_spans(spans).aggregate
        assert score_spans(spans, doubled).aggregate == pytest.approx(
            2 * base, abs=2e-4
        )


@pytest.mark.req("TER-ANL-002")
class TestProperties:
    @pytest.mark.parametrize("seed", SEEDS)
    def test_aligned_plus_waste_equals_total(self, seed: int) -> None:
        spans = _random_spans(seed, seed % 25)
        score = score_spans(spans, _random_weights(seed))
        assert score.aligned_tokens + score.waste_tokens == score.total_tokens
        assert score.total_tokens == sum(s.tokens for s in spans)
        assert score.aligned_tokens == sum(s.tokens for s in spans if s.aligned)

    @pytest.mark.parametrize("seed", SEEDS)
    def test_scores_lie_between_zero_and_one(self, seed: int) -> None:
        score = score_spans(_random_spans(seed, 1 + seed % 30), _random_weights(seed))
        assert 0.0 <= score.aggregate <= 1.0
        assert 0.0 <= score.raw_ratio <= 1.0
        assert all(0.0 <= v <= 1.0 for v in score.phase_scores.values())

    @pytest.mark.parametrize("seed", SEEDS)
    def test_adding_a_waste_span_never_raises_ter(self, seed: int) -> None:
        rng = random.Random(seed)
        spans = _random_spans(seed, seed % 20)
        weights = _random_weights(seed)
        before = score_spans(spans, weights)
        waste = ScoredSpan(rng.choice(PHASES), rng.randint(1, 400), aligned=False)
        after = score_spans([*spans, waste], weights)
        assert after.aggregate <= before.aggregate
        assert after.raw_ratio <= before.raw_ratio
        assert after.phase_scores[waste.phase] <= before.phase_scores[waste.phase]
        assert after.waste_tokens == before.waste_tokens + waste.tokens

    @pytest.mark.parametrize("seed", SEEDS)
    def test_order_of_spans_does_not_matter(self, seed: int) -> None:
        spans = _random_spans(seed, 15)
        shuffled = list(spans)
        random.Random(seed + 1).shuffle(shuffled)
        assert score_spans(spans) == score_spans(shuffled)

    @pytest.mark.parametrize("seed", SEEDS)
    def test_all_aligned_scores_one(self, seed: int) -> None:
        spans = [ScoredSpan(s.phase, s.tokens, True) for s in _random_spans(seed, 10)]
        score = score_spans(spans, _random_weights(seed))
        assert score.raw_ratio == 1.0
        assert score.aggregate == pytest.approx(1.0, abs=1e-4)
