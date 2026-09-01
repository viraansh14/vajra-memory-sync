> **This project has moved.** It now ships as the `memory/` component of
> **[vajra-harness](https://github.com/viraansh14/vajra-harness)**, alongside the
> coordination bus it was always meant to run beside. This repository is archived
> and read-only. Development continues there.

# memory-sync

Shared, self-auditing memory for two Claude Code agents running on different machines.

A Claude Code session keeps durable facts as markdown files with YAML frontmatter. Run two
sessions on two machines and you get two divergent brains. This is the convergence layer: a
git-backed sync with a generated index, a placement audit, and a status file that is allowed
to say `DEGRADED` out loud.

It runs on a Windows PC and a macOS laptop against the same code path. Both machines call the
same CLI, so behaviour cannot drift between platforms.

## The problem it actually solves

Naive file sync between two agents fails in three specific ways, and each one is a silent
failure. That is what makes them worth engineering against:

1. **A fact gets filed as machine-local when it is estate-wide.** The other machine never
   learns it. Nothing errors. The index still looks complete, because `lint` only inspects
   what `collect()` returned, and `collect()` never walked the directory the fact was
   misfiled into. So `audit()` walks *every* directory independently and reports files the
   collector never saw. A completeness check that can only see what the collector found is
   not a completeness check.

2. **The transport reports FAIL for reasons unrelated to the memory.** mDNS on this LAN
   resolves intermittently, returning `Name or service not known` minutes apart for a host
   that is up. A single-remote sync reads that as data loss. `sync_repo` instead walks an
   ordered candidate list (mDNS name, then raw IP, then the relay) and reports *which* remote
   carried the run.

3. **A stale status file reads as a fresh pass.** A verdict without a timestamp cannot be
   distinguished from one left behind by a run that has since started failing. Every status
   write carries the time it was written.

## Scopes

Each memory declares a scope in frontmatter, and the scope decides which repo it lives in:

| Directory      | Meaning                                              | Syncs |
|----------------|------------------------------------------------------|-------|
| `_shared/`     | Estate-wide facts, true regardless of machine         | Yes   |
| `_local/`      | Facts about *this* machine only                       | No    |
| `_peer-local/` | A read-only mirror of the other machine's `_local/`   | Pull  |

`_peer-local` is the part that is easy to get wrong. Each machine needs to *know* the peer's
local facts without *owning* them, or you get two writers on one file and a merge conflict on
every sync.

## Commands

```bash
memory-sync index   --root <dir> --machine winpc|macmini   # rebuild MEMORY.md, run all gates
memory-sync lint    --root <dir> --machine winpc|macmini   # frontmatter/scope disagreement
memory-sync migrate --root <dir> --machine winpc|macmini   # flat layout -> scoped layout
memory-sync sync    --root <dir> --machine winpc|macmini   # commit, pull --rebase, push
```

`sync` takes `--remotes peer,peerip,relay` (ordered fallback) and `--status-file` to write a
machine-readable verdict for a scheduler to consume.

## Gates

`index` and `sync` both regenerate the index and evaluate every gate. The verdict is the worst
of them, and it is never rounded up:

- `completeness`: every memory file on disk appears in the generated index
- `lint`: no file disagrees with its own frontmatter
- `placement`: no memory sits in a directory its scope does not permit
- `transport`: which remote carried the run, or that all candidates failed

A run where the memory is intact but the network leg is down reports `DEGRADED`, not `FAIL`
and not `PASS`. That distinction is the whole point: an operator needs to know the difference
between "your memory is broken" and "your memory is fine and the Wi-Fi is not".

## Refusals

`sync` refuses to run against a repo left mid-rebase or on a detached HEAD. Syncing from a
detached HEAD silently commits to no branch, and the work is invisible on the next checkout.
Refusing is louder than succeeding into a void.

## Install

```bash
pip install -e .
python -m pytest -q     # 55 tests
```

Requires Python 3.12+ and `pyyaml`. Git is invoked as a subprocess, not through a binding.

## Scheduling

`scripts/` holds the PowerShell side: `hook-pull.ps1` / `hook-push.ps1` /
`hook-commit-on-write.ps1` for session hooks, and `reconcile.ps1` for the periodic
reconciler. The macOS side runs the same CLI from a launchd job. Both write the same status
file shape, so one dashboard reads both machines.

## Status

55 tests passing. Running in production between two machines since 2026-09-01, converging
209 memories.
