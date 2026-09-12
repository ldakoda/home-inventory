import logging

import requests

from app.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.rawg.io/api/games"

# RAWG has no structured min/max-players field the way BGG does for board games --
# store pages just say "Single Player" or "Multiplayer" as marketing copy. These
# tags are the closest approximation available, so "Number of Players" is built
# from whichever of them the game actually carries, best-effort only.
_PLAYER_TAGS = [
    ("Massively Multiplayer", "Massively Multiplayer"),
    ("Multiplayer", "Multiplayer"),
    ("Co-op", "Co-op"),
    ("Online Co-Op", "Online Co-op"),
    ("Local Co-Op", "Local Co-op"),
    ("Split Screen", "Split Screen"),
    ("Singleplayer", "Single Player"),
]


def _players_from_tags(tags: list[dict]) -> str:
    names = {t.get("name", "") for t in tags}
    found = []
    for tag_name, label in _PLAYER_TAGS:
        if tag_name in names and label not in found:
            found.append(label)
    return ", ".join(found)


def search_rawg(query: str, num_results: int = 5) -> list[dict]:
    """Video-game metadata source -- RAWG is the free option with the best
    coverage for this (IGDB needs a Twitch developer app; MobyGames' API is
    paid). Cover art/genre/ESRB rating/release year come straight from the
    search results; only the full description needs a second per-candidate
    call, same two-step pattern OMDb/BGG already use.
    """
    settings = get_settings()
    if not settings.rawg_api_key or not query.strip():
        return []

    try:
        res = requests.get(
            BASE_URL,
            params={"key": settings.rawg_api_key, "search": query, "page_size": num_results},
            timeout=8,
        )
        if res.status_code != 200:
            return []
        raw_results = res.json().get("results", [])
    except requests.RequestException:
        logger.exception("RAWG search failed for %r", query)
        return []

    matches = []
    for g in raw_results:
        genres = [x["name"] for x in g.get("genres", []) if x.get("name")]
        entry = {
            "Title": g.get("name", ""),
            "Year Released": (g.get("released") or "")[:4],
            "image_path": g.get("background_image") or "",
        }
        if genres:
            entry["Genre"] = ", ".join(genres)
        esrb = g.get("esrb_rating")
        if esrb and esrb.get("name"):
            entry["ESRB Rating"] = esrb["name"]
        players = _players_from_tags(g.get("tags", []))
        if players:
            entry["Number of Players"] = players
        if g.get("id"):
            entry["_rawg_id"] = g["id"]
        matches.append(entry)

    if matches and matches[0].get("_rawg_id"):
        details = _fetch_description(matches[0].pop("_rawg_id"), settings.rawg_api_key)
        if details:
            matches[0]["Description"] = details

    for m in matches:
        m.pop("_rawg_id", None)

    return matches


def _fetch_description(game_id: int, api_key: str) -> str:
    try:
        res = requests.get(f"{BASE_URL}/{game_id}", params={"key": api_key}, timeout=8)
        if res.status_code == 200:
            return res.json().get("description_raw", "").strip()
    except requests.RequestException:
        logger.exception("RAWG detail fetch failed for id=%s", game_id)
    return ""
