"""
抓取 13 个板块的新闻候选（股票经济 / 石油黄金 / 政治军事 / 历史 / 音乐演出 / 设计美术 / 专辑影视 /
人工智能 / 自我提升 / 物理化学 / 生物科技 / 心灵疗养 / 就业机会）

结构：大类（板块）-> { 子类名: { 来源名: RSS地址 } }
- 每条新闻会带上 "source"（来源名），邮件里显示成来源徽标
- 每个子类在 core/summarize.py 的 SECTION_SPECS 里有对应的"选几条 + 筛选规则"
- 某个源失效时只会打印警告并跳过，不会影响其他源
- 想增删源，直接改下面的 CATEGORY_SOURCES；板块顺序和每板块条数上限在 core/layout.py

运行前需要先装：pip install feedparser requests
"""
import calendar
import json
import re
import time
from datetime import datetime
from urllib.parse import quote

import feedparser
import requests


# Google News 搜索型 RSS：没有官方 RSS 的主题用它兜底（在 GitHub 服务器上可能被拒绝，被拒只会跳过）
def _gnews(query, lang="zh-CN", gl="CN", ceid="CN:zh-Hans"):
    return f"https://news.google.com/rss/search?q={quote(query)}&hl={lang}&gl={gl}&ceid={ceid}"


