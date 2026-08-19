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
- OpenClaw `definePluginEntry`/plugin-entry SDK needs host >=2026.3.24-beta.2; 2026.3.11 lacks it entirely (nowhere in dist) — host too old, not an import bug.
- Import OpenClaw SDK from focused subpath `openclaw/plugin-sdk/plugin-entry`; the root `openclaw/plugin-sdk` barrel is deprecated per its CHANGELOG (scheduled removal).
- OpenClaw `git:` install takes `@<ref>`/`#<ref>` (branch/tag/commit); test a branch via `git:owner/repo@branch`. `-l` link can't test clone-pruning (check A) — only a real git install can.
- Live pass: OpenClaw HONORS before_tool_call `block` (tool doesn't run, blockReason reaches model) and `requireApproval`; checks C+D+E green (A green earlier).
- OpenClaw tool events: write=`{toolName:"write",params:{path,content}}`, read=`{toolName:"read",params:{path}}` — translate.ts mapToolCall guesses map them correctly.
- Can't model-bait an OpenClaw deny probe: opus-4-8 refuses IMDS/exfil/escape actions before before_tool_call fires. Prove `block` via forced-block A/B on a BENIGN write (file created without vs absent with).
- OpenClaw plugins register hooks/commands/tools, not skills/agents; port CLI skills as commands (openclaw/COMMANDS-PLAN.md).
- Git install keeps the whole clone (hooks/rules/clawness present at install root), so no engine vendoring needed — clone-pruning fear resolved.
- OpenClaw plugin command: `api.registerCommand(def)`, invoked as bare `/name` in a GLOBAL namespace (no auto `clawness:` prefix) — prefix ours `clawness-`.
- OpenClaw reserves command names (`status`,`context`,`model`,`config`,`skill`,`help`…); `status` is blocked. Set `acceptsArgs:true` or the matcher drops any args.
- OpenClaw command result `{continueAgent:true}` continues the turn to the LLM (handler may rewrite body); handler also has `ctx.runtimeContext.llm.complete` — so add/refresh CAN be commands.
- Live: `openclaw plugins inspect clawness --runtime --json` confirms `commands:["clawness-status"]` registers. To live-test a dist change, copy dist/src/*.js into the install clone `~/.openclaw/git/git-93ab38b0bc060a29/repo/openclaw/dist/src`.
- `openclaw agent --local` does NOT run the plugin-command interceptor — `/clawness-status` went to the LLM (it paraphrased injected context). Same CLI gap as SessionStart notes; verify command reply on a real channel.
