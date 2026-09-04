"""Image storage behind a small interface so the backing store (local disk today,
an S3-compatible bucket once the app moves to a host with an ephemeral filesystem)
can change without touching route/template code.
"""

import os
import re
import uuid
from abc import ABC, abstractmethod

from fastapi import UploadFile

from app.config import get_settings

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def safe_filename(original_name: str) -> str:
    stem, ext = os.path.splitext(original_name)
    stem = _SAFE_CHARS.sub("_", stem)[:80]
    ext = _SAFE_CHARS.sub("", ext)[:10]
    return f"{stem}_{uuid.uuid4().hex[:8]}{ext}"


class ImageStorage(ABC):
    @abstractmethod
    def save(self, upload: UploadFile) -> str:
        """Persist the uploaded file and return a URL/path usable in an <img src>."""

    @abstractmethod
    def delete(self, image_path: str) -> None:
        """Best-effort removal of a previously stored image."""


class LocalImageStorage(ImageStorage):
    def __init__(self, image_dir: str):
        self.image_dir = image_dir
        os.makedirs(self.image_dir, exist_ok=True)

    def save(self, upload: UploadFile) -> str:
        filename = safe_filename(upload.filename or "upload")
        dest_path = os.path.join(self.image_dir, filename)
        with open(dest_path, "wb") as f:
            f.write(upload.file.read())
        return f"/uploaded_images/{filename}"

    def delete(self, image_path: str) -> None:
        if not image_path.startswith("/uploaded_images/"):
            return
        filename = image_path.removeprefix("/uploaded_images/")
        full_path = os.path.join(self.image_dir, filename)
        try:
            os.remove(full_path)
        except OSError:
            pass


class GCSImageStorage(ImageStorage):
    """Images live in a Cloud Storage bucket with uniform (public-read) access
    configured at the bucket level, so uploaded files are reachable at a plain
    https://storage.googleapis.com/<bucket>/<name> URL.
    """

    def __init__(self, bucket_name: str):
        from google.cloud import storage as gcs

        self.client = gcs.Client()
        self.bucket = self.client.bucket(bucket_name)

    def save(self, upload: UploadFile) -> str:
        filename = safe_filename(upload.filename or "upload")
        blob = self.bucket.blob(filename)
        blob.upload_from_file(upload.file, content_type=upload.content_type)
        return blob.public_url

    def delete(self, image_path: str) -> None:
        filename = image_path.rsplit("/", 1)[-1]
        try:
            self.bucket.blob(filename).delete()
        except Exception:
            pass


def get_image_storage() -> ImageStorage:
    settings = get_settings()
    if settings.storage_backend == "gcs":
        return GCSImageStorage(settings.gcs_bucket_name)
    return LocalImageStorage(settings.image_dir)
