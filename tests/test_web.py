import json

from fastapi.testclient import TestClient

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
        self.assertEqual(len(options["topics"]), 15)
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
        for route in ("/", "/confirm", "/manage", "/unsubscribe", "/login"):
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
