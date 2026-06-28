# Changelog

All notable changes to Clawness will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-06-28

### Added
- **Per-codebase memory (`.clawness/memory.md`).** A project-local lessons-learned
  log that the hook injects into every prompt, right after the rules block — the
  auto-recalled "memories" pattern (cf. Cursor/Windsurf), but version-controllable
  and shared with your team. Recurring gotchas, build quirks, and hard-won fixes
  survive across sessions instead of being re-discovered each time. Bounded by
  `CLAW_MEMORY_BUDGET` (chars, default 2000); when it overflows, the most recent
  lessons (file tail) are kept. `clawness init --write` seeds a starter file.
- **Relevance floor for ranked rules.** Ranked rules are now only injected when
  the prompt actually matches them, gauged on TF-IDF cosine (`CLAW_MIN_RELEVANCE`,
  default 0.06; `0` disables). RRF fusion scores are rank-based and don't encode
  match strength, so without a floor a signal-less prompt filled every `CLAW_TOP_K`
  slot with scattershot matches. Strong matches sit far above the floor, so the
  eval is unaffected (MRR@5 0.978, hit-rate 1.000 unchanged); only the noise tail
  is trimmed. Mandatory rules are never floored.
- **Project stack awareness (`hooks/stack_detect.py`, SessionStart).** Detects the
  project's language/framework stack from its files (same detection as `clawness
  init`) and injects a one-line note — e.g. "Detected project stack: Python,
  FastAPI, SQL" — so Claude starts the session already knowing the ecosystem.
  Opt-out `CLAW_NO_STACK_NOTE`.
- **Codebase-aware retrieval.** The `UserPromptSubmit` hook now detects the project
  stack (fresh each prompt) and applies a higher relevance floor
  (`CLAW_OFFSTACK_MIN_RELEVANCE`, default 0.15) to language/framework rules from
  stacks the project doesn't use. So a vague prompt in a Python repo no longer
  surfaces SQL/React/Capacitor noise — while a genuinely strong cross-domain match
  still passes (a real React question gets React rules, even after a mid-session
  `npm install`). Cross-cutting domains (general/meta/workflows/security/testing)
  are never penalized; an unknown stack disables the penalty. Opt-out
  `CLAW_NO_STACK_FILTER`. CLI/eval pass no stack, so retrieval quality is unchanged.
- **Auto-bootstrap on first session (`hooks/memory_init.py`, SessionStart).** The
  first time you open a project, Clawness creates `.clawness/memory.md` (seeded with
  a how-to line) and injects a note so Claude tells you it exists and that you can
  grow it by saying "remember this: …". Gated to real git work trees (never home /
  filesystem root), silent once the file exists, opt-out via `CLAW_NO_MEMORY`.
  Mirrors the existing `git_check` SessionStart pattern — hooks can't prompt the
  user directly, so Claude relays the note.
- **Rule `WF-LESSONS-001`.** Tells Claude to append a terse lesson to
  `.clawness/memory.md` immediately when asked to "remember" something, or on the
  *second* occurrence of a mistake/gotcha otherwise — keeping entries short and
  deduplicated, and reading the log before repeating work in an area it covers.

### Changed
- **Ranked rules now display `relevance=` (TF-IDF cosine), not `score=` (RRF).**
  The old `score` was the rank-based RRF value — ~0.03 for every rule regardless
  of match strength — which read as if it were below the `CLAW_MIN_RELEVANCE=0.06`
  floor and falsely suggested the floor wasn't working. The shown number is now
  the actual TF-IDF relevance the floor is gauged on (e.g. `relevance=0.133`), so
  it's interpretable and directly comparable to the floor. Ordering is still RRF
  fusion, so retrieval quality (and the eval) is unchanged.

### Fixed
- **Hook forces UTF-8 stdout.** The injected rules/memory blocks contain
  non-ASCII (em-dashes, etc.); on a Windows console defaulting to cp1252 these
  could be mangled or raise `UnicodeEncodeError` and drop rule injection. The
  `UserPromptSubmit` hook now reconfigures stdout to UTF-8 regardless of platform.
- **Memory block footer no longer reads as a lesson.** `render_memory_block` now
  puts a blank line before its upkeep footer, so it isn't glued to the file's
  `## Lessons` heading.
- **git-presence check no longer false-alarms on workspace/monorepo parents.**
  `git rev-parse` only searches upward, so opening a parent folder whose actual
  repositories live in subfolders made `git_check` wrongly report "not under
  version control". It now also does a bounded downward scan (depth ≤ 4, capped
  dir count, skipping `node_modules`/`.venv`/build dirs and other vendored trees)
  so a tree that does use git isn't flagged.
- **Stack detection no longer mislabels plain Node projects as React.** A bare
  `package.json` now maps to Node/TypeScript only; React/Next/etc. are inferred
  from actual dependencies (deep scan), so an Express or CLI project isn't tagged
  React. Improves both `clawness init` and the new stack-awareness note.

## [0.3.0] - 2026-06-28

### Fixed
- **Plan gate no longer blocks Claude Code's plan-mode plan file.** The
  `PreToolUse` gate denied *all* Write/Edit until a plan was approved — including
  the plan file you write in order to *get* approval, a catch-22 that broke plan
  mode. Writes under `<config>/plans/` are now always exempt (project-file edits
  are still gated as before).

