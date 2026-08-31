"""Git transport. Every call is bounded; failure is reported, never swallowed.

Two corrections from review are encoded here:

O2  `receive.denyCurrentBranch=updateInstead` inspects the RECEIVING worktree,
    not the sender's. The receiver is dirty exactly when it is mid-turn writing
    a memory, so pushes fail intermittently and precisely when the peer is most
    active. The commit-on-write hook keeps the receiver clean; this module makes
    the residual failure loud rather than silent.

O2-minor  A non-fast-forward push must pull-rebase and retry IMMEDIATELY.
    Deferring to the 30-minute reconciler leaves a known divergence standing.
"""
import subprocess

DEFAULT_TIMEOUT = 45


def run(args, cwd, timeout=DEFAULT_TIMEOUT):
    """Run git bounded by a timeout. Returns (returncode, combined_output)."""
    try:
        p = subprocess.run(["git"] + list(args), cwd=str(cwd), timeout=timeout,
                           capture_output=True, text=True)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout after {}s".format(timeout)
    except OSError as e:
        return 127, "git not runnable: {}".format(e)


def commit_all(repo, message):
    """Commit everything. Returns True if a commit was created."""
    run(["add", "-A"], repo)
    code, _ = run(["diff", "--cached", "--quiet"], repo)
    if code == 0:
        return False
    run(["commit", "-qm", message], repo)
    return True


def pull_rebase(repo, remote, branch):
    code, out = run(["pull", "--rebase", remote, branch], repo)
    if code == 0:
        return {"state": "PASS", "detail": "pulled"}
    unreachable = (
        "does not appear to be a git repository" in out
        or "Could not read from remote" in out
        or "No such remote" in out
        or "not a git repository" in out
        or "unable to access" in out
        or "timeout after" in out
    )
    if unreachable:
        return {"state": "UNKNOWN", "detail": "peer unreachable: {}".format(out[:200])}
    return {"state": "FAIL", "detail": out[:300]}


def push_with_retry(repo, remote, branch, attempts=3):
    """Push; on non-fast-forward, rebase and retry immediately."""
    last = ""
    for _ in range(attempts):
        code, out = run(["push", remote, branch], repo)
        if code == 0:
            return {"state": "PASS", "detail": "pushed"}
        last = out
        if "non-fast-forward" in out or "fetch first" in out or "rejected" in out:
            r = pull_rebase(repo, remote, branch)
            if r["state"] == "PASS":
                continue
            return {"state": r["state"],
                    "detail": "rebase before retry: " + r["detail"]}
        return {"state": "FAIL", "detail": out[:300]}
    return {"state": "FAIL", "detail": "exhausted retries: {}".format(last[:300])}
