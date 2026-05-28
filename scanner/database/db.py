import aiosqlite
from config import config


def get_db() -> aiosqlite.Connection:
    return aiosqlite.connect(config.DB_PATH)


async def init_db():
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT NOT NULL,
                tg_id       INTEGER,
                details     TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()
