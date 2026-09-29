"""A deterministic lexical embedder: the offline fallback and the test double.

Texts become signed feature-hashed bags of words and word bigrams, then are
L2-normalised. Texts that share vocabulary have positive cosine similarity,
texts that share none have similarity near zero, and the result is identical
on every platform because hashing uses BLAKE2b rather than Python's salted
``hash``. It carries no semantic knowledge, so reports that use it must say so.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

_WORD = re.compile(r"[a-z0-9_]+")


class HashingEmbedder:
    """Feature-hashing embedder satisfying the ``Embedder`` port."""

    def __init__(self, dimension: int = 384) -> None:
        if dimension < 8:
            raise ValueError("dimension must be at least 8")
        self.dimension = dimension
        self.name = f"lexical-hash-{dimension}"

    def embed(self, texts: Sequence[str]) -> NDArray[np.float32]:
        matrix = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for row, text in enumerate(texts):
            for feature in _features(text):
                index, sign = self._slot(feature)
                matrix[row, index] += sign
            norm = float(np.linalg.norm(matrix[row]))
            if norm > 0.0:
                matrix[row] /= norm
        return matrix

    def encode(self, texts: str | Sequence[str], **_: object) -> NDArray[np.float32]:
        """``sentence-transformers``-shaped call, so TER 3 code can use this embedder.

        A single string returns a 1-D vector; a sequence returns a 2-D matrix.
        Keyword arguments such as ``convert_to_numpy`` are accepted and ignored.
        """
        if isinstance(texts, str):
            vector: NDArray[np.float32] = self.embed([texts])[0]
            return vector
        return self.embed(list(texts))

    def _slot(self, feature: str) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        return value % self.dimension, 1.0 if (value >> 63) & 1 else -1.0


def _features(text: str) -> list[str]:
    words = _WORD.findall(text.lower())
    bigrams = [f"{a} {b}" for a, b in zip(words, words[1:])]
    return words + bigrams
