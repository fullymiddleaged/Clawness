"""Tests for the findings ledger (clawness/findings.py).

Fail-first (TST-FAILFIRST-001): `test_rescan_preserves_false_positive` is the
load-bearing one — invert the `status in ADJUDICATED` guard in merge_scan and it
goes red, proving the ledger really refuses to re-open a judged finding.
"""

from __future__ import annotations

import json

import pytest

from clawness import findings as F


def _cand(cid, cls="sql-injection", file="a.py", line=1):
    return {
        "id": cid, "file": file, "line": line, "class": cls,
        "cwe": "CWE-89", "rule": "SEC-SQLI-001", "severity": "critical",
        "confidence": "high", "snippet": "cur.execute(f'...')",
    }


def test_merge_adds_new_as_status_new():
    led = F.merge_scan([_cand("aaa"), _cand("bbb")], {})
    assert set(led) == {"aaa", "bbb"}
    assert led["aaa"]["status"] == F.STATUS_NEW
    assert led["aaa"]["first_seen"] == led["aaa"]["last_seen"]


def test_merge_marks_disappeared_gone():
    led = F.merge_scan([_cand("aaa")], {})
    led2 = F.merge_scan([], led)              # sink removed from code
    assert led2["aaa"]["status"] == F.STATUS_GONE


def test_rescan_preserves_false_positive():
    led = F.merge_scan([_cand("aaa")], {})
    led = F.set_verdict(led, "aaa", F.STATUS_FALSE_POSITIVE, verdict="parameterised, safe")
    led = F.merge_scan([_cand("aaa")], led)   # same sink still present
    assert led["aaa"]["status"] == F.STATUS_FALSE_POSITIVE
    assert led["aaa"]["verdict"] == "parameterised, safe"


def test_gone_then_reappears_restores_adjudication():
    led = F.merge_scan([_cand("aaa")], {})
    led = F.set_verdict(led, "aaa", F.STATUS_CONFIRMED)
    led = F.merge_scan([], led)               # disappears
    assert led["aaa"]["status"] == F.STATUS_GONE
    led = F.merge_scan([_cand("aaa")], led)   # comes back
    assert led["aaa"]["status"] == F.STATUS_CONFIRMED


def test_gone_then_reappears_unjudged_is_new():
    led = F.merge_scan([_cand("aaa")], {})
    led = F.merge_scan([], led)
    led = F.merge_scan([_cand("aaa")], led)
    assert led["aaa"]["status"] == F.STATUS_NEW


def test_merge_refreshes_last_seen(monkeypatch):
    led = F.merge_scan([_cand("aaa")], {}, now=100.0)
    led = F.merge_scan([_cand("aaa")], led, now=200.0)
    assert led["aaa"]["first_seen"] == 100.0
    assert led["aaa"]["last_seen"] == 200.0


def test_set_verdict_rejects_bad_status():
    led = F.merge_scan([_cand("aaa")], {})
    with pytest.raises(ValueError):
        F.set_verdict(led, "aaa", "totally-bogus")


def test_set_verdict_rejects_unknown_id():
    with pytest.raises(ValueError):
        F.set_verdict({}, "nope", F.STATUS_CONFIRMED)


def test_set_verdict_does_not_mutate_input():
    led = F.merge_scan([_cand("aaa")], {})
    led2 = F.set_verdict(led, "aaa", F.STATUS_CONFIRMED)
    assert led["aaa"]["status"] == F.STATUS_NEW      # original untouched
    assert led2["aaa"]["status"] == F.STATUS_CONFIRMED


def test_outstanding_only_new_sorted():
    led = F.merge_scan([_cand("a", file="z.py", line=2),
                        _cand("b", file="a.py", line=9),
                        _cand("c", file="a.py", line=1)], {})
    led = F.set_verdict(led, "a", F.STATUS_CONFIRMED)
    out = F.outstanding(led)
    assert [c["id"] for c in out] == ["c", "b"]       # a is judged; c<b by line
    assert all(c["status"] == F.STATUS_NEW for c in out)


def test_coverage_and_convergence():
    led = F.merge_scan([_cand("a"), _cand("b"), _cand("c")], {})
    cov = F.coverage(led)
    assert cov["live"] == 3 and cov["outstanding"] == 3 and not cov["converged"]
    assert cov["pct"] == 0.0
    led = F.set_verdict(led, "a", F.STATUS_CONFIRMED)
    led = F.set_verdict(led, "b", F.STATUS_FALSE_POSITIVE)
    led = F.set_verdict(led, "c", F.STATUS_FIXED)
    cov = F.coverage(led)
    assert cov["converged"] and cov["pct"] == 100.0
    assert cov["confirmed"] == 1 and cov["false_positive"] == 1 and cov["fixed"] == 1


def test_coverage_excludes_gone_from_live():
    led = F.merge_scan([_cand("a"), _cand("b")], {})
    led = F.merge_scan([_cand("a")], led)             # b disappears
    cov = F.coverage(led)
    assert cov["gone"] == 1
    assert cov["live"] == 1


def test_empty_ledger_is_converged():
    cov = F.coverage({})
    assert cov["converged"] and cov["pct"] == 100.0 and cov["live"] == 0


def test_save_load_roundtrip(tmp_path):
    led = F.merge_scan([_cand("aaa")], {})
    led = F.set_verdict(led, "aaa", F.STATUS_CONFIRMED, notes="exploitable via /q")
    F.save_findings(tmp_path, led)
    assert F.findings_path(tmp_path).exists()
    loaded = F.load_findings(tmp_path)
    assert loaded["aaa"]["status"] == F.STATUS_CONFIRMED
    assert loaded["aaa"]["notes"] == "exploitable via /q"


def test_load_tolerates_garbage(tmp_path):
    p = F.findings_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not json", encoding="utf-8")
    assert F.load_findings(tmp_path) == {}


def test_load_missing_returns_empty(tmp_path):
    assert F.load_findings(tmp_path) == {}


def test_load_accepts_wrapped_shape(tmp_path):
    p = F.findings_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"findings": {"x": _cand("x") | {"status": "new"}}}), encoding="utf-8")
    led = F.load_findings(tmp_path)
    assert "x" in led
