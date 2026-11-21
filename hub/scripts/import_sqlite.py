"""One-time import of a HomeMusic SQLite backup into an empty PostgreSQL baseline."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Json, Jsonb
from sqlalchemy import JSON, Boolean, Date, DateTime
from sqlalchemy.dialects.postgresql import JSONB

from app.db import get_database_url
from app.models import Base

SOURCE_REVISION = "20a000000034"
TARGET_REVISION = "30a000000001"


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _convert(kind, value):
    if value is None:
        return None
    if isinstance(kind, JSONB):
        return Jsonb(json.loads(value))
    if isinstance(kind, JSON):
        return Json(json.loads(value))
    if isinstance(kind, Boolean):
        if value not in (0, 1):
            raise ValueError("expected a SQLite boolean")
        return bool(value)
    if isinstance(kind, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(kind, Date):
        return date.fromisoformat(value)
    return value


def _reset_sequence(target, table, high_water: int) -> None:
    sequence = target.execute(
        """SELECT n.nspname, c.relname
           FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.oid = pg_get_serial_sequence(%s, %s)::regclass""",
        (table, "id"),
    ).fetchone()
    if sequence is None:
        return
    largest = target.execute(sql.SQL("SELECT max(id) FROM {}").format(sql.Identifier(table))).fetchone()[0]
    target.execute(sql.SQL("ALTER SEQUENCE {} RESTART WITH {}").format(
        sql.Identifier(*sequence), sql.Literal(max(largest or 0, high_water) + 1)
    ))


def transfer(path: Path, url=None) -> dict[str, int]:
    database = get_database_url(url).set(drivername="postgresql")
    with (
        closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source,
        psycopg.connect(database.render_as_string(hide_password=False)) as target,
    ):
        source.execute("BEGIN")
        if source.execute("SELECT version_num FROM alembic_version").fetchall() != [(SOURCE_REVISION,)]:
            raise ValueError(f"expected a SQLite backup at revision {SOURCE_REVISION}")
        target.execute("SELECT pg_advisory_xact_lock(7013421)")
        if target.execute("SELECT version_num FROM alembic_version").fetchall() != [(TARGET_REVISION,)]:
            raise ValueError(f"expected PostgreSQL baseline {TARGET_REVISION}")
        tables = Base.metadata.sorted_tables
        source_names = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if source_names - {"alembic_version", "sqlite_sequence"} != {table.name for table in tables}:
            raise ValueError("SQLite table list differs from the PostgreSQL baseline")
        # Prevent accidental application writes during the one-time import.
        target.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
            sql.SQL(", ").join(sql.Identifier(table.name) for table in tables)
        ))
        for table in tables:
            if target.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(table.name))).fetchone():
                raise ValueError(f"target table {table.name} is not empty")

        high_water = dict(source.execute("SELECT name, seq FROM sqlite_sequence")) if "sqlite_sequence" in source_names else {}
        target.execute("SET CONSTRAINTS ALL DEFERRED")
        counts = {}
        for table in tables:
            name = table.name
            columns = list(table.columns)
            source_columns = {row[1] for row in source.execute(f"PRAGMA table_info({_identifier(name)})")}
            if source_columns != {column.name for column in columns}:
                raise ValueError(f"column list differs in {name}")
            select_columns = ", ".join(_identifier(column.name) for column in columns)
            rows = source.execute(f"SELECT {select_columns} FROM {_identifier(name)}")
            copy = sql.SQL("COPY {} ({}) FROM STDIN").format(
                sql.Identifier(name), sql.SQL(", ").join(sql.Identifier(column.name) for column in columns)
            )
            count = 0
            try:
                with target.cursor().copy(copy) as stream:
                    for row in rows:
                        stream.write_row(tuple(_convert(column.type, value) for column, value in zip(columns, row, strict=True)))
                        count += 1
            except (ValueError, TypeError, psycopg.Error) as error:
                # COPY errors can include library data and credentials.
                line = error.diag.context.split(",")[0] if isinstance(error, psycopg.Error) and error.diag.context else ""
                raise RuntimeError(f"Failed to import {name} after reading {count} rows ({type(error).__name__}, {line})") from None
            actual = target.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(name))).fetchone()[0]
            if actual != count:
                raise ValueError(f"row count differs in {name}: SQLite {count}, PostgreSQL {actual}")
            counts[name] = count
            if "id" in table.primary_key.columns and len(table.primary_key.columns) == 1:
                _reset_sequence(target, name, high_water.get(name, 0))
        target.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Path to the SQLite backup. Connection comes from Hub settings.")
    args = parser.parse_args()
    try:
        counts = transfer(args.snapshot)
    except (ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    print(f"Imported {sum(counts.values())} rows across {len(counts)} tables.")


if __name__ == "__main__":
    main()
