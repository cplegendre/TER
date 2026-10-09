# L3 Grounded: repository evidence behind the analysis

At L3 TER stops judging a session from its event stream alone and asks the
repository what the code says: which files and symbols exist, which tests
import a module, what changed in the working tree and how each file evolved.
All of it comes through one provider-neutral driven port,
`RepositoryEvidence`, served by interchangeable engines that load as
capabilities.

This page covers every L3 capability: the port and its engines (Python,
TypeScript, JavaScript, Svelte and Vue imports and call edges), each task's
expected change surface with the edits outside it, imports that break the
repository's architecture contracts, [context bundles](#context-bundles),
[evidence usage, outcome value, drift and the evidence graph](#evidence-usage-outcome-value-drift-and-the-evidence-graph),
[advisory model routing](#model-routing-advisory), and
[call edges, more contracts and recorded measures](#call-edges-more-contracts-and-recorded-measures).
All 34 L3 requirements are verified by tests, so **L3 Grounded is met**
(with L0 to L2). The vision points that need real sessions stay `partial`
until they are checked on a judged corpus; what was checked on 9 October
2026, what is still unproven and the road to L4 are in
[strategy.md](strategy.md).

## Requirements

| Id | Requirement | Verified by |
|---|---|---|
| TER-EVD-001 | TER shall obtain repository evidence only through the provider-neutral RepositoryEvidence port. | `tests/contract/test_repository_evidence.py`, `tests/architecture/test_repository_evidence_boundary.py`, the `provider-neutral-evidence` import contract |
| TER-EVD-002 | The lexical repository evidence adapter shall return identical results for identical repository content. | `tests/unit/test_ter4_repository_evidence.py::TestLexicalDeterminism`, `tests/golden/test_repository_evidence_snapshot.py` |
| TER-EVD-003 | When a detector requests repository evidence for a Python source module, the repository evidence adapter shall return every test module whose import statements import that source module. | contract suite, `TestTestsImporting`, `TestSharedRules` |
| TER-EVD-012 | Where the repository evidence adapter supports the language of a source file, the repository evidence adapter shall return the symbols and imports of that file read from its syntax, and the call edges of a Python file. | `TestPythonSyntax`, golden `repository/python-ast.json`; `tests/unit/test_ter4_ecmascript_evidence.py` (`TestImportForms`, `TestWhatIsNotAnImport`, `TestComponents`, `TestDefinitions`, `TestResolution`), golden `repository/syntax.json`, the contract suite's TypeScript/Svelte monorepo |
| TER-EVD-013 | Where the repository is a Git working tree, the Git repository evidence adapter shall return the current diff and the history of each file. | `TestGitWorkTree`, `TestGitDiff`, `TestGitHistory` |
| TER-ARC-007 | TER shall load repository engines through the ter.capabilities plugin registry. | `TestRepositoryEnginesArePlugins` |
| TER-EVD-006 | TER shall compute the expected change surface of each task and report every edit outside it. | `tests/unit/test_ter4_change_surface.py` (`TestChangeSurface`, `TestUnrelatedModification`, `TestSurfaceExpansion`, `TestGroundedFold`), `tests/unit/test_ter4_grounded_cli.py` |
| TER-EVD-007 | When an edit to a Python module adds an import that breaks a declared import-linter forbidden, layers or independence contract, TER shall report an architectural boundary violation. | `TestBoundaryViolation`, `TestContractRules`, `TestImportLinterReader`, `tests/contract/test_architecture_contracts.py`, the `structure_of` contract tests, `tests/unit/test_ter4_grounded_cli.py` |
| TER-EVD-014 | When a detector requests repository evidence for a TypeScript, JavaScript, Svelte or Vue source module, the repository evidence adapter shall return every test module whose imports load that source module. | `TestEcmaScriptTests`, contract `test_tests_of_another_language_are_linked_or_refused`, golden `repository/syntax.json` |
| TER-EVD-015 | When an edit adds a direct import that breaks a declared import-linter protected or acyclic_siblings contract, or a declared dependency-cruiser forbidden path rule, TER shall report an architectural boundary violation. | `tests/unit/test_ter4_contract_kinds.py`, `tests/contract/test_architecture_contracts.py` |
| TER-EVD-016 | Where the repository evidence adapter supports TypeScript, JavaScript, Svelte or Vue source files, the repository evidence adapter shall return the call edges of each such file. | `tests/unit/test_ter4_ecmascript_evidence.py::TestCallEdges`, contract `test_an_engine_that_reads_a_language_returns_its_call_edges`, golden `repository/syntax.json` |
| TER-EXP-002 | TER shall record the TER 3 ratio and the outcome verdict of a session as events of that session, so that every reported metric is recomputed from the recorded events alone. | `tests/equivalence/test_recompute_from_events.py`, `tests/unit/test_ter4_records.py` |
| TER-EVD-017 | TER shall map a session path to a repository path when the path lies under any accepted root of the session, where the accepted roots are the directory most session paths name the repository by, each Claude Code worktree checkout under which a session path names a repository file or directory, the directory such a worktree was made from when a session path under it names a repository file or directory, and each other directory under which at least two distinct session paths name repository files with at least one of them in a subdirectory. | `tests/unit/test_ter4_session_roots.py::TestMoreThanOneCheckout` |
| TER-EVD-018 | When TER matches session paths against roots, TER shall compare Windows drive letters without regard to case and with either path separator, reading a Git-Bash path of the form /x/... as the drive path X:/... only when the session also uses drive-letter paths. | `TestPathSpelling` |
| TER-EVD-019 | If an edited path lies outside every root of the session and under a .claude directory that is not a worktree checkout, then TER shall place the edit as harness state and report no change surface finding for it. | `TestHarnessState` |
| TER-EVD-020 | While no session path names a repository file or lies in a repository directory, TER shall take as the session root the prefix most session paths agree on whose remainder lies under a repository directory at least two levels deep. | `TestNewDirectories` |

Unless named otherwise, the test classes are in
`tests/unit/test_ter4_repository_evidence.py` (engines) or
`tests/unit/test_ter4_change_surface.py` (grounded detectors). Live
analysis does not read the diff (P062), so P051 and P062 stay partial.

**Real data.** TER-EVD-006 and TER-EVD-007 are verified on synthetic
sessions and synthetic repositories committed with Git. On 9 October 2026
the detectors were run on the owner's 52 real sessions, each with the
repository it worked in checked out at its start commit; that run produced
the session-root rules (TER-EVD-017 to TER-EVD-020) and the
[`unrelated_modification` calibration](#calibration-unrelated_modification-9-oct-2026)
(0 of 29 judged findings true). The points they serve (P063 change surface,
P064 expansion, P065 boundary violations, P066 unrelated modifications) stay
`partial` until a judged corpus shows true positives and finding rates.

## The port

```mermaid
flowchart LR
    D["grounded detectors, context bundles,<br/>evidence usage, routing"] --> P["RepositoryEvidence port<br/>ter.ports.driven"]
    P --> L["lexical<br/>files, search, test links"]
    P --> G["git<br/>+ diff, history"]
    P --> A["python-ast<br/>+ symbols, imports, calls"]
    P --> S["syntax<br/>python-ast + TS, JS, Svelte, Vue imports"]
    P --> F["InMemoryRepositoryEvidence<br/>(fake for tests)"]
```

An engine is built for one repository root. Every method returns values from
`ter.domain.repository`, never vendor or IO types:

| Method | Returns | Engines without it |
|---|---|---|
| `files()` | every file, relative to the root, `/`-separated, sorted; no `.git`, `.hg` or `.svn` content, no symbolic links | all have it |
| `text(path)` | a listed file's UTF-8 text | all have it |
| `search(needle, regex=False)` | `TextMatch(path, line, text)` for every line containing `needle`, by path then line; binary and non-UTF-8 files are skipped | all have it |
| `tests_importing(path)` | the test modules whose import statements import the module at `path` | all have it for Python; `syntax` also for TypeScript, JavaScript, Svelte and Vue |
| `structure(path)` | `SourceStructure`: symbols, imports, call edges, or a parse `error` | `None` |
| `structure_of(path, text)` | what `structure(path)` would return if the file held `text`; also for a path the repository does not list (a file a session creates) | `None` |
| `diff()` | `RepositoryDiff`: the working tree against `HEAD` | `None` |
| `history(path)` | `FileCommit`s, newest first | `None` |

A path the repository does not list raises `UnknownPathError`; asking for
the tests of a file in a language the engine has no import rule for raises
`UnsupportedLanguageError`. An engine says what it cannot do by returning
`None`, never by guessing.

Every obligation is a test in `tests/contract/test_repository_evidence.py`,
run against all four engines and the in-memory fake on one synthetic
repository (`tests/contract/repository_fixture.py`), and again on a
TypeScript/Svelte monorepo (`tests/contract/ecmascript_fixture.py`): an
engine that reads a file's structure must resolve its imports to the files
the fixture names and link its tests; one that does not must refuse.

## Engines

Engines are capabilities (ADR 0005), keyed `RepositoryEvidence.<engine>`
and declared both in `ter.bootstrap.capabilities.BUILTIN_CAPABILITIES` and
as `ter.capabilities` entry points in `pyproject.toml`. Callers get one
through the registry:

```python
from ter.bootstrap.capabilities import repository_evidence

engine = repository_evidence("path/to/repo", "syntax")  # default: "lexical"
engine.tests_importing("src/pkg/core.py")
```

An installed package adds an engine by declaring a
`RepositoryEvidence.<name>` entry point whose class takes the repository
root (TER-ARC-007). A class that lacks a port method is refused with a
`CapabilityError` naming it.

### `lexical`: the deterministic baseline

Reads files under the root and answers from their text. Every answer depends
only on paths and bytes, not on where the root is, file times or directory
order, so it is the baseline richer engines are measured against (P055). The
golden snapshot `tests/golden/snapshots/repository/lexical.json` freezes its
answers on the synthetic repository.

Import statements are read by pattern: `import a.b`, `import a as b, c`,
`from a import (b, c)`, backslash continuations, and relative imports
resolved against the importing file's package. Being lexical, it also
counts an import statement written inside a string.

### `python-ast`: Python syntax trees

The lexical engine plus `structure()` for `.py` files, from the standard
library's `ast`:

- **symbols**: classes, functions and methods, qualified within the module
  (`Calc.total`, `Service.fetch.inner`), with first and last line;
- **imports**: one `ImportEdge(module, names, line)` per statement, relative
  imports resolved to absolute module names;
- **call edges**: `CallEdge(caller, callee, line, resolved)` for each call
  whose target is a dotted name, attributed to the innermost enclosing
  definition or `<module>`, with `resolved` set to the absolute name when the
  head of the callee is a name the file imports or defines at top level
  (`plus` imported from `.core` as `add` resolves to `pkg.core.add`). Calls
  on other expressions (`f()()`, `x[0]()`) have no name and are left out.

Its test-to-source links use the syntax tree, so an import inside a string
does not count; a test file that does not parse falls back to the lexical
reading. Files in other languages have no structure.

### `syntax`: Python, TypeScript, JavaScript, Svelte and Vue

The `python-ast` engine extended, file by file, to the languages of a
typical web repository (`ter.adapters.driven.repository.syntax`, reader in
`ecmascript.py`). It is the default for `--repo`. `.py` files are read
exactly as `python-ast` reads them. `.ts`, `.tsx`, `.mts`, `.cts`, `.js`,
`.jsx`, `.mjs`, `.cjs` files and the `<script>` blocks of `.svelte` and
`.vue` components (line numbers counted in the whole file; an HTML comment
cannot hide a script block, and markup is never read) give:

- **imports**: `import x, {a as b} from 'm'`, `import * as ns from 'm'`,
  `import type {T} from 'm'` (a type-only import is still a dependency: the
  importer compiles against it), `export {a} from 'm'`, `export * from 'm'`,
  side-effect `import 'm'`, `require('m')` and `import('m')` with a literal
  specifier. `ImportEdge.module` is the specifier as written and
  `ImportEdge.candidates` the repository paths it may load, in resolution
  order; `resolve_import(edge, files)` picks the first the repository (or
  the session) holds. A computed specifier (`import(name)`, a template with
  `${}`) names no file and is left out;
- **symbols**: `function` and `class` declarations (qualified within
  enclosing definitions), class methods, module-level
  `const f = (...) =>` arrow functions and, for a component whose file stem
  is a name (`CourseCard.svelte`), the component itself;
- **call edges** (TER-EVD-016): see [Call edges, more contracts and recorded measures](#call-edges-more-contracts-and-recorded-measures).
  The file's `module` is its own path.

The reader is a tokenizer, not a parser, and needs no third-party package:
it skips comments, string literals, template literals (but reads the code
inside `${...}`) and regular expression literals (a `/` after a value
divides, anywhere else it starts a regular expression), so `import` in a
comment, a string or a regex is never an import, and `loader.import(x)`,
`{ import: 1 }` and `import.meta` are not imports either. A quote string
ends at its line, so an apostrophe in JSX text cannot swallow the rest of
the file.

**Resolution.** Relative specifiers resolve against the importing file's
directory; then, in order: `$lib` to `src/lib` of the nearest SvelteKit
project (a directory with `svelte.config.*` or a `package.json` that
depends on `@sveltejs/kit`); the `compilerOptions.paths` of the nearest
`tsconfig.json` or `jsconfig.json` (JSON with comments and trailing commas;
`extends` followed within the repository; the longest matching pattern
wins; `paths` resolve against `baseUrl`, else the config's own directory);
a workspace package, by the `name` of any `package.json` outside
`node_modules`, to its declared entries (`exports`, `svelte`, `types`,
`module`, `main`; a `.js` entry also tries the `.ts` it compiles from) then
`src/index.*`, `src/lib/index.*` and `index.*`, and a subpath of it
(`@scope/pkg/sub`, `exports` subpath patterns) to that path in the package;
and finally `baseUrl`. Each resolved path is probed as written, with each
source suffix (`.ts`, `.tsx`, `.d.ts`, `.js`, ..., `.svelte`, `.vue`,
`.json`) and as a directory's `index.*`. Anything else (`react`,
`svelte/store`, `$app/stores`, `node:fs`) is an external package: no
candidates, no edge. Generated files that are not in the repository
(`.svelte-kit/tsconfig.json`, SvelteKit's `./$types`) resolve to nothing.

The project configuration is read once per engine, on the first ECMAScript
question, from the repository's own files; an engine serves one commit.

**Tests.** A TypeScript or JavaScript test module is `*.test.*` or
`*.spec.*`, or any such file under a `__tests__/` or `tests/` directory;
nothing under `node_modules` is a test. `tests_importing(path)` returns the
test modules with an import that resolves to `path`. Links are direct: a
test that imports `./index` is a test of the index file, not of what the
index re-exports (TER-EVD-014).

**On real repositories.** On shallow clones of two public repositories of
the kind TER's real sessions worked in (`tutors-sdk/tutors-mono-repo`, a
SvelteKit pnpm monorepo of 730 source files, and `lgriffin/ESI.ts`, 1,703),
96% and 99.6% of the imports that name a repository path resolved to a
file (1,566 of 1,626 and 4,174 of 4,189). Every miss, checked by hand,
names a file the checkout does not hold: SvelteKit's generated `./$types`,
packages whose only entry is a build output, and fixtures whose paths are
deliberately broken. The grounding
precompute takes 1.4 s and 3.1 s there (the Python-only engine on TER's own
repository: 1.9 s).

### `git`: diff and history

The lexical engine over the files Git sees (tracked files still on disk and
untracked files that are not ignored), plus:

- `diff()`: staged, unstaged and untracked changes against `HEAD` (or the
  empty tree before the first commit, with `base` `None`). Each
  `FileChange` has a status (`added`, `modified`, `deleted`, `untracked`,
  `type-changed`), added and removed line counts (`None` for binary files)
  and the unified patch.
- `history(path)`: the file's commits, newest first, following renames;
  each `FileCommit` has the commit id, ISO 8601 commit time, author name,
  subject, the file's name in that commit and its line counts there.

Only this engine runs `git`, as a subprocess, with the settings that would
make output depend on user configuration pinned (no colour, no external
diff, no rename detection in diffs, unquoted paths, the C locale, literal
pathspecs). It refuses, with `NotAWorkTreeError`, a directory that is not
the top level of a Git working tree (a plain directory, a subdirectory, a
bare repository), so "no Git" is never mistaken for "no changes". The other
engines return no diff or history without error.

## Grounded analysis: change surface and boundaries

`python -m ter explain` and `python -m ter a3` take the repository a
session worked in:

```bash
git worktree add ../shop-at-start <start-commit>   # the commit the session started from
python -m ter explain session.jsonl --repo ../shop-at-start
python -m ter a3 session.jsonl --repo ../shop-at-start --html a3.html
```

`--repo-engine` picks the engine (default `syntax`, which reads the import
graph of Python, TypeScript, JavaScript, Svelte and Vue files; `python-ast`
reads Python only; `lexical` gives the surface without import links). The
repository must be the one at the session's start commit: TER replays the
session's edits on it, so a checkout that already holds them reads as edits
that cannot be replayed. Without `--repo`, `explain` and `a3` are exactly
the L2 commands, and their output is unchanged.

### Computed once, then a fold

`ter.application.ground.ground_session` reads the repository once through
the port and returns a `RepositoryGrounding` (`ter.domain.lean.grounding`),
values only:

- the files, the Python module names and the **import graph** at the start
  commit (which repository files each file imports, and the reverse), from
  the syntax of every Python, TypeScript, JavaScript, Svelte and Vue file
  the engine reads; third-party code (`node_modules`, minified `*.min.*`
  bundles) is not read;
- the **distinctive symbols** each file defines: names a prompt can only
  mean one way, with an inner underscore or an inner capital
  (`tests_importing`, `ExplainSession`; never `run` or `main`);
- the mapping from the session's paths to repository paths, through the
  session's **roots** (`roots` in `explain --json` and in the A3's
  `background.repository`; the text output lists them when there is more
  than one). See [Session roots](#session-roots) below;
- for each edit and write, the file **after** it, replayed on the start
  commit's text (`replay_edit`: `content` replaces; `old_string` must be
  found, else the file's text is unknown from there on), parsed with
  `structure_of`, and the imports the edit **added** (a TypeScript or
  JavaScript import resolves against the start commit's files and the
  files the session created);
- the architecture contracts the repository declares, read through the
  `ArchitectureContracts` port.

The analysis is then given the grounding beside the events
(`explain(..., repository=g)`, `AnalysisEngine.explain(repository=g)`); the
grounded detectors read it like any other index, nothing in the domain does
IO, and batch still equals incremental (`TestGroundedFold`). The grounding
of the TER repository itself (1,000 files) takes under two seconds.

### The expected change surface (TER-EVD-006)

Per task (a prompt and the work up to the next one), built from structure
only, never from token counts or word scores:

1. **Seeds**: the repository files the prompt names, by file name or path
   (`pricing.py`, `src/app/domain/pricing.py`), dotted module name
   (`app.domain.pricing`) or a distinctive symbol the file defines
   (`price_with_tax`). A prompt that names none *inherits* the seeds of the
   last prompt that did when the intent record reads it as a refinement or
   an acknowledgement ("go ahead"); after a changed intent, or before any
   file was named, the seed is the task's **first edited** repository file.
2. **Neighbours**: every repository file a seed imports or is imported by,
   at the start commit or after the task's own edits (a new module a seed
   now imports is a neighbour).
3. **Tests**: every test module that imports a seed or a neighbour.

Every edit of the task is then placed, and every placement is in the
analysis (`change_surfaces` in `explain --json`, one line in the text
output):

| Placement | Meaning | Finding |
|---|---|---|
| `inside` | the file is a seed, a neighbour or a test of the surface | none |
| `expansion` | outside, but it imports or is imported by a file inside (P064) | `surface_expansion` |
| `unrelated` | outside, with no import link to any file inside (P066) | `unrelated_modification` |
| `harness_state` | outside every root and under a `.claude` directory that is not a worktree checkout: the agent's plans, memory and settings (`~/.claude/plans/x.md`) (TER-EVD-019) | none: not judged |
| `outside_repository` | not a path under any root of the session (`/tmp/x.py`) | none: not judged |

A harness-state or outside edit is never a seed, a neighbour or a finding.
A project's own `CLAUDE.md` or `.claude/settings.json` inside the root is a
repository path and placed like any other file.

### Session roots

A session's tools use absolute paths (`/home/me/shop/src/a.py`); the
repository lists paths relative to its root (`src/a.py`). Events carry no
working directory, so the roots come from the paths alone
(`session_roots` in `ter.domain.repository`):

1. **Spelling** (TER-EVD-018). Paths are compared in one spelling:
   backslashes become `/` and a drive letter is upper case (`d:\x` and
   `D:/x` are one path). A Git-Bash path `/d/x` is read as `D:/x` only when
   the session also uses drive-letter paths: on Linux or macOS `/d` is an
   ordinary directory, and a Git-Bash-only session matches its own spelling
   anyway, so the conditional rule loses nothing and never invents a drive.
   Nothing else is case-folded.
2. **Main root.** For each path, the shortest prefix whose remainder is a
   repository file is a candidate; the root is the candidate most paths
   agree on (ties: the shorter, then the first in sort order). When no path
   names a file (a session that only creates files), the same vote runs on
   paths whose directory is a repository directory, and then (TER-EVD-020)
   on paths that lie under a repository directory **at least two levels
   deep** (`src/main/java/...` for a new Java package). One level is not
   enough: a top-level name such as `src` also names directories above
   checkouts (`/home/me/src/proj`), which would make a false root.
3. **Other checkouts** (TER-EVD-017). A Claude Code worktree checkout
   `<dir>/.claude/worktrees/<name>` is a root when the remainder of a path
   under it names a repository file or directory (or lies in one); `<dir>`,
   the checkout the worktree was made from, is a root when a path under it
   (outside its `.claude`) does the same. For paths under no root yet, a
   prefix is a root when at least two distinct paths under it name
   repository files, at least one of them in a subdirectory, so a stray
   `README.md` or `package.json` elsewhere does not make a root.

A path under more than one root (a worktree inside the main checkout) maps
through the deepest. `grounding.root` is the main root, the first of
`grounding.roots`.

**Rejected: a root from the created files' common directory.** A session
whose paths name nothing the start commit holds could take the deepest
common directory of the files it created. That directory lies *inside* the
repository (`/w/p/src/newpkg`), so every path would map to the wrong
repository path; a session with no structural match keeps no root, and its
edits stay `outside_repository`.

### Boundary violations (TER-EVD-007)

`ImportLinterContracts` (`ter.adapters.driven.import_linter`, capability
`ArchitectureContracts.import-linter`) reads the contracts a repository
declares for import-linter, from `.importlinter`, `setup.cfg` or
`pyproject.toml` (this repository's own `[tool.importlinter]` is a real
example and a test fixture). It evaluates `forbidden`, `layers` (with
`containers`, independent `a | b` and non-independent `a : b` siblings) and
`independence` contracts, honouring `ignore_imports` with `*` and `**`
wildcards, and (TER-EVD-015) `protected` and `acyclic_siblings` contracts
and dependency-cruiser path rules; custom contract types are skipped, never
guessed. The reader does
no IO: TER reads the file through `RepositoryEvidence`, at the start commit.
A file that cannot be read is reported (`contract_problem`, and a warning on
stderr) and no contract is checked.

An edit **adds** an import when the replayed file imports a module after the
edit that it did not import before it (`from p import m` imports the
submodule `p.m` when it is a module of the repository, otherwise `p`). Each
added import is checked against every contract for the file's module. Only
direct imports are judged; an import already there at the start commit is
never reported.

### Grounded detectors

They are plugins like the L2 detectors (`ter.domain.lean.surface`,
`GROUNDED_DETECTORS`), each with a countermeasure and a follow-up measure in
the A3, but they join the registry only when the analysis is given
repository evidence, so an L2 analysis lists exactly the L2 detectors.

| Detector | Waste | Kind | Confidence rule |
|---|---|---|---|
| `unrelated_modification` | overproduction | waste | Per task and file outside the surface and not one import link beyond it: 0.60 (uncertain) when the prompt named the seeds and the file's imports were read from its syntax (calibrated on real sessions, see below); 0.65 (uncertain) when the seeds were inherited; 0.55 (uncertain) when the file has no import evidence (a language the engine does not read, did not parse, or a `lexical` engine) or the seed is the task's first edit, or the file is a test module the task created (on a real session, a new test reached the code under test only through the CLI). |
| `surface_expansion` | overproduction | waste | Per task and file one import link beyond the surface: 0.60 (uncertain) when the prompt named the seeds, 0.50 otherwise. Always uncertain: callers often have to change with what they call. |
| `boundary_violation` | defects | risk | Per added import and broken contract: **0.90** when the import is still there after the session's last edit of the file; 0.70 when a later edit could not be replayed; 0.55 (uncertain) when a later edit removed it. |

Countermeasures: keep each task's edits inside its surface (a CLAUDE.md rule
and the list of files to revert or split off); make ripple edits a stated
decision; run `lint-imports` in a PostToolUse hook on `Edit|Write` and name
the broken contracts in CLAUDE.md.

## Shared rules

The pure rules every engine and the fake share live in
`ter.domain.repository`:

- a **test module** is `test_*.py` or `*_test.py` (pytest's default naming;
  `conftest.py` and helpers are not tests), or a TypeScript, JavaScript,
  Svelte or Vue file named `*.test.*` or `*.spec.*` or lying under
  `__tests__/` or `tests/` (`is_test_module`);
- **third-party code** is anything under `node_modules` and minified
  bundles (`*.min.js`, `*.min.mjs`): never a source or a test of the
  repository (`is_vendored`);
- an ECMAScript import **loads** the first of its candidate paths that is a
  file (`resolve_import`);
- a file's **module name** follows parent directories while they hold an
  `__init__.py`; the first directory without one is an import root (`src/`,
  `tests/`, the repository root). Namespace packages are not recognised;
- an import statement **imports** module `M` when it names `M` or a dotted
  name under it (Python imports every parent package first), and
  `from P import m` may import the submodule `P.m`. `pkg.core_extra` is not
  `pkg.core`;
- links are **direct**: a test that imports `pkg.util`, which imports
  `pkg.core`, is a test of `pkg.util` only. Imports made by calling
  `importlib` are not import statements and are never seen.

## Boundaries

- `ter.domain.repository` holds values and pure rules only; no IO.
- The `provider-neutral-evidence` import contract keeps the domain module and
  every engine free of model SDKs, tokenizers, embedders, TER 3 internals and
  external capability stacks (P054). The engines are one package in the
  `independent-adapters` contract.
- `tests/architecture/test_repository_evidence_boundary.py` checks that no
  module outside the engines imports `subprocess`, `ast` or a Git or
  tree-sitter library, so repository evidence can only come through the
  port (TER-EVD-001).

## Where the code lives

| Layer | Module |
|---|---|
| domain | `ter/domain/repository.py`: `TextMatch`, `Symbol`, `ImportEdge`, `CallEdge`, `SourceStructure`, `FileChange`, `RepositoryDiff`, `FileCommit`, errors, shared rules |
| domain | `ter/domain/repository.py` also: `ArchitectureContract`, `Layer`, `ContractViolation`, `contract_violations`, `imported_modules`, `session_roots`, `session_root`, `repository_path`, `canonical_path`, `is_harness_state` |
| domain | `ter/domain/lean/grounding.py`: `RepositoryGrounding`, `EditGrounding`, `replay_edit`; `ter/domain/lean/surface.py`: `ChangeSurface`, `change_surfaces`, the grounded detectors |
| ports | `ter/ports/driven.py`: `RepositoryEvidence`, `ArchitectureContracts` |
| driven adapters | `ter/adapters/driven/repository/`: `lexical.py`, `python_ast.py`, `git.py`, `syntax.py` with the ECMAScript reader `ecmascript.py`; `ter/adapters/driven/import_linter.py`; fakes `InMemoryRepositoryEvidence`, `InMemoryArchitectureContracts` in `ter/adapters/driven/in_memory.py` |
| application | `ter/application/ground.py`: `ground_session`; `ExplainSession(repository=, contracts=)` |
| composition | `ter/bootstrap/capabilities.py`: `repository_evidence(root, engine)`; `ter/bootstrap`: `make_contracts()`, `--repo` wiring |

## Context bundles

Instead of letting the agent read its way to the evidence, TER can hand it
a *context bundle*: the repository evidence selected for the next decision,
inside a token budget, with every fragment's reason recorded. TER then
measures, from what the session went on to do, how much of the bundle was
used and what it lacked (issue #42).

| Id | Requirement | Verified by |
|---|---|---|
| TER-CTX-001 | TER shall build deterministic context bundles that hold only the evidence selected for the next decision. | `tests/unit/test_ter4_context_bundle.py` (`TestSelection`, `TestDeterminism`, `TestBudget`, `TestCli`) |
| TER-EVD-004 | When TER supplies a context fragment to the agent, TER shall append one context.supplied event holding the fragment id, its source and the reason it was selected. | `TestSupplied`, `TestCli::test_bundle_prints_the_bundle_and_records_its_supply` |
| TER-CTX-003 | TER shall report context precision and context recall for each context bundle. | `TestPrecisionRecall`, `TestCli::test_report_prints_the_measures` |
| TER-EVD-005 | When a critical evidence list is given for a session, TER shall report context recall as the share of listed items present in context before the first dependent edit. | `TestCriticalRecall`, `TestCriticalFile` |
| TER-CTX-004 | TER shall report unused context as inventory carrying cost and missing context as defect risk. | `TestInventoryAndRisk` |

The TER 3 context orchestrator (`ter_calculator.fragment_store`,
`context_graph`, `budget_optimizer`, `delta_composer`) was the inspiration;
nothing of it is imported. Selection is rebuilt on the `RepositoryEvidence`
port and the change surface.

### Building a bundle (TER-CTX-001)

Selection follows the [change surface](#the-expected-change-surface-ter-evd-006)
at the start commit (`surface_of` and `files_named` in
`ter.domain.lean.surface`), in this order:

1. **seeds**: the files the prompt names (file name, path, dotted module,
   distinctive symbol). A prompt that names none inherits the seeds of the
   last earlier prompt of the session that named some; with nothing to
   inherit the bundle is empty and marked `insufficient`;
2. **tests of a seed**: test modules importing a seed (or that a seed
   imports);
3. **neighbours**: files a seed imports or is imported by;
4. **tests of a neighbour**.

Within a rank files come in path order; third-party code is never selected.
Nothing outside the surface is ever in a bundle.

**Packing.** Seeds and their tests are supplied whole (`file`) when they
fit the remaining budget, else as their **outline** (imports and
definitions with line ranges, from `structure()`); neighbours and their
tests in their smaller form, usually the outline. What fits in no form is
listed under `omitted` with the tokens its smallest form needs. Packing
walks the ranks in order, so a lower-ranked file never displaces a seed; a
bundle that could not hold every seed in some form, or has no seed, says
`insufficient` instead of passing for complete: sufficiency, not minimum
size (P159).

**Fragments.** Each has an `id` (`ctx-` + a hash of source, form and text),
`source` (repository path), `role`, `form`, `reason` (`named by the prompt`,
`test linked to seed src/app/domain/pricing.py`,
`imports seed src/app/domain/pricing.py`, ...), `tokens` (the tokenizer's
count of the text) and the `text`. The bundle's `tokens` never exceed its
`budget`.

**Determinism.** A bundle is a pure function of the prompt text, budget,
repository content and tokenizer. Ids are content hashes, every list is
sorted, and the JSON (`ter.context-bundle/1`, keys sorted) and Markdown
forms are canonical, so the same inputs give byte-identical bundles
wherever the repository is checked out. The prompt itself is not stored,
only its digest; the build time is not in the bundle.

### Supplying it (TER-EVD-004)

```bash
# the next decision of a live session recorded by `ter hook`:
python -m ter context bundle --event-log ~/.cache/ter/events --session <id> --repo .
# a prompt not yet sent, a transcript, a budget, a file:
python -m ter context bundle session.jsonl --prompt "Fix pricing.py" \
    --repo ../shop-at-start --budget 4000 --out bundle.md
```

`bundle` prints the bundle (Markdown; `--json` for JSON) for the session's
last prompt, or `--prompt`, and appends one `context.supplied` event per
fragment to the event log (`--event-log`, default the hook's log;
`--no-record` appends nothing). The event's text is JSON naming the bundle,
the fragment id, source, reason, role, form and tokens (not the fragment's
text); its parent is the prompt event the bundle answers. Ids derive from
the session, bundle and fragment, so supplying the same bundle twice is a
redelivery the analysis reads once.

`context.supplied` is a lifecycle kind (`ter.event/0.5`): counted, never
scored, no Lean step, so recording a supply changes no L1 or L2 measure.

The bundle reaches the agent through whoever ran the command (a person, a
skill, a slash command). It is **never** written into a hook response:
below L4 TER delivers no intervention (TER-INT-001,
`tests/architecture/test_no_intervention.py`).

### Measuring it (TER-CTX-003, TER-EVD-005, TER-CTX-004)

```bash
python -m ter context report session.jsonl --repo ../shop-at-start \
    [--critical critical.json] [--budget N] [--json]
python -m ter context report --event-log DIR --session <id> --repo .
```

A session whose events hold `context.supplied` events is measured on the
bundles it was supplied. A recorded session without them is measured on
the bundles TER **would have** supplied: one per prompt, built at the start
commit and placed right after the prompt (`simulated` in the JSON; "rebuilt
for each prompt" in the text). That is how evidence-selected context is
compared with what a full-context session actually used (P153).

**Window.** A bundle's window runs from its first `context.supplied` event
to the next bundle, the next task's prompt (the first prompt after a tool
request in the window, so a bundle built ahead of its prompt keeps that
prompt's task), or the end of the session.

| Measure | Rule |
|---|---|
| used fragment | in the window, an edit or write changes its file; or an edit's new text or a shell command names a distinctive symbol its file defines; or a shell command names its path or file name (running its tests). Reading it again is not use. |
| precision | used fragments / fragments (`None` for an empty bundle) |
| needed | repository files (present at the start commit) edited in the window, and test modules a shell command in the window names; with a critical list, the listed items whose first dependent edit is in the window |
| recall | needed items the bundle held / needed (`None` when nothing was needed) |
| unused inventory | tokens of unused fragments, carried from the supply to the end of the session: priced at the first model turn after it at the cache-write rate when that turn shows caching (else input), then each later turn at cache-read (else input), with each turn's model's rates from the `PriceBook` port, as `ter.domain.costing` prices context inventory (P157) |
| missing context (defect risk) | needed items the bundle lacked, each with the edits or test runs that depended on it and the agent's own reads of it in the window (`read_by` empty: the edit ran without that evidence in context) (P158) |

The used rule is deliberately small and local. A general evidence-usage
model (`ter.domain.lean.usage`, TER-EVD-008) is being built separately; the
two should converge on one rule, after which precision reads it.

**Critical evidence list (TER-EVD-005).** A person lists, per session, the
evidence the task cannot do without, as JSON or CSV
(`ter.adapters.driven.critical_evidence`):

```json
{
  "schema": "ter.critical-evidence/1",
  "sessions": {
    "<session id>": [
      "src/shop/pricing.py",
      {"path": "src/shop/tax.py", "symbol": "vat_rate"}
    ]
  }
}
```

```csv
session_id,path,symbol
<session id>,src/shop/pricing.py,
<session id>,src/shop/tax.py,vat_rate
```

Paths are repository paths (`/`-separated; `./` and backslashes are
normalised; absolute paths are refused). For each item, the **first
dependent edit** is the first edit that changes its file, changes a file
that imports it (at the start commit or after the edit), or whose new text
names its symbol. The item was **in context** when a bundle supplied its
file or the agent read the file before that edit. Critical recall is the
share of items with a dependent edit that were in context; items no edit
depended on are listed (`not_depended_on`) and left out of the share. A
file in a bundle counts as holding every symbol it defines, outline or not.

**Code.** domain `ter/domain/context_bundle.py` (selection, packing,
`supplied_events`, `read_supplied`) and `ter/domain/context_metrics.py`
(`measure_context`, `CriticalEvidence`); application
`ter/application/context.py` (`ContextBuilder`, `SupplyContext`,
`MeasureContext`); adapters `ter/adapters/driven/critical_evidence.py`,
`ter/adapters/driving/context_cli.py`; composition
`ter/bootstrap/context.py`.

**Limits.** Bundles are built from the start commit's text, so a later
prompt's bundle does not see the session's own edits. Selection is the
import graph and prompt names only: the evidence beyond imports that the
`unrelated_modification` calibration found (issues a prompt names, CI
config, docs) is not selected. Precision and recall are verified on
synthetic sessions only; P153, P155 and P156 stay `partial` until bundles
are compared with real sessions (issue #42).

## Evidence usage, outcome value, drift and the evidence graph

| Id | Requirement | Verified by |
|---|---|---|
| TER-EVD-008 | TER shall report, for each repository read, whether a later decision, edit or test used its content. | `tests/unit/test_ter4_evidence_usage.py::TestEvidenceUsage` |
| TER-GRF-001 | TER shall build an evidence graph that links the intent, observations, decisions, actions, changes and validation events of each session. | `TestEvidenceGraph` |
| TER-LEN-009 | TER shall judge the value of each exploration, reasoning and validation event against the software outcome stated in the current intent, using repository evidence of what the outcome required. | `TestOutcomeValue` |
| TER-ITN-006 | If agent exploration or reasoning departs from the current intent without a recorded intent change, and repository evidence shows it touches nothing the intended change depends on, then TER shall classify the departure as drift. | `TestExplorationDrift` |

All four need `--repo`; without it `explain` and `a3` are unchanged. They
are computed when the analysis is asked for, from the folded steps and the
`RepositoryGrounding`, so batch equals incremental
(`test_the_graph_is_deterministic_and_batch_equals_incremental`). On a real
session of about 1,300 events they add about 0.2 s.

### Evidence usage (TER-EVD-008, points 67 to 70)

`ter.domain.lean.usage`. A **repository read** is a `Read` of a repository
file, or a shell command that only reads (`ShellIntent.EXPLORE`: `cat`,
`head`, `grep`, ...; or a `sed -n` print) naming repository files by path,
relative or absolute under a session root (one read per file; the output's
tokens are shared between them). On a real session most reads were shell
commands, so leaving them out would miss most of the evidence. Each read is
searched forward for a structural use; the first use of each kind is
recorded with its event id:

| Use | Rule | Material |
|---|---|---|
| `edited` | a later edit or write changes the same file | yes |
| `imported_by_edit` | a later edit changes a file that imports the read file (start commit, or after that edit) | yes |
| `named_in_edit` | a later edit's text (not its own path) names the file | yes |
| `named_in_command` | a later shell command that neither checks nor reads names the file | yes |
| `tested` | a later check names the file or a test module that imports it | yes |
| `named_in_decision` | a later reasoning block or response names the file | no |

A file is **named** by its base name, a distinctive stem
(`fragment_store`), its dotted module name, or a distinctive symbol it
defines (from the repository's syntax, or from the read's own text), each
counted only when no other file shares it: `__init__.py`, `index.ts` or a
`run` defined twice name nothing. Reading the file again is not a use.

Status: `used` (any use), `unused` (none, and a response followed the read)
or `pending` (no response yet; a live read is not judged early). `material`
marks reads a change, a command or a check acted on (influential, P069);
unused reads carry their context tokens (P070). The summary gives the share
of judged reads that were used (P068) and the files read against the files
changed (P067). `explain --json`: `evidence_usage`; A3 JSON:
`analysis.evidence_usage`; text: one `evidence usage` line.

**Limits.** An ad-hoc script run (`python - <<EOF`) is read as a check by
the L2 shell reading, so a script that rewrites a file it names shows as
`tested`; it is a material use either way. Changes made by such scripts are
not edits, so "files changed" counts edit and write tools only.

### Outcome value (TER-LEN-009, points 6, 7)

`ter.domain.lean.value`. Per task (a prompt and the work up to the next),
what the outcome **required** is the task's change surface (seeds named by
the prompt, their import neighbours and tests) plus the files the task
edited. Every exploration request, reasoning block and check is judged
against it, with the evidence usage of its reads. Each judgement names its
rule, the files, and the evidence ids (the event and its result, the
prompt, the events that used it). Value classes:

| Class | Meaning |
|---|---|
| `required` | it touched what the outcome required and the change or its check used it |
| `supporting` | it fed the work from off the surface, or touched the surface without being used, or the task changed nothing |
| `no_value` | off the surface and nothing later used it; always uncertain |
| `unjudged` | no repository evidence either way (names no repository file, or no response yet) |

| Rule | Class | Confidence |
|---|---|---|
| `explore.surface_used` | required | 0.85 |
| `explore.off_surface_used` | supporting | 0.75 |
| `explore.surface_unused`, `explore.decision_only`, `explore.no_change_used` | supporting | 0.60 |
| `explore.unused` | no_value | 0.65 |
| `explore.no_change_unused` | no_value | 0.55 |
| `reasoning.surface_then_change` | required | 0.75 |
| `reasoning.surface_no_change`, `reasoning.off_surface_used` | supporting | 0.60 |
| `reasoning.off_surface_unused` | no_value | 0.55 |
| `validation.covers_surface` (after an edit, names a surface file or test) | required | 0.85 |
| `validation.suite_after_change` (after an edit, names no file) | required | 0.75 |
| `validation.off_surface`, `validation.baseline` | supporting | 0.60 |
| `validation.no_new_change` (no edit since the task's previous check) | no_value | 0.55 |
| `validation.no_change` | supporting | 0.55 |
| `*.no_evidence` | unjudged | 0 |

Below 0.70 a judgement is uncertain. Searches and other exploration are
judged by the files their request or output names that a later read or
edit touched. The judgements are a view for the reader: they do not change
activity classes or the scorecard. `explain --json`: `outcome_value` (with
`rules`); A3 JSON: `analysis.outcome_value`; text: one `outcome value` line.

### Exploration drift (TER-ITN-006, point 48)

A grounded detector, `exploration_drift` (`ter.domain.lean.drift`, waste
*motion*), with a countermeasure (keep exploration on what the change
depends on) and a follow-up measure. It joins the registry with the other
grounded detectors only when the analysis has repository evidence
(`EVIDENCE_DETECTORS`), so an L2 analysis lists exactly the L2 detectors.

Within a task that changed something, a read (tool or shell) or a reasoning
block naming repository files is a departure when its intent alignment is
not in the aligned band and **every** file it touches is independent of the
change. A file is **dependent** when it is on the surface, edited by the
task, one import link from the surface, used later by a change, command or
check, or a **test, doc, CI or config file** (`file_role`). The last rule
is the calibration lesson above: import-graph evidence alone gave 0 true
positives in 29 judged `unrelated_modification` findings, because tasks
need tests, docs, CI, config and modules an issue names. A new prompt (a
recorded intent change) starts a new task and surface, so following it is
never drift.

| Case | Confidence |
|---|---|
| read, prompt named the seeds, source file in a different top-level package (`src/app`, `scripts`, `packages/web`) from every seed and edited file, nothing later named it | **0.75** |
| read in the same top-level package, or named later by a decision | 0.60 (uncertain) |
| surface inherited from an earlier prompt | 0.60 (uncertain) |
| surface grown from the task's first edit (the prompt named no file) | 0.50 (uncertain) |
| reasoning naming only independent files | 0.50 (uncertain) |

No finding before a response follows (live), or in a task that changed
nothing. **Real data still needed**: the confident case is a rule, not a
calibration; P048 stays partial until findings on judged real sessions
(D4) show true positives, as `unrelated_modification` was checked.

### The grounded evidence graph (TER-GRF-001, points 71, 72, 77)

`ter.domain.lean.evidence_graph`, schema `ter.evidence-graph/1`. Nodes are
events with a role: `intent` (prompt), `observation` (tool result),
`agent_decision` (reasoning, planning call), `action` (other tool requests),
`change` (edit, write), `validation` (a check) and `response`. Edges point
from the later event to the one it rests on, each type drawn by one rule:

| Edge | Rule |
|---|---|
| `observes` | observation -> the request whose result it is |
| `pursues` | every decision, action, change, validation and response -> the prompt in force |
| `based_on` | decision -> every observation since the previous decision or prompt |
| `decided_by` | action, change or validation -> the latest decision since the prompt |
| `motivated_by` | action, change, validation or response -> the latest observation since the prompt |
| `uses` | a later event -> the read whose content it used (evidence usage) |
| `validates` | validation -> every change since the previous validation |
| `corrects` | change -> the latest failed check result not yet followed by a pass |

Every decision has at least one evidence edge (`pursues`, and `based_on`
when it saw observations: P072). `reconstruct(event)` follows the edges
back and returns the events a change rests on, in session order (P077; no
command prints it yet). Nodes are in session order and edges sorted, so the
JSON is deterministic. `explain --json`: `grounded_evidence_graph` (with the
rules and counts per role and edge type); A3 JSON: `analysis.evidence_graph`.
The L2 graph (`evidence_graph`, `ter.evidence/0.1`) is unchanged.

## Model routing (advisory)

TER can say which model *role* each task of a recorded session should have
run on, and where it should have escalated. At L3 the router is advisory and
offline: it produces decisions and `route.escalated` events for analysis and
the A3's recommendations; it never answers a hook or changes a live session
(TER-INT-001, `tests/architecture/test_no_intervention.py`).

```bash
python -m ter route session.jsonl                       # classes, roles, decisions
python -m ter route session.jsonl --profile local-code --json
python -m ter route session.jsonl --repo ../shop-at-start  # grounded classes
```

| Id | Requirement | Verified by |
|---|---|---|
| TER-RTE-001 | TER shall reference language models only through the role names defined in the active routing profile. | `tests/architecture/test_model_roles.py` (no `ter` module spells a model id in a string literal), `tests/contract/test_routing_profiles.py` (JSON adapter and fake), `tests/unit/test_ter4_routing.py::TestProfiles` (decisions survive a model swap) |
| TER-RTE-005 | TER shall classify each task by complexity, ambiguity, risk, repository scope and validation needs, and record the evidence for each class. | `TestClassification`, `TestGroundedClassification`, `test_cli_route_prints_classes_and_decisions` |
| TER-RTE-002 | When the model router escalates a request, the model router shall append one route.escalated event holding the triggering signal, the source role, the target role, the latency and the token usage. | `TestEscalation` |
| TER-RTE-003 | If no detector signal with evidence supports escalation, then TER shall keep the current execution profile. | `TestKeepTheProfile` |
| TER-DET-011 | When a session records a model escalation after a completed model call, TER shall report the escalation as waiting waste unless the escalated call adds evidence that the earlier call did not. | `tests/unit/test_ter4_unearned_escalation.py` |

Test classes without a file are in `tests/unit/test_ter4_routing.py`.

### Routing profiles are data

A routing profile (`ter.routing_profile/1`, one JSON file each under
`ter/data/routing_profiles/`) names **roles** and what each means, a
provider and a model id; which role each kind of task starts on
(`read_only`, `validate`, `change`); which role each role escalates to; and
`escalate_on`, the detectors whose confident findings may move a task. It
carries a mandatory `source` note, like the price book (ADR 0003). The
`RoutingProfiles` port (`JsonRoutingProfiles`, fake `InMemoryRoutingProfiles`)
serves them; `--profiles DIR` reads another directory. A profile that maps a
task kind or an escalation to a role it does not define, or escalates in a
cycle, is refused.

| Profile | explore | implement | review | escalate |
|---|---|---|---|---|
| `default` | small hosted | mid hosted | mid hosted | large hosted |
| `local-fast` | small local | small local | small local | local coder |
| `local-code` | local coder | local coder | mid hosted | mid hosted |
| `frontier-standard` | mid hosted | mid hosted | mid hosted | large hosted |
| `frontier-deep` | mid hosted | large hosted | large hosted | (none) |

Hosted model ids are price book entries, so every hosted role can be priced
(`test_every_hosted_role_is_priced`). Everything TER decides names roles;
swapping the models behind them changes no decision and no event text.

### Task classes (TER-RTE-005)

A task is a prompt and the steps up to the next prompt (steps before the
first prompt form a task with no prompt). Each task gets a kind and five
classes, each with the events it rests on and its published rule
(`CLASSIFICATION_RULES`). All rules are structural; none reads a token count
(`test_classes_never_depend_on_token_counts`).

| Dimension | Classes | Rule |
|---|---|---|
| kind | read_only, validate, change | edits or writes make a change; otherwise any check makes it validate |
| complexity | low, medium, high | distinct files edited: 0-1 low, 2-3 medium, 4+ high; one level higher when the task's checks fail with 2 or more distinct failure signatures |
| ambiguity | low, medium, high | low when the prompt names a file (path or file name; with `--repo`, a repository file, module or distinctive symbol: the change surface's `named` seeds); medium when it names none but refines or acknowledges the earlier intent, or asks no question; high when it names no file and asks a question, or there is no prompt |
| risk | low, medium, high | high for a build, dependency or CI file, a destructive shell command (`rm -r`, `git reset --hard`, forced push, `drop table`), and with `--repo` an unrelated edit, a broken architecture contract, or an edited file 3 or more repository files import; medium for any other non-test source edit; low otherwise |
| repository scope | none, file, directory, repository | the files edited (or, with no edit, read or searched), as repository paths with `--repo`: one file, one directory, several directories |
| validation needs | none, check, tests | nothing edited; only documentation or configuration (a lint or build check); source edited (tests), citing with `--repo` the change surface's test modules |

### The router (TER-RTE-002, TER-RTE-003)

`ter.domain.routing.route_session` starts each task on the role its profile
gives its kind. It escalates the task, once, to the role the profile names
above that one when a **signal** supports it: a confident finding (0.70 or
more) of a detector in `escalate_on` that cites an event of the task. The
first such finding in session order is the trigger. Without one the task
keeps its role and the decision says so; an uncertain finding, a finding of
another detector, a finding of another task, or a role with nothing above it
never escalates.

Each escalation yields exactly one `route.escalated` event (actor `system`,
id from the session, the task's prompt and the finding, so re-running gives
the same id; its `parent_id` is the triggering step). The events are
numbered after the session's own events. The text holds the record:

```text
escalated implement -> escalate on rework_cycle (finding=rework_cycle:… latency_ms=4000 input=1200 output=300 cache_read=0 cache_write=0)
```

The latency and token usage are what the task had spent on the source role
when the signal fired: the cost an escalation has to earn back (issue #39).
They travel in the text (`RouteEscalation.parse` reads them back) rather than
in the event's `usage`, because usage fields are summed as spend and these
tokens are already counted on the calls they summarise.

### Escalation without new evidence (TER-DET-011)

`route.escalated` is a lifecycle kind (counted, never scored) and, like
`route.failover`, a Lean step at the Respond stage: the session waited on it.
The `unearned_escalation` detector (waiting) reads two kinds of escalation:

- a `route.escalated` step;
- a re-attempt: the first response after an `attempt.started` served by
  another model (`usage.model`) than the response before it. A routing
  harness such as GARE records attempts this way. A failover inside an
  attempt is left to `failed_route`, and two roles of one attempt on
  different models are not an escalation.

It counts only after a completed model call: a response of the same task
before the escalation. The escalated call **adds evidence** when, before the
next prompt or the next escalation, the session gains something no step
before the escalation held: a file read or searched that was not read
before, a check result (command, outcome and failure signature) not seen
before, or another tool output whose fingerprint is new. An edit's own
result is not evidence, and response text is not compared. With no new
evidence the escalation marker and the escalated response are waiting waste:
0.80 for a recorded `route.escalated` with an escalated response; 0.60
(uncertain) for a re-attempt on another model, because a harness's
verification results are not steps and the change may be lateral; 0.50
(uncertain) when no escalated response was recorded. The countermeasure asks
the routing profile to escalate only on evidence and to hand the stronger
model new evidence; the follow-up measure is the count of such findings.

The three GARE fixtures hold no escalation (every attempt ran on one model),
so the detector is verified on synthetic sessions; P030 needs no real data,
while P147, P149 and P150 stay partial until issue #39 records real
escalations, their outcome change, cost and latency.

### Where the routing code lives

| Layer | Module |
|---|---|
| domain | `ter/domain/routing.py`: `RoutingProfile`, `ModelBinding`, `TaskKind`, `classify_tasks`, `TaskClassification`, `ClassEvidence`, `CLASSIFICATION_RULES`, `route_session`, `RouteDecision`, `RoutingPlan`, `RouteEscalation`; `ter/domain/lean/detectors.py`: `UnearnedEscalation` |
| ports | `ter/ports/driven.py`: `RoutingProfiles` |
| driven adapters | `ter/adapters/driven/routing_profiles.py`: `JsonRoutingProfiles`; data `ter/data/routing_profiles/*.json`; fake `InMemoryRoutingProfiles` |
| application | `ter/application/route.py`: `RouteSession` |
| driving | `python -m ter route` (`cli.py`, `route_report.py`) |

Limits: the router reads a recorded session after the fact, so its
escalations are what *should* have happened, not what did; decisions are not
yet shown in the A3 (P146 partial); and the recommendation is not delivered
to a live session before L4 (P138, TER-INT-005).

## Call edges, more contracts and recorded measures

### ECMAScript call edges (TER-EVD-016)

The `syntax` engine's ECMAScript reader returns `CallEdge(caller, callee,
line, resolved)` in the shape Python's syntax tree gives, in source order:

- a **call** is a name or a dotted chain of names followed by an argument
  list: `f(x)`, `api.load()`, `new Course()`, `f?.()`, `writable<T[]>([])`
  and a decorator `@Component({...})`. `callee` is the chain as written,
  with `?.` read as `.`;
- the **caller** is the innermost named definition whose body holds the
  call (`Calc.total`, `outer.inner`, an arrow constant `load`), else
  `<module>`. Callbacks and other anonymous functions belong to the
  definition they are written in; a class field initialiser to the class;
- a call **resolves** when the head of its chain is a name the file binds:
  by `import` (default, named, `* as ns`), `const x = require(...)`,
  `const {a: b} = require(...)` or TypeScript's `import x = require(...)`,
  to `<module>#<exported name>[.<rest>]`, where `<module>` is the repository
  file the import loads (`packages/shared/src/util.ts#formatTitle`) or,
  when it loads none, the specifier as written (`vitest#describe`); or by a
  module-level `function`, `class` or arrow constant, to `<file>#<name>`.
  A definition shadows an import; the first import of a name wins.

Coverage limits: a call on a computed receiver (`f().g()`, `a[0].b()`,
`a!.b()`) is not read; a dynamic call (`obj[name]()`) is not read and
`fn.call(...)` reads as a call of `fn.call`; a method call on a receiver a
tokenizer cannot type (`this.save()`, `course.count()`) has no `resolved`
target; a call through a re-export resolves to the file imported (`$lib` ->
`src/lib/index.ts`), not to the file that defines the name; calls in Svelte
and Vue markup are outside the script code read; and, with no JSX grammar,
a word before parentheses in JSX text, or `a < b > (c)`, reads as a call.

### More architecture contracts (TER-EVD-015)

`boundary_violation` judges, besides import-linter's `forbidden`, `layers`
and `independence` contracts:

| Contract | Source | A new direct import breaks it when |
|---|---|---|
| `protected` | import-linter (`protected_modules`, `allowed_importers`, `as_packages`) | it imports a protected module from outside that module and outside every allowed importer |
| `acyclic_siblings` | import-linter (`ancestors`, `depth`, `skip_descendants`) | it links two children of a covered package (the deepest package both sides share, within an ancestor, at most `depth` levels below it, not under a skipped descendant), the importing child did not depend on the imported one before, and the imported child already reaches the importing one through the children's imports: it closes a cycle. The graph is the start commit's, updated by the session's earlier edits |
| `path_forbidden` | dependency-cruiser `forbidden` rules in `.dependency-cruiser.json` | the importing file's repository path matches the rule's `from.path` (and no `from.pathNot`) and the imported file's matches `to.path` (and no `to.pathNot`); `$1`..`$9` in `to` stand for `from`'s groups. A side with no condition matches every file |

`forbidden` and `protected` read `as_packages` (false: the named modules
alone). Path rules judge only TypeScript, JavaScript, Svelte and Vue imports
(named by the repository file they load) and module contracts only Python
imports, so neither kind judges the other's imports. A dependency-cruiser
rule with any other condition (`circular`, `orphan`, `dependencyTypes`,
`reachable`, ...) or `severity: "off"` is skipped, as are `allowed` rules
and configurations written as JavaScript (`.dependency-cruiser.js`): they
are programs, not data. A regular expression that does not compile makes
the file unreadable. Grounding reads every contract reader
(`ArchitectureContracts.import-linter` and
`ArchitectureContracts.dependency-cruiser`, the first declaring source of
each), so a repository with a Python package and a TypeScript app gets
both; one unreadable source is reported and leaves the others' contracts
standing. The countermeasure still names `lint-imports`; for path rules the
hook command to run is `depcruise`.

### Recorded measures (TER-EXP-002)

The TER 3 ratio and the outcome verdict are the two report figures not
folded from the agent's activity. `RecordMeasures(log)`
(`ter.application.record`) appends an explained session to an `EventLog`:
its own events the log lacks, then a `metric.recorded` event (the ratio,
`{"name": "ter3", "value", "method"}`) and a `verdict.recorded` event (the
run, its source, the acceptance contract and every piece of evidence, with
the verdict as a cross-check), placed after the session's last event. A
record's id is derived from its session, kind and content, so recording
twice appends nothing new.

`explain_recorded(events, tokenizer, prices)` rebuilds the explanation and
the A3 from the log alone: the events enter through `EventIngest`, the fold
reads the recorded ratio (`EventKind.is_record`: no step, count, token
figure or timeline row includes a record), and the verdict is judged again
from what its record holds (a record whose evidence no longer judges to its
verdict is refused). On the golden corpus, with the offline TER 3 scorer
and a JUnit outcome, every figure of the analysis and the A3, the ratio and
the verdict included, equals the original report. Not recorded: the
source's usage limits (they qualify figures) and repository grounding
(evidence about the repository at its start commit, not events).

## Known limits

L3 is met by its requirements; these are the limits of what it reads. The
open real-data questions (precision, context recall, escalation value) are
listed in [strategy.md](strategy.md#what-remains-unproven-on-real-data).

- Test-to-source links and import graphs cover Python, TypeScript,
  JavaScript, Svelte and Vue; other languages have no structure.
- Call edges name what is called and resolve it only through the file's
  imports and module-level definitions: a Python name to its dotted name,
  an ECMAScript name to `<file>#<exported name>`. They do not follow
  re-exports to the defining file, and method calls through `self`, `this`
  or another local receiver stay unresolved.
- TypeScript and JavaScript are read by a tokenizer: a `{` in a function's
  return type (`): { a: string } {`) ends the symbol early, and Vite or
  bundler aliases other than `$lib` and `tsconfig` paths are not read.
- Architecture contracts judge direct imports only; indirect chains,
  wildcard module expressions, custom import-linter types, dependency-cruiser
  rules with conditions other than `path`/`pathNot`, `allowed` rules and
  `.dependency-cruiser.js` configurations are not evaluated.
- Symbol references other than calls are not yet navigable (P057).
- The engines read the repository on every call; nothing is cached, which
  keeps answers current. Checking that a path is listed costs no walk in the
  lexical and syntax-tree engines (each path component is checked
  directly), but `files()`, `search()` and `tests_importing()` walk the
  tree, and the Git engine asks `git` each time.
- Under the `syntax` engine the change surface reads Python, TypeScript,
  JavaScript, Svelte and Vue imports; any other file (CSS, Markdown, JSON)
  is inside it only when the prompt names it, so its findings are uncertain.
- The change surface does not read the issue a prompt names, the files the
  session read before editing, or file roles (test, doc, CI, config), which
  is why every `unrelated_modification` finding is capped below 0.70.
- Live analysis does not read the working-tree diff (P062); grounding uses
  the repository at the start commit.
- Routing and context bundles are advisory and offline: below L4 nothing is
  written into a hook response.

## Checking the grounded detectors on real sessions

`scripts/corpus_grounded.py` runs `explain --repo` over real sessions at the
commit each one started from. It takes a labels file with `session_id`, `repo`,
`cwd` and `commit` columns, exports each repository at that commit with
`git archive` (the repository itself is only read), and writes two files:
counts per repository (placements and grounded findings, safe to share) and a
review CSV with one row per grounded finding (edited path and the task's seed
files, kept on the owner's machine) with an empty `verdict` column to mark
`true` or `false`.

```bash
python scripts/corpus_grounded.py labels-d4.csv ~/.claude/projects \
    --work ~/ter-data/grounded --out grounded.json --review review.csv
```

The counts file (`ter.corpus-grounded/2`) also says, per repository, how
many sessions named it by 0, 1 or 2+ roots (`roots`) and why each
`outside_repository` edit was outside (`outside_reasons`, content-free): a
worktree checkout that was not accepted, a relative path, no session root
(and whether the path lay under the session's working directory), a letter
case difference, a path under the working directory but not under a root,
and, for a path outside the working directory, a Git-Bash drive spelling,
another checkout of the repository (some suffix of the path is a repository
file), a temporary directory (`/tmp/`, `/Temp/`, `AppData/Local/Temp`) or
elsewhere. Harness state has its own placement and is counted under
`placements`.

On the owner's 52 sessions (before TER-EVD-017 to TER-EVD-020), 244 edits
were outside the repository: 86 outside the working directory, 107 with no
session root (26 of them under the working directory), 12 in worktree
checkouts and 39 harness state.

### Calibration: `unrelated_modification` (9 Oct 2026)

With the `syntax` engine and the session-root rules, the owner's 52 sessions
gave 9 confident `unrelated_modification` findings (all in one TypeScript
repository) and 339 uncertain ones. The owner judged all 9 confident findings
and 20 uncertain ones sampled with seed 7, from each session's prompts and
the narration before the edit. **None of the 29 was truly unrelated.** The
edits were tests and fixtures for the task (8), CI workflows and config the
feature needed (6), docs for the change (5), source or scripts the task
required but the prompt did not name, such as modules of the issues it
referred to (7), a test helper needed to pass lint (2), a hook the user asked
to unify (1) and a licence for a new project (1).

The import graph cannot see those links, so the confident case dropped from
0.80 to 0.60: every `unrelated_modification` finding is now uncertain, a
pointer for review that is never counted as waste. Raising it again needs a
judged corpus with true positives, and evidence beyond imports (the issue a
prompt names, files the session read before editing, file roles such as
test, doc, CI and config).

## Session languages and stack

Leigh asked for "a correlation between token usage and delivery mechanism":
does a TypeScript/Svelte session spend tokens differently from a Python one?
Every analysis now records the session's *profile* (no flag needed), and
`scripts/corpus_by_stack.py` compares sessions by it
([l6-stack-comparison.md](l6-stack-comparison.md)).

| Id | Requirement | Verified by |
|---|---|---|
| TER-STK-001 | TER shall record, for each session, the languages of the files the session reads and edits, named from a documented table of file extensions and file names, with each language's read and edit counts, the request events they were counted from, and the session's dominant language. | `tests/unit/test_ter4_stack.py::TestLanguages`, `test_explain_and_a3_carry_the_profile_with_and_without_a_repository`, `test_the_cli_prints_languages_and_the_json_lists_them` |
| TER-STK-002 | Where repository evidence is given for a session, TER shall record the ecosystems, frameworks, build tools, package managers and workspaces that the repository's manifests declare, each fact citing the manifest paths that declare it. | `tests/unit/test_ter4_stack.py::TestManifests`, `test_explain_and_a3_carry_the_profile_with_and_without_a_repository` |

### Languages (no repository needed)

Every `fs.read`, `fs.edit` and `fs.write` request names a path; its language
comes from `ter.domain.stack.LANGUAGE_BY_EXTENSION` (lower-cased extension)
or `LANGUAGE_BY_FILENAME` (`Dockerfile`, `Makefile`, `Gemfile`, ...). The
table, abridged:

| Language | Extensions |
|---|---|
| TypeScript | `.ts` `.tsx` `.mts` `.cts` |
| JavaScript | `.js` `.jsx` `.mjs` `.cjs` |
| Svelte, Vue, Astro | `.svelte`, `.vue`, `.astro` |
| Python | `.py` `.pyi` `.pyx` (`.ipynb` is Jupyter Notebook) |
| Go, Rust, Java, Kotlin | `.go`, `.rs`, `.java`, `.kt` `.kts` |
| C, C++, C#, Swift, Ruby, PHP, ... | `.c` `.h`, `.cpp` `.cc` `.hpp` ..., `.cs`, `.swift`, `.rb`, `.php` |
| Shell, SQL, HCL | `.sh` `.bash` `.zsh`, `.sql`, `.tf` |
| Markup, styling, data, prose | `.html`, `.css` `.scss` `.less`, `.json` `.yaml` `.toml` `.xml`, `.md` `.mdx` `.rst` `.txt` |

Per language the profile keeps edit and read requests, distinct files
edited and read, and the request event ids it counted (results are never
counted twice). Extensions the table does not know are counted apart
(`unrecognised_extensions`) and never pick the dominant language.

The **dominant language** is decided in tiers: the programming language with
the most edits; else the markup/data/prose language with the most edits
(a docs-only session is a Markdown session); else, for a session that edits
nothing, the programming language read most; else the non-code language
read most. Ties go to more reads (or edits), then to the name. The profile is
a summary of the folded steps, so live and batch analysis agree.

### Stack (with `--repo`)

With repository evidence, `ter.application.stack.read_stack` lists the
repository's manifests through the `RepositoryEvidence` port (at most 200,
shallowest first; more are flagged `truncated`), reads each one's text, and
the pure parsers in `ter.domain.stack` turn it into facts:

| Manifest | Facts |
|---|---|
| `package.json` | ecosystem `node`; frameworks from dependencies (`svelte`, `@sveltejs/kit` → `sveltekit`, `react`, `next`, `vue`, `nuxt`, `@angular/core`, `solid-js`, `astro`, `express`, `fastify`, `@nestjs/core`, `electron`, ...); build tools (`vite`, `webpack`, `turbo`, `nx`); `typescript`; `packageManager`; `workspaces` (`npm`/`yarn`/`bun workspaces`, or `package.json workspaces` when no manager is declared) |
| `pnpm-workspace.yaml` | `pnpm`, `pnpm workspaces` when it lists packages |
| lock files | `pnpm-lock.yaml`, `yarn.lock`, `package-lock.json`, `bun.lock(b)`, `poetry.lock`, `uv.lock`, `Pipfile`, `Cargo.lock`, `go.sum` → package manager |
| `pyproject.toml` | ecosystem `python`; build backend (`hatch`, `poetry`, `setuptools`, `flit`, `pdm`, `maturin`, `uv`); `[tool.poetry]`, `[tool.uv]`, `uv workspace`; frameworks from dependencies (`django`, `flask`, `fastapi`, `starlette`, `streamlit`, `torch` → `pytorch`, `numpy`, `pandas`, ...) |
| `requirements*.txt` | ecosystem `python`; frameworks as above |
| `go.mod`, `go.work` | ecosystem `go`; `gin`, `echo`, `fiber`, `chi`, `gorilla-mux`, `grpc`, `cobra`; `go workspace` |
| `Cargo.toml` | ecosystem `rust`; `cargo workspace`; `tokio`, `axum`, `actix-web`, `rocket`, `warp`, `bevy`, `tauri`, `leptos` |
| `pom.xml`, `build.gradle(.kts)`, `settings.gradle(.kts)` | ecosystem `jvm`; `maven` / `gradle`; `spring-boot`, `quarkus`, `micronaut`, `android`, `kotlin`; `maven multi-module`, `gradle multi-project` |
| `turbo.json`, `nx.json`, `lerna.json`, `tsconfig.json`, `setup.py`, `setup.cfg` | the tool they name |

Manifests under `node_modules`, `vendor`, `.venv`, `dist`, `build`, `target`,
`.svelte-kit`, `.next` and similar directories describe dependencies or build
output, not the repository, and are skipped. A manifest that does not parse
still declares its ecosystem. Facts from several manifests (a monorepo with
many `package.json`) merge into one fact per kind and name that cites every
manifest declaring it. The stack *label* used to group sessions is the
frameworks joined by `+` (`svelte+sveltekit`), else the ecosystems, `none`
when the manifests declare neither, and `unknown` without repository
evidence.

### Where it shows

- `explain --json` and the lean JSON: `profile` (languages with evidence,
  `dominant_language`, `dominant_basis`, `unrecognised_extensions`, `stack`
  with facts, manifests and label, or `null` without `--repo`);
- the A3 JSON: `background.profile`;
- `explain` and `a3` text: a `languages` line and, with `--repo`, a `stack`
  line.

Code: `ter/domain/stack.py` (tables, `languages_of`, manifest parsers,
`merge_facts`, `stack_label`), `ter/domain/lean/analysis.py`
(`session_profile`, `LeanAnalysis.profile`), `ter/application/stack.py`
(`read_stack`), joined in `ExplainSession` after the fold.

**Limits.** Languages follow file names, not content: a `.h` file is C even
in a C++ project, and generated files count like hand-written ones. Stack
facts are what the manifests *declare*, not what the session touched; in a
monorepo the label covers every package, not the one the session edited.
