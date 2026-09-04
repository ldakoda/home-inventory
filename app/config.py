import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.secret_key = os.environ["SECRET_KEY"]
        self.auth_password_hash = os.environ["AUTH_PASSWORD_HASH"]
        self.session_max_age_days = int(os.getenv("SESSION_MAX_AGE_DAYS", "30"))

        self.gcp_project = os.getenv("GCP_PROJECT", "")

        # Image storage: "local" (default, for dev) or "gcs" (Cloud Storage, for prod).
        self.storage_backend = os.getenv("STORAGE_BACKEND", "local")
        self.image_dir = os.getenv("IMAGE_DIR", "uploaded_images")
        self.gcs_bucket_name = os.getenv("GCS_BUCKET_NAME", "")

        self.omdb_api_key = os.getenv("OMDB_KEY", "")
        self.bgg_api_token = os.getenv("BGG_TOKEN", "")
        self.emoji_api_key = os.getenv("EMOJI_API_KEY", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
