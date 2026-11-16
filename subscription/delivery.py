"""Persistent outbox. Scan creates tasks, workers perform isolated deliveries."""

import json
import os
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import requests

from .render import links, render_issue, render_notice
from .store import BEIJING, EDITIONS, GRACE_DAYS, stamp, utcnow


class ResendClient:
    def __init__(self, api_key=None, base_url="https://api.resend.com"):
        self.api_key = api_key or os.environ.get("RESEND_API_KEY", "")
        self.base_url = base_url.rstrip("/")

    def send(self, payload, key):
        if not self.api_key:
            raise ValueError("缺少 RESEND_API_KEY")
        response = requests.post(self.base_url + "/emails", json=payload, timeout=15,
                                 headers={"Authorization": "Bearer " + self.api_key, "Idempotency-Key": key})
        response.raise_for_status()
        email_id = response.json().get("id")
        if not email_id:
            raise ValueError("邮件服务商未返回邮件 ID，请核查")
        return email_id


def eligibility(row, now):
    if row["status"] != "active" or not row["confirmed_at"] or row["suppressed_at"]:
        return None
    if row["expires_at"]:
        expires = datetime.fromisoformat(row["expires_at"])
        if expires > now:
            return "paid"
        if expires + timedelta(days=GRACE_DAYS) > now:
            return "grace"
        return None
    return "trial" if row["trial_remaining"] > 0 else None


ACCOUNT_QUERY = """SELECT s.*, p.expires_at FROM subscribers s
                   LEFT JOIN subscriptions p ON p.subscriber_id=s.id"""


