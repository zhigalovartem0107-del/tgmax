"""
Репост из нескольких Telegram-каналов в канал MAX.
Управление — через меню прямо в MAX (напиши своему боту /start) или командами через / (/help).
Есть тайник с мемами: когда очередь пуста, бот выкладывает пост оттуда.

Установка:   pip install telethon requests
"""
import asyncio
import builtins
import contextlib
import contextvars
import copy
import functools
import hashlib
import json
import math
import os
import random
import re
import socket
import sys
import threading
import time
import traceback
from collections import Counter, deque
from difflib import SequenceMatcher

import requests
import urllib3
from requests.adapters import HTTPAdapter
from telethon import TelegramClient
from urllib3.util.retry import Retry


BOT_VERSION = 2026101905            # номер версии: main.py на хостинге запускает файл с самым большим номером

# Хостинг ботов (Bothost и т.п.): файл бота назван main.py, или задан TG_MAX_DIR, или хостинг выдал BOT_ID.
# Тогда все файлы бота живут в папке data рядом (она сохраняется между перезапусками), консоли нет.
_FILE = globals().get("__file__") or ""
HOSTED = bool(os.environ.get("TG_MAX_DIR") or os.environ.get("BOT_ID")) \
    or os.path.dirname(os.path.abspath(_FILE or ".")) == "/app" or (
    os.path.basename(_FILE) == "main.py"
    and not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(_FILE)), "config.json")))
if HOSTED and __name__ == "__main__":
    _data = os.environ.get("TG_MAX_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(_data, exist_ok=True)
    os.chdir(_data)
    os.environ["TG_MAX_DIR"] = _data
    os.environ.setdefault("TG_MAX_NO_SEARCH", "1")   # чужие входы в Telegram на хостинге не ищем
if HOSTED and not os.environ.get("TZ"):              # часы сервера обычно в UTC — расписание и ночь по Москве
    os.environ["TZ"] = "MSK-3"
    with contextlib.suppress(AttributeError):
        time.tzset()
for _stream in (sys.stdout, sys.stderr):              # логи хостинга видны сразу, а не когда наберётся буфер
    with contextlib.suppress(Exception):
        _stream.reconfigure(line_buffering=True)
LOG_FILE = "bot.log"
JOURNAL = deque(maxlen=300)          # что бот делал с постами — для команды /log и кнопки «Журнал»


def _log_line(line):
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def print(*args, **kwargs):          # всё, что бот пишет в консоль, дублируется в bot.log
    line = time.strftime("%H:%M:%S") + " " + " ".join(str(a) for a in args)
    kwargs.setdefault("flush", True)
    builtins.print(line, **kwargs)
    _log_line(line)


def journal(text):
    """Запись о том, что произошло с постом: видна в консоли, в bot.log и в /log."""
    print(text)
    JOURNAL.append(time.strftime("%d.%m %H:%M") + " " + text)


def log_exc(where):
    """Ошибка с подробностями: в консоль — одна строка, полный текст — в bot.log."""
    e = sys.exc_info()[1]
    _log_line(time.strftime("%H:%M:%S") + f" ОШИБКА в {where}:\n" + traceback.format_exc())
    builtins.print(time.strftime("%H:%M:%S") + f" ⚠️ {where}: {type(e).__name__}: {e} (подробности в {LOG_FILE})",
                   flush=True)


def _excepthook(tp, val, tb):
    _log_line("НЕПЕРЕХВАЧЕННАЯ ОШИБКА:\n" + "".join(traceback.format_exception(tp, val, tb)))
    builtins.print(f"⚠️ {tp.__name__}: {val} (подробности в {LOG_FILE})", flush=True)


sys.excepthook = _excepthook
threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback)

DATA_FILES = ["settings.json", "themes.json", "queue.json", "pub_state.json", "last_posts.json",
              "posted_history.json", "stash.json", "cards.json", "channel_id.txt"]
DATA_GLOBS = ["queue_*.json", "pub_state_*.json", "stash_*.json", "last_posts_*.json", "posted_history_*.json"]
SKIP_DIRS = {"DCIM", "Pictures", "Movies", "Music", "Podcasts", "Telegram", "WhatsApp", "Alarms",
             "Ringtones", "Notifications", "Download.tmp", "cache", "__pycache__"}


def _find_sessions(roots, skip, max_depth=5, max_dirs=20000, max_sec=15):
    """Все найденные на устройстве файлы session.session (кроме папки skip), свежие первыми."""
    found, seen, t0, visited = {}, set(), time.time(), 0
    skip = os.path.abspath(skip)
    for root in roots:
        stack = [(root, 0)]
        while stack and visited < max_dirs and time.time() - t0 < max_sec:
            d, depth = stack.pop()
            real = os.path.realpath(d)
            if real in seen:
                continue
            seen.add(real)
            visited += 1
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_file() and e.name == "session.session":
                        if os.path.abspath(d) != skip and os.path.getsize(e.path) > 0:
                            found[os.path.abspath(d)] = e.stat().st_mtime
                    elif (e.is_dir(follow_symlinks=False) and depth < max_depth
                          and not e.name.startswith(".") and e.name not in SKIP_DIRS):
                        stack.append((e.path, depth + 1))
                except OSError:
                    continue
    return [d for d, _ in sorted(found.items(), key=lambda kv: -kv[1])]


async def _authorized(session_name):
    """True — вход рабочий, False — нет, None — не удалось проверить (например, нет интернета)."""
    try:
        c = TelegramClient(session_name, API_ID, API_HASH)
        await c.connect()
        ok = await c.is_user_authorized()
        await c.disconnect()
        return bool(ok)
    except Exception as e:
        print("  проверка входа не удалась:", e)
        return None


def setup_data_dir(base=None, extra_roots=None, check=None):
    """Работаем в той же папке, что и раньше (откуда запущен скрипт). Если здесь нет рабочего входа в Telegram,
    ищем его по телефону, проверяем каждый найденный session.session и берём тот, что реально залогинен —
    вместе с настройками и очередью. Тогда код из Telegram не нужен."""
    import glob
    import shutil
    base = base or os.environ.get("TG_MAX_DIR") or os.getcwd()
    os.makedirs(base, exist_ok=True)
    os.chdir(base)
    check = check or (lambda name: asyncio.run(_authorized(name)))
    print(f"Бот версии {BOT_VERSION}. Папка данных бота:", os.getcwd())
    if not TG_READY:
        print("Telegram-аккаунт не подключён — работаю без входа (это нормально).")
        return
    if os.path.exists("session.session"):
        ok = check("session")
        if ok is not False:                       # рабочий или не удалось проверить — не трогаем
            print("Вход в Telegram: найден ✅")
            return
        print("Вход в Telegram здесь есть, но он больше не действует — ищу другой...")
    if os.environ.get("TG_MAX_NO_SEARCH"):        # сервер с несколькими ботами: чужие входы не трогаем
        print("Рабочий вход в Telegram не найден — подключи аккаунт в MAX: меню → «🔑 Telegram».")
        return
    roots = [base, os.path.dirname(os.path.abspath(__file__)),
             os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else "",
             os.path.expanduser("~"), os.path.dirname(base), "/storage/emulated/0", "/sdcard",
             "/data/user/0/ru.iiec.pydroid3", "/data/data/ru.iiec.pydroid3"] + list(extra_roots or [])
    roots = [r for i, r in enumerate(roots) if r and os.path.isdir(r) and r not in roots[:i]]
    cands = _find_sessions(roots, base)
    print(f"Ищу вход в Telegram на устройстве... найдено файлов: {len(cands)}")
    for src in cands:
        print("  проверяю:", src)
        shutil.copy2(os.path.join(src, "session.session"), "probe_session.session")
        ok = check("probe_session")
        if ok:
            if os.path.exists("session.session"):
                shutil.copy2("session.session", "session.session.bak")
            os.replace("probe_session.session", "session.session")
            names = list(DATA_FILES)
            for g in DATA_GLOBS:
                names += [os.path.basename(p) for p in glob.glob(os.path.join(src, g))]
            for name in names:
                if os.path.exists(os.path.join(src, name)) and not os.path.exists(name):
                    shutil.copy2(os.path.join(src, name), name)
            for sub in ("stash_media", "queue_media"):
                if os.path.isdir(os.path.join(src, sub)) and not os.path.exists(sub):
                    shutil.copytree(os.path.join(src, sub), sub)
            print(f"Рабочий вход найден в {src} — скопировал сюда вместе с настройками. Код не нужен ✅")
            return
        os.remove("probe_session.session")
    print("Рабочий вход в Telegram не найден — понадобится войти.")


# ================== НАСТРОЙКИ (редко меняются) ==================
# Ключи и токены лежат в config.json рядом со скриптом (при первом запуске бот спросит их сам).
CONFIG_FILE = "config.json"
CONFIG_FIELDS = [
    ("api_id", "Telegram api_id (число, берётся на my.telegram.org)"),
    ("api_hash", "Telegram api_hash (там же)"),
    ("phone", "Номер телефона аккаунта Telegram, например +79991234567"),
    ("max_token", "Токен бота MAX"),
    ("max_channel_link", "Ссылка на твой канал MAX, например https://max.ru/название"),
]


TG_FIELDS = ("api_id", "api_hash", "phone")    # нужны только для входа в аккаунт Telegram (login.py)
LOGIN = "--login" in sys.argv                  # запуск через login.py: войти в аккаунт Telegram


FIELD_HINTS = {
    "api_id": "api_id — число из 7–8 цифр с my.telegram.org (раздел API development tools)",
    "api_hash": "api_hash — 32 символа (цифры и буквы a–f) оттуда же",
    "phone": "номер в международном формате, например +79991234567",
    "max_token": "токен бота MAX — длинная строка без пробелов",
    "max_channel_link": "ссылка вида https://max.ru/название_канала",
}


def field_ok(key, v):
    v = str(v or "").strip()
    if key == "api_id":
        return v.isdigit() and 6 <= len(v) <= 10
    if key == "api_hash":
        return re.fullmatch(r"[0-9a-fA-F]{32}", v) is not None
    if key == "phone":
        digits = re.sub(r"\D", "", v)
        return 10 <= len(digits) <= 15
    if key == "max_token":
        return len(v) >= 8 and " " not in v
    if key == "max_channel_link":
        return "max.ru/" in v
    return bool(v)


def norm_phone(v):
    """+79991234567 из «8 999 123-45-67», «79991234567» и т.п."""
    d = re.sub(r"\D", "", str(v or ""))
    if len(d) == 11 and d.startswith("8"):
        d = "7" + d[1:]
    return "+" + d if d else ""


def load_config(interactive):
    cfg = {"api_id": 0, "api_hash": "", "phone": "", "max_token": "", "max_channel_link": "",
           "max_chat_id": None, "max_api": "https://platform-api2.max.ru", "verify_ssl": False,
           "test_repost_last": False}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"{CONFIG_FILE} не читается ({e}) — проверь запятые и кавычки")
    env = {key: os.getenv(var) for key, var in (("api_id", "TG_API_ID"), ("api_hash", "TG_API_HASH"),
           ("phone", "TG_PHONE"), ("max_token", "BOT_TOKEN"), ("max_token", "MAX_TOKEN"),
           ("max_channel_link", "MAX_CHANNEL_LINK"), ("max_api", "MAX_API_URL"),
           ("max_chat_id", "MAX_CHAT_ID")) if os.getenv(var)}
    env = {k: v.strip() for k, v in env.items()}
    if os.getenv("BOT_TOKEN") and os.getenv("MAX_TOKEN") and os.getenv("BOT_TOKEN").strip() != env["max_token"]:
        print("⚠️ Заданы и BOT_TOKEN, и MAX_TOKEN, и они разные — беру MAX_TOKEN. Лишний лучше удалить.")
    link = env.get("max_channel_link", "")
    if link and "max.ru/" not in link:                # «@name» или «name» -> https://max.ru/name
        env["max_channel_link"] = "https://max.ru/" + link.lstrip("@").strip("/")
    if env.get("max_chat_id"):
        try:
            env["max_chat_id"] = int(env["max_chat_id"])
        except ValueError:
            print("⚠️ MAX_CHAT_ID должен быть числом (например -80231555661308) — не использую.")
            del env["max_chat_id"]
    need = [(k, label) for k, label in CONFIG_FIELDS if LOGIN or k not in TG_FIELDS]
    bad = [k for k, _ in need if not field_ok(k, env.get(k) or cfg.get(k))]
    if bad and not interactive and __name__ == "__main__":     # хостинг: спросить некого
        print("Не хватает настроек: " + ", ".join(bad) + ". Задай переменные окружения: "
              "BOT_TOKEN (или MAX_TOKEN) — токен бота MAX, MAX_CHANNEL_LINK — ссылка на канал MAX.")
        sys.exit(1)
    if interactive and bad:
        if os.path.exists(CONFIG_FILE):
            print(f"В {CONFIG_FILE} неверные поля: {', '.join(bad)}. Исправим.")
        else:
            print(f"Не нашёл {CONFIG_FILE} рядом со скриптом. Если он у тебя есть — положи его в эту же папку "
                  f"и запусти заново. Если нет — заполним сейчас.")
        for key, label in need:
            while not field_ok(key, env.get(key) or cfg.get(key)):
                try:
                    v = input(f"{label}: ").strip()
                except EOFError:                     # консоли нет: данные — только в переменных окружения
                    print("Нет консоли, чтобы спросить настройки. Задай переменные окружения: "
                          "MAX_TOKEN — токен бота MAX, MAX_CHANNEL_LINK — ссылка на канал MAX "
                          f"(сейчас неверно или пусто: {', '.join(bad)}).")
                    sys.exit(1)
                if key == "phone":
                    v = norm_phone(v)
                if not field_ok(key, v):
                    print(f"  Не подходит. Нужно: {FIELD_HINTS[key]}")
                    continue
                cfg[key] = int(v) if key == "api_id" else v
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print("Сохранено в config.json")
    cfg.update(env)                                  # переменные окружения сильнее файла (в файл не пишутся)
    try:
        cfg["api_id"] = int(cfg["api_id"] or 0)
    except (TypeError, ValueError):
        cfg["api_id"] = 0
    cfg["phone"] = norm_phone(cfg["phone"])
    return cfg


CONFIG = load_config(interactive=(__name__ == "__main__" and not HOSTED))
API_ID, API_HASH, PHONE = CONFIG["api_id"], CONFIG["api_hash"], CONFIG["phone"]
TG_READY = all(field_ok(k, CONFIG.get(k)) for k in ("api_id", "api_hash")) and (
    field_ok("phone", CONFIG.get("phone")) or os.path.exists("session.session"))   # есть ключи для входа в Telegram
MAX_TOKEN = CONFIG["max_token"]
MAX_CHAT_ID = CONFIG["max_chat_id"]
MAX_CHANNEL_LINK = CONFIG["max_channel_link"]
TEST_REPOST_LAST = bool(CONFIG["test_repost_last"])     # True на один запуск: переопубликовать последние посты

STATE_FILE = "last_posts.json"         # последний id поста по каждому каналу
HISTORY_FILE = "posted_history.json"   # что уже опубликовано (для дублей)
SETTINGS_FILE = "settings.json"        # настройки из меню
QUEUE_FILE = "queue.json"              # очередь постов на публикацию
PUB_FILE = "pub_state.json"            # когда и что публиковали (для расписания)
MEDIA_DIR = "queue_media"              # фото постов, ждущих в очереди
STASH_FILE = "stash.json"              # тайник: мемы про запас
STASH_DIR = "stash_media"              # файлы из тайника
CARDS_FILE = "cards.json"              # карточки одобрения в чатах админов (чтобы их чистить)
THEMES_FILE = "themes.json"            # темы: свои каналы, свой канал MAX, свои настройки

# Стартовые значения. Дальше всё меняется через меню в MAX и хранится в settings.json
DEFAULTS = {
    "channels": ["robloxers", "roblox1", "robloxx", "Dark_Roblox", "RobloxRu_Official"],
    "paused": False,
    "interval": 60,                    # секунд между проверками
    "video_on": True,
    "video_mb": 250,                   # MAX принимает видео до 250 МБ
    "dedupe_on": True,
    "similarity": 0.75,                # похожесть текста (дубль по словам)
    "meaning": 0.50,                   # порог "по смыслу": меньше = ловит больше дублей
    "dedupe_hours": 72,                # сколько часов помним опубликованное
    "signature_on": True,
    "signature_text": "🕹️Роблокс мемы🕹️",
    # строки с этими словами удаляются целиком (подписи каналов-доноров)
    "old_signatures": ["Роблокс мемы"],
    # рекламные строки удаляются целиком
    "ad_keywords": ["funpay", "playerok", "плейрок", "плеерок", "робуксы на",
                    "купить robux", "купить робукс", "по самой низкой цене", "наш вк"],
    # расписание: посты не пачкой, а постепенно
    "queue_on": True,
    "gap_min": 60,                     # пауза между публикациями (минут)
    "burst_min": 30,                   # посты ОДНОГО канала с разницей до N минут выходят сразу вместе
    "burst_max": 3,                    # но не больше стольких подряд
    "max_age_h": 24,                   # пост, ждущий в очереди дольше, удаляется
    "later_min": 60,                   # кнопка «⏰ Через…» на карточке поста: через сколько минут выложить
    "auto_series": False,              # посты одного канала сами выходят пачкой (ВЫКЛ: именно это выкладывало «кучей»)
    "stash_on": True,                  # тайник: если очередь пуста, выкладывать из него
    "empty_warn_on": True,             # предупреждать админов, если на ближайший час нет постов
    # админы бота в MAX (первый — владелец); заполняется через меню
    "admins": [],
    "admin_names": {},
    "admin_chats": {},
    # ночной режим: в эти часы (по времени телефона/сервера) ничего не публикуется
    "night_on": False,
    "night_from": 0,
    "night_to": 8,
    # срочные посты (например со словом «официально») выходят без очереди
    "urgent_on": True,
    "urgent_words": ["официально"],
    "urgent_max_h": 3,                 # не больше стольких срочных в час
    # посты с этими словами не публикуются (конкурсы, розыгрыши, раздачи)
    "stop_words": ["конкурс", "розыгр", "разыгр", "giveaway", "раздач"],
    # автозамена слов: [["старое", "новое"], ...]
    "replacements": [],
    # ручное одобрение: бот присылает пост админам с кнопками
    "approval_on": True,
    # ежедневный отчёт админам
    "report_on": True,
    "report_hour": 21,
    # приоритет каналов: {"имя_канала": 2}; 1 — обычный, 2 — важный
    "priority": {},
}
# Последние строки поста, где ТОЛЬКО ссылки на эти сайты (футеры), удаляются.
FOOTER_DOMAINS = ["t.me", "telegram.me", "vk.com", "vk.ru", "vk.cc",
                  "funpay", "playerok", "boosty"]
# ================================================================

MAX_API = CONFIG["max_api"].rstrip("/")   # адрес API MAX
HEADERS = {"Authorization": MAX_TOKEN}

# MAX перешёл на российский сертификат, которого нет в Pydroid/Python,
# поэтому проверку сертификата отключаем.
VERIFY_SSL = bool(CONFIG["verify_ssl"])
if not VERIFY_SSL:
    urllib3.disable_warnings()
S = requests.Session()
S.verify = VERIFY_SSL
# при обрыве соединения запрос повторяется сам (только до установки соединения — дублей не будет)
try:
    _retry = Retry(total=3, connect=3, read=0, status=0, other=0, backoff_factor=1, allowed_methods=None)
except TypeError:                       # старый urllib3
    _retry = Retry(total=3, connect=3, read=0, status=0, backoff_factor=1, method_whitelist=False)
_adapter = HTTPAdapter(max_retries=_retry, pool_connections=10, pool_maxsize=20)
S.mount("https://", _adapter)
S.mount("http://", _adapter)

# t.me читаем отдельной сессией: быстро сдаёмся, если сайт недоступен (в РФ Telegram блокируют),
# и можно пустить только его через прокси (TG_PROXY=http://user:pass@host:port) — MAX идёт напрямую.
TG_PROXY = (CONFIG.get("tg_proxy") or os.environ.get("TG_PROXY") or "").strip()
WS = requests.Session()
WS.trust_env = False
if TG_PROXY:
    WS.proxies = {"http": TG_PROXY, "https": TG_PROXY}
try:
    _wretry = Retry(total=1, connect=1, read=0, status=0, other=0, backoff_factor=1, allowed_methods=None)
except TypeError:
    _wretry = Retry(total=1, connect=1, read=0, status=0, backoff_factor=1, method_whitelist=False)
WS.mount("https://", HTTPAdapter(max_retries=_wretry, pool_connections=10, pool_maxsize=20))
WS.mount("http://", HTTPAdapter(max_retries=_wretry, pool_connections=10, pool_maxsize=20))
TG_HOSTS = ["t.me", "telegram.me"]                   # если t.me не отвечает — то же самое через telegram.me


if __name__ == "__main__":
    setup_data_dir()                      # до загрузки настроек: подтянуть рабочий вход и данные

# ---------- файлы состояния и настройки ----------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


_WRITE_LOCK = threading.Lock()


def save_json(path, data):
    """Атомарная запись: если процесс убили посередине, файл не повреждается."""
    with _WRITE_LOCK:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)


GCFG = copy.deepcopy(DEFAULTS)           # общие настройки (и значения по умолчанию для старой темы)
GCFG.update(load_json(SETTINGS_FILE, {}))
# перенос владельца из старой версии настроек
if GCFG.get("owner_id") and GCFG["owner_id"] not in GCFG["admins"]:
    GCFG["admins"].insert(0, GCFG["owner_id"])
    if GCFG.get("owner_chat"):
        GCFG["admin_chats"][str(GCFG["owner_id"])] = GCFG["owner_chat"]

# Общие для всех тем. Всё остальное — настройки конкретной темы.
GLOBAL_KEYS = {"paused", "interval", "admins", "admin_names", "admin_chats",
               "report_on", "report_hour", "owner_id", "owner_chat"}
STAT_KEYS = ("published", "dups", "errors", "expired", "blocked", "rejected", "urgent", "stash")


class Theme:
    """Тема = свои каналы-источники + свой канал MAX + своя очередь, тайник, расписание и фильтры.
    Все темы работают одновременно; в меню ты выбираешь, какую настраиваешь."""

    def __init__(self, tid, name, link, chat_id=None, own=None, legacy=False):
        self.id, self.name, self.link, self.chat_id = tid, name, link, chat_id
        self.legacy = legacy                        # первая тема хранит данные в старых файлах
        self.own = own if own is not None else {}   # настройки темы
        if legacy:
            self.f = {"queue": QUEUE_FILE, "pub": PUB_FILE, "stash": STASH_FILE,
                      "state": STATE_FILE, "history": HISTORY_FILE}
        else:
            def sfx(base):
                root, ext = os.path.splitext(base)
                return f"{root}_{tid}{ext}"
            self.f = {"queue": sfx(QUEUE_FILE), "pub": sfx(PUB_FILE), "stash": sfx(STASH_FILE),
                      "state": sfx(STATE_FILE), "history": sfx(HISTORY_FILE)}
        self.prefix = "" if legacy else f"{tid}|"   # в id постов и пачек (чтобы кнопки находили тему)
        self.fs = "" if legacy else f"{tid}_"       # в именах временных файлов
        self.queue = load_json(self.f["queue"], [])
        self.pub = {"last_pub": 0, "ch_last_date": {}, "ch_last_pub": {}, "series": {}}
        self.pub.update(load_json(self.f["pub"], {}))
        self.stats = {k: 0 for k in STAT_KEYS}
        self.stats.update(self.pub.get("stats", {}))
        self.stash = load_json(self.f["stash"], [])
        self.saved_ids = load_json(self.f["state"], {})
        self.history = load_json(self.f["history"], [])
        self.last_ids = {}                          # последний обработанный пост по каждому каналу
        self.force = {"until": 0}                   # до этого времени публикуем без ожидания
        self.flags = {"stash_now": False, "test": False, "empty_at": 0}
        self.lock = None                            # asyncio.Lock, создаётся при запуске

    def to_json(self):
        return {"id": self.id, "name": self.name, "link": self.link, "chat_id": self.chat_id,
                "own": self.own, "legacy": self.legacy}


def _load_themes():
    data = load_json(THEMES_FILE, {})
    themes = [Theme(d["id"], d["name"], d.get("link", ""), d.get("chat_id"), d.get("own", {}),
                    d.get("legacy", False)) for d in data.get("themes", [])]
    if not themes:
        themes = [Theme("main", "Основная", MAX_CHANNEL_LINK, MAX_CHAT_ID, {}, legacy=True)]
    meta = {"active": data.get("active", {}), "last_report": data.get("last_report", "")}
    if not meta["last_report"]:
        meta["last_report"] = themes[0].pub.get("last_report", "")
    return themes, meta


THEMES, META = _load_themes()
CUR = contextvars.ContextVar("theme", default=None)   # тема, с которой сейчас работает код


