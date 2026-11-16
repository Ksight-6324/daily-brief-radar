"""Transactional subscription state. Amounts are integer CNY fen, times are UTC."""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from .migrations import initialize


PLANS = {"weekly": (300, 7), "monthly": (990, 30), "quarterly": (2900, 90), "annual": (9900, 365)}
PLAN_LABELS = {"weekly": "周付", "monthly": "月付", "quarterly": "季付", "annual": "年付"}
SECTIONS = {
    "markets": "股票经济", "commodities": "石油黄金", "politics_mil": "政治军事",
    "history": "中外历史", "live_music": "音乐演出", "design_art": "设计美术",
    "album_film": "专辑影视", "ai": "人工智能", "growth": "自我提升",
    "science_pc": "物理化学", "biotech": "生物科技", "mind": "心灵疗养",
    "jobs": "就业机会", "sports_health": "体育健康", "gaming": "电竞游戏",
}
BEIJING = ZoneInfo("Asia/Shanghai")
EDITIONS = {"morning": (8, "早报"), "evening": (20, "晚报")}
GRACE_DAYS = 3


def utcnow():
    return datetime.now(timezone.utc)


def edition_for(now):
    return "evening" if now.astimezone(BEIJING).hour >= 20 else "morning"


def stamp(value):
    if value.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def normalize_email(value):
    email = value.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email):
        raise ValueError("请输入有效邮箱")
    return email


