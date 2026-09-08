---
name: se-debug
description: Debug a reproducible bug, failing test, unexpected behavior, or performance regression with measured hypotheses and a retained regression test.
---

# Debugging loop

Ground changes in a reproduced failure or concrete trace evidence. Prefer the smallest red case; remove conditions while preserving it. State a falsifiable mechanism and inspect its prediction before editing. When reproduction is unavailable, identify that uncertainty and choose a focused diagnostic. Fix the root cause, inspect affected callers, then rerun the failing case and retain a useful regression test.

After three unsuccessful fix attempts, stop. Record the likely wrong assumption, the options and their cost, and the next observable check. Consult official documentation, then local dependency source, then a relevant reference project; ask for the missing diagnostic rather than guessing.

Avoid guess-and-change, repeating an unchanged attempt, changing two places at once, swallowed exceptions, sleep-based flakiness, fixing only the named caller, or deleting the regression test. Completion requires red->green evidence, a named root cause, caller coverage, and a durable test; add a short Lesson when the three-attempt limit is reached.
