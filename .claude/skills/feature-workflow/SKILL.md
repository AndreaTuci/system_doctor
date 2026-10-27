---
name: feature-workflow
description: "Disciplined process for a multi-file or multi-session feature: keep PLAN and NOTES docs, build in phases (prototype, implement, review-before-commit), end with a suggested-commits table. Use when planning or phasing a non-trivial feature."
author: Lotrek
version: 1.0.0
license: MIT
---

# Feature Workflow

A disciplined way to build a **non-trivial feature** — one that spans many files or several
sessions. This owns *process only*: how to plan, implement, review, and hand off. It defers *how the code
should look* to your project's coding standards.

## 0. Default state: 🔴

At the start of a feature — and at the start of every phase — you are **blocked on the human**
until they explicitly green-light. No code, no commands, no commits before approval.
Pre-implementation push-back is the default posture: it catches real bugs before they're written.

## 1. Read first, in this order

1. **The spec / source of truth** for what to build — the ticket, design doc, or sketch. If it
   conflicts with the code, the spec wins unless the user re-scopes.
2. **Your project's coding standards** — how the code itself should look.
3. **This file** — the process.
4. **`PLAN-<feature>.md` and `NOTES-<feature>.md`** — the live plan and deviations log. If they
   don't exist yet, you're at Phase 0 (§2).
5. **The source-of-truth for this feature's behaviour** — re-read it *at each phase start*.
   Don't trust memory between phases.

## 2. Bootstrap a new feature (Phase 0)

Before any code, create two living documents at the repo root.

### `PLAN-<feature>.md` — the phase plan

```markdown
# Plan — <feature>

> Status snapshot: which phases are done / in flight / planned. Updated in real time.

- [x] Phase P1 — <one-line goal>
- [~] Phase P2 — <one-line goal>      ← "[~]" = in flight
- [ ] Phase P3 — <one-line goal>

## Phase P1 — <name>
**Goal**: one paragraph — what works at the end of this phase that didn't before?

### Files
- [ ] `path/to/file` — what changes / what's new.

### Decisions to settle at kickoff
1. Question (behaviour / value / scope / which existing pattern to extend).

### Out of scope for this phase
- Item explicitly deferred.
```

Conventions:
- Phase IDs are stable (`P1`, `P2`, `P3.5`…). Insert sub-phases with decimals; never renumber.
- Budget per phase: **≤ ~12 files**, each kept small (hold a per-file size budget). If a slice
  won't fit, split it.
- Each phase ships a **usable end-to-end slice** — even if rough — not a half-wired layer.
- Keep the top status snapshot in sync with the per-phase checkboxes.

### `NOTES-<feature>.md` — the deviations log

```markdown
# Notes — <feature>

> Per-phase decisions, deviations, deferred items. Append-only.

## Phase P1 — <name>
### Decisions settled
| # | Question | Resolution | Rationale |
|---|---|---|---|
| Q1 | Should X…? | Yes, do A. | Because B. |

### Deviations from plan
- Added `<file>` because <reason>.

### Deferred to later phases
- <Item> — moved to P<N> because <reason>.

## Cross-cutting deferred work
- <Item> — parked here, not folded into the current phase.
```

## 3. Per-phase loop

Every phase runs the same five steps. **Default at the start of each phase is 🔴.** Don't start
the next phase without an explicit green light.

### Step 1 — Re-read & kick off (🔴 → 🟡)
Re-read the source of truth for this phase. **Surface every open question before writing code** —
batch them in chat and wait for answers. Common categories: behaviour (what happens when X?),
values (numbers, limits, config), scope (where does the phase end?), conventions (which existing
pattern do we extend?). The user answers → record in `NOTES` under "Decisions settled" → 🟢.

### Step 2 — Prototype the unproven part first
If the phase introduces something whose behaviour or feel is uncertain, build it with the
simplest possible placeholder and get it working end-to-end *before* wiring polish, assets, or
adjacent systems. Validate the core, then invest. Skip this for proven or trivially CRUD-like work.

### Step 3 — Implement (🟢)
- Tick the phase to `[~]` the moment you start; tick per-file checkboxes as files land.
- **Follow the project's coding standards for every code decision** — small, explicit, DRY, no
  dead code, named constants over magic numbers.
- **Ask the user to run commands** (build, run, test) unless the session authorises autonomous shell.
- **Stay in scope.** A new idea mid-phase → park it in `NOTES` deferred, don't fold it in.
- **No new dependencies** without checking first.
- **Light tests by default** — the human's run-through is the test; still leave one runnable
  check on non-trivial logic or on library code with no human in the loop.
- Uncertain about behaviour / value / scope / which pattern → **stop and ask.** Silent guesses
  are the failure mode.

