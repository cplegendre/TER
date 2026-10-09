"""Contract suite for the ``ArchitectureContracts`` port.

The import-linter reader, the dependency-cruiser reader and the in-memory
fake are each given a source file that declares two contracts, one that
declares none and one that cannot be read, and must answer alike: the
obligations in the port's docstring, one test each.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from ter.adapters.driven.dependency_cruiser import DependencyCruiserContracts
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

CRUISER_PATH = ".dependency-cruiser.json"
CRUISER = (
    '{"forbidden": [\n'
    '  {"name": "no-ui-to-db", "severity": "error",\n'
    '   "from": {"path": "^src/ui/"}, "to": {"path": "^src/db/"}},\n'
    '  {"name": "no-legacy", "comment": "Nothing new uses legacy code",\n'
    '   "from": {"pathNot": "^src/legacy/"}, "to": {"path": "^src/legacy/"}}\n'
    "]}\n"
)
CRUISER_EXPECTED = (
    ArchitectureContract(
        id="no-ui-to-db",
        name="no-ui-to-db",
        kind=ContractKind.PATH_FORBIDDEN,
        source=CRUISER_PATH,
        from_paths=("^src/ui/",),
        to_paths=("^src/db/",),
    ),
    ArchitectureContract(
        id="no-legacy",
        name="Nothing new uses legacy code",
        kind=ContractKind.PATH_FORBIDDEN,
        source=CRUISER_PATH,
        from_paths_not=("^src/legacy/",),
        to_paths=("^src/legacy/",),
    ),
)


@dataclass(frozen=True)
class Case:
    """One reader and the texts it is held to."""

    make: Callable[[], ArchitectureContracts]
    path: str
    declared: str
    expected: tuple[ArchitectureContract, ...]
    none: str
    broken: str


CASES = {
    "import-linter": Case(
        ImportLinterContracts, PATH, DECLARED, EXPECTED, NONE, BROKEN
    ),
    "dependency-cruiser": Case(
        DependencyCruiserContracts,
        CRUISER_PATH,
        CRUISER,
        CRUISER_EXPECTED,
        '{"options": {}}',
        '{"forbidden": [',
    ),
    "in-memory": Case(
        lambda: InMemoryArchitectureContracts(
            {PATH: {DECLARED: EXPECTED, NONE: ()}}, broken=[BROKEN]
        ),
        PATH,
        DECLARED,
        EXPECTED,
        NONE,
        BROKEN,
    ),
}


@pytest.fixture(params=sorted(CASES))
def case(request: pytest.FixtureRequest) -> Case:
    return CASES[request.param]


@pytest.fixture
def reader(case: Case) -> ArchitectureContracts:
    return case.make()


@pytest.mark.req("TER-EVD-007")
@pytest.mark.req("TER-EVD-015")
def test_satisfies_the_port_protocol(reader: ArchitectureContracts) -> None:
    assert isinstance(reader, ArchitectureContracts) and reader.name


@pytest.mark.req("TER-EVD-007")
@pytest.mark.req("TER-EVD-015")
def test_names_the_files_that_may_declare_contracts(
    reader: ArchitectureContracts, case: Case
) -> None:
    assert case.path in reader.sources()


@pytest.mark.req("TER-EVD-007")
@pytest.mark.req("TER-EVD-015")
def test_reads_every_declared_contract_in_order(
    reader: ArchitectureContracts, case: Case
) -> None:
    assert reader.read(case.path, case.declared) == case.expected


@pytest.mark.req("TER-EVD-007")
@pytest.mark.req("TER-EVD-015")
def test_a_file_that_declares_none_returns_none(
    reader: ArchitectureContracts, case: Case
) -> None:
    assert reader.read(case.path, case.none) == ()


@pytest.mark.req("TER-EVD-007")
@pytest.mark.req("TER-EVD-015")
def test_an_unreadable_declaration_raises_naming_the_file(
    reader: ArchitectureContracts, case: Case
) -> None:
    with pytest.raises(ContractFormatError, match=case.path):
        reader.read(case.path, case.broken)


@pytest.mark.req("TER-EVD-007")
@pytest.mark.req("TER-EVD-015")
def test_the_same_text_yields_equal_contracts(
    reader: ArchitectureContracts, case: Case
) -> None:
    assert reader.read(case.path, case.declared) == reader.read(
        case.path, case.declared
    )


@pytest.mark.req("TER-EVD-007")
def test_the_import_linter_reader_is_a_capability() -> None:
    assert isinstance(make_contracts(), ImportLinterContracts)


@pytest.mark.req("TER-EVD-015")
def test_the_dependency_cruiser_reader_is_a_capability() -> None:
    assert isinstance(make_contracts("dependency-cruiser"), DependencyCruiserContracts)
