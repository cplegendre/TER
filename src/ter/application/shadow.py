"""L4 use case: shadow mode (TER-INT-017).

Replay recorded sessions through the approved policies and write what TER
*would* have advised to the intervention ledger, delivering nothing. Each
session is explained exactly as ``python -m ter explain`` does, its findings
become detector signals, and a fresh Intervention Engine (cooldowns are per
session) judges them in the order they became known. Every verdict, the
advice and every suppression with its reason, is one ledger record.

Shadow mode is where a policy is judged on real sessions before any advice
reaches a developer: the advised records are what a person reviews.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from ..domain.ledger import LedgerRecord
from ..domain.lean.signals import detector_signals
from ..domain.policy_set import PolicySet
from ..domain.signal import Signal
from ..ports.driven import Clock, InterventionLedger
from .explain import ExplainedSession

__all__ = ["ShadowAdvice", "ShadowRun", "shadow_sessions"]


@dataclass(frozen=True)
class ShadowAdvice:
    """One piece of advice TER would have given, with the signal behind it.

    The signal's title and subject (a file path) are kept here for a person
    judging the advice; the ledger record never holds them.
    """

    record: LedgerRecord
    signal: Signal


@dataclass(frozen=True)
class ShadowRun:
    #: Every session replayed, in order.
    sessions: tuple[str, ...]
    #: Every ledger record written, in order.
    records: tuple[LedgerRecord, ...]
    advice: tuple[ShadowAdvice, ...]
    #: ``(session ref, error)`` for every session that could not be explained.
    failures: tuple[tuple[str, str], ...]

    def outcomes(self) -> Counter[tuple[str, str]]:
        """Records per (policy, outcome)."""
        return Counter((r.policy, r.outcome.value) for r in self.records)


def shadow_sessions(
    refs: Iterable[Path],
    explain: Callable[[Path], ExplainedSession],
    policies: PolicySet,
    ledger: InterventionLedger,
    clock: Clock,
) -> ShadowRun:
    """Replay each session through ``policies`` into ``ledger``.

    The policies must be approved (TER-INT-015): an unapproved or changed
    file raises before any session is read. A session that cannot be
    explained is recorded as a failure; the others are still replayed.
    """
    policies.require_approved()
    sessions: list[str] = []
    records: list[LedgerRecord] = []
    advice: list[ShadowAdvice] = []
    failures: list[tuple[str, str]] = []
    for ref in refs:
        try:
            explained = explain(ref)
        except Exception as exc:  # noqa: BLE001 - one bad session never stops a corpus
            failures.append((str(ref), f"{type(exc).__name__}: {exc}"))
            continue
        session_id = explained.trace.session_id or ref.stem
        sessions.append(session_id)
        engine = policies.engine()
        for signal in detector_signals(explained.analysis):
            for verdict in engine.apply(signal):
                record = LedgerRecord.from_verdict(verdict, session_id, clock.now())
                ledger.record(record)
                records.append(record)
                if record.delivered:
                    advice.append(ShadowAdvice(record, signal))
    return ShadowRun(tuple(sessions), tuple(records), tuple(advice), tuple(failures))
