"""Ganesh Festival dashboard with role-based access and Google Sheet storage."""

from __future__ import annotations

import csv
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request as FastAPIRequest, Response
from pydantic import BaseModel
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
load_dotenv(BASE_DIR / ".env")

SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1gdSGkcrZ2tQcyyxO7Y2-vkMPz29nB2hXgWHoCYzlU_c")
SHEET_GID = os.getenv("GOOGLE_SHEET_GID", "0")
COMMITTEE_SHEET_NAME = os.getenv("COMMITTEE_SHEET_NAME", "committee-members")
COMMITTEE_MEMBER_COUNT = max(1, int(os.getenv("COMMITTEE_MEMBER_COUNT", "22")))
REFRESH_SECONDS = max(5, int(os.getenv("REFRESH_SECONDS", "5")))
WRITE_URL = os.getenv("GOOGLE_SHEETS_WRITE_URL", "").strip()
WRITE_TOKEN = os.getenv("GOOGLE_SHEETS_WRITE_TOKEN", "").strip()
WRITE_TIMEOUT_SECONDS = max(20, int(os.getenv("GOOGLE_SHEETS_WRITE_TIMEOUT_SECONDS", "60")))
APP_SECRET = os.getenv("APP_SECRET", "replace-this-secret-before-production")
SESSION_COOKIE = "ganesh_session"
SESSION_SECONDS = max(300, int(os.getenv("SESSION_SECONDS", "28800")))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
VIEWER_USERNAME = os.getenv("VIEWER_USERNAME", "viewer")
VIEWER_PASSWORD = os.getenv("VIEWER_PASSWORD", "viewer123")
USER_DB_PATH = Path(os.getenv("USER_DB_PATH", str(BASE_DIR / "data" / "users.sqlite3")))
USER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Ganesh Festival Dashboard", version="1.0.0")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

_cache: dict[str, Any] = {"at": 0.0, "payload": None}
_cache_lock = threading.Lock()


class LoginPayload(BaseModel):
    username: str
    password: str


class RegisterPayload(BaseModel):
    username: str
    password: str
    confirm_password: str


class ChangePasswordPayload(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


class ChangeViewerPasswordPayload(BaseModel):
    new_password: str
    confirm_password: str


class EntryPayload(BaseModel):
    date: str = ""
    type: str
    name: str = ""
    category: str = ""
    amount: Any = ""
    payment_method: str = ""
    description: str = ""
    phone_number: str = ""
    donation_amount: Any = ""
    committee_member_amount: Any = ""
    committee_member_name: str = ""
    amount_asked_by: str = ""
    added_by: str = ""
    receipt_name: str = ""
    receipt_mime_type: str = ""
    receipt_data: str = ""


def initialize_user_db() -> None:
    with sqlite3.connect(USER_DB_PATH) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return f"{salt.hex()}${digest.hex()}"


def password_matches(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, digest_hex = stored_hash.split("$", 1)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 260_000)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def registered_user_matches(username: str, password: str) -> bool:
    with sqlite3.connect(USER_DB_PATH) as connection:
        row = connection.execute(
            "SELECT password_hash FROM users WHERE username = ?",
            (username.lower(),),
        ).fetchone()
    return bool(row and password_matches(password, row[0]))


def admin_password_matches(password: str) -> bool:
    with sqlite3.connect(USER_DB_PATH) as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE name = 'admin_password_hash'"
        ).fetchone()
    return password_matches(password, row[0]) if row else hmac.compare_digest(password, ADMIN_PASSWORD)


def set_admin_password(password: str) -> None:
    with sqlite3.connect(USER_DB_PATH) as connection:
        connection.execute(
            "INSERT INTO settings(name, value) VALUES('admin_password_hash', ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (password_hash(password),),
        )


def set_session(response: Response, role: str, username: str) -> None:
    token = _encode_session({"role": role, "username": username, "exp": int(time.time()) + SESSION_SECONDS})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
    )


