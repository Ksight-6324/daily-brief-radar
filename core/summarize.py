"""
把已经抓好的各个 json 丢给 DeepSeek，生成结构化的中文简报

【本轮改动——针对实测后一轮很扎心但很中肯的反馈】
1. "今日一句话"上次改成了罗列具体事件名，这次拉回中间地带：要抽象概括当天主题，
   可以举例但不展开讲例子细节。
2. 各榜单头名：不再"死板选各来源排名第一的那条"，改成给AI每个来源的前几条候选，
   AI自己挑"信息能填满五个维度"的那条；哪个来源实在挑不出符合条件的，就跳过这个
   来源，不再用"暂无更多信息""暂不涉及"这种占位废话硬凑。少数派的问题也是这么解决的
   ——不是换源，是让AI从候选里挑真正科技/效率相关的，挑不出来就跳过。
3. 去重加强：科技资讯全览生成时，会把"各榜单头名"已经生成的中文标题也传给AI做参考，
   要求"即使标题、角度、来源不同，只要讲的是同一件事就不要重复选"，而不只是靠原始
   英文标题做字符串匹配。
4. 科技/经济/政治/科学突破这几类内容卡片，"为什么重要/可能影响/后续关注点"三段式
   合并成一个"一句话点评"，减少模板化的重复感。
5. 科学突破新增边界约束：必须是真正的科学发现/技术突破，如果来源里的内容其实是
   政治/经济决策（哪怕挂在科学类RSS源下面），不能选进这个板块。
6. 今日行动建议整个重写：加了明确的"身份提醒"——这个人是在校大学生不是职场人士，
   严禁"作为面试素材""输出产品分析笔记""评估技术可行性"这类职场黑话，建议必须具体
   到"今天几十分钟内能做完的动作"。
7. 所有"对用户的意义/为什么重要"这类字段统一加了"不要写职场黑话，要写第二人称
   '你可以...'这种大白话"的要求。
8. 来源标注：AI筛选结果本身不带来源信息，现在生成完按链接反查原始抓取数据，把
   "来自哪个榜单/媒体"补到每行最前面，卡片变成六段式：来源|||标题|||链接|||标签|||摘要|||点评

运行前需要先跑过对应的抓取脚本，保证 json 文件都在同一个文件夹里
"""
import json
import os
import random
import sys
import time
import urllib.parse
import requests
from datetime import datetime

# 同目录下的模块（python core/summarize.py 运行时，core/ 会自动在导入路径里）
import layout
import learning
import style_rules
import youtube_digest

# ============ DeepSeek API Key：从环境变量读取（GitHub Secrets 里配置） ============
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
# ==========================================================

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

SCIENCE_TOP_N = 2
TECH_TOP_N = 2  # 「人工智能」板块里来自 GitHub/HN/科技媒体的条数（另外 3 条来自 AI 公司动态）
ECONOMY_SUB_N = 2
POLITICS_SUB_N = 2

NO_RHETORIC_RULE = "语言要求：绝对不要使用任何比喻、拟人、排比等修辞手法，不要写“就像”“仿佛”这类词，直接大白话说清楚事实就行，越直白越好。"

NO_JARGON_RULE = """语言风格提醒：写"为什么重要""对用户的意义"这类字段时，不要用"作为面试素材"
"输出产品分析笔记""评估技术可行性"这类空洞的职场黑话或工作任务式表达凑数。要用"你可以..."这种
第二人称、直接、具体的大白话表达，说清楚这条信息对TA本人（结合上面的个人背景判断TA的真实身份和
阶段）具体有什么用、能做什么，不要用一套万能模板去套所有人。"""


# ============ 用户画像：从环境变量读取（引导页问卷生成，GitHub Secrets 里配置） ============
# USER_NAME / USER_PROFILE_TEXT 由 index.html 引导页问卷自动生成，不同用户fork仓库后
# 配的是自己的内容。这里读不到就用一份通用默认画像兜底，保证没配置的情况下（比如你自己
# 原来的用法、或者别人还没填问卷）也能正常跑，不会报错。
USER_NAME = os.environ.get("USER_NAME", "").strip() or "你"
_USER_PROFILE_TEXT = os.environ.get("USER_PROFILE_TEXT", "").strip()

_DEFAULT_PROFILE_TEXT = """身份：暂未设置具体画像
核心目标：了解科技/AI行业趋势，获得有用的信息和具体行动建议
重点关注领域：AI、科技资讯、行业动态
信息过滤规则：无特别排除
内容解释偏好：发生了什么、为什么重要
行动建议偏好：先分析几个可能的方向，再给最推荐的一个
个性化注意事项：无"""

USER_PROFILE_TEXT = _USER_PROFILE_TEXT or _DEFAULT_PROFILE_TEXT

USER_PROFILE_PRIORITY = """筛选内容时请参考这个人的背景（这份背景由TA本人通过问卷填写，请严格按照
里面写的关注领域和排除规则来筛选，不要脑补里面没提到的偏好）：

{profile}

{jargon_rule}""".format(profile=USER_PROFILE_TEXT, jargon_rule=NO_JARGON_RULE)

USER_PROFILE_FULL = """这个人叫{name}，下面是TA通过问卷填写的个人背景，请严格按照这份背景来理解TA的
身份、目标和偏好，不要预设、不要脑补背景里没提到的信息：

{profile}

TA不喜欢空泛鼓励、鸡汤、模糊建议，喜欢直接分析问题、指出风险、给出判断和具体建议。

{jargon_rule}""".format(name=USER_NAME, profile=USER_PROFILE_TEXT, jargon_rule=NO_JARGON_RULE)

SLOT_LABELS = {"noon": "午间", "evening": "晚间"}

# ============ 已推送记录：避免午间和晚间两封简报重复同样的内容 ============
SEEN_FILE = os.environ.get("BRIEF_SEEN_FILE", "seen_items.json")  # 订阅版部署时指向持久磁盘
SEEN_KEEP_DAYS = 14      # 记录保留多少天（RSS 里的旧文章早就滚出去了，不用存太久）
SEEN_MAX_ITEMS = 2000
SEEN_TITLES_HOURS = 36   # 最近多少小时内推送过的标题，会传给 AI 做"同一件事不要重复"的参考


