"""The Git engine: lexical evidence over Git's files, plus diff and history.

Only this module runs ``git`` (a subprocess; no Git library is a
dependency). Every command pins the settings that would otherwise make the
output depend on the user's configuration: no colour, no external diff, no
rename detection in the diff, unquoted paths, the C locale and literal
pathspecs.

* :meth:`GitRepositoryEvidence.files` lists what Git sees: tracked files
  still on disk and untracked files not ignored.
* :meth:`~GitRepositoryEvidence.diff` is the working tree (staged, unstaged
  and untracked changes) against ``HEAD``, or against the empty tree before
  the first commit.
* :meth:`~GitRepositoryEvidence.history` is a file's commits, newest first,
  following renames.

Pointing the engine at anything but the top level of a Git working tree
raises :class:`~ter.domain.repository.NotAWorkTreeError` at construction,
so a caller never mistakes "no Git" for "no changes" (TER-EVD-013).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from ....domain.repository import (
    ChangeStatus,
    FileChange,
    FileCommit,
    NotAWorkTreeError,
    RepositoryDiff,
    RepositoryEvidenceError,
)
from .lexical import LexicalRepositoryEvidence

__all__ = ["GitRepositoryEvidence"]

_SETTINGS = (
    "-c", "core.quotepath=off",
    "-c", "color.ui=never",
    "-c", "diff.renames=false",
    "-c", "diff.external=",
    "-c", "log.showSignature=false",
)  # fmt: skip

_STATUS = {
    "A": ChangeStatus.ADDED,
    "M": ChangeStatus.MODIFIED,
    "D": ChangeStatus.DELETED,
    "T": ChangeStatus.TYPE_CHANGED,
}

_RECORD, _FIELD = "\x1e", "\x1f"


def _count(value: str) -> int | None:
    return None if value == "-" else int(value)


class GitRepositoryEvidence(LexicalRepositoryEvidence):
    """A :class:`~ter.ports.driven.RepositoryEvidence` over a Git working tree."""

    name = "git"

    def __init__(self, root: str | Path) -> None:
        super().__init__(root)
        self._git = shutil.which("git")
        if self._git is None:
            raise RepositoryEvidenceError("the git executable is not on PATH")
        top = self._run(["rev-parse", "--show-toplevel"], fail=NotAWorkTreeError)
        inside = self._run(
            ["rev-parse", "--is-inside-work-tree"], fail=NotAWorkTreeError
        )
        if inside.strip() != "true":
            raise NotAWorkTreeError(f"{root} is not inside a Git working tree")
        if Path(top.strip()).resolve() != self.root.resolve():
            raise NotAWorkTreeError(
                f"{root} is inside the Git working tree at {top.strip()} but is not "
                "its top level; point the git engine at the top level"
            )

    def _run(
        self,
        args: Sequence[str],
        *,
        ok: tuple[int, ...] = (0,),
        fail: type[RepositoryEvidenceError] = RepositoryEvidenceError,
        stdin: str = "",
    ) -> str:
        assert self._git is not None
        env = {
            **os.environ,
            "LC_ALL": "C",
            "GIT_LITERAL_PATHSPECS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
        }
        try:
            run = subprocess.run(
                [self._git, *_SETTINGS, *args],
                cwd=self.root,
                input=stdin.encode("utf-8"),
                capture_output=True,
                env=env,
                check=False,
            )
        except OSError as exc:
            raise fail(f"git {args[0]} failed in {self.root}: {exc}") from exc
        if run.returncode not in ok:
            message = run.stderr.decode("utf-8", "replace").strip()
            if fail is NotAWorkTreeError:
                message = f"{self.root} is not a Git working tree: {message}"
            raise fail(f"git {args[0]} failed: {message}")
        return run.stdout.decode("utf-8", "replace")

    # -- files -------------------------------------------------------------

    def files(self) -> tuple[str, ...]:
        listed = self._run(
            ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]
        )
        paths = set()
        for path in listed.split("\0"):
            full = self.root / path
            if path and not full.is_symlink() and full.is_file():
                paths.add(path)
        return tuple(sorted(paths))

    def _listed(self, path: str) -> bool:
        # Git decides what is listed (ignored files are not), so ask it.
        return path in self.files()

    # -- diff --------------------------------------------------------------

    def _base(self) -> tuple[str | None, str]:
        """``HEAD``'s commit (or ``None``) and the tree to diff against."""
        head = self._run(
            ["rev-parse", "--verify", "-q", "HEAD^{commit}"], ok=(0, 1)
        ).strip()
        if head:
            return head, head
        empty = self._run(["hash-object", "-t", "tree", "--stdin"]).strip()
        return None, empty

    def diff(self) -> RepositoryDiff | None:
        base, tree = self._base()
        flags = ["--no-ext-diff", "--no-renames", "--no-color"]
        statuses = self._run(["diff", *flags, "--name-status", "-z", tree]).split("\0")
        status = {
            path: _STATUS.get(code[:1], ChangeStatus.MODIFIED)
            for code, path in zip(statuses[0::2], statuses[1::2], strict=False)
            if path
        }
        counts: dict[str, tuple[int | None, int | None]] = {}
        for entry in self._run(["diff", *flags, "--numstat", "-z", tree]).split("\0"):
            if entry:
                added, removed, path = entry.split("\t", 2)
                counts[path] = (_count(added), _count(removed))
        changes = [
            FileChange(
                path,
                status[path],
                *counts.get(path, (None, None)),
                self._run(["diff", *flags, tree, "--", path]),
            )
            for path in status
        ]
        untracked = self._run(["ls-files", "-z", "--others", "--exclude-standard"])
        for path in filter(None, untracked.split("\0")):
            if (self.root / path).is_symlink():
                continue
            no_index = ["diff", *flags, "--no-index"]
            stat = self._run(
                [*no_index, "--numstat", "--", os.devnull, path], ok=(0, 1)
            )
            added, removed = (
                (stat.split("\t", 2) + ["-", "-"])[:2] if stat else ("0", "0")
            )
            patch = self._run([*no_index, "--", os.devnull, path], ok=(0, 1))
            changes.append(
                FileChange(
                    path, ChangeStatus.UNTRACKED, _count(added), _count(removed), patch
                )
            )
        return RepositoryDiff(base, tuple(sorted(changes, key=lambda c: c.path)))

    # -- history -----------------------------------------------------------

    def history(self, path: str) -> tuple[FileCommit, ...] | None:
        self._known(path)
        if self._base()[0] is None:
            return ()
        fmt = _RECORD + _FIELD.join(("%H", "%cI", "%an", "%s"))
        out = self._run(
            ["log", "--follow", "--no-ext-diff", "--no-color", "-z", "--numstat",
             f"--format={fmt}", "--", path]
        )  # fmt: skip
        commits: list[FileCommit] = []
        for record in filter(None, out.split(_RECORD)):
            header, _, stats = record.partition("\0")
            sha, committed_at, author, subject = header.split(_FIELD, 3)
            added: int | None = None
            removed: int | None = None
            name = path if not commits else commits[-1].path
            tokens = stats.lstrip("\n").split("\0")
            if tokens and tokens[0]:
                a, r, written = tokens[0].split("\t", 2)
                added, removed = _count(a), _count(r)
                # A rename is written "a\tr\t" then the old and new names.
                name = written or (tokens[2] if len(tokens) > 2 else name)
            commits.append(
                FileCommit(sha, committed_at, author, subject, name, added, removed)
            )
        return tuple(commits)
