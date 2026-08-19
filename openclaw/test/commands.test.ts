/**
 * Tests for the OpenClaw-native Clawness commands.
 *
 * Two layers, mirroring translate.test.ts (pure) + bridge.integration.test.ts
 * (real subprocess): formatCliOutput / command-spec invariants are unit-tested,
 * and the status command is driven end-to-end through the real `clawness` CLI,
 * skipping cleanly when no Python interpreter is on PATH.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { CLAWNESS_COMMANDS, formatCliOutput } from "../src/commands.js";
import { runPythonCli } from "../src/bridge.js";

// OpenClaw's reserved command names (core-owned); a plugin registering any of
// these is rejected. Pinned here so a future command can't silently collide.
const RESERVED = new Set([
  "help", "commands", "status", "diagnostics", "codex", "whoami", "context",
  "btw", "stop", "restart", "reset", "new", "compact", "config", "debug",
  "allowlist", "activation", "skill", "learn", "subagents", "kill", "steer",
  "tell", "model", "models", "queue", "send", "bash", "exec", "think",
  "verbose", "reasoning", "elevated", "usage",
]);

// --- formatCliOutput (pure) ---------------------------------------------------

test("formatCliOutput: noPython yields empty text + noPython flag", () => {
  const out = formatCliOutput({ stdout: "", stderr: "", code: null, noPython: true }, "fb");
  assert.deepEqual(out, { text: "", noPython: true });
});

test("formatCliOutput: exit 0 returns trimmed stdout", () => {
  const out = formatCliOutput({ stdout: "  hello\n", stderr: "", code: 0, noPython: false }, "fb");
  assert.deepEqual(out, { text: "hello", noPython: false });
});

test("formatCliOutput: exit 0 with empty stdout uses the fallback", () => {
  const out = formatCliOutput({ stdout: "   \n", stderr: "", code: 0, noPython: false }, "nothing here");
  assert.deepEqual(out, { text: "nothing here", noPython: false });
});

test("formatCliOutput: non-zero exit surfaces stderr, not silence", () => {
  const out = formatCliOutput(
    { stdout: "", stderr: "boom: bad arg\n", code: 2, noPython: false },
    "fb",
  );
  assert.equal(out.noPython, false);
  assert.match(out.text, /exit 2/);
  assert.match(out.text, /boom: bad arg/);
});

test("formatCliOutput: non-zero exit falls back to stdout when stderr empty", () => {
  const out = formatCliOutput(
    { stdout: "partial output", stderr: "", code: 1, noPython: false },
    "fb",
  );
  assert.match(out.text, /partial output/);
});

// --- command-spec invariants --------------------------------------------------

test("every command name is `clawness-` prefixed and non-reserved", () => {
  assert.ok(CLAWNESS_COMMANDS.length > 0, "expected at least one command");
  for (const cmd of CLAWNESS_COMMANDS) {
    assert.match(cmd.name, /^clawness-/, `${cmd.name} must be clawness- prefixed`);
    // Valid per OpenClaw's validateCommandName: ^[a-z][a-z0-9_-]*$
    assert.match(cmd.name, /^[a-z][a-z0-9_-]*$/, `${cmd.name} has an illegal char`);
    assert.ok(!RESERVED.has(cmd.name), `${cmd.name} collides with a reserved name`);
    assert.ok(cmd.description.trim().length > 0, `${cmd.name} needs a description`);
  }
});

test("command names are unique", () => {
  const names = CLAWNESS_COMMANDS.map((c) => c.name);
  assert.equal(new Set(names).size, names.length, "duplicate command name");
});

test("clawness-status is registered and takes no args", () => {
  const status = CLAWNESS_COMMANDS.find((c) => c.name === "clawness-status");
  assert.ok(status, "clawness-status command missing");
  assert.equal(status!.acceptsArgs, false);
});

test("clawness-query and clawness-audit-rules are registered and accept args", () => {
  for (const name of ["clawness-query", "clawness-audit-rules"]) {
    const cmd = CLAWNESS_COMMANDS.find((c) => c.name === name);
    assert.ok(cmd, `${name} command missing`);
    // OpenClaw's matcher drops args for a command whose acceptsArgs is false.
    assert.equal(cmd!.acceptsArgs, true, `${name} must accept args`);
  }
});

// --- arg handling (pure — no Python needed) ----------------------------------

test("clawness-query with empty args returns a usage line, not a CLI call", async () => {
  const query = CLAWNESS_COMMANDS.find((c) => c.name === "clawness-query")!;
  for (const empty of ["", "   ", "\t\n"]) {
    const out = await query.run(empty);
    assert.equal(out.noPython, false);
    assert.match(out.text, /^Usage: \/clawness-query/);
  }
});

// --- integration: real CLI through the bridge --------------------------------

async function hasPython(): Promise<boolean> {
  const r = await runPythonCli(["stats"]);
  return !r.noPython;
}

test("runPythonCli(['stats']) returns stats from the real CLI", async (t) => {
  if (!(await hasPython())) return t.skip("no Python interpreter on PATH");
  const r = await runPythonCli(["stats"]);
  assert.equal(r.noPython, false);
  assert.equal(r.code, 0);
  assert.match(r.stdout, /Ranked rules/);
  assert.match(r.stdout, /Total/);
});

test("clawness-status command run() surfaces the stats block", async (t) => {
  if (!(await hasPython())) return t.skip("no Python interpreter on PATH");
  const status = CLAWNESS_COMMANDS.find((c) => c.name === "clawness-status")!;
  const out = await status.run("");
  assert.equal(out.noPython, false);
  assert.match(out.text, /Ranked rules/);
  assert.match(out.text, /Retrieval/);
});

test("clawness-query command run() surfaces retrieved rules for a prompt", async (t) => {
  if (!(await hasPython())) return t.skip("no Python interpreter on PATH");
  const query = CLAWNESS_COMMANDS.find((c) => c.name === "clawness-query")!;
  const out = await query.run("add JWT auth to an order endpoint");
  assert.equal(out.noPython, false);
  assert.match(out.text, /CLAWNESS RULES/);
  assert.match(out.text, /MANDATORY/);
});

test("clawness-audit-rules command run() surfaces the corpus report", async (t) => {
  if (!(await hasPython())) return t.skip("no Python interpreter on PATH");
  const audit = CLAWNESS_COMMANDS.find((c) => c.name === "clawness-audit-rules")!;
  const out = await audit.run("");
  assert.equal(out.noPython, false);
  assert.match(out.text, /finding\(s\) across/);
});

test("clawness-audit-rules forwards flag args (--stale) to the CLI", async (t) => {
  if (!(await hasPython())) return t.skip("no Python interpreter on PATH");
  const audit = CLAWNESS_COMMANDS.find((c) => c.name === "clawness-audit-rules")!;
  const out = await audit.run("--stale");
  assert.equal(out.noPython, false);
  // --stale runs only the stale check, so its section header must appear and
  // the coverage/overlap/reachability headers must not.
  assert.match(out.text, /\[stale\]/);
  assert.doesNotMatch(out.text, /\[coverage\]/);
});