def rome_now():
    """罗马当前时间（自动处理夏令时）。取不到时区数据时退回到 UTC+1。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Rome"))
    except Exception:
        from datetime import timezone, timedelta
        return datetime.now(timezone(timedelta(hours=1)))


def detect_slot(now):
    """17 点以前算午间简报，之后算晚间简报"""
    return "noon" if now.hour < 17 else "evening"


def load_seen(now):
    """读取已推送记录，返回 (已推送链接集合, 最近推送过的标题列表, 原始记录列表)"""
    from datetime import timedelta
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            records = json.load(f).get("items", [])
    except (FileNotFoundError, ValueError):
        records = []

    kept = []
    for r in records:
        try:
            ts = datetime.fromisoformat(r["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=now.tzinfo)
        except Exception:
            continue
        if now - ts <= timedelta(days=SEEN_KEEP_DAYS):
            kept.append({**r, "_ts": ts})

    urls = {r["url"] for r in kept if r.get("url")}
    recent = sorted((r for r in kept if now - r["_ts"] <= timedelta(hours=SEEN_TITLES_HOURS)),
                    key=lambda r: r["_ts"], reverse=True)
    titles = [r["title"] for r in recent if r.get("title")]
    records = [{k: v for k, v in r.items() if k != "_ts"} for r in kept]
    return urls, titles, records


def save_seen(records, sections, now):
    """把这次简报里出现的文章链接和标题追加进记录，供下一次去重"""
    new = []
    for key, text in sections.items():
        if key in ("overview", "action", "greeting"):
            continue
        for line in (text or "").split("\n"):
            parts = [p.strip() for p in line.split("|||")]
            # 卡片行：来源|||标题|||链接|||...；教务行：标题|||链接|||说明（没有来源）
            if len(parts) >= 3 and parts[2].startswith("http"):
                new.append({"url": parts[2], "title": parts[1], "ts": now.isoformat()})
            elif len(parts) >= 2 and parts[1].startswith("http"):
                new.append({"url": parts[1], "title": parts[0], "ts": now.isoformat()})
    have = {r["url"] for r in records}
    for n in new:
        if n["url"] not in have:
            records.append(n)
            have.add(n["url"])
    records = records[-SEEN_MAX_ITEMS:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"items": records}, f, ensure_ascii=False, indent=1)
    print(f"已记录 {len(new)} 条本次推送内容到 {SEEN_FILE}（共 {len(records)} 条）")


def generate_greeting(todo_hint, action_hint, slot="noon"):
    """生成开头问候语+结尾结束语。

    【本次修复】之前问候语天天都差不多，原因有两个：
    1. call_deepseek统一用temperature=0.3（偏保守、求稳），这个场景需要更高的随机性，
       所以这次单独给greeting传了更高的temperature
    2. 之前只说"每天不一样"这种抽象要求，AI很容易还是写成差不多的句式。这次改成
       每次随机抽一个"健康提示方向"和一个"语气锚点"塞进prompt，从源头上制造差异，
       而不是完全指望AI自己"想着要不一样"
    另外，之前漏了你要的两块内容：提醒今天该做的事、健康小知识（喝水之类），这次加上了。
    """
    if slot == "evening":
        mood_rule = """现在是罗马时间的晚上，写给一天快结束的TA。语气放松自然，像朋友一样，
提醒TA花几分钟看完今晚的内容，做完一件小事就可以放松休息了。"""
        health_pool = ["记得喝水", "站起来拉伸一下肩颈", "让眼睛歇一歇别一直盯屏幕",
                       "睡前少刷一会儿手机", "做几个深呼吸放松下来", "早点休息别熬夜"]
    else:
        mood_rule = """现在是罗马时间的中午，写给午饭前后的TA。语气要像朋友一样自然温暖，
提醒TA看一眼今天的内容、午后有空可以做的小事。"""
        health_pool = ["记得喝水", "起来活动一下、别久坐", "好好吃午饭", "让眼睛歇一歇别一直盯屏幕",
                       "做几个深呼吸调整状态", "有空开窗透透气、晒会儿太阳"]

    health_topic = random.choice(health_pool)
    tone_anchor = random.choice([
        "像刚聊完天顺口说一句的语气", "像发消息提醒朋友的语气", "简短利落，不要铺垫太多",
        "带点俏皮但不浮夸", "平静温和，像很熟的朋友", "直接一点，像在催TA赶紧行动",
    ])

    prompt = f"""这个人叫{USER_NAME}。请你以"每日AI简报"这个AI助手的身份，给TA写一段开头问候和一段结尾道别。

{mood_rule}

今天的问候要包含三个要素，自然揉在一起说，不要写成三条并列的清单：
1. 提到"{USER_NAME}"这个名字
2. 提醒TA今天该做的事——参考下面"今日待办参考"里的内容，挑最值得提一句的说（不用照抄，用你自己的话说）
3. 带一句健康小提示，这次要提的方向是："{health_topic}"（用你自己的话自然说出来，不要生硬地copy这句话）

今日待办参考：
{todo_hint}
{action_hint}

写作要求：
- 这次的语气基调：{tone_anchor}
- 开头问候2句话左右，40字以内
- 结尾道别1句话，20字以内，呼应今天的行动
- 不要写"祝你度过美好的一天"这类通用客套话
- 不要写日期、天气这类你不确定的信息
- 每次遣词造句都要有变化，避免使用固定的开头句式

{NO_RHETORIC_RULE}

格式严格按：
【开头】
（开头问候内容）
【结尾】
（结尾道别内容）

