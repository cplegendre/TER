"""Alignment scorers behind the :class:`~ter.ports.driven.AlignmentScorer` port.

The domain's default is :class:`~ter.domain.lean.intent.LexicalAlignment`
(overlap of key terms). :class:`EmbeddingAlignment` scores the same key terms
with any :class:`~ter.ports.driven.Embedder`: the cosine similarity of the two
term lists' embeddings, clipped to [0, 1]. With
:class:`~ter.adapters.driven.embedders.HashingEmbedder` it is deterministic and
offline; with a sentence-transformers embedder it adds semantic similarity.
"""

from __future__ import annotations

from ter.ports.driven import Embedder

__all__ = ["EmbeddingAlignment"]


class EmbeddingAlignment:
    """Cosine similarity of the embedded key terms, clipped to [0, 1]."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder

    @property
    def name(self) -> str:
        return f"embedding:{self._embedder.name}"

    @property
    def rule(self) -> str:
        return (
            f"Cosine similarity, clipped to [0, 1], of the {self._embedder.name} "
            "embeddings of the intent's and the event's key terms (sorted, "
            "space-joined); 0 when either side has no terms."
        )

    def score(self, intent: frozenset[str], activity: frozenset[str]) -> float:
        if not intent or not activity:
            return 0.0
        vectors = self._embedder.embed(
            [" ".join(sorted(intent)), " ".join(sorted(activity))]
        )
        cosine = float(vectors[0] @ vectors[1])
        return max(0.0, min(1.0, cosine))
