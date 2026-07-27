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
import json
import math
import os
import sys
import time
from pathlib import Path

import yaml

from .core import Clawness, _estimate_tokens, load_rules


def _default_rules_dir() -> Path:
    """Locate the global rules directory, robust to how clawness was launched —
    run from a clone / plugin cache, pip-installed editable, or pip-installed into
    site-packages. Order: CLAW_RULES_DIR env, package-relative ./rules, then the
    manual-install location under the Claude config dir."""
    env = os.environ.get("CLAW_RULES_DIR")
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

    # --stack makes the hook's codebase-aware filtering reachable from the CLI.
    # Without it, off-stack behaviour can only be exercised through a real hook
    # run, so a claim like "llm/ stays quiet in a Rust repo" is untestable here.
    # Omitted (the default) leaves stack_domains=None, which disables the penalty
    # entirely — so plain `clawness query` and `clawness eval` are unchanged.
    stack = None
    if args.stack:
        stack = {d.strip() for d in args.stack.split(",") if d.strip()}

    wl = Clawness(
        rules_dir,
        context_budget=args.budget,
        top_k=args.top_k,
        stack_domains=stack,
    )
    # CLI output is for a human diagnosing retrieval, not model context — always
    # show relevance/timing here even though the hook hides them.
    result = wl.retrieve(args.query, domain=args.domain, show_meta=True)
    print(result)


def cmd_stats(args: argparse.Namespace) -> None:
    rules_dir = Path(args.rules_dir)
    wl = Clawness(rules_dir)
    s = wl.stats

    print(f"Rules directory : {s['rules_dir']}")
    print(f"Ranked rules    : {s['ranked_rules']}")
    print(f"Mandatory rules : {s['mandatory_rules']}")
    print(f"Total           : {s['total_rules']}")
    print(f"Retrieval       : BM25 + TF-IDF + RRF + concept expansion (lexical, ~2ms)")
    ranked_room = max(0, s["context_budget"] - s["mandatory_tokens"])
    # What the mandatory block costs on the turns it's abbreviated to an id list
    # (see session_state / CLAW_FULL_EVERY) — computed, not assumed, so it stays
    # honest as rules are added.
    abbreviated = _estimate_tokens(
        "MANDATORY (in context above, still binding): "
        + ", ".join(r.id for r in wl._mandatory_rules)
    )
    try:
        full_every = int(os.environ.get("CLAW_FULL_EVERY", "5"))
    except ValueError:
        full_every = 5
    # CLAW_FULL_EVERY<=1 disables abbreviation entirely (see session_state), so
    # "1 prompt in 1" would be a confusing way to say "every turn".
    if full_every <= 1:
        cadence = "every turn"
    else:
        cadence = (f"full on 1 prompt in {full_every}, "
                   f"~{abbreviated} as an id list in between")
    print(
        f"Tokens / turn   : ~{s['mandatory_tokens']} mandatory ({cadence}) "
        f"+ up to ~{ranked_room} ranked (top-{s['top_k']}, budget {s['context_budget']})"
    )

    # domain breakdown
    ranked, mandatory = load_rules(rules_dir)
    domains: dict[str, int] = {}
    for r in ranked + mandatory:
        domains[r.domain] = domains.get(r.domain, 0) + 1
    if domains:
        print("\nBy domain:")
        for d, count in sorted(domains.items()):
            print(f"  {d}: {count}")


# Unambiguous weasel phrases that make a rule unenforceable. A rule should say
# exactly what to do, not hedge. (Bare "consider" is intentionally excluded — it
# has legitimate uses, e.g. "consider the alternatives the agent proposes".)
import re as _re
VAGUE_RE = _re.compile(
    r"(?i)\b(where appropriate|as appropriate|when possible|where possible|"
    r"if necessary|if needed|as needed|try to|should probably|might want to|"
    r"and so on)\b"
)


