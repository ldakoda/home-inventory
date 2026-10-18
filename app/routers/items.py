import difflib
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from app.static_version import STATIC_VERSION
from starlette.datastructures import UploadFile

from app.deps import require_auth
from app.integrations.bgg import fetch_bgg_game_details, fetch_bgg_game_matches, fetch_bgg_images
from app.integrations.image_search import search_multiple_web_images
from app.integrations.musicbrainz import fetch_cover_art, fetch_full_details, find_by_artist_title_split, search_musicbrainz
from app.integrations.vision_ocr import extract_cover_queries
from app.integrations.omdb import fetch_omdb_movie_matches
from app.integrations.rawg import find_confident_match, search_rawg
from app.integrations.tmdb import search_tmdb
from app.integrations.wikipedia import search_wikipedia
from app.bulk_add import entry_key, existing_keys, parse_lines, split_entry
from app.models import Item
from app.repository import Repository, get_repository
from app.storage import get_image_storage

router = APIRouter(dependencies=[Depends(require_auth)])
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_v"] = STATIC_VERSION


def _get_item_or_404(repo: Repository, item_id: str) -> Item:
    item = repo.get_item(item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    return item


def _item_or_none(repo: Repository, item_id: str) -> Item | None:
    # The Add Item form searches metadata before an item exists yet -- it hits
    # these same lookup routes with the sentinel id "new" instead of a real one.
    return None if item_id == "new" else _get_item_or_404(repo, item_id)


def _match_json(match: dict) -> str:
    return json.dumps({k: v for k, v in match.items() if k != "id"})


_SEARCH_STOP_WORDS = {"the", "a", "an", "of", "and", "&"}


def _fuzzy_filter(items: list[Item], query: str) -> list[Item]:
    query = query.strip().lower()
    if not query:
        return items

    query_words = query.split()
    # Ignoring stop words here (not in the substring check above) keeps a
    # multi-word search meaningfully narrowing: matching on *any* single word
    # (the old behavior) let "the" alone match nearly every title in a typical
    # movie collection, so "the devil" returned half the library instead of
    # narrowing down to "The Devil Wears Prada". Requiring every *meaningful*
    # word to appear fixes that without penalizing titles that are themselves
    # mostly stop words (falls back to the raw word list if nothing's left).
    meaningful_words = [w for w in query_words if w not in _SEARCH_STOP_WORDS] or query_words

    def matches(item: Item) -> bool:
        name = item.name.lower()
        if query in name:
            return True
        if all(word in name for word in meaningful_words):
            return True
        # Typo tolerance, but only for a single meaningful word, and compared
        # against each word in the name individually rather than the name (or
        # query) as a whole -- whole-string ratios run artificially high for
        # short titles regardless of actual relevance (comparing "catan" to
        # the full "Aquaman" or "Matilda" both cleared the old 0.5 threshold).
        if len(meaningful_words) == 1:
            word = meaningful_words[0]
            return any(difflib.SequenceMatcher(None, word, w).ratio() >= 0.75 for w in name.split())
        return False

    return [item for item in items if matches(item)]


_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
_COLLECTION_SUFFIX = re.compile(r"\s*\d*-?\s*movies?\s+collection\s*$", re.IGNORECASE)
_PACKAGING_WORD = re.compile(r"\s+(?:collection|trilogy|double feature)\s*$", re.IGNORECASE)
_TRAILING_PAREN_LIST = re.compile(r"\(([^()]+)\)\s*$")
_ANNIVERSARY_SUFFIX = re.compile(r"\s+\d+(?:st|nd|rd|th)\s+anniversary\s*$", re.IGNORECASE)
_SEASON_RANGE_SUFFIX = re.compile(r":?\s*seasons?\s+[\d]+\s*[\-–—]\s*[\d]+\s*$", re.IGNORECASE)
_COMPLETE_SERIES_SUFFIX = re.compile(r"\s*[\-–—]\s*the complete series\s*$", re.IGNORECASE)


def _clean_movie_query(raw: str) -> str:
    """Inventory item names are retail packaging text ("Transformers 4-Movie
    Collection", "X-Men / X2 / X-Men: The Last Stand (Trilogy)", "Planet of the
    Apes Trilogy"), not the clean title a movie database indexes under. Strip that
    down to something searchable: take the first title out of a slash-separated
    multi-pack, then repeatedly drop a trailing parenthetical (edition/format
    label) or box-set word ("N-Movie(s) Collection", "Collection", "Trilogy",
    "Double Feature") until neither pattern matches anymore.
    """
    q = raw.strip()

    # "3 Film Collection (American Sniper / Gran Torino / Sully)" or "4 Film
    # Favorites: Girls Night Collection (27 Dresses / Bride Wars / ...)" list the
    # actual included films in a trailing parenthetical rather than before it --
    # naively splitting the whole string on " / " first (as below) mangles these
    # into "3 Film Collection (American Sniper", which matches nothing. Pull the
    # first title out of that parenthetical instead when this pattern applies
    # (a trailing "(...)" whose contents are themselves slash-separated).
    paren_list = _TRAILING_PAREN_LIST.search(q)
    if paren_list and " / " in paren_list.group(1) and " / " not in q[: paren_list.start()]:
        return paren_list.group(1).split(" / ")[0].strip()

    q = q.split(" / ")[0].strip()
    while True:
        stripped = _TRAILING_PAREN.sub("", q).strip()
        stripped = _COLLECTION_SUFFIX.sub("", stripped).strip()
        stripped = _PACKAGING_WORD.sub("", stripped).strip()
        stripped = _ANNIVERSARY_SUFFIX.sub("", stripped).strip()
        stripped = _SEASON_RANGE_SUFFIX.sub("", stripped).strip()
        stripped = _COMPLETE_SERIES_SUFFIX.sub("", stripped).strip()
        if stripped == q:
            break
        q = stripped
    return q or raw.strip()


_MEDIA_RETAILER_DOMAINS = (
    # retailers
    "ebayimg.com", "ebay.com",
    "media-amazon.com", "amazon.com",
    "bbystatic.com", "bestbuy.com",
    "walmartimages.com", "walmart.com",
    "target.scene7.com", "target.com",
    "dvdempire.com", "moviestop.com", "barnesandnoble.com",
    # dedicated DVD/Blu-ray cover art archives -- often a better source than a
    # retailer listing photo for a specific/niche combo release
    "static-bluray.com", "bluray.com",
    "dvdcover.com", "dvd-covers.org", "box3.net",
    "hirescovers.net", "freecovers.net", "coverlib.com",
    "fanart.tv",
)


def _is_media_retailer(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in _MEDIA_RETAILER_DOMAINS)


@router.get("/", response_class=HTMLResponse)
def index(request: Request, repo: Repository = Depends(get_repository)):
    categories = repo.list_categories()
    items = repo.list_items()
    # Fields available per category, for the client-side "detail to show / sort by"
    # picker -- primary_field is excluded since that's already the item's name/title.
    category_fields = {c.slug: [f for f in c.fields if f != c.primary_field] for c in categories}
    category_names = {c.slug: f"{c.icon} {c.name}" for c in categories}
    category_counts = repo.count_items_by_category()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "categories": categories,
            "items": items,
            "active_category": "all",
            "q": "",
            "category_fields_json": json.dumps(category_fields),
            "category_names_json": json.dumps(category_names),
            "category_counts": category_counts,
        },
    )


