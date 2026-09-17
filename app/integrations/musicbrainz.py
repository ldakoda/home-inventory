import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://musicbrainz.org/ws/2"
COVER_ART_URL = "https://coverartarchive.org/release"

# MusicBrainz's usage policy requires a descriptive User-Agent and caps
# anonymous requests at ~1/second -- a stricter limit than any other
# integration here, since a single "search" resolves into a chain of
# release-group -> release -> tracklist -> cover-art calls. A lock (not just
# a bare timestamp) matters here specifically because, unlike the other
# integrations, cover art for the non-top candidates now loads via separate
# concurrent browser requests (see lookup_musicbrainz_thumb) that can hit
# this from multiple threads at once.
_HEADERS = {"User-Agent": "HomeInventoryApp/1.0 (https://github.com/ldakoda/home-inventory)"}
_MIN_GAP_SECONDS = 1.1
_lock = threading.Lock()
_last_request_at = 0.0


def _throttled_get(url: str, params: dict) -> requests.Response | None:
    global _last_request_at
    with _lock:
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
    requests. Only the top candidate gets that full chain eagerly (Genre,
    Track List, cover art); the rest keep their release-group id (as "id",
    same convention bgg.py uses) so the results page can lazy-load just their
    cover art in the background via lookup_musicbrainz_thumb, same idea as
    bgg.py's click-to-load thumbnails.
    """
    query = query.strip()
    if not query:
        return []

    # A bare query weights toward a literal release-group *title* match, so
    # searching just an artist's name (e.g. "Michael Jackson", no album title)
    # surfaced other people's songs/tributes literally titled that before any
    # of the artist's own albums. OR-ing in an explicit artist-field match
    # fixes that -- but quoting the *whole* query for the artist field too
    # broke the opposite case (a combined "artist + album title" search like
    # "Fleetwood Mac Rumours" doesn't literally equal any artist's name, so a
    # quoted artist clause there matched nothing and narrowed results instead
    # of adding to them). Keeping the plain query as one alternative and
    # adding the artist-field match as a second, rather than quoting both,
    # keeps the original title-search behavior intact while still surfacing
    # an artist-only search's own catalog.
    safe_query = query.replace('"', "")
    lucene_query = f'{safe_query} OR artist:"{safe_query}"'
    res = _throttled_get(f"{BASE_URL}/release-group", {"query": lucene_query, "fmt": "json", "limit": num_results})
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
            "id": g.get("id", ""),
        })

    if matches:
        matches[0].update(fetch_full_details(matches[0]["id"]))

    return matches


def _pick_release(rg_id: str) -> dict | None:
    """The release-group is the abstract album; an actual tracklist/cover art
    lives on one of its releases (a specific pressing). Prefer a genuine vinyl
    pressing when one's listed; otherwise fall back to whichever release is
    listed first (often the original pressing for older albums, which was
    vinyl anyway before other formats existed).
    """
    releases_res = _throttled_get(f"{BASE_URL}/release-group/{rg_id}", {"inc": "releases+media", "fmt": "json"})
    if releases_res is None:
        return None
    releases = releases_res.json().get("releases", []) or []
    if not releases:
        return None

    def is_vinyl(release: dict) -> bool:
        return any("vinyl" in (m.get("format") or "").lower() for m in release.get("media", []))

    return next((r for r in releases if is_vinyl(r)), releases[0])


def fetch_cover_art(rg_id: str) -> str:
    """Cover art only, for lazy-loading a non-top search result's thumbnail
    without paying for the full genre+tracklist chain those don't need yet.
    """
    if not rg_id:
        return ""
    release = _pick_release(rg_id)
    if release is None:
        return ""
    return _cover_art_for_release(release["id"])


def _cover_art_for_release(release_id: str) -> str:
    cover_res = _throttled_get(f"{COVER_ART_URL}/{release_id}", {})
    if cover_res is None:
        return ""
    images = cover_res.json().get("images", []) or []
    front = next((i for i in images if i.get("front")), images[0] if images else None)
    if not front:
        return ""
    return front.get("thumbnails", {}).get("large") or front.get("image", "")


def fetch_full_details(rg_id: str) -> dict:
    """Genre, Track List, and cover art for one specific candidate -- used both
    to eagerly fill the top search result and, on demand, to fill in whichever
    candidate the user actually clicks "Select" on (see lookup_musicbrainz_details
    in items.py), so the confirm-before-applying preview always reflects the
    exact candidate chosen rather than possibly-stale/partial data captured
    when the results list was first rendered.
    """
    details: dict = {}
    if not rg_id:
        return details

    genre_res = _throttled_get(f"{BASE_URL}/release-group/{rg_id}", {"inc": "genres", "fmt": "json"})
    if genre_res is not None:
        genres = sorted(genre_res.json().get("genres", []) or [], key=lambda g: -g.get("count", 0))
        names = [g["name"] for g in genres[:3] if g.get("name")]
        if names:
            details["Genre"] = ", ".join(n.title() for n in names)

    release = _pick_release(rg_id)
    if release is None:
        return details
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
            details["Track List"] = "\n".join(track_lines)

    image = _cover_art_for_release(release_id)
    if image:
        details["image_path"] = image

    return details
