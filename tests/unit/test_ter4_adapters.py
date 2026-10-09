"""Unit tests for TER 4 driven adapters."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from ter.adapters.driven.claude_code import (
    CLAUDE_CODE_TOOL_KINDS,
    ClaudeCodeJsonlSource,
    tool_kind,
)
from ter.adapters.driven.embedders import HashingEmbedder
from ter.adapters.driven.in_memory import FixedClock, SystemClock
from ter.adapters.driven.tokenizers import RegexTokenizer, TiktokenTokenizer
from ter.domain import Actor, EventKind, ToolKind
from ter.ports import Clock, Embedder, Tokenizer


def _write(path: Path, records: list[Any]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def _assistant(uuid: str, req: str, content: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uuid,
        "sessionId": "s1",
        "requestId": req,
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {
            "role": "assistant",
            "content": content,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    }


class TestToolMap:
    @pytest.mark.req("TER-SRC-003")
    def test_known_tools_map_to_kinds(self) -> None:
        assert tool_kind("Read") is ToolKind.FS_READ
        assert tool_kind("Bash") is ToolKind.EXEC_SHELL
        assert tool_kind("Task") is ToolKind.AGENT_HANDOFF

    @pytest.mark.parametrize("name", [None, "", "mcp__github__get_me", "read"])
    def test_unknown_tools_are_other(self, name: str | None) -> None:
        assert tool_kind(name) is ToolKind.OTHER

    @pytest.mark.req("TER-SRC-003")
    def test_every_kind_except_other_has_a_claude_tool(self) -> None:
        mapped = set(CLAUDE_CODE_TOOL_KINDS.values())
        assert mapped == set(ToolKind) - {ToolKind.OTHER}


class TestClaudeCodeJsonlSource:
    @pytest.mark.req("TER-SRC-002")
    def test_unrecognised_records_are_counted_not_dropped(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "s.jsonl",
            [
                {"type": "summary", "summary": "x"},
                {
                    "type": "user",
                    "uuid": "u1",
                    "sessionId": "s1",
                    "message": {"role": "user", "content": "hello"},
                },
                {"type": "system", "subtype": "compact"},
                ["not", "an", "object"],
                {"no_type": True},
            ],
        )
        trace = ClaudeCodeJsonlSource().read(path)
        # summary and system are documented metadata: accounted for, no event.
        assert trace.unrecognised_by_type == {"list": 1, "<missing>": 1}
        assert trace.metadata_by_type == {"summary": 1, "system": 1}
        assert [r.line for r in trace.unrecognised] == [4, 5]
        assert trace.coverage == pytest.approx(3 / 5)

    @pytest.mark.req("TER-SRC-001")
    def test_tool_results_inherit_their_request_kind(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "s.jsonl",
            [
                _assistant(
                    "a1",
                    "r1",
                    [
                        {"type": "thinking", "thinking": "look"},
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Grep",
                            "input": {"pattern": "x"},
                        },
                    ],
                ),
                {
                    "type": "user",
                    "uuid": "u2",
                    "sessionId": "s1",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "t1",
                                "content": "a.py:1",
                            },
                            {
                                "type": "tool_result",
                                "tool_use_id": "orphan",
                                "content": "?",
                            },
                            {"type": "image", "source": {}},
                        ],
                    },
                },
            ],
        )
        events = ClaudeCodeJsonlSource().read(path).events
        kinds = [(e.kind, e.actor) for e in events]
        assert kinds == [
            (EventKind.REASONING, Actor.ASSISTANT),
            (EventKind.TOOL_REQUESTED, Actor.ASSISTANT),
            (EventKind.TOOL_COMPLETED, Actor.TOOL),
            (EventKind.TOOL_COMPLETED, Actor.TOOL),
        ]
        request, result, orphan = events[1].tool, events[2].tool, events[3].tool
        assert request is not None and result is not None and orphan is not None
        assert request.arguments == {"pattern": "x"}
        assert (result.native_name, result.kind, result.call_id) == (
            "Grep",
            ToolKind.FS_SEARCH,
            "t1",
        )
        assert (orphan.native_name, orphan.kind) == ("", ToolKind.OTHER)
        assert events[1].text == '{"pattern":"x"}'

    @pytest.mark.req("TER-SRC-007", "TER-ANL-001")
    def test_user_prompts_are_intent_not_generated_work(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "s.jsonl",
            [
                {
                    "type": "user",
                    "uuid": "u1",
                    "sessionId": "s1",
                    "message": {"role": "user", "content": "Fix the failing test"},
                },
                _assistant("a1", "r1", [{"type": "text", "text": "Done"}]),
            ],
        )
        trace = ClaudeCodeJsonlSource().read(path)
        prompt = trace.events[0]
        assert (prompt.kind, prompt.actor) == (EventKind.PROMPT, Actor.USER)
        assert prompt.kind.value == "intent.stated"
        assert "Fix the failing test" in prompt.text
        assert prompt not in trace.generated()

    def test_usage_is_attached_once_per_model_turn(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "s.jsonl",
            [
                _assistant(
                    "a1",
                    "r1",
                    [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}],
                )
            ],
        )
        events = ClaudeCodeJsonlSource().read(path).events
        assert [e.usage is not None for e in events] == [True, False]
        assert events[0].usage is not None and events[0].usage.output_tokens == 5
        assert events[0].timestamp == datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestTokenizers:
    def test_regex_tokenizer_counts_words_numbers_and_symbols(self) -> None:
        tokenizer = RegexTokenizer()
        assert isinstance(tokenizer, Tokenizer)
        assert tokenizer.count("") == 0
        assert tokenizer.count("parse_duration('2h') == 7200") == 12
        assert tokenizer.count("   ") == 1

    def test_tiktoken_tokenizer_is_lazy_and_counts_via_encoding(self) -> None:
        tokenizer = TiktokenTokenizer()
        assert isinstance(tokenizer, Tokenizer)
        assert tokenizer.name == "tiktoken-cl100k_base"
        assert tokenizer.count("") == 0

        class _Encoding:
            def encode(self, text: str) -> list[int]:
                return list(range(len(text.split())))

        tokenizer._encoding = _Encoding()
        assert tokenizer.count("three word text") == 3


class TestHashingEmbedder:
    def test_satisfies_the_port_and_returns_unit_vectors(self) -> None:
        embedder = HashingEmbedder(64)
        assert isinstance(embedder, Embedder)
        vectors = embedder.embed(["fix the parser", "", "fix the parser"])
        assert vectors.shape == (3, 64)
        assert vectors.dtype == np.float32
        assert np.linalg.norm(vectors[0]) == pytest.approx(1.0, abs=1e-6)
        assert not vectors[1].any()
        assert np.array_equal(vectors[0], vectors[2])

    def test_shared_vocabulary_scores_higher_than_unrelated_text(self) -> None:
        a, b, c = HashingEmbedder().embed(
            [
                "fix the duration parser for hours",
                "the duration parser fails for hours",
                "upgrade requests in pyproject",
            ]
        )
        assert float(a @ b) > float(a @ c) + 0.2

    def test_encode_mimics_sentence_transformers_shapes(self) -> None:
        embedder = HashingEmbedder(32)
        assert embedder.encode("one", convert_to_numpy=True).shape == (32,)
        assert embedder.encode(["one", "two"]).shape == (2, 32)

    def test_output_is_frozen_across_platforms(self) -> None:
        vector = HashingEmbedder(16).embed(["golden"])[0]
        assert [round(float(x), 4) for x in vector if x] == [-1.0]
        assert int(np.flatnonzero(vector)[0]) == 11

    def test_rejects_tiny_dimensions(self) -> None:
        with pytest.raises(ValueError):
            HashingEmbedder(4)


class TestClocks:
    def test_fixed_clock_advances_only_when_told(self) -> None:
        start = datetime(2026, 9, 29, tzinfo=timezone.utc)
        clock = FixedClock(start)
        assert isinstance(clock, Clock)
        assert clock.now() == start
        clock.advance(timedelta(seconds=5))
        assert clock.now() == start + timedelta(seconds=5)

    def test_system_clock_is_timezone_aware(self) -> None:
        now = SystemClock().now()
        assert isinstance(SystemClock(), Clock)
        assert now.tzinfo is not None
