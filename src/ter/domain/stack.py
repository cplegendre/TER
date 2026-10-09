"""The languages and stack of a session (L3, TER-STK-001 and TER-STK-002).

Two kinds of fact, both pure functions of what they are given:

* **Languages** come from the extensions of the files a session reads and
  edits (:data:`LANGUAGE_BY_EXTENSION`, :data:`LANGUAGE_BY_FILENAME`). They
  need no repository: every file tool request names its path. Each language
  cites the request events it was counted from.
* **Stack facts** come from the manifests a repository declares
  (``package.json``, ``pnpm-workspace.yaml``, ``pyproject.toml``,
  ``requirements*.txt``, ``go.mod``, ``Cargo.toml``, ``pom.xml``,
  ``build.gradle``, lock files, ...). The parsers here read manifest *text*;
  the application layer obtains that text through the
  :class:`~ter.ports.driven.RepositoryEvidence` port (TER-EVD-001). Each fact
  cites the manifest paths that declare it.

The dominant language is the one a session's delivery is written in: the
language with the most edit requests, programming languages before markup,
data and prose (:data:`NON_CODE_LANGUAGES`); a session that edits nothing
takes the language it read most, by the same rule. Ties go to more reads,
then to the name, so the choice is deterministic.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from .events import EventId

__all__ = [
    "IGNORED_DIRECTORIES",
    "LANGUAGE_BY_EXTENSION",
    "LANGUAGE_BY_FILENAME",
    "MANIFEST_NAMES",
    "NON_CODE_LANGUAGES",
    "FileTouch",
    "LanguageUse",
    "RepositoryStack",
    "SessionProfile",
    "StackFact",
    "StackKind",
    "extension_of",
    "is_manifest",
    "language_of",
    "languages_of",
    "manifest_facts",
    "merge_facts",
    "stack_label",
]

#: File extension (lower case, with the dot) -> language. Documented in
#: docs/ter4/l3-grounded.md ("Session languages and stack").
LANGUAGE_BY_EXTENSION: Mapping[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".pyx": "Python",
    ".ipynb": "Jupyter Notebook",
    ".ts": "TypeScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".svelte": "Svelte",
    ".vue": "Vue",
    ".astro": "Astro",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".scala": "Scala",
    ".groovy": "Groovy",
    ".gradle": "Groovy",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".fs": "F#",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".hh": "C++",
    ".hxx": "C++",
    ".m": "Objective-C",
    ".swift": "Swift",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hs": "Haskell",
    ".clj": "Clojure",
    ".lua": "Lua",
    ".r": "R",
    ".jl": "Julia",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".tf": "HCL",
    ".proto": "Protocol Buffers",
    ".graphql": "GraphQL",
    ".gql": "GraphQL",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "SCSS",
    ".less": "Less",
    ".md": "Markdown",
    ".mdx": "Markdown",
    ".rst": "reStructuredText",
    ".txt": "Text",
    ".json": "JSON",
    ".jsonc": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".xml": "XML",
    ".ini": "INI",
    ".cfg": "INI",
    ".csv": "CSV",
}

#: File names with no telling extension -> language.
LANGUAGE_BY_FILENAME: Mapping[str, str] = {
    "Dockerfile": "Dockerfile",
    "Containerfile": "Dockerfile",
    "Makefile": "Makefile",
    "GNUmakefile": "Makefile",
    "Jenkinsfile": "Groovy",
    "Gemfile": "Ruby",
    "Rakefile": "Ruby",
}

#: Languages that are markup, styling, data or prose rather than programs: a
#: session's dominant language is a programming language whenever it edited
#: (or, editing nothing, read) one.
NON_CODE_LANGUAGES = frozenset(
    {
        "HTML",
        "CSS",
        "SCSS",
        "Less",
        "Markdown",
        "reStructuredText",
        "Text",
        "JSON",
        "YAML",
        "TOML",
        "XML",
        "INI",
        "CSV",
    }
)


def extension_of(path: str) -> str:
    """The lower-cased extension of ``path`` with its dot, or ``""``."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = PurePosixPath(name).suffix
    return suffix.lower() if suffix and suffix != name else ""


