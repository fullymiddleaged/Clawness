# Plan — OpenClaw-native commands for Clawness

**Status: PROPOSAL, not yet implemented.** This is a design + implementation plan for
giving OpenClaw users the CLI-backed Clawness skills as native OpenClaw commands. No
command code exists yet; the adapter is still hook-only.

## Why

Clawness's 12 `skills/*/SKILL.md` are Claude Code skills. Verified against the installed
OpenClaw SDK (2026.7.1): **OpenClaw plugins cannot contribute skills.** A plugin's
`register(api)` can add hooks, **commands** (`OpenClawPluginCommandDefinition`), tools,
services, memory capabilities, and providers, but there is no skill type in the plugin
SDK. OpenClaw "skills" are a separate user-level system installed via `openclaw skills
install`. So on OpenClaw today none of our skills or sub-agents surface.

The OpenClaw-native equivalent of a slash-command skill is a **plugin command**. We give
OpenClaw users their own commands, implemented OpenClaw-specifically in the adapter, not
by reusing the Claude `SKILL.md` files.

## Scope

**In scope — the CLI-backed, host-agnostic skills**, ported as OpenClaw commands:

- `status` — report loaded rule counts + token cost (runs `clawness stats`).
- `stats` / `query` — surface the CLI query/stats directly (read-only).
- `audit-rules` — maintainer corpus check (runs `clawness audit-rules`).

**Deferred — needs a decision on command result semantics (see Phase 0):**

- `add` (author a rule from NL) and `refresh` (version-gap fix) both rely on the *model*
  writing YAML. Feasible only if a command result can inject agent instructions rather
  than just return text. Decide after Phase 0.

**Out of scope — agent-spawning skills** (`audit`, `review`, `perf`, `test`): they
orchestrate Claude Code sub-agents. OpenClaw has its own subagent system
(`SubagentRunParams` in the SDK), but wiring our 7 agents to it is a separate project.
`claude-md` is Claude-specific (CLAUDE.md) and does not apply to OpenClaw.

## Phase 0 — verify the command API (do this FIRST, don't assume)

Confirmed so far: `OpenClawPluginCommandDefinition`, `PluginCommandContext`,
`PluginCommandResult` exist and commands are a supported plugin capability. NOT yet
verified:

1. The exact shape of `OpenClawPluginCommandDefinition` and how `register(api)` adds a
   command (a `commands` field on the definition, or an `api.registerCommand(...)` call).
   Read `<openclaw>/dist/plugin-sdk/*.d.ts` for these three types.
2. What a `PluginCommandResult` can do: plain reply text only, or inject agent context /
   trigger a turn. This decides whether `add`/`refresh` are possible as commands.
3. How a user invokes a plugin command (in-session `/name`, `openclaw <name>` CLI, or
   both) and how it is namespaced (`clawness:status` vs `status`).

Method: read the real `.d.ts`, then register one throwaway command and confirm it appears
in `openclaw plugins inspect clawness --runtime --json` (`commands` array) and is
invocable on the live host. Same offline-check-plus-one-live-pass method used for hooks.

## Phase 1 — `status` proof of concept

- Add `runPythonCli(args)` to [src/bridge.ts](src/bridge.ts): spawn
  `python3 -m clawness.cli <args>` with `PYTHONPATH=REPO_ROOT` (mirror `runPythonHook`;
  the clone root already carries `clawness/`, so no pip install is needed — same reason
  the hooks work). Reuse the existing `python3 → python → py` picker and the noPython path.
- New host-agnostic file `src/commands.ts`: define the command(s) and their handlers,
  which call `runPythonCli` and format the result. No OpenClaw imports here (keep it
  testable against real Python, like bridge/translate/notes).
- Register the commands in [src/index.ts](src/index.ts) — the only file that touches the
  OpenClaw API. `index.ts` stays thin: each handler wraps in try/catch, fails toward doing
  nothing, and returns the no-Python note when Python is absent (reuse `NO_PYTHON_NOTE`).

## Phase 2 — the remaining read-only commands

Add `stats`, `query`, `audit-rules` the same way once Phase 1's shape is proven.

## Phase 3 — decide on `add` / `refresh`

Only if Phase 0 shows a command can inject agent instructions. Otherwise document them as
Claude-Code-only and have OpenClaw users run the CLI directly.

## Tests (ENF-TEST-001, TST-FAILFIRST-001)

- Unit-test the command definitions and result formatting in `test/`.
- A real-subprocess integration test that runs `python3 -m clawness.cli stats` through
  `runPythonCli`, mirroring `test/bridge.integration.test.ts` (skips cleanly with no
  Python on PATH).
- Watch each new test fail first.

## Docs + release

- Update [README.md](README.md) "What works today" and [CLAUDE.md](CLAUDE.md) with a
  commands section; correct the root README's OpenClaw note (skills/agents are Claude-only;
  OpenClaw gets commands). GEN-DOCSYNC-001.
- **Rebuild and commit `openclaw/dist/src`** — CI runs `git diff --exit-code dist/src`.
- User-visible additive feature → **minor version bump** (1.13.0) across all four version
  places + a CHANGELOG entry, per the root CLAUDE.md release rules. A push to `main` is a
  release, so do this work on a branch (`feat/openclaw-commands`) until it is ready.

## Open questions

1. Can a `PluginCommandResult` inject agent instructions / trigger a turn, or only return
   text? (Gates Phase 3.)
2. How are plugin commands invoked and namespaced on OpenClaw?
3. Do we want an OpenClaw-flavoured `claude-md` equivalent (an AGENTS.md/SOUL.md size
   check), or does the existing `openclaw-audit` skill already cover that ground?
