"""
抓取各类别新闻（经济 / 政治 / 科学 + 欧洲意大利 / 设计 / 游戏 / 股市 / 国内政策 / 招聘 / 时尚 / AI公司）

结构：大类 -> { 子类名: RSS地址 或 {来源名: RSS地址} }
- 值是字符串：该子类只有一个源（旧写法，保持兼容）
- 值是字典：该子类有多个源，每条新闻会带上 "source"（来源名），邮件里显示成来源徽标

某个源失效时只会打印警告并跳过，不会影响其他源。
想增删源，直接改下面的 CATEGORY_SOURCES 即可。

运行前需要先装：pip install feedparser requests
"""
import json
import re
from datetime import datetime

import feedparser
import requests

# Google News 搜索型 RSS：没有官方 RSS 的主题（招聘动态、AI 公司新闻）用它兜底
def _gnews(query, lang="zh-CN", gl="CN", ceid="CN:zh-Hans"):
    from urllib.parse import quote
    return f"https://news.google.com/rss/search?q={quote(query)}&hl={lang}&gl={gl}&ceid={ceid}"


CATEGORY_SOURCES = {
    # ===== 原有类别（保持不变） =====
    "经济": {
        "中国经济": "https://www.chinanews.com.cn/rss/finance.xml",
        "世界经济": "http://feeds.bbci.co.uk/news/business/rss.xml",
    },
    "政治": {
        "国内时政": "https://www.chinanews.com.cn/rss/china.xml",
        "国际时政": "http://feeds.bbci.co.uk/news/world/rss.xml",
    },
    "科学突破": {
        "科学突破": "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    },

    # ===== 新增类别 =====
    "欧洲与意大利": {
        "欧盟": {
            "Politico Europe": "https://www.politico.eu/feed/",
            "BBC Europe": "http://feeds.bbci.co.uk/news/world/europe/rss.xml",
        },
        "意大利": {
            "ANSA": "https://www.ansa.it/english/news/english_nr_rss.xml",
            "la Repubblica": "https://www.repubblica.it/rss/homepage/rss2.0.xml",
        },
    },
    "设计": {
        "设计": {
            "Dezeen": "https://www.dezeen.com/feed/",
            "Designboom": "https://www.designboom.com/feed/",
            "Wallpaper": "https://www.wallpaper.com/rss",
            "ArchDaily": "https://www.archdaily.com/feed",
            "Core77": "https://feeds.feedburner.com/core77/blog",
        },
    },
    "游戏": {
        "3A游戏资讯": {
            "Steam": "https://store.steampowered.com/feeds/news/",
            "IGN": "https://feeds.feedburner.com/ign/games-all",
            "GameSpot": "https://www.gamespot.com/feeds/mashup/",
            "PC Gamer": "https://www.pcgamer.com/rss/",
            "Gematsu": "https://www.gematsu.com/feed",
            "VGC": "https://www.videogameschronicle.com/feed/",
            "Eurogamer": "https://www.eurogamer.net/feed",
            "Polygon": "https://www.polygon.com/rss/index.xml",
        },
        "游戏公司": {
            "GamesIndustry.biz": "https://www.gamesindustry.biz/feed",
            "Game Developer": "https://www.gamedeveloper.com/rss.xml",
        },
    },
    "股市": {
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
    },
    "国内要闻与政策": {
        "国内要闻": {
            "中新网要闻": "https://www.chinanews.com.cn/rss/scroll-news.xml",
        },
        "政策走向": {
            "新华网": "http://www.news.cn/politics/news_politics.xml",
            "人民网": "http://www.people.com.cn/rss/politics.xml",
            "中新网财经": "https://www.chinanews.com.cn/rss/finance.xml",
        },
    },
    "招聘": {
        "大公司招聘动态": {
            "校招社招新闻": _gnews("校招 OR 社招 OR 招聘 腾讯 OR 字节跳动 OR 阿里巴巴 OR 华为 OR 美团 OR 小米"),
            "海外大厂招聘": _gnews("hiring OR layoffs OR \"graduate program\" Google OR Apple OR Microsoft OR Nvidia OR Ubisoft OR Epic",
                                    lang="en-US", gl="US", ceid="US:en"),
        },
    },
    "时尚": {
        "时尚品牌": {
            "Business of Fashion": "https://www.businessoffashion.com/arc/outboundfeeds/rss/",
            "Vogue": "https://www.vogue.com/feed/rss",
            "WWD": "https://wwd.com/feed/",
            "Hypebeast": "https://hypebeast.com/feed",
            "Highsnobiety": "https://www.highsnobiety.com/feed/",
            "华丽志": "https://luxe.co/feed",
        },
    },
    "AI公司": {
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
}

REQUEST_TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; daily-brief-radar/1.0)"}
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text, limit=220):
    """去掉HTML标签和多余空白，截断到 limit 字"""
    text = _TAG_RE.sub(" ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _parse(url):
    """用 requests 拉取（带超时，避免某个源卡死整条流水线），再交给 feedparser 解析"""
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
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

    results = []
    for entry in feed.entries[:max_items]:
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
            if isinstance(src, dict):
                # 多个源：每个源少取几条，合起来给AI挑
                items = []
                for source_name, url in src.items():
                    got = fetch_one(category, subcategory, url, source_name, max_items=5)
                    print(f"  · {source_name}: {len(got)} 条")
                    items.extend(got)
            else:
                items = fetch_one(category, subcategory, src)
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