def cur():
    return CUR.get() or THEMES[0]


@contextlib.contextmanager
def theme_ctx(t):
    tok = CUR.set(t)
    try:
        yield t
    finally:
        CUR.reset(tok)


def theme_by_id(tid):
    return next((t for t in THEMES if t.id == tid), None)


def theme_of_ref(ref):
    """Тема по id поста/пачки (у не-первых тем он начинается с «id|»)."""
    t = theme_by_id(ref.split("|", 1)[0]) if "|" in ref else None
    return t or THEMES[0]


def active_theme(uid):
    return theme_by_id(META["active"].get(str(uid))) or THEMES[0]


def theme_tag():
    return f" · 🎭 {cur().name}" if len(THEMES) > 1 else ""


class _ThemeCfg:
    """CFG[...]: общие ключи — из GCFG, остальное — из настроек текущей темы."""

    def __getitem__(self, k):
        if k in GLOBAL_KEYS:
            return GCFG[k]
        t = cur()
        if k not in t.own:
            t.own[k] = copy.deepcopy((GCFG if t.legacy else DEFAULTS)[k])
        return t.own[k]

    def __setitem__(self, k, v):
        if k in GLOBAL_KEYS:
            GCFG[k] = v
        else:
            cur().own[k] = v

    def get(self, k, default=None):
        try:
            return self[k]
        except KeyError:
            return default


class _ListProxy:
    """Список текущей темы (QUEUE, STASH) — ведёт себя как обычный list."""

    def __init__(self, attr):
        self._a = attr

    def _t(self):
        return getattr(cur(), self._a)

    def __iter__(self):
        return iter(self._t())

    def __len__(self):
        return len(self._t())

    def __bool__(self):
        return bool(self._t())

    def __contains__(self, x):
        return x in self._t()

    def __getitem__(self, i):
        return self._t()[i]

    def __setitem__(self, i, v):
        self._t()[i] = v

    def __getattr__(self, name):
        return getattr(self._t(), name)


class _DictProxy(_ListProxy):
    """Словарь текущей темы (PUB, STATS, LAST_IDS, FORCE, FLAGS)."""

    def __delitem__(self, k):
        del self._t()[k]


CFG = _ThemeCfg()
QUEUE = _ListProxy("queue")            # посты, ждущие публикации
STASH = _ListProxy("stash")            # тайник: [{id, text, photos, videos, ...}]
PUB = _DictProxy("pub")                # когда и что публиковали (для расписания)
STATS = _DictProxy("stats")
LAST_IDS = _DictProxy("last_ids")
FORCE = _DictProxy("force")
FLAGS = _DictProxy("flags")            # запросы из меню/команд для планировщика
BAD = {}                               # канал -> когда повторить попытку (не открылся)
STASH_LOCK = threading.Lock()
CARDS = load_json(CARDS_FILE, [])      # [{chat, mid, ref, del_at, t}]
CARDS_LOCK = threading.Lock()


def save_cfg():
    save_json(SETTINGS_FILE, GCFG)
    save_json(THEMES_FILE, {"themes": [t.to_json() for t in THEMES], "active": META["active"],
                            "last_report": META["last_report"]})


def save_queue():
    t = cur()
    save_json(t.f["queue"], t.queue)


def save_pub():
    t = cur()
    t.pub["stats"] = dict(t.stats)
    save_json(t.f["pub"], t.pub)


def save_stash():
    t = cur()
    with STASH_LOCK:
        save_json(t.f["stash"], t.stash)


def save_last_ids():
    t = cur()
    save_json(t.f["state"], t.last_ids)


def save_history():
    t = cur()
    t.history[:] = prune(t.history)        # в памяти тоже не копим лишнее
    save_json(t.f["history"], t.history)


def bump(key):
    """Счётчик для статуса и отчёта (хранится на диске)."""
    STATS[key] = STATS.get(key, 0) + 1
    save_pub()


def signature():
    if not CFG["signature_on"] or not CFG["signature_text"].strip():
        return ""
    link = cur().link
    return f"[{CFG['signature_text']}]({link})" if link else CFG["signature_text"]


def create_theme(name):
    n = 2
    while any(t.id == f"t{n}" for t in THEMES):
        n += 1
    t = Theme(f"t{n}", name, "")
    t.own["channels"] = []
    t.own["old_signatures"] = []
    t.own["signature_text"] = name[:60]
    THEMES.append(t)
    save_cfg()
    return t


def delete_theme(t):
    """Удаляет тему вместе с очередью и тайником. Первую (основную) удалить нельзя."""
    if t.legacy or t not in THEMES:
        return
    with theme_ctx(t):
        for it in list(QUEUE):
            drop_item(it)
        for it in list(STASH):
            stash_remove(it, save=False)
    THEMES.remove(t)
    for uid, tid in list(META["active"].items()):
        if tid == t.id:
            del META["active"][uid]
    for path in t.f.values():
        try:
            os.remove(path)
        except OSError:
            pass
    CUR.set(THEMES[0])
    save_cfg()


# ---------- чистка текста ----------
def fix_markdown(text: str) -> str:
    """Приводит разметку Telegram к виду, который понимает MAX:
    пробелы выносятся за звёздочки (иначе ** остаются в тексте), курсив __x__ -> _x_."""
    text = re.sub(r"\|\|(.+?)\|\|", r"\1", text, flags=re.S)       # спойлеры -> обычный текст

    def wrap(marker, out_marker):
        pat = re.compile(re.escape(marker) + r"(\s*)(.+?)(\s*)" + re.escape(marker), re.S)

        def repl(m):
            parts = [p.strip() for p in m.group(2).split("\n") if p.strip()]
            inner = "\n".join(out_marker + p + out_marker for p in parts)
            return m.group(1) + inner + m.group(3)
        return pat.sub(repl, text)

    text = wrap("**", "**")
    text = wrap("~~", "~~")
    text = wrap("__", "_")
    return text


LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")


def _is_footer_line(line: str) -> bool:
    """Строка состоит только из ссылок на t.me / vk / рекламные сайты."""
    links = LINK_RE.findall(line)
    if not links:
        return False
    if re.search(r"\w", LINK_RE.sub("", line)):   # есть обычный текст
        return False
    return all(any(d in url.lower() for d in FOOTER_DOMAINS) for _, url in links)


def apply_replacements(body: str) -> str:
    """Автозамена слов (ссылки не трогаем)."""
    if not CFG["replacements"] or not body:
        return body
    parts = re.split(r"(\]\([^)]*\)|https?://\S+)", body)
    for i in range(0, len(parts), 2):
        for old, new in CFG["replacements"]:
            parts[i] = re.sub(r"(?<!\w)" + re.escape(old) + r"(?!\w)", lambda _m, n=new: n,
                              parts[i], flags=re.I)
    return "".join(parts)


def blocked_word(body: str):
    """Стоп-слово, из-за которого пост не публикуется (или None)."""
    low = body.lower()
    for w in CFG["stop_words"]:
        if w.strip() and w.lower() in low:
            return w
    return None


def is_urgent(body: str) -> bool:
    low = body.lower()
    return CFG["urgent_on"] and any(w.strip() and w.lower() in low for w in CFG["urgent_words"])


def clean_text(text: str, with_signature: bool = True) -> str:
    """Убирает чужие подписи, футеры, рекламу, @упоминания и t.me-ссылки."""
    body = ""
    if text:
        # премиум-эмодзи и ссылки на людей Telegram -> просто текст
        text = re.sub(r"\[([^\]]*)\]\((?:emoji/|tg://)[^)]*\)", r"\1", text)
        lines = []
        for line in text.split("\n"):
            low = line.lower()
            if any(sig.lower() in low for sig in CFG["old_signatures"]):
                continue  # подпись канала-донора
            if any(ad.lower() in low for ad in CFG["ad_keywords"]):
                continue  # реклама
            lines.append(line)
        # футеры в конце поста (строки только из ссылок на каналы/ВК/рекламу)
        while lines and (not lines[-1].strip() or _is_footer_line(lines[-1])):
            lines.pop()
        body = "\n".join(lines)
        body = re.sub(r"\[([^\]]*)\]\(https?://t\.me/[^)]*\)", r"\1", body)
        body = re.sub(r"https?://t\.me/\S+", "", body)   # голые ссылки t.me
        body = re.sub(r"@\w+", "", body)                   # @упоминания
        body = re.sub(r"[ \t]+\n", "\n", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        body = apply_replacements(body)
    body = fix_markdown(body)
    if not with_signature:
        return body
    return (body + "\n\n" + signature()).strip()


# ---------- защита от дублей ----------
def norm(text: str) -> str:
    """Текст без ссылок, знаков и эмодзи — для сравнения постов."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = text.lower().replace("ё", "е")
    return " ".join(re.findall(r"\w+", text))[:1000]


STOP_WORDS = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был
него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы
тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого
какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при
наконец два об другой хоть после над больше тот через эти нас про всего них какая много разве три эту
моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между
это стали стал будут будет также уже пока ещё новый новая новые the a an of to in is are for on and
with at by from that this it be как вам такой список""".split())
# слишком общие для темы слова: учитываются, но с малым весом
GENERIC = {"roblox", "роблокс", "robux", "робукс", "офици", "новос", "обнов", "добав",
           "игра", "игр", "игры", "ивент", "событ", "анонс", "стало", "извес"}


@functools.lru_cache(maxsize=4096)
def terms(text: str):
    """Смысловые слова поста: без мусора, с грубой обрезкой окончаний (результат кэшируется)."""
    out = set()
    for w in re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е")):
        if w in STOP_WORDS or (len(w) < 2 and not w.isdigit()):
            continue
        if "а" <= w[0] <= "я":
            w = w[:5]              # "хэллоуинском" и "хэллоуинскую" -> "хэллоу"
        out.add(w)
    return frozenset(out)


def meaning_match(a: str, b: str, df, n_hist) -> bool:
    """Два поста про одно и то же? Сравниваем веса общих редких слов.
    df — Counter: в скольких постах истории встречается слово; n_hist — размер истории."""
    ta, tb = terms(a), terms(b)
    if len(ta) < 3 or len(tb) < 3:
        return False
    n = n_hist + 2

    def weight(w):
        d = df.get(w, 0) + (w in ta) + (w in tb)
        idf = math.log((n + 1) / (d + 0.5)) + 1
        return idf * (0.3 if w in GENERIC else 1.0)

    shared = ta & tb
    if len([w for w in shared if w not in GENERIC]) < 2:
        return False               # нужно минимум 2 общих "не общих" слова
    small = ta if len(ta) <= len(tb) else tb
    total = sum(weight(w) for w in small)
    common = sum(weight(w) for w in shared)
    return total > 0 and common / total >= CFG["meaning"]


def similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) < 25:        # слишком короткие — только точное совпадение
        return False
    sm = SequenceMatcher(None, a, b)
    if sm.real_quick_ratio() >= CFG["similarity"] and sm.quick_ratio() >= CFG["similarity"] \
            and sm.ratio() >= CFG["similarity"]:
        return True
    wa, wb = set(a.split()), set(b.split())
    return len(wa & wb) / max(1, len(wa | wb)) >= 0.7


def find_duplicate(entry, history):
    """Возвращает запись истории, на которую похож пост, либо None."""
    norms = [x.get("norm", "") for x in history]
    df = None
    for h in history:
        if entry["fwd"] and set(entry["fwd"]) & set(h.get("fwd", [])):
            return h                     # один и тот же пост переслан из одного оригинала
        # посты почти без текста сравниваем по самой картинке/видео
        if len(entry["norm"]) < 25 and entry["img"] and set(entry["img"]) & set(h.get("img", [])):
            return h
        # "alts" — как другие каналы пересказывали тот же пост (бот их запоминает)
        for text in [h.get("norm", "")] + h.get("alts", []):
            if similar(entry["norm"], text):
                return h
            if df is None:               # считаем один раз на пост, а не на каждую пару
                df = Counter(w for n in norms for w in terms(n))
            if meaning_match(entry["norm"], text, df, len(norms)):
                return h
    return None


def is_duplicate(entry, history):
    return find_duplicate(entry, history) is not None


def fwd_key(m):
    f = getattr(m, "fwd_from", None)
    if f and getattr(f, "channel_post", None) and getattr(f, "from_id", None):
        cid = getattr(f.from_id, "channel_id", None)
        if cid:
            return f"{cid}:{f.channel_post}"
    return None


def prune(history):
    limit = time.time() - CFG["dedupe_hours"] * 3600
    return [h for h in history if h.get("t", 0) >= limit][-300:]


# ---------- MAX API ----------
def _collect_chat_ids(obj, found):
    """Достаём id только КАНАЛОВ из событий MAX (личные чаты игнорируем)."""
    if isinstance(obj, dict):
        if isinstance(obj.get("chat_id"), int) and (
                obj.get("chat_type") == "channel" or obj.get("is_channel") is True):
            found.append(obj["chat_id"])
        for v in obj.values():
            _collect_chat_ids(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _collect_chat_ids(v, found)


def _save_channel_id(cid):
    with open("channel_id.txt.tmp", "w") as f:
        f.write(str(cid))
    os.replace("channel_id.txt.tmp", "channel_id.txt")


def find_chat_id_safe():
    """id канала MAX без ожидания и без падений: файл -> ссылка. None — пока не знаем (привяжем по сообщению в канале)."""
    try:
        with open("channel_id.txt") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        with contextlib.suppress(OSError):
            os.remove("channel_id.txt")
    try:
        me = S.get(f"{MAX_API}/me", headers=HEADERS, timeout=30)
        print("Проверка токена /me ->", me.status_code, me.text[:150])
        if me.status_code in (401, 403):
            print("⚠️ Токен MAX не принят — проверь BOT_TOKEN / max_token.")
    except Exception as e:
        print("MAX: нет связи при запуске:", type(e).__name__, e)
    slug = MAX_CHANNEL_LINK.rstrip("/").split("/")[-1]
    for cand in (slug, "@" + slug):
        try:
            r = S.get(f"{MAX_API}/chats/{cand}", headers=HEADERS, timeout=30)
            print("Поиск по ссылке", cand, "->", r.status_code, r.text[:150])
            if r.status_code == 200 and isinstance(r.json().get("chat_id"), int):
                cid = r.json()["chat_id"]
                _save_channel_id(cid)
                return cid
        except Exception as e:
            print("Поиск канала по ссылке:", type(e).__name__, e)
    return None


def find_chat_id():
    """Определяем id канала MAX: из файла, по ссылке или через /updates."""
    cid = find_chat_id_safe()
    if cid:
        return cid

    print()
    print("=== Не нашёл канал по ссылке. Ловлю id через события бота. ===")
    print("Сейчас НАПИШИ ЛЮБОЕ СООБЩЕНИЕ В СВОЁМ КАНАЛЕ MAX (у тебя 2 минуты).")
    print("Если не сработает — удали бота из админов канала и добавь заново.")
    deadline = time.time() + 120
    marker = None
    while time.time() < deadline:
        params = {"timeout": 20, "limit": 50}
        if marker:
            params["marker"] = marker
        r = S.get(f"{MAX_API}/updates", headers=HEADERS, params=params, timeout=40)
        if r.status_code != 200:
            print("/updates ->", r.status_code, r.text[:150])
            time.sleep(3)
            continue
        data = r.json()
        marker = data.get("marker", marker)
        found = []
        _collect_chat_ids(data.get("updates", []), found)
        if found:
            print("Нашёл канал, chat_id:", found)
            cid = found[0]
            _save_channel_id(cid)
            return cid
    raise RuntimeError("Не удалось определить chat_id. Пришли мне вывод выше.")


def resolve_chat_id(link):
    """id канала MAX по ссылке (бот должен быть в канале). None — не нашли."""
    slug = link.rstrip("/").split("/")[-1]
    for cand in (slug, "@" + slug):
        try:
            r = S.get(f"{MAX_API}/chats/{cand}", headers=HEADERS, timeout=30)
            if r.status_code == 200 and "chat_id" in r.json():
                return r.json()["chat_id"]
        except Exception as e:
            print("Поиск канала по ссылке:", e)
    return None


def upload_image(path):
    r = S.post(f"{MAX_API}/uploads", params={"type": "image"},
               headers=HEADERS, timeout=30)
    r.raise_for_status()
    url = r.json()["url"]
    with open(path, "rb") as f:
        up = S.post(url, files={"data": f}, timeout=120)
    up.raise_for_status()
    data = up.json()
    # токен лежит в photos -> {id: {token}}
    photos = data.get("photos")
    if photos:
        return next(iter(photos.values()))["token"]
    return data["token"]


def upload_video(path):
    """У видео токен выдаётся сразу вместе со ссылкой для загрузки.
    Большие файлы шлём потоком (а не multipart): multipart читает весь файл в память — на телефоне падает."""
    r = S.post(f"{MAX_API}/uploads", params={"type": "video"},
               headers=HEADERS, timeout=30)
    r.raise_for_status()
    info = r.json()
    url, name = info["url"], os.path.basename(path)
    size = os.path.getsize(path)

    def multipart():
        with open(path, "rb") as f:
            return S.post(url, files={"data": (name, f, "video/mp4")}, timeout=900)

    def stream():
        headers = {
            "Content-Disposition": f'attachment; filename="{name}"',
            "Content-Type": "application/octet-stream",
            "Content-Range": f"bytes 0-{size - 1}/{size}",
            "Connection": "keep-alive",
        }
        with open(path, "rb") as f:
            return S.post(url, data=f, headers=headers, timeout=900)

    order = (stream, multipart) if size > 40 * 1024 * 1024 else (multipart, stream)
    last = None
    for attempt in order:
        try:
            up = attempt()
        except Exception as e:
            print(f"  загрузка видео ({attempt.__name__}) не прошла: {e}")
            last = str(e)
            continue
        if up.status_code < 300:
            return info["token"]
        last = f"{up.status_code} {up.text[:150]}"
        print(f"  загрузка видео ({attempt.__name__}) не прошла: {last}")
    raise RuntimeError(f"Загрузка видео не удалась: {last}")


def _check_attachments(r, expected):
    """MAX мог принять сообщение, но потерять вложения. Не молчим об этом."""
    if not expected:
        return
    try:
        got = len(((r.json().get("message") or {}).get("body") or {}).get("attachments") or [])
    except Exception:
        return
    if got < expected:
        journal(f"⚠️ MAX принял сообщение, но вложений в нём {got} из {expected}")
        notify(f"⚠️ В MAX пост вышел без части вложений ({got} из {expected}). Если повторяется — пришли bot.log.")


def send_max(chat_id, text, image_paths, video_path=None):
    attachments = []
    for p in image_paths:                 # путь к файлу или {"token": ...} (из тайника)
        token = p["token"] if isinstance(p, dict) else upload_image(p)
        attachments.append({"type": "image", "payload": {"token": token}})
    if video_path:
        token = video_path["token"] if isinstance(video_path, dict) else upload_video(video_path)
        attachments.append({"type": "video", "payload": {"token": token}})
    body = {"format": "markdown"}
    if text:
        body["text"] = text
    if attachments:
        body["attachments"] = attachments
    tries = 100 if video_path else 8      # видео MAX обрабатывает дольше (до ~5 минут)
    for _ in range(tries):
        try:
            r = S.post(f"{MAX_API}/messages", params={"chat_id": chat_id},
                       headers=HEADERS, json=body, timeout=60)
        except requests.exceptions.ReadTimeout:
            # MAX мог принять сообщение, но не успел ответить — повтор дал бы дубль
            print("  MAX не ответил вовремя, считаю сообщение отправленным")
            return
        if r.status_code == 200:
            _check_attachments(r, len(attachments))
            return
        if "not.ready" in r.text or "not.processed" in r.text:
            time.sleep(3)
            continue
        raise RuntimeError(f"MAX ошибка {r.status_code}: {r.text}")
    raise RuntimeError("Вложение так и не стало готово")


MAX_TEXT = 3900


def split_text(text, limit=MAX_TEXT):
    """MAX принимает до 4000 символов в сообщении: длинный пост режем по абзацам, потом по строкам и словам."""
    text = text or ""
    if len(text) <= limit:
        return [text]
    out = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


def build_parts(text, images, videos):
    """Текст уходит с первым вложением; фото — одним сообщением, видео — по одному."""
    chunks = split_text(text)
    text, extra = chunks[0], chunks[1:]
    parts = []
    for k in range(0, len(images), 10):      # в одном сообщении MAX — до 10 вложений
        parts.append((text, images[k:k + 10], None))
        text = ""
    for v in videos:
        parts.append((text, [], v))
        text = ""
    if not parts:
        parts.append((text, [], None))
    parts += [(c, [], None) for c in extra]  # хвост длинного текста — отдельными сообщениями
    return parts


def send_post(chat_id, text, images, videos):
    for t, imgs, v in build_parts(text, images, videos):
        send_max(chat_id, t, imgs, v)


# ---------- ВРЕМЯ, НОЧЬ, ОТЧЁТ ----------
def local_hour():
    return time.localtime(time.time()).tm_hour


def in_night():
    if not CFG["night_on"]:
        return False
    a, b, h = CFG["night_from"], CFG["night_to"], local_hour()
    if a == b:
        return False
    return a <= h < b if a < b else (h >= a or h < b)


def after_night(ts):
    """Время попадает на ночной режим — переносим на его конец (например, 21:30 при ночи 21–8 -> 8:00 утра)."""
    if not CFG["night_on"] or CFG["night_from"] == CFG["night_to"]:
        return ts
    a, b = CFG["night_from"], CFG["night_to"]
    lt = time.localtime(ts)
    h = lt.tm_hour
    if not (a <= h < b if a < b else (h >= a or h < b)):
        return ts
    end = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + (1 if h >= b else 0), b, 0, 0, 0, 0, -1))
    return max(end, ts)


def make_report(title=None):
    base = PUB.get("rep_base", {})

    def d(k):
        return STATS.get(k, 0) - base.get(k, 0)
    pend = sum(1 for x in QUEUE if x.get("status") == "pending")
    return ("📈 Отчёт" + (f" · 🎭 {title}" if title else "") + "\n\n"
            f"Опубликовано: {d('published')} (срочных: {d('urgent')}, из тайника: {d('stash')})\n"
            f"Дублей пропущено: {d('dups')}\n"
            f"Отсеяно стоп-словами: {d('blocked')}\n"
            f"Отклонено вручную: {d('rejected')}\n"
            f"Ошибок: {d('errors')}\n\n"
            f"Сейчас в очереди: {len(QUEUE)} (ждут одобрения: {pend}) · в тайнике: {len(STASH)}")


def send_report():
    """Один отчёт админам: по каждой теме отдельный блок."""
    parts = []
    for t in list(THEMES):
        with theme_ctx(t):
            parts.append(make_report(t.name if len(THEMES) > 1 else None))
            PUB["rep_base"] = dict(STATS)
            save_pub()
    META["last_report"] = time.strftime("%Y-%m-%d", time.localtime(time.time()))
    save_cfg()
    notify("\n\n———\n\n".join(parts), tag=False)


# ---------- МЕНЮ В MAX ----------
PENDING = {}           # user_id -> действие, для которого ждём текст
INVITES = {}           # код приглашения админа -> время окончания
LAST_INVITE = {"code": None}
BIND = {"theme": None}      # тема, которая ждёт привязки канала MAX (первое сообщение в канале)
CLAIM_CODE = None
CONSOLE_CODE = {"code": None}   # код админа из консоли: у кого есть доступ к консоли, тот может стать админом


def new_console_code():
    """Новый одноразовый код админа — показывается в консоли при каждом запуске и после каждого использования."""
    code = str(random.randint(100000, 999999))
    CONSOLE_CODE["code"] = code
    print("=" * 40)
    print("КОД АДМИНА: отправь его боту в MAX с любого аккаунта —")
    print("этот аккаунт станет админом бота:", code)
    print("=" * 40)


def B(text, payload):
    return {"type": "callback", "text": text, "payload": payload}


def api_send(chat_id, text, rows=None, image_token=None):
    body = {"text": text}
    atts = []
    if image_token:
        atts.append({"type": "image", "payload": {"token": image_token}})
    if rows:
        atts.append({"type": "inline_keyboard", "payload": {"buttons": rows}})
    if atts:
        body["attachments"] = atts
    r = S.post(f"{MAX_API}/messages", params={"chat_id": chat_id},
               headers=HEADERS, json=body, timeout=30)
    if r.status_code != 200:
        print("Меню: ошибка отправки", r.status_code, r.text[:150])
        return None
    try:
        return ((r.json().get("message") or {}).get("body") or {}).get("mid")
    except Exception:
        return None


def add_card(chat, mid, ref):
    """Запоминает карточку (сообщение с кнопками), чтобы потом убрать её из чата."""
    if not mid:
        return
    with CARDS_LOCK:
        CARDS.append({"chat": chat, "mid": mid, "ref": ref, "del_at": None, "t": time.time()})
        save_json(CARDS_FILE, CARDS)


