# Clawness

**Install once. Works everywhere. Your AI coding agent gets the right rules for every task — automatically.**

Clawness is a Claude Code plugin that puts the right coding rules in context on every prompt, automatically. It ships 114 coding rules across 18 domains, 7 adversarial review sub-agents, output compression, and a default-on plan-approval gate — all in under 1 MB with zero infrastructure. You install it once, and it silently injects the relevant rules into every Claude Code session across every project on your machine.

Inspired by [infinri/Writ](https://github.com/infinri/Writ), rebuilt from ~2GB of infrastructure to pure Python.

---

## 30-Second Version

Installing the plugin is **two commands plus a restart** — the plugin downloads its Python backend on first launch, so it isn't fully live until step 3.

**1. Install** (from any Claude Code session):

```bash
claude plugin marketplace add fullymiddleaged/clawness
claude plugin install clawness@clawness
```

**2. Restart Claude Code** (or run `/reload-plugins`) so the hooks actually load.

**3. Let first-run setup finish.** On the first session, a background hook installs Clawness's one dependency (**PyYAML**) into your environment. This needs **Python 3.10+ on your PATH** and takes just a few seconds. Retrieval is pure-Python **lexical + concept** matching — no models, no downloads.

**4. Verify** — ask Claude:

```
what clawness rules do you see in your context?
```

If it describes the injected rule block, you're live. (`/clawness:status` also works.)

> `clawness@clawness` isn't a typo — it's `plugin@marketplace`, and both happen to be named *clawness*. No Python 3.10+? See [Installing Python](#installing-python-if-you-dont-have-it) — without it, the plugin installs but injects nothing.

---

## What Problem Does This Solve?

You have coding rules: *"always use parameterized SQL," "async I/O end-to-end," "all API responses use the envelope format."*

Without Clawness, you either:
- **Dump all rules into CLAUDE.md** → wastes tokens, dilutes attention on every turn
- **Remember to mention rules manually** → you forget, Claude forgets

With Clawness:
- **115 rules live in YAML files**, organized by domain
- **A hook fires on every prompt**, retrieves only the rules relevant to your current task (under a millisecond on the lexical path)
- **Mandatory rules** (security, testing) appear on every turn, non-negotiably
- **Ranked rules** (Next.js patterns, React hooks, FastAPI conventions) appear only when relevant
- **Output compression** strips noise from long bash output so Claude's context stays clean
- **Adversarial sub-agents** (security red/blue team, code critic, architecture challenger) are available for deeper review

---

## How It Works

```
You type a prompt in Claude Code
        │
        ▼
┌──────────────────────────┐
│  Hook: UserPromptSubmit  │  fires automatically before Claude sees your prompt
│  hooks/claude_hook.py    │
└──────────┬───────────────┘
           │
     ┌─────┴──────┐
     ▼            ▼
┌─────────┐  ┌──────────┐
│ GLOBAL  │  │ PROJECT  │    global rules from ~/.claude/clawness/rules/
│ rules   │  │ rules    │    project rules from <project>/.clawness/rules/
└────┬────┘  └────┬─────┘
     └──────┬─────┘
            ▼
┌──────────────────────────┐
│  BM25 + TF-IDF + RRF    │  hybrid lexical retrieval + concept expansion
│  + concept expansion    │  picks the top rules in ~1ms (pure Python)
│  context budget: 4000    │  stops adding rules when token budget is full
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│  Claude Code             │  sees: mandatory rules (always)
│  (your agent)            │      + relevant ranked rules (per-prompt)
│                          │      + your original prompt
└──────────────────────────┘
```

**In plain terms:** for each prompt, Clawness scores every rule by how well it matches what you're working on — by shared keywords and concepts — and quietly adds the few that fit (plus the always-on mandatory ones). BM25, TF-IDF, and RRF are just the techniques that do the scoring; the concept layer bridges synonyms (login ↔ auth ↔ jwt) so wording differences don't matter. You never touch any of it. No ML models, no downloads — it runs in about a millisecond.

**Two layers of rules:**
- **Global** (`~/.claude/clawness/rules/`) — installed once, applies to every project
- **Project** (`<your-project>/.clawness/rules/`) — optional, layers on top for project-specific conventions. Commit to git so your whole team shares them.

### Retrieval engine

Pure Python, one dependency (PyYAML) — no ML models, no embeddings, no services, nothing to download at query time:

- **BM25-Okapi + TF-IDF cosine, fused via Reciprocal Rank Fusion** — two complementary lexical rankers, so a rule surfaces whether your prompt shares its exact terms or just its overall vocabulary.
- **Concept expansion (26 concept groups)** maps synonyms onto shared markers — `login ↔ auth ↔ jwt ↔ session`, `postgres ↔ db ↔ query`, `unwrap ↔ error ↔ exception` — applied to both the rules and your prompt. This is the "different words, same idea" reach a vector model gives, but instant and dependency-free. (Extend `_CONCEPT_GROUPS` in `clawness/core.py` to widen it.)
- **Light stemming** collapses plural/verb forms (`caches` → `cache`, `maintained` → `maintain`).
- **Mandatory rules** are always injected; the rest are ranked and capped by a token budget.

**Measured quality** — run `clawness eval`: on a 44-query labeled set, **MRR@5 = 0.977** and **hit-rate = 1.000** (every query surfaces its expected rule, usually at rank 1). CI enforces floors on these so retrieval can't silently regress as rules are added.

**Cost** — **~1 ms per prompt**; ~472 tokens of always-on mandatory rules plus the few selected ranked rules. Run `clawness stats` for your exact per-turn estimate.

---

## Install

### Installing Python (if you don't have it)

Clawness needs **Python 3.10+** on your PATH. Check first:

```bash
python --version     # or: python3 --version
```

If that prints `3.10` or higher, you're set — skip to Option 1. Otherwise:

**Windows** — install from [python.org/downloads](https://www.python.org/downloads/) and **tick "Add python.exe to PATH"** on the first screen (easy to miss, and the usual reason `python` "isn't found" later). Or with winget:

```powershell
winget install Python.Python.3.12
```

**macOS** — usually preinstalled as `python3`. If not:

```bash
brew install python
```

**Linux** — use your package manager:

```bash
sudo apt install python3      # Debian / Ubuntu
sudo dnf install python3      # Fedora / RHEL
sudo pacman -S python         # Arch
```

Then open a **new** terminal (so PATH refreshes) and re-run the check above.

### Option 1: Plugin Install (Recommended)

From any Claude Code session:

```bash
claude plugin marketplace add fullymiddleaged/clawness
claude plugin install clawness@clawness
```

The install registers the skills, agents, hooks, and rules — but it isn't live until you reload and the backend finishes setting up:

1. **Restart Claude Code** (or run `/reload-plugins`) so the hooks load.
2. **Let first-run setup finish** — on the first session a background hook installs PyYAML (a few seconds). Details below.
3. **Verify** — ask Claude *"what clawness rules do you see in your context?"*, or run `/clawness:status`.

> **What the plugin installs on first run.** Claude Code's install screen lists the components (commands, agents, hooks) but doesn't spell out what the hooks *do*. For transparency:
> - **Requires Python 3.10+ on your PATH** — the hooks are Python scripts. No Python, no rules (Clawness installs but silently injects nothing). Need it? See [Installing Python](#installing-python-if-you-dont-have-it).
> - **On your first session, a background `SessionStart` hook runs `pip install`** to fetch **PyYAML** (the only dependency) into your environment — a few seconds. That's it: retrieval is pure-Python lexical + concept matching, no models. Nothing is installed at plugin-install time; it happens on first session and is logged to `bootstrap.log` in the plugin's data directory.
> - **The plan gate is on by default** — it blocks file edits until you approve a plan (via plan mode, or disable with `CLAW_NO_PLAN_GATE=1`). See [Plan Gate](#plan-gate-on-by-default).

### Option 2: Manual Install

For more control, or if the plugin system isn't available in your environment.

**Requirements:** Python 3.10+ (see [Installing Python](#installing-python-if-you-dont-have-it)) and Claude Code. No Docker, no Node, no databases, no ML models. Retrieval is pure-Python lexical + concept matching; PyYAML is the only dependency.

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

### What the Manual Installer Does (7 steps)

| Step | What | Why |
|------|------|-----|
| 1 | Check Python 3.10+ | Finds `python` / `python3` / `py` |
| 2 | Install clawness + deps | Editable `pip install` — adds the `clawness` command and PyYAML (the only dependency) |
| 3 | Verify files | Confirms rules and hook scripts are present |
| 4 | Lint rules | Validates every `.yml` rule file |
| 5 | Test retrieval | Runs a test query to confirm the engine works |
| 6 | Install agents & skills | Copies to `~/.claude/agents/` and `~/.claude/skills/` |
| 7 | Configure hooks | Adds rule injection, output compression, and the plan gate (on by default) to `settings.json` |

The installer is idempotent — safe to re-run. It won't duplicate hooks or overwrite existing settings.

### Uninstall

**Plugin install** — use Claude Code's own command (the `/plugin` menu's remove is unreliable; use the CLI):

```bash
claude plugin uninstall clawness
claude plugin marketplace remove clawness   # optional — also drops the marketplace
```

Add `--prune` to also clean up dependencies, and `--scope project` if you installed it at project scope.

**Manual install** — don't just delete the folder: that leaves hook entries in `settings.json` pointing at missing scripts, which error on every prompt. Run the uninstaller first (it removes the hooks and the copied agents/skills), then delete the folder:

```bash
# macOS / Linux
bash ~/.claude/clawness/uninstall.sh
rm -rf ~/.claude/clawness

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\clawness\uninstall.ps1"
Remove-Item -Recurse -Force "$env:USERPROFILE\.claude\clawness"
```

Left in place on purpose (remove by hand if you want): the `pyyaml` pip package (shared with other tools), and any per-project rules in each project's `.clawness/`.

---

## Using It

### The Short Answer

**Just use Claude Code normally.** After installation, the hook fires silently on every prompt. You don't type anything special, you don't reference rules, you don't invoke agents. Claude just sees the relevant rules in its context and follows them.

### What Claude Actually Sees

When you type *"implement the user registration endpoint"*, Claude receives this prepended to the conversation:

```
--- CLAWNESS RULES (9 rules, 0.31ms) ---

# MANDATORY (7)
[ENF-CURRENT-001] (general/error)
  RULE: Always use current best practices for the present month and year...
[ENF-SEC-001] (security/error)
  RULE: All secrets must come from environment variables...
[ENF-SEC-002] (security/error)
  RULE: Always use parameterized queries...
...

# RELEVANT (2)
[FA-PYDANTIC-001] (fastapi/error) score=0.033
  WHEN: Defining request or response shapes for any endpoint.
  RULE: Define Pydantic models for every request body and response...
[GEN-VALIDATE-001] (general/error) score=0.031
  WHEN: Receiving any input from users, APIs, files, or external systems.
  RULE: Validate and sanitize all external input at the boundary...

--- END CLAWNESS RULES ---
```

The mandatory rules always appear. The ranked rules change based on your prompt.

**Token cost.** A typical turn injects **~1,300 tokens** — roughly **~470 fixed** for the always-on mandatory block plus the selected ranked rules. To keep that fixed cost down, mandatory rules render **compactly** (just the directive, not the `WHEN`/`BAD`/`GOOD` examples, which would repeat identically every turn). Ranked rules render in full, since their examples are prompt-relevant. Run `clawness stats` to see your exact per-turn estimate, and tune it with `CLAW_TOP_K` / `CLAW_BUDGET`, `CLAW_VERBOSE` (full mandatory examples), or `CLAW_COMPACT` (trim ranked too).

**Relevance floor.** Ranked rules are only injected when the prompt actually matches them — a TF-IDF cosine floor (`CLAW_MIN_RELEVANCE`, default `0.06`) drops coincidental matches, so a prompt with no topical signal returns few or zero ranked rules instead of filling all `CLAW_TOP_K` slots with scattershot. Mandatory rules are unaffected. Raise the floor to be stricter, or set it to `0` to disable.

### Verify It's Working

Three ways, depending on what you want to see:

- **Confirm it's live (user-facing):** run `/clawness:status`, or just ask Claude *"what clawness rules do you see in your context?"* — if the hook is active it'll describe the injected rules.
- **Watch first-run setup happen:** launch with `claude --debug` once. Claude Code doesn't show hook output in the normal UI (hook stdout goes to the model, and the dependency bootstrap runs in the background), so `--debug` is how you see the live install activity.
- **Read the install record:** the first-session bootstrap logs every step to **`bootstrap.log`** in the plugin's data directory — the Python interpreter used, each `pip` attempt and its result, the installed PyYAML version, and a final `bootstrap ready` line. Check it if rules aren't appearing.

> Note: there's no in-UI "installer" banner — that's a Claude Code limitation (hooks can't print to the user's screen), not a Clawness setting. The three methods above are how you observe setup.

### Output Compression

When Claude runs a bash command that produces 80+ lines of output (test suites, builds, long logs), the PostToolUse compression hook fires automatically. It extracts only the error/failure lines with context and provides a summary, keeping Claude's context clean for the next prompt.

### Plan Gate (on by default)

Clawness enforces a plan-first workflow: file edits (`Write`/`Edit`/`MultiEdit`/`NotebookEdit`) are blocked until you've approved a plan. It rides Claude Code's **native plan mode** — present a plan, approve it, and the gate clears itself for the rest of the session. No special commands are needed in the normal flow.

If Claude tries to edit before a plan is approved, you'll see a deny message explaining what to do. Approve a plan in plan mode and editing proceeds.

**To turn it off — no command needed:** set `CLAW_NO_PLAN_GATE=1` in your environment to disable the gate globally.

For finer control there's a CLI (available after a manual install, or any `pip install` — see the [CLI Reference](#cli-reference)):

```bash
clawness plan off       # disable for this project
clawness plan on        # re-enable
clawness plan status    # show current state
clawness plan approve   # manual override (headless / no plan mode)
```

**Version control:** the plan gate stops *unplanned* edits, but recovering from a *bad* edit is git's job — Clawness deliberately doesn't reimplement checkpoints. If you open a project that isn't a git repo, a SessionStart check nudges Claude to ask whether you'd like to `git init` (it never initializes without your say-so). The check looks upward (cwd and its parents) *and* a few levels down, so opening a workspace or monorepo parent whose repos live in subfolders won't trigger a false "no git" nudge. Silence it with `CLAW_NO_GIT_CHECK=1`.

**Stack awareness:** at session start Clawness detects your project's stack from its files (the same detection as `clawness init`) and injects a one-line note — e.g. *"Detected project stack: Python, FastAPI, SQL"* — so Claude starts already knowing the ecosystem instead of inferring it. It's a heuristic, stated as such, and complements the per-prompt rule retrieval. Silence it with `CLAW_NO_STACK_NOTE=1`.

---

## Per-Project Setup

Global rules handle security, testing, general best practices, and framework conventions. For project-specific rules (your API format, your database conventions, your naming patterns), use `init`:

```bash
cd ~/projects/my-app
clawness init .
```

This scans your project and reports:

```
Project: /home/you/projects/my-app

Detected stack:
  + Node.js project
  + TypeScript
  + Next.js
  + Capacitor (mobile)
  + React
  + Prisma ORM

Recommended rule domains: capacitor, general, nextjs, react, typescript, workflows

Starter project rule:
  id: MY_APP-STACK-001
  domain: my-app
  ...
```

Add `--write` to create the project rules directory:

```bash
clawness init . --write
```

This creates `.clawness/rules/` and a starter `.clawness/memory.md` in your project. The hook automatically picks up rules from this directory when you're working in the project. **Commit `.clawness/` to git** — your whole team gets the same rules.

### Project Rules Directory

```
my-app/
├── .clawness/
│   ├── memory.md                 # Per-codebase lessons, injected every turn
│   └── rules/
│       ├── _mandatory/           # Project-specific mandatory rules
│       │   └── MYAPP-DEPLOY-001.yml
│       └── my-app/               # Project-specific ranked rules
│           ├── MYAPP-API-001.yml
│           └── MYAPP-DB-001.yml
├── src/
├── package.json
└── ...
```

Rules in `.clawness/rules/_mandatory/` are always injected when working in this project. Rules in other subdirectories are ranked as usual.

### Project Memory (lessons learned)

`.clawness/memory.md` is a plain-markdown log of per-codebase gotchas — build
quirks, recurring mistakes, hard-won fixes. The hook injects it into **every
prompt** (right after the rules block), so a lesson recorded once is recalled
every session instead of re-discovered.

**It creates itself.** The first time you open a project (in a git repo),
Clawness's SessionStart hook writes a starter `.clawness/memory.md` and tells
Claude to let you know it exists — so you can see it working from day one. To add
to it, just say **"remember this: …"** and Claude appends a lesson; or edit the
file directly. (Opt out of auto-create with `CLAW_NO_MEMORY=1`; it never touches
the home directory or non-git folders, and goes silent once the file exists.)

Claude also maintains it on its own: rule `WF-LESSONS-001` tells it to record a
lesson immediately when you ask, or the *second* time a mistake recurs — keeping
entries short, deduplicated, and pruned. Keep it lean; it's injected every turn.
The block is bounded by `CLAW_MEMORY_BUDGET` (characters, default `2000`); when
the file overflows, the most recent lessons (the tail) are kept. **Commit it** so
the whole team shares the same hard-won knowledge.

---

## Writing Rules

### Rule Format

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

### What Each Field Does

| Field | Required | Drives Retrieval? | Purpose |
|-------|----------|-------------------|---------|
| `id` | Yes | Yes | Unique ID, shown in output |
| `domain` | Yes | Yes | Category for filtering |
| `severity` | No | No | `error` / `warning` / `info` |
| `tags` | **Recommended** | **Yes** | Keywords — what topic does this rule cover? |
| `triggers` | **Recommended** | **Yes** | Code tokens that signal relevance |
| `when` | **Recommended** | Yes | When should this rule apply? |
| `rule` | Yes | Yes | The instruction Claude follows |
| `violation` | No | Yes | What NOT to do |
| `correct` | No | Yes | What TO do |

### Tips for Good Rules

**`tags` and `triggers` are the most important fields.** The retriever matches your prompt against these. Think: *what words would someone use when working on a task this rule applies to?*

```yaml
# Bad — too generic
tags: [code]
triggers: [function]

# Good — specific to the actual concept
tags: [database, connection, pooling, timeout, postgres]
triggers: [create_engine, SessionLocal, get_db, connection_pool]
```

**Use `_mandatory/` sparingly.** Every mandatory rule costs tokens on every prompt. Reserve for security gates and testing requirements.

**Run `lint` after adding rules:**

```bash
clawness lint
```

`lint` checks required fields and **rejects vague phrasing** — a rule that says "validate input *where appropriate*" or "*try to* handle errors" isn't enforceable. State the rule precisely.

**Check retrieval still works after adding rules:**

```bash
clawness eval     # MRR@5 + hit-rate against tests/ground_truth.json
```

If you add rules in a new area, add a query or two to `tests/ground_truth.json` so the eval set keeps pace with the corpus.

---

## Sub-Agents

Clawness ships seven adversarial sub-agents that Claude Code can delegate to. The main ones are below; the full list with model/effort settings is in the [Configuration](#agent-model-configuration) table.

### Security Red Team / Blue Team

When you say *"run a security audit on the auth module"*, the workflow rule tells Claude to:
1. **Delegate to `security-red-team`** — thinks like an attacker, runs OWASP Top 10, searches for CVEs published *this month* affecting your stack
2. **Delegate to `security-blue-team`** — takes the red team report, triages findings, proposes exact code fixes, adds hardening measures
3. **Synthesize** — Claude merges both reports into a prioritized action plan

### Code Critic

For code reviews before merge. Focuses on bugs, performance, edge cases, and maintainability — the things the original author is blind to.

### Architecture Challenger

Devil's advocate for design decisions. Stress-tests assumptions: *what if it's 10x the load? what if this component fails? is there a simpler alternative?*

### Triggering Agents

You can invoke them directly:

```
> have the security-red-team agent review the auth module
> have the code-critic agent review my latest changes
```

Or just describe the task naturally — the workflow rules tell Claude when to reach for them:

```
> run a security audit on this project
> review the code before we merge
> should we use PostgreSQL or MongoDB for this?
```

**Proactive offers.** The agent-spawning skills (`audit`, `review`, `perf`) don't auto-run, because launching several sub-agents is expensive. Instead, when your prompt sounds like a security audit, code review, or performance check, Clawness nudges Claude to *offer* — e.g. "Sounds like you want a security audit — want me to run the red team / blue team now?" — and it only spawns the agents once you say yes. You can always skip the offer and run them directly with `/clawness:audit`, `/clawness:review`, or `/clawness:perf`.

---

## CLI Reference

The CLI is optional — everyday use needs no commands. It's installed by the **manual installer** (and by any `pip install` of the package), which puts a `clawness` command on your PATH. **Plugin-only users:** the rule injection, agents, skills, and plan gate all work without the CLI; to get the `clawness` command too, run `pip install -e <plugin-dir>` (or just do a [manual install](#option-2-manual-install)).

```bash
# Retrieve rules for a task description
clawness query "implement async REST endpoint"
clawness query "handle null values" --domain typescript
clawness query "set up logging" --top-k 3 --budget 2000

# Scan a project and suggest rules
clawness init /path/to/project
clawness init . --write    # create .clawness/rules/ in this project

# Corpus management
clawness stats             # show rule counts by domain + per-turn token estimate
clawness lint              # validate rule files (incl. vague-phrasing check)
clawness bench             # benchmark retrieval latency
clawness eval              # retrieval quality: MRR@5 + hit-rate vs. ground truth
clawness eval --floor-mrr 0.85 --floor-hit 0.95   # fail below floors (CI gate)

# Plan gate (on by default; normal flow uses native plan mode)
clawness plan status       # show gate state
clawness plan off          # disable for this project
clawness plan approve      # manual override (headless use)

# Emit an AGENTS.md so any agent (not just Claude Code) can use the CLI
clawness agents-md --write

# Point at a different rules directory
clawness --rules-dir /path/to/rules stats
```

> If `clawness` isn't found after install, your Python user-scripts directory isn't on your PATH. Either add it, or use the identical long form `python -m clawness.cli <command>` (`python3` on macOS/Linux), which works from any directory.

---

## What Ships

| Component | Count | Purpose |
|-----------|-------|---------|
| **Rules** | 114 across 18 domains | Coding standards, injected per-prompt |
| **Agents** | 7 sub-agents | Security red/blue team, code critic, test writer, perf auditor, refactor advisor, architecture challenger |
| **Skills** | 6 slash commands | `/clawness:audit`, `/clawness:review`, `/clawness:test`, `/clawness:perf`, `/clawness:add`, `/clawness:status` |
| **Hooks** | 5 (rule injection, output compression, plan gate, git check, dependency bootstrap) | Automatic context management & workflow enforcement |
| **CLI** | 8 commands | query, init, stats, lint, bench, eval, plan, agents-md |
| **Installers** | bash + PowerShell (with matching uninstallers) | 7-step setup for Windows, macOS, Linux |
| **Plugin manifest** | marketplace + plugin | For `claude plugin install` |

### Rule Domains

| Domain | Rules | Covers |
|--------|-------|--------|
| `general` | 17 | Cross-cutting: abstraction/YAGNI, comments, memory, nesting, magic numbers, immutability, dependency selection, versioning/lockfiles, linting, naming, validation, logging, env config, accessibility, git, performance |
| `nextjs` | 10 | Server/Client components, data fetching, caching, layouts, metadata, Server Actions |
| `fastapi` | 8 | Pydantic v2, dependency injection, async, error handling, CORS, DB sessions |
| `meta` | 8 | Rationalization counters — rebuttals to common AI shortcuts ("too simple to test", hardcode "temporarily", "I'll refactor later", trusting input) |
| `python` | 7 | Async I/O, imports, error handling, type hints, mutable defaults, context managers, pathlib |
| `workflows` | 7 | Multi-agent orchestration (security audit, code review, testing, perf, refactoring, architecture, parallel research) |
| `capacitor` | 6 | Platform detection, permissions, lifecycle, WebView, sync, App Store |
| `css` | 6 | `!important`, relative units, flex/grid layout, custom properties, responsive, focus states |
| `docker` | 6 | Layer caching, multi-stage builds, non-root, secrets, tag pinning, slim images |
| `java` | 6 | Null safety, equals/hashCode, try-with-resources, exceptions, immutability, collections |
| `go` | 5 | Error handling, nil maps, context, goroutine lifecycle, data races |
| `rust` | 5 | unwrap/expect, error handling, clone, unsafe, iterators |
| `sql` | 5 | N+1 queries, indexes, transactions, `SELECT *`, migrations |
| `security` | 5 | XSS, SQLi, auth, secrets, deps *(mandatory)* |
| `react` | 4 | Hooks, state management, list keys, forms |
| `typescript` | 4 | Null safety, async errors, strict mode, Zod |
| `bash` | 4 | Strict mode, quoting, error checking, shellcheck |
| `testing` | 1 | Test coverage for new code *(mandatory)* |

The 7 **mandatory** rules (always injected) are the 5 `security` rules, the 1 `testing` rule, and 1 current-practices rule (counted under `general`).

---

## Configuration

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `CLAW_RULES_DIR` | (next to hook script) | Override global rules directory |
| `CLAW_TOP_K` | `5` | Max ranked rules per prompt |
| `CLAW_BUDGET` | `4000` | Max tokens for the rule block |
| `CLAW_MIN_RELEVANCE` | `0.06` | TF-IDF cosine floor for ranked rules — below it a rule is treated as noise and not injected. Raise it to be stricter (fewer, more on-topic rules), set `0` to disable the floor |
| `CLAW_NO_MEMORY` | (unset) | Don't auto-create `.clawness/memory.md` on first session |
| `CLAW_MEMORY_BUDGET` | `2000` | Max characters of project memory injected per turn (keeps the tail on overflow) |
| `CLAW_NO_STACK_NOTE` | (unset) | Don't inject the detected-stack note at session start |
| `CLAW_VERBOSE` | (unset) | Render mandatory rules in full (`WHEN`/`BAD`/`GOOD`) instead of compact — more tokens per turn |
| `CLAW_COMPACT` | (unset) | Also render ranked rules compactly (directive only) — fewer tokens per turn |
| `CLAW_NO_PLAN_GATE` | (unset) | Disable the plan gate globally |
| `CLAW_NO_GIT_CHECK` | (unset) | Stop offering to `git init` when a project isn't under version control |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Claude Code's config dir — the installer/uninstaller follow it if you've relocated it |
| `CLAUDE_CODE_SUBAGENT_MODEL` | (none) | Override model for ALL sub-agents |

### Agent Model Configuration

Clawness uses a two-tier model strategy:

- **Orchestrator (your main Claude Code session):** Opus 4.8 — handles planning, synthesis, and coordination
- **Sub-agents (the 7 worker agents):** Sonnet 4.6 — handles focused analysis tasks at ~80% lower cost

Start Claude Code with Opus for orchestration:

```bash
claude --model claude-opus-4-8
```

Sub-agents automatically run on Sonnet 4.6. When the orchestrator delegates to a sub-agent (e.g. the red team), the sub-agent runs on Sonnet 4.6 and returns its findings to Opus for synthesis. You get Opus-quality coordination with Sonnet-speed execution.

| Agent | Default Model | Effort | Max Turns | Why |
|-------|--------------|--------|-----------|-----|
| `security-red-team` | claude-sonnet-4-6 | high | 25 | Thorough vulnerability scanning |
| `security-blue-team` | claude-sonnet-4-6 | high | 25 | Fix proposals need reasoning |
| `code-critic` | claude-sonnet-4-6 | medium | 15 | Read-only code analysis |
| `test-writer` | claude-sonnet-4-6 | medium | 20 | Writes and runs test files |
| `perf-auditor` | claude-sonnet-4-6 | medium | 15 | Performance pattern matching |
| `refactor-advisor` | claude-sonnet-4-6 | medium | 15 | Read-only code smell detection |
| `arch-challenger` | claude-sonnet-4-6 | high | 15 | Architecture stress-testing |

### Changing Model Defaults

**Change a single agent's model** — edit its `.md` file in `~/.claude/agents/`:

```yaml
# Aliases (auto-update when Anthropic releases new versions)
model: haiku        # cheapest — fast lookups, simple tasks
model: sonnet       # balanced — resolves to latest Sonnet
model: opus         # premium — resolves to latest Opus

# Pinned versions (exact model, won't change)
model: claude-haiku-4-5-20251001
model: claude-sonnet-4-6
model: claude-opus-4-8
model: claude-opus-4-6    # older Opus, cheaper
```

**Change ALL sub-agents at once** — set an environment variable:

```bash
# Run all sub-agents on Haiku (cheapest possible)
export CLAUDE_CODE_SUBAGENT_MODEL="claude-haiku-4-5-20251001"

# Or use the latest Sonnet (alias, auto-updates)
export CLAUDE_CODE_SUBAGENT_MODEL="sonnet"
```

**Change the orchestrator model** — this is your main Claude Code session:

```bash
# Start with Opus 4.8 (recommended for orchestration)
claude --model claude-opus-4-8

# Or switch mid-session
/model claude-opus-4-8

# Budget option: Sonnet for everything (orchestrator + sub-agents)
claude --model claude-sonnet-4-6
```

**The `effort:` field** controls reasoning depth independently of model:

```yaml
effort: low         # quick, shallow
effort: medium      # balanced (default for most agents)
effort: high        # thorough (default for security + architecture)
effort: max         # maximum reasoning — expensive, use sparingly
```

**The `maxTurns:` field** caps tool calls per agent run, preventing runaway costs.

### Where Rules Live

| Location | Scope | When Loaded |
|----------|-------|-------------|
| `~/.claude/clawness/rules/` | Global | Every prompt, every project |
| `<project>/.clawness/rules/` | Project | Only when working in that project |
| `<project>/.clawness/rules/_mandatory/` | Project mandatory | Every prompt while in that project |

> The `~/.claude/clawness/rules/` path applies to a **manual** install. With the **plugin** install, the global rules ship inside the plugin and load from its cache automatically — you don't manage that path. Either way, project rules in `<project>/.clawness/rules/` work the same.

---

## How It Compares

| | Writ | Clawness | CLAUDE.md |
|---|---|---|---|
| Rule selection | Hybrid RAG (BM25 + vector + graph) | Hybrid (BM25 + TF-IDF + RRF + concepts) | All rules, every turn |
| Token cost per turn | selected rules only | ~1,300/turn (~470 mandatory + ~5 selected) | all 115 rules (~13k+) every turn |
| Infrastructure | Neo4j + Docker + ONNX (~2 GB) | PyYAML (~200 KB) | None |
| Install time | ~5 minutes | ~5 seconds | Copy/paste |
| Mandatory rules | Yes | Yes | Manual discipline |
| Context budget | Yes | Yes | No |
| Output compression | No | Yes (PostToolUse hook) | No |
| Sub-agents | No | 7 adversarial agents | No |
| Per-project rules | No | Yes (.clawness/rules/) | Yes (per-directory CLAUDE.md) |

---

## Troubleshooting

**Plugin install: skills/hooks not showing up**
Run `/reload-plugins`, or check `claude plugin list`. On first session, a background `SessionStart` hook installs **PyYAML** into your environment (a few seconds) — that's all the default lexical retrieval needs. Check `bootstrap.log` in the plugin's data directory for progress, and run `claude --debug` to see hook activity. (Make sure Python 3.10+ is on your PATH — without it the hooks can't run.)

**Hook not firing / Claude doesn't see rules**
Check `~/.claude/settings.json` contains the hook config. Run the installer again — it's idempotent and will report what's already configured vs what it adds.

**PowerShell: "running scripts is disabled"**
```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**"No module named yaml"**
```bash
python -m pip install pyyaml --user
```

**Wrong rules appearing / right rules not appearing**
Test what the retriever sees for your exact prompt:
```bash
python -m clawness.cli query "your exact prompt text here"
```
Improve `tags` and `triggers` fields on the rules that should match.

**Too many mandatory rules eating tokens**
Move rules from `_mandatory/` to a ranked domain. Only security gates and test requirements should be mandatory.

**Want to temporarily disable Clawness**
Rename the hook entries in `~/.claude/settings.json` or delete them. Re-run the installer to add them back.

---

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

Inspired by [infinri/Writ](https://github.com/infinri/Writ), which pioneered hybrid-RAG rule retrieval for AI coding agents. Clawness takes the same core ideas and repackages them without the infrastructure.