def language_of(path: str) -> str | None:
    """The language of the file at ``path``, from its name; ``None`` when the
    tables do not know it."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if name in LANGUAGE_BY_FILENAME:
        return LANGUAGE_BY_FILENAME[name]
    if name.startswith("Dockerfile."):
        return "Dockerfile"
    return LANGUAGE_BY_EXTENSION.get(extension_of(path))


# ---------------------------------------------------------------------------
# Languages of a session
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileTouch:
    """One file tool request: the event, whether it changes the file, the path."""

    event_id: EventId
    edit: bool
    path: str


@dataclass(frozen=True)
class LanguageUse:
    """How a session used one language: requests and distinct files, with the
    request events they were counted from."""

    language: str
    edits: int
    reads: int
    files_edited: int
    files_read: int
    evidence: tuple[EventId, ...]

    @property
    def code(self) -> bool:
        return self.language not in NON_CODE_LANGUAGES

    def as_dict(self) -> dict[str, object]:
        return {
            "language": self.language,
            "edits": self.edits,
            "reads": self.reads,
            "files_edited": self.files_edited,
            "files_read": self.files_read,
            "evidence": list(self.evidence),
        }


class StackKind(StrEnum):
    """What a stack fact says about the repository."""

    ECOSYSTEM = "ecosystem"
    LANGUAGE = "language"
    FRAMEWORK = "framework"
    BUILD_TOOL = "build_tool"
    PACKAGE_MANAGER = "package_manager"
    WORKSPACE = "workspace"


_KIND_ORDER = {k: i for i, k in enumerate(StackKind)}


@dataclass(frozen=True)
class StackFact:
    """One fact a repository's manifests declare, citing every manifest path
    that declares it."""

    kind: StackKind
    name: str
    sources: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "name": self.name,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class RepositoryStack:
    """The stack a repository declares: facts, the manifests read, and the
    manifests that could not be read as text."""

    facts: tuple[StackFact, ...] = ()
    manifests: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()
    #: True when more manifests were listed than were read.
    truncated: bool = False

    def of_kind(self, kind: StackKind) -> tuple[str, ...]:
        return tuple(f.name for f in self.facts if f.kind is kind)

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "facts": [f.as_dict() for f in self.facts],
            "manifests": list(self.manifests),
            "label": stack_label(self),
        }
        if self.unreadable:
            out["unreadable"] = list(self.unreadable)
        if self.truncated:
            out["truncated"] = True
        return out


@dataclass(frozen=True)
class SessionProfile:
    """The languages a session read and edited and, when repository evidence
    was given, the stack its repository declares (TER-STK-001, TER-STK-002)."""

    languages: tuple[LanguageUse, ...] = ()
    #: The dominant language and what decided it ("edits" or "reads").
    dominant: str | None = None
    dominant_basis: str | None = None
    #: Extensions of files no table knows, with how many requests named them.
    unrecognised: tuple[tuple[str, int], ...] = ()
    #: ``None`` when no repository evidence was given.
    stack: RepositoryStack | None = None

    def language(self, name: str) -> LanguageUse | None:
        return next((u for u in self.languages if u.language == name), None)

    def as_dict(self) -> dict[str, object]:
        return {
            "languages": [u.as_dict() for u in self.languages],
            "dominant_language": self.dominant,
            "dominant_basis": self.dominant_basis,
            "unrecognised_extensions": dict(self.unrecognised),
            "stack": None if self.stack is None else self.stack.as_dict(),
        }


def _rank(use: LanguageUse, by_edits: bool) -> tuple[int, int, str]:
    first, second = (use.edits, use.reads) if by_edits else (use.reads, use.edits)
    return (-first, -second, use.language)


def languages_of(
    touches: Iterable[FileTouch], stack: RepositoryStack | None = None
) -> SessionProfile:
    """The profile of a session's file requests, in session order."""
    edits: dict[str, int] = {}
    reads: dict[str, int] = {}
    edited: dict[str, set[str]] = {}
    read: dict[str, set[str]] = {}
    evidence: dict[str, list[EventId]] = {}
    unknown: dict[str, int] = {}
    for touch in touches:
        language = language_of(touch.path)
        if language is None:
            ext = extension_of(touch.path) or "(none)"
            unknown[ext] = unknown.get(ext, 0) + 1
            continue
        counts, files = (edits, edited) if touch.edit else (reads, read)
        counts[language] = counts.get(language, 0) + 1
        files.setdefault(language, set()).add(touch.path)
        evidence.setdefault(language, []).append(touch.event_id)
    uses = [
        LanguageUse(
            language=name,
            edits=edits.get(name, 0),
            reads=reads.get(name, 0),
            files_edited=len(edited.get(name, ())),
            files_read=len(read.get(name, ())),
            evidence=tuple(ids),
        )
        for name, ids in evidence.items()
    ]
    uses.sort(key=lambda u: _rank(u, True))
    dominant: str | None = None
    basis: str | None = None
    # Tiers: code edited, non-code edited, code read, non-code read.
    for by_edits, code in ((True, True), (True, False), (False, True), (False, False)):
        pool = [
            u for u in uses if u.code is code and (u.edits if by_edits else u.reads) > 0
        ]
        if pool:
            dominant = min(pool, key=lambda u: _rank(u, by_edits)).language
            basis = "edits" if by_edits else "reads"
            break
    return SessionProfile(
        languages=tuple(uses),
        dominant=dominant,
        dominant_basis=basis,
        unrecognised=tuple(sorted(unknown.items())),
        stack=stack,
    )


