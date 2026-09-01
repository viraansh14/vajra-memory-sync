# VAJRA shared memory: design

- **Date:** 2026-09-01
- **Status:** approved for planning; not yet implemented
- **Authors:** winpc-claude, reconciled with mac-claude's independently-built design
- **Operator decisions:** shared-core model; git transport; hooks + timer; per-agent classification with cross-review; synthesis over both designs

## 1. Problem

Two Claude Code agents (`winpc-claude` on Windows, `mac-claude` on macOS) accumulate
memories independently. Neither can see what the other learned. A fact established on one
machine is invisible to the other until a human repeats it.

Claude Code keys auto-memory by **working directory**, so the two stores can never align
on their own: the PC's live store is `~/.claude/projects/C--WINDOWS-system32/memory/`,
the Mac's is `~/.claude/projects/-Users-<user>/memory/`. Different keys, different
contents, no shared history. Any design must work inside that constraint rather than
fight it.

## 2. Current state (measured 2026-09-01 01:37-02:05 IST)

**Mac** — `~/.claude/projects/-Users-<user>/memory/`:

| Location | Files | Notes |
| --- | ---: | --- |
| `_shared/` | 167 | **already a git repo** (`_shared/.git`); Syncthing folder `vajra-claude-memory` |
| top level `*.md` | 24 | machine-local, left in place |
| total | 191 | |

**PC** — memory is fragmented across four project scopes, none under git:

| Project scope | Files | Notes |
| --- | ---: | --- |
| `C--Users-viraa` | 190 | largest, but `MEMORY.md` stale since 2026-08-05 |
| `C--WINDOWS-system32` | 26 | **the live store**; `MEMORY.md` current |
| `C--VAJRA` | 17 | |
| `C--` | 7 | |
| total | 240 | |

**Transport, as found (and since resolved):** Syncthing runs on both. The Mac had the PC
device (`4NVUDD3-…`) set to `paused: true`, which was the sole reason the link was down —
the PC side was healthy throughout (process up, `:22000` listening, discovery enabled).
Mac-claude had diagnosed this as "the PC's Syncthing is offline", which was incorrect;
each side was blaming the other while the cause was one pause flag. **Unpaused
2026-09-01 ~02:20; link now `connected=True`.** This also unfroze the Obsidian vault and
dotfiles folders, which had been stalled for the same reason.

**Known transport defect:** the live Syncthing connection is
`relay://64.235.45.16:443` — a *public Syncthing relay* — despite both machines sitting
on `<your-lan>/24` with the PC's LAN address statically configured, discovery succeeding
and `localAnnounceEnabled=true`. The direct LAN dial is failing, most plausibly macOS
Local Network Privacy blocking Syncthing, or the PC firewall on `:22000`. Traffic is
end-to-end encrypted so this is not a disclosure, but the fallback path currently takes a
third-party detour instead of the ~6ms LAN hop. Low urgency **only because** Syncthing is
demoted to fallback (D5); it must be fixed before Syncthing is ever relied on as primary.

**Not a viable spine:** the relay `<relay-host>` was unreachable for 29 hours
(2026-08-30 15:33 → 2026-08-31 20:54) with a full disk. It must not be on the critical
path for memory.

## 3. Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| D1 | Shared core + machine-local | Prevents both machines loading ~400 memories, most irrelevant to the reader |
| D2 | Machine-local is **synced but not loaded** | Gives local memories git backup and makes cross-review possible; scope controls *loading*, not *transmission* |
| D3 | git is the store | Real 3-way merge and history; both designs already converged on this |
| D4 | Direct git push over LAN is the primary transport | Real merges instead of `.sync-conflict-*` files; 6-14ms vs the relay's europe-west2 hop |
| D5 | Syncthing is the **fallback** transport | Already running and proven; carries the repo when direct push is not possible |
| D6 | GCS bundle is the offsite backup | `gs://<your-bucket>`, the operator's own project; survives both machines dying; no third party |
| D7 | **`MEMORY.md` is generated, never tracked** | It is the highest-churn file and the one both agents always append to; generating it makes its conflict class structurally impossible |
| D8 | Relay is **not** on the critical path | It failed silently for 29 hours |

## 4. Architecture

### 4.1 Store layout

One git repo, `vajra-claude-memory`, whose working tree is the `_shared/` directory
inside each machine's live memory dir. This is the layout the Mac already has.

```
<live memory dir>/
  MEMORY.md              <- GENERATED per machine, gitignored, never synced
  _shared/               <- git repo: estate-wide memories (loaded)
    .git/
    RULE.md              <- the classification rule
    <shared>.md
  _local/                <- git repo: THIS machine's memories (loaded)
    .git/
    <local>.md
  _peer-local/           <- read-only mirror of the peer's _local (NOT loaded)
    <peer local>.md
```

Because the index uses relative links, a memory's directory is irrelevant to whether it
can be read — only whether the generated index points at it. That is what lets
`_peer-local` be present on disk yet absent from context.

