"""Incremental analysis of a ``ter.event`` stream (maturity level L1, Observed).

:class:`AnalysisEngine` folds events one at a time into running state and can
be asked for a :class:`StreamReport` at any point. Batch analysis is the same
fold over a whole trace (:func:`analyse_batch`), so a session analysed live,
event by event, and a session analysed afterwards produce identical reports by
construction (TER-ANL-010).

Every step is O(1) amortised in the length of the session: the engine keeps
counters, hash sets and an insertion-ordered map of open tool requests, and
never re-reads earlier events. The cost of one step is linear only in the size
of the event itself (its text is tokenised and its arguments canonicalised).

Events are identified by :attr:`Event.id`. An event whose id was already
applied is discarded and leaves the state untouched (TER-OBS-004), so hooks
that fire twice, retried deliveries and replays of an append-only log are all
harmless.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeVar

from .events import Actor, Event, EventId, EventKind, TokenUsage, ToolKind
from .lean.analysis import LeanAnalyser, LeanAnalysis, TerMeasure
from .lean.detectors import DEFAULT_REGISTRY, DetectorRegistry
from .lean.grounding import RepositoryGrounding
from .lean.intent import (
    DEFAULT_INTENT_CONFIG,
    LEXICAL_ALIGNMENT,
    AlignmentScorer,
    IntentConfig,
)

__all__ = [
    "AnalysisEngine",
    "EventClass",
    "Signal",
    "Signals",
    "StreamReport",
    "TimelineRow",
    "TokenCounter",
    "analyse_batch",
    "canonical_arguments",
    "explain_batch",
]

#: Argument keys that name the file a read targets, across tool vocabularies.
_PATH_KEYS = ("file_path", "notebook_path", "path")

_EDIT_KINDS = frozenset({ToolKind.FS_EDIT, ToolKind.FS_WRITE})

_K = TypeVar("_K", bound=StrEnum)


class TokenCounter(Protocol):
    """What the engine needs from a tokenizer.

    Structurally identical to :class:`ter.ports.driven.Tokenizer`, which the
    domain may not import: any tokenizer adapter satisfies both.
    """

    @property
    def name(self) -> str: ...

    @property
    def exact(self) -> bool: ...

    def count(self, text: str) -> int: ...


class EventClass(StrEnum):
    """Who an event's tokens belong to, for the generated/user/tool split."""

    GENERATED = "generated"
    USER = "user"
    TOOL = "tool"
    LIFECYCLE = "lifecycle"

    @classmethod
    def of(cls, kind: EventKind) -> EventClass:
        if kind.is_generated:
            return cls.GENERATED
        if kind is EventKind.PROMPT:
            return cls.USER
        if kind.is_lifecycle:
            return cls.LIFECYCLE
        return cls.TOOL


class Signal(StrEnum):
    """Something noteworthy that one event revealed. Values are stable ids."""

    DUPLICATE_EVENT = "event.duplicate"
    DUPLICATE_TOOL_CALL = "tool.duplicate_call"
    REPEATED_READ = "fs.repeated_read"
    ORPHAN_RESULT = "tool.orphan_result"
    UNVALIDATED_EDIT = "fs.unvalidated_edit"


@dataclass(frozen=True)
class Signals:
    """The engine's answer to one :meth:`AnalysisEngine.apply`.

    ``accepted`` is False only for an event whose id was already applied; such
    an event raises :attr:`Signal.DUPLICATE_EVENT` and nothing else.
    """

    event_id: EventId
    accepted: bool
    raised: tuple[Signal, ...] = ()

    def __contains__(self, signal: object) -> bool:
        return signal in self.raised


@dataclass(frozen=True)
class TimelineRow:
    """One accepted event as a row of the session timeline."""

    index: int
    event_id: EventId
    kind: EventKind
    actor: Actor
    tool_kind: ToolKind | None
    native_name: str | None
    tokens: int
    output_tokens: int | None
    signals: tuple[Signal, ...]


