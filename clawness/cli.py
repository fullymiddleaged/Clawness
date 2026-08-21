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
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from . import findings as findings_mod
from . import scan as scan_mod
from .core import Clawness, _estimate_tokens, load_rules
from .plan import find_project_root
from .staleness import WATCHED_LABELS, parse_range


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


_VERIFIED_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def _stamp_problems(r, raw: dict | None) -> list[str]:
    """Mechanical validation of a rule's version stamp (applies_to/verified/sources).

    Every check here exists because the failure it catches is **silent**. A stamp
    that can't be read doesn't raise and doesn't warn — it just never fires, so
    the rule looks reviewed and behaves exactly like an unreviewed one. Judgment
    (is this range *right*?) is not lintable and belongs to the review pass; what
    is lintable is whether the stamp can work at all.
    """
    problems: list[str] = []
    raw_applies = (raw or {}).get("applies_to")

    # The loader coerces an unusable applies_to to {} so the prompt hook can't
    # crash on one. That silence is correct at runtime and wrong here.
    if raw_applies is not None and not r.applies_to:
        problems.append(
            "'applies_to' is not a mapping of framework -> version range — "
            "it is being dropped, so this rule reads as unstamped"
        )

    for label, spec in r.applies_to.items():
        # The one that matters most: a label absent from VERSION_WATCH_* never
        # matches the detector's `versions` dict, so the check silently never
        # fires. A dead check that looks configured is worse than no check.
        if label not in WATCHED_LABELS:
            problems.append(
                f"'applies_to' names '{label}', which no detector emits — the "
                f"key must be a VERSION_WATCH label ({_nearest_label(label)})"
            )
        if parse_range(spec) is None:
            problems.append(
                f"'applies_to[{label}]' range '{spec}' is unparseable — use "
                "'13-15' or '15' (one or two numeric components per bound)"
            )

    if r.verified:
        if not _VERIFIED_RE.match(r.verified):
            problems.append(
                f"'verified' is '{r.verified}' — expected YYYY-MM (the month the "
                "rule was reviewed)"
            )
        elif r.verified[:7] > datetime.now().strftime("%Y-%m"):
            problems.append(f"'verified' date '{r.verified}' is in the future")

    # verified/sources qualify a range. Without one they describe nothing, and
    # the author probably believes the rule is armed when it is not.
    if (r.verified or r.sources) and not r.applies_to:
        problems.append(
            "'verified'/'sources' without 'applies_to' — there is no range for "
            "them to establish, and this rule arms no staleness warning"
        )

    return problems


def _nearest_label(label: str) -> str:
    """Best guess at what a mistyped framework label meant, for the lint message."""
    folded = label.replace(".", "").replace("-", "").lower()
    for known in sorted(WATCHED_LABELS):
        if known.replace(".", "").replace("-", "").lower() == folded:
            return f"did you mean '{known}'?"
    return "known labels: " + ", ".join(sorted(WATCHED_LABELS))


def cmd_lint(args: argparse.Namespace) -> None:
    rules_dir = Path(args.rules_dir)
    ranked, mandatory = load_rules(rules_dir)
    issues = 0
    # Raw YAML per file, kept so the stamp pass can see what the author WROTE
    # rather than what the loader could salvage — a dropped `applies_to` is
    # invisible in the Rule object, which is exactly the failure to report.
    raw_by_path: dict[str, dict] = {}

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
        else:
            raw_by_path[str(yml_path)] = parsed

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
        problems += _stamp_problems(r, raw_by_path.get(r.source_path))
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


# ---------------------------------------------------------------------------
# audit-rules — maintainer tooling for keeping the corpus honest
# ---------------------------------------------------------------------------
#
# Unlike `lint`, every check here is a JUDGMENT call dressed as a number: an
# overlapping pair may be two correct rules, an unstamped rule may simply not
# need a stamp, a "stale" verified date may be on a rule nothing has changed
# under. So this is report-only and non-zero exit is opt-in (`--strict`); CI
# gates `lint` and `eval`, not this.


