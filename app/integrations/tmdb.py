import logging

import requests

from app.config import get_settings

logger = logging.getLogger(__name__)

POSTER_BASE = "https://image.tmdb.org/t/p/w500"


def search_tmdb(query: str, num_results: int = 8) -> list[dict]:
    """Search TMDb for the given title. Tries collections first -- a box-set/franchise
    name like "X-Men Trilogy" or "N-Movie Collection" matches a real TMDb collection far
    better than it ever would a single-title search -- then falls back to individual
    movies. Collection hits carry only a title and poster (a collection bundles several
    films, so there's no single rating/genre/runtime for it); movie hits get a second
    per-title lookup for Rating, Genre, Length of Movie, and Description too, the same
    two-step pattern OMDb/BGG already use.
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

    # Collections don't carry their own single rating/genre/runtime (they bundle
    # several films) -- Type is the one field that's still meaningful for them.
    for c in results:
        c["Type"] = "Collection"

    if len(results) < num_results:
        try:
            res = requests.get(
                "https://api.themoviedb.org/3/search/movie",
                params={"api_key": settings.tmdb_api_key, "query": query},
                timeout=6,
            )
            if res.status_code == 200:
                for m in res.json().get("results", [])[: num_results - len(results)]:
                    entry = {
                        "Title": m.get("title", ""),
                        "Year Released": (m.get("release_date") or "")[:4],
                        "Type": "Movie",
                        "image_path": f"{POSTER_BASE}{m['poster_path']}" if m.get("poster_path") else "",
                    }
                    if m.get("id"):
                        entry.update(_fetch_movie_details(m["id"], settings.tmdb_api_key))
                    results.append(entry)
        except requests.RequestException:
            logger.exception("TMDb movie search failed for %r", query)

    return results[:num_results]


def _fetch_movie_details(movie_id: int, api_key: str) -> dict:
    """OMDb's per-title Rating/Genre/Length/Description, sourced from TMDb instead --
    the search endpoint above only returns title/year/poster, so this is a second
    call per candidate (same two-step pattern OMDb/BGG already use) to fill in the
    rest of what the missing-metadata check actually looks for.
    """
    details: dict = {}
    try:
        res = requests.get(f"https://api.themoviedb.org/3/movie/{movie_id}", params={"api_key": api_key}, timeout=6)
        if res.status_code == 200:
            data = res.json()
            genres = [g["name"] for g in data.get("genres", []) if g.get("name")]
            if genres:
                details["Genre"] = ", ".join(genres)
            if data.get("runtime"):
                details["Length of Movie"] = f"{data['runtime']} min"
            if data.get("overview"):
                details["Description"] = data["overview"]
    except requests.RequestException:
        logger.exception("TMDb movie details failed for id=%s", movie_id)

    try:
        res = requests.get(
            f"https://api.themoviedb.org/3/movie/{movie_id}/release_dates", params={"api_key": api_key}, timeout=6
        )
        if res.status_code == 200:
            for country in res.json().get("results", []):
                if country.get("iso_3166_1") != "US":
                    continue
                for release in country.get("release_dates", []):
                    if release.get("certification"):
                        details["Rating"] = release["certification"]
                        break
                break
    except requests.RequestException:
        logger.exception("TMDb release_dates failed for id=%s", movie_id)

    return details
