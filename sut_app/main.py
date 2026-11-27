from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, timedelta
from time import time
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

try:
    from . import database
except ImportError:
    import database


APP_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))
ALLOWED_OPT_IN = {"subscribed", "unsubscribed", "pending"}


class ContactInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    first_name: str = Field(min_length=1, max_length=100)
    opt_in_status: str

    @field_validator("opt_in_status")
    @classmethod
    def validate_opt_in_status(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_OPT_IN:
            raise ValueError("opt_in_status must be subscribed, unsubscribed, or pending")
        return normalized


class OrderInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    sku: str = Field(min_length=1, max_length=100)
    amount: float = Field(ge=0, allow_inf_nan=False)
    purchase_date: date
    customer_email: EmailStr | None = None


class CopyRequest(BaseModel):
    product_name: str = Field(min_length=1, max_length=120)
    audience: str = Field(min_length=1, max_length=160)
    discount_code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")


class AppLogin(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)
    next_path: str = Field(default="/", max_length=30)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize_db()
    yield


app = FastAPI(title="Dotdigital-Lite", version="1.0.0", lifespan=lifespan)
APP_SESSION_COOKIE = "app_session"
APP_SESSION_TTL = 8 * 60 * 60
APP_UI_PATHS = {"/", "/contacts", "/dashboard/kpis", "/assistant"}
LOGIN_RETURN_PATHS = {"/", "/contacts", "/assistant"}


@app.middleware("http")
async def require_api_key_for_api(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        expected_key = os.getenv("APP_API_KEY", "").strip()
        if not expected_key or expected_key.lower().startswith("replace-with-"):
            return JSONResponse(
                {"detail": "API access is not configured. Set APP_API_KEY to a unique secret."},
                status_code=503,
            )
        supplied_key = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(supplied_key.encode("utf-8"), expected_key.encode("utf-8")):
            return JSONResponse({"detail": "API key required or invalid"}, status_code=401)
    return await call_next(request)


def app_auth_configuration() -> tuple[str, str, bytes] | None:
    username = os.getenv("ASSISTANT_USERNAME")
    password = os.getenv("ASSISTANT_PASSWORD")
    session_secret_value = os.getenv("ASSISTANT_SESSION_SECRET", "")
    session_secret = session_secret_value.encode("utf-8")
    if (
        not username
        or not username.strip()
        or username.strip().lower().startswith("replace-with-")
        or not password
        or not password.strip()
        or password.strip().lower().startswith("replace-with-")
        or session_secret_value.strip().lower().startswith("replace-with-")
        or len(session_secret) < 32
    ):
        return None
    return username, password, session_secret


def create_app_session(session_secret: bytes) -> tuple[str, str]:
    csrf_token = secrets.token_urlsafe(32)
    payload = json.dumps(
        {"csrf_token": csrf_token, "expires_at": int(time()) + APP_SESSION_TTL},
        separators=(",", ":"),
    ).encode("utf-8")
    encoded_payload = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    signature = hmac.new(session_secret, encoded_payload.encode("ascii"), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{encoded_payload}.{encoded_signature}", csrf_token


def read_app_session(token: str | None, session_secret: bytes) -> dict[str, int | str] | None:
    if not token:
        return None
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        signature = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
        expected_signature = hmac.new(
            session_secret,
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            return None
        payload = base64.urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4))
        session = json.loads(payload)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        return None

    csrf_token = session.get("csrf_token") if isinstance(session, dict) else None
    expires_at = session.get("expires_at") if isinstance(session, dict) else None
    if not isinstance(csrf_token, str) or not isinstance(expires_at, int) or expires_at <= int(time()):
        return None
    return session


def require_app_session(request: Request) -> tuple[dict[str, int | str], bytes]:
    configuration = app_auth_configuration()
    if configuration is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "App sign-in is not configured. Set ASSISTANT_USERNAME, "
                "ASSISTANT_PASSWORD, and an ASSISTANT_SESSION_SECRET of at least 32 characters."
            ),
        )
    session = read_app_session(
        request.cookies.get(APP_SESSION_COOKIE),
        configuration[2],
    )
    if session is None:
        raise HTTPException(status_code=401, detail="Sign in to use the app")
    return session, configuration[2]


def require_app_csrf(request: Request, session: dict[str, int | str]) -> None:
    csrf_token = request.headers.get("X-CSRF-Token", "")
    if not hmac.compare_digest(str(session["csrf_token"]), csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")


@app.middleware("http")
async def require_app_login(request: Request, call_next):
    if request.method not in {"GET", "HEAD"} or request.url.path not in APP_UI_PATHS:
        return await call_next(request)

    configuration = app_auth_configuration()
    if configuration is None:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"configured": False, "next_path": request.url.path},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )

    session = read_app_session(
        request.cookies.get(APP_SESSION_COOKIE),
        configuration[2],
    )
    if session is None:
        if request.url.path == "/dashboard/kpis" and request.headers.get("HX-Request") == "true":
            return HTMLResponse("Your session expired. Reload the page and sign in again.", status_code=401)
        destination = request.url.path if request.url.path in LOGIN_RETURN_PATHS else "/"
        return RedirectResponse(f"/login?next={destination}", status_code=303)

    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, sku_prefix: str | None = Query(default=None)):
    session, _ = require_app_session(request)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "active_page": "dashboard",
            "csrf_token": session["csrf_token"],
            "sku_prefix": sku_prefix or "",
        },
    )


