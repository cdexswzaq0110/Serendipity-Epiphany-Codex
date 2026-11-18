# Dispatch reference

Use a short contract:

```text
Goal:
Inputs:
Write scope:
Dependencies:
Verification:
Stop condition:
Return:
```

`Depends on` means a result or decision is required first. A shared write location is a lock: serialize its writers without inventing a dependency chain. If scopes overlap or cannot be named, keep the work with one writer.

Record the requested model, host-reported model when available, routing reason, retry count, and verification result. Missing runtime data is `unknown`, not an inferred value.
