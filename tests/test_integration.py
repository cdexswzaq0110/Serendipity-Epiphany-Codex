import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest

from se_codex.install import install_project
from se_codex.protection import memory_config

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def command(self, entry, *args, cwd=None, stdin=None):
        return subprocess.run([sys.executable, '-I', str(entry), *args], cwd=cwd or ROOT, input=stdin,
                              capture_output=True, text=True, encoding='utf-8', timeout=30)

    def test_demo_cli_and_bad_json(self):
        demo = self.command(ROOT / 'se.py', 'demo')
        self.assertEqual(demo.returncode, 0, demo.stderr)
        self.assertTrue(json.loads(demo.stdout)['data']['passed'])
        bad = self.command(ROOT / 'se.py', 'plan', '--json', '-', stdin='not-json')
        self.assertEqual(bad.returncode, 2)
        self.assertFalse(json.loads(bad.stdout)['ok'])

    def test_mcp_wire_is_utf8_json_only_and_authority_is_fixed(self):
        with tempfile.TemporaryDirectory() as directory:
            messages = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'memory_call', 'arguments': {'operation': 'source-propose', 'arguments': {'source_kind': 'external', 'locator': 'fixture:來源', 'content': '中文候選資料'}}}},
                {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'memory_call', 'arguments': {'operation': 'activate', 'arguments': {}}}},
            ]
            process = self.command(ROOT / 'se.py', 'mcp', '--db', str(Path(directory) / 'wire.sqlite'), '--scope', 'wire', stdin=''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in messages))
            self.assertEqual(process.returncode, 0, process.stderr)
            rows = [json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual([row['id'] for row in rows], [1, 2, 3, 4])
            self.assertEqual(rows[0]['result']['protocolVersion'], '2025-11-25')
            self.assertFalse(rows[2]['result']['isError'])
            self.assertIn('error', rows[3])

    def test_real_project_install_links_cli_hooks_and_repeat(self):
        with tempfile.TemporaryDirectory(prefix='se-install-') as directory:
            target = Path(directory) / 'project with spaces'
            target.mkdir()
            subprocess.run(['git', 'init', '-q', str(target)], check=True, capture_output=True)
            installed = install_project(ROOT, target, apply=True)
            self.assertFalse(installed['conflicts'])
            entry = target / '.se-codex/se.py'
            from se_codex.execution import skill_catalog
            self.assertEqual(len(skill_catalog(target / '.se-codex')), 8)
            self.assertTrue((target / '.se-codex/docs/EXECUTION.md').is_file())
            governance = (target / '.agents/skills/se-memory/references/governance.md').read_text(encoding='utf-8')
            runbook = re.search(r'`([^`]*docs/MEMORY_RUNBOOK.md)`', governance).group(1)
            self.assertTrue((target / runbook).is_file())
            result = self.command(entry, 'demo', cwd=target)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['data']['passed'])
            result = self.command(entry, 'doctor', '--project', str(target), cwd=target)
            self.assertEqual(result.returncode, 0, result.stderr)
            for markdown in (target / '.agents/skills').rglob('*.md'):
                for link in re.findall(r'\]\(([^)]+)\)', markdown.read_text(encoding='utf-8')):
                    if '://' not in link:
                        self.assertTrue((markdown.parent / link).resolve().is_file(), str(markdown) + ': ' + link)
            hook = json.loads((target / '.codex/hooks.json').read_text(encoding='utf-8'))['hooks']['PreToolUse'][0]['hooks'][0]['command']
            nested = target / 'nested'
            nested.mkdir()
            # Execute the exact trusted, generated hook command through the native shell.
            shell = ['powershell', '-NoProfile', '-Command', hook] if sys.platform == 'win32' else ['sh', '-c', hook]
            event = {'hook_event_name': 'PreToolUse', 'tool_name': 'spawn_agent', 'tool_input': {}}
            process = subprocess.run(shell, cwd=nested, input=json.dumps(event), capture_output=True, text=True, encoding='utf-8', timeout=20)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')
            self.assertEqual(install_project(ROOT, target, apply=True)['created'], [])

    def test_generated_config_binds_db_and_protects_broker(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'project'
            state = project / '.se-state'
            state.mkdir(parents=True)
            fragment = memory_config(project, ROOT, state / 'memory.sqlite', 'project')
            parsed = tomllib.loads(fragment)
            server = parsed['mcp_servers']['se_memory']
            self.assertIn('-I', server['args'])
            self.assertEqual(server['args'][-1], 'project')
            rules = parsed['permissions']['se-worker']['filesystem']
            self.assertEqual(rules[str(state.resolve())], 'deny')
            self.assertEqual(rules[str(ROOT)], 'read')
            self.assertEqual(parsed['permissions']['se-worker']['network']['enabled'], False)
            with self.assertRaises(ValueError):
                memory_config(project, ROOT, project / 'memory.sqlite', 'project')


if __name__ == '__main__':
    unittest.main()
