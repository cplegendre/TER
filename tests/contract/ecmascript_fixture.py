"""A small synthetic TypeScript/Svelte monorepo for the ECMAScript evidence
tests: pnpm-style workspace packages, a SvelteKit app, a ``tsconfig`` base
with ``paths`` (and comments, and trailing commas), and imports in every
form TER reads, next to text that only looks like an import.

The import graph, as the ``syntax`` engine resolves it::

    apps/web/src/routes/+page.svelte -> $lib/api.ts, $lib/Card.svelte
    apps/web/src/routes/+page.ts     -> $lib (src/lib/index.ts)
    apps/web/src/lib/index.ts        -> ./api
    apps/web/src/lib/api.ts          -> @mono/model (packages/model/src/index.ts),
                                        @shared/util (tsconfig paths)
    apps/web/src/lib/Card.svelte     -> ./api (dynamic import)
    packages/model/src/index.ts      -> ./course, ./lab (re-exports)
    packages/model/src/course.ts     -> ./lab (type-only)
    apps/web/src/lib/theme.ts          (imports nothing, imported by nothing)

``node_modules`` holds an installed package, whose files are neither
sources nor tests of the repository.
"""

from __future__ import annotations

ES_REPO: dict[str, str] = {
    "package.json": (
        '{\n  "name": "mono",\n  "private": true,\n'
        '  "workspaces": ["packages/*", "apps/*"]\n}\n'
    ),
    "pnpm-workspace.yaml": "packages:\n  - 'packages/*'\n  - 'apps/*'\n",
    "tsconfig.base.json": (
        "{\n"
        "  // shared settings\n"
        '  "compilerOptions": {\n'
        '    "baseUrl": ".",\n'
        '    "paths": {\n'
        '      "@shared/*": ["packages/shared/src/*"], /* the shared kit */\n'
        "    },\n"
        "  },\n"
        "}\n"
    ),
    "packages/model/package.json": (
        '{\n  "name": "@mono/model",\n  "main": "dist/index.js",\n'
        '  "types": "dist/index.d.ts"\n}\n'
    ),
    "packages/model/src/index.ts": (
        "export * from './course';\nexport { type Lab, loadLab } from \"./lab\";\n"
    ),
    "packages/model/src/course.ts": (
        "import type { Lab } from './lab';\n"
        "\n"
        "export class Course {\n"
        "  labs: Lab[] = [];\n"
        "  count(): number {\n"
        "    return this.labs.length;\n"
        "  }\n"
        "}\n"
    ),
    "packages/model/src/lab.ts": (
        "export interface Lab {\n  title: string;\n}\n"
        "\n"
        "export function loadLab(title: string): Lab {\n  return { title };\n}\n"
    ),
    "packages/model/src/course.test.ts": (
        "import { describe, it, expect } from 'vitest';\n"
        "import { Course } from './course';\n"
        "\n"
        "describe('Course', () => {\n"
        "  it('counts', () => expect(new Course().count()).toBe(0));\n"
        "});\n"
    ),
    "packages/model/src/__tests__/lab.ts": (
        "import { loadLab } from '../lab.js';\n\nloadLab('x');\n"
    ),
    "packages/shared/package.json": '{\n  "name": "@mono/shared"\n}\n',
    "packages/shared/src/util.ts": (
        "// import { nope } from './nope';\n"
        "const sample = \"import { lab } from '../../model/src/lab'\";\n"
        "export const formatTitle = (title: string): string =>\n"
        "  title.trim() + sample.length;\n"
    ),
    "apps/web/package.json": (
        '{\n  "name": "web",\n  "private": true,\n'
        '  "devDependencies": { "@sveltejs/kit": "2.5.0" }\n}\n'
    ),
    "apps/web/svelte.config.js": (
        "import adapter from '@sveltejs/adapter-static';\n"
        "\n"
        "export default { kit: { adapter: adapter() } };\n"
    ),
    "apps/web/tsconfig.json": (
        '{\n  "extends": "../../tsconfig.base.json",\n'
        '  "compilerOptions": { "strict": true }\n}\n'
    ),
    "apps/web/src/lib/index.ts": "export * from './api';\n",
    "apps/web/src/lib/api.ts": (
        "import { Course } from '@mono/model';\n"
        "import { formatTitle } from '@shared/util';\n"
        "import { writable } from 'svelte/store';\n"
        "\n"
        "export const courses = writable<Course[]>([]);\n"
        "\n"
        "export async function loadCourse(title: string): Promise<Course> {\n"
        "  const course = new Course();\n"
        "  formatTitle(title);\n"
        "  return course;\n"
        "}\n"
    ),
    "apps/web/src/lib/api.spec.ts": (
        "const api = require('./api');\n\napi.loadCourse('x');\n"
    ),
    "apps/web/src/lib/theme.ts": "export const darkTheme = { background: '#000' };\n",
    "apps/web/src/lib/Card.svelte": (
        '<script lang="ts">\n'
        "  export let title: string;\n"
        "  const more = () => import('./api');\n"
        "</script>\n"
        "\n"
        "<h2>{title}</h2>\n"
    ),
    "apps/web/src/routes/+page.svelte": (
        '<script lang="ts">\n'
        "  import { loadCourse } from '$lib/api';\n"
        "  import Card from '$lib/Card.svelte';\n"
        "\n"
        "  function handleClick() {\n"
        "    loadCourse('intro');\n"
        "  }\n"
        "</script>\n"
        "\n"
        "<!-- <script>import x from './commented-out'</script> -->\n"
        "<p>Don't import 'anything' from markup</p>\n"
        '<Card title="x" on:click={handleClick} />\n'
    ),
    "apps/web/src/routes/+page.ts": (
        "import { loadCourse } from '$lib';\n"
        "\n"
        "export const load = () => loadCourse('intro');\n"
    ),
    "apps/web/tests/home.spec.ts": (
        "import { test } from '@playwright/test';\n"
        "import { formatTitle } from '@shared/util';\n"
        "\n"
        "test('home', () => formatTitle('x'));\n"
    ),
    "apps/web/node_modules/dep/package.json": '{\n  "name": "dep"\n}\n',
    "apps/web/node_modules/dep/index.test.js": "import x from './index.js';\n",
    "apps/web/node_modules/dep/index.js": "export default 1;\n",
}

