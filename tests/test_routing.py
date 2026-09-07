import json
import tempfile
import unittest
from pathlib import Path

from se_codex.routing import plan_tasks, route_task, validate_capabilities


CONFIG = """[dispatch]\nmax_parallel = 2\n"""
AGENTS = {
    "astra": 'model = "gpt-6-astra"\neffort = "high"\n',
    "sol": 'model = "gpt-5.6-sol"\neffort = "high"\n',
    "terra": 'model = "gpt-5.6-terra"\neffort = "medium"\n',
    "luna": 'model = "gpt-5.6-luna"\neffort = "low"\n',
}


def task(task_id, *, kind="implementation", complexity="standard", risk="low", uncertainty="low", write_paths=None, depends_on=None):
    return {
        "id": task_id, "goal": task_id, "kind": kind, "complexity": complexity,
        "risk": risk, "uncertainty": uncertainty, "write_paths": write_paths or [],
        "depends_on": depends_on or [], "acceptance": ["works"],
    }


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / ".codex" / "agents").mkdir(parents=True)
        (root / ".codex" / "config.toml").write_text(CONFIG)
        for name, content in AGENTS.items():
            (root / ".codex" / "agents" / f"{name}.toml").write_text(content)
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_difficulty_routing(self):
        self.assertEqual(route_task(task("a", kind="architecture"), self.root)["role"], "kernel")
        self.assertEqual(route_task(task("s", complexity="complex"), self.root)["role"], "sol")
        self.assertEqual(route_task(task("l", complexity="simple"), self.root)["role"], "luna")
        self.assertEqual(route_task(task("t"), self.root)["role"], "terra")

    def test_waves_respect_dependencies_conflicts_and_config_limit(self):
        result = plan_tasks([
            task("a", write_paths=["src/a.py"]),
            task("b", write_paths=["src/b.py"]),
            task("c", write_paths=["src/a.py"], depends_on=["a"]),
        ], self.root)
        self.assertEqual(result["max_parallel"], 2)
        self.assertEqual(result["waves"], [["a", "b"], ["c"]])

    def test_rejects_traversal_and_cycles(self):
        with self.assertRaises(ValueError):
            route_task(task("bad", write_paths=["../outside.py"]), self.root)
        with self.assertRaises(ValueError):
            plan_tasks([task("a", depends_on=["b"]), task("b", depends_on=["a"])], self.root)

    def test_mock_capability_catalog(self):
        plan = plan_tasks([task("a")], self.root)
        validate_capabilities(plan, {"gpt-5.6-terra": {"efforts": ["medium"]}})
        with self.assertRaises(ValueError):
            validate_capabilities(plan, {"gpt-5.6-terra": {"efforts": ["low"]}})

    def test_actual_repo_config_and_example_tasks(self):
        root = Path(__file__).resolve().parents[1]
        tasks = json.loads((root / "examples" / "tasks.json").read_text(encoding="utf-8"))
        result = plan_tasks(tasks, root)
        self.assertEqual(result["max_parallel"], 3)
        self.assertEqual(result["waves"], [["architecture", "research"], ["implementation"], ["review"]])
        by_id = {item["id"]: item for item in result["tasks"]}
        self.assertEqual(by_id["architecture"]["model"], "gpt-6-astra")
        self.assertEqual(by_id["implementation"]["model"], "gpt-5.6-sol")
        self.assertEqual(by_id["review"]["model"], "gpt-5.6-terra")
        self.assertEqual(by_id["research"]["model"], "gpt-5.6-luna")

    def test_windows_path_rejections_and_casefold_conflict(self):
        for path in ("C:relative.py", "C:/absolute.py", "\\\\server\\share\\x.py", "src/x:a.py", "src/*.py", "src/name. ", "src/NUL.txt", "src/a\x00.py"):
            with self.assertRaises(ValueError, msg=path):
                route_task(task("bad", write_paths=[path]), self.root)
        result = plan_tasks([task("a", write_paths=["Src/Shared.py"]), task("b", write_paths=["src/shared.py"])], self.root)
        self.assertEqual(result["waves"], [["a"], ["b"]])
        self.assertEqual(result['tasks'][0]['goal'], 'a')
        self.assertEqual(result['tasks'][0]['acceptance'], ['works'])
        self.assertEqual(result['tasks'][0]['write_paths'], ['Src/Shared.py'])

    def test_existing_symlink_alias_outside_project_is_rejected(self):
        outside = Path(self.tmp.name).parent / (Path(self.tmp.name).name + "-outside")
        outside.mkdir()
        link = self.root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            outside.rmdir()
            self.skipTest("symlinks unavailable on this Windows environment")
        try:
            with self.assertRaises(ValueError):
                route_task(task("bad", write_paths=["linked/output.py"]), self.root)
        finally:
            link.unlink(missing_ok=True)
            outside.rmdir()

    def test_bad_task_shapes_are_value_errors(self):
        for bad in (None, "text", [task("ok"), None]):
            with self.assertRaises(ValueError):
                plan_tasks(bad, self.root)
        for key, value in (("goal", ""), ("acceptance", [""]), ("acceptance", [1]), ('kind', []), ('risk', {})):
            bad = task("bad")
            bad[key] = value
            with self.assertRaises(ValueError):
                route_task(bad, self.root)


if __name__ == "__main__":
    unittest.main()
