# Clawness

[![CI](https://github.com/fullymiddleaged/Clawness/actions/workflows/ci.yml/badge.svg)](https://github.com/fullymiddleaged/Clawness/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/fullymiddleaged/Clawness)](https://github.com/fullymiddleaged/Clawness/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)

**Install once. Your coding agent gets the right rules for every task, without you having to mention or explain them.**

Clawness is a Python backed plugin compatible with **Claude Code and [OpenClaw](https://openclaw.ai)**.
It's designed for full stack devs or researchers who often work across various code bases at short 
notice, it works out (per prompt) which of your coding rules matter for the current task and puts 
just those into context, so you never have to repeat your standards or dump them all into a flat config 
file per repo.

But it also does heaps more!

What you get:

- **215 rules** across 29 domains: general coding, plus scientific computing, machine
  learning, research method, and building with LLMs. Only the ones that match your task
  are injected.
- **7 review sub-agents**: security red/blue team, code critic, architecture challenger,
  and more.
- **A plan-approval gate** before the first edit of a session, on by default.
- **Security**: a deterministic vulnerability scan with an accumulating findings ledger
  (so an audit converges in 1-2 passes, not 5-10), a red/blue-team `/clawness:audit`, a
  guard on risky tool calls, and a trust ledger for skills, agents, and MCP servers.
- **Session continuity**: a per-project lessons memory, a warning when your context window
  is filling up, and a handoff the next session picks up on its own.
- **Low token cost.** Only the matching rules are injected, never the whole set. A typical
  turn is around 1,700 tokens instead of loading all 215 rules every turn.

Under 1 MB, no services, no ML models, about 3 ms per prompt. Pure Python, with PyYAML as
the only dependency.

> **Not just for shipping code.** 61 rules cover scientific computing, machine learning,
> and research method (`science`, `research`, `ml`, CFD, Julia, Fortran, MATLAB, R),
> injected the same automatic way. See [For Researchers and Scientists](#for-researchers-and-scientists).

Inspired by [infinri/Writ](https://github.com/infinri/Writ), rebuilt from ~2 GB of
infrastructure to pure Python.

---

## Contents

- [Quickstart](#quickstart) · [Install](#install)
- [What problem does this solve?](#what-problem-does-this-solve) · [How it works](#how-it-works)
- [Using it](#using-it): [context watch](#context-watch-claude-code-on-by-default) · [handoff](#session-handoff-claude-code-on-by-default) · [version-gap](#version-gap-detection-on-by-default) · [plan gate](#plan-gate-claude-code-on-by-default) · [session-start checks](#session-start-checks) · [session security](#session-security-access-guard--trust-ledger-on-by-default)
- [For researchers and scientists](#for-researchers-and-scientists)
- [Per-project setup](#per-project-setup) · [Writing rules](#writing-rules) · [Sub-agents](#sub-agents)
- [CLI reference](#cli-reference) · [What ships](#what-ships) · [Configuration](#configuration)
- [How it compares](#how-it-compares) · [Troubleshooting](#troubleshooting)

---

## Quickstart

### Claude Code

Two commands plus a session (or IDE) restart. Here, we assume you already have Python 3.10+ available on your PATH.

**1. Install** (from any Claude Code session):

```bash
claude plugin marketplace add fullymiddleaged/clawness
claude plugin install clawness@clawness
```

**2. Restart Claude Code** (or run `/reload-plugins`) so the hooks load.

**3. Let first-run setup finish.** On your first session, a background hook installs the
one Python dependency (**PyYAML**). This needs **Python 3.10+ on your PATH** and takes a few
seconds. There are no models to download.

**4. Verify** by asking Claude *"what clawness rules do you see in your context?"*, or run
`/clawness:status`. If it describes the injected rules, you're live.

> `clawness@clawness` isn't a typo. It's `plugin@marketplace`, and both are named
> *clawness*. No Python 3.10+? See [Installing Python](#installing-python). Without it the
> plugin installs but injects nothing, and Claude tells you so on your first session.

### OpenClaw (experimental)

Clawness runs inside OpenClaw through a thin TypeScript adapter that shells out to the same
Python engine and rules, so there's no second copy to keep in sync.

**Requires** Node ≥ 22.22.3, Python 3.10+, and **OpenClaw ≥ 2026.3.24-beta.2** (the first
release with the plugin API the adapter needs). An older OpenClaw is rejected at install
with a clear error. Upgrade with `npm i -g openclaw`.

```bash
openclaw plugins install git:github.com/fullymiddleaged/Clawness
openclaw gateway restart
```

**What works on OpenClaw today:** rule and memory injection, the session-start notes, and
the access guard (block/ask). OpenClaw plugins can't contribute Claude Code skills or
sub-agents, so instead of the `/clawness:*` skills you get three OpenClaw-native
**commands** — `/clawness-status`, `/clawness-query`, and `/clawness-audit-rules` —
implemented in the adapter over the same Python CLI. The rest of the skills, including
`add` and `refresh`, stay Claude-Code-only (they need the model driving its own file tools,
which the plugin-command surface can't provide).

OpenClaw also gets three capabilities with no Claude Code equivalent, riding hooks Claude
Code doesn't have: **install-time trust vetting** (a skill/plugin is scanned for injection
and exfil tells as it installs and findings are surfaced — advisory by default, with opt-in
blocking via `CLAW_INSTALL_BLOCK=1`), **re-orientation
after compaction** (when OpenClaw squashes context, Clawness re-injects the handoff and
stack notes so a mid-task session recovers), and **`.clawness/memory.md` as a searchable
memory corpus**. A few Claude Code features stay dormant on OpenClaw because they read
Claude-specific state: the plan gate and the model-tier advisor (and the context watch,
whose job the compaction re-orientation now does natively). Full setup is in
[`openclaw/README.md`](openclaw/README.md).

---

## What Problem Does This Solve?

A coding agent forgets your conventions between turns, trusts every tool call you've
allowed, and gives you no cheap way to enforce a standard or rein in a runaway edit.

Take rules like *"parameterized SQL only," "async I/O end-to-end."* Without Clawness you
either dump them all into a config file (wastes tokens every turn) or mention them by hand
(you forget, the agent forgets). Clawness scores every rule against your task and injects
only the few that fit, plus an always-on mandatory set. So a developer moving between
frontend, backend, and SQL always has the right rules and never the rest. The same hook
carries the rest of what's [in the box](#clawness), each covered under [Using It](#using-it).

**Make them *your* standards.** The 215 built-in rules are a starting point. Run
`/clawness:add describe your rule` and Clawness writes the tagged YAML for you, or drop
`.yml` files in `.clawness/rules/`. Commit `.clawness/rules/` and `.clawness/memory.md` to
share them with your team. → [Per-Project Setup](#per-project-setup) · [Writing Rules](#writing-rules)

---

## How It Works

```
your prompt
   │  hook fires automatically, before your agent sees it
   ▼
score every rule against the task   (global rules + <project>/.clawness/rules/)
   │  BM25 + TF-IDF + RRF + concept expansion, ~3 ms, pure Python
   ▼
your agent sees:  mandatory rules (always) + the few that matched + your prompt
```

Concepts bridge synonyms, so `login ↔ auth ↔ jwt` all match the same rule. No models, no
downloads, and you never touch any of it.

Rules come in two layers: **global** (installed once, every project) and **project**
(`<your-project>/.clawness/rules/`, optional, commit them to share with your team).

### The retrieval engine

Pure Python, one dependency (PyYAML), nothing to download at query time:

- **BM25 and TF-IDF, fused with Reciprocal Rank Fusion.** Two word-matching methods that
  fail in different places, so a rule is found whether your prompt shares its exact terms or
  just its general vocabulary.
- **Concept expansion (32 groups)** maps synonyms onto shared markers
  (`login ↔ auth ↔ jwt`, `postgres ↔ db ↔ query`), the reach a vector model gives but
  instant and dependency-free. Light stemming collapses plural and verb forms.
- **Project memory is searched the same way**, so a long lessons log stays a few lines per
  turn. See [Project Memory](#project-memory).

**Quality is measured.** `clawness eval` scores retrieval against 249 known-answer
questions: **MRR@5 = 0.990**, **hit-rate = 1.000**, both CI-enforced.

**Cost.** About 2 ms and roughly 850 tokens for the always-on mandatory block, plus the few
matched rules (a typical turn is near 1,700). The mandatory block renders in full on prompt
1 and every fifth after; in between it's a one-line list of ids. Run `clawness stats` for
your exact estimate.

---

## Install

### Installing Python

Clawness needs **Python 3.10+** on your PATH. Check first:

```bash
python --version     # or: python3 --version
```

If that prints `3.10` or higher, skip to the [Quickstart](#quickstart). Otherwise:

**Windows.** Install from [python.org/downloads](https://www.python.org/downloads/) and
**tick "Add python.exe to PATH"** on the first screen. It's easy to miss, and it's the
usual reason `python` "isn't found" later. Or with winget:

```powershell
winget install Python.Python.3.12
```

**macOS.** Usually preinstalled as `python3`. If not: `brew install python`.

**Linux.** Use your package manager: `sudo apt install python3` (Debian/Ubuntu),
`sudo dnf install python3` (Fedora/RHEL), or `sudo pacman -S python` (Arch).

Then open a **new** terminal so PATH refreshes, and re-run the check.

> **Windows Store stub.** If the Microsoft Store `python.exe` placeholder is on your PATH,
> it looks like a working Python but isn't. Remove it under *Manage App Execution Aliases*,
> or install real Python from python.org.

### Plugin install (recommended)

See the [Claude Code](#claude-code) and [OpenClaw](#openclaw-experimental) steps in the
Quickstart above. The Claude Code plan gate and access guard are on by default; disable
with `CLAW_NO_PLAN_GATE=1` / `CLAW_NO_ACCESS_GUARD=1`.

### Manual install

For more control, or if the plugin system isn't available. Needs Python 3.10+ and Claude
Code. No Docker, Node, databases, or ML models.

**Windows (PowerShell):**

```powershell
git clone https://github.com/fullymiddleaged/clawness.git "$env:USERPROFILE\.claude\clawness"
cd "$env:USERPROFILE\.claude\clawness"
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**macOS / Linux:**

```bash
git clone https://github.com/fullymiddleaged/clawness.git ~/.claude/clawness
cd ~/.claude/clawness
bash install.sh
```

The installer checks Python, does an editable `pip install` (adding the `clawness` command
and PyYAML), verifies and lints the rules, runs a test query, copies the agents and skills
into `~/.claude/`, and writes the hook config into `settings.json`, the same set the plugin
install wires. Running it twice is safe: it won't duplicate hooks or overwrite settings.

### Uninstall

**Plugin install.** Use the CLI (the `/plugin` menu's remove is unreliable):

```bash
claude plugin uninstall clawness
claude plugin marketplace remove clawness   # optional, also drops the marketplace
```

Add `--prune` to clean up dependencies, and `--scope project` if you installed it at
project scope.

**Manual install.** Don't just delete the folder: that leaves hook entries pointing at
missing scripts, which error on every prompt. Run the uninstaller first, then delete:

```bash
# macOS / Linux
bash ~/.claude/clawness/uninstall.sh
rm -rf ~/.claude/clawness

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\clawness\uninstall.ps1"
Remove-Item -Recurse -Force "$env:USERPROFILE\.claude\clawness"
```

Two things are left in place on purpose: the `pyyaml` pip package (shared with other
tools), and any per-project rules in each project's `.clawness/`.

---

## Using It

**Just use your agent normally.** After install, the hook fires silently on every prompt.
You don't type anything special or reference rules by hand. Your agent sees the relevant
rules in its context and follows them.

### What your agent sees

When you type *"implement the user registration endpoint"*, this gets prepended to the
conversation:

```
--- CLAWNESS RULES ---
# MANDATORY (9)
[ENF-SEC-001] RULE: All secrets must come from environment variables...
[ENF-SEC-004] RULE: Use a proven auth library...
...
# RELEVANT (2)
[FA-PYDANTIC-001] RULE: Define Pydantic models for every request body and response...
[GEN-VALIDATE-001] RULE: Validate and sanitize all external input at the boundary...
--- END CLAWNESS RULES ---
```

The mandatory rules always appear; the ranked ones change with your prompt. (Scores and
timing are hidden by default so they don't defeat prompt caching. Set `CLAW_VERBOSE=1` to
see them.)

**Match score and project awareness.** A ranked rule appears only on a genuine match;
anything below a minimum score is dropped as coincidence. Rules for languages and frameworks
your project doesn't use need a higher score, so a vague prompt in a Python repo won't pull
in SQL or React noise. `science` and `research` are never held back by project type but must
match strongly; CFD, Julia, Fortran, MATLAB, and R sit at the highest bar, since their words
(solver, converge, residual, vectorize) are also everyday programming words. Tune with
`CLAW_MIN_RELEVANCE` and friends (see [Configuration](#environment-variables)).

### Output compression

When your agent runs a bash command that produces 80+ lines (test suites, builds, long
logs), a hook extracts the error and failure lines with context and a summary, keeping the
context clean.

**Content reads are exempt.** `cat`, `head`/`tail`, `grep`/`rg`, `git diff`, `git show` and
similar return content the agent reasons about line by line, so they pass through whole.
Past about 400 lines they're truncated from the end with a note saying how many lines are
missing and to use Read/Grep for the rest.

### Context watch (Claude Code, on by default)

Long sessions get worse before they break, usually without warning. Clawness reads your
session's transcript each prompt and tells you when to move on:

- **~70% full:** a brief mention that a fresh session may be worth it soon.
- **~85% full:** a recommendation to start fresh, plus an offer to write a
  [handoff](#session-handoff-claude-code-on-by-default) and save any durable lesson to
  `.clawness/memory.md`.
- **Filling fast:** a single turn that adds a big chunk of the window is flagged while you
  still have room to act.

Each level fires at most once per session. The token count is read from the transcript; the
window *size* is inferred from the `[1m]` marker on your model and corrected upward if usage
exceeds it. Set `CLAW_CONTEXT_LIMIT` if the guess is wrong, or off with
`CLAW_NO_CONTEXT_WATCH=1`.

### Session handoff (Claude Code, on by default)

When the context watch recommends a fresh session, it offers to write a handoff first, and
the next session in that project **picks it up on its own**:

```
Session 1, 87% full
  → "Context is nearly full. Want me to write a handoff before you start fresh?"
  → writes .clawness/handoff.md

Session 2, first message
  → "Last session left off mid-refactor of core.py, tests failing on budget truncation,
     nothing committed. Next step was fixing the tail truncation. Want to pick that up?"
```

You don't have to remember the file exists or know its path; a session-start hook finds it
and injects the content.

- **Say "carry on" and Claude carries on**, straight to the next step. Open with something
  else and it just tells you where things stood and waits.
- **Blockers go under `## Open questions`**, so Claude asks those and only those. Usually
  it says "none".
- **Writing a new handoff archives the old one** to `.clawness/handoffs/done/`; nothing is
  deleted. Ask for one any time with "write a handoff".
- **Gitignore it** (the [ignore block](#per-project-setup) Clawness offers covers it). It's
  a personal note, not shared knowledge; durable lessons go in `.clawness/memory.md`.

Off with `CLAW_NO_HANDOFF=1`.

### Version-gap detection (on by default)

When a framework ships a new major, rules written for the old one don't notice, because a
major bump keeps the words ("route", "cache") and changes their meaning. So a rule can record
what it was checked against:

```yaml
applies_to: {"Next.js": "13-15"}    # versions established against
verified: "2026-08"                 # when someone actually checked
sources: ["https://nextjs.org/docs/app/building-your-application/routing"]
```

When your project runs a version past that range, you get one sentence at session start and
your agent is told to check current docs first. Only a *verified* stamp warns, once per gap,
re-arming when the version moves. It covers the ~14 frameworks Clawness watches (Next.js,
React, TypeScript, Django, FastAPI, Pydantic, SQLAlchemy, NumPy, pandas, and more); **23
rules ship stamped today**, the rest silent. Run **`/clawness:refresh <domain>`** to act on a
warning: it checks what your code uses, reads the migration guide, and writes version-corrected
overrides into `.clawness/rules/` on your approval. Off with `CLAW_NO_STALENESS_NOTE=1`.

### Plan gate (Claude Code, on by default)

Before the first edit of a session, Clawness asks rather than editing blind, riding Claude
Code's plan mode. Approve a plan (Shift+Tab), or approve the native "proceed without a plan?"
dialog if the agent edits first; either clears the gate for the session, so you're asked at
most once. Headless is the same: `--permission-mode plan` clears it, and `acceptEdits`/`auto`/
`dontAsk`/`bypassPermissions` count as pre-approved.

Turn it off globally with `CLAW_NO_PLAN_GATE=1` or `{"plan_gate":{"enabled":false}}` in
`~/.claude/clawness/config.json` (there's no per-project switch, on purpose). `clawness plan
status` reports the state.

### Session-start checks

At the start of a session, a few one-line notes orient your agent. None of them change your
files without asking.

- **Git check.** If the project isn't a git repo, Claude offers to `git init` (never
  without your say-so). It looks upward and a few levels down, so a monorepo parent won't
  trigger a false "no git" nudge. Off: `CLAW_NO_GIT_CHECK=1`.
- **Stack note.** Detects your stack from its files and injects a line like *"Detected
  project stack: Next.js 14.2, React 18.3, TypeScript 5.4"*, so your agent knows the
  ecosystem and which major versions. Versions it can't read are left off, not guessed.
  Off: `CLAW_NO_STACK_NOTE=1`.
- **Changelog.** If a `CHANGELOG.md` exists, a note reminds Claude to add the line as part
  of the work rather than at release time. If it doesn't, Claude offers **once, ever** to
  create one. Off: `CLAW_NO_CHANGELOG_CHECK=1`.
- **Model-tier check (Claude Code).** On the first prompt, Clawness mentions once if your
  model tier looks mismatched to the task: a small tier on deep work (architecture, security,
  diagnosis) or your top tier on a trivial edit. It suggests, never switches. Off:
  `CLAW_NO_MODEL_ADVISOR=1`.
- **CLAUDE.md size.** If your `CLAUDE.md` grows past ~6,000 tokens (it's loaded in full
  every turn), a note tells Claude to give you the number and point you at the tools that
  fix it. It asks once per project. Off: `CLAW_NO_CLAUDE_MD_CHECK=1`.

### Security audit (deterministic scan + accumulating findings ledger)

An LLM security scan is non-deterministic: run it five times and you get five different
subsets, because the model *wanders* to different files and free-associates about risk
each run. Teams paper over this by re-scanning 5-10 times. The variance is almost all in
**discovery, not judgment** — so Clawness makes discovery deterministic and reduces the
model to adjudicating a fixed list.

**`clawness scan`** is a regex/lexical enumerator (zero LLM tokens, identical every run)
that finds sink/source candidates — SQL and command injection, unsafe deserialization,
code eval, XSS, path traversal, broken object authz, hardcoded secrets, weak crypto, SSRF
— across Python, JavaScript/TypeScript, Go, Java/Kotlin/Scala, Ruby, C# and PHP, each
tagged with a CWE, a mapped rule, and a **stable id**. Every scan merges into an
accumulating ledger (`.clawness/security/findings.json`): a candidate stays `new` until
you adjudicate it, a removed sink becomes `gone` (remembering its verdict so it is never
re-litigated), and a **coverage** signal tells you when every candidate has been looked at.
That convergence — not a fixed number of re-runs — is when to stop.

```bash
clawness scan                 # enumerate + accumulate the ledger, print coverage
clawness scan --new-only      # just what hasn't been adjudicated yet
clawness scan status          # coverage without re-scanning
clawness scan --fail-on high  # opt-in CI gate on unresolved findings (report-only otherwise)
clawness scan --sarif out.sarif  # also fold in bandit/semgrep/CodeQL output (see below)
```

If your project already runs a SAST tool, drop its **SARIF** output anywhere in the tree
(or pass `--sarif <path>`) and the scan folds those findings in too — re-keyed to stable
ids, mapped onto the same finding classes, and deduped against the native hits. No SAST
tool need be installed; Clawness ingests the `*.sarif` output only, so PyYAML stays the
one dependency.

> **Plugin install (most users): you don't type these.** The `clawness` CLI ships only
> with the manual install; on the plugin path you run the audit through **`/clawness:audit`**
> (below), which invokes the scan for you via the bundled wrapper. The commands above are
> for the manual-install CLI and for CI.

**`/clawness:audit`** ties it together and Claude reaches for it on its own when you ask
for a security review: it runs the scan, then the **red team** adjudicates only the *new*
candidates (and hunts for what the enumerator can't see — logic flaws, auth bypass, CVEs
this month), the **blue team** proposes fixes, and both write verdicts back to the ledger.
Run it twice and the second pass only looks at what's new.

On its own it's a **tripwire, not a SAST engine** (its own enumerator over-reports and
misses cross-file taint) — plus real SAST whenever you have SARIF output to feed it. Either
way a human/LLM adjudication pass and the ledger sit on top. The ledger is **gitignored by
default** — it records where the vulnerabilities are.
Opt out of the enumerator with `CLAW_NO_SCAN=1`.

The corpus also ships a `security/` rule domain (SQLi, XSS, SSRF, path traversal, authz,
crypto, dependency safety) plus the mandatory `ENF-SEC-*` rules, injected automatically on
security-shaped prompts.

### Session security (access guard + trust ledger, on by default)

Two hooks defend the session against the agent's own tool calls: text hidden in a file or
page that tricks the agent into leaking data or deleting something, a tampered skill, or you
approving prompts without reading them. They add roughly zero tokens unless they fire.

**Access guard.** Looks at each Bash/Write/Edit/Read call and, for the risky ones, makes you
decide **even when you've already allowed that tool** (a hook overrides your permission
list). Two outcomes:

- **`ask`** shows a confirmation. It covers commands that are normal in one place and risky
  in another: pipe-to-shell (`curl … | sh`), `git push --force` (but not `--force-with-lease`),
  writes outside the project, reads of credential files outside it (`~/.ssh`, `~/.aws`),
  named package installs, cloud uploads (`aws s3 cp`, `gsutil`, `az blob`), and any network
  call to an outside server carrying data or a token. Asked once per destination per session.
- **`deny`** is a hard block with no override: cloud-metadata endpoints, a recursive delete
  of a filesystem root/home/system dir, reading a local secret into a network command,
  uploading a secret, or piping one command's output into an upload to a server that appears
  nowhere in your code. To proceed, run it yourself or set `CLAW_NO_ACCESS_GUARD=1`.

It stays out of normal work: reading your own `.env`, plain API GETs, lockfile installs
(`npm ci`), and local traffic are all silent. It's a **tripwire, not a sandbox** (it
pattern-matches tool calls, so someone determined can slip past it); the real boundary is a
container with an egress allowlist. It catches honest mistakes, copy-pasted installers, and
injected attacks (the kind behind 2026's npm credential-stealing worms and fake VS Code AI
extensions) by turning each into a question or a flagged change.

**Trust ledger.** Fingerprints your project's skills, agents, commands, and MCP servers,
then tells you when one appears or changes between sessions. `clawness audit-skills` scans
those files for signs of hidden instructions on demand.

Opt-outs: `CLAW_NO_ACCESS_GUARD=1`, `CLAW_NO_TRUST_LEDGER=1`.

### Verify it's working

- **Is it live?** Run `/clawness:status`, or ask *"what clawness rules do you see in your
  context?"*
- **Watch first-run setup:** launch once with `claude --debug`. Hook output isn't shown
  otherwise.
- **Install record:** the first session logs each step to `bootstrap.log` in the plugin's
  data directory.

---

## For Researchers and Scientists

Clawness isn't only for shipping software. 32 rules across `science/` and `research/` cover
physics, maths, and engineering practice; 21 more cover simulation and the languages the work
is written in (CFD, Julia, Fortran, MATLAB, R); and an 8-rule `ml/` domain covers training
and evaluating models. One install covers them all, however many repos your work spans.

**Catching errors that survive review.** Ask a question and the relevant rule arrives with
it:

| You ask | You get |
|---|---|
| *"check the units in this equation"* | `SCI-UNITS-001`: carry units, check dimensional consistency first |
| *"is this p value significant"* | `SCI-STATS-001`: multiple comparisons, effect size with a CI, not a bare p |
| *"my simulation results look wrong"* | `SCI-VALIDATE-001`: validate against a known analytic case first |
| *"make my numerical results reproducible"* | `SCI-REPRO-001`: pin seed, versions, data revision; record the command |
| *"is this idea actually novel"* | `RES-NOVELTY-001`: run the negative search; most reinvention is a vocabulary mismatch |
| *"check these references are real"* | `RES-CITECHECK-001`: resolve each DOI and read the source; never trust an AI-generated citation |
| *"which reporting checklist for this study"* | `RES-REPORTING-001`: PRISMA / CONSORT / STROBE / ARRIVE by study type |
| *"is this matrix solve trustworthy"* | `SCI-LINALG-001`: check the condition number; `lstsq` over `inv` when ill-conditioned |
| *"the solver isn't converging"* | `CFD-CONVERGE-001`: falling residuals aren't a converged answer; show grid convergence |
| *"speed up this MATLAB loop"* | `ML-VECTOR-001`: preallocate; `x(end+1)` copies the whole array every iteration |
| *"my cross-validation accuracy looks too good"* | `MLD-LEAKAGE-001`: fit every transform inside a Pipeline within CV |

**Research method, not just code.** `research/` covers stating a falsifiable question first,
citing the source you actually opened, separating what a source says from what you inferred,
date-bounding a literature sweep, and structured synthesis (established / contested / open).
It also covers integrity and reporting against recognised standards: verifying every citation
exists and supports its claim (COPE/ICMJE), matching the checklist to the study type (PRISMA,
CONSORT, STROBE, ARRIVE), a data/code-availability statement with a DOI, and pre-registration.

**Training and evaluating models.** The `ml/` domain covers what separates a real result
from an optimistic one: data leakage, cross-validation discipline (nested CV, grouped/temporal
splits), a metric chosen before results and measured against a baseline, class imbalance,
calibration, reproducibility, and overfitting. It's detected from the modelling libraries a
project imports (scikit-learn, XGBoost, PyTorch, statsmodels), so it reaches any model-training
codebase, scientific or not. (Its rule ids use `MLD-`; `ML-` is the MATLAB domain.)

**It works where you work.** Clawness usually holds back rules for languages you don't use,
but `science/` and `research/` are exempt: a directory with only `paper.tex`, or nothing yet,
still gets them. Detection stacks, so `paper.tex` alongside `analysis.py` counts as both:

```bash
clawness query --stack science,python "is this derivation right"       # → SCI-DERIVE-001
clawness query --stack science,python "mutable default argument here"  # → PY-MUTABLE-001
```

**Building with LLMs?** The `llm/` domain covers eval sets instead of vibes for prompt
changes, schema-constrained output, prompt injection into tool-using agents, and never
asserting exact model text in a test. These rules *are* held back by project type, so they
stay out of projects that call no model.

---

## Per-Project Setup

Global rules handle security, testing, general best practices, and framework conventions.
For project-specific rules (your API format, your DB conventions, your naming patterns),
use `init`:

```bash
cd ~/projects/my-app
clawness init .            # scan and report
clawness init . --write    # create .clawness/rules/ and a starter memory.md
```

The scan detects your stack, recommends rule domains, and suggests a starter rule. `--write`
creates `.clawness/rules/` and `.clawness/memory.md`; the hook picks them up automatically
when you work in the project.

**Commit `.clawness/rules/` and `.clawness/memory.md`** so your whole team gets the same
rules and lessons. The rest of `.clawness/` is per-machine session state (your outstanding
handoff, the archived ones, and the ledgers) and belongs in `.gitignore`. On the first
session Clawness offers to add the block for you (it asks; it never edits `.gitignore`
itself):

```gitignore
# Clawness per-machine session state (memory.md and rules/ stay shared)
.clawness/*
!.clawness/memory.md
!.clawness/rules/
```

Use `.clawness/*`, not `.clawness/`. Ignoring the directory itself stops git descending
into it, and the two exceptions silently do nothing.

### Project rules directory

```
my-app/
├── .clawness/
│   ├── memory.md                 # Per-codebase lessons, retrieved per prompt (commit)
│   ├── handoff.md                # Outstanding note for the next session (gitignore)
│   ├── handoffs/done/            # Superseded handoffs, timestamped (gitignore)
│   ├── *.json                    # Guard, nag, and session ledgers (gitignore)
│   └── rules/                    # Project rules (commit)
│       ├── _mandatory/           # Always injected while in this project
│       │   └── MYAPP-DEPLOY-001.yml
│       └── my-app/               # Ranked as usual
│           ├── MYAPP-API-001.yml
│           └── MYAPP-DB-001.yml
├── src/
└── package.json
```

### Project memory

`.clawness/memory.md` is a plain-markdown log of per-codebase gotchas: build quirks,
recurring mistakes, hard-won fixes. Clawness retrieves from it on every prompt (right after
the rules block), so a lesson recorded once is recalled when it's relevant.

**It creates itself.** The first time you open a project (in a git repo), a session-start
hook writes a starter `.clawness/memory.md` and tells Claude to let you know it exists. To
add to it, say **"remember this: …"** and Claude appends a lesson, or edit the file
directly. Opt out of auto-create with `CLAW_NO_MEMORY=1`.

Claude maintains it on its own too: mandatory rule `ENF-MEM-001` tells it to record a lesson
when you ask or a correction repeats, one line of 120 characters or less, and to put it
**here rather than in `CLAUDE.md`**.

**The log is searched, not dumped.** Rather than paste the whole file every turn, Clawness
searches it like the rules and injects only the matching entries:

```markdown
## Always
- entries here are injected every turn (keep to 3)

## Lessons
- everything here is ranked against your prompt; the top few are injected
```

So a 200-entry log costs about what a 4-entry log costs. Lessons and rules are searched
separately, so a lesson can never crowd out a rule. **Commit the file** to share it; tune
with `CLAW_MEMORY_*` (see [Configuration](#environment-variables)).

### CLAUDE.md vs project rules vs memory.md

Claude Code loads `CLAUDE.md` in full on every turn, before any hook runs, so Clawness can't
budget or trim it. Fine for a page of orientation, expensive for the file it becomes after a
year of "just add a note". There are four homes, and one question tells you which:

| | Loaded | Cost per turn | Put here |
|---|---|---|---|
| `CLAUDE.md` | every turn, in full | the whole file, uncapped | what must fire **even when nothing in your prompt hints at it**: what the project is, key files, workflow, and every "don't change this without reading why" |
| `.claude/rules/` **with `paths:`** | when a matching file is read | nothing until then | guidance tied to particular files or directories (Claude Code's own mechanism) |
| `.clawness/rules/` | when it matches your prompt | inside `CLAW_BUDGET` | long rationale attached to a *topic* rather than a path, surfaced when you're working on it |
| `.clawness/memory.md` | top 3 matches | ≤1200 characters | one-line traps that already bit you |

The question is the first column: **does it need to fire when the prompt gives no hint that
it applies?** If yes, it belongs in CLAUDE.md, because retrieval can't guess. If no,
retrieval is cheaper, since you pay for it only on the turns it matters.

> **`@path` imports do not help.** Breaking a big CLAUDE.md into `@other-file.md` references
> is the most popular version of this advice and it moves zero tokens:
> [imports are expanded and loaded at launch](https://code.claude.com/docs/en/memory#import-additional-files),
> four hops deep. Only the four rows above change what you pay.

If your CLAUDE.md gets large (past ~6,000 tokens, `CLAW_CLAUDE_MD_LIMIT`), a session-start
note points you at the fix. Run **`/clawness:claude-md`** to sort it section by section into
stay / cut / `.claude/rules/` / `.clawness/rules/` / `.clawness/memory.md`, with a plan shown
before anything moves. Claude Code's own [`/doctor`](https://code.claude.com/docs/en/commands)
does the trim natively; the two compose.

> **One gap.** When Claude records a lesson, `ENF-MEM-001` sends it to `.clawness/memory.md`.
> But Claude Code's own `#` shortcut writes to `CLAUDE.md` directly, with no hook in that
> path, so Clawness can't route it. Say "remember this: …" instead of using `#` if you want
> a note ranked rather than re-read every turn.

---

## Writing Rules

### The easy way: describe it

Describe the rule and let Clawness write it:

```
/clawness:add always use server actions for form mutations in Next.js
```

It generates a properly-tagged rule (with `violation`/`correct` examples), saves it to your
project's `.clawness/rules/` (or the global set if there's no project dir), and confirms
before writing. No YAML by hand.

### Rule format

```yaml
id: FA-PYDANTIC-001
domain: fastapi
severity: error          # error | warning | info
tags: [pydantic, model, schema, validation, request, response]
triggers: [BaseModel, schema, model, request, response, body, Field]
when: Defining request or response shapes for any endpoint.
rule: >
  Define Pydantic models for every request body and response. Never
  accept or return raw dicts. Use separate models for create, update,
  and read operations.
violation: "@app.post('/users') async def create(data: dict)"
correct: "@app.post('/users', response_model=UserRead) async def create(data: UserCreate)"
```

| Field | Required | Drives retrieval? | Purpose |
|-------|----------|-------------------|---------|
| `id` | Yes | Yes | Unique ID, shown in output |
| `domain` | Yes | Yes | Category for filtering |
| `severity` | No | No | `error` / `warning` / `info` |
| `tags` | **Recommended** | **Yes** | Keywords: what topic does this cover? |
| `triggers` | **Recommended** | **Yes** | Code tokens that signal relevance |
| `when` | **Recommended** | Yes | When should this rule apply? |
| `rule` | Yes | Yes | The instruction your agent follows |
| `violation` | No | Yes | What NOT to do |
| `correct` | No | Yes | What TO do |
| `applies_to` | No | No | Framework versions this was established against, e.g. `{"Next.js": "13-15"}` |
| `verified` | No | No | `YYYY-MM`, when someone actually checked |
| `sources` | No | No | URLs that justified the range |

The last three are the [version stamp](#version-gap-detection-on-by-default). The range is
inclusive, one or two numeric components per bound (`13-15`, or `15` for a single major, or
`1.4-2.0` where the minor matters). All three together, or the rule stays silent.

### Tips for good rules

**`tags` and `triggers` matter most.** The retriever matches your prompt against these.
Ask: *what words would someone use when working on a task this rule applies to?*

```yaml
# Bad: too generic
tags: [code]
triggers: [function]

# Good: specific to the actual concept
tags: [database, connection, pooling, timeout, postgres]
triggers: [create_engine, SessionLocal, get_db, connection_pool]
```

**Use `_mandatory/` sparingly.** Every mandatory rule costs tokens on every prompt. Reserve
it for security gates and testing requirements.

**Check your work:**

```bash
clawness lint     # required fields, and rejects vague phrasing ("where appropriate")
clawness eval     # MRR@5 + hit-rate against tests/ground_truth.json
```

If you add rules in a new area, add a query or two to `tests/ground_truth.json` so the test
set keeps pace.

---

## Sub-Agents

Clawness ships seven review sub-agents your agent can delegate to. Trigger them by name or
just describe the task, since the workflow rules tell Claude when to reach for them:

```
> have the security-red-team agent review the auth module
> run a security audit on this project
> review the code before we merge
> should we use PostgreSQL or MongoDB for this?
```

- **Security red team / blue team.** The red team thinks like an attacker, runs OWASP Top
  10, and searches for CVEs published this month. The blue team triages those findings and
  proposes exact fixes. Claude merges both into a prioritized plan.
- **Code critic.** Bugs, performance, edge cases, and maintainability before a merge.
- **Architecture challenger.** Stress-tests a design: *what at 10x load? what if this fails?
  is there a simpler option?*
- Plus **test writer**, **perf auditor**, and **refactor advisor**.

**Offers first.** Spawning sub-agents is expensive, so the `audit`/`review`/`perf` skills
never auto-run. When your prompt sounds like one, Clawness nudges Claude to offer first. You
can also run them directly: `/clawness:audit`, `/clawness:review`, `/clawness:perf`.

Model and effort settings are in [Agent Model Configuration](#agent-model-configuration).

---

## CLI Reference

The CLI is optional, since everyday use needs no commands. It's installed by the **manual
installer** (and by any `pip install` of the package). **Plugin-only users:** rule
injection, agents, skills, and the plan gate all work without it; the few skills that need
it (`status`, `refresh`, `claude-md`, `audit-rules`) run the bundled CLI through a small
wrapper the plugin writes at the start of each session, so no install is required.

```bash
# Retrieve rules for a task description
clawness query "implement async REST endpoint"
clawness query "handle null values" --domain typescript

# Scan a project and suggest rules
clawness init /path/to/project
clawness init . --write

# Security scan (deterministic; accumulates a findings ledger)
clawness scan                              # enumerate + coverage (report-only)
clawness scan --new-only                   # only candidates awaiting adjudication
clawness scan status                       # ledger + coverage without re-scanning
clawness scan --fail-on high               # opt-in CI gate on unresolved findings

# Manage the rule set
clawness stats             # rule counts by domain + per-turn token estimate
clawness lint              # validate rule files (incl. vague-phrasing check)
clawness bench             # benchmark retrieval latency
clawness eval              # retrieval quality: MRR@5 + hit-rate
clawness eval --floor-mrr 0.85 --floor-hit 0.95   # fail below floors (CI gate)

# Corpus health (maintainers)
clawness audit-rules                       # all checks
clawness audit-rules --stale               # missing / over-wide version stamps
clawness audit-rules --coverage            # ranked rules in no eval query
clawness audit-rules --overlap             # rule pairs competing for one slot
clawness audit-rules --strict              # exit non-zero if anything is reported

# Plan gate (normal flow uses native plan mode)
clawness plan status       # gate state, and what turned it off if it is

# Emit an AGENTS.md so any agent can use the CLI
clawness agents-md --write
```

> If `clawness` isn't found after install, your Python user-scripts directory isn't on your
> PATH. Either add it, or use the long form `python -m clawness.cli <command>` (`python3`
> on macOS/Linux), which works from any directory.

---

## What Ships

| Component | Count | Purpose |
|-----------|-------|---------|
| **Rules** | 215 across 29 domains | Coding, science, ML, research, and LLM standards, injected per prompt |
| **Agents** | 7 sub-agents | Security red/blue team, code critic, test writer, perf auditor, refactor advisor, architecture challenger |
| **Skills** | 13 slash commands | `/clawness:audit`, `/clawness:review`, `/clawness:test`, `/clawness:perf`, `/clawness:add`, `/clawness:status`, `/clawness:user-docs`, `/clawness:claude-md`, `/clawness:refresh`, `/clawness:audit-rules`, `/clawness:bootstrap`, `/clawness:eval-set`, `/clawness:openclaw-audit` |
| **Hooks** | 12 | Rule injection, context watch, model-tier check, output compression, plan gate, access guard, trust ledger, and the session-start checks |
| **CLI** | 11 commands | query, init, stats, lint, bench, eval, scan, plan, agents-md, audit-rules, audit-skills |
| **Installers** | bash + PowerShell | With matching uninstallers, for Windows/macOS/Linux |

### Rule domains

| Domain | Rules | Covers |
|--------|-------|--------|
| `general` | 26 | Cross-cutting: prior art, abstraction/YAGNI, comments, memory, magic numbers, immutability, dependency selection, versioning/lockfiles, changelog upkeep, linting, naming, validation, logging, env config, accessibility, git, performance *(3 mandatory)* |
| `science` | 18 | Physics/maths/engineering: dimensional consistency, numerical stability, matrix conditioning, uncertainty propagation, statistical discipline, derivation checking, solver validation and convergence, RNG and seeding, reproducibility, paper claims, figure standards, array/dataframe correctness, notebook hygiene |
| `research` | 14 | Source hygiene, citation verification, date-bounded sweeps, reporting standards (PRISMA/CONSORT/STROBE/ARRIVE), data/code availability with a DOI, pre-registration, peer-review responses, falsifiable questions, novelty search, structured synthesis |
| `security` | 11 | Auth, secrets, deps, untrusted-content/exfil *(4 mandatory)*; SQLi, XSS, supply-chain, SSRF, path traversal, IDOR, password hashing *(ranked)* |
| `workflows` | 11 | Multi-agent orchestration, session handoff, sub-agent cost/vetting, lessons-memory upkeep *(1 mandatory)* |
| `nextjs` | 10 | Server/Client components, data fetching, caching, layouts, metadata, Server Actions |
| `fastapi` | 8 | Pydantic v2, dependency injection, async, error handling, CORS, DB sessions |
| `meta` | 8 | Rebuttals to common AI shortcuts ("too simple to test", "I'll refactor later", trusting input) |
| `ml` | 8 | Training/evaluating your own models: leakage, cross-validation, metrics/baselines, class imbalance, calibration, reproducibility, overfitting |
| `llm` | 7 | Building with models: eval sets, prompt injection, schema-constrained output, token cost, non-determinism, model-id pinning, retrieval |
| `python` | 7 | Async I/O, imports, error handling, type hints, mutable defaults, context managers, pathlib |
| `testing` | 7 | Coverage for new code *(1 mandatory)*; watching a test fail, boundary testing, determinism, mocking, assertions, isolation *(ranked)* |
| `capacitor` | 6 | Platform detection, permissions, lifecycle, WebView, sync, App Store |
| `css` | 6 | `!important`, relative units, flex/grid, custom properties, responsive, focus states |
| `docker` | 6 | Layer caching, multi-stage builds, non-root, secrets, tag pinning, slim images |
| `java` | 6 | Null safety, equals/hashCode, try-with-resources, exceptions, immutability, collections |
| `cfd` | 5 | Mesh quality and y+, residual vs grid convergence, Courant number, turbulence model choice, boundary conditions |
| `go` | 5 | Error handling, nil maps, context, goroutine lifecycle, data races |
| `reliability` | 5 | Timeouts, bounded retry with backoff/jitter, idempotency keys, rate limiting, graceful degradation |
| `rust` | 5 | unwrap/expect, error handling, clone, unsafe, iterators |
| `sql` | 5 | N+1 queries, indexes, transactions, `SELECT *`, migrations |
| `bash` | 4 | Strict mode, quoting, error checking, shellcheck |
| `fortran` | 4 | `implicit none`, `intent`, explicit precision, column-major order |
| `julia` | 4 | Type stability, allocation and views, dispatch and type piracy, `Project.toml` |
| `matlab` | 4 | Preallocation, `\` over `inv`, functions over scripts, reproducible figures |
| `r` | 4 | `NA` semantics, type coercion, vectorised loops, `renv` reproducibility |
| `react` | 4 | Hooks, state management, list keys, forms |
| `typescript` | 4 | Null safety, async errors, strict mode, Zod |
| `ci` | 3 | SHA-pinned actions, OIDC over long-lived secrets, no fork-PR code with secrets |

The 9 **mandatory** rules (always injected) are the 4 `security` rules, the 1 `testing`
rule, the lessons-memory rule (`workflows`), and 3 under `general`: current practices,
verification, and output voice (which keeps your agent from narrating the rules system at
you).

---

## Configuration

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `CLAW_RULES_DIR` | (next to hook script) | Override global rules directory |
| `CLAW_TOP_K` | `5` | Max ranked rules per prompt |
| `CLAW_BUDGET` | `4000` | Max tokens for the rule block (memory has its own separate limit) |
| `CLAW_MIN_RELEVANCE` | `0.06` | Minimum match score. Below it, a rule counts as coincidence. `0` turns it off |
| `CLAW_TOPICAL_MIN_RELEVANCE` | `0.12` | Middle bar, for `science`/`research` (never held back by project type) |
| `CLAW_OFFSTACK_MIN_RELEVANCE` | `0.15` | Higher bar, for language/framework rules your project doesn't use |
| `CLAW_NARROW_MIN_RELEVANCE` | `0.22` | Highest bar, for CFD/Julia/Fortran/MATLAB/R off-project (their words overlap everyday code) |
| `CLAW_NO_STACK_FILTER` | (unset) | Ignore the project's languages; treat every domain the same |
| `CLAW_NO_MEMORY` | (unset) | Don't auto-create `.clawness/memory.md` |
| `CLAW_MEMORY_BUDGET` | `1200` | Character limit on the whole project-memory block |
| `CLAW_MEMORY_TOP_K` | `3` | Max ranked lessons per prompt. Set high to inject the whole log |
| `CLAW_MEMORY_MIN_RELEVANCE` | `0.20` | Minimum match score for a lesson. `0` turns it off |
| `CLAW_MEMORY_PIN_BUDGET` | `400` | Max characters of the always-injected `## Always` section |
| `CLAW_MEMORY_MAX_ENTRIES` | `200` | Only the newest N lessons are searched |
| `CLAW_NO_STACK_NOTE` | (unset) | Don't inject the detected-stack note |
| `CLAW_NO_STALENESS_NOTE` | (unset) | Don't warn on a framework version past the range its rules were verified against |
| `CLAW_NO_MODEL_ADVISOR` | (unset) | Don't check the session's model tier (Claude Code) |
| `CLAW_NO_CONTEXT_WATCH` | (unset) | Disable context-pressure warnings (Claude Code) |
| `CLAW_CONTEXT_LIMIT` | (auto) | Your context window in tokens. Auto-detected, set explicitly if the guess is wrong |
| `CLAW_CONTEXT_WARN` | `0.70` | Fraction of the window at which to mention context is filling |
| `CLAW_CONTEXT_URGENT` | `0.85` | Fraction at which to recommend a fresh session |
| `CLAW_CONTEXT_SURGE` | `0.12` | A single turn adding this fraction is flagged (when ≤5 turns of headroom remain) |
| `CLAW_NO_HANDOFF` | (unset) | Don't pick up `.clawness/handoff.md` at session start |
| `CLAW_HANDOFF_BUDGET` | `2000` | Max characters of the handoff injected (keeps the head) |
| `CLAW_VERBOSE` | (unset) | Render mandatory rules in full and show retrieval metadata. More tokens per turn |
| `CLAW_COMPACT` | (unset) | Render ranked rules compactly too. Fewer tokens per turn |
| `CLAW_FULL_EVERY` | `5` | Show the full mandatory block on prompt 1 and every Nth after (id list in between) |
| `CLAW_NO_PLAN_GATE` | (unset) | Turn the plan gate off (Claude Code) |
| `CLAW_NO_ACCESS_GUARD` | (unset) | Turn off the access guard |
| `CLAW_NO_TRUST_LEDGER` | (unset) | Don't fingerprint skills/agents/MCP |
| `CLAW_NO_GIT_CHECK` | (unset) | Stop offering to `git init` |
| `CLAW_NO_CHANGELOG_CHECK` | (unset) | Stop the changelog reminder and one-time offer |
| `CLAW_NO_CLAUDE_MD_CHECK` | (unset) | Stop the oversized-`CLAUDE.md` note (Claude Code) |
| `CLAW_CLAUDE_MD_LIMIT` | `6000` | Estimated `CLAUDE.md` tokens above which that note fires |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Claude Code's config dir; the installer follows it if relocated |
| `CLAUDE_CODE_SUBAGENT_MODEL` | (none) | Override model for ALL sub-agents |

### Agent model configuration

Split by what the agent is for, not by role:

- **Judgment and adversarial work inherits your session's model.** A security review or a
  code critique is only as good as the model making the call, so `model:` is omitted on
  these (Claude Code's `inherit` default).
- **Mechanical work is pinned to `sonnet`.** Test generation and pattern scans don't need
  your top tier.

Clawness never hardcodes a frontier model in a shipped agent, since it can't know your plan,
access, or budget.

| Agent | Model | Effort | Max Turns |
|-------|-------|--------|-----------|
| `security-red-team` | inherit | high | 25 |
| `security-blue-team` | inherit | high | 25 |
| `arch-challenger` | inherit | high | 15 |
| `code-critic` | inherit | medium | 15 |
| `test-writer` | `sonnet` | medium | 20 |
| `perf-auditor` | `sonnet` | medium | 15 |
| `refactor-advisor` | `sonnet` | medium | 15 |

**Override** by editing an agent's `.md` in `~/.claude/agents/`. `model:` takes aliases
(`haiku`/`sonnet`/`opus`/`fable`), a pinned ID, or `inherit`; `effort:` runs `low` through
`max`; `maxTurns:` caps tool calls. Retarget all sub-agents at once with
`CLAUDE_CODE_SUBAGENT_MODEL`.

### Where rules live

| Location | Scope | When loaded |
|----------|-------|-------------|
| `~/.claude/clawness/rules/` | Global | Every prompt, every project |
| `<project>/.clawness/rules/` | Project | Only when working in that project |
| `<project>/.clawness/rules/_mandatory/` | Project mandatory | Every prompt while in that project |

> The `~/.claude/clawness/rules/` path applies to a **manual** install. With the **plugin**
> install, the global rules ship inside the plugin and load from its cache automatically.
> Either way, project rules work the same.

---

## How It Compares

Against [Writ](https://github.com/infinri/Writ) (the hybrid-RAG project that inspired it)
and plain Claude Code with no plugin:

| | Writ | **Clawness** | Vanilla Claude Code |
|---|---|---|---|
| Finding the right rules | 5-stage hybrid RAG (BM25 + vector + graph) | Word and concept matching (BM25 + TF-IDF + RRF) | None; CLAUDE.md loaded in full or mentioned by hand |
| Token cost / turn | selected rules (5k budget) | ~1,700 (mandatory + selected) | all of CLAUDE.md, every turn |
| Infrastructure | Docker + Neo4j + ONNX + daemon (~2 GB) | PyYAML (~200 KB) | none |
| Install | ~5 min (containers) | ~5 sec | built-in |
| Always-on mandatory rules | Yes (30) | Yes (9) | manual discipline |
| Per-project rules | Yes | Yes (`.clawness/rules/`) | per-dir CLAUDE.md |
| Plan-first gate | Yes (token approval) | Yes (uses built-in plan mode) | built-in plan mode (opt-in) |
| Output compression | No | Yes | No |
| Review sub-agents | 1 (rules-driven) | 7 (red/blue team, critic, …) | general subagents, not preconfigured |
| Guard on risky commands | No | Yes, overrides what you've allowed | permission prompts you learn to click through |
| Skill/agent/MCP trust ledger | No | Yes, alerts on change | No |

Per Writ's author: its sub-agents run isolated (no shared reasoning), and its review agent's
adversarial character comes from the rules it's given, so the count is one adversarial agent.

---

## Troubleshooting

**Plugin install: skills/hooks not showing up.** Run `/reload-plugins` or check
`claude plugin list`. On first session a background hook installs PyYAML (a few seconds);
check `bootstrap.log` in the plugin's data directory, and run `claude --debug` to see hook
activity. Make sure Python 3.10+ is on your PATH.

**Hook not firing / agent doesn't see rules.** Check `~/.claude/settings.json` contains the
hook config. Re-run the installer; it's safe to repeat and reports what's already there.

**PowerShell: "running scripts is disabled".**
```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**"No module named yaml".**
```bash
python -m pip install pyyaml --user
```

**Wrong rules appearing / right rules not appearing.** Test what the retriever sees:
```bash
python -m clawness.cli query "your exact prompt text here"
```
Then improve `tags` and `triggers` on the rules that should match.

**Too many mandatory rules eating tokens.** Move rules from `_mandatory/` to a ranked
domain. Only security gates and test requirements should be mandatory.

**Temporarily disable Clawness.** Rename or delete the hook entries in
`~/.claude/settings.json`. Re-run the installer to add them back.

---

## License

MIT. See [LICENSE](LICENSE).

## Acknowledgments

Inspired by [infinri/Writ](https://github.com/infinri/Writ), which pioneered hybrid-RAG rule
retrieval for AI coding agents. Clawness takes the same core ideas and repackages them
without the infrastructure.
