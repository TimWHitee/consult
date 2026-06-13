import base64
import io
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import face
from .config import config
from .database import connect, dump_json, init_db, json_list, row_to_dict, rows_to_dicts
from .qr import (
    create_qr_payload,
    default_expiration,
    iso_now,
    new_nonce,
    parse_dt,
    save_qr_png,
    verify_qr_payload,
)
from .schemas import (
    AccessRuleIn,
    AccessRulePatch,
    CompanySetupIn,
    EmployeeCredentialIn,
    EmployeeGuestPassIn,
    EmployeeIn,
    EmployeeLoginIn,
    EmployeePatch,
    EmployeePassStatusIn,
    EmployeePasswordChangeIn,
    GuestPassIn,
    QrPassIn,
    QrPassRevokeIn,
    RoomAllowlistIn,
    RoomIn,
    RoomLimitOverrideIn,
    RoomPatch,
    ScannerIn,
    ScannerPatch,
    ScannerQrImageIn,
    ScannerVerifyIn,
    SecuritySettingsIn,
)
from .security import generate_token, hash_password, hash_token, verify_password, verify_token


app = FastAPI(
    title="SKUD API",
    version="1.0.0",
    description="Universal office access control API with QR and face recognition scanners.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

admin_dir = Path(__file__).resolve().parent.parent / "admin"
if admin_dir.exists():
    app.mount("/admin", StaticFiles(directory=admin_dir, html=True), name="admin")

employee_dir = Path(__file__).resolve().parent.parent / "employee"
if employee_dir.exists():
    app.mount("/employee", StaticFiles(directory=employee_dir, html=True), name="employee")

scanner_app_dir = Path(__file__).resolve().parent.parent / "webapp"
if scanner_app_dir.exists():
    app.mount("/scanner", StaticFiles(directory=scanner_app_dir, html=True), name="scanner")

storage_dir = Path(config.STORAGE_DIR)
storage_dir.mkdir(parents=True, exist_ok=True)
app.mount("/storage", StaticFiles(directory=storage_dir), name="storage")


@app.exception_handler(sqlite3.IntegrityError)
async def sqlite_integrity_error_handler(request: Request, exc: sqlite3.IntegrityError) -> JSONResponse:
    message = str(exc)
    if "employees.company_id, employees.external_id" in message:
        detail = "Employee with this external_id already exists"
    elif "employee_credentials.company_id, employee_credentials.login" in message:
        detail = "Employee login already exists"
    elif "employee_credentials.company_id, employee_credentials.employee_id" in message:
        detail = "Employee already has credentials"
    elif "rooms.company_id, rooms.code" in message:
        detail = "Room code already exists"
    elif "scanners.company_id, scanners.name" in message:
        detail = "Scanner name already exists"
    elif "companies.slug" in message:
        detail = "Company slug already exists"
    else:
        detail = "Data conflict. Check unique fields and try again"
    return JSONResponse(status_code=409, content={"detail": detail})


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/admin/")


@app.on_event("startup")
def startup() -> None:
    init_db()
    revoke_expired_qr_passes()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sqlite_dt(value: str | None) -> str | None:
    if not value:
        return None
    return parse_dt(value).strftime("%Y-%m-%d %H:%M:%S")


def revoke_expired_qr_passes(company_id: int | None = None) -> int:
    query = "UPDATE qr_passes SET revoked_at = CURRENT_TIMESTAMP, revoked_reason = 'expired' WHERE revoked_at IS NULL AND expires_at < CURRENT_TIMESTAMP"
    values: list[Any] = []
    if company_id is not None:
        query += " AND company_id = ?"
        values.append(company_id)
    with connect() as db:
        cursor = db.execute(query, values)
        return int(cursor.rowcount or 0)


def current_admin(
    x_api_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        with connect() as db:
            rows = db.execute(
                """
                SELECT employee_sessions.*, employees.role, employees.full_name,
                       companies.name AS company_name, companies.slug AS company_slug
                FROM employee_sessions
                JOIN employees ON employees.id = employee_sessions.employee_id
                JOIN companies ON companies.id = employee_sessions.company_id
                WHERE employee_sessions.revoked_at IS NULL
                  AND employees.status = 'active'
                  AND (employee_sessions.expires_at IS NULL OR employee_sessions.expires_at > CURRENT_TIMESTAMP)
                """
            ).fetchall()

        for row in rows:
            item = row_to_dict(row)
            if item and verify_token(token, item["token_hash"]):
                if item["role"] not in {"hr", "security", "pass_office"}:
                    raise HTTPException(status_code=403, detail="Employee role is not allowed to administer company")
                return {
                    "company_id": item["company_id"],
                    "company_name": item["company_name"],
                    "company_slug": item["company_slug"],
                    "role": item["role"],
                    "employee_id": item["employee_id"],
                }

    if x_api_key:
        with connect() as db:
            rows = db.execute(
                """
                SELECT api_keys.*, companies.name AS company_name, companies.slug AS company_slug
                FROM api_keys
                JOIN companies ON companies.id = api_keys.company_id
                WHERE api_keys.revoked_at IS NULL
                """
            ).fetchall()

        for row in rows:
            item = row_to_dict(row)
            if item and verify_token(x_api_key, item["key_hash"]):
                return item

    raise HTTPException(status_code=401, detail="Admin login or X-API-Key is required")


def current_scanner(x_scanner_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    if not x_scanner_token:
        raise HTTPException(status_code=401, detail="X-Scanner-Token header is required")

    with connect() as db:
        rows = db.execute(
            """
            SELECT scanners.*, rooms.name AS room_name, rooms.code AS room_code
            FROM scanners
            JOIN rooms ON rooms.id = scanners.room_id
            WHERE scanners.status = 'active'
            """
        ).fetchall()

    for row in rows:
        item = row_to_dict(row)
        if item and verify_token(x_scanner_token, item["token_hash"]):
            with connect() as db:
                db.execute("UPDATE scanners SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?", (item["id"],))
            return item

    raise HTTPException(status_code=401, detail="Invalid scanner token")


def current_employee(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer employee token is required")

    token = authorization.split(" ", 1)[1].strip()
    with connect() as db:
        rows = db.execute(
            """
            SELECT employee_sessions.*, employees.full_name, employees.email, employees.phone,
                   employees.position, employees.external_id, employees.status,
                   employees.role, employees.access_level, employees.pass_status,
                   companies.name AS company_name, companies.slug AS company_slug
            FROM employee_sessions
            JOIN employees ON employees.id = employee_sessions.employee_id
            JOIN companies ON companies.id = employee_sessions.company_id
            WHERE employee_sessions.revoked_at IS NULL
              AND employees.status = 'active'
              AND (employee_sessions.expires_at IS NULL OR employee_sessions.expires_at > CURRENT_TIMESTAMP)
            """
        ).fetchall()

    for row in rows:
        item = row_to_dict(row)
        if item and verify_token(token, item["token_hash"]):
            return item

    raise HTTPException(status_code=401, detail="Invalid employee token")


def require_company_item(db, table: str, company_id: int, item_id: int) -> dict[str, Any]:
    row = db.execute(f"SELECT * FROM {table} WHERE id = ? AND company_id = ?", (item_id, company_id)).fetchone()
    item = row_to_dict(row)
    if not item:
        raise HTTPException(status_code=404, detail=f"{table[:-1]} not found")
    return item


def normalize_login(login: str) -> str:
    return login.strip().lower()


def upsert_employee_credential(db, company_id: int, employee_id: int, login: str, password: str) -> None:
    db.execute(
        """
        INSERT INTO employee_credentials (company_id, employee_id, login, password_hash)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(company_id, employee_id) DO UPDATE SET
            login = excluded.login,
            password_hash = excluded.password_hash,
            updated_at = CURRENT_TIMESTAMP
        """,
        (company_id, employee_id, normalize_login(login), hash_password(password)),
    )


def decode_qr_image_base64(image_base64: str) -> str:
    if "," in image_base64:
        image_base64 = image_base64.split(",", 1)[1]

    try:
        content = base64.b64decode(image_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid QR image payload") from exc

    try:
        from PIL import Image
        image = Image.open(io.BytesIO(content)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid QR image") from exc

    try:
        import cv2
        import numpy as np

        array = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        value, _, _ = cv2.QRCodeDetector().detectAndDecode(array)
        if value:
            return value
    except Exception:
        pass

    try:
        from pyzbar.pyzbar import decode

        decoded = decode(image)
        if decoded:
            return decoded[0].data.decode("utf-8")
    except Exception:
        pass

    raise HTTPException(status_code=422, detail="QR code was not found in the image")


def employee_photo_url(company_id: int, employee_id: int) -> str | None:
    with connect() as db:
        row = row_to_dict(
            db.execute(
                """
                SELECT file_path FROM face_photos
                WHERE company_id = ? AND employee_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (company_id, employee_id),
            ).fetchone()
        )
    if not row:
        return None
    try:
        return qr_public_url(row["file_path"])
    except ValueError:
        return None


def enrich_rule(rule: dict[str, Any]) -> dict[str, Any]:
    import json

    rule["allowed_methods"] = json_list(rule.get("allowed_methods"))
    rule["schedule"] = json.loads(rule["schedule_json"]) if rule.get("schedule_json") else None
    rule["is_active"] = bool(rule["is_active"])
    return rule


def enrich_room(room: dict[str, Any]) -> dict[str, Any]:
    room["allowed_methods"] = json_list(room.get("allowed_methods")) or ["qr", "card", "face"]
    room["biometric_only"] = bool(room.get("biometric_only"))
    return room


def enrich_scanner(scanner: dict[str, Any]) -> dict[str, Any]:
    scanner["allowed_methods"] = json_list(scanner.get("allowed_methods"))
    return scanner


def log_event(
    *,
    company_id: int,
    employee_id: int | None,
    room_id: int,
    scanner_id: int,
    method: str,
    direction: str,
    decision: str,
    reason: str,
    confidence: float | None = None,
    qr_pass_id: int | None = None,
    raw_subject: str | None = None,
    subject_type: str = "employee",
    guest_id: int | None = None,
) -> int:
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO access_events (
                company_id, employee_id, room_id, scanner_id, method, direction,
                decision, reason, confidence, qr_pass_id, raw_subject, subject_type, guest_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                employee_id,
                room_id,
                scanner_id,
                method,
                direction,
                decision,
                reason,
                confidence,
                qr_pass_id,
                raw_subject,
                subject_type,
                guest_id,
            ),
        )
        event_id = int(cursor.lastrowid)
        if decision == "granted" and employee_id:
            if direction == "entry":
                db.execute(
                    """
                    INSERT OR REPLACE INTO room_occupancy (company_id, room_id, employee_id, entered_at, last_event_id)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
                    """,
                    (company_id, room_id, employee_id, event_id),
                )
                db.execute(
                    """
                    INSERT INTO employee_presence (company_id, employee_id, status, last_entry_at, last_event_id, updated_at)
                    VALUES (?, ?, 'in_office', CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(company_id, employee_id) DO UPDATE SET
                        status = 'in_office',
                        last_entry_at = CURRENT_TIMESTAMP,
                        last_event_id = excluded.last_event_id,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (company_id, employee_id, event_id),
                )
            elif direction == "exit":
                db.execute(
                    "DELETE FROM room_occupancy WHERE company_id = ? AND room_id = ? AND employee_id = ?",
                    (company_id, room_id, employee_id),
                )
                db.execute(
                    """
                    INSERT INTO employee_presence (company_id, employee_id, status, last_exit_at, last_event_id, updated_at)
                    VALUES (?, ?, 'out_office', CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(company_id, employee_id) DO UPDATE SET
                        status = 'out_office',
                        last_exit_at = CURRENT_TIMESTAMP,
                        last_event_id = excluded.last_event_id,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (company_id, employee_id, event_id),
                )
        return event_id


def notify_employee(db, company_id: int, employee_id: int | None, type_: str, title: str, body: str, payload: dict[str, Any] | None = None) -> None:
    db.execute(
        """
        INSERT INTO notifications (company_id, employee_id, type, title, body, payload_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (company_id, employee_id, type_, title, body, dump_json(payload or {})),
    )


def notify_security_staff(db, company_id: int, title: str, body: str, payload: dict[str, Any] | None = None) -> None:
    rows = rows_to_dicts(db.execute("SELECT id FROM employees WHERE company_id = ? AND role = 'security' AND status = 'active'", (company_id,)).fetchall())
    for row in rows:
        notify_employee(db, company_id, row["id"], "security_alert", title, body, payload)


def get_company_setting(db, company_id: int, key: str, default: str) -> str:
    row = row_to_dict(db.execute("SELECT value FROM company_settings WHERE company_id = ? AND key = ?", (company_id, key)).fetchone())
    return str(row["value"]) if row else default


def set_company_setting(db, company_id: int, key: str, value: str) -> None:
    db.execute(
        """
        INSERT INTO company_settings (company_id, key, value)
        VALUES (?, ?, ?)
        ON CONFLICT(company_id, key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """,
        (company_id, key, value),
    )


def anti_passback_minutes(db, company_id: int) -> int:
    return int(get_company_setting(db, company_id, "anti_passback_minutes", "15"))


def log_access_change(
    db,
    company_id: int,
    action: str,
    employee_id: int | None = None,
    room_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    db.execute(
        """
        INSERT INTO access_change_logs (company_id, employee_id, room_id, action, details_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (company_id, employee_id, room_id, action, dump_json(details or {})),
    )
    if employee_id:
        title = "Изменение доступа"
        body = "Ваши права доступа были обновлены." if action != "access_revoked" else "Ваш доступ был ограничен."
        notify_employee(db, company_id, employee_id, "access_change", title, body, {"action": action, "room_id": room_id})


def schedule_allows(schedule: dict[str, Any] | None, now: datetime | None = None) -> bool:
    if not schedule:
        return True
    now = now or utc_now()
    weekdays = schedule.get("weekdays")
    if weekdays and now.weekday() not in [int(day) for day in weekdays]:
        return False
    start = schedule.get("start_time")
    end = schedule.get("end_time")
    if start and end:
        current = now.strftime("%H:%M")
        if not (str(start) <= current <= str(end)):
            return False
    return True


def current_room_limit(db, company_id: int, room_id: int, default_limit: int | None) -> int | None:
    row = row_to_dict(
        db.execute(
            """
            SELECT limit_value FROM room_limit_overrides
            WHERE company_id = ? AND room_id = ?
              AND valid_from <= ? AND valid_until >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (company_id, room_id, iso_now(), iso_now()),
        ).fetchone()
    )
    if row:
        return int(row["limit_value"])
    return int(default_limit) if default_limit is not None else None


def room_current_count(db, company_id: int, room_id: int) -> int:
    return int(
        db.execute(
            "SELECT COUNT(*) AS count FROM room_occupancy WHERE company_id = ? AND room_id = ?",
            (company_id, room_id),
        ).fetchone()["count"]
    )


def check_anti_passback(company_id: int, employee_id: int, direction: str, method: str) -> tuple[bool, str]:
    if direction != "entry" or method not in {"qr", "card"}:
        return True, "anti_passback_not_applicable"
    with connect() as db:
        interval = anti_passback_minutes(db, company_id)
        if interval <= 0:
            return True, "anti_passback_disabled"
        presence = row_to_dict(
            db.execute(
                "SELECT * FROM employee_presence WHERE company_id = ? AND employee_id = ?",
                (company_id, employee_id),
            ).fetchone()
        )
    if not presence:
        return True, "anti_passback_clear"
    if presence["status"] == "in_office":
        return False, "anti_passback_employee_in_office"
    if presence.get("last_exit_at"):
        last_exit = parse_dt(presence["last_exit_at"])
        if utc_now() - last_exit < timedelta(minutes=interval):
            return False, "anti_passback_interval_not_elapsed"
    return True, "anti_passback_clear"


def notify_anti_passback(company_id: int, employee_id: int, reason: str) -> None:
    with connect() as db:
        employee = row_to_dict(db.execute("SELECT full_name FROM employees WHERE id = ? AND company_id = ?", (employee_id, company_id)).fetchone())
        full_name = employee["full_name"] if employee else f"#{employee_id}"
        body = f"Повторная попытка входа: id {employee_id}, {full_name}, время {iso_now()}."
        notify_security_staff(db, company_id, "Блокировка повторного входа", body, {"employee_id": employee_id, "reason": reason})


def employee_permissions(db, company_id: int, employee_id: int | None) -> list[dict[str, Any]]:
    if not employee_id:
        return []
    return rows_to_dicts(
        db.execute(
            """
            SELECT access_rules.*, rooms.name AS room_name, rooms.access_level AS room_access_level
            FROM access_rules
            JOIN rooms ON rooms.id = access_rules.room_id
            WHERE access_rules.company_id = ? AND access_rules.employee_id = ?
            ORDER BY rooms.name
            """,
            (company_id, employee_id),
        ).fetchall()
    )


def employee_can_invite_guest(db, company_id: int, employee_id: int, room_id: int) -> bool:
    employee = row_to_dict(db.execute("SELECT * FROM employees WHERE id = ? AND company_id = ?", (employee_id, company_id)).fetchone())
    room = row_to_dict(db.execute("SELECT * FROM rooms WHERE id = ? AND company_id = ?", (room_id, company_id)).fetchone())
    if not employee or not room or employee["status"] != "active" or employee.get("pass_status") == "blocked":
        return False
    if int(room.get("access_level") or 1) == 1:
        return True
    rule = row_to_dict(
        db.execute(
            """
            SELECT * FROM access_rules
            WHERE company_id = ? AND employee_id = ? AND room_id = ? AND is_active = 1
            """,
            (company_id, employee_id, room_id),
        ).fetchone()
    )
    if not rule:
        return False
    if rule["valid_from"] and rule["valid_from"] > iso_now():
        return False
    if rule["valid_until"] and rule["valid_until"] < iso_now():
        return False
    import json

    schedule = json.loads(rule["schedule_json"]) if rule.get("schedule_json") else None
    return schedule_allows(schedule)


def create_security_alert(company_id: int, employee_id: int | None, room_id: int, event_id: int, reason: str) -> None:
    with connect() as db:
        permissions = employee_permissions(db, company_id, employee_id)
        db.execute(
            """
            INSERT INTO security_alerts (company_id, employee_id, room_id, access_event_id, reason, permissions_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (company_id, employee_id, room_id, event_id, reason, dump_json(permissions)),
        )


def check_access(company_id: int, employee_id: int, room_id: int, method: str, direction: str = "entry") -> tuple[bool, str]:
    now = iso_now()
    with connect() as db:
        employee = row_to_dict(
            db.execute(
                "SELECT * FROM employees WHERE id = ? AND company_id = ?",
                (employee_id, company_id),
            ).fetchone()
        )
        if not employee:
            return False, "employee_not_found"
        if employee["status"] != "active":
            return False, "employee_not_active"
        if employee.get("pass_status") == "blocked":
            return False, "employee_pass_blocked"

        room = row_to_dict(db.execute("SELECT * FROM rooms WHERE id = ? AND company_id = ?", (room_id, company_id)).fetchone())
        if not room or room["status"] != "active":
            return False, "room_not_active"
        room_methods = json_list(room.get("allowed_methods")) or ["qr", "card", "face"]
        if method not in room_methods:
            return False, "method_not_allowed_for_room"
        if method != "face" and int(room.get("biometric_only") or 0):
            return False, "room_requires_biometrics"
        if int(room.get("access_level") or 1) >= 3:
            allowed = row_to_dict(
                db.execute(
                    """
                    SELECT 1 FROM room_level3_allowlist
                    WHERE company_id = ? AND room_id = ? AND employee_id = ?
                    """,
                    (company_id, room_id, employee_id),
                ).fetchone()
            )
            if not allowed:
                return False, "level3_allowlist_required"
            if method != "face":
                return False, "level3_requires_face"

        if method == "qr" and employee.get("pass_status") != "active":
            return False, "employee_pass_blocked"

        limit = current_room_limit(db, company_id, room_id, room.get("capacity"))
        if direction == "entry" and limit is not None and room_current_count(db, company_id, room_id) >= limit:
            return False, "room_capacity_limit_reached"

        if int(room.get("access_level") or 1) == 1:
            return True, "access_granted_level1"

        rule = row_to_dict(
            db.execute(
                """
                SELECT * FROM access_rules
                WHERE company_id = ? AND employee_id = ? AND room_id = ? AND is_active = 1
                """,
                (company_id, employee_id, room_id),
            ).fetchone()
        )
        if not rule:
            return False, "no_access_rule"
        if rule["valid_from"] and rule["valid_from"] > now:
            return False, "access_rule_not_started"
        if rule["valid_until"] and rule["valid_until"] < now:
            return False, "access_rule_expired"
        import json

        schedule = json.loads(rule["schedule_json"]) if rule.get("schedule_json") else None
        if not schedule_allows(schedule):
            return False, "access_schedule_denied"
    return True, "access_granted"


def qr_public_url(path: Path | str) -> str:
    storage_root = Path(config.STORAGE_DIR).resolve()
    qr_path = Path(path).resolve()
    relative = qr_path.relative_to(storage_root).as_posix()
    return f"/storage/{relative}"


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "face_engine_available": face.face_engine_available()}


@app.post("/api/v1/setup/company", status_code=201)
def setup_company(payload: CompanySetupIn, x_bootstrap_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    if x_bootstrap_token != config.BOOTSTRAP_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid bootstrap token")

    admin_key = generate_token("skud_admin")
    employee_token = None
    admin_employee = None
    try:
        with connect() as db:
            cursor = db.execute("INSERT INTO companies (name, slug) VALUES (?, ?)", (payload.name, payload.slug))
            company_id = int(cursor.lastrowid)
            db.execute(
                "INSERT INTO api_keys (company_id, name, key_hash, role) VALUES (?, ?, ?, 'admin')",
                (company_id, payload.admin_key_name, hash_token(admin_key)),
            )
            if payload.owner_login and payload.owner_password:
                employee_cursor = db.execute(
                    """
                    INSERT INTO employees (company_id, full_name, status, role, access_level, pass_status)
                    VALUES (?, ?, 'active', ?, 3, 'active')
                    """,
                    (company_id, payload.owner_full_name or "Administrator", payload.owner_role),
                )
                employee_id = int(employee_cursor.lastrowid)
                upsert_employee_credential(db, company_id, employee_id, payload.owner_login, payload.owner_password)
                employee_token = generate_token("skud_employee")
                db.execute(
                    """
                    INSERT INTO employee_sessions (company_id, employee_id, token_hash)
                    VALUES (?, ?, ?)
                    """,
                    (company_id, employee_id, hash_token(employee_token)),
                )
                admin_employee = row_to_dict(db.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone())
            company = row_to_dict(db.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone())
    except sqlite3.IntegrityError as exc:
        if "companies.slug" in str(exc) or "UNIQUE constraint failed" in str(exc):
            raise HTTPException(status_code=409, detail="Company slug already exists") from exc
        raise

    return {
        "company": company,
        "admin_api_key": admin_key,
        "admin_employee": admin_employee,
        "admin_employee_token": employee_token,
    }


@app.get("/api/v1/company")
def get_company(admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    return {"id": admin["company_id"], "name": admin["company_name"], "slug": admin["company_slug"]}


@app.get("/api/v1/settings/security")
def get_security_settings(admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        return {"anti_passback_minutes": anti_passback_minutes(db, admin["company_id"])}


@app.patch("/api/v1/settings/security")
def update_security_settings(payload: SecuritySettingsIn, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        set_company_setting(db, admin["company_id"], "anti_passback_minutes", str(payload.anti_passback_minutes))
    return {"anti_passback_minutes": payload.anti_passback_minutes}


@app.post("/api/v1/qr-passes/revoke-expired")
def revoke_expired_qr_passes_endpoint(admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    return {"revoked_count": revoke_expired_qr_passes(admin["company_id"])}


@app.post("/api/v1/employees", status_code=201)
def create_employee(payload: EmployeeIn, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO employees (company_id, external_id, full_name, position, email, phone, status, role, access_level, pass_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                admin["company_id"],
                payload.external_id,
                payload.full_name,
                payload.position,
                payload.email,
                payload.phone,
                payload.status,
                payload.role,
                payload.access_level,
                payload.pass_status,
            ),
        )
        employee_id = int(cursor.lastrowid)
        if payload.password:
            login = payload.login or payload.email or payload.external_id
            if not login:
                raise HTTPException(status_code=400, detail="login, email or external_id is required when password is set")
            upsert_employee_credential(db, admin["company_id"], employee_id, login, payload.password)
        return require_company_item(db, "employees", admin["company_id"], employee_id)


@app.get("/api/v1/employees")
def list_employees(admin: Annotated[dict[str, Any], Depends(current_admin)]) -> list[dict[str, Any]]:
    with connect() as db:
        return rows_to_dicts(db.execute("SELECT * FROM employees WHERE company_id = ? ORDER BY full_name", (admin["company_id"],)).fetchall())


@app.get("/api/v1/employees/{employee_id}")
def get_employee(employee_id: int, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        return require_company_item(db, "employees", admin["company_id"], employee_id)


@app.get("/api/v1/employees/{employee_id}/profile")
def get_employee_profile(employee_id: int, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        employee = require_company_item(db, "employees", admin["company_id"], employee_id)
        rules = [
            enrich_rule(rule)
            for rule in rows_to_dicts(
                db.execute(
                    """
                    SELECT access_rules.*, rooms.name AS room_name, rooms.code AS room_code, rooms.access_level AS room_access_level
                    FROM access_rules
                    JOIN rooms ON rooms.id = access_rules.room_id
                    WHERE access_rules.company_id = ? AND access_rules.employee_id = ?
                    ORDER BY rooms.name
                    """,
                    (admin["company_id"], employee_id),
                ).fetchall()
            )
        ]
        level1_rooms = rows_to_dicts(
            db.execute(
                "SELECT * FROM rooms WHERE company_id = ? AND access_level = 1 AND status = 'active' ORDER BY name",
                (admin["company_id"],),
            ).fetchall()
        )
        events = rows_to_dicts(
            db.execute(
                """
                SELECT * FROM access_events
                WHERE company_id = ? AND employee_id = ?
                ORDER BY occurred_at DESC
                LIMIT 100
                """,
                (admin["company_id"], employee_id),
            ).fetchall()
        )
    return {"employee": employee, "access_rules": rules, "level1_rooms": level1_rooms, "events": events}


@app.patch("/api/v1/employees/{employee_id}")
def update_employee(employee_id: int, payload: EmployeePatch, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if updates:
        columns = ", ".join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values()) + [employee_id, admin["company_id"]]
        with connect() as db:
            db.execute(f"UPDATE employees SET {columns}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?", values)
            if updates.get("status") == "fired":
                db.execute(
                    """
                    UPDATE employees
                    SET pass_status = 'blocked', pass_block_reason = 'employee_fired', fired_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND company_id = ?
                    """,
                    (employee_id, admin["company_id"]),
                )
                db.execute(
                    """
                    UPDATE qr_passes
                    SET revoked_at = CURRENT_TIMESTAMP, revoked_reason = 'employee_fired'
                    WHERE company_id = ? AND revoked_at IS NULL
                      AND (
                        (subject_type = 'employee' AND subject_id = ?)
                        OR (subject_type = 'guest' AND subject_id IN (
                            SELECT id FROM guests WHERE company_id = ? AND host_employee_id = ?
                        ))
                      )
                    """,
                    (admin["company_id"], employee_id, admin["company_id"], employee_id),
                )
                db.execute("UPDATE guests SET status = 'revoked', updated_at = CURRENT_TIMESTAMP WHERE company_id = ? AND host_employee_id = ?", (admin["company_id"], employee_id))
                log_access_change(db, admin["company_id"], "employee_fired_passes_revoked", employee_id, details={"status": "fired"})
            elif "pass_status" in updates:
                log_access_change(db, admin["company_id"], f"employee_pass_{updates['pass_status']}", employee_id, details=updates)
            return require_company_item(db, "employees", admin["company_id"], employee_id)
    return get_employee(employee_id, admin)


@app.delete("/api/v1/employees/{employee_id}")
def delete_employee(employee_id: int, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        employee = require_company_item(db, "employees", admin["company_id"], employee_id)
        db.execute("DELETE FROM employees WHERE id = ? AND company_id = ?", (employee_id, admin["company_id"]))
    return {"status": "deleted", "employee_id": employee["id"]}


@app.post("/api/v1/employees/{employee_id}/credentials")
def set_employee_credentials(
    employee_id: int,
    payload: EmployeeCredentialIn,
    admin: Annotated[dict[str, Any], Depends(current_admin)],
) -> dict[str, Any]:
    with connect() as db:
        employee = require_company_item(db, "employees", admin["company_id"], employee_id)
        upsert_employee_credential(db, admin["company_id"], employee_id, payload.login, payload.password)
    return {"employee_id": employee["id"], "login": normalize_login(payload.login)}


@app.post("/api/v1/employees/{employee_id}/pass-status")
def set_employee_pass_status(
    employee_id: int,
    payload: EmployeePassStatusIn,
    admin: Annotated[dict[str, Any], Depends(current_admin)],
) -> dict[str, Any]:
    with connect() as db:
        require_company_item(db, "employees", admin["company_id"], employee_id)
        db.execute(
            """
            UPDATE employees
            SET pass_status = ?, pass_block_reason = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND company_id = ?
            """,
            (payload.pass_status, payload.reason, employee_id, admin["company_id"]),
        )
        if payload.pass_status == "blocked":
            db.execute(
                """
                UPDATE qr_passes
                SET revoked_at = CURRENT_TIMESTAMP, revoked_reason = ?
                WHERE company_id = ? AND subject_type = 'employee' AND subject_id = ? AND revoked_at IS NULL
                """,
                (payload.reason or "employee_pass_blocked", admin["company_id"], employee_id),
            )
        log_access_change(db, admin["company_id"], f"employee_pass_{payload.pass_status}", employee_id, details=payload.model_dump())
        return require_company_item(db, "employees", admin["company_id"], employee_id)


@app.post("/api/v1/employees/{employee_id}/face-photos", status_code=201)
async def upload_face_photo(
    employee_id: int,
    admin: Annotated[dict[str, Any], Depends(current_admin)],
    file: UploadFile = File(...),
) -> dict[str, Any]:
    content = await file.read()
    with connect() as db:
        require_company_item(db, "employees", admin["company_id"], employee_id)

    path = face.save_upload_bytes(admin["company_id"], employee_id, file.filename or "face.jpg", content)
    encoding, quality_status = face.image_encoding(path)

    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO face_photos (company_id, employee_id, file_path, quality_status)
            VALUES (?, ?, ?, ?)
            """,
            (admin["company_id"], employee_id, path, quality_status),
        )
        photo_id = int(cursor.lastrowid)
        if encoding is not None:
            db.execute(
                """
                INSERT INTO face_embeddings (company_id, employee_id, source_photo_id, embedding_json)
                VALUES (?, ?, ?, ?)
                """,
                (admin["company_id"], employee_id, photo_id, dump_json(encoding)),
            )
        photo = row_to_dict(db.execute("SELECT * FROM face_photos WHERE id = ?", (photo_id,)).fetchone())

    return {"photo": photo, "enrolled": encoding is not None, "quality_status": quality_status}


@app.post("/api/v1/employees/{employee_id}/qr-passes", status_code=201)
def create_employee_qr_pass(
    employee_id: int,
    payload: QrPassIn,
    admin: Annotated[dict[str, Any], Depends(current_admin)],
) -> dict[str, Any]:
    expires_at = payload.expires_at or default_expiration(payload.ttl_hours)
    parse_dt(expires_at)
    nonce = new_nonce()
    with connect() as db:
        employee = require_company_item(db, "employees", admin["company_id"], employee_id)
        if employee.get("pass_status") == "blocked":
            raise HTTPException(status_code=403, detail="Employee pass is blocked")
        cursor = db.execute(
            """
            INSERT INTO qr_passes (company_id, subject_type, subject_id, nonce, expires_at)
            VALUES (?, 'employee', ?, ?, ?)
            """,
            (admin["company_id"], employee_id, nonce, expires_at),
        )
        pass_id = int(cursor.lastrowid)

    qr_payload = create_qr_payload(
        company_id=admin["company_id"],
        qr_pass_id=pass_id,
        subject_type="employee",
        subject_id=employee_id,
        expires_at=expires_at,
        nonce=nonce,
    )
    qr_dir = Path(config.STORAGE_DIR) / "qr" / str(admin["company_id"])
    qr_dir.mkdir(parents=True, exist_ok=True)
    qr_path = qr_dir / f"employee_{employee_id}_pass_{pass_id}.png"
    save_qr_png(qr_payload, str(qr_path))
    return {
        "id": pass_id,
        "payload": qr_payload,
        "qr_png_path": str(qr_path),
        "qr_png_url": qr_public_url(qr_path),
        "expires_at": expires_at,
    }


@app.post("/api/v1/guest-passes", status_code=201)
def create_guest_pass(payload: GuestPassIn, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    starts_at = parse_dt(payload.visit_starts_at).isoformat()
    ends_at = parse_dt(payload.visit_ends_at).isoformat()
    if parse_dt(ends_at) <= parse_dt(starts_at):
        raise HTTPException(status_code=400, detail="visit_ends_at must be later than visit_starts_at")

    nonce = new_nonce()
    with connect() as db:
        require_company_item(db, "employees", admin["company_id"], payload.host_employee_id)
        require_company_item(db, "rooms", admin["company_id"], payload.room_id)
        if not employee_can_invite_guest(db, admin["company_id"], payload.host_employee_id, payload.room_id):
            raise HTTPException(status_code=403, detail="Host employee has no active access to this room")
        cursor = db.execute(
            """
            INSERT INTO guests (
                company_id, host_employee_id, room_id, full_name, document_number,
                visit_starts_at, visit_ends_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                admin["company_id"],
                payload.host_employee_id,
                payload.room_id,
                payload.full_name,
                payload.document_number,
                starts_at,
                ends_at,
            ),
        )
        guest_id = int(cursor.lastrowid)
        pass_cursor = db.execute(
            """
            INSERT INTO qr_passes (company_id, subject_type, subject_id, nonce, expires_at)
            VALUES (?, 'guest', ?, ?, ?)
            """,
            (admin["company_id"], guest_id, nonce, ends_at),
        )
        pass_id = int(pass_cursor.lastrowid)
        guest = row_to_dict(db.execute("SELECT * FROM guests WHERE id = ?", (guest_id,)).fetchone())

    qr_payload = create_qr_payload(
        company_id=admin["company_id"],
        qr_pass_id=pass_id,
        subject_type="guest",
        subject_id=guest_id,
        expires_at=ends_at,
        nonce=nonce,
    )
    qr_dir = Path(config.STORAGE_DIR) / "qr" / str(admin["company_id"])
    qr_dir.mkdir(parents=True, exist_ok=True)
    qr_path = qr_dir / f"guest_{guest_id}_pass_{pass_id}.png"
    save_qr_png(qr_payload, str(qr_path))
    return {
        "guest": guest,
        "qr_pass": {
            "id": pass_id,
            "payload": qr_payload,
            "qr_png_path": str(qr_path),
            "qr_png_url": qr_public_url(qr_path),
            "expires_at": ends_at,
        },
    }


@app.get("/api/v1/guests")
def list_guests(
    admin: Annotated[dict[str, Any], Depends(current_admin)],
    host_employee_id: int | None = None,
    limit: int = Query(100, le=500),
) -> list[dict[str, Any]]:
    query = "SELECT * FROM guests WHERE company_id = ?"
    values: list[Any] = [admin["company_id"]]
    if host_employee_id is not None:
        query += " AND host_employee_id = ?"
        values.append(host_employee_id)
    query += " ORDER BY visit_starts_at DESC LIMIT ?"
    values.append(limit)
    with connect() as db:
        return rows_to_dicts(db.execute(query, values).fetchall())


@app.post("/api/v1/guests/{guest_id}/qr-passes", status_code=201)
def reissue_guest_qr_pass(guest_id: int, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    nonce = new_nonce()
    with connect() as db:
        guest = require_company_item(db, "guests", admin["company_id"], guest_id)
        if guest["status"] != "active":
            raise HTTPException(status_code=403, detail="Guest invitation is not active")
        if parse_dt(guest["visit_ends_at"]) < utc_now():
            raise HTTPException(status_code=403, detail="Guest invitation has expired")
        pass_cursor = db.execute(
            """
            INSERT INTO qr_passes (company_id, subject_type, subject_id, nonce, expires_at)
            VALUES (?, 'guest', ?, ?, ?)
            """,
            (admin["company_id"], guest_id, nonce, guest["visit_ends_at"]),
        )
        pass_id = int(pass_cursor.lastrowid)

    qr_payload = create_qr_payload(
        company_id=admin["company_id"],
        qr_pass_id=pass_id,
        subject_type="guest",
        subject_id=guest_id,
        expires_at=guest["visit_ends_at"],
        nonce=nonce,
    )
    qr_dir = Path(config.STORAGE_DIR) / "qr" / str(admin["company_id"])
    qr_dir.mkdir(parents=True, exist_ok=True)
    qr_path = qr_dir / f"guest_{guest_id}_pass_{pass_id}.png"
    save_qr_png(qr_payload, str(qr_path))
    return {
        "id": pass_id,
        "payload": qr_payload,
        "qr_png_path": str(qr_path),
        "qr_png_url": qr_public_url(qr_path),
        "expires_at": guest["visit_ends_at"],
    }


@app.post("/api/v1/employee/login")
def employee_login(payload: EmployeeLoginIn) -> dict[str, Any]:
    with connect() as db:
        credential = row_to_dict(
            db.execute(
                """
                SELECT employee_credentials.*, employees.status, employees.full_name,
                       companies.slug AS company_slug
                FROM employee_credentials
                JOIN employees ON employees.id = employee_credentials.employee_id
                JOIN companies ON companies.id = employee_credentials.company_id
                WHERE companies.slug = ? AND employee_credentials.login = ?
                """,
                (payload.company_slug, normalize_login(payload.login)),
            ).fetchone()
        )
        if not credential or not verify_password(payload.password, credential["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid login or password")
        if credential["status"] != "active":
            raise HTTPException(status_code=403, detail="Employee is not active")

        token = generate_token("skud_employee")
        db.execute(
            """
            INSERT INTO employee_sessions (company_id, employee_id, token_hash)
            VALUES (?, ?, ?)
            """,
            (credential["company_id"], credential["employee_id"], hash_token(token)),
        )

    return {
        "employee_token": token,
        "employee_id": credential["employee_id"],
        "company_id": credential["company_id"],
        "company_slug": credential["company_slug"],
        "full_name": credential["full_name"],
    }


@app.post("/api/v1/employees/{employee_id}/passes/reissue", status_code=201)
def reissue_employee_permanent_pass(
    employee_id: int,
    payload: QrPassIn,
    admin: Annotated[dict[str, Any], Depends(current_admin)],
) -> dict[str, Any]:
    with connect() as db:
        require_company_item(db, "employees", admin["company_id"], employee_id)
        db.execute(
            "UPDATE employees SET pass_status = 'active', pass_block_reason = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?",
            (employee_id, admin["company_id"]),
        )
        log_access_change(db, admin["company_id"], "employee_pass_reissued", employee_id)
    if not payload.expires_at and payload.ttl_hours == 12:
        payload.ttl_hours = 24 * 365 * 5
    return create_employee_qr_pass(employee_id, payload, admin)


@app.post("/api/v1/qr-passes/{qr_pass_id}/revoke")
def revoke_qr_pass(qr_pass_id: int, payload: QrPassRevokeIn, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        qr_pass = require_company_item(db, "qr_passes", admin["company_id"], qr_pass_id)
        db.execute(
            """
            UPDATE qr_passes
            SET revoked_at = CURRENT_TIMESTAMP, revoked_reason = ?
            WHERE id = ? AND company_id = ?
            """,
            (payload.reason, qr_pass_id, admin["company_id"]),
        )
        employee_id = qr_pass["subject_id"] if qr_pass["subject_type"] == "employee" else None
        log_access_change(db, admin["company_id"], "qr_pass_revoked", employee_id, details={"qr_pass_id": qr_pass_id, "reason": payload.reason})
    return {"status": "revoked", "qr_pass_id": qr_pass_id}


@app.get("/api/v1/employee/me")
def employee_me(employee: Annotated[dict[str, Any], Depends(current_employee)]) -> dict[str, Any]:
    employee_id = int(employee["employee_id"])
    company_id = int(employee["company_id"])
    with connect() as db:
        access_rules = rows_to_dicts(
            db.execute(
                """
                SELECT access_rules.*, rooms.name AS room_name, rooms.code AS room_code
                FROM access_rules
                JOIN rooms ON rooms.id = access_rules.room_id
                WHERE access_rules.company_id = ? AND access_rules.employee_id = ?
                ORDER BY rooms.name
                """,
                (company_id, employee_id),
            ).fetchall()
        )
        events = rows_to_dicts(
            db.execute(
                """
                SELECT access_events.*, rooms.name AS room_name
                FROM access_events
                LEFT JOIN rooms ON rooms.id = access_events.room_id
                WHERE access_events.company_id = ? AND access_events.employee_id = ?
                ORDER BY access_events.occurred_at DESC
                LIMIT 20
                """,
                (company_id, employee_id),
            ).fetchall()
        )
        notifications = rows_to_dicts(
            db.execute(
                """
                SELECT * FROM notifications
                WHERE company_id = ? AND employee_id = ?
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (company_id, employee_id),
            ).fetchall()
        )
    return {
        "company": {"id": company_id, "name": employee["company_name"], "slug": employee["company_slug"]},
        "employee": {
            "id": employee_id,
            "full_name": employee["full_name"],
            "external_id": employee["external_id"],
            "position": employee["position"],
            "email": employee["email"],
            "phone": employee["phone"],
            "status": employee["status"],
            "role": employee.get("role"),
            "access_level": employee.get("access_level"),
            "pass_status": employee.get("pass_status"),
            "photo_url": employee_photo_url(company_id, employee_id),
        },
        "access_rules": [enrich_rule(rule) for rule in access_rules],
        "recent_events": events,
        "notifications": notifications,
    }


@app.get("/api/v1/employee/access-events")
def employee_own_events(employee: Annotated[dict[str, Any], Depends(current_employee)], limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
    with connect() as db:
        return rows_to_dicts(
            db.execute(
                """
                SELECT access_events.*, rooms.name AS room_name
                FROM access_events
                LEFT JOIN rooms ON rooms.id = access_events.room_id
                WHERE access_events.company_id = ? AND access_events.employee_id = ?
                ORDER BY access_events.occurred_at DESC
                LIMIT ?
                """,
                (employee["company_id"], employee["employee_id"], limit),
            ).fetchall()
        )


@app.get("/api/v1/employee/guests")
def employee_own_guests(employee: Annotated[dict[str, Any], Depends(current_employee)], limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
    with connect() as db:
        return rows_to_dicts(
            db.execute(
                """
                SELECT guests.*, rooms.name AS room_name
                FROM guests
                LEFT JOIN rooms ON rooms.id = guests.room_id
                WHERE guests.company_id = ? AND guests.host_employee_id = ?
                ORDER BY guests.visit_starts_at DESC
                LIMIT ?
                """,
                (employee["company_id"], employee["employee_id"], limit),
            ).fetchall()
        )


@app.get("/api/v1/employee/notifications")
def employee_notifications(employee: Annotated[dict[str, Any], Depends(current_employee)], limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
    with connect() as db:
        return rows_to_dicts(
            db.execute(
                """
                SELECT * FROM notifications
                WHERE company_id = ? AND employee_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (employee["company_id"], employee["employee_id"], limit),
            ).fetchall()
        )


@app.post("/api/v1/employee/qr-passes", status_code=201)
def create_own_qr_pass(payload: QrPassIn, employee: Annotated[dict[str, Any], Depends(current_employee)]) -> dict[str, Any]:
    employee_id = int(employee["employee_id"])
    company_id = int(employee["company_id"])
    if employee.get("pass_status") == "blocked":
        raise HTTPException(status_code=403, detail="Employee pass is blocked")
    expires_at = payload.expires_at or default_expiration(payload.ttl_hours)
    parse_dt(expires_at)
    nonce = new_nonce()
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO qr_passes (company_id, subject_type, subject_id, nonce, expires_at)
            VALUES (?, 'employee', ?, ?, ?)
            """,
            (company_id, employee_id, nonce, expires_at),
        )
        pass_id = int(cursor.lastrowid)

    qr_payload = create_qr_payload(
        company_id=company_id,
        qr_pass_id=pass_id,
        subject_type="employee",
        subject_id=employee_id,
        expires_at=expires_at,
        nonce=nonce,
    )
    qr_dir = Path(config.STORAGE_DIR) / "qr" / str(company_id)
    qr_dir.mkdir(parents=True, exist_ok=True)
    qr_path = qr_dir / f"employee_{employee_id}_pass_{pass_id}.png"
    save_qr_png(qr_payload, str(qr_path))
    return {
        "id": pass_id,
        "payload": qr_payload,
        "qr_png_path": str(qr_path),
        "qr_png_url": qr_public_url(qr_path),
        "expires_at": expires_at,
    }


@app.post("/api/v1/employee/guest-passes", status_code=201)
def create_own_guest_pass(payload: EmployeeGuestPassIn, employee: Annotated[dict[str, Any], Depends(current_employee)]) -> dict[str, Any]:
    company_id = int(employee["company_id"])
    employee_id = int(employee["employee_id"])
    starts_at = parse_dt(payload.visit_starts_at).isoformat()
    ends_at = parse_dt(payload.visit_ends_at).isoformat()
    if parse_dt(ends_at) <= parse_dt(starts_at):
        raise HTTPException(status_code=400, detail="visit_ends_at must be later than visit_starts_at")

    with connect() as db:
        if not employee_can_invite_guest(db, company_id, employee_id, payload.room_id):
            raise HTTPException(status_code=403, detail="You cannot invite guests to this room")

    admin_like = {"company_id": company_id}
    guest_payload = GuestPassIn(
        host_employee_id=employee_id,
        room_id=payload.room_id,
        full_name=payload.full_name,
        document_number=payload.document_number,
        visit_starts_at=starts_at,
        visit_ends_at=ends_at,
    )
    return create_guest_pass(guest_payload, admin_like)


@app.post("/api/v1/employee/change-password")
def employee_change_password(
    payload: EmployeePasswordChangeIn,
    employee: Annotated[dict[str, Any], Depends(current_employee)],
) -> dict[str, Any]:
    with connect() as db:
        credential = row_to_dict(
            db.execute(
                "SELECT * FROM employee_credentials WHERE company_id = ? AND employee_id = ?",
                (employee["company_id"], employee["employee_id"]),
            ).fetchone()
        )
        if not credential or not verify_password(payload.current_password, credential["password_hash"]):
            raise HTTPException(status_code=403, detail="Current password is incorrect")
        db.execute(
            """
            UPDATE employee_credentials
            SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (hash_password(payload.new_password), credential["id"]),
        )
    return {"status": "password_changed"}


@app.post("/api/v1/rooms", status_code=201)
def create_room(payload: RoomIn, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO rooms (company_id, name, code, description, capacity, access_level, allowed_methods, biometric_only, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                admin["company_id"],
                payload.name,
                payload.code,
                payload.description,
                payload.capacity,
                payload.access_level,
                dump_json(payload.allowed_methods),
                int(payload.biometric_only),
                payload.status,
            ),
        )
        return enrich_room(require_company_item(db, "rooms", admin["company_id"], int(cursor.lastrowid)))


@app.get("/api/v1/rooms")
def list_rooms(admin: Annotated[dict[str, Any], Depends(current_admin)]) -> list[dict[str, Any]]:
    with connect() as db:
        return [enrich_room(room) for room in rows_to_dicts(db.execute("SELECT * FROM rooms WHERE company_id = ? ORDER BY name", (admin["company_id"],)).fetchall())]


@app.patch("/api/v1/rooms/{room_id}")
def update_room(room_id: int, payload: RoomPatch, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if "allowed_methods" in updates:
        updates["allowed_methods"] = dump_json(updates["allowed_methods"])
    if "biometric_only" in updates:
        updates["biometric_only"] = int(updates["biometric_only"])
    if updates:
        columns = ", ".join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values()) + [room_id, admin["company_id"]]
        with connect() as db:
            db.execute(f"UPDATE rooms SET {columns}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?", values)
            if updates.get("access_level") == 3 or updates.get("biometric_only"):
                log_access_change(db, admin["company_id"], "room_level3_or_biometric_enabled", room_id=room_id, details=updates)
            return enrich_room(require_company_item(db, "rooms", admin["company_id"], room_id))
    with connect() as db:
        return enrich_room(require_company_item(db, "rooms", admin["company_id"], room_id))


@app.get("/api/v1/rooms/{room_id}/level3-allowlist")
def list_room_level3_allowlist(room_id: int, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> list[dict[str, Any]]:
    with connect() as db:
        require_company_item(db, "rooms", admin["company_id"], room_id)
        return rows_to_dicts(
            db.execute(
                """
                SELECT room_level3_allowlist.*, employees.full_name, employees.external_id
                FROM room_level3_allowlist
                JOIN employees ON employees.id = room_level3_allowlist.employee_id
                WHERE room_level3_allowlist.company_id = ? AND room_level3_allowlist.room_id = ?
                ORDER BY employees.full_name
                """,
                (admin["company_id"], room_id),
            ).fetchall()
        )


@app.post("/api/v1/rooms/{room_id}/level3-allowlist", status_code=201)
def add_room_level3_allowlist(room_id: int, payload: RoomAllowlistIn, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        require_company_item(db, "rooms", admin["company_id"], room_id)
        require_company_item(db, "employees", admin["company_id"], payload.employee_id)
        db.execute(
            """
            INSERT OR IGNORE INTO room_level3_allowlist (company_id, room_id, employee_id)
            VALUES (?, ?, ?)
            """,
            (admin["company_id"], room_id, payload.employee_id),
        )
        log_access_change(db, admin["company_id"], "level3_allowlist_added", payload.employee_id, room_id)
    return {"status": "added", "room_id": room_id, "employee_id": payload.employee_id}


@app.delete("/api/v1/rooms/{room_id}/level3-allowlist/{employee_id}")
def remove_room_level3_allowlist(room_id: int, employee_id: int, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        db.execute(
            "DELETE FROM room_level3_allowlist WHERE company_id = ? AND room_id = ? AND employee_id = ?",
            (admin["company_id"], room_id, employee_id),
        )
        log_access_change(db, admin["company_id"], "level3_allowlist_removed", employee_id, room_id)
    return {"status": "removed", "room_id": room_id, "employee_id": employee_id}


@app.post("/api/v1/rooms/{room_id}/limit-overrides", status_code=201)
def create_room_limit_override(room_id: int, payload: RoomLimitOverrideIn, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    valid_from = parse_dt(payload.valid_from).isoformat()
    valid_until = parse_dt(payload.valid_until).isoformat()
    if parse_dt(valid_until) <= parse_dt(valid_from):
        raise HTTPException(status_code=400, detail="valid_until must be later than valid_from")
    with connect() as db:
        require_company_item(db, "rooms", admin["company_id"], room_id)
        cursor = db.execute(
            """
            INSERT INTO room_limit_overrides (company_id, room_id, limit_value, valid_from, valid_until, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (admin["company_id"], room_id, payload.limit_value, valid_from, valid_until, payload.reason),
        )
        return require_company_item(db, "room_limit_overrides", admin["company_id"], int(cursor.lastrowid))


@app.post("/api/v1/access-rules", status_code=201)
def upsert_access_rule(payload: AccessRuleIn, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        require_company_item(db, "employees", admin["company_id"], payload.employee_id)
        require_company_item(db, "rooms", admin["company_id"], payload.room_id)
        db.execute(
            """
            INSERT INTO access_rules (
                company_id, employee_id, room_id, allowed_methods, valid_from, valid_until, schedule_json, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, employee_id, room_id) DO UPDATE SET
                allowed_methods = excluded.allowed_methods,
                valid_from = excluded.valid_from,
                valid_until = excluded.valid_until,
                schedule_json = excluded.schedule_json,
                is_active = excluded.is_active,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                admin["company_id"],
                payload.employee_id,
                payload.room_id,
                dump_json(payload.allowed_methods),
                payload.valid_from,
                payload.valid_until,
                dump_json(payload.schedule) if payload.schedule is not None else None,
                int(payload.is_active),
            ),
        )
        rule_id = int(
            db.execute(
                "SELECT id FROM access_rules WHERE company_id = ? AND employee_id = ? AND room_id = ?",
                (admin["company_id"], payload.employee_id, payload.room_id),
            ).fetchone()["id"]
        )
        log_access_change(
            db,
            admin["company_id"],
            "access_rule_upserted" if payload.is_active else "access_revoked",
            payload.employee_id,
            payload.room_id,
            payload.model_dump(),
        )
        return enrich_rule(require_company_item(db, "access_rules", admin["company_id"], rule_id))


@app.get("/api/v1/access-rules")
def list_access_rules(
    admin: Annotated[dict[str, Any], Depends(current_admin)],
    employee_id: int | None = None,
    room_id: int | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM access_rules WHERE company_id = ?"
    values: list[Any] = [admin["company_id"]]
    if employee_id is not None:
        query += " AND employee_id = ?"
        values.append(employee_id)
    if room_id is not None:
        query += " AND room_id = ?"
        values.append(room_id)
    with connect() as db:
        return [enrich_rule(item) for item in rows_to_dicts(db.execute(query, values).fetchall())]


@app.patch("/api/v1/access-rules/{rule_id}")
def update_access_rule(rule_id: int, payload: AccessRulePatch, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    db_updates: dict[str, Any] = {}
    for key, value in updates.items():
        if key == "allowed_methods":
            db_updates[key] = dump_json(value)
        elif key == "schedule":
            db_updates["schedule_json"] = dump_json(value) if value is not None else None
        elif key == "is_active":
            db_updates[key] = int(value)
        else:
            db_updates[key] = value
    if db_updates:
        columns = ", ".join([f"{key} = ?" for key in db_updates.keys()])
        values = list(db_updates.values()) + [rule_id, admin["company_id"]]
        with connect() as db:
            before = require_company_item(db, "access_rules", admin["company_id"], rule_id)
            db.execute(f"UPDATE access_rules SET {columns}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?", values)
            log_access_change(
                db,
                admin["company_id"],
                "access_rule_updated" if db_updates.get("is_active", before["is_active"]) else "access_revoked",
                before["employee_id"],
                before["room_id"],
                updates,
            )
            return enrich_rule(require_company_item(db, "access_rules", admin["company_id"], rule_id))
    with connect() as db:
        return enrich_rule(require_company_item(db, "access_rules", admin["company_id"], rule_id))


@app.post("/api/v1/scanners", status_code=201)
def create_scanner(payload: ScannerIn, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    scanner_token = generate_token("skud_scanner")
    with connect() as db:
        require_company_item(db, "rooms", admin["company_id"], payload.room_id)
        cursor = db.execute(
            """
            INSERT INTO scanners (company_id, room_id, name, direction, allowed_methods, token_hash, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                admin["company_id"],
                payload.room_id,
                payload.name,
                payload.direction,
                dump_json(payload.allowed_methods),
                hash_token(scanner_token),
                payload.status,
            ),
        )
        scanner = enrich_scanner(require_company_item(db, "scanners", admin["company_id"], int(cursor.lastrowid)))
    return {"scanner": scanner, "scanner_token": scanner_token}


@app.get("/api/v1/scanners")
def list_scanners(admin: Annotated[dict[str, Any], Depends(current_admin)]) -> list[dict[str, Any]]:
    with connect() as db:
        return [
            enrich_scanner(item)
            for item in rows_to_dicts(db.execute("SELECT * FROM scanners WHERE company_id = ? ORDER BY name", (admin["company_id"],)).fetchall())
        ]


@app.patch("/api/v1/scanners/{scanner_id}")
def update_scanner(scanner_id: int, payload: ScannerPatch, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if "allowed_methods" in updates:
        updates["allowed_methods"] = dump_json(updates["allowed_methods"])
    if updates:
        columns = ", ".join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values()) + [scanner_id, admin["company_id"]]
        with connect() as db:
            db.execute(f"UPDATE scanners SET {columns}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?", values)
            return enrich_scanner(require_company_item(db, "scanners", admin["company_id"], scanner_id))
    with connect() as db:
        return enrich_scanner(require_company_item(db, "scanners", admin["company_id"], scanner_id))


@app.post("/api/v1/scanner/decode-qr")
def scanner_decode_qr(payload: ScannerQrImageIn, scanner: Annotated[dict[str, Any], Depends(current_scanner)]) -> dict[str, Any]:
    qr_payload = decode_qr_image_base64(payload.image_base64)
    return {"qr_payload": qr_payload}


@app.get("/api/v1/scanner/rooms/{room_id}/methods")
def scanner_room_methods(room_id: int, scanner: Annotated[dict[str, Any], Depends(current_scanner)]) -> dict[str, Any]:
    with connect() as db:
        room = enrich_room(require_company_item(db, "rooms", scanner["company_id"], room_id))
    return {
        "room_id": room["id"],
        "room_name": room["name"],
        "room_code": room["code"],
        "allowed_methods": room["allowed_methods"],
        "status": room["status"],
    }


@app.post("/api/v1/scanner/verify")
def scanner_verify(payload: ScannerVerifyIn, scanner: Annotated[dict[str, Any], Depends(current_scanner)]) -> dict[str, Any]:
    room_id = payload.room_id or scanner["room_id"]
    with connect() as db:
        room = enrich_room(require_company_item(db, "rooms", scanner["company_id"], room_id))
    employee_id = None
    confidence = None
    qr_pass_id = None
    guest_id = None
    reason = "unknown"
    subject_type = "employee"
    raw_subject = payload.raw_subject

    if payload.method not in room["allowed_methods"]:
        event_id = log_event(
            company_id=scanner["company_id"],
            employee_id=None,
            room_id=room_id,
            scanner_id=scanner["id"],
            method=payload.method,
            direction=scanner["direction"],
            decision="denied",
            reason="method_not_allowed_for_room",
            raw_subject=payload.raw_subject,
        )
        return {"decision": "denied", "reason": "method_not_allowed_for_room", "event_id": event_id, "room_id": room_id, "scanner_id": scanner["id"]}

    if payload.method == "qr":
        if not payload.qr_payload:
            reason = "qr_payload_required"
        else:
            ok, data, reason = verify_qr_payload(payload.qr_payload)
            if reason == "qr_expired" and data:
                with connect() as db:
                    db.execute(
                        "UPDATE qr_passes SET revoked_at = CURRENT_TIMESTAMP, revoked_reason = 'expired' WHERE id = ? AND company_id = ? AND revoked_at IS NULL",
                        (int(data["qr_pass_id"]), scanner["company_id"]),
                    )
            if ok and data:
                qr_pass_id = int(data["qr_pass_id"])
                with connect() as db:
                    qr_pass = row_to_dict(
                        db.execute(
                            """
                            SELECT * FROM qr_passes
                            WHERE id = ? AND company_id = ? AND nonce = ? AND revoked_at IS NULL
                            """,
                            (qr_pass_id, scanner["company_id"], data["nonce"]),
                        ).fetchone()
                    )
                if not qr_pass:
                    reason = "qr_pass_not_found_or_revoked"
                elif data["subject_type"] == "employee":
                    employee_id = int(data["subject_id"])
                    reason = "identified_by_qr"
                elif data["subject_type"] == "guest":
                    subject_type = "guest"
                    guest_id = int(data["subject_id"])
                    with connect() as db:
                        guest = row_to_dict(
                            db.execute(
                                """
                                SELECT * FROM guests
                                WHERE id = ? AND company_id = ? AND status = 'active'
                                """,
                                (guest_id, scanner["company_id"]),
                            ).fetchone()
                        )
                    if not guest:
                        reason = "guest_not_found_or_inactive"
                    else:
                        guest_id = int(guest["id"])
                        employee_id = int(guest["host_employee_id"])
                        raw_subject = guest["full_name"]
                        if int(guest["room_id"]) != int(room_id):
                            reason = "guest_room_mismatch"
                        elif parse_dt(guest["visit_starts_at"]) > utc_now():
                            reason = "guest_visit_not_started"
                        elif parse_dt(guest["visit_ends_at"]) < utc_now():
                            reason = "guest_visit_expired"
                        else:
                            reason = "guest_access_granted"
                else:
                    reason = "unsupported_qr_subject"
    elif payload.method == "card":
        if not payload.raw_subject:
            reason = "card_subject_required"
        else:
            with connect() as db:
                employee = row_to_dict(
                    db.execute(
                        """
                        SELECT * FROM employees
                        WHERE company_id = ? AND (external_id = ? OR CAST(id AS TEXT) = ?)
                        """,
                        (scanner["company_id"], payload.raw_subject, payload.raw_subject),
                    ).fetchone()
                )
            if employee:
                employee_id = int(employee["id"])
                reason = "identified_by_card"
            else:
                reason = "card_subject_not_found"
    elif payload.method == "face":
        if not payload.face_image_base64:
            reason = "face_image_required"
        else:
            employee_id, confidence, reason = face.recognize_base64(scanner["company_id"], payload.face_image_base64)

    if employee_id and subject_type == "employee":
        anti_ok, anti_reason = check_anti_passback(scanner["company_id"], employee_id, scanner["direction"], payload.method)
        if not anti_ok:
            decision = "denied"
            reason = anti_reason
            notify_anti_passback(scanner["company_id"], employee_id, anti_reason)
        else:
            granted, access_reason = check_access(scanner["company_id"], employee_id, room_id, payload.method, scanner["direction"])
            decision = "granted" if granted else "denied"
            reason = access_reason
    elif employee_id and subject_type == "guest" and reason == "guest_access_granted":
        decision = "granted"
    else:
        decision = "denied"

    event_id = log_event(
        company_id=scanner["company_id"],
        employee_id=employee_id,
        room_id=room_id,
        scanner_id=scanner["id"],
        method=payload.method,
        direction=scanner["direction"],
        decision=decision,
        reason=reason,
        confidence=confidence,
        qr_pass_id=qr_pass_id,
        raw_subject=raw_subject,
        subject_type=subject_type,
        guest_id=guest_id,
    )
    if subject_type == "guest" and employee_id:
        with connect() as db:
            if decision == "granted":
                title = "Гость прошел"
                body = f"Ваш гость {raw_subject or ''} прошел через {scanner['name']}."
                notification_type = "guest_granted"
            else:
                title = "Гость не смог пройти"
                body = f"Ваш гость {raw_subject or ''} не смог зайти в {scanner['name']}, обратитесь в отдел безопасности"
                notification_type = "guest_denied"
            notify_employee(db, scanner["company_id"], employee_id, notification_type, title, body, {"event_id": event_id, "room_id": room_id})

    with connect() as db:
        security_room = row_to_dict(db.execute("SELECT access_level FROM rooms WHERE id = ? AND company_id = ?", (room_id, scanner["company_id"])).fetchone())
    if decision == "denied" and security_room and int(security_room.get("access_level") or 1) >= 3:
        create_security_alert(scanner["company_id"], employee_id, room_id, event_id, reason)

    response: dict[str, Any] = {
        "decision": decision,
        "reason": reason,
        "signal_color": "green" if decision == "granted" else "red",
        "event_id": event_id,
        "unlock_seconds": config.UNLOCK_SECONDS if decision == "granted" else 0,
        "employee_id": employee_id,
        "room_id": room_id,
        "scanner_id": scanner["id"],
    }
    if confidence is not None:
        response["confidence"] = confidence
    return response


@app.get("/api/v1/access-events")
def list_access_events(
    admin: Annotated[dict[str, Any], Depends(current_admin)],
    employee_id: int | None = None,
    room_id: int | None = None,
    decision: str | None = None,
    subject_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(100, le=500),
) -> list[dict[str, Any]]:
    query = "SELECT * FROM access_events WHERE company_id = ?"
    values: list[Any] = [admin["company_id"]]
    for field, value in (("employee_id", employee_id), ("room_id", room_id), ("decision", decision), ("subject_type", subject_type)):
        if value is not None:
            query += f" AND {field} = ?"
            values.append(value)
    if date_from:
        query += " AND occurred_at >= ?"
        values.append(parse_dt(date_from).isoformat())
    if date_to:
        query += " AND occurred_at <= ?"
        values.append(parse_dt(date_to).isoformat())
    query += " ORDER BY occurred_at DESC LIMIT ?"
    values.append(limit)
    with connect() as db:
        return rows_to_dicts(db.execute(query, values).fetchall())


@app.get("/api/v1/access-change-logs")
def list_access_change_logs(
    admin: Annotated[dict[str, Any], Depends(current_admin)],
    employee_id: int | None = None,
    room_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(100, le=500),
) -> list[dict[str, Any]]:
    query = "SELECT * FROM access_change_logs WHERE company_id = ?"
    values: list[Any] = [admin["company_id"]]
    if employee_id is not None:
        query += " AND employee_id = ?"
        values.append(employee_id)
    if room_id is not None:
        query += " AND room_id = ?"
        values.append(room_id)
    if date_from:
        query += " AND created_at >= ?"
        values.append(parse_dt(date_from).isoformat())
    if date_to:
        query += " AND created_at <= ?"
        values.append(parse_dt(date_to).isoformat())
    query += " ORDER BY created_at DESC LIMIT ?"
    values.append(limit)
    with connect() as db:
        return rows_to_dicts(db.execute(query, values).fetchall())


@app.get("/api/v1/security-alerts")
def list_security_alerts(
    admin: Annotated[dict[str, Any], Depends(current_admin)],
    room_id: int | None = None,
    employee_id: int | None = None,
    limit: int = Query(100, le=500),
) -> list[dict[str, Any]]:
    query = "SELECT * FROM security_alerts WHERE company_id = ?"
    values: list[Any] = [admin["company_id"]]
    if room_id is not None:
        query += " AND room_id = ?"
        values.append(room_id)
    if employee_id is not None:
        query += " AND employee_id = ?"
        values.append(employee_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    values.append(limit)
    with connect() as db:
        return rows_to_dicts(db.execute(query, values).fetchall())


@app.get("/api/v1/employees/{employee_id}/access-events")
def employee_events(employee_id: int, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> list[dict[str, Any]]:
    return list_access_events(admin=admin, employee_id=employee_id)


@app.get("/api/v1/rooms/{room_id}/access-events")
def room_events(room_id: int, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> list[dict[str, Any]]:
    return list_access_events(admin=admin, room_id=room_id)


@app.get("/api/v1/stats/occupancy")
def occupancy(admin: Annotated[dict[str, Any], Depends(current_admin)]) -> list[dict[str, Any]]:
    with connect() as db:
        rows = rows_to_dicts(
            db.execute(
                """
                SELECT rooms.id AS room_id, rooms.name AS room_name, rooms.code AS room_code,
                       employees.id AS employee_id, employees.full_name, room_occupancy.entered_at
                FROM rooms
                LEFT JOIN room_occupancy ON room_occupancy.room_id = rooms.id
                LEFT JOIN employees ON employees.id = room_occupancy.employee_id
                WHERE rooms.company_id = ?
                ORDER BY rooms.name, employees.full_name
                """,
                (admin["company_id"],),
            ).fetchall()
        )

    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        room = result.setdefault(
            row["room_id"],
            {
                "room_id": row["room_id"],
                "room_name": row["room_name"],
                "room_code": row["room_code"],
                "current_count": 0,
                "employees": [],
            },
        )
        if row["employee_id"] is not None:
            room["current_count"] += 1
            room["employees"].append({"id": row["employee_id"], "full_name": row["full_name"], "entered_at": row["entered_at"]})
    return list(result.values())


@app.get("/api/v1/stats/throughput")
def throughput(
    admin: Annotated[dict[str, Any], Depends(current_admin)],
    room_id: int | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT date(occurred_at) AS day, room_id, COUNT(*) AS granted_entries
        FROM access_events
        WHERE company_id = ? AND decision = 'granted' AND direction = 'entry'
    """
    values: list[Any] = [admin["company_id"]]
    if room_id is not None:
        query += " AND room_id = ?"
        values.append(room_id)
    query += " GROUP BY date(occurred_at), room_id ORDER BY day DESC, room_id"
    with connect() as db:
        return rows_to_dicts(db.execute(query, values).fetchall())


@app.get("/api/v1/stats/office-time")
def office_time(
    admin: Annotated[dict[str, Any], Depends(current_admin)],
    employee_id: int | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT employee_id, room_id, direction, occurred_at
        FROM access_events
        WHERE company_id = ? AND decision = 'granted' AND employee_id IS NOT NULL
    """
    values: list[Any] = [admin["company_id"]]
    if employee_id is not None:
        query += " AND employee_id = ?"
        values.append(employee_id)
    query += " ORDER BY employee_id, room_id, occurred_at"
    with connect() as db:
        events = rows_to_dicts(db.execute(query, values).fetchall())

    open_entries: dict[tuple[int, int], datetime] = {}
    totals: dict[tuple[int, str], float] = {}
    now = utc_now()
    for event in events:
        key = (int(event["employee_id"]), int(event["room_id"]))
        day = event["occurred_at"][:10]
        total_key = (int(event["employee_id"]), day)
        occurred = parse_dt(event["occurred_at"])
        if event["direction"] == "entry":
            open_entries[key] = occurred
        elif event["direction"] == "exit" and key in open_entries:
            totals[total_key] = totals.get(total_key, 0.0) + max(0.0, (occurred - open_entries.pop(key)).total_seconds())

    for key, entered_at in open_entries.items():
        total_key = (key[0], entered_at.date().isoformat())
        totals[total_key] = totals.get(total_key, 0.0) + max(0.0, (now - entered_at).total_seconds())

    return [
        {"employee_id": key[0], "day": key[1], "seconds_in_office": int(seconds)}
        for key, seconds in sorted(totals.items(), key=lambda item: (item[0][1], item[0][0]), reverse=True)
    ]


@app.get("/api/v1/reports/employee-attendance")
def employee_attendance_report(
    admin: Annotated[dict[str, Any], Depends(current_admin)],
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    from_dt = sqlite_dt(date_from) or (utc_now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    to_dt = sqlite_dt(date_to) or utc_now().strftime("%Y-%m-%d %H:%M:%S")
    with connect() as db:
        return rows_to_dicts(
            db.execute(
                """
                SELECT
                    employees.id AS employee_id,
                    employees.external_id AS employee_external_id,
                    employees.full_name AS employee_full_name,
                    substr(access_events.occurred_at, 1, 10) AS visit_day,
                    rooms.id AS room_id,
                    rooms.name AS room_name,
                    MIN(CASE WHEN access_events.direction = 'entry' THEN access_events.occurred_at END) AS first_entry_at,
                    MAX(CASE WHEN access_events.direction = 'exit' THEN access_events.occurred_at END) AS last_exit_at,
                    GROUP_CONCAT(DISTINCT access_events.method) AS identification_methods,
                    COUNT(*) AS access_event_count
                FROM access_events
                JOIN employees ON employees.id = access_events.employee_id
                LEFT JOIN rooms ON rooms.id = access_events.room_id
                WHERE access_events.company_id = ?
                  AND access_events.decision = 'granted'
                  AND access_events.employee_id IS NOT NULL
                  AND datetime(access_events.occurred_at) >= datetime(?)
                  AND datetime(access_events.occurred_at) <= datetime(?)
                GROUP BY employees.id, visit_day, rooms.id
                ORDER BY visit_day DESC, employees.full_name, rooms.name
                """,
                (admin["company_id"], from_dt, to_dt),
            ).fetchall()
        )


@app.get("/api/v1/reports/room-utilization")
def room_utilization_report(
    admin: Annotated[dict[str, Any], Depends(current_admin)],
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    from_dt = sqlite_dt(date_from) or (utc_now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    to_dt = sqlite_dt(date_to) or utc_now().strftime("%Y-%m-%d %H:%M:%S")
    with connect() as db:
        return rows_to_dicts(
            db.execute(
                """
                SELECT
                    rooms.id AS room_id,
                    rooms.name AS room_name,
                    substr(access_events.occurred_at, 1, 10) AS day,
                    COUNT(DISTINCT CASE WHEN access_events.direction = 'entry' THEN access_events.employee_id END) AS people_count,
                    MIN(CASE WHEN access_events.direction = 'entry' THEN access_events.occurred_at END) AS first_entry_at,
                    MAX(CASE WHEN access_events.direction = 'exit' THEN access_events.occurred_at END) AS last_exit_at,
                    COUNT(*) AS access_event_count
                FROM access_events
                JOIN rooms ON rooms.id = access_events.room_id
                WHERE access_events.company_id = ?
                  AND access_events.decision = 'granted'
                  AND datetime(access_events.occurred_at) >= datetime(?)
                  AND datetime(access_events.occurred_at) <= datetime(?)
                GROUP BY rooms.id, day
                ORDER BY day DESC, rooms.name
                """,
                (admin["company_id"], from_dt, to_dt),
            ).fetchall()
        )
