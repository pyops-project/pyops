import os.path
import atexit
import gc

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool, QueuePool

from app.lib.files import conf_data

project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

_engine = None

def get_db_path():
    db_rel = conf_data()["db"]
    if os.path.isabs(db_rel):
        return db_rel
    return os.path.join(project_root, db_rel)

def init_db():
    """Initialize database tables once."""
    engine = get_db_engine()
    Base.metadata.create_all(bind=engine)

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
