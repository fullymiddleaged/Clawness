/**
 * notes.ts — run the SessionStart note hooks and collect their output.
 *
 * These are the same fail-silent, platform-neutral Python hooks Claude Code runs
 * at SessionStart. We run them in order and return each non-empty note so the
 * caller can inject them into the first turn. The Claude-specific ones
 * (plan_gate, context watch, model advisor) are intentionally NOT here — they
 * read Claude-only state and stay dormant in v1 (see the plan).
 */
import { runPythonHook } from "./bridge.js";
import { buildSessionPayload } from "./translate.js";
// Order mirrors plugin.json's SessionStart registration. ensure_deps (skills
// bootstrap) is omitted: skills are out of the core OpenClaw scope for now.
export const SESSION_NOTE_HOOKS = [
    "hooks/git_check.py",
    "hooks/memory_init.py",
    "hooks/handoff_check.py",
    "hooks/stack_detect.py",
    "hooks/changelog_check.py",
    "hooks/claude_md_check.py",
    "hooks/trust_ledger.py",
];
/**
 * Run every session-start note hook. Returns one entry per hook that produced
 * non-empty stdout. `noPython` is true when no interpreter was found (the first
 * hook that reports it short-circuits the rest — they'd all report the same).
 */
export async function runSessionNotes(args) {
    const payload = buildSessionPayload(args);
    const notes = [];
    for (const hook of SESSION_NOTE_HOOKS) {
        const result = await runPythonHook(hook, { ...payload });
        if (result.noPython)
            return { notes, noPython: true };
        const text = result.stdout.trim();
        if (text)
            notes.push({ hook, text });
    }
    return { notes, noPython: false };
}
