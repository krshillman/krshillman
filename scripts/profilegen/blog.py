"""Latest posts from an RSS 2.0 or Atom feed."""
from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

ATOM = "{http://www.w3.org/2005/Atom}"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = parsedate_to_datetime(value)  # RSS (RFC 822)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))  # Atom (RFC 3339)
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_feed(xml_text: str, now: datetime, limit: int) -> list[dict]:
    """Newest-first posts, skipping anything scheduled in the future."""
    root = ET.fromstring(xml_text)
    posts = []
    for item in root.iter("item"):
        posts.append({
            "title": (item.findtext("title") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "published": _parse_date(item.findtext("pubDate")),
        })
    for entry in root.iter(f"{ATOM}entry"):
        link = entry.find(f"{ATOM}link[@rel='alternate']")
        if link is None:
            link = entry.find(f"{ATOM}link")
        posts.append({
            "title": (entry.findtext(f"{ATOM}title") or "").strip(),
            "link": (link.get("href", "") if link is not None else "").strip(),
            "published": _parse_date(entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated")),
        })
    posts = [p for p in posts if p["title"] and p["link"] and p["published"] and p["published"] <= now]
    posts.sort(key=lambda p: p["published"], reverse=True)
    return posts[:limit]


def fetch_posts(url: str, now: datetime, limit: int) -> list[dict]:
    request = urllib.request.Request(url, headers={"User-Agent": "profilegen"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return parse_feed(response.read().decode("utf-8", "replace"), now, limit)
