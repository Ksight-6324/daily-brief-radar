"""Reader-facing writing rules shared by the summarizer and the subscription pipeline.

Three things live here:
1. Audience mix: about 70% of the stories should matter to ordinary people, the rest may be specialist frontier news.
2. Plain-language and Chinese-name instructions for the model prompts.
3. A clean-up pass that asks the model to fix any card line that still contains English names.
"""

import hashlib
import re

EVERYDAY_SHARE = 0.7

PLAIN_LANGUAGE_RULE = (
    "通俗要求：读者是没有专业背景的普通人。专业术语一律换成日常说法，不得不用的术语，紧接着用一句话说明它是什么。"
    "物理、化学、生物、医药、金融、军事这类专业内容，先说清“这对普通人的生活意味着什么”，再说发生了什么，"
    "不要堆砌数据、公式和缩写。可以举生活中的具体例子帮助理解，但仍然不使用比喻和拟人。")

CHINESE_NAMES_RULE = (
    "译名要求：标题、摘要、点评里所有外国的人名、地名、国家和城市名、机构和公司名、书名、影视和专辑名、赛事和奖项名，"
    "一律译成简体中文，使用通行译名，没有通行译名的按读音音译；中文后面不要再括号附英文原文。"
    "只有日常里本来就这样说的缩写和型号可以保留字母，例如 AI、GDP、CPU、5G、iPhone、ChatGPT。")

# Everyday abbreviations and product names that are left untouched by the clean-up pass.
KEEP_LATIN = {
    "ai", "gdp", "cpu", "gpu", "usb", "pc", "app", "5g", "4g", "gpt", "chatgpt", "iphone", "ipad", "ios",
    "android", "wifi", "led", "oled", "dna", "rna", "ct", "vr", "ar", "ipo", "etf", "ceo", "pdf", "ssd",
    "mrna", "covid", "covid-19", "github", "youtube", "tiktok", "ps5", "xbox",
}

# Chinese names for the publications that have an established one. Others, mostly initialisms, stay as they are.
SOURCE_ZH = {
    "The Guardian": "卫报", "BBC": "英国广播公司", "Politico Europe": "欧洲政治新闻网", "SCMP Business": "南华早报商业",
    "RTHK": "香港电台", "MarketWatch": "市场观察", "Seeking Alpha": "寻找阿尔法", "OilPrice": "油价网",
    "Mining.com": "矿业网", "Smithsonian": "史密森尼学会", "Nature Biotechnology": "自然·生物技术",
    "Physics World": "物理世界", "Quanta": "量子杂志", "Psychology Today": "今日心理学",
    "Rolling Stone": "滚石", "Variety": "综艺", "Billboard": "公告牌", "Harper's Bazaar": "时尚芭莎",
    "Elle": "世界时装之苑", "NVIDIA": "英伟达", "Google AI": "谷歌人工智能", "NHS England": "英国国家医疗服务体系",
    "Defense News": "防务新闻", "Defense One": "防务一号", "Breaking Defense": "突破防务", "History Extra": "历史特刊",
    "Zen Habits": "禅习惯", "James Clear": "詹姆斯·克利尔", "Scott Young": "斯科特·扬", "Farnam Street": "法纳姆街",
    "Cal Newport": "卡尔·纽波特", "Nir and Far": "尼尔·埃亚尔", "Hacker News": "黑客新闻", "ANSA": "安莎通讯社",
    "ARTnews": "艺术新闻", "Artforum": "艺术论坛", "Archaeology": "考古学杂志",
}


def zh_source(name):
    """Chinese label for a source, keeping any Chinese suffix such as 商业 or 能源."""
    for key in sorted(SOURCE_ZH, key=len, reverse=True):
        if name.startswith(key):
            return SOURCE_ZH[key] + name[len(key):].strip()
    return name


