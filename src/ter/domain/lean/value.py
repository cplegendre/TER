"""Outcome value (L3, TER-LEN-009): exploration, reasoning and validation
judged against what the requested outcome required.

L2 values edits and writes against the intent in force (TER-LEN-003). Here
the other work of a task is judged with repository evidence of what the
outcome required: the task's **expected change surface**
(:mod:`.surface`: the files the prompt names, their import neighbours and
their tests, plus the files the task edited) and the **evidence usage** of
each read (:mod:`.usage`). A task is a prompt and the work up to the next
one, so a recorded intent change (a new prompt) starts a new surface.

Every judgement has a value class, a published rule (``VALUE_RULES``) that
fixes its confidence, and the event ids it rests on. Below
:data:`~.model.UNCERTAIN_BELOW` a judgement is uncertain. Judgements are a
view for the reader: they do not change the activity classes or the
scorecard.

Value classes:

``required``
    The outcome needed it: it touched the change surface and the change or
    its check went on to use it.
``supporting``
    It fed the work without being on the surface (an off-surface file a
    later change used), or it was on the surface but nothing later used it,
    or the task changed nothing.
``no_value``
    Nothing on the surface and nothing later used it. Always uncertain:
    reading to rule a place out is legitimate, and the calibration of the
    change surface on real sessions showed tasks need files the import
    graph does not tie to them.
``unjudged``
    No repository evidence either way: it names no repository file, or no
    response follows it yet.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ..events import EventId, EventKind, ToolKind
from .grounding import RepositoryGrounding
from .model import UNCERTAIN_BELOW, Stage, Step
from .surface import ChangeSurface
from .usage import EvidenceUsage, FileNames, UsageStatus

__all__ = [
    "VALUE_RULES",
    "JudgedKind",
    "OutcomeValue",
    "Task",
    "ValueClass",
    "ValueJudgement",
    "ValueRule",
    "outcome_value",
    "tasks_of",
]


class ValueClass(StrEnum):
    REQUIRED = "required"
    SUPPORTING = "supporting"
    NO_VALUE = "no_value"
    UNJUDGED = "unjudged"


class JudgedKind(StrEnum):
    EXPLORATION = "exploration"
    REASONING = "reasoning"
    VALIDATION = "validation"


@dataclass(frozen=True)
class ValueRule:
    value: ValueClass
    confidence: float
    text: str


#: Every rule a judgement can rest on: its class, confidence and wording.
VALUE_RULES: Mapping[str, ValueRule] = {
    "explore.surface_used": ValueRule(
        ValueClass.REQUIRED,
        0.85,
        "Exploration of a file on the task's change surface (or one the task "
        "edited) that a later edit, command or check used.",
    ),
    "explore.off_surface_used": ValueRule(
        ValueClass.SUPPORTING,
        0.75,
        "Exploration of a file off the change surface that a later edit, "
        "command or check used.",
    ),
    "explore.surface_unused": ValueRule(
        ValueClass.SUPPORTING,
        0.6,
        "Exploration of a file on the change surface that no later edit, "
        "command or check used.",
    ),
    "explore.decision_only": ValueRule(
        ValueClass.SUPPORTING,
        0.6,
        "Exploration of a file off the change surface that only a later "
        "reasoning block or response named.",
    ),
    "explore.unused": ValueRule(
        ValueClass.NO_VALUE,
        0.65,
        "Exploration of files off the change surface that nothing later used, "
        "with a response after it. Uncertain: reading to rule a place out is "
        "legitimate.",
    ),
    "explore.no_change_used": ValueRule(
        ValueClass.SUPPORTING,
        0.6,
        "Exploration in a task that changed nothing (no change surface), used "
        "by a later event.",
    ),
    "explore.no_change_unused": ValueRule(
        ValueClass.NO_VALUE,
        0.55,
        "Exploration in a task that changed nothing, used by no later event.",
    ),
    "explore.no_evidence": ValueRule(
        ValueClass.UNJUDGED,
        0.0,
        "Exploration that names no repository file, or with no response after it yet.",
    ),
    "reasoning.surface_then_change": ValueRule(
        ValueClass.REQUIRED,
        0.75,
        "Reasoning that names a file or a distinctive symbol of the change "
        "surface, followed by an edit in the same task.",
    ),
    "reasoning.surface_no_change": ValueRule(
        ValueClass.SUPPORTING,
        0.6,
        "Reasoning that names a file of the change surface, with no edit "
        "after it in the task.",
    ),
    "reasoning.off_surface_used": ValueRule(
        ValueClass.SUPPORTING,
        0.6,
        "Reasoning that names only files off the change surface, at least one "
        "of which a later event reads or edits.",
    ),
    "reasoning.off_surface_unused": ValueRule(
        ValueClass.NO_VALUE,
        0.55,
        "Reasoning that names only files off the change surface that nothing "
        "later reads or edits.",
    ),
    "reasoning.no_evidence": ValueRule(
        ValueClass.UNJUDGED,
        0.0,
        "Reasoning that names no repository file, or in a task that changed nothing.",
    ),
    "validation.covers_surface": ValueRule(
        ValueClass.REQUIRED,
        0.85,
        "A check after an edit of the task that names a file of the change "
        "surface or one of its tests.",
    ),
    "validation.suite_after_change": ValueRule(
        ValueClass.REQUIRED,
        0.75,
        "A check after an edit of the task that names no repository file "
        "(the whole suite, a linter, a type checker).",
    ),
    "validation.off_surface": ValueRule(
        ValueClass.SUPPORTING,
        0.6,
        "A check after an edit of the task that names only files off the "
        "change surface.",
    ),
    "validation.baseline": ValueRule(
        ValueClass.SUPPORTING,
        0.6,
        "A check before the task's first edit: a baseline for the change.",
    ),
    "validation.no_new_change": ValueRule(
        ValueClass.NO_VALUE,
        0.55,
        "A check after the task's edits with no edit since the previous check "
        "of the task: it re-checks unchanged work.",
    ),
    "validation.no_change": ValueRule(
        ValueClass.SUPPORTING,
        0.55,
        "A check in a task that changed nothing.",
    ),
}


@dataclass(frozen=True)
class ValueJudgement:
    """The value of one exploration, reasoning or validation event."""

    event_id: EventId
    kind: JudgedKind
    rule: str
    #: Repository files the event touched or named.
    files: tuple[str, ...]
    #: The event, its result, the task's prompt and the events that used it.
    evidence: tuple[EventId, ...]

    @property
    def value(self) -> ValueClass:
        return VALUE_RULES[self.rule].value

    @property
    def confidence(self) -> float:
        return VALUE_RULES[self.rule].confidence

    @property
    def uncertain(self) -> bool:
        return self.confidence < UNCERTAIN_BELOW

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "value": self.value.value,
            "confidence": self.confidence,
            "uncertain": self.uncertain,
            "rule": self.rule,
            "files": list(self.files),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class OutcomeValue:
    judgements: tuple[ValueJudgement, ...] = ()

    def counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {
            k.value: {v.value: 0 for v in ValueClass} for k in JudgedKind
        }
        for j in self.judgements:
            out[j.kind.value][j.value.value] += 1
        return out

    def as_dict(self) -> dict[str, object]:
        return {
            "classes": [v.value for v in ValueClass],
            "rules": {
                k: {"value": r.value.value, "confidence": r.confidence, "text": r.text}
                for k, r in VALUE_RULES.items()
            },
            "summary": self.counts(),
            "uncertain": sum(
                j.uncertain and j.value is not ValueClass.UNJUDGED
                for j in self.judgements
            ),
            "judgements": [j.as_dict() for j in self.judgements],
        }


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Task:
    """A prompt and the steps up to the next one, with its change surface."""

    prompt: Step | None
    steps: tuple[Step, ...]
    surface: ChangeSurface | None
    #: Repository files the task edited.
    edited: frozenset[str]

    @property
    def required(self) -> frozenset[str]:
        """What the outcome required: the surface plus the files edited."""
        if self.surface is None:
            return frozenset()
        return self.surface.files | self.edited

    def anchors(self) -> tuple[EventId, ...]:
        """The task's prompt and the prompt that named its seeds."""
        ids: list[EventId] = []
        if self.surface is not None and self.surface.named_by is not None:
            ids.append(self.surface.named_by)
        if self.prompt is not None:
            ids.append(self.prompt.event_id)
        return tuple(dict.fromkeys(ids))


