"""Token use and waste by language and stack over a corpus (L6).

:func:`session_measures` reads one explained session's content-free measures,
and :class:`CompareByStack` explains every session of a corpus and stratifies
them by language and by stack, task category and outcome
(TER-STK-010, TER-STK-011; :mod:`ter.domain.stack_comparison`).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.stack import stack_label
from ..domain.stack_comparison import (
    DEFAULT_MIN_SESSIONS,
    NO_FILES,
    UNLABELLED,
    Dimension,
    SessionMeasures,
    StackComparison,
    compare_strata,
)
from .explain import ExplainedSession

__all__ = [
    "RATE_DETECTORS",
    "CompareByStack",
    "CorpusSession",
    "StackCorpusReport",
    "session_measures",
]

#: Detectors whose allocated tokens make each waste rate.
RATE_DETECTORS: Mapping[str, str] = {
    "rework_rate": "rework_cycle",
    "regeneration_rate": "regeneration",
    "exploration_rate": "repeated_exploration",
}


def _share(part: float, whole: float) -> float | None:
    return part / whole if whole else None


def session_measures(
    explained: ExplainedSession, labels: Mapping[str, str] | None = None
) -> SessionMeasures:
    """One session's group keys, labels and content-free measures."""
    analysis = explained.analysis
    card = analysis.scorecard
    labels = labels or {}
    generated = card.generated_tokens
    by_finding = analysis.allocated_waste_tokens()
    detector_of = {f.id: f.detector for f in analysis.findings}
    by_detector: defaultdict[str, float] = defaultdict(float)
    for finding_id, tokens in by_finding.items():
        by_detector[detector_of.get(finding_id, finding_id)] += tokens
    inventory = explained.a3.inventory
    profile = analysis.profile
    touched = bool(profile.languages or profile.unrecognised)
    return SessionMeasures(
        # A session that named files no table knows is ``unknown``; one that
        # named no file at all says nothing about a language (TER-STK-013).
        language=profile.dominant or ("unknown" if touched else NO_FILES),
        stack=stack_label(analysis.profile.stack),
        task_category=labels.get("task_category") or UNLABELLED,
        outcome=labels.get("outcome") or UNLABELLED,
        generated_tokens=generated,
        context_tokens=card.context_tokens,
        flow_efficiency=card.flow_efficiency_tokens,
        unused_context=(
            None
            if inventory is None
            else _share(inventory.unused_tokens, inventory.retrieved_tokens)
        ),
        rework_rate=_share(by_detector[RATE_DETECTORS["rework_rate"]], generated),
        regeneration_rate=_share(
            by_detector[RATE_DETECTORS["regeneration_rate"]], generated
        ),
        exploration_rate=_share(
            by_detector[RATE_DETECTORS["exploration_rate"]], generated
        ),
    )


@dataclass(frozen=True)
class CorpusSession:
    """A session to compare: its transcript, labels and, optionally, the
    repository it started from."""

    path: Path
    labels: Mapping[str, str] = field(default_factory=dict)
    repo: Path | None = None


@dataclass(frozen=True)
class StackCorpusReport:
    """The language and stack comparisons of a corpus, with failures counted."""

    sessions: int
    analysed: int
    errors: Mapping[str, int]
    by_language: StackComparison
    by_stack: StackComparison

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": "ter.corpus-by-stack/1",
            "sessions": self.sessions,
            "analysed": self.analysed,
            "errors": dict(self.errors),
            "by_language": self.by_language.as_dict(),
            "by_stack": self.by_stack.as_dict(),
        }


#: ``explain(path, repo)``: the explained session, with repository evidence
#: when ``repo`` is given.
Explain = Callable[[Path, Path | None], ExplainedSession]


class CompareByStack:
    """Explain each session and compare them by language and by stack."""

    def __init__(
        self, explain: Explain, min_sessions: int = DEFAULT_MIN_SESSIONS
    ) -> None:
        if min_sessions < 1:
            raise ValueError(f"min_sessions must be at least 1, not {min_sessions}")
        self._explain = explain
        self._min = min_sessions

    def __call__(self, sessions: Iterable[CorpusSession]) -> StackCorpusReport:
        measures: list[SessionMeasures] = []
        errors: Counter[str] = Counter()
        total = 0
        for session in sessions:
            total += 1
            try:
                explained = self._explain(session.path, session.repo)
            except Exception as exc:  # noqa: BLE001 - a failure is a count here
                errors[type(exc).__name__] += 1
                continue
            measures.append(session_measures(explained, session.labels))
        return StackCorpusReport(
            sessions=total,
            analysed=len(measures),
            errors=dict(sorted(errors.items())),
            by_language=compare_strata(measures, Dimension.LANGUAGE, self._min),
            by_stack=compare_strata(measures, Dimension.STACK, self._min),
        )
