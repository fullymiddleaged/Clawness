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
  `before_prompt_build` → we read `event.prompt` → Python prints the block → we return
  `{appendContext}` → host injects it into the model prompt. Links 3–4 are verified; links
  1 and 5 are not. We read `event?.prompt` with a fallback and return `{}` when empty, so a
  differently-named field (`userPrompt`, `text`, …) makes rules **silently** never fire.
- **Event/ctx field names.** We read `event.prompt`, `event.toolName`, `event.params`, and
  resolve `cwd`/`sessionKey` from several candidate fields with a `process.cwd()` fallback.
- **`before_tool_call` runtime block/approval semantics** (the inspector's open proof-gap).
- **Injection `placement`.** `PluginNextTurnInjection.placement` is an unexported optional
  enum; we omit it and take the host default. Confirm the note renders where intended.
- **Built-in tool names.** `translate.ts` maps by intent-keyword tokens
  (`bash`/`run_command`/`writeFile`/`read-file` …). Confirm OpenClaw's actual tool names and
  parameter field names, and extend the keyword/field tables if they differ.
- **Node engine.** OpenClaw requires Node ≥22.22.3; this package builds/tests on ≥22.19,
  but the host runtime floor is OpenClaw's.
