# Plan — deepening Clawness on OpenClaw beyond the 4 hooks

**Status: RESEARCH / PROPOSAL.** A separate initiative from the commands work
(`feat/openclaw-commands`, COMMANDS-PLAN.md). Its own branch + version when started.

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

### 2. Memory slot → `.clawness/memory.md` as a first-class memory section
- **Maps to:** `clawness/memory.py` project-lessons injection (now via `before_prompt_build`).
- **SDK:** `registerMemoryPromptSection`, `registerMemoryCapability`,
  `registerMemoryCorpusSupplement`, `registerMemoryPromptSupplement`; host memory slot is
  `plugins.slots.memory`.
- **Win:** a real memory section vs a prompt append; composes with host memory.
- **Step 1:** confirm whether registering a memory section DISPLACES or COEXISTS with the
  bundled slot, and whether our ranked-lessons output fits `registerMemoryPromptSection`.

### 3. Context engine → rules retrieval as a native engine
- **Maps to:** `clawness/core.py` BM25+TF-IDF+RRF ranking (now via `before_prompt_build`).
- **SDK:** `registerContextEngine`.
- **Win:** architecturally cleaner than prompt-append; composes with host context handling.
- **Risk:** likely the largest surface; may duplicate what `before_prompt_build` already
  does well. Verify the contract before assuming it's worth the rewrite — the current
  append path is live-confirmed and cheap.

### 4. Install/skill vetting → extend the trust ledger
- **Maps to:** `clawness/trust.py` (TOFU fingerprints + injection-tell scan), today a
  SessionStart diff.
- **SDK:** `before_install` hook + `registerSkillsChangeListener`.
- **Win:** vet a plugin/skill AT install and watch skill changes natively.
- **Step 1:** read `before_install` payload — does it expose the artifact path/content to
  scan, and can the hook block/ask?

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
