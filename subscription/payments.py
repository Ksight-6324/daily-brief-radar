"""Alipay face-to-face payment with RSA2 signed requests and notifications."""

import base64
import json
import os
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .store import utcnow


def canonical(values, notification=False):
    excluded = {"sign", "sign_type"} if notification else {"sign"}
    return "&".join(f"{key}={values[key]}" for key in sorted(values) if key not in excluded and values[key] != "")


class Alipay:
    def __init__(self, store, app_id=None, seller_id=None, public_key=None, private_key=None, notify_url=None):
        self.store = store
        self.app_id = app_id or os.environ.get("ALIPAY_APP_ID", "")
        self.seller_id = seller_id or os.environ.get("ALIPAY_SELLER_ID", "")
        self.public_key = public_key or os.environ.get("ALIPAY_PUBLIC_KEY", "")
        self.private_key = private_key or os.environ.get("ALIPAY_PRIVATE_KEY", "")
        self.notify_url = notify_url or os.environ.get("PUBLIC_BASE_URL", "").rstrip("/") + "/api/payments/alipay/notify"
        self.gateway = "https://openapi.alipay.com/gateway.do"
        if os.environ.get("ALIPAY_SANDBOX") == "1":
            self.gateway = "https://openapi-sandbox.dl.alipaydev.com/gateway.do"

    def verify(self, text, signature):
        try:
            key = serialization.load_pem_public_key(self.public_key.replace("\\n", "\n").encode())
            key.verify(base64.b64decode(signature, validate=True), text.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        except (ValueError, TypeError, InvalidSignature) as error:
            raise ValueError("支付宝签名验证失败") from error

    def notify(self, values, now=None):
        if not self.app_id or not self.seller_id or not self.public_key:
            raise ValueError("支付宝尚未配置")
        if values.get("sign_type") != "RSA2":
            raise ValueError("仅接受 RSA2 签名")
        self.verify(canonical(values, notification=True), values.get("sign", ""))
        if values.get("app_id") != self.app_id or values.get("seller_id") != self.seller_id:
            raise ValueError("支付宝应用或收款方不匹配")
        if values.get("trade_status") not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            raise ValueError("交易尚未支付成功")
        try:
            amount = Decimal(values["total_amount"])
            if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
                raise ValueError("无效支付金额")
            amount_fen = int(amount * 100)
        except (KeyError, InvalidOperation, OverflowError) as error:
            raise ValueError("无效支付金额") from error
        return self.store.confirm_payment(values.get("out_trade_no"), values.get("trade_no"), amount_fen, now)

    def precreate_parameters(self, order, now=None):
        if not self.app_id or not self.seller_id or not self.private_key or not self.notify_url.startswith("https://"):
            raise ValueError("支付服务尚未配置完整")
        now = now or utcnow()
        deadline = datetime.fromisoformat(order["created_at"]) + timedelta(minutes=30)
        if now >= deadline:
            raise ValueError("支付订单已过期，请重新创建")
        business = {"out_trade_no": order["order_token"], "total_amount": f"{order['amount'] / 100:.2f}",
                    "subject": "Daily Brief Radar 简报订阅", "time_expire": deadline.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")}
        values = {"app_id": self.app_id, "method": "alipay.trade.precreate", "format": "JSON", "charset": "utf-8",
                  "sign_type": "RSA2", "timestamp": now.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                  "version": "1.0", "notify_url": self.notify_url,
                  "biz_content": json.dumps(business, ensure_ascii=False, separators=(",", ":"))}
        key = serialization.load_pem_private_key(self.private_key.replace("\\n", "\n").encode(), password=None)
        values["sign"] = base64.b64encode(key.sign(canonical(values).encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
        return values

    def precreate(self, order, now=None):
        response = requests.post(self.gateway, data=self.precreate_parameters(order, now), timeout=15)
        response.raise_for_status()
        document = response.json()
        name = "alipay_trade_precreate_response"
        if name not in document or "sign" not in document:
            raise ValueError("支付宝返回无效响应")
        # Alipay signs the original JSON substring, including its original whitespace.
        start = response.text.index(":", response.text.index('"' + name + '"')) + 1
        raw = response.text[start:].lstrip()
        result, end = json.JSONDecoder().raw_decode(raw)
        self.verify(raw[:end], document["sign"])
        if result.get("code") != "10000" or result.get("out_trade_no") != order["order_token"]:
            raise ValueError("支付宝创建订单失败，请稍后重试")
        qr_code = result.get("qr_code", "")
        if not qr_code.startswith("https://qr.alipay.com/"):
            raise ValueError("支付宝返回无效收款链接")
        with self.store.transaction() as conn:
            conn.execute("UPDATE orders SET qr_code=? WHERE id=?", (qr_code, order["id"]))
        return qr_code
