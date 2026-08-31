"""Single command surface for hooks, timers, and humans.

Both machines call this same entry point, so behaviour cannot drift between
platforms. Subcommands: index, lint, migrate, sync.
"""
import argparse
import sys
from pathlib import Path
from . import index as idx
from . import status as st
from . import transport as tp
from .migrate import migrate_dir

COMMANDS = ("index", "lint", "migrate", "sync")
REPOS = ("_shared", "_local")


def _args(argv):
    p = argparse.ArgumentParser(prog="memory-sync")
    p.add_argument("cmd", choices=COMMANDS)
    p.add_argument("--root", required=True)
    p.add_argument("--machine", required=True, choices=["winpc", "macmini"])
    p.add_argument("--status-file")
    p.add_argument("--remote", default="peer")
    p.add_argument("--branch", default="main")
    p.add_argument("--timeout", type=int, default=45)
    return p.parse_args(argv)


def _regenerate(root, machine, status_file, extra_checks=None):
    """Rebuild the index and evaluate every gate. Returns (verdict, checks)."""
    entries = idx.collect(root, machine)
    problems = idx.lint(entries)
    (root / "MEMORY.md").write_text(idx.render(entries), encoding="utf-8")
    checks = dict(extra_checks or {})
    checks["completeness"] = st.completeness(root, entries, machine)
    checks["lint"] = ({"state": "PASS", "detail": "no disagreement"} if not problems
                      else {"state": "FAIL", "detail": "; ".join(problems)})
    v = st.verdict(checks)
    if status_file:
        st.write_status(status_file, {"verdict": v, "checks": checks,
                                      "indexed": len(entries), "machine": machine})
    return v, checks


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print("usage: memory-sync {index|lint|migrate|sync} --root R --machine M")
        return 2
    a = _args(argv)
    root = Path(a.root)

    if a.cmd == "migrate":
        for d, default in (("_shared", "estate"), ("_local", a.machine)):
            if (root / d).is_dir():
                print(d, migrate_dir(root / d, default))
        return 0

    if a.cmd == "lint":
        problems = idx.lint(idx.collect(root, a.machine))
        for pr in problems:
            print(pr)
        return 1 if problems else 0

    extra = {}
    if a.cmd == "sync":
        for r in REPOS:
            repo = root / r
            if not (repo / ".git").is_dir():
                extra["sync:" + r] = {"state": "UNKNOWN", "detail": "not a git repo"}
                continue
            tp.commit_all(repo, "memory: sync")
            pulled = tp.pull_rebase(repo, a.remote, a.branch)
            if pulled["state"] == "PASS":
                extra["sync:" + r] = tp.push_with_retry(repo, a.remote, a.branch)
            else:
                extra["sync:" + r] = pulled

    v, checks = _regenerate(root, a.machine, a.status_file, extra)
    print(v, checks["completeness"]["detail"])
    return 0 if v == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
