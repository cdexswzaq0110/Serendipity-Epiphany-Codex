from __future__ import annotations

import tempfile
import unittest
import hashlib
from pathlib import Path
from unittest.mock import patch

from src.se_codex.install import InstallError, _rollback, install_project


class InstallProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        self._make_source(self.source)
        self.target.mkdir()
        (self.target / ".git").mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_dry_run_returns_only_plans_and_conflicts(self) -> None:
        result = install_project(self.source, self.target)

        self.assertEqual(set(result), {"paths", "conflicts"})
        self.assertEqual(result["conflicts"], [])
        self.assertIn("AGENTS.md", result["paths"])
        self.assertIn(".se-codex/se.py", result["paths"])
        self.assertFalse((self.target / "AGENTS.md").exists())

    def test_install_rewrites_runtime_links_and_is_idempotent(self) -> None:
        installed = install_project(self.source, self.target, apply=True)

        self.assertIn("AGENTS.md", installed["created"])
        self.assertIn(
            ".se-codex/docs/MEMORY_RUNBOOK.md",
            (self.target / "AGENTS.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "../../../.se-codex/docs/MEMORY_RUNBOOK.md",
            (self.target / ".agents/skills/se-demo/SKILL.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "python .se-codex/se.py",
            (self.target / ".codex/hooks.json").read_text(encoding="utf-8"),
        )

        repeated = install_project(self.source, self.target, apply=True)
        self.assertEqual(repeated["created"], [])
        self.assertEqual(repeated["conflicts"], [])
        self.assertIn("AGENTS.md", repeated["already_installed"])

    def test_conflict_rejects_the_entire_batch_without_writes(self) -> None:
        (self.target / "AGENTS.md").write_text("user instructions\n", encoding="utf-8")
        result = install_project(self.source, self.target, apply=True)

        self.assertEqual(result["created"], [])
        self.assertEqual(result["conflicts"], ["AGENTS.md"])
        self.assertEqual((self.target / "AGENTS.md").read_text(encoding="utf-8"), "user instructions\n")
        self.assertFalse((self.target / ".se-codex").exists())

    def test_partial_conflict_never_writes_other_artifacts(self) -> None:
        (self.target / ".codex").mkdir()
        (self.target / ".codex" / "config.toml").write_text("user config\n", encoding="utf-8")

        result = install_project(self.source, self.target, apply=True)

        self.assertEqual(result["created"], [])
        self.assertIn(".codex/config.toml", result["conflicts"])
        self.assertFalse((self.target / "AGENTS.md").exists())

    def test_file_parent_is_rejected_during_preflight(self) -> None:
        (self.target / ".se-codex").write_text("parent file\n", encoding="utf-8")

        with self.assertRaises(ValueError):
            install_project(self.source, self.target, apply=True)

        self.assertEqual((self.target / ".se-codex").read_text(encoding="utf-8"), "parent file\n")

    def test_ancestor_reparse_point_is_rejected_by_preflight(self) -> None:
        reparse = self.target / ".se-codex"
        with patch("src.se_codex.install._is_reparse_point", side_effect=lambda path: path == reparse):
            with self.assertRaises(InstallError):
                install_project(self.source, self.target, apply=True)
        self.assertFalse((self.target / "AGENTS.md").exists())

    def test_rollback_rechecks_path_before_unlinking(self) -> None:
        parent = self.target / ".se-codex"
        parent.mkdir()
        created = parent / "created.txt"
        created.write_text("created\n", encoding="utf-8")
        digest = hashlib.sha256(created.read_bytes()).hexdigest()
        relative = Path(".se-codex/created.txt")

        with patch("src.se_codex.install._is_reparse_point", side_effect=lambda path: path == parent):
            _rollback(self.target, [(created, relative, digest)])

        self.assertTrue(created.exists())

    def test_target_symlink_is_rejected_without_escape(self) -> None:
        linked_target = self.root / "linked-target"
        try:
            linked_target.symlink_to(self.target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Symlinks are unavailable: {error}")

        with self.assertRaises(ValueError):
            install_project(self.source, linked_target, apply=True)
        self.assertFalse((self.target / "AGENTS.md").exists())

    @staticmethod
    def _make_source(source: Path) -> None:
        (source / ".codex/agents").mkdir(parents=True)
        (source / ".agents/skills/se-demo/references").mkdir(parents=True)
        (source / "src/se_codex").mkdir(parents=True)
        (source / "docs").mkdir()

        (source / "AGENTS.md").write_text(
            "Read docs/MEMORY_RUNBOOK.md before recovery.\n", encoding="utf-8"
        )
        (source / "README.md").write_text("# Demo\n", encoding="utf-8")
        (source / "LICENSE").write_text("MIT\n", encoding="utf-8")
        (source / "se.py").write_text("print('launcher')\n", encoding="utf-8")
        (source / "docs/MEMORY_RUNBOOK.md").write_text("# Runbook\n", encoding="utf-8")
        (source / "src/se_codex/memory.py").write_text("VALUE = 1\n", encoding="utf-8")
        (source / ".codex/config.toml").write_text('model = "gpt-6-astra"\n', encoding="utf-8")
        (source / ".codex/agents/terra.toml").write_text('name = "terra"\n', encoding="utf-8")
        (source / ".codex/hooks.json").write_text(
            '{"command": "python se.py memory recall"}\n', encoding="utf-8"
        )
        (source / ".agents/skills/se-demo/SKILL.md").write_text(
            "---\nname: se-demo\ndescription: Demo.\n---\n"
            "Read [runbook](../../../docs/MEMORY_RUNBOOK.md).\n",
            encoding="utf-8",
        )
        (source / ".agents/skills/se-demo/references/guide.md").write_text(
            "# Guide\n", encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main()
