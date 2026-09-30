"""Waste detectors: small plugins behind one protocol.

A detector reads a :class:`SessionView` and yields :class:`~.model.Finding`
values. Each one is conservative on purpose (point 91): it reports only what
the event stream shows, cites every event it relies on (point 40), and gives
a confidence from an explicit rule, published as ``confidence_rule``. Below
:data:`~.model.UNCERTAIN_BELOW` a finding is reported as uncertain rather
than dropped or asserted (points 85, 86).

To add a detector, implement :class:`WasteDetector` and register it in a
:class:`DetectorRegistry` (point 197). ``DEFAULT_REGISTRY`` holds the L2 set.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol

from ..events import EventId, EventKind, ToolKind
from .facts import overlap
from .model import (
    ActivityClass,
    CycleVerdict,
    Finding,
    FindingKind,
    LeanWaste,
    Outcome,
    Stage,
    Step,
    ValidationCycle,
)

__all__ = [
    "DEFAULT_REGISTRY",
    "DetectorRegistry",
    "ExcessivePlanning",
    "FragmentedEdits",
    "PrematureImplementation",
    "Regeneration",
    "RepeatedExploration",
    "RepeatedReasoning",
    "RepeatedToolCall",
    "ReworkCycle",
    "SessionView",
    "UnnecessaryHandoff",
    "UnusedContext",
    "UnvalidatedImplementation",
    "WasteDetector",
    "validation_cycles",
]

_EXPLORE_KINDS = frozenset({ToolKind.FS_READ, ToolKind.FS_SEARCH})
_DOC_SUFFIXES = frozenset({".md", ".rst", ".txt", ".adoc"})


@dataclass(frozen=True)
class SessionView:
    """Steps plus the indexes detectors need, built once per analysis."""

    steps: tuple[Step, ...]
    completion_of: dict[int, Step] = field(default_factory=dict)
    by_id: dict[EventId, Step] = field(default_factory=dict)

    @classmethod
    def of(cls, steps: Sequence[Step]) -> SessionView:
        completion_of = {
            s.request_index: s
            for s in steps
            if s.is_completion and s.request_index is not None
        }
        return cls(tuple(steps), completion_of, {s.event_id: s for s in steps})

    def requests(self) -> Iterator[Step]:
        return (s for s in self.steps if s.is_request)

    def pair(self, request: Step) -> tuple[Step, ...]:
        """A request and, when observed, its completion."""
        done = self.completion_of.get(request.index)
        return (request,) if done is None else (request, done)

    def segment_end(self, index: int) -> int:
        """Index of the last step before the next prompt (or of the last step)."""
        for step in self.steps[index + 1 :]:
            if step.kind is EventKind.PROMPT:
                return step.index - 1
        return len(self.steps) - 1


class WasteDetector(Protocol):
    """A plugin that finds one kind of waste or risk in a session."""

    @property
    def id(self) -> str: ...

    @property
    def waste(self) -> LeanWaste: ...

    @property
    def kind(self) -> FindingKind: ...

    @property
    def summary(self) -> str: ...

    @property
    def confidence_rule(self) -> str: ...

    def detect(self, view: SessionView) -> Iterable[Finding]: ...


def _finding(
    detector: WasteDetector,
    view: SessionView,
    *,
    confidence: float,
    title: str,
    explanation: str,
    evidence: Iterable[Step],
    waste: Iterable[Step] = (),
    share: float = 1.0,
    subject: str = "",
    activity_class: ActivityClass = ActivityClass.AVOIDABLE,
) -> Finding:
    cited = sorted({s.index: s for s in evidence}.values(), key=lambda s: s.index)
    wasted = (
        sorted({s.index: s for s in waste}.values(), key=lambda s: s.index)
        if detector.kind is FindingKind.WASTE
        else []
    )
    anchor = wasted[0] if wasted else cited[0]
    share = max(0.0, min(1.0, share))
    return Finding(
        id=f"{detector.id}:{anchor.event_id}",
        detector=detector.id,
        waste=detector.waste,
        kind=detector.kind,
        activity_class=activity_class,
        confidence=round(max(0.0, min(1.0, confidence)), 4),
        title=title,
        explanation=explanation,
        evidence=tuple(s.event_id for s in cited),
        waste_events=tuple(s.event_id for s in wasted),
        share=share if wasted else 0.0,
        subject=subject,
        tokens=round(sum(s.tokens for s in wasted) * share),
        context_tokens=round(sum(s.context_tokens for s in wasted) * share),
        seconds=round(sum(s.seconds for s in wasted) * share, 3),
    )


def _edits_between(view: SessionView, start: int, end: int) -> list[Step]:
    return [s for s in view.steps[start + 1 : end] if s.is_edit]


def _basename(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).name


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepeatedToolCall:
    """Point 19 (and 39): the same call again, with the same result."""

    id: str = "repeated_tool_call"
    waste: LeanWaste = LeanWaste.OVER_PROCESSING
    kind: FindingKind = FindingKind.WASTE
    summary: str = "A tool call repeated with unchanged input and unchanged output."
    confidence_rule: str = (
        "0.90 when input and output are identical and nothing was edited in "
        "between; 0.85 for a validation re-run with no edit in between; 0.75 "
        "when edits happened in between but the output is still identical; "
        "0.50 (uncertain) when an output was not observed. Different output: "
        "no finding. File reads and searches are left to repeated_exploration; "
        "a validation re-run after edits is left to rework_cycle."
    )

    def detect(self, view: SessionView) -> Iterable[Finding]:
        last: dict[str, Step] = {}
        for step in view.requests():
            key = step.call_key
            if key is None or step.tool_kind in _EXPLORE_KINDS:
                continue
            earlier = last.get(key)
            last[key] = step
            if earlier is None:
                continue
            edited = bool(_edits_between(view, earlier.index, step.index))
            if step.is_validation and edited:
                continue
            before = view.completion_of.get(earlier.index)
            after = view.completion_of.get(step.index)
            if before is None or after is None:
                confidence = 0.5
                note = "One of the two results was not observed, so the output may have differed."
            elif before.output_hash != after.output_hash:
                continue
            elif step.is_validation:
                confidence = 0.85
                note = "No file was edited between the two runs, so the second could not tell the agent anything new."
            elif edited:
                confidence = 0.75
                note = "Files were edited in between, yet the result was identical."
            else:
                confidence = 0.9
                note = "Nothing changed in between and the result was identical."
            what = (
                "validation run" if step.is_validation else f"{step.native_name} call"
            )
            yield _finding(
                self,
                view,
                confidence=confidence,
                title=f"Repeated {what}",
                explanation=(
                    f"{step.native_name} was called again with the same input "
                    f"({_short(step.subject)}). {note}"
                ),
                evidence=(*view.pair(earlier), *view.pair(step)),
                waste=view.pair(step),
                subject=step.subject,
            )


@dataclass(frozen=True)
class RepeatedExploration:
    """Points 18, 28, 36: re-reading a file or re-running a search."""

    id: str = "repeated_exploration"
    waste: LeanWaste = LeanWaste.MOTION
    kind: FindingKind = FindingKind.WASTE
    summary: str = (
        "A file re-read, or a search re-run, that returned what the agent already had."
    )
    confidence_rule: str = (
        "0.85 when the same read or search (same arguments) returned identical "
        "output and the file was not edited in between; 0.55 (uncertain) when "
        "either output was not observed. A read after an edit of that file, a "
        "read of a different range, or a changed output is not a finding."
    )

    def detect(self, view: SessionView) -> Iterable[Finding]:
        last: dict[str, Step] = {}
        for step in view.requests():
            if step.tool_kind not in _EXPLORE_KINDS or step.call_key is None:
                continue
            earlier = last.get(step.call_key)
            last[step.call_key] = step
            if earlier is None:
                continue
            edits = _edits_between(view, earlier.index, step.index)
            if step.tool_kind is ToolKind.FS_READ:
                if any(set(e.paths) & set(step.paths) for e in edits):
                    continue
            elif edits:
                continue
            before = view.completion_of.get(earlier.index)
            after = view.completion_of.get(step.index)
            if before is None or after is None:
                confidence = 0.55
                note = "One of the two results was not observed."
            elif before.output_hash != after.output_hash:
                continue
            else:
                confidence = 0.85
                note = "The output was identical and the agent had not changed it."
            verb = "Re-read" if step.tool_kind is ToolKind.FS_READ else "Re-ran search"
            yield _finding(
                self,
                view,
                confidence=confidence,
                title=f"{verb} {_short(step.subject)}",
                explanation=(
                    f"{verb} {_short(step.subject)} {step.index - earlier.index} "
                    f"events after the first time. {note} Its "
                    f"{view.completion_of[step.index].context_tokens if step.index in view.completion_of else 0}"
                    " tokens of output re-entered the context."
                ),
                evidence=(*view.pair(earlier), *view.pair(step)),
                waste=view.pair(step),
                subject=step.subject,
            )


def validation_cycles(view: SessionView) -> tuple[ValidationCycle, ...]:
    """Pair every failed validation run with the next run of the same command.

    Only pairs with at least one edit in between are cycles: the edits were an
    attempt to fix the failure. The verdict is iteration when the next run
    passed or failed differently, and rework when it failed identically. Runs
    whose outcome cannot be read form no cycle.
    """
    runs = [
        (s, view.completion_of.get(s.index)) for s in view.requests() if s.is_validation
    ]
    cycles: list[ValidationCycle] = []
    for i, (run, result) in enumerate(runs):
        if result is None or result.outcome is not Outcome.FAILED:
            continue
        following = next(
            ((r, res) for r, res in runs[i + 1 :] if r.command == run.command), None
        )
        if following is None:
            continue
        next_run, next_result = following
        fixes = _edits_between(view, run.index, next_run.index)
        outcome = next_result.outcome if next_result is not None else None
        if (
            not fixes
            or next_result is None
            or outcome is None
            or outcome is Outcome.UNKNOWN
        ):
            continue
        if next_result.outcome is Outcome.PASSED:
            verdict, reason = CycleVerdict.ITERATION, "the next run passed"
        elif next_result.failure_signature != result.failure_signature:
            verdict, reason = CycleVerdict.ITERATION, "the next run failed differently"
        else:
            verdict, reason = CycleVerdict.REWORK, "the next run failed identically"
        cycles.append(
            ValidationCycle(
                failed_run=run.event_id,
                failure=result.event_id,
                fixes=tuple(f.event_id for f in fixes),
                next_run=next_run.event_id,
                next_result=next_result.event_id,
                next_outcome=outcome,
                verdict=verdict,
                command=run.command or "",
                reason=reason,
            )
        )
    return tuple(cycles)


@dataclass(frozen=True)
class ReworkCycle:
    """Points 26, 37: an edit-test-fail cycle that did not move the failure."""

    id: str = "rework_cycle"
    waste: LeanWaste = LeanWaste.REWORK
    kind: FindingKind = FindingKind.WASTE
    summary: str = (
        "Edits after a failing check that left the check failing the same way."
    )
    confidence_rule: str = (
        "Only cycles of the same validation command with edits in between. "
        "0.80 when the next run fails with the same failure signature (timings "
        "and addresses ignored); 0.90 when that is the second or later identical "
        "failure in a row. A cycle whose next run passes or fails differently "
        "is productive iteration and is not a finding."
    )

    def detect(self, view: SessionView) -> Iterable[Finding]:
        streak: dict[str, int] = {}
        for cycle in validation_cycles(view):
            if cycle.verdict is not CycleVerdict.REWORK:
                streak[cycle.command] = 0
                continue
            streak[cycle.command] = streak.get(cycle.command, 0) + 1
            confidence = 0.8 if streak[cycle.command] == 1 else 0.9
            fixes = [view.by_id[e] for e in cycle.fixes]
            fix_pairs = [s for f in fixes for s in view.pair(f)]
            rerun = view.by_id[cycle.next_run]
            ids = (cycle.failed_run, cycle.failure, cycle.next_run)
            yield _finding(
                self,
                view,
                confidence=confidence,
                title=f"Fix attempt did not change the failure of {_short(cycle.command)}",
                explanation=(
                    f"{len(fixes)} edit(s) to {_files(fixes)} after `{_short(cycle.command)}` "
                    "failed, then the same command failed with the same failure. The "
                    "attempt was rework: it did not move the check."
                ),
                evidence=(*(view.by_id[e] for e in ids), *fix_pairs, *view.pair(rerun)),
                waste=(*fix_pairs, *view.pair(rerun)),
                subject=cycle.command,
            )


@dataclass(frozen=True)
class UnvalidatedImplementation:
    """Point 25: changes reported without a validation run after them."""

    id: str = "unvalidated_implementation"
    waste: LeanWaste = LeanWaste.DEFECTS
    kind: FindingKind = FindingKind.RISK
    summary: str = "Edits the agent reported on without running a check after them, or a last check that failed."
    confidence_rule: str = (
        "Judged per prompt, once the agent has responded after its edits. 0.85 "
        "when no validation ran in the whole session; 0.75 when validation ran "
        "earlier but not after these edits; 0.50 (uncertain) when every "
        "unvalidated edit is documentation. 0.85 when the last validation "
        "before the response failed."
    )

    def detect(self, view: SessionView) -> Iterable[Finding]:
        any_validation = any(s.is_validation for s in view.steps)
        start = 0
        while start < len(view.steps):
            end = view.segment_end(start)
            yield from self._segment(view, view.steps[start : end + 1], any_validation)
            start = end + 1

    def _segment(
        self, view: SessionView, steps: Sequence[Step], any_validation: bool
    ) -> Iterator[Finding]:
        pending: list[Step] = []
        last_run: Step | None = None
        for step in steps:
            if step.is_edit:
                pending.append(step)
            elif step.is_validation:
                pending = []
                last_run = step
        responses = [s for s in steps if s.kind is EventKind.RESPONSE]
        final = responses[-1] if responses else None
        if final is None:
            return
        if pending and final.index > pending[-1].index:
            docs = all(
                PurePosixPath(p).suffix.lower() in _DOC_SUFFIXES
                for e in pending
                for p in e.paths
            ) and any(e.paths for e in pending)
            if docs:
                confidence, why = (
                    0.5,
                    "Only documentation changed, which may not need a check.",
                )
            elif any_validation:
                confidence, why = (
                    0.75,
                    "Checks ran earlier in the session, but not after these edits.",
                )
            else:
                confidence, why = 0.85, "No check ran anywhere in the session."
            yield _finding(
                self,
                view,
                confidence=confidence,
                title=f"{len(pending)} edit(s) reported without validation",
                explanation=(
                    f"The agent edited {_files(pending)} and then responded without "
                    f"running tests, a type check or the code. {why}"
                ),
                evidence=(*(s for e in pending for s in view.pair(e)), final),
                subject=_files(pending),
            )
        if last_run is not None and final.index > last_run.index:
            result = view.completion_of.get(last_run.index)
            if result is not None and result.outcome is Outcome.FAILED:
                later_edits = [e for e in pending if e.index > last_run.index]
                if not later_edits:
                    yield _finding(
                        self,
                        view,
                        confidence=0.85,
                        title="Responded after a failing check",
                        explanation=(
                            f"The last validation, `{_short(last_run.command or '')}`, "
                            "failed and the agent responded without fixing or re-running it."
                        ),
                        evidence=(last_run, result, final),
                        subject=last_run.command or "",
                    )


@dataclass(frozen=True)
class PrematureImplementation:
    """Points 22, 23: changing code before looking at it."""

    id: str = "premature_implementation"
    waste: LeanWaste = LeanWaste.DEFECTS
    kind: FindingKind = FindingKind.RISK
    summary: str = (
        "An edit to a file the agent had not read, written or seen in a search."
    )
    confidence_rule: str = (
        "0.75 for an in-place edit of a file never read, written or named in an "
        "earlier search or shell output; 0.45 (uncertain) for writing a new file "
        "before any exploration at all in the session. One finding per file."
    )

    def detect(self, view: SessionView) -> Iterable[Finding]:
        known: set[str] = set()
        seen_names: set[str] = set()
        explored = False
        flagged: set[str] = set()
        prompt: Step | None = None
        for step in view.steps:
            if step.kind is EventKind.PROMPT:
                prompt = step
            if step.is_completion and step.tool_kind is not ToolKind.FS_READ:
                seen_names |= step.words
            if not step.is_request:
                continue
            if step.stage is Stage.EXPLORE:
                explored = True
            if step.is_edit:
                for path in step.paths:
                    base = _basename(path).lower()
                    if path in known or path in flagged or base in seen_names:
                        continue
                    if step.tool_kind is ToolKind.FS_EDIT:
                        confidence = 0.75
                        why = "It was not read, written or named in any earlier output."
                    elif not explored:
                        confidence = 0.45
                        why = "Nothing in the repository had been explored yet; the file may be new."
                    else:
                        continue
                    flagged.add(path)
                    cited = [step] if prompt is None else [prompt, step]
                    yield _finding(
                        self,
                        view,
                        confidence=confidence,
                        title=f"Changed {_short(path)} before reading it",
                        explanation=f"{step.native_name} changed {path} with no evidence collected about it first. {why}",
                        evidence=cited,
                        subject=path,
                    )
            if step.tool_kind in (
                ToolKind.FS_READ,
                ToolKind.FS_EDIT,
                ToolKind.FS_WRITE,
            ):
                known.update(step.paths)


@dataclass(frozen=True)
class ExcessivePlanning:
    """Point 24: planning that does not transition into action."""

    id: str = "excessive_planning"
    waste: LeanWaste = LeanWaste.OVER_PROCESSING
    kind: FindingKind = FindingKind.WASTE
    summary: str = "A run of planning steps (reasoning, to-do updates) with no action between them."
    confidence_rule: str = (
        "A run of at least 4 consecutive planning steps (reasoning blocks and "
        "plan/to-do updates) with no exploration, edit, check or response in "
        "between. Confidence 0.55 + 0.05 per step, capped at 0.90. The first two "
        "steps of the run are counted as necessary; the rest as waste."
    )
    minimum: int = 4

    def detect(self, view: SessionView) -> Iterable[Finding]:
        run: list[Step] = []
        for step in (*view.steps, None):
            if step is not None and (
                step.is_completion and step.tool_kind is ToolKind.PLAN
            ):
                continue
            if step is not None and step.is_generated and step.stage is Stage.PLAN:
                run.append(step)
                continue
            if len(run) >= self.minimum:
                extra = run[2:]
                yield _finding(
                    self,
                    view,
                    confidence=min(0.9, 0.55 + 0.05 * len(run)),
                    title=f"{len(run)} planning steps without acting",
                    explanation=(
                        f"{len(run)} reasoning or to-do steps in a row before the agent "
                        "explored, edited, checked or answered anything."
                    ),
                    evidence=[s for e in run for s in view.pair(e)],
                    waste=[s for e in extra for s in view.pair(e)],
                    subject=f"{len(run)} planning steps",
                )
            run = []


@dataclass(frozen=True)
class FragmentedEdits:
    """Point 27: one coherent change split into many round trips."""

    id: str = "fragmented_edits"
    waste: LeanWaste = LeanWaste.OVER_PROCESSING
    kind: FindingKind = FindingKind.WASTE
    summary: str = (
        "Three or more edits in a row to the same file, each its own round trip."
    )
    confidence_rule: str = (
        "At least 3 consecutive edit calls to one file with only reasoning and "
        "their results in between. 0.70 for 3, plus 0.05 per extra edit, capped "
        "at 0.85. Only the round-trip overhead (results and waiting of edits "
        "after the first) is counted as waste, never the change itself."
    )
    minimum: int = 3

    def detect(self, view: SessionView) -> Iterable[Finding]:
        run: list[Step] = []
        for step in (*view.requests(), None):
            if (
                step is not None
                and step.tool_kind is ToolKind.FS_EDIT
                and len(step.paths) == 1
                and (not run or run[-1].paths == step.paths)
                and (not run or _only_thinking_between(view, run[-1], step))
            ):
                run.append(step)
                continue
            if len(run) >= self.minimum:
                overhead = [
                    view.completion_of[s.index]
                    for s in run[1:]
                    if s.index in view.completion_of
                ]
                yield _finding(
                    self,
                    view,
                    confidence=min(0.85, 0.7 + 0.05 * (len(run) - self.minimum)),
                    title=f"{len(run)} separate edits to {_short(run[0].paths[0])}",
                    explanation=(
                        f"{len(run)} consecutive edits to {run[0].paths[0]}, each a separate "
                        "tool round trip, where one multi-edit would do."
                    ),
                    evidence=[s for e in run for s in view.pair(e)],
                    waste=overhead,
                    subject=run[0].paths[0],
                )
            run = (
                [step]
                if step is not None
                and step.tool_kind is ToolKind.FS_EDIT
                and len(step.paths) == 1
                else []
            )


def _only_thinking_between(view: SessionView, a: Step, b: Step) -> bool:
    return all(
        s.kind is EventKind.REASONING
        or (s.is_completion and s.request_index == a.index)
        for s in view.steps[a.index + 1 : b.index]
    )


@dataclass(frozen=True)
class UnusedContext:
    """Points 21, 34, 35: file contents read and never used again."""

    id: str = "unused_context"
    waste: LeanWaste = LeanWaste.INVENTORY
    kind: FindingKind = FindingKind.WASTE
    summary: str = "A file read whose name and definitions never appear in anything the agent did afterwards."
    confidence_rule: str = (
        "Judged only once the agent has responded after the read. A read counts "
        "as used when a later reasoning, response or tool call names the file, "
        "or names something the file defines, or edits it. Otherwise 0.65 when "
        "the file defined names that were never used, 0.55 when it defined none. "
        "Always uncertain: reading to rule something out is legitimate, and only "
        "repository evidence (L3) can tell."
    )

    def detect(self, view: SessionView) -> Iterable[Finding]:
        last_response = max(
            (s.index for s in view.steps if s.kind is EventKind.RESPONSE), default=-1
        )
        judged: set[str] = set()
        for step in view.steps:
            if not (
                step.is_completion and step.tool_kind is ToolKind.FS_READ and step.paths
            ):
                continue
            path = step.paths[0]
            if path in judged or step.index > last_response:
                continue
            judged.add(path)
            if self._used(view, step, path):
                continue
            request = (
                view.steps[step.request_index]
                if step.request_index is not None
                else step
            )
            yield _finding(
                self,
                view,
                confidence=0.65 if step.identifiers else 0.55,
                title=f"Read {_short(path)} and never used it",
                explanation=(
                    f"Nothing the agent did after reading {path} names the file"
                    + (
                        f" or any of the {len(step.identifiers)} name(s) it defines"
                        if step.identifiers
                        else ""
                    )
                    + f". Its {step.context_tokens} tokens stayed in the context as inventory."
                ),
                evidence=(request, step),
                waste=(request, step),
                subject=path,
            )

    @staticmethod
    def _used(view: SessionView, read: Step, path: str) -> bool:
        base = _basename(path).lower()
        stem = base.rsplit(".", 1)[0]
        names = {base} | ({stem} if len(stem) >= 4 else set())
        names |= {i.lower() for i in read.identifiers}
        for later in view.steps[read.index + 1 :]:
            if (
                later.is_request
                and path in later.paths
                and later.tool_kind is not ToolKind.FS_READ
            ):
                return True
            if (
                later.is_generated
                and later.tool_kind is not ToolKind.FS_READ
                and names & later.words
            ):
                return True
        return False


@dataclass(frozen=True)
class UnnecessaryHandoff:
    """Point 29 (and 30): delegating work, then doing it anyway."""

    id: str = "unnecessary_handoff"
    waste: LeanWaste = LeanWaste.HANDOFFS
    kind: FindingKind = FindingKind.WASTE
    summary: str = "A subagent handoff whose task the agent then did itself."
    confidence_rule: str = (
        "A later tool call by the agent itself shares at least 3 content words "
        "and at least half of the smaller word set with the handoff's task. "
        "Confidence 0.45 + 0.40 × overlap, capped at 0.85. The handoff (and "
        "its waiting time) is the waste; the agent's own call is kept."
    )

    def detect(self, view: SessionView) -> Iterable[Finding]:
        for handoff in view.requests():
            if handoff.tool_kind is not ToolKind.AGENT_HANDOFF or not handoff.words:
                continue
            best: tuple[float, Step] | None = None
            for later in view.steps[handoff.index + 1 :]:
                if not later.is_request or later.tool_kind is ToolKind.AGENT_HANDOFF:
                    continue
                shared = handoff.words & later.words
                score = overlap(handoff.words, later.words)
                if (
                    len(shared) >= 3
                    and score >= 0.5
                    and (best is None or score > best[0])
                ):
                    best = (score, later)
            if best is None:
                continue
            score, own = best
            yield _finding(
                self,
                view,
                confidence=min(0.85, 0.45 + 0.4 * score),
                title=f"Delegated {_short(handoff.subject)}, then did it directly",
                explanation=(
                    f"The agent handed '{_short(handoff.subject)}' to a subagent, then ran "
                    f"{own.native_name} on the same subject ({_short(own.subject)}), sharing "
                    f"{len(handoff.words & own.words)} key words. The handoff added a round "
                    "trip the agent did not rely on."
                ),
                evidence=(*view.pair(handoff), *view.pair(own)),
                waste=view.pair(handoff),
                subject=handoff.subject,
            )


@dataclass(frozen=True)
class RepeatedReasoning:
    """Point 17: reasoning that restates earlier reasoning without new decisions."""

    id: str = "repeated_reasoning"
    waste: LeanWaste = LeanWaste.OVER_PROCESSING
    kind: FindingKind = FindingKind.WASTE
    summary: str = "A reasoning block that restates an earlier one for the same prompt."
    confidence_rule: str = (
        "Same prompt and no edit in between. The later block shares at least 3 "
        "content words with the earlier one, and at most 25% of its content words "
        "are new (in neither the earlier block nor the prompt). Confidence 0.85 − "
        "novelty, minus 0.10 when the later block has fewer than 6 content words. "
        "The restated share (1 − novelty) of the later block is waste."
    )

    def detect(self, view: SessionView) -> Iterable[Finding]:
        segment: list[Step] = []
        prompt: frozenset[str] = frozenset()
        for step in view.steps:
            if step.kind is EventKind.PROMPT:
                segment, prompt = [], step.words
                continue
            if step.is_edit:
                segment = []
                continue
            if step.kind is not EventKind.REASONING or len(step.words) < 4:
                continue
            best: tuple[float, Step] | None = None
            for earlier in segment:
                if len(earlier.words & step.words) < 3:
                    continue
                novelty = len(step.words - earlier.words - prompt) / len(step.words)
                if novelty <= 0.25 and (best is None or novelty < best[0]):
                    best = (novelty, earlier)
            segment.append(step)
            if best is None:
                continue
            novelty, earlier = best
            yield _finding(
                self,
                view,
                confidence=0.85 - novelty - (0.1 if len(step.words) < 6 else 0.0),
                title="Reasoning restated",
                explanation=(
                    f"This reasoning restates reasoning from {step.index - earlier.index} "
                    f"events earlier: {1 - novelty:.0%} of its key words were already there "
                    "or in the prompt, and nothing was edited in between."
                ),
                evidence=(earlier, step),
                waste=(step,),
                share=1 - novelty,
                subject="reasoning",
            )


@dataclass(frozen=True)
class Regeneration:
    """Point 20: rewriting work that already existed."""

    id: str = "regeneration"
    waste: LeanWaste = LeanWaste.OVERPRODUCTION
    kind: FindingKind = FindingKind.WASTE
    summary: str = "A whole-file write that mostly regenerates content already there."
    confidence_rule: str = (
        "A whole-file write of a file the agent had itself written, keeping at "
        "least 80% of its lines: 0.80. A whole-file write of a file it had read "
        "(3+ lines), keeping at least 60% of them: 0.60 (uncertain; small files "
        "are often rewritten on purpose). The retained share of the write is waste."
    )

    def detect(self, view: SessionView) -> Iterable[Finding]:
        written: dict[str, Step] = {}
        read: dict[str, Step] = {}
        for step in view.steps:
            if step.is_completion and step.tool_kind is ToolKind.FS_READ and step.paths:
                read[step.paths[0]] = step
            if not (
                step.is_request and step.tool_kind is ToolKind.FS_WRITE and step.paths
            ):
                continue
            path = step.paths[0]
            new = step.lines
            prior_write = written.get(path)
            prior_read = read.get(path)
            written[path] = step
            if not new:
                continue
            if prior_write is not None and prior_write.lines:
                kept = len(prior_write.lines & new) / len(prior_write.lines)
                if kept >= 0.8:
                    yield self._found(
                        view, step, prior_write, kept, 0.8, "it had written itself"
                    )
                continue
            if prior_read is not None and len(prior_read.lines) >= 3:
                kept = len(prior_read.lines & new) / len(prior_read.lines)
                if kept >= 0.6:
                    yield self._found(
                        view, step, prior_read, kept, 0.6, "it had just read"
                    )

    def _found(
        self,
        view: SessionView,
        step: Step,
        prior: Step,
        kept: float,
        confidence: float,
        what: str,
    ) -> Finding:
        path = step.paths[0]
        share = len(prior.lines & step.lines) / len(step.lines)
        return _finding(
            self,
            view,
            confidence=confidence,
            title=f"Rewrote {_short(path)} in full",
            explanation=(
                f"The agent rewrote {path}, a file {what}, keeping {kept:.0%} of its "
                f"lines; {share:.0%} of the new file repeats existing content. An "
                "in-place edit would have produced only the change."
            ),
            evidence=(prior, step),
            waste=(step,),
            share=share,
            subject=path,
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class DetectorRegistry:
    """An ordered set of detectors, keyed by id. Order breaks ties in reports."""

    def __init__(self, detectors: Iterable[WasteDetector] = ()) -> None:
        self._detectors: dict[str, WasteDetector] = {}
        for detector in detectors:
            self.register(detector)

    def register(self, detector: WasteDetector) -> None:
        if detector.id in self._detectors:
            raise ValueError(f"Detector {detector.id!r} is already registered")
        self._detectors[detector.id] = detector

    def __iter__(self) -> Iterator[WasteDetector]:
        return iter(self._detectors.values())

    def __len__(self) -> int:
        return len(self._detectors)

    def __contains__(self, detector_id: object) -> bool:
        return detector_id in self._detectors

    def get(self, detector_id: str) -> WasteDetector:
        return self._detectors[detector_id]

    def run(self, view: SessionView) -> tuple[Finding, ...]:
        """Every detector's findings, largest cost first, then in session order."""
        order = {d.id: i for i, d in enumerate(self)}
        index = {s.event_id: s.index for s in view.steps}
        found = [f for d in self for f in d.detect(view)]
        return tuple(
            sorted(
                found,
                key=lambda f: (
                    f.kind is not FindingKind.WASTE,
                    -(f.tokens + f.context_tokens),
                    index[f.evidence[0]],
                    order[f.detector],
                    f.id,
                ),
            )
        )


#: The L2 detector set, in catalogue order.
DEFAULT_REGISTRY = DetectorRegistry(
    (
        RepeatedToolCall(),
        RepeatedExploration(),
        ReworkCycle(),
        UnvalidatedImplementation(),
        PrematureImplementation(),
        ExcessivePlanning(),
        FragmentedEdits(),
        UnusedContext(),
        UnnecessaryHandoff(),
        RepeatedReasoning(),
        Regeneration(),
    )
)


def _short(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _files(steps: Sequence[Step]) -> str:
    paths = sorted({p for s in steps for p in s.paths})
    if not paths:
        return "files"
    if len(paths) <= 3:
        return ", ".join(paths)
    return f"{', '.join(paths[:3])} and {len(paths) - 3} more"
