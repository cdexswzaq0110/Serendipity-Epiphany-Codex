from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.se_codex.hooks import evaluate
from src.se_codex.runtime import inspect_project


ROOT = Path(__file__).resolve().parents[1]


class FakeServer:
    def __init__(self, root: Path):
        self.calls = []

    def call(self, method: str, params: dict):
        self.calls.append(method)
        if method == "model/list":
            return {
                "data": [
                    {"model": "gpt-5.6-sol", "supportedReasoningEfforts": [{"reasoningEffort": "high"}]},
                    {"model": "gpt-5.6-terra", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]},
                    {"model": "gpt-5.6-luna", "supportedReasoningEfforts": [{"reasoningEffort": "low"}]},
                ]
            }
        if method == "skills/list":
            return {"data": [{"cwd": str(ROOT), "skills": [{"name": "se-kernel", "path": "a"}, {"name": "unrelated", "path": "b"}], "errors": []}]}
        if method == "hooks/list":
            return {"data": [{"cwd": str(ROOT), "hooks": [
                {"eventName": "SessionStart", "source": "project", "enabled": True, "trustStatus": "trusted", "sourcePath": str(ROOT / ".codex/hooks.json")},
                {"eventName": "PreToolUse", "source": "user", "enabled": True, "trustStatus": "trusted", "sourcePath": str(ROOT / "other.json")},
            ], "errors": [], "warnings": []}]}
        raise AssertionError(method)

    def close(self):
        pass


class ModelFailureServer(FakeServer):
    def call(self, method: str, params: dict):
        if method == "model/list":
            raise RuntimeError("model catalog unavailable")
        return super().call(method, params)


class UntrustedHookServer(FakeServer):
    def call(self, method: str, params: dict):
        result = super().call(method, params)
        if method == "hooks/list":
            result["data"][0]["hooks"][0]["trustStatus"] = "untrusted"
        return result


class RuntimeAndHookTests(unittest.TestCase):
    def test_models_alone_do_not_prove_native_runtime_readiness(self):
        class MissingRuntime(FakeServer):
            def call(self, method, params):
                if method != 'model/list':
                    return {'data': []}
                result = super().call(method, params)
                result['data'].append({'model': 'gpt-6-astra', 'supportedReasoningEfforts': [{'reasoningEffort': 'high'}]})
                return result
        report = inspect_project(ROOT, live=True, server_factory=MissingRuntime)
        self.assertTrue(all(item['ready'] for item in report['readiness']))
        self.assertFalse(report['live_verified'])
        self.assertFalse(report['inference_verified'])
        self.assertTrue(any('se-memory' in message for message in report['limitations']))

    def test_live_doctor_keeps_diagnostics_when_kernel_model_is_missing(self):
        report = inspect_project(ROOT, live=True, server_factory=FakeServer)
        readiness = {item["role"]: item for item in report["readiness"]}
        self.assertFalse(readiness["kernel"]["ready"])
        self.assertEqual(readiness["kernel"]["model"], "gpt-6-astra")
        self.assertTrue(readiness["terra"]["ready"])
        self.assertEqual([item["name"] for item in report["skills_discovery"]["skills"]], ["se-kernel"])
        self.assertEqual(len(report["hooks_discovery"]["hooks"]), 1)
        self.assertFalse(report["live_verified"])
        self.assertTrue(any("gpt-6-astra" in item for item in report["limitations"]))

    def test_model_failure_does_not_discard_skill_or_hook_diagnostics(self):
        report = inspect_project(ROOT, live=True, server_factory=ModelFailureServer)
        self.assertEqual(report["skills_discovery"]["skills"][0]["name"], "se-kernel")
        self.assertEqual(report["hooks_discovery"]["hooks"][0]["eventName"], "SessionStart")
        self.assertTrue(any("model catalog unavailable" in item for item in report["limitations"]))

    def test_untrusted_project_hook_is_reported(self):
        report = inspect_project(ROOT, live=True, server_factory=UntrustedHookServer)
        self.assertEqual(len(report["hooks_discovery"]["hooks"]), 1)
        self.assertTrue(any("not trusted" in item for item in report["limitations"]))

    def test_pre_tool_use_requires_configured_role_model_effort_pair(self):
        base = {"hook_event_name": "PreToolUse", "tool_name": "spawn_agent", "tool_input": {}}
        self.assertEqual(evaluate({**base, "tool_input": {"agent_type": "terra"}}, ROOT), {})
        denied_model = evaluate({**base, "tool_input": {"agent_type": "terra", "model": "gpt-6-astra"}}, ROOT)
        self.assertEqual(denied_model["hookSpecificOutput"]["permissionDecision"], "deny")
        denied_effort = evaluate({**base, "tool_input": {"model": "gpt-5.6-terra", "reasoning_effort": "high"}}, ROOT)
        self.assertEqual(denied_effort["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(evaluate({**base, "tool_input": {"model": "gpt-5.6-terra", "reasoning_effort": "medium"}}, ROOT), {})

    def test_hooks_json_uses_relative_launcher_for_git_root(self):
        hooks = json.loads((ROOT / ".codex/hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(set(hooks["hooks"]), {"SessionStart", "PreToolUse"})
        commands = [entry["hooks"][0]["command"] for entries in hooks["hooks"].values() for entry in entries]
        self.assertTrue(all("git" in command and "rev-parse" in command and "runpy" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
