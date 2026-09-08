"""Deterministic task routing and safe parallel dispatch planning."""

from __future__ import annotations

import tomllib
import re
from pathlib import Path
from typing import Any


KINDS = {"architecture", "implementation", "review", "research"}
COMPLEXITIES = {"simple", "standard", "complex"}
RISKS = {"low", "high"}
UNCERTAINTIES = {"low", "high"}
ROLES = {"kernel", "astra", "sol", "terra", "luna"}


def _profiles(project_root: Path) -> dict[str, dict[str, str]]:
    codex = project_root / ".codex"
    config_path = codex / "config.toml"
    config: dict[str, Any] = {}
    if config_path.is_file():
        with config_path.open("rb") as stream:
            config = tomllib.load(stream)

    profiles: dict[str, dict[str, str]] = {}
    kernel = _profile(config)
    if kernel:
        profiles["kernel"] = kernel

    agent_dir = codex / "agents"
    for path in sorted(agent_dir.glob("*.toml")):
        with path.open("rb") as stream:
            data = tomllib.load(stream)
        profiles[path.stem] = _profile(data)

    defaults = config.get('agents', {})
    if isinstance(defaults, dict) and 'terra' not in profiles:
        fallback = {'model': defaults.get('default_subagent_model'),
                    'effort': defaults.get('default_subagent_reasoning_effort')}
        if all(isinstance(value, str) and value.strip() for value in fallback.values()):
            profiles['terra'] = fallback

    return profiles


def _profile(data: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in ("model", "effort", "reasoning_effort", "model_reasoning_effort"):
        value = data.get(key)
        if isinstance(value, str) and value:
            result["effort" if key in {"reasoning_effort", "model_reasoning_effort"} else key] = value
    nested = data.get("agent")
    if isinstance(nested, dict):
        result.update({k: v for k, v in _profile(nested).items() if k not in result})
    return result


def _validate_task(task: dict[str, Any], project_root: Path | None = None) -> None:
    if not isinstance(task, dict):
        raise ValueError("each task must be an object")
    required = {"id", "goal", "kind", "complexity", "risk", "uncertainty", "write_paths", "depends_on", "acceptance"}
    missing = sorted(required - task.keys())
    if missing:
        raise ValueError(f"task missing keys: {', '.join(missing)}")
    if not isinstance(task["id"], str) or not task["id"].strip():
        raise ValueError("task id must be a non-empty string")
    if not isinstance(task["goal"], str) or not task["goal"].strip():
        raise ValueError("goal must be a non-empty string")
    for key, allowed in (("kind", KINDS), ("complexity", COMPLEXITIES), ("risk", RISKS), ("uncertainty", UNCERTAINTIES)):
        if not isinstance(task[key], str) or task[key] not in allowed:
            raise ValueError(f"invalid {key}: {task[key]!r}")
    if not isinstance(task["acceptance"], list) or not task["acceptance"] or not all(isinstance(x, str) and x.strip() for x in task["acceptance"]):
        raise ValueError("acceptance must be a non-empty list of non-empty strings")
    if not isinstance(task["depends_on"], list) or not all(isinstance(x, str) and x.strip() for x in task["depends_on"]):
        raise ValueError("depends_on must be a list of task ids")
    if not isinstance(task["write_paths"], list):
        raise ValueError("write_paths must be a list")
    for raw in task["write_paths"]:
        if not isinstance(raw, str) or not raw or _canonical_path(raw, project_root) is None:
            raise ValueError(f"unsafe write path: {raw!r}")


def _canonical_path(raw: str, project_root: Path | None = None) -> str | None:
    normalized = raw.replace("\\", "/")
    if (
        not normalized
        or normalized in {"", "."}
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(char in normalized for char in ':*?[]"<>|')
        or any(ord(char) < 32 for char in normalized)
    ):
        return None
    parts = normalized.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        return None
    if any(part[-1] in ". " for part in parts):
        return None
    if any(part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))} for part in parts):
        return None
    canonical = "/".join(parts).casefold()
    if project_root is not None:
        root = Path(project_root).resolve()
        try:
            resolved = (root / Path(*parts)).resolve(strict=False)
            canonical = resolved.relative_to(root).as_posix().casefold()
        except (ValueError, OSError, RuntimeError):
            return None
    return canonical


def _profile_for(role: str, profiles: dict[str, dict[str, str]]) -> dict[str, str]:
    candidates = [role]
    if role == "kernel":
        candidates.append("astra")
    for candidate in candidates:
        profile = profiles.get(candidate)
        if profile and profile.get("model"):
            return profile
    raise ValueError(f"no configured model for role {role!r} in .codex/config.toml or .codex/agents")


