"""Claude Code adapter: JSONL transcripts to ``ter.event`` traces."""

from __future__ import annotations

from .session_source import ClaudeCodeJsonlSource
from .tool_map import CLAUDE_CODE_TOOL_KINDS, tool_kind

__all__ = ["CLAUDE_CODE_TOOL_KINDS", "ClaudeCodeJsonlSource", "tool_kind"]
