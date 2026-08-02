"""
Tests for rule rendering, incl. the dynamic {{CURRENT_DATE}} placeholder.

Runs under pytest, or standalone:  python tests/test_render.py
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawness.core import (  # noqa: E402
    Clawness,
    Rule,
    _current_date,
    _DATE_TOKEN,
    _estimate_tokens,
    load_rules,
)

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


def test_default_block_has_no_per_turn_telemetry():
    # relevance scores and timing vary every turn — embedding them makes an
    # otherwise identical block byte-different each prompt (defeats prompt
    # caching) and tells the model nothing. Hidden unless CLAW_VERBOSE.
    wl = Clawness(RULES_DIR)
    block = wl.retrieve("implement async fastapi endpoint with pydantic")
    assert "relevance=" not in block
    assert "ms)" not in block
    assert block.startswith("--- CLAWNESS RULES ---")
    # two retrievals of the same query render byte-identically
    assert block == wl.retrieve("implement async fastapi endpoint with pydantic")


def test_show_meta_restores_relevance_and_timing():
    wl = Clawness(RULES_DIR)
    block = wl.retrieve("implement async fastapi endpoint with pydantic", show_meta=True)
    assert "relevance=" in block
    assert "ms)" in block


# --- the context budget ---------------------------------------------------
# `retrieve` stops adding ranked rules the moment one would push the block past
# CLAW_BUDGET. The arithmetic had no test that could fail on it: a generous budget
# admits everything whatever the comparison says, so these fix a small corpus and
# aim the budget at exact rule boundaries.

_RULE_YML = """\
id: GEN-KAFKA-{n}
domain: general
severity: warning
tags: [kafka, consumer]
triggers: [kafka consumer rebalance]
when: Consuming from a kafka topic, case {n}.
rule: Commit offsets after processing, never before, in consumer case {n}.
"""


def _corpus(count: int = 4) -> Path:
    d = Path(tempfile.mkdtemp()) / "rules" / "general"
    d.mkdir(parents=True)
    for n in range(count):
        (d / f"GEN-KAFKA-{n}.yml").write_text(_RULE_YML.format(n=n), encoding="utf-8")
    return d.parent


def _ranked_cost(wl: Clawness, query: str, n: int) -> int:
    """Token cost of the first *n* ranked rules, as `retrieve` measures it."""
    return sum(
        _estimate_tokens(wl._ranked_rules[idx].render(rel, compact=wl._ranked_compact))
        for idx, rel in wl._rank(query, None, 10)[:n]
    )


def test_budget_admits_a_rule_that_fits_exactly():
    """The check is `used + cost > budget`, so a rule landing precisely on the
    budget still ships. A budget of "two rules' worth plus slack" passes under `>=`
    too — this one sits exactly on the line."""
    rules = _corpus()
    query = "kafka consumer rebalance"
    probe = Clawness(rules, context_budget=10_000, top_k=3)
    exact = _ranked_cost(probe, query, 2)

    wl = Clawness(rules, context_budget=exact, top_k=3)
    block = wl.retrieve(query)
    assert block.count("[GEN-KAFKA-") == 2

    # One token less and only the first rule fits — proof the budget is the thing
    # doing the work here, not top_k or the relevance floor.
    tight = Clawness(rules, context_budget=exact - 1, top_k=3)
    assert tight.retrieve(query).count("[GEN-KAFKA-") == 1


def test_budget_accumulates_across_rules_rather_than_resetting():
    """`used_tokens += cost`. If it assigned instead of accumulating, every rule
    would be measured against an empty block and the budget would never bind."""
    rules = _corpus(6)
    query = "kafka consumer rebalance"
    probe = Clawness(rules, context_budget=10_000, top_k=6)
    two_rules = _ranked_cost(probe, query, 2)

    wl = Clawness(rules, context_budget=two_rules, top_k=6)
    assert wl.retrieve(query).count("[GEN-KAFKA-") == 2


def test_mandatory_rules_are_charged_against_the_same_budget():
    # Ranked rules get what's left after the always-on block, which is why
    # `clawness stats` reports the two separately.
    rules = _corpus()
    mand = rules / "_mandatory"
    mand.mkdir()
    (mand / "MAND-001.yml").write_text(
        _RULE_YML.format(n=99).replace("id: GEN-KAFKA-99", "id: MAND-001"), encoding="utf-8"
    )
    query = "kafka consumer rebalance"
    probe = Clawness(rules, context_budget=10_000, top_k=3)
    room_for_two = _ranked_cost(probe, query, 2)

    with_mandatory = Clawness(rules, context_budget=room_for_two, top_k=3)
    block = with_mandatory.retrieve(query)
    assert "MAND-001" in block
    assert block.count("[GEN-KAFKA-") < 2      # the mandatory block ate the room


# --- block shape ----------------------------------------------------------

def test_block_is_terminated_by_the_end_marker():
    # The hook prints this block into the prompt; without a terminator the model
    # has no boundary between the rules and the user's own text.
    wl = Clawness(_corpus())
    block = wl.retrieve("kafka consumer rebalance")
    assert block.splitlines()[-1] == "--- END CLAWNESS RULES ---"
    assert block.splitlines()[0] == "--- CLAWNESS RULES ---"


def test_meta_header_counts_mandatory_and_ranked_together():
    rules = _corpus()
    mand = rules / "_mandatory"
    mand.mkdir()
    for n in (98, 99):
        (mand / f"MAND-{n}.yml").write_text(
            _RULE_YML.format(n=n).replace(f"id: GEN-KAFKA-{n}", f"id: MAND-{n}"),
            encoding="utf-8",
        )
    wl = Clawness(rules, top_k=3)
    header = wl.retrieve("kafka consumer rebalance", show_meta=True).splitlines()[0]
    assert header.startswith("--- CLAWNESS RULES (5 rules,")   # 2 mandatory + 3 ranked


def test_abbreviated_mandatory_replaces_the_full_text_with_an_id_list():
    rules = _corpus()
    mand = rules / "_mandatory"
    mand.mkdir()
    # Two mandatory rules, not one: with a single id the separator between them is
    # unobservable, so ", ".join could be "".join and nothing would notice.
    for n, rid in ((98, "MAND-001"), (99, "MAND-002")):
        (mand / f"{rid}.yml").write_text(
            _RULE_YML.format(n=n).replace(f"id: GEN-KAFKA-{n}", f"id: {rid}"),
            encoding="utf-8",
        )
    wl = Clawness(rules, top_k=1)
    full = wl.retrieve("kafka consumer rebalance")
    short = wl.retrieve("kafka consumer rebalance", abbreviate_mandatory=True)

    assert "# MANDATORY (2)" in full
    assert "Commit offsets after processing" in full

    assert "MANDATORY (in context above, still binding): MAND-001, MAND-002" in short
    assert "# MANDATORY (2)" not in short
    # The rule stays binding but its text is gone — that's the whole saving.
    assert short.count("Commit offsets after processing") < full.count(
        "Commit offsets after processing")
    assert len(short) < len(full)


def test_abbreviation_is_off_unless_asked_for():
    # `abbreviate_mandatory and self._mandatory_rules` — an `or` there would
    # abbreviate every turn, which reads identically in a corpus with no
    # mandatory rules and silently drops the rule text in one that has them.
    rules = _corpus()
    mand = rules / "_mandatory"
    mand.mkdir()
    (mand / "MAND-001.yml").write_text(
        _RULE_YML.format(n=99).replace("id: GEN-KAFKA-99", "id: MAND-001"), encoding="utf-8"
    )
    block = Clawness(rules, top_k=1).retrieve("kafka consumer rebalance")
    assert "in context above, still binding" not in block


def test_retrieve_before_build_index_raises_rather_than_returning_an_empty_block():
    wl = Clawness(_corpus(), build_index=False)
    try:
        wl.retrieve("kafka consumer rebalance")
    except RuntimeError as e:
        assert "build_index()" in str(e)
    else:
        raise AssertionError("retrieve() must refuse to run on an unbuilt index")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok  {name}")
            except Exception as e:  # noqa: BLE001
                print(f"FAIL {name}: {e}")
    print("done")