def _months_between(older: str, newer: str) -> int | None:
    """Whole months from *older* to *newer*, both YYYY-MM(-DD), or None."""
    try:
        oy, om = int(older[:4]), int(older[5:7])
        ny, nm = int(newer[:4]), int(newer[5:7])
    except (ValueError, IndexError):
        return None
    return (ny - oy) * 12 + (nm - om)


def _audit_stale(rules: list, max_age: int | None) -> tuple[int, list[str]]:
    """Rules with no stamp, an aged stamp, or a stamp wider than its evidence."""
    lines: list[str] = []
    unstamped = [r for r in rules if not r.applies_to]
    if unstamped:
        by_domain: dict[str, int] = {}
        for r in unstamped:
            by_domain[r.domain] = by_domain.get(r.domain, 0) + 1
        lines.append(f"  {len(unstamped)} rule(s) carry no 'applies_to' — no version "
                     "provenance, so they can never be detected as stale:")
        for domain, count in sorted(by_domain.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {domain}: {count}")

    now = datetime.now().strftime("%Y-%m")
    for r in rules:
        if not r.applies_to:
            continue
        # A range spanning more majors than it cites sources for is the "13-17
        # slammed on everything" shape. Crude on purpose — it can't tell a
        # thorough single source from a lazy one, which is exactly why this
        # command reports rather than gates.
        for label, spec in r.applies_to.items():
            parsed = parse_range(spec)
            if parsed is None:
                continue
            majors = parsed[1][0] - parsed[0][0] + 1
            if majors > max(1, len(r.sources)):
                lines.append(
                    f"  {r.id}: '{label}' spans {majors} majors ({spec}) on "
                    f"{len(r.sources)} source(s) — widen only as far as evidence goes"
                )
        if max_age is None or not r.verified:
            continue
        age = _months_between(r.verified, now)
        if age is not None and age > max_age:
            lines.append(f"  {r.id}: verified {r.verified} ({age} months ago)")

    if max_age is None:
        lines.append("  (age check skipped — pass --max-age <months> to run it; "
                     "there is no default because there is no review-cadence data "
                     "to derive one from)")
    return len([ln for ln in lines if not ln.startswith("  (")]), lines


def _audit_coverage(ranked: list, data_path: Path) -> tuple[int, list[str]]:
    """Ranked rules that appear in no ground-truth `expect` list.

    A rule nothing measures can regress in retrieval without any signal — the
    eval floors stay green while the rule quietly stops surfacing.
    """
    try:
        queries = json.loads(data_path.read_text(encoding="utf-8")).get("queries", [])
    except (OSError, ValueError) as e:
        return 1, [f"  could not read {data_path}: {e}"]

    covered = {rid for q in queries for rid in q.get("expect", [])}
    missing = sorted(r.id for r in ranked if r.id not in covered)
    if not missing:
        return 0, ["  every ranked rule appears in at least one eval query"]
    lines = [f"  {len(missing)} of {len(ranked)} ranked rules are in no eval query:"]
    lines += [f"    {rid}" for rid in missing]
    return len(missing), lines


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    dot = sum(w * b[t] for t, w in a.items() if t in b)
    if dot == 0.0:
        return 0.0
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _audit_overlap(wl: Clawness, threshold: float) -> tuple[int, list[str]]:
    """Rule pairs similar enough that they compete for the same top-k slot.

    Both may be correct — the cost is that one slot restates the other. Uses the
    engine's own TF-IDF vectors, so "similar" means what the retriever means.
    """
    index = wl._tfidf
    if index is None or not index._doc_vectors:
        return 0, ["  no index"]
    rules = wl._ranked_rules
    pairs: list[tuple[float, str, str]] = []
    vectors = index._doc_vectors
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            score = _cosine(vectors[i], vectors[j])
            if score >= threshold:
                pairs.append((score, rules[i].id, rules[j].id))
    if not pairs:
        return 0, [f"  no pairs above {threshold:.2f}"]
    pairs.sort(reverse=True)
    lines = [f"  {len(pairs)} pair(s) at or above {threshold:.2f}:"]
    lines += [f"    {score:.3f}  {a} <-> {b}" for score, a, b in pairs]
    return len(pairs), lines


def _audit_reachability(wl: Clawness) -> tuple[int, list[str]]:
    """Rules their own `when` can't retrieve — unreachable by construction.

    The weakest possible bar: if a rule's own trigger description doesn't put it
    in the top 5, no user prompt will either.
    """
    unreachable = [
        r.id for r in wl._ranked_rules
        if r.when and r.id not in wl.rank_ids(r.when, top_k=5)
    ]
    if not unreachable:
        return 0, [f"  all {len(wl._ranked_rules)} ranked rules retrieve on their own 'when'"]
    lines = [f"  {len(unreachable)} rule(s) don't retrieve on their own 'when':"]
    lines += [f"    {rid}" for rid in unreachable]
    return len(unreachable), lines


def cmd_audit_rules(args: argparse.Namespace) -> None:
    rules_dir = Path(args.rules_dir)
    wl = Clawness(rules_dir)
    ranked, mandatory = wl._ranked_rules, wl._mandatory_rules

    # No check flags means run them all — the useful default for "how healthy is
    # this corpus?", which is the question the command exists to answer.
    selected = [args.stale, args.coverage, args.overlap, args.reachability]
    run_all = not any(selected)

    data_path = (
        Path(args.data) if args.data
        else Path(__file__).resolve().parent.parent / "tests" / "ground_truth.json"
    )

    checks = [
        ("stale", run_all or args.stale,
         lambda: _audit_stale(ranked + mandatory, args.max_age)),
        ("coverage", run_all or args.coverage,
         lambda: _audit_coverage(ranked, data_path)),
        ("overlap", run_all or args.overlap,
         lambda: _audit_overlap(wl, args.overlap_threshold)),
        ("reachability", run_all or args.reachability,
         lambda: _audit_reachability(wl)),
    ]

    findings = 0
    for name, enabled, run in checks:
        if not enabled:
            continue
        count, lines = run()
        findings += count
        print(f"\n[{name}]")
        for line in lines:
            print(line)

    print(f"\n{findings} finding(s) across {len(ranked) + len(mandatory)} rules.")
    if findings and args.strict:
        sys.exit(1)


# Statuses that still demand attention for the opt-in --fail-on gate: a
# false-positive or fixed finding is resolved and must not fail CI.
_UNRESOLVED = {findings_mod.STATUS_NEW, findings_mod.STATUS_REVIEWED, findings_mod.STATUS_CONFIRMED}


def _scan_status_label(entry: dict) -> str:
    st = entry.get("status", "")
    v = entry.get("verdict") or ""
    return f"{st}" + (f" — {v}" if v else "")


def _print_coverage(cov: dict) -> None:
    if cov["converged"]:
        tail = "converged — nothing outstanding" if cov["live"] else "no candidates"
    else:
        tail = f"{cov['outstanding']} outstanding"
    print(
        f"\nCoverage: {cov['adjudicated']}/{cov['live']} adjudicated "
        f"({cov['pct']}%) — {tail}"
    )
    if cov["confirmed"] or cov["false_positive"] or cov["fixed"] or cov["gone"]:
        print(
            f"  confirmed={cov['confirmed']} "
            f"false-positive={cov['false_positive']} "
            f"fixed={cov['fixed']} gone={cov['gone']}"
        )


def cmd_scan(args: argparse.Namespace) -> None:
    root = find_project_root(Path(args.project).resolve())

    # `scan --set <id> <status>` records an adjudication (what the audit agents
    # call to write a verdict back). Never enumerates; a bad id/status exits 1.
    if args.set:
        finding_id, status = args.set
        ledger = findings_mod.load_findings(root)
        try:
            ledger = findings_mod.set_verdict(
                ledger, finding_id, status,
                verdict=args.verdict, severity=args.severity, notes=args.notes,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        findings_mod.save_findings(root, ledger)
        print(f"recorded {status} on {finding_id}")
        return

    if scan_mod.scan_disabled():
        print("clawness scan is disabled (CLAW_NO_SCAN is set).", file=sys.stderr)
        return

    # `scan status` reports the stored ledger without re-enumerating.
    if args.action == "status":
        ledger = findings_mod.load_findings(root)
        cov = findings_mod.coverage(ledger)
        if not ledger:
            print(f"No findings ledger yet for {root} — run `clawness scan` first.")
            return
        print(f"[clawness scan status] {root}")
        for cid, entry in sorted(
            ledger.items(), key=lambda kv: (kv[1].get("file", ""), kv[1].get("line", 0))
        ):
            print(
                f"  {entry.get('file')}:{entry.get('line')}  "
                f"{entry.get('severity')}/{entry.get('confidence')}  "
                f"{entry.get('class')} ({entry.get('cwe')})  [{_scan_status_label(entry)}]  {cid}"
            )
        _print_coverage(cov)
        return

    sarif_arg = [args.sarif] if args.sarif else None
    candidates = scan_mod.enumerate_candidates(root, sarif=sarif_arg)
    cov_map = scan_mod.coverage_map(root)
    sarif_n = sum(1 for c in candidates if c.get("source") == "sarif")

    # Persist: merge this scan into the accumulating ledger, then report.
    ledger = findings_mod.merge_scan(candidates, findings_mod.load_findings(root))
    findings_mod.save_findings(root, ledger)
    cov = findings_mod.coverage(ledger)

    # Build the view (a filtered slice of THIS scan's candidates).
    view = candidates
    if args.klass:
        view = [c for c in view if c["class"] == args.klass]
    if args.new_only:
        new_ids = {c["id"] for c in findings_mod.outstanding(ledger)}
        view = [c for c in view if c["id"] in new_ids]

    if args.json:
        print(json.dumps({
            "candidates": view,
            "scan_coverage": cov_map,
            "ledger_coverage": cov,
        }, indent=2, sort_keys=True))
    else:
        print(f"[clawness scan] {root}")
        print(
            f"  {len(candidates)} candidate(s) across "
            f"{len({c['class'] for c in candidates})} class(es), "
            f"{cov_map['files_scanned']} file(s) scanned"
            + (f"  (+{sarif_n} from SARIF)" if sarif_n else "")
        )
        if args.new_only or args.klass:
            print(f"  showing {len(view)} "
                  f"({'new only' if args.new_only else ''}"
                  f"{', ' if args.new_only and args.klass else ''}"
                  f"{('class=' + args.klass) if args.klass else ''})")
        for c in view:
            print(
                f"  {c['file']}:{c['line']}  {c['severity']}/{c['confidence']}  "
                f"{c['class']} ({c['cwe']})"
                + (f"  → {c['rule']}" if c["rule"] else "")
            )
        _print_coverage(cov)
        print("\nTripwire, not a SAST engine — adjudicate these, don't trust them "
              "blindly. Verdicts persist in .clawness/security/findings.json.")

    # Opt-in CI gate: fail on any UNRESOLVED finding at/above the floor severity.
    if args.fail_on:
        blocking = [
            e for e in ledger.values()
            if e.get("status") in _UNRESOLVED
            and scan_mod.severity_at_least(e.get("severity", "low"), args.fail_on)
        ]
        if blocking:
            print(
                f"\n{len(blocking)} unresolved finding(s) at or above "
                f"'{args.fail_on}' — failing (--fail-on).",
                file=sys.stderr,
            )
            sys.exit(1)


def main() -> None:
    # The corpus + CLI output use em-dashes/arrows; bare stdout defaults to cp1252
    # on Windows and raises UnicodeEncodeError on them. Pin UTF-8 where possible
    # (guarded: a captured/replaced stream may not expose reconfigure).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

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

    # audit-rules (corpus health: provenance, eval coverage, overlap, reachability)
    p_audit_rules = sub.add_parser(
        "audit-rules",
        help="Report corpus health: version provenance, eval coverage, "
             "near-duplicate rules, unreachable rules",
    )
    p_audit_rules.add_argument("--stale", action="store_true",
                               help="Rules with no/aged/over-wide version stamps")
    p_audit_rules.add_argument("--coverage", action="store_true",
                               help="Ranked rules in no ground-truth query")
    p_audit_rules.add_argument("--overlap", action="store_true",
                               help="Rule pairs competing for the same top-k slot")
    p_audit_rules.add_argument("--reachability", action="store_true",
                               help="Rules their own 'when' can't retrieve")
    p_audit_rules.add_argument(
        "--max-age", type=int, default=None,
        help="Months after which a 'verified' date counts as aged. No default: "
             "there is no review-cadence data to derive one from, and an invented "
             "number would be argued with instead of acted on.",
    )
    p_audit_rules.add_argument("--overlap-threshold", type=float, default=0.30,
                               help="Cosine at or above which a pair is reported "
                                    "(default 0.30)")
    p_audit_rules.add_argument("--data", default=None,
                               help="Path to ground_truth.json (default: bundled tests/)")
    p_audit_rules.add_argument("--strict", action="store_true",
                               help="Exit non-zero if anything is reported")

    # audit-skills (TOFU integrity: scan context-injected artifacts)
    p_audit = sub.add_parser(
        "audit-skills",
        help="Audit skills/agents/commands/MCP for injection tells + print fingerprints",
    )
    p_audit.add_argument("--project", default=".", help="Project directory (default: cwd)")

    # scan (deterministic attack-surface enumerator + accumulating findings ledger)
    p_scan = sub.add_parser(
        "scan",
        help="Enumerate security candidates deterministically and accumulate a "
             "findings ledger (report-only; --fail-on opts into a CI gate)",
    )
    p_scan.add_argument(
        "action", nargs="?", default="scan", choices=["scan", "status"],
        help="scan (enumerate + merge, the default) or status (show the ledger "
             "without re-scanning)",
    )
    p_scan.add_argument("--project", default=".", help="Project directory (default: cwd)")
    p_scan.add_argument("--json", action="store_true", help="Machine-readable output")
    p_scan.add_argument("--new-only", action="store_true",
                        help="Show only candidates still awaiting adjudication")
    p_scan.add_argument("--class", dest="klass", default=None,
                        help="Filter to one candidate class (e.g. sql-injection)")
    p_scan.add_argument("--sarif", default=None, metavar="PATH",
                        help="Ingest a .sarif file or directory of SAST output "
                             "(bandit/semgrep/CodeQL); by default any *.sarif under "
                             "the project is auto-detected and folded in")
    p_scan.add_argument("--fail-on", default=None,
                        choices=["low", "medium", "high", "critical"],
                        help="Opt-in CI gate: exit non-zero if any UNRESOLVED "
                             "finding is at or above this severity")
    p_scan.add_argument("--set", nargs=2, metavar=("ID", "STATUS"), default=None,
                        help="Record a verdict on a finding (what the audit agents "
                             "call): STATUS in new/reviewed/confirmed/false-positive/"
                             "fixed/gone")
    p_scan.add_argument("--verdict", default=None, help="Verdict text for --set")
    p_scan.add_argument("--severity", default=None, help="Override severity for --set")
    p_scan.add_argument("--notes", default=None, help="Notes for --set")

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
    elif args.command == "scan":
        cmd_scan(args)
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
            "audit-rules": cmd_audit_rules,
            "audit-skills": cmd_audit_skills,
        }[args.command](args)


if __name__ == "__main__":
    main()