不要写多余的开场白。
"""
    return call_deepseek(prompt, temperature=0.95)


def llm_config():
    """决定用哪家模型。配置了 LLM_API_KEY 就走 OpenAI 兼容接口（OpenAI、Gemini 的兼容端点等都行），
    没配置就沿用原来的 DeepSeek，所以不改 Secret 时行为和以前完全一样。

    环境变量（都是 GitHub Secret）：
      LLM_API_KEY           必填才会启用；OpenAI 的 API Key（注意：ChatGPT Plus 订阅不含 API 额度，要单独充值）
      LLM_MODEL             模型名，默认 gpt-6-luna（OpenAI 目前最便宜的一档）
      LLM_BASE_URL          接口地址，默认 https://api.openai.com/v1
      LLM_REASONING_EFFORT  推理强度，默认 low（只对 api.openai.com 生效；填 none 可关闭推理、更快更省，
                            但并非所有模型都支持 none；留空用默认值）
    """
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        return {"name": "DeepSeek", "url": DEEPSEEK_URL, "key": DEEPSEEK_API_KEY,
                "model": "deepseek-chat", "effort": ""}
    base = (os.environ.get("LLM_BASE_URL", "").strip() or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "").strip() or "gpt-6-luna"
    effort = ""
    if "api.openai.com" in base:
        effort = os.environ.get("LLM_REASONING_EFFORT", "").strip() or "low"
    return {"name": f"{model}", "url": base + "/chat/completions", "key": key, "model": model, "effort": effort}


def call_llm(prompt, max_retries=4, temperature=0.3):
    cfg = llm_config()
    headers = {
        "Authorization": f"Bearer {cfg['key']}",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    if cfg["effort"]:
        body["reasoning_effort"] = cfg["effort"]

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(cfg["url"], headers=headers, json=body, timeout=180)
            if resp.status_code == 400:
                # 有些新模型不接受自定义 temperature / reasoning_effort：去掉这个参数立刻重试（不算一次失败）
                msg = resp.text.lower()
                dropped = False
                if "temperature" in msg and "temperature" in body:
                    body.pop("temperature"); dropped = True
                if "reasoning" in msg and "reasoning_effort" in body:
                    body.pop("reasoning_effort"); dropped = True
                if dropped:
                    print("  ℹ️ 该模型不支持部分参数，已去掉后重试")
                    resp = requests.post(cfg["url"], headers=headers, json=body, timeout=180)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return (content or "").strip()
        except Exception as e:
            last_error = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (401, 403):
                print(f"  ❌ {cfg['name']} 认证失败（{status}），请检查 Secret 里的 API Key 是否正确")
                raise
            if status == 429 and "quota" in str(getattr(e.response, "text", "")).lower():
                print(f"  ❌ {cfg['name']} 额度不足，请到 API 账户充值（ChatGPT Plus 订阅不含 API 额度）")
                raise
            if attempt < max_retries:
                wait_seconds = attempt * 5
                print(f"  ⚠️ 第{attempt}次请求失败（{e}），{wait_seconds}秒后重试...")
                time.sleep(wait_seconds)
            else:
                print(f"  ⚠️ 第{attempt}次请求失败（{e}），放弃")
    raise last_error


call_deepseek = call_llm  # 旧名字保留，避免遗漏的调用点出错


def safe_text(label, fn, *args, **kwargs):
    """通用保护：fn 出错就返回空串，让对应部分在邮件里留空而不是整次运行失败"""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        print(f"  ⚠️ 【{label}】生成失败，已跳过：{e}")
        return ""


def safe_pick(label, *args, **kwargs):
    """调用 summarize_one_pick，出错（接口报错、内容审核拒绝等）只跳过这一个板块，不让整次运行失败"""
    try:
        return summarize_one_pick(label, *args, **kwargs)
    except Exception as e:
        print(f"  ⚠️ 【{label}】生成失败，已跳过：{e}")
        return ""


def load_json(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"⚠️ 没找到 {filename}，先跑对应的抓取脚本再来跑这个")
        return None


def filter_seen(items, seen_urls):
    """按给定链接集合过滤候选项。"""
    if not items or not seen_urls:
        return items
    return [x for x in items if (x.get("url") or "").strip() not in seen_urls]


def seen_block_text(seen_titles):
    """把已排除标题整理成给 AI 的提示语；没有就不加。"""
    if not seen_titles:
        return ""
    lines = "\n".join(f"- {t}" for t in seen_titles[:100])
    return ("另外，下面这些内容已经推送过了，即使换了标题、换了来源，"
            "只要讲的是同一件事就绝对不能再选：\n" + lines + "\n\n")


# ============ 来源标注 ============
# AI筛选结果本身不带来源信息，这里在生成后按链接反查原始抓取数据，把"这条新闻来自
# 哪个榜单/媒体"补到每行最前面，最终格式变成六段式：
# 来源|||标题|||链接|||标签|||深度摘要|||一句话点评
SUBCATEGORY_SOURCE_NAME = {
    "中国经济": "中新网财经",
    "世界经济": "BBC商业",
    "国内时政": "中新网时政",
    "国际时政": "BBC国际",
    "科学突破": "BBC科学",
}

DOMAIN_SOURCE = {
    "techcrunch.com": "TechCrunch",
    "theverge.com": "The Verge",
    "bensbites.co": "Ben's Bites",
    "sspai.com": "少数派",
    "github.com": "GitHub",
    "news.ycombinator.com": "Hacker News",
    "chinanews.com.cn": "中新网",
    "bbc.co.uk": "BBC",
    "bbc.com": "BBC",
}


def source_from_domain(url):
    """链接反查不到原始数据时，用域名兜底给出可读的来源名"""
    try:
        domain = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""
    if domain.startswith("www."):
        domain = domain[4:]
    for key, name in DOMAIN_SOURCE.items():
        if domain == key or domain.endswith("." + key):
            return name
    return domain


def attach_source_to_lines(text, item_pool):
    """把AI返回的五段式每行补上来源名（按链接精确匹配回原始抓取数据）"""
    if not text or not item_pool:
        return text
    by_url = {}
    for item in item_pool:
        url = (item.get("url") or "").strip()
        if url and url not in by_url:
            by_url[url] = item
    out_lines = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if "|||" not in line:
            out_lines.append(raw_line)
            continue
        parts = [p.strip() for p in line.split("|||")]
        url = parts[1] if len(parts) >= 2 else ""
        item = by_url.get(url.strip()) if url else None
        if item:
            source = item.get("source") or SUBCATEGORY_SOURCE_NAME.get(item.get("subcategory", ""), "")
        else:
            source = source_from_domain(url)
        source = style_rules.zh_source(source)
        if source:
            out_lines.append(f"{source}|||{line}")
        else:
            out_lines.append(raw_line)
    return "\n".join(out_lines)


def summarize_jwc(data):
    items = data["items"][:20]
    lines = "\n".join([f"{i+1}. [{x['date']}] {x['title']} (链接{i+1}: {x['url']})" for i, x in enumerate(items)])

    prompt = f"""下面是学校教务网最近的通知标题列表，每条后面标了对应的链接编号。请你严格按下面的格式输出，每一条都必须换行独占一行：

