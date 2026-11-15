import base64
import unittest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from subscription.payments import Alipay
from tests.test_subscription import NOW, StoreTest


class PaymentTest(StoreTest):
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

    def test_precreate_request_has_signed_order_and_validated_response(self):
        account = self.subscriber()
        order = self.store.create_order(account["id"], "monthly", NOW)
        params = self.provider.precreate_parameters(order, NOW)
        import json
        business = json.loads(params["biz_content"])
        self.assertEqual(business["out_trade_no"], order["order_token"])
        self.assertEqual(business["total_amount"], "9.90")
        signed = "&".join(f"{key}={params[key]}" for key in sorted(params) if key != "sign" and params[key])
        self.private.public_key().verify(base64.b64decode(params["sign"]), signed.encode(), padding.PKCS1v15(), hashes.SHA256())


if __name__ == "__main__":
    unittest.main()
