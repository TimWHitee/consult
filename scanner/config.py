import os
from dataclasses import dataclass


@dataclass
class Config:
    BOT_TOKEN: str = os.getenv(
        "GUARD_BOT_TOKEN", "8300257523:AAF0Xwv7KofPUvXl3z5oXFUsPJQ2spfsLLc")
    DB_PATH: str = os.getenv("DB_PATH", "skud.db")

    # Мастерпассы должны совпадать с первым ботом
    MASTERPASS_EMPLOYEE: str = os.getenv("MASTERPASS_EMPLOYEE", "skud15")
    MASTERPASS_GUEST: str = os.getenv("MASTERPASS_GUEST", "skud16")
    FACE_DIR: str = "face_photos"


config = Config()
