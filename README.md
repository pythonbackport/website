# pythonbackport website

Static landing page for the [pythonbackport](https://github.com/pythonbackport) GitHub org.

The page is **fully data-driven**. Every byte of repository metadata on
the rendered page comes directly from the live GitHub API at build time.
There is no on-disk cache of repo data, and there is no config file —
just one Python script, one HTML template, and a generated `index.html`.

```
GitHub API ──► scripts/build.py ──► index.html
                      ▲
                      │
                 templates/index.template.html
                 (HTML shell with {{projects}} placeholder)
```

## Files

| File | Role | Edit? |
| --- | --- | --- |
| `scripts/build.py` | Fetches `/orgs/<org>` + `/orgs/<org>/repos`, renders HTML. | rarely |
| `scripts/make_logo.py` | Turns `pythonbackportlogo.png` into the transparent `logo.png`. | rarely |
| `templates/index.template.html` | Static HTML shell with `{{projects}}`, `{{site_title}}`, `{{site_tagline}}`. | yes |
| `index.html` | Generated. Do not edit by hand. | no |
| `logo.png` | Generated from `pythonbackportlogo.png`. Do not edit by hand. | no |
| `.github/workflows/refresh.yml` | Daily cron + on-push regeneration, auto-commits the rebuilt index. | rarely |

## Local

```bash
python scripts/build.py            # full build against live API
python scripts/build.py --check    # CI guard: fail if index.html is stale
python scripts/build.py --stdout    # print HTML to stdout
python scripts/build.py --org foo  # fetch a different org
python scripts/make_logo.py        # regenerate logo.png from the artwork
```

`GH_TOKEN` (or `GITHUB_TOKEN`) raises the API limit from 60/h to 5,000/h.

## Branding

`pythonbackportlogo.png` is the source artwork: a flat blue-and-yellow mark on
an opaque white background. The site is dark themed, so the raw file would
render as a bright white rectangle. `scripts/make_logo.py` removes the white
backdrop (recovering true per-pixel alpha by un-mixing each shape against its
brand colour), crops the transparent margin, and writes `logo.png`. Both the
standard library and the favicon use that generated file.

## Classification

The script buckets each fetched repo into one of two sections based on
the live API response — no lookup tables:

* **Backports** — every public repo that isn't the discussion hub.
* **Community** — repos whose name, description or topics contain
  `discussion`, `discussions`, or `forum`.

The repo that hosts this very website is auto-detected and excluded
by name match (`website`, `.github`, anything equal to the org login)
or by description / topics containing `website` or `github-pages`.

## Community link

GitHub Discussions is not enabled for this org — the org-level page answers
`404` and the `community` repo is archived — so the community call-to-action
points at the Discord invite instead. The invite is the `DISCORD_INVITE`
constant in `scripts/build.py`, which means the daily refresh workflow can
never silently revert it to a dead GitHub Discussions link.

## Automatic refresh

`.github/workflows/refresh.yml` runs daily at 06:13 UTC and rebuilds
`index.html` from the live GitHub API. If anything changed, it commits
the updated file back to the `master` branch, and the Pages deploy
workflow (`.github/workflows/static.yml`) publishes the new build.