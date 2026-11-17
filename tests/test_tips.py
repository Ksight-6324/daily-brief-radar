import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from subscription.cli import yuan_to_fen
from subscription.delivery import Delivery
from subscription.render import render_issue
from tests.support import NOW, StoreCase
from web.app import create_app


class TipStoreTest(StoreCase):
    def test_leaderboard_sums_named_tips_and_hides_anonymous_ones(self):
        self.store.add_tip("老王", 666, "wechat", "2026-11-16", NOW)
        self.store.add_tip("小李", 1000, "alipay", "2026-11-16", NOW)
        self.store.add_tip(" 老王 ", 500, "alipay", "2026-11-17", NOW)
        self.store.add_tip("", 99900, "alipay", "2026-11-17", NOW)
        self.store.add_tip("后来者", 1000, "alipay", "2026-11-17", NOW)
        self.assertEqual(self.store.leaderboard(), [
            {"rank": 1, "name": "老王", "total": 1166, "times": 2},
            {"rank": 2, "name": "小李", "total": 1000, "times": 1},
            {"rank": 3, "name": "后来者", "total": 1000, "times": 1},
        ])
        self.assertEqual(len(self.store.tips()), 5)

    def test_tips_reject_bad_amounts_channels_dates_and_nicknames(self):
        cases = (("a", 0, "alipay", "2026-11-17"), ("a", 1.5, "alipay", "2026-11-17"),
                 ("a", 100, "paypal", "2026-11-17"), ("a", 100, "alipay", "11/17"),
                 ("x" * 21, 100, "alipay", "2026-11-17"), ("me@example.com", 100, "alipay", "2026-11-17"),
                 ("坏​名字", 100, "alipay", "2026-11-17"))
        for name, amount, channel, day in cases:
            with self.assertRaises(ValueError, msg=(name, amount, channel, day)):
                self.store.add_tip(name, amount, channel, day, NOW)
        self.assertEqual(self.store.tips(), [])

    def test_removal_by_nickname_takes_every_record_off_the_board(self):
        self.store.add_tip("老王", 666, "wechat", "2026-11-16", NOW)
        self.store.add_tip("老王", 100, "wechat", "2026-11-17", NOW)
        kept = self.store.add_tip("", 100, "wechat", "2026-11-17", NOW)
        self.assertEqual(self.store.remove_tips(name="老王"), 2)
        self.assertEqual(self.store.remove_tips(name=""), 0, "anonymous records are only removed by id")
        self.assertEqual(self.store.remove_tips(tip_id=kept), 1)
        with self.assertRaises(ValueError):
            self.store.remove_tips()

    def test_yuan_amounts_parse_exactly(self):
        self.assertEqual(yuan_to_fen("6.66"), 666)
        self.assertEqual(yuan_to_fen("10"), 1000)
        for bad in ("0", "-1", "1.001", "abc", "100001"):
            with self.assertRaises(SystemExit, msg=bad):
                yuan_to_fen(bad)


class TipWebTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.codes = tempfile.TemporaryDirectory()
        self.addCleanup(self.codes.cleanup)
        self.client = TestClient(create_app(self.store, self.delivery()))
        self.addCleanup(self.client.close)

    def delivery(self):
        return Delivery(self.store, "https://brief.example.com", "Brief <brief@example.com>")

    def test_leaderboard_api_lists_only_public_fields(self):
        self.store.add_tip("老王", 666, "wechat", "2026-11-16", NOW)
        self.store.add_tip("", 666, "wechat", "2026-11-16", NOW)
        self.assertEqual(self.client.get("/api/leaderboard").json(),
                         {"tippers": [{"rank": 1, "name": "老王", "total": 666, "times": 1}]})

    def test_payment_codes_are_served_from_the_data_disk_only(self):
        with patch.dict("os.environ", {"BRIEF_TIP_CODE_DIR": self.codes.name}):
            self.assertEqual(self.client.get("/api/tip-codes/alipay").status_code, 404)
            (Path(self.codes.name) / "alipay.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
            response = self.client.get("/api/tip-codes/alipay")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "image/png")
            self.assertEqual(self.client.get("/api/tip-codes/..%2Fbrief.db").status_code, 404)
            self.assertEqual(self.client.get("/api/tip-codes/wechat").status_code, 404)

    def test_support_page_explains_tips_and_obeys_the_content_security_policy(self):
        page = self.client.get("/support").text
        for text in ("自愿", "备注", "大哥榜", "/api/tip-codes/alipay", "/api/tip-codes/wechat",
                     "/static/support.js", 'id="contact-line"'):
            self.assertIn(text, page)
        self.assertNotRegex(page, r"<script(?![^>]*\bsrc=)")
        self.assertNotRegex(page, r'\sstyle="')
        self.assertEqual(self.client.get("/static/support.js").status_code, 200)

    def test_free_reading_hides_paid_plans_and_keeps_orders_closed(self):
        self.assertFalse(self.client.get("/api/options").json()["payments"])
        alipay = {"ALIPAY_APP_ID": "app", "ALIPAY_SELLER_ID": "seller", "ALIPAY_PRIVATE_KEY": "k", "ALIPAY_PUBLIC_KEY": "k"}
        with patch.dict("os.environ", alipay):
            client = TestClient(create_app(self.store, self.delivery()))
            self.addCleanup(client.close)
            self.assertTrue(client.get("/api/options").json()["payments"])
            with patch.dict("os.environ", {"BRIEF_FREE_ACCESS": "1"}):
                self.assertFalse(client.get("/api/options").json()["payments"])
                account = self.subscriber()
                response = client.post("/api/orders", json={"plan": "monthly"},
                                       headers={"Authorization": "Bearer " + account["manage_token"]})
                self.assertEqual(response.status_code, 503)

    def test_every_issue_links_to_the_tips_page(self):
        html, text = render_issue("2026-11-15", {"ai": "官方|||标题|||https://example.com|||AI|||摘要|||重要"}, [],
                                  "https://brief.example.com", "token")
        self.assertIn('href="https://brief.example.com/support"', html)
        self.assertIn("https://brief.example.com/support", text)
        for body in (html, text):
            self.assertIn("一毛、一块还是一百，在我这都是莫大的鼓励", body)
        self.assertEqual(html.count("/support"), 1)
