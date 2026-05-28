import os
from datetime import datetime
import hashlib
from aiogram import Router, F
from aiogram.types import Message, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from database.models import get_employee, save_invite_code, add_log
from qr_gen import create_temporary_pass

router = Router()

os.makedirs(config.QR_DIR, exist_ok=True)


class InviteGuestStates(StatesGroup):
    waiting_guest_fio = State()
    waiting_visit_date = State()


# ──────────────────── QR пропуск сотрудника ──────────────────────────

@router.message(F.text == "🪪 Мой QR-пропуск")
async def my_qr_pass(message: Message, is_employee: bool):
    if not is_employee:
        await message.answer("⛔ Эта функция доступна только сотрудникам.")
        return

    employee = await get_employee(message.from_user.id)
    qr_hash, filepath = create_temporary_pass(
        employee_id=message.from_user.id,
        masterpass=config.MASTERPASS_EMPLOYEE,
    )
    await add_log("employee_qr_issued", message.from_user.id, f"hash={qr_hash[:16]}")

    await message.answer_photo(
        FSInputFile(filepath),
        caption=(
            f"🪪 <b>QR-пропуск сотрудника</b>\n"
            f"👤 {employee['full_name']}\n"
            f"📅 {datetime.now().strftime('%d.%m.%Y')}\n\n"
            f"<i>Действителен только сегодня.</i>"
        ),
        parse_mode="HTML",
    )


# ──────────────────── Инвайт гостя ───────────────────────────────────

@router.message(F.text == "👤 Пригласить гостя")
async def invite_guest_start(message: Message, is_employee: bool, state: FSMContext):
    if not is_employee:
        await message.answer("⛔ Эта функция доступна только сотрудникам.")
        return

    await state.set_state(InviteGuestStates.waiting_guest_fio)
    await message.answer("👤 Введите ФИО гостя (полностью, как в паспорте):")


@router.message(InviteGuestStates.waiting_guest_fio)
async def invite_guest_fio(message: Message, state: FSMContext):
    fio = message.text.strip()
    if len(fio.split()) < 2:
        await message.answer("⚠️ Введите полное ФИО (минимум имя и фамилия):")
        return

    await state.update_data(guest_fio=fio)
    await state.set_state(InviteGuestStates.waiting_visit_date)
    await message.answer("📅 Укажите дату визита гостя (ДД.ММ.ГГГГ):")


@router.message(InviteGuestStates.waiting_visit_date)
async def invite_guest_date(message: Message, state: FSMContext):
    date_text = message.text.strip()
    try:
        datetime.strptime(date_text, "%d.%m.%Y")
    except ValueError:
        await message.answer("⚠️ Неверный формат. Введите дату как ДД.ММ.ГГГГ:")
        return

    data = await state.get_data()
    guest_fio = data["guest_fio"]
    await state.clear()

    raw = f"{message.from_user.id}|{guest_fio}|{date_text}|{datetime.now().timestamp()}"
    invite_code = hashlib.sha256(raw.encode()).hexdigest()[:8].upper()

    await save_invite_code(
        code=invite_code,
        employee_id=message.from_user.id,
        guest_fio=guest_fio,
        visit_date=date_text,
    )
    await add_log(
        "invite_code_created",
        message.from_user.id,
        f"guest_fio={guest_fio} | date={date_text} | code={invite_code}",
    )

    bot_username = (await message.bot.get_me()).username
    invite_link = f"https://t.me/{bot_username}?start={invite_code}"

    await message.answer(
        f"✅ <b>Ссылка-приглашение создана!</b>\n\n"
        f"👤 Гость: <b>{guest_fio}</b>\n"
        f"📅 Дата визита: <b>{date_text}</b>\n"
        f"🔑 Код: <code>{invite_code}</code>\n\n"
        f"🔗 Ссылка для гостя:\n{invite_link}\n\n"
        f"Перешлите эту ссылку гостю. Ссылка одноразовая.",
        parse_mode="HTML",
    )
