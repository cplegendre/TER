"""The session scorecard: six separate dimensions and Software Value Efficiency.

TER reports efficiency, flow, quality, cost, risk and outcome as separate
dimensions (points 3, 82, 83; TER-SCR-001), each a list of named measures
with their units, and never folds them into one opaque score. The first five
measure agent behaviour and come from the :class:`~.analysis.LeanAnalysis`
alone; *outcome* is the verdict judged apart from them
(:mod:`ter.domain.outcome`), or ``unknown`` when no outcome evidence was
supplied. This module sits beside the behaviour measures: it reads both, and
no behaviour measure reads it.

**Software Value Efficiency** (SVE, point 81; TER-SCR-003) is reported next
to the Token Efficiency Ratio. It is value delivered toward a *verified*
outcome per unit of resource consumed:

* ``tokens``: value-adding generated tokens ÷ generated tokens, when the
  outcome was accepted; 0 when it was rejected (no value was delivered);
* ``time``: the same ratio for agent wall time;
* ``tokens_per_outcome`` and ``seconds_per_outcome``: the resources spent
  per accepted outcome.

Without outcome evidence, or with an *incomplete* verdict, SVE is
**unknown**: TER does not guess value from tokens (TER-LEN-005). Value-adding
work is the Lean activity class of :mod:`.analysis` (edits that change the
software and the final response); cost in money waits for cost on the L2
scorecard, so resources are tokens and time.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..outcome import OutcomeVerdict, Verdict
from .analysis import LeanAnalysis, Scorecard
from .model import ActivityClass
from .wip import WipKind

__all__ = [
    "SVE_DEFINITION",
    "Dimension",
    "Measure",
    "ScorecardDimension",
    "SoftwareValueEfficiency",
    "ValueStatus",
    "scorecard_dimensions",
    "software_value_efficiency",
]

#: One-sentence definition shown wherever SVE is reported.
SVE_DEFINITION = (
    "Software Value Efficiency: value-adding work toward a verified (accepted) "
    "outcome per unit of resource: value-adding generated tokens / generated "
    "tokens, and the same for agent time; 0 when the outcome was rejected, "
    "unknown without outcome evidence."
)


class Dimension(StrEnum):
    """The six scorecard dimensions, in report order."""

    EFFICIENCY = "efficiency"
    FLOW = "flow"
    QUALITY = "quality"
    COST = "cost"
    RISK = "risk"
    OUTCOME = "outcome"

    @property
    def label(self) -> str:
        return self.value.capitalize()

    @property
    def question(self) -> str:
        return _QUESTIONS[self]


_QUESTIONS: dict[Dimension, str] = {
    Dimension.EFFICIENCY: "How much of the resource became value?",
    Dimension.FLOW: "How smoothly did work move, and how much was left open?",
    Dimension.QUALITY: "Was the work checked, and did the checks converge?",
    Dimension.COST: "What did the session consume, and how much was waste?",
    Dimension.RISK: "What may be wrong that nothing has shown yet?",
    Dimension.OUTCOME: "Was the requested change accepted?",
}

MeasureValue = float | int | str | None


@dataclass(frozen=True)
class Measure:
    """One named measure of a dimension; ``None`` means unknown, never zero."""

    key: str
    label: str
    value: MeasureValue
    unit: str
    note: str = ""

    def as_dict(self) -> dict[str, object]:
        value = round(self.value, 4) if isinstance(self.value, float) else self.value
        return {
            "key": self.key,
            "label": self.label,
            "value": value,
            "unit": self.unit,
            "note": self.note,
        }


@dataclass(frozen=True)
class ScorecardDimension:
    dimension: Dimension
    measures: tuple[Measure, ...]

    def measure(self, key: str) -> Measure:
        return next(m for m in self.measures if m.key == key)

    def as_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension.value,
            "question": self.dimension.question,
            "measures": [m.as_dict() for m in self.measures],
        }


class ValueStatus(StrEnum):
    """Whether SVE could be measured."""

    MEASURED = "measured"
    """The outcome was accepted: value was delivered and is divided by resource."""
    NO_VALUE = "no_value"
    """The outcome was rejected: no value was delivered, so SVE is 0."""
    UNKNOWN = "unknown"
    """No outcome evidence, or an incomplete verdict: value is not known."""


@dataclass(frozen=True)
class SoftwareValueEfficiency:
    """Value delivered toward a verified outcome per unit of resource."""

    status: ValueStatus
    verdict: Verdict | None
    verified_outcomes: int | None
    tokens: float | None
    time: float | None
    value_tokens: int | None
    value_seconds: float | None
    tokens_per_outcome: float | None
    seconds_per_outcome: float | None
    reason: str

    @property
    def value(self) -> float | None:
        """The headline figure: SVE by generated tokens."""
        return self.tokens

    def as_dict(self) -> dict[str, object]:
        def r(v: float | None, digits: int = 4) -> float | None:
            return None if v is None else round(v, digits)

        return {
            "status": self.status.value,
            "verdict": None if self.verdict is None else self.verdict.value,
            "verified_outcomes": self.verified_outcomes,
            "tokens": r(self.tokens),
            "time": r(self.time),
            "value_tokens": self.value_tokens,
            "value_seconds": r(self.value_seconds, 3),
            "tokens_per_outcome": r(self.tokens_per_outcome, 1),
            "seconds_per_outcome": r(self.seconds_per_outcome, 3),
            "reason": self.reason,
            "definition": SVE_DEFINITION,
        }


def _ratio(part: float, whole: float) -> float | None:
    return part / whole if whole > 0 else None


def software_value_efficiency(
    scorecard: Scorecard, outcome: OutcomeVerdict | None
) -> SoftwareValueEfficiency:
    """SVE of a session from its scorecard and, when known, its outcome verdict."""
    if outcome is None or outcome.verdict is Verdict.INCOMPLETE:
        reason = (
            "no outcome evidence was supplied (--outcome FILE)"
            if outcome is None
            else "the outcome is incomplete: " + "; ".join(outcome.reasons)
        )
        return SoftwareValueEfficiency(
            ValueStatus.UNKNOWN,
            None if outcome is None else outcome.verdict,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            f"Unknown: {reason}; value is never inferred from tokens.",
        )
    generated = scorecard.generated_tokens
    seconds = scorecard.agent_seconds
    if outcome.verdict is Verdict.REJECTED:
        return SoftwareValueEfficiency(
            ValueStatus.NO_VALUE,
            outcome.verdict,
            0,
            0.0 if generated > 0 else None,
            0.0 if seconds > 0 else None,
            0,
            0.0,
            None,
            None,
            "The outcome was rejected: no verified value was delivered.",
        )
    key = ActivityClass.VALUE_ADDING.value
    value_tokens = dict(scorecard.activity_tokens).get(key, 0)
    value_seconds = dict(scorecard.activity_seconds).get(key, 0.0)
    return SoftwareValueEfficiency(
        ValueStatus.MEASURED,
        outcome.verdict,
        1,
        _ratio(value_tokens, generated),
        _ratio(value_seconds, seconds),
        value_tokens,
        value_seconds,
        float(generated),
        seconds if seconds > 0 else None,
        "The outcome was accepted: value-adding work over everything consumed.",
    )


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def scorecard_dimensions(
    analysis: LeanAnalysis,
    sve: SoftwareValueEfficiency,
    outcome: OutcomeVerdict | None,
) -> tuple[ScorecardDimension, ...]:
    """The six dimensions, each with its named measures (TER-SCR-001)."""
    sc = analysis.scorecard
    wip = analysis.wip
    final = wip.final
    peak = wip.peak

    def m(
        key: str, label: str, value: MeasureValue, unit: str, note: str = ""
    ) -> Measure:
        return Measure(key, label, value, unit, note)

    efficiency = (
        m(
            "ter",
            "Token Efficiency Ratio",
            None if sc.ter is None else sc.ter.value,
            "ratio",
            "not computed" if sc.ter is None else sc.ter.method,
        ),
        m(
            "software_value_efficiency",
            "Software Value Efficiency",
            sve.value,
            "ratio",
            sve.status.value,
        ),
        m(
            "value_adding_share",
            "Value-adding share of generated tokens",
            sc.activity_share(ActivityClass.VALUE_ADDING)
            if sc.generated_tokens
            else None,
            "ratio",
        ),
        m(
            "avoidable_share",
            "Avoidable share of generated tokens",
            sc.activity_share(ActivityClass.AVOIDABLE) if sc.generated_tokens else None,
            "ratio",
        ),
    )
    flow = (
        m(
            "flow_efficiency_tokens",
            "Flow efficiency (tokens)",
            sc.flow_efficiency_tokens,
            "ratio",
        ),
        m(
            "flow_efficiency_time",
            "Flow efficiency (time)",
            sc.flow_efficiency_time,
            "ratio",
        ),
        m(
            "wip_peak",
            "Peak WIP",
            None if peak is None else peak.total,
            "items",
            "" if peak is None else f"after event {peak.event_id}",
        ),
        m(
            "wip_final",
            "WIP at the end",
            None if final is None else final.total,
            "items",
        ),
    )
    quality = (
        m("validation_runs", "Validation runs", sc.validation_runs, "count"),
        m("validations_passed", "Runs that passed", sc.validations_passed, "count"),
        m("validations_failed", "Runs that failed", sc.validations_failed, "count"),
        m("iterations", "Iteration cycles (converging)", sc.iterations, "count"),
        m("rework_cycles", "Rework cycles (same failure)", sc.rework_cycles, "count"),
        m(
            "edits_validated_share",
            "Edits covered by a later validation run",
            _share(wip.edits_validated, wip.edits_opened),
            "ratio",
            f"{wip.edits_validated} of {wip.edits_opened}",
        ),
    )
    cost = (
        m("generated_tokens", "Generated tokens", sc.generated_tokens, "tokens"),
        m("context_tokens", "Context tokens", sc.context_tokens, "tokens"),
        m("agent_seconds", "Agent time", sc.agent_seconds, "seconds"),
        m("waste_tokens", "Avoidable generated tokens", sc.waste_tokens, "tokens"),
        m(
            "waste_context_tokens",
            "Avoidable context tokens",
            sc.waste_context_tokens,
            "tokens",
        ),
        m("waste_seconds", "Avoidable agent time", sc.waste_seconds, "seconds"),
    )
    risk = (
        m("risk_findings", "Risk findings", sc.risks, "count"),
        m("uncertain_findings", "Uncertain findings", sc.uncertain_findings, "count"),
        m(
            "unvalidated_edits_at_end",
            "Edits no validation run covered",
            len(wip.still_open(WipKind.EDITS)),
            "count",
        ),
        m(
            "unresolved_failures_at_end",
            "Failing checks never seen passing again",
            len(wip.still_open(WipKind.FAILURES)),
            "count",
        ),
    )
    outcome_measures = (
        m(
            "verdict",
            "Verdict",
            "unknown" if outcome is None else outcome.verdict.value,
            "verdict",
            "no outcome evidence supplied"
            if outcome is None
            else "; ".join(outcome.reasons),
        ),
        m(
            "tokens_per_verified_outcome",
            "Generated tokens per verified outcome",
            sve.tokens_per_outcome,
            "tokens",
        ),
    )
    return (
        ScorecardDimension(Dimension.EFFICIENCY, efficiency),
        ScorecardDimension(Dimension.FLOW, flow),
        ScorecardDimension(Dimension.QUALITY, quality),
        ScorecardDimension(Dimension.COST, cost),
        ScorecardDimension(Dimension.RISK, risk),
        ScorecardDimension(Dimension.OUTCOME, outcome_measures),
    )
