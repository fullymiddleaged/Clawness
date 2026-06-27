#!/usr/bin/env python3
"""
CLI for Clawness.

Usage:
    clawness query "implement async endpoint"
    clawness query "handle auth tokens" --domain python --top-k 3
    clawness stats
    clawness lint
    clawness bench
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

from .core import WritLite, load_rules


def _default_rules_dir() -> Path:
    """Locate the global rules directory, robust to how writ_lite was launched —
    run from a clone / plugin cache, pip-installed editable, or pip-installed into
    site-packages. Order: WRIT_RULES_DIR env, package-relative ./rules, then the
    manual-install location under the Claude config dir."""
    env = os.environ.get("WRIT_RULES_DIR")
    if env:
        return Path(env)
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    claude_dir = Path(cfg.split(",")[0].strip()) if cfg else Path.home() / ".claude"
    candidates = [
        Path(__file__).resolve().parent.parent / "rules",  # clone / plugin cache / editable
        claude_dir / "clawness" / "rules",                 # manual install location
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]  # package-relative — used in the "not found" message


DEFAULT_RULES_DIR = _default_rules_dir()


def cmd_query(args: argparse.Namespace) -> None:
    rules_dir = Path(args.rules_dir)
    if not rules_dir.exists():
        print(f"Rules directory not found: {rules_dir}", file=sys.stderr)
        sys.exit(1)

    wl = WritLite(rules_dir, context_budget=args.budget, top_k=args.top_k)
    result = wl.retrieve(args.query, domain=args.domain)
    print(result)


def cmd_stats(args: argparse.Namespace) -> None:
    rules_dir = Path(args.rules_dir)
    wl = WritLite(rules_dir)
    s = wl.stats

    print(f"Rules directory : {s['rules_dir']}")
    print(f"Ranked rules    : {s['ranked_rules']}")
    print(f"Mandatory rules : {s['mandatory_rules']}")
    print(f"Total           : {s['total_rules']}")
    print(f"Semantic embed  : {s['embeddings'] or 'off (lexical + concepts only)'}")

    # domain breakdown
    ranked, mandatory = load_rules(rules_dir)
    domains: dict[str, int] = {}
    for r in ranked + mandatory:
        domains[r.domain] = domains.get(r.domain, 0) + 1
    if domains:
        print("\nBy domain:")
        for d, count in sorted(domains.items()):
            print(f"  {d}: {count}")


def cmd_lint(args: argparse.Namespace) -> None:
    rules_dir = Path(args.rules_dir)
    ranked, mandatory = load_rules(rules_dir)
    issues = 0

    for r in ranked + mandatory:
        problems = []
        if not r.id:
            problems.append("missing 'id'")
        if not r.rule:
            problems.append("missing 'rule'")
        if not r.when:
            problems.append("missing 'when'")
        if r.severity not in ("error", "warning", "info"):
            problems.append(f"invalid severity '{r.severity}'")
        if not r.tags:
            problems.append("no tags (retrieval quality will suffer)")

        if problems:
            issues += len(problems)
            print(f"  {r.source_path}:")
            for p in problems:
                print(f"    - {p}")

    total = len(ranked) + len(mandatory)
    if issues == 0:
        print(f"All {total} rules pass lint.")
    else:
        print(f"\n{issues} issue(s) across {total} rules.")
        sys.exit(1)


def cmd_bench(args: argparse.Namespace) -> None:
    rules_dir = Path(args.rules_dir)
    wl = WritLite(rules_dir)

    queries = [
        "implement async REST endpoint",
        "write unit tests for auth module",
        "handle database connection errors",
        "import ordering and circular deps",
        "validate user input from form",
        "add type hints to function",
        "refactor class to use composition",
        "set up CI pipeline config",
        "add logging to payment flow",
        "optimize SQL query performance",
    ]

    print(f"Benchmarking {len(queries)} queries against {wl.stats['total_rules']} rules...\n")

    times: list[float] = []
    for q in queries:
        t0 = time.perf_counter_ns()
        wl.retrieve(q)
        elapsed = (time.perf_counter_ns() - t0) / 1e6
        times.append(elapsed)
        print(f"  {elapsed:6.3f}ms  {q}")

    times.sort()

    def pct(p: float) -> float:
        # Nearest-rank percentile; clamp so we never index past the end.
        if not times:
            return 0.0
        rank = math.ceil(p / 100 * len(times))
        return times[min(max(rank, 1), len(times)) - 1]

    p50 = pct(50)
    p95 = pct(95)
    avg = sum(times) / len(times)

    print(f"\n  avg={avg:.3f}ms  p50={p50:.3f}ms  p95={p95:.3f}ms")


def cmd_init(args: argparse.Namespace) -> None:
    from .init import main as init_main
    init_args = [args.project_dir]
    if args.write:
        init_args.append("--write")
    sys.argv = ["clawness-init"] + init_args
    init_main()


def cmd_plan(args: argparse.Namespace) -> None:
    from . import plan as P
    root = P.find_project_root(Path(args.project))

    def show() -> None:
        enabled = P.gate_enabled(root)
        print(f"Project   : {root}")
        print(f"Plan gate : {'ON (default)' if enabled else 'off'}")
        print(f"Manual approve (override): {'active' if P.manually_approved(root) else 'none'}")
        print()
        print("Normal flow needs no commands: present a plan, the user approves it")
        print("in plan mode, and the gate clears for the session automatically.")

    action = args.action
    if action in ("show", "status"):
        show()
    elif action == "approve":
        P.approve(root)
        print("Manually approved — file edits allowed for this project until reset.")
    elif action == "reset":
        P.reset(root)
        print("Manual approval cleared.")
    elif action in ("on", "off"):
        cfg = P.load_config(root)
        cfg["plan_gate"]["enabled"] = (action == "on")
        P.save_config(root, cfg)
        print(f"Plan gate {'enabled' if action == 'on' else 'disabled'} for {root}.")


AGENTS_MD_TEMPLATE = """\
# AGENTS.md

