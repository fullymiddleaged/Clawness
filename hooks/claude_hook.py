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

# The rules + memory blocks contain non-ASCII (em-dashes, arrows, etc.). On
# Windows the default console encoding is cp1252, which mangles or crashes
# (UnicodeEncodeError) on characters it can't represent — which would drop rule
# injection entirely. Claude reads hook stdout as UTF-8, so emit UTF-8 always.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from clawness.core import Clawness, load_rules, Rule, render_memory_block
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
    # PyYAML. Fast enough to run on every prompt without risking the hook timeout.
    wl = Clawness(global_dir, context_budget=budget, top_k=top_k,
                  stack_domains=stack_domains)

    # --- Load project rules (if present) ---
    project_dir = find_project_rules(cwd)
    if project_dir and project_dir.exists():
        proj_ranked, proj_mandatory = load_rules(project_dir)

        # Merge project rules into the retriever
        if proj_ranked or proj_mandatory:
            # Rebuild with combined rules
            all_ranked = wl._ranked_rules + proj_ranked
            all_mandatory = wl._mandatory_rules + proj_mandatory
            wl._mandatory_rules = all_mandatory
            wl._ranked_rules = all_ranked

            # Rebuild indexes with the combined corpus
            if all_ranked:
                from clawness.core import _tokenize, TfIdfIndex, BM25
                search_texts = [r._search_text for r in all_ranked]
                tokenized = [_tokenize(t) for t in search_texts]
                wl._bm25 = BM25()
                wl._bm25.build(tokenized)
                wl._tfidf = TfIdfIndex()
                wl._tfidf.build(search_texts)

    block = wl.retrieve(prompt)

    # --- Inject project memory (lessons-learned log), if present ---
    memory_path = find_project_memory(cwd)
    if memory_path:
        mem_budget = int(os.environ.get("CLAW_MEMORY_BUDGET", "2000"))
        memory_block = render_memory_block(memory_path, char_budget=mem_budget)
        if memory_block:
            block = block + "\n\n" + memory_block

    suggestions = suggest_actions(prompt)
    if suggestions:
        block = block + "\n" + suggestions
    print(block)


if __name__ == "__main__":
    main()
