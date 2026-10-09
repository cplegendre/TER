"""Session languages and repository stack (L3: TER-STK-001, TER-STK-002)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from ter4_lean_builder import Script

from ter import bootstrap
from ter.adapters.driven.in_memory import (
    InMemoryRepositoryEvidence,
    InMemorySessionSource,
)
from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.cli import format_profile, main
from ter.application import ExplainSession
from ter.application.stack import read_stack
from ter.domain import AnalysisEngine, SessionTrace
from ter.domain.lean import explain
from ter.domain.stack import (
    LANGUAGE_BY_EXTENSION,
    FileTouch,
    RepositoryStack,
    StackFact,
    StackKind,
    is_manifest,
    language_of,
    languages_of,
    manifest_facts,
    merge_facts,
    stack_label,
)

STK1 = pytest.mark.req("TER-STK-001")
STK2 = pytest.mark.req("TER-STK-002")


def facts(path: str, text: str) -> set[tuple[str, str]]:
    return {(f.kind.value, f.name) for f in manifest_facts(path, text)}


def mixed_session() -> Script:
    s = Script("mixed")
    s.prompt("Add a tutor card to the course page")
    s.read("/w/repo/src/lib/Card.svelte")
    s.read("/w/repo/src/lib/course.ts")
    s.read("/w/repo/README.md")
    s.edit("/w/repo/src/lib/course.ts")
    s.edit("/w/repo/src/lib/course.ts")
    s.edit("/w/repo/src/lib/Card.svelte")
    s.write("/w/repo/scripts/seed.py", "print(1)\n")
    s.search("Card")
    s.bash("pnpm test")
    s.say("Done.")
    return s


# --- languages from file names (TER-STK-001) --------------------------------


@STK1
class TestLanguages:
    def test_the_documented_table_maps_common_extensions(self) -> None:
        assert language_of("a/b.ts") == "TypeScript"
        assert language_of("a/b.TSX") == "TypeScript"
        assert language_of("C:\\w\\App.svelte") == "Svelte"
        assert language_of("x.py") == "Python"
        assert language_of("go/main.go") == "Go"
        assert language_of("Dockerfile") == "Dockerfile"
        assert language_of("docker/Dockerfile.dev") == "Dockerfile"
        assert language_of("unknown.zzz") is None
        assert language_of(".bashrc") is None  # a dot file has no extension
        assert all(k == k.lower() and k.startswith(".") for k in LANGUAGE_BY_EXTENSION)

    def test_a_mixed_language_session_counts_each_language_with_evidence(
        self,
    ) -> None:
        s = mixed_session()
        profile = explain(s.events, RegexTokenizer()).profile
        ts = profile.language("TypeScript")
        svelte = profile.language("Svelte")
        py = profile.language("Python")
        md = profile.language("Markdown")
        assert ts is not None and svelte is not None and py is not None
        assert md is not None
        assert (ts.edits, ts.reads, ts.files_edited, ts.files_read) == (2, 1, 1, 1)
        assert (svelte.edits, svelte.reads) == (1, 1)
        assert (py.edits, py.reads) == (1, 0)
        assert (md.edits, md.reads) == (0, 1)
        # Evidence: exactly the request events, never their results.
        requests = {
            e.id: e for e in s.events if e.kind.value == "tool.requested" and e.tool
        }
        for use in profile.languages:
            assert use.evidence and all(i in requests for i in use.evidence)
        assert len(ts.evidence) == 3
        assert profile.dominant == "TypeScript" and profile.dominant_basis == "edits"
        assert [u.language for u in profile.languages][0] == "TypeScript"
        assert profile.stack is None  # no repository evidence was given

    def test_a_session_with_no_edits_takes_the_language_it_read_most(self) -> None:
        s = Script()
        s.prompt("How does pricing work?")
        s.read("src/pricing.go")
        s.read("src/tax.go")
        s.read("src/ui.ts")
        s.say("It multiplies.")
        p = explain(s.events, RegexTokenizer()).profile
        assert p.dominant == "Go" and p.dominant_basis == "reads"
        assert all(u.edits == 0 for u in p.languages)

    def test_a_session_that_names_no_file_has_no_language(self) -> None:
        s = Script()
        s.prompt("hello")
        s.search("x")
        s.bash("ls")
        s.say("hi")
        p = explain(s.events, RegexTokenizer()).profile
        assert p.languages == () and p.dominant is None and p.dominant_basis is None
        assert p.unrecognised == ()

    def test_unknown_extensions_are_counted_apart_and_never_dominant(self) -> None:
        s = Script()
        s.prompt("tweak")
        s.edit("a/b.zzz")
        s.edit("a/c.zzz")
        s.edit("a/LICENSE")
        s.read("a/d.py")
        p = explain(s.events, RegexTokenizer()).profile
        assert p.unrecognised == (("(none)", 1), (".zzz", 2))
        assert [u.language for u in p.languages] == ["Python"]
        assert p.dominant == "Python" and p.dominant_basis == "reads"

    def test_code_edits_outrank_markup_edits_and_markup_edits_outrank_reads(
        self,
    ) -> None:
        def dominant(*touches: tuple[str, bool]) -> tuple[str | None, str | None]:
            p = languages_of(
                FileTouch(f"e{i}", edit, path) for i, (path, edit) in enumerate(touches)
            )
            return p.dominant, p.dominant_basis

        # One code edit beats three Markdown edits.
        assert dominant(
            ("a.md", True), ("b.md", True), ("c.md", True), ("x.rs", True)
        ) == ("Rust", "edits")
        # A docs-only session is a Markdown session even when it read code.
        assert dominant(("a.md", True), ("x.rs", False), ("y.rs", False)) == (
            "Markdown",
            "edits",
        )
        # Ties: more reads, then the name.
        assert dominant(("a.ts", True), ("b.py", True), ("c.py", False)) == (
            "Python",
            "edits",
        )
        assert dominant(("a.ts", True), ("b.py", True)) == ("Python", "edits")
        assert dominant() == (None, None)

    def test_the_profile_is_the_same_live_and_in_batch(self) -> None:
        s = mixed_session()
        engine = AnalysisEngine(RegexTokenizer())
        half = len(s.events) // 2
        for event in s.events[:half]:
            engine.apply(event)
        early = engine.explain().profile
        for event in s.events[half:]:
            engine.apply(event)
            engine.apply(event)  # idempotent by event id
        live = engine.explain().profile
        assert live == explain(s.events, RegexTokenizer()).profile
        assert (
            early.as_dict()
            == explain(s.events[:half], RegexTokenizer()).profile.as_dict()
        )


# --- stack from manifests (TER-STK-002) -------------------------------------


SVELTE_PACKAGE = json.dumps(
    {
        "name": "course",
        "packageManager": "pnpm@9.1.0",
        "devDependencies": {"@sveltejs/kit": "^2", "svelte": "^5", "vite": "^5"},
        "dependencies": {"typescript": "^5"},
    }
)


@STK2
class TestManifests:
    def test_svelte_and_sveltekit_are_detected_from_package_json(self) -> None:
        found = facts("apps/course/package.json", SVELTE_PACKAGE)
        assert {
            ("ecosystem", "node"),
            ("framework", "svelte"),
            ("framework", "sveltekit"),
            ("build_tool", "vite"),
            ("language", "typescript"),
            ("package_manager", "pnpm"),
        } == found
        assert all(
            f.sources == ("apps/course/package.json",)
            for f in manifest_facts("apps/course/package.json", SVELTE_PACKAGE)
        )

    def test_react_next_vue_and_workspaces(self) -> None:
        assert ("framework", "next") in facts(
            "package.json", '{"dependencies": {"next": "1", "react": "1"}}'
        )
        assert ("framework", "vue") in facts(
            "package.json", '{"dependencies": {"vue": "3"}}'
        )
        assert ("workspace", "yarn workspaces") in facts(
            "package.json", '{"packageManager": "yarn@4", "workspaces": ["apps/*"]}'
        )
        assert ("workspace", "npm workspaces") in facts(
            "package.json",
            '{"packageManager": "npm@10", "workspaces": {"packages": ["a"]}}',
        )
        assert ("workspace", "package.json workspaces") in facts(
            "package.json", '{"workspaces": ["a"]}'
        )
        assert not any(
            k == "workspace" for k, _ in facts("package.json", '{"workspaces": []}')
        )

    def test_pnpm_workspaces(self) -> None:
        assert facts("pnpm-workspace.yaml", "packages:\n  - 'apps/*'\n") == {
            ("ecosystem", "node"),
            ("package_manager", "pnpm"),
            ("workspace", "pnpm workspaces"),
        }
        assert ("workspace", "pnpm workspaces") not in facts(
            "pnpm-workspace.yaml", "# empty\n"
        )
        assert facts("pnpm-lock.yaml", "lockfileVersion: 9") == {
            ("ecosystem", "node"),
            ("package_manager", "pnpm"),
        }

    def test_pyproject_and_requirements(self) -> None:
        pyproject = (
            '[build-system]\nrequires = ["hatchling"]\n'
            'build-backend = "hatchling.build"\n'
            '[project]\nname = "x"\ndependencies = ["FastAPI>=0.1", "numpy"]\n'
            '[project.optional-dependencies]\ndev = ["pytest"]\n'
            "[tool.uv.workspace]\nmembers = ['pkgs/*']\n"
        )
        assert facts("pyproject.toml", pyproject) == {
            ("ecosystem", "python"),
            ("build_tool", "hatch"),
            ("framework", "fastapi"),
            ("framework", "numpy"),
            ("package_manager", "uv"),
            ("workspace", "uv workspace"),
        }
        poetry = (
            '[build-system]\nbuild-backend = "poetry.core.masonry.api"\n'
            "[tool.poetry.dependencies]\npython = '^3.11'\nDjango = '^5'\n"
        )
        assert facts("pyproject.toml", poetry) == {
            ("ecosystem", "python"),
            ("build_tool", "poetry"),
            ("package_manager", "poetry"),
            ("framework", "django"),
        }
        assert facts(
            "requirements-dev.txt",
            "# web\nflask==3.0 ; python_version>'3'\n-r base.txt\n",
        ) == {("ecosystem", "python"), ("framework", "flask")}

    def test_go_cargo_and_jvm(self) -> None:
        go_mod = (
            "module example.com/x\n\ngo 1.22\n\nrequire (\n"
            "\tgithub.com/gin-gonic/gin v1.9.1\n\tgithub.com/spf13/cobra v1.8.0\n)\n"
        )
        assert facts("go.mod", go_mod) == {
            ("ecosystem", "go"),
            ("framework", "gin"),
            ("framework", "cobra"),
        }
        cargo = '[workspace]\nmembers = ["a"]\n[workspace.dependencies]\naxum = "0.7"\n'
        assert facts("Cargo.toml", cargo) == {
            ("ecosystem", "rust"),
            ("workspace", "cargo workspace"),
            ("framework", "axum"),
        }
        pom = (
            "<project><parent><groupId>org.springframework.boot</groupId></parent>"
            "<modules>\n  <module>api</module></modules></project>"
        )
        assert facts("pom.xml", pom) == {
            ("ecosystem", "jvm"),
            ("build_tool", "maven"),
            ("framework", "spring-boot"),
            ("workspace", "maven multi-module"),
        }
        assert ("language", "kotlin") in facts(
            "build.gradle.kts", 'plugins { kotlin("jvm") version "2.0.0" }'
        )
        assert ("workspace", "gradle multi-project") in facts(
            "settings.gradle", "include 'app'\n"
        )

    def test_a_malformed_manifest_still_declares_its_ecosystem(self) -> None:
        assert facts("package.json", "{not json") == {("ecosystem", "node")}
        assert facts("package.json", "[1, 2]") == {("ecosystem", "node")}
        assert facts("pyproject.toml", "[[[") == {("ecosystem", "python")}
        assert facts("Cargo.toml", "= =") == {("ecosystem", "rust")}
        assert facts("notes.txt", "svelte") == set()

    def test_manifests_under_dependency_and_output_directories_are_ignored(
        self,
    ) -> None:
        assert is_manifest("package.json")
        assert is_manifest("apps/web/package.json")
        assert is_manifest("requirements-test.txt")
        assert not is_manifest("node_modules/svelte/package.json")
        assert not is_manifest("apps/web/node_modules/x/package.json")
        assert not is_manifest(".venv/lib/pyproject.toml")
        assert not is_manifest("src/package.ts")
        assert not is_manifest("docs/requirements.md")

    def test_a_monorepo_merges_facts_and_cites_every_manifest(self) -> None:
        repo = InMemoryRepositoryEvidence(
            {
                "package.json": '{"workspaces": ["apps/*"], "devDependencies": {"turbo": "2"}}',
                "pnpm-workspace.yaml": "packages:\n  - apps/*\n",
                "apps/course/package.json": SVELTE_PACKAGE,
                "apps/admin/package.json": '{"dependencies": {"svelte": "5", "react": "18"}}',
                "node_modules/react/package.json": '{"dependencies": {"vue": "3"}}',
                "apps/course/src/App.svelte": "<p/>",
            }
        )
        stack = read_stack(repo)
        assert stack.manifests == (
            "apps/admin/package.json",
            "apps/course/package.json",
            "package.json",
            "pnpm-workspace.yaml",
        )
        by = {(f.kind, f.name): f.sources for f in stack.facts}
        assert by[(StackKind.FRAMEWORK, "svelte")] == (
            "apps/admin/package.json",
            "apps/course/package.json",
        )
        assert by[(StackKind.FRAMEWORK, "react")] == ("apps/admin/package.json",)
        assert (StackKind.FRAMEWORK, "vue") not in by  # node_modules ignored
        assert by[(StackKind.WORKSPACE, "pnpm workspaces")] == ("pnpm-workspace.yaml",)
        assert (StackKind.BUILD_TOOL, "turborepo") in by
        assert stack_label(stack) == "react+svelte+sveltekit"
        # Ordered by kind, then name.
        kinds = [f.kind for f in stack.facts]
        assert kinds == sorted(kinds, key=list(StackKind).index)

    def test_manifest_reads_are_bounded_and_unreadable_ones_counted(self) -> None:
        files = {f"p{i}/package.json": "{}" for i in range(5)}
        files["bun.lockb"] = "\0binary"
        stack = read_stack(InMemoryRepositoryEvidence(files), limit=3)
        assert stack.truncated and len(stack.manifests) == 3
        assert stack.manifests[0] == "bun.lockb"  # shallowest first

    def test_merge_and_labels(self) -> None:
        merged = merge_facts(
            [
                StackFact(StackKind.ECOSYSTEM, "node", ("b/package.json",)),
                StackFact(StackKind.ECOSYSTEM, "node", ("a/package.json",)),
            ]
        )
        assert merged == (
            StackFact(
                StackKind.ECOSYSTEM, "node", ("a/package.json", "b/package.json")
            ),
        )
        assert stack_label(None) == "unknown"
        assert stack_label(RepositoryStack()) == "none"
        assert stack_label(RepositoryStack(merged)) == "node"


# --- in explain, A3 and the CLI ---------------------------------------------


def _explained(repo: InMemoryRepositoryEvidence | None) -> object:
    s = mixed_session()
    source = InMemorySessionSource(
        {"mixed": SessionTrace("mixed", "script", tuple(s.events))}
    )
    return ExplainSession(source, RegexTokenizer(), repository=repo)("mixed")


@STK1
@STK2
def test_explain_and_a3_carry_the_profile_with_and_without_a_repository() -> None:
    from ter.application.explain import ExplainedSession

    plain = _explained(None)
    assert isinstance(plain, ExplainedSession)
    assert plain.analysis.profile.stack is None
    lean = plain.analysis.as_dict()
    assert lean["profile"]["dominant_language"] == "TypeScript"  # type: ignore[index]
    assert lean["profile"]["stack"] is None  # type: ignore[index]
    a3 = plain.a3.as_dict()
    assert a3["background"]["profile"] == lean["profile"]  # type: ignore[index]
    text = format_profile(plain.analysis)
    assert "TypeScript 2 edit(s)/1 read(s)" in text and "dominant TypeScript" in text
    assert "stack" not in text

    repo = InMemoryRepositoryEvidence(
        {"package.json": SVELTE_PACKAGE, "src/lib/course.ts": "export {}\n"}
    )
    grounded = _explained(repo)
    assert isinstance(grounded, ExplainedSession)
    stack = grounded.analysis.profile.stack
    assert stack is not None and stack.manifests == ("package.json",)
    assert grounded.a3.analysis.profile.stack == stack
    d = grounded.analysis.as_dict()["profile"]["stack"]  # type: ignore[index]
    assert d["label"] == "svelte+sveltekit"
    assert {"kind": "framework", "name": "svelte", "sources": ["package.json"]} in d[
        "facts"
    ]
    assert "stack            svelte+sveltekit" in format_profile(grounded.analysis)


@STK1
def test_the_cli_prints_languages_and_the_json_lists_them(tmp_path: Path) -> None:
    from test_ter4_grounded_cli import Transcript

    t = Transcript()
    t.prompt("Fix the card")
    t.tool("Read", {"file_path": "/w/r/src/Card.svelte"}, "<p/>")
    t.tool(
        "Edit",
        {"file_path": "/w/r/src/Card.svelte", "old_string": "p", "new_string": "q"},
        "updated",
    )
    t.say("Done.")
    path = t.write(tmp_path / "s.jsonl")
    out, err = io.StringIO(), io.StringIO()
    assert (
        main(["explain", str(path)], bootstrap.cli_services(), stdout=out, stderr=err)
        == 0
    )
    assert "languages        Svelte 1 edit(s)/1 read(s)" in out.getvalue()
    out = io.StringIO()
    assert (
        main(
            ["explain", str(path), "--json"],
            bootstrap.cli_services(),
            stdout=out,
            stderr=err,
        )
        == 0
    )
    profile = json.loads(out.getvalue())["profile"]
    assert profile["dominant_language"] == "Svelte"
    assert profile["languages"][0]["evidence"]
