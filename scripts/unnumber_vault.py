"""Explicit offline folder migration. Preview by default; --apply or --rollback MANIFEST.

Stop the backend and finish editing notes first. Renames stay within this vault;
collisions are rejected. Edited text and SQLite have verified backups, binary
files are renamed without copying, and every move has a reversible journal.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.directory_names import NUMBERED_NAMES, OLD_ROOTS, ROOTS
from backend.app import vault_links
from scripts.align_vault_layout import safe, digest, db_changes, write_manifest

PREFIX = re.compile(r"^\d{1,2}_(?=\D)")
SKIP = {"backups", "cache", "__pycache__", "node_modules", ".git", ".claudian", ".workbuddy", "dsh"}


def entries(root: Path):
    """Business folders + live reference/config files, never historical backups."""
    starts = [*OLD_ROOTS, *ROOTS, ".torch-os", ".obsidian", "_Torch"]
    seen = set()
    for start in starts:
        base = safe(root, start)
        if not base.is_dir():
            continue
        for parent, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = [name for name in dirs if name not in SKIP]
            for name in ["", *dirs, *files]:
                path = Path(parent) / name
                rel = path.relative_to(root).as_posix()
                safe(root, rel)
                if rel not in seen:
                    seen.add(rel)
                    yield rel, path
    for path in root.glob("*.md"):
        yield path.name, safe(root, path.name)


def remap(value, mapping):
    for old in sorted(mapping, key=len, reverse=True):
        if value == old or value.startswith(old + "/"):
            return mapping[old] + value[len(old):]
    return value


def plan(root: Path):
    rows = list(entries(root))
    renames = []
    for rel, path in rows:
        # System directory names stay intact; notebook-trash contents must follow
        # current paths so whole-notebook restoration works after migration.
        business = rel.split('/')[0] in (*OLD_ROOTS, *ROOTS)
        trash = rel.startswith('.torch-os/本子回收站/')
        if path.is_dir() and (business or trash) and PREFIX.match(path.name):
            name = PREFIX.sub('', path.name)
            target = path.with_name(name).relative_to(root).as_posix()
            if not name or safe(root, target).exists():
                raise ValueError(f"同名目标已存在，未改动：{target}")
            renames.append({"source": rel, "target": target})
    renames.sort(key=lambda row: (-row['source'].count('/'), row['source']))
    mapping = {}
    directory_targets = set()
    for row in renames:
        parts = row['source'].split('/')
        # Every numbered business folder is removed, while date and file names stay.
        mapping[row['source']] = '/'.join(PREFIX.sub('', part) for part in parts)
        target_key = mapping[row['source']].casefold()
        if target_key in directory_targets: raise ValueError('去编号后文件夹重名，未改动')
        directory_targets.add(target_key)
    destinations = {}
    files = []
    for rel, path in rows:
        if not path.is_file(): continue
        target = remap(rel, mapping)
        folded = target.casefold()
        if folded in destinations and destinations[folded] != rel:
            raise ValueError(f"路径碰撞，未改动：{target}")
        destinations[folded] = rel
        files.append({"source": rel, "target": target, "bytes": path.stat().st_size})
    return {"schema": 1, "vault": str(root), "renames": renames, "mapping": mapping, "files": files}


def rewriter(mapping):
    names = dict(NUMBERED_NAMES)
    for old, new in mapping.items(): names[old.rsplit('/', 1)[-1]] = new.rsplit('/', 1)[-1]
    pattern = re.compile(r"(?<![\w])(" + '|'.join(map(re.escape, sorted(names, key=len, reverse=True))) + r")(?![\w.-])")
    def text(value):
        result = pattern.sub(lambda m: names[m[1]], value)
        for old, new in names.items():
            result = result.replace(quote(old, safe=''), quote(new, safe=''))
        return result
    def obj(value):
        if isinstance(value, str): return text(value)
        if isinstance(value, list): return [obj(v) for v in value]
        if isinstance(value, dict): return {text(k): obj(v) for k, v in value.items()}
        return value
    return text, obj


def apply(root: Path, database: Path | None = None):
    root = root.resolve(strict=True)
    data = plan(root)
    if not data['renames']: return None
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = safe(root, f'.torch-os/backups/directory-names/{stamp}')
    backup.mkdir(parents=True)
    manifest = backup / 'manifest.json'
    data.update(status='preparing', backup=str(backup.relative_to(root).as_posix()), edits=[],
                database=str(database) if database else '', db_changes=[])
    write_manifest(manifest, data)
    rewrite, rewrite_obj = rewriter(data['mapping'])
    file_mapping = {row['source']: row['target'] for row in data['files']}
    # Obsidian accepts unique suffix paths as well as full vault paths.
    aliases = {}
    for old, new in file_mapping.items():
        parts = old.split('/')
        for index in range(1, len(parts)):
            alias = '/'.join(parts[index:])
            aliases[alias] = new if alias not in aliases else None
    file_mapping.update({old: new for old, new in aliases.items() if new is not None and '/' in old})
    for row in data['files']:
        source = safe(root, row['source'])
        row['sha256'] = digest(source)
        if source.suffix.lower() not in {'.md', '.json', '.yaml', '.yml', '.canvas'}: continue
        if source.stat().st_size > 32 * 1024 * 1024: continue
        raw = source.read_bytes()
        try: text = raw.decode('utf-8-sig')
        except UnicodeDecodeError: continue
        updated = rewrite(text)
        if source.suffix.lower() == '.md':
            linked, _ = vault_links.rewrite_links(text, file_mapping, row['source'], row['target'])
            # Once links are resolved against actual files, do not reinterpret a
            # numbered note title as a folder (e.g. [[01_日记]]).
            links = []
            def hold(match):
                links.append(match[0])
                return f'\x00TORCH_LINK_{len(links)-1}\x00'
            protected = re.sub(r'!?\[\[[^\n]*?\]\]|!?\[[^\]\n]*\]\([^\n]*?\)', hold, linked)
            updated = rewrite(protected)
            for index, link in enumerate(links): updated = updated.replace(f'\x00TORCH_LINK_{index}\x00', link)
        if source.suffix in {'.json', '.canvas'}:
            try:
                old_obj = json.loads(text)
                new_obj = rewrite_obj(old_obj)
                updated = json.dumps(new_obj, ensure_ascii=False, indent=2) + '\n' if new_obj != old_obj else text
            except ValueError: pass
        if updated == text: continue
        new = (b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b'') + updated.encode('utf-8')
        saved, staged = backup / 'originals' / row['source'], backup / 'staged' / row['source']
        saved.parent.mkdir(parents=True, exist_ok=True)
        staged.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(raw)
        staged.write_bytes(new)
        if digest(saved) != row['sha256']: raise RuntimeError('备份校验失败，未执行改名')
        data['edits'].append({**row, 'after': hashlib.sha256(new).hexdigest()})
    # Full preflight after preparation: a concurrent edit must not be overwritten.
    for row in data['files']:
        if not row['source'].startswith('.obsidian/') and digest(safe(root, row['source'])) != row['sha256']:
            raise RuntimeError('文件在准备期间变化，停止迁移')
    connection = sqlite3.connect(database) if database and database.is_file() else None
    try:
        if connection:
            with closing(sqlite3.connect(backup / 'database.sqlite3')) as saved: connection.backup(saved)
            connection.execute('BEGIN IMMEDIATE')
            data['db_changes'] = db_changes(connection, data['mapping'])
        data['status'] = 'applying'
        write_manifest(manifest, data)
        for row in data['renames']:
            source, target = safe(root, row['source']), safe(root, row['target'])
            if target.exists(): raise RuntimeError('目标在预检后被占用，停止改名')
            source.rename(target)
        for row in data['edits']:
            target = safe(root, row['target'])
            if row['source'].startswith('.obsidian/') and digest(target) != row['sha256']:
                data.setdefault('skipped_live_settings', []).append(row['source'])
                continue
            if digest(target) != row['sha256']: raise RuntimeError('文件在改名后变化，停止更新引用')
            shutil.copy2(backup / 'staged' / row['source'], target)
        if connection:
            for row in data['db_changes']:
                connection.execute(f'UPDATE "{row["table"]}" SET "{row["column"]}"=? WHERE rowid=?', (row['after'], row['rowid']))
            connection.commit()
        edits = {row['source']: row for row in data['edits']}
        for row in data['files']:
            if row['source'].startswith('.obsidian/'): continue
            # SQLite was deliberately updated and has its own integrity check/backup.
            if database and safe(root, row['target']) == database: continue
            if database and row['source'].startswith(database.relative_to(root).as_posix() + '-') : continue
            expected = edits.get(row['source'], {}).get('after', row['sha256'])
            if digest(safe(root, row['target'])) != expected: raise RuntimeError('迁移后内容校验失败')
        data['status'] = 'done'
        write_manifest(manifest, data)
        return manifest
    except Exception:
        if connection: connection.rollback()
        data['status'] = 'interrupted'
        write_manifest(manifest, data)
        raise
    finally:
        if connection: connection.close()


def rollback(root: Path, manifest: Path):
    manifest = safe(root, manifest.resolve().relative_to(root.resolve()).as_posix())
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if Path(data['vault']).resolve() != root.resolve(): raise ValueError('备份不属于这个库')
    if data['status'] != 'done': raise ValueError('未完成迁移需根据 manifest 核对已执行路径，不能盲目回退')
    backup = safe(root, data['backup'])
    edits = {row['source']: row for row in data['edits']}
    for row in data['files']:
        if row['source'] == row['target'] and row['source'] not in edits: continue
        if data['database'] and str(safe(root, row['target'])).startswith(data['database']): continue
        expected = edits.get(row['source'], {}).get('after', row['sha256'])
        if digest(safe(root, row['target'])) != expected: raise ValueError('迁移后文件已编辑，拒绝覆盖')
    for row in data['renames']:
        if row['source'].split('/')[0] in OLD_ROOTS and safe(root, row['source']).exists():
            raise ValueError('旧目录已重新出现，拒绝合并')
    connection = sqlite3.connect(data['database']) if data['database'] else None
    try:
        if connection:
            connection.execute('BEGIN IMMEDIATE')
            for row in data['db_changes']:
                current = connection.execute(f'SELECT "{row["column"]}" FROM "{row["table"]}" WHERE rowid=?', (row['rowid'],)).fetchone()
                if not current or current[0] != row['after']: raise ValueError('数据库引用已变化，拒绝回退')
        for row in data['edits']:
            saved = backup / 'originals' / row['source']
            if digest(saved) != row['sha256']: raise ValueError('备份损坏')
        for row in data['edits']: shutil.copy2(backup / 'originals' / row['source'], safe(root, row['target']))
        for row in reversed(data['renames']):
            source, target = safe(root, row['source']), safe(root, row['target'])
            if source.exists(): raise ValueError('旧目录被占用')
            target.rename(source)
        if connection:
            for row in data['db_changes']:
                connection.execute(f'UPDATE "{row["table"]}" SET "{row["column"]}"=? WHERE rowid=?', (row['before'], row['rowid']))
            connection.commit()
        data['status'] = 'rolled_back'
        write_manifest(manifest, data)
    finally:
        if connection: connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, required=True)
    parser.add_argument('--database', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    root = args.vault.resolve(strict=True)
    if args.rollback:
        rollback(root, args.rollback)
        print('已回退目录与引用')
    elif args.apply:
        print(apply(root, args.database or root / '.torch-os/torch_os.sqlite3') or '已经是无编号目录')
    else:
        data = plan(root)
        print(json.dumps({'directories': len(data['renames']), 'files': len(data['files']), 'moves': data['mapping']}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
