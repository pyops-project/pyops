"""Integration tests for the start-up schema sync (`init_db()` / `sync_schema()`) on real SQLite files.

Test-only columns live in their own MetaData (`_job_metadata`), never in `Base.metadata`."""
import contextlib
import re
import sqlite3

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, text

from tests.conftest import sqlite_schema


def _rows(path, table):
    with contextlib.closing(sqlite3.connect(str(path))) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


def _projected(rows, like):
    """`rows` reduced to the columns of `like` (drops added columns)."""
    return [{key: row[key] for key in like[0]} for row in rows]


def _backups(path):
    return sorted(path.parent.glob(f"{path.stem}.bck.*.db"))


def _job_metadata(*columns):
    """Test-only metadata declaring the `job` table with only its key plus `columns`."""
    md = MetaData()
    Table("job", md, Column("id", String, primary_key=True), *columns)
    return md


def _probe_metadata():
    return _job_metadata(
        Column("probe_note", String),
        Column("probe_n", Integer, nullable=False, server_default=text("3")),
        Column("probe_ref", Integer, index=True),
    )


def test_fresh_db_gets_full_schema(empty_db):
    from sqlalchemy import inspect
    from app.orm.database import Base, get_db_engine, init_db

    assert init_db() == ["created table job", "created table schedule"]

    insp = inspect(get_db_engine())
    assert sorted(insp.get_table_names()) == sorted(Base.metadata.tables)
    for table in Base.metadata.sorted_tables:
        assert [col["name"] for col in insp.get_columns(table.name)] == [col.name for col in table.columns]
        assert {ix["name"] for ix in insp.get_indexes(table.name)} == {ix.name for ix in table.indexes}
    assert _backups(empty_db) == []


def test_models_upgrade_populated_baseline_db(empty_db, seed_db, schema_baseline):
    """CI guard: fails when a model change cannot be added in place to a database of any released schema baseline."""
    from app.orm.database import init_db

    seed_db(empty_db, schema_baseline)
    jobs, schedules = _rows(empty_db, "job"), _rows(empty_db, "schedule")

    init_db()

    assert _projected(_rows(empty_db, "job"), jobs) == jobs
    assert _projected(_rows(empty_db, "schedule"), schedules) == schedules


def test_job_schedule_id_added_to_3_3_6_db(empty_db, seed_db):
    from app.orm.database import init_db

    seed_db(empty_db, "3.3.6")

    init_db()

    assert [row["schedule_id"] for row in _rows(empty_db, "job")] == [None]
    assert "ix_job_schedule_id" in {name for _, name, _ in sqlite_schema(empty_db)}


def test_schedule_keep_n_added_to_3_3_6_db(empty_db, seed_db):
    from app.orm.database import init_db

    seed_db(empty_db, "3.3.6")

    init_db()

    assert [row["keep_n"] for row in _rows(empty_db, "schedule")] == [0]


def test_schedule_max_fail_n_added_to_3_3_6_db(empty_db, seed_db):
    from app.orm.database import init_db

    seed_db(empty_db, "3.3.6")

    init_db()

    assert [row["max_fail_n"] for row in _rows(empty_db, "schedule")] == [3]


def test_columns_and_index_added_after_backup(empty_db, seed_db):
    from app.lib.files import version
    from app.orm.database import get_db_engine, sync_schema

    seed_db(empty_db, "3.3.6")
    schema, jobs = sqlite_schema(empty_db), _rows(empty_db, "job")

    assert sync_schema(get_db_engine(), _probe_metadata()) == [
        "added column job.probe_note",
        "added column job.probe_n",
        "added column job.probe_ref",
        "created index ix_job_probe_ref",
    ]

    rows = _rows(empty_db, "job")
    assert _projected(rows, jobs) == jobs
    assert [(row["probe_note"], row["probe_n"], row["probe_ref"]) for row in rows] == [(None, 3, None)]
    assert "ix_job_probe_ref" in {name for _, name, _ in sqlite_schema(empty_db)}
    [backup] = _backups(empty_db)
    assert re.fullmatch(rf"test\.bck\.{re.escape(version())}\.[0-9a-f]{{12}}\.\d{{8}}T\d{{6}}\.db", backup.name)
    assert sqlite_schema(backup) == schema


def test_second_run_changes_nothing(empty_db, seed_db):
    from app.orm.database import get_db_engine, sync_schema

    seed_db(empty_db, "3.3.6")
    sync_schema(get_db_engine(), _probe_metadata())
    schema, backups = sqlite_schema(empty_db), _backups(empty_db)

    assert sync_schema(get_db_engine(), _probe_metadata()) == []
    assert sqlite_schema(empty_db) == schema
    assert _backups(empty_db) == backups


def test_failure_rolls_back_and_names_statement_and_backup(empty_db, seed_db):
    """A failed sync leaves the content unchanged, so restarting reuses the backup instead of piling up copies."""
    from app.orm.database import get_db_engine, sync_schema

    seed_db(empty_db, "3.3.6")
    schema = sqlite_schema(empty_db)
    md = _job_metadata(
        Column("probe_note", String),  # applied first, then rolled back
        Column("probe_bad", Integer, nullable=False),  # no server_default: rejected on a populated table
    )

    def failed_sync():
        with pytest.raises(RuntimeError) as exc:
            sync_schema(get_db_engine(), md)
        return str(exc.value)

    first = failed_sync()
    [backup] = _backups(empty_db)
    # the restart happens later: the reused backup keeps its name, no copy with a newer datetime appears
    earlier = backup.with_name(re.sub(r"\d{8}T\d{6}", "20000101T000000", backup.name))
    backup.rename(earlier)
    second = failed_sync()

    assert _backups(empty_db) == [earlier]
    assert not (empty_db.parent / "test.bck.tmp").exists()
    for error, named in ((first, backup), (second, earlier)):
        assert f"schema sync of {empty_db} failed and was rolled back: " \
               "Cannot add a NOT NULL column with default value NULL" in error
        assert "ALTER TABLE job ADD COLUMN probe_bad INTEGER NOT NULL" in error
        assert str(named) in error
    assert sqlite_schema(empty_db) == schema


def test_stale_backup_tmp_does_not_block_backup(empty_db, seed_db):
    """A <stem>.bck.tmp left by a start-up killed mid-backup is replaced, not refused by VACUUM INTO."""
    from app.orm.database import get_db_engine, sync_schema

    seed_db(empty_db, "3.3.6")
    schema = sqlite_schema(empty_db)
    stale = empty_db.parent / "test.bck.tmp"
    stale.write_bytes(b"partial backup")

    assert sync_schema(get_db_engine(), _probe_metadata()) == [
        "added column job.probe_note",
        "added column job.probe_n",
        "added column job.probe_ref",
        "created index ix_job_probe_ref",
    ]

    [backup] = _backups(empty_db)
    assert sqlite_schema(backup) == schema
    assert not stale.exists()


def test_init_db_disposes_engine(empty_db):
    import app.orm.database as database

    database.init_db()

    assert database._engine is None
