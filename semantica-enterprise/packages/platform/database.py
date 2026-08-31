from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=connect_args,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401
    from .migrations import run_migrations

    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    # M10.1: manually curated graph edges do not necessarily originate from a
    # document chunk. Keep extracted provenance when present, while allowing
    # first-class graph editing from the UI.
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE facts ALTER COLUMN source_chunk_id DROP NOT NULL"
            )
