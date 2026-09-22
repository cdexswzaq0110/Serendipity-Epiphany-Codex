from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from se_codex.execution import execute, read_report, validate_manifest
from se_codex.memory import MemoryStore


ROOT = Path(__file__).resolve().parents[1]
USAGE = {
    "input_tokens": 10,
    "output_tokens": 10,
    "cached_input_tokens": 0,
    "total_tokens": 20,
}


def completed(output="worker completed"):
    return {
        "status": "completed",
        "output": output,
        "model_calls": 1,
        "usage": dict(USAGE),
        "interventions": [],
        "reroutes": [],
    }


class FakeRunner:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, root, prompt, model, effort, **kwargs):
        with self.lock:
            self.calls.append({"root": Path(root), "prompt": prompt, "model": model, "effort": effort})
            if not self.actions:
                raise AssertionError("unexpected model call")
            action = self.actions.pop(0)
        return action(Path(root), kwargs) if callable(action) else action


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="se-execution-")
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.state = self.root / "state"
        self.project.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Runtime Test")
        self._git("config", "user.email", "runtime@test.invalid")
        (self.project / "app.txt").write_text("old\n", encoding="utf-8")
        self._git("add", "app.txt")
        self._git("commit", "-qm", "initial")

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.project, check=True, capture_output=True, text=True
        )

    def _check(self, expected="new\n", check_id="content"):
        code = (
            "from pathlib import Path; "
            f"assert Path('app.txt').read_text(encoding='utf-8') == {expected!r}"
        )
        return {"id": check_id, "argv": [sys.executable, "-c", code], "timeout_seconds": 10}

    def _task(self, task_id="change", **overrides):
        task = {
            "id": task_id,
            "goal": "change the requested file",
            "kind": "implementation",
            "complexity": "simple",
            "risk": "low",
            "uncertainty": "low",
            "write_paths": ["app.txt"],
            "depends_on": [],
            "acceptance": ["app.txt has the requested content"],
            "verify": ["content"],
        }
        task.update(overrides)
        return task

    def _manifest(self, tasks=None, **overrides):
        manifest = {
            "tasks": tasks or [self._task()],
            "checks": [self._check()],
            "integration_checks": ["content"],
            "max_parallel": 1,
            "max_attempts": 3,
            "max_model_calls": 6,
            "max_seconds": 30,
            "max_tokens": 1_000,
        }
        manifest.update(overrides)
        return manifest

    @staticmethod
    def _write(value):
        def action(root, _kwargs):
            (root / "app.txt").write_text(value, encoding="utf-8")
            return completed()

        return action

    def test_verified_candidate_is_integrated_only_in_isolated_clone(self):
        runner = FakeRunner([self._write("new\n")])

        report = execute(
            self._manifest(), self.project, ROOT, self.state, apply=False, runner=runner
        )

        self.assertEqual(report["status"], "completed", report.get("error"))
        self.assertEqual(report["tasks"][0]["status"], "integrated")
        self.assertFalse(report["applied"])
        self.assertEqual((self.project / "app.txt").read_text(encoding="utf-8"), "old\n")
        self.assertTrue(Path(report["patch"]).read_bytes())
        self.assertEqual(report["model_calls"], 1)
        self.assertEqual(report["usage"]["total_tokens"], 20)

    def test_memory_database_is_stored_as_an_absolute_recovery_binding(self):
        database = self.root / 'memory.sqlite'
        operator = MemoryStore(str(database), scope='project')
        operator.close()
        manifest = self._manifest(memory={'db': 'memory.sqlite', 'scope': 'project'})
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            validate_manifest(manifest, self.project, ROOT)
        finally:
            os.chdir(previous)
        self.assertEqual(manifest['memory']['db'], str(database.resolve()))

    def test_unknown_memory_scope_is_rejected_without_creating_it_or_calling_model(self):
        database = self.root / 'memory.sqlite'
        operator = MemoryStore(str(database), scope='project')
        operator.close()
        runner = FakeRunner([])

        with self.assertRaisesRegex(ValueError, 'scope'):
            execute(
                self._manifest(memory={'db': str(database), 'scope': 'typo'}),
                self.project,
                ROOT,
                self.state,
                runner=runner,
            )

        connection = sqlite3.connect(database)
        try:
            scopes = [row[0] for row in connection.execute('SELECT scope FROM scopes')]
        finally:
            connection.close()
        self.assertEqual(scopes, ['project'])
        self.assertEqual(runner.calls, [])

    def test_unsupported_memory_schema_is_rejected_before_model_call(self):
        database = self.root / 'memory.sqlite'
        operator = MemoryStore(str(database), scope='project')
        operator.close()
        connection = sqlite3.connect(database)
        try:
            connection.execute('PRAGMA user_version = 999')
        finally:
            connection.close()
        runner = FakeRunner([])

        with self.assertRaisesRegex(ValueError, 'schema version'):
            execute(
                self._manifest(memory={'db': str(database), 'scope': 'project'}),
                self.project,
                ROOT,
                self.state,
                runner=runner,
            )
        self.assertEqual(runner.calls, [])

    def test_failed_dependency_never_dispatches_or_reaches_source(self):
        first = self._task("first")
        second = self._task(
            "second",
            write_paths=["second.txt"],
            depends_on=["first"],
            acceptance=["dependency completed"],
        )
        runner = FakeRunner([self._write("wrong\n")])

        report = execute(
            self._manifest([first, second], max_attempts=1),
            self.project,
            ROOT,
            self.state,
            mode="direct",
            apply=True,
            runner=runner,
        )

        by_id = {task["id"]: task for task in report["tasks"]}
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(by_id["first"]["status"], "failed")
        self.assertEqual(by_id["second"]["error"], "dependency_failed")
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual((self.project / "app.txt").read_text(encoding="utf-8"), "old\n")
        self.assertFalse((self.project / "second.txt").exists())

    def test_manifest_and_worker_are_both_bound_to_write_scope(self):
        protected = self._task(write_paths=["tests"])
        with self.assertRaisesRegex(ValueError, "protected"):
            execute(self._manifest([protected]), self.project, ROOT, self.state, runner=FakeRunner([]))

        def escape(root, _kwargs):
            (root / "outside.txt").write_text("escape\n", encoding="utf-8")
            return completed()

        report = execute(
            self._manifest(max_attempts=1),
            self.project,
            ROOT,
            self.state,
            mode="direct",
            apply=True,
            runner=FakeRunner([escape]),
        )
        self.assertEqual(report["status"], "blocked")
        self.assertIn("outside its scope", report["tasks"][0]["error"])
        self.assertFalse((self.project / "outside.txt").exists())

    def test_closed_loop_repairs_then_escalates_before_integration(self):
        runner = FakeRunner(
            [self._write("wrong one\n"), self._write("wrong two\n"), self._write("new\n")]
        )

        report = execute(
            self._manifest(), self.project, ROOT, self.state, apply=False, runner=runner
        )

        self.assertEqual(report["status"], "completed", report.get("error"))
        self.assertEqual([call["model"] for call in runner.calls[:2]], ["gpt-5.6-luna"] * 2)
        self.assertEqual(runner.calls[2]["model"], "gpt-5.6-terra")
        self.assertIn("Observed verification failure", runner.calls[1]["prompt"])
        self.assertEqual(report["model_calls"], 3)
        self.assertEqual(report["usage"]["total_tokens"], 60)

    def test_stop_file_discards_a_completed_worker_result(self):
        def cancel_after_write(root, _kwargs):
            (root / "app.txt").write_text("new\n", encoding="utf-8")
            (root.parent / "STOP").write_text("stop", encoding="utf-8")
            return completed("done")

        report = execute(
            self._manifest(), self.project, ROOT, self.state, apply=True, runner=FakeRunner([cancel_after_write])
        )

        self.assertEqual(report["status"], "blocked")
        self.assertIn("Cancelled", report["tasks"][0]["error"])
        self.assertEqual((self.project / "app.txt").read_text(encoding="utf-8"), "old\n")

    def test_model_call_budget_stops_an_unverified_repair_loop(self):
        runner = FakeRunner([self._write("wrong\n")])

        report = execute(
            self._manifest(max_model_calls=1), self.project, ROOT, self.state, runner=runner
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(report["model_calls"], 1)
        self.assertIn("limit", report["tasks"][0]["error"])

    def test_independent_workers_really_overlap_and_integrate_both_patches(self):
        barrier = threading.Barrier(2)
        first = self._task('first')
        second = self._task('second', write_paths=['second.txt'], verify=['second'])
        checks = [self._check(), {'id': 'second', 'argv': [sys.executable, '-c',
                  "from pathlib import Path; assert Path('second.txt').read_text() == 'second'"], 'timeout_seconds': 10}]
        def worker(root, prompt, model, effort, **kwargs):
            barrier.wait(timeout=10)
            if '"second.txt"' in prompt:
                (root / 'second.txt').write_text('second', encoding='utf-8')
            else:
                (root / 'app.txt').write_text('new\n', encoding='utf-8')
            return completed()
        report = execute(self._manifest([first, second], checks=checks, integration_checks=['content', 'second'],
                                        max_parallel=2), self.project, ROOT, self.state, apply=True, runner=worker)
        self.assertEqual(report['status'], 'completed', report.get('error'))
        self.assertEqual((self.project / 'second.txt').read_text(), 'second')
        self.assertEqual(len(report['tasks']), 2)

    def test_memory_containment_during_inference_prevents_integration(self):
        database = self.root / 'memory.sqlite'
        operator = MemoryStore(str(database), scope='project')
        try:
            item = operator.execute('source-propose', {'scope': 'project', 'source_kind': 'external',
                                     'locator': 'test:source', 'content': 'candidate evidence'})['data']
            def contaminated(root, kwargs):
                (root / 'app.txt').write_text('new\n', encoding='utf-8')
                operator.execute('quarantine', {'scope': 'project', 'memory_id': item['memory_id'],
                                                'revision': 1, 'reason': 'test incident'})
                self.assertTrue(kwargs['cancelled']())
                return completed()
            report = execute(self._manifest(memory={'db': str(database), 'scope': 'project'}),
                             self.project, ROOT, self.state, apply=True, runner=FakeRunner([contaminated]))
            self.assertEqual(report['status'], 'blocked')
            self.assertEqual((self.project / 'app.txt').read_text(), 'old\n')
        finally:
            operator.close()

    def test_deleted_memory_database_is_not_recreated_by_runtime_monitor(self):
        database = self.root / 'memory.sqlite'
        operator = MemoryStore(str(database), scope='project')
        operator.close()

        def delete_binding(root, _kwargs):
            (root / 'app.txt').write_text('new\n', encoding='utf-8')
            database.unlink()
            return completed()

        report = execute(
            self._manifest(memory={'db': str(database), 'scope': 'project'}),
            self.project,
            ROOT,
            self.state,
            apply=True,
            runner=FakeRunner([delete_binding]),
        )

        self.assertEqual(report['status'], 'blocked')
        self.assertFalse(database.exists())
        self.assertEqual((self.project / 'app.txt').read_text(), 'old\n')

    def test_memory_epoch_change_during_final_check_rejects_unapplied_result(self):
        database = self.root / 'memory.sqlite'
        operator = MemoryStore(str(database), scope='project')
        try:
            candidate = operator.execute('source-propose', {
                'scope': 'project', 'source_kind': 'external',
                'locator': 'test:late-change', 'content': 'late evidence',
            })['data']
            activate = (
                "import sys; from pathlib import Path; "
                f"sys.path.insert(0, {str(ROOT / 'src')!r}); "
                "from se_codex.memory import MemoryStore; "
                f"store=MemoryStore({str(database)!r}, scope='project'); "
                f"store.execute('activate', {{'scope':'project','memory_id':{candidate['memory_id']!r},"
                "'revision':1,'evidence':'verified during final check','policy_version':'test-v1'}); "
                "store.close()"
            )
            checks = [self._check(), {
                'id': 'late-memory-change',
                'argv': [sys.executable, '-c', activate],
                'timeout_seconds': 10,
            }]
            report = execute(
                self._manifest(
                    checks=checks,
                    integration_checks=['content', 'late-memory-change'],
                    memory={'db': str(database), 'scope': 'project'},
                ),
                self.project,
                ROOT,
                self.state,
                apply=False,
                runner=FakeRunner([self._write('new\n')]),
            )
            self.assertEqual(report['status'], 'blocked')
            self.assertIn('memory', report['error'].casefold())
            self.assertEqual((self.project / 'app.txt').read_text(), 'old\n')
        finally:
            operator.close()

    def test_report_detects_modified_event_history(self):
        report = execute(self._manifest(), self.project, ROOT, self.state,
                         runner=FakeRunner([self._write('new\n')]))
        directory = Path(report['run_directory'])
        self.assertIn('verified', read_report(directory)['journal_integrity'])
        path = directory / 'events.jsonl'
        path.write_text(path.read_text(encoding='utf-8').replace('planned', 'changed', 1), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'hash'):
            read_report(directory)


if __name__ == "__main__":
    unittest.main()
