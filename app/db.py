from functools import lru_cache

from google.cloud import firestore

from app.config import get_settings


@lru_cache
def get_firestore_client() -> firestore.Client:
    settings = get_settings()
    kwargs = {"project": settings.gcp_project} if settings.gcp_project else {}
    return firestore.Client(**kwargs)
