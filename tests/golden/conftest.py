"""Hermetic harness for the golden characterisation tests.

TER 3 reaches its tokenizer and embedding model through two process-wide
seams in ``ter_calculator.embedding_cache``. Pinning both to TER 4's
deterministic offline adapters makes every golden score reproducible on any
machine, with no network and no model weights, so a golden diff can only mean
that analysis logic changed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import pytest

from ter.adapters.driven.embedders import HashingEmbedder
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter_calculator import embedding_cache

from .corpus import CORPUS, GOLDEN_DIR, REPO_ROOT

__all__ = [
    "CORPUS",
    "assert_matches_snapshot",
    "assert_matches_text_snapshot",
    "pinned_models",
]

SNAPSHOT_DIR = GOLDEN_DIR / "snapshots"
UPDATE_ENV = "TER_UPDATE_GOLDEN"


class _TokenizerAsEncoding:
    """Presents a ``Tokenizer`` port as the tiktoken ``Encoding`` TER 3 expects."""

    def __init__(self, tokenizer: RegexTokenizer) -> None:
        self._tokenizer = tokenizer

    def encode(self, text: str) -> range:
        return range(self._tokenizer.count(text))


@pytest.fixture
def pinned_models(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Route TER 3's tokenizer and embedder through deterministic adapters."""
    monkeypatch.setattr(
        embedding_cache, "_TIKTOKEN_ENC", _TokenizerAsEncoding(RegexTokenizer())
    )
    monkeypatch.setitem(
        embedding_cache._MODEL_CACHE,
        embedding_cache.DEFAULT_MODEL_NAME,
        HashingEmbedder(embedding_cache.EMBEDDING_DIM),
    )
    yield


def assert_matches_snapshot(name: str, actual: dict[str, Any]) -> None:
    """Compare ``actual`` with ``snapshots/<name>.json``.

    Set ``TER_UPDATE_GOLDEN=1`` to (re)write snapshots. A snapshot change is a
    behaviour change and belongs in its own reviewed commit.
    """
    path = SNAPSHOT_DIR / f"{name}.json"
    rendered = json.dumps(actual, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get(UPDATE_ENV) == "1":
        path.write_text(rendered, encoding="utf-8")
        return
    if not path.exists():
        pytest.fail(
            f"Missing golden snapshot {path.relative_to(REPO_ROOT)}; "
            f"run with {UPDATE_ENV}=1 to create it"
        )
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert json.loads(rendered) == expected, (
        f"{name} no longer matches its golden snapshot. If the change is "
        f"intended, regenerate with {UPDATE_ENV}=1 and commit the diff."
    )


def assert_matches_text_snapshot(relative: str, actual: str) -> None:
    """Compare rendered text (SVG, HTML) with ``snapshots/<relative>``.

    Same contract as :func:`assert_matches_snapshot`: ``TER_UPDATE_GOLDEN=1``
    (re)writes the file, and any diff is a reviewed change.
    """
    path = SNAPSHOT_DIR / relative
    if os.environ.get(UPDATE_ENV) == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
        return
    if not path.exists():
        pytest.fail(
            f"Missing golden snapshot {path.relative_to(REPO_ROOT)}; "
            f"run with {UPDATE_ENV}=1 to create it"
        )
    expected = path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"{relative} no longer matches its golden snapshot. If the change is "
        f"intended, regenerate with {UPDATE_ENV}=1 and commit the diff."
    )
