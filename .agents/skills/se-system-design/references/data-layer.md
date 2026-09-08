# Data layer reference

Start from storage and access patterns: read/write ratio, query shapes, growth, retention, and transaction boundaries. Choose indexes and query plans before caches; shard only when scale requires it. A shard key must have high cardinality, distribute evenly, and match dominant queries. Keep strongly consistent invariants on one shard where possible.

Document replication mode and read-after-write behavior. Make a per-path consistency table and map every invariant to its protection. Treat hot reads, contention, write bursts, and maintenance as distinct symptoms: consider cache/read replica/materialized view; atomic update/lock/queue by key; queue/batch/backpressure; partition/archive/backup respectively before adding generic capacity.
