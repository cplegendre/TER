"""Ports: the Protocols between the TER core and the outside world.

Driven ports (``ter.ports.driven``) are what TER needs from outside: session
sources, tokenizers, embedders, clocks. Every driven port has one contract
suite under ``tests/contract``, and both the real adapter and its in-memory
fake must pass it.
"""

from __future__ import annotations

from .driven import Clock, Embedder, SessionSource, Tokenizer

__all__ = ["Clock", "Embedder", "SessionSource", "Tokenizer"]
