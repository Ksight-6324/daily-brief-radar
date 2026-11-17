"""Bridge the existing fetchers and summarizer into immutable subscription issues."""

import sys
from pathlib import Path

from .store import BEIJING, EDITIONS, SECTIONS, edition_for, utcnow


def parse_markdown(content, now=None, edition=None):
    now = now or utcnow()
    sections = {}
    current = None
    issue_date = None
    slot = None
    for line in content.splitlines():
        if line.startswith("DATE|||"):
            issue_date = line.split("|||", 1)[1].strip()
        elif line.startswith("SLOT|||"):
            slot = line.split("|||", 1)[1].strip()
        elif line.startswith("SECTION|||"):
            current = line.split("|||", 1)[1].strip()
        elif current:
            sections.setdefault(current, []).append(line)
    if issue_date != now.astimezone(BEIJING).date().isoformat():
        raise ValueError("简报日期与北京时间当天日期不符")
    if slot in EDITIONS and edition and slot != edition:
        raise ValueError("内容版次与发布版次不符")
    edition = edition or (slot if slot in EDITIONS else edition_for(now))
    snapshot = {}
    aliases = {"ai": ["ai", "ai_feeds", "ai_tech"], "history": ["history_news", "history"]}
    for key in SECTIONS:
        candidates = aliases.get(key, [key])
        cards = [line.strip() for candidate in candidates for line in sections.get(candidate, []) if "|||" in line]
        if cards:
            snapshot[key] = cards[0]
    if edition not in EDITIONS:
        raise ValueError("请选择早报或晚报版次")
    if not snapshot:
        raise ValueError("本期没有有效内容，停止发布")
    return issue_date, edition, snapshot


def freeze_markdown(store, content, now=None, edition=None):
    issue_date, edition, snapshot = parse_markdown(content, now, edition)
    return store.freeze_issue(issue_date, snapshot, now, edition=edition)


def generate_markdown(now=None, edition=None):
    """Generate one shared story per available topic for the requested edition."""
    now = now or utcnow()
    edition = edition or edition_for(now)
    if edition not in EDITIONS:
        raise ValueError("请选择早报或晚报版次")
    core_path = str(Path(__file__).resolve().parents[1] / "core")
    if core_path not in sys.path:
        sys.path.insert(0, core_path)
    import style_rules
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
    issue_date = f"{now.astimezone(BEIJING):%Y-%m-%d}"
    # About 70% of the topics lean towards ordinary readers' daily lives; the rest may carry specialist frontier news.
    lenses = style_rules.lens_for_topics(SECTIONS, issue_date, edition)
    for key, label in SECTIONS.items():
        category = "历史" if key == "history" else label
        candidates = [item for item in items if item.get("category") == category]
        if not candidates:
            continue
        rule = "只选一条有实质影响的消息。摘要两至三句，点评写为什么重要。整条控制在 250 字以内。" + summarize.NO_ADVICE_RULE
        if key == "jobs":
            rule += "只陈述招聘、用工、薪资动态。招聘新闻须明确标注，禁止编造岗位与投递链接。"
        if key == "sports_health":
            rule += "只陈述赛事、运动和健康研究的事实，不提供个体诊断、用药或治疗建议。"
        if key == "growth":
            rule += ("优先选择自律与自我约束相关的内容：习惯养成、专注力、克服拖延、自控力与行动力，"
                     "要有研究或实践依据。点评写出一个今天就能开始的具体做法。鸡汤、空洞励志、广告软文、购物清单不要选。"
                     "语气要温和平和：优先选博客、文章和演讲口播里温和、理性、讲方法的内容；"
                     "打鸡血、贩卖焦虑、说教训斥、鼓吹逆袭暴富的内容一律不要选。")
        if key == "gaming":
            rule += ("覆盖全球游戏与电竞：新作发售与定档、重要版本更新、主机与平台动态，以及世界各地的电竞赛事结果与赛程。"
                     "优先影响面大的作品和顶级赛事，写清楚游戏名、平台或赛事名、战队与结果。传闻须标明未经证实。")
        if key == "fashion":
            rule += ("覆盖全球时尚动态：时装周与品牌发布、流行趋势、不同的潮流风格。摘要说清这是什么风格或趋势，"
                     "点评写出可参考的穿搭与色彩搭配思路（主色、辅色或点缀色）。促销折扣、购物清单、明星八卦不要选。")
        result = summarize.summarize_one_pick(category, candidates, 1, rule, seen_titles, lens=lenses[key])
        if result.strip():
            sections[key] = result
    if not sections:
        raise ValueError("本期没有有效内容，停止发布")
    text = f"DATE|||{issue_date}\nSLOT|||{edition}\n"
    text += "".join(f"SECTION|||{key}\n{content}\n" for key, content in sections.items())
    Path("daily_brief.md").write_text(text, encoding="utf-8")
    # Content-selection history is independent of per-subscriber provider acceptance.
    summarize.save_seen(records, sections, now)
    return text
