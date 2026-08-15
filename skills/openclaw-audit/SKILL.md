---
name: openclaw-audit
description: >
  Trim an OpenClaw workspace's base system prompt. Measures what the always-injected
  workspace files (SOUL.md, AGENTS.md, IDENTITY.md, USER.md, MEMORY.md) cost per turn,
  then works through them section by section — cutting what the tools or codebase already
  say, moving durable conventions to Clawness ranked retrieval (.clawness/rules/) and
  one-line traps to .clawness/memory.md, and leaving load-bearing persona and identity
  where they are. Destructive and deliberate: run it when you have the session to spend.
---

# Trim an OpenClaw workspace prompt

OpenClaw assembles its system prompt fresh for every agent turn. Alongside the fixed
sections it manages (Tooling, Safety, Skills, Workspace, …), it injects the workspace's
own Markdown files **in full, every turn**: `AGENTS.md`, `SOUL.md`, `IDENTITY.md`,
`USER.md`, `BOOTSTRAP.md` (new workspaces only, self-deletes after first run), and
`MEMORY.md` when present. That injected block is the one part of the per-turn cost the
workspace owner controls — and the usual "landslide of noise per turn" complaint is it
growing unchecked. OpenClaw caps it at 20,000 chars per file and 60,000 total and
truncates past that with a terse notice, which is a worse outcome than trimming on
purpose: you don't choose what gets cut.

This skill is the **remedy**, the OpenClaw sibling of `/clawness:claude-md`. It is opt-in
work: you invoking it is the consent, because a good trim is destructive and takes a while.

