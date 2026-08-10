---
name: audit-rules
description: >
  Review one domain of the Clawness rule corpus for correctness against current
  official documentation, then record what was established as a version stamp
  (applies_to / verified / sources) on each rule individually. For maintainers and
  fork maintainers. Reports verdicts and writes stamps; never rewrites rule text.
---

# Audit a rule domain

`clawness audit-rules` measures what is measurable — missing provenance, eval
blind spots, near-duplicate rules, unreachable rules. This skill is the half that
isn't measurable: **is this rule still true?**

It is also where version ranges come from. There is no way to derive what a rule
was written against — git dates are a weak proxy (an author can write for an older
major than the day's current one) and rule text names an API, not a version. So
`applies_to` is an **output of this review**, established rule by rule from that
rule's own evidence.

**One domain per run.** 212 rules in one pass is not a review. Reviewing several
domains at once is how a careful pass becomes a bulk stamp, which is the exact
failure per-rule stamping exists to prevent.

## Before you start

Run the mechanical checks first, so the review has the numbers in front of it:

```bash
clawness audit-rules --stale --overlap        # or: python -m clawness.cli ...
```

`--overlap` matters here specifically: near-duplicates within the domain you are
about to review are cheapest to spot while you have all of it in your head.

## Steps

### 1. Read the whole domain

Every `rules/<domain>/*.yml`, plus any existing `applies_to` on them. Note which
frameworks the domain names and what versions the current rules imply.

### 2. Look up what is current

Follow the lookup procedure in
[`skills/refresh/SKILL.md`](../refresh/SKILL.md) step 3 — same procedure, different
destination: that one writes to one project's `.clawness/rules/`, this one fixes
the corpus upstream. In short: the framework's own migration guide, then its
changelog, then the current official docs page. **Not recalled knowledge** — a
model's sense of "what's current" lags real releases by exactly the gap this
feature exists to close.

### 3. Give each rule a verdict

| Verdict | Meaning |
| --- | --- |
| **Still true** | Correct at the current major. The honest majority. |
| **Now wrong** | The API or the recommendation changed. |
| **Now the framework default** | True but redundant — the framework does it for you, so the rule is noise competing for a top-k slot. |
| **Unsettled** | The docs didn't answer it. Leave it alone and say so. |

### 4. Report, don't rewrite

**Never edit rule text from this pass.** A rule silently flipped by a
hallucination governs every subsequent prompt in every project that installs
Clawness — strictly worse than a stale one, because it looks reviewed. Report the
verdicts and let the maintainer decide what to change.

The one thing this skill *does* write is the stamp (step 5).

### 5. Stamp each rule individually

For every rule you settled, add:

```yaml
applies_to: {"Next.js": "13-15"}
verified: "<YYYY-MM, this month>"
sources: ["<the URL that justified it>"]
```

Rules on rules:

- **Per rule, from that rule's evidence.** Do not derive a domain-wide span and
  apply it across the folder. A domain range is the union of its rules' ranges,
  which is the widest claim available, and wide is the direction that fails
  *silently*.
- **Both bounds need evidence, and the lower one is the easy one to fake.** "True
  from 13" is a claim about 13, not a free consequence of checking 17.
- **Where evidence doesn't settle a bound, narrow.** Too narrow produces a visible
  false warning that gets corrected; too wide produces silence that doesn't.
- **Leave unsettled rules unstamped** and list them. A gap is truthful; a stamp
  that says "checked" when nobody checked is the original bug wearing a hallmark.
- **The label must be one a detector emits** (`"Next.js"`, not `"next"`). A label
  nothing emits never matches, so the rule can never be flagged stale again.
- All three fields, or the rule stays silent — `applies_to` alone is asserted, not
  established, and deliberately arms nothing.

### 6. Verify

```bash
clawness lint     # validates labels, ranges, and dates mechanically
clawness eval --floor-mrr 0.85 --floor-hit 0.95
```

`eval` must be unchanged: stamps are excluded from the search text, so a moved
score means something went into the wrong field.

Then report: how many rules reviewed, how many still true, how many wrong, how
many left unsettled, and the domain-level `--stale` count before and after.

## Don't

- Don't stamp a folder. Ever.
- Don't rewrite rule text. Report it.
- Don't stamp from memory — every stamp needs a URL you actually read.
- Don't treat repeated per-project overrides of one id as noise. That is the
  signal the rule needs fixing here, upstream, rather than being patched in every
  project separately.
