#!/usr/bin/env python3
"""Install and verify the SE skill bundle plus SE-Codex-local skills."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

SCHEMA_VERSION = 1
MANIFEST = "manifest.json"
LOCAL_SKILLS = {"se-kernel", "se-memory", "se-recover", "se-git"}


class SyncError(Exception):
    pass


def normalized_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    final_newline = text.endswith("\n")
    normalized = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
    if final_newline and not normalized.endswith("\n"):
        normalized += "\n"
    return normalized.encode("utf-8")


def file_hash(path: Path) -> str:
    return hashlib.sha256(normalized_bytes(path)).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SyncError(f"無法讀取 {path}：{error}") from error
    if not isinstance(value, dict):
        raise SyncError(f"{path} 必須是 JSON object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _manifest_files(root: Path) -> list[dict[str, str]]:
    return [
        {"path": path.relative_to(root).as_posix(), "sha256": file_hash(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != MANIFEST
    ]


def validate(root: Path) -> list[str]:
    problems: list[str] = []
    path = root / MANIFEST
    if not path.is_file():
        return [f"缺少 {MANIFEST}"]
    try:
        manifest = read_json(path)
    except SyncError as error:
        return [str(error)]
    required = {"schema_version", "bundle_version", "source_repo", "source_commit", "local_source", "files"}
    for key in sorted(required - manifest.keys()):
        problems.append(f"manifest 缺少欄位：{key}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"不支援 schema_version：{manifest.get('schema_version')!r}")
    items = manifest.get("files")
    if not isinstance(items, list):
        return [*problems, "manifest files 必須是 array"]
    expected: dict[str, str] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            problems.append(f"files[{index}] 格式錯誤")
            continue
        relative, digest = item["path"], item["sha256"]
        candidate = Path(relative) if isinstance(relative, str) else Path()
        if not relative or "\\" in relative or candidate.is_absolute() or ".." in candidate.parts:
            problems.append(f"files[{index}] 路徑錯誤：{relative!r}")
        elif relative in expected:
            problems.append(f"manifest 重複路徑：{relative}")
        elif not isinstance(digest, str) or len(digest) != 64:
            problems.append(f"files[{index}] sha256 格式錯誤：{relative}")
        else:
            expected[relative] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != MANIFEST
    }
    for relative in sorted(expected.keys() - actual):
        problems.append(f"缺檔：{relative}")
    for relative in sorted(actual - expected.keys()):
        problems.append(f"多檔：{relative}")
    for relative in sorted(actual & expected.keys()):
        digest = file_hash(root / relative)
        if digest != expected[relative]:
            problems.append(f"hash 不符：{relative}（預期 {expected[relative]}，實際 {digest}）")
    return problems


def _git_value(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        raise SyncError(f"git {' '.join(arguments)} 失敗：{result.stderr.strip()}")
    return result.stdout.strip()


def _copy_skill(source: Path, destination: Path) -> None:
    if not (source / "SKILL.md").is_file():
        raise SyncError(f"skill 缺少 SKILL.md：{source}")
    shutil.copytree(source, destination)


def install(bundle: Path, local: Path, dest: Path, with_extended: bool) -> None:
    source_manifest = read_json(bundle / MANIFEST)
    required = {"schema_version", "bundle_version", "source_repo", "source_commit", "files"}
    if required - source_manifest.keys():
        raise SyncError("SE bundle manifest 缺少必要欄位")
    expected_source = {item["path"]: item["sha256"] for item in source_manifest["files"]}
    for relative, digest in expected_source.items():
        path = bundle / relative
        if not path.is_file() or file_hash(path) != digest:
            raise SyncError(f"SE bundle 驗證失敗：{relative}")

    core = sorted(path for path in bundle.glob("se-*") if path.is_dir())
    if len(core) != 9:
        raise SyncError(f"SE bundle 必須含 9 個核心 skill，實際為 {len(core)}")
    local_paths = sorted(path for path in local.glob("se-*") if path.is_dir())
    if {path.name for path in local_paths} != LOCAL_SKILLS:
        raise SyncError("skills-local 必須且只能包含 se-kernel、se-memory、se-recover、se-git")
    core_names = {path.name for path in core}
    # The bundle may retain extended variants of core skills for compatibility. The
    # canonical top-level core wins; --with-extended adds only distinct skill names.
    extended = (sorted(path for path in (bundle / "extended").glob("se-*")
                       if path.is_dir() and path.name not in core_names)
                if with_extended else [])
    names = [path.name for path in [*core, *extended, *local_paths]]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise SyncError("skill 名稱衝突：" + ", ".join(duplicates))

    parent = dest.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{dest.name}.stage-", dir=parent))
    backup = parent / f".{dest.name}.backup-{os.getpid()}"
    try:
        for source in [*core, *extended, *local_paths]:
            _copy_skill(source, stage / source.name)
        local_root = local.parent.resolve()
        combined = {
            "schema_version": SCHEMA_VERSION,
            "bundle_version": source_manifest["bundle_version"],
            "source_repo": source_manifest["source_repo"],
            "source_commit": source_manifest["source_commit"],
            "local_source": {
                "path": local.relative_to(local_root).as_posix(),
                "source_repo": _git_value(local_root, "remote", "get-url", "origin"),
                "source_commit": _git_value(local_root, "rev-parse", "HEAD"),
                "skills": sorted(LOCAL_SKILLS),
            },
            "with_extended": with_extended,
            "files": _manifest_files(stage),
        }
        write_json(stage / MANIFEST, combined)
        problems = validate(stage)
        if problems:
            raise SyncError("暫存目錄驗證失敗：\n" + "\n".join(problems))
        if backup.exists():
            shutil.rmtree(backup)
        if dest.exists():
            os.replace(dest, backup)
        try:
            os.replace(stage, dest)
        except OSError:
            if backup.exists() and not dest.exists():
                os.replace(backup, dest)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if backup.exists() and dest.exists():
            shutil.rmtree(backup, ignore_errors=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install")
    install_parser.add_argument("--bundle", type=Path, required=True)
    install_parser.add_argument("--dest", type=Path, required=True)
    install_parser.add_argument("--local", type=Path, default=Path(__file__).resolve().parents[1] / "skills-local")
    install_parser.add_argument("--with-extended", action="store_true")
    check_parser = commands.add_parser("check")
    check_parser.add_argument("--dest", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "install":
            install(arguments.bundle.resolve(), arguments.local.resolve(), arguments.dest.resolve(), arguments.with_extended)
        else:
            problems = validate(arguments.dest.resolve())
            if problems:
                raise SyncError("驗證失敗：\n" + "\n".join(problems))
    except (OSError, SyncError) as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1
    print("通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
