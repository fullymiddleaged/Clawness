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

## Native commands — read-only only, and that is a hard SDK limit

`src/commands.ts` defines host-agnostic command specs; `index.ts` maps each onto
`api.registerCommand`. Three ship, all backed by the bundled `clawness` CLI via
`runPythonCli`: `/clawness-status` (`stats`), `/clawness-query <prompt>` (`query`,
prompt forwarded as ONE positional so spaces survive), `/clawness-audit-rules [flags]`
(`audit-rules`, raw args split into flag tokens). Every command is `clawness-` prefixed
because plugin commands share one GLOBAL namespace with no auto-prefix and many plain
words (`status`, …) are reserved by core; `acceptsArgs:true` is mandatory or the matcher
drops args. `formatCliOutput` surfaces stderr on non-zero exit so a broken invocation is
visible, not silent.

- **`add`/`refresh` are NOT ported, and cannot be — this is verified, don't retry it.**
  They need the *model* to do multi-step work with its own file tools (research docs,
  grep the codebase, propose, get approval, write YAML). An external plugin command
  **cannot inject an instruction the agent runs**: `handlePluginCommand` hands the handler
  a *copy* of `commandBody` (a string) and no handle to `command`/`sessionCtx`, so
  `continueAgent:true` continues the agent on the user's ORIGINAL body and the handler's
  `reply.text` is dropped on the continue branch (traced through `commands-handlers.runtime`
  + `get-reply` in openclaw 2026.6.34). `runtimeContext.llm.complete` is optional and
  side-generates without file tools/approval/doc-lookup — wrong for `refresh` by design.
  `agentPromptGuidance` is static system-prompt text on *every* turn — the always-on cost
  Clawness avoids. So `add`/`refresh` stay Claude-Code skills; the full write-up and the
  runtime evidence are in [COMMANDS-PLAN.md](COMMANDS-PLAN.md) Phase 3.
- **OpenClaw *skills* are a separate channel from plugin commands** (`openclaw skills
  install`), so the ambition of "give OpenClaw users add/refresh" isn't dead — it just
  can't ride the plugin. That port is unscoped; don't assume its file format or git-install
  support without verifying against the SDK.
- **The command *reply path* is still owed a live pass.** `openclaw plugins inspect`
  confirms registration, but `openclaw agent --local` routes `/clawness-*` to the LLM
  (same one-shot-CLI gap as SessionStart notes), so the actual reply must be confirmed on
  a real interactive host.

## OpenClaw-native extensions beyond the shared hooks (1.13.0)

Three capabilities with no Claude Code equivalent, built under one hard constraint:
**nothing changes Claude Code / shared-engine behaviour.** All logic is OpenClaw-only —
`src/{compaction,install,memory}.ts` (host-agnostic, unit-tested) wired by `index.ts`, plus
`pyhooks/*.py` that reuse `clawness.{trust,memory}` **read-only**. The pyhooks self-locate
the repo root (`parents[2]`) and run through the same bridge as the shared hooks; they never
run on the Claude Code path. Each fails toward doing nothing. Contracts were read from the
real installed SDK (openclaw 2026.7.1-2); the findings are in `EXTENSIONS-PLAN.md`.

- **`after_compaction` re-orientation (`src/compaction.ts`).** The native home for the
  context-watch + handoff. The compaction hooks are **observe-only** (`=> void`), so a hook
  cannot author a rich handoff (no model in the loop) — don't try to make it. What it CAN
  do: rules + ranked memory self-heal on the next `before_prompt_build`, so we re-inject only
  the SessionStart-only orientation a compaction drops — a notice + the handoff and stack
  notes (the `REORIENTATION_NOTE_HOOKS` subset in `notes.ts`). **Do NOT re-run the full
  session-note set here:** the once-per-project nags (changelog/claude_md/trust/git/
  memory_init) key their ledgers on the session id, which a compaction can ROTATE, so
  re-running them re-asks answered questions. The idempotency key embeds a per-compaction
  marker (`previousSessionId`), so retries de-dupe but the next compaction re-fires.
  `registerCompactionProvider` is deliberately unused — it replaces the summarizer, not our
  goal.
