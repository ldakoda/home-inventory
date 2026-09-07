import logging
import urllib.parse

import requests

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "HomeInventoryApp/1.0"}


def search_wikipedia(query: str, num_results: int = 3) -> list[dict]:
    """Fallback metadata source for items nothing else covers -- e.g. outdoor/yard
    games (Spikeball, KanJam, cornhole, Kubb) that BoardGameGeek doesn't catalog
    at all. Wikipedia reliably has a page for these, but only prose: this can
    fill in Description and a photo, never structured fields like player count
    or age rating, since those aren't queryable data on a Wikipedia page.
    """
    query = query.strip()
    if not query:
        return []

    try:
        search_res = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": num_results},
            headers=_HEADERS,
            timeout=8,
        )
        if search_res.status_code != 200:
            return []
        titles = [r["title"] for r in search_res.json().get("query", {}).get("search", [])]
    except requests.RequestException:
        logger.exception("Wikipedia search failed for %r", query)
        return []

    matches = []
    for title in titles:
        try:
            res = requests.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}",
                headers=_HEADERS,
                timeout=8,
            )
            if res.status_code != 200:
                continue
            data = res.json()
            if data.get("type") == "disambiguation":
                continue
            matches.append({
                "Title": data.get("title", title),
                "Description": data.get("extract", ""),
                "image_path": data.get("thumbnail", {}).get("source", ""),
            })
        except requests.RequestException:
            logger.exception("Wikipedia summary failed for %r", title)

    return matches
