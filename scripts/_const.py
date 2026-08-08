from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_JSON = ROOT / "site.json"
TEMPLATE = ROOT / "templates" / "index.template.html"
OUTPUT = ROOT / "index.html"

API_BASE = "https://api.github.com"
USER_AGENT = "pythonbackport-website/1.0"

PLACEHOLDER_PROJECTS = "{{projects}}"
PLACEHOLDER_TITLE = "{{site_title}}"
PLACEHOLDER_TAGLINE = "{{site_tagline}}"