# ---------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------

#: Directories whose manifests describe dependencies or build output, not the
#: repository's own stack.
IGNORED_DIRECTORIES = frozenset(
    {
        "node_modules",
        "bower_components",
        "vendor",
        "third_party",
        ".venv",
        "venv",
        "env",
        "site-packages",
        "__pycache__",
        "dist",
        "build",
        "out",
        "target",
        ".svelte-kit",
        ".next",
        ".nuxt",
        ".turbo",
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".mypy_cache",
    }
)

_LOCK_FILES: Mapping[str, tuple[str, str]] = {
    "pnpm-lock.yaml": ("node", "pnpm"),
    "yarn.lock": ("node", "yarn"),
    "package-lock.json": ("node", "npm"),
    "npm-shrinkwrap.json": ("node", "npm"),
    "bun.lockb": ("node", "bun"),
    "bun.lock": ("node", "bun"),
    "poetry.lock": ("python", "poetry"),
    "uv.lock": ("python", "uv"),
    "Pipfile": ("python", "pipenv"),
    "Pipfile.lock": ("python", "pipenv"),
    "Cargo.lock": ("rust", "cargo"),
    "go.sum": ("go", "go modules"),
}

_BUILD_FILES: Mapping[str, tuple[str, StackKind, str]] = {
    "turbo.json": ("node", StackKind.BUILD_TOOL, "turborepo"),
    "nx.json": ("node", StackKind.BUILD_TOOL, "nx"),
    "lerna.json": ("node", StackKind.WORKSPACE, "lerna"),
    "tsconfig.json": ("node", StackKind.LANGUAGE, "typescript"),
    "setup.py": ("python", StackKind.BUILD_TOOL, "setuptools"),
    "setup.cfg": ("python", StackKind.BUILD_TOOL, "setuptools"),
    "go.work": ("go", StackKind.WORKSPACE, "go workspace"),
}

#: Manifest file names (``requirements*.txt`` is matched by pattern too).
MANIFEST_NAMES = frozenset(
    {
        "package.json",
        "pnpm-workspace.yaml",
        "pyproject.toml",
        "requirements.txt",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        *_LOCK_FILES,
        *_BUILD_FILES,
    }
)
_REQUIREMENTS = re.compile(r"^requirements[\w.-]*\.txt$")

