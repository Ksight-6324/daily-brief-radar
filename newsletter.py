"""Content publisher for Museletter. Subscriber state belongs to the engine."""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import timedelta
from html import escape
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests
from dotenv import load_dotenv

from subscription.pipeline import generate_markdown, parse_markdown
from subscription.render import card
from subscription.store import BEIJING, EDITIONS, SECTIONS, edition_for, utcnow


class MuseletterError(RuntimeError):
    """A diagnostic safe to print without response bodies or request credentials."""


class Publisher:
    def __init__(self, path=None, session=None):
        self.url = os.environ.get("MUSELETTER_URL", "http://127.0.0.1:8000").rstrip("/")
        parsed = urlsplit(self.url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("MUSELETTER_URL must be an HTTP(S) server URL without credentials")
        self.api_key = os.environ.get("MUSELETTER_API_KEY", "").strip()
        self.list_ref = os.environ.get("MUSELETTER_LIST", "default").strip() or "default"
        self.tag = os.environ.get("MUSELETTER_TAG", "").strip() or None
        self.session = session or requests.Session()
        self.path = Path(path or os.environ.get("BRIEF_PUBLISH_DB_PATH", "var/publisher.db")).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS issues (
                business_key TEXT PRIMARY KEY, issue_date TEXT NOT NULL,
                edition TEXT NOT NULL, payload TEXT NOT NULL,
                campaign_id TEXT, submitted INTEGER NOT NULL DEFAULT 0
            )""")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=120, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def business_key(self, now, edition):
        if edition not in EDITIONS:
            raise ValueError("请选择早报或晚报版次")
        identity = [self.url, self.list_ref, self.tag, now.astimezone(BEIJING).date().isoformat(), edition]
        return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()

    def current(self, now, edition):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM issues WHERE business_key=?", (self.business_key(now, edition),)).fetchone()

    def prepare(self, content, now, edition):
        issue_date, edition, sections = parse_markdown(content, now, edition)
        subject = f"每日简报 · {EDITIONS[edition][1]} {issue_date}"
        parts = [f"<h1>{escape(subject)}</h1>", "<h2>本期必知</h2>"]
        observation = ""
        for index, key in enumerate(sections):
            if index == 3:
                parts.append("<h2>其他值得一扫</h2>")
            html, _, hint = card(sections[key])
            parts.append(f"<h2>{escape(SECTIONS[key])}</h2>\n{html}")
            if not observation:
                observation = hint
        parts.append(f"<h2>观察点</h2><p>{escape(observation)}</p>")
        payload = {"subject": subject, "body_markdown": "\n\n".join(parts),
                   "tag": self.tag, "track_opens": False}
        key = self.business_key(now, edition)
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO issues (business_key, issue_date, edition, payload) VALUES (?, ?, ?, ?)",
                         (key, issue_date, edition, json.dumps(payload, ensure_ascii=False)))
        return {"status": "prepared", "issue_date": issue_date, "edition": edition}

    def request(self, method, path, payload=None, key=None):
        if not self.api_key:
            raise MuseletterError("MUSELETTER_API_KEY is required for publishing")
        headers = {"Authorization": "Bearer " + self.api_key}
        if key:
            headers["Idempotency-Key"] = key
        try:
            response = self.session.request(method, self.url + path, json=payload, headers=headers,
                                            timeout=(10, 30), allow_redirects=False)
        except requests.RequestException:
            raise MuseletterError("Museletter request failed. Retry with the same publisher database.") from None
        if not 200 <= response.status_code < 300:
            raise MuseletterError(f"Museletter HTTP {response.status_code}. Check engine health and campaign test-send status.")
        try:
            result = response.json()
        except ValueError:
            raise MuseletterError("Museletter returned invalid JSON") from None
        if not isinstance(result, dict) or not isinstance(result.get("id"), str) or not result["id"]:
            raise MuseletterError("Museletter returned an invalid campaign")
        return result

    def publish(self, now, edition, skip_test=False, draft_only=False):
        local = now.astimezone(BEIJING)
        key = self.business_key(now, edition)
        if not draft_only and local.hour < EDITIONS[edition][0]:
            return {"status": "scheduled", "edition": edition}
        if not draft_only and edition != edition_for(now):
            raise ValueError("该版次已经超过发布窗口")
        with self.connect() as conn:
            # Serialize callers across processes sharing this file, including HTTP mutations.
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM issues WHERE business_key=?", (key,)).fetchone()
                if row is None:
                    raise ValueError("请先生成并冻结本期内容")
                campaign_id = row["campaign_id"]
                if row["submitted"]:
                    return {"status": "submitted", "campaign_id": campaign_id}
                if not campaign_id:
                    created = self.request("POST", f"/v1/lists/{quote(self.list_ref, safe='')}/campaigns",
                                           json.loads(row["payload"]), "brief-create-" + key)
                    campaign_id = created["id"]
                    conn.execute("UPDATE issues SET campaign_id=? WHERE business_key=?", (campaign_id, key))
                if draft_only:
                    return {"status": "draft", "campaign_id": campaign_id}
                path = "/v1/campaigns/" + quote(campaign_id, safe="")
                campaign = self.request("GET", path)
                if campaign.get("status") == "draft":
                    campaign = self.request("POST", path + "/send", {"confirm": True, "skip_test": skip_test},
                                            "brief-send-" + key)
                if campaign.get("status") not in ("sending", "sent"):
                    raise MuseletterError("Museletter campaign requires review before publishing")
                conn.execute("UPDATE issues SET submitted=1 WHERE business_key=?", (key,))
                return {"status": "submitted", "campaign_id": campaign_id}
            finally:
                # Preserve a successfully created campaign even when sending times out or fails.
                conn.commit()


def schedule_once(publisher, now=None):
    now = now or utcnow()
    local = now.astimezone(BEIJING)
    editions = [edition_for(now)] if local.hour >= 8 else []
    upcoming = (now + timedelta(minutes=30)).astimezone(BEIJING)
    editions += [edition for edition, (hour, _) in EDITIONS.items()
                 if local.date() == upcoming.date() and local.hour < hour <= upcoming.hour]
    for edition in editions:
        row = publisher.current(now, edition)
        if row is not None and row["submitted"]:
            continue
        env = {**os.environ, "BRIEF_MODE": "museletter", "BRIEF_EDITION": edition,
               "BRIEF_PUBLISH_DB_PATH": str(publisher.path), "PYTHONUTF8": "1"}
        subprocess.run([sys.executable, "run_all.py"], cwd=Path(__file__).resolve().parent,
                       env=env, check=True, timeout=1800)


def main():
    load_dotenv(Path.cwd() / ".env")
    parser = argparse.ArgumentParser(description="Daily Brief Radar Museletter publisher")
    parser.add_argument("--edition", choices=EDITIONS, default=os.environ.get("BRIEF_EDITION"))
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--input", default="daily_brief.md")
    commands.add_parser("generate")
    publish = commands.add_parser("publish")
    publish.add_argument("--draft", action="store_true", help="create the campaign for preview and testing without sending")
    publish.add_argument("--skip-test", action="store_true", default=os.environ.get("MUSELETTER_SKIP_TEST", "false").lower() == "true")
    commands.add_parser("status")
    schedule = commands.add_parser("schedule")
    schedule.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    publisher = Publisher()
    now = utcnow()
    edition = args.edition or edition_for(now)
    if args.command == "generate":
        result = publisher.prepare(generate_markdown(now, edition), now, edition)
    elif args.command == "prepare":
        result = publisher.prepare(Path(args.input).read_text(encoding="utf-8-sig"), now, edition)
    elif args.command == "publish":
        result = publisher.publish(now, edition, args.skip_test, args.draft)
    elif args.command == "status":
        row = publisher.current(now, edition)
        result = {"status": "missing" if row is None else "submitted" if row["submitted"] else "prepared",
                  "campaign_id": row["campaign_id"] if row else None, "edition": edition}
    else:
        while True:
            try:
                schedule_once(publisher)
            except (MuseletterError, ValueError, subprocess.SubprocessError):
                if not args.watch:
                    raise
                print("Newsletter schedule failed. Retrying on the next scan.", flush=True)
            if not args.watch:
                break
            time.sleep(60)
        return
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except MuseletterError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError):
        print("Newsletter command failed. Check configuration, input and engine campaign status.", file=sys.stderr)
        sys.exit(1)