@router.get("/items", response_class=HTMLResponse)
def list_items(request: Request, q: str = "", category: str = "all", repo: Repository = Depends(get_repository)):
    items = repo.list_items(category_slug=category)
    items = _fuzzy_filter(items, q)
    return templates.TemplateResponse(request, "partials/item_list.html", {"items": items})


@router.get("/items/{item_id}/row", response_class=HTMLResponse)
def item_row(request: Request, item_id: str, repo: Repository = Depends(get_repository)):
    item = _get_item_or_404(repo, item_id)
    return templates.TemplateResponse(request, "partials/item_row.html", {"item": item})


@router.get("/items/new", response_class=HTMLResponse)
def new_item_form(request: Request, repo: Repository = Depends(get_repository)):
    categories = repo.list_categories()
    if not categories:
        return HTMLResponse("<p>Create a category first.</p>")
    return templates.TemplateResponse(
        request, "partials/add_form.html", {"categories": categories, "category": categories[0]}
    )


@router.get("/bulk-add", response_class=HTMLResponse)
def bulk_add_form(request: Request, category: str = "", repo: Repository = Depends(get_repository)):
    categories = repo.list_categories()
    selected = next((c.slug for c in categories if c.slug == category), None)
    if selected is None:
        # Default to Video Games (the main use so far), else the first category.
        selected = next((c.slug for c in categories if c.name.lower() == "video games"), categories[0].slug if categories else "")
    return templates.TemplateResponse(request, "partials/bulk_add_modal.html", {"categories": categories, "selected_slug": selected})


@router.get("/bulk-add/seed", response_class=HTMLResponse)
def bulk_add_seed(request: Request):
    text = (Path(__file__).resolve().parent.parent / "seed" / "box_of_games.txt").read_text(encoding="utf-8")
    return templates.TemplateResponse(request, "partials/bulk_add_text.html", {"text": text})


@router.post("/bulk-add/preview", response_class=HTMLResponse)
async def bulk_add_preview(request: Request, repo: Repository = Depends(get_repository)):
    form = await request.form()
    category = repo.get_category(str(form.get("category_slug", "")))
    if category is None:
        raise HTTPException(404, "Unknown category")

    uses_platform = "Platform" in category.fields
    have = existing_keys(repo.list_items(category_slug=category.slug), uses_platform)
    new, dupes = [], []
    for title, platform in parse_lines(str(form.get("text", "")), str(form.get("default_platform", ""))):
        (dupes if entry_key(title, platform, uses_platform) in have else new).append((title, platform))
    return templates.TemplateResponse(request, "partials/bulk_add_preview.html", {"category": category, "new": new, "dupes": dupes})


