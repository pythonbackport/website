#!/usr/bin/env python3
"""
build.py — Generate index.html directly from the live GitHub API.

There is no config file. Every byte of repository metadata on the
generated page comes from https://api.github.com at build time:

  * Site name + tagline -> GET /orgs/<org>
  * Repositories       -> GET /orgs/<org>/repos
  * Stars, language, license, topics, description -> per-repo fields

The only judgement calls the script makes are:
  * Skip archived / fork / private repos.
  * Skip the website repo itself (detected by topics containing "website"
    or by name matching the org, or by description containing "website").
  * Bucket repos into two categories based on their topics + description:
      - "Community"  : repo is the discussion hub (name == "community"
                       or description mentions "discussion" / "community").
      - "Backports"  : everything else with a description.
  * Render in stars-descending order within each bucket.
  * If a repo field is missing in the API response, the corresponding
    DOM block is dropped. Never substituted with placeholder text.

Usage:
  python scripts/build.py [--org pythonbackport]
  python scripts/build.py --check
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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "index.template.html"
OUTPUT = ROOT / "index.html"

API_BASE = "https://api.github.com"
USER_AGENT = "pythonbackport-website/1.0"

DEFAULT_ORG = "pythonbackport"


# ---------- HTTP ------------------------------------------------------------

def gh_get(url: str, token: str | None) -> tuple[object, dict[str, str]]:
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
            raise SystemExit(f"GitHub API 404 for {url}. Is the org name correct?") from e
        raise


def gh_list(url: str, token: str | None) -> list[object]:
    """Walk every page of a GitHub list endpoint."""
    out: list[object] = []
    while url:
        data, headers = gh_get(url, token)
        if isinstance(data, list):
            out.extend(data)
        else:
            return [data]
        nxt = None
        for part in headers.get("link", "").split(","):
            m = re.match(r"\s*<([^>]+)>;\s*rel=\"next\"", part)
            if m:
                nxt = m.group(1)
                break
        url = nxt
    return out


def fetch_org(org: str, token: str | None) -> dict:
    url = f"{API_BASE}/orgs/{org}"
    data, _ = gh_get(url, token)
    if not isinstance(data, dict):
        raise SystemExit(f"Unexpected response from /orgs/{org}")
    return data


def fetch_repos(org: str, token: str | None) -> list[dict]:
    url = f"{API_BASE}/orgs/{org}/repos?per_page=100&type=public&sort=updated"
    data = gh_list(url, token)
    return [r for r in data if isinstance(r, dict)]


# ---------- Classification --------------------------------------------------
# Pure functions. Every input is a dict from the API, every output is a
# string or bool. No hard-coded project-name lookup tables.

def is_website_repo(repo: dict, org_name: str) -> bool:
    name = (repo.get("name") or "").lower()
    if name == org_name.lower():
        return True
    if name in {"website", ".github"}:
        return True
    desc = (repo.get("description") or "").lower()
    if "website" in desc or "landing page" in desc or "this website" in desc:
        return True
    topics = " ".join(t.lower() for t in (repo.get("topics") or []))
    if "website" in topics or "github-pages" in topics:
        return True
    return False


def should_show_repo(repo: dict) -> bool:
    if repo.get("private") or repo.get("archived") or repo.get("fork"):
        return False
    return True


def classify_repo(repo: dict) -> str:
    """Bucket into 'community' or 'backports' based on live topics + desc."""
    name = (repo.get("name") or "").lower()
    desc = (repo.get("description") or "").lower()
    topics = " ".join(t.lower() for t in (repo.get("topics") or []))
    haystack = f"{name} {desc} {topics}"
    if any(s in haystack for s in ("discussion", "discussions", "forum")):
        return "community"
    return "backports"


# ---------- HTML helpers ----------------------------------------------------

def esc(s: object) -> str:
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                  .replace(">", "&gt;").replace('"', "&quot;"))


def has_text(s: object) -> bool:
    return isinstance(s, str) and s.strip() != ""


def trim(text: str | None, n: int) -> str | None:
    if not has_text(text):
        return text
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def render_repo_card(repo: dict) -> str:
    """Render one <a class="project-card"> from a raw GitHub repo payload.

    Drops any sub-block whose field is missing. No placeholder words.
    """
    name = repo.get("name")
    url = repo.get("html_url")
    if not (has_text(name) and has_text(url)):
        return ""

    desc = trim(repo.get("description"), 220)

    tag: str | None = None
    for t in repo.get("topics") or []:
        if isinstance(t, str) and t.lower().startswith("pep"):
            tag = t.upper()
            break
    if not has_text(tag) and has_text(repo.get("description")):
        m = re.search(r"\bPEP\s*\d{2,4}\b", repo["description"], re.IGNORECASE)
        if m:
            tag = m.group(0).upper()

    tag_html = f'<span class="project-tag">{esc(tag)}</span>' if has_text(tag) else ""
    desc_html = f'<div class="project-desc">{esc(desc)}</div>' if has_text(desc) else ""

    parts: list[str] = []
    stars = repo.get("stargazers_count")
    if isinstance(stars, int):
        parts.append(f"<span>★ {stars:,}</span>")
    topics_clean = [t for t in (repo.get("topics") or []) if has_text(t)]
    if topics_clean:
        chips = " · ".join(esc(t) for t in topics_clean[:3])
        parts.append(f"<span>{chips}</span>")
    elif has_text(repo.get("language")):
        parts.append(f"<span>{esc(repo['language'])}</span>")
    spdx = (repo.get("license") or {}).get("spdx_id")
    if has_text(spdx):
        parts.append(f"<span>{esc(spdx)}</span>")
    meta_html = f'<div class="project-meta">{"".join(parts)}</div>' if parts else ""

    head = f'<div class="project-head"><div class="project-title"><span>{esc(name)}</span></div>{tag_html}</div>'
    return (
        f'<a href="{esc(url)}" target="_blank" rel="noopener" '
        f'class="project-card reveal">{head}{desc_html}{meta_html}</a>'
    )


def render_category(cat_key: str, repos: list[dict]) -> str:
    cards = "\n".join(c for c in (render_repo_card(r) for r in repos) if c)
    if not cards:
        return ""
    if cat_key == "community":
        label, desc = "Community", "Discussion hubs and meta-repositories."
    else:
        label, desc = "Backports", "Libraries that reimplement or backport features of modern CPython."
    return f"""
