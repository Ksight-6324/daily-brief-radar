"""
简报的板块布局：13 个板块的顺序、每个板块里每一组内容最多几条。

summarize.py 用它来限制条数、send_email.py 用它来决定邮件里的显示顺序。
想调整顺序，直接挪动 LAYOUT 里的行；想改某组的条数上限，改后面的数字。
每个板块所有组的上限加起来不超过 MAX_PER_SECTION（5）。

LAYOUT 里每个条目：(邮件里的板块标题, [(内容组 key, 该组最多几条), ...])
内容组 key 对应 daily_brief.md 里的 SECTION|||key，由 summarize.py 生成。
"""

MAX_PER_SECTION = 5

LAYOUT = [
    ("📈 股票经济", [("markets", 5)]),
    ("🛢 石油黄金", [("commodities", 5)]),
    ("🏛 政治军事", [("politics_mil", 5)]),
    ("🏺 中外历史", [("history", 3), ("history_news", 2)]),
    ("🎵 音乐演出", [("live_music", 5)]),
    ("🎨 设计美术", [("design_art", 4), ("style", 1)]),
    ("💿 专辑影视", [("album_film", 5)]),
    ("🤖 人工智能", [("ai_tech", 2), ("ai_feeds", 3)]),
    ("🌱 自我提升", [("growth", 2), ("youtube", 3)]),
    ("⚛️ 物理化学", [("science_pc", 5)]),
    ("🧬 生物科技", [("biotech", 5)]),
    ("🧘 心灵疗养", [("mind", 4), ("poem", 1)]),
    ("💼 就业机会", [("jobs", 5)]),
]

# 所有内容组的 key，按邮件里出现的顺序
CONTENT_KEYS = [key for _, groups in LAYOUT for key, _ in groups]

# 不是"新闻"的内容组：总览和行动建议不把它们当作新闻参考
NON_NEWS_KEYS = ("youtube", "poem", "style", "history")


def _is_card(line):
    return "|||" in line


def apply_caps(sections):
    """把每个内容组裁到上限条数（超出的丢掉后面的），并保证每个板块总数不超过 MAX_PER_SECTION。
    原地修改 sections 字典，返回被裁掉的总条数。"""
    trimmed = 0
    for _, groups in LAYOUT:
        budget = MAX_PER_SECTION
        for key, cap in groups:
            lines = (sections.get(key) or "").split("\n")
            kept, n = [], 0
            allowed = min(cap, budget)
            for line in lines:
                if _is_card(line):
                    if n >= allowed:
                        trimmed += 1
                        continue
                    n += 1
                kept.append(line)
            sections[key] = "\n".join(kept)
            budget -= n
    return trimmed
