"""Public archive of sent briefs: browse issue by issue, or search stories by keyword and topic.

Pages are rendered on the server so they work without JavaScript and every issue has a stable link.
Only issues whose send time has passed are shown, so a frozen snapshot never leaks before its mail."""

import json
import re
from datetime import date, datetime
from html import escape
from urllib.parse import urlencode

from subscription.feed import build_feed
from subscription.insight import LABEL_KEYS, TOPIC_MARK
from subscription.store import BEIJING, EDITIONS, SECTIONS, utcnow


ISSUES_PER_PAGE = 10
RESULTS_PER_PAGE = 30
MAX_QUERY = 40
MAX_TERMS = 5
PREVIEW_STORIES = 3
INSIGHTS_PER_PAGE = 20
WEEKDAYS = "一二三四五六日"
SEARCH_FIELDS = ("label", "title", "summary", "importance", "tags", "source")


def sent_at(issue_date, edition):
    day = date.fromisoformat(issue_date)
    return datetime(day.year, day.month, day.day, EDITIONS[edition][0], tzinfo=BEIJING)


def issue_heading(issue):
    day = date.fromisoformat(issue["date"])
    return f"{day.year}年{day.month}月{day.day}日 周{WEEKDAYS[day.weekday()]} · {issue['edition_label']}"


def issue_path(issue):
    return f"/archive/{issue['date']}/{issue['edition']}"


def search_terms(query):
    return [term.lower() for term in query.split()][:MAX_TERMS]


def matches(story, terms):
    text = " ".join(story[field] for field in SEARCH_FIELDS).lower()
    return all(term in text for term in terms)


def search(issues, terms, topic):
    """Matching stories, newest issue first. Every term must appear in the story or its topic name."""
    return [(issue, story) for issue in issues for story in issue["stories"]
            if (not topic or story["topic"] == topic) and (not terms or matches(story, terms))]


def mark(text, terms):
    """Escape text and highlight each search term."""
    if not terms or not text:
        return escape(text)
    pattern = re.compile("|".join(re.escape(term) for term in sorted(terms, key=len, reverse=True)), re.IGNORECASE)
    parts, last = [], 0
    for found in pattern.finditer(text):
        parts.append(escape(text[last:found.start()]))
        parts.append(f"<mark>{escape(found.group())}</mark>")
        last = found.end()
    parts.append(escape(text[last:]))
    return "".join(parts)


def page_number(value):
    try:
        return max(1, min(int(value), 10000))
    except (TypeError, ValueError):
        return 1


class Archive:
    def __init__(self, store):
        self.store = store
        self.cache = (None, [])

    def issues(self, now=None):
        """Sent issues, newest first. Snapshots are immutable, so the parsed copy only changes when one is added."""
        version = self.store.archive_version()
        cached_version, parsed = self.cache
        if version != cached_version:
            parsed = []
            for row in self.store.archive_issues():
                for issue in build_feed([row], [], "all"):
                    issue["insight"] = row["insight"]
                    issue["insight_topics"] = json.loads(row["insight_topics"]) if row["insight_topics"] else []
                    parsed.append(issue)
            self.cache = (version, parsed)
        now = now or utcnow()
        return [issue for issue in parsed if sent_at(issue["date"], issue["edition"]) <= now]

    def find(self, issue_date, edition, now=None):
        """The issue with its newer and older neighbours, or None while it is unknown or not yet sent."""
        issues = self.issues(now)
        for index, issue in enumerate(issues):
            if issue["date"] == issue_date and issue["edition"] == edition:
                newer = issues[index - 1] if index else None
                older = issues[index + 1] if index + 1 < len(issues) else None
                return issue, newer, older
        return None


