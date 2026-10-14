"""The hook on hostile payloads (TER-TST-010).

Claude Code hands the hook whatever it has; the hook fails open, so a bug in
the translation would never surface as a crash. These properties make it
surface: a payload of the wrong shape must be refused with a stated reason,
never by an exception the entry point happened to catch, and the hook's
output is always a JSON object Claude Code accepts.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from ter.adapters.driven.in_memory import InMemoryEventLog
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.claude_hooks import HookStatus, handle_hook, run_hook
from ter.application import RecordEvent

from .strategies import fuzz, json_values, mutated

HOOKS = Path(__file__).resolve().parents[1] / "fixtures" / "hooks"
PAYLOADS = [
    json.loads(p.read_text(encoding="utf-8")) for p in sorted(HOOKS.glob("*.json"))
]
FUZZ = fuzz(300)
#: The reason handle_hook gives when it caught an exception it did not expect.
CAUGHT = re.compile(r"^[A-Z][A-Za-z]*(Error|Exception)\b")

payloads = st.one_of(
    st.sampled_from(PAYLOADS).flatmap(lambda p: mutated(p, max_mutations=3)),
    json_values,
)


def _recorder() -> RecordEvent:
    return RecordEvent(RegexTokenizer(), InMemoryEventLog())


@pytest.mark.req("TER-TST-010")
@FUZZ
@given(payload=payloads)
def test_a_payload_is_recorded_or_refused_with_a_reason(payload: Any) -> None:
    result = handle_hook(json.dumps(payload), _recorder)
    event(result.status.value)
    if result.status is HookStatus.IGNORED:
        assert result.reason
        assert not CAUGHT.match(result.reason), result.reason


#: Stop is the event advice is given on; drawn often so advice is exercised.
STOP = json.loads((HOOKS / "stop.json").read_text(encoding="utf-8"))
raws = st.one_of(
    payloads.map(json.dumps),
    st.just(STOP)
    .flatmap(lambda p: st.one_of(st.just(p), mutated(p, 1)))
    .map(json.dumps),
    st.text(max_size=80),
)
advice = st.one_of(
    st.lists(st.text(min_size=1, max_size=200), max_size=3).map(tuple),
    st.just(None),  # the advice step itself fails
)


@pytest.mark.req("TER-TST-010", "TER-TST-008")
@FUZZ
@given(raw=raws, drawn=advice)
def test_the_hook_always_answers_with_a_json_object(
    raw: str, drawn: tuple[str, ...] | None
) -> None:
    def advise(result: Any) -> tuple[str, ...]:
        if drawn is None:
            raise RuntimeError("advice failed")
        return drawn

    out = io.StringIO()
    result = run_hook(io.StringIO(raw), out, _recorder, advise=advise)
    answer = json.loads(out.getvalue())
    event(f"advice shown: {bool(result.advice)}")
    if result.advice:
        assert list(answer) == ["systemMessage"]
        assert isinstance(answer["systemMessage"], str)
        assert answer["systemMessage"].startswith(result.advice[0])
    else:
        assert answer == {}
