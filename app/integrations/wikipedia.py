import logging
import time
import urllib.parse

import requests

logger = logging.getLogger(__name__)

# Identifies the app and links back to it per Wikimedia's API etiquette --
# generic/anonymous User-Agents from cloud datacenter IP ranges (which is what
# Cloud Run's outbound traffic looks like to Wikimedia) are more likely to get
# throttled.
_HEADERS = {"User-Agent": "HomeInventoryApp/1.0 (https://github.com/ldakoda/home-inventory)"}


def _get_with_retry(url: str, params: dict | None = None, max_retries: int = 2) -> requests.Response | None:
    # Wikimedia's shared API is occasionally slow to respond or briefly
    # rate-limits a request from Cloud Run's shared/rotating egress IP pool --
    # observed in practice as an otherwise-healthy request coming back empty.
    # One retry with a short backoff absorbs that without adding the
    # dedicated rate-limiter machinery bgg.py needs (this path fires far less
    # often -- only when BGG has no match at all).
    res = None
    for attempt in range(max_retries):
        try:
            res = requests.get(url, params=params, headers=_HEADERS, timeout=8)
        except requests.RequestException:
            logger.exception("Wikipedia request failed for %s", url)
            return None
        if res.status_code == 200:
            return res
        if attempt < max_retries - 1:
            time.sleep(1.5 * (attempt + 1))
    logger.warning("Wikipedia request to %s kept returning %s after retries", url, res.status_code if res else "?")
    return None


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

    search_res = _get_with_retry(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": num_results},
    )
    if search_res is None:
        return []
    titles = [r["title"] for r in search_res.json().get("query", {}).get("search", [])]

    matches = []
    for title in titles:
        res = _get_with_retry(f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}")
        if res is None:
            continue
        data = res.json()
        if data.get("type") == "disambiguation":
            continue
        matches.append({
            "Title": data.get("title", title),
            "Description": data.get("extract", ""),
            "image_path": data.get("thumbnail", {}).get("source", ""),
        })

    return matches
