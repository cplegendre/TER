"""ECMAScript import evidence: TypeScript, JavaScript, Svelte and Vue.

Pure functions (no IO) that read a module's imports and top-level
definitions, and resolve each import specifier to the repository files it
may load. The ``syntax`` engine (:mod:`.syntax`) serves them through the
``RepositoryEvidence`` port.

**Reading.** A tokenizer, not a full parser: it skips comments, string and
template literals (but reads the code inside ``${...}``) and regular
expression literals, so ``import`` written in a comment or a string is never
an import. From the tokens it reads

* ``import x, {a as b} from 'm'``, ``import * as ns from 'm'``,
  ``import type {T} from 'm'`` (a type-only import is still a dependency:
  the importer compiles against the imported file);
* ``export {a} from 'm'``, ``export * from 'm'``, ``export * as ns from 'm'``;
* ``import 'm'`` (side effect only);
* ``require('m')`` and ``import('m')`` with a literal specifier. A computed
  specifier (``import(name)``, a template with ``${}``) names no file and is
  left out.

Svelte and Vue components are read from their ``<script>`` blocks, with
line numbers counted in the whole file. Symbols are the definitions a
prompt can name: ``function`` and ``class`` declarations (qualified within
enclosing functions and classes), class methods, ``const f = (...) =>``
arrow functions at module level and, for a component, the component itself
(the file's stem, a class spanning the file, when the stem is a name:
``CourseCard.svelte`` but not ``+page.svelte``). Call edges are not read
(``calls`` is empty; TER-EVD-016).

**Resolving.** :class:`EcmaScriptProject` holds what resolution needs from
the repository, read once: the ``compilerOptions.paths`` and ``baseUrl`` of
each ``tsconfig.json`` and ``jsconfig.json`` (comments and trailing commas
allowed, ``extends`` followed within the repository), the SvelteKit
``$lib`` alias of each SvelteKit project (a directory with a
``svelte.config.*`` file, or a ``package.json`` that depends on
``@sveltejs/kit``) and the workspace packages (each ``package.json``
``name`` outside ``node_modules``, with its entry points). A specifier
resolves, in order, as

1. relative (``./x``, ``../x``) to the importing file's directory;
2. ``$lib`` or ``$lib/x`` to ``src/lib`` of the nearest SvelteKit project;
3. a ``paths`` pattern of the nearest ``tsconfig.json``/``jsconfig.json``
   (the longest matching prefix wins; every substitute is a candidate);
4. a workspace package name, or a path inside one (``@scope/pkg/sub``);
5. under ``baseUrl``, when the nearest config sets it;

and anything else is an external package: no candidates, no edge. Each
resolved path is probed the way TypeScript, Node and Vite do: as written,
then with each source suffix, then as a directory's ``index``; a ``.js``
specifier also tries the ``.ts`` file it compiles from.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ....domain.repository import (
    ImportEdge,
    SourceStructure,
    Symbol,
    SymbolKind,
    is_vendored,
)

__all__ = [
    "LANGUAGE",
    "EcmaScriptProject",
    "ecmascript_imports",
    "ecmascript_structure",
    "probe",
    "script_code",
    "strip_json_comments",
    "tokenize",
    "Tokens",
]

LANGUAGE = "ecmascript"

#: Suffixes tried, in order, after a specifier that names no file as written.
PROBE_SUFFIXES = (
    ".ts",
    ".tsx",
    ".d.ts",
    ".mts",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".svelte",
    ".vue",
    ".json",
)
#: ``index`` files tried, in order, when a specifier names a directory.
INDEX_SUFFIXES = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", ".svelte")
#: A compiled suffix written in a specifier, and the sources it compiles from.
_COMPILED = {
    ".js": (".ts", ".tsx"),
    ".jsx": (".tsx",),
    ".mjs": (".mts",),
    ".cjs": (".cts",),
}
_COMPONENTS = frozenset({".svelte", ".vue"})

# -- tokens --------------------------------------------------------------------

#: Token kinds.
NAME, STRING, TEMPLATE, PUNCT, VALUE = "name", "str", "tpl", "punct", "value"

# One match per significant token: whitespace and comments before it are
# skipped inside the expression (possessively, so a comment is never read
# back as a ``/``). The token group is optional: at the end of the code only
# whitespace and comments match.
_TOKEN = re.compile(
    r"""
    (?:\s+|//[^\n]*|/\*(?:[^*]|\*(?!/))*(?:\*/|\Z))*+
    (?:
      (?P<name>[A-Za-z_$ -￿][\w$ -￿]*)
      |(?P<punct>=>|\.\.\.|\?\.|[{}()\[\];,.<>=!+\-*%&|^~?:@#])
      |(?P<str>'(?:[^'\\\n]|\\.)*'?|"(?:[^"\\\n]|\\.)*"?)
      |(?P<num>\d[\w.]*)
      |(?P<slash>/)
      |(?P<tick>`)
      |(?P<other>.)
    )?
    """,
    re.VERBOSE | re.DOTALL,
)
_NEWLINE = re.compile("\n")
_TEMPLATE_CHUNK = re.compile(r"(?:[^`\\$]|\\.|\$(?!\{))*", re.DOTALL)
_REGEX = re.compile(r"/(?:[^/\\\[\n]|\\.|\[(?:[^\]\\\n]|\\.)*\])+/[A-Za-z]*")
#: After these keywords a ``/`` starts a regular expression, not a division.
_BEFORE_EXPRESSION = frozenset(
    {
        "return",
        "typeof",
        "instanceof",
        "in",
        "of",
        "new",
        "delete",
        "void",
        "throw",
        "case",
        "do",
        "else",
        "yield",
        "await",
    }
)


class Tokens:
    """The significant tokens of some code, as parallel lists (one entry per
    token; comments and whitespace dropped, each literal one token).

    ``values`` holds the value of each closed string literal and of each
    template literal without substitutions, by token index.
    """

    __slots__ = ("kinds", "offsets", "texts", "values", "_code", "_newlines")

    def __init__(self, code: str) -> None:
        self.kinds: list[str] = []
        self.texts: list[str] = []
        self.offsets: list[int] = []
        self.values: dict[int, str] = {}
        self._code = code
        self._newlines: list[int] | None = None

    def __len__(self) -> int:
        return len(self.texts)

    def text(self, i: int) -> str:
        """The token's text, or ``""`` past either end."""
        return self.texts[i] if 0 <= i < len(self.texts) else ""

    def literal(self, i: int) -> str | None:
        """The value of the string or template literal at ``i``, if any."""
        return self.values.get(i)

    def line(self, i: int) -> int:
        """The 1-based line the token starts on."""
        if self._newlines is None:  # only files with an import or a definition
            self._newlines = [m.start() for m in _NEWLINE.finditer(self._code)]
        return bisect_right(self._newlines, self.offsets[i]) + 1

    def _add(self, kind: str, text: str, offset: int) -> None:
        self.kinds.append(kind)
        self.texts.append(text)
        self.offsets.append(offset)


def _divides(kind: str | None, text: str) -> bool:
    """Whether a ``/`` after a token of ``kind`` and ``text`` is a division
    (else it starts a regular expression)."""
    if kind is None:
        return False
    if kind == NAME:
        return text not in _BEFORE_EXPRESSION
    if kind == PUNCT:
        return text in (")", "]", "}")
    return True  # after a literal or a number


def tokenize(code: str) -> Tokens:
    """The significant tokens of ``code``."""
    tokens = Tokens(code)
    kinds, texts, offsets, values = (
        tokens.kinds,
        tokens.texts,
        tokens.offsets,
        tokens.values,
    )
    match = _TOKEN.match
    pos = 0
    end = len(code)
    #: Brace depth inside each open template substitution ``${ ... }``.
    substitutions: list[int] = []
    previous: tuple[str | None, str] = (None, "")

    def template(start: int, whole: bool) -> int:
        """Read a template from ``start`` (just after the backtick, or after
        the ``}`` closing a substitution) to its end or next ``${``."""
        chunk = _TEMPLATE_CHUNK.match(code, start)
        stop = chunk.end() if chunk else start
        tokens._add(TEMPLATE, code[start:stop], start)
        if stop < end and code[stop] == "`":
            if whole:
                values[len(texts) - 1] = code[start:stop]
            return stop + 1
        if stop >= end:  # unterminated
            return end
        substitutions.append(0)  # ``${``: code until the matching ``}``
        return stop + 2

    while pos < end:
        m = match(code, pos)
        kind = m.lastgroup if m is not None else None  # always a match
        if m is None or kind is None:
            break
        text = m.group(kind)
        stop = m.end()
        start = stop - len(text)
        if kind == "name":
            kinds.append(NAME)
            texts.append(text)
            offsets.append(start)
            previous = (NAME, text)
        elif kind == "punct":
            if substitutions:
                if text == "{":
                    substitutions[-1] += 1
                elif text == "}":
                    if substitutions[-1] == 0:
                        substitutions.pop()
                        pos = template(stop, False)
                        previous = (
                            (TEMPLATE, "") if code[pos - 1] == "`" else (None, "")
                        )
                        continue
                    substitutions[-1] -= 1
            kinds.append(PUNCT)
            texts.append(text)
            offsets.append(start)
            previous = (PUNCT, text)
        elif kind == "str":
            if len(text) >= 2 and text[-1] == text[0]:
                values[len(texts)] = text[1:-1]
            tokens._add(STRING, text, start)
            previous = (STRING, text)
        elif kind == "tick":
            pos = template(stop, True)
            # After the closing backtick a ``/`` divides; after ``${`` it
            # starts an expression.
            previous = (TEMPLATE, "") if code[pos - 1] == "`" else (None, "")
            continue
        elif kind == "slash":
            regex = None if _divides(*previous) else _REGEX.match(code, start)
            if regex is not None:
                tokens._add(VALUE, regex.group(), start)
                previous = (VALUE, "")
                pos = regex.end()
                continue
            tokens._add(PUNCT, "/", start)
            previous = (PUNCT, "/")
        else:  # a number, or a character no rule names
            tokens._add(VALUE if kind == "num" else PUNCT, text, start)
            previous = (VALUE, text) if kind == "num" else (PUNCT, text)
        pos = stop
    return tokens


# -- Svelte and Vue components ---------------------------------------------------

_SCRIPT = re.compile(
    r"<script\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>(.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def script_code(path: str, text: str) -> str:
    """The code TER reads from a file: the whole text, or for a Svelte or
    Vue component its ``<script>`` blocks, everything else blanked to its
    line breaks so lines keep their numbers."""
    if PurePosixPath(path).suffix not in _COMPONENTS:
        return text
    # An HTML comment cannot hide a script block (blanked, lines kept).
    visible = _HTML_COMMENT.sub(lambda m: "\n" * m.group().count("\n"), text)
    parts: list[str] = []
    last = 0
    for match in _SCRIPT.finditer(visible):
        parts.append("\n" * visible.count("\n", last, match.start(1)) + ";")
        parts.append(match.group(1))
        last = match.end(1)
    return "".join(parts)


# -- imports and definitions -----------------------------------------------------


def _brackets(tokens: Tokens) -> dict[int, int]:
    """Index of each opening bracket -> index of the bracket closing it."""
    pairs: dict[int, int] = {}
    stack: list[tuple[str, int]] = []
    closing = {")": "(", "]": "[", "}": "{"}
    kinds = tokens.kinds
    for i, text in enumerate(tokens.texts):
        if text in _OPENING:
            if kinds[i] == PUNCT:
                stack.append((text, i))
        elif text in closing and kinds[i] == PUNCT:
            # Unbalanced code: close the nearest matching opener, if any.
            for depth in range(len(stack) - 1, -1, -1):
                if stack[depth][0] == closing[text]:
                    pairs[stack[depth][1]] = i
                    del stack[depth:]
                    break
    return pairs


_OPENING = frozenset("([{")


def _bound_names(tokens: Tokens, start: int, stop: int) -> tuple[str, ...]:
    """The names an ``import``/``export`` clause binds, as the module
    exports them: ``default`` for a default import, ``*`` for a namespace,
    the exported name of ``{a as b}`` (``a``)."""
    texts, kinds = tokens.texts, tokens.kinds
    names: list[str] = []
    i = start
    if i < stop and texts[i] == "type" and texts[i + 1] != "from":
        i += 1  # ``import type X``
    while i < stop:
        text = texts[i]
        if text == "*":
            names.append("*")
            i += 3 if i + 1 < stop and texts[i + 1] == "as" else 1
        elif text == "{":
            item_start = True
            i += 1
            while i < stop and texts[i] != "}":
                t = texts[i]
                if t == ",":
                    item_start = True
                elif (
                    item_start and t == "type" and texts[i + 1] not in (",", "}", "as")
                ):
                    pass  # ``{ type T }``
                elif item_start and kinds[i] in (NAME, STRING):
                    names.append(tokens.literal(i) or t)
                    item_start = False
                i += 1
            i += 1
        elif kinds[i] == NAME and text not in ("from", "type"):
            names.append("default")
            i += 1
        else:
            i += 1
    return tuple(names)


_IMPORT_WORDS = frozenset({"import", "export", "require"})


def ecmascript_imports(tokens: Tokens) -> list[tuple[str, tuple[str, ...], int]]:
    """``(specifier, names, line)`` of every import in source order."""
    found: list[tuple[str, tuple[str, ...], int]] = []
    texts, kinds = tokens.texts, tokens.kinds
    text, literal = tokens.text, tokens.literal
    n = len(texts)
    for i in [i for i, t in enumerate(texts) if t in _IMPORT_WORDS]:
        if kinds[i] != NAME or text(i - 1) in (".", "?."):
            continue
        word = texts[i]
        if text(i + 1) == "(":
            if word == "export":
                continue
            spec = literal(i + 2)
            if spec is not None and text(i + 3) in (")", ","):
                found.append((spec, (), tokens.line(i)))
        elif word == "import":
            if i + 1 >= n:
                continue
            spec = literal(i + 1)
            if spec is not None:  # ``import 'side-effect'``
                found.append((spec, (), tokens.line(i)))
            elif kinds[i + 1] == NAME or texts[i + 1] in ("{", "*"):
                j = i + 1
                while j < n and j < i + 400:
                    t = texts[j]
                    if t in (";", "=") or kinds[j] == STRING:
                        break  # ``import x = require(...)`` reads as require
                    if t == "from" and kinds[j] == NAME:
                        spec = literal(j + 1)
                        if spec is not None:
                            names = _bound_names(tokens, i + 1, j)
                            found.append((spec, names, tokens.line(i)))
                            break
                    j += 1
        elif word == "export":
            j = i + 1
            if text(j) == "type" and text(j + 1) in ("{", "*"):
                j += 1
            if text(j) == "*":
                close = j + 3 if text(j + 1) == "as" else j + 1
            elif text(j) == "{":
                close = j
                while close < n and texts[close] != "}":
                    close += 1
                close += 1
            else:
                continue
            if text(close) == "from" and (spec := literal(close + 1)):
                names = _bound_names(tokens, j, close)
                found.append((spec, names, tokens.line(i)))
    return found


_NOT_METHODS = frozenset(
    {"if", "for", "while", "switch", "catch", "return", "function", "super"}
)
_MEMBER_START = frozenset(
    {
        "{",
        "}",
        ";",
        "static",
        "async",
        "get",
        "set",
        "*",
        "public",
        "private",
        "protected",
        "override",
        "readonly",
    }
)
_DEFINING = frozenset({"function", "class", "const", "let", "var", "(", "{", "}"})


def _definitions(tokens: Tokens, pairs: Mapping[int, int]) -> list[Symbol]:
    texts, kinds = tokens.texts, tokens.kinds
    n = len(texts)
    symbols: list[Symbol] = []
    #: Enclosing definitions: (name, index of the closing brace, is a class).
    scope: list[tuple[str, int, bool]] = []
    depth = 0

    def body_after(i: int) -> int | None:
        """The ``{`` opening the body of a definition whose header starts at
        ``i``: after the parameter list, or ``None`` for a declaration
        without a body (``;`` first)."""
        j = i
        while j < n and texts[j] not in ("(", "{", ";"):
            j += 1
        if j < n and texts[j] == "(":
            j = pairs.get(j, j) + 1
            while j < n and texts[j] not in ("{", ";"):
                j = pairs.get(j, j) + 1 if texts[j] in ("(", "[") else j + 1
        return j if j < n and texts[j] == "{" else None

    def add(name: str, kind: SymbolKind, start: int, close: int) -> None:
        qualified = ".".join([*(s for s, _, _ in scope), name])
        symbols.append(Symbol(qualified, kind, tokens.line(start), tokens.line(close)))

    for i, word in enumerate(texts):
        while scope and i > scope[-1][1]:
            scope.pop()
        kind = kinds[i]
        if kind == PUNCT:
            if word == "{":
                depth += 1
            elif word == "}":
                depth -= 1
            continue
        if kind != NAME:
            continue
        in_class = bool(scope) and scope[-1][2]
        if word not in _DEFINING and not in_class:
            continue
        before = texts[i - 1] if i else ""
        if before in (".", "?.", "@"):
            continue
        if word in ("function", "class"):
            j = i + 1
            if j < n and texts[j] == "*":
                j += 1
            name = texts[j] if j < n and kinds[j] == NAME else None
            if word == "class" and name in ("extends", "implements"):
                name = None
            body = body_after(j)
            if name is None or body is None or body not in pairs:
                continue
            what = SymbolKind.CLASS if word == "class" else SymbolKind.FUNCTION
            add(name, what, i, pairs[body])
            scope.append((name, pairs[body], word == "class"))
        elif (
            in_class
            and i + 1 < n
            and texts[i + 1] == "("
            and word not in _NOT_METHODS
            and before in _MEMBER_START
        ):
            body = body_after(i + 1)
            if body is None or body not in pairs:
                continue
            add(word, SymbolKind.METHOD, i, pairs[body])
            scope.append((word, pairs[body], False))
        elif word in ("const", "let", "var") and depth == 0 and not scope:
            _arrow_constant(tokens, pairs, i, body_after, add, scope)
    return symbols


def _arrow_constant(
    tokens: Tokens,
    pairs: Mapping[int, int],
    i: int,
    body_after: Callable[[int], int | None],
    add: Callable[[str, SymbolKind, int, int], None],
    scope: list[tuple[str, int, bool]],
) -> None:
    """``const f = (...) => ...`` or ``const f = function ...`` at ``i``."""
    texts, kinds = tokens.texts, tokens.kinds
    n = len(texts)
    j = i + 1
    if j >= n or kinds[j] != NAME:
        return
    name = texts[j]
    j += 1
    if j < n and texts[j] == ":":  # a type annotation
        while j < n and texts[j] not in ("=", ";"):
            j = pairs.get(j, j) + 1
    if j >= n or texts[j] != "=":
        return
    j += 1
    if j < n and texts[j] == "async":
        j += 1
    body: int | None
    if j < n and texts[j] == "function":
        body = body_after(j + 1)
    elif j + 1 < n and kinds[j] == NAME and texts[j + 1] == "=>":
        body = j + 2
    elif j < n and texts[j] == "(" and j in pairs:
        k = pairs[j] + 1
        while k < n and texts[k] not in ("=>", ";", "{", "="):
            k = pairs.get(k, k) + 1
        if k >= n or texts[k] != "=>":
            return
        body = k + 1
    else:
        return
    if body is None or body >= n:
        return
    close = pairs.get(body) if texts[body] == "{" else None
    if close is None:
        add(name, SymbolKind.FUNCTION, i, _expression_end(tokens, pairs, body))
    else:
        add(name, SymbolKind.FUNCTION, i, close)
        scope.append((name, close, False))


_STATEMENT_STARTS = frozenset(
    {"export", "import", "const", "let", "var", "function", "class"}
)


def _expression_end(tokens: Tokens, pairs: Mapping[int, int], start: int) -> int:
    """The last token of an expression starting at ``start``: up to a ``;``
    or ``,`` at its own level, a closing bracket, or a new line that starts
    a declaration."""
    texts = tokens.texts
    n = len(texts)
    i = start
    last = start
    while i < n:
        t = texts[i]
        if t in (";", ",", ")", "]", "}"):
            break
        if i > start and t in _STATEMENT_STARTS and tokens.line(i) > tokens.line(last):
            break
        last = pairs.get(i, i)
        i = last + 1
    return last


def ecmascript_structure(
    path: str,
    text: str,
    resolve: Callable[[str, str], tuple[str, ...]],
) -> SourceStructure:
    """The import evidence of one TypeScript, JavaScript, Svelte or Vue file
    (pure: no IO). ``resolve(path, specifier)`` gives an import's candidate
    repository paths (:meth:`EcmaScriptProject.candidates`)."""
    tokens = tokenize(script_code(path, text))
    imports = tuple(
        ImportEdge(spec, names, line, resolve(path, spec))
        for spec, names, line in ecmascript_imports(tokens)
    )
    symbols = _definitions(tokens, _brackets(tokens))
    p = PurePosixPath(path)
    component = p.name.split(".")[0]
    if p.suffix in _COMPONENTS and component.isidentifier():
        last = max(1, text.count("\n") + (0 if text.endswith("\n") else 1))
        symbols.insert(0, Symbol(component, SymbolKind.CLASS, 1, last))
    return SourceStructure(
        path,
        LANGUAGE,
        path,
        symbols=tuple(sorted(symbols, key=lambda s: (s.line, s.name))),
        imports=imports,
    )


# -- resolution ------------------------------------------------------------------


def _join(directory: str, relative: str) -> str | None:
    """``relative`` under ``directory`` (``""`` is the root), normalised;
    ``None`` when it climbs above the root."""
    parts = [] if directory in ("", ".") else directory.split("/")
    for part in relative.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _dir(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "" if parent == "." else parent


def probe(base: str) -> tuple[str, ...]:
    """The files a path written in an import may load, in resolution order."""
    if not base:
        return tuple(f"index{s}" for s in INDEX_SUFFIXES)
    suffix = PurePosixPath(base).suffix
    out = [base]
    stem = base[: -len(suffix)] if suffix else base
    out.extend(stem + s for s in _COMPILED.get(suffix, ()))
    out.extend(base + s for s in PROBE_SUFFIXES)
    out.extend(f"{base}/index{s}" for s in INDEX_SUFFIXES)
    return tuple(dict.fromkeys(out))


def strip_json_comments(text: str) -> str:
    """JSON with comments and trailing commas (``tsconfig.json``) as JSON."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _json(text: str) -> dict[str, object] | None:
    try:
        value = json.loads(strip_json_comments(text))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class PathMapping:
    """The ``compilerOptions`` of one ``tsconfig.json``/``jsconfig.json``
    that resolution uses: ``paths`` patterns with their substitutes, each
    already a repository path pattern, and ``baseUrl`` as a directory."""

    paths: tuple[tuple[str, tuple[str, ...]], ...] = ()
    base_url: str | None = None


@dataclass(frozen=True)
class WorkspacePackage:
    name: str
    root: str
    #: Entry files the package declares, as repository paths, in order.
    entries: tuple[str, ...] = ()
    #: ``exports`` subpath patterns: ``./x/*`` -> repository path patterns.
    subpaths: tuple[tuple[str, tuple[str, ...]], ...] = ()


_CONFIGS = ("tsconfig.json", "jsconfig.json")
_SVELTE_CONFIGS = frozenset(
    {"svelte.config.js", "svelte.config.ts", "svelte.config.mjs", "svelte.config.cjs"}
)
_ENTRY_FIELDS = ("svelte", "types", "typings", "module", "main", "browser")
_CONDITIONS = ("svelte", "types", "import", "module", "default", "require", "node")


def _strings(value: object) -> list[str]:
    """Every target in an ``exports`` value (string, conditions, array)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        keys = [k for k in _CONDITIONS if k in value] + [
            k for k in value if k not in _CONDITIONS
        ]
        return [s for k in keys for s in _strings(value[k])]
    return []


def _package(root: str, data: Mapping[str, object]) -> WorkspacePackage | None:
    name = data.get("name")
    if not isinstance(name, str) or not name:
        return None
    entries: list[str] = []
    subpaths: list[tuple[str, tuple[str, ...]]] = []
    exports = data.get("exports")
    if isinstance(exports, dict) and any(str(k).startswith(".") for k in exports):
        for key, value in exports.items():
            targets = tuple(
                t for t in (_join(root, s) for s in _strings(value)) if t is not None
            )
            if key == ".":
                entries.extend(targets)
            elif isinstance(key, str) and key.startswith("./"):
                subpaths.append((key[2:], targets))
    elif exports is not None:
        entries.extend(t for s in _strings(exports) if (t := _join(root, s)))
    for key in _ENTRY_FIELDS:
        value = data.get(key)
        if isinstance(value, str) and (target := _join(root, value)) is not None:
            entries.append(target)
    for fallback in ("src/index", "src/lib/index", "index"):
        if (target := _join(root, fallback)) is not None:
            entries.append(target)
    return WorkspacePackage(name, root, tuple(dict.fromkeys(entries)), tuple(subpaths))


def _match(pattern: str, specifier: str) -> str | None:
    """What ``*`` stands for when ``specifier`` matches ``pattern`` (``""``
    for an exact match), or ``None``."""
    head, star, tail = pattern.partition("*")
    if not star:
        return "" if specifier == pattern else None
    if (
        specifier.startswith(head)
        and specifier.endswith(tail)
        and len(specifier) >= len(head) + len(tail)
    ):
        return specifier[len(head) : len(specifier) - len(tail)]
    return None


def _best(
    patterns: Iterable[tuple[str, tuple[str, ...]]], specifier: str
) -> tuple[str, ...] | None:
    """The substitutes of the pattern that matches ``specifier`` with the
    longest prefix (TypeScript's rule), ``*`` filled in."""
    best: tuple[int, tuple[str, ...]] | None = None
    for pattern, targets in patterns:
        star = _match(pattern, specifier)
        if star is None:
            continue
        rank = len(pattern.partition("*")[0]) + (0 if "*" in pattern else 10_000)
        if best is None or rank > best[0]:
            best = (rank, tuple(t.replace("*", star, 1) for t in targets))
    return None if best is None else best[1]


@dataclass
class EcmaScriptProject:
    """What import resolution needs from a repository, read once."""

    #: Directory -> the path mapping of the config file in it.
    configs: dict[str, PathMapping] = field(default_factory=dict)
    #: Roots of SvelteKit projects (``$lib`` is ``<root>/src/lib``).
    sveltekit: frozenset[str] = frozenset()
    packages: dict[str, WorkspacePackage] = field(default_factory=dict)

    @classmethod
    def read(
        cls, files: Iterable[str], text: Callable[[str], str | None]
    ) -> EcmaScriptProject:
        """The project configuration of a repository whose files are
        ``files``; ``text(path)`` reads one (``None`` when unreadable). Only
        listed files are read: a config that ``extends`` a generated file the
        repository does not hold (``.svelte-kit/tsconfig.json``) inherits
        nothing from it."""
        listed = frozenset(files)

        def read(path: str) -> str | None:
            return text(path) if path in listed else None

        config_files: dict[str, str] = {}
        manifests: dict[str, dict[str, object]] = {}
        sveltekit: set[str] = set()
        for path in sorted(listed):
            if is_vendored(path):
                continue
            name = PurePosixPath(path).name
            directory = _dir(path)
            if name in _CONFIGS:
                # tsconfig.json wins over jsconfig.json in one directory.
                if directory not in config_files or name == "tsconfig.json":
                    config_files[directory] = path
            elif name in _SVELTE_CONFIGS:
                sveltekit.add(directory)
            elif name == "package.json":
                raw = read(path)
                data = _json(raw) if raw is not None else None
                if data is not None:
                    manifests[directory] = data
        for directory, data in manifests.items():
            for key in ("dependencies", "devDependencies"):
                deps = data.get(key)
                if isinstance(deps, dict) and "@sveltejs/kit" in deps:
                    sveltekit.add(directory)
        packages: dict[str, WorkspacePackage] = {}
        for directory in sorted(manifests):
            package = _package(directory, manifests[directory])
            if package is not None:
                packages.setdefault(package.name, package)
        configs = {
            d: _mapping(p, read, frozenset(config_files.values()))
            for d, p in config_files.items()
        }
        return cls(configs, frozenset(sveltekit), packages)

    def _nearest(self, directory: str, roots: Collection[str]) -> str | None:
        d: str | None = directory
        while d is not None:
            if d in roots:
                return d
            d = None if d == "" else _dir(d)
        return None

    def candidates(self, importer: str, specifier: str) -> tuple[str, ...]:
        """The repository files ``specifier``, imported by the file at
        ``importer``, may load, in resolution order; ``()`` for an external
        package."""
        spec = specifier.split("?", 1)[0]
        if not spec or spec.startswith(("/", "node:", "http:", "https:", "data:")):
            return ()
        directory = _dir(importer)
        if spec.startswith(("./", "../")) or spec in (".", ".."):
            base = _join(directory, spec)
            return () if base is None else probe(base)
        if spec == "$lib" or spec.startswith("$lib/"):
            kit = self._nearest(directory, self.sveltekit)
            if kit is not None and (base := _join(kit, "src/lib" + spec[4:])):
                return probe(base)
        bases: list[str] = []
        config = self._nearest(directory, self.configs)
        mapping = self.configs[config] if config is not None else PathMapping()
        mapped = _best(mapping.paths, spec)
        if mapped is not None:
            bases.extend(mapped)
        package = self._package(spec)
        if package is not None:
            pkg, rest = package
            if rest:
                subs = _best(pkg.subpaths, rest)
                bases.extend(subs or ())
                bases.extend(
                    b
                    for prefix in ("", "src/", "src/lib/")
                    if (b := _join(pkg.root, prefix + rest)) is not None
                )
            else:
                return tuple(
                    dict.fromkeys(
                        [*(c for b in bases for c in probe(b)), *_entries(pkg)]
                    )
                )
        if mapping.base_url is not None and (b := _join(mapping.base_url, spec)):
            bases.append(b)
        return tuple(dict.fromkeys(c for b in bases for c in probe(b)))

    def _package(self, spec: str) -> tuple[WorkspacePackage, str] | None:
        parts = spec.split("/")
        count = 2 if spec.startswith("@") else 1
        name = "/".join(parts[:count])
        package = self.packages.get(name)
        if package is None:
            return None
        return package, "/".join(parts[count:])


def _entries(package: WorkspacePackage) -> list[str]:
    """Candidates for a bare workspace package import: each declared entry
    as written (and the source a ``.js`` entry compiles from), then the
    conventional source entries."""
    out: list[str] = []
    for entry in package.entries:
        suffix = PurePosixPath(entry).suffix
        if suffix and suffix in _COMPILED or suffix in PROBE_SUFFIXES:
            out.append(entry)
            stem = entry[: -len(suffix)]
            out.extend(stem + s for s in _COMPILED.get(suffix, ()))
        else:
            out.extend(probe(entry))
    return out


def _mapping(
    path: str,
    text: Callable[[str], str | None],
    configs: frozenset[str],
    seen: frozenset[str] = frozenset(),
) -> PathMapping:
    """The path mapping ``path`` declares, with what it ``extends`` from
    another config file of the repository (its own settings win)."""
    raw = text(path)
    data = _json(raw) if raw is not None else None
    if data is None or path in seen:
        return PathMapping()
    directory = _dir(path)
    inherited = PathMapping()
    extends = data.get("extends")
    parents: list[object] = (
        [extends]
        if isinstance(extends, str)
        else list(extends)
        if isinstance(extends, list)
        else []
    )
    for parent in parents:  # a later parent's settings win (TypeScript 5)
        if not isinstance(parent, str) or not parent.startswith("."):
            continue  # a package's config: not in the repository
        target = _join(directory, parent)
        if target is None:
            continue
        for candidate in (target, target + ".json", f"{target}/tsconfig.json"):
            if candidate in configs or (
                candidate.endswith(".json") and text(candidate)
            ):
                inherited = _mapping(candidate, text, configs, seen | {path})
                break
    options = data.get("compilerOptions")
    options = options if isinstance(options, dict) else {}
    base_url = inherited.base_url
    raw_base = options.get("baseUrl")
    if isinstance(raw_base, str):
        base_url = _join(directory, raw_base)
    raw_paths = options.get("paths")
    if not isinstance(raw_paths, dict):
        return PathMapping(inherited.paths, base_url)
    # ``paths`` resolve against ``baseUrl`` when set, else the config's own
    # directory (TypeScript 4.1+).
    against = base_url if base_url is not None else directory
    paths: list[tuple[str, tuple[str, ...]]] = []
    for pattern, targets in raw_paths.items():
        if not isinstance(targets, list):
            continue
        resolved = tuple(
            t
            for t in (_join(against, s) for s in targets if isinstance(s, str))
            if t is not None
        )
        paths.append((str(pattern), resolved))
    return PathMapping(tuple(paths), base_url)
