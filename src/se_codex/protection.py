"""Exercise the installed Codex sandbox using disposable, non-sensitive canaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .runtime import codex_command


def _toml(value):
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(key) + ' = ' + _toml(item) for key, item in value.items()) + '}'
    return json.dumps(value, ensure_ascii=False)


def memory_config(project: Path, tool_root: Path, database: Path, scope: str) -> str:
    """Render a reviewable project configuration fragment, without applying it."""
    project, tool_root, database = project.resolve(), tool_root.resolve(), database.absolute()
    if not scope.strip() or '\x00' in scope:
        raise ValueError('scope must be non-empty')
    if not project.is_dir() or not database.parent.is_dir():
        raise ValueError('Project and database parent directories must exist')
    from .install import _assert_no_reparse_ancestors
    _assert_no_reparse_ancestors(database)
    database = database.resolve()
    if project == database.parent or project.is_relative_to(database.parent):
        raise ValueError('Use a dedicated state directory, not the workspace or its ancestor')
    rules = {
        ':root': 'deny', ':minimal': 'read',
        str(Path(sys.prefix)): 'read', str(Path(sys.base_prefix)): 'read',
        str(database.parent): 'deny',
        ':workspace_roots': {'.': 'write', '.codex': 'read', '.agents': 'read',
                             'AGENTS.md': 'read', '.se-state': 'deny', '.se-codex': 'read'},
    }
    if tool_root == project:
        rules[str(tool_root / 'src/se_codex')] = 'read'
        rules[str(tool_root / 'se.py')] = 'read'
    else:
        rules[str(tool_root)] = 'read'
    server = {'command': sys.executable,
              'args': ['-I', str(tool_root / 'se.py'), 'mcp', '--db', str(database), '--scope', scope],
              'enabled': True, 'required': True}
    return '\n'.join([
        '# Generated fragment. Review and merge into the project config, then start a NEW task.',
        '# Remove legacy sandbox_mode/sandbox_workspace_write from every loaded config first.',
        '# Other MCP servers/connectors have independent permissions; audit them separately.',
        'default_permissions = "se-worker"', 'approval_policy = "never"', '',
        '[permissions.se-worker]', 'extends = ":workspace"', 'filesystem = ' + _toml(rules),
        'network = { enabled = false }', '', '[mcp_servers.se_memory]',
        *[key + ' = ' + _toml(value) for key, value in server.items()], '',
    ])


def probe_sandbox() -> dict:
    """No model invocation, credential reads, global configuration, or ACL changes."""
    if os.name != 'nt':
        return {'verified': False, 'reason': 'This probe currently targets native Windows Codex sandbox.', 'mode': 'governance'}
    with tempfile.TemporaryDirectory(prefix='se-probe-') as directory:
        base = Path(directory)
        workspace = base / 'workspace'
        workspace.mkdir()
        vault = base / 'vault'
        vault.mkdir()
        secret = vault / 'canary.txt'
        secret.write_text('disposable-canary', encoding='utf-8')
        config = workspace / '.codex'
        config.mkdir()
        guard = config / 'guard.txt'
        guard.write_text('configuration-canary', encoding='utf-8')
        rules = {
            'extends': ':workspace',
            'filesystem': {
                ':root': 'deny', ':minimal': 'read',
                str(Path(sys.prefix)): 'read', str(Path(sys.base_prefix)): 'read',
                str(vault): 'deny',
                ':workspace_roots': {'.': 'write', '.codex': 'read', '.agents': 'read', '.se-state': 'deny', '.se-codex': 'read'},
            },
            'network': {'enabled': False},
        }
        # Values travel as argv/TOML and Python argv; no shell interpolation.
        code = """import json,sys
from pathlib import Path
workspace,secret,guard=map(Path,sys.argv[1:])
results={}
for name,action in [
 ('workspace_write',lambda:(workspace/'output.txt').write_text('ok')),
 ('vault_read',lambda:secret.read_text()),
 ('vault_write',lambda:secret.write_text('changed')),
 ('config_write',lambda:guard.write_text('changed'))]:
 try: action(); results[name]='allowed'
 except OSError: results[name]='denied'
print(json.dumps(results))
"""
        argv = [*codex_command(), 'sandbox', '-P', 'se-probe', '-C', str(workspace),
                '-c', 'permissions.se-probe=' + _toml(rules),
                sys.executable, '-I', '-c', code, str(workspace), str(secret), str(guard)]
        try:
            process = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                     timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as error:
            return {'verified': False, 'mode': 'governance', 'reason': type(error).__name__}
        result = None
        for line in process.stdout.splitlines():
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict) and 'workspace_write' in parsed:
                    result = parsed
            except ValueError:
                continue
        expected = {'workspace_write': 'allowed', 'vault_read': 'denied', 'vault_write': 'denied', 'config_write': 'denied'}
        canaries_unchanged = secret.read_text(encoding='utf-8') == 'disposable-canary' and guard.read_text(encoding='utf-8') == 'configuration-canary'
        verified = process.returncode == 0 and result == expected and canaries_unchanged
        return {'verified': verified, 'mode': 'probe_only' if verified else 'governance',
                'checks': result, 'exit_code': process.returncode,
                'canaries_unchanged': canaries_unchanged,
                'reason': 'Disposable sandbox probe passed; a real worker session still needs its own verified permissions and MCP boundary.' if verified else 'Protected execution was not verified; no permission fallback was attempted.',
                'diagnostic': process.stderr[-2000:].replace(str(base), '<probe>')}
