"""One-sentence cross-topic insight: after an issue is frozen, the AI links stories from different topics.

The sentence may only use facts from the frozen stories, must name every topic it links as 【主题名】, and
is stored once beside the issue. When no honest link exists the issue simply has no insight."""

import json
import re
import sys
from pathlib import Path

from .render import parse_card
from .store import SECTIONS


MIN_TOPICS = 3
MAX_LENGTH = 200
TOPIC_MARK = re.compile(r"【([^【】]{1,8})】")
LABEL_KEYS = {label: key for key, label in SECTIONS.items()}


def stories(sections):
    """(key, label, title, summary) for each topic in display order."""
    result = []
    for key in SECTIONS:
        if key not in sections:
            continue
        card = parse_card(sections[key])
        title, summary = (card["title"], card["summary"]) if card else (sections[key].strip()[:120], "")
        if title:
            result.append((key, SECTIONS[key], title, summary))
    return result


def build_prompt(items, rules=""):
    lines = "\n".join(f"【{label}】{title}：{summary}" if summary else f"【{label}】{title}"
                      for _, label, title, summary in items)
    return (
        "你是一份中文每日新闻简报的主编。下面是本期各主题的新闻，每行一条，格式为【主题】标题：摘要。\n\n"
        f"{lines}\n\n"
        "请写一句“一句话洞察”：从中找出两到四个不同主题的新闻之间真实存在的联系，说清一件事可能怎样影响另一件事，"
        "让读者看到新闻背后的关联，而不是把新闻再罗列一遍。\n"
        "要求：\n"
        f"1. 只写一句话，60 到 150 个汉字，不超过 {MAX_LENGTH} 字。\n"
        "2. 每提到一个主题，用【主题名】标出，主题名必须与上面列表中的完全一致，至少涉及两个不同主题。\n"
        "3. 只依据上面提供的新闻，不要补充材料里没有的事实、数字、公司、人物或股价变化。推测的影响用“可能”“或许”等措辞。"
        "只说明新闻之间的关联，不要对读者提建议、说教或号召读者做什么。\n"
        f"4. {rules}\n"
        "5. 联系必须直接、普通人一看就信，宁缺毋滥。如果这些新闻之间找不到站得住脚的联系，只输出一个字：无。\n"
        "6. 只输出这句话本身，不要标题、引号、序号或解释。"
    )


def validate(text, items):
    """Return (sentence, topic keys) or None when the answer breaks the rules."""
    sentence = " ".join((text or "").split()).strip("“”\"'「」 ")
    if not sentence or sentence in ("无", "无。") or len(sentence) > MAX_LENGTH or len(sentence) < 20:
        return None
    available = {label for _, label, _, _ in items}
    keys = []
    for label in TOPIC_MARK.findall(sentence):
        if label not in available:
            return None
        if LABEL_KEYS[label] not in keys:
            keys.append(LABEL_KEYS[label])
    return (sentence, keys) if len(keys) >= 2 else None


def write_insight(sections, call, rules="", attempts=2):
    """Ask the model, check the answer, and try once more if it breaks the rules. None means no insight."""
    items = stories(sections)
    if len(items) < MIN_TOPICS:
        return None
    prompt = build_prompt(items, rules)
    for _ in range(attempts):
        found = validate(call(prompt), items)
        if found:
            return found
    return None


def core_model():
    """The project's configured model (DeepSeek or LLM_API_KEY) and its writing rules."""
    core_path = str(Path(__file__).resolve().parents[1] / "core")
    if core_path not in sys.path:
        sys.path.insert(0, core_path)
    import style_rules
    import summarize

    rules = summarize.NO_ADVICE_RULE + style_rules.PLAIN_LANGUAGE_RULE + style_rules.CHINESE_NAMES_RULE
    return (lambda prompt: summarize.call_llm(prompt, max_retries=2)), rules


def ensure_insight(store, issue_id, call=None, rules=None, now=None):
    """Write the insight for a frozen issue unless it already has one. Returns the stored row or None."""
    existing = store.insight(issue_id)
    if existing:
        return existing
    with store.connect() as conn:
        row = conn.execute("SELECT sections FROM daily_issue WHERE id=?", (issue_id,)).fetchone()
    if row is None:
        raise ValueError("内容快照不存在")
    if call is None:
        call, default_rules = core_model()
        rules = default_rules if rules is None else rules
    found = write_insight(json.loads(row["sections"]), call, rules or "")
    if found is None:
        return None
    store.save_insight(issue_id, found[0], found[1], now)
    return store.insight(issue_id)
