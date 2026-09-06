"""Firestore-backed data access. Categories are keyed by slug; items get
auto-generated ids. There's no ORM here on purpose -- Firestore documents are
just dicts, so this is a thin, explicit translation layer between those dicts
and the dataclasses in app.models.
"""

from datetime import datetime, timezone

from google.cloud.firestore_v1.base_query import FieldFilter

from app.db import get_firestore_client
from app.models import Category, Item

CATEGORIES = "categories"
ITEMS = "items"


def _category_from_doc(doc) -> Category:
    d = doc.to_dict()
    return Category(
        slug=doc.id,
        name=d["name"],
        icon=d.get("icon", "📦"),
        primary_field=d["primary_field"],
        fields=d.get("fields", []),
        is_builtin=d.get("is_builtin", False),
    )


def _item_from_doc(doc, category: Category | None) -> Item:
    d = doc.to_dict()
    return Item(
        id=doc.id,
        category_slug=d["category_slug"],
        name=d["name"],
        image_path=d.get("image_path"),
        attributes=d.get("attributes", {}),
        created_at=d.get("created_at"),
        updated_at=d.get("updated_at"),
        category=category,
    )


class Repository:
    def __init__(self):
        self.db = get_firestore_client()

    # -- categories ---------------------------------------------------

    def list_categories(self) -> list[Category]:
        docs = self.db.collection(CATEGORIES).order_by("name").stream()
        return [_category_from_doc(d) for d in docs]

    def get_category(self, slug: str) -> Category | None:
        doc = self.db.collection(CATEGORIES).document(slug).get()
        return _category_from_doc(doc) if doc.exists else None

    def create_category(self, category: Category) -> None:
        self.db.collection(CATEGORIES).document(category.slug).set({
            "name": category.name,
            "icon": category.icon,
            "primary_field": category.primary_field,
            "fields": category.fields,
            "is_builtin": category.is_builtin,
        })

    def update_category(self, slug: str, **fields) -> None:
        self.db.collection(CATEGORIES).document(slug).update(fields)

    # -- items ----------------------------------------------------------

    def list_items(self, category_slug: str | None = None) -> list[Item]:
        categories = {c.slug: c for c in self.list_categories()}

        query = self.db.collection(ITEMS)
        if category_slug and category_slug != "all":
            query = query.where(filter=FieldFilter("category_slug", "==", category_slug))

        items = [_item_from_doc(d, categories.get(d.to_dict().get("category_slug"))) for d in query.stream()]
        items.sort(key=lambda i: i.name.lower())
        return items

    def get_item(self, item_id: str) -> Item | None:
        doc = self.db.collection(ITEMS).document(item_id).get()
        if not doc.exists:
            return None
        category = self.get_category(doc.to_dict().get("category_slug"))
        return _item_from_doc(doc, category)

    def create_item(self, category_slug: str, name: str, image_path: str | None, attributes: dict) -> Item:
        now = datetime.now(timezone.utc)
        ref = self.db.collection(ITEMS).document()
        ref.set({
            "category_slug": category_slug,
            "name": name,
            "image_path": image_path,
            "attributes": attributes,
            "created_at": now,
            "updated_at": now,
        })
        return self.get_item(ref.id)

    def update_item(self, item_id: str, **fields) -> Item | None:
        fields["updated_at"] = datetime.now(timezone.utc)
        self.db.collection(ITEMS).document(item_id).update(fields)
        return self.get_item(item_id)

    def delete_item(self, item_id: str) -> None:
        self.db.collection(ITEMS).document(item_id).delete()


def get_repository() -> Repository:
    return Repository()
