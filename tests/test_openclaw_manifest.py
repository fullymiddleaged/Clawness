"""
Root-vs-subdir OpenClaw manifest parity.

A git install (`openclaw plugins install git:github.com/fullymiddleaged/Clawness`)
clones the whole repo and reads the plugin manifest at the CLONE ROOT, so the
discovery files (package.json's `openclaw` block + openclaw.plugin.json) are
duplicated at the repo root, pointing the host at the prebuilt adapter entry that
lives under openclaw/. Duplication drifts; this makes drift a CI failure.

Runs under pytest, or standalone:  python tests/test_openclaw_manifest.py
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _json(rel: str) -> dict:
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def test_root_manifest_matches_subdir_manifest():
    """The two openclaw.plugin.json copies must be identical."""
    root = _json("openclaw.plugin.json")
    sub = _json("openclaw/openclaw.plugin.json")
    assert root == sub, (
        "openclaw.plugin.json (root) and openclaw/openclaw.plugin.json disagree; "
        "keep them identical — the root copy is what a git install reads."
    )


def test_root_package_points_at_the_prebuilt_entry():
    """Root package.json must expose the built adapter entry to the host."""
    root_pkg = _json("package.json")
    oc = root_pkg.get("openclaw", {})
    assert oc.get("extensions") == ["./openclaw/dist/src/index.js"], (
        f"root package.json openclaw.extensions is {oc.get('extensions')!r}; "
        "must point at ./openclaw/dist/src/index.js (the committed build output)."
    )
    assert oc.get("plugin", {}).get("json") == "openclaw.plugin.json"


def test_committed_entry_exists():
    """git installs don't build — the entry the manifest names must be tracked."""
    entry = REPO / "openclaw" / "dist" / "src" / "index.js"
    assert entry.is_file(), (
        f"{entry} is missing. Run 'npm run build' in openclaw/ and commit "
        "dist/src — the host loads this prebuilt file on a git install."
    )


def test_version_floors_agree_and_are_declared():
    """Both package.json openclaw blocks must declare the same install/API floor.

    A git install reads the ROOT package.json, so its compat.pluginApi (enforced
    at install for non-bundled sources) is what actually gates a too-old host.
    The subdir dev copy mirrors it; drift would let one claim support the other
    doesn't. The floor is >=2026.3.24-beta.2, the first version whose plugin-sdk
    ships the definePluginEntry / plugin-entry API this adapter is built against.
    """
    root_oc = _json("package.json")["openclaw"]
    sub_oc = _json("openclaw/package.json")["openclaw"]
    for block in (root_oc, sub_oc):
        assert block.get("compat", {}).get("pluginApi"), (
            "package.json openclaw.compat.pluginApi is missing; declare the "
            "plugin-sdk API floor so a too-old host is rejected at install."
        )
    assert root_oc["compat"] == sub_oc["compat"], (
        f"compat drift: root={root_oc['compat']!r}, subdir={sub_oc['compat']!r}"
    )
    assert root_oc.get("install") == sub_oc.get("install"), (
        f"install-hint drift: root={root_oc.get('install')!r}, "
        f"subdir={sub_oc.get('install')!r}"
    )


def test_adapter_versions_agree():
    """Both adapter package.json versions (root + subdir) must match."""
    root_v = _json("package.json")["version"]
    sub_v = _json("openclaw/package.json")["version"]
    assert root_v == sub_v, (
        f"adapter version drift: root package.json={root_v!r}, "
        f"openclaw/package.json={sub_v!r}"
    )


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
