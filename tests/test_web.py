import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from subscription.delivery import Delivery
from tests.support import NOW, StoreCase
from web.app import create_app


class WebTest(StoreCase):
    def setUp(self):
        super().setUp()
        delivery = Delivery(self.store, "https://brief.example.com", "Brief <brief@example.com>")
        self.client = TestClient(create_app(self.store, delivery))
        self.addCleanup(self.client.close)

    def headers(self, account):
        return {"Authorization": "Bearer " + account["manage_token"]}

    def test_public_options_and_api_accept_all_fifteen_preferences(self):
        response = self.client.get("/api/options")
        self.assertEqual(response.status_code, 200)
        options = response.json()
        self.assertEqual(len(options["topics"]), 16)
        self.assertEqual(options["plans"][0], {"id": "weekly", "label": "周付", "amount": 300, "days": 7})
        self.assertEqual(options["schedule"], {"timezone": "Asia/Shanghai", "times": ["08:00", "20:00"]})
        selected = [topic["id"] for topic in options["topics"]]
        self.assertEqual(self.client.post("/api/subscribe", json={
            "email": "all@example.com", "preferences": selected, "consent": True,
        }).status_code, 200)
        account = self.subscriber()
        response = self.client.post("/api/preferences", headers=self.headers(account), json={"preferences": selected})
        self.assertEqual(response.status_code, 200)
        saved = self.client.get("/api/account", headers=self.headers(account)).json()["preferences"]
        self.assertEqual(set(saved), set(selected))

    def test_proxy_clients_have_separate_rate_limits_and_cannot_skip_their_own_limit(self):
        # Compose trusts Caddy on its private network and publishes only Caddy's ports.
        proxy_app = ProxyHeadersMiddleware(self.client.app, trusted_hosts="*")
        with TestClient(proxy_app, client=("172.20.0.5", 40000)) as client:
            for index in range(20):
                response = client.post("/api/subscribe", headers={"X-Forwarded-For": "198.51.100.10"},
                    json={"email": f"proxy-{index}@example.invalid", "preferences": [], "consent": True})
                self.assertEqual(response.status_code, 200)
            self.assertEqual(client.post("/api/subscribe", headers={"X-Forwarded-For": "198.51.100.10"},
                json={"email": "proxy-limit@example.invalid", "preferences": [], "consent": True}).status_code, 429)
            self.assertEqual(client.post("/api/subscribe", headers={"X-Forwarded-For": "198.51.100.11"},
                json={"email": "proxy-other@example.invalid", "preferences": [], "consent": True}).status_code, 200)

    def test_a_delivered_login_link_expires_and_cannot_be_extended_by_editing_its_timestamp(self):
        import re
        from datetime import timedelta
        from unittest.mock import patch
        delivery = Delivery(self.store, "https://brief.example.com", "Brief <brief@example.com>")
        account = self.subscriber("login-expiry@example.com")
        delivery.enqueue_login("login-expiry@example.com", NOW)
        with self.store.connect() as conn:
            payload = json.loads(conn.execute("SELECT payload FROM delivery_log WHERE task_type='login_link'").fetchone()[0])
        token = re.search(r"/manage#([^\s<]+)", payload["text"]).group(1)
        headers = {"Authorization": "Bearer " + token}
        with patch("subscription.store.utcnow", return_value=NOW + timedelta(minutes=59)):
            self.assertEqual(self.client.get("/api/account", headers=headers).status_code, 200)
        with patch("subscription.store.utcnow", return_value=NOW + timedelta(hours=1)):
            self.assertEqual(self.client.get("/api/account", headers=headers).status_code, 401)
            self.assertEqual(self.client.post("/api/account/delete", headers=headers,
                json={"confirm_email": "login-expiry@example.com"}).status_code, 401)
        parts = token.split(".")
        self.assertEqual(len(parts), 3)
        parts[1] = str(int(parts[1]) + 86400)
        with patch("subscription.store.utcnow", return_value=NOW):
            self.assertEqual(self.client.get("/api/account", headers={"Authorization": "Bearer " + ".".join(parts)}).status_code, 401)
        self.assertEqual(self.client.get("/api/account", headers=self.headers(account)).status_code, 200)

    def test_subscribe_requires_consent_and_queues_confirmation(self):
        values = {"email": "new@example.com", "preferences": ["jobs"], "consent": False}
        self.assertEqual(self.client.post("/api/subscribe", json=values).status_code, 422)
        values["consent"] = True
        response = self.client.post("/api/subscribe", json=values)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("token", response.text)
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM delivery_log").fetchone()
        self.assertEqual(row["task_type"], "confirmation")
        self.assertIn("/confirm#", json.loads(row["payload"])["html"])
        self.assertIn("/unsubscribe#", json.loads(row["payload"])["html"])

    def test_confirm_then_manage_preferences_and_unsubscribe(self):
        from subscription.store import utcnow
        account = self.store.register("web@example.com", [], utcnow())
        response = self.client.post("/api/confirm", json={"token": account["confirmation_token"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["manage_token"], account["manage_token"])
        response = self.client.get("/api/account", headers=self.headers(account))
        self.assertEqual(response.json()["trial_remaining"], 3)
        response = self.client.post("/api/preferences", headers=self.headers(account), json={"preferences": ["ai", "jobs"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/account", headers=self.headers(account)).json()["preferences"], ["ai", "jobs"])
        response = self.client.post("/api/unsubscribe", headers=self.headers(account))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.subscriber(account["id"])["status"], "unsubscribed")

    def test_unsubscribe_get_is_safe_and_post_accepts_one_click(self):
        account = self.subscriber()
        self.assertEqual(self.client.get("/api/unsubscribe", params={"token": account["manage_token"]}).status_code, 405)
        self.assertEqual(self.store.subscriber(account["id"])["status"], "active")
        self.assertEqual(self.client.post("/api/unsubscribe", params={"token": account["manage_token"]}).status_code, 200)
        self.assertEqual(self.store.subscriber(account["id"])["status"], "unsubscribed")

    def test_login_mail_unsubscribe_header_cannot_be_used_to_access_or_delete_the_account(self):
        import re
        from datetime import timedelta
        from unittest.mock import patch
        delivery = Delivery(self.store, "https://brief.example.com", "Brief <brief@example.com>")
        account = self.subscriber("header-scope@example.invalid")
        delivery.enqueue_login("header-scope@example.invalid", NOW)
        with self.store.connect() as conn:
            payload = json.loads(conn.execute("SELECT payload FROM delivery_log WHERE task_type='login_link'").fetchone()[0])
        token = re.search(r"token=([^>]+)", payload["headers"]["List-Unsubscribe"]).group(1)
        headers = {"Authorization": "Bearer " + token}
        with patch("subscription.store.utcnow", return_value=NOW + timedelta(days=365)):
            self.assertEqual(self.client.get("/api/account", headers=headers).status_code, 401)
            self.assertEqual(self.client.post("/api/account/delete", headers=headers,
                json={"confirm_email": "header-scope@example.invalid"}).status_code, 401)
            self.assertEqual(self.client.post("/api/unsubscribe", params={"token": token}).status_code, 200)
        self.assertEqual(self.store.subscriber(account["id"])["status"], "unsubscribed")

    def test_account_and_payment_routes_require_authentication(self):
        self.assertEqual(self.client.get("/api/account").status_code, 401)
        self.assertEqual(self.client.get("/api/account", headers={"Authorization": "Bearer invalid"}).status_code, 401)
        self.assertEqual(self.client.post("/api/orders", json={"plan": "monthly"}).status_code, 401)
        account = self.subscriber()
        response = self.client.post("/api/orders", headers=self.headers(account), json={"plan": "monthly"})
        self.assertEqual(response.status_code, 503)

    def test_invalid_payment_callback_does_not_change_state(self):
        self.assertEqual(self.client.post("/api/payments/alipay/notify", data={"sign": "fake"}).status_code, 400)
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM orders").fetchone()[0], 0)

    def test_payment_callback_limits_actual_body_and_rejects_duplicate_fields(self):
        response = self.client.post("/api/payments/alipay/notify", content=b"x" * 65537,
                                    headers={"Content-Type": "application/x-www-form-urlencoded", "Content-Length": "0"})
        self.assertEqual(response.status_code, 413)
        response = self.client.post("/api/payments/alipay/notify", content="sign=a&sign=b",
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(response.status_code, 400)

    def test_pages_include_security_headers_and_real_forms(self):
        for route in ("/", "/confirm", "/manage", "/unsubscribe", "/login", "/delete", "/privacy", "/terms", "/support"):
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["referrer-policy"], "no-referrer")
                self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_signups_are_rate_limited_without_disclosing_account_existence(self):
        payload = {"email": "new@example.com", "preferences": [], "consent": True}
        first = self.client.post("/api/subscribe", json=payload)
        second = self.client.post("/api/subscribe", json=payload)
        third = self.client.post("/api/subscribe", json=payload)
        fourth = self.client.post("/api/subscribe", json=payload)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(third.status_code, 200)
        self.assertEqual(fourth.status_code, 429)

    def test_login_request_never_reveals_whether_an_address_is_subscribed(self):
        account = self.subscriber("home@example.com")
        known = self.client.post("/api/login", json={"email": "Home@Example.com"})
        unknown = self.client.post("/api/login", json={"email": "stranger@example.com"})
        self.assertEqual(known.status_code, 200)
        self.assertEqual(known.json(), unknown.json())
        with self.store.connect() as conn:
            rows = conn.execute("SELECT task_type,subscriber_id FROM delivery_log").fetchall()
        self.assertEqual([(row["task_type"], row["subscriber_id"]) for row in rows], [("login_link", account["id"])])
        self.assertEqual(self.client.post("/api/login", json={"email": "not-an-email"}).status_code, 400)

    def test_login_requests_are_rate_limited_per_address(self):
        self.subscriber("busy@example.com")
        codes = [self.client.post("/api/login", json={"email": "busy@example.com"}).status_code for _ in range(4)]
        self.assertEqual(codes, [200, 200, 200, 429])

    def issues(self):
        story = lambda title, key: f"来源|||{title}|||https://example.com/{key}|||标签|||摘要{title}|||重要{title}"
        self.store.freeze_issue("2026-11-14", {"ai": story("昨日AI", "a"), "jobs": story("昨日就业", "j")}, NOW, edition="evening")
        self.store.freeze_issue("2026-11-15", {"ai": story("今日AI", "a2"), "markets": story("今日市场", "m"),
                                               "gaming": "javascript 伪链接|||游戏<b>|||javascript:alert(1)|||标签|||摘要|||重要"}, NOW, edition="morning")

    def test_feed_requires_login_and_lists_followed_topics_first(self):
        self.issues()
        self.assertEqual(self.client.get("/api/feed").status_code, 401)
        account = self.subscriber()
        self.client.post("/api/preferences", headers=self.headers(account), json={"preferences": ["ai", "gaming"]})
        followed = self.client.get("/api/feed", headers=self.headers(account)).json()
        self.assertFalse(followed["limited"])
        self.assertFalse(followed["general"])
        self.assertEqual([(issue["date"], issue["edition"]) for issue in followed["issues"]],
                         [("2026-11-15", "morning"), ("2026-11-14", "evening")])
        self.assertEqual([story["topic"] for story in followed["issues"][0]["stories"]], ["ai", "gaming"])
        self.assertEqual([story["topic"] for story in followed["issues"][1]["stories"]], ["ai"])
        self.assertEqual(followed["issues"][0]["stories"][0]["title"], "今日AI")
        self.assertEqual(followed["issues"][0]["stories"][1]["url"], "")
        everything = self.client.get("/api/feed", params={"scope": "all"}, headers=self.headers(account)).json()
        self.assertEqual([story["topic"] for story in everything["issues"][0]["stories"]], ["ai", "gaming", "markets"])
        self.assertTrue(everything["issues"][0]["stories"][2]["followed"] is False)
        self.assertEqual(self.client.get("/api/feed", params={"scope": "x"}, headers=self.headers(account)).status_code, 400)

    def test_free_access_shows_free_mode_and_full_feed_after_trial(self):
        self.issues()
        account = self.subscriber()
        with self.store.transaction() as conn:
            conn.execute("UPDATE subscribers SET trial_remaining=0 WHERE id=?", (account["id"],))
        self.assertTrue(self.client.get("/api/feed", headers=self.headers(account)).json()["limited"])
        with patch.dict("os.environ", {"BRIEF_FREE_ACCESS": "1"}):
            self.assertEqual(self.client.get("/api/account", headers=self.headers(account)).json()["delivery_mode"], "free")
            feed = self.client.get("/api/feed?scope=all", headers=self.headers(account)).json()
        self.assertFalse(feed["limited"])
        self.assertEqual(len(feed["issues"]), 2)

    def test_feed_changes_immediately_after_the_reader_edits_topics(self):
        self.issues()
        account = self.subscriber()
        self.client.post("/api/preferences", headers=self.headers(account), json={"preferences": ["jobs"]})
        feed = self.client.get("/api/feed", headers=self.headers(account)).json()
        self.assertEqual([[story["topic"] for story in issue["stories"]] for issue in feed["issues"]], [["jobs"]])
        self.client.post("/api/preferences", headers=self.headers(account), json={"preferences": []})
        general = self.client.get("/api/feed", headers=self.headers(account)).json()
        self.assertTrue(general["general"])
        self.assertEqual(len(general["issues"][0]["stories"]), 3)

    def test_feed_is_a_one_issue_preview_when_the_trial_is_used_up_and_hidden_before_confirmation(self):
        self.issues()
        account = self.subscriber()
        with self.store.transaction() as conn:
            conn.execute("UPDATE subscribers SET trial_remaining=0 WHERE id=?", (account["id"],))
        feed = self.client.get("/api/feed", headers=self.headers(account)).json()
        self.assertTrue(feed["limited"])
        self.assertEqual(len(feed["issues"]), 1)
        self.assertEqual(self.client.get("/api/account", headers=self.headers(account)).json()["delivery_mode"], None)
        pending = self.store.register("pending@example.com", [], NOW)
        self.assertEqual(self.client.get("/api/feed", headers=self.headers(pending)).status_code, 403)

    def test_delete_account_needs_login_and_the_typed_address_and_then_signs_the_reader_out(self):
        account = self.subscriber("bye@example.com")
        self.assertEqual(self.client.post("/api/account/delete", json={"confirm_email": "bye@example.com"}).status_code, 401)
        wrong = self.client.post("/api/account/delete", headers=self.headers(account), json={"confirm_email": "other@example.com"})
        self.assertEqual(wrong.status_code, 400)
        self.assertIsNotNone(self.store.subscriber(account["id"]))
        done = self.client.post("/api/account/delete", headers=self.headers(account), json={"confirm_email": " Bye@Example.com "})
        self.assertEqual(done.status_code, 200)
        self.assertIsNone(self.store.subscriber(account["id"]))
        self.assertEqual(self.client.get("/api/account", headers=self.headers(account)).status_code, 401)
        self.assertEqual(self.client.get("/delete").status_code, 200)

    def test_delete_account_is_refused_while_a_payment_is_pending(self):
        account = self.subscriber("busy2@example.com")
        self.store.create_order(account["id"], "weekly")
        response = self.client.post("/api/account/delete", headers=self.headers(account), json={"confirm_email": "busy2@example.com"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("支付订单", response.json()["detail"])
        self.assertIsNotNone(self.store.subscriber(account["id"]))

    def test_privacy_policy_describes_the_data_the_system_really_keeps(self):
        page = self.client.get("/privacy").text
        for fact in ("邮箱地址", "Resend", "支付宝", "删除账号", "不使用 Cookie", "30 天", "无法还原为邮箱", "发送成功 30 天后清除", "访问日志保存约 14 天"):
            self.assertIn(fact, page)
        self.assertNotIn("<script>", page.replace('<script src="/static/contact.js" defer></script>', ""))
        home = self.client.get("/").text
        self.assertEqual(home.count('href="/privacy"'), 2)
        self.assertEqual(home.count('href="/terms"'), 2)

    def test_home_page_introduces_the_service_and_obeys_the_content_security_policy(self):
        import re
        page = self.client.get("/").text
        for text in ("短视频的新闻？", "看了就忘！", "不如在三分钟", "而不是让短视频填满你三小时", "每日 AI 报告", "12 个小时", "三条优势", "比短视频更快", "比营销号更可信", "比报道更全面", "选择天天雷达！", "每天汇总全球要点", "自动发到你的邮箱", "08:00", "20:00", "示例"):
            self.assertIn(text, page)
        for anchor in ('id="preview"', 'id="subscribe"', 'id="tips"', 'id="subscribe-form"', 'href="/#preview"', 'href="/support"'):
            self.assertIn(anchor, page)
        # The sample email names each part of a real issue, in the order readers meet them.
        parts = re.findall(r'data-cap="([^"]+)"', page)
        for part in ("刊头", "今日焦点", "主标题", "关键词", "一句话摘要", "和你有关"):
            self.assertIn(part, parts)
        self.assertLess(parts.index("今日焦点"), parts.index("主标题"))
        self.assertNotRegex(page, r"<script(?![^>]*\bsrc=)", "the policy forbids inline scripts")
        self.assertNotRegex(page, r'\sstyle="', "the policy forbids style attributes")
        self.assertNotIn("新闻联播", page, "no comparative claim names a specific broadcaster")
        self.assertIn('class="clock"', page)
        self.assertNotIn("twelve-bar", page)
        self.assertIn("script-src 'self'", self.client.get("/").headers["content-security-policy"])
        for path in ("/static/landing.css", "/static/landing.js", "/static/style.css", "/static/app.js"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
            self.assertIn(f'"{path}"', page)
        self.assertNotRegex(self.client.get("/static/landing.css").text, r"url\(|@import")

    def test_contact_details_come_from_configuration_and_bad_addresses_are_dropped(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {"CONTACT_EMAIL": "Privacy@Example.com", "OPERATOR_NAME": " 示例工作室 "}):
            self.assertEqual(self.client.get("/api/options").json()["contact"], {"email": "privacy@example.com", "operator": "示例工作室"})
        with patch.dict("os.environ", {"CONTACT_EMAIL": "javascript:alert(1)", "OPERATOR_NAME": ""}):
            self.assertEqual(self.client.get("/api/options").json()["contact"], {"email": "", "operator": ""})

    def test_terms_page_describes_free_reading_and_voluntary_tips(self):
        import re
        page = self.client.get("/terms").text
        text = re.sub(r"<[^>]+>|\s+", "", page)
        for fact in ("订阅不收费，也不限量。", "不会自动扣款", "打赏完全自愿，金额随意。", "大哥榜", "没有备注的打赏不上榜",
                     "撤下", "原路退回", "不构成投资", "请以原始来源为准", "08:00", "20:00"):
            self.assertIn(fact, text)
        for stale in ("免费试读", "宽限期", "周付", "手动续费"):
            self.assertNotIn(stale, text)
        self.assertIn('href="/privacy"', page)
        self.assertIn('id="contact-line"', page)
