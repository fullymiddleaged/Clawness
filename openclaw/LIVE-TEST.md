# Live host smoke test — the one thing offline checks can't settle

The offline layers (`npm test`, `npm run plugin:check`, out-of-tree `tsc` against the real
`.d.ts`) confirm the shapes we **return** and that the hook names/manifest resolve. Only a
running host confirms the values the host **passes** us and whether it honours what we
return. This runbook drives that pass and tells you exactly what to report back.

Design context and the full "why" for each item is in [CLAUDE.md](CLAUDE.md) → *Open —
needs a live host*.

## Prerequisites

- **Node ≥ 22.22.3** (OpenClaw's engine floor — stricter than this package's `>=22.19`).
  Check: `node --version`.
- **Python 3.10+ on PATH** as `python3`, `python`, or `py`. Without it the adapter injects
  the "Python not found" note instead of rules — so if that note is all you see, fix PATH
  first, it isn't a hook bug. Check: `python3 --version`.
- **A model-provider API key** for `openclaw onboard`.
- A **git-tracked project** to run the test prompt in (this repo works; any repo with
  Python reachable is fine).

## Setup — test the REAL install a user would run

The plugin is git-installable from this repo: the repo root carries the OpenClaw
discovery files (`package.json`'s `openclaw` block + `openclaw.plugin.json`) pointing at
the committed prebuilt entry `openclaw/dist/src/index.js`, and the clone brings the Python
engine (`hooks/`, `rules/`, `clawness/`) along with it. **Test that path, not `--link`** —
`--link` runs from your working copy inside the full checkout and hides packaging bugs.

```bash
# 1. Install OpenClaw and onboard (enter the API key when prompted).
npm i -g openclaw
openclaw onboard

# 2. Install Clawness the way a user would — from GitHub.
openclaw plugins install git:github.com/fullymiddleaged/Clawness

# 3. Restart the gateway and prove the runtime registrations.
openclaw gateway restart
openclaw plugins inspect clawness --runtime --json
```

Step 3's `inspect --runtime` loads the module and lists the surfaces it actually
registered. **Confirm all four hooks appear**: `before_prompt_build`, `session_start`,
`before_tool_call`, `after_tool_call` — plus `onStartup` activation. Save that JSON; paste
it back.

> **Dev fallback only:** `cd openclaw && npm install && npm run build`, then from the repo
> root `openclaw plugins install --link ./openclaw`. Use this to iterate on the adapter —
> it does NOT prove the packaged install, so don't sign the release off on it.

### ⚠️ The load-bearing packaging unknown — check this FIRST

The whole "no engine vendoring" design rests on one runtime fact offline checks can't
settle: **does the installed plugin still contain the sibling Python engine?** The adapter
shells out to `../hooks/claude_hook.py` relative to its own location
([bridge.ts](src/bridge.ts) `REPO_ROOT`). That works only if the installer keeps the whole
clone as the plugin directory. If OpenClaw instead prunes the install to just the declared
entry (dropping `hooks/`, `rules/`, `clawness/`), every hook spawn fails silently and the
plugin does nothing — the same silent-failure signature as a Python-less box.

So before the checklist below, from the installed plugin directory confirm the engine is
present: `openclaw plugins inspect clawness --runtime --json` should show it loaded, and if
you can locate the install dir, check that `hooks/` and `rules/` sit one level above
`openclaw/dist/src/`. If they're gone, the fix is to vendor the engine into the package
(the rejected "dedicated repo" option) — report that and stop; the other items are moot.

## The checklist — run ONE session, one prompt, in the git project

For each item report **pass/fail** and, where noted, the **actual field name you observed**
— those are what let me correct `resolvePromptText` / the resolvers in `src/index.ts` and
the keyword tables in `src/translate.ts` if the host differs from our assumptions.

- [ ] **A. Rules actually reach the model.** Send a normal coding prompt. The Clawness
  rules block (`--- CLAWNESS RULES ---` … mandatory rules) should appear in the model's
  context. **If it does NOT**, the host names the prompt field something outside
  `resolvePromptText`'s candidate list (`prompt`/`userPrompt`/`text`/`input`/`message`) —
  this is the one silent-failure path. Dump the `before_prompt_build` event object and tell
  me **the real field the user's text arrives under**; I add it to the head of the list.
- [ ] **B. A SessionStart note renders.** Start a fresh session in the git repo. A
  `[Clawness]` note should appear on the first turn (changelog / stack / handoff / trust —
  whichever the repo triggers). This exercises the enqueue fix (`{sessionKey, text}`, not
  the old `appendContext`) and the default `placement`. Note **where** it renders.
- [ ] **C. Access-guard `ask` fires.** Take an ask-tier action and confirm an approval
  dialog appears — e.g. `git push --force` (not `--force-with-lease`), or a Write to a path
  **outside** the project root. Then confirm a **deny**-tier action blocks — e.g. a `curl`
  piping a local secret to a host absent from the repo. (`before_tool_call` runtime
  block/approval semantics is the inspector's one open proof-gap.)
- [ ] **D. Tool-name mapping is right.** Confirm OpenClaw's actual built-in tool names for
  shell / write / edit / read map through `mapToolCall` (item C firing at all proves the
  shell path; note the real tool names and their param field names from the event so I can
  extend the keyword/field tables in `translate.ts` if they differ).
- [ ] **E. `after_tool_call` settles the ask-ledger.** Approve one ask-tier action, then
  retry the *same* action in the same session — it should **not** re-ask (the ledger
  promoted it to confirmed). A declined-then-retried action **should** re-ask.

## Report back

Paste: (1) the `inspect --runtime --json` output, (2) pass/fail for A–E, and (3) for any
fail, the raw event object so I can read the real field names. Once A–C are green, the
feature is stable enough to release, and the version bump + merge to main (4 places +
CHANGELOG per the root CLAUDE.md — a push to main IS a release) is the last step.

---

CLI command sources (verified against current docs, August 2026):
[Manage plugins](https://docs.openclaw.ai/plugins/manage-plugins),
[Building plugins](https://docs.openclaw.ai/plugins/building-plugins).
