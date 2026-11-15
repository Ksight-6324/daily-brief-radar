"""
把 daily_brief.md 解析成结构化数据，生成精致的 HTML 卡片邮件发出去

【本轮改动】
1. 各榜单头名从8段（含"普通人获得"）精简成7段（来源/标题/链接/发生了什么/处理情况/原理科普/对你的意义）
2. 科技资讯/经济/政治/科学突破的内容卡片从7段精简成5段（标题/链接/标签/摘要/一句话点评），
   原来的"为什么重要/可能影响/后续关注点"三段合并成"一句话点评"一段，减少模板化重复感
3. 行动建议板块渲染逻辑不变
4. 结束语挪进底部"今日最该做的一件事"的蓝色框里，更醒目
5. 内容卡片改为六段式（来源/标题/链接/标签/摘要/一句话点评），卡片顶部新增来源徽标
"""
import os
import re
import smtplib
from html import escape as esc

import layout
from email.mime.text import MIMEText
from email.header import Header

# 邮箱账号/授权码/收件人：从环境变量读取（GitHub Secrets 里配置）
QQ_EMAIL = os.environ.get("QQ_EMAIL", "")
QQ_AUTH_CODE = os.environ.get("QQ_AUTH_CODE", "")
SEND_TO = os.environ.get("SEND_TO", QQ_EMAIL)

# SMTP 服务器/端口可通过环境变量覆盖，默认仍是 QQ 邮箱。
# Gmail：smtp.gmail.com / 465；Outlook：smtp-mail.outlook.com / 587（587 走 STARTTLS）
SMTP_SERVER = os.environ.get("SMTP_SERVER", "").strip() or "smtp.qq.com"
SMTP_PORT = int(os.environ.get("SMTP_PORT", "").strip() or "465")

COLOR_DARK = "#2c3e50"
COLOR_ACCENT = "#3f6b8f"
COLOR_MUTED = "#8a95a3"
COLOR_BG_ACCENT = "#eaf0f5"
COLOR_BG_MUTED = "#f4f5f6"

MAX_EMAIL_KB = 95  # 单封邮件大小上限，超过就拆成两封（Gmail 约 102KB 会截断）

# 简报时段（由 summarize.py 按罗马时间判断后写入 daily_brief.md）
SLOT_GREETING = {"noon": "午安", "evening": "晚上好", "morning": "早安"}
SLOT_TAG = {"noon": "午报", "evening": "晚报", "morning": "早报"}

# 板块的顺序、标题和每组条数上限在 core/layout.py 的 LAYOUT 里调整（邮件和摘要脚本共用同一份）

_LEADING_MARKER_RE = re.compile(r"^[\s\-\*•·]*(\d+[\.\、\)]\s+)?")


def _split_line(line, min_parts):
    line = _LEADING_MARKER_RE.sub("", line).strip()
    if "|||" not in line:
        return None
    parts = [p.strip() for p in line.split("|||")]
    if len(parts) < min_parts:
        parts += [""] * (min_parts - len(parts))
    return parts


def parse_brief(content):
    date = ""
    slot = "morning"
    keys = ["overview", "jwc", "action", "greeting"] + layout.CONTENT_KEYS
    sections = {k: "" for k in keys}
    current = None
    for line in content.split("\n"):
        if line.startswith("DATE|||"):
            date = line.split("|||", 1)[1]
        elif line.startswith("SLOT|||"):
            slot = line.split("|||", 1)[1]
        elif line.startswith("SECTION|||"):
            current = line.split("|||", 1)[1]
        elif current in sections:
            sections[current] += line + "\n"
    return date, slot, sections


