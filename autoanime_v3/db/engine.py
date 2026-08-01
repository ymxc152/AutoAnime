"""SQLite engine and connection configuration."""

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, URL


BUSY_TIMEOUT_MS = 10000


def _configure_dbapi_connection(connection, connection_record=None):
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def create_engine_for_path(database_path):
    path = Path(database_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        future=True,
        connect_args={"check_same_thread": False, "timeout": BUSY_TIMEOUT_MS / 1000},
    )
    event.listen(engine, "connect", _configure_dbapi_connection)
    return engine


def connect_sqlite(database_path):
    path = Path(database_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000)
    _configure_dbapi_connection(connection)
    return connection
