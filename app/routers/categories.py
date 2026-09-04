from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_auth
from app.models import Category

router = APIRouter(dependencies=[Depends(require_auth)])
templates = Jinja2Templates(directory="app/templates")


def slugify(name: str) -> str:
    return "".join(c for c in name.lower().replace(" ", "-") if c.isalnum() or c == "-")


@router.get("/categories/new", response_class=HTMLResponse)
def new_category_form(request: Request):
    return templates.TemplateResponse(request, "partials/category_form.html", {})


@router.post("/categories", response_class=HTMLResponse)
def create_category(
    request: Request,
    name: str = Form(""),
    icon: str = Form("📦"),
    primary_field: str = Form(""),
    raw_fields: str = Form(""),
    db: Session = Depends(get_db),
):
    name = name.strip()
    primary_field = primary_field.strip()
    if not name or not primary_field:
        return templates.TemplateResponse(
            request,
            "partials/category_form.html",
            {"error": "Category name and primary field are both required."},
            status_code=400,
        )

    fields = [f.strip() for f in raw_fields.split(",") if f.strip()]
    if primary_field not in fields:
        fields.insert(0, primary_field)

    slug = slugify(name)
    if db.query(Category).filter(Category.slug == slug).first():
        return templates.TemplateResponse(
            request,
            "partials/category_form.html",
            {"error": f"A category named '{name}' already exists."},
            status_code=400,
        )

    category = Category(slug=slug, name=name, icon=icon, primary_field=primary_field, fields=fields)
    db.add(category)
    db.commit()

    categories = db.query(Category).order_by(Category.name).all()
    return templates.TemplateResponse(
        request,
        "partials/category_tabs.html",
        {"categories": categories, "active_category": slug, "oob": True},
    )
