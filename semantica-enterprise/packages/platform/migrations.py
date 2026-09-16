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
            source = path.read_text(encoding="utf-8")
            # A leading dialect directive applies to the complete migration.
            # Earlier single-statement migrations happened to work with the
            # statement-level parser, but multi-statement PostgreSQL migrations
            # must never leak JSONB casts or ALTER syntax into SQLite tests.
            first_line, separator, remaining = source.partition("\n")
            if first_line.startswith("-- dialect:"):
                file_dialects = {
                    item.strip()
                    for item in first_line.removeprefix("-- dialect:").split(",")
                }
                source = remaining if separator and engine.dialect.name in file_dialects else ""
            statements = [item.strip() for item in source.split(";\n") if item.strip()]
            for statement in statements:
                if statement.startswith("-- dialect:"):
                    directive, _, sql = statement.partition("\n")
                    dialects = {item.strip() for item in directive.removeprefix("-- dialect:").split(",")}
                    if engine.dialect.name not in dialects:
                        continue
                    statement = sql.strip()
                    if not statement:
                        continue
                connection.exec_driver_sql(statement)
            connection.execute(
                text("INSERT INTO schema_migrations(version) VALUES (:version)"),
                {"version": path.stem},
            )
