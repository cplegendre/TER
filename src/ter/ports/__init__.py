"""Ports: the Protocols between the TER core and the outside world.

Driven ports (``ter.ports.driven``) are what TER needs from outside: session
sources, tokenizers, embedders, clocks, price books. Every driven port has one contract
suite under ``tests/contract``, and both the real adapter and its in-memory
fake must pass it.

Driving ports (``ter.ports.driving``) are what TER offers to entry points such
as hooks and the CLI.
"""

from __future__ import annotations

from .driven import (
    Clock,
    Embedder,
    EventLog,
    PriceBook,
    SessionSource,
    TerScorer,
    Tokenizer,
)
from .driving import EventIngest

#: Driven ports a capability (``ter.capabilities`` entry point) can plug into,
#: by the name its key uses: ``<Port>.<adapter>`` (ADR 0005).
DRIVEN_PORTS: dict[str, type[object]] = {
    "Clock": Clock,
    "Embedder": Embedder,
    "EventLog": EventLog,
    "PriceBook": PriceBook,
    "SessionSource": SessionSource,
    "TerScorer": TerScorer,
    "Tokenizer": Tokenizer,
}

__all__ = [
    "DRIVEN_PORTS",
    "Clock",
    "Embedder",
    "EventIngest",
    "EventLog",
    "PriceBook",
    "SessionSource",
    "TerScorer",
    "Tokenizer",
]
