"""Pure functions that merge several normalised accounts into one view."""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

OTHER_COLOR = "#8b949e"


@dataclass(frozen=True)
class Day:
    day: date
    total: int
    by_account: dict[str, int]


@dataclass(frozen=True)
class Language:
    name: str
    color: str
    share: float


@dataclass
class ActivityItem:
    kind: str  # commits | pr | review | issue | repo | private
    at: datetime
    account: str  # display label, e.g. "work"
    repo: dict | None = None
    count: int = 0
    number: int | None = None
    title: str = ""
    url: str = ""
    state: str = ""
    private: dict = field(default_factory=dict)


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ── Calendar ────────────────────────────────────────────────────────────────

def merge_calendars(accounts: list[dict]) -> list[Day]:
    """Sum daily counts across accounts, filling any gaps with zero days."""
    counts: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for account in accounts:
        for entry in account["calendar"]:
            counts[date.fromisoformat(entry["date"])][account["login"]] += entry["count"]
    if not counts:
        return []
    first, last = min(counts), max(counts)
    days = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        split = {a["login"]: counts.get(day, {}).get(a["login"], 0) for a in accounts}
        days.append(Day(day, sum(split.values()), split))
    return days


def streaks(days: list[Day]) -> tuple[int, int]:
    """(current, longest). An empty *today* doesn't break the current streak."""
    longest = run = 0
    for d in days:
        run = run + 1 if d.total > 0 else 0
        longest = max(longest, run)
    tail = days[:-1] if days and days[-1].total == 0 else days
    current = 0
    for d in reversed(tail):
        if d.total == 0:
            break
        current += 1
    return current, longest


def busiest_day(days: list[Day]) -> Day | None:
    active = [d for d in days if d.total > 0]
    return max(active, key=lambda d: (d.total, d.day)) if active else None


def level_thresholds(days: list[Day]) -> tuple[float, float, float]:
    """Quartile cut-points over non-zero days, like GitHub's own graph."""
    values = sorted(d.total for d in days if d.total > 0)
    if len(set(values)) <= 1:
        v = values[0] if values else 1
        return (v - 0.5, v - 0.5, v - 0.5)  # every active day lands on level 4
    q1, q2, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return (q1, q2, q3)


def level(count: int, thresholds: tuple[float, float, float]) -> int:
    if count <= 0:
        return 0
    for index, cut in enumerate(thresholds, start=1):
        if count <= cut:
            return index
    return 4


# ── Totals ──────────────────────────────────────────────────────────────────

TOTAL_KEYS = ("contributions", "commits", "prs", "reviews", "issues", "restricted")


def totals(accounts: list[dict]) -> dict[str, dict[str, int]]:
    """{metric: {login: value}} for the last-year metrics, plus repos and all-time."""
    result = {key: {a["login"]: a["totals"].get(key, 0) for a in accounts} for key in TOTAL_KEYS}
    result["repos"] = {
        a["login"]: len(a["repos"]) + a.get("diagnostics", {}).get("hidden_repos", 0) for a in accounts
    }
    result["all_time"] = {a["login"]: sum(a.get("years", {}).values()) for a in accounts}
    return result


def unique_repo_count(accounts: list[dict]) -> int:
    names = {r["name"] for a in accounts for r in a["repos"]}
    hidden = sum(a.get("diagnostics", {}).get("hidden_repos", 0) for a in accounts)
    return len(names) + hidden


def first_year(accounts: list[dict]) -> int | None:
    years = [int(y) for a in accounts for y, n in a.get("years", {}).items() if n > 0]
    return min(years) if years else None


# ── Languages ───────────────────────────────────────────────────────────────

def language_breakdown(accounts: list[dict], ignore: tuple[str, ...] = (), limit: int = 8) -> list[Language]:
    """Share of the year's commits per language.

    Each repository's commit count (summed across accounts) is spread over its
    languages in proportion to their byte size, so a language counts for as
    much as you actually committed to code written in it.
    """
    repo_commits: dict[str, int] = defaultdict(int)
    repo_languages: dict[str, list[dict]] = {}
    for account in accounts:
        for repo in account["repos"]:
            repo_commits[repo["name"]] += repo["commits"]
            repo_languages[repo["name"]] = repo["languages"]

    weights: dict[str, float] = defaultdict(float)
    colors: dict[str, str] = {}
    for name, commits in repo_commits.items():
        langs = [l for l in repo_languages[name] if l["name"] not in ignore and l["size"] > 0]
        size = sum(l["size"] for l in langs)
        if not size or not commits:
            continue
        for lang in langs:
            weights[lang["name"]] += commits * lang["size"] / size
            colors.setdefault(lang["name"], lang.get("color") or OTHER_COLOR)

    grand = sum(weights.values())
    if not grand:
        return []
    ranked = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    limit = max(limit, 2)  # room for at least one language plus "Other"
    if len(ranked) > limit:
        head, tail = ranked[: limit - 1], ranked[limit - 1 :]
        result = [Language(n, colors[n], w / grand) for n, w in head]
        result.append(Language("Other", OTHER_COLOR, sum(w for _, w in tail) / grand))
        return result
    return [Language(n, colors[n], w / grand) for n, w in ranked]


# ── Recent activity ─────────────────────────────────────────────────────────

_PRIVATE_COUNTERS = {"commits": "commits", "pr": "prs", "review": "reviews", "issue": "issues"}


def activity_items(accounts: list[dict], since: datetime, limit: int) -> list[ActivityItem]:
    """Newest-first activity across all accounts.

    Public repositories are listed by name (commits grouped per repository).
    Anything in a private repository is folded into one anonymous summary per
    account so repository names, PR titles etc. never leave the API.
    """
    public: list[ActivityItem] = []
    private: list[ActivityItem] = []
    for account in accounts:
        label = account["label"]
        commits: dict[str, ActivityItem] = {}
        summary = {"commits": 0, "prs": 0, "reviews": 0, "issues": 0, "repos": set(),
                   "other": account.get("recent_restricted", 0)}
        latest: datetime | None = None
        for event in account["events"]:
            at = parse_ts(event["at"])
            if at < since:
                continue
            repo = event["repo"]
            if repo["private"]:
                counter = _PRIVATE_COUNTERS.get(event["kind"])
                if counter:
                    summary[counter] += event.get("count", 1) if event["kind"] == "commits" else 1
                summary["repos"].add(repo["name"])
                latest = max(latest, at) if latest else at
            elif event["kind"] == "commits":
                item = commits.setdefault(repo["name"], ActivityItem("commits", at, label, repo))
                item.count += event["count"]
                item.at = max(item.at, at)
            else:
                public.append(ActivityItem(
                    kind=event["kind"], at=at, account=label, repo=repo,
                    number=event.get("number"), title=event.get("title", ""),
                    url=event.get("url", ""), state=event.get("state", ""),
                ))
        public.extend(commits.values())
        if latest or summary["other"]:
            summary["repos"] = len(summary["repos"])
            private.append(ActivityItem("private", latest or since, label, private=summary))

    public.sort(key=lambda i: i.at, reverse=True)
    chosen = private[:limit] + public[: max(0, limit - len(private))]
    return sorted(chosen, key=lambda i: i.at, reverse=True)
