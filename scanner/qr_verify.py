import hashlib
from dataclasses import dataclass
from datetime import datetime


@dataclass
class QRVerifyResult:
    success: bool
    fio: str = ""
    passport: str = ""
    visit_date: str = ""
    error: str = ""
    is_employee: bool = False


def verify_qr_payload(payload: str, masterpass_employee: str, masterpass_guest: str) -> QRVerifyResult:
    """
    Принимает строку из QR-кода формата:
        fio|passport|visit_date|hash

    Пересчитывает hash и сравнивает.
    Пробует оба мастерпасса (сотрудник и гость).
    """
    parts = payload.strip().split("|")
    if len(parts) != 4:
        return QRVerifyResult(success=False, error="Неверный формат QR-кода.")

    fio, passport, visit_date, qr_hash = parts

    # Нормализация — так же, как при генерации
    fio_norm = ''.join(fio.split()).lower()
    passport_norm = ''.join(passport.split())
    date_norm = visit_date.strip()

    def compute_hash(masterpass: str) -> str:
        pre = f"{fio_norm}|{passport_norm}|{date_norm}|{masterpass}"
        print(pre)
        return hashlib.sha256(pre.encode("utf-8")).hexdigest()

    # Проверяем сначала как сотрудника
    if compute_hash(masterpass_employee) == qr_hash:
        return QRVerifyResult(
            success=True,
            fio=fio,
            passport=passport,
            visit_date=visit_date,
            is_employee=True,
        )

    # Проверяем как гостя
    if compute_hash(masterpass_guest) == qr_hash:
        # Проверяем что пропуск действителен сегодня
        today = datetime.now().strftime("%d.%m.%Y")
        if visit_date != today:
            return QRVerifyResult(
                success=False,
                fio=fio,
                passport=passport,
                visit_date=visit_date,
                error=f"Пропуск действителен только {visit_date}, сегодня {today}.",
            )
        return QRVerifyResult(
            success=True,
            fio=fio,
            passport=passport,
            visit_date=visit_date,
            is_employee=False,
        )

    return QRVerifyResult(
        success=False,
        fio=fio,
        passport=passport,
        visit_date=visit_date,
        error="Хэш не совпадает. QR-код недействителен или подделан.",
    )