def tasks_of(
    steps: Sequence[Step],
    g: RepositoryGrounding,
    surfaces: Sequence[ChangeSurface],
) -> Iterator[Task]:
    """Every prompt segment (and the steps before the first prompt)."""
    by_prompt = {s.prompt: s for s in surfaces}
    prompt: Step | None = None
    members: list[Step] = []

    def close() -> Task:
        edited = frozenset(
            p
            for s in members
            if s.is_edit and s.paths and (p := g.repository_path(s.paths[0]))
        )
        key = prompt.event_id if prompt is not None else None
        return Task(prompt, tuple(members), by_prompt.get(key), edited)

    for step in steps:
        if step.kind is EventKind.PROMPT:
            if prompt is not None or members:
                yield close()
            prompt, members = step, []
            continue
        members.append(step)
    if prompt is not None or members:
        yield close()


# ---------------------------------------------------------------------------
# Judgements
# ---------------------------------------------------------------------------


@dataclass
class _Session:
    steps: Sequence[Step]
    g: RepositoryGrounding
    names: FileNames
    usage: EvidenceUsage
    completion_of: Mapping[int, Step]
    #: Repository path -> indexes of read and edit requests of it.
    touched: dict[str, list[int]]
    last_response: int

    def touched_after(self, path: str, index: int) -> int | None:
        return next((i for i in self.touched.get(path, ()) if i > index), None)


