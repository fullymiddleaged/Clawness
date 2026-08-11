---
name: refresh
description: >
  Bring this project's Clawness rules up to date with the framework version it
  actually runs. Establishes what is installed and what the codebase uses, looks
  up what changed since the rules were verified, and writes version-corrected
  overrides into .clawness/rules/ — reporting the list and stopping for approval
  before it writes anything. Run it when the SessionStart version-gap note fires,
  or whenever a major upgrade lands.
---

# Refresh rules for a framework version

Clawness rules can carry a version stamp — `applies_to`, `verified`, `sources` —
recording the range they were established against. When a project runs past that
range, a SessionStart note says so once and deliberately does nothing else.

This skill is the **remedy**. The note is only the diagnosis: it fires before you
have said what you came for, so it cannot propose a session's worth of destructive
work. You invoking this skill *is* the consent it couldn't ask for.

**This is the only path permitted to author rule files.** Nothing automatic may
invoke it — not the note, not a hook, not your own initiative mid-task. An earlier
version of this feature let the automatic path write rules and it produced heaps of
them, consuming a session opened for something else.

**Say this before starting.** It takes a while, it writes files into the project,
and the rules it writes will govern every future prompt in this repo. Get an
explicit go-ahead, and tell them nothing is written until they approve the list.

## Argument

`/clawness:refresh <domain>` — one domain (`nextjs`, `react`, `python`, `fastapi`,
…). With no argument, use the detected stack, and if that spans several domains ask
which one. **One domain per run.** Reviewing several at once is how a careful pass
becomes a bulk stamp, which is the failure this whole feature exists to prevent.

## Steps

### 1. Establish what is actually installed

The **lockfile is authoritative; the manifest range is not.** `^17.0.0` in
`package.json` is a permission, not a fact — read `package-lock.json`,
`pnpm-lock.yaml`, `yarn.lock`, `uv.lock`, `poetry.lock`, or `pip freeze` output for
the resolved version. This is `GEN-INSTALLED-VER-001`'s procedure, and it matters
more here than usual: the rule you write will be asserted to future sessions as
fact.

If you cannot resolve an exact version, say so and stop. Do not write a rule
against a version you had to guess.

### 2. Establish what the codebase actually uses

Version alone is not the answer. A Next.js 17 project still on the Pages Router
must not be given App Router rules, and a project that never touches the changed
API needs no rule at all.

Grep for the constructs each candidate rule would govern **before** writing it. A
rule that doesn't apply here is not harmless — it competes for a top-k slot on
every prompt, forever, and displaces one that does apply.

### 3. Look up what changed

Between the stamped range and the installed major. Prefer, in order:

1. The framework's own migration guide / upgrade guide for that major.
2. The official changelog or release notes.
3. The current official docs page for the API in question.

**Not recalled knowledge.** A model's sense of "what's current" lags real releases
by exactly the gap this feature exists to close — using it here would launder a
guess into a stamped, sourced rule, which is worse than the stale rule you started
with. If you cannot reach documentation, report that and stop.

Record the URL you used for each finding. It becomes the rule's `sources`, and it
is what makes a generated stamp auditable rather than asserted.

*(This step is shared with `clawness audit-rules`' review pass — same lookup,
different destination: this writes to one project, that fixes the corpus upstream.)*

### 4. Classify each rule in the domain

Read every rule in the domain, global and project. For each, one verdict:

| Verdict | Action |
| --- | --- |
| **Still true** at the installed version | Nothing. Say so — the honest majority. |
| **Changed** | Override under its **own id** (see below). |
| **Now the framework default** | Override to say so, or drop it — a rule telling you to do what the framework already does is pure noise. |
| **New concept, no rule exists** | New id, new rule. |
| **Can't settle it** | Leave alone, report as unsettled. A gap is truthful; a guess is not. |

### 5. Report the list and stop

Present the classification as a table with the proposed id, verdict, and source URL
for each. **Write nothing until the user approves.**

Cap what you propose at **6 rule files** unless the user asks for more. That number
is a judgment call, not a measurement: it is small enough that the review stays a
review, and a major migration that genuinely needs more is a conversation worth
having rather than a batch to wave through. If more than six rules are affected,
propose the six that matter most and say what you left out.

### 6. Write the approved overrides

Into `<project>/.clawness/rules/<domain>/<ID>.yml`.

**Override a superseded rule under its OWN id.** `NX-ROUTE-001` for v17 replaces
`NX-ROUTE-001` for v14. A new id would leave both in the corpus, one describing 14
and one 17, competing for the same slot — strictly worse than the stale rule alone.
`add_rules` replaces by id, so the project copy wins outright. A genuinely new
concept with no existing rule does get a new id.

Full rule format, plus the stamp:

```yaml
id: NX-ROUTE-001
domain: nextjs
severity: warning
tags: [routing, app-router]
triggers: [route, app router, layout]
when: Defining routes in this project.
rule: <what to do, precisely, at the installed version>
violation: <the v14 shape, if it helps>
correct: <the v17 shape>
applies_to: {"Next.js": "17"}
verified: "<YYYY-MM, this month>"
sources: ["<the URL from step 3>"]
# Generated by /clawness:refresh for Next.js 17 on <YYYY-MM-DD>.
```

Three things that are not optional:

- **Stamp only the major you actually checked.** `"17"` if you checked 17. Never a
  span reaching back through majors you didn't verify — that converts an honest gap
  into a false assurance, and it is the exact failure per-rule stamping exists to
  prevent. Where evidence doesn't settle a bound, narrow rather than widen: too
  narrow produces a visible false warning that gets corrected, too wide produces
  silence that doesn't.
- **The label must be the detector's** — `"Next.js"`, not `"next"` or `"NextJS"`.
  A label no detector emits never matches, so the rule silently never goes stale
  again. `clawness lint` catches this; run it.
- **The header comment naming the command, version and date.** A later audit has to
  be able to tell generated rules from hand-written ones at a glance.

Stamping the rules you write closes the loop: when this project later moves 17→18,
**your own generated rules go stale and get flagged by the same check.**

### 7. Verify

The `clawness` CLI ships with the plugin but isn't on your PATH; run it via the
wrapper the SessionStart bootstrap writes each session:
`CLAW="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/clawness/clawness-cli.sh"`. If `$CLAW`
is missing the bootstrap hasn't run — start a fresh session. (Editable/manual
installs may use `python -m clawness.cli` instead.)

- `bash "$CLAW" lint` — validates the stamp mechanically: unknown framework label,
  unparseable range, future `verified` date.
- `bash "$CLAW" query "<a prompt this should match>"` and confirm the new id
  appears. A rule that doesn't retrieve is worse than the stale one it replaced.
- Tell the user the new rules load on the **next** session, not this one.
- Suggest committing `.clawness/rules/` — it is meant to be shared, unlike the
  handoff and the ledgers.

## Don't

- Don't run this unprompted, or fold it into another task. It is opt-in work.
- Don't write rules from memory. Every rule needs a `sources` URL you actually read.
- Don't widen a range to cover majors you didn't check.
- Don't rewrite rules that are still true. The honest outcome of a refresh is often
  "two changed, six fine".
- Don't edit the **global** corpus from here. This writes project overrides. If a
  rule is wrong upstream, say so — repeated per-project overrides of one id are the
  signal it needs fixing at source.
