"""Repository evidence engines: adapters for the ``RepositoryEvidence`` port.

Four engines, each a capability (``RepositoryEvidence.<engine>``, ADR 0005):

* :class:`LexicalRepositoryEvidence` (``lexical``): the deterministic
  baseline. Files, text search, and test-to-source links read from import
  statements as text (TER-EVD-002, TER-EVD-003).
* :class:`GitRepositoryEvidence` (``git``): the lexical engine over the files
  Git sees, plus the working tree's diff and each file's history, read by
  running ``git`` (TER-EVD-013). Refuses a directory that is not the top
  level of a Git working tree.
* :class:`PythonSyntaxEvidence` (``python-ast``): the lexical engine plus
  symbols, imports and call edges of Python files from the standard
  library's ``ast`` (TER-EVD-012).
* :class:`SourceSyntaxEvidence` (``syntax``): the Python syntax-tree engine
  plus imports, definitions and test-to-source links of TypeScript,
  JavaScript, Svelte and Vue files, resolved to repository files through
  relative paths, ``tsconfig``/``jsconfig`` paths, SvelteKit's ``$lib`` and
  workspace packages (TER-EVD-012, TER-EVD-014). The default for ``--repo``.

They share one package because the richer engines extend the baseline; the
package as a whole is one adapter for the import contracts. No engine imports
a model provider (the ``provider-neutral-evidence`` contract, P054).
"""

from __future__ import annotations

from .git import GitRepositoryEvidence
from .lexical import LexicalRepositoryEvidence
from .python_ast import PythonSyntaxEvidence
from .syntax import SourceSyntaxEvidence

__all__ = [
    "GitRepositoryEvidence",
    "LexicalRepositoryEvidence",
    "PythonSyntaxEvidence",
    "SourceSyntaxEvidence",
]
