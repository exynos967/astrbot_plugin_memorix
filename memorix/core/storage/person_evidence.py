"""协调 AstrBot 段落删除、事实证据快照和画像失效。"""

import json
from typing import Any, Sequence


class PersonEvidenceMixin:
    def invalidate_person_evidence(self, paragraph_hashes: Sequence[str], *, conn: Any) -> None:
        people = set()
        for paragraph_hash in paragraph_hashes:
            paragraph = self.get_paragraph(paragraph_hash)
            if paragraph is None:
                continue
            metadata = paragraph.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = self._decode_metadata(metadata)
            raw_ids = metadata.get("person_ids", [])
            if isinstance(raw_ids, list):
                people.update(item.strip() for item in raw_ids if isinstance(item, str) and item.strip())
            person_id = metadata.get("person_id")
            if isinstance(person_id, str) and person_id.strip():
                people.add(person_id.strip())
        for person_id in people:
            self.enqueue_person_profile_refresh(person_id=person_id, reason="person_evidence_changed", conn=conn)

    def detach_paragraph_facts(self, paragraph_hashes: Sequence[str], *, recoverable: bool, conn: Any) -> None:
        self.invalidate_person_evidence(paragraph_hashes, conn=conn)
        # 每段独立保存，允许回收站只恢复同一删除批次中的部分段落。
        for paragraph_hash in paragraph_hashes:
            snapshot = self.detach_fact_evidence_for_paragraphs(
                [paragraph_hash], reason="paragraph_deleted", conn=conn,
            )
            if recoverable and snapshot["evidence"]:
                conn.execute(
                    "INSERT OR IGNORE INTO paragraph_fact_evidence_backups VALUES (?, ?)",
                    (paragraph_hash, json.dumps(snapshot, ensure_ascii=False)),
                )
            elif not recoverable:
                conn.execute("DELETE FROM paragraph_fact_evidence_backups WHERE paragraph_hash = ?", (paragraph_hash,))
            for claim in snapshot["claims"]:
                if claim["scope_type"] == "person":
                    self.enqueue_person_profile_refresh(
                        person_id=claim["scope_id"], reason="fact_evidence_deleted", conn=conn,
                    )

    def restore_paragraph_facts(self, paragraph_hash: str, *, conn: Any) -> None:
        row = conn.execute(
            "SELECT snapshot_json FROM paragraph_fact_evidence_backups WHERE paragraph_hash = ?", (paragraph_hash,),
        ).fetchone()
        if row is not None:
            snapshot = json.loads(row[0])
            self.restore_fact_evidence_snapshot(snapshot, conn=conn)
            conn.execute("DELETE FROM paragraph_fact_evidence_backups WHERE paragraph_hash = ?", (paragraph_hash,))
            for claim in snapshot["claims"]:
                if claim["scope_type"] == "person":
                    self.enqueue_person_profile_refresh(
                        person_id=claim["scope_id"], reason="fact_evidence_restored", conn=conn,
                    )
        self.invalidate_person_evidence([paragraph_hash], conn=conn)
