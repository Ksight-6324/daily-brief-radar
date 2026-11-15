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


def card(text):
    line = next((line.strip() for line in text.splitlines() if "|||" in line), "")
    if not line:
        return f"<p>{escape(text)}</p>", text, "以原始来源的后续更新为准。"
    parts = (line.split("|||") + [""] * 6)[:6]
    source, title, url, tags, summary, importance = [part.strip() for part in parts]
    url = safe_link(url)
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
    label = "本期必知" if len(ordered) < 3 else "本期三条必知"
    content = [f'<h2 style="font-size:15px;letter-spacing:1px">{label}</h2>']
    heading = issue_date + (" · " + EDITIONS[edition][1] if edition else "")
    text_parts = [f"Daily Brief Radar · {heading}", label]
    observation = ""
    for index, key in enumerate(ordered):
        if index == 3:
            content.append('<details><summary style="font-size:15px;border-top:1px solid #dce3d8;padding-top:24px">其他值得一扫</summary>')
            text_parts.append("其他值得一扫")
        html, text, hint = card(sections[key])
        content.append(f'<div id="section-{key}" style="margin:24px 0"><p style="color:#66736a;font-size:12px">{index+1:02d} / {SECTIONS[key]}</p>{html}</div>')
        text_parts.append(text)
        if not observation:
            observation = hint
    if len(ordered) > 3:
        content.append("</details>")
    banner = "你的订阅已到期，目前处于三天宽限期。请续费以继续收到简报。" if grace else ""
    manage_url, unsubscribe_url = links(base_url, token)
    template = Template(Path(__file__).with_name("email.html").read_text(encoding="utf-8"))
    html = template.substitute(issue_date=escape(heading), content="".join(content),
                               banner=escape(banner), observation="观察点 · " + escape(observation),
                               manage_url=escape(manage_url, quote=True), unsubscribe_url=escape(unsubscribe_url, quote=True))
    text_parts += [banner, "观察点 · " + observation, "管理偏好或续费 " + manage_url, "退订 " + unsubscribe_url]
    return html, "\n\n".join(text_parts)


def render_notice(title, body, base_url, token):
    manage_url, unsubscribe_url = links(base_url, token)
    html = (f'<html lang="zh"><body style="font-family:Arial,sans-serif;line-height:1.8;color:#233d35">'
            f'<h1>{escape(title)}</h1><p>{escape(body)}</p>'
            f'<p><a href="{escape(manage_url, quote=True)}">管理订阅或续费</a></p>'
            f'<p><a href="{escape(unsubscribe_url, quote=True)}">退订</a></p></body></html>')
    return html, f"{title}\n{body}\n管理订阅或续费 {manage_url}\n退订 {unsubscribe_url}"
