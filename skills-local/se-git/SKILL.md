---
name: se-git
description: Apply a safe, reviewable Git workflow for branches, commits, pushes, pull requests, and recovery from history operations.
---

# Git workflow

Before code work run `git branch --show-current` and `git status`; inspect recent commit style before committing. Work on a feature branch named `<type>/<short-description>` when the repository workflow permits it. Keep one concern per commit, use an imperative subject of about 50 characters, and put why/trade-offs in the body. Keep code and its changed documentation synchronized.

Before commit inspect the diff, tests, secrets, and status. Before a PR verify the commit range, diff, tests/lint, and clean status; describe what/why/verification/known limits. Use worktrees only for genuinely concurrent slices and clean them after integration.

For reset, rebase, branch deletion, force push, or other history surgery, first preserve a recovery point with an annotated `backup/<branch>-<YYYY-MM-DD>` tag and record the recovery path. Prefer `--force-with-lease`. Follow the current user authorization: ask only when the action is destructive, ambiguous, or would publish externally; do not silently push, merge, or open a PR. Never commit secrets, `.env`, credentials, or unrelated files.
