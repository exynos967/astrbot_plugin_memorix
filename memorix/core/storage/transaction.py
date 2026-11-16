import sqlite3
from typing import Optional, Type

from .sqlite_connection import ManagedSQLiteConnection


class ConnectionTransaction:
    """为外部注入的 SQLite 连接提供统一事务语义。"""

    def __init__(self, connection: sqlite3.Connection, *, immediate: bool = False) -> None:
        if not isinstance(connection, ManagedSQLiteConnection):
            raise TypeError("外部 SQLite 连接不支持受管事务；请注入 ManagedSQLiteConnection")
        self.connection = connection
        self.immediate = immediate
        self._savepoint_name: Optional[str] = None
        self._scope_depth: int = 0
        self._prev_isolation_level: Optional[str] = ""
        self._changed_isolation: bool = False

    def __enter__(self) -> sqlite3.Connection:
        if self.connection.in_transaction:
            self._savepoint_name = f"a_memorix_override_{id(self)}"
            self.connection.execute(f"SAVEPOINT {self._savepoint_name}")
        else:
            self._prev_isolation_level = self.connection.isolation_level
            if self._prev_isolation_level is not None:
                self.connection.isolation_level = None
                self._changed_isolation = True
            try:
                self.connection.execute("BEGIN IMMEDIATE" if self.immediate else "BEGIN")
            except BaseException:
                if self._changed_isolation:
                    self.connection.isolation_level = self._prev_isolation_level
                    self._changed_isolation = False
                raise
        self._scope_depth = self.connection.begin_managed_scope()
        return self.connection

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: object,
    ) -> bool:
        try:
            rollback_requested = self.connection.end_managed_scope(self._scope_depth)
            if exc_type is not None or rollback_requested:
                if self._savepoint_name is not None:
                    self.connection.execute(f"ROLLBACK TO SAVEPOINT {self._savepoint_name}")
                    self.connection.execute(f"RELEASE SAVEPOINT {self._savepoint_name}")
                elif self.connection.isolation_level is None:
                    if self.connection.in_transaction:
                        self.connection.execute("ROLLBACK")
                else:
                    self.connection.force_rollback()
                if exc_type is None and rollback_requested:
                    raise RuntimeError("事务中的操作请求了回滚")
            elif self._savepoint_name is not None:
                self.connection.execute(f"RELEASE SAVEPOINT {self._savepoint_name}")
            elif self.connection.isolation_level is None:
                if self.connection.in_transaction:
                    self.connection.execute("COMMIT")
            else:
                self.connection.force_commit()
        finally:
            if self._changed_isolation:
                self.connection.isolation_level = self._prev_isolation_level
                self._changed_isolation = False
        return False
