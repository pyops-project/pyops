import os.path
import atexit
import gc
import glob
import hashlib
from datetime import datetime

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool, QueuePool
from sqlalchemy.schema import CreateColumn

from app.lib.files import conf_data, version

project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

_engine = None

def get_db_path():
    db_rel = conf_data()["db"]
    if os.path.isabs(db_rel):
        return db_rel
    return os.path.join(project_root, db_rel)

def _backup(engine, db) -> str:
    """Write a consistent copy of `db` next to it as `<stem>.bck.<VERSION>.<HASH>.<DATETIME>.db` and
    return its path; an existing backup with the same content hash is reused instead."""
    folder, stem = os.path.dirname(db), os.path.splitext(os.path.basename(db))[0]
    tmp = os.path.join(folder, f"{stem}.bck.tmp")
    if os.path.exists(tmp):  # left by a start-up killed mid-backup; VACUUM INTO refuses to overwrite
        os.remove(tmp)
    # VACUUM rebuilds the file from the content (WAL included), so equal content gives an equal
    # file, e.g. on every restart after a failed, rolled-back sync; it cannot run in a transaction
    with engine.connect() as conn:
        conn.exec_driver_sql("VACUUM INTO ?", (tmp,))
    with open(tmp, "rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()[:12]
    existing = glob.glob(os.path.join(folder, f"{stem}.bck.*.{digest}.*.db"))
    if existing:
        os.remove(tmp)
        return existing[0]
    backup = os.path.join(folder, f"{stem}.bck.{version()}.{digest}.{datetime.now():%Y%m%dT%H%M%S}.db")
    os.replace(tmp, backup)
    return backup

def sync_schema(engine, metadata) -> list[str]:
    """Additively bring the database up to `metadata`: create missing tables, add missing columns
    (ALTER TABLE ... ADD COLUMN) and create missing indexes. Returns the applied changes ([] when current).

    Before changing a database that already has tables, a backup is written next to it (see _backup).
    The changes run in one transaction; on failure the database is left unchanged and a RuntimeError
    names the failing statement, the backup and the recovery steps."""
    insp = inspect(engine)
    live_tables = set(insp.get_table_names())
    tables, columns, indexes = [], [], []
    for table in metadata.sorted_tables:
        if table.name not in live_tables:
            tables.append(table)
            continue
        live_columns = {col["name"] for col in insp.get_columns(table.name)}
        columns += [column for column in table.columns if column.name not in live_columns]
        live_indexes = {index["name"] for index in insp.get_indexes(table.name)}
        indexes += [index for index in table.indexes if index.name not in live_indexes]
    if not (tables or columns or indexes):
        return []

    db = engine.url.database
    backup = _backup(engine, db) if live_tables else None

    changes = []
    try:
        with engine.begin() as conn:
            # pysqlite autocommits DDL; an explicit BEGIN makes the whole sync one transaction
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            if tables:
                metadata.create_all(conn, tables=tables)
                changes += [f"created table {table.name}" for table in tables]
            for column in columns:
                conn.exec_driver_sql(
                    f"ALTER TABLE {conn.dialect.identifier_preparer.format_table(column.table)} "
                    f"ADD COLUMN {CreateColumn(column).compile(dialect=conn.dialect)}"
                )
                changes.append(f"added column {column.table.name}.{column.name}")
            for index in indexes:
                index.create(conn)
                changes.append(f"created index {index.name}")
    except DBAPIError as exc:
        raise RuntimeError(
            f"schema sync of {db} failed and was rolled back: {exc.orig}\n"
            f"  statement: {exc.statement}\n"
            f"  backup: {backup or 'none (new database)'}\n"
            "  to recover: restore the backup, fix the cause, restart pyops (the sync runs again)"
        ) from exc
    return changes

def init_db() -> list[str]:
    """Sync the schema to the models (see sync_schema) and return the applied changes.

    Disposes the engine afterwards: an SQLite connection must not be carried across fork() into the
    runner or the gunicorn workers, which open their own lazily via get_db_engine()."""
    changes = sync_schema(get_db_engine(), Base.metadata)
    destroy_db()
    return changes

def get_db_engine():
    """Return global engine instance with shared pool."""
    global _engine
    if _engine is None:
        url = f"sqlite:///{get_db_path()}"
        pool_size = conf_data().get("pool_size", 5)
        pool_recycle = conf_data().get("pool_recycle", 300)
        _engine = create_engine(
            url, poolclass=QueuePool, pool_size=pool_size, pool_recycle=pool_recycle, pool_pre_ping=True, future=True
        )
        with _engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.commit()
    return _engine

def destroy_db():
    """Dispose the global engine."""
    global _engine
    if _engine is not None:
        # Checkpoint WAL before dispose to ensure clean closure
        with _engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(FULL)"))
            conn.commit()
        _engine.dispose()
        if hasattr(_engine.pool, '_connections'):
            for conn in list(_engine.pool._connections.values()):
                if hasattr(conn, 'close'):
                    try:
                        conn.close()
                    except:
                        pass
        _engine = None
        gc.collect()

Base = declarative_base()

atexit.register(destroy_db)
