"""Golden characterisation of TER 3 analysis (maturity gate for L0).

These tests freeze what ``ter analyze`` computes today for every session in
the golden corpus: the aggregate ratio, phase scores, every span label, waste
patterns, economics and input analysis. The TER 4 rebuild moves this logic
into the hexagon piece by piece; any move that changes a number fails here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ter_calculator.analyze_pipeline import analyze_session, default_analyze_args
from ter_calculator.models import ALIGNED_LABELS, TERResult

from .conftest import CORPUS, assert_matches_snapshot

pytestmark = pytest.mark.usefixtures("pinned_models")

#: Pipeline configurations whose output is frozen, keyed by snapshot suffix.
CONFIGS: dict[str, dict[str, Any]] = {
    "default": {},
    "fine": {"fine_segmentation": True},
}


def _r(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def summarise(result: TERResult) -> dict[str, Any]:
    """Project a TER 3 result onto the fields whose behaviour is frozen."""
    summary: dict[str, Any] = {
        "session_id": result.session_id,
        "classifier_version": result.classifier_version,
        "aggregate_ter": _r(result.aggregate_ter),
        "raw_ratio": _r(result.raw_ratio),
        "phase_scores": {k: _r(v) for k, v in sorted(result.phase_scores.items())},
        "tokens": {
            "total": result.total_tokens,
            "aligned": result.aligned_tokens,
            "waste": result.waste_tokens,
        },
        "intent_confidence": _r(result.intent.confidence) if result.intent else None,
        "spans": [
            {
                "position": cs.span.position,
                "phase": cs.span.phase.value,
                "block_type": cs.span.block_type,
                "tool": cs.span.tool_name,
                "tokens": cs.span.token_count,
                "label": cs.label.value,
                "confidence": _r(cs.confidence),
                "cosine": _r(cs.cosine_similarity),
                "reason": cs.explanation.reason_code if cs.explanation else None,
            }
            for cs in result.classified_spans
        ],
        "waste_patterns": [
            {
                "type": wp.pattern_type,
                "start": wp.start_position,
                "end": wp.end_position,
                "spans": wp.spans_involved,
                "tokens": wp.tokens_wasted,
            }
            for wp in result.waste_patterns
        ],
    }
    if result.uncertainty is not None:
        u = result.uncertainty
        summary["uncertainty"] = {
            "interval": [_r(u.interval_lower), _r(u.interval_upper)],
            "low_confidence_tokens": u.low_confidence_tokens,
            "reliability": u.reliability,
        }
    if result.economics is not None:
        e = result.economics
        summary["economics"] = {
            "input": e.total_input_tokens,
            "output": e.total_output_tokens,
            "cache_creation": e.total_cache_creation_tokens,
            "cache_read": e.total_cache_read_tokens,
            "cache_hit_rate": _r(e.cache_hit_rate),
            "cost_usd": _r(e.estimated_cost_usd),
            "waste_cost_usd": _r(e.estimated_waste_cost_usd),
            "positional": [
                _r(e.positional.early_ter),
                _r(e.positional.mid_ter),
                _r(e.positional.late_ter),
            ],
            "input_growth": _r(e.input_growth.growth_rate),
            "context_bloat": e.input_growth.context_bloat_detected,
        }
    if result.input_analysis is not None:
        ia = result.input_analysis
        summary["input_analysis"] = {
            "user_ratio": _r(ia.token_breakdown.user_ratio),
            "prompt_redundancy": _r(ia.prompt_similarity.prompt_redundancy_score),
            "similar_pairs": len(ia.prompt_similarity.similar_pairs),
            "drift": ia.intent_drift.overall_trajectory,
            "drift_steps": [s.drift_type for s in ia.intent_drift.steps],
            "alignment": _r(ia.prompt_response_alignment.average_alignment),
            "low_alignment": ia.prompt_response_alignment.low_alignment_count,
        }
    return summary


def _analyse(path: Path, overrides: dict[str, Any]) -> TERResult:
    args = default_analyze_args(str(path))
    for key, value in overrides.items():
        setattr(args, key, value)
    return analyze_session(args)


@pytest.mark.req("TER-ANL-000")
@pytest.mark.parametrize("config", sorted(CONFIGS))
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_ter3_analysis_matches_golden_snapshot(name: str, config: str) -> None:
    result = _analyse(CORPUS[name], CONFIGS[config])
    assert_matches_snapshot(f"{name}.{config}", summarise(result))


@pytest.mark.req("TER-ANL-001")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_user_authored_tokens_are_never_scored(name: str) -> None:
    result = _analyse(CORPUS[name], {})
    roles = {cs.span.source_role for cs in result.classified_spans}
    assert roles <= {"assistant"}
    assert result.total_tokens == sum(
        cs.span.token_count for cs in result.classified_spans
    )


@pytest.mark.req("TER-ANL-002")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_accounting_invariants_hold(name: str) -> None:
    result = _analyse(CORPUS[name], {})
    assert result.aligned_tokens + result.waste_tokens == result.total_tokens
    assert result.aligned_tokens == sum(
        cs.span.token_count
        for cs in result.classified_spans
        if cs.label in ALIGNED_LABELS
    )
    assert 0.0 <= result.aggregate_ter <= 1.0
    assert all(0.0 <= score <= 1.0 for score in result.phase_scores.values())