**Single source of truth (mac-claude O3).** Scope is expressed in two places — the
directory and the frontmatter — and they can disagree: a file may sit in `_shared/` while
declaring `scope: macmini`. To remove the ambiguity:

> **Frontmatter `scope:` is authoritative for loading.** The generated index filters on
> it and on nothing else. The directory is *only* physical storage and transport
> grouping, never a loading decision.

The completeness gate (§6) additionally **lints for disagreement** between a file's
directory and its declared scope, so drift is caught by construction rather than noticed
later.

Local memories stay at top level, outside the shared repo, matching what the Mac already
did. D2 (local is synced but not loaded) is satisfied by a **second repo per machine**
rather than by mixing scopes in one tree, which keeps the shared history clean and lets
the two be pushed independently:

```
<live memory dir>/_local/        <- this machine's local memories, a git repo
<live memory dir>/_peer-local/   <- READ-ONLY clone of the PEER's local repo
```

Each machine pushes `_local` to the peer, where it lands as `_peer-local`. That makes
local memories readable for cross-review (§7 step 4) and gives them git backup, while
the generated index never lists anything under `_peer-local` — so they are **synced but
never loaded**, exactly as D2 requires. `_peer-local` is never written locally; it is a
mirror, and conflicts there are resolved by taking the peer's version.

Both repos use the same transport tiers (§4.4).

### 4.2 Scope and the generated index

Every memory declares its scope in frontmatter:

```yaml
metadata:
  type: project
  scope: estate        # estate | winpc | macmini
```

`MEMORY.md` is regenerated locally on each machine from those scope fields, and is
gitignored. The PC's index lists `estate` + `winpc`; the Mac's lists `estate` +
`macmini`. Same repo, different view.

This is the load-bearing idea. `MEMORY.md` is injected into every session and appended
to by both agents constantly. Tracking it guarantees perpetual conflicts; under
Syncthing it produces a `.sync-conflict-*` file on every concurrent write. Generating it
removes the conflict class entirely and reduces a scope change to a one-line frontmatter
edit.

Index entries point into the subdirectory with relative links, which is how a memory in
`_shared/` becomes reachable from the index:

```markdown
- [Instrument failure catalogue](_shared/instrument-failure-catalogue.md) — read before trusting any green check
- [PC crash investigation](_local/pc-crash-investigation-2026-08-17.md) — winpc-local
```

### 4.3 PC fragmentation

The PC's four project scopes are resolved by making `C--WINDOWS-system32` canonical (it
is the live store) and exposing the same repo in the other three via **directory
junctions** (`mklink /J`, no admin required, unlike symlinks). One clone, one working
tree, visible from whichever directory Claude is launched in.

### 4.4 Transport tiers

```
tier 1  git push over LAN ssh        (primary; both directions verified)
tier 2  Syncthing folder             (fallback; already configured Mac-side)
tier 3  git bundle -> GCS bucket     (offsite backup + disaster recovery)
```

Both machines set `receive.denyCurrentBranch = updateInstead` so pushes land
worktree-to-worktree with no bare intermediary.

**Correction (mac-claude O2).** An earlier draft claimed the dirty-worktree refusal "does
not arise because hooks commit before pushing". That was wrong: `updateInstead` inspects
the **receiving** worktree, not the sender's, and the receiver is dirty precisely when it
is mid-turn writing a memory. Unfixed, this makes push fail intermittently and exactly
when the peer is most active.

Fix: a `PostToolUse` **commit-on-write** hook commits immediately after any memory-file
write, so the worktree is essentially never dirty between operations. This also shrinks
the `Stop`-hook window. If residual failures remain, the fallback is a bare intermediary
both push to (GCS-hosted, keeping no local central node).

**Non-fast-forward pushes** (both machines pushing at once) must `pull --rebase` and retry
**immediately**, never defer to the 30-minute reconciler — deferring leaves a known
divergence standing for up to half an hour.

### 4.5 Triggers

| Trigger | Action | Constraint |
| --- | --- | --- |
| `SessionStart` | pull, regenerate index | **hard timeout, fails open** — a sync fault must never block a session |
| `Stop` / `SessionEnd` | commit, push tier 1 → 2 | best effort; failure recorded, never silent |
| timer, 30 min, both machines | full reconcile: pull, merge, regenerate, push, bundle to GCS | this is what makes "always synced" true after a blackout |

The PC already has `SessionStart`, `Stop` and `SessionEnd` hooks in
`~/.claude/settings.json`; the new hooks must be **added to** that config, not replace it.

## 5. Conflict semantics

- Different files edited on both machines → git merges cleanly, no human involvement.
- Same file edited on both → a real conflict. **Both bodies are kept**, marked for an
  agent to reconcile on next session. A memory is never auto-discarded.
- `MEMORY.md` → cannot conflict; it is generated (D7).
- Deletion is treated as a conflict-worthy edit: a memory deleted on one machine and
  edited on the other is kept, not deleted.

## 6. Failure semantics and observability

