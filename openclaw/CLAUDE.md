# CLAUDE.md — working on the OpenClaw adapter

Orientation for agents/devs working **on** `openclaw/`. The consumer-facing overview
(what it is, layout, how to build/test) is in [README.md](README.md); this file is the
*why*. It loads automatically when Claude Code works in this subtree, so it costs nothing
on the root CLAUDE.md.

## Core design rules (don't undo without reading these)

- **OpenClaw is NOT a build dependency.** The host provides `openclaw/*` modules at
  runtime; we import them against the minimal ambient types in `src/openclaw.d.ts`, never
  a package. Its engine floor (Node ≥22.22.3) is stricter than what we build/test against,
  and pulling it in would couple our build to an 87 MB host. Keep the ambient stub as the
  only type surface, and keep it accurate (see the enqueue lesson below).
- **`src/index.ts` is the ONLY file that touches the OpenClaw API**, and it is
  deliberately thin: every handler wraps its work in try/catch and fails toward doing
  nothing. All real logic lives in `bridge.ts`/`translate.ts`/`notes.ts`, which are
  host-agnostic and unit-tested against the real Python. Correcting a host field name
  touches only `index.ts` or the keyword tables in `translate.ts`, never the tested core.
- **We shell out to the exact `../hooks/` scripts** — no reimplementation. One engine,
  one rules corpus, one test-gated Python contract shared with the Claude Code path.

## How we verify against the real SDK, without depending on it

Two offline layers plus one owed live pass:

1. **`npm run plugin:check`** (`@openclaw/plugin-inspector`, pinned devDep, ~870 KB) —
   statically validates the manifest, `register()` shapes, and hook wiring against the real
   SDK. CI-gated (exits non-zero on a hard breakage). Currently PASS / 0 breakages, with
   one non-fatal proof-gap it can't settle statically: `before_tool_call` block/approval
   *runtime* semantics.
2. **Type-check `src/` against the real `.d.ts`, out-of-tree.** The `plugin-test-api` mock
   the docs describe is **excluded from the published package** (`!dist/plugin-sdk/
   plugin-test-api.*` in its `files`), so that route does not exist. But the package ships
   the real types. The verification is a *throwaway*: `npm i openclaw` in a scratch dir +
   `tsc --noEmit` of `src/` against the real types, with our ambient `openclaw.d.ts`
   removed. Disposable step, not a dependency — and it earned its keep (see below).
3. **Live smoke test** — still owed; only a running host confirms the runtime facts below.

## What's confirmed vs open

**Confirmed offline** (inspector + the type-check pass):
- The four hook names resolve: `before_prompt_build`, `session_start`, `before_tool_call`,
  `after_tool_call`; plus `definePluginEntry`/`register()` shapes, the manifest, and the
  SDK import subpaths.
- Our returns are valid against the real hook result types: `before_prompt_build`
  `{appendContext}`; `before_tool_call` `{block, blockReason}` / `{requireApproval}` /
  `{params}`.

**The type-check caught one real bug.** The `session_start` note path was calling
`enqueueNextTurnInjection({idempotencyKey, appendContext})`, but the real
`PluginNextTurnInjection` requires `{sessionKey, text}` and has **no** `appendContext` — so
notes were being dropped by the host. `appendContext` is correct for the
`before_prompt_build` *result*, a different shape; the two were conflated. Fixed via
`buildNextTurnInjection` in `translate.ts` (tested), and `src/openclaw.d.ts` now carries
the verified `NextTurnInjection` shape so the in-tree build enforces it. **Lesson: the
ambient stub is only as good as its last verification — re-run the type-check pass after
changing anything we send to the host.**

**Open — needs a live host** (types confirm the shapes we RETURN; only a live run confirms
the values the host PASSES, and whether the host honours what we return):

