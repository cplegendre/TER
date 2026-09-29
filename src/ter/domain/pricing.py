"""Model prices and the cost arithmetic built on them.

Prices are data, not code: they change on a vendor's schedule, differ by
model and by date, and must be traceable to a source. The domain defines what
a price is (:class:`Rates`), how dated prices resolve (:class:`PriceSchedule`)
and how token counts become dollars. Where the prices come from is a
``PriceBook`` port (``ter.ports``); the shipped adapter reads a dated JSON
file.

All rates are US dollars per million tokens. Cost arithmetic uses the
operation order TER 3 used (``tokens * rate / 1_000_000``) so dollar figures
are bit-for-bit unchanged.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

__all__ = [
    "PriceEntry",
    "PriceSchedule",
    "Rates",
    "TokenCounts",
    "UnknownModelError",
    "token_cost",
    "usage_cost",
]

TOKENS_PER_RATE_UNIT = 1_000_000


class UnknownModelError(KeyError):
    """No price is known for the model (or none was in effect on the date)."""


@dataclass(frozen=True, slots=True)
class Rates:
    """Per-million-token prices in USD for one model."""

    input: float
    output: float
    cache_read: float
    cache_write: float

    def __post_init__(self) -> None:
        for name in ("input", "output", "cache_read", "cache_write"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Rate {name} must be finite and >= 0, got {value}")

    @property
    def thinking(self) -> float:
        """Extended-thinking tokens are billed as output."""
        return self.output


@dataclass(frozen=True, slots=True)
class TokenCounts:
    """Billed token counts for a session or turn."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0


def token_cost(tokens: int, rate_per_mtok: float) -> float:
    """Dollar cost of ``tokens`` at ``rate_per_mtok`` dollars per million."""
    return tokens * rate_per_mtok / TOKENS_PER_RATE_UNIT


def usage_cost(counts: TokenCounts, rates: Rates) -> float:
    """Dollar cost of a usage record: input, output, cache reads and writes."""
    return (
        token_cost(counts.input, rates.input)
        + token_cost(counts.output, rates.output)
        + token_cost(counts.cache_read, rates.cache_read)
        + token_cost(counts.cache_write, rates.cache_write)
    )


@dataclass(frozen=True, slots=True)
class PriceEntry:
    """A model's rates from a given date, with where the numbers came from."""

    model: str
    effective_from: date
    rates: Rates
    source: str = ""
    aliases: tuple[str, ...] = ()


class PriceSchedule:
    """Resolves a model name (or alias) and a date to the rates then in effect.

    Each model may have several dated entries; the latest one whose
    ``effective_from`` is on or before the requested date wins. With no date,
    the latest entry wins.
    """

    def __init__(self, entries: Iterable[PriceEntry]) -> None:
        self._entries: dict[str, list[PriceEntry]] = {}
        self._aliases: dict[str, str] = {}
        for entry in entries:
            self._entries.setdefault(entry.model, []).append(entry)
        for model, dated in self._entries.items():
            dated.sort(key=lambda e: e.effective_from)
            days = [e.effective_from for e in dated]
            if len(set(days)) != len(days):
                raise ValueError(f"Duplicate effective_from dates for {model}")
            for entry in dated:
                for alias in entry.aliases:
                    owner = self._aliases.setdefault(alias, model)
                    if owner != model or alias in self._entries:
                        raise ValueError(f"Alias {alias!r} names more than one model")

    def models(self) -> tuple[str, ...]:
        """Canonical model names with at least one price, sorted."""
        return tuple(sorted(self._entries))

    def resolve(self, model: str) -> str:
        """Return the canonical model name for a name or alias."""
        if model in self._entries:
            return model
        if model in self._aliases:
            return self._aliases[model]
        raise UnknownModelError(model)

    def entry(self, model: str, at: date | None = None) -> PriceEntry:
        """Return the price entry in effect for ``model`` on ``at``."""
        dated = self._entries[self.resolve(model)]
        if at is None:
            return dated[-1]
        in_effect = [e for e in dated if e.effective_from <= at]
        if not in_effect:
            raise UnknownModelError(f"{model} has no price in effect on {at}")
        return in_effect[-1]

    def rate(self, model: str, at: date | None = None) -> Rates:
        """Return the rates in effect for ``model`` on ``at`` (latest if None)."""
        return self.entry(model, at).rates
