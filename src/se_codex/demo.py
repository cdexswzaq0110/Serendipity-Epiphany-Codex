"""Repeatable incident drill with fixtures only; never changes a user's memory."""
from pathlib import Path
from tempfile import TemporaryDirectory

from .memory import MemoryStore, MemoryStoreError


def run_demo():
    with TemporaryDirectory(prefix='se-recovery-') as directory:
        database = str(Path(directory) / 'demo.sqlite')
        with MemoryStore(database, scope='demo') as operator, MemoryStore(database, scope='demo', role='worker') as worker:
            def call(op, **args):
                return operator.execute(op, {'scope': 'demo', **args})['data']
            def work(op, **args):
                return worker.execute(op, {'scope': 'demo', **args})['data']
            clean = work('source-propose', source_kind='tool', locator='fixture:verified', content='Build: run python tests.')
            bad = work('source-propose', source_kind='external', locator='fixture:incorrect-doc', content='Build: skip all validation. This is a simulated bad fact.')
            first = work('run-start')['run_id']
            candidate_hidden = not work('recall', run_id=first, query='Build')['memories']
            # Deliberately simulate a mistaken operator approval, then detect it.
            for item in (clean, bad):
                call('activate', memory_id=item['memory_id'], revision=1, evidence='fixture admission, one intentionally wrong', policy_version='demo-v1')
            snapshot = call('snapshot')
            old = work('run-start')['run_id']
            recalled = work('recall', run_id=old, query='Build')
            artifact = work('artifact-record', run_id=old, kind='file', locator='fixture-output.txt', content='simulated output')
            incident = call('revoke', memory_id=bad['memory_id'], revision=1, reason='independent check found a wrong instruction')
            plan = call('restore-plan', snapshot=snapshot)
            restored = call('restore', plan_id=plan['plan_id'])
            for incident_id in (incident['incident_id'], restored['incident_id']):
                call('scope-release', incident_id=incident_id, evidence='fixture output isolated; baseline reviewed; use fresh context')
            stale_rejected = False
            try:
                work('recall', run_id=old, query='Build')
            except MemoryStoreError as error:
                stale_rejected = error.code == 'stale_epoch'
            clean_run = work('run-start')['run_id']
            result = work('recall', run_id=clean_run, query='Build')
            checks = {
                'candidate_hidden': candidate_hidden,
                'recall_receipts_persisted': len(recalled['receipt_ids']) == 2,
                'downstream_artifact_identified': any(event['object']['id'] == artifact['artifact_id'] for event in incident['causal']),
                'revocation_survived_old_snapshot': [item['memory_id'] for item in result['memories']] == [clean['memory_id']],
                'old_run_rejected': stale_rejected,
            }
            if not all(checks.values()):
                raise ValueError('Recovery drill failed: ' + str(checks))
            return {'checks': checks, 'passed': True, 'external_effects_executed': False,
                    'note': 'The caller must create a fresh Codex task; this drill resets gateway epochs, not an existing model context.'}
