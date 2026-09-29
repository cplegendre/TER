"""TER score computation.

The arithmetic lives in the TER 4 domain (``ter.domain.scoring``); this module
adapts TER 3's classified spans to it and keeps ``compute_ter``'s signature
and ``TERResult`` output unchanged.
"""

from __future__ import annotations

from ter.domain.scoring import ScoredSpan, score_spans

from .models import (
    ALIGNED_LABELS,
    PHASE_WEIGHTS_DEFAULT,
    ClassifiedSpan,
    SpanPhase,
    TERResult,
    IntentVector,
)

#: Phases in TER 3's summation order (the ``SpanPhase`` declaration order).
_PHASES: tuple[str, ...] = tuple(phase.value for phase in SpanPhase)


def compute_ter(
    classified_spans: list[ClassifiedSpan],
    session_id: str,
    intent: IntentVector | None = None,
    phase_weights: dict[SpanPhase, float] | None = None,
) -> TERResult:
    """Compute the Token Efficiency Ratio from classified spans.

    Returns per-phase scores, weighted aggregate TER, raw ratio,
    and token counts.
    """
    weights = phase_weights or PHASE_WEIGHTS_DEFAULT
    score = score_spans(
        (
            ScoredSpan(
                phase=cs.span.phase.value,
                tokens=cs.span.token_count,
                aligned=cs.label in ALIGNED_LABELS,
            )
            for cs in classified_spans
        ),
        weights={phase.value: weight for phase, weight in weights.items()},
        phases=_PHASES,
    )

    from .uncertainty import estimate_uncertainty

    return TERResult(
        session_id=session_id,
        aggregate_ter=score.aggregate,
        raw_ratio=score.raw_ratio,
        phase_scores=dict(score.phase_scores),
        total_tokens=score.total_tokens,
        aligned_tokens=score.aligned_tokens,
        waste_tokens=score.waste_tokens,
        intent=intent,
        classified_spans=list(classified_spans),
        uncertainty=estimate_uncertainty(classified_spans),
        classifier_version="v11",
    )
