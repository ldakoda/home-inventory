import logging
import os

import anyio.to_thread
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.deps import RequiresLogin, auth_redirect_handler
from app.routers import auth, categories, items

logging.basicConfig(level=logging.INFO)

settings = get_settings()

app = FastAPI(title="Home Inventory")
app.add_exception_handler(RequiresLogin, auth_redirect_handler)


@app.on_event("startup")
async def _raise_thread_pool_limit() -> None:
    # All routes here are sync `def`s, so FastAPI runs each one in Starlette's
    # shared worker-thread pool (default cap: 40). The missing-metadata panel
    # fires ~60 concurrent requests on its own, which was enough to fully
    # saturate that pool -- a real user click (e.g. "Select") made *during*
    # that scan couldn't get a thread to even start running on, regardless of
    # any request-level prioritization in bgg.py's rate limiter. Raise the cap
    # well above the panel's own concurrency so unrelated requests are never
    # starved by it.
    anyio.to_thread.current_default_thread_limiter().total_tokens = 200

app.mount("/static", StaticFiles(directory="app/static"), name="static")

if settings.storage_backend == "local":
    os.makedirs(settings.image_dir, exist_ok=True)
    app.mount("/uploaded_images", StaticFiles(directory=settings.image_dir), name="uploaded_images")

app.include_router(auth.router)
app.include_router(items.router)
app.include_router(categories.router)
