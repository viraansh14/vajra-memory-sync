import json
from pathlib import Path
from memory_sync.cli import main


def _mem(name, scope):
    return ("---\nname: {n}\ndescription: d\nmetadata:\n  scope: {s}\n---\n\nb\n"
            .format(n=name, s=scope))


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


def test_migrate_rewrites_legacy_scope(tmp_path):
    t = tmp_path
    (t / "_shared").mkdir()
    (t / "_shared" / "old.md").write_text(
        "---\nname: old\nmetadata:\n  sync_scope: shared\n---\n\nb\n", encoding="utf-8")
    assert main(["migrate", "--root", str(t), "--machine", "winpc"]) == 0
    assert "scope: estate" in (t / "_shared" / "old.md").read_text(encoding="utf-8")


def test_index_never_lists_peer_local(tmp_path):
    t = _tree(tmp_path)
    (t / "_peer-local").mkdir()
    (t / "_peer-local" / "peer.md").write_text(_mem("peer", "macmini"), encoding="utf-8")
    main(["index", "--root", str(t), "--machine", "winpc"])
    out = (t / "MEMORY.md").read_text(encoding="utf-8")
    assert "_peer-local" not in out
    assert "peer" not in out
