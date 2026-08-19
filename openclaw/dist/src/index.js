/**
 * index.ts — OpenClaw plugin entry. The ONLY file that touches the OpenClaw API.
 *
 * Deliberately thin and defensive: every handler wraps its work in try/catch and
 * fails toward doing nothing, mirroring Clawness's fail-open/fail-silent design.
 * All real logic is in bridge/translate/notes, which are host-agnostic and tested
 * against the real Python. The exact ctx/event field names OpenClaw passes are
 * read with fallbacks because they aren't fully pinned in the public docs (see
 * README) — correcting a field name here never touches the tested core.
 */
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { runPythonHook, PLUGIN_DIR } from "./bridge.js";
import { buildPromptPayload, buildPreToolPayload, buildPostToolPayload, buildNextTurnInjection, mapToolCall, parseGuardStdout, resolvePromptText, } from "./translate.js";
import { runSessionNotes } from "./notes.js";
import { CLAWNESS_COMMANDS } from "./commands.js";
const NO_PYTHON_NOTE = "[Clawness] Python 3.10+ was not found on PATH, so Clawness's rules, memory, " +
    "and access guard are inactive. Point the user at the Installing Python section " +
    "of https://github.com/fullymiddleaged/clawness — do not install Python for them.";
/** Pull the working directory from whatever field the host provides. */
function resolveCwd(event, ctx) {
    return (event?.cwd ?? ctx?.cwd ?? ctx?.workspace?.root ?? ctx?.projectRoot ?? process.cwd());
}
/** A stable per-session id so session_state and the .clawness/ ledgers key right. */
function resolveSessionId(event, ctx) {
    return String(ctx?.sessionKey ?? event?.sessionKey ?? ctx?.sessionId ?? ctx?.agentId ?? "");
}
export default definePluginEntry({
    id: "clawness",
    name: "Clawness",
    description: "Retrieval-ranked coding rules, project memory, and an access guard.",
    register(api) {
        const log = api.logger;
        let warnedNoPython = false;
        const noteNoPython = () => {
            if (warnedNoPython)
                return null;
            warnedNoPython = true;
            return NO_PYTHON_NOTE;
        };
        // --- Rules + memory injection, every prompt ---------------------------
        api.on("before_prompt_build", async (event, ctx) => {
            try {
                const prompt = resolvePromptText(event);
                if (!prompt)
                    return {};
                const result = await runPythonHook("hooks/claude_hook.py", buildPromptPayload({
                    prompt,
                    cwd: resolveCwd(event, ctx),
                    sessionId: resolveSessionId(event, ctx),
                }));
                if (result.noPython) {
                    const note = noteNoPython();
                    return note ? { appendContext: note } : {};
                }
                const text = result.stdout.trim();
                return text ? { appendContext: text } : {};
            }
            catch (err) {
                log?.warn?.(`clawness before_prompt_build failed: ${String(err)}`);
                return {};
            }
        });
        // --- SessionStart notes ----------------------------------------------
        api.on("session_start", async (event, ctx) => {
            try {
                const sessionId = resolveSessionId(event, ctx);
                const { notes, noPython } = await runSessionNotes({
                    cwd: resolveCwd(event, ctx),
                    sessionId,
                });
                const enqueue = api.session?.workflow?.enqueueNextTurnInjection;
                if (noPython) {
                    const note = noteNoPython();
                    if (note && enqueue) {
                        enqueue(buildNextTurnInjection({ sessionId, text: note, idempotencyKey: "clawness:no-python" }));
                    }
                    return;
                }
                if (!enqueue) {
                    if (notes.length)
                        log?.debug?.("clawness: no enqueueNextTurnInjection; notes dropped");
                    return;
                }
                for (const note of notes) {
                    enqueue(buildNextTurnInjection({ sessionId, text: note.text, idempotencyKey: `clawness:${note.hook}` }));
                }
            }
            catch (err) {
                log?.warn?.(`clawness session_start failed: ${String(err)}`);
            }
        });
        // --- Access guard: block/ask before a tool runs ----------------------
        api.on("before_tool_call", async (event, ctx) => {
            try {
                const mapped = mapToolCall(event?.toolName ?? "", event?.params);
                if (!mapped)
                    return {};
                const result = await runPythonHook("hooks/access_guard.py", buildPreToolPayload(mapped, {
                    cwd: resolveCwd(event, ctx),
                    sessionId: resolveSessionId(event, ctx),
                }));
                if (result.noPython)
                    return {}; // guard inactive without Python — fail open
                const decision = parseGuardStdout(result.stdout);
                if (decision.action === "deny") {
                    return { block: true, blockReason: decision.reason };
                }
                if (decision.action === "ask") {
                    return {
                        requireApproval: {
                            title: "Clawness: approval required",
                            description: decision.reason,
                            severity: "warning",
                        },
                    };
                }
                return {};
            }
            catch (err) {
                log?.warn?.(`clawness before_tool_call failed: ${String(err)}`);
                return {}; // fail open
            }
        });
        // --- Settle the guard's ask-ledger once a call has actually run ------
        api.on("after_tool_call", async (event, ctx) => {
            try {
                const mapped = mapToolCall(event?.toolName ?? "", event?.params);
                if (!mapped)
                    return;
                await runPythonHook("hooks/access_guard.py", buildPostToolPayload(mapped, {
                    cwd: resolveCwd(event, ctx),
                    sessionId: resolveSessionId(event, ctx),
                    toolResponse: event?.result ?? event?.output ?? {},
                }));
            }
            catch (err) {
                log?.warn?.(`clawness after_tool_call failed: ${String(err)}`);
            }
        });
        // --- Native commands (read-only CLI surface) -------------------------
        // Commands share one global namespace and `status` is reserved, so ours are
        // `clawness-` prefixed. A host too old to expose registerCommand simply gets
        // no commands — fail toward nothing, like every other handler here.
        if (typeof api.registerCommand === "function") {
            for (const cmd of CLAWNESS_COMMANDS) {
                try {
                    api.registerCommand({
                        name: cmd.name,
                        description: cmd.description,
                        acceptsArgs: cmd.acceptsArgs,
                        handler: async (cctx) => {
                            try {
                                const out = await cmd.run(String(cctx?.args ?? ""));
                                // A user who typed the command is owed the reason on no-Python,
                                // so surface the note unconditionally (not the deduped session one).
                                return { text: out.noPython ? NO_PYTHON_NOTE : out.text };
                            }
                            catch (err) {
                                log?.warn?.(`clawness command /${cmd.name} failed: ${String(err)}`);
                                return { text: "Clawness command failed; see host logs." };
                            }
                        },
                    });
                }
                catch (err) {
                    log?.warn?.(`clawness registerCommand(/${cmd.name}) failed: ${String(err)}`);
                }
            }
        }
        log?.debug?.(`clawness OpenClaw adapter registered (plugin dir ${PLUGIN_DIR})`);
    },
});
