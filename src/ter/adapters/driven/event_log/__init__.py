"""Event log adapters behind the :class:`~ter.ports.driven.EventLog` port."""

from __future__ import annotations

from .codec import event_from_record, event_to_record
from .jsonl import JsonlEventLog

__all__ = ["JsonlEventLog", "event_from_record", "event_to_record"]
