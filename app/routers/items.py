import difflib

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_auth
from app.integrations.bgg import fetch_bgg_game_details, fetch_bgg_game_matches
from app.integrations.image_search import search_multiple_web_images
from app.integrations.omdb import fetch_omdb_movie_matches
from app.models import Category, Item
from app.storage import get_image_storage

router = APIRouter(dependencies=[Depends(require_auth)])
templates = Jinja2Templates(directory="app/templates")


def _get_category_or_404(db: Session, slug: str) -> Category:
    category = db.query(Category).filter(Category.slug == slug).first()
    if category is None:
        raise HTTPException(404, f"Unknown category '{slug}'")
    return category


def _get_item_or_404(db: Session, item_id: int) -> Item:
    item = db.get(Item, item_id)
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


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    categories = db.query(Category).order_by(Category.name).all()
    items = db.query(Item).order_by(Item.name).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"categories": categories, "items": items, "active_category": "all", "q": ""},
    )


@router.get("/items", response_class=HTMLResponse)
def list_items(request: Request, q: str = "", category: str = "all", db: Session = Depends(get_db)):
    query = db.query(Item)
    if category != "all":
        query = query.join(Category).filter(Category.slug == category)
    items = query.order_by(Item.name).all()
    items = _fuzzy_filter(items, q)
    return templates.TemplateResponse(request, "partials/item_list.html", {"items": items})


@router.get("/items/{item_id}/row", response_class=HTMLResponse)
def item_row(request: Request, item_id: int, db: Session = Depends(get_db)):
    item = _get_item_or_404(db, item_id)
    return templates.TemplateResponse(request, "partials/item_row.html", {"item": item})


@router.get("/items/new", response_class=HTMLResponse)
def new_item_form(request: Request, db: Session = Depends(get_db)):
    categories = db.query(Category).order_by(Category.name).all()
    if not categories:
        return HTMLResponse("<p>Create a category first.</p>")
    return templates.TemplateResponse(
        request, "partials/add_form.html", {"categories": categories, "category": categories[0]}
    )


@router.get("/categories/{slug}/fields", response_class=HTMLResponse)
def category_fields(request: Request, slug: str, db: Session = Depends(get_db)):
    category = _get_category_or_404(db, slug)
    return templates.TemplateResponse(request, "partials/field_inputs.html", {"category": category, "values": {}})


@router.post("/items", response_class=HTMLResponse)
async def create_item(
    request: Request,
    category_slug: str = Form(""),
    image_url: str = Form(""),
    image_file: UploadFile | None = None,
    db: Session = Depends(get_db),
):
    category = _get_category_or_404(db, category_slug)
    form = await request.form()

    attributes = {field: str(form.get(field, "")).strip() for field in category.fields}
    name = attributes.get(category.primary_field, "").strip()
    if not name:
        return HTMLResponse("<p class='error'>The primary field is required.</p>", status_code=400)

    image_path = image_url.strip()
    if image_file is not None and image_file.filename:
        image_path = get_image_storage().save(image_file)

    item = Item(category_id=category.id, name=name, image_path=image_path or None, attributes=attributes)
    db.add(item)
    db.commit()

    response = HTMLResponse("")
    response.headers["HX-Trigger"] = "refreshList"
    return response


@router.get("/items/{item_id}/edit", response_class=HTMLResponse)
def edit_item_form(request: Request, item_id: int, db: Session = Depends(get_db)):
    item = _get_item_or_404(db, item_id)
    return templates.TemplateResponse(request, "partials/item_edit.html", {"item": item})


@router.put("/items/{item_id}", response_class=HTMLResponse)
async def update_item(
    request: Request,
    item_id: int,
    image_url: str = Form(""),
    image_file: UploadFile | None = None,
    db: Session = Depends(get_db),
):
    item = _get_item_or_404(db, item_id)
    form = await request.form()

    attributes = {field: str(form.get(field, item.attributes.get(field, ""))).strip() for field in item.category.fields}
    name = attributes.get(item.category.primary_field, "").strip()
    if not name:
        return HTMLResponse("<p class='error'>The primary field is required.</p>", status_code=400)

    item.attributes = attributes
    item.name = name

    if image_file is not None and image_file.filename:
        item.image_path = get_image_storage().save(image_file)
    elif image_url.strip():
        item.image_path = image_url.strip()

    db.commit()
    return templates.TemplateResponse(request, "partials/item_row.html", {"item": item})


@router.delete("/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = _get_item_or_404(db, item_id)
    db.delete(item)
    db.commit()
    return Response("", status_code=200)


@router.get("/items/{item_id}/lookup/omdb", response_class=HTMLResponse)
def lookup_omdb(request: Request, item_id: int, q: str, db: Session = Depends(get_db)):
    item = _get_item_or_404(db, item_id)
    matches = fetch_omdb_movie_matches(q)
    return templates.TemplateResponse(
        request, "partials/lookup_results.html", {"item": item, "matches": matches, "kind": "omdb"}
    )


@router.get("/items/{item_id}/lookup/bgg", response_class=HTMLResponse)
def lookup_bgg(request: Request, item_id: int, q: str, db: Session = Depends(get_db)):
    item = _get_item_or_404(db, item_id)
    raw_matches = fetch_bgg_game_matches(q)
    matches = []
    for m in raw_matches[:8]:
        matches.append({"id": m["id"], "Title": m["name"], "Year Released": m.get("year", "")})
    return templates.TemplateResponse(
        request, "partials/lookup_results.html", {"item": item, "matches": matches, "kind": "bgg"}
    )


@router.get("/items/{item_id}/lookup/bgg-details", response_class=HTMLResponse)
def lookup_bgg_details(request: Request, item_id: int, bgg_id: str, title: str, db: Session = Depends(get_db)):
    item = _get_item_or_404(db, item_id)
    details = fetch_bgg_game_details(bgg_id)
    match = {
        "Title": title,
        "Number of Players": details.get("Number of Players", ""),
        "Length of Play": details.get("Length of Play", ""),
        "Age Rating": details.get("Age Rating", ""),
        "image_path": details.get("image_path", ""),
    }
    return templates.TemplateResponse(
        request, "partials/lookup_results.html", {"item": item, "matches": [match], "kind": "bgg"}
    )


@router.get("/items/{item_id}/lookup/web-images", response_class=HTMLResponse)
def lookup_web_images(request: Request, item_id: int, q: str, db: Session = Depends(get_db)):
    item = _get_item_or_404(db, item_id)
    urls = search_multiple_web_images(q, num_results=8)
    matches = [{"image_path": url} for url in urls]
    return templates.TemplateResponse(
        request, "partials/lookup_results.html", {"item": item, "matches": matches, "kind": "image"}
    )


@router.post("/items/{item_id}/apply-metadata", response_class=HTMLResponse)
async def apply_metadata(request: Request, item_id: int, db: Session = Depends(get_db)):
    item = _get_item_or_404(db, item_id)
    form = await request.form()

    attributes = dict(item.attributes)
    for field in item.category.fields:
        if field in form and str(form[field]).strip():
            attributes[field] = str(form[field]).strip()

    item.attributes = attributes
    item.name = attributes.get(item.category.primary_field, item.name)

    if "image_path" in form and str(form["image_path"]).strip():
        item.image_path = str(form["image_path"]).strip()

    db.commit()
    return templates.TemplateResponse(request, "partials/item_row.html", {"item": item})
