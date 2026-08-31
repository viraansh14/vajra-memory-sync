# VAJRA Shared Memory Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `winpc-claude` and `mac-claude` one shared, continuously-synced memory, with each machine keeping its own local memories out of the other's context.

**Architecture:** Memories live in git repos (`_shared`, `_local`) inside each machine's live Claude memory dir. `MEMORY.md` is never tracked — it is regenerated on each machine from a `scope:` field in each memory's frontmatter, which removes the only file both agents append to and therefore the entire index-conflict class. Sync rides three tiers: direct git push over LAN ssh (primary), Syncthing (fallback), GCS bundle (offsite DR). A shared Python package does index generation, linting, and transport, so both machines run identical logic.

**Tech Stack:** Python 3.12+ in a repo-local venv (`pyyaml`, `pytest`), git 2.54, PowerShell 7 (PC scheduling), launchd (Mac scheduling), `gcloud storage` (bundles).

**Spec:** `docs/superpowers/specs/2026-09-01-shared-memory-design.md`

## Global Constraints

- **Frontmatter `scope:` is the single source of truth for loading.** Directory is storage/transport grouping only. Values: `estate` | `winpc` | `macmini`.
- **`MEMORY.md` is generated and gitignored.** Never commit it, never merge it.
- **A probe that cannot measure reports `UNKNOWN`, never `PASS`.** No success-by-absence.
- **SessionStart must fail open.** A sync fault must never block a session from starting. Hard timeout on every network call.
- **Hook wiring is additive.** `~/.claude/settings.json` already has `SessionStart`, `Stop`, `SessionEnd` entries. Append; never replace the file.
- **ASCII-only in PowerShell sources.** PS 5.1 parses BOM-less files as ANSI; curly quotes break strings.
- **Never auto-discard a memory.** Conflicts keep both bodies for an agent to reconcile.
- **Existing Mac schema is `metadata.sync_scope: shared`** across 167 files and must be migrated, not assumed.
- Python is invoked as `python` on PC and `python3` on Mac; all scripts use the venv interpreter path, never a bare name.

---

### Task 1: Repo skeleton, venv, and frontmatter parser

**Files:**
- Create: `C:\Users\<user>\vajra-memory-sync\memory_sync\__init__.py`
- Create: `C:\Users\<user>\vajra-memory-sync\memory_sync\frontmatter.py`
- Create: `C:\Users\<user>\vajra-memory-sync\tests\test_frontmatter.py`
- Create: `C:\Users\<user>\vajra-memory-sync\requirements.txt`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `parse(text: str) -> tuple[dict, str]` returning `(frontmatter_dict, body)`; `scope_of(meta: dict, default: str) -> str`; `dump(meta: dict, body: str) -> str`

- [ ] **Step 1: Create the venv and requirements**

```bash
cd /c/Users/<user>/vajra-memory-sync
python -m venv .venv
echo "pyyaml==6.0.3" > requirements.txt
echo "pytest==9.1.1" >> requirements.txt
.venv/Scripts/pip install -q -r requirements.txt
echo ".venv/" > .gitignore
echo "MEMORY.md" >> .gitignore
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_frontmatter.py`:

```python
import pytest
from memory_sync.frontmatter import parse, scope_of, dump

RAW = """---
name: capability-os
description: "A thing: with a colon, and \\"quotes\\""
metadata:
  sync_scope: shared
  type: project
---

Body line one.
"""

def test_parse_splits_meta_and_body():
    meta, body = parse(RAW)
    assert meta["name"] == "capability-os"
    assert meta["metadata"]["sync_scope"] == "shared"
    assert body.strip() == "Body line one."

def test_parse_handles_no_frontmatter():
    meta, body = parse("just a body\n")
    assert meta == {}
    assert body.strip() == "just a body"

def test_scope_of_prefers_explicit_scope():
    assert scope_of({"metadata": {"scope": "estate"}}, "winpc") == "estate"

def test_scope_of_falls_back_to_default_when_absent():
    assert scope_of({"metadata": {}}, "winpc") == "winpc"

def test_scope_of_never_guesses_from_sync_scope():
    # migration is Task 2's job; the parser must not silently reinterpret
    assert scope_of({"metadata": {"sync_scope": "shared"}}, "winpc") == "winpc"

def test_dump_roundtrips():
    meta, body = parse(RAW)
    meta["metadata"]["scope"] = "estate"
    out = dump(meta, body)
    meta2, body2 = parse(out)
    assert meta2["metadata"]["scope"] == "estate"
    assert body2.strip() == body.strip()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_frontmatter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory_sync'`

- [ ] **Step 4: Write minimal implementation**

Create `memory_sync/__init__.py` (empty file). Create `memory_sync/frontmatter.py`:

```python
"""Parse and emit the YAML frontmatter Claude Code memories use."""
import yaml

DELIM = "---"


def parse(text):
    """Return (frontmatter_dict, body). Missing frontmatter yields ({}, text)."""
    if not text.startswith(DELIM):
        return {}, text
    parts = text.split(DELIM, 2)
    if len(parts) < 3:
        return {}, text
    meta = yaml.safe_load(parts[1]) or {}
    if not isinstance(meta, dict):
        return {}, text
    return meta, parts[2].lstrip("\n")


def scope_of(meta, default):
    """Resolve loading scope. ONLY metadata.scope is authoritative (spec D7/O3).

    Deliberately ignores legacy sync_scope: migrating that is Task 2's job, and
    silently reinterpreting it here would hide un-migrated files.
    """
    md = meta.get("metadata") or {}
    scope = md.get("scope")
    return scope if scope else default


def dump(meta, body):
    """Serialise back to a memory file."""
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).rstrip("\n")
    return "{d}\n{h}\n{d}\n\n{b}".format(d=DELIM, h=head, b=body.lstrip("\n"))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_frontmatter.py -v`
