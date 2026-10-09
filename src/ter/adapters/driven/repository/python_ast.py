"""The Python syntax-tree engine: the lexical baseline plus ``ast`` evidence.

For every ``.py`` file, :meth:`PythonSyntaxEvidence.structure` returns

* **symbols**: every class, function and method definition, qualified within
  the module (``Calc.total``, ``outer.inner``), with its first and last line;
* **imports**: every import statement, relative imports resolved to absolute
  module names against the file's package;
* **call edges**: every call whose target is a dotted name (``f``,
  ``self.helper``, ``os.path.join``), attributed to the innermost enclosing
  definition (or ``<module>``) and resolved to an absolute name when its head
  is a name the file imports or defines at top level. Calls on other
  expressions (``f()()``, ``x[0]()``) have no name to record and are left
  out.

Test-to-source links read import statements from the syntax tree, so an
import written inside a string does not count; a test file that does not
parse falls back to the lexical reading. Files in other languages have no
structure (``None``) under this engine (TER-EVD-012).
"""

from __future__ import annotations

import ast
import warnings

from ....domain.repository import (
    MODULE_LEVEL,
    CallEdge,
    ImportEdge,
    SourceStructure,
    Symbol,
    SymbolKind,
    is_python_source,
    module_name,
    package_of,
    resolve_relative,
)
from .lexical import LexicalRepositoryEvidence, lexical_imports

__all__ = ["PythonSyntaxEvidence", "python_structure"]

LANGUAGE = "python"

_Def = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head is not None else None
    return None


class _Walker(ast.NodeVisitor):
    def __init__(self, package: str) -> None:
        self.package = package
        self.scope: list[tuple[str, bool]] = []  # (name, is a class)
        self.symbols: list[Symbol] = []
        self.imports: list[ImportEdge] = []
        self.calls: list[tuple[int, int, str, str]] = []  # line, col, caller, callee
        self.bindings: dict[str, str] = {}

    def _qualified(self, name: str) -> str:
        return ".".join([*(s for s, _ in self.scope), name])

    def _caller(self) -> str:
        return ".".join(s for s, _ in self.scope) or MODULE_LEVEL

    def _definition(self, node: _Def, kind: SymbolKind) -> None:
        # Decorators, bases, defaults and annotations run in the enclosing
        # scope; only the body belongs to the definition.
        for child in ast.iter_child_nodes(node):
            if child not in node.body:
                self.visit(child)
        self.symbols.append(
            Symbol(
                self._qualified(node.name),
                kind,
                node.lineno,
                node.end_lineno or node.lineno,
            )
        )
        self.scope.append((node.name, isinstance(node, ast.ClassDef)))
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._definition(node, SymbolKind.CLASS)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        in_class = bool(self.scope) and self.scope[-1][1]
        self._definition(node, SymbolKind.METHOD if in_class else SymbolKind.FUNCTION)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(ImportEdge(alias.name, (), node.lineno))
            if alias.asname:
                self.bindings.setdefault(alias.asname, alias.name)
            else:
                head = alias.name.split(".")[0]
                self.bindings.setdefault(head, head)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = resolve_relative(node.level, node.module, self.package)
        names = tuple(alias.name for alias in node.names)
        self.imports.append(ImportEdge(module, names, node.lineno))
        if module.startswith("."):
            return
        for alias in node.names:
            if alias.name != "*":
                self.bindings.setdefault(
                    alias.asname or alias.name, f"{module}.{alias.name}"
                )

    def visit_Call(self, node: ast.Call) -> None:
        callee = _dotted(node.func)
        if callee is not None:
            self.calls.append((node.lineno, node.col_offset, self._caller(), callee))
        self.generic_visit(node)


def python_structure(path: str, text: str, files: frozenset[str]) -> SourceStructure:
    """The syntax-tree evidence of one Python file (pure: no IO)."""
    module = module_name(path, files)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # e.g. invalid escape sequences
            tree = ast.parse(text, filename=path)
    except (SyntaxError, ValueError) as exc:
        where = f" (line {exc.lineno})" if isinstance(exc, SyntaxError) else ""
        return SourceStructure(
            path, LANGUAGE, module, error=f"{type(exc).__name__}: {exc.args[0]}{where}"
        )
    walker = _Walker(package_of(path, files))
    walker.visit(tree)
    defined = {
        node.name: f"{module}.{node.name}" if module else node.name
        for node in tree.body
        if isinstance(node, _Def)
    }
    known = {**walker.bindings, **defined}

    def resolve(callee: str) -> str | None:
        head, dot, rest = callee.partition(".")
        target = known.get(head)
        return None if target is None else target + dot + rest

    calls = tuple(
        CallEdge(caller, callee, line, resolve(callee))
        for line, _, caller, callee in sorted(walker.calls, key=lambda c: (c[0], c[1]))
    )
    return SourceStructure(
        path,
        LANGUAGE,
        module,
        symbols=tuple(sorted(walker.symbols, key=lambda s: (s.line, s.name))),
        imports=tuple(walker.imports),
        calls=calls,
    )


class PythonSyntaxEvidence(LexicalRepositoryEvidence):
    """A :class:`~ter.ports.driven.RepositoryEvidence` with Python syntax trees."""

    name = "python-ast"

    def structure(self, path: str) -> SourceStructure | None:
        self._known(path)
        if not is_python_source(path):
            return None
        return python_structure(path, self.text(path), frozenset(self.files()))

    def _imports(
        self, path: str, text: str, files: frozenset[str]
    ) -> tuple[ImportEdge, ...]:
        structure = python_structure(path, text, files)
        if structure.error is not None:
            return lexical_imports(text, package_of(path, files))
        return structure.imports
