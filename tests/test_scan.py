"""Tests for the deterministic attack-surface enumerator (clawness/scan.py).

Fail-first (TST-FAILFIRST-001): these were watched red before green — e.g.
`test_clean_file_yields_nothing` fails the moment the `os.environ`/parameterised
forms in clean.py are (wrongly) flagged, and `test_ids_stable_across_runs` fails
if candidate_id stops folding whitespace.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from clawness import scan

FIXTURES = Path(__file__).parent / "fixtures" / "vuln"


@pytest.fixture(autouse=True)
def _clear_disable(monkeypatch):
    monkeypatch.delenv("CLAW_NO_SCAN", raising=False)


def _by_file(cands, name):
    return [c for c in cands if c["file"] == name]


def test_finds_each_planted_class():
    cands = scan.enumerate_candidates(FIXTURES)
    found = {c["class"] for c in cands}
    # Every class planted across app.py + ui.jsx must surface.
    expected = {
        "sql-injection", "command-injection", "unsafe-deserialization",
        "code-eval", "xss", "path-traversal", "hardcoded-secret",
        "weak-crypto", "ssrf",
    }
    missing = expected - found
    assert not missing, f"enumerator missed classes: {sorted(missing)}"


# Per-language planted classes. Each app.* fixture deliberately contains one sink
# per class its ecosystem can express (Go has no eval/deserialization idiom the
# enumerator models, hence a shorter set). Watched red before green by neutering
# the relevant _p(...) pattern in scan.py (TST-FAILFIRST-001).
_PLANTED_BY_FILE = {
    "app.py": {
        "sql-injection", "command-injection", "unsafe-deserialization", "code-eval",
        "path-traversal", "hardcoded-secret", "weak-crypto", "ssrf",
    },
    "ui.jsx": {"xss", "code-eval", "sql-injection", "weak-crypto", "hardcoded-secret"},
    "app.go": {
        "sql-injection", "command-injection", "xss", "path-traversal",
        "weak-crypto", "ssrf", "hardcoded-secret",
    },
    "app.rb": {
        "sql-injection", "command-injection", "unsafe-deserialization", "code-eval",
        "xss", "path-traversal", "weak-crypto", "ssrf", "hardcoded-secret",
    },
    "App.java": {
        "sql-injection", "command-injection", "unsafe-deserialization", "code-eval",
        "xss", "path-traversal", "weak-crypto", "ssrf", "hardcoded-secret",
    },
    "App.cs": {
        "sql-injection", "command-injection", "unsafe-deserialization", "code-eval",
        "xss", "path-traversal", "weak-crypto", "ssrf", "hardcoded-secret",
    },
    "app.php": {
        "sql-injection", "command-injection", "unsafe-deserialization", "code-eval",
        "xss", "path-traversal", "weak-crypto", "ssrf", "hardcoded-secret",
    },
}

_CLEAN_FILES = ["clean.py", "clean.go", "clean.rb", "Clean.java", "Clean.cs", "clean.php"]


@pytest.mark.parametrize("fixture, expected", sorted(_PLANTED_BY_FILE.items()))
def test_planted_classes_found_per_language(fixture, expected):
    cands = scan.enumerate_candidates(FIXTURES)
    found = {c["class"] for c in _by_file(cands, fixture)}
    missing = expected - found
    assert not missing, f"{fixture}: enumerator missed {sorted(missing)}"


@pytest.mark.parametrize("clean", _CLEAN_FILES)
def test_clean_file_yields_nothing(clean):
    cands = scan.enumerate_candidates(FIXTURES)
    hits = _by_file(cands, clean)
    assert hits == [], f"{clean} must not produce candidates, got {[(c['line'], c['class']) for c in hits]}"


def test_every_candidate_has_required_shape():
    for c in scan.enumerate_candidates(FIXTURES):
        for field in ("id", "file", "line", "class", "cwe", "rule", "severity", "confidence", "snippet"):
            assert field in c, f"candidate missing {field}: {c}"
        assert c["class"] in scan.CLASS_META
        assert len(c["id"]) == 16


def test_ids_stable_across_runs():
    a = scan.enumerate_candidates(FIXTURES)
    b = scan.enumerate_candidates(FIXTURES)
    assert [c["id"] for c in a] == [c["id"] for c in b]


def test_ordering_deterministic():
    a = scan.enumerate_candidates(FIXTURES)
    b = scan.enumerate_candidates(FIXTURES)
    keys_a = [(c["file"], c["line"], c["class"]) for c in a]
    keys_b = [(c["file"], c["line"], c["class"]) for c in b]
    assert keys_a == keys_b
    assert keys_a == sorted(keys_a), "output must be sorted by (file, line, class)"


def test_ids_unique():
    cands = scan.enumerate_candidates(FIXTURES)
    ids = [c["id"] for c in cands]
    assert len(ids) == len(set(ids))


def test_candidate_id_is_whitespace_insensitive():
    a = scan.candidate_id("a.py", 3, "code-eval", "return   eval( x )")
    b = scan.candidate_id("a.py", 3, "code-eval", "return eval( x )")
    assert a == b


def test_candidate_id_changes_with_line_and_class():
    base = scan.candidate_id("a.py", 3, "code-eval", "eval(x)")
    assert scan.candidate_id("a.py", 4, "code-eval", "eval(x)") != base
    assert scan.candidate_id("a.py", 3, "ssrf", "eval(x)") != base
    assert scan.candidate_id("b.py", 3, "code-eval", "eval(x)") != base


def test_disabled_returns_empty(monkeypatch):
    monkeypatch.setenv("CLAW_NO_SCAN", "1")
    assert scan.enumerate_candidates(FIXTURES) == []
    assert scan.scan_disabled() is True


def test_missing_root_returns_empty(tmp_path):
    assert scan.enumerate_candidates(tmp_path / "does-not-exist") == []


def test_skip_dirs_are_not_scanned(tmp_path):
    vuln = "os.system('rm ' + x)\n"
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ok.py").write_text(vuln, encoding="utf-8")
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "bad.py").write_text(vuln, encoding="utf-8")
    files = {c["file"] for c in scan.enumerate_candidates(tmp_path)}
    assert "src/ok.py" in files
    assert not any("node_modules" in f for f in files)


def test_secret_reference_not_flagged(tmp_path):
    (tmp_path / "c.py").write_text(
        'api_key = os.environ["API_KEY"]\n'
        'secret = "changeme-please"\n',      # placeholder → skipped
        encoding="utf-8",
    )
    assert scan.enumerate_candidates(tmp_path) == []


def test_hardcoded_secret_literal_is_flagged(tmp_path):
    (tmp_path / "c.py").write_text('api_key = "a1b2c3d4e5f6g7h8"\n', encoding="utf-8")
    cands = scan.enumerate_candidates(tmp_path)
    assert any(c["class"] == "hardcoded-secret" for c in cands)


def test_coverage_map_counts_files():
    cov = scan.coverage_map(FIXTURES)
    assert cov["files_scanned"] >= 3
    assert set(cov["classes"]) == set(scan.CLASS_META)


def test_severity_at_least():
    assert scan.severity_at_least("critical", "high")
    assert scan.severity_at_least("high", "high")
    assert not scan.severity_at_least("medium", "high")
    assert not scan.severity_at_least("low", "critical")
