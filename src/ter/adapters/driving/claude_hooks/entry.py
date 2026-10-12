"""The hook entry point: raw hook input in, events applied, never an exception out.

Claude Code runs a hook command per event and blocks on it, so this adapter
fails open. Malformed JSON, an unexpected payload shape, or a failure in the
ingest behind it (a full disk, a read-only log directory) all become an
``ignored`` result; the agent session is never interrupted by TER.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import IO

from ....domain.stream import Signals
from ....ports.driven import Clock
from ....ports.driving import EventIngest
from ...claude_code_turns import PromptRecord, transcript_prompt, transcript_turn
from .record import record_payload
from .translate import HookStatus, HookTranslation, is_internal_helper, translate

__all__ = [
    "ADVISE_ON",
    "AdviseHook",
    "HookResult",
    "PromptLookup",
    "TurnLookup",
    "TranscriptExists",
    "derive",
    "handle_hook",
    "hook_output",
    "run_hook",
]

#: Gives the advice due for a handled payload: ``(result) -> messages``.
AdviseHook = Callable[["HookResult"], tuple[str, ...]]

#: Finds the turn a Stop closes: ``(transcript_path, received_at) -> uuid``.
TurnLookup = Callable[[str, datetime | None], str | None]
#: Finds the record holding a prompt:
#: ``(transcript_path, prompt, received_at) -> record``.
PromptLookup = Callable[[str, str, datetime | None], PromptRecord | None]

#: Whether a subagent transcript file exists: ``(agent_transcript_path) -> bool``.
TranscriptExists = Callable[[str], bool]


def _is_file(path: str) -> bool:
    return Path(path).is_file()


#: What the hook prints for Claude Code: an empty object changes nothing.
HOOK_OUTPUT = "{}"

#: The hook events after which advice is given: the end of a turn, so the
#: developer reads it between turns and the analysis runs once per turn.
ADVISE_ON = frozenset({"Stop"})


#: Claude Code cuts hook output past 10,000 characters; advice stays inside it.
MESSAGE_LIMIT = 10_000


def hook_output(advice: tuple[str, ...]) -> str:
    """The hook's stdout: ``{}``, or the advice as a message to the developer.

    ``systemMessage`` is shown to the user and never added to the model's
    context, so advice changes nothing the agent reads (TER-INT-002). It is
    the only hook response field TER writes; the architecture test
    ``tests/architecture/test_no_intervention.py`` keeps it that way. Advice
    that would not fit in :data:`MESSAGE_LIMIT` is counted at the end rather
    than cut mid-message; every piece is in the ledger.
    """
    if not advice:
        return HOOK_OUTPUT
    return json.dumps({"systemMessage": _fit(advice)}, ensure_ascii=False)


def _fit(advice: tuple[str, ...]) -> str:
    shown: list[str] = []
    size = 0
    for n, message in enumerate(advice):
        rest = len(advice) - n - 1
        tail = len(f"\n\nand {rest} more pieces of advice in the ledger") if rest else 0
        if shown and size + 2 + len(message) + tail > MESSAGE_LIMIT:
            break
        shown.append(message)
        size += len(message) + (2 if size else 0)
    hidden = len(advice) - len(shown)
    if hidden:
        noun = "piece" if hidden == 1 else "pieces"
        shown.append(f"and {hidden} more {noun} of advice in the ledger")
    return "\n\n".join(shown)


@dataclass(frozen=True)
class HookResult:
    """What handling one hook payload did."""

    status: HookStatus
    hook_event_name: str | None = None
    session_id: str | None = None
    appended: int = 0
    signals: tuple[Signals, ...] = ()
    reason: str = ""
    #: Where the raw payload was recorded, when recording was asked for.
    recorded_to: Path | None = None
    #: Why recording failed; the payload is still handled.
    record_error: str = ""
    #: The advice shown to the developer for this payload (L4 Advisory).
    advice: tuple[str, ...] = ()
    #: Why no advice could be given; the hook still answers.
    advice_error: str = ""


def derive(
    payload: object,
    received_at: datetime | None,
    turns: TurnLookup | None = transcript_turn,
    prompts: PromptLookup | None = transcript_prompt,
    exists: TranscriptExists | None = _is_file,
) -> HookTranslation:
    """The events the live hook derives from one decoded payload.

    For a Stop, ``turns`` finds the transcript turn the stop closes, so the
    ``task.completed`` id matches the session source's (TER-OBS-005). For a
    UserPromptSubmit, ``prompts`` finds the transcript record holding the
    prompt, so the ``intent.stated`` id matches too (TER-OBS-007). The hook
    check replays recordings through this same function. A lookup that fails
    or finds nothing leaves the event keyed by the hook's own fallback.

    For a SubagentStop with no ``agent_type``, ``exists`` stats the file its
    ``agent_transcript_path`` names; a missing file marks a Claude Code
    internal helper agent, which yields no event (see
    :mod:`.translate`). A stat that fails counts as present, so the stop
    keeps its event, as before.
    """
    missing = False
    if isinstance(payload, Mapping) and exists is not None:
        agent_path = payload.get("agent_transcript_path")
        if (
            payload.get("hook_event_name") == "SubagentStop"
            and isinstance(agent_path, str)
            and agent_path
            and is_internal_helper(payload, True)
        ):
            try:
                missing = not exists(agent_path)
            except Exception:  # noqa: BLE001 - a hook must fail open
                missing = False
    turn: str | None = None
    prompt_at: PromptRecord | None = None
    name = payload.get("hook_event_name") if isinstance(payload, Mapping) else None
    path = payload.get("transcript_path") if isinstance(payload, Mapping) else None
    if isinstance(payload, Mapping) and isinstance(path, str) and path:
        try:
            if name == "Stop" and turns is not None:
                turn = turns(path, received_at)
            prompt = payload.get("prompt")
            if name == "UserPromptSubmit" and prompts is not None:
                if isinstance(prompt, str):
                    prompt_at = prompts(path, prompt, received_at)
        except Exception:  # noqa: BLE001 - a hook must fail open
            turn, prompt_at = None, None
    return translate(
        payload,
        received_at=received_at,
        turn=turn,
        prompt_at=prompt_at,
        agent_transcript_missing=missing,
    )


def handle_hook(
    raw: str | bytes | Mapping[str, object],
    ingest: EventIngest | Callable[[], EventIngest],
    *,
    clock: Clock | None = None,
    turns: TurnLookup | None = transcript_turn,
    prompts: PromptLookup | None = transcript_prompt,
    exists: TranscriptExists | None = _is_file,
) -> HookResult:
    """Translate one hook payload and apply its events to ``ingest``.

    ``ingest`` may be a factory, called only when there are events to apply,
    so lifecycle hooks and bad input never touch the event log. ``turns``
    finds the turn a Stop closes and ``prompts`` the record holding a prompt
    (see :func:`derive`); ``exists`` stats a SubagentStop's transcript file.
    An internal helper agent's SubagentStop returns status ``internal``
    without touching ``ingest``.
    """
    try:
        payload: object = raw if isinstance(raw, Mapping) else json.loads(raw)
    except (ValueError, TypeError) as error:
        return HookResult(HookStatus.IGNORED, reason=f"invalid JSON: {error}")
    try:
        received_at: datetime | None = clock.now() if clock is not None else None
        translation = derive(payload, received_at, turns, prompts, exists)
        if translation.status is not HookStatus.RECORDED:
            return _unrecorded(translation)
        sink = ingest if isinstance(ingest, EventIngest) else ingest()
        signals = tuple(sink.apply(event) for event in translation.events)
    except Exception as error:  # noqa: BLE001 - a hook must fail open
        return HookResult(HookStatus.IGNORED, reason=f"{type(error).__name__}: {error}")
    return HookResult(
        HookStatus.RECORDED,
        translation.hook_event_name,
        translation.session_id,
        appended=sum(1 for s in signals if s.accepted),
        signals=signals,
    )


def _unrecorded(translation: HookTranslation) -> HookResult:
    return HookResult(
        translation.status,
        translation.hook_event_name,
        translation.session_id,
        reason=translation.reason,
    )


def run_hook(
    stdin: IO[str],
    stdout: IO[str],
    ingest: EventIngest | Callable[[], EventIngest],
    *,
    clock: Clock | None = None,
    record_to: Path | None = None,
    advise: AdviseHook | None = None,
) -> HookResult:
    """Read one payload from ``stdin``, handle it, and print the hook output.

    With ``record_to``, the raw payload is first recorded there (see
    :mod:`.record`). With ``advise`` (L4 Advisory), a recorded payload of an
    :data:`ADVISE_ON` event asks it for the advice due, and the hook prints
    that advice as a message to the developer (:func:`hook_output`); advice
    that cannot be given leaves the output ``{}``. Always returns; the caller
    exits 0.
    """
    try:
        raw = stdin.read()
    except Exception as error:  # noqa: BLE001 - a hook must fail open
        result = HookResult(HookStatus.IGNORED, reason=f"unreadable stdin: {error}")
    else:
        if record_to is None:
            result = handle_hook(raw, ingest, clock=clock)
        else:
            # Read the clock once, so a recording replays to the very ids
            # (prompts and stops include the second received) seen live. A
            # clock that fails is left to handle_hook, which ignores the
            # payload with the reason, as without recording.
            moment = _read_clock(clock)
            recorded_to, record_error = _record(raw, record_to, moment)
            result = replace(
                handle_hook(raw, ingest, clock=_At(moment) if moment else clock),
                recorded_to=recorded_to,
                record_error=record_error,
            )
    if advise is not None:
        result = _advise(result, advise)
    stdout.write(hook_output(result.advice) + "\n")
    return result


def _advise(result: HookResult, advise: AdviseHook) -> HookResult:
    if (
        result.status is not HookStatus.RECORDED
        or result.hook_event_name not in ADVISE_ON
    ):
        return result
    try:
        advice = tuple(advise(result))
    except Exception as error:  # noqa: BLE001 - a hook must fail open
        return replace(result, advice_error=f"{type(error).__name__}: {error}")
    return replace(result, advice=advice)


@dataclass(frozen=True)
class _At:
    """A clock stopped at one moment."""

    moment: datetime

    def now(self) -> datetime:
        return self.moment


def _read_clock(clock: Clock | None) -> datetime | None:
    try:
        return clock.now() if clock is not None else None
    except Exception:  # noqa: BLE001 - a hook must fail open
        return None


def _record(
    raw: str, directory: Path, received_at: datetime | None
) -> tuple[Path | None, str]:
    try:
        return record_payload(raw, directory, received_at=received_at), ""
    except Exception as error:  # noqa: BLE001 - a hook must fail open
        return None, f"{type(error).__name__}: {error}"