Applying the lesson that cost 29 hours: **a failed sync must never silently no-op**,
because a silent sync failure is exactly divergence.

- Every run writes a status file with `OK` / `DEGRADED` / `BAD` / `INCOMPLETE`.
- Anything unmeasurable reports `UNKNOWN`, never `PASS`.
- A **completeness gate** counts files in the repo against files in the generated index
  and fails loudly if they disagree — an unindexed memory is invisible by construction.
- State changes raise a local desktop toast. `ntfy` is LAN-only with no subscriber, so
  the toast is the only live alert path.
- The existing `VajraPeerLiveness` task already asserts peer reachability end-to-end and
  is the natural place to surface sync staleness.

## 7. Backfill

`RULE.md`, committed to the repo, is the single written rule both agents classify
against. First draft:

> **estate** — true regardless of which machine reads it: project state and goals,
> doctrine and protocols, external facts, cross-machine topology, anything another agent
> would be wrong not to know.
> **machine-local** — true only of this box: its hardware, thermals, disks, drivers,
> OS quirks, paths, and local service layout.
> When genuinely uncertain, classify **estate**. A wrongly-local memory is invisible to
> the peer forever; a wrongly-estate memory is merely noise.

Sequence:

1. Unpause the PC device in the Mac's Syncthing (unblocks everything).
2. Mac-claude re-checks its existing 167/24 split against `RULE.md`.
3. winpc-claude classifies its 240 across four scopes, deduping against the Mac's 167.
4. Cross-review: each agent reviews the other's calls, hunting specifically for facts
   wrongly marked machine-local.
5. Operator signs off on the estate set only.

Expect heavy duplication: both stores descend from the same estate history, so the
merged total will be well below 240 + 191.

## 8. Acceptance falsifiers

The design is accepted only when each of these has been **executed**, not reasoned about:

1. A memory written on the PC appears on the Mac after one sync cycle, and vice versa.
2. Concurrent edits to *different* memories both survive with no conflict.
3. Concurrent edits to the *same* memory surface a conflict and lose neither body.
4. With the peer powered off, sync reports `BAD`/`UNKNOWN` (never `OK`), loses nothing,
   and converges when the peer returns.
5. The PC's generated index provably contains no `macmini`-scoped entry, and vice versa.
6. The completeness gate fires when a memory file is added without an index entry.
7. Killing a session mid-turn loses no committed memory; the 30-minute reconciler
   recovers the push.
8. A restore from the GCS bundle alone reproduces the shared set.
   **STATUS: PASSED 2026-09-01.** Bucket verified independently from the PC
   (4 bundles, EUROPE-WEST2). Mac-claude executed the restore: fresh download of
   `-latest.bundle` → `git clone` into a scratch dir → **167 `.md` files, HEAD `ccdc870`**,
   matching the live `_shared` count exactly. The DR path is real, not asserted.

9. A push to a peer whose worktree is dirty mid-turn still succeeds (guards the O2 fix).
10. A file whose directory and `scope:` disagree is caught by the lint (guards the O3 fix).

## 9. Rollout and rollback

Rollout is staged, and each stage is reversible:

1. Unpause the PC device in the Mac's Syncthing; confirm the link. *(reversible: re-pause)*
2. Clone the Mac's existing `_shared` repo into the PC canonical dir; add junctions from
   the other three scopes. *(reversible: delete clone + junctions)*
3. Create `_local` on each machine and move that machine's local memories into it; push
   each to the peer as `_peer-local`. *(reversible: move files back)*
4. Generate the PC index; verify falsifiers 5 and 6 **before** wiring any hook.
5. Add hooks and the timer. *(reversible: remove the added entries; the pre-existing
   `SessionStart` / `Stop` / `SessionEnd` hooks must be preserved, not replaced)*
6. Enable the GCS bundle and verify falsifier 8 (restore from bundle alone).

**Full backup before step 2:** copy both machines' memory dirs to timestamped archives.
Rollback at any stage is deleting the repo and restoring the archive; the original flat
memory files are untouched throughout, since `_shared/` is additive.

## 10. Out of scope

- Merging `.remember/` (a separate store, currently a frozen one-time copy).
- The episodic-memory plugin store and the ruflo/AgentDB backend.
- `CLAUDE.md` itself.
- Any third agent or machine, though the layout does not preclude one.

## 11. Open questions

1. **Do the Mac's 167 belong in `estate`?** They include many `capos-*`, `aksha-*` and
   other project memories. Plausibly estate-wide, but this is Mac-claude's unreviewed
   classification and step 4 of §7 exists to test it.
2. **Does `C--Users-viraa` (190 files, index stale since 2026-08-05) hold anything the
   live store lacks?** It may be a historical store worth mining once rather than syncing.
3. **`vaidya-tree-import@sutradhara`** re-fetches a 34 GB tree roughly every 7-11 minutes
   on the relay and caused the outage. Unrelated to this design but unresolved, and it
   will re-wedge the relay.
