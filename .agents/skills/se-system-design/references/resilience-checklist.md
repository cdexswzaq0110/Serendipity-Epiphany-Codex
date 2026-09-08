# Resilience and observability reference

Check resource competition, lock ordering, hot partitions, and tenant fairness. Overload controls may include rate limits, concurrency caps, queues, autoscaling, load shedding, prioritization, and backpressure; state which layer owns each one.

For delivery: retry only timeout/5xx, use bounded exponential backoff with jitter and idempotency keys, define failover/fallback, and make at-least-once consumers idempotent. Use a DLQ with alerting and reconciliation; plan partial cleanup. Measure golden signals plus p95/p99 and per-key/per-tenant views, then connect metrics to traces and logs without leaking secrets.
