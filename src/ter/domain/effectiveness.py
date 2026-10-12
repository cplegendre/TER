"""Did the advice help? Follow-up of delivered interventions and policy effectiveness.

L4 Advisory step 6 (TER-INT-011, TER-INT-012, points 167 to 170, 179). Like
the Intervention Engine, this reads only detector signals and ledger records,
never events, steps or findings (TER-INT-006).

**Follow-up** (:func:`follow_up`). Once :data:`WINDOW` tool events have been
observed after a piece of advice was delivered, the record gets its outcome:
the flow measures over the :data:`WINDOW` tool events before the advice and
the :data:`WINDOW` after it, and the response. The response is inferred from
behaviour: *accepted* when no confident signal of the policy's detectors
appears in the window after the advice and the advised finding itself did
not grow after it (more round trips on the same file extend one
``fragmented_edits`` finding), *ignored* otherwise. *Rejected* needs the
developer to say so; it is never inferred. Only advice delivered live (a
record with ``delivered_at``) is followed up or scored: shadow advice was
never shown to anyone.

The measures, each per tool event of its window (so a short window at the
start of a session compares fairly):

* ``repeats_per_tool_event``: confident signals (at least
  :data:`~ter.domain.intervention.ADVICE_CONFIDENCE`) of the policy's
  detectors: the waste the advice is about. Before the advice, the advised
  signal itself is left out; after it, the advised signal counts when it
  grew after the delivery;
* ``waste_per_tool_event``: every waste signal of any detector, uncertain
  ones included, since uncertain findings count as waste until verified
  (ADR 0006).

**Effectiveness** (:func:`effectiveness`). Per policy and policy file,
over every observed outcome: how often the advice was accepted, and the mean
repeat rate before and after. With fewer than :data:`MIN_OBSERVED` outcomes
the policy is *unproven*. Otherwise it *improves* when most advice was
accepted and repeats did not rise after it; anything else is flagged for
recalibration (point 170).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .intervention import ADVICE_CONFIDENCE
from .ledger import LedgerRecord, Observation, Response
from .signal import Signal

__all__ = [
    "MIN_OBSERVED",
    "WINDOW",
    "Effect",
    "PolicyEffectiveness",
    "effectiveness",
    "follow_up",
    "is_live",
]

#: Tool events after delivery before an outcome is recorded (TER-INT-011),
#: and the length of each before and after window.
WINDOW = 10

#: Observed outcomes a policy needs before it is judged (TER-INT-012).
MIN_OBSERVED = 5

#: The share of advice that must be accepted for a policy to count as helping.
ACCEPTANCE_FLOOR = 0.5

REPEATS = "repeats_per_tool_event"
WASTE = "waste_per_tool_event"


def follow_up(
    record: LedgerRecord,
    signals: Sequence[Signal],
    tool_events: int,
    detectors: Iterable[str],
    observed_at: datetime,
) -> Observation | None:
    """The outcome of a delivered intervention, once it can be observed.

    ``signals`` are the session's detector signals, ``tool_events`` the
    number of tool events observed so far, and ``detectors`` the detectors
    the record's policy answers. Returns ``None`` for a record not delivered
    live (a suppression, or shadow advice), one already observed, or one with
    fewer than :data:`WINDOW` tool events after its delivery.
    """
    if not is_live(record) or record.observation is not None:
        return None
    at = record.delivered_at
    assert at is not None  # is_live
    if tool_events - at < WINDOW:
        return None
    watched = frozenset(detectors) | {record.detector}
    before = _measures(signals, max(at - WINDOW, 0), at, watched, record.signal_id)
    after = _measures(signals, at, at + WINDOW, watched, None)
    grew = any(s.id == record.signal_id and s.position > at for s in signals)
    response = Response.IGNORED if after[REPEATS] > 0 or grew else Response.ACCEPTED
    return Observation(response, observed_at, tool_events - at, before, after)


def is_live(record: LedgerRecord) -> bool:
    """True for advice delivered to a developer while the session ran."""
    return record.delivered and record.delivered_at is not None


def _measures(
    signals: Sequence[Signal],
    start: int,
    end: int,
    watched: frozenset[str],
    advised: str | None,
) -> dict[str, float]:
    """The measures over tool events ``start`` (exclusive) to ``end``."""
    span = end - start
    inside = [s for s in signals if start < s.position <= end]
    repeats = sum(
        1
        for s in inside
        if s.detector in watched
        and s.id != advised
        and s.confidence >= ADVICE_CONFIDENCE
    )
    waste = sum(1 for s in inside if s.kind == "waste")
    if span <= 0:
        return {REPEATS: 0.0, WASTE: 0.0}
    return {REPEATS: round(repeats / span, 4), WASTE: round(waste / span, 4)}


class Effect(StrEnum):
    """What the ledger says about a policy (TER-INT-012)."""

    IMPROVES = "improves"
    NO_IMPROVEMENT = "no_improvement"
    UNPROVEN = "unproven"


@dataclass(frozen=True)
class PolicyEffectiveness:
    """One policy's effectiveness under one policy file."""

    policy: str
    policy_version: str
    advised: int
    observed: int
    accepted: int
    ignored: int
    rejected: int
    #: Mean repeats per tool event before and after the advice.
    repeats_before: float
    repeats_after: float
    effect: Effect

    @property
    def acceptance(self) -> float | None:
        """The share of observed advice accepted, or ``None`` before any."""
        return self.accepted / self.observed if self.observed else None

    @property
    def recalibrate(self) -> bool:
        """True when the policy should be removed or recalibrated (point 170)."""
        return self.effect is Effect.NO_IMPROVEMENT

    @property
    def why(self) -> str:
        if self.effect is Effect.UNPROVEN:
            return (
                f"{self.observed} observed outcome(s); "
                f"{MIN_OBSERVED} are needed to judge"
            )
        acceptance = self.acceptance or 0.0
        verdict = (
            "helps" if self.effect is Effect.IMPROVES else "does not improve outcomes"
        )
        return (
            f"{verdict}: {acceptance:.0%} accepted, repeats per tool event "
            f"{self.repeats_before:.3f} before and {self.repeats_after:.3f} after"
        )


