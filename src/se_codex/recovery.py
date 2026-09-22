"""Operator containment and local evidence capture; never replay an agent or effect."""
from __future__ import annotations

import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import uuid

from .execution import read_report
from .install import _assert_no_reparse_ancestors
from .memory import MemoryStore


def _read(path, limit=32 * 1024 * 1024):
    _assert_no_reparse_ancestors(path.absolute())
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Evidence file exceeds the 32 MiB capture limit')
    return data


def _json(path):
    return json.loads(_read(path).decode('utf-8-sig'))


def _capture_run(run, database, scope, destination):
    """Signal only an explicitly named run with a matching persisted memory binding."""
    run = Path(run).absolute()
    _assert_no_reparse_ancestors(run)
    run = run.resolve(strict=True)
    contract = run / ('goal.json' if (run / 'goal.json').is_file() else 'manifest.json')
    spec = _json(contract)
    memory = spec.get('memory') if isinstance(spec, dict) else None
    if not isinstance(memory, dict) or memory.get('scope') != scope:
        raise ValueError('Run does not declare the affected memory scope')
    declared = memory.get('db')
    if not isinstance(declared, str) or not Path(declared).is_absolute():
        raise ValueError('Run has no absolute memory binding; inspect legacy runs manually')
    if Path(declared).resolve() != database:
        raise ValueError('Run belongs to another memory database')
    # Capture failures must not prevent the stop request. Do not follow a planted STOP link.
    stop = run / 'STOP'
    _assert_no_reparse_ancestors(stop)
    try:
        with stop.open('x', encoding='utf-8') as stream:
            stream.write('Operator memory containment; use a fresh reviewed task.\n')
    except FileExistsError:
        if not stop.is_file():
            raise ValueError('STOP is not a regular file')
    destination.mkdir()
    result = {'run_directory': str(run), 'stop_requested': True,
              'termination_confirmed': False, 'files': {}, 'errors': []}
    for name in [contract.name, 'report.json', 'events.jsonl', 'result.patch']:
        source = run / name
        if not source.exists():
            continue
        try:
            data = _read(source)
            (destination / name).write_bytes(data)
            result['files'][name] = hashlib.sha256(data).hexdigest()
        except (ValueError, OSError) as error:
            result['errors'].append(f'{name}: {type(error).__name__}; evidence capture failed')
    try:
        report = read_report(destination)
        result.update(status_at_capture=report['status'], journal_integrity=report['journal_integrity'],
                      applied=report.get('applied', False))
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        result['errors'].append('Captured report/journal unavailable or inconsistent; inspect original run')
    return result


def contain_memory(database, scope, request, state_root, runs=()):
    """Quarantine a revision, invalidate epochs, stop named runs, and back up the whole store.

    Partial evidence failures leave containment in place and are returned explicitly.
    This operator utility does not restore a backup, release incidents, or invoke models.
    """
    if not isinstance(request, dict) or set(request) - {'memory_id', 'revision', 'reason', 'incident_id'}:
        raise ValueError('Recovery request accepts memory_id, revision, reason, and optional incident_id')
    if len(runs) > 16:
        raise ValueError('Recovery accepts at most 16 explicit run directories')
    database = Path(database).absolute()
    state_root = Path(state_root).absolute()
    _assert_no_reparse_ancestors(database)
    _assert_no_reparse_ancestors(state_root)
    database = database.resolve(strict=True)
    # Refuse a typo rather than letting MemoryStore initialize a new scope/database.
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        if not connection.execute('SELECT 1 FROM scopes WHERE scope = ?', (scope,)).fetchone():
            raise ValueError('Recovery requires an existing memory scope')
    directory = state_root / ('recovery-' + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    (directory / '.gitignore').write_text('*\n', encoding='utf-8')
    report = {'status': 'preparing', 'scope': scope, 'database': str(database),
              'directory': str(directory), 'runs': [], 'errors': [],
              'context_reset': False, 'effects_replayed': False, 'scope_released': False}

    def save():
        (directory / 'recovery.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    with MemoryStore(str(database), scope=scope) as operator:
        incident = operator.execute('quarantine', {'scope': scope, **request})['data']
        report.update(status='contained', incident=incident)
        save()
        for index, run in enumerate(dict.fromkeys(str(Path(p).absolute()) for p in runs)):
            try:
                captured = _capture_run(run, database, scope, directory / f'run-{index + 1}')
                report['runs'].append(captured)
                report['errors'].extend(captured['errors'])
            except (ValueError, OSError, sqlite3.Error) as error:
                report['errors'].append(f'Run {index + 1}: {error}; inspect and stop manually')
            save()
        backup = directory / 'memory-backup.sqlite'
        try:
            # SQLite backup includes committed WAL pages; copying just the main file does not.
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(backup)) as target:
                    source.backup(target)
                    if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('Backup integrity check failed')
                    frozen, epoch = target.execute('SELECT frozen, epoch FROM scopes WHERE scope = ?', (scope,)).fetchone()
                    sequence = target.execute('SELECT COALESCE(MAX(seq), 0) FROM safety_events WHERE scope = ?', (scope,)).fetchone()[0]
            with backup.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            report['backup'] = {'path': str(backup), 'sha256': digest, 'integrity_check': 'ok',
                                'scope_frozen': bool(frozen), 'epoch': epoch, 'event_sequence': sequence}
        except (ValueError, OSError, sqlite3.Error) as error:
            report['backup_failure'] = {'path': str(backup), 'usable': False}
            report['errors'].append(f'Consistent backup failed: {type(error).__name__}; scope remains contained')
        report['incident'] = operator.execute('incident-report', {'scope': scope, 'incident_id': incident['incident_id']})['data']
    report['status'] = 'contained_with_errors' if report['errors'] else 'contained'
    if not report['incident']['scope_frozen']:
        report['status'] = 'containment_lost'
        report['errors'].append('Scope was released during evidence capture; review concurrent operator activity')
    report['unknown'] = 'Only explicitly supplied runs were signalled; unbound tools and older contexts require manual inspection.'
    (directory / 'FRESH_START.md').write_text(
        '# Recovery handoff — operator review required\n\n'
        '1. Confirm native tasks stopped; a STOP file is a request, not termination evidence.\n'
        '2. Review recovery.json and the retained run copies as untrusted evidence.\n'
        '3. Compare applied artifacts against the recorded Git base; repair on a fresh branch.\n'
        '4. Reconcile uncertain external effects against the external system; never blindly replay.\n'
        '5. Review a memory restore-plan if needed; preserve revocations and safety events.\n'
        '6. Run independent acceptance checks, then release each reviewed incident.\n'
        '7. Start a NEW task with the user goal, reviewed code and valid memory. Do not fork the contaminated context.\n\n'
        'This bundle is evidence, not a clean model context or an automatically approved restore point.\n'
        + ('The verified SQLite backup contains the whole store and must stay in operator-controlled storage.\n'
           if 'backup' in report else
           'BACKUP FAILED: no verified backup is available. Any memory-backup.sqlite left here is unusable; create a new consistent backup from the source store.\n'),
        encoding='utf-8')
    save()
    return report
