from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, text


def run_migrations(engine: Engine) -> None:
    """Apply packaged, append-only SQL migrations exactly once.

    SQLAlchemy metadata still supports a fresh install; this registry supplies
    an auditable and repeatable upgrade path for existing persistent volumes.
    """
    migration_root = Path(__file__).with_name("sql_migrations")
    migration_files = sorted(migration_root.glob("*.sql"))
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('semantica_enterprise_migrations'))"))
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version VARCHAR(200) PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        applied = {
            row[0] for row in connection.exec_driver_sql("SELECT version FROM schema_migrations")
        }
        for path in migration_files:
            if path.stem in applied:
                continue
            statements = [item.strip() for item in path.read_text(encoding="utf-8").split(";\n") if item.strip()]
            for statement in statements:
                connection.exec_driver_sql(statement)
            connection.execute(
                text("INSERT INTO schema_migrations(version) VALUES (:version)"),
                {"version": path.stem},
            )

