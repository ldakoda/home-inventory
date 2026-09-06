import logging
import urllib.parse

import requests

from app.config import get_settings

logger = logging.getLogger(__name__)


def fetch_omdb_movie_matches(movie_title: str) -> list[dict]:
    settings = get_settings()
    if not settings.omdb_api_key or not movie_title or not movie_title.strip():
        return []

    try:
        encoded_q = urllib.parse.quote_plus(movie_title.strip())
        url = f"http://www.omdbapi.com/?s={encoded_q}&apikey={settings.omdb_api_key}"
        res = requests.get(url, timeout=5).json()

        if res.get("Response") != "True":
            return []

        matches = []
        for item in res.get("Search", [])[:6]:
            d_url = f"http://www.omdbapi.com/?i={item['imdbID']}&apikey={settings.omdb_api_key}"
            d_res = requests.get(d_url, timeout=4).json()
            if d_res.get("Response") == "True":
                matches.append({
                    "Title": d_res.get("Title", ""),
                    "Year Released": d_res.get("Year", ""),
                    "Rating": d_res.get("Rated", ""),
                    "Length of Movie": d_res.get("Runtime", ""),
                    "Type": d_res.get("Type", "movie").capitalize(),
                    "Genre": d_res.get("Genre", ""),
                    "Description": d_res.get("Plot") if d_res.get("Plot") not in (None, "N/A") else "",
                    "image_path": d_res.get("Poster") if d_res.get("Poster") not in (None, "N/A") else "",
                })
        return matches
    except requests.RequestException:
        logger.exception("OMDb search failed for %r", movie_title)
        return []