Expected: PASS, 6 passed

- [ ] **Step 6: Commit**

```bash
git add memory_sync tests requirements.txt .gitignore
git commit -m "feat: frontmatter parser with scope resolution"
```

---

### Task 2: Schema migration (`sync_scope` -> `scope`)

Mac-claude's 167 existing files use `metadata.sync_scope: shared`. The spec requires `metadata.scope: estate`. This task migrates them idempotently.

**Files:**
- Create: `memory_sync/migrate.py`
- Create: `tests/test_migrate.py`

**Interfaces:**
- Consumes: `parse`, `dump` from `memory_sync.frontmatter`
- Produces: `migrate_text(text: str, default_scope: str) -> tuple[str, bool]` returning `(new_text, changed)`; `migrate_dir(path: Path, default_scope: str) -> dict` returning counts

- [ ] **Step 1: Write the failing test**

Create `tests/test_migrate.py`:

```python
from pathlib import Path
from memory_sync.migrate import migrate_text, migrate_dir

SHARED = "---\nname: a\nmetadata:\n  sync_scope: shared\n---\n\nbody\n"
PLAIN = "---\nname: b\nmetadata:\n  type: project\n---\n\nbody\n"
DONE = "---\nname: c\nmetadata:\n  scope: estate\n---\n\nbody\n"

def test_sync_scope_shared_becomes_estate():
    out, changed = migrate_text(SHARED, "macmini")
    assert changed is True
    assert "scope: estate" in out
    assert "sync_scope" not in out

def test_missing_scope_gets_machine_default():
    out, changed = migrate_text(PLAIN, "macmini")
    assert changed is True
    assert "scope: macmini" in out

def test_already_migrated_is_untouched():
    out, changed = migrate_text(DONE, "macmini")
    assert changed is False
    assert out == DONE

def test_migration_is_idempotent():
    once, _ = migrate_text(SHARED, "macmini")
    twice, changed = migrate_text(once, "macmini")
    assert changed is False
    assert once == twice

def test_migrate_dir_counts_and_skips_memory_md(tmp_path):
    (tmp_path / "a.md").write_text(SHARED, encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text(SHARED, encoding="utf-8")
    res = migrate_dir(tmp_path, "macmini")
    assert res["changed"] == 1
    assert res["skipped_index"] == 1
    assert "scope: estate" in (tmp_path / "a.md").read_text(encoding="utf-8")
    assert "sync_scope" in (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_migrate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory_sync.migrate'`

- [ ] **Step 3: Write minimal implementation**

Create `memory_sync/migrate.py`:

```python
"""One-time, idempotent migration of legacy sync_scope to spec scope."""
from pathlib import Path
from .frontmatter import parse, dump

LEGACY_MAP = {"shared": "estate"}


def migrate_text(text, default_scope):
    meta, body = parse(text)
    if not meta:
        return text, False
    md = meta.setdefault("metadata", {}) or {}
    meta["metadata"] = md
    if md.get("scope"):
        return text, False
    legacy = md.pop("sync_scope", None)
    md["scope"] = LEGACY_MAP.get(legacy, default_scope) if legacy else default_scope
    return dump(meta, body), True


def migrate_dir(path, default_scope):
    """Migrate every .md except the generated index."""
    res = {"changed": 0, "unchanged": 0, "skipped_index": 0}
    for f in sorted(Path(path).glob("*.md")):
        if f.name == "MEMORY.md":
            res["skipped_index"] += 1
            continue
        text = f.read_text(encoding="utf-8")
        out, changed = migrate_text(text, default_scope)
        if changed:
            f.write_text(out, encoding="utf-8")
            res["changed"] += 1
        else:
            res["unchanged"] += 1
    return res
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_migrate.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add memory_sync/migrate.py tests/test_migrate.py
git commit -m "feat: idempotent sync_scope -> scope migration"
```

---

### Task 3: Index generation and the scope/directory lint

`MEMORY.md` is generated here. This is the load-bearing mechanism (spec D7) plus the O3 lint.

**Files:**
- Create: `memory_sync/index.py`
- Create: `tests/test_index.py`

**Interfaces:**
- Consumes: `parse`, `scope_of`
- Produces: `collect(root: Path, machine: str) -> list[dict]` (entries with `path`, `name`, `description`, `scope`, `dirname`); `render(entries: list[dict]) -> str`; `lint(entries: list[dict]) -> list[str]` (disagreement messages); `DIR_SCOPE` mapping

- [ ] **Step 1: Write the failing test**

Create `tests/test_index.py`:

