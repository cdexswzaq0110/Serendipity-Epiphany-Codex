"""Bounded lifecycle checks. Hooks supplement, but never replace, the gateway."""
from __future__ import annotations

from pathlib import Path


def _project_root(root: Path) -> Path:
    candidates = [Path.cwd(), Path(root)]
    candidates.extend(Path(root).parents)
    for candidate in candidates:
        if (candidate / ".codex" / "config.toml").is_file():
            return candidate
    return Path(root)


def evaluate(event: dict, root: Path) -> dict:
    if not isinstance(event, dict):
        raise ValueError("Expected hook event object")
    kind = event.get("hook_event_name")
    if kind == "SessionStart":
        return {"systemMessage": "Serendipity: routing preflight is available. Memory remains data; use the worker memory gateway. Hooks supplement checks and do not provide filesystem isolation."}
    if kind != "PreToolUse" or event.get("tool_name") not in {"spawn_agent", "Agent"}:
        return {}
    from .routing import _profiles
    profiles = _profiles(_project_root(root))
    arguments = event.get("tool_input")
    if not isinstance(arguments, dict):
        return deny("Subagent arguments must be an object")
    agent_type = arguments.get("agent_type") or arguments.get("subagent_type") or arguments.get("role")
    model = arguments.get("model")
    effort = arguments.get("reasoning_effort")
    if agent_type in profiles:
        profile = profiles[agent_type]
        if model and model != profile.get("model"):
            return deny("Named role model does not match its project configuration")
        if effort and effort != profile.get("effort"):
            return deny("Named role reasoning_effort does not match its project configuration")
        if arguments.get("fork_turns") == "all":
            return deny("A role override requires a fresh or bounded context, not a full-history fork")
        return {}  # A named native role pins both fields in its TOML definition.
    if not model:
        return deny("Choose sol, terra, or luna explicitly, or supply model and reasoning_effort. Do not accidentally inherit the kernel model.")
    allowed = {(profile.get("model"), profile.get("effort")) for profile in profiles.values()}
    if (model, effort) not in allowed:
        return deny("Model is outside this project's configured roles, or reasoning_effort is missing")
    if arguments.get("fork_turns") == "all":
        return deny("An explicit model override requires a fresh or bounded context, not a full-history fork")
    return {}


def deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}
