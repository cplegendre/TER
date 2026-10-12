"""L4 use case: decide on a session's signals and deliver the new advice.

The decisions are shadow mode's (:func:`decide`): a fresh Intervention
Engine judges every detector signal of the session in the order they became
known, and each verdict is one ledger record stamped with the policy file's
digest. A record the ledger already holds is not written again, so asking
again after more events writes and delivers only what is new.

:func:`follow_up_session` records the outcome of advice already given
(TER-INT-011): advice with :data:`~ter.domain.effectiveness.WINDOW` tool
events after its delivery gets its response and before-after flow measures in
the ledger, and :func:`policy_effectiveness` scores each policy from those
outcomes (TER-INT-012). The control loop (:mod:`ter.application.loop`) runs
these as its Decide, Measure and Learn stages.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.effectiveness import PolicyEffectiveness, effectiveness, follow_up
from ..domain.ledger import Intervention, LedgerRecord
from ..domain.lean import LeanAnalysis
from ..domain.lean.signals import detector_signals, tool_event_count
from ..domain.maturity import Maturity
from ..domain.policy_set import PolicySet
from ..ports.driven import Clock, InterventionLedger

__all__ = [
    "Decisions",
    "decide",
    "follow_up_session",
    "policy_effectiveness",
]


@dataclass(frozen=True)
class Decisions:
    """Every decision about one session's signals, and which were new."""

    session_id: str
    #: Every decision made, in order.
    records: tuple[LedgerRecord, ...]
    #: Every advised decision with the signal behind it, in order.
    interventions: tuple[Intervention, ...]
    #: Decisions the ledger did not already hold.
    written: tuple[LedgerRecord, ...]

    @property
    def new_interventions(self) -> tuple[Intervention, ...]:
        """Advice not given before: the advised records written this time."""
        new = {r.id for r in self.written}
        return tuple(i for i in self.interventions if i.record.id in new)


def decide(
    session_id: str,
    analysis: LeanAnalysis,
    policies: PolicySet,
    ledger: InterventionLedger,
    clock: Clock,
    ceiling: Maturity = Maturity.ADVISORY,
    delivered_at: int | None = None,
) -> Decisions:
    """Judge every signal of ``analysis`` and record the decisions.

    ``delivered_at`` is the tool event count when the advice is delivered
    live; advised records keep it, so their follow-up counts from there.

    The policies must be approved (TER-INT-015). The engine is fresh, so
    cooldowns count within the session, and the same signals under the same
    file give the same record ids.

    A live finding can gain evidence between two asks (more fragmented edits
    of one file), which gives its decision a new record id. A policy that
    already advised on that signal under this file is not asked again: the
    revised advice is neither written nor delivered, as one engine would
    never advise a signal twice by one policy.
    """
    policies.require_approved()
    version = policies.digest()
    engine = policies.engine(ceiling)
    entries = ledger.entries(session_id)
    known = {e.id for e in entries}
    given = {
        (e.policy, e.signal_id)
        for e in entries
        if e.delivered and e.policy_version == version
    }
    records: list[LedgerRecord] = []
    interventions: list[Intervention] = []
    written: list[LedgerRecord] = []
    for signal in detector_signals(analysis):
        for verdict in engine.apply(signal):
            record = LedgerRecord.from_verdict(
                verdict, session_id, clock.now(), version, delivered_at
            )
            if record.id not in known and record.delivered:
                if (record.policy, record.signal_id) in given:
                    continue  # advised before, on an earlier revision
                given.add((record.policy, record.signal_id))
            if record.id not in known:
                ledger.record(record)
                known.add(record.id)
                written.append(record)
            records.append(record)
            if record.delivered:
                interventions.append(Intervention(record, signal))
    return Decisions(session_id, tuple(records), tuple(interventions), tuple(written))


def follow_up_session(
    session_id: str,
    analysis: LeanAnalysis,
    policies: PolicySet,
    ledger: InterventionLedger,
    clock: Clock,
) -> tuple[LedgerRecord, ...]:
    """Record the outcome of every delivered intervention now observable.

    Returns the records observed this time, with their outcomes.
    """
    signals = detector_signals(analysis)
    tool_events = tool_event_count(analysis)
    # A record made under another policy file is measured on its own
    # detector only: the current file may answer different detectors.
    version = policies.digest()
    detectors = {p.id: p.detectors for p in policies.policies}
    observed: list[LedgerRecord] = []
    for record in ledger.entries(session_id):
        observation = follow_up(
            record,
            signals,
            tool_events,
            detectors.get(record.policy, frozenset())
            if record.policy_version == version
            else frozenset(),
            clock.now(),
        )
        if observation is not None:
            ledger.observe(session_id, record.id, observation)
            observed.append(record.observed(observation))
    return tuple(observed)


def policy_effectiveness(ledger: InterventionLedger) -> tuple[PolicyEffectiveness, ...]:
    """Each policy's effectiveness over every session in ``ledger`` (TER-INT-012)."""
    return effectiveness(
        record for session in ledger.sessions() for record in ledger.entries(session)
    )
