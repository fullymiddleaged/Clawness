# CLAUDE.md — working on Clawness

Orientation for agents/devs working **on** this repo. User-facing docs live in
[README.md](README.md); release history in [CHANGELOG.md](CHANGELOG.md). This file
captures architecture, conventions, and the *why* behind non-obvious decisions.

## What this is
A **Claude Code plugin** that retrieves relevant coding rules and injects them
into every prompt via a `UserPromptSubmit` hook. Pure Python; **PyYAML is the only
dependency**. No ML models, no services, no Docker.

## Architecture (request flow)
1. `hooks/claude_hook.py` (UserPromptSubmit) loads global rules (plugin/clone
   `rules/`) + project rules (`<project>/.clawness/rules/`), retrieves, prints the
   block to stdout → Claude sees it.
2. Retrieval engine = `clawness/core.py`: **BM25 + TF-IDF fused via RRF**, over a
   **concept-expanded** token stream (`_CONCEPT_GROUPS`) + light stemming.
   Mandatory rules (`rules/_mandatory/`) always injected; rest ranked + budget-capped.
   A **relevance floor** (`CLAW_MIN_RELEVANCE`, default 0.06, gauged on TF-IDF cosine —
   not RRF, which is rank-based) drops scattershot matches so signal-less prompts
   inject few/no ranked rules. **Codebase-aware:** the hook detects the project stack
   (`detect_stack` → `scan_project`, fresh each prompt) and passes it to `Clawness`;
   off-stack language/framework rules (`_STACK_DOMAINS` minus detected) face a higher
   floor (`CLAW_OFFSTACK_MIN_RELEVANCE`, default 0.15) so e.g. a Python repo doesn't
   surface SQL/React noise, while strong cross-domain matches still pass. Cross-cutting
   domains (general/meta/workflows/security/testing) are never penalized. Passing no
   stack (CLI/eval) disables the penalty, so eval is unaffected. ~1ms/prompt + ~3ms scan.
3. **Project memory** (`<project>/.clawness/memory.md`): if present, the hook appends
   it verbatim after the rules block (`render_memory_block` in `core.py`) — a
   per-codebase lessons log, not a ranked rule, so it never touches the engine.
   Char-bounded by `CLAW_MEMORY_BUDGET` (default 2000), keeping the tail on overflow.
   `WF-LESSONS-001` is the rule that tells Claude to maintain it. The file is
   auto-created on first session by `hooks/memory_init.py` (SessionStart) — gated to
   git work trees, opt-out `CLAW_NO_MEMORY`; it injects a note (like `git_check`) so
   Claude announces the file to the user, since hooks can't prompt directly.

## Key files
- `clawness/core.py` — engine (rules loader, tokenizer + `_CONCEPT_GROUPS`, BM25,
  TF-IDF, RRF, `Clawness` class, `rank_ids`, rendering, `render_memory_block`).
- `clawness/cli.py` — `clawness` CLI: query, stats, lint, bench, eval, init, plan, agents-md.
- `clawness/plan.py` — plan-gate logic (`gate_decision`, `is_plan_file`, session approval).
- `hooks/` — runtime hooks (`claude_hook`, `compress_output`, `plan_gate`, `git_check`,
  `memory_init`, `stack_detect`, `ensure_deps`) + setup helpers (`setup_settings/agents/skills` — manual install only).
- `rules/<domain>/*.yml` — the corpus (115 rules / 18 domains; `_mandatory/` = always-on).
- `agents/*.md`, `skills/<name>/SKILL.md` — auto-discovered by the plugin.
- `.claude-plugin/{plugin.json,marketplace.json}` — plugin + marketplace manifests.
- `tests/ground_truth.json` — labeled eval queries (grow it when adding rule areas).

## Design decisions (don't undo without reading these)
- **Lexical + concept retrieval only.** model2vec/semantic was removed in 0.3.0:
  a per-prompt hook is a fresh process every turn, so the model reloaded each time
  (blew the hook timeout), and it scored no better than lexical on the eval. The
  **concept dictionary (`_CONCEPT_GROUPS`) is our "semantic"** — enrich *that* for
  better recall, never add a model to the hot path.
- **Hook commands use a portable interpreter picker** `for p in python3 python py; …`
  (Windows has no `python3`; Claude runs hooks via a POSIX shell). Same picker in
  `plugin.json` and what `setup_settings.py` writes.
- **Plan gate rides native plan mode.** `PreToolUse` denies edits until ExitPlanMode
  is recorded for the session. **Plan-file writes (`<config>/plans/`) are exempt**
  (`is_plan_file`) — gating them is a catch-22. Fails open on any error.
- **Token efficiency:** mandatory rules render compact (id+RULE only); `CLAW_VERBOSE`
  / `CLAW_COMPACT` toggle. Keep the per-turn block lean.
- **Two install paths:** plugin (hooks declared in `plugin.json`, loaded from cache)
  vs manual (`install.sh`/`install.ps1` → editable `pip install` + `setup_settings.py`
  writes hooks to `settings.json`). The plugin path does NOT install the `clawness`
  CLI — plugin users verify via `/clawness:status`.
- **Naming:** package `clawness`, env vars `CLAW_*`, project dir `.clawness/`. (The
  `infinri/Writ` mentions in README are upstream credit — leave them.)

## Dev workflow
- Test: `python -m pytest tests/` (set `CLAW_NO_PLAN_GATE=1` if the gate blocks your
  edits in this repo — but **unset it before running the suite**, since the plan-gate
  tests assert the gate is on and will fail with it disabled).
- Rules: `clawness lint` (rejects missing fields **and vague phrasing**),
  `clawness eval --floor-mrr 0.85 --floor-hit 0.95` (MRR@5/hit-rate; CI-gated),
  `clawness stats`, `clawness bench`.
- CI (`.github/workflows/ci.yml`) runs lint + tests + eval across ubuntu/macOS/windows × py3.10–3.14.

## Gotchas
- **Always pass `encoding="utf-8"` on file I/O and stdin/stdout.** The corpus uses
  em-dashes/smart-quotes; bare `open()`/`read_text()`/`sys.stdin` default to cp1252
  on Windows and mangle them into mojibake (`—` → `â€"`) *at load time*. `clawness
  lint` now flags non-UTF-8 / U+FFFD rule files; keep new reads/writes UTF-8.
- **Keep the hook ~1ms** — no heavy imports or model loads in `claude_hook.py`/`core.py`.
- **Version lives in 3 places** — bump `pyproject.toml`, `.claude-plugin/plugin.json`,
  `.claude-plugin/marketplace.json` together, and add a CHANGELOG entry.
- Rule YAML: `id, domain, severity, tags, triggers, when, rule, violation, correct`
  (concept terms must be single tokens; multi-word phrases never match).
- **`{{CURRENT_DATE}}`** in any rule field is replaced at render time with the live
  month + year (e.g. "June 2026") — see `_DATE_TOKEN`/`_current_date` in `core.py`.
  Substituted only on render, not in the search text, so retrieval stays
  date-independent. `ENF-CURRENT-001` uses it.
- Two things can't be tested from a sandbox: a real `pip install -e .` completing,
  and plugin hooks on a real Windows + python.org box. Smoke-test both before release.