【需要行动】
标题|||链接|||一句话说明（不超过15字，说清楚要做什么/截止时间）
（最多5条，没有就不写这个分组）

【仅需了解】
标题|||链接|||一句话说明（不超过15字）
（最多5条）

格式要求：每条内容严格用 ||| 分隔标题、链接、说明三部分，不要加序号，不要加"-"开头。链接必须原样使用我给你的对应"链接N"的真实网址，不要编造。

{NO_RHETORIC_RULE}

判断标准：转专业、报名截止日期临近的竞赛、需要核对的考试安排/成绩 = 需要行动；获奖喜报、预告类通知、跟自己大概率无关的 = 仅需了解。

通知列表：
{lines}
"""
    return call_deepseek(prompt)


def get_top_picks(github_data, hn_data, rss_data, seen_titles=None):
    """各榜单头名：不再死板选各来源排名第一的那条，给AI每个来源的候选池，
    让AI自己挑"信息能填满五个维度、且主题跟科技/AI相关"的那条；
    某个来源实在挑不出合适的，就跳过，不强行凑数。"""
    candidate_blocks = []
    used_titles = set()  # 记录所有候选标题，供后面tech环节去重参考（不管有没有被选中）

    if github_data and github_data["items"]:
        cands = github_data["items"][:5]
        lines = "\n".join([f"  - {x['name']}：{x['description']} (链接: {x['url']})" for x in cands])
        candidate_blocks.append(f"【来源：GitHub 热门榜】候选（从中选1条，选不出合适的就跳过这个来源）：\n{lines}")
        used_titles.update(x["name"] for x in cands)

    if hn_data and hn_data["items"]:
        cands = hn_data["items"][:5]
        lines = "\n".join([f"  - {x['title']}（{x['score']}分） (链接: {x['url']})" for x in cands])
        candidate_blocks.append(f"【来源：Hacker News 热门榜】候选：\n{lines}")
        used_titles.update(x["title"] for x in cands)

    if rss_data and rss_data["items"]:
        by_source = {}
        for item in rss_data["items"]:
            by_source.setdefault(item["source"], []).append(item)
        for src, items in by_source.items():
            cands = items[:5]
            lines = "\n".join([f"  - {x['title']} (链接: {x['url']})" for x in cands])
            candidate_blocks.append(f"【来源：{src} 头条】候选：\n{lines}")
            used_titles.update(x["title"] for x in cands)

    if not candidate_blocks:
        return "", used_titles

    all_candidates = "\n\n".join(candidate_blocks)
    seen_block = seen_block_text(seen_titles)
    prompt = f"""下面按来源列出了几组候选内容，请你从每个来源的候选里挑出1条，生成五段式解读。

{USER_PROFILE_PRIORITY}

挑选规则（很重要）：
1. 优先挑"五个维度都能给出具体内容"的候选，而不是排名第一的那条——如果第一条信息不够具体，
   换这个来源的其他候选试试
2. 如果某个来源的所有候选都填不满五个维度，或者主题跟科技/AI/效率完全无关（比如少数派候选里
   全是影视推荐、生活随笔），就直接跳过这个来源，不要为了凑数硬选一条然后写"暂无""暂不涉及"
3. 允许最终选出的条数少于来源数量，宁缺毋滥

格式严格按（每条一行独占一行，用 ||| 分隔八段，不要加序号或"-"开头）：
来源名|||中文标题|||链接|||发生了什么|||处理情况|||原理科普|||对你的意义

各部分要求（每一段都只写一句话，20字以内，必须是具体信息，不许写"暂无""暂不涉及"这类占位词——
写不出具体内容就说明这条不该被选，请换一条候选）：
- 中文标题：英文翻译成中文
- 链接：原样使用候选里给的真实网址，不要编造
- 发生了什么：一句话说清楚具体是什么事
- 处理情况：现在进展到哪一步（真的没提到就换候选，不要写占位词）
- 原理科普：一句话说清楚背后的原理/技术是什么，用初中生能听懂的话
- 对你的意义：结合上面的个人背景，给一句具体感受或启发

{NO_RHETORIC_RULE}

{style_rules.reader_rules(len(candidate_blocks))}

不要写多余的开场白。

{seen_block}候选内容：
{all_candidates}
"""
    result = style_rules.localize_cards(call_deepseek(prompt), call_deepseek)
    return result, used_titles


def summarize_tech(github_data, hn_data, rss_data, exclude_titles, top_picks_text, seen_titles=None):
    """科技资讯全览：排除候选池里已经出现过的标题，同时把头名的中文标题传给AI做"同一事件"判断参考"""
    all_lines = []
    item_pool = []

    if github_data:
        for x in github_data["items"][:10]:
            if x["name"] in exclude_titles:
                continue
            all_lines.append(f"- [GitHub 热门榜] {x['name']}：{x['description']} (链接: {x['url']})")
            item_pool.append({**x, "source": "GitHub 热门榜"})

    if hn_data:
        for x in hn_data["items"][:15]:
            if x["title"] in exclude_titles:
                continue
            all_lines.append(f"- [Hacker News] {x['title']}（{x['score']}分） (链接: {x['url']})")
            item_pool.append({**x, "source": "Hacker News"})

    if rss_data:
        for x in rss_data["items"][:30]:
            if x["title"] in exclude_titles:
                continue
            all_lines.append(f"- [{x['source']}] {x['title']} (链接: {x['url']})")
            item_pool.append(x)

    combined = "\n".join(all_lines)
    if not combined:
        return ""

    seen_block = seen_block_text(seen_titles)
    prompt = f"""下面是今天科技/AI圈的原始信息。请你注意：下面这些事件已经在"各榜单头名"板块里详细
