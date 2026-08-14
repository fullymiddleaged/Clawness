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
npm test        # builds, then runs unit + real-subprocess integration tests
```

The integration tests (`test/bridge.integration.test.ts`) drive the **real** Python hooks
through the bridge — the same path used at runtime — so a broken payload contract fails
here rather than silently in front of a user. They skip cleanly when no Python is on PATH.

## Assumptions to verify against a live OpenClaw SDK

The public docs pin the hook names and the broad return shapes, but a few details are
read defensively and should be confirmed on a real host (correcting any of these touches
only `src/index.ts` or the keyword tables in `src/translate.ts`, never the tested core):

- **Event/ctx field names.** We read `event.prompt`, `event.toolName`, `event.params`, and
  resolve `cwd`/`sessionKey` from several candidate fields with a `process.cwd()` fallback.
- **`api.session.workflow.enqueueNextTurnInjection`** is the injection path for
  `session_start` notes; guarded with optional chaining if absent.
- **Built-in tool names.** `translate.ts` maps by intent-keyword tokens
  (`bash`/`run_command`/`writeFile`/`read-file` …). Confirm OpenClaw's actual tool names
  and parameter field names, and extend the keyword/field tables if they differ.
- **Node engine.** OpenClaw requires Node ≥ 22.22.3; this package builds and tests on
  ≥ 22.19 but the host runtime floor is OpenClaw's.
