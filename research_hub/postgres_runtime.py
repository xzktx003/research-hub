"""Optional PostgreSQL runtime adapter for the Research Hub repository layer."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .postgres import DEFAULT_MIGRATIONS_DIR, PostgresMigrationRunner, split_sql_statements


class PostgresRuntimeDependencyError(RuntimeError):
    """Raised when PostgreSQL runtime mode is requested without a driver."""


class PostgresRuntimeSQLUnsupportedError(RuntimeError):
    """Raised when qmark SQL cannot be translated safely for PostgreSQL."""


class PostgresRow(Mapping[str, Any]):
    """Mapping row with sqlite3.Row-compatible name and index access."""

    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._index = {column: index for index, column in enumerate(self._columns)}

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._index[key]]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)

    def keys(self) -> list[str]:
        return list(self._columns)


def _runtime_dependency_error(dsn: str) -> PostgresRuntimeDependencyError:
    return PostgresRuntimeDependencyError(
        "RESEARCH_HUB_POSTGRES_DSN is set, but neither psycopg nor psycopg2 is installed. "
        "Install one PostgreSQL DB-API driver in the runtime environment, or unset "
        "RESEARCH_HUB_POSTGRES_DSN to use SQLite. DSN prefix: "
        f"{dsn.split(':', 1)[0]!r}"
    )


def connect_driver(dsn: str) -> Any:
    """Return a raw PostgreSQL DB-API connection using an installed driver."""

    try:
        import psycopg  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        psycopg = None
    if psycopg is not None:
        return psycopg.connect(dsn)

    try:
        import psycopg2  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise _runtime_dependency_error(dsn) from exc
    return psycopg2.connect(dsn)


def postgres_dsn_from_env() -> str | None:
    """Return the runtime PostgreSQL DSN, if configured."""

    return os.environ.get("RESEARCH_HUB_POSTGRES_DSN") or None


def is_postgres_runtime_enabled() -> bool:
    """Return whether the app should use PostgreSQL instead of SQLite."""

    return postgres_dsn_from_env() is not None


def create_database_from_env(sqlite_path: Path) -> Any:
    """Create the configured Database-compatible backend.

    This helper keeps SQLite as the default and switches only when
    ``RESEARCH_HUB_POSTGRES_DSN`` is set.
    """

    dsn = postgres_dsn_from_env()
    if dsn:
        return PostgresRuntimeDatabase(dsn)

    from .database import Database

    return Database(sqlite_path)


def translate_qmark_sql(sql: str, params: Sequence[Any] | None = None) -> tuple[str, tuple[Any, ...] | None]:
    """Translate sqlite qmark placeholders to PostgreSQL DB-API placeholders.

    Literal strings, quoted identifiers, dollar-quoted blocks, and SQL comments
    are preserved. ``expr IS ?`` is rewritten to ``expr IS NULL`` for ``None``
    and ``expr = %s`` otherwise because PostgreSQL does not allow ``IS $1``.
    """

    if params is None:
        if "?" in sql:
            raise PostgresRuntimeSQLUnsupportedError(
                "SQL contains qmark placeholders but no parameter sequence was provided"
            )
        return sql, None

    translated_params: list[Any] = []
    param_index = 0
    out: list[str] = []
    index = 0
    quote: str | None = None
    dollar_tag: str | None = None
    line_comment = False
    block_comment = False

    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""

        if line_comment:
            out.append(char)
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            out.append(char)
            if char == "*" and next_char == "/":
                out.append(next_char)
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, index):
                out.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
            else:
                out.append(char)
                index += 1
            continue
        if quote is not None:
            out.append(char)
            if char == quote:
                if next_char == quote:
                    out.append(next_char)
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if char == "-" and next_char == "-":
            out.extend((char, next_char))
            line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            out.extend((char, next_char))
            block_comment = True
            index += 2
            continue
        if char in {"'", '"'}:
            out.append(char)
            quote = char
            index += 1
            continue
        if char == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[index:])
            if match:
                dollar_tag = match.group(0)
                out.append(dollar_tag)
                index += len(dollar_tag)
                continue
        if char == "?":
            if param_index >= len(params):
                raise PostgresRuntimeSQLUnsupportedError(
                    "SQL contains more qmark placeholders than supplied parameters"
                )
            value = params[param_index]
            param_index += 1
            current = "".join(out)
            if re.search(r"\sIS\s$", current, flags=re.IGNORECASE):
                del out[-4:]
                if value is None:
                    out.append(" IS NULL")
                else:
                    out.append(" = %s")
                    translated_params.append(value)
            else:
                out.append("%s")
                translated_params.append(value)
            index += 1
            continue

        out.append(char)
        index += 1

    if param_index != len(params):
        raise PostgresRuntimeSQLUnsupportedError(
            "SQL contains fewer qmark placeholders than supplied parameters"
        )
    return "".join(out), tuple(translated_params)


class PostgresRuntimeCursor:
    def __init__(self, raw_cursor: Any) -> None:
        self._raw_cursor = raw_cursor

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> "PostgresRuntimeCursor":
        translated_sql, translated_params = translate_qmark_sql(sql, params)
        if translated_params is None:
            self._raw_cursor.execute(translated_sql)
        else:
            self._raw_cursor.execute(translated_sql, translated_params)
        return self

    def executemany(self, sql: str, seq_of_params: Sequence[Sequence[Any]]) -> "PostgresRuntimeCursor":
        for params in seq_of_params:
            self.execute(sql, params)
        return self

    def fetchone(self) -> PostgresRow | None:
        row = self._raw_cursor.fetchone()
        if row is None:
            return None
        return self._wrap_row(row)

    def fetchall(self) -> list[PostgresRow]:
        return [self._wrap_row(row) for row in self._raw_cursor.fetchall()]

    @property
    def rowcount(self) -> int:
        return self._raw_cursor.rowcount

    def close(self) -> None:
        close = getattr(self._raw_cursor, "close", None)
        if close is not None:
            close()

    def _wrap_row(self, row: Any) -> PostgresRow:
        if isinstance(row, PostgresRow):
            return row
        if isinstance(row, Mapping):
            return PostgresRow(tuple(row.keys()), tuple(row.values()))
        description = getattr(self._raw_cursor, "description", None)
        if not description:
            return PostgresRow(tuple(str(index) for index in range(len(row))), tuple(row))
        columns = tuple(column[0] for column in description)
        return PostgresRow(columns, tuple(row))


class PostgresRuntimeConnection:
    """sqlite3.Connection-shaped wrapper around a PostgreSQL DB-API connection."""

    def __init__(self, raw_connection: Any) -> None:
        self._raw_connection = raw_connection

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> PostgresRuntimeCursor:
        cursor = PostgresRuntimeCursor(self._raw_connection.cursor())
        return cursor.execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Sequence[Sequence[Any]]) -> PostgresRuntimeCursor:
        cursor = PostgresRuntimeCursor(self._raw_connection.cursor())
        return cursor.executemany(sql, seq_of_params)

    def executescript(self, sql: str) -> None:
        for statement in split_sql_statements(sql):
            self.execute(statement)

    def commit(self) -> None:
        self._raw_connection.commit()

    def rollback(self) -> None:
        self._raw_connection.rollback()

    def close(self) -> None:
        self._raw_connection.close()


class PostgresRuntimeDatabase:
    """Database-compatible PostgreSQL runtime backend."""

    def __init__(self, dsn: str, migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> None:
        self.dsn = dsn
        self.migrations_dir = migrations_dir

    def initialize(self) -> None:
        conn = connect_driver(self.dsn)
        try:
            PostgresMigrationRunner(conn=conn, migrations_dir=self.migrations_dir).apply()
        finally:
            conn.close()

    @contextmanager
    def connect(self) -> Iterator[PostgresRuntimeConnection]:
        conn = PostgresRuntimeConnection(connect_driver(self.dsn))
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

