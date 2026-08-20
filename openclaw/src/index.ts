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
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { runPythonHook, PLUGIN_DIR } from "./bridge.js";
import {
  buildPromptPayload,
  buildPreToolPayload,
  buildPostToolPayload,
  buildNextTurnInjection,
  mapToolCall,
  parseGuardStdout,
  resolvePromptText,
  type MappedTool,
} from "./translate.js";
import { runSessionNotes } from "./notes.js";
import { CLAWNESS_COMMANDS } from "./commands.js";
import { runInstallScan, toInstallResult } from "./install.js";
import { buildReorientation } from "./compaction.js";
import { makeMemoryCorpusSupplement } from "./memory.js";

const NO_PYTHON_NOTE =
  "[Clawness] Python 3.10+ was not found on PATH, so Clawness's rules, memory, " +
  "and access guard are inactive. Point the user at the Installing Python section " +
  "of https://github.com/fullymiddleaged/clawness — do not install Python for them.";

/** Pull the working directory from whatever field the host provides. */
function resolveCwd(event: any, ctx: any): string {
  return (
    event?.cwd ?? ctx?.cwd ?? ctx?.workspace?.root ?? ctx?.projectRoot ?? process.cwd()
  );
}

/** A stable per-session id so session_state and the .clawness/ ledgers key right. */
function resolveSessionId(event: any, ctx: any): string {
  return String(ctx?.sessionKey ?? event?.sessionKey ?? ctx?.sessionId ?? ctx?.agentId ?? "");
}

