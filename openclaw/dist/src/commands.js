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
/**
 * Format a finished CLI run into reply text. A non-zero exit surfaces stderr
 * (where the CLI reports errors) so a broken invocation is visible, not silent.
 */
export function formatCliOutput(result, emptyFallback) {
    if (result.noPython)
        return { text: "", noPython: true };
    if (result.code !== 0) {
        const detail = (result.stderr.trim() || result.stdout.trim() || "(no output)").trim();
        return { text: `Clawness CLI failed (exit ${result.code ?? "killed"}):\n${detail}`, noPython: false };
    }
    const text = result.stdout.trim();
    return { text: text || emptyFallback, noPython: false };
}
/** `/clawness-status` — loaded rule counts + token cost (runs `clawness stats`). */
const statusCommand = {
    name: "clawness-status",
    description: "Show Clawness rule counts, retrieval config, and per-turn token cost.",
    acceptsArgs: false,
    async run() {
        const result = await runPythonCli(["stats"]);
        return formatCliOutput(result, "Clawness reported no stats.");
    },
};
/** Every OpenClaw-native Clawness command, registered in order by index.ts. */
export const CLAWNESS_COMMANDS = [statusCommand];
