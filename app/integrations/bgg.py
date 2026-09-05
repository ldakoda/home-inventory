import logging
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
# scans). Serialize all outbound calls with a minimum gap between them, and
# retry with backoff on 429 as a safety net for whatever slips through.
_MIN_REQUEST_GAP_SECONDS = 1.2
_rate_lock = threading.Lock()
_last_request_at = 0.0


def _headers() -> dict:
    settings = get_settings()
    headers = dict(_HEADERS_BASE)
    if settings.bgg_api_token:
        headers["Authorization"] = f"Bearer {settings.bgg_api_token}"
    return headers


def _throttled_get(url: str, headers: dict, timeout: int, max_retries: int = 4) -> requests.Response:
    global _last_request_at

    for attempt in range(max_retries):
        with _rate_lock:
            wait = _MIN_REQUEST_GAP_SECONDS - (time.time() - _last_request_at)
            if wait > 0:
                time.sleep(wait)
            res = requests.get(url, headers=headers, timeout=timeout)
            _last_request_at = time.time()

        if res.status_code == 429:
            retry_after = res.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 1.5 * (attempt + 1)
            time.sleep(delay)
            continue

        return res

    return res


def fetch_bgg_game_matches(game_title: str) -> list[dict]:
    if not game_title or not game_title.strip():
        return []

    clean_title = game_title.strip()
    encoded_q = urllib.parse.quote_plus(clean_title)
    headers = _headers()
    items: list[dict] = []

    try:
        exact_url = f"https://boardgamegeek.com/xmlapi2/search?query={encoded_q}&type=boardgame&exact=1"
        res_exact = _throttled_get(exact_url, headers, timeout=8)
        if res_exact.status_code == 200:
            root = ET.fromstring(res_exact.content)
            for item in root.findall("item"):
                items.append(_parse_search_item(item, clean_title))

        if len(items) < 8:
            search_url = f"https://boardgamegeek.com/xmlapi2/search?query={encoded_q}&type=boardgame"
            res_broad = _throttled_get(search_url, headers, timeout=8)
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


def fetch_bgg_game_details(bgg_id: str, max_retries: int = 3) -> dict:
    if not bgg_id:
        return {}

    url = f"https://boardgamegeek.com/xmlapi2/thing?id={bgg_id}"
    headers = _headers()

    for attempt in range(max_retries):
        try:
            res = _throttled_get(url, headers, timeout=8)
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

    return {
        "image_path": image_path,
        "Number of Players": players,
        "Length of Play": length,
        "Age Rating": age,
    }


def _attr(item: ET.Element, tag: str) -> str:
    elem = item.find(tag)
    return elem.attrib.get("value", "") if elem is not None else ""
