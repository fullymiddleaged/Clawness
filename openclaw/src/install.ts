/**
 * install.ts — host-agnostic logic for OpenClaw's `before_install` trust vetting.
 *
 * OpenClaw fires `before_install` with the artifact's on-disk `sourcePath` and
 * accepts `{ findings, block, blockReason }` back. We scan the artifact with the
 * bundled `openclaw/pyhooks/install_scan.py` (which reuses `clawness.trust`) and
 * translate its JSON into that result. Pure/translation code lives here so it is
 * unit-testable; index.ts owns the only OpenClaw contact. Fails toward doing
 * nothing — a broken scan never blocks an install.
 *
 * This is an OpenClaw-only surface: Claude Code has no install-time hook, so none
 * of this runs there and the shared engine is untouched.
 */
import { runPythonHook } from "./bridge.js";

const INSTALL_SCAN_HOOK = "openclaw/pyhooks/install_scan.py";

/** One finding in OpenClaw's `PluginInstallFinding` shape. */
export interface InstallFinding {
  ruleId: string;
  severity: "info" | "warn" | "critical";
  file: string;
  line: number;
  message: string;
}

/** Raw scan output from the Python hook. */
export interface InstallScan {
  findings: InstallFinding[];
  critical: number;
}

/** OpenClaw's `before_install` result shape. */
export interface InstallResult {
  findings?: InstallFinding[];
  block?: boolean;
  blockReason?: string;
}

const VALID_SEVERITY = new Set(["info", "warn", "critical"]);

/**
 * Parse install_scan.py stdout into a validated scan. Any malformed field drops
 * that finding rather than the whole result, and non-JSON yields an empty scan —
 * defensive because this decides whether an install proceeds.
 */
export function parseInstallScan(stdout: string): InstallScan {
  const text = (stdout ?? "").trim();
  if (!text) return { findings: [], critical: 0 };
  let obj: unknown;
  try {
    obj = JSON.parse(text);
  } catch {
    return { findings: [], critical: 0 };
  }
  const rawFindings = (obj as { findings?: unknown })?.findings;
  if (!Array.isArray(rawFindings)) return { findings: [], critical: 0 };

  const findings: InstallFinding[] = [];
  for (const f of rawFindings) {
    const r = f as Record<string, unknown>;
    const severity = String(r?.severity ?? "warn");
    if (!VALID_SEVERITY.has(severity)) continue;
    if (typeof r?.message !== "string" || !r.message) continue;
    findings.push({
      ruleId: typeof r.ruleId === "string" ? r.ruleId : "clawness/injection-tell",
      severity: severity as InstallFinding["severity"],
      file: typeof r.file === "string" ? r.file : "",
      line: Number.isFinite(r.line as number) ? (r.line as number) : 0,
      message: r.message,
    });
  }
  const critical = findings.filter((f) => f.severity === "critical").length;
  return { findings, critical };
}

/**
 * Turn a scan into a before_install result. Findings are ALWAYS advisory and are
 * surfaced for the user to judge; a block is armed only when `allowBlock` (opt-in,
 * off by default — see index.ts) AND at least one CRITICAL tell is present. This
 * mirrors `clawness audit-skills`, which is deliberately report-only: injection
 * tells are high-signal but not proof — a security skill legitimately mentions
 * `curl`/`.env`, and any repo that DOCUMENTS these patterns (this one included:
 * `trust.py`'s own regexes, the security rules) would false-positive. Defaulting to
 * block would let a broad artifact block real work, the exact failure the guard
 * philosophy warns against. Returns {} when there is nothing to report.
 */
export function toInstallResult(scan: InstallScan, opts: { allowBlock: boolean }): InstallResult {
  if (!scan.findings.length) return {};
  const result: InstallResult = { findings: scan.findings };
  if (opts.allowBlock && scan.critical > 0) {
    result.block = true;
    const n = scan.critical;
    result.blockReason =
      `Clawness blocked this install: ${n} critical prompt-injection/exfil ` +
      `tell${n === 1 ? "" : "s"} in the artifact (see findings). Blocking is on ` +
      `via CLAW_INSTALL_BLOCK; unset it (or set CLAW_NO_INSTALL_SCAN=1) and retry ` +
      `if this is a trusted tool.`;
  }
  return result;
}

/** Run the bundled scanner over an artifact path. Empty scan when Python is absent. */
export async function runInstallScan(sourcePath: string): Promise<InstallScan> {
  if (!sourcePath) return { findings: [], critical: 0 };
  const result = await runPythonHook(INSTALL_SCAN_HOOK, { sourcePath });
  if (result.noPython) return { findings: [], critical: 0 };
  return parseInstallScan(result.stdout);
}