#: Dependency name -> framework or tool, per ecosystem.
_NODE_PACKAGES: Mapping[str, tuple[StackKind, str]] = {
    "svelte": (StackKind.FRAMEWORK, "svelte"),
    "@sveltejs/kit": (StackKind.FRAMEWORK, "sveltekit"),
    "react": (StackKind.FRAMEWORK, "react"),
    "react-native": (StackKind.FRAMEWORK, "react-native"),
    "next": (StackKind.FRAMEWORK, "next"),
    "@remix-run/react": (StackKind.FRAMEWORK, "remix"),
    "vue": (StackKind.FRAMEWORK, "vue"),
    "nuxt": (StackKind.FRAMEWORK, "nuxt"),
    "@angular/core": (StackKind.FRAMEWORK, "angular"),
    "solid-js": (StackKind.FRAMEWORK, "solid"),
    "preact": (StackKind.FRAMEWORK, "preact"),
    "astro": (StackKind.FRAMEWORK, "astro"),
    "express": (StackKind.FRAMEWORK, "express"),
    "fastify": (StackKind.FRAMEWORK, "fastify"),
    "@nestjs/core": (StackKind.FRAMEWORK, "nestjs"),
    "electron": (StackKind.FRAMEWORK, "electron"),
    "vite": (StackKind.BUILD_TOOL, "vite"),
    "webpack": (StackKind.BUILD_TOOL, "webpack"),
    "turbo": (StackKind.BUILD_TOOL, "turborepo"),
    "nx": (StackKind.BUILD_TOOL, "nx"),
    "typescript": (StackKind.LANGUAGE, "typescript"),
}
_PYTHON_PACKAGES: Mapping[str, str] = {
    "django": "django",
    "flask": "flask",
    "fastapi": "fastapi",
    "starlette": "starlette",
    "tornado": "tornado",
    "aiohttp": "aiohttp",
    "streamlit": "streamlit",
    "gradio": "gradio",
    "torch": "pytorch",
    "tensorflow": "tensorflow",
    "jax": "jax",
    "scikit-learn": "scikit-learn",
    "pandas": "pandas",
    "numpy": "numpy",
}
_PYTHON_BACKENDS: Mapping[str, str] = {
    "poetry": "poetry",
    "hatchling": "hatch",
    "setuptools": "setuptools",
    "flit": "flit",
    "pdm": "pdm",
    "maturin": "maturin",
    "uv_build": "uv",
}
_GO_MODULES: Mapping[str, str] = {
    "github.com/gin-gonic/gin": "gin",
    "github.com/labstack/echo": "echo",
    "github.com/gofiber/fiber": "fiber",
    "github.com/go-chi/chi": "chi",
    "github.com/gorilla/mux": "gorilla-mux",
    "google.golang.org/grpc": "grpc",
    "github.com/spf13/cobra": "cobra",
}
_RUST_CRATES: Mapping[str, str] = {
    "tokio": "tokio",
    "axum": "axum",
    "actix-web": "actix-web",
    "rocket": "rocket",
    "warp": "warp",
    "bevy": "bevy",
    "tauri": "tauri",
    "leptos": "leptos",
}
_JVM_MARKERS: tuple[tuple[str, StackKind, str], ...] = (
    ("org.springframework.boot", StackKind.FRAMEWORK, "spring-boot"),
    ("spring-boot-starter", StackKind.FRAMEWORK, "spring-boot"),
    ("io.quarkus", StackKind.FRAMEWORK, "quarkus"),
    ("io.micronaut", StackKind.FRAMEWORK, "micronaut"),
    ("com.android.application", StackKind.FRAMEWORK, "android"),
    ("com.android.library", StackKind.FRAMEWORK, "android"),
    ("org.jetbrains.kotlin", StackKind.LANGUAGE, "kotlin"),
    ('kotlin("jvm")', StackKind.LANGUAGE, "kotlin"),
    ('kotlin("multiplatform")', StackKind.LANGUAGE, "kotlin"),
)


def is_manifest(path: str) -> bool:
    """Whether ``path`` (repository-relative, ``/``-separated) is a manifest
    of the repository's own stack: a known name outside dependency and build
    output directories."""
    parts = path.split("/")
    if any(part in IGNORED_DIRECTORIES for part in parts[:-1]):
        return False
    name = parts[-1]
    return name in MANIFEST_NAMES or bool(_REQUIREMENTS.match(name))


def _python_name(spec: str) -> str | None:
    """The normalised distribution name of a PEP 508 requirement line."""
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
    return match.group(1).lower().replace("_", "-") if match else None


def _python_deps(names: Iterable[str]) -> list[tuple[StackKind, str]]:
    out: list[tuple[StackKind, str]] = []
    for name in names:
        framework = _PYTHON_PACKAGES.get(name)
        if framework is not None:
            out.append((StackKind.FRAMEWORK, framework))
    return out


