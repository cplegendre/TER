"""ECMAScript import evidence (L3): TypeScript, JavaScript, Svelte and Vue
files under the ``syntax`` engine.

What is read (imports in every form, definitions, ``<script>`` blocks) and
what is not (comments, strings, computed specifiers); how specifiers resolve
to repository files (relative paths with probing and index files,
``tsconfig`` paths and ``baseUrl``, SvelteKit's ``$lib``, workspace
packages, external packages); test modules and test-to-source links
(TER-EVD-014); and the change surface over a TypeScript/Svelte monorepo,
where edits now have import evidence (TER-EVD-006). Positive, negative and
boundary cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ter4_lean_builder import Script
from ter4_shop_repo import at, shop_repo

from tests.contract.ecmascript_fixture import ES_LINKS, ES_REPO, ES_TESTS_OF
from tests.contract.repository_fixture import write_repo
from ter.adapters.driven.repository import SourceSyntaxEvidence
from ter.adapters.driven.repository.ecmascript import (
    EcmaScriptProject,
    ecmascript_structure,
    probe,
    script_code,
    strip_json_comments,
    tokenize,
)
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.application.ground import ground_session
from ter.bootstrap.capabilities import repository_evidence
from ter.domain.lean import LeanAnalysis, explain
from ter.domain.lean.surface import EditPlacement
from ter.domain.repository import (
    ImportEdge,
    SymbolKind,
    UnsupportedLanguageError,
    is_test_module,
    is_vendored,
    resolve_import,
    source_language,
)

ME = "src/m.ts"


def imports(code: str, path: str = ME) -> list[tuple[str, tuple[str, ...], int]]:
    s = ecmascript_structure(path, code, lambda p, spec: ())
    return [(e.module, e.names, e.line) for e in s.imports]


def specifiers(code: str, path: str = ME) -> list[str]:
    return [spec for spec, _, _ in imports(code, path)]


def project(files: dict[str, str]) -> EcmaScriptProject:
    return EcmaScriptProject.read(sorted(files), files.get)


@pytest.fixture
def mono(tmp_path: Path) -> SourceSyntaxEvidence:
    return SourceSyntaxEvidence(write_repo(tmp_path, ES_REPO))


# --- reading imports ------------------------------------------------------------


@pytest.mark.req("TER-EVD-012")
class TestImportForms:
    def test_every_static_form_is_an_import_with_its_names(self) -> None:
        code = (
            "import def, { a as b, type T } from './a';\n"
            "import * as ns from './ns';\n"
            "import 'side-effect';\n"
            "export { c, d as e } from './c';\n"
            "export * from './star';\n"
            "export * as all from './all';\n"
        )
        assert imports(code) == [
            ("./a", ("default", "a", "T"), 1),
            ("./ns", ("*",), 2),
            ("side-effect", (), 3),
            ("./c", ("c", "d"), 4),
            ("./star", ("*",), 5),
            ("./all", ("*",), 6),
        ]

    def test_type_only_imports_and_exports_are_dependencies(self) -> None:
        code = (
            "import type { Lab } from './lab';\n"
            "import type Course from './course';\n"
            "export type { Unit } from './unit';\n"
        )
        assert imports(code) == [
            ("./lab", ("Lab",), 1),
            ("./course", ("default",), 2),
            ("./unit", ("Unit",), 3),
        ]

    def test_require_and_dynamic_import_with_a_literal_specifier(self) -> None:
        code = (
            "const a = require('./a');\n"
            'const b = await import("./b");\n'
            "const c = import(`./c`);\n"
            "const d = () => import('./d', { with: { type: 'json' } });\n"
        )
        assert specifiers(code) == ["./a", "./b", "./c", "./d"]

    def test_a_multi_line_import_is_one_import_on_its_first_line(self) -> None:
        code = "\n\nimport {\n  a,\n  b, // the b\n} from './x'\nconst y = 1\n"
        assert imports(code) == [("./x", ("a", "b"), 3)]

    def test_code_inside_a_template_substitution_is_read(self) -> None:
        code = "const s = `before ${await import('./inner')} after`;\n"
        assert specifiers(code) == ["./inner"]


@pytest.mark.req("TER-EVD-012")
class TestWhatIsNotAnImport:
    def test_comments_and_strings_that_mention_import_are_not_imports(self) -> None:
        code = (
            "// import a from './line-comment';\n"
            "/* import b from './block-comment';\n"
            "   require('./still-comment') */\n"
            "const s = \"import c from './in-a-string'\";\n"
            "const t = 'require(\"./in-a-string\")';\n"
            "const u = `import d from './in-a-template'`;\n"
        )
        assert specifiers(code) == []

    def test_a_regular_expression_literal_hides_nothing_after_it(self) -> None:
        code = (
            "const quote = /['\"]import/g;\n"
            "const half = total / 2 / count;\n"
            "import real from './after-regex';\n"
        )
        assert specifiers(code) == ["./after-regex"]

    def test_member_calls_keys_and_computed_specifiers_are_not_imports(
        self,
    ) -> None:
        code = (
            "loader.import('./member');\n"
            "context.require('./member');\n"
            "const o = { import: 1, require: 2 };\n"
            "const m = import.meta.url;\n"
            "const n = import(name);\n"
            "const p = import(`./pages/${name}`);\n"
            "export { local };\n"
            "const later = from('./not-a-clause');\n"
        )
        assert specifiers(code) == []

    def test_an_unterminated_string_stops_at_its_line(self) -> None:
        # JSX text with an apostrophe reads as a string to the end of the line.
        code = "const x = <p>Don't</p>;\nimport y from './y';\n"
        assert specifiers(code, "src/m.tsx") == ["./y"]


@pytest.mark.req("TER-EVD-012")
class TestComponents:
    def test_only_script_blocks_are_read_and_lines_are_the_files(self) -> None:
        svelte = (
            "<script context=\"module\" lang='ts'>\n"
            "  export const prerender = true;\n"
            "</script>\n"
            "<!-- <script>import hidden from './hidden'</script> -->\n"
            '<script lang="ts">\n'
            "  import Card from './Card.svelte';\n"
            "</script>\n"
            "<p>import fake from './markup'</p>\n"
            "{#await import('./in-markup')}{/await}\n"
        )
        assert imports(svelte, "src/routes/Home.svelte") == [
            ("./Card.svelte", ("default",), 6)
        ]
        assert script_code("src/a.ts", "x") == "x"

    def test_a_vue_component_is_read_the_same_way(self) -> None:
        vue = "<template><div/></template>\n<script setup>\nimport A from './A.vue'\n</script>\n"
        assert imports(vue, "src/App.vue") == [("./A.vue", ("default",), 3)]

    def test_a_component_is_a_symbol_named_by_its_file(self) -> None:
        s = ecmascript_structure(
            "src/CourseCard.svelte",
            "<script>\n  function open() {}\n</script>\n<p/>\n",
            lambda p, spec: (),
        )
        assert [(x.name, x.kind, x.line, x.end_line) for x in s.symbols] == [
            ("CourseCard", SymbolKind.CLASS, 1, 4),
            ("open", SymbolKind.FUNCTION, 2, 2),
        ]
        route = ecmascript_structure("src/+page.svelte", "<p/>\n", lambda p, s: ())
        assert route.symbols == ()


@pytest.mark.req("TER-EVD-012")
class TestDefinitions:
    def test_functions_classes_methods_and_arrow_constants(self) -> None:
        code = (
            "export function topLevel(x: number): number {\n"  # 1
            "  function inner() { return 1 }\n"  # 2
            "  return x;\n"  # 3
            "}\n"  # 4
            "export class Calc extends Base<T> {\n"  # 5
            "  constructor() { super(); }\n"  # 6
            "  total(xs: number[]): number { return xs.length }\n"  # 7
            "  static make() { return new Calc() }\n"  # 8
            "  label: string = 'x';\n"  # 9
            "}\n"  # 10
            "export const load = async (a: string): Promise<void> => {\n"  # 11
            "  await a;\n"  # 12
            "};\n"  # 13
            "const short = (x) => x + 1\n"  # 14
            "const typed: Handler = function () {}\n"  # 15
            "let notAFunction = 3;\n"  # 16
            "function overload(a: string): void;\n"  # 17
        )
        s = ecmascript_structure(ME, code, lambda p, spec: ())
        assert [(x.name, x.kind.value, x.line, x.end_line) for x in s.symbols] == [
            ("topLevel", "function", 1, 4),
            ("topLevel.inner", "function", 2, 2),
            ("Calc", "class", 5, 10),
            ("Calc.constructor", "method", 6, 6),
            ("Calc.total", "method", 7, 7),
            ("Calc.make", "method", 8, 8),
            ("load", "function", 11, 13),
            ("short", "function", 14, 14),
            ("typed", "function", 15, 15),
        ]
        assert s.language == "ecmascript" and s.module == ME


# --- call edges (TER-EVD-016) ------------------------------------------------------


def calls(
    code: str, path: str = ME, files: tuple[str, ...] = ()
) -> list[tuple[str, str, int, str | None]]:
    def resolve(importer: str, spec: str) -> tuple[str, ...]:
        if spec.startswith("."):
            return ("src/" + spec.lstrip("./") + ".ts",)
        return ()

    s = ecmascript_structure(path, code, resolve, files)
    return [(c.caller, c.callee, c.line, c.resolved) for c in s.calls]


@pytest.mark.req("TER-EVD-016")
class TestCallEdges:
    def test_calls_name_their_caller_and_resolve_through_imports(self) -> None:
        code = (
            "import def, { a as b, c } from './util';\n"  # 1
            "import * as ns from 'lib';\n"  # 2
            "const req = require('./req');\n"  # 3
            "const { x, y: z } = require('pkg');\n"  # 4
            "export function top() {\n"  # 5
            "  b(1); ns.deep.f(); def();\n"  # 6
            "  req.run(); z(); x.go();\n"  # 7
            "  helper();\n"  # 8
            "}\n"  # 9
            "function helper() { return local() }\n"  # 10
            "c();\n"  # 11
        )
        files = ("src/util.ts",)
        assert calls(code, files=files) == [
            ("top", "b", 6, "src/util.ts#a"),
            ("top", "ns.deep.f", 6, "lib#deep.f"),
            ("top", "def", 6, "src/util.ts#default"),
            ("top", "req.run", 7, "./req#run"),
            ("top", "z", 7, "pkg#y"),
            ("top", "x.go", 7, "pkg#x.go"),
            ("top", "helper", 8, "src/m.ts#helper"),
            ("helper", "local", 10, None),
            ("<module>", "c", 11, "src/util.ts#c"),
        ]

    def test_methods_nested_functions_and_arrow_constants_are_callers(self) -> None:
        code = (
            "class Calc extends Base {\n"  # 1
            "  constructor() { super(); this.reset(); }\n"  # 2
            "  total(xs: number[]): number { return sum(xs) }\n"  # 3
            "  static make() { return new Calc() }\n"  # 4
            "  label = compute();\n"  # 5
            "}\n"  # 6
            "function outer() {\n"  # 7
            "  function inner() { deep() }\n"  # 8
            "  items.map((i) => wrap(i));\n"  # 9
            "}\n"  # 10
            "export const load = async () => fetchAll();\n"  # 11
        )
        assert calls(code) == [
            ("Calc.constructor", "super", 2, None),
            ("Calc.constructor", "this.reset", 2, None),
            ("Calc.total", "sum", 3, None),
            ("Calc.make", "Calc", 4, "src/m.ts#Calc"),
            ("Calc", "compute", 5, None),
            ("outer.inner", "deep", 8, None),
            ("outer", "items.map", 9, None),
            ("outer", "wrap", 9, None),
            ("load", "fetchAll", 11, None),
        ]

    def test_generic_optional_and_nested_calls(self) -> None:
        code = (
            "const store = writable<Course[]>([]);\n"  # 1
            "maybe?.(1); obj?.method(2);\n"  # 2
            "outer(inner(3));\n"  # 4
            "@Component({ selector: 'x' })\n"  # 4
            "class C {}\n"
        )
        assert [(c, ln) for _, c, ln, _ in calls(code)] == [
            ("writable", 1),
            ("maybe", 2),
            ("obj.method", 2),
            ("outer", 3),
            ("inner", 3),
            ("Component", 4),
        ]

    def test_definitions_keywords_and_computed_receivers_are_not_calls(
        self,
    ) -> None:
        code = (
            "if (ready) { go() }\n"  # 1
            "for (const x of xs) {}\n"  # 2
            "while (wait(1)) {}\n"  # 3
            "const f = function named(a) { return (a) };\n"  # 4
            "const o = { save() { return 1 }, load(): void {} };\n"  # 5
            "interface Api { fetch(id: string): Promise<void>; }\n"  # 6
            "make().then(done);\n"  # 7
            "list[0].push(1); value!.trim();\n"  # 8
            "const t = typeof (x); import('./lazy'); require('./x');\n"  # 9
            "a < b && c > (d);\n"  # 10
            "// call(1)\n"  # 11
            "const s = 'call(2)' + `${tpl(3)}`;\n"  # 12
            "if (x) run();\n"  # 13
            "!check();\n"  # 14
        )
        assert [(c, ln) for _, c, ln, _ in calls(code)] == [
            ("go", 1),
            ("wait", 3),
            ("make", 7),
            ("tpl", 12),
            ("run", 13),
            ("check", 14),
        ]

    def test_a_definition_shadows_an_import_and_the_first_import_wins(
        self,
    ) -> None:
        code = (
            "import { f } from 'one';\n"
            "import { f as g, g as h } from 'two';\n"
            "import { g } from 'three';\n"
            "function h() {}\n"
            "f(); g(); h();\n"
        )
        assert [r for _, _, _, r in calls(code)] == [
            "one#f",
            "two#f",
            "src/m.ts#h",
        ]

    def test_an_unresolved_import_resolves_to_its_specifier(self) -> None:
        # ``./gone`` loads no file of the repository: the call keeps the
        # specifier as written, never a guessed file.
        code = "import { x } from './gone';\nx();\n"
        assert calls(code, files=("src/other.ts",)) == [
            ("<module>", "x", 2, "./gone#x")
        ]

    def test_svelte_markup_calls_are_not_read(self) -> None:
        code = (
            "<script>\n"
            "  import { save } from './api';\n"
            "  function click() { save(1) }\n"
            "</script>\n"
            "<button on:click={() => save(2)}>{format(3)}</button>\n"
        )
        assert calls(code, "src/C.svelte", ("src/api.ts",)) == [
            ("click", "save", 3, "src/api.ts#save")
        ]

    def test_the_monorepo_calls_resolve_to_the_files_their_imports_load(
        self, mono: SourceSyntaxEvidence
    ) -> None:
        api = mono.structure("apps/web/src/lib/api.ts")
        assert api is not None
        assert [(c.caller, c.callee, c.resolved) for c in api.calls] == [
            ("<module>", "writable", "svelte/store#writable"),
            ("loadCourse", "Course", "packages/model/src/index.ts#Course"),
            ("loadCourse", "formatTitle", "packages/shared/src/util.ts#formatTitle"),
        ]


# --- resolving specifiers -------------------------------------------------------


@pytest.mark.req("TER-EVD-012")
class TestResolution:
    def test_a_relative_specifier_probes_suffixes_then_index_files(self) -> None:
        p = project({})
        found = p.candidates("src/a/b.ts", "../lib/util")
        assert found[:3] == ("src/lib/util", "src/lib/util.ts", "src/lib/util.tsx")
        assert "src/lib/util.svelte" in found
        assert found.index("src/lib/util.js") < found.index("src/lib/util/index.ts")
        files = {"src/lib/util/index.ts"}
        assert resolve_import(ImportEdge("../lib/util", (), 1, found), files) == (
            "src/lib/util/index.ts"
        )

    def test_a_js_specifier_finds_the_typescript_it_compiles_from(self) -> None:
        assert probe("src/lab.js")[:3] == ("src/lab.js", "src/lab.ts", "src/lab.tsx")
        assert probe("src/Card.svelte")[0] == "src/Card.svelte"

    def test_a_path_above_the_root_and_external_packages_name_no_file(
        self,
    ) -> None:
        p = project({})
        assert p.candidates("a.ts", "../outside") == ()
        for spec in ("react", "@scope/pkg", "node:fs", "/abs/path", "https://x/y.js"):
            assert p.candidates("src/a.ts", spec) == ()

    def test_a_query_or_fragment_is_not_part_of_the_path(self) -> None:
        assert project({}).candidates("src/a.ts", "./logo.svg?raw")[0] == (
            "src/logo.svg"
        )

    def test_lib_is_src_lib_of_the_nearest_sveltekit_project(self) -> None:
        files = {
            "apps/web/svelte.config.js": "export default {}\n",
            "apps/docs/package.json": '{"devDependencies": {"@sveltejs/kit": "2"}}',
        }
        p = project(files)
        assert p.candidates("apps/web/src/routes/+page.ts", "$lib/api")[1] == (
            "apps/web/src/lib/api.ts"
        )
        assert p.candidates("apps/web/src/x.ts", "$lib")[0] == "apps/web/src/lib"
        assert "apps/web/src/lib/index.ts" in p.candidates("apps/web/x.ts", "$lib")
        assert p.candidates("apps/docs/src/a.ts", "$lib/b")[1] == (
            "apps/docs/src/lib/b.ts"
        )
        # Outside any SvelteKit project, $lib is an unknown package.
        assert p.candidates("tools/a.ts", "$lib/b") == ()

    def test_tsconfig_paths_with_comments_extends_and_base_url(self) -> None:
        files = {
            "tsconfig.base.json": (
                "{ // base\n"
                '  "compilerOptions": { "baseUrl": "./src",\n'
                '    "paths": { "@/*": ["app/*", "fallback/*"], "~exact": ["one.ts"], },\n'
                "  },\n}\n"
            ),
            "pkg/tsconfig.json": '{ "extends": "../tsconfig.base.json" }',
            "other/jsconfig.json": (
                '{ "compilerOptions": { "paths": { "#u/*": ["./utils/*"] } } }'
            ),
        }
        p = project(files)
        found = p.candidates("pkg/a.ts", "@/models/course")
        assert found.index("src/app/models/course.ts") < found.index(
            "src/fallback/models/course.ts"
        )
        assert p.candidates("pkg/a.ts", "~exact")[0] == "src/one.ts"
        # baseUrl: a bare specifier may name a file under it.
        assert p.candidates("pkg/a.ts", "lib/x")[1] == "src/lib/x.ts"
        # Without baseUrl, paths resolve against the config's own directory.
        assert p.candidates("other/b.js", "#u/fmt")[1] == "other/utils/fmt.ts"
        # A file under no config gets no mapping.
        assert p.candidates("elsewhere/c.ts", "@/models/course") == ()

    def test_the_longest_matching_pattern_wins(self) -> None:
        files = {
            "tsconfig.json": (
                '{"compilerOptions": {"paths": {"@app/*": ["a/*"], '
                '"@app/core/*": ["core/*"]}}}'
            )
        }
        assert project(files).candidates("x.ts", "@app/core/y")[0] == "core/y"

    def test_a_broken_config_is_ignored_not_guessed(self) -> None:
        files = {"tsconfig.json": "{ not json", "package.json": "[1, 2]"}
        p = project(files)
        assert p.candidates("a.ts", "@/x") == ()
        assert p.packages == {}

    def test_workspace_packages_map_to_their_entries_and_subpaths(self) -> None:
        files = {
            "packages/model/package.json": (
                '{"name": "@mono/model", "main": "dist/index.js"}'
            ),
            "packages/ui/package.json": (
                '{"name": "ui", "exports": {".": {"svelte": "./src/lib/index.ts"},'
                ' "./icons/*": "./src/lib/icons/*.svelte"}}'
            ),
            "node_modules/react/package.json": '{"name": "react"}',
        }
        p = project(files)
        model = p.candidates("apps/web/a.ts", "@mono/model")
        assert model[:3] == (
            "packages/model/dist/index.js",
            "packages/model/dist/index.ts",
            "packages/model/dist/index.tsx",
        )
        assert "packages/model/src/index.ts" in model
        assert p.candidates("a.ts", "ui")[0] == "packages/ui/src/lib/index.ts"
        assert p.candidates("a.ts", "ui/icons/Star")[0] == (
            "packages/ui/src/lib/icons/Star.svelte"
        )
        sub = p.candidates("a.ts", "@mono/model/src/lab")
        assert "packages/model/src/lab.ts" in sub
        # An installed package is not a workspace package.
        assert p.candidates("a.ts", "react") == ()

    def test_every_import_of_the_monorepo_resolves_as_the_fixture_says(
        self, mono: SourceSyntaxEvidence
    ) -> None:
        files = frozenset(mono.files())
        for path, links in ES_LINKS.items():
            structure = mono.structure(path)
            assert structure is not None
            assert tuple(resolve_import(e, files) for e in structure.imports) == links

    def test_the_project_is_read_once_per_engine(
        self, mono: SourceSyntaxEvidence, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        walks = 0
        listed = SourceSyntaxEvidence.files

        def counting(self: SourceSyntaxEvidence) -> tuple[str, ...]:
            nonlocal walks
            walks += 1
            return listed(self)

        monkeypatch.setattr(SourceSyntaxEvidence, "files", counting)
        for path in ES_LINKS:
            mono.structure(path)
            mono.structure_of(path, mono.text(path))
        assert walks == 1


# --- tests and test-to-source links -----------------------------------------------


@pytest.mark.req("TER-EVD-014")
class TestEcmaScriptTests:
    @pytest.mark.parametrize(
        "path",
        [
            "src/a.test.ts",
            "src/a.spec.tsx",
            "src/a.test.js",
            "src/a.spec.mjs",
            "src/__tests__/a.ts",
            "apps/web/tests/home.ts",
            "src/Card.test.svelte",
        ],
    )
    def test_test_module_conventions(self, path: str) -> None:
        assert is_test_module(path)

    @pytest.mark.parametrize(
        "path",
        [
            "src/a.ts",
            "src/testing.ts",
            "src/attest.ts",
            "src/a.test.json",
            "tests/data.json",
            "node_modules/dep/a.test.js",
            "src/test/a.ts",
            "tests/helpers.py",
        ],
    )
    def test_not_test_modules(self, path: str) -> None:
        assert not is_test_module(path)

    def test_languages_by_suffix(self) -> None:
        assert source_language("a.py") == "python"
        for suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".svelte", ".vue"):
            assert source_language("a" + suffix) == "ecmascript"
        assert source_language("a.json") is None and source_language("a.d") is None
        assert is_vendored("a/node_modules/b/c.js") and not is_vendored("node_modules")
        assert is_vendored("apps/live/static/pdf.worker.min.mjs")
        assert not is_vendored("src/admin.ts") and not is_vendored("src/minimal.ts")

    @pytest.mark.parametrize("source", sorted(ES_TESTS_OF))
    def test_every_test_importing_a_source_file(
        self, mono: SourceSyntaxEvidence, source: str
    ) -> None:
        assert mono.tests_importing(source) == ES_TESTS_OF[source]

    def test_vue_and_javascript_sources_have_their_tests(self, tmp_path: Path) -> None:
        files = {
            "src/App.vue": "<script setup>\nimport { fmt } from './fmt.mjs'\n</script>\n",
            "src/fmt.mjs": "export const fmt = (s) => s;\n",
            "src/widget.jsx": "export default () => null;\n",
            "src/App.spec.js": "import App from './App.vue';\n",
            "src/__tests__/fmt.js": "const { fmt } = require('../fmt.mjs');\n",
            "test/widget.test.jsx": "import W from '../src/widget';\n",
            "src/fmt.mjs.md": "import { fmt } from './fmt.mjs'\n",
        }
        engine = SourceSyntaxEvidence(write_repo(tmp_path, files))
        assert engine.tests_importing("src/App.vue") == ("src/App.spec.js",)
        assert engine.tests_importing("src/fmt.mjs") == ("src/__tests__/fmt.js",)
        assert engine.tests_importing("src/widget.jsx") == ("test/widget.test.jsx",)

    def test_python_files_keep_their_rule_and_other_files_are_refused(
        self, tmp_path: Path
    ) -> None:
        files = {
            **ES_REPO,
            "tools/pkg/__init__.py": "",
            "tools/pkg/core.py": "X = 1\n",
            "tools/test_core.py": "from pkg import core\n",
            "src/fake.test.ts": "import core from 'pkg.core';\n",
        }
        engine = SourceSyntaxEvidence(write_repo(tmp_path, files))
        assert engine.tests_importing("tools/pkg/core.py") == ("tools/test_core.py",)
        with pytest.raises(UnsupportedLanguageError):
            engine.tests_importing("package.json")


# --- the change surface over a TypeScript/Svelte monorepo ----------------------------


API = "apps/web/src/lib/api.ts"
UTIL = "packages/shared/src/util.ts"
THEME = "apps/web/src/lib/theme.ts"
LAB = "packages/model/src/lab.ts"


@pytest.fixture
def web(tmp_path: Path) -> Path:
    return shop_repo(tmp_path / "shop", ES_REPO)


def analyse(script: Script, root: Path, engine: str = "syntax") -> LeanAnalysis:
    g = ground_session(script.events, repository_evidence(root, engine))
    return explain(script.events, RegexTokenizer(), repository=g)


def api_task(s: Script) -> None:
    s.prompt("Make loadCourse in api.ts trim the title")
    s.read(at(API), ES_REPO[API])
    s.edit(at(API), "  formatTitle(title);", "  formatTitle(title.trim());")


@pytest.mark.req("TER-EVD-006")
class TestEcmaScriptChangeSurface:
    def test_imports_place_typescript_edits_inside_the_surface(self, web: Path) -> None:
        s = Script()
        api_task(s)
        s.edit(at(UTIL), "title.trim()", "title.trim().toLowerCase()")
        s.edit(at("apps/web/src/lib/api.spec.ts"), "'x'", "' x '")
        [surface] = analyse(s, web).surfaces
        assert surface.seeds == (API,)
        assert UTIL in surface.neighbours
        assert "packages/model/src/index.ts" in surface.neighbours
        assert "apps/web/src/lib/Card.svelte" in surface.neighbours
        assert "apps/web/src/lib/api.spec.ts" in surface.tests
        assert "apps/web/tests/home.spec.ts" in surface.tests  # a test of util
        assert {e.placement for e in surface.edits} == {EditPlacement.INSIDE}
        grounded = {"unrelated_modification", "surface_expansion"}
        assert not [f for f in analyse(s, web).findings if f.detector in grounded]

    def test_an_unlinked_typescript_edit_is_a_finding_for_review(
        self, web: Path
    ) -> None:
        s = Script()
        api_task(s)
        s.edit(at(THEME), "#000", "#111")
        a = analyse(s, web)
        [f] = [f for f in a.findings if f.detector == "unrelated_modification"]
        assert f.subject == THEME and f.confidence == 0.6 and f.uncertain

    def test_a_file_one_link_beyond_is_an_expansion(self, web: Path) -> None:
        s = Script()
        api_task(s)
        s.edit(at(LAB), "return { title };", "return { title: title.trim() };")
        [surface] = analyse(s, web).surfaces
        [edit] = [e for e in surface.edits if e.path == LAB]
        assert edit.placement is EditPlacement.EXPANSION

    def test_without_ecmascript_evidence_the_same_edit_stays_uncertain(
        self, web: Path
    ) -> None:
        s = Script()
        api_task(s)
        s.edit(at(THEME), "#000", "#111")
        a = analyse(s, web, engine="python-ast")
        [f] = [f for f in a.findings if f.detector == "unrelated_modification"]
        assert f.confidence == 0.55 and f.uncertain
        [surface] = a.surfaces
        assert surface.neighbours == ()

    def test_a_created_module_the_seed_now_imports_is_inside(self, web: Path) -> None:
        s = Script()
        api_task(s)
        s.write(at("apps/web/src/lib/slug.ts"), "export const slug = (s) => s;\n")
        s.edit(
            at(API),
            "import { formatTitle } from '@shared/util';\n",
            "import { formatTitle } from '@shared/util';\nimport { slug } from './slug';\n",
        )
        g = ground_session(s.events, repository_evidence(web, "syntax"))
        edit = list(g.edits.values())[-1]
        assert edit.parsed and "apps/web/src/lib/slug.ts" in edit.links
        assert edit.module is None  # no Python module: no contract is checked
        [surface] = analyse(s, web).surfaces
        placements = {e.path: e.placement for e in surface.edits}
        assert placements["apps/web/src/lib/slug.ts"] is EditPlacement.INSIDE

    def test_grounding_walks_the_repository_at_most_twice(
        self, web: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Once for the file list, once for the project configuration; never
        # per source file or per edit.
        walks = 0
        listed = SourceSyntaxEvidence.files

        def counting(self: SourceSyntaxEvidence) -> tuple[str, ...]:
            nonlocal walks
            walks += 1
            return listed(self)

        monkeypatch.setattr(SourceSyntaxEvidence, "files", counting)
        s = Script()
        api_task(s)
        s.edit(at(THEME), "#000", "#111")
        s.write(at("apps/web/src/lib/new.ts"), "import './theme';\n")
        ground_session(s.events, SourceSyntaxEvidence(web))
        assert walks <= 2

    def test_installed_packages_are_not_read(self, web: Path) -> None:
        s = Script()
        api_task(s)
        g = ground_session(s.events, repository_evidence(web, "syntax"))
        assert not [p for p in g.links if "node_modules" in p]
        assert g.links[API] == ("packages/model/src/index.ts", UTIL)
        assert g.importers[API] == (
            "apps/web/src/lib/Card.svelte",
            "apps/web/src/lib/api.spec.ts",
            "apps/web/src/lib/index.ts",
            "apps/web/src/routes/+page.svelte",
        )


@pytest.mark.req("TER-EVD-012")
def test_json_comments_are_stripped_but_strings_are_kept() -> None:
    text = '{ "a": "x // not a comment", /* c */ "b": [1, 2,], } // end'
    assert (
        strip_json_comments(text).replace(" ", "") == '{"a":"x//notacomment","b":[1,2]}'
    )
    assert tokenize("a/*x*/b").texts == ["a", "b"]