class Delivery:
    def __init__(self, store, base_url=None, sender=None, client=None):
        self.store = store
        self.base_url = (base_url or os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("PUBLIC_BASE_URL 必须为完整网站地址")
        self.sender = sender or os.environ.get("RESEND_FROM", "")
        self.client = client or ResendClient()

    def payload(self, row, subject, html, text):
        token = self.store.token(row)
        return {"from": self.sender, "to": [row["email"]], "subject": subject, "html": html, "text": text,
                "headers": {"List-Unsubscribe": f"<{self.base_url}/api/unsubscribe?token={token}>",
                            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}}

    def enqueue(self, conn, row, task_type, key, payload, now, issue_id=None):
        task_id = uuid4().hex
        cursor = conn.execute("""INSERT OR IGNORE INTO delivery_log(id,subscriber_id,task_type,business_key,issue_id,
            status,payload,idempotency_key,created_at,next_attempt_at) VALUES(?,?,?,?,?,'pending',?,?,?,?)""",
            (task_id, row["id"], task_type, key, issue_id, json.dumps(payload, ensure_ascii=False, sort_keys=True),
             "brief/" + task_id, stamp(now), stamp(now)))
        return cursor.rowcount

    def enqueue_confirmation(self, account, now=None):
        now = now or utcnow()
        with self.store.transaction() as conn:
            row = conn.execute("SELECT * FROM subscribers WHERE id=?", (account["id"],)).fetchone()
            confirmation_url = f"{self.base_url}/confirm#{self.store.token(row, 'confirm')}"
            manage_url, unsubscribe_url = links(self.base_url, self.store.token(row))
            from html import escape
            html = (f'<h1>确认订阅 Daily Brief Radar</h1><p>北京时间每天 08:00 和 20:00 各一封。确认后开始三期免费试读，按邮件期数计算。</p>'
                    f'<p><a href="{escape(confirmation_url)}">确认我的邮箱</a></p><p>链接两天内有效。</p>'
                    f'<p><a href="{escape(manage_url)}">管理偏好</a> · <a href="{escape(unsubscribe_url)}">退订</a></p>')
            text = (f"确认订阅 Daily Brief Radar，开始三期免费试读。北京时间每天 08:00 和 20:00 各一封，按邮件期数计算。\n{confirmation_url}"
                    f"\n管理偏好 {manage_url}\n退订 {unsubscribe_url}")
            payload = self.payload(row, "请确认你的简报订阅", html, text)
            return self.enqueue(conn, row, "confirmation", row["token_nonce"], payload, now)

    def enqueue_login(self, email, now=None):
        """Email a personal-home link to a confirmed subscriber. Unknown or stopped addresses queue nothing."""
        now = now or utcnow()
        with self.store.transaction() as conn:
            row = conn.execute("SELECT * FROM subscribers WHERE email=?", (email,)).fetchone()
            if row is None or row["status"] not in ("active", "expired") or not row["confirmed_at"] or row["suppressed_at"]:
                return 0
            body = "点击下面的链接进入你的个人主页，查看关注内容、调整板块或续费。如果不是你本人申请，请忽略此邮件。"
            html, text = render_notice("进入你的简报主页", body, self.base_url, self.store.token(row), action="进入我的主页")
            payload = self.payload(row, "你的 Daily Brief Radar 登录链接", html, text)
            return self.enqueue(conn, row, "login_link", uuid4().hex, payload, now)

    def enqueue_issue(self, issue_id, now=None):
        now = now or utcnow()
        count = 0
        with self.store.transaction() as conn:
            issue = conn.execute("SELECT * FROM daily_issue WHERE id=?", (issue_id,)).fetchone()
            if issue is None:
                raise ValueError("内容快照不存在")
            if issue["issue_date"] != now.astimezone(BEIJING).date().isoformat():
                raise ValueError("仅发送当天快照")
            if issue["edition"] not in EDITIONS or now.astimezone(BEIJING).hour < EDITIONS[issue["edition"]][0]:
                return 0
            sections = json.loads(issue["sections"])
            for row in conn.execute(ACCOUNT_QUERY).fetchall():
                mode = eligibility(row, now)
                if mode is None:
                    continue
                if mode == "trial":
                    reserved = conn.execute("""SELECT count(*) FROM delivery_log WHERE subscriber_id=?
                        AND task_type='daily_brief' AND status IN ('pending','sending','failed','needs_review')""", (row["id"],)).fetchone()[0]
                    if reserved >= row["trial_remaining"]:
                        continue
                html, text = render_issue(issue["issue_date"], sections, json.loads(row["preferences"]),
                                          self.base_url, self.store.token(row), mode == "grace", issue["edition"])
                label = EDITIONS[issue["edition"]][1]
                payload = self.payload(row, f"Daily Brief Radar · {issue['issue_date']} {label}", html, text)
                count += self.enqueue(conn, row, "daily_brief", issue["issue_date"] + "/" + issue["edition"], payload, now, issue_id)
        return count

    def enqueue_reminders(self, now=None):
        now = now or utcnow()
        count = 0
        with self.store.transaction() as conn:
            for row in conn.execute(ACCOUNT_QUERY).fetchall():
                if row["status"] in ("pending", "unsubscribed") or row["suppressed_at"]:
                    continue
                reminder = None
                cycle = row["expires_at"] or "trial"
                if row["expires_at"]:
                    expiry = datetime.fromisoformat(row["expires_at"])
                    days = (expiry.astimezone(BEIJING).date() - now.astimezone(BEIJING).date()).days
                    reminder = {7: "t7", 3: "t3", 1: "t1"}.get(days)
                    if reminder == "t7":
                        # A seven-day plan is always seven days from expiry right after payment, so the notice would be noise.
                        plan = conn.execute("""SELECT o.plan FROM subscriptions p JOIN orders o ON o.id=p.expiry_order_id
                                               WHERE p.subscriber_id=?""", (row["id"],)).fetchone()
                        if plan and plan["plan"] == "weekly":
                            reminder = None
                    if now >= expiry + timedelta(days=GRACE_DAYS):
                        reminder = "paused"
                        conn.execute("UPDATE subscriptions SET status='expired',updated_at=? WHERE subscriber_id=?", (stamp(now), row["id"]))
                        conn.execute("UPDATE subscribers SET status='expired' WHERE id=?", (row["id"],))
                    elif now >= expiry:
                        reminder = "expired"
                        conn.execute("UPDATE subscriptions SET status='grace_period',updated_at=? WHERE subscriber_id=?", (stamp(now), row["id"]))
                elif row["trial_remaining"] <= 1:
                    reminder = "trial_end"
                if reminder is None:
                    continue
                titles = {"t7": "你的简报订阅还有七天到期", "t3": "你的简报订阅还有三天到期",
                          "t1": "你的简报订阅明天到期", "expired": "订阅已到期，进入三天宽限期",
                          "paused": "每日简报已暂停，续费后恢复", "trial_end": "三期免费试读即将结束"}
                title = titles[reminder]
                html, text = render_notice(title, "点击管理订阅选择套餐并手动续费。提前续费会保留剩余时间。", self.base_url, self.store.token(row))
                payload = self.payload(row, title, html, text)
                payload["tags"] = [{"name": "reminder", "value": reminder}]
                count += self.enqueue(conn, row, "renewal_reminder", cycle + "/" + reminder, payload, now)
        return count

    def run_once(self, now=None):
        now = now or utcnow()
        with self.store.transaction() as conn:
            task = conn.execute("""SELECT * FROM delivery_log WHERE
                (status IN ('pending','failed') AND next_attempt_at<=? AND attempts<6)
                OR (status='sending' AND lease_until<=?) ORDER BY (task_type='daily_brief'),created_at,id LIMIT 1""", (stamp(now), stamp(now))).fetchone()
            if task is None:
                return None
            first = task["first_attempt_at"]
            if first and now - datetime.fromisoformat(first) >= timedelta(hours=23):
                conn.execute("UPDATE delivery_log SET status='needs_review',error_message='provider idempotency window exceeded' WHERE id=?", (task["id"],))
                return "needs_review"
            conn.execute("""UPDATE delivery_log SET status='sending',first_attempt_at=COALESCE(first_attempt_at,?),
                lease_until=?,attempts=attempts+1 WHERE id=?""", (stamp(now), stamp(now + timedelta(minutes=10)), task["id"]))
        # Keep the writer lock through the HTTP request so unsubscribe and another worker
        # cannot interleave with the final eligibility check and provider submission.
        with self.store.transaction() as conn:
            current = conn.execute("SELECT status,attempts FROM delivery_log WHERE id=?", (task["id"],)).fetchone()
            if current["status"] != "sending" or current["attempts"] != task["attempts"] + 1:
                return "skipped"
            row = conn.execute(ACCOUNT_QUERY + " WHERE s.id=?", (task["subscriber_id"],)).fetchone()
            allowed = row and row["status"] != "unsubscribed" and not row["suppressed_at"]
            if task["task_type"] == "daily_brief":
                allowed = allowed and eligibility(row, now) is not None
            elif task["task_type"] == "login_link":
                # A login link is only useful right after it was requested.
                allowed = (allowed and row["status"] in ("active", "expired") and row["confirmed_at"]
                           and task["created_at"] >= stamp(now - timedelta(hours=1)))
            elif task["task_type"] == "confirmation":
                # Re-subscription confirmations may be sent to an explicitly unsubscribed account.
                allowed = row and not row["suppressed_at"] and row["token_nonce"] == task["business_key"] and row["confirmation_expires_at"] >= stamp(now)
            else:
                cycle = task["business_key"].rsplit("/", 1)[0]
                allowed = allowed and (row["expires_at"] or "trial") == cycle
            if not allowed:
                conn.execute("UPDATE delivery_log SET status='skipped',error_message='recipient no longer eligible',lease_until=NULL WHERE id=?", (task["id"],))
                return "skipped"
            try:
                if not self.sender:
                    raise ValueError("缺少 RESEND_FROM")
                provider_id = self.client.send(json.loads(task["payload"]), task["idempotency_key"])
            except (requests.RequestException, ValueError) as error:
                retry_at = now + timedelta(seconds=min(60 * (2 ** task["attempts"]), 3600))
                conn.execute("UPDATE delivery_log SET status='failed',error_message=?,next_attempt_at=?,lease_until=NULL WHERE id=?",
                             (type(error).__name__, stamp(retry_at), task["id"]))
                return "failed"
            conn.execute("UPDATE delivery_log SET status='success',sent_at=?,provider_id=?,error_message=NULL,lease_until=NULL WHERE id=?",
                         (stamp(now), provider_id, task["id"]))
            if task["task_type"] == "daily_brief" and eligibility(row, now) == "trial":
                conn.execute("UPDATE subscribers SET trial_remaining=trial_remaining-1 WHERE id=?", (row["id"],))
            if task["task_type"] == "renewal_reminder":
                cycle, reminder = task["business_key"].rsplit("/", 1)
                conn.execute("INSERT OR IGNORE INTO renewal_reminders VALUES(?,?,?,?)", (row["id"], cycle, reminder, stamp(now)))
            return "success"
