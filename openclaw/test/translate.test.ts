import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mapToolCall,
  parseGuardStdout,
  buildPromptPayload,
  buildPreToolPayload,
  buildPostToolPayload,
} from "../src/translate.js";

test("buildPromptPayload carries prompt/cwd/session_id in Claude shape", () => {
  const p = buildPromptPayload({ prompt: "hi", cwd: "/proj", sessionId: "s1" });
  assert.equal(p.prompt, "hi");
  assert.equal(p.cwd, "/proj");
  assert.equal(p.session_id, "s1");
});

test("mapToolCall: shell-family names -> Bash with command", () => {
  for (const name of ["bash", "shell", "exec", "run_command", "terminal"]) {
    const m = mapToolCall(name, { command: "ls -la" });
    assert.equal(m?.tool_name, "Bash", `name=${name}`);
    assert.equal(m?.tool_input.command, "ls -la");
  }
});

test("mapToolCall: Bash pulls command from alternate field names", () => {
  const m = mapToolCall("shell", { cmd: "echo hi" });
  assert.equal(m?.tool_name, "Bash");
  assert.equal(m?.tool_input.command, "echo hi");
});

test("mapToolCall: write names -> Write with file_path and content", () => {
  const m = mapToolCall("write_file", { path: "/a/b.txt", text: "data" });
  assert.equal(m?.tool_name, "Write");
  assert.equal(m?.tool_input.file_path, "/a/b.txt");
  assert.equal(m?.tool_input.content, "data");
});

test("mapToolCall: edit names -> Edit (checked before read to avoid 'file' collision)", () => {
  const m = mapToolCall("edit_file", { file_path: "/a/b.txt" });
  assert.equal(m?.tool_name, "Edit");
  assert.equal(m?.tool_input.file_path, "/a/b.txt");
});

test("mapToolCall: read names -> Read with file_path", () => {
  const m = mapToolCall("read_file", { path: "/a/b.txt" });
  assert.equal(m?.tool_name, "Read");
  assert.equal(m?.tool_input.file_path, "/a/b.txt");
});

test("mapToolCall: unknown / unguarded tool -> null", () => {
  assert.equal(mapToolCall("web_search", { query: "x" }), null);
  assert.equal(mapToolCall("my_tool", { input: "x" }), null); // matches nothing guarded
});

test("mapToolCall: guarded name but missing payload field -> null", () => {
  assert.equal(mapToolCall("bash", {}), null); // no command
  assert.equal(mapToolCall("write_file", {}), null); // no file path
});

test("parseGuardStdout: empty stdout -> allow", () => {
  assert.deepEqual(parseGuardStdout(""), { action: "allow", reason: "" });
  assert.deepEqual(parseGuardStdout("   \n"), { action: "allow", reason: "" });
});

test("parseGuardStdout: deny decision -> deny with reason", () => {
  const out = JSON.stringify({
    hookSpecificOutput: { permissionDecision: "deny", permissionDecisionReason: "no" },
  });
  assert.deepEqual(parseGuardStdout(out), { action: "deny", reason: "no" });
});

test("parseGuardStdout: ask decision -> ask with reason", () => {
  const out = JSON.stringify({
    hookSpecificOutput: { permissionDecision: "ask", permissionDecisionReason: "confirm" },
  });
  assert.deepEqual(parseGuardStdout(out), { action: "ask", reason: "confirm" });
});

test("parseGuardStdout: non-JSON garbage -> fails open to allow", () => {
  assert.deepEqual(parseGuardStdout("not json"), { action: "allow", reason: "" });
});

test("buildPreToolPayload / buildPostToolPayload set the right event names", () => {
  const mapped = { tool_name: "Bash" as const, tool_input: { command: "ls" } };
  const pre = buildPreToolPayload(mapped, { cwd: "/p", sessionId: "s" });
  assert.equal(pre.hook_event_name, "PreToolUse");
  assert.equal(pre.tool_name, "Bash");

  const post = buildPostToolPayload(mapped, { cwd: "/p", sessionId: "s", toolResponse: { ok: 1 } });
  assert.equal(post.hook_event_name, "PostToolUse");
  // tool_response MUST be present so access_guard.py settles its ask-ledger.
  assert.ok("tool_response" in post);
  assert.deepEqual(post.tool_response, { ok: 1 });
});

test("buildPostToolPayload defaults tool_response to {} when none given", () => {
  const mapped = { tool_name: "Bash" as const, tool_input: { command: "ls" } };
  const post = buildPostToolPayload(mapped, { cwd: "/p", sessionId: "s", toolResponse: undefined });
  assert.ok("tool_response" in post);
  assert.deepEqual(post.tool_response, {});
});
