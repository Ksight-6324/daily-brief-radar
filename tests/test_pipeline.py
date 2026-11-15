import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from subscription.pipeline import freeze_markdown
from tests.support import NOW, StoreCase


class PipelineTest(StoreCase):
    def test_generated_markdown_becomes_a_frozen_mvp_snapshot(self):
        text = """DATE|||2026-11-15
SLOT|||noon
SECTION|||markets
Reuters|||市场消息|||https://example.com/1|||标签|||事实|||重要性
SECTION|||ai_tech
官方|||模型更新|||https://example.com/2|||标签|||事实|||重要性
SECTION|||jobs
官网|||岗位开放|||https://example.com/3|||标签|||事实|||重要性
"""
        issue = freeze_markdown(self.store, text, NOW)
        with self.store.connect() as conn:
            snapshot = conn.execute("SELECT * FROM daily_issue WHERE id=?", (issue,)).fetchone()
            sections = json.loads(snapshot["sections"])
        self.assertEqual(set(sections), {"markets", "ai", "jobs"})
        self.assertIn("模型更新", sections["ai"])
        with self.assertRaises(ValueError):
            freeze_markdown(self.store, text, NOW + timedelta(days=1))
        with self.assertRaises(ValueError):
            freeze_markdown(self.store, text.replace("SECTION|||jobs", "SECTION|||other"), NOW)

    def test_cli_initializes_and_scans_empty_persistent_database(self):
        env = {**os.environ, "BRIEF_DB_PATH": str(self.store.path)}
        root = Path(__file__).resolve().parents[1]
        for command in ("init", "scan", "status"):
            result = subprocess.run([sys.executable, "-m", "subscription.cli", command], cwd=root,
                                    env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unmatched", result.stdout)

    def test_normal_subscription_entry_reuses_frozen_content_and_only_enqueues(self):
        from tests.test_delivery import SECTIONS
        from subscription.store import utcnow, ROME
        self.subscriber()
        now = utcnow()
        self.store.freeze_issue(now.astimezone(ROME).date().isoformat(), SECTIONS, now)
        env = {**os.environ, "BRIEF_DB_PATH": str(self.store.path), "BRIEF_MODE": "subscription", "PYTHONUTF8": "1"}
        result = subprocess.run([sys.executable, "run_all.py"], cwd=Path(__file__).resolve().parents[1],
                                env=env, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.store.connect() as conn:
            rows = conn.execute("SELECT status,task_type FROM delivery_log").fetchall()
        self.assertEqual([(row["status"], row["task_type"]) for row in rows], [("pending", "daily_brief")])
