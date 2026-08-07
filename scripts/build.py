#!/usr/bin/env python3
"""
build.py — Generate index.html directly from the live GitHub API.

There is NO on-disk cache of repository metadata. Every run hits
  https://api.github.com/orgs/<org>/repos
fetches every page, then renders index.html from the response.

The only non-derived file in this repo is `site.json` (categories, copy,
display rules). It contains ZERO repository-specific data.

Usage:
  python scripts/build.py
  python scripts/build.py --check   # exit 1 if index.html would change
  python scripts/build.py --stdout

Environment:
  GH_TOKEN  optional GitHub token (raises rate limit 60/h -> 5000/h)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
SITE_JSON = ROOT / "site.json"
TEMPLATE = ROOT / "templates" / "index.template.html"
OUTPUT = ROOT / "index.html"

API_BASE = "https://api.github.com"
USER_AGENT = "pythonbackport-website/1.0"

PLACEHOLDER_PROJECTS = "{{projects}}"
PLACEHOLDER_TITLE = "{{site_title}}"
PLACEHOLDER_TAGLINE = "{{site_tagline}}"


# ---------- GitHub API ------------------------------------------------------

def gh_get(url: str, token: str | None) -> tuple[Any, dict]:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        if e.code == 403 and "rate limit" in body.lower():
            raise SystemExit("GitHub API rate limit hit. Set GH_TOKEN and retry.") from e
        if e.code == 404:
            raise SystemExit(f"GitHub API 404 for {url}. Is the org name in site.json correct?") from e
        raise


def gh_list(url: str, token: str | None) -> list[Any]:
    """Walk every page of a GitHub list endpoint."""
    out: list[Any] = []
    while url:
        data, headers = gh_get(url, token)
        if isinstance(data, list):
            out.extend(data)
        else:
            return [data]
        nxt = None
        for part in headers.get("link", "").split(","):
            m = re.match(r'\s*<([^>]+)>;\s*rel="next"', part)
            if m:
                nxt = m.group(1)
                break
        url = nxt
    return out


def fetch_org_repos(org: str, token: str | None) -> list[dict]:
    url = f"{API_BASE}/orgs/{org}/repos?per_page=100&type=public&sort=updated"
    return gh_list(url, token)


# ---------- Classification --------------------------------------------------

def classify(repo: dict, site: dict) -> str:
    """Pick the best category for a repo based on its live description + topics."""
    desc = (repo.get("description") or "").lower()
    topics = " ".join(repo.get("topics") or []).lower()
    name = repo.get("name", "").lower()
    haystack = f"{name} {desc} {topics}"

    cats = site.get("categories", {})

    for cat_key, cat_cfg in cats.items():
        if name in [n.lower() for n in cat_cfg.get("always_include_names", [])]:
            return cat_key

    best_cat, best_score = None, 0
    for cat_key, cat_cfg in cats.items():
        score = sum(1 for kw in cat_cfg.get("keywords", []) if kw in haystack)
        if score > best_score:
            best_cat, best_score = cat_key, score
    return best_cat or "core"


def should_show(repo: dict, site: dict) -> bool:
    d = site.get("display", {})
    if repo.get("private"):
        return False
    if repo.get("archived") and not d.get("show_archived", False):
        return False
    if repo.get("fork") and not d.get("show_forks", False):
        return False
    if repo.get("name") in d.get("exclude_repos", []):
        return False
    return True


def trim(text: str | None, n: int) -> str | None:
    if not text:
        return text
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


# ---------- HTML rendering --------------------------------------------------

def esc(s: Any) -> str:
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                  .replace(">", "&gt;").replace('"', "&quot;"))


def has_text(s: Any) -> bool:
    return isinstance(s, str) and s.strip() != ""


def render_card(repo: dict, site: dict) -> str:
    """Render one <a class="project-card"> from live GitHub data.

    Drops any sub-block whose field is missing in the API response.
    No placeholder words, ever.
    """
    name = repo.get("name")
    url = repo.get("html_url")
    if not (has_text(name) and has_text(url)):
        return ""

    max_desc = site.get("display", {}).get("max_description_chars", 220)
    desc = trim(repo.get("description"), max_desc)

    icon_em = "★" if "featured" in (repo.get("topics") or []) else None
    title = f'<span>{esc(name)}</span>'
    if icon_em:
        title = f'<span>{esc(icon_em)}</span>' + title

    tag = None
    # First try a topic that looks like a PEP id
    for t in repo.get("topics") or []:
        if isinstance(t, str) and t.lower().startswith("pep"):
            tag = t.upper()
            break
    # Otherwise look for a PEP <number> mention in the description (first hit)
    if not has_text(tag) and has_text(repo.get("description")):
        m = re.search(r"\bPEP\s*\d{2,4}\b", repo["description"], re.IGNORECASE)
        if m:
            tag = m.group(0).upper().replace("PEP ", "PEP ")
    tag_html = f'<span class="project-tag">{esc(tag)}</span>' if has_text(tag) else ""

    desc_html = f'<div class="project-desc">{esc(desc)}</div>' if has_text(desc) else ""

    parts: list[str] = []
    stars = repo.get("stargazers_count")
    if isinstance(stars, int):
        parts.append(f"<span>★ {stars:,}</span>")
    topics_clean = [t for t in (repo.get("topics") or []) if has_text(t)]
    max_topics = site.get("display", {}).get("max_topics_per_card", 3)
    if topics_clean:
        chips = " · ".join(esc(t) for t in topics_clean[:max_topics])
        parts.append(f"<span>{chips}</span>")
    elif has_text(repo.get("language")):
        parts.append(f"<span>{esc(repo['language'])}</span>")
    spdx = (repo.get("license") or {}).get("spdx_id")
    if has_text(spdx):
        parts.append(f"<span>{esc(spdx)}</span>")
    meta_html = f'<div class="project-meta">{"".join(parts)}</div>' if parts else ""

    head = f'<div class="project-head"><div class="project-title">{title}</div>{tag_html}</div>'
    return (
        f'<a href="{esc(url)}" target="_blank" rel="noopener" '
        f'class="project-card reveal">{head}{desc_html}{meta_html}</a>'
    )


def render_category(cat_key: str, cat_cfg: dict, repos: list[dict], site: dict) -> str:
    cards = "\n".join(c for c in (render_card(r, site) for r in repos) if c)
    if not cards:
        return ""

    title = esc(cat_cfg.get("title", cat_key.title()))
    desc = esc(cat_cfg.get("description", "")) if has_text(cat_cfg.get("description")) else ""

    return f"""
