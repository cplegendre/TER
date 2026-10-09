"""Contract suite for the ``ArchitectureContracts`` port.

The import-linter reader and the in-memory fake are each given a source file
that declares two contracts, one that declares none and one that cannot be
read, and must answer alike: the obligations in the port's docstring, one
test each.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ter.adapters.driven.import_linter import ImportLinterContracts
from ter.adapters.driven.in_memory import InMemoryArchitectureContracts
from ter.bootstrap import make_contracts
from ter.domain.repository import (
    ArchitectureContract,
    ContractFormatError,
    ContractKind,
    Layer,
)
from ter.ports import ArchitectureContracts

DECLARED = (
    "[tool.importlinter]\n"
    'root_packages = ["app"]\n'
    "\n"
    "[[tool.importlinter.contracts]]\n"
    'id = "layers"\n'
    'name = "Layers"\n'
    'type = "layers"\n'
    'layers = ["app.ui", "app.core"]\n'
    "\n"
    "[[tool.importlinter.contracts]]\n"
    'name = "Pure core"\n'
    'type = "forbidden"\n'
    'source_modules = ["app.core"]\n'
    'forbidden_modules = ["requests"]\n'
    'ignore_imports = ["app.core.io -> requests"]\n'
)
NONE = '[project]\nname = "app"\n'
BROKEN = "[tool.importlinter\n"
PATH = "pyproject.toml"

EXPECTED = (
    ArchitectureContract(
        id="layers",
        name="Layers",
        kind=ContractKind.LAYERS,
        layers=(Layer(("app.ui",)), Layer(("app.core",))),
        source=PATH,
    ),
    ArchitectureContract(
        id="contract-2",
        name="Pure core",
        kind=ContractKind.FORBIDDEN,
        source_modules=("app.core",),
        forbidden_modules=("requests",),
        ignore_imports=("app.core.io -> requests",),
        source=PATH,
    ),
)


def _import_linter() -> ArchitectureContracts:
    return ImportLinterContracts()


def _memory() -> ArchitectureContracts:
    return InMemoryArchitectureContracts(
        {PATH: {DECLARED: EXPECTED, NONE: ()}}, broken=[BROKEN]
    )


@pytest.fixture(params=[_import_linter, _memory], ids=["import-linter", "in-memory"])
def reader(request: pytest.FixtureRequest) -> ArchitectureContracts:
    factory: Callable[[], ArchitectureContracts] = request.param
    return factory()


@pytest.mark.req("TER-EVD-007")
def test_satisfies_the_port_protocol(reader: ArchitectureContracts) -> None:
    assert isinstance(reader, ArchitectureContracts) and reader.name


@pytest.mark.req("TER-EVD-007")
def test_names_the_files_that_may_declare_contracts(
    reader: ArchitectureContracts,
) -> None:
    assert PATH in reader.sources()


@pytest.mark.req("TER-EVD-007")
def test_reads_every_declared_contract_in_order(reader: ArchitectureContracts) -> None:
    assert reader.read(PATH, DECLARED) == EXPECTED


@pytest.mark.req("TER-EVD-007")
def test_a_file_that_declares_none_returns_none(reader: ArchitectureContracts) -> None:
    assert reader.read(PATH, NONE) == ()


@pytest.mark.req("TER-EVD-007")
def test_an_unreadable_declaration_raises_naming_the_file(
    reader: ArchitectureContracts,
) -> None:
    with pytest.raises(ContractFormatError, match=PATH):
        reader.read(PATH, BROKEN)


@pytest.mark.req("TER-EVD-007")
def test_the_same_text_yields_equal_contracts(reader: ArchitectureContracts) -> None:
    assert reader.read(PATH, DECLARED) == reader.read(PATH, DECLARED)


@pytest.mark.req("TER-EVD-007")
def test_the_import_linter_reader_is_a_capability() -> None:
    assert isinstance(make_contracts(), ImportLinterContracts)
