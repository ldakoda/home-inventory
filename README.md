# Home Inventory

A personal inventory tracker (movies, board games, kitchen gear, and custom
categories you define) with a search/browse "finder" view, per-category
metadata lookup (OMDb, BoardGameGeek), and a fallback web image search.

Originally a single-file Streamlit app (see `legacy_streamlit_app/`); rebuilt
as a FastAPI + HTMX app on Firestore + Cloud Storage, deployed on Cloud Run.

Live at: https://home-inventory-245646809555.us-east1.run.app

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
- `GCP_PROJECT` — the Firebase/GCP project id (`home-inventory-caaf7`)
- `GOOGLE_APPLICATION_CREDENTIALS` — path to a service account key with
  Firestore + Storage access (see `secrets/`, gitignored -- not in the repo)
- `STORAGE_BACKEND` — `local` for dev (images on disk) or `gcs` for prod
- `OMDB_KEY` / `BGG_TOKEN` / `EMOJI_API_KEY` are optional; those features just
  degrade gracefully (no results) without them.

One-time import of data from the old Streamlit app's SQLite export:

```bash
python scripts/migrate_sqlite_to_firestore.py
```

Run it:

```bash
python -m uvicorn app.main:app --reload
```

## Deploying

```bash
gcloud run deploy home-inventory --source . --region us-east1 \
  --allow-unauthenticated --project home-inventory-caaf7 \
  --env-vars-file secrets/cloud-run-env.yaml
```

`secrets/cloud-run-env.yaml` (gitignored) holds `SECRET_KEY`,
`AUTH_PASSWORD_HASH`, `GCP_PROJECT`, `GCS_BUCKET_NAME` — Cloud Run reads these
as plain env vars; on a project with more than one maintainer these should
move to Secret Manager instead.

The deploying identity needs, on top of the Firebase Admin SDK's default
Firestore/Storage access: **Editor** (for Cloud Build + Artifact Registry)
and **Cloud Run Admin** (for the service + IAM binding that makes it public)
granted via IAM on both the Firebase Admin service account and the project's
default Compute Engine service account (Cloud Build's runtime identity).

## Architecture

- **Data model**: one `categories` Firestore collection (keyed by slug --
  built-in and custom categories are the same kind of document) + one `items`
  collection with a map `attributes` field for category-specific data.
  `app/repository.py` is a thin translation layer, not an ORM -- Firestore
  documents are just dicts. Replaces the old one-CSV-per-category design and
  the triplicated add/edit code paths that went with it.
- **Auth**: bcrypt password hash + a signed, expiring session cookie
  (`itsdangerous`). Replaces the old hardcoded PIN.
- **Storage**: `app/storage.py` is a small interface with two
  implementations -- local disk for dev, Cloud Storage for prod (public-read
  bucket; images here are movie posters/product photos, not sensitive).
  SQLite-on-a-Cloud-Storage-FUSE-mount was considered and rejected: that mount
  doesn't support write locking, so concurrent writes can silently lose data
  -- not acceptable for a live database file.
- **UI**: server-rendered Jinja2 templates + HTMX for interactivity (no JS
  build step, no SPA). `app/templates/partials/` are the HTMX fragment
  responses; edit/add/delete/search all swap DOM fragments in place.
- Git is no longer used as a database — the old app committed every single
  field edit and image upload straight to `main` via the GitHub API.

## Known gaps / not yet ported

- No pagination — fine at ~300 items, will matter if the inventory grows a lot.
- A few original `st.selectbox` fields (e.g. movie "Type", kitchen "Type of
  Equipment") are now plain text inputs rather than dropdowns.
- No automated tests yet.
- Secrets are plain Cloud Run env vars, not Secret Manager -- fine for a
  single-maintainer project, worth revisiting if that changes.
