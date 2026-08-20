import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { assembleReorientation, COMPACTION_NOTICE, buildReorientation } from "../src/compaction.js";

test("assembleReorientation: no notes → [] (adds no noise, not even the notice)", () => {
  assert.deepEqual(assembleReorientation([], "m1"), []);
});

test("assembleReorientation: leads with the notice, then one msg per note", () => {
  const notes = [
    { hook: "hooks/handoff_check.py", text: "handoff body" },
    { hook: "hooks/stack_detect.py", text: "stack: Python" },
  ];
  const out = assembleReorientation(notes, "m1");
  assert.equal(out.length, 3);
  assert.equal(out[0].text, COMPACTION_NOTICE);
  assert.equal(out[1].text, "handoff body");
  assert.equal(out[2].text, "stack: Python");
});

test("assembleReorientation: keys embed the marker so a later compaction re-fires", () => {
  const notes = [{ hook: "hooks/stack_detect.py", text: "stack" }];
  const a = assembleReorientation(notes, "compactionA");
  const b = assembleReorientation(notes, "compactionB");
  assert.notEqual(a[0].key, b[0].key);
  assert.match(a[1].key, /stack_detect\.py:compactionA/);
});

// Integration: runs the real orientation hooks against this repo. stack_detect
// always emits, so we expect the notice + at least the stack note. Skips cleanly
// if Python is unavailable (buildReorientation returns [] on noPython).
test("buildReorientation: integration — re-orients from real hooks", async () => {
  // dist/test → dist → openclaw → repo root (the project the hooks analyze).
  const here = dirname(fileURLToPath(import.meta.url));
  const repoRoot = resolve(here, "..", "..", "..");
  const out = await buildReorientation({ cwd: repoRoot, sessionId: "test-compaction", marker: "itest" });
  if (out.length === 0) return; // no Python on PATH — acceptable
  assert.equal(out[0].text, COMPACTION_NOTICE);
  assert.ok(out.length >= 2, "expected the notice plus at least the stack note");
});
