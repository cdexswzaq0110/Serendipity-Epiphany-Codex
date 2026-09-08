"""Repeatable local benchmark runner for Codex execution harnesses."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import random
import re
import subprocess
import time
from typing import Any, Callable
from uuid import uuid4


class BenchmarkError(ValueError):
    """Raised when a benchmark manifest cannot be run safely."""


Execute = Callable[..., dict[str, Any]]
_HARNESS = {"direct", "closed-loop"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def load_manifest(path: Path) -> dict[str, Any]:
    """Load and validate a JSON benchmark manifest without running it."""

    manifest_path = Path(path).resolve()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BenchmarkError(f"Cannot read benchmark manifest: {manifest_path}") from error
    if not isinstance(data, dict):
        raise BenchmarkError("Benchmark manifest must be an object")
    _validate_manifest(data, manifest_path.parent)
    return data


def run_benchmark(
    manifest_path: Path,
    state_root: Path,
    *,
    execute_fn: Execute | None = None,
    tool_root: Path | None = None,
) -> dict[str, Any]:
    """Run every case in shuffled arm/repeat order and preserve raw reports.

    The runner creates a new owned subdirectory under ``state_root`` and never
    deletes clones or reports. The executor always applies its verified patch to the freshly
    created benchmark clone, never to a user repository.
    """

    path = Path(manifest_path).resolve()
    manifest = load_manifest(path)
    root = Path(state_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_root = root / _run_id()
    run_root.mkdir()
    reports = run_root / "reports"
    clones = run_root / "clones"
    reports.mkdir()
    clones.mkdir()

    executor = execute_fn or _load_executor()
    source_root = Path(tool_root).resolve() if tool_root else Path(__file__).resolve().parents[2]
    configuration = {
        'manifest': manifest,
        'manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'executor_files': {p.relative_to(source_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in (source_root / 'src/se_codex').glob('*.py')},
    }
    (run_root / 'configuration.json').write_text(json.dumps(configuration, ensure_ascii=False, indent=2), encoding='utf-8')
    trials = _trials(manifest)
    random.Random(manifest["seed"]).shuffle(trials)
    records: list[dict[str, Any]] = []

    for ordinal, trial in enumerate(trials, start=1):
        record = _run_trial(
            trial,
            ordinal,
            manifest,
            path.parent,
            clones,
            executor,
            source_root,
        )
        records.append(record)
        (reports / f"{ordinal:03d}-{record['trial_id']}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    result = {
        "run_id": run_root.name,
        "state_root": str(run_root),
        "manifest": str(path),
        "execution_apply": True,
        "seed": manifest["seed"],
        "records": records,
        "summary": _summarize(records),
    }
    (run_root / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return result


def _validate_manifest(manifest: dict[str, Any], base: Path) -> None:
    _required(manifest, "cases", list)
    _required(manifest, "checks", list)
    _required(manifest, "arms", list)
    _required(manifest, "repeats", int)
    _required(manifest, "seed", int)
    _required(manifest, "max_seconds", int)
    _required(manifest, "max_tokens", int)
    if manifest["repeats"] < 1:
        raise BenchmarkError("repeats must be positive")
    if not 1 <= manifest["max_seconds"] <= 600:
        raise BenchmarkError("max_seconds must be between 1 and 600")
    if not 1 <= manifest["max_tokens"] <= 250000:
        raise BenchmarkError("max_tokens must be between 1 and 250000")
    protected_paths = manifest.get("protected_paths", ["tests"])
    if not isinstance(protected_paths, list) or not all(isinstance(path, str) and path for path in protected_paths):
        raise BenchmarkError("protected_paths must be a list of non-empty paths")
    check_ids = _unique_objects(manifest["checks"], "check")
    for check in manifest["checks"]:
        _required(check, "id", str)
        _required(check, "argv", list)
        _required(check, "timeout_seconds", int)
        if not check["argv"] or not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in check["argv"]):
            raise BenchmarkError(f"check {check['id']!r} has an invalid argv")
        if not 1 <= check["timeout_seconds"] <= 300:
            raise BenchmarkError(f"check {check['id']!r} timeout_seconds must be 1..300")
    _unique_objects(manifest["arms"], "arm")
    for arm in manifest["arms"]:
        _required(arm, "id", str)
        _required(arm, "harness", str)
        _required(arm, "model", str)
        _required(arm, "effort", str)
        if arm["harness"] not in _HARNESS:
            raise BenchmarkError(f"arm {arm['id']!r} has an unsupported harness")
    _unique_objects(manifest["cases"], "case")
    if len(manifest["cases"]) * len(manifest["arms"]) * manifest["repeats"] > 24:
        raise BenchmarkError("benchmark exceeds the 24-trial resource limit")
    for case in manifest["cases"]:
        _required(case, "id", str)
        _required(case, "repo", str)
        _required(case, "commit", str)
        _required(case, "task", dict)
        if not _is_commit(case["commit"]):
            raise BenchmarkError(f"case {case['id']!r} must use an exact commit hash")
        repo = _local_repo(case["repo"], base)
        _resolve_commit(repo, case["commit"])
        task = case["task"]
        for key in ("id", "goal", "kind", "complexity", "risk", "uncertainty", "write_paths", "depends_on", "acceptance", "verify"):
            if key not in task:
                raise BenchmarkError(f"case {case['id']!r} task missing {key!r}")
        if not isinstance(task["verify"], list) or not task["verify"]:
            raise BenchmarkError(f"case {case['id']!r} task verify must name checks")
        if not isinstance(task["id"], str) or not _SAFE_ID.fullmatch(task["id"]):
            raise BenchmarkError(f"case {case['id']!r} task id is unsafe")
        unknown = set(task["verify"]) - set(check_ids)
        if unknown:
            raise BenchmarkError(f"case {case['id']!r} names unknown checks: {sorted(unknown)}")


def _run_trial(
    trial: dict[str, Any],
    ordinal: int,
    manifest: dict[str, Any],
    base: Path,
    clones: Path,
    executor: Execute,
    tool_root: Path,
) -> dict[str, Any]:
    case, arm, repeat = trial["case"], trial["arm"], trial["repeat"]
    trial_id = f"{case['id']}-{arm['id']}-r{repeat}"
    clone = clones / f"{ordinal:03d}-{trial_id}"
    started = _now()
    try:
        repo = _local_repo(case["repo"], base)
        commit = _resolve_commit(repo, case["commit"])
        _clone_at_commit(repo, commit, clone)
        check_map = {check["id"]: check for check in manifest["checks"]}
        checks = [check_map[check_id] for check_id in case["task"]["verify"]]
        execution_manifest = {
            "tasks": [deepcopy(case["task"])],
            "checks": deepcopy(checks),
            "integration_checks": [check["id"] for check in checks],
            "max_attempts": 3,
            "max_parallel": 1,
            "max_seconds": manifest["max_seconds"],
            "max_model_calls": 4,
            "max_tokens": manifest["max_tokens"],
            "protected_paths": deepcopy(manifest.get("protected_paths", ["tests"])),
        }
        raw = executor(
            execution_manifest,
            project=clone,
            tool_root=tool_root,
            state_root=clones.parent,
            mode=arm["harness"],
            model_override=arm["model"],
            effort_override=arm["effort"],
            apply=True,
        )
        if not isinstance(raw, dict):
            raise BenchmarkError("execution.execute must return an object")
        check_results = [_run_check(check, clone) for check in checks]
        return _record(trial_id, case, arm, repeat, clone, started, raw, check_results)
    except Exception as error:
        return _record(
            trial_id,
            case,
            arm,
            repeat,
            clone,
            started,
            {"status": "failed", "error": f"{type(error).__name__}: {error}"},
            [],
        )


def _record(
    trial_id: str,
    case: dict[str, Any],
    arm: dict[str, Any],
    repeat: int,
    clone: Path,
    started: str,
    raw: dict[str, Any],
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    passed = all(check.get("passed") is True for check in checks) if checks else False
    status = raw.get("status") if raw.get("status") in {"completed", "blocked", "failed"} else "failed"
    return {
        "trial_id": trial_id,
        "case_id": case["id"],
        "arm_id": arm["id"],
        "harness": arm["harness"],
        "model": arm["model"],
        "effort": arm["effort"],
        "repeat": repeat,
        "started_at": started,
        "clone": str(clone),
        "status": status,
        "accepted": status == "completed" and passed,
        "acceptance_defects": sum(check.get("passed") is False for check in checks),
        "unknown_checks": sum(check.get("passed") is None for check in checks),
        "duration_seconds": _number_or_none(raw.get("duration_seconds")),
        "usage": raw.get("usage") if isinstance(raw.get("usage"), dict) else None,
        "model_calls": raw.get("model_calls") if isinstance(raw.get("model_calls"), int) else None,
        "interventions": len(raw["interventions"]) if isinstance(raw.get("interventions"), list) else None,
        "checks": checks,
        "raw_execution": raw,
    }


def _run_check(check: dict[str, Any], project: Path) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            check["argv"], cwd=project, capture_output=True,
            timeout=check["timeout_seconds"], check=False,
        )
        return {
            "id": check["id"], "passed": completed.returncode == 0,
            "returncode": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 6),
            "stdout": _decode(completed.stdout), "stderr": _decode(completed.stderr),
        }
    except subprocess.TimeoutExpired as error:
        return {
            "id": check["id"], "passed": False, "returncode": None,
            "duration_seconds": round(time.monotonic() - started, 6),
            "stdout": _decode(error.stdout), "stderr": _decode(error.stderr), "error": "timeout",
        }
    except OSError as error:
        return {
            "id": check["id"], "passed": None, "returncode": None,
            "duration_seconds": round(time.monotonic() - started, 6),
            "error": f"{type(error).__name__}: {error}",
        }


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[(record["harness"], record["model"], record["effort"])].append(record)
    aggregates = [
        _aggregate(harness, model, effort, group)
        for (harness, model, effort), group in sorted(groups.items())
    ]
    return {
        "groups": aggregates,
        "harness_comparisons": _comparisons(aggregates, ("model", "effort"), "harness"),
        "model_comparisons": _comparisons(aggregates, ("harness", "effort"), "model"),
        "interpretation": "Descriptive aggregates only: no significance test or overall winner is inferred.",
    }


def _aggregate(harness: str, model: str, effort: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    known_durations = [record["duration_seconds"] for record in records if record["duration_seconds"] is not None]
    known_interventions = [record["interventions"] for record in records if record["interventions"] is not None]
    usage_keys = sorted({key for record in records if record["usage"] for key in record["usage"]})
    usage_metrics = {
        key: {
            "total": sum(record["usage"][key] for record in records if isinstance(record["usage"], dict) and isinstance(record["usage"].get(key), (int, float))),
            "known_runs": sum(isinstance(record["usage"], dict) and isinstance(record["usage"].get(key), (int, float)) for record in records),
            "unknown_runs": sum(not isinstance(record["usage"], dict) or not isinstance(record["usage"].get(key), (int, float)) for record in records),
        }
        for key in usage_keys
    }
    usage = {
        "known_runs": sum(isinstance(record["usage"], dict) for record in records),
        "unknown_runs": sum(not isinstance(record["usage"], dict) for record in records),
        "metrics": usage_metrics,
    }
    return {
        "harness": harness,
        "model": model,
        "effort": effort,
        "runs": len(records),
        "completed": sum(record["accepted"] for record in records),
        "completion_rate": sum(record["accepted"] for record in records) / len(records),
        "acceptance_defects": sum(record["acceptance_defects"] for record in records),
        "unknown_checks": sum(record["unknown_checks"] for record in records),
        "interventions": {
            "total": sum(known_interventions) if known_interventions else None,
            "known_runs": len(known_interventions),
            "unknown_runs": len(records) - len(known_interventions),
        },
        "duration_seconds": {
            "total": sum(known_durations) if known_durations else None,
            "known_runs": len(known_durations),
            "unknown_runs": len(records) - len(known_durations),
        },
        "usage": usage,
    }


def _comparisons(
    aggregates: list[dict[str, Any]], held_keys: tuple[str, ...], varying_key: str
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for aggregate in aggregates:
        grouped[tuple(aggregate[key] for key in held_keys)].append(aggregate)
    return [
        {"held_constant": dict(zip(held_keys, held)), "groups": values}
        for held, values in sorted(grouped.items())
        if len({item[varying_key] for item in values}) > 1
    ]


def _trials(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"case": case, "arm": arm, "repeat": repeat}
        for case in manifest["cases"]
        for arm in manifest["arms"]
        for repeat in range(1, manifest["repeats"] + 1)
    ]


def _clone_at_commit(repo: Path, commit: str, clone: Path) -> None:
    if clone.exists():
        raise BenchmarkError(f"Owned clone path already exists: {clone}")
    _git(["clone", "--no-hardlinks", "--no-local", "--no-checkout", str(repo), str(clone)], repo.parent)
    _git(["checkout", "--detach", commit], clone)


def _local_repo(raw: str, base: Path) -> Path:
    if "://" in raw or raw.startswith(("\\\\", '//')):
        raise BenchmarkError(f"Repository must be a local path: {raw!r}")
    path = Path(raw)
    repo = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if not repo.is_dir():
        raise BenchmarkError(f"Repository does not exist: {repo}")
    _git(["rev-parse", "--is-inside-work-tree"], repo)
    return repo


def _resolve_commit(repo: Path, commit: str) -> str:
    return _git(["rev-parse", "--verify", f"{commit}^{{commit}}"], repo).strip()


def _git(argv: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *argv], cwd=cwd, text=True, encoding="utf-8", capture_output=True, timeout=30, check=False
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise BenchmarkError(message)
    return completed.stdout


def _load_executor() -> Execute:
    try:
        from .execution import execute
    except ImportError as error:
        raise BenchmarkError("Execution interface is unavailable; provide execute_fn or install se_codex.execution") from error
    return execute


def _required(data: dict[str, Any], key: str, expected: type) -> None:
    if not isinstance(data.get(key), expected) or isinstance(data.get(key), bool) and expected is int:
        raise BenchmarkError(f"manifest field {key!r} must be {expected.__name__}")


def _unique_objects(items: list[Any], kind: str) -> set[str]:
    values: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise BenchmarkError(f"each {kind} needs a non-empty id")
        if not _SAFE_ID.fullmatch(item["id"]):
            raise BenchmarkError(f"unsafe {kind} id: {item['id']!r}")
        if item["id"] in values:
            raise BenchmarkError(f"duplicate {kind} id: {item['id']}")
        values.add(item["id"])
    return values


def _is_commit(value: str) -> bool:
    return bool(value) and len(value) in range(7, 65) and all(character in "0123456789abcdefABCDEF" for character in value)


def _number_or_none(value: Any) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _decode(value: bytes | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return f"benchmark-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