def render_overview(text):
    one_liner, ai_view, trend, todo = "", "", "", ""
    mode = None
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "今日一句话" in line:
            mode = "one_liner"
            continue
        if "AI观察" in line:
            mode = "ai_view"
            continue
        if "趋势预测" in line:
            mode = "trend"
            continue
        if "最该做的" in line:
            mode = "todo"
            continue
        if mode == "one_liner":
            one_liner += line
        elif mode == "ai_view":
            ai_view += line + " "
        elif mode == "trend":
            trend += line + " "
        elif mode == "todo":
            todo += line

    trend_html = f'''<div style="font-size:15px; color:#cfd8e3; line-height:1.7; margin-top:12px; border-top:1px solid rgba(255,255,255,0.2); padding-top:12px;">🔮 {trend}</div>''' if trend else ""

    html = f'''<div style="background:{COLOR_DARK}; border-radius:8px; padding:22px; margin-bottom:20px; color:#fff;">
        <div style="font-size:19px; font-weight:700; line-height:1.6;">{one_liner}</div>
        <div style="font-size:15px; color:#cfd8e3; line-height:1.7; margin-top:14px; border-top:1px solid rgba(255,255,255,0.2); padding-top:14px;">💭 {ai_view}</div>
        {trend_html}
    </div>'''
    return html, todo


def render_jwc_cards(text):
    html = ""
    mode = None
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if "需要行动" in line:
            mode = "action"
            html += f'<div style="font-size:15px; color:{COLOR_ACCENT}; font-weight:bold; margin:16px 0 10px;">● 需要行动</div>'
            continue
        if "仅需" in line:
            mode = "info"
            html += f'<div style="font-size:15px; color:{COLOR_MUTED}; font-weight:bold; margin:16px 0 10px;">○ 仅需了解</div>'
            continue
        parts = _split_line(line, 3)
        if not parts:
            continue
        title, url, desc = parts[0], parts[1], parts[2]
        if not title:
            continue
        title_html = f'<a href="{url}" style="color:inherit; text-decoration:none; border-bottom:1px dotted currentColor;">{title}</a>' if url else title
        if mode == "action":
            html += f'''<div style="background:{COLOR_BG_ACCENT}; border-left:3px solid {COLOR_ACCENT}; border-radius:4px; padding:14px 18px; margin-bottom:10px;">
                <div style="font-size:17px; font-weight:600; color:#222;">{title_html}</div>
                <div style="font-size:15px; color:#555; margin-top:6px; line-height:1.5;">{desc}</div>
            </div>'''
        else:
            html += f'''<div style="background:{COLOR_BG_MUTED}; border-radius:4px; padding:14px 18px; margin-bottom:10px;">
                <div style="font-size:16px; color:#444;">{title_html}</div>
                <div style="font-size:14px; color:#999; margin-top:6px; line-height:1.5;">{desc}</div>
            </div>'''
    return html


def render_top_picks(text):
    """七段式：来源|||标题|||链接|||发生了什么|||处理情况|||原理科普|||对你的意义"""
    html = ""
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        parts = _split_line(line, 7)
        if not parts:
            continue
        label, title, url, happened, progress, principle, meaning = parts[:7]
        if not title:
            continue
        title_html = f'<a href="{url}" style="color:#222; text-decoration:none; border-bottom:1px dotted #222;">{title}</a>' if url else title
        html += f'''<div style="background:#fff; border:1px solid {COLOR_ACCENT}; border-radius:6px; padding:18px 20px; margin-bottom:14px;">
            <div style="display:inline-block; background:{COLOR_ACCENT}; color:#fff; font-size:12px; padding:3px 10px; border-radius:10px; margin-bottom:8px;">{label}</div>
            <div style="font-size:18px; font-weight:600; color:#222; margin-top:6px;">{title_html}</div>
            <div style="font-size:14.5px; color:#666; margin-top:10px; line-height:1.6;"><b style="color:{COLOR_ACCENT};">发生了什么：</b>{happened}</div>
            <div style="font-size:14.5px; color:#666; margin-top:6px; line-height:1.6;"><b style="color:{COLOR_ACCENT};">处理情况：</b>{progress}</div>
            <div style="font-size:14.5px; color:#666; margin-top:6px; line-height:1.6;"><b style="color:{COLOR_ACCENT};">原理科普：</b>{principle}</div>
            <div style="font-size:14.5px; color:#333; margin-top:8px; line-height:1.6; background:{COLOR_BG_ACCENT}; padding:8px 10px; border-radius:4px;"><b style="color:{COLOR_ACCENT};">对你的意义：</b>{meaning}</div>
        </div>'''
    return html


