"""Claude Code hooks as a driving adapter: hook JSON in, ``ter.event`` out.

See ``docs/ter4/l1-observed.md`` for the hook-to-event table.
"""

from __future__ import annotations

from .entry import (
    HOOK_OUTPUT,
    HookResult,
    PromptLookup,
    TranscriptExists,
    TurnLookup,
    derive,
    handle_hook,
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
    "HOOK_OUTPUT",
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
    "is_internal_helper",
    "read_recordings",
    "record_payload",
    "run_hook",
    "translate",
]
