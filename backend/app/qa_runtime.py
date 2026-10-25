"""Start an offline workbench with its own disposable, retained QA workspace.

This entry point must never import ``config`` or ``main``: the data paths are fully injected, independently of the user's .env and process
environment. Only the checked-in fictional Markdown examples seed new runs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from .application import create_app
from .db import Database
from .vault import scan_vault


_WORKSPACE_NAME = re.compile(r'qa-[a-z0-9][a-z0-9-]{0,63}\Z')
_MARKER = '.qa-workspace.json'
_REPORT = 'qa-report.json'
_HOST = '127.0.0.1'


class QAWorkspaceError(ValueError):
    """A controlled error that never includes private paths or contents."""


def _inspect(path: Path) -> os.stat_result | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise QAWorkspaceError('QA paths cannot contain symbolic links or reparse points.')
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise QAWorkspaceError('QA paths must contain only ordinary directories and files.')
    if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
        raise QAWorkspaceError('QA files cannot share hard links with another workspace.')
    return info


def _check_parents(path: Path) -> None:
    # Inspect before resolve(): resolving first would erase evidence of a junction.
    for parent in reversed((path, *path.parents)):
        _inspect(parent)


def _check_tree(folder: Path) -> None:
    info = _inspect(folder)
    if info is None or not stat.S_ISDIR(info.st_mode):
        raise QAWorkspaceError('The QA workspace or example directory is unavailable.')
    for child in folder.iterdir():
        child_info = _inspect(child)
        if child_info is None:
            raise QAWorkspaceError('QA files changed during validation; start a fresh run.')
        if stat.S_ISDIR(child_info.st_mode):
            _check_tree(child)


def _timestamp() -> str:
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')


def _write_json(path: Path, value: dict) -> None:
    _check_parents(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _validate_marker(workspace: Path) -> None:
    marker_path = workspace / _MARKER
    try:
        if marker_path.stat().st_size > 4096:
            raise QAWorkspaceError('The QA workspace marker is invalid.')
        marker = json.loads(marker_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError):
        raise QAWorkspaceError('The QA workspace marker is missing or invalid.') from None
    if not isinstance(marker, dict) or (
        marker.get('schema') != 1 or marker.get('environment') != 'qa'
        or marker.get('workspace') != workspace.name
    ):
        raise QAWorkspaceError('Only a workspace created by the QA launcher can be reused.')


def create_qa_app(*, project_root: Path | None = None,
                  workspace: str | None = None) -> tuple[FastAPI, Path]:
    """Create an isolated application; ``project_root`` is a test seam, not a CLI option.

    Reusing a named workspace preserves all its local edits and decisions. New
    workspaces start with candidate memories; nothing is automatically confirmed.
    """
    if workspace is not None and not _WORKSPACE_NAME.fullmatch(workspace):
        raise QAWorkspaceError('Workspace names must match qa-[a-z0-9][a-z0-9-]{0,63}.')
    root = Path(os.path.abspath(project_root or Path(__file__).parents[2]))
    _check_parents(root)
    if not root.is_dir():
        raise QAWorkspaceError('The project directory is unavailable.')
    parent = root / '.test-data'
    _check_parents(parent)
    if parent.exists() and not parent.is_dir():
        raise QAWorkspaceError('The QA workspace parent must be a directory.')
    destination = parent / (workspace or f'qa-{uuid4().hex}')
    _check_parents(destination)

    if workspace is not None:
        _check_tree(destination)
        _validate_marker(destination)
    else:
        examples = root / 'examples' / 'vault'
        _check_parents(examples)
        _check_tree(examples)
        markdown = sorted(path for path in examples.rglob('*')
                          if path.is_file() and path.suffix.lower() == '.md')
        if not markdown:
            raise QAWorkspaceError('No fictional Markdown examples are available.')
        parent.mkdir(exist_ok=True)
        destination.mkdir(exist_ok=False)
        vault = destination / 'vault'
        vault.mkdir()
        for source in markdown:
            _check_parents(source)
            target = vault / source.relative_to(examples)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Copy contents only, never follow a source link or preserve a hard link.
            with target.open('xb') as output:
                output.write(source.read_bytes())
        _write_json(destination / _MARKER, {
            'schema': 1, 'environment': 'qa', 'workspace': destination.name,
            'created_at': _timestamp(), 'seed': 'examples/vault Markdown only',
        })

    _check_tree(destination)
    database = Database(destination / 'torch-os.sqlite3')
    vault = destination / 'vault'
    app = create_app(database=database, vault_dir=vault,
                     host=_HOST, security_environ={}, runtime_label='qa',
                     enable_dsh=False)
    scan_vault(vault, database)
    app.state.qa_workspace = destination.name
    return app, destination


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('Port must be an integer from 1 to 65535.') from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError('Port must be an integer from 1 to 65535.')
    return port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run 薪火OS in an isolated, offline QA workspace.')
    parser.add_argument('--port', type=_port, default=8766)
    parser.add_argument('--workspace', help='Reuse an existing qa-* name inside .test-data; never a path.')
    parser.add_argument('--check', action='store_true', help='Validate and retain a QA workspace without starting HTTP.')
    args = parser.parse_args(argv)
    try:
        app, workspace = create_qa_app(workspace=args.workspace)
        report = {
            'environment': 'qa', 'workspace': workspace.name, 'mode': 'offline',
            'harness_enabled': False, 'url': f'http://{_HOST}:{args.port}',
            'status': 'validated' if args.check else 'startup_requested',
            'updated_at': _timestamp(), 'retained_after_exit': True,
        }
        _write_json(workspace / _REPORT, report)
    except (QAWorkspaceError, OSError):
        # Do not echo OS error paths, .env values, or fixture contents.
        print('QA startup refused: workspace validation failed. Use a new QA workspace.')
        return 2
    print(json.dumps(report, ensure_ascii=False))
    if args.check:
        return 0
    import uvicorn

    try:
        # Passing the instance avoids import-string reloaders loading main/config.
        uvicorn.run(app, host=_HOST, port=args.port, log_level='warning', access_log=False)
    finally:
        report['status'] = 'exited'
        report['updated_at'] = _timestamp()
        try:
            _write_json(workspace / _REPORT, report)
        except (QAWorkspaceError, OSError):
            print('QA stopped; the retained report could not be updated safely.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
