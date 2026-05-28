import aiosqlite
from database.db import get_db


# ──────────────────────────── WHITELIST ────────────────────────────

async def add_to_whitelist(username: str):
    """Добавить username в whitelist (без @)."""
    username = username.lstrip("@").lower()
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO whitelist (username) VALUES (?)", (username,)
        )
        await db.commit()


async def remove_from_whitelist(username: str):
    username = username.lstrip("@").lower()
    async with get_db() as db:
        await db.execute("DELETE FROM whitelist WHERE username = ?", (username,))
        await db.commit()


async def is_whitelisted(username: str) -> bool:
    if not username:
        return False
    username = username.lstrip("@").lower()
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT 1 FROM whitelist WHERE username = ?", (username,)
        )
        return await cursor.fetchone() is not None


# ──────────────────────────── EMPLOYEES ────────────────────────────

async def get_employee(tg_id: int) -> aiosqlite.Row | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM employees WHERE tg_id = ?", (tg_id,)
        )
        return await cursor.fetchone()


async def add_employee(tg_id: int, full_name: str, phone: str | None = None):
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO employees (tg_id, full_name, phone) VALUES (?, ?, ?)",
            (tg_id, full_name, phone),
        )
        await db.commit()


async def is_employee(tg_id: int) -> bool:
    return await get_employee(tg_id) is not None


# ──────────────────────────── GUESTS ───────────────────────────────

async def add_guest(
    fio: str,
    passport: str,
    qr_hash: str,
    invited_by: int,
    visit_date: str,
):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO guests (fio, passport, qr_hash, invited_by, visit_date)
               VALUES (?, ?, ?, ?, ?)""",
            (fio, passport, qr_hash, invited_by, visit_date),
        )
        await db.commit()


# ──────────────────────────── INVITE CODES ─────────────────────────

async def save_invite_code(
    code: str, employee_id: int, guest_fio: str, visit_date: str
):
    async with get_db() as db:
        await db.execute(
            """INSERT OR REPLACE INTO invite_codes (code, employee_id, guest_fio, visit_date)
               VALUES (?, ?, ?, ?)""",
            (code, employee_id, guest_fio, visit_date),
        )
        await db.commit()


async def get_invite_code(code: str) -> aiosqlite.Row | None:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM invite_codes WHERE code = ? AND used = 0", (code,)
        )
        return await cursor.fetchone()


async def mark_invite_used(code: str):
    async with get_db() as db:
        await db.execute(
            "UPDATE invite_codes SET used = 1 WHERE code = ?", (code,)
        )
        await db.commit()


# ──────────────────────────── LOGS ─────────────────────────────────

async def add_log(event_type: str, tg_id: int | None = None, details: str = ""):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO logs (event_type, tg_id, details) VALUES (?, ?, ?)",
            (event_type, tg_id, details),
        )
        await db.commit()
