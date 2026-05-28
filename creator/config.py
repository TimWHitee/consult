import os
from dataclasses import dataclass


@dataclass
class Config:
    BOT_TOKEN: str = os.getenv(
        "BOT_TOKEN", "8525902599:AAF-Fpukyz5adlAZbnG06lzbwN9n5fnrHnw")
    DB_PATH: str = os.getenv("DB_PATH", "skud.db")
    MASTERPASS_EMPLOYEE: str = os.getenv("MASTERPASS_EMPLOYEE", "skud15")
    MASTERPASS_GUEST: str = os.getenv("MASTERPASS_GUEST", "skud16")
    QR_DIR: str = "qr_codes"
    ADMIN_TG_ID: int = 896079043


config = Config()
