"""L4 use case: decide on a session's signals and deliver the new advice.

The decisions are shadow mode's (:func:`decide`): a fresh Intervention
Engine judges every detector signal of the session in the order they became
known, and each verdict is one ledger record stamped with the policy file's
digest. A record the ledger already holds is not written again, so asking
again after more events writes and delivers only what is new. Live advice
(:func:`advise_session`) hands each new advised record to an
:class:`~ter.ports.driven.InterventionChannel` (TER-INT-003): advice the
developer sees, never a change to the agent's input (TER-INT-002), always
with the signal and evidence behind it (TER-INT-014).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.ledger import Intervention, LedgerRecord
from ..domain.lean import LeanAnalysis
from ..domain.lean.signals import detector_signals
from ..domain.maturity import Maturity
from ..domain.policy_set import PolicySet
from ..ports.driven import Clock, InterventionChannel, InterventionLedger

__all__ = ["Decisions", "advise_session", "decide"]


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
) -> Decisions:
    """Judge every signal of ``analysis`` and record the decisions.

    The policies must be approved (TER-INT-015). The engine is fresh, so
    cooldowns count within the session, and the same signals under the same
    file give the same record ids.
    """
    policies.require_approved()
    version = policies.digest()
    engine = policies.engine(ceiling)
    known = {e.id for e in ledger.entries(session_id)}
    records: list[LedgerRecord] = []
    interventions: list[Intervention] = []
    written: list[LedgerRecord] = []
    for signal in detector_signals(analysis):
        for verdict in engine.apply(signal):
            record = LedgerRecord.from_verdict(
                verdict, session_id, clock.now(), version
            )
            if record.id not in known:
                ledger.record(record)
                known.add(record.id)
                written.append(record)
            records.append(record)
            if record.delivered:
                interventions.append(Intervention(record, signal))
    return Decisions(session_id, tuple(records), tuple(interventions), tuple(written))


def advise_session(
    session_id: str,
    analysis: LeanAnalysis,
    policies: PolicySet,
    ledger: InterventionLedger,
    channel: InterventionChannel,
    clock: Clock,
    ceiling: Maturity = Maturity.ADVISORY,
) -> Decisions:
    """Decide on the session's signals and deliver the advice not given before.

    Each new advised record is written to the ledger before it is delivered,
    so advice is never delivered twice, even if delivery fails part way.
    """
    decisions = decide(session_id, analysis, policies, ledger, clock, ceiling)
    for intervention in decisions.new_interventions:
        channel.deliver(intervention)
    return decisions
