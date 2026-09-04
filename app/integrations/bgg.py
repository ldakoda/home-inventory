import logging
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


def _headers() -> dict:
    settings = get_settings()
    headers = dict(_HEADERS_BASE)
    if settings.bgg_api_token:
        headers["Authorization"] = f"Bearer {settings.bgg_api_token}"
    return headers


def fetch_bgg_game_matches(game_title: str) -> list[dict]:
    if not game_title or not game_title.strip():
        return []

    clean_title = game_title.strip()
    encoded_q = urllib.parse.quote_plus(clean_title)
    headers = _headers()
    items: list[dict] = []

    try:
        exact_url = f"https://boardgamegeek.com/xmlapi2/search?query={encoded_q}&type=boardgame&exact=1"
        res_exact = requests.get(exact_url, headers=headers, timeout=8)
        if res_exact.status_code == 200:
            root = ET.fromstring(res_exact.content)
            for item in root.findall("item"):
                items.append(_parse_search_item(item, clean_title))

        if len(items) < 8:
            search_url = f"https://boardgamegeek.com/xmlapi2/search?query={encoded_q}&type=boardgame"
            res_broad = requests.get(search_url, headers=headers, timeout=8)
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
            res = requests.get(url, headers=headers, timeout=8)
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
