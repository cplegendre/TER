"""Model routing (L3): routing profiles, task classes and the router policy.

TER references language models only through the **role names** of the
active routing profile (TER-RTE-001): ``explore``, ``implement``, ``review``,
``escalate`` and whatever else a profile defines. A profile is data
(``ter/data/routing_profiles/*.json``, read through the ``RoutingProfiles``
port); which provider and model a role means is the profile's business, so
a model replaced in the profile changes no code and no policy.

The router is **advisory** at L3. It reads a recorded session, classifies
each task (TER-RTE-005), picks a role for it from that class, and escalates
the task to a higher role only when a detector signal with evidence supports
it (TER-RTE-003). Every escalation produces exactly one ``route.escalated``
event (TER-RTE-002). The decisions and events feed analysis and the A3's
recommendations; nothing here reaches a live session (TER-INT-001).

Every class is read from structure (files named, edited and read; import
links; checks run; failure signatures), never from token counts. The rules
are published in :data:`CLASSIFICATION_RULES` and ``docs/ter4/l3-grounded.md``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath

from .events import Actor, Event, EventId, EventKind, Provenance, make_event_id
from .lean.detectors import SessionView
from .lean.grounding import RepositoryGrounding
from .lean.intent import IntentRelation
from .lean.model import Finding, Stage, Step
from .lean.surface import ChangeSurface, EditPlacement, SeedBasis, change_surfaces
from .repository import is_test_module

__all__ = [
    "CLASSIFICATION_RULES",
    "ClassEvidence",
    "Dimension",
    "Level",
    "ModelBinding",
    "RouteDecision",
    "RouteEscalation",
    "RoutingPlan",
    "RoutingProfile",
    "RoutingProfileError",
    "RoutingSignal",
    "Scope",
    "TaskClassification",
    "TaskKind",
    "UnknownRoleError",
    "ValidationNeed",
    "classify_tasks",
    "escalation_event",
    "route_session",
]


# ---------------------------------------------------------------------------
# Routing profiles (TER-RTE-001)
# ---------------------------------------------------------------------------


class RoutingProfileError(ValueError):
    """A routing profile that is malformed or names a role it does not define."""


class UnknownRoleError(KeyError):
    """A role name the active routing profile does not define."""


class TaskKind(StrEnum):
    """What a task does, structurally: the key a profile maps to a role."""

    #: No edit and no check: reading, searching, answering.
    READ_ONLY = "read_only"
    #: Runs checks but edits nothing.
    VALIDATE = "validate"
    #: Edits or writes at least one file.
    CHANGE = "change"


@dataclass(frozen=True)
class ModelBinding:
    """What a role means in one profile: a provider and its model id."""

    provider: str
    model: str

    def as_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "model": self.model}


@dataclass(frozen=True)
class RoutingProfile:
    """Roles, what each task kind starts on, and where each role escalates.

    ``escalate_on`` names the detectors whose confident findings count as a
    signal that supports escalation; any other finding never moves a task.
    """

    name: str
    roles: Mapping[str, ModelBinding]
    task_roles: Mapping[TaskKind, str]
    escalation: Mapping[str, str]
    escalate_on: frozenset[str]
    description: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise RoutingProfileError("A routing profile needs a name")
        if not self.roles:
            raise RoutingProfileError(f"Profile {self.name!r} defines no roles")
        missing = set(TaskKind) - set(self.task_roles)
        if missing:
            raise RoutingProfileError(
                f"Profile {self.name!r} gives no role to task kind(s) "
                + ", ".join(sorted(k.value for k in missing))
            )
        named = {*self.task_roles.values(), *self.escalation, *self.escalation.values()}
        unknown = sorted(r for r in named if r not in self.roles)
        if unknown:
            raise RoutingProfileError(
                f"Profile {self.name!r} names undefined role(s) {', '.join(unknown)}"
            )
        for start in self.escalation:
            seen = {start}
            role = self.escalation.get(start)
            while role is not None:
                if role in seen:
                    raise RoutingProfileError(
                        f"Profile {self.name!r} escalates in a cycle through {role!r}"
                    )
                seen.add(role)
                role = self.escalation.get(role)

    def binding(self, role: str) -> ModelBinding:
        """The provider and model a role names (raises :class:`UnknownRoleError`)."""
        try:
            return self.roles[role]
        except KeyError:
            raise UnknownRoleError(
                f"Role {role!r} is not defined in routing profile {self.name!r}"
            ) from None

    def role_for(self, kind: TaskKind) -> str:
        return self.task_roles[kind]

    def escalates_to(self, role: str) -> str | None:
        return self.escalation.get(role)

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "roles": {r: b.as_dict() for r, b in self.roles.items()},
            "task_roles": {k.value: r for k, r in self.task_roles.items()},
            "escalation": dict(self.escalation),
            "escalate_on": sorted(self.escalate_on),
        }


# ---------------------------------------------------------------------------
# Task classification (TER-RTE-005)
# ---------------------------------------------------------------------------


class Dimension(StrEnum):
    COMPLEXITY = "complexity"
    AMBIGUITY = "ambiguity"
    RISK = "risk"
    SCOPE = "repository_scope"
    VALIDATION = "validation_needs"


class Level(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Scope(StrEnum):
    NONE = "none"
    FILE = "file"
    DIRECTORY = "directory"
    REPOSITORY = "repository"


class ValidationNeed(StrEnum):
    #: Nothing changed, so nothing needs checking.
    NONE = "none"
    #: Only documentation or configuration changed: a lint or build check.
    CHECK = "check"
    #: Source changed: tests must run.
    TESTS = "tests"


#: The published rule behind each dimension (documented in l3-grounded.md).
CLASSIFICATION_RULES: Mapping[Dimension, str] = {
    Dimension.COMPLEXITY: (
        "Distinct files the task edits or writes: 0-1 low, 2-3 medium, 4 or "
        "more high; one level higher (at most high) when its checks fail with "
        "2 or more distinct failure signatures."
    ),
    Dimension.AMBIGUITY: (
        "From the prompt: low when it names a file (a path or file name, or "
        "with repository evidence a file, module or distinctive symbol of the "
        "repository); medium when it names none but refines or acknowledges "
        "an earlier intent, or asks no question; high when it names no file "
        "and asks a question, or the task has no prompt."
    ),
    Dimension.RISK: (
        "High when the task edits a build, dependency or CI file, runs a "
        "destructive shell command, or (with repository evidence) edits a "
        "file outside its change surface with no import link to it, breaks "
        "an architecture contract, or edits a file 3 or more repository "
        "files import; medium when it edits any other non-test source file; "
        "low otherwise (no edits, or tests and documentation only)."
    ),
    Dimension.SCOPE: (
        "The files the task edits (or, when it edits none, reads or "
        "searches), as repository paths when repository evidence is given: "
        "none, one file, one directory, or several directories (repository)."
    ),
    Dimension.VALIDATION: (
        "None when the task edits nothing; check when it edits only "
        "documentation or configuration; tests when it edits source, citing "
        "with repository evidence the test modules of its change surface."
    ),
}


@dataclass(frozen=True)
class ClassEvidence:
    """One dimension's class and the events and rule it rests on."""

    dimension: Dimension
    value: str
    reason: str
    events: tuple[EventId, ...]

    @property
    def rule(self) -> str:
        return CLASSIFICATION_RULES[self.dimension]

    def as_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension.value,
            "value": self.value,
            "reason": self.reason,
            "events": list(self.events),
            "rule": self.rule,
        }


