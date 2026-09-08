---
name: se-two-axis-review
description: Review a completed change on separate specification and engineering-standards axes using one pinned snapshot and evidence-backed findings.
---

# Two-axis review

Pin the reviewed content: resolve a branch/tag to a commit SHA and identify its comparison base, or save the working diff and its hash. Review the same content on two axes independently:

- **Spec:** deliverables and acceptance, behavior, omissions, scope creep, errors/boundaries/failure paths, and observable output.
- **Standards:** evidence and testability, data flow/names/boundaries/public interfaces, duplication/abstraction/coupling, project rules, security, performance, compatibility, and the conditional smell baseline.

Use issue/user-path/spec evidence in that order; if no specification exists, say so. Read repository rules and [smell baseline](references/smell-baseline.md) for Standards unless explicit project rules replace it. Do not let one axis influence the first pass of the other.

Report each finding as `[blocker|important|suggestion] file:location - issue; evidence and evidence grade; impact; minimal fix`. Severity and evidence strength are separate. Zero findings is valid. Findings are claims for the developer to reproduce: close or withdraw them with counterevidence or a minimal failing test. Keep `## Spec` and `## Standards` outputs separate; do not rank one axis against the other.

The review is complete when both passes used the recorded content and findings are located and evidenced. The developer then resolves or disputes actionable findings with evidence. The reviewer stays read-only unless the task explicitly includes fixes; do not manufacture findings to fill either axis.
