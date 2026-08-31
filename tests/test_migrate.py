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
