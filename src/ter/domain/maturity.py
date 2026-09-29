"""Maturity levels L0 to L6.

Each level is both a build milestone (TER cannot claim a level until every
requirement at that level is verified) and a runtime ceiling (an installation
can run a mature build with capabilities above its ceiling switched off).
"""

from __future__ import annotations

from enum import IntEnum


class Maturity(IntEnum):
    """Ordered maturity levels. Higher levels include every lower level."""

    MEASURED = 0
    OBSERVED = 1
    EXPLAINED = 2
    GROUNDED = 3
    ADVISORY = 4
    CORRECTIVE = 5
    LEARNING = 6

    @property
    def code(self) -> str:
        """Short code such as ``"L3"``."""
        return f"L{self.value}"

    @property
    def title(self) -> str:
        """Human-readable name such as ``"Grounded"``."""
        return self.name.capitalize()

    def permits(self, required: Maturity) -> bool:
        """Return True when a capability needing ``required`` may run under this ceiling."""
        return required <= self

    @classmethod
    def parse(cls, value: str | int) -> Maturity:
        """Parse ``"L3"``, ``"grounded"``, ``"3"`` or ``3`` into a level.

        Raises:
            ValueError: If the value names no level.
        """
        if isinstance(value, int) and not isinstance(value, bool):
            return cls(value)
        text = str(value).strip()
        upper = text.upper()
        if upper in cls.__members__:
            return cls[upper]
        if upper.startswith("L"):
            upper = upper[1:]
        if upper.isdigit() and int(upper) in {m.value for m in cls}:
            return cls(int(upper))
        names = ", ".join(f"{m.code}/{m.name.lower()}" for m in cls)
        raise ValueError(f"Unknown maturity level {value!r}; expected one of {names}")
