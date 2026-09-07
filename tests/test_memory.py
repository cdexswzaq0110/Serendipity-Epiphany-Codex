from concurrent.futures import ThreadPoolExecutor
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from se_codex.memory import MemoryStore, MemoryStoreError
from se_codex.mcp import dispatch


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / 'memory.sqlite')
        self.operator = MemoryStore(self.db, scope='test')
        self.worker = MemoryStore(self.db, scope='test', role='worker')

    def tearDown(self):
        self.worker.close()
        self.operator.close()
        self.temp.cleanup()

    def call(self, operation, **args):
        return self.operator.execute(operation, {'scope': 'test', **args})['data']

    def work(self, operation, **args):
        return self.worker.execute(operation, {'scope': 'test', **args})['data']

    def memory(self, content='build uses python', active=True, **args):
        item = self.work('source-propose', source_kind='external', locator='fixture:reviewed', content=content, **args)
        if active:
            self.call('activate', memory_id=item['memory_id'], revision=1, evidence='fixture checked', policy_version='v1')
        return item

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(MemoryStoreError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_candidate_never_recalled_or_worker_activated(self):
        item = self.memory(active=False)
        run = self.work('run-start')['run_id']
        self.assertEqual(self.work('recall', run_id=run, query='python')['memories'], [])
        self.assert_code('permission_denied', self.work, 'activate', memory_id=item['memory_id'], revision=1, evidence='fake', policy_version='v1')
        self.assert_code('scope_mismatch', self.worker.execute, 'run-start', {'scope': 'other'})

    def test_receipt_is_committed_before_return_and_retains_external_lineage(self):
        item = self.memory()
        result = self.work('recall', run_id=self.work('run-start')['run_id'], query='python')
        self.assertEqual(result['memories'][0]['source']['kind'], 'external')
        row = self.operator._conn.execute('SELECT * FROM recalls WHERE receipt_id = ?', (result['receipt_ids'][0],)).fetchone()
        self.assertEqual(row['delivered_sha256'], item['content_sha256'])

    def test_receipt_failure_returns_no_content_and_rolls_back(self):
        self.memory()
        self.operator._conn.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON recalls BEGIN SELECT RAISE(ABORT, 'fault'); END")
        self.assert_code('storage_error', self.work, 'recall', run_id=self.work('run-start')['run_id'], query='python')
        self.assertEqual(self.operator._conn.execute('SELECT COUNT(*) FROM recalls').fetchone()[0], 0)

    def test_expiry_secret_and_capacity(self):
        item = self.memory(active=False, expires_at='2000-01-01T00:00:00Z')
        self.assert_code('expired', self.call, 'activate', memory_id=item['memory_id'], revision=1, evidence='checked', policy_version='v1')
        self.assert_code('sensitive_content', self.memory, '-----BEGIN PRIVATE KEY-----', active=False)
        with MemoryStore(':memory:', scope='test', max_memories=1) as small:
            data = {'scope': 'test', 'source_kind': 'tool', 'locator': 'fixture', 'content': 'fact'}
            small.execute('source-propose', data)
            self.assert_code('capacity_exceeded', small.execute, 'source-propose', data)

    def test_recall_character_budget(self):
        self.memory('python ' * 20)
        with MemoryStore(self.db, scope='test', role='worker', max_recall_chars=30) as small:
            run = small.execute('run-start', {'scope': 'test'})['data']['run_id']
            result = small.execute('recall', {'scope': 'test', 'run_id': run, 'query': 'python'})
            self.assertEqual(result['data']['memories'], [])

    def test_candidate_revision_cannot_replace_active_memory(self):
        item = self.memory()
        run = self.work('run-start')['run_id']
        candidate = self.work('memory-derive', run_id=run, memory_id=item['memory_id'], content='python candidate', source_ids=[item['source_id']])
        self.assertEqual(self.work('recall', run_id=run, query='python')['memories'][0]['revision'], 1)
        self.call('activate', memory_id=item['memory_id'], revision=candidate['revision'], evidence='reviewed correction', policy_version='v1')
        self.assert_code('stale_epoch', self.work, 'recall', run_id=run, query='python')
        new_run = self.work('run-start')['run_id']
        self.assertEqual(self.work('recall', run_id=new_run, query='python')['memories'][0]['revision'], 2)

    def chain(self):
        item = self.memory()
        used = self.work('run-start')['run_id']
        self.work('recall', run_id=used, query='python')
        artifact = self.work('artifact-record', run_id=used, kind='file', locator='result.txt', content='result')
        downstream = self.work('run-start')['run_id']
        self.work('artifact-use', run_id=downstream, artifact_id=artifact['artifact_id'], revision=1)
        derived = self.work('memory-derive', run_id=downstream, content='derived python', parent_artifacts=[{'id': artifact['artifact_id'], 'revision': 1}])
        exposed = self.work('run-start')['run_id']
        self.work('source-use', run_id=exposed, source_id=item['source_id'], relation='observed')
        effect = self.work('effect-plan', run_id=downstream, tool='fake-export', target='fixture', idempotency_key='export-1')
        self.work('effect-record', effect_id=effect['effect_id'], status='uncertain')
        return item, used, artifact, downstream, derived, exposed, effect

    def test_containment_follows_transitive_lineage_and_records_effects(self):
        item, used, artifact, downstream, derived, exposed, effect = self.chain()
        report = self.call('revoke', memory_id=item['memory_id'], revision=1, reason='incorrect source')
        causal = {event['object']['id'] for event in report['causal']}
        self.assertTrue({used, artifact['artifact_id'], downstream, derived['memory_id'], effect['effect_id']} <= causal)
        self.assertIn(exposed, {event['object']['id'] for event in report['exposed']})
        self.assertEqual(report['effects'][0]['status'], 'uncertain')
        self.assert_code('scope_frozen', self.work, 'run-start')
        self.call('scope-release', incident_id=report['incident_id'], evidence='outputs isolated; external export unresolved, disabled')
        self.assert_code('stale_epoch', self.work, 'recall', run_id=used, query='python')
        self.assert_code('unsafe_provenance', self.call, 'activate', memory_id=derived['memory_id'], revision=1, policy_version='v2', evidence='attempt')
        new = self.work('run-start')['run_id']
        self.assert_code('unsafe_provenance', self.work, 'memory-derive', run_id=new, content='bad', source_ids=[item['source_id']])
        self.assert_code('quarantined', self.work, 'artifact-use', run_id=new, artifact_id=artifact['artifact_id'], revision=1)

    def test_revoke_same_incident_is_idempotent(self):
        item = self.memory()
        args = {'memory_id': item['memory_id'], 'revision': 1, 'reason': 'wrong', 'incident_id': 'fixture-incident'}
        one = self.call('revoke', **args)
        count = self.operator._conn.execute('SELECT COUNT(*) FROM safety_events').fetchone()[0]
        two = self.call('revoke', **args)
        self.assertTrue(two['idempotent'])
        self.assertEqual(one['memory_epoch'], two['memory_epoch'])
        self.assertEqual(count, self.operator._conn.execute('SELECT COUNT(*) FROM safety_events').fetchone()[0])

    def test_restore_while_frozen_preserves_revocation_and_retires_new_content(self):
        bad = self.memory('python wrong')
        good = self.memory('python correct')
        snapshot = self.call('snapshot')
        newer = self.memory('python unreviewed baseline')
        incident = self.call('revoke', memory_id=bad['memory_id'], revision=1, reason='wrong')
        plan = self.call('restore-plan', snapshot=snapshot)
        self.assertEqual(plan['diff']['skip_revoked'][0]['memory_id'], bad['memory_id'])
        self.assertIn({'memory_id': newer['memory_id'], 'revision': 1}, plan['diff']['retire'])
        restored = self.call('restore', plan_id=plan['plan_id'])
        self.assertTrue(restored['scope_frozen'])
        self.assertTrue(self.call('restore', plan_id=plan['plan_id'])['idempotent'])
        self.call('scope-release', incident_id=incident['incident_id'], evidence='audit complete')
        self.assert_code('scope_frozen', self.work, 'run-start')
        self.call('scope-release', incident_id=restored['incident_id'], evidence='baseline verified')
        result = self.work('recall', run_id=self.work('run-start')['run_id'], query='python')
        self.assertEqual([m['memory_id'] for m in result['memories']], [good['memory_id']])
        self.assert_code('revoked', self.call, 'activate', memory_id=bad['memory_id'], revision=1, evidence='attempt', policy_version='v2')

    def test_snapshot_tamper_foreign_store_and_stale_plan_fail_closed(self):
        self.memory()
        snapshot = self.call('snapshot')
        corrupted = copy.deepcopy(snapshot)
        corrupted['payload']['memories'][0]['content'] = 'injected'
        self.assert_code('snapshot_corrupt', self.call, 'restore-plan', snapshot=corrupted)
        with MemoryStore(':memory:', scope='test') as other:
            self.assert_code('foreign_snapshot', other.execute, 'restore-plan', {'scope': 'test', 'snapshot': snapshot})
        plan = self.call('restore-plan', snapshot=snapshot)
        self.memory('changed')
        self.assert_code('restore_conflict', self.call, 'restore', plan_id=plan['plan_id'])

    def test_revoked_policy_cannot_return_from_snapshot(self):
        item = self.memory()
        snapshot = self.call('snapshot')
        incident = self.call('policy-revoke', policy_version='v1', reason='admission check defective')
        plan = self.call('restore-plan', snapshot=snapshot)
        self.assertEqual(plan['diff']['skip_revoked'][0]['memory_id'], item['memory_id'])
        self.call('scope-release', incident_id=incident['incident_id'], evidence='isolated')
        self.assert_code('revoked_policy', self.call, 'activate', memory_id=item['memory_id'], revision=1, policy_version='v1', evidence='attempt')

    def test_operator_can_revalidate_false_alarm_but_not_derived_revoked_source(self):
        item = self.memory()
        incident = self.work('quarantine', memory_id=item['memory_id'], revision=1, reason='suspected')
        self.call('scope-release', incident_id=incident['incident_id'], evidence='independent check confirmed false alarm')
        self.call('activate', memory_id=item['memory_id'], revision=1, policy_version='v2', evidence='independently revalidated original source')
        run = self.work('run-start')['run_id']
        derived = self.work('memory-derive', run_id=run, content='python derived', source_ids=[item['source_id']])
        incident = self.call('revoke', memory_id=item['memory_id'], revision=1, reason='now proven wrong')
        self.call('scope-release', incident_id=incident['incident_id'], evidence='isolated')
        self.assert_code('unsafe_provenance', self.call, 'activate', memory_id=derived['memory_id'], revision=1, policy_version='v3', evidence='attempt to revalidate')

    def test_restore_missing_revision_keeps_store_journal(self):
        item = self.memory()
        snapshot = self.call('snapshot')
        self.operator._conn.execute('DELETE FROM memories WHERE memory_id = ?', (item['memory_id'],))
        plan = self.call('restore-plan', snapshot=snapshot)
        self.assertEqual(len(plan['diff']['add']), 1)
        restored = self.call('restore', plan_id=plan['plan_id'])
        self.assertEqual(len(restored['restored']), 1)
        self.assertGreater(self.call('status')['event_seq'], snapshot['manifest']['event_seq'])
        self.call('scope-release', incident_id=restored['incident_id'], evidence='checked restored content and journal')
        result = self.work('recall', run_id=self.work('run-start')['run_id'], query='python')
        self.assertEqual(result['memories'][0]['memory_id'], item['memory_id'])

    def test_corrupt_content_is_not_recalled_but_can_be_contained(self):
        item = self.memory()
        run = self.work('run-start')['run_id']
        self.operator._conn.execute("UPDATE memories SET content = 'tampered' WHERE memory_id = ?", (item['memory_id'],))
        self.assert_code('storage_integrity', self.work, 'recall', run_id=run, query='python')
        self.assertTrue(self.call('revoke', memory_id=item['memory_id'], revision=1, reason='hash mismatch')['scope_frozen'])

    def test_concurrent_recall_and_revoke_are_serialized(self):
        item = self.memory()
        run = self.work('run-start')['run_id']
        def recall():
            try:
                return self.work('recall', run_id=run, query='python')
            except MemoryStoreError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(recall)
            self.call('revoke', memory_id=item['memory_id'], revision=1, reason='concurrent revoke')
            result = future.result()
        self.assertTrue(isinstance(result, dict) or result == 'scope_frozen')
        self.assert_code('scope_frozen', self.work, 'recall', run_id=run, query='python')

    def test_compensation_requires_applied_linked_effect_and_operator_evidence(self):
        run = self.work('run-start')['run_id']
        original = self.work('effect-plan', run_id=run, tool='fixture', target='local', idempotency_key='one')
        self.work('effect-record', effect_id=original['effect_id'], status='applied', external_reference='fixture:1')
        self.assert_code('permission_denied', self.work, 'effect-record', effect_id=original['effect_id'], status='compensated', evidence='fake')
        self.assert_code('unverified_compensation', self.call, 'effect-record', effect_id=original['effect_id'], status='compensated', evidence='check', compensation_effect_id='missing')
        fix = self.work('effect-plan', run_id=run, tool='fixture-undo', target='local', idempotency_key='two', compensates_effect_id=original['effect_id'])
        self.work('effect-record', effect_id=fix['effect_id'], status='applied', external_reference='fixture:2')
        result = self.call('effect-record', effect_id=original['effect_id'], status='compensated', evidence='external state independently checked', compensation_effect_id=fix['effect_id'])
        self.assertFalse(result['executed'])

    def test_mcp_has_fixed_worker_authority(self):
        def request(op, args):
            return {'method': 'tools/call', 'params': {'name': 'memory_call', 'arguments': {'operation': op, 'arguments': args}}}
        for op in ('activate', 'restore', 'scope-release', 'policy-revoke'):
            with self.assertRaises(ValueError):
                dispatch(request(op, {}), self.worker, 'test')
        for args in ({'role': 'operator'}, {'scope': 'other'}, {'db': 'other.db'}):
            with self.assertRaises(ValueError):
                dispatch(request('run-start', args), self.worker, 'test')
        self.assertFalse(dispatch(request('run-start', {}), self.worker, 'test')['isError'])


if __name__ == '__main__':
    unittest.main()
