"""Render a frozen section snapshot through the compiled MJML template."""

from html import escape
from pathlib import Path
from string import Template
from urllib.parse import quote, urlsplit

from .store import EDITIONS, SECTIONS


def safe_link(value):
    parsed = urlsplit(value)
    return value if parsed.scheme in ("http", "https") and parsed.netloc else ""


def links(base_url, token):
    value = quote(token, safe="")
    return f"{base_url}/manage#{value}", f"{base_url}/unsubscribe#{value}"


def parse_card(text):
    """Split one frozen story line into its six fields, or None for free text."""
    line = next((line.strip() for line in text.splitlines() if "|||" in line), "")
    if not line:
        return None
    parts = (line.split("|||") + [""] * 6)[:6]
    source, title, url, tags, summary, importance = [part.strip() for part in parts]
    return {"source": source, "title": title, "url": safe_link(url), "tags": tags,
            "summary": summary, "importance": importance}


def card(text):
    story = parse_card(text)
    if story is None:
        return f"<p>{escape(text)}</p>", text, "以原始来源的后续更新为准。"
    source, title, url = story["source"], story["title"], story["url"]
    tags, summary, importance = story["tags"], story["summary"], story["importance"]
    source_link = f'<a href="{escape(url, quote=True)}" style="color:#285440">阅读来源 · {escape(source)}</a>' if url else escape(source)
    html = (f'<p style="font-size:12px;color:#6a776c">{escape(tags)}</p>'
            f'<h3 style="font-size:20px;margin:8px 0">{escape(title)}</h3>'
            f'<p>{escape(summary)}</p><p><strong>为什么重要</strong><br>{escape(importance)}</p>'
            f'<p style="font-size:12px">{source_link}</p>')
    return html, f"{title}\n{summary}\n为什么重要\n{importance}\n{source} {url}", importance


def render_issue(issue_date, sections, selected, base_url, token, grace=False, edition=None):
    available = [key for key in SECTIONS if key in sections]
    selected = [key for key in available if key in selected]
    ordered = selected + [key for key in available if key not in selected]
    # Followed topics are always expanded. Without a usable selection the issue keeps its general layout.
    featured = len(selected) if selected else 3
    if selected:
        label = "你关注的主题"
    else:
        label = "本期必知" if len(ordered) < 3 else "本期三条必知"
    content = [f'<h2 style="font-size:15px;letter-spacing:1px">{label}</h2>']
    heading = issue_date + (" · " + EDITIONS[edition][1] if edition else "")
    text_parts = [f"Daily Brief Radar · {heading}", label]
    observation = ""
    for index, key in enumerate(ordered):
        if index == featured:
            content.append('<details><summary style="font-size:15px;border-top:1px solid #dce3d8;padding-top:24px">其他值得一扫</summary>')
            text_parts.append("其他值得一扫")
        html, text, hint = card(sections[key])
        content.append(f'<div id="section-{key}" style="margin:24px 0"><p style="color:#66736a;font-size:12px">{index+1:02d} / {SECTIONS[key]}</p>{html}</div>')
        text_parts.append(text)
        if not observation:
            observation = hint
    if len(ordered) > featured:
        content.append("</details>")
    banner = "你的订阅已到期，目前处于三天宽限期。请续费以继续收到简报。" if grace else ""
    manage_url, unsubscribe_url = links(base_url, token)
    template = Template(Path(__file__).with_name("email.html").read_text(encoding="utf-8"))
    html = template.substitute(issue_date=escape(heading), content="".join(content),
                               banner=escape(banner), observation="观察点 · " + escape(observation),
                               manage_url=escape(manage_url, quote=True), unsubscribe_url=escape(unsubscribe_url, quote=True))
    text_parts += [banner, "观察点 · " + observation, "管理偏好或续费 " + manage_url, "退订 " + unsubscribe_url]
    return html, "\n\n".join(text_parts)


