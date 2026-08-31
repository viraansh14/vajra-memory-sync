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
