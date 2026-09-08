"""A goal-driven agent: model decisions -> real tools -> observations -> next decision."""
from __future__ import annotations

import json
from pathlib import Path
import time
import uuid

from .execution import (Journal, apply_patch, checked_project, clone, execute, git,
                        usage_sum, verify_checks, RESERVED, skill_catalog, validate_manifest, save_report)
from .routing import _canonical_path, _conflicts, _profiles
from .transport import run_codex


def decision_schema():
    text = {'type': 'string'}
    texts = {'type': 'array', 'items': text}
    properties = {'id': text, 'goal': text, 'kind': {'type': 'string', 'enum': ['architecture', 'implementation', 'review', 'research']},
                  'complexity': {'type': 'string', 'enum': ['simple', 'standard', 'complex']},
                  'risk': {'type': 'string', 'enum': ['low', 'high']},
                  'uncertainty': {'type': 'string', 'enum': ['low', 'high']},
                  'write_paths': texts, 'depends_on': texts, 'acceptance': texts, 'verify': texts}
    return {'type': 'object', 'additionalProperties': False,
            'properties': {'action': {'type': 'string', 'enum': ['dispatch', 'done', 'blocked']},
                           'reason': text, 'tasks': {'type': 'array', 'items': {'type': 'object',
                            'additionalProperties': False, 'properties': properties, 'required': list(properties)}}},
            'required': ['action', 'reason', 'tasks']}