def render_content_cards(text):
    """六段式：来源|||标题|||链接|||标签|||深度摘要|||一句话点评"""
    html = ""
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        parts = _split_line(line, 5)
        if not parts:
            continue
        if len(parts) >= 6:
            source, title, url, tags, digest, comment = parts[:6]
        else:
            source = ""
            title, url, tags, digest, comment = parts[:5]
        if not title:
            continue
        title_html = f'<a href="{url}" style="color:#222; text-decoration:none; border-bottom:1px dotted #222;">{title}</a>' if url else title
        tag_badges = "".join([
            f'<span style="display:inline-block; background:{COLOR_BG_ACCENT}; color:{COLOR_ACCENT}; font-size:12px; padding:3px 8px; border-radius:8px; margin-right:5px;">{t.strip()}</span>'
            for t in tags.split(",") if t.strip()
        ])
        source_badge = f'<span style="display:inline-block; background:{COLOR_ACCENT}; color:#fff; font-size:12px; padding:3px 10px; border-radius:10px; margin-right:6px; vertical-align:2px;">{source}</span>' if source else ""
        html += f'''<div style="background:{COLOR_BG_MUTED}; border-radius:6px; padding:16px 18px; margin-bottom:12px;">
            <div style="font-size:16.5px; font-weight:600; color:#222;">{source_badge}{title_html}</div>
            <div style="margin-top:8px;">{tag_badges}</div>
            <div style="font-size:14.5px; color:#555; margin-top:8px; line-height:1.6;">{digest}</div>
            <div style="font-size:13.5px; color:#888; margin-top:8px; line-height:1.6;">💬 {comment}</div>
        </div>'''
    return html


def render_youtube_cards(text):
    """频道|||标题|||链接|||新作/往期|||依据|||概括"""
    html = ""
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        parts = _split_line(line, 6)
        if not parts or not parts[1]:
            continue
        channel, title, url, kind, basis, summary = [esc(p) for p in parts[:6]]
        kind_color = COLOR_ACCENT if kind == "新作" else COLOR_MUTED
        basis_note = "依据：字幕" if basis == "字幕" else "依据：仅标题和简介，概括较粗略"
        title_html = f'<a href="{url}" style="color:#222; text-decoration:none; border-bottom:1px dotted #222;">{title}</a>' if url else title
        html += f'''<div style="background:{COLOR_BG_MUTED}; border-radius:6px; padding:16px 18px; margin-bottom:12px;">
            <div style="font-size:16.5px; font-weight:600; color:#222;">
                <span style="display:inline-block; background:{COLOR_ACCENT}; color:#fff; font-size:12px; padding:3px 10px; border-radius:10px; margin-right:6px; vertical-align:2px;">{channel}</span>
                <span style="display:inline-block; background:{kind_color}; color:#fff; font-size:12px; padding:3px 8px; border-radius:10px; margin-right:6px; vertical-align:2px;">{kind}</span>{title_html}</div>
            <div style="font-size:14.5px; color:#555; margin-top:8px; line-height:1.6;">{summary}</div>
            <div style="font-size:12.5px; color:#999; margin-top:6px;">{basis_note}</div>
        </div>'''
    return html


def render_history_cards(text):
    """维基百科|||标题|||链接|||年份|||解读|||为什么值得知道"""
    html = ""
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        parts = _split_line(line, 6)
        if not parts or not parts[1]:
            continue
        _, title, url, year, explain, why = [esc(p) for p in parts[:6]]
        title_html = f'<a href="{url}" style="color:#222; text-decoration:none; border-bottom:1px dotted #222;">{title}</a>' if url else title
        html += f'''<div style="background:{COLOR_BG_MUTED}; border-radius:6px; padding:16px 18px; margin-bottom:12px;">
            <div style="font-size:16.5px; font-weight:600; color:#222;">
                <span style="display:inline-block; background:{COLOR_ACCENT}; color:#fff; font-size:12px; padding:3px 10px; border-radius:10px; margin-right:6px; vertical-align:2px;">{year}</span>{title_html}</div>
            <div style="font-size:14.5px; color:#555; margin-top:8px; line-height:1.6;">{explain}</div>
            <div style="font-size:13.5px; color:#888; margin-top:8px; line-height:1.6;">💬 {why}</div>
        </div>'''
    return html