def resolve_cards(refs, keep_chat=None, keep_sec=300):
    """Пост одобрен/отклонён. У остальных админов карточку убираем сразу,
    у того, кто нажал (keep_chat), — через 5 минут (пусть успеет увидеть итог)."""
    refs = set(refs)
    now = time.time()
    with CARDS_LOCK:
        for c in CARDS:
            if c["ref"] in refs and c.get("del_at") is None:
                mine = keep_chat is not None and c["chat"] == keep_chat
                c["del_at"] = now + keep_sec if mine else now
        save_json(CARDS_FILE, CARDS)


def delete_msg(mid):
    r = S.delete(f"{MAX_API}/messages", params={"message_id": mid}, headers=HEADERS, timeout=30)
    if r.status_code != 200:
        print("Карточки: не удалось убрать", r.status_code, r.text[:100])
    return r.status_code == 200


def purge_cards():
    """Удаляет карточки, у которых подошло время. Забытые (никто не нажал) — через 3 суток."""
    now = time.time()
    with CARDS_LOCK:
        for c in CARDS:
            if c.get("del_at") is None and now - c.get("t", now) > 3 * 86400:
                c["del_at"] = now
        due = [c for c in CARDS if c.get("del_at") is not None and c["del_at"] <= now]
    for c in due:
        try:
            delete_msg(c["mid"])
        except Exception as e:
            print("Карточки: ошибка удаления:", e)
    if due:
        with CARDS_LOCK:
            CARDS[:] = [c for c in CARDS if c not in due]
            save_json(CARDS_FILE, CARDS)


def api_answer(callback_id, text, rows):
    atts = [{"type": "inline_keyboard", "payload": {"buttons": rows}}] if rows else []
    msg = {"text": text, "attachments": atts}
    r = S.post(f"{MAX_API}/answers", params={"callback_id": callback_id},
               headers=HEADERS, json={"message": msg}, timeout=30)
    if r.status_code != 200:                      # запасной вариант: id в теле запроса
        r = S.post(f"{MAX_API}/answers", headers=HEADERS, timeout=30,
                   json={"callback_id": callback_id, "message": msg})
    if r.status_code != 200:
        print("Меню: ошибка ответа", r.status_code, r.text[:150])


def preview_text(it):
    text = (it["text"] + "\n\n" + signature()).strip()
    if len(text) > 800:
        text = text[:800] + "…"
    media = []
    if it.get("photos"):
        media.append(f"фото: {len(it['photos'])}")
    if it.get("vids"):
        media.append(f"видео: {len(it['vids'])}")
    head = f"✋ Новый пост из @{it['ch']}{theme_tag()}" + (" ⚡ срочный" if it.get("urgent") else "")
    return f"{head}\n({', '.join(media) or 'только текст'})\n\n{text}"


def send_preview(it, rows=None, chats=None):
    """Присылает админам пост на одобрение (с кнопками rows)."""
    msg = preview_text(it)
    token = None
    if it.get("photos") and os.path.exists(it["photos"][0]):
        try:
            token = upload_image(it["photos"][0])
        except Exception as e:
            print("Предпросмотр: фото не загрузилось:", e)
    for chat in (chats or set(CFG["admin_chats"].values())):
        try:
            add_card(chat, api_send(chat, msg, rows, token), it["id"])
        except Exception as e:
            print("Предпросмотр: ошибка:", e)


def send_previews(items):
    """Новые посты ОДНОГО канала за одну проверку.
    Один пост — карточка с кнопками «Сейчас» / «Через …» / «Пропустить».
    Несколько — отдельные превью + ОДНА карточка с кнопками на всю пачку."""
    items = [x for x in items if x in QUEUE and x.get("status") == "pending"]
    if not items:
        return
    later = f"⏰ Через {fmt_min(CFG['later_min'])}"
    if len(items) == 1:
        it = items[0]
        send_preview(it, [[B("✅ Сейчас", f"ap:now:{it['id']}"), B(later, f"ap:later:{it['id']}")],
                          [B("❌ Пропустить", f"ap:no:{it['id']}")]])
        return
    gid = f"{cur().prefix}g{int(time.time() * 1000)}"
    for it in items:
        it["grp"] = gid
    save_queue()
    lines = []
    for i, it in enumerate(items):
        send_preview(it, [[B("❌ Убрать этот", f"ap:no:{it['id']}")]])
        snip = (it.get("text") or "(без текста)")[:50].replace("\n", " ")
        lines.append(f"{i + 1}. {snip}")
    text = (f"📦 Пачка из @{items[0]['ch']}{theme_tag()}: постов {len(items)}\n\n" + "\n".join(lines)
            + "\n\nОдна кнопка на всю пачку:")
    rows = [[B("✅ Все сейчас", f"ap:gnow:{gid}")],
            [B(f"⏰ Все вместе через {fmt_min(CFG['later_min'])}", f"ap:glater:{gid}")],
            [B("❌ Пропустить все", f"ap:gno:{gid}")]]
    for chat in set(CFG["admin_chats"].values()):
        try:
            add_card(chat, api_send(chat, text, rows), gid)
        except Exception as e:
            print("Предпросмотр: ошибка:", e)


def is_admin(uid):
    return uid in CFG["admins"]


def is_owner(uid):
    return bool(CFG["admins"]) and CFG["admins"][0] == uid


def notify(text, tag=True):
    if tag and len(THEMES) > 1:
        text = f"🎭 {cur().name}\n{text}"
    for chat in set(CFG["admin_chats"].values()):
        try:
            api_send(chat, text)
        except Exception as e:
            print("notify:", e)


def on_off(v):
    return "ВКЛ ✅" if v else "ВЫКЛ ⛔"


def fmt_iv(s):
    return f"{s} сек" if s < 60 else f"{s // 60} мин"


def fmt_min(m):
    m = int(m)
    if m < 60:
        return f"{m} мин"
    return f"{m // 60} ч" + (f" {m % 60} мин" if m % 60 else "")


def fmt_words(words):
    return ", ".join(words) if words else "—"


def next_pub_text():
    if not QUEUE:
        return "очередь пуста"
    gap = CFG["gap_min"] * 60 if CFG["queue_on"] else 0
    left = PUB["last_pub"] + gap - time.time()
    return "можно публиковать сразу" if left <= 0 else f"через {fmt_min(math.ceil(left / 60))}"


def screen(name, uid=None):
    back = [B("◀️ В меню", "m:main")]
    if name == "ch":
        chs = CFG["channels"]
        text = "📡 Каналы-источники\n\n" + ("\n".join(f"{i + 1}. @{c}" for i, c in enumerate(chs)) or "список пуст") \
            + "\n\nНажми на канал, чтобы убрать его."
        rows = [[B(f"❌ {c}", f"a:chdel:{i}")] for i, c in enumerate(chs)]
        rows += [[B("➕ Добавить канал", "p:chadd")], back]
    elif name == "vid":
        text = (f"🎬 Видео и GIF: {on_off(CFG['video_on'])}\n"
                f"Максимальный размер: {CFG['video_mb']} МБ\n\n"
                "Видео больше лимита пропускаются вместе с постом (MAX принимает до 250 МБ).")
        rows = [[B(f"🎬 Видео: {on_off(CFG['video_on'])}", "a:vid")],
                [B("➖ 50 МБ", "a:vmb:-50"), B("➕ 50 МБ", "a:vmb:50")],
                [B("➖ 10 МБ", "a:vmb:-10"), B("➕ 10 МБ", "a:vmb:10")], back]
    elif name == "dd":
        text = (f"🔁 Защита от дублей: {on_off(CFG['dedupe_on'])}\n"
                f"Порог по смыслу: {CFG['meaning']:.2f} (меньше = ловит больше дублей)\n"
                f"Память: {CFG['dedupe_hours']} ч")
        rows = [[B(f"🔁 Дубли: {on_off(CFG['dedupe_on'])}", "a:dd")],
                [B("🔍 Ловить больше", "a:ddm:-0.05"), B("🔍 Ловить меньше", "a:ddm:0.05")],
                [B(f"🧠 Память: {CFG['dedupe_hours']} ч (сменить)", "a:ddh")], back]
    elif name == "fl":
        text = ("🧹 Фильтры\n\n"
                f"Рекламные слова (строка удаляется):\n{fmt_words(CFG['ad_keywords'])}\n\n"
                f"Подписи доноров (строка удаляется):\n{fmt_words(CFG['old_signatures'])}")
        rows = [[B("➕ Реклама", "p:adda"), B("➖ Реклама", "p:dela")],
                [B("➕ Подпись донора", "p:addo"), B("➖ Подпись донора", "p:delo")],
                [B("🚫 Стоп-слова", "m:sw"), B("🔄 Автозамена", "m:rw")], back]
    elif name == "sg":
        text = (f"✍️ Моя подпись: {on_off(CFG['signature_on'])}\n"
                f"Текст: {CFG['signature_text']}\n"
                "Подпись — кликабельная ссылка на твой канал.")
        rows = [[B(f"✍️ Подпись: {on_off(CFG['signature_on'])}", "a:sg")],
                [B("✏️ Изменить текст", "p:sgtext")], back]
    elif name == "iv":
        text = f"⏱ Как часто проверять каналы на новые посты\nСейчас: каждые {fmt_iv(CFG['interval'])}"
        rows = [[B("30 сек", "a:iv:30"), B("1 мин", "a:iv:60"), B("2 мин", "a:iv:120")],
                [B("5 мин", "a:iv:300"), B("10 мин", "a:iv:600")], back]
    elif name == "q":
        text = (f"⏳ Расписание публикаций: {on_off(CFG['queue_on'])}\n\n"
                f"Пауза между публикациями: {fmt_min(CFG['gap_min'])}\n"
                f"Авто-серии: {on_off(CFG['auto_series'])} — посты одного канала с разницей до "
                f"{fmt_min(CFG['burst_min'])} сами выходят вместе (не больше {CFG['burst_max']} подряд)\n"
                f"Кнопка «⏰ Через…» на карточке поста: {fmt_min(CFG['later_min'])}\n"
                f"Старые посты удаляются из очереди через {CFG['max_age_h']} ч\n\n"
                f"В очереди: {len(QUEUE)} · следующая публикация: {next_pub_text()}\n\n"
                "Если расписание выключено, посты выходят сразу, как появляются.")
        rows = [[B(f"⏳ Расписание: {on_off(CFG['queue_on'])}", "a:qon")],
                [B("➖15 мин", "a:gap:-15"), B("➕15 мин", "a:gap:15"), B("✏️ Своя", "p:gapx")],
                [B("30 мин", "a:gapset:30"), B("1 час", "a:gapset:60"), B("2 часа", "a:gapset:120")],
                [B(f"🔗 Авто-серии: {on_off(CFG['auto_series'])}", "a:ser")],
                [B(f"📭 Предупреждать о пустой очереди: {on_off(CFG['empty_warn_on'])}", "a:ewarn")],
                [B("⏰ «Через…»: −15 мин", "a:later:-15"), B("+15 мин", "a:later:15")],
                [B("🔗 Окно серии −5", "a:bur:-5"), B("+5", "a:bur:5"), B("✏️ Своё", "p:burx")],
                [B("Серия до: −1", "a:bmax:-1"), B("+1", "a:bmax:1")],
                [B(f"🗑 Хранить пост до {CFG['max_age_h']} ч (сменить)", "a:age")],
                [B("📋 Очередь", "m:ql"), B("⏩ Выложить всё сейчас", "a:qnow")],
                [B("🌙 Ночь", "m:nt"), B("⚡ Срочные", "m:ur"), B("⭐ Приоритет", "m:pr")], back]
    elif name == "ql":
        now = time.time()
        items = sorted(QUEUE, key=lambda x: x["date"])[:10]
        lines = []
        for i, it in enumerate(items):
            snip = (it.get("text") or "(без текста)")[:40].replace("\n", " ")
            mark = ({"pending": "✋", "stuck": "⚠️"}.get(it.get("status"), "")) + ("⚡" if it.get("urgent") else "")
            if it.get("at", 0) > now:
                when = "выйдет в " + time.strftime("%H:%M", time.localtime(it["at"]))
            else:
                when = f"ждёт {fmt_min((now - it['enq']) / 60)}"
            lines.append(f"{i + 1}. {mark}@{it['ch']} · {when} · {snip}")
        text = (f"📋 Очередь ({len(QUEUE)})\nСледующая публикация: {next_pub_text()}\n\n"
                + ("\n".join(lines) or "пока пусто"))
        rows = [[B("⏩ Выложить всё сейчас", "a:qnow")], [B("🗑 Очистить очередь", "a:qclr")],
                [B("◀️ Назад", "m:q")]]
    elif name == "nt":
        text = (f"🌙 Ночной режим: {on_off(CFG['night_on'])}\n"
                f"Не публикую с {CFG['night_from']:02d}:00 до {CFG['night_to']:02d}:00\n"
                f"Сейчас на телефоне/сервере: {time.strftime('%H:%M', time.localtime(time.time()))}\n\n"
                "Ночью посты копятся в очереди и выходят утром по расписанию. "
                "Кнопка «Выложить всё сейчас» и тест работают и ночью.")
        rows = [[B(f"🌙 Ночной режим: {on_off(CFG['night_on'])}", "a:night")],
                [B("С: −1 ч", "a:nfrom:-1"), B("С: +1 ч", "a:nfrom:1")],
                [B("До: −1 ч", "a:nto:-1"), B("До: +1 ч", "a:nto:1")], [B("◀️ Назад", "m:q")]]
    elif name == "ur":
        text = (f"⚡ Срочные посты: {on_off(CFG['urgent_on'])}\n\n"
                f"Слова-триггеры: {fmt_words(CFG['urgent_words'])}\n"
                f"Не больше {CFG['urgent_max_h']} срочных в час\n\n"
                "Пост со словом-триггером выходит сразу, не ждёт очереди и паузы. "
                "Посты со стоп-словами (конкурсы и т.п.) не публикуются в любом случае.")
        rows = [[B(f"⚡ Срочные: {on_off(CFG['urgent_on'])}", "a:urg")],
                [B("➕ Слово", "p:addur"), B("➖ Слово", "p:delur")],
                [B("Лимит в час: −1", "a:urgmax:-1"), B("+1", "a:urgmax:1")], [B("◀️ Назад", "m:q")]]
    elif name == "pr":
        chs = CFG["channels"]
        text = ("⭐ Приоритет каналов\n\nВажные каналы (⭐) выходят из очереди раньше: "
                "их посты считаются на 3 часа «старше». Нажми на канал, чтобы переключить.\n\n"
                + ("\n".join(f"{'⭐' if CFG['priority'].get(c, 1) == 2 else '▫️'} @{c}" for c in chs) or "каналов нет"))
        rows = [[B(f"{'⭐' if CFG['priority'].get(c, 1) == 2 else '▫️'} {c}", f"a:prio:{i}")] for i, c in enumerate(chs)]
        rows.append([B("◀️ Назад", "m:q")])
    elif name == "sw":
        text = ("🚫 Стоп-слова\n\nПосты, где есть любое из этих слов, не публикуются "
                "(по умолчанию — конкурсы, розыгрыши, раздачи).\n\n" + fmt_words(CFG["stop_words"]))
        rows = [[B("➕ Добавить", "p:addsw"), B("➖ Убрать", "p:delsw")], [B("◀️ Фильтры", "m:fl")]]
    elif name == "rw":
        pairs = [f"{o} → {n}" for o, n in CFG["replacements"]]
        text = ("🔄 Автозамена слов\n\nСлова в постах заменяются на твои (регистр не важен, ссылки не "
                "трогаются). Заменяется слово целиком, формы слова («робукс», «робуксы») добавляй отдельно.\n\n" + ("\n".join(pairs) or "пока пусто"))
        rows = [[B("➕ Добавить", "p:addrw"), B("➖ Убрать", "p:delrw")], [B("◀️ Фильтры", "m:fl")]]
    elif name == "ap":
        pend = sum(1 for x in QUEUE if x.get("status") == "pending")
        text = (f"✋ Ручное одобрение: {on_off(CFG['approval_on'])}\n\n"
                "Бот присылает каждый новый пост админам с кнопками «Сейчас», «Через …» и «Пропустить». "
                "Если из канала пришло сразу несколько постов — одна карточка на всю пачку. "
                "Когда убедишься, что всё работает правильно, выключи — посты пойдут сами по расписанию.\n\n"
                f"Ждут одобрения: {pend}")
        rows = [[B(f"✋ Одобрение: {on_off(CFG['approval_on'])}", "a:apr")]]
        if pend:
            rows.append([B(f"✅ Одобрить все по очереди ({pend})", "a:aprall")])
        rows.append(back)
    elif name == "rp":
        text = (f"📈 Ежедневный отчёт: {on_off(CFG['report_on'])}\n"
                f"Присылаю в {CFG['report_hour']:02d}:00\n\n" + make_report())
        rows = [[B(f"📈 Отчёт: {on_off(CFG['report_on'])}", "a:rep")],
                [B("Час: −1", "a:rhour:-1"), B("Час: +1", "a:rhour:1")],
                [B("📨 Прислать сейчас", "a:repnow")], back]
    elif name == "ad":
        lines, rows = [], []
        for i, a in enumerate(CFG["admins"]):
            nm = CFG["admin_names"].get(str(a), str(a))
            lines.append(f"{i + 1}. {nm}" + (" 👑 владелец" if i == 0 else "") + (" (это ты)" if a == uid else ""))
            if i > 0 and is_owner(uid):
                rows.append([B(f"❌ Убрать {nm}", f"a:addel:{i}")])
        if is_owner(uid):
            rows.append([B("➕ Пригласить админа", "a:adinv")])
        text = "👥 Админы бота\n\n" + "\n".join(lines) + \
            ("" if is_owner(uid) else "\n\nДобавлять и убирать админов может только владелец.")
        rows.append(back)
    elif name == "adcode":
        text = (f"🔑 Код приглашения: {LAST_INVITE['code']}\n\n"
                "Новый админ должен открыть этого бота в MAX, нажать «Старт» и отправить ему "
                "этот код. Код одноразовый и действует 15 минут.")
        rows = [[B("◀️ К админам", "m:ad")]]
    elif name == "ts":
        text = ("🧪 Тест-репост\n\nБот возьмёт ПОСЛЕДНИЙ пост каждого канала из списка и выложит их сразу, "
                "без ожидания расписания (похожие друг на друга пропустит как дубли). "
                "Память о прошлых постах на время теста очищается.\n\nЗапустить?")
        rows = [[B("✅ Да, запустить", "a:test")], back]
    elif name == "st":
        lines = [f"@{c}: последний пост {LAST_IDS.get(c, '—')}" for c in CFG["channels"]]
        text = ("📊 Статус\n\n" + "\n".join(lines) +
                f"\n\nОпубликовано: {STATS['published']}\nВ очереди: {len(QUEUE)}"
                f"\nДублей пропущено: {STATS['dups']}\nОтсеяно стоп-словами: {STATS['blocked']}"
                f"\nУстарело и удалено: {STATS['expired']}"
                f"\nОшибок: {STATS['errors']}")
        rows = [[B("🔄 Обновить", "m:st")], back]
    elif name == "sh":
        ph = sum(1 for x in STASH if x.get("photos"))
        vd = sum(1 for x in STASH if x.get("videos"))
        tx = sum(1 for x in STASH if not x.get("photos") and not x.get("videos"))
        text = (f"🗃 Тайник: {on_off(CFG['stash_on'])}\n\n"
                f"Внутри: {len(STASH)} (с фото: {ph}, с видео: {vd}, только текст: {tx})\n\n"
                "Когда в очереди пусто, бот сам выкладывает отсюда по одному — не чаще раза в "
                f"{fmt_min(max(CFG['gap_min'], 10))}.\n"
                "Пополнить: «➕ Добавить» и кидай мемы — фото, видео, текст, можно пересылать. "
                "Или просто скинь боту фото/видео в любой момент.")
        rows = [[B(f"🗃 Тайник: {on_off(CFG['stash_on'])}", "a:shon")],
                [B("➕ Добавить", "p:stashadd"), B("📋 Список", "m:shl")],
                [B("📤 Выложить сейчас", "a:shnow"), B("🗑 Очистить", "m:shc")], back]
    elif name == "shl":
        lines, rows = [], []
        for i, it in enumerate(STASH[:10]):
            icon = "🎬" if it.get("videos") else ("🖼" if it.get("photos") else "📝")
            extra = f" +{len(it['photos'])} фото" if len(it.get("photos", [])) > 1 else ""
            snip = (it.get("text") or "(без текста)")[:40].replace("\n", " ")
            lines.append(f"{i + 1}. {icon} {snip}{extra}")
        text = f"📋 Тайник ({len(STASH)})\n\n" + ("\n".join(lines) or "пусто") + \
            ("\n…и ещё " + str(len(STASH) - 10) if len(STASH) > 10 else "") + \
            "\n\nВыходят по порядку, сверху вниз. Нажми номер, чтобы убрать."
        btns = [B(f"❌ {i + 1}", f"a:shdel:{i}") for i in range(min(10, len(STASH)))]
        rows = [btns[i:i + 5] for i in range(0, len(btns), 5)]
        rows.append([B("◀️ Тайник", "m:sh")])
    elif name == "shc":
        text = f"🗑 Очистить тайник? Удалится всё: {len(STASH)} шт."
        rows = [[B("✅ Да, очистить", "a:shclr"), B("Нет", "m:sh")]]
    elif name == "th":
        lines = []
        for i, t in enumerate(THEMES):
            with theme_ctx(t):
                chans = len(CFG["channels"])
                lines.append(f"{'✅' if t is active_theme(uid) else '▫️'} {i + 1}. {t.name} · каналов: {chans} · "
                             f"очередь: {len(QUEUE)} · тайник: {len(STASH)}"
                             + ("" if t.chat_id else " · ⚠️ канал MAX не привязан"))
        text = ("🎭 Темы\n\nВсе темы работают одновременно: каждая читает свои каналы Telegram и "
                "выкладывает в свой канал MAX. Отмеченная ✅ — та, которую ты сейчас настраиваешь в меню.\n\n"
                + "\n".join(lines))
        rows = [[B(("✅ " if t is active_theme(uid) else "") + t.name, f"a:thsw:{i}")] for i, t in enumerate(THEMES)]
        rows += [[B("➕ Новая тема", "p:themenew"), B("⚙️ Эта тема", "m:thm")], back]
    elif name == "thm":
        t = cur()
        text = (f"⚙️ Тема «{t.name}»\n\n"
                f"Канал MAX: {t.link or '— не задан'}\n"
                f"Привязка: {'✅ есть' if t.chat_id else '⚠️ нет (добавь бота админом канала и напиши там любое сообщение)'}\n"
                f"Каналов-источников: {len(CFG['channels'])}\n"
                f"Подпись: {CFG['signature_text']}")
        rows = [[B("✏️ Название", "p:thname"), B("🔗 Канал MAX", "p:thlink")],
                [B("📡 Каналы-источники", "m:ch")]]
        if not t.legacy:
            rows.append([B("🗑 Удалить тему", "m:thd")])
        rows.append([B("◀️ Темы", "m:th")])
    elif name == "thd":
        text = f"🗑 Удалить тему «{cur().name}»? Очередь и тайник этой темы пропадут."
        rows = [[B("✅ Да, удалить", "a:thdel"), B("Нет", "m:thm")]]
    elif name == "lg":
        lines = list(JOURNAL)[-25:]
        text = "📜 Журнал (последние события)\n\n" + ("\n".join(lines) if lines else "пока пусто")
        rows = [[B("🔄 Обновить", "m:lg"), B("🔎 Проверка", "m:dg")], back]
    elif name == "dg":
        text = run_diag()
        rows = [[B("🔄 Проверить снова", "m:dg")], back]
    elif name == "hp":
        text = help_text() + "\n\nМожно просто скинуть боту фото/видео/пересланное — улетит в тайник."
        rows = [back]
    else:
        text = (f"⚙️ Меню бота · 🎭 {cur().name}\n\n"
                f"Состояние: {'⏸ на паузе' if CFG['paused'] else '▶️ работает'}\n"
                f"Каналов: {len(CFG['channels'])} · проверка каждые {fmt_iv(CFG['interval'])}\n"
                f"Расписание: {'пауза ' + fmt_min(CFG['gap_min']) if CFG['queue_on'] else 'выкл'} · "
                f"в очереди: {len(QUEUE)}\n"
                f"Опубликовано: {STATS['published']} · дублей пропущено: {STATS['dups']}\n"
                f"Одобрение: {'вкл' if CFG['approval_on'] else 'выкл'} · ночь: {'вкл' if CFG['night_on'] else 'выкл'}\n"
                f"Тайник: {len(STASH)} шт. ({'вкл' if CFG['stash_on'] else 'выкл'})")
        rows = [[B("📡 Каналы", "m:ch"), B("🎬 Видео", "m:vid")],
                [B("⏳ Расписание", "m:q"), B("🔁 Дубли", "m:dd")],
                [B("🧹 Фильтры", "m:fl"), B("✍️ Подпись", "m:sg")],
                [B("⏱ Интервал", "m:iv"), B("👥 Админы", "m:ad")],
                [B("✋ Одобрение", "m:ap"), B("📈 Отчёт", "m:rp")],
                [B("📊 Статус", "m:st"), B("🧪 Тест", "m:ts")],
                [B("🗃 Тайник", "m:sh"), B("❓ Команды", "m:hp")],
                [B("🎭 Темы", "m:th"), B("📜 Журнал", "m:lg")],
                [B("🔎 Проверка связи", "m:dg"), B("🔑 Telegram", "tg:menu")],
                [B("▶️ Продолжить" if CFG["paused"] else "⏸ Пауза", "a:pause")]]
    return text, rows


