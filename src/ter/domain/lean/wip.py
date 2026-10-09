"""Work in progress inside an agent session (points 31, 32; TER-WIP-001).

Lean holds that work started and not finished hides problems and slows flow.
An agent's unfinished work is visible in its event stream, and
:class:`WipTracker` counts it after every event, in four kinds:

* **edits**: an ``fs.edit`` or ``fs.write`` request is open until the result
  of a validation run *requested after it* arrives (whatever the result: the
  edit has then been checked, and a failure becomes WIP of its own).
* **failures**: a validation run whose output reads as failed opens a
  failure for that check (its normalised command line). A later run of the
  *same* command that passes resolves it; a pass of a different command does
  not, because at L2 TER cannot tell which checks one command covers.
* **tasks**: the items of the latest to-do list (``plan.todo`` with a
  ``todos`` list) that are not ``completed``, plus every subagent handoff
  (``agent.handoff``) whose result has not arrived.
* **hypotheses**: an open line of inquiry. Each exploration request (a read,
  search, fetch or exploring shell command) opens a hypothesis about its
  subject (the file path, otherwise the tool kind and subject), unless one
  on that subject is already open. It is resolved when the agent acts on it
  (an edit or write of that path) or when the turn ends (a new prompt or a
  ``task.completed`` event): the agent has then drawn its conclusion.

Every :meth:`WipTracker.add` is O(1) amortised: each item is opened once and
closed at most once, and closing all hypotheses at a turn end, or all edits
before a validation run, pays only for items opened earlier. The tracker
records one :class:`WipSample` per accepted event, so a report can show WIP
over the session and its peak.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from ..events import Event, EventId, EventKind, ToolKind
from .model import Outcome, ShellIntent, Step

__all__ = ["WipKind", "WipReport", "WipSample", "WipTracker"]


class WipKind(StrEnum):
    """The four kinds of unresolved work TER counts."""

    HYPOTHESES = "hypotheses"
    TASKS = "tasks"
    EDITS = "edits"
    FAILURES = "failures"

    @property
    def label(self) -> str:
        return self.value.capitalize()


_EXPLORE_KINDS = frozenset({ToolKind.FS_READ, ToolKind.FS_SEARCH, ToolKind.NET_FETCH})
_EDIT_KINDS = frozenset({ToolKind.FS_EDIT, ToolKind.FS_WRITE})


@dataclass(frozen=True)
class WipSample:
    """Unresolved work right after one event."""

    event_id: EventId
    hypotheses: int
    tasks: int
    edits: int
    failures: int

    @property
    def total(self) -> int:
        return self.hypotheses + self.tasks + self.edits + self.failures

    def count(self, kind: WipKind) -> int:
        return {
            WipKind.HYPOTHESES: self.hypotheses,
            WipKind.TASKS: self.tasks,
            WipKind.EDITS: self.edits,
            WipKind.FAILURES: self.failures,
        }[kind]

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            **{k.value: self.count(k) for k in WipKind},
            "total": self.total,
        }


@dataclass(frozen=True)
class WipReport:
    """WIP over the session: every sample, the peak and what is still open.

    ``peak`` is the first sample with the largest total; ``peak_by_kind`` the
    largest count of each kind on its own. ``open_at_end`` cites the event
    that opened each item still unresolved after the last event.
    """

    samples: tuple[WipSample, ...]
    peak: WipSample | None
    peak_by_kind: tuple[tuple[WipKind, int], ...]
    open_at_end: tuple[tuple[WipKind, tuple[EventId, ...]], ...]
    edits_opened: int
    edits_validated: int

    @property
    def final(self) -> WipSample | None:
        return self.samples[-1] if self.samples else None

    def peak_of(self, kind: WipKind) -> int:
        return dict(self.peak_by_kind).get(kind, 0)

    def still_open(self, kind: WipKind) -> tuple[EventId, ...]:
        return dict(self.open_at_end).get(kind, ())

    def as_dict(self) -> dict[str, object]:
        final = self.final
        return {
            "kinds": [k.value for k in WipKind],
            "series": [
                [s.event_id, *(s.count(k) for k in WipKind)] for s in self.samples
            ],
            "peak": None if self.peak is None else self.peak.as_dict(),
            "peak_by_kind": {k.value: n for k, n in self.peak_by_kind},
            "final": None if final is None else final.as_dict(),
            "open_at_end": {k.value: list(ids) for k, ids in self.open_at_end},
            "edits_opened": self.edits_opened,
            "edits_validated": self.edits_validated,
        }


@dataclass
class WipTracker:
    """Counts unresolved work, one event at a time (a fold; see module doc)."""

    _hypotheses: dict[str, EventId] = field(default_factory=dict)
    # Open edits in request order: event id -> step index of the request.
    _edits: dict[EventId, int] = field(default_factory=dict)
    _failures: dict[str, EventId] = field(default_factory=dict)
    _handoffs: dict[int, EventId] = field(default_factory=dict)
    _todos: int = 0
    _todo_event: EventId | None = None
    _edits_opened: int = 0
    _edits_validated: int = 0
    _samples: list[WipSample] = field(default_factory=list)
    _peak: WipSample | None = None
    _peaks: dict[WipKind, int] = field(
        default_factory=lambda: dict.fromkeys(WipKind, 0)
    )

    def add(self, event: Event, step: Step | None) -> WipSample:
        """Fold one accepted event; ``step`` is None for lifecycle events."""
        todos: object = None
        if event.kind is EventKind.TOOL_REQUESTED and event.tool is not None:
            todos = event.tool.arguments.get("todos")
        return self.observe(event.id, event.kind, step, todos)

    def observe(
        self,
        event_id: EventId,
        kind: EventKind,
        step: Step | None,
        todos: object = None,
    ) -> WipSample:
        """Fold one event given its id, kind, step and any to-do list it carries."""
        if kind is EventKind.TASK_COMPLETED or kind is EventKind.PROMPT:
            self._hypotheses.clear()
        if step is not None:
            if step.is_request:
                self._on_request(step, todos)
            elif step.is_completion:
                self._on_completion(step)
        return self._sample(event_id)

    def _on_request(self, step: Step, todos: object) -> None:
        kind = step.tool_kind
        if kind in _EDIT_KINDS:
            self._edits[step.event_id] = step.index
            self._edits_opened += 1
            for path in step.paths:
                self._hypotheses.pop(path, None)
        elif kind in _EXPLORE_KINDS or step.shell is ShellIntent.EXPLORE:
            key = step.paths[0] if step.paths else f"{kind}:{step.subject}"
            self._hypotheses.setdefault(key, step.event_id)
        elif kind is ToolKind.AGENT_HANDOFF:
            self._handoffs[step.index] = step.event_id
        elif kind is ToolKind.PLAN and isinstance(todos, list):
            self._todos = _open_todos(todos)
            self._todo_event = step.event_id if self._todos else None

    def _on_completion(self, step: Step) -> None:
        request = step.request_index
        if request is None:
            return
        if step.tool_kind is ToolKind.AGENT_HANDOFF:
            self._handoffs.pop(request, None)
        if step.shell is not ShellIntent.VALIDATE:
            return
        # The run checked every edit requested before it.
        while self._edits:
            first = next(iter(self._edits))
            if self._edits[first] >= request:
                break
            del self._edits[first]
            self._edits_validated += 1
        check = step.command or step.subject
        if step.outcome is Outcome.FAILED:
            self._failures.setdefault(check, step.event_id)
        elif step.outcome is Outcome.PASSED:
            self._failures.pop(check, None)

    def _sample(self, event_id: EventId) -> WipSample:
        sample = WipSample(
            event_id=event_id,
            hypotheses=len(self._hypotheses),
            tasks=self._todos + len(self._handoffs),
            edits=len(self._edits),
            failures=len(self._failures),
        )
        self._samples.append(sample)
        if self._peak is None or sample.total > self._peak.total:
            self._peak = sample
        for kind in WipKind:
            self._peaks[kind] = max(self._peaks[kind], sample.count(kind))
        return sample

    def report(self) -> WipReport:
        tasks = (*((self._todo_event,) if self._todo_event else ()),)
        tasks += tuple(self._handoffs.values())
        return WipReport(
            samples=tuple(self._samples),
            peak=self._peak,
            peak_by_kind=tuple((k, self._peaks[k]) for k in WipKind),
            open_at_end=(
                (WipKind.HYPOTHESES, tuple(self._hypotheses.values())),
                (WipKind.TASKS, tasks),
                (WipKind.EDITS, tuple(self._edits)),
                (WipKind.FAILURES, tuple(self._failures.values())),
            ),
            edits_opened=self._edits_opened,
            edits_validated=self._edits_validated,
        )

    @classmethod
    def of_steps(cls, steps: Iterable[Step]) -> WipReport:
        """WIP from steps alone, without the events they came from.

        Lifecycle events and to-do lists are not in steps, so turn ends are
        known only from prompts and to-do items are not counted. Use the
        tracker inside :class:`~.analysis.LeanAnalyser` for the full count.
        """
        tracker = cls()
        for step in steps:
            tracker.observe(step.event_id, step.kind, step)
        return tracker.report()


def _open_todos(todos: list[object]) -> int:
    """To-do items not marked completed (an item without a status is open)."""
    return sum(
        1
        for item in todos
        if not (isinstance(item, Mapping) and item.get("status") == "completed")
    )
