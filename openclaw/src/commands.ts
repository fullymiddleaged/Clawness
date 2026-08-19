/**
 * commands.ts — host-agnostic definitions for the OpenClaw-native Clawness
 * commands. Like bridge/translate/notes, this file imports nothing from OpenClaw
 * so it stays unit-testable against the real Python CLI. index.ts adapts each
 * spec to `api.registerCommand` and owns the only OpenClaw contact.
 *
 * Naming: OpenClaw plugin commands share one GLOBAL namespace with no automatic
 * plugin prefix, and `status` (among others) is reserved by core — so every
 * command here is prefixed `clawness-` (see openclaw/COMMANDS-PLAN.md Phase 0).
 */
import { runPythonCli } from "./bridge.js";

/** What a command handler produces, before host-specific note substitution. */
export interface CommandOutput {
  /** Reply text to deliver to the user. Empty iff noPython is true. */
  text: string;
  /** True when no Python interpreter was found — index.ts substitutes the note. */
  noPython: boolean;
}

/** A host-agnostic Clawness command; index.ts maps this onto the OpenClaw API. */
export interface ClawnessCommand {
  /** Invocation name WITHOUT the leading slash, already `clawness-` prefixed. */
  name: string;
  /** One-line description shown in /help and command menus. */
  description: string;
  /** Whether the command consumes arguments (OpenClaw drops args otherwise). */
  acceptsArgs: boolean;
  /** Run the command. `args` is the raw string after the name (may be empty). */
  run(args: string): Promise<CommandOutput>;
}

/**
 * Format a finished CLI run into reply text. A non-zero exit surfaces stderr
 * (where the CLI reports errors) so a broken invocation is visible, not silent.
 */
export function formatCliOutput(
  result: { stdout: string; stderr: string; code: number | null; noPython: boolean },
  emptyFallback: string,
): CommandOutput {
  if (result.noPython) return { text: "", noPython: true };
  if (result.code !== 0) {
    const detail = (result.stderr.trim() || result.stdout.trim() || "(no output)").trim();
    return { text: `Clawness CLI failed (exit ${result.code ?? "killed"}):\n${detail}`, noPython: false };
  }
  const text = result.stdout.trim();
  return { text: text || emptyFallback, noPython: false };
}

/** `/clawness-status` — loaded rule counts + token cost (runs `clawness stats`). */
const statusCommand: ClawnessCommand = {
  name: "clawness-status",
  description: "Show Clawness rule counts, retrieval config, and per-turn token cost.",
  acceptsArgs: false,
  async run() {
    const result = await runPythonCli(["stats"]);
    return formatCliOutput(result, "Clawness reported no stats.");
  },
};

/** Every OpenClaw-native Clawness command, registered in order by index.ts. */
export const CLAWNESS_COMMANDS: readonly ClawnessCommand[] = [statusCommand];
