# Clawness for OpenClaw (experimental)

This directory is a small TypeScript [OpenClaw](https://openclaw.ai) plugin that lets
Clawness run inside OpenClaw the same way it runs inside Claude Code. It does **not**
reimplement anything: it shells out to the exact Python hook scripts in `../hooks/` and
translates payload/response shapes between OpenClaw's hook API and the
`stdin-JSON → stdout` contract those scripts already speak. One engine, one rules corpus,
one test-gated Python contract.

> Design rationale, the SDK-verification method, and the open items to confirm against a
> live host live in [CLAUDE.md](CLAUDE.md) (loaded automatically when working in this
> subtree). Read it before changing the adapter.

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
npm test               # builds, then runs unit + real-subprocess integration tests
npm run plugin:check   # offline compatibility check against the OpenClaw SDK
```

- `npm test` — the integration tests (`test/bridge.integration.test.ts`) drive the **real**
  Python hooks through the bridge, the same path used at runtime, so a broken payload
  contract fails here rather than in front of a user. They skip cleanly when no Python is on
  PATH.
- `npm run plugin:check` — runs
  [`@openclaw/plugin-inspector`](https://github.com/openclaw/plugin-inspector) (pinned
  devDep) against this package: offline, no OpenClaw checkout, no credentials. It statically
  validates the manifest, the `register()` shapes, and the hook wiring against the real
  OpenClaw SDK, and exits non-zero on a hard breakage (so CI catches SDK drift).
