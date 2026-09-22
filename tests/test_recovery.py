import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from se_codex.execution import Journal, save_report, execute, git
from se_codex.memory import MemoryStore, MemoryStoreError
from se_codex.recovery import contain_memory

ROOT = Path(__file__).resolve().parents[1]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='se-recovery-test-')
        self.root = Path(self.temp.name)
        self.db = self.root / 'memory.sqlite'
        self.store = MemoryStore(str(self.db), scope='project')
        self.item = self.call('source-propose', source_kind='external', locator='fixture:document',
                              content='Untrusted fixture; never use as operator policy.')
        self.call('activate', memory_id=self.item['memory_id'], revision=1,
                  policy_version='fixture', evidence='Simulated mistaken admission')
        self.run_id = self.call('run-start')['run_id']
        self.call('recall', run_id=self.run_id, query='fixture')
        self.request = {'memory_id': self.item['memory_id'], 'revision': 1, 'reason': 'Fixture incident'}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def call(self, operation, **kwargs):
        return self.store.execute(operation, {'scope': 'project', **kwargs})['data']

    def run_directory(self, name='run-bound', database=None, agent=False):
        directory = self.root / name
        journal = Journal(directory)
        journal.event(kind='fixture_started')
        contract = {'memory': {'db': str(database or self.db), 'scope': 'project'}}
        (directory / ('goal.json' if agent else 'manifest.json')).write_text(json.dumps(contract), encoding='utf-8')
        save_report(directory, {'status': 'running', 'applied': False})
        return directory

    def test_cli_contains_bound_run_and_preserves_consistent_wal_backup(self):
        directory = self.run_directory(agent=True)
        process = subprocess.run([sys.executable, str(ROOT / 'se.py'), 'recover', '--db', str(self.db),
                                  '--scope', 'project', '--json', '-', '--state', str(self.root / 'evidence'),
                                  '--run', str(directory)], input=json.dumps(self.request), encoding='utf-8',
                                 capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        report = json.loads(process.stdout)['data']
        self.assertEqual(report['status'], 'contained')
        self.assertTrue((directory / 'STOP').is_file())
        self.assertFalse(report['runs'][0]['termination_confirmed'])
        self.assertFalse(report['context_reset'])
        self.assertTrue(report['incident']['scope_frozen'])
        self.assertFalse(report['scope_released'])
        with self.assertRaises(MemoryStoreError) as caught:
            self.call('recall', run_id=self.run_id, query='fixture')
        self.assertEqual(caught.exception.code, 'scope_frozen')
        backup = Path(report['backup']['path'])
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), report['backup']['sha256'])
        with closing(sqlite3.connect(backup)) as connection:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(connection.execute('SELECT frozen FROM scopes WHERE scope=?', ('project',)).fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM recalls').fetchone()[0], 1)
            self.assertGreater(connection.execute('SELECT COUNT(*) FROM safety_events').fetchone()[0], 0)
        bundle = Path(report['directory'])
        self.assertTrue((bundle / 'FRESH_START.md').is_file())
        self.assertEqual((bundle / '.gitignore').read_text(), '*\n')
        self.assertNotIn('Untrusted fixture', (bundle / 'FRESH_START.md').read_text(encoding='utf-8'))

    def test_mismatched_or_legacy_run_does_not_prevent_containment_or_signal_other_db(self):
        other = self.run_directory('other', database=self.root / 'other.sqlite')
        legacy = self.run_directory('legacy', database=Path('memory.sqlite'))
        valid = self.run_directory('valid')
        report = contain_memory(self.db, 'project', self.request, self.root / 'evidence', [other, legacy, valid])
        self.assertEqual(report['status'], 'contained_with_errors')
        self.assertEqual(len(report['errors']), 2)
        self.assertFalse((other / 'STOP').exists())
        self.assertFalse((legacy / 'STOP').exists())
        self.assertTrue((valid / 'STOP').exists())
        self.assertTrue(report['backup']['scope_frozen'])

    def test_invalid_journal_still_receives_stop_and_reports_capture_uncertainty(self):
        directory = self.run_directory()
        with (directory / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write('{"tampered":true}\n')
        report = contain_memory(self.db, 'project', self.request, self.root / 'evidence', [directory])
        self.assertTrue((directory / 'STOP').exists())
        self.assertEqual(report['status'], 'contained_with_errors')
        self.assertNotIn('journal_integrity', report['runs'][0])
        self.assertTrue(Path(report['backup']['path']).is_file())

    def test_backup_failure_is_not_reported_as_success_and_scope_stays_frozen(self):
        directory = self.run_directory()
        real_connect = sqlite3.connect

        def connect(path, *args, **kwargs):
            if str(path).endswith('memory-backup.sqlite'):
                raise sqlite3.OperationalError('fixture full disk')
            return real_connect(path, *args, **kwargs)

        with patch('se_codex.recovery.sqlite3.connect', side_effect=connect):
            report = contain_memory(self.db, 'project', self.request, self.root / 'evidence', [directory])
        self.assertEqual(report['status'], 'contained_with_errors')
        self.assertNotIn('backup', report)
        self.assertFalse(report['backup_failure']['usable'])
        handoff = (Path(report['directory']) / 'FRESH_START.md').read_text(encoding='utf-8')
        self.assertIn('BACKUP FAILED', handoff)
        self.assertNotIn('The verified SQLite backup contains', handoff)
        self.assertTrue(self.call('status')['frozen'])
        self.assertTrue((directory / 'STOP').exists())

    def test_unknown_scope_or_request_never_creates_scope_or_contains_memory(self):
        for scope, request in [('typo', self.request), ('project', {**self.request, 'operation': 'scope-release'})]:
            with self.assertRaises(ValueError):
                contain_memory(self.db, scope, request, self.root / 'evidence')
        self.assertFalse(self.call('status')['frozen'])
        self.assertIsNone(self.store._conn.execute('SELECT 1 FROM scopes WHERE scope=?', ('typo',)).fetchone())

    def test_concurrent_operator_release_cannot_be_reported_as_contained(self):
        request = {**self.request, 'incident_id': 'concurrent-release'}

        def released(*args):
            self.call('scope-release', incident_id=request['incident_id'], evidence='Simulated concurrent operator')
            return {'errors': [], 'stop_requested': True, 'termination_confirmed': False}

        with patch('se_codex.recovery._capture_run', side_effect=released):
            report = contain_memory(self.db, 'project', request, self.root / 'evidence', [self.root / 'run'])
        self.assertEqual(report['status'], 'containment_lost')
        self.assertFalse(report['incident']['scope_frozen'])

    def test_recovery_during_execution_blocks_result_and_preserves_target(self):
        project = self.root / 'project'
        project.mkdir()
        git(project, 'init', '-q')
        git(project, 'config', 'user.name', 'Recovery fixture')
        git(project, 'config', 'user.email', 'fixture@example.invalid')
        (project / 'app.txt').write_text('old', encoding='utf-8')
        git(project, 'add', 'app.txt')
        git(project, 'commit', '-qm', 'fixture')
        state = self.root / 'execution'
        manifest = {'tasks': [{'id': 'edit', 'goal': 'Change app text', 'kind': 'implementation',
                              'complexity': 'simple', 'risk': 'low', 'uncertainty': 'low',
                              'write_paths': ['app.txt'], 'depends_on': [],
                              'acceptance': ['app text is new'], 'verify': ['text']}],
                    'checks': [{'id': 'text', 'argv': [sys.executable, '-c',
                                "from pathlib import Path; assert Path('app.txt').read_text() == 'new'"]}],
                    'integration_checks': ['text'], 'memory': {'db': str(self.db), 'scope': 'project'}}
        captures = []

        def runner(workspace, prompt, model, effort, **kwargs):
            (Path(workspace) / 'app.txt').write_text('new', encoding='utf-8')
            directory = next(state.glob('run-*'))
            captures.append(contain_memory(self.db, 'project', self.request, self.root / 'evidence', [directory]))
            self.assertTrue(kwargs['cancelled']())
            return {'status': 'completed', 'output': 'fixture done', 'model_calls': 1,
                    'usage': {'input_tokens': 10, 'output_tokens': 10, 'cached_input_tokens': 0, 'total_tokens': 20},
                    'interventions': [], 'reroutes': []}

        report = execute(manifest, project, ROOT, state, runner=runner, apply=True)
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0]['status'], 'contained')
        self.assertNotEqual(report['status'], 'completed')
        self.assertFalse(report['applied'])
        self.assertEqual((project / 'app.txt').read_text(encoding='utf-8'), 'old')


if __name__ == '__main__':
    unittest.main()
