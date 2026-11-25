# profilegen

Builds this profile's combined view of two GitHub accounts: **krshillman** (personal) and **chhillman-uom** (work). It runs from [`.github/workflows/update-readme.yml`](../.github/workflows/update-readme.yml).

The output is:

| Output | Where it goes |
| --- | --- |
| `contributions-{light,dark}.svg`: a merged contribution calendar, the per-account split and streaks | `profile-assets` branch |
| `overview-{light,dark}.svg`: commits, PRs, reviews, issues, repos, all-time total and languages | `profile-assets` branch |
| The "Recent activity" list for both accounts | `README.md`, between `<!-- profile:activity:* -->` |
| The latest blog posts from the RSS feed | `README.md`, between `<!-- profile:blog:* -->` |

Settings such as accounts, timezone, look-back window, language limits and the feed URL live in [`.github/profile.toml`](../.github/profile.toml). The script only uses the Python standard library.

## Why not lowlighter/metrics or recent-activity?

Both read the REST **Events API**. On 7 Oct 2025 GitHub [removed commit counts from push events](https://github.blog/changelog/2025-08-08-upcoming-changes-to-github-events-api-payloads/), and that broke both tools:

- recent-activity printed "Pushed undefined commit(s)" ([open issue](https://github.com/Readme-Workflows/recent-activity/issues/351)).
- The metrics habits/activity plugins crashed with "Unexpected error" ([discussion](https://github.com/lowlighter/metrics/discussions/1813)). metrics has had no release since 2023.

Neither tool can merge two accounts anyway.

profilegen uses GraphQL `contributionsCollection` instead. This is the same data that drives the green squares on your profile, and the Events API change doesn't affect it.

## Tokens

Each account needs its own **classic** personal access token, created while signed in as that account. A token only sees its owner's private contributions. Give each token the `repo` and `read:user` scopes.

| Secret | Created by | Used for |
| --- | --- | --- |
| `METRICS_TOKEN` | krshillman | personal |
| `WORK_TOKEN` | chhillman-uom | work |

- **Organisation with SAML SSO:** on the token's page, click *Configure SSO* and authorise it for the org. If you skip this, that org's private repos come back hidden and the workflow logs a warning.
- **Least privilege:** with only `read:user`, private work still counts towards the totals and the calendar if *Include private contributions* is on. You lose the private repos' languages and the private-activity summary line.
- **Why classic tokens:** a fine-grained token is limited to one owner, either you or a single org. It can't cover your own repos and the org's repos at the same time.
- **Failures:** if either token fails (for example, it expired), the run fails and the last good cards stay up. GitHub emails you about the failed run.

## Privacy

- The SVGs contain only numbers and language names.
- In the README, public repositories are named and linked.
- Anything in a private repository is folded into one anonymous line per account, for example "🔒 42 commits and 3 reviews across 4 private repositories". Repository names, PR titles and issue titles from private repositories are never written anywhere.
- The workflow logs (public on a public repo) print counts only.

## Run locally

```bash
export PERSONAL_TOKEN=... WORK_TOKEN=...
PYTHONPATH=scripts python -m profilegen --out dist --readme README.md
PYTHONPATH=scripts python -m unittest discover -s scripts/tests -t scripts
```

`--dump-raw raw.json` saves the fetched data and `--from-raw raw.json` re-renders from it without calling the API. The dump contains private repository names, so don't commit it (it's covered by `.gitignore`).
