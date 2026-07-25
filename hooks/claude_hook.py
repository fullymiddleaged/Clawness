#!/usr/bin/env python3
"""
Claude Code hook for Clawness — rule retrieval with global + project layers.

How it works:
  1. Fires on every UserPromptSubmit
  2. Loads GLOBAL rules from ~/.claude/clawness/rules/ (always)
  3. Loads PROJECT rules from <project>/.clawness/rules/ (if they exist)
  4. Merges both into a single retriever
  5. Retrieves relevant rules for the current prompt
  6. Prints the rule block to stdout → Claude sees it as context

Install once, works everywhere. Project rules layer on top when present.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Force UTF-8 on both stdin and stdout. On Windows these default to the locale
# code page (cp1252), which (a) mangles non-ASCII in a prompt read from stdin —
# em-dashes, accents, emoji — before we ever see it, and (b) mangles or crashes
# (UnicodeEncodeError) when emitting the rules/memory blocks, dropping injection.
# Claude speaks UTF-8 on both ends, so pin it regardless of platform.
try:
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from clawness.core import Clawness, load_rules, Rule, render_memory_block
    from clawness.session_state import (
        bump_prompt_count,
        context_snapshot,
        memory_changed,
        should_alert_context,
        should_show_full,
    )
except Exception:
    # Dependencies not ready yet (e.g. the SessionStart bootstrap is still
    # installing pyyaml). Degrade silently rather than erroring the prompt.
    sys.exit(0)


def find_global_rules() -> Path:
    """Global rules: next to this script, or CLAW_RULES_DIR override."""
    if env := os.environ.get("CLAW_RULES_DIR"):
        return Path(env)
    return Path(__file__).resolve().parent.parent / "rules"


def find_project_rules(cwd: str) -> Path | None:
    """Walk up from cwd looking for .clawness/rules/ in the project tree."""
    current = Path(cwd).resolve()
    # Walk up at most 10 levels looking for .clawness/rules/
    for _ in range(10):
        candidate = current / ".clawness" / "rules"
        if candidate.is_dir():
            return candidate
        # Also check for .git to stop at repo root
        if (current / ".git").exists():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def find_project_memory(cwd: str) -> Path | None:
    """Walk up from cwd looking for .clawness/memory.md in the project tree.

    Mirrors find_project_rules so the lessons log sits beside the project rules
    and is discovered the same way (stop at the repo root)."""
    current = Path(cwd).resolve()
    for _ in range(10):
        candidate = current / ".clawness" / "memory.md"
        if candidate.is_file():
            return candidate
        if (current / ".git").exists():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def detect_stack(cwd: str) -> set[str] | None:
    """Detect the project's stack domains for codebase-aware retrieval.

    Returns the set of detected domains (e.g. {"python","fastapi","general"}) when
    at least one language/framework is recognized, else None — and None means "do
    not penalize off-stack rules", so an unknown/empty project behaves exactly as
    before. Opt out entirely with CLAW_NO_STACK_FILTER. Fails open to None."""
    if os.environ.get("CLAW_NO_STACK_FILTER"):
        return None
    try:
        from clawness.core import _STACK_DOMAINS
        from clawness.init import scan_project

        # Scan the project root (the dir that holds .clawness/) when we found one,
        # else cwd — so detection matches where rules/memory are anchored.
        proj = find_project_rules(cwd)
        scan_root = proj.parent.parent if proj else Path(cwd)
        domains = set(scan_project(scan_root).get("domains", []))
        # Only enable the off-stack penalty once we actually recognize a language
        # or framework — otherwise we'd penalize everything on a bare/unknown repo.
        return domains if (domains & _STACK_DOMAINS) else None
    except Exception:
        return None


def suggest_actions(prompt: str) -> str:
    """Detect audit/review/perf intent and return a short, deterministic nudge
    so Claude reliably *offers* the relevant skill (auto-invocation alone isn't
    reliable). The skills themselves confirm before spawning agents, so this only
    surfaces the offer — it never runs anything."""
    p = prompt.lower()

    def has(*phrases: str) -> bool:
        return any(ph in p for ph in phrases)

    lines: list[str] = []
    if has("security audit", "pentest", "penetration test", "red team", "blue team",
           "vulnerabilit", "is this secure", "is it secure", "security review",
           "security check", "owasp", "threat model", "exploit"):
        lines.append(
            "This resembles a security audit. Offer to run the red team / blue team "
            "review (/clawness:audit) — it spawns several sub-agents, so ask "
            "before running."
        )
    if has("code review", "review my code", "review the code", "review my changes",
           "review my pr", "pr review", "pull request", "before merging",
           "before i merge", "ready to merge"):
        lines.append(
            "This resembles a code review. Offer to run the adversarial review "
            "(/clawness:review) — confirm before running."
        )
    if has("performance audit", "perf audit", "performance review", "optimize performance",
           "n+1", "bottleneck", "profiling", "why is this slow", "too slow",
           "memory leak", "re-render", "rerender"):
        lines.append(
            "This resembles a performance review. Offer to run the performance audit "
            "(/clawness:perf) — confirm before running."
        )

    if not lines:
        return ""
    return "\n--- CLAWNESS SUGGESTED ACTIONS ---\n" + "\n".join(f"- {ln}" for ln in lines)


def context_note(event: dict, cwd: str, session_id: str) -> str:
    """Alert text when this session's context is getting full, else "".

    Imported lazily inside the function so a broken/absent context_watch can
    never stop the rules block from printing."""
    from clawness.context_watch import (
        assess, find_transcript, read_context_tokens, render_alert,
    )

    transcript = find_transcript(event, cwd, session_id)
    if not transcript:
        return ""
    tokens = read_context_tokens(transcript)
    if not tokens:
        return ""
    previous = context_snapshot(session_id, tokens)
    alert = assess(tokens, previous_tokens=previous)
    if not alert or not should_alert_context(session_id, alert.level):
        return ""
    return render_alert(alert)


def main() -> None:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, IOError):
        event = {}

    prompt = (
        event.get("prompt")
        or event.get("user_prompt")
        or event.get("message")
        or event.get("user_message")
        or event.get("query")
        or ""
    )

    if not prompt:
        sys.exit(0)

    cwd = event.get("cwd", os.getcwd())
    budget = int(os.environ.get("CLAW_BUDGET", "4000"))
    top_k = int(os.environ.get("CLAW_TOP_K", "5"))

    # --- Session-aware re-injection ---
    # The mandatory block is identical every prompt, yet re-sent in full on
    # every single turn. Show it in full on prompt 1 and every CLAW_FULL_EVERY
    # prompts after (default 5); abbreviate to an id list in between — the
    # rules stay just as binding, only their re-statement is compressed.
    # CLAW_FULL_EVERY=1 restores the old always-full behavior. Any failure in
    # the session-state lookup defaults to a full render (fail toward showing
    # more, never less).
    session_id = event.get("session_id", "") or ""
    try:
        full_every = int(os.environ.get("CLAW_FULL_EVERY", "5"))
    except ValueError:
        full_every = 5
    prompt_count = bump_prompt_count(session_id)
    show_full = should_show_full(prompt_count, full_every)

    # --- Detect the project's stack (codebase-aware retrieval) ---
    # Off-stack language/framework rules then face a higher relevance floor, so a
    # vague prompt in a Python repo won't surface SQL/Capacitor/React noise — while
    # a genuinely strong cross-domain match still gets through. Scanned fresh each
    # prompt (~3ms) so a mid-session dependency is picked up immediately. If no
    # language/framework is recognized (unknown stack), pass None → no penalty.
    stack_domains = detect_stack(cwd)

    # --- Load global rules (always) ---
    global_dir = find_global_rules()
    if not global_dir.exists():
        sys.exit(0)

    # Pure-Python lexical + concept retrieval — ~1ms, no model, no deps beyond
    # PyYAML. build_index=False: defer the BM25/TF-IDF build until project rules
    # (below) are merged in, so a project with .clawness/rules/ only builds the
    # index once instead of once for global-only then again for the merged set.
    wl = Clawness(global_dir, context_budget=budget, top_k=top_k,
                  stack_domains=stack_domains, build_index=False)

    # --- Load project rules (if present) ---
    project_dir = find_project_rules(cwd)
    if project_dir and project_dir.exists():
        proj_ranked, proj_mandatory = load_rules(project_dir)
        if proj_ranked or proj_mandatory:
            wl.add_rules(proj_ranked, proj_mandatory)

    wl.build_index()

    block = wl.retrieve(prompt, abbreviate_mandatory=not show_full)

    # --- Inject project memory (lessons-learned log), if present ---
    # Memory is RETRIEVED, not dumped: pinned `## Always` entries plus the few
    # lessons that match this prompt (see clawness/memory.py). That's a handful
    # of lines however long the log gets, so it ships every turn rather than
    # riding the mandatory block's cadence — abbreviating a block that's already
    # prompt-specific and ~3 lines would save nothing and lose the match.
    # A file that changed this session forces its newest entries in regardless of
    # match, so a lesson written mid-session is never invisible on the next turn.
    memory_path = find_project_memory(cwd)
    if memory_path:
        try:
            mem_budget = int(os.environ.get("CLAW_MEMORY_BUDGET", "1200"))
        except ValueError:
            mem_budget = 1200
        memory_block = render_memory_block(
            memory_path,
            char_budget=mem_budget,
            query=prompt,
            force_recent=memory_changed(session_id, memory_path),
        )
        if memory_block:
            block = block + "\n\n" + memory_block

    # --- Context-pressure watch ---
    # Reads the session's own transcript to see how full the window is, and warns
    # once per level so a long session gets told BEFORE it degrades or auto-
    # compacts. Rides this hook rather than a separate one: it's a file tail plus
    # arithmetic (~0.5ms), not worth another process spawn per prompt. Wrapped
    # whole — a session that can't be measured must never break the prompt.
    if not os.environ.get("CLAW_NO_CONTEXT_WATCH"):
        try:
            note = context_note(event, cwd, session_id)
            if note:
                block = block + "\n\n" + note
        except Exception:
            pass

    suggestions = suggest_actions(prompt)
    if suggestions:
        block = block + "\n" + suggestions
    print(block)


if __name__ == "__main__":
    main()
