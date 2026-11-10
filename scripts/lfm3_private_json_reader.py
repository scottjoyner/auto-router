"""Shared bounded, owner-private JSON input reader for LFM3 custodian CLIs.

Only local private regular files; this is NOT source authentication, trusted
custody, model execution or permission to infer. Do not log payloads or paths.
"""
from __future__ import annotations

import json
import os
import stat

from research.lfm3_top5.unstructured_triage_audit import (
    _invalid_constant, _pairs_no_duplicate,
)


def read_private_bytes(path: str, *, limit: int, dir_fd: int | None = None) -> bytes:
    """Read a bounded, confidential JSON file via one no-follow file handle.

    Reject FIFO/device/directory, hard links, non-owner ownership and any
    group/other permission. A malicious same-UID writer still needs external
    custody controls; no local filesystem check proves independent evidence.
    """
    if type(path) is not str or not path or type(limit) is not int or limit < 1:
        raise ValueError("invalid_private_input_path_or_limit")
    if dir_fd is not None and (
        type(dir_fd) is not int or dir_fd < 0 or '/' in path or path in ('.', '..')
    ):
        raise ValueError("invalid_pinned_directory_input_name")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise ValueError("required_local_filesystem_safety_features_absent")
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    fd = os.open(path, flags, dir_fd=dir_fd)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        mode = stat.S_IMODE(before.st_mode)
        if (not stat.S_ISREG(before.st_mode) or
            before.st_uid != os.geteuid() or
            before.st_nlink != 1 or
            not (mode & stat.S_IRUSR) or
            (mode & (stat.S_IRWXG | stat.S_IRWXO | stat.S_IXUSR)) or
            not 0 < before.st_size <= limit):
            raise ValueError("unapproved_private_input_file_metadata")
        content = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if (not 0 < len(content) <= limit or
            len(content) != before.st_size or
            before.st_dev != after.st_dev or
            before.st_ino != after.st_ino or
            before.st_size != after.st_size or
            before.st_mtime_ns != after.st_mtime_ns or
            before.st_ctime_ns != after.st_ctime_ns):
            raise ValueError("oversized_or_mutated_private_input")
    return content


def read_private_json(path: str, *, limit: int, dir_fd: int | None = None):
    """Strict duplicate-key JSON from the same bounded private FD interface."""
    content=read_private_bytes(path,limit=limit,dir_fd=dir_fd)
    return json.loads(content.decode("utf-8"),
                      object_pairs_hook=_pairs_no_duplicate,
                      parse_constant=_invalid_constant)
