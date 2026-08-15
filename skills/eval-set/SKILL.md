---
name: eval-set
description: >
  Build and run a project-specific retrieval eval so changes to your rules or base
  prompt are scored, not eyeballed. Mirrors tests/ground_truth.json + `clawness eval`:
  you write prompt→expected-rule cases, then measure MRR@k and hit-rate before and
  after an edit. Run it after trimming a base prompt into ranked retrieval (see
  openclaw-audit / claude-md), or whenever you change rules and want proof retrieval
  still surfaces what matters.
---

# Score your retrieval, don't eyeball it

When you move guidance out of an always-loaded base prompt (a bloated `CLAUDE.md`, an
OpenClaw `SOUL.md`/`AGENTS.md`) into Clawness's ranked retrieval, you trade a guarantee
for a probability: the content used to be present on *every* turn; now it surfaces only
when the prompt is relevant enough to rank it. That trade is usually right — it is the
whole point of `/clawness:claude-md` and `/clawness:openclaw-audit` — but it is only safe
if you can *check* that the content still surfaces for the prompts that need it.

This skill builds that check. It is the same machinery Clawness gates its own corpus with:
a labelled set of `prompt → expected rule ID(s)` cases, scored by MRR@k and hit-rate via
`clawness eval`. The output is a number that moves when retrieval regresses, so a rule edit
that quietly stops surfacing shows up instead of hiding until someone hits it in anger.

This is harness-agnostic — it evaluates Clawness retrieval, which is identical under Claude
Code and under the OpenClaw adapter. Nothing here touches OpenClaw's own prompt.

## When to run it

- **After trimming a base prompt** into `.clawness/rules/` — write a case for each thing
  the moved content used to guarantee, then confirm hit-rate is 1.0 before you delete the
  original. This is the verification step `openclaw-audit`/`claude-md` point at.
- **After editing or adding rules** — re-run to confirm you didn't push an existing rule
  out of the top-k for prompts that depend on it.
- **As a CI gate**, once the set is stable — the same `--floor-mrr`/`--floor-hit` floors
  Clawness uses on its own eval.

## Steps

### 1. Create the case file

Author `.clawness/eval/cases.json` in the shape below (a filled-in copy of this skill's
`cases.template.json`). Write it directly — the plugin root isn't reachable from skill
Bash, so don't try to `cp` the template from the plugin dir.

The shape (identical to `tests/ground_truth.json`):

```json
{
  "queries": [
    { "q": "how should I handle errors in our service layer", "expect": ["SVC-ERR-001"] }
  ]
}
```

- `q` — a prompt someone on this project would **actually type**. Write real phrasings,
  not the rule's own title; retrieval is lexical + concept-expansion, so the words matter.
- `expect` — the ranked rule ID(s) that should appear in the top-k. Any one of them
  counts as a hit, so list alternatives when more than one rule legitimately answers.
- `expect` targets **ranked** rules only. Mandatory rules (`_mandatory/`) are injected
  every turn and are not scored — don't put them here.

> Files under `.clawness/` are gitignored by Clawness's default allowlist. If you want the
> eval set committed and shared, add `!.clawness/eval/` to `.gitignore`.

### 2. Write good cases

- **One case per thing you care about surfacing.** If you moved five conventions out of a
  base prompt, that is at least five cases.
- **Phrase from the asker's side.** "make focus states visible for keyboard nav" beats
  "accessibility rule for focus" — the first is what a developer types.
- **Add near-miss cases too.** A prompt that should *not* pull a rule is worth a case with
  the correct rule expected, so a change that over-broadens a rule shows as a new miss
  elsewhere.
- **Keep the set stable.** Its value is as a fixed baseline; churning the cases every edit
  defeats the point. Grow it deliberately when you add a rule area.

### 3. Run it

The `clawness` CLI ships with the plugin but isn't on PATH; use the wrapper the
SessionStart bootstrap writes each session:

```bash
CLAW="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/clawness/clawness-cli.sh"
bash "$CLAW" eval --data .clawness/eval/cases.json
```

(Editable/manual installs may use `python -m clawness.cli eval --data ...`.)

It prints MRR@k, hit-rate, and every miss with what was expected versus what actually
ranked — which tells you exactly which case to fix and whether the fault is the rule's
`tags`/`triggers` or the case's phrasing.

### 4. Interpret and act on misses

For each miss (`expected [X]; got [...]`):

- If the right rule is missing from the list entirely, its `tags`/`triggers` don't carry
  the words the prompt uses — widen them (or the concept groups) rather than the case.
- If the right rule is present but below the cutoff, something else is out-ranking it;
  check whether a newly-added rule overlaps (`clawness audit-rules --overlap`).
- If the *case* is unrealistic (nobody would phrase it that way), fix the case, not the
  corpus.

Never "fix" a miss by deleting the case. A case you can't satisfy is telling you the
content you trimmed does *not* reliably surface — put it back in the base prompt and mark
it STAY.

### 5. Gate it (optional)

Once the set is stable and green, wire floors into CI so a regression fails the build:

```bash
bash "$CLAW" eval --data .clawness/eval/cases.json --floor-mrr 0.85 --floor-hit 0.95
```

Pick floors from your *current* passing numbers, not aspirational ones — a floor above
where you are just fails every build. `eval` exits non-zero when a floor is breached, so
it drops into a CI step directly.

## Don't

- Don't score mandatory rules — they're always injected; measuring them is noise.
- Don't chase MRR by rewording cases to match rule titles. The cases must stay realistic
  or the number stops meaning "will this surface for a real prompt".
- Don't set floors you don't currently meet.
- Don't treat a green eval as proof a base-prompt trim was safe on its own — it proves the
  *listed* prompts surface the content. Coverage is only as good as your cases; write one
  for everything the trimmed content used to guarantee.