def _judge_exploration(s: _Session, task: Task, step: Step) -> ValueJudgement:
    result = s.completion_of.get(step.index)
    own: tuple[EventId, ...] = (step.event_id,) + (
        (result.event_id,) if result is not None else ()
    )
    end = result.index if result is not None else step.index
    pending = end >= s.last_response
    reads = s.usage.of(step.event_id)
    # path -> (used at all, used by a change, command or check, using events)
    found: dict[str, tuple[bool, bool, tuple[EventId, ...]]] = {}
    if reads:
        for read in reads:
            found[read.path] = (
                bool(read.uses),
                read.material,
                tuple(u.event_id for u in read.uses),
            )
        pending = all(r.status is UsageStatus.PENDING for r in reads)
    else:
        named = s.names.named(step.words) | s.names.named(
            result.words if result is not None else frozenset()
        )
        for path in sorted(named):
            hit = s.touched_after(path, end)
            ids = () if hit is None else (s.steps[hit].event_id,)
            found[path] = (hit is not None, hit is not None, ids)
    files = tuple(found)
    on = [found[p] for p in files if p in task.required]
    off = [found[p] for p in files if p not in task.required]
    used = any(u for u, _, _ in found.values())
    uses = tuple(dict.fromkeys(e for _, _, ids in found.values() for e in ids))
    if not files or (pending and not used):
        rule = "explore.no_evidence"
    elif task.surface is None:
        rule = "explore.no_change_used" if used else "explore.no_change_unused"
    elif on:
        material = any(m for _, m, _ in on)
        rule = "explore.surface_used" if material else "explore.surface_unused"
    elif any(m for _, m, _ in off):
        rule = "explore.off_surface_used"
    elif used:
        rule = "explore.decision_only"
    else:
        rule = "explore.unused"
    return ValueJudgement(
        step.event_id,
        JudgedKind.EXPLORATION,
        rule,
        files,
        (*own, *task.anchors(), *uses),
    )


def _judge_reasoning(s: _Session, task: Task, step: Step) -> ValueJudgement:
    files = tuple(sorted(s.names.named(step.words)))
    on = set(files) & task.required
    edits_after = [e for e in task.steps if e.is_edit and e.index > step.index]
    used: list[int] = []
    if files and task.surface is None:
        rule = "reasoning.no_evidence"
    elif not files:
        rule = "reasoning.no_evidence"
    elif on and edits_after:
        rule = "reasoning.surface_then_change"
        used = [edits_after[0].index]
    elif on:
        rule = "reasoning.surface_no_change"
    else:
        used = sorted(
            i for p in files if (i := s.touched_after(p, step.index)) is not None
        )
        rule = "reasoning.off_surface_used" if used else "reasoning.off_surface_unused"
    return ValueJudgement(
        step.event_id,
        JudgedKind.REASONING,
        rule,
        files,
        (step.event_id, *task.anchors(), *(s.steps[i].event_id for i in used)),
    )


def _judge_validation(
    s: _Session, task: Task, step: Step, since: Sequence[Step]
) -> ValueJudgement:
    result = s.completion_of.get(step.index)
    own = (step.event_id,) + ((result.event_id,) if result is not None else ())
    named = s.names.named(step.words)
    files = tuple(sorted(named))
    surface_tests = set(task.surface.tests) if task.surface is not None else set()
    before = [e for e in task.steps if e.is_edit and e.index < step.index]
    if task.surface is None:
        rule = "validation.no_change"
    elif not before:
        rule = "validation.baseline"
    elif not since:
        rule = "validation.no_new_change"
    elif named & (task.required | surface_tests):
        rule = "validation.covers_surface"
    elif not named:
        rule = "validation.suite_after_change"
    else:
        rule = "validation.off_surface"
    return ValueJudgement(
        step.event_id,
        JudgedKind.VALIDATION,
        rule,
        files,
        (*own, *task.anchors(), *(e.event_id for e in since)),
    )


def outcome_value(
    steps: Sequence[Step],
    g: RepositoryGrounding,
    surfaces: Sequence[ChangeSurface],
    usage: EvidenceUsage,
    completion_of: Mapping[int, Step],
    names: FileNames | None = None,
) -> OutcomeValue:
    """Judge every exploration, reasoning and validation event (TER-LEN-009)."""
    names = names if names is not None else FileNames(g)
    touched: dict[str, list[int]] = {}
    for step in steps:
        if (
            step.is_request
            and step.tool_kind
            in (ToolKind.FS_READ, ToolKind.FS_EDIT, ToolKind.FS_WRITE)
            and step.paths
            and (p := g.repository_path(step.paths[0])) is not None
        ):
            touched.setdefault(p, []).append(step.index)
    last_response = max(
        (s.index for s in steps if s.kind is EventKind.RESPONSE), default=-1
    )
    session = _Session(steps, g, names, usage, completion_of, touched, last_response)
    out: list[ValueJudgement] = []
    for task in tasks_of(steps, g, surfaces):
        since: list[Step] = []
        for step in task.steps:
            if step.is_edit:
                since.append(step)
            elif step.is_request and step.checks:
                out.append(_judge_validation(session, task, step, since))
                since = []
            elif step.is_request and step.stage is Stage.EXPLORE:
                out.append(_judge_exploration(session, task, step))
            elif step.kind is EventKind.REASONING:
                out.append(_judge_reasoning(session, task, step))
    return OutcomeValue(tuple(out))
