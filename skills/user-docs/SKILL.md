---
name: user-docs
description: >
  Write user documentation for a project by scanning its codebase — its UI screens
  and its public API/CLI — inferring what someone can actually do with it, and drafting
  brief, task-oriented docs to the Diátaxis standard (tutorial / how-to / reference /
  explanation). Handles both non-technical end-user docs and developer docs. Use when
  asked to document the app, write user or API docs, generate a getting-started guide,
  or when the project's docs are missing or out of date. Scans and proposes an outline,
  then writes only after you approve it.
---

# Write user documentation from the codebase

Good docs describe what the software actually lets someone do. This skill builds that
picture from the code — the UI screens and the public API/CLI — then writes it up to a
known modern standard, kept brief.

**Two audiences, one standard.** Software usually needs one or both:

- **End-user docs** — for the person using the product. Driven by the **UI**: screens,
  buttons, forms, settings. Non-technical, task-first.
- **Developer docs** — for someone integrating with or extending it. Driven by the
  **public API/CLI/config**: exports, endpoints, commands, flags, env vars.

Both are structured with **[Diátaxis](https://diataxis.fr)** — the framework Django,
Cloudflare, and Gatsby use — and written in **Google-style prose** (second person,
active voice, present tense, sentence-case headings, short sentences).

**Say this before you start writing.** This can produce a lot of files and it takes a
while. Whether the user typed `/clawness:user-docs` or you reached for this skill on
your own, the discipline is the same: **scan, propose an outline, and stop for approval
before writing anything.** Never author a docs tree uninvited.

## Argument

`/clawness:user-docs [end-user|dev]` — which audience to document. With no argument,
detect what the project has (a UI, a public API/CLI, or both) and ask which to produce.
You can do both — run the flow once per audience.

## The standard

Structure every page by its **Diátaxis mode**, and keep one mode per page:

| Mode | Answers | Shape |
| --- | --- | --- |
| **Tutorial** | "get me started" | one guided first task, end to end |
| **How-to guide** | "how do I do X" | numbered imperative steps for one real task |
| **Reference** | "what is X exactly" | exhaustive, consistent, look-up-able |
| **Explanation** | "why does it work this way" | a concept and its rationale |

Never blend a tutorial with reference on one page — the reader is there for one thing.
For an existing product, weight the effort toward **how-to guides and reference**; add
explanation only where a concept genuinely needs it.

**Brevity is the standard, not a nicety.** Lead with the task. Cut any sentence that
restates the heading, the UI, or the code. A how-to step is an imperative ("Click
**Export**"), not a paragraph.

## Steps

### 1. Find where docs live, and fit it

Detect the existing docs setup and write into it rather than imposing a new one:
MkDocs (`mkdocs.yml`), Docusaurus (`docusaurus.config.*`), Sphinx (`conf.py`), Astro
Starlight, VitePress, a plain `docs/` tree, or README-only. If none exists, propose a
`docs/` tree of Markdown files. Do not install a docs framework.

Read the current docs first — you are updating, not duplicating. The README usually
already covers install and a first run; reference it, don't repeat it.

### 2. Build the capability inventory (the scan)

Before writing a word, work out what the software does. Run `/clawness:status` (or the
bundled CLI wrapper) to read the detected stack so you know where routing, CLI, and
config live, then explore with Grep/Glob/Read.

**End-user surface (from the UI):**
- **Screens** — file-based routing (`app/`, `pages/`, `routes/`) or a router config
  (React Router, Vue Router).
- **User-visible strings** — headings, button labels, form fields, nav and menu items,
  `aria-label`/`placeholder`. **i18n message catalogs are the jackpot**: they enumerate
  every user-facing string in one file.
- **Actions → capabilities** — a form submit, a button handler, a mutation is a task
  the user can perform ("you can export a report"). Settings screens become the
  configuration reference.

**Developer surface (from the public API):**
- **Entry points** — `package.json` `exports`/`bin`, public functions/classes, HTTP
  route handlers, CLI commands and flags (argparse / click / commander / cobra), SDK
  surface, extension points (hooks, plugins, interfaces).
- **Setup** — install steps, env vars, config files, build and run scripts.

Cluster the result into a **capability map**: task → where it lives in the UI or API.
That map is what you turn into pages.

### 3. Propose the outline, then stop

Present the proposed Diátaxis tree as a table — each page with its mode and a one-line
intent — plus where the files will go. **Write nothing until the user approves.** This
is the gate that makes an auto-invoked run safe.

```
docs/
  getting-started.md      tutorial   — first run, one task end to end
  guides/export-report.md how-to     — export a report as CSV
  guides/invite-user.md   how-to     — invite a teammate
  reference/screens.md    reference  — every screen and what it does
  reference/settings.md   reference  — every setting, its default and effect
```

### 4. Write the approved pages, one at a time

For each approved page, in the standard:
- Open with the task or the thing being defined — no preamble.
- How-to guides are numbered imperative steps. Reference is complete and consistent.
- **Ground every claim in source.** A button label, route, command, flag, or env var
  you write must match the actual string in the code, verbatim. If you can't find it in
  the code, don't document it — you're inventing a feature.
- Keep each page short. If a reference page sprawls, split it by area, don't pad it.

### 5. Verify

- Grep each cited label / route / command / flag back against the source and confirm it
  exists exactly as written. This is the check that catches a hallucinated feature.
- Confirm internal links resolve and any new pages are wired into the docs nav/index.
- Re-read for length: cut what the code or a heading already says.
- Report what you wrote and what remains, and remind the user new docs pages don't
  affect anything until they commit them.

## Don't

- Don't write before the user approves the outline — auto-invocation still stops at the
  gate.
- Don't document a screen, command, or flag you can't find in the code. No invented surface.
- Don't write walls of prose. One mode per page, task first, imperative steps.
- Don't duplicate the README, and don't reword existing docs while relocating them —
  move first, trim in a separate pass if asked.
- Don't install a documentation framework or restructure an existing one to match a
  template. Fit what's there.