@router.post("/bulk-add/commit", response_class=HTMLResponse)
async def bulk_add_commit(request: Request, repo: Repository = Depends(get_repository)):
    form = await request.form()
    category = repo.get_category(str(form.get("category_slug", "")))
    if category is None:
        raise HTTPException(404, "Unknown category")

    uses_platform = "Platform" in category.fields
    have = existing_keys(repo.list_items(category_slug=category.slug), uses_platform)
    added = skipped = 0
    for value in form.getlist("entry"):
        title, platform = split_entry(str(value))
        key = entry_key(title, platform, uses_platform)
        if not title or key in have:
            skipped += 1
            continue
        attributes = {f: "" for f in category.fields}
        attributes[category.primary_field] = title
        if uses_platform:
            attributes["Platform"] = platform
        repo.create_item(category_slug=category.slug, name=title, image_path=None, attributes=attributes)
        have.add(key)
        added += 1

    response = templates.TemplateResponse(request, "partials/bulk_add_done.html", {"category": category, "added": added, "skipped": skipped})
    response.headers["HX-Trigger"] = "refreshList"
    return response


@router.get("/categories/{slug}/fields", response_class=HTMLResponse)
def category_fields(request: Request, slug: str, repo: Repository = Depends(get_repository)):
    category = repo.get_category(slug)
    if category is None:
        raise HTTPException(404, f"Unknown category '{slug}'")
    return templates.TemplateResponse(request, "partials/field_inputs.html", {"category": category, "values": {}})


@router.post("/items", response_class=HTMLResponse)
async def create_item(
    request: Request,
    repo: Repository = Depends(get_repository),
):
    form = await request.form()
    category_slug = str(form.get("category_slug", ""))
    image_url = str(form.get("image_url", ""))
    image_file = form.get("image_file")

    category = repo.get_category(category_slug)
    if category is None:
        raise HTTPException(404, f"Unknown category '{category_slug}'")

    attributes = {f: str(form.get(f, "")).strip() for f in category.fields}
    name = attributes.get(category.primary_field, "").strip()
    if not name:
        return HTMLResponse("<p class='error'>The primary field is required.</p>", status_code=400)

    image_path = image_url.strip()
    if isinstance(image_file, UploadFile) and image_file.filename:
        image_path = get_image_storage().save(image_file)

    repo.create_item(category_slug=category.slug, name=name, image_path=image_path or None, attributes=attributes)

    response = HTMLResponse("")
    response.headers["HX-Trigger"] = "refreshList"
    return response


@router.get("/items/{item_id}/view", response_class=HTMLResponse)
def view_item(request: Request, item_id: str, repo: Repository = Depends(get_repository)):
    item = _get_item_or_404(repo, item_id)
    return templates.TemplateResponse(request, "partials/item_view_modal.html", {"item": item})


@router.get("/items/{item_id}/edit", response_class=HTMLResponse)
def edit_item_form(request: Request, item_id: str, repo: Repository = Depends(get_repository)):
    item = _get_item_or_404(repo, item_id)
    return templates.TemplateResponse(request, "partials/item_edit.html", {"item": item})


@router.put("/items/{item_id}", response_class=HTMLResponse)
async def update_item(
    request: Request,
    item_id: str,
    repo: Repository = Depends(get_repository),
):
    item = _get_item_or_404(repo, item_id)
    form = await request.form()
    image_url = str(form.get("image_url", ""))
    image_file = form.get("image_file")

    attributes = {f: str(form.get(f, item.attributes.get(f, ""))).strip() for f in item.category.fields}
    name = attributes.get(item.category.primary_field, "").strip()
    if not name:
        return HTMLResponse("<p class='error'>The primary field is required.</p>", status_code=400)

    updates = {"attributes": attributes, "name": name}

    if isinstance(image_file, UploadFile) and image_file.filename:
        updates["image_path"] = get_image_storage().save(image_file)
    elif image_url.strip():
        updates["image_path"] = image_url.strip()

    item = repo.update_item(item_id, **updates)
    return templates.TemplateResponse(request, "partials/item_row.html", {"item": item})


@router.delete("/items/{item_id}")
def delete_item(item_id: str, repo: Repository = Depends(get_repository)):
    _get_item_or_404(repo, item_id)
    repo.delete_item(item_id)
    return Response("", status_code=200)


@router.post("/items/{item_id}/attachments", response_class=HTMLResponse)
async def upload_attachments(request: Request, item_id: str, repo: Repository = Depends(get_repository)):
    item = _get_item_or_404(repo, item_id)
    form = await request.form()
    files = [f for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]

    storage = get_image_storage()
    for upload in files:
        url = storage.save(upload)
        item = repo.add_attachment(item_id, {
            "id": uuid.uuid4().hex,
            "filename": upload.filename,
            "url": url,
            "content_type": upload.content_type or "",
        })

    return templates.TemplateResponse(request, "partials/item_attachments.html", {"item": item})


