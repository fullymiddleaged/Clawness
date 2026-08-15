/**
 * translate.ts — shape translation between OpenClaw and the Claude-Code-shaped
 * payloads the Python hooks expect. Pure functions, no I/O, no OpenClaw imports.
 */
/**
 * Read the user's prompt out of the `before_prompt_build` event. The host's field
 * name isn't pinned in the public docs (see README), and a miss fails SILENTLY —
 * an empty prompt makes the handler return {} and rules never inject, with no
 * error — so we take the first non-empty string among the plausible candidates,
 * `prompt` first so a host already on that field is unaffected. Pure and testable;
 * index.ts (which can't be imported without the host) just calls this. The live
 * pass confirms the real name; add it to the head of the list if it differs.
 */
export function resolvePromptText(event) {
    const e = (event ?? {});
    const candidates = [e.prompt, e.userPrompt, e.text, e.input, e.message];
    for (const c of candidates) {
        if (typeof c === "string" && c.trim())
            return c;
    }
    return "";
}
/** Build the UserPromptSubmit-shaped payload for claude_hook.py. */
export function buildPromptPayload(args) {
    return { prompt: args.prompt, cwd: args.cwd, session_id: args.sessionId };
}
/** Build a SessionStart-shaped payload for the note hooks. */
export function buildSessionPayload(args) {
    return { cwd: args.cwd, session_id: args.sessionId, hook_event_name: "SessionStart" };
}
/** Build a next-turn injection in the shape the OpenClaw SDK actually requires. */
export function buildNextTurnInjection(args) {
    const out = { sessionKey: args.sessionId, text: args.text };
    if (args.idempotencyKey)
        out.idempotencyKey = args.idempotencyKey;
    return out;
}
// OpenClaw's built-in tool names are not identical to Claude Code's, so we map
// by intent. Tool names arrive in many shapes (`bash`, `run_command`,
// `writeFile`, `read-file`), so we split the name into lowercase word tokens —
// on camelCase AND on non-alphanumerics (underscores are \w, so a `\b` regex
// silently fails on `run_command`) — and match tokens against keyword sets.
// Exact OpenClaw tool schemas must be confirmed at integration time (see
// README); these keyword sets are where any correction lands.
const WRITE_WORDS = new Set(["write", "writefile", "create", "new", "newfile"]);
const EDIT_WORDS = new Set(["edit", "replace", "patch", "modify", "apply"]);
const BASH_WORDS = new Set(["bash", "shell", "exec", "run", "command", "terminal", "sh", "process"]);
const READ_WORDS = new Set(["read", "readfile", "cat", "view", "open", "get"]);
function nameTokens(name) {
    const words = name
        .replace(/([a-z0-9])([A-Z])/g, "$1 $2") // split camelCase
        .split(/[^a-zA-Z0-9]+/)
        .filter(Boolean)
        .map((s) => s.toLowerCase());
    return new Set(words);
}
function hasAny(tokens, words) {
    for (const t of tokens)
        if (words.has(t))
            return true;
    return false;
}
function firstString(params, keys) {
    for (const k of keys) {
        const v = params[k];
        if (typeof v === "string" && v.length > 0)
            return v;
    }
    return undefined;
}
/**
 * Map an OpenClaw tool call to the Claude-shaped {tool_name, tool_input} the
 * guard classifier understands. Returns null for tools the guard ignores.
 */
export function mapToolCall(toolName, params) {
    const p = params ?? {};
    const tokens = nameTokens(toolName ?? "");
    // Order: test the more specific write/edit intents before the broad read/exec
    // ones, so e.g. a hypothetical "get_and_write" leans write.
    if (hasAny(tokens, WRITE_WORDS)) {
        const file_path = firstString(p, ["file_path", "path", "file", "filename", "target"]);
        const content = firstString(p, ["content", "text", "data", "body", "new_content"]);
        if (file_path)
            return { tool_name: "Write", tool_input: { file_path, content: content ?? "" } };
    }
    if (hasAny(tokens, EDIT_WORDS)) {
        const file_path = firstString(p, ["file_path", "path", "file", "filename", "target"]);
        if (file_path)
            return { tool_name: "Edit", tool_input: { file_path, ...p } };
    }
    if (hasAny(tokens, BASH_WORDS)) {
        const command = firstString(p, ["command", "cmd", "script", "code", "input", "shell"]);
        if (command)
            return { tool_name: "Bash", tool_input: { command } };
    }
    if (hasAny(tokens, READ_WORDS)) {
        const file_path = firstString(p, ["file_path", "path", "file", "filename", "target"]);
        if (file_path)
            return { tool_name: "Read", tool_input: { file_path } };
    }
    return null;
}
/** Build the PreToolUse payload for access_guard.py. */
export function buildPreToolPayload(mapped, args) {
    return {
        hook_event_name: "PreToolUse",
        tool_name: mapped.tool_name,
        tool_input: mapped.tool_input,
        session_id: args.sessionId,
        cwd: args.cwd,
    };
}
/** Build the PostToolUse payload. `tool_response` MUST be present so the guard's
 * ask-ledger settles (see access_guard.py's tool_response check). */
export function buildPostToolPayload(mapped, args) {
    return {
        hook_event_name: "PostToolUse",
        tool_name: mapped.tool_name,
        tool_input: mapped.tool_input,
        session_id: args.sessionId,
        cwd: args.cwd,
        tool_response: args.toolResponse ?? {},
    };
}
/**
 * Parse access_guard.py's stdout into a decision. The hook prints nothing and
 * exits 0 for ALLOW; for ask/deny it prints
 * {hookSpecificOutput:{permissionDecision, permissionDecisionReason}}.
 */
export function parseGuardStdout(stdout) {
    const text = stdout.trim();
    if (!text)
        return { action: "allow", reason: "" };
    try {
        const obj = JSON.parse(text);
        const hso = obj?.hookSpecificOutput ?? {};
        const decision = hso.permissionDecision;
        const reason = hso.permissionDecisionReason ?? "";
        if (decision === "deny")
            return { action: "deny", reason };
        if (decision === "ask")
            return { action: "ask", reason };
    }
    catch {
        // Non-JSON output (shouldn't happen) — fail open to allow.
    }
    return { action: "allow", reason: "" };
}
