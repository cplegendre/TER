"""The Token Efficiency Ratio: how much of what an agent generated served intent.

TER is computed over *scored spans*: agent-generated stretches of output, each
with a phase (reasoning, tool use, generation), a token count and a verdict on
whether it was aligned with the user's intent. Deciding the verdict is the
classifier's job; this module only does the arithmetic, and does it exactly
as TER 3 did so golden scores are unchanged:

* a phase score is ``aligned / total`` for that phase, rounded to 4 places,
  and ``1.0`` for a phase with no tokens (nothing generated, nothing wasted);
* the aggregate TER is the weighted sum of the rounded phase scores, summed
  in phase order and then rounded to 4 places;
* the raw ratio is ``aligned / total`` over every phase, ``1.0`` when empty;
* ``aligned + waste == total`` always.

With non-negative weights that sum to 1, every score lies in ``[0, 1]``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "DEFAULT_PHASE_WEIGHTS",
    "PHASES",
    "EfficiencyScore",
    "ScoredSpan",
    "score_spans",
    "validate_phase_weights",
]

#: Scoring phases, in the order TER sums them.
PHASES: tuple[str, ...] = ("reasoning", "tool_use", "generation")

#: TER's default phase weights. Tool use counts most: it is where waste costs
#: time as well as tokens.
DEFAULT_PHASE_WEIGHTS: Mapping[str, float] = {
    "reasoning": 0.3,
    "tool_use": 0.4,
    "generation": 0.3,
}

_WEIGHT_SUM_TOLERANCE = 0.01


@dataclass(frozen=True, slots=True)
class ScoredSpan:
    """One agent-generated span with its classifier verdict."""

    phase: str
    tokens: int
    aligned: bool


@dataclass(frozen=True, slots=True)
class EfficiencyScore:
    """The result of scoring a set of spans."""

    phase_scores: Mapping[str, float]
    aggregate: float
    raw_ratio: float
    total_tokens: int
    aligned_tokens: int

    @property
    def waste_tokens(self) -> int:
        """Tokens in spans judged not aligned with intent."""
        return self.total_tokens - self.aligned_tokens


def validate_phase_weights(
    weights: Mapping[str, float],
    phases: Sequence[str] = PHASES,
    *,
    tolerance: float = _WEIGHT_SUM_TOLERANCE,
) -> None:
    """Check that weights cover every phase, are non-negative and sum to 1.

    Raises:
        ValueError: If a phase has no weight, a weight is negative or not
            finite, or the weights sum to more than ``tolerance`` away from 1.
    """
    _check_weights(weights, phases)
    total = sum(weights[phase] for phase in phases)
    if abs(total - 1.0) > tolerance:
        raise ValueError(f"Phase weights must sum to 1.0, got {total}")


def score_spans(
    spans: Iterable[ScoredSpan],
    weights: Mapping[str, float] | None = None,
    phases: Sequence[str] = PHASES,
) -> EfficiencyScore:
    """Compute per-phase scores, the weighted aggregate and the raw ratio.

    ``weights`` defaults to :data:`DEFAULT_PHASE_WEIGHTS`; an empty mapping
    also means the defaults. Weights are applied as given, not normalised: use
    :func:`validate_phase_weights` to reject weights that do not sum to 1.

    Raises:
        ValueError: If a span has an unknown phase or negative token count,
            or a phase has no weight or a negative one.
    """
    chosen = weights or DEFAULT_PHASE_WEIGHTS
    _check_weights(chosen, phases)

    aligned: dict[str, int] = dict.fromkeys(phases, 0)
    total: dict[str, int] = dict.fromkeys(phases, 0)
    for span in spans:
        if span.phase not in total:
            raise ValueError(f"Unknown phase {span.phase!r}; expected one of {phases}")
        if span.tokens < 0:
            raise ValueError(f"Token counts cannot be negative, got {span.tokens}")
        total[span.phase] += span.tokens
        if span.aligned:
            aligned[span.phase] += span.tokens

    phase_scores: dict[str, float] = {
        phase: round(aligned[phase] / total[phase], 4) if total[phase] > 0 else 1.0
        for phase in phases
    }
    aggregate = sum(chosen[phase] * phase_scores[phase] for phase in phases)

    total_aligned = sum(aligned.values())
    total_all = sum(total.values())
    raw_ratio = total_aligned / total_all if total_all > 0 else 1.0

    return EfficiencyScore(
        phase_scores=phase_scores,
        aggregate=round(aggregate, 4),
        raw_ratio=round(raw_ratio, 4),
        total_tokens=total_all,
        aligned_tokens=total_aligned,
    )


def _check_weights(weights: Mapping[str, float], phases: Sequence[str]) -> None:
    missing = [phase for phase in phases if phase not in weights]
    if missing:
        raise ValueError(f"No weight for phase(s) {', '.join(missing)}")
    for phase in phases:
        value = weights[phase]
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Weight for {phase} must be finite and >= 0, got {value}")
