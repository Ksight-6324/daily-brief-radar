import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone

from subscription.store import SECTIONS, Store
from tests.support import NOW, StoreCase


class StoreTest(StoreCase):
    def test_all_sixteen_topics_can_be_selected_and_cleared(self):
        self.assertEqual(list(SECTIONS.values()), [
            "股票经济", "石油黄金", "政治军事", "中外历史", "音乐演出", "设计美术", "时尚穿搭", "专辑影视",
            "人工智能", "自我提升", "物理化学", "生物科技", "心灵疗养", "就业机会", "体育健康", "电竞游戏",
        ])
        account = self.store.register("topics@example.com", list(SECTIONS), NOW)
        self.store.confirm(account["confirmation_token"], NOW)
        self.assertEqual(set(json.loads(self.store.subscriber(account["id"])["preferences"])), set(SECTIONS))
        self.store.set_preferences(account["manage_token"], [], NOW)
        self.assertEqual(json.loads(self.store.subscriber(account["id"])["preferences"]), [])

    def test_weekly_payment_costs_three_yuan_and_extends_exactly_seven_days_once(self):
        account = self.subscriber()
        order = self.store.create_order(account["id"], "weekly", NOW)
        self.assertEqual(order["amount"], 300)
        self.store.confirm_payment(order["order_token"], "weekly-1", 300, NOW)
        expiry = self.store.subscription(account["id"])["expires_at"]
        self.assertEqual(expiry, "2026-11-22T11:00:00+00:00")
        self.store.confirm_payment(order["order_token"], "weekly-1", 300, NOW)
        self.assertEqual(self.store.subscription(account["id"])["expires_at"], expiry)

    def test_morning_and_evening_snapshots_are_independent_and_accept_available_topics(self):
        morning = self.store.freeze_issue("2026-11-15", {"sports_health": "体育新闻"}, NOW, edition="morning")
        evening = self.store.freeze_issue("2026-11-15", {"gaming": "赛事新闻"}, NOW, edition="evening")
        self.assertNotEqual(morning, evening)
        self.assertEqual(self.store.freeze_issue("2026-11-15", {"sports_health": "体育新闻"}, NOW, edition="morning"), morning)
        with self.assertRaises(ValueError):
            self.store.freeze_issue("2026-11-15", {"sports_health": "覆盖"}, NOW, edition="morning")

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
            self.store.freeze_issue("2026-11-16", {"unknown": "新闻"}, NOW)
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


class AccountDeletionTest(StoreCase):
    def rows(self, table, account):
        with self.store.connect() as conn:
            return conn.execute(f"SELECT * FROM {table} WHERE subscriber_id=?", (account["id"],)).fetchall()

    def test_deletion_erases_personal_data_but_keeps_an_anonymous_payment_record(self):
        account = self.subscriber("gone@example.com")
        order = self.pay(account)
        with self.store.transaction() as conn:
            conn.execute("""INSERT INTO delivery_log(id,subscriber_id,task_type,business_key,status,payload,idempotency_key,created_at,next_attempt_at)
                            VALUES('d','%s','daily_brief','k','success','{"to":["gone@example.com"]}','brief/d',?,?)""" % account["id"], (NOW.isoformat(),) * 2)
            conn.execute("INSERT INTO renewal_reminders VALUES(?,?,?,?)", (account["id"], "c", "t3", NOW.isoformat()))
        self.store.delete_account(account["id"], NOW)
        self.assertIsNone(self.store.subscriber(account["id"]))
        self.assertIsNone(self.store.subscription(account["id"]))
        self.assertEqual(self.rows("delivery_log", account), [])
        self.assertEqual(self.rows("renewal_reminders", account), [])
        kept = self.store.order(order["order_token"])
        self.assertEqual((kept["status"], kept["amount"], kept["payment_ref"], kept["subscriber_id"]), ("paid", 990, "trade-1", None))
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            dump = " ".join(str(value) for table in ("subscribers", "orders", "deleted_accounts", "delivery_log")
                            for row in conn.execute(f"SELECT * FROM {table}") for value in tuple(row))
        self.assertNotIn("gone@example.com", dump)

    def test_old_links_stop_working_and_the_same_address_can_sign_up_again(self):
        account = self.subscriber("again@example.com")
        self.store.delete_account(account["id"], NOW)
        with self.assertRaises(ValueError):
            self.store.account_for_token(account["manage_token"])
        fresh = self.store.register("again@example.com", [], NOW)
        self.assertNotEqual(fresh["id"], account["id"])
        self.assertNotEqual(fresh["manage_token"], account["manage_token"])

    def test_deleting_and_signing_up_again_does_not_reset_the_free_trial(self):
        account = self.subscriber("trial@example.com")
        with self.store.transaction() as conn:
            conn.execute("UPDATE subscribers SET trial_remaining=1 WHERE id=?", (account["id"],))
        self.store.delete_account(account["id"], NOW)
        fresh = self.store.register("TRIAL@example.com", [], NOW)
        self.store.confirm(fresh["confirmation_token"], NOW)
        self.assertEqual(self.store.subscriber(fresh["id"])["trial_remaining"], 1)
        self.store.delete_account(fresh["id"], NOW)
        again = self.store.register("trial@example.com", [], NOW)
        self.assertEqual(self.store.subscriber(again["id"])["trial_remaining"], 1)

    def test_deletion_does_not_lift_a_suppression(self):
        account = self.subscriber("bounce@example.com")
        with self.store.transaction() as conn:
            conn.execute("UPDATE subscribers SET suppressed_at=? WHERE id=?", (NOW.isoformat(), account["id"]))
        self.store.delete_account(account["id"], NOW)
        self.assertIsNone(self.store.register("bounce@example.com", [], NOW))

    def test_a_recent_unpaid_order_blocks_deletion_until_the_payment_window_closes(self):
        account = self.subscriber("pending@example.com")
        order = self.store.create_order(account["id"], "weekly", NOW)
        with self.assertRaises(ValueError):
            self.store.delete_account(account["id"], NOW + timedelta(minutes=10))
        self.assertIsNotNone(self.store.subscriber(account["id"]))
        self.store.delete_account(account["id"], NOW + timedelta(minutes=31))
        self.assertIsNone(self.store.order(order["order_token"])["subscriber_id"])

    def test_a_payment_arriving_after_deletion_is_kept_as_unmatched_not_applied(self):
        account = self.subscriber("late@example.com")
        order = self.store.create_order(account["id"], "weekly", NOW)
        self.store.delete_account(account["id"], NOW + timedelta(minutes=31))
        self.assertEqual(self.store.confirm_payment(order["order_token"], "late-1", 300, NOW + timedelta(hours=1)), "unmatched")
        self.assertEqual(self.store.confirm_payment(order["order_token"], "late-1", 300, NOW + timedelta(hours=2)), "unmatched")
        self.assertEqual(self.store.order(order["order_token"])["payment_ref"], "late-1")
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM subscriptions").fetchone()[0], 0)
