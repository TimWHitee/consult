from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SKUD_API_DB_PATH", "skud_api.db")
os.environ.setdefault("SKUD_STORAGE_DIR", "storage")

from api.database import connect, dump_json, init_db
from api.qr import create_qr_payload, default_expiration, new_nonce, save_qr_png
from api.security import hash_password, hash_token


DB_PATH = ROOT / "skud_api.db"
STORAGE_DIR = ROOT / "storage"
CREATOR_DB = ROOT / "creator" / "skud.db"
SCANNER_DB = ROOT / "scanner" / "skud.db"
SMOKE_DBS = [
    STORAGE_DIR / "smoke_room_methods.db",
    STORAGE_DIR / "smoke_room_methods2.db",
    STORAGE_DIR / "smoke" / "09f9d9cfe3db43dabee80d228bb3bf39.db",
    STORAGE_DIR / "smoke" / "us15_us21_8b10336c496946aea9d036524516e064.db",
    STORAGE_DIR / "smoke" / "us15_us21_final_d7feb86328f24303a4ecdf442413312f.db",
]

DEMO_ADMIN_API_KEY = "skud_admin_demo_key"
DEMO_ADMIN_LOGIN = "admin"
DEMO_ADMIN_PASSWORD = "admin123"
DEMO_COMPANY_SLUG = "demo"

SCANNER_TOKENS = {
    "main_entry": "skud_scanner_demo_main_entry",
    "main_exit": "skud_scanner_demo_main_exit",
    "server_room": "skud_scanner_demo_server_room",
    "meeting_qr": "skud_scanner_demo_meeting_qr",
    "parking_card": "skud_scanner_demo_parking_card",
}


def now(offset_minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)).replace(microsecond=0).isoformat()


def sqlite_now(offset_minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)).strftime("%Y-%m-%d %H:%M:%S")


def reset_files() -> None:
    for path in [DB_PATH, *SMOKE_DBS]:
        if path.exists():
            path.unlink()
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def insert_employee(db: sqlite3.Connection, company_id: int, data: dict[str, object]) -> int:
    cursor = db.execute(
        """
        INSERT INTO employees (
            company_id, external_id, full_name, position, email, phone,
            status, role, access_level, pass_status, pass_block_reason, fired_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            company_id,
            data.get("external_id"),
            data["full_name"],
            data.get("position"),
            data.get("email"),
            data.get("phone"),
            data.get("status", "active"),
            data.get("role", "employee"),
            data.get("access_level", 1),
            data.get("pass_status", "active"),
            data.get("pass_block_reason"),
            data.get("fired_at"),
        ),
    )
    employee_id = int(cursor.lastrowid)
    if data.get("login") and data.get("password"):
        db.execute(
            """
            INSERT INTO employee_credentials (company_id, employee_id, login, password_hash)
            VALUES (?, ?, ?, ?)
            """,
            (company_id, employee_id, str(data["login"]).lower(), hash_password(str(data["password"]))),
        )
    return employee_id


def insert_event(
    db: sqlite3.Connection,
    company_id: int,
    employee_id: int | None,
    room_id: int,
    scanner_id: int,
    method: str,
    direction: str,
    decision: str,
    reason: str,
    occurred_at: str,
    *,
    confidence: float | None = None,
    raw_subject: str | None = None,
    subject_type: str = "employee",
    guest_id: int | None = None,
) -> int:
    cursor = db.execute(
        """
        INSERT INTO access_events (
            company_id, employee_id, room_id, scanner_id, method, direction,
            decision, reason, confidence, raw_subject, subject_type, guest_id, occurred_at
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
            raw_subject,
            subject_type,
            guest_id,
            occurred_at,
        ),
    )
    return int(cursor.lastrowid)


