"""
Tests for `clawness lint` (clawness/cli.py cmd_lint).

Runs under pytest, or standalone:  python tests/test_lint.py
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _lint(rules_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "clawness.cli", "--rules-dir", str(rules_dir), "lint"],
        capture_output=True, text=True, cwd=REPO,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


_MINIMAL = """\
id: {id}
domain: {domain}
severity: warning
tags: [t]
triggers: [t]
when: When something happens.
rule: Do the specific thing.
"""


def test_clean_corpus_passes(tmp_path):
    _write(tmp_path / "general" / "GEN-X-001.yml", _MINIMAL.format(id="GEN-X-001", domain="general"))
    r = _lint(tmp_path)
    assert r.returncode == 0, r.stdout
    assert "pass lint" in r.stdout


def test_missing_rule_text_flagged(tmp_path):
    # The one field the whole corpus exists to carry. A rule with no `rule:` still
    # loads (load_rules is forgiving so one bad file can't crash the hook), so lint
    # is the only thing standing between it and a silently empty injection.
    content = _MINIMAL.format(id="GEN-X-001", domain="general").replace(
        "rule: Do the specific thing.\n", "")
    _write(tmp_path / "general" / "GEN-X-001.yml", content)
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "missing 'rule'" in r.stdout


def test_missing_when_flagged(tmp_path):
    content = _MINIMAL.format(id="GEN-X-001", domain="general").replace(
        "when: When something happens.\n", "")
    _write(tmp_path / "general" / "GEN-X-001.yml", content)
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "missing 'when'" in r.stdout


def test_invalid_severity_flagged(tmp_path):
    content = _MINIMAL.format(id="GEN-X-001", domain="general").replace(
        "severity: warning", "severity: critical")
    _write(tmp_path / "general" / "GEN-X-001.yml", content)
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "invalid severity 'critical'" in r.stdout


def test_unparseable_yaml_flagged_not_silently_dropped(tmp_path):
    # load_rules skips a file that won't parse, so `stats` just shows one fewer
    # rule than the author wrote — no error anywhere. Lint has to be loud.
    _write(tmp_path / "general" / "GEN-OK-001.yml", _MINIMAL.format(id="GEN-OK-001", domain="general"))
    _write(tmp_path / "general" / "GEN-BAD-001.yml", 'id: GEN-BAD-001\nrule: "bad \\d escape"\n[\n')
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "does not parse as YAML" in r.stdout
    assert "silently dropped" in r.stdout


def test_replacement_char_flagged_as_encoding_corruption(tmp_path):
    # An em-dash that went through cp1252 and back leaves U+FFFD baked into the
    # file; it renders into every prompt from then on.
    content = _MINIMAL.format(id="GEN-X-001", domain="general").replace(
        "Do the specific thing.", "Do the specific thing � not the vague one.")
    _write(tmp_path / "general" / "GEN-X-001.yml", content)
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "U+FFFD" in r.stdout


def test_duplicate_id_flagged(tmp_path):
    _write(tmp_path / "general" / "GEN-X-001.yml", _MINIMAL.format(id="DUP-001", domain="general"))
    _write(tmp_path / "python" / "PY-X-001.yml", _MINIMAL.format(id="DUP-001", domain="python"))
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "duplicate id 'DUP-001'" in r.stdout


def test_missing_triggers_flagged(tmp_path):
    content = _MINIMAL.format(id="GEN-X-001", domain="general").replace("triggers: [t]\n", "")
    _write(tmp_path / "general" / "GEN-X-001.yml", content)
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "no triggers" in r.stdout


def test_oversized_mandatory_rule_flagged(tmp_path):
    long_rule = "Do the thing. " * 60  # well over 500 chars once rendered
    content = (
        "id: MAND-X-001\ndomain: general\nseverity: error\n"
        "tags: [t]\ntriggers: [t]\nwhen: Always.\n"
        f"rule: >\n  {long_rule}\n"
    )
    _write(tmp_path / "_mandatory" / "MAND-X-001.yml", content)
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "always-on budget" in r.stdout


def test_domain_folder_mismatch_flagged(tmp_path):
    _write(tmp_path / "python" / "GEN-X-001.yml", _MINIMAL.format(id="GEN-X-001", domain="general"))
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "doesn't match its folder" in r.stdout


def test_mandatory_domain_not_checked_against_folder(tmp_path):
    # The _mandatory/ folder isn't itself a domain name, so a mandatory rule's
    # `domain: security` must not be flagged as a folder mismatch.
    _write(tmp_path / "_mandatory" / "MAND-X-001.yml", _MINIMAL.format(id="MAND-X-001", domain="security"))
    r = _lint(tmp_path)
    assert r.returncode == 0, r.stdout


def test_vague_phrasing_in_violation_correct_flagged(tmp_path):
    content = _MINIMAL.format(id="GEN-X-001", domain="general") + \
        'violation: "do it wrong"\ncorrect: "fix it where appropriate"\n'
    _write(tmp_path / "general" / "GEN-X-001.yml", content)
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "vague phrasing in 'correct'" in r.stdout


# ── Version stamps (applies_to / verified / sources) ──────────────
#
# Every check below catches a SILENT failure: a stamp that can't be read doesn't
# raise and doesn't warn, it just never fires — so the rule looks reviewed and
# behaves exactly like an unreviewed one.

_STAMPED = _MINIMAL + """\
applies_to: {{"{label}": "{spec}"}}
verified: "{verified}"
sources: ["https://nextjs.org/docs"]
"""


def _write_stamped(tmp_path, label="Next.js", spec="13-15", verified="2026-08"):
    _write(
        tmp_path / "nextjs" / "NX-X-001.yml",
        _STAMPED.format(id="NX-X-001", domain="nextjs",
                        label=label, spec=spec, verified=verified),
    )


def test_valid_stamp_passes(tmp_path):
    _write_stamped(tmp_path)
    r = _lint(tmp_path)
    assert r.returncode == 0, r.stdout


def test_unknown_framework_label_flagged(tmp_path):
    # The check to watch fail first (TST-FAILFIRST-001). A label no detector
    # emits never matches the `versions` dict, so the warning silently never
    # fires — the whole feature goes inert while looking configured.
    _write_stamped(tmp_path, label="NextJS")
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "no detector emits" in r.stdout
    assert "did you mean 'Next.js'?" in r.stdout


def test_unrecognisable_label_lists_the_valid_ones(tmp_path):
    _write_stamped(tmp_path, label="Frobnicator")
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "known labels:" in r.stdout


def test_unparseable_range_flagged(tmp_path):
    _write_stamped(tmp_path, spec=">=13 <=15")
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "is unparseable" in r.stdout


def test_open_ended_range_flagged(tmp_path):
    # "13-" reads as "and everything after" — the claim nobody has evidence for.
    _write_stamped(tmp_path, spec="13-")
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "is unparseable" in r.stdout


def test_applies_to_that_is_not_a_mapping_flagged(tmp_path):
    # The loader coerces this to {} so the prompt hook can't crash on it. Lint is
    # the only place the author learns their stamp was thrown away.
    content = _MINIMAL.format(id="NX-X-001", domain="nextjs") + \
        'applies_to: "Next.js 13-15"\n'
    _write(tmp_path / "nextjs" / "NX-X-001.yml", content)
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "is being dropped" in r.stdout


def test_malformed_verified_date_flagged(tmp_path):
    _write_stamped(tmp_path, verified="August 2026")
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "expected YYYY-MM" in r.stdout


def test_future_verified_date_flagged(tmp_path):
    _write_stamped(tmp_path, verified="2999-01")
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "is in the future" in r.stdout


def test_verified_without_applies_to_flagged(tmp_path):
    # Evidence with no claim to establish. The author almost certainly believes
    # the rule arms a warning; it does not.
    content = _MINIMAL.format(id="NX-X-001", domain="nextjs") + \
        'verified: "2026-08"\nsources: ["https://nextjs.org/docs"]\n'
    _write(tmp_path / "nextjs" / "NX-X-001.yml", content)
    r = _lint(tmp_path)
    assert r.returncode == 1
    assert "arms no staleness warning" in r.stdout


def test_applies_to_without_evidence_is_allowed(tmp_path):
    # Asserted-not-verified is a legal, deliberate state: the rule stays silent
    # until review establishes the range. Lint must not push authors to fake it.
    content = _MINIMAL.format(id="NX-X-001", domain="nextjs") + \
        'applies_to: {"Next.js": "13-15"}\n'
    _write(tmp_path / "nextjs" / "NX-X-001.yml", content)
    r = _lint(tmp_path)
    assert r.returncode == 0, r.stdout


def test_unstamped_rule_is_allowed(tmp_path):
    _write(tmp_path / "nextjs" / "NX-X-001.yml",
           _MINIMAL.format(id="NX-X-001", domain="nextjs"))
    r = _lint(tmp_path)
    assert r.returncode == 0, r.stdout


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