讲过了，即使这里的标题、角度、来源看起来不一样，只要讲的是同一件事（比如同一家公司同一个产品的
同一次动作），就绝对不能再选：
{top_picks_text if top_picks_text else "（本次没有头名内容）"}

{seen_block}现在请你处理下面的原始信息：
1. 先判断哪些内容其实在讲同一件事，合并成一条
2. 排除掉跟上面"各榜单头名"重复的事件
3. 把英文全部翻译成中文
4. 只挑和人工智能（模型、AI产品、AI工具与开源项目、AI行业动态）直接相关的内容，其他科技内容一律不要选；
   严格挑出其中最值得关注的{TECH_TOP_N}条，宁缺毋滥

行首方括号（如[TechCrunch AI]）表示这条来自哪个来源，供你判断用，不要写进标题或摘要。

{USER_PROFILE_PRIORITY}

格式严格按（每条一行独占一行，用 ||| 分隔五段，不要加序号或"-"开头）：
中文标题|||链接|||关键词标签|||深度摘要|||一句话点评

各部分要求：
- 链接：原样使用对应内容后面给的真实网址，不要编造
- 关键词标签：2-3个词，逗号分隔
- 深度摘要：用大白话讲清楚"这是什么事"，18-20字左右
- 一句话点评：把"为什么重要/可能有什么影响/接下来该关注什么"揉进一句话里说清楚，20-25字，
  要有具体信息，不要写套话

{NO_RHETORIC_RULE}

{style_rules.reader_rules(TECH_TOP_N)}

按重要程度从高到低排列。不要写多余的开场白。

原始信息：
{combined}
"""
    result = call_deepseek(prompt)
    return style_rules.localize_cards(attach_source_to_lines(result, item_pool), call_deepseek)


def summarize_one_pick(label, items, top_n, extra_rule="", seen_titles=None, lens=None):
    """经济/政治子类、科学突破的通用总结函数。lens 为 everyday 或 frontier 时按该取向选稿，
    不传则按“至少 70% 贴近普通人”的比例选稿。"""
    if not items:
        return ""

    source_name = SUBCATEGORY_SOURCE_NAME.get(label, label)
    # 新类别的条目自带 source（具体媒体名）和 summary（RSS摘要），旧类别没有，保持原有行为
    has_source = any(x.get("source") for x in items)
    lines = "\n".join([_format_item_line(x, source_name) for x in items])
    seen_block = seen_block_text(seen_titles)

    prompt = f"""下面是今天【{label}】类别的原始新闻标题（每条后面带了原文链接，行首方括号里是来源名，
供你参考，不要写进标题）。请你：
1. 严格挑出其中最值得关注、影响力最大的 {top_n} 条，不要超过这个数量
2. 如果不是简体中文（英文、意大利文、繁体中文等），翻译成简体中文
全局规则：花边新闻（明星八卦、恋情绯闻、婚变、红毯穿搭、生活琐事、争吵骂战）一律不要选，只选有实质内容的新闻。
{extra_rule}
{seen_block}
{NO_JARGON_RULE}

格式严格按（每条一行独占一行，用 ||| 分隔五段，不要加序号或"-"开头）：
中文标题|||链接|||关键词标签|||深度摘要|||一句话点评

各部分要求：
- 链接：原样使用对应内容后面给的真实网址，不要编造
- 关键词标签：2-3个词，逗号分隔
- 深度摘要：用大白话讲清楚"这是什么事"，18-20字左右
- 一句话点评：把"为什么重要/可能有什么影响"揉进一句话里，20-25字，要具体，不要套话

{NO_RHETORIC_RULE}

{style_rules.reader_rules(top_n, lens)}

事实要求：只能依据上面给出的标题和摘要来写，原文里没有的数字、人名、结论、原因一律不要编造；
信息不够就换一条，或者直接不选。

如果这批新闻里根本没有符合【{label}】主题的内容，直接返回空，不要硬选不相关的内容凑数。

不要写多余的开场白。

