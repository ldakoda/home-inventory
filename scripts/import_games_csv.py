"""Add games from a CSV (columns: Title, Platform) to the Video Games category.
Dry run by default; pass --apply to write. Safe to re-run: an item is skipped
if one with the same title AND platform already exists in the category.

Requires GOOGLE_APPLICATION_CREDENTIALS (or ADC) with Firestore write access,
and GCP_PROJECT if it can't be inferred -- same as migrate_sqlite_to_firestore.py.

Usage: python scripts/import_games_csv.py video_games_import.csv [--apply] [--category "Video Games"]
"""

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.repository import get_repository  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--apply", action="store_true", help="write to Firestore (default is a dry run)")
    parser.add_argument("--category", default="Video Games", help="category name to add the items to")
    args = parser.parse_args()

    repo = get_repository()
    category = next((c for c in repo.list_categories() if c.name.lower() == args.category.lower()), None)
    if category is None:
        names = ", ".join(c.name for c in repo.list_categories())
        sys.exit(f"No category named '{args.category}'. Available: {names}")

    existing = {
        (i.name.lower().strip(), (i.attributes.get("Platform") or "").lower().strip())
        for i in repo.list_items(category_slug=category.slug)
    }
    existing_names = {name for name, _ in existing}

    added = skipped = 0
    with open(args.csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            title = (row.get("Title") or "").strip()
            platform = (row.get("Platform") or "").strip()
            if not title:
                continue
            key = (title.lower(), platform.lower())
            if key in existing:
                skipped += 1
                print(f"skip   {title} [{platform}] (already there)")
                continue
            note = "  (same title on another platform already exists)" if title.lower() in existing_names else ""
            print(f"{'add ' if args.apply else 'would add'}  {title} [{platform}]{note}")
            if args.apply:
                repo.create_item(category_slug=category.slug, name=title, image_path=None, attributes={"Platform": platform})
            existing.add(key)
            added += 1

    print(f"\n{'Added' if args.apply else 'Would add'} {added}, skipped {skipped}.")
    if not args.apply:
        print("Dry run only -- re-run with --apply to write.")


if __name__ == "__main__":
    main()
