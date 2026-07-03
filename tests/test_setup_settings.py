"""
Tests for the manual-install hook wiring (hooks/setup_settings.py).

The manual-install path (settings.json) must wire the same hooks as the plugin
path (.claude-plugin/plugin.json). This guards against the two drifting — in
particular the access guard (PreToolUse + PostToolUse) and trust ledger
(SessionStart), which the installer historically omitted.

Runs under pytest, or standalone:  python tests/test_setup_settings.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import setup_settings as S  # noqa: E402

HOOK_SCRIPT = Path(__file__).resolve().parent.parent / "hooks" / "claude_hook.py"


def _install(tmp_path) -> dict:
    settings = tmp_path / "settings.json"
    S.merge(settings, HOOK_SCRIPT)
    return json.loads(settings.read_text(encoding="utf-8"))


def _scripts_on(data: dict, event: str) -> set[str]:
    """Set of Clawness script basenames wired under an event (across all groups)."""
    out = set()
    for group in data.get("hooks", {}).get(event, []):
        for h in group.get("hooks", []):
            for name in S.CLAW_HOOK_SCRIPTS:
                if name in h.get("command", ""):
                    out.add(name)
    return out


def test_access_guard_wired_on_pre_and_post_tool_use(tmp_path):
    data = _install(tmp_path)
    assert "access_guard.py" in _scripts_on(data, "PreToolUse")
    # PostToolUse is required for the two-phase ledger's confirm step — without
    # it a declined ask could never re-ask.
    assert "access_guard.py" in _scripts_on(data, "PostToolUse")


def test_access_guard_matchers_match(tmp_path):
    data = _install(tmp_path)
    expected = "Bash|Write|Edit|MultiEdit|NotebookEdit|Read"
    for event in ("PreToolUse", "PostToolUse"):
        matchers = [
            g.get("matcher")
            for g in data["hooks"][event]
            if any("access_guard.py" in h.get("command", "") for h in g.get("hooks", []))
        ]
        assert matchers == [expected], f"{event}: {matchers}"


def test_trust_ledger_wired_on_session_start(tmp_path):
    data = _install(tmp_path)
    assert "trust_ledger.py" in _scripts_on(data, "SessionStart")


def test_all_expected_hooks_present(tmp_path):
    data = _install(tmp_path)
    assert _scripts_on(data, "UserPromptSubmit") == {"claude_hook.py"}
    assert _scripts_on(data, "SessionStart") == {
        "git_check.py", "memory_init.py", "stack_detect.py", "trust_ledger.py",
    }
    assert "compress_output.py" in _scripts_on(data, "PostToolUse")
    assert "plan_gate.py" in _scripts_on(data, "PreToolUse")


def test_install_is_idempotent(tmp_path):
    settings = tmp_path / "settings.json"
    S.merge(settings, HOOK_SCRIPT)
    first = json.loads(settings.read_text(encoding="utf-8"))
    second_msg = S.merge(settings, HOOK_SCRIPT)
    second = json.loads(settings.read_text(encoding="utf-8"))
    assert first == second  # no duplication on re-run
    assert "already configured" in second_msg


def test_uninstall_removes_new_hooks(tmp_path):
    settings = tmp_path / "settings.json"
    S.merge(settings, HOOK_SCRIPT)
    S.unmerge(settings)
    data = json.loads(settings.read_text(encoding="utf-8"))
    # Every Clawness hook is gone; the hooks section is left empty (not dangling).
    for event in ("PreToolUse", "PostToolUse", "SessionStart", "UserPromptSubmit"):
        assert _scripts_on(data, event) == set(), event


def test_matches_plugin_manifest(tmp_path):
    """The manual installer must wire the same hooks as the plugin manifest.
    ensure_deps.py is the one intentional exception (plugin-only — a manual
    install runs a real `pip install`, so the deps are already present)."""
    data = _install(tmp_path)
    manifest = json.loads(
        (Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json")
        .read_text(encoding="utf-8")
    )
    plugin_hooks = manifest["hooks"]

    def plugin_scripts_on(event: str) -> set[str]:
        out = set()
        for group in plugin_hooks.get(event, []):
            for h in group.get("hooks", []):
                for name in S.CLAW_HOOK_SCRIPTS:
                    if name in h.get("command", ""):
                        out.add(name)
        return out

    for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "SessionStart"):
        plugin_set = plugin_scripts_on(event) - {"ensure_deps.py"}
        installer_set = _scripts_on(data, event)
        assert plugin_set == installer_set, (
            f"{event}: plugin has {plugin_set}, installer has {installer_set}"
        )


if __name__ == "__main__":
    import tempfile
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests passed")