@router.delete("/items/{item_id}/attachments/{attachment_id}", response_class=HTMLResponse)
def delete_attachment(
    request: Request, item_id: str, attachment_id: str, repo: Repository = Depends(get_repository)
):
    item = _get_item_or_404(repo, item_id)
    attachment = next((a for a in item.attachments if a.get("id") == attachment_id), None)
    if attachment:
        get_image_storage().delete(attachment["url"])
    item = repo.remove_attachment(item_id, attachment_id)
    return templates.TemplateResponse(request, "partials/item_attachments.html", {"item": item})


def _target_id(item_id: str, panel: str) -> str:
    return f"missing-meta-item-{item_id}" if panel == "missing" else f"item-{item_id}"


@router.get("/items/{item_id}/lookup/omdb", response_class=HTMLResponse)
def lookup_omdb(request: Request, item_id: str, q: str, panel: str = "", repo: Repository = Depends(get_repository)):
    item = _item_or_none(repo, item_id)
    cleaned_q = _clean_movie_query(q)
    looks_like_bundle = cleaned_q != q.strip()

    matches = fetch_omdb_movie_matches(q)
    if not matches and looks_like_bundle:
        matches = fetch_omdb_movie_matches(cleaned_q)
    notes = []

    if not matches:
        # OMDb only indexes individual titles -- a box-set/collection name (or any title
        # it doesn't have) won't match. TMDb also has real "collection" entries for
        # franchises/box-sets, so it's a much better fallback than a generic image search.
        # Search on the cleaned title ("Transformers 4-Movie Collection" -> "Transformers"),
        # since the literal retail packaging text rarely matches either database directly.
        matches = search_tmdb(cleaned_q)
        if matches:
            notes.append(f"No OMDb match -- showing TMDb results for '{cleaned_q}' instead. Collection hits carry only a title/poster; individual movie hits include rating, genre, runtime, and description too.")

    if looks_like_bundle:
        # A "Double Feature"/"Collection"/slash-separated combo (often a store-exclusive
        # 2-in-1 disc, e.g. a Walmart double feature) is its own retail product, not
        # something OMDb or TMDb indexes at all -- neither has a matching single title
        # or franchise entry for "movie A + movie B on one disc". Search the actual web
        # for that specific product's box art and offer it alongside any single-movie
        # matches above, since either could be the right image to use.
        #
        # This is a scraped search, not an official API (Google's Custom Search JSON
        # API and eBay's Browse API were both tried and are unavailable to new
        # projects/accounts) -- it returns plenty of unrelated junk for an ambiguous
        # multi-word title, so only keep hits from domains that are actually
        # movie/media retailers, and over-fetch since most candidates get discarded.
        existing_images = {m.get("image_path") for m in matches}
        raw_box_art = search_multiple_web_images(f"{q} DVD cover", num_results=24)
        box_art = [url for url in raw_box_art if _is_media_retailer(url)][:6]
        matches = matches + [{"image_path": url} for url in box_art if url not in existing_images]
        if box_art:
            notes.append("This looks like a multi-movie combo pack -- web results for the actual box art are included too (poster only, no other metadata).")

    for m in matches:
        m["match_json"] = _match_json(m)

    return templates.TemplateResponse(
        request,
        "partials/lookup_results.html",
        {"item": item, "matches": matches, "kind": "omdb", "target_id": _target_id(item_id, panel), "panel": panel, "note": " ".join(notes) or None},
    )


def _web_cover_fallback(title: str) -> str:
    # BGG's thing endpoint can come back empty (rate limit, auth, outage) even
    # when the search that found the game worked; a web image search for the box
    # art is better than a blank card.
    if not title:
        return ""
    urls = search_multiple_web_images(f"{title} board game box", num_results=1)
    return urls[0] if urls else ""


def _bgg_details_with_cover_fallback(bgg_id: str, title: str, priority: bool) -> dict:
    details = fetch_bgg_game_details(bgg_id, priority=priority)
    if not details.get("image_path"):
        cover = _web_cover_fallback(title)
        if cover:
            details["image_path"] = cover
    return details


