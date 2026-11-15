"""Operational commands. Run from the repository root with python -m subscription.cli."""

import argparse
import json
import time
from pathlib import Path

from .delivery import Delivery
from .pipeline import freeze_markdown, generate_markdown
from .store import ROME, Store, utcnow


def main():
    parser = argparse.ArgumentParser(description="Daily Brief Radar subscription operations")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("status")
    commands.add_parser("scan")
    commands.add_parser("publish")
    commands.add_parser("generate")
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
    if args.command == "init":
        print("Database initialized")
    elif args.command == "register":
        selected = [value for value in args.preferences.split(",") if value]
        account = store.register(args.email, selected, now)
        if account:
            delivery.enqueue_confirmation(account, now)
        print("Confirmation requested")
    elif args.command == "generate":
        generate_markdown(now)
        print("Shared MVP content generated")
    elif args.command in ("freeze", "publish"):
        with store.connect() as conn:
            issue = conn.execute("SELECT id FROM daily_issue WHERE issue_date=?", (now.astimezone(ROME).date().isoformat(),)).fetchone()
        if issue and args.command == "publish":
            issue_id = issue["id"]
        else:
            text = Path(getattr(args, "input", "daily_brief.md")).read_text(encoding="utf-8-sig")
            issue_id = freeze_markdown(store, text, now)
        queued = delivery.enqueue_issue(issue_id, now) if args.command == "publish" else 0
        print(json.dumps({"issue_id": issue_id, "queued": queued}))
    elif args.command == "scan":
        with store.connect() as conn:
            issue = conn.execute("SELECT id FROM daily_issue WHERE issue_date=?", (now.astimezone(ROME).date().isoformat(),)).fetchone()
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
    elif args.command == "status":
        with store.connect() as conn:
            statuses = {row["status"]: row["count"] for row in conn.execute("SELECT status,count(*) AS count FROM delivery_log GROUP BY status")}
            unmatched = conn.execute("SELECT count(*) FROM orders WHERE status='unmatched'").fetchone()[0]
        print(json.dumps({"deliveries": statuses, "unmatched": unmatched}))


if __name__ == "__main__":
    main()
