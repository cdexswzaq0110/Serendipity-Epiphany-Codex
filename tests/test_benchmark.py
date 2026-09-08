from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from se_codex.benchmark import BenchmarkError, _run_check, load_manifest, run_benchmark
from se_codex.execution import execute as real_execute


class BenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repo = self.root / "fixture-repo"
        self.repo.mkdir()
        self._git("init")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Benchmark Test")
        (self.repo / "README.md").write_text("fixture\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "fixture")
        self.commit = self._git("rev-parse", "HEAD").strip()
        self.manifest_path = self.root / "benchmark.json"
        self._write_manifest()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_runs_shuffled_local_clones_and_preserves_raw_reports(self) -> None:
        calls = []

        def execute(manifest, **kwargs):
            calls.append((manifest, kwargs))
            (kwargs["project"] / "marker.txt").write_text("ok", encoding="utf-8")
            return {
                "status": "completed", "duration_seconds": 1.25,
                "usage": {"input_tokens": 12}, "model_calls": 1,
                "interventions": [], "checks": [], "tasks": [],
            }

        result = run_benchmark(self.manifest_path, self.root / "state", execute_fn=execute)

        self.assertEqual(len(calls), 4)
        self.assertTrue(all(call[1]["apply"] is True for call in calls))
        self.assertTrue(all(call[0]["max_parallel"] == 1 for call in calls))
        self.assertTrue(all(call[0]["max_seconds"] == 120 for call in calls))
        self.assertTrue(all(call[0]["max_tokens"] == 12000 for call in calls))
        self.assertTrue(all(call[0]["protected_paths"] == ["benchmark.json"] for call in calls))
        self.assertTrue(all(call[0]["integration_checks"] == ["marker"] for call in calls))
        self.assertTrue(all(record["accepted"] for record in result["records"]))
        report_dir = Path(result["state_root"]) / "reports"
        self.assertEqual(len(list(report_dir.glob("*.json"))), 4)
        self.assertTrue((Path(result["state_root"]) / "summary.json").is_file())
        usage = result["summary"]["groups"][0]["usage"]
        self.assertEqual(usage["metrics"]["input_tokens"]["unknown_runs"], 0)
        self.assertEqual(usage["unknown_runs"], 0)

    def test_real_execution_applies_verified_patch_before_external_check(self) -> None:
        def fake_runner(worker, prompt, model, effort, **kwargs):
            (worker / "marker.txt").write_text("ok", encoding="utf-8")
            return {"status": "completed", "model_calls": 1, "usage": {"total_tokens": 1}, "interventions": []}

        def execute(manifest, **kwargs):
            return real_execute(manifest, runner=fake_runner, **kwargs)

        result = run_benchmark(
            self.manifest_path, self.root / "state", execute_fn=execute,
            tool_root=Path(__file__).resolve().parents[1],
        )

        self.assertTrue(all(record["accepted"] for record in result["records"]))
        self.assertTrue(all(record["raw_execution"]["applied"] for record in result["records"]))
        self.assertTrue(all((Path(record["clone"]) / "marker.txt").is_file() for record in result["records"]))

    def test_unknown_usage_is_explicitly_reported(self) -> None:
        def execute(*args, **kwargs):
            (kwargs["project"] / "marker.txt").write_text("ok", encoding="utf-8")
            return {"status": "completed", "duration_seconds": 1, "usage": None, "model_calls": 1, "interventions": []}

        result = run_benchmark(self.manifest_path, self.root / "state", execute_fn=execute)

        group = result["summary"]["groups"][0]
        self.assertEqual(group["usage"]["known_runs"], 0)
        self.assertEqual(group["usage"]["unknown_runs"], 2)
        self.assertEqual(group["duration_seconds"]["unknown_runs"], 0)

    def test_rejects_remote_unresolved_or_unsafe_manifest_before_execution(self) -> None:
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        data["cases"][0]["repo"] = "https://example.invalid/repo.git"
        self.manifest_path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(BenchmarkError):
            load_manifest(self.manifest_path)

        data["cases"][0]["repo"] = str(self.repo)
        data["cases"][0]["commit"] = "deadbee"
        self.manifest_path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(BenchmarkError):
            load_manifest(self.manifest_path)

        data["cases"][0]["commit"] = self.commit
        data["cases"][0]["task"]["id"] = "../../escape"
        self.manifest_path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(BenchmarkError):
            load_manifest(self.manifest_path)

    def test_rejects_more_than_twenty_four_trials(self) -> None:
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        data["repeats"] = 13
        self.manifest_path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(BenchmarkError):
            load_manifest(self.manifest_path)

    def test_check_output_with_non_utf8_bytes_is_preserved_as_failure(self) -> None:
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        data["checks"][0]["argv"] = [
            sys.executable, "-c", "import sys; sys.stderr.buffer.write(b'\\xff'); raise SystemExit(1)",
        ]
        self.manifest_path.write_text(json.dumps(data), encoding="utf-8")

        result = run_benchmark(
            self.manifest_path, self.root / "state",
            execute_fn=lambda *args, **kwargs: {"status": "completed"},
        )

        check = result["records"][0]["checks"][0]
        self.assertFalse(check["passed"])
        self.assertIn("�", check["stderr"])

    def test_example_checks_fail_at_baseline_and_pass_after_manual_repairs(self) -> None:
        project = Path(__file__).resolve().parents[1]
        manifest = load_manifest(project / "examples" / "benchmark.json")
        check_map = {check["id"]: check for check in manifest["checks"]}
        baseline = self.root / "baseline"
        subprocess.run(["git", "clone", "--no-hardlinks", "--no-local", "--no-checkout", str(project), str(baseline)], check=True, capture_output=True)
        subprocess.run(["git", "checkout", "--detach", manifest["cases"][0]["commit"]], cwd=baseline, check=True, capture_output=True)

        self.assertFalse(_run_check(check_map["routing-agent-defaults"], baseline)["passed"])
        self.assertFalse(_run_check(check_map["installer-runtime-link"], baseline)["passed"])

        routing = baseline / "src/se_codex/routing.py"
        routing_text = routing.read_text(encoding="utf-8")
        marker = "    return profiles\n\n\ndef _profile"
        replacement = """    defaults = config.get(\"agents\")\n    if isinstance(defaults, dict):\n        model = defaults.get(\"default_subagent_model\")\n        effort = defaults.get(\"default_subagent_reasoning_effort\")\n        if isinstance(model, str) and isinstance(effort, str):\n            profiles.setdefault(\"terra\", {\"model\": model, \"effort\": effort})\n\n    return profiles\n\n\ndef _profile"""
        self.assertIn(marker, routing_text)
        routing.write_text(routing_text.replace(marker, replacement), encoding="utf-8")

        installer = baseline / "src/se_codex/install.py"
        old = 'text = text.replace("../../../docs/", "../../../.se-codex/docs/")'
        new = old + '\n        text = text.replace("`docs/MEMORY_RUNBOOK.md`", "`.se-codex/docs/MEMORY_RUNBOOK.md`")'
        self.assertIn(old, installer.read_text(encoding="utf-8"))
        installer.write_text(installer.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

        self.assertTrue(_run_check(check_map["routing-agent-defaults"], baseline)["passed"])
        self.assertTrue(_run_check(check_map["installer-runtime-link"], baseline)["passed"])

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(json.dumps({
            "seed": 7, "repeats": 2, "max_seconds": 120, "max_tokens": 12000,
            "protected_paths": ["benchmark.json"],
            "checks": [{
                "id": "marker",
                "argv": [sys.executable, "-c", "from pathlib import Path; assert Path('marker.txt').read_text() == 'ok'"],
                "timeout_seconds": 10,
            }],
            "arms": [
                {"id": "direct-terra", "harness": "direct", "model": "gpt-5.6-terra", "effort": "medium"},
                {"id": "closed-terra", "harness": "closed-loop", "model": "gpt-5.6-terra", "effort": "medium"},
            ],
            "cases": [{
                "id": "fixture", "repo": str(self.repo), "commit": self.commit,
                "task": {
                    "id": "fixture-task", "goal": "write a marker", "kind": "implementation",
                    "complexity": "simple", "risk": "low", "uncertainty": "low",
                    "write_paths": ["marker.txt"], "depends_on": [],
                    "acceptance": ["marker exists"], "verify": ["marker"],
                },
            }],
        }, ensure_ascii=False), encoding="utf-8")

    def _git(self, *argv: str) -> str:
        return subprocess.run(["git", *argv], cwd=self.repo, text=True, encoding="utf-8", capture_output=True, check=True).stdout


if __name__ == "__main__":
    unittest.main()