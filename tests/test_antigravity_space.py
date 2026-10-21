"""Space checks must run before allocating either a snapshot or an archive."""

import os
import sqlite3
from contextlib import closing

import pytest

from clicodelog import sync_antigravity


@pytest.mark.parametrize("large_wal", [True, False])
def test_raw_backup_budget_includes_wal_and_superseded_copy(tmp_path, monkeypatch, large_wal):
    source = tmp_path / "source.db"
    target = tmp_path / "backup" / "saved.db"
    target.parent.mkdir()
    previous = b"previous backup" * (200 if large_wal else 100_000)
    target.write_bytes(previous)
    os.utime(target, (0, 0))
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE history (payload BLOB)")
        conn.execute("INSERT INTO history VALUES (zeroblob(?))", (1024 * 1024 if large_wal else 32,))
        conn.commit()
        wal = source.with_name(source.name + "-wal")
        source_size, wal_size = source.stat().st_size, wal.stat().st_size
        free = wal_size // 2 if large_wal else len(previous) // 2
        assert source_size * 2 < free, "The previous budget would incorrectly allow this backup"
        budgets = []
        def available(path, needed):
            budgets.append(needed)
            return needed <= free, free
        monkeypatch.setattr(sync_antigravity, "has_free_space", available)
        stats = {"copied": 0, "skipped": 0, "failed": 0, "skipped_no_space": 0}
        sync_antigravity._raw(source, target, stats, lambda *args: pytest.fail("must not copy"))
        assert budgets and budgets[0] >= source_size + wal_size + len(previous)
        assert stats["skipped_no_space"] == 1 and stats["copied"] == 0
        assert target.read_bytes() == previous
        assert list(target.parent.iterdir()) == [target], "No archive or temporary file may be allocated"