PROMPTS = {
    "chadd": "Пришли каналы Telegram, из которых брать посты: @name или t.me/name "
             "(можно несколько — через пробел или с новой строки).",
    "themenew": "Пришли название новой темы, например: «Мемы про котов».",
    "thname": "Пришли новое название темы.",
    "thlink": "Пришли ссылку на канал MAX, куда выкладывать эту тему (https://max.ru/…). "
              "Бот должен быть админом этого канала.",
    "adda": "Пришли рекламное слово (или несколько через запятую). Строки с ним будут удаляться.",
    "dela": "Пришли рекламное слово, которое нужно убрать из списка.",
    "addo": "Пришли слово из подписи канала-донора (или несколько через запятую).",
    "delo": "Пришли слово подписи донора, которое нужно убрать из списка.",
    "sgtext": "Пришли новый текст подписи (до 60 символов).",
    "gapx": "Пришли паузу между публикациями в минутах (0–720), например 45.",
    "burx": "Пришли окно серии в минутах (0–120). 0 = серии выключены.",
    "addsw": "Пришли стоп-слово (или несколько через запятую). Посты с ним публиковаться не будут.",
    "delsw": "Пришли стоп-слово, которое нужно убрать.",
    "addrw": "Пришли замену в виде: старое = новое\n(можно несколько строк). Если справа пусто — слово просто удаляется.",
    "delrw": "Пришли слово слева (то, что заменяется), чтобы убрать эту замену.",
    "addur": "Пришли слово-триггер для срочных постов (или несколько через запятую).",
    "delur": "Пришли слово-триггер, которое нужно убрать.",
    "stashadd": "Кидай мемы в тайник: фото, видео, текст, можно пересылать из других чатов. "
                "Каждое сообщение — отдельный пост. Закончишь — жми «Готово».",
}


def split_words(text):
    return [w.strip().lower() for w in re.split(r"[,\n]", text) if w.strip()]


def apply_text(action, text, uid=None):
    """Применяет присланный текст. Возвращает (ответ, экран)."""
    if action == "chadd":
        names, bad = [], []
        for tok in re.split(r"[\s,;]+", text.strip()):
            if not tok:
                continue
            m = re.fullmatch(r"(?:https?://)?(?:t\.me/|telegram\.me/)?@?([A-Za-z0-9_]{4,32})/?", tok)
            if m:
                names.append(m.group(1))
            else:
                bad.append(tok)
        have = [c.lower() for c in CFG["channels"]]
        added = []
        for name in names:
            if name.lower() in have:
                continue
            CFG["channels"].append(name)
            have.append(name.lower())
            BAD.pop(name, None)
            added.append(name)
        save_cfg()
        if not names:
            return "Не похоже на username канала. Попробуй ещё раз.", "ch"
        msg = ("Добавил: " + ", ".join("@" + n for n in added) + ". Старые посты не публикую, только новые."
               if added else "Эти каналы уже в списке.")
        if bad:
            msg += "\nНе поняла: " + ", ".join(bad)
        return msg, "ch"
    if action == "themenew":
        name = text.strip()[:40]
        if not name:
            return "Нужно название.", "th"
        t = create_theme(name)
        META["active"][str(uid)] = t.id
        CUR.set(t)
        save_cfg()
        return f"Создал тему «{name}» и переключился на неё.", "ask:thlink"
    if action == "thname":
        cur().name = text.strip()[:40] or cur().name
        save_cfg()
        return "Название обновлено.", "thm"
    if action == "thlink":
        link = text.strip()
        if "/" not in link and not link.startswith("http"):
            link = "https://max.ru/" + link.lstrip("@")
        if "max.ru" not in link:
            return "Нужна ссылка вида https://max.ru/название_канала", "thm"
        t = cur()
        t.link = link
        t.chat_id = resolve_chat_id(link)
        save_cfg()
        nxt = "ask:chadd" if not CFG["channels"] else "thm"
        if t.chat_id:
            return "Канал MAX найден и привязан ✅", nxt
        BIND["theme"] = t.id
        return ("Не нашёл канал по ссылке. Добавь бота админом в этот канал MAX и напиши там любое "
                "сообщение — я привяжу сам."), nxt
    if action in ("adda", "addo"):
        key = "ad_keywords" if action == "adda" else "old_signatures"
        added = [w for w in split_words(text) if w not in [x.lower() for x in CFG[key]]]
        CFG[key] += added
        save_cfg()
        return (f"Добавил: {fmt_words(added)}" if added else "Такие слова уже есть."), "fl"
    if action in ("dela", "delo"):
        key = "ad_keywords" if action == "dela" else "old_signatures"
        gone = split_words(text)
        before = len(CFG[key])
        CFG[key] = [x for x in CFG[key] if x.lower() not in gone]
        save_cfg()
        return (f"Убрал слов: {before - len(CFG[key])}"), "fl"
    if action == "sgtext":
        CFG["signature_text"] = text.strip()[:60]
        save_cfg()
        return "Подпись обновлена.", "sg"
    if action in ("addsw", "addur"):
        key = "stop_words" if action == "addsw" else "urgent_words"
        added = [w for w in split_words(text) if w not in [x.lower() for x in CFG[key]]]
        CFG[key] += added
        save_cfg()
        return (f"Добавил: {fmt_words(added)}" if added else "Такие слова уже есть."), \
            ("sw" if action == "addsw" else "ur")
    if action in ("delsw", "delur"):
        key = "stop_words" if action == "delsw" else "urgent_words"
        gone = split_words(text)
        before = len(CFG[key])
        CFG[key] = [x for x in CFG[key] if x.lower() not in gone]
        save_cfg()
        return f"Убрал слов: {before - len(CFG[key])}", ("sw" if action == "delsw" else "ur")
    if action == "addrw":
        added = 0
        for line in text.split("\n"):
            if "=" not in line:
                continue
            old, _, new = line.partition("=")
            old, new = old.strip(), new.strip()
            if not old:
                continue
            CFG["replacements"] = [p for p in CFG["replacements"] if p[0].lower() != old.lower()]
            CFG["replacements"].append([old, new])
            added += 1
        save_cfg()
        return (f"Замен сохранено: {added}" if added else "Нужен формат: старое = новое"), "rw"
    if action == "delrw":
        gone = [w.lower() for w in split_words(text)]
        before = len(CFG["replacements"])
        CFG["replacements"] = [p for p in CFG["replacements"] if p[0].lower() not in gone]
        save_cfg()
        return f"Убрал замен: {before - len(CFG['replacements'])}", "rw"
    if action in ("gapx", "burx"):
        m = re.search(r"\d+", text)
        if not m:
            return "Нужно число минут. Попробуй ещё раз.", "q"
        v = int(m.group())
        if action == "gapx":
            CFG["gap_min"] = max(0, min(720, v))
        else:
            CFG["burst_min"] = max(0, min(120, v))
        save_cfg()
        return "Готово.", "q"
    return "Не понял.", "main"


def do_action(act, uid=None):
    """Кнопки-действия. Возвращает экран, который показать после."""
    p = act.split(":")
    scr = "main"
    if p[0] == "pause":
        CFG["paused"] = not CFG["paused"]
    elif p[0] == "vid":
        CFG["video_on"] = not CFG["video_on"]
        scr = "vid"
    elif p[0] == "vmb":
        CFG["video_mb"] = max(10, min(250, CFG["video_mb"] + int(p[1])))
        scr = "vid"
    elif p[0] == "dd":
        CFG["dedupe_on"] = not CFG["dedupe_on"]
        scr = "dd"
    elif p[0] == "ddm":
        CFG["meaning"] = round(max(0.30, min(0.80, CFG["meaning"] + float(p[1]))), 2)
        scr = "dd"
    elif p[0] == "ddh":
        opts = [24, 48, 72, 168]
        CFG["dedupe_hours"] = opts[(opts.index(CFG["dedupe_hours"]) + 1) % 4] if CFG["dedupe_hours"] in opts else 72
        scr = "dd"
    elif p[0] == "sg":
        CFG["signature_on"] = not CFG["signature_on"]
        scr = "sg"
    elif p[0] == "iv":
        CFG["interval"] = int(p[1])
        scr = "iv"
    elif p[0] == "qon":
        CFG["queue_on"] = not CFG["queue_on"]
        scr = "q"
    elif p[0] == "gap":
        CFG["gap_min"] = max(0, min(720, CFG["gap_min"] + int(p[1])))
        scr = "q"
    elif p[0] == "gapset":
        CFG["gap_min"] = int(p[1])
        scr = "q"
    elif p[0] == "bur":
        CFG["burst_min"] = max(0, min(120, CFG["burst_min"] + int(p[1])))
        scr = "q"
    elif p[0] == "bmax":
        CFG["burst_max"] = max(1, min(10, CFG["burst_max"] + int(p[1])))
        scr = "q"
    elif p[0] == "age":
        opts = [6, 12, 24, 48]
        CFG["max_age_h"] = opts[(opts.index(CFG["max_age_h"]) + 1) % 4] if CFG["max_age_h"] in opts else 24
        scr = "q"
    elif p[0] == "qnow":
        FORCE["until"] = time.time() + 60
        scr = "ql"
    elif p[0] == "qclr":
        resolve_cards(item_refs(QUEUE), CFG["admin_chats"].get(str(uid)))
        for it in list(QUEUE):
            drop_item(it)
        save_queue()
        scr = "ql"
    elif p[0] == "night":
        CFG["night_on"] = not CFG["night_on"]
        scr = "nt"
    elif p[0] in ("nfrom", "nto"):
        key = "night_from" if p[0] == "nfrom" else "night_to"
        CFG[key] = (CFG[key] + int(p[1])) % 24
        scr = "nt"
    elif p[0] == "urg":
        CFG["urgent_on"] = not CFG["urgent_on"]
        scr = "ur"
    elif p[0] == "urgmax":
        CFG["urgent_max_h"] = max(1, min(20, CFG["urgent_max_h"] + int(p[1])))
        scr = "ur"
    elif p[0] == "prio":
        i = int(p[1])
        if 0 <= i < len(CFG["channels"]):
            ch = CFG["channels"][i]
            CFG["priority"][ch] = 1 if CFG["priority"].get(ch, 1) == 2 else 2
        scr = "pr"
    elif p[0] == "apr":
        CFG["approval_on"] = not CFG["approval_on"]
        if not CFG["approval_on"]:                 # выключили — всё ожидающее уходит в очередь
            approve_all_pending(CFG["admin_chats"].get(str(uid)))
        scr = "ap"
    elif p[0] == "aprall":
        approve_all_pending(CFG["admin_chats"].get(str(uid)))
        scr = "ap"
    elif p[0] == "rep":
        CFG["report_on"] = not CFG["report_on"]
        scr = "rp"
    elif p[0] == "rhour":
        CFG["report_hour"] = (CFG["report_hour"] + int(p[1])) % 24
        scr = "rp"
    elif p[0] == "repnow":
        send_report()
        scr = "rp"
    elif p[0] == "test":
        FLAGS["test"] = True
        notify("🧪 Тест запущен: публикую последние посты каналов…")
    elif p[0] == "adinv":
        if is_owner(uid):
            code = str(random.randint(100000, 999999))
            INVITES[code] = time.time() + 900
            LAST_INVITE["code"] = code
            scr = "adcode"
        else:
            scr = "ad"
    elif p[0] == "addel":
        i = int(p[1])
        if is_owner(uid) and 0 < i < len(CFG["admins"]):
            gone = CFG["admins"].pop(i)
            CFG["admin_names"].pop(str(gone), None)
            CFG["admin_chats"].pop(str(gone), None)
        scr = "ad"
    elif p[0] == "ewarn":
        CFG["empty_warn_on"] = not CFG["empty_warn_on"]
        cur().flags["empty_at"] = 0
        scr = "q"
    elif p[0] == "thsw":
        i = int(p[1])
        if 0 <= i < len(THEMES):
            META["active"][str(uid)] = THEMES[i].id
            CUR.set(THEMES[i])
        scr = "main"
    elif p[0] == "thdel":
        if not cur().legacy:
            delete_theme(cur())
        scr = "th"
    elif p[0] == "ser":
        CFG["auto_series"] = not CFG["auto_series"]
        scr = "q"
    elif p[0] == "later":
        CFG["later_min"] = max(5, min(720, CFG["later_min"] + int(p[1])))
        scr = "q"
    elif p[0] == "shon":
        CFG["stash_on"] = not CFG["stash_on"]
        scr = "sh"
    elif p[0] == "shdel":
        i = int(p[1])
        if 0 <= i < len(STASH):
            stash_remove(STASH[i])
        scr = "shl"
    elif p[0] == "shclr":
        for it in list(STASH):
            stash_remove(it, save=False)
        save_stash()
        scr = "sh"
    elif p[0] == "shnow":
        if STASH:
            FLAGS["stash_now"] = True
            notify("🗃 Выкладываю из тайника…")
        scr = "sh"
    elif p[0] == "chdel":
        i = int(p[1])
        if 0 <= i < len(CFG["channels"]):
            gone = CFG["channels"].pop(i)
            LAST_IDS.pop(gone, None)
            cur().saved_ids.pop(gone, None)
        scr = "ch"
    save_cfg()
    return scr


def handle_callback(cb):
    uid = (cb.get("user") or {}).get("user_id")
    cid = cb.get("callback_id")
    payload = cb.get("payload") or ""
    kind, _, rest = payload.partition(":")
    if kind == "m":
        PENDING.pop(uid, None)
        text, rows = screen(rest, uid)
    elif kind == "a":
        text, rows = screen(do_action(rest, uid), uid)
    elif kind == "ap":
        text, rows = approve(rest, uid, cb.get("_chat"))
    elif kind == "tg":
        PENDING.pop(uid, None)
        text, rows = tg_screen(uid) if rest == "menu" else tg_callback(rest, uid)
    elif kind == "p":
        PENDING[uid] = rest
        cancel = [[B("✅ Готово", "m:sh")]] if rest == "stashadd" else [[B("❌ Отмена", "m:main")]]
        text, rows = PROMPTS.get(rest, "Пришли текст."), cancel
    else:
        text, rows = screen("main", uid)
    api_answer(cid, text, rows)


def item_refs(items):
    """id постов и пачек, на которые у админов висят карточки."""
    return [x["id"] for x in items] + [x["grp"] for x in items if x.get("grp")]


def approve_all_pending(chat=None):
    """Все ждущие одобрения -> в очередь по расписанию. Возвращает сколько."""
    items = [x for x in QUEUE if x.get("status") == "pending"]
    now = time.time()
    for x in items:
        x["status"] = "ok"
        x["enq"] = now
    if items:
        save_queue()
        resolve_cards(item_refs(items), chat)
    return len(items)


def last_slot():
    """Самое позднее время, на которое уже что-то выложено или назначено."""
    t = PUB["last_pub"]
    for x in QUEUE:
        if x.get("status", "ok") == "ok" and x.get("at", 0) > t:
            t = x["at"]
    return t


def _approve(rest, uid, chat=None):
    """Кнопки на карточках одобрения. Возвращает (текст, кнопки) на замену карточки.
    now/later/no — один пост, gnow/glater/gno — вся пачка, ok — просто в очередь по расписанию."""
    verdict, _, ref = rest.partition(":")
    if verdict in ("retry", "bare", "drop"):
        return fix_stuck(verdict, ref, uid, chat)
    group = verdict.startswith("g")
    mode = verdict[1:] if group else verdict
    items = [x for x in QUEUE if x.get("status") == "pending"
             and (x.get("grp") == ref if group else x["id"] == ref)]
    if not items:
        return "Этот пост уже обработан.", None
    chat = chat or CFG["admin_chats"].get(str(uid))
    who = CFG["admin_names"].get(str(uid), "админ")
    names = ", ".join(sorted({"@" + x["ch"] for x in items}))
    many = f"постов: {len(items)}" if len(items) > 1 else "пост"
    now = time.time()
    grps = {x["grp"] for x in items if x.get("grp")}

    def done(text):
        save_queue()
        refs = [x["id"] for x in items]
        refs += [g for g in grps if not any(y.get("grp") == g and y.get("status") == "pending" for y in QUEUE)]
        resolve_cards(refs, chat)             # у других админов — сразу, у этого — через 5 минут
        return text, None

    if mode == "no":
        for it in items:
            drop_item(it)
            bump("rejected")
        return done(f"❌ Пропустил {who}: {many} из {names} не будет опубликован.")
    at = 0
    if mode == "later":
        # цепочка: если на 12:00 уже что-то назначено, следующий пост — на 13:00, потом 14:00
        at = now + CFG["later_min"] * 60
        slot = last_slot()
        if slot > 0 and CFG["gap_min"] > 0:
            at = max(at, slot + CFG["gap_min"] * 60)
        at = after_night(at)                  # на ночь не назначаем — переносим на утро
    for it in items:
        it["status"] = "ok"
        it["enq"] = now                       # возраст считаем от одобрения
        if mode == "now":
            it["manual"], it["at"] = True, 0
        elif mode == "later":
            it["manual"], it["at"] = True, at
    if mode == "now":
        return done(f"✅ {who}: {many} из {names} — выкладываю сейчас.")
    if mode == "later":
        when = time.strftime("%H:%M", time.localtime(at))
        if time.localtime(at).tm_yday != time.localtime(now).tm_yday:
            when = "завтра в " + when
        tail = " Все вместе." if len(items) > 1 else ""
        return done(f"⏰ {who}: {many} из {names} — выйдет в {when} "
                    f"(через {fmt_min(round((at - now) / 60))}).{tail}")
    return done(f"✅ {who}: {many} из {names} — в очереди, выйдет по расписанию.")


def approve(rest, uid, chat=None):
    """Тема определяется по id поста/пачки из кнопки, а не по тому, какую тему открыл админ."""
    ref = rest.partition(":")[2]
    with theme_ctx(theme_of_ref(ref)):
        return _approve(rest, uid, chat)


def send_stuck_card(it):
    snip = (it.get("text") or "(без текста)")[:120].replace("\n", " ")
    text = (f"⚠️ Пост из @{it['ch']} не вышел как есть{theme_tag()}\n"
            f"Причина: {it.get('stuck_why', '?')}\n\n{snip}\n\nЧто делать?")
    rows = [[B("🔁 Повторить", f"ap:retry:{it['id']}"), B("📤 Без вложения", f"ap:bare:{it['id']}")],
            [B("🗑 Убрать", f"ap:drop:{it['id']}")]]
    for chat in set(CFG["admin_chats"].values()):
        try:
            add_card(chat, api_send(chat, text, rows), it["id"])
        except Exception as e:
            print("Карточка проблемного поста:", e)


def fix_stuck(verdict, ref, uid, chat):
    it = next((x for x in QUEUE if x["id"] == ref and x.get("status") == "stuck"), None)
    if it is None:
        return "Этот пост уже обработан.", None
    who = CFG["admin_names"].get(str(uid), "админ")
    if verdict == "drop":
        drop_item(it)
        bump("rejected")
        text = f"🗑 {who}: пост из @{it['ch']} убран."
    else:
        it.update(status="ok", fails=0, retry_at=0, manual=True, at=0, enq=time.time())
        if verdict == "bare":
            it["nomedia"] = True
        text = (f"📤 {who}: выкладываю пост из @{it['ch']} без недостающего вложения."
                if verdict == "bare" else f"🔁 {who}: пробую ещё раз пост из @{it['ch']}.")
    save_queue()
    resolve_cards([ref], chat)
    return text, None


# ---------- ТАЙНИК ----------
def extract_content(msg):
    """Из сообщения MAX (в т.ч. пересланного) достаёт текст и фото/видео."""
    msg = msg or {}
    body = msg.get("body") or {}
    text = body.get("text") or ""
    raw = list(body.get("attachments") or [])
    link = msg.get("link") or {}
    if link.get("type") == "forward":
        inner = link.get("message") or {}
        text = text or inner.get("text") or ""
        raw += inner.get("attachments") or []
    atts = []
    for a in raw:
        if a.get("type") in ("image", "video"):
            pl = a.get("payload") or {}
            atts.append({"kind": a["type"], "url": pl.get("url"), "token": pl.get("token")})
    return text, atts


