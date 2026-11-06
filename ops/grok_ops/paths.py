"""Fixed root-owned paths for the grok-ops lane.

Nothing in this module reads the process environment. Callers that execute
release code pin the current symlink once, re-check it, and open the target
with O_NOFOLLOW inside /root/afs-releases.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

CURRENT_LINK = "/root/autonomous-futures-system"
RELEASES_PREFIX = "/root/afs-releases"
SHARED_DIR = "/root/afs-shared"
ENV_FILE = "/root/afs-shared/.env"
LOCK_DIR = "/root/afs-shared/deploy.lock"
PREVIOUS_FILE = "/root/afs-shared/current.previous"
TOKEN_DIR = "/var/lib/afs-ops/tokens"
AUDIT_LOG = "/var/lib/afs-ops/audit.log"
FLAT_FILE = "/var/lib/afs-ops/flat.json"
EXPOSURE_FILE = "/var/lib/afs-ops/exposure.json"
RELEASE_ENV = "/etc/afs-ops/release.env"
INSTALLED_CODE = "/usr/local/lib/afs-ops"
PYTHON = "/usr/bin/python3"
BASELINE_REL = "scripts/ops/baseline_reset.sh"
ATOMIC_REL = "scripts/atomic_release.sh"
VARIANTS_REL = "research/trend_r3_forward/trend_variants_shadow.py"


class OwnershipError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PinnedRelease:
    link: str
    real: str
    prefix: str
    dev: int
    ino: int

    def reverify(self, *, uid: int) -> None:
        listed = os.lstat(self.link)
        assert_owned(listed, uid=uid)
        if not stat.S_ISLNK(listed.st_mode):
            raise OwnershipError("release-link")
        real = _realpath_inside(os.readlink(self.link), self.prefix)
        if real != self.real:
            raise OwnershipError("release-toctou")
        again = os.lstat(self.real)
        if (again.st_dev, again.st_ino) != (self.dev, self.ino):
            raise OwnershipError("release-toctou")
        assert_owned(again, uid=uid)


def assert_owned(st: os.stat_result, *, uid: int) -> None:
    if st.st_uid != uid:
        raise OwnershipError("ownership")
    # Linux reports symlink modes as 0777 and ignores them. The parent
    # directory's ownership is what stops a non-root user from replacing it.
    if stat.S_ISLNK(st.st_mode):
        return
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OwnershipError("ownership")


def assert_directory(path: str, *, uid: int) -> None:
    listed = os.lstat(path)
    if stat.S_ISLNK(listed.st_mode) or not stat.S_ISDIR(listed.st_mode):
        raise OwnershipError("ownership")
    assert_owned(listed, uid=uid)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != (listed.st_dev, listed.st_ino):
            raise OwnershipError("toctou")
        assert_owned(current, uid=uid)
    finally:
        os.close(fd)


def _realpath_inside(target: str, prefix: str) -> str:
    if not target.startswith("/"):
        raise OwnershipError("release-prefix")
    real = os.path.realpath(target)
    root = prefix.rstrip("/")
    if real != root and not real.startswith(root + "/"):
        raise OwnershipError("release-prefix")
    return real


def _assert_components(real: str, prefix: str, *, uid: int) -> None:
    root = prefix.rstrip("/")
    if not _is_inside(real, root):
        raise OwnershipError("release-prefix")
    cursor = root
    assert_directory(cursor, uid=uid)
    rest = real[len(root):].strip("/")
    parts = [] if not rest else rest.split("/")
    for part in parts:
        if part in {"", ".", ".."}:
            raise OwnershipError("release-prefix")
        cursor = cursor + "/" + part
        listed = os.lstat(cursor)
        if stat.S_ISLNK(listed.st_mode):
            raise OwnershipError("release-symlink")
        assert_owned(listed, uid=uid)


def _is_inside(path: str, prefix: str) -> bool:
    root = prefix.rstrip("/")
    return path == root or path.startswith(root + "/")


def pin_release(link: str, prefix: str, *, uid: int) -> PinnedRelease:
    parent = os.path.dirname(link)
    assert_directory(parent, uid=uid)
    listed = os.lstat(link)
    if not stat.S_ISLNK(listed.st_mode):
        raise OwnershipError("release-link")
    assert_owned(listed, uid=uid)
    real = _realpath_inside(os.readlink(link), prefix)
    _assert_components(real, prefix, uid=uid)
    fd = os.open(real, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        current = os.fstat(fd)
        again_link = os.lstat(link)
        again_real = _realpath_inside(os.readlink(link), prefix)
        listed_real = os.lstat(real)
        if again_real != real or (current.st_dev, current.st_ino) != (listed_real.st_dev, listed_real.st_ino):
            raise OwnershipError("release-toctou")
        if (again_link.st_dev, again_link.st_ino) != (listed.st_dev, listed.st_ino):
            raise OwnershipError("release-toctou")
        assert_owned(current, uid=uid)
    finally:
        os.close(fd)
    return PinnedRelease(link=link, real=real, prefix=prefix, dev=current.st_dev, ino=current.st_ino)


def open_verified(root_real: str, relative: str, *, uid: int) -> str:
    """Open relative under root_real with O_NOFOLLOW and return that path.

    A missing leaf raises FileNotFoundError. A symlink, a writable directory,
    or a path that leaves root_real raises OwnershipError.
    """
    if relative.startswith("/") or ".." in Path(relative).parts:
        raise OwnershipError("path")
    directory = root_real.rstrip("/")
    parts = [part for part in relative.split("/") if part]
    if not parts:
        raise OwnershipError("path")
    for part in parts[:-1]:
        directory = directory + "/" + part
        listed = os.lstat(directory)
        if stat.S_ISLNK(listed.st_mode) or not stat.S_ISDIR(listed.st_mode):
            raise OwnershipError("release-symlink")
        assert_owned(listed, uid=uid)
    final = directory + "/" + parts[-1]
    if not _is_inside(os.path.realpath(directory), root_real.rstrip("/")):
        raise OwnershipError("release-prefix")
    try:
        fd = os.open(final, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise OwnershipError("release-symlink") from exc
    try:
        current = os.fstat(fd)
        again = os.lstat(final)
        if stat.S_ISLNK(again.st_mode) or (current.st_dev, current.st_ino) != (again.st_dev, again.st_ino):
            raise OwnershipError("toctou")
        assert_owned(current, uid=uid)
        real = os.path.realpath(final)
        if not _is_inside(real, root_real.rstrip("/")):
            raise OwnershipError("release-prefix")
    finally:
        os.close(fd)
    return final