@router.get("/items/{item_id}/lookup/bgg", response_class=HTMLResponse)
def lookup_bgg(request: Request, item_id: str, q: str, panel: str = "", repo: Repository = Depends(get_repository)):
    item = _item_or_none(repo, item_id)
    # The missing-metadata panel fires up to ~60 of these at once as a passive
    # background scan; anything else (the edit drawer's or Add Item form's own
    # search box) is a single deliberate click a user is actively waiting on.
    # Give the latter the fast lane so it never queues behind the former's
    # backlog -- see bgg.py's _bulk_limiter/_priority_limiter -- and cut the
    # bulk scan down to one BGG call per row instead of up to three (skip the
    # separate exact-match request, and skip the eager top-candidate image
    # fetch below), since even fully throttled-but-reliable per-call pacing
    # made the ~60-row scan take minutes. Every candidate still gets an image
    # on demand via the click-to-load thumb either way.
    is_bulk_scan = panel == "missing"
    priority = not is_bulk_scan
    raw_matches = fetch_bgg_game_matches(q, priority=priority, fast=is_bulk_scan)
    matches = []
    for m in raw_matches[:8]:
        matches.append({"id": m["id"], "Title": m["name"], "Year Released": m.get("year", "")})

    # BGG's search endpoint returns no image, only the separate "thing" endpoint does.
    # For a single deliberate search, fetch it for just the top (most likely) match
    # and embed it directly in this response -- an htmx auto-trigger nested inside
    # another auto-triggered swap turned out to fire unreliably, so this avoids a
    # second client-side round trip. Skipped during the bulk scan (see above); every
    # candidate there stays image-less until clicked (lookup_results.html's
    # click-to-load thumb).
    if matches and not is_bulk_scan:
        top_details = _bgg_details_with_cover_fallback(matches[0]["id"], matches[0]["Title"], priority)
        for key in ("image_path", "Number of Players", "Length of Play", "Age Rating", "Description"):
            if top_details.get(key):
                matches[0][key] = top_details[key]
        # Covers for the remaining cards in one batched request; any that miss
        # still fall back to the click-to-load thumb.
        images = fetch_bgg_images([m["id"] for m in matches[1:]], priority=priority)
        for m in matches[1:]:
            if images.get(m["id"]):
                m["image_path"] = images[m["id"]]

    kind = "bgg"
    note = None
    if not matches:
        # BGG only catalogs tabletop games -- an outdoor/yard game (Spikeball,
        # KanJam, cornhole, Kubb) has no entry there at all. Wikipedia reliably
        # covers these instead, but only gives prose: Description and a photo,
        # never player count/length/age (not structured data on a Wikipedia
        # page), so it's a fallback rather than a peer of BGG.
        wiki_matches = search_wikipedia(q)
        if wiki_matches:
            matches = wiki_matches
            kind = "wiki"
            note = (
                f"No BGG match -- showing Wikipedia results for '{q}' instead. "
                "These include a description & photo but not player count, length, "
                "or age rating (Wikipedia doesn't have that as structured data)."
            )

    # Powers the edit drawer's "auto-fill empty fields from the top result" (see
    # autoFillFromSearch in index.html) the same way OMDb/TMDb/web-image matches
    # already do -- harmless for the bulk scan, which never reads this key.
    for m in matches:
        m["match_json"] = _match_json(m)

    return templates.TemplateResponse(
        request,
        "partials/lookup_results.html",
        {"item": item, "matches": matches, "kind": kind, "panel": panel, "target_id": _target_id(item_id, panel), "note": note},
    )


@router.get("/items/{item_id}/lookup/bgg-thumb", response_class=HTMLResponse)
def lookup_bgg_thumb(request: Request, item_id: str, bgg_id: str, title: str = "", panel: str = "", repo: Repository = Depends(get_repository)):
    # BGG's search endpoint (used above) returns no image at all, only the "thing"
    # endpoint does -- so each search result lazy-loads its own thumbnail via this
    # endpoint after the results render, rather than fetching full details for every
    # candidate up front (which would be several BGG API round-trips per game).
    # A real click is always priority. The missing-metadata panel also auto-triggers
    # this once per row (for the top candidate only, see lookup_results.html) so box
    # art still shows up without slowing down the candidate list itself -- that one
    # firing ~60 times at once needs the slow/bulk lane like the rest of the scan,
    # not the fast lane meant for a single deliberate click.
    _item_or_none(repo, item_id)
    priority = panel != "missing"
    details = fetch_bgg_game_details(bgg_id, priority=priority)
    image_path = details.get("image_path", "")
    if not image_path and priority:
        image_path = _web_cover_fallback(title)
    return templates.TemplateResponse(request, "partials/lazy_thumb.html", {"image_path": image_path})


@router.get("/items/{item_id}/lookup/bgg-details", response_class=HTMLResponse)
def lookup_bgg_details(request: Request, item_id: str, bgg_id: str, title: str, panel: str = "", repo: Repository = Depends(get_repository)):
    # Rendered into the global #modal-slot (see base.html) rather than swapped inline --
    # a modal always has the same home regardless of whether this was opened from the
    # edit drawer, the missing-metadata panel, or the Add Item form, so it sidesteps
    # needing to track a different "in-progress results" container id per context.
    item = _item_or_none(repo, item_id)
    # Always a "Select" click a user is actively waiting on -- always priority,
    # regardless of which panel/context it was opened from.
    details = fetch_bgg_game_details(bgg_id, priority=True)
    if not details.get("image_path"):
        details["image_path"] = _web_cover_fallback(title)
    match = {
        "Title": title,
        "Number of Players": details.get("Number of Players", ""),
        "Length of Play": details.get("Length of Play", ""),
        "Age Rating": details.get("Age Rating", ""),
        "Description": details.get("Description", ""),
        "image_path": details.get("image_path", ""),
    }
    return templates.TemplateResponse(
        request,
        "partials/bgg_confirm_modal.html",
        {"item": item, "match": match, "match_json": _match_json(match), "panel": panel, "target_id": _target_id(item_id, panel)},
    )