This repository uses **clawness** to supply coding rules on demand. It is a
plain CLI over a YAML rule corpus, so *any* agent that can run a shell command
can use it — it is not tied to one editor or tool.

## Before writing or changing code

Run the retriever with a short description of the task and follow what it
returns:

    clawness query "<what you are about to implement>"

- Rules marked `(.../error)` and anything in the mandatory set are
  non-negotiable.
- `warning` / `info` rules are strong defaults — deviate only with a reason.
- Re-run the query when you move to a different part of the task or stack.

## Optional plan gate

This project may have a plan gate enabled (on by default; check
`clawness plan status`). File edits are blocked until a plan is approved. In
Claude Code, approve a plan via native plan mode and the gate clears for the
session. In other agents, a human can approve manually:

    clawness plan approve     # allow edits for this project
    clawness plan off         # disable the gate entirely

## Notes

- Rules live in the clawness `rules/` tree (YAML) and are versioned with the
  repo.
- This file is the portable entry point. Claude Code users additionally get
  automatic rule injection, Bash-output compression, and the plan gate wired
  in through hooks.
"""


def cmd_agents(args: argparse.Namespace) -> None:
    project = Path(args.project)
    target = project / "AGENTS.md"
    if args.write:
        if target.exists():
            print(f"{target} already exists — not overwriting. Snippet to add:\n")
            print(AGENTS_MD_TEMPLATE)
        else:
            target.write_text(AGENTS_MD_TEMPLATE)
            print(f"Wrote {target}")
    else:
        print(AGENTS_MD_TEMPLATE)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="clawness",
        description="Lightweight hybrid rule retrieval for AI coding agents.",
    )
    parser.add_argument(
        "--rules-dir", "-r",
        default=str(DEFAULT_RULES_DIR),
        help="Path to rules directory (default: ./rules/)",
    )
    sub = parser.add_subparsers(dest="command")

    # query
    p_query = sub.add_parser("query", help="Retrieve rules for a query")
    p_query.add_argument("query", help="Natural-language task description")
    p_query.add_argument("--domain", "-d", default=None, help="Filter to domain")
    p_query.add_argument("--top-k", "-k", type=int, default=6)
    p_query.add_argument("--budget", "-b", type=int, default=4000, help="Token budget")

    # stats
    sub.add_parser("stats", help="Show corpus statistics")

    # lint
    sub.add_parser("lint", help="Validate rule files")

    # bench
    sub.add_parser("bench", help="Benchmark retrieval latency")

    # init
    p_init = sub.add_parser("init", help="Scan project and suggest rule domains")
    p_init.add_argument("project_dir", nargs="?", default=".", help="Project directory to scan")
    p_init.add_argument("--write", action="store_true", help="Write starter rule to disk")

    # plan (process-keeper gate; ON by default, cleared via native plan mode)
    p_plan = sub.add_parser("plan", help="Plan gate status / overrides (on by default)")
    p_plan.add_argument(
        "action",
        choices=["show", "status", "approve", "reset", "on", "off"],
        help="status | approve (manual override) | reset | on | off",
    )
    p_plan.add_argument("--project", default=".", help="Project directory (default: cwd)")

    # agents-md (portable entry point for any agent)
    p_agents = sub.add_parser("agents-md", help="Print/write an AGENTS.md that points any agent at clawness")
    p_agents.add_argument("--project", default=".", help="Project directory (default: cwd)")
    p_agents.add_argument("--write", action="store_true", help="Write AGENTS.md to the project")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "init":
        cmd_init(args)
    elif args.command == "plan":
        cmd_plan(args)
    elif args.command == "agents-md":
        cmd_agents(args)
    else:
        {"query": cmd_query, "stats": cmd_stats, "lint": cmd_lint, "bench": cmd_bench}[
            args.command
        ](args)


if __name__ == "__main__":
    main()
