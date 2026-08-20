import { test } from "node:test";
import assert from "node:assert/strict";
import { parseInstallScan, toInstallResult } from "../src/install.js";

test("parseInstallScan: empty/whitespace stdout → empty scan", () => {
  assert.deepEqual(parseInstallScan(""), { findings: [], critical: 0 });
  assert.deepEqual(parseInstallScan("   \n"), { findings: [], critical: 0 });
});

test("parseInstallScan: non-JSON → empty scan (fail toward no block)", () => {
  assert.deepEqual(parseInstallScan("not json"), { findings: [], critical: 0 });
});

test("parseInstallScan: reads findings and counts criticals", () => {
  const stdout = JSON.stringify({
    findings: [
      { ruleId: "clawness/injection-tell", severity: "critical", file: "SKILL.md", line: 2, message: "override" },
      { ruleId: "clawness/injection-tell", severity: "warn", file: "SKILL.md", line: 3, message: "curl" },
    ],
    critical: 1,
  });
  const scan = parseInstallScan(stdout);
  assert.equal(scan.findings.length, 2);
  assert.equal(scan.critical, 1); // recomputed from findings, not trusted from input
});

test("parseInstallScan: drops rows with bad severity or missing message", () => {
  const stdout = JSON.stringify({
    findings: [
      { severity: "bogus", file: "a", line: 1, message: "x" },
      { severity: "warn", file: "b", line: 2 }, // no message
      { severity: "critical", file: "c", line: 3, message: "real" },
    ],
  });
  const scan = parseInstallScan(stdout);
  assert.equal(scan.findings.length, 1);
  assert.equal(scan.findings[0].message, "real");
  assert.equal(scan.critical, 1);
});

test("parseInstallScan: findings not an array → empty", () => {
  assert.deepEqual(parseInstallScan(JSON.stringify({ findings: "nope" })), { findings: [], critical: 0 });
});

test("toInstallResult: no findings → {} (no noise on a clean artifact)", () => {
  assert.deepEqual(toInstallResult({ findings: [], critical: 0 }, { allowBlock: true }), {});
});

test("toInstallResult: warn-only findings surface but never block", () => {
  const scan = { findings: [{ ruleId: "r", severity: "warn" as const, file: "f", line: 1, message: "curl" }], critical: 0 };
  const res = toInstallResult(scan, { allowBlock: true });
  assert.equal(res.findings?.length, 1);
  assert.equal(res.block, undefined);
});

test("toInstallResult: a critical arms block when allowed", () => {
  const scan = { findings: [{ ruleId: "r", severity: "critical" as const, file: "f", line: 1, message: "override" }], critical: 1 };
  const res = toInstallResult(scan, { allowBlock: true });
  assert.equal(res.block, true);
  assert.match(res.blockReason ?? "", /CLAW_NO_INSTALL_BLOCK/);
});

test("toInstallResult: allowBlock=false surfaces findings but does not block", () => {
  const scan = { findings: [{ ruleId: "r", severity: "critical" as const, file: "f", line: 1, message: "override" }], critical: 1 };
  const res = toInstallResult(scan, { allowBlock: false });
  assert.equal(res.findings?.length, 1);
  assert.equal(res.block, undefined);
});
