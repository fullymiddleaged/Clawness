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
- Plugin skills/hooks load from ~/.claude/plugins/cache/clawness/<version>, so new skills can't be smoke-tested pre-push.
- Defender blocks pip's console-script shim (clawness.exe): "Access is denied". Test packaging via `python -m clawness.cli`.
- In tests, `shutil.which("bash")` finds WSL bash, which resolves no python (no .exe); prefer Git Bash — that's what runs hooks.
- ${CLAUDE_PLUGIN_ROOT} is EMPTY in skill/command Bash (only set in hook/MCP JSON; upstream #9354); skills reach the CLI via ensure_deps' stashed clawness-cli.sh.
- agents/ and skills/ .md carry version-pinned standards (OWASP edition, framework checks) invisible to the staleness mechanism (rules/ only) — review by hand.
- Smoke-test the OpenClaw adapter offline via `npm run plugin:check` (@openclaw/plugin-inspector); never pull the 87MB `openclaw` pkg for plugin-test-api.
- Verify OpenClaw SDK type shapes via throwaway `npm i openclaw`+tsc against real .d.ts (not a build dep); plugin-test-api is excluded from the npm pkg.
- OpenClaw git install reads the clone ROOT only (no subdir); the subdir plugin needs root package.json+openclaw.plugin.json + committed openclaw/dist/src.
- OpenClaw 2026.3.11 `plugins install` has NO git source (path/archive/npm/-l link only); git-source install is a newer-version feature — test old versions via `-l ~/clone/openclaw`.