def _package_json(text: str) -> list[tuple[StackKind, str]]:
    found: list[tuple[StackKind, str]] = [(StackKind.ECOSYSTEM, "node")]
    try:
        data = json.loads(text)
    except ValueError:
        return found
    if not isinstance(data, dict):
        return found
    names: set[str] = set()
    for key in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
        deps = data.get(key)
        if isinstance(deps, dict):
            names.update(str(n) for n in deps)
    for name in sorted(names):
        if name in _NODE_PACKAGES:
            found.append(_NODE_PACKAGES[name])
    manager: str | None = None
    declared = data.get("packageManager")
    if isinstance(declared, str) and declared:
        manager = declared.split("@", 1)[0].strip().lower() or None
        if manager:
            found.append((StackKind.PACKAGE_MANAGER, manager))
    workspaces = data.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    if isinstance(workspaces, list) and workspaces:
        label = f"{manager} workspaces" if manager in ("npm", "yarn", "bun") else None
        found.append((StackKind.WORKSPACE, label or "package.json workspaces"))
    return found


def _pnpm_workspace(text: str) -> list[tuple[StackKind, str]]:
    found: list[tuple[StackKind, str]] = [
        (StackKind.ECOSYSTEM, "node"),
        (StackKind.PACKAGE_MANAGER, "pnpm"),
    ]
    if re.search(r"^packages\s*:", text, re.MULTILINE):
        found.append((StackKind.WORKSPACE, "pnpm workspaces"))
    return found


def _toml(text: str) -> dict[str, object] | None:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def _table(data: Mapping[str, object], *keys: str) -> Mapping[str, object]:
    current: object = data
    for key in keys:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key)
    return current if isinstance(current, Mapping) else {}


def _pyproject(text: str) -> list[tuple[StackKind, str]]:
    found: list[tuple[StackKind, str]] = [(StackKind.ECOSYSTEM, "python")]
    data = _toml(text)
    if data is None:
        return found
    backend = _table(data, "build-system").get("build-backend")
    if isinstance(backend, str):
        head = backend.split(".", 1)[0].removesuffix("_core").removesuffix("-core")
        if head in _PYTHON_BACKENDS:
            found.append((StackKind.BUILD_TOOL, _PYTHON_BACKENDS[head]))
    tool = _table(data, "tool")
    if "poetry" in tool:
        found.append((StackKind.PACKAGE_MANAGER, "poetry"))
    if "uv" in tool:
        found.append((StackKind.PACKAGE_MANAGER, "uv"))
        if _table(tool, "uv", "workspace"):
            found.append((StackKind.WORKSPACE, "uv workspace"))
    specs: list[str] = []
    project = _table(data, "project")
    deps = project.get("dependencies")
    if isinstance(deps, list):
        specs.extend(str(d) for d in deps)
    for group in (
        _table(data, "project", "optional-dependencies"),
        _table(data, "dependency-groups"),
    ):
        for values in group.values():
            if isinstance(values, list):
                specs.extend(str(d) for d in values if isinstance(d, str))
    names = [n for n in (_python_name(s) for s in specs) if n]
    names.extend(
        str(n).lower().replace("_", "-")
        for n in _table(data, "tool", "poetry", "dependencies")
    )
    found.extend(_python_deps(sorted(set(names))))
    return found


def _requirements(text: str) -> list[tuple[StackKind, str]]:
    names = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = _python_name(line)
        if name:
            names.add(name)
    return [(StackKind.ECOSYSTEM, "python"), *_python_deps(sorted(names))]


def _go_mod(text: str) -> list[tuple[StackKind, str]]:
    found: list[tuple[StackKind, str]] = [(StackKind.ECOSYSTEM, "go")]
    modules = re.findall(
        r"^\s*(?:require\s+)?([\w.-]+\.[\w.-]+/[\w./-]+)\s+v", text, re.MULTILINE
    )
    seen: set[str] = set()
    for module in modules:
        for prefix, framework in _GO_MODULES.items():
            if (
                module == prefix or module.startswith(prefix + "/")
            ) and framework not in seen:
                seen.add(framework)
                found.append((StackKind.FRAMEWORK, framework))
    return found


