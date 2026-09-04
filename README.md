# Home Inventory

A personal inventory tracker (movies, board games, kitchen gear, and custom
categories you define) with a search/browse "finder" view, per-category
metadata lookup (OMDb, BoardGameGeek), and a fallback web image search.

Originally a single-file Streamlit app (see `legacy_streamlit_app/`); rebuilt
as a FastAPI + HTMX + SQLite app.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate   # or `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:
- `SECRET_KEY` — `python -c "import secrets; print(secrets.token_hex(32))"`
- `AUTH_PASSWORD_HASH` — `python -c "from app.security import hash_password; print(hash_password('your-password'))"`
- `OMDB_KEY` / `BGG_TOKEN` / `EMOJI_API_KEY` are optional; those features just
  degrade gracefully (no results) without them.

One-time import of the old CSV data:

```bash
python scripts/migrate_from_csv.py
```

Run it:

```bash
python -m uvicorn app.main:app --reload
```

## Architecture

- **Data model**: one `Category` table (built-in and custom categories are
  the same kind of row) + one `Item` table with a JSON `attributes` column
  for category-specific fields. Replaces the old one-CSV-per-category design
  and the triplicated add/edit code paths that went with it.
- **Auth**: bcrypt password hash + a signed, expiring session cookie
  (`itsdangerous`). Replaces the old hardcoded PIN.
- **Storage**: images live on local disk via `app/storage.py`, which is a
  small interface — swap in an S3-compatible backend later (for a host with
  an ephemeral filesystem) without touching routes or templates.
- **UI**: server-rendered Jinja2 templates + HTMX for interactivity (no JS
  build step, no SPA). `app/templates/partials/` are the HTMX fragment
  responses; edit/add/delete/search all swap DOM fragments in place.
- Git is no longer used as a database — the old app committed every single
  field edit and image upload straight to `main` via the GitHub API. The
  SQLite file and `uploaded_images/` uploads are now gitignored; only code is
  versioned.

## Known gaps / not yet ported

- No pagination — fine at ~300 items, will matter if the inventory grows a lot.
- A few original `st.selectbox` fields (e.g. movie "Type", kitchen "Type of
  Equipment") are now plain text inputs rather than dropdowns.
- No automated tests yet.
- Hosting/deployment target not finalized — works locally today; the DB and
  storage layers are written to swap backends without a rewrite once a host
  is picked.
