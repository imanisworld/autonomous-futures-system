"""Fixed root-owned paths for the grok-ops lane.

Nothing in this module reads the process environment or the process cwd.
Release and code paths are opened from ``/`` with openat,
O_NOFOLLOW, and fstat on the fd just opened. A symlink is refused before any
mode or ownership check. The release pin keeps the directory fd, and a later
open has to match that fd's device and inode.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass

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
# Operator-signed files. The lane passes these paths through and does not
# write, mint, or sign them.
APPROVAL_FILE = "/root/afs-maint/afs-approval.txt"
SIGNATURE_FILE = "/root/afs-maint/afs-approval.txt.sig"

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW
_Fstat = Callable[[int], os.stat_result]


class OwnershipError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class PinnedRelease:
    link: str
    real: str
    prefix: str
    dev: int
    ino: int
    dirfd: int
    uid: int
    fstat: _Fstat = os.fstat

    def close(self) -> None:
        if self.dirfd >= 0:
            os.close(self.dirfd)
            self.dirfd = -1

    def reverify(self) -> None:
        self._held_matches()
        if not self.link:
            return
        fresh = pin_release(self.link, self.prefix, uid=self.uid, fstat=self.fstat)
        try:
            if (fresh.dev, fresh.ino) != (self.dev, self.ino) or fresh.real != self.real:
                raise OwnershipError("release-toctou")
        finally:
            fresh.close()

    def _held_matches(self) -> os.stat_result:
        if self.dirfd < 0:
            raise OwnershipError("release-toctou")
        current = self.fstat(self.dirfd)
        if stat.S_ISLNK(current.st_mode):
            raise OwnershipError("release-symlink")
        if (current.st_dev, current.st_ino) != (self.dev, self.ino):
            raise OwnershipError("release-toctou")
        _require_directory(current, uid=self.uid)
        return current


def _require_directory(st: os.stat_result, *, uid: int) -> None:
    """Refuse a symlink before looking at owner or mode bits."""
    if stat.S_ISLNK(st.st_mode):
        raise OwnershipError("release-symlink")
    if not stat.S_ISDIR(st.st_mode):
        raise OwnershipError("ownership")
    if st.st_uid != uid:
        raise OwnershipError("ownership")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OwnershipError("ownership")


def _require_file(st: os.stat_result, *, uid: int) -> None:
    if stat.S_ISLNK(st.st_mode):
        raise OwnershipError("release-symlink")
    if not stat.S_ISREG(st.st_mode):
        raise OwnershipError("ownership")
    if st.st_uid != uid:
        raise OwnershipError("ownership")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OwnershipError("ownership")


def _parts(path: str) -> list[str]:
    if not path.startswith("/") or "\x00" in path:
        raise OwnershipError("release-prefix")
    parts = [part for part in path.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise OwnershipError("release-prefix")
    return parts


def _walk_dir(path: str, *, uid: int, fstat: _Fstat) -> int:
    """Open ``path`` from ``/`` and return the directory fd. Caller closes it."""
    dirfd = os.open("/", _DIR_FLAGS)
    try:
        _require_directory(fstat(dirfd), uid=uid)
        for part in _parts(path):
            try:
                nextfd = os.open(part, _DIR_FLAGS, dir_fd=dirfd)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise OwnershipError("release-symlink") from exc
                raise OwnershipError("ownership") from exc
            os.close(dirfd)
            dirfd = nextfd
            _require_directory(fstat(dirfd), uid=uid)
        return dirfd
    except Exception:
        os.close(dirfd)
        raise


def _inside(real: str, prefix: str) -> str:
    root = prefix.rstrip("/")
    if real != root and not real.startswith(root + "/"):
        raise OwnershipError("release-prefix")
    return real


def assert_directory(path: str, *, uid: int, fstat: _Fstat = os.fstat) -> None:
    os.close(_walk_dir(path, uid=uid, fstat=fstat))


def pin_release(link: str, prefix: str, *, uid: int, fstat: _Fstat = os.fstat) -> PinnedRelease:
    parent = os.path.dirname(link)
    name = os.path.basename(link)
    if not name or parent == link:
        raise OwnershipError("release-link")
    parent_fd = _walk_dir(parent, uid=uid, fstat=fstat)
    release_fd = -1
    try:
        listed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISLNK(listed.st_mode):
            raise OwnershipError("release-link")
        if listed.st_uid != uid:
            raise OwnershipError("ownership")
        target = os.readlink(name, dir_fd=parent_fd)
        real = _inside("/" + "/".join(_parts(target)), prefix)
        release_fd = _walk_dir(real, uid=uid, fstat=fstat)
        current = fstat(release_fd)
        if stat.S_ISLNK(current.st_mode):
            raise OwnershipError("release-symlink")
        _require_directory(current, uid=uid)
        again = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (again.st_dev, again.st_ino) != (listed.st_dev, listed.st_ino):
            raise OwnershipError("release-toctou")
        followed = os.stat(name, dir_fd=parent_fd, follow_symlinks=True)
        if (followed.st_dev, followed.st_ino) != (current.st_dev, current.st_ino):
            raise OwnershipError("release-toctou")
        pin = PinnedRelease(
            link=link,
            real=real,
            prefix=prefix.rstrip("/"),
            dev=current.st_dev,
            ino=current.st_ino,
            dirfd=release_fd,
            uid=uid,
            fstat=fstat,
        )
        release_fd = -1
        return pin
    finally:
        if release_fd >= 0:
            os.close(release_fd)
        os.close(parent_fd)


def hold_directory(path: str, *, uid: int, fstat: _Fstat = os.fstat) -> PinnedRelease:
    """Open ``path`` from ``/`` and keep the directory fd.

    Production release pins go through ``pin_release``. This walk is the
    same openat check for a directory that is not reached through the
    current-release symlink.
    """
    fd = _walk_dir(path, uid=uid, fstat=fstat)
    current = fstat(fd)
    return PinnedRelease(
        link="",
        real="/" + "/".join(_parts(path)),
        prefix="/" + "/".join(_parts(path)),
        dev=current.st_dev,
        ino=current.st_ino,
        dirfd=fd,
        uid=uid,
        fstat=fstat,
    )


def open_verified(
    root_real: str,
    relative: str,
    *,
    uid: int,
    pin: PinnedRelease | None = None,
    fstat: _Fstat = os.fstat,
) -> str:
    """Open ``relative`` under a pinned directory fd and return that path.

    A missing leaf raises FileNotFoundError. A symlink raises OwnershipError
    before mode or owner bits are consulted. When ``pin`` is set, its held
    device and inode have to match.
    """
    owns = pin is None
    if pin is None:
        pin = hold_directory(root_real, uid=uid, fstat=fstat)
    try:
        if pin.real.rstrip("/") != root_real.rstrip("/"):
            raise OwnershipError("release-prefix")
        if pin.uid != uid:
            raise OwnershipError("ownership")
        pin.reverify()
        return _open_from(pin, relative)
    finally:
        if owns and pin is not None:
            pin.close()


def _open_from(pin: PinnedRelease, relative: str) -> str:
    if relative.startswith("/") or not relative:
        raise OwnershipError("path")
    parts = [part for part in relative.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise OwnershipError("path")
    pin._held_matches()
    directory = pin.dirfd
    opened: list[int] = []
    filefd = -1
    try:
        for part in parts[:-1]:
            try:
                nextfd = os.open(part, _DIR_FLAGS, dir_fd=directory)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise OwnershipError("release-symlink") from exc
                raise OwnershipError("ownership") from exc
            opened.append(nextfd)
            directory = nextfd
            _require_directory(pin.fstat(directory), uid=pin.uid)
        try:
            filefd = os.open(parts[-1], _FILE_FLAGS, dir_fd=directory)
        except FileNotFoundError:
            raise
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise OwnershipError("release-symlink") from exc
            raise OwnershipError("ownership") from exc
        current = pin.fstat(filefd)
        if stat.S_ISLNK(current.st_mode):
            raise OwnershipError("release-symlink")
        _require_file(current, uid=pin.uid)
        pin._held_matches()
        return pin.real.rstrip("/") + "/" + "/".join(parts)
    finally:
        if filefd >= 0:
            os.close(filefd)
        for fd in opened:
            os.close(fd)


def file_identity(path: str, *, uid: int, fstat: _Fstat = os.fstat) -> tuple[int, int]:
    """Device and inode of a regular file opened from ``/`` with O_NOFOLLOW."""
    parent = os.path.dirname(path)
    name = os.path.basename(path)
    dirfd = _walk_dir(parent, uid=uid, fstat=fstat)
    filefd = -1
    try:
        try:
            filefd = os.open(name, _FILE_FLAGS, dir_fd=dirfd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise OwnershipError("release-symlink") from exc
            raise OwnershipError("code-path") from exc
        current = fstat(filefd)
        if stat.S_ISLNK(current.st_mode):
            raise OwnershipError("release-symlink")
        _require_file(current, uid=uid)
        return current.st_dev, current.st_ino
    finally:
        if filefd >= 0:
            os.close(filefd)
        os.close(dirfd)
