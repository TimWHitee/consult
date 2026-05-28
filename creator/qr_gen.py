import qrcode
import hashlib
from datetime import datetime


def generate_qr(text: str, filename: str = "qrcode.png") -> str:
    qr = qrcode.QRCode(version=5, box_size=10, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image()
    img.save(filename)
    return filename


def build_qr_payload(fio: str, passport: str, visit_date: str, masterpass: str) -> tuple[str, str]:
    """
    Строит самодостаточный payload для QR.
    Формат строки: fio|passport|visit_date|hash
    Hash = sha256(fio|passport|visit_date|masterpass)
    Возвращает (payload_string, hash_hex)
    """
    fio_norm = ''.join(fio.split()).lower()
    passport_norm = ''.join(passport.split())
    date_norm = visit_date.strip()

    pre_hash = f"{fio_norm}|{passport_norm}|{date_norm}|{masterpass}"
    qr_hash = hashlib.sha256(pre_hash.encode('utf-8')).hexdigest()

    payload = f"{fio}|{passport}|{visit_date}|{qr_hash}"
    return payload, qr_hash


def create_guest_pass(fio: str, passport: str, visit_date: str, masterpass: str = 'skud16') -> tuple[str, str]:
    """
    Генерирует QR-пропуск для гостя.
    Возвращает (qr_hash, filepath)
    """
    payload, qr_hash = build_qr_payload(fio, passport, visit_date, masterpass)
    safe_name = ''.join(c for c in fio if c.isalnum()
                        or c in (' ', '_')).strip().replace(' ', '_')
    filename = f"qr_codes/QR_guest_{safe_name}_{visit_date.replace('.', '')}.png"
    generate_qr(payload, filename)
    return qr_hash, filename


def create_temporary_pass(employee_id: int | str, masterpass: str = 'skud15') -> tuple[str, str]:
    """
    Генерирует временный QR-пропуск для сотрудника (на сегодня).
    Возвращает (qr_hash, filepath)
    """
    visit_date = datetime.now().strftime("%d.%m.%Y")
    payload, qr_hash = build_qr_payload(
        fio=str(employee_id),
        passport='employee',
        visit_date=visit_date,
        masterpass=masterpass,
    )
    filename = f"qr_codes/QR_emp_{employee_id}_{visit_date.replace('.', '')}.png"
    generate_qr(payload, filename)
    return qr_hash, filename
