import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from subscription.pipeline import freeze_markdown, generate_markdown
from tests.support import NOW, StoreCase


class PipelineTest(StoreCase):
    def test_enabled_topics_have_collector_sources(self):
        from fetchers.fetch_category_news import CATEGORY_SOURCES
        from subscription.store import SECTIONS
        for key, label in SECTIONS.items():
            category = "历史" if key == "history" else label
            self.assertTrue(CATEGORY_SOURCES.get(category), label)

    def test_generator_includes_all_topics_and_separate_edition_metadata(self):
        from subscription.store import SECTIONS
        items = [{"category": "历史" if key == "history" else label, "title": label,
                  "url": "https://example.com/" + key} for key, label in SECTIONS.items()]
        summarizer = SimpleNamespace(
            load_json=lambda _: {"items": items}, load_seen=lambda _: (set(), [], []),
            filter_seen=lambda candidates, _: candidates, NO_ADVICE_RULE="依据原始来源。",
            summarize_one_pick=lambda label, candidates, *args: f"官方|||{label}|||{candidates[0]['url']}|||主题|||事实|||影响",
            save_seen=Mock(),
        )
        with patch.dict(sys.modules, {"summarize": summarizer}), patch("subscription.pipeline.Path.write_text"):
            text = generate_markdown(NOW, edition="morning")
        self.assertIn("SLOT|||morning", text)
        issue_id = freeze_markdown(self.store, text, NOW, edition="morning")
        self.assertEqual(set(json.loads(self.store.issue("2026-11-15", "morning")["sections"])), set(SECTIONS))
        self.assertIsNotNone(issue_id)
        with self.assertRaises(ValueError):
            freeze_markdown(self.store, text, NOW, edition="evening")

    def test_snapshot_date_follows_beijing_after_utc_date_boundary(self):
        when = datetime(2026, 11, 15, 16, 5, tzinfo=timezone.utc)
        text = "DATE|||2026-11-16\nSLOT|||morning\nSECTION|||gaming\n官方|||比赛|||https://example.com/game|||赛事|||事实|||影响"
        freeze_markdown(self.store, text, when)
        self.assertIsNotNone(self.store.issue("2026-11-16", "morning"))

    def test_cli_loads_local_dotenv_without_overriding_process_configuration(self):
        root = Path(__file__).resolve().parents[1]
        dotenv_database = self.store.path.parent / "dotenv.db"
        (self.store.path.parent / ".env").write_text(
            "BRIEF_DB_PATH=" + dotenv_database.as_posix() + "\n", encoding="utf-8"
        )
        env = {**os.environ, "PYTHONPATH": str(root)}
        env.pop("BRIEF_DB_PATH", None)
        result = subprocess.run([sys.executable, "-m", "subscription.cli", "init"],
                                cwd=self.store.path.parent, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(dotenv_database.exists())
        overridden_database = self.store.path.parent / "overridden.db"
        env["BRIEF_DB_PATH"] = str(overridden_database)
        result = subprocess.run([sys.executable, "-m", "subscription.cli", "init"],
                                cwd=self.store.path.parent, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(overridden_database.exists())

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
            freeze_markdown(self.store, "DATE|||2026-11-15\nSECTION|||unknown\n无效|||内容", NOW)

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
        from subscription.store import utcnow, BEIJING
        self.subscriber()
        now = utcnow()
        self.store.freeze_issue(now.astimezone(BEIJING).date().isoformat(), SECTIONS, now)
        env = {**os.environ, "BRIEF_DB_PATH": str(self.store.path), "BRIEF_MODE": "subscription", "PYTHONUTF8": "1"}
        result = subprocess.run([sys.executable, "run_all.py"], cwd=Path(__file__).resolve().parents[1],
                                env=env, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.store.connect() as conn:
            rows = conn.execute("SELECT status,task_type FROM delivery_log").fetchall()
        expected = [("pending", "daily_brief")] if now.astimezone(BEIJING).hour >= 8 else []
        self.assertEqual([(row["status"], row["task_type"]) for row in rows], expected)