- **Does a rule actually reach the model?** The load-bearing chain is: host calls
  `before_prompt_build` → we read the prompt → Python prints the block → we return
  `{appendContext}` → host injects it into the model prompt. Links 3–4 are verified; links
  1 and 5 are not. `resolvePromptText` (translate.ts, tested) reads the first non-empty of
  `prompt`/`userPrompt`/`text`/`input`/`message`, so the common alternate names are covered;
  a name outside that list still makes rules **silently** never fire, so the live pass must
  confirm the real field — add it to the head of that list if it differs.
- **Event/ctx field names.** We read the prompt via `resolvePromptText`, plus
  `event.toolName`, `event.params`, and resolve `cwd`/`sessionKey` from several candidate
  fields with a `process.cwd()` fallback.
- **`before_tool_call` runtime block/approval semantics** (the inspector's open proof-gap).
- **Injection `placement`.** `PluginNextTurnInjection.placement` is an unexported optional
  enum; we omit it and take the host default. Confirm the note renders where intended.
- **Built-in tool names.** `translate.ts` maps by intent-keyword tokens
  (`bash`/`run_command`/`writeFile`/`read-file` …). Confirm OpenClaw's actual tool names and
  parameter field names, and extend the keyword/field tables if they differ.
- **Node engine.** OpenClaw requires Node ≥22.22.3; this package builds/tests on ≥22.19,
  but the host runtime floor is OpenClaw's.

## Distribution — git install (don't undo the root-level files)

Users install with `openclaw plugins install git:github.com/fullymiddleaged/Clawness`.
That shape drove four decisions that look odd in isolation; each is load-bearing.

- **There is a `package.json` and an `openclaw.plugin.json` at the REPO ROOT, not just
  here.** OpenClaw's git installer clones the repo and reads the plugin manifest at the
  **clone root** — the docs describe no subdirectory/monorepo support — so a manifest that
  lives only in `openclaw/` is never found. The root `package.json`'s `openclaw.extensions`
  points at `./openclaw/dist/src/index.js`; the root `openclaw.plugin.json` is a byte copy
  of this dir's. `tests/test_openclaw_manifest.py` fails CI if the two copies drift or the
  entry path changes. **Don't "clean up" the root `package.json` as stray Node cruft in a
  Python repo — deleting it makes the plugin uninstallable.** The subdir `package.json`
  stays too: it's the dev/build/test package (tsc, node --test, the inspector devDep).
- **`openclaw/dist/src/*.js` is COMMITTED.** Git installs load prebuilt JS and do **not**
  run a build/prepare step (the docs: "TypeScript source entries are only for source
  checkouts and local development paths"). So the entry must be in the clone. The root
  `.gitignore` still ignores `dist/` (that's the *Python* sdist/wheel output) but carves
  out `!openclaw/dist/`, and `openclaw/.gitignore` uses `dist/*` + `!dist/src/` to expose
  only `dist/src` (runtime), never `dist/test`. A `.gitattributes` pins those files to
  `eol=lf` so a Windows checkout doesn't rewrite them. The CI `openclaw` job rebuilds and
  `git diff --exit-code dist/src` to catch a stale commit — **rebuild and commit `dist/src`
  whenever you touch `src/`**, or CI fails.
- **No engine vendoring — and that is the point of git-install over npm.** The clone brings
  `hooks/`, `rules/`, `clawness/` with it, so `bridge.ts`'s `../hooks/...` resolves against
  the real engine: one source of truth, no second copy to sync. An npm package of just
  `openclaw/` could not carry the sibling engine (files outside the package dir), which is
  why that option was rejected. The cost is the committed `dist/src` above.
- **Open — the packaging fact only a live host settles:** does the installer keep the
  **whole clone** as the plugin dir (so `../hooks` exists at runtime), or prune it to the
  declared entry? If it prunes, every hook spawn fails silently — same signature as a
  Python-less box — and the fix is to vendor the engine after all. This is the FIRST thing
  the live pass checks; see `LIVE-TEST.md`.
