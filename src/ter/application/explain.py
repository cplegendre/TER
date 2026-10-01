"""L2 use case: explain a recorded session and assemble its A3.

The analysis is the same fold the L1 engine runs live
(:meth:`ter.domain.stream.AnalysisEngine.explain`), so a session explained
afterwards and one explained while it runs agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..domain.events import EventKind, SessionTrace
from ..domain.lean import A3Report, LeanAnalysis, TerMeasure, build_a3
from ..domain.stream import explain_batch
from ..ports.driven import SessionSource, TerScorer, Tokenizer

__all__ = ["ExplainSession", "ExplainedSession"]


@dataclass(frozen=True)
class ExplainedSession:
    trace: SessionTrace
    analysis: LeanAnalysis
    a3: A3Report


class ExplainSession:
    """Read a session, explain it, and build its A3. TER is optional."""

    def __init__(
        self,
        source: SessionSource,
        tokenizer: Tokenizer,
        scorer: TerScorer | None = None,
    ) -> None:
        self._source = source
        self._tokenizer = tokenizer
        self._scorer = scorer

    def __call__(self, ref: str | Path) -> ExplainedSession:
        trace = self._source.read(ref)
        ter = (
            TerMeasure(self._scorer.score(ref), self._scorer.method)
            if self._scorer is not None
            else None
        )
        analysis = explain_batch(trace.events, self._tokenizer, ter=ter)
        intents = tuple(e.text for e in trace.events if e.kind is EventKind.PROMPT)
        return ExplainedSession(trace, analysis, build_a3(analysis, intents))