def render_poem_card(text):
    """标题|||作者·朝代|||原文（句子用 / 分隔）|||赏析|||背景"""
    html = ""
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        parts = _split_line(line, 5)
        if not parts or not parts[0] or not parts[2]:
            continue
        title, author, body, appreciation, background = [esc(p) for p in parts[:5]]
        body_html = "<br>".join(s.strip() for s in body.split(" / ") if s.strip())
        html += f'''<div style="background:#fff; border:1px solid {COLOR_ACCENT}; border-radius:6px; padding:18px 20px; margin-bottom:12px;">
            <div style="text-align:center; font-size:18px; font-weight:600; color:{COLOR_DARK};">《{title}》</div>
            <div style="text-align:center; font-size:13px; color:{COLOR_MUTED}; margin-top:2px;">{author}</div>
            <div style="text-align:center; font-family:'Songti SC','SimSun',serif; font-size:16px; color:#222; line-height:1.9; margin:12px 0;">{body_html}</div>
            <div style="font-size:14.5px; color:#555; line-height:1.6;"><b style="color:{COLOR_ACCENT};">赏析：</b>{appreciation}</div>
            <div style="font-size:13.5px; color:#888; margin-top:6px; line-height:1.6;">📖 {background}</div>
        </div>'''
    return html


def render_style_card(text):
    """维基百科|||风格名|||链接|||起源|||特征|||代表|||练习"""
    html = ""
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        parts = _split_line(line, 7)
        if not parts or not parts[1]:
            continue
        _, name, url, origin, features, reps, practice = [esc(p) for p in parts[:7]]
        name_html = f'<a href="{url}" style="color:#222; text-decoration:none; border-bottom:1px dotted #222;">{name}</a>' if url else name
        feats = "".join(f'<li style="margin-bottom:3px;">{f.strip()}</li>' for f in features.split("；") if f.strip())
        html += f'''<div style="background:{COLOR_BG_MUTED}; border-radius:6px; padding:16px 18px; margin-bottom:12px;">
            <div style="font-size:17px; font-weight:600; color:#222;">{name_html}</div>
            <div style="font-size:14px; color:#666; margin-top:6px; line-height:1.6;"><b style="color:{COLOR_ACCENT};">起源：</b>{origin}</div>
            <div style="font-size:14px; color:#666; margin-top:6px;"><b style="color:{COLOR_ACCENT};">核心特征：</b></div>
            <ul style="font-size:14px; color:#555; margin:4px 0 0 18px; padding:0; line-height:1.6;">{feats}</ul>
            <div style="font-size:14px; color:#666; margin-top:6px; line-height:1.6;"><b style="color:{COLOR_ACCENT};">代表：</b>{reps}</div>
            <div style="font-size:14px; color:#333; margin-top:8px; line-height:1.6; background:{COLOR_BG_ACCENT}; padding:8px 10px; border-radius:4px;"><b style="color:{COLOR_ACCENT};">今天的小练习：</b>{practice}</div>
        </div>'''
    return html


# 特殊板块用各自的版式，其余用通用的六段式卡片
SECTION_RENDERERS = {
    "youtube": render_youtube_cards,
    "history": render_history_cards,
    "poem": render_poem_card,
    "style": render_style_card,
}