@router.get("/items/{item_id}/lookup/web-images", response_class=HTMLResponse)
def lookup_web_images(request: Request, item_id: str, q: str, repo: Repository = Depends(get_repository)):
    item = _item_or_none(repo, item_id)
    urls = search_multiple_web_images(q, num_results=8)
    matches = [{"image_path": url, "match_json": _match_json({"image_path": url})} for url in urls]
    return templates.TemplateResponse(
        request,
        "partials/lookup_results.html",
        {"item": item, "matches": matches, "kind": "image", "target_id": _target_id(item_id, ""), "panel": ""},
    )


@router.get("/items/{item_id}/lookup/rawg", response_class=HTMLResponse)
def lookup_rawg(request: Request, item_id: str, q: str, panel: str = "", repo: Repository = Depends(get_repository)):
    item = _item_or_none(repo, item_id)
    platform_hint = item.attributes.get("Platform", "") if item else ""
    matches = search_rawg(q, platform_hint=platform_hint)
    for m in matches:
        m["match_json"] = _match_json(m)
    return templates.TemplateResponse(
        request,
        "partials/lookup_results.html",
        {"item": item, "matches": matches, "kind": "rawg", "target_id": _target_id(item_id, panel), "panel": panel},
    )


@router.get("/items/{item_id}/lookup/musicbrainz", response_class=HTMLResponse)
def lookup_musicbrainz(request: Request, item_id: str, q: str, panel: str = "", repo: Repository = Depends(get_repository)):
    item = _item_or_none(repo, item_id)
    matches, note = search_musicbrainz(q)
    for m in matches:
        m["match_json"] = _match_json(m)
    return templates.TemplateResponse(
        request,
        "partials/lookup_results.html",
        {"item": item, "matches": matches, "kind": "musicbrainz", "target_id": _target_id(item_id, panel), "panel": panel, "note": note},
    )


@router.post("/items/{item_id}/lookup/musicbrainz-photo", response_class=HTMLResponse)
async def lookup_musicbrainz_photo(request: Request, item_id: str, repo: Repository = Depends(get_repository)):
    # Snap-a-photo search: reads the cover(s) in the photo by prominent text
    # size and position (see extract_cover_queries -- this is what tells a
    # cover's real title/artist typography apart from a promo sticker or
    # barcode, and what lets a photo of two records side by side identify
    # both instead of mashing all the text into one unsearchable query).
    #
    # Each detected cover's text, and the whole-photo web-detection guess,
    # all get tried through the precise artist+title split match (grounded
    # in the artist's actual discography) before ANY of them are trusted to
    # fall back to a plain text search -- a partially-misread cover's plain
    # search can return real-but-wrong candidates instead of failing
    # outright, which would otherwise stop a more reliable signal (the web
    # guess, or a different detected cover) from getting a turn at all.
    form = await request.form()
    panel = str(form.get("panel", ""))
    item = _item_or_none(repo, item_id)
    target_id = _target_id(item_id, panel)
    photo = form.get("photo")

    if not (isinstance(photo, UploadFile) and photo.filename):
        return templates.TemplateResponse(
            request, "partials/lookup_results.html",
            {"item": item, "matches": [], "kind": "musicbrainz", "target_id": target_id, "panel": panel,
             "note": "No photo received -- try taking the picture again."},
        )

    image_bytes = await photo.read()
    queries, web_guess = extract_cover_queries(image_bytes)
    per_cover_limit = 5 if len(queries) > 1 else 10
    web_guess = web_guess if web_guess.lower() not in [q.lower() for q in queries] else ""

    matches: list[dict] = []
    used_queries: list[str] = []
    search_note = None
    via_web_guess = False

    if len(queries) <= 1:
        # The common case: exactly one detected cover. The web guess is a
        # single whole-photo signal here, so it's safe to let it rescue this
        # one slot if the cover's own text doesn't pan out.
        query = queries[0] if queries else ""
        if query:
            matches = find_by_artist_title_split(query, per_cover_limit)
            if matches:
                used_queries = [query]
        if not matches and web_guess:
            web_matches = find_by_artist_title_split(web_guess, per_cover_limit)
            if web_matches:
                matches, used_queries, via_web_guess = web_matches, [web_guess], True
        if not matches and query:
            matches, search_note = search_musicbrainz(query, per_cover_limit)
            used_queries = [query] if matches else []
        if not matches and web_guess:
            web_matches, web_search_note = search_musicbrainz(web_guess, per_cover_limit)
            if web_matches:
                matches, used_queries, search_note, via_web_guess = web_matches, [web_guess], web_search_note, True
    else:
        # Multiple detected covers -- resolve each independently (its own
        # precise match, falling back to a plain search only for that one
        # cover) so one cover's web-detection guess -- which can only ever
        # describe one of them -- can't crowd out the other's results by
        # "succeeding" first and skipping the rest.
        for q in queries:
            cover_matches = find_by_artist_title_split(q, per_cover_limit)
            if not cover_matches:
                cover_matches, note = search_musicbrainz(q, per_cover_limit)
                if note:
                    search_note = note
            if cover_matches:
                matches.extend(cover_matches)
                used_queries.append(q)

    if not matches:
        return templates.TemplateResponse(
            request, "partials/lookup_results.html",
            {"item": item, "matches": [], "kind": "musicbrainz", "target_id": target_id, "panel": panel,
             "note": "Couldn't read any text on that cover, and couldn't visually recognize the album either -- try a closer, well-lit photo of the front, or search by title/artist instead."},
        )

    matches[0].update(fetch_full_details(matches[0]["id"]))
    for m in matches:
        m["match_json"] = _match_json(m)

    if via_web_guess:
        prefix = f'Couldn\'t find a match from the printed text, so recognized the cover as "{used_queries[0]}" instead.'
    elif len(used_queries) > 1:
        prefix = f"Looks like {len(used_queries)} records in this photo -- showing matches for each."
    else:
        prefix = f'Read "{used_queries[0]}" off the photo.'
    note = prefix + (f" {search_note}" if search_note else "")
    return templates.TemplateResponse(
        request, "partials/lookup_results.html",
        {"item": item, "matches": matches, "kind": "musicbrainz", "target_id": target_id, "panel": panel, "note": note},
    )


