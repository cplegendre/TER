"""Outcome and acceptance: was the requested software change accepted?

This is judgement about the result, kept apart from the measurement of agent
behaviour (point 5). An :class:`AcceptanceContract` names the checks that
must pass; an :class:`OutcomeSource` (a driven port) supplies the evidence a
run produced, one :class:`CheckEvidence` per check; :func:`judge` compares
the two and returns an :class:`OutcomeVerdict` that keeps the evidence of
every check.

There is deliberately no weighted outcome score. A verdict is ``accepted``,
``rejected`` or ``incomplete``, with the per-check evidence beside it, so the
judgement stays explainable and is never folded into behaviour measures. No
behaviour measure imports this module (the ``behaviour-blind-to-outcome``
import contract); reports show the verdict next to them, and may divide a
behaviour total by the number of verified outcomes
(:func:`per_verified_outcome`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "AcceptanceContract",
    "Check",
    "CheckEvidence",
    "CheckResult",
    "CheckStatus",
    "OutcomeEvidence",
    "OutcomeFormatError",
    "OutcomeVerdict",
    "Verdict",
    "combine_statuses",
    "judge",
    "per_verified_outcome",
]


class OutcomeFormatError(ValueError):
    """An outcome record exists but cannot be read as outcome evidence."""


class CheckStatus(StrEnum):
    """What one piece of evidence says about one check."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"

    @property
    def is_failure(self) -> bool:
        return self in (CheckStatus.FAILED, CheckStatus.ERROR)


class Verdict(StrEnum):
    """The judgement on a run against its acceptance contract."""

    ACCEPTED = "accepted"
    """Every required check has passing evidence and none has failing evidence."""
    REJECTED = "rejected"
    """At least one required check has failing evidence."""
    INCOMPLETE = "incomplete"
    """Nothing failed, but a required check was not shown to pass."""


@dataclass(frozen=True, slots=True)
class Check:
    """One condition of acceptance, identified as the evidence names it."""

    id: str
    required: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("a check needs a non-empty id")


@dataclass(frozen=True, slots=True)
class AcceptanceContract:
    """The checks a run must pass for its outcome to be accepted."""

    name: str
    checks: tuple[Check, ...]

    def __post_init__(self) -> None:
        ids = [c.id for c in self.checks]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(
                f"duplicate check ids in contract: {', '.join(duplicates)}"
            )

    @classmethod
    def every_check_in(cls, evidence: OutcomeEvidence) -> AcceptanceContract:
        """The default contract: every check the run recorded must pass."""
        seen: dict[str, None] = {}
        for item in evidence.checks:
            seen.setdefault(item.check_id, None)
        return cls("every recorded check passes", tuple(Check(i) for i in seen))


@dataclass(frozen=True, slots=True)
class CheckEvidence:
    """One recorded result for one check, and where it was recorded."""

    check_id: str
    status: CheckStatus
    source: str
    detail: str = ""
    seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.check_id.strip():
            raise ValueError("check evidence needs a non-empty check id")

    def as_dict(self) -> dict[str, object]:
        return {
            "check": self.check_id,
            "status": self.status.value,
            "source": self.source,
            "detail": self.detail,
            "seconds": None if self.seconds is None else round(self.seconds, 3),
        }


@dataclass(frozen=True, slots=True)
class OutcomeEvidence:
    """Everything an outcome source recorded for one run, in record order."""

    run_ref: str
    source: str
    checks: tuple[CheckEvidence, ...]


def combine_statuses(statuses: Iterable[CheckStatus]) -> CheckStatus | None:
    """One status for a check with several records (a rerun, say).

    Any error or failure wins (error before failure); otherwise a pass; a
    check that was only ever skipped stays skipped; no records gives None.
    """
    found = set(statuses)
    for status in (
        CheckStatus.ERROR,
        CheckStatus.FAILED,
        CheckStatus.PASSED,
        CheckStatus.SKIPPED,
    ):
        if status in found:
            return status
    return None


