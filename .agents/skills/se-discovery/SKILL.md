---
name: se-discovery
description: Use for ambiguous or broad work where "done" cannot yet be stated observably; build an evidence-backed decision frontier before design or implementation.
---

# Requirements discovery

Use this skill only when the endpoint is unclear or the work spans unresolved decisions. If observable acceptance is already clear, go directly to system design.

## Working loop

1. Name the endpoint in one sentence, then breadth-scan the repository and relevant evidence.
2. Create `docs/maps/<topic>.md` with: known facts (`fact/evidence/source`), decision nodes (`question/type/status/blocks/answer`), and a current frontier.
3. Type every node as `fact`, `trade-off`, `feasibility`, or `scope`. Facts require sources; trade-offs and scope decisions belong to the human; feasibility can use a throwaway prototype.
4. Resolve exactly one frontier node at a time, record the answer and evidence, then recompute what is unblocked. Do not pre-slice uncertainty into implementation tickets.
5. Stop when the frontier is stable and the endpoint is observable. Hand off the endpoint, decisions, evidence, and non-blocking open questions to design.

## Guardrails

Keep the map as the durable state. If the frontier does not shrink for two rounds, a resolved node unlocks nothing, or the map exceeds roughly 30 nodes, split or escalate. Discovery produces decisions and a map, never code or a fake specification.
