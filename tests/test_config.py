from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = {
    "sol": ("gpt-5.6-sol", "high"),
    "terra": ("gpt-5.6-terra", "medium"),
    "luna": ("gpt-5.6-luna", "low"),
}
SKILLS = ("se-kernel", "se-memory", "se-recover")


class CodexConfigurationTests(unittest.TestCase):
    def test_project_config_declares_portable_defaults(self) -> None:
        with (ROOT / ".codex" / "config.toml").open("rb") as config_file:
            config = tomllib.load(config_file)

        self.assertEqual(config["model"], "gpt-6-astra")
        self.assertEqual(config["model_reasoning_effort"], "high")
        self.assertEqual(config["sandbox_mode"], "workspace-write")
        self.assertEqual(config["approval_policy"], "on-request")
        self.assertNotEqual(config["sandbox_mode"], "danger-full-access")
        self.assertEqual(config["agents"], {
            "default_subagent_model": "gpt-5.6-terra",
            "default_subagent_reasoning_effort": "medium",
            "max_concurrent_threads_per_session": 3,
        })

    def test_custom_agents_have_explicit_routing_metadata(self) -> None:
        for name, (model, effort) in AGENTS.items():
            with self.subTest(agent=name):
                with (ROOT / ".codex" / "agents" / f"{name}.toml").open("rb") as agent_file:
                    agent = tomllib.load(agent_file)

                self.assertEqual(agent["name"], name)
                self.assertEqual(agent["model"], model)
                self.assertEqual(agent["model_reasoning_effort"], effort)
                self.assertTrue(agent["description"].strip())
                self.assertTrue(agent["developer_instructions"].strip())

    def test_skills_have_discoverable_frontmatter(self) -> None:
        for name in SKILLS:
            with self.subTest(skill=name):
                skill = (ROOT / ".agents" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertTrue(skill.startswith(f"---\nname: {name}\n"))
                self.assertIn("description:", skill.split("---", 2)[1])
                self.assertNotIn("TODO", skill)


if __name__ == "__main__":
    unittest.main()