def story_card(story, terms=(), context=None):
    """One story in the same layout as the reader's home page. Context links a search hit to its issue."""
    parts = [f'<article class="story" id="{escape(story["topic"])}">']
    if context:
        parts.append(f'<p class="archive-hit-meta"><a href="{escape(issue_path(context))}#{escape(story["topic"])}">'
                     f'{escape(issue_heading(context))}</a></p>')
    parts.append(f'<span class="topic-label">{mark(story["label"], terms)}</span>')
    parts.append(f'<h3>{mark(story["title"], terms)}</h3>')
    if story["summary"]:
        parts.append(f'<p>{mark(story["summary"], terms)}</p>')
    if story["importance"]:
        parts.append(f'<p class="story-why"><strong>为什么重要 </strong>{mark(story["importance"], terms)}</p>')
    source = story["source"] or "原始来源"
    if story["url"]:
        parts.append(f'<a href="{escape(story["url"], quote=True)}" target="_blank" rel="noopener noreferrer">'
                     f'阅读来源 · {mark(source, terms)} ↗</a>')
    elif story["source"]:
        parts.append(f'<span class="source-label">{mark(story["source"], terms)}</span>')
    parts.append("</article>")
    return "".join(parts)


def search_form(query, topic):
    options = ['<option value="">全部主题</option>'] + [
        f'<option value="{key}"{" selected" if key == topic else ""}>{escape(label)}</option>'
        for key, label in SECTIONS.items()]
    return (
        '<form class="archive-search" role="search" action="/archive" method="get">'
        '<label class="visually-hidden" for="archive-q">关键词</label>'
        f'<input id="archive-q" name="q" type="search" value="{escape(query, quote=True)}" maxlength="{MAX_QUERY}" '
        'placeholder="搜关键词，如 AI、黄金、裁员" enterkeyhint="search" />'
        '<label class="visually-hidden" for="archive-topic">主题</label>'
        f'<select id="archive-topic" name="topic">{"".join(options)}</select>'
        '<button class="button primary" type="submit">搜索</button>'
        '</form>')


def pager(page, pages, query, topic):
    if pages <= 1:
        return ""

    def link(number, text, rel):
        values = {key: value for key, value in (("q", query), ("topic", topic)) if value}
        if number > 1:
            values["page"] = number
        href = "/archive" + ("?" + urlencode(values) if values else "")
        return f'<a href="{escape(href, quote=True)}" rel="{rel}">{text}</a>'

    newer = link(page - 1, "← 较新", "prev") if page > 1 else "<span></span>"
    older = link(page + 1, "较早 →", "next") if page < pages else "<span></span>"
    return f'<nav class="archive-pager" aria-label="翻页">{newer}<span>第 {page} / {pages} 页</span>{older}</nav>'


def issue_summary(issue):
    titles = "".join(f'<li><span class="topic-label">{escape(story["label"])}</span>{escape(story["title"])}</li>'
                     for story in issue["stories"][:PREVIEW_STORIES])
    total = len(issue["stories"])
    return (f'<section class="archive-issue"><h2><a href="{escape(issue_path(issue))}">{escape(issue_heading(issue))}</a></h2>'
            f'<ul>{titles}</ul><a class="archive-more" href="{escape(issue_path(issue))}">查看本期全部 {total} 条 →</a></section>')


