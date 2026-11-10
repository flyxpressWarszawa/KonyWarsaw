"""Offline SQL-state fixture for RemoteStore paid-lease patch proposal.

NOT a WordPress/PHP integration test. These tests verify the proposed SQL
transitions against synthetic rows and that both claim paths are covered.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
import unittest


PATCH = (
    Path(__file__).resolve().parents[1] / "patches"
    / "WORDPRESS-REMOTESTORE-LEASE-NO-PAID-REQUEUE-20261111.diff"
)
UNCERTAIN_KINDS = ("legacy.llm80.train", "llm.generate")


def simulated_cleanup(db: sqlite3.Connection, now: int):
    db.execute(
        "UPDATE jobs SET state='uncertain', updated_at=? "
        "WHERE state='running' AND kind IN (?,?) AND lease_until<?",
        (now, *UNCERTAIN_KINDS, now),
    )
    db.execute(
        "UPDATE jobs SET state='pending', node='', lease_hash='', updated_at=? "
        "WHERE state='running' AND kind NOT IN (?,?) AND attempts<4 AND lease_until<?",
        (now, *UNCERTAIN_KINDS, now),
    )
    db.execute(
        "UPDATE jobs SET state='failed', updated_at=? "
        "WHERE state='running' AND attempts>=4 AND lease_until<?",
        (now, now),
    )


class RemoteStoreLeaseProposalTest(unittest.TestCase):
    def test_patch_covers_both_plain_claim_and_seo_batch_paths(self):
        text = PATCH.read_text(encoding="utf-8")
        self.assertEqual(text.count("+        $wpdb->query("), 4)
        self.assertEqual(text.count("kind IN ('legacy.llm80.train','llm.generate')"), 2)
        self.assertEqual(text.count("kind NOT IN ('legacy.llm80.train','llm.generate')"), 2)
        self.assertEqual(text.count("kind<>'legacy.llm80.train'"), 2)
        self.assertNotIn("sk-sp-", text)
        self.assertNotIn("MSW_ALIYUN_TOKEN_PLAN_API_KEY", text)

    def test_expired_paid_job_is_uncertain_not_pending_or_failed(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE jobs (id TEXT,kind TEXT,state TEXT,node TEXT,lease_hash TEXT,"
            "attempts INT,lease_until INT,updated_at INT)"
        )
        rows = [
            ("paid_expired", "llm.generate", "running", "worker1", "secret-hash", 1, 100, 1),
            ("paid_many_attempts", "llm.generate", "running", "worker1", "secret-hash", 5, 100, 1),
            ("training", "legacy.llm80.train", "running", "worker1", "secret-hash", 1, 100, 1),
            ("seo", "seo.analyze", "running", "worker2", "h2", 2, 100, 1),
            ("seo_exhausted", "seo.analyze", "running", "worker2", "h2", 4, 100, 1),
            ("paid_fresh", "llm.generate", "running", "worker1", "h1", 1, 2000, 1),
            ("paid_ack", "llm.generate", "completed", "worker1", "h1", 1, 0, 1),
            ("pending_paid", "llm.generate", "pending", "", "", 0, 0, 1),
        ]
        conn.executemany("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)", rows)
        simulated_cleanup(conn, 1000)
        got = {
            id_: (state, node, lease_hash) for id_, state, node, lease_hash
            in conn.execute("SELECT id,state,node,lease_hash FROM jobs")
        }
        self.assertEqual(got["paid_expired"], ("uncertain", "worker1", "secret-hash"))
        self.assertEqual(got["paid_many_attempts"], ("uncertain", "worker1", "secret-hash"))
        self.assertEqual(got["training"][0], "uncertain")
        self.assertEqual(got["seo"], ("pending", "", ""))
        self.assertEqual(got["seo_exhausted"][0], "failed")
        self.assertEqual(got["paid_fresh"][0], "running")
        self.assertEqual(got["paid_ack"][0], "completed")
        self.assertEqual(got["pending_paid"][0], "pending")
        # A second batch cleanup does not requeue an already-uncertain paid row.
        simulated_cleanup(conn, 1001)
        self.assertEqual(
            conn.execute("SELECT state FROM jobs WHERE id='paid_expired'").fetchone()[0],
            "uncertain"
        )


if __name__ == "__main__":
    unittest.main()