def everyday_quota(top_n):
    """How many of top_n stories must be about ordinary people's lives (70%, at least one)."""
    return max(1, int(top_n * EVERYDAY_SHARE + 0.5)) if top_n > 0 else 0


def lens_for_topics(keys, issue_date, edition):
    """Give each topic of one issue the 'everyday' or 'frontier' lens, about 70% everyday.

    The choice is a deterministic shuffle by date and edition, so a retry rebuilds the same issue
    and a reader who follows one topic sees frontier news on roughly three days in ten.
    """
    keys = list(keys)
    frontier_count = len(keys) - int(len(keys) * EVERYDAY_SHARE + 0.5)
    ranked = sorted(keys, key=lambda key: hashlib.sha256(f"{issue_date}/{edition}/{key}".encode()).hexdigest())
    frontier = set(ranked[:frontier_count])
    return {key: ("frontier" if key in frontier else "everyday") for key in keys}


def audience_rule(top_n, lens=None):
    if lens == "everyday":
        return ("选稿取向（贴近普通人）：这一条优先选与普通人的钱包、工作、健康、出行、消费、日常生活直接相关的新闻；"
                "候选里实在没有，再选其他有实质内容的。")
    if lens == "frontier":
        return ("选稿取向（专业前沿）：这一条可以选专业性较强的前沿进展，但必须按下面的通俗要求，"
                "用没有专业背景的人也能听懂的话讲，并写明它将来可能怎样影响普通人。")
    everyday = everyday_quota(top_n)
    return (f"选稿比例：共选 {top_n} 条时，至少 {everyday} 条要与普通人的钱包、工作、健康、出行、消费、日常生活直接相关；"
            f"其余最多 {top_n - everyday} 条可以选专业性较强的前沿进展。")


def reader_rules(top_n, lens=None):
    return "\n".join((audience_rule(top_n, lens), PLAIN_LANGUAGE_RULE, CHINESE_NAMES_RULE))


_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’.\-]*[A-Za-z0-9]|[A-Za-z]{2}")


def _text_fields(fields):
    """Indexes of the fields a reader sees as prose: not the link, not the source label."""
    url = next((i for i, value in enumerate(fields) if value.startswith("http")), None)
    if url is None:
        return []
    return [i for i in range(len(fields)) if i != url and not (url >= 2 and i < url - 1)]


def needs_localizing(line):
    fields = [part.strip() for part in line.split("|||")]
    for index in _text_fields(fields):
        for word in _LATIN_WORD.findall(fields[index]):
            if word.lower().strip(".-") not in KEEP_LATIN and len(word) >= 2:
                return True
    return False


def localize_line(line, call_ai):
    """Ask the model to translate leftover English names in one card line. Keep the original on any doubt."""
    before = [part.strip() for part in line.split("|||")]
    prompt = ("下面是一行简报卡片，用 ||| 分隔各段。请把其中所有英文的人名、地名、国家和城市名、机构和公司名、书名、"
              "影视和专辑名、赛事和奖项名翻译成简体中文通行译名，没有通行译名的按读音音译，不要在中文后面再括号附英文。"
              "只有 AI、GDP、CPU、5G、iPhone、ChatGPT 这类日常就这样说的缩写和型号可以保留。\n"
              "严格保持：分段数量和 ||| 位置不变，链接原样不动，不增删内容，不加说明。只输出改好的这一行。\n\n" + line)
    try:
        fixed = (call_ai(prompt) or "").strip().splitlines()
    except Exception:
        return line
    fixed = fixed[0].strip() if len(fixed) == 1 else ""
    after = [part.strip() for part in fixed.split("|||")]
    if len(after) != len(before) or any(a != b for a, b in zip(after, before) if b.startswith("http")):
        return line
    return fixed


def localize_cards(text, call_ai):
    """Run the clean-up on every card line of a section text that still contains English names."""
    if not text:
        return text
    return "\n".join(localize_line(line, call_ai) if "|||" in line and needs_localizing(line) else line
                     for line in text.split("\n"))
