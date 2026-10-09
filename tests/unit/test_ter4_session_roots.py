"""Session roots (L3): more than one checkout (TER-EVD-017), path spelling
(TER-EVD-018), harness state (TER-EVD-019) and the root of a session that
only creates files in new directories (TER-EVD-020). Positive, negative and
boundary cases for the pure rules in ``ter.domain.repository``, and the
placements they give on the synthetic shop repository."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
from ter4_lean_builder import Script
from ter4_shop_repo import GIT_ENV, SESSION_ROOT, SHOP, at, shop_repo

from ter.adapters.driven.tokenizers import RegexTokenizer
from ter.adapters.driving.cli import format_surfaces
from ter.application.ground import ground_session
from ter.bootstrap.capabilities import repository_evidence
from ter.domain.lean import LeanAnalysis, explain
from ter.domain.lean.a3 import build_a3
from ter.domain.lean.surface import GROUNDED_DETECTORS, EditPlacement
from ter.domain.repository import (
    canonical_path,
    is_harness_state,
    repository_path,
    session_root,
    session_roots,
)

EVD17 = pytest.mark.req("TER-EVD-017")
EVD18 = pytest.mark.req("TER-EVD-018")
EVD19 = pytest.mark.req("TER-EVD-019")
EVD20 = pytest.mark.req("TER-EVD-020")
GROUNDED = {d.id for d in GROUNDED_DETECTORS}

FILES = {"src/app/a.py", "src/app/b.py", "README.md", "package.json"}
PRICING = "src/app/domain/pricing.py"
SUMMARY = "src/app/reports/summary.py"
WORKTREE = f"{SESSION_ROOT}/.claude/worktrees/fix-rounding"


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    return shop_repo(tmp_path / "shop")


def analyse(script: Script, root: Path) -> LeanAnalysis:
    g = ground_session(script.events, repository_evidence(root, "python-ast"))
    return explain(script.events, RegexTokenizer(), repository=g)


def placements(analysis: LeanAnalysis) -> dict[str, EditPlacement]:
    return {e.path: e.placement for s in analysis.surfaces for e in s.edits}


# --- more than one checkout ---------------------------------------------------


@EVD17
class TestMoreThanOneCheckout:
    def test_a_worktree_checkout_inside_the_main_root_is_a_root(self) -> None:
        paths = ["/w/p/src/app/a.py", "/w/p/.claude/worktrees/x/src/app/b.py"]
        roots = session_roots(paths, FILES)
        assert roots == ("/w/p", "/w/p/.claude/worktrees/x")
        # The deepest root decides.
        assert repository_path(paths[1], roots) == "src/app/b.py"
        assert repository_path(paths[0], roots) == "src/app/a.py"

    def test_a_worktree_whose_remainders_name_a_new_file_in_a_known_directory(
        self,
    ) -> None:
        roots = session_roots(
            ["/w/p/src/app/a.py", "/w/p/.claude/worktrees/x/src/app/new.py"], FILES
        )
        assert roots == ("/w/p", "/w/p/.claude/worktrees/x")

    def test_a_worktree_that_names_nothing_of_the_repository_is_not_a_root(
        self,
    ) -> None:
        paths = ["/w/p/src/app/a.py", "/w/p/.claude/worktrees/x/notes/todo.txt"]
        assert session_roots(paths, FILES) == ("/w/p",)
        # Boundary: the worktree directory itself, with no remainder.
        assert session_roots(
            ["/w/p/src/app/a.py", "/w/p/.claude/worktrees/x"], FILES
        ) == ("/w/p",)

    def test_the_checkout_a_worktree_was_made_from_is_a_root(self) -> None:
        paths = [
            "/w/p/.claude/worktrees/x/src/app/a.py",
            "/w/p/.claude/worktrees/x/src/app/b.py",
            "/w/p/README.md",
        ]
        roots = session_roots(paths, FILES)
        assert roots == ("/w/p/.claude/worktrees/x", "/w/p")
        assert repository_path("/w/p/README.md", roots) == "README.md"
        assert repository_path(paths[0], roots) == "src/app/a.py"

    def test_the_owner_needs_a_path_of_its_own_outside_claude(self) -> None:
        paths = [
            "/w/p/.claude/worktrees/x/src/app/a.py",
            "/w/p/.claude/plans/plan.md",
            "/w/p/notes.txt",
        ]
        assert session_roots(paths, FILES) == ("/w/p/.claude/worktrees/x",)
        # Boundary: a worktree at the top of the file system has no owner.
        assert session_roots(["/.claude/worktrees/x/src/app/a.py"], FILES) == (
            "/.claude/worktrees/x",
        )

    def test_another_checkout_with_two_files_one_in_a_subdirectory(self) -> None:
        paths = [
            "/w/p/src/app/a.py",
            "/w/p/src/app/b.py",
            "/old/copy/src/app/a.py",
            "/old/copy/README.md",
        ]
        roots = session_roots(paths, FILES)
        assert roots == ("/w/p", "/old/copy")
        assert repository_path("/old/copy/src/app/new.py", roots) == "src/app/new.py"

    def test_stray_top_level_files_elsewhere_do_not_make_a_root(self) -> None:
        paths = [
            "/w/p/src/app/a.py",
            "/w/p/src/app/b.py",
            "/other/README.md",
            "/other/package.json",
        ]
        assert session_roots(paths, FILES) == ("/w/p",)

    def test_one_file_in_a_subdirectory_is_not_enough(self) -> None:
        # Boundary: one path, and the same path twice, are one distinct file.
        paths = [
            "/w/p/src/app/a.py",
            "/old/copy/src/app/a.py",
            "/old/copy/src/app/a.py",
        ]
        assert session_roots(paths, FILES) == ("/w/p",)

    def test_session_root_is_the_main_root_and_stays_compatible(self) -> None:
        paths = ["/w/p/src/app/a.py", "/w/p/.claude/worktrees/x/src/app/b.py"]
        assert session_root(paths, FILES) == "/w/p"
        assert session_root([], FILES) is None
        assert repository_path("/w/p/src/app/a.py", "/w/p") == "src/app/a.py"
        assert repository_path("/w/p/src/app/a.py", ()) is None

    def test_worktree_edits_are_placed_against_the_surface(self, shop: Path) -> None:
        s = Script()
        s.prompt("Fix the rounding in pricing.py")
        s.read(at(PRICING), SHOP[PRICING])
        s.edit(f"{WORKTREE}/{PRICING}", "* 1.2", "* 12 / 10")
        s.edit(f"{WORKTREE}/{SUMMARY}", "len(rows)", "len(list(rows))")
        a = analyse(s, shop)
        assert placements(a) == {
            PRICING: EditPlacement.INSIDE,
            SUMMARY: EditPlacement.UNRELATED,
        }
        # Most paths name the worktree, so it is the main root; the checkout
        # it was made from follows.
        g = a.repository
        assert g is not None and g.roots == (WORKTREE, SESSION_ROOT)
        assert g.root == WORKTREE
        assert g.as_dict()["roots"] == [WORKTREE, SESSION_ROOT]
        a3 = build_a3(a).as_dict()
        assert a3["background"]["repository"]["roots"] == [  # type: ignore[index]
            WORKTREE,
            SESSION_ROOT,
        ]
        assert f"  repository roots {WORKTREE}, {SESSION_ROOT}" in format_surfaces(a)

    def test_an_ungrounded_a3_has_no_repository(self) -> None:
        s = Script()
        s.prompt("Fix it")
        s.edit("/w/p/a.py")
        a3 = build_a3(explain(s.events, RegexTokenizer())).as_dict()
        assert "repository" not in a3["background"]  # type: ignore[operator]
        json.dumps(a3)


# --- path spelling --------------------------------------------------------------


@EVD18
class TestPathSpelling:
    def test_drive_letters_and_separators_are_one_spelling(self) -> None:
        assert canonical_path("d:\\code\\repo\\a.py") == "D:/code/repo/a.py"
        assert canonical_path("D:/code/repo") == "D:/code/repo"
        assert canonical_path("/d/code/repo", windows=True) == "D:/code/repo"
        assert canonical_path("/d", windows=True) == "D:/"
        # Without a drive-letter path in the session, /d is a directory.
        assert canonical_path("/d/code/repo") == "/d/code/repo"
        # Only single letters are drives.
        assert canonical_path("/dev/x", windows=True) == "/dev/x"

    def test_every_spelling_of_a_windows_path_reaches_one_root(self) -> None:
        paths = [
            "D:/code/repo/src/app/a.py",
            "d:\\code\\repo\\src\\app\\b.py",
            "/d/code/repo/README.md",
        ]
        roots = session_roots(paths, FILES)
        assert roots == ("D:/code/repo",)
        assert [repository_path(p, roots) for p in paths] == [
            "src/app/a.py",
            "src/app/b.py",
            "README.md",
        ]

    def test_a_git_bash_only_session_keeps_its_own_spelling(self) -> None:
        paths = ["/d/code/repo/src/app/a.py", "/d/code/repo/src/app/b.py"]
        assert session_roots(paths, FILES) == ("/d/code/repo",)
        assert repository_path(paths[0], "/d/code/repo") == "src/app/a.py"
        # A POSIX root never matches a drive spelling.
        assert repository_path("D:/code/repo/src/app/a.py", "/d/code/repo") is None

    def test_no_other_case_folding(self) -> None:
        assert repository_path("D:/Code/repo/src/app/a.py", "D:/code/repo") is None
        assert repository_path("/W/p/src/app/a.py", "/w/p") is None
        assert repository_path("d:/code/repo/src/app/a.py", "D:/code/repo") == (
            "src/app/a.py"
        )


# --- harness state ---------------------------------------------------------------


@EVD19
class TestHarnessState:
    @pytest.mark.parametrize(
        "path",
        [
            "/home/dev/.claude/plans/fix-rounding.md",
            "/home/dev/.claude/CLAUDE.md",
            "C:\\Users\\dev\\.claude\\settings.json",
            "~/.claude/projects/x/memory.md",
            "/w/p/.claude/worktrees/x/.claude/settings.local.json",
        ],
    )
    def test_paths_under_a_claude_directory_are_harness_state(self, path: str) -> None:
        assert is_harness_state(path)

    @pytest.mark.parametrize(
        "path",
        [
            "/w/p/.claude/worktrees/x/src/a.py",
            "/w/p/src/a.py",
            "/w/p/claude/notes.md",
            "/w/p/.claude",  # boundary: the directory itself
            "/w/p/.Claude/x.md",  # no case folding
        ],
    )
    def test_other_paths_are_not(self, path: str) -> None:
        assert not is_harness_state(path)

    def test_harness_edits_are_placed_and_never_judged(self, shop: Path) -> None:
        s = Script()
        s.prompt("Fix the rounding in pricing.py")
        s.edit(at(PRICING), "* 1.2", "* 12 / 10")
        s.write("/home/dev/.claude/plans/rounding.md", "# plan\n")
        s.write("/home/dev/.claude/projects/shop/memory/MEMORY.md", "note\n")
        s.write("/tmp/scratch.py", "print(1)\n")
        a = analyse(s, shop)
        assert placements(a) == {
            PRICING: EditPlacement.INSIDE,
            "/home/dev/.claude/plans/rounding.md": EditPlacement.HARNESS_STATE,
            "/home/dev/.claude/projects/shop/memory/MEMORY.md": (
                EditPlacement.HARNESS_STATE
            ),
            "/tmp/scratch.py": EditPlacement.OUTSIDE_REPOSITORY,
        }
        assert not [f for f in a.findings if f.detector in GROUNDED]
        [surface] = a.surfaces
        assert surface.outside() == ()

    def test_a_harness_only_task_seeds_nothing(self, shop: Path) -> None:
        s = Script()
        s.prompt("Write the plan down")
        s.write("/home/dev/.claude/plans/p.md", "# plan\n")
        s.edit(at(SUMMARY), "len(rows)", "len(list(rows))")
        [surface] = analyse(s, shop).surfaces
        assert surface.seeds == (SUMMARY,)  # the first repository edit

    def test_the_projects_own_claude_files_stay_repository_paths(
        self, tmp_path: Path
    ) -> None:
        files = {**SHOP, ".claude/settings.json": "{}\n", "CLAUDE.md": "# rules\n"}
        repo = shop_repo(tmp_path / "shop", files)
        s = Script()
        s.prompt("Fix the rounding in pricing.py")
        s.edit(at(PRICING), "* 1.2", "* 12 / 10")
        s.edit(at(".claude/settings.json"), "{}", '{"a": 1}')
        s.edit(at("CLAUDE.md"), "rules", "the rules")
        placed = placements(analyse(s, repo))
        assert placed[".claude/settings.json"] is EditPlacement.UNRELATED
        assert placed["CLAUDE.md"] is EditPlacement.UNRELATED


# --- a session that only creates files in new directories -------------------------


JAVA = {
    "pom.xml",
    "src/main/java/com/acme/App.java",
    "src/test/java/com/acme/AppTest.java",
}


@EVD20
class TestNewDirectories:
    def test_new_files_under_a_deep_repository_directory_find_the_root(
        self,
    ) -> None:
        paths = [
            "/home/me/acme/src/main/java/com/acme/billing/Invoice.java",
            "/home/me/acme/src/main/java/com/acme/billing/Line.java",
        ]
        assert session_roots(paths, JAVA) == ("/home/me/acme",)
        assert repository_path(paths[0], "/home/me/acme") == (
            "src/main/java/com/acme/billing/Invoice.java"
        )

    def test_a_top_level_directory_name_alone_makes_no_root(self) -> None:
        # ``src`` is a repository directory, but /home/me/src is not a root.
        assert session_roots(["/home/me/src/other/new/A.java"], JAVA) == ()

    def test_two_levels_deep_is_enough(self) -> None:
        # Boundary: the deepest repository directory above the path is
        # ``src/main`` (two levels).
        files = {"src/main/App.java", "README.md"}
        assert session_roots(["/w/p/src/main/pkg/New.java"], files) == ("/w/p",)

    def test_a_known_file_or_directory_vote_wins_first(self) -> None:
        paths = [
            "/home/me/acme/src/main/java/com/acme/App.java",
            "/elsewhere/x/src/main/java/com/acme/new/B.java",
            "/elsewhere/y/src/main/java/com/acme/new/C.java",
        ]
        assert session_root(paths, JAVA) == "/home/me/acme"


# --- corpus diagnostics (scripts/corpus_grounded.py) ---------------------------


def _corpus_script() -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "corpus_grounded", root / "scripts" / "corpus_grounded.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@EVD17
@EVD18
def test_the_corpus_script_splits_reasons_an_edit_is_outside() -> None:
    why = _corpus_script().why_outside
    roots, files = ("D:/code/repo",), {"src/app/a.py"}
    cwd = "D:/code/repo"
    assert why("/d/code/repo/x.py", roots, cwd, files) == "Git-Bash drive spelling"
    assert why("E:/old/repo/src/app/a.py", roots, cwd, files).startswith(
        "another checkout of the repository"
    )
    assert why("C:/Users/me/AppData/Local/Temp/x.py", roots, cwd, files) == (
        "temporary directory"
    )
    assert why("/tmp/x.py", ("/w/p",), "/w/p", files) == "temporary directory"
    assert why("/srv/other/x.py", ("/w/p",), "/w/p", files) == "elsewhere"
    assert why("/w/p/x.py", (), "/w/p", files) == (
        "no session root found (path under cwd)"
    )
    assert why("/w/p/.claude/worktrees/n/x.py", ("/w/p",), "/w/p", files) == (
        "under a .claude/worktrees checkout"
    )


@EVD17
def test_the_corpus_script_counts_sessions_by_roots() -> None:
    bucket = _corpus_script().roots_bucket
    assert [bucket(n) for n in (0, 1, 2, 3)] == ["0", "1", "2+", "2+"]


@EVD17
def test_the_corpus_script_exports_again_for_another_commit(tmp_path: Path) -> None:
    repo = shop_repo(tmp_path / "shop")
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "added.txt").write_text("new\n", encoding="utf-8")
    _git(repo, "add", "added.txt")
    _git(repo, "commit", "-q", "-m", "second")
    second = _git(repo, "rev-parse", "HEAD")
    export = _corpus_script().export
    dest = tmp_path / "export"
    export(str(repo), first, dest)
    assert not (dest / "added.txt").exists()
    export(str(repo), second, dest)
    assert (dest / "added.txt").exists()
    assert (dest / ".ter-exported").read_text(encoding="utf-8") == second
    export(str(repo), first, dest)
    assert not (dest / "added.txt").exists()


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV, "HOME": str(repo.parent)}
    done = subprocess.run(
        ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()
