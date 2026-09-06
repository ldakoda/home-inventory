import html
import logging
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET

import requests

from app.config import get_settings

logger = logging.getLogger(__name__)

_HEADERS_BASE = {
    "User-Agent": "HomeInventoryApp/1.0",
    "Accept": "text/xml,application/xml",
}

# BGG rate-limits per API token and returns 429 once several requests land
# close together (observed even with ~1s client-side spacing under bulk
# scans). Serialize outbound calls with a minimum gap between them, and retry
# with backoff on 429 as a safety net for whatever slips through.
#
# Two independent lanes share this backoff logic: the missing-metadata panel's
# bulk background scan (~60 rows) stays on a slow lane so it doesn't itself
# trigger rate limiting, while a real user click (Select, click-to-load
# thumbnail) gets its own fast lane so it never sits queued behind the bulk
# scan's minutes-long backlog -- that queuing was making "Select" feel broken
# during a scan. Both lanes hit the same BGG token, so a click can still
# occasionally collide and get a 429, but the per-request retry absorbs that.
class _RateLimiter:
    def __init__(self, min_gap_seconds: float):
        self._lock = threading.Lock()
        self._last_at = 0.0
        self._min_gap = min_gap_seconds

    def get(self, url: str, headers: dict, timeout: int) -> requests.Response:
        with self._lock:
            wait = self._min_gap - (time.time() - self._last_at)
            if wait > 0:
                time.sleep(wait)
            res = requests.get(url, headers=headers, timeout=timeout)
            self._last_at = time.time()
            return res


_bulk_limiter = _RateLimiter(1.2)
_priority_limiter = _RateLimiter(0.4)


def _headers() -> dict:
    settings = get_settings()
    headers = dict(_HEADERS_BASE)
    if settings.bgg_api_token:
        headers["Authorization"] = f"Bearer {settings.bgg_api_token}"
    return headers


def _throttled_get(url: str, headers: dict, timeout: int, max_retries: int = 4, priority: bool = False) -> requests.Response:
    limiter = _priority_limiter if priority else _bulk_limiter
    res = None

    for attempt in range(max_retries):
        res = limiter.get(url, headers, timeout)

        if res.status_code == 429:
            retry_after = res.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 1.5 * (attempt + 1)
            time.sleep(delay)
            continue

        return res

    return res


def fetch_bgg_game_matches(game_title: str, priority: bool = False, fast: bool = False) -> list[dict]:
    """fast=True skips the separate exact-match request and goes straight to the
    broad search, which already includes exact matches -- verified to return the
    same top result in the overwhelming majority of cases (the broad results are
    sorted by closeness of length to the query, which puts a real exact match
    first). Halves the BGG calls for the missing-metadata panel's ~60-row bulk
    scan, which otherwise took several minutes even with per-call throttling.
    """
    if not game_title or not game_title.strip():
        return []

    clean_title = game_title.strip()
    encoded_q = urllib.parse.quote_plus(clean_title)
    headers = _headers()
    items: list[dict] = []

    try:
        if not fast:
            exact_url = f"https://boardgamegeek.com/xmlapi2/search?query={encoded_q}&type=boardgame&exact=1"
            res_exact = _throttled_get(exact_url, headers, timeout=8, priority=priority)
            if res_exact.status_code == 200:
                root = ET.fromstring(res_exact.content)
                for item in root.findall("item"):
                    items.append(_parse_search_item(item, clean_title))

        if len(items) < 8:
            search_url = f"https://boardgamegeek.com/xmlapi2/search?query={encoded_q}&type=boardgame"
            res_broad = _throttled_get(search_url, headers, timeout=8, priority=priority)
            if res_broad.status_code == 200:
                root = ET.fromstring(res_broad.content)
                existing_ids = {i["id"] for i in items}
                broad_items = [
                    _parse_search_item(item, clean_title)
                    for item in root.findall("item")
                    if item.attrib.get("id") not in existing_ids
                ]
                broad_items.sort(key=lambda x: (abs(len(x["name"]) - len(clean_title)), x["name"].lower()))
                items.extend(broad_items)

        return items[:8]
    except (requests.RequestException, ET.ParseError):
        logger.exception("BGG search failed for %r", game_title)
        return []


def _parse_search_item(item: ET.Element, fallback_name: str) -> dict:
    name_elem = item.find("name")
    year_elem = item.find("yearpublished")
    return {
        "id": item.attrib.get("id"),
        "name": name_elem.attrib.get("value") if name_elem is not None else fallback_name,
        "year": year_elem.attrib.get("value") if year_elem is not None else "",
    }


def fetch_bgg_game_details(bgg_id: str, max_retries: int = 3, priority: bool = False) -> dict:
    if not bgg_id:
        return {}

    url = f"https://boardgamegeek.com/xmlapi2/thing?id={bgg_id}"
    headers = _headers()

    for attempt in range(max_retries):
        try:
            res = _throttled_get(url, headers, timeout=8, priority=priority)
            if res.status_code == 202:
                time.sleep(2 * (attempt + 1))
                continue

            if res.status_code == 200:
                return _parse_details(res.content)
        except (requests.RequestException, ET.ParseError):
            logger.exception("BGG detail fetch failed for id=%s", bgg_id)
            break

    return {}


def _parse_details(xml_content: bytes) -> dict:
    root = ET.fromstring(xml_content)
    item = root.find("item")
    if item is None:
        return {}

    image_elem = item.find("image")
    if image_elem is None or not image_elem.text:
        image_elem = item.find("thumbnail")
    image_path = image_elem.text if image_elem is not None else ""

    min_p = _attr(item, "minplayers")
    max_p = _attr(item, "maxplayers")
    players = f"{min_p}-{max_p} Players" if min_p and max_p and min_p != max_p else f"{min_p} Players"

    min_t = _attr(item, "minplaytime")
    max_t = _attr(item, "maxplaytime")
    length = f"{min_t}-{max_t} min" if min_t and max_t and min_t != max_t else f"{min_t} min"

    age = _attr(item, "minage")
    if age and age != "0":
        age = f"{age}+"

    desc_elem = item.find("description")
    description = _clean_description(desc_elem.text) if desc_elem is not None and desc_elem.text else ""

    return {
        "image_path": image_path,
        "Number of Players": players,
        "Length of Play": length,
        "Age Rating": age,
        "Description": description,
    }


def _clean_description(text: str) -> str:
    # BGG descriptions are sometimes HTML-entity-encoded a second time on top of
    # the XML encoding ElementTree already decoded (e.g. a literal "&amp;" left
    # in the text), and often carry embedded line breaks as blank-line runs.
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    return text


def _attr(item: ET.Element, tag: str) -> str:
    elem = item.find(tag)
    return elem.attrib.get("value", "") if elem is not None else ""
