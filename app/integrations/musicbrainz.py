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

# Generic words a reverse-image "best guess" label tends to append that
# aren't actually part of the title (e.g. "michael buble christmas album").
_GENERIC_FILLER_WORDS = {"album", "record", "records", "vinyl", "lp", "cover", "art", "sleeve"}


def _throttled_get(url: str, params: dict) -> requests.Response | None:
    """A single search can now chain quite a few of these (artist lookup,
    discography browse, bare-query search, then genre/release/tracks/cover-art
    for the top result) -- a single transient timeout or 503 anywhere in that
    longer chain used to fail the whole search. One retry (still respecting
    the same rate limit before trying again) covers the common transient
    case without meaningfully changing behavior for a real, persistent
    outage -- that still correctly gives up and surfaces the honest
    "search failed" note instead of hanging or retrying forever.
    """
    global _last_request_at
    with _lock:
        for attempt in range(2):
            wait = _MIN_GAP_SECONDS - (time.time() - _last_request_at)
            if wait > 0:
                time.sleep(wait)
            try:
                res = requests.get(url, params=params, headers=_HEADERS, timeout=10)
            except requests.RequestException:
                logger.warning("MusicBrainz request failed for %s (attempt %d/2)", url, attempt + 1)
                _last_request_at = time.time()
                continue
            _last_request_at = time.time()
            if res.status_code == 200:
                return res
            logger.warning("MusicBrainz returned %s for %s (attempt %d/2)", res.status_code, url, attempt + 1)
    return None


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


def find_by_artist_title_split(query: str, num_results: int = 5) -> list[dict]:
    """Public entry point for the artist+title split heuristic (see
    _find_via_artist_title_split below) -- exposed so a caller with more
    than one candidate query for the same photo (e.g. the photo-scan route,
    which has both OCR text and a web-detection guess) can try this precise,
    discography-grounded match against each of them before falling back to
    either one's noisier plain text search. That matters because a
    garbled/partial OCR read can still produce a query whose plain search
    returns real-but-wrong candidates instead of failing outright -- which
    would hide the correct album exactly when this matters most, since
    "the search returned nothing" is no longer a usable signal to fall back
    to the other candidate query.
    """
    query = query.strip()
    if not query:
        return []
    return _find_via_artist_title_split(query.replace('"', ""), num_results)


def _find_via_artist_title_split(safe_query: str, num_results: int) -> list[dict]:
    """A combined "Artist Title" query (e.g. what OCR reads off a photographed
    cover: "MICHAEL BUBLE CHRISTMAS") loses to MusicBrainz's own text
    relevance more often than not -- a same-artist compilation whose title
    literally repeats both words (e.g. "Michael Bublé's Christmas Party")
    outscores the real album, which is titled plainly just "Christmas", so it
    can rank low or not appear in the results at all. Splitting the query
    into a leading artist-name guess and a trailing title-fragment guess,
    confirming the artist via _lookup_artist, and then checking that
    artist's own (already-reliable) studio discography for a title
    containing the fragment sidesteps that scoring problem entirely.

    Tries the longest artist-prefix first (most specific/least ambiguous),
    falling back to shorter ones only if a longer split's artist doesn't
    confirm or its discography doesn't contain a matching title.
    """
    words = safe_query.split()
    if len(words) < 2:
        return []
    for split in range(len(words) - 1, 0, -1):
        artist_guess = " ".join(words[:split])
        # A reverse-image "best guess" phrase (the web-detection fallback in
        # vision_ocr.py) often tacks on a generic descriptive word ("...
        # christmas album") that isn't part of the actual title -- dropping
        # these before matching is what lets that fragment still line up
        # with the real title ("Christmas") instead of missing by one word.
        title_words = [w for w in words[split:] if w.lower() not in _GENERIC_FILLER_WORDS]
        title_fragment = " ".join(title_words).lower()
        if not title_fragment:
            continue
        artist = _lookup_artist(artist_guess)
        if not artist:
            continue
        albums = _artist_studio_albums(artist["id"], artist.get("name", artist_guess), 50)
        matching = [a for a in albums if title_fragment in a["Title"].lower()]
        if matching:
            return matching[:num_results]
    return []


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
    # Splitting the query into an artist-name guess and a title-fragment
    # guess and checking that artist's real discography catches the album
    # that plain text relevance below would otherwise bury or drop entirely
    # (see _find_via_artist_title_split) -- computed before the text search
    # so it can be merged in ahead of those results, guaranteeing the real
    # album is actually there rather than hoping it outscores compilations.
    split_matches = _find_via_artist_title_split(safe_query, num_results)

    lucene_query = f'{safe_query} OR artist:"{safe_query}"'
    res = _throttled_get(f"{BASE_URL}/release-group", {"query": lucene_query, "fmt": "json", "limit": num_results})
    if res is None:
        # _throttled_get returning None means the request itself failed (a
        # timeout, or MusicBrainz's 1/sec anonymous limit rejecting it) --
        # not that the search legitimately came back empty. Saying so instead
        # of just showing "No matches found" (which reads as "this album
        # doesn't exist") is the honest message, and tells the user retrying
        # is actually worth doing.
        if split_matches:
            split_matches[0].update(fetch_full_details(split_matches[0]["id"]))
            return split_matches, None
        return [], "Search failed -- MusicBrainz didn't respond in time. Try searching again in a moment."
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

    if split_matches:
        # Promote the split-identified album(s) to the front rather than
        # just appending them when "missing" -- the real album is often
        # already in the bare-query results, just buried behind same-artist
        # compilations, so a naive dedup-and-append would see it as already
        # present and leave it exactly where the bad ranking put it.
        split_ids = {m["id"] for m in split_matches}
        remainder = [m for m in matches if m["id"] not in split_ids]
        matches = (split_matches + remainder)[:num_results]

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