```python
from pathlib import Path
from memory_sync.index import collect, render, lint

def _mem(name, scope, desc="hook text"):
    return (
        "---\nname: {n}\ndescription: {d}\nmetadata:\n  scope: {s}\n---\n\nbody\n"
        .format(n=name, d=desc, s=scope)
    )

def _tree(tmp_path):
    (tmp_path / "_shared").mkdir()
    (tmp_path / "_local").mkdir()
    (tmp_path / "_peer-local").mkdir()
    (tmp_path / "_shared" / "cat.md").write_text(_mem("cat", "estate"), encoding="utf-8")
    (tmp_path / "_local" / "gpu.md").write_text(_mem("gpu", "winpc"), encoding="utf-8")
    (tmp_path / "_peer-local" / "batt.md").write_text(_mem("batt", "macmini"), encoding="utf-8")
    return tmp_path

def test_collect_includes_estate_and_own_machine_only(tmp_path):
    entries = collect(_tree(tmp_path), "winpc")
    names = sorted(e["name"] for e in entries)
    assert names == ["cat", "gpu"]

def test_peer_local_never_appears_in_index(tmp_path):
    out = render(collect(_tree(tmp_path), "winpc"))
    assert "batt" not in out
    assert "_peer-local" not in out

def test_mac_view_is_the_mirror_image(tmp_path):
    entries = collect(_tree(tmp_path), "macmini")
    assert sorted(e["name"] for e in entries) == ["batt", "cat"]

def test_render_uses_relative_links_and_hooks(tmp_path):
    out = render(collect(_tree(tmp_path), "winpc"))
    assert "- [cat](_shared/cat.md) - hook text" in out
    assert "- [gpu](_local/gpu.md) - hook text" in out

def test_lint_flags_directory_scope_disagreement(tmp_path):
    t = _tree(tmp_path)
    (t / "_shared" / "wrong.md").write_text(_mem("wrong", "macmini"), encoding="utf-8")
    problems = lint(collect(t, "winpc") + collect(t, "macmini"))
    assert any("wrong" in p and "_shared" in p for p in problems)

def test_lint_is_silent_when_consistent(tmp_path):
    assert lint(collect(_tree(tmp_path), "winpc")) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory_sync.index'`

- [ ] **Step 3: Write minimal implementation**

Create `memory_sync/index.py`:

```python
"""Generate MEMORY.md per machine from frontmatter scope, and lint for drift."""
from pathlib import Path
from .frontmatter import parse, scope_of

# Which scopes each physical directory is expected to hold.
DIR_SCOPE = {
    "_shared": {"estate"},
    "_local": {"winpc", "macmini"},
    "_peer-local": {"winpc", "macmini"},
}

HEADER = (
    "<!-- GENERATED by memory_sync. Do not edit; do not commit. "
    "Regenerate with: memory-sync index -->\n\n"
)


def collect(root, machine):
    """Entries this machine should LOAD: scope == estate, or scope == machine.

    _peer-local is excluded by directory because it is the peer's local set:
    present on disk for backup and cross-review, never loaded (spec D2).
    """
    out = []
    for d in ("_shared", "_local"):
        base = Path(root) / d
        if not base.is_dir():
            continue
        default = "estate" if d == "_shared" else machine
        for f in sorted(base.glob("*.md")):
            if f.name == "MEMORY.md":
                continue
            meta, _ = parse(f.read_text(encoding="utf-8"))
            scope = scope_of(meta, default)
            if scope not in ("estate", machine):
                continue
            out.append({
                "path": "{}/{}".format(d, f.name),
                "dirname": d,
                "name": meta.get("name") or f.stem,
                "description": meta.get("description") or "",
                "scope": scope,
            })
    return out


def render(entries):
    lines = [HEADER]
    for e in sorted(entries, key=lambda x: x["name"]):
        lines.append("- [{n}]({p}) - {d}\n".format(n=e["name"], p=e["path"], d=e["description"]))
    return "".join(lines)


def lint(entries):
    """Flag files whose directory and declared scope disagree (spec O3)."""
    problems = []
    seen = set()
    for e in entries:
        key = e["path"]
        if key in seen:
            continue
        seen.add(key)
        allowed = DIR_SCOPE.get(e["dirname"], set())
        if allowed and e["scope"] not in allowed:
            problems.append(
                "scope/dir disagreement: {p} is in {d} but declares scope: {s}".format(
                    p=e["name"], d=e["dirname"], s=e["scope"])
            )
    return sorted(problems)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_index.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add memory_sync/index.py tests/test_index.py
git commit -m "feat: per-machine index generation + scope/dir lint"
```

---

### Task 4: Completeness gate and status reporting

Enforces the rule that a missing verdict is silent by construction, so counts are compared against an independent denominator.

**Files:**
- Create: `memory_sync/status.py`
- Create: `tests/test_status.py`

**Interfaces:**
- Consumes: `collect`, `lint` from `memory_sync.index`
- Produces: `completeness(root: Path, entries: list, machine: str) -> dict`; `verdict(checks: dict) -> str` returning one of `OK|DEGRADED|BAD|INCOMPLETE`; `write_status(path: Path, payload: dict) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_status.py`:

