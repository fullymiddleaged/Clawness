/**
 * bridge.ts — spawn a bundled Clawness Python hook and capture its stdout.
 *
 * The whole adapter strategy: reuse the exact Python hook scripts Claude Code
 * runs, unchanged, by feeding them the same JSON-on-stdin they already expect
 * and reading their stdout. This file is the only place that spawns Python; it
 * imports nothing from OpenClaw, so it is fully testable against the real hooks.
 */
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve, join } from "node:path";
// dist/src/bridge.js  → dist/src → dist → openclaw → <repo root>
// src/bridge.ts (tsx) → src → openclaw → <repo root>
// Either way the repo root is two levels above this file's parent directory's
// parent. We compute the openclaw package dir first, then its parent (repo root).
const HERE = dirname(fileURLToPath(import.meta.url));
// HERE is dist/src or src; the openclaw package dir is one level up, repo root two.
export const PLUGIN_DIR = resolve(HERE, "..", ".."); // .../openclaw
export const REPO_ROOT = resolve(PLUGIN_DIR, ".."); // repo root holding hooks/ and rules/
// Mirrors the interpreter picker baked into every hook command in plugin.json
// (`for p in python3 python py`). Windows has no python3; python.org ships py.
const INTERPRETERS = ["python3", "python", "py"];
/**
 * Run a bundled hook script with `payload` as JSON on stdin.
 *
 * @param scriptRelPath repo-relative path, e.g. "hooks/claude_hook.py"
 * @param payload the hook event object (Claude-Code-shaped)
 * @param timeoutMs kill budget; the hooks are ~400ms but we allow slack
 */
export async function runPythonHook(scriptRelPath, payload, timeoutMs = 12000) {
    const script = join(REPO_ROOT, scriptRelPath);
    const input = JSON.stringify(payload);
    for (const interpreter of INTERPRETERS) {
        const result = await trySpawn(interpreter, script, input, timeoutMs);
        if (result === "ENOENT")
            continue; // interpreter not on PATH — try the next
        return { stdout: result.stdout, code: result.code, noPython: false };
    }
    // No interpreter resolved. Fail silent like the Python hooks do — the caller
    // decides whether to surface a one-line "Python not found" note.
    return { stdout: "", code: null, noPython: true };
}
function trySpawn(interpreter, script, input, timeoutMs) {
    return new Promise((resolvePromise) => {
        let settled = false;
        const done = (outcome) => {
            if (settled)
                return;
            settled = true;
            resolvePromise(outcome);
        };
        let child;
        try {
            child = spawn(interpreter, [script], {
                cwd: REPO_ROOT,
                stdio: ["pipe", "pipe", "pipe"],
                // A hook resolving `clawness` relies on sys.path; the scripts insert
                // their own parent, so no PYTHONPATH is required. We still set cwd to
                // the repo root for predictable relative resolution.
            });
        }
        catch {
            done("ENOENT");
            return;
        }
        let stdout = "";
        const timer = setTimeout(() => {
            try {
                child.kill();
            }
            catch {
                /* ignore */
            }
            done({ stdout, code: null });
        }, timeoutMs);
        child.on("error", (err) => {
            clearTimeout(timer);
            // ENOENT means this interpreter name isn't on PATH → let the caller try
            // the next one. Any other spawn error, fail silent as empty output.
            done(err.code === "ENOENT" ? "ENOENT" : { stdout, code: null });
        });
        child.stdout?.on("data", (chunk) => {
            stdout += chunk.toString("utf8");
        });
        child.on("close", (code) => {
            clearTimeout(timer);
            done({ stdout, code });
        });
        try {
            child.stdin?.write(input, "utf8");
            child.stdin?.end();
        }
        catch {
            /* the error/close handlers will settle */
        }
    });
}
