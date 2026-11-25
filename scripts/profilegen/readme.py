"""Markdown for the README sections, and marker-based replacement."""
from __future__ import annotations

import re
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .aggregate import ActivityItem

_MD_SPECIAL = re.compile(r"([\\`*_\[\]{}()#+!|~>])")


def md(text: str, limit: int | None = None) -> str:
    """Escape untrusted text (titles, repo names) for inline Markdown."""
    text = " ".join(text.split())
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return _MD_SPECIAL.sub(r"\\\1", escape(text, quote=False))


def _day(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    return f"{local.day} {local:%b}"


def _span(first: datetime | None, last: datetime, tz: ZoneInfo) -> str:
    """"21 Nov", "18–21 Nov" or "28 Oct – 3 Nov"."""
    if first is None:
        return _day(last, tz)
    a, b = first.astimezone(tz), last.astimezone(tz)
    if a.date() == b.date():
        return _day(last, tz)
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {b:%b}"
    return f"{_day(first, tz)} – {_day(last, tz)}"


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (plural or word + 's')}"


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def format_activity(item: ActivityItem, tz: ZoneInfo, days: int) -> str:
    if item.kind == "private":
        p = item.private
        parts = [f"**{_plural(p['commits'], 'commit')}**"] if p["commits"] else []
        parts += [_plural(p[k], w, pl) for k, w, pl in (
            ("prs", "pull request", None), ("reviews", "review", None), ("issues", "issue", None),
            ("created", "new repository", "new repositories"),
        ) if p.get(k)]
        if parts and p["repos"]:
            text = f"{_join(parts)} across {_plural(p['repos'], 'private repository', 'private repositories')}"
        elif parts:
            text = f"{_join(parts)} in private repositories"
        else:
            text = f"**{_plural(p['other'], 'private contribution')}**"
            p = {**p, "other": 0}
        if p["other"]:
            text += f" (+{p['other']:,} more private contributions)"
        return f"- 🔒 {text} <sub>· {item.account} · last {days} days</sub>"

    repo = f"[{md(item.repo['name'])}]({item.repo['url']})"
    meta = f" <sub>· {item.account} · {_day(item.at, tz)}</sub>"
    title = md(item.title, 72)
    if item.kind == "commits":
        return (f"- 🔨 Pushed **{_plural(item.count, 'commit')}** to {repo}"
                f" <sub>· {item.account} · {_span(item.first, item.at, tz)}</sub>")
    if item.kind == "pr":
        merged = " (merged)" if item.state == "MERGED" else ""
        return f"- 🔀 Opened PR [#{item.number}]({item.url}) in {repo}: {title}{merged}{meta}"
    if item.kind == "review":
        return f"- 👀 Reviewed PR [#{item.number}]({item.url}) in {repo}: {title}{meta}"
    if item.kind == "issue":
        return f"- 🐛 Opened issue [#{item.number}]({item.url}) in {repo}: {title}{meta}"
    if item.kind == "repo":
        return f"- 📦 Created repository {repo}{meta}"
    raise ValueError(f"unknown activity kind: {item.kind}")


def activity_markdown(items: list[ActivityItem], tz: ZoneInfo, days: int) -> str:
    if not items:
        return f"_No activity in the last {days} days._"
    return "\n".join(format_activity(i, tz, days) for i in items)


def blog_markdown(posts: list[dict], tz: ZoneInfo) -> str:
    if not posts:
        return "_No posts yet._"
    lines = []
    for post in posts:
        local = post["published"].astimezone(tz)
        lines.append(f"- [{md(post['title'])}]({post['link']}) <sub>· {local.day} {local:%b %Y}</sub>")
    return "\n".join(lines)


def replace_section(text: str, name: str, body: str) -> tuple[str, bool]:
    """Replace everything between <!-- profile:NAME:start --> and :end -->."""
    start, end = f"<!-- profile:{name}:start -->", f"<!-- profile:{name}:end -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(text):
        return text, False
    return pattern.sub(lambda _: f"{start}\n{body}\n{end}", text, count=1), True
