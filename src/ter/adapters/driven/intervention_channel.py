"""The advice channel: advice as messages for the developer (L4 Advisory).

Delivering an intervention renders it as text (the advice, the signal behind
it and its evidence, TER-INT-014) and keeps it. The entry point that owns the
developer's screen shows the messages; for Claude Code that is the hook,
which prints them as a message the developer sees and the model does not
(TER-INT-002). This adapter never writes to the agent's input.
"""

from __future__ import annotations

from ...domain.ledger import Intervention

__all__ = ["MessageChannel"]


class MessageChannel:
    """Collects each delivered intervention as one message, in order."""

    name = "message"

    def __init__(self) -> None:
        self._messages: list[str] = []

    def deliver(self, intervention: Intervention) -> None:
        self._messages.append(intervention.message())

    def delivered(self) -> tuple[str, ...]:
        return tuple(self._messages)
