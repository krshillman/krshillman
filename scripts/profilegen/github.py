"""Fetch contribution data from the GitHub GraphQL API.

Uses `contributionsCollection`, which (unlike the REST Events API) still
reports commit counts after GitHub slimmed the Events API payloads in
October 2025.

Everything is normalised into plain JSON-serialisable dicts so the rest of
the pipeline (and the tests) never touch raw GraphQL shapes.
"""
from __future__ import annotations

import json
import os
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


# Fixed hints keyed on fragments of GitHub's error messages. In Actions only
# the error type and these hints are printed: the logs of a public repository
# are public, and raw messages can name organisations.
_HINTS = (
    ("SAML", "authorise the token for the organisation's SAML single sign-on"),
    ("IP allow list", "an organisation IP allow list is blocking the Actions runner"),
    ("rate limit", "GitHub API rate limit - try again later"),
    ("Could not resolve to a User", "check the login in .github/profile.toml"),
)


def describe_errors(errors: list[dict]) -> str:
    verbose = os.environ.get("GITHUB_ACTIONS") != "true"
    parts = []
    for error in errors[:5]:
        message = error.get("message", "")
        text = error.get("type", "ERROR")
        hints = [hint for needle, hint in _HINTS if needle.lower() in message.lower()]
        if hints:
            text += f" ({'; '.join(hints)})"
        if verbose:
            text += f": {message[:200]}"
        parts.append(text)
    if len(errors) > 5:
        parts.append(f"+{len(errors) - 5} more")
    return "; ".join(parts) or "unknown error"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def graphql(token: str, query: str, variables: dict[str, Any], *, attempts: int = 3) -> tuple[dict, list]:
    """Run a query. Returns (data, errors): GraphQL can return partial data
    alongside errors on nullable fields, which callers may still use."""
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
            detail = ""
            if os.environ.get("GITHUB_ACTIONS") != "true":
                detail = ": " + exc.read()[:300].decode("utf-8", "replace")
            raise GitHubError(f"HTTP {exc.code}{detail}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < attempts:
                time.sleep(5 * attempt)
                continue
            raise GitHubError(f"network error: {exc}") from None

    errors = payload.get("errors") or []
    data = payload.get("data")
    if not data:
        raise GitHubError(describe_errors(errors) if errors else "empty response")
    return data, errors


def _require_user(data: dict, errors: list[dict], login: str) -> dict:
    """Most fields under `user` are non-null in GitHub's schema, so a field
    error anywhere below it (e.g. SAML enforcement on one repository) nulls the
    whole user. Report the actual errors rather than a misleading "not found"."""
    user = data.get("user")
    if user:
        return user
    if errors and not all(e.get("type") == "NOT_FOUND" for e in errors):
        raise GitHubError(f"GraphQL error for '{login}': {describe_errors(errors)}")
    raise GitHubError(f"user '{login}' not found")


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

    repos = []
    for entry in year.get("commitContributionsByRepository") or []:
        repo = _repo((entry or {}).get("repository"))
        if repo is None:  # not allowed by the schema; defensive only
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
    user = _require_user(data, errors, login)
    totals = {key[1:]: value["contributionCalendar"]["totalContributions"] for key, value in user.items()}
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
    user = _require_user(data, errors, login)
    years = user["year"].get("contributionYears") or []
    year_totals, year_errors = fetch_year_totals(token, login, years, now)
    return normalize(login, label, data, year_totals, errors + year_errors)
