from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

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
    EmployeeIn,
    EmployeePatch,
    QrPassIn,
    RoomIn,
    RoomPatch,
    ScannerIn,
    ScannerPatch,
    ScannerVerifyIn,
)
from .security import generate_token, hash_token, verify_token


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


@app.on_event("startup")
def startup() -> None:
    init_db()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def current_admin(x_api_key: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header is required")

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

    raise HTTPException(status_code=401, detail="Invalid API key")


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


def require_company_item(db, table: str, company_id: int, item_id: int) -> dict[str, Any]:
    row = db.execute(f"SELECT * FROM {table} WHERE id = ? AND company_id = ?", (item_id, company_id)).fetchone()
    item = row_to_dict(row)
    if not item:
        raise HTTPException(status_code=404, detail=f"{table[:-1]} not found")
    return item


def enrich_rule(rule: dict[str, Any]) -> dict[str, Any]:
    import json

    rule["allowed_methods"] = json_list(rule.get("allowed_methods"))
    rule["schedule"] = json.loads(rule["schedule_json"]) if rule.get("schedule_json") else None
    rule["is_active"] = bool(rule["is_active"])
    return rule


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
) -> int:
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO access_events (
                company_id, employee_id, room_id, scanner_id, method, direction,
                decision, reason, confidence, qr_pass_id, raw_subject
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            elif direction == "exit":
                db.execute(
                    "DELETE FROM room_occupancy WHERE company_id = ? AND room_id = ? AND employee_id = ?",
                    (company_id, room_id, employee_id),
                )
        return event_id


def check_access(company_id: int, employee_id: int, room_id: int, method: str) -> tuple[bool, str]:
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
        if method not in json_list(rule["allowed_methods"]):
            return False, "method_not_allowed_for_employee"
        if rule["valid_from"] and rule["valid_from"] > now:
            return False, "access_rule_not_started"
        if rule["valid_until"] and rule["valid_until"] < now:
            return False, "access_rule_expired"
    return True, "access_granted"


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "face_engine_available": face.face_engine_available()}