export default definePluginEntry({
  id: "clawness",
  name: "Clawness",
  description: "Retrieval-ranked coding rules, project memory, and an access guard.",
  register(api: OpenClawPluginApi) {
    const log = api.logger;
    let warnedNoPython = false;

    // The memory corpus supplement's search/get carry no cwd, so we track the most
    // recent session/prompt cwd and hand it to the Python ranker. Falls back to the
    // process cwd before any event has arrived. (Known limit under multi-workspace;
    // see src/memory.ts + EXTENSIONS-PLAN.md.)
    let lastCwd = process.cwd();

    const noteNoPython = (): string | null => {
      if (warnedNoPython) return null;
      warnedNoPython = true;
      return NO_PYTHON_NOTE;
    };

    // --- Rules + memory injection, every prompt ---------------------------
    api.on("before_prompt_build", async (event: any, ctx: any) => {
      try {
        const prompt = resolvePromptText(event);
        if (!prompt) return {};
        const cwd = resolveCwd(event, ctx);
        lastCwd = cwd;
        const result = await runPythonHook(
          "hooks/claude_hook.py",
          buildPromptPayload({
            prompt,
            cwd,
            sessionId: resolveSessionId(event, ctx),
          }),
        );
        if (result.noPython) {
          const note = noteNoPython();
          return note ? { appendContext: note } : {};
        }
        const text = result.stdout.trim();
        return text ? { appendContext: text } : {};
      } catch (err) {
        log?.warn?.(`clawness before_prompt_build failed: ${String(err)}`);
        return {};
      }
    });

    // --- SessionStart notes ----------------------------------------------
    api.on("session_start", async (event: any, ctx: any) => {
      try {
        const sessionId = resolveSessionId(event, ctx);
        const cwd = resolveCwd(event, ctx);
        lastCwd = cwd;
        const { notes, noPython } = await runSessionNotes({
          cwd,
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
          if (notes.length) log?.debug?.("clawness: no enqueueNextTurnInjection; notes dropped");
          return;
        }
        for (const note of notes) {
          enqueue(buildNextTurnInjection({ sessionId, text: note.text, idempotencyKey: `clawness:${note.hook}` }));
        }
      } catch (err) {
        log?.warn?.(`clawness session_start failed: ${String(err)}`);
      }
    });

    // --- Access guard: block/ask before a tool runs ----------------------
    api.on("before_tool_call", async (event: any, ctx: any) => {
      try {
        const mapped = mapToolCall(event?.toolName ?? "", event?.params);
        if (!mapped) return {};
        const result = await runPythonHook(
          "hooks/access_guard.py",
          buildPreToolPayload(mapped, {
            cwd: resolveCwd(event, ctx),
            sessionId: resolveSessionId(event, ctx),
          }),
        );
        if (result.noPython) return {}; // guard inactive without Python — fail open
        const decision = parseGuardStdout(result.stdout);
        if (decision.action === "deny") {
          return { block: true, blockReason: decision.reason };
        }
        if (decision.action === "ask") {
          return {
            requireApproval: {
              title: "Clawness: approval required",
              description: decision.reason,
              severity: "warning" as const,
            },
          };
        }
        return {};
      } catch (err) {
        log?.warn?.(`clawness before_tool_call failed: ${String(err)}`);
        return {}; // fail open
      }
    });

    // --- Settle the guard's ask-ledger once a call has actually run ------
    api.on("after_tool_call", async (event: any, ctx: any) => {
      try {
        const mapped: MappedTool | null = mapToolCall(event?.toolName ?? "", event?.params);
        if (!mapped) return;
        await runPythonHook(
          "hooks/access_guard.py",
          buildPostToolPayload(mapped, {
            cwd: resolveCwd(event, ctx),
            sessionId: resolveSessionId(event, ctx),
            toolResponse: event?.result ?? event?.output ?? {},
          }),
        );
      } catch (err) {
        log?.warn?.(`clawness after_tool_call failed: ${String(err)}`);
      }
    });

    // --- After compaction: re-inject the orientation the host squashed -----
    // The native home for Clawness's context-watch + handoff. Rules and ranked
    // memory self-heal on the next before_prompt_build, so we re-state only the
    // SessionStart-only orientation (handoff + stack) plus a notice. Fail silent.
    api.on("after_compaction", async (event: any, ctx: any) => {
      try {
        const enqueue = api.session?.workflow?.enqueueNextTurnInjection;
        if (!enqueue) return;
        const sessionId = resolveSessionId(event, ctx);
        const cwd = resolveCwd(event, ctx);
        lastCwd = cwd;
        // A stable marker per compaction so retries de-dupe but the next compaction
        // re-fires: previousSessionId is unique per rotation; fall back to a count/time.
        const marker = String(
          event?.previousSessionId ?? event?.compactedCount ?? event?.messageCount ?? Date.now(),
        );
        const messages = await buildReorientation({ cwd, sessionId, marker });
        for (const m of messages) {
          enqueue(buildNextTurnInjection({ sessionId, text: m.text, idempotencyKey: `clawness:compaction:${m.key}` }));
        }
      } catch (err) {
        log?.warn?.(`clawness after_compaction failed: ${String(err)}`);
      }
    });

    // --- Before install: vet the artifact for injection/exfil tells --------
    // OpenClaw hands us the artifact's sourcePath and accepts {findings, block}.
    // We scan via clawness.trust; findings are ADVISORY and always surface. Blocking
    // is OPT-IN (CLAW_INSTALL_BLOCK=1): the tell scan false-positives on any artifact
    // that documents these patterns (a security skill, or this repo itself), so
    // blocking by default would block real work — the guard philosophy forbids that.
    // Fail open — never block on error.
    if (process.env.CLAW_NO_INSTALL_SCAN !== "1") {
      api.on("before_install", async (event: any, _ctx: any) => {
        try {
          const sourcePath = String(event?.sourcePath ?? event?.source_path ?? "");
          if (!sourcePath) return {};
          const scan = await runInstallScan(sourcePath);
          return toInstallResult(scan, { allowBlock: process.env.CLAW_INSTALL_BLOCK === "1" });
        } catch (err) {
          log?.warn?.(`clawness before_install failed: ${String(err)}`);
          return {}; // fail open
        }
      });
    }

    // --- Memory corpus: .clawness/memory.md as native searchable memory ----
    // Additive/non-exclusive: the ranked block still injects every turn; this makes
    // the same lessons discoverable through OpenClaw's memory search. Guarded — a
    // host without the API simply gets no supplement.
    if (process.env.CLAW_NO_MEMORY_CORPUS !== "1" && typeof api.registerMemoryCorpusSupplement === "function") {
      try {
        api.registerMemoryCorpusSupplement(makeMemoryCorpusSupplement(() => lastCwd));
      } catch (err) {
        log?.warn?.(`clawness registerMemoryCorpusSupplement failed: ${String(err)}`);
      }
    }

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
            handler: async (cctx: any) => {
              try {
                const out = await cmd.run(String(cctx?.args ?? ""));
                // A user who typed the command is owed the reason on no-Python,
                // so surface the note unconditionally (not the deduped session one).
                return { text: out.noPython ? NO_PYTHON_NOTE : out.text };
              } catch (err) {
                log?.warn?.(`clawness command /${cmd.name} failed: ${String(err)}`);
                return { text: "Clawness command failed; see host logs." };
              }
            },
          });
        } catch (err) {
          log?.warn?.(`clawness registerCommand(/${cmd.name}) failed: ${String(err)}`);
        }
      }
    }

    log?.debug?.(`clawness OpenClaw adapter registered (plugin dir ${PLUGIN_DIR})`);
  },
});
