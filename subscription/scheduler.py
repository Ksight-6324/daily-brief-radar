"""Recoverable daily scan for a persistent host sharing the API/worker database."""

import os
import subprocess
import sys
from pathlib import Path

from .store import BEIJING, edition_for, utcnow


def schedule_once(store, delivery, now=None):
    now = now or utcnow()
    local = now.astimezone(BEIJING)
    queued = delivery.enqueue_reminders(now)
    if local.hour < 8:
        return queued
    edition = edition_for(now)
    issue = store.issue(local.date().isoformat(), edition)
    if issue is None:
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "BRIEF_MODE": "subscription", "BRIEF_DB_PATH": str(store.path),
               "BRIEF_EDITION": edition, "PYTHONUTF8": "1"}
        subprocess.run([sys.executable, "run_all.py"], cwd=root, env=env, check=True, timeout=1800)
        issue = store.issue(local.date().isoformat(), edition)
        if issue is None:
            raise ValueError("生成器未创建当天快照")
    return queued + delivery.enqueue_issue(issue["id"], now)