def _choice(task: dict[str, Any]) -> tuple[str, str]:
    if task["kind"] == "architecture":
        return "kernel", "architecture task requires kernel planning"
    if task["risk"] == "high" or task["uncertainty"] == "high" or task["complexity"] == "complex":
        return "sol", "high risk, uncertainty, or complexity requires stronger review"
    if task["complexity"] == "simple" and task["risk"] == "low" and task["uncertainty"] == "low":
        return "luna", "small, bounded, low-risk task"
    return "terra", "standard implementation or analysis task"


def route_task(task: dict[str, Any], project_root: Path) -> dict[str, Any]:
    project_root = Path(project_root)
    _validate_task(task, project_root)
    role, reason = _choice(task)
    profile = _profile_for(role, _profiles(Path(project_root)))
    if not profile.get("effort"):
        raise ValueError(f"no reasoning effort configured for role {role!r}")
    return {
        "id": task["id"],
        "goal": task['goal'],
        "acceptance": list(task['acceptance']),
        "execution": 'kernel' if role == 'kernel' else 'delegate',
        "role": role,
        "model": profile["model"],
        "effort": profile["effort"],
        "reason": reason,
        "write_paths": list(task['write_paths']),
        "conflict_paths": [_canonical_path(path, project_root) for path in task["write_paths"]],
        "depends_on": list(task["depends_on"]),
    }


def _conflicts(left: list[str], right: list[str]) -> bool:
    for first in left:
        for second in right:
            a, b = first.split("/"), second.split("/")
            if a == b[: len(a)] or b == a[: len(b)]:
                return True
    return False


def _configured_parallel(project_root: Path) -> int | None:
    path = Path(project_root) / ".codex" / "config.toml"
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    value = data.get("max_parallel")
    dispatch = data.get("dispatch")
    if value is None and isinstance(dispatch, dict):
        value = dispatch.get("max_parallel")
    agents = data.get("agents")
    if value is None and isinstance(agents, dict):
        value = agents.get("max_concurrent_threads_per_session")
    if value is None:
        return None
    if not isinstance(value, int) or value < 1:
        raise ValueError("configured max_parallel must be a positive integer")
    return value


def validate_capabilities(plan: dict[str, Any], catalog: dict[str, Any]) -> None:
    """Validate a routed plan against a testable host capability catalog."""
    tasks = plan.get("tasks", [plan])
    for task in tasks:
        model = task.get("model")
        effort = task.get("effort")
        available = catalog.get(model)
        if available is None:
            raise ValueError(f"model unavailable: {model}")
        efforts = available.get("efforts", available) if isinstance(available, dict) else available
        if effort not in efforts:
            raise ValueError(f"effort {effort!r} unavailable for model {model!r}")


def plan_tasks(tasks: list[dict[str, Any]], project_root: Path, max_parallel: int = 3) -> dict[str, Any]:
    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or max_parallel < 1 or max_parallel > 3:
        raise ValueError("max_parallel must be between 1 and 3")
    configured = _configured_parallel(Path(project_root))
    if configured is not None:
        max_parallel = min(max_parallel, configured, 3)
    if not isinstance(tasks, list):
        raise ValueError("tasks must be a list")
    for task in tasks:
        _validate_task(task, Path(project_root))
    ids = [task["id"] for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("task ids must be unique")
    for task in tasks:
        for dependency in task["depends_on"]:
            if dependency not in ids:
                raise ValueError(f"missing dependency {dependency!r} for task {task['id']!r}")

    routes = [route_task(task, Path(project_root)) for task in tasks]
    by_id = {route["id"]: route for route in routes}
    remaining = set(ids)
    waves: list[list[str]] = []
    while remaining:
        ready = [task_id for task_id in ids if task_id in remaining and all(dep not in remaining for dep in by_id[task_id]["depends_on"])]
        if not ready:
            raise ValueError("dependency cycle detected")
        wave: list[str] = []
        for task_id in ready:
            if len(wave) >= max_parallel:
                break
            candidate = by_id[task_id]
            if any(_conflicts(candidate["conflict_paths"], by_id[current]["conflict_paths"]) for current in wave):
                continue
            wave.append(task_id)
        if not wave:
            raise ValueError("unable to form a non-conflicting dispatch wave")
        waves.append(wave)
        remaining.difference_update(wave)

    return {"tasks": routes, "waves": waves, "max_parallel": max_parallel}
