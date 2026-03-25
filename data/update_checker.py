# data/update_checker.py
# Background git update checker — shared between the launch dialog and the
# in-ops status bar indicator.  All git I/O runs on daemon threads; results
# are delivered via Qt signals so callers never need to poll.

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading

from PyQt6.QtCore import QObject, pyqtSignal


class UpdateWorker(QObject):
    """Runs git fetch / pull on daemon threads and signals results.

    Signals:
        check_done(int):         commits behind origin/main.
                                 -1 = network/git error
                                 -2 = dev build (dirty working tree or local commits)
        pull_done(bool, bool):   (success, deps_changed)
    """

    check_done = pyqtSignal(int)
    pull_done  = pyqtSignal(bool, bool)

    def __init__(self):
        super().__init__()
        self._root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def start_check(self):
        threading.Thread(target=self._do_check, daemon=True).start()

    def start_pull(self):
        threading.Thread(target=self._do_pull, daemon=True).start()

    # ── internals ─────────────────────────────────────────────────────────────

    def _do_check(self):
        git_dir = os.path.join(self._root, ".git")
        if not os.path.isdir(git_dir):
            self.check_done.emit(-3)   # not a git install — zip download
            return
        try:
            subprocess.run(
                ["git", "fetch", "--quiet"],
                cwd=self._root, timeout=10,
                capture_output=True, check=True,
            )
            dirty = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self._root, timeout=5,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            ahead = subprocess.run(
                ["git", "rev-list", "origin/main..HEAD", "--count"],
                cwd=self._root, timeout=5,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            if dirty or int(ahead) > 0:
                self.check_done.emit(-2)   # dev build — don't offer update
                return
            r = subprocess.run(
                ["git", "rev-list", "HEAD..origin/main", "--count"],
                cwd=self._root, timeout=5,
                capture_output=True, text=True, check=True,
            )
            self.check_done.emit(int(r.stdout.strip()))
        except Exception:
            self.check_done.emit(-1)

    def _env_hash(self) -> str:
        fname = "storm_windows.yml" if sys.platform == "win32" else "storm_mac.yml"
        path  = os.path.join(self._root, "envs", fname)
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""

    def _do_pull(self):
        try:
            hash_before = self._env_hash()
            subprocess.run(
                ["git", "pull"],
                cwd=self._root, timeout=30,
                capture_output=True, check=True,
            )
            hash_after  = self._env_hash()
            deps_changed = bool(hash_before) and hash_before != hash_after
            self.pull_done.emit(True, deps_changed)
        except Exception:
            self.pull_done.emit(False, False)
