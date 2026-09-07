"""Read-only host discovery through the documented Codex app-server protocol."""
from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
import tomllib
from pathlib import Path


def codex_command() -> list[str]:
    executable = shutil.which("codex")
    if not executable:
        raise ValueError("Codex CLI is not on PATH")
    path = Path(executable)
    if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        entry = path.parent / "node_modules/@openai/codex/bin/codex.js"
        node = shutil.which("node")
        if not entry.is_file() or not node:
            raise ValueError("Use a Codex executable or the official npm installation with Node on PATH")
        return [node, str(entry)]
    return [str(path)]


class AppServer:
    """One bounded local connection; no account credentials or transcripts are read."""

    def __init__(self, root: Path):
        self.process = subprocess.Popen(
            [*codex_command(), "app-server", "--stdio"], cwd=root,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.messages: queue.Queue = queue.Queue()
        self.next_id = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.call("initialize", {"clientInfo": {"name": "se_codex", "version": "0.1.0"},
                                     "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            self.messages.put(None)

    def send(self, message: dict):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def call(self, method: str, params: dict, timeout: int = 20):
        self.next_id += 1
        request_id = self.next_id
        self.send({"method": method, "id": request_id, "params": params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if message is None:
                raise ValueError("Codex app-server exited before replying")
            if message.get("id") == request_id:
                if "error" in message:
                    raise ValueError(f"{method}: {message['error'].get('message', 'request failed')}")
                return message.get("result", {})
        raise ValueError(f"Codex app-server timeout: {method}")

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()


def _summary(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    return f"{error.__class__.__name__}: {message}"


def _model_rows(catalog: list[dict]) -> list[dict]:
    rows = []
    for model in catalog:
        if not isinstance(model, dict):
            continue
        model_id = model.get("model", model.get("id"))
        if not isinstance(model_id, str) or not model_id:
            continue
        efforts = []
        for entry in model.get("supportedReasoningEfforts", []):
            if isinstance(entry, str):
                efforts.append(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("reasoningEffort"), str):
                efforts.append(entry["reasoningEffort"])
        rows.append({"model": model_id, "efforts": efforts})
    return rows


def _readiness(profiles: dict[str, dict[str, str]], models: list[dict] | None = None, live: bool = False) -> list[dict]:
    available = {row["model"]: set(row["efforts"]) for row in models or []}
    readiness = []
    for role in ("kernel", "sol", "terra", "luna"):
        profile = profiles.get(role, {})
        model = profile.get("model")
        effort = profile.get("effort")
        config_ready = bool(model and effort)
        model_available = None if models is None else model in available
        effort_supported = None if models is None or not model_available else effort in available[model]
        live_ready = not live or (models is not None and model_available and effort_supported)
        limits = []
        if not config_ready:
            limits.append("role is missing configured model or reasoning effort")
        if models is not None and not model_available:
            limits.append(f"configured model {model!r} was not present in the live catalog")
        if models is not None and model_available and not effort_supported:
            limits.append(f"configured reasoning effort {effort!r} was not advertised by model {model!r}")
        if live and models is None:
            limits.append("live model capability could not be checked")
        readiness.append({
            "role": role,
            "model": model,
            "effort": effort,
            "config_ready": config_ready,
            "model_available": model_available,
            "effort_supported": effort_supported,
            "ready": config_ready and live_ready,
            "limitations": limits,
        })
    return readiness


def _discover_skills(raw: object) -> tuple[dict, list[str]]:
    items = raw.get("data", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        items = []
    skills = []
    limitations = []
    discovered = []
    for page in items:
        if not isinstance(page, dict):
            continue
        for error in page.get("errors", []):
            limitations.append(f"skills discovery error: {error}")
        nested = page.get("skills", [])
        if isinstance(nested, list):
            discovered.extend(nested)
        if page.get('name'):
            discovered.append(page)
    for item in discovered:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name.startswith("se-"):
            entry = {"name": name, 'enabled': item.get('enabled')}
            if isinstance(item.get("path"), str):
                entry["path"] = item["path"]
            skills.append(entry)
    ready = {item['name'] for item in skills if item['enabled'] is True}
    for name in sorted({'se-kernel', 'se-memory', 'se-recover'} - ready):
        limitations.append(f'skill {name} was not discovered as enabled')
    return {"skills": skills}, limitations


def _discover_hooks(raw: object, root: Path) -> tuple[dict, list[str]]:
    items = raw.get("data", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return {"hooks": []}, ['hooks discovery returned invalid data']
    hooks = []
    limitations = []
    expected = (root / ".codex/hooks.json").resolve()
    for item in items:
        if not isinstance(item, dict):
            continue
        for error in [*item.get("errors", []), *item.get("warnings", [])]:
            limitations.append(f"hooks discovery issue: {error}")
        nested = item.get("hooks", [])
        if not isinstance(nested, list):
            nested = [item] if item.get("sourcePath") else []
        for hook in nested:
            if not isinstance(hook, dict):
                continue
            source_path = hook.get("sourcePath")
            if not isinstance(source_path, str):
                continue
            try:
                same_source = Path(source_path).resolve() == expected
            except OSError:
                same_source = False
            if not same_source:
                continue
            entry = {
                "eventName": hook.get("eventName"),
                "source": hook.get("source"),
                "enabled": hook.get("enabled"),
                "trustStatus": hook.get("trustStatus"),
                "sourcePath": source_path,
            }
            hooks.append(entry)
            if hook.get("enabled") is False:
                limitations.append(f"hook {hook.get('eventName', '?')} is disabled")
            if str(hook.get("trustStatus", "")).lower() != "trusted":
                limitations.append(f"hook {hook.get('eventName', '?')} is not trusted")
    names = {str(hook['eventName']).casefold() for hook in hooks}
    for name in ('sessionstart', 'pretooluse'):
        if name not in names:
            limitations.append(f'project hook {name} was not discovered')
    return {"hooks": hooks}, limitations


def _list_models(server: AppServer) -> list[dict]:
    catalog = []
    cursor = None
    for _ in range(20):
        params = {"limit": 100, "includeHidden": False}
        if cursor:
            params["cursor"] = cursor
        page = server.call("model/list", params)
        if not isinstance(page, dict):
            raise ValueError("model/list returned a non-object response")
        data = page.get("data", [])
        if not isinstance(data, list):
            raise ValueError("model/list returned invalid data")
        catalog.extend(data)
        cursor = page.get("nextCursor")
        if not cursor:
            return catalog
    raise ValueError("Model catalog pagination exceeded safety bound")


def _add_readiness_limitations(result: dict) -> None:
    for item in result["readiness"]:
        for limitation in item["limitations"]:
            result["limitations"].append(f"{item['role']}: {limitation}")


def inspect_project(root: Path, live: bool = False, server_factory=AppServer) -> dict:
    from .routing import _profiles

    root = root.resolve()
    profiles = _profiles(root)
    config = tomllib.loads((root / ".codex/config.toml").read_text(encoding="utf-8"))
    result = {"root": str(root), "profiles": profiles,
              "default_subagent_model": config.get("agents", {}).get("default_subagent_model"),
              "skills": sorted(p.parent.name for p in (root / ".agents/skills").glob("*/SKILL.md")),
              "security_mode": "governance; protected execution requires a verified permission profile",
              "readiness": _readiness(profiles),
              "limitations": ["live model, skill, and hook capabilities were not queried"],
              "live_verified": False}
    result['inference_verified'] = False
    if not live:
        return result
    result["limitations"] = []
    server = None
    model_rows = None
    try:
        try:
            server = server_factory(root)
        except Exception as error:
            result["limitations"].append(f"app-server unavailable: {_summary(error)}")
            result["readiness"] = _readiness(profiles, None, live=True)
            _add_readiness_limitations(result)
            return result
        try:
            model_rows = _model_rows(_list_models(server))
            result["models"] = model_rows
        except Exception as error:
            result["limitations"].append(f"model catalog unavailable: {_summary(error)}")
        try:
            result["skills_discovery"], discovery_limits = _discover_skills(server.call("skills/list", {"cwds": [str(root)], "forceReload": True}))
            result["limitations"].extend(discovery_limits)
        except Exception as error:
            result["skills_discovery"] = {"skills": [], "error": _summary(error)}
            result["limitations"].append(f"skills discovery unavailable: {_summary(error)}")
        try:
            result["hooks_discovery"], discovery_limits = _discover_hooks(server.call("hooks/list", {"cwds": [str(root)]}), root)
            result["limitations"].extend(discovery_limits)
        except Exception as error:
            result["hooks_discovery"] = {"hooks": [], "error": _summary(error)}
            result["limitations"].append(f"hooks discovery unavailable: {_summary(error)}")
        result["readiness"] = _readiness(profiles, model_rows, live=True)
        _add_readiness_limitations(result)
        if all(item["ready"] for item in result["readiness"]) and len(result["limitations"]) == 0:
            result["live_verified"] = True
        else:
            result["limitations"].append("hooks and host capability checks supplement configuration; they do not prove filesystem isolation")
    finally:
        if server is not None:
            try:
                server.close()
            except Exception as error:
                result["limitations"].append(f"app-server close failed: {_summary(error)}")
    return result