<section id="{cat_key}" style="background: var(--bg-soft); border-top: 1px solid var(--border-soft); border-bottom: 1px solid var(--border-soft);">
    <div class="container">
        <div class="section-head reveal">
            <h2 class="section-title">{title}</h2>
            {f'<p class="section-desc">{desc}</p>' if desc else ''}
        </div>
        <div class="projects-grid">
{cards}
        </div>
    </div>
</section>"""


def render_stats(repos: list[dict], site: dict) -> str:
    """First cell is always the live repo count. Extra cells come from site.json."""
    items: list[tuple[str, str]] = []
    if repos:
        items.append((f"{len(repos)}+", "Active Projects"))
    for s in site.get("stats_extra", []):
        if isinstance(s, dict) and has_text(s.get("value")) and has_text(s.get("label")):
            items.append((s["value"], s["label"]))
    if not items:
        return ""
    cells = "\n".join(
        f'            <div><div class="stat-num">{esc(v)}</div><div class="stat-label">{esc(l)}</div></div>'
        for v, l in items
    )
    return f"""
<section class="stats">
    <div class="container">
        <div class="stats-grid reveal">
{cells}
        </div>
    </div>
</section>"""


def render_marquee(repos: list[dict], site: dict) -> str:
    cfg = site.get("marquee") or {}
    if not cfg.get("enabled", True):
        return ""
    items: list[str] = []
    for r in repos:
        if has_text(r.get("name")):
            items.append(r["name"])
        for t in (r.get("topics") or [])[:3]:
            if has_text(t):
                items.append(t)
    seen: set[str] = set()
    unique: list[str] = []
    for it in items:
        if it.lower() in seen:
            continue
        seen.add(it.lower())
        unique.append(it)
    while len(unique) < 12:
        unique = unique + unique
    span = lambda t: f'<span class="stack-item"><span class="bullet"></span>{esc(t)}</span>'  # noqa: E731
    track = "\n            ".join(span(t) for t in (unique + unique))
    eyebrow = esc(cfg.get("eyebrow", "Backporting the ecosystem"))
    title = esc(cfg.get("title", "From PEPs to PyPI"))
    desc = esc(cfg.get("description", ""))
    return f"""
