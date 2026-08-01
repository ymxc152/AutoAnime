"""Small explicit Unit of Work used by SQLite repositories."""

from .engine import connect_sqlite


class SqliteUnitOfWork:
    def __init__(self, database_path):
        self.database_path = database_path
        self.connection = None
        self.committed = False

    def __enter__(self):
        self.connection = connect_sqlite(self.database_path)
        self.connection.row_factory = __import__("sqlite3").Row
        self.connection.execute("BEGIN IMMEDIATE")
        return self

    def commit(self):
        self.connection.commit()
        self.committed = True

    def rollback(self):
        if self.connection is not None:
            self.connection.rollback()

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is not None or not self.committed:
                self.rollback()
        finally:
            self.connection.close()
            self.connection = None

