"""UpdateWorker._do_check against real throwaway git repositories."""
import shutil
import subprocess

import pytest

pytest.importorskip("PyQt6")

from data.update_checker import UpdateWorker

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd), "PATH": _PATH})


_PATH = __import__("os").environ.get("PATH", "")


def _commit(repo, name):
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)


@pytest.fixture
def repos(tmp_path):
    """origin (bare) with main and field-test, a clone of it, and a second
    clone used to push new commits."""
    origin, seed, clone = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "clone"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", "-q", str(origin), str(seed))
    _commit(seed, "a")
    _git(seed, "push", "-q", "origin", "HEAD:main")
    _git(seed, "push", "-q", "origin", "HEAD:field-test")
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    return seed, clone


def _check(root):
    worker = UpdateWorker()
    worker._root = str(root)
    got = []
    worker.check_done.connect(got.append)
    worker._do_check()
    return got[-1]


def test_up_to_date_on_main(repos):
    _seed, clone = repos
    assert _check(clone) == 0


def test_counts_commits_behind_main(repos):
    seed, clone = repos
    _commit(seed, "b")
    _git(seed, "push", "-q", "origin", "HEAD:main")
    assert _check(clone) == 1


def test_compares_with_the_tracked_branch(repos):
    seed, clone = repos
    _git(clone, "checkout", "-q", "-b", "field-test", "--track", "origin/field-test")
    _commit(seed, "b")
    _git(seed, "push", "-q", "origin", "HEAD:main")         # main moves on...
    assert _check(clone) == 0                                # ...but field-test didn't
    _commit(seed, "c")
    _git(seed, "push", "-q", "origin", "HEAD:field-test")
    assert _check(clone) == 2                                # b and c, on field-test


def test_local_commits_are_a_dev_build(repos):
    _seed, clone = repos
    _commit(clone, "local")
    assert _check(clone) == -2


def test_not_a_git_install(tmp_path):
    assert _check(tmp_path) == -3