**Say this before starting.** This reorganises files the user may care about a great deal
(SOUL.md *is* the agent's personality), and it takes most of a session. Tell them: nothing
is deleted until its replacement is verified, and they can stop after any single file. Then
get an explicit go-ahead.

> **Filenames and limits below are from OpenClaw's public docs, not a live install.**
> Before you measure, confirm the workspace path and the file set actually present — see
> the same caution in [openclaw/README.md](openclaw/README.md) ("Assumptions to verify").
> The default workspace is `~/.openclaw/workspace/`; the user may point elsewhere.

## First: is this the right tool?

This skill earns its place only when Clawness is running under OpenClaw (the `openclaw/`
TypeScript adapter shelling out to the Python hooks), because the whole point is to move
bulk *out* of always-injected files and *into* Clawness's ranked retrieval, which the
adapter provides via the `before_prompt_build` hook. Without that adapter installed there
is nowhere cheaper for the content to go, and you should stop and say so — trimming a
workspace file with no ranked destination just loses information.

Check first: is the `openclaw/` adapter installed and are `.clawness/rules/` and
`.clawness/memory.md` reachable in this workspace? If not, say what's missing and stop.

## What actually reduces per-turn cost

Only these move the number. Get this right or the exercise is theatre:

| Destination | When it loads | Use for |
| --- | --- | --- |
| `SOUL.md` / `IDENTITY.md` / `USER.md` | every turn, in full | persona, identity, values, guardrails, user profile — load-bearing every turn |
| `AGENTS.md` | every turn, in full — **and it is the ONLY file sub-agent sessions get** | core operating instructions; keep it the leanest of all |
| OpenClaw `MEMORY.md` | every turn, in full | **not a cheap destination** — moving bulk here saves nothing |
| `.clawness/rules/*.yml` | ranked per prompt, inside `CLAW_BUDGET` | durable conventions tied to a *topic* rather than the agent's identity |
| `.clawness/memory.md` | ranked per prompt, ~3 lines | one-line traps that bit once |
| OpenClaw skills | when invoked or judged relevant | multi-step procedures |

Two OpenClaw-specific traps:

- **OpenClaw's own `MEMORY.md` is injected whole, like the others.** Do not "save cost" by
  shovelling material from SOUL.md into MEMORY.md — same per-turn tax, just relabelled.
  Durable conventions belong in `.clawness/rules/` (ranked); one-line traps in
  `.clawness/memory.md` (ranked). Only genuinely-every-turn curated memory stays in
  OpenClaw's MEMORY.md.
- **`AGENTS.md` is the file sub-agents inherit, alone.** Every char there is paid by the
  main loop *and* every sub-agent spawn. It is the highest-leverage file to shrink and the
  one where "keep it operational, move the rest" matters most.

OpenClaw's own guidance echoes this: keep `SOUL.md` under ~500 words and `AGENTS.md` under
~300; adherence improves as they shrink, not worse.

## Steps

### 1. Measure

Read each present workspace file (`AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`,
`MEMORY.md`; ignore `BOOTSTRAP.md` — it self-deletes). Report:

- per-file characters and a rough token estimate (chars ÷ 4; call it rough, it is), plus
  the total and how it sits against the 20k/file and 60k/total caps
- a per-section breakdown by top-level heading within each file, largest first

The breakdown is the point. "Your workspace is 8k tokens" invites a shrug; "your AGENTS.md
Tooling section is 2.1k tokens re-describing tools OpenClaw already lists in its Tooling
section, and every sub-agent pays for it too" does not.

**If you can, measure the real assembled cost, not just the files.** OpenClaw exposes the
fully assembled prompt (all fixed sections + injected files) through the `llm_input` and
`before_agent_run` hooks. A tiny observation-only plugin logging the assembled length once
gives the true per-turn number, of which the workspace files are only a part. Offer this;
mark it as docs-derived and unverified against a live host until you've run it.

### 2. Classify every section

Go heading by heading, per file, and assign one verdict. **Default to STAY.** The burden of
proof is on moving something.

- **STAY** — persona, identity, values, behavioural guardrails, user profile, and core
  operating instructions. This is load-bearing *every turn regardless of the prompt*, which
  is exactly what retrieval cannot be trusted to surface on demand. SOUL.md's personality
  and USER.md's "call me X" must be present whether or not the prompt hints at them. Keep
  them — just tighten wording (see Don'ts: tighten is not reword-the-meaning).
- **CUT** — anything the tools, workspace, or codebase already answer: tool inventories
  (OpenClaw's Tooling section already lists them), restated OpenClaw docs, directory
  layouts, dependency lists, and history that reads as a changelog.
- **→ `.clawness/rules/<domain>/<ID>.yml`** — a durable convention tied to a *topic* rather
  than the agent's identity (coding standards, a library's house style, error-handling
  rules). Full rule format: `id, domain, severity, tags, triggers, when, rule, violation,
  correct`. `tags`/`triggers` drive retrieval — put the words someone would actually type.
- **→ `.clawness/memory.md`** — one-line traps only, under `## Lessons`, ≤120 chars, naming
  the file or flag so it retrieves. If the rationale is the payload, it is not a memory
  entry; use `.clawness/rules/` instead.

Present the whole classification as a table and **get approval before moving anything.**
Include the projected per-turn token saving (and, if AGENTS.md shrinks, note the extra
sub-agent saving separately).

### 3. Move one section at a time, verify, then delete

Never delete from a workspace file until its replacement is proven to surface. For each
approved section, smallest first:

1. Write the new file (or add the rule / memory line).
2. Verify it:
   - `.clawness/rules/` → confirm the new ID retrieves. The `clawness` CLI ships with the
     plugin but isn't on PATH; run it via the wrapper the SessionStart bootstrap writes:
     `CLAW="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/clawness/clawness-cli.sh"`, then
     `bash "$CLAW" query "<a prompt this should match>"` and check the ID appears.
     (Editable/manual installs may use `python -m clawness.cli query ...`.)
   - `.clawness/memory.md` → the entry is one line under `## Lessons`.
3. Only then remove the section from the workspace file.
4. Say what moved and what the file is down to.

If verification fails, put the section back and mark it STAY. A rule that doesn't retrieve
is strictly worse than a paragraph in SOUL.md.

### 4. Finish

- Re-measure and report the before/after per file and in total (and against the caps).
- If anything moved to `.clawness/rules/`, run `bash "$CLAW" lint` — it rejects missing
  fields and vague phrasing, and a rule that fails lint won't survive CI in projects that
  gate on it.
- **Lock in the trade with an eval set.** Moving content from an always-injected file into
  ranked retrieval swaps a guarantee for a probability. Run `/clawness:eval-set` to write a
  case for each thing the trimmed content used to guarantee and confirm hit-rate is 1.0 —
  that is the proof the move was safe, and it catches a later rule edit that stops surfacing.
- Remind the user that OpenClaw re-reads workspace files each session, so the smaller prompt
  takes effect on the next agent run.
- Suggest committing the workspace files as one reviewable change (the moves can then be
  read and reverted as a unit). Note that `SOUL.md`/`AGENTS.md`/`MEMORY.md` are usually
  committed, while `USER.md` is often personal — respect the project's existing gitignore.

## Don't

- Don't run this without the OpenClaw adapter and `.clawness/` retrieval in place — there
  is no cheap destination otherwise.
- Don't start from a passing mention or your own initiative. It is opt-in work.
- Don't move something because it is long. Move it because it is *not identity* — persona
  and user profile STAY however long, conventions and inventories move however short.
- Don't relocate bulk into OpenClaw's own `MEMORY.md`; that is the same per-turn cost.
- Don't rewrite a persona while relocating around it. Trim wording in a separate, visible
  pass if the user wants — a reorganisation that silently reworded SOUL.md is impossible to
  review, and SOUL.md is the one file where wording *is* the behaviour.
- Don't treat the docs-derived filenames, caps, or hook names as verified. Confirm the real
  workspace against a live OpenClaw before acting, and say which parts you couldn't check.
