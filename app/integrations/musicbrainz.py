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

# MusicBrainz tags reissues/greatest-hits/live sets/remix albums with a
# "secondary-types" list on top of the primary "Album" type -- for a real
# artist's catalog these outnumber actual studio albums by ~10 to 1 (e.g.
# Michael Jackson's "type=Album" browse is ~100 entries, of which only 12 are
# real studio albums), so an artist-only search needs these stripped out to
# be useful as a "top albums" list at all.
_EXCLUDED_SECONDARY_TYPES = {
    "compilation", "live", "soundtrack", "remix", "dj-mix",
    "mixtape/street", "demo", "interview", "audiobook", "spokenword",
}


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


def _lookup_artist(safe_query: str) -> dict | None:
    """Confidently resolve a bare artist-name search (e.g. "Michael Jackson",
    no album title) to one specific MusicBrainz artist, so it can be routed to
    their studio discography instead of a generic text search. MusicBrainz's
    own relevance score (0-100) does the disambiguating: an exact/near-exact
    artist name scores ~100, while a combined "artist + album title" query
    (e.g. "Fleetwood Mac Rumours") scores far lower or returns nothing at all
    -- which is exactly the signal needed to leave that case to the existing
    release-group text search below, unchanged.
    """
    res = _throttled_get(f"{BASE_URL}/artist", {"query": f'artist:"{safe_query}"', "fmt": "json", "limit": 1})
    if res is None:
        return None
    artists = res.json().get("artists", []) or []
    if not artists or artists[0].get("score", 0) < 90:
        return None
    return artists[0]


def _artist_studio_albums(artist_id: str, artist_name: str, limit: int) -> list[dict]:
    """The artist's real studio albums only -- see _EXCLUDED_SECONDARY_TYPES
    for why the raw browse needs filtering down this much. Sorted oldest
    first, like a normal discography listing.
    """
    res = _throttled_get(f"{BASE_URL}/release-group", {"artist": artist_id, "type": "album", "fmt": "json", "limit": 100})
    if res is None:
        return []
    groups = res.json().get("release-groups", []) or []
    studio = [
        g for g in groups
        if not ({s.lower() for s in (g.get("secondary-types") or [])} & _EXCLUDED_SECONDARY_TYPES)
    ]
    studio.sort(key=lambda g: g.get("first-release-date") or "9999")
    return [
        {
            "Title": g.get("title", ""),
            "Artist": artist_name,
            "Year Released": (g.get("first-release-date") or "")[:4],
            "id": g.get("id", ""),
        }
        for g in studio[:limit]
    ]


def search_musicbrainz(query: str, num_results: int = 5) -> tuple[list[dict], str | None]:
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
        return [], None

    safe_query = query.replace('"', "")

    # Try resolving the query to one specific artist first, so an artist-only
    # search (no album title) surfaces that artist's actual studio discography
    # instead of whatever a generic text search ranks highest (which, for a
    # famous name, is mostly tribute albums and other people's songs literally
    # titled after them).
    artist = _lookup_artist(safe_query)
    if artist:
        matches = _artist_studio_albums(artist["id"], artist.get("name", safe_query), max(num_results, 10))
        # A single-word query can also exactly match some obscure act's literal
        # name (e.g. "Rumours" is, confusingly, also an obscure band -- not just
        # the Fleetwood Mac album title) -- require a real multi-album catalog
        # before trusting the artist read over a plain title search, so a thin
        # one-release homonym doesn't hijack an actual album-title search.
        if len(matches) >= 2:
            matches[0].update(fetch_full_details(matches[0]["id"]))
            note = f"Showing {artist.get('name', safe_query)}'s studio albums (compilations, live albums, and remixes are left out)."
            return matches, note

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
    # an artist-only search's own catalog (the _lookup_artist path above is
    # the real fix for that case now -- this OR clause just remains as a
    # fallback for artists _lookup_artist doesn't confidently resolve).
    lucene_query = f'{safe_query} OR artist:"{safe_query}"'
    res = _throttled_get(f"{BASE_URL}/release-group", {"query": lucene_query, "fmt": "json", "limit": num_results})
    if res is None:
        return [], None
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

    return matches, None


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
