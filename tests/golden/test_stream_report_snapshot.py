"""Golden snapshot of the L1 ``StreamReport`` for every corpus session.

Freezes the observables computed from the event stream alone (counts, token
estimates, usage, duplicate calls, repeated reads, open requests, the
timeline), so any change to them is a reviewed diff.
"""

from __future__ import annotations

import pytest

from ter.adapters.driven.claude_code import ClaudeCodeJsonlSource
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.application import AnalyseTrace

from .conftest import CORPUS, assert_matches_snapshot


@pytest.mark.req("TER-ANL-010")
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_stream_report_matches_golden_snapshot(name: str) -> None:
    report = AnalyseTrace(ClaudeCodeJsonlSource(), RegexTokenizer())(CORPUS[name])
    assert_matches_snapshot(f"{name}.stream", report.as_dict())
