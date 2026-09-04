import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings


def verify_password(plain_password: str) -> bool:
    settings = get_settings()
    return bcrypt.checkpw(plain_password.encode("utf-8"), settings.auth_password_hash.encode("utf-8"))


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.secret_key, salt="home-inventory-session")


def create_session_token() -> str:
    return _serializer().dumps({"authenticated": True})


def verify_session_token(token: str | None) -> bool:
    if not token:
        return False
    settings = get_settings()
    max_age = settings.session_max_age_days * 86400
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return False
    return bool(data.get("authenticated"))
