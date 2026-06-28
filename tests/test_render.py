"""
Tests for rule rendering, incl. the dynamic {{CURRENT_DATE}} placeholder.

Runs under pytest, or standalone:  python tests/test_render.py
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness.core import Rule, _current_date, _DATE_TOKEN, load_rules  # noqa: E402

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"


def _rule(text: str) -> Rule:
    r = Rule(id="X-1", domain="test", severity="info", mandatory=False,
             tags=["t"], triggers=[], when="w", rule=text, violation="", correct="")
    r.build_search_text()
    return r


def test_date_token_substituted_at_render():
    out = _rule(f"use best practices as of {_DATE_TOKEN}").render(compact=True)
    assert _DATE_TOKEN not in out
    assert _current_date() in out
    assert datetime.now().strftime("%B %Y") == _current_date()  # "June 2026"


def test_text_without_token_is_untouched():
    out = _rule("a plain rule with no token").render(compact=True)
    assert "a plain rule with no token" in out


def test_token_kept_in_search_text_so_retrieval_is_date_independent():
    r = _rule(f"x {_DATE_TOKEN}")
    assert _DATE_TOKEN in r._search_text       # not substituted in the index
    # ...and a live date string is NOT indexed
    assert _current_date() not in r._search_text


def test_enf_current_rule_uses_the_placeholder():
    _, mand = load_rules(RULES_DIR)
    r = next(x for x in mand if x.id == "ENF-CURRENT-001")
    assert _DATE_TOKEN in r.rule                # stored with the token
    assert _current_date() in r.render(compact=True)  # rendered with the live date


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok  {name}")
            except Exception as e:  # noqa: BLE001
                print(f"FAIL {name}: {e}")
    print("done")
