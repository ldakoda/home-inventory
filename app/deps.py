from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.security import verify_session_token

SESSION_COOKIE_NAME = "home_inventory_session"


class RequiresLogin(StarletteHTTPException):
    """Raised by the auth dependency; caught by main.py to redirect to /login."""

    def __init__(self):
        super().__init__(status_code=303, detail="Login required")


def require_auth(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not verify_session_token(token):
        raise RequiresLogin()


async def auth_redirect_handler(request: Request, exc: RequiresLogin) -> RedirectResponse:
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)
