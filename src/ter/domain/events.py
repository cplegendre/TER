"""Provider-neutral event model: the ``ter.event`` contract.

An agent session is a sequence of events. Every harness adapter translates
its native records into these types, and every analysis in TER reads only
these types. Detectors reason about *tool kinds* (``fs.read``) rather than
native tool names (``Read``), so a second harness needs a new adapter and no
change here.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NewType

#: Version of the normalised ``ter.event`` contract. Bumped on any change to
#: the fields below that consumers can observe.
EVENT_SCHEMA_VERSION = "ter.event/0.1"

#: Stable, content-derived identity of an event. The same source record always
#: yields the same id, which makes replay idempotent and findings citable.
EventId = NewType("EventId", str)


class Actor(StrEnum):
    """Who produced an event."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class EventKind(StrEnum):
    """What happened. Values are stable identifiers in the ``ter.event`` contract."""

    PROMPT = "intent.stated"
    REASONING = "reasoning"
    RESPONSE = "response"
    TOOL_REQUESTED = "tool.requested"
    TOOL_COMPLETED = "tool.completed"

    @property
    def is_generated(self) -> bool:
        """True for events the agent generated, which are the only ones TER scores."""
        return self in {
            EventKind.REASONING,
            EventKind.RESPONSE,
            EventKind.TOOL_REQUESTED,
        }


class ToolKind(StrEnum):
    """Harness-independent category of a tool call."""

    FS_READ = "fs.read"
    FS_SEARCH = "fs.search"
    FS_EDIT = "fs.edit"
    FS_WRITE = "fs.write"
    EXEC_SHELL = "exec.shell"
    NET_FETCH = "net.fetch"
    AGENT_HANDOFF = "agent.handoff"
    PLAN = "plan.todo"
    OTHER = "other"


@dataclass(frozen=True)
class TokenUsage:
    """Provider-reported token usage for one model turn."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation, keeping the native name alongside the neutral kind."""

    native_name: str
    kind: ToolKind
    call_id: str | None = None
    arguments: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Provenance:
    """Where an event came from, precisely enough to cite it in a report."""

    source: str
    record_id: str
    lines: tuple[int, ...] = ()
    block_index: int | None = None
    fingerprint: str | None = None


@dataclass(frozen=True)
class Event:
    """One normalised event in an agent session."""

    id: EventId
    session_id: str
    sequence: int
    kind: EventKind
    actor: Actor
    text: str
    provenance: Provenance
    timestamp: datetime | None = None
    tool: ToolCall | None = None
    usage: TokenUsage | None = None
    parent_id: EventId | None = None


@dataclass(frozen=True)
class UnrecognisedRecord:
    """A source record the adapter could not map, kept so coverage is honest."""

    line: int
    record_type: str


@dataclass(frozen=True)
class SessionTrace:
    """A whole session as normalised events, plus what could not be mapped."""

    session_id: str
    source_format: str
    events: tuple[Event, ...]
    unrecognised: tuple[UnrecognisedRecord, ...] = ()
    schema_version: str = EVENT_SCHEMA_VERSION

    @property
    def unrecognised_by_type(self) -> Mapping[str, int]:
        return dict(Counter(r.record_type for r in self.unrecognised))

    @property
    def coverage(self) -> float:
        """Share of source records that produced at least one event (1.0 when empty)."""
        mapped = len({e.provenance.record_id for e in self.events})
        total = mapped + len(self.unrecognised)
        return 1.0 if total == 0 else mapped / total

    def generated(self) -> tuple[Event, ...]:
        """Events the agent generated; user input and tool output are excluded."""
        return tuple(e for e in self.events if e.kind.is_generated)


def make_event_id(*parts: object) -> EventId:
    """Derive a stable event id from the parts that identify a source record.

    The id is the first 16 hex characters of a SHA-256 over the NUL-joined
    parts, so it is stable across runs, platforms and Python versions.
    """
    raw = "\0".join("" if p is None else str(p) for p in parts)
    return EventId(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16])