### Removed
- **model2vec / semantic embeddings, entirely.** It was a poor fit for a
  per-prompt hook: each turn is a fresh process, so the model reloaded every
  time (no warm state without a daemon, which we won't add), and on our eval it
  scored no better than lexical + concept retrieval. Gone: `embeddings.py`, the
  `[semantic]` pip extra, the `numpy` dependency, `CLAW_SEMANTIC` /
  `CLAW_EMBED_MODEL`, the installer `--semantic` flag, and all related docs.
  **PyYAML is now the only dependency.**

### Changed
- Retrieval is now purely **BM25 + TF-IDF + RRF + concept expansion** — pure
  Python, ~1 ms per prompt, no models, no downloads, no `numpy`.
- **Expanded the concept dictionary to 26 groups** (null-safety, naming, docs,
  refactoring, immutability, build/CI, git, shell, mobile, and a "shortcut"
  group that surfaces the rationalization rules), plus more terms in existing
  groups. The concept layer is the "different words, same idea" reach that
  replaces semantic embeddings — instantly and with zero dependencies.

## [0.2.2] - 2026-06-28

### Fixed
- **Rule injection silently failing (`UserPromptSubmit hook error` / no rules).**
  The per-prompt hook loaded the model2vec semantic model on every turn, which
  blew the hook timeout (and on a fresh machine tried to download ~30 MB inline),
  so nothing got injected. Retrieval is now **lexical + concept by default**
  (~1 ms per prompt); semantic is opt-in. On our ground-truth eval the lexical
  path scores at least as well, so this is faster with no quality loss.

### Changed
- **Semantic embeddings (model2vec) are now opt-in** via `CLAW_SEMANTIC=1`
  (was on-by-default). The first-run bootstrap installs only PyYAML by default —
  no ~30 MB model download behind your back — and the per-prompt hook never loads
  the model. The manual installer flag flips from `--no-semantic` to `--semantic`
  (PowerShell: `-Semantic`). `stats` now reports semantic as off/opt-in.

## [0.2.1] - 2026-06-27

### Fixed
- Plugin install failed: `marketplace.json` declared the plugin `source` as a
  second GitHub clone of the same repo. Corrected to `"./"` (the plugin *is* the
  marketplace repo), so install reuses the already-fetched copy — no redundant
  clone, and no install-time clone at all for local marketplaces.
- Manual installer hardcoded a single Python interpreter into the settings.json
  hooks. It now writes the same portable `python3 → python → py` picker as the
  plugin hooks, so hooks run even when only the Windows `py` launcher (or only
  `python3`) is on PATH.

## [0.2.0] - 2026-06-27

### Added
- 7 new rule domains: Go, Rust, Java, SQL, bash, CSS, Docker
- Semantic (model2vec) retrieval, on by default — fuses with BM25 + TF-IDF +
  concept expansion via Reciprocal Rank Fusion; opt out with `CLAW_NO_SEMANTIC`
- Plan-approval gate (default-on, opt-out), riding Claude Code's native plan mode,
  with a `plan` CLI command (`status` / `on` / `off` / `approve` / `reset`)
- SessionStart git-presence check (nudges to `git init`; silence with `CLAW_NO_GIT_CHECK`)
- SessionStart dependency-bootstrap hook (installs PyYAML / model2vec in the background)
- `agents-md` CLI command — emit an AGENTS.md so any agent can drive the CLI
- `meta` domain: 8 rationalization-counter rules that rebut common AI shortcuts
  (skip tests, "too simple", hardcode "temporarily", trust input) — surfaced by
  the retriever when a prompt signals a shortcut
- Vagueness lint: `lint` now rejects unenforceable weasel phrasing in rules
- Retrieval-quality eval harness: `eval` command with a labeled ground-truth set,
  reporting MRR@5 + hit-rate against configurable floors (gates CI)
- Token efficiency: mandatory rules render compactly (~45% smaller fixed block);
  `CLAW_VERBOSE` / `CLAW_COMPACT` knobs; `stats` reports per-turn token estimate

### Changed
- Rule corpus expanded from 57 to 114 rules; now 18 domains total
- Renamed throughout to **Clawness** — package `clawness` (was `writ_lite`),
  env vars `CLAW_*` (were `WRIT_*`), project rules in `.clawness/` (was `.writ/`)
- `clawness` is now installed as a real command (editable `pip install`)
- Plugin distribution via `.claude-plugin` marketplace + plugin manifests (`claude plugin install`)

## [0.1.0] - 2026-06-24

### Added
- Hybrid retrieval engine (BM25 + TF-IDF + Reciprocal Rank Fusion)
- 57 rules across 10 domains: mandatory security, Next.js, FastAPI, Capacitor, React, TypeScript, Python, general, workflows
- 7 adversarial sub-agents: security red/blue team, code critic, test writer, performance auditor, refactor advisor, architecture challenger
- 6 skills (slash commands): `/clawness:audit`, `/clawness:review`, `/clawness:test`, `/clawness:perf`, `/clawness:add`, `/clawness:status`
- UserPromptSubmit hook for automatic rule injection
- PostToolUse hook for bash output compression
- Global rules (~/.claude/clawness/rules/) + project rules (.clawness/rules/) layering
- `clawness init` project scanner with auto-detection for Next.js, FastAPI, Capacitor, React, TypeScript, Python
- `clawness query`, `stats`, `lint`, `bench` CLI commands
- Plugin manifest (.claude-plugin/plugin.json) and marketplace manifest
- PowerShell and bash installers (7-step, idempotent)
- Per-agent model configuration (default: claude-sonnet-4-6 for sub-agents, claude-opus-4-8 recommended for orchestrator)
