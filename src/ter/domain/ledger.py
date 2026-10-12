"""L4 Advisory: the intervention ledger's records (TER-INT-009).

Every decision the Intervention Engine makes about a signal under a policy
becomes one :class:`LedgerRecord`: the triggering signal, the evidence it
cites, the action taken (advice, or none when a guard suppressed it, with
the reason) and, once observed, the outcome (points 175 to 178, 180). The
ledger is how interventions are audited, and how later steps score each
policy from what happened after it advised (TER-INT-011, TER-INT-012).

Records are shareable like the corpus reports: they cite event ids, the
detector, its published rule and the policy, never prompt text, file
content, or a finding's title or subject. (A signal id can name a path when
one step touched several files.)

A record's id is a digest of the decision (session, policy and the digest
of the policy file that made it, signal id, revision and outcome), so
replaying the same signals under the same policies writes the same ids, a
revised policy writes new ones, and a ledger keeps the first record for each
id.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from .intervention import Action, AdviceKind, Outcome, Verdict
from .signal import Signal

__all__ = [
    "LEDGER_SCHEMA",
    "Intervention",
    "LedgerError",
    "LedgerRecord",
    "Observation",
    "Response",
    "record_id",
]

LEDGER_SCHEMA = "ter.intervention-ledger/1"


class LedgerError(ValueError):
    """A ledger record that is malformed, or an observation it cannot take."""


class Response(StrEnum):
    """What the developer did after advice (point 167, TER-INT-011)."""

    ACCEPTED = "accepted"
    IGNORED = "ignored"
    REJECTED = "rejected"


def _measures(value: Mapping[str, float], what: str) -> Mapping[str, float]:
    measures: dict[str, float] = {}
    for name, number in value.items():
        if not isinstance(name, str) or not name:
            raise LedgerError(f"{what}: measure names must be non-empty strings")
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise LedgerError(f"{what}: measure {name!r} must be a number")
        measures[name] = float(number)
    return MappingProxyType(measures)


@dataclass(frozen=True)
class Observation:
    """The observed outcome of one delivered intervention (point 178).

    ``tool_events`` is how many tool events after the advice it was observed;
    ``before`` and ``after`` are flow measures around the advice (point 168).
    Which measures, and the 10-event window, are TER-INT-011's.
    """

    response: Response
    observed_at: datetime
    tool_events: int
    before: Mapping[str, float] = field(default_factory=dict)
    after: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.tool_events, bool) or self.tool_events < 0:
            raise LedgerError("an observation's tool_events must be >= 0")
        object.__setattr__(self, "before", _measures(self.before, "before"))
        object.__setattr__(self, "after", _measures(self.after, "after"))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Observation):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def __hash__(self) -> int:
        return hash(json.dumps(self.as_dict(), sort_keys=True))

    def as_dict(self) -> dict[str, Any]:
        return {
            "response": self.response.value,
            "observed_at": self.observed_at.isoformat(),
            "tool_events": self.tool_events,
            "before": dict(sorted(self.before.items())),
            "after": dict(sorted(self.after.items())),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Observation:
        try:
            return cls(
                response=Response(data["response"]),
                observed_at=datetime.fromisoformat(data["observed_at"]),
                tool_events=_int(data["tool_events"], "tool_events"),
                before=dict(data.get("before") or {}),
                after=dict(data.get("after") or {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, LedgerError):
                raise
            raise LedgerError(f"not a ledger observation: {exc}") from None


def record_id(
    session_id: str,
    policy: str,
    signal_id: str,
    revision: tuple[float, tuple[str, ...]],
    outcome: Outcome,
    policy_version: str = "",
) -> str:
    """The id of one decision: the same decision always gets the same id.

    ``policy_version`` is the digest of the policy file, so the same signal
    judged under a revised file is a different decision.
    """
    confidence, evidence = revision
    key = json.dumps(
        [
            session_id,
            policy,
            policy_version,
            signal_id,
            confidence,
            list(evidence),
            outcome.value,
        ]
    )
    return "int-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class LedgerRecord:
    """One decision about one signal under one policy (TER-INT-009).

    ``action`` is the action taken: :attr:`Action.ADVISE` when the policy
    advised, ``None`` when a guard suppressed it (``outcome`` and ``reason``
    say which and why). An action is only ever recorded with the evidence
    behind it. ``observation`` is the outcome, attached once observed.
    """

    id: str
    session_id: str
    recorded_at: datetime
    policy: str
    signal_id: str
    detector: str
    waste: str
    kind: str
    confidence: float
    rule: str
    position: int
    evidence: tuple[str, ...]
    outcome: Outcome
    reason: str
    action: Action | None = None
    advice_kind: AdviceKind | None = None
    advice: str = ""
    #: The digest of the policy file that made the decision, when known.
    policy_version: str = ""
    observation: Observation | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.session_id or not self.policy:
            raise LedgerError("a ledger record needs an id, a session and a policy")
        if not self.signal_id or not self.detector:
            raise LedgerError(f"record {self.id} names no signal")
        if (self.action is not None) != (self.outcome is Outcome.ADVISED):
            raise LedgerError(
                f"record {self.id}: an action is recorded exactly when the "
                "policy advised"
            )
        if self.action is not None:
            if not self.evidence:
                raise LedgerError(
                    f"record {self.id}: no intervention without evidence (TER-INT-007)"
                )
            if self.advice_kind is None or not self.advice.strip():
                raise LedgerError(f"record {self.id}: advice needs its kind and text")
        if self.observation is not None and self.action is None:
            raise LedgerError(
                f"record {self.id}: only a delivered intervention has an outcome"
            )

    @classmethod
    def from_verdict(
        cls,
        verdict: Verdict,
        session_id: str,
        recorded_at: datetime,
        policy_version: str = "",
    ) -> LedgerRecord:
        """The record of one engine verdict, under the policy file whose
        digest is ``policy_version``."""
        signal = verdict.signal
        advice = verdict.advice
        return cls(
            id=record_id(
                session_id,
                verdict.policy,
                signal.id,
                signal.revision,
                verdict.outcome,
                policy_version,
            ),
            session_id=session_id,
            recorded_at=recorded_at,
            policy=verdict.policy,
            signal_id=signal.id,
            detector=signal.detector,
            waste=signal.waste,
            kind=signal.kind,
            confidence=signal.confidence,
            rule=signal.rule,
            position=signal.position,
            evidence=signal.evidence,
            outcome=verdict.outcome,
            reason=verdict.reason,
            action=Action.ADVISE if verdict.advised else None,
            advice_kind=advice.kind if advice is not None else None,
            advice=advice.message if advice is not None else "",
            policy_version=policy_version,
        )

    @property
    def delivered(self) -> bool:
        """True when the policy advised: an intervention, not a suppression."""
        return self.action is not None

    def observed(self, observation: Observation) -> LedgerRecord:
        """This record with its outcome; an outcome is recorded once."""
        if not self.delivered:
            raise LedgerError(
                f"record {self.id} was suppressed ({self.outcome.value}): there "
                "is no intervention to observe"
            )
        if self.observation is not None:
            raise LedgerError(f"record {self.id} already has its outcome")
        return replace(self, observation=observation)

    def as_dict(self) -> dict[str, Any]:
        """The record without its observation, as written to a ledger."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "recorded_at": self.recorded_at.isoformat(),
            "policy": self.policy,
            "policy_version": self.policy_version,
            "signal": {
                "id": self.signal_id,
                "detector": self.detector,
                "waste": self.waste,
                "kind": self.kind,
                "confidence": self.confidence,
                "rule": self.rule,
                "position": self.position,
            },
            "evidence": list(self.evidence),
            "outcome": self.outcome.value,
            "reason": self.reason,
            "action": self.action.value if self.action is not None else None,
            "advice": None
            if self.advice_kind is None
            else {"kind": self.advice_kind.value, "message": self.advice},
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> LedgerRecord:
        """Read a record written by :meth:`as_dict` (no observation)."""
        try:
            signal = data["signal"]
            advice = data.get("advice")
            action = data.get("action")
            evidence = data["evidence"]
            if not isinstance(evidence, list) or not all(
                isinstance(e, str) for e in evidence
            ):
                raise LedgerError("evidence must be a list of event ids")
            return cls(
                id=str(data["id"]),
                session_id=str(data["session_id"]),
                recorded_at=datetime.fromisoformat(data["recorded_at"]),
                policy=str(data["policy"]),
                policy_version=str(data.get("policy_version") or ""),
                signal_id=str(signal["id"]),
                detector=str(signal["detector"]),
                waste=str(signal["waste"]),
                kind=str(signal["kind"]),
                confidence=_number(signal["confidence"], "confidence"),
                rule=str(signal["rule"]),
                position=_int(signal["position"], "position"),
                evidence=tuple(evidence),
                outcome=Outcome(data["outcome"]),
                reason=str(data["reason"]),
                action=None if action is None else Action(action),
                advice_kind=None if advice is None else AdviceKind(advice["kind"]),
                advice="" if advice is None else str(advice["message"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, LedgerError):
                raise
            raise LedgerError(f"not a ledger record: {exc!r}") from None


def _int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LedgerError(f"{what} must be an integer")
    return value


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LedgerError(f"{what} must be a number")
    return float(value)


#: Evidence ids a message names before it counts the rest.
_EVIDENCE_SHOWN = 4


@dataclass(frozen=True)
class Intervention:
    """One piece of advice to deliver: an advised record and its signal.

    The record is what the ledger keeps; the signal adds the finding's title
    and subject, which the developer sees but the ledger never holds.
    """

    record: LedgerRecord
    signal: Signal

    def __post_init__(self) -> None:
        if not self.record.delivered:
            raise LedgerError(
                f"record {self.record.id} was not advised: nothing to deliver"
            )
        if self.record.signal_id != self.signal.id:
            raise LedgerError(
                f"record {self.record.id} is about {self.record.signal_id}, "
                f"not {self.signal.id}"
            )

    def message(self) -> str:
        """The advice with the signal and evidence behind it (TER-INT-014)."""
        record = self.record
        kind = record.advice_kind.value if record.advice_kind else "advice"
        shown = ", ".join(record.evidence[:_EVIDENCE_SHOWN])
        more = len(record.evidence) - _EVIDENCE_SHOWN
        evidence = shown + (f" and {more} more" if more > 0 else "")
        return (
            f"TER {kind} ({record.policy}): {record.advice}\n"
            f"Why: {self.signal.title} ({record.detector}, confidence "
            f"{record.confidence:.2f}: {record.rule})\n"
            f"Evidence: {evidence}"
        )
