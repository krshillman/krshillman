"""Build the combined profile.

    python -m profilegen --config .github/profile.toml --out dist --readme README.md

Writes {contributions,overview}-{light,dark}.svg into --out and rewrites the
<!-- profile:activity --> and <!-- profile:blog --> sections of the README.
Tokens are read from the environment variables named in the config.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import aggregate, blog, github, readme, render
from .config import load_config

IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


def warn(message: str) -> None:
    print(f"::warning::{message}" if IN_ACTIONS else f"warning: {message}", file=sys.stderr)


def report(account: dict) -> None:
    """Print a diagnostic summary. Never prints repository names: Actions logs
    on a public repository are public."""
    diag = account.get("diagnostics", {})
    t = account["totals"]
    private_repos = sum(1 for r in account["repos"] if r["private"])
    print(f"[{account['label']}] @{account['login']}: {t['contributions']} contributions, "
          f"{t['commits']} commits, {t['prs']} PRs, {t['reviews']} reviews, {t['issues']} issues, "
          f"{len(account['repos'])} repos committed to ({private_repos} private), "
          f"{t['restricted']} restricted, all-time {sum(account.get('years', {}).values())}")
    owner = diag.get("token_owner")
    if owner and owner.lower() != account["login"].lower():
        warn(f"the token for @{account['login']} belongs to @{owner}. It cannot see @{account['login']}'s "
             "private contributions - create the token while signed in as that account.")
    if diag.get("errors"):
        warn(f"@{account['login']}: partial data - {github.describe_errors(diag['errors'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="profilegen", description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", default=".github/profile.toml")
    parser.add_argument("--out", default="dist", help="directory for the generated SVGs")
    parser.add_argument("--readme", default="README.md", help="README to update ('' to skip)")
    parser.add_argument("--from-raw", help="load account data from a JSON file instead of the API")
    parser.add_argument("--dump-raw", help="save fetched account data to this JSON file (debugging; "
                                           "contains private repo names - do not commit it)")
    parser.add_argument("--no-blog", action="store_true", help="skip fetching the blog feed")
    parser.add_argument("--allow-partial", action="store_true",
                        help="publish even if some accounts could not be fetched")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    tz = ZoneInfo(config.timezone)
    now = datetime.now(timezone.utc)

    if args.from_raw:
        accounts = json.loads(Path(args.from_raw).read_text(encoding="utf-8"))
    else:
        accounts, failed = [], []
        for acc in config.accounts:
            token = os.environ.get(acc.token_env, "").strip()
            if not token:
                warn(f"${acc.token_env} is not set - cannot fetch @{acc.login} ({acc.label})")
                failed.append(acc.login)
                continue
            try:
                accounts.append(github.fetch_account(acc.login, acc.label, token, now, config.activity_days))
            except github.GitHubError as exc:
                warn(f"could not fetch @{acc.login} ({acc.label}): {exc}")
                failed.append(acc.login)
        # Publishing a "combined" card that silently lacks an account would be
        # misleading, so by default keep the last good output and fail loudly.
        if failed and not args.allow_partial:
            print(f"error: failed to fetch {', '.join('@' + f for f in failed)}; nothing was written "
                  "(use --allow-partial to publish anyway)", file=sys.stderr)
            return 1
    if not accounts:
        print("error: no account data - check the tokens", file=sys.stderr)
        return 1
    if args.dump_raw:
        Path(args.dump_raw).write_text(json.dumps(accounts, indent=2), encoding="utf-8")
    for account in accounts:
        report(account)

    # ── SVG cards ──
    meta = [render.AccountMeta(a["login"], a["label"]) for a in accounts]
    days = aggregate.merge_calendars(accounts)
    totals = aggregate.totals(accounts)
    languages = aggregate.language_breakdown(accounts, config.languages_ignore, config.languages_max)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for theme in render.THEMES:
        (out / f"contributions-{theme.name}.svg").write_text(
            render.contributions_svg(days, meta, theme), encoding="utf-8")
        (out / f"overview-{theme.name}.svg").write_text(
            render.overview_svg(totals, aggregate.unique_repo_count(accounts), aggregate.first_year(accounts),
                                languages, meta, now.astimezone(tz), theme), encoding="utf-8")
    print(f"wrote SVGs to {out}/")

    # ── README sections ──
    if args.readme:
        path = Path(args.readme)
        text = original = path.read_text(encoding="utf-8")
        since = now - timedelta(days=config.activity_days)
        items = aggregate.activity_items(accounts, since, config.activity_max_items)
        text, found = readme.replace_section(text, "activity", readme.activity_markdown(items, tz, config.activity_days))
        if not found:
            warn(f"{path}: no <!-- profile:activity:start/end --> markers")
        if config.blog_feed and not args.no_blog:
            try:
                posts = blog.fetch_posts(config.blog_feed, now, config.blog_max_posts)
            except Exception as exc:  # a broken feed must not block the stats
                warn(f"blog feed {config.blog_feed}: {exc}")
            else:
                text, found = readme.replace_section(text, "blog", readme.blog_markdown(posts, tz))
                if not found:
                    warn(f"{path}: no <!-- profile:blog:start/end --> markers")
        if text != original:
            path.write_text(text, encoding="utf-8")
            print(f"updated {path}")
        else:
            print(f"{path} unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
