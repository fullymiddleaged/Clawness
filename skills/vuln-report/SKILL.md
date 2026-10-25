---
name: vuln-report
description: >
  Write a vulnerability report that is clear, short and ready to submit: a
  GitHub-advisory-shaped disclosure for maintainers (summary, affected versions, CVSS
  4.0 vector, CWE, root cause at pinned file:line, minimal PoC, impact, suggested fix),
  or a one-page summary of a security audit. Every claim cites code and says whether it
  was verified. Saves a draft locally and never submits anything. Use when the user asks
  to write up, draft or report a vulnerability, CVE, security advisory, bug bounty
  submission or security finding, or to summarise an audit's results.
---

# Vulnerability report

A maintainer triaging this has five minutes. Lead with what's broken and how to see
it; cut everything that doesn't help them reproduce, rate or fix it.

**Pick the mode.** Disclosure to a project's maintainers (default) → *Advisory*. A
summary of `/clawness:security-audit` or `/clawness:vuln-hunt` results for the
user's team → *Audit summary*. One advisory per root cause: two sinks with the same
missing check is one report, while two unrelated bugs are two.

## Gather first, write second

- Pinned commit `git rev-parse HEAD`, remote `git remote get-url origin`, and the
  latest release tag. Build file links as permalinks:
  `https://github.com/<owner>/<repo>/blob/<sha>/<path>#L<a>-L<b>`. A link to a
  branch rots the day the fix lands.
- The finding from the ledger (`.clawness/security/findings.json`) or the conversation:
  class, file:line, attack path, PoC and its output.
- Affected range: the earliest version you checked that has it and the latest. Write
  only what you checked ("confirmed in 2.3.1 through 2.4.0 and main @ 9f3c2e1"),
  never a guessed "all versions".
- What was checked for duplicates (advisories, OSV, issues) and what was not verified.

## Advisory template

Use these headings in this order. When the user files through GitHub's private
vulnerability reporting, the header lines map to the form's package, version,
severity and CWE fields, and the sections below them form the description:

```markdown
# <Class> in <component> allows <attacker> to <impact>

**Package:** <ecosystem>/<name> · **Affected:** <range checked> · **Patched:** none yet
**Severity:** <Low|Medium|High|Critical> (<score>) `CVSS:4.0/<vector>`
**CWE:** CWE-<n> <name>

## Summary
<Two sentences: what an attacker sends, through what, and what they get.>

## Details
<Root cause in a short paragraph, then the path from input to sink as permalinked
file:line steps. Quote only the lines that matter.>

## PoC
<Exact setup (commit, config, command to start it), the request or input, and the
observed output. Harmless marker only.>

## Impact
<Who is affected and under what configuration. Name the preconditions (auth, a
non-default setting, user interaction) here and in the vector.>

## Suggested fix
<The smallest change, as a diff where you have one, plus the variant sites that
need the same fix.>

## Notes
Checked for duplicates: <sources>. Not verified: <list, or "nothing">.
Credit: <the name the user wants credited>.
```

## Audit summary template

```markdown
# Security audit: <project> @ <sha> (<date>)
**Scope:** <paths> · **Method:** clawness scan + <SAST tools run, or "no SAST"> + red/blue review
**Coverage:** <adjudicated>/<total> candidates, converged: <yes|no>

| Sev | Finding | Location | Status |
|-----|---------|----------|--------|
<one row per confirmed finding, most severe first>

## Fix first
<At most three items, one line each: what to change and why it's first.>

Not covered: <what the method can't see, such as cross-file taint without SAST>.
```

## Style rules

- **Each sentence is a fact the reader needs.** No preamble ("I discovered…",
  "This report describes…"), no closing pleasantries, no restating the title.
- **Certain where verified, explicit where not.** Write "allows" for what the PoC
  showed. For what you didn't show, write "not verified: …" in Notes, and never
  "could potentially" or "may possibly".
- **Severity matches the vector.** Don't use "critical" or "severe" in prose unless
  the score says so. No exclamation marks, no capitals for emphasis.
- **Numbers and names over adjectives:** "any unauthenticated user" not "attackers",
  "reads any file the service user can" not "serious data exposure".
- **Short.** An advisory runs about a screen plus the PoC. Past two, cut Details
  first.
- Define a term only if a maintainer of *this* project wouldn't know it.

## Finish

1. Save the draft to `.clawness/security/reports/<YYYY-MM-DD>-<slug>.md`. It's
   gitignored, since it maps an unfixed vulnerability.
2. Show the user the draft and list anything marked not verified.
3. Name the disclosure channel: the project's `SECURITY.md`, or GitHub's "Report a
   vulnerability" on the repo's Security tab. Then stop. **Never submit, email, post
   or open an issue yourself.** The user sends it.

If $ARGUMENTS names a ledger id or finding, write that one; otherwise ask which.