```python
import json
from pathlib import Path
from memory_sync.status import completeness, verdict, write_status

def _mem(name, scope):
    return "---\nname: {n}\nmetadata:\n  scope: {s}\n---\n\nb\n".format(n=name, s=scope)

def test_completeness_ok_when_every_loadable_file_is_indexed(tmp_path):
    (tmp_path / "_shared").mkdir()
    (tmp_path / "_shared" / "a.md").write_text(_mem("a", "estate"), encoding="utf-8")
    from memory_sync.index import collect
    res = completeness(tmp_path, collect(tmp_path, "winpc"), "winpc")
    assert res["state"] == "PASS"

def test_completeness_fails_when_a_file_is_unindexed(tmp_path):
    (tmp_path / "_shared").mkdir()
    (tmp_path / "_shared" / "a.md").write_text(_mem("a", "estate"), encoding="utf-8")
    (tmp_path / "_shared" / "b.md").write_text(_mem("b", "estate"), encoding="utf-8")
    from memory_sync.index import collect
    entries = collect(tmp_path, "winpc")[:1]  # simulate a dropped entry
    res = completeness(tmp_path, entries, "winpc")
    assert res["state"] == "FAIL"
    assert "1" in res["detail"]

def test_verdict_unknown_never_becomes_ok():
    assert verdict({"a": {"state": "UNKNOWN"}}) == "DEGRADED"

def test_verdict_fail_dominates_unknown():
    assert verdict({"a": {"state": "UNKNOWN"}, "b": {"state": "FAIL"}}) == "BAD"

def test_verdict_all_pass_is_ok():
    assert verdict({"a": {"state": "PASS"}}) == "OK"

def test_write_status_is_valid_json(tmp_path):
    p = tmp_path / "s.json"
    write_status(p, {"verdict": "OK"})
    assert json.loads(p.read_text(encoding="utf-8"))["verdict"] == "OK"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_status.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory_sync.status'`

- [ ] **Step 3: Write minimal implementation**

Create `memory_sync/status.py`:

```python
"""Verdicts, the completeness gate, and status persistence."""
import json
from pathlib import Path
from .frontmatter import parse, scope_of


def completeness(root, entries, machine):
    """Count loadable files on disk against indexed entries.

    An unindexed memory is invisible, and invisibility is silent, so this
    compares against an independent denominator rather than trusting the index.
    """
    denom = 0
    for d, default in (("_shared", "estate"), ("_local", machine)):
        base = Path(root) / d
        if not base.is_dir():
            continue
        for f in base.glob("*.md"):
            if f.name == "MEMORY.md":
                continue
            meta, _ = parse(f.read_text(encoding="utf-8"))
            if scope_of(meta, default) in ("estate", machine):
                denom += 1
    got = len(entries)
    if got == denom:
        return {"state": "PASS", "detail": "{}/{} indexed".format(got, denom)}
    return {"state": "FAIL", "detail": "{}/{} indexed, {} missing".format(got, denom, denom - got)}


def verdict(checks):
    states = [c.get("state") for c in checks.values()]
    if "INCOMPLETE" in states:
        return "INCOMPLETE"
    if "FAIL" in states:
        return "BAD"
    if "UNKNOWN" in states:
        return "DEGRADED"
    return "OK"


def write_status(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_status.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add memory_sync/status.py tests/test_status.py
git commit -m "feat: completeness gate and verdict rules"
```

---

### Task 5: Transport — git tiers with the O2 fix

Implements tier 1 (LAN push/pull) with the corrections mac-claude raised: receiver-dirty tolerance and immediate non-fast-forward retry.

**Files:**
- Create: `memory_sync/transport.py`
- Create: `tests/test_transport.py`

**Interfaces:**
- Consumes: nothing from prior tasks (pure git wrapper)
- Produces: `run(args, cwd, timeout) -> tuple[int, str]`; `commit_all(repo, message) -> bool`; `push_with_retry(repo, remote, branch, attempts=3) -> dict`; `pull_rebase(repo, remote, branch) -> dict`

- [ ] **Step 1: Write the failing test**

Create `tests/test_transport.py`:

```python
import subprocess
from pathlib import Path
import pytest
from memory_sync.transport import run, commit_all, push_with_retry, pull_rebase

def _repo(path, name):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", name], cwd=path, check=True)
    subprocess.run(["git", "config", "receive.denyCurrentBranch", "updateInstead"], cwd=path, check=True)
    (path / "seed.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)
    return path

def test_commit_all_returns_false_when_nothing_changed(tmp_path):
    r = _repo(tmp_path / "a", "a")
    assert commit_all(r, "noop") is False

def test_commit_all_commits_a_new_memory(tmp_path):
    r = _repo(tmp_path / "a", "a")
    (r / "new.md").write_text("x\n", encoding="utf-8")
    assert commit_all(r, "add") is True

def test_push_lands_in_peer_worktree(tmp_path):
    a = _repo(tmp_path / "a", "a")
    b = _repo(tmp_path / "b", "b")
    subprocess.run(["git", "remote", "add", "peer", str(b)], cwd=a, check=True)
    subprocess.run(["git", "fetch", "-q", "peer"], cwd=a, check=True)
    subprocess.run(["git", "reset", "-q", "--hard", "peer/main"], cwd=a, check=True)
    (a / "fromA.md").write_text("A\n", encoding="utf-8")
    commit_all(a, "from A")
    res = push_with_retry(a, "peer", "main")
    assert res["state"] == "PASS"
    assert (b / "fromA.md").exists()

def test_push_succeeds_even_when_receiver_worktree_is_dirty(tmp_path):
    """Guards spec O2: updateInstead inspects the RECEIVING worktree."""
    a = _repo(tmp_path / "a", "a")
    b = _repo(tmp_path / "b", "b")
    subprocess.run(["git", "remote", "add", "peer", str(b)], cwd=a, check=True)
    subprocess.run(["git", "fetch", "-q", "peer"], cwd=a, check=True)
    subprocess.run(["git", "reset", "-q", "--hard", "peer/main"], cwd=a, check=True)
    (b / "dirty.md").write_text("uncommitted\n", encoding="utf-8")  # receiver mid-turn
    (a / "fromA.md").write_text("A\n", encoding="utf-8")
    commit_all(a, "from A")
    res = push_with_retry(a, "peer", "main")
    assert res["state"] in ("PASS", "FAIL")
    if res["state"] == "FAIL":
        assert "dirty" in res["detail"].lower() or "updateInstead" in res["detail"]

def test_pull_rebase_reports_unknown_when_remote_missing(tmp_path):
    a = _repo(tmp_path / "a", "a")
    res = pull_rebase(a, "nosuch", "main")
    assert res["state"] in ("UNKNOWN", "FAIL")
    assert res["state"] != "PASS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_transport.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory_sync.transport'`

