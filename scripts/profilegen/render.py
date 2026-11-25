"""Render the combined profile cards as standalone SVGs (light + dark).

The SVGs contain only aggregate numbers and language names — never repository
names — so private and organisation work can be counted safely.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape

from .aggregate import Day, Language, busiest_day, level, level_thresholds, streaks

WIDTH = 840
PAD = 24
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    border: str
    text: str
    muted: str
    track: str
    levels: tuple[str, str, str, str, str]
    accounts: tuple[str, ...]


# GitHub Primer colours, so the cards sit naturally on github.com.
LIGHT = Theme(
    name="light", bg="#ffffff", border="#d1d9e0", text="#1f2328", muted="#59636e", track="#eff2f5",
    levels=("#eff2f5", "#aceebb", "#4ac26b", "#2da44e", "#116329"),
    accounts=("#0969da", "#8250df", "#bf3989", "#bc4c00"),
)
DARK = Theme(
    name="dark", bg="#0d1117", border="#3d444d", text="#f0f6fc", muted="#9198a1", track="#151b23",
    levels=("#151b23", "#033a16", "#196c2e", "#2ea043", "#56d364"),
    accounts=("#4493f8", "#ab7df8", "#f778ba", "#f0883e"),
)
THEMES = (LIGHT, DARK)


@dataclass(frozen=True)
class AccountMeta:
    login: str
    label: str


def _fmt(n: int) -> str:
    return f"{n:,}"


def _plural(n: int, word: str) -> str:
    return f"{_fmt(n)} {word}{'' if n == 1 else 's'}"


def _short_date(d: date) -> str:
    return f"{d.day} {d:%b}"


def _svg(height: int, title: str, desc: str, body: list[str], theme: Theme) -> str:
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{escape(title)}</title>',
        f'<desc id="desc">{escape(desc)}</desc>',
        "<style>"
        f"text{{font-family:{FONT};fill:{theme.text}}}"
        f".m{{fill:{theme.muted}}}.b{{font-weight:600}}"
        "</style>",
        f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="6" '
        f'fill="{theme.bg}" stroke="{theme.border}"/>',
        *body,
        "</svg>",
        "",
    ])


def _split_bar(x: float, y: float, width: float, height: float, parts: list[tuple[float, str]],
               theme: Theme, clip_id: str) -> list[str]:
    """A rounded horizontal bar made of proportional coloured segments."""
    out = [
        f'<clipPath id="{clip_id}"><rect x="{x}" y="{y}" width="{width}" height="{height}" '
        f'rx="{height / 2}"/></clipPath>',
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{height / 2}" fill="{theme.track}"/>',
        f'<g clip-path="url(#{clip_id})">',
    ]
    total = sum(v for v, _ in parts)
    cursor = x
    if total > 0:
        for value, color in parts:
            w = width * value / total
            if w <= 0:
                continue
            out.append(f'<rect x="{cursor:.2f}" y="{y}" width="{w:.2f}" height="{height}" fill="{color}"/>')
            cursor += w
    out.append("</g>")
    return out


def _dot_legend(entries: list[tuple[str, str, str]], x: float, y: float, anchor: str, size: int = 12) -> str:
    """`● label value` entries on one line. entries: (color, label, value)."""
    spans = []
    for i, (color, label, value) in enumerate(entries):
        gap = ' dx="14"' if i else ""
        spans.append(f'<tspan fill="{color}"{gap}>●</tspan>')
        if label:
            spans.append(f'<tspan dx="4" class="b">{escape(label)}</tspan>')
        spans.append(f'<tspan dx="4" class="m">{escape(value)}</tspan>')
    return f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}">{"".join(spans)}</text>'


# ── Contributions calendar ──────────────────────────────────────────────────

CELL, PITCH = 11, 14


def contributions_svg(days: list[Day], accounts: list[AccountMeta], theme: Theme) -> str:
    total = sum(d.total for d in days)
    per_account = {a.login: sum(d.by_account.get(a.login, 0) for d in days) for a in accounts}
    colors = {a.login: theme.accounts[i % len(theme.accounts)] for i, a in enumerate(accounts)}

    body = [
        f'<text x="{PAD}" y="50"><tspan class="b" font-size="28">{_fmt(total)}</tspan>'
        f'<tspan dx="8" font-size="15" class="m">contributions in the last year</tspan></text>',
    ]
    subtitle = " + ".join(f"@{a.login} ({a.label})" for a in accounts)
    body.append(f'<text x="{PAD}" y="74" font-size="12" class="m">{escape(subtitle)}</text>')

    bar_w = 260
    body += _split_bar(WIDTH - PAD - bar_w, 34, bar_w, 8,
                       [(per_account[a.login], colors[a.login]) for a in accounts], theme, "split")
    body.append(_dot_legend(
        [(colors[a.login], a.label, _fmt(per_account[a.login])) for a in accounts],
        WIDTH - PAD, 64, "end"))

    grid_x, grid_y = PAD + 30, 112
    if days:
        thresholds = level_thresholds(days)
        first = days[0].day
        first_sunday = first - timedelta(days=(first.weekday() + 1) % 7)

        # Month labels: where the month changes between columns, skipping a
        # leading partial month that would collide with the next label.
        columns = (days[-1].day - first_sunday).days // 7 + 1
        labels: list[tuple[int, str, int]] = []
        for col in range(columns):
            start = max(first_sunday + timedelta(weeks=col), first)
            if not labels or start.month != labels[-1][2]:
                labels.append((col, f"{start:%b}", start.month))
        if len(labels) > 1 and labels[1][0] - labels[0][0] < 3:
            labels.pop(0)
        for col, text, _ in labels:
            body.append(f'<text x="{grid_x + col * PITCH}" y="{grid_y - 8}" font-size="11" class="m">{text}</text>')
        for row, text in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
            body.append(f'<text x="{PAD}" y="{grid_y + row * PITCH + 9}" font-size="10" class="m">{text}</text>')

        for d in days:
            col = (d.day - first_sunday).days // 7
            row = (d.day.weekday() + 1) % 7
            fill = theme.levels[level(d.total, thresholds)]
            body.append(f'<rect x="{grid_x + col * PITCH}" y="{grid_y + row * PITCH}" '
                        f'width="{CELL}" height="{CELL}" rx="2" fill="{fill}"/>')

    footer_y = grid_y + 7 * PITCH + 22
    current, longest = streaks(days)
    stats = [f"Current streak {_plural(current, 'day')}", f"Longest streak {_plural(longest, 'day')}"]
    best = busiest_day(days)
    if best:
        stats.append(f"Busiest day {_fmt(best.total)} on {_short_date(best.day)}")
    body.append(f'<text x="{PAD}" y="{footer_y}" font-size="12" class="m">{escape("  ·  ".join(stats))}</text>')

    more_x = WIDTH - PAD
    squares_x = more_x - 32 - 5 * PITCH
    body.append(f'<text x="{more_x}" y="{footer_y}" font-size="11" text-anchor="end" class="m">More</text>')
    for i, fill in enumerate(theme.levels):
        body.append(f'<rect x="{squares_x + i * PITCH}" y="{footer_y - 10}" width="{CELL}" height="{CELL}" '
                    f'rx="2" fill="{fill}"/>')
    body.append(f'<text x="{squares_x - 6}" y="{footer_y}" font-size="11" text-anchor="end" class="m">Less</text>')

    desc = (f"{_fmt(total)} contributions in the last year across "
            + ", ".join(f"{a.login} ({a.label}: {_fmt(per_account[a.login])})" for a in accounts)
            + f". Current streak {current} days, longest streak {longest} days.")
    return _svg(footer_y + 22, "Combined GitHub contributions", desc, body, theme)


# ── Overview: stat tiles + languages ────────────────────────────────────────

def overview_svg(
    totals: dict[str, dict[str, int]],
    unique_repos: int,
    since_year: int | None,
    languages: list[Language],
    accounts: list[AccountMeta],
    updated: datetime,
    theme: Theme,
) -> str:
    colors = {a.login: theme.accounts[i % len(theme.accounts)] for i, a in enumerate(accounts)}
    legend = "".join(
        f'<tspan dx="{14 if i else 12}" fill="{colors[a.login]}">●</tspan>'
        f'<tspan dx="4" class="m">{escape(a.label)}</tspan>'
        for i, a in enumerate(accounts)
    ) if len(accounts) > 1 else ""
    body = [
        f'<text x="{PAD}" y="40" font-size="16"><tspan class="b">Last 12 months</tspan>'
        f'<tspan font-size="12">{legend}</tspan></text>',
        f'<text x="{WIDTH - PAD}" y="40" font-size="12" text-anchor="end" class="m">'
        f'Updated {updated.day} {updated:%b %Y}, {updated:%H:%M} {escape(updated.tzname() or "")}</text>',
    ]

    tiles = [
        ("commits", "Commits", None),
        ("prs", "Pull requests", None),
        ("reviews", "Code reviews", None),
        ("issues", "Issues", None),
        ("repos", "Repos committed to", unique_repos),
        ("all_time", f"All-time since {since_year}" if since_year else "All-time", None),
    ]
    tile_w = (WIDTH - 2 * PAD) / len(tiles)
    top, bottom = 62, 136
    for i, (key, label, override) in enumerate(tiles):
        split = totals.get(key, {})
        value = override if override is not None else sum(split.values())
        cx = PAD + tile_w * (i + 0.5)
        if i:
            x = PAD + tile_w * i
            body.append(f'<line x1="{x:.1f}" y1="{top + 6}" x2="{x:.1f}" y2="{bottom - 6}" stroke="{theme.border}"/>')
        body.append(f'<text x="{cx:.1f}" y="{top + 30}" font-size="24" text-anchor="middle" class="b">{_fmt(value)}</text>')
        body.append(f'<text x="{cx:.1f}" y="{top + 50}" font-size="12" text-anchor="middle" class="m">{escape(label)}</text>')
        if len(accounts) > 1:
            body.append(_dot_legend(
                [(colors[a.login], "", _fmt(split.get(a.login, 0))) for a in accounts],
                round(cx, 1), top + 68, "middle", size=11))

    rule_y = bottom + 10
    body.append(f'<line x1="{PAD}" y1="{rule_y}" x2="{WIDTH - PAD}" y2="{rule_y}" stroke="{theme.border}"/>')

    heading_y = rule_y + 30
    body.append(f'<text x="{PAD}" y="{heading_y}" font-size="14" class="b">Languages'
                f'<tspan dx="8" font-size="12" class="m" font-weight="400">'
                f'share of commits, weighted by each repo\'s code</tspan></text>')
    bar_y = heading_y + 14
    if languages:
        body += _split_bar(PAD, bar_y, WIDTH - 2 * PAD, 10,
                           [(l.share, l.color) for l in languages], theme, "langs")
        cols = 4
        col_w = (WIDTH - 2 * PAD) / cols
        for i, lang in enumerate(languages):
            x = PAD + (i % cols) * col_w
            y = bar_y + 38 + (i // cols) * 22
            body.append(f'<circle cx="{x + 5:.1f}" cy="{y - 4}" r="5" fill="{lang.color}"/>')
            body.append(f'<text x="{x + 16:.1f}" y="{y}" font-size="12">{escape(lang.name)}'
                        f'<tspan dx="6" class="m">{lang.share * 100:.1f}%</tspan></text>')
        rows = (len(languages) + cols - 1) // cols
        height = bar_y + 38 + (rows - 1) * 22 + 22
    else:
        body.append(f'<text x="{PAD}" y="{bar_y + 20}" font-size="12" class="m">No language data yet.</text>')
        height = bar_y + 44

    all_split = ", ".join(f"{a.label} {_fmt(totals['commits'].get(a.login, 0))}" for a in accounts)
    desc = (f"Last 12 months combined: {_fmt(sum(totals['commits'].values()))} commits ({all_split}), "
            f"{_fmt(sum(totals['prs'].values()))} pull requests, {_fmt(sum(totals['reviews'].values()))} reviews. "
            "Top languages: " + ", ".join(f"{l.name} {l.share * 100:.0f}%" for l in languages[:5]))
    return _svg(height, "Combined GitHub statistics", desc, body, theme)