### Step 4 — Review-before-commit (🟡)
Before asking the user to commit, review the uncommitted work-set:
```
git status   # confirm scope
```
Audit each new/modified file for: dead code (defined, never used); useless indirection (inline
it); duplication (consolidate — DRY); fragile logic (swallowed errors, bad edge/null
assumptions); common antipatterns (duplication, global mutable state, premature abstraction,
swallowed errors); and inconsistency with sibling files. Report as two sections, then apply the
fixes:
```
### Fixes applied
1. `<file>:<line>` — <problem>. Fix: <action>.

### Reviewed and intentionally NOT changed
- <thing> — why it looks odd but is correct.
```

### Step 5 — Recap, suggested commits & checkpoint (🔴)
End every phase, in order:
1. **Recap**: files touched (grouped by area), deviations (link to the `NOTES` entry), and
   **what the user should test by hand — the happy-path run-through** (the exact steps: which
   command, what to do, what should happen), with edge cases listed separately.
2. **A suggested-commits table** (below) — the artifact the user commits from. **You never
   commit yourself.**
3. **Tick the phase to `[x]`** in `PLAN`.
4. **Append closure notes** to `NOTES`.
5. **🔴 awaiting approval.** State it. The user reviews, commits, then green-lights the next phase.

#### The suggested-commits table
A phase touches up to ~12 files; landing that as one fat commit destroys the history. Propose a
split the user runs verbatim — two columns, `git add` and `git commit`, one concern per row:

| `git add` | `git commit` |
|---|---|
| `git add src/config/limits.js` | `git commit -m "feat(config): add rate-limit constants"` |
| `git add src/lib/state-machine.js` | `git commit -m "feat(lib): reusable finite state machine"` |
| `git add src/features/checkout.js` | `git commit -m "feat(checkout): wire the state machine"` |
| `git add PLAN-checkout.md NOTES-checkout.md` | `git commit -m "docs(checkout): close phase P2"` |

Rules:
- **`git add` column** — explicit paths only, never `git add .` / `-A`. Globs OK for generated
  siblings. Each row must be independently `git add`-able exactly as written.
- **`git commit` column** — the full command, copy-paste-able (keep the message free of
  unescaped `"` and backticks). Message = `<type>(<scope>): <subject>` — type ∈
  `feat` · `fix` · `refactor` (behaviour-preserving) · `docs` · `chore` · `deps`; imperative,
  lower-case subject; scope short and consistent with the git log.
- **Split heuristics**: one concern per commit; dependency order top-to-bottom (a row never
  references what a later row introduces); assets/config travel with the code that uses them;
  docs / plan / notes get their own `docs(...)` row.

Close the table with: *"Review and adjust; I won't run any of these — commits are yours."*

### When a phase fails or reopens
If the human's run-through fails, or their commit review sends work back, the phase drops to 🟡
and re-enters Step 3 — it stays `[~]`, never `[x]`, until it genuinely lands. Log the failure
and the fix in `NOTES`. If a slice is abandoned rather than fixed, record that in `NOTES` too;
don't silently delete it.

## 4. The semaphore protocol

| Symbol | Meaning |
|---|---|
| 🔴 | Blocked on the human. You wait. Default at the start of every phase. |
| 🟡 | Working, but a checkpoint approaches (question batch, mid-phase pivot, review-before-commit). |
| 🟢 | Explicitly approved. You may proceed. |

Use these emoji literally in chat — they tell the user whether you stopped on purpose or went silent.

## 5. Hard rules

The non-negotiable spine of this workflow — code-quality rules live in your project's coding
standards, not restated here:

1. **Default 🔴** — start every feature and every phase blocked on the human.
2. **The spec is the source of truth**; adapt its intent to the existing conventions.
3. **Scope discipline** — new ideas go to `NOTES` deferred or an explicit re-scope, never silent
   creep; no new dependencies without asking.
4. **Ask if in doubt** — don't assume, don't guess.
5. **The agent never commits.** You propose the split (§3, step 5) and ask the user to run
   commands; the human runs every `git add` / `commit` / `push`, unless the session explicitly
   authorises otherwise.

## 6. When to deviate

Methodology, not law:
- **One-file fixes** — skip the phase plan; still do the review pass and produce a single-row
  commit table.
- **Spike / exploration** — the user asks to "just see if this is possible": skip the planning
  machinery, and say so in your recap.
- **Autonomous mode** — the user can override "ask if in doubt" for a session ("go, don't ask").
  Still do the review pass and still produce the commit table. Autonomous mode relaxes rule 4
  (ask if in doubt), never rule 5 (the agent never commits) — the human still owns every commit.

If you're unsure whether to deviate — ask.
