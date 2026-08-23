"""Data-safety and containment tests.

Everything here protects data the user cannot regenerate, or blocks a path that
reaches outside the app's own directory.
"""

import json
import threading

import pytest

from clicodelog import storage
from clicodelog.utils import decode_path_id, encode_path_id, is_safe_id, safe_child


# ----------------------------------------------------------------- atomic writes
def test_write_json_is_atomic_and_leaves_no_temp_files(tmp_path):
    target = tmp_path / "bookmarks.json"
    assert storage.write_json(target, [{"id": "a"}])
    assert json.loads(target.read_text()) == [{"id": "a"}]
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_corrupt_file_is_quarantined_not_silently_emptied(tmp_path):
    """The old code returned [] on a decode error and then saved that empty list
    back, permanently destroying every bookmark. The data must survive."""
    target = tmp_path / "bookmarks.json"
    target.write_text('[{"id": "important"}, {"id": "trunca')

    result = storage.load_json(target, [])

    assert result == []                       # caller still gets a usable default
    quarantined = list(tmp_path.glob("bookmarks.json.corrupt-*"))
    assert quarantined, "corrupt file must be preserved, not discarded"
    assert "important" in quarantined[0].read_text()
    assert not target.exists()                # so a later save cannot clobber it


def test_backup_copy_is_kept(tmp_path):
    target = tmp_path / "bookmarks.json"
    storage.write_json(target, [{"id": "first"}], keep_backup=True)
    storage.write_json(target, [{"id": "second"}], keep_backup=True)
    bak = tmp_path / "bookmarks.json.bak"
    assert bak.exists()
    assert json.loads(bak.read_text()) == [{"id": "first"}]


def test_concurrent_writes_never_produce_a_partial_file(tmp_path):
    target = tmp_path / "data.json"
    payload = [{"i": i, "pad": "x" * 500} for i in range(400)]
    errors = []

    def writer(n):
        try:
            for _ in range(15):
                storage.write_json(target, [{**r, "w": n} for r in payload])
        except Exception as e:                      # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    loaded = json.loads(target.read_text())         # must always parse
    assert len(loaded) == 400


def test_bookmarks_survive_a_round_trip(tmp_path, monkeypatch):
    from clicodelog import bookmarks
    monkeypatch.setattr(bookmarks, "BOOKMARKS_FILE", tmp_path / "bookmarks.json")

    bookmarks.add_bookmark({"source": "claude-code", "project_id": "p",
                            "session_id": "s", "uuid": "u1", "note": "keep me"})
    bookmarks.add_bookmark({"source": "claude-code", "project_id": "p",
                            "session_id": "s", "uuid": "u2"})
    assert len(bookmarks.load_bookmarks()) == 2

    bookmarks.remove_bookmark("claude-code:p:s:u2")
    remaining = bookmarks.load_bookmarks()
    assert len(remaining) == 1
    assert remaining[0]["note"] == "keep me"


def test_disk_guard_blocks_oversized_work(tmp_path):
    ok, free = storage.has_free_space(tmp_path, 1)
    assert ok and free > 0
    ok, _ = storage.has_free_space(tmp_path, 10 ** 15)
    assert not ok, "must refuse work that cannot fit"


# ----------------------------------------------------------------- containment
@pytest.mark.parametrize("bad", ["..", ".", "../etc", "a/b", "a\\b", "", "x\x00y"])
def test_unsafe_ids_are_rejected(bad):
    assert not is_safe_id(bad)


@pytest.mark.parametrize("good", ["-Users-x-proj", "abc123", "session-2026-08-01",
                                  "a.b.c", "under_score"])
def test_safe_ids_are_accepted(good):
    assert is_safe_id(good)


def test_safe_child_refuses_to_escape(tmp_path):
    assert safe_child(tmp_path, "..") is None
    assert safe_child(tmp_path, "..", "..") is None
    assert safe_child(tmp_path, "ok") == tmp_path / "ok"


def test_path_id_round_trip():
    for path in ["/Users/x/my_app", "/a-b/c_d", "/with space/and-dash"]:
        assert decode_path_id(encode_path_id(path)) == path


def test_traversal_ids_are_rejected_by_the_api_layer(tmp_path, monkeypatch):
    from clicodelog import conversation
    monkeypatch.setattr(conversation, "DATA_DIR", tmp_path)
    assert conversation.find_session_path("..", "x", "claude-code") is None
    result = conversation.get_conversation("..", "..", "claude-code")
    assert "error" in result


# ----------------------------------------------------------------- sync safety
def test_sync_replaces_files_without_ever_removing_the_original(tmp_path):
    """The old copy unlinked the destination first, so a failure mid-copy left
    nothing behind — and for a file the source had already pruned, that backup
    was the only remaining copy."""
    from clicodelog.sync import _place_atomically

    src = tmp_path / "src.jsonl"
    dest = tmp_path / "dest.jsonl"
    src.write_text("new content")
    dest.write_text("old content")

    assert _place_atomically(src, dest)
    assert dest.read_text() == "new content"
    assert not [p for p in tmp_path.iterdir() if ".tmp-sync" in p.name]


def test_sync_never_deletes_files_missing_from_the_source(tmp_path):
    """Additive backup is the whole point: upstream pruning must not propagate."""
    from clicodelog.sync import _additive_copy

    src = tmp_path / "src"
    dest = tmp_path / "dest"
    (src / "keep").mkdir(parents=True)
    (src / "keep" / "a.jsonl").write_text("a")
    (dest / "keep").mkdir(parents=True)
    (dest / "keep" / "pruned-upstream.jsonl").write_text("irreplaceable")

    stats = {"copied": 0, "skipped": 0, "failed": 0, "skipped_no_space": 0}
    _additive_copy(src, dest, stats)

    assert (dest / "keep" / "a.jsonl").read_text() == "a"
    assert (dest / "keep" / "pruned-upstream.jsonl").read_text() == "irreplaceable"
