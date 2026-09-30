"""Price book adapters behind the :class:`~ter.ports.driven.PriceBook` port.

:class:`JsonPriceBook` reads a dated price file. With no path it reads the
file shipped inside the ``ter`` package (``ter/data/price_book.json``), so
prices change by editing data and every rate carries its source.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from ....domain.pricing import PriceEntry, PriceSchedule, Rates

__all__ = [
    "PRICE_BOOK_SCHEMA",
    "JsonPriceBook",
    "default_price_book",
    "parse_price_book",
]

PRICE_BOOK_SCHEMA = "ter.price_book/1"
_RATE_KEYS = ("input", "output", "cache_read", "cache_write")


def parse_price_book(document: Any) -> list[PriceEntry]:
    """Validate a decoded price book document and return its entries.

    Raises:
        ValueError: If the document is not a ``ter.price_book/1`` book of
            USD per-million-token prices, or an entry is malformed.
    """
    if not isinstance(document, dict):
        raise ValueError("A price book must be a JSON object")
    if document.get("schema") != PRICE_BOOK_SCHEMA:
        raise ValueError(
            f"Unsupported price book schema {document.get('schema')!r}; "
            f"expected {PRICE_BOOK_SCHEMA!r}"
        )
    if (
        document.get("currency") != "USD"
        or document.get("unit") != "per_million_tokens"
    ):
        raise ValueError("Price books must be in USD per million tokens")
    prices = document.get("prices")
    if not isinstance(prices, list) or not prices:
        raise ValueError("A price book needs a non-empty 'prices' list")
    return [_parse_entry(raw, index) for index, raw in enumerate(prices)]


def _parse_entry(raw: Any, index: int) -> PriceEntry:
    where = f"prices[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    try:
        model = raw["model"]
        effective_from = date.fromisoformat(raw["effective_from"])
        rates_raw = raw["rates"]
        values = {key: rates_raw[key] for key in _RATE_KEYS}
        for key, value in values.items():
            # bool is an int subclass; a true/false rate is a typo, not $1/$0.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"rates.{key} must be a number, not {value!r}")
        rates = Rates(**{key: float(value) for key, value in values.items()})
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{where} is malformed: {exc}") from exc
    source = raw.get("source", "")
    aliases = raw.get("aliases", [])
    if not isinstance(model, str) or not model:
        raise ValueError(f"{where}.model must be a non-empty string")
    if not isinstance(source, str) or not source:
        raise ValueError(f"{where}.source must say where the rates came from")
    if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
        raise ValueError(f"{where}.aliases must be a list of strings")
    return PriceEntry(
        model=model,
        effective_from=effective_from,
        rates=rates,
        source=source,
        aliases=tuple(aliases),
    )


class JsonPriceBook:
    """A price book loaded from a ``ter.price_book/1`` JSON file."""

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            text = (
                resources.files("ter")
                .joinpath("data", "price_book.json")
                .read_text(encoding="utf-8")
            )
            self.name = "ter:data/price_book.json"
        else:
            text = Path(path).read_text(encoding="utf-8")
            self.name = str(path)
        self._schedule = PriceSchedule(parse_price_book(json.loads(text)))

    def models(self) -> tuple[str, ...]:
        return self._schedule.models()

    def entry(self, model: str, at: date | None = None) -> PriceEntry:
        """The full dated entry, including its source note."""
        return self._schedule.entry(model, at)

    def rate(self, model: str, at: date | None = None) -> Rates:
        return self._schedule.rate(model, at)


@lru_cache(maxsize=1)
def default_price_book() -> JsonPriceBook:
    """The price book shipped with TER, loaded once per process."""
    return JsonPriceBook()
