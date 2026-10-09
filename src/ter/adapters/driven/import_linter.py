"""Architecture contracts in import-linter's configuration format.

Reads the contracts a repository declares for `import-linter
<https://import-linter.readthedocs.io/>`_, from ``.importlinter`` or
``setup.cfg`` (INI: an ``[importlinter]`` section and one
``[importlinter:contract:<id>]`` section per contract) or ``pyproject.toml``
(``[tool.importlinter]`` and its ``[[tool.importlinter.contracts]]`` tables).

The contract types TER evaluates are ``forbidden``, ``layers`` and
``independence``. Other types (``acyclic_siblings``, ``protected``, custom
ones) are left out: TER has no rule for them, and guessing one would report
violations the project never declared. Layers keep import-linter's notation:
``a | b`` for independent siblings, ``a : b`` for siblings that may import
each other, and a parenthesised optional layer ``(a)``.

The adapter does no IO: it parses text the caller read through
``RepositoryEvidence`` (TER-EVD-001), so the contracts are those of the
commit being analysed.
"""

from __future__ import annotations

import configparser
import tomllib
from collections.abc import Mapping, Sequence

from ...domain.repository import (
    ArchitectureContract,
    ContractFormatError,
    ContractKind,
    Layer,
)

__all__ = ["ImportLinterContracts"]

_INI_CONTRACT = "importlinter:contract:"


def _modules(value: object, where: str, key: str) -> tuple[str, ...]:
    """A list of module names, from a TOML list or a multi-line INI value."""
    if value is None:
        return ()
    if isinstance(value, str):
        items: Sequence[object] = value.splitlines()
    elif isinstance(value, list):
        items = value
    else:
        raise ContractFormatError(f"{where}: {key} must be a list of module names")
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ContractFormatError(f"{where}: {key} must be a list of module names")
        text = item.split("#", 1)[0].strip()
        if text:
            out.append(text)
    return tuple(out)


def _layer(text: str) -> Layer:
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    if "|" in text:
        return Layer(tuple(m.strip() for m in text.split("|") if m.strip()), True)
    if ":" in text:
        return Layer(tuple(m.strip() for m in text.split(":") if m.strip()), False)
    return Layer((text,))


def _contract(
    fields: Mapping[str, object], default_id: str, where: str
) -> ArchitectureContract | None:
    kind_name = fields.get("type")
    if not isinstance(kind_name, str):
        raise ContractFormatError(f"{where}: contract {default_id} has no type")
    try:
        kind = ContractKind(kind_name.strip())
    except ValueError:
        return None  # a contract type TER has no rule for
    raw_id = fields.get("id", default_id)
    contract_id = str(raw_id).strip() or default_id
    name = fields.get("name", contract_id)
    if not isinstance(name, str):
        raise ContractFormatError(f"{where}: contract {contract_id} name must be text")
    here = f"{where} contract {contract_id}"
    contract = ArchitectureContract(
        id=contract_id,
        name=name.strip(),
        kind=kind,
        source_modules=_modules(fields.get("source_modules"), here, "source_modules"),
        forbidden_modules=_modules(
            fields.get("forbidden_modules"), here, "forbidden_modules"
        ),
        layers=tuple(_layer(t) for t in _modules(fields.get("layers"), here, "layers")),
        containers=_modules(fields.get("containers"), here, "containers"),
        modules=_modules(fields.get("modules"), here, "modules"),
        ignore_imports=_modules(fields.get("ignore_imports"), here, "ignore_imports"),
        source=where,
    )
    missing = {
        ContractKind.FORBIDDEN: not (
            contract.source_modules and contract.forbidden_modules
        ),
        ContractKind.LAYERS: not contract.layers,
        ContractKind.INDEPENDENCE: not contract.modules,
    }[kind]
    if missing:
        raise ContractFormatError(
            f"{here}: a {kind.value} contract needs its module lists"
        )
    return contract


class ImportLinterContracts:
    """An :class:`~ter.ports.driven.ArchitectureContracts` for import-linter
    configuration."""

    name = "import-linter"

    def sources(self) -> tuple[str, ...]:
        return (".importlinter", "setup.cfg", "pyproject.toml")

    def read(self, path: str, text: str) -> tuple[ArchitectureContract, ...]:
        if path.endswith(".toml"):
            return self._toml(path, text)
        return self._ini(path, text)

    def _toml(self, path: str, text: str) -> tuple[ArchitectureContract, ...]:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ContractFormatError(f"{path}: {exc}") from exc
        tool = data.get("tool", {})
        section = tool.get("importlinter") if isinstance(tool, dict) else None
        if section is None:
            return ()
        if not isinstance(section, dict):
            raise ContractFormatError(f"{path}: [tool.importlinter] must be a table")
        tables = section.get("contracts", [])
        if not isinstance(tables, list) or not all(isinstance(t, dict) for t in tables):
            raise ContractFormatError(
                f"{path}: [[tool.importlinter.contracts]] must be tables"
            )
        found = (
            _contract(table, f"contract-{n}", path)
            for n, table in enumerate(tables, start=1)
        )
        return tuple(c for c in found if c is not None)

    def _ini(self, path: str, text: str) -> tuple[ArchitectureContract, ...]:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(text, source=path)
        except configparser.Error as exc:
            raise ContractFormatError(f"{path}: {exc}") from exc
        found = (
            _contract(dict(parser[name]), name[len(_INI_CONTRACT) :], path)
            for name in parser.sections()
            if name.startswith(_INI_CONTRACT)
        )
        return tuple(c for c in found if c is not None)
