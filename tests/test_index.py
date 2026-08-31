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
    assert sorted(e["name"] for e in entries) == ["cat"]


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
