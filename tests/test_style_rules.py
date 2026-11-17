import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

import style_rules  # noqa: E402

TOPICS = [f"t{i}" for i in range(16)]


class StyleRulesTest(unittest.TestCase):
    def test_about_seventy_percent_of_topics_are_everyday_and_stable(self):
        first = style_rules.lens_for_topics(TOPICS, "2026-11-17", "morning")
        self.assertEqual(list(first.values()).count("everyday"), 11)
        self.assertEqual(list(first.values()).count("frontier"), 5)
        self.assertEqual(first, style_rules.lens_for_topics(TOPICS, "2026-11-17", "morning"))
        self.assertNotEqual(first, style_rules.lens_for_topics(TOPICS, "2026-11-17", "evening"))

    def test_frontier_topics_rotate_across_days(self):
        seen = set()
        for day in range(1, 29):
            lens = style_rules.lens_for_topics(TOPICS, f"2026-11-{day:02d}", "morning")
            seen.update(key for key, value in lens.items() if value == "frontier")
        self.assertEqual(seen, set(TOPICS))

    def test_quota_rounds_to_seventy_percent_and_never_zero(self):
        self.assertEqual([style_rules.everyday_quota(n) for n in (1, 2, 3, 5, 10)], [1, 1, 2, 4, 7])

    def test_rules_cover_audience_plain_language_and_chinese_names(self):
        everyday = style_rules.reader_rules(1, "everyday")
        self.assertIn("普通人", everyday)
        self.assertIn("通俗要求", everyday)
        self.assertIn("译成简体中文", everyday)
        self.assertIn("专业前沿", style_rules.reader_rules(1, "frontier"))
        self.assertIn("至少 4 条", style_rules.reader_rules(5))

    def test_source_labels_use_chinese_names_and_keep_suffix(self):
        self.assertEqual(style_rules.zh_source("The Guardian商业"), "卫报商业")
        self.assertEqual(style_rules.zh_source("BBC商业"), "英国广播公司商业")
        self.assertEqual(style_rules.zh_source("中新网财经"), "中新网财经")

    def test_only_cards_with_english_names_are_sent_for_cleanup(self):
        calls = []

        def fake(prompt):
            calls.append(prompt)
            return "卫报|||马克龙访问柏林|||https://example.com/a|||外交|||法国总统马克龙访问德国。|||两国关系升温。"

        text = ("卫报|||Macron visits Berlin|||https://example.com/a|||外交|||法国总统马克龙访问德国。|||两国关系升温。\n"
                "央视|||人工智能进入课堂|||https://example.com/b|||教育|||学校开始使用 AI 辅助教学，成本 5G 网络。|||家长更省心。")
        result = style_rules.localize_cards(text, fake)
        self.assertEqual(len(calls), 1)
        self.assertIn("马克龙访问柏林", result)
        self.assertIn("人工智能进入课堂", result)

    def test_bad_cleanup_output_keeps_original_line(self):
        original = "卫报|||Macron visits Berlin|||https://example.com/a|||外交|||摘要|||点评"
        for bad in ("卫报|||马克龙访问柏林|||https://evil.example/a|||外交|||摘要|||点评",
                    "卫报|||马克龙访问柏林|||https://example.com/a|||外交|||摘要",
                    "好的\n马克龙访问柏林"):
            self.assertEqual(style_rules.localize_cards(original, lambda _prompt, bad=bad: bad), original)

        def broken(_prompt):
            raise RuntimeError("接口失败")

        self.assertEqual(style_rules.localize_cards(original, broken), original)


if __name__ == "__main__":
    unittest.main()
