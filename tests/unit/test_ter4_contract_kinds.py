"""Architecture contracts TER-EVD-007 does not evaluate (TER-EVD-015).

import-linter's ``protected`` and ``acyclic_siblings`` contracts on Python
modules, and dependency-cruiser's ``forbidden`` path rules on TypeScript,
JavaScript, Svelte and Vue files: the rules (positive, negative and boundary
cases), the readers, and the ``boundary_violation`` detector over a
replayed session in a Python repository and in a TypeScript/Svelte monorepo,
and in one repository that declares both.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ter4_lean_builder import Script
from ter4_shop_repo import SHOP, at, shop_repo

from tests.contract.ecmascript_fixture import ES_REPO
from ter.adapters.driven.dependency_cruiser import DependencyCruiserContracts
from ter.adapters.driven.import_linter import ImportLinterContracts
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.application.ground import ground_session
from ter.bootstrap import make_contracts
from ter.bootstrap.capabilities import repository_evidence
from ter.domain.lean import LeanAnalysis, explain
from ter.domain.lean.model import Finding
from ter.domain.repository import (
    ArchitectureContract,
    ContractFormatError,
    ContractKind,
    contract_violations,
)

pytestmark = pytest.mark.req("TER-EVD-015")


def _protected(
    *protected: str, allowed: tuple[str, ...] = (), as_packages: bool = True
) -> ArchitectureContract:
    return ArchitectureContract(
        "P",
        "protected",
        ContractKind.PROTECTED,
        protected_modules=protected,
        allowed_importers=allowed,
        as_packages=as_packages,
    )


def _acyclic(
    *ancestors: str, depth: int = 10, skip: tuple[str, ...] = ()
) -> ArchitectureContract:
    return ArchitectureContract(
        "A",
        "acyclic",
        ContractKind.ACYCLIC_SIBLINGS,
        ancestors=ancestors,
        depth=depth,
        skip_descendants=skip,
    )


def _rule(
    frm: tuple[str, ...] = (),
    to: tuple[str, ...] = (),
    frm_not: tuple[str, ...] = (),
    to_not: tuple[str, ...] = (),
) -> ArchitectureContract:
    return ArchitectureContract(
        "R",
        "rule",
        ContractKind.PATH_FORBIDDEN,
        from_paths=frm,
        from_paths_not=frm_not,
        to_paths=to,
        to_paths_not=to_not,
    )


# --- the rules ------------------------------------------------------------------


class TestProtected:
    def test_only_allowed_importers_import_a_protected_module(self) -> None:
        c = _protected("app.db", allowed=("app.repo",))
        [v] = contract_violations("app.web.views", "app.db.session", [c])
        assert v.contract == "P" and "app.db is protected" in v.rule
        assert "app.repo" in v.rule
        assert contract_violations("app.repo.orders", "app.db.session", [c]) == ()

    def test_the_protected_package_may_import_itself(self) -> None:
        c = _protected("app.db")
        assert contract_violations("app.db.models", "app.db.session", [c]) == ()
        assert len(contract_violations("app.dbx", "app.db", [c])) == 1

    def test_as_packages_false_protects_only_the_named_modules(self) -> None:
        c = _protected("app.db", allowed=("app.repo",), as_packages=False)
        assert contract_violations("app.web", "app.db.session", [c]) == ()
        assert len(contract_violations("app.web", "app.db", [c])) == 1
        # Only app.repo itself is allowed, not the modules under it.
        assert len(contract_violations("app.repo.sub", "app.db", [c])) == 1

    def test_ignored_imports_are_exempt(self) -> None:
        c = ArchitectureContract(
            "P",
            "p",
            ContractKind.PROTECTED,
            protected_modules=("app.db",),
            ignore_imports=("app.cli -> app.db.**",),
        )
        assert contract_violations("app.cli", "app.db.session", [c]) == ()

    def test_forbidden_reads_as_packages_too(self) -> None:
        c = ArchitectureContract(
            "F",
            "f",
            ContractKind.FORBIDDEN,
            source_modules=("app.domain",),
            forbidden_modules=("app.io",),
            as_packages=False,
        )
        assert len(contract_violations("app.domain", "app.io", [c])) == 1
        assert contract_violations("app.domain.x", "app.io", [c]) == ()
        assert contract_violations("app.domain", "app.io.files", [c]) == ()


class TestAcyclicSiblings:
    GRAPH = {
        "app.b.x": frozenset({"app.c.y"}),
        "app.c.y": frozenset({"app.a.z", "os"}),
        "app.a.z": frozenset(),
    }

    def test_an_import_that_closes_a_sibling_cycle_is_a_violation(self) -> None:
        [v] = contract_violations(
            "app.a.z", "app.b.x", [_acyclic("app")], graph=self.GRAPH
        )
        assert "app.a -> app.b -> app.c -> app.a" in v.rule
        assert "children of app" in v.rule

    def test_an_import_with_no_way_back_is_not(self) -> None:
        assert (
            contract_violations(
                "app.b.x", "app.a.z", [_acyclic("app")], graph=self.GRAPH
            )
            == ()
        )

    def test_a_dependency_already_there_is_not_this_imports_doing(self) -> None:
        graph = {**self.GRAPH, "app.a.w": frozenset({"app.b.q"})}
        assert (
            contract_violations("app.a.z", "app.b.x", [_acyclic("app")], graph=graph)
            == ()
        )

    def test_the_deepest_shared_package_is_the_one_judged(self) -> None:
        graph = {"app.a.m2.k": frozenset({"app.a.m1.j"})}
        [v] = contract_violations(
            "app.a.m1.j", "app.a.m2.k", [_acyclic("app")], graph=graph
        )
        assert "children of app.a" in v.rule
        # A parent importing its own child links no siblings.
        assert (
            contract_violations("app.a", "app.a.m2.k", [_acyclic("app")], graph=graph)
            == ()
        )

    def test_depth_and_skipped_descendants_bound_the_contract(self) -> None:
        graph = {"app.a.m2.k": frozenset({"app.a.m1.j"})}
        # app.a is one level below the ancestor: inside depth 1, not depth 0.
        assert (
            len(
                contract_violations(
                    "app.a.m1.j", "app.a.m2.k", [_acyclic("app", depth=1)], graph=graph
                )
            )
            == 1
        )
        assert (
            contract_violations(
                "app.a.m1.j", "app.a.m2.k", [_acyclic("app", depth=0)], graph=graph
            )
            == ()
        )
        assert (
            contract_violations(
                "app.a.m1.j",
                "app.a.m2.k",
                [_acyclic("app", skip=("app.a",))],
                graph=graph,
            )
            == ()
        )

    def test_without_an_import_graph_it_is_not_judged(self) -> None:
        assert contract_violations("app.a.z", "app.b.x", [_acyclic("app")]) == ()

    def test_outside_every_ancestor_nothing_is_judged(self) -> None:
        graph = {"lib.b": frozenset({"lib.a"})}
        assert (
            contract_violations("lib.a", "lib.b", [_acyclic("app")], graph=graph) == ()
        )


class TestPathRules:
    def test_a_file_matching_from_must_not_import_one_matching_to(self) -> None:
        c = _rule(frm=("^src/ui/",), to=("^src/db/",))
        [v] = contract_violations(
            "src/ui/List.svelte", "src/db/client.ts", [c], by_path=True
        )
        assert v.rule == "src/ui/List.svelte must not import src/db/client.ts"
        assert (
            contract_violations("src/api/a.ts", "src/db/client.ts", [c], by_path=True)
            == ()
        )
        assert (
            contract_violations("src/ui/a.ts", "src/ui/b.ts", [c], by_path=True) == ()
        )

    def test_path_not_exempts_files_on_either_side(self) -> None:
        c = _rule(
            frm=("^src/",),
            to=("^src/db/",),
            frm_not=(r"\.spec\.ts$",),
            to_not=("types",),
        )
        assert (
            len(contract_violations("src/a.ts", "src/db/x.ts", [c], by_path=True)) == 1
        )
        assert (
            contract_violations("src/a.spec.ts", "src/db/x.ts", [c], by_path=True) == ()
        )
        assert (
            contract_violations("src/a.ts", "src/db/types.ts", [c], by_path=True) == ()
        )

    def test_group_matching_names_the_same_feature(self) -> None:
        # No feature imports another feature.
        c = _rule(
            frm=("^src/features/([^/]+)/",),
            to=("^src/features/",),
            to_not=("^src/features/$1/",),
        )
        mine, theirs = "src/features/cart/a.ts", "src/features/user/b.ts"
        assert len(contract_violations(mine, theirs, [c], by_path=True)) == 1
        assert (
            contract_violations(mine, "src/features/cart/c.ts", [c], by_path=True) == ()
        )

    def test_a_side_without_conditions_matches_every_file(self) -> None:
        c = _rule(to=("^src/legacy/",))
        assert (
            len(contract_violations("any/x.ts", "src/legacy/y.ts", [c], by_path=True))
            == 1
        )

    def test_path_and_module_contracts_never_judge_each_other(self) -> None:
        path = _rule(frm=("app",), to=("db",))
        module = _protected("app.db")
        # A Python import is judged by module contracts only, and a
        # TypeScript import by path rules only.
        assert [
            v.contract for v in contract_violations("app.web", "app.db", [path, module])
        ] == ["P"]
        assert [
            v.contract
            for v in contract_violations(
                "app/web.ts", "app/db.ts", [path, module], by_path=True
            )
        ] == ["R"]


# --- the readers ------------------------------------------------------------------


class TestImportLinterReader:
    def test_reads_protected_and_acyclic_contracts_from_toml(self) -> None:
        text = (
            "[[tool.importlinter.contracts]]\n"
            'id = "db"\nname = "Only the repository touches the database"\n'
            'type = "protected"\nprotected_modules = ["app.db"]\n'
            'allowed_importers = ["app.repo"]\nas_packages = false\n'
            "\n"
            "[[tool.importlinter.contracts]]\n"
            'id = "dag"\nname = "No cycles"\ntype = "acyclic_siblings"\n'
            'ancestors = ["app"]\ndepth = 2\nskip_descendants = ["app.legacy"]\n'
        )
        protected, acyclic = ImportLinterContracts().read("pyproject.toml", text)
        assert protected == ArchitectureContract(
            "db",
            "Only the repository touches the database",
            ContractKind.PROTECTED,
            source="pyproject.toml",
            protected_modules=("app.db",),
            allowed_importers=("app.repo",),
            as_packages=False,
        )
        assert acyclic.kind is ContractKind.ACYCLIC_SIBLINGS
        assert (acyclic.ancestors, acyclic.depth, acyclic.skip_descendants) == (
            ("app",),
            2,
            ("app.legacy",),
        )

    def test_reads_them_from_ini_with_defaults(self) -> None:
        text = (
            "[importlinter:contract:dag]\nname = No cycles\n"
            "type = acyclic_siblings\nancestors =\n    app\n    lib\n\n"
            "[importlinter:contract:db]\nname = DB\ntype = protected\n"
            "protected_modules = app.db\nas_packages = True\n"
        )
        dag, db = ImportLinterContracts().read(".importlinter", text)
        assert dag.ancestors == ("app", "lib") and dag.depth == 10
        assert db.protected_modules == ("app.db",) and db.as_packages
        assert db.allowed_importers == ()

    @pytest.mark.parametrize(
        "body",
        [
            'type = "protected"\n',
            'type = "acyclic_siblings"\n',
            'type = "acyclic_siblings"\nancestors = ["app"]\ndepth = -1\n',
            'type = "protected"\nprotected_modules = ["a"]\nas_packages = "maybe"\n',
        ],
    )
    def test_an_incomplete_contract_is_unreadable(self, body: str) -> None:
        text = f"[[tool.importlinter.contracts]]\nname = 'x'\n{body}"
        with pytest.raises(ContractFormatError, match="pyproject.toml"):
            ImportLinterContracts().read("pyproject.toml", text)

    def test_a_path_rule_type_is_not_an_import_linter_type(self) -> None:
        text = "[[tool.importlinter.contracts]]\nname = 'x'\ntype = 'path_forbidden'\n"
        assert ImportLinterContracts().read("pyproject.toml", text) == ()


CRUISER = {
    "forbidden": [
        {
            "name": "no-ui-to-db",
            "comment": "The UI reaches the database through the API",
            "severity": "error",
            "from": {"path": "^apps/web/src/routes/"},
            "to": {"path": ["^packages/model/src/lab", "^packages/db/"]},
        },
        {
            "name": "no-circular",
            "severity": "warn",
            "from": {},
            "to": {"circular": True},
        },
        {"name": "silenced", "severity": "off", "from": {}, "to": {"path": "x"}},
        {
            "name": "not-to-dev-dep",
            "from": {"path": "^src"},
            "to": {"dependencyTypes": ["npm-dev"]},
        },
        {"from": {"pathNot": "\\.spec\\.ts$"}, "to": {"path": "^legacy/"}},
    ],
    "options": {"doNotFollow": {"path": "node_modules"}},
}


class TestDependencyCruiserReader:
    def test_reads_path_rules_and_skips_what_it_cannot_judge(self) -> None:
        reader = DependencyCruiserContracts()
        assert reader.sources() == (".dependency-cruiser.json",)
        ui, unnamed = reader.read(".dependency-cruiser.json", json.dumps(CRUISER))
        assert ui == ArchitectureContract(
            "no-ui-to-db",
            "The UI reaches the database through the API",
            ContractKind.PATH_FORBIDDEN,
            source=".dependency-cruiser.json",
            from_paths=("^apps/web/src/routes/",),
            to_paths=("^packages/model/src/lab", "^packages/db/"),
        )
        assert unnamed.id == "rule-5" and unnamed.name == "rule-5"
        assert unnamed.from_paths_not == ("\\.spec\\.ts$",)

    def test_a_configuration_without_rules_declares_none(self) -> None:
        reader = DependencyCruiserContracts()
        assert reader.read(".dependency-cruiser.json", '{"options": {}}') == ()

    @pytest.mark.parametrize(
        "text",
        [
            "{ not json",
            "[]",
            '{"forbidden": {}}',
            '{"forbidden": [{"from": {"path": "(unclosed"}, "to": {}}]}',
            '{"forbidden": [{"from": {"path": 3}, "to": {}}]}',
            '{"forbidden": [{"from": [], "to": {}}]}',
            '{"forbidden": ["rule"]}',
        ],
    )
    def test_an_unreadable_configuration_raises(self, text: str) -> None:
        with pytest.raises(ContractFormatError, match=r"\.dependency-cruiser\.json"):
            DependencyCruiserContracts().read(".dependency-cruiser.json", text)

    def test_the_reader_is_a_capability(self) -> None:
        assert isinstance(
            make_contracts("dependency-cruiser"), DependencyCruiserContracts
        )


# --- the detector over replayed sessions -------------------------------------------

MODEL = "src/app/domain/model.py"
CHECKOUT = "src/app/service/checkout.py"
WEB = "src/app/adapters/web.py"

_EXTRA_CONTRACTS = (
    "\n[[tool.importlinter.contracts]]\n"
    'id = "checkout-api"\n'
    'name = "Only the web adapter calls checkout"\n'
    'type = "protected"\n'
    'protected_modules = ["app.service.checkout"]\n'
    'allowed_importers = ["app.adapters.web"]\n'
    "\n[[tool.importlinter.contracts]]\n"
    'id = "no-cycles"\n'
    'name = "The app packages form no cycle"\n'
    'type = "acyclic_siblings"\n'
    'ancestors = ["app"]\n'
)


def _found(analysis: LeanAnalysis) -> list[Finding]:
    return [f for f in analysis.findings if f.detector == "boundary_violation"]


def _analyse(script: Script, root: Path) -> LeanAnalysis:
    g = ground_session(
        script.events,
        repository_evidence(root, "syntax"),
        (ImportLinterContracts(), DependencyCruiserContracts()),
    )
    return explain(script.events, RegexTokenizer(), repository=g)


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    files = dict(SHOP)
    files["pyproject.toml"] = SHOP["pyproject.toml"] + _EXTRA_CONTRACTS
    # Reports, a sibling of the other packages, uses the domain at the start.
    files["src/app/reports/summary.py"] = (
        "from app.domain.model import order_total\n\n\n"
        "def monthly_summary(rows):\n    return order_total(rows)\n"
    )
    return shop_repo(tmp_path / "shop", files)


class TestPythonContractsInASession:
    def test_importing_a_protected_module_is_a_violation(self, shop: Path) -> None:
        s = Script()
        s.prompt("Show the checkout total in summary.py")
        s.edit(
            at("src/app/reports/summary.py"),
            "def monthly_summary",
            "from app.service.checkout import checkout\n\n\ndef monthly_summary",
        )
        [f] = _found(_analyse(s, shop))
        assert f.subject == "checkout-api" and f.confidence == 0.9
        assert "app.service.checkout is protected" in f.explanation

    def test_the_allowed_importer_is_not_a_violation(self, shop: Path) -> None:
        s = Script()
        s.prompt("Use checkout in web.py")
        s.edit(at(WEB), "def handle", "import app.service.checkout\n\n\ndef handle")
        assert _found(_analyse(s, shop)) == []

    def test_an_import_closing_a_sibling_cycle_is_a_violation(self, shop: Path) -> None:
        s = Script()
        s.prompt("Let the Order in model.py summarise itself")
        s.edit(
            at(MODEL),
            "class Order:",
            "from app.reports.summary import monthly_summary\n\n\nclass Order:",
        )
        subjects = sorted(f.subject for f in _found(_analyse(s, shop)))
        assert subjects == ["no-cycles"]
        [f] = _found(_analyse(s, shop))
        assert "app.domain -> app.reports -> app.domain" in f.explanation

    def test_a_cycle_made_by_two_edits_of_the_session_is_found(
        self, tmp_path: Path
    ) -> None:
        files = dict(SHOP)
        files["pyproject.toml"] = SHOP["pyproject.toml"] + _EXTRA_CONTRACTS
        root = shop_repo(tmp_path / "shop", files)
        s = Script()
        s.prompt("Connect reports and the domain")
        s.edit(
            at("src/app/reports/summary.py"),
            "def monthly_summary",
            "from app.domain.model import order_total\n\n\ndef monthly_summary",
        )
        s.edit(
            at(MODEL),
            "class Order:",
            "from app.reports.summary import monthly_summary\n\n\nclass Order:",
        )
        found = _found(_analyse(s, root))
        # The first edit adds a dependency with no way back; the second
        # closes the cycle over the first one.
        assert [f.subject for f in found] == ["no-cycles"]
        assert "app.domain -> app.reports -> app.domain" in found[0].explanation


WEB_PAGE = "apps/web/src/routes/+page.ts"


@pytest.fixture
def mono(tmp_path: Path) -> Path:
    files = dict(ES_REPO)
    files[".dependency-cruiser.json"] = json.dumps(CRUISER, indent=2)
    return shop_repo(tmp_path / "mono", files)


class TestPathRulesInASession:
    def test_a_forbidden_typescript_import_is_a_violation(self, mono: Path) -> None:
        s = Script()
        s.prompt("Load a lab on the home page in +page.ts")
        edit, _ = s.edit(
            at(WEB_PAGE),
            "import { loadCourse } from '$lib';\n",
            "import { loadCourse } from '$lib';\n"
            "import { loadLab } from '../../../../packages/model/src/lab';\n",
        )
        [f] = _found(_analyse(s, mono))
        assert f.subject == "no-ui-to-db" and f.confidence == 0.9
        assert f"{WEB_PAGE} -> packages/model/src/lab.ts" in f.title
        assert "line 2" in f.explanation and edit.id in f.evidence

    def test_an_allowed_typescript_import_is_not(self, mono: Path) -> None:
        s = Script()
        s.prompt("Format titles on the home page in +page.ts")
        s.edit(
            at(WEB_PAGE),
            "import { loadCourse } from '$lib';\n",
            "import { loadCourse } from '$lib';\n"
            "import { formatTitle } from '@shared/util';\n",
        )
        assert _found(_analyse(s, mono)) == []

    def test_a_python_and_a_typescript_package_in_one_repository(
        self, tmp_path: Path
    ) -> None:
        files = {**ES_REPO, **{f"py/{p}": t for p, t in SHOP.items()}}
        files[".dependency-cruiser.json"] = json.dumps(CRUISER)
        files["pyproject.toml"] = SHOP["pyproject.toml"]
        root = shop_repo(tmp_path / "both", files)
        g = ground_session(
            Script().events,
            repository_evidence(root, "syntax"),
            (ImportLinterContracts(), DependencyCruiserContracts()),
        )
        assert g.contract_source == "pyproject.toml, .dependency-cruiser.json"
        assert {c.kind for c in g.contracts} == {
            ContractKind.LAYERS,
            ContractKind.FORBIDDEN,
            ContractKind.PATH_FORBIDDEN,
        }

    def test_one_unreadable_source_leaves_the_others_standing(
        self, tmp_path: Path
    ) -> None:
        files = dict(ES_REPO)
        files[".dependency-cruiser.json"] = "{ broken"
        files["pyproject.toml"] = SHOP["pyproject.toml"]
        root = shop_repo(tmp_path / "half", files)
        g = ground_session(
            Script().events,
            repository_evidence(root, "syntax"),
            (ImportLinterContracts(), DependencyCruiserContracts()),
        )
        assert {c.id for c in g.contracts} == {"layers", "pure-domain"}
        assert g.contract_problem is not None
        assert ".dependency-cruiser.json" in g.contract_problem
