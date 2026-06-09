import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .config import config


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'admin',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    external_id TEXT,
    full_name TEXT NOT NULL,
    position TEXT,
    email TEXT,
    phone TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, external_id)
);

CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    code TEXT NOT NULL,
    description TEXT,
    capacity INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, code)
);

CREATE TABLE IF NOT EXISTS access_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    allowed_methods TEXT NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    schedule_json TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, employee_id, room_id)
);

CREATE TABLE IF NOT EXISTS scanners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'entry',
    allowed_methods TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    last_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS face_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    quality_status TEXT NOT NULL DEFAULT 'stored',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS face_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    source_photo_id INTEGER REFERENCES face_photos(id) ON DELETE SET NULL,
    embedding_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS qr_passes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    subject_type TEXT NOT NULL,
    subject_id INTEGER NOT NULL,
    nonce TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS access_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    room_id INTEGER REFERENCES rooms(id) ON DELETE SET NULL,
    scanner_id INTEGER REFERENCES scanners(id) ON DELETE SET NULL,
    method TEXT NOT NULL,
    direction TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    confidence REAL,
    qr_pass_id INTEGER REFERENCES qr_passes(id) ON DELETE SET NULL,
    raw_subject TEXT,
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS room_occupancy (
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    entered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_event_id INTEGER REFERENCES access_events(id) ON DELETE SET NULL,
    PRIMARY KEY(company_id, room_id, employee_id)
);
"""


def init_db() -> None:
    Path(config.STORAGE_DIR).mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript(SCHEMA)


@contextmanager
def connect():
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) for row in rows if row is not None]


def json_list(value: str | None) -> list[str]:
    if not value:
        return []
    loaded = json.loads(value)
    return loaded if isinstance(loaded, list) else []


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
