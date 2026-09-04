from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.deps import SESSION_COOKIE_NAME
from app.security import create_session_token, verify_password

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "next": next}
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, password: str = Form(""), next: str = Form("/")):
    if not verify_password(password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Incorrect password.", "next": next}, status_code=401
        )

    settings = get_settings()
    response = RedirectResponse(url=next or "/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_token(),
        max_age=settings.session_max_age_days * 86400,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