- [ ] **Step 3: Write minimal implementation**

Create `memory_sync/transport.py`:

```python
"""Git transport. Every call is bounded; failure is reported, never swallowed."""
import subprocess

DEFAULT_TIMEOUT = 45


def run(args, cwd, timeout=DEFAULT_TIMEOUT):
    try:
        p = subprocess.run(["git"] + list(args), cwd=str(cwd), timeout=timeout,
                           capture_output=True, text=True)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout after {}s".format(timeout)


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
    if "does not appear to be a git repository" in out or "No such remote" in out:
        return {"state": "UNKNOWN", "detail": "peer unreachable: {}".format(out[:200])}
    return {"state": "FAIL", "detail": out[:300]}


def push_with_retry(repo, remote, branch, attempts=3):
    """Push, and on non-fast-forward pull-rebase and retry IMMEDIATELY.

    Deferring to the 30-minute reconciler would leave a known divergence
    standing for up to half an hour (spec O2 minor).
    """
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
            return {"state": r["state"], "detail": "rebase before retry: " + r["detail"]}
        return {"state": "FAIL", "detail": out[:300]}
    return {"state": "FAIL", "detail": "exhausted retries: {}".format(last[:300])}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_transport.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add memory_sync/transport.py tests/test_transport.py
git commit -m "feat: git transport with non-ff retry and receiver-dirty handling"
```

---

### Task 6: CLI entry point

Single command surface both machines and all hooks call, so behaviour cannot drift between platforms.

**Files:**
- Create: `memory_sync/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above
- Produces: `main(argv: list[str]) -> int`; subcommands `index`, `lint`, `migrate`, `sync`; `--root`, `--machine`, `--status-file`, `--timeout` flags

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
import json
from pathlib import Path
from memory_sync.cli import main

def _mem(name, scope):
    return "---\nname: {n}\ndescription: d\nmetadata:\n  scope: {s}\n---\n\nb\n".format(n=name, s=scope)

def _tree(tmp_path):
    (tmp_path / "_shared").mkdir()
    (tmp_path / "_shared" / "a.md").write_text(_mem("a", "estate"), encoding="utf-8")
    return tmp_path

def test_index_writes_memory_md(tmp_path):
    t = _tree(tmp_path)
    rc = main(["index", "--root", str(t), "--machine", "winpc"])
    assert rc == 0
    assert "- [a](_shared/a.md)" in (t / "MEMORY.md").read_text(encoding="utf-8")

def test_index_writes_status_with_ok_verdict(tmp_path):
    t = _tree(tmp_path)
    s = tmp_path / "status.json"
    main(["index", "--root", str(t), "--machine", "winpc", "--status-file", str(s)])
    assert json.loads(s.read_text(encoding="utf-8"))["verdict"] == "OK"

def test_lint_returns_nonzero_on_disagreement(tmp_path):
    t = _tree(tmp_path)
    (t / "_shared" / "bad.md").write_text(_mem("bad", "macmini"), encoding="utf-8")
    assert main(["lint", "--root", str(t), "--machine", "macmini"]) != 0

def test_unknown_subcommand_is_an_error(tmp_path):
    assert main(["nope"]) != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory_sync.cli'`

- [ ] **Step 3: Write minimal implementation**

Create `memory_sync/cli.py`:

```python
"""Single command surface for hooks, timers, and humans."""
import argparse
import sys
from pathlib import Path
from . import index as idx
from . import status as st
from .migrate import migrate_dir


def _args(argv):
    p = argparse.ArgumentParser(prog="memory-sync")
    p.add_argument("cmd", choices=["index", "lint", "migrate", "sync"])
    p.add_argument("--root", required=True)
    p.add_argument("--machine", required=True, choices=["winpc", "macmini"])
    p.add_argument("--status-file")
    p.add_argument("--timeout", type=int, default=45)
    return p.parse_args(argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("index", "lint", "migrate", "sync"):
        print("usage: memory-sync {index|lint|migrate|sync} --root R --machine M")
        return 2
    a = _args(argv)
    root = Path(a.root)

    if a.cmd == "migrate":
        for d, default in (("_shared", "estate"), ("_local", a.machine)):
            if (root / d).is_dir():
                print(d, migrate_dir(root / d, default))
        return 0

    entries = idx.collect(root, a.machine)
    problems = idx.lint(entries)

    if a.cmd == "lint":
        for pr in problems:
            print(pr)
        return 1 if problems else 0

    (root / "MEMORY.md").write_text(idx.render(entries), encoding="utf-8")
    checks = {
        "completeness": st.completeness(root, entries, a.machine),
        "lint": ({"state": "PASS", "detail": "no disagreement"} if not problems
                 else {"state": "FAIL", "detail": "; ".join(problems)}),
    }
    v = st.verdict(checks)
    if a.status_file:
        st.write_status(a.status_file, {"verdict": v, "checks": checks,
                                        "indexed": len(entries), "machine": a.machine})
    print(v, checks["completeness"]["detail"])
    return 0 if v == "OK" else 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -v && .venv/Scripts/python -m pytest tests -v`
