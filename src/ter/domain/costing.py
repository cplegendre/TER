"""What a session cost, priced from its events and a dated price book.

A session is priced at the prices in force when it ran (TER-ANL-040): the
session date is the UTC date of its first timestamped event, and every model
turn is priced with the price book entry whose ``effective_from`` is the
latest that is not after that date. Turns whose model is unnamed, or has no
price on that date, stay unpriced and are counted, never guessed.

Cost figures are marked *estimated* (TER-ANL-041) when a turn's usage had no
cache fields (the provider did not say what was cached, so cache-priced
figures are guesses), when the session has no model turns at all, when it has
no timestamp to date it by (the latest prices are used), or when some turns
could not be priced. Each reason is listed, so a reader sees why.

Context inventory (``ter.domain.lean.inventory``) is priced as carrying cost:
a context item is paid for on the first model turn after it entered (at the
cache-write rate when that turn shows caching, else the input rate) and again
on every later turn (the cache-read rate when caching, else input). Only
the price book supplies rates; nothing here is hard-coded (ADR 0003).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Protocol

from .events import TokenUsage
from .pricing import (
    TOKENS_PER_RATE_UNIT,
    Rates,
    TokenCounts,
    UnknownModelError,
    usage_cost,
)

if TYPE_CHECKING:  # the lean package imports this module at runtime
    from .lean.inventory import ContextInventory, InventoryItem
    from .lean.model import Step

__all__ = [
    "EstimateReason",
    "ModelCost",
    "Prices",
    "SessionCost",
    "price_session",
    "session_date",
]


class Prices(Protocol):
    """What costing needs from a price book.

    Structurally the :class:`ter.ports.driven.PriceBook` port, which the
    domain may not import: every price book adapter satisfies both.
    """

    @property
    def name(self) -> str: ...

    def rate(self, model: str, at: date | None = None) -> Rates: ...


class EstimateReason:
    """Stable ids for why a cost figure is an estimate."""

    NO_CACHE_FIELDS = "no-cache-fields"
    NO_USAGE = "no-usage"
    NO_SESSION_DATE = "no-session-date"
    UNPRICED_TURNS = "unpriced-turns"

    TEXT = {
        NO_CACHE_FIELDS: (
            "usage without cache fields: cache reads and writes are unknown, "
            "so context is priced at the input rate"
        ),
        NO_USAGE: "the session reports no provider usage",
        NO_SESSION_DATE: "no event has a timestamp; the latest prices were used",
        UNPRICED_TURNS: "some model turns name no model or one with no price",
    }


@dataclass(frozen=True)
class ModelCost:
    """Turns and cost of one model in the session."""

    model: str
    turns: int
    usd: float


@dataclass(frozen=True)
class SessionCost:
    """A session's cost and the cost of its context inventory, in USD."""

    price_book: str
    priced_on: date | None
    turns: int
    usd: float
    by_model: tuple[ModelCost, ...]
    unpriced_turns: int
    unpriced_models: tuple[str, ...]
    unused_context_usd: float
    reread_context_usd: float
    estimate_reasons: tuple[str, ...]

    @property
    def estimated(self) -> bool:
        return bool(self.estimate_reasons)

    def as_dict(self) -> dict[str, object]:
        return {
            "price_book": self.price_book,
            "priced_on": None if self.priced_on is None else self.priced_on.isoformat(),
            "estimated": self.estimated,
            "estimate_reasons": list(self.estimate_reasons),
            "turns": self.turns,
            "usd": round(self.usd, 6),
            "by_model": [
                {"model": m.model, "turns": m.turns, "usd": round(m.usd, 6)}
                for m in self.by_model
            ],
            "unpriced_turns": self.unpriced_turns,
            "unpriced_models": list(self.unpriced_models),
            "unused_context_usd": round(self.unused_context_usd, 6),
            "reread_context_usd": round(self.reread_context_usd, 6),
        }


