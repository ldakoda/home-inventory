from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Category:
    """A collection type (Movies, Board Games, Kitchen Gear, or a user-defined one).

    Built-in and custom categories are the same kind of row -- the old Streamlit app
    special-cased the three built-ins in separate CSVs/code paths; here they're just
    seeded documents in the `categories` Firestore collection, keyed by slug.
    """

    slug: str
    name: str
    icon: str = "📦"
    primary_field: str = ""
    fields: list[str] = field(default_factory=list)
    is_builtin: bool = False


@dataclass
class Item:
    id: str
    category_slug: str
    name: str
    image_path: str | None
    attributes: dict
    created_at: datetime | None = None
    updated_at: datetime | None = None
    category: Category | None = None
