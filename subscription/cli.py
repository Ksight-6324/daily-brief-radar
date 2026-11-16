"""Operational commands. Run from the repository root with python -m subscription.cli."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from .delivery import Delivery
from .dnscheck import check_domain, domain_of
from .maintenance import backup_store, health, prune_logs
from .pipeline import freeze_markdown, generate_markdown
from .store import BEIJING, EDITIONS, Store, edition_for, utcnow


def main():
    load_dotenv(Path.cwd() / ".env")
    parser = argparse.ArgumentParser(description="Daily Brief Radar subscription operations")
    parser.add_argument("--edition", choices=EDITIONS, default=os.environ.get("BRIEF_EDITION"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("status")
    erase = commands.add_parser("delete-account")
    erase.add_argument("email")
    backup = commands.add_parser("backup")
    backup.add_argument("--to", default="backups")
    backup.add_argument("--keep-days", type=int, default=30, help="delete older backups after a new one succeeds, 0 keeps all")
    prune = commands.add_parser("prune")
    prune.add_argument("--days", type=int, default=30)
    check = commands.add_parser("check")
    check.add_argument("--ping", action="store_true", help="notify the configured external monitor after a healthy check")
    dns = commands.add_parser("check-dns")
    dns.add_argument("domain", nargs="?")
    dns.add_argument("--selector", default="resend")
    dns.add_argument("--send-label", default="send")
    commands.add_parser("scan")
    commands.add_parser("publish")
    commands.add_parser("generate")
    scheduler = commands.add_parser("schedule")
    scheduler.add_argument("--watch", action="store_true")
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--input", default="daily_brief.md")
    register = commands.add_parser("register")
    register.add_argument("email")
    register.add_argument("--preferences", default="")
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--limit", type=int, default=100)
    worker.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    store = Store()
    delivery = Delivery(store)
    now = utcnow()
    edition = args.edition or edition_for(now)
    if args.command == "init":
        print("Database initialized")
    elif args.command == "register":
        selected = [value for value in args.preferences.split(",") if value]
        account = store.register(args.email, selected, now)
        if account:
            delivery.enqueue_confirmation(account, now)
        print("Confirmation requested")
    elif args.command == "generate":
        generate_markdown(now, edition=edition)
        print("Shared edition content generated")
    elif args.command in ("freeze", "publish"):
        issue = store.issue(now.astimezone(BEIJING).date().isoformat(), edition)
        if issue and args.command == "publish":
            issue_id = issue["id"]
        else:
            text = Path(getattr(args, "input", "daily_brief.md")).read_text(encoding="utf-8-sig")
            issue_id = freeze_markdown(store, text, now, edition=edition)
        queued = delivery.enqueue_issue(issue_id, now) if args.command == "publish" else 0
        print(json.dumps({"issue_id": issue_id, "queued": queued}))
    elif args.command == "scan":
        issue = store.issue(now.astimezone(BEIJING).date().isoformat(), edition)
        count = delivery.enqueue_issue(issue["id"], now) if issue else 0
        count += delivery.enqueue_reminders(now)
        print(json.dumps({"queued": count}))
    elif args.command == "worker":
        while True:
            for _ in range(1 if args.once else args.limit):
                result = delivery.run_once()
                if result is None:
                    break
                print(result, flush=True)
            if not args.watch:
                break
            time.sleep(5)
    elif args.command == "schedule":
        from .scheduler import schedule_once
        while True:
            try:
                print(json.dumps({"queued": schedule_once(store, delivery)}), flush=True)
            except (ValueError, subprocess.SubprocessError) as error:
                if not args.watch:
                    raise
                print("Scheduler failed: " + type(error).__name__, flush=True)
                time.sleep(240)
            if not args.watch:
                break
            time.sleep(60)
    elif args.command == "delete-account":
        with store.connect() as conn:
            row = conn.execute("SELECT id FROM subscribers WHERE email=?", (args.email.strip().lower(),)).fetchone()
        if row is None:
            raise SystemExit("No account for that address")
        store.delete_account(row["id"], now)
        print("Account deleted")
    elif args.command == "backup":
        print(json.dumps(backup_store(store, Path(args.to), args.keep_days, now)))
    elif args.command == "prune":
        print(json.dumps(prune_logs(store, args.days, now)))
    elif args.command == "check":
        backup_dir = Path(os.environ["BRIEF_BACKUP_DIR"]) if os.environ.get("BRIEF_BACKUP_DIR") else None
        problems = health(store, now, backup_dir=backup_dir)
        ping_url = os.environ.get("HEALTHCHECK_PING_URL")
        if args.ping and ping_url and not any(level == "error" for level, _ in problems):
            try:
                requests.get(ping_url, timeout=10).raise_for_status()
            except requests.RequestException:
                problems.append(("error", "外部健康探测失败，请检查监控地址与网络"))
        print(json.dumps({"ok": not any(level == "error" for level, _ in problems),
                          "problems": [{"level": level, "message": message} for level, message in problems]}, ensure_ascii=False))
        if any(level == "error" for level, _ in problems):
            raise SystemExit(1)
    elif args.command == "check-dns":
        domain = args.domain or domain_of(os.environ.get("RESEND_FROM", ""))
        checks = check_domain(domain, selector=args.selector, send_label=args.send_label)
        print(json.dumps({"domain": domain, "checks": checks}, ensure_ascii=False, indent=1))
        if any(check["status"] == "fail" for check in checks):
            raise SystemExit(1)
    elif args.command == "status":
        with store.connect() as conn:
            statuses = {row["status"]: row["count"] for row in conn.execute("SELECT status,count(*) AS count FROM delivery_log GROUP BY status")}
            unmatched = conn.execute("SELECT count(*) FROM orders WHERE status='unmatched'").fetchone()[0]
        print(json.dumps({"deliveries": statuses, "unmatched": unmatched}))


if __name__ == "__main__":
    main()
