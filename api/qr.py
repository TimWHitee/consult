import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import qrcode

from .config import config


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def sign_body(body: str) -> str:
    return hmac.new(
        config.QR_SECRET.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_qr_payload(
    *,
    company_id: int,
    qr_pass_id: int,
    subject_type: str,
    subject_id: int,
    expires_at: str,
    nonce: str,
) -> str:
    body_data = {
        "v": 1,
        "company_id": company_id,
        "qr_pass_id": qr_pass_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "expires_at": expires_at,
        "nonce": nonce,
    }
    body_json = json.dumps(body_data, separators=(",", ":"), sort_keys=True)
    body = b64url_encode(body_json.encode("utf-8"))
    return f"skud1.{body}.{sign_body(body)}"


def verify_qr_payload(payload: str) -> tuple[bool, dict[str, Any] | None, str]:
    parts = payload.strip().split(".")
    if len(parts) != 3 or parts[0] != "skud1":
        return False, None, "invalid_qr_format"

    _, body, signature = parts
    if not hmac.compare_digest(sign_body(body), signature):
        return False, None, "invalid_qr_signature"

    try:
        data = json.loads(b64url_decode(body))
    except (ValueError, json.JSONDecodeError):
        return False, None, "invalid_qr_payload"

    try:
        expires_at = parse_dt(data["expires_at"])
    except (KeyError, ValueError, TypeError):
        return False, None, "invalid_qr_expiration"

    if expires_at < utc_now():
        return False, data, "qr_expired"

    return True, data, "ok"


def default_expiration(hours: int = 12) -> str:
    return (utc_now() + timedelta(hours=hours)).isoformat()


def new_nonce() -> str:
    return secrets.token_urlsafe(18)


def save_qr_png(payload: str, path: str) -> None:
    qr = qrcode.QRCode(version=4, box_size=10, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    qr.make_image().save(path)
