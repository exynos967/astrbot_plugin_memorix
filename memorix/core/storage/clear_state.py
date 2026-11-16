"""整库软删除时保留人物管理状态，恢复不覆盖后续人工编辑。"""

OVERRIDE_TABLES = ("person_profile_overrides", "person_profile_alias_overrides")


def clear_profile_state(store, conn):
    snapshot = {
        "claims": [],
        "overrides": {},
        "profiles": [dict(row) for row in conn.execute("SELECT * FROM person_profile_snapshots")],
    }
    for row in conn.execute("SELECT * FROM fact_claims WHERE status IN ('active', 'conflicted')").fetchall():
        claim = dict(row)
        deleted = store.retract_fact_claim(claim["claim_id"], reason="memory_scope_cleared")
        tx_row = conn.execute(
            "SELECT MAX(transition_id) FROM fact_transitions WHERE old_claim_id = ? OR new_claim_id = ?",
            (claim["claim_id"], claim["claim_id"]),
        ).fetchone()
        deleted_transition_id = int(tx_row[0]) if tx_row and tx_row[0] is not None else None
        paragraph_evidence = [
            item[0]
            for item in conn.execute(
                "SELECT evidence_id FROM fact_evidence WHERE claim_id = ? AND evidence_type = 'paragraph'",
                (claim["claim_id"],),
            )
        ]
        snapshot["claims"].append(
            {
                "row": claim,
                "deleted_updated_at": deleted["updated_at"],
                "deleted_transition_id": deleted_transition_id,
                "paragraph_evidence": paragraph_evidence,
            }
        )
    for table in OVERRIDE_TABLES:
        snapshot["overrides"][table] = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM person_profile_snapshots")
    return snapshot


def restore_profile_state(store, conn, snapshot):
    restored = 0
    for item in snapshot.get("claims", []):
        old = item["row"]
        claim = store.get_fact_claim(old["claim_id"])
        if claim is None or claim["status"] != "retracted" or claim["updated_at"] != item["deleted_updated_at"]:
            continue
        if "deleted_transition_id" in item and item["deleted_transition_id"] is not None:
            tx_row = conn.execute(
                "SELECT MAX(transition_id) FROM fact_transitions WHERE old_claim_id = ? OR new_claim_id = ?",
                (old["claim_id"], old["claim_id"]),
            ).fetchone()
            current_tx_id = int(tx_row[0]) if tx_row and tx_row[0] is not None else None
            if current_tx_id != item["deleted_transition_id"]:
                continue
        if any(
            not conn.execute("SELECT 1 FROM paragraphs WHERE hash = ? AND COALESCE(is_deleted, 0) = 0", (h,)).fetchone()
            for h in item.get("paragraph_evidence", [])
        ):
            continue
        store.restore_fact_claim(old["claim_id"], reason="memory_scope_restored")
        conn.execute("UPDATE fact_claims SET valid_to = ? WHERE claim_id = ?", (old["valid_to"], old["claim_id"]))
        if old["scope_type"] == "person":
            store.enqueue_person_profile_refresh(person_id=old["scope_id"], reason="memory_scope_restored", conn=conn)
        restored += 1
    for table in OVERRIDE_TABLES:
        for row in snapshot.get("overrides", {}).get(table, []):
            columns = {col[1] for col in conn.execute(f"PRAGMA table_info({table})")}
            row = {key: value for key, value in row.items() if key in columns}
            cursor = conn.execute(
                f"INSERT OR IGNORE INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                tuple(row.values()),
            )
            if cursor.rowcount:
                store.enqueue_person_profile_refresh(
                    person_id=row["person_id"], reason="memory_scope_restored", conn=conn
                )
                restored += 1
    for row in snapshot.get("profiles", []):
        row = {**row, "expires_at": 0}
        columns = {col[1] for col in conn.execute("PRAGMA table_info(person_profile_snapshots)")}
        row = {key: value for key, value in row.items() if key in columns}
        conn.execute(
            f"INSERT OR IGNORE INTO person_profile_snapshots ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
            tuple(row.values()),
        )
    return restored
