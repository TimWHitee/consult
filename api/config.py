import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ApiConfig:
    DB_PATH: str = os.getenv("SKUD_API_DB_PATH", "skud_api.db")
    STORAGE_DIR: str = os.getenv("SKUD_STORAGE_DIR", "storage")
    QR_SECRET: str = os.getenv("SKUD_QR_SECRET", "change-me-in-production")
    BOOTSTRAP_TOKEN: str = os.getenv("SKUD_BOOTSTRAP_TOKEN", "bootstrap-change-me")
    FACE_THRESHOLD: float = float(os.getenv("SKUD_FACE_THRESHOLD", "0.55"))
    UNLOCK_SECONDS: int = int(os.getenv("SKUD_UNLOCK_SECONDS", "5"))


config = ApiConfig()
