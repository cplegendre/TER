"""Tokenizer adapters behind the :class:`~ter.ports.driven.Tokenizer` port."""

from __future__ import annotations

import re
from typing import Any

__all__ = ["RegexTokenizer", "TiktokenTokenizer"]

# Words, runs of digits, and single punctuation or symbol characters. Close
# enough to BPE counts for relative comparisons, and identical on every
# platform, which is what regression tests need.
_TOKEN_PATTERN = re.compile(r"[A-Za-z]+|\d+|[^\sA-Za-z\d]")


class RegexTokenizer:
    """Deterministic, offline token estimate. Never exact."""

    name = "regex-v1"
    exact = False

    def count(self, text: str) -> int:
        return max(1, len(_TOKEN_PATTERN.findall(text))) if text else 0


class TiktokenTokenizer:
    """``cl100k_base`` BPE via tiktoken, an approximation of Claude's tokenizer.

    The encoding is loaded on first use. tiktoken downloads it once over the
    network, so offline environments should use :class:`RegexTokenizer`.
    """

    exact = False

    def __init__(self, encoding: str = "cl100k_base") -> None:
        self.name = f"tiktoken-{encoding}"
        self._encoding_name = encoding
        self._encoding: Any = None

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._encoding is None:
            import tiktoken

            self._encoding = tiktoken.get_encoding(self._encoding_name)
        return len(self._encoding.encode(text))
