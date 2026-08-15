# Clawness for OpenClaw (experimental)

This directory is a small TypeScript [OpenClaw](https://openclaw.ai) plugin that lets
Clawness run inside OpenClaw the same way it runs inside Claude Code. It does **not**
reimplement anything: it shells out to the exact Python hook scripts in `../hooks/` and
translates payload/response shapes between OpenClaw's hook API and the
`stdin-JSON → stdout` contract those scripts already speak. One engine, one rules corpus,
one test-gated Python contract.

## What works today (core scope)

| Clawness feature | Claude Code hook | OpenClaw hook | Adapter |
|---|---|---|---|
| Rules + memory injection | `UserPromptSubmit` | `before_prompt_build` | `hooks/claude_hook.py` → `appendContext` |
| SessionStart notes | `SessionStart` | `session_start` | seven note hooks → `enqueueNextTurnInjection` |
| Access guard (block/ask) | `PreToolUse` | `before_tool_call` | `hooks/access_guard.py` → `block` / `requireApproval` |
| Guard ask-ledger settle | `PostToolUse` | `after_tool_call` | `hooks/access_guard.py` (PostToolUse) |

**Deferred** (they read Claude-specific state and stay dormant, failing silent):
context watch (parses Claude's transcript JSONL), plan gate (rides Claude's native plan
mode + `permission_mode`), model advisor (reads Claude's `settings.json`), and the
skills-bootstrap wrapper.

## Layout

- `src/bridge.ts` — spawns a Python hook (`python3 → python → py` picker mirroring
  `plugin.json`), feeds it JSON on stdin, returns its stdout. Locates the repo root from
  the package's own path, so no env var is needed.
- `src/translate.ts` — pure shape translation: OpenClaw ctx → Claude payload, OpenClaw
  tool name/params → the `{tool_name, tool_input}` the guard classifier expects, and the
  guard's `permissionDecision` → OpenClaw's block/approval return. No I/O, no OpenClaw imports.
- `src/notes.ts` — runs the seven SessionStart note hooks and collects their output.
- `src/index.ts` — the **only** file that touches the OpenClaw API. Deliberately thin and
  defensive: every handler wraps its work in try/catch and fails toward doing nothing.
- `src/openclaw.d.ts` — minimal ambient types for the SDK (OpenClaw isn't a dependency).

## Build & test

```bash
cd openclaw
npm install
npm test          # builds, then runs unit + real-subprocess integration tests
npm run plugin:check   # offline compatibility check against the OpenClaw SDK
```

The integration tests (`test/bridge.integration.test.ts`) drive the **real** Python hooks
through the bridge — the same path used at runtime — so a broken payload contract fails
here rather than silently in front of a user. They skip cleanly when no Python is on PATH.

`plugin:check` runs [`@openclaw/plugin-inspector`](https://github.com/openclaw/plugin-inspector)
(pinned devDep, ~870 KB) against this package — **offline, no OpenClaw checkout, no
credentials, no provider network.** It statically validates that the manifest, the
`register()` shapes, and the hook wiring match the real OpenClaw SDK, and exits non-zero on
a hard breakage (so CI catches an SDK drift). It currently reports PASS with one non-fatal
proof-gap: it can't statically prove `before_tool_call` preserves block/approval semantics —
that is confirmed instead by the unit tests in `test/translate.test.ts` and, finally, by the
live smoke test below.

> **We deliberately do NOT depend on the full `openclaw` package for tests.** Its
> `plugin-sdk/plugin-test-api` mock would let us unit-test handlers against the real SDK
> types, but the only way to get it is the whole `openclaw` package (~87 MB unpacked; there
> is no standalone `@openclaw/plugin-sdk`). That contradicts this adapter's core rule — the
> host provides `openclaw/*` at runtime; it is never a build dependency — so the SDK-contract
> layer is verified by the offline inspector plus the live pass, not by pulling in the host.
>
> Note `plugin-test-api` turned out to be **excluded from the published package**
> (`!dist/plugin-sdk/plugin-test-api.*` in its `files`), so that route does not exist anyway.
> What the package DOES ship is the real `.d.ts` types. We used them once, out-of-tree: a
> throwaway `npm i openclaw` in a scratch dir + `tsc --noEmit` of `src/` against the real
> types (with our ambient `openclaw.d.ts` removed). That is a disposable verification step,
> not a dependency, and it earned its keep — see the enqueue fix below.

## Assumptions to verify against a live OpenClaw SDK

**Now confirmed offline** by `npm run plugin:check`: the four hook names
(`before_prompt_build`, `session_start`, `before_tool_call`, `after_tool_call`), the
`definePluginEntry`/`register()` shapes, the manifest, and the SDK import subpaths all match
the real OpenClaw SDK (PASS, 0 breakages).

**Also confirmed by type-checking `src/` against the real `.d.ts`** (the throwaway install
above): our `before_prompt_build` return (`{appendContext}`) and `before_tool_call` return
(`{block, blockReason}` / `{requireApproval}` / `{params}`) are valid against the real hook
result types, and all four hook names resolve. That pass **caught one real bug**: the
`session_start` note path was calling `enqueueNextTurnInjection({idempotencyKey,
appendContext})`, but the real `PluginNextTurnInjection` requires `{sessionKey, text}` and
has no `appendContext` — so notes were being dropped by the host. Fixed via
`buildNextTurnInjection` in `translate.ts` (tested), and the ambient stub now carries the
verified shape so the in-tree build enforces it.

What remains needs a live host — the inspector's one open proof-gap (`before_tool_call`
block/approval *runtime* semantics) and the runtime field names below (types confirm the
shapes we RETURN; only a live host confirms the values it PASSES, and whether an injection's
default `placement` renders the note where intended):

The public docs pin the hook names and the broad return shapes, but a few details are
read defensively and should be confirmed on a real host (correcting any of these touches
only `src/index.ts` or the keyword tables in `src/translate.ts`, never the tested core):

- **Event/ctx field names.** We read `event.prompt`, `event.toolName`, `event.params`, and
  resolve `cwd`/`sessionKey` from several candidate fields with a `process.cwd()` fallback.
- **`api.session.workflow.enqueueNextTurnInjection`** is the injection path for
  `session_start` notes (payload shape now verified — see the enqueue fix above); still
  guarded with optional chaining in case the host omits it.
- **Built-in tool names.** `translate.ts` maps by intent-keyword tokens
  (`bash`/`run_command`/`writeFile`/`read-file` …). Confirm OpenClaw's actual tool names
  and parameter field names, and extend the keyword/field tables if they differ.
- **Node engine.** OpenClaw requires Node ≥ 22.22.3; this package builds and tests on
  ≥ 22.19 but the host runtime floor is OpenClaw's.