initialize_user_db()


def _encode_session(payload: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(APP_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _decode_session(token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    expected = hmac.new(APP_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padding = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(f"{body}{padding}"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def current_role(request: FastAPIRequest) -> str | None:
    payload = _decode_session(request.cookies.get(SESSION_COOKIE))
    role = payload.get("role") if payload else None
    return role if role in {"admin", "viewer"} else None


def current_session(request: FastAPIRequest) -> dict[str, Any] | None:
    return _decode_session(request.cookies.get(SESSION_COOKIE))


def require_role(request: FastAPIRequest, role: str | None = None) -> str:
    current = current_role(request)
    if not current:
        raise HTTPException(status_code=401, detail="Please log in to continue.")
    if role and current != role:
        raise HTTPException(status_code=403, detail="Admin access is required for this action.")
    return current


def sheet_csv_url(sheet_name: str = "") -> str:
    params = {"tqx": "out:csv"}
    if sheet_name:
        params["sheet"] = sheet_name
    else:
        params["gid"] = SHEET_GID
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?{urlencode(params)}"


def append_sheet_row(entry: dict[str, Any], receipt: dict[str, str] | None = None) -> dict[str, Any]:
    if not WRITE_URL:
        raise RuntimeError("Google Sheets write connector is not configured. Set GOOGLE_SHEETS_WRITE_URL.")
    request_body: dict[str, Any] = {"token": WRITE_TOKEN, "row": entry}
    if receipt:
        request_body["receipt"] = receipt
    body = json.dumps(request_body).encode("utf-8")
    request = Request(
        WRITE_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "GaneshFestivalDashboard/1.0"},
        method="POST",
    )
    with urlopen(request, timeout=WRITE_TIMEOUT_SECONDS) as response:
        raw = response.read().decode("utf-8")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "The Google Sheets connector returned a non-JSON response. Redeploy the Apps Script as a web app and use its latest /exec URL."
        ) from exc
    if not result.get("ok", False):
        raise RuntimeError(result.get("error", "Google Sheets rejected the entry."))
    return result


def fetch_sheet_rows(sheet_name: str = "") -> tuple[list[dict[str, str]], bool]:
    request = Request(
        sheet_csv_url(sheet_name),
        headers={"User-Agent": "GaneshFestivalDashboard/1.0"},
    )
    with urlopen(request, timeout=15) as response:
        text = response.read().decode("utf-8-sig")

    if not text.strip():
        return [], False
    reader = csv.DictReader(io.StringIO(text))
    headers = [str(header or "").strip().lower() for header in (reader.fieldnames or [])]
    if sheet_name:
        valid_committee_headers = {"committee member name", "fixed amount"}
        if not valid_committee_headers.issubset(set(headers)):
            return [], False
    else:
        amount_headers = {"amount", "spent amount", "committee member amount", "donation amount"}
        if "date" not in headers or not amount_headers.intersection(headers):
            return [], False
    rows = []
    for source_row in reader:
        row = {
            str(key or "").strip().lower(): str(value or "").strip()
            for key, value in source_row.items()
        }
        if any(row.values()):
            rows.append(row)
    return rows, True


def first_value(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name.lower(), "").strip()
        if value:
            return value
    return ""


def parse_amount(value: str) -> float:
    cleaned = re.sub(r"[^0-9.\-]", "", value or "")
    try:
        return round(float(cleaned), 2) if cleaned else 0.0
    except ValueError:
        return 0.0


def payment_bucket(value: str) -> str:
    normalized = (value or "").strip().lower()
    if "cash" in normalized:
        return "Cash"
    if any(token in normalized for token in ("online", "upi", "bank", "card", "transfer")):
        return "Online"
    return "Other"


def normalize_phone(value: str) -> tuple[str, str]:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) > 10:
        digits = digits[-10:]
    if len(digits) == 10:
        return digits, f"******{digits[-4:]}"
    return (digits or ""), (value.strip() or "—")


def parse_date(value: str) -> tuple[str, str]:
    raw = (value or "").strip()
    formats = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y")
    for date_format in formats:
        try:
            parsed = datetime.strptime(raw, date_format)
            return parsed.strftime("%Y-%m-%d"), parsed.strftime("%d %b %Y")
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d"), parsed.strftime("%d %b %Y")
    except ValueError:
        return "unknown", raw or "—"


def classify(kind: str, category: str, name: str, description: str) -> str:
    value = f"{kind} {category}".lower().replace("_", " ")
    if "expense" in value or "spend" in value or "cost" in value:
        return "expense"
    if "volunteer" in value and ("donat" in value or not kind.strip()):
        return "volunteer donation"
    if "donat" in value or "collection" in value or "income" in value:
        return "donation"
    # Friendly fallback for rows where the Type cell was left blank. Explicit Type
    # values remain the preferred and unambiguous format.
    if not kind.strip():
        if "volunteer" in value:
            return "volunteer donation"
        if "donat" in value or "collection" in value or "offering" in value or category.strip().lower() in {"general", "donation"}:
            return "donation"
        if category.strip() or description.strip():
            return "expense"
        if name.strip():
            return "donation"
    return "other"


def build_dashboard(rows: list[dict[str, str]], has_headers: bool) -> dict[str, Any]:
    donations = 0.0
    expenses = 0.0
    volunteer_total = 0.0
    donation_count = 0
    expense_count = 0
    volunteer_count = 0
    daily_expenses: defaultdict[str, float] = defaultdict(float)
    volunteer_donations: defaultdict[str, float] = defaultdict(float)
    payment_breakdown: dict[str, defaultdict[str, float]] = {
        "received": defaultdict(float),
        "spent": defaultdict(float),
    }
    entries: list[dict[str, Any]] = []
    donor_entries: list[dict[str, Any]] = []
    expense_entries: list[dict[str, Any]] = []

    def record_entry(entry_type: str, amount: float, name: str, phone: str, date_key: str, date_label: str, category: str, description: str, payment_method: str) -> None:
        nonlocal donations, expenses, volunteer_total, donation_count, expense_count, volunteer_count
        if amount <= 0 or entry_type not in {"donation", "volunteer donation", "expense"}:
            return
        if entry_type == "expense":
            expenses += amount
            expense_count += 1
            payment_breakdown["spent"][payment_bucket(payment_method)] += amount
            if date_key != "unknown":
                daily_expenses[date_key] += amount
        elif entry_type == "volunteer donation":
            donations += amount
            volunteer_total += amount
            donation_count += 1
            volunteer_count += 1
            payment_breakdown["received"][payment_bucket(payment_method)] += amount
            volunteer_donations[name] += amount
        else:
            donations += amount
            donation_count += 1
            payment_breakdown["received"][payment_bucket(payment_method)] += amount

        entry = {
            "date": date_label,
            "date_key": date_key,
            "type": entry_type.title(),
            "name": name,
            "phone": phone,
            "category": category,
            "description": description,
            "payment_method": payment_method,
            "amount": amount,
        }
        entries.append(entry)
        if entry_type == "expense":
            expense_entries.append(entry)
        if entry_type in {"donation", "volunteer donation"}:
            donor_entries.append(entry)

    for row in rows:
        date_key, date_label = parse_date(first_value(row, "date", "entry date", "day", "date spent"))
        kind = first_value(row, "type", "entry type", "transaction type")
        committee_member_name = first_value(row, "committee member name")
        amount_asked_by = first_value(row, "amount asked by", "asked by")
        name = first_value(row, "name", "donater", "donor", "donor name", "volunteer name", "person") or committee_member_name or "—"
        added_by_value = first_value(row, "added by", "added by admin") or committee_member_name or name or "—"
        phone_key, phone_display = normalize_phone(first_value(row, "phone number", "phone", "mobile", "contact number"))
        phone_identity = phone_key or f"name:{name.strip().lower()}"
        category_value = first_value(row, "category", "expense category")
        description_value = first_value(row, "description", "details", "purpose")
        category = category_value or "—"
        description = description_value or "—"
        payment_method = first_value(row, "payment method", "payment", "mode") or "—"
        receipt_url = first_value(row, "receipt url", "receipt", "screenshot url", "image url")
        spent_amount = parse_amount(first_value(row, "spent amount"))
        amount = parse_amount(first_value(row, "amount", "value", "total")) or spent_amount
        donation_amount = parse_amount(first_value(row, "committee member amount", "donation amount", "donater amount", "donor amount"))
        entry_type = classify(kind, category_value, name, description_value)
        if not kind.strip() and spent_amount > 0 and donation_amount <= 0:
            entry_type = "expense"

        # The sheet can hold an expense in Amount and a donor contribution in
        # Donation Amount on the same row, so record both independently.
        if donation_amount > 0:
            donation_type = "volunteer donation" if "volunteer" in f"{kind} {category_value}".lower() else "donation"
            donation_entry = {
                "phone_identity": phone_identity,
                "phone": phone_display,
            }
            entries_before = len(entries)
            record_entry(donation_type, donation_amount, name, phone_display, date_key, date_label, category, description, payment_method)
            if len(entries) > entries_before:
                entries[-1]["receipt_url"] = receipt_url
                entries[-1]["committee_member_name"] = committee_member_name
                entries[-1]["amount_asked_by"] = amount_asked_by
                entries[-1]["added_by"] = added_by_value
            donor_entries[-1].update(donation_entry)

        # If Donation Amount is present, it is the authoritative donation value;
        # avoid counting the main Amount twice when that row is also classified as donation.
        if not (donation_amount > 0 and entry_type in {"donation", "volunteer donation"}):
            entries_before = len(entries)
            record_entry(entry_type, amount, name, phone_display, date_key, date_label, category, description, payment_method)
            if len(entries) > entries_before:
                entries[-1]["receipt_url"] = receipt_url
                entries[-1]["committee_member_name"] = committee_member_name
                entries[-1]["amount_asked_by"] = amount_asked_by
                entries[-1]["added_by"] = added_by_value
            if entry_type in {"donation", "volunteer donation"}:
                donor_entries[-1].update({"phone_identity": phone_identity, "phone": phone_display})

    entries.sort(key=lambda item: item["date_key"], reverse=True)
    unique_donors: dict[str, dict[str, Any]] = {}
    for entry in donor_entries:
        identity = entry.get("phone_identity") or f"name:{entry['name'].strip().lower()}"
        donor = unique_donors.setdefault(identity, {
            "name": entry["name"],
            "phone": entry.get("phone", "—"),
            "amount": 0.0,
            "entry_count": 0,
            "date_key": entry["date_key"],
            "date": entry.get("date", "—"),
            "added_by": entry.get("added_by", "—"),
            "type": entry["type"],
        })
        donor["amount"] += entry["amount"]
        donor["entry_count"] += 1
        if donor["name"] == "—" and entry["name"] != "—":
            donor["name"] = entry["name"]
        if entry["date_key"] >= donor["date_key"]:
            donor["date_key"] = entry["date_key"]
            donor["date"] = entry.get("date", "—")
            donor["added_by"] = entry.get("added_by", "—")
    donor_entries = sorted(unique_donors.values(), key=lambda item: item["amount"], reverse=True)
    daily = [
        {"date": key, "label": datetime.strptime(key, "%Y-%m-%d").strftime("%d %b"), "amount": round(value, 2)}
        for key, value in sorted(daily_expenses.items())
    ]
    volunteers = [
        {"name": name, "amount": round(amount, 2)}
        for name, amount in sorted(volunteer_donations.items(), key=lambda item: item[1], reverse=True)
    ]

    return {
        "setup_needed": not has_headers,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "donations": round(donations, 2),
            "expenses": round(expenses, 2),
            "balance": round(donations - expenses, 2),
            "volunteer_donations": round(volunteer_total, 2),
            "donation_count": donation_count,
            "expense_count": expense_count,
            "volunteer_count": volunteer_count,
        },
        "daily_expenses": daily,
        "payment_breakdown": {
            group: [{"method": method, "amount": round(amount, 2)} for method, amount in values.items() if amount > 0]
            for group, values in payment_breakdown.items()
        },
        "volunteer_donations": volunteers,
        "donor_entries": donor_entries,
        "expense_entries": sorted(expense_entries, key=lambda item: item["date_key"], reverse=True),
        "recent_entries": entries[:30],
    }


def build_committee_summary(committee_rows: list[dict[str, str]], main_rows: list[dict[str, str]]) -> dict[str, Any]:
    paid_by_member: defaultdict[str, float] = defaultdict(float)
    for row in main_rows:
        name = first_value(row, "committee member name", "name")
        amount = parse_amount(first_value(row, "committee member amount"))
        if name and amount > 0:
            paid_by_member[name.strip().lower()] += amount

    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in committee_rows:
        name = first_value(row, "committee member name", "name")
        if not name:
            continue
        identity = name.strip().lower()
        if identity in seen:
            continue
        seen.add(identity)
        fixed_amount = parse_amount(first_value(row, "fixed amount", "target amount"))
        paid_amount = round(paid_by_member.get(identity, 0.0), 2)
        latest_payment = next(
            (
                payment for payment in reversed(main_rows)
                if first_value(payment, "committee member name", "name").strip().lower() == identity
                and parse_amount(first_value(payment, "committee member amount")) > 0
            ),
            {},
        )
        paid_date = first_value(latest_payment, "date", "paid date", "date spent")
        _, paid_date_label = parse_date(paid_date)
        members.append({
            "name": name.strip(),
            "fixed_amount": round(fixed_amount, 2),
            "paid_amount": paid_amount,
            "remaining_amount": round(fixed_amount - paid_amount, 2),
            "paid_date": paid_date_label if paid_date else "—",
            "added_by": first_value(latest_payment, "added by", "added by admin") or "—",
        })

    total_fixed = round(sum(member["fixed_amount"] for member in members), 2)
    total_paid = round(sum(member["paid_amount"] for member in members), 2)
    return {
        "target_count": COMMITTEE_MEMBER_COUNT,
        "listed_count": len(members),
        "total_fixed": total_fixed,
        "total_paid": total_paid,
        "total_remaining": round(total_fixed - total_paid, 2),
        "members": members,
    }


def get_dashboard() -> dict[str, Any]:
    now = datetime.now(timezone.utc).timestamp()
    with _cache_lock:
        if _cache["payload"] is not None and now - _cache["at"] < REFRESH_SECONDS:
            return _cache["payload"]
        try:
            rows, has_headers = fetch_sheet_rows()
            committee_rows, _ = fetch_sheet_rows(COMMITTEE_SHEET_NAME)
            payload = build_dashboard(rows, has_headers)
            payload["committee"] = build_committee_summary(committee_rows, rows)
        except Exception as exc:  # Keep the dashboard available during temporary Sheet errors.
            payload = {
                "setup_needed": False,
                "error": "Unable to refresh Google Sheet data right now.",
                "details": str(exc),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "totals": {"donations": 0, "expenses": 0, "balance": 0, "volunteer_donations": 0, "donation_count": 0, "expense_count": 0, "volunteer_count": 0},
                "daily_expenses": [],
                "payment_breakdown": {"received": [], "spent": []},
                "volunteer_donations": [],
                "donor_entries": [],
                "expense_entries": [],
                "recent_entries": [],
                "committee": {"target_count": COMMITTEE_MEMBER_COUNT, "listed_count": 0, "total_fixed": 0, "total_paid": 0, "total_remaining": 0, "members": []},
            }
        _cache.update({"at": now, "payload": payload})
        return payload


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.post("/api/login")
async def login(payload: LoginPayload, response: Response) -> dict[str, str]:
    username = payload.username.strip()
    role = None
    if hmac.compare_digest(username, ADMIN_USERNAME) and admin_password_matches(payload.password):
        role = "admin"
    elif hmac.compare_digest(username, VIEWER_USERNAME) and hmac.compare_digest(payload.password, VIEWER_PASSWORD):
        role = "viewer"
    elif registered_user_matches(username, payload.password):
        role = "viewer"
    if not role:
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    set_session(response, role, username)
    return {"role": role, "username": username}


@app.post("/api/register")
async def register(payload: RegisterPayload, response: Response) -> dict[str, str]:
    username = payload.username.strip()
    username_key = username.lower()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,31}", username):
        raise HTTPException(status_code=400, detail="Username must be 3–32 characters using letters, numbers, dot, dash, or underscore.")
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must contain at least 8 characters.")
    if payload.password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    reserved = {ADMIN_USERNAME.lower(), VIEWER_USERNAME.lower()}
    if username_key in reserved:
        raise HTTPException(status_code=409, detail="That username is reserved. Choose another username.")
    try:
        with sqlite3.connect(USER_DB_PATH) as connection:
            connection.execute(
                "INSERT INTO users(username, password_hash, created_at) VALUES(?, ?, ?)",
                (username_key, password_hash(payload.password), datetime.now(timezone.utc).isoformat()),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="That username is already registered.") from exc
    set_session(response, "viewer", username_key)
    return {"role": "viewer", "username": username_key}