<section id="{cat_key}" style="background: var(--bg-soft); border-top: 1px solid var(--border-soft); border-bottom: 1px solid var(--border-soft);">
    <div class="container">
        <div class="section-head reveal">
            <h2 class="section-title">{esc(label)}</h2>
            <p class="section-desc">{esc(desc)}</p>
        </div>
        <div class="projects-grid">
{cards}
        </div>
    </div>
</section>"""


def render_marquee(repos: list[dict]) -> str:
    items: list[str] = []
    for r in repos:
        if has_text(r.get("name")):
            items.append(r["name"])
        for t in (r.get("topics") or [])[:3]:
            if has_text(t):
                items.append(t)
    if not items:
        return ""
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
    return f"""
<section id="stack">
    <div class="container">
        <div class="section-head reveal">
            <span class="section-eyebrow">Backporting the ecosystem</span>
            <h2 class="section-title">From PEPs to PyPI</h2>
            <p class="section-desc">We work across the entire Python ecosystem — interpreters, compilers, libraries and tooling.</p>
        </div>
    </div>
    <div class="stack-marquee">
        <div class="stack-track">
            {track}
        </div>
    </div>
</section>"""


def render_stats(repos: list[dict]) -> str:
    items: list[tuple[str, str]] = []
    if repos:
        items.append((f"{len(repos)}+", "Active Projects"))
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


# ---------- Pipeline --------------------------------------------------------

def build_site(org: str, token: str | None) -> tuple[dict, list[tuple[str, list[dict]]]]:
    """Fetch live data and produce (org_meta, categorized_repos)."""
    org_meta = fetch_org(org, token)
    raw_repos = fetch_repos(org, token)

    visible = [r for r in raw_repos
               if should_show_repo(r) and not is_website_repo(r, org_meta.get("login") or org)]

    groups: dict[str, list[dict]] = {"community": [], "backports": []}
    for r in visible:
        groups[classify_repo(r)].append(r)

    for cat in groups.values():
        cat.sort(key=lambda r: (-(r.get("stargazers_count") or 0), (r.get("name") or "").lower()))

    ordered = [(k, groups[k]) for k in ("backports", "community") if groups[k]]
    return org_meta, ordered


def assemble(org_meta: dict, categorized: list[tuple[str, list[dict]]]) -> str:
    if not TEMPLATE.exists():
        raise SystemExit(f"Missing template: {TEMPLATE}")
    tpl = TEMPLATE.read_text(encoding="utf-8")

    all_repos = [r for _, items in categorized for r in items]

    title = org_meta.get("name") or org_meta.get("login") or ""
    tagline = org_meta.get("description") or ""

    section_blocks = [render_category(k, items) for k, items in categorized]
    projects_html = "\n".join(b for b in section_blocks if b)
    stats_html = render_stats(all_repos)
    marquee_html = render_marquee(all_repos)

    tpl = tpl.replace("{{site_title}}", esc(title))
    tpl = tpl.replace("{{site_tagline}}", esc(tagline))
    if "{{projects}}" not in tpl:
        raise SystemExit("Template missing {{projects}} placeholder")
    tpl = tpl.replace("{{projects}}", projects_html)
    for ph, body in (("{{stats}}", stats_html), ("{{marquee}}", marquee_html)):
        if ph in tpl:
            tpl = tpl.replace(ph, body)
    return tpl


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate index.html from live GitHub API")
    p.add_argument("--org", default=DEFAULT_ORG,
                   help=f"GitHub org/user to fetch (default: {DEFAULT_ORG})")
    p.add_argument("--check", action="store_true",
                   help="Exit 1 if index.html would change (CI guard)")
    p.add_argument("--stdout", action="store_true",
                   help="Write HTML to stdout instead of index.html")
    args = p.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    org_meta, categorized = build_site(args.org, token)
    rendered = assemble(org_meta, categorized)

    if args.stdout:
        sys.stdout.buffer.write(rendered.encode("utf-8"))
        return 0

    if args.check:
        existing = OUTPUT.read_bytes() if OUTPUT.exists() else b""
        if existing != rendered.encode("utf-8"):
            print("index.html is out of date. Run: python scripts/build.py")
            return 1
        print("index.html is up to date.")
        return 0

    OUTPUT.write_bytes(rendered.encode("utf-8"))
    total = sum(len(items) for _, items in categorized)
    print(f"wrote {OUTPUT.relative_to(ROOT)} "
          f"({len(rendered):,} bytes, {total} repos across {len(categorized)} categories)")
    return 0


if __name__ == "__main__":
    sys.exit(main())