@router.get("/items/{item_id}/lookup/musicbrainz-thumb", response_class=HTMLResponse)
def lookup_musicbrainz_thumb(request: Request, item_id: str, rg_id: str, repo: Repository = Depends(get_repository)):
    # Only the top search result gets cover art eagerly (see search_musicbrainz --
    # each candidate's art costs 2 more throttled MusicBrainz/Cover-Art-Archive
    # calls). The rest lazy-load their own art via this endpoint once the results
    # are already on screen, same click-to-load idea bgg.py uses for box art --
    # except these auto-load (MusicBrainz's 1/sec limit already paces them out
    # visibly one at a time, so there's no bulk-scan case here to gate behind a click).
    _item_or_none(repo, item_id)
    image_path = fetch_cover_art(rg_id)
    return templates.TemplateResponse(request, "partials/lazy_thumb.html", {"image_path": image_path})


@router.get("/items/{item_id}/lookup/musicbrainz-details", response_class=HTMLResponse)
def lookup_musicbrainz_details(
    request: Request, item_id: str, rg_id: str, title: str, artist: str = "", year: str = "",
    panel: str = "", repo: Repository = Depends(get_repository),
):
    # Rendered into the global #modal-slot, mirroring bgg-details -- the grid's
    # own lazy-loaded art (lookup_musicbrainz_thumb) is cheap/visual-only, but
    # committing to a candidate deserves the real tracklist and a correctly-
    # matched cover fetched fresh for THIS candidate, not whatever was (or
    # wasn't) loaded into the grid at render time.
    item = _item_or_none(repo, item_id)
    details = fetch_full_details(rg_id)
    match = {"Title": title, "Artist": artist, "Year Released": year, **details}
    return templates.TemplateResponse(
        request,
        "partials/musicbrainz_confirm_modal.html",
        {"item": item, "match": match, "match_json": _match_json(match), "panel": panel, "target_id": _target_id(item_id, panel)},
    )


@router.get("/categories/{slug}/new-lookup-panel", response_class=HTMLResponse)
def new_lookup_panel(request: Request, slug: str, repo: Repository = Depends(get_repository)):
    category = repo.get_category(slug)
    if category is None:
        raise HTTPException(404, f"Unknown category '{slug}'")
    return templates.TemplateResponse(request, "partials/new_item_lookup_panel.html", {"category": category})


@router.post("/items/{item_id}/apply-metadata", response_class=HTMLResponse)
async def apply_metadata(request: Request, item_id: str, repo: Repository = Depends(get_repository)):
    item = _get_item_or_404(repo, item_id)
    form = await request.form()

    attributes = dict(item.attributes)
    for f in item.category.fields:
        if f in form and str(form[f]).strip():
            attributes[f] = str(form[f]).strip()

    updates = {"attributes": attributes, "name": attributes.get(item.category.primary_field, item.name)}

    if "image_path" in form and str(form["image_path"]).strip():
        updates["image_path"] = str(form["image_path"]).strip()

    item = repo.update_item(item_id, **updates)

    if str(form.get("panel", "")) == "missing":
        response = HTMLResponse("")
        response.headers["HX-Trigger"] = "refreshList, refreshMissingMeta"
        return response

    return templates.TemplateResponse(request, "partials/item_row.html", {"item": item})


