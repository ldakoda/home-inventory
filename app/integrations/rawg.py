import difflib
import logging
import re

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


def _entry_from_result(g: dict) -> dict:
    # RAWG can send an explicit `null` for genres/tags on some entries
    # (not just omit the key) -- dict.get's default only kicks in when the
    # key is absent, so a bare `or []` is needed to catch the null case too.
    genres = [x["name"] for x in (g.get("genres") or []) if x.get("name")]
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
    players = _players_from_tags(g.get("tags") or [])
    if players:
        entry["Number of Players"] = players
    if g.get("id"):
        entry["_rawg_id"] = g["id"]
    return entry


_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}


def _norm_title(title: str) -> str:
    t = title.lower().replace("&", " and ")
    t = re.sub(r"[\u2122\u00ae\u00a9]", "", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    words = [_ROMAN.get(w, w) for w in t.split()]
    if words and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def _title_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm_title(a), _norm_title(b)).ratio()


def _carries_platform(g: dict, hint: str) -> bool:
    names = [p.get("platform", {}).get("name", "").lower() for p in (g.get("platforms") or [])]
    return any(hint in n for n in names)


def find_confident_match(title: str, platform_hint: str = "", min_score: float = 0.8) -> tuple[dict | None, float, str]:
    """Best RAWG match for an owned game, or None when it isn't confident enough.

    Returns (entry, score, best_guess_title). The score starts as how closely
    the titles match, loses 0.25 when the owned platform is known but the
    candidate isn't released on it (the wrong game in a franchise), and loses
    0.1 when a different-year runner-up scores about the same (ambiguous).
    entry is only set when score >= min_score.
    """
    settings = get_settings()
    if not settings.rawg_api_key or not title.strip():
        return None, 0.0, ""

    try:
        res = requests.get(
            BASE_URL,
            params={"key": settings.rawg_api_key, "search": title, "page_size": 5},
            timeout=8,
        )
        if res.status_code != 200:
            logger.warning("RAWG search for %r returned HTTP %s", title, res.status_code)
            return None, 0.0, ""
        results = res.json().get("results") or []
    except requests.RequestException:
        logger.exception("RAWG search failed for %r", title)
        return None, 0.0, ""

    hint = platform_hint.strip().lower()
    scored = []
    for g in results:
        score = _title_similarity(title, g.get("name", ""))
        if hint and not _carries_platform(g, hint):
            score -= 0.25
        scored.append((score, g))
    if not scored:
        return None, 0.0, ""

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    if len(scored) > 1:
        runner_score, runner = scored[1]
        if runner_score >= best_score - 0.03 and (runner.get("released") or "")[:4] != (best.get("released") or "")[:4]:
            best_score -= 0.1

    best_score = max(0.0, round(best_score, 2))
    if best_score < min_score:
        return None, best_score, best.get("name", "")

    entry = _entry_from_result(best)
    rawg_id = entry.pop("_rawg_id", None)
    if rawg_id:
        description = _fetch_description(rawg_id, settings.rawg_api_key)
        if description:
            entry["Description"] = description
    return entry, best_score, best.get("name", "")


def search_rawg(query: str, num_results: int = 5, platform_hint: str = "") -> list[dict]:
    """Video-game metadata source -- RAWG is the free option with the best
    coverage for this (IGDB needs a Twitch developer app; MobyGames' API is
    paid). Cover art/genre/ESRB rating/release year come straight from the
    search results; only the full description needs a second per-candidate
    call, same two-step pattern OMDb/BGG already use.

    platform_hint (e.g. "PlayStation 4"): a rebooted franchise entry often
    shares its exact title with an older game in the series (the 2018 PS4
    "God of War" vs. the 2005 PS2 original both being named "God of War") --
    RAWG's relevance ranking doesn't know which one an owned copy actually
    is, so when a platform is already known, candidates that carry it are
    moved to the front (stable otherwise) rather than blindly trusting
    result order.
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

    if platform_hint.strip():
        hint = platform_hint.strip().lower()
        def carries_hint(g):
            names = [p.get("platform", {}).get("name", "").lower() for p in (g.get("platforms") or [])]
            return any(hint in n for n in names)
        raw_results = sorted(raw_results, key=lambda g: not carries_hint(g))

    matches = [_entry_from_result(g) for g in raw_results]

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
            return (res.json().get("description_raw") or "").strip()
    except requests.RequestException:
        logger.exception("RAWG detail fetch failed for id=%s", game_id)
    return ""
