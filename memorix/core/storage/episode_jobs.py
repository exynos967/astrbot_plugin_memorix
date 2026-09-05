"""Episode 重建队列的版本、租约和原子发布协议。"""

import time
import uuid


class EpisodeJobsMixin:
    def enqueue_episode_rebuilds(self, sources, reason=""):
        sources = self._dedupe_episode_sources(sources)
        now = time.time()
        with self.transaction(immediate=True) as conn:
            conn.executemany(
                """INSERT INTO episode_rebuild_sources
                    (source, status, retry_count, reason, requested_at, updated_at,
                     desired_revision, built_revision, next_attempt_at)
                   VALUES (?, 'pending', 0, ?, ?, ?, 1, 0, 0)
                   ON CONFLICT(source) DO UPDATE SET
                     desired_revision = desired_revision + 1,
                     status = CASE WHEN lease_until > excluded.requested_at
                                   THEN 'running' ELSE 'pending' END,
                     retry_count = 0, last_error = NULL, next_attempt_at = 0,
                     reason = excluded.reason, requested_at = excluded.requested_at,
                     updated_at = excluded.updated_at""",
                [(source, str(reason)[:200], now, now) for source in sources],
            )
        return len(sources)

    def claim_episode_rebuild(self, generation_hash, *, source=None, max_retry=3, lease_seconds=120):
        now = time.time()
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                """SELECT * FROM episode_rebuild_sources
                   WHERE (? IS NULL OR source = ?)
                     AND (desired_revision > built_revision OR COALESCE(built_generation_hash, '') != ?)
                     AND COALESCE(lease_until, 0) <= ?
                     AND COALESCE(next_attempt_at, 0) <= ?
                     AND (retry_count < ? OR status = 'pending'
                          OR COALESCE(retry_generation_hash, '') != ?)
                   ORDER BY requested_at LIMIT 1""",
                (source, source, generation_hash, now, now, max(1, max_retry), generation_hash),
            ).fetchone()
            if row is None:
                return None
            job = dict(row)
            job.update(
                lease_token=uuid.uuid4().hex,
                claimed_revision=job["desired_revision"],
                claimed_generation_hash=generation_hash,
            )
            conn.execute(
                """UPDATE episode_rebuild_sources SET status = 'running', lease_token = ?,
                   lease_until = ?, claimed_revision = ?, claimed_generation_hash = ?, updated_at = ?
                   WHERE source = ?""",
                (job["lease_token"], now + lease_seconds, job["claimed_revision"], generation_hash, now, job["source"]),
            )
            return job

    def renew_episode_rebuild(self, job, lease_seconds=120):
        now = time.time()
        with self.transaction(immediate=True) as conn:
            result = conn.execute(
                """UPDATE episode_rebuild_sources SET lease_until = ?, updated_at = ?
                   WHERE source = ? AND lease_token = ? AND lease_until > ?
                     AND desired_revision = ?""",
                (now + lease_seconds, now, job["source"], job["lease_token"], now, job["claimed_revision"]),
            )
            return result.rowcount == 1

    def publish_episode_rebuild(self, job, payloads, generation_hash):
        now = time.time()
        with self.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM episode_rebuild_sources WHERE source = ?", (job["source"],)).fetchone()
            if row is None or row["lease_token"] != job["lease_token"]:
                return {"status": "superseded", "episode_count": 0}
            if (
                row["desired_revision"] != job["claimed_revision"]
                or (row["lease_until"] or 0) <= now
                or generation_hash != job["claimed_generation_hash"]
            ):
                self._release_episode_job(conn, job, now)
                return {"status": "superseded", "episode_count": 0}
            result = self.replace_episodes_for_source(job["source"], payloads)
            conn.execute(
                """UPDATE episode_rebuild_sources SET status = 'done', built_revision = ?,
                   built_generation_hash = ?, lease_token = NULL, lease_until = NULL,
                   retry_count = 0, last_error = NULL, next_attempt_at = 0, updated_at = ?
                   WHERE source = ? AND lease_token = ?""",
                (job["claimed_revision"], generation_hash, now, job["source"], job["lease_token"]),
            )
            return {**result, "status": "done"}

    @staticmethod
    def _release_episode_job(conn, job, now):
        conn.execute(
            """UPDATE episode_rebuild_sources SET status = 'pending', lease_token = NULL,
               lease_until = NULL, next_attempt_at = 0, updated_at = ?
               WHERE source = ? AND lease_token = ?""",
            (now, job["source"], job["lease_token"]),
        )

    def fail_episode_rebuild(self, job, error=None):
        now = time.time()
        with self.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM episode_rebuild_sources WHERE source = ?", (job["source"],)).fetchone()
            if row is None or row["lease_token"] != job["lease_token"]:
                return
            if error is None or row["desired_revision"] != job["claimed_revision"]:
                self._release_episode_job(conn, job, now)
                return
            attempts = (row["retry_count"] if row["retry_generation_hash"] == job["claimed_generation_hash"] else 0) + 1
            conn.execute(
                """UPDATE episode_rebuild_sources SET status = 'failed', retry_count = ?,
                   last_error = ?, retry_generation_hash = ?, lease_token = NULL, lease_until = NULL,
                   next_attempt_at = ?, updated_at = ? WHERE source = ? AND lease_token = ?""",
                (
                    attempts,
                    str(error)[:500],
                    job["claimed_generation_hash"],
                    now + min(300, 2 ** min(attempts, 8)),
                    now,
                    job["source"],
                    job["lease_token"],
                ),
            )
