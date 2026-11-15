"""Bridge the existing fetchers and summarizer into immutable subscription issues."""

import sys
from pathlib import Path

from .store import ROME, utcnow


def freeze_markdown(store, content, now=None):
    now = now or utcnow()
    sections = {}
    current = None
    issue_date = None
    for line in content.splitlines():
        if line.startswith("DATE|||"):
            issue_date = line.split("|||", 1)[1].strip()
        elif line.startswith("SECTION|||"):
            current = line.split("|||", 1)[1].strip()
        elif current:
            sections.setdefault(current, []).append(line)
    if issue_date != now.astimezone(ROME).date().isoformat():
        raise ValueError("简报日期与罗马当天日期不符")
    snapshot = {}
    for key, candidates in {"markets": ["markets"], "ai": ["ai_feeds", "ai_tech"], "jobs": ["jobs"]}.items():
        cards = [line.strip() for candidate in candidates for line in sections.get(candidate, []) if "|||" in line]
        if not cards:
            raise ValueError(f"板块 {key} 缺少内容，停止发布")
        snapshot[key] = cards[0]
    return store.freeze_issue(issue_date, snapshot, now)


def generate_markdown(now=None):
    """Generate one shared story per MVP section using the existing summarizer."""
    now = now or utcnow()
    core_path = str(Path(__file__).resolve().parents[1] / "core")
    sys.path.insert(0, core_path)
    import summarize

    # These settings are local to this generator process and never come from a reader profile.
    summarize.USER_NAME = "读者"
    summarize.USER_PROFILE_PRIORITY = "为中文通用读者筛选有实质影响的新闻，只依据提供的候选，不做个人画像推断。"
    category_data = summarize.load_json("category_news.json")
    if not category_data:
        raise ValueError("缺少抓取结果 category_news.json")
    seen_urls, seen_titles, records = summarize.load_seen(now)
    items = summarize.filter_seen(category_data["items"], seen_urls)
    sections = {}
    for key, category in (("markets", "股票经济"), ("ai_feeds", "人工智能"), ("jobs", "就业机会")):
        candidates = [item for item in items if item.get("category") == category]
        if not candidates:
            raise ValueError(f"{category}没有有效候选，停止发布")
        rule = "只选一条有实质影响的消息。摘要两至三句，点评写为什么重要。整条控制在 250 字以内。" + summarize.NO_ADVICE_RULE
        if key == "jobs":
            rule += "只陈述招聘、用工、薪资动态。招聘新闻须明确标注，禁止编造岗位与投递链接。"
        sections[key] = summarize.summarize_one_pick(category, candidates, 1, rule, seen_titles)
    text = f"DATE|||{now.astimezone(ROME):%Y-%m-%d}\nSLOT|||noon\n"
    text += "".join(f"SECTION|||{key}\n{content}\n" for key, content in sections.items())
    Path("daily_brief.md").write_text(text, encoding="utf-8")
    # Content-selection history is independent of per-subscriber provider acceptance.
    summarize.save_seen(records, sections, now)
    return text
