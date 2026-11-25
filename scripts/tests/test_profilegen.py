import os
import unittest
import xml.etree.ElementTree as ET
from unittest import mock
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from profilegen import aggregate, blog, github, readme, render
from profilegen.github import normalize

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
TZ = ZoneInfo("Australia/Melbourne")


def repo(name, private=False):
    return {"nameWithOwner": name, "url": f"https://github.com/{name}", "isPrivate": private}


def graphql_payload(login="me", viewer="me", days=None):
    days = days or [("2026-11-23", 2), ("2026-11-24", 0), ("2026-11-25", 3)]
    return {
        "viewer": {"login": viewer},
        "user": {
            "login": login,
            "createdAt": "2016-01-01T00:00:00Z",
            "year": {
                "contributionYears": [2026],
                "totalCommitContributions": 5,
                "totalIssueContributions": 1,
                "totalPullRequestContributions": 2,
                "totalPullRequestReviewContributions": 3,
                "totalRepositoryContributions": 0,
                "restrictedContributionsCount": 0,
                "contributionCalendar": {
                    "totalContributions": sum(c for _, c in days),
                    "weeks": [{"contributionDays": [{"date": d, "contributionCount": c} for d, c in days]}],
                },
                "commitContributionsByRepository": [
                    {"contributions": {"totalCount": 4},
                     "repository": {**repo("me/public"), "languages": {"edges": [
                         {"size": 300, "node": {"name": "Python", "color": "#3572A5"}},
                         {"size": 100, "node": {"name": "Shell", "color": "#89e051"}}]}}},
                    {"contributions": {"totalCount": 1},
                     "repository": {**repo("org/secret-project", private=True), "languages": {"edges": [
                         {"size": 50, "node": {"name": "SQL", "color": "#e38c00"}}]}}},
                ],
            },
            "recent": {
                "restrictedContributionsCount": 0,
                "commitContributionsByRepository": [
                    {"repository": repo("me/public"), "contributions": {"nodes": [
                        {"occurredAt": "2026-11-24T00:00:00Z", "commitCount": 2},
                        {"occurredAt": "2026-11-20T00:00:00Z", "commitCount": 1}]}},
                    {"repository": repo("org/secret-project", private=True), "contributions": {"nodes": [
                        {"occurredAt": "2026-11-25T00:00:00Z", "commitCount": 7}]}},
                ],
                "pullRequestContributions": {"nodes": [
                    {"occurredAt": "2026-11-22T03:00:00Z", "pullRequest": {
                        "number": 4, "title": "Fix [bug] in *parser*", "url": "https://github.com/me/public/pull/4",
                        "state": "MERGED", "repository": repo("me/public")}},
                    {"occurredAt": "2026-11-21T03:00:00Z", "pullRequest": {
                        "number": 9, "title": "Confidential migration", "url": "https://github.com/org/secret-project/pull/9",
                        "state": "OPEN", "repository": repo("org/secret-project", private=True)}},
                    None,
                ]},
                "pullRequestReviewContributions": {"nodes": []},
                "issueContributions": {"nodes": [None]},  # connection nodes are nullable
                "repositoryContributions": {"nodes": [
                    {"occurredAt": "2026-11-23T00:00:00Z", "repository": repo("org/new-private", private=True)}]},
            },
        },
    }


def account(login="me", label="personal", **kw):
    return normalize(login, label, graphql_payload(login=login, **kw), {"2025": 10, "2026": 5}, [])


class NormalizeTest(unittest.TestCase):
    def test_shapes(self):
        acc = account()
        self.assertEqual(acc["totals"]["commits"], 5)
        self.assertEqual([r["name"] for r in acc["repos"]], ["me/public", "org/secret-project"])
        self.assertEqual(len(acc["calendar"]), 3)
        kinds = sorted(e["kind"] for e in acc["events"])
        self.assertEqual(kinds, ["commits", "commits", "commits", "pr", "pr", "repo"])  # null nodes skipped

    def test_token_owner_recorded(self):
        acc = normalize("work", "work", graphql_payload(login="work", viewer="someone-else"), {}, [])
        self.assertEqual(acc["diagnostics"]["token_owner"], "someone-else")


