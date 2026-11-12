# Запуск на хостинге ботов (Bothost и т.п.): python main.py
# Сам находит рядом файл бота с самой свежей версией (строка BOT_VERSION = ...), как бы он ни назывался:
# чтобы обновить бота, просто загрузи новый файл рядом — переименовывать и удалять старые не нужно.
# Все файлы бота (настройки, админы, очередь) лежат в папке data — она сохраняется между перезапусками.
import hashlib
import hmac
import os
import re
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 🔒 Защита от кражи: без правильного BOT_SECRET бот не запускается. В коде — только SHA-256 секрета, не сам секрет
#   (держи в синхроне с reloader/services/license.py — это проверяет тест). Секрет задаётся в переменных хостинга.
_SECRET_SHA256 = "2ce7077c94ee1b5c55f7ea2bda1308e0814fbf2696e2f3d4fadcc7c3294066d9"


def _license_gate():
    secret = (os.environ.get("BOT_SECRET") or "").strip()
    if secret and hmac.compare_digest(hashlib.sha256(secret.encode("utf-8")).hexdigest(), _SECRET_SHA256):
        return
    why = "нет переменной BOT_SECRET" if not secret else "BOT_SECRET не совпадает"
    print(f"🔒 КРИТИЧЕСКАЯ ОШИБКА: Unauthorized copy detected. Неверный или отсутствующий BOT_SECRET — "
          f"бот остановлен. ({why})\n   Задай правильный BOT_SECRET в переменных окружения хостинга.", flush=True)
    sys.exit(3)


_license_gate()


def bot_version(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            head = f.read(4000)
    except OSError:
        return -1
    m = re.search(r"^BOT_VERSION\s*=\s*(\d+)", head, re.M)
    return int(m.group(1)) if m else (0 if "MAX_API" in head or os.path.basename(path).startswith("tg_to_max") else -1)


files = [os.path.join(HERE, n) for n in sorted(os.listdir(HERE))
         if n.endswith(".py") and n not in ("main.py", "login.py") and not n.startswith("test_")]
BOT = max(files, key=bot_version, default=os.path.join(HERE, "tg_to_max.py"))
if bot_version(BOT) < 0:
    BOT = os.path.join(HERE, "tg_to_max.py")
DATA = os.environ.get("TG_MAX_DIR") or os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)
os.chdir(DATA)
os.environ.setdefault("TG_MAX_NO_SEARCH", "1")       # чужие входы в Telegram на хостинге не ищем
print("Запускаю", os.path.basename(BOT), "версии", bot_version(BOT), flush=True)
sys.argv = [BOT] + sys.argv[1:]
runpy.run_path(BOT, run_name="__main__")