@dataclass(frozen=True)
class StreamReport:
    """L1 observables, computed from the event stream alone.

    Counts are tuples of ``(key, count)`` pairs sorted by key, with zero counts
    left out, so reports compare and hash by value.
    """

    session_id: str | None
    tokenizer: str
    tokens_exact: bool
    total_events: int
    by_kind: tuple[tuple[EventKind, int], ...]
    by_tool: tuple[tuple[ToolKind, int], ...]
    by_class: tuple[tuple[EventClass, int], ...]
    tokens_by_class: tuple[tuple[EventClass, int], ...]
    usage: TokenUsage
    duplicate_tool_calls: tuple[EventId, ...]
    repeated_reads: tuple[tuple[str, int], ...]
    orphan_results: tuple[EventId, ...]
    open_requests: tuple[EventId, ...]
    edits_since_validation: int
    peak_edits_without_validation: int
    timeline: tuple[TimelineRow, ...]
    #: What the source cannot report (``SessionTrace.usage_limits``).
    usage_limits: tuple[str, ...] = ()

    def count(self, key: EventKind | ToolKind | EventClass) -> int:
        """Return the count for an event kind, tool kind or event class."""
        pairs: tuple[tuple[StrEnum, int], ...]
        if isinstance(key, EventKind):
            pairs = self.by_kind
        elif isinstance(key, ToolKind):
            pairs = self.by_tool
        else:
            pairs = self.by_class
        return dict(pairs).get(key, 0)

    def tokens(self, event_class: EventClass) -> int:
        """Return the estimated text tokens for one event class."""
        return dict(self.tokens_by_class).get(event_class, 0)

    @property
    def generated_events(self) -> int:
        return self.count(EventClass.GENERATED)

    @property
    def user_events(self) -> int:
        return self.count(EventClass.USER)

    @property
    def tool_events(self) -> int:
        return self.count(EventClass.TOOL)

    @property
    def lifecycle_events(self) -> int:
        return self.count(EventClass.LIFECYCLE)

    @property
    def repeated_read_count(self) -> int:
        """Reads beyond the first of each path."""
        return sum(n - 1 for _, n in self.repeated_reads)

    def as_dict(self) -> dict[str, object]:
        """A JSON-ready projection, stable enough to snapshot."""
        out: dict[str, object] = {
            "session_id": self.session_id,
            "tokenizer": self.tokenizer,
            "tokens_exact": self.tokens_exact,
            "total_events": self.total_events,
            "by_kind": {k.value: n for k, n in self.by_kind},
            "by_tool": {k.value: n for k, n in self.by_tool},
            "by_class": {k.value: n for k, n in self.by_class},
            "tokens_by_class": {k.value: n for k, n in self.tokens_by_class},
            "usage": {
                "input": self.usage.input_tokens,
                "output": self.usage.output_tokens,
                "cache_creation": self.usage.cache_creation_tokens,
                "cache_read": self.usage.cache_read_tokens,
            },
            "duplicate_tool_calls": list(self.duplicate_tool_calls),
            "repeated_reads": {path: n for path, n in self.repeated_reads},
            "orphan_results": list(self.orphan_results),
            "open_requests": list(self.open_requests),
            "edits_since_validation": self.edits_since_validation,
            "peak_edits_without_validation": self.peak_edits_without_validation,
            "timeline": [
                [
                    r.index,
                    r.event_id,
                    r.kind.value,
                    r.actor.value,
                    r.tool_kind.value if r.tool_kind else None,
                    r.native_name,
                    r.tokens,
                    r.output_tokens,
                    [s.value for s in r.signals],
                ]
                for r in self.timeline
            ],
        }
        if self.usage_limits:
            out["usage_limits"] = list(self.usage_limits)
        return out


