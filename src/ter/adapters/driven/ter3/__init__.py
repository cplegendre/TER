"""TER 3 scoring as a :class:`~ter.ports.driven.TerScorer` driven adapter.

Runs the TER 3 ``analyze`` pipeline (``ter_calculator.analyze_pipeline``) on a
session and returns its aggregate ratio, so the L2 scorecard keeps TER beside
the Lean dimensions.

TER 3 reaches its tokenizer and embedding model through process-wide seams in
``ter_calculator.embedding_cache``. When a tokenizer and an embedder are
injected, :class:`Ter3Scorer` pins those seams to them for the duration of
one call and restores them afterwards: that is the offline, deterministic
mode (the same pinning the golden tests use). Without them, TER 3 uses its
own tiktoken encoding and sentence-transformers model, which may download.
Because the seams are process-wide, every score (pinned or not) runs under
one process-wide lock: concurrent scores take turns, so none reads another's
tokenizer or embedder, or sees seams restored while it is still running.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from ter_calculator import embedding_cache
from ter_calculator.analyze_pipeline import analyze_session, default_analyze_args

from ....ports.driven import Embedder, Tokenizer

__all__ = ["Ter3Scorer"]

# Guards the TER 3 seams for the whole save, pin, score and restore sequence.
_SEAMS = threading.Lock()


class _TokenizerAsEncoding:
    """Presents a ``Tokenizer`` port as the tiktoken ``Encoding`` TER 3 expects."""

    def __init__(self, tokenizer: Tokenizer) -> None:
        self._tokenizer = tokenizer

    def encode(self, text: str) -> range:
        return range(self._tokenizer.count(text))


class Ter3Scorer:
    """The TER 3 aggregate ratio of a Claude Code transcript."""

    def __init__(
        self, tokenizer: Tokenizer | None = None, embedder: Embedder | None = None
    ) -> None:
        if (tokenizer is None) != (embedder is None):
            raise ValueError("Pin both the tokenizer and the embedder, or neither")
        self._tokenizer = tokenizer
        self._embedder = embedder
        if tokenizer is not None and embedder is not None:
            self.method = (
                f"TER 3, offline ({tokenizer.name} tokens, {embedder.name} embeddings)"
            )
        else:
            self.method = f"TER 3 ({embedding_cache.DEFAULT_MODEL_NAME} embeddings)"

    def score(self, ref: str | Path) -> float:
        with _SEAMS, self._pinned():
            result = analyze_session(default_analyze_args(str(ref)))
        return float(result.aggregate_ter)

    @contextmanager
    def _pinned(self) -> Iterator[None]:
        if self._tokenizer is None or self._embedder is None:
            yield
            return
        name = embedding_cache.DEFAULT_MODEL_NAME
        saved_encoding = embedding_cache._TIKTOKEN_ENC
        had_model = name in embedding_cache._MODEL_CACHE
        saved_model = embedding_cache._MODEL_CACHE.get(name)
        embedding_cache._TIKTOKEN_ENC = cast(Any, _TokenizerAsEncoding(self._tokenizer))
        embedding_cache._MODEL_CACHE[name] = self._embedder
        try:
            yield
        finally:
            embedding_cache._TIKTOKEN_ENC = saved_encoding
            if had_model:
                embedding_cache._MODEL_CACHE[name] = saved_model
            else:
                embedding_cache._MODEL_CACHE.pop(name, None)
