"""Command line surface; JSON files/stdin keep content out of shell interpolation."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAX_JSON_BYTES = 10 * 1024 * 1024


def read_json(path: str):
    if path == "-":
        raw = sys.stdin.buffer.read(MAX_JSON_BYTES + 1)
    else:
        with open(path, "rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("JSON input exceeds 10 MiB")
    return json.loads(raw.decode("utf-8-sig"))


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser():
    result = argparse.ArgumentParser(description="Codex model routing and auditable project memory")
    commands = result.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Check project configuration; --live reads host capabilities")
    doctor.add_argument("--live", action="store_true")
    doctor.add_argument("--project", type=Path, default=ROOT)
    plan = commands.add_parser("plan", help="Plan model assignments and non-conflicting waves; does not run models")
    plan.add_argument("--json", required=True, help="Task list JSON file, or - for stdin")
    plan.add_argument("--project", type=Path, default=ROOT)
    plan.add_argument("--max-parallel", type=int, default=3)
    memory = commands.add_parser("memory", help="Operator CLI for local memory governance")
    memory.add_argument("operation")
    memory.add_argument("--db", required=True)
    memory.add_argument("--scope", required=True)
    memory.add_argument("--json", default="-", help="Arguments JSON file, or - for stdin")
    mcp = commands.add_parser("mcp", help="Start stdio MCP with fixed worker authority")
    mcp.add_argument("--db", required=True)
    mcp.add_argument("--scope", required=True)
    commands.add_parser("hook", help="Read a Codex lifecycle event from stdin")
    install = commands.add_parser("install", help="Preview a project-local install; never overwrite conflicting files")
    install.add_argument("--target", type=Path, required=True)
    install.add_argument("--apply", action="store_true")
    commands.add_parser("demo", help="Run the pollution/recovery scenario in a temporary database")
    commands.add_parser('test', help='Run the repository regression suite without model calls')
    commands.add_parser('safety-probe', help='Probe native Windows sandbox using disposable canaries; no model calls')
    configuration = commands.add_parser('memory-config', help='Print a protected memory/MCP config fragment; does not apply it')
    configuration.add_argument('--project', type=Path, default=ROOT)
    configuration.add_argument('--db', type=Path, required=True)
    configuration.add_argument('--scope', required=True)
    for name, help_text in [('run', 'Execute an authorized task manifest through real Codex workers'),
                            ('agent', 'Run a model-driven goal agent with fixed acceptance checks')]:
        action = commands.add_parser(name, help=help_text)
        action.add_argument('--json', required=True)
        action.add_argument('--project', type=Path, required=True)
        action.add_argument('--state', type=Path, default=ROOT / '.se-state')
        action.add_argument('--apply', action='store_true', help='Apply verified patch to the unchanged target worktree')
        if name == 'agent':
            action.add_argument('--kernel-model')
            action.add_argument('--kernel-effort')
    bench = commands.add_parser('benchmark', help='Run bounded real model trials on isolated local Git clones')
    bench.add_argument('--json', type=Path, required=True)
    bench.add_argument('--state', type=Path, default=ROOT / '.se-eval')
    report = commands.add_parser('report', help='Read a run report without replaying model calls')
    report.add_argument('--run', type=Path, required=True)
    stop = commands.add_parser('stop', help='Request an orderly stop at the next execution boundary')
    stop.add_argument('--run', type=Path, required=True)
    return result


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8-sig')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "doctor":
            from .runtime import inspect_project
            data = inspect_project(arguments.project, arguments.live)
            ready = not arguments.live or data.get('live_verified', False)
            emit({"ok": ready, "data": data})
            return 0 if ready else 3
        elif arguments.command == "plan":
            from .routing import plan_tasks
            emit({"ok": True, "data": plan_tasks(read_json(arguments.json), arguments.project, arguments.max_parallel)})
        elif arguments.command in {'run', 'agent'}:
            if arguments.command == 'run':
                from .execution import execute
                data = execute(read_json(arguments.json), arguments.project, ROOT, arguments.state, apply=arguments.apply)
            else:
                from .agent import run_agent
                data = run_agent(read_json(arguments.json), arguments.project, ROOT, arguments.state,
                                 apply=arguments.apply, kernel_model=arguments.kernel_model,
                                 kernel_effort=arguments.kernel_effort)
            emit({'ok': data['status'] == 'completed', 'data': data})
            return 0 if data['status'] == 'completed' else 3
        elif arguments.command == 'benchmark':
            from .benchmark import run_benchmark
            data = run_benchmark(arguments.json, arguments.state, tool_root=ROOT)
            emit({'ok': True, 'data': data})
        elif arguments.command in {'report', 'stop'}:
            from .execution import read_report
            data = read_report(arguments.run)
            if arguments.command == 'stop':
                if data.get('status') != 'running':
                    raise ValueError('Run is already terminal; no operation was replayed')
                (arguments.run / 'STOP').write_text('Operator requested stop\n', encoding='utf-8')
                emit({'ok': True, 'data': {'stop_requested': True}})
            else:
                emit({'ok': True, 'data': data})
        elif arguments.command == "memory":
            from .memory import MemoryStore
            body = read_json(arguments.json)
            if not isinstance(body, dict):
                raise ValueError("Memory arguments must be a JSON object")
            if body.get("scope", arguments.scope) != arguments.scope:
                raise ValueError("Scope does not match --scope")
            store = MemoryStore(arguments.db, role="operator", scope=arguments.scope)
            try:
                emit(store.execute(arguments.operation, {**body, "scope": arguments.scope}))
            finally:
                store.close()
        elif arguments.command == "mcp":
            from .mcp import serve
            serve(arguments.db, arguments.scope, sys.stdin, sys.stdout)
        elif arguments.command == "hook":
            from .hooks import evaluate
            emit(evaluate(read_json("-"), ROOT))
        elif arguments.command == "install":
            from .install import install_project
            report = install_project(ROOT, arguments.target, arguments.apply)
            emit({"ok": not bool(report.get("conflicts")), "data": report})
            return 3 if report.get("conflicts") else 0
        elif arguments.command == "demo":
            from .demo import run_demo
            emit({"ok": True, "data": run_demo()})
        elif arguments.command == 'test':
            import unittest
            if not (ROOT / 'tests').is_dir():
                raise ValueError('Run the regression suite in the source repository')
            suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            return 0 if result.wasSuccessful() else 1
        elif arguments.command == 'safety-probe':
            from .protection import probe_sandbox
            data = probe_sandbox()
            emit({'ok': data['verified'], 'data': data})
            return 0 if data['verified'] else 3
        elif arguments.command == 'memory-config':
            from .protection import memory_config
            print(memory_config(arguments.project, ROOT, arguments.db, arguments.scope))
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        code = getattr(error, "code", "invalid_input" if isinstance(error, ValueError) else "storage_error")
        # Hook parse failures must deny supported tool events, not silently pass.
        if arguments.command == "hook":
            print("Serendipity hook could not validate this event", file=sys.stderr)
            return 2
        message = str(error) if isinstance(error, ValueError) else "Local operation failed; check input paths and storage availability"
        emit({"ok": False, "error": {"code": code, "message": message}})
        return 2 if code == "invalid_input" else (4 if code == "storage_error" else 3)


if __name__ == "__main__":
    raise SystemExit(main())
