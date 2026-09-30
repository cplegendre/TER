"""Driven ports: what the TER core needs from the outside world."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..domain.events import Event, SessionTrace
from ..domain.pricing import Rates

if TYPE_CHECKING:
    # Annotation-only, so short-lived entry points (hooks) do not pay for
    # importing numpy just to declare the Embedder port.
    import numpy as np
    from numpy.typing import NDArray


@runtime_checkable
class SessionSource(Protocol):
    """Reads a recorded agent session and returns it as normalised events.

    Obligations, verified by ``tests/contract/test_session_source.py``:

    * the same input yields an identical trace on every call;
    * event ids are unique within a trace and sequences run 0..n-1;
    * records the adapter cannot map are counted, never silently dropped.
    """

    format_name: str

    def read(self, ref: str | Path) -> SessionTrace: ...


@runtime_checkable
class Tokenizer(Protocol):
    """Counts tokens in text.

    ``exact`` is True only when the count matches the target model's own
    tokenizer, so reports can state how much to trust token figures.
    """

    name: str
    exact: bool

    def count(self, text: str) -> int: ...


@runtime_checkable
class Embedder(Protocol):
    """Embeds texts as unit-length vectors of a fixed dimension."""

    name: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> NDArray[np.float32]:
        """Return an array of shape ``(len(texts), dimension)``."""
        ...


@runtime_checkable
class Clock(Protocol):
    """Supplies the current time, so time-dependent logic is testable."""

    def now(self) -> datetime: ...


@runtime_checkable
class PriceBook(Protocol):
    """Supplies per-model token prices, by date.

    Obligations, verified by ``tests/contract/test_price_book.py``:

    * ``rate`` accepts every name in ``models()`` and returns the same rates
      on every call;
    * with ``at=None`` it returns the latest rates; with a date, the rates in
      effect on that date;
    * an unknown model, or a date before a model's first price, raises
      ``ter.domain.UnknownModelError``.
    """

    name: str

    def models(self) -> tuple[str, ...]: ...

    def rate(self, model: str, at: date | None = None) -> Rates: ...


@runtime_checkable
class EventLog(Protocol):
    """An append-only store of normalised events, grouped by session.

    Live mode appends each event as it arrives; analysis replays the log. The
    log may hold the same event twice (a retried append): consumers rely on
    event ids, not on the log, for idempotency.

    Obligations, verified by ``tests/contract/test_event_log.py``:

    * ``events(session_id)`` returns appended events in append order, equal
      field for field to what was appended;
    * sessions never leak into each other, and an unknown session is empty;
    * ``sessions()`` lists every session with at least one event, sorted.
    """

    def append(self, event: Event) -> None: ...

    def events(self, session_id: str) -> tuple[Event, ...]: ...

    def sessions(self) -> tuple[str, ...]: ...