def render_action_advice(text):
    directions, not_worth, recommend = [], "", ""
    mode = None
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if "可能的方向" in line:
            mode = "directions"
            continue
        if "不建议投入" in line:
            mode = "not_worth"
            continue
        if "今日推荐行动" in line:
            mode = "recommend"
            continue
        if mode == "directions":
            parts = _split_line(line, 2)
            if parts and parts[0]:
                directions.append((parts[0], parts[1]))
        elif mode == "not_worth":
            not_worth += line
        elif mode == "recommend":
            recommend += line

    if not directions and not not_worth and not recommend:
        return ""

    directions_html = "".join([
        f'''<div style="padding:10px 0; border-bottom:1px solid rgba(255,255,255,0.15);">
            <div style="font-size:15px; font-weight:600; color:#fff;">🧭 {name}</div>
            <div style="font-size:14px; color:#cfd8e3; margin-top:4px; line-height:1.6;">{detail}</div>
        </div>'''
        for name, detail in directions
    ])

    not_worth_html = f'''<div style="margin-top:14px; padding:12px 14px; background:rgba(255,255,255,0.08); border-radius:6px;">
        <div style="font-size:13px; color:#a8b4c2;">⚠️ 不建议现在投入</div>
        <div style="font-size:14px; color:#e5eaf0; margin-top:4px; line-height:1.6;">{not_worth}</div>
    </div>''' if not_worth else ""

    recommend_html = f'''<div style="margin-top:14px; padding:14px 16px; background:{COLOR_ACCENT}; border-radius:6px;">
        <div style="font-size:13px; color:#dce6f0;">✅ 今日推荐行动</div>
        <div style="font-size:15.5px; color:#fff; font-weight:600; margin-top:4px; line-height:1.6;">{recommend}</div>
    </div>''' if recommend else ""

    return f'''<h2 style="font-size:18px; color:{COLOR_DARK}; margin-top:26px;">🎯 今日行动建议</h2>
    <div style="background:{COLOR_DARK}; border-radius:8px; padding:18px 20px;">
        {directions_html}
        {not_worth_html}
        {recommend_html}
    </div>'''


def render_greeting(text):
    """解析AI生成的开头问候+结尾道别"""
    opening, closing = "", ""
    mode = None
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "开头" in line and line.startswith("【"):
            mode = "opening"
            continue
        if "结尾" in line and line.startswith("【"):
            mode = "closing"
            continue
        if mode == "opening":
            opening += line
        elif mode == "closing":
            closing += line
    return opening, closing


def build_html(date, slot, sections, section_idx=None, continuation=False):
    """section_idx：只渲染 layout.LAYOUT 里这些位置的板块（None=全部）；
    continuation=True：生成"续篇"邮件，只含这些板块，用于内容太多、一封装不下
    （Gmail 超过约 102KB 会截断）时拆成两封。"""
    if section_idx is None:
        section_idx = range(len(layout.LAYOUT))
    overview_html, todo = render_overview(sections["overview"])
    jwc_html = render_jwc_cards(sections["jwc"])

    # 按布局依次渲染各板块；板块里每一组内容用各自的版式（新闻卡片 / 频道 / 历史 / 诗词 / 风格小课）
    extra_sections_html = ""
    for idx in section_idx:
        title, groups = layout.LAYOUT[idx]
        content = "".join(
            SECTION_RENDERERS.get(key, render_content_cards)(sections.get(key, ""))
            for key, _ in groups
        )
        if content:
            extra_sections_html += f'''
            <h2 style="font-size:18px; color:{COLOR_DARK}; margin-top:26px;">{title}</h2>
            {content}
            '''

    if continuation:
        return f"""
    <div style="max-width:620px; margin:0 auto; font-family:-apple-system,'PingFang SC',sans-serif;">
        <h1 style="font-size:23px; color:{COLOR_DARK}; margin-bottom:6px;">📋 每日简报（续）</h1>
        <div style="font-size:15px; color:{COLOR_MUTED}; margin-bottom:8px;">{date} · 内容较多，后半部分在这一封</div>
        {extra_sections_html}
    </div>
    """

    action_html = render_action_advice(sections.get("action", ""))
    opening, closing = render_greeting(sections.get("greeting", ""))

    opening_html = f'''<div style="font-size:15px; color:{COLOR_DARK}; background:{COLOR_BG_ACCENT}; border-radius:8px; padding:14px 18px; margin-bottom:16px; line-height:1.6;">{SLOT_GREETING.get(slot, "你好")}👋 {opening}</div>''' if opening else ""
    closing_in_footer = f'''<div style="font-size:15px; font-weight:600; color:{COLOR_DARK}; margin-top:12px; border-top:1px solid rgba(63,107,143,0.25); padding-top:12px; line-height:1.6;">💌 {closing}</div>''' if closing else ""

    has_jwc = bool(jwc_html.strip())
    jwc_block = f'''<h2 style="font-size:18px; color:{COLOR_DARK}; margin-top:8px;">学校教务</h2>
        {jwc_html}''' if has_jwc else ""

    tab_bar = f'''
    <div style="display:table; width:100%; margin-bottom:18px; border-bottom:2px solid #eee;">
        <div style="display:table-cell; width:50%; text-align:center; padding:12px 0; background:{COLOR_DARK}; color:#fff; font-size:16px; font-weight:600; border-radius:6px 0 0 0;">🏫 学校教务</div>
        <div style="display:table-cell; width:50%; text-align:center; padding:12px 0; background:{COLOR_ACCENT}; color:#fff; font-size:16px; font-weight:600; border-radius:0 6px 0 0;">💻 科技资讯</div>
    </div>
    '''
    if not has_jwc:
        tab_bar = ""

    footer = f'''<div style="background:{COLOR_BG_ACCENT}; border-radius:8px; padding:18px 22px; margin-top:30px; text-align:center;">
        <div style="font-size:14px; color:{COLOR_MUTED};">今日最该做的一件事</div>
        <div style="font-size:17px; font-weight:600; color:{COLOR_DARK}; margin-top:6px;">✅ {todo}</div>
        {closing_in_footer}
    </div>'''

    return f"""
    <div style="max-width:620px; margin:0 auto; font-family:-apple-system,'PingFang SC',sans-serif;">
        <h1 style="font-size:23px; color:{COLOR_DARK}; margin-bottom:6px;">📋 每日简报</h1>
        <div style="font-size:15px; color:{COLOR_MUTED}; margin-bottom:18px;">{date}</div>

        {opening_html}

        {overview_html}

        {tab_bar}

        {jwc_block}

        {extra_sections_html}

        {action_html}

        {footer}
    </div>
    """


