from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from database.models import add_to_whitelist, remove_from_whitelist, add_log

router = Router()

ADMIN_ID = config.ADMIN_TG_ID


def is_admin(tg_id: int) -> bool:
    return tg_id == ADMIN_ID


# ──────────────────── Команды админа ─────────────────────────────────

@router.message(Command("addemployee"))
async def cmd_add_employee(message: Message):
    if not is_admin(message.from_user.id):
        return  # молча игнорируем

    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer("Использование: /addemployee @username")
        return

    username = parts[1].lstrip("@").lower()
    await add_to_whitelist(username)
    await add_log("whitelist_add", message.from_user.id, f"@{username}")
    await message.answer(f"✅ @{username} добавлен в список сотрудников.")


@router.message(Command("removeemployee"))
async def cmd_remove_employee(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer("Использование: /removeemployee @username")
        return

    username = parts[1].lstrip("@").lower()
    await remove_from_whitelist(username)
    await add_log("whitelist_remove", message.from_user.id, f"@{username}")
    await message.answer(f"✅ @{username} удалён из списка сотрудников.")


@router.message(Command("admin"))
async def cmd_admin_help(message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "🔧 <b>Команды администратора:</b>\n\n"
        "/addemployee @username — добавить сотрудника\n"
        "/removeemployee @username — удалить сотрудника",
        parse_mode="HTML",
    )
