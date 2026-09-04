import difflib
import re

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

    matches = fetch_omdb_movie_matches(q)
    if not matches and cleaned_q != q:
        matches = fetch_omdb_movie_matches(cleaned_q)
    note = None

    if not matches:
        # OMDb only indexes individual titles -- a box-set/collection name (or any title
        # it doesn't have) won't match. TMDb also has real "collection" entries for
        # franchises/box-sets, so it's a much better fallback than a generic image search.
        # Search on the cleaned title ("Transformers 4-Movie Collection" -> "Transformers"),
        # since the literal retail packaging text rarely matches either database directly.
        matches = search_tmdb(cleaned_q)
        if matches:
            note = "No OMDb match (common for box-set/collection titles) -- showing TMDb results for '" + cleaned_q + "' instead. Collection hits only set the poster/title, not rating or genre."

    return templates.TemplateResponse(
        request,
        "partials/lookup_results.html",
        {"item": item, "matches": matches, "kind": "omdb", "target_id": _target_id(item_id, panel), "panel": panel, "note": note},
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
