import json
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from subscription.delivery import Delivery, ResendClient
from subscription.render import render_issue
from tests.support import NOW, StoreCase


SECTIONS = {
    "markets": "Reuters|||市场消息|||https://example.com/market|||经济|||已核实的市场变化。|||影响融资成本。",
    "ai": "官方公告|||模型发布|||https://example.com/ai|||AI|||模型有新进展。|||关注实际可用性。",
    "jobs": "招聘官网|||招聘开放|||https://example.com/jobs|||就业|||新岗位开放申请。|||关注申请截止日期。",
}


class ProviderHandler(BaseHTTPRequestHandler):
    calls = []
    fail = False

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append((self.path, self.headers["Idempotency-Key"], body))
        self.send_response(503 if type(self).fail else 200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"message":"unavailable"}' if type(self).fail else b'{"id":"email-123"}')

    def log_message(self, *args):
        pass


class DeliveryTest(StoreCase):
    def setUp(self):
        super().setUp()
        ProviderHandler.calls = []
        ProviderHandler.fail = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        client = ResendClient("test-key", f"http://127.0.0.1:{self.server.server_port}")
        self.delivery = Delivery(self.store, "https://brief.example.com", "Brief <brief@example.com>", client)
        self.issue_id = self.store.freeze_issue("2026-11-15", SECTIONS, NOW)

    def rows(self):
        with self.store.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM delivery_log ORDER BY created_at,id")]

    def test_both_editions_send_once_and_consume_two_trial_issues(self):
        account = self.subscriber()
        evening_time = NOW + timedelta(hours=1)
        evening = self.store.freeze_issue("2026-11-15", {"gaming": SECTIONS["ai"]}, evening_time, edition="evening")
        self.assertEqual(self.delivery.enqueue_issue(evening, NOW), 0)
        self.assertEqual(self.delivery.enqueue_issue(self.issue_id, NOW), 1)
        self.assertEqual(self.delivery.run_once(NOW), "success")
        self.assertEqual(self.delivery.enqueue_issue(evening, evening_time), 1)
        self.assertEqual(self.delivery.run_once(evening_time), "success")
        self.assertEqual(self.delivery.enqueue_issue(evening, evening_time), 0)
        self.assertEqual(self.store.subscriber(account["id"])["trial_remaining"], 1)
        subjects = [call[2]["subject"] for call in ProviderHandler.calls]
        self.assertIn("早报", subjects[0])
        self.assertIn("晚报", subjects[1])

    def test_expanded_snapshot_preserves_all_topics_and_legacy_three_topic_rendering(self):
        from subscription.store import SECTIONS as TOPICS
        expanded = {key: f"官方|||{label}新闻|||https://example.com/{key}|||主题|||事实|||影响" for key, label in TOPICS.items()}
        html, text = render_issue("2026-11-15", expanded, ["sports_health", "gaming"], "https://brief.example.com", "token")
        self.assertLess(html.index('id="section-sports_health"'), html.index('id="section-markets"'))
        self.assertLess(html.index('id="section-gaming"'), html.index('id="section-markets"'))
        for label in TOPICS.values():
            self.assertIn(label, text)
        legacy_html, _ = render_issue("2026-11-15", SECTIONS, ["gaming"], "https://brief.example.com", "token")
        self.assertIn("市场消息", legacy_html)

    def test_followed_topics_are_all_expanded_and_the_rest_are_folded(self):
        from subscription.store import SECTIONS as TOPICS
        expanded = {key: f"官方|||{label}新闻|||https://example.com/{key}|||主题|||事实|||影响" for key, label in TOPICS.items()}
        followed = ["jobs", "ai", "gaming", "history", "mind"]
        html, text = render_issue("2026-11-15", expanded, followed, "https://brief.example.com", "token")
        fold = html.index("<details>")
        for key in followed:
            self.assertLess(html.index(f'id="section-{key}"'), fold)
        self.assertGreater(html.index('id="section-markets"'), fold)
        self.assertIn("你关注的主题", html)
        self.assertEqual(text.count("其他值得一扫"), 1)
        general, _ = render_issue("2026-11-15", expanded, [], "https://brief.example.com", "token")
        self.assertIn("本期三条必知", general)
        self.assertLess(general.index('id="section-politics_mil"'), general.index("<details>"))
        self.assertGreater(general.index('id="section-history"'), general.index("<details>"))
        unavailable, _ = render_issue("2026-11-15", {"markets": expanded["markets"]}, ["jobs"], "https://brief.example.com", "token")
        self.assertNotIn("<details>", unavailable)

    def test_scan_and_worker_are_idempotent_and_consume_one_trial(self):
        account = self.subscriber()
        self.assertEqual(self.delivery.enqueue_issue(self.issue_id, NOW), 1)
        self.assertEqual(self.delivery.enqueue_issue(self.issue_id, NOW), 0)
        self.assertEqual(self.delivery.run_once(NOW), "success")
        self.assertIsNone(self.delivery.run_once(NOW))
        self.assertEqual(len(ProviderHandler.calls), 1)
        self.assertEqual(self.store.subscriber(account["id"])["trial_remaining"], 2)
        self.assertEqual(self.rows()[0]["provider_id"], "email-123")
        self.assertIn("List-Unsubscribe", ProviderHandler.calls[0][2]["headers"])

    def test_failure_retries_identical_payload_and_key(self):
        account = self.subscriber()
        self.delivery.enqueue_issue(self.issue_id, NOW)
        ProviderHandler.fail = True
        self.assertEqual(self.delivery.run_once(NOW), "failed")
        self.assertEqual(self.store.subscriber(account["id"])["trial_remaining"], 3)
        ProviderHandler.fail = False
        self.assertEqual(self.delivery.run_once(NOW + timedelta(minutes=2)), "success")
        self.assertEqual(ProviderHandler.calls[0], ProviderHandler.calls[1])

    def test_concurrent_workers_send_one_message(self):
        self.subscriber()
        self.delivery.enqueue_issue(self.issue_id, NOW)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.delivery.run_once(NOW), range(2)))
        self.assertEqual(results.count("success"), 1)
        self.assertEqual(len(ProviderHandler.calls), 1)

    def test_replaced_worker_cannot_send_or_consume_trial_after_losing_lease(self):
        account = self.subscriber()
        self.delivery.enqueue_issue(self.issue_id, NOW)
        claimed = threading.Event()
        resume = threading.Event()
        original = self.store.transaction
        counts = {}

        @contextmanager
        def pause_after_claim():
            name = threading.current_thread().name
            counts[name] = counts.get(name, 0) + 1
            with original() as conn:
                yield conn
            if name == "stale-worker" and counts[name] == 1:
                claimed.set()
                resume.wait(5)

        results = []
        with patch.object(self.store, "transaction", pause_after_claim):
            thread = threading.Thread(name="stale-worker", target=lambda: results.append(self.delivery.run_once(NOW)))
            thread.start()
            self.assertTrue(claimed.wait(3))
            try:
                self.assertEqual(self.delivery.run_once(NOW + timedelta(minutes=11)), "success")
            finally:
                resume.set()
                thread.join(5)
        self.assertEqual(len(ProviderHandler.calls), 1)
        self.assertEqual(self.store.subscriber(account["id"])["trial_remaining"], 2)

    def test_racing_resubscription_confirmation_contains_current_valid_challenge(self):
        account = self.subscriber()
        self.store.unsubscribe(account["manage_token"], NOW)
        first = self.store.register("reader@example.com", [], NOW + timedelta(minutes=1))
        second = self.store.register("reader@example.com", [], NOW + timedelta(minutes=2))
        self.delivery.enqueue_confirmation(first, NOW + timedelta(minutes=3))
        self.delivery.enqueue_confirmation(second, NOW + timedelta(minutes=3))
        current = self.store.subscriber(account["id"])
        queued = [row for row in self.rows() if row["status"] == "pending"]
        self.assertEqual(len(queued), 1)
        self.assertIn(self.store.token(current, "confirm"), json.loads(queued[0]["payload"])["html"])

    def test_unsubscribe_between_queue_and_send_skips_message(self):
        account = self.subscriber()
        self.delivery.enqueue_issue(self.issue_id, NOW)
        self.store.unsubscribe(account["manage_token"], NOW)
        self.assertEqual(self.delivery.run_once(NOW), "skipped")
        self.assertEqual(ProviderHandler.calls, [])

    def test_pending_expired_and_exhausted_readers_are_excluded(self):
        self.store.register("pending@example.com", [], NOW)
        account = self.subscriber("expired@example.com")
        self.pay(account)
        late = datetime(2026, 12, 19, 11, tzinfo=timezone.utc)
        issue = self.store.freeze_issue("2026-12-19", SECTIONS, late)
        self.assertEqual(self.delivery.enqueue_issue(issue, late), 0)
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM subscriptions WHERE subscriber_id=?", (account["id"],))
            conn.execute("UPDATE subscribers SET trial_remaining=0 WHERE id=?", (account["id"],))
        self.assertEqual(self.delivery.enqueue_issue(issue, late), 0)

    def test_grace_period_receives_banner_and_then_stops(self):
        account = self.subscriber()
        self.pay(account)
        during = datetime(2026, 12, 16, 11, tzinfo=timezone.utc)
        issue = self.store.freeze_issue("2026-12-16", SECTIONS, during)
        self.assertEqual(self.delivery.enqueue_issue(issue, during), 1)
        self.assertIn("已到期", json.loads(self.rows()[0]["payload"])["html"])
        after = datetime(2026, 12, 18, 11, tzinfo=timezone.utc)
        self.assertEqual(self.delivery.run_once(after), "skipped")

    def test_uncertain_delivery_after_provider_window_requires_review(self):
        self.subscriber()
        self.delivery.enqueue_issue(self.issue_id, NOW)
        with self.store.transaction() as conn:
            conn.execute("UPDATE delivery_log SET status='sending',first_attempt_at=?,lease_until=?",
                         (NOW.isoformat(), NOW.isoformat()))
        self.assertEqual(self.delivery.run_once(NOW + timedelta(hours=24)), "needs_review")
        self.assertEqual(ProviderHandler.calls, [])

    def test_three_successful_issues_end_trial_and_excess_queued_work_is_excluded(self):
        account = self.subscriber()
        for index in range(3):
            when = NOW + timedelta(days=index)
            issue = self.store.freeze_issue(when.date().isoformat(), SECTIONS, when)
            self.assertEqual(self.delivery.enqueue_issue(issue, when), 1)
            self.assertEqual(self.delivery.run_once(when), "success")
        when = NOW + timedelta(days=3)
        issue = self.store.freeze_issue(when.date().isoformat(), SECTIONS, when)
        self.assertEqual(self.delivery.enqueue_issue(issue, when), 0)
        self.assertEqual(self.store.subscriber(account["id"])["trial_remaining"], 0)

    def test_reminder_is_unique_per_expiry_cycle(self):
        account = self.subscriber()
        self.pay(account)
        when = datetime(2026, 12, 8, 11, tzinfo=timezone.utc)
        self.assertEqual(self.delivery.enqueue_reminders(when), 1)
        self.assertEqual(self.delivery.enqueue_reminders(when), 0)
        self.assertEqual(self.delivery.run_once(when), "success")
        self.pay(account, "trade-2", when)
        later = datetime(2027, 1, 7, 11, tzinfo=timezone.utc)
        self.assertEqual(self.delivery.enqueue_reminders(later), 1)

    def test_renderer_reorders_without_removing_sections_and_escapes_external_text(self):
        html, text = render_issue("2026-11-15", SECTIONS, ["jobs"], "https://brief.example.com", "token")
        self.assertLess(html.index('id="section-jobs"'), html.index('id="section-markets"'))
        for title in ("招聘开放", "模型发布", "市场消息"):
            self.assertIn(title, text)
        bad = {**SECTIONS, "jobs": '坏源|||<script>alert(1)</script>|||javascript:alert(1)|||标签|||<img src=x>|||说明'}
        html, _ = render_issue("2026-11-15", bad, [], "https://brief.example.com", "token")
        self.assertNotIn("<script>", html)
        self.assertNotIn('href="javascript:', html)
        self.assertIn("&lt;img", html)

    def test_login_link_is_sent_only_to_confirmed_active_readers_and_expires_after_an_hour(self):
        account = self.subscriber("login@example.com")
        self.assertEqual(self.delivery.enqueue_login("nobody@example.com", NOW), 0)
        pending = self.store.register("pending@example.com", [], NOW)
        self.assertEqual(self.delivery.enqueue_login("pending@example.com", NOW), 0)
        self.assertIsNotNone(pending)
        self.assertEqual(self.delivery.enqueue_login("login@example.com", NOW), 1)
        self.assertEqual(self.delivery.run_once(NOW), "success")
        path, _, body = ProviderHandler.calls[0]
        self.assertEqual(body["to"], ["login@example.com"])
        self.assertIn("/manage#" + account["manage_token"], body["html"])
        self.assertEqual(self.store.subscriber(account["id"])["trial_remaining"], 3)
        self.assertEqual(self.delivery.enqueue_login("login@example.com", NOW), 1)
        self.assertEqual(self.delivery.run_once(NOW + timedelta(hours=2)), "skipped")
        self.assertEqual(len(ProviderHandler.calls), 1)

    def test_login_link_is_not_sent_after_unsubscribe(self):
        account = self.subscriber("stopped@example.com")
        self.assertEqual(self.delivery.enqueue_login("stopped@example.com", NOW), 1)
        self.store.unsubscribe(account["manage_token"], NOW)
        self.assertEqual(self.delivery.run_once(NOW), "skipped")
        self.assertEqual(ProviderHandler.calls, [])
        self.assertEqual(self.delivery.enqueue_login("stopped@example.com", NOW), 0)

    def test_transactional_mail_is_sent_before_a_queued_bulk_issue(self):
        self.subscriber("first@example.com")
        self.assertEqual(self.delivery.enqueue_issue(self.issue_id, NOW), 1)
        self.subscriber("second@example.com")
        self.assertEqual(self.delivery.enqueue_login("second@example.com", NOW), 1)
        self.assertEqual(self.delivery.run_once(NOW), "success")
        self.assertEqual(ProviderHandler.calls[0][2]["to"], ["second@example.com"])

    def test_weekly_plan_skips_the_seven_day_notice_but_keeps_later_ones(self):
        account = self.subscriber()
        order = self.store.create_order(account["id"], "weekly", NOW)
        self.store.confirm_payment(order["order_token"], "weekly-1", 300, NOW)
        self.assertEqual(self.delivery.enqueue_reminders(NOW + timedelta(minutes=1)), 0)
        self.assertEqual(self.delivery.enqueue_reminders(NOW + timedelta(days=4)), 1)
        self.assertEqual(self.delivery.enqueue_reminders(NOW + timedelta(days=6)), 1)
