"""L4 Advisory: the Intervention Engine's signals, policies and guards.

The Intervention Engine is separate from the Analysis Engine (TER-INT-006,
points 121, 161, 162): it reads only :class:`Signal` values, structured
detector signals that an analysis exports (:mod:`ter.domain.lean.signals`),
and never events, steps or findings. An import-linter contract keeps this
module free of ``ter.domain.lean`` and ``ter.domain.stream``.

A :class:`Policy` names the detectors it answers, the evidence and confidence
a signal needs, a cooldown and a hysteresis threshold, and the advice to
give. :class:`InterventionEngine` folds signals in the order they became
known and returns one :class:`Verdict` per (signal, policy) pair, so every
suppression is recorded with its reason, not only the advice. The guards run
in a fixed order:

1. **Ceiling** (TER-INT-002): below L4 Advisory nothing is advised.
2. **Evidence** (TER-INT-007, points 139, 140): a signal citing no event
   (nothing beyond a token count) is never advised on.
3. **Eligibility** (TER-INT-010, point 91): only detectors the calibration
   policy marks eligible, after a judged sample with no known false
   positives, are advised on.
4. **Confidence**: an uncertain signal (below 0.70) counts as waste until
   verified (ADR 0006) but never triggers an intervention, and a policy may
   ask for more.
5. **Hysteresis and cooldown** (TER-INT-016, points 165, 166): a policy
   fires once ``min_signals`` matching signals arrived since it last fired,
   and stays silent for ``cooldown_tool_events`` tool events after it fires.

Folding is idempotent by signal id, and :func:`decide` (batch) equals the
same signals applied one at a time (incremental).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from .maturity import Maturity
from .signal import Signal

__all__ = [
    "ADVICE_CONFIDENCE",
    "Advice",
    "AdviceKind",
    "Eligibility",
    "InterventionEngine",
    "Outcome",
    "Policy",
    "PolicyError",
    "Signal",
    "Verdict",
    "decide",
]

#: The confidence below which a finding is uncertain (``UNCERTAIN_BELOW`` in
#: the Lean model). Uncertain signals are never advised on, whatever a policy
#: says; repeated here so the engine imports nothing from the analysis.
ADVICE_CONFIDENCE = 0.70


class PolicyError(ValueError):
    """A policy that could advise on a token count alone, or is malformed."""


class AdviceKind(StrEnum):
    """What a policy's advice is (TER-INT-003)."""

    WARNING = "warning"
    RECOMMENDATION = "recommendation"


class Outcome(StrEnum):
    """What the engine did with one signal under one policy."""

    ADVISED = "advised"
    BELOW_CEILING = "below_ceiling"
    NO_EVIDENCE = "no_evidence"
    INELIGIBLE = "ineligible"
    LOW_CONFIDENCE = "low_confidence"
    BELOW_HYSTERESIS = "below_hysteresis"
    COOLDOWN = "cooldown"


@dataclass(frozen=True)
class Advice:
    """The warning or recommendation a policy gives (TER-INT-003)."""

    kind: AdviceKind
    message: str


@dataclass(frozen=True)
class Policy:
    """A declarative advisory policy (TER-INT-008, points 163, 164).

    ``min_evidence`` is the number of cited events a signal needs; it must be
    at least 1, so no policy can fire on a token count alone (point 139).
    ``min_confidence`` is raised to :data:`ADVICE_CONFIDENCE` if set lower.
    ``min_signals`` is the hysteresis: matching signals needed since the last
    firing. ``cooldown_tool_events`` silences the policy after it fires.
    """

    id: str
    detectors: frozenset[str]
    advice: Advice
    min_evidence: int = 1
    min_confidence: float = ADVICE_CONFIDENCE
    min_signals: int = 1
    cooldown_tool_events: int = 10

    def __post_init__(self) -> None:
        if not self.id:
            raise PolicyError("a policy needs an id")
        if not self.detectors:
            raise PolicyError(f"policy {self.id} names no detector")
        if self.min_evidence < 1:
            raise PolicyError(
                f"policy {self.id} needs min_evidence >= 1: no intervention may "
                "rest on a token count alone (TER-INT-007)"
            )
        if not 0.0 <= self.min_confidence <= 1.0:
            raise PolicyError(f"policy {self.id}: min_confidence must be in [0, 1]")
        if self.min_signals < 1:
            raise PolicyError(f"policy {self.id}: min_signals must be >= 1")
        if self.cooldown_tool_events < 0:
            raise PolicyError(f"policy {self.id}: cooldown_tool_events must be >= 0")
        if not self.advice.message.strip():
            raise PolicyError(f"policy {self.id} gives no advice")

    @property
    def confidence_floor(self) -> float:
        """The confidence a signal needs: never below :data:`ADVICE_CONFIDENCE`."""
        return max(self.min_confidence, ADVICE_CONFIDENCE)

    def answers(self, signal: Signal) -> bool:
        return signal.detector in self.detectors


