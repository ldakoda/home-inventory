"""One-time import of the legacy per-category CSV files into the new SQLite schema.

Run once after setting up the new app (`python scripts/migrate_from_csv.py`). Safe to
re-run: it skips categories/items that already exist by slug/name rather than duplicating.
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Category, Item  # noqa: E402

BUILTIN_CATEGORIES = [
    {
        "slug": "movies",
        "name": "Movies & TV",
        "icon": "🎬",
        "primary_field": "Title",
        "fields": ["Title", "Rating", "Year Released", "Length of Movie", "Type", "Genre"],
        "csv_file": "movies_and_tv_collection.csv",
    },
    {
        "slug": "games",
        "name": "Board & Card Games",
        "icon": "🎲",
        "primary_field": "Title",
        "fields": ["Title", "Number of Players", "Length of Play", "Age Rating", "Style of Game"],
        "csv_file": "board_and_card_games_collection.csv",
    },
    {
        "slug": "kitchen",
        "name": "Kitchen Gear",
        "icon": "🍳",
        "primary_field": "Name of Item",
        "fields": ["Name of Item", "Type of Equipment", "Instruction Manual Link"],
        "csv_file": "kitchen_gear_inventory_v2.csv",
    },
]

CUSTOM_CATEGORIES_REGISTRY = "custom_categories_registry.csv"


def load_custom_category_defs() -> list[dict]:
    if not os.path.exists(CUSTOM_CATEGORIES_REGISTRY):
        return []

    reg_df = pd.read_csv(CUSTOM_CATEGORIES_REGISTRY)
    defs = []
    for _, row in reg_df.iterrows():
        fields = [f.strip() for f in str(row["Fields"]).split(",") if f.strip() and f.strip() != "Image_Path"]
        defs.append({
            "slug": str(row["Category Name"]).lower().replace(" ", "-"),
            "name": str(row["Category Name"]),
            "icon": str(row["Icon"]),
            "primary_field": str(row["Primary Col"]),
            "fields": fields,
            "csv_file": str(row["File Path"]),
        })
    return defs


def import_category(session, cat_def: dict) -> None:
    category = session.query(Category).filter(Category.slug == cat_def["slug"]).first()
    if category is None:
        category = Category(
            slug=cat_def["slug"],
            name=cat_def["name"],
            icon=cat_def["icon"],
            primary_field=cat_def["primary_field"],
            fields=cat_def["fields"],
            is_builtin=cat_def["slug"] in {c["slug"] for c in BUILTIN_CATEGORIES},
        )
        session.add(category)
        session.flush()
        print(f"Created category '{category.name}' ({category.slug})")
    else:
        print(f"Category '{category.name}' already exists, reusing it")

    csv_path = cat_def["csv_file"]
    if not os.path.exists(csv_path):
        print(f"  no CSV at {csv_path}, skipping items")
        return

    df = pd.read_csv(csv_path, on_bad_lines="skip")
    primary_col = cat_def["primary_field"]
    if primary_col not in df.columns:
        print(f"  primary field '{primary_col}' missing from {csv_path}, skipping items")
        return

    existing_names = {i.name.lower().strip() for i in category.items}
    imported = 0
    for _, row in df.iterrows():
        name = str(row.get(primary_col, "")).strip()
        if not name or name.lower() in existing_names:
            continue

        attributes = {}
        for field in cat_def["fields"]:
            value = row.get(field, "")
            attributes[field] = "" if pd.isna(value) else str(value).strip()

        image_path = row.get("Image_Path", "")
        image_path = "" if pd.isna(image_path) else str(image_path).strip()

        session.add(Item(
            category_id=category.id,
            name=name,
            image_path=image_path or None,
            attributes=attributes,
        ))
        existing_names.add(name.lower())
        imported += 1

    print(f"  imported {imported} item(s)")


def main():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        for cat_def in BUILTIN_CATEGORIES + load_custom_category_defs():
            import_category(session, cat_def)
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    main()
