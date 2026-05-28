from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.fsm.context import FSMContext

from config import config
from database.models import (
    add_employee, is_employee, is_whitelisted,
    get_invite_code, add_log,
)

router = Router()


# ──────────────────────── Клавиатуры ────────────────────────────────

def kb_request_contact():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def kb_main_employee():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🪪 Мой QR-пропуск")],
            [KeyboardButton(text="👤 Пригласить гостя")],
        ],
        resize_keyboard=True,
    )


def kb_main_guest():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🎫 Получить QR-пропуск гостя")]],
        resize_keyboard=True,
    )


# ──────────────────────── /start ────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandStart):
    tg_id = message.from_user.id
    args = command.args  # deep-link параметр после /start

    # Если пришёл с инвайт-кодом — сохраняем в FSM сразу
    if args:
        invite_row = await get_invite_code(args)
        if invite_row:
            await state.update_data(
                invite_code=args,
                visit_date=invite_row["visit_date"],
                expected_fio=invite_row["guest_fio"],
            )
        else:
            await message.answer("⚠️ Ссылка-приглашение недействительна или уже использована.")

    employee = await is_employee(tg_id)

    if employee:
        await message.answer(
            f"👋 С возвращением, {message.from_user.first_name}!\nВыберите действие:",
            reply_markup=kb_main_employee(),
        )
        return

    # Гость с инвайт-кодом — сразу ведём к получению QR
    if args and await get_invite_code(args):
        await message.answer(
            "🔗 Ссылка-приглашение принята!\n"
            "Нажмите кнопку ниже, чтобы получить QR-пропуск.",
            reply_markup=kb_main_guest(),
        )
        return

    # Неизвестный пользователь
    await message.answer(
        "👋 Привет! Это бот системы контроля доступа.\n\n"
        "Если вы сотрудник — поделитесь контактом для авторизации.\n"
        "Если вы гость — вам нужна ссылка-приглашение от сотрудника.",
        reply_markup=kb_request_contact(),
    )


# ──────────────────── Авторизация через контакт ─────────────────────

@router.message(F.contact)
async def handle_contact(message: Message):
    contact = message.contact

    if contact.user_id != message.from_user.id:
        await message.answer("⚠️ Пожалуйста, поделитесь своим контактом, а не чужим.")
        return

    tg_id = message.from_user.id
    username = message.from_user.username or ""

    if await is_employee(tg_id):
        await message.answer("✅ Вы уже авторизованы.", reply_markup=kb_main_employee())
        return

    # Админ всегда проходит без whitelist
    if tg_id != config.ADMIN_TG_ID and not await is_whitelisted(username):
        await message.answer(
            "⛔ Вашего аккаунта нет в списке сотрудников.\n"
            "Обратитесь к администратору."
        )
        return

    full_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip()
    await add_employee(tg_id=tg_id, full_name=full_name, phone=contact.phone_number)
    await add_log("employee_registered", tg_id, f"{full_name} | @{username}")

    await message.answer(
        f"✅ Авторизация успешна!\nДобро пожаловать, {full_name}!",
        reply_markup=kb_main_employee(),
    )