@dataclass(frozen=True)
class Eligibility:
    """The calibration policy: detectors eligible for intervention (TER-INT-010).

    A detector is a waste category here: each detector names one kind of one
    Lean waste, and judging happens per detector, so eligibility is per
    detector too (a Lean waste spans detectors judged differently). ``basis``
    names the judged sample behind the list; it is required whenever the list
    is not empty.
    """

    detectors: frozenset[str]
    basis: str

    def __post_init__(self) -> None:
        if self.detectors and not self.basis.strip():
            raise PolicyError(
                "an eligibility list needs its basis: the judged sample that "
                "made its detectors eligible (TER-INT-010)"
            )

    def allows(self, detector: str) -> bool:
        return detector in self.detectors


@dataclass(frozen=True)
class Verdict:
    """The engine's answer for one signal under one policy, with the reason.

    ``advice`` is set only when ``outcome`` is :attr:`Outcome.ADVISED`; the
    signal travels with it, so the evidence is shown with every intervention
    (TER-INT-014).
    """

    signal: Signal
    policy: str
    outcome: Outcome
    reason: str
    advice: Advice | None = None

    @property
    def advised(self) -> bool:
        return self.outcome is Outcome.ADVISED


@dataclass
class _PolicyState:
    pending: int = 0
    fired_at: int | None = None
    #: Signal ids counted toward ``pending`` or advised, so a re-derived
    #: finding is never counted or advised twice.
    counted: set[str] = field(default_factory=set)
    advised: set[str] = field(default_factory=set)


@dataclass
class InterventionEngine:
    """The incremental Intervention Engine: one signal at a time.

    Apply signals in the order they became known (by ``position``). Applying
    the same signal twice changes nothing and returns no verdicts. Live
    analysis re-derives findings, so a signal id can come back with a new
    :attr:`~ter.domain.signal.Signal.revision` (a result arrived and raised
    its confidence): it is judged again, but never counted toward hysteresis
    twice and never advised twice by one policy.
    """

    policies: tuple[Policy, ...]
    eligibility: Eligibility
    ceiling: Maturity = Maturity.ADVISORY
    _seen: dict[str, tuple[float, tuple[str, ...]]] = field(
        default_factory=dict, init=False, repr=False
    )
    _state: dict[str, _PolicyState] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        ids = [p.id for p in self.policies]
        if len(ids) != len(set(ids)):
            raise PolicyError("policy ids must be unique")

    def apply(self, signal: Signal) -> tuple[Verdict, ...]:
        if self._seen.get(signal.id) == signal.revision:
            return ()
        self._seen[signal.id] = signal.revision
        return tuple(
            self._judge(signal, policy)
            for policy in self.policies
            if policy.answers(signal)
            and signal.id not in self._state_of(policy).advised
        )

    def _state_of(self, policy: Policy) -> _PolicyState:
        return self._state.setdefault(policy.id, _PolicyState())

    def _judge(self, signal: Signal, policy: Policy) -> Verdict:
        def verdict(outcome: Outcome, reason: str) -> Verdict:
            return Verdict(signal, policy.id, outcome, reason)

        if not self.ceiling.permits(Maturity.ADVISORY):
            return verdict(
                Outcome.BELOW_CEILING,
                f"the maturity ceiling is {self.ceiling.code}; advice needs L4",
            )
        if len(signal.evidence) < policy.min_evidence:
            return verdict(
                Outcome.NO_EVIDENCE,
                f"cites {len(signal.evidence)} event(s); the policy needs "
                f"{policy.min_evidence}",
            )
        if not self.eligibility.allows(signal.detector):
            return verdict(
                Outcome.INELIGIBLE,
                f"{signal.detector} is not intervention-eligible in the "
                "calibration policy",
            )
        if signal.confidence < policy.confidence_floor:
            return verdict(
                Outcome.LOW_CONFIDENCE,
                f"confidence {signal.confidence:.2f} is below "
                f"{policy.confidence_floor:.2f}",
            )
        state = self._state_of(policy)
        if (
            state.fired_at is not None
            and signal.position - state.fired_at < policy.cooldown_tool_events
        ):
            return verdict(
                Outcome.COOLDOWN,
                f"{policy.id} fired {signal.position - state.fired_at} tool "
                f"event(s) ago; cooldown is {policy.cooldown_tool_events}",
            )
        if signal.id not in state.counted:
            state.counted.add(signal.id)
            state.pending += 1
        if state.pending < policy.min_signals:
            return verdict(
                Outcome.BELOW_HYSTERESIS,
                f"{state.pending} of {policy.min_signals} matching signal(s) "
                "since the policy last fired",
            )
        state.pending = 0
        state.fired_at = signal.position
        state.advised.add(signal.id)
        return Verdict(
            signal,
            policy.id,
            Outcome.ADVISED,
            f"{signal.detector} at confidence {signal.confidence:.2f}, citing "
            f"{len(signal.evidence)} event(s)",
            policy.advice,
        )


def decide(
    signals: Iterable[Signal],
    policies: Iterable[Policy],
    eligibility: Eligibility,
    ceiling: Maturity = Maturity.ADVISORY,
) -> tuple[Verdict, ...]:
    """Batch form: the same fold as :class:`InterventionEngine`, in order."""
    engine = InterventionEngine(tuple(policies), eligibility, ceiling)
    ordered = sorted(signals, key=lambda s: (s.position, s.id))
    return tuple(v for s in ordered for v in engine.apply(s))
