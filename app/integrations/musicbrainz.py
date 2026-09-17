import logging
import time

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://musicbrainz.org/ws/2"
COVER_ART_URL = "https://coverartarchive.org/release"

# MusicBrainz's usage policy requires a descriptive User-Agent and caps
# anonymous requests at ~1/second -- a stricter limit than any other
# integration here, since a single "search" resolves into a chain of
# release-group -> release -> tracklist -> cover-art calls (see search_musicbrainz).
_HEADERS = {"User-Agent": "HomeInventoryApp/1.0 (https://github.com/ldakoda/home-inventory)"}
_MIN_GAP_SECONDS = 1.1
_last_request_at = 0.0


def _throttled_get(url: str, params: dict) -> requests.Response | None:
    global _last_request_at
    wait = _MIN_GAP_SECONDS - (time.time() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    try:
        res = requests.get(url, params=params, headers=_HEADERS, timeout=10)
    except requests.RequestException:
        logger.exception("MusicBrainz request failed for %s", url)
        return None
    finally:
        _last_request_at = time.time()
    return res if res.status_code == 200 else None


def search_musicbrainz(query: str, num_results: int = 5) -> list[dict]:
    """Vinyl-record metadata source. MusicBrainz needs no API key/signup (unlike
    RAWG for video games), but its data model is release-group (the abstract
    album) -> release (a specific pressing/format) -> tracks -- there's no
    single call that returns a tracklist, so this is a chain of throttled
    requests. Only the top candidate gets that full chain (Track List,
    Description-free Genre, cover art via the companion Cover Art Archive);
    the rest stay at the cheap release-group level, same "top result gets full
    detail, others are cheap" pattern bgg.py already uses.
    """
    query = query.strip()
    if not query:
        return []

    res = _throttled_get(f"{BASE_URL}/release-group", {"query": query, "fmt": "json", "limit": num_results})
    if res is None:
        return []
    groups = res.json().get("release-groups", [])[:num_results]

    matches = []
    for g in groups:
        artists = [a.get("name", "") for a in g.get("artist-credit", []) if a.get("name")]
        matches.append({
            "Title": g.get("title", ""),
            "Artist": ", ".join(artists),
            "Year Released": (g.get("first-release-date") or "")[:4],
            "_rg_id": g.get("id", ""),
        })

    if matches:
        _fill_top_candidate_details(matches[0])

    for m in matches:
        m.pop("_rg_id", None)

    return matches


def _fill_top_candidate_details(match: dict) -> None:
    rg_id = match.pop("_rg_id", "")
    if not rg_id:
        return

    genre_res = _throttled_get(f"{BASE_URL}/release-group/{rg_id}", {"inc": "genres", "fmt": "json"})
    if genre_res is not None:
        genres = sorted(genre_res.json().get("genres", []) or [], key=lambda g: -g.get("count", 0))
        names = [g["name"] for g in genres[:3] if g.get("name")]
        if names:
            match["Genre"] = ", ".join(n.title() for n in names)

    releases_res = _throttled_get(f"{BASE_URL}/release-group/{rg_id}", {"inc": "releases+media", "fmt": "json"})
    if releases_res is None:
        return
    releases = releases_res.json().get("releases", []) or []
    if not releases:
        return

    # Prefer an actual vinyl pressing's tracklist when one exists; fall back to
    # the first-listed release (often the original pressing for older albums,
    # which was vinyl anyway before other formats existed).
    def is_vinyl(release: dict) -> bool:
        return any("vinyl" in (m.get("format") or "").lower() for m in release.get("media", []))

    release = next((r for r in releases if is_vinyl(r)), releases[0])
    release_id = release["id"]

    tracks_res = _throttled_get(f"{BASE_URL}/release/{release_id}", {"inc": "recordings", "fmt": "json"})
    if tracks_res is not None:
        media = tracks_res.json().get("media", []) or []
        track_lines = []
        for m in media:
            for t in m.get("tracks", []) or []:
                pos = t.get("position", "")
                title = t.get("title", "")
                if title:
                    track_lines.append(f"{pos}. {title}")
        if track_lines:
            match["Track List"] = "\n".join(track_lines)

    cover_res = _throttled_get(f"{COVER_ART_URL}/{release_id}", {})
    if cover_res is not None:
        images = cover_res.json().get("images", []) or []
        front = next((i for i in images if i.get("front")), images[0] if images else None)
        if front:
            match["image_path"] = front.get("thumbnails", {}).get("large") or front.get("image", "")