@app.post("/api/admin/change-password")
async def change_admin_password(payload: ChangePasswordPayload, request: FastAPIRequest) -> dict[str, str]:
    require_role(request, "admin")
    if not admin_password_matches(payload.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must contain at least 8 characters.")
    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="New passwords do not match.")
    set_admin_password(payload.new_password)
    return {"message": "Admin password updated successfully."}


@app.get("/api/admin/users")
async def registered_users(request: FastAPIRequest) -> dict[str, list[dict[str, str]]]:
    require_role(request, "admin")
    with sqlite3.connect(USER_DB_PATH) as connection:
        rows = connection.execute(
            "SELECT username, created_at FROM users ORDER BY created_at DESC"
        ).fetchall()
    return {"users": [{"username": row[0], "created_at": row[1]} for row in rows]}


@app.post("/api/admin/users/{username}/password")
async def change_viewer_password(
    username: str,
    payload: ChangeViewerPasswordPayload,
    request: FastAPIRequest,
) -> dict[str, str]:
    require_role(request, "admin")
    username_key = username.strip().lower()
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must contain at least 8 characters.")
    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="New passwords do not match.")
    with sqlite3.connect(USER_DB_PATH) as connection:
        cursor = connection.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (password_hash(payload.new_password), username_key),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Viewer account not found.")
    return {"message": f"Password updated for {username_key}."}