CATEGORY_SOURCES = {
    "股票经济": {
        "美股": {
            "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
            "CNBC Investing": "https://www.cnbc.com/id/15839069/device/rss/rss.html",
            "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
            "Seeking Alpha": "https://seekingalpha.com/market_currents.xml",
        },
        "财报": {
            "CNBC Earnings": "https://www.cnbc.com/id/15839135/device/rss/rss.html",
            "Nasdaq Earnings": "https://www.nasdaq.com/feed/rssoutbound?category=Earnings",
        },
        "港股": {
            "SCMP Business": "https://www.scmp.com/rss/92/feed",
            "RTHK财经": "https://rthk.hk/rthk/news/rss/c_expressnews_cfinance.xml",
        },
        "宏观经济": {
            "中新网财经": "https://www.chinanews.com.cn/rss/finance.xml",
            "BBC商业": "http://feeds.bbci.co.uk/news/business/rss.xml",
            "The Guardian商业": "https://www.theguardian.com/business/rss",
            "纽约时报商业": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        },
    },
    "石油黄金": {
        "石油能源": {
            "OilPrice": "https://oilprice.com/rss/main",
            "CNBC能源": "https://www.cnbc.com/id/19836768/device/rss/rss.html",
            "Rigzone": "https://www.rigzone.com/news/rss/rigzone_latest.aspx",
        },
        "黄金贵金属": {
            "FXStreet": "https://www.fxstreet.com/rss/news",
            "Mining.com": "https://www.mining.com/feed/",
            "Investing.com大宗商品": "https://www.investing.com/rss/commodities.rss",
        },
    },
    "政治军事": {
        "国际政治": {
            "BBC": "http://feeds.bbci.co.uk/news/world/rss.xml",
            "The Guardian": "https://www.theguardian.com/world/rss",
            "纽约时报": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
            "半岛电视台": "https://www.aljazeera.com/xml/rss/all.xml",
            "德国之声": "https://rss.dw.com/rdf/rss-en-all",
            "Politico Europe": "https://www.politico.eu/feed/",
            "ANSA": "https://www.ansa.it/english/news/english_nr_rss.xml",
            "中新网国际": "https://www.chinanews.com.cn/rss/world.xml",
        },
        "国内政策": {
            "中新网时政": "https://www.chinanews.com.cn/rss/china.xml",
            "新华网": "http://www.news.cn/politics/news_politics.xml",
            "中新网要闻": "https://www.chinanews.com.cn/rss/scroll-news.xml",
        },
        "军事": {
            "Defense One": "https://www.defenseone.com/rss/all/",
            "Breaking Defense": "https://breakingdefense.com/feed/",
            "Defense News": "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml",
            "The War Zone": "https://www.twz.com/feed",
            "War on the Rocks": "https://warontherocks.com/feed/",
        },
    },
    "历史": {
        "历史新闻": {
            "Smithsonian": "https://www.smithsonianmag.com/rss/history/",
            "History Extra": "https://www.historyextra.com/feed/",
            "Archaeology": "https://www.archaeology.org/rss",
        },
    },
    "音乐演出": {
        "音乐与演出": {
            "Billboard": "https://www.billboard.com/feed/",
            "Variety音乐": "https://variety.com/v/music/feed/",
            "Rolling Stone": "https://www.rollingstone.com/music/feed/",
            "Pitchfork新闻": "https://pitchfork.com/feed/feed-news/rss",
            "Stereogum": "https://www.stereogum.com/feed/",
            "Consequence": "https://consequence.net/feed/",
            "NME": "https://www.nme.com/feed",
            "The Quietus": "https://thequietus.com/feed",
        },
    },
    "设计美术": {
        "设计": {
            "Dezeen": "https://www.dezeen.com/feed/",
            "Designboom": "https://www.designboom.com/feed/",
            "Wallpaper": "https://www.wallpaper.com/rss",
            "ArchDaily": "https://www.archdaily.com/feed",
            "Core77": "https://feeds.feedburner.com/core77/blog",
        },
        "美术": {
            "Artnet": "https://news.artnet.com/feed",
            "Hyperallergic": "https://hyperallergic.com/feed/",
            "ARTnews": "https://www.artnews.com/feed/",
            "Artforum": "https://www.artforum.com/feed/",
            "Colossal": "https://www.thisiscolossal.com/feed/",
        },
    },
    "专辑影视": {
        "新专辑": {
            "Pitchfork专辑评论": "https://pitchfork.com/feed/feed-album-reviews/rss",
            "Stereogum": "https://www.stereogum.com/feed/",
            "Consequence": "https://consequence.net/feed/",
            "NME": "https://www.nme.com/feed",
            "Rolling Stone": "https://www.rollingstone.com/music/feed/",
        },
        "影视": {
            "Variety": "https://variety.com/feed/",
            "Deadline": "https://deadline.com/feed/",
            "IndieWire": "https://www.indiewire.com/feed/",
            "The Film Stage": "https://thefilmstage.com/feed/",
            "Little White Lies": "https://lwlies.com/feed/",
            "SlashFilm": "https://www.slashfilm.com/feed/",
        },
    },
    "人工智能": {
        "AI公司动态": {
            "OpenAI": "https://openai.com/news/rss.xml",
            "Google AI": "https://blog.google/technology/ai/rss/",
            "DeepMind": "https://deepmind.google/blog/rss.xml",
            "NVIDIA": "https://blogs.nvidia.com/feed/",
            "量子位": "https://www.qbitai.com/feed",
            "AI公司新闻": _gnews("Anthropic OR OpenAI OR DeepSeek OR Gemini OR Mistral OR xAI",
                                  lang="en-US", gl="US", ceid="US:en"),
        },
    },
    "自我提升": {
        "自我提升文章": {
            "Big Think": "https://bigthink.com/feed/",
            "Farnam Street": "https://fs.blog/feed/",
            "Zen Habits": "https://zenhabits.net/feed/",
            "Scott Young": "https://www.scotthyoung.com/blog/feed/",
            "Cal Newport": "https://calnewport.com/feed/",
            "Lifehacker": "https://lifehacker.com/feed/rss",
        },
    },
    "物理化学": {
        "物理": {
            "Phys.org物理": "https://phys.org/rss-feed/physics-news/",
            "ScienceDaily物理": "https://www.sciencedaily.com/rss/matter_energy/physics.xml",
            "Physics World": "https://physicsworld.com/feed/",
            "Quanta": "https://api.quantamagazine.org/feed/",
        },
        "化学": {
            "Phys.org化学": "https://phys.org/rss-feed/chemistry-news/",
            "ScienceDaily化学": "https://www.sciencedaily.com/rss/matter_energy/chemistry.xml",
        },
    },
    "生物科技": {
        "生物科技": {
            "STAT": "https://www.statnews.com/feed/",
            "Endpoints": "https://endpts.com/feed/",
            "BioPharma Dive": "https://www.biopharmadive.com/feeds/news/",
            "GEN": "https://www.genengnews.com/feed/",
            "Phys.org生物技术": "https://phys.org/rss-feed/biology-news/biotechnology/",
            "Nature Biotechnology": "https://www.nature.com/nbt.rss",
        },
    },
    "心灵疗养": {
        "心灵疗养文章": {
            "Greater Good": "https://greatergood.berkeley.edu/rss",
            "Mindful": "https://www.mindful.org/feed/",
            "Psyche": "https://psyche.co/feed",
            "Tricycle": "https://tricycle.org/feed/",
            "ScienceDaily心理": "https://www.sciencedaily.com/rss/mind_brain/psychology.xml",
        },
    },
    "就业机会": {
        "大公司招聘动态": {
            # Google News 搜索源在 GitHub 服务器上曾被拒绝（503），已改用浏览器请求头再试；
            # 仍失败时只会跳过，由前面几个稳定来源兜底
            "TechCrunch裁员专题": "https://techcrunch.com/tag/layoffs/feed/",
            "钛媒体": "https://www.tmtpost.com/rss.xml",
            "IT之家": "https://www.ithome.com/rss/",
            "校招社招新闻": _gnews("校招 OR 社招 OR 招聘 腾讯 OR 字节跳动 OR 阿里巴巴 OR 华为 OR 美团 OR 小米"),
            "海外大厂招聘": _gnews("hiring OR layoffs OR \"graduate program\" Google OR Apple OR Microsoft OR Nvidia OR Ubisoft OR Epic",
                                    lang="en-US", gl="US", ceid="US:en"),
        },
    },
}