def run_agent(spec, project: Path, tool_root: Path, state_root: Path, *, apply=False,
              kernel_model=None, kernel_effort=None, runner=run_codex, executor=execute):
    spec = json.loads(json.dumps(spec))
    project = checked_project(project)
    if not isinstance(spec, dict) or not isinstance(spec.get('goal'), str) or not spec['goal'].strip():
        raise ValueError('Agent requires a goal')
    if not isinstance(spec.get('acceptance'), list) or not spec['acceptance'] or not all(isinstance(x, str) and x.strip() for x in spec['acceptance']):
        raise ValueError('Agent requires fixed acceptance criteria')
    paths = spec.get('write_paths')
    if not isinstance(paths, list) or not paths or any(not isinstance(x, str) or _canonical_path(x, project) is None for x in paths):
        raise ValueError('Agent requires safe bounded write_paths')
    if _conflicts([p.casefold() for p in paths], RESERVED):
        raise ValueError('Agent write scope overlaps protected execution configuration')
    for key, default, ceiling in [('max_rounds', 3, 5), ('max_model_calls', 10, 30),
                                  ('max_seconds', 900, 3600), ('max_tokens', 100000, 1000000)]:
        value = spec.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= ceiling:
            raise ValueError(f'{key} must be in 1..{ceiling}')
        spec[key] = value
    if not spec.get('checks') or not spec.get('integration_checks'):
        raise ValueError('Agent requires trusted checks and integration_checks')
    # Validate all executable acceptance input before the first model call.
    validation_task = {'id': 'scope-check', 'goal': spec['goal'], 'kind': 'implementation',
                       'complexity': 'standard', 'risk': 'low', 'uncertainty': 'low',
                       'write_paths': paths, 'depends_on': [], 'acceptance': spec['acceptance'],
                       'verify': spec['integration_checks']}
    validate_manifest({**spec, 'tasks': [validation_task]}, project, tool_root)
    if bool(kernel_model) != bool(kernel_effort):
        raise ValueError('Kernel override requires model and effort together')
    profile = _profiles(tool_root)['kernel']
    model, effort = kernel_model or profile['model'], kernel_effort or profile['effort']
    directory = Path(state_root).resolve() / ('agent-' + uuid.uuid4().hex)
    journal = Journal(directory)
    (directory / 'goal.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    base = git(project, 'rev-parse', 'HEAD').decode().strip()
    workspace = directory / 'workspace'
    started = time.monotonic()
    deadline = started + spec['max_seconds']
    report = {'agent_id': directory.name, 'status': 'running', 'goal': spec['goal'], 'base_commit': base,
              'kernel_model': model, 'kernel_effort': effort, 'decisions': [], 'executions': [],
              'checks': [], 'model_calls': 0, 'usage': None, 'interventions': [], 'applied': False,
              'run_directory': str(directory)}
    save_report(directory, report)
    inferences = []
    execution_usage = []
    observations = []
    memory_epoch = None

    def stopped():
        nonlocal memory_epoch
        if (directory / 'STOP').exists() or time.monotonic() >= deadline:
            return True
        if spec.get('memory'):
            from .memory import MemoryStore
            mem = spec['memory']
            store = MemoryStore(mem['db'], scope=mem['scope'], role='operator')
            try:
                state = store.execute('status', {'scope': mem['scope']})['data']
            finally:
                store.close()
            if memory_epoch is None:
                memory_epoch = state['epoch']
            return state['frozen'] or memory_epoch != state['epoch']
        return False

    def budget():
        all_usage = [*inferences, *execution_usage]
        calls = sum(x.get('model_calls', 0) for x in all_usage)
        usage = usage_sum(all_usage)
        remaining = spec['max_tokens'] - (usage.get('total_tokens', 0) if usage else 0)
        if stopped() or calls >= spec['max_model_calls'] or remaining <= 0 or (calls and usage is None):
            raise ValueError('Agent stopped by memory, cancellation, time, call or usage budget')
        return spec['max_model_calls'] - calls, remaining

    try:
        if stopped():
            raise ValueError('Agent cannot start with a frozen memory scope')
        clone(project, workspace, base)
        for round_index in range(spec['max_rounds']):
            calls_left, tokens_left = budget()
            prompt = ('You are the development kernel. Inspect this local repository using read-only tools. '
                      'Choose the next action from evidence. dispatch creates concrete worker tasks; done is permitted '
                      'only when the authorized goal is implemented; blocked gives a concrete missing requirement. '
                      'Task verify fields must reference the supplied check IDs. Keep tasks within authorized write_paths. '
                      'Acceptance criteria and verification commands are fixed. Do not execute writes or delegate tools yourself. '
                      'Use simple/standard/complex honestly: workers route Luna/Terra/Sol and architecture routes Astra. '
                      'Use relevant engineering skills on demand. Return only the structured decision.\n'
                      + json.dumps({'goal': spec['goal'], 'acceptance': spec['acceptance'], 'write_paths': paths,
                                    'checks': [{'id': x['id']} for x in spec['checks']],
                                    'skills': skill_catalog(tool_root),
                                    'observations': observations}, ensure_ascii=False))
            inference = runner(workspace, prompt, model, effort, read_only=True, schema=decision_schema(),
                               seconds=max(1, deadline - time.monotonic()), max_tokens=tokens_left,
                               cancelled=stopped, event=lambda **row: journal.event(round=round_index + 1, **row))
            inferences.append(inference)
            if inference['status'] != 'completed':
                raise ValueError(inference.get('error', 'Kernel inference failed'))
            observed_usage = usage_sum([*inferences, *execution_usage])
            if observed_usage is None or observed_usage['total_tokens'] > spec['max_tokens'] or stopped():
                raise ValueError('Kernel stopped: result is outside the observed budget or memory epoch')
            decision = json.loads(inference['output'])
            if not isinstance(decision, dict) or decision.get('action') not in {'dispatch', 'done', 'blocked'}:
                raise ValueError('Kernel returned an invalid action')
            report['decisions'].append(decision)
            journal.event(kind='decision', round=round_index + 1, decision=decision)
            if decision['action'] == 'blocked':
                raise ValueError(decision.get('reason', 'Kernel needs operator input'))
            if decision['action'] == 'done':
                report['checks'] = verify_checks(spec['checks'], spec['integration_checks'], workspace, journal.event, deadline)
                if all(x['passed'] for x in report['checks']):
                    if git(workspace, 'status', '--porcelain', '--untracked-files=all').strip() or stopped():
                        raise ValueError('Verification changed the result or execution was stopped')
                    patch = git(workspace, 'diff', '--binary', base, 'HEAD')
                    output = directory / 'result.patch'
                    output.write_bytes(patch)
                    report.update(status='completed', patch=str(output))
                    if apply:
                        checked_project(project)
                        if git(project, 'rev-parse', 'HEAD').decode().strip() != base or stopped():
                            raise ValueError('Target or memory changed before apply')
                        apply_patch(project, patch)
                        report['applied'] = True
                    break
                observations.append({'action': 'done_rejected', 'checks': report['checks']})
                continue
            tasks = decision.get('tasks')
            if not isinstance(tasks, list) or not tasks or len(tasks) > 6:
                raise ValueError('Kernel dispatch must contain 1..6 tasks')
            for task in tasks:
                if not isinstance(task, dict) or not isinstance(task.get('write_paths'), list):
                    raise ValueError('Kernel returned an invalid task')
                for path in task['write_paths']:
                    canonical = _canonical_path(path, workspace) if isinstance(path, str) else None
                    if canonical is None or not any(canonical == _canonical_path(p, workspace) or canonical.startswith(_canonical_path(p, workspace) + '/') for p in paths):
                        raise ValueError('Kernel attempted to expand the authorized write scope')
            calls_left, tokens_left = budget()
            manifest = {key: spec[key] for key in ['checks', 'integration_checks', 'protected_paths', 'memory'] if key in spec}
            manifest.update(tasks=tasks, max_attempts=3, max_model_calls=calls_left,
                            max_tokens=tokens_left, max_seconds=max(1, int(deadline - time.monotonic())),
                            max_parallel=spec.get('max_parallel', 3), stop_file=str(directory / 'STOP'))
            outcome = executor(manifest, workspace, tool_root, directory / 'executions', apply=True, runner=runner)
            report['executions'].append(outcome)
            execution_usage.append({'model_calls': outcome['model_calls'], 'usage': outcome['usage']})
            observations.append({'status': outcome['status'], 'checks': outcome['checks'],
                                 'tasks': [{'id': t['id'], 'status': t['status'], 'error': t.get('error'),
                                            'checks': t.get('checks', [])} for t in outcome['tasks']],
                                 'error': outcome.get('error')})
            if outcome['status'] != 'completed':
                # A new kernel decision can refine a failed plan, but does not replay an uncertain tool call.
                if any(t.get('error') for t in outcome['tasks']) or not outcome['tasks']:
                    raise ValueError(outcome.get('error', 'Execution blocked'))
                continue
            git(workspace, 'add', '--all')
            git(workspace, 'commit', '--quiet', '--allow-empty', '-m', 'Accepted agent execution ' + str(round_index + 1))
        else:
            raise ValueError('Agent exhausted its decision rounds without verified completion')
    except (ValueError, OSError) as error:
        report.update(status='blocked', error=str(error)[:3000])
    finally:
        report.update(duration_seconds=round(time.monotonic() - started, 3),
                      model_calls=sum(x.get('model_calls', 0) for x in [*inferences, *execution_usage]),
                      usage=usage_sum([*inferences, *execution_usage]),
                      interventions=[x for i in inferences for x in i.get('interventions', [])]
                                   + [x for e in report['executions'] for x in e.get('interventions', [])])
        report['kernel_inferences'] = inferences
        journal.event(kind='agent_finished', status=report['status'], error=report.get('error'))
        report['journal_sha256'] = journal.previous
        save_report(directory, report)
    return report
