---
name: claude-md
description: >
  Shrink an oversized CLAUDE.md. Measures what the project's always-loaded
  instructions cost per turn, then works through it section by section —
  cutting what the codebase already says, moving path-specific guidance to
  .claude/rules/ and prompt-specific conventions to .clawness/rules/, and
  leaving load-bearing "don't undo this" content where it is. Destructive
  and deliberate: run it when you have the session to spend.
---

# Shrink CLAUDE.md

CLAUDE.md is loaded by the harness, in full, at the start of every session and
re-read after `/compact`. It is the one context cost nothing can cap: Clawness's
rules block is budgeted, the lessons log is ranked and budgeted, CLAUDE.md is
neither. Files past ~200 lines also measurably reduce how reliably Claude follows
any single instruction in them, so a big CLAUDE.md costs twice — tokens, and the
adherence of the rules you actually cared about.

This skill is the **remedy**. The Clawness SessionStart check is only the
diagnosis: it reports the number once and deliberately refuses to start this work,
because it fires before you've said what you came for. You invoking this skill
*is* the consent that note couldn't ask for.

**Say this before starting.** This is a destructive reorganisation of a file the
user may care about a great deal, and it takes a while. Tell them: it will take
most of a session, nothing is deleted until its replacement is verified, and they
can stop after any single section. Then get an explicit go-ahead.

## First: is this the right tool?

Claude Code ships `/doctor` (alias `/checkup`, v2.1.206+), which proposes CLAUDE.md
trims natively and migrates what remains into skills and nested CLAUDE.md files. It
reports before changing anything.

- **If the project does not use Clawness project rules**, say so and recommend
  `/doctor` instead. It is maintained by the harness and knows more about how
  instructions load than this skill does. Do not compete with it out of loyalty.
- **This skill earns its place when the project uses Clawness**, because it has two
  destinations `/doctor` doesn't know exist: `.clawness/rules/` (retrieved by
  relevance and budgeted, with no line cap and the full rule format) and
  `.clawness/memory.md` (one-line traps). Running both is fine and they compose —
  `/doctor` for the trim, this for the Clawness-specific placement.

## What actually reduces per-turn cost

Only two harness mechanisms do, plus Clawness's own retrieval. Get this right or
the whole exercise is theatre:

| Destination | When it loads | Use for |
| --- | --- | --- |
| `CLAUDE.md` | every session, in full | short, always-true, load-bearing |
| `.claude/rules/*.md` **with `paths:` frontmatter** | when Claude reads a matching file | guidance tied to specific files or directories |
| nested `<subdir>/CLAUDE.md` | when Claude reads files in that subdir | guidance tied to one area of the tree |
| `.clawness/rules/*.yml` | ranked per prompt, inside `CLAW_BUDGET` | conventions tied to a *topic* rather than a path |
| `.clawness/memory.md` | ranked per prompt, ~3 lines | one-line traps that bit once |
| skills | when invoked or judged relevant | multi-step procedures |

**`@path` imports do NOT reduce cost, and neither do `.claude/rules/` files without
`paths:` frontmatter.** Imports are expanded and loaded at launch, recursively to
four hops; un-scoped rules load every session at the same priority as CLAUDE.md.
Reorganising a 9,000-token CLAUDE.md into `@` references is the most common version
of this task, and it moves exactly zero tokens. If the user proposes it, tell them
plainly before they spend the session on it.

## Steps

### 1. Measure

Read `CLAUDE.md`, plus `CLAUDE.local.md` and `.claude/CLAUDE.md` if present — the
harness loads all of them, so the cost is the sum. Report:

- total characters and a rough token estimate (chars ÷ 4; call it rough, it is)
- a per-section breakdown by top-level heading, largest first

The breakdown is the point. "Your CLAUDE.md is 9k tokens" invites a shrug; "your
Architecture section alone is 3.4k tokens and describes files Claude can read" does
not. Do not follow `@path` imports for the measurement unless the user asks — say
that the real number is higher if any exist.

### 2. Classify every section

Go heading by heading and assign one of five verdicts. **Default to STAY.** The
burden of proof is on moving something, not on keeping it.

- **STAY** — anything shaped like *"don't undo this without reading why"*, *"we
  tried X and it broke"*, or a constraint that is invisible from the code. This is
  load-bearing precisely when the prompt gives no hint that it applies, so
  retrieval can't be trusted to surface it. A missed ranked rule is a slightly worse
  answer; a missed "don't undo this" is the regression it existed to prevent. Also
  STAY: build/test commands, and short conventions that differ from tool defaults.
- **CUT** — anything the codebase already answers: directory layouts, dependency
  lists, architecture summaries, file inventories, restatements of `package.json`
  scripts or the `Makefile`, and history that reads as a changelog.
- **→ `.claude/rules/<topic>.md`** — applies only when touching certain files.
  Needs `paths:` frontmatter with globs, or it loads every session and you have
  achieved nothing. Check the globs against a real file in the repo before moving on.
- **→ `.clawness/rules/<domain>/<ID>.yml`** — a durable convention tied to a topic
  rather than a path (naming, error handling, a library's house style). Full rule
  format: `id, domain, severity, tags, triggers, when, rule, violation, correct`.
  `tags` and `triggers` drive retrieval — put the words someone would actually type.
- **→ `.clawness/memory.md`** — one-line traps only, under `## Lessons`, 120 chars
  max, naming the file or flag so it retrieves. If the rationale is the payload, it
  is not a memory entry; the line cap will shred it. Use `.clawness/rules/` instead.

Present the whole classification as a table and **get approval before moving
anything**. Include the projected token saving so the user can judge whether it is
worth their session.

### 3. Move one section at a time, verify, then delete

Never delete the original until its replacement is proven to surface. For each
approved section, in order, smallest first:

1. Write the new file (or add the rule).
2. Verify it:
   - `.clawness/rules/` → `clawness query "<a prompt this should match>"` and
     confirm the new ID appears. If `clawness` isn't on PATH (normal for a
     plugin-only install), use `python -m clawness.cli query ...`.
   - `.claude/rules/` with `paths:` → confirm the glob matches a real file in the
     repo. It loads when Claude reads a matching file, so it cannot be proven from
     inside this turn; say that rather than implying you tested it.
   - `.clawness/memory.md` → the entry is one line under `## Lessons`.
3. Only then remove the section from CLAUDE.md.
4. Say what moved and what the file is down to.

If verification fails, put the section back and mark it STAY. A rule that doesn't
retrieve is strictly worse than a paragraph in CLAUDE.md.

### 4. Finish

- Re-measure and report the before/after.
- If anything moved to `.clawness/rules/`, run `clawness lint` — it rejects missing
  fields and vague phrasing, and a rule that fails lint won't survive CI in projects
  that gate on it.
- Remind the user that new `.claude/rules/` and `.clawness/rules/` files load on the
  next session, not this one.
- Suggest committing the result as one reviewable change, so the move can be read
  and reverted as a unit.

## Don't

- Don't start this from a SessionStart note, a passing mention, or your own
  initiative. It is opt-in work.
- Don't move something because it is long. Move it because it is *conditional*.
- Don't rewrite prose while relocating it. Move it verbatim, then trim in a
  separate pass if the user wants — a reorganisation that also silently reworded
  the rules is impossible to review.
- Don't touch `~/.claude/CLAUDE.md` or a managed-policy CLAUDE.md. This is about
  the project's file.
