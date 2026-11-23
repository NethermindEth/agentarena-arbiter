"""
Per-run workspace and per-agent sandboxes for the findings-triage pipeline.

Layout of one run (``<work_dir>/run-<random>/``, created 0700)::

    base/repo     copy of the task checkout; the build agent installs deps + prebuilds here
    base/.home    the build agent's HOME (toolchain caches: solc builds, cargo registry...)
    a/<id>/{repo,.home,t}       one private copy of ``base`` per agent (``t`` is its TMPDIR)
    evidence/<unit>/<role>/...  PoC files each agent created (kept after the run, until
                                :func:`prune_runs` deletes the run)
    .active       locked while the run is in progress (see ``RUN_LOCK``)

The agent directories have short names because the CLI binds its sandbox proxy sockets in
TMPDIR, and a Unix socket path is limited to 107 bytes (see :func:`check_socket_paths`).

Isolation model (see also app/core/claude_code_cli.py):

* Every agent works in its **own full copy** of the prebuilt base (``cp -a --reflink=auto``;
  no hard links, so no inode is shared with the base or another agent).
* Agent commands run in the Claude Code **bubblewrap sandbox** with ``failIfUnavailable``
  (never fall back to unsandboxed execution) and ``allowUnsandboxedCommands: false`` (the
  model cannot opt out per command). Writes are confined to the agent's own
  ``repo``/``.home``/``.tmp``; reads of the service user's HOME, the service directory, the
  task-data directory, the container credential mounts and the whole triage work directory
  (every other agent, run and piece of evidence) are denied, except the agent's own sandbox;
  network is a strict allowlist (empty for finding agents, package registries for the build
  agent). bubblewrap must be >= 0.8.0 and not setuid so nested user namespaces are disabled;
  Claude Code's "weaker nested sandbox" mode is deliberately not supported (a nested user
  namespace can undo its write denies).
* PoCs move between agents only through **Python**: after an agent exits (and any process it
  left behind is killed), the PoC files it *created* (new code files under a test, script or
  poc directory, never configuration or dependencies; regular files with plain names only,
  read through directory handles that never follow a symlink, size-capped) are copied into
  ``evidence/``, and the next agent gets them under ``.triage/pocs/<role>/`` in a *fresh*
  sandbox built from the clean base. Files an agent *modified* are never propagated; they are
  shown to later agents as a system-computed diff (``.triage/diffs/<role>.diff``). So a
  verifier always re-runs a PoC against unmodified sources: a PoC that only passes because its
  author rigged the sources or the test libraries does not reproduce.
* The base itself holds the sponsor's code: when it is sealed, every file of the task checkout
  the build agent modified or deleted is put back (``evidence/build-restored.diff`` keeps its
  version, and the agents are told which files). What the build agent *added* stays:
  dependencies it fetched, build output, and its HOME (toolchains and caches, but also any
  dotfile it wrote). That build environment is the same for every agent of every finding, and
  the build agent never sees a finding.
"""

from __future__ import annotations

