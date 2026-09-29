"""Ports: the Protocols between the TER core and the outside world.

Driven ports (``ter.ports.driven``) are what TER needs from outside: session
sources, tokenizers, embedders, clocks, price books. Every driven port has one contract
suite under ``tests/contract``, and both the real adapter and its in-memory
fake must pass it.

Driving ports (``ter.ports.driving``) are what TER offers to entry points such
as hooks and the CLI.
"""

from __future__ import annotations

from .driven import Clock, Embedder, EventLog, PriceBook, SessionSource, Tokenizer
from .driving import EventIngest

__all__ = [
    "Clock",
    "Embedder",
    "EventIngest",
    "EventLog",
    "PriceBook",
    "SessionSource",
    "Tokenizer",
]
