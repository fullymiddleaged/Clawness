# Contributing to Clawness

Thanks for your interest. Contributions are welcome — rules, agents, bug fixes, and documentation improvements.

## Adding Rules

The easiest way to contribute. Create a `.yml` file in the appropriate `rules/<domain>/` directory.

### Rule template

```yaml
id: DOMAIN-SHORT-NNN
domain: your_domain
severity: error|warning|info
tags: [specific, search, keywords]
triggers: [code, tokens, that, appear, in, relevant, prompts]
when: Clear condition for when this rule applies.
rule: >
  The instruction Claude should follow. Be specific and actionable.
violation: "Concrete example of what NOT to do"
correct: "Concrete example of what TO do"
```

### Checklist

- [ ] `id` is unique and follows the `DOMAIN-SHORT-NNN` pattern
- [ ] `tags` and `triggers` contain words someone would actually use when working on a task this rule covers
- [ ] `rule` is specific enough that Claude can follow it without interpretation
- [ ] `violation` and `correct` show concrete code, not vague descriptions
- [ ] `python -m clawness.cli lint` passes
- [ ] `python -m clawness.cli query "a prompt that should match"` returns your rule in the top results

### Where to put rules

- `rules/_mandatory/` — only for security gates and non-negotiable requirements. Every mandatory rule costs tokens on every prompt, so be conservative.
- `rules/<domain>/` — for everything else. The retriever handles relevance.

## Adding Agents

Create a `.md` file in `agents/` with YAML frontmatter:

```yaml
---
name: your-agent-name
description: >
  When this agent should be invoked. Be specific — this text helps
  Claude decide when to delegate to your agent.
model: sonnet          # OMIT this line entirely for judgment/adversarial agents
effort: medium
maxTurns: 15
tools: Read, Grep, Glob
---

You are a [role]. Your job is to [specific task].

## Process
1. ...
2. ...

## Output Format
...
```

### Agent checklist

- [ ] `description` clearly states when to use this agent
- [ ] `model` matches the kind of work: **omit it** (Claude Code's `inherit`
      default) for judgment and adversarial agents, so they track whatever tier
      the user chose; pin the `sonnet` alias only for mechanical work like code
      or test generation and pattern scans. Never hardcode a frontier model
      (`opus`/`fable`) or a pinned ID in a shipped agent — we can't know a user's
      plan, access, or budget, and silently downgrading their judgment work is
      worse still, since a shallower answer reads exactly like a good one
- [ ] `tools` is minimal — only grant what the agent needs
- [ ] `maxTurns` is set to prevent runaway costs
- [ ] Read-only agents don't have `Write` or `Edit` tools
- [ ] Instructions are specific enough to produce consistent output

## Adding Skills

Create a directory in `skills/<name>/` with a `SKILL.md` inside:

```yaml
---
name: your-skill-name
description: >
  When this skill should be available. Becomes /clawness:<name> in Claude Code.
---

# Skill Name

Instructions for Claude when this skill is invoked.

If $ARGUMENTS is provided, use it as [context].
```

## Code Changes

### Setup

```bash
git clone https://github.com/fullymiddleaged/clawness.git
cd clawness
python -m clawness.cli lint     # rules valid
python -m clawness.cli bench    # retrieval works
python -m pytest tests/          # tests pass
```

### Guidelines

- Pure Python, no heavy dependencies (PyYAML is the only external dep)
- Keep the retriever fast (<1ms for <500 rules)
- Test your changes: `python -m pytest tests/`
- Lint rules after any YAML changes: `python -m clawness.cli lint`

## Pull Requests

- One feature/fix per PR
- Include a test if you're changing Python code
- Update CHANGELOG.md under `## [Unreleased]`
- Rule PRs: include the `clawness query` output showing your rule matches the right prompts
