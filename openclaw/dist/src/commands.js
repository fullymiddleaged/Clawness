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
/**
 * `/clawness-query <prompt>` — show which rules retrieve for a prompt (runs
 * `clawness query`). The prompt is one natural-language positional that may
 * contain spaces, so the raw arg string is forwarded as a SINGLE argv element,
 * never split. Empty args get a usage line instead of a broken CLI invocation.
 */
const queryCommand = {
    name: "clawness-query",
    description: "Show which Clawness rules retrieve for a given prompt.",
    acceptsArgs: true,
    async run(args) {
        const prompt = args.trim();
        if (!prompt) {
            return { text: "Usage: /clawness-query <prompt> — e.g. /clawness-query add JWT auth to the API", noPython: false };
        }
        const result = await runPythonCli(["query", prompt]);
        return formatCliOutput(result, "No rules retrieved for that prompt.");
    },
};
/**
 * `/clawness-audit-rules [flags]` — maintainer corpus-health report (runs
 * `clawness audit-rules`). The CLI subcommand takes only optional FLAGS
 * (--stale/--overlap/--strict/…), no positional, so the raw arg string is split
 * on whitespace into argv tokens; bare invocation runs the full default audit.
 */
const auditRulesCommand = {
    name: "clawness-audit-rules",
    description: "Report Clawness corpus health: version provenance, eval coverage, near-duplicates, unreachable rules.",
    acceptsArgs: true,
    async run(args) {
        const flags = args.trim() ? args.trim().split(/\s+/) : [];
        const result = await runPythonCli(["audit-rules", ...flags]);
        return formatCliOutput(result, "Clawness audit-rules produced no output.");
    },
};
/** Every OpenClaw-native Clawness command, registered in order by index.ts. */
export const CLAWNESS_COMMANDS = [
    statusCommand,
    queryCommand,
    auditRulesCommand,
];