def cmd_lint(args: argparse.Namespace) -> None:
    rules_dir = Path(args.rules_dir)
    ranked, mandatory = load_rules(rules_dir)
    issues = 0

    # Encoding pass: load_rules silently skips files that don't decode as UTF-8
    # or won't parse (so one bad file can't crash the hook). Lint must surface
    # them loudly, and flag any U+FFFD replacement char that signals a prior
    # decode mishap baked into the file.
    for yml_path in sorted(rules_dir.rglob("*.yml")):
        try:
            text = yml_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            issues += 1
            print(f"  {yml_path}:")
            print(f"    - not valid UTF-8 ({e.__class__.__name__}) — re-save the file as UTF-8")
            continue
        if "�" in text:
            issues += 1
            print(f"  {yml_path}:")
            print("    - contains the Unicode replacement char (U+FFFD) — encoding corruption")
        # Parse pass: a rule that doesn't parse is silently dropped from the
        # corpus by load_rules, so without this it disappears with no signal at
        # all — `stats` just shows one fewer rule than the author wrote. The
        # classic cause is an invalid escape (\{, \d) in a double-quoted scalar;
        # use single quotes or a literal block for anything with backslashes.
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as e:
            issues += 1
            print(f"  {yml_path}:")
            print(f"    - does not parse as YAML ({e.__class__.__name__}) — this rule is")
            print("      silently dropped from the corpus until fixed")
            continue
        if not isinstance(parsed, dict):
            issues += 1
            print(f"  {yml_path}:")
            print("    - top level is not a mapping — rule file must be a single YAML mapping")

    # Duplicate-id pass across the whole corpus (ranked + mandatory share one
    # namespace — the retriever and rendering both index by id).
    id_sources: dict[str, list[str]] = {}
    for r in ranked + mandatory:
        if r.id:
            id_sources.setdefault(r.id, []).append(r.source_path)

    # Mandatory rules render on EVERY prompt — an oversized one is a standing
    # token tax. 500 chars (compact render) covers every current rule with
    # headroom; a new one that blows past it should be trimmed or demoted.
    MANDATORY_CHAR_CEILING = 500

    for r in ranked + mandatory:
        problems = []
        if not r.id:
            problems.append("missing 'id'")
        elif len(id_sources.get(r.id, [])) > 1:
            others = ", ".join(p for p in id_sources[r.id] if p != r.source_path)
            problems.append(f"duplicate id '{r.id}' also used by: {others}")
        if not r.rule:
            problems.append("missing 'rule'")
        if not r.when:
            problems.append("missing 'when'")
        if r.severity not in ("error", "warning", "info"):
            problems.append(f"invalid severity '{r.severity}'")
        if not r.tags:
            problems.append("no tags (retrieval quality will suffer)")
        if not r.triggers:
            problems.append("no triggers (retrieval quality will suffer)")
        for field_name in ("rule", "when", "violation", "correct"):
            m = VAGUE_RE.search(getattr(r, field_name))
            if m:
                problems.append(
                    f"vague phrasing in '{field_name}': \"{m.group(0)}\" — "
                    "state the rule precisely"
                )
        if r.source_path:
            folder = Path(r.source_path).parent.name
            if folder != "_mandatory" and r.domain != folder:
                problems.append(
                    f"domain '{r.domain}' doesn't match its folder '{folder}'"
                )
        if r.mandatory:
            rendered_len = len(r.render(compact=True))
            if rendered_len > MANDATORY_CHAR_CEILING:
                problems.append(
                    f"mandatory rule renders {rendered_len} chars (compact) — "
                    f"exceeds the {MANDATORY_CHAR_CEILING}-char always-on budget; "
                    "trim the rule text or demote it to a ranked domain"
                )

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
    wl = Clawness(rules_dir)

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
        if not enabled:
            if os.environ.get("CLAW_NO_PLAN_GATE"):
                print("  off because CLAW_NO_PLAN_GATE is set in this shell.")
            else:
                for path in P.global_config_paths():
                    if path.exists():
                        print(f"  off because {path} sets plan_gate.enabled = false.")
                        break
        print()
        print("The gate asks once per session and clears when you approve a plan or")
        print("the first edit. It cannot be turned off for one project: the only")
        print("switches are CLAW_NO_PLAN_GATE=1 in your environment, or")
        print(f'  {P.global_config_paths()[0]}')
        print('  containing {"plan_gate": {"enabled": false}}')

    show()


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

Clawness has a plan gate, on by default (check `clawness plan status`). The
first file edit of a session prompts for confirmation. In Claude Code, approving
a plan in native plan mode — or approving that first edit — clears the gate for
the rest of the session.

It cannot be switched off for a single project, by design: a project-local
kill switch is invisible and permanent, and a gate that is silently off looks
exactly like one that is working. Set `CLAW_NO_PLAN_GATE=1` for a headless run,
which lasts only as long as that shell.

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
            target.write_text(AGENTS_MD_TEMPLATE, encoding="utf-8")
            print(f"Wrote {target}")
    else:
        print(AGENTS_MD_TEMPLATE)


def cmd_audit_skills(args: argparse.Namespace) -> None:
    """Audit context-injected artifacts (skills, sub-agents, slash-commands, MCP
    servers) for prompt-injection / exfil tells, and print their fingerprints.

    A hit is advisory, not proof — a security skill may legitimately mention
    `curl` or `.env`. Exits 1 if any tells are found so CI can gate on it."""
    from .plan import find_project_root
    from .trust import scan_artifacts, scan_injection_tells

    root = find_project_root(Path(args.project))
    artifacts = scan_artifacts(root)
    if not artifacts:
        print(f"No skills/agents/commands/MCP servers found under {root / '.claude'}.")
        return

    findings = 0
    for key, digest in sorted(artifacts.items()):
        short = digest[:12]
        if key.endswith("#mcpServers"):
            # A config block, not a file body — list it; its presence is the signal.
            print(f"  {key}  [{short}]  (MCP servers declared — review the endpoints)")
            continue
        path = root / key
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            print(f"  {key}  [{short}]  - unreadable ({e.__class__.__name__})")
            continue
        tells = scan_injection_tells(text)
        if tells:
            findings += len(tells)
            print(f"  {key}  [{short}]")
            for t in tells:
                print(f"    - {t}")
        else:
            print(f"  {key}  [{short}]  ok")

    total = len(artifacts)
    if findings == 0:
        print(f"\nAudited {total} artifact(s); no injection tells found.")
    else:
        print(f"\n{findings} injection tell(s) across {total} artifact(s) — review the diffs above.")
        sys.exit(1)


