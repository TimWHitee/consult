import aiosqlite
from config import config


def get_db() -> aiosqlite.Connection:
    """Возвращает контекстный менеджер подключения к БД."""
    return aiosqlite.connect(config.DB_PATH)


async def init_db():
    """Создаёт таблицы при первом запуске."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS whitelist (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS employees (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id       INTEGER UNIQUE NOT NULL,
                full_name   TEXT NOT NULL,
                phone       TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS invite_codes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT UNIQUE NOT NULL,
                employee_id INTEGER REFERENCES employees(tg_id),
                guest_fio   TEXT NOT NULL,
                visit_date  TEXT NOT NULL,
                used        INTEGER DEFAULT 0,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS guests (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fio         TEXT NOT NULL,
                passport    TEXT NOT NULL,
                qr_hash     TEXT NOT NULL,
                invited_by  INTEGER REFERENCES employees(tg_id),
                visit_date  TEXT NOT NULL,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT NOT NULL,
                tg_id       INTEGER,
                details     TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()
