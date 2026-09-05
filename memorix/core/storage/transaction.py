"""存储模块的显式事务入口；内部 CRUD 不得提前提交外层事务。"""

import sqlite3
from typing import ContextManager


class MetadataTransactionMixin:
    def transaction(self, *, immediate: bool = False) -> ContextManager[sqlite3.Connection]:
        if self._connection_manager is None:
            raise RuntimeError("MetadataStore 未连接数据库")
        return self._connection_manager.transaction(immediate=immediate)