@app.get("/contacts", response_class=HTMLResponse)
def contacts_page(request: Request):
    session, _ = require_app_session(request)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id, first_name, email, opt_in_status FROM contacts ORDER BY first_name COLLATE NOCASE, email"
        ).fetchall()
    return templates.TemplateResponse(
        request=request,
        name="contacts.html",
        context={
            "active_page": "contacts",
            "contacts": [dict(row) for row in rows],
            "csrf_token": session["csrf_token"],
        },
    )


@app.post("/contacts", status_code=201)
def add_contact(request: Request, contact: ContactInput):
    session, _ = require_app_session(request)
    require_app_csrf(request, session)
    try:
        with database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO contacts (email, first_name, opt_in_status) VALUES (?, ?, ?)",
                (str(contact.email), contact.first_name, contact.opt_in_status),
            )
    except sqlite3.IntegrityError as error:
        if "UNIQUE constraint failed: contacts.email" in str(error):
            raise HTTPException(status_code=409, detail="A contact with that email already exists") from error
        raise
    return {"id": cursor.lastrowid, **contact.model_dump(mode="json")}


@app.delete("/contacts/{contact_id}")
def delete_contact(contact_id: int, request: Request):
    session, _ = require_app_session(request)
    require_app_csrf(request, session)
    with database.connect() as connection:
        cursor = connection.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Contact not found")
    return {"deleted": True, "id": contact_id}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next_path: str = Query(default="/", alias="next")):
    configured = app_auth_configuration() is not None
    if next_path not in LOGIN_RETURN_PATHS:
        next_path = "/"
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"configured": configured, "next_path": next_path},
        status_code=200 if configured else 503,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/login")
def app_login(login: AppLogin, request: Request):
    configuration = app_auth_configuration()
    if configuration is None:
        raise HTTPException(
            status_code=503,
            detail="App sign-in is not configured. Set its environment variables before signing in.",
        )
    expected_username, expected_password, session_secret = configuration
    username_matches = hmac.compare_digest(
        login.username.encode("utf-8"),
        expected_username.encode("utf-8"),
    )
    password_matches = hmac.compare_digest(
        login.password.encode("utf-8"),
        expected_password.encode("utf-8"),
    )
    if not username_matches or not password_matches:
        return JSONResponse({"detail": "Invalid username or password"}, status_code=401)

    token, _ = create_app_session(session_secret)
    next_path = login.next_path if login.next_path in LOGIN_RETURN_PATHS else "/"
    response = JSONResponse({"ok": True, "next": next_path})
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        APP_SESSION_COOKIE,
        token,
        max_age=APP_SESSION_TTL,
        httponly=True,
        secure=os.getenv("SESSION_COOKIE_SECURE", "").strip().lower() == "true"
        or request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    return response


