import aiosqlite
from database.db import get_db


async def add_log(event_type: str, tg_id: int | None = None, details: str = ""):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO logs (event_type, tg_id, details) VALUES (?, ?, ?)",
            (event_type, tg_id, details),
        )
        await db.commit()