def preferences(value):
    if not isinstance(value, list) or any(key not in SECTIONS for key in value):
        raise ValueError("请选择有效板块")
    return sorted(set(value))


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("BRIEF_DB_PATH", "var/brief.db")).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        secret = os.environ.get("BRIEF_TOKEN_SECRET", "")
        if secret and len(secret) < 32:
            raise ValueError("BRIEF_TOKEN_SECRET 至少 32 个字符")
        if not secret:
            key_path = self.path.with_suffix(self.path.suffix + ".key")
            temporary_key = key_path.with_name(key_path.name + "." + uuid4().hex)
            temporary_key.write_text(secrets.token_hex(32), encoding="ascii")
            os.chmod(temporary_key, 0o600)
            try:
                os.link(temporary_key, key_path)
            except FileExistsError:
                pass
            finally:
                temporary_key.unlink()
            secret = key_path.read_text(encoding="ascii")
        self.secret = secret.encode()
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            initialize(conn)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def token(self, row, purpose="manage"):
        message = f"{row['id']}.{row['token_nonce']}.{purpose}"
        signature = hmac.new(self.secret, message.encode(), hashlib.sha256).hexdigest()
        return f"{row['id']}.{signature}"

    def account_for_token(self, token, purpose="manage", conn=None):
        if not isinstance(token, str) or len(token) > 160:
            raise ValueError("链接无效")
        subscriber_id = token.split(".", 1)[0]
        if conn is None:
            with self.connect() as connection:
                return self.account_for_token(token, purpose, connection)
        row = conn.execute("SELECT * FROM subscribers WHERE id=?", (subscriber_id,)).fetchone()
        if row is None or not hmac.compare_digest(self.token(row, purpose), token):
            raise ValueError("链接无效")
        return dict(row)

    def subscriber(self, subscriber_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM subscribers WHERE id=?", (subscriber_id,)).fetchone()
            return dict(row) if row else None

    def subscription(self, subscriber_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM subscriptions WHERE subscriber_id=?", (subscriber_id,)).fetchone()
            return dict(row) if row else None

    def order(self, order_token):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM orders WHERE order_token=?", (order_token,)).fetchone()
            return dict(row) if row else None

    def issue(self, issue_date, edition):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM daily_issue WHERE issue_date=? AND edition=?", (issue_date, edition)).fetchone()
            return dict(row) if row else None

    def recent_issues(self, limit):
        """Newest frozen morning and evening issues first. Legacy snapshots are not shown."""
        with self.connect() as conn:
            rows = conn.execute("""SELECT issue_date,edition,sections FROM daily_issue
                WHERE edition IN ('morning','evening')
                ORDER BY issue_date DESC, CASE edition WHEN 'evening' THEN 1 ELSE 0 END DESC LIMIT ?""", (limit,)).fetchall()
            return [dict(row) for row in rows]

    def register(self, email, selected, now=None):
        now = now or utcnow()
        email = normalize_email(email)
        selected = preferences(selected)
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM subscribers WHERE email=?", (email,)).fetchone()
            if row and row["status"] not in ("pending", "unsubscribed"):
                return None
            if row and row["suppressed_at"]:
                return None
            if row and row["confirmation_expires_at"] > stamp(now):
                return {"id": row["id"], "confirmation_token": self.token(row, "confirm"),
                        "manage_token": self.token(row)}
            subscriber_id = row["id"] if row else uuid4().hex
            nonce = secrets.token_hex(16)
            conn.execute(
                """INSERT INTO subscribers(id,email,status,preferences,token_nonce,confirmation_expires_at,created_at)
                VALUES(?,?,'pending',?,?,?,?) ON CONFLICT(email) DO UPDATE SET
                token_nonce=excluded.token_nonce, confirmation_expires_at=excluded.confirmation_expires_at,
                preferences=excluded.preferences""",
                (subscriber_id, email, json.dumps(selected), nonce, stamp(now + timedelta(days=2)), stamp(now)),
            )
            account = dict(conn.execute("SELECT * FROM subscribers WHERE id=?", (subscriber_id,)).fetchone())
            conn.execute("""UPDATE delivery_log SET status='skipped',error_message='account links rotated'
                            WHERE subscriber_id=? AND status IN ('pending','failed')""", (subscriber_id,))
            return {"id": subscriber_id, "confirmation_token": self.token(account, "confirm"),
                    "manage_token": self.token(account)}

    def confirm(self, token, now=None):
        now = now or utcnow()
        with self.transaction() as conn:
            row = self.account_for_token(token, "confirm", conn)
            if row["confirmation_expires_at"] < stamp(now) or row["suppressed_at"]:
                raise ValueError("确认链接已过期或邮箱已停用，请重新申请")
            conn.execute("UPDATE subscribers SET status='active', confirmed_at=?, unsubscribed_at=NULL WHERE id=?",
                         (stamp(now), row["id"]))
            return row["id"]

    def set_preferences(self, token, selected, now=None):
        selected = preferences(selected)
        with self.transaction() as conn:
            row = self.account_for_token(token, conn=conn)
            conn.execute("UPDATE subscribers SET preferences=? WHERE id=?", (json.dumps(selected), row["id"]))

    def unsubscribe(self, token, now=None):
        with self.transaction() as conn:
            row = self.account_for_token(token, conn=conn)
            now = now or utcnow()
            conn.execute("""UPDATE subscribers SET status='unsubscribed', unsubscribed_at=?,
                            confirmation_expires_at=? WHERE id=?""",
                         (stamp(now), stamp(now - timedelta(seconds=1)), row["id"]))

    def create_order(self, subscriber_id, plan, now=None):
        if plan not in PLANS:
            raise ValueError("请选择有效套餐")
        with self.transaction() as conn:
            if not conn.execute("SELECT 1 FROM subscribers WHERE id=?", (subscriber_id,)).fetchone():
                raise ValueError("用户不存在")
            order_token = "DBR" + uuid4().hex
            conn.execute("""INSERT INTO orders(id,order_token,subscriber_id,plan,amount,status,created_at)
                            VALUES(?,?,?,?,?,'pending',?)""",
                         (uuid4().hex, order_token, subscriber_id, plan, PLANS[plan][0], stamp(now or utcnow())))
        return self.order(order_token)

    def confirm_payment(self, order_token, payment_ref, amount, now=None):
        """Call only after verifying the payment provider's authenticated notification."""
        now = now or utcnow()
        if not order_token or not payment_ref or not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValueError("支付字段无效")
        with self.transaction() as conn:
            reused = conn.execute("SELECT * FROM orders WHERE payment_ref=?", (payment_ref,)).fetchone()
            if reused and (reused["order_token"] != order_token or reused["amount"] != amount):
                raise ValueError("支付流水已关联其他订单")
            order = conn.execute("SELECT * FROM orders WHERE order_token=?", (order_token,)).fetchone()
            if order is None:
                conn.execute("""INSERT INTO orders(id,order_token,amount,payment_ref,status,created_at,paid_at)
                                VALUES(?,?,?,?,'unmatched',?,?)""",
                             (uuid4().hex, order_token, amount, payment_ref, stamp(now), stamp(now)))
                return "unmatched"
            if order["amount"] != amount:
                raise ValueError("支付金额与订单不一致")
            if order["status"] in ("paid", "refunded", "unmatched"):
                if order["payment_ref"] != payment_ref:
                    raise ValueError("订单已关联其他支付流水")
                return order["status"]
            current = conn.execute("SELECT expires_at FROM subscriptions WHERE subscriber_id=?",
                                   (order["subscriber_id"],)).fetchone()
            start = max(datetime.fromisoformat(current["expires_at"]), now) if current else now
            expires_at = stamp(start + timedelta(days=PLANS[order["plan"]][1]))
            conn.execute("UPDATE orders SET status='paid', payment_ref=?, paid_at=?, applied_expires_at=? WHERE id=?",
                         (payment_ref, stamp(now), expires_at, order["id"]))
            conn.execute("""INSERT INTO subscriptions(subscriber_id,status,expires_at,expiry_order_id,updated_at)
                            VALUES(?,'active',?,?,?) ON CONFLICT(subscriber_id) DO UPDATE SET
                            status='active',expires_at=excluded.expires_at,expiry_order_id=excluded.expiry_order_id,
                            updated_at=excluded.updated_at""",
                         (order["subscriber_id"], expires_at, order["id"], stamp(now)))
            conn.execute("UPDATE subscribers SET status='active' WHERE id=? AND confirmed_at IS NOT NULL AND status != 'unsubscribed'",
                         (order["subscriber_id"],))
            return "paid"

    def freeze_issue(self, issue_date, sections, now=None, edition=None):
        now = now or utcnow()
        edition = edition or edition_for(now)
        date.fromisoformat(issue_date)
        if edition not in EDITIONS:
            raise ValueError("请选择早报或晚报版次")
        if not sections or not set(sections).issubset(SECTIONS) or any(not isinstance(value, str) or not value.strip() for value in sections.values()):
            raise ValueError("快照必须包含有效主题内容")
        payload = json.dumps(sections, sort_keys=True, ensure_ascii=False)
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM daily_issue WHERE issue_date=? AND edition=?", (issue_date, edition)).fetchone()
            if row:
                if row["sections"] != payload:
                    raise ValueError("该期快照已冻结，禁止覆盖")
                return row["id"]
            issue_id = uuid4().hex
            conn.execute("INSERT INTO daily_issue(id,issue_date,edition,sections,generated_at) VALUES(?,?,?,?,?)",
                         (issue_id, issue_date, edition, payload, stamp(now)))
            return issue_id

    def allow_request(self, bucket, limit, now=None):
        now = now or utcnow()
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM request_limits WHERE bucket=?", (bucket,)).fetchone()
            if row and datetime.fromisoformat(row["window_start"]) > now - timedelta(hours=1):
                if row["count"] >= limit:
                    return False
                conn.execute("UPDATE request_limits SET count=count+1 WHERE bucket=?", (bucket,))
            else:
                conn.execute("INSERT OR REPLACE INTO request_limits VALUES(?,?,1)", (bucket, stamp(now)))
            return True
