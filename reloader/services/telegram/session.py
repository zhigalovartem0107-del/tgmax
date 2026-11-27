"""Проверка Telegram-входа и закрытие соединения при отказе."""

from contextlib import suppress


async def connect_account(client, *, keep_open=True, keep_for_login=False):
    """Возвращает открытый клиент, если вход подтверждён или запрошен явный логин."""
    retain = False
    try:
        await client.connect()
        authorized = bool(await client.is_user_authorized())
        retain = keep_open and (authorized or keep_for_login)
        return (client if retain else None), authorized
    finally:
        if not retain:
            with suppress(Exception):
                await client.disconnect()
