"""L4 use case: one turn of the control loop (TER-INT-013).

TER's central control flow is Observe, Analyse, Decide, Intervene, Measure,
Learn (:mod:`ter.domain.loop`). :class:`ControlLoop` runs those stages, in
that order, for one session each time it is asked (the live hook asks at
every ``Stop``):

1. **Observe** the session's events from the :class:`~ter.ports.driven.EventLog`;
2. **Analyse** them through a fresh :class:`~ter.ports.driving.EventIngest`,
   exactly as a recorded session is explained, and, given control limits,
   place the session against them: each firing control signal becomes a
   signal a policy can name (TER-SPC-020);
3. **Decide** on every detector signal under the approved policy file
   (:func:`~ter.application.advise.decide`), writing each verdict to the
   ledger;
4. **Intervene**: hand the advice not given before to the
   :class:`~ter.ports.driven.InterventionChannel`;
5. **Measure** the outcome of earlier advice whose window has passed
   (:func:`~ter.application.advise.follow_up_session`);
6. **Learn**: score every policy from the ledger's outcomes
   (:func:`~ter.application.advise.policy_effectiveness`) and report the
   ones to recalibrate. TER never changes a policy itself: a person edits
   and re-approves the file (TER-INT-015). Learn reads the whole ledger and
   its report is for a person, so a caller that cannot show it (the hook)
   turns it off with ``learn=False`` and a person runs it on demand
   (``python -m ter policy effectiveness``). Since it always reads every
   outcome recorded so far, a Learn that failed or was skipped loses nothing.

Without a channel the loop is in shadow (``python -m ter policy shadow``):
it decides and records but delivers nothing, so it has nothing to measure
or learn from.

A failure up to Intervene raises, so a caller that must fail open (the
hook) gives no advice. Measure and Learn run after the advice is in the
channel: their failures are kept on the turn instead, so they never cost
advice already decided.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from ..domain.effectiveness import PolicyEffectiveness
from ..domain.events import Event
from ..domain.lean import LeanAnalysis
from ..domain.lean.control import ControlLimits, session_control
from ..domain.lean.signals import control_signals, tool_event_count
from ..domain.signal import Signal
from ..domain.ledger import Intervention, LedgerRecord
from ..domain.loop import Stage
from ..domain.maturity import Maturity
from ..domain.policy_set import PolicySet
from ..ports.driven import Clock, EventLog, InterventionChannel, InterventionLedger
from .advise import Decisions, decide, follow_up_session, policy_effectiveness
from .observe import IngestFactory, ingest_all

__all__ = ["ControlLoop", "LoopTurn", "advise_session"]

T = TypeVar("T")


@dataclass(frozen=True)
class LoopTurn:
    """What one turn of the loop did, stage by stage."""

    session_id: str
    #: The stages that ran, in order.
    stages: tuple[Stage, ...]
    #: Tool events observed: the clock Measure's windows count in.
    tool_events: int
    #: Every decision about the session's signals (Decide).
    decisions: Decisions
    #: Advice handed to the channel this turn (Intervene).
    delivered: tuple[Intervention, ...]
    #: Records whose outcome was recorded this turn (Measure).
    observed: tuple[LedgerRecord, ...]
    #: Every policy's effectiveness, when Learn ran.
    learned: tuple[PolicyEffectiveness, ...]
    #: ``(stage, error)`` for a Measure or Learn stage that failed.
    errors: tuple[tuple[Stage, str], ...] = ()
    #: The session's firing control signals, when the loop has limits.
    control: tuple[Signal, ...] = ()
    #: True when the limits came from another detector set, so no control
    #: signal was used (TER-SPC-007).
    stale_limits: bool = False

    @property
    def recalibrate(self) -> tuple[PolicyEffectiveness, ...]:
        """The policies Learn flagged for a person to recalibrate or remove."""
        return tuple(p for p in self.learned if p.recalibrate)


class ControlLoop:
    """Observe, Analyse, Decide, Intervene, Measure, Learn for one session.

    Without a channel the loop is in shadow: Intervene, Measure and Learn
    are skipped. ``learn=False`` skips Learn only.
    """

    def __init__(
        self,
        policies: PolicySet,
        ledger: InterventionLedger,
        clock: Clock,
        channel: InterventionChannel | None = None,
        ceiling: Maturity = Maturity.ADVISORY,
        *,
        learn: bool = True,
        limits: ControlLimits | None = None,
    ) -> None:
        self._policies = policies
        self._learn = learn
        self._limits = limits
        self._ledger = ledger
        self._clock = clock
        self._channel = channel
        self._ceiling = ceiling

    @property
    def live(self) -> bool:
        """True when advice is delivered; False in shadow."""
        return self._channel is not None

    def turn(self, session_id: str, log: EventLog, ingest: IngestFactory) -> LoopTurn:
        """Run every stage once for ``session_id``, observing ``log``.

        The policies must be approved (TER-INT-015): an unapproved file
        raises before any event is read.
        """
        self._policies.require_approved()
        events = self.observe(session_id, log)
        analysis = self.analyse(session_id, events, ingest)
        return self._act(session_id, analysis, [Stage.OBSERVE, Stage.ANALYSE])

    def act(self, session_id: str, analysis: LeanAnalysis) -> LoopTurn:
        """Run the stages from Decide on an analysis already made."""
        self._policies.require_approved()
        return self._act(session_id, analysis, [])

    def _act(
        self,
        session_id: str,
        analysis: LeanAnalysis,
        stages: list[Stage],
    ) -> LoopTurn:
        events = tool_event_count(analysis)
        placed, stale = self.place(analysis)
        control = placed or ()
        decisions = self.decide(session_id, analysis, control)
        stages.append(Stage.DECIDE)
        if not self.live:
            return LoopTurn(
                session_id,
                tuple(stages),
                events,
                decisions,
                (),
                (),
                (),
                control=control,
                stale_limits=stale,
            )
        delivered = self.intervene(decisions)
        stages.append(Stage.INTERVENE)
        errors: list[tuple[Stage, str]] = []
        observed = self._after(
            Stage.MEASURE,
            stages,
            errors,
            lambda: self.measure(session_id, analysis, placed),
        )
        learned = (
            self._after(Stage.LEARN, stages, errors, self.learn) if self._learn else ()
        )
        return LoopTurn(
            session_id,
            tuple(stages),
            events,
            decisions,
            delivered,
            observed,
            learned,
            tuple(errors),
            control,
            stale,
        )

    @staticmethod
    def observe(session_id: str, log: EventLog) -> tuple[Event, ...]:
        """Observe: the session's recorded events, in order."""
        return log.events(session_id)

    @staticmethod
    def analyse(
        session_id: str, events: tuple[Event, ...], ingest: IngestFactory
    ) -> LeanAnalysis:
        """Analyse: fold the events into the session's Lean analysis."""
        return ingest_all(ingest(), events).explain(session_id)

    def place(self, analysis: LeanAnalysis) -> tuple[tuple[Signal, ...] | None, bool]:
        """Part of Analyse: the session against its control limits.

        Returns the firing control signals as signals (TER-SPC-020), or
        ``None`` when they cannot be judged (no limits, or stale ones), and
        whether the limits are stale. Live, the session is unfinished, so
        only the measures it can be judged on mid-session are placed.
        """
        if self._limits is None:
            return None, False
        placed = session_control(analysis, self._limits)
        if placed.stale:
            return None, True
        return control_signals(placed, analysis, live=self.live), False

    def decide(
        self,
        session_id: str,
        analysis: LeanAnalysis,
        control: tuple[Signal, ...] = (),
    ) -> Decisions:
        """Decide: judge every signal and record each verdict in the ledger.

        Live, advised records keep the tool event count they are delivered
        at, so Measure counts their window from there.
        """
        return decide(
            session_id,
            analysis,
            self._policies,
            self._ledger,
            self._clock,
            self._ceiling,
            delivered_at=tool_event_count(analysis) if self.live else None,
            control=control,
        )

    def intervene(self, decisions: Decisions) -> tuple[Intervention, ...]:
        """Intervene: deliver the advice not given before.

        Each record is already in the ledger, so advice is never delivered
        twice, even if delivery fails part way.
        """
        assert self._channel is not None  # live
        new = decisions.new_interventions
        for intervention in new:
            self._channel.deliver(intervention)
        return new

    def measure(
        self,
        session_id: str,
        analysis: LeanAnalysis,
        control: tuple[Signal, ...] | None = None,
    ) -> tuple[LedgerRecord, ...]:
        """Measure: record the outcome of earlier advice now observable.

        Advice on a control signal waits while ``control`` is ``None``.
        """
        return follow_up_session(
            session_id, analysis, self._policies, self._ledger, self._clock, control
        )

    def learn(self) -> tuple[PolicyEffectiveness, ...]:
        """Learn: every policy's effectiveness from the ledger's outcomes."""
        return policy_effectiveness(self._ledger)

    @staticmethod
    def _after(
        stage: Stage,
        stages: list[Stage],
        errors: list[tuple[Stage, str]],
        run: Callable[[], tuple[T, ...]],
    ) -> tuple[T, ...]:
        """Run a stage after Intervene, keeping its failure on the turn."""
        try:
            result = run()
        except Exception as exc:  # noqa: BLE001 - never cost advice already decided
            errors.append((stage, f"{type(exc).__name__}: {exc}"))
            result = ()
        stages.append(stage)
        return result


def advise_session(
    session_id: str,
    analysis: LeanAnalysis,
    policies: PolicySet,
    ledger: InterventionLedger,
    channel: InterventionChannel,
    clock: Clock,
    ceiling: Maturity = Maturity.ADVISORY,
) -> Decisions:
    """Live advice on an analysis already made: the loop from Decide on.

    Delivers the advice not given before (TER-INT-003): advice the developer
    sees, never a change to the agent's input (TER-INT-002), always with the
    signal and evidence behind it (TER-INT-014). Then follows up earlier
    advice and, when that recorded new outcomes, scores the policies.
    """
    loop = ControlLoop(policies, ledger, clock, channel, ceiling)
    return loop.act(session_id, analysis).decisions
