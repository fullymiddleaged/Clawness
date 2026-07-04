"""
Version parity: the package version lives in FOUR places and they must agree.
A drift here shipped a `0.1.0` importable version while the manifests said
`0.7.0` — this test makes that a CI failure, not a surprise.

Runs under pytest, or standalone:  python tests/test_version.py
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent

import clawness  # noqa: E402


def _pyproject_version() -> str:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
    assert m, "no version in pyproject.toml"
    return m.group(1)


def _json_version(rel: str, *keys) -> str:
    data = json.loads((REPO / rel).read_text(encoding="utf-8"))
    for k in keys:
        data = data[k]
    return data


def test_all_version_sources_agree():
    versions = {
        "clawness.__version__": clawness.__version__,
        "pyproject.toml": _pyproject_version(),
        "plugin.json": _json_version(".claude-plugin/plugin.json", "version"),
        "marketplace.json": _json_version(
            ".claude-plugin/marketplace.json", "plugins", 0, "version"),
    }
    unique = set(versions.values())
    assert len(unique) == 1, f"version drift: {versions}"


def test_changelog_has_current_version_entry():
    ver = clawness.__version__
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"[{ver}]" in changelog or f"## {ver}" in changelog, \
        f"CHANGELOG.md has no entry for {ver}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
