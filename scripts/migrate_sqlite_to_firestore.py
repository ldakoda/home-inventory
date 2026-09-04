"""One-time push of the local SQLite data (already imported from the original
Streamlit app's CSVs) into Firestore. Safe to re-run: categories are upserted
by slug, and items are skipped if a same-named item already exists in that
category.

Requires GOOGLE_APPLICATION_CREDENTIALS (or ADC) to point at a service
account with Firestore write access, and GCP_PROJECT set if it can't be
inferred from the credentials.

Usage: python scripts/migrate_sqlite_to_firestore.py [path-to-inventory.db]
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Category  # noqa: E402
from app.repository import get_repository  # noqa: E402

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "inventory.db")


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
    if not os.path.exists(db_path):
        print(f"No SQLite database at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    repo = get_repository()

    categories_by_id = {}
    for row in conn.execute("SELECT id, slug, name, icon, primary_field, fields, is_builtin FROM categories"):
        slug = row["slug"]
        categories_by_id[row["id"]] = slug

        if repo.get_category(slug) is not None:
            print(f"Category '{slug}' already in Firestore, reusing it")
            continue

        repo.create_category(Category(
            slug=slug,
            name=row["name"],
            icon=row["icon"],
            primary_field=row["primary_field"],
            fields=json.loads(row["fields"]),
            is_builtin=bool(row["is_builtin"]),
        ))
        print(f"Created category '{row['name']}' ({slug})")

    imported = 0
    skipped = 0
    for row in conn.execute("SELECT category_id, name, image_path, attributes FROM items"):
        slug = categories_by_id.get(row["category_id"])
        if slug is None:
            continue

        existing = [i for i in repo.list_items(category_slug=slug) if i.name.lower().strip() == row["name"].lower().strip()]
        if existing:
            skipped += 1
            continue

        repo.create_item(
            category_slug=slug,
            name=row["name"],
            image_path=row["image_path"],
            attributes=json.loads(row["attributes"]),
        )
        imported += 1

    conn.close()
    print(f"Imported {imported} item(s), skipped {skipped} already-present item(s)")


if __name__ == "__main__":
    main()
