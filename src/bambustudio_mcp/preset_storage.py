"""POSIX storage for configured preset directories; never follow profile symlinks."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path

MAX_JSON = 1024 * 1024


def encode(data):
    raw = (json.dumps(data, indent=2, allow_nan=False) + "\n").encode()
    if len(raw) > MAX_JSON:
        raise ValueError("Resolved preset exceeds 1 MiB")
    return raw


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def document(raw):
    if len(raw) > MAX_JSON:
        raise ValueError("Preset JSON exceeds 1 MiB")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("Non-finite JSON value")
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def preset_name(name):
    if (not isinstance(name, str) or not name or not name.isprintable() or name != name.strip() or name.startswith(".")
            or len(name.encode()) > 180 or any(c in "/\\" or ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError("New preset names must be visible filenames without separators or control characters")
    return name


@contextlib.contextmanager
def directory(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Preset roots must be absolute paths without traversal")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_file(fd, name):
    file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(file, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_JSON:
            raise ValueError("Preset must be a regular, single-link file under 1 MiB")
        raw = stream.read(MAX_JSON + 1)
        if len(raw) > MAX_JSON:
            raise ValueError("Preset JSON exceeds 1 MiB")
        return raw


@contextlib.contextmanager
def write_lock(fd):
    lock = os.open(".mcp-preset.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Unsafe preset lock")
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
    finally:
        os.close(lock)


def write_file(fd, name, raw, replace=False):
    temporary = ".preset-" + uuid.uuid4().hex
    file = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(file, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        else:
            os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            os.unlink(temporary, dir_fd=fd)
        os.fsync(fd)
        if read_file(fd, name) != raw:
            raise OSError("Preset readback did not match")
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def backup_directory(fd, name, create=False):
    parent = None
    child = None
    try:
        for part in (".preset-backups", digest(name.encode())):
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            if parent is not None:
                os.close(parent)
            parent = child
            fd = child
        yield child
    finally:
        if parent is not None:
            os.close(parent)


def save_backup(fd, name, raw):
    revision = digest(raw)
    with backup_directory(fd, name, create=True) as backups:
        try:
            write_file(backups, revision + ".json", raw)
        except FileExistsError:
            if read_file(backups, revision + ".json") != raw:
                raise ValueError("Existing backup checksum mismatch")
    return revision
