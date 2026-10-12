"""Policy file adapter behind the :class:`~ter.ports.driven.InterventionPolicies` port.

:class:`JsonInterventionPolicies` reads a ``ter.intervention-policies/1``
JSON file: the declarative policies, the eligibility list and the approval
record a person writes with ``python -m ter policy approve`` (TER-INT-008,
TER-INT-015). Validation is the domain's (``PolicySet.from_mapping``), so a
file and an in-memory policy set obey the same rules. It never approves.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...domain.intervention import PolicyError
from ...domain.policy_set import PolicySet

__all__ = ["JsonInterventionPolicies"]


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """A JSON object, refusing a repeated key.

    The approval digest covers the parsed values, so a key written twice
    would let the earlier, discarded copy change without voiding approval.
    """
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise PolicyError(f"duplicate key {key!r}")
        document[key] = value
    return document


class JsonInterventionPolicies:
    """Reads intervention policies from a JSON file named by ``ref``."""

    name = "json"

    def policies(self, ref: str | Path) -> PolicySet:
        path = Path(ref).expanduser()
        try:
            document = json.loads(
                # utf-8-sig: Windows editors and PowerShell 5 write a BOM.
                path.read_text(encoding="utf-8-sig"),
                object_pairs_hook=_unique_keys,
            )
        except PolicyError as exc:
            raise PolicyError(f"{path}: {exc}") from None
        except FileNotFoundError:
            raise PolicyError(f"{path}: no such policy file") from None
        except (OSError, UnicodeDecodeError) as exc:
            raise PolicyError(f"{path}: cannot read: {exc}") from None
        except json.JSONDecodeError as exc:
            raise PolicyError(f"{path}: not JSON: {exc}") from None
        try:
            return PolicySet.from_mapping(document)
        except PolicyError as exc:
            raise type(exc)(f"{path}: {exc}") from None
