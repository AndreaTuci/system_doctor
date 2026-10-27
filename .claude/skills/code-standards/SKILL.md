---
name: code-standards
description: "House style for how code should look: small files and functions, DRY/reuse over duplication, explicit over implicit, descriptive names, named constants, no dead code, no swallowed errors. Use when writing, adding, refactoring, or reviewing code."
author: Lotrek
version: 1.0.0
license: MIT
---

# Code Standards

The house style for **how code should look and be structured** here. It does not cover
*what* to build or the *process* for building it — only how the code itself should look.

## The taste test

Use the Zen of Python as the taste test for every change (if Python is handy, `python -c
"import this"` prints it). Each line maps to a concrete rule:

| Principle | What it means here |
|---|---|
| Explicit > implicit | Pass state through parameters and return values, not globals or hidden side effects. Name every tunable value — a named constant, never a bare literal. |
| Simple > complex | The simplest thing that works before the flexible/generic version. Prove a mechanism before you abstract it. |
| Flat > nested | Guard-clause and return early. Don't nest conditionals four deep. |
| Sparse > dense | One responsibility per file, per function. |
| Readability counts | Code is read far more than written — optimize for the next reader, not the keystroke. |
| Errors never pass silently | No empty `catch`/`except`. Fail loud in development; handle explicitly where it matters. |
| Refuse to guess | Ambiguous behaviour, value, or scope → stop and ask (see Hard constraints). |
| One obvious way | Reuse before you write. One home per concern. |
| Hard to explain ⇒ bad idea | If a design needs a paragraph to justify, reconsider it or cut it. |
| Namespaces are great | Group by concern; keep clear module boundaries. |

## Rules

**Small files and functions.** Target ≤ ~200 lines per file and ≤ ~40 lines per function
(adjust per project, but keep a budget). When a file outgrows it, extract helpers or split
by concern instead of letting it sprawl.

**Reuse & DRY.** Before writing, search for an existing helper, constant, type, or pattern —
the second copy of any logic is a bug, so extract one source. Match the idioms already in the
codebase rather than inventing parallel ones.

**Clean code.**
- Descriptive names; no abbreviations that need a comment to decode.
- No dead code, no commented-out blocks, no speculative "might need it later" generality.
- No magic numbers in logic — name them, and promote shared ones to a constants module.
- Comments explain *why*, never *what* the code already says.

## Antipatterns — don't ship these

**Structure & state**
- Global mutable state for things that should be passed explicitly.
- A god-object or a sprawling `if/else` state blob → separate the concerns, or model
  non-trivial state as an explicit state machine.
- Copy-paste across modules → extract a shared helper or base.

**Performance & lifecycle**
- Needless work in a genuinely hot path (a tight loop, a per-request path) — but optimize only
  once profiling shows it matters, and never by sharing mutable state across concurrent work.
  (Reusing instances / object pooling is a game/embedded/GC-free tactic, not a general rule —
  on most backends it fights the garbage collector and invites data races.)
- Leaked listeners, timers, or subscriptions on long-lived objects → clean them up on teardown.

**Scope & abstraction**
- Premature abstraction — a generic framework before the thing it generalizes is proven.
  Prototype first.
- Silent scope creep — a new idea mid-task → record it as deferred work, don't fold it in.

## Tests

Light tests by default: when a human can run the change, trust them as the tester — tell them
what to test and what to report back. But leave at least one runnable check on non-trivial pure
logic (a parser, an algorithm, a money or security path) and on library code with no human in
the loop — there, "the human is the tester" quietly means nobody is. Write heavy tests only when
explicitly asked. When you do write tests, follow the same rules: small, explicit, readable, no
duplication.

## Hard constraints

- **If in doubt, ask — never assume.**
- **Don't overstep the task's scope.** You can suggest, but don't implement beyond what's
  confirmed.
- When you do implement, obey the rules above: small, explicit, readable, reuse over duplication.
