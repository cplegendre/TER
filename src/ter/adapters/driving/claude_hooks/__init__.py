"""Claude Code hooks as a driving adapter: hook JSON in, ``ter.event`` out.

See ``docs/ter4/l1-observed.md`` for the hook-to-event table.
"""

from __future__ import annotations

from .entry import (
    ADVISE_ON,
    HOOK_OUTPUT,
    AdviseHook,
    HookResult,
    PromptLookup,
    TranscriptExists,
    TurnLookup,
    derive,
    handle_hook,
    hook_output,
    run_hook,
)
from .record import RECORDING_SCHEMA, Recording, read_recordings, record_payload
from .translate import (
    INTERNAL_HELPER,
    LIFECYCLE_HOOKS,
    HookStatus,
    HookTranslation,
    is_internal_helper,
    translate,
)

__all__ = [
    "ADVISE_ON",
    "HOOK_OUTPUT",
    "AdviseHook",
    "INTERNAL_HELPER",
    "LIFECYCLE_HOOKS",
    "RECORDING_SCHEMA",
    "HookResult",
    "HookStatus",
    "HookTranslation",
    "PromptLookup",
    "TranscriptExists",
    "TurnLookup",
    "Recording",
    "derive",
    "handle_hook",
    "hook_output",
    "is_internal_helper",
    "read_recordings",
    "record_payload",
    "run_hook",
    "translate",
]
