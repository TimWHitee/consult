import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from database.db import init_db
from middlewares.auth import AuthMiddleware
from handlers import common, employee, guest, face, admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    await init_db()
    logger.info("✅ Database initialized")

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(AuthMiddleware())

    # Порядок важен: admin первым, чтобы команды не перехватывались другими роутерами
    dp.include_router(admin.router)
    dp.include_router(common.router)
    dp.include_router(employee.router)
    dp.include_router(guest.router)
    dp.include_router(face.router)

    logger.info("🤖 Bot started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