Expected: PASS, all tests green

- [ ] **Step 5: Commit**

```bash
git add memory_sync/cli.py tests/test_cli.py
git commit -m "feat: memory-sync CLI"
```

---

### Task 7: PC store setup — clone, junctions, `_local`

First task that touches live memory. Backup first; every step reversible.

**Files:**
- Create: `scripts/setup-pc.ps1`
- Modify: none yet (hooks come in Task 8)

**Interfaces:**
- Consumes: `memory-sync migrate`, `memory-sync index` from Task 6
- Produces: `C:\Users\<user>\.claude\projects\C--WINDOWS-system32\memory\{_shared,_local}` as git repos; junctions from the other three project scopes

- [ ] **Step 1: Back up both machines' memory dirs**

```bash
STAMP=$(date +%Y%m%d-%H%M)
for d in /c/Users/<user>/.claude/projects/*/memory; do
  cp -r "$d" "${d}.bak-${STAMP}"
done
ls -d /c/Users/<user>/.claude/projects/*/memory.bak-* | wc -l   # expect 4
```

- [ ] **Step 2: Clone the Mac's existing `_shared` over LAN**

```bash
CANON=/c/Users/<user>/.claude/projects/C--WINDOWS-system32/memory
git clone ssh://viraansh@<peer-lan-ip>/Users/<user>/.claude/projects/-Users-viraansh/memory/_shared "$CANON/_shared"
ls "$CANON/_shared"/*.md | wc -l    # expect 167
```

- [ ] **Step 3: Verify the clone matches the source before trusting it**

```bash
git -C "$CANON/_shared" rev-parse --short HEAD
# Expected: matches `git -C <mac path> rev-parse --short HEAD` (ccdc870 or later)
```

- [ ] **Step 4: Create `_local` and move PC-local memories into it**

```bash
CANON=/c/Users/<user>/.claude/projects/C--WINDOWS-system32/memory
mkdir -p "$CANON/_local"
cd "$CANON"
for f in *.md; do [ "$f" = "MEMORY.md" ] || mv "$f" _local/; done
git -C "$CANON/_local" init -q -b main
git -C "$CANON/_local" config receive.denyCurrentBranch updateInstead
git -C "$CANON/_local" add -A
git -C "$CANON/_local" commit -qm "seed: winpc local memories"
ls "$CANON/_local"/*.md | wc -l   # expect 25 (26 minus MEMORY.md)
```

- [ ] **Step 5: Migrate schema and generate the index**

```bash
cd /c/Users/<user>/vajra-memory-sync
CANON=/c/Users/<user>/.claude/projects/C--WINDOWS-system32/memory
.venv/Scripts/python -m memory_sync.cli migrate --root "$CANON" --machine winpc
.venv/Scripts/python -m memory_sync.cli index --root "$CANON" --machine winpc \
  --status-file /c/Users/<user>/.local/memory-sync/memory-sync.json
```

Expected: prints `OK <n>/<n> indexed`

- [ ] **Step 6: Verify falsifier 5 — no macmini entry in the PC index**

```bash
CANON=/c/Users/<user>/.claude/projects/C--WINDOWS-system32/memory
grep -c "_peer-local" "$CANON/MEMORY.md" || echo "0 peer-local entries (correct)"
.venv/Scripts/python -m memory_sync.cli lint --root "$CANON" --machine winpc
```

Expected: zero `_peer-local` references; lint exits 0

- [ ] **Step 7: Add junctions from the other three project scopes**

```powershell
$canon = "C:\Users\<user>\.claude\projects\C--WINDOWS-system32\memory"
foreach ($p in @("C--Users-viraa","C--VAJRA","C--")) {
  $dst = "C:\Users\<user>\.claude\projects\$p\memory"
  foreach ($sub in @("_shared","_local")) {
    if (-not (Test-Path "$dst\$sub")) { cmd /c mklink /J "$dst\$sub" "$canon\$sub" | Out-Null }
  }
}
Get-ChildItem "C:\Users\<user>\.claude\projects\C--VAJRA\memory" | Where-Object { $_.LinkType }
```

Expected: `_shared` and `_local` listed with LinkType `Junction`

- [ ] **Step 8: Commit the setup script**

```bash
cd /c/Users/<user>/vajra-memory-sync
git add scripts/setup-pc.ps1
git commit -m "feat: PC store setup - clone, _local, junctions"
```

---

### Task 8: Hook wiring (additive)

**Files:**
- Modify: `C:\Users\<user>\.claude\settings.json` (append to existing `SessionStart`, `Stop`; add `PostToolUse`)
- Create: `scripts/hook-pull.ps1`, `scripts/hook-push.ps1`, `scripts/hook-commit-on-write.ps1`

**Interfaces:**
- Consumes: `memory-sync index`, `transport.commit_all`, `push_with_retry`
- Produces: three hook scripts, each exiting 0 unconditionally so a sync fault can never block a session

- [ ] **Step 1: Snapshot settings.json before touching it**

```bash
cp /c/Users/<user>/.claude/settings.json /c/Users/<user>/.claude/settings.json.bak-$(date +%Y%m%d-%H%M)
python -c "import json;d=json.load(open(r'C:\Users\<user>\.claude\settings.json'));print(sorted(d.get('hooks',{}).keys()))"
```

Expected: prints existing hook keys including `SessionStart`, `Stop`, `SessionEnd` — these must survive.

- [ ] **Step 2: Write `scripts/hook-pull.ps1`**

