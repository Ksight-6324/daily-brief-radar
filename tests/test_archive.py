from unittest.mock import patch

from fastapi.testclient import TestClient

from subscription.delivery import Delivery
from tests.support import NOW, StoreCase
from web.app import create_app


def card(title, summary="摘要", source="来源", url="https://example.com/a", importance="影响"):
    return f"{source}|||{title}|||{url}|||标签|||{summary}|||{importance}"


class ArchiveTest(StoreCase):
    # NOW is 19:00 Beijing time on 2026-11-15: that morning issue is out, the evening issue is still frozen.
    def setUp(self):
        super().setUp()
        self.store.freeze_issue("2026-11-14", {"ai": card("OpenAI 发布新模型"), "markets": card("A股收涨")}, NOW, "morning")
        self.store.freeze_issue("2026-11-14", {"commodities": card("金价创新高", summary="避险需求推高 Gold")}, NOW, "evening")
        self.store.freeze_issue("2026-11-15", {"ai": card("国产大模型开源"), "jobs": card("秋招放缓")}, NOW, "morning")
        self.store.freeze_issue("2026-11-15", {"ai": card("尚未发送的晚报新闻")}, NOW, "evening")
        clock = patch("web.archive.utcnow", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.client = TestClient(create_app(self.store, Delivery(self.store, "https://brief.example.com", "Brief <b@example.com>")))
        self.addCleanup(self.client.close)

    def test_index_lists_sent_issues_newest_first_and_hides_the_unsent_evening_issue(self):
        response = self.client.get("/archive")
        self.assertEqual(response.status_code, 200)
        page = response.text
        self.assertNotIn("尚未发送的晚报新闻", page)
        self.assertNotIn("/archive/2026-11-15/evening", page)
        order = [page.index(path) for path in ("/archive/2026-11-15/morning", "/archive/2026-11-14/evening", "/archive/2026-11-14/morning")]
        self.assertEqual(order, sorted(order))
        self.assertIn("2026年11月15日 周日 · 早报", page)
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertNotIn("<script", page)
        self.assertNotIn("style=", page)

    def test_unsent_issue_is_not_found_until_its_send_time(self):
        self.assertEqual(self.client.get("/archive/2026-11-15/evening").status_code, 404)
        self.assertEqual(self.client.get("/archive/2026-11-16/morning").status_code, 404)
        self.assertEqual(self.client.get("/archive/not-a-date/morning").status_code, 404)
        self.assertEqual(self.client.get("/archive/2026-11-14/legacy").status_code, 404)
        with patch("web.archive.utcnow", return_value=NOW.replace(hour=12)):
            page = self.client.get("/archive/2026-11-15/evening")
        self.assertEqual(page.status_code, 200)
        self.assertIn("尚未发送的晚报新闻", page.text)

    def test_issue_page_shows_every_story_and_links_neighbours(self):
        page = self.client.get("/archive/2026-11-14/evening").text
        self.assertIn("金价创新高", page)
        self.assertIn('href="https://example.com/a"', page)
        self.assertIn('rel="noopener noreferrer"', page)
        self.assertIn('href="/archive/2026-11-15/morning"', page)
        self.assertIn('href="/archive/2026-11-14/morning"', page)

    def test_keyword_search_matches_any_field_case_insensitively_and_highlights(self):
        page = self.client.get("/archive", params={"q": "gold"}).text
        self.assertIn("找到 1 条", page)
        self.assertIn("<mark>Gold</mark>", page)
        self.assertIn('href="/archive/2026-11-14/evening#commodities"', page)
        self.assertNotIn("A股收涨", page)

    def test_every_term_must_match_and_topic_names_are_searchable(self):
        page = self.client.get("/archive", params={"q": "人工智能 开源"}).text
        self.assertIn("找到 1 条", page)
        self.assertIn("国产大模型<mark>开源</mark>", page)
        self.assertNotIn("OpenAI 发布新模型", page)
        self.assertIn("没有找到", self.client.get("/archive", params={"q": "不存在的词"}).text)

    def test_topic_filter_alone_and_combined_with_a_keyword(self):
        page = self.client.get("/archive", params={"topic": "ai"}).text
        self.assertIn("找到 2 条", page)
        self.assertLess(page.index("国产大模型开源"), page.index("OpenAI 发布新模型"))
        self.assertNotIn("尚未发送的晚报新闻", page)
        self.assertIn('<option value="ai" selected>', page)
        self.assertIn("找到 1 条", self.client.get("/archive", params={"topic": "ai", "q": "openai"}).text)
        # An unknown topic is ignored instead of failing.
        self.assertEqual(self.client.get("/archive", params={"topic": "nope"}).status_code, 200)

    def test_search_input_is_escaped_and_bad_page_numbers_are_tolerated(self):
        page = self.client.get("/archive", params={"q": '"><script>alert(1)</script>', "page": "x"})
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("<script>", page.text)
        self.assertEqual(self.client.get("/archive", params={"page": "-5"}).status_code, 200)
        self.assertEqual(self.client.get("/archive", params={"q": "金" * 5000}).status_code, 200)

    def test_story_text_and_unsafe_links_from_snapshots_are_escaped(self):
        self.store.freeze_issue("2026-11-13", {"ai": card("<img src=x onerror=alert(1)>", url="javascript:alert(1)")}, NOW, "morning")
        page = self.client.get("/archive/2026-11-13/morning").text
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)
        self.assertNotIn("javascript:", page)

    def test_new_issues_appear_after_the_cache_was_filled(self):
        self.client.get("/archive")
        self.store.freeze_issue("2026-11-12", {"gaming": card("电竞联赛开幕")}, NOW, "evening")
        self.assertIn("<mark>电竞</mark>联赛开幕", self.client.get("/archive", params={"q": "电竞"}).text)

    def test_issue_list_is_paginated(self):
        for day in range(1, 13):
            self.store.freeze_issue(f"2026-10-{day:02d}", {"ai": card(f"十月第{day}天")}, NOW, "morning")
        first = self.client.get("/archive").text
        self.assertIn('href="/archive?page=2"', first)
        self.assertNotIn("十月第1天", first)
        self.assertIn("十月第1天", self.client.get("/archive", params={"page": 2}).text)

    def test_empty_archive_explains_itself(self):
        with patch("web.archive.utcnow", return_value=NOW.replace(year=2020)):
            self.assertIn("第一期简报发出后", self.client.get("/archive").text)

    def test_email_links_to_the_archive(self):
        from subscription.render import render_issue
        html, text = render_issue("2026-11-15", {"ai": card("标题")}, [], "https://brief.example.com", "token", edition="morning")
        self.assertIn('href="https://brief.example.com/archive"', html)
        self.assertIn("往期简报与搜索 https://brief.example.com/archive", text)
