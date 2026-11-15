import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone

from subscription.store import Store
from tests.support import NOW, StoreCase


class StoreTest(StoreCase):
    def test_confirmation_and_email_normalization(self):
        account = self.store.register(" Reader@Example.com ", [], NOW)
        self.assertEqual(self.store.subscriber(account["id"])["status"], "pending")
        self.store.confirm(account["confirmation_token"], NOW)
        reader = self.store.subscriber(account["id"])
        self.assertEqual(reader["email"], "reader@example.com")
        self.assertEqual(reader["trial_remaining"], 3)
        self.assertEqual(json.loads(reader["preferences"]), [])
        self.assertEqual(self.store.confirm(account["confirmation_token"], NOW), account["id"])

    def test_signup_cannot_overwrite_confirmed_preferences(self):
        account = self.subscriber()
        again = self.store.register("reader@example.com", ["jobs"], NOW)
        self.assertIsNone(again)
        self.assertEqual(json.loads(self.store.subscriber(account["id"])["preferences"]), ["markets"])

    def test_invalid_input_and_expired_confirmation(self):
        for email in ("", "invalid", "x\r\nTo: bad@example.com", "x@"):
            with self.subTest(email=email), self.assertRaises(ValueError):
                self.store.register(email, [], NOW)
        with self.assertRaises(ValueError):
            self.store.register("a@example.com", ["unknown"], NOW)
        account = self.store.register("a@example.com", [], NOW)
        with self.assertRaises(ValueError):
            self.store.confirm(account["confirmation_token"], datetime(2026, 11, 18, tzinfo=timezone.utc))

    def test_repeat_payment_extends_once_and_keeps_order_audit(self):
        account = self.subscriber()
        order = self.pay(account)
        self.store.confirm_payment(order["order_token"], "trade-1", 990, NOW)
        sub = self.store.subscription(account["id"])
        self.assertEqual(sub["expires_at"], "2026-12-15T11:00:00+00:00")
        self.assertEqual(self.store.order(order["order_token"])["status"], "paid")

    def test_early_and_expired_renewal(self):
        account = self.subscriber()
        self.pay(account)
        self.pay(account, "trade-2")
        self.assertEqual(self.store.subscription(account["id"])["expires_at"], "2027-01-14T11:00:00+00:00")
        later = datetime(2027, 2, 1, 11, tzinfo=timezone.utc)
        self.pay(account, "trade-3", later)
        self.assertEqual(self.store.subscription(account["id"])["expires_at"], "2027-03-03T11:00:00+00:00")

    def test_concurrent_distinct_payments_preserve_both_extensions(self):
        account = self.subscriber()
        orders = [self.store.create_order(account["id"], "monthly", NOW) for _ in range(2)]
        def confirm(index):
            store = Store(self.store.path)
            return store.confirm_payment(orders[index]["order_token"], f"trade-{index}", 990, NOW)
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(confirm, range(2)))
        self.assertEqual(self.store.subscription(account["id"])["expires_at"], "2027-01-14T11:00:00+00:00")

    def test_wrong_amount_or_reused_trade_rolls_back(self):
        account = self.subscriber()
        first = self.store.create_order(account["id"], "monthly", NOW)
        with self.assertRaises(ValueError):
            self.store.confirm_payment(first["order_token"], "trade-1", 1, NOW)
        self.assertIsNone(self.store.subscription(account["id"]))
        self.store.confirm_payment(first["order_token"], "trade-1", 990, NOW)
        second = self.store.create_order(account["id"], "monthly", NOW)
        with self.assertRaises(ValueError):
            self.store.confirm_payment(second["order_token"], "trade-1", 990, NOW)
        self.assertEqual(self.store.order(second["order_token"])["status"], "pending")

    def test_unmatched_payment_is_retained(self):
        self.store.confirm_payment("missing-order", "trade-x", 990, NOW)
        order = self.store.order("missing-order")
        self.assertEqual(order["status"], "unmatched")
        self.assertEqual(order["payment_ref"], "trade-x")
        self.assertIsNone(order["subscriber_id"])

    def test_payment_does_not_bypass_confirmation_or_unsubscribe(self):
        account = self.store.register("pending@example.com", [], NOW)
        self.pay(account)
        self.assertEqual(self.store.subscriber(account["id"])["status"], "pending")
        self.store.confirm(account["confirmation_token"], NOW)
        self.store.unsubscribe(account["manage_token"], NOW)
        self.pay(account, "trade-2")
        self.assertEqual(self.store.subscriber(account["id"])["status"], "unsubscribed")

    def test_snapshot_is_immutable_and_has_all_mvp_sections(self):
        sections = {"markets": "市场新闻", "ai": "人工智能", "jobs": "就业新闻"}
        first = self.store.freeze_issue("2026-11-15", sections, NOW)
        again = self.store.freeze_issue("2026-11-15", sections, NOW)
        self.assertEqual(first, again)
        with self.assertRaises(ValueError):
            self.store.freeze_issue("2026-11-15", {**sections, "jobs": "修改"}, NOW)
        with self.assertRaises(ValueError):
            self.store.freeze_issue("2026-11-16", {"markets": "新闻"}, NOW)
        with closing(sqlite3.connect(self.store.path)) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE daily_issue SET sections = '{}' WHERE id = ?", (first,))

    def test_direct_expiry_updates_and_old_confirmation_after_unsubscribe_are_rejected(self):
        account = self.subscriber()
        self.pay(account)
        with self.store.transaction() as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE subscriptions SET expires_at='2099-01-01T00:00:00+00:00'")
        self.store.unsubscribe(account["manage_token"], NOW)
        with self.assertRaises(ValueError):
            self.store.confirm(account["confirmation_token"], NOW)

    def test_preferences_and_unsubscribe_require_management_token(self):
        account = self.subscriber()
        self.store.set_preferences(account["manage_token"], [], NOW)
        self.assertEqual(json.loads(self.store.subscriber(account["id"])["preferences"]), [])
        with self.assertRaises(ValueError):
            self.store.set_preferences("bad-token", ["jobs"], NOW)
        self.store.unsubscribe(account["manage_token"], NOW)
        self.assertEqual(self.store.subscriber(account["id"])["status"], "unsubscribed")


if __name__ == "__main__":
    unittest.main()
