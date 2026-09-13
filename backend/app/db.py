"""Engine + sesión SQLAlchemy. SQLite por defecto, Postgres vía DATABASE_URL."""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def init_db():
    from . import models  # noqa: F401 (registra modelos)
    Base.metadata.create_all(bind=engine)


def migrate():
    """Columnas nuevas en DBs ya creadas (SQLite y Postgres). Idempotente."""
    from sqlalchemy import text
    with engine.begin() as conn:
        for col in ("greeted INTEGER DEFAULT 0", "clarify_count INTEGER DEFAULT 0"):
            try:
                conn.execute(text(f"ALTER TABLE conversations ADD COLUMN {col}"))
            except Exception:
                pass
