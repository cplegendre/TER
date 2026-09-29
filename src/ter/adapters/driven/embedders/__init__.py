"""Embedder adapters behind the :class:`~ter.ports.driven.Embedder` port."""

from __future__ import annotations

from .lexical import HashingEmbedder

__all__ = ["HashingEmbedder"]
