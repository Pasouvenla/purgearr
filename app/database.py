import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import DATABASE_URL

logger = logging.getLogger(__name__)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app.models import user, media, settings, rule, action_log, library_snapshot  # noqa: F401
    Base.metadata.create_all(bind=engine)


def migrate_db():
    """Add new columns to existing tables (idempotent, SQLite ALTER TABLE)."""
    from sqlalchemy import text
    new_columns = [
        ("rules", "condition_max_size_gb", "FLOAT"),
        ("rules", "condition_watch_status", "VARCHAR"),
        ("rules", "condition_resolution", "TEXT"),
        ("rules", "condition_tags", "TEXT"),
        ("rules", "schedule_interval_hours", "INTEGER"),
        ("rules", "priority", "INTEGER DEFAULT 0"),
        ("rules", "action_unmonitor_arr", "BOOLEAN DEFAULT 0"),
        ("rules", "action_delete_from_media_server", "BOOLEAN DEFAULT 0"),
        ("media_items", "file_path", "VARCHAR"),
        ("media_items", "arr_path", "VARCHAR"),
    ]
    with engine.connect() as conn:
        for table, col, col_type in new_columns:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception as e:
                msg = str(e).lower()
                if "duplicate column" not in msg and "already exists" not in msg:
                    logger.warning(f"migrate_db: unexpected error adding {table}.{col}: {e}")

        # Deduplicate media_items by (source, source_id) — keep lowest id (first inserted)
        # then add a unique index so duplicates can never accumulate again.
        try:
            conn.execute(text("""
                DELETE FROM media_items
                WHERE id NOT IN (
                    SELECT MIN(id) FROM media_items GROUP BY source, source_id
                )
            """))
            conn.commit()
        except Exception:
            pass

        try:
            conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_media_source_id
                ON media_items (source, source_id)
            """))
            conn.commit()
        except Exception:
            pass
