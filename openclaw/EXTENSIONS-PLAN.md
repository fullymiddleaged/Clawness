# Plan — deepening Clawness on OpenClaw beyond the 4 hooks

**Status: SHIPPED in 1.13.0 (opportunities #1, #2, #4); #3 evaluated and declined.**
All four SDK contracts were verified against the real installed host (openclaw
2026.7.1-2). Compaction re-orientation (#1), the searchable memory corpus (#2), and
install-time trust vetting (#4) ship as OpenClaw-only adapter code
(`openclaw/src/{compaction,memory,install}.ts` + `openclaw/pyhooks/*.py`); #3 (a native
context engine) was declined for cause — see its section. Built under the constraint that
nothing changes Claude Code / shared-engine behaviour: all logic lives in `openclaw/`,
reusing `clawness.*` read-only.

Historical framing below (contract questions, "step 1s") is kept for the record; each
opportunity now carries an **Outcome**.

## Why

The adapter today uses 4 event hooks (`before_prompt_build`, `session_start`,
`before/after_tool_call`) + `registerCommand`. The installed OpenClaw SDK (2026.6.34)
exposes many more `register*` capabilities and ~20 typed hooks. Four map directly onto
Clawness features that currently have **no OpenClaw home** or sit shoehorned into
`before_prompt_build`. Method names below are **verified present** in the installed SDK
types; **none of their contracts (args, return shapes, manifest `contracts.*` entries)
are verified yet** — that is step 1 of each.

**How to verify a contract:** `npm i openclaw` in a throwaway scratch dir (see the "Verify
SDK type shapes via throwaway npm i openclaw" memory — it is NOT a build dep). Then in
`node_modules/openclaw`, `grep -rn "<MethodName>" dist/**/*.d.ts` and read the param/return
types out of `dist/types-*.d.ts`. The 2026.6.34 types name the methods; read the real one.

## Opportunities, ranked by fit

### 1. Compaction hooks → native context-watch + handoff (HIGHEST value)
- **Maps to:** `clawness/context_watch.py` + `clawness/handoff.py` — today the most
  Claude-Code-specific corner, only advisory (warns at 70/85%).
- **SDK:** `before_compaction` / `after_compaction` hooks + `registerCompactionProvider`.
- **Win:** write `.clawness/handoff.md` + flush a memory lesson AT compaction time,
  natively, instead of guessing a %. The one existing Clawness feature with zero plugin
  surface today.
- **Step 1:** read the `before_compaction` hook payload + result type and
  `registerCompactionProvider`'s contract. Decide hook-only vs provider.
- **Outcome (SHIPPED, hook-only):** `before_/after_compaction` are observe-only
  (`=> void`) — no model in the loop, so a hook cannot AUTHOR a rich handoff. But
  `after_compaction`'s `ctx` carries `workspaceDir` and the real token budget, and the
  adapter can enqueue a next-turn injection. Since rules + ranked memory self-heal on the
  next `before_prompt_build`, the honest native win is to re-inject only the
  SessionStart-only orientation the compaction drops (handoff + stack) plus a notice.
  `registerCompactionProvider` (a summarization backend) was NOT used — it replaces the
  summarizer, which isn't the goal. See `src/compaction.ts`.

### 2. Memory slot → `.clawness/memory.md` as a first-class memory section
- **Maps to:** `clawness/memory.py` project-lessons injection (now via `before_prompt_build`).
- **SDK:** `registerMemoryPromptSection`, `registerMemoryCapability`,
  `registerMemoryCorpusSupplement`, `registerMemoryPromptSupplement`; host memory slot is
  `plugins.slots.memory`.
- **Win:** a real memory section vs a prompt append; composes with host memory.
- **Step 1:** confirm whether registering a memory section DISPLACES or COEXISTS with the
  bundled slot, and whether our ranked-lessons output fits `registerMemoryPromptSection`.
- **Outcome (SHIPPED, corpus supplement):** `registerMemoryPromptSection`/`…Supplement`'s
  builder is SYNCHRONOUS, returns `string[]`, and receives no prompt — so it can't shell to
  our async Python ranker and can't rank against the turn. Dead end for us. But
  `registerMemoryCorpusSupplement({ search, get })` is async and query-bearing and
  documented "additive (non-exclusive)" — it COEXISTS with the bundled slot. So we expose
  `.clawness/memory.md` as a searchable corpus ranked by `clawness.memory`, additive to the
  per-turn injection. Known limit: `search`/`get` carry no cwd, so the supplement resolves
  the project from the most-recent session cwd (fine for single-workspace; a multi-workspace
  live pass is owed). See `src/memory.ts` + `pyhooks/memory_corpus.py`.

### 3. Context engine → rules retrieval as a native engine
- **Maps to:** `clawness/core.py` BM25+TF-IDF+RRF ranking (now via `before_prompt_build`).
- **SDK:** `registerContextEngine`.
- **Win:** architecturally cleaner than prompt-append; composes with host context handling.
- **Risk:** likely the largest surface; may duplicate what `before_prompt_build` already
  does well. Verify the contract before assuming it's worth the rewrite — the current
  append path is live-confirmed and cheap.
- **Outcome (DECLINED, for cause):** the contract confirms the warning. `ContextEngine` is
  a full transcript store — `bootstrap`/`ingest`/`ingestBatch`/`afterTurn`/`maintain`/
  `assemble` — and `registerContextEngine` is documented an **"exclusive slot — only one
  active at a time."** Registering ours would mean reimplementing OpenClaw's entire context
  storage and assembly and DISPLACING the host default, to achieve what `before_prompt_build`
  already does by appending our block. That is strictly more risk (owning transcript state,
  no composition) for no user-visible gain, and it collides with the "don't break core /
  keep dual support" constraint. Not built; this Outcome is the completion of #3.

### 4. Install/skill vetting → extend the trust ledger
- **Maps to:** `clawness/trust.py` (TOFU fingerprints + injection-tell scan), today a
  SessionStart diff.
- **SDK:** `before_install` hook + `registerSkillsChangeListener`.
- **Win:** vet a plugin/skill AT install and watch skill changes natively.
- **Step 1:** read `before_install` payload — does it expose the artifact path/content to
  scan, and can the hook block/ask?
- **Outcome (SHIPPED):** yes on both. `before_install`'s event carries the artifact's
  on-disk `sourcePath`; the result type `{ findings?, block?, blockReason? }` lets us both
  contribute findings AND block. `pyhooks/install_scan.py` walks the artifact (skipping
  vendored dirs), scans each line via `clawness.trust.scan_injection_tells`, and returns
  findings with line numbers; `src/install.ts` surfaces all findings (ADVISORY) and arms
  `block` only when the user opts in (`CLAW_INSTALL_BLOCK=1`) AND a CRITICAL tell is present.
  **Blocking is opt-in, not default — corrected in 1.13.1 after a live pass:** the tell scan
  false-positives on any artifact that documents these patterns (Clawness's own repo scored
  37 "critical" from `trust.py` + the security rules), so a block-by-default would stop
  legitimate installs. `CLAW_NO_INSTALL_SCAN=1` turns it off. `registerSkillsChangeListener`
  (watch skills change) was left for a later pass — the install-time gate is the higher-value
  half.

## Constraints (unchanged from the commands work)
- Keep `src/index.ts` the ONLY OpenClaw-touching file; logic host-agnostic + tested.
- Every new handler fails toward doing nothing (fail-open), like the existing 4.
- No `openclaw` build dep. Rebuild + commit `openclaw/dist/src` on any `src/` change.
- Each capability is a user-visible feature → minor version bump + CHANGELOG at its release.

## Open questions
1. Do any of these need a manifest `contracts.*` declaration (like tools do) to activate,
   or is `register*` at `register()` time enough? Check per capability.
2. Does the one-shot `openclaw agent` CLI exercise compaction / memory-section paths, or
   are they (like commands + SessionStart notes) only reachable on a real interactive
   host? Determines how much is verifiable offline.
