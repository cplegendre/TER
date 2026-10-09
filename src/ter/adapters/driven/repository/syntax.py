"""The multi-language syntax engine: Python syntax trees plus ECMAScript
import evidence (TypeScript, JavaScript, Svelte, Vue).

``syntax`` is the ``python-ast`` engine extended, file by file, to the
languages of a typical web repository:

* ``.py`` files are read exactly as ``python-ast`` reads them;
* ``.ts``, ``.tsx``, ``.mts``, ``.cts``, ``.js``, ``.jsx``, ``.mjs``,
  ``.cjs`` files and the ``<script>`` blocks of ``.svelte`` and ``.vue``
  components are read by :mod:`.ecmascript`: imports (each with the
  repository files it may load), top-level definitions and class methods.
  The ``module`` of such a file is its repository path.

Import resolution needs the repository's ``tsconfig.json``/``jsconfig.json``
files, SvelteKit projects and workspace ``package.json`` files. They are
read through this engine, once, on the first ECMAScript question, and kept
for the engine's life: an engine serves one repository at one commit (the
grounding reads the start commit once), so a session's own edits to those
files are not followed.

Test-to-source links for an ECMAScript file are the test modules
(``*.test.*``, ``*.spec.*``, files under ``__tests__/`` or ``tests/``)
whose imports resolve to that file (TER-EVD-014). Links are direct: a test
that imports ``./index`` is a test of the ``index`` file, not of what the
index re-exports.
"""

from __future__ import annotations

from ....domain.repository import (
    SourceStructure,
    UnsupportedLanguageError,
    is_ecmascript_source,
    is_python_source,
    is_test_module,
    is_vendored,
    resolve_import,
)
from .ecmascript import LANGUAGE, EcmaScriptProject, ecmascript_structure
from .python_ast import PythonSyntaxEvidence

__all__ = ["SourceSyntaxEvidence"]


class SourceSyntaxEvidence(PythonSyntaxEvidence):
    """A :class:`~ter.ports.driven.RepositoryEvidence` that reads Python,
    TypeScript, JavaScript, Svelte and Vue sources."""

    name = "syntax"

    _project: EcmaScriptProject | None = None

    def project(self) -> EcmaScriptProject:
        """The repository's import resolution settings, read once."""
        if self._project is None:
            self._project = EcmaScriptProject.read(self.files(), self._read)
        return self._project

    def _ecmascript(self, path: str, text: str | None) -> SourceStructure:
        if text is None:
            return SourceStructure(path, LANGUAGE, path, error="not UTF-8 text")
        return ecmascript_structure(
            path, text, self.project().candidates, self.listing()
        )

    def structure(self, path: str) -> SourceStructure | None:
        if not is_ecmascript_source(path):
            return super().structure(path)
        self._known(path)
        return self._ecmascript(path, self._read(path))

    def structure_of(self, path: str, text: str) -> SourceStructure | None:
        if not is_ecmascript_source(path):
            return super().structure_of(path, text)
        return self._ecmascript(path, text)

    def tests_importing(self, path: str) -> tuple[str, ...]:
        if is_python_source(path):
            return super().tests_importing(path)
        self._known(path)
        if not is_ecmascript_source(path):
            raise UnsupportedLanguageError(
                f"{path} is not a Python, TypeScript, JavaScript, Svelte or Vue module"
            )
        files = frozenset(self.files())
        found: list[str] = []
        for candidate in sorted(files):
            if not is_test_module(candidate) or not is_ecmascript_source(candidate):
                continue
            if is_vendored(candidate):
                continue
            structure = self._ecmascript(candidate, self._read(candidate))
            if any(resolve_import(e, files) == path for e in structure.imports):
                found.append(candidate)
        return tuple(found)
