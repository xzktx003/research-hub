"""PostgreSQL migration runner and SQLite migration evidence helpers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MIGRATIONS_DIR = PROJECT_ROOT / "migrations" / "postgresql"
EXPECTED_SCHEMA_VERSION = "6"
SQLITE_ONLY_TABLES = {"sqlite_sequence"}
POSTGRES_RUNTIME_TABLES = {"schema_migrations"}


class Cursor(Protocol):
    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Any: ...
    def commit(self) -> Any: ...
    def rollback(self) -> Any: ...
    def close(self) -> Any: ...


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path
    checksum: str
    sql: str


@dataclass(frozen=True)
class MigrationPlan:
    migrations: tuple[Migration, ...]
    pending: tuple[Migration, ...]
    applied_versions: tuple[str, ...]

    @property
    def plan_checksum(self) -> str:
        payload = "\n".join(f"{migration.version}:{migration.checksum}" for migration in self.migrations)
        return sha256_text(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "migration_count": len(self.migrations),
            "pending_count": len(self.pending),
            "applied_versions": list(self.applied_versions),
            "plan_checksum": self.plan_checksum,
            "migrations": [
                {
                    "version": migration.version,
                    "name": migration.name,
                    "checksum": migration.checksum,
                    "statement_count": len(split_sql_statements(migration.sql)),
                    "pending": migration in self.pending,
                    "path": str(migration.path),
                }
                for migration in self.migrations
            ],
        }


@dataclass(frozen=True)
class SchemaDiff:
    sqlite_path: Path
    sqlite_checksum: str | None
    postgres_plan_checksum: str
    missing_tables: tuple[str, ...]
    extra_tables: tuple[str, ...]
    column_mismatches: dict[str, dict[str, tuple[str, ...]]]

    @property
    def ok(self) -> bool:
        return not self.missing_tables and not self.column_mismatches

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "sqlite_path": str(self.sqlite_path),
            "sqlite_checksum": self.sqlite_checksum,
            "postgres_plan_checksum": self.postgres_plan_checksum,
            "missing_tables": list(self.missing_tables),
            "extra_tables": list(self.extra_tables),
            "column_mismatches": {
                table: {
                    side: list(columns)
                    for side, columns in mismatch.items()
                }
                for table, mismatch in self.column_mismatches.items()
            },
        }


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_migrations(migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> tuple[Migration, ...]:
    migrations: list[Migration] = []
    for path in sorted(migrations_dir.glob("[0-9][0-9][0-9]_*.sql")):
        version, name = path.stem.split("_", 1)
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=version,
                name=name,
                path=path,
                checksum=sha256_text(sql),
                sql=sql,
            )
        )
    if not migrations:
        raise RuntimeError(f"No PostgreSQL migrations found in {migrations_dir}")
    return tuple(migrations)


def split_sql_statements(sql: str) -> tuple[str, ...]:
    """Split SQL on statement semicolons while preserving quoted bodies."""

    statements: list[str] = []
    start = 0
    index = 0
    quote: str | None = None
    dollar_tag: str | None = None
    line_comment = False
    block_comment = False

    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""

        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, index):
                index += len(dollar_tag)
                dollar_tag = None
            else:
                index += 1
            continue
        if quote is not None:
            if char == quote:
                if next_char == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if char == "-" and next_char == "-":
            line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[index:])
            if match:
                dollar_tag = match.group(0)
                index += len(dollar_tag)
                continue
        if char == ";":
            statement = sql[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1

    tail = sql[start:].strip()
    if tail:
        statements.append(tail)
    return tuple(statements)


def connect_from_env(database_url: str | None = None) -> Connection:
    """Create a PostgreSQL connection using an installed driver.

    No PostgreSQL driver is required for dry-run/diff tests. Live connections
    support psycopg v3 first, then psycopg2 when either package is available.
    """

    dsn = database_url or os.environ.get("RESEARCH_HUB_POSTGRES_DSN") or os.environ.get(
        "DATABASE_URL"
    )
    if not dsn:
        raise RuntimeError("Set RESEARCH_HUB_POSTGRES_DSN or DATABASE_URL for PostgreSQL access")

    try:
        import psycopg  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        psycopg = None
    if psycopg is not None:
        return psycopg.connect(dsn)

    try:
        import psycopg2  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PostgreSQL live mode requires psycopg or psycopg2; dry-run mode has no driver dependency"
        ) from exc
    return psycopg2.connect(dsn)


@contextmanager
def cursor_for(conn: Connection) -> Iterator[Cursor]:
    cursor = conn.cursor()
    try:
        yield cursor
    finally:
        close = getattr(cursor, "close", None)
        if close is not None:
            close()


class PostgresMigrationRunner:
    def __init__(
        self,
        conn: Connection | None = None,
        migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
    ) -> None:
        self.conn = conn
        self.migrations_dir = migrations_dir

    def plan(self) -> MigrationPlan:
        migrations = load_migrations(self.migrations_dir)
        applied = self.applied_versions() if self.conn is not None else ()
        pending = tuple(migration for migration in migrations if migration.version not in applied)
        return MigrationPlan(migrations=migrations, pending=pending, applied_versions=applied)

    def applied_versions(self) -> tuple[str, ...]:
        if self.conn is None:
            return ()
        with cursor_for(self.conn) as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
            return tuple(row[0] for row in cursor.fetchall())

    def apply(self, dry_run: bool = False) -> MigrationPlan:
        plan = self.plan()
        if dry_run:
            return plan
        if self.conn is None:
            raise RuntimeError("A PostgreSQL connection is required unless dry_run=True")

        try:
            with cursor_for(self.conn) as cursor:
                for migration in plan.pending:
                    for statement in split_sql_statements(migration.sql):
                        cursor.execute(statement)
                    cursor.execute(
                        """
                        INSERT INTO schema_migrations (version, name, checksum)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (version) DO UPDATE SET
                            name = EXCLUDED.name,
                            checksum = EXCLUDED.checksum
                        """,
                        (migration.version, migration.name, migration.checksum),
                    )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return plan

    def smoke_check(self) -> dict[str, Any]:
        if self.conn is None:
            raise RuntimeError("A PostgreSQL connection is required for smoke_check")
        required_tables = (
            "schema_migrations",
            "schema_meta",
            "paper",
            "paper_version",
            "artifact",
            "job",
            "invention_candidate",
            "patent_draft",
            "topic_config_version",
            "topic_alias",
            "topic_quota",
            "topic_digest_note",
            "author",
            "organization",
            "venue",
            "paper_author",
            "author_organization",
            "paper_venue",
            "pipeline_run",
            "candidate_component",
            "integration_mechanism_record",
            "patent_stage_run",
        )
        with cursor_for(self.conn) as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                """
            )
            tables = {row[0] for row in cursor.fetchall()}
            cursor.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'")
            row = cursor.fetchone()
        missing = sorted(set(required_tables) - tables)
        return {
            "ok": not missing and row is not None and row[0] == EXPECTED_SCHEMA_VERSION,
            "missing_tables": missing,
            "schema_version": row[0] if row else None,
        }


