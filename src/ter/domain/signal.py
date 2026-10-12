"""The structured detector signal: the one value analysis and intervention share.

It lives in its own module so neither engine imports the other (TER-INT-006,
point 121): ``ter.domain.lean.signals`` builds signals from findings, and
``ter.domain.intervention`` reads them. Import-linter contracts forbid both
directions between the Lean model and the Intervention Engine.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Signal"]


@dataclass(frozen=True)
class Signal:
    """One structured detector signal: what the Intervention Engine reads.

    ``position`` is the number of tool events observed when the signal became
    known (its last cited event), the clock cooldowns count in. ``evidence``
    lists the event ids the signal rests on; ``rule`` is the detector's
    published confidence rule.
    """

    id: str
    detector: str
    waste: str
    kind: str
    confidence: float
    title: str
    subject: str
    evidence: tuple[str, ...]
    rule: str
    position: int

    @property
    def has_evidence(self) -> bool:
        """True when the signal cites at least one event (TER-INT-007)."""
        return bool(self.evidence)

    @property
    def revision(self) -> tuple[float, tuple[str, ...]]:
        """What can change while a finding is re-derived live: a later result
        can raise its confidence or add evidence under the same id."""
        return (self.confidence, self.evidence)