import ctypes
import difflib
import fcntl
import json
import logging
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRIAGE_DIR = ".triage"
AGENTS_DIR = "a"
TMP_DIR = "t"
RUN_PREFIX = "run-"
# Held (flock) by the process running the run, here or in another process sharing the work dir;
# the kernel drops it when that process dies, so a run whose lock can be taken has ended.
RUN_LOCK = ".active"
# Directories never compared, restored or indexed, at any depth: the system's own staging dir,
# VCS metadata, installed packages and bytecode caches. None of it is code under audit.
SKIP_DIFF_DIRS = frozenset({".triage", ".git", "node_modules", "__pycache__", ".pytest_cache"})
# Build output, skipped only next to a build manifest (a project root), where the build writes
# it. Elsewhere a directory with one of these names may hold sources: src/cache/, a crate named
# target.
BUILD_OUTPUT_DIRS = frozenset(
    {"out", "cache", "target", "artifacts", "broadcast", "typechain", "typechain-types", "coverage"}
)
_BUILD_MANIFEST = re.compile(
    r"foundry\.toml|Cargo\.toml|Scarb\.toml|package\.json|truffle(-config)?\.js"
    r"|hardhat\.config\.[cm]?[jt]s"
)
MAX_STAGED_FILES = 100
MAX_STAGED_FILE_BYTES = 1_000_000
MAX_STAGED_TOTAL_BYTES = 8_000_000
MAX_DIFF_FILE_BYTES = 300_000
MAX_DIFF_CHARS = 60_000
MAX_NOT_STAGED_LISTED = 200
MIN_BWRAP_VERSION = (0, 8, 0)
# The CLI binds its sandbox proxy sockets at $TMPDIR/claude-{http,socks}-<16 hex>.sock, and
# sun_path holds 108 bytes including the terminating NUL.
_CLI_SOCKET_NAME = "claude-socks-" + "0" * 16 + ".sock"
MAX_UNIX_SOCKET_PATH_BYTES = 107
_RUN_NAME_LEN = len(RUN_PREFIX) + 8  # tempfile.mkdtemp adds 8 random characters
_SANDBOX_ID_DIGITS = 6
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
# Path components of files that may be staged into other agents' prompts/sandboxes.
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.@+=-]{1,128}$", re.ASCII)
# Only PoC files move between agents: new code files under one of these directories.
POC_DIRS = frozenset({"test", "tests", "script", "poc"})
POC_EXTENSIONS = frozenset(
    (".sol", ".vy", ".huff", ".yul", ".rs", ".go", ".move", ".cairo", ".py")
    + (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts")
)
# Never staged, even inside a PoC directory: dependencies, and files that change how a project
# builds or which code its tests load (configs, dotfiles, auto-loaded hooks). A PoC that passes
# only because of one of them would not show anything about the project as configured.
_NO_STAGE_DIRS = frozenset({"lib", "node_modules"})
_NO_STAGE_NAMES = frozenset({"conftest.py", "build.rs", "setup.py"})
_CONFIG_NAME = re.compile(r"(^|[.-])config\.[A-Za-z]+$")
# What these name depends on the process that follows them (/proc/self/cwd, /dev/fd...): a link
# through them can resolve harmlessly from the service and into another sandbox from an agent.
_PROCESS_RELATIVE = (Path("/proc"), Path("/dev"))


class SandboxUnavailable(RuntimeError):
    """The command sandbox (or the CLI that runs it) cannot run here."""


def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """
    ``subprocess.run`` with only ``PATH`` in the environment. The service's environment holds
    the API keys, and a child is dumpable again after exec (see :func:`harden_service_process`),
    so while it ran a process of the same user could read them from its /proc/<pid>/environ.
    """
    env = {"PATH": os.environ.get("PATH", os.defpath)}
    # Callers pass fixed argv: coreutils on paths the service built, or the bwrap shutil.which
    # found; PATH is the service's own.
    return subprocess.run(argv, env=env, **kwargs)  # noqa: S603  see above


# ----------------------------------------------------------------------------------------
# Host checks
# ----------------------------------------------------------------------------------------
def _bwrap_version(bwrap: str) -> tuple[int, ...] | None:
    try:
        out = _run([bwrap, "--version"], capture_output=True, text=True, timeout=10).stdout
    except OSError, subprocess.TimeoutExpired:
        return None
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", out or "")
    return tuple(int(x or 0) for x in m.groups()) if m else None


def probe_bubblewrap() -> str | None:
    """
    Check that bubblewrap can provide the isolation the Claude Code sandbox relies on.

    Returns ``None`` when it can, otherwise a human-readable reason. This only avoids starting a
    run that cannot work; the hard guarantee is ``sandbox.failIfUnavailable`` in every agent's
    settings (the CLI itself refuses to start without a working sandbox).
    """
    if not sys.platform.startswith("linux"):
        return f"the bubblewrap sandbox is Linux-only (platform: {sys.platform})"
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return "bubblewrap (bwrap) is not installed"
    if not shutil.which("socat"):
        return "socat is not installed (needed by the sandbox network proxy)"
    if os.stat(bwrap).st_mode & stat.S_ISUID:
        return "bwrap is setuid; it then cannot disable nested user namespaces (which can undo write denies)"
    version = _bwrap_version(bwrap)
    if version is None or version < MIN_BWRAP_VERSION:
        return (
            f"bubblewrap {'.'.join(map(str, version)) if version else '(unknown version)'} is too old; "
            f">= {'.'.join(map(str, MIN_BWRAP_VERSION))} is needed to disable nested user namespaces"
        )
    argv = [
        bwrap,
        "--unshare-all",
        "--die-with-parent",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "true",
    ]
    try:
        proc = _run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"bubblewrap probe failed: {exc}"
    if proc.returncode != 0:
        return f"bubblewrap cannot create namespaces here: {(proc.stderr or '').strip()[:300]}"
    return None


def harden_service_process() -> None:
    """
    Mark this (the arbiter) process non-dumpable (Linux), as defence in depth.

    The arbiter's environment holds the Claude, Gemini and backend API keys. A non-dumpable
    process's /proc entries are root-owned, so even a sandbox escape running as the same user
    cannot read ``/proc/<arbiter pid>/environ``. Children are dumpable again (the flag resets
    on exec), so each gets a minimal environment instead: ``child_env`` for the ``claude`` CLI,
    :func:`_run` for the coreutils and bubblewrap the service runs.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        PR_SET_DUMPABLE = 4
        if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            logger.warning("prctl(PR_SET_DUMPABLE, 0) failed: errno %s", ctypes.get_errno())
        else:
            logger.info("service process marked non-dumpable")
    except OSError as exc:  # pragma: no cover - libc always loadable on Linux
        logger.warning("could not mark the service process non-dumpable: %s", exc)


def kill_processes_under(root: Path) -> int:
    """
    SIGKILL every process of this user whose working directory is inside ``root``.

    The CLI starts agent commands detached, so killing the CLI's process group does not reach
    them. Anything an agent left running in its sandbox is killed before its files are read.
    """
    if not sys.platform.startswith("linux"):
        return 0
    root_s = str(Path(root).resolve())
    me, uid, killed = os.getpid(), os.getuid(), 0
    for _ in range(3):  # a dying process may have forked; sweep a few times
        found = 0
        for entry in os.listdir("/proc"):
            if not entry.isdigit() or int(entry) == me:
                continue
            try:
                if os.stat(f"/proc/{entry}").st_uid != uid:
                    continue
                cwd = os.readlink(f"/proc/{entry}/cwd")
            except OSError:
                continue
            if cwd == root_s or cwd.startswith(root_s + os.sep):
                try:
                    os.kill(int(entry), signal.SIGKILL)
                    found += 1
                except OSError:
                    pass
        killed += found
        if not found:
            break
    if killed:
        logger.warning("killed %d leftover process(es) in %s", killed, root_s)
    return killed


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_work_dir(configured: str, protected: Sequence[Path]) -> Path:
    """
    Resolve the triage work directory and check it lies outside every protected region.

    Agents are denied all reads inside ``protected`` (HOME, service dir, task data), and a
    permission deny cannot be re-allowed for a sub-path, so sandboxes must live elsewhere.
    """
    base = (
        Path(configured).expanduser()
        if configured
        else Path(tempfile.gettempdir()) / f"arbiter-triage-{os.getuid()}"
    )
    base = base.resolve()
    for region in protected:
        if _is_within(base, Path(region).resolve()):
            raise ValueError(
                f"TRIAGE_WORK_DIR {base} is inside {region}, which triage agents may not read; "
                "set TRIAGE_WORK_DIR to a disk-backed directory outside it"
            )
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(base)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise ValueError(f"TRIAGE_WORK_DIR {base} must be a real directory owned by this user")
    os.chmod(base, 0o700)
    return base


def longest_socket_path(work_dir: Path) -> int:
    """Bytes in the longest proxy socket path an agent's CLI binds under ``work_dir``."""
    tmpdir = (
        Path(work_dir) / ("r" * _RUN_NAME_LEN) / AGENTS_DIR / ("0" * _SANDBOX_ID_DIGITS) / TMP_DIR
    )
    return len(os.fsencode(tmpdir / _CLI_SOCKET_NAME))


def check_socket_paths(work_dir: Path) -> None:
    """
    Raise ``ValueError`` when agents' proxy sockets under ``work_dir`` would exceed the Unix
    socket path limit: the sandboxed agent could then not start.
    """
    longest = longest_socket_path(work_dir)
    if longest > MAX_UNIX_SOCKET_PATH_BYTES:
        allowed = MAX_UNIX_SOCKET_PATH_BYTES - (longest - len(os.fsencode(work_dir)))
        raise ValueError(
            f"TRIAGE_WORK_DIR {work_dir} is too long: agents' sandbox sockets would need "
            f"{longest} bytes, over the {MAX_UNIX_SOCKET_PATH_BYTES} a Unix socket path allows; "
            f"use a path of at most {allowed} bytes"
        )


def _lock_run(root: Path, *, wait: bool) -> int | None:
    """
    Take the run's lock (see ``RUN_LOCK``) and return its descriptor, or None when another
    open file holds it (a run in progress, even in this process). Closing the descriptor
    releases the lock.
    """
    fd = os.open(root / RUN_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd


def prune_runs(work_dir: Path, max_age_days: int, *, keep_sandboxes: bool) -> int:
    """
    Tidy the runs no process is working on any more: delete those last changed over
    ``max_age_days`` ago, evidence included, and the base and sandboxes a run left behind when
    its process died (a full repository copy each). With ``keep_sandboxes`` (``DEBUG``) those
    stay until the run ages out.

    Only real ``run-*`` directories owned by this user are touched, never a run in progress.
    Returns how many runs were deleted.
    """
    cutoff = time.time() - max_age_days * 86_400
    removed = cleared = 0
    with os.scandir(work_dir) as entries:
        for entry in entries:
            if not entry.name.startswith(RUN_PREFIX):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
                continue
            path = Path(entry.path)
            # Held while tidying, so a run being created waits for it before making its dirs.
            try:
                fd = _lock_run(path, wait=False)
            except OSError:
                continue
            if fd is None:
                continue  # in progress
            try:
                if st.st_mtime < cutoff:
                    _force_rmtree(path)
                    removed += 1
                elif not keep_sandboxes and any(
                    os.path.lexists(path / d) for d in ("base", AGENTS_DIR)
                ):
                    _force_rmtree(path / "base")
                    _force_rmtree(path / AGENTS_DIR)
                    cleared += 1
            finally:
                os.close(fd)
    if removed:
        logger.info(
            "removed %d triage run(s) older than %d days from %s", removed, max_age_days, work_dir
        )
    if cleared:
        logger.warning(
            "deleted the sandboxes of %d triage run(s) that ended without cleaning up (the "
            "process died); their evidence stays",
            cleared,
        )
    return removed


# ----------------------------------------------------------------------------------------
# Safe file helpers (agent-produced trees are adversarial: symlinks, FIFOs, odd names, races)
# ----------------------------------------------------------------------------------------
def _safe_rel(rel: str) -> bool:
    """A relative path made only of plain, printable-ASCII components (no '.', '..', newlines)."""
    parts = rel.split("/")
    return bool(parts) and all(_SAFE_COMPONENT.match(p) and p not in (".", "..") for p in parts)


def _open_beneath(root: Path, rel: str) -> int | None:
    """
    Open the regular file ``root/rel`` for reading, opening every path component relative to
    its parent's directory handle with ``O_NOFOLLOW``: a component swapped for a symlink (even
    concurrently) makes the open fail instead of escaping ``root``. Returns the descriptor, or
    None when the file cannot be opened that way or is not a regular file.
    """
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    flags_dir = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(root, flags_dir)
    except OSError:
        return None
    try:
        for part in parts[:-1]:
            nfd = os.open(part, flags_dir, dir_fd=fd)
            os.close(fd)
            fd = nfd
        ffd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except OSError:
        os.close(fd)
        return None
    os.close(fd)
    if not stat.S_ISREG(os.fstat(ffd).st_mode):
        os.close(ffd)
        return None
    return ffd


def _same_as(root: Path, rel: str, original: Path) -> bool:
    """Whether ``root/rel`` (opened as :func:`_open_beneath` does) has ``original``'s bytes."""
    fd = _open_beneath(root, rel)
    if fd is None:
        return False
    with os.fdopen(fd, "rb") as copy, open(original, "rb") as source:
        if os.fstat(copy.fileno()).st_size != os.fstat(source.fileno()).st_size:
            return False
        while True:
            a, b = copy.read(1 << 20), source.read(1 << 20)
            if a != b:
                return False
            if not a:
                return True


def _read_beneath(root: Path, rel: str, limit: int) -> bytes | None:
    """
    Read a regular file ``root/rel`` of at most ``limit`` bytes, opened as
    :func:`_open_beneath` does, so a symlinked component cannot lead the read out of ``root``.
    """
    ffd = _open_beneath(root, rel)
    if ffd is None:
        return None
    try:
        chunks, remaining = [], limit + 1
        while remaining > 0:
            chunk = os.read(ffd, min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        return data if len(data) <= limit else None
    finally:
        os.close(ffd)


def is_poc_file(rel: str) -> bool:
    """Whether a file an agent created is staged for later agents (see ``POC_DIRS``)."""
    *dirs, name = rel.split("/")
    return (
        any(d in POC_DIRS for d in dirs)
        and not any(d in _NO_STAGE_DIRS or d.startswith(".") for d in dirs)
        and not name.startswith(".")
        and os.path.splitext(name)[1] in POC_EXTENSIONS
        and name not in _NO_STAGE_NAMES
        and not _CONFIG_NAME.search(name)
    )


def _walk_files(root: Path) -> Iterable[tuple[str, os.stat_result]]:
    """
    Yield ``(relpath, lstat)`` for regular files under ``root``; never follows symlinks. Skips
    ``SKIP_DIFF_DIRS`` everywhere and ``BUILD_OUTPUT_DIRS`` at project roots.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        project_root = any(_BUILD_MANIFEST.fullmatch(name) for name in filenames)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in SKIP_DIFF_DIRS and not (project_root and d in BUILD_OUTPUT_DIRS)
        ]
        for name in filenames:
            full = Path(dirpath) / name
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                yield str(full.relative_to(root)), st


def _safe_mkdirs(root: Path, rel: str) -> Path | None:
    """Create ``root/rel`` dirs, refusing any existing component that is not a real directory."""
    cur = root
    for part in Path(rel).parts:
        if part in ("", ".", "..") or os.sep in part:
            return None
        cur = cur / part
        try:
            st = os.lstat(cur)
            if not stat.S_ISDIR(st.st_mode):
                return None
        except FileNotFoundError:
            os.mkdir(cur, 0o755)
    return cur


def _force_rmtree(path: Path) -> None:
    """
    Delete a tree an agent may have tampered with (e.g. ``chmod a-w`` on its own directories).
    ``chmod -R`` does not follow symlinks met while recursing, so it cannot touch anything else.
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(st.st_mode):
        os.unlink(path)
        return
    _run(["chmod", "-R", "u+rwX", "--", str(path)], capture_output=True)
    shutil.rmtree(path, ignore_errors=True)
    if os.path.lexists(path):
        logger.warning("could not fully delete %s", path)


def _fresh_dir(path: Path) -> None:
    """Replace whatever is at ``path`` (file, symlink, tree) with an empty real directory."""
    _force_rmtree(path)
    os.mkdir(path, 0o755)


def _copy_tree(src: Path, dst: Path) -> None:
    """``cp -a`` (timestamps preserved so build caches stay valid; symlinks copied as links)."""
    _run(["cp", "-a", "--reflink=auto", f"{src}/.", str(dst)], check=True, capture_output=True)


# ----------------------------------------------------------------------------------------
# Workspace
# ----------------------------------------------------------------------------------------
@dataclass
class Sandbox:
    """One agent's private directory tree: its repository copy, HOME and TMPDIR."""

    name: str
    root: Path

    @property
    def repo(self) -> Path:
        """The agent's copy of the prebuilt checkout (its working directory)."""
        return self.root / "repo"

    @property
    def home(self) -> Path:
        """The agent's private HOME (toolchain and compiler caches)."""
        return self.root / ".home"

    @property
    def tmp(self) -> Path:
        """The agent's private TMPDIR (a short name, see :func:`check_socket_paths`)."""
        return self.root / TMP_DIR


@dataclass
class AgentChanges:
    """What an agent did to its sandbox, computed by the system (not self-reported)."""

    new_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    diff: str = ""


class TriageWorkspace:
    """One evaluation run's base checkout, agent sandboxes and evidence. See module docstring."""

    def __init__(
        self,
        work_dir: Path,
        *,
        secret_paths: Sequence[Path],
        keep_sandboxes: bool,
    ) -> None:
        self.work_dir = Path(work_dir).resolve()
        self.root = Path(tempfile.mkdtemp(prefix=RUN_PREFIX, dir=self.work_dir))  # 0700, unique
        # Taken before anything else is created in the run, and held until cleanup().
        self._lock_fd: int | None = _lock_run(self.root, wait=True)
        self.base = Sandbox("base", self.root / "base")
        self.agents_dir = self.root / AGENTS_DIR
        self.evidence_dir = self.root / "evidence"
        self.secret_paths = [
            p for p in dict.fromkeys(Path(s).resolve() for s in secret_paths) if p.exists()
        ]
        self.keep_sandboxes = keep_sandboxes
        self._index: dict[str, tuple[int, int]] = {}
        self._source: Path | None = None
        self._next_id = 0
        self._id_lock = threading.Lock()  # sandboxes are made from worker threads
        try:
            for d in (self.base.root, self.agents_dir, self.evidence_dir):
                d.mkdir(mode=0o700)
        except BaseException:
            self._release()
            raise

    # --- base ----------------------------------------------------------------------
    def init_base(self, source_repo: Path) -> None:
        """Copy the task checkout into ``base/repo`` and create the base HOME/TMP."""
        for d in (self.base.repo, self.base.home, self.base.tmp):
            d.mkdir(mode=0o755)
        self._source = Path(source_repo)
        _copy_tree(self._source, self.base.repo)

    def seal_base(self) -> list[str]:
        """
        Freeze the base after the build agent ran: kill anything it left running, verify the
        layout, put back the checkout's files it modified or deleted, drop symlinks that could
        point out of a sandbox, and index file metadata for later change detection.

        Returns the checkout files that were put back (paths relative to the repository).
        """
        kill_processes_under(self.base.root)
        for d in (self.base.repo, self.base.home):
            st = os.lstat(d)
            if not stat.S_ISDIR(st.st_mode):
                raise RuntimeError(f"{d} is no longer a real directory")
        _fresh_dir(self.base.tmp)
        _run(
            ["chmod", "-R", "u+rwX", "--", str(self.base.repo), str(self.base.home)],
            capture_output=True,
        )
        restored = self._restore_checkout()
        removed = 0
        for top in (self.base.repo, self.base.home):
            for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
                for name in dirnames + filenames:
                    p = Path(dirpath) / name
                    if p.is_symlink() and not self._link_may_stay(p):
                        p.unlink()
                        removed += 1
        if removed:
            logger.warning(
                "removed %d symlink(s) that could escape a sandbox from the base", removed
            )
        # The build agent's own CLI state must not leak into the agents' HOME copies.
        for entry in self.base.home.glob(".claude*"):
            _force_rmtree(entry)
        _fresh_dir(self.base.repo / TRIAGE_DIR)
        self._index = {rel: (st.st_size, st.st_mtime_ns) for rel, st in _walk_files(self.base.repo)}
        return restored

    def _restore_checkout(self) -> list[str]:
        """
        Put back every file of the task checkout that the build agent modified, deleted or
        replaced, so all agents judge the sponsor's code. Files it added stay (dependencies,
        build output); so do the directories :func:`_walk_files` skips (build output at project
        roots and installed packages, which a build legitimately rewrites; a source directory
        that merely shares such a name is restored). The build agent's version is saved as
        ``evidence/build-restored.diff``. Call only once nothing runs in the base any more.
        """
        if self._source is None:
            return []
        restored: list[str] = []
        chunks: list[str] = []
        for rel, _ in _walk_files(self._source):
            original = self._source / rel
            if _same_as(self.base.repo, rel, original):
                continue
            changed = _read_beneath(self.base.repo, rel, MAX_DIFF_FILE_BYTES)
            if changed is None and not os.path.lexists(self.base.repo / rel):
                chunks.append(f"--- a/{rel}\n+++ /dev/null\n(file deleted)\n")
            else:
                before = _read_beneath(self._source, rel, MAX_DIFF_FILE_BYTES)
                chunks.append(self._diff_one(rel, before, changed))
            self._put_back(rel, original)
            restored.append(rel)
        if restored:
            diff = "".join(chunks)
            if len(diff) > MAX_DIFF_CHARS:
                diff = diff[:MAX_DIFF_CHARS] + "\n... (diff truncated)\n"
            (self.evidence_dir / "build-restored.diff").write_text(diff, encoding="utf-8")
            logger.warning(
                "the build agent modified or deleted %d file(s) of the checkout; put back: %s",
                len(restored),
                ", ".join(restored[:20]) + (" ..." if len(restored) > 20 else ""),
            )
        return sorted(restored)

    def _put_back(self, rel: str, original: Path) -> None:
        """Replace whatever is at ``base/repo/rel`` with a copy of ``original``."""
        *dirs, name = rel.split("/")
        cur = self.base.repo
        for part in dirs:
            cur = cur / part
            try:
                if stat.S_ISDIR(os.lstat(cur).st_mode):
                    continue
                _force_rmtree(cur)  # a file or symlink where the checkout has a directory
            except FileNotFoundError:
                pass
            os.mkdir(cur, 0o755)
        target = cur / name
        _force_rmtree(target)
        # A fresh mtime: tools that compare mtimes (cargo, make) would otherwise keep what the
        # build compiled from the agent's version.
        shutil.copyfile(original, target)
        shutil.copymode(original, target)

    def _link_may_stay(self, link: Path) -> bool:
        """
        Whether a symlink in the base is kept when the base is sealed.

        A relative link must point inside the base, so it points inside each agent's copy too (a
        copy sits one level deeper, so a relative link leaving the base could reach something
        else from there). Only its own target is checked, not a link that target may be: every
        link in the base is checked on its own, so a virtualenv's ``python3 -> python`` stays
        when ``python -> /usr/bin/python3`` does. An absolute link may name a system path, but
        not lead into the work directory (other runs and agents, or the shared base instead of
        the agent's own copy), a secret region or a process-relative path, nor to a directory
        holding one (``/``, ``/var``...), from where the file tools could walk into it; and it
        may not climb with "..", which after a process-relative step leads somewhere else for
        each process.
        """
        target = Path(os.readlink(link))
        if target.is_absolute():
            if ".." in target.parts:
                return False
            forbidden = [self.work_dir, *self.secret_paths, *_PROCESS_RELATIVE]
            return not any(
                _is_within(path, region) or _is_within(region, path)
                for path in (target, target.resolve())
                for region in forbidden
            )
        full = link.parent / target
        # The directory part may run through links and "..", so it is resolved; the last part
        # is the target itself (unless it is "..", which names a directory to resolve too).
        pointed = full.resolve() if full.name == ".." else full.parent.resolve() / full.name
        return _is_within(pointed, self.base.root.resolve())

    # --- agent sandboxes -------------------------------------------------------------
    def make_sandbox(self, name: str) -> Sandbox:
        """
        Create a private copy of the sealed base for one agent. ``name`` labels it in logs; its
        directory gets a short id instead.
        """
        if not _SAFE_NAME.match(name):
            raise ValueError(f"unsafe sandbox name {name!r}")
        with self._id_lock:
            sandbox_id, self._next_id = self._next_id, self._next_id + 1
        if sandbox_id >= 16**_SANDBOX_ID_DIGITS:
            raise RuntimeError("too many sandboxes in one triage run")
        sb = Sandbox(name, self.agents_dir / f"{sandbox_id:0{_SANDBOX_ID_DIGITS}x}")
        logger.debug("sandbox %s is %s", name, sb.root)
        _force_rmtree(sb.root)
        sb.root.mkdir(mode=0o700)
        try:
            _copy_tree(self.base.root, sb.root)
            _fresh_dir(sb.tmp)
            _fresh_dir(sb.repo / TRIAGE_DIR)
        except BaseException:
            _force_rmtree(sb.root)  # a half-made copy would hold its disk space until the run ends
            raise
        return sb

    def finish(self, sb: Sandbox) -> None:
        """Call when the agent's CLI exited: stop anything it left running in its sandbox."""
        kill_processes_under(sb.root)

    def discard(self, sb: Sandbox) -> None:
        """Stop anything left running in an agent's sandbox, then delete it (unless kept)."""
        kill_processes_under(sb.root)
        if not self.keep_sandboxes:
            _force_rmtree(sb.root)

    def cleanup(self) -> None:
        """
        Delete the base and every sandbox, keep the (small) evidence directory, and end the run
        (release its lock).
        """
        try:
            kill_processes_under(self.root)
            if not self.keep_sandboxes:
                _force_rmtree(self.base.root)
                _force_rmtree(self.agents_dir)
        finally:
            self._release()

    def _release(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def changes(self, sb: Sandbox) -> AgentChanges:
        """
        Compare an agent's ``repo`` with the sealed base (call after :meth:`finish`).

        Change detection uses size + mtime, confirmed by content comparison. A same-size,
        mtime-restored edit is not reported here, which is acceptable: the guarantee against
        rigged PoCs is that modified files are never propagated and every PoC is re-run by later
        agents in a clean copy of the base. The diff is an aid for reviewers, not the defence.
        """
        out = AgentChanges()
        seen = set()
        chunks: list[str] = []
        for rel, st in _walk_files(sb.repo):
            seen.add(rel)
            meta = self._index.get(rel)
            if meta is None:
                out.new_files.append(rel)
            elif meta != (st.st_size, st.st_mtime_ns):
                before = _read_beneath(self.base.repo, rel, MAX_DIFF_FILE_BYTES)
                after = _read_beneath(sb.repo, rel, MAX_DIFF_FILE_BYTES)
                if before is not None and before == after:
                    continue  # touched, not changed
                out.modified_files.append(rel)
                chunks.append(self._diff_one(rel, before, after))
        out.deleted_files = sorted(set(self._index) - seen)
        for rel in out.deleted_files:
            chunks.append(f"--- a/{rel}\n+++ /dev/null\n(file deleted)\n")
        out.new_files.sort()
        out.modified_files.sort()
        diff = "".join(chunks)
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + "\n... (diff truncated)\n"
        out.diff = diff
        return out

    @staticmethod
    def _diff_one(rel: str, before: bytes | None, after: bytes | None) -> str:
        if before is None or after is None or b"\0" in before[:8192] or b"\0" in after[:8192]:
            return f"--- a/{rel}\n+++ b/{rel}\n(binary, oversized or unreadable file changed)\n"
        lines = difflib.unified_diff(
            before.decode("utf-8", "replace").splitlines(keepends=True),
            after.decode("utf-8", "replace").splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
        return "".join(lines) + "\n"

    # --- evidence staging ---------------------------------------------------------------
    def collect_evidence(
        self, sb: Sandbox, unit: str, role: str, changes: AgentChanges
    ) -> tuple[Path, list[str]]:
        """
        Copy the PoC files an agent created (see :func:`is_poc_file`) into
        ``evidence/<unit>/<role>/``, record its other new files as not staged, and save the
        system-computed diff of files it modified. Returns ``(evidence dir, staged files)``.
        Files with names outside a plain-ASCII charset are neither staged nor listed (they could
        smuggle text into later prompts, or not even be valid UTF-8).
        """
        if not (_SAFE_NAME.match(unit) and _SAFE_NAME.match(role)):
            raise ValueError(f"unsafe evidence key {unit!r}/{role!r}")
        (self.evidence_dir / unit).mkdir(exist_ok=True)
        dest = self.evidence_dir / unit / role
        _fresh_dir(dest)  # a retry replaces the failed attempt's evidence
        staged: list[str] = []
        not_staged: list[str] = []
        total = 0
        capped = False
        for rel in changes.new_files:
            if not _safe_rel(rel):
                logger.warning("[%s/%s] not staging a file with an unsafe name", unit, role)
                continue
            if not is_poc_file(rel):
                not_staged.append(rel)
                continue
            if len(staged) >= MAX_STAGED_FILES:
                capped = True
                not_staged.append(rel)
                continue
            data = _read_beneath(sb.repo, rel, MAX_STAGED_FILE_BYTES)
            if data is None or total + len(data) > MAX_STAGED_TOTAL_BYTES:
                not_staged.append(rel)
                continue
            parent = _safe_mkdirs(dest, str(Path(rel).parent))
            if parent is None:
                not_staged.append(rel)
                continue
            (parent / Path(rel).name).write_bytes(data)
            staged.append(rel)
            total += len(data)
        if capped:
            logger.warning("[%s/%s] staging capped at %d files", unit, role, MAX_STAGED_FILES)
        (self.evidence_dir / unit / f"{role}.diff").write_text(changes.diff or "", errors="replace")
        (self.evidence_dir / unit / f"{role}.not-staged.json").write_text(
            json.dumps({"files": not_staged[:MAX_NOT_STAGED_LISTED], "count": len(not_staged)})
        )
        return dest, staged

    def stage_into(self, sb: Sandbox, unit: str, roles: Sequence[str]) -> str:
        """
        Place earlier agents' evidence into a fresh sandbox under ``.triage/`` and return a
        (trusted, system-written) note describing what is there. File names in the note are
        JSON-escaped.
        """
        triage = sb.repo / TRIAGE_DIR
        _fresh_dir(triage)
        pocs_root = triage / "pocs"
        diffs_root = triage / "diffs"
        pocs_root.mkdir()
        diffs_root.mkdir()
        lines: list[str] = []
        for role in roles:
            if not (_SAFE_NAME.match(unit) and _SAFE_NAME.match(role)):
                raise ValueError(f"unsafe evidence key {unit!r}/{role!r}")
            src = self.evidence_dir / unit / role
            (pocs_root / role).mkdir()
            files = [rel for rel, _ in _walk_files(src) if _safe_rel(rel)] if src.is_dir() else []
            for rel in files:
                data = _read_beneath(src, rel, MAX_STAGED_FILE_BYTES)
                if data is None:
                    continue
                parent = _safe_mkdirs(pocs_root / role, str(Path(rel).parent))
                if parent is not None:
                    (parent / Path(rel).name).write_bytes(data)
            diff_src = self.evidence_dir / unit / f"{role}.diff"
            diff = diff_src.read_text(errors="replace") if diff_src.is_file() else ""
            (diffs_root / f"{role}.diff").write_text(diff)
            listed = json.dumps(sorted(files)[:30])
            lines.append(
                f"  - {role}: PoC files it created (copied to {TRIAGE_DIR}/pocs/{role}/<original path>): {listed}. "
                f"Other new files, not staged: {self._not_staged_note(unit, role)}. "
                f"Pre-existing files it modified or deleted: "
                f"{'see ' + TRIAGE_DIR + '/diffs/' + role + '.diff' if diff.strip() else 'none'}."
            )
        return "\n".join(lines) if lines else "  (none)"

    def _not_staged_note(self, unit: str, role: str) -> str:
        """The (JSON-escaped) new files of a role that collect_evidence did not stage."""
        src = self.evidence_dir / unit / f"{role}.not-staged.json"
        if not src.is_file():
            return "none"
        try:
            record = json.loads(src.read_text())
            files = sorted(f for f in record["files"] if isinstance(f, str) and _safe_rel(f))
            count = int(record["count"])
        except ValueError, KeyError, TypeError:
            return "unknown"
        if not count:
            return "none"
        listed = json.dumps(files[:30])
        return listed if count <= 30 else f"{listed} and {count - 30} more"

    # --- Claude Code settings ----------------------------------------------------------
    def settings_for(
        self, sb: Sandbox, *, allow_bash: bool, allowed_domains: Sequence[str]
    ) -> dict:
        """
        Build the ``--settings`` for one agent.

        Permission-rule paths use ``//`` for absolute paths (a single leading ``/`` would be
        relative); sandbox filesystem paths are plain absolute paths.
        """
        repo = str(sb.repo.resolve())
        allow = [f"Read(/{repo}/**)", f"Edit(/{repo}/**)"]
        if allow_bash:
            # autoAllowBashIfSandboxed (below) is not enough on its own: under
            # CLAUDE_CODE_SUBPROCESS_ENV_SCRUB the CLI turns that auto-allow off and runs every
            # command in the sandbox, so Bash also needs this explicit allow rule.
            allow.append("Bash")
        deny = [f"Read(/{p}/**)" for p in self.secret_paths] + ["WebFetch", "WebSearch"]
        settings: dict = {"permissions": {"allow": allow, "deny": deny}}
        if allow_bash:
            settings["sandbox"] = {
                "enabled": True,
                "failIfUnavailable": True,
                "autoAllowBashIfSandboxed": True,
                "allowUnsandboxedCommands": False,
                "filesystem": {
                    "allowWrite": [repo, str(sb.home.resolve()), str(sb.tmp.resolve())],
                    # The whole work dir (every run, agent and piece of evidence) is denied;
                    # only this agent's own sandbox is re-allowed below.
                    "denyRead": [str(p) for p in self.secret_paths] + [str(self.work_dir)],
                    "allowRead": [str(sb.root.resolve())],
                },
                "network": {"allowedDomains": list(allowed_domains), "strictAllowlist": True},
            }
        return settings
