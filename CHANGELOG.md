# Changelog

All notable changes to Clawness will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-06-27

### Added
- 7 new rule domains: Go, Rust, Java, SQL, bash, CSS, Docker
- Semantic (model2vec) retrieval, on by default — fuses with BM25 + TF-IDF +
  concept expansion via Reciprocal Rank Fusion; opt out with `WRIT_NO_SEMANTIC`
- Plan-approval gate (default-on, opt-out), riding Claude Code's native plan mode,
  with a `plan` CLI command (`status` / `on` / `off` / `approve` / `reset`)
- SessionStart git-presence check (nudges to `git init`; silence with `WRIT_NO_GIT_CHECK`)
- SessionStart dependency-bootstrap hook (installs PyYAML / model2vec in the background)
- `agents-md` CLI command — emit an AGENTS.md so any agent can drive the CLI

### Changed
- Rule corpus expanded from 57 to 106 rules; now 17 domains total
- Plugin distribution via `.claude-plugin` marketplace + plugin manifests (`claude plugin install`)

## [0.1.0] - 2026-06-24

### Added
- Hybrid retrieval engine (BM25 + TF-IDF + Reciprocal Rank Fusion)
- 57 rules across 10 domains: mandatory security, Next.js, FastAPI, Capacitor, React, TypeScript, Python, general, workflows
- 7 adversarial sub-agents: security red/blue team, code critic, test writer, performance auditor, refactor advisor, architecture challenger
- 6 skills (slash commands): `/clawness:audit`, `/clawness:review`, `/clawness:test`, `/clawness:perf`, `/clawness:add`, `/clawness:status`
- UserPromptSubmit hook for automatic rule injection
- PostToolUse hook for bash output compression
- Global rules (~/.claude/clawness/rules/) + project rules (.writ/rules/) layering
- `clawness init` project scanner with auto-detection for Next.js, FastAPI, Capacitor, React, TypeScript, Python
- `clawness query`, `stats`, `lint`, `bench` CLI commands
- Plugin manifest (.claude-plugin/plugin.json) and marketplace manifest
- PowerShell and bash installers (7-step, idempotent)
- Per-agent model configuration (default: claude-sonnet-4-6 for sub-agents, claude-opus-4-8 recommended for orchestrator)
