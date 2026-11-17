"""
抓取关注的 YouTube 频道的最近视频（官方 RSS，不需要 API Key）

每个频道返回最近 15 条视频的标题、发布时间、链接、简介。
具体选哪条、怎么概括，由 core/youtube_digest.py 在摘要阶段决定。

想增删频道：改下面的 CHANNELS。频道 ID 的获取方法：
  打开频道主页 → 页面源码里搜 "channelId"，或在 YouTube 搜索结果里读取（以 UC 开头的一串字符）。

运行前需要先装：pip install feedparser requests
"""
import json
import re
from datetime import datetime

import feedparser
import requests

CHANNELS = [
    # 已下架：傑克揚升（@biojacknow）、反抗者J（@TopJ2099），改用更温和的博客、文章和演讲，见 fetch_category_news.py 的“自我提升”
    {"name": "麓安先生", "channel_id": "UCeU7Vq5ds73MOJ_1lc1139A"},    # @麓安先生
]

REQUEST_TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; daily-brief-radar/1.0)"}
_WS_RE = re.compile(r"\s+")


def fetch_channel(ch):
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['channel_id']}"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        print(f"⚠️ 频道「{ch['name']}」抓取失败，跳过（{e}）")
        return []

    videos = []
    for e in feed.entries:
        videos.append({
            "video_id": e.get("yt_videoid", ""),
            "title": e.get("title", ""),
            "url": e.get("link", ""),
            "published": e.get("published", ""),
            "description": _WS_RE.sub(" ", e.get("summary", "") or "").strip()[:1500],
        })
    return videos


if __name__ == "__main__":
    channels = []
    for ch in CHANNELS:
        print(f"正在抓取频道「{ch['name']}」...")
        videos = fetch_channel(ch)
        print(f"  → {len(videos)} 条视频")
        channels.append({"name": ch["name"], "channel_id": ch["channel_id"], "videos": videos})

    output = {
        "fetched_at": datetime.now().isoformat(),
        "source": "youtube",
        "channels": channels,
    }
    with open("youtube.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"已保存到 youtube.json（共 {sum(len(c['videos']) for c in channels)} 条）")