原始新闻：
{lines}
"""
    result = call_deepseek(prompt)
    if result and has_source:
        # 按链接反查每条的真实来源（Dezeen / CNBC / OpenAI ...），反查不到时用域名兜底
        result = attach_source_to_lines(result, items)
    elif result:
        result = "\n".join(
            f"{source_name}|||{line}" if "|||" in line else line
            for line in result.split("\n")
        )
    return style_rules.localize_cards(result, call_deepseek)


def _format_item_line(x, default_source):
    """给AI看的一行候选：[来源] 标题｜日期｜摘要 (链接: ...)。摘要能减少AI只看标题就瞎猜的情况"""
    src = x.get("source") or default_source
    line = f"- [{src}] {x['title']}"
    date = (x.get("published") or "")[:16]
    if date:
        line += f"｜{date}"
    summary = x.get("summary")
    if summary:
        line += f"｜摘要：{summary}"
    return f"{line} (链接: {x['url']})"


def titles_from(text):
    """从已生成的板块文本里取出标题（行格式：来源|||标题|||链接|||...），用来给后面的板块做去重参考"""
    titles = []
    for line in (text or "").split("\n"):
        parts = [p.strip() for p in line.split("|||")]
        if len(parts) >= 3 and parts[1]:
            titles.append(parts[1])
    return titles


# ============ 13 个板块的内容规格 ============
# 板块的顺序、每个板块每组最多几条，在 core/layout.py；
# 这里写每个"内容组"从抓取脚本的哪个大类/子类里挑、挑几条、筛选规则。
# 额外规则从"3."开始编号（1、2 是通用规则）。想调整某个板块的口味，直接改这里的文字。
WATCHLIST = os.environ.get("WATCHLIST", "").strip()  # 可选：你关注的公司/股票代码，逗号分隔
_WATCH_RULE = f"\n5. 用户重点关注的公司/股票（相关消息优先选）：{WATCHLIST}" if WATCHLIST else ""

NO_ADVICE_RULE = "只陈述事实，绝对不要给买入、卖出、持有建议，不要写目标价，也不要预测价格涨跌。"

# (内容组 key, 抓取大类, [(子类, 选几条, 额外规则), ...])
SECTION_SPECS = [
    ("markets", "股票经济", [
        ("美股", 2,
         "3. 只挑对美股大盘或大型上市公司有实质影响的消息（指数、利率、监管、重大公司事件）\n"
         f"4. {NO_ADVICE_RULE}" + _WATCH_RULE),
        ("财报", 1,
         "3. 只挑公司发布财报/业绩的新闻。标题或摘要里有的关键数字（营收、利润、同比、是否超预期、业绩指引）要写出来，"
         "没有就不要编造数字；优先大型、知名的上市公司\n"
         f"4. {NO_ADVICE_RULE}" + _WATCH_RULE),
        ("港股", 1,
         "3. 只挑港股、恒生指数或在港上市公司相关的消息（重要公告、融资、供股、业绩、监管）\n"
         f"4. {NO_ADVICE_RULE}" + _WATCH_RULE),
        ("宏观经济", 1,
         "3. 只挑对中国或世界经济有实质影响的消息（增长、通胀、利率、贸易、产业政策、就业数据）\n"
         f"4. {NO_ADVICE_RULE}"),
    ]),
    ("commodities", "石油黄金", [
        ("石油能源", 3,
         "3. 只挑原油、天然气和能源市场本身的消息：油价走势与原因、OPEC 及产油国决策、库存数据、重大供给中断、地缘影响。"
         "新能源项目的宣传稿、公司人事不要选\n"
         f"4. {NO_ADVICE_RULE}"),
        ("黄金贵金属", 2,
         "3. 只挑黄金、白银等贵金属价格和市场的消息：价格走势与驱动因素、央行购金、矿业重大事件。"
         "外汇和股市的消息即使顺带提到金价也不要选\n"
         f"4. {NO_ADVICE_RULE}"),
    ]),
    ("politics_mil", "政治军事", [
        ("国际政治", 2,
         "3. 只挑对世界格局有实质影响的政治外交新闻：选举、战争与停火谈判、制裁、重大条约、欧盟和意大利的重要政策。"
         "评论文章和人物花边不要选"),
        ("国内政策", 1,
         "3. 只挑具体的政策发布、法规修订、部委或监管机构的动作、产业/就业/教育政策走向。"
         "领导人活动报道、口号式宣传稿、纯文化稿件不要选\n"
         "4. 一句话点评要点出：这个政策实际影响谁、有什么变化"),
        ("军事", 2,
         "3. 只挑军事与国防本身的新闻：战争局势、重大装备与部署、军费和军事战略、军工产业。"
         "纯评论、历史回顾、装备迷的产品介绍不要选"),
    ]),
    ("history_news", "历史", [
        ("历史新闻", 2,
         "3. 只挑有历史学价值的内容：考古新发现、历史研究的新结论、重要历史事件或人物的深度解读。"
         "怀旧清单、影视剧集解析、文化八卦不要选"),
    ]),
    ("live_music", "音乐演出", [
        ("音乐与演出", 5,
         "3. 只挑音乐现场与演出本身的新闻：演唱会和巡演的宣布与回顾、音乐节、歌剧与交响乐演出、颁奖典礼的表演与结果、"
         "重要音乐人的演出事件。明星八卦、恋情、争吵、穿搭一律不要选；单纯的新专辑发布留给「专辑影视」板块，这里不选"),
    ]),
    ("design_art", "设计美术", [
        ("设计", 2,
         "3. 优先挑有影响力的设计新闻：知名设计师/事务所/品牌的新作品、重要展览与设计周（如米兰设计周）、"
         "设计行业动向、意大利设计。纯商品促销不要选"),
        ("美术", 2,
         "3. 只挑美术与艺术本身的新闻：重要展览、美术馆与博物馆动态、艺术家的作品与访谈、重要拍卖与艺术奖项、"
         "文物与艺术史的新发现。八卦和纯商业炒作不要选"),
    ]),
    ("album_film", "专辑影视", [
        ("新专辑", 2,
         "3. 只挑新专辑本身：新专辑的发布或宣布、有代表性的专辑评论（高分或有争议的作品）。"
         "演唱会、八卦、单曲花絮不要选"),
        ("影视", 3,
         "3. 只挑电影和电视剧本身的新闻：新片和新剧的上映/定档/首播、预告片、电影节与奖项、票房与口碑。"
         "明星八卦、感情生活、红毯穿搭、绯闻一律不要选"),
    ]),
    ("ai_feeds", "人工智能", [
        ("AI公司动态", 3,
         "3. 只挑AI公司自己的重要发布或动向：新模型、产品功能、重大合作、融资、安全与政策声明。"
         "教程类、客户案例类、纯评论类内容不要选"),
    ]),
    ("growth", "自我提升", [
        ("自律与行动力", 2,
         "3. 优先挑自律与自我约束的文章：习惯养成、专注力、克服拖延、自控力与行动力，要有具体方法和依据。"
         "鸡汤、空洞励志、广告软文、购物清单不要选\n"
         "4. 一句话点评写出读完能带走的一个可操作要点\n"
         "5. 语气要温和平和：优先选博客、文章和演讲口播里温和、理性、讲方法的内容；"
         "打鸡血、贩卖焦虑、说教训斥、鼓吹逆袭暴富的内容一律不要选"),
    ]),
    ("science_pc", "物理化学", [
        ("物理", 3,
         "3. 只挑真正的物理学发现或实验进展（量子、凝聚态、天体与宇宙、粒子、光学等），说清楚新发现是什么。"
         "科普问答、人物访谈、测验题不要选"),
        ("化学", 2,
         "3. 只挑真正的化学发现或材料进展（新反应、新材料、催化、电池等），说清楚新发现是什么。"
         "科普问答、人物访谈不要选"),
    ]),
    ("biotech", "生物科技", [
        ("生物科技", 5,
         "3. 只挑生物科技与医药研发的实质进展：药物审批和临床试验结果、基因与细胞疗法、生物技术新方法、重要融资与并购。"
         "公司人事、会议预告不要选\n"
         "4. 医药内容只陈述事实，不要写成对读者的用药或治疗建议"),
    ]),
    ("mind", "心灵疗养", [
        ("心灵疗养文章", 4,
         "3. 只挑有研究或实践依据的内容：冥想与正念练习、心理健康研究、情绪与压力调节、睡眠与身心恢复、哲学与心灵思考。"
         "推销产品、神秘学、偏方疗法不要选\n"
         "4. 涉及心理健康的内容只陈述研究和做法，不要写成医疗或诊断建议"),
    ]),
    ("jobs", "就业机会", [
        ("大公司招聘动态", 5,
         "3. 候选里混有综合科技和商业新闻，只有和招聘、用工、裁员、薪资直接相关的才选，其余一律不选，宁可返回空。"
         "要选的是跟大公司招聘有关的新闻：校招/社招启动、扩招或缩招、裁员、薪资与岗位趋势、招聘要求变化。"
         "优先腾讯、字节、阿里、华为、美团、小米及海外科技/游戏/设计相关大公司；很久以前的宣讲会通知不要选\n"
         "4. 这里是招聘动态新闻，不是岗位列表，不要编造具体岗位或投递链接"),
    ]),
]

# overview / jwc / action / greeting 是整体性的内容；其余 key 来自 layout.LAYOUT
ALL_SECTION_KEYS = ["overview", "jwc", *layout.CONTENT_KEYS, "action", "greeting"]



def generate_action_advice(jwc_text, tech_text, top_picks_text, economy_text, politics_text, science_text, extra_text=""):
    """今日行动建议：结合用户画像+当天全部内容，方向和推荐动作必须是学生今天能做完的具体小事"""
    all_content = f"""教务网：
{jwc_text}

