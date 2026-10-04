---
name: security-audit
description: >
  Run a stateful red team / blue team security audit on the current project or a
  specific module. First enumerates the attack surface deterministically with
  `clawness scan` (zero LLM tokens, identical every run) and accumulates verdicts
  in a findings ledger, so each pass adjudicates only what is NEW instead of
  re-scanning blind — turning a 5-10 run scan into 1-2 converging passes. Reach
  for this whenever the user asks for a security audit, a vulnerability scan, a
  code security review, or hardening — before deployment, after adding auth/
  payment/user-data code, or on a schedule — even if they don't type the command.
---

# Security Audit Workflow

A stateful adversarial audit. Discovery is deterministic (`clawness scan`),
judgment accumulates in a ledger (`.clawness/security/findings.json`), so runs
**converge** instead of repeating.

> **Before spawning agents:** the red/blue-team pass launches several adversarial
> sub-agents and is token-intensive. If you surfaced this proactively (the user
> mentioned security but didn't explicitly ask to run it), first ask: "Want me to
> run the full red team / blue team audit now?" and proceed only once they
> confirm. Running `clawness scan` itself is cheap and deterministic — you may run
> that first to show the surface, then ask before fanning out agents. If the user
> explicitly invoked `/clawness:security-audit` or already said yes, skip the question.

The CLI isn't on PATH. Reach it through the bundled launcher, falling back to the wrapper the SessionStart bootstrap stashes on older Claude Code:

```bash
CLAW="${CLAUDE_PLUGIN_ROOT}/scripts/clawness-cli.sh"
[ -f "$CLAW" ] || CLAW="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/clawness/clawness-cli.sh"
bash "$CLAW" scan --project .          # enumerate + accumulate the ledger
bash "$CLAW" scan status --project .   # show coverage without re-scanning
```

If neither path exists (an older Claude Code before the bootstrap has run), start a fresh session.

## Steps

1. **Enumerate (deterministic, free)** — Run `bash "$CLAW" scan --project <scope>`.
   This lists sink/source candidates (SQL/command injection, unsafe deserialization,
   XSS, path traversal, broken authz, hardcoded secrets, weak crypto, SSRF), each
   with a CWE, a mapped rule id, and a stable id, and merges them into the ledger.
   Note the **coverage line** it prints. Use `--new-only` to see just what hasn't
   been adjudicated yet, `--class <name>` to focus one category.

   **SAST pass (when a scanner is installed).** Check which of these are on PATH
   (`command -v`) and run each one present, writing SARIF into one directory, then
   re-run the scan with `--sarif` so the results merge into the same ledger, dedup
   against the native candidates, and get adjudicated in step 3 like everything else:

   ```bash
   OUT=.clawness/security/sast; mkdir -p "$OUT"
   semgrep scan --config p/default --metrics=off --sarif-output="$OUT/semgrep.sarif" .
   bandit -r . -f sarif -o "$OUT/bandit.sarif"          # Python
   gitleaks dir . --report-format sarif --report-path "$OUT/gitleaks.sarif"
   trivy fs --format sarif --output "$OUT/trivy.sarif" .  # dependencies, IaC
   bash "$CLAW" scan --project . --sarif "$OUT"
   ```

   - **Never install a scanner, and never pick a ruleset that phones home, without
     asking.** Semgrep's `--config auto` sends the project URL to Semgrep's registry,
     which is why it isn't used above. If the user supplied their own Semgrep rules or
     CodeQL queries (common for researchers), run those instead. For CodeQL, build the
     database, then `codeql database analyze <db> --format=sarif-latest
     --output="$OUT/codeql.sarif"`.
   - If no scanner is installed, say which ones would add coverage for this stack
     and carry on with the native scan. Don't stall.
   - `.clawness/` is gitignored and skipped by auto-detection, so this SARIF never
     gets committed by accident (it maps where the vulnerabilities are). `--sarif`
     is what folds it in. If the project also commits `*.sarif` elsewhere, run
     `scan` once without `--sarif` to pick those up as well.
   - Tools that don't emit SARIF (TruffleHog's JSON, or a third-party skill's
     markdown report, such as secureIO's `security-scan`) can't enter the ledger.
     Read them as leads for the red team, not as adjudicated findings.

2. **Scope** — If the user named a module or directory, pass it as `--project` and
   focus there. Otherwise let the scan cover the tree and prioritise the
   security-sensitive candidates (auth, authorization, data validation, endpoints,
   queries, secret handling).

3. **Red Team — adjudicate only what's NEW.** Delegate to the `security-red-team`
   sub-agent, handing it the candidates whose ledger status is `new` (from
   `scan --new-only`): "Adjudicate these enumerated candidates. For each, decide
   confirmed / false-positive and give the attack path and file:line. Check for
   current-month CVEs affecting our stack. Also look for classes the enumerator
   can't see (logic flaws, auth bypass, IDOR beyond the heuristic). Do NOT
   re-litigate items already marked confirmed/false-positive/fixed." Have it write
   each verdict back to the ledger.

4. **Blue Team** — Hand the confirmed findings to the `security-blue-team`
   sub-agent: "Triage these confirmed findings, propose the exact code fix for
   each, add hardening, and search for current mitigations for any CVEs. Mark items
   `fixed` in the ledger once patched." It also reviews the red team's calls for
   false positives.

5. **Synthesize + report coverage** — Merge both reports into one action plan:
   - Critical (fix before deploy) / Important (this sprint) / Improvements (backlog)
   - Overall posture assessment
   - The **coverage number** from `scan status`: adjudicated vs outstanding, and
     whether it has **converged** (nothing outstanding). Tell the user when
     coverage has converged so they know they can stop — that convergence, not a
     fixed number of re-runs, is the signal.

## Honest framing (state this, don't oversell)

`clawness scan` is a **regex/heuristic tripwire, not a SAST engine** (CodeQL/
Semgrep). It makes discovery reproducible and routes attention; it will miss
cross-file taint and obfuscation, and it over-reports. Say whether a real SAST pass
ran: without one, the report rests on the tripwire alone. The value is that
adjudicating a fixed short list is far more stable than open-ended discovery, and
the ledger means a verdict is recorded once, not re-derived every run.

If $ARGUMENTS is provided, scope the audit to that path or module.
