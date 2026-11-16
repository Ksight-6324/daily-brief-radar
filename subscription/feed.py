"""Read-only view of frozen issues for a subscriber's personal home page."""

import json

from .render import parse_card
from .store import EDITIONS, SECTIONS


def build_feed(issues, followed, scope="followed"):
    """Newest issues first. Followed topics lead, and scope=followed hides the rest.

    A reader with no followed topics gets the general edition for either scope."""
    followed = [key for key in SECTIONS if key in followed]
    only_followed = scope == "followed" and bool(followed)
    feed = []
    for issue in issues:
        sections = json.loads(issue["sections"])
        available = [key for key in SECTIONS if key in sections]
        ordered = [key for key in available if key in followed]
        if not only_followed:
            ordered += [key for key in available if key not in followed]
        stories = []
        for key in ordered:
            fields = parse_card(sections[key]) or {"source": "", "title": sections[key].strip()[:120], "url": "",
                                                    "tags": "", "summary": "", "importance": ""}
            stories.append({"topic": key, "label": SECTIONS[key], "followed": key in followed, **fields})
        if stories:
            feed.append({"date": issue["issue_date"], "edition": issue["edition"],
                         "edition_label": EDITIONS[issue["edition"]][1], "stories": stories})
    return feed
