"""Tests for `clawness audit-rules` (clawness/cli.py cmd_audit_rules).

Driven as a subprocess like tests/test_cli.py, because `--strict`'s non-zero exit
is the whole reason the flag exists and is invisible when you call the function
directly.

Unlike `lint`, every check here is a judgment call dressed as a number, so the
command is report-only by default. These tests pin that: findings alone must not
fail the process.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "clawness.cli", *args],
        capture_output=True, text=True, encoding="utf-8", cwd=REPO, env=env,
    )


_RULE = """\
id: {id}
domain: general
severity: warning
tags: [{tags}]
triggers: [{triggers}]
when: {when}
rule: {rule}
"""


def _corpus(tmp_path: Path, extra: str = "") -> Path:
    """Two rules with nothing in common, so overlap is zero unless a test says so."""
    rules = tmp_path / "rules"
    (rules / "general").mkdir(parents=True)
    (rules / "general" / "GEN-KUBE-001.yml").write_text(
        _RULE.format(id="GEN-KUBE-001", tags="kubernetes, scheduling",
                     triggers="kubernetes, pod, affinity",
                     when="Scheduling kubernetes pods.",
                     rule="Set pod affinity and resource requests explicitly.") + extra,
        encoding="utf-8",
    )
    (rules / "general" / "GEN-INVOICE-001.yml").write_text(
        _RULE.format(id="GEN-INVOICE-001", tags="invoice, billing",
                     triggers="invoice, billing, vat",
                     when="Generating a customer invoice.",
                     rule="Round invoice totals once, at the end, to two decimals."),
        encoding="utf-8",
    )
    return rules


def _ground_truth(tmp_path: Path, expect: list[str]) -> Path:
    path = tmp_path / "gt.json"
    path.write_text(json.dumps({"queries": [{"q": "kubernetes pods", "expect": expect}]}),
                    encoding="utf-8")
    return path


# ── Exit codes ────────────────────────────────────────────────────

def test_findings_alone_do_not_fail(tmp_path):
    r = _cli("--rules-dir", str(_corpus(tmp_path)), "audit-rules", "--stale")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "finding(s)" in r.stdout


def test_strict_fails_when_there_are_findings(tmp_path):
    r = _cli("--rules-dir", str(_corpus(tmp_path)), "audit-rules", "--stale", "--strict")
    assert r.returncode == 1, r.stdout


def test_strict_passes_when_there_are_none(tmp_path):
    rules = _corpus(tmp_path)
    gt = _ground_truth(tmp_path, ["GEN-KUBE-001", "GEN-INVOICE-001"])
    r = _cli("--rules-dir", str(rules), "audit-rules", "--coverage",
             "--data", str(gt), "--strict")
    assert r.returncode == 0, r.stdout


# ── stale ─────────────────────────────────────────────────────────

def test_unstamped_rules_are_rolled_up_per_domain(tmp_path):
    r = _cli("--rules-dir", str(_corpus(tmp_path)), "audit-rules", "--stale")
    assert "2 rule(s) carry no 'applies_to'" in r.stdout
    assert "general: 2" in r.stdout


def test_the_age_check_is_skipped_without_max_age(tmp_path):
    """No default: there is no review-cadence data to derive one from, and an
    invented number gets argued with instead of acted on."""
    stamp = ('applies_to: {"Next.js": "15"}\nverified: "2020-01"\n'
             'sources: ["https://nextjs.org/docs"]\n')
    r = _cli("--rules-dir", str(_corpus(tmp_path, stamp)), "audit-rules", "--stale")
    assert "age check skipped" in r.stdout
    assert "months ago" not in r.stdout


def test_an_aged_stamp_is_reported_when_max_age_is_given(tmp_path):
    stamp = ('applies_to: {"Next.js": "15"}\nverified: "2020-01"\n'
             'sources: ["https://nextjs.org/docs"]\n')
    r = _cli("--rules-dir", str(_corpus(tmp_path, stamp)),
             "audit-rules", "--stale", "--max-age", "12")
    assert "GEN-KUBE-001: verified 2020-01" in r.stdout


def test_a_recent_stamp_is_not_reported_as_aged(tmp_path):
    from datetime import datetime
    stamp = (f'applies_to: {{"Next.js": "15"}}\nverified: "{datetime.now():%Y-%m}"\n'
             'sources: ["https://nextjs.org/docs"]\n')
    r = _cli("--rules-dir", str(_corpus(tmp_path, stamp)),
             "audit-rules", "--stale", "--max-age", "12")
    assert "months ago" not in r.stdout


def test_a_range_wider_than_its_evidence_is_reported(tmp_path):
    """The '13-17 slammed on everything' shape: five majors, one source."""
    stamp = ('applies_to: {"Next.js": "13-17"}\nverified: "2026-08"\n'
             'sources: ["https://nextjs.org/docs"]\n')
    r = _cli("--rules-dir", str(_corpus(tmp_path, stamp)), "audit-rules", "--stale")
    assert "spans 5 majors" in r.stdout


def test_a_single_major_on_one_source_is_not_reported(tmp_path):
    stamp = ('applies_to: {"Next.js": "17"}\nverified: "2026-08"\n'
             'sources: ["https://nextjs.org/docs"]\n')
    r = _cli("--rules-dir", str(_corpus(tmp_path, stamp)), "audit-rules", "--stale")
    assert "spans" not in r.stdout


# ── coverage ──────────────────────────────────────────────────────

def test_rules_in_no_eval_query_are_listed(tmp_path):
    rules = _corpus(tmp_path)
    gt = _ground_truth(tmp_path, ["GEN-KUBE-001"])
    r = _cli("--rules-dir", str(rules), "audit-rules", "--coverage", "--data", str(gt))
    assert "1 of 2 ranked rules are in no eval query" in r.stdout
    assert "GEN-INVOICE-001" in r.stdout


def test_full_coverage_says_so(tmp_path):
    rules = _corpus(tmp_path)
    gt = _ground_truth(tmp_path, ["GEN-KUBE-001", "GEN-INVOICE-001"])
    r = _cli("--rules-dir", str(rules), "audit-rules", "--coverage", "--data", str(gt))
    assert "every ranked rule appears in at least one eval query" in r.stdout


def test_an_unreadable_ground_truth_is_a_finding_not_a_crash(tmp_path):
    rules = _corpus(tmp_path)
    r = _cli("--rules-dir", str(rules), "audit-rules", "--coverage",
             "--data", str(tmp_path / "nope.json"))
    assert r.returncode == 0
    assert "could not read" in r.stdout


# ── overlap ───────────────────────────────────────────────────────

def test_unrelated_rules_do_not_overlap(tmp_path):
    r = _cli("--rules-dir", str(_corpus(tmp_path)), "audit-rules", "--overlap")
    assert "no pairs above" in r.stdout


def test_near_duplicate_rules_are_paired(tmp_path):
    rules = _corpus(tmp_path)
    (rules / "general" / "GEN-KUBE-002.yml").write_text(
        _RULE.format(id="GEN-KUBE-002", tags="kubernetes, scheduling",
                     triggers="kubernetes, pod, affinity",
                     when="Scheduling kubernetes pods.",
                     rule="Set pod affinity and resource requests explicitly."),
        encoding="utf-8",
    )
    r = _cli("--rules-dir", str(rules), "audit-rules", "--overlap")
    assert "GEN-KUBE-001 <-> GEN-KUBE-002" in r.stdout


def test_the_threshold_is_honoured(tmp_path):
    rules = _corpus(tmp_path)
    (rules / "general" / "GEN-KUBE-002.yml").write_text(
        _RULE.format(id="GEN-KUBE-002", tags="kubernetes, scheduling",
                     triggers="kubernetes, pod, affinity",
                     when="Scheduling kubernetes pods.",
                     rule="Set pod affinity and resource requests explicitly."),
        encoding="utf-8",
    )
    r = _cli("--rules-dir", str(rules), "audit-rules",
             "--overlap", "--overlap-threshold", "0.99")
    assert "no pairs above 0.99" in r.stdout


# ── reachability ──────────────────────────────────────────────────

def test_reachable_rules_report_clean(tmp_path):
    r = _cli("--rules-dir", str(_corpus(tmp_path)), "audit-rules", "--reachability")
    assert "all 2 ranked rules retrieve on their own 'when'" in r.stdout


def test_the_real_corpus_is_fully_reachable():
    """Locks in a currently-healthy state: a rule its own `when` can't retrieve
    is unreachable by construction, since no user prompt will do better."""
    r = _cli("audit-rules", "--reachability", "--strict")
    assert r.returncode == 0, r.stdout


# ── dispatch ──────────────────────────────────────────────────────

def test_no_flags_runs_every_check(tmp_path):
    r = _cli("--rules-dir", str(_corpus(tmp_path)), "audit-rules")
    for section in ("[stale]", "[coverage]", "[overlap]", "[reachability]"):
        assert section in r.stdout
