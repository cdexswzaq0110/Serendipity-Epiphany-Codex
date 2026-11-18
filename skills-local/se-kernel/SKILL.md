---
name: se-kernel
description: Plan, route, coordinate, and accept a Codex development task when it needs explicit scope, model selection, delegation, or integration.
---

# Kernel

Define the requested outcome, affected surface, authorization boundary, and observable acceptance condition before dispatching work. Inspect the relevant code first.

Route architecture, material conflicts, and final acceptance to Astra; difficult or high-risk implementation to Sol; normal scoped implementation to Terra; simple bounded checks or low-risk changes to Luna. Confirm the host supports the chosen model and reasoning level before relying on it.

Delegate only work that is independently useful. Every worker brief names its goal, necessary inputs, write scope, dependencies, verification, stop condition, and output. Parallel writers need disjoint scopes; serialize shared files, schemas, lockfiles, and Git operations. Read [the dispatch reference](references/dispatch.md) when coordinating multiple workers or resolving scope conflicts.

Integrate only results with evidence from the deliverable. Repair from a reproduced failure. Report verified outcomes and unknowns without turning routine tasks into mandatory design or review ceremonies.

For standalone execution, `se.py run` consumes a fixed task contract and `se.py agent` consumes a goal contract. Read [execution contracts](../../../docs/EXECUTION.md) when using these entrypoints; do not assume `plan` dispatches a model.