@app.get("/api/session")
async def session(request: FastAPIRequest) -> dict[str, Any]:
    role = current_role(request)
    return {"authenticated": bool(role), "role": role}


@app.post("/api/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/dashboard")
async def dashboard(request: FastAPIRequest) -> dict[str, Any]:
    require_role(request)
    return get_dashboard()


@app.get("/api/committee-phones")
async def committee_phones(request: FastAPIRequest) -> dict[str, list[dict[str, str]]]:
    require_role(request, "admin")
    main_rows, _ = fetch_sheet_rows()
    committee_rows, _ = fetch_sheet_rows(COMMITTEE_SHEET_NAME)
    phones: dict[str, tuple[str, str]] = {}
    for row in committee_rows + main_rows:
        name = first_value(row, "committee member name", "name")
        phone = first_value(row, "phone number", "phone", "mobile", "contact number")
        if name and phone and name.strip().lower() not in phones:
            phones[name.strip().lower()] = (name.strip(), phone)
    return {"members": [{"name": display_name, "phone": phone} for display_name, phone in phones.values()]}


@app.post("/api/entries")
async def create_entry(payload: EntryPayload, request: FastAPIRequest) -> dict[str, Any]:
    require_role(request, "admin")
    entry_type = payload.type.strip().lower()
    allowed_types = {"donation", "expense", "volunteer donation"}
    if entry_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Choose Donation, Expense, or Volunteer Donation.")

    effective_name = payload.name.strip() or payload.amount_asked_by.strip()
    selected_added_by = payload.added_by.strip() or payload.committee_member_name.strip()
    required_fields = {"payment method": payload.payment_method.strip()}
    if entry_type == "expense":
        required_fields["category"] = payload.category.strip()
        required_fields["added by"] = payload.committee_member_name.strip()
        required_fields["amount asked by"] = payload.amount_asked_by.strip()
    else:
        required_fields["name"] = effective_name
        if payload.type.strip().lower() == "donation":
            required_fields["added by"] = payload.added_by.strip()
    missing = [label for label, value in required_fields.items() if not value]
    if missing:
        raise HTTPException(status_code=400, detail=f"Required fields: {', '.join(missing)}.")
    phone_digits = re.sub(r"\D", "", payload.phone_number)
    if len(phone_digits) > 10:
        phone_digits = phone_digits[-10:]
    if len(phone_digits) != 10:
        raise HTTPException(status_code=400, detail="Phone number must contain 10 digits.")

    amount = parse_amount(str(payload.amount or ""))
    donation_amount = parse_amount(str(payload.committee_member_amount or payload.donation_amount or ""))
    if entry_type == "expense" and amount <= 0:
        raise HTTPException(status_code=400, detail="Enter an expense amount greater than zero.")
    if entry_type != "expense":
        donation_amount = donation_amount or amount
        if donation_amount <= 0:
            raise HTTPException(status_code=400, detail="Enter a donation amount greater than zero.")
        amount = 0.0

    added_by_name = selected_added_by or effective_name
    row = {
        "date": payload.date.strip() or datetime.now().strftime("%Y-%m-%d"),
        "type": entry_type.title(),
        "name": effective_name,
        "committee member name": payload.committee_member_name.strip(),
        "amount asked by": payload.amount_asked_by.strip(),
        "added by": added_by_name,
        "paid date": payload.date.strip() or datetime.now().strftime("%Y-%m-%d"),
        "category": payload.category.strip(),
        "amount": round(amount, 2) if amount else "",
        "payment method": payload.payment_method.strip(),
        "description": payload.description.strip(),
        "phone number": payload.phone_number.strip(),
        "donation amount": round(donation_amount, 2) if donation_amount else "",
        "committee member amount": round(donation_amount, 2) if donation_amount else "",
        "spent amount": round(amount or donation_amount, 2) if (amount or donation_amount) else "",
        "receipt url": "",
    }
    receipt = None
    if payload.receipt_data:
        if entry_type != "expense":
            raise HTTPException(status_code=400, detail="Receipt images can only be attached to expenses.")
        if len(payload.receipt_data) > 12_000_000:
            raise HTTPException(status_code=400, detail="Receipt image is too large. Keep it under 8 MB.")
        receipt = {
            "name": payload.receipt_name.strip() or "expense-receipt",
            "mime_type": payload.receipt_mime_type.strip() or "image/jpeg",
            "base64": payload.receipt_data,
        }
    try:
        result = append_sheet_row(row, receipt)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Google Sheets took too long to respond. Check the sheet before submitting the same entry again.",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    with _cache_lock:
        _cache.update({"at": 0.0, "payload": None})
    return {"ok": True, "message": "Entry saved to Google Sheets.", "row": row, "connector": result}


@app.get("/health")
async def health() -> dict[str, Any]:
    payload = get_dashboard()
    return {"status": "ok", "sheet_configured": bool(SHEET_ID), "setup_needed": payload.get("setup_needed", False), "error": payload.get("error")}