@app.post("/api/v1/setup/company", status_code=201)
def setup_company(payload: CompanySetupIn, x_bootstrap_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    if x_bootstrap_token != config.BOOTSTRAP_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid bootstrap token")

    admin_key = generate_token("skud_admin")
    with connect() as db:
        cursor = db.execute("INSERT INTO companies (name, slug) VALUES (?, ?)", (payload.name, payload.slug))
        company_id = int(cursor.lastrowid)
        db.execute(
            "INSERT INTO api_keys (company_id, name, key_hash, role) VALUES (?, ?, ?, 'admin')",
            (company_id, payload.admin_key_name, hash_token(admin_key)),
        )
        company = row_to_dict(db.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone())

    return {"company": company, "admin_api_key": admin_key}


@app.get("/api/v1/company")
def get_company(admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    return {"id": admin["company_id"], "name": admin["company_name"], "slug": admin["company_slug"]}


@app.post("/api/v1/employees", status_code=201)
def create_employee(payload: EmployeeIn, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO employees (company_id, external_id, full_name, position, email, phone, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (admin["company_id"], payload.external_id, payload.full_name, payload.position, payload.email, payload.phone, payload.status),
        )
        return require_company_item(db, "employees", admin["company_id"], int(cursor.lastrowid))


@app.get("/api/v1/employees")
def list_employees(admin: Annotated[dict[str, Any], Depends(current_admin)]) -> list[dict[str, Any]]:
    with connect() as db:
        return rows_to_dicts(db.execute("SELECT * FROM employees WHERE company_id = ? ORDER BY full_name", (admin["company_id"],)).fetchall())


@app.get("/api/v1/employees/{employee_id}")
def get_employee(employee_id: int, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        return require_company_item(db, "employees", admin["company_id"], employee_id)


@app.patch("/api/v1/employees/{employee_id}")
def update_employee(employee_id: int, payload: EmployeePatch, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if updates:
        columns = ", ".join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values()) + [employee_id, admin["company_id"]]
        with connect() as db:
            db.execute(f"UPDATE employees SET {columns}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?", values)
            return require_company_item(db, "employees", admin["company_id"], employee_id)
    return get_employee(employee_id, admin)


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
        require_company_item(db, "employees", admin["company_id"], employee_id)
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
    return {"id": pass_id, "payload": qr_payload, "qr_png_path": str(qr_path), "expires_at": expires_at}


@app.post("/api/v1/rooms", status_code=201)
def create_room(payload: RoomIn, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO rooms (company_id, name, code, description, capacity, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (admin["company_id"], payload.name, payload.code, payload.description, payload.capacity, payload.status),
        )
        return require_company_item(db, "rooms", admin["company_id"], int(cursor.lastrowid))


@app.get("/api/v1/rooms")
def list_rooms(admin: Annotated[dict[str, Any], Depends(current_admin)]) -> list[dict[str, Any]]:
    with connect() as db:
        return rows_to_dicts(db.execute("SELECT * FROM rooms WHERE company_id = ? ORDER BY name", (admin["company_id"],)).fetchall())


@app.patch("/api/v1/rooms/{room_id}")
def update_room(room_id: int, payload: RoomPatch, admin: Annotated[dict[str, Any], Depends(current_admin)]) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if updates:
        columns = ", ".join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values()) + [room_id, admin["company_id"]]
        with connect() as db:
            db.execute(f"UPDATE rooms SET {columns}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?", values)
            return require_company_item(db, "rooms", admin["company_id"], room_id)
    with connect() as db:
        return require_company_item(db, "rooms", admin["company_id"], room_id)


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
            db.execute(f"UPDATE access_rules SET {columns}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?", values)
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


@app.post("/api/v1/scanner/verify")
def scanner_verify(payload: ScannerVerifyIn, scanner: Annotated[dict[str, Any], Depends(current_scanner)]) -> dict[str, Any]:
    scanner_methods = json_list(scanner["allowed_methods"])
    employee_id = None
    confidence = None
    qr_pass_id = None
    reason = "unknown"

    if payload.method not in scanner_methods:
        event_id = log_event(
            company_id=scanner["company_id"],
            employee_id=None,
            room_id=scanner["room_id"],
            scanner_id=scanner["id"],
            method=payload.method,
            direction=scanner["direction"],
            decision="denied",
            reason="method_not_allowed_for_scanner",
            raw_subject=payload.raw_subject,
        )
        return {"decision": "denied", "reason": "method_not_allowed_for_scanner", "event_id": event_id}

    if payload.method == "qr":
        if not payload.qr_payload:
            reason = "qr_payload_required"
        else:
            ok, data, reason = verify_qr_payload(payload.qr_payload)
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
                elif data["subject_type"] != "employee":
                    reason = "unsupported_qr_subject"
                else:
                    employee_id = int(data["subject_id"])
                    reason = "identified_by_qr"
    elif payload.method == "face":
        if not payload.face_image_base64:
            reason = "face_image_required"
        else:
            employee_id, confidence, reason = face.recognize_base64(scanner["company_id"], payload.face_image_base64)

    if employee_id:
        granted, access_reason = check_access(scanner["company_id"], employee_id, scanner["room_id"], payload.method)
        decision = "granted" if granted else "denied"
        reason = access_reason
    else:
        decision = "denied"

    event_id = log_event(
        company_id=scanner["company_id"],
        employee_id=employee_id,
        room_id=scanner["room_id"],
        scanner_id=scanner["id"],
        method=payload.method,
        direction=scanner["direction"],
        decision=decision,
        reason=reason,
        confidence=confidence,
        qr_pass_id=qr_pass_id,
        raw_subject=payload.raw_subject,
    )
    response: dict[str, Any] = {
        "decision": decision,
        "reason": reason,
        "event_id": event_id,
        "unlock_seconds": config.UNLOCK_SECONDS if decision == "granted" else 0,
        "employee_id": employee_id,
        "room_id": scanner["room_id"],
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
    limit: int = Query(100, le=500),
) -> list[dict[str, Any]]:
    query = "SELECT * FROM access_events WHERE company_id = ?"
    values: list[Any] = [admin["company_id"]]
    for field, value in (("employee_id", employee_id), ("room_id", room_id), ("decision", decision)):
        if value is not None:
            query += f" AND {field} = ?"
            values.append(value)
    query += " ORDER BY occurred_at DESC LIMIT ?"
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