def _cargo(text: str) -> list[tuple[StackKind, str]]:
    found: list[tuple[StackKind, str]] = [(StackKind.ECOSYSTEM, "rust")]
    data = _toml(text)
    if data is None:
        return found
    if "workspace" in data:
        found.append((StackKind.WORKSPACE, "cargo workspace"))
    names: set[str] = set()
    for keys in (
        ("dependencies",),
        ("dev-dependencies",),
        ("workspace", "dependencies"),
    ):
        names.update(str(n) for n in _table(data, *keys))
    for name in sorted(names):
        if name in _RUST_CRATES:
            found.append((StackKind.FRAMEWORK, _RUST_CRATES[name]))
    return found


def _jvm(text: str) -> list[tuple[StackKind, str]]:
    return [(kind, name) for marker, kind, name in _JVM_MARKERS if marker in text]


def _pom(text: str) -> list[tuple[StackKind, str]]:
    found = [(StackKind.ECOSYSTEM, "jvm"), (StackKind.BUILD_TOOL, "maven"), *_jvm(text)]
    if re.search(r"<modules>\s*<module>", text):
        found.append((StackKind.WORKSPACE, "maven multi-module"))
    return found


def _gradle(text: str) -> list[tuple[StackKind, str]]:
    return [(StackKind.ECOSYSTEM, "jvm"), (StackKind.BUILD_TOOL, "gradle"), *_jvm(text)]


def _gradle_settings(text: str) -> list[tuple[StackKind, str]]:
    found = [(StackKind.ECOSYSTEM, "jvm"), (StackKind.BUILD_TOOL, "gradle")]
    if re.search(r"^\s*include\b", text, re.MULTILINE):
        found.append((StackKind.WORKSPACE, "gradle multi-project"))
    return found


def manifest_facts(path: str, text: str) -> tuple[StackFact, ...]:
    """The stack facts one manifest declares, each citing ``path``. A manifest
    that does not parse still declares its ecosystem."""
    name = path.rsplit("/", 1)[-1]
    found: list[tuple[StackKind, str]]
    if name == "package.json":
        found = _package_json(text)
    elif name == "pnpm-workspace.yaml":
        found = _pnpm_workspace(text)
    elif name == "pyproject.toml":
        found = _pyproject(text)
    elif _REQUIREMENTS.match(name):
        found = _requirements(text)
    elif name == "go.mod":
        found = _go_mod(text)
    elif name == "Cargo.toml":
        found = _cargo(text)
    elif name == "pom.xml":
        found = _pom(text)
    elif name in ("build.gradle", "build.gradle.kts"):
        found = _gradle(text)
    elif name in ("settings.gradle", "settings.gradle.kts"):
        found = _gradle_settings(text)
    elif name in _LOCK_FILES:
        ecosystem, manager = _LOCK_FILES[name]
        found = [(StackKind.ECOSYSTEM, ecosystem), (StackKind.PACKAGE_MANAGER, manager)]
    elif name in _BUILD_FILES:
        ecosystem, kind, tool_name = _BUILD_FILES[name]
        found = [(StackKind.ECOSYSTEM, ecosystem), (kind, tool_name)]
    else:
        found = []
    unique = dict.fromkeys(found)
    return tuple(StackFact(kind, value, (path,)) for kind, value in unique)


def merge_facts(facts: Iterable[StackFact]) -> tuple[StackFact, ...]:
    """One fact per (kind, name), citing every manifest that declares it,
    ordered by kind then name."""
    sources: dict[tuple[StackKind, str], set[str]] = {}
    for fact in facts:
        sources.setdefault((fact.kind, fact.name), set()).update(fact.sources)
    return tuple(
        StackFact(kind, name, tuple(sorted(paths)))
        for (kind, name), paths in sorted(
            sources.items(), key=lambda item: (_KIND_ORDER[item[0][0]], item[0][1])
        )
    )


def stack_label(stack: RepositoryStack | None) -> str:
    """A short name for a stack, to group sessions by: its frameworks joined
    by ``+``; without frameworks, its ecosystems; ``unknown`` without
    repository evidence and ``none`` when the manifests declare nothing."""
    if stack is None:
        return "unknown"
    for kind in (StackKind.FRAMEWORK, StackKind.ECOSYSTEM):
        names = stack.of_kind(kind)
        if names:
            return "+".join(sorted(names))
    return "none"
