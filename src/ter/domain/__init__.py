"""The pure TER domain: the Lean model of an agentic software session.

Nothing in this package performs IO or knows about a specific agent harness,
tokenizer or model vendor. Those arrive through ``ter.ports``.
"""

from __future__ import annotations

from .events import (
    Actor,
    Event,
    EventId,
    EventKind,
    Provenance,
    SessionTrace,
    TokenUsage,
    ToolCall,
    ToolKind,
    UnrecognisedRecord,
    make_event_id,
)
from .maturity import Maturity
from .pricing import (
    PriceEntry,
    PriceSchedule,
    Rates,
    TokenCounts,
    UnknownModelError,
    token_cost,
    usage_cost,
)
from .scoring import (
    DEFAULT_PHASE_WEIGHTS,
    PHASES,
    EfficiencyScore,
    ScoredSpan,
    score_spans,
    validate_phase_weights,
)
from .stream import (
    AnalysisEngine,
    EventClass,
    Signal,
    Signals,
    StreamReport,
    TimelineRow,
    TokenCounter,
    analyse_batch,
)

__all__ = [
    "Actor",
    "AnalysisEngine",
    "DEFAULT_PHASE_WEIGHTS",
    "EfficiencyScore",
    "Event",
    "EventClass",
    "EventId",
    "EventKind",
    "Maturity",
    "PHASES",
    "PriceEntry",
    "PriceSchedule",
    "Provenance",
    "Rates",
    "ScoredSpan",
    "SessionTrace",
    "Signal",
    "Signals",
    "StreamReport",
    "TimelineRow",
    "TokenCounter",
    "TokenCounts",
    "TokenUsage",
    "ToolCall",
    "ToolKind",
    "UnknownModelError",
    "UnrecognisedRecord",
    "analyse_batch",
    "make_event_id",
    "score_spans",
    "token_cost",
    "usage_cost",
    "validate_phase_weights",
]