# Categories with an automated metadata lookup get a "missing metadata" banner.
# key_field is the attribute that (along with a missing image) marks an item as
# incomplete; lookup_path picks which /items/{id}/lookup/... endpoint the panel's
# per-row auto-search hits.
_MISSING_METADATA_CONFIG = {
    "movies": {"key_field": "Rating", "lookup_path": "omdb"},
    "games": {"key_field": "Number of Players", "lookup_path": "bgg"},
    # RAWG has no one field every game is guaranteed to carry, so video games are
    # flagged on a missing cover or Description only (key_field None).
    "video-games": {"key_field": None, "lookup_path": "rawg"},
}


def _missing_config(slug: str) -> dict | None:
    # Tolerate slug spelling differences (video-games / video_games / videogames).
    if slug in _MISSING_METADATA_CONFIG:
        return _MISSING_METADATA_CONFIG[slug]
    normalized = re.sub(r"[^a-z0-9]", "", slug.lower())
    if normalized == "videogames":
        return _MISSING_METADATA_CONFIG["video-games"]
    return None


def _items_missing_metadata(repo: Repository, slug: str) -> list[Item]:
    config = _missing_config(slug)
    if config is None:
        return []

    key_field = config["key_field"]

    def is_missing(item: Item) -> bool:
        if not item.image_path:
            return True
        if key_field and not item.attributes.get(key_field, "").strip():
            return True
        return not item.attributes.get("Description", "").strip()

    return [i for i in repo.list_items(category_slug=slug) if is_missing(i)]


@router.get("/categories/{slug}/missing-metadata", response_class=HTMLResponse)
def missing_metadata_banner(request: Request, slug: str, repo: Repository = Depends(get_repository)):
    if _missing_config(slug) is None:
        return HTMLResponse("")
    count = len(_items_missing_metadata(repo, slug))
    return templates.TemplateResponse(
        request, "partials/missing_metadata_banner.html", {"count": count, "category_slug": slug}
    )


@router.get("/categories/{slug}/missing-metadata/panel", response_class=HTMLResponse)
def missing_metadata_panel(request: Request, slug: str, repo: Repository = Depends(get_repository)):
    config = _missing_config(slug)
    if config is None:
        return HTMLResponse("")
    category = repo.get_category(slug)
    return templates.TemplateResponse(
        request,
        "partials/missing_metadata_panel.html",
        {"items": _items_missing_metadata(repo, slug), "category": category, "lookup_path": config["lookup_path"]},
    )


_AUTOFILL_BATCH = 10
_AUTOFILL_FIELDS = ("Genre", "ESRB Rating", "Year Released", "Number of Players", "Description")


@router.post("/categories/{slug}/missing-metadata/autofill", response_class=HTMLResponse)
async def missing_metadata_autofill(request: Request, slug: str, repo: Repository = Depends(get_repository)):
    """Fills in covers and details for games RAWG matches with >= 80% confidence.

    Works through the flagged items a few at a time (a whole library in one
    request would outlive Cloud Run's request timeout). Each response renders
    this batch's results plus a sentinel that fires the next batch as soon as it
    is swapped in, carrying the ids already handled and the running totals.
    Only empty fields are filled; anything already on an item is left alone.
    """
    config = _missing_config(slug)
    category = repo.get_category(slug)
    if config is None or category is None or config["lookup_path"] != "rawg":
        raise HTTPException(400, "Auto-fill isn't available for this category")

    form = await request.form()
    done = {i for i in str(form.get("done", "")).split(",") if i}
    applied_total = int(str(form.get("applied", "0")) or 0)
    skipped_total = int(str(form.get("skipped", "0")) or 0)

    todo = [i for i in _items_missing_metadata(repo, slug) if i.id not in done]
    batch, remaining = todo[:_AUTOFILL_BATCH], len(todo) - _AUTOFILL_BATCH

    results = []
    for item in batch:
        done.add(item.id)
        entry, score, guess = find_confident_match(item.name, item.attributes.get("Platform", ""))
        pct = int(round(score * 100))
        if entry is None:
            skipped_total += 1
            results.append({"ok": False, "name": item.name, "pct": pct, "guess": guess})
            continue

        attributes = dict(item.attributes)
        filled = []
        for key in _AUTOFILL_FIELDS:
            if key in category.fields and not str(attributes.get(key, "")).strip() and entry.get(key):
                attributes[key] = entry[key]
                filled.append(key)
        image_path = item.image_path
        if not image_path and entry.get("image_path"):
            image_path = entry["image_path"]
            filled.insert(0, "Cover")

        if filled:
            repo.update_item(item.id, image_path=image_path, attributes=attributes)
        applied_total += 1
        results.append({"ok": True, "name": item.name, "pct": pct, "guess": entry.get("Title", guess), "filled": filled})

    response = templates.TemplateResponse(
        request,
        "partials/missing_metadata_autofill.html",
        {
            "results": results,
            "slug": slug,
            "more": remaining > 0,
            "remaining": max(remaining, 0),
            "done": ",".join(sorted(done)),
            "applied": applied_total,
            "skipped": skipped_total,
        },
    )
    if remaining <= 0 or not batch:
        response.headers["HX-Trigger"] = "refreshList, refreshMissingMeta"
    return response