#: The repository file each import of a source file loads (``None``: an
#: external package), in source order.
ES_LINKS: dict[str, tuple[str | None, ...]] = {
    "packages/model/src/index.ts": (
        "packages/model/src/course.ts",
        "packages/model/src/lab.ts",
    ),
    "packages/model/src/course.ts": ("packages/model/src/lab.ts",),
    "packages/model/src/lab.ts": (),
    "packages/model/src/course.test.ts": (None, "packages/model/src/course.ts"),
    "packages/model/src/__tests__/lab.ts": ("packages/model/src/lab.ts",),
    "packages/shared/src/util.ts": (),
    "apps/web/svelte.config.js": (None,),
    "apps/web/src/lib/index.ts": ("apps/web/src/lib/api.ts",),
    "apps/web/src/lib/api.ts": (
        "packages/model/src/index.ts",
        "packages/shared/src/util.ts",
        None,
    ),
    "apps/web/src/lib/api.spec.ts": ("apps/web/src/lib/api.ts",),
    "apps/web/src/lib/theme.ts": (),
    "apps/web/src/lib/Card.svelte": ("apps/web/src/lib/api.ts",),
    "apps/web/src/routes/+page.svelte": (
        "apps/web/src/lib/api.ts",
        "apps/web/src/lib/Card.svelte",
    ),
    "apps/web/src/routes/+page.ts": ("apps/web/src/lib/index.ts",),
    "apps/web/tests/home.spec.ts": (None, "packages/shared/src/util.ts"),
}

#: Expected test modules per source file (direct imports only).
ES_TESTS_OF: dict[str, tuple[str, ...]] = {
    "packages/model/src/course.ts": ("packages/model/src/course.test.ts",),
    "packages/model/src/lab.ts": ("packages/model/src/__tests__/lab.ts",),
    "packages/model/src/index.ts": (),
    "packages/shared/src/util.ts": ("apps/web/tests/home.spec.ts",),
    "apps/web/src/lib/api.ts": ("apps/web/src/lib/api.spec.ts",),
    "apps/web/src/lib/Card.svelte": (),
    "apps/web/src/lib/theme.ts": (),
}
