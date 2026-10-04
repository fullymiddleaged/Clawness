"""
Tests for scripts/clawness-cli.sh, the launcher skills reach through
"${CLAUDE_PLUGIN_ROOT}/scripts/clawness-cli.sh", and the fallback snippet every
CLI-using skill carries.

The launcher replaces the per-session stashed wrapper (hooks/ensure_deps.py) on
Claude Code versions that substitute ${CLAUDE_PLUGIN_ROOT} in a SKILL.md body. On
older versions the reference stays literal and expands to an empty path in Bash,
so the snippet must fall back to the wrapper; both branches are pinned here.

Runs under pytest, or standalone:  python tests/test_cli_launcher.py
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "scripts" / "clawness-cli.sh"
LINE1 = 'CLAW="${CLAUDE_PLUGIN_ROOT}/scripts/clawness-cli.sh"'
LINE2 = '[ -f "$CLAW" ] || CLAW="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/clawness/clawness-cli.sh"'

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ensure_deps import _git_bash  # noqa: E402


def _shell():
    sh = _git_bash()
    if not sh:
        pytest.skip("no POSIX shell available")
    return sh


def _env(**kw):
    env = dict(os.environ)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.pop("CLAWNESS_CLI_PRINT_ROOT", None)
    env.update(kw)
    return env


def test_launcher_has_lf_line_endings():
    # A CRLF checkout makes sh fail with "$'\r': command not found".
    assert b"\r" not in LAUNCHER.read_bytes()
    attrs = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert re.search(r"^\*\.sh\s+text\s+eol=lf\s*$", attrs, re.MULTILINE)


def test_launcher_locates_the_plugin_root_in_a_form_python_can_read(tmp_path):
    """On Windows Git Bash plain `pwd` gives /c/..., which Windows Python can't put
    on sys.path; the launcher must hand over C:/... instead."""
    sh = _shell()
    r = subprocess.run([sh, str(LAUNCHER)], cwd=str(tmp_path), capture_output=True,
                       text=True, timeout=60, env=_env(CLAWNESS_CLI_PRINT_ROOT="1"))
    assert r.returncode == 0, r.stderr
    assert Path(r.stdout.strip()).resolve() == REPO


def test_launcher_runs_the_cli_from_an_arbitrary_cwd(tmp_path):
    sh = _shell()
    probe = subprocess.run(
        [sh, "-c", 'for p in python3 python py; do command -v "$p" && exit 0; done; exit 1'],
        capture_output=True, text=True, timeout=60)
    if probe.returncode != 0:
        pytest.skip(f"no interpreter visible to {sh}")
    r = subprocess.run([sh, str(LAUNCHER), "stats"], cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=60, env=_env())
    assert r.returncode == 0, r.stderr
    assert "Ranked rules" in r.stdout and "Total" in r.stdout


def _resolve(tmp_path, plugin_root):
    sh = _shell()
    env = _env(HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(tmp_path / "cfg"))
    if plugin_root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = plugin_root
    r = subprocess.run([sh, "-c", f'{LINE1}\n{LINE2}\nprintf "%s" "$CLAW"'],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_snippet_uses_the_launcher_when_the_root_is_substituted(tmp_path):
    root = REPO.as_posix()
    assert _resolve(tmp_path, root) == f"{root}/scripts/clawness-cli.sh"


def test_snippet_falls_back_to_the_wrapper_on_older_claude_code(tmp_path):
    # Unsubstituted, ${CLAUDE_PLUGIN_ROOT} expands to "" in the Bash tool.
    assert Path(_resolve(tmp_path, None)) == tmp_path / "cfg" / "clawness" / "clawness-cli.sh"


def test_every_cli_skill_carries_both_halves():
    using = [f for f in sorted((REPO / "skills").glob("*/SKILL.md"))
             if "clawness-cli.sh" in f.read_text(encoding="utf-8")]
    assert len(using) >= 8
    for f in using:
        text = f.read_text(encoding="utf-8")
        assert LINE1 in text and LINE2 in text, f.parent.name
