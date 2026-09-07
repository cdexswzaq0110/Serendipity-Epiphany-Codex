# Memory data boundary

Normal lifecycle: `candidate → active → superseded`; a non-revoked version may be quarantined, while `revoked` is terminal. A correction creates a new revision.

Admission needs an independently recorded source and evidence; a worker cannot make its own memory trusted by asserting validation. Recall must reject memory outside scope or in any unavailable state. When storage is implemented, write a recall receipt transactionally before returning memory content. A failed receipt means no memory is delivered.

Record source ID, source version or hash, memory revision, applicable scope, invalidation condition, and policy version. These records show contact and lineage, not proof of the model's private reasoning.

When installed, the CLI shape is `python se.py memory <operation> --db <path> --scope <project> --json <file>`; `--json -` reads standard input. Read `docs/MEMORY_RUNBOOK.md` for operation-specific requirements. The default local operator uses governance mode. Workers use only the fixed-role `memory_call` MCP tool for candidate submission, recall, trace, and initial quarantine; they cannot activate, revoke, restore, or change policy. Keep source and candidate original text out of developer instructions.