def seed_api_db() -> None:
    init_db()
    with connect() as db:
        company_id = int(
            db.execute("INSERT INTO companies (name, slug) VALUES (?, ?)", ("Demo SKUD Corporation", DEMO_COMPANY_SLUG)).lastrowid
        )
        db.execute(
            "INSERT INTO api_keys (company_id, name, key_hash, role) VALUES (?, ?, ?, 'admin')",
            (company_id, "Demo admin API key", hash_token(DEMO_ADMIN_API_KEY)),
        )
        db.execute(
            "INSERT INTO company_settings (company_id, key, value) VALUES (?, 'anti_passback_minutes', '15')",
            (company_id,),
        )

        employees = [
            {
                "external_id": "ADM-001",
                "full_name": "Анна Смирнова",
                "position": "HR manager / admin",
                "email": "anna.smirnova@demo.local",
                "phone": "+7 900 100-00-01",
                "role": "hr",
                "access_level": 3,
                "login": DEMO_ADMIN_LOGIN,
                "password": DEMO_ADMIN_PASSWORD,
            },
            {
                "external_id": "SEC-007",
                "full_name": "Игорь Волков",
                "position": "Security officer",
                "email": "igor.volkov@demo.local",
                "phone": "+7 900 100-00-02",
                "role": "security",
                "access_level": 3,
                "login": "security",
                "password": "security123",
            },
            {
                "external_id": "PASS-011",
                "full_name": "Мария Орлова",
                "position": "Pass office specialist",
                "email": "maria.orlova@demo.local",
                "role": "pass_office",
                "access_level": 2,
                "login": "passoffice",
                "password": "pass123",
            },
            {
                "external_id": "EMP-101",
                "full_name": "Дмитрий Козлов",
                "position": "Backend engineer",
                "email": "dmitry.kozlov@demo.local",
                "role": "employee",
                "access_level": 2,
                "login": "dmitry",
                "password": "demo123",
            },
            {
                "external_id": "EMP-102",
                "full_name": "Елена Морозова",
                "position": "Product manager",
                "email": "elena.morozova@demo.local",
                "role": "employee",
                "access_level": 2,
                "login": "elena",
                "password": "demo123",
            },
            {
                "external_id": "EMP-103",
                "full_name": "Павел Никитин",
                "position": "Finance analyst",
                "email": "pavel.nikitin@demo.local",
                "role": "employee",
                "access_level": 2,
                "login": "pavel",
                "password": "demo123",
            },
            {
                "external_id": "CLEAN-01",
                "full_name": "Ольга Белова",
                "position": "Cleaning contractor",
                "role": "cleaner",
                "access_level": 1,
                "login": "cleaner",
                "password": "demo123",
            },
            {
                "external_id": "EMP-LOCK",
                "full_name": "Сергей Фомин",
                "position": "Suspended contractor",
                "status": "suspended",
                "role": "employee",
                "access_level": 1,
            },
            {
                "external_id": "EMP-BLOCK",
                "full_name": "Наталья Громова",
                "position": "Lost pass example",
                "role": "employee",
                "access_level": 1,
                "pass_status": "blocked",
                "pass_block_reason": "lost_card",
            },
            {
                "external_id": "EMP-FIRED",
                "full_name": "Алексей Старостин",
                "position": "Former employee",
                "status": "fired",
                "role": "employee",
                "access_level": 1,
                "pass_status": "blocked",
                "pass_block_reason": "employee_fired",
                "fired_at": sqlite_now(-60 * 24 * 12),
            },
        ]
        employee_ids = {item["external_id"]: insert_employee(db, company_id, item) for item in employees}

        rooms = [
            ("Lobby", "LOBBY", "Главный вход, турникет", 80, 1, ["qr", "card", "face"], 0, "active"),
            ("Open Space 4F", "OPEN-4F", "Открытая рабочая зона", 120, 1, ["card", "face"], 0, "active"),
            ("Meeting Room A", "MEET-A", "Переговорная для гостей и сотрудников", 12, 1, ["qr"], 0, "active"),
            ("Finance Office", "FIN-2", "Финансовый отдел", 14, 2, ["card", "face"], 0, "active"),
            ("R&D Lab", "LAB-RD", "Лаборатория с оборудованием", 20, 2, ["card", "face"], 0, "active"),
            ("Server Room", "SRV-1", "Критичная зона, только лицо и allowlist", 4, 3, ["face"], 1, "active"),
            ("Archive", "ARCH-1", "Архив договоров", 6, 2, ["face"], 1, "active"),
            ("Parking", "PARK-P1", "Парковка, вход по карте", 200, 1, ["card"], 0, "active"),
            ("Closed Storage", "OLD-STORE", "Отключенная тестовая комната", 10, 2, ["card"], 0, "inactive"),
        ]
        room_ids: dict[str, int] = {}
        for name, code, description, capacity, level, methods, biometric, status in rooms:
            room_ids[code] = int(
                db.execute(
                    """
                    INSERT INTO rooms (
                        company_id, name, code, description, capacity,
                        access_level, allowed_methods, biometric_only, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (company_id, name, code, description, capacity, level, dump_json(methods), biometric, status),
                ).lastrowid
            )

        scanner_defs = [
            ("main_entry", room_ids["LOBBY"], "Main entrance entry", "entry", ["qr", "card", "face"], -2),
            ("main_exit", room_ids["LOBBY"], "Main entrance exit", "exit", ["qr", "card", "face"], -1),
            ("server_room", room_ids["SRV-1"], "Server room Face ID", "entry", ["face"], -4),
            ("meeting_qr", room_ids["MEET-A"], "Meeting room QR kiosk", "entry", ["qr"], -8),
            ("parking_card", room_ids["PARK-P1"], "Parking card gate", "entry", ["card"], -6),
        ]
        scanner_ids: dict[str, int] = {}
        for key, room_id, name, direction, methods, last_seen_offset in scanner_defs:
            scanner_ids[key] = int(
                db.execute(
                    """
                    INSERT INTO scanners (company_id, room_id, name, direction, allowed_methods, token_hash, status, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (company_id, room_id, name, direction, dump_json(methods), hash_token(SCANNER_TOKENS[key]), sqlite_now(last_seen_offset)),
                ).lastrowid
            )

        all_active_rooms = [room_ids[code] for code in ["LOBBY", "OPEN-4F", "MEET-A", "FIN-2", "LAB-RD", "PARK-P1"]]
        restricted_rooms = [room_ids["FIN-2"], room_ids["LAB-RD"], room_ids["ARCH-1"]]
        access_map = {
            "ADM-001": all_active_rooms + [room_ids["SRV-1"], room_ids["ARCH-1"]],
            "SEC-007": all_active_rooms + [room_ids["SRV-1"], room_ids["ARCH-1"]],
            "PASS-011": [room_ids["LOBBY"], room_ids["MEET-A"], room_ids["PARK-P1"]],
            "EMP-101": [room_ids["LOBBY"], room_ids["OPEN-4F"], room_ids["MEET-A"], room_ids["LAB-RD"], room_ids["PARK-P1"]],
            "EMP-102": [room_ids["LOBBY"], room_ids["OPEN-4F"], room_ids["MEET-A"], room_ids["PARK-P1"]],
            "EMP-103": [room_ids["LOBBY"], room_ids["OPEN-4F"], room_ids["FIN-2"], room_ids["PARK-P1"]],
            "CLEAN-01": [room_ids["LOBBY"], room_ids["OPEN-4F"], room_ids["MEET-A"]],
        }
        for external_id, access_rooms in access_map.items():
            for room_id in access_rooms:
                schedule = None
                if external_id == "CLEAN-01":
                    schedule = {"weekdays": [0, 1, 2, 3, 4], "start_time": "18:00", "end_time": "22:00"}
                room_methods = db.execute("SELECT allowed_methods FROM rooms WHERE id = ?", (room_id,)).fetchone()["allowed_methods"]
                db.execute(
                    """
                    INSERT INTO access_rules (
                        company_id, employee_id, room_id, allowed_methods, valid_from, valid_until, schedule_json, is_active
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        company_id,
                        employee_ids[external_id],
                        room_id,
                        room_methods,
                        now(-60 * 24 * 30),
                        now(60 * 24 * 180),
                        dump_json(schedule) if schedule else None,
                    ),
                )

        for external_id in ["ADM-001", "SEC-007"]:
            db.execute(
                "INSERT INTO room_level3_allowlist (company_id, room_id, employee_id) VALUES (?, ?, ?)",
                (company_id, room_ids["SRV-1"], employee_ids[external_id]),
            )

        db.execute(
            """
            INSERT INTO room_limit_overrides (company_id, room_id, limit_value, valid_from, valid_until, reason)
            VALUES (?, ?, 2, ?, ?, 'Maintenance window demo')
            """,
            (company_id, room_ids["SRV-1"], now(-60), now(60 * 8)),
        )

        qr_dir = STORAGE_DIR / "qr" / str(company_id)
        qr_dir.mkdir(parents=True, exist_ok=True)
        for idx, external_id in enumerate(["ADM-001", "EMP-101", "EMP-102", "EMP-103"]):
            nonce = new_nonce()
            expires_at = default_expiration(24 + idx)
            pass_id = int(
                db.execute(
                    "INSERT INTO qr_passes (company_id, subject_type, subject_id, nonce, expires_at) VALUES (?, 'employee', ?, ?, ?)",
                    (company_id, employee_ids[external_id], nonce, expires_at),
                ).lastrowid
            )
            payload = create_qr_payload(
                company_id=company_id,
                qr_pass_id=pass_id,
                subject_type="employee",
                subject_id=employee_ids[external_id],
                expires_at=expires_at,
                nonce=nonce,
            )
            save_qr_png(payload, str(qr_dir / f"employee_{employee_ids[external_id]}_pass_{pass_id}.png"))

        guest_id = int(
            db.execute(
                """
                INSERT INTO guests (
                    company_id, host_employee_id, room_id, full_name, document_number,
                    visit_starts_at, visit_ends_at, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    company_id,
                    employee_ids["EMP-102"],
                    room_ids["MEET-A"],
                    "Виктория Романова",
                    "4510 123456",
                    now(-90),
                    now(60 * 5),
                ),
            ).lastrowid
        )
        guest_nonce = new_nonce()
        guest_pass_id = int(
            db.execute(
                "INSERT INTO qr_passes (company_id, subject_type, subject_id, nonce, expires_at) VALUES (?, 'guest', ?, ?, ?)",
                (company_id, guest_id, guest_nonce, now(60 * 5)),
            ).lastrowid
        )
        guest_payload = create_qr_payload(
            company_id=company_id,
            qr_pass_id=guest_pass_id,
            subject_type="guest",
            subject_id=guest_id,
            expires_at=now(60 * 5),
            nonce=guest_nonce,
        )
        save_qr_png(guest_payload, str(qr_dir / f"guest_{guest_id}_pass_{guest_pass_id}.png"))

        face_dir = STORAGE_DIR / "faces" / str(company_id)
        face_dir.mkdir(parents=True, exist_ok=True)
        for idx, external_id in enumerate(["ADM-001", "SEC-007", "EMP-101", "EMP-102", "EMP-103"]):
            fake_photo = face_dir / f"{employee_ids[external_id]}_demo.txt"
            fake_photo.write_text("synthetic face placeholder\n", encoding="utf-8")
            photo_id = int(
                db.execute(
                    "INSERT INTO face_photos (company_id, employee_id, file_path, quality_status) VALUES (?, ?, ?, 'synthetic')",
                    (company_id, employee_ids[external_id], str(fake_photo)),
                ).lastrowid
            )
            embedding = [round(((idx + 1) * (n + 3) % 17) / 17, 6) for n in range(16)]
            db.execute(
                "INSERT INTO face_embeddings (company_id, employee_id, source_photo_id, embedding_json) VALUES (?, ?, ?, ?)",
                (company_id, employee_ids[external_id], photo_id, dump_json(embedding)),
            )

        demo_events = [
            ("EMP-101", "LOBBY", "main_entry", "card", "entry", "granted", "access_granted", -430, None),
            ("EMP-101", "OPEN-4F", "main_entry", "face", "entry", "granted", "access_granted_level1", -410, 0.81),
            ("EMP-102", "LOBBY", "main_entry", "qr", "entry", "granted", "access_granted_level1", -380, None),
            ("EMP-103", "FIN-2", "main_entry", "card", "entry", "granted", "access_granted", -330, None),
            ("EMP-LOCK", "LOBBY", "main_entry", "card", "entry", "denied", "employee_not_active", -300, None),
            ("EMP-BLOCK", "LOBBY", "main_entry", "qr", "entry", "denied", "employee_pass_blocked", -280, None),
            ("CLEAN-01", "OPEN-4F", "main_entry", "card", "entry", "denied", "access_schedule_denied", -260, None),
            ("SEC-007", "SRV-1", "server_room", "face", "entry", "granted", "access_granted", -220, 0.93),
            ("EMP-101", "SRV-1", "server_room", "face", "entry", "denied", "level3_allowlist_required", -190, 0.75),
            ("EMP-102", "LOBBY", "main_exit", "card", "exit", "granted", "access_granted_level1", -170, None),
            ("EMP-102", "LOBBY", "main_entry", "card", "entry", "denied", "anti_passback_interval_not_elapsed", -165, None),
            ("EMP-101", "MEET-A", "meeting_qr", "qr", "entry", "granted", "access_granted_level1", -90, None),
            ("EMP-103", "PARK-P1", "parking_card", "card", "entry", "granted", "access_granted_level1", -70, None),
            ("ADM-001", "SRV-1", "server_room", "face", "entry", "granted", "access_granted", -45, 0.95),
            ("EMP-101", "OPEN-4F", "main_exit", "card", "exit", "granted", "access_granted_level1", -20, None),
        ]
        event_ids: list[int] = []
        for external_id, room_code, scanner_key, method, direction, decision, reason, offset, confidence in demo_events:
            event_ids.append(
                insert_event(
                    db,
                    company_id,
                    employee_ids[external_id],
                    room_ids[room_code],
                    scanner_ids[scanner_key],
                    method,
                    direction,
                    decision,
                    reason,
                    sqlite_now(offset),
                    confidence=confidence,
                    raw_subject=external_id,
                )
            )

        insert_event(
            db,
            company_id,
            employee_ids["EMP-102"],
            room_ids["MEET-A"],
            scanner_ids["meeting_qr"],
            "qr",
            "entry",
            "granted",
            "guest_access_granted",
            sqlite_now(-55),
            raw_subject="Виктория Романова",
            subject_type="guest",
            guest_id=guest_id,
        )

        for external_id, room_code, entered_offset, last_event_idx in [
            ("EMP-101", "MEET-A", -90, 11),
            ("EMP-103", "FIN-2", -330, 3),
            ("SEC-007", "SRV-1", -220, 7),
            ("ADM-001", "SRV-1", -45, 13),
            ("EMP-103", "PARK-P1", -70, 12),
        ]:
            db.execute(
                """
                INSERT INTO room_occupancy (company_id, room_id, employee_id, entered_at, last_event_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (company_id, room_ids[room_code], employee_ids[external_id], sqlite_now(entered_offset), event_ids[last_event_idx]),
            )
            db.execute(
                """
                INSERT INTO employee_presence (company_id, employee_id, status, last_entry_at, last_event_id)
                VALUES (?, ?, 'in_office', ?, ?)
                ON CONFLICT(company_id, employee_id) DO UPDATE SET
                    status = excluded.status,
                    last_entry_at = excluded.last_entry_at,
                    last_event_id = excluded.last_event_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (company_id, employee_ids[external_id], sqlite_now(entered_offset), event_ids[last_event_idx]),
            )

        for external_id in ["EMP-102", "EMP-101"]:
            db.execute(
                """
                INSERT INTO employee_presence (company_id, employee_id, status, last_entry_at, last_exit_at, last_event_id)
                VALUES (?, ?, 'out_office', ?, ?, ?)
                ON CONFLICT(company_id, employee_id) DO UPDATE SET
                    status = excluded.status,
                    last_entry_at = excluded.last_entry_at,
                    last_exit_at = excluded.last_exit_at,
                    last_event_id = excluded.last_event_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (company_id, employee_ids[external_id], sqlite_now(-380), sqlite_now(-20), event_ids[-1]),
            )

        db.execute(
            """
            INSERT INTO security_alerts (company_id, employee_id, room_id, access_event_id, severity, reason, permissions_json)
            VALUES (?, ?, ?, ?, 'high', 'level3_allowlist_required', ?)
            """,
            (
                company_id,
                employee_ids["EMP-101"],
                room_ids["SRV-1"],
                event_ids[8],
                dump_json({"employee": "EMP-101", "room": "Server Room", "allowed": False}),
            ),
        )

        notifications = [
            ("SEC-007", "security_alert", "Anti-passback blocked", "EMP-102 tried to re-enter before 15 minutes."),
            ("EMP-101", "access_change", "Access updated", "You now have access to R&D Lab and Meeting Room A."),
            ("EMP-102", "guest_granted", "Guest passed", "Виктория Романова entered Meeting Room A."),
            ("EMP-BLOCK", "pass_blocked", "Pass blocked", "Your pass is blocked because it was reported lost."),
        ]
        for external_id, type_, title, body in notifications:
            db.execute(
                """
                INSERT INTO notifications (company_id, employee_id, type, title, body, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (company_id, employee_ids[external_id], type_, title, body, dump_json({"demo": True})),
            )

        change_logs = [
            ("access_rule_upserted", "EMP-101", "LAB-RD", {"source": "bulk grant"}),
            ("level3_allowlist_added", "SEC-007", "SRV-1", {"reason": "security officer"}),
            ("employee_pass_blocked", "EMP-BLOCK", None, {"reason": "lost_card"}),
            ("room_limit_override_created", None, "SRV-1", {"limit": 2}),
        ]
        for action, external_id, room_code, details in change_logs:
            db.execute(
                """
                INSERT INTO access_change_logs (company_id, employee_id, room_id, action, details_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    employee_ids[external_id] if external_id else None,
                    room_ids[room_code] if room_code else None,
                    action,
                    dump_json(details),
                ),
            )


def recreate_legacy_creator_db() -> None:
    CREATOR_DB.parent.mkdir(parents=True, exist_ok=True)
    if CREATOR_DB.exists():
        CREATOR_DB.unlink()
    con = sqlite3.connect(CREATOR_DB)
    con.executescript(
        """
        CREATE TABLE whitelist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL
        );
        CREATE TABLE employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            phone TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE invite_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            employee_id INTEGER REFERENCES employees(tg_id),
            guest_fio TEXT NOT NULL,
            visit_date TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE guests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fio TEXT NOT NULL,
            passport TEXT NOT NULL,
            qr_hash TEXT NOT NULL,
            invited_by INTEGER REFERENCES employees(tg_id),
            visit_date TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            tg_id INTEGER,
            details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    con.executemany("INSERT INTO whitelist (username) VALUES (?)", [("demo_admin",), ("security_demo",)])
    con.executemany(
        "INSERT INTO employees (tg_id, full_name, phone) VALUES (?, ?, ?)",
        [
            (100101, "Анна Смирнова", "+7 900 100-00-01"),
            (100102, "Дмитрий Козлов", "+7 900 100-00-04"),
            (100103, "Елена Морозова", "+7 900 100-00-05"),
        ],
    )
    con.executemany(
        "INSERT INTO invite_codes (code, employee_id, guest_fio, visit_date) VALUES (?, ?, ?, ?)",
        [
            ("INV-DEMO-001", 100103, "Виктория Романова", datetime.now().date().isoformat()),
            ("INV-DEMO-002", 100102, "Олег Васильев", (datetime.now() + timedelta(days=1)).date().isoformat()),
        ],
    )
    con.executemany(
        "INSERT INTO guests (fio, passport, qr_hash, invited_by, visit_date) VALUES (?, ?, ?, ?, ?)",
        [
            ("Виктория Романова", "4510 123456", "demo_guest_qr_hash_1", 100103, datetime.now().date().isoformat()),
            ("Олег Васильев", "4510 654321", "demo_guest_qr_hash_2", 100102, (datetime.now() + timedelta(days=1)).date().isoformat()),
        ],
    )
    con.executemany(
        "INSERT INTO logs (event_type, tg_id, details) VALUES (?, ?, ?)",
        [
            ("employee_registered", 100101, "Demo admin employee"),
            ("guest_invited", 100103, "Виктория Романова"),
            ("qr_generated", 100102, "INV-DEMO-002"),
        ],
    )
    con.commit()
    con.close()


def recreate_legacy_scanner_db() -> None:
    SCANNER_DB.parent.mkdir(parents=True, exist_ok=True)
    if SCANNER_DB.exists():
        SCANNER_DB.unlink()
    con = sqlite3.connect(SCANNER_DB)
    con.executescript(
        """
        CREATE TABLE logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            tg_id INTEGER,
            details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    con.executemany(
        "INSERT INTO logs (event_type, tg_id, details) VALUES (?, ?, ?)",
        [
            ("qr_granted", 100102, "Lobby entry granted"),
            ("face_denied", 100102, "Server Room denied: level3_allowlist_required"),
            ("card_denied", 100105, "Anti-passback interval not elapsed"),
        ],
    )
    con.commit()
    con.close()


def main() -> None:
    reset_files()
    seed_api_db()
    recreate_legacy_creator_db()
    recreate_legacy_scanner_db()
    print("Demo data seeded")
    print(f"Company slug: {DEMO_COMPANY_SLUG}")
    print(f"Admin login: {DEMO_ADMIN_LOGIN}")
    print(f"Admin password: {DEMO_ADMIN_PASSWORD}")
    print(f"Admin API key: {DEMO_ADMIN_API_KEY}")
    print("Scanner tokens:")
    for key, token in SCANNER_TOKENS.items():
        print(f"  {key}: {token}")


if __name__ == "__main__":
    main()
