# Plan — OpenClaw-native commands for Clawness

**Status: Phases 1–2 SHIPPED; Phase 3 CLOSED as not viable.** The read-only commands
`/clawness-status`, `/clawness-query`, `/clawness-audit-rules` exist end-to-end (see
`src/commands.ts`). Phase 3 (`add`/`refresh`) was investigated and **cannot be faithfully
ported to the plugin-command surface** — the mechanism Phase 0 assumed does not hold for
external plugins (verified against the real runtime; see Phase 3 below). `add`/`refresh`
remain Claude-Code-only skills.

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

**NOT viable as commands (Phase 3 finding — was thought feasible after Phase 0):**

- `add` (author a rule from NL) and `refresh` (version-gap fix) rely on the *model* doing
  multi-step work with its own file tools (research docs, grep the codebase, propose, get
  approval, write YAML). Phase 0 read the runtime and concluded a `continueAgent:true`
  command could rewrite the agent's turn body to feed it that workflow. **Re-verified in
  Phase 3: it cannot** — an external plugin handler gets a *copy* of `commandBody` and no
  handle to mutate the body the agent sees, so `continueAgent:true` continues the agent on
  the ORIGINAL body and the handler's `text` is discarded. `add`/`refresh` stay
  Claude-Code-only. See Phase 3.

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
   So a command **can** cause the agent to run a turn. **At the time this read like it
   unblocked Phase 3** via (a) rewriting the body into a generation instruction +
   `continueAgent:true`, or (b) calling `ctx.runtimeContext.llm.complete(...)` in-handler.
   **Phase 3 re-verification overturned (a)** (see below): the `cleanedBody` rewrite path
   (`command.commandBodyNormalized` / `sessionCtx.BodyStripped`) belongs to *bundled*
   command handlers that hold those objects; an *external plugin* handler is handed a
   string copy of `commandBody` and cannot mutate what the agent sees. So `continueAgent`
   only continues the agent on the user's ORIGINAL body — it cannot inject a workflow.

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

## Phase 3 — `add` / `refresh` — CLOSED, not viable as commands

**Finding (verified against openclaw 2026.6.34 runtime + types):** an external plugin
command handler **cannot** inject an instruction the agent runs. The three levers were
checked and each fails for this use:

- **`continueAgent:true` + body rewrite (the Phase 0 plan):** impossible. `handlePluginCommand`
  builds the handler's `PluginCommandContext` from *copied* fields (`commandBody:
  command.commandBodyNormalized` — a string) and never writes back. In `get-reply`, the
  continue path sets `cleanedBody` from `command.commandBodyNormalized` / `sessionCtx.BodyStripped`
  — neither of which a plugin handler can touch — so the agent continues on the ORIGINAL
  body and the handler's `reply.text` is dropped on the continue branch.
- **`ctx.runtimeContext.llm.complete` (side-generate in-handler):** possible but wrong for
  this. `runtimeContext.llm` is *optional* (may be absent), and it generates without the
  agent's file tools, approval step, or doc research. That directly violates `refresh`'s
  design (research official docs, never write from memory, stop for approval) and skips
  `add`'s save/confirm/test steps.
- **`agentPromptGuidance`:** static system-prompt text injected on *every* agent turn while
  the command is registered — the always-on token cost Clawness's budgeted retrieval exists
  to avoid. Fine for a one-line pointer, not for a multi-step workflow.

**Decision:** `add`/`refresh` remain Claude-Code skills. OpenClaw users author/refresh rules
by running that workflow with their agent directly (or, later, via an OpenClaw *skill* —
`openclaw skills install` is a separate distribution channel from plugin commands, so the
ambition isn't dead, it just can't ride the plugin; that port is unscoped). The OpenClaw
plugin-command surface is complete at the three read-only commands.

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

1. ~~Can a `PluginCommandResult` inject agent instructions / trigger a turn?~~ **Answered
   across Phase 0 + Phase 3: it can trigger a turn but CANNOT inject an instruction.**
   `continueAgent: true` continues the turn to the LLM, but on the ORIGINAL body — an
   external plugin handler cannot rewrite what the agent sees (Phase 0's "rewrite the body"
   read applied to bundled handlers, not plugins). `ctx.runtimeContext.llm.complete` exists
   but is optional and side-generates. This is why Phase 3 is closed as not viable.
2. ~~How are plugin commands invoked and namespaced?~~ **Resolved in Phase 0:** bare `/name`
   in a global namespace, no auto-prefix, `status` (and many words) reserved → we prefix
   `clawness-`.
3. Do we want an OpenClaw-flavoured `claude-md` equivalent (an AGENTS.md/SOUL.md size
   check), or does the existing `openclaw-audit` skill already cover that ground?
