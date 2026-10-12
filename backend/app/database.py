"""SQLAlchemy engine / session management and database initialisation."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


def _make_engine():
    kwargs = {"connect_args": {"check_same_thread": False}, "future": True}
    if settings.database_url.startswith("sqlite"):
        kwargs["pool_pre_ping"] = True
    return create_engine(settings.database_url, **kwargs)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables and apply additive column migrations. Safe to repeat."""
    # Importing the models module registers every model class with ``Base``, which
    # is what makes ``create_all`` aware of them. Imported dynamically so the
    # import is not reported as unused.
    import importlib

    importlib.import_module(f"{__package__}.models")

    Base.metadata.create_all(bind=engine)
    migrate_additive()


def _column_ddl(table_name: str, column, dialect) -> str:
    """Build an ``ALTER TABLE ... ADD COLUMN`` statement for SQLite/Postgres."""
    col_type = column.type.compile(dialect=dialect)
    stmt = f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {col_type}'
    default = None
    if column.server_default is not None:
        arg = getattr(column.server_default, "arg", None)
        if arg is not None:
            default = str(arg)
    if default is None and not column.nullable:
        # A NOT NULL column can only be added with a value in SQLite.
        if col_type.upper().startswith("BOOL"):
            default = "0"
        elif "INT" in col_type.upper() or "FLOAT" in col_type.upper():
            default = "0"
        else:
            default = "''"
    if default is not None:
        stmt += f" DEFAULT {default}"
    return stmt


def migrate_additive() -> list[str]:  # noqa: C901 - a short, linear sequence of steps
    """Add columns/indexes that exist on the models but not yet in the database.

    This is a deliberately narrow, additive-only migration: it never drops or
    rewrites a column, so existing demonstration data is preserved. SQLite
    supports ``ADD COLUMN`` for nullable columns and for ``NOT NULL`` columns
    that carry a default, which is what :func:`_column_ddl` guarantees.

    Returns the list of applied changes (useful for logs and tests).
    """
    from sqlalchemy import inspect, text as sql_text

    applied: list[str] = []
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                conn.execute(sql_text(_column_ddl(table.name, column, engine.dialect)))
                applied.append(f"{table.name}.{column.name}")

    # create_all skips indexes on tables that already exist, so create any that
    # are missing explicitly.
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            try:
                index.create(bind=engine, checkfirst=True)
            except Exception as exc:  # noqa: BLE001 - index is an optimisation, not correctness
                applied.append(f"index-skipped:{index.name}:{exc.__class__.__name__}")

    applied.extend(_backfill_city_keys())
    return applied


def _backfill_city_keys() -> list[str]:
    """Populate ``challenges.city_key`` for rows that predate the column.

    City-scoped duplicate detection filters on ``city_key``; without this, reports
    created before the upgrade would be invisible to that filter.
    """
    from sqlalchemy import inspect, text as sql_text

    from .services.duplicate_service import normalise_city

    inspector = inspect(engine)
    if "challenges" not in inspector.get_table_names():
        return []
    columns = {c["name"] for c in inspector.get_columns("challenges")}
    if "city_key" not in columns:
        return []

    updates: list[tuple[str, int]] = []
    with engine.connect() as conn:
        rows = conn.execute(sql_text(
            'SELECT id, city FROM challenges WHERE city_key IS NULL AND city IS NOT NULL'
        )).fetchall()
    for row_id, city in rows:
        key = normalise_city(city)
        if key:
            updates.append((key, row_id))
    if not updates:
        return []
    with engine.begin() as conn:
        for key, row_id in updates:
            conn.execute(
                sql_text("UPDATE challenges SET city_key = :key WHERE id = :id"),
                {"key": key, "id": row_id})
    return [f"backfilled city_key for {len(updates)} challenges"]
