"""Copy the project-scoped Codex agent into an existing Git repository."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Iterable


class InstallError(ValueError):
    """Raised when an installation target is unsafe or incomplete."""


@dataclass(frozen=True)
class _Artifact:
    destination: Path
    content: bytes

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


_REPARSE_POINT = 0x0400
_RUNTIME_FILES = ("se.py", "README.md", "LICENSE", "docs/MEMORY_RUNBOOK.md")


def install_project(source_root: Path, target: Path, apply: bool = False) -> dict:
    """Plan or apply a non-overwriting project installation.

    ``target`` must already be a Git repository. A dry run returns only the
    planned destination paths and conflicting paths. Applying refuses the
    entire batch when any existing target file differs from its source.
    """

    source = _checked_source(source_root)
    destination_root = _checked_target(target)
    _reject_nested_roots(source, destination_root)
    artifacts = _collect_artifacts(source)
    conflicts, already_installed = _preflight(destination_root, artifacts)
    planned_paths = [artifact.destination.as_posix() for artifact in artifacts]

    if not apply:
        return {"paths": planned_paths, "conflicts": conflicts}
    if conflicts:
        return {
            "created": [],
            "already_installed": already_installed,
            "conflicts": conflicts,
        }

    created: list[tuple[Path, Path, str]] = []
    try:
        for artifact in artifacts:
            destination = destination_root / artifact.destination
            _assert_target_path_safe(destination_root, artifact.destination)
            if destination.exists():
                if _hash_file(destination) == artifact.digest:
                    continue
                raise InstallError(f"Target changed during installation: {artifact.destination}")

            destination.parent.mkdir(parents=True, exist_ok=True)
            _assert_target_path_safe(destination_root, artifact.destination)
            try:
                with destination.open("xb") as target_file:
                    target_file.write(artifact.content)
            except FileExistsError as error:
                if destination.is_file() and _hash_file(destination) == artifact.digest:
                    continue
                raise InstallError(f"Target changed during installation: {artifact.destination}") from error
            created.append((destination, artifact.destination, artifact.digest))
    except Exception:
        _rollback(destination_root, created)
        raise

    return {
        "created": [path.relative_to(destination_root).as_posix() for path, _, _ in created],
        "already_installed": already_installed,
        "conflicts": [],
    }


def _checked_source(source_root: Path) -> Path:
    source = Path(source_root).absolute()
    if not source.is_dir():
        raise InstallError(f"Source root does not exist: {source}")
    if _is_reparse_point(source):
        raise InstallError(f"Source root cannot be a symlink or reparse point: {source}")
    return source.resolve(strict=True)


def _checked_target(target: Path) -> Path:
    root = Path(target).absolute()
    _assert_no_reparse_ancestors(root)
    if not root.is_dir():
        raise InstallError(f"Target must be an existing Git repository: {root}")
    if not (root / ".git").exists():
        raise InstallError(f"Target is not a Git repository: {root}")
    return root.resolve(strict=True)


def _reject_nested_roots(source: Path, target: Path) -> None:
    if _is_within(source, target) or _is_within(target, source):
        raise InstallError("Source and target cannot contain one another")


def _collect_artifacts(source: Path) -> list[_Artifact]:
    files: list[tuple[Path, Path]] = []
    files.append((source / "AGENTS.md", Path("AGENTS.md")))
    files.append((source / ".codex/config.toml", Path(".codex/config.toml")))

    for agent in _regular_files(source / ".codex/agents", "*.toml"):
        files.append((agent, Path(".codex/agents") / agent.name))

    hooks = source / ".codex/hooks.json"
    if hooks.exists():
        files.append((hooks, Path(".codex/hooks.json")))

    for skill in _skill_files(source / ".agents/skills"):
        files.append((skill, skill.relative_to(source)))

    for relative in _RUNTIME_FILES:
        files.append((source / relative, Path(".se-codex") / relative))
    for document in _regular_files(source / 'docs', '*.md'):
        relative = document.relative_to(source)
        if relative.as_posix() not in _RUNTIME_FILES:
            files.append((document, Path('.se-codex') / relative))
    for document in _regular_files(source / 'docs', '*.json'):
        files.append((document, Path('.se-codex/docs') / document.name))
    for name in ('run.json', 'goal.json'):
        example = source / 'examples' / name
        if example.exists():
            files.append((example, Path('.se-codex/examples') / name))
    for module in _regular_files(source / "src/se_codex", "*.py"):
        files.append((module, Path(".se-codex/src/se_codex") / module.name))
    files.append((source / ".codex/config.toml", Path(".se-codex/.codex/config.toml")))
    for agent in _regular_files(source / ".codex/agents", "*.toml"):
        files.append((agent, Path(".se-codex/.codex/agents") / agent.name))

    artifacts: list[_Artifact] = [_Artifact(Path('.se-state/.gitignore'), b'*\n!.gitignore\n')]
    destinations: set[Path] = set()
    for file_path, destination in files:
        _require_regular_file(file_path)
        if destination in destinations:
            raise InstallError(f"Duplicate installation destination: {destination}")
        destinations.add(destination)
        artifacts.append(_Artifact(destination, _transform(file_path, destination)))

    return sorted(artifacts, key=lambda artifact: artifact.destination.as_posix())


def _regular_files(directory: Path, pattern: str) -> Iterable[Path]:
    if not directory.is_dir() or _is_reparse_point(directory):
        raise InstallError(f"Required source directory is missing or unsafe: {directory}")
    for path in sorted(directory.glob(pattern)):
        _require_regular_file(path)
        yield path


def _skill_files(directory: Path) -> Iterable[Path]:
    if not directory.is_dir() or _is_reparse_point(directory):
        raise InstallError(f"Required source directory is missing or unsafe: {directory}")
    skills = sorted(path for path in directory.glob("se-*") if path.is_dir())
    if not skills:
        raise InstallError(f"No se-* skills found: {directory}")
    for skill in skills:
        if _is_reparse_point(skill):
            raise InstallError(f"Source skill cannot be a symlink or reparse point: {skill}")
        for path in sorted(skill.rglob("*")):
            if path.is_dir():
                if _is_reparse_point(path):
                    raise InstallError(f"Source directory cannot be a symlink or reparse point: {path}")
                continue
            _require_regular_file(path)
            yield path


def _require_regular_file(path: Path) -> None:
    if _is_reparse_point(path) or not path.is_file():
        raise InstallError(f"Required source file is missing or unsafe: {path}")


def _transform(source: Path, destination: Path) -> bytes:
    content = source.read_bytes()
    if source.suffix not in {".md", ".json"}:
        return content

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InstallError(f"Expected UTF-8 text file: {source}") from error

    if destination.parts[:2] == (".agents", "skills"):
        text = text.replace("../../../docs/", "../../../.se-codex/docs/")
    if destination == Path('.agents/skills/se-memory/references/governance.md'):
        text = text.replace('`docs/MEMORY_RUNBOOK.md`', '`.se-codex/docs/MEMORY_RUNBOOK.md`')
    if destination == Path("AGENTS.md"):
        text = text.replace("docs/MEMORY_RUNBOOK.md", ".se-codex/docs/MEMORY_RUNBOOK.md")
        text = text.replace('docs/EXECUTION.md', '.se-codex/docs/EXECUTION.md')
    if destination == Path(".codex/hooks.json"):
        text = text.replace("root+'/se.py'", "root+'/.se-codex/se.py'")
        text = re.sub(
            r"(?<![A-Za-z0-9_.\\/-])se\.py\b",
            ".se-codex/se.py",
            text,
        )
    return text.encode("utf-8")


def _preflight(root: Path, artifacts: Iterable[_Artifact]) -> tuple[list[str], list[str]]:
    conflicts: list[str] = []
    already_installed: list[str] = []
    for artifact in artifacts:
        _assert_target_path_safe(root, artifact.destination)
        destination = root / artifact.destination
        if destination.exists():
            if destination.is_file() and _hash_file(destination) == artifact.digest:
                already_installed.append(artifact.destination.as_posix())
            else:
                conflicts.append(artifact.destination.as_posix())
    return conflicts, already_installed


def _assert_target_path_safe(root: Path, relative: Path) -> None:
    current = root
    if _is_reparse_point(current):
        raise InstallError(f"Target root cannot be a symlink or reparse point: {root}")
    for index, part in enumerate(relative.parts):
        current /= part
        if _path_entry_exists(current) and _is_reparse_point(current):
            raise InstallError(f"Target path cannot traverse a symlink or reparse point: {current}")
        if index < len(relative.parts) - 1 and _path_entry_exists(current) and not current.is_dir():
            raise InstallError(f"Target path parent is not a directory: {current}")


def _assert_no_reparse_ancestors(path: Path) -> None:
    current = path
    while True:
        if _path_entry_exists(current) and _is_reparse_point(current):
            raise InstallError(f"Target cannot use a symlink or reparse-point ancestor: {current}")
        if current.parent == current:
            return
        current = current.parent


def _is_reparse_point(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & _REPARSE_POINT)


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _hash_file(path: Path) -> str:
    if _is_reparse_point(path) or not path.is_file():
        raise InstallError(f"Target file is missing or unsafe: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rollback(root: Path, created: Iterable[tuple[Path, Path, str]]) -> None:
    for path, relative, expected_digest in reversed(list(created)):
        try:
            _assert_target_path_safe(root, relative)
            if path != root / relative:
                continue
            if path.is_file() and not _is_reparse_point(path) and _hash_file(path) == expected_digest:
                path.unlink()
        except (OSError, InstallError):
            continue


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True
