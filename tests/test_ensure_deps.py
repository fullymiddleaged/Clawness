"""
Tests for the SessionStart dependency/bootstrap hook (hooks/ensure_deps.py),
specifically the CLI-wrapper stash added to fix skills on plugin-only installs.

The bug it guards against: a skill's Bash sees an empty ${CLAUDE_PLUGIN_ROOT}
(upstream anthropics/claude-code#9354) and `clawness` is not pip-installed, so
`python -m clawness.cli` from a project dir raises ModuleNotFoundError. The
wrapper bakes the plugin root onto PYTHONPATH so the CLI resolves from any cwd.

The load-bearing test is test_wrapper_resolves_import_from_arbitrary_cwd: it runs
the *generated* wrapper from a directory that is NOT the source checkout and
asserts `import clawness` succeeded. Everything else could pass while the real bug
persists.

Runs under pytest, or standalone:  python tests/test_ensure_deps.py
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import ensure_deps as E  # noqa: E402

REPO = Path(__file__).resolve().parent.parent  # the plugin root ensure_deps self-locates


def _git_bash() -> str | None:
    """The POSIX shell Claude Code runs hooks through. On Windows that is Git
    Bash, preferred explicitly: the only `bash` on PATH is usually WSL's, which
    resolves no `python`/`py` (no `.exe`), so the wrapper's picker falls through
    there and the test would measure the wrong shell (see project memory)."""
    import shutil
    if os.name == "nt":
        for p in (r"C:\Program Files\Git\bin\bash.exe",
                  r"C:\Program Files\Git\usr\bin\bash.exe"):
            if Path(p).exists():
                return p
    return shutil.which("bash") or shutil.which("sh")


def test_config_dirs_honors_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/a, /b")
    dirs = E._config_dirs()
    assert dirs[:2] == [Path("/a"), Path("/b")]
    assert dirs[-1] == Path.home() / ".claude"

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert E._config_dirs() == [Path.home() / ".claude"]


def test_stash_writes_wrapper_and_plugin_root(tmp_path):
    E.stash_cli_wrapper([tmp_path])
    d = tmp_path / "clawness"
    wrapper = d / "clawness-cli.sh"
    root_file = d / "plugin_root"
    assert wrapper.exists() and root_file.exists()

    text = wrapper.read_text(encoding="utf-8")
    # POSIX separators in the sh string (no backslash escaping), and the module call.
    assert f'export PYTHONPATH="{REPO.as_posix()}"' in text
    assert "-m clawness.cli" in text
    assert 'for p in python3 python py;' in text  # interpreter picker, as elsewhere
    assert root_file.read_text(encoding="utf-8").strip() == str(REPO)


def test_writes_to_every_config_dir(tmp_path):
    d1, d2 = tmp_path / "one", tmp_path / "two"
    E.stash_cli_wrapper([d1, d2])
    assert (d1 / "clawness" / "clawness-cli.sh").exists()
    assert (d2 / "clawness" / "clawness-cli.sh").exists()


def test_stash_never_raises_on_bad_dir(tmp_path):
    # A config dir that can't be created must be logged and skipped, not raised —
    # the whole file's contract is best-effort.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir", encoding="utf-8")
    # blocker/clawness can't be mkdir'd (parent is a file); must not raise.
    E.stash_cli_wrapper([blocker])


def test_wrapper_resolves_import_from_arbitrary_cwd(tmp_path):
    """THE regression test: run the generated wrapper from a dir that is not the
    checkout and confirm `import clawness` resolved (the CLI printed its stats)."""
    sh = _git_bash()
    if not sh:
        import pytest
        pytest.skip("no POSIX shell available")

    # Premise check: an interpreter must be visible to this shell, else the
    # wrapper's picker legitimately falls through and we'd be testing nothing.
    probe = subprocess.run(
        [sh, "-c", 'for p in python3 python py; do command -v "$p" && exit 0; done; exit 1'],
        capture_output=True, text=True, timeout=60)
    if probe.returncode != 0:
        import pytest
        pytest.skip(f"no interpreter visible to {sh} — premise not met")

    cfg = tmp_path / "cfg"
    E.stash_cli_wrapper([cfg])
    wrapper = cfg / "clawness" / "clawness-cli.sh"

    # cwd must NOT be the checkout (cwd on sys.path would mask a broken PYTHONPATH)
    # and must NOT itself contain a `clawness/` dir (namespace-package shadowing).
    work = tmp_path / "work"
    work.mkdir()
    r = subprocess.run([sh, str(wrapper), "stats"], cwd=str(work),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr!r}"
    # `stats` prints these only if `import clawness` succeeded.
    assert "Ranked rules" in r.stdout and "Total" in r.stdout, r.stdout


if __name__ == "__main__":
    import tempfile

    class _MP:
        """Tiny monkeypatch stand-in for standalone runs."""
        def __init__(self): self._saved = {}
        def setenv(self, k, v): self._saved.setdefault(k, os.environ.get(k)); os.environ[k] = v
        def delenv(self, k, raising=True): self._saved.setdefault(k, os.environ.get(k)); os.environ.pop(k, None)
        def undo(self):
            for k, v in self._saved.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v

    passed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        with tempfile.TemporaryDirectory() as d:
            if "monkeypatch" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                mp = _MP()
                try:
                    fn(mp)
                finally:
                    mp.undo()
            else:
                fn(Path(d))
        passed += 1
        print(f"  ok  {name}")
    print(f"\n{passed} tests passed")