class ErrorReportingTest(unittest.TestCase):
    SAML = {"type": "FORBIDDEN", "path": ["user", "year"],
            "message": "Resource protected by organization SAML enforcement. You must grant your token access to uni-org."}

    def test_null_user_with_errors_reports_the_error(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaises(github.GitHubError) as ctx:
                github._require_user({"viewer": {"login": "w"}, "user": None}, [self.SAML], "w")
        message = str(ctx.exception)
        self.assertIn("FORBIDDEN", message)
        self.assertIn("SAML single sign-on", message)
        self.assertNotIn("uni-org", message)  # raw text never reaches public Actions logs
        self.assertNotIn("not found", message)

    def test_local_runs_include_raw_message(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": ""}):
            self.assertIn("uni-org", github.describe_errors([self.SAML]))

    def test_not_found(self):
        err = {"type": "NOT_FOUND", "message": "Could not resolve to a User with the login of 'x'."}
        with self.assertRaisesRegex(github.GitHubError, "not found"):
            github._require_user({"user": None}, [err], "x")
        with self.assertRaisesRegex(github.GitHubError, "not found"):
            github._require_user({"user": None}, [], "x")


class CalendarTest(unittest.TestCase):
    def test_merge_sums_and_fills_gaps(self):
        a = account("a", days=[("2026-11-20", 1), ("2026-11-22", 2)])
        b = account("b", label="work", days=[("2026-11-22", 5)])
        days = aggregate.merge_calendars([a, b])
        self.assertEqual([d.day for d in days], [date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 22)])
        self.assertEqual([d.total for d in days], [1, 0, 7])
        self.assertEqual(days[2].by_account, {"a": 2, "b": 5})

    def test_streaks_ignore_empty_today(self):
        mk = lambda counts: [aggregate.Day(date(2026, 1, 1) + timedelta(i), c, {}) for i, c in enumerate(counts)]
        self.assertEqual(aggregate.streaks(mk([1, 1, 0, 1, 1, 1, 0])), (3, 3))
        self.assertEqual(aggregate.streaks(mk([1, 1, 1, 1, 0, 1])), (1, 4))
        self.assertEqual(aggregate.streaks(mk([0, 0])), (0, 0))
        self.assertEqual(aggregate.streaks([]), (0, 0))

    def test_levels_span_all_four(self):
        days = [aggregate.Day(date(2026, 1, i + 1), c, {}) for i, c in enumerate([0, 1, 2, 3, 4])]
        th = aggregate.level_thresholds(days)
        self.assertEqual([aggregate.level(d.total, th) for d in days], [0, 1, 2, 3, 4])

    def test_levels_single_value(self):
        days = [aggregate.Day(date(2026, 1, 1), 2, {}), aggregate.Day(date(2026, 1, 2), 0, {})]
        th = aggregate.level_thresholds(days)
        self.assertEqual(aggregate.level(2, th), 4)
        self.assertEqual(aggregate.level(0, th), 0)


class LanguageTest(unittest.TestCase):
    def test_weighted_by_commits_and_merged_across_accounts(self):
        langs = aggregate.language_breakdown([account("a"), account("b", label="work")])
        shares = {l.name: round(l.share, 3) for l in langs}
        # per account: public 4 commits -> Python 3, Shell 1; private 1 commit -> SQL 1
        self.assertEqual(shares, {"Python": 0.6, "Shell": 0.2, "SQL": 0.2})
        self.assertAlmostEqual(sum(l.share for l in langs), 1.0)

    def test_limit_groups_other_and_ignore(self):
        langs = aggregate.language_breakdown([account()], limit=2)
        self.assertEqual([(l.name, round(l.share, 2)) for l in langs], [("Python", 0.6), ("Other", 0.4)])
        langs = aggregate.language_breakdown([account()], ignore=("Shell",))
        self.assertEqual([l.name for l in langs], ["Python", "SQL"])

    def test_empty(self):
        self.assertEqual(aggregate.language_breakdown([]), [])


class ActivityTest(unittest.TestCase):
    def items(self, limit=10):
        return aggregate.activity_items([account()], NOW - timedelta(days=30), limit)

    def test_private_details_never_rendered(self):
        text = readme.activity_markdown(self.items(), TZ, 30)
        self.assertNotIn("secret-project", text)
        self.assertNotIn("Confidential", text)
        self.assertNotIn("new-private", text)
        self.assertIn("🔒 **7 commits**, 1 pull request and 1 new repository across 2 private repositories", text)

    def test_private_repo_creation_alone(self):
        acc = account()
        acc["events"] = [e for e in acc["events"] if e["kind"] == "repo"]
        text = readme.activity_markdown(aggregate.activity_items([acc], NOW - timedelta(days=30), 10), TZ, 30)
        self.assertIn("🔒 1 new repository across 1 private repository", text)
        self.assertNotIn("0 private contributions", text)

    def test_public_commits_grouped_and_titles_escaped(self):
        text = readme.activity_markdown(self.items(), TZ, 30)
        self.assertIn("Pushed **3 commits** to [me/public](https://github.com/me/public) <sub>· personal · 20–24 Nov</sub>", text)
        self.assertIn(r"Fix \[bug\] in \*parser\* (merged)", text)

    def test_private_summary_survives_limit(self):
        items = self.items(limit=1)
        self.assertEqual([i.kind for i in items], ["private"])

    def test_window_respected(self):
        items = aggregate.activity_items([account()], NOW - timedelta(days=1), 10)
        self.assertEqual([i.kind for i in items], ["private"])

    def test_empty(self):
        self.assertEqual(readme.activity_markdown([], TZ, 30), "_No activity in the last 30 days._")


class ReadmeTest(unittest.TestCase):
    def test_replace_section(self):
        text = "a\n<!-- profile:x:start -->\nold\n<!-- profile:x:end -->\nb"
        new, found = readme.replace_section(text, "x", r"new \1 \\ stuff")
        self.assertTrue(found)
        self.assertEqual(new, "a\n<!-- profile:x:start -->\n" + r"new \1 \\ stuff" + "\n<!-- profile:x:end -->\nb")

    def test_missing_markers(self):
        self.assertEqual(readme.replace_section("nothing", "x", "y"), ("nothing", False))


class BlogTest(unittest.TestCase):
    RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Future post</title><link>https://b/f</link><pubDate>Fri, 11 Dec 2026 09:00:00 +1100</pubDate></item>
      <item><title>Old &amp; good</title><link>https://b/o</link><pubDate>Sat, 01 Aug 2026 09:00:00 +1000</pubDate></item>
      <item><title>New</title><link>https://b/n</link><pubDate>Sat, 21 Nov 2026 09:00:00 +1100</pubDate></item>
      <item><title>No date</title><link>https://b/x</link></item>
    </channel></rss>"""

    def test_rss_sorted_future_skipped(self):
        posts = blog.parse_feed(self.RSS, NOW, 5)
        self.assertEqual([p["title"] for p in posts], ["New", "Old & good"])
        self.assertIn("[Old &amp; good](https://b/o) <sub>· 1 Aug 2026</sub>", readme.blog_markdown(posts, TZ))

    def test_atom(self):
        atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title>
          <link rel="alternate" href="https://b/a"/><published>2026-11-01T00:00:00Z</published></entry></feed>"""
        self.assertEqual(blog.parse_feed(atom, NOW, 5)[0]["link"], "https://b/a")


class RenderTest(unittest.TestCase):
    def render_all(self, accounts):
        meta = [render.AccountMeta(a["login"], a["label"]) for a in accounts]
        days = aggregate.merge_calendars(accounts)
        totals = aggregate.totals(accounts)
        langs = aggregate.language_breakdown(accounts)
        for theme in render.THEMES:
            yield render.contributions_svg(days, meta, theme)
            yield render.overview_svg(totals, aggregate.unique_repo_count(accounts), aggregate.first_year(accounts),
                                      langs, meta, NOW.astimezone(TZ), theme)

    def test_valid_xml_without_repo_names(self):
        for svg in self.render_all([account("a"), account("b", label="work")]):
            ET.fromstring(svg)
            self.assertNotIn("secret-project", svg)
            self.assertNotIn("me/public", svg)

    def test_empty_data_renders(self):
        empty = account(days=[("2026-11-25", 0)])
        empty["repos"] = []
        for svg in self.render_all([empty]):
            ET.fromstring(svg)


if __name__ == "__main__":
    unittest.main()
