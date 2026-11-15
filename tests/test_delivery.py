import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
