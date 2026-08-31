"""One-time, idempotent migration of legacy sync_scope to spec scope.

mac-claude's 167 existing shared memories carry `metadata.sync_scope: shared`.
The spec requires `metadata.scope: estate`. This rewrites them in place, and is
safe to run repeatedly: a file that already declares `scope` is never touched.
"""
from pathlib import Path
from .frontmatter import parse, dump

LEGACY_MAP = {"shared": "estate"}


def migrate_text(text, default_scope):
    """Return (new_text, changed)."""
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
