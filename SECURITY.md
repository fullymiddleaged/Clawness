# Security Policy

## Supported Versions

Only the latest released version of Clawness is supported. Since
`marketplace.json` points at `main` (see [CLAUDE.md](CLAUDE.md#releasing)),
updating to the newest release is the only supported way to get a fix.

## Reporting a Vulnerability

Please report security issues privately via
[GitHub Security Advisories](https://github.com/fullymiddleaged/Clawness/security/advisories/new)
rather than a public issue. Include:

- The affected file(s)/hook(s) and version
- Steps to reproduce, and what you expected vs. observed
- Impact (what an attacker could do with it)

You should get an initial response within a few days.

## Scope

Clawness runs entirely locally as a Claude Code plugin — there's no server or
service to attack. Relevant classes of report:

- A hook (`hooks/*.py`) that can be tricked into executing, reading, or
  transmitting something it shouldn't
- The access guard (`clawness/guard.py`) failing to catch (or wrongly
  allowing) a genuinely dangerous tool call
- The trust ledger (`clawness/trust.py`) missing a real prompt-injection
  pattern in a skill/agent/MCP artifact
- Path traversal or arbitrary file write via rule/memory/handoff file handling

**Out of scope / by design:** the access guard is a harm-reduction tripwire
over an agent's own tool calls, not a sandbox — it's built to catch honest
mistakes and low-effort attacks, and a determined adversary who already
controls the agent's shell can route around regex-based detection (see
[CLAUDE.md](CLAUDE.md) for the full threat-model rationale). If you find a
guard bypass, it's still worth reporting — we track and close specific
bypasses even though the guard doesn't claim to be airtight — just note in
the report whether it's a design gap or a genuine bug in the matching logic.