def send_brief_email():
    try:
        with open("daily_brief.md", "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError as e:
        print("❌ 没找到 daily_brief.md，无法发送邮件")
        raise RuntimeError("daily_brief.md 不存在") from e

    date, slot, sections = parse_brief(content)
    slot_tag = SLOT_TAG.get(slot, "简报")

    # 压缩缩进和换行；Gmail 遇到超过约 102KB 的邮件会截断，所以超过 MAX_EMAIL_KB 就拆成两封
    def mini(html):
        return re.sub(r"\n\s*", "", html)

    all_idx = list(range(len(layout.LAYOUT)))
    full = mini(build_html(date, slot, sections))
    print(f"邮件大小：{len(full.encode('utf-8')) / 1024:.1f} KB（上限 {MAX_EMAIL_KB} KB）")
    emails = [(f"每日简报·{slot_tag} {date}", full)]
    if len(full.encode("utf-8")) > MAX_EMAIL_KB * 1024:
        # 从多到少尝试：第一封放尽可能多的板块，剩下的放进第二封
        for k in range(len(all_idx) - 1, 0, -1):
            part1 = mini(build_html(date, slot, sections, section_idx=all_idx[:k]))
            if len(part1.encode("utf-8")) <= MAX_EMAIL_KB * 1024 or k == 1:
                part2 = mini(build_html(date, slot, sections, section_idx=all_idx[k:], continuation=True))
                emails = [(f"每日简报·{slot_tag} {date}（1/2）", part1),
                          (f"每日简报·{slot_tag} {date}（2/2）", part2)]
                print(f"内容较多，拆成两封发送：{len(part1.encode('utf-8')) / 1024:.1f} KB + {len(part2.encode('utf-8')) / 1024:.1f} KB")
                break

    try:
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=60)
        else:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=60)
            server.starttls()
        with server:
            server.login(QQ_EMAIL, QQ_AUTH_CODE)
            for subject, html_content in emails:
                msg = MIMEText(html_content, "html", "utf-8")
                msg["From"] = QQ_EMAIL
                msg["To"] = SEND_TO
                msg["Subject"] = Header(subject, "utf-8")
                server.sendmail(QQ_EMAIL, [SEND_TO], msg.as_string())

        print(f"✅ 简报已发送到 {SEND_TO}")

    except Exception as e:
        print(f"❌ 发送失败：{e}")
        raise


if __name__ == "__main__":
    send_brief_email()