- **`before_install` trust vetting (`src/install.ts` + `pyhooks/install_scan.py`).** The
  event carries the artifact's on-disk `sourcePath`; the result `{findings, block, blockReason}`
  lets us surface findings AND block. We reuse `scan_injection_tells` line-by-line for real
  line numbers. **Block arms only on a CRITICAL tell** (agent-hijack / exfil-host / metadata
  / decode-execute) — the dual-use tells (curl, `.env`, base64, zero-width) warn but never
  block, because a real security skill legitimately mentions them. Escape hatches:
  `CLAW_NO_INSTALL_BLOCK` (warn, don't block), `CLAW_NO_INSTALL_SCAN` (off). This is the same
  harm-reduction framing as the access guard: widen block conservatively, prefer surfacing.
- **Memory corpus supplement (`src/memory.ts` + `pyhooks/memory_corpus.py`).** ADDITIVE, not
  a replacement: the ranked block still injects every turn via `before_prompt_build`; this
  makes the same lessons discoverable through OpenClaw's native memory search.
  `registerMemoryPromptSection`/`…Supplement`'s builder is **sync, returns `string[]`, and
  gets no prompt** — a dead end (can't call our async ranker, can't rank the turn). The fit is
  `registerMemoryCorpusSupplement({search, get})`, which is async + query-bearing +
  "additive (non-exclusive)". **Known limit:** `search`/`get` carry no cwd, so the supplement
  resolves the project from the most-recent session cwd (`lastCwd` in `index.ts`) — correct
  for single-workspace; a multi-workspace live pass is owed.
- **Context engine (`registerContextEngine`) — evaluated and DECLINED.** It is an
  **exclusive** whole-transcript store (`bootstrap`/`ingest`/`assemble`/…). Using it means
  reimplementing OpenClaw's context storage and DISPLACING the host default, to do what
  `before_prompt_build` already does by appending. More risk, no user-visible gain, and it
  collides with the no-core-regression constraint. Don't revisit without a new reason.

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
   **It pins to the LATEST `openclaw` on npm, so it cannot catch that an OLDER host
   lacks the API entirely.** `definePluginEntry` and the whole `plugin-sdk/plugin-entry`
   subpath do not exist before `2026.3.24-beta.2` (verified: absent everywhere in a
   pinned `npm i openclaw@2026.3.11` — the first live install target, which is why it
   was discovered but failed to load). That is a version *floor*, not an import-path bug:
   the import stays the focused subpath the docs mandate (root `openclaw/plugin-sdk` is
   the *deprecated* barrel, scheduled for removal), and the floor is declared as
   `openclaw.compat.pluginApi` / `install.minHostVersion` `>=2026.3.24-beta.2` in BOTH
   package.json files so a too-old host is rejected at install instead of failing to load.
3. **Live smoke test** — DONE (OpenClaw 2026.7.1, git install, real agent turns). Results
   under *What's confirmed vs open* below. Re-run against any host `>=2026.3.24-beta.2`;
   anything older cannot load this API.

## What's confirmed vs open

**Confirmed offline** (inspector + the type-check pass):
- The four hook names resolve: `before_prompt_build`, `session_start`, `before_tool_call`,
  `after_tool_call`; plus `definePluginEntry`/`register()` shapes, the manifest, and the
  `plugin-sdk/plugin-entry` import subpath — **on the latest SDK only** (the offline
  check pins latest; the API floor is `>=2026.3.24-beta.2`, declared in the manifests).
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

**Confirmed live** (OpenClaw 2026.7.1, git install, real agent turns):

- **Rules reach the model.** `before_prompt_build` fires and the host injects our
  `{appendContext}` into the model prompt — the mandatory block appears verbatim. The
  prompt arrives under `prompt`, `resolvePromptText`'s first candidate, so no reorder was
  needed.
- **Access guard is honoured.** `before_tool_call` fires on real tool calls; a returned
  `{block}` stops the tool and surfaces `blockReason` to the model, and `{requireApproval}`
  round-trips. `after_tool_call` settles the ask-ledger (a repeated out-of-project write
  goes ask→allow).
- **Tool names/params.** Real events: write=`{toolName:"write",params:{path,content}}`,
  read=`{toolName:"read",params:{path}}`; the `translate.ts` keyword/field tables map both
  correctly. The shell tool's name went unobserved — the model refuses the deny-tier baits
  before running a shell command, which is why `block` was proved via a forced-block A/B on
  a benign write (file created un-forced, absent when blocked), not a real malicious command.

**Still open — one path, plus the engine floor:**

- **SessionStart notes don't surface through the one-shot `openclaw agent` CLI.** The
  `session_start` → `enqueueNextTurnInjection` call is real and awaited but returns
  `undefined`, and the note text never appears across turns. Root-caused as a CLI-harness
  limit, not our bug — next-turn injection is presumably delivered on an interactive
  channel, which the CLI can't exercise. Verify once on a real channel. It **fails silent**
  (a dropped note costs nothing), and the load-bearing no-Python warning also rides
  `before_prompt_build`, so it lands regardless. `placement` stays unconfirmed for the same
  reason (we omit it and take the host default).
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
