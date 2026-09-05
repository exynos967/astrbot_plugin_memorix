"""从 A_memorix schema 22 移植的人物别名存储。"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional


class MetadataProfileAliasMixin:
    def find_person_ids_by_manual_alias(self, alias: str) -> List[str]:
        token = alias.strip().casefold()
        if not token:
            return []
        matches = []
        for row in self._conn.execute("SELECT person_id, aliases_json FROM person_profile_alias_overrides"):
            if token in {value.casefold() for value in json.loads(row[1])}:
                matches.append(str(row[0]))
        return matches

    @staticmethod
    def _normalize_person_profile_aliases(aliases: List[str]) -> List[str]:
        """校验并按大小写无关规则去重人工别名。"""
        if not isinstance(aliases, list):
            raise ValueError("aliases 必须是字符串列表")

        normalized: List[str] = []
        seen = set()
        for raw_alias in aliases:
            if not isinstance(raw_alias, str):
                raise ValueError("aliases 中的每一项都必须是字符串")
            alias = raw_alias.strip()
            if not alias:
                continue
            if len(alias) > 128:
                raise ValueError("单个别名不能超过 128 个字符")
            key = alias.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(alias)

        if not normalized:
            raise ValueError("至少需要保留一个人物别名")
        if len(normalized) > 100:
            raise ValueError("人物别名不能超过 100 个")
        return normalized


    def get_person_profile_alias_override(self, person_id: str) -> Optional[Dict[str, Any]]:
        """读取人工维护的人物别名集合。"""
        token = str(person_id or "").strip()
        if not token:
            return None
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT person_id, aliases_json, updated_at, updated_by, source
            FROM person_profile_alias_overrides
            WHERE person_id = ?
            LIMIT 1
            """,
            (token,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        aliases = json.loads(str(row[1] or "[]"))
        if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
            raise RuntimeError(f"人物别名覆盖数据格式错误: person_id={token}")
        return {
            "person_id": str(row[0]),
            "aliases": aliases,
            "updated_at": row[2],
            "updated_by": str(row[3] or ""),
            "source": str(row[4] or ""),
        }


    def set_person_profile_alias_override(
        self,
        *,
        person_id: str,
        aliases: List[str],
        updated_by: str = "",
        source: str = "webui",
        updated_at: Optional[float] = None,
        conn: Any = None,
    ) -> Dict[str, Any]:
        """写入完整的人工别名集合，保存后该集合优先于自动推导结果。"""
        token = str(person_id or "").strip()
        if not token:
            raise ValueError("person_id 不能为空")
        normalized = self._normalize_person_profile_aliases(aliases)
        ts = float(updated_at) if updated_at is not None else datetime.now().timestamp()
        connection = self._resolve_conn(conn)
        connection.execute(
            """
            INSERT INTO person_profile_alias_overrides (
                person_id, aliases_json, updated_at, updated_by, source
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(person_id) DO UPDATE SET
                aliases_json = excluded.aliases_json,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by,
                source = excluded.source
            """,
            (
                token,
                json.dumps(normalized, ensure_ascii=False),
                ts,
                str(updated_by or ""),
                str(source or ""),
            ),
        )
        if conn is None:
            connection.commit()
        return {
            "person_id": token,
            "aliases": normalized,
            "updated_at": ts,
            "updated_by": str(updated_by or ""),
            "source": str(source or ""),
        }


    def delete_person_profile_alias_override(self, person_id: str, *, conn: Any = None) -> bool:
        """清除人工别名集合，使画像重新使用自动推导别名。"""
        token = str(person_id or "").strip()
        if not token:
            return False
        connection = self._resolve_conn(conn)
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM person_profile_alias_overrides WHERE person_id = ?",
            (token,),
        )
        if conn is None:
            connection.commit()
        return cursor.rowcount > 0
