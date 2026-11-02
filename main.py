# Запуск на хостинге ботов (Bothost и т.п.): python main.py
# Сам находит рядом файл бота с самой свежей версией (строка BOT_VERSION = ...), как бы он ни назывался:
# чтобы обновить бота, просто загрузи новый файл рядом — переименовывать и удалять старые не нужно.
# Все файлы бота (настройки, админы, очередь) лежат в папке data — она сохраняется между перезапусками.
import os
import re
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


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
