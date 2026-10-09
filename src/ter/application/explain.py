"""L2 use case: explain a recorded session and assemble its A3.

The analysis is the same fold the L1 engine runs live
(:meth:`ter.domain.stream.AnalysisEngine.explain`), so a session explained
afterwards and one explained while it runs agree.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..domain.events import EventKind, SessionTrace
from ..domain.lean import A3Report, LeanAnalysis, TerMeasure, build_a3
from ..domain.lean.grounding import RepositoryGrounding
from ..domain.outcome import (
    AcceptanceContract,
    OutcomeVerdict,
    judge,
)
from ..ports.driven import (
    ArchitectureContracts,
    OutcomeSource,
    PriceBook,
    RepositoryEvidence,
    SessionSource,
    TerScorer,
    Tokenizer,
)
from .ground import ground_session
from .observe import IngestFactory, fresh_ingest, ingest_all
from .stack import read_stack

__all__ = ["ExplainSession", "ExplainedSession"]


@dataclass(frozen=True)
class ExplainedSession:
    trace: SessionTrace
    analysis: LeanAnalysis
    a3: A3Report
    outcome: OutcomeVerdict | None = None
    #: The repository evidence the analysis was grounded on (L3), if any.
    grounding: RepositoryGrounding | None = None


class ExplainSession:
    """Read a session, explain it, and build its A3. TER is optional.

    With an outcome source and a run reference, the run's outcome is judged
    against the acceptance contract (default: every recorded check passes)
    after the analysis is complete, and shown beside it. The analysis never
    sees the verdict (point 5).

    With a price book, the A3 also prices the session and its context
    inventory at the prices in force on the session date (TER-ANL-040).

    With repository evidence (L3: the repository as it was when the session
    started), the analysis is grounded on it: every task's expected change
    surface, edits outside it, and, with a contracts reader, imports that
    break the repository's declared architecture contracts, and the stack
    its manifests declare (TER-STK-002). Without it the explanation is
    exactly the L2 one; the session's languages come from file names either
    way (TER-STK-001).
    """

    def __init__(
        self,
        source: SessionSource,
        tokenizer: Tokenizer,
        scorer: TerScorer | None = None,
        outcomes: OutcomeSource | None = None,
        prices: PriceBook | None = None,
        ingest: IngestFactory | None = None,
        repository: RepositoryEvidence | None = None,
        contracts: ArchitectureContracts
        | Sequence[ArchitectureContracts]
        | None = None,
    ) -> None:
        self._source = source
        self._repository = repository
        self._contracts = contracts
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
        grounding = (
            ground_session(trace.events, self._repository, self._contracts)
            if self._repository is not None
            else None
        )
        analysis = (
            ingest.explain(trace.session_id, ter=ter)
            if grounding is None
            else ingest.explain(trace.session_id, ter=ter, repository=grounding)
        )
        if self._repository is not None:
            # The stack is a fact about the repository, not about events:
            # joined to the folded profile once, after the analysis (L3).
            analysis = replace(
                analysis,
                profile=replace(analysis.profile, stack=read_stack(self._repository)),
            )
        verdict = self._judge(outcome_ref, contract)
        intents = tuple(e.text for e in trace.events if e.kind is EventKind.PROMPT)
        return ExplainedSession(
            trace,
            analysis,
            build_a3(
                analysis, intents, verdict, trace.usage_limits, prices=self._prices
            ),
            verdict,
            grounding,
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
