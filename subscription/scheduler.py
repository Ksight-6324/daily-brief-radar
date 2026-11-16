"""Recoverable daily scan for a persistent host sharing the API/worker database."""

import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from .store import BEIJING, EDITIONS, edition_for, utcnow

# Content is generated this long before the release time so mail leaves at 08:00 and 20:00, not after generation.
LEAD = timedelta(minutes=30)


def ensure_issue(store, issue_date, edition):
    """Return the frozen snapshot, running the existing generator when it is missing."""
    issue = store.issue(issue_date, edition)
    if issue is None:
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "BRIEF_MODE": "subscription", "BRIEF_DB_PATH": str(store.path),
               "BRIEF_EDITION": edition, "PYTHONUTF8": "1"}
        subprocess.run([sys.executable, "run_all.py"], cwd=root, env=env, check=True, timeout=1800)
        issue = store.issue(issue_date, edition)
        if issue is None:
            raise ValueError("生成器未创建当天快照")
    return issue


def schedule_once(store, delivery, now=None):
    now = now or utcnow()
    local = now.astimezone(BEIJING)
    queued = delivery.enqueue_reminders(now)
    if local.hour >= EDITIONS["morning"][0]:
        # Release window: generate if missing, then queue. Recovers a scheduler that was down at release time.
        issue = ensure_issue(store, local.date().isoformat(), edition_for(now))
        queued += delivery.enqueue_issue(issue["id"], now)
    # Lead window: freeze the upcoming edition early. Queueing waits for its release hour.
    upcoming = (now + LEAD).astimezone(BEIJING)
    for edition, (hour, _) in EDITIONS.items():
        if local.hour < hour <= upcoming.hour and local.date() == upcoming.date():
            ensure_issue(store, local.date().isoformat(), edition)
    return queued
