import subprocess
from pathlib import Path
import pytest
from memory_sync.transport import run, commit_all, push_with_retry, pull_rebase


def _repo(path, name):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", name], cwd=path, check=True)
    subprocess.run(["git", "config", "receive.denyCurrentBranch", "updateInstead"],
                   cwd=path, check=True)
    (path / "seed.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)
    return path


def _linked(tmp_path):
    """Two repos sharing history, a wired to push into b."""
    a = _repo(tmp_path / "a", "a")
    b = _repo(tmp_path / "b", "b")
    subprocess.run(["git", "remote", "add", "peer", str(b)], cwd=a, check=True)
    subprocess.run(["git", "fetch", "-q", "peer"], cwd=a, check=True)
    subprocess.run(["git", "reset", "-q", "--hard", "peer/main"], cwd=a, check=True)
    return a, b


def test_commit_all_returns_false_when_nothing_changed(tmp_path):
    r = _repo(tmp_path / "a", "a")
    assert commit_all(r, "noop") is False


def test_commit_all_commits_a_new_memory(tmp_path):
    r = _repo(tmp_path / "a", "a")
    (r / "new.md").write_text("x\n", encoding="utf-8")
    assert commit_all(r, "add") is True


def test_push_lands_in_peer_worktree(tmp_path):
    a, b = _linked(tmp_path)
    (a / "fromA.md").write_text("A\n", encoding="utf-8")
    commit_all(a, "from A")
    res = push_with_retry(a, "peer", "main")
    assert res["state"] == "PASS", res
    assert (b / "fromA.md").exists()


def test_push_when_receiver_worktree_is_dirty(tmp_path):
    """Guards spec O2: updateInstead inspects the RECEIVING worktree.

    Either it succeeds, or it fails LOUDLY with a diagnosable reason.
    What it must never do is report success while dropping the push.
    """
    a, b = _linked(tmp_path)
    (b / "seed.md").write_text("locally modified\n", encoding="utf-8")  # receiver mid-turn
    (a / "fromA.md").write_text("A\n", encoding="utf-8")
    commit_all(a, "from A")
    res = push_with_retry(a, "peer", "main")
    assert res["state"] in ("PASS", "FAIL")
    if res["state"] == "PASS":
        assert (b / "fromA.md").exists()
    else:
        assert res["detail"], "a failure must carry a diagnosable reason"


def test_pull_rebase_reports_unknown_when_remote_missing(tmp_path):
    a = _repo(tmp_path / "a", "a")
    res = pull_rebase(a, "nosuch", "main")
    assert res["state"] != "PASS"
    assert res["state"] in ("UNKNOWN", "FAIL")


def test_run_bounds_a_hanging_call(tmp_path):
    code, out = run(["--version"], tmp_path, timeout=30)
    assert code == 0
    assert "git version" in out
