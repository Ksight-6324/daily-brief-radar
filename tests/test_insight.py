import json
import sqlite3
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

from subscription import insight
from subscription.delivery import Delivery
from tests.support import NOW, StoreCase
from web.app import create_app


def card(title, summary="摘要"):
    return f"来源|||{title}|||https://example.com/a|||标签|||{summary}|||影响"


SECTIONS = {"ai": card("国产芯片取得突破"), "jobs": card("初级程序员岗位减少"), "markets": card("科技股走强"),
            "gaming": card("国产游戏获提名")}
GOOD = "【人工智能】的芯片突破可能让更多工作交给机器，【就业机会】里的初级程序员岗位或许会继续收紧。"


class InsightWritingTest(StoreCase):
    def test_prompt_lists_every_story_and_the_house_rules(self):
        prompts = []
        insight.write_insight(SECTIONS, lambda prompt: prompts.append(prompt) or GOOD, rules="只陈述事实")
        self.assertIn("【人工智能】国产芯片取得突破：摘要", prompts[0])
        self.assertIn("【股票经济】科技股走强", prompts[0])
        self.assertIn("只陈述事实", prompts[0])
        self.assertIn("只输出一个字：无", prompts[0])

    def test_a_valid_sentence_is_kept_with_the_topics_it_links(self):
        self.assertEqual(insight.write_insight(SECTIONS, lambda prompt: "“" + GOOD + "”"), (GOOD, ["ai", "jobs"]))

    def test_answers_that_break_the_rules_are_retried_once_then_dropped(self):
        bad = ["无", "【人工智能】很重要，影响深远，值得长期关注和持续观察。",
               "【人工智能】与【时尚穿搭】可能互相影响，这两个领域都在快速变化当中。",
               "【人工智能】与【就业机会】" + "很长" * 120,
               # Real answer from 2026-11-17: it explains that there is no link instead of answering 无.
               "【人工智能】的新闻引人关注，但与【股票经济】、【就业机会】之间没有直接关联，因此无法建立站得住脚的联系。",
               "【人工智能】进步可能影响【就业机会】，也可能带动【股票经济】，甚至波及【电竞游戏】的制作方式。"]
        for answer in bad:
            calls = []
            self.assertIsNone(insight.write_insight(SECTIONS, lambda prompt: calls.append(1) or answer), answer)
            self.assertEqual(len(calls), 2)
        answers = iter(["无", GOOD])
        self.assertEqual(insight.write_insight(SECTIONS, lambda prompt: next(answers))[1], ["ai", "jobs"])

    def test_issues_with_fewer_than_three_topics_get_no_insight_and_no_model_call(self):
        self.assertIsNone(insight.write_insight({"ai": SECTIONS["ai"], "jobs": SECTIONS["jobs"]}, self.fail))

    def test_insight_is_written_once_and_cannot_be_changed(self):
        issue_id = self.store.freeze_issue("2026-11-15", SECTIONS, NOW, "morning")
        calls = []
        first = insight.ensure_insight(self.store, issue_id, lambda prompt: calls.append(1) or GOOD, rules="", now=NOW)
        self.assertEqual(first["body"], GOOD)
        self.assertEqual(json.loads(first["topics"]), ["ai", "jobs"])
        insight.ensure_insight(self.store, issue_id, lambda prompt: calls.append(1) or "别的话", rules="")
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.store.save_insight(issue_id, "【人工智能】和【股票经济】另一句话", ["ai", "markets"]))
        with self.store.connect() as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE issue_insight SET body='x'")
        with self.store.connect() as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM issue_insight")

    def test_store_rejects_insights_without_two_known_topics_or_issue(self):
        issue_id = self.store.freeze_issue("2026-11-15", SECTIONS, NOW, "morning")
        for body, topics in ((GOOD, ["ai"]), (GOOD, ["ai", "nope"]), ("", ["ai", "jobs"]), ("长" * 301, ["ai", "jobs"])):
            with self.assertRaises(ValueError):
                self.store.save_insight(issue_id, body, topics)
        with self.assertRaises(ValueError):
            self.store.save_insight("missing", GOOD, ["ai", "jobs"])

    def test_publish_queues_mail_even_when_the_insight_model_fails(self):
        from subscription import cli
        self.subscriber()
        issue_id = self.store.freeze_issue("2026-11-15", SECTIONS, NOW, "evening")
        evening = NOW.replace(hour=12)
        with patch.dict("os.environ", {"BRIEF_DB_PATH": str(self.store.path)}), \
                patch.object(sys, "argv", ["cli", "--edition", "evening", "publish"]), \
                patch("subscription.cli.utcnow", return_value=evening), \
                patch("subscription.delivery.utcnow", return_value=evening), \
                patch("subscription.cli.ensure_insight", side_effect=RuntimeError("model down")), \
                patch("builtins.print") as printed:
            cli.main()
        output = " ".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn('"queued": 1', output)
        self.assertIn("Insight skipped: RuntimeError", output)
        self.assertNotIn("model down", output)
        self.assertIsNone(self.store.insight(issue_id))