@app.get("/assistant", response_class=HTMLResponse)
def assistant_page(request: Request):
    session, _ = require_app_session(request)
    return templates.TemplateResponse(
        request=request,
        name="assistant.html",
        context={
            "active_page": "assistant",
            "csrf_token": session["csrf_token"],
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post("/assistant/generate")
async def assistant_generate(request: Request, copy_request: CopyRequest):
    session, _ = require_app_session(request)
    require_app_csrf(request, session)
    return await generate_marketing_copy(copy_request)


@app.post("/logout")
def app_logout(request: Request):
    session, _ = require_app_session(request)
    require_app_csrf(request, session)
    response = JSONResponse({"ok": True})
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(
        APP_SESSION_COOKIE,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    return response


@app.get("/dashboard/kpis", response_class=HTMLResponse)
def dashboard_kpis(
    request: Request,
    days: str = Query(default="30"),
    sku_prefix: str | None = Query(default=None),
):
    kpis = calculate_kpis(days, sku_prefix)
    return templates.TemplateResponse(
        request=request,
        name="kpi_fragment.html",
        context={"kpis": kpis},
    )


@app.post("/api/contacts/import")
def import_contacts(
    payload: list[dict[str, Any]],
    simulate_bug: bool = Query(default=False),
):
    valid_contacts: list[ContactInput] = []
    for row in payload:
        try:
            valid_contacts.append(ContactInput.model_validate(row))
        except Exception:
            if not simulate_bug:
                raise HTTPException(status_code=422, detail="One or more contacts are invalid")

    inserted = 0
    with database.connect() as connection:
        for contact in valid_contacts:
            try:
                connection.execute(
                    "INSERT INTO contacts (email, first_name, opt_in_status) VALUES (?, ?, ?)",
                    (str(contact.email), contact.first_name, contact.opt_in_status),
                )
                inserted += 1
            except Exception as error:
                if "UNIQUE constraint failed" in str(error):
                    raise HTTPException(status_code=409, detail="Contact email already exists") from error
                raise
    if simulate_bug:
        return {"imported": len(payload)}
    return {"imported": inserted, "rejected": 0}


@app.get("/api/contacts")
def get_contacts():
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT email, first_name, opt_in_status FROM contacts ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/orders")
def create_order(order: OrderInput):
    with database.connect() as connection:
        cursor = connection.execute(
            "INSERT INTO orders (sku, amount, purchase_date, customer_email) VALUES (?, ?, ?, ?)",
            (order.sku, order.amount, order.purchase_date.isoformat(), str(order.customer_email) if order.customer_email else None),
        )
        order_id = cursor.lastrowid
    return {"id": order_id, **order.model_dump(mode="json")}


def calculate_kpis(days: str, sku_prefix: str | None = None) -> dict[str, int | float | str]:
    conditions: list[str] = []
    parameters: list[Any] = []
    if days != "all":
        try:
            day_count = int(days)
            if day_count <= 0:
                raise ValueError
        except ValueError as error:
            raise HTTPException(status_code=422, detail="days must be a positive integer or 'all'") from error
        cutoff = (date.today() - timedelta(days=day_count - 1)).isoformat()
        conditions.append("purchase_date >= ?")
        parameters.append(cutoff)

    if sku_prefix:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", sku_prefix):
            raise HTTPException(status_code=422, detail="Invalid SKU prefix")
        conditions.append("sku LIKE ?")
        parameters.append(f"{sku_prefix}%")
    date_filter = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with database.connect() as connection:
        row = connection.execute(
            f"""SELECT COALESCE(SUM(amount), 0) AS total_sales,
                       COUNT(*) AS order_count,
                       COUNT(DISTINCT customer_email) AS unique_customers
                FROM orders {date_filter}""",
            tuple(parameters),
        ).fetchone()

    total_sales = round(float(row["total_sales"]), 2)
    order_count = int(row["order_count"])
    return {
        "days": days,
        "total_sales": total_sales,
        "order_count": order_count,
        "unique_customers": int(row["unique_customers"]),
        "average_order_value": round(total_sales / order_count, 2) if order_count else 0.0,
    }


@app.get("/api/analytics/kpis")
def get_kpis(
    days: str = Query(default="30"),
    sku_prefix: str | None = Query(default=None),
):
    return calculate_kpis(days, sku_prefix)


def mock_copy(product_name: str, audience: str, discount_code: str) -> str:
    return (
        f"Meet {product_name}, made for {audience}. Enjoy a little something extra with code "
        f"{discount_code} at checkout. Discover your new favorite today."
    )


@app.post("/api/ai/generate-copy")
async def generate_copy(
    request: CopyRequest,
    hallucinate: bool = Query(default=False),
):
    return await generate_marketing_copy(request, hallucinate)


async def generate_marketing_copy(
    request: CopyRequest,
    hallucinate: bool = False,
) -> dict[str, str]:
    api_key = os.getenv("OPENAI_API_KEY")
    model = "mock"
    if api_key:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                        "messages": [
                            {"role": "system", "content": "Write concise, warm marketing copy. Only use the provided discount code; do not invent codes. Keep copy under 60 words."},
                            {"role": "user", "content": f"Product: {request.product_name}\nAudience: {request.audience}\nAllowed discount code: {request.discount_code}"},
                        ],
                        "temperature": 0.4,
                    },
                )
                response.raise_for_status()
                copy = response.json()["choices"][0]["message"]["content"].strip()
                model = "openai"
        except (httpx.HTTPError, KeyError, IndexError, TypeError):
            copy = mock_copy(request.product_name, request.audience, request.discount_code)
    else:
        copy = mock_copy(request.product_name, request.audience, request.discount_code)

    if hallucinate:
        copy += " Use code EXTRA50 for another surprise discount."
    return {"copy": copy, "model": model}


@app.delete("/api/test-cleanup/{prefix}")
def cleanup_test_data(prefix: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", prefix):
        raise HTTPException(status_code=400, detail="Invalid cleanup prefix")
    with database.connect() as connection:
        contacts = connection.execute(
            "DELETE FROM contacts WHERE email LIKE ? OR first_name LIKE ?",
            (f"{prefix}%", f"{prefix}%"),
        ).rowcount
        orders = connection.execute(
            "DELETE FROM orders WHERE sku LIKE ? OR customer_email LIKE ?",
            (f"{prefix}%", f"{prefix}%"),
        ).rowcount
    return {"deleted_contacts": contacts, "deleted_orders": orders}