@dataclass(frozen=True)
class TaskClassification:
    """A task (a prompt and the steps up to the next prompt), classified."""

    task: int
    prompt: EventId | None
    #: Index of the first and last step of the task.
    first: int
    last: int
    kind: TaskKind
    complexity: Level
    ambiguity: Level
    risk: Level
    scope: Scope
    validation: ValidationNeed
    evidence: tuple[ClassEvidence, ...]
    grounded: bool = False

    def of(self, dimension: Dimension) -> ClassEvidence:
        return next(e for e in self.evidence if e.dimension is dimension)

    def as_dict(self) -> dict[str, object]:
        return {
            "task": self.task,
            "prompt": self.prompt,
            "steps": [self.first, self.last],
            "kind": self.kind.value,
            "grounded": self.grounded,
            "classes": {e.dimension.value: e.value for e in self.evidence},
            "evidence": [e.as_dict() for e in self.evidence],
        }


_BUILD_FILES = re.compile(
    r"(^|/)(pyproject\.toml|setup\.py|setup\.cfg|requirements[^/]*\.txt|"
    r"poetry\.lock|uv\.lock|Pipfile(\.lock)?|package\.json|package-lock\.json|"
    r"pnpm-lock\.yaml|yarn\.lock|Cargo\.toml|Cargo\.lock|go\.mod|go\.sum|"
    r"Dockerfile|docker-compose\.ya?ml|Makefile|tox\.ini|noxfile\.py|"
    r"\.pre-commit-config\.yaml)$"
    r"|(^|/)\.github/workflows/|(^|/)\.gitlab-ci\.yml$|(^|/)migrations/",
)
_DESTRUCTIVE = re.compile(
    r"\brm\s+-[a-z]*r|\bgit\s+(reset\s+--hard|push\s+(-f|--force)|clean\s+-[a-z]*f)"
    r"|\bdrop\s+(table|database)\b",
    re.IGNORECASE,
)
_SOURCE_SUFFIXES = frozenset(
    {
        ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".svelte",
        ".vue", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cc", ".cpp", ".hpp",
        ".cs", ".rb", ".php", ".swift", ".scala", ".sh", ".sql",
    }
)  # fmt: skip
_FILE_WORD = re.compile(r"[\w./-]*\w\.[A-Za-z]{1,5}\b|\b[\w-]+/[\w./-]+")
_CONTINUES = (IntentRelation.REFINED, IntentRelation.ACKNOWLEDGED)
#: Importers at which an edited file's blast radius makes a task high risk.
WIDE_IMPORTERS = 3


