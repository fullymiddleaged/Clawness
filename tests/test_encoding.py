"""
Encoding regression tests. Rule YAML is UTF-8; reading it with the platform
default (cp1252 on Windows) silently mangled em-dashes/smart-quotes into mojibake
at load time. These guard that load_rules pins UTF-8 and tolerates bad files.

Runs under pytest, or standalone:  python tests/test_encoding.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness.core import load_rules  # noqa: E402

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


def test_corpus_loads_without_mojibake():
    ranked, mandatory = load_rules(RULES_DIR)
    for r in ranked + mandatory:
        blob = " ".join([r.rule, r.when, r.violation, r.correct])
        assert "�" not in blob, f"{r.id} has a replacement char"
        # the classic UTF-8-read-as-cp1252 signature for em-dash / smart quotes
        assert "â€" not in blob, f"{r.id} contains mojibake (was decoded wrong)"
        assert "Ã©" not in blob, f"{r.id} contains mojibake"


def test_known_em_dash_rule_is_intact():
    _, mandatory = load_rules(RULES_DIR)
    r = next(x for x in mandatory if x.id == "ENF-CURRENT-001")
    assert "—" in r.rule          # real U+2014
    assert "â€" not in r.rule       # not mojibake


def test_load_rules_pins_utf8_regardless_of_default():
    """A rule file with an em-dash must round-trip even though open()'s platform
    default may be cp1252."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "EM-001.yml"
        p.write_text(
            "id: EM-001\ndomain: test\nseverity: info\ntags: [x]\n"
            "when: x\nrule: use an em-dash — like this\n",
            encoding="utf-8",
        )
        ranked, _ = load_rules(Path(d))
        assert ranked and "—" in ranked[0].rule


def test_load_rules_skips_undecodable_file_without_crashing():
    """A malformed (non-UTF-8) file must be skipped, not crash the loader — the
    prompt hook depends on this."""
    with tempfile.TemporaryDirectory() as d:
        good = Path(d) / "GOOD-001.yml"
        good.write_text(
            "id: GOOD-001\ndomain: test\nseverity: info\ntags: [x]\nwhen: x\nrule: ok\n",
            encoding="utf-8",
        )
        bad = Path(d) / "BAD-001.yml"
        bad.write_bytes(b"\xff\xfe id: BAD\x80\x81 not utf-8")  # invalid UTF-8
        ranked, _ = load_rules(Path(d))  # must not raise
        ids = {r.id for r in ranked}
        assert "GOOD-001" in ids
        assert "BAD-001" not in ids


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok  {name}")
            except Exception as e:  # noqa: BLE001
                print(f"FAIL {name}: {e}")
    print("done")
