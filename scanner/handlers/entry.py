from typing import List, Tuple
import os
import asyncio
import numpy as np
import pickle
import face_recognition

from functools import partial
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from database.models import add_log
from qr_verify import verify_qr_payload

from pyzbar.pyzbar import decode
from PIL import Image

router = Router()

os.makedirs(config.FACE_DIR, exist_ok=True)
os.makedirs("qr_scans", exist_ok=True)


# ──────────────────────── Загрузка модели ────────────────────────────


def _load_model(model_path: str) -> Tuple[List[str], np.ndarray, float]:
    with open(model_path, "rb") as f:
        model = pickle.load(f)
    threshold = float(model["threshold"])
    people = model["people"]
    names = sorted(people.keys())
    centroids = np.stack(
        [np.asarray(people[n]["centroid"], dtype=np.float32) for n in names], axis=0
    )
    return names, centroids, threshold


_names, _centroids, _THRESHOLD = _load_model("handlers/model.pkl")


def recognize_person(image_path: str) -> str:
    """Синхронная — вызывать через run_in_executor."""
    img = face_recognition.load_image_file(image_path)
    boxes = face_recognition.face_locations(img, model="hog")

    if len(boxes) != 1:
        return "Неизвестный"

    enc = face_recognition.face_encodings(img, known_face_locations=boxes)[0]
    enc = enc.astype(np.float32)

    dists = np.linalg.norm(_centroids - enc[None, :], axis=1)
    best_idx = int(np.argmin(dists))

    if float(dists[best_idx]) < _THRESHOLD:
        return _names[best_idx]
    return "Неизвестный"


# ──────────────────────── Состояния ──────────────────────────────────

class EntryStates(StatesGroup):
    waiting_qr_photo = State()
    waiting_face_photo = State()


# ──────────────────────── Клавиатуры ─────────────────────────────────

def kb_main():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📷 Войти по QR-коду")],
            [KeyboardButton(text="🪪 Войти по Face ID")],
        ],
        resize_keyboard=True,
    )


# ──────────────────────── /start ─────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🏢 <b>Система контроля доступа</b>\n\nВыберите способ входа:",
        reply_markup=kb_main(),
        parse_mode="HTML",
    )


# ──────────────────────── QR-вход ────────────────────────────────────

@router.message(F.text == "📷 Войти по QR-коду")
async def entry_qr_start(message: Message, state: FSMContext):
    await state.set_state(EntryStates.waiting_qr_photo)
    await message.answer(
        "📲 Отправьте фотографию вашего QR-кода.\n\n"
        "<i>Убедитесь, что QR хорошо виден и не смазан.</i>",
        parse_mode="HTML",
    )


@router.message(EntryStates.waiting_qr_photo, F.photo)
async def handle_qr_photo(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🔍 Считываю QR-код...")

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    filepath = f"qr_scans/{message.from_user.id}_{photo.file_id}.jpg"
    await message.bot.download_file(file.file_path, filepath)

    img = Image.open(filepath)
    decoded = decode(img)
    payload = decoded[0].data.decode("utf-8") if decoded else None

    if not payload:
        await add_log("qr_decode_failed", message.from_user.id, f"file={filepath}")
        await message.answer(
            "❌ <b>Не удалось считать QR-код.</b>\n\nПопробуйте сделать более чёткое фото.",
            parse_mode="HTML",
            reply_markup=kb_main(),
        )
        return

    result = verify_qr_payload(
        payload=payload,
        masterpass_employee=config.MASTERPASS_EMPLOYEE,
        masterpass_guest=config.MASTERPASS_GUEST,
    )

    if result.success:
        role = "сотрудник" if result.is_employee else "гость"
        role_emoji = "👔" if result.is_employee else "🧑‍🤝‍🧑"
        await add_log("qr_access_granted", message.from_user.id,
                      f"fio={result.fio} | role={role} | date={result.visit_date}")
        await message.answer(
            f"✅ <b>Доступ разрешён!</b>\n\n"
            f"{role_emoji} <b>{result.fio}</b>\n"
            f"📋 Статус: {role}\n"
            f"📅 Пропуск: {result.visit_date}\n\n"
            f"<i>Турникет открыт. Добро пожаловать!</i>",
            parse_mode="HTML",
            reply_markup=kb_main(),
        )
    else:
        await add_log("qr_access_denied", message.from_user.id,
                      f"fio={result.fio} | error={result.error}")
        await message.answer(
            f"🚫 <b>Доступ запрещён!</b>\n\n"
            f"❌ {result.error}\n\n"
            f"<i>Обратитесь к охраннику или администратору.</i>",
            parse_mode="HTML",
            reply_markup=kb_main(),
        )


@router.message(EntryStates.waiting_qr_photo)
async def handle_qr_not_photo(message: Message):
    await message.answer("📸 Пожалуйста, отправьте именно фотографию QR-кода.")


# ──────────────────────── Face ID вход ───────────────────────────────

@router.message(F.text == "🪪 Войти по Face ID")
async def entry_face_start(message: Message, state: FSMContext):
    await state.set_state(EntryStates.waiting_face_photo)
    await message.answer(
        "🤳 Отправьте своё селфи для распознавания лица.\n\n"
        "<i>Смотрите прямо в камеру при хорошем освещении.</i>",
        parse_mode="HTML",
    )


@router.message(EntryStates.waiting_face_photo, F.photo)
async def handle_face_photo(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🔍 Обрабатываю фото, подождите...")

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    filepath = os.path.join(
        config.FACE_DIR, f"{message.from_user.id}_{photo.file_id}.jpg")
    await message.bot.download_file(file.file_path, filepath)

    await add_log("face_photo_received", message.from_user.id, f"file={filepath}")

    # face_recognition синхронный и тяжёлый — запускаем в потоке
    loop = asyncio.get_event_loop()
    name = await loop.run_in_executor(None, partial(recognize_person, filepath))

    if name == "Неизвестный":
        await add_log("face_access_denied", message.from_user.id, "unknown_person")
        await message.answer(
            "🚫 <b>Доступ запрещён!</b>\n\n"
            "❌ Лицо не распознано.\n\n"
            "<i>Обратитесь к охраннику или воспользуйтесь QR-кодом.</i>",
            parse_mode="HTML",
            reply_markup=kb_main(),
        )
    else:
        await add_log("face_access_granted", message.from_user.id, f"name={name}")
        await message.answer(
            f"✅ <b>Доступ разрешён!</b>\n\n"
            f"👤 <b>{name}</b>\n\n"
            f"<i>Турникет открыт. Добро пожаловать!</i>",
            parse_mode="HTML",
            reply_markup=kb_main(),
        )


@router.message(EntryStates.waiting_face_photo)
async def handle_face_not_photo(message: Message):
    await message.answer("📸 Пожалуйста, отправьте именно фотографию.")