def extract_postgres_tables(
    migrations: tuple[Migration, ...] | None = None,
) -> dict[str, set[str]]:
    migrations = migrations or load_migrations()
    sql = "\n".join(migration.sql for migration in migrations)
    tables: dict[str, set[str]] = {}
    for match in re.finditer(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\);",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        table = match.group(1)
        columns: set[str] = set()
        for raw_line in match.group(2).splitlines():
            line = raw_line.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            first = line.split(maxsplit=1)[0].strip('"')
            constraint_keyword = first.split("(", 1)[0].upper()
            if constraint_keyword in {
                "CHECK",
                "CONSTRAINT",
                "FOREIGN",
                "PRIMARY",
                "UNIQUE",
            }:
                continue
            columns.add(first)
        tables[table] = columns
    for match in re.finditer(
        r"ALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s+"
        r"ADD\s+COLUMN(?:\s+IF\s+NOT\s+EXISTS)?\s+([A-Za-z_][A-Za-z0-9_]*)",
        sql,
        flags=re.IGNORECASE,
    ):
        tables.setdefault(match.group(1), set()).add(match.group(2))
    return tables


def extract_sqlite_tables(sqlite_path: Path) -> dict[str, set[str]]:
    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        tables: dict[str, set[str]] = {}
        for row in rows:
            table = row["name"]
            columns = {
                column["name"]
                for column in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            tables[table] = columns
    return tables


def diff_sqlite_to_postgres(
    sqlite_path: Path,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
) -> SchemaDiff:
    migrations = load_migrations(migrations_dir)
    sqlite_tables = extract_sqlite_tables(sqlite_path)
    postgres_tables = extract_postgres_tables(migrations)
    comparable_postgres = {
        table: columns
        for table, columns in postgres_tables.items()
        if table not in POSTGRES_RUNTIME_TABLES
    }

    missing_tables = tuple(sorted(set(sqlite_tables) - set(comparable_postgres)))
    extra_tables = tuple(
        sorted((set(comparable_postgres) - set(sqlite_tables)) - SQLITE_ONLY_TABLES)
    )
    column_mismatches: dict[str, dict[str, tuple[str, ...]]] = {}
    for table in sorted(set(sqlite_tables) & set(comparable_postgres)):
        sqlite_columns = sqlite_tables[table]
        postgres_columns = comparable_postgres[table]
        missing_columns = tuple(sorted(sqlite_columns - postgres_columns))
        extra_columns = tuple(sorted(postgres_columns - sqlite_columns))
        if missing_columns or extra_columns:
            column_mismatches[table] = {
                "missing_in_postgres": missing_columns,
                "extra_in_postgres": extra_columns,
            }

    plan = MigrationPlan(migrations=migrations, pending=migrations, applied_versions=())
    return SchemaDiff(
        sqlite_path=sqlite_path,
        sqlite_checksum=sha256_file(sqlite_path) if sqlite_path.exists() else None,
        postgres_plan_checksum=plan.plan_checksum,
        missing_tables=missing_tables,
        extra_tables=extra_tables,
        column_mismatches=column_mismatches,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=DEFAULT_MIGRATIONS_DIR,
        help="Directory containing PostgreSQL migration SQL files.",
    )
    parser.add_argument("--database-url", help="PostgreSQL DSN. Defaults to env.")
    parser.add_argument("--dry-run", action="store_true", help="Print migration plan only.")
    parser.add_argument(
        "--sqlite",
        type=Path,
        help="Optional SQLite database to compare against the PostgreSQL schema contract.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    conn = None if args.dry_run else connect_from_env(args.database_url)
    runner = PostgresMigrationRunner(conn=conn, migrations_dir=args.migrations_dir)
    try:
        plan = runner.apply(dry_run=args.dry_run)
        payload: dict[str, Any] = {"plan": plan.to_dict()}
        if args.sqlite is not None:
            payload["sqlite_diff"] = diff_sqlite_to_postgres(
                args.sqlite,
                migrations_dir=args.migrations_dir,
            ).to_dict()
        if conn is not None:
            payload["smoke"] = runner.smoke_check()
    finally:
        if conn is not None:
            conn.close()

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"PostgreSQL migrations: {payload['plan']['migration_count']}")
        print(f"Pending migrations: {payload['plan']['pending_count']}")
        print(f"Plan checksum: {payload['plan']['plan_checksum']}")
        if "sqlite_diff" in payload:
            diff = payload["sqlite_diff"]
            print(f"SQLite diff ok: {diff['ok']}")
            print(f"SQLite checksum: {diff['sqlite_checksum']}")
        if "smoke" in payload:
            print(f"Smoke ok: {payload['smoke']['ok']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
