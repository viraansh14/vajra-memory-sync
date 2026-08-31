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
