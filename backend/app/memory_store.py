"""Small write transaction for mentor memory/portrait files, not a new data store.

Cooperating readers/writers lock the same canonical parent and filename before
probing the file (Windows path resolution can itself block replacement). External editors do
not take this lock: a byte comparison catches edits before commit, but cannot
provide an OS-level compare-and-swap against arbitrary external writers.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path


# Re-entrant: nested operations (e.g. question -> archive operation) may hash
# to the same advisory slot in the same thread. The file locks remain separate.
_THREAD_LOCKS = [threading.RLock() for _ in range(64)]


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


class DocumentUpdate:
    def __init__(self, path, error_type):
        self.path = path
        self.error_type = error_type
        self.original = _read_bytes(path)

    def backup(self, directory: Path, stem: str) -> Path | None:
        if self.original is None:
            return
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{stem}-{uuid.uuid4().hex}.md"
        # Exclusive creation never overwrites a previous history file.
        with target.open("xb") as handle:
            try:
                handle.write(self.original)
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                handle.close()
                target.unlink(missing_ok=True)
                raise
        return target

    def write(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="\n", delete=False,
                dir=self.path.parent, prefix=".memory-", suffix=".tmp",
            ) as handle:
                temporary = Path(handle.name)
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            if _read_bytes(self.path) != self.original:
                raise self.error_type("记忆文件已在别处修改，本次未覆盖；请重新读取后重试", 409)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


@contextmanager
def _advisory_lock(key: str, *, namespace: str = "torch-os-locks"):
    """Thread + OS file lock shared by cooperating torch processes.

    Lock files live in the temp directory, outside the user's Markdown
    hierarchy. They are never unlinked: another process may hold the same
    inode while a caller replaces the key file.
    """
    with _THREAD_LOCKS[int(key[:8], 16) % len(_THREAD_LOCKS)]:
        lock_dir = Path(tempfile.gettempdir()) / namespace
        lock_dir.mkdir(parents=True, exist_ok=True)
        with (lock_dir / f"{key}.lock").open("a+b") as handle:
            # Windows byte-range locks may cover EOF. Never initialize a
            # byte here: another process may already have locked that byte.
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def operation_lock(key: str):
    """Serialize one archive operation key across threads and processes."""
    digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
    with _advisory_lock(digest, namespace="torch-os-operation-locks"):
        yield


@contextmanager
def _locked_path(path: Path, vault_dir: Path | None = None):
    requested = path.absolute()
    # Do not resolve/stat the leaf before taking its lock: CPython 3.12's
    # Windows realpath opens it without delete sharing. Resolve only the parent
    # for a stable key; retain the original path for the Vault link checks.
    canonical = requested.parent.resolve() / requested.name
    key = hashlib.sha256(os.path.normcase(str(canonical)).encode("utf-8")).hexdigest()
    with _advisory_lock(key, namespace="torch-os-memory-locks"):
        if vault_dir is not None:
            from .vault import safe_note_path
            root = Path(vault_dir).resolve()
            checked = safe_note_path(root, requested.relative_to(root).as_posix())
        else:
            if requested.is_symlink() or (hasattr(requested, "is_junction") and requested.is_junction()):
                raise ValueError("不读写链接记忆文件")
            checked = canonical
        yield checked


def read_document(path: Path, *, vault_dir: Path) -> bytes | None:
    """Read a complete version without racing a cooperating Windows replace."""
    with _locked_path(path, vault_dir) as checked:
        return _read_bytes(checked)


@contextmanager
def update_document(path: Path, error_type, *, vault_dir: Path | None = None):
    try:
        with _locked_path(path, vault_dir) as checked:
            yield DocumentUpdate(checked, error_type)
    except OSError as error:
        raise error_type("记忆保存失败：无法完成锁定、历史备份或文件写入；请检查存储后重试", 500) from error
