import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import Base, engine
from app.deps import RequiresLogin, auth_redirect_handler
from app.routers import auth, categories, items

logging.basicConfig(level=logging.INFO)

settings = get_settings()

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Home Inventory")
app.add_exception_handler(RequiresLogin, auth_redirect_handler)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/uploaded_images", StaticFiles(directory=settings.image_dir), name="uploaded_images")

app.include_router(auth.router)
app.include_router(items.router)
app.include_router(categories.router)
