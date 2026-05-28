from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message
from database.models import is_employee, add_log


class AuthMiddleware(BaseMiddleware):
    """
    Пробрасывает в хендлеры флаги:
      - is_employee: bool
      - invite_code: str | None  (из deep link ?start=CODE)
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user:
            employee = await is_employee(user.id)
            data["is_employee"] = employee

        return await handler(event, data)