```powershell
# SessionStart: refresh memory then regenerate the index. MUST fail open.
$ErrorActionPreference = "SilentlyContinue"
$canon = "C:\Users\<user>\.claude\projects\C--WINDOWS-system32\memory"
$py    = "C:\Users\<user>\vajra-memory-sync\.venv\Scripts\python.exe"
$job = Start-Job { param($c) git -C "$c\_shared" pull --rebase peer main 2>&1 } -ArgumentList $canon
if (Wait-Job $job -Timeout 20) { Receive-Job $job | Out-Null } else { Stop-Job $job -Force }
Remove-Job $job -Force
& $py -m memory_sync.cli index --root $canon --machine winpc `
      --status-file "C:\Users\<user>\.local\memory-sync\memory-sync.json" | Out-Null
exit 0
```

- [ ] **Step 3: Verify the hook fails open when the peer is unreachable**

```powershell
# Simulate: point 'peer' at a dead host, run the hook, confirm exit 0 and a fresh index
$canon = "C:\Users\<user>\.claude\projects\C--WINDOWS-system32\memory"
git -C "$canon\_shared" remote add peerdead ssh://nosuch.invalid/x 2>$null
powershell -File C:\Users\<user>\vajra-memory-sync\scripts\hook-pull.ps1
"exit=$LASTEXITCODE"
```

Expected: `exit=0`, and `MEMORY.md` still regenerated

- [ ] **Step 4: Write `scripts/hook-push.ps1` and `scripts/hook-commit-on-write.ps1`**

```powershell
# hook-push.ps1 - Stop/SessionEnd: commit and push both repos, best effort.
$ErrorActionPreference = "SilentlyContinue"
$canon = "C:\Users\<user>\.claude\projects\C--WINDOWS-system32\memory"
$py    = "C:\Users\<user>\vajra-memory-sync\.venv\Scripts\python.exe"
& $py -m memory_sync.cli sync --root $canon --machine winpc `
      --status-file "C:\Users\<user>\.local\memory-sync\memory-sync.json" | Out-Null
exit 0
```

```powershell
# hook-commit-on-write.ps1 - PostToolUse: keep the worktree clean so an
# inbound peer push is never refused by updateInstead (spec O2).
$ErrorActionPreference = "SilentlyContinue"
$canon = "C:\Users\<user>\.claude\projects\C--WINDOWS-system32\memory"
foreach ($r in @("_shared","_local")) {
  if (Test-Path "$canon\$r\.git") {
    git -C "$canon\$r" add -A 2>$null
    git -C "$canon\$r" diff --cached --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { git -C "$canon\$r" commit -qm "memory: autocommit on write" 2>$null }
  }
}
exit 0
```

- [ ] **Step 5: Append hooks to settings.json without clobbering**

```python
import json, pathlib
p = pathlib.Path(r"C:\Users\<user>\.claude\settings.json")
d = json.loads(p.read_text(encoding="utf-8"))
h = d.setdefault("hooks", {})
S = r"C:\Users\<user>\vajra-memory-sync\scripts"
def add(event, script, matcher=None):
    entry = {"type": "command",
             "command": f'powershell -NonInteractive -ExecutionPolicy Bypass -File "{S}\\{script}"'}
    block = {"hooks": [entry]}
    if matcher:
        block["matcher"] = matcher
    lst = h.setdefault(event, [])
    if not any(script in json.dumps(x) for x in lst):
        lst.append(block)
add("SessionStart", "hook-pull.ps1")
add("Stop", "hook-push.ps1")
add("PostToolUse", "hook-commit-on-write.ps1", matcher="Write|Edit")
p.write_text(json.dumps(d, indent=2), encoding="utf-8")
print(sorted(h.keys()))
```

- [ ] **Step 6: Verify settings.json is still valid and nothing was lost**

```bash
python -c "import json;d=json.load(open(r'C:\Users\<user>\.claude\settings.json'));print('SessionStart',len(d['hooks']['SessionStart']),'Stop',len(d['hooks']['Stop']),'SessionEnd',len(d['hooks'].get('SessionEnd',[])))"
diff <(python -c "import json;print(sorted(json.load(open(r'C:\Users\<user>\.claude\settings.json'))['hooks'].keys()))") <(python -c "import json;print(sorted(json.load(open(r'C:\Users\<user>\.claude\settings.json.bak-'+__import__('glob').glob(r'C:\Users\<user>\.claude\settings.json.bak-*')[-1][-13:]))['hooks'].keys()))") || true
```

Expected: pre-existing entries still present, counts increased by exactly 1 where added

- [ ] **Step 7: Commit**

```bash
cd /c/Users/<user>/vajra-memory-sync
git add scripts/
git commit -m "feat: additive hook wiring for pull/push/commit-on-write"
```

---

### Task 9: Timers and the falsifier suite

**Files:**
- Create: `scripts/reconcile.ps1`
- Create: `tests/falsifiers/run_falsifiers.py`

**Interfaces:**
- Consumes: the CLI and transport modules
- Produces: Scheduled Task `VajraMemorySync` (30 min); an executable falsifier report

- [ ] **Step 1: Write the reconciler**

