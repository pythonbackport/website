# pythonbackport website

Static landing page for the [pythonbackport](https://github.com/pythonbackport) GitHub org.

The page is **fully data-driven**. Every repository card on the site comes
directly from the live GitHub API at build time. There is no on-disk cache
of repository metadata anywhere in this repo.

## How it works

```
GitHub API ──► scripts/build.py ──► index.html
                      ▲
                      │
                 site.json  (display rules + org config)
                 templates/index.template.html  (HTML shell with {{projects}})
```

| File | Role | Edit it? |
| --- | --- | --- |
| `site.json` | Categories, keywords, excluded repos, copy. **Zero repo metadata.** | yes |
| `templates/index.template.html` | Static HTML shell. Contains `{{projects}}` placeholder. | yes |
| `scripts/build.py` | Fetches repos + renders HTML. | rarely |
| `scripts/_mock_repos.json` | Local fixtures for `--mock` mode. Ignored by git. | n/a |
| `index.html` | **Generated. Do not edit by hand.** | no |

## Local development

```bash
# Render using mock data (no network, fast iteration on templates)
python scripts/build.py --mock

# Render against the live GitHub API (rate-limited to 60/h without token)
python scripts/build.py

# Fail if index.html is stale (useful in CI before deploying)
python scripts/build.py --check
```

`GH_TOKEN` (or `GITHUB_TOKEN`) raises the API limit to 5,000/h.

## Automatic refresh

`.github/workflows/refresh.yml` runs daily at 06:13 UTC and rebuilds
`index.html` from the live GitHub API. If anything changed, it commits the
updated file back to the `master` branch. The Pages deploy workflow
(`.github/workflows/static.yml`) then publishes the new build.

## Customising the layout

* **Add a category** — edit `site.json` → `categories` with a `keywords`
  list and an optional `order`. Repos that match those keywords land there.
* **Pin a repo** — add its name to a category's `order` array.
* **Force a repo into a category** — add its name to that category's
  `always_include_names` array.
* **Hide a repo** — add its name to `display.exclude_repos`.

No code changes are needed for any of the above.

## License

MIT — see [LICENSE](LICENSE).