"""Map a TER 3 ``TERResult`` onto the report view-model.

This is the only reports module that knows TER 3's types. When analysis moves
into the hexagon, a sibling mapper from the new result replaces it and every
renderer keeps working unchanged.
"""

from __future__ import annotations

from collections import Counter

from ter.domain.report import (
    EconomicsSummary,
    KeyMetrics,
    LabelTokens,
    PhaseScore,
    PositionalTer,
    Reliability,
    ReportSection,
    SessionReport,
    SpanCell,
    UncertaintySummary,
    WasteEntry,
)
from ter_calculator.models import TERResult


def from_ter_result(
    result: TERResult, *, sections: tuple[ReportSection, ...] = ()
) -> SessionReport:
    """Build a :class:`SessionReport` from a TER 3 analysis result."""
    label_tokens: Counter[str] = Counter()
    phase_tokens: Counter[str] = Counter()
    spans: list[SpanCell] = []
    for item in result.classified_spans:
        label = item.label.value
        phase = item.span.phase.value
        label_tokens[label] += item.span.token_count
        phase_tokens[phase] += item.span.token_count
        spans.append(
            SpanCell(
                position=item.span.position,
                phase=phase,
                label=label,
                tokens=item.span.token_count,
                confidence=float(item.confidence),
            )
        )

    positional: PositionalTer | None = None
    economics: EconomicsSummary | None = None
    if result.economics is not None:
        e = result.economics
        p = e.positional
        positional = PositionalTer(
            early=float(p.early_ter),
            mid=float(p.mid_ter),
            late=float(p.late_ter),
            early_spans=p.early_span_count,
            mid_spans=p.mid_span_count,
            late_spans=p.late_span_count,
        )
        economics = EconomicsSummary(
            input_tokens=e.total_input_tokens,
            output_tokens=e.total_output_tokens,
            cache_read_tokens=e.total_cache_read_tokens,
            cache_write_tokens=e.total_cache_creation_tokens,
            cache_hit_rate=float(e.cache_hit_rate),
            cost_usd=float(e.estimated_cost_usd),
            waste_cost_usd=float(e.estimated_waste_cost_usd),
        )

    uncertainty: UncertaintySummary | None = None
    if result.uncertainty is not None:
        u = result.uncertainty
        uncertainty = UncertaintySummary(
            lower=float(u.interval_lower),
            upper=float(u.interval_upper),
            confidence_level=float(u.confidence_level),
            reliability=Reliability.parse(u.reliability),
            mean_confidence=float(u.mean_confidence),
            low_confidence_tokens=u.low_confidence_tokens,
            low_confidence_share=float(u.low_confidence_share),
            method=u.method,
        )

    return SessionReport(
        session_id=result.session_id,
        classifier_version=result.classifier_version,
        metrics=KeyMetrics(
            ter=float(result.aggregate_ter),
            raw_ratio=float(result.raw_ratio),
            total_tokens=result.total_tokens,
            aligned_tokens=result.aligned_tokens,
            waste_tokens=result.waste_tokens,
        ),
        composition=tuple(
            LabelTokens(label, tokens) for label, tokens in sorted(label_tokens.items())
        ),
        phases=tuple(
            PhaseScore(phase, float(score), phase_tokens.get(phase, 0))
            for phase, score in result.phase_scores.items()
        ),
        spans=tuple(spans),
        waste_patterns=tuple(
            WasteEntry(
                pattern_type=wp.pattern_type,
                description=wp.description,
                start_position=wp.start_position,
                end_position=wp.end_position,
                spans_involved=wp.spans_involved,
                tokens_wasted=wp.tokens_wasted,
            )
            for wp in result.waste_patterns
        ),
        positional=positional,
        economics=economics,
        uncertainty=uncertainty,
        sections=sections,
    )
