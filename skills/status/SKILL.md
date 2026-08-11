---
name: status
description: >
  Show the current Clawness configuration: which rules are loaded,
  which agents are available, and how the hook is configured. Use to
  understand what rules Claude is seeing for this project.
---

# Clawness Status

Show the user what's currently active in their Clawness setup.

> **Running the Clawness CLI.** It ships with the plugin but isn't on your PATH.
> Use the wrapper the SessionStart bootstrap hook writes each session:
> ```bash
> CLAW="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/clawness/clawness-cli.sh"
> bash "$CLAW" <command>          # e.g. bash "$CLAW" stats
> ```
> If `$CLAW` doesn't exist, the bootstrap hasn't run yet — start a fresh session.
> (Editable/manual installs may instead use `python -m clawness.cli ...`.)

Keep this FAST — it's a quick health check, not a full scan. Step 1 already
proves retrieval at zero cost, so a test query is unnecessary.

## Steps

1. **Report the rules you already see (no command).** You (Claude) receive the
   Clawness rule block at the top of your context this turn. Summarize it: how
   many mandatory + relevant rules, and a few rule IDs. If you see it, injection
   is working — this alone is the health check.

   **If you do NOT see an injected block, say so plainly and do not rationalize
   it.** The hook injects the global rules on *every* prompt in *every*
   directory — the working directory (including being in the Clawness source
   repo) does **not** disable injection; it only affects the optional
   `.clawness/rules/` project overlay. A missing block means the hook is **not
   running** — either not loaded this session (needs a restart / `/reload-plugins`)
   or erroring (check `/hooks` and `claude --debug`). Never attribute a missing
   block to the current directory.

2. **Show the corpus + token cost.** Run (fast):
   ```bash
   bash "$CLAW" stats
   ```
   Report total rules, domains, and the `Tokens / turn` line.

3. **Project rules (only if present).** If `.clawness/rules/` exists in the
   project (walk up to the git root), mention it; otherwise skip silently.

**Summary** — keep it to a few lines:
- Rule injection: working (you saw the block) / not seeing rules
- Global rules: N across M domains; ~N tokens/turn (tune with `CLAW_TOP_K`, `CLAW_VERBOSE`, `CLAW_COMPACT`)
- Project rules: N, or "none — `bash \"$CLAW\" init .` to add some"
