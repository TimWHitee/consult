"""
Распознавание лица: пользователь отправляет фото боту.
Здесь заглушка — подключи face_recognition / DeepFace / API по желанию.
"""
import os
from aiogram import Router, F
from aiogram.types import Message

from database.models import add_log

router = Router()

FACE_DIR = "face_photos"
os.makedirs(FACE_DIR, exist_ok=True)


@router.message(F.photo)
async def handle_face_photo(message: Message):
    await message.answer("📸 Фото получено, обрабатываю...")

    # Скачиваем лучшее качество фото
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    filepath = os.path.join(FACE_DIR, f"{message.from_user.id}_{photo.file_id}.jpg")
    await message.bot.download_file(file.file_path, filepath)

    await add_log(
        "face_photo_received",
        message.from_user.id,
        f"file={filepath}",
    )

    # ──────────────────────────────────────────────────────────────
    # МЕСТО ДЛЯ ВАШЕЙ ЛОГИКИ РАСПОЗНАВАНИЯ
    # Варианты:
    #   1. face_recognition (локально):
    #      import face_recognition
    #      known = face_recognition.load_image_file("db/employee_X.jpg")
    #      unknown = face_recognition.load_image_file(filepath)
    #      result = face_recognition.compare_faces([known_encoding], unknown_encoding)
    #
    #   2. DeepFace:
    #      from deepface import DeepFace
    #      result = DeepFace.verify(filepath, "db/employee_X.jpg")
    #
    #   3. Внешний API (например, Face++ или Azure Face API):
    #      response = requests.post(API_URL, files={"image": open(filepath, "rb")})
    # ──────────────────────────────────────────────────────────────

    # Заглушка — всегда отвечаем нейтрально
    recognized = False  # заменить на результат реального распознавания

    if recognized:
        await message.answer(
            "✅ <b>Лицо распознано!</b>\nДоступ разрешён.",
            parse_mode="HTML",
        )
        await add_log("face_recognized", message.from_user.id, "access=granted")
    else:
        await message.answer(
            "❌ <b>Лицо не распознано.</b>\n"
            "Обратитесь к охраннику или воспользуйтесь QR-кодом.",
            parse_mode="HTML",
        )
        await add_log("face_not_recognized", message.from_user.id, "access=denied")
