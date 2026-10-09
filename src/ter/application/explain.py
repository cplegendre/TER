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
from ..domain.outcome import (
    AcceptanceContract,
    OutcomeVerdict,
    judge,
)
from ..ports.driven import (
    OutcomeSource,
    PriceBook,
    SessionSource,
    TerScorer,
    Tokenizer,
)
from .observe import IngestFactory, fresh_ingest, ingest_all

__all__ = ["ExplainSession", "ExplainedSession"]


@dataclass(frozen=True)
class ExplainedSession:
    trace: SessionTrace
    analysis: LeanAnalysis
    a3: A3Report
    outcome: OutcomeVerdict | None = None


class ExplainSession:
    """Read a session, explain it, and build its A3. TER is optional.

    With an outcome source and a run reference, the run's outcome is judged
    against the acceptance contract (default: every recorded check passes)
    after the analysis is complete, and shown beside it. The analysis never
    sees the verdict (point 5).

    With a price book, the A3 also prices the session and its context
    inventory at the prices in force on the session date (TER-ANL-040).
    """

    def __init__(
        self,
        source: SessionSource,
        tokenizer: Tokenizer,
        scorer: TerScorer | None = None,
        outcomes: OutcomeSource | None = None,
        prices: PriceBook | None = None,
        ingest: IngestFactory | None = None,
    ) -> None:
        self._source = source
        self._ingest = fresh_ingest(tokenizer, ingest)
        self._scorer = scorer
        self._outcomes = outcomes
        self._prices = prices

    def __call__(
        self,
        ref: str | Path,
        outcome_ref: str | Path | None = None,
        contract: AcceptanceContract | None = None,
    ) -> ExplainedSession:
        trace = self._source.read(ref)
        ter = (
            TerMeasure(self._scorer.score(ref), self._scorer.method)
            if self._scorer is not None
            else None
        )
        # The recording enters analysis through EventIngest, like a live
        # session (TER-OBS-001).
        ingest = ingest_all(self._ingest(), trace.events)
        analysis = ingest.explain(trace.session_id, ter=ter)
        verdict = self._judge(outcome_ref, contract)
        intents = tuple(e.text for e in trace.events if e.kind is EventKind.PROMPT)
        return ExplainedSession(
            trace,
            analysis,
            build_a3(
                analysis, intents, verdict, trace.usage_limits, prices=self._prices
            ),
            verdict,
        )

    def _judge(
        self, outcome_ref: str | Path | None, contract: AcceptanceContract | None
    ) -> OutcomeVerdict | None:
        if outcome_ref is None:
            return None
        if self._outcomes is None:
            raise ValueError(
                f"{outcome_ref}: an outcome was asked for but no outcome source is wired"
            )
        evidence = self._outcomes.outcome(outcome_ref)
        return None if evidence is None else judge(evidence, contract)
