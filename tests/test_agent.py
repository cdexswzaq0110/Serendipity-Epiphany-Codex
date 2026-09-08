import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from se_codex.agent import run_agent


ROOT = Path(__file__).resolve().parents[1]
USAGE = {
    "input_tokens": 10,
    "output_tokens": 10,
    "cached_input_tokens": 0,
    "total_tokens": 20,
}


def inference(output):
    return {
        "status": "completed",
        "output": output,
        "model_calls": 1,
        "usage": dict(USAGE),
        "interventions": [],
        "reroutes": [],
    }


def decision(action, reason="fixture decision", tasks=None):
    return json.dumps({"action": action, "reason": reason, "tasks": tasks or []})


class SequencedRunner:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, root, prompt, model, effort, **kwargs):
        with self.lock:
            self.calls.append(
                {
                    "root": Path(root),
                    "prompt": prompt,
                    "model": model,
                    "effort": effort,
                    "read_only": kwargs.get("read_only", False),
                }
            )
            if not self.actions:
                raise AssertionError("unexpected model call")
            action = self.actions.pop(0)
        return action(Path(root), kwargs) if callable(action) else action


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="se-agent-")
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.state = self.root / "state"
        self.project.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Agent Test")
        self._git("config", "user.email", "agent@test.invalid")
        (self.project / "app.txt").write_text("old\n", encoding="utf-8")
        self._git("add", "app.txt")
        self._git("commit", "-qm", "initial")

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.project, check=True, capture_output=True, text=True
        )

    def _check(self):
        code = (
            "from pathlib import Path; "
            "assert Path('app.txt').read_text(encoding='utf-8') == 'new\\n'"
        )
        return {"id": "content", "argv": [sys.executable, "-c", code], "timeout_seconds": 10}

    def _spec(self, **overrides):
        spec = {
            "goal": "change app.txt from old to new",
            "acceptance": ["app.txt contains exactly new"],
            "write_paths": ["app.txt"],
            "checks": [self._check()],
            "integration_checks": ["content"],
            "protected_paths": ["tests"],
            "max_rounds": 3,
            "max_model_calls": 8,
            "max_seconds": 30,
            "max_tokens": 1_000,
            "max_parallel": 1,
        }
        spec.update(overrides)
        return spec

    @staticmethod
    def _task(write_paths=None):
        return {
            "id": "implement",
            "goal": "write the requested content",
            "kind": "implementation",
            "complexity": "simple",
            "risk": "low",
            "uncertainty": "low",
            "write_paths": write_paths or ["app.txt"],
            "depends_on": [],
            "acceptance": ["app.txt contains exactly new"],
            "verify": ["content"],
        }

    def test_done_text_is_rejected_when_external_acceptance_fails(self):
        runner = SequencedRunner([inference(decision("done", "I say it is done"))])

        report = run_agent(
            self._spec(max_rounds=1), self.project, ROOT, self.state, runner=runner
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["decisions"][0]["action"], "done")
        self.assertFalse(report["checks"][0]["passed"])
        self.assertEqual((self.project / "app.txt").read_text(encoding="utf-8"), "old\n")

    def test_kernel_cannot_expand_authorized_write_paths(self):
        runner = SequencedRunner(
            [inference(decision("dispatch", tasks=[self._task(["outside.txt"])]))]
        )
        executor_calls = []

        def executor(*args, **kwargs):
            executor_calls.append((args, kwargs))
            raise AssertionError("executor must not receive an expanded scope")

        report = run_agent(
            self._spec(), self.project, ROOT, self.state, runner=runner, executor=executor
        )

        self.assertEqual(report["status"], "blocked")
        self.assertIn("expand", report["error"])
        self.assertEqual(executor_calls, [])
        self.assertFalse((self.project / "outside.txt").exists())

    def test_model_decision_dispatch_observation_done_is_a_closed_loop(self):
        def worker(root, _kwargs):
            (root / "app.txt").write_text("new\n", encoding="utf-8")
            return inference("implemented in assigned workspace")

        runner = SequencedRunner(
            [
                inference(decision("dispatch", tasks=[self._task()])),
                worker,
                inference(decision("done", "observed verified execution")),
            ]
        )

        report = run_agent(
            self._spec(), self.project, ROOT, self.state, apply=True, runner=runner
        )

        self.assertEqual(report["status"], "completed", report.get("error"))
        self.assertTrue(report["applied"])
        self.assertEqual([item["action"] for item in report["decisions"]], ["dispatch", "done"])
        self.assertEqual(report["executions"][0]["status"], "completed")
        self.assertEqual(report["executions"][0]["tasks"][0]["status"], "integrated")
        self.assertEqual((self.project / "app.txt").read_text(encoding="utf-8"), "new\n")
        self.assertEqual(report["model_calls"], 3)
        self.assertEqual(report["usage"]["total_tokens"], 60)
        self.assertEqual([call["model"] for call in runner.calls],
                         ["gpt-6-astra", "gpt-5.6-luna", "gpt-6-astra"])
        self.assertTrue(runner.calls[0]["read_only"])
        self.assertFalse(runner.calls[1]["read_only"])
        self.assertIn('"status": "completed"', runner.calls[2]["prompt"])

    def test_cancellation_after_kernel_turn_cannot_complete_or_apply(self):
        def cancel(root, _kwargs):
            (root.parent / "STOP").write_text("stop", encoding="utf-8")
            return inference(decision("done", "done despite cancellation"))

        report = run_agent(
            self._spec(), self.project, ROOT, self.state, apply=True,
            runner=SequencedRunner([cancel]),
        )

        self.assertEqual(report["status"], "blocked")
        self.assertIn("stopped", report["error"].casefold())
        self.assertEqual((self.project / "app.txt").read_text(encoding="utf-8"), "old\n")


if __name__ == "__main__":
    unittest.main()
