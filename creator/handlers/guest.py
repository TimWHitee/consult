import os
from aiogram import Router, F
from aiogram.types import Message, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from database.models import get_invite_code, mark_invite_used, add_guest, add_log
from qr_gen import create_guest_pass

router = Router()

os.makedirs(config.QR_DIR, exist_ok=True)


class GuestPassStates(StatesGroup):
    waiting_fio = State()
    waiting_passport = State()


# ──────────────────── Кнопка "Получить QR" ───────────────────────────

@router.message(F.text == "🎫 Получить QR-пропуск гостя")
async def guest_pass_start(message: Message, state: FSMContext):
    data = await state.get_data()
    invite_code = data.get("invite_code")

    if not invite_code:
        # Гость зашёл без deep-link — просим ввести код вручную
        await message.answer(
            "🔑 У вас нет активной ссылки-приглашения.\n"
            "Попросите сотрудника прислать вам ссылку."
        )
        return

    # Код есть — начинаем сбор данных
    await state.set_state(GuestPassStates.waiting_fio)
    await message.answer("👤 Введите ваше ФИО (полностью, как в паспорте):")


# ──────────────────── FSM: сбор данных ───────────────────────────────

@router.message(GuestPassStates.waiting_fio)
async def handle_guest_fio(message: Message, state: FSMContext):
    fio = message.text.strip()
    if len(fio.split()) < 2:
        await message.answer("⚠️ Введите полное ФИО (минимум имя и фамилия):")
        return

    data = await state.get_data()
    expected_fio = data.get("expected_fio", "")

    # Сравниваем без учёта регистра и лишних пробелов
    if expected_fio and ' '.join(fio.lower().split()) != ' '.join(expected_fio.lower().split()):
        await message.answer(
            "❌ ФИО не совпадает с данными в приглашении.\n"
            "Проверьте правильность написания и попробуйте ещё раз:"
        )
        return

    await state.update_data(fio=fio)
    await state.set_state(GuestPassStates.waiting_passport)
    await message.answer("🪪 Введите серию и номер паспорта (10 цифр, без пробелов):")


@router.message(GuestPassStates.waiting_passport)
async def handle_guest_passport(message: Message, state: FSMContext):
    passport = message.text.strip()

    if not passport.isdigit() or len(passport) != 10:
        await message.answer("⚠️ Введите ровно 10 цифр серии и номера паспорта:")
        return

    data = await state.get_data()
    fio = data["fio"]
    invite_code = data["invite_code"]
    visit_date = data["visit_date"]

    await state.clear()

    # Получаем строку инвайта ещё раз (проверка, что не использован)
    invite_row = await get_invite_code(invite_code)
    if not invite_row:
        await message.answer(
            "❌ Ссылка-приглашение уже использована или недействительна.\n"
            "Попросите сотрудника выдать новую."
        )
        return

    await message.answer("⏳ Генерирую ваш QR-пропуск...")

    qr_hash, filepath = create_guest_pass(
        fio=fio,
        passport=passport,
        visit_date=visit_date,
        masterpass=config.MASTERPASS_GUEST,
    )

    await add_guest(
        fio=fio,
        passport=passport,
        qr_hash=qr_hash,
        invited_by=invite_row["employee_id"],
        visit_date=visit_date,
    )
    await mark_invite_used(invite_code)
    await add_log(
        "guest_qr_issued",
        message.from_user.id,
        f"fio={fio} | date={visit_date} | hash={qr_hash[:16]}",
    )

    await message.answer_photo(
        FSInputFile(filepath),
        caption=(
            f"🎫 <b>QR-пропуск гостя</b>\n"
            f"👤 {fio}\n"
            f"📅 Действителен: <b>{visit_date}</b>\n\n"
            f"<i>Покажите этот QR-код при входе.</i>"
        ),
        parse_mode="HTML",
    )
