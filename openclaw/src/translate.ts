/**
 * translate.ts — shape translation between OpenClaw and the Claude-Code-shaped
 * payloads the Python hooks expect. Pure functions, no I/O, no OpenClaw imports.
 */

/** The subset of a Claude Code hook payload our Python hooks read. */
export interface ClawPayload {
  prompt?: string;
  cwd?: string;
  session_id?: string;
  hook_event_name?: string;
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  tool_response?: unknown;
}

/** Build the UserPromptSubmit-shaped payload for claude_hook.py. */
export function buildPromptPayload(args: {
  prompt: string;
  cwd: string;
  sessionId: string;
}): ClawPayload {
  return { prompt: args.prompt, cwd: args.cwd, session_id: args.sessionId };
}

/** Build a SessionStart-shaped payload for the note hooks. */
export function buildSessionPayload(args: {
  cwd: string;
  sessionId: string;
}): ClawPayload {
  return { cwd: args.cwd, session_id: args.sessionId, hook_event_name: "SessionStart" };
}

/**
 * A guarded tool call in the shape `clawness/guard.py::classify_tool_call`
 * expects, or null when the tool isn't one the guard reasons about.
 */
export interface MappedTool {
  tool_name: "Bash" | "Write" | "Edit" | "Read";
  tool_input: Record<string, unknown>;
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

function nameTokens(name: string): Set<string> {
  const words = name
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2") // split camelCase
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((s) => s.toLowerCase());
  return new Set(words);
}

function hasAny(tokens: Set<string>, words: Set<string>): boolean {
  for (const t of tokens) if (words.has(t)) return true;
  return false;
}

function firstString(params: Record<string, unknown>, keys: string[]): string | undefined {
  for (const k of keys) {
    const v = params[k];
    if (typeof v === "string" && v.length > 0) return v;
  }
  return undefined;
}

/**
 * Map an OpenClaw tool call to the Claude-shaped {tool_name, tool_input} the
 * guard classifier understands. Returns null for tools the guard ignores.
 */
export function mapToolCall(
  toolName: string,
  params: Record<string, unknown> | undefined,
): MappedTool | null {
  const p = params ?? {};
  const tokens = nameTokens(toolName ?? "");

  // Order: test the more specific write/edit intents before the broad read/exec
  // ones, so e.g. a hypothetical "get_and_write" leans write.
  if (hasAny(tokens, WRITE_WORDS)) {
    const file_path = firstString(p, ["file_path", "path", "file", "filename", "target"]);
    const content = firstString(p, ["content", "text", "data", "body", "new_content"]);
    if (file_path) return { tool_name: "Write", tool_input: { file_path, content: content ?? "" } };
  }
  if (hasAny(tokens, EDIT_WORDS)) {
    const file_path = firstString(p, ["file_path", "path", "file", "filename", "target"]);
    if (file_path) return { tool_name: "Edit", tool_input: { file_path, ...p } };
  }
  if (hasAny(tokens, BASH_WORDS)) {
    const command = firstString(p, ["command", "cmd", "script", "code", "input", "shell"]);
    if (command) return { tool_name: "Bash", tool_input: { command } };
  }
  if (hasAny(tokens, READ_WORDS)) {
    const file_path = firstString(p, ["file_path", "path", "file", "filename", "target"]);
    if (file_path) return { tool_name: "Read", tool_input: { file_path } };
  }
  return null;
}

/** Build the PreToolUse payload for access_guard.py. */
export function buildPreToolPayload(
  mapped: MappedTool,
  args: { cwd: string; sessionId: string },
): ClawPayload {
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
export function buildPostToolPayload(
  mapped: MappedTool,
  args: { cwd: string; sessionId: string; toolResponse: unknown },
): ClawPayload {
  return {
    hook_event_name: "PostToolUse",
    tool_name: mapped.tool_name,
    tool_input: mapped.tool_input,
    session_id: args.sessionId,
    cwd: args.cwd,
    tool_response: args.toolResponse ?? {},
  };
}

export interface GuardDecision {
  /** "allow" (do nothing), "ask" (requireApproval), or "deny" (block). */
  action: "allow" | "ask" | "deny";
  reason: string;
}

/**
 * Parse access_guard.py's stdout into a decision. The hook prints nothing and
 * exits 0 for ALLOW; for ask/deny it prints
 * {hookSpecificOutput:{permissionDecision, permissionDecisionReason}}.
 */
export function parseGuardStdout(stdout: string): GuardDecision {
  const text = stdout.trim();
  if (!text) return { action: "allow", reason: "" };
  try {
    const obj = JSON.parse(text);
    const hso = obj?.hookSpecificOutput ?? {};
    const decision = hso.permissionDecision;
    const reason = hso.permissionDecisionReason ?? "";
    if (decision === "deny") return { action: "deny", reason };
    if (decision === "ask") return { action: "ask", reason };
  } catch {
    // Non-JSON output (shouldn't happen) — fail open to allow.
  }
  return { action: "allow", reason: "" };
}
