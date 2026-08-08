# Project lessons (Clawness memory)
<!-- Clawness retrieves from this file each prompt: `## Always` entries are
     injected every turn (keep to 3), `## Lessons` entries only when they match
     the prompt. Tell Claude "remember this: ..." or append a bullet yourself
     (one line, <=120 chars, newest at the bottom). See ENF-MEM-001. -->

## Always

## Lessons

- mutmut 3.7 refuses to run on native Windows; use a WSL copy on the WSL filesystem, not /mnt/c (far slower).
- mutmut survivor lists contain false survivors (weak test selection) — apply each mutant by hand before writing a test.
- Never hand-test a hook with git-bash `mktemp -d`: Windows Python resolves /tmp/x to C:\tmp\x, so git_root fails and it looks broken.
- A staleness note asking Claude to author rules wrote heaps of them and degraded the session; keep rule-authoring opt-in.
- `clawness bench` swings 1.3-5ms with machine load; compare against a stashed HEAD run before blaming a change.