def effectiveness(records: Iterable[LedgerRecord]) -> tuple[PolicyEffectiveness, ...]:
    """Every policy's effectiveness from ledger outcomes, by policy and file."""
    groups: dict[tuple[str, str], list[LedgerRecord]] = {}
    for record in records:
        if is_live(record):
            groups.setdefault((record.policy, record.policy_version), []).append(record)
    return tuple(
        _score(policy, version, groups[policy, version])
        for policy, version in sorted(groups)
    )


def _score(
    policy: str, version: str, records: list[LedgerRecord]
) -> PolicyEffectiveness:
    observations = [r.observation for r in records if r.observation is not None]
    count = {
        response: sum(1 for o in observations if o.response is response)
        for response in Response
    }
    before = _mean(o.before.get(REPEATS, 0.0) for o in observations)
    after = _mean(o.after.get(REPEATS, 0.0) for o in observations)
    if len(observations) < MIN_OBSERVED:
        effect = Effect.UNPROVEN
    elif (
        count[Response.ACCEPTED] / len(observations) >= ACCEPTANCE_FLOOR
        and after <= before
    ):
        effect = Effect.IMPROVES
    else:
        effect = Effect.NO_IMPROVEMENT
    return PolicyEffectiveness(
        policy,
        version,
        advised=len(records),
        observed=len(observations),
        accepted=count[Response.ACCEPTED],
        ignored=count[Response.IGNORED],
        rejected=count[Response.REJECTED],
        repeats_before=before,
        repeats_after=after,
        effect=effect,
    )


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return round(sum(items) / len(items), 4) if items else 0.0
