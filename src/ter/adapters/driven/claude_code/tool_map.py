"""Compatibility re-export; the table lives in :mod:`ter.adapters.claude_code_tools`."""

from __future__ import annotations

from ...claude_code_tools import CLAUDE_CODE_TOOL_KINDS, tool_kind

__all__ = ["CLAUDE_CODE_TOOL_KINDS", "tool_kind"]