def index_page(issues, query="", topic="", page=1):
    query = " ".join(query.split())[:MAX_QUERY]
    topic = topic if topic in SECTIONS else ""
    terms = search_terms(query)
    body = ['<article class="archive">', '<p class="panel-context">往期简报</p>',
            '<h1>错过的简报，都在这里。</h1>',
            '<p class="archive-lead">每天早晚两期的全部内容按日期保存。输入关键词或选择主题，就能找回几天前看过的那条新闻。</p>',
            search_form(query, topic)]
    if not issues:
        body.append('<p class="form-note">第一期简报发出后，会在这里出现。</p>')
    elif terms or topic:
        results = search(issues, terms, topic)
        pages = max(1, -(-len(results) // RESULTS_PER_PAGE))
        page = min(page, pages)
        scope = "、".join(filter(None, [f"“{query}”" if query else "", SECTIONS.get(topic, "")]))
        if results:
            body.append(f'<p class="archive-count" aria-live="polite">{escape(scope)} · 找到 {len(results)} 条</p>')
            start = (page - 1) * RESULTS_PER_PAGE
            body += [story_card(story, terms, issue) for issue, story in results[start:start + RESULTS_PER_PAGE]]
        else:
            body.append(f'<p class="archive-count" aria-live="polite">没有找到{escape(scope)}相关的简报。换个说法试试，比如更短的关键词，或者选“全部主题”。</p>')
        body.append(pager(page, pages, query, topic))
        body.append('<p class="account-actions"><a href="/archive">清空搜索，按日期浏览</a></p>')
    else:
        pages = max(1, -(-len(issues) // ISSUES_PER_PAGE))
        page = min(page, pages)
        start = (page - 1) * ISSUES_PER_PAGE
        body += [issue_summary(issue) for issue in issues[start:start + ISSUES_PER_PAGE]]
        body.append(pager(page, pages, query, topic))
    body.append(subscribe_note())
    body.append("</article>")
    title = f"搜索“{query}” · 往期简报" if query else (f"{SECTIONS[topic]} · 往期简报" if topic else "往期简报")
    return layout(title, "Daily Brief Radar 往期简报存档：按日期翻阅每天早晚两期，或按关键词、主题搜索历史新闻。", "".join(body))


def issue_page(issue, newer, older):
    heading = issue_heading(issue)
    neighbours = [f'<a href="{escape(issue_path(newer))}" rel="next">← 较新 · {escape(issue_heading(newer))}</a>' if newer else "<span></span>",
                  f'<a href="{escape(issue_path(older))}" rel="prev">较早 · {escape(issue_heading(older))} →</a>' if older else "<span></span>"]
    body = ['<article class="archive">', '<p class="panel-context"><a href="/archive">往期简报</a></p>',
            f'<h1>{escape(heading)}</h1>',
            f'<p class="archive-lead">本期共 {len(issue["stories"])} 条，按主题排列。AI 辅助摘要，请以原始来源为准。</p>',
            insight_block(issue, heading="本期一句话洞察"),
            *[story_card(story) for story in issue["stories"]],
            f'<nav class="archive-pager" aria-label="相邻期号">{"".join(neighbours)}</nav>',
            subscribe_note(), "</article>"]
    titles = "；".join(story["title"] for story in issue["stories"][:PREVIEW_STORIES])
    return layout(f"{heading} · 往期简报", f"Daily Brief Radar {heading}：{titles}"[:160], "".join(body))


def missing_page():
    body = ('<article class="archive"><p class="panel-context">往期简报</p><h1>没有找到这一期。</h1>'
            '<p class="archive-lead">这一期可能还没发出，或者地址有误。</p>'
            '<p class="account-actions"><a href="/archive">去往期简报</a></p></article>')
    return layout("没有找到这一期 · 往期简报", "Daily Brief Radar 往期简报", body)


def subscribe_note():
    return ('<aside class="archive-subscribe"><p>想每天早上 8 点、晚上 8 点直接在邮箱里看到？</p>'
            '<a class="button primary" href="/#subscribe">免费订阅</a></aside>')


def layout(title, description, body, current="/archive"):
    marker = ' aria-current="page"'
    nav = "".join(
        f'<a href="{href}"{marker if href == current else ""}>{label}</a>'
        for href, label in (("/#subscribe", "免费订阅"), ("/archive", "往期"), ("/insight", "一句话洞察"), ("/manage", "我的主页")))
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="description" content="{escape(description, quote=True)}" />
    <meta name="theme-color" content="#f4f1e8" />
    <title>{escape(title)} · Daily Brief Radar</title>
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg" />
    <link rel="stylesheet" href="/static/style.css" />
  </head>
  <body>
    <a class="skip-link" href="#main">跳到主要内容</a>
    <div class="site-shell">
      <header class="site-header">
        <a class="wordmark" href="/" aria-label="Daily Brief Radar 首页"><span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>DAILY BRIEF RADAR</a>
        <nav aria-label="主导航">{nav}</nav>
      </header>
      <main id="main">{body}</main>
      <footer class="site-footer">
        <div class="foot-top">
          <span class="foot-brand">DAILY BRIEF RADAR</span>
          <div class="foot-links"><a href="/archive">往期简报</a><a href="/insight">一句话洞察</a><a href="/support">打赏</a><a href="/privacy">隐私政策</a><a href="/terms">服务条款</a></div>
        </div>
        <div class="foot-bottom">
          <p>免费订阅 · 主动确认 · 随时退订</p>
          <p>AI 辅助摘要，请以原始来源为准。</p>
        </div>
      </footer>
    </div>
  </body>
</html>
"""


def insight_sentence(issue):
    """The sentence with each 【主题】 linked to that topic's story in the issue."""
    parts, last = [], 0
    present = {story["topic"] for story in issue["stories"]}
    for found in TOPIC_MARK.finditer(issue["insight"]):
        parts.append(escape(issue["insight"][last:found.start()]))
        key = LABEL_KEYS.get(found.group(1))
        if key in present:
            parts.append(f'<a href="{escape(issue_path(issue))}#{key}">【{escape(found.group(1))}】</a>')
        else:
            parts.append(escape(found.group()))
        last = found.end()
    parts.append(escape(issue["insight"][last:]))
    return "".join(parts)


def insight_block(issue, heading="一句话洞察"):
    if not issue.get("insight"):
        return ""
    return (f'<aside class="insight-block"><p class="insight-kicker">{escape(heading)}</p>'
            f'<p class="insight-sentence">{insight_sentence(issue)}</p>'
            '<p class="insight-note">AI 根据本期新闻写出的跨主题联想，只是可能的关联，不构成任何投资或职业建议。</p></aside>')


def insight_page(issues, page=1):
    with_insight = [issue for issue in issues if issue.get("insight")]
    body = ['<article class="archive">', '<p class="panel-context">一句话洞察</p>',
            '<h1>新闻不缺，缺的是新闻之间的联系。</h1>',
            '<p class="archive-lead">每期简报冻结后，AI 读完全部主题的新闻，把不同主题之间的联系写成一句话：'
            '一件事可能怎样牵动另一件事。点【主题】可以跳到对应的那条新闻。</p>']
    if not with_insight:
        body.append('<p class="form-note">第一句洞察会随下一期简报一起发布。</p>')
    else:
        pages = max(1, -(-(len(with_insight) - 1) // INSIGHTS_PER_PAGE))
        page = min(page, pages)
        if page == 1:
            latest = with_insight[0]
            body.append(f'<section class="insight-latest"><h2><a href="{escape(issue_path(latest))}">{escape(issue_heading(latest))}</a></h2>')
            body.append(insight_block(latest, heading="最新一期"))
            linked = [story for key in latest["insight_topics"] for story in latest["stories"] if story["topic"] == key]
            if linked:
                body.append('<h3 class="insight-linked">这句话串起的新闻</h3>')
                body += [story_card(story) for story in linked]
            body.append(f'<a class="archive-more" href="{escape(issue_path(latest))}">看本期全部 {len(latest["stories"])} 条新闻 →</a></section>')
        start = 1 + (page - 1) * INSIGHTS_PER_PAGE
        older = with_insight[start:start + INSIGHTS_PER_PAGE]
        if older:
            body.append('<h2 class="insight-history-title">往期洞察</h2><ol class="insight-history">')
            body += [f'<li><a class="insight-date" href="{escape(issue_path(issue))}">{escape(issue_heading(issue))}</a>'
                     f'<p>{insight_sentence(issue)}</p></li>' for issue in older]
            body.append("</ol>")
        if pages > 1:
            newer = f'<a href="/insight{"?page=" + str(page - 1) if page > 2 else ""}" rel="prev">← 较新</a>' if page > 1 else "<span></span>"
            later = f'<a href="/insight?page={page + 1}" rel="next">较早 →</a>' if page < pages else "<span></span>"
            body.append(f'<nav class="archive-pager" aria-label="翻页">{newer}<span>第 {page} / {pages} 页</span>{later}</nav>')
    body += [subscribe_note(), "</article>"]
    return layout("一句话洞察", "Daily Brief Radar 一句话洞察：AI 把每期简报里不同主题的新闻串成一句话，看见新闻背后的联系。",
                  "".join(body), current="/insight")
