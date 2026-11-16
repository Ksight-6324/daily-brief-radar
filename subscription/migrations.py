"""Initialize or upgrade the shared database in one serialized transaction."""

import sqlite3
from pathlib import Path


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
MIGRATION_PATH = Path(__file__).with_name("migration-v2.sql")
MIGRATION_V3_PATH = Path(__file__).with_name("migration-v3.sql")


def execute_script(conn, script):
    # executescript() implicitly commits. Complete statements preserve our outer transaction,
    # including multi-line trigger bodies, so an interrupted upgrade rolls back fully.
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("数据库脚本包含不完整语句")


def initialize(conn):
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > 3:
            raise ValueError("数据库版本高于当前程序支持版本")
        if version == 1:
            execute_script(conn, MIGRATION_PATH.read_text(encoding="utf-8"))
        if version in (1, 2):
            execute_script(conn, MIGRATION_V3_PATH.read_text(encoding="utf-8"))
        execute_script(conn, SCHEMA_PATH.read_text(encoding="utf-8"))
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("数据库外键校验失败")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
