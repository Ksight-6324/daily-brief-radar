import base64
import json
import unittest
from unittest.mock import patch

import requests

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from subscription.payments import Alipay
from tests.support import NOW, StoreCase


class PaymentTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_pem = self.private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        private_pem = self.private.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        self.provider = Alipay(self.store, "app-123", "seller-123", public_pem, private_pem,
                               "https://brief.example.com/api/payments/alipay/notify")

    def notification(self, token, **overrides):
        values = {"app_id": "app-123", "seller_id": "seller-123", "trade_status": "TRADE_SUCCESS",
                  "out_trade_no": token, "trade_no": "ali-123", "total_amount": "9.90", "sign_type": "RSA2"}
        values.update(overrides)
        text = "&".join(f"{key}={values[key]}" for key in sorted(values) if key != "sign_type" and values[key])
        values["sign"] = base64.b64encode(self.private.sign(text.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
        return values

    def test_verified_notification_activates_order_once(self):
        account = self.subscriber()
        order = self.store.create_order(account["id"], "monthly", NOW)
        notification = self.notification(order["order_token"])
        self.provider.notify(notification, NOW)
        self.provider.notify(notification, NOW)
        self.assertEqual(self.store.subscription(account["id"])["expires_at"], "2026-12-15T11:00:00+00:00")

    def test_tampered_signature_app_seller_amount_and_status_are_rejected(self):
        account = self.subscriber()
        order = self.store.create_order(account["id"], "monthly", NOW)
        for overrides in ({"app_id": "other"}, {"seller_id": "other"}, {"trade_status": "WAIT_BUYER_PAY"},
                          {"total_amount": "9.901"}, {"total_amount": "NaN"}, {"total_amount": "0.01"},
                          {"sign_type": "RSA"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.provider.notify(self.notification(order["order_token"], **overrides), NOW)
        notification = self.notification(order["order_token"])
        notification["total_amount"] = "99.00"
        with self.assertRaises(ValueError):
            self.provider.notify(notification, NOW)
        self.assertIsNone(self.store.subscription(account["id"]))

    def test_precreate_request_carries_signed_order_and_amount(self):
        account = self.subscriber()
        order = self.store.create_order(account["id"], "monthly", NOW)
        params = self.provider.precreate_parameters(order, NOW)
        business = json.loads(params["biz_content"])
        self.assertEqual(business["out_trade_no"], order["order_token"])
        self.assertEqual(business["total_amount"], "9.90")
        signed = "&".join(f"{key}={params[key]}" for key in sorted(params) if key != "sign" and params[key])
        self.private.public_key().verify(base64.b64decode(params["sign"]), signed.encode(), padding.PKCS1v15(), hashes.SHA256())

    def gateway_response(self, token, valid=True):
        content = {"code": "10000", "out_trade_no": token, "qr_code": "https://qr.alipay.com/test-order"}
        raw = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        signature = base64.b64encode(self.private.sign(raw.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
        response = requests.Response()
        response.status_code = 200
        response._content = ('{"alipay_trade_precreate_response":' + raw + ',"sign":' + json.dumps(signature if valid else "fake") + '}').encode()
        response.encoding = "utf-8"
        return response

    def test_signed_precreate_response_is_verified_before_saving_qr(self):
        account = self.subscriber()
        order = self.store.create_order(account["id"], "monthly", NOW)
        with patch("subscription.payments.requests.post", return_value=self.gateway_response(order["order_token"])):
            self.provider.precreate(order, NOW)
        self.assertEqual(self.store.order(order["order_token"])["qr_code"], "https://qr.alipay.com/test-order")
        with patch("subscription.payments.requests.post", return_value=self.gateway_response(order["order_token"], valid=False)):
            with self.assertRaises(ValueError):
                self.provider.precreate(order, NOW)

    def test_weekly_order_uses_three_yuan_in_signed_precreate_and_callback(self):
        from fastapi.testclient import TestClient
        from web.app import create_app
        from subscription.store import utcnow
        account = self.subscriber()
        order = self.store.create_order(account["id"], "weekly", utcnow())
        headers = {"Authorization": "Bearer " + account["manage_token"]}
        with TestClient(create_app(self.store, payment=self.provider)) as client:
            with patch("subscription.payments.requests.post", return_value=self.gateway_response(order["order_token"])) as gateway:
                response = client.post("/api/orders", headers=headers, json={"plan": "weekly"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["amount"], 300)
            self.assertEqual(json.loads(gateway.call_args.kwargs["data"]["biz_content"])["total_amount"], "3.00")
            notification = self.notification(order["order_token"], total_amount="3.00", trade_no="ali-weekly")
            self.assertEqual(client.post("/api/payments/alipay/notify", data=notification).text, "success")
            expiry = self.store.subscription(account["id"])["expires_at"]
            self.assertEqual(client.post("/api/payments/alipay/notify", data=notification).text, "success")
            self.assertEqual(self.store.subscription(account["id"])["expires_at"], expiry)

    def test_real_payment_api_route_verifies_callback_and_enforces_order_ownership(self):
        from fastapi.testclient import TestClient
        from web.app import create_app
        from subscription.delivery import Delivery
        from subscription.store import utcnow
        account = self.subscriber()
        other = self.subscriber("other@example.com")
        headers = {"Authorization": "Bearer " + account["manage_token"]}
        delivery = Delivery(self.store, "https://brief.example.com", "Brief <brief@example.com>")
        with TestClient(create_app(self.store, delivery, self.provider)) as client:
            order = self.store.create_order(account["id"], "monthly", utcnow())
            with patch("subscription.payments.requests.post", return_value=self.gateway_response(order["order_token"])):
                response = client.post("/api/orders", headers=headers, json={"plan": "monthly"})
            self.assertEqual(response.status_code, 200, response.text)
            token = response.json()["order_token"]
            self.assertEqual(client.get(f"/api/orders/{token}/qr", headers=headers).headers["content-type"], "image/svg+xml")
            foreign = {"Authorization": "Bearer " + other["manage_token"]}
            self.assertEqual(client.get(f"/api/orders/{token}", headers=foreign).status_code, 404)
            response = client.post("/api/payments/alipay/notify", data=self.notification(token))
            self.assertEqual(response.text, "success")
            self.assertEqual(client.get(f"/api/orders/{token}", headers=headers).json()["status"], "paid")


if __name__ == "__main__":
    unittest.main()
