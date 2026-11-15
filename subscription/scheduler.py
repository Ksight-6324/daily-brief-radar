"""Recoverable daily scan for a persistent host sharing the API/worker database."""

import os
import subprocess
import sys
from pathlib import Path

from .store import ROME, utcnow


def schedule_once(store, delivery, now=None):
    now = now or utcnow()
    local = now.astimezone(ROME)
    queued = delivery.enqueue_reminders(now)
    if local.hour < 12:
        return queued
    with store.connect() as conn:
        issue = conn.execute("SELECT id FROM daily_issue WHERE issue_date=?", (local.date().isoformat(),)).fetchone()
    if issue is None:
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "BRIEF_MODE": "subscription", "BRIEF_DB_PATH": str(store.path), "PYTHONUTF8": "1"}
        subprocess.run([sys.executable, "run_all.py"], cwd=root, env=env, check=True, timeout=1800)
        with store.connect() as conn:
            issue = conn.execute("SELECT id FROM daily_issue WHERE issue_date=?", (local.date().isoformat(),)).fetchone()
        if issue is None:
            raise ValueError("生成器未创建当天快照")
    return queued + delivery.enqueue_issue(issue["id"], now)
