"""
Tests for SAST readiness (clawness/sast.py) and its suggested-action line in
hooks/claude_hook.py.

Runs under pytest, or standalone:  python tests/test_sast.py
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from clawness import sast  # noqa: E402


def _which(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def _project() -> Path:
    d = Path(tempfile.mkdtemp())
    (d / ".git").mkdir()
    return d


def _hook():
    spec = importlib.util.spec_from_file_location("claude_hook_sast", REPO / "hooks" / "claude_hook.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- detection -------------------------------------------------------------

def test_installed_scanners_reports_only_what_is_on_path_in_order():
    assert sast.installed_scanners(_which()) == []
    assert sast.installed_scanners(_which("trivy", "semgrep")) == ["semgrep", "trivy"]


def test_a_failing_which_is_skipped_not_fatal():
    def boom(name):
        raise OSError("nope")
    assert sast.installed_scanners(boom) == []


# --- the once-per-project offer --------------------------------------------

def test_offer_fires_once_per_project():
    root = _project()
    assert sast.should_offer(root) is True
    assert sast.should_offer(root) is False
    assert json.loads((root / ".clawness" / "sast.json").read_text(encoding="utf-8")) == {"offered": True}


def test_corrupt_ledger_offers_again_rather_than_never():
    root = _project()
    (root / ".clawness").mkdir()
    (root / ".clawness" / "sast.json").write_text("{ broken", encoding="utf-8")
    assert sast.should_offer(root) is True
    assert sast.should_offer(root) is False


def test_outside_a_project_it_offers_without_writing_a_ledger():
    bare = Path(tempfile.mkdtemp())
    assert sast.should_offer(bare) is True
    assert not (bare / ".clawness").exists()


def test_installed_scanner_never_spends_the_offer():
    root = _project()
    line = sast.sast_line(str(root), _which("semgrep"))
    assert "semgrep" in line
    assert not (root / ".clawness" / "sast.json").exists()


# --- the line ----------------------------------------------------------------

def test_line_names_installed_scanners():
    line = sast.render_line(["semgrep", "gitleaks"], offer=False)
    assert "semgrep, gitleaks" in line and "/clawness:security-audit" in line


def test_offer_line_names_one_install_and_the_fallback():
    line = sast.render_line([], offer=True)
    assert "semgrep" in line
    assert "decline" in line and "built-in scan" in line   # the fallback is always named
    assert "once" in line


def test_after_the_offer_the_line_only_names_the_fallback():
    line = sast.render_line([], offer=False)
    assert "already offered" in line and "uv tool install" not in line


# --- hook wiring -----------------------------------------------------------

def test_scan_prompts_trigger_the_line(monkeypatch):
    hook = _hook()
    monkeypatch.setattr(sast.shutil, "which", _which())
    monkeypatch.delenv("CLAW_NO_SAST_OFFER", raising=False)
    root = _project()
    out = hook.suggest_actions("run a SAST scan on this repo", str(root))
    assert "/clawness:security-audit" in out and "No SAST scanner is installed" in out
    # Second security prompt in the same project: the fallback, not the offer.
    again = hook.suggest_actions("do a security scan of the api", str(root))
    assert "already offered" in again


def test_sast_needs_a_word_boundary():
    hook = _hook()
    assert hook.suggest_actions("recover from this disaster gracefully", "") == ""


def test_offer_can_be_silenced(monkeypatch):
    hook = _hook()
    monkeypatch.setattr(sast.shutil, "which", _which())
    monkeypatch.setenv("CLAW_NO_SAST_OFFER", "1")
    out = hook.suggest_actions("run semgrep please", str(_project()))
    assert "/clawness:security-audit" in out and "SAST scanner" not in out


def test_diff_scoped_security_prompts_name_the_builtin_review(monkeypatch):
    hook = _hook()
    monkeypatch.setenv("CLAW_NO_SAST_OFFER", "1")
    out = hook.suggest_actions("is this change secure?", "")
    assert "/security-review" in out and "/clawness:security-audit" in out


def test_whole_repo_audits_do_not_name_the_builtin_review(monkeypatch):
    hook = _hook()
    monkeypatch.setenv("CLAW_NO_SAST_OFFER", "1")
    out = hook.suggest_actions("run a security audit of the whole repo", "")
    assert "/clawness:security-audit" in out and "/security-review" not in out


def test_diff_scope_alone_is_not_a_security_request():
    hook = _hook()
    assert "/security-review" not in hook.suggest_actions("tidy up this change", "")
