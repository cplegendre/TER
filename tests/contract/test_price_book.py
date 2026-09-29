"""Contract suite for the ``PriceBook`` port.

The shipped JSON price book and the in-memory fake run the same assertions,
so the fake cannot drift from the obligations the real adapter meets.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

from ter.adapters.driven.in_memory import InMemoryPriceBook
from ter.adapters.driven.pricing import JsonPriceBook
from ter.domain import PriceEntry, Rates, UnknownModelError
from ter.ports import PriceBook

Factory = Callable[[], PriceBook]


def _json() -> PriceBook:
    return JsonPriceBook()


def _memory() -> PriceBook:
    real = JsonPriceBook()
    return InMemoryPriceBook(
        PriceEntry(
            model=model,
            effective_from=real.entry(model).effective_from,
            rates=real.rate(model),
            source="copied from the shipped book",
        )
        for model in real.models()
    )


@pytest.fixture(params=[_json, _memory], ids=["json", "in-memory"])
def book(request: pytest.FixtureRequest) -> PriceBook:
    factory: Factory = request.param
    return factory()


def test_satisfies_the_port_protocol(book: PriceBook) -> None:
    assert isinstance(book, PriceBook)
    assert book.name
    assert book.models()


def test_every_listed_model_has_valid_rates(book: PriceBook) -> None:
    for model in book.models():
        rates = book.rate(model)
        assert isinstance(rates, Rates)
        assert rates.output >= rates.input >= rates.cache_read >= 0


def test_rates_are_deterministic(book: PriceBook) -> None:
    for model in book.models():
        assert book.rate(model) == book.rate(model)


def test_latest_rates_are_in_effect_today(book: PriceBook) -> None:
    for model in book.models():
        assert book.rate(model, date(2026, 9, 29)) == book.rate(model)


def test_unknown_model_raises(book: PriceBook) -> None:
    with pytest.raises(UnknownModelError):
        book.rate("no-such-model")


def test_date_before_any_price_raises(book: PriceBook) -> None:
    for model in book.models():
        with pytest.raises(UnknownModelError):
            book.rate(model, date(1999, 1, 1))
