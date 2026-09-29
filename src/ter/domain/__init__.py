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

__all__ = [
    "Actor",
    "Event",
    "EventId",
    "EventKind",
    "Maturity",
    "Provenance",
    "SessionTrace",
    "TokenUsage",
    "ToolCall",
    "ToolKind",
    "UnrecognisedRecord",
    "make_event_id",
]