def fetch_file(url, base, default_ext):
    """Скачивает файл по ссылке в base+расширение. Возвращает путь."""
    r = S.get(url, timeout=120, stream=True)
    r.raise_for_status()
    ct = (r.headers.get("Content-Type") or "").lower()
    ext = next((e for k, e in (("jpeg", ".jpg"), ("png", ".png"), ("gif", ".gif"), ("webp", ".webp"),
                               ("quicktime", ".mov"), ("mp4", ".mp4")) if k in ct), default_ext)
    path = base + ext
    with open(path, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    if os.path.getsize(path) == 0:
        os.remove(path)
        raise RuntimeError("пустой файл")
    return path


def video_urls(att):
    """Ссылки на скачивание видео из MAX: из самого сообщения и через /videos/{token}."""
    urls = []
    if att.get("url"):
        urls.append(att["url"])
    if att.get("token"):
        try:
            r = S.get(f"{MAX_API}/videos/{att['token']}", headers=HEADERS, timeout=30)
            if r.status_code == 200:
                u = r.json().get("urls") or {}
                for k in ("mp4_720", "mp4_1080", "mp4_480", "mp4_360", "mp4_240", "mp4_144"):
                    if u.get(k):
                        urls.append(u[k])
        except Exception as e:
            print("Тайник: /videos не ответил:", e)
    return urls


def stash_add(uid, text, atts):
    """Кладёт сообщение в тайник. Возвращает (элемент или None, текст-ответ админу)."""
    os.makedirs(STASH_DIR, exist_ok=True)
    sid = f"s{int(time.time() * 1000)}{random.randint(10, 99)}"
    photos, videos, problems = [], [], []
    for n, a in enumerate(atts):
        base = f"{STASH_DIR}/{sid}_{n}"
        if a["kind"] == "image":
            path = None
            if a.get("url"):
                try:
                    path = fetch_file(a["url"], base, ".jpg")
                except Exception as e:
                    print("Тайник: фото не скачалось:", e)
            if path or a.get("token"):
                photos.append({"path": path, "token": a.get("token")})
            else:
                problems.append("фото")
        else:
            path = None
            for url in video_urls(a):
                try:
                    path = fetch_file(url, base, ".mp4")
                    break
                except Exception as e:
                    print("Тайник: видео не скачалось:", e)
            if path or a.get("token"):
                videos.append({"path": path, "token": a.get("token")})
            else:
                problems.append("видео")
    text = (text or "").strip()
    warn = f"\n⚠️ Не удалось сохранить: {', '.join(problems)}" if problems else ""
    if not (text or photos or videos):
        return None, "Пусто — нечего класть в тайник." + warn
    item = {"id": sid, "text": text, "photos": photos, "videos": videos,
            "added": time.time(), "by": uid, "sent": 0, "fails": 0}
    STASH.append(item)
    save_stash()
    kind = "видео" if videos else ("фото" if photos else "текст")
    return item, f"✅ В тайнике: {len(STASH)} (добавил: {kind}){warn}"


def stash_remove(it, save=True):
    for p in it.get("photos", []) + it.get("videos", []):
        if p.get("path"):
            try:
                os.remove(p["path"])
            except OSError:
                pass
    if it in STASH:
        STASH.remove(it)
    if save:
        save_stash()


# ---------- КОМАНДЫ через / ----------
COMMAND_HELP = [
    ("menu", "Главное меню"),
    ("status", "Статус бота"),
    ("queue", "Очередь постов"),
    ("now", "Выложить следующий пост из очереди сейчас"),
    ("skip", "Убрать следующий пост из очереди"),
    ("approve", "Одобрить все ждущие посты (пойдут по расписанию)"),
    ("pause", "Поставить бота на паузу"),
    ("resume", "Снять с паузы"),
    ("stash", "Тайник с мемами"),
    ("add", "Кидать мемы в тайник"),
    ("done", "Закончить добавление в тайник"),
    ("stashnow", "Выложить пост из тайника сейчас"),
    ("gap", "Пауза между постами в минутах, например /gap 45"),
    ("later", "Задержка кнопки «Через…», например /later 90"),
    ("theme", "Темы: /theme — список, /theme 2 — переключить"),
    ("warn", "Вкл/выкл предупреждение «на ближайший час постов нет»"),
    ("check", "Проверка: связь с MAX, Telegram, каналами"),
    ("log", "Журнал: что бот делал с постами"),
    ("report", "Прислать отчёт"),
    ("help", "Список команд"),
]


def help_text():
    return "❓ Команды\n\n" + "\n".join(f"/{n} — {d}" for n, d in COMMAND_HELP)


HOOKS = {"dropped": 0, "warned": False}


def drop_webhooks():
    """Вебхук на токене (его ставят хостинги ботов или старые копии бота) забирает все события — бот в long
    polling тогда ничего не получает и молчит. Снимаем все подписки. Возвращает, сколько сняли."""
    n = 0
    try:
        r = S.get(f"{MAX_API}/subscriptions", headers=HEADERS, timeout=20)
        if r.status_code != 200:
            _log_line(time.strftime("%H:%M:%S") + f" MAX: /subscriptions ответил {r.status_code} {r.text[:100]}")
            return 0
        for sub in r.json().get("subscriptions") or []:
            url = sub.get("url")
            if not url:
                continue
            d = S.delete(f"{MAX_API}/subscriptions", params={"url": url}, headers=HEADERS, timeout=20)
            try:
                ok = d.status_code == 200 and d.json().get("success", True) is not False
            except ValueError:
                ok = d.status_code == 200
            print(f"MAX: снял вебхук {url} — он перехватывал сообщения бота"
                  + ("" if ok else f" (не вышло: {d.status_code} {d.text[:100]})"))
            n += ok
    except Exception as e:
        _log_line(time.strftime("%H:%M:%S") + f" MAX: проверка вебхуков не удалась: {e}")
    HOOKS["dropped"] += n
    if n and HOOKS["dropped"] >= 3 and not HOOKS["warned"] and CFG["admins"]:
        HOOKS["warned"] = True
        notify("⚠️ Кто-то снова и снова ставит вебхук на токен бота (хостинг или вторая копия бота). Пока он стоит, "
               "бот не слышит сообщения. Оставь бота запущенным только в одном месте, а на хостинге выключи "
               "вебхук/домен для этого бота.", tag=False)
    return n


def register_commands():
    """Показывает команды в подсказке MAX (если API не принял — не страшно, команды работают и так)."""
    try:
        body = {"commands": [{"name": n, "description": d[:100]} for n, d in COMMAND_HELP[:32]]}
        r = S.patch(f"{MAX_API}/me/commands", headers=HEADERS, timeout=30, json=body)
        if r.status_code != 200:                     # старый способ (раньше команды задавались через PATCH /me)
            r = S.patch(f"{MAX_API}/me", headers=HEADERS, timeout=30, json=body)
        if r.status_code != 200:
            print("Подсказки команд MAX не поддерживает — не страшно, команды /menu, /help и др. работают.")
    except Exception as e:
        print("Команды бота: не зарегистрировались:", e)


def run_command(cmd, arg, uid, chat_id):
    """Быстрые команды. Любая команда выходит из режима ввода."""
    PENDING.pop(uid, None)

    def show(name):
        m, rows = screen(name, uid)
        api_send(chat_id, m, rows)

    def say(text, rows=None):
        api_send(chat_id, text, rows)

    def num(lo, hi):
        m = re.search(r"\d+", arg)
        return max(lo, min(hi, int(m.group()))) if m else None

    pend = sum(1 for x in QUEUE if x.get("status") == "pending")
    if cmd in ("start", "menu", "m"):
        show("main")
    elif cmd in ("help", "h", "commands"):
        show("hp")
    elif cmd in ("status", "st"):
        show("st")
    elif cmd in ("queue", "q"):
        show("ql")
    elif cmd == "now":
        ok = [x for x in QUEUE if x.get("status", "ok") == "ok"]
        if not ok:
            if pend:
                say(f"Выкладывать нечего: {pend} ждут одобрения (/approve или кнопки на карточках).")
            else:
                say("Очередь пуста." + (" Есть тайник — /stashnow." if STASH else ""))
        else:
            it = min(ok, key=prio_key)
            it["manual"], it["at"], it["retry_at"] = True, 0, 0
            save_queue()
            say(f"⏩ Выкладываю пост из @{it['ch']} (через пару секунд).")
    elif cmd == "skip":
        items = sorted(QUEUE, key=lambda x: x["date"])
        if not items:
            say("Очередь пуста.")
        else:
            it = items[0]
            snip = (it.get("text") or "(без текста)")[:40].replace("\n", " ")
            drop_item(it)
            save_queue()
            bump("rejected")
            resolve_cards(item_refs([it]), chat_id)
            say(f"🗑 Убрал из очереди: @{it['ch']} · {snip}")
    elif cmd in ("approve", "ok"):
        n = approve_all_pending(chat_id)
        say(f"✅ Одобрил: {n}. Выходят по расписанию." if n else "Ждущих одобрения нет.")
    elif cmd == "pause":
        CFG["paused"] = True
        save_cfg()
        say("⏸ Пауза. /resume — продолжить.")
    elif cmd in ("resume", "go"):
        CFG["paused"] = False
        save_cfg()
        say("▶️ Работаю.")
    elif cmd == "stash":
        show("sh")
    elif cmd in ("add", "stashadd"):
        PENDING[uid] = "stashadd"
        say(PROMPTS["stashadd"], [[B("✅ Готово", "m:sh")]])
    elif cmd in ("done", "cancel"):
        show("sh" if cmd == "done" else "main")
    elif cmd == "stashnow":
        if STASH:
            FLAGS["stash_now"] = True
            say("🗃 Выкладываю из тайника (через пару секунд).")
        else:
            say("Тайник пуст — /add, чтобы пополнить.")
    elif cmd == "gap":
        v = num(0, 720)
        if v is None:
            say(f"Сейчас пауза между постами: {fmt_min(CFG['gap_min'])}. Пример: /gap 45")
        else:
            CFG["gap_min"] = v
            save_cfg()
            say(f"Пауза между постами: {fmt_min(v)}.")
    elif cmd == "later":
        v = num(5, 720)
        if v is None:
            say(f"Сейчас кнопка «Через…» = {fmt_min(CFG['later_min'])}. Пример: /later 90")
        else:
            CFG["later_min"] = v
            save_cfg()
            say(f"Кнопка «Через…» теперь {fmt_min(v)}.")
    elif cmd == "warn":
        CFG["empty_warn_on"] = not CFG["empty_warn_on"]
        cur().flags["empty_at"] = 0
        save_cfg()
        say("📭 Предупреждение о пустой очереди: " + on_off(CFG["empty_warn_on"]))
    elif cmd in ("theme", "themes", "t"):
        if not arg:
            show("th")
        else:
            t = THEMES[int(arg) - 1] if arg.isdigit() and 1 <= int(arg) <= len(THEMES) else \
                next((x for x in THEMES if arg.lower() in x.name.lower()), None)
            if t is None:
                say("Нет такой темы. /theme — список.")
            else:
                META["active"][str(uid)] = t.id
                save_cfg()
                CUR.set(t)
                show("main")
    elif cmd == "check":
        say("🔎 Проверяю связь, секунду…")
        say(run_diag())
    elif cmd == "log":
        show("lg")
    elif cmd == "report":
        say(make_report())
    else:
        show("hp")
    return True


def resend_pending_cards(chat):
    """Админ появился (или сменил чат): присылаем ему карточки постов, которые уже ждут одобрения."""
    later = f"⏰ Через {fmt_min(CFG['later_min'])}"
    n = 0
    for t in list(THEMES):
        with theme_ctx(t):
            for it in [x for x in QUEUE if x.get("status") == "pending"]:
                if any(c["ref"] == it["id"] and c["chat"] == chat for c in CARDS):
                    continue
                send_preview(it, [[B("✅ Сейчас", f"ap:now:{it['id']}"), B(later, f"ap:later:{it['id']}")],
                                  [B("❌ Пропустить", f"ap:no:{it['id']}")]], chats=[chat])
                n += 1
    if n:
        api_send(chat, f"📬 Постов ждёт одобрения: {n}")


def add_admin(uid, chat_id, name):
    if uid not in CFG["admins"]:
        CFG["admins"].append(uid)
    CFG["admin_names"][str(uid)] = name or str(uid)
    CFG["admin_chats"][str(uid)] = chat_id
    save_cfg()
    resend_pending_cards(chat_id)           # что накопилось без админа — показываем сразу


CODE_TRIES = {}         # user_id -> (неверных кодов подряд, время последнего)


def note_wrong_code(uid, t):
    env = (os.environ.get("ADMIN_CODE") or "").strip()
    t = (t or "").strip()
    if re.fullmatch(r"\d{6}", t) or (len(env) >= 4 and len(t) == len(env) and " " not in t):   # похоже на код
        n, last = CODE_TRIES.get(uid, (0, 0))
        if time.time() - last >= 600:                   # пауза прошла — считаем заново
            n = 0
        CODE_TRIES[uid] = (n + 1, time.time())


def handle_message(uid, chat_id, name, text, msg=None):
    global CLAIM_CODE
    t = (text or "").strip()
    if not is_admin(uid):
        env_code = (os.environ.get("ADMIN_CODE") or "").strip()      # свой код из настроек хостинга
        tries = CODE_TRIES.get(uid, (0, 0))
        if tries[0] >= 5 and time.time() - tries[1] < 600:           # подбор кода: 5 ошибок — пауза 10 минут
            left = max(1, -(-int(600 - (time.time() - tries[1])) // 60))
            print("Меню: много неверных кодов от", uid, name, "— пауза", left, "мин")
            api_send(chat_id, f"Слишком много неверных кодов. Подожди {left} мин и отправь код снова.")
            return
        if env_code and len(env_code) >= 4 and t == env_code:
            add_admin(uid, chat_id, name)
            first = CFG["admins"][0] == uid
            print("Меню: по коду ADMIN_CODE добавлен", "владелец" if first else "админ", uid, name)
            api_send(chat_id, "Готово, ты владелец бота ✅" if first else "Готово, ты теперь админ бота ✅")
            m, rows = screen("main", uid)
            api_send(chat_id, m, rows)
            if not first:
                notify(f"👥 Новый админ (ADMIN_CODE): {name or uid}")
        elif not CFG["admins"] and CLAIM_CODE and t == CLAIM_CODE:      # первый вход: владелец
            add_admin(uid, chat_id, name)
            CLAIM_CODE = None
            with contextlib.suppress(OSError):
                os.remove("claim_code.txt")
            print("Меню: владелец привязан, user_id =", uid)
            api_send(chat_id, "Готово, ты владелец бота ✅")
            m, rows = screen("main", uid)
            api_send(chat_id, m, rows)
        elif CFG["admins"] and CONSOLE_CODE["code"] and t == CONSOLE_CODE["code"]:   # код из консоли
            add_admin(uid, chat_id, name)
            print("Меню: добавлен админ по коду из консоли", uid, name)
            api_send(chat_id, "Готово, ты теперь админ бота ✅")
            m, rows = screen("main", uid)
            api_send(chat_id, m, rows)
            notify(f"👥 Новый админ (код из консоли): {name or uid}")
            new_console_code()                                         # старый код больше не действует
        elif t in INVITES and INVITES[t] > time.time():                # приглашение от владельца
            del INVITES[t]
            add_admin(uid, chat_id, name)
            print("Меню: добавлен админ", uid, name)
            api_send(chat_id, "Готово, ты теперь админ бота ✅")
            m, rows = screen("main", uid)
            api_send(chat_id, m, rows)
            notify(f"👥 Новый админ: {name or uid}")
        elif not CFG["admins"]:
            note_wrong_code(uid, t)
            api_send(chat_id, "Привет! Чтобы управлять ботом, отправь код привязки — он в консоли или логах бота "
                              "(строка «отправь ему этот код»), или свой ADMIN_CODE из настроек хостинга.")
        else:
            note_wrong_code(uid, t)
            api_send(chat_id, "Нет доступа. Отправь код админа из консоли/логов бота, свой ADMIN_CODE из настроек "
                              "хостинга или попроси владельца прислать код приглашения.")
        return
    changed = False
    if CFG["admin_chats"].get(str(uid)) != chat_id:
        CFG["admin_chats"][str(uid)] = chat_id
        changed = True
    if name and CFG["admin_names"].get(str(uid)) != name:
        CFG["admin_names"][str(uid)] = name
        changed = True
    if changed:
        save_cfg()
        resend_pending_cards(chat_id)
    content, atts = extract_content(msg) if msg else (t, [])
    if t.startswith("/"):                                  # быстрые команды
        cmd, _, arg = t[1:].partition(" ")
        run_command(cmd.split("@")[0].strip().lower(), arg.strip(), uid, chat_id)
        return
    action = PENDING.get(uid)
    if action == "stashadd":                               # режим добавления: всё присланное — в тайник
        if not atts and t.lower() in ("готово", "done", "хватит"):
            PENDING.pop(uid, None)
            m, rows = screen("sh", uid)
            api_send(chat_id, m, rows)
            return
        _, info = stash_add(uid, content, atts)
        api_send(chat_id, info, [[B("✅ Готово", "m:sh")]])
        return
    if action and action.startswith("tg_") and t:          # подключение Telegram: ключи, номер, код
        answer, rows = tg_text(uid, action, t, ((msg or {}).get("body") or {}).get("mid"))
        api_send(chat_id, answer, rows)
        return
    PENDING.pop(uid, None)
    if action and t:
        answer, scr = apply_text(action, t, uid)
        if scr.startswith("ask:"):                         # цепочка ввода: следующий вопрос
            PENDING[uid] = scr[4:]
            api_send(chat_id, answer + "\n\n" + PROMPTS[scr[4:]], [[B("⏭ Пропустить", "m:thm")]])
            return
        api_send(chat_id, answer)
        m, rows = screen(scr, uid)
        api_send(chat_id, m, rows)
    elif atts:                                             # просто скинул фото/видео — в тайник
        _, info = stash_add(uid, content, atts)
        api_send(chat_id, info + "\n(без команды — сохранил в тайник)", [[B("🗃 Тайник", "m:sh")]])
    else:
        m, rows = screen("main", uid)
        api_send(chat_id, m, rows)


def on_channel_message(rec):
    """Сообщение в канале MAX: если тема ждёт привязки канала — привязываем (только каналы, не группы)."""
    tid, cid = BIND.get("theme"), rec.get("chat_id")
    if not tid or not isinstance(cid, int) or any(x.chat_id == cid for x in THEMES):
        return
    if rec.get("chat_type") not in (None, "channel"):
        return
    t = theme_by_id(tid)
    BIND["theme"] = None
    if t is None or t.chat_id:
        return
    t.chat_id = cid
    if t is THEMES[0]:
        _save_channel_id(cid)
    save_cfg()
    print(f"Канал MAX привязан к теме «{t.name}»: chat_id = {cid}")
    with theme_ctx(t):
        notify("✅ Канал MAX привязан к этой теме. Публикую сюда.")


def handle_update(u):
    kind = u.get("update_type")
    uid = None
    if kind == "message_callback":
        uid = ((u.get("callback") or {}).get("user") or {}).get("user_id")
    elif kind == "message_created":
        uid = ((u.get("message") or {}).get("sender") or {}).get("user_id")
    elif kind == "bot_started":
        uid = (u.get("user") or {}).get("user_id")
    with theme_ctx(active_theme(uid)):           # меню работает с темой, выбранной этим админом
        _handle_update(u, kind)


def _handle_update(u, kind):
    if kind == "message_callback":
        cb = dict(u.get("callback") or {})
        cb["_chat"] = ((u.get("message") or {}).get("recipient") or {}).get("chat_id")
        if is_admin((cb.get("user") or {}).get("user_id")):
            handle_callback(cb)
    elif kind == "message_created":
        msg = u.get("message") or {}
        rec = msg.get("recipient") or {}
        if rec.get("chat_type") != "dialog":
            on_channel_message(rec)                    # посты в каналах игнорируем (кроме привязки)
            return
        sender = msg.get("sender") or {}
        name = sender.get("first_name") or sender.get("name") or ""
        handle_message(sender.get("user_id"), rec.get("chat_id"), name,
                       (msg.get("body") or {}).get("text") or "", msg)
    elif kind == "bot_added":
        adder = (u.get("user") or {}).get("user_id")
        if (u.get("is_channel") or u.get("chat_type") == "channel") and (not CFG["admins"] or is_admin(adder)):
            on_channel_message({"chat_id": u.get("chat_id"), "chat_type": "channel"})
    elif kind == "bot_started":
        user = u.get("user") or {}
        handle_message(user.get("user_id"), u.get("chat_id"),
                       user.get("first_name") or user.get("name") or "", "/start")


def menu_loop():
    """Слушает события бота MAX (кнопки и сообщения) — работает в отдельном потоке."""
    global CLAIM_CODE
    if not CFG["admins"]:
        try:                                             # код не меняется при перезапусках, пока им не воспользуются
            with open("claim_code.txt") as f:
                CLAIM_CODE = f.read().strip()
        except OSError:
            CLAIM_CODE = ""
        if not re.fullmatch(r"\d{6}", CLAIM_CODE or ""):
            CLAIM_CODE = str(random.randint(100000, 999999))
            with contextlib.suppress(OSError), open("claim_code.txt", "w") as f:
                f.write(CLAIM_CODE)
        print("=" * 40)
        print("МЕНЮ: открой своего бота в MAX, нажми «Старт»")
        print("и отправь ему этот код:", CLAIM_CODE)
        print("=" * 40)
    else:
        print("Меню готово: напиши своему боту в MAX /start")
        new_console_code()
    register_commands()
    drop_webhooks()
    marker, fails, hooks_at, empty, last_err = None, 0, time.time(), 0, None
    while True:
        # вебхук могли поставить снова (хостинг, вторая копия): раз в минуту и после пустых ответов — проверяем
        if time.time() - hooks_at > 60 or (empty >= 2 and time.time() - hooks_at > 30):
            hooks_at, empty = time.time(), 0
            drop_webhooks()
        try:
            params = {"timeout": 25, "limit": 100,
                      "types": "message_created,message_callback,bot_started,bot_added"}
            if marker:
                params["marker"] = marker
            r = S.get(f"{MAX_API}/updates", headers=HEADERS, params=params, timeout=45)
            if r.status_code != 200:
                if (r.status_code, int(time.time() // 300)) != last_err:
                    last_err = (r.status_code, int(time.time() // 300))
                    print(f"Меню: MAX /updates ответил {r.status_code} {r.text[:120]}"
                          + (" — токен не принят, проверь BOT_TOKEN / max_token" if r.status_code in (401, 403) else ""))
                if r.status_code not in (401, 403) and time.time() - hooks_at > 30:   # 405 и т.п. — мешает вебхук
                    hooks_at = time.time()
                    drop_webhooks()
                time.sleep(5)
                continue
            data = r.json()
            marker = data.get("marker", marker)
            empty = 0 if data.get("updates") else empty + 1
            if fails >= 3:
                print("Меню: связь с MAX восстановлена ✅")
            fails = 0
            for u in data.get("updates", []):
                try:
                    handle_update(u)
                except Exception:
                    log_exc("обработка сообщения в меню")
        except (requests.ConnectionError, requests.Timeout) as e:
            # мобильный интернет рвёт долгие соединения — это нормально, просто переподключаемся
            fails += 1
            _log_line(time.strftime("%H:%M:%S") + f" Меню: обрыв связи с MAX ({fails} подряд): {e}")
            if fails == 3:
                print("Меню: нет связи с MAX — проверь интернет. Бот переподключится сам.")
            time.sleep(5 if fails < 3 else 15)
        except Exception as e:
            print("Меню: сбой связи:", type(e).__name__, e)
            time.sleep(5)


# ---------- ОЧЕРЕДЬ И РАСПИСАНИЕ ----------
def drop_item(it):
    """Убирает пост из очереди и удаляет его фото."""
    for p in it.get("photos", []):
        try:
            os.remove(p)
        except OSError:
            pass
    if it in QUEUE:
        QUEUE.remove(it)


def batch_for_head(head):
    """Первый пост + следующие посты ТОГО ЖЕ канала, вышедшие с разницей до окна серии."""
    if not CFG["auto_series"]:
        return [head]
    burst = CFG["burst_min"] * 60
    same = sorted([x for x in QUEUE if x["ch"] == head["ch"] and x is not head
                   and x["date"] >= head["date"]], key=lambda x: x["date"])
    batch = [head]
    for x in same:
        if len(batch) >= CFG["burst_max"] or burst <= 0:
            break
        if x["date"] - batch[-1]["date"] <= burst:
            batch.append(x)
        else:
            break
    return batch


def is_followup(it):
    """Пост канала вышел вслед за уже опубликованным (в окне серии) — можно сразу."""
    if not CFG["auto_series"]:
        return False
    ch = it["ch"]
    last = PUB["ch_last_date"].get(ch)
    burst = CFG["burst_min"] * 60
    if last is None or burst <= 0:
        return False
    return 0 <= it["date"] - last <= burst and PUB["series"].get(ch, 0) < CFG["burst_max"]


VIDEO_RETRY_PAUSES = [3, 10]      # паузы между попытками скачать видео из Telegram (сек)


class MediaLost(Exception):
    """Вложение поста потерялось или не скачалось: без решения админа пост без него не выходит."""


def has_image(m):
    """Фото в посте Telegram, в том числе картинка, отправленная файлом."""
    if getattr(m, "photo", None):
        return True
    d = getattr(m, "document", None)
    mt = (getattr(d, "mime_type", "") or "") if d is not None else ""
    return (mt.startswith("image/") and mt != "image/gif"
            and not getattr(m, "sticker", None) and not getattr(m, "gif", None))


def photo_ext(m):
    d = getattr(m, "document", None)
    mt = (getattr(d, "mime_type", "") or "") if d is not None and not getattr(m, "photo", None) else ""
    return {"image/png": ".png", "image/webp": ".webp"}.get(mt, ".jpg")


async def download_photo(m, dest):
    path = await m.download_media(file=dest)
    return path if path and os.path.exists(path) and os.path.getsize(path) > 0 else None


async def ensure_photos(client, it):
    """Фото поста. Если файл пропал с диска — докачиваем из Telegram, а не публикуем пост без фото."""
    photos = it.get("photos", [])
    have = [p for p in photos if os.path.exists(p)]
    if len(have) == len(photos):
        return have
    ids = it.get("pids") or []
    os.makedirs(MEDIA_DIR, exist_ok=True)
    out = []
    for i, p in enumerate(photos):
        if os.path.exists(p):
            out.append(p)
            continue
        got = None
        if i < len(ids):
            try:
                msg = await client.get_messages(it["ch"], ids=ids[i])
                if msg and has_image(msg):
                    got = await download_photo(msg, p)
            except Exception as e:
                print(f"[{it['ch']}] фото {ids[i]} не докачалось: {e}")
        if got:
            out.append(got)
        elif not it.get("nomedia"):
            raise MediaLost(f"фото пропало с диска и не докачалось ({i + 1} из {len(photos)})")
    return out


async def download_video(client, ch, vid):
    """Качает видео поста из Telegram. Возвращает (путь, None) или (None, причина)."""
    os.makedirs(MEDIA_DIR, exist_ok=True)
    dest = f"{MEDIA_DIR}/{cur().fs}v_{ch}_{vid}.mp4"
    err = "неизвестная ошибка"
    attempts = len(VIDEO_RETRY_PAUSES) + 1
    for n in range(attempts):
        pause = VIDEO_RETRY_PAUSES[n] if n < len(VIDEO_RETRY_PAUSES) else 0
        try:
            msg = await client.get_messages(ch, ids=vid)
            if not msg:
                err = "пост удалён или недоступен"
            elif not video_doc(msg):
                err = "в посте нет видео"
                break                                  # повторять бессмысленно
            else:
                path = await msg.download_media(file=dest)
                if path and os.path.exists(path) and os.path.getsize(path) > 0:
                    return path, None
                err = "Telegram отдал пустой файл"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:150]
            pause = max(pause, min(int(getattr(e, "seconds", 0) or 0), 120))   # FloodWait
        if os.path.exists(dest):                       # огрызок после обрыва
            try:
                os.remove(dest)
            except OSError:
                pass
        if n < attempts - 1:
            print(f"[{ch}] видео {vid}: попытка {n + 1} не удалась ({err}), повторю...")
            await asyncio.sleep(pause)
    return None, err


async def to_stuck(it, why):
    """Пост не может выйти полностью — ждёт решения админа (повторить / без вложения / убрать)."""
    it["status"] = "stuck"
    it["stuck_why"] = str(why)[:150]
    save_queue()
    print(f"[{it['ch']}] пост {it['id']} ждёт решения админа: {why}")
    await asyncio.to_thread(send_stuck_card, it)
    return "stuck"


async def publish_item(client, chat_id, it):
    """'ok', 'retry', 'stuck' (ждёт решения админа) или 'dropped'. Отправленные части не шлёт повторно."""
    try:
        chunks = split_text((it["text"] + "\n\n" + signature()).strip())
        text, extra = chunks[0], chunks[1:]
        photos = await ensure_photos(client, it)
        parts = []
        for k in range(0, len(photos), 10):          # в одном сообщении MAX — до 10 вложений
            parts.append(("img", text, photos[k:k + 10], None))
            text = ""
        if CFG["video_on"]:
            for vid in it.get("vids", []):
                parts.append(("vid", text, [], vid))
                text = ""
        if not parts:
            parts.append(("txt", text, [], None))
        parts += [("txt", c, [], None) for c in extra]       # хвост длинного текста — отдельными сообщениями
        for i, (kind, t, imgs, vid) in enumerate(parts):
            if i < it.get("sent", 0):
                continue                           # эта часть уже ушла в прошлую попытку
            if kind == "vid":
                print(f"[{it['ch']}] качаю видео поста {vid}...")
                path, verr = await download_video(client, it["ch"], vid)
                if not path:
                    if not it.get("nomedia") and it.get("sent", 0) == 0:
                        raise MediaLost(f"видео не скачалось: {verr}")
                    print(f"[{it['ch']}] видео {vid} не скачалось ({verr}) — пропускаю его")
                    await asyncio.to_thread(
                        notify, f"⚠️ В посте из @{it['ch']} не скачалось видео: {verr}. Остальное выложил без него.")
                    if t:                      # текст был привязан к видео — не теряем его
                        await asyncio.to_thread(send_max, chat_id, t, [], None)
                else:
                    try:
                        await asyncio.to_thread(send_max, chat_id, t, [], path)
                    finally:
                        if os.path.exists(path):
                            os.remove(path)
            else:
                await asyncio.to_thread(send_max, chat_id, t, imgs, None)
            it["sent"] = i + 1
            save_queue()
        bump("published")
        journal(f"[{it['ch']}] опубликован пост {it['id']}")
        return "ok"
    except MediaLost as e:
        return await to_stuck(it, e)
    except Exception as e:
        it["fails"] = it.get("fails", 0) + 1
        bump("errors")
        print(f"[{it['ch']}] ошибка публикации {it['id']} (попытка {it['fails']}):", e)
        if it["fails"] >= 3:
            if (it.get("photos") or it.get("vids")) and not it.get("nomedia"):
                return await to_stuck(it, f"не отправился в MAX: {str(e)[:100]}")
            drop_item(it)
            save_queue()
            await asyncio.to_thread(
                notify, f"⚠️ Пост из @{it['ch']} не удалось опубликовать, пропущен.\nПричина: {str(e)[:150]}")
            return "dropped"
        it["retry_at"] = time.time() + 60
        save_queue()
        return "retry"


def prio_key(x):
    """Чем важнее канал, тем «старше» считается его пост (до 3 часов форы)."""
    return x["date"] - (CFG["priority"].get(x["ch"], 1) - 1) * 3 * 3600


def mark_published(it):
    """Запоминает публикацию (для расписания) и убирает пост из очереди."""
    ch = it["ch"]
    PUB["ch_last_date"][ch] = max(PUB["ch_last_date"].get(ch, 0), it["date"])
    PUB["ch_last_pub"][ch] = time.time()
    PUB["last_pub"] = time.time()
    save_pub()
    drop_item(it)
    save_queue()


async def publish_stash(chat_id):
    """Выкладывает следующий пост из тайника. True — выложено."""
    it = next((x for x in STASH if x.get("retry_at", 0) <= time.time()), None)
    if it is None:
        return False
    text = (it.get("text", "") + "\n\n" + signature()).strip()

    def media(lst):
        out = []
        for p in lst:
            if p.get("path") and os.path.exists(p["path"]):
                out.append(p["path"])
            elif p.get("token"):
                out.append({"token": p["token"]})
        return out
    parts = build_parts(text, media(it.get("photos", [])), media(it.get("videos", [])))
    try:
        for i, (t, imgs, v) in enumerate(parts):
            if i < it.get("sent", 0):
                continue                               # эта часть уже ушла в прошлую попытку
            await asyncio.to_thread(send_max, chat_id, t, imgs, v)
            it["sent"] = i + 1
            save_stash()
    except Exception as e:
        it["fails"] = it.get("fails", 0) + 1
        it["retry_at"] = time.time() + 120
        bump("errors")
        print(f"Тайник: ошибка публикации (попытка {it['fails']}):", e)
        if it["fails"] >= 3:
            stash_remove(it, save=False)
            await asyncio.to_thread(notify, f"⚠️ Пост из тайника не удалось выложить, убрал его.\nПричина: {str(e)[:150]}")
        save_stash()
        return False
    stash_remove(it)
    PUB["last_pub"] = time.time()
    bump("published")
    bump("stash")
    print(f"Тайник: выложен пост, осталось {len(STASH)}")
    if len(STASH) <= 2:
        await asyncio.to_thread(notify, f"🗃 В тайнике осталось: {len(STASH)}. Подкинь ещё мемов — /add")
    return True


async def maybe_stash(chat_id, now):
    """Постов не хватает (в очереди нечего выкладывать) — берём из тайника."""
    if not CFG["stash_on"] or not STASH:
        return
    if any(x.get("status", "ok") == "ok" for x in QUEUE):
        return                                         # в очереди уже есть что выкладывать
    if now - PUB["last_pub"] < max(CFG["gap_min"], 10) * 60:
        return
    await publish_stash(chat_id)


async def publish_due(client, chat_id):
    now = time.time()
    limit = CFG["max_age_h"] * 3600
    stale = [it for it in QUEUE if not it.get("manual") and now - it["enq"] > limit]
    for it in stale:
        drop_item(it)
        bump("expired")
        print(f"[{it['ch']}] пост {it['id']} устарел в очереди — удалён")
    if stale:
        save_queue()
        resolve_cards(item_refs(stale))
    force = now < FORCE["until"]
    ready = [x for x in QUEUE if x.get("status", "ok") == "ok"
             and x.get("retry_at", 0) <= now and x.get("at", 0) <= now]

    # 1) посты с кнопок «Сейчас» / «Через …»: выходят в своё время, без паузы и ночного режима.
    #    Пачка с одним временем уходит подряд — это и есть «оба вместе».
    for x in sorted([x for x in ready if x.get("manual")], key=lambda x: (x.get("at", 0), x["date"])):
        res = await publish_item(client, chat_id, x)
        if res == "retry":
            break
        if res == "ok":
            mark_published(x)
    ready = [x for x in ready if x in QUEUE and not x.get("manual")]

    if in_night() and not force:
        return                                       # ночью ничего не выходит
    if not ready:
        await maybe_stash(chat_id, now)
        return

    # 2) срочные посты — без очереди и паузы (но не больше N в час)
    PUB["urgent_times"] = [t for t in PUB.get("urgent_times", []) if now - t < 3600]
    if CFG["urgent_on"] and not force:
        for x in sorted([x for x in ready if x.get("urgent")], key=lambda x: x["date"]):
            if len(PUB["urgent_times"]) >= CFG["urgent_max_h"]:
                break
            res = await publish_item(client, chat_id, x)
            if res == "retry":
                return
            if res == "ok":
                PUB["urgent_times"].append(time.time())
                PUB["ch_last_date"][x["ch"]] = max(PUB["ch_last_date"].get(x["ch"], 0), x["date"])
                bump("urgent")
                save_pub()
                drop_item(x)
                save_queue()
        ready = [x for x in ready if x in QUEUE]
        if not ready:
            return

    # 3) обычные посты по расписанию: один пост за паузу
    gap = CFG["gap_min"] * 60 if CFG["queue_on"] else 0
    new_series = True
    if force or gap == 0:
        due = sorted(ready, key=prio_key)
    else:
        due = []
        if CFG["auto_series"]:
            for x in sorted(ready, key=lambda x: x["date"]):
                if is_followup(x):                   # серия: сразу, не ждём паузу
                    due, new_series = [x], False
                    break
        if not due and now - last_slot() >= gap:
            due = [x for x in batch_for_head(min(ready, key=prio_key)) if x in ready]
    seen = set()
    for it in due:
        res = await publish_item(client, chat_id, it)
        if res == "retry":
            break
        if res in ("dropped", "stuck"):
            continue
        ch = it["ch"]
        if new_series and ch not in seen:
            PUB["series"][ch] = 1
        else:
            PUB["series"][ch] = PUB["series"].get(ch, 0) + 1
        seen.add(ch)
        mark_published(it)


START = time.time()
TICK = 5               # как часто планировщик проверяет очередь, секунд


def upcoming_posts(now, horizon=3600):
    """Сколько постов выйдет в ближайший час: назначенные, по расписанию и из тайника."""
    gap = CFG["gap_min"] * 60 if CFG["queue_on"] else 0
    n, auto_ok = 0, False
    for x in QUEUE:
        if x.get("status", "ok") != "ok":
            continue
        if x.get("manual"):
            n += x.get("at", 0) <= now + horizon
        else:
            auto_ok = True
    if auto_ok and last_slot() + gap <= now + horizon:
        n += 1
    queue_has_ok = any(x.get("status", "ok") == "ok" for x in QUEUE)
    if CFG["stash_on"] and STASH and not queue_has_ok \
            and PUB["last_pub"] + max(CFG["gap_min"], 10) * 60 <= now + horizon:
        n += 1
    return n


def check_empty(now):
    """Если на ближайший час ничего не выйдет — пишем админам (не чаще раза в 3 часа)."""
    if not CFG["empty_warn_on"] or in_night() or now - START < 120:
        return
    t = cur()
    if upcoming_posts(now) > 0:
        t.flags["empty_at"] = 0                      # посты появились — в следующий раз предупредим снова
        return
    if t.flags["empty_at"] and now - t.flags["empty_at"] < 3 * 3600:
        return
    t.flags["empty_at"] = now
    pend = sum(1 for x in QUEUE if x.get("status") == "pending")
    stuck = sum(1 for x in QUEUE if x.get("status") == "stuck")
    later = [x["at"] for x in QUEUE if x.get("status", "ok") == "ok" and x.get("manual") and x.get("at", 0) > now]
    lines = ["📭 На ближайший час постов нет."]
    lines.append("Очередь пуста." if not QUEUE else f"В очереди: {len(QUEUE)}, но в этот час ничего не выйдет.")
    if later:
        lines.append("Следующий назначен на " + time.strftime("%H:%M", time.localtime(min(later))) + ".")
    if not CFG["stash_on"]:
        lines.append("Тайник выключен.")
    elif not STASH:
        lines.append("Тайник пуст — подкинь мемов: /add")
    if pend:
        lines.append(f"Ждут одобрения: {pend} — /approve")
    if stuck:
        lines.append(f"Ждут решения (⚠️): {stuck} — смотри карточки")
    notify("\n".join(lines))


async def scheduler(client, t):
    """Планировщик одной темы: раз в несколько секунд проверяет, не пора ли что-то публиковать."""
    CUR.set(t)
    while t in THEMES:
        await asyncio.sleep(TICK)
        if CFG["paused"] or not t.chat_id:
            continue
        try:
            async with t.lock:
                if FLAGS["stash_now"]:
                    FLAGS["stash_now"] = False
                    if not await publish_stash(t.chat_id):
                        await asyncio.to_thread(notify, "🗃 Из тайника не вышло (пусто или ошибка) — смотри консоль.")
                await publish_due(client, t.chat_id)
            await asyncio.to_thread(check_empty, time.time())
        except Exception:
            log_exc(f"планировщик «{t.name}»")


async def background_loop():
    """Общие дела: убираем старые карточки у админов, шлём ежедневный отчёт."""
    n = 0
    while True:
        await asyncio.sleep(TICK)
        try:
            await asyncio.to_thread(purge_cards)
            n += 1
            today = time.strftime("%Y-%m-%d", time.localtime(time.time()))
            if n % 6 == 0 and CFG["report_on"] and META["last_report"] != today \
                    and local_hour() >= CFG["report_hour"]:
                await asyncio.to_thread(send_report)
        except Exception as e:
            print("Фоновые задачи: ошибка:", e)


# ---------- ЧТЕНИЕ КАНАЛОВ ----------
FRESH_SEC = 8          # пост берём в работу, когда он не меняется N секунд (альбом долетел целиком)
BACKLOG_MAX = 10       # не больше стольких накопившихся постов канала за один раз
SEEN = {}              # (тема, канал, пост) -> (когда впервые увидели, id сообщений)
RECHECK = set()        # каналы, которые надо перечитать через FRESH_SEC секунд
WATCH = set()          # имена каналов (в нижнем регистре), за которыми следим
WAKE_NAMES = set()     # каналы, в которых Telegram сообщил о новом посте
WAKE = None            # asyncio.Event: «пришёл новый пост» — читаем сразу, не ждём интервала


def video_doc(m):
    """Видео/GIF/кружок/файл-видео в посте Telegram, иначе None."""
    doc = m.video or m.gif or getattr(m, "video_note", None)
    if doc:
        return doc
    d = getattr(m, "document", None)
    if d is not None and (getattr(d, "mime_type", "") or "").startswith("video/"):
        return d
    return None


def video_info(m):
    """(размер в байтах, длительность) для видео/GIF, иначе None."""
    doc = video_doc(m)
    if not doc:
        return None
    dur = 0
    for attr in getattr(doc, "attributes", []):
        if hasattr(attr, "duration"):
            dur = int(attr.duration or 0)
    return int(getattr(doc, "size", 0) or 0), dur


async def process_channel(client, ch, fails):
    """Новые посты канала текущей темы. Карточки одобрения уходят ПАЧКОЙ после обхода (см. send_previews)."""
    fresh = []
    try:
        await _process_channel(client, ch, fails, fresh)
    finally:
        if fresh:
            await asyncio.to_thread(send_previews, fresh)


async def _process_channel(client, ch, fails, fresh):
    """Читает новые посты канала, чистит и ставит в очередь на публикацию."""
    t = cur()
    history = t.history
    msgs = await client.get_messages(ch, min_id=LAST_IDS[ch], limit=30)
    msgs = sorted(msgs, key=lambda m: m.id)

    # склеиваем альбомы (несколько фото/видео = один пост)
    groups, order = {}, []
    for m in msgs:
        key = m.grouped_id or f"single{m.id}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(m)

    if len(order) > BACKLOG_MAX:                     # бот долго был выключен: не заваливаем админа старьём
        skipped = [m for key in order[:-BACKLOG_MAX] for m in groups[key]]
        order = order[-BACKLOG_MAX:]
        LAST_IDS[ch] = max(LAST_IDS[ch], max(m.id for m in skipped))
        save_last_ids()
        journal(f"[{ch}] накопилось много новых постов — пропускаю самые старые ({len(skipped)} сообщений), "
                f"показываю последние {BACKLOG_MAX}")
        await asyncio.to_thread(notify, f"ℹ️ @{ch}: накопилось много постов, пока бот был выключен. "
                                        f"Старые пропустил, показываю последние {BACKLOG_MAX}.")
    os.makedirs(MEDIA_DIR, exist_ok=True)
    # Считаем по СВОИМ часам «с момента, как увидели»: так не зависим от часов на телефоне и в Telegram.
    now_m = time.monotonic()
    for key in order:
        sig = tuple(m.id for m in groups[key])
        seen = SEEN.get((t.id, ch, key))
        if seen is None or seen[1] != sig:
            SEEN[(t.id, ch, key)] = (now_m, sig)
    for key in order:
        if ch not in LAST_IDS or CFG["paused"]:
            return
        grp = groups[key]
        skey = (t.id, ch, key)
        if now_m - SEEN[skey][0] < FRESH_SEC:
            RECHECK.add(ch.lower())       # ещё «устаканивается» — перечитаем через пару секунд
            return
        last = grp[-1].id
        raw = next((m.text for m in grp if m.text), "")
        body = clean_text(raw, with_signature=False)
        img_paths, pids, hashes = [], [], []
        queued = False
        try:
            # фото: скачиваем сразу (они лёгкие), считаем отпечаток для дублей
            for m in grp:
                if has_image(m):
                    dest = f"{MEDIA_DIR}/{t.fs}{ch}_{m.id}{photo_ext(m)}"
                    p = await download_photo(m, dest)
                    if not p:
                        raise RuntimeError(f"фото {m.id} не скачалось")
                    img_paths.append(p)
                    pids.append(m.id)
                    with open(p, "rb") as f:
                        hashes.append(hashlib.md5(f.read()).hexdigest())
            # видео/GIF: пока только данные о файле — качать будем при публикации
            vids = []
            if CFG["video_on"]:
                for m in grp:
                    vi = video_info(m)
                    if vi:
                        vids.append((m, vi))
                        hashes.append(f"v{vi[0]}-{vi[1]}")
            entry = {"norm": norm(body), "img": hashes, "t": time.time(),
                     "fwd": [k for k in (fwd_key(m) for m in grp) if k]}
            has_media = bool(img_paths or vids)
            dup = find_duplicate(entry, history) if CFG["dedupe_on"] else None
            if not body and not has_media:
                journal(f"[{ch}] пост {last}: пусто после чистки — пропускаю")
            elif (bw := blocked_word(body)) is not None:
                bump("blocked")
                journal(f"[{ch}] пост {last}: стоп-слово «{bw}» — не публикую")
            elif dup is not None:
                # запоминаем и этот пересказ, чтобы ловить следующие похожие
                alts = dup.setdefault("alts", [])
                if entry["norm"] and len(alts) < 6:
                    alts.append(entry["norm"])
                    save_history()
                bump("dups")
                journal(f"[{ch}] пост {last}: ДУБЛЬ по смыслу, уже публиковали — пропускаю")
            elif any(vi[0] > CFG["video_mb"] * 1024 * 1024 for _, vi in vids):
                journal(f"[{ch}] пост {last}: видео больше {CFG['video_mb']} МБ — пропускаю")
                await asyncio.to_thread(notify, f"⚠️ @{ch}: видео больше {CFG['video_mb']} МБ — пост пропущен.")
            elif len(QUEUE) >= 100:
                journal(f"[{ch}] пост {last}: очередь переполнена (100) — пропускаю")
            else:
                status = "pending" if CFG["approval_on"] else "ok"
                if status == "pending" and not CFG["admin_chats"]:
                    journal("⚠️ Некому показать пост на одобрение: напиши своему боту в MAX /start")
                item = {"id": f"{t.prefix}{ch}:{grp[0].id}", "ch": ch,
                        "date": grp[0].date.timestamp(), "enq": time.time(),
                        "text": body, "photos": img_paths, "pids": pids,
                        "vids": [m.id for m, _ in vids], "sent": 0, "fails": 0,
                        "urgent": is_urgent(body), "status": status}
                QUEUE.append(item)
                save_queue()
                history.append(entry)               # сразу помним, чтобы ловить дубли в очереди
                save_history()
                queued = True
                journal(f"[{ch}] пост {last} поставлен в очередь (всего {len(QUEUE)})"
                      + (" — жду одобрения" if status == "pending" else ""))
                if status == "pending":
                    fresh.append(item)
        except Exception as e:
            fails[(t.id, ch, last)] = fails.get((t.id, ch, last), 0) + 1
            n = fails[(t.id, ch, last)]
            bump("errors")
            print(f"[{ch}] ошибка на посте {last} (попытка {n}):", e)
            if n < 3:
                return                    # повторим на следующей проверке
            journal(f"[{ch}] пост {last} не получается обработать — пропускаю")
            await asyncio.to_thread(notify, f"⚠️ @{ch}: пост {last} не удалось обработать ({str(e)[:100]}), пропущен.")
        finally:
            if not queued:
                for p in img_paths:
                    if p and os.path.exists(p):
                        os.remove(p)
        SEEN.pop(skey, None)
        if ch in LAST_IDS:
            LAST_IDS[ch] = max(LAST_IDS[ch], last)
            save_last_ids()


async def sync_channels(client):
    """Подхватывает каналы текущей темы. Не открывшийся канал не выбрасываем навсегда — пробуем снова."""
    saved = cur().saved_ids
    for ch in list(CFG["channels"]):
        if ch in LAST_IDS or BAD.get(ch, 0) > time.time() or net_blocked(ch):
            continue
        try:
            latest = await asyncio.wait_for(client.get_messages(ch, limit=1), 60)
        except Exception as e:
            if is_net_error(e):                        # сеть, а не канал: не пугаем «имя неверное»
                await reconnect_tg(client)
                await asyncio.to_thread(note_net, ch, e, is_web_client(client))
                continue
            first = ch not in BAD
            BAD[ch] = time.time() + 600                # повтор через 10 минут
            journal(f"Канал {ch} не открывается: {type(e).__name__}: {e}")
            if first:
                await asyncio.to_thread(
                    notify, f"⚠️ Канал @{ch} не открывается ({type(e).__name__}). Пробую каждые 10 минут. "
                            f"Если имя неверное — убери его в меню.")
            continue
        note_net(ch)
        if ch in BAD:
            del BAD[ch]
            journal(f"Канал {ch} снова открывается")
        latest_id = latest[0].id if latest else 0
        if TEST_REPOST_LAST:
            LAST_IDS[ch] = max(latest_id - 1, 0)
            print(f"ТЕСТ: [{ch}] переопубликую последний пост {latest_id}")
        elif ch in saved:
            LAST_IDS[ch] = saved[ch]
        else:
            LAST_IDS[ch] = latest_id     # новый канал: старое не постим
    save_last_ids()


async def run_test(client):
    """Тест-репост из меню: берём последний пост каждого канала темы заново и публикуем сразу."""
    cur().history.clear()
    save_history()
    FORCE["until"] = time.time() + 300
    for ch in list(LAST_IDS):
        try:
            latest = await client.get_messages(ch, limit=1)
            LAST_IDS[ch] = max((latest[0].id if latest else 0) - 1, 0)
        except Exception as e:
            print(f"ТЕСТ: [{ch}] не открылся:", e)
    save_last_ids()


NET = {"fails": {}, "until": {}, "since": {}, "warned": False}   # сеть до Telegram: по каналам


def is_net_error(e):
    return isinstance(e, (requests.ConnectionError, requests.Timeout, ConnectionError, TimeoutError,
                          asyncio.TimeoutError, socket.gaierror))


def net_blocked(ch):
    return NET["until"].get(ch, 0) > time.time()


TG_DOWN_TEXT = ("⚠️ Telegram (t.me) не открывается с этого сервера — каналы не читаются.\n\n"
                "Скорее всего, сервер в России: там Telegram заблокирован. Что делать: перенести бота на сервер "
                "за границей (на Bothost — локация «Нидерланды») или задать переменную TG_PROXY с прокси. "
                "Как только t.me откроется, бот продолжит сам.")


def note_net(ch, e=None, web=True):
    """Учёт сетевых ошибок t.me: пауза растёт (1, 2, 4… до 10 минут), в лог — только смена состояния."""
    if e is None:
        if NET["fails"].pop(ch, None):
            journal(f"Канал {ch}: t.me снова отвечает ✅")
        NET["until"].pop(ch, None)
        NET["since"].pop(ch, None)
        for k in [k for k in NET["fails"] if WATCH and k.lower() not in WATCH]:   # каналы, которые уже убрали
            for d in (NET["fails"], NET["until"], NET["since"]):
                d.pop(k, None)
        if not NET["fails"]:
            NET["warned"] = False
        return
    n = NET["fails"].get(ch, 0) + 1
    NET["fails"][ch] = n
    NET["until"][ch] = time.time() + min(600, 60 * 2 ** min(n - 1, 4))
    since = NET["since"].setdefault(ch, time.time())
    if n == 1:
        journal(f"Канал {ch}: {'t.me' if web else 'Telegram'} не отвечает ({type(e).__name__}) — "
                f"пробую реже, пока не заработает")
    _log_line(time.strftime("%H:%M:%S") + f" t.me {ch}: {type(e).__name__}: {e}")
    if not NET["warned"] and time.time() - since > 180 and CFG["admins"]:
        NET["warned"] = True
        text = TG_DOWN_TEXT if web else ("⚠️ Нет связи с Telegram уже несколько минут — каналы не читаются. "
                                         "Проверь интернет. Бот переподключится сам.")
        print(text.replace("\n\n", " "))
        notify(text, tag=False)


async def reconnect_tg(client):
    """Аккаунт Telegram (Telethon) после долгого обрыва сам не переподключается — помогаем."""
    cur = getattr(client, "cur", client)
    if is_web_client(cur) or not hasattr(cur, "is_connected"):
        return
    with contextlib.suppress(Exception):
        if not cur.is_connected():
            await asyncio.wait_for(cur.connect(), 30)


async def poll(client, t, ch, fails, sem):
    CUR.set(t)
    async with sem:
        try:
            await process_channel(client, ch, fails)
            note_net(ch)
        except Exception as e:
            if is_net_error(e):
                await reconnect_tg(client)
                await asyncio.to_thread(note_net, ch, e, is_web_client(client))
            else:
                log_exc(f"чтение канала {ch}")


# ---------- ЧТЕНИЕ БЕЗ ВХОДА В TELEGRAM (публичные каналы через t.me/s/<канал>) ----------
import html as _html
from datetime import datetime as _dt
from datetime import timezone as _tz

WEB_FLAG = "web_mode.flag"             # есть этот файл — бот работает без входа в Telegram
WEB_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0 Mobile Safari/537.36", "Accept-Language": "ru,en;q=0.8"}


class WebMediaUnavailable(RuntimeError):
    pass


class _Media:
    """Вложение из веб-версии: известна только ссылка (у большого видео её нет)."""

    def __init__(self, url):
        self.url = url
        self.size = 0
        self.attributes = []
        self.mime_type = ""


class WebMessage:
    """Похож на сообщение Telethon ровно настолько, насколько нужно боту."""

    def __init__(self, ch, mid, date, text="", photo=None, video=None, grouped_id=None):
        self.ch, self.id, self.date, self.text = ch, mid, date, text
        self.grouped_id = grouped_id
        self.photo = _Media(photo) if photo else None
        self.video = video                       # _Media (url может быть None) или None
        self.gif = self.video_note = self.document = self.sticker = self.fwd_from = None

    async def download_media(self, file=None):
        media = self.photo or self.video
        if media is None or not media.url:
            raise WebMediaUnavailable("видео слишком большое для веб-версии Telegram (нужен вход по коду)")
        return await asyncio.to_thread(_download_url, media.url, file)


def _download_url(url, dest):
    r = WS.get(url, headers=WEB_UA, timeout=(10, 120), stream=True)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    return dest


def web_fetch(url):
    last = None
    for host in TG_HOSTS:
        u = url.replace("https://t.me/", f"https://{host}/", 1)
        try:
            r = WS.get(u, headers=WEB_UA, timeout=(8, 20))
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            continue
        if r.status_code != 200:
            raise RuntimeError(f"{host} ответил {r.status_code}")
        return r.text
    raise last


def _attr(tag, name):
    m = re.search(r'\b' + name + r'="([^"]*)"', tag)
    return _html.unescape(m.group(1)) if m else None


def _abs_url(u):
    if not u:
        return None
    u = _html.unescape(u)
    return "https:" + u if u.startswith("//") else u


def _html_to_md(h):
    """HTML поста -> текст с разметкой, как отдаёт Telethon (**жирный**, __курсив__, [текст](ссылка))."""
    h = re.sub(r"<br\s*/?>", "\n", h)
    h = re.sub(r"<(b|strong)>(.*?)</\1>", r"**\2**", h, flags=re.S)
    h = re.sub(r"<(i|em)>(.*?)</\1>", r"__\2__", h, flags=re.S)
    h = re.sub(r"<(s|strike|del)>(.*?)</\1>", r"~~\2~~", h, flags=re.S)
    h = re.sub(r"<(code|pre)>(.*?)</\1>", r"`\2`", h, flags=re.S)

    def link(m):
        href, label = _html.unescape(m.group(1)), re.sub(r"<[^>]+>", "", m.group(2))
        return label if not label.strip() or label.strip() == href.strip() else f"[{label}]({href})"
    h = re.sub(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', link, h, flags=re.S)
    h = re.sub(r"<[^>]+>", "", h)
    return _html.unescape(h).strip()


def _parse_block(ch, block):
    m = re.search(r'data-post="([^"/]+)/(\d+)"', block)
    if not m:
        return []                                   # служебные блоки без поста
    bubble = int(m.group(2))
    dm = re.search(r'<time[^>]*datetime="([^"]+)"', block)
    try:
        date = _dt.fromisoformat(dm.group(1)) if dm else _dt.now(_tz.utc)
    except ValueError:
        date = _dt.now(_tz.utc)
    if date.tzinfo is None:
        date = date.replace(tzinfo=_tz.utc)
    text = ""
    for tm in re.finditer(r'<div class="(tgme_widget_message_text[^"]*)"[^>]*>(.*?)</div>', block, re.S):
        if "reply" not in tm.group(1):               # текст ответа-цитаты не берём
            text = _html_to_md(tm.group(2))
            break
    items = []                                       # (id, фото-ссылка | None, видео | None)
    for tag in re.findall(r"<a\b[^>]*>", block):
        if "tgme_widget_message_photo_wrap" in (_attr(tag, "class") or ""):
            href = _attr(tag, "href") or ""
            hm = re.search(r"/(\d+)(?:\?|$)", href)
            bg = re.search(r"background-image:\s*url\(\s*['\"]?([^'\")]+)", _html.unescape(_attr(tag, "style") or ""))
            items.append((int(hm.group(1)) if hm else bubble, _abs_url(bg.group(1)) if bg else None, None))
    if "video_player" in block or "tgme_widget_message_video" in block or "roundvideo" in block:
        vm = re.search(r"<video\b[^>]*\bsrc=\"([^\"]+)\"", block)
        pm = re.search(r'<a\b[^>]*video_player[^>]*href="[^"]*/(\d+)(?:\?[^"]*)?"', block)
        items.append((int(pm.group(1)) if pm else bubble, None,
                      _Media(_abs_url(vm.group(1)) if vm else None)))
    if not items:
        return [WebMessage(ch, bubble, date, text)]
    items.sort(key=lambda x: x[0])
    gid = bubble if len(items) > 1 else None
    return [WebMessage(ch, pid, date, text if k == 0 else "", photo=ph, video=vd, grouped_id=gid)
            for k, (pid, ph, vd) in enumerate(items)]


def parse_web_page(ch, page):
    if "tgme_widget_message_wrap" not in page:
        if "tgme_channel_history" in page:
            return []                                # канал пустой
        raise RuntimeError("у канала нет веб-версии (приватный, закрыт для просмотра или имя неверное)")
    out, seen = [], set()
    for block in re.split(r'(?=<div class="tgme_widget_message_wrap)', page)[1:]:
        for msg in _parse_block(ch, block):
            if msg.id not in seen:
                seen.add(msg.id)
                out.append(msg)
    return out


class WebClient:
    """Подмена клиента Telethon: читает публичные каналы с t.me/s/<канал>. Вход в Telegram не нужен."""
    is_web = True

    def on(self, event):
        return lambda f: f

    async def connect(self):
        pass

    async def is_user_authorized(self):
        return True

    @staticmethod
    def _load(ch, before=None):
        url = f"https://t.me/s/{ch}" + (f"?before={before}" if before else "")
        return parse_web_page(ch, web_fetch(url))

    def _latest(self, ch, min_id):
        msgs = self._load(ch)
        for _ in range(2 if min_id else 0):          # если новых больше, чем на одной странице — листаем назад
            if not msgs or min(m.id for m in msgs) <= min_id + 1:
                break
            older = self._load(ch, min(m.id for m in msgs))
            if not older:
                break
            msgs += older
        print(f"[web] {ch}: на странице постов — {len(msgs)}")
        return msgs

    async def get_messages(self, ch, min_id=0, limit=30, ids=None):
        if ids is not None:
            msgs = await asyncio.to_thread(self._load, ch, ids + 1)
            return next((m for m in msgs if m.id == ids), None)
        msgs = await asyncio.to_thread(self._latest, ch, min_id)
        return sorted([m for m in msgs if m.id > min_id], key=lambda m: -m.id)[:limit]


class LoginBlocked(Exception):
    """Telegram не прислал код так, чтобы его можно было ввести здесь. Текст — что делать пользователю."""


def _explain(e):
    """Понятное объяснение типичных ошибок входа в Telegram (по названию ошибки)."""
    if isinstance(e, LoginBlocked):
        return str(e)
    hints = {
        "ApiIdInvalidError": "api_id и api_hash не подходят. Зайди на my.telegram.org, войди этим же номером, "
                             "открой «API development tools», создай приложение (любое название) и впиши новые "
                             "api_id и api_hash в config.json.",
        "ApiIdPublishedFloodError": "Эти api_id/api_hash Telegram заблокировал. Создай новые на my.telegram.org "
                                    "(API development tools) и впиши в config.json.",
        "PhoneNumberInvalidError": "Номер указан неверно. Нужен формат +79991234567 (поле phone в config.json).",
        "PhoneNumberBannedError": "Этот номер заблокирован в Telegram. Нужен другой номер.",
        "PhoneNumberFloodError": "На этот номер слишком часто просили код. Подожди несколько часов.",
        "PhoneNumberUnoccupiedError": "На этот номер не зарегистрирован Telegram. Нужен номер с аккаунтом Telegram.",
        "AuthKeyDuplicatedError": "Этот вход (session.session) запущен ещё где-то. Удали session.session и войди заново.",
        "PasswordHashInvalidError": "Неверный пароль двухэтапной проверки.",
        "UpdateAppToLoginError": "Библиотека telethon устарела, Telegram её не пускает. Обнови: pip install -U telethon "
                                 "(в Pydroid: Pip → telethon → Upgrade) и запусти бота заново.",
        "SendCodeUnavailableError": "Telegram больше не даёт другого способа прислать код. Введи код, который уже "
                                    "пришёл, или подожди пару часов и попробуй снова.",
        "PhoneCodeHashEmptyError": "Сбился запрос кода. Запусти бота заново и запроси код один раз.",
        "AuthRestartError": "Telegram попросил начать вход заново. Запусти бота ещё раз.",
    }
    return hints.get(type(e).__name__)


def _wait_text(e):
    sec = getattr(e, "seconds", None)
    if not sec:
        return None
    h, m = divmod(int(sec) // 60, 60)
    return f"Telegram просит подождать {h} ч {m} мин, прежде чем слать новый код. Раньше — не придёт."


def _sent_text(sent, phone=None):
    """Куда Telegram отправил код — понятными словами."""
    phone = phone or PHONE
    t = getattr(sent, "type", None)
    kind = type(t).__name__
    where = {"SentCodeTypeApp": "в ПРИЛОЖЕНИЕ Telegram, где уже открыт этот аккаунт: чат «Telegram» "
                                "с синей галочкой (там же, где уведомления о входе)",
             "SentCodeTypeSms": f"по SMS на номер {phone}", "SentCodeTypeSmsWord": f"по SMS на номер {phone} (слово)",
             "SentCodeTypeSmsPhrase": f"по SMS на номер {phone} (фраза)",
             "SentCodeTypeFirebaseSms": f"по SMS на номер {phone}",
             "SentCodeTypeFragmentSms": "в приложение Fragment (номер куплен на fragment.com)",
             "SentCodeTypeCall": "звонком (робот продиктует код)",
             "SentCodeTypeFlashCall": "звонком-сбросом", "SentCodeTypeMissedCall": "пропущенным звонком: "
                                                                             "код — последние цифры номера, с которого звонили"}
    if kind == "SentCodeTypeEmailCode":
        text = f"на ПОЧТУ {getattr(t, 'email_pattern', '')} (почта для входа, привязанная к Telegram)"
    else:
        text = where.get(kind, kind)
    nxt = {"CodeTypeSms": "по SMS", "CodeTypeCall": "звонком", "CodeTypeFlashCall": "звонком-сбросом",
           "CodeTypeMissedCall": "пропущенным звонком", "CodeTypeFragmentSms": "через Fragment"}.get(
        type(getattr(sent, "next_type", None)).__name__)
    if nxt:
        wait = getattr(sent, "timeout", None)
        text += f".\nЕсли не придёт — введи r{f' через {wait} сек' if wait else ''}, пришлю {nxt}"
    return text


async def _send_code(client, phone=None):
    """Запрос (или повтор) кода. Ситуации, когда ввести код здесь нельзя, превращает в LoginBlocked."""
    try:
        sent = await client.send_code_request(phone or PHONE)
    except AttributeError as e:          # telethon не знает ответ «SMS платные» и падает на нём
        if "PaymentRequired" not in str(e):
            raise
        raise LoginBlocked(
            "Telegram не шлёт код: этот аккаунт сейчас не открыт ни в одном приложении Telegram, а SMS на этот "
            "номер Telegram отправляет только за деньги. Открой этот аккаунт в официальном приложении Telegram "
            "(на любом телефоне или компьютере) и запусти бота снова — код придёт туда бесплатно. "
            "Или выбери вход по QR со второго устройства.") from None
    if type(getattr(sent, "type", None)).__name__ == "SentCodeTypeSetUpEmailRequired":
        raise LoginBlocked(
            "Telegram требует сначала привязать почту для входа. Открой официальное приложение Telegram → "
            "Настройки → Конфиденциальность → Почта для входа, привяжи почту и запусти бота снова. "
            "Или выбери вход по QR со второго устройства.")
    return sent


async def _login_by_code(client, E):
    sent = await _send_code(client)
    print(f"Код отправлен {_sent_text(sent)}.")
    print("Код вводи только сюда. Никому его не пересылай и не вставляй в чаты — Telegram тогда сразу его отменяет.")
    for _ in range(6):
        code = (await asyncio.to_thread(
            input, "Введи код (или r — прислать другим способом): ")).strip().replace(" ", "")
        if code.lower() == "r":
            try:
                sent = await _send_code(client)
                print(f"Отправил повторно {_sent_text(sent)}.")
            except LoginBlocked:
                raise
            except Exception as e:
                _log_line(traceback.format_exc())
                print(f"Повторно не отправилось: {type(e).__name__}: {e}")
                print(_wait_text(e) or _explain(e) or "Введи код, который уже пришёл.")
            continue
        if not code:
            continue
        try:
            await client.sign_in(PHONE, code)
            return True
        except E.SessionPasswordNeededError:
            pw = await asyncio.to_thread(input, "Пароль двухэтапной проверки Telegram: ")
            await client.sign_in(password=pw)
            return True
        except E.PhoneCodeInvalidError:
            print("Неверный код. Попробуй ещё раз.")
        except E.PhoneCodeExpiredError:
            print("Код устарел — запрашиваю новый.")
            sent = await _send_code(client)
            print(f"Новый код отправлен {_sent_text(sent)}.")
    return False


async def _login_by_link(client, E):
    for _ in range(8):
        qr = await client.qr_login()
        print("\nЭту ссылку нельзя просто открыть на этом же телефоне — Telegram её так не принимает.")
        print("Она работает только как QR-код: сделай из неё QR (любой сайт «QR-генератор») и отсканируй")
        print("ДРУГИМ устройством, где открыт этот аккаунт: Telegram → Настройки → Устройства → Подключить устройство.")
        print("Ссылка живёт 60 секунд:\n")
        print(qr.url, "\n")
        try:
            await qr.wait(60)
            break
        except asyncio.TimeoutError:
            print("Ссылка устарела, делаю новую...")
        except E.SessionPasswordNeededError:
            pw = await asyncio.to_thread(input, "Пароль двухэтапной проверки Telegram: ")
            await client.sign_in(password=pw)
            break
    return await client.is_user_authorized()


def telethon_version_note():
    """Версия telethon. Старые версии Telegram не пускает или они не понимают новые ответы при входе."""
    import telethon
    v = str(getattr(telethon, "__version__", "") or "")
    try:
        old = tuple(int(x) for x in v.split(".")[:2]) < (1, 40)
    except ValueError:
        return True, f"telethon {v or '?'}"
    if old:
        return False, f"telethon {v} устарел — обнови: pip install -U telethon (в Pydroid: Pip → telethon → Upgrade)"
    return True, f"telethon {v}"


async def telegram_login(client):
    """Нет рабочего входа в Telegram. Возвращает клиента: Telethon (после входа) или WebClient (без входа)."""
    from telethon import errors as E
    print("=" * 50)
    print(f"Нужен вход в Telegram. Номер: {PHONE}")
    ok_ver, ver = telethon_version_note()
    if not ok_ver:
        print("⚠️ " + ver)
    print("  1 — войти по коду (придёт в приложение Telegram или по SMS)")
    print("  2 — войти по ссылке (нужно второе устройство с этим аккаунтом)")
    print("  3 — БЕЗ ВХОДА: читать публичные каналы через t.me")
    print("=" * 50)
    choice = (await asyncio.to_thread(input, "Выбери 1, 2 или 3 [1]: ")).strip() or "1"
    while choice in ("1", "2"):
        try:
            if await (_login_by_code if choice == "1" else _login_by_link)(client, E):
                print("Вход выполнен ✅")
                return client
            print("Войти не удалось.")
        except Exception as e:
            _log_line(traceback.format_exc())
            print(f"Ошибка Telegram: {type(e).__name__}: {e}")
            print(_wait_text(e) or _explain(e) or "Войти не получилось. Пришли эту строку, подскажу.")
        choice = (await asyncio.to_thread(
            input, "1 — снова по коду, 2 — по ссылке, 3 — без входа [3]: ")).strip() or "3"
    with open(WEB_FLAG, "w") as f:
        f.write("web")
    print("Работаю БЕЗ входа: читаю публичные каналы через t.me.")
    print(f"(Чтобы вернуться ко входу по коду — удали файл {WEB_FLAG} и запусти заново.)")
    return WebClient()


# ---------- QR-КОД БЕЗ СТОРОННИХ БИБЛИОТЕК (для входа в Telegram по QR) ----------
def qr_matrix(text):
    """Матрица QR-кода (байтовый режим, коррекция L, версии 1–20) — список строк из True/False."""
    data = text.encode("utf-8")
    ecc_len = [0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28]
    blocks_n = [0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8]

    def raw_modules(v):
        r = (16 * v + 128) * v + 64
        if v >= 2:
            na = v // 7 + 2
            r -= (25 * na - 10) * na - 55
            if v >= 7:
                r -= 36
        return r

    def data_cw(v):
        return raw_modules(v) // 8 - ecc_len[v] * blocks_n[v]

    for ver in range(1, 21):
        if 4 + (8 if ver < 10 else 16) + 8 * len(data) <= data_cw(ver) * 8:
            break
    else:
        raise ValueError("слишком длинный текст для QR")
    bits = []

    def put(val, n):
        bits.extend((val >> i) & 1 for i in reversed(range(n)))
    put(4, 4)
    put(len(data), 8 if ver < 10 else 16)
    for b in data:
        put(b, 8)
    cap = data_cw(ver) * 8
    put(0, min(4, cap - len(bits)))
    put(0, (-len(bits)) % 8)
    pad = 0xEC
    while len(bits) < cap:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    dcw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    def gmul(x, y):
        z = 0
        for i in reversed(range(8)):
            z = (z << 1) ^ ((z >> 7) * 0x11D)
            z ^= ((y >> i) & 1) * x
        return z

    deg = ecc_len[ver]
    div = [0] * (deg - 1) + [1]
    root = 1
    for _ in range(deg):
        for j in range(deg):
            div[j] = gmul(div[j], root)
            if j + 1 < deg:
                div[j] ^= div[j + 1]
        root = gmul(root, 2)

    def rs_rem(msg):
        res = [0] * deg
        for b in msg:
            f = b ^ res.pop(0)
            res.append(0)
            for i, c in enumerate(div):
                res[i] ^= gmul(c, f)
        return res

    nb, raw = blocks_n[ver], raw_modules(ver) // 8
    n_short, short_len = nb - raw % nb, raw // nb
    blocks, k = [], 0
    for i in range(nb):
        dl = short_len - deg + (0 if i < n_short else 1)
        dat = dcw[k:k + dl]
        k += dl
        ecc = rs_rem(dat)
        blocks.append(dat + ([0] if i < n_short else []) + ecc)
    words = [blk[i] for i in range(len(blocks[0])) for j, blk in enumerate(blocks)
             if i != short_len - deg or j >= n_short]

    size = ver * 4 + 17
    M = [[False] * size for _ in range(size)]
    F = [[False] * size for _ in range(size)]

    def setf(x, y, v):
        M[y][x], F[y][x] = v, True
    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if 0 <= cx + dx < size and 0 <= cy + dy < size:
                    setf(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
    if ver > 1:
        na = ver // 7 + 2
        step = (ver * 8 + na * 3 + 5) // (na * 4 - 4) * 2
        pos, p = [6], size - 7
        while len(pos) < na:
            pos.insert(1, p)
            p -= step
        for i, a in enumerate(pos):
            for j, b in enumerate(pos):
                if (i, j) in ((0, 0), (0, na - 1), (na - 1, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        setf(a + dx, b + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask):
        d = (1 << 3) | mask                            # уровень коррекции L = 01
        r = d
        for _ in range(10):
            r = (r << 1) ^ ((r >> 9) * 0x537)
        fb = ((d << 10) | r) ^ 0x5412
        bit = lambda i: (fb >> i) & 1 == 1             # noqa: E731
        for i in range(6):
            setf(8, i, bit(i))
        setf(8, 7, bit(6))
        setf(8, 8, bit(7))
        setf(7, 8, bit(8))
        for i in range(9, 15):
            setf(14 - i, 8, bit(i))
        for i in range(8):
            setf(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            setf(8, size - 15 + i, bit(i))
        setf(8, size - 8, True)
    draw_format(0)
    if ver >= 7:
        r = ver
        for _ in range(12):
            r = (r << 1) ^ ((r >> 11) * 0x1F25)
        vb = (ver << 12) | r
        for i in range(18):
            a, b = size - 11 + i % 3, i // 3
            setf(a, b, (vb >> i) & 1 == 1)
            setf(b, a, (vb >> i) & 1 == 1)
    i, right = 0, size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for jj in range(2):
                x = right - jj
                y = size - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not F[y][x] and i < len(words) * 8:
                    M[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
        right -= 2
    masks = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
             lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
             lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
             lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0]

    def apply(m):
        for y in range(size):
            for x in range(size):
                if not F[y][x] and masks[m](x, y):
                    M[y][x] = not M[y][x]

    def penalty():
        s = 0
        for line in M + [list(c) for c in zip(*M)]:
            run, prev = 0, None
            for v in line:
                run = run + 1 if v == prev else 1
                prev = v
                s += 3 if run == 5 else (1 if run > 5 else 0)
        for y in range(size - 1):
            for x in range(size - 1):
                if M[y][x] == M[y][x + 1] == M[y + 1][x] == M[y + 1][x + 1]:
                    s += 3
        dark = sum(map(sum, M))
        return s + abs(dark * 20 - size * size * 10) // (size * size) * 10

    best = None
    for m in range(8):
        apply(m)
        draw_format(m)
        p = penalty()
        if best is None or p < best[0]:
            best = (p, m)
        apply(m)                                       # снять маску (XOR обратим)
    apply(best[1])
    draw_format(best[1])
    return M


def qr_png(text, path, scale=8, border=4):
    """QR-код картинкой PNG (чёрно-белая, без сторонних библиотек)."""
    import struct
    import zlib
    m = qr_matrix(text)
    n = len(m)
    px = (n + 2 * border) * scale
    rows = []
    for y in range(px):
        my = y // scale - border
        line = bytearray(b"\0")
        for x in range(px):
            mx = x // scale - border
            line.append(0 if 0 <= mx < n and 0 <= my < n and m[my][mx] else 255)
        rows.append(bytes(line))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", px, px, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))
    return path


# ---------- ПОДКЛЮЧЕНИЕ TELEGRAM ИЗ МЕНЮ MAX ----------
class ClientBox:
    """Текущий клиент Telegram (t.me или аккаунт). Подключили аккаунт из MAX — подменяем на лету, без перезапуска."""

    def __init__(self, cur):
        self.cur = cur

    def __getattr__(self, name):
        return getattr(self.cur, name)


ON_NEW = None          # обработчик новых постов (из main) — вешаем и на подключённый позже аккаунт
TG_LOGIN = {}          # вход, который сейчас идёт из MAX: ключи, номер, временный клиент
TG_SESSION_TMP = "login_tmp"


def is_web_client(client):
    return isinstance(getattr(client, "cur", client), WebClient)


def mask_phone(p):
    p = str(p or "")
    return p[:2] + "•" * max(0, len(p) - 6) + p[-4:] if len(p) > 6 else p


def tg_forget(mid):
    """Убрать из чата сообщение с секретом. False — не вышло (пусть удалит сам)."""
    if not mid:
        return True
    try:
        return delete_msg(mid) is not False
    except Exception:
        return False


DELETE_YOURSELF = "⚠️ Не смог удалить твоё сообщение — удали его из чата сам."


def save_tg_keys(api_id, api_hash, phone):
    """Ключи Telegram -> config.json (остальное в файле не трогаем) и в работающего бота."""
    global API_ID, API_HASH, PHONE, TG_READY
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg.update(api_id=int(api_id), api_hash=api_hash, phone=phone or cfg.get("phone") or "")
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    CONFIG.update(cfg)
    API_ID, API_HASH, PHONE, TG_READY = int(api_id), api_hash, phone, True


async def _tg_drop():
    c = TG_LOGIN.pop("client", None)
    if c is not None:
        with contextlib.suppress(Exception):
            await c.disconnect()


async def tg_send_code(resend=False):
    """Шаг «прислать код». Возвращает текст: куда ушёл код, или 'ok', если вход уже есть."""
    if not resend:
        await _tg_drop()
        for f in (TG_SESSION_TMP + ".session", TG_SESSION_TMP + ".session-journal"):
            with contextlib.suppress(OSError):
                os.remove(f)
        c = TelegramClient(TG_SESSION_TMP, int(TG_LOGIN["api_id"]), TG_LOGIN["api_hash"])
        TG_LOGIN["client"] = c
        await c.connect()
        if await c.is_user_authorized():
            await tg_finish()
            return "ok"
    sent = await _send_code(TG_LOGIN["client"], TG_LOGIN["phone"])
    return _sent_text(sent, TG_LOGIN["phone"])


async def tg_sign_in(code=None, password=None):
    """'ok' — вошли, 'password' — нужен облачный пароль, 'bad' — неверный код, 'expired' — код устарел (новый отправлен)."""
    from telethon import errors as E
    c = TG_LOGIN["client"]
    try:
        if password is not None:
            await c.sign_in(password=password)
        else:
            await c.sign_in(TG_LOGIN["phone"], code)
    except E.SessionPasswordNeededError:
        return "password"
    except E.PhoneCodeInvalidError:
        return "bad"
    except E.PhoneCodeExpiredError:
        TG_LOGIN["sent"] = await tg_send_code(resend=True)
        return "expired"
    await tg_finish()
    return "ok"


async def tg_finish():
    """Вход удался: ключи — в config.json, сессия — в session.session, бот читает каналы через аккаунт."""
    c = TG_LOGIN.pop("client")
    if not TG_LOGIN.get("phone"):
        with contextlib.suppress(Exception):
            me = await c.get_me()
            TG_LOGIN["phone"] = "+" + str(me.phone) if getattr(me, "phone", None) else ""
    await c.disconnect()
    box = TG_CLIENT
    old = getattr(box, "cur", None)
    if old is not None and not is_web_client(old):
        with contextlib.suppress(Exception):
            await old.disconnect()
    if os.path.exists(TG_SESSION_TMP + ".session"):
        os.replace(TG_SESSION_TMP + ".session", "session.session")
    save_tg_keys(TG_LOGIN["api_id"], TG_LOGIN["api_hash"], TG_LOGIN.get("phone") or "")
    new = TelegramClient("session", API_ID, API_HASH)
    await new.connect()
    if ON_NEW is not None:
        with contextlib.suppress(Exception):
            from telethon import events
            new.add_event_handler(ON_NEW, events.NewMessage())
    if isinstance(box, ClientBox):
        box.cur = new
    with contextlib.suppress(OSError):
        os.remove(WEB_FLAG)
    TG_LOGIN.clear()
    print("Telegram: аккаунт подключён из меню MAX ✅")


QR_TEXT = ("📷 Вход в Telegram по QR-коду\n\n"
           "1) Открой эту картинку на ДРУГОМ экране — например, в MAX на компьютере.\n"
           "2) На телефоне: Telegram → Настройки → Устройства → «Подключить устройство» и наведи камеру на QR.\n\n"
           "Код живёт полминуты — если не успеешь, я пришлю новый сам.")


async def tg_qr_loop(uid, chat):
    """Вход по QR: без кодов и SMS. Шлём картинку в MAX, ждём скан, при истечении — новая картинка."""
    from telethon import errors as E
    c = None

    def say(text, rows=None, token=None):
        return asyncio.to_thread(api_send, chat, text, rows, token)
    try:
        await _tg_drop()
        for f in (TG_SESSION_TMP + ".session", TG_SESSION_TMP + ".session-journal"):
            with contextlib.suppress(OSError):
                os.remove(f)
        c = TelegramClient(TG_SESSION_TMP, int(TG_LOGIN["api_id"]), TG_LOGIN["api_hash"])
        TG_LOGIN["client"] = c
        await c.connect()
        if not await c.is_user_authorized():
            qr = await c.qr_login()
            for _ in range(8):
                if TG_LOGIN.get("client") is not c:      # нажали «Отмена»
                    return
                path = await asyncio.to_thread(qr_png, qr.url, "tg_login_qr.png")
                token = await asyncio.to_thread(upload_image, path)
                await say(QR_TEXT, [TG_CANCEL], token)
                left = (qr.expires - _dt.now(_tz.utc)).total_seconds()
                try:
                    await qr.wait(left if 10 <= left <= 120 else 30)
                    break
                except asyncio.TimeoutError:
                    await qr.recreate()
            else:
                await _tg_drop()
                await say("QR-код так и не отсканировали. Нажми «📷 Войти по QR-коду» ещё раз, когда будешь готов.",
                          [[B("📷 Войти по QR-коду", "tg:qr")], [B("◀️ Меню", "m:main")]])
                return
        await tg_finish()
        await say("✅ Telegram подключён! Теперь читаю каналы через аккаунт: все посты и большие видео.",
                  [[B("◀️ Меню", "m:main")]])
    except E.SessionPasswordNeededError:
        text, rows = tg_ask(uid, "tg_password", "QR-код принят.")
        await say(text, rows)
    except Exception as e:
        if c is not None and TG_LOGIN.get("client") is not c:   # отменили — молчим
            return
        await _tg_drop()
        await say(tg_error_text(e), [[B("📷 Попробовать снова", "tg:qr")], [B("◀️ Меню", "m:main")]])
    finally:
        with contextlib.suppress(OSError):
            os.remove("tg_login_qr.png")


def tg_qr_begin(uid):
    PENDING.pop(uid, None)
    TG_LOGIN.pop("mode", None)
    asyncio.run_coroutine_threadsafe(tg_qr_loop(uid, CFG["admin_chats"].get(str(uid))), MAIN_LOOP)
    return "📷 Готовлю QR-код для входа…", [TG_CANCEL]


def tg_run(coro, timeout=90):
    """Выполнить шаг входа в главном цикле бота (меню MAX работает в своём потоке)."""
    return asyncio.run_coroutine_threadsafe(coro, MAIN_LOOP).result(timeout)


def tg_error_text(e):
    _log_line(traceback.format_exc())
    return (f"❌ Не получилось: {type(e).__name__}\n\n"
            + (_wait_text(e) or _explain(e) or f"{e}\nПришли этот текст тому, кто настраивает бота."))


TG_CANCEL = [B("❌ Отмена", "tg:cancel")]
TG_STEPS = {
    "tg_api_id": "Шаг 1 из 4. Пришли api_id — число из 7–8 цифр.\n\n"
                 "Где взять: открой в браузере my.telegram.org → войди своим номером → "
                 "«API development tools» → строка «App api_id».",
    "tg_api_hash": "Шаг 2 из 4. Пришли api_hash — строка из 32 символов (там же, «App api_hash»).",
    "tg_phone": "Шаг 3 из 4. Пришли номер телефона аккаунта Telegram, например +79991234567.",
    "tg_code": "Шаг 4 из 4. Пришли сюда код из Telegram.",
    "tg_password": "На аккаунте стоит облачный пароль (двухэтапная проверка). Пришли его — "
                   "сообщение с паролем я сразу удалю.",
}


def tg_screen(uid):
    """Экран «🔑 Telegram» в меню."""
    if TG_CLIENT is not None and not is_web_client(TG_CLIENT):
        text = (f"🔑 Аккаунт Telegram: подключён ✅\nНомер: {mask_phone(PHONE)}\n\n"
                "Каналы читаю через аккаунт: видны все посты и большие видео.")
        rows = [[B("🔄 Подключить другой аккаунт", "tg:start")], [B("📷 Другой аккаунт по QR-коду", "tg:qr")]]
    else:
        text = ("🔑 Аккаунт Telegram: не подключён\n\n"
                "Сейчас читаю публичные каналы через t.me — это работает, но большие видео недоступны.\n\n"
                "Для подключения понадобятся:\n"
                "1) api_id и api_hash — с сайта my.telegram.org (раздел API development tools)\n"
                "2) номер телефона аккаунта\n"
                "3) код, который придёт в приложение Telegram\n\n"
                "Если код не приходит — «📷 Войти по QR-коду»: сканируешь QR телефоном, коды не нужны.")
        rows = [[B("🔑 Подключить аккаунт", "tg:start")], [B("📷 Войти по QR-коду (без кода)", "tg:qr")]]
    if not is_owner(uid):
        text += "\n\nПодключать аккаунт может только владелец бота."
        rows = []
    return text, rows + [[B("◀️ Меню", "m:main")]]


def tg_ask(uid, step, prefix=""):
    PENDING[uid] = step
    rows = []
    if step in ("tg_api_id", "tg_api_hash") and field_ok("api_id", CONFIG.get("api_id")) \
            and field_ok("api_hash", CONFIG.get("api_hash")):
        rows.append([B("⏭ Оставить сохранённые ключи", "tg:keep")])
    if step == "tg_phone" and field_ok("phone", CONFIG.get("phone")):
        rows.append([B(f"⏭ Оставить номер {mask_phone(CONFIG['phone'])}", "tg:keepphone")])
    if step == "tg_code":
        rows.append([B("🔁 Прислать другим способом", "tg:resend")])
        rows.append([B("📷 Войти по QR-коду (без кода)", "tg:qr")])
    return (prefix + "\n\n" if prefix else "") + TG_STEPS[step], rows + [TG_CANCEL]


def tg_after_phone(uid):
    """Номер есть — просим Telegram прислать код."""
    try:
        res = tg_run(tg_send_code())
    except Exception as e:
        PENDING.pop(uid, None)
        return tg_error_text(e), [[B("🔁 Начать заново", "tg:start")], [B("◀️ Меню", "m:main")]]
    if res == "ok":
        PENDING.pop(uid, None)
        return "✅ Telegram подключён (вход уже был). Читаю каналы через аккаунт.", [[B("◀️ Меню", "m:main")]]
    return tg_ask(uid, "tg_code", f"📨 Код отправлен {res}.")


def tg_callback(rest, uid):
    """Кнопки tg:* — возвращает (текст, кнопки)."""
    if not is_owner(uid):
        return tg_screen(uid)
    if rest == "start":
        if MAIN_LOOP is None:
            return "Бот ещё запускается — попробуй через минуту.", [[B("◀️ Меню", "m:main")]]
        TG_LOGIN.clear()
        return tg_ask(uid, "tg_api_id")
    if rest == "qr":
        if MAIN_LOOP is None:
            return "Бот ещё запускается — попробуй через минуту.", [[B("◀️ Меню", "m:main")]]
        if not TG_LOGIN.get("api_hash") and field_ok("api_id", CONFIG.get("api_id")) \
                and field_ok("api_hash", CONFIG.get("api_hash")):
            TG_LOGIN.update(api_id=CONFIG["api_id"], api_hash=CONFIG["api_hash"])
        if not TG_LOGIN.get("api_hash"):
            TG_LOGIN.clear()
            TG_LOGIN["mode"] = "qr"
            return tg_ask(uid, "tg_api_id", "Для входа по QR нужны только api_id и api_hash (номер и код не нужны).")
        return tg_qr_begin(uid)
    if rest == "keep":
        TG_LOGIN.update(api_id=CONFIG["api_id"], api_hash=CONFIG["api_hash"])
        if TG_LOGIN.get("mode") == "qr":
            return tg_qr_begin(uid)
        return tg_ask(uid, "tg_phone")
    if rest == "keepphone" and TG_LOGIN.get("api_hash"):
        TG_LOGIN["phone"] = CONFIG["phone"]
        return tg_after_phone(uid)
    if rest == "resend" and TG_LOGIN.get("client") is not None:
        try:
            return tg_ask(uid, "tg_code", f"📨 Отправил повторно {tg_run(tg_send_code(resend=True))}.")
        except Exception as e:
            return tg_ask(uid, "tg_code", tg_error_text(e))
    PENDING.pop(uid, None)                           # cancel и всё непонятное
    if MAIN_LOOP is not None:
        with contextlib.suppress(Exception):
            tg_run(_tg_drop(), 30)
    TG_LOGIN.clear()
    return tg_screen(uid)


def tg_text(uid, step, t, mid=None):
    """Ответ на шаг ввода (tg_*) — возвращает (текст, кнопки)."""
    if step == "tg_api_id":
        if not field_ok("api_id", t):
            return tg_ask(uid, step, "Не похоже на api_id: нужно число из 7–8 цифр.")
        TG_LOGIN["api_id"] = int(t)
        return tg_ask(uid, "tg_api_hash")
    if step == "tg_api_hash":
        note = "" if tg_forget(mid) else DELETE_YOURSELF
        if not field_ok("api_hash", t):
            return tg_ask(uid, step, "Не похоже на api_hash: нужно 32 символа (цифры и буквы a–f).")
        TG_LOGIN["api_hash"] = t
        if TG_LOGIN.get("mode") == "qr":
            text, rows = tg_qr_begin(uid)
            return (note + "\n\n" if note else "") + text, rows
        return tg_ask(uid, "tg_phone", note)
    if step == "tg_phone":
        phone = norm_phone(t)
        if not field_ok("phone", phone):
            return tg_ask(uid, step, "Не похоже на номер. Пример: +79991234567")
        TG_LOGIN["phone"] = phone
        return tg_after_phone(uid)
    forgot = tg_forget(mid)                          # код и пароль в чате не оставляем
    if step == "tg_password" and not forgot:
        api_send(CFG["admin_chats"].get(str(uid)), DELETE_YOURSELF)
    if TG_LOGIN.get("client") is None:
        PENDING.pop(uid, None)
        return tg_screen(uid)
    try:
        if step == "tg_code":
            res = tg_run(tg_sign_in(code=re.sub(r"\D", "", t)))
        else:
            res = tg_run(tg_sign_in(password=t))
    except Exception as e:
        if step == "tg_password" and "PasswordHashInvalid" in type(e).__name__:
            return tg_ask(uid, step, "Неверный пароль. Попробуй ещё раз.")
        PENDING.pop(uid, None)
        return tg_error_text(e), [[B("🔁 Начать заново", "tg:start")], [B("◀️ Меню", "m:main")]]
    if res == "password":
        return tg_ask(uid, "tg_password")
    if res == "bad":
        return tg_ask(uid, "tg_code", "Неверный код. Проверь и пришли ещё раз.")
    if res == "expired":
        return tg_ask(uid, "tg_code", f"Код устарел. Новый отправлен {TG_LOGIN.get('sent')}.")
    PENDING.pop(uid, None)
    return ("✅ Telegram подключён! Теперь читаю каналы через аккаунт: все посты и большие видео.",
            [[B("◀️ Меню", "m:main")]])


# ---------- ДИАГНОСТИКА ----------
TG_CLIENT = None
MAIN_LOOP = None


def diag_max():
    """Связь с MAX: токен, каналы тем, админы. Список (успех, текст)."""
    out = []
    try:
        r = S.get(f"{MAX_API}/me", headers=HEADERS, timeout=20)
        if r.status_code == 200:
            me = r.json()
            out.append((True, f"MAX: токен работает, бот «{me.get('name') or me.get('username') or '?'}»"))
        elif r.status_code in (401, 403):
            out.append((False, f"MAX: токен не принят ({r.status_code}) — проверь max_token в config.json"))
        else:
            out.append((False, f"MAX: /me ответил {r.status_code}"))
    except Exception as e:
        out.append((False, f"MAX: нет связи ({type(e).__name__}) — проверь интернет"))
    for t in THEMES:
        if not t.chat_id:
            out.append((False, f"Тема «{t.name}»: канал MAX не привязан (добавь бота админом канала)"))
            continue
        try:
            r = S.get(f"{MAX_API}/chats/{t.chat_id}", headers=HEADERS, timeout=20)
            ok = r.status_code == 200
            out.append((ok, f"Тема «{t.name}»: канал MAX " + ("доступен" if ok else
                        f"недоступен ({r.status_code}) — бот должен быть админом канала")))
        except Exception as e:
            out.append((False, f"Тема «{t.name}»: канал MAX не проверить ({type(e).__name__})"))
    n = len(CFG["admins"])
    out.append((n > 0, f"Админов бота: {n}" + ("" if n else " — отправь боту в MAX код привязки из консоли")))
    return out


async def diag_telegram(client):
    out = []
    if is_web_client(client):
        out.append((True, "Telegram: режим без входа (читаю через t.me)"))
    else:
        try:
            ok = await client.is_user_authorized()
            out.append((bool(ok), "Telegram: вход " + ("выполнен" if ok else "НЕ выполнен")))
        except Exception as e:
            out.append((False, f"Telegram: нет связи ({type(e).__name__})"))
    per_theme = []
    for t in list(THEMES):
        with theme_ctx(t):
            per_theme.append((t, list(CFG["channels"])))
    flat = [ch for _, chans in per_theme for ch in chans]
    got_all = await asyncio.gather(*(asyncio.wait_for(client.get_messages(ch, limit=1), 15) for ch in flat),
                                   return_exceptions=True)
    pos = 0
    for t, chans in per_theme:
        got = got_all[pos:pos + len(chans)]
        pos += len(chans)
        with theme_ctx(t):
            for ch, latest in zip(chans, got):
                try:
                    if isinstance(latest, BaseException):
                        raise latest
                    if latest:
                        age = max(0, int(time.time() - latest[0].date.timestamp()))
                        out.append((True, f"@{ch}: читается, последний пост #{latest[0].id} ({fmt_min(age // 60)} назад)"))
                    else:
                        out.append((True, f"@{ch}: читается, постов пока нет"))
                except Exception as e:
                    if is_net_error(e):
                        out.append((False, f"@{ch}: t.me не отвечает с этого сервера ({type(e).__name__}). "
                                           f"Нужен сервер за границей или прокси TG_PROXY"))
                    else:
                        out.append((False, f"@{ch}: не читается ({type(e).__name__}: {str(e)[:60]})"))
            pend = sum(1 for x in QUEUE if x.get("status") == "pending")
            out.append((True, f"Тема «{t.name}»: в очереди {len(QUEUE)}, ждут одобрения {pend}, в тайнике {len(STASH)}"))
    return out


def run_diag():
    """Полная проверка для кнопки/команды /check (вызывается из потока меню)."""
    out = diag_max()
    if MAIN_LOOP is not None and TG_CLIENT is not None:
        try:
            out += asyncio.run_coroutine_threadsafe(diag_telegram(TG_CLIENT), MAIN_LOOP).result(40)
        except Exception as e:
            out.append((False, f"Telegram: проверка не удалась ({type(e).__name__})"))
    bad = sum(1 for ok, _ in out if not ok)
    head = "🔎 Проверка: всё в порядке ✅" if not bad else f"🔎 Проверка: проблем — {bad}"
    return head + "\n\n" + "\n".join(("✅ " if ok else "❌ ") + t for ok, t in out)


async def check_cli():
    """python tg_to_max.py --check: проверка без запуска бота."""
    use_web = not TG_READY or os.path.exists(WEB_FLAG) or not os.path.exists("session.session")
    client = WebClient() if use_web else TelegramClient("session", API_ID, API_HASH)
    try:
        await client.connect()
    except Exception as e:
        print("Telegram: не удалось подключиться:", e)
    out = [telethon_version_note()] + await asyncio.to_thread(diag_max) + await diag_telegram(client)
    print("\n".join(("✅ " if ok else "❌ ") + t for ok, t in out))


async def main():
    global WAKE, TG_CLIENT, MAIN_LOOP, ON_NEW
    from telethon import events
    ok = False
    client = WebClient()
    if TG_READY:
        client = TelegramClient("session", API_ID, API_HASH)
        try:
            await client.connect()
            ok = await client.is_user_authorized()
        except Exception as e:
            print("Telegram: не удалось подключиться:", e)
    if not ok:
        if LOGIN:
            if os.path.exists(WEB_FLAG):
                os.remove(WEB_FLAG)
            client = await telegram_login(client)
            if not isinstance(client, WebClient) and os.path.exists(WEB_FLAG):
                os.remove(WEB_FLAG)
        else:
            print("Режим без входа в Telegram: читаю публичные каналы через t.me.")
            print("Подключить аккаунт Telegram: в MAX меню бота → «🔑 Telegram» (или запусти login.py).")
            client = WebClient()
    print("Telegram: читаю каналы без входа (t.me)" if isinstance(client, WebClient) else "Telegram: вход выполнен")
    client = ClientBox(client)
    TG_CLIENT, MAIN_LOOP = client, asyncio.get_running_loop()
    MAIN_LOOP.set_exception_handler(lambda loop, ctx: _log_line(
        "ОШИБКА в фоновой задаче: " + str(ctx.get("message")) + " " + repr(ctx.get("exception"))))
    await asyncio.to_thread(drop_webhooks)               # вебхук на токене глушит бота — снимаем до всего остального
    main_t = THEMES[0]
    env_chat = CONFIG.get("max_chat_id") if os.environ.get("MAX_CHAT_ID") else None
    if env_chat and main_t.chat_id != env_chat:          # переменная MAX_CHAT_ID — явное указание, какой канал
        main_t.chat_id = env_chat
        await asyncio.to_thread(_save_channel_id, env_chat)
        save_cfg()
    if not main_t.chat_id:
        main_t.chat_id = MAX_CHAT_ID or await asyncio.to_thread(find_chat_id_safe)
        if main_t.chat_id:
            await asyncio.to_thread(_save_channel_id, main_t.chat_id)
            save_cfg()
        else:
            BIND["theme"] = main_t.id                    # привяжем по первому сообщению/добавлению в канал
            print("⚠️ Канал MAX по ссылке не нашёлся. Добавь бота АДМИНОМ своего канала MAX и напиши в канале "
                  "любое сообщение — бот привяжет канал сам. Или задай переменную MAX_CHAT_ID (число id канала).")
    for t in THEMES:
        print(f"Тема «{t.name}»: MAX chat_id =", t.chat_id)
        with theme_ctx(t):
            t.history[:] = [] if TEST_REPOST_LAST else prune(t.history)

    WAKE = asyncio.Event()

    @client.on(events.NewMessage())
    async def on_new(event):                 # Telegram сам сообщает о новом посте — читаем сразу
        try:
            if not event.is_channel:
                return
            chat = await event.get_chat()
            name = (getattr(chat, "username", None) or "").lower()
        except Exception:
            return
        if name in WATCH:
            WAKE_NAMES.add(name)
            WAKE.set()
    ON_NEW = on_new

    asyncio.create_task(asyncio.to_thread(menu_loop))   # меню в MAX
    asyncio.create_task(background_loop())              # чистка карточек, отчёт
    tasks, fails, sem = {}, {}, asyncio.Semaphore(5)
    last_full, woke, chan_try = 0.0, set(), time.time()
    while True:
        if not THEMES[0].chat_id and time.time() - chan_try > 300:   # канал так и не привязан — ищем снова
            chan_try = time.time()
            cid = await asyncio.to_thread(find_chat_id_safe)
            if cid and not THEMES[0].chat_id:
                THEMES[0].chat_id = cid
                if BIND.get("theme") == THEMES[0].id:
                    BIND["theme"] = None
                save_cfg()
                print("Канал MAX найден по ссылке: chat_id =", cid)
        for t in list(THEMES):                           # у каждой темы свой планировщик
            if t.id not in tasks or tasks[t.id].done():
                t.lock = t.lock or asyncio.Lock()
                tasks[t.id] = asyncio.create_task(scheduler(client, t))
        if CFG["paused"]:
            await asyncio.sleep(3)
            continue
        full = time.time() - last_full >= CFG["interval"]
        jobs, names = [], set()
        for t in list(THEMES):
            with theme_ctx(t):
                await sync_channels(client)
                if t.flags["test"]:
                    t.flags["test"] = False
                    await run_test(client)
                for ch in list(CFG["channels"]):
                    names.add(ch.lower())
                    if ch in t.last_ids and (full or ch.lower() in woke) and not net_blocked(ch):
                        jobs.append((t, ch))
        WATCH.clear()
        WATCH.update(names)
        if full:
            last_full = time.time()
        woke = set()
        # все каналы всех тем читаются одновременно, а не по очереди
        await asyncio.gather(*(poll(client, t, ch, fails, sem) for t, ch in jobs))
        if RECHECK:                                      # посты ещё «устаканиваются» — перечитаем через пару секунд
            await asyncio.sleep(FRESH_SEC + 1)
            woke |= set(RECHECK)
            RECHECK.clear()
            continue

        waited, left = 0, max(1, CFG["interval"] - (time.time() - last_full))
        while (waited < left and not CFG["paused"] and not WAKE.is_set()
               and not any(t.flags["test"] for t in THEMES)):
            await asyncio.sleep(1)
            waited += 1
        if WAKE.is_set():                                # событие: даём альбому долететь и читаем
            await asyncio.sleep(FRESH_SEC + 1)
            woke |= WAKE_NAMES
            WAKE_NAMES.clear()
            WAKE.clear()


if __name__ == "__main__":
    if "--check" in sys.argv:
        asyncio.run(check_cli())
    else:
        asyncio.run(main())
