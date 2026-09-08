# Stages 3-5 reference

## Entities and invariants

For each entity answer: where data comes from, flow, owner, modifiers, lifecycle, state transitions, duplicates, and removable special cases. Map each invariant to a concrete DB constraint, transaction, compare-and-swap, lock, or queue-key serialization.

## API checklist

Specify resource, action, consistency, idempotency, pagination, versioning, security, rate limits, and observability. Long work returns `202` plus a `Location` for status. State error shape and boundary behavior instead of leaving them implicit.

## High-level flow

Use one numbered flow per functional requirement. For each step identify the component, its input/output, the guarantee, and the failure introduced. Keep the request journey minimal and explicit.