@dataclass(frozen=True, slots=True)
class CheckResult:
    """A contract check with all its evidence; ``status`` is None when there is none."""

    check: Check
    status: CheckStatus | None
    evidence: tuple[CheckEvidence, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "check": self.check.id,
            "required": self.check.required,
            "status": None if self.status is None else self.status.value,
            "evidence": [e.as_dict() for e in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class OutcomeVerdict:
    """The verdict, the contract it was judged against and every check's evidence."""

    verdict: Verdict
    run_ref: str
    source: str
    contract: AcceptanceContract
    results: tuple[CheckResult, ...]
    unlisted: tuple[CheckEvidence, ...]
    reasons: tuple[str, ...]

    def count(self, status: CheckStatus | None, *, required: bool = True) -> int:
        return sum(
            1
            for r in self.results
            if r.status is status and (r.check.required or not required)
        )

    @property
    def accepted(self) -> bool:
        return self.verdict is Verdict.ACCEPTED

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict.value,
            "run": self.run_ref,
            "source": self.source,
            "contract": self.contract.name,
            "reasons": list(self.reasons),
            "required_checks": sum(1 for r in self.results if r.check.required),
            "checks": [r.as_dict() for r in self.results],
            "unlisted": [e.as_dict() for e in self.unlisted],
        }


def _names(results: Sequence[CheckResult], limit: int = 5) -> str:
    ids = [r.check.id for r in results]
    shown = ", ".join(ids[:limit])
    return shown + (f" and {len(ids) - limit} more" if len(ids) > limit else "")


def judge(
    evidence: OutcomeEvidence, contract: AcceptanceContract | None = None
) -> OutcomeVerdict:
    """Judge a run's evidence against a contract (default: every recorded check).

    * **rejected** when any required check has failing (failed or error)
      evidence;
    * otherwise **incomplete** when a required check has no evidence, was
      only skipped, or the contract requires no check at all;
    * otherwise **accepted**.

    Optional checks are reported but never change the verdict. Evidence for
    checks the contract does not name is kept in ``unlisted``.
    """
    contract = contract or AcceptanceContract.every_check_in(evidence)
    by_check: dict[str, list[CheckEvidence]] = {}
    for item in evidence.checks:
        by_check.setdefault(item.check_id, []).append(item)
    results = tuple(
        CheckResult(
            check,
            combine_statuses(e.status for e in by_check.get(check.id, ())),
            tuple(by_check.get(check.id, ())),
        )
        for check in contract.checks
    )
    named = {c.id for c in contract.checks}
    unlisted = tuple(e for e in evidence.checks if e.check_id not in named)
    required = [r for r in results if r.check.required]
    failing = [r for r in required if r.status is not None and r.status.is_failure]
    unproven = [r for r in required if r.status in (None, CheckStatus.SKIPPED)]
    reasons: list[str] = []
    if failing:
        verdict = Verdict.REJECTED
        reasons.append(f"{len(failing)} required check(s) failed: {_names(failing)}")
    elif not required:
        verdict = Verdict.INCOMPLETE
        reasons.append("the acceptance contract requires no check")
    elif unproven:
        verdict = Verdict.INCOMPLETE
        reasons.append(
            f"{len(unproven)} required check(s) not shown to pass: {_names(unproven)}"
        )
    else:
        verdict = Verdict.ACCEPTED
        reasons.append(f"all {len(required)} required check(s) passed")
    return OutcomeVerdict(
        verdict,
        evidence.run_ref,
        evidence.source,
        contract,
        results,
        unlisted,
        tuple(reasons),
    )


def per_verified_outcome(
    total: float, verdicts: Sequence[OutcomeVerdict]
) -> float | None:
    """A behaviour total (tokens, cost) divided by the accepted outcomes.

    None when no outcome was accepted: work with no verified outcome has no
    per-outcome figure, rather than an infinite or zero one.
    """
    accepted = sum(1 for v in verdicts if v.accepted)
    return total / accepted if accepted else None
