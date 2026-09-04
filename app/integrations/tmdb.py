import logging

import requests

from app.config import get_settings

logger = logging.getLogger(__name__)

POSTER_BASE = "https://image.tmdb.org/t/p/w500"


def search_tmdb(query: str, num_results: int = 8) -> list[dict]:
    """Search TMDb for the given title. Tries collections first -- a box-set/franchise
    name like "X-Men Trilogy" or "N-Movie Collection" matches a real TMDb collection far
    better than it ever would a single-title search -- then falls back to individual
    movies. Collection hits carry only a title and poster (no rating/genre/year);
    movie hits add a year.
    """
    settings = get_settings()
    if not settings.tmdb_api_key or not query.strip():
        return []

    results: list[dict] = []

    try:
        res = requests.get(
            "https://api.themoviedb.org/3/search/collection",
            params={"api_key": settings.tmdb_api_key, "query": query},
            timeout=6,
        )
        if res.status_code == 200:
            for c in res.json().get("results", []):
                if c.get("poster_path"):
                    results.append({
                        "Title": c.get("name", ""),
                        "image_path": f"{POSTER_BASE}{c['poster_path']}",
                    })
    except requests.RequestException:
        logger.exception("TMDb collection search failed for %r", query)

    if len(results) < num_results:
        try:
            res = requests.get(
                "https://api.themoviedb.org/3/search/movie",
                params={"api_key": settings.tmdb_api_key, "query": query},
                timeout=6,
            )
            if res.status_code == 200:
                for m in res.json().get("results", [])[: num_results - len(results)]:
                    results.append({
                        "Title": m.get("title", ""),
                        "Year Released": (m.get("release_date") or "")[:4],
                        "image_path": f"{POSTER_BASE}{m['poster_path']}" if m.get("poster_path") else "",
                    })
        except requests.RequestException:
            logger.exception("TMDb movie search failed for %r", query)

    return results[:num_results]
