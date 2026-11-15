import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from subscription.store import Store
from tests.support import NOW, StoreCase


class MigrationTest(StoreCase):
    def legacy_database(self):
        path = self.store.path.parent / "legacy.db"
        schema = Path(__file__).with_name("fixtures").joinpath("schema-v1.sql").read_text(encoding="utf-8")
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.executescript(schema)
            conn.execute("""INSERT INTO subscribers(id,email,status,token_nonce,confirmation_expires_at,created_at,confirmed_at)
                            VALUES('reader','legacy@example.com','active','nonce',?,?,?)""", (NOW.isoformat(),) * 3)
            conn.execute("""INSERT INTO orders(id,order_token,subscriber_id,plan,amount,payment_ref,status,created_at,applied_expires_at)
                            VALUES('paid','old-token','reader','monthly',990,'ali-old','paid',?,'2026-12-15T11:00:00+00:00')""", (NOW.isoformat(),))
            conn.execute("INSERT INTO subscriptions VALUES('reader','active','2026-12-15T11:00:00+00:00','paid',?)", (NOW.isoformat(),))
            conn.execute("INSERT INTO daily_issue VALUES('old-issue','2026-11-15',?,?)", (json.dumps({"markets": "旧新闻"}), NOW.isoformat()))
            for status in ("pending", "success", "sending"):
                conn.execute("""INSERT INTO delivery_log(id,subscriber_id,task_type,business_key,issue_id,status,payload,idempotency_key,created_at,next_attempt_at)
                                VALUES(?,'reader','daily_brief',?,'old-issue',?,'{}',?,?,?)""",
                             (status, status, status, status, NOW.isoformat(), NOW.isoformat()))
        return path

    def test_migration_preserves_paid_expiry_snapshots_and_success_and_allows_weekly_renewal(self):
        store = Store(self.legacy_database())
        with store.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(conn.execute("SELECT edition FROM daily_issue WHERE id='old-issue'").fetchone()[0], "legacy")
            statuses = dict(conn.execute("SELECT id,status FROM delivery_log"))
        self.assertEqual(statuses, {"pending": "skipped", "success": "success", "sending": "needs_review"})
        self.assertEqual(store.subscription("reader")["expiry_order_id"], "paid")
        order = store.create_order("reader", "weekly", NOW)
        store.confirm_payment(order["order_token"], "ali-weekly", 300, NOW)
        self.assertEqual(store.subscription("reader")["expires_at"], "2026-12-22T11:00:00+00:00")
        with store.transaction() as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE subscriptions SET expires_at='2099-01-01'")

    def test_concurrent_startup_migrates_once_and_failed_migration_rolls_back(self):
        path = self.legacy_database()
        broken = path.parent / "broken.sql"
        broken.write_text("DROP TABLE orders;\nINVALID SQL;\n", encoding="utf-8")
        with patch("subscription.migrations.MIGRATION_PATH", broken), self.assertRaises(sqlite3.OperationalError):
            Store(path)
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT payment_ref FROM orders").fetchone()[0], "ali-old")
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: Store(path), range(2)))
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
