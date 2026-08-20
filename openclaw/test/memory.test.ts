import { test } from "node:test";
import assert from "node:assert/strict";
import { parseSearchResults, parseGetResult, makeMemoryCorpusSupplement } from "../src/memory.js";

test("parseSearchResults: empty/non-JSON/non-array → []", () => {
  assert.deepEqual(parseSearchResults(""), []);
  assert.deepEqual(parseSearchResults("nope"), []);
  assert.deepEqual(parseSearchResults(JSON.stringify({ not: "an array" })), []);
});

test("parseSearchResults: maps rows and drops those without a snippet", () => {
  const stdout = JSON.stringify([
    { corpus: "clawness-memory", path: ".clawness/memory.md", title: "t", score: 0.9, snippet: "lesson one", id: "a" },
    { corpus: "clawness-memory", score: 0.5 }, // no snippet → dropped
  ]);
  const rows = parseSearchResults(stdout);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].snippet, "lesson one");
  assert.equal(rows[0].score, 0.9);
});

test("parseSearchResults: fills defaults for missing optional fields", () => {
  const rows = parseSearchResults(JSON.stringify([{ snippet: "x" }]));
  assert.equal(rows[0].corpus, "clawness-memory");
  assert.equal(rows[0].path, ".clawness/memory.md");
  assert.equal(rows[0].score, 0);
});

test("parseGetResult: null / 'null' / non-JSON / no content → null", () => {
  assert.equal(parseGetResult(""), null);
  assert.equal(parseGetResult("null"), null);
  assert.equal(parseGetResult("nope"), null);
  assert.equal(parseGetResult(JSON.stringify({ corpus: "x" })), null);
});

test("parseGetResult: returns a validated record", () => {
  const stdout = JSON.stringify({ corpus: "clawness-memory", path: ".clawness/memory.md", content: "the lesson", fromLine: 1, lineCount: 1, id: "z" });
  const got = parseGetResult(stdout);
  assert.equal(got?.content, "the lesson");
  assert.equal(got?.fromLine, 1);
});

test("makeMemoryCorpusSupplement: blank query/lookup short-circuit to empty (no spawn)", async () => {
  const sup = makeMemoryCorpusSupplement(() => "/tmp/does-not-matter");
  assert.deepEqual(await sup.search({ query: "   " }), []);
  assert.equal(await sup.get({ lookup: "" }), null);
});
