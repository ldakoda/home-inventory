import logging
import re
import urllib.parse

import requests
from ddgs import DDGS

logger = logging.getLogger(__name__)


def search_multiple_web_images(query_text: str, num_results: int = 8) -> list[str]:
    """Multi-tier fallback (DuckDuckGo -> simplified DuckDuckGo -> Wikimedia) so a
    narrow/misspelled query still returns something.
    """
    if not query_text or not query_text.strip():
        return []

    raw_query = query_text.strip()
    clean_q = re.sub(r"[^\w\s]", "", raw_query)
    results: list[str] = []

    _try_ddg(clean_q, num_results, results)

    if len(results) < 3 and len(clean_q.split()) > 2:
        short_q = " ".join(clean_q.split()[:2])
        _try_ddg(short_q, num_results, results)

    if len(results) < 3:
        for search_term in [clean_q, " ".join(clean_q.split()[:2])]:
            _try_wikimedia(search_term, results)
            if len(results) >= num_results:
                break

    return results[:num_results]


def _try_ddg(query: str, num_results: int, results: list[str]) -> None:
    try:
        with DDGS() as ddgs:
            for r in ddgs.images(query, max_results=num_results):
                img_url = r.get("image") or r.get("thumbnail")
                if img_url and img_url not in results:
                    results.append(img_url)
    except Exception:
        logger.exception("DuckDuckGo image search failed for %r", query)


def _try_wikimedia(search_term: str, results: list[str]) -> None:
    try:
        encoded_q = urllib.parse.quote_plus(search_term)
        wiki_url = (
            "https://commons.wikimedia.org/w/api.php?action=query&generator=search"
            f"&gsrsearch={encoded_q}&gsrlimit=10&prop=pageimages&pithumbsize=500&format=json"
        )
        res = requests.get(wiki_url, headers={"User-Agent": "HomeInventoryApp/1.0"}, timeout=5).json()
        pages = res.get("query", {}).get("pages", {})
        for _, page_data in pages.items():
            thumb = page_data.get("thumbnail", {}).get("source")
            if thumb and thumb not in results:
                results.append(thumb)
    except requests.RequestException:
        logger.exception("Wikimedia image search failed for %r", search_term)
