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

**Feasible (Phase 0 resolved the gate) — scheduled for Phase 3:**

- `add` (author a rule from NL) and `refresh` (version-gap fix) rely on the *model* writing
  YAML. Phase 0 confirmed a command result **can** continue the agent turn
  (`continueAgent: true` + body rewrite) or call the LLM directly
  (`ctx.runtimeContext.llm`), so both are possible as commands. Do them in Phase 3 after
  the read-only shape is proven.

**Out of scope — agent-spawning skills** (`audit`, `review`, `perf`, `test`): they
orchestrate Claude Code sub-agents. OpenClaw has its own subagent system
(`SubagentRunParams` in the SDK), but wiring our 7 agents to it is a separate project.
`claude-md` is Claude-specific (CLAUDE.md) and does not apply to OpenClaw.

## Phase 0 — verify the command API — DONE (offline, against real SDK 2026.7.1)

Verified by reading the real types (`dist/types-DaHgOqFX.d.ts`) **and** the runtime
(`dist/commands-CjgJ-luM.js`, `commands-handlers.runtime-CunY89Nu.js`, `get-reply-*.js`,
`command-registration-tKF3dsKu.js`) in the installed host. All three questions answered;
no assumptions left, but see the owed live pass below.

**1. How a command is registered — `api.registerCommand(def)`.** It is a method on the
plugin API (the same `api` object hooks register on), not a field on the definition. Doc
string: *"Register a custom command that bypasses the LLM agent. Plugin commands are
processed before built-in commands and before agent invocation."* The definition shape
(`OpenClawPluginCommandDefinition`):

```ts
{ name: string;                 // bare, no leading slash, no plugin prefix ("tts")
  description: string;          // shown in /help and menus (required, non-empty)
  handler: (ctx: PluginCommandContext) => PluginCommandResult | Promise<…>;
  acceptsArgs?: boolean;        // MUST be true or args are rejected (matcher returns null)
  requireAuth?: boolean;        // default true — only authorized senders
  nativeNames?: { default?: string } & Record<string,string>;  // slash/menu aliases
  agentPromptGuidance?: readonly (string | {text; surfaces?})[];// static system-prompt guidance
  channels?: readonly string[]; // omit = every surface
  ownership?: "plugin" | "reserved"; requiredScopes?; … }
```

`PluginCommandContext` carries `args` (raw string after the name), `commandBody`,
`config` (full `OpenClawConfig`), `sessionKey`/`sessionId`/`sessionFile`, `agentId`,
`channel`, `isAuthorizedSender`, and **`runtimeContext.llm.complete(request)`** — a
bound-agent LLM handle available inside the handler (see Q2).

**2. What a result can do — text reply, OR continue the agent turn.**
`PluginCommandResult = ReplyPayload & { continueAgent?: boolean; suppressReply?: boolean }`,
and `ReplyPayload.text` is the plain-text reply body. Runtime semantics (traced through
`runCommands` → `get-reply-*.js`):
   - `continueAgent` omitted/false → `{ kind: "reply" }`: the handler's `text` is
     delivered to the user and **the agent turn stops**. This is the read-only path
     (`status`/`stats`/`query`/`audit-rules`): run the CLI, return stdout as `text`.
   - `continueAgent: true` → `{ kind: "continue", cleanedBody }`: processing **continues
     to the LLM agent**, and the handler may first rewrite the body the agent sees
     (`sessionCtx.BodyStripped` / `command.commandBodyNormalized`).
   So a command **can** feed the agent a turn. **This unblocks Phase 3:** `add`/`refresh`
   are feasible either by (a) rewriting the body into a generation instruction +
   `continueAgent:true` (lets the normal agent write YAML through its file tools — mirrors
   how the Claude `SKILL.md` works), or (b) calling `ctx.runtimeContext.llm.complete(...)`
   directly in the handler. Prefer (a): it reuses the agent's own file tools and approval
   path rather than side-generating.

**3. Invocation + namespacing — bare `/name`, GLOBAL namespace, collision-prone.**
Invoked in-session as `/name` (`matchPluginCommand` matches `/<name>` with `_`↔`-`
normalization, plus `nativeNames`). Names live in one global `pluginCommands` map — there
is **no automatic `clawness:` prefix**. A fixed reserved set (`getReservedCommands`)
blocks plugins from these, and **`status` is reserved** (also `stats` is free but
`context`, `model`, `config`, `skill`, `help`, `commands`, `usage`, … are reserved).
`validateCommandName` allows `^[a-z][a-z0-9_-]*$`.
   - **Decision: prefix every command `clawness-`.** `status` is reserved so `/status` is
     impossible anyway, and generic words (`add`, `refresh`, `query`) would collide with
     other plugins in the shared namespace. Ship `/clawness-status`, `/clawness-stats`,
     `/clawness-query`, `/clawness-audit-rules` (later `/clawness-add`, `/clawness-refresh`).
     All must set `acceptsArgs: true` if they take any args, or the matcher drops them.

**Owed live pass (fold into Phase 1, don't build a throwaway):** confirm the real `status`
command appears in `openclaw plugins inspect clawness --runtime --json` and is invocable on
the live host. The offline evidence is unusually strong (real matcher + executor + result
consumer read, not just types), so a standalone throwaway command is not worth a
dist-rebuild cycle — prove it with the actual Phase 1 command instead.

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

1. ~~Can a `PluginCommandResult` inject agent instructions / trigger a turn?~~ **Resolved
   in Phase 0: yes** — `continueAgent: true` continues the turn to the LLM (and lets the
   handler rewrite the body), and `ctx.runtimeContext.llm.complete` is available in-handler.
2. ~~How are plugin commands invoked and namespaced?~~ **Resolved in Phase 0:** bare `/name`
   in a global namespace, no auto-prefix, `status` (and many words) reserved → we prefix
   `clawness-`.
3. Do we want an OpenClaw-flavoured `claude-md` equivalent (an AGENTS.md/SOUL.md size
   check), or does the existing `openclaw-audit` skill already cover that ground?
