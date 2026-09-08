# Benchmark

`examples/benchmark.json` is a local, fixed-commit benchmark manifest. Each trial creates a fresh local Git clone, gives one bounded task to one harness/model arm, then runs the task's external acceptance command. The runner shuffles every case/arm/repeat combination with the recorded seed and retains its clone, raw execution report, check result, and summary under a new state directory.

The example has two real, reproducible gaps at commit `dcb1891d783866a4ab5e64fa08ab9d7c8ee92722`:

1. `routing-agent-defaults`: `routing.py` does not fall back to `[agents]` defaults when no role TOML exists.
2. `installer-runtime-link`: the installed memory-governance reference has no runbook link that resolves to the copied runtime document.

Every task includes the normal routing fields and `verify`, which names fixed external acceptance checks. The runner sends one task at a time with `max_attempts: 3`, `max_parallel: 1`, and `max_model_calls: 4`; the manifest supplies its own protected paths, duration limit, and token limit. Trial count is limited to 24. IDs use a safe filename character set before they are used in retained state paths. `max_seconds` must be 1–600 and `max_tokens` 1–250,000; this example uses 180 seconds and 250,000 tokens.

The example runs two cases once through `direct-luna`, `closed-loop-luna`, and `closed-loop-terra`, all at `low` effort: six executions, with up to three worker attempts each. The same-model, same-effort comparison is the primary result: direct Luna versus closed-loop Luna. Cross-model comparisons also hold harness and effort constant. A result never infers statistical significance or an overall winner.

Both arms begin at the same clean commit and load the same project `AGENTS.md`, skills, and native configuration. This measures a narrow **closed-loop versus single-pass** ablation under that shared base configuration. It does not compare the original Claude harness or establish every harness-level difference. An adapted original-recipe arm needs its own clearly labeled manifest and acceptance controls.

The executor always applies a verified patch to the benchmark's fresh, owned clone before this runner performs the fixed acceptance checks. It never applies to the source repository. No benchmark is started automatically; running one invokes model execution. The token setting is an observed soft stop: native usage events arrive after a turn, so a turn can overshoot the setting. If token usage is unknown for a model call, execution stops later dispatch and acceptance rather than treating it as zero. Summary usage reports `known_runs` and `unknown_runs` explicitly, including when every usage report is unknown.

The executor interface is supplied separately:

```python
execution.execute(
    manifest, project=owned_clone, tool_root=tool_root, state_root=state_root,
    mode="closed-loop", model_override=None, effort_override=None, apply=True,
)
```

The benchmark tests cover local fixed-commit validation, safe IDs and the 24-trial bound, retained raw reports, unknown usage, non-UTF-8 check output, the real execution apply path with a deterministic runner, and both example checks failing at baseline then passing after a manual correct repair. They do not call a model.
The current worker only receives a relevant skill index when the task is an architecture/review task or verification failure requires debugging. Earlier pilot results predate this narrowing and must be labeled accordingly. Each new benchmark saves a manifest snapshot and executor file hashes. Harness results include workflow and prompt/context changes; they do not isolate only the retry mechanism.