class InsightPageTest(StoreCase):
    # NOW is 19:00 Beijing time on 2026-11-15: that morning issue is out, the evening issue is still frozen.
    def setUp(self):
        super().setUp()
        older = self.store.freeze_issue("2026-11-14", SECTIONS, NOW, "evening")
        self.store.save_insight(older, "【股票经济】走强与【人工智能】进展可能有关，<script>不会执行</script>。", ["markets", "ai"], NOW)
        morning = self.store.freeze_issue("2026-11-15", SECTIONS, NOW, "morning")
        self.store.save_insight(morning, GOOD, ["ai", "jobs"], NOW)
        evening = self.store.freeze_issue("2026-11-15", SECTIONS, NOW, "evening")
        self.store.save_insight(evening, "【人工智能】与【股票经济】还没发出的晚报洞察，现在不能看到。", ["ai", "markets"], NOW)
        clock = patch("web.archive.utcnow", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.client = TestClient(create_app(self.store, Delivery(self.store, "https://brief.example.com", "B <b@example.com>")))
        self.addCleanup(self.client.close)

    def test_insight_page_leads_with_the_latest_sent_insight_and_its_stories(self):
        response = self.client.get("/insight")
        self.assertEqual(response.status_code, 200)
        page = response.text
        self.assertNotIn("还没发出的晚报洞察", page)
        self.assertIn('<a href="/archive/2026-11-15/morning#ai">【人工智能】</a>', page)
        self.assertIn('<a href="/archive/2026-11-15/morning#jobs">【就业机会】</a>', page)
        self.assertIn("这句话串起的新闻", page)
        self.assertLess(page.index("这句话串起的新闻"), page.index("初级程序员岗位减少"))
        self.assertIn("往期洞察", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>", page)
        self.assertIn('<a href="/insight" aria-current="page">一句话洞察</a>', page)
        self.assertIn("不构成任何投资或职业建议", page)

    def test_issue_page_shows_its_insight_above_the_stories(self):
        page = self.client.get("/archive/2026-11-15/morning").text
        self.assertIn("本期一句话洞察", page)
        self.assertLess(page.index("本期一句话洞察"), page.index('class="story"'))

    def test_new_insight_appears_without_restart_and_empty_state_is_explained(self):
        with patch("web.archive.utcnow", return_value=NOW.replace(year=2020)):
            self.assertIn("第一句洞察会随下一期简报一起发布", self.client.get("/insight").text)
        later = self.store.freeze_issue("2026-11-13", SECTIONS, NOW, "morning")
        self.client.get("/insight")
        self.store.save_insight(later, "【就业机会】与【股票经济】之间可能存在新的联系，值得一起看。", ["jobs", "markets"], NOW)
        self.assertIn("之间可能存在新的联系", self.client.get("/insight").text)

    def test_every_page_menu_links_to_the_insight(self):
        for path in ("/", "/support", "/privacy", "/terms", "/archive"):
            self.assertIn('href="/insight"', self.client.get(path).text, path)
