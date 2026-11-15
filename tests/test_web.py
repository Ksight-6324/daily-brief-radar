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
        for route in ("/", "/confirm", "/manage", "/unsubscribe"):
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
