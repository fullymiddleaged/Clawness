## What does this change?

<!-- One or two sentences: what, and why. -->

## Checklist

- [ ] `python -m clawness.cli lint` passes (if rules changed)
- [ ] `python -m pytest tests/` passes
- [ ] `python -m clawness.cli eval --floor-mrr 0.85 --floor-hit 0.95` passes (if retrieval/rules changed)
- [ ] Added/updated a test for any Python code change
- [ ] Updated `CHANGELOG.md` under `## [Unreleased]`
- [ ] For a new rule: included `clawness query "..."` output showing it matches the intended prompts

## Related issue

<!-- Closes #... , if applicable -->
