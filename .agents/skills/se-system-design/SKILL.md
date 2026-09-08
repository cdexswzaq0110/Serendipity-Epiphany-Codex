---
name: se-system-design
description: Turn an understood endpoint into a quantified, reviewable system design with contracts, invariants, failure handling, and explicit trade-offs.
---

# System design

Design from evidence before choosing components. Use the relevant stages: S0 domain and red lines; S1 requirements and scope; S2 workload estimates and bottleneck hypothesis when scale matters; S3 entities/invariants/state; S4 API contract; S5 one request journey; S6 the consequential bottleneck; S7 data layer; S8 resilience and observability; S9 trade-offs and limits. Skip stages that do not affect this decision; label estimates rather than inventing precision.

## Laws that change decisions

- Quantify before architecture; find the bottleneck before adding components.
- Give different paths different guarantees and minimize the synchronous cannot-fail path.
- Inspect p95/p99, per-key, and per-shard behavior; averages hide hot spots.
- For each layer name the new failure mode it introduces.
- Check index/query design before reaching for Redis or sharding.

## Required output

Produce enough of the above to make the design reviewable. For a consequential choice, record the problem, alternatives, reason, cost and verification. Explain how critical invariants are enforced; do not presume that every system needs a database or distributed architecture.

Read the focused references only when entering those stages: [entities and API](references/stages-3-5.md), [data layer](references/data-layer.md), [resilience](references/resilience-checklist.md), and [deliverable template](references/deliverable-template.md).
