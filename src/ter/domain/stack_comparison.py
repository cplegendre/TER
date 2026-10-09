"""Token use and waste by language and stack, stratified (L6, TER-STK-010/011).

Sessions differ in task, size and outcome far more than in language, so a raw
comparison by language mostly measures which tasks were done in which
language. This module only compares like with like: sessions are grouped into
strata of (language or stack) x task category x outcome label, every stratum
reports its session count, and a stratum with fewer than ``min_sessions``
sessions is marked *insufficient* and reports no measure. Groups are compared
only inside one (task category, outcome) cell and only when at least two of
its groups are sufficient.

Every number is a count, a median or a ratio of token counts: nothing here
holds a prompt, a path or code, so the result can be shared from a private
corpus. Pure: no IO.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "DEFAULT_MIN_SESSIONS",
    "MEASURES",
    "UNLABELLED",
    "Dimension",
    "SessionMeasures",
    "StackComparison",
    "Stratum",
    "compare_strata",
]

#: Strata with fewer sessions than this are insufficient (no comparison).
DEFAULT_MIN_SESSIONS = 5
#: The task category and outcome of a session with no label.
UNLABELLED = "unlabelled"


class Dimension(StrEnum):
    """What sessions are grouped by."""

    LANGUAGE = "language"
    STACK = "stack"


@dataclass(frozen=True)
class SessionMeasures:
    """One session's group keys, labels and measures; content-free.

    Rates are shares of the session's generated tokens that confident waste
    findings of that detector are charged with (the scorecard allocation);
    ``unused_context`` is the share of retrieved context tokens no later event
    used. A measure is ``None`` when its denominator is zero.
    """

    language: str
    stack: str
    task_category: str
    outcome: str
    generated_tokens: int
    context_tokens: int
    flow_efficiency: float | None
    unused_context: float | None
    rework_rate: float | None
    regeneration_rate: float | None
    exploration_rate: float | None

    def group(self, dimension: Dimension) -> str:
        return self.language if dimension is Dimension.LANGUAGE else self.stack


#: Measure name -> how to read it from a session.
MEASURES: Mapping[str, Callable[[SessionMeasures], float | None]] = {
    "generated_tokens": lambda m: float(m.generated_tokens),
    "context_tokens": lambda m: float(m.context_tokens),
    "flow_efficiency": lambda m: m.flow_efficiency,
    "unused_context": lambda m: m.unused_context,
    "rework_rate": lambda m: m.rework_rate,
    "regeneration_rate": lambda m: m.regeneration_rate,
    "exploration_rate": lambda m: m.exploration_rate,
}


@dataclass(frozen=True)
class Stratum:
    """Sessions of one group, task category and outcome."""

    group: str
    task_category: str
    outcome: str
    sessions: int
    sufficient: bool
    #: Measure -> (median, sessions with a value); empty when insufficient.
    medians: Mapping[str, tuple[float | None, int]]

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "group": self.group,
            "task_category": self.task_category,
            "outcome": self.outcome,
            "sessions": self.sessions,
            "sufficient": self.sufficient,
        }
        if self.sufficient:
            out["median"] = {
                k: None if v is None else round(v, 4)
                for k, (v, _) in self.medians.items()
            }
            out["with_value"] = {k: n for k, (_, n) in self.medians.items()}
        return out


@dataclass(frozen=True)
class StackComparison:
    """Every stratum of one dimension, and the cells where groups compare."""

    dimension: Dimension
    min_sessions: int
    strata: tuple[Stratum, ...]

    @property
    def comparable_cells(self) -> tuple[tuple[str, str], ...]:
        """(task category, outcome) cells holding two or more sufficient groups."""
        counts: dict[tuple[str, str], int] = {}
        for s in self.strata:
            if s.sufficient:
                key = (s.task_category, s.outcome)
                counts[key] = counts.get(key, 0) + 1
        return tuple(sorted(k for k, n in counts.items() if n >= 2))

    def stratum(self, group: str, task_category: str, outcome: str) -> Stratum | None:
        return next(
            (
                s
                for s in self.strata
                if (s.group, s.task_category, s.outcome)
                == (group, task_category, outcome)
            ),
            None,
        )

    def as_dict(self) -> dict[str, object]:
        cells = self.comparable_cells
        return {
            "dimension": self.dimension.value,
            "min_sessions": self.min_sessions,
            "sessions": sum(s.sessions for s in self.strata),
            "strata": [s.as_dict() for s in self.strata],
            "insufficient_strata": sum(1 for s in self.strata if not s.sufficient),
            "comparisons": [
                {
                    "task_category": task,
                    "outcome": outcome,
                    "groups": [
                        s.group
                        for s in self.strata
                        if s.sufficient
                        and (s.task_category, s.outcome) == (task, outcome)
                    ],
                }
                for task, outcome in cells
            ],
        }


def _median(values: Iterable[float | None]) -> tuple[float | None, int]:
    present = [v for v in values if v is not None]
    return (statistics.median(present) if present else None, len(present))


def compare_strata(
    sessions: Iterable[SessionMeasures],
    dimension: Dimension,
    min_sessions: int = DEFAULT_MIN_SESSIONS,
) -> StackComparison:
    """Group ``sessions`` by ``dimension`` x task category x outcome.

    Raises:
        ValueError: When ``min_sessions`` is below 1.
    """
    if min_sessions < 1:
        raise ValueError(f"min_sessions must be at least 1, not {min_sessions}")
    strata: dict[tuple[str, str, str], list[SessionMeasures]] = {}
    for m in sessions:
        strata.setdefault((m.group(dimension), m.task_category, m.outcome), []).append(
            m
        )
    out = []
    for (group, task, outcome), members in sorted(strata.items()):
        sufficient = len(members) >= min_sessions
        out.append(
            Stratum(
                group=group,
                task_category=task,
                outcome=outcome,
                sessions=len(members),
                sufficient=sufficient,
                medians=(
                    {
                        name: _median(read(m) for m in members)
                        for name, read in MEASURES.items()
                    }
                    if sufficient
                    else {}
                ),
            )
        )
    return StackComparison(dimension, min_sessions, tuple(out))