# 时效过滤：只保留最近多少小时内发布的新闻（简报的定位是"这半天内值得关注的事"）。
# 更新慢的板块放宽时间窗；已推送过的内容由 seen_items.json 排除，不会重复。没有发布时间的条目保留。
DEFAULT_MAX_AGE_HOURS = 48
MAX_AGE_HOURS = {
    "石油黄金": 72, "政治军事": 72, "历史": 240, "音乐演出": 72, "设计美术": 72, "专辑影视": 72,
    "自我提升": 504, "物理化学": 96, "生物科技": 72, "心灵疗养": 168, "就业机会": 168,
}

REQUEST_TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; daily-brief-radar/1.0)"}
# Google News 对"像机器人"的请求头更严格，用普通浏览器的请求头
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text, limit=220):
    """去掉HTML标签和多余空白，截断到 limit 字"""
    text = _TAG_RE.sub(" ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _parse(url):
    """用 requests 拉取（带超时，避免某个源卡死整条流水线），再交给 feedparser 解析"""
    headers = BROWSER_HEADERS if "news.google.com" in url else HEADERS
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def fetch_one(category, subcategory, url, source_name=None, max_items=10):
    """抓取单个RSS源。max_items留得比展示条数多一些，方便后面AI筛选时有得挑"""
    label = f"{category}-{subcategory}" + (f"/{source_name}" if source_name else "")
    try:
        feed = _parse(url)
    except Exception as e:
        print(f"⚠️ 【{label}】抓取失败，跳过（{e}）")
        return []

    if feed.bozo and not feed.entries:
        print(f"⚠️ 【{label}】抓取可能有问题，跳过（{url}）")
        return []

    max_age = MAX_AGE_HOURS.get(category, DEFAULT_MAX_AGE_HOURS) * 3600
    now_ts = time.time()

    results = []
    for entry in feed.entries:
        if len(results) >= max_items:
            break
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if parsed and now_ts - calendar.timegm(parsed) > max_age:
            continue  # 太旧了
        item = {
            "category": category,
            "subcategory": subcategory,
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "published": entry.get("published", entry.get("updated", "")),
        }
        if source_name:
            item["source"] = source_name
        summary = _clean(entry.get("summary", ""))
        if summary and summary != item["title"]:
            item["summary"] = summary
        results.append(item)
    return results


if __name__ == "__main__":
    all_items = []

    for category, subs in CATEGORY_SOURCES.items():
        for subcategory, src in subs.items():
            print(f"正在抓取【{category}-{subcategory}】...")
            items = []
            for source_name, url in src.items():
                # 多个源：每个源少取几条，合起来给AI挑
                got = fetch_one(category, subcategory, url, source_name, max_items=5)
                print(f"  · {source_name}: {len(got)} 条")
                items.extend(got)
            print(f"  → 共 {len(items)} 条")
            all_items.extend(items)

    print(f"\n总共抓取到 {len(all_items)} 条")

    output = {
        "fetched_at": datetime.now().isoformat(),
        "source": "category_news",
        "items": all_items,
    }
    with open("category_news.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"已保存到 category_news.json（共 {len(all_items)} 条）")