def _is_source(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in _SOURCE_SUFFIXES


@dataclass(frozen=True)
class _Task:
    index: int
    prompt: Step | None
    steps: tuple[Step, ...]


def _segments(view: SessionView) -> list[_Task]:
    out: list[_Task] = []
    current: list[Step] = []
    prompt: Step | None = None
    for step in view.steps:
        if step.kind is EventKind.PROMPT:
            if current:
                out.append(_Task(len(out), prompt, tuple(current)))
            prompt, current = step, [step]
            continue
        current.append(step)
    if current:
        out.append(_Task(len(out), prompt, tuple(current)))
    return out


def _level_up(level: Level) -> Level:
    return Level.HIGH if level is not Level.LOW else Level.MEDIUM


def _paths(g: RepositoryGrounding | None, steps: Iterable[Step]) -> dict[str, Step]:
    """Distinct target paths of ``steps`` (repository paths when grounded),
    each with the first step that named it."""
    out: dict[str, Step] = {}
    for step in steps:
        if not step.paths:
            continue
        raw = step.paths[0]
        path = raw if g is None else g.repository_path(raw)
        if path is not None and path not in out:
            out[path] = step
    return out


def _complexity(edited: Mapping[str, Step], task: _Task) -> ClassEvidence:
    n = len(edited)
    level = Level.LOW if n <= 1 else Level.MEDIUM if n <= 3 else Level.HIGH
    reason = f"edits {n} file{'s' if n != 1 else ''}"
    events = [s.event_id for s in edited.values()]
    failures = {
        s.failure_signature: s for s in task.steps if s.failure_signature is not None
    }
    if len(failures) >= 2:
        level = _level_up(level)
        reason += f"; checks failed with {len(failures)} distinct signatures"
        events += [s.event_id for s in failures.values()]
    return ClassEvidence(Dimension.COMPLEXITY, level.value, reason, tuple(events))


def _ambiguity(
    task: _Task,
    surface: ChangeSurface | None,
    relation: Mapping[EventId, IntentRelation],
) -> ClassEvidence:
    prompt = task.prompt
    if prompt is None:
        return ClassEvidence(
            Dimension.AMBIGUITY, Level.HIGH.value, "no prompt states the task", ()
        )
    cited = (prompt.event_id,)
    if surface is not None and surface.basis is SeedBasis.NAMED:
        return ClassEvidence(
            Dimension.AMBIGUITY,
            Level.LOW.value,
            "the prompt names repository file(s) " + ", ".join(surface.seeds[:3]),
            cited,
        )
    named = sorted(
        {
            m.group(0)
            for m in _FILE_WORD.finditer(_prompt_text(prompt))
            if len(m.group(0)) >= 4
        }
    )
    if named:
        return ClassEvidence(
            Dimension.AMBIGUITY,
            Level.LOW.value,
            "the prompt names " + ", ".join(named[:3]),
            cited,
        )
    if (surface is not None and surface.basis is SeedBasis.INHERITED) or relation.get(
        prompt.event_id
    ) in _CONTINUES:
        return ClassEvidence(
            Dimension.AMBIGUITY,
            Level.MEDIUM.value,
            "names no file but continues the earlier intent",
            cited,
        )
    if prompt.questions:
        return ClassEvidence(
            Dimension.AMBIGUITY,
            Level.HIGH.value,
            "names no file and asks a question",
            cited,
        )
    return ClassEvidence(
        Dimension.AMBIGUITY,
        Level.MEDIUM.value,
        "names no file, asks no question",
        cited,
    )


def _prompt_text(prompt: Step) -> str:
    # A prompt step keeps its first 80 characters as its subject and its
    # content words; file names survive in either.
    return prompt.subject + " " + " ".join(sorted(prompt.words))


def _risk(
    task: _Task,
    edited: Mapping[str, Step],
    g: RepositoryGrounding | None,
    surface: ChangeSurface | None,
    findings: Sequence[Finding],
) -> ClassEvidence:
    build = [(p, s) for p, s in edited.items() if _BUILD_FILES.search(p)]
    destructive = [
        s
        for s in task.steps
        if s.is_request and s.command and _DESTRUCTIVE.search(s.command)
    ]
    high: list[str] = []
    events: list[EventId] = []
    if build:
        high.append(
            "edits build, dependency or CI file(s) "
            + ", ".join(p for p, _ in build[:3])
        )
        events += [s.event_id for _, s in build]
    if destructive:
        high.append("runs a destructive command: " + (destructive[0].command or ""))
        events += [s.event_id for s in destructive]
    if surface is not None:
        unrelated = [e for e in surface.edits if e.placement is EditPlacement.UNRELATED]
        if unrelated:
            high.append(
                "edits outside its change surface with no import link: "
                + ", ".join(e.path for e in unrelated[:3])
            )
            events += [e.event_id for e in unrelated]
    ids = {s.event_id for s in task.steps}
    broken = [
        f
        for f in findings
        if f.detector == "boundary_violation" and set(f.evidence) & ids
    ]
    if broken:
        high.append("breaks an architecture contract")
        events += [broken[0].evidence[0]]
    if g is not None:
        wide = [
            (p, s)
            for p, s in edited.items()
            if len(g.importers.get(p, ())) >= WIDE_IMPORTERS
        ]
        if wide:
            p, s = wide[0]
            high.append(
                f"edits {p}, which {len(g.importers[p])} repository files import"
            )
            events.append(s.event_id)
    if high:
        return ClassEvidence(
            Dimension.RISK,
            Level.HIGH.value,
            "; ".join(high),
            tuple(dict.fromkeys(events)),
        )
    source = [
        (p, s) for p, s in edited.items() if _is_source(p) and not is_test_module(p)
    ]
    if source:
        return ClassEvidence(
            Dimension.RISK,
            Level.MEDIUM.value,
            "edits source " + ", ".join(p for p, _ in source[:3]),
            tuple(s.event_id for _, s in source),
        )
    return ClassEvidence(
        Dimension.RISK,
        Level.LOW.value,
        "edits only tests or documentation" if edited else "edits nothing",
        tuple(s.event_id for s in edited.values()),
    )


def _scope(edited: Mapping[str, Step], touched: Mapping[str, Step]) -> ClassEvidence:
    files = edited or touched
    verb = "edits" if edited else "reads"
    if not files:
        return ClassEvidence(Dimension.SCOPE, Scope.NONE.value, "touches no file", ())
    events = tuple(s.event_id for s in files.values())
    if len(files) == 1:
        (path,) = files
        return ClassEvidence(
            Dimension.SCOPE, Scope.FILE.value, f"{verb} {path}", events
        )
    dirs = sorted({str(PurePosixPath(p.replace("\\", "/")).parent) for p in files})
    if len(dirs) == 1:
        return ClassEvidence(
            Dimension.SCOPE,
            Scope.DIRECTORY.value,
            f"{verb} {len(files)} files in {dirs[0]}",
            events,
        )
    return ClassEvidence(
        Dimension.SCOPE,
        Scope.REPOSITORY.value,
        f"{verb} {len(files)} files across {len(dirs)} directories",
        events,
    )


def _validation(
    edited: Mapping[str, Step], surface: ChangeSurface | None
) -> ClassEvidence:
    if not edited:
        return ClassEvidence(
            Dimension.VALIDATION, ValidationNeed.NONE.value, "edits nothing", ()
        )
    source = [(p, s) for p, s in edited.items() if _is_source(p)]
    if not source:
        return ClassEvidence(
            Dimension.VALIDATION,
            ValidationNeed.CHECK.value,
            "edits only documentation or configuration",
            tuple(s.event_id for s in edited.values()),
        )
    reason = "edits source " + ", ".join(p for p, _ in source[:3])
    if surface is not None:
        reason += (
            "; tests of its surface: " + ", ".join(surface.tests[:3])
            if surface.tests
            else "; no test module imports its surface"
        )
    return ClassEvidence(
        Dimension.VALIDATION,
        ValidationNeed.TESTS.value,
        reason,
        tuple(s.event_id for _, s in source),
    )


def classify_tasks(
    view: SessionView, findings: Sequence[Finding] = ()
) -> tuple[TaskClassification, ...]:
    """Classify every task of a session on the five dimensions (TER-RTE-005).

    A task is a prompt and the steps up to the next prompt (steps before the
    first prompt form a task without one). ``findings`` are the session's
    detector findings; only grounded ones (``boundary_violation``) inform a
    class. With repository evidence on ``view``, paths are repository paths
    and the change surface informs ambiguity, risk and validation needs.
    """
    g = view.repository
    surfaces = {s.prompt: s for s in change_surfaces(view)} if g is not None else {}
    relation = {r.event_id: r.relation for r in view.intent.record.revisions}
    out: list[TaskClassification] = []
    for task in _segments(view):
        edits = [s for s in task.steps if s.is_edit]
        edited = _paths(g, edits)
        touched = _paths(
            g,
            (s for s in task.steps if s.is_request and s.stage is Stage.EXPLORE),
        )
        surface = surfaces.get(task.prompt.event_id if task.prompt else None)
        if edits:
            kind = TaskKind.CHANGE
        elif any(s.checks for s in task.steps):
            kind = TaskKind.VALIDATE
        else:
            kind = TaskKind.READ_ONLY
        evidence = (
            _complexity(edited, task),
            _ambiguity(task, surface, relation),
            _risk(task, edited, g, surface, findings),
            _scope(edited, touched),
            _validation(edited, surface),
        )
        out.append(
            TaskClassification(
                task=task.index,
                prompt=task.prompt.event_id if task.prompt else None,
                first=task.steps[0].index,
                last=task.steps[-1].index,
                kind=kind,
                complexity=Level(evidence[0].value),
                ambiguity=Level(evidence[1].value),
                risk=Level(evidence[2].value),
                scope=Scope(evidence[3].value),
                validation=ValidationNeed(evidence[4].value),
                evidence=evidence,
                grounded=g is not None,
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# The router policy (TER-RTE-002, TER-RTE-003)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteEscalation:
    """What a ``route.escalated`` event holds (TER-RTE-002).

    ``latency_ms`` and the token counts are what the task had spent on the
    source role when the signal fired: the cost the escalation has to earn
    back. They travel in the event's text, not its ``usage``, because usage
    fields are summed as spend and these tokens are already counted on the
    calls they summarise.
    """

    signal: str
    finding: str
    source_role: str
    target_role: str
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def text(self) -> str:
        return (
            f"escalated {self.source_role} -> {self.target_role} on {self.signal} "
            f"(finding={self.finding} latency_ms={self.latency_ms} "
            f"input={self.input_tokens} output={self.output_tokens} "
            f"cache_read={self.cache_read_tokens} "
            f"cache_write={self.cache_creation_tokens})"
        )

    @classmethod
    def parse(cls, text: str) -> RouteEscalation | None:
        """Read :meth:`text` back; ``None`` for text in another shape."""
        m = _ESCALATION.fullmatch(text.strip())
        if m is None:
            return None
        return cls(
            signal=m["signal"],
            finding=m["finding"],
            source_role=m["source"],
            target_role=m["target"],
            latency_ms=int(m["latency"]),
            input_tokens=int(m["input"]),
            output_tokens=int(m["output"]),
            cache_read_tokens=int(m["cache_read"]),
            cache_creation_tokens=int(m["cache_write"]),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "signal": self.signal,
            "finding": self.finding,
            "source_role": self.source_role,
            "target_role": self.target_role,
            "latency_ms": self.latency_ms,
            "usage": {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "cache_read": self.cache_read_tokens,
                "cache_write": self.cache_creation_tokens,
            },
        }


_ESCALATION = re.compile(
    r"escalated (?P<source>\S+) -> (?P<target>\S+) on (?P<signal>\S+) "
    r"\(finding=(?P<finding>\S+) latency_ms=(?P<latency>\d+) "
    r"input=(?P<input>\d+) output=(?P<output>\d+) "
    r"cache_read=(?P<cache_read>\d+) cache_write=(?P<cache_write>\d+)\)"
)


@dataclass(frozen=True)
class RoutingSignal:
    """A detector finding offered to the router as a reason to escalate."""

    detector: str
    finding: str
    confidence: float
    evidence: tuple[EventId, ...]
    #: Index of the step at which the signal became observable.
    at: int

    def as_dict(self) -> dict[str, object]:
        return {
            "detector": self.detector,
            "finding": self.finding,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "at": self.at,
        }


@dataclass(frozen=True)
class RouteDecision:
    """The role chosen for one task, and why."""

    task: TaskClassification
    role: str
    final_role: str
    reason: str
    signal: RoutingSignal | None = None
    escalation: RouteEscalation | None = None
    event: Event | None = None

    @property
    def escalated(self) -> bool:
        return self.escalation is not None

    def as_dict(self) -> dict[str, object]:
        return {
            "task": self.task.task,
            "prompt": self.task.prompt,
            "role": self.role,
            "final_role": self.final_role,
            "escalated": self.escalated,
            "reason": self.reason,
            "signal": self.signal.as_dict() if self.signal else None,
            "escalation": self.escalation.as_dict() if self.escalation else None,
            "event": self.event.id if self.event else None,
        }


@dataclass(frozen=True)
class RoutingPlan:
    """The router's advice for a whole session: one decision per task."""

    session_id: str
    profile: RoutingProfile
    decisions: tuple[RouteDecision, ...]
    #: The ``route.escalated`` events, one per escalation, in task order.
    events: tuple[Event, ...] = field(default=())

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "ter.route/0.1",
            "session_id": self.session_id,
            "profile": self.profile.as_dict(),
            "rules": {d.value: r for d, r in CLASSIFICATION_RULES.items()},
            "tasks": [d.task.as_dict() for d in self.decisions],
            "decisions": [d.as_dict() for d in self.decisions],
            "events": [
                {
                    "id": e.id,
                    "sequence": e.sequence,
                    "kind": e.kind.value,
                    "actor": e.actor.value,
                    "text": e.text,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                }
                for e in self.events
            ],
        }


def _signals(
    task: TaskClassification,
    profile: RoutingProfile,
    findings: Sequence[Finding],
    index: Mapping[EventId, int],
) -> list[RoutingSignal]:
    """Confident findings of an escalating detector whose evidence lies in
    the task; each fires at its last evidence step inside the task."""
    out: list[RoutingSignal] = []
    for f in findings:
        if f.detector not in profile.escalate_on or f.uncertain or not f.evidence:
            continue
        inside = [
            index[e]
            for e in f.evidence
            if e in index and task.first <= index[e] <= task.last
        ]
        if not inside:
            continue
        out.append(
            RoutingSignal(f.detector, f.id, f.confidence, f.evidence, max(inside))
        )
    return sorted(out, key=lambda s: (s.at, s.finding))


def escalation_event(
    session_id: str,
    escalation: RouteEscalation,
    *,
    sequence: int,
    trigger: Step,
    task: TaskClassification,
) -> Event:
    """The one ``route.escalated`` event recording an escalation."""
    key = task.prompt or f"task{task.task}"
    return Event(
        id=make_event_id(session_id, "route.escalated", key, escalation.finding),
        session_id=session_id,
        sequence=sequence,
        kind=EventKind.ROUTE_ESCALATED,
        actor=Actor.SYSTEM,
        text=escalation.text(),
        provenance=Provenance(
            source="ter.route", record_id=f"{key}:{escalation.finding}"
        ),
        timestamp=trigger.timestamp,
        parent_id=trigger.event_id,
    )


def route_session(
    session_id: str,
    steps: Sequence[Step],
    tasks: Sequence[TaskClassification],
    findings: Sequence[Finding],
    profile: RoutingProfile,
    *,
    first_sequence: int = 0,
) -> RoutingPlan:
    """Choose a role for every task and escalate where a signal supports it.

    Each task starts on the role its profile gives its kind. It escalates,
    once, to the role the profile names above that one when a confident
    finding of a detector in ``escalate_on`` cites an event of the task
    (TER-RTE-002). Without such a signal the task keeps its role
    (TER-RTE-003). Escalation events are numbered from ``first_sequence``
    so they can be appended after the session's own events.
    """
    index = {s.event_id: s.index for s in steps}
    decisions: list[RouteDecision] = []
    events: list[Event] = []
    for task in tasks:
        role = profile.role_for(task.kind)
        profile.binding(role)  # a profile always defines the roles it maps
        signals = _signals(task, profile, findings, index)
        target = profile.escalates_to(role)
        if not signals:
            decisions.append(
                RouteDecision(
                    task,
                    role,
                    role,
                    f"{task.kind.value} task stays on {role}: no detector signal "
                    "with evidence supports escalation",
                )
            )
            continue
        signal = signals[0]
        if target is None:
            decisions.append(
                RouteDecision(
                    task,
                    role,
                    role,
                    f"{signal.detector} fired, but the profile names no role above "
                    f"{role}",
                    signal,
                )
            )
            continue
        trigger = steps[signal.at]
        spent = steps[task.first : signal.at + 1]
        usage = [s.usage for s in spent if s.usage is not None]
        escalation = RouteEscalation(
            signal=signal.detector,
            finding=signal.finding,
            source_role=role,
            target_role=target,
            latency_ms=round(sum(s.seconds for s in spent) * 1000),
            input_tokens=sum(u.input_tokens for u in usage),
            output_tokens=sum(u.output_tokens for u in usage),
            cache_read_tokens=sum(u.cache_read_tokens for u in usage),
            cache_creation_tokens=sum(u.cache_creation_tokens for u in usage),
        )
        event = escalation_event(
            session_id,
            escalation,
            sequence=first_sequence + len(events),
            trigger=trigger,
            task=task,
        )
        events.append(event)
        decisions.append(
            RouteDecision(
                task,
                role,
                target,
                f"{signal.detector} ({signal.confidence:.2f}) cites "
                f"{len(signal.evidence)} event(s) of the task: escalate {role} -> "
                f"{target}",
                signal,
                escalation,
                event,
            )
        )
    return RoutingPlan(session_id, profile, tuple(decisions), tuple(events))
