"""Проверка секрета запуска по SHA-256 из переменных хостинга."""
import hashlib
import hmac
import os
import re
import sys

from bot_app.core import errors as _errors

# Проверка ключа из прежнего запуска MAX без BOT_PLATFORM.
_LEGACY_MAX_SHA256 = "2ce7077c94ee1b5c55f7ea2bda1308e0814fbf2696e2f3d4fadcc7c3294066d9"
HASH_VAR = "BOT_SECRET_SHA256"
ENV_VAR = "BOT_SECRET"
UNAUTHORIZED = "Unauthorized copy detected"
MESSAGE = (f"🔒 КРИТИЧЕСКАЯ ОШИБКА: {UNAUTHORIZED}. Неверный или отсутствующий BOT_SECRET — бот остановлен.\n"
           "   Проверь BOT_SECRET и BOT_SECRET_SHA256 в переменных окружения хостинга.")


def sha256(secret):
    return hashlib.sha256(str(secret).encode("utf-8")).hexdigest()


def secret_ok(secret, expected=None):
    """Секрет верный? Сравниваем ХЕШИ, причём compare_digest — чтобы время сравнения не подсказывало секрет."""
    if not isinstance(secret, str) or not secret:
        return False
    expected = str(expected or "").strip().lower()
    return bool(re.fullmatch(r"[0-9a-f]{64}", expected)) and hmac.compare_digest(sha256(secret), expected)


def check(env=None, expected=None):
    """(ok, причина). env — словарь переменных окружения (по умолчанию os.environ)."""
    env = os.environ if env is None else env
    secret = str(env.get(ENV_VAR) or "").strip()
    if not secret:
        return False, "нет переменной BOT_SECRET"
    if expected is None:
        platform = str(env.get("BOT_PLATFORM") or "").strip().lower()
        expected = str(env.get(HASH_VAR) or "").strip()
        # У старых инсталляций MAX мог остаться неиспользуемый хеш из новой сборки.
        if not platform and secret_ok(secret, _LEGACY_MAX_SHA256):
            return True, "ok"
        if platform in ("", "max") and not expected:
            expected = _LEGACY_MAX_SHA256
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(expected or "").strip()):
        return False, "задай BOT_SECRET_SHA256 (64 шестнадцатеричных символа)"
    if not secret_ok(secret, expected):
        return False, "BOT_SECRET не совпадает"
    return True, "ok"


def error_code(env=None):
    """Код отказа замка: секрета нет — ERR_LICENSE_MISSING, неверный — ERR_LICENSE_BAD."""
    env = os.environ if env is None else env
    return "ERR_LICENSE_MISSING" if not str(env.get(ENV_VAR) or "").strip() else "ERR_LICENSE_BAD"


def enforce(env=None, expected=None, log=print, _exit=None):
    """Пускаем бота только с верным BOT_SECRET. Иначе — критическая ошибка в лог и немедленный выход (код 3),
    бот не стартует. Возвращает True, если секрет верный."""
    ok, why = check(env, expected)
    if ok:
        return True
    (log or (lambda *_a: None))(f"{_errors.line(error_code(env))}\n{MESSAGE} ({why})")
    (_exit or sys.exit)(3)
    return False                                         # на случай, если _exit (в тестах) не завершает процесс