def canonical_arguments(arguments: Mapping[str, object]) -> str:
    """Serialise tool arguments so equal calls compare equal regardless of key order."""
    return json.dumps(
        arguments,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _call_key(kind: ToolKind, arguments: Mapping[str, object]) -> str:
    raw = f"{kind.value}\0{canonical_arguments(arguments)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_path(arguments: Mapping[str, object]) -> str | None:
    for key in _PATH_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _sorted_counts(counter: Counter[_K]) -> tuple[tuple[_K, int], ...]:
    return tuple(sorted(((k, n) for k, n in counter.items() if n), key=lambda p: p[0]))


class AnalysisEngine:
    """Running L1 analysis of one session's event stream.

    Apply events with :meth:`apply`; read the analysis with :meth:`snapshot`.
    The engine accepts events of one session only: the first accepted event
    fixes :attr:`session_id`, and an event from another session is rejected
    with :class:`ValueError` before it touches any state.
    """

    def __init__(self, tokenizer: TokenCounter) -> None:
        self._tokenizer = tokenizer
        self._session_id: str | None = None
        self._seen: set[EventId] = set()
        self._by_kind: Counter[EventKind] = Counter()
        self._by_tool: Counter[ToolKind] = Counter()
        self._by_class: Counter[EventClass] = Counter()
        self._tokens: Counter[EventClass] = Counter()
        self._input = self._output = self._cache_create = self._cache_read = 0
        self._call_keys: set[str] = set()
        self._duplicates: list[EventId] = []
        self._reads: Counter[str] = Counter()
        self._requested_calls: set[str] = set()
        # Insertion-ordered, so open requests list in request order; keyed by
        # call id, with a synthetic key for requests that carry none.
        self._open: dict[str, EventId] = {}
        self._orphans: list[EventId] = []
        self._edits_since_validation = 0
        self._peak_edits = 0
        self._timeline: list[TimelineRow] = []
        self._lean = LeanAnalyser()

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def __len__(self) -> int:
        """Number of accepted events."""
        return len(self._timeline)

    def __contains__(self, event_id: object) -> bool:
        return event_id in self._seen

    def apply(self, event: Event) -> Signals:
        """Fold one event into the analysis and report what it revealed."""
        if event.id in self._seen:
            return Signals(event.id, accepted=False, raised=(Signal.DUPLICATE_EVENT,))
        if self._session_id is not None and event.session_id != self._session_id:
            raise ValueError(
                f"Event {event.id} belongs to session {event.session_id!r}, "
                f"not {self._session_id!r}"
            )
        self._session_id = event.session_id
        self._seen.add(event.id)

        event_class = EventClass.of(event.kind)
        tokens = self._tokenizer.count(event.text)
        self._by_kind[event.kind] += 1
        self._by_class[event_class] += 1
        self._tokens[event_class] += tokens
        if event.usage is not None:
            self._input += event.usage.input_tokens
            self._output += event.usage.output_tokens
            self._cache_create += event.usage.cache_creation_tokens
            self._cache_read += event.usage.cache_read_tokens

        raised: list[Signal] = []
        if event.kind is EventKind.TOOL_REQUESTED and event.tool is not None:
            raised.extend(self._on_request(event))
        elif event.kind is EventKind.TOOL_COMPLETED:
            raised.extend(self._on_completion(event))

        signals = tuple(raised)
        self._lean.add(event, tokens)
        self._timeline.append(
            TimelineRow(
                index=len(self._timeline),
                event_id=event.id,
                kind=event.kind,
                actor=event.actor,
                tool_kind=event.tool.kind if event.tool else None,
                native_name=event.tool.native_name if event.tool else None,
                tokens=tokens,
                output_tokens=event.usage.output_tokens if event.usage else None,
                signals=signals,
            )
        )
        return Signals(event.id, accepted=True, raised=signals)

    def apply_all(self, events: Iterable[Event]) -> None:
        """Apply events in order, discarding the per-event signals."""
        for event in events:
            self.apply(event)

    def _on_request(self, event: Event) -> list[Signal]:
        assert event.tool is not None
        tool = event.tool
        raised: list[Signal] = []
        self._by_tool[tool.kind] += 1

        key = _call_key(tool.kind, tool.arguments)
        if key in self._call_keys:
            self._duplicates.append(event.id)
            raised.append(Signal.DUPLICATE_TOOL_CALL)
        else:
            self._call_keys.add(key)

        if tool.kind is ToolKind.FS_READ:
            path = _read_path(tool.arguments)
            if path is not None:
                self._reads[path] += 1
                if self._reads[path] > 1:
                    raised.append(Signal.REPEATED_READ)

        if tool.kind in _EDIT_KINDS:
            self._edits_since_validation += 1
            self._peak_edits = max(self._peak_edits, self._edits_since_validation)
            if self._edits_since_validation > 1:
                raised.append(Signal.UNVALIDATED_EDIT)
        elif tool.kind is ToolKind.EXEC_SHELL:
            self._edits_since_validation = 0

        call = tool.call_id or f"\0{event.id}"
        self._requested_calls.add(call)
        self._open[call] = event.id
        return raised

    def _on_completion(self, event: Event) -> list[Signal]:
        call = event.tool.call_id if event.tool else None
        if call is None or call not in self._requested_calls:
            self._orphans.append(event.id)
            return [Signal.ORPHAN_RESULT]
        self._open.pop(call, None)
        return []

    def snapshot(self) -> StreamReport:
        """The analysis so far. Cost is linear in the timeline, not in history."""
        return StreamReport(
            session_id=self._session_id,
            tokenizer=self._tokenizer.name,
            tokens_exact=self._tokenizer.exact,
            total_events=len(self._timeline),
            by_kind=_sorted_counts(self._by_kind),
            by_tool=_sorted_counts(self._by_tool),
            by_class=_sorted_counts(self._by_class),
            tokens_by_class=_sorted_counts(self._tokens),
            usage=TokenUsage(
                input_tokens=self._input,
                output_tokens=self._output,
                cache_creation_tokens=self._cache_create,
                cache_read_tokens=self._cache_read,
            ),
            duplicate_tool_calls=tuple(self._duplicates),
            repeated_reads=tuple(
                sorted((p, n) for p, n in self._reads.items() if n > 1)
            ),
            orphan_results=tuple(self._orphans),
            open_requests=tuple(self._open.values()),
            edits_since_validation=self._edits_since_validation,
            peak_edits_without_validation=self._peak_edits,
            timeline=tuple(self._timeline),
        )

    def explain(
        self,
        *,
        ter: TerMeasure | None = None,
        registry: DetectorRegistry = DEFAULT_REGISTRY,
        alignment: AlignmentScorer = LEXICAL_ALIGNMENT,
        intent_config: IntentConfig = DEFAULT_INTENT_CONFIG,
        repository: RepositoryGrounding | None = None,
    ) -> LeanAnalysis:
        """The L2 explanation so far: findings, value stream, scorecard, graph,
        intent timeline.

        Steps and intent facts are folded in :meth:`apply` in O(1) amortised
        time; detectors and alignment run here, over the session so far, in
        time linear in its length. ``repository`` grounds the analysis on
        repository evidence computed beforehand (L3).
        """
        return self._lean.analysis(
            ter=ter,
            registry=registry,
            alignment=alignment,
            intent_config=intent_config,
            repository=repository,
        )


def analyse_batch(events: Iterable[Event], tokenizer: TokenCounter) -> StreamReport:
    """Analyse a whole stream at once: exactly the fold of :meth:`AnalysisEngine.apply`."""
    engine = AnalysisEngine(tokenizer)
    engine.apply_all(events)
    return engine.snapshot()


def explain_batch(
    events: Iterable[Event],
    tokenizer: TokenCounter,
    *,
    ter: TerMeasure | None = None,
    registry: DetectorRegistry = DEFAULT_REGISTRY,
    alignment: AlignmentScorer = LEXICAL_ALIGNMENT,
    intent_config: IntentConfig = DEFAULT_INTENT_CONFIG,
    repository: RepositoryGrounding | None = None,
) -> LeanAnalysis:
    """Explain a whole stream at once: the L2 view of the same fold as :func:`analyse_batch`."""
    engine = AnalysisEngine(tokenizer)
    engine.apply_all(events)
    return engine.explain(
        ter=ter,
        registry=registry,
        alignment=alignment,
        intent_config=intent_config,
        repository=repository,
    )