<section id="stack">
    <div class="container">
        <div class="section-head reveal">
            <span class="section-eyebrow">{eyebrow}</span>
            <h2 class="section-title">{title}</h2>
            {f'<p class="section-desc">{desc}</p>' if desc else ''}
        </div>
    </div>
    <div class="stack-marquee">
        <div class="stack-track">
            {track}
        </div>
    </div>
</section>"""


# ---------- Pipeline --------------------------------------------------------

def load_site() -> dict:
    return json.loads(SITE_JSON.read_text(encoding="utf-8"))


def build_repos(site: dict, token: str | None) -> list[dict]:
    """Fetch org repos, filter, classify, sort. NO on-disk cache."""
    raw = fetch_org_repos(site["org"], token)
    visible = [r for r in raw if should_show(r, site)]

    by_cat: dict[str, list[dict]] = {k: [] for k in site.get("categories", {})}
    for r in visible:
        cat = classify(r, site)
        cat_cfg = site.get("categories", {}).get(cat, {})
        if cat_cfg.get("hidden_by_default"):
            continue
        by_cat.setdefault(cat, []).append(r)

    ordered: list[tuple[str, list[dict]]] = []
    seen_ids: set[int] = set()
    for cat_key, cat_cfg in site.get("categories", {}).items():
        items = by_cat.get(cat_key, [])
        # Apply explicit ordering by name
        items_sorted = sorted(items, key=lambda r: (
            [(cat_cfg.get("order", []).index(r["name"]) if r["name"] in cat_cfg.get("order", []) else 999)],
            -r.get("stargazers_count", 0),
            r["name"].lower(),
        ))
        ordered.append((cat_key, items_sorted))
        seen_ids.update(id(r) for r in items_sorted)
    return [(cat, items) for cat, items in ordered if items]


def assemble(site: dict, categorized: list[tuple[str, list[dict]]]) -> str:
    if not TEMPLATE.exists():
        raise SystemExit(f"Missing template: {TEMPLATE}")
    tpl = TEMPLATE.read_text(encoding="utf-8")

    all_repos = [r for _, items in categorized for r in items]

    section_blocks = [render_category(k, site["categories"].get(k, {}), items, site)
                      for k, items in categorized]
    stats_block = render_stats(all_repos, site)
    marquee_block = render_marquee(all_repos, site)

    projects_html = "\n".join(b for b in section_blocks if b)

    site_block = site.get("site", {})
    title = esc(site_block.get("title", site["org"]))
    tagline = esc(site_block.get("tagline", ""))

    # Replace all known placeholders. Order matters: title/tagline first,
    # then {{projects}}, then optional stats/marquee.
    tpl = tpl.replace(PLACEHOLDER_TITLE, title)
    tpl = tpl.replace(PLACEHOLDER_TAGLINE, tagline)
    if PLACEHOLDER_PROJECTS not in tpl:
        raise SystemExit(f"Template missing placeholder {PLACEHOLDER_PROJECTS!r}")
    tpl = tpl.replace(PLACEHOLDER_PROJECTS, projects_html)
    for ph, body in (("{{stats}}", stats_block), ("{{marquee}}", marquee_block)):
        if ph in tpl:
            tpl = tpl.replace(ph, body)

    return tpl


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate index.html from live GitHub API")
    p.add_argument("--check", action="store_true",
                   help="Exit 1 if index.html would change (CI guard)")
    p.add_argument("--stdout", action="store_true",
                   help="Write HTML to stdout instead of index.html")
    args = p.parse_args(list(argv) if argv is not None else None)

    site = load_site()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    categorized = build_repos(site, token)

    rendered = assemble(site, categorized)

    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    if args.check:
        existing = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if existing != rendered:
            print("index.html is out of date. Run: python scripts/build.py")
            return 1
        print("index.html is up to date.")
        return 0

    OUTPUT.write_text(rendered, encoding="utf-8")
    total = sum(len(items) for _, items in categorized)
    print(f"wrote {OUTPUT.relative_to(ROOT)} "
          f"({len(rendered):,} bytes, {total} repos across {len(categorized)} categories)")
    return 0


if __name__ == "__main__":
    sys.exit(main())