def render_confirmation(confirmation_url, manage_url, unsubscribe_url, sender=""):
    """Confirmation mail. Until the reader confirms nothing else is sent, so the action must be impossible to miss."""
    address = sender.split("<")[-1].rstrip("> ").strip() if sender else ""
    sender_note = f"发件地址 {escape(address)}" if address else "发件人 Daily Brief Radar"
    font = "font-family:Arial,'PingFang SC','Microsoft YaHei',sans-serif"
    html = (
        f'<html lang="zh"><body style="margin:0;padding:0;background:#f4f3ec;{font}">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0">还差一步：点击确认，才会开始收到每日简报。</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f3ec"><tr><td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#ffffff;border:3px solid #285440;border-radius:8px">'
        f'<tr><td style="background:#285440;color:#ffffff;padding:18px 28px;font-size:14px;letter-spacing:2px;{font}">DAILY BRIEF RADAR · 订阅确认</td></tr>'
        f'<tr><td style="padding:28px 28px 8px;color:#233d35;{font}">'
        '<p style="margin:0 0 12px;font-size:26px;font-weight:700;line-height:1.4">还差最后一步，订阅才会生效</p>'
        '<p style="margin:0;font-size:16px;line-height:1.8">你刚刚用这个邮箱申请了每日简报。<strong style="color:#b3261e">不点击下面的“确认订阅”，我们不会给你发送任何简报。</strong></p></td></tr>'
        f'<tr><td align="center" style="padding:24px 28px"><a href="{escape(confirmation_url, quote=True)}" '
        f'style="display:block;background:#1f7a45;color:#ffffff;font-size:22px;font-weight:700;text-decoration:none;padding:18px 24px;border-radius:8px;{font}">'
        '确认订阅</a></td></tr>'
        f'<tr><td style="padding:0 28px 20px;color:#233d35;font-size:15px;line-height:1.8;{font}">'
        '<p style="margin:0 0 8px"><strong>点击后会打开网站，请在页面上再点一次“确认邮箱并开始试读”，才算完成。</strong></p>'
        '<p style="margin:0">北京时间每天 08:00 早报、20:00 晚报，确认后开始三期免费试读。此链接两天内有效。</p></td></tr>'
        f'<tr><td style="padding:0 28px 24px;{font}"><div style="background:#fff4d6;border:1px solid #e0b83a;border-radius:6px;padding:14px 16px;color:#5a4300;font-size:14px;line-height:1.8">'
        '<strong>请留意垃圾邮件</strong><br>'
        '如果这封邮件在“垃圾邮件”里，请点击“不是垃圾邮件”，并把发件人加入联系人或白名单，之后的每日简报才会进入收件箱。<br>'
        f'{sender_note}</div></td></tr>'
        f'<tr><td style="padding:16px 28px 24px;border-top:1px solid #dce3d8;color:#66736a;font-size:12px;line-height:1.8;{font}">'
        '如果不是你本人申请，请忽略此邮件，不会有任何订阅。<br>'
        f'按钮无法点击时，复制此地址到浏览器：<br><span style="word-break:break-all">{escape(confirmation_url)}</span><br>'
        f'<a href="{escape(manage_url, quote=True)}" style="color:#285440">管理偏好</a> · '
        f'<a href="{escape(unsubscribe_url, quote=True)}" style="color:#285440">退订</a></td></tr>'
        '</table></td></tr></table></body></html>')
    text = ("【请点击确认】还差最后一步，订阅才会生效。\n"
            "不点击下面的链接确认订阅，我们不会给你发送任何简报。\n\n"
            f"确认订阅：{confirmation_url}\n\n"
            "点击后会打开网站，请在页面上再点一次“确认邮箱并开始试读”，才算完成。\n"
            "北京时间每天 08:00 早报、20:00 晚报，确认后开始三期免费试读。链接两天内有效。\n\n"
            "请留意垃圾邮件：如果这封邮件在垃圾邮件里，请点“不是垃圾邮件”，并把发件人加入联系人或白名单。\n"
            f"{('发件地址 ' + address) if address else ''}\n\n"
            "如果不是你本人申请，请忽略此邮件。\n"
            f"管理偏好 {manage_url}\n退订 {unsubscribe_url}")
    return html, text


def render_notice(title, body, base_url, token, action="管理订阅或续费"):
    manage_url, unsubscribe_url = links(base_url, token)
    html = (f'<html lang="zh"><body style="font-family:Arial,sans-serif;line-height:1.8;color:#233d35">'
            f'<h1>{escape(title)}</h1><p>{escape(body)}</p>'
            f'<p><a href="{escape(manage_url, quote=True)}">{escape(action)}</a></p>'
            f'<p><a href="{escape(unsubscribe_url, quote=True)}">退订</a></p></body></html>')
    return html, f"{title}\n{body}\n{action} {manage_url}\n退订 {unsubscribe_url}"
