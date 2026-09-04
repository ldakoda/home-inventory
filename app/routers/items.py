import difflib
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from app.deps import require_auth
from app.integrations.bgg import fetch_bgg_game_details, fetch_bgg_game_matches
from app.integrations.image_search import search_multiple_web_images
from app.integrations.omdb import fetch_omdb_movie_matches
from app.integrations.tmdb import search_tmdb
from app.models import Item
from app.repository import Repository, get_repository
from app.storage import get_image_storage

router = APIRouter(dependencies=[Depends(require_auth)])
templates = Jinja2Templates(directory="app/templates")


def _get_item_or_404(repo: Repository, item_id: str) -> Item:
    item = repo.get_item(item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    return item


def _fuzzy_filter(items: list[Item], query: str) -> list[Item]:
    query = query.strip().lower()
    if not query:
        return items

    query_words = query.split()

    def matches(item: Item) -> bool:
        name = item.name.lower()
        if query in name:
            return True
        if any(word in name for word in query_words):
            return True
        return difflib.SequenceMatcher(None, query, name).ratio() >= 0.5

    return [item for item in items if matches(item)]


_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
_COLLECTION_SUFFIX = re.compile(r"\s*\d*-?\s*movies?\s+collection\s*$", re.IGNORECASE)
_PACKAGING_WORD = re.compile(r"\s+(?:collection|trilogy|double feature)\s*$", re.IGNORECASE)


def _clean_movie_query(raw: str) -> str:
    """Inventory item names are retail packaging text ("Transformers 4-Movie
    Collection", "X-Men / X2 / X-Men: The Last Stand (Trilogy)", "Planet of the
    Apes Trilogy"), not the clean title a movie database indexes under. Strip that
    down to something searchable: take the first title out of a slash-separated
    multi-pack, then repeatedly drop a trailing parenthetical (edition/format
    label) or box-set word ("N-Movie(s) Collection", "Collection", "Trilogy",
    "Double Feature") until neither pattern matches anymore.
    """
    q = raw.split(" / ")[0].strip()
    while True:
        stripped = _TRAILING_PAREN.sub("", q).strip()
        stripped = _COLLECTION_SUFFIX.sub("", stripped).strip()
        stripped = _PACKAGING_WORD.sub("", stripped).strip()
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
    return templates.TemplateResponse(
        request,
        "index.html",
        {"categories": categories, "items": items, "active_category": "all", "q": ""},
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


def _target_id(item_id: str, panel: str) -> str:
    return f"missing-meta-item-{item_id}" if panel == "missing" else f"item-{item_id}"


@router.get("/items/{item_id}/lookup/omdb", response_class=HTMLResponse)
def lookup_omdb(request: Request, item_id: str, q: str, panel: str = "", repo: Repository = Depends(get_repository)):
    item = _get_item_or_404(repo, item_id)
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
            notes.append(f"No OMDb match -- showing TMDb results for '{cleaned_q}' instead (poster/title only, no rating or genre).")

    if looks_like_bundle:
        # A "Double Feature"/"Collection"/slash-separated combo (often a store-exclusive
        # 2-in-1 disc, e.g. a Walmart double feature) is its own retail product, not
        # something OMDb or TMDb indexes at all -- neither has a matching single title
        # or franchise entry for "movie A + movie B on one disc". Search the actual web
        # for that specific product's box art and offer it alongside any single-movie
        # matches above, since either could be the right image to use.
        #
        # The underlying search (scraped, not an official API) returns plenty of
        # unrelated junk for an ambiguous multi-word title -- a plain video thumbnail,
        # a stock photo, once literally a grammar lesson. Only keep hits from domains
        # that are actually movie/media retailers, and over-fetch since most
        # candidates get discarded by that filter.
        existing_images = {m.get("image_path") for m in matches}
        raw_box_art = search_multiple_web_images(f"{q} DVD cover", num_results=24)
        box_art = [url for url in raw_box_art if _is_media_retailer(url)][:6]
        matches = matches + [{"image_path": url} for url in box_art if url not in existing_images]
        if box_art:
            notes.append("This looks like a multi-movie combo pack -- web results for the actual box art are included too (poster only, no other metadata).")

    return templates.TemplateResponse(
        request,
        "partials/lookup_results.html",
        {"item": item, "matches": matches, "kind": "omdb", "target_id": _target_id(item_id, panel), "panel": panel, "note": " ".join(notes) or None},
    )


@router.get("/items/{item_id}/lookup/bgg", response_class=HTMLResponse)
def lookup_bgg(request: Request, item_id: str, q: str, repo: Repository = Depends(get_repository)):
    item = _get_item_or_404(repo, item_id)
    raw_matches = fetch_bgg_game_matches(q)
    matches = []
    for m in raw_matches[:8]:
        matches.append({"id": m["id"], "Title": m["name"], "Year Released": m.get("year", "")})
    return templates.TemplateResponse(
        request,
        "partials/lookup_results.html",
        {"item": item, "matches": matches, "kind": "bgg", "target_id": _target_id(item_id, ""), "panel": ""},
    )


@router.get("/items/{item_id}/lookup/bgg-details", response_class=HTMLResponse)
def lookup_bgg_details(request: Request, item_id: str, bgg_id: str, title: str, repo: Repository = Depends(get_repository)):
    item = _get_item_or_404(repo, item_id)
    details = fetch_bgg_game_details(bgg_id)
    match = {
        "Title": title,
        "Number of Players": details.get("Number of Players", ""),
        "Length of Play": details.get("Length of Play", ""),
        "Age Rating": details.get("Age Rating", ""),
        "image_path": details.get("image_path", ""),
    }
    return templates.TemplateResponse(
        request,
        "partials/lookup_results.html",
        {"item": item, "matches": [match], "kind": "bgg", "target_id": _target_id(item_id, ""), "panel": ""},
    )


@router.get("/items/{item_id}/lookup/web-images", response_class=HTMLResponse)
def lookup_web_images(request: Request, item_id: str, q: str, repo: Repository = Depends(get_repository)):
    item = _get_item_or_404(repo, item_id)
    urls = search_multiple_web_images(q, num_results=8)
    matches = [{"image_path": url} for url in urls]
    return templates.TemplateResponse(
        request,
        "partials/lookup_results.html",
        {"item": item, "matches": matches, "kind": "image", "target_id": _target_id(item_id, ""), "panel": ""},
    )


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


def _movies_missing_metadata(repo: Repository) -> list[Item]:
    def is_missing(item: Item) -> bool:
        rating = item.attributes.get("Rating", "").strip()
        return not item.image_path or not rating

    return [i for i in repo.list_items(category_slug="movies") if is_missing(i)]


@router.get("/categories/{slug}/missing-metadata", response_class=HTMLResponse)
def missing_metadata_banner(request: Request, slug: str, repo: Repository = Depends(get_repository)):
    if slug != "movies":
        return HTMLResponse("")
    count = len(_movies_missing_metadata(repo))
    return templates.TemplateResponse(
        request, "partials/missing_metadata_banner.html", {"count": count, "category_slug": slug}
    )


@router.get("/categories/{slug}/missing-metadata/panel", response_class=HTMLResponse)
def missing_metadata_panel(request: Request, slug: str, repo: Repository = Depends(get_repository)):
    if slug != "movies":
        return HTMLResponse("")
    return templates.TemplateResponse(
        request, "partials/missing_metadata_panel.html", {"items": _movies_missing_metadata(repo)}
    )
