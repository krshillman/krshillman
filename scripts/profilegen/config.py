from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Account:
    login: str
    label: str
    token_env: str


@dataclass(frozen=True)
class Config:
    accounts: tuple[Account, ...]
    timezone: str = "Australia/Melbourne"
    activity_days: int = 30
    activity_max_items: int = 8
    languages_max: int = 8
    languages_ignore: tuple[str, ...] = ()
    blog_feed: str | None = None
    blog_max_posts: int = 5


def load_config(path: str | Path) -> Config:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    accounts = tuple(
        Account(login=a["login"], label=a.get("label", a["login"]), token_env=a["token_env"])
        for a in data.get("accounts", [])
    )
    if not accounts:
        raise ValueError(f"{path}: at least one [[accounts]] entry is required")
    if len({a.login.lower() for a in accounts}) != len(accounts):
        raise ValueError(f"{path}: account logins must be unique")

    display = data.get("display", {})
    activity = data.get("activity", {})
    languages = data.get("languages", {})
    blog = data.get("blog", {})
    return Config(
        accounts=accounts,
        timezone=display.get("timezone", "Australia/Melbourne"),
        activity_days=int(activity.get("days", 30)),
        activity_max_items=int(activity.get("max_items", 8)),
        languages_max=int(languages.get("max", 8)),
        languages_ignore=tuple(languages.get("ignore", ())),
        blog_feed=blog.get("feed") or None,
        blog_max_posts=int(blog.get("max_posts", 5)),
    )
