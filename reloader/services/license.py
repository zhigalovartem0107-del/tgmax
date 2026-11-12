"""🔒 Защита от кражи: бот запускается только с правильным BOT_SECRET.

Секрет задаёт владелец в переменных окружения хостинга (Bothost → Variables → BOT_SECRET). В КОДЕ хранится только
SHA-256 секрета, а не сам секрет: даже если кто-то скопирует все файлы бота, без секрета запустить его не получится.
main.py в самом начале вызывает enforce(): нет или неверный BOT_SECRET — «Unauthorized copy detected» и немедленный
выход. Только проверка — без сети и без состояния бота, легко тестируется."""
import hashlib
import hmac
import os
import sys

# SHA-256 правильного BOT_SECRET. Сам секрет здесь НЕ хранится — только его хеш (поменять секрет: пересчитать хеш
#   `python -c "import hashlib;print(hashlib.sha256(b'НОВЫЙ_СЕКРЕТ').hexdigest())"` и вписать сюда).
EXPECTED_SHA256 = "2ce7077c94ee1b5c55f7ea2bda1308e0814fbf2696e2f3d4fadcc7c3294066d9"
ENV_VAR = "BOT_SECRET"
UNAUTHORIZED = "Unauthorized copy detected"
MESSAGE = (f"🔒 КРИТИЧЕСКАЯ ОШИБКА: {UNAUTHORIZED}. Неверный или отсутствующий BOT_SECRET — бот остановлен.\n"
           "   Задай правильный BOT_SECRET в переменных окружения хостинга (у легального владельца он есть).")


def sha256(secret):
    return hashlib.sha256(str(secret).encode("utf-8")).hexdigest()


def secret_ok(secret, expected=EXPECTED_SHA256):
    """Секрет верный? Сравниваем ХЕШИ, причём compare_digest — чтобы время сравнения не подсказывало секрет."""
    if not isinstance(secret, str) or not secret:
        return False
    return hmac.compare_digest(sha256(secret), str(expected).strip().lower())


def check(env=None, expected=EXPECTED_SHA256):
    """(ok, причина). env — словарь переменных окружения (по умолчанию os.environ)."""
    env = os.environ if env is None else env
    secret = str(env.get(ENV_VAR) or "").strip()
    if not secret:
        return False, "нет переменной BOT_SECRET"
    if not secret_ok(secret, expected):
        return False, "BOT_SECRET не совпадает"
    return True, "ok"


def enforce(env=None, expected=EXPECTED_SHA256, log=print, _exit=None):
    """Пускаем бота только с верным BOT_SECRET. Иначе — критическая ошибка в лог и немедленный выход (код 3),
    бот не стартует. Возвращает True, если секрет верный."""
    ok, why = check(env, expected)
    if ok:
        return True
    (log or (lambda *_a: None))(f"{MESSAGE} ({why})")
    (_exit or sys.exit)(3)
    return False                                         # на случай, если _exit (в тестах) не завершает процесс
