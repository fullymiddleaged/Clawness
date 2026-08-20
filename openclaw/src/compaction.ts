/**
 * compaction.ts — host-agnostic logic for OpenClaw's `after_compaction` hook.
 *
 * This is the native home for the one Clawness feature with no plugin surface on
 * Claude Code: the context watch + handoff. Claude Code can only GUESS when the
 * window is filling (70/85%); OpenClaw tells us exactly, by firing `after_compaction`
 * the moment it squashes older detail out of context.
 *
 * What we do about it, honestly: rules and ranked memory self-heal on the very next
 * prompt (OpenClaw re-runs `before_prompt_build` every turn), so those need nothing.
 * What a compaction DROPS and does NOT restore is the SessionStart-only orientation —
 * the handoff pickup and the stack note — plus the fact that detail was lost. So we
 * re-inject exactly that: a short notice, then the handoff + stack notes. We do NOT
 * fabricate a handoff (an observe-only hook has no model in the loop) and we do NOT
 * re-run the once-per-project nags (see REORIENTATION_NOTE_HOOKS in notes.ts).
 *
 * All logic here is host-agnostic and unit-tested; index.ts wires `after_compaction`
 * and calls `enqueueNextTurnInjection`. Fails toward doing nothing.
 */
import { runSessionNotes, REORIENTATION_NOTE_HOOKS, type SessionNote } from "./notes.js";

/** The short notice that leads the re-orientation, so the model knows why. */
export const COMPACTION_NOTICE =
  "[Clawness] The conversation was just compacted, so earlier detail may be gone. " +
  "If you were mid-task, re-establish context before continuing — the orientation " +
  "below is re-stated, and .clawness/memory.md + .clawness/handoff.md hold the durable record.";

/** One re-orientation message to enqueue after a compaction. */
export interface ReorientationNote {
  text: string;
  /** Idempotency suffix so the host de-dupes within a compaction but not across them. */
  key: string;
}

/**
 * Build the messages to re-inject after a compaction: the notice first, then each
 * orientation note the hooks produced. Returns [] when Python is absent or nothing
 * had anything to say (an empty handoff + no stack note is the common case, and it
 * should add zero noise beyond nothing — so we suppress the bare notice too).
 *
 * `marker` disambiguates one compaction from the next (the after_compaction event's
 * `previousSessionId` or `compactedCount`), so retries within one compaction de-dupe
 * while a later compaction re-fires.
 */
export async function buildReorientation(args: {
  cwd: string;
  sessionId: string;
  marker: string;
}): Promise<ReorientationNote[]> {
  let notes: SessionNote[] = [];
  try {
    const result = await runSessionNotes({
      cwd: args.cwd,
      sessionId: args.sessionId,
      hooks: REORIENTATION_NOTE_HOOKS,
    });
    if (result.noPython) return [];
    notes = result.notes;
  } catch {
    return [];
  }

  return assembleReorientation(notes, args.marker);
}

/**
 * Pure assembly: the notice first (only when there IS orientation to show, so an
 * empty set adds nothing), then one message per note, each keyed by hook + marker.
 * Split out from buildReorientation so it is testable without spawning Python.
 */
export function assembleReorientation(notes: SessionNote[], marker: string): ReorientationNote[] {
  if (!notes.length) return [];
  const out: ReorientationNote[] = [{ text: COMPACTION_NOTICE, key: `notice:${marker}` }];
  for (const note of notes) {
    out.push({ text: note.text, key: `${note.hook}:${marker}` });
  }
  return out;
}