各榜单头名：
{top_picks_text}

科技资讯：
{tech_text}

经济：
{economy_text}

政治：
{politics_text}

科学突破：
{science_text}

其他板块（AI公司、股市、游戏、设计、欧洲意大利、国内政策、招聘、时尚）：
{extra_text if extra_text else "无"}
"""

    prompt = f"""{USER_PROFILE_FULL}

下面是今天简报的全部内容，请你基于这些信息，给这个人生成一段"今日行动建议"。

严格按这个结构输出：

【可能的方向】
列出2-3个今天内容里能延伸出的具体方向，每个方向必须是"一个大学生今天几十分钟到1小时内
能实际做完的具体小事"，比如"打开这个产品自己用10分钟，记录3个体验感受"这种程度，
绝对不能是"整理XX清单""输出XX笔记""评估XX可行性"这类模糊的工作任务式表达。
格式：方向名称|||具体做法（20-30字，要具体到"打开什么、看什么、做什么"）

【不建议投入】
指出1个不值得现在投入时间的方向，并说明为什么（比如门槛太高、跟TA当前阶段不匹配），
1-2句话，不超过40字

【今日推荐行动】
从上面的方向里选1个，或者结合TA的个人背景，给一个TA今天就能开始做的
具体动作，必须具体到"打开什么、做什么、大概花多久"，不超过40字，不能是模糊的"了解一下"
"关注一下"

{NO_RHETORIC_RULE}

不要写多余的开场白。

补充限制：行动建议里不要出现买入、卖出具体股票或任何投资操作的建议。

今天的简报内容：
{all_content}
"""
    return call_deepseek(prompt)


def summarize_overview(jwc_text, tech_text):
    prompt = f"""下面是今天简报各板块的摘要（科技、国际政治、经济、股市、文化艺术等，内容来自全世界），
请你写一段"今日总览"，概括这半天里全世界最值得关注的事，格式严格如下：

【今日一句话】
用抽象但有信息量的方式概括今天的主题/趋势，不超过25字。要求：
- 不要写"今日多项通知发布"这种完全没有指向性的空话
- 但也不要罗列多个具体产品名/公司名/事件细节堆在一句话里，那样太琐碎
- 正确的程度类似"AI安全与开源合规成为今日焦点"这种——概括出一个主题/矛盾/趋势，
  可以带1个简短例子帮助理解，但不要在这一句话里展开讲例子的细节

【AI观察】
2-3句话，讲清楚今天各板块里最重要的两三件事，以及它们之间有没有共同点（比如政治和经济的联动、科技和文化的呼应），直接说事实和判断

【趋势预测】
1-2句话，基于今天的信息，往前展望一步：接下来可能会怎样发展。
{NO_JARGON_RULE}
结合这个人的个人背景，这个趋势可能意味着什么值得关注的方向（不要用职场黑话）

【今日最该做的一件事】
如果教务网有"需要行动"的内容，从中挑最紧急的一条，一句话提醒（不超过20字）；如果没有，就写"今天没有紧急待办，可以轻松看资讯"

{NO_RHETORIC_RULE}

教务网摘要：
{jwc_text}