def session_date(timestamps: Iterable[datetime | None]) -> date | None:
    """The UTC date of the first timestamp, or None when there is none.

    A naive timestamp is taken as UTC.
    """
    for ts in timestamps:
        if ts is None:
            continue
        if ts.tzinfo is not None:
            ts = ts.astimezone(UTC)
        return ts.date()
    return None


def _caching(usage: TokenUsage) -> bool:
    return usage.cache_creation_tokens > 0 or usage.cache_read_tokens > 0


def price_session(
    steps: tuple[Step, ...], inventory: ContextInventory, prices: Prices
) -> SessionCost:
    """Price every model turn of ``steps`` and the context ``inventory``.

    Linear in the session: one pass prices the turns and builds, per step,
    the per-token cost of carrying one token from that step to the end.
    """
    on = session_date(s.timestamp for s in steps)
    turns = 0
    unpriced = 0
    unpriced_models: set[str] = set()
    no_cache = False
    total = 0.0
    by_model: dict[str, tuple[int, float]] = {}
    # Per-token prices of each turn, for carrying context: (ingest, carry).
    turn_rates: dict[int, tuple[float, float]] = {}
    for step in steps:
        usage = step.usage
        if usage is None:
            continue
        turns += 1
        no_cache = no_cache or not usage.cache_reported
        rates = _rates(prices, usage.model, on)
        if rates is None:
            unpriced += 1
            unpriced_models.add(usage.model or "<unnamed>")
            continue
        cost = usage_cost(
            TokenCounts(
                input=usage.input_tokens,
                output=usage.output_tokens,
                cache_read=usage.cache_read_tokens,
                cache_write=usage.cache_creation_tokens,
            ),
            rates,
        )
        total += cost
        model = usage.model or ""
        n, usd = by_model.get(model, (0, 0.0))
        by_model[model] = (n + 1, usd + cost)
        caching = _caching(usage)
        turn_rates[step.index] = (
            (rates.cache_write if caching else rates.input) / TOKENS_PER_RATE_UNIT,
            (rates.cache_read if caching else rates.input) / TOKENS_PER_RATE_UNIT,
        )

    # first_after[i]: ingest price of the first priced turn after step i;
    # carry_after[i]: summed carry prices of the priced turns after that one.
    first_after = [0.0] * len(steps)
    carry_after = [0.0] * len(steps)
    first = 0.0
    carry = 0.0
    nearest: tuple[float, float] | None = None
    for step in reversed(steps):
        first_after[step.index] = first
        carry_after[step.index] = carry
        rate = turn_rates.get(step.index)
        if rate is not None:
            if nearest is not None:
                carry += nearest[1]
            nearest = rate
            first = rate[0]
    index_of = {s.event_id: s.index for s in steps}

    def held(items: tuple[InventoryItem, ...]) -> float:
        return sum(
            item.tokens * (first_after[i] + carry_after[i])
            for item in items
            for i in (index_of[item.event_id],)
        )

    reasons: list[str] = []
    if no_cache or turns == 0:
        reasons.append(EstimateReason.NO_CACHE_FIELDS)
    if turns == 0:
        reasons.append(EstimateReason.NO_USAGE)
    if on is None:
        reasons.append(EstimateReason.NO_SESSION_DATE)
    if unpriced:
        reasons.append(EstimateReason.UNPRICED_TURNS)
    return SessionCost(
        price_book=prices.name,
        priced_on=on,
        turns=turns,
        usd=total,
        by_model=tuple(
            ModelCost(m, n, usd) for m, (n, usd) in sorted(by_model.items())
        ),
        unpriced_turns=unpriced,
        unpriced_models=tuple(sorted(unpriced_models)),
        unused_context_usd=held(inventory.unused),
        reread_context_usd=held(inventory.reread),
        estimate_reasons=tuple(reasons),
    )


def _rates(prices: Prices, model: str | None, on: date | None) -> Rates | None:
    if not model:
        return None
    try:
        return prices.rate(model, on)
    except UnknownModelError:
        return None