def cmd_eval(args: argparse.Namespace) -> None:
    """Measure retrieval quality against a labeled ground-truth set.
    Reports MRR@k and hit-rate; fails (exit 1) if below the given floors."""
    data_path = (
        Path(args.data) if args.data
        else Path(__file__).resolve().parent.parent / "tests" / "ground_truth.json"
    )
    if not data_path.exists():
        print(f"Ground-truth file not found: {data_path}", file=sys.stderr)
        sys.exit(2)
    queries = json.loads(data_path.read_text(encoding="utf-8")).get("queries", [])
    if not queries:
        print("No queries in ground-truth file.", file=sys.stderr)
        sys.exit(2)

    wl = Clawness(Path(args.rules_dir), top_k=args.top_k)
    k = args.top_k
    rr_sum = 0.0
    hits = 0
    misses: list[tuple[str, list[str], list[str]]] = []

    for entry in queries:
        q = entry["q"]
        expect = set(entry.get("expect", []))
        ids = wl.rank_ids(q, top_k=k)
        rank = next((i + 1 for i, rid in enumerate(ids) if rid in expect), None)
        if rank:
            rr_sum += 1.0 / rank
            hits += 1
        else:
            misses.append((q, sorted(expect), ids))

    n = len(queries)
    mrr, hit_rate = rr_sum / n, hits / n
    print(f"Eval: {n} queries  |  top-k={k}")
    print(f"  MRR@{k}    : {mrr:.3f}")
    print(f"  hit-rate  : {hit_rate:.3f}  ({hits}/{n})")
    if misses:
        print(f"\n  {len(misses)} miss(es):")
        for q, expect, ids in misses:
            print(f"    - \"{q}\"")
            print(f"        expected one of {expect}; got {ids}")

    failed = False
    if args.floor_mrr is not None and mrr < args.floor_mrr:
        print(f"\nFAIL: MRR@{k} {mrr:.3f} < floor {args.floor_mrr}", file=sys.stderr)
        failed = True
    if args.floor_hit is not None and hit_rate < args.floor_hit:
        print(f"FAIL: hit-rate {hit_rate:.3f} < floor {args.floor_hit}", file=sys.stderr)
        failed = True
    if failed:
        sys.exit(1)


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
    p_query.add_argument("--top-k", "-k", type=int, default=5)
    p_query.add_argument("--budget", "-b", type=int, default=4000, help="Token budget")
    p_query.add_argument(
        "--stack", default=None,
        help="Comma-separated detected stack (e.g. python,science). Off-stack "
             "language/framework rules then face the higher relevance floor, "
             "matching what the hook does. Omit to disable the penalty.",
    )

    # stats
    sub.add_parser("stats", help="Show corpus statistics")

    # lint
    sub.add_parser("lint", help="Validate rule files")

    # bench
    sub.add_parser("bench", help="Benchmark retrieval latency")

    # eval
    p_eval = sub.add_parser("eval", help="Measure retrieval quality (MRR@k + hit-rate)")
    p_eval.add_argument("--data", default=None, help="Path to ground_truth.json (default: bundled tests/)")
    p_eval.add_argument("--top-k", "-k", type=int, default=5)
    p_eval.add_argument("--floor-mrr", type=float, default=None, help="Fail if MRR below this")
    p_eval.add_argument("--floor-hit", type=float, default=None, help="Fail if hit-rate below this")

    # audit-skills (TOFU integrity: scan context-injected artifacts)
    p_audit = sub.add_parser(
        "audit-skills",
        help="Audit skills/agents/commands/MCP for injection tells + print fingerprints",
    )
    p_audit.add_argument("--project", default=".", help="Project directory (default: cwd)")

    # init
    p_init = sub.add_parser("init", help="Scan project and suggest rule domains")
    p_init.add_argument("project_dir", nargs="?", default=".", help="Project directory to scan")
    p_init.add_argument("--write", action="store_true", help="Write starter rule to disk")

    # plan (process-keeper gate; ON by default, cleared via native plan mode)
    p_plan = sub.add_parser("plan", help="Show plan gate status (the gate is on by default)")
    p_plan.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["show", "status"],
        help="status (the only action — the gate has no per-project switches)",
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
        {
            "query": cmd_query,
            "stats": cmd_stats,
            "lint": cmd_lint,
            "bench": cmd_bench,
            "eval": cmd_eval,
            "audit-skills": cmd_audit_skills,
        }[args.command](args)


if __name__ == "__main__":
    main()
