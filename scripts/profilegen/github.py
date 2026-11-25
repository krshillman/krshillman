"""Fetch contribution data from the GitHub GraphQL API.

Uses `contributionsCollection`, which (unlike the REST Events API) still
reports commit counts after GitHub slimmed the Events API payloads in
October 2025.

Everything is normalised into plain JSON-serialisable dicts so the rest of
the pipeline (and the tests) never touch raw GraphQL shapes.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

API_URL = "https://api.github.com/graphql"

_REPO = "nameWithOwner url isPrivate"

ACCOUNT_QUERY = f"""
query ($login: String!, $from: DateTime!, $to: DateTime!, $recentFrom: DateTime!) {{
  viewer {{ login }}
  user(login: $login) {{
    login
    createdAt
    year: contributionsCollection(from: $from, to: $to) {{
      contributionYears
      totalCommitContributions
      totalIssueContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalRepositoryContributions
      restrictedContributionsCount
      contributionCalendar {{
        totalContributions
        weeks {{ contributionDays {{ date contributionCount }} }}
      }}
      commitContributionsByRepository(maxRepositories: 100) {{
        contributions {{ totalCount }}
        repository {{
          {_REPO}
          languages(first: 10, orderBy: {{field: SIZE, direction: DESC}}) {{
            edges {{ size node {{ name color }} }}
          }}
        }}
      }}
    }}
    recent: contributionsCollection(from: $recentFrom, to: $to) {{
      restrictedContributionsCount
      commitContributionsByRepository(maxRepositories: 50) {{
        repository {{ {_REPO} }}
        contributions(first: 100, orderBy: {{field: OCCURRED_AT, direction: DESC}}) {{
          nodes {{ occurredAt commitCount }}
        }}
      }}
      pullRequestContributions(first: 50, orderBy: {{direction: DESC}}) {{
        nodes {{ occurredAt pullRequest {{ number title url state repository {{ {_REPO} }} }} }}
      }}
      pullRequestReviewContributions(first: 50, orderBy: {{direction: DESC}}) {{
        nodes {{ occurredAt pullRequest {{ number title url repository {{ {_REPO} }} }} }}
      }}
      issueContributions(first: 50, orderBy: {{direction: DESC}}) {{
        nodes {{ occurredAt issue {{ number title url repository {{ {_REPO} }} }} }}
      }}
      repositoryContributions(first: 20, orderBy: {{direction: DESC}}) {{
        nodes {{ occurredAt repository {{ {_REPO} }} }}
      }}
    }}
  }}
}}
"""


class GitHubError(RuntimeError):
    pass


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def graphql(token: str, query: str, variables: dict[str, Any], *, attempts: int = 3) -> tuple[dict, list]:
    """Run a query. Returns (data, errors); partial data with errors is allowed
    because SAML-protected orgs null out individual nodes rather than failing."""
    body = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profilegen",
        },
    )
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise GitHubError("401 Unauthorized: the token is invalid, expired or revoked") from None
            if exc.code in (502, 503, 504) and attempt < attempts:
                time.sleep(5 * attempt)
                continue
            detail = exc.read()[:300].decode("utf-8", "replace")
            raise GitHubError(f"HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < attempts:
                time.sleep(5 * attempt)
                continue
            raise GitHubError(f"network error: {exc}") from None

    errors = payload.get("errors") or []
    data = payload.get("data")
    if not data:
        raise GitHubError("; ".join(e.get("message", "unknown error") for e in errors) or "empty response")
    return data, errors


def _repo(node: dict | None) -> dict | None:
    if not node:
        return None
    return {"name": node["nameWithOwner"], "url": node["url"], "private": bool(node["isPrivate"])}


def _nodes(connection: dict | None) -> list[dict]:
    return [n for n in ((connection or {}).get("nodes") or []) if n]


def normalize(login: str, label: str, data: dict, year_totals: dict[str, int], errors: list[dict]) -> dict:
    """Turn the GraphQL response into the account dict used everywhere else."""
    user = data["user"]
    year = user["year"]
    recent = user["recent"]
    calendar = year["contributionCalendar"]

    repos, hidden_repos = [], 0
    for entry in year.get("commitContributionsByRepository") or []:
        repo = _repo((entry or {}).get("repository"))
        if repo is None:
            hidden_repos += 1
            continue
        languages = [
            {"name": edge["node"]["name"], "color": edge["node"]["color"], "size": edge["size"]}
            for edge in ((entry["repository"].get("languages") or {}).get("edges") or [])
            if edge and edge.get("node")
        ]
        repos.append({**repo, "commits": entry["contributions"]["totalCount"], "languages": languages})

    events: list[dict] = []
    for entry in recent.get("commitContributionsByRepository") or []:
        repo = _repo((entry or {}).get("repository"))
        if repo is None:
            continue
        for node in _nodes(entry.get("contributions")):
            events.append({"kind": "commits", "at": node["occurredAt"], "count": node["commitCount"], "repo": repo})
    for kind, field, item_key in (
        ("pr", "pullRequestContributions", "pullRequest"),
        ("review", "pullRequestReviewContributions", "pullRequest"),
        ("issue", "issueContributions", "issue"),
    ):
        for node in _nodes(recent.get(field)):
            item = node.get(item_key)
            repo = _repo((item or {}).get("repository"))
            if item is None or repo is None:
                continue
            events.append({
                "kind": kind,
                "at": node["occurredAt"],
                "number": item["number"],
                "title": item["title"],
                "url": item["url"],
                "state": item.get("state", ""),
                "repo": repo,
            })
    for node in _nodes(recent.get("repositoryContributions")):
        repo = _repo(node.get("repository"))
        if repo is not None:
            events.append({"kind": "repo", "at": node["occurredAt"], "repo": repo})

    return {
        "login": user["login"],
        "label": label,
        "created_at": user["createdAt"],
        "calendar": [
            {"date": day["date"], "count": day["contributionCount"]}
            for week in calendar["weeks"]
            for day in week["contributionDays"]
        ],
        "totals": {
            "contributions": calendar["totalContributions"],
            "commits": year["totalCommitContributions"],
            "prs": year["totalPullRequestContributions"],
            "reviews": year["totalPullRequestReviewContributions"],
            "issues": year["totalIssueContributions"],
            "repos_created": year["totalRepositoryContributions"],
            "restricted": year["restrictedContributionsCount"],
        },
        "repos": repos,
        "years": year_totals,
        "events": events,
        "recent_restricted": recent.get("restrictedContributionsCount") or 0,
        "diagnostics": {
            "requested_login": login,
            "token_owner": (data.get("viewer") or {}).get("login"),
            "hidden_repos": hidden_repos,
            "errors": [
                {"type": e.get("type", "ERROR"), "message": e.get("message", "")[:200]} for e in errors
            ],
        },
    }


def fetch_year_totals(token: str, login: str, years: list[int], now: datetime) -> tuple[dict[str, int], list]:
    if not years:
        return {}, []
    parts = []
    for year in sorted(set(years)):
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = min(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc), now)
        parts.append(
            f'y{year}: contributionsCollection(from: "{iso(start)}", to: "{iso(end)}") '
            "{ contributionCalendar { totalContributions } }"
        )
    query = "query ($login: String!) { user(login: $login) { " + " ".join(parts) + " } }"
    data, errors = graphql(token, query, {"login": login})
    user = data.get("user") or {}
    totals = {
        key[1:]: value["contributionCalendar"]["totalContributions"]
        for key, value in user.items()
        if value
    }
    return totals, errors


def fetch_account(login: str, label: str, token: str, now: datetime, activity_days: int) -> dict:
    # Stay strictly under the API's one-year maximum span.
    start = (now - timedelta(days=364)).replace(hour=0, minute=0, second=0, microsecond=0)
    variables = {
        "login": login,
        "from": iso(start),
        "to": iso(now),
        "recentFrom": iso(now - timedelta(days=activity_days)),
    }
    data, errors = graphql(token, ACCOUNT_QUERY, variables)
    if not data.get("user"):
        raise GitHubError(f"user '{login}' not found")
    years = data["user"]["year"].get("contributionYears") or []
    year_totals, year_errors = fetch_year_totals(token, login, years, now)
    return normalize(login, label, data, year_totals, errors + year_errors)