各板块摘要：
{tech_text}
"""
    return call_deepseek(prompt)


if __name__ == "__main__":
    _cfg = llm_config()
    print(f"本次使用的模型：{_cfg['name']}（{_cfg['url']}）")
    if not _cfg["key"]:
        print("❌ 没有配置任何 API Key（LLM_API_KEY 或 DEEPSEEK_API_KEY），无法生成简报")
        sys.exit(1)

    now = rome_now()
    slot = detect_slot(now)
    print(f"正在生成罗马时间 {now:%Y-%m-%d %H:%M} 的{SLOT_LABELS[slot]}简报...\n")

    today = now.strftime("%Y-%m-%d")
    sections = {}

    # 之前推送过的内容：链接用来在候选里直接剔除，标题传给 AI 避免"换个来源讲同一件事"
    seen_urls, seen_titles, seen_records = load_seen(now)
    print(f"已推送记录：{len(seen_urls)} 条链接，其中最近 {SEEN_TITLES_HOURS} 小时 {len(seen_titles)} 条标题\n")

    jwc_data = load_json("jwc_news.json")
    if jwc_data:
        jwc_data["items"] = filter_seen(jwc_data["items"], seen_urls)
        if not jwc_data["items"]:
            jwc_data = None

    if jwc_data:
        print("正在总结教务网通知...")
        sections["jwc"] = summarize_jwc(jwc_data)
        print(sections["jwc"] + "\n")

    github_data = load_json("github_trending.json")
    hn_data = load_json("hacker_news.json")
    rss_data = load_json("rss_sources.json")
    if github_data:
        github_data["items"] = filter_seen(github_data["items"], seen_urls)
        if not github_data["items"]:
            github_data = None
    if hn_data:
        hn_data["items"] = filter_seen(hn_data["items"], seen_urls)
        if not hn_data["items"]:
            hn_data = None
    if rss_data:
        rss_data["items"] = filter_seen(rss_data["items"], seen_urls)
        if not rss_data["items"]:
            rss_data = None

    # ---------- 人工智能板块（上半）：GitHub 热门 / Hacker News / 科技媒体里和 AI 相关的内容 ----------
    # 整个简报里 AI 只占「人工智能」一个板块（最多 5 条）：这里挑 2 条，下面的 AI 公司动态挑 3 条
    if github_data or hn_data or rss_data:
        print(f"正在挑选人工智能相关的科技资讯（目标{TECH_TOP_N}条）...")
        try:
            sections["ai_tech"] = summarize_tech(github_data, hn_data, rss_data, set(), "", seen_titles)
        except Exception as e:
            print(f"  ⚠️ 科技资讯生成失败，跳过：{e}")
            sections["ai_tech"] = ""
        print(sections.get("ai_tech", "") + "\n")

    category_data = load_json("category_news.json")
    if category_data:
        category_data["items"] = filter_seen(category_data["items"], seen_urls)
        if not category_data["items"]:
            category_data = None

    # ---------- 各板块：每个子类单独让AI挑，某一个失败不影响其他板块 ----------
    if category_data:
        items_by_key = {}
        for item in category_data["items"]:
            key = (item.get("category"), item.get("subcategory"))
            items_by_key.setdefault(key, []).append(item)

        dedupe = list(seen_titles) + titles_from(sections.get("ai_tech", ""))  # 之前推过的 + 本次已选的标题
        for key, category, subs in SECTION_SPECS:
            parts = []
            for sub_label, top_n, rule in subs:
                items = items_by_key.get((category, sub_label), [])
                if not items:
                    print(f"（【{category}-{sub_label}】没有候选，跳过）")
                    continue
                print(f"正在筛选【{category}-{sub_label}】（目标{top_n}条）...")
                try:
                    result = summarize_one_pick(sub_label, items, top_n, rule, seen_titles=dedupe)
                except Exception as e:
                    print(f"  ⚠️ 【{sub_label}】生成失败，跳过：{e}")
                    continue
                if result:
                    parts.append(result)
                    dedupe.extend(titles_from(result))
                    print(result + "\n")
            sections[key] = "\n".join(parts)

    # ---------- YouTube 频道更新（并入「自我提升」板块） ----------
    yt_data = load_json("youtube.json")
    if yt_data:
        seen_ts = {}
        for r in seen_records:
            try:
                seen_ts[r["url"]] = datetime.fromisoformat(r["ts"])
            except Exception:
                pass
        try:
            sections["youtube"] = youtube_digest.build_youtube_section(call_deepseek, yt_data, seen_ts, now)
        except Exception as e:
            print(f"⚠️ YouTube 板块生成失败，跳过：{e}")
        if sections.get("youtube"):
            print(sections["youtube"] + "\n")

    # ---------- 历史上的今天（中外历史）/ 艺术风格小课（设计美术）/ 每日诗词（心灵疗养） ----------
    for key, builder, label in (("history", learning.build_history, "历史上的今天"),
                                ("poem", learning.build_poem, "每日诗词"),
                                ("style", learning.build_style, "艺术风格小课")):
        print(f"正在生成【{label}】...")
        try:
            sections[key] = builder(call_deepseek, now, slot, seen_urls)
        except Exception as e:
            print(f"  ⚠️ 【{label}】生成失败，跳过：{e}")
            continue
        if sections.get(key):
            print(sections[key] + "\n")

    # 每个板块最多 5 条：AI 偶尔会多给，这里强制裁掉多出来的
    trimmed = layout.apply_caps(sections)
    if trimmed:
        print(f"（有 {trimmed} 条超出板块上限，已裁掉）")

    # 给行动建议参考的新闻文字（YouTube、历史、诗词、艺术风格不放进去，行动建议只基于新闻）
    news_keys = [k for k in layout.CONTENT_KEYS if k not in layout.NON_NEWS_KEYS]
    extra_text = "\n\n".join(f"【{key}】\n{sections[key]}" for key in news_keys if sections.get(key))
    # 总览要覆盖"这半天内全世界值得关注的事"：把各新闻板块的内容合在一起给 AI 归纳
    tech_for_overview = "\n".join(sections[k] for k in news_keys if sections.get(k)) or "无"
    has_content = any(sections.get(k) for k in ALL_SECTION_KEYS if k not in ("overview", "action", "greeting"))

    if has_content:
        print("正在生成今日总览...")
        sections["overview"] = safe_text("今日总览", summarize_overview,
            sections.get("jwc", "无"), tech_for_overview
        )
        print(sections["overview"] + "\n")

        print("正在生成今日行动建议（学生视角，禁止职场黑话）...")
        sections["action"] = safe_text("今日行动建议", generate_action_advice,
            sections.get("jwc", "无"),
            sections.get("ai_tech", "无"),
            "无",
            "无",
            "无",
            "无",
            extra_text,
        )
        print(sections["action"] + "\n")

    print(f"正在生成{SLOT_LABELS[slot]}问候语...")
    sections["greeting"] = safe_text("问候语", generate_greeting,
        sections.get("overview", "（今天没有总览内容）"),
        sections.get("action", "（今天没有行动建议）"),
        slot,
    )
    print(sections["greeting"] + "\n")

    with open("daily_brief.md", "w", encoding="utf-8") as f:
        f.write(f"DATE|||{today}\n")
        f.write(f"SLOT|||{slot}\n")
        for key in ALL_SECTION_KEYS:
            f.write(f"SECTION|||{key}\n")
            f.write(sections.get(key, "") + "\n")

    print("✅ 简报已生成：daily_brief.md")

    # 记录本次推送内容。注意：这里只写本地文件；工作流只有在邮件发送成功之后才会把它提交回仓库，
    # 所以邮件发送失败的话，这些内容不会被当成"已推送"，下一次还会再选。
    save_seen(seen_records, sections, now)
