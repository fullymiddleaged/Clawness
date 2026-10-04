---
name: messaging
description: >
  Write or rewrite the product's customer-facing copy — landing page, hero section,
  CTAs, onboarding, empty states, pricing, app-store and marketing pages — with the
  customer as the hero and the product as their guide. Scans the existing UI copy,
  drafts a one-page message brief (who the customer is, what they want, the problem in
  their words, the plan, the call to action, what's at stake, what success looks like),
  and stops for approval before changing any copy. Use when asked to improve marketing
  or UI copy, write a landing page, sharpen a value proposition, or when copy talks
  about the product instead of the customer.
---

# Customer-as-hero messaging

Most product copy describes the product: its features, its stack, its history. People
skim past that, because they're looking for their own problem. This skill rewrites
copy as a short story where the **customer is the hero**, the **product is the guide**
that helps them win, and every screen answers "what's in it for me?" within a few
seconds.

**Say this before you start.** Copy changes are visible to every user. Whether the user
typed `/clawness:messaging` or you reached for this skill yourself: **scan, draft the
brief, and stop for approval before editing any copy.**

## Argument

`/clawness:messaging [surface]`. The surface is what to work on: `landing`, `onboarding`,
`pricing`, `empty-states`, a route, or a file. With no argument, find the
highest-traffic surface (usually the landing page or first-run screen) and propose
starting there.

## The brief (seven parts, one line each)

| Part | Question it answers | Bad sign |
| --- | --- | --- |
| **Hero** | Who is the customer, and the ONE thing they want here? | "Everyone"; several wants |
| **Problem** | What's in their way: the external obstacle, how it makes them feel, why it's unfair | Only the external problem; nobody buys a fix for a feeling they weren't shown |
| **Guide** | Why trust us: empathy ("we get it") plus proof (numbers, logos, reviews) | Bragging with no empathy, or empathy with no proof |
| **Plan** | Three or fewer steps from where they are to using it | A feature list instead of steps |
| **Call to action** | One direct CTA ("Start free") plus one low-commitment one (a guide, a demo) | Several competing primary buttons; "Learn more" as the only CTA |
| **Stakes** | What it costs them to do nothing | Fear-mongering, or nothing at all |
| **Success** | Their life after: concrete, visual, specific | "Unlock your potential" |

The brief is the deliverable that matters. Copy is just the brief applied to a screen.

## Steps

### 1. Scan the existing copy

Find where copy lives: route files and components for hero sections and CTAs, i18n
message catalogs (the best source: every user-facing string in one place), CMS or
Markdown content, `<title>`/meta descriptions, app-store listings. Read the README and
any docs for who the product is for. Collect the current headline, subhead, primary
CTA, and section headings for the surface.

### 2. Draft the brief, then stop

Fill the seven-part table from what the code and docs actually say. Where they don't
say, **don't invent customers or proof**: ask. A testimonial, a stat, or a customer
count you can't find in the source isn't proof, it's fabrication. Present:

- the brief,
- a before/after for the headline, subhead and primary CTA,
- the files you'd change.

**Edit nothing until the user approves.**

### 3. Apply it to the surface

- **Headline**: what the customer gets, in their words. Readable in five seconds.
  Not the product name, not a pun.
- **Subhead**: the problem it solves, or the plan in one line.
- **Primary CTA**: one, repeated at the top and the end of the page and in the header.
  A verb plus the outcome ("Start my free trial"), never "Submit" or "Learn more".
- **Sections**: problem → guide (empathy + proof) → plan (≤3 steps) → success → stakes,
  then the CTA again. Trim anything that serves none of these.
- **UI copy**: empty states name the next step and its payoff ("No projects yet:
  create one to see your first report in a minute"). Onboarding is the plan. Error
  messages say what to do next, not what went wrong internally.
- Keep the existing design system, components, and i18n keys. Change strings, not
  structure, unless the user approved a structural change.

### 4. Verify

- Grep each changed string back to confirm it landed where intended and that every
  locale's catalog is updated (or flagged for translation).
- Five-second test: read only the headline, subhead and CTA. Can you say what's
  offered, why it matters, and what to do next? If not, rewrite.
- Report what changed, with before/after for the headline and CTA.

## Don't

- Don't make the product the hero ("We're the leading…"). The customer is.
- Don't invent testimonials, stats, logos or customer counts. Proof comes from source
  or from the user.
- Don't stack primary CTAs, or use clever wording where clear wording works.
- Don't change copy before the brief is approved, even when auto-invoked.
- Don't restructure layouts or swap components to fit a template. Fit what's there.