```powershell
# reconcile.ps1 - the convergence guarantee. Runs every 30 min regardless of sessions.
$ErrorActionPreference = "SilentlyContinue"
$canon = "C:\Users\<user>\.claude\projects\C--WINDOWS-system32\memory"
$py    = "C:\Users\<user>\vajra-memory-sync\.venv\Scripts\python.exe"
foreach ($r in @("_shared","_local")) {
  if (Test-Path "$canon\$r\.git") {
    git -C "$canon\$r" add -A 2>$null
    git -C "$canon\$r" diff --cached --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { git -C "$canon\$r" commit -qm "memory: reconcile" 2>$null }
    git -C "$canon\$r" pull --rebase peer main 2>$null
    git -C "$canon\$r" push peer main 2>$null
  }
}
& $py -m memory_sync.cli index --root $canon --machine winpc `
      --status-file "C:\Users\<user>\.local\memory-sync\memory-sync.json" | Out-Null
```

- [ ] **Step 2: Register the 30-minute task**

```powershell
$act = New-ScheduledTaskAction -Execute "C:\Program Files\PowerShell\7\pwsh.exe" `
  -Argument '-NonInteractive -ExecutionPolicy Bypass -File "C:\Users\<user>\vajra-memory-sync\scripts\reconcile.ps1"'
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) -RepetitionInterval (New-TimeSpan -Minutes 30)
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName "VajraMemorySync" -Action $act -Trigger $trg -Settings $set -Force
Get-ScheduledTask -TaskName "VajraMemorySync" | Select-Object TaskName,State
```

Expected: `VajraMemorySync  Ready`

- [ ] **Step 3: Run the falsifier suite and record results**

Each falsifier from spec §8 must be **executed**, not reasoned about. Falsifier 8 is already PASSED (recorded in the spec). Run 1-7, 9, 10:

```bash
cd /c/Users/<user>/vajra-memory-sync
.venv/Scripts/python tests/falsifiers/run_falsifiers.py --machine winpc --peer <peer-lan-ip>
```

Expected: a table of falsifier -> PASS/FAIL. **Any FAIL blocks completion**; report the closest attempt and exactly which check failed.

- [ ] **Step 4: Commit**

```bash
git add scripts/reconcile.ps1 tests/falsifiers/
git commit -m "feat: 30-min reconciler and executable falsifier suite"
```

---

### Task 10: Mac half and backfill classification

Executed by `mac-claude` on signal; it has pre-staged `_shared`, the GCS bundle, and launchd.

**Files:**
- Create (Mac): `~/vajra-memory-sync` clone of this repo
- Modify (Mac): `~/.claude/settings.json`, launchd plist

**Interfaces:**
- Consumes: the same CLI, with `--machine macmini`
- Produces: symmetric setup; `_peer-local` on both sides

- [ ] **Step 1: Send mac-claude the ordered steps**

Mac steps, in order: clone this tooling repo; create venv and `pip install -r requirements.txt` (Mac has **neither pytest nor pyyaml**); run `migrate --machine macmini`; move its 24 locals into `_local`; add `peer` remotes both directions; set `receive.denyCurrentBranch=updateInstead`; wire the three hooks additively; install the launchd 30-min reconciler; demote the Syncthing folder to fallback.

- [ ] **Step 2: Establish `_peer-local` in both directions**

```bash
# PC pulls the Mac's _local into _peer-local
CANON=/c/Users/<user>/.claude/projects/C--WINDOWS-system32/memory
git clone ssh://viraansh@<peer-lan-ip>/Users/<user>/.claude/projects/-Users-viraansh/memory/_local "$CANON/_peer-local"
ls "$CANON/_peer-local"/*.md | wc -l   # expect 24
```

- [ ] **Step 3: Verify falsifier 2 — `_peer-local` is present but never loaded**

```bash
CANON=/c/Users/<user>/.claude/projects/C--WINDOWS-system32/memory
grep -c "_peer-local" "$CANON/MEMORY.md" || echo "0 (correct: synced, not loaded)"
```

Expected: `0`

- [ ] **Step 4: Cross-review classification**

Each agent reviews the other's calls against `RULE.md`, hunting specifically for facts wrongly marked machine-local (the dangerous direction — a wrongly-local memory is invisible to the peer forever). Produce a list of proposed reclassifications for operator sign-off. Do **not** apply reclassifications unilaterally.

- [ ] **Step 5: Operator signs off on the estate set**

Present the final estate list. Only after sign-off, commit any reclassifications.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: mac half wired, peer-local established, classification cross-reviewed"
```

---

## Self-Review

**Spec coverage:** D1 Task 3/7; D2 Task 10 (`_peer-local`); D3 Tasks 5/7; D4 Task 5; D5 Task 10 step 1; D6 already PASSED (falsifier 8, spec-recorded); D7 Task 3; D8 not implemented anywhere by design. O1 Task 10; O2 Tasks 5 and 8 (commit-on-write + non-ff retry); O3 Tasks 3 and 6 (lint). §6 completeness gate Task 4. §7 backfill Task 10.

**Known gap:** the spec's toast-on-state-change (§6) is not yet its own task; `VajraPeerLiveness` already provides local alerting, and `memory-sync.json` is written for it to read. If state-change toasting is wanted for memory specifically, it is a Task 11.

**Type consistency:** `parse`/`scope_of`/`dump` (Task 1) used unchanged in Tasks 2-4. `collect`/`render`/`lint` (Task 3) used in Tasks 4 and 6. `commit_all`/`push_with_retry`/`pull_rebase` (Task 5) used in Tasks 8-9. `--root`/`--machine`/`--status-file` consistent across every invocation.

**Ordering:** Tasks 1-6 are pure and testable offline. Task 7 is the first to touch live memory and is preceded by a full backup. Tasks 8-9 are reversible config. Task 10 requires the peer.
