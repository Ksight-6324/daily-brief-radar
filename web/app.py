import hashlib
import io
import json
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qsl

import qrcode
import qrcode.image.svg
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool

from subscription.delivery import Delivery
from subscription.payments import Alipay
from subscription.store import PLAN_LABELS, PLANS, SECTIONS, Store, stamp, utcnow


STATIC = Path(__file__).with_name("static")


class Signup(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    preferences: list[str] = Field(default_factory=list, max_length=len(SECTIONS))
    consent: StrictBool


class Confirmation(BaseModel):
    token: str = Field(min_length=1, max_length=160)


class Preferences(BaseModel):
    preferences: list[str] = Field(max_length=len(SECTIONS))


class OrderRequest(BaseModel):
    plan: str = Field(max_length=20)


def create_app(store=None, delivery=None, payment=None):
    load_dotenv(Path.cwd() / ".env")
    store = store or Store()
    delivery = delivery or Delivery(store)
    payment = payment or Alipay(store)
    app = FastAPI(title="Daily Brief Radar", docs_url=None, redoc_url=None)
    app.state.store = store

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
        return response

    def account(request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "请从邮件中的管理链接进入")
        try:
            return store.account_for_token(authorization[7:])
        except ValueError as error:
            raise HTTPException(401, "管理链接无效，请使用最新邮件中的链接") from error

    @app.exception_handler(ValueError)
    async def invalid_input(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.get("/health")
    def health():
        with store.connect() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/api/options")
    def options():
        return {
            "topics": [{"id": key, "label": label} for key, label in SECTIONS.items()],
            "plans": [{"id": key, "label": PLAN_LABELS[key], "amount": amount, "days": days}
                      for key, (amount, days) in PLANS.items()],
            "schedule": {"timezone": "Asia/Shanghai", "times": ["08:00", "20:00"]},
        }

    @app.post("/api/subscribe")
    def subscribe(values: Signup, request: Request):
        if values.consent is not True:
            raise HTTPException(422, "请主动同意通过邮箱接收每日简报")
        email_hash = hashlib.sha256(values.email.strip().lower().encode()).hexdigest()
        address = request.client.host if request.client else "unknown"
        ip_hash = hashlib.sha256(address.encode()).hexdigest()
        if not store.allow_request("email:" + email_hash, 3) or not store.allow_request("ip:" + ip_hash, 20):
            raise HTTPException(429, "申请较频繁，请一小时后重试")
        registered = store.register(values.email, values.preferences)
        if registered:
            delivery.enqueue_confirmation(registered)
        return {"message": "如果该邮箱可以订阅，我们会发送确认邮件。请检查收件箱和垃圾邮件文件夹。"}

    @app.post("/api/confirm")
    def confirm(values: Confirmation):
        subscriber_id = store.confirm(values.token)
        return {"manage_token": store.token(store.subscriber(subscriber_id))}

    @app.get("/api/account")
    def read_account(request: Request):
        row = account(request)
        sub = store.subscription(row["id"])
        return {"email": row["email"], "status": row["status"], "preferences": json.loads(row["preferences"]),
                "trial_remaining": row["trial_remaining"], "confirmed": bool(row["confirmed_at"]),
                "expires_at": sub["expires_at"] if sub else None,
                "payment_configured": bool(payment.app_id and payment.seller_id and payment.private_key and payment.public_key)}

    @app.post("/api/preferences")
    def update_preferences(values: Preferences, request: Request):
        row = account(request)
        store.set_preferences(store.token(row), values.preferences)
        return {"message": "偏好已保存，从下一期简报开始生效。"}

    @app.post("/api/unsubscribe")
    def unsubscribe(request: Request, token: str | None = None):
        if token is None:
            row = account(request)
            token = store.token(row)
        store.unsubscribe(token)
        return {"message": "已退订，每日简报已停止。"}

    @app.post("/api/orders")
    def create_order(values: OrderRequest, request: Request):
        row = account(request)
        if not row["confirmed_at"] or row["status"] == "unsubscribed":
            raise HTTPException(403, "请先确认邮箱并保持订阅状态")
        if not all((payment.app_id, payment.seller_id, payment.public_key, payment.private_key)):
            raise HTTPException(503, "支付服务尚未开放，请稍后重试")
        if not store.allow_request("orders:" + row["id"], 12):
            raise HTTPException(429, "创建订单较频繁，请稍后重试")
        with store.connect() as conn:
            existing = conn.execute("""SELECT order_token FROM orders WHERE subscriber_id=? AND plan=?
                AND status='pending' AND created_at>? ORDER BY created_at DESC LIMIT 1""",
                (row["id"], values.plan, stamp(utcnow() - timedelta(minutes=25)))).fetchone()
        order = store.order(existing["order_token"]) if existing else store.create_order(row["id"], values.plan)
        try:
            payment_url = order["qr_code"] or payment.precreate(order)
        except (requests.RequestException, ValueError) as error:
            raise HTTPException(502, "支付宝暂时无法创建收款码，请稍后重试") from error
        return {"order_token": order["order_token"], "amount": order["amount"], "payment_url": payment_url}

    def owned_order(order_token, request):
        row = account(request)
        order = store.order(order_token)
        if order is None or order["subscriber_id"] != row["id"]:
            raise HTTPException(404, "订单不存在")
        return order

    @app.get("/api/orders/{order_token}")
    def read_order(order_token: str, request: Request):
        order = owned_order(order_token, request)
        return {"order_token": order["order_token"], "status": order["status"], "amount": order["amount"]}

    @app.get("/api/orders/{order_token}/qr")
    def order_qr(order_token: str, request: Request):
        order = owned_order(order_token, request)
        if not order["qr_code"] or order["status"] != "pending":
            raise HTTPException(400, "订单当前没有可用收款码")
        image = qrcode.make(order["qr_code"], image_factory=qrcode.image.svg.SvgPathImage)
        buffer = io.BytesIO()
        image.save(buffer)
        return Response(buffer.getvalue(), media_type="image/svg+xml")

    @app.post("/api/payments/alipay/notify", response_class=PlainTextResponse)
    async def notify(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 65536:
                return PlainTextResponse("failure", status_code=413)
            body.extend(chunk)
        try:
            fields = parse_qsl(body.decode("utf-8"), keep_blank_values=True, max_num_fields=100, errors="strict")
            form = dict(fields)
            if len(fields) != len(form):
                raise ValueError("重复回调字段")
            # The verified callback is short and SQLite serializes updates from concurrent callbacks.
            from starlette.concurrency import run_in_threadpool
            await run_in_threadpool(payment.notify, form)
        except (ValueError, UnicodeError):
            return PlainTextResponse("failure", status_code=400)
        return "success"

    @app.get("/")
    @app.get("/confirm")
    @app.get("/manage")
    @app.get("/unsubscribe")
    def page():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
