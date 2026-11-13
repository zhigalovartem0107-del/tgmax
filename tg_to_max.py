"""
Репост из нескольких Telegram-каналов в канал MAX.
Управление — через меню прямо в MAX (напиши своему боту /start) или командами через / (/help).

Установка:   pip install telethon requests
"""
import asyncio
import contextlib
import contextvars
import copy
import functools
import hashlib
import json
import math
import os
import queue
import random
import secrets
import re
import unicodedata
import socket
import sys
import threading
import time
import traceback
import types
import zlib
from collections import Counter, deque
from difflib import SequenceMatcher

for _k in [k for k in sys.modules if k == "reloader" or k.startswith("reloader.")]:
    del sys.modules[_k]                   # каждая загрузка файла — свой, свежий пакет reloader
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
import urllib3
from requests.adapters import HTTPAdapter
from telethon import TelegramClient
from urllib3.util.retry import Retry


BOT_VERSION = 2026111329            # номер версии: main.py на хостинге запускает файл с самым большим номером

if __name__ == "__main__":
    os.umask(0o077)                     # новые файлы (токены, вход в Telegram, клиенты) — только для самого бота
    # 🔒 Защита от кражи (как в main.py): при прямом запуске бота — тоже требуем правильный BOT_SECRET.
    #   На импорт (тесты) не влияет: сработает только при запуске как главный файл. Проверка — до любой работы.
    try:
        from reloader.services import license as _license
    except Exception:
        print("🔒 КРИТИЧЕСКАЯ ОШИБКА: Unauthorized copy detected. Модуль лицензии недоступен — бот остановлен.",
              flush=True)
        sys.exit(3)
    _license.enforce(log=lambda _m: print(_m, flush=True))

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
# Продажа ботов: этот же файл запускает ботов клиентов (TGMAX_CLIENT=1) — у них свои токен, папка и лимиты.
CLIENT_MODE = os.environ.get("TGMAX_CLIENT") == "1"
HOST_MODE = os.environ.get("TGMAX_HOST") == "1" and not CLIENT_MODE   # один бот для всех: у каждого пользователя свой кабинет
CLIENT_TG = os.environ.get("TGMAX_TG", "1") == "1"   # продавец разрешил клиенту вход в Telegram
NO_TG = CLIENT_MODE and not CLIENT_TG
LOCKED = os.environ.get("TGMAX_LOCKED", "")           # "expired" — подписка кончилась, "off" — выключен продавцом
SELLER_CONTACT = (os.environ.get("SELLER_CONTACT") or "").strip() or "@ВАШ_КОНТАКТ"  # задай свой: env SELLER_CONTACT
PRICE_30, PRICE_90 = 300, 750                         # если тарифов нет (старые настройки)
DEFAULT_TARIFFS = [{"name": "Старт", "themes": 1, "sources": 5, "price": 300},
                   {"name": "Стандарт", "themes": 3, "sources": 10, "price": 600},
                   {"name": "Про", "themes": 10, "sources": 30, "price": 1500}]
# 🆓 навсегда: срок кончился — кабинет работает так: 1 канал MAX, 1 источник, только текст, пост не чаще раза
#    в gap_min минут, без ИИ и умных функций, в меню — только основное; в очереди до queue_max постов;
#    источник проверяется раз в check_min минут; без соцсетей
FREE_PLAN = {"name": "Бесплатный", "themes": 1, "sources": 1, "gap_min": 30, "queue_max": 20, "check_min": 15}
def env_int(name, default):
    """Число из переменной окружения; пусто или опечатка — значение по умолчанию (бот не падает)."""
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        print(f"⚠️ Переменная {name}={raw!r} — не число, беру {default}")
        return default


LIMIT_THEMES = env_int("TGMAX_LIMIT_THEMES", 3)
LIMIT_SOURCES = env_int("TGMAX_LIMIT_SOURCES", 10)
LIMIT_ADMINS = 3
for _stream in (sys.stdout, sys.stderr):              # логи хостинга видны сразу, а не когда наберётся буфер
    with contextlib.suppress(Exception):
        _stream.reconfigure(line_buffering=True)
from reloader.core import log as _log
from reloader.core import files as _files
from reloader.core.files import load_json, load_json_safe, save_json, DATA_FILES, DATA_GLOBS, _find_sessions  # noqa: E402
from reloader.core.timeutil import MONTHS_SHORT  # noqa: E402
from reloader.core import timeutil as _timeutil
from reloader.core.timeutil import n_word, fmt_min, fmt_date, fmt_words, plain_snip, price90, num_sp, week_range  # noqa: E402
from reloader.keyboards import buttons as _buttons
from reloader.keyboards.buttons import B, LB, on_off  # noqa: E402
from reloader.core.log import LOG_FILE, JOURNAL, _log_line, print, journal, log_exc  # noqa: E402,F401 (LOG_FILE — для проверок)
from reloader.services.telegram.pool import AccountPool, PoolBusy, SessionError  # noqa: E402
from reloader.services.codes.store import CodeStore, looks_like_code  # noqa: E402
from reloader.services.buyers.profile import build_profile, render_profile, contact_info, match_buyer, paginate  # noqa: E402
from reloader.services.billing.catalog import over_limit  # noqa: E402
from reloader.services.media.policy import MAX_MEDIA_MB, size_skip_reason  # noqa: E402
from reloader.services.ai.keys import KeyRotator, mask_keys, parse_keys  # noqa: E402
from reloader.services.ai.rewrite import AiUnavailable, DEFAULTS as AI_DEFAULTS, rewrite as ai_rewrite_text  # noqa: E402
from reloader import config as APP_CONFIG  # noqa: E402
ADMIN_IDS = set(APP_CONFIG.ADMIN_IDS)      # 👥 администраторы бота (ADMIN_IDS в переменных хостинга)

# Ключи официального Telegram Desktop: вход без my.telegram.org. Владелец выбрал это сам, на свой риск —
# Telegram может ограничить аккаунт за чужие ключи. Свои ключи — кнопка «🔧 Свои ключи».
DESKTOP_KEYS = ("2040", "b18441a1ff607e10a989891a5462e627")


def tg_client(name, api_id, api_hash):
    if str(api_id) == DESKTOP_KEYS[0]:
        c = TelegramClient(name, int(api_id), api_hash, device_model="PC 64bit", system_version="Windows 10",
                           app_version="5.6.3 x64", lang_code="ru", system_lang_code="ru-RU")
        with contextlib.suppress(Exception):            # как настоящий Telegram Desktop — меньше шансов, что
            c._init_request.lang_pack = "tdesktop"      # Telegram примет вход за подозрительный и отзовёт
        return c
    return TelegramClient(name, api_id, api_hash)


async def _authorized(session_name):
    """True — вход рабочий, False — нет, None — не удалось проверить (например, нет интернета)."""
    try:
        c = tg_client(session_name, API_ID, API_HASH)
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
            if os.path.isdir(os.path.join(src, "queue_media")) and not os.path.exists("queue_media"):
                shutil.copytree(os.path.join(src, "queue_media"), "queue_media")
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
        return v.isdigit() and 4 <= len(v) <= 10
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
    need = [(k, label) for k, label in CONFIG_FIELDS if (LOGIN or k not in TG_FIELDS)
            and not ((CLIENT_MODE or HOST_MODE) and k == "max_channel_link")]
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
TG_READY = not NO_TG and all(field_ok(k, CONFIG.get(k)) for k in ("api_id", "api_hash")) and (
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
    "ads_skip": True,                  # рекламные посты каналов-источников не публикуем
    "ads_ask": True,
    "pin_new": False,                  # 📌 закреплять каждый новый пост в канале
    "autodel_h": 0,                    # 🗑 удалять посты из канала через N часов (0 — не удалять)                   # спорный пост (может, реклама, может, нет) — спросить владельца «да/нет»
    "ads_strict": False,               # 🧠 строже к рекламе (ловит больше, но может задеть обычный пост)
    "smart_time": False,               # 🧠 публиковать в часы, когда больше просмотров
    "smart_tags": False,               # 🧠 сам добавляет хэштеги по теме
    "ai_edit": False,                  # 🪄 ИИ-редактор (бета): пересказывает пост другими словами
    "smart_quality": False,            # 🧠 слабые посты — на твоё одобрение, сильные — сразу
    "dedupe_strict": False,            # 🧠 строже ловит повторы (одна картинка/похожий текст)
    "trusted_sites": [],               # свои надёжные сайты: ссылки туда рекламой не считаются
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
    "max_age_h": 24,                   # пост, ждущий в очереди дольше, удаляется
    "later_min": 60,                   # кнопка «⏰ Через…» на карточке поста: через сколько минут выложить
    "empty_warn_on": True,             # предупреждать админов, если на ближайший час нет постов
    # админы бота в MAX (первый — владелец); заполняется через меню
    "admins": [],
    "admin_names": {},
    "admin_chats": {},
    # ночной режим: в эти часы (по времени телефона/сервера) ничего не публикуется
    "night_on": False,
    "night_from": 0,
    "night_to": 8,
    # посты с этими словами не публикуются (конкурсы, розыгрыши, раздачи)
    "stop_words": ["конкурс", "розыгр", "разыгр", "giveaway", "раздач"],
    # автозамена слов: [["старое", "новое"], ...]
    "replacements": [],
    # ручное одобрение: бот присылает пост админам с кнопками
    "approval_on": True,
    # ежедневный отчёт админам
    "report_on": True,
    "report_hour": 21,
}
# Последние строки поста, где ТОЛЬКО ссылки на эти сайты (футеры), удаляются.
FOOTER_DOMAINS = ["t.me", "telegram.me", "max.ru", "vk.com", "vk.ru", "vk.cc",
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
def tg_proxy_url(config, env):
    """Прокси для t.me: переменная TG_PROXY главнее config.json (как и остальные настройки)."""
    return (env.get("TG_PROXY") or config.get("tg_proxy") or "").strip()


TG_PROXY = tg_proxy_url(CONFIG, os.environ)
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


# ---------- ⏱ СКОРОСТЬ: где бот тратит время (запросы к MAX/ЮKassa/ИИ/t.me, нажатия, запись файлов) ----------
PERF_LOCK = threading.Lock()
PERF = {"since": time.time(), "http": {}, "upd": {}, "ai": {}, "json": {}, "tg": {}, "trend": {},
        "slow": deque(maxlen=15)}
PERF_SLOW_SEC = 1.5          # нажатие обрабатывалось дольше — строка в лог с тем, на что ушло время
PERF_COMMANDS = {"start", "id", "perf", "channels", "kanaly", "panel", "admin", "p", "menu", "m", "card", "predict",
                 "trends", "stats", "status", "queue", "log", "check", "help"}   # свои строки в /perf
_PERF_LOCAL = threading.local()   # запросы, сделанные во время текущего нажатия (для строки «медленно»)


def perf_add(kind, key, sec):
    """Учёт: сколько раз, сколько всего секунд, самый долгий раз."""
    with PERF_LOCK:
        rec = PERF[kind].setdefault(key, [0, 0.0, 0.0])
        rec[0] += 1
        rec[1] += sec
        rec[2] = max(rec[2], sec)
    calls = getattr(_PERF_LOCAL, "calls", None)
    if calls is not None and kind != "upd":
        calls.append((key if kind == "http" else f"{kind}:{key}", sec))


def perf_endpoint(method, url):
    """«GET /chats/{id}» вместо адреса с числами и параметрами — одинаковые запросы складываем вместе.
    У чужих сервисов (ЮKassa, ИИ, t.me) впереди имя сайта."""
    from urllib.parse import urlsplit
    sp = urlsplit(url or "")
    path = re.sub(r"/-?\d+(?=/|$|:)", "/{id}", sp.path or "/")
    path = re.sub(r"/[A-Za-z0-9_.\-]{24,}(?=/|$|:)", "/{x}", path)
    host = sp.hostname or ""
    return f"{method} {'' if host == urlsplit(MAX_API).hostname else host}{path}"


def _perf_hook(r, *a, **k):
    with contextlib.suppress(Exception):
        key = perf_endpoint(r.request.method, r.url)
        if not key.endswith("/updates"):                # долгое ожидание событий MAX — так и задумано
            perf_add("http", key, r.elapsed.total_seconds())
    return r


S.hooks["response"].append(_perf_hook)
WS.hooks["response"].append(_perf_hook)
_save_json_raw = save_json


def save_json(path, data, private=False, backup=False):
    """Запись файла данных (reloader.core.files.save_json) + учёт времени. Словарь поменяли из другого потока
    прямо во время записи — пишем ещё раз (меню обрабатывает нажатия разных людей одновременно)."""
    t0 = time.perf_counter()
    try:
        for n in range(3):
            try:
                return _save_json_raw(path, data, private, backup)
            except RuntimeError:
                if n == 2:
                    raise
    finally:
        perf_add("json", os.path.basename(str(path)), time.perf_counter() - t0)


CL_LOCK = threading.RLock()  # кабинеты и темы: новый кабинет / тема заводится строго по одному


BG_THREADS = set()           # фоновые «отправить и забыть» из asyncio-цикла (для проверок: bg_wait)
BG_LOCK = threading.Lock()


def on_loop_thread():
    """Код идёт в потоке asyncio-цикла (из корутины): сетевой запрос здесь держит ВСЁ — чтение Telegram всех
    каналов и публикацию. В потоках меню, трендов, аналитики и в asyncio.to_thread — False."""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def in_background(fn, *args, name="bg"):
    """«Отправить и забыть»: из asyncio-цикла — в отдельном потоке с той же темой (contextvars), чтобы медленный
    MAX не останавливал бота; из любого другого места — сразу, как обычный вызов."""
    if not on_loop_thread():
        return fn(*args)
    ctx = contextvars.copy_context()

    def run():
        try:
            ctx.run(fn, *args)
        except Exception:
            log_exc(f"фон: {getattr(fn, '__name__', name)}")
        finally:
            with BG_LOCK:
                BG_THREADS.discard(threading.current_thread())

    th = threading.Thread(target=run, daemon=True, name=f"bg-{name}")
    with BG_LOCK:
        BG_THREADS.add(th)
    th.start()
    return None


def bg_wait(timeout=10.0):
    """Дождаться фоновых отправок (проверки, остановка). True — все закончились."""
    end = time.time() + timeout
    while time.time() < end:
        with BG_LOCK:
            alive = [t for t in BG_THREADS if t.is_alive()]
        if not alive:
            return True
        alive[0].join(max(0.01, min(0.2, end - time.time())))
    return False


def cabinet_locked(fn):
    """Заводит кабинет или тему под CL_LOCK: два новых покупателя в одну секунду не получат один номер темы."""
    @functools.wraps(fn)
    def wrap(*a, **k):
        with CL_LOCK:
            return fn(*a, **k)
    return wrap


if __name__ == "__main__":
    setup_data_dir()                      # до загрузки настроек: подтянуть рабочий вход и данные

# ---------- файлы состояния и настройки ----------


if CLIENT_MODE:                           # бот клиента начинает с чистого листа: свои каналы, своя подпись
    DEFAULTS["channels"] = []
    DEFAULTS["signature_text"] = ""
    DEFAULTS["old_signatures"] = []
    DEFAULTS["approval_on"] = False       # покупателю — «просто работает»: посты выходят сами, без нажатий
    DEFAULTS["gap_min"] = 20              # и без долгих пауз между постами
GCFG = copy.deepcopy(DEFAULTS)           # общие настройки (и значения по умолчанию для старой темы)
GCFG.update(load_json_safe(SETTINGS_FILE, {}))      # испорчен/пропал — из резервной копии
for _k in ("host_uids", "host_chat"):     # старые версии записывали «хозяйские» id из чужих чатов (канала) — забываем
    GCFG.pop(_k, None)
def contact_ok(s):
    """Контакт продавца похож на настоящий (ник, ссылка, телефон), а не «.» или пробел."""
    return len(re.sub(r"[^\w]", "", str(s or ""))) >= 3


def _env_fp(value):
    return hashlib.sha256(str(value or "").strip().encode()).hexdigest()[:16]


def panel_set(key, value, env_value):
    """Настройку задали в панели: запоминаем и отпечаток переменной окружения на этот момент."""
    GCFG[key] = value
    GCFG.setdefault("env_seen", {})[key] = _env_fp(env_value)


def pick_setting(key, env_value):
    """Обычная (не секретная) настройка: из панели или из переменной — что поменяли ПОЗЖЕ.
    Переменную изменили после ввода в панели (или отпечатка нет — старые настройки) — берём переменную."""
    env_value = str(env_value or "").strip()
    panel = GCFG.get(key)
    if not panel:
        return env_value or None
    if env_value and (GCFG.get("env_seen") or {}).get(key) != _env_fp(env_value):
        return env_value
    return panel


if not contact_ok(GCFG.get("seller_contact")):
    GCFG.pop("seller_contact", None)                    # «.» и прочий мусор — забываем, хозяину покажем «задай контакт»
if not CLIENT_MODE and GCFG.get("seller_contact"):       # контакт: из панели или переменной — что поменяли позже
    SELLER_CONTACT = pick_setting("seller_contact", os.environ.get("SELLER_CONTACT")) or SELLER_CONTACT
elif CLIENT_MODE:
    _sc = load_json("subscription.json", {}).get("contact")
    if _sc:
        SELLER_CONTACT = _sc
# перенос владельца из старой версии настроек
if GCFG.get("owner_id") and GCFG["owner_id"] not in GCFG["admins"]:
    GCFG["admins"].insert(0, GCFG["owner_id"])
    if GCFG.get("owner_chat"):
        GCFG["admin_chats"][str(GCFG["owner_id"])] = GCFG["owner_chat"]

# Общие для всех тем. Всё остальное — настройки конкретной темы.
GLOBAL_KEYS = {"paused", "interval", "admins", "admin_names", "admin_chats",
               "report_on", "report_hour", "owner_id", "owner_chat"}
STAT_KEYS = ("published", "dups", "errors", "expired", "blocked", "rejected", "ads")


def theme_files(tid):
    """Файлы данных темы tid (не первой основной): очередь, публикации, последние посты, история, реклама."""
    def sfx(base):
        root, ext = os.path.splitext(base)
        return f"{root}_{tid}{ext}"
    return {"queue": sfx(QUEUE_FILE), "pub": sfx(PUB_FILE),
            "state": sfx(STATE_FILE), "history": sfx(HISTORY_FILE), "adheld": sfx("ads_held.json")}


class Theme:
    """Тема = свои каналы-источники + свой канал MAX + своя очередь, расписание и фильтры.
    Все темы работают одновременно; в меню ты выбираешь, какую настраиваешь."""

    def __init__(self, tid, name, link, chat_id=None, own=None, legacy=False, owner=""):
        self.id, self.name, self.link, self.chat_id = tid, name, link, chat_id
        self.owner = str(owner or "")               # host-режим: чей это кабинет (user_id владельца кабинета)
        self.legacy = legacy                        # первая тема хранит данные в старых файлах
        self.own = own if own is not None else {}   # настройки темы
        if legacy:
            self.f = {"queue": QUEUE_FILE, "pub": PUB_FILE,
                      "state": STATE_FILE, "history": HISTORY_FILE, "adheld": "ads_held.json"}
        else:
            self.f = theme_files(tid)
        self.prefix = "" if legacy else f"{tid}|"   # в id постов и пачек (чтобы кнопки находили тему)
        self.fs = "" if legacy else f"{tid}_"       # в именах временных файлов
        self.queue = load_json(self.f["queue"], [])
        self.pub = {"last_pub": 0, "ch_last_date": {}, "ch_last_pub": {}, "series": {}}
        self.pub.update(load_json(self.f["pub"], {}))
        self.stats = {k: 0 for k in STAT_KEYS}
        self.stats.update(self.pub.get("stats", {}))
        self.adheld = load_json(self.f["adheld"], [])   # что отсеяно как реклама (для «🧠 Учить фильтр»)
        self.saved_ids = load_json(self.f["state"], {})
        self.history = load_json(self.f["history"], [])
        self.last_ids = {}                          # последний обработанный пост по каждому каналу
        self.force = {"until": 0}                   # до этого времени публикуем без ожидания
        self.flags = {"test": False, "empty_at": 0}
        self.lock = None                            # asyncio.Lock, создаётся при запуске

    def to_json(self):
        return {"id": self.id, "name": self.name, "link": self.link, "chat_id": self.chat_id,
                "own": self.own, "legacy": self.legacy, "owner": self.owner}


def _load_themes():
    data = load_json_safe(THEMES_FILE, {})
    themes = [Theme(d["id"], d["name"], d.get("link", ""), d.get("chat_id"), d.get("own", {}),
                    d.get("legacy", False), d.get("owner", "")) for d in data.get("themes", [])]
    if not themes and not HOST_MODE:
        themes = [Theme("main", "Основная", MAX_CHANNEL_LINK, MAX_CHAT_ID, {}, legacy=True)]
    meta = {"active": data.get("active", {}), "last_report": data.get("last_report", "")}
    if not meta["last_report"] and themes:
        meta["last_report"] = themes[0].pub.get("last_report", "")
    return themes, meta


THEMES, META = _load_themes()
_VOID = Theme("__void__", "—", "")                    # запасная тема, чтобы cur() не падал без кабинетов
CUR = contextvars.ContextVar("theme", default=None)   # тема, с которой сейчас работает код
_log.CUR = CUR                         # журнал знает, чей кабинет


def cur():
    """Тема, с которой сейчас работает код. Не выбрана: в host-режиме — пустая (а не первая попавшаяся —
    она чья-то чужая), в обычном боте — первая (основная)."""
    t = CUR.get()
    if t is not None:
        return t
    return _VOID if HOST_MODE or not THEMES else THEMES[0]


# ---------- HOST-РЕЖИМ: кабинеты пользователей в одном боте ----------
TENANTS_FILE = "tenants.json"
TENANTS = load_json_safe(TENANTS_FILE, {}) if HOST_MODE else {}   # {user_id владельца кабинета: {...}}
TEN_LOCK = threading.Lock() if False else None


def save_tenants():
    if HOST_MODE:
        save_json(TENANTS_FILE, TENANTS, backup=True)


def blank_tenant(uid, name=""):
    return {"name": name or f"user{uid}", "admins": [uid], "chats": {}, "names": {},
            "until": 0, "enabled": True, "plan": "", "lim_themes": LIMIT_THEMES, "lim_sources": LIMIT_SOURCES,
            "warned": [], "created": time.time(), "consent": ""}


def tenant_key(uid):
    """К какому кабинету относится пользователь. Свой кабинет (ключ — его id; у хозяина — кабинет хозяина)
    важнее кабинетов, где он есть в списке админов: иначе «мои темы» — чужие. None — ни к какому."""
    if not HOST_MODE or uid is None:
        return None
    su = str(uid)
    if su in TENANTS:
        return su
    hk = host_id()
    if hk and hk in TENANTS and is_host(uid):
        return hk
    return next((key for key, c in TENANTS.items() if any(same_uid(a, uid) for a in (c.get("admins") or []))), None)


def tenant_by_chat(chat):
    """Кабинет по чату пользователя — надёжнее id (MAX шлёт разные id в разных событиях). Предпочитаем оплаченный."""
    if not HOST_MODE or not chat:
        return None
    matches = [key for key, c in TENANTS.items()
               if any(same_uid(ch, chat) for ch in (c.get("chats") or {}).values())]
    if not matches:
        return None
    matches.sort(key=lambda k: (tenant_mode(k) == "run", TENANTS[k].get("until", 0)), reverse=True)
    return matches[0]


def tenant_rec_by_theme():
    """Запись кабинета, которому принадлежит текущая тема (для CFG admins/paused в host-режиме)."""
    if not HOST_MODE:
        return None
    key = getattr(cur(), "owner", "")
    return TENANTS.get(key) if key else None


def tenant_themes(key):
    return [t for t in THEMES if t.owner == str(key)]


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
    """Тема по id поста/пачки: у не-первых тем он начинается с «id|», у старой основной — без «|».
    Темы уже нет (удалили) — None: кнопку старой карточки не относим к чужой теме."""
    if "|" in ref:
        return theme_by_id(ref.split("|", 1)[0])
    return next((t for t in THEMES if t.legacy), None)


def active_theme(uid):
    if HOST_MODE:
        key = tenant_key(uid)
        if key is None:
            return None
        ts = tenant_themes(key)
        if not ts:
            return None
        act = theme_by_id(META["active"].get(str(uid)))
        return act if act in ts else ts[0]
    return theme_by_id(META["active"].get(str(uid))) or (THEMES[0] if THEMES else _VOID)


def buyer_menu(uid):
    """Главное меню покупателя — ровно 6 кнопок. «Поддержка» — прямая ссылка на профиль продавца в MAX.
    У хозяина и админов ADMIN_IDS главное меню — панель кабинетов; это меню они видят через «👀»."""
    state = ("⏸ Бот на паузе — посты не выходят. Снять: «⚙️ Настройки»." if CFG["paused"]
             else "▶️ Бот работает — новые посты выходят сами.")
    text = "📋 Главное меню\n\n" + state + "\n\n" + subscription_line()
    link = seller_link()
    support = LB("❓ Поддержка", link) if link else B("❓ Поддержка", "m:sup")   # не ссылка (телефон) — показать контакт
    rows = [[B("🎭 Мои каналы", "m:th"), B("📅 Расписание", "m:q")],
            [B("⚙️ Настройки", "m:more"), B("📊 Статистика и Отчеты", "m:sr")],
            [B("💳 Подписка", "m:pay", intent="positive"), support]]
    return text.strip(), rows


def simple_menu(uid):
    """Простое меню покупателя (как у ботов клиентов): у клиента-подпроцесса и у покупателя в host-режиме."""
    return CLIENT_MODE or (HOST_MODE and not is_host(uid))


def show_tg(uid):
    """Кнопку «🔑 Telegram» в host-режиме видит только хозяин: аккаунт Telegram общий на весь бот."""
    if NO_TG:
        return False
    if HOST_MODE and not is_host(uid):
        return False
    return True


def my_themes(uid):
    """Темы, которыми управляет пользователь: в host-режиме — только его кабинет."""
    if HOST_MODE:
        key = tenant_key(uid)
        return tenant_themes(key) if key else []
    return THEMES


def theme_tag():
    n = len(tenant_themes(cur().owner)) if HOST_MODE and cur().owner else len(THEMES)
    return f" · 🎭 {cur().name}" if n > 1 else ""


_TENANT_KEYS = {"admins": "admins", "admin_chats": "chats", "admin_names": "names", "paused": "paused"}
_TENANT_DEF = {"admins": [], "admin_chats": {}, "admin_names": {}, "paused": False}


class _ThemeCfg:
    """CFG[...]: общие ключи — из GCFG, остальное — из настроек текущей темы.
    В host-режиме admins/admin_chats/admin_names/paused берутся у кабинета текущей темы."""

    def __getitem__(self, k):
        if HOST_MODE and k in _TENANT_KEYS:
            rec = tenant_rec_by_theme()
            if rec is not None:
                return rec.setdefault(_TENANT_KEYS[k], copy.deepcopy(_TENANT_DEF[k]))
            if k != "paused" or cur().owner:            # нет кабинета: карточки и уведомления — никому чужому
                return copy.deepcopy(_TENANT_DEF[k])
        if k in GLOBAL_KEYS:
            return GCFG[k]
        t = cur()
        if k not in t.own:
            t.own[k] = copy.deepcopy((GCFG if t.legacy else DEFAULTS)[k])
        return t.own[k]

    def __setitem__(self, k, v):
        if HOST_MODE and k in _TENANT_KEYS:
            rec = tenant_rec_by_theme()
            if rec is not None:
                rec[_TENANT_KEYS[k]] = v
                save_tenants()
                return
            if k != "paused" or cur().owner:            # нет кабинета — некуда записать (и в общие не пишем)
                return
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
    """Список текущей темы (QUEUE, ADHELD) — ведёт себя как обычный list."""

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
ADHELD = _ListProxy("adheld")          # отсеянная реклама: последние 15, можно вернуть кнопкой «не реклама»
PUB = _DictProxy("pub")                # когда и что публиковали (для расписания)
STATS = _DictProxy("stats")
LAST_IDS = _DictProxy("last_ids")
FORCE = _DictProxy("force")
FLAGS = _DictProxy("flags")            # запросы из меню/команд для планировщика
BAD = {}                               # канал -> когда повторить попытку (не открылся)
CARDS = load_json(CARDS_FILE, [])      # [{chat, mid, ref, del_at, t}]
CARDS_LOCK = threading.Lock()


def save_cfg():
    save_json(SETTINGS_FILE, GCFG, private=True, backup=True)   # тут и ключи из панели: файл читает только бот
    save_json(THEMES_FILE, {"themes": [t.to_json() for t in THEMES], "active": META["active"],
                            "last_report": META["last_report"]}, backup=True)


def save_queue():
    t = cur()
    save_json(t.f["queue"], t.queue)


def save_adheld():
    t = cur()
    save_json(t.f["adheld"], t.adheld)


def save_pub():
    t = cur()
    t.pub["stats"] = dict(t.stats)
    save_json(t.f["pub"], t.pub)


def save_last_ids():
    t = cur()
    save_json(t.f["state"], t.last_ids)


def save_history():
    t = cur()
    t.history[:] = prune(t.history)        # в памяти тоже не копим лишнее
    save_json(t.f["history"], t.history)


_SAVE_SEQ, _SAVE_SEQ_LOCK, _SAVE_N = {}, threading.Lock(), __import__("itertools").count()


async def save_json_async(path, data, **kw):
    """Запись файла из корутины — в потоке, цикл не ждёт диск. data — снимок, сделанный в цикле (копия);
    более старый снимок не затрёт более новый, если записи одного файла обгонят друг друга."""
    seq = next(_SAVE_N)

    def write():
        with _SAVE_SEQ_LOCK:
            if _SAVE_SEQ.get(path, -1) > seq:
                return
            save_json(path, data, **kw)
            _SAVE_SEQ[path] = seq

    await asyncio.to_thread(write)


async def save_history_async():
    """save_history из чтения каналов: чистка — в цикле (быстро), запись истории (может быть сотни КБ) — в потоке."""
    t = cur()
    t.history[:] = prune(t.history)
    await save_json_async(t.f["history"], list(t.history))


async def save_last_ids_async():
    t = cur()
    await save_json_async(t.f["state"], dict(t.last_ids))


def bump(key):
    """Счётчик для статуса и отчёта (хранится на диске)."""
    STATS[key] = STATS.get(key, 0) + 1
    save_pub()


def signature():
    if not CFG["signature_on"] or not CFG["signature_text"].strip():
        return ""
    link = cur().link
    return f"[{CFG['signature_text']}]({link})" if link else CFG["signature_text"]


def new_theme_id():
    """id новой темы — больше всех, что уже были (GCFG["theme_seq"]): новая тема не получает файлы, карточки
    и задачи удалённой (после «🗑 Очистить старые каналы» старые данные не всплывают в новых каналах)."""
    nums = [int(t.id[1:]) for t in THEMES if re.fullmatch(r"t\d+", t.id)]
    n = max([int(GCFG.get("theme_seq") or 1)] + nums) + 1
    while any(t.id == f"t{n}" for t in THEMES) or any(os.path.exists(p) for p in theme_files(f"t{n}").values()):
        n += 1                                          # файлы остались от удалённой темы — номер пропускаем
    GCFG["theme_seq"] = n
    return f"t{n}"


@cabinet_locked
def create_theme(name, owner=""):
    t = Theme(new_theme_id(), name, "", owner=str(owner or ""))
    t.own["channels"] = []
    t.own["old_signatures"] = []
    t.own["signature_text"] = name[:60]
    THEMES.append(t)
    save_cfg()
    return t


def delete_theme(t, force=False):
    """Удаляет тему вместе с очередью. Первую (основную) удалить нельзя — кроме очистки
    в host-режиме (force): там основная тема — пережиток обычного бота, без неё всё работает."""
    if t not in THEMES or (t.legacy and not (force and HOST_MODE)):
        return
    with theme_ctx(t):
        for it in list(QUEUE):
            drop_item(it)
    THEMES.remove(t)
    for uid, tid in list(META["active"].items()):
        if tid == t.id:
            del META["active"][uid]
    for path in t.f.values():
        try:
            os.remove(path)
        except OSError:
            pass
    CUR.set(None)                                       # дальше — без удалённой темы (см. cur())
    save_cfg()


# ---------- чистка текста ----------
# код и адреса ссылок — как есть; голая ссылка заканчивается перед закрывающей разметкой (**, __, ~~) и пробелом
MD_KEEP = re.compile(r"```.*?```|`[^`\n]*`|\]\([^)]*\)|https?://\S+?(?=(?:\*\*|__|~~)?(?:\s|$))", re.S)


def fix_markdown(text: str) -> str:
    """Приводит разметку Telegram к виду, который понимает MAX:
    пробелы выносятся за звёздочки (иначе ** остаются в тексте), курсив __x__ -> _x_.
    Код и адреса ссылок не трогаем: «__» в адресе — часть адреса, а не курсив."""
    keep = []

    def hide(m):
        keep.append(m.group(0))
        return f"\x00{len(keep) - 1}\x00"
    text = MD_KEEP.sub(hide, text)
    text = re.sub(r"\|\|(.+?)\|\|", r"\1", text, flags=re.S)       # спойлеры -> обычный текст

    text = re.sub(r"\*\*\*\*|~~~~", "", text)    # пустая пара (жирный без текста): «**@канал**» без канала, «**а:****б**»

    def wrap(marker, token):
        pat = re.compile(re.escape(marker) + r"(\s*)(.+?)(\s*)" + re.escape(marker), re.S)

        def repl(m):
            parts = [p.strip() for p in m.group(2).split("\n") if p.strip()]
            if not parts:                              # «** **» — оставляем пробел, слова не склеиваются
                return m.group(1) + m.group(2) + m.group(3)
            inner = "\n".join(token + p + token for p in parts)
            return m.group(1) + inner + m.group(3)
        return pat.sub(repl, text)

    # парные метки — во временные знаки; что осталось от ** и ~~ — непарное, в MAX оно видно звёздочками: убираем
    text = wrap("**", "\ue000")
    text = wrap("~~", "\ue001")
    text = wrap("__", "\ue002")
    text = text.replace("**", "").replace("~~", "")
    text = text.replace("\ue000", "**").replace("\ue001", "~~").replace("\ue002", "_")
    return re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], text)


LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")


def owner_filters():
    """Подписи доноров и рекламные слова: добавленные в ЛЮБОЙ теме этого владельца работают во всех его темах."""
    sigs, ads = list(CFG["old_signatures"]), list(CFG["ad_keywords"])
    with contextlib.suppress(Exception):
        t = cur()
        mates = [x for x in THEMES if x is not t and x.owner == t.owner] if HOST_MODE or len(THEMES) > 1 else []
        for x in mates:
            sigs += [v for v in x.own.get("old_signatures") or [] if v not in sigs]
            ads += [v for v in x.own.get("ad_keywords") or [] if v not in ads]
    return sigs, ads


def _is_promo_line(line):
    """«Подпишись на @x», «Наш чат: @x»: без @канала от строки остался бы огрызок — убираем строку целиком."""
    if not AD_REF.search(line):
        return False
    rest = AD_REF.sub(" ", LINK_RE.sub(r"\1", line))
    if AD_SUB.search(rest):
        return True
    return len(re.findall(r"[^\W\d_]{2,}", rest)) <= 3 and bool(AD_LABEL.search(rest.strip(" .!|—-🔗👉⤵️")))


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


# ---------- распознавание рекламы ----------
AD_STRONG = re.compile(r"\berid\b|[?&]erid=|#\s?реклам|(?:^|\n)\s*реклама\b|на\s+правах\s+рекламы|#ad\b|#промо\b|"
                       r"партн[её]рск\w*\s+(?:пост|материал|публикац)|спонсор\w*\s+(?:пост|выпуск|публикац)|"
                       r"рекламодател|взаимн\w*\s+пиар|взаимопиар|#пиар\b", re.I)
AD_CTA = re.compile(r"подпис(?:ыв|ат|ыва|ка\b|ывайся|ывайтесь|ись|итесь)|подпиш(?:ись|итесь)|заход(?:и|ите)\b|"
                    r"вступ(?:ай|айте)\b|залета(?:й|йте)\b|переход(?:и|ите)\b|жми(?:те)?\b|успе(?:й|йте)\b|"
                    r"промокод|скидк|розыгрыш|разыгрыва|заработ|доход\w* от|казино|ставк[аиу] на|бонус|"
                    r"регистрир|по ссылке|ссылк\w* в (?:профиле|описании|шапке|био)|в нашем боте|в бот[ае]\b|"
                    r"акци[яи] до|только сегодня|бесплатн\w* (?:курс|доступ|урок)|пасс?ивн\w* доход", re.I)
AD_LINK = re.compile(r"https?://[^\s)\]]+|(?<![\w@/])t\.me/[^\s)\]]+|(?<!\w)@[A-Za-z0-9_]{4,}bot\b", re.I)
# Известные рекламные редиректы/лендинги/трекеры и партнёрские метки — локальный «точно реклама» без ИИ.
AD_HOSTS = re.compile(r"justclick\.ru|salebot\.|getcourse\.|bit\.ly|clck\.ru|vk\.cc|tinyurl\.com|cutt\.ly|"
                      r"goo\.su|taplink\.cc|u\.to/|t\.me/\S*start=|[?&](?:utm_source|utm_campaign|erid)=", re.I)
# Правила «точно реклама», общие для любой темы (игры, вязание, магазины…):
AD_REF = re.compile(r"(?<![\w@/])@([A-Za-z]\w{3,31})|t\.me/(?:s/)?([A-Za-z]\w{3,31})", re.I)     # чужой канал
AD_SUB = re.compile(r"подпи(?:шись|шитесь|сывайся|сывайтесь|саться)|вступа(?:й|йте)\b|вступи(?:те)?\b", re.I)
AD_GIVE = re.compile(r"розыгрыш|разыгрыва|конкурс|участ(?:ие|ия|ии|вуй|вова|ник)|билет|победител|выигра|"
                     r"крути\w* колес|колес\w* фортун", re.I)
AD_LIMIT = re.compile(r"активац\w*\s+ограничен|кол(?:-?в\w*|ичеств\w*)\s+ограничен|поторопи(?:сь|тесь)|"
                      r"успе(?:й|йте)\s+(?:активир|забрать|ввести|получить|воспольз|купить)|пока\s+не\s+(?:разобрали|закончил)",
                      re.I)
AD_CODE = re.compile(r"\bкод(?:ы|а|ом|ов)?\b|промокод|ваучер|voucher|купон|coupon|redeem|скидк", re.I)
AD_LABEL = re.compile(r"\b(?:чат|канал|группа|бот|паблик|источник|автор|наш|сайт|инст\w*|тикток|вк|ютуб)\b|:\s*$", re.I)
AD_SURE = ("пометка", "условия:", "код или скидка")    # причины, при которых пост — точно реклама


def ad_sub_refs(text, own=""):
    """Чужие каналы, на которые пост просит подписаться: «Подпишись на @x», «Вступай: t.me/x»."""
    refs = set()
    for line in (text or "").split("\n"):
        if AD_SUB.search(line):
            refs |= {(a or b).lower() for a, b in AD_REF.findall(line)}
    return refs - {(own or "").lower()}


# Официальные сайты игр и площадок: ссылка туда — новость или раздача от разработчиков, а не реклама
TRUSTED_SITES = ("roblox.com", "rbx.com", "supercell.com", "brawlstars.com", "clashroyale.com", "clashofclans.com",
                 "standoff2.com", "axlebolt.com", "so2.com", "minecraft.net", "minecraft.wiki", "mojang.com",
                 "pubg.com", "pubgmobile.com", "krafton.com", "battlegroundsmobileindia.com",
                 "hoyoverse.com", "hoyolab.com", "mihoyo.com", "genshin-impact.fandom.com",
                 "epicgames.com", "fortnite.com",
                 "steampowered.com", "steamcommunity.com", "playstation.com", "xbox.com", "nintendo.com",
                 "ea.com", "ubisoft.com", "riotgames.com", "leagueoflegends.com", "blizzard.com", "battle.net",
                 "hoyoverse.com", "genshin.hoyoverse.com", "pubgmobile.com", "garena.com", "rockstargames.com",
                 "youtube.com", "youtu.be", "twitch.tv", "wikipedia.org", "fandom.com", "x.com", "twitter.com",
                 "devforum.roblox.com", "vk.com/roblox")


def _trusted(u):
    host = re.sub(r"^https?://(www\.)?", "", u).split("/")[0].lower()
    mine = [re.sub(r"^https?://(www\.)?", "", d.lower()).strip("/ ") for d in CFG["trusted_sites"]]
    return any(host == d or host.endswith("." + d) for d in list(TRUSTED_SITES) + [m for m in mine if m])


def _tg_name(u):
    m = re.search(r"t\.me/(?:s/)?([^/?#]+)", u)
    return m.group(1).lower() if m else ""


def url_buttons(m):
    """Сколько у поста кнопок-ссылок (рекламные посты почти всегда с кнопкой «Перейти»)."""
    n = getattr(m, "url_buttons", None)
    if isinstance(n, int):
        return n
    rm = getattr(m, "reply_markup", None)
    return sum(1 for row in getattr(rm, "rows", None) or [] for b in getattr(row, "buttons", None) or []
               if getattr(b, "url", None))


def ad_score(raw, ch="", buttons=0):
    """(баллы, причины): похож ли пост канала-источника на рекламу. 10 баллов и больше — реклама.
    Маркировка «Реклама»/erid (обязательна по закону) — сразу 10; иначе считаем чужие ссылки, ботов и призывы."""
    text = raw or ""
    score, why = 0, []
    if AD_STRONG.search(text):
        score += 10
        why.append("пометка «реклама»/erid")
    own = (ch or "").lower()
    links = {u.rstrip(".,!?:;").lower() for u in AD_LINK.findall(text)}
    links = {u for u in links if not (own and _tg_name(u) == own)}      # ссылки на сам канал — не реклама
    invites = sum(1 for u in links if "/+" in u or "joinchat" in u)
    bots = sum(1 for u in links if "start=" in u or re.search(r"bot\b", u.split("?")[0]))
    other_tg = sum(1 for u in links if "t.me/" in u) - invites - bots
    external = sum(1 for u in links if "t.me/" not in u and not u.startswith("@") and not _trusted(u))
    if invites:
        score += min(10, 6 * invites)
        why.append("ссылка-приглашение в чужой канал")
    if bots:
        score += min(8, 4 * bots)
        why.append("ссылка на бота")
    if other_tg > 0:
        score += min(4, 2 * other_tg)
        why.append("ссылки на другие каналы")
    if external:
        score += min(6, 2 * external)
        why.append("внешние ссылки")
    if AD_HOSTS.search(text):
        score += 4
        why.append("рекламная ссылка (трекер/лендинг)")
    cta = {m.group(0).lower() for m in AD_CTA.finditer(text)}
    if cta:
        score += min(6, 2 * len(cta))
        why.append("призывы: " + ", ".join(sorted(cta))[:40])
    if buttons:
        score += 4
        why.append("кнопка-ссылка")
    if ad_sub_refs(text, own) and AD_GIVE.search(text):          # розыгрыш «подпишись на каналы — получи билет»;
        why.insert(0, "условия: подписаться на чужие каналы")      # просто подпись канала «@news | @chat» — не реклама
    if AD_LIMIT.search(text) and AD_CODE.search(text):
        why.insert(0, "код или скидка с «успей — ограничено»")
    return score, why


# ---------- обучаемый фильтр рекламы («мини-ИИ»): учится на нажатиях «реклама» / «не реклама» ----------
AD_MODEL_FILE = "ad_model.json"
AD_MODEL_MIN = 3          # учиться начинает, когда есть хотя бы 3 примера рекламы и 3 нормальных поста


def ad_tokens(text, buttons=0):
    """Признаки поста: основы слов (первые 5 букв) + ссылки, боты, кнопки, пометка «реклама»."""
    t = (text or "").lower().replace("ё", "е")
    toks = {w[:5] for w in re.findall(r"[a-zа-я0-9]{3,}", t) if not w.isdigit()}
    links = AD_LINK.findall(t)
    if any("/+" in u or "joinchat" in u for u in links):
        toks.add("__invite")
    if any("start=" in u or re.search(r"bot\b", u.split("?")[0]) for u in links):
        toks.add("__bot")
    if links:
        toks.add("__link")
    if len(set(links)) >= 2:
        toks.add("__links2")
    if buttons:
        toks.add("__btn")
    if AD_STRONG.search(t):
        toks.add("__mark")
    return toks


def ad_model():
    m = load_json(AD_MODEL_FILE, {})
    return {"ad": m.get("ad", {}), "ok": m.get("ok", {}), "n_ad": m.get("n_ad", 0), "n_ok": m.get("n_ok", 0)}


def ad_learn(text, buttons, is_ad):
    m = ad_model()
    k = "ad" if is_ad else "ok"
    m["n_" + k] += 1
    for tok in ad_tokens(text, buttons):
        m[k][tok] = m[k].get(tok, 0) + 1
    save_json(AD_MODEL_FILE, m)


def ad_prob(text, buttons=0):
    """Вероятность «реклама» по тому, чему научили. None — пока мало примеров."""
    m = ad_model()
    if m["n_ad"] < AD_MODEL_MIN or m["n_ok"] < AD_MODEL_MIN:
        return None
    lo = math.log((m["n_ad"] + 1) / (m["n_ok"] + 1))
    for tok in ad_tokens(text, buttons):
        pa = (m["ad"].get(tok, 0) + 0.5) / (m["n_ad"] + 1)
        po = (m["ok"].get(tok, 0) + 0.5) / (m["n_ok"] + 1)
        if m["ad"].get(tok) or m["ok"].get(tok):        # незнакомые слова ничего не говорят
            lo += math.log(pa / po)
    return 1 / (1 + math.exp(-max(-30, min(30, lo))))


AD_MONEY = re.compile(r"промокод|скидк|казино|ставк[аиу]|букмекер|заработ|доход|бонус|розыгрыш|регистрир|"
                      r"курс[ауе]?\b|купи|покупк|закаж|магазин|цен[аые]\b|руб\b|₽|\$|кэшбэк|кешбек", re.I)


def ad_verdict(raw, ch="", buttons=0):
    """(вердикт, причины): "ad" — точно реклама, "ask" — спорно (спросить владельца), "ok" — не реклама.
    Точно реклама: пометка «Реклама»/erid по закону, очень много рекламных признаков или ты уже отмечал такое.
    Всё, что «может быть рекламой» (например, видео с припиской «переходи в бота»), — спорное: решаешь ты."""
    strict = CFG.get("ads_strict")
    thr, sure = (7, 14) if strict else (10, 17)        # 🧠 строже — ловим больше рекламы
    score, why = ad_score(raw, ch, buttons)
    p = ad_prob(raw, buttons)
    if any(w.startswith(AD_SURE) for w in why):
        if any(w.startswith("пометка") for w in why):  # учимся только на рекламе с пометкой по закону: правила
            with contextlib.suppress(Exception):       # («код», «подпишись») не должны учить фильтр — иначе он
                ad_learn(raw, buttons, True)           # начнёт ловить обычные посты с этими словами
        return "ad", why
    learned = [f"🧠 похоже на рекламу, которую ты отмечал ({round(p * 100)}%)"] if p is not None else []
    if p is not None and p > 0.97:                     # ты уже отмечал такое как рекламу — уверенно
        return "ad", learned + why
    if p is not None and p < 0.15:                     # такое ты отмечал как «не реклама»
        return "ok", why
    out = any(w.startswith(("внешние ссылки", "ссылка-приглашение")) for w in why)
    btn = any(w.startswith("кнопка-ссылка") for w in why)
    if score >= sure or (btn and out) or (score >= thr and AD_MONEY.search(raw or "") and (out or btn)):
        return "ad", why                               # продажа со ссылкой/кнопкой наружу — точно реклама
    if score >= thr or (p is not None and p > 0.9):
        return "ask", (learned + why) if score < thr else why
    return "ok", why


def is_ad_post(raw, ch="", buttons=0):
    """(не публиковать сразу?, причины): и точная, и спорная реклама сама не выходит —
    спорную бот присылает владельцу на «да/нет» (или отсеивает, если вопросы выключены)."""
    v, why = ad_verdict(raw, ch, buttons)
    return v != "ok", why


# ===== 🧠 УМНОЕ ВРЕМЯ: публикуем в часы, когда у канала больше просмотров =====
def hot_hours():
    """Часы (0-23) с просмотрами выше среднего, по постам за 7 дней. None — данных мало, не придерживаем."""
    now = time.time()
    by = {}
    for p in PUB.get("day_posts", []):
        if p.get("views") is not None and now - p["t"] < 7 * 86400:
            h = time.localtime(p["t"]).tm_hour
            by.setdefault(h, []).append(p["views"])
    seen = sum(len(v) for v in by.values())
    if seen < 20 or len(by) < 4:
        return None
    avg = {h: sum(v) / len(v) for h, v in by.items()}
    overall = sum(avg.values()) / len(avg)
    return {h for h, a in avg.items() if a >= overall}


def smart_hold(now, ready, limit):
    """🧠 Умное время: сейчас «холодный» час — придержим посты, но не дольше 60% срока хранения и не срочные."""
    if not CFG.get("smart_time") or on_free():
        return False
    hot = hot_hours()
    if hot is None or time.localtime(now).tm_hour in hot:
        return False
    if any(x.get("urgent") for x in ready):
        return False
    return max((now - x["enq"] for x in ready), default=0) < 0.6 * limit


# ===== 🧠 УМНЫЕ ХЭШТЕГИ =====
TAG_RULES = [(("роблокс", "roblox", "робукс", "robux"), ["#roblox", "#роблокс"]),
             (("стандофф", "standoff", "스탠", "so2", "скин"), ["#standoff2", "#стандофф"]),
             (("бравл", "brawl", "бравлик"), ["#brawlstars", "#бравлстарс"]),
             (("майнкрафт", "minecraft", "майн", "мобы"), ["#minecraft", "#майнкрафт"]),
             (("геншин", "genshin", "примогем"), ["#genshin", "#геншин"]),
             (("пабг", "pubg", "пубг"), ["#pubgmobile", "#пабг"]),
             (("обнов", "update", "патч", "версия"), ["#обновление"]),
             (("ивент", "event", "событ"), ["#ивент"]),
             (("промокод", "код на", "бесплатн"), ["#промокод"]),
             (("мем", "прикол", "ржака", "смешн"), ["#мемы"])]


def smart_tags(raw, ch=""):
    """🧠 2-3 хэштега по теме поста. Если в тексте уже есть хэштеги — не трогаем."""
    if not CFG.get("smart_tags") or not raw or "#" in raw or on_free():
        return ""
    low = raw.lower()
    tags = []
    for words, out in TAG_RULES:
        if any(w in low for w in words):
            for t in out:
                if t not in tags:
                    tags.append(t)
        if len(tags) >= 3:
            break
    return ("\n\n" + " ".join(tags[:3])) if tags else ""


AI_PROVIDERS = {"gemini": ("GEMINI_API_KEYS", "Gemini"), "openrouter": ("OPENROUTER_API_KEYS", "OpenRouter"),
                "deepseek": ("DEEPSEEK_API_KEYS", "DeepSeek")}


def ai_keys(provider):
    """Ключи провайдера ИИ: сохранённые в панели бота (settings.json) главнее переменной окружения."""
    saved = (GCFG.get("ai_keys") or {}).get(provider)
    if saved:
        return list(saved)
    return parse_keys(os.environ.get(AI_PROVIDERS[provider][0]))


AI_ROTATORS = {p: KeyRotator(p, (lambda p=p: ai_keys(p))) for p in AI_PROVIDERS}


def ai_settings():
    """Адрес и модель каждого провайдера: по умолчанию — из rewrite.DEFAULTS; можно поменять переменными."""
    out = {}
    for prov, d in AI_DEFAULTS.items():
        env = prov.upper()
        out[prov] = {"url": (os.environ.get(f"{env}_API_URL") or d["url"]).rstrip("/"),
                     "model": os.environ.get(f"{env}_MODEL") or d["model"]}
    return out


def ai_llm_ready():
    return any(ai_keys(p) for p in AI_PROVIDERS)


# 🧠 ИИ-расход (экономия ключей): бот зовёт ИИ только когда это оправдано. «Максимально экономить» —
# авто-пересказ постов лишь для VIP-кабинетов; «Стандарт»/«Без лимитов» — как раньше.
AI_EXHAUSTED_LOG = "AI skipped: all keys exhausted. Falling back to local rules."
AI_BUDGETS = {"save": "Максимально экономить", "std": "Стандарт", "max": "Без лимитов"}

def ai_budget():
    b = GCFG.get("ai_budget")
    return b if b in AI_BUDGETS else "std"

def theme_is_vip(t=None):
    """VIP-кабинет: ИИ работает даже в «Максимально экономить». Хозяин — всегда VIP; покупателя
    хозяин отмечает сам (TENANTS[owner]['vip'])."""
    t = t if t is not None else cur()
    owner = str(getattr(t, "owner", "") or "")
    if HOST_MODE and owner and owner == host_id():
        return True
    return bool((TENANTS.get(owner) or {}).get("vip"))

def ai_rewrite_allowed(t=None):
    """Авто-пересказ постов ИИ. В режиме «Максимально экономить» — только VIP-кабинеты (бережём ключи)."""
    return ai_budget() != "save" or theme_is_vip(t)


def ai_rewrite(text):
    """🤖 Пересказ поста через ИИ (Gemini → OpenRouter → DeepSeek). Все ключи исчерпаны — AiUnavailable."""
    t0 = time.perf_counter()
    try:
        return ai_rewrite_text(text, AI_ROTATORS, S, ai_settings(), journal)
    finally:
        perf_add("ai", "пересказ поста", time.perf_counter() - t0)


def compose_text(raw, ch="", rewritten=False):
    """Текст поста для MAX: сам пост + умные хэштеги (если включены) + подпись.
    Без ключей ИИ — локальный пересказ синонимами; с ключами пересказ делает publish_item (через ИИ)."""
    if ai_on() and not rewritten and not ai_llm_ready():
        with contextlib.suppress(Exception):
            raw = ai_edit(raw)
    return ((raw or "") + smart_tags(raw, ch) + "\n\n" + signature()).strip()


# ===== 🧠 АВТО-ОЦЕНКА ПОСТА: слабый — на одобрение, сильный — сразу =====
def post_quality(body, has_media):
    """0-10: насколько пост «живой». Учитываем длину, медиа, эмодзи, вопрос/призыв."""
    body = body or ""
    score = 0
    if has_media:
        score += 4
    n = len(body)
    score += 3 if n >= 60 else 2 if n >= 20 else 1 if n else 0
    if re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", body):
        score += 1
    if "?" in body or "!" in body:
        score += 1
    if has_media and n >= 20:
        score += 1
    return min(10, score)





def sig_norm(s):
    """Строка «по смыслу»: без оформления, ссылок, эмодзи и знаков; 𝐑𝐨𝐛𝐥𝐨𝐱 -> roblox, ё -> е."""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"\]\([^)]*\)", "]", s)                  # [текст](ссылка) -> текст
    s = re.sub(r"https?://\S+", " ", s).lower().replace("ё", "е")
    return " ".join(re.findall(r"[0-9a-zа-я]+", s))


# @упоминание — отдельное слово (вместе с пробелом после) или приклеенное к знакам: «(@x)», «**@x**»;
# @ в адресе (youtube.com/@x), в почте (a@b.ru), в тексте ссылки «[@x](…)» — не трогаем
MENTION_WORD_RE = re.compile(r"(?:^|(?<=[ \t]))@\w+(?:[ \t]+|$)", re.M)
MENTION_RE = re.compile(r"(?<![\w/.@=\[-])@\w+")


def drop_mentions(body: str) -> str:
    return MENTION_RE.sub("", MENTION_WORD_RE.sub("", body))


def tidy_spaces(body: str) -> str:
    """Без пробелов в конце строк и лишних пустых строк."""
    body = re.sub(r"[ \t]+\n", "\n", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def clean_text(text: str, with_signature: bool = True) -> str:
    """Убирает чужие подписи, футеры, рекламу, @упоминания и t.me-ссылки."""
    body = ""
    if text:
        # премиум-эмодзи и ссылки на людей Telegram -> просто текст
        text = re.sub(r"\[([^\]]*)\]\((?:emoji/|tg://)[^)]*\)", r"\1", text)
        lines = []
        sig_list, ad_list = owner_filters()
        sigs = [n for n in (sig_norm(x) for x in sig_list) if n]
        ads = [a.lower().strip() for a in ad_list if a.strip()]
        ads_n = [n for n in (sig_norm(a) for a in ad_list) if n]
        for line in text.split("\n"):
            low = line.lower()
            nl = sig_norm(line)
            if sigs and nl and any(sg in nl for sg in sigs):
                continue  # подпись канала-донора (жирная, ссылкой, с эмодзи — всё равно узнаём)
            if any(ad in low for ad in ads) or (nl and any(a in nl for a in ads_n)):
                continue  # реклама (тоже в любом оформлении)
            if _is_footer_line(line) or _is_promo_line(line):
                continue  # строка только ради чужого канала («Подпишись на @x», «Наш чат: @x») — целиком
            lines.append(line)
        # футеры в конце поста (строки только из ссылок на каналы/ВК/рекламу)
        while lines and (not lines[-1].strip() or _is_footer_line(lines[-1])):
            lines.pop()
        body = "\n".join(lines)
        body = re.sub(r"\[([^\]]*)\]\(https?://t\.me/[^)]*\)", r"\1", body)
        body = re.sub(r"https?://t\.me/\S+", "", body)   # голые ссылки t.me
        body = drop_mentions(body)                         # @упоминания (не @ внутри ссылок и почты)
        body = tidy_spaces(body)
        body = apply_replacements(body)
    body = tidy_spaces(fix_markdown(body))
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
        img_cut = 120 if CFG.get("dedupe_strict") else 25   # 🧠 строже: та же картинка = дубль даже с подписью
        if len(entry["norm"]) < img_cut and entry["img"] and set(entry["img"]) & set(h.get("img", [])):
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
    cid = resolve_chat_id(MAX_CHANNEL_LINK) if MAX_CHANNEL_LINK else None
    print("Поиск канала по ссылке", MAX_CHANNEL_LINK, "->", cid or "бот не видит такой канал среди своих")
    if cid:
        _save_channel_id(cid)
    return cid


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


def max_chats():
    """Все чаты бота в MAX как есть (GET /chats, все страницы). Ответ MAX пишем в журнал без обработки —
    видно, как именно MAX показывает канал (ссылка, тип, статус)."""
    out, marker = [], None
    for page in range(1, 11):
        try:
            params = {"count": 100}
            if marker:
                params["marker"] = marker
            r = S.get(f"{MAX_API}/chats", headers=HEADERS, params=params, timeout=30)
            print(f"MAX GET /chats (стр. {page}) -> {r.status_code}: {r.text[:6000]}")
            if r.status_code != 200:
                break
            j = r.json()
        except Exception as e:
            print("Список чатов бота:", type(e).__name__, e)
            break
        out += [c for c in j.get("chats") or [] if isinstance(c.get("chat_id"), int)]
        marker = j.get("marker")
        if not marker:
            break
    return out


KNOWN_FILE = "known_chats.json"
KNOWN_CHATS = load_json_safe(KNOWN_FILE, {})   # id канала (строкой) -> {"added_by", "t"}: MAX с июня 2026
                                               # не отдаёт список чатов (GET /chats) — каналы бот помнит сам
CHAT_INFO_CACHE = {}                           # id -> (сведения от MAX, когда спросили)


def remember_chat(cid, who=None):
    """Запоминаем канал, где есть бот (из «бот добавлен», привязки, проверенного id)."""
    if not isinstance(cid, int):
        return
    rec = KNOWN_CHATS.setdefault(str(cid), {"t": time.time()})
    if who is not None and not rec.get("added_by"):
        rec["added_by"] = str(who)
    with contextlib.suppress(OSError):
        save_json(KNOWN_FILE, KNOWN_CHATS, backup=True)


def forget_chat(cid):
    if KNOWN_CHATS.pop(str(cid), None) is not None:
        with contextlib.suppress(OSError):
            save_json(KNOWN_FILE, KNOWN_CHATS, backup=True)


def chat_info_cached(cid, ttl=600):
    """GET /chats/{id} (этот метод у MAX есть) с кэшем: не дёргаем MAX на каждое открытие списка каналов."""
    got = CHAT_INFO_CACHE.get(cid)
    if got is None or time.time() - got[1] > (ttl if got[0] else 60):
        got = (chat_info(cid), time.time())
        CHAT_INFO_CACHE[cid] = got
    return got[0]


def known_chats(types=("channel",)):
    """Каналы, которые бот знает сам (MAX их больше не перечисляет): [{chat_id, title, link, type}]."""
    ids = list(dict.fromkeys([int(k) for k in KNOWN_CHATS if re.fullmatch(r"-?\d+", k)]
                             + [t.chat_id for t in THEMES if isinstance(t.chat_id, int)]))
    out = []
    for cid in ids:
        info = chat_info_cached(cid)
        if not info or info.get("status") not in (None, "active") or info.get("type") not in (None,) + tuple(types):
            continue
        out.append({"chat_id": cid, "title": info.get("title") or str(cid), "link": info.get("link") or "",
                    "type": info.get("type")})
    return out


def bot_channels():
    """Каналы MAX, где состоит бот: [{chat_id, title, link}]. Что вернул GET /chats (если MAX ещё отдаёт) +
    каналы, которые бот знает сам (из «бот добавлен», привязанных тем, проверенных id)."""
    out = {c["chat_id"]: {"chat_id": c["chat_id"], "title": c.get("title") or str(c["chat_id"]), "link": c.get("link") or ""}
           for c in max_chats() if c.get("type") == "channel" and c.get("status") in (None, "active")}
    for c in known_chats():
        out.setdefault(c["chat_id"], {k: c[k] for k in ("chat_id", "title", "link")})
    return list(out.values())


def chat_admin_ids(chat_id):
    """id админов канала (для проверки «это твой канал»). None — MAX не ответил."""
    try:
        r = S.get(f"{MAX_API}/chats/{chat_id}/members/admins", headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        return [m.get("user_id") for m in (r.json().get("members") or [])]
    except Exception:
        return None


def cabinet_admin_of(key, cid, extra=()):
    """Админ ли канала MAX cid кто-то из админов кабинета key (или extra). None — MAX не ответил."""
    adm = chat_admin_ids(cid)
    if adm is None:
        return None
    mine = list((TENANTS.get(key) or {}).get("admins") or []) + ([key] if key else []) + list(extra)
    return any(same_uid(a, m) for a in adm for m in mine)


BIND_WAIT_SEC = 20 * 60   # дал ссылку, бот канал не нашёл — 20 минут любое слово в канале привязывает его
CHAN_EVENTS = deque(maxlen=15)   # что пришло из каналов MAX и что бот сделал — для /channels (диагностика)


def chan_event(cid, who, result, title=""):
    CHAN_EVENTS.append(f"{time.strftime('%d.%m %H:%M')} канал {title or ''} ({cid}), автор {who or 'неизвестен'}: {result}")


def waiting_theme(cid):
    """Как раньше: тема ждёт канал (дали ссылку, а бот его не нашёл) — пост в канале привязывает его.
    Ждёт одна тема — она; ждут несколько — та, чей владелец есть в админах канала."""
    now = time.time()
    ws = [x for x in THEMES if not x.chat_id and now - float(x.own.get("bind_wait") or 0) < BIND_WAIT_SEC]
    if len(ws) == 1:
        return ws[0]
    mine = [x for x in ws if cabinet_admin_of(x.owner, cid)]
    return mine[0] if len(mine) == 1 else None


def channels_report():
    """/channels (админу): каналы MAX, где бот, к чему привязаны, кто ждёт канал и последние события из каналов."""
    taken = {t.chat_id: t for t in THEMES if t.chat_id}
    lines = ["📢 Каналы MAX, где бот:"]
    for c in bot_channels()[:30]:
        t = taken.get(c["chat_id"])
        owner = ((TENANTS.get(t.owner) or {}).get("name") or t.owner or "—") if t else ""
        lines.append(f"• {c['title']} ({c['chat_id']}) {c['link'] or 'без ссылки'} — "
                     + (f"привязан к «{t.name}» ({owner})" if t else "свободен"))
    if len(lines) == 1:
        lines.append("— ни одного: MAX не вернул боту каналы (бот не админ ни в одном канале?)")
    now = time.time()
    ws = [t for t in THEMES if not t.chat_id and now - float(t.own.get("bind_wait") or 0) < BIND_WAIT_SEC]
    lines += ["", "⏳ Ждут канал (дали ссылку):"] + ([f"• «{t.name}» — {t.link or 'без ссылки'}" for t in ws] or ["— никто"])
    lines += ["", "🕓 Последние события из каналов:"] + (list(CHAN_EVENTS)[::-1] or
                                                        ["— пока ничего: бот не получал сообщений из каналов"])
    lines += ["", "📊 События от MAX с запуска: " + (", ".join(f"{k}={v}" for k, v in UPD_STATS.most_common(8)) or "—"),
              "", "💡 MAX с июня 2026 не отдаёт боту список каналов. Канал бот узнаёт, когда его добавляют: "
              "убери бота из админов канала и добавь снова — канал появится здесь и привяжется. Или пришли боту "
              "ID канала: адрес канала в web.max.ru (web.max.ru/-123…)."]
    return "\n".join(lines)[:3900]


CHAN_HINTS = {}          # (chat_id канала, о чём сказали) -> когда: подсказки владельцу — не чаще раза в час


def chan_hint_due(cid, kind, now=None):
    now = now or time.time()
    if now - CHAN_HINTS.get((cid, kind), 0) < 3600:
        return False
    CHAN_HINTS[(cid, kind)] = now
    return True


def pick_channels(uid):
    """Каналы, которые пользователь может привязать: бот там есть; (в host-режиме у покупателя) он сам админ
    этого канала — чужой канал не привязать. У каждого c["theme"] — тема, к которой канал привязан сейчас (или None):
    такой канал можно перепривязать (раньше он прятался, и закрытый канал «застревал» в чужой/пустой теме)."""
    taken = {t.chat_id: t for t in THEMES if t.chat_id}
    chans = bot_channels()
    for c in chans:
        c["theme"] = taken.get(c["chat_id"])
    if not HOST_MODE or is_host(uid):
        return chans
    key = tenant_key(uid)
    mine = {str(m) for m in (TENANTS.get(key) or {}).get("admins") or []} | {str(uid), str(key)}
    return [c for c in chans if (KNOWN_CHATS.get(str(c["chat_id"])) or {}).get("added_by") in mine
            or cabinet_admin_of(key, c["chat_id"], [uid])]


def channel_test(chat_id):
    """Пробуем по-настоящему опубликовать в канал проверочное сообщение (и сразу удаляем). Текст — что вышло."""
    if not chat_id:
        return "Канал MAX ещё не привязан — сначала выбери его."
    try:
        r = S.post(f"{MAX_API}/messages", params={"chat_id": chat_id}, headers=HEADERS, timeout=30,
                   json={"text": "✅ Проверка: бот может публиковать в этот канал. (сообщение сейчас удалится)"})
    except Exception as e:
        return f"⚠️ Нет связи с MAX ({type(e).__name__}). Попробуй через минуту."
    if r.status_code == 200:
        with contextlib.suppress(Exception):
            delete_msg(((r.json().get("message") or {}).get("body") or {}).get("mid"))
        return "✅ Проверка пройдена: бот может публиковать в этот канал. Посты будут выходить."
    why = r.text[:200]
    rights = bot_rights(chat_id)
    if r.status_code == 403 or rights is False:
        return ("⛔ MAX не разрешает боту публиковать в этот канал.\n\nЧто сделать:\n"
                "1. Открой канал в MAX → ⋮ → Управление → Администраторы.\n"
                "2. Если бота там нет — «Добавить администратора» → найди бота.\n"
                "3. У бота включи право «Публиковать сообщения» (или «Писать сообщения») и сохрани.\n"
                "4. Вернись и нажми «🩺 Проверить публикацию» ещё раз.\n\n"
                f"Ответ MAX: {r.status_code} {why}")
    if r.status_code == 404:
        return ("⛔ MAX не видит этот канал у бота — похоже, бота убрали из канала. Добавь бота администратором "
                f"заново и выбери канал ещё раз.\n\nОтвет MAX: {why}")
    return f"⚠️ MAX не принял проверочное сообщение: {r.status_code} {why}"


def _norm_link(link):
    return re.sub(r"^https?://", "", (link or "").strip().lower()).rstrip("/").replace("www.", "")


def link_slug(link):
    """«https://max.ru/Reloader_NEWS/», «max.ru/reloader_news», «@Reloader_NEWS» -> «reloader_news»."""
    t = _norm_link(link).lstrip("@")
    return t.split("/")[-1] if t else ""


def resolve_chat_id(link):
    """id канала MAX по ссылке, @имени или имени. Поиска чата по ссылке в MAX Bot API нет (чат — только по числовому
    id), поэтому ищем среди чатов, где состоит бот (GET /chats): канал или группа, ссылка в любом виде, регистр не
    важен; ссылки у чата нет — по названию (если такое одно). None — бот такого чата не видит."""
    m = re.search(r"(?:^|/)(-?\d{4,})/?$", (link or "").strip())
    if m:                                             # ID канала: web.max.ru/-123… или просто число
        cid = int(m.group(1))
        info = chat_info(cid)
        if info.get("chat_id") or info.get("type"):
            remember_chat(cid)
            return cid
        return None
    want = link_slug(link)
    if not want:
        return None
    chats = [c for c in max_chats() if c.get("status") in (None, "active")]
    seen = {c["chat_id"] for c in chats}
    chats += [c for c in known_chats(("channel", "chat")) if c["chat_id"] not in seen]   # MAX не перечисляет — свои
    hit = next((c["chat_id"] for c in chats if c.get("link") and link_slug(c["link"]) == want), None)
    if hit is None:
        bare = lambda x: re.sub(r"[\W_]+", "", (x or "").lower())
        same = [c["chat_id"] for c in chats if bare(c.get("title")) == bare(want)]
        hit = same[0] if len(same) == 1 else None
    if hit is None:                                   # старый поиск по ссылке (из документации MAX убран) — вдруг
        with contextlib.suppress(Exception):
            r = S.get(f"{MAX_API}/chats/{want}", headers=HEADERS, timeout=20)
            j = r.json() if r.status_code == 200 else {}
            if isinstance(j.get("chat_id"), int) and j.get("type") in (None, "channel", "chat"):
                hit = j["chat_id"]
                remember_chat(hit)
    return hit


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
    return _upload_big(path, "video", "video/mp4")


FILE_TYPES = {".pdf": "application/pdf", ".zip": "application/zip", ".rar": "application/vnd.rar",
              ".7z": "application/x-7z-compressed", ".apk": "application/vnd.android.package-archive",
              ".apks": "application/vnd.android.package-archive", ".xapk": "application/vnd.android.package-archive",
              ".obb": "application/octet-stream", ".mcaddon": "application/octet-stream",
              ".mcpack": "application/octet-stream", ".mcworld": "application/octet-stream",
              ".mctemplate": "application/octet-stream", ".mclevel": "application/octet-stream",
              ".litemod": "application/octet-stream", ".mcstructure": "application/octet-stream"}
STREAM_FROM = 40 * 1024 * 1024           # файлы больше — грузим потоком, не читая целиком в память

# Без аккаунта Telegram (API) бот не читает файлы и закрытые каналы — t.me их не отдаёт. Этой же фразой
# объясняем пропуск файла в журнале: пост не исчезает молча, всегда видно точную причину.
FILE_NEEDS_API = "Не удалось скачать файл: нужен аккаунт Telegram (API) для чтения закрытых каналов"


def file_mime(path):
    """Тип файла по расширению: свои — для архивов, APK и файлов игр (у Python они есть не во всех версиях)."""
    import mimetypes
    ext = os.path.splitext(str(path))[1].lower()
    return FILE_TYPES.get(ext) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def content_disposition(name):
    """Заголовок с именем файла: в HTTP-заголовке можно только латиницу — русское имя кладём в filename* (UTF-8)."""
    from urllib.parse import quote
    plain = re.sub(r"[^\x20-\x7e]", "_", name).replace('"', "'")
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}"


def upload_file(path):
    """Документ (PDF, архив, APK, музыка, голосовое…) — в MAX уходит вложением «файл» со своим именем и типом."""
    return _upload_big(path, "file", file_mime(path))


def _upload_big(path, kind, mime):
    """У видео токен выдаётся сразу вместе со ссылкой для загрузки, у файла — в ответе на загрузку.
    Большие файлы шлём потоком (а не multipart): multipart читает весь файл в память — на телефоне падает."""
    what = "видео" if kind == "video" else "файла"
    r = S.post(f"{MAX_API}/uploads", params={"type": kind},
               headers=HEADERS, timeout=30)
    r.raise_for_status()
    info = r.json()
    url, name = info["url"], os.path.basename(path)
    size = os.path.getsize(path)

    def multipart():
        with open(path, "rb") as f:
            return S.post(url, files={"data": (name, f, mime)}, timeout=900)

    def stream():
        headers = {
            "Content-Disposition": content_disposition(name),
            "Content-Type": mime,
            "Content-Range": f"bytes 0-{size - 1}/{size}",
            "Connection": "keep-alive",
        }
        with open(path, "rb") as f:
            return S.post(url, data=f, headers=headers, timeout=900)

    order = (stream, multipart) if size > STREAM_FROM else (multipart, stream)
    last, status = None, None
    for attempt in order:
        try:
            up = attempt()
        except Exception as e:
            print(f"  загрузка {what} ({attempt.__name__}) не прошла: {e}")
            last, status = f"{type(e).__name__}: {e}"[:200], None
            continue
        if up.status_code < 300:
            token = info.get("token")
            if not token:
                with contextlib.suppress(ValueError, AttributeError):
                    token = up.json().get("token")
            if token:
                return token
            last = f"MAX не выдал токен: {up.text[:150]}"
            continue
        last, status = f"{up.status_code} {up.text[:150]}", up.status_code
        print(f"  загрузка {what} ({attempt.__name__}) не прошла: {last}")
    raise RuntimeError(f"MAX upload failed with HTTP {status}: {last[4:]}" if status else f"MAX upload failed: {last}")


def _lost_attachments(r, chat_id, mid, expected):
    """MAX мог принять сообщение, но потерять вложение. Ответ на отправку показал меньше вложений — переспрашиваем
    MAX само сообщение: потеря подтвердилась — (осталось, отправили); всё на месте — None."""
    if not expected:
        return None
    try:
        got = len(((r.json().get("message") or {}).get("body") or {}).get("attachments") or [])
    except Exception:
        return None
    if got >= expected:
        return None
    if mid:
        with contextlib.suppress(Exception):
            rr = S.get(f"{MAX_API}/messages", params={"chat_id": chat_id, "message_ids": mid}, headers=HEADERS,
                       timeout=20)
            msgs = rr.json().get("messages") if rr.status_code == 200 else None
            body = ((msgs or [{}])[0] or {}).get("body") or {}
            if "attachments" in body:
                got = len(body.get("attachments") or [])
    return (got, expected) if got < expected else None


def send_max(chat_id, text, image_paths, video_path=None, file_path=None):
    attachments = []
    for p in image_paths:                 # путь к файлу или {"token": ...} (уже загружено в MAX)
        token = p["token"] if isinstance(p, dict) else upload_image(p)
        attachments.append({"type": "image", "payload": {"token": token}})
    if video_path:
        token = video_path["token"] if isinstance(video_path, dict) else upload_video(video_path)
        attachments.append({"type": "video", "payload": {"token": token}})
    if file_path:                         # путь к файлу или {"token": ..., "kind": "file"/"audio"} (уже в MAX)
        if isinstance(file_path, dict):
            attachments.append({"type": file_path.get("kind") or "file", "payload": {"token": file_path["token"]}})
        else:
            attachments.append({"type": "file", "payload": {"token": upload_file(file_path)}})
    body = {"format": "markdown"}
    if text:
        body["text"] = text
    if attachments:
        body["attachments"] = attachments
    tries = 100 if video_path or file_path else 8      # видео и файлы MAX обрабатывает дольше (до ~5 минут)
    for _ in range(tries):
        try:
            r = S.post(f"{MAX_API}/messages", params={"chat_id": chat_id},
                       headers=HEADERS, json=body, timeout=60)
        except requests.exceptions.ReadTimeout:
            # MAX мог принять сообщение, но не успел ответить — повтор дал бы дубль
            print("  MAX не ответил вовремя, считаю сообщение отправленным")
            return
        if r.status_code == 200:
            mid = None
            with contextlib.suppress(Exception):
                mid = ((r.json().get("message") or {}).get("body") or {}).get("mid")
            lost = _lost_attachments(r, chat_id, mid, len(attachments))
            if lost:                                 # сообщение без файла в канале не оставляем — повторит вызывающий
                if mid:
                    with contextlib.suppress(Exception):
                        delete_msg(mid)
                raise AttachmentsLost(f"MAX dropped the attachment ({lost[0]} of {lost[1]} kept)")
            return mid
        if "not.ready" in r.text or "not.processed" in r.text:
            time.sleep(3)
            continue
        if r.status_code == 403 or "chat.denied" in r.text or "chat.not.found" in r.text:
            raise ChatDenied(f"MAX ошибка {r.status_code}: {r.text[:200]}")
        if r.status_code == 429 or r.status_code >= 500:
            raise MaxTemporary(f"MAX send failed with HTTP {r.status_code}: {r.text[:150]}")
        if r.status_code == 400 and body.get("format") and re.search(r"format|markdown|pars", r.text, re.I):
            body.pop("format")                       # MAX не понял разметку — шлём тем же текстом без неё
            if text:
                body["text"] = re.sub(r"\*\*|__|~~|`", "", text)
            print("  MAX не принял разметку — отправляю обычным текстом")
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


def build_parts(text, images, videos, files=()):
    """Текст уходит с первым вложением; фото — одним сообщением, видео и файлы — по одному.
    Части: (текст, фото, видео, файл)."""
    chunks = split_text(text)
    text, extra = chunks[0], chunks[1:]
    parts = []
    for k in range(0, len(images), 10):      # в одном сообщении MAX — до 10 вложений
        parts.append((text, images[k:k + 10], None, None))
        text = ""
    for v in videos:
        parts.append((text, [], v, None))
        text = ""
    for fl in files:
        parts.append((text, [], None, fl))
        text = ""
    if not parts:
        parts.append((text, [], None, None))
    parts += [(c, [], None, None) for c in extra]  # хвост длинного текста — отдельными сообщениями
    return parts


def send_part(chat_id, t, imgs, v, fl=None):
    if fl is None:
        return send_max(chat_id, t, imgs, v)
    return send_max(chat_id, t, imgs, v, fl)


def send_post(chat_id, text, images, videos, files=()):
    for t, imgs, v, fl in build_parts(text, images, videos, files):
        send_part(chat_id, t, imgs, v, fl)


class MaxTemporary(RuntimeError):
    """MAX временно не принимает (перегружен, 5xx, 429) — пробовать позже, пост не выбрасывать."""


class AttachmentsLost(MaxTemporary):
    """MAX принял сообщение, но без вложения (сообщение уже убрано из канала) — отправить ещё раз."""


MAX_RETRY_PAUSES = [2, 5, 10]   # повторы загрузки файла и отправки части поста в MAX (сек): 3 раза, коротко, молча


def why(e):
    """Точная причина сбоя — для журнала админа (покупателю её не показываем)."""
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code}"
    if isinstance(e, (asyncio.TimeoutError, requests.Timeout)):
        return f"timed out ({type(e).__name__})"
    return f"{type(e).__name__}: {e}"[:220]


def upload_why(e):
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"MAX upload failed with HTTP {e.response.status_code}"
    msg = str(e)
    return msg[:220] if msg.startswith("MAX upload failed") else f"MAX upload failed: {why(e)}"


def _size(n):
    return f"{n / 1048576:.1f} МБ" if n >= 1048576 else f"{max(1, n // 1024)} КБ"


def upload_retry(kind, path, what):
    """Файл -> токен MAX. Сбой (HTTP 5xx/429, обрыв, таймаут, MAX не выдал токен) — до 3 повторов с короткой паузой.
    Каждая попытка — в bot.log; не вышло — MediaUploadFailed с точной причиной."""
    fn = {"image": upload_image, "video": upload_video, "file": upload_file}[kind]
    size = os.path.getsize(path) if path and os.path.exists(path) else 0
    attempts, err = len(MAX_RETRY_PAUSES) + 1, "?"
    for n in range(attempts):
        t0 = time.monotonic()
        try:
            tok = fn(path)
            print(f"📎 {what}: загружено в MAX ({_size(size)}, {time.monotonic() - t0:.1f} с"
                  + (f", с {n + 1}-й попытки" if n else "") + ")")
            return tok
        except Exception as e:
            err = upload_why(e)
            code = e.response.status_code if isinstance(e, requests.HTTPError) and e.response is not None else 0
            last = n == attempts - 1 or (400 <= code < 500 and code not in (408, 429))   # 4xx — повтор не поможет
            print(f"📎 {what}: загрузка в MAX не прошла (попытка {n + 1}/{attempts}): {err}"
                  + ("" if last else f" — повтор через {MAX_RETRY_PAUSES[n]} с"))
            if last:
                raise MediaUploadFailed(f"upload failed: {what.split(': ', 1)[-1]} — {err} ({n + 1} attempts)") from e
            time.sleep(MAX_RETRY_PAUSES[n])


def is_temporary(e):
    if isinstance(e, requests.HTTPError) and e.response is not None:     # загрузка файла в MAX: 5xx / 429
        return e.response.status_code >= 500 or e.response.status_code == 429
    return isinstance(e, MaxTemporary) or is_net_error(e)


def retry_later(it, e, what):
    """Временный сбой: пост остаётся, повтор с растущей паузой (1, 2, 4… до 15 минут)."""
    n = it.get("tfails", 0) + 1
    it["tfails"] = n
    it["retry_at"] = time.time() + min(900, 60 * 2 ** min(n - 1, 4))
    if n in (1, 5) or n % 20 == 0:
        print(f"{what}: временный сбой ({type(e).__name__}: {str(e)[:120]}) — повторю позже, пост сохранён")


class ChatDenied(RuntimeError):
    """MAX не пускает бота в канал (бот не админ / нет права публиковать / канал удалён)."""


DENIED = {}            # chat_id -> до какого времени не пытаться публиковать (нет прав)
DENIED_TOLD = {}       # chat_id -> когда последний раз говорили админам
DENY_N = {}            # chat_id -> сколько отказов подряд


def bot_rights(chat_id):
    """Что MAX говорит о боте в канале: True — админ и может писать, False — не админ, None — не узнать."""
    try:
        r = S.get(f"{MAX_API}/chats/{chat_id}/members/me", headers=HEADERS, timeout=20)
        if r.status_code in (403, 404):
            return False
        if r.status_code != 200:
            return None
        j = r.json()
    except Exception:
        return None
    if not (j.get("is_admin") or j.get("is_owner")):
        return False
    perms = j.get("permissions")
    return True if not perms or any(p in perms for p in ("write", "post_edit_delete_message")) else None


def note_denied(chat_id, e):
    """MAX не принял пост в канал. Посты НЕ удаляем. MAX иногда отказывает случайно, поэтому админам пишем,
    только если отказ повторился и MAX сам не подтверждает, что бот — админ канала (не чаще раза в час)."""
    now = time.time()
    n = DENY_N[chat_id] = DENY_N.get(chat_id, 0) + 1
    rights = bot_rights(chat_id)
    if n == 1 or (rights is True and n < 8):
        wait = 120 if n == 1 else 300
        DENIED[chat_id] = now + wait
        print(f"Канал MAX {chat_id}: MAX не принял пост ({e}); бот админ канала: "
              f"{ {True: 'да', False: 'нет'}.get(rights, 'не узнать') } — похоже на сбой, повторю через {wait // 60} мин")
        return
    DENIED[chat_id] = now + 600
    print(f"Канал MAX {chat_id}: нет прав на публикацию ({e}) — посты сохранены, повторю через 10 минут")
    if now - DENIED_TOLD.get(chat_id, 0) > 3600:
        DENIED_TOLD[chat_id] = now
        if rights is True:
            notify("⚠️ MAX уже полчаса не принимает посты в канал этой темы, хотя бот в нём админ. Похоже на сбой "
                   "MAX — посты не удаляю, пробую каждые 10 минут. Если не пройдёт само: в канале MAX сними бота "
                   "из админов и добавь заново с правом публиковать.")
            return
        notify("⛔ Не могу публиковать в канал этой темы: MAX ответил «нет прав».\n\n"
               "Что сделать: зайди в канал MAX → Админы → добавь бота админом с правом публиковать сообщения. "
               "Если тема привязана не к тому каналу — «🎭 Мои каналы» → тема → «📢 Канал MAX».\n\n"
               "Посты не удаляю — выложу, как только появится доступ (проверяю раз в 10 минут).")


def note_allowed(chat_id):
    DENY_N.pop(chat_id, None)
    if DENIED.pop(chat_id, None):
        DENIED_TOLD.pop(chat_id, None)
        print(f"Канал MAX {chat_id}: доступ есть, публикую ✅")


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
            f"Опубликовано: {d('published')}\n"
            f"Дублей пропущено: {d('dups')}\n"
            f"Рекламы отсеяно: {d('ads')}\n"
            f"Отсеяно стоп-словами: {d('blocked')}\n"
            f"Отклонено вручную: {d('rejected')}\n"
            f"Ошибок: {d('errors')}\n\n"
            f"Сейчас в очереди: {len(QUEUE)} (ждут одобрения: {pend})")


def pin_post(chat, mid):
    """📌 Закрепить пост в канале (без уведомления подписчиков)."""
    try:
        r = S.put(f"{MAX_API}/chats/{chat}/pin", headers=HEADERS, timeout=20, json={"message_id": mid, "notify": False})
        if r.status_code != 200:
            journal(f"📌 Не получилось закрепить пост: MAX {r.status_code} — дай боту право «Закреплять сообщения»")
    except Exception as e:
        print("Закреп:", e)


def post_extras(mids):
    """После публикации: 📌 закрепить пост и/или 🗑 поставить его на автоудаление (бесплатно, во всех тарифах)."""
    mids = [m for m in (mids or []) if m]
    chat = cur().chat_id
    if not mids or not chat:
        return
    if CFG.get("pin_new"):
        in_background(pin_post, chat, mids[0], name="pin")    # из публикации (корутина) — не держим цикл
    h = int(CFG.get("autodel_h") or 0)
    if h > 0:
        lst = list(PUB.get("autodel") or [])
        lst.append({"mids": mids, "at": time.time() + h * 3600})
        PUB["autodel"] = lst
        save_pub()


def channel_autodel_due(now=None):
    """🗑 Посты, у которых вышел срок, удаляем из канала (по всем темам)."""
    now = now or time.time()
    for t in list(THEMES):
        with theme_ctx(t):
            lst = list(PUB.get("autodel") or [])
            due = [x for x in lst if x["at"] <= now]
            if not due:
                continue
            for x in due:
                for m in x["mids"]:
                    with contextlib.suppress(Exception):
                        delete_msg(m)
            PUB["autodel"] = [x for x in lst if x["at"] > now]
            save_pub()
            journal(f"🗑 Автоудаление: убрал из канала постов — {len(due)}")


def record_pub(mid, ch, text, raw=None, buttons=0, mids=None):
    """Что вышло в канал: для отчёта за день (просмотры, лучший пост, источники). Храним неделю.
    Полный текст — только у последних 30 (для кнопки «🚫 Реклама» в обучении фильтра)."""
    now = time.time()
    posts = [p for p in PUB.get("day_posts", []) if now - p["t"] < 8 * 86400]
    posts.append({"mid": mid, "t": now, "ch": ch, "text": plain_snip(text, 90),
                  "raw": (raw if raw is not None else text or "")[:1500], "buttons": buttons})
    for p in posts[:-30]:
        p.pop("raw", None)
    PUB["day_posts"] = posts
    save_pub()
    with contextlib.suppress(Exception):
        post_extras(mids or [mid])


def ask_ad(item):
    """Спорный пост — владельцу на решение: реклама или нет (ответ учит фильтр)."""
    card = dict(item, text=f"🤔 Может быть рекламой ({item.get('why') or 'есть рекламные признаки'}).\n"
                           f"Это реклама?\n\n{item.get('text') or ''}")
    send_preview(card, [[B("✅ Не реклама — выложить", f"ap:adok:{item['id']}")],
                        [B("🚫 Реклама — не выкладывать", f"ap:adad:{item['id']}")]])


def hold_ad(item):
    """Отсеянная реклама ждёт 2 суток: вдруг это не реклама — тогда выложим кнопкой."""
    now = time.time()
    keep = []
    for x in list(ADHELD) + [item]:
        if now - x["enq"] < 2 * 86400:
            keep.append(x)
        else:
            drop_files(x)
    while len(keep) > 15:
        drop_files(keep.pop(0))
    ADHELD[:] = keep
    save_adheld()


def drop_files(it):
    for p in it.get("photos", []):
        path = p.get("path") if isinstance(p, dict) else p
        if path:
            with contextlib.suppress(OSError):
                os.remove(path)


def fetch_views(chat_id, mids):
    """Просмотры постов канала MAX: {mid: просмотры}. Чего MAX не отдал — того нет в ответе."""
    out = {}
    mids = [m for m in mids if m]
    for k in range(0, len(mids), 100):
        try:
            r = S.get(f"{MAX_API}/messages", params={"chat_id": chat_id, "message_ids": ",".join(mids[k:k + 100])},
                      headers=HEADERS, timeout=30)
            if r.status_code != 200:
                continue
            for m in r.json().get("messages") or []:
                mid = (m.get("body") or {}).get("mid")
                views = (m.get("stat") or {}).get("views")
                if mid and isinstance(views, int):
                    out[mid] = views
        except Exception as e:
            print("Отчёт: просмотры не получены:", e)
    return out


def day_stats(t, now=None):
    """Итоги темы за последние сутки."""
    now = now or time.time()
    base = PUB.get("rep_base", {})
    posts = [p for p in PUB.get("day_posts", []) if now - p["t"] < 86400]
    views = fetch_views(t.chat_id, [p["mid"] for p in posts]) if t.chat_id and posts else {}
    for p in posts:
        p["views"] = views.get(p["mid"])
    seen = [p for p in posts if p["views"] is not None]
    hours = [0] * 24
    for p in posts:
        hours[time.localtime(p["t"]).tm_hour] += 1
    return {"name": t.name, "link": t.link or "", "posts": len(posts),
            "views": sum(p["views"] for p in seen) if seen else None,
            "avg": round(sum(p["views"] for p in seen) / len(seen)) if seen else None,
            "top": max(seen, key=lambda p: p["views"]) if seen else None,
            "sources": Counter(p["ch"] for p in posts).most_common(3),
            "hours": hours, "dups": STATS.get("dups", 0) - base.get("dups", 0),
            "errors": STATS.get("errors", 0) - base.get("errors", 0),
            "ads": STATS.get("ads", 0) - base.get("ads", 0)}


_FONT_R = (
    "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"
)

_FONT_B = (
    "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"
    "3u2Wyr3bzfP5f+9UHpw="
)


def _font(bold, size):
    import io
    from PIL import ImageFont
    import base64
    raw = zlib_decompress(base64.b64decode("".join(_FONT_B if bold else _FONT_R)))
    return ImageFont.truetype(io.BytesIO(raw), size)


def zlib_decompress(b):
    import zlib
    return zlib.decompress(b)


def fmt_num(n):
    if n is None:
        return "—"
    return f"{n / 1000:.1f}K".replace(".0K", "K") if n >= 10000 else f"{n:,}".replace(",", " ")


REPORT_PER_IMAGE = 3    # тем на одной картинке отчёта: больше — следующая картинка


def report_image(stats, path, all_stats=None, page=""):
    """Отчёт за день картинкой: по карточке на тему — посты, просмотры, график по часам, лучший пост, источники.
    Шрифт Inter (SIL Open Font License 1.1) встроен в бота: на сервере шрифтов может не быть."""
    from PIL import Image, ImageDraw
    W, pad, card_h = 1080, 40, 640
    H = 250 + len(stats) * (card_h + 30) + 60
    bg, card, line = (13, 16, 30), (24, 29, 52), (40, 47, 80)
    white, muted, accent, blue, green = (240, 243, 255), (150, 160, 195), (124, 92, 255), (42, 163, 239), (61, 220, 151)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    f = {k: _font(b, sz) for k, (b, sz) in {"h1": (1, 64), "h2": (1, 42), "num": (1, 54), "b28": (1, 28),
                                            "r28": (0, 28), "r24": (0, 24), "r20": (0, 20)}.items()}
    for i in range(0, 220):                                # шапка с мягким градиентом
        c = tuple(int(bg[k] + (accent[k] - bg[k]) * (1 - i / 220) * 0.35) for k in range(3))
        d.line([(0, i), (W, i)], fill=c)
    d.text((pad, 50), "Итоги дня", font=f["h1"], fill=white)
    if page:
        d.text((W - pad, 72), page, font=f["r28"], fill=muted, anchor="ra")
    tot = all_stats or stats                                # в шапке — итог по всем темам, на каждой картинке
    total_p = sum(s["posts"] for s in tot)
    total_v = sum(s["views"] or 0 for s in tot)
    d.text((pad, 135), time.strftime("%d.%m.%Y", time.localtime()) + f"  ·  постов: {total_p}"
           + (f"  ·  просмотров: {fmt_num(total_v)}" if any(s["views"] is not None for s in tot) else ""),
           font=f["r28"], fill=muted)

    def wrap(text, font, width, lines=2):
        text = "".join(ch for ch in (text or "") if ord(ch) < 0x2000 or ch in "–—•…₽№→↑↓▲▼")   # эмодзи шрифт не знает
        words, out, cur_ = text.split(), [], ""
        for w in words:
            t = (cur_ + " " + w).strip()
            if d.textlength(t, font=font) <= width:
                cur_ = t
            else:
                out.append(cur_)
                cur_ = w
                if len(out) == lines:
                    break
        if len(out) < lines and cur_:
            out.append(cur_)
        if len(out) == lines and " ".join(out) != " ".join(words):
            while out[-1] and d.textlength(out[-1] + "…", font=font) > width:
                out[-1] = out[-1][:-1]
            out[-1] += "…"
        return out

    y = 250
    for s in stats:
        x0, x1 = pad, W - pad
        d.rounded_rectangle([x0, y, x1, y + card_h], radius=28, fill=card)
        d.text((x0 + 32, y + 28), s["name"][:30], font=f["h2"], fill=white)
        if s["link"]:
            d.text((x0 + 32, y + 82), s["link"].replace("https://", "")[:50], font=f["r20"], fill=muted)
        tiles = [("постов", str(s["posts"]), white), ("просмотров", fmt_num(s["views"]), blue),
                 ("в среднем", fmt_num(s["avg"]), green)]
        tw = (x1 - x0 - 64 - 2 * 20) // 3
        for k, (label, val, col) in enumerate(tiles):
            tx = x0 + 32 + k * (tw + 20)
            d.rounded_rectangle([tx, y + 125, tx + tw, y + 245], radius=20, fill=line)
            d.text((tx + 22, y + 138), val, font=f["num"], fill=col)
            d.text((tx + 22, y + 205), label, font=f["r20"], fill=muted)
        gx, gy, gw, gh = x0 + 32, y + 285, x1 - x0 - 64, 90                  # посты по часам
        mx = max(s["hours"]) or 1
        bw = gw / 24
        for h, n in enumerate(s["hours"]):
            bh = 4 + int((gh - 4) * n / mx) if n else 4
            d.rounded_rectangle([gx + h * bw + 3, gy + gh - bh, gx + (h + 1) * bw - 3, gy + gh], radius=4,
                                fill=accent if n else line)
        for h in (0, 6, 12, 18, 23):
            d.text((gx + h * bw + 2, gy + gh + 6), f"{h:02d}", font=f["r20"], fill=muted)
        ty = y + 420
        d.text((x0 + 32, ty), "Лучший пост", font=f["b28"], fill=white)
        top = s["top"]
        if top:
            d.text((x1 - 32, ty), f"{fmt_num(top['views'])} просмотров", font=f["b28"], fill=blue, anchor="ra")
            for k, ln in enumerate(wrap(top["text"], f["r24"], x1 - x0 - 64) or ["(без текста)"]):
                d.text((x0 + 32, ty + 42 + k * 32), ln, font=f["r24"], fill=muted)
        else:
            d.text((x0 + 32, ty + 42), "просмотры появятся, когда посты наберут их в MAX" if s["posts"] else
                   "за сутки постов не было", font=f["r24"], fill=muted)
        src = "  ·  ".join(f"@{c} — {n}" for c, n in s["sources"]) or "—"
        foot = f"Источники: {src}"
        d.text((x0 + 32, ty + 125), wrap(foot, f["r20"], x1 - x0 - 64, 1)[0] if foot else "", font=f["r20"], fill=muted)
        d.text((x0 + 32, ty + 160), f"Повторов отсеяно: {s['dups']}  ·  рекламы: {s.get('ads', 0)}  ·  "
               f"ошибок: {s['errors']}", font=f["r20"], fill=muted)
        y += card_h + 30
    d.text((W // 2, H - 45), "TG → MAX", font=f["r20"], fill=line, anchor="mm")
    img.save(path, "PNG", optimize=True)
    return path


def send_report():
    """Отчёт за день. В host-режиме — каждому кабинету свой отчёт в его чат."""
    if HOST_MODE:
        for key in list(TENANTS):
            if not tenant_live(key):
                continue
            ts = [t for t in tenant_themes(key) if theme_live(t)]
            chats = set((TENANTS[key].get("chats") or {}).values())
            if ts and chats:
                with contextlib.suppress(Exception):
                    _report_group(ts, chats)
        META["last_report"] = time.strftime("%Y-%m-%d", time.localtime(time.time()))
        save_cfg()
        return
    _report_group(list(THEMES), set(CFG["admin_chats"].values()), use_notify=True)
    META["last_report"] = time.strftime("%Y-%m-%d", time.localtime(time.time()))
    save_cfg()


def _report_group(themes, chats, use_notify=False):
    parts, stats = [], []
    for t in themes:
        with theme_ctx(t):
            parts.append(make_report(t.name if len(themes) > 1 else None))
            with contextlib.suppress(Exception):
                stats.append(day_stats(t))
            PUB["rep_base"] = dict(STATS)
            save_pub()
    tokens = []
    chunks = [stats[k:k + REPORT_PER_IMAGE] for k in range(0, len(stats), REPORT_PER_IMAGE)]
    try:
        for n, chunk in enumerate(chunks):                 # по 3 темы на картинку — крупно и читаемо
            page = f"{n + 1}/{len(chunks)}" if len(chunks) > 1 else ""
            path = report_image(chunk, f"report_day_{n}.png", stats, page)
            tokens.append(upload_image(path))
            with contextlib.suppress(OSError):
                os.remove(path)
    except ImportError:
        print("Отчёт: нет Pillow — шлю текстом (добавь Pillow в requirements.txt)")
        tokens = []
    except Exception as e:
        print("Отчёт: картинка не получилась:", e)
        tokens = []
    if tokens:
        tv = sum(s["views"] or 0 for s in stats)
        cap = (f"📊 Итоги дня: постов {sum(s['posts'] for s in stats)}"
               + (f", просмотров {fmt_num(tv)}" if any(s["views"] is not None for s in stats) else ""))
        for chat in chats:
            for n, tok in enumerate(tokens):
                with contextlib.suppress(Exception):
                    api_send(chat, cap if n == 0 else f"📊 Итоги дня — {n + 1}/{len(tokens)}", None, tok)
        return
    if use_notify:
        notify("\n\n———\n\n".join(parts), tag=False)
        return
    for chat in chats:
        with contextlib.suppress(Exception):
            api_send(chat, "\n\n———\n\n".join(parts))


# ---------- МЕНЮ В MAX ----------
PENDING = {}           # user_id -> действие, для которого ждём текст
INVITES = {}           # код приглашения админа -> время окончания
LAST_INVITE = {"code": None}
BIND = {"theme": None}      # тема, которая ждёт привязки канала MAX (первое сообщение в канале)
BIND_HINT = ("Чтобы привязать канал, добавьте бота в администраторы и напишите любое сообщение в вашем канале, "
             "который хотите привязать (после привязки его можно удалить).")   # канал по ссылке не нашёлся
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


HOST_CODE = {"code": ""}   # длинный секретный код: кто пришлёт его боту — станет ХОЗЯИНОМ (host-режим)


def _gen_host_code():
    return "".join(random.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(12))   # 12 знаков, не угадать


def load_host_code():
    """Код хозяина: один и тот же между перезапусками (host_code.txt или env HOST_CODE), пока им не воспользуются."""
    code = (os.environ.get("HOST_CODE") or "").strip()
    if not code:
        try:
            with open("host_code.txt") as f:
                code = f.read().strip()
        except OSError:
            code = ""
        if len(code) < 8:
            code = _gen_host_code()
            with contextlib.suppress(OSError), open("host_code.txt", "w") as f:
                f.write(code)
    HOST_CODE["code"] = code
    return code


def new_host_code():
    """Новый код хозяина после использования — старый больше не действует."""
    code = _gen_host_code()
    HOST_CODE["code"] = code
    with contextlib.suppress(OSError), open("host_code.txt", "w") as f:
        f.write(code)
    print("=" * 40)
    print("НОВЫЙ КОД ХОЗЯИНА (на будущее, никому не показывай):", code)
    print("=" * 40)


def api_send(chat_id, text, rows=None, image_token=None, keep=False):
    body = {"text": text} if text is not None else {}          # картинка без подписи — без текста
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
        mid = ((r.json().get("message") or {}).get("body") or {}).get("mid")
    except Exception:
        return None
    if not keep:                                    # keep — аналитика: остаётся в истории чата
        with contextlib.suppress(Exception):
            track_sent(chat_id, mid, text or "", rows)
    return mid


# ---------- чистый чат: сообщения бота исчезают через час, меню — одно (новое заменяет старое) ----------
AUTODEL_FILE = "autodel.json"
AUTODEL_SEC = 3600
AUTODEL_LOCK = threading.Lock()
AUTODEL = {"temp": [], "menu": {}}
AUTODEL.update(load_json(AUTODEL_FILE, {}))
KEEP_TEXT = ("📊 Итоги дня", "📈 Отчёт", "🔥 Тренд для")   # отчёты и сигналы трендов остаются в чате


def track_sent(chat_id, mid, text, rows):
    """Сообщение бота в личке — удалим через час. Карточки с решением (одобрить пост, оплата) и отчёты не трогаем:
    у карточек свой порядок, отчёт нужен для истории."""
    if not mid:
        return
    pl = json.dumps(rows or [], ensure_ascii=False)
    if '"ap:' in pl or "a:clpay" in pl or (text or "").startswith(KEEP_TEXT):
        return
    with AUTODEL_LOCK:
        AUTODEL["temp"].append({"chat": chat_id, "mid": mid, "at": time.time() + AUTODEL_SEC})
        save_json(AUTODEL_FILE, AUTODEL)


TRANSIENT = {}                 # чат -> [(mid, меню?)]: промежуточное (ошибки, «Анализирую…», меню канала)
TRANSIENT_LOCK = threading.Lock()


def send_transient(chat, text, rows=None, menu=False):
    """Промежуточное сообщение: уберём после следующего нажатия (меню канала — когда придёт аналитика)."""
    mid = api_send(chat, text, rows)
    if mid and chat is not None:
        with TRANSIENT_LOCK:
            TRANSIENT.setdefault(str(chat), []).append((mid, bool(menu)))
    return mid


def clear_transient(chat, menus=True, only=None):
    """Убрать промежуточные сообщения чата: ошибки и «Анализирую…»; menus — и меню канала; only — одно это."""
    if chat is None:
        return
    with TRANSIENT_LOCK:
        box = TRANSIENT.get(str(chat)) or []
        drop = [mid for mid, menu in box if (only is None or mid == only) and (menus or not menu)]
        if not drop:
            return
        rest = [x for x in box if x[0] not in drop]
        if rest:
            TRANSIENT[str(chat)] = rest
        else:
            TRANSIENT.pop(str(chat), None)
    with AUTODEL_LOCK:
        AUTODEL["temp"] = [x for x in AUTODEL["temp"] if x["mid"] not in drop]
        save_json(AUTODEL_FILE, AUTODEL)
    for mid in drop:
        with contextlib.suppress(Exception):
            delete_msg(mid)


def send_screen(chat_id, text, rows):
    """Меню/экран: живёт, пока не позовёшь новое меню, — тогда старое удаляется."""
    mid = api_send(chat_id, text, rows)
    if not mid:
        return mid
    old = None
    with AUTODEL_LOCK:
        AUTODEL["temp"] = [x for x in AUTODEL["temp"] if x["mid"] != mid]
        old = AUTODEL["menu"].get(str(chat_id))
        AUTODEL["menu"][str(chat_id)] = mid
        save_json(AUTODEL_FILE, AUTODEL)
    if old and old != mid:
        with contextlib.suppress(Exception):
            delete_msg(old)
    return mid


def purge_autodel(now=None):
    """Удаляет сообщения бота, которым пора (час прошёл)."""
    now = now or time.time()
    with AUTODEL_LOCK:
        due = [x for x in AUTODEL["temp"] if x["at"] <= now]
        if not due:
            return 0
        AUTODEL["temp"] = [x for x in AUTODEL["temp"] if x["at"] > now]
        save_json(AUTODEL_FILE, AUTODEL)
    for x in due:
        with contextlib.suppress(Exception):
            delete_msg(x["mid"])
    return len(due)


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


def drop_card(ref, chat):
    """Убирает одну карточку в одном чате (у владельца темы карточка остаётся)."""
    if chat is None:
        return
    with CARDS_LOCK:
        for c in CARDS:
            if c["ref"] == ref and c["chat"] == chat and c.get("del_at") is None:
                c["del_at"] = time.time()
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
    text = re.sub(r"\*\*|__|~~", "", LINK_RE.sub(r"\1", text))   # в карточке — как увидят читатели, без ** и [](…)
    if len(text) > 800:
        text = text[:800] + "…"
    media = []
    if it.get("photos"):
        media.append(f"фото: {len(it['photos'])}")
    if it.get("vids"):
        media.append(f"видео: {len(it['vids'])}")
    if it.get("docs"):
        media.append("файлы: " + ", ".join(d["name"] for d in it["docs"])[:120])
    head = f"✋ Новый пост из @{it['ch']}{theme_tag()}"
    return f"{head}\n({', '.join(media) or 'только текст'})\n\n{text}"


def card_chats():
    """Кому слать карточки и уведомления текущей темы: чаты админов её кабинета. В host-режиме хозяину —
    только по его собственным темам (чужие посты он не одобряет); теме без кабинета — никому."""
    chats = CFG["admin_chats"]
    out = set(chats.values())
    if HOST_MODE and cur().owner != host_id():
        out -= {c for u, c in chats.items() if is_host(u)} | ({HOST_CHAT.get("chat")} - {None})
    elif HOST_MODE and cur().owner and HOST_CHAT.get("chat"):
        out.add(HOST_CHAT["chat"])                        # своя тема хозяина — и в его личный чат
    return out


def send_preview(it, rows=None, chats=None):
    """Присылает админам пост на одобрение (с кнопками rows)."""
    msg = preview_text(it)
    token = None
    if it.get("photos") and os.path.exists(it["photos"][0]):
        try:
            token = upload_image(it["photos"][0])
        except Exception as e:
            print("Предпросмотр: фото не загрузилось:", e)
    for chat in (chats or card_chats()):
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
        snip = plain_snip(it.get("text"), 50)
        lines.append(f"{i + 1}. {snip}")
    text = (f"📦 Пачка из @{items[0]['ch']}{theme_tag()}: постов {len(items)}\n\n" + "\n".join(lines)
            + "\n\nОдна кнопка на всю пачку. Посты выйдут по одному, с паузой расписания:")
    rows = [[B("✅ По очереди, начиная сейчас", f"ap:gnow:{gid}")],
            [B(f"⏰ По очереди через {fmt_min(CFG['later_min'])}", f"ap:glater:{gid}")],
            [B("❌ Пропустить все", f"ap:gno:{gid}")]]
    for chat in card_chats():
        try:
            add_card(chat, api_send(chat, text, rows), gid)
        except Exception as e:
            print("Предпросмотр: ошибка:", e)


def same_uid(a, b):
    """ID в MAX иногда приходит числом, иногда строкой — сравниваем надёжно (по строке)."""
    return a is not None and b is not None and str(a) == str(b)


def is_admin(uid):
    if HOST_MODE:
        return is_host(uid) or tenant_key(uid) is not None
    return uid in CFG["admins"]


FIXED_OWNER = 0
try:
    FIXED_OWNER = int(os.environ.get("OWNER_ID") or 0)   # твой user_id в MAX: панель только у тебя, даже если код утёк
except (TypeError, ValueError):
    FIXED_OWNER = 0
HOST_OWNER = FIXED_OWNER                              # хозяин бота в host-режиме


def is_owner(uid):
    if HOST_MODE:                                   # в host-режиме «владелец» = главный в своём кабинете
        key = tenant_key(uid)
        return key is not None and same_uid((TENANTS[key].get("admins") or [None])[0], uid)
    if FIXED_OWNER:
        return same_uid(uid, FIXED_OWNER)
    return bool(CFG["admins"]) and CFG["admins"][0] == uid


def host_id():
    """ID хозяина: кого признали по коду хозяина (host_uid), иначе — OWNER_ID."""
    return str(GCFG.get("host_uid") or (HOST_OWNER if HOST_OWNER else "") or "")


def is_host(uid):
    """Хозяин бота (ты): видит панель всех кабинетов. Только по OWNER_ID или по коду хозяина (host_uid).
    Сравниваем как строки — MAX шлёт id то числом, то строкой."""
    if not HOST_MODE or uid is None:
        return False
    return same_uid(uid, HOST_OWNER) or same_uid(uid, GCFG.get("host_uid"))


def is_global_admin(uid):
    return str(uid) in {str(x) for x in ADMIN_IDS}


def can_panel(uid):
    """Панель /admin: хозяин бота и администраторы из ADMIN_IDS."""
    return (is_host(uid) or is_global_admin(uid)) if HOST_MODE else is_owner(uid)


def owner_locked_out(uid):
    """OWNER_ID задан, но пишет не владелец и ещё не админ — владельцем стать нельзя, только по приглашению."""
    return bool(FIXED_OWNER) and not same_uid(uid, FIXED_OWNER) and not is_admin(uid)


def notify(text, tag=True, rows=None):
    n = len(tenant_themes(cur().owner)) if HOST_MODE and cur().owner else len(THEMES)
    if tag and n > 1:
        text = f"🎭 {cur().name}\n{text}"
    in_background(_notify_send, list(card_chats()), text, rows, name="notify")   # из корутины — не держим цикл


def _notify_send(chats, text, rows):
    for chat in chats:
        try:
            api_send(chat, text, rows)
        except Exception as e:
            print("notify:", e)


TERMS_VER = 1                          # поменял условия — подними номер, покупатели подтвердят заново
CONSENT_FILE = "consent.json"          # кто и когда подтвердил права на контент (доказательство при споре)


def terms_text():
    return (f"📄 Условия использования (кратко)\n\n"
            "1. Что делает бот. По твоим настройкам копирует новые посты из указанных ТОБОЙ каналов Telegram "
            "в ТВОЙ канал MAX. Продавец даёт программу и её работу на сервере. Какие каналы копировать, "
            "выбираешь только ты, продавец контент не выбирает и не проверяет.\n\n"
            "2. Права на контент. Добавляй только свои каналы или каналы, на копирование которых у тебя есть "
            "разрешение правообладателя. За то, что публикуется в твоём канале, отвечаешь ты как его владелец. "
            "Продавец — технический посредник (ст. 1253.1 ГК РФ).\n\n"
            "3. Запрещено: публиковать чужой контент без разрешения, материалы, запрещённые законом РФ, спам; "
            "нарушать правила Telegram и MAX.\n\n"
            f"4. Жалобы правообладателей — {SELLER_CONTACT}. По обоснованной жалобе источник убирается, "
            "а бот может быть приостановлен до выяснения.\n\n"
            "5. Оплата — по тарифу (кнопка «💳 Тариф и оплата»). Кончился срок — бот на паузе до "
            "оплаты, настройки сохраняются.\n\n"
            "6. Отказаться можно в любой момент: вернём деньги за неиспользованные дни "
            "(ст. 32 закона «О защите прав потребителей»).\n\n"
            "7. Данные. Хранятся только нужные для работы: ID и имена админов в MAX, настройки, очередь постов; "
            "если подключаешь Telegram — сессия входа на сервере. Третьим лицам не передаются. "
            "Удалить всё — напиши продавцу.\n\n"
            "8. Перерывы в работе Telegram, MAX или сервера возможны; за блокировки каналов и действия этих "
            "площадок продавец не отвечает.\n\n"
            + (f"Полный текст оферты: {legal_info()['offer_url']}" if legal_info()["offer_url"] else
               f"Полный текст оферты — у продавца: {SELLER_CONTACT}."))


RIGHTS_TEXT = ("📄 Подтверди права на контент\n\n"
               "Бот копирует только те каналы, которые укажешь ты. Перед добавлением каналов-источников "
               "подтверди:\n\n"
               "1. Каналы, которые ты добавляешь, принадлежат тебе, или у тебя есть разрешение их владельца "
               "(правообладателя) копировать и публиковать их посты в MAX.\n"
               "2. За публикуемый контент отвечаешь ты: за авторские права, законы РФ, правила Telegram и MAX.\n"
               "3. Если правообладатель или площадка предъявит претензию, отвечаешь ты: продавец бота не выбирает "
               "и не проверяет контент.\n"
               "4. По обоснованной жалобе правообладателя продавец может убрать источник или приостановить бота.\n\n"
               "Нажимая «✅ Подтверждаю», ты соглашаешься с этим и с «📄 Условиями». "
               "Дата и твой ID в MAX сохраняются.")


def consented(uid):
    return load_json(CONSENT_FILE, {}).get(str(uid), {}).get("ver") == TERMS_VER


def sources_list(chs, show=60, empty="пока нет"):
    """Нумерованный список источников для экрана; длинный — первые show и «… и ещё N» (MAX — до 4000 символов)."""
    lines = [f"{i + 1}. {ch_label(c)}" for i, c in enumerate(chs[:show])]
    if len(chs) > show:
        lines.append(f"… и ещё {len(chs) - show}")
    return "\n".join(lines) or empty


def source_rows(chs, payload, per=None):
    """Кнопки «❌ источник» (не больше 48): до 26 — по одной в ряд, больше — по две (у MAX — до 30 рядов)."""
    per = per or (1 if len(chs) <= 26 else 2)
    btns = [B(f"❌ {ch_label(c).lstrip('@')}"[:40 if per == 1 else 30], payload(i)) for i, c in enumerate(chs[:48])]
    return [btns[i:i + per] for i in range(0, len(btns), per)]


def drop_source(i):
    """Убирает источник №i текущей темы (и что о нём помнили)."""
    gone = CFG["channels"].pop(i)
    LAST_IDS.pop(gone, None)
    cur().saved_ids.pop(gone, None)
    return gone


NO_THEME = "Сначала нужна тема: «🎭 Мои каналы» → «➕ Новая тема» — к ней привяжешь канал MAX и источники."


def add_sources(text, admin=False):
    """Добавляет каналы-источники (Telegram, соцсети) в текущую тему. (ответ, экран после).
    admin — добавляет продавец из панели: про лимит — подсказка ему, а не «перейди на тариф»."""
    if cur() is _VOID:                                    # темы нет — не «добавил» в пустоту
        return NO_THEME, "th"
    names, bad = [], []
    for tok in re.split(r"[\s,;]+", text.strip()):
        if not tok:
            continue
        skey, why = social_parse(tok)
        if skey or why:
            if not feature_on("social"):
                bad.append(tok + " (соцсети пока в бете — скоро будут у всех)")
            elif on_free():
                bad.append(tok + " (соцсети — на платном тарифе" + ("" if admin else ": «💳 Подписка»") + ")")
            elif why:
                bad.append(f"{tok} ({why})")
            else:
                names.append(skey)
                save_json(SOCIAL_FILE, SOCIAL)
            continue
        m = re.fullmatch(r"(?:https?://)?(?:t\.me/|telegram\.me/)?@?([A-Za-z0-9_]{4,32})/?", tok)
        if m:
            names.append(m.group(1))
        else:
            bad.append(tok)
    have = [c.lower() for c in CFG["channels"]]
    added, over = [], []
    for name in names:
        if name.lower() in have:
            continue
        if (CLIENT_MODE or (HOST_MODE and sub_info())) and len(CFG["channels"]) >= lim_sources():
            over.append(name)
            continue
        CFG["channels"].append(name)
        have.append(name.lower())
        BAD.pop(name, None)
        added.append(name)
    save_cfg()
    if not names:
        return "Не похоже на username канала. Попробуй ещё раз." + (
            "\nНе поняла: " + ", ".join(bad) if bad else ""), "ch"
    msg = ("Добавил: " + ", ".join(ch_label(n) for n in added) + ". Старые посты не публикую, только новые."
           if added else "" if over else "Эти каналы уже в списке.")   # не влезли в лимит — это не «уже в списке»
    if bad:
        msg += "\nНе поняла: " + ", ".join(bad)
    if over and admin:
        msg += (f"\n⚠️ В тарифе покупателя до {lim_sources()} источников в теме — не добавил: "
                + ", ".join("@" + n for n in over) + ". Больше — смени ему тариф или лимиты: «📦 Тариф», «📏 Лимиты».")
        return msg.strip(), "ch"
    if over:
        msg += (f"\n⚠️ В тарифе до {lim_sources()} каналов в одной теме — не добавил: "
                + ", ".join("@" + n for n in over) + ". Нужно больше — перейди на тариф больше 👇")
        return msg.strip(), "pay"
    return msg.strip(), "ch"


def need_rights(uid):
    """Покупатель ещё не подтвердил права на контент — каналы-источники добавлять нельзя."""
    return (CLIENT_MODE or HOST_MODE) and not consented(uid)


def record_consent(uid):
    data = load_json(CONSENT_FILE, {})
    data[str(uid)] = {"ver": TERMS_VER, "t": time.time(), "date": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "name": CFG["admin_names"].get(str(uid), "")}
    save_json(CONSENT_FILE, data)
    journal(f"Права на контент подтверждены: {data[str(uid)]['name'] or uid} (ID {uid}), условия v{TERMS_VER}")


def rights_rows(uid=None):
    return [[B("✅ Подтверждаю", "rk:ok")], [B("📄 Условия", "m:terms")]] + prompt_nav(uid)


def fmt_iv(s):
    return f"{s} сек" if s < 60 else f"{s // 60} мин"


def next_pub_text():
    if not QUEUE:
        return "очередь пуста"
    gap = CFG["gap_min"] * 60 if CFG["queue_on"] else 0
    left = PUB["last_pub"] + gap - time.time()
    return "можно публиковать сразу" if left <= 0 else f"через {fmt_min(math.ceil(left / 60))}"


FLASH = {}   # короткое сообщение пользователю над следующим экраном («✅ Канал привязан»)


def screen(name, uid=None):
    text, rows = _screen(name, uid)
    note = FLASH.pop(uid, None)
    return ((note + "\n\n" + text) if note else text), rows


def status_text():
    lines = [f"@{c}: последний пост {LAST_IDS.get(c, '—')}" for c in CFG["channels"]]
    return ("\n".join(lines) +
            f"\n\nОпубликовано: {STATS['published']}\nВ очереди: {len(QUEUE)}"
            f"\nДублей пропущено: {STATS['dups']}\nОтсеяно стоп-словами: {STATS['blocked']}"
            f"\nУстарело и удалено: {STATS['expired']}"
            f"\nОшибок: {STATS['errors']}")


def report_text():
    return (f"📈 Ежедневный отчёт: {on_off(CFG['report_on'])}\n"
            f"Присылаю в {CFG['report_hour']:02d}:00\n\n" + make_report())


def report_rows():
    return [[B(f"📈 Отчёт: {on_off(CFG['report_on'])}", "a:rep")],
            [B("Час: −1", "a:rhour:-1"), B("Час: +1", "a:rhour:1")],
            [B("📨 Прислать сейчас", "a:repnow")]]


def _screen(name, uid=None):
    back = [B("◀️ В меню", "m:main")]
    got = buyer_admin_screen(name, uid)
    if got is not None:
        return got
    if name in ("cllegal", "legal", "privacy", "deldata"):      # ⚖️ документы, реквизиты, данные
        got = legal_screen(name, uid)
        if got is not None:
            return got
        name = "main"
    if name == "dg" and simple_menu(uid):
        name = "main"                                   # 🔎 проверки связи у покупателя нет — его меню
    if name == "bview" and HOST_MODE and can_panel(uid):   # 👀 админ смотрит меню глазами покупателя
        text, rows = buyer_menu(uid)
        return "👀 Так покупатели видят главное меню:\n\n" + text, rows + [[B("◀️ Панель", "m:cl")]]
    if name in ("main", "bview") and HOST_MODE:
        if can_panel(uid):
            name = "cl"                                 # у хозяина/админа главное меню — панель кабинетов
        else:
            return buyer_menu(uid)                      # у покупателя — меню из 6 кнопок
    if HOST_MODE and is_host(uid) and name == "home":   # «Мои каналы» хозяина: заводим кабинет с темой, если его ещё нет
        t = ensure_host_cabinet()
        act = active_theme(uid)                         # открываем ту тему, что хозяин выбрал, а не всегда первую
        if act is not None and act.owner == host_id():
            t = act
        if t is not None:
            META["active"][str(uid)] = t.id
            CUR.set(t)
    if name.startswith("cl"):
        got = (host_screen if HOST_MODE else clients_screen)(name, uid)
        if got is not None:
            return got
        name = "main"
    if name == "ch":
        chs = CFG["channels"]
        text = "📡 Каналы-источники\n\n" + sources_list(chs, empty="список пуст") \
            + ("\n\nНажми на канал, чтобы убрать его." if chs else
               "\n\nНажми «➕ Добавить канал» и пришли канал Telegram: @name или https://t.me/name") \
            + ("\nКнопками — первые 48: убери их, покажу следующие." if len(chs) > 48 else "")
        rows = source_rows(chs, lambda i: f"a:chdel:{i}")
        if feature_on("social"):
            text += "\n\n🌐 Можно и соцсети: пришли ссылку на аккаунт TikTok, VK, YouTube, Rutube или сайт с RSS."
        rows += [[B("➕ Добавить канал", "p:chadd")], back]
    elif name == "vid":
        text = (f"🎬 Видео и GIF: {on_off(CFG['video_on'])}\n"
                f"Максимальный размер: {media_cap_mb()} МБ\n\n"
                "Пост с файлом больше лимита пропускается целиком — без текста и без ссылки "
                f"(потолок {MAX_MEDIA_MB} МБ).")
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
                f"🚫 Рекламные посты: {'пропускаю' if CFG['ads_skip'] else 'публикую (выключено)'} — бот сам узнаёт "
                "рекламу: пометка «Реклама»/erid, ссылки на чужие каналы и ботов, кнопки «Перейти», «промокод», "
                f"«подпишись». Отсеяно: {STATS.get('ads', 0)}. Что именно отсеял — в 📜 Журнале.\n"
                f"🤔 Спорные посты (может, реклама, а может, нет): "
                f"{'присылаю тебе с кнопками «Не реклама / Реклама» — ответ учит фильтр' if CFG.get('ads_ask', True) else 'отсеиваю сам'}.\n\n"
                f"Рекламные слова (строка удаляется):\n{fmt_words(CFG['ad_keywords'])}\n\n"
                f"Подписи доноров (строка удаляется):\n{fmt_words(CFG['old_signatures'])}")
        if CFG["trusted_sites"]:
            text += "\n\n✅ Твои надёжные сайты (ссылки туда — не реклама): " + fmt_words(CFG["trusted_sites"])
        rows = [[B(f"🚫 Рекламные посты: {'пропускаю' if CFG['ads_skip'] else 'выкл'}", "a:adsk")],
                [B(f"🤔 Спорные посты: {'спрашивать меня' if CFG.get('ads_ask', True) else 'отсеивать сами'}", "a:adask")],
                [B("🧠 Учить фильтр", "m:adl")],
                [B("✅ Надёжные сайты +", "p:addts"), B("✅ Надёжные сайты −", "p:delts")],
                [B("➕ Реклама", "p:adda"), B("➖ Реклама", "p:dela")],
                [B("➕ Подпись донора", "p:addo"), B("➖ Подпись донора", "p:delo")],
                [B("🚫 Стоп-слова", "m:sw"), B("🔄 Автозамена", "m:rw")], back]
    elif name == "adl":
        m = ad_model()
        held = list(ADHELD)[-5:][::-1]
        pubs = [p for p in PUB.get("day_posts", []) if p.get("mid") and p.get("raw") is not None
                and not p.get("ad")][-5:][::-1]
        smart = ("учится ✅" if m["n_ad"] >= AD_MODEL_MIN and m["n_ok"] >= AD_MODEL_MIN else
                 "начнёт помогать после 3 отметок каждого вида")
        text = (f"🧠 Обучение фильтра рекламы — {smart}\n"
                f"Запомнил: рекламы {m['n_ad']}, нормальных постов {m['n_ok']}.\n\n"
                "Бот ошибся? Отметь — он запомнит и в следующий раз решит правильно.\n\n"
                "Не выложил как рекламу:\n" + ("\n".join(f"{i + 1}. {plain_snip(x.get('text') or x.get('raw'), 60)} "
                                                       f"(@{x['ch']})" for i, x in enumerate(held)) or "— пусто") +
                "\n\nВышло в канал:\n" + ("\n".join(f"{i + 1}. {p['text'][:60]} (@{p['ch']})"
                                                   for i, p in enumerate(pubs)) or "— пусто"))
        rows = [[B(f"✅ {i + 1}. Не реклама — выложить", f"a:adok:{x['id']}")] for i, x in enumerate(held)]
        rows += [[B(f"🚫 {i + 1}. Это реклама — удалить", f"a:adbad:{p['mid']}")] for i, p in enumerate(pubs)]
        rows += [[B("◀️ Фильтры", "m:fl")]]
    elif name == "sg":
        text = (f"✍️ Моя подпись: {on_off(CFG['signature_on'])}\n"
                f"Текст: {CFG['signature_text']}\n"
                "Подпись — кликабельная ссылка на твой канал.")
        rows = [[B(f"✍️ Подпись: {on_off(CFG['signature_on'])}", "a:sg")],
                [B("✏️ Изменить текст", "p:sgtext")], back]
    elif name == "iv":
        text = (f"⏱ Как часто проверять каналы на новые посты\nСейчас: каждые {fmt_iv(CFG['interval'])}\n\n"
                f"Это только проверка. Как часто посты ВЫХОДЯТ — в «⏳ Расписание»: сейчас пауза между "
                f"постами {fmt_min(CFG['gap_min']) if CFG['queue_on'] else 'нет (выходят сразу)'}.")
        rows = [[B("30 сек", "a:iv:30"), B("1 мин", "a:iv:60"), B("2 мин", "a:iv:120")],
                [B("5 мин", "a:iv:300"), B("10 мин", "a:iv:600")], [B("⏳ Расписание", "m:q")], back]
    elif name == "ai":
        hh = hot_hours()
        hot = "" if hh is None else " · лучшие часы: " + ", ".join(f"{h:02d}" for h in sorted(hh))
        text = ("🧠 Умные функции\n\nБот работает эффективнее. Всё внутри бота, бесплатно, тексты никуда "
                "не уходят.\n\n"
                f"⏰ Умное время — {on_off(CFG['smart_time'])}: публикую в часы, когда больше просмотров{hot}. "
                "Данные берутся из отчётов (нужно 1-2 дня).\n"
                f"🏷 Умные хэштеги — {on_off(CFG['smart_tags'])}: сам добавляю 2-3 хэштега по теме.\n"
                f"⭐ Авто-оценка — {on_off(CFG['smart_quality'])}: слабые посты сначала тебе на ✅, сильные сразу "
                "(работает, когда ручное одобрение выключено).\n"
                f"🔁 Строгие дубли — {on_off(CFG['dedupe_strict'])}: та же картинка = повтор даже с другой подписью.\n"
                f"🚫 Строго к рекламе — {on_off(CFG['ads_strict'])}: ловлю больше рекламы (может задеть обычный пост).")
        rows = [[B(f"⏰ Умное время: {on_off(CFG['smart_time'])}", "a:aismart")],
                [B(f"🏷 Умные хэштеги: {on_off(CFG['smart_tags'])}", "a:aitags")],
                [B(f"⭐ Авто-оценка: {on_off(CFG['smart_quality'])}", "a:aiqual")],
                [B(f"🔁 Строгие дубли: {on_off(CFG['dedupe_strict'])}", "a:aidup")],
                [B(f"🚫 Строго к рекламе: {on_off(CFG['ads_strict'])}", "a:aiads")]]
        if feature_on("ai"):
            text += (f"\n\n🪄 ИИ-редактор (бета) — {on_off(CFG.get('ai_edit'))}: пересказываю пост другими словами — "
                     "тема та же, длина та же. Ссылки, @каналы, хэштеги, цифры и КАПС не трогаю.")
            rows += [[B(f"🪄 ИИ-редактор: {on_off(CFG.get('ai_edit'))}", "a:aied")]]
        rows.append(back)
    elif name.split(":")[0] == "q":
        page = 1 if name.partition(":")[2] == "1" else 0
        text = (f"⏳ Расписание публикаций: {on_off(CFG['queue_on'])}\n"
                 "Настрой, как часто бот будет выкладывать посты.\n\n"
                f"Пауза между публикациями: {fmt_min(CFG['gap_min'])}\n"
                f"Кнопка «⏰ Через…» на карточке поста: {fmt_min(CFG['later_min'])}\n"
                f"Старые посты удаляются из очереди через {CFG['max_age_h']} ч\n\n"
                f"В очереди: {len(QUEUE)} · следующая публикация: {next_pub_text()}\n\n"
                "Если расписание выключено, посты выходят сразу, как появляются.")
        backlog = sum(1 for x in QUEUE if x.get("status", "ok") == "ok")
        if CFG["queue_on"] and backlog >= 3 and backlog * CFG["gap_min"] >= 120:
            text += (f"\n\n⚠️ Очередь длинная: последний пост выйдет примерно через "
                     f"{fmt_min(backlog * CFG['gap_min'])}. Уменьши паузу (например, 20–30 мин).")
        text += f"\n\nСтраница {page + 1} из 2."
        if page == 0:
            rows = [[B(f"⏳ Расписание: {on_off(CFG['queue_on'])}", "a:qon")],
                    [B("➖15 мин", "a:gap:-15"), B("➕15 мин", "a:gap:15"), B("✏️ Своя", "p:gapx")],
                    [B("30 мин", "a:gapset:30"), B("1 час", "a:gapset:60"), B("2 часа", "a:gapset:120")],
                    [B("🌙 Ночь", "m:nt")],
                    [B("📋 Очередь", "m:ql"), B("⏩ Выложить сейчас", "a:qnow")],
                    [B("Далее ➡️", "m:q:1")], back]
        else:
            rows = [[B("⏰ «Через…» −15", "a:later:-15"), B("+15", "a:later:15")],
                    [B(f"🗑 Хранить: {CFG['max_age_h']} ч", "a:age")],
                    [B(f"📭 Пустая очередь: {on_off(CFG['empty_warn_on'])}", "a:ewarn")],
                    [B("⬅️ Назад", "m:q")], back]
    elif name == "ql":
        now = time.time()
        items = sorted(QUEUE, key=lambda x: x["date"])[:10]
        lines = []
        for i, it in enumerate(items):
            snip = plain_snip(it.get("text"), 40)
            mark = {"pending": "✋"}.get(it.get("status"), "")
            if it.get("status") == "pending":
                when = f"ждёт ТВОЕГО одобрения уже {fmt_min((now - it['enq']) / 60)}"
            elif it.get("at", 0) > now:
                when = "выйдет в " + time.strftime("%H:%M", time.localtime(it["at"]))
            else:
                when = f"в очереди {fmt_min((now - it['enq']) / 60)}"
            lines.append(f"{i + 1}. {mark}@{it['ch']} · {when} · {snip}")
        pend = sum(1 for x in QUEUE if x.get("status") == "pending")
        text = (f"📋 Очередь ({len(QUEUE)})\nСледующая публикация: {next_pub_text()}\n\n"
                + ("\n".join(lines) or "пока пусто")
                + ("\n\n✋ — пост ждёт, пока ты нажмёшь «Сейчас» / «Через…» / «Пропустить» на его карточке. "
                   "Сам он не выйдет." if pend else ""))
        dels = [B(f"❌ {i + 1}", f"a:qdel:{it['id']}") for i, it in enumerate(items)]
        rows = [dels[i:i + 5] for i in range(0, len(dels), 5)]
        if items:
            text += "\n\nНажми ❌ с номером, чтобы убрать конкретный пост."
        if pend:
            rows += [[B(f"📬 Показать карточки ({pend})", "a:qcards")],
                     [B(f"✅ Одобрить все ждущие ({pend})", "a:qapr")]]
        rows += [[B("⏩ Выложить всё сейчас", "a:qnow")], [B("🗑 Очистить очередь", "a:qclr")],
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
        text, rows = report_text(), report_rows() + [back]
    elif name == "sr":                                   # 📊 Статистика и Отчеты (покупатель): статус + отчёт
        text = "📊 Статистика и Отчеты\n\n" + status_text() + "\n\n" + report_text()
        rows = [[B("🔄 Обновить", "m:sr")]] + report_rows() \
            + ([[B("📊 Статистика просмотров", "m:anl")]] if feature_on("stats") and not on_free() else []) + [back]
    elif name == "ad":
        lines, rows = [], []
        for i, a in enumerate(CFG["admins"]):
            nm = CFG["admin_names"].get(str(a), str(a))
            lines.append(f"{i + 1}. {nm}" + (" 👑 владелец" if i == 0 else "") + (" (это ты)" if a == uid else ""))
            if i > 0 and is_owner(uid):
                rows.append([B(f"❌ Убрать {nm}", f"a:addel:{i}")])
        if is_owner(uid):
            rows.append([B("➕ Пригласить админа", "a:adinv")])
            if len(CFG["admins"]) > 1:
                rows.append([B("🚨 Убрать всех админов", "m:adkick")])
        text = "👥 Админы бота\n\n" + "\n".join(lines) + \
            ("" if is_owner(uid) else "\n\nДобавлять и убирать админов может только владелец.")
        rows.append(back)
    elif name == "adkick":
        text = ("🚨 Убрать всех админов, кроме тебя?\n\nНа случай, если к боту попал чужой. Старые коды приглашений "
                "перестанут действовать. Своих потом пригласишь заново.")
        rows = [[B("✅ Да, убрать всех", "a:adkickok")], [B("❌ Нет", "m:ad")]]
    elif name == "adfull":
        text = f"👥 В тарифе до {LIMIT_ADMINS} админов — больше добавить нельзя. Убери кого-то или напиши {SELLER_CONTACT}."
        rows = [[B("◀️ Админы", "m:ad")]]
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
        text = "📊 Статус\n\n" + status_text()
        rows = [[B("🔄 Обновить", "m:st")], back]
    elif name == "th":
        mine = my_themes(uid)
        lines = []
        for i, t in enumerate(mine):
            with theme_ctx(t):
                chans = len(CFG["channels"])
                lines.append(f"{'✅' if t is active_theme(uid) else '▫️'} {i + 1}. {t.name} · каналов: {chans} · "
                             f"очередь: {len(QUEUE)}"
                             + ("" if t.chat_id else " · ⚠️ канал MAX не привязан"))
        text = ("🎭 Темы\nДобавь свои каналы MAX.\n\nКаждая тема читает свои каналы Telegram и выкладывает в свой канал MAX. "
                "Отмеченная ✅ — та, которую ты сейчас настраиваешь.\n\n"
                + "\n".join(lines))
        rows = [[B(("✅ " if t is active_theme(uid) else "") + t.name, f"a:thsw:{i}")] for i, t in enumerate(mine)]
        if len(mine) > 1:
            on = all_mode(uid)
            text += ("\n\n🌐 Настройки сразу для всех тем: " + ("ВКЛ — всё, что меняешь в меню (фильтры, расписание, "
                     "видео…), меняется во всех темах. Каналы у каждой темы свои." if on else
                     "выкл — меняешь только выбранную тему."))
            rows.append([B(f"🌐 Для всех тем: {'вкл' if on else 'выкл'}", "a:allmode")])
        rows += [[B("➕ Новая тема", "p:themenew"), B("⚙️ Эта тема", "m:thm")], back]
    elif name == "thm":
        t = cur()
        text = (f"⚙️ Тема «{t.name}»\n\n"
                f"Канал MAX: {t.link or '— не задан'}\n"
                f"Привязка: {'✅ есть' if t.chat_id else '⚠️ нет — нажми «📢 Канал MAX» и выбери канал'}\n"
                f"Каналов-источников: {len(CFG['channels'])}\n"
                f"Подпись: {CFG['signature_text']}")
        rows = [[B("✏️ Название", "p:thname"), B("📢 Канал MAX", "m:chpick")]]
        if t.chat_id:
            rows.append([B("🩺 Проверить публикацию", "a:chtest")])
        rows += [
                [B("📡 Каналы-источники", "m:ch")]]
        if not t.legacy:
            rows.append([B("🗑 Удалить тему", "m:thd")])
        rows.append([B("◀️ Темы", "m:th")])
    elif name.split(":")[0] == "chpick":
        t = cur()
        chans = pick_channels(uid)
        per = 5                                            # по 5 каналов на страницу
        pages = max(1, (len(chans) + per - 1) // per)
        tail = name.partition(":")[2]
        page = min(max(0, int(tail) if tail.isdigit() else 0), pages - 1)
        if t.chat_id:
            text = f"📢 Тема «{t.name}» уже выкладывает в канал MAX ✅\n\nМожно выбрать другой канал:"
        else:
            text = f"📢 Куда выкладывать тему «{t.name}» — выбери свой канал MAX:"
        if chans:
            text += f"\n\n(каналы, где бот — админ. Страница {page + 1} из {pages})"
        else:
            text += ("\n\nПока не вижу твоих каналов. Сделай так:\n1. Открой свой канал MAX → Админы → Добавить → "
                     "найди этого бота.\n2. Включи ему право «Публиковать сообщения».\n3. Вернись и нажми «🔄 Обновить».")
        def label(c):
            if c["theme"] is t:
                return "✅ " + c["title"]
            if c["theme"] is not None:
                return "🔁 " + c["title"]
            return "📢 " + c["title"]
        if any(c["theme"] is not None and c["theme"] is not t for c in chans):
            text += "\n🔁 — канал сейчас у другой темы: нажми, и он перейдёт в эту."
        shown = chans[page * per:(page + 1) * per]
        rows = [[B(label(c)[:30], f"a:chbind:{c['chat_id']}")] for c in shown]
        nav = []
        if page > 0:
            nav.append(B("⬅️ Назад", f"m:chpick:{page - 1}"))
        if page < pages - 1:
            nav.append(B("Далее ➡️", f"m:chpick:{page + 1}"))
        if nav:
            rows.append(nav)
        if t.chat_id:
            rows.append([B("🩺 Проверить публикацию", "a:chtest")])
        rows += [[B("🔄 Обновить", f"m:chpick:{page}"), B("🔗 Ввести ссылку", "p:thlink")],
                 [B("◀️ В меню", "m:main")]]
    elif name == "pinset":
        h = int(CFG.get("autodel_h") or 0)
        when = "не удалять" if not h else (f"через {h // 24} дн." if h >= 24 and h % 24 == 0 else f"через {h} ч")
        text = (f"📌 Закреп и автоудаление · 🎭 {cur().name}\n\n"
                f"📌 Закреплять каждый новый пост: {on_off(CFG.get('pin_new'))}\n"
                "Свежий пост закрепляется в канале, прошлый закреп снимается сам. Боту нужно право "
                "«Закреплять сообщения».\n\n"
                f"🗑 Удалять посты из канала: {when}\n"
                "Пост сам исчезнет из канала через выбранное время.\n\n"
                "Бесплатно, во всех тарифах.")
        rows = [[B(f"📌 Закреплять новый пост: {on_off(CFG.get('pin_new'))}", "a:pinnew")],
                [B(("✅ " if h == 0 else "") + "Не удалять", "a:adh:0"), B(("✅ " if h == 6 else "") + "6 ч", "a:adh:6"),
                 B(("✅ " if h == 12 else "") + "12 ч", "a:adh:12")],
                [B(("✅ " if h == 24 else "") + "24 ч", "a:adh:24"), B(("✅ " if h == 48 else "") + "48 ч", "a:adh:48"),
                 B(("✅ " if h == 168 else "") + "7 дн", "a:adh:168")], back]
    elif name == "thd":
        text = f"🗑 Удалить тему «{cur().name}»? Очередь этой темы пропадёт."
        rows = [[B("✅ Да, удалить", "a:thdel"), B("Нет", "m:thm")]]
    elif name == "lg":
        own = cur().owner if HOST_MODE else ""
        lines = [x for x in JOURNAL if not HOST_MODE or (own and getattr(x, "owner", "") == own)
                 or (is_host(uid) and not getattr(x, "owner", ""))][-25:]   # каждый видит только свой журнал
        text = "📜 Журнал (последние события)\n\n" + ("\n".join(lines) if lines else "пока пусто")
        rows = [[B("🔄 Обновить", "m:lg")] + ([] if simple_menu(uid) else [B("🔎 Проверка", "m:dg")]), back]
    elif name == "dg":
        text = run_diag()
        rows = [[B("🔄 Проверить снова", "m:dg")], back]
    elif name == "sup":                                  # ❓ Поддержка, когда контакт продавца — не ссылка (телефон)
        link = seller_link()
        known = contact_ok(SELLER_CONTACT) and SELLER_CONTACT != "@ВАШ_КОНТАКТ"
        text = "❓ Поддержка\n\n" + (f"Напиши продавцу: {SELLER_CONTACT}" if known else "Продавец ещё не указал контакт.")
        req = LEGAL.requisites(legal_info())
        if req:
            text += "\n\n🏢 Продавец:\n" + req
        rows = ([[LB("❓ Поддержка", link)]] if link else []) + [[B("📄 Документы и реквизиты", "m:legal")], back]
    elif name == "hp":
        text = help_text(uid)
        rows = [back]
    elif name == "gd":
        text = guide_text()
        rows = [[B("📢 Куда выкладывать (канал MAX)", "m:chpick")],
                [B("📡 Откуда брать посты (Telegram)", "m:ch")], back]
    elif name == "anl" and feature_on("stats"):
        chans = [t for t in (scope_themes() if HOST_MODE else THEMES) if t.chat_id]
        text = ("📊 Аналитика — «Итоги недели» по каждому каналу\n\n"
                "Бот берёт ВСЕ посты канала MAX за последние 7 дней (и выложенные ботом, и твои ручные) "
                "и присылает одну картинку на канал:\n\n"
                "• просмотры за неделю и рост или падение к прошлой неделе;\n"
                "• график по дням — какой день был лучшим;\n"
                "• посты, просмотры на пост и рекорд недели;\n"
                "• «Когда выкладывать» — просмотры по часам (пик и «тихие» часы) и 3 лучших времени: "
                "во сколько раз там больше просмотров, чем в среднем (берём время, где 2+ поста, — не случайность);\n"
                "• «Лучшие посты» — 3 самых просматриваемых поста с картинкой;\n"
                "• совет, когда ставить главное.\n\n"
                "Как пользоваться: ставь важные посты на лучшее время (⏳ Расписание) или включи 🧠 Умное время — "
                "бот сам будет придерживать посты до «горячих» часов.\n\n"
                "⏳ Подсчёт занимает до 1–2 минут — нажми кнопку один раз и подожди, картинки придут сами.\n\n"
                f"Каналов: {len(chans)} — по картинке на каждый.\n"
                "Бот должен быть админом канала, иначе MAX не покажет просмотры.\n\n"
                "🖼 Карточка канала — подписчики и прирост (сегодня / 7 / 30 дней), график роста, просмотры "
                "на пост за 24 / 48 / 72 часа и ER.\n"
                "🌐 Сводка по всем каналам — вся сеть одной картинкой: подписчики, просмотры, общий ER, топ-3.\n"
                "🔮 Прогноз поста — пришли черновик, бот оценит его до публикации.\n"
                "📊 Любой канал — перешли боту пост из него (или пришли ссылку на канал): «📊 Рекламный охват», "
                "«📈 Массовая проверка» (каналы вашей сетки) или «⚡ Оценка потенциала». Без привязки и без прав "
                "админа, если канал открытый.\n\n"
                "🌗 Картинки днём (06:00–17:00 МСК) светлые, вечером и ночью — тёмные.")
        rows = [[B("📊 Прислать картинку сейчас", "a:anl")],
                [B("🖼 Карточка канала", "a:anlcard")],
                [B("🌐 Сводка по всем каналам", "a:anlnet")],
                [B("🔮 Прогноз поста", "a:predict")],
                *([[B("🔥 Тренды", "m:trends")]] if feature_on("trends") else []),
                [B("🕘 Недавние проверки", "m:fwr")],
                [B(f"Фон карточек: {ANL_BG_NAMES[anl_bg_mode(uid)]}", "a:anlbg")],
                [B("◀️ Панель", "m:cl") if HOST_MODE and can_panel(uid) else back[0]]]
    elif name == "trends" and feature_on("trends"):
        text, rows = trend_screen(uid)
    elif name == "fwr":                                  # 🕘 Недавние проверки каналов (по пересланным постам)
        text, rows = fwd_recent_screen(uid)
    elif name == "predict" and feature_on("stats"):
        text = PREDICT_ASK
        rows = [[B("◀️ Аналитика", "m:anl")]]
    elif name == "pay" and (CLIENT_MODE or HOST_MODE):
        text, rows = pay_screen()
    elif name.split(":")[0] == "myt" and HOST_MODE and feature_on("mytariff"):
        text, rows = mytariff_screen(name.partition(":")[2])
    elif name == "terms":
        text = terms_text() + "\n\n" + legal_footer()
        rows = (legal_doc_rows() + [[B("🔐 Какие данные хранит бот", "m:privacy")]]
                + ([[B(DEL_BTN, "m:deldata")]] if legal_buyer(uid) else [])
                + [[B("📡 Каналы-источники", "m:ch")], back])
    elif name == "rights":
        text, rows = RIGHTS_TEXT, rights_rows(uid)
    elif name == "more" and simple_menu(uid):
        text = ("⚙️ Ещё настройки\n\n"
                "🎬 Видео — выкладывать ли видео и до какого размера\n"
                "🔁 Повторы — не выкладывать один и тот же пост дважды\n"
                "🛡 Фильтр рекламы — слова: строки с ними бот вырезает из постов\n"
                "✍️ Подпись под постами — текст в конце каждого поста\n"
                f"👥 Админы — дать управлять ботом ещё кому-то (до {LIMIT_ADMINS})\n"
                + ("" if HOST_MODE else "🎭 Темы — несколько каналов MAX, у каждого свои источники\n")
                + "📜 Журнал — что бот сделал с каждым постом\n"
                + ("" if not show_tg(uid) else "🔑 Telegram — твой аккаунт Telegram: файлы, закрытые каналы, большие видео\n")
                + "🧠 Умное — умное время, хэштеги, авто-оценка, строгие дубли и реклама\n"
                + "❓ Команды — быстрые команды через /\n"
                + "📄 Условия — правила использования бота")
        rows = [[B("🎬 Видео", "m:vid"), B("🔁 Повторы", "m:dd")],
                [B("🛡 Фильтр рекламы", "m:fl"), B("👥 Админы", "m:ad")],
                ([] if HOST_MODE else [B("🎭 Темы", "m:th")]) + [B("📜 Журнал", "m:lg")],   # у покупателя — «🎭 Мои каналы»
                *([[B("🔑 Telegram", "tg:menu")]] if show_tg(uid) else []),
                [B("🧠 Умное", "m:ai"), B("❓ Команды", "m:hp")],
                [B("✍️ Подпись под постами", "m:sg"), B("⏳ Расписание", "m:q")],
                [B("✋ Проверка постов", "m:ap")],
                [B("📖 Инструкция", "m:gd")],
                *([] if on_free() else [[B("📌 Закреп и автоудаление", "m:pinset")]]),
                [B("▶️ Продолжить" if CFG["paused"] else "⏸ Пауза", "a:pause")],
                [B("📄 Условия", "m:terms")], back]
    elif HOST_MODE and simple_menu(uid) and not can_panel(uid):
        return _screen("main", uid)                     # неизвестный экран — меню покупателя из 6 кнопок
    elif simple_menu(uid):
        text, rows = client_main(uid)
    else:
        text = (f"⚙️ Меню бота · 🎭 {cur().name}\n\n"
                f"Состояние: {'⏸ на паузе' if CFG['paused'] else '▶️ работает'}\n"
                f"Каналов: {len(CFG['channels'])} · проверка каждые {fmt_iv(CFG['interval'])}\n"
                f"Расписание: {'пауза ' + fmt_min(CFG['gap_min']) if CFG['queue_on'] else 'выкл'} · "
                f"в очереди: {len(QUEUE)}\n"
                f"Опубликовано: {STATS['published']} · дублей пропущено: {STATS['dups']}\n"
                f"Одобрение: {'вкл' if CFG['approval_on'] else 'выкл'} · ночь: {'вкл' if CFG['night_on'] else 'выкл'}")
        rows = [[B("📡 Каналы", "m:ch"), B("🎬 Видео", "m:vid")],
                [B("⏳ Расписание", "m:q"), B("🔁 Дубли", "m:dd")],
                [B("🧹 Фильтры", "m:fl"), B("✍️ Подпись", "m:sg")],
                [B("⏱ Интервал", "m:iv"), B("👥 Админы", "m:ad")],
                [B("✋ Одобрение", "m:ap"), B("📈 Отчёт", "m:rp")],
                [B("📊 Статус", "m:st"), B("🧪 Тест", "m:ts")],
                [B("❓ Команды", "m:hp")],
                [B("📌 Закреп и автоудаление", "m:pinset")],
                [B("🎭 Темы", "m:th"), B("📜 Журнал", "m:lg")],
                [B("🧠 Умное", "m:ai"), B("🔎 Проверка", "m:dg")] + ([B("🔑 Telegram", "tg:menu")] if show_tg(uid) else []),
                [B("▶️ Продолжить" if CFG["paused"] else "⏸ Пауза", "a:pause")]]
        if all_mode(uid):
            text += "\n\n🌐 Режим «для всех тем»: настройки меняются сразу во всех темах (выкл — в 🎭 Темы)"
        if can_panel(uid):
            cnt = len(TENANTS) if HOST_MODE else len(CLIENTS)
            rows.insert(0, [B(f"💼 Кабинеты ({cnt})" if HOST_MODE else f"💼 Клиенты ({cnt})", "m:cl")])
    return text, rows


SOCIAL_HINT = ("\n\n🌐 Бета: можно прислать ссылку на аккаунт соцсети — tiktok.com/@имя, vk.com/имя, "
               "youtube.com/@имя, rutube.ru/channel/…, или сайт/блог с RSS. Буду брать оттуда новые посты.")
PROMPTS = {
    "cllg:name": ("Пришли наименование продавца, как в документах: «ИП Иванов Иван Иванович», «ООО «Ромашка»» или "
                  "«Самозанятый Иванов Иван Иванович». «-» — очистить."),
    "cllg:inn": "Пришли ИНН: 12 цифр у ИП и самозанятого, 10 цифр у ООО. Проверю контрольные цифры. «-» — очистить.",
    "cllg:ogrn": "Пришли ОГРНИП (15 цифр, у ИП) или ОГРН (13 цифр, у ООО). У самозанятого его нет. «-» — очистить.",
    "cllg:address": "Пришли юридический адрес (адрес регистрации), как в выписке. «-» — очистить.",
    "cllg:email": "Пришли почту поддержки для покупателей: help@site.ru. «-» — очистить.",
    "cllg:phone": "Пришли телефон поддержки: +7 999 123-45-67. «-» — очистить.",
    "cllg:offer_url": "Пришли ссылку на оферту (договор с покупателем): https://… «-» — очистить.",
    "cllg:privacy_url": ("Пришли ссылку на политику конфиденциальности (как обрабатываешь персональные данные, "
                         "152-ФЗ): https://… «-» — очистить."),
    "cllg:refund_url": "Пришли ссылку на условия возврата денег: https://… «-» — очистить.",
    "cllg:cookies_url": ("Пришли ссылку на политику cookie — она нужна, если у тебя есть сайт: https://… "
                         "«-» — очистить (тогда возьму из переменной COOKIES_URL, если она есть)."),
    "trendwords": ("✏️ Пришли 1–5 слов или фраз через запятую — по ним бот будет искать новости для трендов "
                   "(например: roblox, brawl stars).\n\nПришли «авто» — снова брать слова из последних постов канала."),
    "codeentry": "Отправь код активации сообщением в этот чат.",
    "buyerreq": "📇 Продавец просит поделиться контактом — нажми кнопку ниже. Так он сможет написать тебе лично в MAX.",
    "buysearch": "Пришли id покупателя или его @username — найду карточку.",
    "codegen": ("Сколько кодов сделать? Пришли число от 1 до 50 — каждый код на 30 дней. "
                "Или пришли свой код: 10–12 символов, буквы и цифры вперемешку."),
    "coderevoke": "Пришли код, который нужно удалить. Удалённый код больше не сработает.",
    "clnew": "Пришли токен бота MAX клиента (длинная строка из настроек его бота на платформе MAX).",
    "clmsg": "Пришли текст — разошлю его всем клиентам (админам их ботов).",
    "clsess": ("👤 Пришли файл .session (вход Telethon) прямо сюда: 📎 → Файл. Бот проверит вход, положит файл "
               "в sessions/ и добавит аккаунт в пул. Сообщение с файлом удалю из чата."),
    "clfsadd": ("Пришли ссылку на канал MAX (например https://max.ru/mychannel) или его ID.\n"
                "Важно: бот должен быть администратором этого канала — иначе MAX не даст проверить подписчиков."),
    "cladpost": ("📢 Рекламная рассылка — пользователям бесплатного тарифа, в их чат с ботом (платные её не получают).\n\n"
                 "Пришли рекламный пост: текст и/или одну картинку."),
    "cladmark": ("Теперь маркировка по закону о рекламе. Пришли одной строкой рекламодателя и erid (токен из ОРД), "
                 "например:\nООО «Ромашка», ИНН 7700000000, erid: 2VtzqwXXXX\n\nСлово «Реклама» бот допишет сам."),
    "claikeys": ("🤖 Пришли ключи ИИ — через запятую или каждый с новой строки, можно сразу несколько. "
                 "Сообщение с ключами удалю из чата. Ключи из бота главнее переменной в Bothost."),
    "clvk": ("🔑 Ключ VK нужен, чтобы брать посты из VK. Бесплатно: vk.com/apps?act=manage → «Создать» → "
             "тип «Standalone» → в настройках приложения скопируй «Сервисный ключ доступа» и пришли сюда. "
             "Сообщение с ключом удалю из чата; ключ из бота главнее переменной VK_TOKEN в Bothost."),
    "cltt": ("🎵 Cookies TikTok — когда TikTok не отдаёт ролики без входа, бот читает профиль как ты. "
             "Войди в TikTok в браузере (лучше запасным аккаунтом) → расширение Cookie-Editor → «Export» "
             "(JSON или Header string) → пришли сюда. Нужен хотя бы sessionid. Сообщение удалю из чата; "
             "cookies из бота главнее переменной TIKTOK_COOKIES в Bothost."),
    "clsay": "Пришли сообщение для клиента — отправлю его от имени его бота.",
    "cltarset": "Пришли тарифы — по одному в строке: название, сколько тем, сколько каналов в теме, цена за 30 дней "
                "(за 90 дней будет ×2,5). Например:\nСтарт 1 5 300\nСтандарт 3 10 600\nПро 10 30 1500",
    "promo": "Пришли промокод — например LETO50.",
    "clpromo": "Пришли название нового промокода (буквы и цифры, например LETO50). "
               "Потом в нём можно поменять скидку/дни, число активаций и срок действия.",
    "clyk": ("🔑 Ключи ЮKassa. Пришли одним сообщением shopId и секретный ключ через пробел, например:\n"
             "123456 live_AbCd…\n\nГде взять: ЮKassa → нужный магазин → Интеграция → Ключи API. shopId — число вверху, "
             "ключ — нажми «Выпустить новый ключ» и скопируй его целиком. Твоё сообщение с ключом я попробую сразу удалить "
             "(если MAX не даст — удали сам) и проверю ключи в ЮKassa. Ключи из панели главнее переменных Bothost — перезапуск не нужен."),
    "clcontact": "Пришли свой контакт для клиентов (телефон, @ник в MAX или ссылка) — его бот показывает "
                 "клиентам во всех сообщениях об оплате и поддержке.",
    "clpayset": "Пришли, как тебе платить — этот текст клиенты увидят при оплате. Например:\n«Перевод на Сбербанк "
                "по номеру +7…, получатель Артём Ж. После перевода нажми «Я оплатил».»",
    "chadd": "Пришли каналы Telegram, из которых брать посты: @name или t.me/name "
             "(можно несколько — через пробел или с новой строки).",
    "clsrcadd": ("Пришли каналы Telegram для этой темы покупателя: @name или t.me/name "
                 "(можно несколько — через пробел или с новой строки)."),
    "themenew": "Пришли название новой темы, например: «Мемы про котов».",
    "thname": "Пришли новое название темы.",
    "thlink": "Пришли ссылку на канал MAX, куда выкладывать эту тему (https://max.ru/…). "
              "Бот должен быть админом этого канала.\n\nЗакрытый канал или ссылку искать лень — нажми "
              "«📋 Выбрать канал из списка»: покажу каналы, где бот уже админ.",
    "adda": "Пришли рекламное слово (или несколько через запятую). Строки с ним будут удаляться.",
    "dela": "Пришли рекламное слово, которое нужно убрать из списка.",
    "addo": "Пришли слово из подписи канала-донора (или несколько через запятую).",
    "delo": "Пришли слово подписи донора, которое нужно убрать из списка.",
    "sgtext": "Пришли новый текст подписи (до 60 символов).",
    "gapx": "Пришли паузу между публикациями в минутах (0–720), например 45.",
    "addts": "Пришли сайт, ссылки на который НЕ считать рекламой (официальный сайт игры и т.п.), например "
             "brawlstars.com — можно несколько через запятую. Сайты Roblox, Minecraft, PUBG, Brawl Stars, Standoff 2, "
             "Genshin и YouTube уже в списке.",
    "delts": "Пришли сайт, который убрать из надёжных.",
    "addsw": "Пришли стоп-слово (или несколько через запятую). Посты с ним публиковаться не будут.",
    "delsw": "Пришли стоп-слово, которое нужно убрать.",
    "addrw": "Пришли замену в виде: старое = новое\n(можно несколько строк). Если справа пусто — слово просто удаляется.",
    "delrw": "Пришли слово слева (то, что заменяется), чтобы убрать эту замену.",
}


def split_words(text):
    return [w.strip().lower() for w in re.split(r"[,\n]", text) if w.strip()]


def apply_text(action, text, uid=None):
    """Применяет присланный текст. Возвращает (ответ, экран)."""
    if (action or "").startswith("cllg:"):               # ⚖️ реквизиты продавца — только хозяин
        return legal_set(uid, action.split(":", 1)[1], text)
    if action == "trendwords":                         # 🔥 свои слова для поиска трендов
        return trend_words_set(cur(), text), "trends"
    if action in ("buysearch", "coderevoke") or (action or "").startswith("codegen:"):
        if not can_panel(uid):
            return "Нет доступа.", "main"
        if action == "buysearch":
            q = text.strip()[:40]
            return f"🔎 Ищу «{q}»", f"buyersq:{q}"
        if action == "coderevoke":
            ok = CODES.delete(text)
            return ("🗑 Код удалён — его больше нельзя ввести." if ok else
                    "Такого кода нет: либо опечатка, либо его уже ввели."), "codes"
        n = text.strip()
        try:
            t = tariffs()[int(action.partition(":")[2])]
        except (ValueError, IndexError):
            return "Тариф не найден — начни заново из «🎟 Коды».", "codes"
        if n.isdigit() and int(n) <= 50:
            if int(n) < 1:
                return "Нужно число от 1 до 50 или свой код.", "codes"
            codes, path = codes_generate(int(n), t["name"], 30)
            CODE_EXPORT[uid] = path
            return f"🎟 Сделал {len(codes)} кодов на «{t['name']}» (30 дн.). Файл с кодами — следом.", "codes"
        try:
            code = CODES.add_custom(n, t["name"], 30)
        except ValueError as e:
            return str(e), "codes"
        return f"✅ Добавил код {code} на «{t['name']}» (30 дн.).", "codes"
    if action == "clyk":
        if not can_panel(uid):
            return "Нет доступа.", "main"
        tail = secret_tail(uid)
        parts = [x for x in re.split(r"[\s,;:]+", text.strip()) if x]
        sid = next((x for x in parts if x.isdigit()), "")
        key = next((_yk_clean(x) for x in parts if x.startswith(("live_", "test_"))), "")
        if not sid or not key:
            return ("Не понял. Нужно: shopId (только цифры) и ключ, который начинается с live_ или test_, "
                    "через пробел. Пример: 123456 live_AbCd…"), "cltar"
        GCFG["yk_shop"], GCFG["yk_key"] = sid, key        # ключи из панели главнее переменных хостинга
        save_cfg()
        ok, why = yk_check(fresh=True)
        keep = ("" if yk_env_keys() == (sid, key) else
                "\n\n💾 Чтобы ключи не пропали при пересборке бота на хостинге, задай их и в переменных Bothost: "
                "YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY (тот же shopId и ключ).")
        return ("✅ ЮKassa " + why + "\nТеперь покупатели сразу получают ссылку на оплату." + keep if ok else
                "❌ ЮKassa: " + why) + tail, "cltar"
    if (action or "").startswith("claikeys:"):            # 🤖 ключи ИИ списком — панель
        if not can_panel(uid):
            return "Нет доступа.", "main"
        tail = secret_tail(uid)
        prov = action.split(":", 1)[1]
        if prov not in AI_PROVIDERS:
            return "Не знаю такой сервис." + tail, "clkeys"
        keys = parse_keys(text)
        if not keys:
            return "Не вижу ключей. Пришли их через запятую или каждый с новой строки." + tail, "clkeys"
        GCFG.setdefault("ai_keys", {})[prov] = keys
        save_cfg()
        AI_ROTATORS[prov].reset()                         # новый список — работает сразу, без перезапуска
        title = AI_PROVIDERS[prov][1]
        return (f"✅ Ключи {title} сохранены: {len(keys)} ({mask_keys(keys)}). Работают сразу, без перезапуска; "
                "упёрся в лимит — бот берёт следующий." + tail), "clkeys"
    if action in ("clvk", "cltt"):                        # 🔑 ключ VK / 🎵 cookies TikTok — панель
        if not can_panel(uid):
            return "Нет доступа.", "main"
        tail = secret_tail(uid)
        if action == "clvk":
            tok = text.strip()
            if not re.fullmatch(r"[\w.-]{20,}", tok):
                return "Не похоже на ключ VK. Пришли «Сервисный ключ доступа» целиком." + tail, "clkeys"
            GCFG["vk_token"] = tok
            what = f"Ключ VK сохранён ✅ (…{tok[-4:]}). Источники VK начнут читаться в течение 10 минут."
        else:
            cookies = parse_cookies(text)
            if not cookies:
                return ("Не похоже на cookies. Пришли экспорт из Cookie-Editor (JSON) или строку вида "
                        "«sessionid=…; tt_csrf_token=…»." + tail), "clkeys"
            GCFG["tt_cookies"] = cookies
            n = cookies.count("=")
            what = (f"Cookies TikTok сохранены ✅ ({n} шт." + (", есть sessionid" if "sessionid=" in cookies else
                    ", ⚠️ без sessionid — TikTok может не пустить") + "). Ролики начнут читаться в течение 10 минут.")
        save_cfg()
        SOCIAL_CACHE.clear()
        return what + tail, "clkeys"
    if action == "promo":
        if not HOST_MODE:
            return "Промокоды здесь не работают.", "main"
        return promo_apply(tenant_key(uid) or cur().owner, text, uid), "pay"
    if action.startswith("clpromo:"):
        if not can_panel(uid):
            return "Нет доступа.", "main"
        code = promo_norm(text)
        if len(code) < 3:
            return "Название промокода — хотя бы 3 буквы или цифры, например LETO50.", "clpr"
        if code in promos():
            return f"Промокод {code} уже есть — открыл его.", f"clpo:{code}"
        kind = "days" if action.endswith(":days") else "pct"
        promos()[code] = {"kind": kind, "value": PROMO_DEF[kind], "max": 10, "used": [],
                          "until": time.time() + 30 * 86400, "created": time.time()}
        save_cfg()
        return (f"✅ Промокод {code} создан: {promo_what(promos()[code])}, 10 активаций, действует 30 дней.\n"
                "Ниже можно всё поменять."), f"clpo:{code}"
    if action in ("clnew", "clmsg", "cltarset", "clpayset", "clcontact", "clfsadd") or action.startswith("clsay:"):
        if not can_panel(uid) or CLIENT_MODE:
            return "Нет доступа.", "main"
        return clients_text(action, text, uid)
    if action == "chadd" and need_rights(uid):
        return "Сначала подтверди права на контент.", "rights"
    if action == "chadd":
        return add_sources(text)
    if (action or "").startswith("clsrcadd:"):          # 📡 админ подключает источники теме покупателя
        if not can_panel(uid):
            return "Это кнопка админа бота.", "main"
        t = theme_by_id(action.split(":", 1)[1])
        if t is None:
            return "Тема не найдена (удалена?).", "cl"
        with theme_ctx(t):
            before = list(CFG["channels"])
            answer, _ = add_sources(text, admin=True)
            new = [c for c in CFG["channels"] if c not in before]
            if new:                                       # покупатель видит в журнале, откуда новые посты
                journal("📡 Продавец подключил источники: " + ", ".join(ch_label(c) for c in new))
        return answer, f"clsrt:{t.id}"
    if action == "themenew":
        name = text.strip()[:40]
        if not name:
            return "Нужно название.", "th"
        if (CLIENT_MODE or HOST_MODE) and len(my_themes(uid)) >= lim_themes():
            return (f"В твоём тарифе до {lim_themes()} {n_word(lim_themes(), 'темы', 'тем', 'тем')}. "
                    "Нужно больше — перейди на тариф больше 👇"), "pay"
        t = create_theme(name, owner=(tenant_key(uid) or "") if HOST_MODE else "")
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
        if t is _VOID:                                    # темы нет — не пишем «привязан», а ведём к темам
            return NO_THEME, "th"
        cid = resolve_chat_id(link)
        if not cid:                                       # закрытый канал (max.ru/join/…) — ищем среди каналов бота
            want = _norm_link(link)
            cid = next((c["chat_id"] for c in pick_channels(uid) if want and _norm_link(c["link"]) == want), None)
        t.link, t.chat_id = link, cid
        other = next((x for x in THEMES if x is not t and t.chat_id and x.chat_id == t.chat_id), None)
        if other is not None:                             # канал у другой темы — переносим, если он «твой»
            if other.owner == t.owner or any(c["chat_id"] == t.chat_id for c in pick_channels(uid)):
                other.chat_id = None
                with theme_ctx(other):
                    journal(f"🔁 Канал MAX перепривязан к теме «{t.name}» — у этой темы канала больше нет")
            else:
                t.chat_id = None
                save_cfg()
                print(f"Канал MAX {cid}: уже привязан к теме «{other.name}» другого кабинета — сюда не переношу")
                return ("⚠️ Этот канал MAX уже привязан к другому кабинету — сюда привязать его не могу. "
                        "Если канал твой, напиши в «❓ Поддержка»."), "thm"
        if t.chat_id:
            t.own.pop("bind_wait", None)
        else:
            t.own["bind_wait"] = time.time()              # 20 минут: любое слово в канале — и он привязан
        save_cfg()
        nxt = "ask:chadd" if not CFG["channels"] else "thm"
        if t.chat_id:
            return "Канал MAX найден и привязан ✅" + rights_note(t.chat_id), nxt
        BIND["theme"] = t.id
        return BIND_HINT, "thm"                           # только это — без вопроса об источниках следом
    if action in ("addts", "delts"):
        sites = [re.sub(r"^https?://(www\.)?", "", w.strip().lower()).split("/")[0] for w in re.split(r"[\s,;]+", text)]
        sites = [w for w in sites if "." in w]
        if action == "addts":
            added = [w for w in sites if w not in CFG["trusted_sites"]]
            CFG["trusted_sites"] += added
            save_cfg()
            return (f"Добавил: {fmt_words(added)}" if added else "Не нашёл сайтов (нужно вида site.com)."), "fl"
        before = len(CFG["trusted_sites"])
        CFG["trusted_sites"] = [w for w in CFG["trusted_sites"] if w not in sites]
        save_cfg()
        return f"Убрал: {before - len(CFG['trusted_sites'])}", "fl"
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
    if action == "addsw":
        added = [w for w in split_words(text) if w not in [x.lower() for x in CFG["stop_words"]]]
        CFG["stop_words"] += added
        save_cfg()
        return (f"Добавил: {fmt_words(added)}" if added else "Такие слова уже есть."), "sw"
    if action == "delsw":
        gone = split_words(text)
        before = len(CFG["stop_words"])
        CFG["stop_words"] = [x for x in CFG["stop_words"] if x.lower() not in gone]
        save_cfg()
        return f"Убрал слов: {before - len(CFG['stop_words'])}", "sw"
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
    if action == "gapx":
        m = re.search(r"\d+", text)
        if not m:
            return "Нужно число минут. Попробуй ещё раз.", "q"
        CFG["gap_min"] = max(0, min(720, int(m.group())))
        save_cfg()
        return "Готово.", "q"
    return "Не понял.", "main"


def do_action(act, uid=None):
    """Кнопки-действия. Возвращает экран, который показать после."""
    p = act.split(":")
    scr = "main"
    if p[0] == "cllgform":                                  # ⚖️ форма: ИП → ООО → самозанятый
        return legal_form_cycle(uid)
    if p[0] in ("buyerask", "buyercard", "codesexp") and HOST_MODE and can_panel(uid):   # 👥 🎟 — в панели хозяина
        return host_action(p, uid) or "cl"
    if p[0] == "ykchk":                                    # 🔎 Проверить ЮKassa — спросить заново, не из памяти
        YK_CHECK_CACHE.clear()
        return "cltar"
    if p[0].startswith("cl"):
        if HOST_MODE:
            return host_action(p, uid) or "cl"
        if not CLIENT_MODE:
            return clients_action(p, uid) or "main"
    if p[0] == "chbind":
        cid = int(p[1])
        c = next((x for x in pick_channels(uid) if x["chat_id"] == cid), None)
        if c is None:
            FLASH[uid] = "Этот канал недоступен: бот там не админ, канал уже привязан или он не твой."
            return "chpick"
        t = cur()
        if t is _VOID:
            FLASH[uid] = NO_THEME
            return "th"
        old = c.get("theme")
        if old is not None and old is not t:               # канал был у другой темы — переносим сюда
            old.chat_id = None
            with theme_ctx(old):
                journal(f"🔁 Канал MAX «{c['title']}» перепривязан к теме «{t.name}» — у этой темы канала больше нет")
                if HOST_MODE and old.owner != t.owner:
                    notify(f"🔁 Канал MAX «{c['title']}» больше не привязан к теме «{old.name}».")
        t.chat_id, t.link = cid, c["link"] or t.link
        t.own.pop("bind_wait", None)
        remember_chat(cid)
        DENIED.pop(cid, None)
        DENY_N.pop(cid, None)
        save_cfg()
        print(f"Канал MAX «{c['title']}» привязан к теме «{t.name}»: chat_id = {cid}")
        FLASH[uid] = f"✅ Канал «{c['title']}» привязан к теме «{t.name}»." + rights_note(cid)
        return "ch" if not CFG["channels"] else "thm"
    if p[0] == "pinnew":
        CFG["pin_new"] = not CFG.get("pin_new")
        return "pinset"
    if p[0] == "adh":
        CFG["autodel_h"] = max(0, min(24 * 30, int(p[1])))
        return "pinset"
    if p[0] == "chtest":
        FLASH[uid] = channel_test(cur().chat_id)
        return "thm"
    if p[0] == "pause":
        CFG["paused"] = not CFG["paused"]
    elif p[0] == "vid":
        CFG["video_on"] = not CFG["video_on"]
        scr = "vid"
    elif p[0] == "vmb":
        CFG["video_mb"] = max(10, min(250, CFG["video_mb"] + int(p[1])))
        scr = "vid"
    elif p[0] == "adok":                                   # «не реклама»: учим и выкладываем
        it = next((x for x in ADHELD if x["id"] == ":".join(p[1:])), None)
        if it is not None:
            ADHELD.remove(it)
            save_adheld()
            ad_learn(it.get("raw") or it.get("text"), it.get("buttons", 0), False)
            it.update(status="ok", manual=True, enq=time.time(), at=time.time())
            QUEUE.append(it)
            save_queue()
            journal(f"[{it['ch']}] 🧠 «не реклама» — запомнил и выкладываю пост {it['id']}")
        scr = "adl"
    elif p[0] == "adbad":                                  # «это реклама»: учим и удаляем из канала
        mid = ":".join(p[1:])
        post = next((x for x in PUB.get("day_posts", []) if x.get("mid") == mid), None)
        if post is not None:
            ad_learn(post.get("raw") or post.get("text"), post.get("buttons", 0), True)
            post["ad"] = True
            save_pub()
            with contextlib.suppress(Exception):
                delete_msg(mid)
            bump("ads")
            journal(f"[{post['ch']}] 🧠 «это реклама» — запомнил и удалил из канала")
        scr = "adl"
    elif p[0] == "allmode":
        m = GCFG.setdefault("all_mode", {})
        m[str(uid)] = not m.get(str(uid))
        scr = "th"
    elif p[0] == "aied":
        CFG["ai_edit"] = not CFG.get("ai_edit")
        scr = "ai"
    elif p[0] == "anl":
        FLASH[uid] = analytics_start(uid)
        scr = "anl"
    elif p[0] in ("anlcard", "anlnet") and feature_on("stats"):
        FLASH[uid] = anl_cards_start(uid, "net" if p[0] == "anlnet" else "chan")
        scr = "anl"
    elif p[0] == "anlbg":
        mode = ANL_BG[(ANL_BG.index(anl_bg_mode(uid)) + 1) % len(ANL_BG)]
        if not isinstance(GCFG.get("anl_bg"), dict):
            GCFG["anl_bg"] = {}                              # испорченная настройка — начинаем заново
        GCFG["anl_bg"][str(uid)] = mode
        save_cfg()
        FLASH[uid] = f"Фон карточек: {ANL_BG_NAMES[mode]}" + (
            "" if mode != "ai" or ai_keys("openrouter") else
            " — нужен ключ OpenRouter (🤖 ключи ИИ); пока ключа нет, беру фото дня")
        scr = "anl"
    elif p[0] == "predict" and feature_on("stats"):
        PENDING[uid] = "predict"
        scr = "predict"
    elif p[0] in ("trendtg", "trendnow", "trendoff"):
        FLASH[uid] = trend_action(p, uid)
        scr = "trends"
    elif p[0] in ("aismart", "aitags", "aiqual", "aidup", "aiads"):
        key = {"aismart": "smart_time", "aitags": "smart_tags", "aiqual": "smart_quality",
               "aidup": "dedupe_strict", "aiads": "ads_strict"}[p[0]]
        CFG[key] = not CFG[key]
        scr = "ai"
    elif p[0] == "adask":
        CFG["ads_ask"] = not CFG.get("ads_ask", True)
        scr = "fl"
    elif p[0] == "adsk":
        CFG["ads_skip"] = not CFG["ads_skip"]
        scr = "fl"
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
    elif p[0] == "age":
        opts = [6, 12, 24, 48]
        CFG["max_age_h"] = opts[(opts.index(CFG["max_age_h"]) + 1) % 4] if CFG["max_age_h"] in opts else 24
        scr = "q"
    elif p[0] == "qnow":
        FORCE["until"] = time.time() + 60
        scr = "ql"
    elif p[0] == "qapr":
        approve_all_pending(CFG["admin_chats"].get(str(uid)))
        scr = "ql"
    elif p[0] == "qcards":
        resend_pending_cards(CFG["admin_chats"].get(str(uid)), force=True, uid=uid)
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
        scr = "sr" if HOST_MODE and simple_menu(uid) else "rp"
    elif p[0] == "rhour":
        CFG["report_hour"] = (CFG["report_hour"] + int(p[1])) % 24
        scr = "sr" if HOST_MODE and simple_menu(uid) else "rp"
    elif p[0] == "repnow":
        send_report()
        scr = "sr" if HOST_MODE and simple_menu(uid) else "rp"
    elif p[0] == "test":
        FLAGS["test"] = True
        notify("🧪 Тест запущен: публикую последние посты каналов…")
    elif p[0] == "adinv" and CLIENT_MODE and len(CFG["admins"]) >= LIMIT_ADMINS:
        scr = "adfull"
    elif p[0] == "adinv":
        if is_owner(uid):
            code = str(random.randint(100000, 999999))
            INVITES[code] = time.time() + 900
            LAST_INVITE["code"] = code
            scr = "adcode"
        else:
            scr = "ad"
    elif p[0] == "adkickok":
        if is_owner(uid):
            gone = CFG["admins"][1:]
            del CFG["admins"][1:]
            for a in gone:
                CFG["admin_names"].pop(str(a), None)
                CFG["admin_chats"].pop(str(a), None)
            INVITES.clear()
            with contextlib.suppress(Exception):
                new_console_code()
            journal(f"🚨 Владелец убрал всех админов ({len(gone)}), коды приглашений сброшены")
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
        mine = my_themes(uid)
        if 0 <= i < len(mine):
            META["active"][str(uid)] = mine[i].id
            CUR.set(mine[i])
            save_cfg()                                     # выбор темы переживает перезапуск
        scr = "home" if HOST_MODE and is_host(uid) else "main"   # у хозяина «main» — это панель кабинетов
    elif p[0] == "thdel":
        if not cur().legacy:
            delete_theme(cur())
        scr = "th"
        scr = "q"
    elif p[0] == "later":
        CFG["later_min"] = max(5, min(720, CFG["later_min"] + int(p[1])))
        scr = "q"
    elif p[0] == "qdel":
        ref = ":".join(p[1:])
        it = next((x for x in QUEUE if x["id"] == ref), None)
        if it is not None:
            resolve_cards(item_refs([it]), CFG["admin_chats"].get(str(uid)))
            drop_item(it)
            bump("rejected")
            save_queue()
        scr = "ql"
    elif p[0] == "chdel":
        i = int(p[1])
        if 0 <= i < len(CFG["channels"]):
            drop_source(i)
        scr = "ch"
    save_cfg()
    return scr


SPREAD_SKIP = {"channels", "priority"}          # своё у каждой темы — «для всех тем» не копируем


def all_mode(uid):
    return len(THEMES) > 1 and bool(GCFG.get("all_mode", {}).get(str(uid)))


# списки слов: «для всех тем» переносит само изменение (добавили/убрали), свои слова каждой темы остаются
SPREAD_MERGE = {"ad_keywords", "old_signatures", "stop_words", "replacements", "trusted_sites"}
# своё у каждой темы: меняется только у тем, где было то же, что у этой (общая подпись), — чужая не затирается
SPREAD_OWN = {"signature_text"}
SPREAD_LABELS = {"ad_keywords": "рекламные слова", "old_signatures": "подписи доноров", "stop_words": "стоп-слова",
                 "replacements": "автозамены", "trusted_sites": "надёжные сайты",
                 "signature_text": "текст подписи", "signature_on": "подпись вкл/выкл", "gap_min": "пауза между постами",
                 "queue_on": "расписание", "video_on": "видео", "approval_on": "проверка постов",
                 "night_on": "ночной режим", "dedupe_on": "повторы", "ads_skip": "фильтр рекламы"}


LAST_SCREEN = {}       # админ -> экран меню, с которого он пришёл (туда ведёт «◀️ Назад» из ввода)
SPREAD_QUIET = {"chadd", "chdel", "thlink", "themenew", "thname", "trendwords"}   # своё у темы — «для всех» не про них


def prompt_nav(uid):
    """Кнопки под вводом: всегда «◀️ Назад» (откуда пришёл) и «❌ Отмена» (в меню) — без тупиков."""
    return [[B("◀️ Назад", "m:" + (LAST_SCREEN.get(uid) or "main")), B("❌ Отмена", "m:main")]]


def spread_value(mine_before, mine_after, theirs):
    """Список слов: в чужой теме убираем то, что убрали здесь, и добавляем то, что добавили; её слова — целы."""
    gone = [x for x in mine_before if x not in mine_after]
    new = [x for x in mine_after if x not in mine_before]
    kept = [x for x in theirs if x not in gone]
    return kept + [x for x in new if x not in kept]


def spread_to_all(uid, fn):
    """Режим «🌐 для всех тем»: в остальных темах этого же владельца меняем ТОЛЬКО то, что поменяли здесь.
    Остальные настройки каждой темы (свои слова, своя подпись, каналы) не трогаются."""
    if not all_mode(uid):
        return fn()
    here = cur()
    keys = [k for k in DEFAULTS if k not in GLOBAL_KEYS and k not in SPREAD_SKIP]
    before = {k: copy.deepcopy(CFG[k]) for k in keys}
    res = fn()
    if cur() is not here:                      # переключили тему — это не настройка
        return res
    changed = {k: copy.deepcopy(CFG[k]) for k in keys if CFG[k] != before[k]}
    if changed:
        scope = [t for t in THEMES if t.owner == here.owner]     # только темы этого владельца, не чужие кабинеты
        for t in scope:
            if t is here:
                continue
            with theme_ctx(t):
                for k, v in changed.items():
                    if k in SPREAD_MERGE and isinstance(v, list) and isinstance(CFG[k], list):
                        CFG[k] = spread_value(before[k], v, CFG[k])
                    elif k in SPREAD_OWN:
                        if CFG[k] == before[k]:
                            CFG[k] = copy.deepcopy(v)
                    else:
                        CFG[k] = copy.deepcopy(v)
        save_cfg()
        what = ", ".join(dict.fromkeys(SPREAD_LABELS.get(k, k) for k in changed))
        journal(f"🌐 Для всех тем ({len(scope)}): изменено только — {what}")
        note = (f"🌐 Применил ко всем темам ({len(scope)}): изменил только {what}, остальное у каждой темы — своё."
                + (" Текст подписи — только у тем с той же подписью." if SPREAD_OWN & set(changed) else ""))
        FLASH[uid] = (FLASH[uid] + "\n" + note) if FLASH.get(uid) else note
    return res


def handle_callback(cb):
    uid = (cb.get("user") or {}).get("user_id")
    cid = cb.get("callback_id")
    payload = cb.get("payload") or ""
    kind, _, rest = payload.partition(":")
    clear_transient(cb.get("_chat"), menus=False)          # нажал кнопку — промежуточные сообщения убираем
    if HOST_MODE and not is_host(uid):
        key = tenant_by_chat(cb.get("_chat")) or tenant_key(uid)
        if key and not tenant_live(key) and payload not in ("m:pay", "p:promo", "m:legal", "m:privacy", "m:terms",
                                                            "m:deldata") \
                and not payload.startswith(("py:", "pk:", "pd:", "pc:", "pe:", "lg:")):
            api_answer(cid, *host_lock_screen(uid))
            return
    if kind == "m":
        PENDING.pop(uid, None)
        LAST_SCREEN[uid] = rest
        text, rows = screen(rest, uid)
    elif kind == "a":
        try:
            scr = spread_to_all(uid, lambda: do_action(rest, uid))
        except (IndexError, ValueError, KeyError):         # старая кнопка: список уже поменялся
            _log_line(time.strftime("%H:%M:%S") + f" Меню: устаревшая кнопка {payload}")
            scr = {"addel": "fl", "chdel": "ch", "prio": "pr", "shdel": "shl", "qdel": "ql",
                   "thsw": "th"}.get(rest.split(":")[0], "main")
        if isinstance(scr, str):
            LAST_SCREEN[uid] = scr
        text, rows = screen(scr, uid)
        if uid in CODE_EXPORT and cb.get("_chat"):           # 📄 экспорт кодов — файлом в чат хозяина
            send_max(cb["_chat"], "📄 Активные коды — по одному в строке. Не пересылай их покупателям.", [],
                     file_path=CODE_EXPORT.pop(uid))
    elif kind == "ap":
        text, rows = approve(rest, uid, cb.get("_chat"))
    elif kind == "tg" and (NO_TG or (HOST_MODE and not is_host(uid))):   # общий аккаунт Telegram — только у хозяина
        text, rows = screen("main", uid)
    elif kind == "tg":
        PENDING.pop(uid, None)
        text, rows = tg_screen(uid) if rest == "menu" else tg_callback(rest, uid)
    elif kind == "fw":                                     # 📊 три кнопки канала и «🕘 Недавние проверки»
        text, rows = fwd_callback(rest, uid, cb.get("_chat"))
    elif kind == "lg":                                     # ⚖️ согласие, удаление данных, отписка от рекламы
        text, rows = legal_callback(rest, uid, cb.get("_chat"))
    elif kind == "py" and (CLIENT_MODE or HOST_MODE):      # выбрал тариф и срок
        text, rows = pay_choice(rest, uid)
    elif kind == "pd" and (CLIENT_MODE or HOST_MODE):      # «Я оплатил» / заявка
        text, rows = host_pay_manual(rest, uid) if HOST_MODE else pay_done(rest, uid)
    elif kind == "pe" and HOST_MODE:                       # «⏭ Пропустить» почту для чека — запасная почта
        PENDING.pop(uid, None)
        text, rows = host_pay_online(rest, uid)
    elif kind == "pc" and HOST_MODE:                       # «🔄 Проверить оплату» (ЮKassa)
        text, rows = host_pay_check(uid)
    elif kind == "pk" and (CLIENT_MODE or HOST_MODE):      # «Оплатить онлайн» (ЮKassa)
        text, rows = host_pay_online(rest, uid) if HOST_MODE else pay_online(rest, uid)
    elif kind == "p" and rest.startswith("cl") and HOST_MODE and not can_panel(uid):
        PENDING.pop(uid, None)                             # ввод панели — только для админов
        text, rows = screen("main", uid)
    elif kind == "rk":                                     # подтвердил права на контент — сразу к добавлению
        record_consent(uid)
        PENDING[uid] = "chadd"
        text, rows = "Спасибо, подтверждение сохранено ✅\n\n" + PROMPTS["chadd"], prompt_nav(uid)
        if feature_on("social"):
            text += SOCIAL_HINT
    elif kind == "p" and rest == "chadd" and need_rights(uid):
        PENDING.pop(uid, None)
        text, rows = RIGHTS_TEXT, rights_rows(uid)
    elif kind == "p" and rest not in PROMPTS and rest.split(":")[0] not in PROMPTS:
        PENDING.pop(uid, None)                             # старая кнопка ввода — такой настройки больше нет
        text, rows = screen("main", uid)
    elif kind == "p":
        PENDING[uid] = rest
        cancel = prompt_nav(uid)
        if rest == "thlink":
            cancel = [[B("📋 Выбрать канал из списка", "m:chpick")]] + cancel
        text, rows = PROMPTS.get(rest, PROMPTS.get(rest.split(":")[0])), cancel
        if all_mode(uid) and rest not in SPREAD_QUIET and not rest.startswith("cl"):
            text += ("\n\n🌐 Режим «для всех тем»: изменится только эта настройка — во всех твоих темах; "
                     "свои слова и подписи других тем останутся.")
        if rest == "chadd" and feature_on("social"):
            text += SOCIAL_HINT
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
    if verdict in ("adok", "adad"):                       # ответ на «Это реклама?»
        it = next((x for x in ADHELD if x["id"] == ref), None)
        if it is None:
            return "Этот пост уже обработан.", None
        ADHELD.remove(it)
        save_adheld()
        who = CFG["admin_names"].get(str(uid), "админ")
        resolve_cards([ref], chat or CFG["admin_chats"].get(str(uid)))
        if verdict == "adok":
            ad_learn(it.get("raw") or it.get("text"), it.get("buttons", 0), False)
            it.update(status="ok", manual=True, enq=time.time(), at=time.time())
            QUEUE.append(it)
            save_queue()
            journal(f"[{it['ch']}] 🧠 {who}: «не реклама» — запомнил и выкладываю пост {it['id']}")
            return f"✅ {who}: не реклама — выкладываю. Запомнил, похожие посты больше не спрошу.", None
        ad_learn(it.get("raw") or it.get("text"), it.get("buttons", 0), True)
        drop_files(it)
        bump("ads")
        journal(f"[{it['ch']}] 🧠 {who}: «реклама» — запомнил, пост {it['id']} не выкладываю")
        return f"🚫 {who}: реклама — не выкладываю. Запомнил, похожие буду отсеивать сам.", None
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
    items.sort(key=lambda x: x["date"])
    for k, it in enumerate(items):
        it["status"] = "ok"
        it["enq"] = now                       # возраст считаем от одобрения
        if k == 0 and mode in ("now", "later"):   # пачка не вываливается разом: первый — сейчас (или к сроку),
            it["manual"], it["at"] = True, (now if mode == "now" else at)   # остальные — по очереди с паузой
    rest = (f" Остальные — по очереди, по одному (пауза {fmt_min(CFG['gap_min'])})." if len(items) > 1 and CFG["queue_on"]
            else " Остальные — по очереди, по одному." if len(items) > 1 else "")
    if mode == "now":
        return done(f"✅ {who}: {many} из {names} — " + ("первый выкладываю сейчас." + rest if rest else "выкладываю сейчас."))
    if mode == "later":
        when = time.strftime("%H:%M", time.localtime(at))
        if time.localtime(at).tm_yday != time.localtime(now).tm_yday:
            when = "завтра в " + when
        return done(f"⏰ {who}: {many} из {names} — " + ("первый" if rest else "") + f" выйдет в {when} "
                    f"(через {fmt_min(round((at - now) / 60))}).{rest}")
    return done(f"✅ {who}: {many} из {names} — в очереди, выйдет по расписанию.")


def approve(rest, uid, chat=None):
    """Тема определяется по id поста/пачки из кнопки, а не по тому, какую тему открыл админ."""
    ref = rest.partition(":")[2]
    t = theme_of_ref(ref)
    if t is None:                                          # тему удалили («🗑 Очистить старые каналы» и т.п.)
        resolve_cards([ref])
        return "Этого поста уже нет — его тему удалили. Карточку убрал.", None
    if HOST_MODE and t not in my_themes(uid):              # чужой пост (другого кабинета) — не трогаем
        drop_card(ref, chat)
        return "Это пост не из твоих тем — не трогаю его. Карточку убрал.", None
    with theme_ctx(t):
        return _approve(rest, uid, chat)


# ---------- ВЛОЖЕНИЯ ИЗ СООБЩЕНИЙ ----------
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
        if a.get("type") in ("image", "video", "file", "audio"):
            pl = a.get("payload") or {}
            atts.append({"kind": a["type"], "url": pl.get("url"), "token": pl.get("token"),
                         "name": a.get("filename") or pl.get("filename") or ""})
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
    ("gap", "Пауза между постами в минутах, например /gap 45"),
    ("later", "Задержка кнопки «Через…», например /later 90"),
    ("theme", "Темы: /theme — список, /theme 2 — переключить"),
    ("warn", "Вкл/выкл предупреждение «на ближайший час постов нет»"),
    ("check", "Проверка: связь с MAX, Telegram, каналами"),
    ("log", "Журнал: что бот делал с постами"),
    ("report", "Прислать отчёт"),
    ("stats", "📊 Аналитика просмотров за 7 дней — прямо сейчас"),
    ("card", "🖼 Карточка канала; /card all — сводка по всем каналам"),
    ("predict", "🔮 Прогноз поста: оценка черновика до публикации"),
    ("trends", "🔥 Тренды: сигнал, когда по теме канала случилось вирусное"),
    ("recent", "🕘 Недавние проверки каналов (перешли пост из любого канала — посчитаю подписчиков)"),
    ("help", "Список команд"),
]


def guide_text():
    """Инструкция для покупателя: настройка по шагам и что делает каждая кнопка."""
    return ("📖 Как пользоваться\n\n"
            "Этот бот сам берёт новые посты из каналов Telegram и выкладывает их в твой канал MAX — "
            "с фото, видео и текстом.\n\n"
            "🚀 Настройка — 3 шага\n"
            "1️⃣ Добавь этого бота в свой канал MAX как админа (с правом публиковать).\n"
            "2️⃣ Нажми «📢 Куда выкладывать» и пришли ссылку на свой канал MAX (https://max.ru/…).\n"
            "3️⃣ Нажми «📡 Откуда брать посты» → «➕ Добавить канал» и пришли канал Telegram "
            "(@name или https://t.me/name).\n"
            "Готово — дальше бот работает сам, круглосуточно.\n\n"
            "🔘 Что делают кнопки\n"
            "📡 Откуда брать посты — каналы Telegram, из которых копировать. Нажми на канал — уберёшь его.\n"
            "📢 Куда выкладывать — твой канал MAX.\n"
            "⏳ Расписание — пауза между постами, чтобы не выкладывать всё разом.\n"
            "✋ Проверка постов — " + ("сейчас ВКЛ: " if CFG["approval_on"] else "сейчас выкл. Если включить, ")
            + "каждый новый пост сначала приходит тебе сюда с кнопками «✅ Сейчас», «⏰ Через…» и "
            "«❌ Пропустить». Не хочешь нажимать — выключи, и посты пойдут сами.\n"
            "✍️ Подпись — твой текст под каждым постом (например, название канала).\n"
            "⏸ Пауза — остановить публикации, ▶️ Продолжить — запустить снова.\n"
            + ("" if NO_TG else "🔑 Telegram (в «⚙️ Ещё настройки») — подключи свой аккаунт Telegram, чтобы "
               "переносились файлы (PDF и т.п.), большие видео и посты закрытых каналов. Нужны только номер и код.\n")
            + 
            "⚙️ Ещё настройки — видео, повторы, удаление рекламы, админы, журнал, 📄 условия.\n\n"
            "⚖️ Добавляй только свои каналы или те, на копирование которых у тебя есть разрешение владельца. "
            "Перед первым добавлением бот попросит это подтвердить.\n\n"
            "❓ Посты не выходят? Напиши продавцу — контакт ниже.\n"
            f"Вопросы и оплата: {SELLER_CONTACT}")


def client_main(uid=None):
    """Главное меню покупателя: что уже настроено, что сделать дальше и что делает каждая кнопка."""
    t = cur()
    me = seller_link() if HOST_MODE and not is_host(uid) else None      # личный чат с продавцом — ссылкой, не через бота
    has_ch = bool(CFG["channels"])
    soc = feature_on("social")
    src = "каналов Telegram" + (" и соцсетей" if soc else "")
    free = on_free(t)                                # 🆓 закреп и аналитика — у платных тарифов
    adv_stats = feature_on("stats") and not free
    if t.chat_id and has_ch:
        state = ("⏸ на паузе — посты не выходят, нажми «▶️ Продолжить»" if CFG["paused"] else
                 "▶️ работает — новые посты выходят сами")
        text = (f"⚙️ Меню бота · 🎭 тема «{t.name}»\n\n"
                f"Сейчас: {state}\n"
                f"📡 Источников: {len(CFG['channels'])} · ⏳ ждут выхода: {len(QUEUE)} · "
                f"✅ выложено: {STATS['published']}\n"
                f"✋ Проверка постов: " + ("вкл — каждый новый пост сначала приходит тебе на ✅/❌"
                                         if CFG["approval_on"] else "выкл — посты выходят без твоего участия"))
        if show_tg(uid) and (TG_CLIENT is None or is_web_client(TG_CLIENT)):
            text += ("\n\n📎 Чтобы переносились файлы, большие видео и закрытые каналы — подключи свой Telegram: "
                     "⚙️ Ещё настройки → 🔑 Telegram (номер и код).")
    else:
        text = (f"⚙️ Меню бота · 🎭 тема «{t.name}»\n\n"
                f"👋 Бот сам копирует НОВЫЕ посты из {src} в твой канал MAX. Осталось настроить 2 шага:\n\n"
                + ("✅ 1. Канал MAX привязан — сюда бот выкладывает посты.\n" if t.chat_id else
                   "1️⃣ Канал MAX — куда выкладывать. Добавь бота администратором в свой канал MAX, "
                   "потом нажми «📢 Куда выкладывать» и выбери канал.\n")
                + (f"✅ 2. Источников: {len(CFG['channels'])} — отсюда бот берёт посты.\n" if has_ch else
                   "2️⃣ Источники — откуда брать. Нажми «📡 Откуда брать посты» и пришли @канал или ссылку "
                   "t.me/канал" + (" (или ссылку на TikTok, VK, YouTube)" if soc else "") + ".\n")
                + "\nПосле этого всё работает само. Старые посты бот не переносит — только новые.")
    text += ("\n\nЧто делают кнопки:\n"
             f"📡 Откуда брать посты — {src}, из которых бот копирует посты\n"
             "📢 Куда выкладывать — твой канал в MAX\n"
             "⏳ Расписание — пауза между постами, чтобы не выходили пачкой\n"
             "✋ Проверка постов — показывать тебе пост перед выходом или нет\n"
             "✍️ Подпись — твой текст под каждым постом\n"
             + ("" if free else
                "📌 Закреп и автоудаление — закреплять новый пост, удалять старые через N часов\n")
             + ("📊 Аналитика — в какие дни и часы у канала больше просмотров\n" if adv_stats else "")
             + "⚙️ Ещё настройки — фильтр рекламы, видео, повторы, отчёт, темы\n"
             + ("💳 Продлить доступ — добавить дни подписки\n" if HOST_MODE else "💳 Тариф и оплата — продлить или сменить тариф\n")
             + "⏸ Пауза — остановить выход постов (всё сохранится)\n"
             + ("💬 Написать мне лично — откроет личный чат с продавцом в MAX\n" if me else "") + "\n"
             "Подробная инструкция — «📖 Как пользоваться».")
    text = (text.rstrip() + "\n\n" + subscription_line()).strip()
    pay_btn = [B("💳 Продлить доступ" if HOST_MODE else "💳 Тариф и оплата", "m:pay")]   # докупить дни — в любой момент
    rows = [[B("📖 Как пользоваться", "m:gd")],
            [B("📡 Откуда брать посты" + (" (Telegram, соцсети)" if soc else " (Telegram)"), "m:ch")],
            [B("📢 Куда выкладывать (канал MAX)", "m:thm" if t.chat_id else "m:chpick")],
            [B("⏳ Расписание", "m:q"), B("✋ Проверка постов", "m:ap")],
            [B("✍️ Подпись", "m:sg")],
            *([] if free else [[B("📌 Закреп и автоудаление", "m:pinset")]]),
            *([[B("📊 Аналитика: когда смотрят", "m:anl")]] if adv_stats else []),
            [B("⚙️ Ещё настройки", "m:more")] + pay_btn,
            [B("▶️ Продолжить" if CFG["paused"] else "⏸ Пауза", "a:pause")]]
    if me:
        rows.append([LB("💬 Написать мне лично", me)])
    if HOST_MODE and not is_host(uid):
        rows.append([B("👤 Мой профиль", "m:mprof")])
    return text, rows


def help_text(uid=None):
    hide = {"check"} if simple_menu(uid) else set()     # 🔎 проверки связи у покупателя нет
    own = ("\n\nДля хозяина:\n/ref — 🔗 Сгенерировать ссылку для клиента (партнёрская)\n"
           "/partner — ID партнёра, его ссылка и покупки клиентов") if uid is not None and can_panel(uid) else ""
    return "❓ Команды\n\n" + "\n".join(f"/{n} — {d}" for n, d in COMMAND_HELP if n not in hide) + own


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
        cmds = [(n, d) for n, d in COMMAND_HELP if not (HOST_MODE and n == "check")]   # видят и покупатели
        body = {"commands": [{"name": n, "description": d[:100]} for n, d in cmds[:32]]}
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
        send_screen(chat_id, m, rows)

    def say(text, rows=None):
        api_send(chat_id, text, rows)

    def num(lo, hi):
        m = re.search(r"\d+", arg)
        return max(lo, min(hi, int(m.group()))) if m else None

    pend = sum(1 for x in QUEUE if x.get("status") == "pending")
    if cmd == "start" and arg:
        ref_from_payload(uid, arg)                     # «/start ref_…» — пришёл по ссылке партнёра
    if cmd in ("partner", "ref", "reflink") and can_panel(uid):   # 🤝 партнёрская ссылка — только хозяин
        say(*ref_start(uid)) if cmd != "partner" else say(partner_screen(arg))
    elif HOST_MODE and can_panel(uid) and cmd in ("start", "menu", "m", "panel", "admin", "p"):
        show("cl")
    elif cmd in ("panel", "admin", "p"):
        if can_panel(uid) and not CLIENT_MODE:
            show("cl")
        else:
            say("Панель — только для администраторов бота.")
    elif cmd == "start" and CLIENT_MODE:
        say(*screen("gd", uid))                        # инструкция — обычным сообщением, меню — под ней
        show("main")
    elif cmd in ("start", "menu", "m"):
        if cmd == "start":
            send_welcome(chat_id)                      # 🎬 короткое видео «как это работает», затем меню
        show("main")
    elif cmd in ("help", "h", "commands"):
        show("hp")
    elif cmd in ("stats", "analytics", "an"):
        say(analytics_start(uid) if feature_on("stats") else "📊 Аналитика пока в бете — скоро будет у всех.")
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
                say("Очередь пуста.")
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
            snip = plain_snip(it.get("text"), 40)
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
    elif cmd == "cancel":
        show("main")
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
    elif cmd in ("channels", "kanaly") and can_panel(uid):
        say(channels_report())                          # 📢 каналы бота, привязки и события из каналов
    elif cmd in ("perf", "speed", "skorost") and can_panel(uid):
        say(perf_report(reset=arg.lower() in ("reset", "сброс")))   # ⏱ где бот тратит время
    elif cmd in ("predict", "prognoz") and feature_on("stats"):
        if arg:
            predict_start(uid, chat_id, arg)            # /predict текст поста — сразу прогноз
        else:
            PENDING[uid] = "predict"
            say(PREDICT_ASK, [[B("◀️ Аналитика", "m:anl")]])
    elif cmd in ("trends", "trend", "trendy") and feature_on("trends"):
        if arg.lower() in ("now", "сейчас", "check"):
            say(trend_check_now(uid))                   # 🔎 проверить тренды прямо сейчас
        else:
            show("trends")
    elif cmd in ("card", "kartochka") and feature_on("stats"):
        say(anl_cards_start(uid, "net" if arg.lower() in ("net", "all", "сеть", "все") else "chan"))
    elif cmd == "check" and simple_menu(uid):
        show("main")                                   # 🔎 проверки связи у покупателя нет — его меню
    elif cmd == "check":
        say("🔎 Проверяю связь, секунду…")
        say(run_diag())
    elif cmd == "log":
        show("lg")
    elif cmd == "report":
        say(make_report())
    elif cmd in ("recent", "checks"):
        show("fwr")
    elif cmd in ("legal", "yur", "rekvizity", "docs", "documents", "privacy", "deletedata"):
        if cmd in ("legal", "yur", "rekvizity") and legal_can_edit(uid):
            show("cllegal")                             # ⚖️ реквизиты — хозяину; остальным — документы
        else:
            show({"privacy": "privacy", "deletedata": "deldata"}.get(cmd, "legal"))
    else:
        show("hp")
    return True


def resend_pending_cards(chat, force=False, uid=None):
    """Админ появился (или сменил чат, или попросил): присылаем карточки постов, которые ждут одобрения.
    В host-режиме — только темы его кабинета (чужие посты покупателю не показываем)."""
    if not chat:
        return
    later = f"⏰ Через {fmt_min(CFG['later_min'])}"
    n = 0
    for t in (my_themes(uid) if HOST_MODE else list(THEMES)):
        with theme_ctx(t):
            for it in [x for x in QUEUE if x.get("status") == "pending"]:
                if not force and any(c["ref"] == it["id"] and c["chat"] == chat for c in CARDS):
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
    resend_pending_cards(chat_id, uid=uid)  # что накопилось без админа — показываем сразу


CODE_TRIES = {}         # user_id -> (неверных кодов подряд, время последнего)


CODE_FAILS = deque()    # когда вводили неверные коды (все вместе) — от подбора с многих аккаунтов
CODE_LOCK = {"until": 0}
CODE_LOCK_LIMIT = 20    # столько неверных кодов за час от всех — вход по кодам закрыт на час


def owner_alert(text):
    """Сообщение главному админу текущего кабинета (в host-режиме) или владельцу бота."""
    if CFG["admins"]:
        chat = CFG["admin_chats"].get(str(CFG["admins"][0]))
        if chat:
            in_background(_quiet_send, chat, text, None, name="owner-alert")   # из корутины — не держим цикл


def _quiet_send(chat, text, rows=None):
    with contextlib.suppress(Exception):
        api_send(chat, text, rows)


HOST_CHAT = {"chat": None}


def host_alert(text, rows=None):
    """Сообщение только тебе, хозяину бота (оплата, безопасность, сбои кабинетов)."""
    chat = HOST_CHAT.get("chat")
    if chat is None and not HOST_MODE and GCFG.get("admins"):
        chat = GCFG["admin_chats"].get(str(GCFG["admins"][0]))
    if chat:
        in_background(_quiet_send, chat, text, rows, name="host-alert")   # из корутины — не держим цикл


def note_wrong_code(uid, t, name=""):
    env = (os.environ.get("ADMIN_CODE") or "").strip()
    t = (t or "").strip()
    if re.fullmatch(r"\d{6}", t) or (len(env) >= 4 and len(t) == len(env) and " " not in t):   # похоже на код
        n, last = CODE_TRIES.get(uid, (0, 0))
        if time.time() - last >= 600:                   # пауза прошла — считаем заново
            n = 0
        CODE_TRIES[uid] = (n + 1, time.time())
        if n + 1 == 5:
            journal(f"🛡 Подбор кода: {name or uid} (ID {uid}) — 5 неверных кодов, пауза 10 минут")
            owner_alert(f"🛡 Кто-то подбирает код доступа к боту: {name or 'без имени'} (ID {uid}) ввёл 5 неверных "
                        "кодов. Я поставил его на паузу. Твой доступ не затронут.")
        now = time.time()
        CODE_FAILS.append(now)
        while CODE_FAILS and now - CODE_FAILS[0] > 3600:
            CODE_FAILS.popleft()
        if len(CODE_FAILS) >= CODE_LOCK_LIMIT and CODE_LOCK["until"] < now:
            CODE_LOCK["until"] = now + 3600
            journal(f"🛡 Подбор кода с разных аккаунтов ({len(CODE_FAILS)} за час) — вход по кодам закрыт на час")
            owner_alert(f"🚨 Похоже, бот пытаются взломать: {len(CODE_FAILS)} неверных кодов за час с разных "
                        "аккаунтов. Вход по кодам закрыт на час. Ты и твои админы работаете как обычно.\n\n"
                        "Если ты раздавал код приглашения — он мог утечь: 👥 Админы → проверь список.")


WELCOME_TEXT = ("👋 Привет! Я копирую посты из твоих Telegram-каналов в канал MAX. "
                "Короткое видео — как это работает. Меню ниже 👇")


def welcome_video_file():
    """Приветственное видео: файл из WELCOME_VIDEO_PATH или один раз скачанное по WELCOME_VIDEO_URL. Нет — None."""
    path = (APP_CONFIG.WELCOME_VIDEO_PATH or "").strip()
    if path and os.path.isfile(path):
        return path
    url = (APP_CONFIG.WELCOME_VIDEO_URL or "").strip()
    if not url:
        return None
    dest = os.path.abspath("welcome_video.mp4")
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return dest
    try:
        r = S.get(url, timeout=60)
        r.raise_for_status()
        if not r.content:
            return None
        with open(dest, "wb") as f:
            f.write(r.content)
        return dest
    except Exception as e:
        journal(f"🎬 Приветственное видео по ссылке не скачалось ({type(e).__name__}) — приветствую текстом")
        return None


def send_welcome(chat_id):
    """/start: видео (если есть), затем текст. Сбой отправки видео не мешает ни тексту, ни меню."""
    path = welcome_video_file()
    if path:
        try:
            send_max(chat_id, WELCOME_TEXT, [], video_path=path)
            return True
        except Exception as e:
            journal(f"🎬 Приветственное видео не отправилось ({type(e).__name__}: {str(e)[:120]}) — текстом")
    api_send(chat_id, WELCOME_TEXT)
    return False


def handle_message(uid, chat_id, name, text, msg=None):
    global CLAIM_CODE
    t = (text or "").strip()
    if t.lower() in ("/id", "id", "/whoami"):           # узнать свой user_id (для OWNER_ID) — доступно всем
        api_send(chat_id, f"Твой ID в MAX: {uid}")
        return
    if HOST_MODE and HOST_CODE["code"] and t.lower() == HOST_CODE["code"] and not is_host(uid):
        GCFG["host_uid"] = str(uid)                      # кто прислал код хозяина — тот и хозяин (не зависит от формата ID)
        if chat_id:
            HOST_CHAT["chat"] = chat_id                  # сюда хозяину уведомления
        host_adopt_orphans()
        new_host_code()                                 # старый код больше не действует
        print(f"HOST: хозяином стал user_id {uid} ({name}) по коду хозяина")
        api_send(chat_id, "Готово, ты теперь ХОЗЯИН бота ✅ Открываю панель всех кабинетов.")
        m, rows = screen("main", uid)
        send_screen(chat_id, m, rows)
        return
    if HOST_MODE and not is_host(uid) and not PENDING.get(uid) and looks_like_code(t) \
            and tenant_key(uid) is not None:
        return activate_by_message(uid, chat_id, t)          # 🔑 код тарифа: обычным сообщением, раньше меню
    if HOST_MODE and is_host(uid) and not PENDING.get(uid) and host_open_cabinet(uid, chat_id, t, msg):
        return                                          # переслал сообщение покупателя / прислал ID — его кабинет
    if HOST_MODE and not is_host(uid) and (phone := msg_contact(msg)) is not None:
        save_contact(uid, chat_id, name, phone, contact_vcf(msg))   # поделился контактом кнопкой — хозяину
        return
    if HOST_MODE and not can_panel(uid):                # кабинет без активной подписки — только оплата (и промокод)
        key = tenant_by_chat(chat_id) or tenant_key(uid)
        if key and not tenant_live(key) and PENDING.get(uid) != "promo":
            send_screen(chat_id, *host_lock_screen(uid))
            return
    if HOST_MODE and not is_admin(uid):                 # нет кабинета (новый id MAX, сбой): без кодов и гейта —
        track_cabinet(uid, chat_id, name)               # заводим сразу и дальше работаем только в СВОЁМ кабинете
        if is_admin(uid):
            with theme_ctx(active_theme(uid)):
                return handle_message(uid, chat_id, name, text, msg)
        return
    if not is_admin(uid) and is_global_admin(uid):      # ADMIN_IDS из переменных хостинга — админ сразу, без кодов
        add_admin(uid, chat_id, name)
    if not is_admin(uid):
        env_code = (os.environ.get("ADMIN_CODE") or "").strip()      # свой код из настроек хостинга
        tries = CODE_TRIES.get(uid, (0, 0))
        if tries[0] >= 5 and time.time() - tries[1] < 600:           # подбор кода: 5 ошибок — пауза 10 минут
            left = max(1, -(-int(600 - (time.time() - tries[1])) // 60))
            print("Меню: много неверных кодов от", uid, name, "— пауза", left, "мин")
            api_send(chat_id, f"Слишком много неверных кодов. Подожди {left} мин и отправь код снова.")
            return
        if CODE_LOCK["until"] > time.time():                          # подбор с многих аккаунтов — пауза всем
            left = max(1, -(-int(CODE_LOCK["until"] - time.time()) // 60))
            api_send(chat_id, f"Вход по кодам временно закрыт (защита от подбора). Попробуй через {left} мин.")
            return
        if FIXED_OWNER and not same_uid(uid, FIXED_OWNER) and (t == env_code or t == CLAIM_CODE):
            note_wrong_code(uid, t, name)               # код владельца у чужого — панель не отдаём
            api_send(chat_id, "У этого бота есть владелец. Доступ к управлению — только по личному приглашению "
                              "от него. Коды привязки тут не действуют.")
            return
        if env_code and len(env_code) >= 4 and t == env_code and not owner_locked_out(uid):
            add_admin(uid, chat_id, name)
            first = CFG["admins"][0] == uid
            print("Меню: по коду ADMIN_CODE добавлен", "владелец" if first else "админ", uid, name)
            api_send(chat_id, "Готово, ты владелец бота ✅" if first else "Готово, ты теперь админ бота ✅")
            m, rows = screen("main", uid)
            send_screen(chat_id, m, rows)
            if not first:
                notify(f"👥 Новый админ (ADMIN_CODE): {name or uid}")
        elif not CFG["admins"] and CLAIM_CODE and t == CLAIM_CODE and not owner_locked_out(uid):   # первый вход: владелец
            add_admin(uid, chat_id, name)
            CLAIM_CODE = None
            with contextlib.suppress(OSError):
                os.remove("claim_code.txt")
            print("Меню: владелец привязан, user_id =", uid)
            api_send(chat_id, "Готово, ты владелец бота ✅")
            if CLIENT_MODE:
                api_send(chat_id, *screen("gd", uid))
                api_send(chat_id, "Пользуясь ботом, ты принимаешь условия использования. Прочитать: "
                                  "⚙️ Ещё настройки → 📄 Условия.", [[B("📄 Условия", "m:terms")]])
            m, rows = screen("main", uid)
            send_screen(chat_id, m, rows)
        elif CFG["admins"] and CONSOLE_CODE["code"] and t == CONSOLE_CODE["code"]:   # код из консоли
            add_admin(uid, chat_id, name)
            print("Меню: добавлен админ по коду из консоли", uid, name)
            api_send(chat_id, "Готово, ты теперь админ бота ✅")
            m, rows = screen("main", uid)
            send_screen(chat_id, m, rows)
            notify(f"👥 Новый админ (код из консоли): {name or uid}")
            new_console_code()                                         # старый код больше не действует
        elif t in INVITES and INVITES[t] > time.time():                # приглашение от владельца
            del INVITES[t]
            add_admin(uid, chat_id, name)
            print("Меню: добавлен админ", uid, name)
            api_send(chat_id, "Готово, ты теперь админ бота ✅")
            m, rows = screen("main", uid)
            send_screen(chat_id, m, rows)
            notify(f"👥 Новый админ: {name or uid}")
        elif CLIENT_MODE:
            note_wrong_code(uid, t, name)
            api_send(chat_id, ("Привет! Это бот, который переносит посты из Telegram в MAX.\n\n"
                               "Чтобы им управлять, отправь сюда 6-значный код привязки — его тебе дал продавец "
                               f"({SELLER_CONTACT})." if not CFG["admins"] else
                               "Привет! Управлять этим ботом может только его владелец. Если ты помогаешь "
                               "владельцу — попроси у него код: 👥 Админы → «➕ Пригласить админа». Отправь код сюда."))
        elif not CFG["admins"]:
            note_wrong_code(uid, t, name)
            api_send(chat_id, "Привет! Чтобы управлять ботом, отправь код привязки — он в консоли или логах бота "
                              "(строка «отправь ему этот код»), или свой ADMIN_CODE из настроек хостинга.")
        else:
            note_wrong_code(uid, t, name)
            api_send(chat_id, "Нет доступа. Отправь код админа из консоли/логов бота, свой ADMIN_CODE из настроек "
                              "хостинга или попроси владельца прислать код приглашения.")
        return
    changed = False
    mine = not HOST_MODE or (cur().owner and cur().owner == tenant_key(uid))   # пишем только в СВОЙ кабинет
    if mine and CFG["admin_chats"].get(str(uid)) != chat_id:
        CFG["admin_chats"][str(uid)] = chat_id
        changed = True
    if mine and name and CFG["admin_names"].get(str(uid)) != name:
        CFG["admin_names"][str(uid)] = name
        changed = True
    if changed:
        save_cfg()
        resend_pending_cards(chat_id, uid=uid)
    content, atts = extract_content(msg) if msg else (t, [])
    if t.startswith("/"):                                  # быстрые команды
        cmd, _, arg = t[1:].partition(" ")
        run_command(cmd.split("@")[0].strip().lower(), arg.strip(), uid, chat_id)
        return
    action = PENDING.get(uid)
    if action == "reflink":                                # 🔗 ссылка для клиента: ждём его сообщение или ID
        if can_panel(uid):
            ref_input(uid, chat_id, msg, t)
        else:
            PENDING.pop(uid, None)
        return
    if not action and not (HOST_MODE and can_panel(uid) and session_atts(atts)) \
            and (src := fwdw.source_of(msg, t)) is not None:      # 📊 пост из любого канала / ссылка на канал
        in_background(fwd_menu, uid, chat_id, src, name="fwd-menu")
        return
    if action == "predict":                                # 🔮 черновик на оценку — не в очередь
        PENDING.pop(uid, None)
        predict_start(uid, chat_id, content, atts)
        return
    if action and action.startswith("tg_") and t:          # подключение Telegram: ключи, номер, код
        answer, rows = tg_text(uid, action, t, ((msg or {}).get("body") or {}).get("mid"))
        api_send(chat_id, answer, rows)
        return
    if action and action.startswith("payemail:"):         # 📧 почта для чека ЮKassa
        PENDING.pop(uid, None)
        email = t.strip().lower()
        ok = bool(EMAIL_RE.match(email)) and len(email) <= 254
        text, rows = host_pay_online(action.split(":", 1)[1], uid, email if ok else None)
        if not ok:
            text = "Это не похоже на email — чек придёт на почту продавца.\n\n" + text
        send_screen(chat_id, text, rows)
        return
    if action in ("cladpost", "cladmark"):                # 📢 рекламная рассылка — только хозяин
        PENDING.pop(uid, None)
        if can_panel(uid):
            ad_input(uid, chat_id, action, content, atts)
        else:
            send_screen(chat_id, *screen("main", uid))
        return
    if action == "clsess" or (HOST_MODE and can_panel(uid) and session_atts(atts)):   # 👤 .session — в пул
        if action == "clsess":                            # (и без «➕»: файл .session от хозяина — тоже в пул)
            PENDING.pop(uid, None)
        if not can_panel(uid):
            send_screen(chat_id, *screen("main", uid))
            return
        gone = True
        if atts:                                          # файл входа = доступ к аккаунту: из чата долой
            gone = False
            with contextlib.suppress(Exception):
                gone = bool(delete_msg(((msg or {}).get("body") or {}).get("mid")))
        text, rows = session_upload(uid, atts)
        if not gone:
            text = ("⚠️ Не смог удалить твоё сообщение с файлом из чата — удали его сам: зажми сообщение → "
                    "«Удалить». В нём полный доступ к аккаунту.\n\n" + text)
        send_screen(chat_id, text, rows)
        return
    if (action in ("clyk", "clvk", "cltt") or (action or "").startswith("claikeys:")) and can_panel(uid):   # ключи: из чата
        gone = False
        with contextlib.suppress(Exception):
            gone = delete_msg(((msg or {}).get("body") or {}).get("mid"))
        YK_MSG_GONE[uid] = gone
    PENDING.pop(uid, None)
    if action and t:
        answer, scr = spread_to_all(uid, lambda: apply_text(action, t, uid))
        if scr == "ask:chadd" and need_rights(uid):        # сначала подтверждение прав на контент
            api_send(chat_id, answer)
            api_send(chat_id, RIGHTS_TEXT, rights_rows(uid))
            return
        if scr.startswith("ask:"):                         # цепочка ввода: следующий вопрос
            PENDING[uid] = scr[4:]
            api_send(chat_id, answer + "\n\n" + PROMPTS[scr[4:]],
                     ([[B("📋 Выбрать канал из списка", "m:chpick")]] if scr == "ask:thlink" else [])
                     + [[B("⏭ Пропустить", "m:thm")]])
            return
        api_send(chat_id, answer)
        if uid in CODE_EXPORT:                              # 🎟 файл с кодами — только хозяину, приватный
            send_max(chat_id, "🎟 Коды одним файлом, по одному в строке. Не пересылай его покупателям.", [],
                     file_path=CODE_EXPORT.pop(uid))
        m, rows = screen(scr, uid)
        send_screen(chat_id, m, rows)
    else:
        m, rows = screen("main", uid)
        send_screen(chat_id, m, rows)


def rights_note(chat_id):
    """Сразу после привязки: может ли бот публиковать (в приватных каналах часто добавляют не админом)."""
    r = bot_rights(chat_id)
    if r is True:
        return "\n\nБот — админ канала, могу публиковать ✅"
    if r is False:
        return ("\n\n⚠️ Но я НЕ админ этого канала — публиковать не смогу. Открой канал в MAX → Админы → "
                "добавь бота админом и включи «Публиковать сообщения». Посты подождут.")
    return ""


def on_channel_message(rec, who=None, added=False):
    """Сообщение/добавление в канале MAX: привязываем канал к теме, которая его ждёт.
    В host-режиме — к активной теме написавшего (его кабинет), чтобы у каждого свой канал.
    added — бота только что добавили в канал: не вышло привязать — говорим добавившему, что делать."""
    cid = rec.get("chat_id")
    if not isinstance(cid, int):
        return
    bound = next((x for x in THEMES if x.chat_id == cid), None)
    if bound is not None:
        if chan_hint_due(cid, "bound"):
            print(f"Канал MAX {cid}: уже привязан к теме «{bound.name}» (кабинет {bound.owner or '—'})")
            chan_event(cid, who, f"уже привязан к «{bound.name}»")
        return
    if rec.get("chat_type") not in (None, "channel"):
        return
    if HOST_MODE:
        try:
            ensure_own_theme(who)                         # тем нет (очистил старые, удалил) — заведём и привяжем к ней
        except Exception:
            log_exc("тема кабинета")
        key = tenant_key(who) or (host_id() if is_host(who) else None)
        t = None
        if key is not None:
            t = active_theme(who)
            if t is None or t.owner != key or t.chat_id:
                t = next((x for x in tenant_themes(key) if not x.chat_id), None)
        link = ""
        if t is None:                                     # автор неизвестен (у постов канала его часто нет):
            info = chat_info(cid)                         # ищем тему, которая ждёт канал с этой же ссылкой
            link = _norm_link(info.get("link"))
            t = next((x for x in THEMES if not x.chat_id and x.link and link and _norm_link(x.link) == link), None)
            if t is None:                                 # как раньше: дали ссылку, бот не нашёл — любое слово
                t = waiting_theme(cid)                    # в канале привязывает его к теме, которая ждёт
        if t is None:
            title = chat_info(cid).get("title") or link or str(cid)
            chan_event(cid, who, "не привязан: нет темы, которая ждёт канал", title)
            print(f"Канал MAX {cid} «{title}»: {'бота добавили' if added else 'есть сообщение'}, но не нашлось темы, "
                  f"которая его ждёт (автор: {who or 'неизвестен'}, ссылка канала: {link or 'нет'}) — не привязываю")
            chat = user_chat(who) if key is not None else None
            if chat and (added or chan_hint_due(cid, "nofree")):   # все темы уже с каналом — не молчим, подсказываем
                with contextlib.suppress(Exception):
                    api_send(chat, f"📢 Вижу канал «{title}». У твоих тем уже есть каналы MAX — чтобы "
                                   "публиковать сюда, открой «🎭 Мои каналы» → «⚙️ Эта тема» → «📢 Канал MAX» "
                                   "и выбери этот канал.")
            return
    else:
        tid = BIND.get("theme")
        if not tid:
            return
        t = theme_by_id(tid)
        BIND["theme"] = None
    if t is None or t.chat_id:
        return
    t.chat_id = cid
    t.own.pop("bind_wait", None)
    remember_chat(cid, who)
    DENIED.pop(cid, None)
    DENY_N.pop(cid, None)
    if t is THEMES[0]:
        _save_channel_id(cid)
    save_cfg()
    print(f"Канал MAX привязан к теме «{t.name}»: chat_id = {cid}")
    chan_event(cid, who, f"привязан к «{t.name}»")
    with theme_ctx(t):
        notify("✅ Канал MAX привязан к этой теме." + (rights_note(cid) or " Публикую сюда."))


def on_bot_removed(u):
    """Бота убрали из канала темы — владельцу сразу: из какого канала и КТО убрал (часто это бот-«защитник»)."""
    cid = u.get("chat_id")
    t = next((x for x in THEMES if x.chat_id == cid), None)
    if t is None:
        return
    who = u.get("user") or {}
    name = who.get("name") or who.get("first_name") or who.get("username") or "неизвестно кто"
    bot_mark = " — это бот" if who.get("is_bot") else ""
    RIGHTS_TOLD[cid] = time.time()                 # часовая проверка не будет повторять то же самое
    with theme_ctx(t):
        journal(f"⛔ Бота убрали из канала MAX темы «{t.name}». Убрал: {name} (ID {who.get('user_id', '?')}){bot_mark}")
    with theme_ctx(t):                                   # сообщение — владельцу ЭТОЙ темы
        owner_alert(f"⛔ Бота убрали из канала MAX темы «{t.name}».\n\nКто убрал: {name}{bot_mark} "
                f"(ID {who.get('user_id', '?')}).\n\n"
                + ("Похоже, это бот-аналитик или «защитник» канала: у него есть право управлять админами. "
                   "Забери у него это право (канал → Админы → этот бот → права) или убери его, потом снова "
                   "добавь моего бота админом.\n\n" if who.get("is_bot") else
                   "Добавь бота админом снова: канал → Админы → Добавить.\n\n")
                + "Посты не пропадут — выложу, как только вернёшь права. ID канала я помню, заново привязывать "
                  "не нужно.")


UPD_STATS = Counter()     # какие события MAX присылал с запуска (для /channels): тип[:вид чата]


def handle_update(u):
    kind = u.get("update_type")
    with contextlib.suppress(Exception):
        ct = ((u.get("message") or {}).get("recipient") or {}).get("chat_type") if kind == "message_created" else ""
        UPD_STATS[f"{kind}:{ct}" if ct else str(kind)] += 1
    uid = None
    if kind == "message_callback":
        uid = ((u.get("callback") or {}).get("user") or {}).get("user_id")
    elif kind == "message_created":
        uid = ((u.get("message") or {}).get("sender") or {}).get("user_id")
    elif kind == "bot_started":
        uid = (u.get("user") or {}).get("user_id")
    if HOST_MODE:
        _chat = u.get("chat_id") if kind == "bot_started" else (
            (u.get("message") or {}).get("recipient") or {}).get("chat_id")
        if fsub_gate(u, kind, uid, _chat):                # 📌 не подписан на канал хозяина — только «Подписаться»
            return
        with contextlib.suppress(Exception):
            host_prepare(uid, _chat, u, kind)
        try:
            ensure_own_theme(uid)
        except Exception:
            log_exc("тема кабинета")
    with theme_ctx(active_theme(uid)):           # меню работает с темой, выбранной этим админом
        try:
            _handle_update(u, kind)
        except Exception:
            log_exc("обработка сообщения в меню")
            msg = u.get("message") or {}
            rec = msg.get("recipient") or {}
            chat = u.get("chat_id") if kind == "bot_started" else (
                rec.get("chat_id") if rec.get("chat_type") in (None, "dialog") or kind == "message_callback" else None)
            if chat and is_admin(uid):
                with contextlib.suppress(Exception):
                    api_send(chat, "⚠️ Не получилось — попробуй ещё раз. Если повторится, пришли /log тому, "
                                   "кто настраивает бота.", [[B("◀️ Меню", "m:main")]])


def _handle_update(u, kind):
    if kind == "message_callback":
        cb = dict(u.get("callback") or {})
        cb["_chat"] = ((u.get("message") or {}).get("recipient") or {}).get("chat_id")
        user = cb.get("user") or {}
        if HOST_MODE and not is_admin(user.get("user_id")):   # нет кабинета — заводим, кнопка работает в СВОЁМ
            track_cabinet(user.get("user_id"), cb["_chat"], user.get("first_name") or user.get("name") or "")
            if is_admin(user.get("user_id")):
                with theme_ctx(active_theme(user.get("user_id"))):
                    handle_callback(cb)
        elif is_admin(user.get("user_id")):
            handle_callback(cb)
    elif kind == "message_created":
        msg = u.get("message") or {}
        rec = msg.get("recipient") or {}
        if rec.get("chat_type") != "dialog":
            if chan_hint_due(rec.get("chat_id"), "raw"):
                print("MAX сообщение не из лички: " + json.dumps(msg, ensure_ascii=False)[:1500])
            on_channel_message(rec, (msg.get("sender") or {}).get("user_id"))   # привязка канала
            return
        sender = msg.get("sender") or {}
        name = sender.get("first_name") or sender.get("name") or ""
        handle_message(sender.get("user_id"), rec.get("chat_id"), name,
                       (msg.get("body") or {}).get("text") or "", msg)
    elif kind == "bot_removed":
        print("MAX bot_removed: " + json.dumps(u, ensure_ascii=False)[:1500])
        chan_event(u.get("chat_id"), (u.get("user") or {}).get("user_id"), "бота убрали (bot_removed)")
        forget_chat(u.get("chat_id"))
        on_bot_removed(u)
    elif kind == "bot_added":
        adder = (u.get("user") or {}).get("user_id")
        print("MAX bot_added: " + json.dumps(u, ensure_ascii=False)[:1500])
        remember_chat(u.get("chat_id"), adder)
        chan_event(u.get("chat_id"), adder, "бота добавили (bot_added)")
        back = next((x for x in THEMES if x.chat_id == u.get("chat_id")), None)
        if back is not None:                           # бота вернули в канал темы — сразу продолжаем
            DENIED.pop(back.chat_id, None)
            DENY_N.pop(back.chat_id, None)
            if RIGHTS_TOLD.pop(back.chat_id, None):
                with theme_ctx(back):                       # сообщение — владельцу ЭТОЙ темы, а не тому, кто сейчас пишет
                    journal("✅ Бота вернули в канал MAX — продолжаю публиковать")
                    owner_alert(f"✅ Бота вернули в канал темы «{back.name}» — продолжаю публиковать.")
        elif (u.get("is_channel") or u.get("chat_type") == "channel"
              or chat_info_cached(u.get("chat_id")).get("type") == "channel") and (HOST_MODE or not CFG["admins"] or is_admin(adder)):
            on_channel_message({"chat_id": u.get("chat_id"), "chat_type": "channel"}, adder, added=True)
    elif kind == "bot_started":
        user = u.get("user") or {}
        with contextlib.suppress(Exception):           # ?start=ref_<ID> — пришёл по ссылке партнёра
            ref_from_payload(user.get("user_id"), u.get("payload"), user.get("first_name") or user.get("name") or "")
        handle_message(user.get("user_id"), u.get("chat_id"),
                       user.get("first_name") or user.get("name") or "", "/start")


# ---------- ⚡ МЕНЮ БЕЗ ОЧЕРЕДИ: нажатия разных людей обрабатываются одновременно ----------
# Раньше события MAX шли строго по одному: пока бот ждал ЮKassa, MAX или ИИ для одного покупателя (до 20–60 с),
# кнопки не отвечали никому. Теперь у каждого пользователя своя «полоса»: его нажатия — по порядку, а чужие
# медленные дела его не держат.
MENU_LANES = max(1, min(16, env_int("MENU_LANES", 4)))
LANES = []                   # [queue.Queue] — по одной на полосу
LANES_LOCK = threading.Lock()
TYPING_AFTER_SEC = 1.0       # нажатие обрабатывается дольше — в чате «печатает…», чтобы было видно, что бот занят


def update_who(u):
    """(пользователь, личный чат с ним) из события MAX — по пользователю выбираем полосу."""
    kind = u.get("update_type")
    msg = u.get("message") or {}
    rec = msg.get("recipient") or {}
    if kind == "message_callback":
        return ((u.get("callback") or {}).get("user") or {}).get("user_id"), rec.get("chat_id")
    if kind == "message_created":
        dialog = rec.get("chat_type") in (None, "dialog")
        return (msg.get("sender") or {}).get("user_id"), (rec.get("chat_id") if dialog else None)
    if kind == "bot_started":
        return (u.get("user") or {}).get("user_id"), u.get("chat_id")
    return None, None


def update_label(u):
    """Короткое имя события для учёта скорости: «кнопка m:anl», «сообщение /perf», «бот добавлен»."""
    kind = u.get("update_type")
    if kind == "message_callback":
        pl = str((u.get("callback") or {}).get("payload") or "")
        return "кнопка " + ":".join(pl.split(":")[:2])[:24]
    if kind == "message_created":
        rec = (u.get("message") or {}).get("recipient") or {}
        if rec.get("chat_type") not in (None, "dialog"):
            return "сообщение в канале"
        t = ((u.get("message") or {}).get("body") or {}).get("text") or ""
        cmd = t.split()[0][1:].split("@")[0].lower() if t.startswith("/") and t.split() else ""
        if not cmd:
            return "сообщение текст"
        known = cmd in {n for n, _ in COMMAND_HELP} or cmd in PERF_COMMANDS
        return "сообщение /" + (cmd[:16] if known else "…")      # чужие «/абракадабра» — одной строкой
    return {"bot_started": "старт", "bot_added": "бот добавлен", "bot_removed": "бот убран"}.get(kind, str(kind))


def send_action(chat, action="typing_on"):
    """Значок в чате: «печатает…» (typing_on), «отправляет фото…» (sending_photo). Не вышло — не страшно."""
    if not chat:
        return False
    try:
        r = S.post(f"{MAX_API}/chats/{chat}/actions", headers=HEADERS, json={"action": action}, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


@contextlib.contextmanager
def chat_busy(chat, action="typing_on", every=4.0, after=0.0):
    """Пока бот занят долгим делом (ИИ, картинка, MAX) — в чате «печатает…». MAX гасит значок через
    несколько секунд, поэтому повторяем. after — показать, только если дело затянулось дольше."""
    stop = threading.Event()

    def loop():
        if after and stop.wait(after):
            return
        while not stop.is_set():
            send_action(chat, action)
            stop.wait(every)

    if chat:
        threading.Thread(target=loop, daemon=True, name="typing").start()
    try:
        yield
    finally:
        stop.set()


def run_update(u):
    """Одно событие меню с учётом времени: дольше 1 с — «печатает…», дольше 1,5 с — строка «🐢 Медленно» в лог
    с самыми долгими запросами (так видно, что тормозит: MAX, ЮKassa, ИИ или запись файлов)."""
    _PERF_LOCAL.calls = []
    t0 = time.perf_counter()
    try:
        with chat_busy(update_who(u)[1], after=TYPING_AFTER_SEC):
            handle_update(u)
    except Exception:
        log_exc("обработка сообщения в меню")
    finally:
        dt = time.perf_counter() - t0
        calls, _PERF_LOCAL.calls = _PERF_LOCAL.calls or [], None
        label = update_label(u)
        perf_add("upd", label, dt)
        if dt >= PERF_SLOW_SEC:
            top = sorted(calls, key=lambda c: -c[1])[:3]
            line = (f"🐢 Медленно: {label} — {dt:.1f} с" + ("; дольше всего: " + ", ".join(
                f"{k} {sec:.1f} с" for k, sec in top) if top else ""))
            with PERF_LOCK:
                PERF["slow"].append((time.time(), line))
            print(line)


def _lane_worker(q):
    while True:
        u = q.get()
        try:
            run_update(u)
        finally:
            q.task_done()


def dispatch_update(u):
    """Событие — в полосу его личного чата с ботом (MAX бывает шлёт одному человеку разные id, а чат один);
    нет личного чата — по пользователю, нет и его — по каналу."""
    with LANES_LOCK:
        if not LANES:
            for i in range(MENU_LANES):
                q = queue.Queue()
                LANES.append(q)
                threading.Thread(target=_lane_worker, args=(q,), daemon=True, name=f"menu-lane-{i}").start()
    who, chat = update_who(u)
    key = str(chat if chat is not None else who if who is not None else u.get("chat_id") or "")
    LANES[zlib.crc32(key.encode("utf-8")) % len(LANES)].put(u)


def lanes_wait(timeout=10.0):
    """Для проверок: дождаться, пока все полосы обработают события."""
    end = time.time() + timeout
    while time.time() < end and any(q.unfinished_tasks for q in LANES):
        time.sleep(0.02)
    return not any(q.unfinished_tasks for q in LANES)


def perf_report(reset=False):
    """/perf — где бот тратит время (с запуска или с прошлого «/perf сброс»)."""
    with PERF_LOCK:
        snap = {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in PERF.items() if k != "since"}
        mins = max(1, int((time.time() - PERF["since"]) / 60))
        if reset:
            for k, v in PERF.items():
                if k != "since":
                    v.clear()
            PERF["since"] = time.time()

    def top(kind, n, title):
        rows = sorted(snap[kind].items(), key=lambda kv: -kv[1][1])[:n]
        return ([title] + [f"• {k} — {c} раз, в среднем {tot / c:.2f} с, самый долгий {mx:.1f} с"
                           for k, (c, tot, mx) in rows] + [""]) if rows else []

    upd = snap["upd"].values()
    n, tot = sum(r[0] for r in upd), sum(r[1] for r in upd)
    lines = [f"⏱ Скорость бота за {mins} мин", "",
             f"Нажатий и сообщений: {n}" + (f", в среднем {tot / n:.2f} с" if n else "")
             + f"; меню обрабатывает {len(LANES) or MENU_LANES} пользователей одновременно", ""]
    lines += top("upd", 6, "🖱 Дольше всего (всего секунд):")
    lines += top("http", 8, "🌐 Запросы (MAX, ЮKassa, ИИ, t.me):")
    lines += top("ai", 4, "🤖 ИИ:")
    lines += top("tg", 3, "📡 Telegram:")
    lines += top("trend", 3, "🔥 Тренды (поиск новостей):")
    lines += top("json", 4, "💾 Запись файлов:")
    slow = snap["slow"][-5:]
    lines += ([f"🐢 Медленные нажатия (дольше {PERF_SLOW_SEC:.1f} с), последние:"]
              + [f"{time.strftime('%H:%M', time.localtime(t))} {line[2:]}" for t, line in slow]
              if slow else ["🐢 Медленных нажатий нет ✅"])
    if reset:
        lines += ["", "Счётчики обнулены."]
    return "\n".join(lines)


def menu_loop():
    """Слушает события бота MAX (кнопки и сообщения) — работает в отдельном потоке."""
    global CLAIM_CODE
    if HOST_MODE:
        load_host_code()
        print("HOST-режим готов: клиенты открывают твоего бота в MAX и оформляют доступ сами.")
        if GCFG.get("host_uid"):
            print(f"Хозяин привязан по коду (host_uid={GCFG.get('host_uid')}). OWNER_ID={HOST_OWNER or '—'}.")
        else:
            print("=" * 40)
            print("КОД ХОЗЯИНА: открой своего бота в MAX, нажми «Старт» и отправь ему ЭТОТ код —")
            print("этот аккаунт станет ХОЗЯИНОМ (панель всех кабинетов):", HOST_CODE["code"])
            print("Никому не показывай этот код.")
            print(f"(OWNER_ID={HOST_OWNER or '— не задан'} — тоже подходит, если совпадёт с твоим ID в MAX.)")
            print("=" * 40)
    elif not CFG["admins"]:
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
                      "types": "message_created,message_callback,bot_started,bot_added,bot_removed"}
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
                dispatch_update(u)                       # у каждого пользователя — своя «полоса»
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
    """Убирает пост из очереди и удаляет его фото, а также видео и файлы, скачанные заранее (пост ждал файл)."""
    for p in it.get("photos", []):
        path = p.get("path") if isinstance(p, dict) else p
        if path:
            with contextlib.suppress(OSError):
                os.remove(path)
    for v in it.get("vids", []):
        if isinstance(v, int):
            drop_file(video_path(it["ch"], v))
    for d in it.get("docs", []):
        if isinstance(d, dict) and d.get("name"):
            drop_file(doc_path(it["ch"], d))
    if it in QUEUE:
        QUEUE.remove(it)


VIDEO_RETRY_PAUSES = [2, 5, 10]   # повторы скачивания файла из Telegram (сек): 3 раза, коротко, молча
DL_TIMEOUT_MIN = 90               # одна попытка скачать файл из Telegram: 90 с + по 128 КБ/с на размер файла


class MediaLost(Exception):
    """Вложение поста потерялось или не скачалось: без решения админа пост без него не выходит."""


class MediaUploadFailed(MediaLost):
    """Файл не загрузился в MAX и после повторов: пост не выходит вовсе (без «огрызка» без файла)."""


class DownloadTimeout(Exception):
    """Попытка скачать файл из Telegram длилась дольше положенного (Telegram «завис»)."""


def media_size(msg):
    d = getattr(msg, "document", None) or getattr(msg, "video", None)
    return int(getattr(d, "size", 0) or 0)


async def tg_fetch(msg, dest, ch, what):
    """Одна попытка скачать вложение из Telegram — не дольше DL_TIMEOUT_MIN + размер / 128 КБ/с. В bot.log —
    размер и время. Зависло — DownloadTimeout («File download timed out after N s»)."""
    limit = DL_TIMEOUT_MIN + media_size(msg) / (128 * 1024)
    t0 = time.monotonic()
    try:
        path = await asyncio.wait_for(tg_media(msg, dest, ch), limit)
    except asyncio.TimeoutError:
        raise DownloadTimeout(f"File download timed out after {limit:.0f} s") from None
    if path and os.path.exists(path) and os.path.getsize(path) > 0:
        print(f"📎 {what}: скачано из Telegram ({_size(os.path.getsize(path))}, {time.monotonic() - t0:.1f} с)")
    return path


def dl_retry_note(what, n, attempts, err):
    print(f"📎 {what}: из Telegram не скачалось (попытка {n + 1}/{attempts}): {err}"
          + (f" — повтор через {VIDEO_RETRY_PAUSES[n]} с" if n < attempts - 1 else ""))


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


def is_cdn_error(e):
    """Telegram отдаёт файл только через свой CDN (так бывает у популярных каналов)."""
    t = f"{type(e).__name__} {e}"
    return "Cdn" in t or "CDN" in t or "MethodInvalid" in t


# ---------- CDN Telegram: скачиваем сами ----------
# Своя CDN-ветка Telethon 1.45 сломана: шлёт запрос к CDN в обычное подключение (ошибка CDN_METHOD_INVALID),
# подсовывает CDN-серверу ключ аккаунта, расшифровывает не тем шифром (IGE вместо CTR) и не закрывает подключения.
# С CDN-сервером говорим как официальные Telegram Desktop и TDLib: временный ключ на сутки с номером CDN-сервера
# (RSA_PAD), первый запрос — со знакомством (initConnection) без данных устройства.
CDN_PART = 128 * 1024                    # кусок файла с CDN (так Telegram считает и проверочные суммы)
CDN_TIMEOUT = 90                         # сек на файл с CDN (+ время на размер): CDN молчит — не ждём вечно
CDN_PAUSE = 1800                         # CDN не ответил — полчаса качаем сразу через t.me
CDN_KEY_TTL = 86400                      # ключ для CDN — временный, на сутки (как у Telegram Desktop)
CDN_STATE = {"down_until": 0, "why": ""}
CDN_KEYS = {}                            # CDN-сервер -> (ключ, сдвиг часов, когда сделан): ключ живёт сутки, как у TDLib


class TgCdnRedirect(RuntimeError):
    """Telethon получил «файл лежит на CDN» — качаем своим способом (tg_cdn_download)."""


class MediaCdnError(RuntimeError):
    """Файл с CDN Telegram не скачался ни своим способом, ни через t.me."""


async def _tl_cdn_off(self, cdn_redirect):
    raise TgCdnRedirect("файл на CDN Telegram — качаю своим способом")


if hasattr(TelegramClient, "_get_cdn_client"):           # отключаем сломанную ветку Telethon: сразу отказ, без
    TelegramClient._get_cdn_client = _tl_cdn_off         # висящих подключений, — и бот качает сам


def cdn_decrypt(data, key, iv, offset):
    """Кусок файла с CDN: AES-256-CTR, в IV последние 4 байта заменены на offset / 16."""
    iv16 = bytes(iv[:12]) + (offset // 16).to_bytes(4, "big")
    try:
        from Cryptodome.Cipher import AES as _AES       # быстрый (ставится вместе с yt-dlp[default])
        return _AES.new(bytes(key), _AES.MODE_CTR, nonce=b"", initial_value=iv16).decrypt(data)
    except ImportError:
        from telethon.crypto import AESModeCTR          # медленнее, но есть всегда (зависимость Telethon)
        return AESModeCTR(bytes(key), iv16).decrypt(data)


def _cdn_keys(cfg, dc_id):
    """Ключи CDN-сервера dc_id из help.getCdnConfig: {отпечаток MTProto: ключ}."""
    import rsa as pyrsa
    from telethon.tl.tlobject import TLObject
    tl_int = lambda x: TLObject.serialize_bytes(x.to_bytes((x.bit_length() + 7) // 8, "big"))
    keys = {}
    for pk in cfg.public_keys:
        if pk.dc_id == dc_id:
            k = pyrsa.PublicKey.load_pkcs1(pk.public_key.encode())
            keys[int.from_bytes(hashlib.sha1(tl_int(k.n) + tl_int(k.e)).digest()[-8:], "little", signed=True)] = k
    return keys


def _rsa_pad(key, data):
    """Шифрование RSA_PAD из MTProto (core.telegram.org/mtproto/auth_key, п. 4.1) — так шифруют официальные клиенты."""
    from telethon.crypto import AES
    padded = data + os.urandom(192 - len(data))
    while True:
        temp = os.urandom(32)
        enc = AES.encrypt_ige(padded[::-1] + hashlib.sha256(temp + padded).digest(), temp, bytes(32))
        block = int.from_bytes(bytes(a ^ b for a, b in zip(temp, hashlib.sha256(enc).digest())) + enc, "big")
        if block < key.n:
            return pow(block, key.e, key.n).to_bytes(256, "big")


async def _cdn_auth_key(conn, dc_id, keys, loggers):
    """Ключ для CDN-сервера: шаги обмена — из Telethon, но ключ временный, на сутки, с номером CDN-сервера
    (p_q_inner_data_temp_dc) и шифрование RSA_PAD — как у Telegram Desktop и TDLib. Telethon умеет только постоянный
    ключ без номера сервера и старое шифрование. Возвращает (ключ, сдвиг часов сервера)."""
    from telethon.crypto import rsa
    from telethon.network import MTProtoPlainSender, authenticator
    from telethon.tl.types import PQInnerDataTempDc
    code = authenticator.do_authentication.__code__
    if not {"PQInnerData", "rsa"} <= set(code.co_names):
        raise MediaCdnError("Telethon обновился — обмен ключами с CDN надо проверить")
    temp_dc = lambda **kw: PQInnerDataTempDc(dc=dc_id, expires_in=CDN_KEY_TTL, **kw)
    encrypt = lambda fp, data, use_old=False: _rsa_pad(keys[fp], data) if fp in keys else None
    handshake = types.FunctionType(code, dict(authenticator.do_authentication.__globals__, PQInnerData=temp_dc,
                                              rsa=types.SimpleNamespace(get_byte_array=rsa.get_byte_array,
                                                                        encrypt=encrypt)))
    await conn.connect(timeout=15)
    try:
        return await handshake(MTProtoPlainSender(conn, loggers=loggers))
    finally:
        await conn.disconnect()


def _cdn_hello(client, req):
    """Первый запрос к CDN — со знакомством (invokeWithLayer + initConnection) без данных устройства, как у Telegram."""
    from telethon.tl import functions
    from telethon.tl.alltlobjects import LAYER
    i = client._init_request
    return functions.InvokeWithLayerRequest(LAYER, functions.InitConnectionRequest(
        api_id=i.api_id, device_model="n/a", system_version="n/a", app_version=i.app_version,
        system_lang_code=i.system_lang_code, lang_pack="", lang_code="", query=req))


async def _cdn_dc(client, dc_id):
    """Адрес CDN-сервера. Список серверов Telethon берёт у Telegram один раз за запуск и не обновляет: сервера в нём
    нет — берём свежий список (как MadelineProto). Нет и в свежем — MediaCdnError с понятной причиной."""
    try:
        return await client._get_dc(dc_id, cdn=True)
    except ValueError:
        type(client)._config = None                       # Telethon перезапросит help.getConfig
    try:
        return await client._get_dc(dc_id, cdn=True)
    except ValueError:
        raise MediaCdnError(f"Telegram не дал адрес CDN-сервера {dc_id} (даже в свежем списке серверов)") from None


async def _cdn_sender(client, dc_id):
    """Отдельное подключение к CDN-серверу Telegram со своим ключом (вход в аккаунт CDN не нужен).
    Ключ берём сделанный раньше (живёт сутки), иначе договариваемся о новом."""
    from telethon import network
    from telethon.tl import functions
    dc = await _cdn_dc(client, dc_id)
    conn = lambda: client._connection(dc.ip_address, dc.port, dc.id, loggers=client._log,
                                      proxy=client._proxy, local_addr=client._local_addr)
    key, offset, made = CDN_KEYS.get(dc_id, (None, 0, 0))
    if key is None or time.time() - made > CDN_KEY_TTL - 3600:     # за час до конца суток — новый
        keys = _cdn_keys(await client(functions.help.GetCdnConfigRequest()), dc_id)
        key, offset = await _cdn_auth_key(conn(), dc_id, keys, client._log)
        CDN_KEYS[dc_id] = (key, offset, time.time())
    sender = network.MTProtoSender(None, loggers=client._log, retries=2, connect_timeout=15, auto_reconnect=False)
    sender.auth_key.key, sender._state.time_offset = key, offset
    await sender.connect(conn())
    return sender


async def _cdn_write(cdn, call, redirect, f, hello):
    """Читает файл с CDN кусками, расшифровывает и пишет в f. «Перезалей» (файла ещё нет на CDN) — просим DC.
    hello оборачивает первый запрос (знакомство с CDN-сервером)."""
    from telethon.tl import functions, types
    offset, reuploads = 0, 0
    while True:
        got = await cdn.send(hello(functions.upload.GetCdnFileRequest(redirect.file_token, offset, CDN_PART)))
        hello = lambda req: req
        if isinstance(got, types.upload.CdnFileReuploadNeeded):
            reuploads += 1
            if reuploads > 5:
                raise MediaCdnError("CDN Telegram так и не получил файл")
            await call(functions.upload.ReuploadCdnFileRequest(redirect.file_token, got.request_token))
            continue
        f.write(await asyncio.to_thread(cdn_decrypt, got.bytes, redirect.encryption_key, redirect.encryption_iv, offset))
        if len(got.bytes) < CDN_PART:
            return
        offset += CDN_PART


async def tg_cdn_download(msg, dest):
    """Вложение сообщения Telegram, которое лежит на CDN: запрос к DC файла, затем чтение с CDN. Путь к файлу."""
    from telethon import utils
    from telethon.tl import functions, types
    client = msg._client
    media = getattr(msg, "media", None) or msg            # настоящее сообщение Telethon — не types.Message:
    dc_id, loc = utils.get_input_location(media)          # get_input_location понимает только само вложение
    expected = getattr(getattr(media, "document", None), "size", None)
    home = dc_id in (None, client.session.dc_id)
    sender = client._sender if home else await client._borrow_exported_sender(dc_id)
    cdn = None
    try:
        call = lambda req: client._call(sender, req)
        redirect = await call(functions.upload.GetFileRequest(loc, offset=0, limit=CDN_PART, cdn_supported=True))
        if not isinstance(redirect, types.upload.FileCdnRedirect):
            raise MediaCdnError("Telegram не отправил файл на CDN — повторю обычным способом")
        cdn = await _cdn_sender(client, redirect.dc_id)
        try:
            with open(dest, "wb") as f:
                await _cdn_write(cdn, call, redirect, f, lambda req: _cdn_hello(client, req))
        except BaseException:
            CDN_KEYS.pop(redirect.dc_id, None)            # ключ мог устареть — следующий файл договорится заново
            raise
        if expected and os.path.getsize(dest) != expected:    # битый файл (apk, архив) хуже, чем никакого
            got = os.path.getsize(dest)
            os.remove(dest)
            raise MediaCdnError(f"файл с CDN пришёл не целиком ({got} из {expected} байт)")
        return dest
    finally:
        if cdn is not None:
            await cdn.disconnect()
        if not home:
            await client._return_exported_sender(sender)


async def cdn_check(client):
    """Самопроверка CDN Telegram с ЭТОГО сервера: свой ключ с CDN-сервером, знакомство и запрос заведомо чужого
    файла. CDN ответил «нет такого файла» — ключ и знакомство приняты, файлы с CDN качаются. (ok, текст)"""
    tg = getattr(client, "cur", client)
    cdn, dc_id = None, "?"
    try:
        from telethon.tl import functions
        cfg = await asyncio.wait_for(tg(functions.help.GetCdnConfigRequest()), CDN_TIMEOUT)
        if not cfg.public_keys:
            return False, "CDN Telegram: Telegram не назвал ни одного CDN-сервера"
        ids = [pk.dc_id for pk in cfg.public_keys]
        for cand in ids:                                  # проверяем сервер, адрес которого Telegram даёт этому аккаунту
            with contextlib.suppress(MediaCdnError):
                await _cdn_dc(tg, cand)
                dc_id = cand
                break
        else:
            return False, (f"CDN Telegram: Telegram не дал адрес ни одного CDN-сервера ({', '.join(map(str, ids))}) — "
                           "видео и файлы больших каналов скачаются только через t.me")
        cdn = await asyncio.wait_for(_cdn_sender(tg, dc_id), CDN_TIMEOUT)
        await asyncio.wait_for(cdn.send(_cdn_hello(tg, functions.upload.GetCdnFileRequest(b"selftest", 0, CDN_PART))),
                               CDN_TIMEOUT)
        CDN_STATE["down_until"] = 0
        return True, f"CDN Telegram (сервер {dc_id}): связь есть — видео и файлы больших каналов скачиваются"
    except asyncio.TimeoutError:
        CDN_KEYS.pop(dc_id, None)
        return False, f"CDN Telegram (сервер {dc_id}): не ответил за {int(CDN_TIMEOUT)} с"
    except Exception as e:
        answer = getattr(e, "message", None) if hasattr(e, "code") else None   # ответ CDN-сервера (RPCError)
        bad = answer is None or re.search(r"CONNECTION|AUTH_KEY|LAYER|API_ID", answer)
        if bad:
            CDN_KEYS.pop(dc_id, None)
        if answer is None:
            return False, f"CDN Telegram (сервер {dc_id}): {type(e).__name__}: {e}"[:200]
        if not bad:
            CDN_STATE["down_until"] = 0                # CDN отвечает — пауза после прошлого сбоя больше не нужна
        return not bad, (f"CDN Telegram (сервер {dc_id}): " + ("не принял подключение" if bad else "связь есть")
                         + f" (ответ: {answer})")
    finally:
        if cdn is not None:
            with contextlib.suppress(Exception):
                await cdn.disconnect()


async def cdn_check_report(client):
    """После запуска: проверяем CDN с этого сервера. Не работает — пишем хозяину (с точной причиной)."""
    ok, text = await cdn_check(client)
    journal(("✅ " if ok else "⚠️ ") + text)
    if not ok:
        await asyncio.to_thread(host_alert, f"⚠️ {text}\nВидео и файлы из больших каналов Telegram могут не "
                                            "скачиваться — посты будут ждать. Пришли это сообщение разработчику.")


async def tg_media(msg, dest, ch=None):
    """Вложение сообщения Telegram -> файл dest. С CDN качаем сами, запасной путь — t.me. Ошибка — MediaCdnError."""
    try:
        return await msg.download_media(file=dest)
    except Exception as e:
        if isinstance(e, WebMediaUnavailable) or not is_cdn_error(e):
            raise
    why = f"CDN не ответил в {CDN_STATE['why']}"         # пауза: показываем, что случилось в первый раз
    if time.time() >= CDN_STATE["down_until"]:
        size = getattr(getattr(getattr(msg, "media", None), "document", None), "size", 0) or 0
        limit = CDN_TIMEOUT + size / (256 * 1024)     # не медленнее ~256 КБ/с
        try:
            return await asyncio.wait_for(tg_cdn_download(msg, dest), limit)
        except Exception as e:
            hung = isinstance(e, (asyncio.TimeoutError, ConnectionError, OSError))
            why = f"не ответил за {int(limit)} с" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {e}"[:120]
            if hung:                                  # сервер CDN недоступен — не мучаем его на каждом файле
                CDN_STATE.update(down_until=time.time() + CDN_PAUSE, why=f"{time.strftime('%H:%M')}: {why}"[:90])
            print(f"[{ch}] {getattr(msg, 'id', '?')}: с CDN Telegram не скачалось ({why}), пробую через t.me")
    path = await web_media(ch, msg.id, dest) if ch else None
    if path:
        return path
    raise MediaCdnError(f"не скачалось с CDN Telegram ({why})")


async def web_media(ch, mid, dest):
    """Запасной путь: скачать вложение поста через t.me (открытые каналы). None — не вышло."""
    try:
        msg = await WebClient().get_messages(ch, ids=mid)
        if msg is None:
            return None
        path = await msg.download_media(file=dest)
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            print(f"[{ch}] {mid}: скачал через t.me (Telegram отдавал только через CDN)")
            return path
    except Exception as e:
        print(f"[{ch}] {mid}: через t.me тоже не скачалось: {e}")
    return None


async def download_photo(m, dest, ch=None):
    path = await tg_fetch(m, dest, ch, f"[{ch or '?'}] фото {getattr(m, 'id', '?')}")
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
    attempts = len(VIDEO_RETRY_PAUSES) + 1
    for i, p in enumerate(photos):
        if os.path.exists(p):
            out.append(p)
            continue
        got, err, n = None, "у поста нет номера фото — докачать нечем", 0
        what = f"[{it['ch']}] пост {it['id']}: фото {i + 1}/{len(photos)}"
        for n in range(attempts if i < len(ids) else 0):     # сбой Telegram — до 3 повторов, молча
            try:
                msg = await client.get_messages(it["ch"], ids=ids[i])
                if not msg or not has_image(msg):
                    err = "пост удалён или в нём больше нет фото"
                    break                                      # повторять бессмысленно
                got = await download_photo(msg, p, it["ch"])
                if got:
                    break
                err = "Telegram отдал пустой файл"
            except Exception as e:
                err = str(e) if isinstance(e, DownloadTimeout) else why(e)
            dl_retry_note(what, n, attempts, err)
            if n < attempts - 1:
                await asyncio.sleep(VIDEO_RETRY_PAUSES[n])
        if got:
            out.append(got)
        else:
            raise MediaLost(f"download failed: photo {i + 1} of {len(photos)} — {err}"
                            + (f" ({n + 1} attempts)" if i < len(ids) else ""))
    return out


def video_path(ch, vid):
    return f"{MEDIA_DIR}/{cur().fs}v_{ch}_{vid}.mp4"


def doc_path(ch, d):
    """Файл поста лежит в своей папке — с родным именем."""
    return os.path.join(f"{MEDIA_DIR}/{cur().fs}d_{ch}_{d['id']}", d["name"])


def have_whole(path, size):
    """Файл уже скачан целиком (в прошлую попытку поста) — второй раз не качаем."""
    return bool(size) and os.path.exists(path) and os.path.getsize(path) == size


async def download_video(client, ch, vid):
    """Качает видео поста из Telegram. Возвращает (путь, None) или (None, причина)."""
    os.makedirs(MEDIA_DIR, exist_ok=True)
    dest = video_path(ch, vid)
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
            elif have_whole(dest, getattr(video_doc(msg), "size", 0)):
                return dest, None
            else:
                path = await tg_fetch(msg, dest, ch, f"[{ch}] видео {vid}")
                if path and os.path.exists(path) and os.path.getsize(path) > 0:
                    return path, None
                err = "Telegram отдал пустой файл"
        except Exception as e:
            err = str(e) if isinstance(e, DownloadTimeout) else why(e)
            pause = max(pause, min(int(getattr(e, "seconds", 0) or 0), 120))   # FloodWait
        if os.path.exists(dest):                       # огрызок после обрыва
            try:
                os.remove(dest)
            except OSError:
                pass
        dl_retry_note(f"[{ch}] видео {vid}", n, attempts, err)
        if n < attempts - 1:
            await asyncio.sleep(pause)
    return None, f"{err} ({n + 1} attempts)"


async def download_doc(client, ch, d):
    """Качает файл поста из Telegram с его именем. Возвращает (путь, None) или (None, причина)."""
    dest = doc_path(ch, d)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    err = "неизвестная ошибка"
    attempts = len(VIDEO_RETRY_PAUSES) + 1
    for n in range(attempts):
        pause = VIDEO_RETRY_PAUSES[n] if n < len(VIDEO_RETRY_PAUSES) else 0
        try:
            msg = await client.get_messages(ch, ids=d["id"])
            if not msg:
                err = "пост удалён или недоступен"
            elif not getattr(msg, "document", None):
                err = "в посте нет файла"
                break
            elif have_whole(dest, getattr(msg.document, "size", 0)):
                return dest, None
            else:
                path = await tg_fetch(msg, dest, ch, f"[{ch}] файл {d['name']}")
                if path and os.path.exists(path) and os.path.getsize(path) > 0:
                    return path, None
                err = "Telegram отдал пустой файл"
        except WebMediaUnavailable as e:
            err = str(e)
            break
        except Exception as e:
            err = str(e) if isinstance(e, DownloadTimeout) else why(e)
            pause = max(pause, min(int(getattr(e, "seconds", 0) or 0), 120))   # FloodWait
        with contextlib.suppress(OSError):
            os.remove(dest)
        dl_retry_note(f"[{ch}] файл {d['name']}", n, attempts, err)
        if n < attempts - 1:
            await asyncio.sleep(pause)
    return None, f"{err} ({n + 1} attempts)"




async def fetch_files(client, it, parts):
    """Видео и файлы поста — на диск ДО первой отправки: {номер части: путь}.
    Не скачалось — MediaLost: пост пропускается целиком (текст без файла не выходит)."""
    files = {}
    for i, (kind, _, _, ref) in enumerate(parts):
        if i < it.get("sent", 0) or kind not in ("vid", "doc"):
            continue
        if kind == "vid":
            print(f"[{it['ch']}] качаю видео поста {ref}...")
            path, err = await download_video(client, it["ch"], ref)
            what = "video"
        else:
            path, err = await download_doc(client, it["ch"], ref)
            what = f"file {ref['name']}"
        if not path:
            raise MediaLost(f"download failed: {what} — {str(err)[:220]}")
        files[i] = path
    return files


class SendFailed(RuntimeError):
    """Часть поста с файлом не ушла в MAX и после повторов."""


def upload_parts(it, parts, files, only=None):
    """Все вложения поста — в MAX ДО первого сообщения (каждое — с повторами). Не загрузилось — MediaUploadFailed:
    пост не выходит вовсе. {номер части: (фото-токены, видео-токен, файл-токен)}; only — одна часть (перезагрузка)."""
    out = {}
    for i, (kind, _t, imgs, ref) in enumerate(parts):
        if i < it.get("sent", 0) or (only is not None and i != only):
            continue
        lbl = f"[{it['ch']}] пост {it['id']}"
        if kind == "img":
            out[i] = ([{"token": upload_retry("image", p, f"{lbl}: фото {k + 1}")} for k, p in enumerate(imgs)],
                      None, None)
        elif kind == "vid":
            out[i] = ([], {"token": upload_retry("video", files[i], f"{lbl}: видео")}, None)
        elif kind == "doc":
            out[i] = ([], None, {"token": upload_retry("file", files[i], f"{lbl}: файл {ref['name']}"), "kind": "file"})
    return out


def drop_file(path):
    """Скачанное видео или файл — с диска. Файл лежит в своей папке внутри MEDIA_DIR — убираем и её."""
    if not path:
        return
    folder = os.path.dirname(os.path.abspath(path))
    if os.path.dirname(folder) == os.path.abspath(MEDIA_DIR):
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
    else:
        with contextlib.suppress(OSError):
            os.remove(path)


def item_has_media(it):
    return bool(it.get("photos") or it.get("vids") or it.get("docs"))


def admin_log(text):
    """Сбой с файлами и повторы — только админу бота: bot.log и его журнал. Покупатель этого не видит — ни в чате,
    ни в своём «📜 Журнал»: в host-режиме запись без кабинета видна только хозяину (с пометкой, чей кабинет);
    у бота клиента — bot.log (продавец смотрит его в «💼 Клиенты» → клиент → «📜 Журнал»)."""
    t = cur()
    if HOST_MODE and t.owner:
        text += f" · кабинет «{(TENANTS.get(t.owner) or {}).get('name') or t.owner}» ({t.owner}), тема «{t.name}»"
    if CLIENT_MODE:
        print(text)
        return
    tok = CUR.set(None) if HOST_MODE else None
    try:
        journal(text)
    finally:
        if tok is not None:
            CUR.reset(tok)


def rollback_post(it):
    """Пост не вышел целиком — уже отправленные его части убираем из канала: «огрызка» без файла не остаётся."""
    mids = [m for m in it.get("mids") or [] if m]
    for mid in mids:
        with contextlib.suppress(Exception):
            delete_msg(mid)
    if mids:
        print(f"📎 [{it['ch']}] пост {it['id']}: убрал из канала уже отправленные части ({len(mids)}) — "
              "пост не вышел целиком")
    it.update(sent=0, mids=[], mid0=None)


def skip_post(it, reason):
    """Пост пропускаем целиком: причина — в журнал админа. Без текста без вложения, без ссылок, без кнопок, без
    сообщений покупателю."""
    admin_log(f"📎 [{it['ch']}] пост {it['id']}: Skipped: {reason}")
    resolve_cards([it["id"]])                        # старые карточки этого поста — убрать
    drop_item(it)
    save_queue()
    return "dropped"


def drop_legacy_stuck():
    """Посты «ждут файла» из прежней версии — пропускаем по новому правилу. Возвращает их число."""
    old = [it for it in QUEUE if it.get("status") == "stuck"]
    for it in old:
        skip_post(it, f"download failed (из прежней очереди: {str(it.get('stuck_why') or '')[:80]})")
    return len(old)


def media_cap_mb():
    """Лимит файла для пропуска: настройка «Видео», но не выше MAX_MEDIA_MB."""
    return min(int(CFG["video_mb"] or MAX_MEDIA_MB), MAX_MEDIA_MB)


async def publish_item(client, chat_id, it):
    """Публикация поста с ограничением по времени: зависший пост (файл, сеть) не держит очередь канала."""
    t0 = time.monotonic()
    try:
        return await asyncio.wait_for(_publish_item(client, chat_id, it), PUBLISH_TIMEOUT)
    except asyncio.TimeoutError:
        if time.monotonic() - t0 < PUBLISH_TIMEOUT * 0.99:
            raise
        it["retry_at"] = time.time() + 600
        save_queue()
        admin_log(f"⏱ [{it['ch']}] пост {it['id']} публиковался больше {PUBLISH_TIMEOUT // 60} мин (зависло) — "
                  "прервал, повторю через 10 мин; остальные посты выходят")
        return "retry"
    finally:
        perf_add("tg", "публикация поста в MAX (с файлами)", time.monotonic() - t0)


async def _publish_item(client, chat_id, it):
    """'ok', 'retry' или 'dropped' (пост пропущен целиком). Отправленные части не шлёт повторно."""
    client = src_client(client, it.get("ch", ""))
    async def snd(*args):
        mid = await asyncio.to_thread(send_max, *args)
        if mid and not it.get("mid0"):
            it["mid0"] = mid                           # первое сообщение поста — по нему просмотры в отчёте
        if mid:
            it.setdefault("mids", []).append(mid)      # все части поста — чтобы автоудаление убрало его целиком
        return mid
    try:
        body = it["text"]
        if ai_on() and ai_rewrite_allowed() and ai_llm_ready() and (body or "").strip():
            if not it.get("ai_text"):                  # пересказ — один раз: повтор отправки его не повторяет
                try:
                    it["ai_text"] = await asyncio.to_thread(ai_rewrite, body)
                    save_queue()
                except AiUnavailable:                  # все ключи ИИ 429 — выкладываем оригинал, пост не теряем
                    journal(AI_EXHAUSTED_LOG)
            body = it.get("ai_text") or body
        chunks = split_text(compose_text(body, it.get("ch", ""), rewritten=bool(it.get("ai_text"))))
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
        for d in it.get("docs", []):
            parts.append(("doc", text, [], d))
            text = ""
        if not parts:
            parts.append(("txt", text, [], None))
        parts += [("txt", c, [], None) for c in extra]       # хвост длинного текста — отдельными сообщениями
        files = await fetch_files(client, it, parts)   # всё — ДО первой отправки: пост не выходит кусками
        media = item_has_media(it)
        att = await asyncio.to_thread(upload_parts, it, parts, files) if media else {}   # и в MAX — тоже до неё

        async def send_retry(i, t):
            """Часть поста с файлом: MAX перегружен (5xx/429), обрыв, «принял, но потерял вложение» — тихо
            повторяем до 3 раз; потерянное вложение загружаем заново. Не вышло — SendFailed (пост откатим)."""
            attempts = len(MAX_RETRY_PAUSES) + 1
            for n in range(attempts):
                imgs_, vid_, fl_ = att.get(i, ([], None, None))
                try:
                    return await snd(chat_id, t, imgs_, vid_, *([fl_] if fl_ else []))
                except ChatDenied:
                    raise
                except Exception as e:
                    if not is_temporary(e):
                        raise
                    err, last = why(e) if not isinstance(e, MaxTemporary) else str(e)[:220], n == attempts - 1
                    print(f"📎 [{it['ch']}] пост {it['id']}, часть {i + 1}/{len(parts)}: MAX не принял сообщение "
                          f"(попытка {n + 1}/{attempts}): {err}" + ("" if last else f" — повтор через {MAX_RETRY_PAUSES[n]} с"))
                    if last:
                        raise SendFailed(f"part {i + 1} of {len(parts)} — {err} ({attempts} attempts)") from e
                    await asyncio.sleep(MAX_RETRY_PAUSES[n])
                    if isinstance(e, AttachmentsLost):     # вложение потерялось — загружаем его заново
                        att.update(await asyncio.to_thread(upload_parts, it, parts, files, i))

        for i, (kind, t, imgs, ref) in enumerate(parts):
            if i < it.get("sent", 0):
                continue                           # эта часть уже ушла в прошлую попытку
            if media:
                await send_retry(i, t)
            else:
                await snd(chat_id, t, imgs, None)
            if kind in ("doc", "vid"):
                drop_file(files.get(i))
            it["sent"] = i + 1
            save_queue()
        if it.get("waits"):
            resolve_cards([it["id"]])                  # дождались файла — карточка «Что делать?» больше не нужна
            admin_log(f"[{it['ch']}] пост {it['id']}: файл скачался с {it['waits'] + 1}-й попытки — вышел целиком")
        bump("published")
        note_allowed(chat_id)
        record_pub(it.get("mid0"), it["ch"], it.get("text", ""), it.get("raw"), it.get("buttons", 0), it.get("mids"))
        journal(f"[{it['ch']}] опубликован пост {it['id']}")
        return "ok"
    except MediaLost as e:
        await asyncio.to_thread(rollback_post, it)    # «огрызок» прошлой попытки (если был) — из канала
        return skip_post(it, str(e))
    except ChatDenied as e:
        await asyncio.to_thread(note_denied, chat_id, e)
        it["retry_at"] = DENIED.get(chat_id, time.time() + 600)
        save_queue()
        return "retry"
    except Exception as e:
        if item_has_media(it):                        # файл не ушёл в MAX и после повторов — пост не выходит вовсе:
            await asyncio.to_thread(rollback_post, it)  # уже отправленные части — из канала
            return skip_post(it, f"upload failed: {str(e)[:300]}")
        if is_temporary(e):
            retry_later(it, e, f"[{it['ch']}] пост {it['id']}")
            save_queue()
            return "retry"
        it["fails"] = it.get("fails", 0) + 1
        bump("errors")
        print(f"[{it['ch']}] ошибка публикации {it['id']} (попытка {it['fails']}):", e)
        if it["fails"] >= 3:
            drop_item(it)
            save_queue()
            journal(f"[{it['ch']}] пост {it['id']}: Skipped: publish failed ({str(e)[:150]})")
            return "dropped"
        it["retry_at"] = time.time() + 60
        save_queue()
        return "retry"


def prio_key(x):
    """Порядок публикации — по времени поста (старые раньше)."""
    return x["date"]


def mark_published(it):
    """Запоминает публикацию (для расписания) и убирает пост из очереди."""
    ch = it["ch"]
    PUB["ch_last_date"][ch] = max(PUB["ch_last_date"].get(ch, 0), it["date"])
    PUB["ch_last_pub"][ch] = time.time()
    PUB["last_pub"] = time.time()
    save_pub()
    drop_item(it)
    save_queue()


async def publish_due(client, chat_id):
    now = time.time()
    if not theme_live(cur()):
        return                                       # кабинет выключен или тема сверх бесплатного тарифа — не публикуем
    if DENIED.get(chat_id, 0) > now:
        return                                       # нет прав на канал — ждём (админам уже сказали)
    limit = CFG["max_age_h"] * 3600
    drop_legacy_stuck()                              # старые «ждущие файла» — пропускаем по новому правилу
    stale = [it for it in QUEUE if not it.get("manual") and now - it["enq"] > limit]
    for it in stale:
        drop_item(it)
        bump("expired")
        journal(f"[{it['ch']}] пост {it['id']} простоял в очереди больше {CFG['max_age_h']} ч — удалён "
                f"(очередь не успевает: уменьши паузу в «⏳ Расписание»)")
    if stale:
        save_queue()
        resolve_cards(item_refs(stale))
    free = on_free()
    force = now < FORCE["until"] and not free
    ready = [x for x in QUEUE if x.get("status", "ok") == "ok"
             and x.get("retry_at", 0) <= now and x.get("at", 0) <= now]

    # 1) посты с кнопок «Сейчас» / «Через …»: выходят в своё время, без паузы и ночного режима.
    #    Просрочились больше чем на 10 минут (бот был выключен или обновлялся) — в обычную очередь по одному.
    for x in ready:
        if x.get("manual") and x.get("at") and now - x["at"] > MISSED_SEC:
            x["manual"] = False
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
        return

    # 2) обычные посты по расписанию: один пост за паузу (старые раньше)
    if smart_hold(now, ready, limit):                # 🧠 умное время: холодный час — ждём лучшего
        return
    gap = max(CFG["gap_min"] * 60 if CFG["queue_on"] else 0, MIN_GAP_SEC)   # даже без расписания — не пачкой
    if free:
        gap = max(gap, FREE_PLAN["gap_min"] * 60)
    if force or gap == 0:
        due = sorted(ready, key=prio_key)
    elif now - last_slot() >= gap:
        due = [min(ready, key=prio_key)]
    else:
        due = []
    for it in due:
        res = await publish_item(client, chat_id, it)
        if res == "retry":
            break
        if res == "dropped":
            continue
        mark_published(it)


MISSED_SEC = 600          # «Сейчас»/«Через», просроченные дольше (бот был выключен), — в обычную очередь
MIN_GAP_SEC = 60          # между постами хотя бы минута, даже без расписания: после перезапуска — не пачкой
START = time.time()
TICK = 5               # как часто планировщик проверяет очередь, секунд


def upcoming_posts(now, horizon=3600):
    """Сколько постов выйдет в ближайший час: назначенные и по расписанию."""
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
    later = [x["at"] for x in QUEUE if x.get("status", "ok") == "ok" and x.get("manual") and x.get("at", 0) > now]
    lines = ["📭 На ближайший час постов нет."]
    lines.append("Очередь пуста." if not QUEUE else f"В очереди: {len(QUEUE)}, но в этот час ничего не выйдет.")
    if later:
        lines.append("Следующий назначен на " + time.strftime("%H:%M", time.localtime(min(later))) + ".")
    if pend:
        lines.append(f"Ждут одобрения: {pend} — /approve")
    notify("\n".join(lines))


async def scheduler(client, t):
    """Планировщик одной темы: раз в несколько секунд проверяет, не пора ли что-то публиковать."""
    CUR.set(t)
    while t in THEMES:
        await asyncio.sleep(TICK)
        if CFG["paused"] or not t.chat_id:
            continue
        if not theme_live(t):
            continue
        try:
            async with t.lock:
                await publish_due(client, t.chat_id)
            await asyncio.to_thread(check_empty, time.time())
        except Exception:
            log_exc(f"планировщик «{t.name}»")


RIGHTS_TOLD = {}       # канал MAX -> когда сказали, что бот там больше не админ
RIGHTS_CHECK = {"next": 0}


def check_rights_all(now=None):
    """Раз в час тихо спрашиваем MAX, админ ли бот в каналах тем. Ничего не публикуем."""
    now = now or time.time()
    for t in list(THEMES):
        if not t.chat_id:
            continue
        r = bot_rights(t.chat_id)
        if r is False:                                   # перепроверяем: MAX иногда отвечает случайно
            time.sleep(RIGHTS_RECHECK_SEC)
            r = bot_rights(t.chat_id)
        with theme_ctx(t):
            if r is False and now - RIGHTS_TOLD.get(t.chat_id, 0) > 6 * 3600:
                RIGHTS_TOLD[t.chat_id] = now
                journal("⛔ Проверка прав: бот больше не админ канала MAX этой темы")
                notify("⛔ Проверка прав: бот больше не админ канала MAX этой темы — посты выходить не смогут.\n\n"
                       "Что сделать: канал MAX → Админы → добавь бота админом с правом публиковать сообщения.\n"
                       "Посты не пропадут — выложу, как только права вернутся.")
            elif r is True and RIGHTS_TOLD.pop(t.chat_id, None):
                journal("✅ Проверка прав: бот снова админ канала MAX")
                notify("✅ Права в канале MAX вернулись — публикую дальше.")


RIGHTS_RECHECK_SEC = 30


async def background_loop():
    """Общие дела: убираем старые карточки у админов, шлём ежедневный отчёт, раз в час проверяем права."""
    n = 0
    RIGHTS_CHECK["next"] = time.time() + 300             # первая проверка прав — через 5 минут после запуска
    while True:
        await asyncio.sleep(TICK)
        try:
            await asyncio.to_thread(purge_cards)
            await asyncio.to_thread(purge_autodel)
            if n % 6 == 0:                               # ~раз в 30 сек — автоудаление постов из каналов
                await asyncio.to_thread(channel_autodel_due)
            if time.time() >= RIGHTS_CHECK["next"]:
                RIGHTS_CHECK["next"] = time.time() + 3600
                await asyncio.to_thread(check_rights_all)
                await asyncio.to_thread(subs_snapshot_all)   # 📈 подписчики каналов — для графика роста
                await asyncio.to_thread(fwd_snapshot_all)    # 🕘 и каналов из «Недавних проверок»
            if HOST_MODE and n % 4 == 0:                 # каждые ~20 сек проверяем онлайн-оплаты кабинетов
                await asyncio.to_thread(host_pay_poll)
            if HOST_MODE and n % 180 == 0:               # ~раз в 15 мин — напоминания о сроке кабинетам
                await asyncio.to_thread(host_terms_all)
            if HOST_MODE and n % 180 == 0:               # бета-функции: неделя прошла — выпускаем всем
                await asyncio.to_thread(beta_tick)
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


def doc_info(m):
    """Файл в посте Telegram (PDF, архив, музыка, голосовое…), кроме фото, видео и стикеров:
    {"id", "name", "size"} или None."""
    d = getattr(m, "document", None)
    if d is None or has_image(m) or video_doc(m) or getattr(m, "sticker", None):
        return None
    fl = getattr(m, "file", None)
    name = getattr(fl, "name", None) or getattr(d, "name", None) or ""
    if not name:
        name = f"file_{m.id}{getattr(fl, 'ext', None) or ''}"
    name = re.sub(r'[\\/:*?"<>|\n\r]+', "_", name).strip()[:120] or f"file_{m.id}"
    return {"id": m.id, "name": name, "size": int(getattr(d, "size", 0) or 0)}


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
    fresh, t0 = [], time.perf_counter()
    try:
        await _process_channel(client, ch, fails, fresh)
    finally:
        perf_add("tg", "чтение канала Telegram", time.perf_counter() - t0)
        if fresh:
            await asyncio.to_thread(send_previews, fresh)


async def _process_channel(client, ch, fails, fresh):
    """Читает новые посты канала, чистит и ставит в очередь на публикацию."""
    t = cur()
    history = t.history
    msgs = await client.get_messages(ch, min_id=LAST_IDS[ch], limit=30)
    msgs = sorted(msgs, key=lambda m: m.id)
    if len(msgs) >= 30 and msgs[0].grouped_id:         # самый старый альбом обрезан лимитом — без начала
        gid = msgs[0].grouped_id                       # (а текст на первом фото) его не публикуем калекой
        msgs = [m for m in msgs if m.grouped_id != gid]

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
        await save_last_ids_async()
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
        media = [] if on_free() else grp                 # 🆓 бесплатный тариф — только текст
        if not media and on_free() and any(has_image(m) or video_info(m) or doc_info(m) for m in grp):
            await asyncio.to_thread(free_media_hint)
        try:
            # фото: скачиваем сразу (они лёгкие), считаем отпечаток для дублей
            for m in media:
                if has_image(m):
                    dest = f"{MEDIA_DIR}/{t.fs}{ch}_{m.id}{photo_ext(m)}"
                    try:
                        p = await download_photo(m, dest, None if is_web_client(client) else ch)
                    except Exception as e:
                        p = None
                        admin_log(f"📎 [{ch}] фото {m.id} пока не скачалось ({why(e)}) — докачаю перед публикацией")
                    img_paths.append(p or dest)        # нет файла — ensure_photos докачает его перед публикацией
                    pids.append(m.id)
                    if p:
                        with open(p, "rb") as f:
                            hashes.append(hashlib.md5(f.read()).hexdigest())
            # видео/GIF: пока только данные о файле — качать будем при публикации
            vids = []
            if CFG["video_on"]:
                for m in media:
                    vi = video_info(m)
                    if vi:
                        vids.append((m, vi))
                        hashes.append(f"v{vi[0]}-{vi[1]}")
            # файлы (PDF, архивы, музыка, голосовые): качаем при публикации.
            # Строгое правило: файл больше лимита или без входа в Telegram не скачать — пост пропускается целиком.
            docs, skip = [], None
            for m in media:
                di = doc_info(m)
                if not di:
                    continue
                if is_web_client(client) and not getattr(m.document, "url", None):
                    skip = skip or f"{FILE_NEEDS_API} (файл «{di['name']}»)"
                elif size_skip_reason(di["size"], media_cap_mb()):
                    skip = skip or size_skip_reason(di["size"], media_cap_mb())
                else:
                    docs.append(di)
                    hashes.append(f"d{di['size']}-{di['name']}")
            if not skip:
                skip = next((size_skip_reason(vi[0], media_cap_mb()) for _, vi in vids
                             if size_skip_reason(vi[0], media_cap_mb())), None)
            entry = {"norm": norm(body), "img": hashes, "t": time.time(),
                     "fwd": [k for k in (fwd_key(m) for m in grp) if k]}
            has_media = bool(img_paths or vids or docs)
            dup = find_duplicate(entry, history) if CFG["dedupe_on"] else None
            if skip:                                     # пост пропущен целиком: в журнале причина, молча
                journal(f"[{ch}] пост {last}: {skip if skip.startswith('Skipped') else 'Skipped: ' + skip}")
            elif not body and not has_media:
                journal(f"[{ch}] пост {last}: пусто после чистки — пропускаю")
            elif (bw := blocked_word(body)) is not None:
                bump("blocked")
                journal(f"[{ch}] пост {last}: стоп-слово «{bw}» — не публикую")
            elif CFG["ads_skip"] and (ad := ad_verdict(raw, ch, nbtn := sum(url_buttons(m) for m in grp)))[0] != "ok":
                held = {"id": f"{t.prefix}{ch}:{grp[0].id}", "ch": ch, "date": grp[0].date.timestamp(),
                        "enq": time.time(), "text": body, "photos": img_paths, "pids": pids,
                        "vids": [m.id for m, _ in vids], "docs": docs, "sent": 0, "fails": 0,
                        "urgent": False, "status": "ok", "raw": raw[:1500], "buttons": nbtn,
                        "why": "; ".join(ad[1])[:120]}
                hold_ad(held)
                queued = True                          # фото не удаляем: вдруг скажут «не реклама»
                if ad[0] == "ask" and CFG.get("ads_ask", True):
                    journal(f"[{ch}] пост {last}: может быть рекламой ({'; '.join(ad[1])}) — спросил тебя")
                    in_background(ask_ad, held, name="ask-ad")   # карточка с фото — загрузка не в цикле
                else:
                    bump("ads")
                    journal(f"[{ch}] пост {last}: похоже на рекламу ({'; '.join(ad[1])}) — не публикую")
            elif dup is not None:
                # запоминаем и этот пересказ, чтобы ловить следующие похожие
                alts = dup.setdefault("alts", [])
                if entry["norm"] and len(alts) < 6:
                    alts.append(entry["norm"])
                    await save_history_async()
                bump("dups")
                journal(f"[{ch}] пост {last}: ДУБЛЬ по смыслу, уже публиковали — пропускаю")
            elif len(QUEUE) >= 100:
                journal(f"[{ch}] пост {last}: очередь переполнена (100) — пропускаю")
            else:
                status = "pending" if CFG["approval_on"] else "ok"
                if status == "ok" and CFG.get("smart_quality") and CFG["admin_chats"] \
                        and post_quality(body, has_media) < 4:
                    status = "pending"                   # 🧠 слабый пост — сначала тебе на одобрение
                if status == "pending" and not CFG["admin_chats"]:
                    journal("⚠️ Некому показать пост на одобрение: напиши своему боту в MAX /start")
                item = {"id": f"{t.prefix}{ch}:{grp[0].id}", "ch": ch,
                        "date": grp[0].date.timestamp(), "enq": time.time(),
                        "text": body, "photos": img_paths, "pids": pids,
                        "vids": [m.id for m, _ in vids], "docs": docs, "sent": 0, "fails": 0,
                        "status": status, "raw": raw[:1500],
                        "buttons": sum(url_buttons(m) for m in grp)}
                if on_free():                           # 🆓 до FREE_PLAN["queue_max"] в очереди: старые уходят
                    auto = sorted((x for x in QUEUE if not x.get("manual")), key=lambda x: x["enq"])
                    for old in auto[:max(0, len(QUEUE) + 1 - FREE_PLAN["queue_max"])]:
                        drop_item(old)
                QUEUE.append(item)
                save_queue()
                history.append(entry)               # сразу помним, чтобы ловить дубли в очереди
                await save_history_async()
                queued = True
                journal(f"[{ch}] пост {last} поставлен в очередь (всего {len(QUEUE)})"
                      + (" — жду одобрения" if status == "pending" else "")
                      + f": фото {len(img_paths)}, видео {len(vids)}, файлы {len(docs)}, текст {len(body)} симв."
                      + (" ⚠️ весь текст вырезали фильтры (🧹 реклама/подписи)" if raw.strip() and not body else ""))
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
            await save_last_ids_async()


DOWN_REMIND = 86400            # «не открывается» про один источник — не чаще раза в сутки, и после перезапуска тоже


def down_first(ch):
    """Пора ли сказать «не открывается»: в этой теме ещё не говорили или говорили больше суток назад.
    Помним в settings.json, а не в памяти — обновление бота не повторяет сообщение."""
    noted, key = GCFG.setdefault("down_noted", {}), f"{cur().id}|{ch}"
    if time.time() - noted.get(key, 0) < DOWN_REMIND:
        return False
    noted[key] = time.time()
    in_background(save_cfg, name="save-cfg")              # из чтения каналов (корутина) — запись с fsync фоном
    return True


def down_over(ch):
    """Источник открылся — забываем поломку: сломается снова — скажем снова."""
    if GCFG.get("down_noted", {}).pop(f"{cur().id}|{ch}", None):
        in_background(save_cfg, name="save-cfg")


async def sync_channels(client):
    """Подхватывает каналы текущей темы. Не открывшийся канал не выбрасываем навсегда — пробуем снова."""
    saved = cur().saved_ids
    for ch in live_channels():
        if ch in LAST_IDS or BAD.get(ch, 0) > time.time() or net_blocked(ch):
            continue
        if is_social(ch) and not feature_on("social"):
            continue
        pool_spawn(ch)
        src = src_client(client, ch)
        try:
            latest = await asyncio.wait_for(src.get_messages(ch, limit=1), 60)
        except Exception as e:
            if POOL.note_error(src, e):                # аккаунт пула отдыхает или выбыл — читаем другим
                journal(f"👤 [{ch}] аккаунт пула: {type(e).__name__} — прочитаю другим на следующем круге")
                continue
            if is_social(ch):                          # соцсеть: не трогаем Telegram, повтор через 10 минут
                first = down_first(ch)
                BAD[ch] = time.time() + 600
                journal(f"Источник {ch_label(ch)} не открывается: {e}")
                if first:
                    await asyncio.to_thread(notify, f"⚠️ {ch_label(ch)} не открывается: {str(e)[:200]}. "
                                                    "Пробую каждые 10 минут.")
                continue
            if is_auth_dead(e):
                await auth_dead(client, e)
                continue
            if is_net_error(e):                        # сеть, а не канал: не пугаем «имя неверное»
                await reconnect_tg(client)
                await asyncio.to_thread(note_net, ch, e, is_web_client(client))
                continue
            first = down_first(ch)
            BAD[ch] = time.time() + 600                # повтор через 10 минут
            journal(f"Канал {ch} не открывается: {type(e).__name__}: {e}")
            if first:
                await asyncio.to_thread(
                    notify, f"⚠️ Канал @{ch} не открывается ({type(e).__name__}). Пробую каждые 10 минут. "
                            f"Если имя неверное — убери его в меню.")
            continue
        note_net(ch)
        down_over(ch)
        if ch in BAD:
            del BAD[ch]
            journal(f"Канал {ch} снова открывается")
        latest_id = latest[0].id if latest else 0
        if TEST_REPOST_LAST:
            LAST_IDS[ch] = max(await last_post_start(src_client(client, ch), ch) - 1, 0)
            print(f"ТЕСТ: [{ch}] переопубликую последний пост {latest_id}")
        elif ch in saved:
            LAST_IDS[ch] = saved[ch]
        else:
            LAST_IDS[ch] = latest_id     # новый канал: старое не постим
    await save_last_ids_async()


async def last_post_start(client, ch):
    """id ПЕРВОГО сообщения последнего поста. Альбом в Telegram — несколько сообщений, текст обычно на первом:
    если взять только самое последнее сообщение, выйдет одно последнее фото без текста."""
    msgs = sorted(await client.get_messages(ch, limit=20), key=lambda m: -m.id)
    if not msgs:
        return 0
    gid = getattr(msgs[0], "grouped_id", None)
    return min(m.id for m in msgs if m is msgs[0] or (gid and getattr(m, "grouped_id", None) == gid))


async def run_test(client):
    """Тест-репост из меню: берём последний пост каждого канала темы заново и публикуем сразу."""
    cur().history.clear()
    save_history()
    FORCE["until"] = time.time() + 300
    for ch in list(LAST_IDS):
        try:
            LAST_IDS[ch] = max(await last_post_start(src_client(client, ch), ch) - 1, 0)
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


AUTH_DEAD = {"AuthKeyUnregisteredError", "AuthKeyDuplicatedError", "AuthKeyInvalidError", "SessionRevokedError",
             "SessionExpiredError", "UserDeactivatedError", "UserDeactivatedBanError"}


def is_auth_dead(e):
    """Telegram отозвал вход (завершили сеанс, вход в двух местах, бан) — повторять бессмысленно."""
    return type(e).__name__ in AUTH_DEAD


async def auth_dead(client, e):
    """Вход слетел: сразу читаем через t.me, мёртвый файл входа удаляем, владельцу — одно сообщение."""
    box = client if isinstance(client, ClientBox) else TG_CLIENT
    if not isinstance(box, ClientBox) or is_web_client(box) or isinstance(box.cur, RemoteClient):
        return
    dead = box.cur
    box.cur = WebClient()
    with contextlib.suppress(Exception):
        await dead.disconnect()
    for f in ("session.session", "session.session-journal"):
        with contextlib.suppress(OSError):
            os.remove(f)
    name = type(e).__name__
    why = ("Telegram заблокировал аккаунт" if "Ban" in name or "Deactivated" in name else
           "Telegram завершил вход бота (сеанс завершили в «Устройствах» или Telegram отозвал его сам)")
    print(f"Telegram: вход больше не действует ({name}) — читаю каналы через t.me")
    journal(f"🔑 Вход в Telegram слетел ({name}) — перешёл на t.me")
    await asyncio.to_thread(owner_alert, f"🔑 {why}.\n\nБот не остановился: читаю открытые каналы через t.me "
                            "(без файлов и больших видео).\n\nЧтобы вернуть: 🔑 Telegram → «Подключить аккаунт». "
                            "Совет: включи в Telegram облачный пароль — так вход надёжнее.")
    if not CLIENT_MODE:
        for cid in list(PROCS):                  # боты клиентов, читавшие через этот вход, — на t.me
            with contextlib.suppress(Exception):
                stop_client(cid)


POLL_TIMEOUT = 600        # сек на чтение одного канала: зависнет — прерываем, остальные каналы не ждут
PUBLISH_TIMEOUT = 2700    # сек на один пост (большое видео + загрузка в MAX): зависнет — повторим позже


async def poll(client, t, ch, fails, sem):
    CUR.set(t)
    src = src_client(client, ch)
    async with POOL.sem_for(src) or sem:                # аккаунты пула читают параллельно, каждый — до 5 каналов
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(process_channel(src, ch, fails), POLL_TIMEOUT)
            note_net(ch)
        except Exception as e:
            if POOL.note_error(src, e):
                journal(f"👤 [{ch}] аккаунт пула: {type(e).__name__} — следующий круг читает другой")
            elif isinstance(e, asyncio.TimeoutError) and time.monotonic() - t0 >= POLL_TIMEOUT * 0.99:
                journal(f"⏱ [{ch}] чтение канала зависло (больше {POLL_TIMEOUT // 60} мин) — прервал, "
                        "остальные каналы читаются дальше, этот попробую на следующем круге")
            elif is_social(ch):
                print(f"[{ch_label(ch)}] не прочитался: {type(e).__name__}: {e}")
            elif is_auth_dead(e):
                await auth_dead(client, e)
            elif is_net_error(e):
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
        self.name = ""


class WebMessage:
    """Похож на сообщение Telethon ровно настолько, насколько нужно боту."""

    def __init__(self, ch, mid, date, text="", photo=None, video=None, grouped_id=None, document=None):
        self.ch, self.id, self.date, self.text = ch, mid, date, text
        self.grouped_id = grouped_id
        self.photo = _Media(photo) if photo else None
        self.video = video                       # _Media (url может быть None) или None
        self.gif = self.video_note = self.sticker = self.fwd_from = None
        self.url_buttons = 0                     # кнопки-ссылки под постом (признак рекламы)
        self.document = document                 # файл/голосовое: _Media с name (url есть только у голосовых)

    async def download_media(self, file=None):
        media = self.photo or self.video or self.document
        if media is None or not media.url:
            if self.document is not None:
                raise WebMediaUnavailable(FILE_NEEDS_API)
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

def _doc_download_url(tag):
    """Прямая ссылка на файл из виджета t.me, если она есть (CDN Telegram). Ссылка на сам пост
    (t.me/<канал>/<id>) файлом не является, поэтому её не берём — вернём None."""
    for m in re.finditer(r'href="([^"]+)"', tag):
        u = _html.unescape(m.group(1))
        host = re.sub(r"^https?://", "", u).split("/")[0].lower()
        if host and not host.endswith(("t.me", "telegram.me", "telegram.org")) and (
                "telesco.pe" in host or "cdn" in host or "/file/" in u or "/dl/" in u):
            return _abs_url(u)
    return None


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
    docs = []                                        # (id, _Media) — файлы и голосовые
    for dm in re.finditer(r'<a\b[^>]*class="[^"]*tgme_widget_message_document_wrap[^"]*"[^>]*>(.*?)</a>', block, re.S):
        tag = dm.group(0)
        tm = re.search(r'tgme_widget_message_document_title[^>]*>(.*?)</div>', dm.group(1), re.S)
        hm = re.search(r'href="[^"]*/(\d+)(?:\?[^"]*)?"', tag)
        d = _Media(_doc_download_url(tag))            # прямая ссылка на файл, если t.me её отдаёт (иначе None)
        d.name = _html.unescape(re.sub(r"<[^>]+>", "", tm.group(1))).strip() if tm else ""
        docs.append((int(hm.group(1)) if hm else bubble, d))
    vm = re.search(r'<audio\b[^>]*\bsrc="([^"]+)"', block)
    if vm and "tgme_widget_message_voice" in block:
        d = _Media(_abs_url(vm.group(1)))
        d.name = f"voice_{bubble}.ogg"
        docs.append((bubble, d))
    if "video_player" in block or "tgme_widget_message_video" in block or "roundvideo" in block:
        vm = re.search(r"<video\b[^>]*\bsrc=\"([^\"]+)\"", block)
        pm = re.search(r'<a\b[^>]*video_player[^>]*href="[^"]*/(\d+)(?:\?[^"]*)?"', block)
        items.append((int(pm.group(1)) if pm else bubble, None,
                      _Media(_abs_url(vm.group(1)) if vm else None)))
    items = [x + (None,) for x in items]
    for did, d in docs:                              # файл — отдельное сообщение (или вместе с альбомом)
        if did not in {x[0] for x in items}:
            items.append((did, None, None, d))
    buttons = len(re.findall(r'tgme_widget_message_inline_button[^"]*url_button', block))
    if not items:
        msg = WebMessage(ch, bubble, date, text)
        msg.url_buttons = buttons
        return [msg]
    items.sort(key=lambda x: x[0])
    gid = bubble if len(items) > 1 else None
    out = [WebMessage(ch, pid, date, text if k == 0 else "", photo=ph, video=vd, grouped_id=gid, document=dc)
           for k, (pid, ph, vd, dc) in enumerate(items)]
    out[0].url_buttons = buttons
    return out


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


# ---------- 🧪 БЕТА-ФУНКЦИИ: сначала у хозяина и тестеров, через неделю — у всех ----------
BETA_DAYS = 7          # столько дней функция в бете; если хозяин не сказал «держать» — выходит для всех сама
BETA_FEATURES = {   # ключ: (название, что делает, где найти)
    "stats": ("📊 Аналитика просмотров",
              "Бот смотрит все посты канала MAX за последние 7 дней и присылает «Итоги недели»: просмотры и рост, "
              "когда лучше выкладывать (лучшие день и час) и самые просматриваемые посты.",
              "меню → «📊 Аналитика» или команда /stats"),
    "social": ("🌐 Соцсети-источники",
               "Брать посты не только из Telegram, но и из TikTok, VK, YouTube, Rutube и сайтов с RSS. "
               "Достаточно прислать ссылку на аккаунт — новые посты пойдут в канал MAX.",
               "меню → «📡 Откуда брать посты» → «➕ Добавить канал» → прислать ссылку"),
    "ai": ("🪄 ИИ-редактор",
           "Пересказывает каждый пост другими словами (синонимами), чтобы он не был копией. Тема, длина, ссылки, "
           "@каналы, цифры и КАПС остаются. Бесплатно, без ключей.",
           "меню → «⚙️ Ещё настройки» → «🧠 Умное» → «🪄 ИИ-редактор»"),
    "trends": ("🔥 Тренды",
               "Бот сам следит за новостями по теме канала покупателя и присылает сигнал, только когда случилось "
               "что-то вирусное: что произошло и почему стоит выложить прямо сейчас. Тратит твои ключи ИИ, поэтому "
               "всем сама не включается — включай нужным покупателям.",
               "меню → «📊 Статистика и Отчеты» → «📊 Статистика просмотров» → «🔥 Тренды» или команда /trends"),
    "mytariff": ("🧩 Свой тариф",
                 "Покупатель сам выбирает, сколько ему нужно тем и источников, — цена считается сама: 200 ₽ база "
                 "(1 тема, 5 источников), +100 ₽ за тему, +50 ₽ за каждые 5 источников.",
                 "меню → «💳 Продлить доступ» → «🧩 Собрать свой тариф»"),
}
BETA_STATE = {"beta": "🧪 бета", "all": "✅ у всех", "off": "⛔ выключена"}
BETA_HOLD = {"trends"}  # эти сами всем не выходят (держим в бете), пока хозяин не решит


def beta_rec(f):
    b = GCFG.setdefault("beta", {})
    if f not in b:
        b[f] = {"state": "beta", "since": time.time(), "hold": f in BETA_HOLD, "testers": []}
        with contextlib.suppress(Exception):
            save_cfg()
    return b[f]


def feature_on(f, key=None):
    """Включена ли функция для кабинета. Хозяину и обычному (не host) боту — всегда."""
    if not HOST_MODE:
        return True
    if key is None:
        with contextlib.suppress(Exception):
            key = cur().owner
    if not key or str(key) == str(HOST_OWNER) or str(key) == str(host_id()) or is_host(key):
        return True
    r = beta_rec(f)
    if r["state"] == "all":
        return True
    if r["state"] == "off":
        return False
    return str(key) in [str(x) for x in r.get("testers") or []]


def beta_tick(now=None):
    """Раз в час: функции, которые неделю в бете и их не держат, — выпускаем всем. Список выпущенных."""
    now = now or time.time()
    out = []
    for f in BETA_FEATURES:
        r = beta_rec(f)
        if r["state"] == "beta" and not r.get("hold") and now - r.get("since", now) >= BETA_DAYS * 86400:
            r["state"] = "all"
            out.append(f)
    if out:
        save_cfg()
        host_alert("🚀 Вышли из беты и включились у всех покупателей:\n"
                   + "\n".join("• " + BETA_FEATURES[f][0] for f in out)
                   + "\n\nВыключить или вернуть в бету: панель → «🧪 Бета-функции».")
    return out


def beta_left(r, now=None):
    return max(0, int((r.get("since", 0) + BETA_DAYS * 86400 - (now or time.time())) // 86400) + 1)


def beta_screen(name, uid):
    """Экраны хозяина: clbeta (все функции), clbf:<f> (одна функция), clbt:<кабинет> (функции покупателя)."""
    back = [B("◀️ Кабинеты", "m:cl")]
    kind, _, arg = name.partition(":")
    if kind == "clbeta":
        lines, rows = [], []
        for f, (title, about, where) in BETA_FEATURES.items():
            r = beta_rec(f)
            if r["state"] == "beta":
                st = ("🧪 в бете, держу (сама не выйдет)" if r.get("hold") else
                      f"🧪 в бете, у всех через {beta_left(r)} дн.") + f" · тестеров: {len(r.get('testers') or [])}"
            else:
                st = {"all": "✅ работает у всех покупателей", "off": "⛔ выключена у всех"}[r["state"]]
            lines.append(f"{title}\n{st}")
            rows.append([B(f"{title}"[:40], f"m:clbf:{f}")])
        text = ("🧪 Бета-функции — новые возможности бота, которые ещё проверяются.\n\n"
                "Как это работает:\n"
                "1. Новая функция сначала есть только у тебя и у тестеров — покупателей, которым ты её включил.\n"
                f"2. Через {BETA_DAYS} дней она сама включается у ВСЕХ покупателей.\n"
                "3. Не хочешь, чтобы выходила, — открой её и нажми «⏸ Держать в бете». "
                "Хочешь сразу всем — «🚀 Включить всем сейчас».\n\n"
                "Как сделать покупателя тестером: «👥 Все покупатели» → покупатель → «🧪 Бета-функции».\n\n"
                + "\n\n".join(lines)
                + "\n\nНажми на функцию — расскажу, что она делает и где её кнопка.")
        rows.append([B("🔑 Ключ VK" + (" ✅" if vk_token() else " (нужен для VK)"), "p:clvk")])
        return text, rows + [back]
    if kind == "clbf" and arg in BETA_FEATURES:
        r = beta_rec(arg)
        title, about, where = BETA_FEATURES[arg]
        names = [(TENANTS.get(k) or {}).get("name") or k for k in r.get("testers") or []]
        text = (f"{title}\n\nЧто делает: {about}\n\nГде кнопка: {where}\n\nСейчас: {BETA_STATE[r['state']]}"
                + (f" ({'держу в бете' if r.get('hold') else f'всем через {beta_left(r)} дн.'})"
                   if r["state"] == "beta" else "")
                + "\nТестеры: " + (", ".join(names) or "пока нет")
                + ("\n\n⚠️ Для источников VK нужен ключ VK — кнопка ниже." if arg == "social" and not vk_token() else ""))
        rows = [[B("🚀 Включить всем сейчас", f"a:clbrel:{arg}")]] if r["state"] != "all" else []
        if r["state"] == "beta":
            rows.append([B("▶️ Не держать (выйдет сама)" if r.get("hold") else "⏸ Держать в бете",
                           f"a:clbhold:{arg}")])
        else:
            rows.append([B("🧪 Вернуть в бету", f"a:clbbeta:{arg}")])
        if r["state"] != "off":
            rows.append([B("⛔ Выключить у всех", f"a:clboff:{arg}")])
        if arg == "social":
            rows += [[B("🔑 Ключ VK" + (" ✅" if vk_token() else ""), "p:clvk")],
                     [B("🎵 Cookies TikTok" + (" ✅" if tiktok_cookies() else ""), "p:cltt")]]
        return text, rows + [[B("◀️ Бета-функции", "m:clbeta")]]
    if kind == "clbt" and arg in TENANTS:
        c = TENANTS[arg]
        rows, lines = [], []
        for f, (title, *_rest) in BETA_FEATURES.items():
            r = beta_rec(f)
            on = arg in [str(x) for x in r.get("testers") or []]
            lines.append(f"{'✅' if feature_on(f, arg) else '▫️'} {title}"
                         + (" (у всех)" if r["state"] == "all" else " (выключена у всех)" if r["state"] == "off" else ""))
            if r["state"] == "beta":
                rows.append([B(f"{'✅' if on else '➕'} {title}"[:40], f"a:clbt:{arg}:{f}")])
        text = (f"🧪 Бета-функции для «{c.get('name') or arg}»\n\n" + "\n".join(lines)
                + "\n\n✅ — у покупателя функция есть, ▫️ — нет.\nНажми на функцию с ➕ — включу её этому покупателю "
                  "(он станет тестером и увидит кнопку у себя в меню). Нажми ещё раз — выключу.")
        rows.append([B("✅ Включить всё бета", f"a:clbt:{arg}:*")])
        return text, rows + [[B("◀️ Кабинет", f"m:clc:{arg}")]]
    return None


def beta_action(p):
    """Кнопки хозяина в бета-экранах. Экран после или None."""
    f = p[1] if len(p) > 1 else ""
    if p[0] in ("clbrel", "clbhold", "clboff", "clbbeta") and f in BETA_FEATURES:
        r = beta_rec(f)
        if p[0] == "clbrel":
            r["state"] = "all"
        elif p[0] == "clbhold":
            r["hold"] = not r.get("hold")
        elif p[0] == "clboff":
            r["state"] = "off"
        else:
            r.update(state="beta", since=time.time(), hold=True)   # вернули сами — сама не выйдет
        save_cfg()
        return f"clbf:{f}"
    if p[0] == "clbt" and f in TENANTS:
        what = p[2] if len(p) > 2 else ""
        for ff in (BETA_FEATURES if what == "*" else [what] if what in BETA_FEATURES else []):
            r = beta_rec(ff)
            ts = [str(x) for x in r.get("testers") or []]
            if what == "*" or f not in ts:
                if f not in ts:
                    ts.append(f)
            else:
                ts.remove(f)
            r["testers"] = ts
        save_cfg()
        return f"clbt:{f}"
    return None


# ---------- 📊 АНАЛИТИКА: в какие дни и часы у канала больше всего просмотров ----------
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAYS_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def fetch_channel_posts(chat_id, days=7, limit=600, known=(), diag=None):
    """[(время, просмотры)] всех постов канала за `days` дней (см. fetch_channel_msgs)."""
    return [(p["t"], p["views"]) for p in fetch_channel_msgs(chat_id, days, limit, known, diag)]


def _msg_media(body):
    """(ссылка на картинку поста, это видео?) из сообщения MAX — для превью в «Лучших постах»."""
    for a in body.get("attachments") or []:
        pl = a.get("payload") or {}
        if a.get("type") == "image" and pl.get("url"):
            return pl["url"], False
        if a.get("type") == "video":
            th = a.get("thumbnail") or pl.get("thumbnail") or {}
            return (th.get("url") if isinstance(th, dict) else th) or None, True
    return None, False


def fetch_channel_msgs(chat_id, days=7, limit=600, known=(), diag=None):
    """ВСЕ посты канала MAX за `days` дней (и выложенные ботом, и руками) с просмотрами:
    [{"mid", "t", "views", "text", "img", "video"}].
    known — [(mid, время)] постов, которые выложил сам бот: их просмотры добираем, даже если ленту MAX не отдал.
    diag — сюда пишем, что вышло (ошибка MAX, сколько постов видно, у скольких есть просмотры)."""
    diag = {} if diag is None else diag
    since = time.time() - days * 86400
    got = {}                                            # mid -> [время, просмотры или None]
    cursor, mode, switched = None, "from", False        # листаем назад: MAX может понимать from или to
    for _ in range(limit // 100 + 4):
        params = {"chat_id": chat_id, "count": 100}
        if cursor is not None:
            params[mode] = cursor
        try:
            r = S.get(f"{MAX_API}/messages", params=params, headers=HEADERS, timeout=30)
        except Exception as e:
            diag["error"] = f"нет связи с MAX ({type(e).__name__})"
            break
        if r.status_code != 200:
            diag["error"] = f"MAX ответил {r.status_code}: {r.text[:120]}"
            break
        try:
            msgs = r.json().get("messages") or []
        except ValueError:
            diag["error"] = "MAX прислал непонятный ответ"
            break
        new, oldest = 0, None
        for m in msgs:
            ts, mid = m.get("timestamp"), (m.get("body") or {}).get("mid")
            if not isinstance(ts, (int, float)) or not mid:
                continue
            oldest = ts if oldest is None else min(oldest, ts)
            if mid in got:
                continue
            new += 1
            v = (m.get("stat") or {}).get("views")
            img, video = _msg_media(m.get("body") or {})
            got[mid] = {"mid": mid, "t": ts / 1000, "views": v if isinstance(v, int) else None,
                        "text": (m.get("body") or {}).get("text") or "", "img": img, "video": video}
        if not msgs or oldest is None or oldest / 1000 < since or len(got) >= limit:
            break
        if new == 0 and cursor is not None:             # страница не сдвинулась — пробуем другой способ листать
            if switched:
                break
            mode, switched = "to", True
            continue
        cursor = oldest - 1
    for mid, t in known or ():                          # посты, выложенные ботом, — даже если лента не пришла
        if mid and mid not in got and t >= since:
            got[mid] = {"mid": mid, "t": t, "views": None, "text": "", "img": None, "video": False}
    need = [mid for mid, p in got.items() if p["views"] is None and p["t"] >= since]
    if need:                                            # в ленте нет просмотров — спрашиваем по id постов
        for mid, v in fetch_views(chat_id, need).items():
            if mid in got:
                got[mid]["views"] = v
    inside = [p for p in got.values() if p["t"] >= since]
    diag["seen"] = len(inside)
    diag["views"] = sum(1 for p in inside if p["views"] is not None)
    return [p for p in inside if p["views"] is not None]


def analyze_posts(posts):
    """Сводка: средние просмотры по дням недели, по часам, сетка 7×24, лучший день/час/окно."""
    if not posts:
        return {"n": 0}
    grid = [[[] for _ in range(24)] for _ in range(7)]
    for ts, v in posts:
        lt = time.localtime(ts)
        grid[lt.tm_wday][lt.tm_hour].append(v)
    avg = lambda xs: sum(xs) / len(xs) if xs else None
    day = [avg([v for h in range(24) for v in grid[d][h]]) for d in range(7)]
    hour = [avg([v for d in range(7) for v in grid[d][h]]) for h in range(24)]
    cell = [[avg(grid[d][h]) for h in range(24)] for d in range(7)]
    total = sum(v for _, v in posts)
    mean = total / len(posts)

    def best(vals, counts, need):
        ok = [(a, i) for i, a in enumerate(vals) if a is not None and counts[i] >= need]
        if not ok:
            ok = [(a, i) for i, a in enumerate(vals) if a is not None]
        return max(ok)[1] if ok else None

    dcount = [sum(len(grid[d][h]) for h in range(24)) for d in range(7)]
    hcount = [sum(len(grid[d][h]) for d in range(7)) for h in range(24)]
    bd, bh = best(day, dcount, 2), best(hour, hcount, 2)
    slots = [(cell[d][h], d, h) for d in range(7) for h in range(24) if cell[d][h] is not None and len(grid[d][h]) >= 2]
    slot = max(slots)[1:] if slots else (bd, bh)
    return {"n": len(posts), "total": total, "mean": mean, "day": day, "hour": hour, "cell": cell,
            "count": [[len(grid[d][h]) for h in range(24)] for d in range(7)],
            "best_day": bd, "best_hour": bh, "slot": slot,
            "lift": (hour[bh] / mean - 1) * 100 if bh is not None and mean else 0,
            "worst_hour": min(((a, i) for i, a in enumerate(hour) if a is not None), default=(None, None))[1]}


def analytics_caption(name, a):
    if not a.get("n"):
        why = a.get("diag") or {}
        if why.get("error") and not why.get("seen"):
            return (f"📊 {name}: MAX не отдал посты канала — {why['error']}.\n"
                    "Обычно так бывает, если бот не админ канала. Сделай бота админом и нажми ещё раз.")
        if why.get("seen"):
            return (f"📊 {name}: MAX показал {why['seen']} постов, но без просмотров. "
                    "Просмотры MAX показывает только админам канала — проверь, что бот там админ.")
        return f"📊 {name}: за последние 7 дней в канале нет постов — считать пока нечего."
    best = a.get("best") or (*a["slot"], 1)
    return (f"📊 {name}: {a['n']} постов за неделю, в среднем {num_sp(a['mean'])} просмотров.\n"
            + (f"Лучшее время — {WEEKDAYS_FULL[best[0]]}, {best[1]:02d}:00. " if best[2] > 1.05 or "best" not in a else
               "Время почти не влияет на просмотры. ") + "Подробно — на картинке.")


# ---------- 📊 «Итоги недели»: один дашборд на канал (просмотры, когда выкладывать, лучшие посты) ----------
WEEKDAYS_LOW = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def week_stats(posts, now=None):
    """Всё для дашборда из постов канала за 14 дней: эта неделя — главная, прошлая — для сравнения (+18%)."""
    now = now or time.time()
    lt = time.localtime(now)                          # неделя — 7 целых календарных дней, последний — сегодня
    since = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)) - 6 * 86400
    cur = [p for p in posts if p["t"] >= since]
    a = analyze_posts([(p["t"], p["views"]) for p in cur])
    if not a["n"]:
        return {"n": 0, "a": a}
    total = sum(p["views"] for p in cur)
    before = sum(p["views"] for p in posts if since - 7 * 86400 <= p["t"] < since)
    days = []
    for k in range(6, -1, -1):                        # 7 дней по порядку, последний — сегодня
        lt = time.localtime(now - k * 86400)
        same = [p["views"] for p in cur if time.localtime(p["t"])[:3] == lt[:3]]
        days.append((WEEKDAYS[lt.tm_wday], sum(same)))
    hours = fill_gaps(a["hour"])
    slots = best_slots(a)
    a["best"] = slots[0] if slots else None           # подпись к картинке говорит то же, что картинка
    return {"n": a["n"], "a": a, "total": total, "trend": round((total / before - 1) * 100) if before else None,
            "days": days, "hours": hours, "quiet": quiet_window(hours), "slots": slots,
            "top": sorted(cur, key=lambda p: -p["views"])[:3], "since": since, "now": now}


def fill_gaps(vals):
    """Часы без постов — плавно между соседними часами (а не ноль: «нет постов» ≠ «нет просмотров»)."""
    known = [i for i, v in enumerate(vals) if v is not None]
    if not known:
        return [0] * len(vals)
    n, out = len(vals), []
    for i, v in enumerate(vals):
        if v is not None:
            out.append(v)
            continue
        a = max((k for k in known if k < i), default=known[-1] - n)
        b = min((k for k in known if k > i), default=known[0] + n)
        va, vb = vals[a % n], vals[b % n]
        out.append(va + (vb - va) * (i - a) / (b - a))
    return out


def quiet_window(hours, width=3):
    """Самые «тихие» часы подряд — туда лучше не ставить главное. (начало, конец)."""
    start = min(range(len(hours) - width + 1), key=lambda i: sum(hours[i:i + width]))
    return start, start + width


def best_slots(a, n=3, prior=1):
    """Лучшие «день + час»: [(день, час, во сколько раз больше среднего)], только выше среднего.
    Время с 2+ постами — впереди: один удачный пост может быть случайностью. А «во сколько раз» считаем осторожно:
    будто в каждом времени есть ещё `prior` обычный пост — чем меньше постов, тем ближе цифра к средней."""
    mean = a["mean"] or 1
    cells = []
    for d in range(7):
        for h in range(24):
            cnt = a["count"][d][h]
            if cnt:
                x = (a["cell"][d][h] * cnt + mean * prior) / (cnt + prior) / mean
                if x > 1.05:
                    cells.append((cnt >= 2, x, d, h))
    return [(d, h, x) for _, x, d, h in sorted(cells, reverse=True)[:n]]


def times_x(x):
    return f"{x:.1f}".replace(".", ",")


@functools.lru_cache(maxsize=None)
def _dash_font(bold, size):
    return _font(bold, size)


@functools.lru_cache(maxsize=4096)
def _has_glyph(ch):
    from PIL import Image, ImageDraw
    def pic(c):
        im = Image.new("L", (60, 60), 0)
        ImageDraw.Draw(im).text((8, 8), c, font=_dash_font(True, 36), fill=255)
        return im.tobytes()
    return pic(ch) != pic("￿")


def dash_clean(text):
    """Текст поста для картинки: без разметки, ссылок и значков, которых нет в шрифте (эмодзи)."""
    t = re.sub(r"\*\*|__|~~|`", "", LINK_RE.sub(r"\1", text or ""))
    t = re.sub(r"https?://\S+", "", t)
    ok = lambda c: c.isspace() or 32 <= ord(c) < 127 or 0x400 <= ord(c) < 0x500 or 0xA0 <= ord(c) < 0x100 \
        or _has_glyph(c)
    return " ".join("".join(c if ok(c) else " " for c in t).split())


def fetch_thumb(url):
    """Картинка поста из MAX для превью. None — нет или не скачалась (нарисуем заглушку)."""
    if not url:
        return None
    try:
        from PIL import Image
        import io
        r = S.get(url, timeout=15)
        return Image.open(io.BytesIO(r.content)).convert("RGB") if r.status_code == 200 else None
    except Exception:
        return None


def _dash_grad(w, h, stops, vertical=False):
    from PIL import Image
    line = Image.new("RGB", (256, 1))
    for i in range(256):
        seg = i / 255 * (len(stops) - 1)
        k = min(int(seg), len(stops) - 2)
        line.putpixel((i, 0), tuple(int(stops[k][j] + (stops[k + 1][j] - stops[k][j]) * (seg - k)) for j in range(3)))
    if vertical:
        line = line.rotate(-90, expand=True)
    return line.resize((max(1, w), max(1, h)), Image.BILINEAR)


D_INK, D_MUTE, D_DIM, D_EDGE = (245, 243, 255), (156, 146, 190), (40, 30, 72), (48, 34, 92)
D_VIOLET, D_PINK, D_CYAN = (168, 85, 247), (236, 72, 153), (34, 211, 238)
D_NEON = [D_CYAN, D_VIOLET, D_PINK]


class Dash:
    """Холст дашборда: рисуем в 2 раза крупнее и уменьшаем в конце — гладкие края и текст."""
    S = 2

    def __init__(self, w, h, blobs):
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
        self.W, self.H = w, h
        img = Image.new("RGB", self.p(w, h), (9, 7, 18))
        glow = Image.new("RGB", img.size, (0, 0, 0))
        g = ImageDraw.Draw(glow)
        for x, y, r, col in blobs:
            g.ellipse(self.p(x - r, y - r, x + r, y + r), fill=col)
        self.set(ImageChops.add(img, glow.filter(ImageFilter.GaussianBlur(140 * self.S))))

    def set(self, img):
        from PIL import ImageDraw
        self.img, self.d = img, ImageDraw.Draw(img)

    def p(self, *v):
        return tuple(int(round(x * self.S)) for x in v)

    def f(self, size, bold=False):
        return _dash_font(bold, int(size * self.S))

    def width(self, s, size, bold=False):
        return self.f(size, bold).getlength(s) / self.S

    def fit(self, s, size, bold, max_w, min_size=18):
        while size > min_size and self.width(s, size, bold) > max_w:
            size -= 1
        return size

    def text(self, xy, s, size, fill=D_INK, anchor="la", bold=False):
        self.d.text(self.p(*xy), s, font=self.f(size, bold), fill=fill, anchor=anchor)

    def one_line(self, s, size, bold, max_w):
        if self.width(s, size, bold) <= max_w:
            return s
        while s and self.width(s + "…", size, bold) > max_w:
            s = s[:-1]
        return s.rstrip(" ,.:;—-") + "…"

    def gtext(self, xy, s, size, stops, anchor="la"):
        from PIL import Image, ImageDraw
        mask = Image.new("L", self.img.size, 0)
        ImageDraw.Draw(mask).text(self.p(*xy), s, font=self.f(size, True), fill=255, anchor=anchor)
        box = mask.getbbox()
        if box:
            self.img.paste(_dash_grad(box[2] - box[0], box[3] - box[1], stops), box[:2], mask.crop(box))

    def grad_round(self, box, r, stops, vertical=True):
        from PIL import Image, ImageDraw
        x0, y0, x1, y1 = self.p(*box)
        m = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), int(r * self.S), fill=255)
        self.img.paste(_dash_grad(x1 - x0, y1 - y0, stops, vertical), (x0, y0), m)

    def glow(self, draw_fn, radius):
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
        layer = Image.new("RGB", self.img.size, (0, 0, 0))
        draw_fn(ImageDraw.Draw(layer))
        self.set(ImageChops.screen(self.img, layer.filter(ImageFilter.GaussianBlur(radius * self.S))))

    def pill(self, x, y, s, size, stops=None, h=48, pad=18):
        w = self.width(s, size, True) + 2 * pad
        if stops:
            self.grad_round((x, y, x + w, y + h), h / 2, stops, vertical=False)
        else:
            self.d.rounded_rectangle(self.p(x, y, x + w, y + h), int(h / 2 * self.S), fill=D_DIM)
        self.text((x + w / 2, y + h / 2), s, size, D_INK, "mm", True)

    def neon_line(self, box, alpha=255, width=2):
        from PIL import Image, ImageDraw
        m = Image.new("L", self.img.size, 0)
        ImageDraw.Draw(m).line(self.p(*box), fill=alpha, width=int(width * self.S))
        self.img.paste(_dash_grad(self.img.width, self.img.height, D_NEON), (0, 0), m)

    def bar(self, box, part, stops):
        x0, y0, x1, y1 = box
        self.d.rounded_rectangle(self.p(*box), int((y1 - y0) / 2 * self.S), fill=D_DIM)
        self.grad_round((x0, y0, x0 + max(y1 - y0, (x1 - x0) * part), y1), (y1 - y0) / 2, stops, vertical=False)

    def badge(self, x, y, size, n, hot):
        self.grad_round((x, y, x + size, y + size), size / 2, [D_PINK, D_VIOLET] if hot else [(90, 60, 170), (60, 40, 120)])
        self.text((x + size / 2, y + size / 2), str(n), size * 0.54, D_INK, "mm", True)

    def area_chart(self, box, values, peak_label="", quiet=None, labels=()):
        """Кривая с мягкой заливкой: линия — неоновый градиент, пик — светящаяся точка с подписью."""
        from PIL import Image, ImageChops, ImageDraw
        x0, y0, x1, y1 = box
        mx = max(values) or 1
        step = (x1 - x0) / (len(values) - 1)
        pts = [(x0 + i * step, y1 - (y1 - y0) * (v / mx) * 0.92) for i, v in enumerate(values)]
        curve = _smooth(pts)
        if quiet:
            qa, qb = x0 + quiet[0] * step, x0 + quiet[1] * step
            shade = Image.new("L", self.img.size, 0)
            ImageDraw.Draw(shade).rounded_rectangle(self.p(qa, y0 - 10, qb, y1), int(14 * self.S), fill=55)
            self.img.paste((60, 50, 100), (0, 0), shade)
            self.text(((qa + qb) / 2, y0 + 8), "тихо", 22, D_MUTE, "mm")
        fill = Image.new("L", self.img.size, 0)
        ImageDraw.Draw(fill).polygon([self.p(*q) for q in curve] + [self.p(x1, y1), self.p(x0, y1)], fill=255)
        fade = Image.new("L", self.img.size, 0)
        fade.paste(_dash_grad(self.img.width, int((y1 - y0) * self.S), [(150,) * 3, (0,) * 3], True).convert("L"),
                   (0, int(y0 * self.S)))
        self.img.paste((150, 70, 230), (0, 0), ImageChops.multiply(fill, fade))
        line = Image.new("L", self.img.size, 0)
        ImageDraw.Draw(line).line([self.p(*q) for q in curve], fill=255, width=int(5 * self.S), joint="curve")
        self.img.paste(_dash_grad(self.img.width, self.img.height, D_NEON), (0, 0), line)
        pi = values.index(max(values))
        px, py = pts[pi]
        self.glow(lambda g: g.ellipse(self.p(px - 16, py - 16, px + 16, py + 16), fill=D_PINK), 18)
        self.d.ellipse(self.p(px - 10, py - 10, px + 10, py + 10), fill=D_INK, outline=D_PINK, width=int(4 * self.S))
        if peak_label:
            w = self.width(peak_label, 24, True) + 32
            lx = min(max(px - w / 2, x0 - 10), x1 + 10 - w)
            self.grad_round((lx, py - 66, lx + w, py - 24), 21, [D_VIOLET, D_PINK], vertical=False)
            self.text((lx + w / 2, py - 45), peak_label, 24, D_INK, "mm", True)
        for i, lab in labels:
            self.text((x0 + i * step, y1 + 28), lab, 22, D_INK if i == pi else D_MUTE, "mm", i == pi)

    def result(self):
        from PIL import Image
        return self.img.resize((self.W, self.H), Image.LANCZOS)


def _smooth(pts, n=14):
    """Плавная кривая через точки (Catmull-Rom)."""
    ext, out = [pts[0]] + pts + [pts[-1]], []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for k in range(n):
            t = k / n
            out.append(tuple(0.5 * (2 * p1[j] + (p2[j] - p0[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t * t
                                    + (3 * p1[j] - p0[j] - 3 * p2[j] + p3[j]) * t ** 3) for j in range(2)))
    return out + [pts[-1]]


def dash_thumb(src, size, rank, video):
    """Квадратная картинка поста (обрезаем по центру) или заглушка в цветах темы."""
    from PIL import Image, ImageDraw
    s = int(size * Dash.S)
    if src is not None:
        side = min(src.size)
        im = src.crop(((src.width - side) // 2, (src.height - side) // 2, (src.width + side) // 2,
                       (src.height + side) // 2)).resize((s, s), Image.LANCZOS)
    else:
        top = [(255, 120, 170), (30, 200, 230), (150, 90, 240)][rank % 3]
        im = _dash_grad(s, s, [top, (110, 50, 210), (30, 16, 70)], vertical=True)
        d = ImageDraw.Draw(im)
        d.ellipse((s * .32, s * .2, s * .68, s * .56), fill=(255, 200, 140))
        d.polygon([(0, s * .8), (s * .3, s * .5), (s * .52, s * .72), (s * .74, s * .46), (s, s * .76), (s, s), (0, s)],
                  fill=(28, 14, 60))
    if video:
        d, c, r = ImageDraw.Draw(im), s / 2, s * .17
        d.ellipse((c - r, c - r, c + r, c + r), fill=(0, 0, 0))
        d.polygon([(c - r * .35, c - r * .5), (c - r * .35, c + r * .5), (c + r * .55, c)], fill=D_INK)
    return im


def _paste_round(dash, im, xy, r):
    from PIL import Image, ImageDraw
    m = Image.new("L", im.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, im.width - 1, im.height - 1), int(r * Dash.S), fill=255)
    dash.img.paste(im, dash.p(*xy), m)


def _dash_hero(c, ws, MX):
    """Главное число недели, рост к прошлой неделе и кривая по дням."""
    c.text((MX, 212), "просмотров за неделю", 28, D_MUTE)
    num = num_sp(ws["total"])
    c.gtext((MX - 6, 244), num, c.fit(num, 132, True, 520), D_NEON)
    tr = ws["trend"]
    if tr is not None:
        up = tr >= 0
        c.pill(MX, 418, f"{'↑ +' if up else '↓ -'}{abs(tr)}% к прошлой неделе", 26,
               stops=[D_CYAN, D_VIOLET] if up else [D_PINK, D_VIOLET], h=52, pad=22)
    best = max(ws["days"], key=lambda dv: dv[1])
    c.area_chart((610, 250, c.W - MX - 6, 430), [v for _, v in ws["days"]],
                 peak_label=f"{best[0]} · {num_sp(best[1])}",
                 labels=list(enumerate(n for n, _ in ws["days"])))


def _dash_strip(c, ws, MX):
    """Ключевые цифры одной полосой: посты, в среднем, рекорд."""
    y = 530
    c.d.rounded_rectangle(c.p(MX, y, c.W - MX, y + 128), int(28 * c.S), fill=(18, 13, 36), outline=D_EDGE,
                          width=int(2 * c.S))
    a = ws["a"]
    cols = [(str(ws["n"]), n_word(ws["n"], "пост вышел", "поста вышло", "постов вышло")),
            (num_sp(a["mean"]), "просмотров на пост"), (num_sp(ws["top"][0]["views"]), "рекорд недели")]
    cw = (c.W - 2 * MX) / 3
    for k, (v, lab) in enumerate(cols):
        cx = MX + cw * k + cw / 2
        if k:
            c.d.line(c.p(MX + cw * k, y + 26, MX + cw * k, y + 102), fill=D_EDGE, width=int(2 * c.S))
        if k == 2:
            c.gtext((cx, y + 50), v, 48, [D_PINK, D_VIOLET], "mm")
        else:
            c.text((cx, y + 50), v, 48, D_INK, "mm", True)
        c.text((cx, y + 96), lab, 23, D_MUTE, "mm")


def _dash_section(c, y, title, note, MX):
    c.neon_line((MX, y, c.W - MX, y), 110)
    c.text((MX, y + 26), title, 40, D_INK, bold=True)
    c.text((c.W - MX, y + 38), note, 24, D_MUTE, "ra")


def _dash_times(c, ws, MX):
    """Когда выкладывать: кривая по часам (пик и «тихо») + 3 лучших «день + время»."""
    _dash_section(c, 694, "Когда выкладывать", "просмотры поста по часам", MX)
    peak = ws["hours"].index(max(ws["hours"]))
    c.area_chart((MX + 10, 826, c.W - MX - 10, 966), ws["hours"], peak_label=f"пик {peak:02d}:00", quiet=ws["quiet"],
                 labels=[(0, "00"), (6, "06"), (12, "12"), (18, "18"), (23, "23")])
    top = ws["slots"][0][2] if ws["slots"] else 1
    if not ws["slots"]:
        c.text((MX, 1060), "Пока нет времени, когда просмотров заметно больше среднего.", 28, D_INK)
        c.text((MX, 1104), "Чем больше постов в разное время — тем точнее подсказка.", 24, D_MUTE)
    for k, (d, h, x) in enumerate(ws["slots"]):
        ry = 1030 + k * 72
        c.badge(MX, ry, 52, k + 1, k == 0)
        label = f"{WEEKDAYS_FULL[d]}, {h:02d}:00"
        c.text((MX + 76, ry + 26), label, c.fit(label, 34, True, 320), D_INK, "lm", True)
        c.bar((480, ry + 18, 830, ry + 34), x / top, [D_CYAN, D_VIOLET, D_PINK] if k == 0 else [D_VIOLET, D_PINK])
        c.text((c.W - MX, ry + 26), f"в {times_x(x)} раза", 28, D_PINK if k == 0 else D_INK, "rm", True)
    c.text((MX, 1030 + 3 * 72 + 4), "во сколько раз больше просмотров, чем в среднем", 22, D_MUTE)


def _dash_posts(c, ws, thumbs, MX):
    """Лучшие посты: картинка, начало текста, дата, просмотры и полоска для сравнения."""
    _dash_section(c, 1300, "Лучшие посты", "по просмотрам", MX)
    best = ws["top"][0]["views"] or 1
    for k, post in enumerate(ws["top"]):
        ry = 1404 + k * 122
        _paste_round(c, dash_thumb(thumbs[k] if k < len(thumbs) else None, 104, k, post.get("video")), (MX, ry), 20)
        c.badge(MX - 10, ry - 10, 40, k + 1, k == 0)
        tx = MX + 128
        title = dash_clean(post.get("text")) or ("Видео" if post.get("video") else "Пост без текста")
        c.text((tx, ry + 4), c.one_line(title, 30, True, 600), 30, D_INK, bold=True)
        lt = time.localtime(post["t"])
        meta = f"{WEEKDAYS_LOW[lt.tm_wday]}, {lt.tm_mday} {MONTHS_SHORT[lt.tm_mon - 1]} · {lt.tm_hour:02d}:{lt.tm_min:02d}"
        c.text((tx, ry + 46), meta + (" · видео" if post.get("video") else ""), 22, D_MUTE)
        c.bar((tx, ry + 84, 740, ry + 96), post["views"] / best, [D_PINK, D_VIOLET] if k == 0 else [D_VIOLET, (110, 70, 210)])
        v = num_sp(post["views"])
        if k == 0:
            c.gtext((c.W - MX, ry + 30), v, 44, [D_PINK, D_VIOLET], "ra")
        else:
            c.text((c.W - MX, ry + 30), v, 44, D_INK, "ra", True)
        c.text((c.W - MX, ry + 80), "просмотров", 22, D_MUTE, "ra")


def _dash_tip(c, ws, MX):
    y = 1786
    c.grad_round((MX, y, c.W - MX, y + 84), 26, [(36, 22, 74), (52, 20, 70)], vertical=False)
    c.d.rounded_rectangle(c.p(MX, y, c.W - MX, y + 84), int(26 * c.S), outline=(90, 50, 150), width=int(2 * c.S))
    c.grad_round((MX + 20, y + 20, MX + 64, y + 64), 22, [D_CYAN, D_VIOLET])
    c.text((MX + 42, y + 42), "!", 30, D_INK, "mm", True)
    d, h, x = ws["slots"][0] if ws["slots"] else (0, 0, 1)
    tip = (f"Главное ставь на {WEEKDAYS_FULL[d]}, {h:02d}:00 — увидят в {times_x(x)} раза больше" if x >= 1.15 else
           "Время почти не влияет на просмотры — выкладывай, когда удобно")
    c.text((MX + 84, y + 42), tip, c.fit(tip, 26, True, c.W - 2 * MX - 104), D_INK, "lm", True)


def week_dashboard(name, ws, thumbs=()):
    """Картинка «Итоги недели» 1080×1920 (экран телефона) в цветах Reloader."""
    MX = 64
    c = Dash(1080, 1920, [(160, 300, 330, (60, 22, 120)), (980, 760, 300, (80, 18, 80)),
                          (120, 1350, 280, (30, 22, 90)), (960, 1720, 300, (60, 16, 90))])
    c.img.paste(_dash_grad(c.img.width, 6 * c.S, D_NEON), (0, 0))
    c.text((MX, 58), "ИТОГИ НЕДЕЛИ", 24, D_MUTE, bold=True)
    rng = week_range(ws["since"], ws["now"])
    pw = c.width(rng, 24, True) + 36
    title = f"«{dash_clean(name) or 'Канал'}»"
    size = c.fit(title, 50, True, c.W - 2 * MX - pw - 24, 34)
    c.text((MX, 92), c.one_line(title, size, True, c.W - 2 * MX - pw - 24), size, D_INK, bold=True)
    c.pill(c.W - MX - pw, 62, rng, 24)
    c.text((c.W - MX, 140), "Reloader", 22, (120, 110, 160), "ra", True)
    _dash_hero(c, ws, MX)
    _dash_strip(c, ws, MX)
    _dash_times(c, ws, MX)
    _dash_posts(c, ws, list(thumbs), MX)
    _dash_tip(c, ws, MX)
    return c.result()


def analytics_send(chat, chans):
    """chans: [(название, chat_id, [(mid, время) постов бота])]. По картинке «Итоги недели» на каждый канал."""
    try:
        def one(ch):
            name, cid, known = (list(ch) + [()])[:3]
            diag = {}
            ws = week_stats(fetch_channel_msgs(cid, days=14, known=known, diag=diag))
            ws["a"]["diag"] = diag
            print(f"Аналитика: «{name}» — постов за неделю {ws['n']}, видно за 2 недели {diag.get('seen', 0)}"
                  + (f", {diag['error']}" if diag.get("error") else ""))
            thumbs = [fetch_thumb(p.get("img")) for p in ws.get("top", [])]
            return name, ws, thumbs

        from concurrent.futures import ThreadPoolExecutor
        todo = [ch for ch in chans if (list(ch) + [None, None])[1]]
        with ThreadPoolExecutor(max_workers=4) as pool:          # каналы считаем одновременно — быстрее
            res = list(pool.map(one, todo))
        if not res:
            api_send(chat, "📊 Нет привязанных каналов MAX — сначала «📢 Куда выкладывать».")
            return
        for name, ws, thumbs in res:
            send_dashboard(chat, name, ws, thumbs)
    except Exception as e:
        log_exc("аналитика")
        with contextlib.suppress(Exception):
            api_send(chat, f"⚠️ Аналитика не получилась: {type(e).__name__}: {str(e)[:150]}. Попробуй ещё раз.")


def send_dashboard(chat, name, ws, thumbs=()):
    """Картинка «Итоги недели» с короткой подписью. Нет постов или картинка не вышла — хотя бы текст."""
    cap, tok = analytics_caption(name, ws["a"]), None
    if ws["n"]:
        try:
            path = f"dashboard_{chat}_{abs(hash(name)) % 10 ** 8}.png"
            week_dashboard(name, ws, thumbs).save(path)       # без optimize: в 7 раз быстрее, на 6% больше
            tok = upload_image(path)
            with contextlib.suppress(OSError):
                os.remove(path)
        except Exception as e:
            print("Аналитика: картинка не получилась:", e)
    try:
        api_send(chat, cap, None, tok) if tok else api_send(chat, cap)
    except Exception as e:
        print("Аналитика: не отправилась:", e)
        if tok:
            api_send(chat, cap)                     # без картинки — но хотя бы цифры


ANL_BUSY = {}          # кто уже ждёт картинку аналитики: повторное нажатие не запускает подсчёт второй раз


def user_chat(uid):
    """Личный чат пользователя с ботом (куда слать картинки и ответы)."""
    if HOST_MODE:
        if is_host(uid) and HOST_CHAT.get("chat"):
            return HOST_CHAT["chat"]
        rec = TENANTS.get(tenant_key(uid) or "") or {}
        if (rec.get("chats") or {}).get(str(uid)):
            return rec["chats"][str(uid)]
    return (CFG.get("admin_chats") or {}).get(str(uid))


def my_channels():
    """[(название темы, chat_id канала MAX, [(mid, время) постов, выложенных ботом])] — каналы этого
    пользователя (в host-режиме — только его кабинета)."""
    chans = []
    for t in (scope_themes() if HOST_MODE else THEMES):
        if t.chat_id:
            with theme_ctx(t):
                known = [(p.get("mid"), p.get("t", 0)) for p in PUB.get("day_posts", []) if p.get("mid")]
            chans.append((t.name, t.chat_id, known))
    return chans


def analytics_start(uid):
    """Кнопка «📊 Аналитика»: считает в фоне, картинки придут в чат."""
    chat = user_chat(uid)
    chans = my_channels()
    if chat is None:
        return "Не нашёл твой чат с ботом — напиши боту /start и нажми ещё раз."
    if not chans:
        return "Нет привязанных каналов MAX — сначала «📢 Куда выкладывать»."
    if time.time() - ANL_BUSY.get(str(uid), 0) < 300:
        return ("⏳ Уже считаю — подожди, картинка придёт сюда сама. Нажимать ещё раз не нужно: "
                "каждое нажатие только запускает подсчёт заново.")
    ANL_BUSY[str(uid)] = time.time()
    wait = (f"⏳ Считаю просмотры {len(chans)} {n_word(len(chans), 'канала', 'каналов', 'каналов')}…\n"
            "Бот перебирает все посты за неделю — это занимает до 1–2 минут. "
            "Не нажимай ещё раз: картинка сама придёт сюда.")
    with contextlib.suppress(Exception):
        api_send(chat, wait)

    def run():
        try:
            analytics_send(chat, chans)
        finally:
            ANL_BUSY.pop(str(uid), None)

    threading.Thread(target=run, daemon=True).start()
    return wait


# ---------- 🖼 ПРЕМИУМ-КАРТОЧКИ: статистика канала и всей сети каналов ----------
from reloader.services import analytics_image as anlimg  # noqa: E402
from reloader.services.ai import image as ai_image  # noqa: E402
from reloader.services.ai.rewrite import ask as ai_ask_text  # noqa: E402
from reloader.services.analytics import predict as anlpredict  # noqa: E402

SUBS_FILE = "subs_history.json"
def _load_subs(path):
    """subs_history.json: испорченное (не словарь, NaN, строки, кривые даты) — отбрасываем, остальное берём."""
    raw = load_json(path, {})
    out = {str(k): anlimg.clean_history(v) for k, v in raw.items() if isinstance(v, dict)} \
        if isinstance(raw, dict) else {}
    return {k: v for k, v in out.items() if v}


SUBS_HIST = _load_subs(SUBS_FILE)       # chat_id канала MAX -> {"ГГГГ-ММ-ДД": подписчиков (последнее за день)}
SUBS_KEEP_DAYS = 62
SUBS_LOCK = threading.Lock()
ANL_BG = ("pattern", "off", "ai")        # фон карточек: фото дня (по умолчанию) → градиент → рисует ИИ → фото дня…
ANL_BG_NAMES = {"pattern": "🏞 фото дня", "off": "🎨 градиент", "ai": "🤖 рисует ИИ"}
ANL_CARD_BUSY = {}                       # «uid:вид» -> когда начали рисовать (второе нажатие не запускает заново)
PRED_HIST = {}                           # chat_id -> (когда, посты канала) — история для прогноза, на 30 мин
PRED_HIST_MAX = 20                       # не больше стольких каналов в памяти (память бота не растёт)
_CARD_FONTS = threading.local()          # шрифты — у каждого потока свои (FreeType не любит общий шрифт)


@functools.lru_cache(maxsize=2)
def _font_bytes(bold):
    import base64
    return zlib.decompress(base64.b64decode("".join(_FONT_B if bold else _FONT_R)))


def card_font(bold, size):
    """Встроенный Inter (с кириллицей) для карточек; распакован один раз, размеры — из памяти потока."""
    import io
    from PIL import ImageFont
    cache = getattr(_CARD_FONTS, "c", None)
    if cache is None:
        cache = _CARD_FONTS.c = {}
    f = cache.get((bold, size))
    if f is None:
        f = cache[(bold, size)] = ImageFont.truetype(io.BytesIO(_font_bytes(bool(bold))), size)
    return f


def channel_subs(info):
    """Подписчиков канала из ответа MAX GET /chats/{id} (participants_count). None — MAX не сказал."""
    n = (info or {}).get("participants_count")
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else None


def subs_snapshot(cid, n, now=None):
    """Запоминаем подписчиков канала на сегодня: MAX историю не отдаёт — копим сами (для графика и прироста)."""
    if not cid or n is None:
        return
    now = now or time.time()
    key, old = anlimg.day_key(now), anlimg.day_key(time.time() - SUBS_KEEP_DAYS * 86400)
    with SUBS_LOCK:
        h = SUBS_HIST.setdefault(str(cid), {})
        if h.get(key) == n:
            return
        h[key] = n
        for k in [k for k in h if k < old]:
            del h[k]
        snap = copy.deepcopy(SUBS_HIST)
    with contextlib.suppress(OSError):
        save_json(SUBS_FILE, snap)


def subs_history(cid):
    with SUBS_LOCK:
        return dict(SUBS_HIST.get(str(cid)) or {})


def subs_snapshot_all():
    """Раз в час (с проверкой прав): подписчики каждого привязанного канала MAX."""
    for cid in dict.fromkeys(t.chat_id for t in list(THEMES) if t.chat_id):
        info = chat_info(cid)
        subs_snapshot(cid, channel_subs(info))


# ---------- 📊 ПОСТ ИЗ ЛЮБОГО КАНАЛА: «Вижу пост из канала: …» и три кнопки (без привязки и без прав админа) ----------
# 📊 Рекламный охват — картинка для рекламодателя (со знаком Reloader); 📈 Массовая проверка — аналитика канала из
# своей сетки; ⚡ Оценка потенциала — 0–100. Число подписчиков канала бот снимает сам раз в час — отсюда
# «Подписалось / Отписалось / Итого». Канал попадает в «🕘 Недавние проверки».
from reloader.services.analytics import potential as anlpot  # noqa: E402
from reloader.services.analytics import watch as fwdw  # noqa: E402

FWD_FILE = "channel_checks.json"         # «🕘 Недавние проверки» и снимки числа подписчиков проверенных каналов
FWD_WATCH = fwdw.Watch(FWD_FILE, save=save_json, load=load_json_safe)
FWD_RETRY = "Не получилось связаться с MAX — перешли пост ещё раз через минуту."
FWD_RETRY_TG = "Не получилось открыть страницу канала в Telegram — пришли ссылку ещё раз через минуту."
FWD_RETRY_BTN = "Не получилось посчитать — MAX или Telegram не ответили. Нажми кнопку ещё раз через минуту."
FWD_NOT_BOUND = ("Этого канала нет в вашей сетке. Чтобы я мог его проверить, привяжите его в разделе "
                 "«🎭 Мои каналы».")
FWD_HIDDEN = ("MAX не показывает этот канал боту — бот в нём не состоит, поэтому посчитать его пока не могу. "
              "Добавьте бота в администраторы канала — и я всё посчитаю.")
FWD_MISSING_TG = ("Не нашёл в Telegram открытый канал по этой ссылке — проверьте её: нужна ссылка на канал или группу, "
                  "а не на человека.")
FWD_MENU = (("ad", "📊 Рекламный охват"), ("mass", "📈 Массовая проверка"), ("pot", "⚡ Оценка потенциала"))
FWD_BUSY = {}                            # «uid:действие:канал» -> когда начали (второе нажатие не запускает заново)
FWD_MAX_POLL = 300                       # каналов за один фоновый проход (раз в час)
FWD_POSTS = 20                           # охват и оценка — по стольким последним постам


def fwd_probe(cid):
    """GET /chats/{id}: (сведения, "ok") — MAX показал канал боту: он открыт для бота, даже если MAX не дал число
    подписчиков; ({}, "hidden") — MAX его боту не показывает (403/404: бот в канале не состоит) — это ещё НЕ
    значит, что канал закрытый; ({}, "error") — MAX не ответил (сеть, 5xx, 429)."""
    try:
        r = S.get(f"{MAX_API}/chats/{cid}", headers=HEADERS, timeout=20)
    except Exception:
        return {}, "error"
    if r.status_code == 200:
        with contextlib.suppress(ValueError):
            info = r.json()
            return (info, "ok") if isinstance(info, dict) else ({}, "error")
        return {}, "error"
    return {}, ("error" if r.status_code >= 500 or r.status_code == 429 else "hidden")


def tg_account(ref, private=False):
    """Канал Telegram через аккаунт Telegram бота (Telethon), если бот в него вошёл: публичный — по имени, закрытый —
    по приглашению или id, если аккаунт в нём состоит (подписан). {"title", "username", "id", "subs",
    "posts": [(время, просмотры)]}; "closed" — закрытый, аккаунта в нём нет; "missing" — такого канала нет (или это
    человек/бот); "off" — аккаунта Telegram у бота нет; "error" — не ответил."""
    client, loop = TG_CLIENT, MAIN_LOOP
    if client is None or loop is None or not loop.is_running() or on_loop_thread():
        return "off"
    try:
        return asyncio.run_coroutine_threadsafe(_tg_account(client, ref, private), loop).result(40)
    except Exception as e:
        _log_line(time.strftime("%H:%M:%S") + f" Проверка канала: аккаунт Telegram не ответил ({type(e).__name__})")
        return "error"


async def _tg_account(client, ref, private):
    try:
        ent = await client.get_entity(ref)
    except Exception as e:
        name = type(e).__name__
        if "Flood" in name or isinstance(e, (ConnectionError, OSError, asyncio.TimeoutError)):
            return "error"
        if private:
            return "closed"                            # приглашение / закрытый канал, где аккаунта нет
        return "missing" if isinstance(e, ValueError) or "Username" in name else "error"
    title = getattr(ent, "title", None)
    if not title:
        return "missing"                               # человек или бот, а не канал
    subs = getattr(ent, "participants_count", None)
    with contextlib.suppress(Exception):               # limit=0 — только число (полные сведения о канале)
        subs = (await client.get_participants(ent, limit=0)).total
    posts = []
    with contextlib.suppress(Exception):
        for m in await client.get_messages(ent, limit=FWD_POSTS):
            if isinstance(getattr(m, "views", None), int) and getattr(m, "date", None) is not None:
                posts.append((m.date.timestamp(), m.views))
    return {"title": str(title)[:120], "username": (getattr(ent, "username", None) or "").lower() or None,
            "id": getattr(ent, "id", None), "subs": subs if isinstance(subs, int) else None, "posts": posts}


def fwd_from_account(ub):
    """Закрытый канал Telegram (приглашение, t.me/c/…): читаем, только если аккаунт бота в нём состоит."""
    if isinstance(ub, dict):
        name = ub.get("username")
        return {"key": f"tg:{name}" if name else f"tgc:{ub['id']}", "title": ub["title"],
                "link": f"https://t.me/{name}" if name else f"https://t.me/c/{ub['id']}", "subs": ub["subs"],
                "posts": ub["posts"], "state": "ok"}
    return {"key": None, "title": "", "link": "", "subs": None, "state": "error" if ub == "error" else "closed"}


def fwd_tg_public(name):
    """Публичный канал Telegram: сначала страница t.me; не открылась (блок, 403, лимит) или без числа — аккаунт
    Telegram бота. Ни то ни другое — «ещё раз» или «не нашёл», но не «закрытый»: у канала с именем закрытым он
    быть не может."""
    out = {"key": f"tg:{name}", "title": f"@{name}", "link": f"https://t.me/{name}", "subs": None}
    info, web_ok = {"title": None, "subs": None}, False
    try:
        info, web_ok = fwdw.parse_tme(web_fetch(f"https://t.me/{name}")), True
    except Exception:
        pass
    if info["subs"] is not None:
        return dict(out, title=info["title"] or out["title"], subs=info["subs"], state="ok")
    ub = tg_account(name)
    if isinstance(ub, dict):
        return dict(out, title=ub["title"], subs=ub["subs"], posts=ub["posts"], state="ok")
    if ub == "missing" or (web_ok and ub == "off"):
        return dict(out, state="missing")
    return dict(out, title=info["title"] or out["title"], state="error")


def fwd_lookup(src):
    """Канал по источнику: {"key", "title", "link", "subs", "state", ["posts"], ["chat_id"]}. state: "ok" — канал
    видно (число подписчиков может быть None: MAX не сказал); "closed" — правда закрытый (приглашение, а аккаунта
    Telegram бота в канале нет); "hidden" — MAX не показывает канал боту (бот в нём не состоит); "missing" — такого
    канала нет; "error" — MAX, t.me или аккаунт Telegram не ответили. Открытый канал «закрытым» не называем."""
    kind = src.get("kind")
    if kind == "closed":                               # приглашение в закрытый канал MAX
        return {"key": None, "title": "", "link": "", "subs": None, "state": "closed"}
    if kind in ("tginvite", "tgc"):
        ref = f"https://t.me/+{src['hash']}" if kind == "tginvite" else int(f"-100{src['id']}")
        return fwd_from_account(tg_account(ref, private=True))
    if kind == "tg":
        return fwd_tg_public(src["username"])
    if kind == "maxlink":
        cid = resolve_chat_id(f"https://max.ru/{src['slug']}")
        if cid is None:                                # MAX ищет только среди каналов бота — это не «закрытый»
            return {"key": f"maxlink:{src['slug']}", "title": src["slug"], "link": f"https://max.ru/{src['slug']}",
                    "subs": None, "state": "hidden"}
        src = {"kind": "max", "chat_id": cid}
    cid = src["chat_id"]
    info, state = fwd_probe(cid)
    return {"key": f"max:{cid}", "title": str(info.get("title") or f"Канал {cid}")[:120],
            "link": max_url(info.get("link")) or "", "subs": channel_subs(info) if state == "ok" else None,
            "state": state, "chat_id": cid}


def fwd_src_of_key(key):
    kind, _, ref = (key or "").partition(":")
    if kind == "max" and re.fullmatch(r"-?\d+", ref):
        return {"kind": "max", "chat_id": int(ref)}
    if kind == "tg" and ref:
        return {"kind": "tg", "username": ref, "post": None}
    if kind == "tgc" and ref.isdigit():
        return {"kind": "tgc", "id": int(ref)}
    if kind == "maxlink" and ref:
        return {"kind": "maxlink", "slug": ref}
    return None


def fwd_menu_rows(key):
    """Ровно три кнопки — и ничего больше."""
    return [[B(label, f"fw:{act}:{key}")] for act, label in FWD_MENU]


def fwd_menu_text(title, state="ok"):
    lines = [f"Вижу пост из канала: {title}.", "", "Что посчитать?",
             "📊 Рекламный охват — сколько просмотров наберёт рекламный пост (картинка для рекламодателя).",
             "📈 Массовая проверка — полная аналитика канала из вашей сетки.",
             "⚡ Оценка потенциала — оценка канала от 0 до 100."]
    if state == "hidden":
        lines += ["", "🙈 MAX не показывает этот канал боту — бот в нём не состоит. Охват и оценку посчитаю, когда "
                      "бот будет в канале (администратором)."]
    return "\n".join(lines)


def fwd_menu_screen(uid, key):
    """Меню канала из «Недавних проверок» (название — какое бот запомнил)."""
    item = next((r for r in FWD_WATCH.recent(str(uid)) if r["key"] == key), None)
    return fwd_menu_text(item["title"] if item else key, item["state"] if item else "ok"), fwd_menu_rows(key)


def fwd_remember(uid, res):
    """Канал — в «Недавние проверки»; открытый — ещё и снимок числа подписчиков (по снимкам — приток и отток)."""
    key, ok = res["key"], res["state"] == "ok"
    first = FWD_WATCH.points(key) == 0
    FWD_WATCH.remember(str(uid), key, {"title": res["title"], "link": res["link"], "state": res["state"],
                                       "closed": res["state"] == "closed"})
    if ok:
        if first and res.get("chat_id"):               # канал бот уже знает (привязан) — есть история по дням
            for day, n in sorted(subs_history(res["chat_id"]).items())[-7:]:
                with contextlib.suppress(ValueError):
                    FWD_WATCH.snap(key, n, time.mktime(time.strptime(day, "%Y-%m-%d")) + 12 * 3600)
        FWD_WATCH.snap(key, res["subs"])
    FWD_WATCH.save()


def fwd_menu(uid, chat, src):
    """Пост из любого канала (или ссылка на канал) — в фоне (MAX и t.me не тормозят бота): «Вижу пост из канала: …»
    и три кнопки. «Это закрытый канал…» — только для приглашения, где аккаунта Telegram бота нет; MAX или t.me не
    ответили — «ещё раз»; канал, который MAX не показывает боту, — меню с честной пометкой (это не «закрытый»)."""
    clear_transient(chat)                              # новый пост — прежнее промежуточное убираем
    if src.get("kind") == "closed":
        send_transient(chat, fwdw.CLOSED_TEXT)
        return
    try:
        res = fwd_lookup(src)
    except Exception:
        log_exc("проверка канала")
        res = {"state": "error"}
    tg = src.get("kind") in ("tg", "tginvite", "tgc")
    if res["state"] == "error":
        send_transient(chat, FWD_RETRY_TG if tg else FWD_RETRY)
        return
    if res["state"] in ("closed", "missing") or not res.get("key"):
        send_transient(chat, fwdw.CLOSED_TEXT if res["state"] == "closed" else FWD_MISSING_TG)
        return
    fwd_remember(uid, res)
    send_transient(chat, fwd_menu_text(res["title"], res["state"]), fwd_menu_rows(res["key"]), menu=True)


def fwd_callback(rest, uid, chat):
    """fw:ad|mass|pot:<канал> — считаем в фоне (ответ придёт отдельным сообщением), меню остаётся;
    fw:r:<канал> — снова меню канала. Только каналы из своих «Недавних проверок» — чужие id не перебрать."""
    act, _, key = (rest or "").partition(":")
    if not FWD_WATCH.is_recent(str(uid), key) or fwd_src_of_key(key) is None:
        return fwd_recent_screen(uid)
    if act in dict(FWD_MENU) and chat:
        busy = f"{uid}:{act}:{key}"
        if time.time() - FWD_BUSY.get(busy, 0) > 120:
            FWD_BUSY[busy] = time.time()
            threading.Thread(target=contextvars.copy_context().run, args=(fwd_job, uid, chat, act, key),
                             daemon=True, name=f"fwd-{act}").start()
    return fwd_menu_screen(uid, key)


def fwd_job(uid, chat, act, key):
    """Кнопка канала: «⏳ Анализирую…» — пока считаем; пришла аналитика (картинка и цифры) — промежуточное и старое
    меню убираем, под цифрами — снова меню канала."""
    wait = send_transient(chat, "⏳ Анализирую канал…")
    try:
        with chat_busy(chat, "sending_photo", after=1.0):
            title = {"ad": fwd_ad, "mass": fwd_mass, "pot": fwd_potential}[act](uid, chat, key, fwd_src_of_key(key))
        if title:
            clear_transient(chat)
            send_transient(chat, f"📊 {title} — что ещё посчитать?", fwd_menu_rows(key), menu=True)
    except Exception:
        log_exc(f"проверка канала ({act})")
        with contextlib.suppress(Exception):
            send_transient(chat, FWD_RETRY_BTN)
    finally:
        FWD_BUSY.pop(f"{uid}:{act}:{key}", None)
        if wait:
            clear_transient(chat, only=wait)


def fwd_bound_theme(uid, src):
    """Тема (канал MAX) из сетки пользователя, куда ведёт пост, или None. Хозяину — все каналы бота."""
    if src.get("kind") == "maxlink":
        cid = resolve_chat_id(f"https://max.ru/{src['slug']}")
    elif src.get("kind") == "max":
        cid = src["chat_id"]
    else:
        return None                                    # канал Telegram в сетку MAX не привязывается
    themes = THEMES if is_host(uid) else my_themes(uid)
    return next((t for t in themes if t.chat_id and same_uid(t.chat_id, cid)), None) if cid else None


def fwd_open(uid, chat, src):
    """Свежие сведения о канале для кнопки. Не вышло — одна понятная фраза (закрытый — только если правда закрытый;
    MAX не показывает боту — так и пишем; не ответил — «ещё раз»): тогда None."""
    res = fwd_lookup(src)
    if res["state"] == "error":
        send_transient(chat, FWD_RETRY_BTN)
        return None
    if res.get("key"):
        fwd_remember(uid, res)
    if res["state"] != "ok":
        send_transient(chat, {"closed": fwdw.CLOSED_TEXT, "hidden": FWD_HIDDEN}.get(res["state"], FWD_MISSING_TG))
        return None
    return res


def fwd_posts(res):
    """Последние посты канала: [(время или None, просмотры)] — что видно через аккаунт Telegram бота, страница
    t.me/s или MAX (что показывает боту)."""
    posts = list(res.get("posts") or ())
    try:
        if not posts and res["key"].startswith("tg:"):
            posts = [(t, v) for _, v, t in fwdw.parse_tme_posts(web_fetch(f"https://t.me/s/{res['key'][3:]}"))]
            if not posts and isinstance(ub := tg_account(res["key"][3:]), dict):
                posts = ub["posts"]
        elif not posts and res.get("chat_id"):
            posts = [(p["t"], p["views"]) for p in fetch_channel_msgs(res["chat_id"], days=14, limit=200)]
    except Exception:
        posts = posts or []                            # просмотров не видно — посчитаем без них
    posts.sort(key=lambda p: p[0] or 0)
    return posts[-FWD_POSTS:]


def fwd_views(posts, now=None):
    """Просмотры для охвата: у свежих постов (меньше суток) просмотры ещё растут — берём посты старше суток,
    если таких хотя бы три."""
    now = now or time.time()
    old = [v for t, v in posts if t and now - t >= 86400]
    return old if len(old) >= 3 else [v for _, v in posts]


def fwd_numbers(res, key, posts):
    """Цифры канала для текста после картинки (📈/👥/👁): подписчики, прирост по снимкам бота, просмотры постов."""
    now = time.time()
    hist = {anlimg.day_key(t): n for t, n in FWD_WATCH.history(key) if anlimg.day_key(t) != anlimg.day_key(now)}
    return anlimg.channel_numbers(res["title"], res["subs"], [{"t": t, "views": v} for t, v in posts if t], hist, now)


def fwd_mass(uid, chat, key, src):
    """📈 Массовая проверка: канал из своей сетки — аналитика канала картинкой и 📈/👥/👁 (+ 🤖); чужой — ровно одна
    фраза (промежуточная — уберём после следующего нажатия). Название канала — аналитика пришла."""
    t = fwd_bound_theme(uid, src)
    if t is None:
        send_transient(chat, FWD_NOT_BOUND)
        return None
    n = anl_channel(t.name, t.chat_id)
    img = anlimg.render_channel(n, card_font, card_bg(anl_bg_mode(uid), n["title"]), fetch_thumb(n.get("icon")))
    send_card(chat, img, stats_text(n))
    return n["title"]


def fwd_ad(uid, chat, key, src):
    """📊 Рекламный охват: вилка просмотров рекламного поста по медиане последних постов, подписчики, ER, приток
    и отток — на картинке (без подписи), за ней — 📈/👥/👁 (+ 🤖 вывод ИИ). Название канала — аналитика пришла."""
    res = fwd_open(uid, chat, src)
    if res is None:
        return
    key = res["key"]                                   # ссылка могла открыться по-другому — снимки по каналу
    posts = fwd_posts(res)
    reach = anlpot.ad_reach(res["subs"], fwd_views(posts))
    j, l, tot = FWD_WATCH.deltas(key)
    n = fwd_numbers(res, key, posts)                                   # те же цифры, что под картинкой (с ER24)
    d = dict(reach, title=res["title"], subs=res["subs"], joins=j, leaves=l, total=tot,
             er24=n.get("er24"), series=[x for _, x in FWD_WATCH.history(key)][-60:])
    img = anlimg.render_ad_reach(d, card_font, card_bg(anl_bg_mode(uid), res["title"]))
    send_card(chat, img, stats_text(n))                                # картинка без подписи, за ней — цифры
    return res["title"]


def fwd_potential(uid, chat, key, src):
    """⚡ Оценка потенциала (0–100): рост по снимкам, вовлечённость, регулярность постов, размер аудитории — на
    картинке (без подписи), за ней — 📈/👥/👁 (+ 🤖 вывод ИИ). Название канала — аналитика пришла."""
    res = fwd_open(uid, chat, src)
    if res is None:
        return
    key, posts = res["key"], fwd_posts(res)
    sc = anlpot.score(res["subs"], FWD_WATCH.history(key), fwd_views(posts), [t for t, _ in posts if t])
    j, l, tot = FWD_WATCH.deltas(key)
    d = dict(sc, title=res["title"], subs=res["subs"], joins=j, leaves=l, total=tot)
    img = anlimg.render_potential(d, card_font, card_bg(anl_bg_mode(uid), res["title"]))
    send_card(chat, img, stats_text(fwd_numbers(res, key, posts)))     # картинка без подписи, за ней — цифры
    return res["title"]


def fwd_snapshot_all():
    """Раз в час: число подписчиков каналов из «Недавних проверок» — так копятся «подписалось / отписалось»."""
    keys = FWD_WATCH.watched()[:FWD_MAX_POLL]
    for key in keys:
        src = fwd_src_of_key(key)
        if src is None:
            continue
        try:
            res = fwd_lookup(src)
        except Exception:
            continue
        if res["state"] == "ok":
            FWD_WATCH.snap(key, res["subs"])
    if keys:
        FWD_WATCH.save()


def fwd_recent_screen(uid):
    """🕘 Недавние проверки: каналы, которые пользователь проверял, — свежие сверху; кнопка — меню канала."""
    items = FWD_WATCH.recent(str(uid))
    lines, rows = ["🕘 Недавние проверки", ""], []
    for r in items:
        if r["state"] == "closed":
            lines.append(f"🔒 {r['title']} — закрытый канал")
        elif r["state"] == "hidden":
            lines.append(f"🙈 {r['title']} — MAX не показывает канал боту: добавьте бота в администраторы")
        else:
            j, l, tot = r["deltas"]
            since = time.strftime("%d.%m", time.localtime(r["since"])) if r["since"] else "—"
            lines.append(f"📢 {r['title']} — {anlimg.fmt_int(r['subs'])} подписчиков · с {since}: "
                         f"+{anlimg.fmt_int(j)} / -{anlimg.fmt_int(l)}")
        rows.append([B(f"{ {'closed': '🔒', 'hidden': '🙈'}.get(r['state'], '📢')} {r['title']}"[:60],
                       f"fw:r:{r['key']}")])
    if not items:
        lines.append("Пока пусто.")
    lines += ["", "Перешли мне пост из любого канала MAX (или пришли ссылку на канал Telegram или MAX) — предложу "
                  "три проверки: 📊 рекламный охват, 📈 массовая проверка и ⚡ оценка потенциала. Привязывать канал "
                  "не нужно."]
    return "\n".join(lines), rows + [[B("◀️ В меню", "m:main")]]


def anl_channel(name, cid, known=()):
    """Цифры карточки канала: подписчики (+ снимок в историю), просмотры постов за неделю, ER24."""
    info = chat_info(cid)
    subs = channel_subs(info)
    subs_snapshot(cid, subs)
    diag = {}
    posts = fetch_channel_msgs(cid, days=7, limit=300, known=known, diag=diag)
    n = anlimg.channel_numbers(info.get("title") or name, subs, posts, subs_history(cid))
    icon = info.get("icon")
    n.update(chat_id=cid, diag=diag, posts=posts, icon=(icon or {}).get("url") if isinstance(icon, dict) else None)
    return n


def anl_bg_mode(uid):
    box = GCFG.get("anl_bg")
    m = box.get(str(uid)) if isinstance(box, dict) else None
    return m if m in ANL_BG else "pattern"


def ai_background(topic):
    """Фон, нарисованный ИИ (OpenRouter, модель с картинками), — раз в неделю на тему, дальше из файла.
    None — ключей OpenRouter нет или не вышло (тогда рисуем узор сами)."""
    from PIL import Image
    import io
    path = f"anl_bg_{anlimg.seed_of(topic) % 10 ** 8}.jpg"
    with contextlib.suppress(Exception):
        if time.time() - os.path.getmtime(path) < 7 * 86400:
            return Image.open(path).convert("RGB")
    cfg = ai_settings().get("openrouter") or {}
    model = os.environ.get("OPENROUTER_IMAGE_MODEL") or ai_image.MODEL
    for key in ai_keys("openrouter")[:3]:
        t0 = time.perf_counter()
        raw = ai_image.generate(topic, key, S, cfg.get("url") or AI_DEFAULTS["openrouter"]["url"], model)
        perf_add("ai", "фон для карточки", time.perf_counter() - t0)
        if not raw:
            continue
        try:
            img = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:
            continue
        with contextlib.suppress(OSError):
            img.save(path, "JPEG", quality=90)
        return img
    return None


from reloader.services.analytics import backgrounds as bgsrc  # noqa: E402

BG_DAILY_FILE = "bg_daily.json"         # фото для карточек на сегодня: день и адрес; само фото — bg_daily.jpg
BG_HTTP = requests.Session()            # своя сессия: к picsum не уходят настройки и заголовки MAX


def card_photo():
    """Фото для карточек — случайное с picsum.photos (без ключей), одно на сутки. None — не вышло: карточка рисует
    свой градиент. BG_PHOTO_URL="" выключает фото (так в проверках — без интернета)."""
    url = os.environ.get("BG_PHOTO_URL", bgsrc.PICSUM_URL)
    if not url:
        return None
    try:
        img, _ = bgsrc.daily_photo(BG_HTTP, BG_DAILY_FILE, url=url,
                                   log=lambda m: _log_line(time.strftime("%H:%M:%S") + " " + m))
    except Exception:
        log_exc("фото для карточек")
        return None
    return img


def card_bg(mode, topic):
    """Фон карточки. «🏞 фото дня» — фото с picsum.photos; «🤖 рисует ИИ» — картинка ИИ (не вышло — фото дня);
    «🎨 градиент» — без фото. None — свой премиальный градиент: карточка рисует его сама, на весь холст."""
    if mode == "off":
        return None
    if mode == "ai":
        img = ai_background(topic)
        if img is not None:
            return img
    return card_photo()


def card_caption(n):
    """Текст после картинки канала — ровно три строки: 📈 «Канал» / 👥 … подписчиков · сегодня …, за 7 дней … /
    👁 Просмотры на пост: 24 ч — …, 48 ч — …, 72 ч — … · ER24 …%. Чего не знаем — «—»."""
    f, n = anlimg, n if isinstance(n, dict) else {}
    title = " ".join(str(n.get("title") or "Канал").split())
    return (f"📈 «{title}»\n"
            f"👥 {f.fmt_int(n.get('subs'))} подписчиков · сегодня {f.fmt_delta(n.get('today'))}, "
            f"за 7 дней {f.fmt_delta(n.get('d7'))}\n"
            f"👁 Просмотры на пост: 24 ч — {f.fmt_int(n.get('v24'))}, 48 ч — {f.fmt_int(n.get('v48'))}, "
            f"72 ч — {f.fmt_int(n.get('v72'))} · ER24 {f.fmt_pct(n.get('er24'))}")


from reloader.services.analytics import insight as anlinsight  # noqa: E402

INSIGHT_CACHE = {}                       # цифры канала -> (когда, вывод ИИ): те же цифры за час — ИИ не спрашиваем
INSIGHT_TTL = 3600
INSIGHT_MAX = 10                         # выводов ИИ за одну сводку сети (дальше — только цифры)


def ai_insight(n, caption):
    """🤖 Вывод ИИ по цифрам канала (ротация ключей Gemini → OpenRouter → DeepSeek): 1–2 предложения по-русски —
    растёт ли канал, высокая ли вовлечённость. Нет ключей или ИИ не ответил — None (тогда три строки)."""
    if not ai_llm_ready():
        return None
    ask = anlinsight.ask_text(caption, anlimg.fmt_delta(n.get("d30")) if anlimg.num(n.get("d30")) is not None else None)
    got = INSIGHT_CACHE.get(ask)
    if got and time.time() - got[0] < INSIGHT_TTL:
        return got[1]
    t0 = time.perf_counter()
    try:
        out = anlinsight.clean(ai_ask_text(ask, AI_ROTATORS, S, ai_settings(), journal, prompt=anlinsight.PROMPT,
                                           timeout=20))
    except AiUnavailable:                              # все ключи 429 — без 🤖-строки, только три стандартные
        _log_line(time.strftime("%H:%M:%S") + " " + AI_EXHAUSTED_LOG)
        return None
    except Exception as e:
        _log_line(time.strftime("%H:%M:%S") + f" Вывод ИИ по каналу: не получен ({type(e).__name__}: {str(e)[:120]})")
        return None
    finally:
        perf_add("ai", "вывод по каналу", time.perf_counter() - t0)
    if out:
        INSIGHT_CACHE[ask] = (time.time(), out)
        while len(INSIGHT_CACHE) > 300:                # память не растёт
            INSIGHT_CACHE.pop(min(INSIGHT_CACHE, key=lambda k: INSIGHT_CACHE[k][0]), None)
    return out


def info_line(n):
    """📊 Доп. контекст под картинкой: средний охват (медиана просмотров поста), средний прирост подписчиков в день
    и лучший день недели. Чего не знаем — «—»."""
    f, n = anlimg, n if isinstance(n, dict) else {}
    growth = f.fmt_delta(n.get("growth_day")) if f.num(n.get("growth_day")) is not None else "—"
    return (f"📊 Средний охват: {f.fmt_int(n.get('avg_reach'))}, "
            f"Средний прирост: {growth} в день, Лучший день: {n.get('best_day') or '—'}")


def stats_text(n, ai=True):
    """Сообщение после картинки: три строки 📈/👥/👁, 4-я — 📊 доп. контекст (охват, прирост, лучший день),
    5-я — 🤖 вывод ИИ (если ИИ подключён и ответил). ИИ по-прежнему смотрит те самые три строки."""
    caption = card_caption(n)
    body = f"{caption}\n{info_line(n)}"
    line = ai_insight(n, caption) if ai else None
    return f"{body}\n🤖 {line}" if line else body


def upload_card(img, photo_like=False):
    """Картинку — в MAX, вернуть токен (None — не вышло). С фоном-узором или фото — JPEG: в 30+ раз быстрее и
    в 5 раз меньше PNG (шум фона PNG не сжимает); плоская карточка — PNG (чёткий текст, и так мало весит)."""
    ext = "jpg" if photo_like else "png"
    path = f"card_{secrets.token_hex(6)}.{ext}"
    try:
        if photo_like:
            img.convert("RGB").save(path, "JPEG", quality=92, subsampling=0)
        else:
            img.save(path, "PNG")
        return upload_image(path)
    except Exception as e:
        print("Аналитика: карточка не загрузилась:", e)
        return None
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


def send_blocks(chat, text, limit=3800):
    """Длинный текст — несколькими сообщениями, режем по пустой строке (блок канала не разрываем). Это цифры
    каналов — остаются в чате."""
    part = ""
    for block in str(text).split("\n\n"):
        if part and len(part) + 2 + len(block) > limit:
            api_send(chat, part, keep=True)
            part = ""
        part = f"{part}\n\n{block}" if part else block
    if part:
        api_send(chat, part, keep=True)


def send_card(chat, img, text_after=None):
    """Картинка аналитики — БЕЗ подписи, сразу за ней — текст (📈/👥/👁 про канал, 🤖). И то и другое остаётся в
    чате (не удаляем через час). Картинка не загрузилась — только текст."""
    tok = upload_card(img, True)
    if tok:
        api_send(chat, None, None, tok, keep=True)
    if text_after:
        send_blocks(chat, text_after)


def anl_cards_send(chat, chans, mode, bg="pattern"):
    """chans — my_channels(). mode "chan": карточка на каждый канал, "net": одна сводка по всей сети.
    Данные каналов собираем одновременно; всё это — в фоновом потоке, меню не ждёт."""
    try:
        from concurrent.futures import ThreadPoolExecutor
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as pool:
            nums = list(pool.map(lambda ch: anl_channel(*ch), chans))
        t1 = time.perf_counter()
        with chat_busy(chat, "sending_photo"):
            if mode == "net":
                net = anlimg.network_numbers(nums)
                bg_img = card_bg(bg, " ".join(n["title"] for n in nums)[:80])
                with ThreadPoolExecutor(max_workers=4) as pool:          # 📈/👥/👁 (+ 🤖) по каждому каналу
                    texts = list(pool.map(lambda x: stats_text(x[1], x[0] < INSIGHT_MAX), enumerate(nums)))
                send_card(chat, anlimg.render_network(net, card_font, bg_img), "\n\n".join(texts))
            else:
                def card(n):                             # фон, рисунок, загрузка и вывод ИИ — параллельно…
                    img = anlimg.render_channel(n, card_font, card_bg(bg, n["title"]), fetch_thumb(n.get("icon")))
                    return upload_card(img, True), stats_text(n)
                with ThreadPoolExecutor(max_workers=3) as pool:
                    tokens = list(pool.map(card, nums))
                for tok, text in tokens:                 # …а в чат — по порядку каналов
                    if tok:                              # картинка без подписи, за ней — 📈/👥/👁 (+ 🤖)
                        api_send(chat, None, None, tok, keep=True)
                    api_send(chat, text, keep=True)
        print(f"⏱ Карточки аналитики ({'сеть' if mode == 'net' else 'каналы'}, {len(nums)}): "
              f"данные MAX {t1 - t0:.1f} с, картинки и отправка {time.perf_counter() - t1:.1f} с")
    except Exception as e:
        log_exc("карточки аналитики")
        with contextlib.suppress(Exception):
            api_send(chat, f"⚠️ Карточка не получилась: {type(e).__name__}: {str(e)[:150]}. Попробуй ещё раз.")


def anl_cards_start(uid, mode):
    """Кнопки «🖼 Карточка канала» / «🌐 Сводка по всем каналам»: рисуем в фоне, картинка придёт в чат."""
    chat, chans = user_chat(uid), my_channels()
    if chat is None:
        return "Не нашёл твой чат с ботом — напиши боту /start и нажми ещё раз."
    if not chans:
        return "Нет привязанных каналов MAX — сначала «📢 Куда выкладывать»."
    key = f"{uid}:{mode}"
    if time.time() - ANL_CARD_BUSY.get(key, 0) < 180:
        return "⏳ Уже рисую — картинка придёт сюда сама, нажимать ещё раз не нужно."
    ANL_CARD_BUSY[key] = time.time()
    bg = anl_bg_mode(uid)

    def run():
        try:
            anl_cards_send(chat, chans, mode, bg)
        finally:
            ANL_CARD_BUSY.pop(key, None)

    threading.Thread(target=run, daemon=True, name="anl-cards").start()
    what = "сводку по всей сети" if mode == "net" else (
        "карточку канала" if len(chans) == 1 else f"карточки {len(chans)} каналов")
    return f"⏳ Рисую {what} — несколько секунд, картинка придёт сюда сама."


# ---------- 🔮 ПРОГНОЗ ПОСТА: оценка черновика до публикации ----------
def predict_history(cid):
    """Посты канала за 30 дней с просмотрами (для прогноза) — из памяти, если спрашивали недавно."""
    now = time.time()
    got = PRED_HIST.get(cid)
    if got is None or now - got[0] > 1800:
        got = (now, fetch_channel_msgs(cid, days=30, limit=300))
        PRED_HIST[cid] = got
        for k in [k for k, v in list(PRED_HIST.items()) if now - v[0] > 1800]:
            PRED_HIST.pop(k, None)                       # устаревшее — долой
        while len(PRED_HIST) > PRED_HIST_MAX:            # и не больше PRED_HIST_MAX каналов
            PRED_HIST.pop(min(PRED_HIST, key=lambda k: PRED_HIST[k][0]), None)
    return got[1]


def predict_text(text, photo=False, video=False, cid=None, name=None, with_p=False):
    """Ответ «🔮 Прогноз поста»: оценка, ожидаемые просмотры, лучшее время, советы (+ совет ИИ, если есть ключи).
    with_p — ещё и сами цифры прогноза (для карточки)."""
    hist = predict_history(cid) if cid else []
    p = anlpredict.predict(text, photo, video, hist, quality=post_quality(text, photo or video))
    out = anlpredict.explain(p, name)
    if (text or "").strip() and ai_llm_ready():
        t0 = time.perf_counter()
        try:
            tip = ai_ask_text(text[:3000], AI_ROTATORS, S, ai_settings(), journal)
            out += "\n\n🤖 Совет ИИ:\n" + tip.strip()[:700]
        except Exception as e:
            _log_line(time.strftime("%H:%M:%S") + f" Прогноз: ИИ не ответил ({type(e).__name__}: {str(e)[:120]})")
        finally:
            perf_add("ai", "совет к посту", time.perf_counter() - t0)
    return (out, p) if with_p else out


PREDICT_ASK = ("🔮 Прогноз поста\n\nПришли сюда черновик — текст, можно с фото или видео. Бот сравнит его с прошлыми "
               "постами твоего канала MAX и скажет:\n\n"
               "• ⭐ оценку из 10;\n• 👁 сколько примерно просмотров наберёт;\n• ⏰ лучшее время выложить;\n"
               "• что улучшить (и совет ИИ, если подключены ключи).\n\n"
               "Черновик никуда не публикуется и не попадает в очередь.")


def predict_start(uid, chat, text, atts=()):
    """Черновик пришёл: считаем в фоне (история канала из MAX + ИИ — до полуминуты), в чате «печатает…»."""
    rows = [[B("🔮 Ещё один пост", "a:predict")], [B("◀️ Аналитика", "m:anl")]]
    if not (text or "").strip() and not atts:
        api_send(chat, "Пришли текст поста (можно с фото или видео) — оценю его до публикации.", rows)
        return
    photo = any(a.get("kind") == "image" for a in atts or ())
    video = any(a.get("kind") == "video" for a in atts or ())
    t = cur() if cur().chat_id else next((x for x in (scope_themes() if HOST_MODE else THEMES) if x.chat_id), None)
    cid = t.chat_id if t is not None else None

    def run():
        tok = None
        try:
            with chat_busy(chat):
                name = (chat_info_cached(cid).get("title") or t.name) if cid else None
                answer, p = predict_text(text, photo, video, cid, name, with_p=True)
            try:                                       # 🔮 карточка прогноза + текст
                bg = card_bg(anl_bg_mode(uid), name or "Прогноз поста")
                tok = upload_card(anlimg.render_predict(p, card_font, bg, name), True)
            except Exception:
                log_exc("карточка прогноза")           # не вышла — ответ всё равно придёт текстом
        except Exception as e:
            log_exc("прогноз поста")
            answer = f"⚠️ Прогноз не получился: {type(e).__name__}. Попробуй ещё раз."
        if tok:                                        # картинка прогноза без подписи, за ней — ответ
            api_send(chat, None, None, tok, keep=True)
        api_send(chat, answer, rows, keep=True)

    threading.Thread(target=run, daemon=True, name="predict").start()


# ---------- 🔥 ТРЕНДЫ: бот сам следит за новостями по теме канала и пишет, только когда случилось вирусное ----------
# Не отчёт по расписанию, а сигнал по событию: фоновый поток раз в 2–3 часа на канал ищет свежие новости по словам
# из последних постов, ИИ строго отвечает «NO» или «YES [n]» с коротким текстом; «NO» — молчим. Одну новость
# (тот же адрес, перепечатку, ту же новость другими словами) одному получателю — один раз (sent_alerts.json).
from reloader.services.trends import search as trsearch  # noqa: E402
from reloader.services.trends import text as trtext  # noqa: E402
from reloader.services.trends.store import AlertStore  # noqa: E402
from reloader.services.trends.worker import Job as TrendJob, NoAI as TrendNoAI, TrendState, TrendWorker  # noqa: E402
from reloader.services.trends.worker import scan as trend_scan  # noqa: E402

TREND_FILE = "sent_alerts.json"          # что уже отправили (и что ИИ отклонил) — никогда не шлём одно дважды
TREND_STATE_FILE = "trends_state.json"   # когда следующая проверка каждой темы и чем кончилась прошлая
TREND_ALERTS = AlertStore(TREND_FILE, save=save_json, load=load_json_safe)
TREND_STATE = TrendState(TREND_STATE_FILE, save=save_json, load=load_json)
TREND_EVERY = (2 * 3600, 3 * 3600)       # каждая тема — раз в 2–3 часа (случайно: темы не идут пачкой)
TREND_TICK = 60                          # как часто фоновый поток смотрит, не пора ли проверить какую-то тему (сек)
TREND_AI_DAY = max(1, env_int("TREND_AI_DAILY", 150))   # вопросов ИИ о трендах в сутки на весь бот
TREND_LOCK = threading.Lock()            # одна проверка трендов за раз (фон и «🔎 Проверить сейчас»)
TREND_MANUAL = {}                        # uid -> когда нажал «Проверить сейчас» (не чаще раза в 5 минут)
TREND_PROXY = (os.environ.get("TREND_PROXY") or "").strip() or None   # прокси для DuckDuckGo, если нужен
TREND_WORKER = None
TREND_VERDICT = {"yes": "нашёл тренд — сигнал отправлен ✅", "no": "ничего вирусного по теме — промолчал",
                 "nothing-new": "новых новостей с прошлой проверки нет", "empty": "свежих новостей по словам не нашлось",
                 "off-topic": "новости только мельком задевают тему канала — промолчал",
                 "warmup": "память трендов восстанавливалась — эта проверка без сигнала (чтобы не повторить старое)",
                 "no-words": "в канале нет постов — не из чего взять слова", "no-ai": "⚠️ ИИ недоступен",
                 "ai-error": "⚠️ ИИ ответил ошибкой", "search-error": "⚠️ поиск новостей не ответил",
                 "send-error": "⚠️ не смог отправить сигнал", "error": "⚠️ сбой проверки"}


def _trend_timed(name, fn):
    def run(query, lang):
        t0 = time.perf_counter()
        try:
            return fn(query, lang)
        finally:
            perf_add("trend", f"поиск: {name}", time.perf_counter() - t0)
    return run


TREND_SEARCH = trsearch.Searcher([
    ("DuckDuckGo", _trend_timed("DuckDuckGo", lambda q, lang: trsearch.ddg_news(q, lang, proxy=TREND_PROXY))),
    ("Google News", _trend_timed("Google News", lambda q, lang: trsearch.google_news(q, WS, lang))),
])


def close_sessions():
    """При остановке бота: закрыть HTTP-сессии (MAX, ИИ, ЮKassa — S; t.me и Google News — WS) и клиентов
    DuckDuckGo. Пока бот работает, сессии одни на всех: соединения переиспользуются, новые не плодятся."""
    for sess in (S, WS):
        with contextlib.suppress(Exception):
            sess.close()
    with contextlib.suppress(Exception):
        trsearch.close_clients()


__import__("atexit").register(close_sessions)


def trend_scope(t):
    """Кому сигнал: кабинет покупателя (host-режим) или админ бота. У каждого своя память «уже отправляли»."""
    return f"cab:{t.owner}" if HOST_MODE and t.owner else "admin"


def trend_enabled(t):
    return bool(t.own.get("trend_on", True))


def trend_words(t):
    """Свои слова для поиска (список строк); испорчено в файле тем — как «не заданы»."""
    w = t.own.get("trend_words")
    return [x for x in w if isinstance(x, str) and x.strip()][:5] if isinstance(w, list) else []


def trend_allowed(t):
    """Тренды возможны: канал MAX привязан; в host-режиме — свой канал хозяина или кабинет покупателя, которому
    хозяин включил функцию (🧪 Бета-функции), оплаченный и не на бесплатном тарифе."""
    if not t.chat_id:
        return False
    if not HOST_MODE:
        return True
    if not t.owner:
        return False
    if str(t.owner) == host_id() or is_host(t.owner):
        return True
    return feature_on("trends", t.owner) and theme_live(t) and not on_free(t)


def trend_targets():
    """Темы для фоновой проверки."""
    return [t.id for t in list(THEMES) if trend_enabled(t) and trend_allowed(t)]


def trend_texts(t, limit=40):
    """Последние посты канала (и выложенные ботом, и ручные — из MAX) + то, что бот выложил и собирается
    выложить. Новые — первыми, без повторов."""
    got = []
    if t.chat_id:
        try:
            r = S.get(f"{MAX_API}/messages", params={"chat_id": t.chat_id, "count": 50}, headers=HEADERS, timeout=20)
            for m in (r.json().get("messages") or []) if r.status_code == 200 else []:
                body = m.get("body") or {}
                if body.get("text"):
                    got.append(((m.get("timestamp") or 0) / 1000, body["text"]))
        except Exception:
            pass                                          # MAX не ответил — хватит того, что бот помнит сам
    with theme_ctx(t):
        got += [(p.get("t", 0), p.get("raw") or p.get("text") or "") for p in PUB.get("day_posts", [])]
        got += [(x.get("enq", 0), x.get("text") or "") for x in list(QUEUE)]
    out, seen = [], set()
    for _, txt in sorted(got, key=lambda x: -(x[0] or 0)):
        key = " ".join(str(txt).split())[:200]
        if key and key not in seen:
            seen.add(key)
            out.append(str(txt))
    return out[:limit]


def trend_job(t):
    texts = trend_texts(t)
    with theme_ctx(t):
        sig = CFG["signature_text"] if CFG["signature_on"] else ""
    title = ((chat_info_cached(t.chat_id) or {}).get("title") if t.chat_id else "") or t.name
    lang = "ru" if trtext.cyr_share(texts) >= 0.3 else "en"
    return TrendJob(trend_scope(t), t.id, title, texts, trend_words(t), sig, lang)


def trend_ask(prompt):
    """Строгий вопрос ИИ — через те же ключи и ротацию (KeyRotator), что и пересказ постов. Нет ключей,
    все исчерпаны или дневной лимит трендов кончился — TrendNoAI (пересказу постов ключи нужнее)."""
    if not ai_llm_ready():
        raise TrendNoAI("нет ключей ИИ")
    if not TREND_STATE.ai_take(TREND_AI_DAY, time.strftime("%Y-%m-%d")):
        raise TrendNoAI(f"на сегодня лимит вопросов ИИ о трендах исчерпан ({TREND_AI_DAY})")
    t0 = time.perf_counter()
    try:
        return ai_ask_text(prompt, AI_ROTATORS, S, ai_settings(), journal, prompt="", timeout=45)
    except AiUnavailable as e:
        raise TrendNoAI(f"ИИ не ответил: {str(e)[:150]}") from None
    finally:
        perf_add("ai", "тренды", time.perf_counter() - t0)


def trend_send(t, text, item):
    """Сигнал — админам кабинета этой темы (как карточки постов). Отправлено хоть одному — True."""
    rows = []
    url = requests.utils.requote_uri(str(item.get("url") or ""))   # кириллица в адресе — %D0%… (MAX примет)
    if url.startswith(("https://", "http://")) and len(url) <= 1000:
        rows.append([LB("📰 Открыть новость", url)])
    rows.append([B("🔕 Не присылать тренды", f"a:trendoff:{t.id}")])
    with theme_ctx(t):
        chats = list(card_chats())
    sent = 0
    for chat in chats:
        for variant in (rows, rows[-1:]):                # кнопку-ссылку MAX не принял — шлём без неё
            try:
                if api_send(chat, text, variant):
                    sent += 1
                    break
            except Exception as e:
                _log_line(time.strftime("%H:%M:%S") + f" Тренды: не отправил в чат {chat}: {type(e).__name__}")
    if sent:
        with theme_ctx(t):
            journal(f"🔥 Тренд: {str(item.get('title') or '')[:120]}")
    return sent > 0


def trend_run(tid, manual=False):
    """Одна проверка темы на тренды (фон или «Проверить сейчас»). Итог scan + название канала."""
    t = theme_by_id(tid)
    if t is None:
        return {"verdict": "error", "error": "темы больше нет", "t": time.time()}
    with TREND_LOCK:
        job = trend_job(t)
        with theme_ctx(t):
            res = trend_scan(job, search=TREND_SEARCH.search, ask=trend_ask, store=TREND_ALERTS,
                             send=lambda text, item: trend_send(t, text, item))
    res["channel"] = job.channel
    TREND_STATE.record(tid, res)                          # для экрана «🔥 Тренды»: чем кончилась проверка
    with contextlib.suppress(Exception):
        TREND_STATE.save()
    line = (f"🔥 Тренды «{job.channel}»{' (вручную)' if manual else ''}: новостей {res.get('found', 0)}, "
            f"новых {res.get('new', 0)} — {TREND_VERDICT.get(res.get('verdict'), res.get('verdict'))}"
            + (f" ({res['error']})" if res.get("error") else ""))
    if res.get("verdict") in ("yes", "send-error", "ai-error", "error"):
        print(line)
    else:
        _log_line(time.strftime("%H:%M:%S") + " " + line)
    return res


def trend_start():
    """Фоновый поток трендов (один на бота). TRENDS=0 в переменных хостинга — выключить совсем."""
    global TREND_WORKER
    if (os.environ.get("TRENDS") or "1").strip().lower() in ("0", "off", "no", "false", "нет"):
        print("🔥 Тренды выключены переменной TRENDS=0")
        return None
    if TREND_WORKER is None:
        TREND_WORKER = TrendWorker(trend_targets, trend_run, TREND_STATE, interval=TREND_EVERY, tick=TREND_TICK,
                                   log=print)
        n = len(trend_targets())
        print(f"🔥 Тренды: слежу за каналами ({n}), каждый — раз в 2–3 часа; сигнал — только когда есть вирусное"
              + ("" if ai_llm_ready() else ". ⚠️ Нет ключей ИИ — сигналов не будет, пока их не добавят"))
    TREND_WORKER.start()
    return TREND_WORKER


def trend_result_text(res):
    """Ответ на «🔎 Проверить сейчас», когда сигнала нет (если есть — пришёл сам сигнал)."""
    v, words = res.get("verdict"), ", ".join((res.get("words") or [])[:5]) or "—"
    if v == "no":
        return (f"🔥 Проверил свежие новости по словам: {words} (новых: {res.get('new', 0)}) — ничего вирусного по "
                "теме канала. Как только что-то случится — пришлю сам.")
    if v == "nothing-new":
        return f"🔥 С прошлой проверки новых новостей по словам {words} нет. Слежу дальше — сигнал придёт сам."
    if v == "off-topic":
        return (f"🔥 Нашёл новости по словам {words}, но они только мельком задевают тему канала — это не тренд. "
                "Слежу дальше.")
    if v == "empty":
        return f"🔥 Свежих новостей по словам {words} за сутки не нашлось. Слежу дальше."
    if v == "no-words":
        return ("🔥 Не из чего взять слова для поиска: в канале пока нет постов. Задай свои: "
                "«🔥 Тренды» → «✏️ Свои слова для поиска».")
    if v == "no-ai":
        return (f"⚠️ ИИ недоступен ({res.get('error') or 'нет ключей'}) — без ИИ сигналы не отправляю: "
                "без строгого отбора пришлось бы слать всё подряд.")
    if v == "search-error":
        return f"⚠️ Поиск новостей не ответил: {res.get('error') or 'нет связи'}. Попробую в следующий раз."
    if v == "send-error":
        return "⚠️ Нашёл тренд, но MAX не принял сообщение — попробую в следующий раз."
    return f"⚠️ Проверка не получилась: {res.get('error') or v}. Попробую в следующий раз."


def trend_check_now(uid):
    """«🔎 Проверить сейчас»: в фоне (поиск + ИИ — до минуты), в чате «печатает…», ответ — сюда."""
    t, chat = cur(), user_chat(uid)
    if not t.chat_id:
        return "Сначала привяжи канал MAX — «📢 Куда выкладывать»."
    if not trend_allowed(t):
        return "🔥 Тренды для этого канала пока недоступны."
    if chat is None:
        return "Не нашёл твой чат с ботом — напиши боту /start и нажми ещё раз."
    if time.time() - TREND_MANUAL.get(str(uid), 0) < 300:
        return "⏳ Недавно проверял — подожди пару минут. Если случится вирусное, сигнал придёт сам."
    if TREND_LOCK.locked():
        return "⏳ Как раз идёт проверка трендов — если найдётся вирусное, сигнал придёт сам."
    TREND_MANUAL[str(uid)] = time.time()
    if len(TREND_MANUAL) > 500:                           # память не растёт: старые нажатия забываем
        for k in [k for k, v in list(TREND_MANUAL.items()) if time.time() - v > 3600]:
            TREND_MANUAL.pop(k, None)
    tid = t.id

    def run():
        try:
            with chat_busy(chat):
                res = trend_run(tid, manual=True)
            TREND_STATE.schedule(tid, time.time() + random.uniform(*TREND_EVERY))   # фон не повторит сразу
            TREND_STATE.save()
            if res.get("verdict") != "yes":
                api_send(chat, trend_result_text(res), [[B("🔥 Тренды", "m:trends")]])
        except Exception:
            log_exc("тренды: проверить сейчас")
            with contextlib.suppress(Exception):
                api_send(chat, "⚠️ Проверка трендов не получилась — попробуй позже.")

    threading.Thread(target=run, daemon=True, name="trend-now").start()
    return "🔎 Ищу свежие новости по теме канала и спрашиваю ИИ — ответ придёт сюда (до минуты)."


def trend_action(p, uid):
    """Кнопки «🔥 Тренды»: вкл/выкл, проверить сейчас, «🔕 Не присылать» из сигнала. Короткий ответ над экраном."""
    if p[0] == "trendnow":
        return trend_check_now(uid)
    t = cur() if p[0] == "trendtg" else theme_by_id(p[1] if len(p) > 1 else "")
    if t is None or (HOST_MODE and t.owner != cur().owner):
        return "Эта тема не твоя — тренды не трогаю."
    t.own["trend_on"] = p[0] == "trendtg" and not trend_enabled(t)
    save_cfg()
    if t.own["trend_on"]:
        return "🔥 Тренды включены — первая проверка в ближайшие 20 минут, дальше каждые 2–3 часа."
    return f"🔕 Тренды для «{t.name}» выключены. Включить снова: «📊 Аналитика» → «🔥 Тренды»."


def trend_words_set(t, text):
    raw = (text or "").strip()
    if raw.lower() in ("-", "авто", "auto", "нет", "сброс", "0"):
        t.own["trend_words"] = []
        save_cfg()
        return "✅ Слова для поиска трендов — снова из последних постов канала."
    words = [w.strip(" #@\"'«»") for w in re.split(r"[,;\n]+", raw)]
    words = list(dict.fromkeys(w[:40] for w in words if len(w) >= 2))[:5]
    if not words:
        return "Не понял. Пришли слова через запятую, например: roblox, brawl stars"
    t.own["trend_words"] = words
    save_cfg()
    return "✅ Ищу тренды по словам: " + ", ".join(words)


def trend_screen(uid):
    """Экран «🔥 Тренды» текущей темы. Без запросов в сеть — всё из памяти бота."""
    t = cur()
    on, last, nxt = trend_enabled(t), TREND_STATE.last(t.id), TREND_STATE.next(t.id)
    n, rec = TREND_ALERTS.stats(trend_scope(t))
    own = trend_words(t)
    lines = ["🔥 Тренды — сигнал, когда по теме канала случается что-то вирусное", "",
             "Каждые 2–3 часа бот ищет свежие новости по словам из последних постов канала. ИИ строго отбирает: "
             "только по-настоящему вирусное или важное, что точно подходит каналу. Есть такое — пришлю короткий "
             "сигнал: что случилось и почему стоит выложить прямо сейчас. Нет — молчу. Одна и та же новость "
             "дважды не приходит.", ""]
    lines.append(f"Канал: «{last.get('channel') or t.name}»" if t.chat_id else
                 "⚠️ Канал MAX не привязан — сначала «📢 Куда выкладывать».")
    lines.append("Статус: " + ("✅ слежу" if on else "⏸ выключено"))
    if own:
        lines.append("Слова для поиска (свои): " + ", ".join(own))
    elif last.get("words"):
        lines.append("Слова для поиска (из постов): " + ", ".join(last["words"][:6]))
    else:
        lines.append("Слова для поиска: возьму из последних постов канала при первой проверке")
    if last.get("t"):
        lines.append(f"Последняя проверка: {time.strftime('%d.%m %H:%M', time.localtime(last['t']))} — "
                     + TREND_VERDICT.get(last.get("verdict"), str(last.get("verdict")))
                     + (f" (новостей {last.get('found', 0)}, новых {last.get('new', 0)})" if last.get("found") else "")
                     + (f": {last['error']}" if last.get("error") and last.get("verdict") != "yes" else "")
                     + (f" ({last['reason']})" if last.get("reason") and last.get("verdict") == "no" else ""))
    if on and nxt and t.chat_id:
        lines.append(f"Следующая: ≈ {time.strftime('%H:%M', time.localtime(max(nxt, time.time())))}")
    lines.append(f"Сигналов отправлено: {n}" + (
        f"; последний {time.strftime('%d.%m', time.localtime(rec.get('t', 0)))}: «{rec.get('title', '')[:80]}»"
        if rec else ""))
    if not ai_llm_ready():
        lines += ["", "⚠️ ИИ не подключён — без строгого отбора ИИ сигналы не приходят. "
                  + ("Ключи ИИ добавляет владелец бота." if HOST_MODE and not can_panel(uid) else
                     "Добавь ключи ИИ (Gemini, OpenRouter или DeepSeek).")]
    if HOST_MODE and len(tenant_themes(t.owner)) > 1:
        lines += ["", "Это для текущей темы. Другая тема — переключи в «🎭 Мои каналы»."]
    rows = [[B("⏸ Выключить" if on else "▶️ Включить", "a:trendtg")],
            [B("🔎 Проверить сейчас", "a:trendnow")],
            [B("✏️ Свои слова для поиска", "p:trendwords")],
            [B("◀️ Аналитика", "m:anl")]]
    return "\n".join(lines), rows


# ---------- 🌐 СОЦСЕТИ-ИСТОЧНИКИ (бета): TikTok, VK, YouTube, Rutube, RSS — по ссылке на аккаунт ----------
SOCIAL_FILE = "social.json"
SOCIAL = load_json(SOCIAL_FILE, {})      # ключ источника («yt~UC…», «rss~a1b2…») -> {"url", "title"}
SOCIAL_TTL = 600                         # соцсети проверяем не чаще раза в 10 минут
SOCIAL_CACHE = {}                        # ключ -> (время, [сообщения])
SOCIAL_NETS = {"tt": "TikTok", "vk": "VK", "yt": "YouTube", "rt": "Rutube", "rss": "RSS"}
SOCIAL_NO = {"instagram.com": "Instagram", "x.com": "X (Twitter)", "twitter.com": "X (Twitter)",
             "facebook.com": "Facebook", "threads.net": "Threads"}


def is_social(ch):
    return "~" in str(ch or "")


def social_label(ch):
    net, _, rest = str(ch).partition("~")
    title = (SOCIAL.get(ch) or {}).get("title") or rest
    return f"{SOCIAL_NETS.get(net, net)}: {title}"


def ch_label(ch):
    return social_label(ch) if is_social(ch) else "@" + str(ch)


def social_parse(tok):
    """Ссылка на аккаунт соцсети -> (ключ, None) или (None, почему нельзя). Не ссылка — (None, None)."""
    t = tok.strip().strip("<>")
    if not re.match(r"(?i)(https?://)?([\w-]+\.)+[a-z]{2,}(/|$)", t) or re.match(r"(?i)(https?://)?(t\.me|telegram\.me)/", t):
        return None, None
    url = t if t.lower().startswith("http") else "https://" + t
    host = re.sub(r"^www\.|^m\.", "", re.match(r"(?i)https?://([^/]+)", url).group(1).lower())
    for dom, nm in SOCIAL_NO.items():
        if host == dom or host.endswith("." + dom):
            return None, f"{nm} не отдаёт посты без входа в аккаунт — пока не поддерживается"
    if host.endswith("tiktok.com"):
        m = re.search(r"/@([\w.]+)", url)
        return (f"tt~{m.group(1)}", None) if m else (None, "нужна ссылка на профиль: tiktok.com/@имя")
    if host in ("vk.com", "vk.ru"):
        m = re.match(r"(?i)https?://[^/]+/([\w.]+)/?$", url)
        return (f"vk~{m.group(1)}", None) if m else (None, "нужна ссылка на страницу: vk.com/имя")
    if host.endswith("youtube.com") or host == "youtu.be":
        m = re.search(r"/channel/(UC[\w-]{22})", url) or re.search(r"/(@[\w.-]+)", url) \
            or re.search(r"/(?:c|user)/([\w.-]+)", url)
        if not m:
            return None, "нужна ссылка на канал: youtube.com/@имя"
        key = "yt~" + m.group(1)
        SOCIAL.setdefault(key, {"url": url, "title": m.group(1)})
        return key, None
    if host.endswith("rutube.ru"):
        m = re.search(r"/channel/(\d+)", url)
        if not m:
            return None, "нужна ссылка на канал: rutube.ru/channel/123…"
        SOCIAL.setdefault(f"rt~{m.group(1)}", {"url": url, "title": m.group(1)})
        return f"rt~{m.group(1)}", None
    key = "rss~" + hashlib.md5(url.encode()).hexdigest()[:10]   # любой сайт/блог/Дзен с RSS
    SOCIAL.setdefault(key, {"url": url, "title": host})
    return key, None


def _soc_msg(ch, mid, ts, text, photos=(), video=None, direct=False):
    """Пост соцсети -> сообщения как у веб-Telegram (альбом = общий grouped_id)."""
    date = _dt.fromtimestamp(ts, _tz.utc)
    photos = [p for p in photos if p][:10]
    if not photos and not video:
        return [SocialMessage(ch, mid * 100, date, text)]
    out = []
    gid = mid if len(photos) + bool(video) > 1 else None
    for k, p in enumerate(photos):
        out.append(SocialMessage(ch, mid * 100 + k, date, text if k == 0 else "", photo=p, grouped_id=gid))
    if video:
        v = _Media(video)
        v.ytdl = not direct                       # прямая ссылка на mp4 — качаем сами, страница ролика — через yt-dlp
        out.append(SocialMessage(ch, mid * 100 + 99, date, "" if photos else text, video=v, grouped_id=gid))
    return out


class SocialMessage(WebMessage):
    async def download_media(self, file=None):
        if self.video is not None and getattr(self.video, "ytdl", False):
            try:
                return await asyncio.to_thread(ytdl_download, self.video.url, file)
            except Exception as e:
                if "tiktok.com" not in self.video.url:
                    raise
                print(f"TikTok: yt-dlp не скачал ролик ({str(e)[:120]}) — пробую через tikwm")
                play = await asyncio.to_thread(tikwm_play, self.video.url)
                return await asyncio.to_thread(_download_url, play, file)
        return await super().download_media(file)


def tikwm_play(url):
    """Прямая ссылка на mp4 ролика TikTok через tikwm (запасной путь, когда yt-dlp не смог)."""
    r = S.get("https://www.tikwm.com/api/", headers=WEB_UA, timeout=(8, 30), params={"url": url, "hd": 1})
    if r.status_code != 200:
        raise RuntimeError(f"tikwm ответил {r.status_code}")
    js = json.loads(r.text)
    d = js.get("data") or {}
    play = d.get("play") or d.get("hdplay") or d.get("wmplay")
    if js.get("code") != 0 or not play:
        raise RuntimeError(f"tikwm: {js.get('msg') or 'нет ссылки на ролик'}")
    return ("https://www.tikwm.com" + play) if play.startswith("/") else play


YTDL = {"tried": 0.0, "lock": threading.Lock()}


def get_ytdl():
    """Модуль yt-dlp. Нет на сервере — бот ставит его сам (pip), не чаще раза в час."""
    import importlib
    try:
        return importlib.import_module("yt_dlp")
    except ImportError:
        pass
    with YTDL["lock"]:
        try:
            return importlib.import_module("yt_dlp")
        except ImportError:
            pass
        if time.time() - YTDL["tried"] < 3600:
            raise RuntimeError("ставлю yt-dlp для TikTok и Rutube — не получилось, попробую через час")
        YTDL["tried"] = time.time()
        import subprocess
        import site
        print("Соцсети: нет yt-dlp — устанавливаю сам (pip install yt-dlp)...")
        for extra in ([], ["--user"]):
            try:
                r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp[default,curl-cffi]",
                                    *extra],
                                   capture_output=True, text=True, timeout=300)
            except Exception as e:
                print("Соцсети: pip не запустился:", e)
                break
            if r.returncode == 0:
                with contextlib.suppress(Exception):
                    us = site.getusersitepackages()
                    if us not in sys.path:
                        sys.path.append(us)
                importlib.invalidate_caches()
                try:
                    mod = importlib.import_module("yt_dlp")
                    print("Соцсети: yt-dlp установлен ✅")
                    return mod
                except ImportError:
                    pass
            else:
                print("Соцсети: pip install yt-dlp не вышел:", (r.stderr or r.stdout)[-300:])
    raise RuntimeError("для TikTok и Rutube нужен yt-dlp — сам поставить не смог; добавь строку yt-dlp "
                       "в requirements.txt и пересобери бота")


CFFI = {"tried": 0.0}


def ensure_cffi():
    """TikTok пускает только «как браузер» — для этого yt-dlp нужен curl_cffi. Нет — ставим (раз в час)."""
    import importlib
    try:
        importlib.import_module("curl_cffi")
        return True
    except ImportError:
        pass
    if time.time() - CFFI["tried"] < 3600:
        return False
    CFFI["tried"] = time.time()
    import subprocess
    print("Соцсети: для TikTok ставлю curl_cffi (yt-dlp[curl-cffi])...")
    with contextlib.suppress(Exception):
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp[default,curl-cffi]"],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--user", "-U",
                                "yt-dlp[default,curl-cffi]"], capture_output=True, text=True, timeout=300)
        importlib.invalidate_caches()
        importlib.import_module("curl_cffi")
        print("Соцсети: curl_cffi установлен ✅ — TikTok читается «как из браузера» (не читается — перезапусти бота)")
        return True
    return False


def ytdl_download(url, dest):
    """Видео из TikTok/Rutube через yt-dlp (если установлен)."""
    try:
        yt_dlp = get_ytdl()
    except RuntimeError as e:
        raise WebMediaUnavailable(str(e))
    if "tiktok.com" in url:
        ensure_cffi()                                  # TikTok пускает только «как браузер»
    base = os.path.splitext(dest)[0]
    opts = {"outtmpl": base + ".%(ext)s", "format": "mp4/best[ext=mp4]/best", "quiet": True, "noprogress": True,
            "max_filesize": CFG["video_mb"] * 1024 * 1024}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
        path = y.prepare_filename(info)
    if path != dest and os.path.exists(path):
        os.replace(path, dest)
    return dest


def soc_get(url):
    """Страница/лента соцсети (cookie согласия — чтобы YouTube в Европе не показывал заглушку)."""
    r = S.get(url, headers=WEB_UA, cookies={"CONSENT": "YES+1", "SOCS": "CAI"}, timeout=(8, 25))
    if r.status_code != 200:
        raise RuntimeError(f"{url.split('/')[2]} ответил {r.status_code}")
    return r.text


def _xml_text(s):
    s = re.sub(r"<br\s*/?>|</p>", "\n", s or "", flags=re.I)
    return _html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse_feed(ch, xml):
    """RSS/Atom -> [(id, время, текст, [фото])]."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    root = ET.fromstring(xml.encode() if isinstance(xml, str) else xml)
    out = []
    for e in root.iter():
        tag = e.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        get = lambda *names: next((c for n in names for c in e if c.tag.split("}")[-1] == n), None)
        title = (get("title").text or "").strip() if get("title") is not None else ""
        link_el = get("link")
        link = ((link_el.get("href") or link_el.text or "").strip()) if link_el is not None else ""
        desc_el = get("description", "summary", "encoded")
        group = get("group")                          # YouTube: media:group/media:description
        if desc_el is None and group is not None:
            desc_el = next((c for c in group if c.tag.split("}")[-1] == "description"), None)
        raw_desc = desc_el.text if desc_el is not None else ""
        when = get("pubDate", "published", "updated")
        ts = time.time()
        with contextlib.suppress(Exception):
            w = (when.text or "").strip()
            ts = (parsedate_to_datetime(w) if "," in w else _dt.fromisoformat(w.replace("Z", "+00:00"))).timestamp()
        pics = [c.get("url") for c in e.iter() if c.tag.split("}")[-1] in ("thumbnail", "content", "enclosure")
                and (c.get("type") or "image").startswith("image") and c.get("url")]
        pics += re.findall(r'<img[^>]+src="([^"]+)"', raw_desc or "")
        desc = _xml_text(raw_desc)
        if desc.startswith(title):
            desc = desc[len(title):].strip()
        text = "\n\n".join(x for x in (title, desc[:700] + ("…" if len(desc) > 700 else ""), link) if x)
        out.append((int(ts), ts, text, pics[:1]))
    return out


def yt_channel_id(ch):
    rest = ch.split("~", 1)[1]
    if rest.startswith("UC"):
        return rest
    known = (SOCIAL.get(ch) or {}).get("cid")
    if known:
        return known
    page = soc_get((SOCIAL.get(ch) or {}).get("url") or f"https://www.youtube.com/{rest}")
    m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', page) or re.search(r"channel/(UC[\w-]{22})", page)
    if not m:
        raise RuntimeError("YouTube: не нашёл канал по ссылке")
    SOCIAL.setdefault(ch, {})["cid"] = m.group(1)
    save_json(SOCIAL_FILE, SOCIAL)
    return m.group(1)


def vk_token():
    """Ключ VK: сохранённый в боте главнее переменной VK_TOKEN; в боте нет — из переменной."""
    return GCFG.get("vk_token") or (os.environ.get("VK_TOKEN") or "").strip() or None


def tiktok_cookies():
    """Cookies TikTok: сохранённые в боте главнее переменной TIKTOK_COOKIES; в боте нет — из переменной."""
    return GCFG.get("tt_cookies") or (os.environ.get("TIKTOK_COOKIES") or "").strip() or None


def parse_cookies(text):
    """Cookies из чата: экспорт Cookie-Editor (JSON-список) или строка «a=1; b=2» -> «a=1; b=2». Не похоже — ""."""
    text = (text or "").strip()
    pairs = []
    if text.startswith("["):
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            pairs = [(str(c["name"]).strip(), str(c["value"]).strip()) for c in json.loads(text)
                     if isinstance(c, dict) and c.get("name")]
    else:
        for part in re.split(r";\s*|\n", text.removeprefix("Cookie:").strip()):
            name, eq, value = part.partition("=")
            if eq and re.fullmatch(r"[\w.-]+", name.strip()):
                pairs.append((name.strip(), value.strip()))
    return "; ".join(f"{n}={v}" for n, v in pairs if n and v)


def tt_web_posts(user, cookies):
    """Ролики со страницы профиля TikTok, открытой с cookies хозяина (когда без входа TikTok не отдаёт):
    [(id, время, текст, обложка, ссылка на ролик)]. Понимает оба формата данных TikTok."""
    r = S.get(f"https://www.tiktok.com/@{user.lstrip('@')}", headers=dict(WEB_UA, Cookie=cookies), timeout=(8, 30))
    if r.status_code != 200:
        raise RuntimeError(f"ответил {r.status_code}")
    items = []
    for sid in ("SIGI_STATE", "__UNIVERSAL_DATA_FOR_REHYDRATION__"):
        m = re.search(rf'<script id="{sid}"[^>]*>(.*?)</script>', r.text, re.S)
        if not m:
            continue
        data = json.loads(m.group(1))
        items = (list((data.get("ItemModule") or {}).values()) if sid == "SIGI_STATE" else
                 ((data.get("__DEFAULT_SCOPE__") or {}).get("webapp.user-detail") or {}).get("itemList") or [])
        if items:
            break
    out = []
    for it in sorted(items, key=lambda i: -int(i.get("createTime") or 0)):
        vid, ts = str(it.get("id") or ""), int(it.get("createTime") or 0)
        if vid.isdigit() and ts:
            out.append((ts, ts, (it.get("desc") or "").strip(), (it.get("video") or {}).get("cover"),
                        f"https://www.tiktok.com/@{user.lstrip('@')}/video/{vid}"))
    if not out:
        raise RuntimeError("роликов нет на странице — cookies устарели? Обнови их в «🔑 Ключи и доступы»")
    return out


def vk_posts(domain):
    tok = vk_token()
    if not tok:
        raise RuntimeError("для VK нужен ключ: хозяин бота задаёт его в панели «🔑 Ключи и доступы» → «🔑 Ключ VK» "
                           "(или переменной VK_TOKEN в Bothost)")
    r = S.get("https://api.vk.com/method/wall.get", timeout=30,
              params={"domain": domain, "count": 20, "v": "5.199", "access_token": tok})
    js = r.json()
    if "error" in js:
        raise RuntimeError(f"VK: {js['error'].get('error_msg')}")
    out = []
    for p in js.get("response", {}).get("items", []):
        if p.get("marked_as_ads") or p.get("copy_history"):
            continue                                  # реклама и репосты — не берём
        text = re.sub(r"\[(?:id|club|public)\d+\|([^\]]+)\]", r"\1", p.get("text") or "")
        pics, video = [], None
        for a in p.get("attachments") or []:
            if a.get("type") == "photo":
                sizes = a["photo"].get("sizes") or []
                if sizes:
                    pics.append(max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))["url"])
            elif a.get("type") == "video" and video is None:
                v = a["video"]
                video = f"https://vk.com/video{v.get('owner_id')}_{v.get('id')}"
        out.append((int(p["id"]), p.get("date") or time.time(), text, pics, video))
    return out


def tikwm_posts(user, limit=10):
    """Последние ролики TikTok через открытый сервис tikwm.com (без входа в аккаунт):
    [(id, время, текст, [фото карусели], ссылка на mp4, обложка)]."""
    r = S.get("https://www.tikwm.com/api/user/posts", headers=WEB_UA, timeout=(8, 30),
              params={"unique_id": user.lstrip("@"), "count": limit, "cursor": 0})
    if r.status_code != 200:
        raise RuntimeError(f"tikwm ответил {r.status_code}")
    js = r.json()
    if js.get("code") != 0:
        raise RuntimeError(f"tikwm: {js.get('msg') or js.get('code')}")
    fix = lambda u: ("https://www.tikwm.com" + u) if isinstance(u, str) and u.startswith("/") else u
    out = []
    for v in ((js.get("data") or {}).get("videos") or [])[:limit]:
        vid = str(v.get("video_id") or v.get("aweme_id") or "")
        ts = v.get("create_time") or ((int(vid) >> 32) if vid.isdigit() and len(vid) > 15 else time.time())
        out.append((int(ts), ts, (v.get("title") or "").strip(), [fix(u) for u in v.get("images") or []],
                    fix(v.get("play") or v.get("wmplay")), fix(v.get("cover") or v.get("origin_cover"))))
    return out


def tt_waf_solve(cs):
    """Анти-бот задачка TikTok (как в RSSHub): найти n, при котором sha256(a + n) == c; ответ — в cookie."""
    import base64
    c = json.loads(base64.b64decode(cs))
    prefix, want = base64.b64decode(c["v"]["a"]), base64.b64decode(c["v"]["c"])
    for i in range(1_000_000):
        if hashlib.sha256(prefix + str(i).encode()).digest() == want:
            c["d"] = base64.b64encode(str(i).encode()).decode()
            return base64.b64encode(json.dumps(c, separators=(",", ":")).encode()).decode()
    raise RuntimeError("не решил анти-бот задачку")


def tt_embed_posts(user):
    """Последние ролики TikTok со страницы-виджета самого TikTok (tiktok.com/embed/@имя — для встраивания профиля
    на сайты; без входа и без сторонних сервисов, как в RSSHub): [(id, время, текст, обложка, ссылка на ролик)]."""
    path = f"/embed/@{user.lstrip('@')}"
    page = S.get("https://www.tiktok.com" + path, headers=WEB_UA, timeout=(8, 30))
    m = re.search(r'id="cs"[^>]*class="([^"]+)"|class="([^"]+)"[^>]*id="cs"|cs="([^"]+)",c', page.text)
    if "_wafchallengeid" in page.text and m:
        cookie = "_wafchallengeid=" + tt_waf_solve(next(g for g in m.groups() if g))
        page = S.get("https://www.tiktok.com" + path, headers=dict(WEB_UA, Cookie=cookie), timeout=(8, 30))
    if page.status_code != 200:
        raise RuntimeError(f"ответил {page.status_code}")
    m = re.search(r'<script id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>', page.text, re.S)
    if not m:
        raise RuntimeError("нет данных профиля на странице")
    data = json.loads(m.group(1))["source"]["data"]
    box = data.get(path) or next((v for k, v in data.items() if k.lower() == path.lower()), None) or {}
    if (box.get("userInfo") or {}).get("privateAccount"):
        raise RuntimeError("аккаунт закрытый")
    out = []
    for v in box.get("videoList") or []:
        vid = str(v.get("id") or "")
        if not vid.isdigit():
            continue
        ts = int(vid) >> 32                            # время публикации зашито в id ролика
        link = f"https://www.tiktok.com/@{v.get('authorUniqueId') or user}/video/{vid}" if v.get("playAddr") else None
        out.append((ts, ts, (v.get("desc") or "").strip(), v.get("coverUrl") or v.get("originCoverUrl"), link))
    if not out:
        raise RuntimeError("роликов нет")
    return out


class _QuietLog:
    """yt-dlp пишет ошибки прямо в консоль хостинга — забираем их себе (причину покажем коротко)."""
    def debug(self, msg):
        pass
    warning = info = debug

    def error(self, msg):
        pass


def ytdl_list(url, limit=10):
    """Список последних роликов профиля (TikTok, Rutube) через yt-dlp: [(id, время, текст, [превью], ссылка)]."""
    yt_dlp = get_ytdl()
    with yt_dlp.YoutubeDL({"extract_flat": True, "playlistend": limit, "quiet": True, "no_warnings": True,
                           "logger": _QuietLog()}) as y:
        info = y.extract_info(url, download=False)
    out = []
    for e in (info.get("entries") or [])[:limit]:
        vid = str(e.get("id") or "")
        ts = e.get("timestamp") or ((int(vid) >> 32) if vid.isdigit() and len(vid) > 15 else time.time())
        mid = int(ts) if not vid.isdigit() or len(vid) > 15 else int(vid)
        thumbs = [t.get("url") for t in (e.get("thumbnails") or []) if t.get("url")]
        link = e.get("webpage_url") or e.get("url") or url
        out.append((mid, ts, (e.get("title") or e.get("description") or "").strip(), thumbs[-1:], link))
    return out


def social_fetch(ch):
    """Последние посты источника-соцсети -> сообщения (как у веб-Telegram)."""
    net, _, rest = ch.partition("~")
    msgs = []
    if net == "vk":
        for mid, ts, text, pics, video in vk_posts(rest):
            msgs += _soc_msg(ch, mid, ts, text, pics, video if CFG["video_on"] and not pics else None)
    elif net == "tt":
        msgs = tiktok_msgs(ch, rest)
    elif net == "rt":
        for mid, ts, text, thumbs, link in ytdl_list(f"https://rutube.ru/channel/{rest}/videos/"):
            msgs += _soc_msg(ch, mid, ts, (text + "\n\n" + link).strip(), thumbs)
    elif net == "yt":
        feed = soc_get(f"https://www.youtube.com/feeds/videos.xml?channel_id={yt_channel_id(ch)}")
        for mid, ts, text, pics in parse_feed(ch, feed):
            msgs += _soc_msg(ch, mid, ts, text, pics)
    elif net == "rss":
        url = (SOCIAL.get(ch) or {}).get("url")
        if not url:
            raise RuntimeError("ссылка источника потерялась — удали его и добавь заново")
        for mid, ts, text, pics in parse_feed(ch, soc_get(url)):
            msgs += _soc_msg(ch, mid, ts, text, pics)
    else:
        raise RuntimeError(f"неизвестный источник {ch}")
    return msgs


def tiktok_msgs(ch, user):
    """TikTok по очереди: страница-виджет самого TikTok -> tikwm -> yt-dlp. Кто первый отдал ролики — того и берём."""
    errs, video_on = [], CFG["video_on"]
    try:                                               # 1) официальная страница-виджет TikTok
        msgs = []
        for mid, ts, text, cover, link in tt_embed_posts(user):
            msgs += _soc_msg(ch, mid, ts, text, [] if video_on and link else [cover], link if video_on else None)
        return msgs
    except Exception as e:
        errs.append(f"виджет TikTok — {str(e)[:150]}")
    if tiktok_cookies():                               # 2) профиль с cookies хозяина (вход в TikTok)
        try:
            msgs = []
            for mid, ts, text, cover, link in tt_web_posts(user, tiktok_cookies()):
                msgs += _soc_msg(ch, mid, ts, text, [] if video_on and link else [cover], link if video_on else None)
            return msgs
        except Exception as e:
            errs.append(f"профиль с cookies — {str(e)[:150]}")
    try:                                               # 3) открытый сервис tikwm
        msgs = []
        for mid, ts, text, imgs, play, cover in tikwm_posts(user):
            if imgs:
                msgs += _soc_msg(ch, mid, ts, text, imgs)              # пост-карусель из фото
            elif video_on and play:
                msgs += _soc_msg(ch, mid, ts, text, [], play, direct=True)
            else:
                msgs += _soc_msg(ch, mid, ts, text, [cover])
        return msgs
    except Exception as e:
        errs.append(f"tikwm — {str(e)[:150]}")
    try:                                               # 4) yt-dlp
        ensure_cffi()
        msgs = []
        for mid, ts, text, thumbs, link in ytdl_list(f"https://www.tiktok.com/@{user}"):
            msgs += _soc_msg(ch, mid, ts, text, [] if video_on else thumbs, link if video_on else None)
        return msgs
    except Exception as e:
        errs.append(f"yt-dlp — {str(e)[:150]}")
    print(f"TikTok @{user}: " + "; ".join(errs))
    raise RuntimeError("TikTok не отдаёт ролики этого аккаунта серверу (просит вход в аккаунт). "
                       "Проверь, что аккаунт открытый" + ("" if tiktok_cookies() else
                       ", или добавь cookies TikTok: панель «🔑 Ключи и доступы»"))


class SocialClient(WebClient):
    """Источник-соцсеть вместо Telegram: get_messages отдаёт посты TikTok/VK/YouTube/RSS."""

    def _cached(self, ch):
        hit = SOCIAL_CACHE.get(ch)
        if hit and time.time() - hit[0] < SOCIAL_TTL:
            return hit[1]
        msgs = social_fetch(ch)
        SOCIAL_CACHE[ch] = (time.time(), msgs)
        return msgs

    async def get_messages(self, ch, min_id=0, limit=30, ids=None):
        msgs = await asyncio.to_thread(self._cached, ch)
        if ids is not None:
            return next((m for m in msgs if m.id == ids), None)
        return sorted([m for m in msgs if m.id > (min_id or 0)], key=lambda m: -m.id)[:limit]


SOCIAL_CLIENT = SocialClient()


def pool_client(path):
    """Клиент Telethon для файла входа из пула — с теми же ключами, что и основной вход."""
    keys = (API_ID, API_HASH) if field_ok("api_id", API_ID) and field_ok("api_hash", API_HASH) else builtin_keys()
    if not keys:
        raise SessionError("нет ключей Telegram (api_id/api_hash) для входа")
    return tg_client(path[:-len(".session")], *keys)


# 👤 Пул аккаунтов Telegram: файлы .session в sessions/, закрепление каналов — в pool.json
POOL = AccountPool("sessions", "pool.json", pool_client, main_session="session.session")
POOL_TASKS = set()     # фоновые вступления аккаунтов пула в новые источники


def pool_run(coro, timeout=60):
    """Действие с пулом из меню (меню MAX работает в своём потоке, пул — в главном цикле бота)."""
    if MAIN_LOOP is not None and MAIN_LOOP.is_running():
        return asyncio.run_coroutine_threadsafe(coro, MAIN_LOOP).result(timeout)
    return asyncio.run(coro)


async def pool_assign(ch):
    try:
        acc = await POOL.assign(ch)
        journal(f"👤 [{ch}] читает аккаунт пула «{acc.name}»")
    except PoolBusy as e:
        journal(f"👤 [{ch}] {e} — пока читаю свободным аккаунтом или основным входом")
    except Exception as e:
        journal(f"👤 [{ch}] аккаунт пула не вступил в канал: {type(e).__name__}: {e}")


def pool_spawn(ch):
    """Новый источник: аккаунт пула вступает в фоне — чтение не ждёт (публичный канал читается и так)."""
    if is_social(ch) or not POOL.usable() or POOL.owner(ch):
        return
    task = asyncio.get_running_loop().create_task(pool_assign(ch))
    POOL_TASKS.add(task)
    task.add_done_callback(POOL_TASKS.discard)


def src_client(client, ch):
    """Чем читать источник: соцсеть — своим клиентом; Telegram — аккаунтом пула (если есть), иначе основным."""
    if is_social(ch):
        return SOCIAL_CLIENT
    return POOL.client_for(ch) or client


# ---------- 🪄 ИИ-РЕДАКТОР (бета, бесплатно, без ключей): пересказ поста другими словами ----------
# Группы взаимозаменяемых слов и фраз (в одной и той же форме). Тема и смысл не меняются, длина почти та же.
AI_GROUPS = [
    "очень|весьма|крайне", "сейчас|теперь", "в настоящее время|на данный момент", "быстро|оперативно|моментально",
    "скоро|совсем скоро|в скором времени", "также|кроме того|вдобавок", "однако|тем не менее|впрочем",
    "поэтому|так что|по этой причине", "например|к примеру", "конечно|разумеется|безусловно",
    "вероятно|скорее всего|по всей видимости", "действительно|в самом деле|и правда",
    "примерно|приблизительно|порядка", "почти|практически", "сразу|мгновенно|тут же",
    "потому что|так как|поскольку", "много|немало", "нужно|необходимо|надо",
    "новый|свежий", "новая|свежая", "новое|свежее", "новые|свежие", "новых|свежих", "новым|свежим",
    "большой|крупный", "большая|крупная", "большое|крупное", "большие|крупные",
    "интересный|любопытный", "интересная|любопытная", "интересное|любопытное", "интересные|любопытные",
    "классный|крутой", "классная|крутая", "классное|крутое", "классные|крутые",
    "отличный|прекрасный", "отличная|прекрасная", "отличное|прекрасное", "отличные|прекрасные",
    "огромный|колоссальный", "огромная|колоссальная", "огромное|колоссальное",
    "сообщили|рассказали", "сообщил|рассказал", "сообщила|рассказала",
    "показали|представили", "показал|представил", "показала|представила",
    "вышел|появился", "вышла|появилась", "вышло|появилось", "вышли|появились",
    "начался|стартовал", "началась|стартовала", "началось|стартовало",
    "стало известно|выяснилось", "получить|забрать", "получите|заберите", "получи|забирай",
    "заходи|залетай", "заходите|залетайте", "успей|поторопись", "успейте|поторопитесь",
    "узнать|выяснить", "узнайте|выясните", "игроки|геймеры", "игроков|геймеров",
    "розыгрыш|конкурс", "розыгрыша|конкурса", "розыгрыше|конкурсе", "подарки|призы", "подарков|призов",
    "бесплатно|даром|без оплаты", "хочешь|желаешь", "главное|основное", "смотри|глянь",
    "в итоге|в результате|в конце концов", "кстати|между прочим", "наконец|в конце концов",
]
AI_SYN = {}
for _g in AI_GROUPS:
    _ws = _g.split("|")
    for _w in _ws:
        AI_SYN.setdefault(_w, [x for x in _ws if x != _w])
AI_RX = re.compile(r"(?<![\wёЁ])(" + "|".join(sorted(map(re.escape, AI_SYN), key=len, reverse=True)) + r")(?![\wёЁ])",
                   re.IGNORECASE)
AI_KEEP = re.compile(r"(https?://\S+|\]\([^)]*\)|[@#][\w.]+|`[^`]*`)")   # ссылки, @каналы, #теги — не трогаем
AI_TOPICS = [("⚽", "футбол матч гол лига чемпионат хоккей спорт"), ("🎮", "игр игра геймер steam playstation xbox пабг "
              "standoff roblox minecraft"), ("💰", "деньг курс рубл доллар банк крипт биткоин цена скидк"),
             ("📱", "iphone айфон смартфон телефон android приложен обновлен"), ("🚗", "авто машин водител дорог"),
             ("🎬", "фильм кино сериал трейлер"), ("🎵", "музык песн трек альбом клип"),
             ("🍽", "рецепт еда блюд вкусн"), ("🌦", "погод дожд снег жара мороз"), ("🤖", "нейросет chatgpt ии "
              "искусствен робот")]


def _ai_case(src, alt):
    if len(src) > 1 and src.isupper():
        return alt.upper()                                # КАПС остаётся КАПСОМ
    if src[:1].isupper():
        return alt[:1].upper() + alt[1:]
    return alt


def ai_edit(text):
    """Пересказ своими словами: меняем слова и обороты на синонимы. Ссылки, @каналы, #теги, цифры, эмодзи,
    КАПС, призывы и длина — как были. Для одного и того же поста результат всегда одинаковый."""
    if not text or not text.strip():
        return text
    rnd = random.Random(hashlib.md5(text.encode()).hexdigest())
    pick = lambda m: (_ai_case(m.group(0), rnd.choice(AI_SYN[m.group(0).lower()]))
                      if m.group(0).lower() in AI_SYN and rnd.random() < 0.85 else m.group(0))
    parts = AI_KEEP.split(text)
    out = "".join(p if i % 2 else AI_RX.sub(pick, p) for i, p in enumerate(parts))
    if not re.match(r"[\U0001F300-\U0001FAFF☀-➿]", out):
        low = out.lower()
        for emo, keys in AI_TOPICS:
            if any(k in low for k in keys.split()):
                out = emo + " " + out
                break
    return out


def ai_on():
    return bool(CFG.get("ai_edit")) and feature_on("ai") and not on_free()


# ---------- 🧩 СВОЙ ТАРИФ (бета): покупатель сам выбирает темы и источники ----------
def custom_prices():
    p = GCFG.get("custom_tariff") or {}
    return int(p.get("base", 200)), int(p.get("theme", 100)), int(p.get("src5", 50))


def custom_tariff(themes, sources):
    themes = max(1, min(10, int(themes)))
    sources = max(5, min(50, int(sources) // 5 * 5))
    base, per_t, per_s = custom_prices()
    price = base + per_t * (themes - 1) + per_s * ((sources - 5) // 5)
    return {"name": f"Свой: {themes} тем, {sources} ист.", "themes": themes, "sources": sources, "price": price}


def pick_tariff(i):
    """Тариф по коду кнопки: номер из списка или «c<темы>x<источники>» — свой тариф."""
    m = re.fullmatch(r"c(\d+)x(\d+)", str(i))
    if m:
        return custom_tariff(int(m.group(1)), int(m.group(2)))
    return tariffs()[int(i)]


def custom_from_name(plan):
    m = re.fullmatch(r"Свой: (\d+) тем, (\d+) ист\.", plan or "")
    return custom_tariff(int(m.group(1)), int(m.group(2))) if m else None


def mytariff_screen(arg):
    m = re.fullmatch(r"(\d+)x(\d+)", arg or "")
    th, src = (int(m.group(1)), int(m.group(2))) if m else (max(1, lim_themes()), max(5, lim_sources()))
    t = custom_tariff(th, src)
    th, src = t["themes"], t["sources"]
    key = cur().owner if HOST_MODE else None
    base, per_t, per_s = custom_prices()
    text = (f"🧩 Собери свой тариф\n\nТем (каналов MAX): {th}\nИсточников в теме: до {src}\n\n"
            f"Цена: {plan_price(t, 30, key)} ₽ за 30 дней · {plan_price(t, 90, key)} ₽ за 90 дней\n\n"
            f"Как считается: {base} ₽ — 1 тема и 5 источников, +{per_t} ₽ за каждую тему, "
            f"+{per_s} ₽ за каждые 5 источников.")
    c = f"c{th}x{src}"
    rows = [[B("➖ тема", f"m:myt:{max(1, th - 1)}x{src}"), B("➕ тема", f"m:myt:{th + 1}x{src}")],
            [B("➖ 5 источников", f"m:myt:{th}x{max(5, src - 5)}"), B("➕ 5 источников", f"m:myt:{th}x{src + 5}")],
            [B(f"30 дн — {plan_price(t, 30, key)} ₽", f"py:{c}:30"), B(f"90 дн — {plan_price(t, 90, key)} ₽", f"py:{c}:90")],
            [B("◀️ Тарифы", "m:pay")]]
    return text, rows


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
        c = tg_client(TG_SESSION_TMP, int(TG_LOGIN["api_id"]), TG_LOGIN["api_hash"])
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
    new = tg_client("session", API_ID, API_HASH)
    await new.connect()
    if ON_NEW is not None:
        with contextlib.suppress(Exception):
            from telethon import events
            new.add_event_handler(ON_NEW, events.NewMessage())
    if isinstance(box, ClientBox):
        box.cur = new
    SHARED["on"] = False
    with contextlib.suppress(OSError):
        os.remove(WEB_FLAG)
    phone = PHONE
    TG_LOGIN.clear()
    print("Telegram: аккаунт подключён из меню MAX ✅")
    await asyncio.to_thread(owner_alert, f"🔐 К боту подключён аккаунт Telegram ({mask_phone(phone)}).\n\n"
                            "Если это сделал не ты: 🔑 Telegram → 🚪 Отключить, а в самом Telegram — "
                            "Настройки → Устройства → завершить чужие сеансы.")
    if not CLIENT_MODE and CLIENTS and GCFG.get("share_tg", False):
        for cid in list(PROCS):                      # боты клиентов перезапустятся уже с этим аккаунтом
            with contextlib.suppress(Exception):
                stop_client(cid)


def builtin_keys():
    """Ключи, с которыми вход — только номер и код: продавца (у бота клиента), иначе Telegram Desktop."""
    k = (os.environ.get("TGMAX_API_ID") or "", os.environ.get("TGMAX_API_HASH") or "")
    if field_ok("api_id", k[0]) and field_ok("api_hash", k[1]):
        return k
    return None if os.environ.get("TGMAX_NO_DESKTOP_KEYS") == "1" else DESKTOP_KEYS


SHARED = {"on": False}  # бот клиента читает каналы через аккаунт продавца


# ---------- ОБЩИЙ АККАУНТ: боты клиентов читают каналы через вход продавца ----------
# Один вход Telegram нельзя запускать в нескольких программах сразу — Telegram рвёт связь всем. Поэтому вход
# только у бота продавца, а боты клиентов просят его прочитать канал (локально, 127.0.0.1, с ключом).
HUB = {"url": "", "key": ""}


def _doc_json(m):
    d = getattr(m, "document", None)
    if d is None:
        return None
    dur = 0
    for a in getattr(d, "attributes", None) or []:
        if getattr(a, "duration", None):
            dur = int(a.duration or 0)
    fl = getattr(m, "file", None)
    return {"size": int(getattr(d, "size", 0) or 0), "mime": getattr(d, "mime_type", "") or "", "duration": dur,
            "name": getattr(fl, "name", None) or "", "ext": getattr(fl, "ext", None) or ""}


def msg_json(m):
    return {"id": m.id, "date": m.date.timestamp(), "text": m.text or "", "grouped_id": m.grouped_id,
            "photo": bool(getattr(m, "photo", None)), "video": bool(getattr(m, "video", None)),
            "gif": bool(getattr(m, "gif", None)), "video_note": bool(getattr(m, "video_note", None)),
            "sticker": bool(getattr(m, "sticker", None)), "doc": _doc_json(m), "fwd": fwd_key(m),
            "buttons": url_buttons(m)}


def hub_start():
    """Бот продавца: локальный сервис чтения каналов для ботов клиентов."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse
    import secrets
    import shutil
    import tempfile

    def tg():
        c = TG_CLIENT
        return None if c is None or is_web_client(c) or not GCFG.get("share_tg", False) else c

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get("X-Key") != HUB["key"]:
                return self._json(403, {"error": "key"})
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            c = tg()
            if u.path == "/ping":
                return self._json(200, {"tg": c is not None})
            if c is None:
                return self._json(503, {"error": "у продавца нет входа в Telegram"})
            try:
                if u.path == "/msgs":
                    ids = int(q["ids"]) if q.get("ids") else None
                    got = asyncio.run_coroutine_threadsafe(
                        c.get_messages(q["ch"], min_id=int(q.get("min_id") or 0), limit=int(q.get("limit") or 30))
                        if ids is None else c.get_messages(q["ch"], ids=ids), MAIN_LOOP).result(150)
                    if ids is not None:
                        return self._json(200, {"msgs": [msg_json(got)] if got else []})
                    return self._json(200, {"msgs": [msg_json(m) for m in got or []]})
                if u.path == "/media":
                    msg = asyncio.run_coroutine_threadsafe(c.get_messages(q["ch"], ids=int(q["id"])),
                                                           MAIN_LOOP).result(150)
                    if not msg:
                        return self._json(404, {"error": "пост удалён"})
                    tmp = tempfile.mkdtemp(dir=".")
                    try:
                        path = asyncio.run_coroutine_threadsafe(msg.download_media(file=tmp + "/"),
                                                                MAIN_LOOP).result(1800)
                        if not path or not os.path.exists(path):
                            return self._json(404, {"error": "нет файла"})
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Length", str(os.path.getsize(path)))
                        self.end_headers()
                        with open(path, "rb") as fh:
                            shutil.copyfileobj(fh, self.wfile, 1 << 16)
                    finally:
                        shutil.rmtree(tmp, ignore_errors=True)
                    return
                return self._json(404, {"error": "?"})
            except Exception as e:
                return self._json(502, {"error": f"{type(e).__name__}: {e}"[:300],
                                        "wait": int(getattr(e, "seconds", 0) or 0)})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    HUB.update(url=f"http://127.0.0.1:{srv.server_address[1]}", key=secrets.token_hex(16))
    threading.Thread(target=srv.serve_forever, daemon=True).start()


class _RDoc:
    def __init__(self, d):
        self.size, self.mime_type, self.name = d["size"], d["mime"], d["name"]
        self.attributes = [types.SimpleNamespace(duration=d["duration"])] if d.get("duration") else []
        self.url = None


class RemoteMessage:
    """Сообщение канала, прочитанное ботом продавца: ведёт себя как сообщение Telethon."""

    def __init__(self, hub, ch, j):
        self._hub, self._ch = hub, ch
        self.id, self.text, self.grouped_id = j["id"], j["text"], j["grouped_id"]
        self.date = _dt.fromtimestamp(j["date"], _tz.utc)
        d = _RDoc(j["doc"]) if j.get("doc") else None
        self.document = d
        self.photo = True if j["photo"] else None
        self.video = d if j["video"] else None
        self.gif = d if j["gif"] else None
        self.video_note = d if j["video_note"] else None
        self.sticker = True if j["sticker"] else None
        self.file = types.SimpleNamespace(name=(j["doc"] or {}).get("name") or None, ext=(j["doc"] or {}).get("ext"))
        self.url_buttons = int(j.get("buttons") or 0)
        f = j.get("fwd")
        self.fwd_from = types.SimpleNamespace(channel_post=f.split(":")[1], from_id=types.SimpleNamespace(
            channel_id=f.split(":")[0])) if f else None

    async def download_media(self, file=None):
        return await asyncio.to_thread(self._hub.fetch_media, self._ch, self.id, file)


class RemoteClient:
    """Бот клиента: читает каналы через бота продавца (его вход в Telegram)."""

    def __init__(self, url, key):
        self.url, self.key = url, key

    def on(self, event):
        return lambda f: f

    def add_event_handler(self, *a):
        pass

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def is_user_authorized(self):
        return True

    def _get(self, path, params, stream=False, timeout=180):
        try:
            r = requests.get(self.url + path, params=params, headers={"X-Key": self.key}, timeout=timeout,
                             stream=stream, proxies={"http": None, "https": None})
        except requests.RequestException as e:
            raise ConnectionError(f"бот продавца не отвечает: {e}") from e
        if r.status_code in (502, 503):
            j = r.json()
            raise ConnectionError(j.get("error") or "Telegram у продавца недоступен")
        if r.status_code != 200 and not stream:
            raise RuntimeError(r.json().get("error") or f"ошибка {r.status_code}")
        return r

    def ping(self):
        try:
            return bool(self._get("/ping", {}, timeout=10).json().get("tg"))
        except Exception:
            return False

    def fetch_media(self, ch, mid, dest):
        r = self._get("/media", {"ch": ch, "id": mid}, stream=True, timeout=(10, 1800))
        if r.status_code != 200:
            raise RuntimeError(f"файл не скачался ({r.status_code})")
        if dest.endswith("/") or os.path.isdir(dest):
            os.makedirs(dest, exist_ok=True)
            dest = os.path.join(dest, f"{mid}")
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)
        return dest

    async def get_messages(self, ch, min_id=0, limit=30, ids=None):
        params = {"ch": ch, "min_id": min_id, "limit": limit}
        if ids is not None:
            params["ids"] = ids
        j = (await asyncio.to_thread(self._get, "/msgs", params)).json()
        msgs = [RemoteMessage(self, ch, x) for x in j.get("msgs") or []]
        if ids is not None:
            return msgs[0] if msgs else None
        return msgs


async def shared_client():
    """Бот клиента без своего входа: читает каналы через бота продавца (если тот разрешил и вошёл в Telegram)."""
    url, key = os.environ.get("TGMAX_HUB") or "", os.environ.get("TGMAX_HUB_KEY") or ""
    if not (CLIENT_MODE and url and key) or os.path.exists(NO_SHARED_FLAG):
        return None
    rc = RemoteClient(url, key)
    if await asyncio.to_thread(rc.ping):
        SHARED["on"] = True
        print("Telegram: читаю каналы через аккаунт продавца")
        return rc
    return None


def use_builtin_keys():
    k = builtin_keys()
    if k and not TG_LOGIN.get("api_hash"):
        TG_LOGIN.update(api_id=int(k[0]), api_hash=k[1], builtin=True)
    return bool(k)


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
        c = tg_client(TG_SESSION_TMP, int(TG_LOGIN["api_id"]), TG_LOGIN["api_hash"])
        TG_LOGIN["client"] = c
        await c.connect()
        if not await c.is_user_authorized():
            qr = await c.qr_login()
            for _ in range(8):
                if TG_LOGIN.get("client") is not c:      # нажали «Отмена»
                    return
                path = await asyncio.to_thread(qr_png, qr.url, "tg_login_qr.png")
                token = await asyncio.to_thread(upload_image, path)
                await say(QR_TEXT + "\n\n📱 Telegram на этом же телефоне и QR не навести? Нажми или скопируй "
                          f"ссылку — откроется Telegram и спросит, разрешить ли вход:\n{qr.url}", [TG_CANCEL], token)
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
    if SHARED["on"] and TG_CLIENT is not None and not is_web_client(TG_CLIENT):
        text = ("🔑 Telegram: подключён ✅ (общий аккаунт)\n\nНичего делать не нужно: бот уже читает каналы через "
                "аккаунт — переносятся файлы и большие видео.\n\nСвой аккаунт нужен, только если хочешь брать посты "
                "из закрытых каналов, где состоишь ты.")
        rows = [[B("🔑 Подключить свой аккаунт", "tg:start")], [B("🚪 Отключить общий аккаунт", "tg:off")]]
    elif TG_CLIENT is not None and not is_web_client(TG_CLIENT):
        text = (f"🔑 Аккаунт Telegram: подключён ✅\nНомер: {mask_phone(PHONE)}\n\n"
                "Каналы читаю через аккаунт: видны все посты и большие видео.")
        rows = [[B("🔄 Подключить другой аккаунт", "tg:start")], [B("📷 Другой аккаунт по QR-коду", "tg:qr")],
                [B("🚪 Отключить Telegram", "tg:off")]]
    elif builtin_keys():
        text = ("🔑 Аккаунт Telegram: не подключён\n\n"
                "Сейчас читаю открытые каналы через t.me — это работает, но закрытые каналы, файлы (PDF и т.п.) "
                "и большие видео недоступны.\n\n"
                "Подключить просто: нужен только номер телефона и код, который придёт в Telegram. "
                "Сайты и ключи не нужны.\n\n"
                "Если код не приходит — «📷 Войти по QR-коду».")
        rows = [[B("🔑 Подключить аккаунт", "tg:start")], [B("📷 Войти по QR-коду (без кода)", "tg:qr")]]
        if not CLIENT_MODE:
            rows.append([B("🔧 Свои ключи (api_id)", "tg:own")])
    else:
        text = ("🔑 Аккаунт Telegram: не подключён\n\n"
                "Сейчас читаю публичные каналы через t.me — это работает, но большие видео недоступны.\n\n"
                "Для подключения понадобятся:\n"
                "1) api_id и api_hash — с сайта my.telegram.org (раздел API development tools)\n"
                "2) номер телефона аккаунта\n"
                "3) код, который придёт в приложение Telegram\n\n"
                "Если код не приходит — «📷 Войти по QR-коду»: сканируешь QR телефоном, коды не нужны.")
        rows = [[B("🔑 Подключить аккаунт", "tg:start")], [B("📷 Войти по QR-коду (без кода)", "tg:qr")]]
    if CLIENT_MODE and os.path.exists(NO_SHARED_FLAG) and os.environ.get("TGMAX_HUB") and is_web_client(TG_CLIENT):
        rows.append([B("🔗 Включить общий аккаунт", "tg:shareon")])
    if not is_owner(uid):
        text += "\n\nПодключать аккаунт может только владелец бота."
        rows = []
    return text, rows + [[B("◀️ Меню", "m:main")]]


NO_SHARED_FLAG = "no_shared.flag"      # бот клиента: общий аккаунт продавца выключен самим клиентом


async def tg_logout():
    """Отключить Telegram: завершаем вход (пропадёт из «Устройств»), стираем файл входа, читаем через t.me."""
    box = TG_CLIENT
    cur = getattr(box, "cur", box)
    shared = SHARED["on"]
    if cur is not None and not is_web_client(cur) and not shared:
        with contextlib.suppress(Exception):
            await cur.log_out()
    if cur is not None and not is_web_client(cur):
        with contextlib.suppress(Exception):
            await cur.disconnect()
    if shared:
        open(NO_SHARED_FLAG, "w").close()
    else:
        for f in ("session.session", "session.session-journal"):
            with contextlib.suppress(OSError):
                os.remove(f)
    open(WEB_FLAG, "w").close()
    SHARED["on"] = False
    if isinstance(box, ClientBox):
        box.cur = WebClient()
    print("Telegram: отключён из меню MAX — читаю через t.me")
    if not CLIENT_MODE:
        for cid in list(PROCS):                  # боты клиентов больше не читают через этот аккаунт
            with contextlib.suppress(Exception):
                stop_client(cid)


async def tg_share_on():
    with contextlib.suppress(OSError):
        os.remove(NO_SHARED_FLAG)
    rc = await shared_client()
    if rc is not None and isinstance(TG_CLIENT, ClientBox):
        TG_CLIENT.cur = rc
    return rc is not None


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
    text = TG_STEPS[step]
    if TG_LOGIN.get("builtin"):                      # ключи встроены — шагов всего два
        text = text.replace("Шаг 3 из 4", "Шаг 1 из 2").replace("Шаг 4 из 4", "Шаг 2 из 2")
    return (prefix + "\n\n" if prefix else "") + text, rows + [TG_CANCEL]


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
        if use_builtin_keys():                       # ключи уже есть — сразу номер
            return tg_ask(uid, "tg_phone")
        return tg_ask(uid, "tg_api_id")
    if rest == "off":
        what = "общий аккаунт" if SHARED["on"] else "аккаунт Telegram"
        return (f"🚪 Отключить {what}?\n\nБот продолжит работать и читать открытые каналы через t.me, но файлы, "
                "закрытые каналы и большие видео переноситься не будут." + ("" if SHARED["on"] else
                "\n\nВход завершится и в самом Telegram (пропадёт из «Настройки → Устройства»)."),
                [[B("✅ Да, отключить", "tg:offok")], [B("❌ Нет", "tg:menu")]])
    if rest == "offok":
        if MAIN_LOOP is not None:
            with contextlib.suppress(Exception):
                tg_run(tg_logout(), 60)
        text, rows = tg_screen(uid)
        return "✅ Telegram отключён. Читаю открытые каналы через t.me.\n\n" + text, rows
    if rest == "shareon":
        ok = MAIN_LOOP is not None and tg_run(tg_share_on(), 30)
        text, rows = tg_screen(uid)
        return ("✅ Снова читаю через общий аккаунт.\n\n" if ok else "Общий аккаунт сейчас недоступен — "
                "попробуй позже.\n\n") + text, rows
    if rest == "own":                                # вход со своими api_id/api_hash
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
        use_builtin_keys()
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


# ---------- ПРОДАЖА: БОТЫ КЛИЕНТОВ ----------
# Продавец (владелец этого бота) в меню «💼 Клиенты» добавляет клиента по токену его бота MAX. Бот клиента —
# этот же файл, запущенный отдельным процессом со своей папкой clients/<id>/ и TGMAX_CLIENT=1. Срок кончился
# или продавец выключил — бот клиента перезапускается в режиме «оплатите» (настройки сохраняются).
CLIENTS_FILE = "clients.json"
CLIENTS_DIR = "clients"
CLIENTS = {} if CLIENT_MODE else load_json(CLIENTS_FILE, {})
PROCS = {}               # id клиента -> {"p": Popen, "mode": ..., "t": время запуска}
CL_NEW = {}              # user_id продавца -> id клиента, которого сейчас заводим
BOT_FILE = os.path.abspath(globals().get("__file__") or "tg_to_max.py")


def save_clients():
    with CL_LOCK:
        save_json(CLIENTS_FILE, CLIENTS)


def client_dir(cid):
    return os.path.abspath(os.path.join(CLIENTS_DIR, cid))


def client_mode(c, now=None):
    """run — работает, expired — срок кончился, off — выключен продавцом."""
    if not c.get("enabled", True):
        return "off"
    return "run" if c.get("until", 0) > (now or time.time()) else "expired"


def sub_info():
    """Подписка текущего кабинета: в host-режиме — из TENANTS, у клиента-подпроцесса — из subscription.json."""
    if HOST_MODE:
        key = getattr(cur(), "owner", "")
        c = TENANTS.get(key) if key else None
        if not c:
            return {}
        info = {"until": c.get("until", 0), "plan": c.get("plan", ""), "mode": tenant_mode(key),
                "themes": c.get("lim_themes", LIMIT_THEMES), "sources": c.get("lim_sources", LIMIT_SOURCES),
                "tariffs": tariffs(), "pay": GCFG.get("pay_info", ""), "online": bool(yk_keys())}
        if info["mode"] == "free":                       # 🆓 срок кончился — лимиты бесплатного тарифа
            info.update(plan=FREE_PLAN["name"], themes=FREE_PLAN["themes"], sources=FREE_PLAN["sources"])
        return info
    return load_json("subscription.json", {}) if CLIENT_MODE else {}


def tenant_mode(key, now=None):
    """run — оплачен (или пробный срок), free — бесплатный тариф (срок кончился или не оплачивал), off — выключен хозяином."""
    c = TENANTS.get(key)
    if c is None or not c.get("enabled", True):          # кабинета нет (потерялся) или хозяин выключил
        return "off"
    return "run" if c.get("until", 0) > (now or time.time()) else "free"


def tenant_live(key):
    """Кабинет работает: оплачен или на бесплатном тарифе (не выключен хозяином)."""
    return tenant_mode(key) in ("run", "free")


def theme_live(t):
    """Тема работает: кабинет живой; на бесплатном тарифе — только первые FREE_PLAN["themes"] темы, остальные на паузе."""
    if not HOST_MODE or not t.owner:
        return True
    mode = tenant_mode(t.owner)
    if mode == "run":
        return True
    return mode == "free" and t in tenant_themes(t.owner)[:FREE_PLAN["themes"]]


def on_free(t=None):
    """Тема — кабинет покупателя на бесплатном тарифе (не оплачен): действуют ограничения FREE_PLAN."""
    t = t or cur()
    return bool(HOST_MODE and getattr(t, "owner", "") and tenant_mode(t.owner) == "free")


def live_channels():
    """Источники текущей темы, которые читаем: на бесплатном тарифе — первые FREE_PLAN["sources"], остальные на паузе
    (не удаляются: оплатит — снова читаются)."""
    chans = list(CFG["channels"])
    if on_free():                                     # 🆓 соцсети — только на платном
        return [c for c in chans if not is_social(c)][:FREE_PLAN["sources"]]
    return chans


LAST_POLL = {}         # (тема, источник) -> когда читали (бесплатный тариф: раз в FREE_PLAN["check_min"] минут)


def poll_due(t, ch, full, woke, now=None):
    """Пора ли читать источник: платный — на каждом круге (full) или по событию (woke);
    бесплатный — раз в FREE_PLAN["check_min"] минут, события не ускоряют."""
    if not on_free(t):
        return full or ch.lower() in woke
    now = now or time.time()
    key = (t.id, ch.lower())
    if now - LAST_POLL.get(key, 0) < FREE_PLAN["check_min"] * 60:
        return False
    LAST_POLL[key] = now
    return True


FREE_HINTS = {}        # тема -> когда подсказали, что фото и видео — на платном тарифе


def free_media_hint(now=None):
    """🆓 Пост с фото/видео вышел текстом — раз в сутки подсказываем про платный тариф."""
    now = now or time.time()
    t = cur()
    if now - FREE_HINTS.get(t.id, 0) < 86400:
        return
    FREE_HINTS[t.id] = now
    notify("📷 В посте источника были фото или видео — на бесплатном тарифе переношу только текст. "
           "Фото, видео и файлы — на платном тарифе: «💳 Продлить доступ».")


@cabinet_locked
def new_cabinet_theme(key, name):
    """Создаёт тему (канал) для кабинета key и возвращает её."""
    t = Theme(new_theme_id(), name, "", owner=str(key))
    if HOST_MODE and str(key) != host_id():               # покупателю — посты выходят сами, без нажатий и долгих пауз
        t.own["approval_on"] = False
        t.own["gap_min"] = 20
    t.own["channels"] = []
    t.own["old_signatures"] = []
    t.own["signature_text"] = ""
    THEMES.append(t)
    save_cfg()
    return t


@cabinet_locked
def create_tenant(uid, chat, name="", username=""):
    """Новый кабинет для пользователя: запись + одна пустая тема (+ пробный период). Возвращает ключ кабинета."""
    key = str(uid)
    TENANTS[key] = blank_tenant(uid, name)
    TENANTS[key]["chats"][str(uid)] = chat
    TENANTS[key]["names"][str(uid)] = name or str(uid)
    if username:
        TENANTS[key]["usernames"] = {str(uid): username}
    save_tenants()
    if not tenant_themes(key):                            # темы остались (кабинет потерялся) — не плодим пустую
        new_cabinet_theme(key, "Мой канал")
    print(f"HOST: новый кабинет {name or uid} (ID {uid})")
    setup = ("Подключи свой канал MAX и каналы Telegram, откуда брать посты: «🎭 Мои каналы» → «⚙️ Эта тема» → "
             "«📢 Канал MAX» и «📡 Каналы-источники».")
    days = int(GCFG.get("trial_days", 3) or 0)
    if days > 0 and tariffs():                            # 🎁 пробный доступ без промокода — один раз, при первом «Старте»
        host_set_plan(key, 0)
        host_extend(key, days)
        TENANTS[key]["trial"] = True
        save_tenants()
        tenant_send(key, f"🎁 Тебе включён бесплатный пробный доступ на {days} {n_word(days, 'день', 'дня', 'дней')} — "
                         f"до {fmt_date(TENANTS[key]['until'])}.\n{setup} Понравится — продлишь кнопкой «💳 Подписка».")
        host_alert(f"🎁 Новый покупатель «{name or uid}» — включил ему пробные {days} дн.")
    else:
        tenant_send(key, f"🆓 Ты на бесплатном тарифе: {free_plan_text()}, навсегда. {setup} Нужно больше — «💳 Подписка».")
    tenant_send(key, CONTACT_ASK, [[CONTACT_BTN]])
    return key


# Бот MAX пишет только от своего имени (у MAX нет API «от аккаунта человека», а Telethon — это Telegram, не MAX).
# Чтобы хозяин мог написать покупателю лично, бот просит у покупателя контакт кнопкой MAX «Поделиться контактом».
CONTACT_BTN = {"type": "request_contact", "text": "📇 Поделиться контактом"}
CONTACT_ASK = ("📇 Хочешь, чтобы продавец помог с настройкой лично? Нажми «Поделиться контактом» — бот передаст ему "
               "твой номер, и он напишет тебе в MAX. Не хочешь — просто пропусти.")


def max_url(text):
    """Ссылка из контакта или ссылки на канал: https://… — как есть (и внутри текста), «max.ru/…» и «web.max.ru/…»
    без https, «@ник» или просто ник (латиницей) — https://max.ru/ник. Телефон и прочее — None."""
    c = (text or "").strip()
    m = re.search(r"(?i)\bhttps?://[^\s<>«»\"']+", c)
    if m:
        return m.group(0).rstrip(".,;:!?)")
    m = re.search(r"(?i)(?<![\w./@])((?:web\.)?max\.ru/[^\s<>«»\"']+)", c)
    if m:
        return "https://" + m.group(1).rstrip(".,;:!?)")
    m = re.search(r"(?<![\w.])@([A-Za-z][\w.]{2,})", c) or re.fullmatch(r"([A-Za-z][\w.]{2,})", c)
    return "https://max.ru/" + m.group(1).rstrip(".") if m else None


def seller_link():
    """Ссылка на личный чат хозяина в MAX — из контакта «📞 Изменить мой контакт», иначе ник хозяина.
    None — открыть нечем (например, контакт — телефон)."""
    nick = GCFG.get("host_nick")
    return max_url(SELLER_CONTACT) or (f"https://max.ru/{nick}" if nick else None)


def contact_vcf(msg):
    """vCard из контакта, которым поделились (как пришёл от MAX); "" — нет."""
    for a in ((msg or {}).get("body") or {}).get("attachments") or []:
        if a.get("type") == "contact":
            return (a.get("payload") or {}).get("vcf_info") or ""
    return ""


def api_send_contact(chat_id, vcf, text=""):
    """Контакт покупателя — вложением «контакт» (та же форма, что приходит от MAX)."""
    body = {"text": text, "attachments": [{"type": "contact", "payload": {"vcf_info": vcf}}]}
    r = S.post(f"{MAX_API}/messages", params={"chat_id": chat_id}, headers=HEADERS, json=body, timeout=30)
    return r.status_code == 200


def msg_contact(msg):
    """Телефон из контакта, которым поделились кнопкой («+79991234567»); "" — контакт без номера; None — не контакт."""
    for a in ((msg or {}).get("body") or {}).get("attachments") or []:
        if a.get("type") == "contact":
            m = re.search(r"TEL[^:\n]*:([+\d][\d\s()\-]{5,})", (a.get("payload") or {}).get("vcf_info") or "")
            digits = re.sub(r"\D", "", m.group(1)) if m else ""
            if len(digits) == 11 and digits.startswith("8"):
                digits = "7" + digits[1:]
            return "+" + digits if digits else ""
    return None


def save_contact(uid, chat_id, name, phone, vcf=""):
    """Покупатель поделился контактом: номер и карточка — в кабинет; хозяин открывает её кнопкой «📇 Контакт»."""
    key = tenant_by_chat(chat_id) or tenant_key(uid)
    if not phone or key not in TENANTS:
        api_send(chat_id, "Не вижу номера в контакте. Попробуй ещё раз кнопкой «📇 Поделиться контактом».")
        return
    TENANTS[key]["phone"] = phone
    TENANTS[key]["contact"] = {"phone": phone, "vcf": vcf, "t": time.time()}
    save_tenants()
    api_send(chat_id, "Спасибо! Передал контакт продавцу — он сможет написать тебе лично.")
    host_alert(f"📇 Покупатель «{TENANTS[key].get('name') or name or key}» поделился контактом: {phone}\n\n"
               "Написать лично: добавь номер в контакты телефона — человек появится в MAX, пиши из своего аккаунта.",
               [[B("👤 Кабинет", f"m:clc:{key}")]])


def tenant_send(key, text, rows=None, image_token=None):
    """Сообщение главному пользователю кабинета от имени бота (можно с картинкой)."""
    c = TENANTS.get(key) or {}
    chat = (c.get("chats") or {}).get(str((c.get("admins") or [None])[0]))
    if chat:
        with contextlib.suppress(Exception):
            return 1 if api_send(chat, text, rows, image_token) else 0
    return 0


# ---------- 📢 РЕКЛАМНАЯ РАССЫЛКА ХОЗЯИНА: пользователям бесплатного тарифа, в чат с ботом ----------
AD_DRAFT = {}            # хозяин -> черновик: текст, картинка, маркировка, erid, куда показать предпросмотр
ERID_RE = re.compile(r"erid\s*[:：]?\s*([\w-]{4,})", re.I)


def ad_targets():
    """Кому реклама: кабинеты на бесплатном тарифе. Платные и выключенные хозяином её не получают."""
    return [k for k in TENANTS if k != host_id() and tenant_mode(k) == "free" and not TENANTS[k].get("ads_off")]


def ad_image(att):
    """Картинка из сообщения хозяина -> токен MAX для отправки другим (MAX разрешает повторно слать по токену)."""
    if not att:
        return None
    if att.get("token"):
        return att["token"]
    os.makedirs(MEDIA_DIR, exist_ok=True)
    return upload_image(fetch_file(att["url"], f"{MEDIA_DIR}/ad_{int(time.time())}", ".jpg")) if att.get("url") else None


def ad_input(uid, chat_id, action, text, atts):
    """Шаги рассылки: пост (текст и/или картинка) -> маркировка «Реклама … erid» -> предпросмотр с кнопкой «Разослать»."""
    text = (text or "").strip()
    if action == "cladpost":
        img = next((a for a in atts if a["kind"] == "image"), None)
        if not text and not img:
            PENDING[uid] = "cladpost"
            api_send(chat_id, "Нужен текст и/или картинка.\n\n" + PROMPTS["cladpost"])
            return
        AD_DRAFT[uid] = {"text": text, "image": ad_image(img), "chat": chat_id}
        PENDING[uid] = "cladmark"
        api_send(chat_id, PROMPTS["cladmark"], [[B("❌ Отмена", "a:cladno")]])
        return
    d, m = AD_DRAFT.get(uid), ERID_RE.search(text)
    if not d:
        api_send(chat_id, "Черновик рекламы потерялся — начни заново: «📢 Сделать рассылку».")
        return
    if not m:
        PENDING[uid] = "cladmark"
        api_send(chat_id, "Без erid рекламу по закону рассылать нельзя.\n\n" + PROMPTS["cladmark"],
                 [[B("❌ Отмена", "a:cladno")]])
        return
    mark = re.sub(r"(?i)^реклама[\s.:,—-]*", "", text)
    d.update(mark=mark, erid=m.group(1), body=(d["text"] + "\n\n" if d["text"] else "") + "Реклама. " + mark)
    n = len(ad_targets())
    api_send(chat_id, d["body"], [[B(f"✅ Разослать ({n} получ.)", "a:cladgo")], [B("❌ Отмена", "a:cladno")]],
             d["image"])                                  # предпросмотр — ровно так, как увидят получатели


def ad_broadcast(d):
    """Рассылка по одному, с паузой (MAX не любит залпы). Итог — хозяину; число доставленных — в журнал для ОРД."""
    targets = ad_targets()
    sent = 0
    for k in targets:
        sent += tenant_send(k, d["body"], [[B(NOADS_BTN, "lg:noads")]], d["image"])   # отписка — в каждой рекламе
        time.sleep(0.05)
    log = GCFG.setdefault("ad_log", [])
    log.append({"t": time.time(), "erid": d["erid"], "mark": d["mark"], "text": d["text"][:300], "sent": sent})
    del log[:-500]
    save_cfg()
    journal(f"📢 Реклама (erid {d['erid']}): доставлено {sent} из {len(targets)}")
    api_send(d["chat"], f"📢 Разослал рекламу: доставлено {sent} из {len(targets)} (erid {d['erid']}).\n"
                        "Число доставленных сохранено — пригодится для отчёта в ОРД.")


def who_pressed(u):
    """Кто на самом деле написал/нажал. В нажатии кнопки message.sender — это сам бот, нужен callback.user."""
    for cand in ((u.get("callback") or {}).get("user"), u.get("user"), (u.get("message") or {}).get("sender")):
        if cand and not cand.get("is_bot"):
            return cand
    return {}


BOT_TITLE = {}


def bot_me():
    """Имя и ник нашего бота в MAX (один раз за запуск)."""
    if not BOT_TITLE:
        try:
            r = S.get(f"{MAX_API}/me", headers=HEADERS, timeout=20)
            if r.status_code == 200:
                BOT_TITLE.update({k: r.json().get(k) for k in ("name", "username", "user_id")})
        except Exception:
            pass
    return BOT_TITLE


def bot_like_nick(nick):
    """Ник похож на бота (так раньше записывалось вместо покупателя) — не показываем и не ссылаемся."""
    n = str(nick or "").lower().lstrip("@")
    return n.endswith("bot") or n.endswith("_bot")


def clean_bot_names():
    """Разовая чистка: имена/ники бота, ошибочно записанные покупателям при нажатии кнопок."""
    changed = False
    me = bot_me()
    for rec in TENANTS.values():
        for uid, nick in list((rec.get("usernames") or {}).items()):
            if bot_like_nick(nick):
                rec["usernames"].pop(uid, None)
                changed = True
                bad = rec.get("names", {}).get(uid)
                if bad and (bad == me.get("name") or not me):
                    rec["names"].pop(uid, None)
        for uid, nm in list((rec.get("names") or {}).items()):
            if nm and nm == me.get("name"):
                rec["names"].pop(uid, None)
                changed = True
    if changed:
        save_tenants()
    return changed


def host_prepare(uid, chat, u, kind):
    """Host-режим: заводим кабинет новому пользователю, запоминаем его чат; хозяину — его чат для уведомлений."""
    if uid is None:
        return
    rec = (u.get("message") or {}).get("recipient") or {}
    dialog = kind != "message_created" or rec.get("chat_type") in (None, "dialog")
    if is_host(uid):
        if chat and dialog:
            HOST_CHAT["chat"] = chat                     # уведомления хозяину — в его личный чат, не в канал
        nick = (who_pressed(u).get("username") or "").lstrip("@")
        if nick and not bot_like_nick(nick) and GCFG.get("host_nick") != nick:
            GCFG["host_nick"] = nick                     # для кнопки «💬 Написать мне лично» у покупателей
            save_cfg()
        return
    if kind == "message_created" and rec.get("chat_type") not in (None, "dialog"):
        return
    sender = who_pressed(u)
    track_cabinet(uid, chat, sender.get("first_name") or sender.get("name") or "",
                  (sender.get("username") or "").lstrip("@"))


@cabinet_locked
def track_cabinet(uid, chat, name="", nick=""):
    """Host-режим: у каждого, кто пишет боту, свой кабинет. Нет — заводим; есть — запоминаем id/чат/имя/ник."""
    if uid is None:
        return
    key = tenant_key(uid) or tenant_by_chat(chat)        # ищем кабинет по id ИЛИ по чату (MAX шлёт разные id)
    if key is None:
        if chat is None:
            return
        create_tenant(uid, chat, name, nick)
        return
    rec = TENANTS[key]
    changed = False
    if not any(same_uid(a, uid) for a in (rec.get("admins") or [])):   # ещё один id того же покупателя
        rec.setdefault("admins", []).append(uid)
        changed = True
    if chat and rec.setdefault("chats", {}).get(str(uid)) != chat:
        rec["chats"][str(uid)] = chat
        changed = True
    if name and rec.setdefault("names", {}).get(str(uid)) != name:
        rec["names"][str(uid)] = name
        changed = True
    if nick and rec.setdefault("usernames", {}).get(str(uid)) != nick:
        rec["usernames"][str(uid)] = nick
        changed = True
    if chat:                                              # убрать пустые кабинеты-дубли с тем же чатом (из-за разных id MAX)
        for k2, c2 in list(TENANTS.items()):
            if k2 != key and any(same_uid(ch, chat) for ch in (c2.get("chats") or {}).values()) \
                    and tenant_mode(k2) != "run" and not any(tt.own.get("channels") for tt in tenant_themes(k2)):
                for tt in list(tenant_themes(k2)):
                    with contextlib.suppress(Exception):
                        delete_theme(tt)
                TENANTS.pop(k2, None)
                changed = True
                print(f"HOST: убрал кабинет-дубль {k2} (тот же покупатель, другой id MAX)")
    if changed:
        save_tenants()


# ---------- 📌 ОБЯЗАТЕЛЬНАЯ ПОДПИСКА (host-режим): ботом пользуются только подписчики каналов хозяина ----------
FSUB_TTL = 1800          # подписку проверили — полчаса не спрашиваем MAX снова
FSUB_OK = {}             # пользователь -> до какого времени считаем, что подписан
FSUB_WARN = {}           # канал -> когда сказали хозяину, что проверить нельзя


def force_subs():
    return GCFG.setdefault("force_subs", [])


def is_member(chat_id, uid):
    """Подписан ли пользователь на канал MAX. None — не узнать (бот не админ канала или MAX не ответил)."""
    try:
        r = S.get(f"{MAX_API}/chats/{chat_id}/members", headers=HEADERS, params={"user_ids": str(uid)}, timeout=15)
        if r.status_code != 200:
            return None
        return any(same_uid(m.get("user_id"), uid) for m in r.json().get("members") or [])
    except Exception:
        return None


def fsub_missing(uid, fresh=False):
    """Обязательные каналы, на которые пользователь не подписан. Проверить нельзя — пускаем (не запираем всех из-за
    настройки канала) и говорим хозяину."""
    subs = force_subs()
    if not subs or (not fresh and FSUB_OK.get(uid, 0) > time.time()):
        return []
    missing = []
    for c in subs:
        ok = is_member(c["chat_id"], uid)
        if ok is None:
            if time.time() - FSUB_WARN.get(c["chat_id"], 0) > 6 * 3600:
                FSUB_WARN[c["chat_id"]] = time.time()
                host_alert(f"⚠️ Не могу проверить подписку на «{c['title']}»: бот больше не администратор канала или "
                           f"MAX не отвечает. Пока пускаю всех. Проверь, что бот — админ канала: {c['link']}")
        elif not ok:
            missing.append(c)
    if not missing:
        FSUB_OK[uid] = time.time() + FSUB_TTL
    return missing


def fsub_lock(missing, again=False):
    names = ", ".join(f"«{c['title']}»" for c in missing)
    text = (("Пока не вижу подписку. " if again else "")
            + f"📢 Чтобы пользоваться ботом, подпишись на наш канал {names}, потом нажми «✅ Проверить подписку».")
    rows = [[LB("📢 Подписаться на канал" if len(missing) == 1 else f"📢 {c['title']}"[:60], c["link"])]
            for c in missing]
    return text, rows + [[B("✅ Проверить подписку", "fs:check")]]


def fsub_gate(u, kind, uid, chat):
    """True — пользователь не подписан: показали экран «Подписаться / Проверить», дальше не идём."""
    if not force_subs() or uid is None or is_host(uid):
        return False
    if kind == "message_created":
        if ((u.get("message") or {}).get("recipient") or {}).get("chat_type") not in (None, "dialog"):
            return False                                   # сообщения в каналах — не от пользователя бота
    elif kind not in ("message_callback", "bot_started"):
        return False
    cb = u.get("callback") or {}
    again = kind == "message_callback" and cb.get("payload") == "fs:check"
    missing = fsub_missing(uid, fresh=again)
    if not missing:
        if again:
            FLASH[uid] = "✅ Подписка есть — спасибо! Добро пожаловать."
        return False
    text, rows = fsub_lock(missing, again)
    if kind == "message_callback":
        api_answer(cb.get("callback_id"), text, rows)
    elif chat:
        send_screen(chat, text, rows)
    return True


def chat_info(chat_id):
    """Название и ссылка канала MAX. {} — не узнать."""
    try:
        r = S.get(f"{MAX_API}/chats/{chat_id}", headers=HEADERS, timeout=20)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


CHAN_LINKS = {}          # chat_id канала MAX -> (ссылка, когда спросили MAX): карточки не спрашивают MAX каждый раз


def theme_channel_url(t):
    """Ссылка на канал MAX, куда выкладывает тема: как её отдаёт MAX по chat_id (сам канал, а не то, что
    когда-то вводили), иначе — ссылка из настроек темы. "" — канал не привязан или ссылки нет (закрытый)."""
    if not t.chat_id:
        return ""
    got = CHAN_LINKS.get(t.chat_id)
    if got is None or time.time() - got[1] > 3600:
        info = chat_info(t.chat_id)
        got = (max_url(info.get("link")) or "", time.time())
        if info:                                          # MAX не ответил — не запоминаем, спросим в следующий раз
            CHAN_LINKS[t.chat_id] = got
    return got[0] or max_url(t.link) or ""


def cabinet_channels(key):
    """Карточка кабинета в панели: каналы MAX (ссылки — ещё и кнопками) и источники Telegram по темам."""
    themes = tenant_themes(key)
    bound = [t for t in themes if t.chat_id]
    lines, rows = ["📺 Каналы MAX:" + ("" if bound else " —")], []
    for t in bound:
        url = theme_channel_url(t)
        lines.append(f"• {t.name} — {url or 'ссылки нет (закрытый канал)'}")
        if url and len(rows) < 10:
            rows.append([LB(f"📺 {t.name}"[:60], url)])
    lines.append("📡 Источники Telegram:" + ("" if themes else " —"))
    for t in themes:
        src = [ch_label(c) for c in t.own.get("channels") or []]
        more = f" и ещё {len(src) - 15}" if len(src) > 15 else ""   # сообщение MAX — до 4000 символов
        lines.append(f"• {t.name}: " + (", ".join(src[:15]) + more if src else "—"))
    text = "\n".join(lines)
    return (text if len(text) <= 2500 else text[:2500].rsplit("\n", 1)[0] + "\n…"), rows


def fsub_add(text):
    """Хозяин прислал ссылку или ID канала для обязательной подписки. (ответ, экран)"""
    t = text.strip()
    cid = int(t) if re.fullmatch(r"-?\d{3,}", t) else resolve_chat_id(t)
    if not cid:
        seen = max_chats()
        return (f"Не нашёл канал «{link_slug(t) or t}» среди чатов, где есть бот.\n\nПроверь:\n"
                "1. Бот добавлен в этот канал администратором (MAX показывает боту только такие каналы).\n"
                "2. Ссылка — публичная ссылка канала (https://max.ru/имя). Если в списке ниже канал есть, но ссылка "
                "другая или её нет — пришли его ID из списка.\n\n"
                + ("Так MAX показывает чаты бота:\n" + "\n".join(
                    f"• {c.get('title') or '—'} — {c.get('link') or 'без ссылки'} (ID {c['chat_id']}, "
                    f"{c.get('type') or '?'}, {c.get('status') or '?'})" for c in seen[:30])
                   if seen else "MAX не вернул ни одного чата бота.")
                + "\n\nПолный ответ MAX — в журнале бота (строка «MAX GET /chats»)."), "clfs"
    if any(c["chat_id"] == cid for c in force_subs()):
        return "Этот канал уже в списке.", "clfs"
    if is_member(cid, host_id() or 0) is None:              # проверяем ровно то, что нужно: видит ли бот подписчиков
        return ("⛔ Бот не может проверять подписчиков этого канала — MAX разрешает это только администраторам. "
                "Сделай бота администратором канала и пришли ссылку ещё раз."), "clfs"
    info = chat_info(cid)
    link = info.get("link") or ("" if re.fullmatch(r"-?\d+", t) else t)   # ссылка от MAX — точная
    if not link:
        return "У этого канала нет ссылки — пришли ссылку на канал (https://max.ru/…), а не ID.", "clfs"
    force_subs().append({"chat_id": cid, "title": info.get("title") or str(cid),
                         "link": link if link.startswith("http") else "https://" + link})
    FSUB_OK.clear()                                       # новый обязательный канал — проверим всех заново
    save_cfg()
    return f"✅ Добавил: «{force_subs()[-1]['title']}». Теперь ботом пользуются только его подписчики.", "clfs"


def host_lock_screen(uid):
    """Экран для кабинета, который хозяин выключил (истёкший срок не закрывает — там бесплатный тариф)."""
    rows = [[LB("💬 Написать мне лично", seller_link())]] if seller_link() else []
    return f"⛔ Доступ приостановлен. Все настройки сохранены.\nПо вопросам: {SELLER_CONTACT}", rows


def host_extend(key, days):
    with CL_LOCK:
        c = TENANTS[key]
        c["until"] = max(c.get("until", 0), time.time()) + days * 86400
        c["enabled"] = True
        c["warned"] = []
        save_tenants()


def host_adopt_orphans():
    """Старые «ничьи» темы (обычный бот до host-режима) становятся личным кабинетом хозяина — бесплатно и бессрочно.
    Также чинит кабинет хозяина, если после ранних экспериментов он остался «клиентским», и переносит темы при смене OWNER_ID."""
    if not HOST_MODE or not host_id():
        return
    key = host_id()
    moved = False
    for k, c in list(TENANTS.items()):                    # хозяин сменился (код/OWNER_ID): забираем темы у прежнего кабинета хозяина
        if c.get("plan") == "Хозяин" and k != key:
            for t in THEMES:
                if t.owner == k:
                    t.owner = ""
                    moved = True
            TENANTS.pop(k, None)
            moved = True
            print(f"HOST: кабинет хозяина переехал на {key} (прежний был {k})")
    # «ничьи» темы (обычный бот до host-режима) забирает хозяин. Темы потерянных кабинетов — НЕ берём:
    # это чужие каналы (иначе хозяину идут чужие посты); покупатель вернётся — получит их обратно
    orphans = [t for t in THEMES if not t.owner]
    has_own = any(t.owner == key for t in THEMES)
    if not orphans and not has_own and key not in TENANTS:
        return                                            # хозяину нечего присваивать и кабинета у него нет
    c = TENANTS.get(key) or blank_tenant(key, "Мой кабинет (хозяин)")
    if not any(same_uid(a, key) for a in (c.get("admins") or [])):
        c["admins"] = [key] + (c.get("admins") or [])
    c["plan"] = "Хозяин"
    c["enabled"] = True
    c["until"] = max(c.get("until", 0), time.time() + 100 * 365 * 86400)   # бессрочно
    c["lim_themes"] = max(c.get("lim_themes", 0), 999)
    c["lim_sources"] = max(c.get("lim_sources", 0), 999)
    TENANTS[key] = c
    for t in orphans:
        t.owner = key
    save_tenants()
    save_cfg()
    if orphans or moved:
        print("HOST: твои темы — личный кабинет хозяина (бесплатно, бессрочно): "
              + (", ".join(t.name for t in THEMES if t.owner == key) or "—"))


def storage_note():
    """Подсказка в лог при запуске: где лежат данные бота и что переживёт пересборку на хостинге."""
    data, code = os.path.abspath(os.getcwd()), os.path.dirname(os.path.abspath(__file__))
    note = f"💾 Данные бота (настройки, кабинеты, темы): {data}"
    with contextlib.suppress(ValueError):
        if os.path.commonpath([data, code]) == code:
            note += ("\n   Папка рядом с кодом: на хостинге без постоянного диска она стирается при пересборке — "
                     "подключи постоянный диск и задай TG_MAX_DIR (путь к нему).")
    miss = [n for n in ("SELLER_CONTACT", "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY") if not (os.environ.get(n) or "").strip()]
    if miss:
        note += "\n   Чтобы контакт и ключи ЮKassa пережили любую пересборку, задай переменные хостинга: " + ", ".join(miss)
    return note


def host_clean_chats():
    """Из каждого кабинета убираем чаты тех, кто не его админ (раньше чат покупателя мог попасть
    в кабинет хозяина — и карточки постов хозяина уходили покупателю)."""
    n = 0
    for key, c in TENANTS.items():
        admins = c.get("admins") or []
        for field in ("chats", "names"):
            d = c.get(field) or {}
            bad = [u for u in d if not any(same_uid(u, a) for a in admins) and u != key]
            for u in bad:
                d.pop(u, None)
                n += field == "chats"
    if n:
        save_tenants()
        print(f"HOST: убрал чужие чаты из кабинетов: {n}")


@cabinet_locked
def ensure_host_cabinet():
    """У хозяина всегда есть его кабинет хотя бы с одной темой — чтобы было что настраивать (даже начав с нуля)."""
    if not HOST_MODE or not host_id():
        return None
    key = host_id()
    c = TENANTS.get(key)
    if c is None:
        c = blank_tenant(key, "Мой кабинет (хозяин)")
        TENANTS[key] = c
    c["plan"] = "Хозяин"
    c["enabled"] = True
    c["until"] = max(c.get("until", 0), time.time() + 100 * 365 * 86400)
    c["lim_themes"] = max(c.get("lim_themes", 0), 999)
    c["lim_sources"] = max(c.get("lim_sources", 0), 999)
    save_tenants()
    if not tenant_themes(key):
        new_cabinet_theme(key, "Мой канал")
    return tenant_themes(key)[0]


def wipe_scope(uid):
    """«🗑 Очистить старые каналы»: темы кабинета того, кто чистит (у хозяина — ещё и «ничьи» темы старых
    версий бота). Кабинеты покупателей и темы потерянных кабинетов сюда не попадают."""
    key = host_id() if is_host(uid) else tenant_key(uid)
    return [t for t in THEMES if (key and t.owner == key) or (is_host(uid) and not t.owner)]


def wipe_themes(themes):
    """Удаляет темы целиком: очередь, отсеянное, файлы и карточки на одобрение.
    Возвращает (тем, источников, постов в очереди)."""
    n_src = n_posts = 0
    gone = {t.id for t in themes}
    for t in list(themes):
        with theme_ctx(t):
            n_src += len(CFG["channels"])
            n_posts += len(QUEUE)
            resolve_cards(item_refs(list(QUEUE)) + [x["id"] for x in ADHELD])   # карточки — из всех чатов
            for it in list(ADHELD):
                drop_files(it)
        delete_theme(t, force=True)
    if BIND.get("theme") in gone:
        BIND["theme"] = None
    save_cfg()
    return len(themes), n_src, n_posts


@cabinet_locked
def ensure_own_theme(uid):
    """У того, кто пишет боту, в кабинете всегда есть тема: иначе привязка канала MAX и источники уходят
    «в пустоту» (после «🗑 Очистить старые каналы», после удаления последней темы)."""
    if not HOST_MODE or uid is None:
        return
    if is_host(uid):
        if host_id() and not tenant_themes(host_id()):
            ensure_host_cabinet()
        return
    key = tenant_key(uid)
    if key is not None and not tenant_themes(key):
        new_cabinet_theme(key, "Мой канал")


def host_set_plan(key, i):
    t = tariffs()[i]
    with CL_LOCK:
        TENANTS[key].update(plan=t["name"], lim_themes=t["themes"], lim_sources=t["sources"])
        save_tenants()


# ---------- ПРОМОКОДЫ (host-режим): скидка % на оплату или бесплатные дни ----------
PROMO_DEF = {"pct": 50, "days": 7}       # значение по умолчанию для новой скидки / бесплатных дней


def promos():
    return GCFG.setdefault("promos", {})


def promo_norm(code):
    return re.sub(r"[^0-9A-ZА-ЯЁ_-]", "", (code or "").upper())[:24]


def promo_problem(p, key=None, now=None):
    """Почему промокод сейчас не сработает (текст) или None — годен."""
    now = now or time.time()
    if p.get("until") and p["until"] < now:
        return "срок действия промокода закончился"
    if p.get("max") and len(p.get("used") or []) >= p["max"]:
        return "активации этого промокода закончились"
    if key is not None and key in (p.get("used") or []):
        return "ты уже активировал этот промокод"
    return None


def promo_what(p):
    return f"скидка {p['value']}%" if p["kind"] == "pct" else f"{p['value']} дн. бесплатно"


def promo_line(code, p):
    used = len(p.get("used") or [])
    st = promo_problem(p)
    return (f"{'✅' if not st else '⛔'} {code} — {promo_what(p)} · активаций {used}/{p.get('max') or '∞'} · "
            + (f"до {fmt_date(p['until'])}" if p.get("until") else "бессрочно"))


def tenant_pct(key):
    """Скидка кабинета, полученная по промокоду (на ближайшую оплату). 0 — нет."""
    pr = (TENANTS.get(key) or {}).get("promo") or {}
    return max(0, min(100, int(pr.get("pct") or 0)))


def plan_price(t, days, key=None):
    """Цена тарифа за срок — со скидкой по промокоду кабинета (если есть)."""
    base = t["price"] if days == 30 else price90(t["price"])
    pct = tenant_pct(key) if (HOST_MODE and key) else 0
    return max(0, int(round(base * (100 - pct) / 100.0))) if pct else base


def promo_apply(key, code, uid=None):
    """Покупатель ввёл промокод. Возвращает текст ответа."""
    code = promo_norm(code)
    p = promos().get(code)
    if not key or key not in TENANTS:
        return "Промокод можно ввести только в своём кабинете."
    if p is None:
        return "❌ Такого промокода нет. Проверь, как он написан."
    bad = promo_problem(p, key)
    if bad:
        return f"❌ Не получилось: {bad}."
    with CL_LOCK:
        p.setdefault("used", []).append(key)
        c = TENANTS[key]
        if p["kind"] == "pct":
            c["promo"] = {"code": code, "pct": int(p["value"])}
        save_cfg()
        save_tenants()
    name = TENANTS[key].get("name")
    if p["kind"] == "days":
        if not TENANTS[key].get("plan"):                  # бесплатный доступ без тарифа — даём первый (самый простой)
            host_set_plan(key, next((i for i, t in enumerate(tariffs()) if t["name"] == p.get("plan")), 0))
        host_extend(key, int(p["value"]))
        host_alert(f"🎟 Кабинет «{name}» активировал промокод {code}: {promo_what(p)}.")
        return (f"🎁 Промокод {code} активирован: {p['value']} дн. бесплатно!\n"
                f"Доступ открыт до {fmt_date(TENANTS[key]['until'])}. Настрой канал: меню → «📢 Куда выкладывать».")
    host_alert(f"🎟 Кабинет «{name}» активировал промокод {code}: {promo_what(p)}.")
    return (f"✅ Промокод {code} принят: скидка {p['value']}% на ближайшую оплату.\n"
            "Выбери тариф — цены уже со скидкой.")


def log_purchase(key, plan, days, price, how, code=""):
    """Покупка подписки — в журнал (для статистики в панели и карточки покупателя). code — только хвост кода.
    Клиент пришёл от партнёра — хозяину «💰 …» (партнёру хозяин платит сам)."""
    log = GCFG.setdefault("pay_log", [])
    log.append({"t": time.time(), "key": key, "name": (TENANTS.get(key) or {}).get("name") or "", "plan": plan,
                "days": int(days), "price": int(price or 0), "how": how, "code": code})
    del log[:-3000]
    save_cfg()
    try:
        ref_on_purchase(key, plan, days, price, how)
    except Exception:
        log_exc("партнёрская покупка")                  # оплату это не останавливает


# ---------- 🤝 ПАРТНЁРСКАЯ ССЫЛКА: хозяин делает ссылку для партнёра, бот помнит, кто от кого, и сообщает о покупках ----------
from reloader.services import referral as refs  # noqa: E402

REF_FILE = "referrals.json"             # кто из клиентов за каким партнёром и что купил (для выплат вручную)
REFS = refs.Referrals(REF_FILE, save=save_json, load=load_json_safe)
REF_FREE = ("промокод 100%",)           # так подписка достаётся бесплатно — это не покупка


def partner_id():
    """ID партнёра: из настроек (/partner 123456), иначе переменная хостинга PARTNER_ID. Нет — None."""
    for v in (GCFG.get("partner_id"), os.environ.get("PARTNER_ID")):
        v = str(v or "").strip()
        if refs.valid_pid(v):
            return v
    return None


def ref_link(pid=None):
    """https://max.ru/<бот>?start=ref_<ID партнёра> или None (нет ID партнёра или MAX не сказал ник бота)."""
    pid = pid or partner_id()
    username = (bot_me() or {}).get("username")
    return refs.link(username, pid) if pid and username else None


def ref_from_payload(uid, payload, name=""):
    """Пришёл по ссылке ?start=ref_<ID>: новый клиент — за партнёром (первый партнёр остаётся). Хозяина и тех, кто
    уже покупал до ссылки, не закрепляем."""
    pid = refs.partner_from_payload(payload)
    if not pid or uid is None or can_panel(uid):
        return
    key = tenant_key(uid)
    if key and any(e.get("key") == key for e in GCFG.get("pay_log") or ()):
        return
    ok, _ = REFS.attach(uid, pid, "ссылка", name)
    if ok:
        _log_line(time.strftime("%H:%M:%S") + f" 🤝 Пришёл по ссылке партнёра {pid}: {name or uid} ({uid})")


def ref_on_purchase(key, plan, days, price, how):
    """Клиент партнёра купил подписку — хозяину «💰 Клиент [Имя] купил подписку по ссылке партнера [ID]!»
    (и тариф, сумма, ID клиента — чтобы заплатить партнёру вручную). Бесплатно (промокод 100%) — не покупка."""
    if how in REF_FREE:
        return
    rec = TENANTS.get(key) or {}
    pid = REFS.partner_of([key] + list(rec.get("admins") or []))
    if not pid:
        return
    name = rec.get("name") or str(key)
    REFS.sale(pid, key, name, plan, days, price, how)
    host_alert(f"💰 Клиент {name} купил подписку по ссылке партнера {pid}!\n"
               f"{plan}, {int(days or 0)} дн." + (f", {int(price)} ₽" if price else "") + f" ({how}) · ID клиента {key}")


def partner_screen(arg):
    """/partner [ID | -]: показать, задать или убрать ID партнёра; ссылка и итоги для выплат."""
    arg = (arg or "").strip()
    if arg == "-":
        GCFG.pop("partner_id", None)
        save_cfg()
    elif arg:
        if not refs.valid_pid(arg):
            return ("ID партнёра — буквы, цифры, «_» или «-», до 32 знаков, без пробелов. Например: "
                    "/partner 123456")
        GCFG["partner_id"] = arg
        save_cfg()
    pid = partner_id()
    if not pid:
        return ("🤝 Партнёр не задан.\n\nЗадайте его ID: /partner 123456 (или переменная PARTNER_ID на хостинге). "
                "Потом /ref — ссылка для клиента.")
    st = REFS.stats(pid)
    return (f"🤝 Партнёр: {pid}\nСсылка: {ref_link(pid) or 'не узнал ник бота в MAX — попробуйте позже'}\n\n"
            f"Клиентов за партнёром: {st['clients']} · покупок: {st['sales']} на {anlimg.fmt_int(st['sum'])} ₽\n\n"
            "🔗 Ссылка для клиента: /ref · поменять партнёра: /partner 123456 · убрать: /partner -")


def ref_start(uid):
    """🔗 Сгенерировать ссылку для клиента: ждём пересланное сообщение клиента (или его ID в MAX)."""
    pid = partner_id()
    if not pid:
        return ("Сначала задайте ID партнёра: /partner 123456 (или переменная PARTNER_ID на хостинге).", None)
    PENDING[uid] = "reflink"
    return (f"🔗 Сгенерировать ссылку для клиента\n\nПерешлите сюда любое сообщение нового клиента — или пришлите "
            f"его ID в MAX (цифрами). Сделаю ссылку партнёра {pid} и закреплю клиента за ним.",
            [[B("✖️ Отмена", "m:cl" if HOST_MODE else "m:main")]])


def ref_input(uid, chat, msg, text):
    """Хозяин переслал сообщение клиента (или прислал ID): ссылка для партнёра, клиент — за партнёром."""
    cid, name = refs.client_from_message(msg, text)
    if cid is None:
        api_send(chat, "Не вижу клиента: перешлите сообщение, которое написал сам клиент (не пост канала), или "
                       "пришлите его ID в MAX — только цифры.", [[B("✖️ Отмена", "m:cl" if HOST_MODE else "m:main")]])
        return
    PENDING.pop(uid, None)
    pid = partner_id()
    url = ref_link(pid)
    if not pid or not url:
        api_send(chat, "Не получилось сделать ссылку: " + ("не задан ID партнёра (/partner 123456)." if not pid
                                                           else "MAX не сказал ник бота — попробуйте через минуту."))
        return
    ok, who = REFS.attach(cid, pid, "хозяин", name or "")
    tail = ("Клиент закреплён за партнёром: когда купит подписку, я напишу вам." if who == pid else
            f"⚠️ Этот клиент уже закреплён за партнёром {who} — его покупки засчитаются ему.")
    api_send(chat, f"🔗 Ссылка для клиента {name or cid} (ID {cid}):\n{url}\n\nОтправьте её партнёру {pid}.\n{tail}")


def host_apply_paid(key, plan, days, price=0, how=""):
    log_purchase(key, plan, days, price, how)
    i = next((k for k, t in enumerate(tariffs()) if t["name"] == plan), None)
    own = custom_from_name(plan)
    if i is not None:
        host_set_plan(key, i)
    elif own:                                             # 🧩 свой тариф: лимиты из названия
        with CL_LOCK:
            TENANTS[key].update(plan=own["name"], lim_themes=own["themes"], lim_sources=own["sources"])
            save_tenants()
    host_extend(key, days)
    c = TENANTS[key]
    if c.pop("promo", None) is not None:                  # скидка по промокоду — на одну оплату
        save_tenants()
    tenant_send(key, f"✅ Оплата получена, спасибо! Тариф «{c.get('plan') or plan}», подписка до {fmt_date(c['until'])}.\n\n"
                     "Теперь настрой свой канал: меню → «📢 Куда выкладывать».")


# ---------- 👥 карточка покупателя · 🎟 коды активации (Playerok) ----------
CODES = CodeStore("codes.json")           # одноразовые коды тарифов: см. reloader/services/codes
CODE_EXPORT = {}                          # хозяин -> файл с кодами, который отправлю следом


def check_min_for(key):
    """Как часто читаем источники: бесплатному — 15 мин (как в тарифе), платному — как обычно (минута)."""
    if tenant_mode(key) == "free":
        return FREE_PLAN["check_min"]
    return max(1, round(GCFG.get("interval", 60) / 60))


def buyer_profile(key, now=None):
    key = str(key or "")
    rec = dict(TENANTS.get(key) or {})
    themes = []
    if key:
        for t in tenant_themes(key):
            themes.append({"name": t.name, "chat_id": t.chat_id, "link": theme_channel_url(t),
                           "sources": list(t.own.get("channels") or []), "published": t.stats.get("published", 0),
                           "last_pub": t.pub.get("last_pub", 0)})
        if tenant_mode(key) == "free":                    # на бесплатном лимиты — бесплатного тарифа
            rec.update(lim_themes=FREE_PLAN["themes"], lim_sources=FREE_PLAN["sources"])
    return build_profile(key, rec, themes, GCFG.get("pay_log", []), now or time.time(), check_min_for(key), fmt_date)


def buyer_screen(key, admin=True):
    p = buyer_profile(key)
    has_contact = bool((TENANTS.get(str(key)) or {}).get("contact"))
    text = render_profile(p, admin=admin, has_contact=has_contact)
    if not admin:
        return text, [[B("◀️ Назад", "m:main")]]
    _, linked = contact_info(p, has_contact)
    rows = [[LB("✉️ Написать лично", f"https://max.ru/{p['username']}")]] if linked else []
    if not linked and has_contact:
        rows.append([B("📇 Контакт покупателя", f"a:buyercard:{key}")])
    if not linked and not has_contact:
        rows.append([B("📇 Попросить контакт", f"a:buyerask:{key}")])
    return text, rows + [[B("◀️ Назад", "m:buyers")]]


def buyer_status(key):
    mode = tenant_mode(key)
    if mode == "run":
        return f"✅ до {fmt_date(TENANTS[key].get('until'))}"
    return {"off": "⛔ выключен", "free": "🆓 бесплатный"}.get(mode, "—")


def buyers_screen(page=0, query=""):
    hk = host_id()
    keys = [k for k, c in TENANTS.items() if k != hk and c.get("plan") != "Хозяин" and match_buyer(k, c, query)]
    keys.sort(key=lambda k: -(TENANTS[k].get("until") or 0))
    shown, page, pages = paginate(keys, page)
    lines = [f"{i}. {TENANTS[k].get('name') or k} · {buyer_status(k)}" for i, k in enumerate(shown, page * 8 + 1)]
    text = ("👥 Покупатели" + (f" — поиск «{query}»" if query else "") + f": {len(keys)}\n\n"
            + ("\n".join(lines) if lines else "Никого не нашёл." if query else "Пока никого нет."))
    rows = [[B(f"{TENANTS[k].get('name') or k}"[:40], f"m:buyer:{k}")] for k in shown]
    nav = ([B("⬅️ Раньше", f"m:buyers:{page - 1}")] if page > 0 else []) \
        + ([B("Дальше ➡️", f"m:buyers:{page + 1}")] if page + 1 < pages else [])
    if nav:
        rows.append(nav)
    return text, rows + [[B("🔎 Найти по id или @username", "p:buysearch")], [B("◀️ Назад", "m:cl")]]


def codes_screen():
    active = CODES.listing()
    username = (bot_me() or {}).get("username")
    text = ("🎟 Коды активации\n\n"
            f"Активных кодов: {len(active)}. Покупатель отправляет код сообщением в бот — тариф включается сразу, "
            "код удаляется.\n\n"
            f"🔗 Ссылка на бота (для описания товара): " + (f"https://max.ru/{username}" if username else "—"))
    if active:
        text += "\n\nКоды (новые сверху):\n" + "\n".join(
            f"{c} · {e['tariff']} · {e['days']} дн. · создан {fmt_date(e['created'])}" for c, e, _ in active[:20])
        if len(active) > 20:
            text += f"\n… и ещё {len(active) - 20} — полный список в экспорте"
    rows = [[B(f"🎟 Сделать коды: {t['name']}"[:60], f"p:codegen:{i}")] for i, t in enumerate(tariffs())]
    rows += [[B("🗑 Удалить код", "p:coderevoke")],
             [B("📄 Экспорт активных (.txt)", "a:codesexp")],
             [B("◀️ Назад", "m:cl")]]
    return text, rows


def buyer_admin_screen(name, uid):
    """Экраны «👥 Покупатели», карточка, «👤 Мой профиль», «🎟 Коды». None — не наши экраны."""
    if not HOST_MODE:
        return None
    if name == "mprof" and not is_host(uid):
        return buyer_screen(tenant_key(uid) or "", admin=False)
    if not can_panel(uid):                              # 👥 🎟 — только в панели /admin
        return None
    if name.startswith("buyer:"):
        return buyer_screen(name.partition(":")[2], admin=True)
    if name == "buyers" or name.startswith("buyers:"):
        page = name.partition(":")[2]
        return buyers_screen(int(page) if page.isdigit() else 0)
    if name.startswith("buyersq:"):
        return buyers_screen(0, name.partition(":")[2])
    if name == "codes":
        return codes_screen()
    return None


def host_activate_plan(key, t, days, how, code=""):
    """Тариф по коду: как оплата — лимиты тарифа и срок (продлевает с текущего срока, если он ещё идёт)."""
    log_purchase(key, t["name"], days, 0, how, code)
    host_set_plan(key, next(i for i, x in enumerate(tariffs()) if x["name"] == t["name"]))
    host_extend(key, days)


def write_codes_file(codes):
    """Файл с кодами для Playerok: один код в строке. Приватный (права 600), в .gitignore."""
    os.makedirs("exports", mode=0o700, exist_ok=True)
    path = os.path.join("exports", f"codes_{secrets.token_hex(4)}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(CodeStore.export_text(codes))
    os.chmod(path, 0o600)
    return path


def codes_generate(count, tariff_name, days, now=None):
    """Партия случайных кодов + файл."""
    codes = CODES.generate(count, tariff_name, days, now=now)
    journal(f"🎟 Сделано кодов: {len(codes)} на «{tariff_name}» ({days} дн.)")
    return codes, write_codes_file(codes)


def activate_by_message(uid, chat_id, raw):
    """Код пришёл обычным сообщением: активируем и отвечаем в тот же чат."""
    text, _ = activate_code(uid, raw)
    rows = [[B("💳 Тарифы", "m:pay")], [B("◀️ В меню", "m:main")]]
    api_send(chat_id, text, rows)


def activate_code(uid, raw):
    """Покупатель ввёл код: погашаем (один раз), применяем тариф. Ошибки — одинаково «Код недействителен»."""
    key = tenant_key(uid)
    if not key or key not in TENANTS:
        return "Код вводится в твоём кабинете — сначала напиши боту /start.", "main"
    status, entry, why = CODES.redeem(raw, buyer=key)
    if status != "ok":
        return f"❌ {why}", "pay"
    t = tariff_by_name(entry["tariff"])
    if t is None:
        CODES.restore(entry)
        return "❌ Тариф этого кода больше не продаётся. Напиши продавцу.", "pay"
    try:
        host_activate_plan(key, t, entry["days"], "код активации", code="…" + entry["code"][-4:])
    except Exception:
        CODES.restore(entry)
        raise
    c = TENANTS[key]
    themes = tenant_themes(key)
    warn = over_limit(len(themes), [(th.name, len(th.own.get("channels") or [])) for th in themes],
                      {"max_channels": t["themes"], "max_sources": t["sources"]})
    text = f"✅ Код принят: тариф «{t['name']}» на {entry['days']} дн. Доступ до {fmt_date(c['until'])}."
    if warn:
        text += ("\n\n⚠️ Сейчас у тебя больше, чем в тарифе («" + "; ".join(warn) + "»). Ничего не удалено: "
                 "всё работает, но добавить новые каналы и источники можно, когда станет в пределах тарифа.")
    return text, "pay"


def host_free_paid(key, t, days):
    """Скидка 100%: платить нечего — сразу открываем доступ."""
    host_apply_paid(key, t["name"], days, 0, "промокод 100%")
    host_alert(f"🎟 Кабинет «{TENANTS[key].get('name')}» получил «{t['name']}» на {days} дн. бесплатно (промокод 100%).")
    return (f"🎁 По промокоду «{t['name']}» на {days} дней — бесплатно. Доступ открыт ✅"), [[B("◀️ В меню", "m:main")]]


def host_pay_online(rest, uid, email=None):
    i, days = rest.split(":")[:2]
    days = int(days)
    gate = legal_gate(uid, i, days)                       # ⚖️ без согласия — ни ссылки, ни платежа
    if gate:
        PENDING.pop(uid, None)
        return gate
    key = tenant_key(uid) or cur().owner
    t = pick_tariff(i)
    price = plan_price(t, days, key)
    if price <= 0:
        return host_free_paid(key, t, days)
    if not yk_keys():
        return host_pay_manual(rest, uid)
    try:
        pid, url = yk_create(price, f"Доступ к боту TG→MAX: «{t['name']}», {days} дней",
                             {"key": key, "plan": t["name"], "days": days}, "https://max.ru", email=email)
    except Exception as e:
        log_exc("ЮKassa (host)")
        ok, why = yk_check(fresh=True)
        host_alert(f"⚠️ ЮKassa не дала ссылку на оплату (кабинет «{(TENANTS.get(key) or {}).get('name') or key}», "
                   f"{price} ₽).\nОтвет ЮKassa: {str(e)[:300]}"
                   + ("" if ok else f"\nКлючи: {why}") + "\n\nПокупателю сказал попробовать чуть позже.")
        how = (GCFG.get("pay_info") or "").strip() or f"реквизиты пришлёт продавец — напиши {SELLER_CONTACT}"
        return (f"⚠️ Онлайн-оплата сейчас не открылась (продавец уже знает).\n\nМожно оплатить переводом: "
                f"«{t['name']}» на {days} дней — {price} ₽.\n{how}\n\nПосле перевода нажми «✅ Я оплатил» — продавец "
                "проверит и откроет доступ."), \
            [[B("✅ Я оплатил", f"pd:{i}:{days}")], [B("🔄 Попробовать онлайн ещё раз", f"py:{i}:{days}")],
             [B("◀️ Назад", "m:pay")]]
    with CL_LOCK:
        TENANTS[key]["pay"] = {"pid": pid, "plan": t["name"], "days": days, "price": price, "t": time.time()}
        save_tenants()
    text = (f"💳 «{t['name']}» на {days} дней — {price} ₽\n\nНажми кнопку и оплати картой или по СБП. "
            "Как только оплата пройдёт, доступ откроется сам (бот проверяет каждые ~20 секунд).")
    rows = [[LB(f"💳 Оплатить {price} ₽", url)], [B("🔄 Проверить оплату", "pc:check")]]
    return text, rows + [[B("◀️ Назад", "m:pay")]]          # ссылка — одним сообщением: этим экраном


def host_pay_manual(rest, uid):
    i, days = rest.split(":")[:2]
    days = int(days)
    gate = legal_gate(uid, i, days)                       # ⚖️ без согласия — ни заявки
    if gate:
        return gate
    key = tenant_key(uid)
    t = pick_tariff(i)
    price = plan_price(t, days, key)
    if price <= 0:
        return host_free_paid(key, t, days)
    with CL_LOCK:
        TENANTS[key]["payreq"] = {"plan": t["name"], "days": days, "price": price, "t": time.time()}
        save_tenants()
    host_alert(f"💳 Заявка на оплату\n\nКабинет: {TENANTS[key].get('name')} (ID {uid})\n"
               f"Тариф: «{t['name']}» на {days} дней — {price} ₽\n\nПроверь перевод и нажми кнопку:",
               [[B("✅ Оплата пришла — открыть", f"a:clpayok:{key}")],
                [B("❌ Не пришла", f"a:clpayno:{key}")]])
    how = (GCFG.get("pay_info") or "").strip()
    reqs = ("Реквизиты:\n" + how) if how else "Жди — хозяин подтвердит оплату."
    return (f"✅ Заявка отправлена. {reqs}\n\n"
            f"Как подтвердят — доступ откроется, придёт сообщение. Вопросы: {SELLER_CONTACT}"), [[B("◀️ Назад", "m:pay")]]


def host_terms_all():
    now = time.time()
    for key, c in list(TENANTS.items()):
        with contextlib.suppress(Exception):
            if not c.get("enabled", True) or not c.get("until"):
                continue
            warned = c.setdefault("warned", [])
            left = c["until"] - now
            price = f"Продлить: кнопка «💳 Оплатить доступ» в боте. Вопросы: {SELLER_CONTACT}."
            if left <= 0 and "end" not in warned:
                warned.append("end"); save_tenants()
                tenant_send(key, "⌛ Подписка закончилась — ты на бесплатном тарифе: " + free_plan_text() + ". "
                                 "Остальные каналы на паузе, все настройки сохранены.\n" + price)
                host_alert(f"⌛ У кабинета «{c.get('name')}» закончилась подписка — перешёл на бесплатный тариф.")
            elif 0 < left <= 86400 and "1" not in warned:
                warned.append("1"); save_tenants()
                tenant_send(key, f"⏰ Подписка заканчивается завтра ({fmt_date(c['until'])}).\n" + price)
            elif 86400 < left <= 3 * 86400 and "3" not in warned:
                warned.append("3"); save_tenants()
                tenant_send(key, f"⏰ Подписка заканчивается через 3 дня ({fmt_date(c['until'])}).\n" + price)


PAY_WAIT = 3 * 3600      # ссылка ЮKassa ждёт оплату столько; дольше — ссылка устарела (или проверяем вручную)


def host_pay_settle(key):
    """Узнаём у ЮKassa, что с оплатой кабинета. 'paid', 'wait', 'gone' (отменена/устарела), 'manual' (часами не
    проверить — передали хозяину) или 'error' (сейчас не проверить). Точная ошибка ЮKassa — в bot.log."""
    c = TENANTS.get(key) or {}
    p = c.get("pay")
    if not p:
        return None
    now = time.time()
    try:
        st = yk_status(p["pid"])
        p.pop("err", None)
    except Exception as e:
        if p.get("err") != str(e):                       # одну и ту же ошибку не пишем каждые 20 секунд
            p["err"] = str(e)
            print(f"ЮKassa: не проверить оплату {p['pid']} (кабинет «{c.get('name')}»): {type(e).__name__}: {e}")
        st = None
    if st == "succeeded":
        with CL_LOCK:
            c.pop("pay", None)
            save_tenants()
        host_apply_paid(key, p["plan"], p["days"], p.get("price", 0), "ЮKassa")
        host_alert(f"💰 Оплата прошла (ЮKassa): кабинет «{c.get('name')}» — «{p['plan']}», "
                   f"{p['days']} дн., {p['price']} ₽. Доступ открыт автоматически.")
        return "paid"
    if now - p.get("t", now) <= PAY_WAIT and st != "canceled":
        return "wait" if st else "error"
    with CL_LOCK:
        c.pop("pay", None)
        if st is None:                                   # не «не прошла»: деньги могли прийти — пусть хозяин проверит
            c["payreq"] = {"plan": p["plan"], "days": p["days"], "price": p.get("price", 0), "t": now}
        save_tenants()
    if st is not None:
        tenant_send(key, "❌ Оплата не прошла или ссылка устарела. Попробуй ещё раз: «💳 Оплатить доступ».")
        return "gone"
    tenant_send(key, "⏳ Не получилось автоматически проверить оплату в ЮKassa — передал продавцу, он проверит вручную "
                     "и откроет доступ. Если ты уже оплатил — ничего делать не нужно.")
    host_alert(f"⚠️ Не могу проверить оплату в ЮKassa уже {PAY_WAIT // 3600} ч (кабинет «{c.get('name')}», "
               f"«{p['plan']}», {p['days']} дн., {p.get('price', 0)} ₽, платёж {p['pid']}).\nОшибка: {p.get('err', '?')[:300]}"
               "\n\nПроверь платёж в личном кабинете ЮKassa и нажми кнопку:",
               [[B("✅ Оплата пришла — открыть", f"a:clpayok:{key}")], [B("❌ Не пришла", f"a:clpayno:{key}")]])
    return "manual"


def host_pay_poll():
    """Фоном (каждые ~20 с) проверяем онлайн-оплаты кабинетов в ЮKassa и открываем доступ автоматически."""
    if not (HOST_MODE and yk_keys()):
        return
    for key in [k for k, c in TENANTS.items() if c.get("pay")]:
        host_pay_settle(key)


def host_pay_check(uid):
    """Кнопка «🔄 Проверить оплату»: спросить ЮKassa сразу, не дожидаясь фоновой проверки."""
    key = tenant_key(uid)
    p = (TENANTS.get(key) or {}).get("pay")
    if not p:
        return "Ожидающей оплаты нет. Тариф и срок — в «💳 Оплатить доступ».", [[B("💳 Тарифы", "m:pay")]]
    res = host_pay_settle(key)
    again = [[B("🔄 Проверить оплату", "pc:check")], [B("◀️ В меню", "m:main")]]
    if res == "paid":
        return "✅ Оплата получена, доступ открыт!", [[B("◀️ В меню", "m:main")]]
    if res == "wait":
        return ("⏳ Оплата ещё не пришла. Если ты уже оплатил — подожди минуту: ЮKassa иногда подтверждает не сразу, "
                "бот проверит сам."), again
    if res == "error":
        return ("⚠️ Сейчас не получилось спросить ЮKassa — бот продолжит проверять сам. Если оплата прошла, доступ "
                "откроется автоматически."), again
    return "Эта оплата уже не ждёт — сообщение об этом пришло выше.", [[B("💳 Тарифы", "m:pay")]]


def tariffs():
    if CLIENT_MODE:
        return load_json("subscription.json", {}).get("tariffs") or DEFAULT_TARIFFS
    return GCFG.get("tariffs") or DEFAULT_TARIFFS


def lim_themes():
    return int(sub_info().get("themes") or LIMIT_THEMES)


def lim_sources():
    return int(sub_info().get("sources") or LIMIT_SOURCES)


def tariff_by_name(name):
    return next((t for t in tariffs() if t["name"] == name), None)


def my_prices():
    """(цена 30 дней, цена 90 дней) по тарифу клиента."""
    t = tariff_by_name(sub_info().get("plan") or "")
    return (t["price"], price90(t["price"])) if t else (PRICE_30, PRICE_90)


def free_plan_text():
    n = FREE_PLAN["sources"]
    return (f"работает {FREE_PLAN['themes']} канал MAX и {n} {n_word(n, 'источник', 'источника', 'источников')}, "
            f"только текст (без фото и видео), пост не чаще раза в {FREE_PLAN['gap_min']} минут, без ИИ")


def tariff_line(t):
    return (f"• {t['name']} — {t['themes']} {n_word(t['themes'], 'тема', 'темы', 'тем')}, до {t['sources']} "
            f"{n_word(t['sources'], 'канала', 'каналов', 'каналов')} в теме: {t['price']} ₽ / 30 дн., "
            f"{price90(t['price'])} ₽ / 90 дн.")


def pay_screen():
    """💳 у покупателя: текущий тариф, все тарифы, кнопки выбора."""
    sub = sub_info()
    until = sub.get("until") or 0
    left = int((until - time.time()) // 86400)
    free = sub.get("mode") == "free"
    now = (f"Сейчас: «{sub.get('plan') or 'индивидуальный'}» — {lim_themes()} "
           f"{n_word(lim_themes(), 'тема', 'темы', 'тем')}, до {lim_sources()} каналов в теме.\n"
           + ("Бесплатно, навсегда. Остальные каналы на паузе — заработают сразу после оплаты." if free else
              f"Подписка до {fmt_date(until)} (осталось {left} дн.)" if left >= 0 and until else
              "Подписка не активна — посты не выходят."))
    req = load_json("pay_request.json", {})
    wait = (f"\n\n⏳ Заявка на «{req['plan']}» ({req['days']} дн.) отправлена — ждём подтверждения продавца."
            if req.get("status") in ("new", "sent") else
            f"\n\n⏳ Жду оплату «{req['plan']}» ({req['days']} дн.) по ссылке выше." if req.get("status") == "link"
            else "")
    free_line = ([f"• {FREE_PLAN['name']} — {FREE_PLAN['themes']} канал MAX, {FREE_PLAN['sources']} "
                  f"{n_word(FREE_PLAN['sources'], 'источник', 'источника', 'источников')}, только текст, "
                  f"пост раз в {FREE_PLAN['gap_min']} мин: 0 ₽, навсегда (бот иногда присылает рекламу)"
                  + (" — сейчас у тебя" if free else "")]
                 if HOST_MODE else [])
    text = ("💳 Тариф и оплата\n\n" + now + wait + "\n\nТарифы:\n"
            + "\n".join(free_line + [tariff_line(t) for t in tariffs()])
            + "\n\nВыбери тариф и срок 👇 Можно продлить заранее или перейти на тариф больше — "
              "дни добавятся к текущему сроку.")
    key = cur().owner if HOST_MODE else None
    pct = tenant_pct(key) if key else 0
    if pct:
        text += f"\n\n🎟 Твоя скидка по промокоду: −{pct}% — цены на кнопках уже со скидкой."
    rows = [[B(f"{t['name']} · 30 дн — {plan_price(t, 30, key)} ₽"[:60], f"py:{i}:30"),
             B(f"90 дн — {plan_price(t, 90, key)} ₽", f"py:{i}:90")] for i, t in enumerate(tariffs())]
    if HOST_MODE and feature_on("mytariff"):
        rows.append([B("🧩 Собрать свой тариф", "m:myt")])
    if HOST_MODE:
        rows.append([B("🎟 Ввести промокод", "p:promo")])
        text += "\n\nЧтобы активировать тариф, отправьте код сообщением."
    return text, rows + [[B("◀️ В меню", "m:main")]]


# ---------- ⚖️ ЮРИДИЧЕСКОЕ: реквизиты продавца, документы, согласие до оплаты, удаление данных ----------
# 152-ФЗ (персональные данные), 54-ФЗ (чеки), «О защите прав потребителей» (оферта, возврат, реквизиты продавца),
# «О рекламе» (отписка). Реквизиты и ссылки вводит хозяин (/legal), покупатель видит их в «📄 Условия» и перед оплатой.
from reloader.services import legal as LEGAL  # noqa: E402

LEGAL_CONSENTS_FILE = "legal_consents.json"   # кто, когда и с какими документами согласился перед оплатой
LEGAL_REQUESTS_FILE = "legal_requests.json"   # запросы на удаление данных: кто, когда, выполнено ли
CONSENT_OFF = "⬜ Я согласен с условиями Оферты и Политикой конфиденциальности"
CONSENT_ON = "✅ Я согласен с условиями Оферты и Политикой конфиденциальности"
PAY_GO = "💳 Перейти к оплате"
DEL_BTN = "🗑 Запросить удаление моих данных"
NOADS_BTN = "🔕 Не присылать рекламу"
LEGAL_DELETED = ("✅ Твои данные удалены по твоему запросу: каналы, настройки, очередь постов, имя и контакты. "
                 "Сведения об оплатах и запись о согласии с офертой (без имени) хранятся столько, сколько требует "
                 "закон. Спасибо, что был с нами!")
DOC_ICONS = {"Оферта": "📄", "Политика конфиденциальности": "🔐", "Условия возврата": "↩️", "Политика cookie": "🍪"}


def legal_info():
    """Реквизиты и ссылки продавца: введённое хозяином в панели → переменные хостинга. У бота клиента (продажа
    ботов) — данные продавца из subscription.json (сам клиент их не меняет)."""
    panel = GCFG.get("legal") if isinstance(GCFG.get("legal"), dict) else {}
    if CLIENT_MODE:
        seller = sub_info().get("legal")
        panel = seller if isinstance(seller, dict) else {}
    return LEGAL.merge(panel, os.environ)


def legal_version():
    return LEGAL.version(legal_info())


def legal_can_edit(uid):
    """Реквизиты меняет только хозяин (и ADMIN_IDS); у бота клиента — никто (это данные продавца)."""
    return bool(uid is not None and can_panel(uid) and not CLIENT_MODE)


def legal_buyer(uid):
    """Покупатель (у него согласие перед оплатой и кнопка удаления данных), а не хозяин бота."""
    return CLIENT_MODE or (HOST_MODE and uid is not None and not can_panel(uid))


def legal_consents():
    data = load_json_safe(LEGAL_CONSENTS_FILE, {})
    return data if isinstance(data, dict) else {}


def legal_consented(uid):
    """Согласие с офертой и политикой ТЕКУЩЕЙ версии (поменялись документы или продавец — согласие заново)."""
    rec = legal_consents().get(str(uid))
    return isinstance(rec, dict) and rec.get("ver") == legal_version()


def legal_agree(uid, plan=""):
    """Покупатель отметил «✅ Я согласен…»: дата, версия и ссылки на документы — доказательство согласия.
    Имя не храним (лишние данные не нужны)."""
    info, data = legal_info(), legal_consents()
    data[str(uid)] = {"ver": legal_version(), "t": time.time(), "date": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "key": (tenant_key(uid) or "") if HOST_MODE else "", "plan": str(plan or ""),
                      "docs": {"offer": info["offer_url"], "privacy": info["privacy_url"],
                               "refund": info["refund_url"]}, "text": CONSENT_ON}
    save_json(LEGAL_CONSENTS_FILE, data, backup=True)
    journal(f"⚖️ Согласие с офертой и политикой: ID {uid}, документы v{data[str(uid)]['ver']}")


def legal_revoke(uid):
    data = legal_consents()
    if data.pop(str(uid), None) is not None:
        save_json(LEGAL_CONSENTS_FILE, data, backup=True)
        journal(f"⚖️ Согласие снято: ID {uid}")


def legal_footer(info=None):
    """Документы и реквизиты продавца — блоком для «📄 Условия» и «❓ Поддержка»."""
    info = info or legal_info()
    docs = dict(LEGAL.documents(info))
    lines = ["📑 Документы:",
             "• Оферта — " + (docs.get("Оферта") or "условия использования бота («📄 Условия»)"),
             "• Политика конфиденциальности — " + (docs.get("Политика конфиденциальности")
                                                     or "«🔐 Какие данные хранит бот»"),
             "• Условия возврата — " + (docs.get("Условия возврата") or "п. 6 условий использования")]
    if docs.get("Политика cookie"):
        lines.append("• Политика cookie — " + docs["Политика cookie"])
    req = LEGAL.requisites(info)
    if req:
        lines += ["", "🏢 Продавец:", req]
    return "\n".join(lines)


def legal_doc_rows(info=None):
    """Кнопки-ссылки на документы продавца (те, что он дал)."""
    return [[LB(f"{DOC_ICONS[n]} {n}", url)] for n, url in LEGAL.documents(info or legal_info())]


def legal_admin_screen():
    """⚖️ Юридическая информация — экран хозяина: что заполнено, что нет, кнопки ввода."""
    info = legal_info()
    panel = GCFG.get("legal") if isinstance(GCFG.get("legal"), dict) else {}
    form = info.get("form") or ""
    og = "ОГРНИП" if form == "ИП" else "ОГРН" if form == "ООО" else "ОГРН / ОГРНИП"

    def val(k):
        v = info.get(k)
        if not v:
            return "—"
        return v + ("" if panel.get(k) else f" (из переменной {LEGAL.FIELDS[k][2]})")

    lines = ["⚖️ Юридическая информация",
             "Это видят покупатели: в «📄 Условия», «❓ Поддержка» и перед оплатой — там же они отмечают согласие "
             "с офертой и политикой конфиденциальности (без отметки оплатить нельзя).", "",
             f"Форма: {val('form')}", f"Наименование: {val('name')}", f"ИНН: {val('inn')}",
             f"{og}: " + ("не нужен" if form == "Самозанятый" else val("ogrn")),
             f"Юридический адрес: {val('address')}", f"Почта поддержки: {val('email')}",
             f"Телефон поддержки: {val('phone')}", "",
             f"Оферта: {val('offer_url')}", f"Политика конфиденциальности: {val('privacy_url')}",
             f"Условия возврата: {val('refund_url')}", f"Политика cookie: {val('cookies_url')} (нужна для сайта)"]
    bad = LEGAL.mismatch(info)
    if bad:
        lines += ["", f"⚠️ Не подходит к форме «{form}»:"] + [f"• {b}" for b in bad]
    miss = LEGAL.missing(info)
    if miss:
        lines += ["", "⚠️ Не заполнено: " + ", ".join(miss) + ". Пока ссылок нет, покупатель видит встроенные "
                  "условия бота и «что хранит бот»."]
    ver = legal_version()
    n = sum(1 for r in legal_consents().values() if isinstance(r, dict) and r.get("ver") == ver)
    lines += ["", f"Согласий покупателей с текущими документами: {n}. Поменяешь оферту или реквизиты — "
                  "покупатели отметят согласие заново перед следующей оплатой.",
              "💾 Чтобы реквизиты пережили пересборку на хостинге, их можно задать и переменными: "
              + ", ".join(env for _, _, env in LEGAL.FIELDS.values()) + ". Введённое здесь — главнее."]
    rows = [[B(f"🏢 Форма: {form or 'не выбрана'}", "a:cllgform")],
            [B("✏️ Наименование", "p:cllg:name"), B("✏️ ИНН", "p:cllg:inn")],
            [B(f"✏️ {og}", "p:cllg:ogrn"), B("✏️ Адрес", "p:cllg:address")],
            [B("✏️ Почта", "p:cllg:email"), B("✏️ Телефон", "p:cllg:phone")],
            [B("🔗 Оферта", "p:cllg:offer_url"), B("🔗 Политика", "p:cllg:privacy_url")],
            [B("🔗 Возврат", "p:cllg:refund_url"), B("🔗 Cookie", "p:cllg:cookies_url")],
            [B("👀 Как видит покупатель", "m:legal")],
            [B("◀️ Тарифы и реквизиты", "m:cltar") if HOST_MODE else B("◀️ В меню", "m:main")]]
    return "\n".join(lines), rows


def legal_screen(name, uid):
    """Экраны: cllegal (хозяин), legal (документы и реквизиты), privacy (что хранит бот), deldata (удаление)."""
    back = [B("◀️ В меню", "m:main")]
    info = legal_info()
    if name == "cllegal":
        return legal_admin_screen() if legal_can_edit(uid) else None
    if name == "legal":
        rows = legal_doc_rows(info) + [[B("🔐 Какие данные хранит бот", "m:privacy")]]
        if legal_buyer(uid):
            rows.append([B(DEL_BTN, "m:deldata")])
        if legal_can_edit(uid):
            rows.append([B("⚖️ Изменить реквизиты", "m:cllegal")])
        return "📄 Документы и реквизиты\n\n" + legal_footer(info), rows + [back]
    if name == "privacy":
        contact = SELLER_CONTACT if contact_ok(SELLER_CONTACT) and SELLER_CONTACT != "@ВАШ_КОНТАКТ" else ""
        rows = [r for r in legal_doc_rows(info) if "Политика конфиденциальности" in r[0]["text"]]
        if legal_buyer(uid):
            rows.append([B(DEL_BTN, "m:deldata")])
        return LEGAL.privacy_text(info, contact), rows + [[B("◀️ Документы", "m:legal")]]
    if name == "deldata":
        if not legal_buyer(uid):
            where = ("«👥 Все покупатели» → покупатель → «🗑 Удалить кабинет»" if HOST_MODE else
                     "«💼 Клиенты» → клиент → «🗑 Удалить клиента»")
            return (f"🗑 Удаление данных — для покупателей: они отправляют запрос тебе, ты удаляешь их данные "
                    f"({where}).", [[B("◀️ Документы", "m:legal")]])
        return ("🗑 Удаление моих данных\n\nОтправим продавцу запрос удалить твои данные: кабинет, каналы, настройки, "
                "очередь постов, имя, ник и контакты. После удаления бот перестанет работать для тебя — вернуть "
                "будет нельзя.\n\nСведения об оплатах продавец хранит столько, сколько требует закон.\n\n"
                "Отправить запрос?"), [[B("✅ Да, отправить запрос", "lg:del")], [B("◀️ Назад", "m:legal")]]
    return None


def legal_push():
    """Реквизиты поменялись — боты клиентов (продажа ботов) показывают новые сразу: они читают subscription.json."""
    for cid in list(CLIENTS):
        with contextlib.suppress(Exception):
            write_subscription(cid)


def legal_set(uid, field, text):
    """Хозяин прислал значение поля реквизитов: проверяем (ИНН/ОГРН — по контрольным цифрам) и сохраняем."""
    if not legal_can_edit(uid) or field not in LEGAL.FIELDS:
        return "Нет доступа.", "main"
    label = LEGAL.FIELDS[field][0]
    val, err = LEGAL.validate(field, text, legal_info().get("form") or "")
    if err:
        return f"❌ {label}: {err}", "cllegal"
    if not isinstance(GCFG.get("legal"), dict):
        GCFG["legal"] = {}
    if val:
        GCFG["legal"][field] = val
    else:
        GCFG["legal"].pop(field, None)
    save_cfg()
    legal_push()
    if val:
        return f"✅ {label}: {val}", "cllegal"
    env = LEGAL.FIELDS[field][2]
    return (f"🧹 {label}: очищено" + (f" — теперь из переменной {env}" if (os.environ.get(env) or "").strip()
                                     else "")), "cllegal"


def legal_form_cycle(uid):
    """🏢 Форма: ИП → ООО → Самозанятый → ИП…"""
    if not legal_can_edit(uid):
        return "main"
    cur_form = legal_info().get("form") or ""
    forms = list(LEGAL.FORMS)
    nxt = forms[(forms.index(cur_form) + 1) % len(forms)] if cur_form in forms else forms[0]
    if not isinstance(GCFG.get("legal"), dict):
        GCFG["legal"] = {}
    GCFG["legal"]["form"] = nxt
    save_cfg()
    legal_push()
    return "cllegal"


def legal_gate(uid, plan, days):
    """Перед любой оплатой: нет согласия с текущими офертой и политикой — экран согласия (иначе None)."""
    if not (HOST_MODE or CLIENT_MODE) or uid is None or legal_consented(uid):
        return None
    return consent_screen(uid, plan, days)


def consent_screen(uid, plan, days, warn=""):
    """Экран согласия: что покупает и за сколько (цена окончательная), ссылки на документы, отметка-«галочка»
    (по умолчанию НЕ отмечена) и «💳 Перейти к оплате»."""
    info = legal_info()
    try:
        t = pick_tariff(plan)
        price = plan_price(t, int(days), cur().owner if HOST_MODE else None)
        what = f"«{t['name']}» на {int(days)} дней — {price} ₽"
    except (ValueError, IndexError, KeyError, TypeError):
        what = "тариф"
    docs = dict(LEGAL.documents(info))
    req = LEGAL.requisites(info)
    text = ("📄 Перед оплатой\n\n" + (warn + "\n\n" if warn else "")
            + f"Ты выбрал {what}. Цена окончательная: без скрытых платежей и автосписаний, карта не привязывается.\n\n"
            "Прочитай документы:\n"
            f"• Оферта — {docs.get('Оферта') or 'условия в боте («📄 Условия»)'}\n"
            f"• Политика конфиденциальности — {docs.get('Политика конфиденциальности') or '«🔐 Какие данные хранит бот»'}\n"
            f"• Условия возврата — {docs.get('Условия возврата') or 'п. 6 условий в боте'}\n"
            + (f"\nПродавец: {req.replace(chr(10), ' · ')}\n" if req else "")
            + "\nОтметь согласие и нажми «💳 Перейти к оплате». Отмечая, ты подтверждаешь, что тебе исполнилось "
              "18 лет (или у тебя есть согласие родителей) и что ты согласен на обработку персональных данных "
              "по Политике конфиденциальности.")
    code = f"{plan}:{days}"
    rows = [[B(CONSENT_ON if legal_consented(uid) else CONSENT_OFF, f"lg:tick:{code}")], [B(PAY_GO, f"lg:go:{code}")]]
    rows += legal_doc_rows(info)
    rows += [[B("📄 Условия", "m:terms"), B("🔐 Данные", "m:privacy")], [B("◀️ Тарифы", "m:pay")]]
    return text, rows


def legal_callback(rest, uid, chat=None):
    """Кнопки lg:… — отметка согласия, «Перейти к оплате», запрос удаления данных, отписка от рекламы."""
    parts = (rest or "").split(":")
    act = parts[0]
    if act in ("tick", "go"):
        plan, days = (parts + ["", ""])[1:3]
        try:                                              # старая кнопка (тарифы поменялись) или подделка —
            ok = pick_tariff(plan) is not None and int(days) in (30, 90)     # к тарифам и ценам, какие есть сейчас
        except (ValueError, IndexError, KeyError, TypeError):
            ok = False
        if not ok:
            return screen("pay", uid)
        if act == "tick":                                 # «галочка»: отметил — согласие с датой; снял — снято
            if legal_consented(uid):
                legal_revoke(uid)
            else:
                legal_agree(uid, f"{plan}:{days}")
            return consent_screen(uid, plan, days)
        if not legal_consented(uid):
            return consent_screen(uid, plan, days, warn=f"⚠️ Чтобы перейти к оплате, отметь согласие: «{CONSENT_OFF}».")
        return pay_choice(f"{plan}:{days}", uid)
    if act == "del":
        return legal_delete_request(uid, chat), [[B("📄 Документы", "m:legal")], [B("◀️ В меню", "m:main")]]
    if act == "noads":
        return ads_opt_out(uid), [[B("◀️ В меню", "m:main")]]
    return screen("legal", uid)


def legal_requests():
    data = load_json_safe(LEGAL_REQUESTS_FILE, {})
    return data if isinstance(data, dict) else {}


def legal_delete_request(uid, chat=None):
    """«🗑 Запросить удаление моих данных»: запрос сохраняем и сразу пишем продавцу (152-ФЗ: после отзыва
    согласия — удалить в течение 30 дней). Повторное нажатие не шлёт второе письмо."""
    if not legal_buyer(uid):
        return "Это для покупателей. Свои данные владелец бота удаляет сам."
    key = tenant_key(uid) if HOST_MODE else "client"
    if not key:
        return "Не нашёл твой кабинет — напиши продавцу: " + SELLER_CONTACT
    data = legal_requests()
    rec = data.get(key)
    if isinstance(rec, dict) and rec.get("status") == "new":
        return f"⏳ Запрос уже отправлен {rec.get('date', '')}. Продавец удалит данные и напишет тебе сюда."
    data[key] = {"uid": str(uid), "t": time.time(), "date": time.strftime("%d.%m.%Y %H:%M"), "status": "new"}
    save_json(LEGAL_REQUESTS_FILE, data, backup=True)
    journal(f"🗑 Запрос на удаление данных: ID {uid}")
    if HOST_MODE:
        c = TENANTS.get(key) or {}
        chans = [str(t.chat_id) for t in tenant_themes(key) if t.chat_id]
        host_alert(f"🗑 Запрос на удаление персональных данных (152-ФЗ)\n\nПокупатель: {c.get('name') or uid} "
                   f"(ID {uid}), кабинет {key}" + (f"\nКаналы MAX: {', '.join(chans)}" if chans else "")
                   + f"\nДата запроса: {data[key]['date']}\n\nПосле отзыва согласия данные нужно удалить в течение "
                     "30 дней. «🗑 Удалить кабинет и данные» сотрёт кабинет со всеми каналами, настройками и "
                     "контактами, а покупателю придёт подтверждение. Сведения об оплатах можно хранить, сколько "
                     "требует закон.",
                   [[B("🗑 Удалить кабинет и данные", f"m:cldel:{key}")], [B("👤 Открыть кабинет", f"m:clc:{key}")]])
    else:                                                 # бот клиента: продавец увидит запрос у себя (см. check_pay_requests)
        save_json("legal_request.json", dict(data[key], name=CFG["admin_names"].get(str(uid), "")))
    return ("✅ Запрос отправлен продавцу. Он удалит твои данные в течение 30 дней и пришлёт сюда подтверждение.\n"
            "Сведения об оплатах хранятся столько, сколько требует закон.")


def legal_forget(key):
    """Кабинет удаляют: если покупатель просил удалить данные — подтверждение ему и запрос закрыт; из согласий —
    без имени; память трендов кабинета — долой."""
    data = legal_requests()
    rec = data.get(key)
    if isinstance(rec, dict) and rec.get("status") == "new":
        tenant_send(key, LEGAL_DELETED)
        rec.update(status="done", done=time.time(), done_date=time.strftime("%d.%m.%Y %H:%M"))
        save_json(LEGAL_REQUESTS_FILE, data, backup=True)
        journal(f"🗑 Данные кабинета {key} удалены по запросу покупателя")
    ids = {str(a) for a in (TENANTS.get(key) or {}).get("admins") or []} | {str(key)}
    for path, loader in ((CONSENT_FILE, load_json), (LEGAL_CONSENTS_FILE, load_json_safe)):
        cons = loader(path, {})
        if isinstance(cons, dict) and any(isinstance(cons.get(i), dict) and "name" in cons[i] for i in ids):
            for i in ids:
                if isinstance(cons.get(i), dict):
                    cons[i].pop("name", None)
            save_json(path, cons, backup=path == LEGAL_CONSENTS_FILE)
    with contextlib.suppress(Exception):
        for box in (TREND_ALERTS.sent, TREND_ALERTS.seen):
            box.pop(f"cab:{key}", None)
        TREND_ALERTS.save()
    with contextlib.suppress(Exception):               # 🤝 отметка партнёра — тоже (суммы для расчёта — без имени)
        REFS.forget(ids)


def ads_opt_out(uid):
    """🔕 Отписка от рекламы (закон «О рекламе»: по просьбе получателя рассылку прекращают)."""
    key = tenant_key(uid) if HOST_MODE else None
    if not key or key not in TENANTS:
        return "Реклама приходит только на бесплатном тарифе — у тебя её нет."
    with CL_LOCK:
        TENANTS[key]["ads_off"] = True
        save_tenants()
    journal(f"🔕 Отписка от рекламы: кабинет {key}")
    return "🔕 Хорошо — рекламу больше не пришлю. Включить обратно — напиши продавцу."


PAY_EMAIL_ASK = "Напишите email (почта) для отправки чека."


def pay_choice(rest, uid=None):
    """Выбрал тариф и срок: как оплатить и кнопка «Я оплатил»."""
    i, days = rest.split(":")[:2]
    days = int(days)
    gate = legal_gate(uid, i, days)                       # ⚖️ сначала согласие с офертой и политикой
    if gate:
        return gate
    t = pick_tariff(i)
    price = plan_price(t, days, cur().owner if HOST_MODE else None)
    how = (sub_info().get("pay") or "").strip()
    if HOST_MODE and price <= 0:                          # промокод 100% — платить нечего
        return (f"🎁 «{t['name']}» на {days} дней — бесплатно по промокоду.",
                [[B("✅ Получить бесплатно", f"pk:{i}:{days}")], [B("◀️ Тарифы", "m:pay")]])
    if HOST_MODE and yk_keys():                           # ЮKassa: сначала почта для чека, потом ссылка
        PENDING[uid] = f"payemail:{i}:{days}"
        return (f"💳 «{t['name']}» на {days} дней — {price} ₽\n\n{PAY_EMAIL_ASK}\n"
                "Чек об оплате придёт на эту почту (так требует закон, 54-ФЗ)."), \
            [[B("⏭ Пропустить", f"pe:{i}:{days}")], [B("◀️ Тарифы", "m:pay")]]
    if sub_info().get("online"):
        text = (f"💳 «{t['name']}» на {days} дней — {price} ₽\n\nОплата картой или по СБП через ЮKassa — чек придёт "
                "автоматически. Нажми «💳 Оплатить онлайн»: пришлю ссылку, а после оплаты бот продлится сам.")
        rows = [[B("💳 Оплатить онлайн", f"pk:{i}:{days}")]]
        if how:
            text += f"\n\nИли переводом:\n{how}\nи потом «✅ Я оплатил»."
            rows.append([B("✅ Я оплатил переводом", f"pd:{i}:{days}")])
        return text, rows + [[B("◀️ Тарифы", "m:pay")]]
    how = how or f"Напиши продавцу {SELLER_CONTACT} — он пришлёт реквизиты."
    text = (f"💳 «{t['name']}» на {days} дней — {price} ₽\n\nКак оплатить:\n{how}\n\n"
            "После оплаты нажми «✅ Я оплатил» — продавец проверит перевод и продлит бота, тебе придёт сообщение.")
    return text, [[B("✅ Я оплатил", f"pd:{i}:{days}")], [B("◀️ Тарифы", "m:pay")]]


def pay_online(rest, uid):
    """«Оплатить онлайн»: просим бота продавца создать платёж в ЮKassa — ссылка придёт сюда."""
    i, days = (int(x) for x in rest.split(":")[:2])
    gate = legal_gate(uid, i, days)                       # ⚖️ без согласия — не просим ссылку на оплату
    if gate:
        return gate
    t = tariffs()[i]
    price = t["price"] if days == 30 else price90(t["price"])
    save_json("pay_request.json", {"status": "new", "mode": "online", "t": time.time(), "plan": t["name"],
                                   "days": days, "price": price, "uid": uid,
                                   "name": CFG["admin_names"].get(str(uid), "")})
    journal(f"💳 Онлайн-оплата: «{t['name']}» на {days} дн. — {price} ₽, жду ссылку")
    return ("⏳ Готовлю ссылку на оплату — придёт сюда через несколько секунд.", [[B("◀️ В меню", "m:main")]])


def pay_done(rest, uid):
    """«Я оплатил»: заявка продавцу (он увидит её в своём боте с кнопкой «продлить»)."""
    i, days = (int(x) for x in rest.split(":")[:2])
    gate = legal_gate(uid, i, days)                       # ⚖️ без согласия — не отправляем заявку
    if gate:
        return gate
    t = tariffs()[i]
    price = t["price"] if days == 30 else price90(t["price"])
    save_json("pay_request.json", {"status": "new", "t": time.time(), "plan": t["name"], "days": days,
                                   "price": price, "uid": uid, "name": CFG["admin_names"].get(str(uid), "")})
    journal(f"💳 Заявка на оплату: «{t['name']}» на {days} дн. — {price} ₽")
    return (f"✅ Заявка отправлена продавцу: «{t['name']}» на {days} дней — {price} ₽.\n\n"
            f"Как только он подтвердит оплату, бот продлится и сюда придёт сообщение. Вопросы: {SELLER_CONTACT}"), \
        [[B("◀️ В меню", "m:main")]]


def lock_text(mode):
    if mode == "off":
        return f"⛔ Бот приостановлен — посты не выходят. Все настройки сохранены.\n\nНапиши: {SELLER_CONTACT}"
    p30, p90 = my_prices()
    return (f"⛔ Подписка на бота закончилась — посты не выходят. Все настройки и каналы сохранены.\n\n"
            f"Продлить: {p30} ₽ за 30 дней или {p90} ₽ за 90 дней — кнопка «💳 Тариф и оплата».\n"
            f"Вопросы: {SELLER_CONTACT}")


def subscription_line():
    """Строка про подписку в меню кабинета/клиента."""
    sub = sub_info()
    until = sub.get("until")
    if not until:
        return ""
    left = int((until - time.time()) // 86400)
    tail = f" (осталось {left} дн.)" if left >= 0 else ""
    plan = sub_info().get("plan")
    return (f"📅 Тариф «{plan}», подписка" if plan else "📅 Подписка") + \
        f" до {fmt_date(until)}{tail}. Продлить или сменить тариф: «💳 Тариф и оплата». Вопросы: {SELLER_CONTACT}"


def write_subscription(cid):
    c = CLIENTS[cid]
    os.makedirs(client_dir(cid), exist_ok=True)
    save_json(os.path.join(client_dir(cid), "subscription.json"),
              {"until": c.get("until", 0), "mode": client_mode(c), "contact": SELLER_CONTACT,
               "plan": c.get("plan") or "", "themes": c.get("lim_themes", LIMIT_THEMES),
               "sources": c.get("lim_sources", LIMIT_SOURCES), "tariffs": tariffs(), "pay": GCFG.get("pay_info", ""),
               "online": bool(yk_keys()), "legal": legal_info()})


def start_client(cid):
    c = CLIENTS[cid]
    mode = client_mode(c)
    d = client_dir(cid)
    os.makedirs(d, exist_ok=True)
    if (c.get("token") or "").strip() == (MAX_TOKEN or "").strip():
        return                                            # второй бот на токене основного перехватывал бы его кнопки
    write_subscription(cid)
    env = dict(os.environ)
    for k in ("MAX_TOKEN", "MAX_CHANNEL_LINK", "MAX_CHAT_ID", "ADMIN_CODE", "TG_API_ID", "TG_API_HASH", "TG_PHONE",
              "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY", "OWNER_ID", "TGMAX_HOST", "HOST_CODE"):   # бот клиента — не host-бот
        env.pop(k, None)
    env.update(BOT_TOKEN=c["token"], TG_MAX_DIR=d, TG_MAX_NO_SEARCH="1", TGMAX_CLIENT="1",
               TGMAX_LOCKED="" if mode == "run" else mode, SELLER_CONTACT=SELLER_CONTACT,
               TGMAX_LIMIT_THEMES=str(c.get("lim_themes", LIMIT_THEMES)),
               TGMAX_LIMIT_SOURCES=str(c.get("lim_sources", LIMIT_SOURCES)),
               TGMAX_TG="1" if c.get("tg", True) else "0",
               TGMAX_HUB=HUB["url"] if GCFG.get("share_tg", False) and TG_CLIENT is not None
               and not is_web_client(TG_CLIENT) else "", TGMAX_HUB_KEY=HUB["key"],
               TGMAX_API_ID=str(API_ID) if field_ok("api_id", API_ID) and field_ok("api_hash", API_HASH) else "",
               TGMAX_API_HASH=API_HASH if field_ok("api_id", API_ID) and field_ok("api_hash", API_HASH) else "",
               MAX_API_URL=MAX_API, PYTHONIOENCODING="utf-8")
    err = open(os.path.join(d, "stderr.log"), "ab")
    import subprocess
    p = subprocess.Popen([sys.executable, BOT_FILE], cwd=d, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=err)
    PROCS[cid] = {"p": p, "mode": mode, "t": time.time()}
    with contextlib.suppress(OSError), open(os.path.join(d, "bot.pid"), "w") as f:
        f.write(str(p.pid))
    print(f"Клиенты: запустил бота «{c.get('name')}» ({mode})")


def kill_stale_clients():
    """Боты клиентов, оставшиеся от прошлого запуска (хостинг перезапустил только основного бота): гасим их,
    иначе они работают на старой версии и вдвоём с новыми читают один токен."""
    import signal
    base = os.path.abspath(CLIENTS_DIR)
    for name in (os.listdir(base) if os.path.isdir(base) else []):
        try:
            with open(os.path.join(base, name, "bot.pid")) as f:
                pid = int(f.read().strip())
            with open(f"/proc/{pid}/environ", "rb") as f:
                if b"TGMAX_CLIENT=1" not in f.read():
                    continue                              # этот номер уже занят чужим процессом
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            continue
        for _ in range(50):
            if not os.path.exists(f"/proc/{pid}"):
                break
            time.sleep(0.1)
        else:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        print(f"Клиенты: остановил старую копию бота {name} (pid {pid})")


def stop_all_clients(*_):
    """Основной бот останавливается — останавливаем и ботов клиентов (при запуске он поднимет их заново)."""
    for pr in list(PROCS.values()):
        with contextlib.suppress(Exception):
            pr["p"].terminate()
    for pr in list(PROCS.values()):
        with contextlib.suppress(Exception):
            pr["p"].wait(5)
    os._exit(0)


def watch_parent():
    """Бот клиента живёт, пока жив основной бот: тот перезапустился (обновление) — выходим, новый запустит нас
    уже новой версией."""
    ppid = os.getppid()

    def loop():
        while True:
            time.sleep(3)
            if os.getppid() != ppid:
                print("Основной бот остановился — выхожу, он запустит меня заново", flush=True)
                os._exit(0)
    threading.Thread(target=loop, daemon=True).start()


def stop_client(cid):
    pr = PROCS.pop(cid, None)
    if pr and pr["p"].poll() is None:
        pr["p"].terminate()
        try:
            pr["p"].wait(10)
        except Exception:
            pr["p"].kill()


def client_send(cid, text, rows=None):
    """Написать админам бота клиента — от имени его бота."""
    c = CLIENTS.get(cid) or {}
    st = load_json(os.path.join(client_dir(cid), "settings.json"), {})
    body = {"text": text}
    if rows:
        body["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": rows}}]
    n = 0
    for chat in set((st.get("admin_chats") or {}).values()):
        with contextlib.suppress(Exception):
            r = S.post(f"{MAX_API}/messages", params={"chat_id": chat}, headers={"Authorization": c.get("token", "")},
                       json=body, timeout=30)
            n += r.status_code == 200
    return n


# ---------- онлайн-оплата через ЮKassa (самозанятые: чеки в «Мой налог» ЮKassa формирует сама) ----------
YK_API = (os.environ.get("YK_API_URL") or "https://api.yookassa.ru").rstrip("/")


def _yk_clean(v):
    """Ключ, скопированный с телефона: убираем пробелы, кавычки, переносы и невидимые символы."""
    return re.sub(r"[\s\"'«»`\u200b-\u200f\ufeff]", "", str(v or ""))


def yk_env_keys():
    """Ключи ЮKassa из переменных хостинга (или config.json); нет пары — None."""
    sid = _yk_clean(os.environ.get("YOOKASSA_SHOP_ID") or CONFIG.get("yookassa_shop_id"))
    key = _yk_clean(os.environ.get("YOOKASSA_SECRET_KEY") or CONFIG.get("yookassa_secret_key"))
    return (sid, key) if sid and key else None


def yk_keys():
    """shopId и секретный ключ ЮKassa: введены в панели бота — они главнее переменных хостинга;
    в панели нет — из переменных (YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY). В коде ключей нет."""
    if GCFG.get("yk_shop") and GCFG.get("yk_key"):
        return _yk_clean(GCFG["yk_shop"]), _yk_clean(GCFG["yk_key"])
    return yk_env_keys()


YK_MSG_GONE = {}       # хозяин -> удалось ли убрать из чата его сообщение с ключом (ЮKassa, VK, TikTok)


def secret_tail(uid):
    """Приписка к ответу, если сообщение с ключом из чата убрать не вышло."""
    return ("" if YK_MSG_GONE.pop(uid, True) else
            "\n\n⚠️ Твоё сообщение с ключом удалить не смог (MAX не даёт боту удалять чужие сообщения) — "
            "удали его сам: зажми сообщение → «Удалить».")


def yk_seen():
    """Что бот видит (без раскрытия секрета): откуда ключи, shopId, тип и длина ключа."""
    k = yk_keys()
    if not k:
        return "ключей нет"
    src = "из панели бота" if GCFG.get("yk_shop") and GCFG.get("yk_key") else "из переменных хостинга"
    kind = "боевой (live_)" if k[1].startswith("live_") else "тестовый (test_)" if k[1].startswith("test_") else "неизвестного вида"
    return f"бот видит ({src}): shopId {k[0]}, ключ {kind}, {len(k[1])} символов, …{k[1][-4:]}"


YK_CHECK_CACHE = {}      # (shopId, ключ) -> (когда, ответ): экран «Тарифы» не ждёт ЮKassa при каждом открытии
YK_CHECK_TTL = 300


def yk_check(fresh=False):
    """(всё ок?, текст): подходят ли ключи ЮKassa — спрашиваем саму ЮKassa. Ответ помним 5 минут (только
    «нет связи» не запоминаем); fresh — спросить заново (ключи только что ввели, оплата не прошла)."""
    keys = yk_keys()
    got = YK_CHECK_CACHE.get(tuple(keys or ()))
    if keys and not fresh and got and time.time() - got[0] < YK_CHECK_TTL:
        return got[1]
    res = _yk_check(keys)
    if keys and not res[1].startswith("нет связи"):
        YK_CHECK_CACHE.clear()
        YK_CHECK_CACHE[tuple(keys)] = (time.time(), res)
    return res


def _yk_check(keys):
    if not keys:
        return False, ("не подключена. Добавь в Bothost переменные YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY "
                       "и сделай пересборку.")
    sid, key = keys
    hints = []
    if not sid.isdigit():
        hints.append(f"YOOKASSA_SHOP_ID должен быть только цифрами (это shopId магазина), а сейчас там «{sid[:12]}…»")
    if not key.startswith(("live_", "test_")):
        hints.append("YOOKASSA_SECRET_KEY должен начинаться с live_ (или test_ у тестового магазина) — "
                     "похоже, вставлено что-то другое")
    try:
        r = S.get(f"{YK_API}/v3/me", auth=keys, timeout=20)
    except Exception as e:
        return False, f"нет связи с ЮKassa ({type(e).__name__}). Попробуй позже."
    if r.status_code == 200:
        j = r.json()
        kind = "тестовый магазин — настоящие деньги не списываются" if j.get("test") else "боевой магазин"
        return True, f"подключена ✅ (магазин {j.get('account_id') or sid}, {kind})"
    if r.status_code == 401:
        err_id = None
        with contextlib.suppress(Exception):
            err_id = r.json().get("id")
        return False, ("ключи не подходят ❌ — ЮKassa ответила «Error in shopId or secret key».\n"
                       + (f"🆔 Код ошибки для поддержки ЮKassa: {err_id} — по нему поддержка видит, почему ключ "
                          "не принят.\n" if err_id else "")
                       + f"🔎 {yk_seen()}. Сверь shopId и последние 4 символа ключа с личным кабинетом ЮKassa.\n"
                       + ("\n".join("• " + h for h in hints) + "\n" if hints else "")
                       + "Как исправить: в ЮKassa слева вверху выбери магазин, через который продаёшь (у тестового "
                         "магазина СВОЙ shopId и свои ключи). Там: Интеграция → Ключи API → скопируй shopId и нажми "
                         "«Выпустить новый ключ» (старый сразу перестаёт работать). Пришли оба кнопкой "
                         "«🔑 Ввести ключи ЮKassa» в «💰 Тарифы и реквизиты» — без перезапуска.")
    return False, f"ЮKassa ответила {r.status_code}: {r.text[:150]}"


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


def receipt_email():
    """Запасная почта для чека (54-ФЗ), если покупатель свою не дал: YOOKASSA_RECEIPT_EMAIL."""
    return _yk_clean(os.environ.get("YOOKASSA_RECEIPT_EMAIL") or CONFIG.get("yookassa_receipt_email")) or None


def yk_create(amount, description, metadata, return_url, email=None):
    """Платёж в ЮKassa. Чек (54-ФЗ) — на почту покупателя, а если её нет — на запасную почту продавца.
    Возвращает (id платежа, ссылка на оплату)."""
    import uuid
    money = {"value": f"{amount:.2f}", "currency": "RUB"}
    body = {"amount": money, "capture": True, "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description[:128], "metadata": metadata}
    customer = email or receipt_email()
    if customer:
        body["receipt"] = {"customer": {"email": customer}, "items": [{
            "description": description[:128], "quantity": "1.00", "vat_code": 1, "amount": money,
            "payment_subject": "service", "payment_mode": "full_payment"}]}
    r = S.post(f"{YK_API}/v3/payments", auth=yk_keys(), timeout=30,
               headers={"Idempotence-Key": str(uuid.uuid4())}, json=body)
    if r.status_code == 400 and "receipt" in r.text.lower() and not customer:
        raise RuntimeError("в магазине ЮKassa включены чеки — добавь в Bothost переменную YOOKASSA_RECEIPT_EMAIL "
                           "(твоя почта для чеков) или выключи чеки в ЮKassa")
    if r.status_code >= 300:
        raise RuntimeError(f"ЮKassa {r.status_code}: {r.text[:1500]}")   # ответ целиком — в bot.log
    j = r.json()
    return j["id"], j["confirmation"]["confirmation_url"]


def yk_status(pid):
    r = S.get(f"{YK_API}/v3/payments/{pid}", auth=yk_keys(), timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"ЮKassa {r.status_code}: {r.text[:1500]}")
    return r.json().get("status")


def apply_paid(cid, req):
    """Оплата пришла: тариф из заявки, срок продлеваем, клиенту — «спасибо»."""
    i = next((k for k, t in enumerate(tariffs()) if t["name"] == req.get("plan")), None)
    if i is not None:
        set_plan(cid, i)
    extend_client(cid, int(req.get("days") or 30))
    c = CLIENTS[cid]
    client_send(cid, f"✅ Оплата получена, спасибо! Тариф «{c.get('plan') or req.get('plan')}», "
                     f"подписка до {fmt_date(c['until'])}.")


def seller_notify(text, rows=None):
    if not GCFG["admins"]:
        return
    chat = GCFG["admin_chats"].get(str(GCFG["admins"][0]))
    if chat:
        with contextlib.suppress(Exception):
            api_send(chat, text, rows or [[B("💼 Клиенты", "m:cl")]])


def pay_request_price(req):
    """Сколько стоит заявка бота клиента по тарифам продавца сейчас (None — такого тарифа или срока нет)."""
    t = next((x for x in tariffs() if x.get("name") == req.get("plan")), None)
    if t is None or req.get("days") not in (30, 90):
        return None
    return t["price"] if req["days"] == 30 else price90(t["price"])


def pay_request_ok(req):
    """💳 Без скрытых платежей: цена в заявке (её видел покупатель) = цене тарифа продавца. Не совпало (тариф
    подорожал, пока покупатель оплачивал, или заявку подделали) — платёж не создаём."""
    want = pay_request_price(req)
    try:
        return want is not None and float(req.get("price")) == float(want)
    except (TypeError, ValueError):
        return False


def check_pay_requests(now=None):
    """Заявки на оплату от ботов клиентов. Онлайн (ЮKassa): создаём платёж, шлём клиенту ссылку, сами следим за
    оплатой и продлеваем. «Я оплатил» (перевод): продавцу заявка с кнопкой «продлить»."""
    now = now or time.time()
    for cid, c in list(CLIENTS.items()):
        path = os.path.join(client_dir(cid), "pay_request.json")
        req = load_json(path, {})
        if req.get("status") == "new" and not pay_request_ok(req):
            with contextlib.suppress(OSError):
                os.remove(path)
            with contextlib.suppress(Exception):
                write_subscription(cid)                   # у бота клиента — тарифы и цены продавца сейчас
            client_send(cid, "⚠️ Цена тарифа изменилась, пока ты оформлял оплату, — ничего не списано. Открой "
                             "«💳 Тариф и оплата» и выбери тариф заново: там актуальная цена.")
            journal(f"💳 Заявка «{c.get('name')}» не принята: в заявке {req.get('price')} ₽, а тариф "
                    f"«{req.get('plan')}» на {req.get('days')} дн. стоит {pay_request_price(req)} ₽")
            continue
        if req.get("mode") == "online" and yk_keys():
            if req.get("status") == "new":
                try:
                    pid, url = yk_create(req["price"], f"Бот TG→MAX: тариф «{req['plan']}», {req['days']} дней",
                                         {"cid": cid, "plan": req["plan"], "days": req["days"]},
                                         f"https://max.ru/{c['username']}" if c.get("username") else "https://max.ru")
                except Exception as e:
                    log_exc("ЮKassa: платёж не создан")
                    req["status"] = "error"
                    save_json(path, req)
                    client_send(cid, f"⚠️ Не получилось создать ссылку на оплату. Попробуй чуть позже или напиши: "
                                     f"{SELLER_CONTACT}")
                    seller_notify(f"⚠️ ЮKassa не создала платёж для «{c.get('name')}»: {str(e)[:150]}")
                    continue
                req.update(status="link", pid=pid, url=url, t_link=now, t_poll=now)
                save_json(path, req)
                client_send(cid, f"💳 «{req['plan']}» на {req['days']} дней — {req['price']} ₽\n\nНажми кнопку и оплати "
                                 "картой или по СБП. Как только оплата пройдёт, бот продлится сам — придёт сообщение.",
                            [[LB(f"💳 Оплатить {req['price']} ₽", url)]])
                continue
            if req.get("status") == "link" and now - req.get("t_poll", 0) >= 20:
                req["t_poll"] = now
                try:
                    st = yk_status(req["pid"])
                except Exception as e:
                    print("ЮKassa: статус не получен:", e)
                    save_json(path, req)
                    continue
                if st == "succeeded":
                    with contextlib.suppress(OSError):
                        os.remove(path)
                    apply_paid(cid, req)
                    seller_notify(f"💰 Оплата прошла (ЮKassa): «{c.get('name')}» — тариф «{req['plan']}», "
                                  f"{req['days']} дн., {req['price']} ₽. Подписка продлена автоматически.")
                elif st == "canceled" or now - req.get("t_link", now) > 3 * 3600:
                    with contextlib.suppress(OSError):
                        os.remove(path)
                    client_send(cid, "❌ Оплата не прошла или ссылка устарела. Попробуй ещё раз: «💳 Тариф и оплата».")
                else:
                    save_json(path, req)
            continue
        if req.get("status") != "new":
            continue
        req["status"] = "sent"
        save_json(path, req)
        seller_notify(f"💳 Заявка на оплату\n\nКлиент: {c.get('name')}"
                      + (f" (@{c['username']})" if c.get("username") else "")
                      + (f", пишет {req['name']}" if req.get("name") else "")
                      + f"\nТариф: «{req.get('plan')}» на {req.get('days')} дней — {req.get('price')} ₽\n\n"
                        "Проверь перевод и нажми кнопку:",
                      [[B("✅ Оплата пришла — продлить", f"a:clpayok:{cid}")],
                       [B("❌ Не пришла", f"a:clpayno:{cid}"), B("✉️ Написать клиенту", f"p:clsay:{cid}")]])


def check_legal_requests():
    """🗑 Запросы на удаление данных из ботов клиентов — продавцу, один раз, с кнопкой «удалить клиента»."""
    for cid, c in list(CLIENTS.items()):
        path = os.path.join(client_dir(cid), "legal_request.json")
        req = load_json(path, {})
        if not isinstance(req, dict) or req.get("status") != "new":
            continue
        req["status"] = "sent"
        save_json(path, req)
        journal(f"🗑 Запрос на удаление данных: клиент «{c.get('name')}»")
        seller_notify("🗑 Запрос на удаление персональных данных (152-ФЗ)\n\n"
                      f"Клиент: {c.get('name')}" + (f" (@{c['username']})" if c.get("username") else "")
                      + (f", пишет {req['name']}" if req.get("name") else "") + f" (ID {req.get('uid')})"
                      + f"\nДата запроса: {req.get('date', '')}\n\nПосле отзыва согласия данные нужно удалить в "
                        "течение 30 дней. «🗑 Удалить клиента и данные» остановит его бота и сотрёт настройки, каналы "
                        "и контакты, а клиенту придёт подтверждение. Сведения об оплатах можно хранить, сколько "
                        "требует закон.",
                      [[B("🗑 Удалить клиента и данные", f"m:cldel:{cid}")], [B("👤 Открыть клиента", f"m:clc:{cid}")]])


def check_client_terms(cid, now=None):
    """Напоминания клиенту за 3 дня и за 1 день, сообщение в день окончания (и продавцу)."""
    now = now or time.time()
    c = CLIENTS[cid]
    if not c.get("enabled", True):
        return
    warned = c.setdefault("warned", [])
    left = c.get("until", 0) - now
    t = next((x for x in tariffs() if x["name"] == c.get("plan")), None)
    p30, p90 = (t["price"], price90(t["price"])) if t else (PRICE_30, PRICE_90)
    price = (f"Продлить: {p30} ₽ за 30 дней или {p90} ₽ за 90 дней — в боте кнопка «💳 Тариф и оплата». "
             f"Вопросы: {SELLER_CONTACT}.")
    if left <= 0:
        if "end" not in warned:
            warned.append("end")
            client_send(cid, "⛔ Подписка на бота закончилась — посты больше не выходят. Настройки сохранены.\n" + price)
            seller_notify(f"⌛ У клиента «{c.get('name')}» закончилась подписка — бот на паузе.")
            save_clients()
    elif left <= 86400:
        if "1" not in warned:
            warned.append("1")
            client_send(cid, f"⏰ Подписка на бота заканчивается завтра ({fmt_date(c['until'])}).\n" + price)
            save_clients()
    elif left <= 3 * 86400 and "3" not in warned:
        warned.append("3")
        client_send(cid, f"⏰ Подписка на бота заканчивается через 3 дня ({fmt_date(c['until'])}).\n" + price)
        save_clients()


CRASHES = {}           # клиент -> времена падений его бота
CRASH_TOLD = {}


def note_client_crash(cid, c):
    """Бот клиента падает раз за разом — продавец узнаёт сразу (с хвостом ошибки), а не от клиента."""
    now = time.time()
    CRASHES[cid] = [x for x in CRASHES.get(cid, []) if now - x < 900] + [now]
    if len(CRASHES[cid]) < 3 or now - CRASH_TOLD.get(cid, 0) < 3600:
        return
    CRASH_TOLD[cid] = now
    tail = ""
    with contextlib.suppress(OSError), open(os.path.join(client_dir(cid), "stderr.log"),
                                            encoding="utf-8", errors="ignore") as f:
        tail = "".join(f.readlines()[-6:])[-600:]
    seller_notify(f"🆘 Бот клиента «{c.get('name')}» падает уже {len(CRASHES[cid])} раз за 15 минут — "
                  f"перезапускаю, но посты у него могут не выходить.\n\nПоследняя ошибка:\n{tail or '—'}",
                  [[B("📜 Журнал клиента", f"m:cllog:{cid}")], [B("💼 Клиенты", "m:cl")]])


def supervise_once():
    """Держит ботов клиентов в нужном состоянии: запущен / «оплатите» / упал — перезапуск."""
    with CL_LOCK:
        for cid in list(PROCS):
            if cid not in CLIENTS:
                stop_client(cid)
        for cid, c in list(CLIENTS.items()):
            mode = client_mode(c)
            pr = PROCS.get(cid)
            if pr and pr["p"].poll() is None and pr["mode"] == mode:
                continue
            if pr and pr["p"].poll() is not None and time.time() - pr["t"] < 30:
                continue                                  # только что упал — подождём, не крутим перезапуски
            if pr and pr["p"].poll() is not None:
                _log_line(time.strftime("%H:%M:%S") + f" Клиенты: бот «{c.get('name')}» остановился "
                                                       f"(код {pr['p'].returncode}), перезапускаю")
                note_client_crash(cid, c)
            stop_client(cid)
            start_client(cid)
        for cid in list(CLIENTS):
            if (CLIENTS[cid].get("token") or "").strip() == (MAX_TOKEN or "").strip():
                continue                                  # «клиент» на токене основного бота — не пишем от его имени
            with contextlib.suppress(Exception):
                check_client_terms(cid)
    with contextlib.suppress(Exception):
        check_pay_requests()
    with contextlib.suppress(Exception):
        check_legal_requests()


def supervise_loop():
    with contextlib.suppress(Exception):
        kill_stale_clients()
    while True:
        try:
            supervise_once()
        except Exception:
            log_exc("клиенты")
        time.sleep(20)


def client_info(cid):
    """Что видно продавцу о клиенте: темы, каналы, источники, админы, опубликовано, код привязки, лог."""
    d = client_dir(cid)
    st = load_json(os.path.join(d, "settings.json"), {})
    th = load_json(os.path.join(d, "themes.json"), {}).get("themes") or []
    published = errors = 0
    for name in (os.listdir(d) if os.path.isdir(d) else []):
        if name.startswith("pub_state") and name.endswith(".json"):
            stt = load_json(os.path.join(d, name), {}).get("stats", {})
            published += stt.get("published", 0)
            errors += stt.get("errors", 0)
    sources = sum(len((t.get("own") or {}).get("channels") or []) for t in th if not t.get("legacy"))
    sources += len(st.get("channels") or [])
    code = ""
    with contextlib.suppress(OSError), open(os.path.join(d, "claim_code.txt")) as f:
        code = f.read().strip()
    cons = sorted(load_json(os.path.join(d, CONSENT_FILE), {}).values(), key=lambda x: x.get("t", 0))
    return {"consent": cons[0].get("date", "") if cons else "",
            "admins": len(st.get("admins") or []), "themes": max(1, len(th)), "sources": sources,
            "published": published, "errors": errors, "code": "" if st.get("admins") else code,
            "names": [t.get("name") for t in th] or ["Основная"]}


def client_log_tail(cid, n=15):
    try:
        with open(os.path.join(client_dir(cid), "bot.log"), encoding="utf-8", errors="ignore") as f:
            lines = [ln.rstrip() for ln in f.readlines()[-200:] if ln.strip() and not ln.startswith(" ")]
        return lines[-n:]
    except OSError:
        return []


def wait_claim_code(cid, sec=15):
    end = time.time() + sec
    while time.time() < end:
        code = client_info(cid)["code"]
        if code:
            return code
        time.sleep(0.5)
    return ""


def new_client(token):
    """Проверяет токен бота клиента. (id клиента, текст ошибки)."""
    token = (token or "").strip()
    if len(token) < 8 or " " in token:
        return None, "Не похоже на токен бота MAX — это длинная строка без пробелов."
    if token == MAX_TOKEN or any(c.get("token") == token for c in CLIENTS.values()):
        return None, "Этот токен уже используется (твой бот или другой клиент)."
    try:
        r = S.get(f"{MAX_API}/me", headers={"Authorization": token}, timeout=20)
    except Exception as e:
        return None, f"MAX не ответил ({type(e).__name__}) — попробуй ещё раз."
    if r.status_code != 200:
        return None, f"MAX не принял токен ({r.status_code}). Проверь, что скопирован целиком."
    me = r.json()
    cid = "c" + str(int(time.time() * 1000))
    with CL_LOCK:
        CLIENTS[cid] = {"name": me.get("name") or me.get("username") or "Клиент", "username": me.get("username") or "",
                        "token": token, "created": time.time(), "until": 0, "enabled": False, "warned": [],
                        "lim_themes": LIMIT_THEMES, "lim_sources": LIMIT_SOURCES}
        save_clients()
    return cid, ""


def set_plan(cid, i):
    """Тариф клиента: лимиты тем и каналов меняются сразу (бот клиента читает их из subscription.json)."""
    t = tariffs()[i]
    with CL_LOCK:
        c = CLIENTS[cid]
        c.update(plan=t["name"], lim_themes=t["themes"], lim_sources=t["sources"])
        save_clients()
        write_subscription(cid)


def extend_client(cid, days):
    with CL_LOCK:
        c = CLIENTS[cid]
        base = max(c.get("until", 0), time.time())
        c["until"] = base + days * 86400
        c["enabled"] = True
        c["warned"] = []
        save_clients()
        write_subscription(cid)
    supervise_once()


CL_STATUS = {"run": "✅ работает", "free": "🆓 бесплатный тариф", "expired": "⌛ подписка кончилась",
             "off": "⛔ выключен"}


def tenant_info(key):
    pub = errors = sources = 0
    names = []
    for t in tenant_themes(key):
        with theme_ctx(t):
            pub += STATS.get("published", 0)
            errors += STATS.get("errors", 0)
            sources += len(CFG["channels"])
            names.append(t.name)
    c = TENANTS.get(key) or {}
    return {"themes": len(tenant_themes(key)), "sources": sources, "published": pub, "errors": errors,
            "admins": len(c.get("admins") or []), "names": names or ["—"]}


def host_stats(now=None):
    """Статистика покупателей для панели хозяина: (текст, кабинеты с заявками на оплату)."""
    now = now or time.time()
    hk = host_id()
    cab = {k: c for k, c in TENANTS.items() if k != hk and c.get("plan") != "Хозяин"}
    mode = {k: tenant_mode(k, now) for k in cab}
    log = [e for e in GCFG.get("pay_log") or [] if e.get("key") != hk]
    paid = [e for e in log if e.get("price", 0) > 0]
    paid30 = [e for e in paid if e["t"] > now - 30 * 86400]
    payers = {e["key"] for e in paid}
    active = [k for k in cab if mode[k] == "run"]
    act_paid = [k for k in active if k in payers]
    soon = [k for k in active if cab[k].get("until", 0) - now <= 3 * 86400]
    expired = [k for k in cab if mode[k] == "free" and cab[k].get("until")]       # кончился срок — на бесплатном
    never = [k for k in cab if mode[k] == "free" and not cab[k].get("until")]
    off = [k for k in cab if mode[k] == "off"]
    new7 = [k for k in cab if cab[k].get("created", 0) > now - 7 * 86400]
    pend = [k for k in cab if cab[k].get("payreq")]
    plans = {}
    for k in active:
        plans[cab[k].get("plan") or "без тарифа"] = plans.get(cab[k].get("plan") or "без тарифа", 0) + 1
    name = lambda k: (TENANTS.get(k) or {}).get("name") or k
    lines = [
        "💼 Кабинеты · статистика покупателей\n",
        f"👥 Покупателей всего: {len(cab)}" + (f" (новых за 7 дней: {len(new7)})" if new7 else ""),
        f"✅ Пользуются с подпиской: {len(active)}",
        f"   • оплатили: {len(act_paid)}",
        f"   • бесплатно (пробный / промокод / подарок): {len(active) - len(act_paid)}",
        f"⏳ Подписка кончается за 3 дня: {len(soon)}",
        f"⌛ Подписка закончилась, теперь на 🆓 бесплатном: {len(expired)}",
        f"🆓 На бесплатном, ещё не платили: {len(never)}",
    ]
    if off:
        lines.append(f"⛔ Выключены тобой: {len(off)}")
    lines += ["",
              f"💳 Покупок подписки: {len(paid)} (за 30 дней: {len(paid30)})",
              f"💰 Выручка: {sum(e['price'] for e in paid)} ₽ (за 30 дней: {sum(e['price'] for e in paid30)} ₽)"]
    if plans:
        lines.append("📦 Тарифы у активных: " + ", ".join(f"{p} — {n}" for p, n in plans.items()))
    if pend:
        lines.append(f"🧾 Ждут подтверждения перевода: {len(pend)}")
    if active:
        lines += ["", "С подпиской сейчас:"] + [
            f"• {name(k)} — до {fmt_date(cab[k]['until'])}" for k in sorted(active, key=lambda k: cab[k]["until"])[:15]]
        if len(active) > 15:
            lines.append(f"…и ещё {len(active) - 15}")
    if log:
        lines += ["", "Последние покупки:"] + [
            f"• {time.strftime('%d.%m', time.localtime(e['t']))} {e.get('name') or name(e['key'])} — «{e['plan']}» {e['days']} дн., "
            + (f"{e['price']} ₽" if e.get("price") else "бесплатно") + (f" ({e['how']})" if e.get("how") else "")
            for e in log[-5:][::-1]]
    if not cab:
        lines.append("\nПока никого — жди первых клиентов.")
    return "\n".join(lines), pend


def host_open_cabinet(uid, chat_id, t, msg):
    """Хозяин переслал сообщение покупателя (или прислал его ID) — открываем его кабинет. True — открыли."""
    sid = None
    link = (msg or {}).get("link") or {}
    if link.get("type") == "forward":
        sid = ((link.get("sender") or {}).get("user_id"))
    elif re.fullmatch(r"\d{4,}", t or ""):
        sid = t
    if sid is None:
        return False
    key = tenant_key(sid)
    if key is None or key == host_id():
        if link.get("type") == "forward" and not (link.get("message") or {}).get("attachments") \
                and not ((msg.get("body") or {}).get("attachments")):
            api_send(chat_id, "Этот человек ещё не заходил в бота — кабинета у него нет.")
            return True
        return False
    m, rows = screen(f"clc:{key}", uid)
    send_screen(chat_id, m, rows)
    return True


def key_state(panel, env_name, shown):
    """Откуда ключ и его хвост — без самого секрета."""
    if panel:
        return f"✅ из бота ({shown(panel)}) — главнее переменной {env_name}"
    env = (os.environ.get(env_name) or "").strip()
    return f"✅ из переменной {env_name} в Bothost ({shown(env)})" if env else "— не задан"


def keys_screen():
    """🔑 Ключи и доступы: что задано, откуда (бот или Bothost) — хвостом, без секретов; кнопки ввода прямо в чат."""
    tail4 = lambda v: "…" + v[-4:]
    n_cookies = lambda v: f"{v.count('=')} cookies" + (", есть sessionid" if "sessionid=" in v else "")
    text = ("🔑 Ключи и доступы\n\nНажми кнопку и пришли ключ сюда, в чат: сообщение с ним удалю. Ключ из бота "
            "главнее переменной в Bothost; «🗑 Убрать» — снова из переменной.\n\n"
            f"💳 ЮKassa: {yk_seen()}\n"
            f"🔑 VK: {key_state(GCFG.get('vk_token'), 'VK_TOKEN', tail4)}\n"
            f"🎵 TikTok cookies: {key_state(GCFG.get('tt_cookies'), 'TIKTOK_COOKIES', n_cookies)}")
    text += "\n\n🤖 Ключи ИИ (пересказ постов): можно несколько — через запятую или с новой строки; " \
            "упёрся в лимит — бот берёт следующий."
    for prov, (env_name, title) in AI_PROVIDERS.items():
        text += f"\n🤖 {title}: {ai_key_state(prov)}"
    rows = [[B("💳 Ключи ЮKassa", "p:clyk")],
            [B("🔑 Ключ VK", "p:clvk")] + ([B("🗑 Убрать", "a:clkeyrm:vk")] if GCFG.get("vk_token") else []),
            [B("🎵 Cookies TikTok", "p:cltt")] + ([B("🗑 Убрать", "a:clkeyrm:tt")] if GCFG.get("tt_cookies") else [])]
    for prov, (env_name, title) in AI_PROVIDERS.items():
        saved = (GCFG.get("ai_keys") or {}).get(prov)
        rows.append([B(f"🤖 Ключи {title}", f"p:claikeys:{prov}")]
                    + ([B("🗑 Убрать", f"a:claikrm:{prov}")] if saved else []))
    return text, rows + [[B("◀️ Кабинеты", "m:cl")]]


def ai_key_state(prov):
    """Сколько ключей ИИ и откуда (бот или переменная) — только хвосты «••••abcd»."""
    env_name = AI_PROVIDERS[prov][0]
    saved = (GCFG.get("ai_keys") or {}).get(prov)
    word = lambda n: n_word(n, "ключ", "ключа", "ключей")
    if saved:
        return f"✅ из бота: {len(saved)} {word(len(saved))} ({mask_keys(saved)}) — главнее {env_name}"
    env = parse_keys(os.environ.get(env_name))
    if env:
        return f"✅ из переменной {env_name}: {len(env)} {word(len(env))} ({mask_keys(env)})"
    return "— не заданы"


POOL_STATUS = {"active": "✅ Активен", "flood": "⏳ FloodWait", "dead": "⛔ Не работает"}


def pool_screen():
    """👤 Telegram Аккаунты: кто читает источники, состояние и сколько каналов на каждом."""
    accs = POOL.status()
    lines = []
    for a in accs:
        st = POOL_STATUS.get(a["status"], a["status"])
        if a["status"] == "flood":
            st += " до " + time.strftime("%H:%M", time.localtime(a["until"]))
        elif a["status"] == "dead" and a["error"]:
            st += f" ({a['error']})"
        elif a["full"]:
            st += " (в лимите каналов Telegram — новых не берёт)"
        lines.append(f"• {a['title']} [{a['name']}] — {st} · каналов: {a['channels']}")
    text = ("👤 Telegram Аккаунты\n\nАккаунты читают источники вместе и одновременно. Новый источник читает "
            "аккаунт, который уже в нём состоит, иначе — самый свободный (вступает в канал сам). FloodWait — "
            "аккаунт отдыхает, читают другие.\n\n"
            + ("\n".join(lines) if lines else "Аккаунтов пока нет — источники читает основной вход бота.")
            + "\n\n➕ Новый аккаунт: нажми кнопку и пришли файл .session (Telethon) прямо в чат. Файл даёт "
              "полный доступ к аккаунту: бот хранит его только у себя, а твоё сообщение с ним удаляет.")
    rows = [[B("➕ Добавить аккаунт (.session)", "p:clsess")]]
    rows += [[B(f"🗑 Удалить {a['name']}"[:60], f"m:cltgaccdel:{a['name']}")] for a in accs]
    return text, rows + [[B("◀️ Кабинеты", "m:cl")]]


def session_atts(atts):
    """Вложения-файлы .session из сообщения."""
    return [a for a in atts or [] if a.get("kind") == "file" and str(a.get("name") or "").lower().endswith(".session")]


def session_upload(uid, atts):
    """Хозяин прислал файл .session: скачать, проверить, в sessions/ и в пул. Не подошёл — понятная причина,
    файла не остаётся."""
    files = session_atts(atts)
    if not files or not files[0].get("url"):
        PENDING[uid] = "clsess"
        return ("Нужен файл .session (вход Telethon) — пришли его файлом: 📎 → Файл.",
                [[B("◀️ Аккаунты", "m:cltgacc")]])
    a = files[0]
    stem = re.sub(r"[^\w-]", "_", a["name"][:-len(".session")])[:40] or "acc"
    name, n = stem, 2
    while os.path.exists(os.path.join("sessions", name + ".session")):
        name, n = f"{stem}_{n}", n + 1
    os.makedirs("sessions", mode=0o700, exist_ok=True)
    path = os.path.join("sessions", name + ".session")
    try:
        try:
            r = S.get(a["url"], timeout=60)
            r.raise_for_status()
        except Exception as e:
            raise SessionError(f"Не смог скачать файл из MAX ({type(e).__name__}) — пришли его ещё раз.")
        if len(r.content) > 20 * 1024 * 1024:
            raise SessionError("Файл слишком большой для .session (бывает до нескольких МБ) — это не файл входа.")
        with open(path, "wb") as f:
            f.write(r.content)
        acc = pool_run(POOL.add(path))
        text = f"✅ Аккаунт «{acc.title}» добавлен в пул. Аккаунтов: {len(POOL.status())}."
    except SessionError as e:
        text = "❌ " + str(e)
    except Exception as e:
        with contextlib.suppress(OSError):
            os.remove(path)
        text = f"❌ Не получилось добавить аккаунт: {type(e).__name__}"
    journal("👤 Пул: " + text)
    screen_text, rows = pool_screen()
    return text + "\n\n" + screen_text, rows


def host_screen(name, uid):
    """Панель /admin: все кабинеты (хозяин бота и администраторы ADMIN_IDS). None — это не экран панели."""
    if not can_panel(uid):
        return None
    back = [B("◀️ Кабинеты", "m:cl")]
    if name.startswith("clb"):
        got = beta_screen(name, uid)
        if got is not None:
            return got
        name = "cl"
    if name == "cl":
        text, pend = host_stats()
        text += ("\n\n✏️ Открыть кабинет покупателя: перешли сюда любое его сообщение или пришли его ID."
                 + ("\n\n⚠️ Задай свой контакт для клиентов: кнопка ниже." if SELLER_CONTACT == "@ВАШ_КОНТАКТ" else ""))
        rows = [[B(f"💳 Проверить оплату: {TENANTS[k].get('name')}"[:60], f"m:clc:{k}")] for k in pend[:5]]
        n_cab = sum(1 for k, c in TENANTS.items() if k != host_id() and c.get("plan") != "Хозяин")
        rows.append([B(f"👥 Все покупатели ({n_cab})", "m:cll:0")])
        rows.append([B("🎭 Мои каналы (мой кабинет)", "m:home")])   # хозяин настраивает свои темы (создаст, если их ещё нет)
        rows += [[B("💰 Тарифы и реквизиты", "m:cltar"), B(f"🎟 Промокоды ({len(promos())})", "m:clpr")],
                 [B("🧪 Бета-функции", "m:clbeta"), B("📊 Аналитика", "m:anl")],
                 [B("📞 Изменить мой контакт", "p:clcontact")],
                 [B("📣 Написать всем", "p:clmsg"), B("📢 Сделать рассылку", "p:cladpost")],
                 [B(f"📌 Обязательная подписка ({len(force_subs())})", "m:clfs")],
                 [B(f"👤 Telegram Аккаунты ({len(POOL.status())})", "m:cltgacc")],
                 [B("🔑 Ключи и доступы", "m:clkeys")],
                 [B(f"🧠 ИИ-расход: {AI_BUDGETS[ai_budget()]}"[:60], "m:clai")],
                 [B("👥 Покупатели", "m:buyers"), B("🎟 Коды", "m:codes")],
                 [B("👀 Посмотреть меню покупателя", "m:bview")],
                 [B("🗑 Очистить старые каналы", "m:clwipe")]]
        return text, rows
    if name == "clkeys":                                  # 🔑 ключи: ЮKassa, VK, TikTok — вводятся прямо в чат
        return keys_screen()
    if name == "cltgacc":                                 # 👤 пул аккаунтов Telegram
        return pool_screen()
    if name.startswith("cltgaccdel:"):
        acc = name.split(":", 1)[1]
        n = next((a["channels"] for a in POOL.status() if a["name"] == acc), 0)
        return (f"🗑 Удалить аккаунт «{acc}» из пула?\n\nФайл входа сотрётся. Его каналов: {n} — их будут "
                "читать другие аккаунты (или основной вход бота)."), \
            [[B("🗑 Да, удалить", f"a:cltgaccrm:{acc}")], [B("◀️ Аккаунты", "m:cltgacc")]]
    if name == "clfs":                                    # 📌 обязательная подписка
        subs = force_subs()
        text = ("📌 Обязательная подписка\n\nПокупатели пользуются ботом, только если подписаны на эти каналы MAX. "
                "Бот должен быть администратором канала — иначе MAX не даст проверить подписчиков.\n\n"
                + ("\n".join(f"• {c['title']} — {c['link']}" for c in subs) + "\n\nНажми на канал, чтобы убрать его."
                   if subs else "Сейчас каналов нет — ботом пользуются все."))
        rows = [[B(f"❌ {c['title']}"[:60], f"a:clfsdel:{i}")] for i, c in enumerate(subs)]
        return text, rows + [[B("➕ Добавить канал", "p:clfsadd")], back]
    if name == "clwipe":                                  # 🗑 очистить старые каналы — сначала спрашиваем
        ts = wipe_scope(uid)
        if not ts:
            return "🗑 Старых каналов нет — в твоём кабинете пусто.", [back]
        lines = []
        for t in ts:
            with theme_ctx(t):
                lines.append(f"• {t.name} — источников: {len(CFG['channels'])}, в очереди: {len(QUEUE)}"
                             + (", канал MAX привязан" if t.chat_id else ""))
        return ("🗑 Очистить старые каналы?\n\nУдалю все темы твоего кабинета:\n" + "\n".join(lines)
                + "\n\nКаналы MAX отвяжу, источники Telegram и очередь сотру, карточки на одобрение уберу. "
                  "Кабинеты покупателей не трону. Вернуть нельзя.\n\n"
                  "Потом подключишь новые каналы: «🎭 Мои каналы (мой кабинет)»."), \
            [[B("✅ Да, очистить", "a:clwipeok")], [B("Нет", "m:cl")]]
    if name == "clai":                                   # 🧠 ИИ-расход — сколько ключей ИИ тратить
        b = ai_budget()
        text = ("🧠 ИИ-расход\n\nСколько ключей ИИ тратить на автопересказ постов. Если ключи кончаются "
                "за день — выбери «Максимально экономить».\n\n"
                "• Максимально экономить — пересказ только для VIP-кабинетов, остальным выкладываем оригинал "
                "(ИИ-подсказки и аналитика по запросу работают как прежде).\n"
                "• Стандарт — пересказ для всех, у кого включён 🪄 ИИ-редактор.\n"
                "• Без лимитов — то же, что «Стандарт».\n\n"
                f"Сейчас: {AI_BUDGETS[b]}.")
        opts = [("save", "Максимально экономить"), ("std", "Стандарт"), ("max", "Без лимитов")]
        rows = [[B(("✅ " if k == b else "") + lbl, f"a:claibud:{k}")] for k, lbl in opts]
        return text, rows + [back]
    kind, _, key = name.partition(":")
    if kind == "cll":                                     # все покупатели: срок, тариф, дата регистрации
        page = int(key or 0)
        now = time.time()
        hk = host_id()
        cab = [(k, c) for k, c in TENANTS.items() if k != hk and c.get("plan") != "Хозяин"]
        order = {"run": 0, "free": 1, "off": 2}
        cab.sort(key=lambda kc: (order.get(tenant_mode(kc[0], now), 3), -(kc[1].get("until") or 0),
                                 -(kc[1].get("created") or 0)))
        per = 10
        pages = max(1, -(-len(cab) // per))
        page = max(0, min(page, pages - 1))
        lines, rows = [], []
        for i, (k, c) in enumerate(cab[page * per:(page + 1) * per], page * per + 1):
            mode = tenant_mode(k, now)
            until = c.get("until") or 0
            if mode == "run":
                left = int((until - now) // 86400)
                sub = f"✅ до {fmt_date(until)} ({left} дн.)"
            elif mode == "off":
                sub = "⛔ выключен"
            elif until:
                sub = f"⌛ закончилась {fmt_date(until)}"
            else:
                sub = "💤 не оплачивал"
            reg = f" · с {fmt_date(c['created'])}" if c.get("created") else ""
            plan = f"«{c['plan']}» " if c.get("plan") else ""
            lines.append(f"{i}. {c.get('name') or k} — {plan}{sub}{reg}")
            rows.append([B(f"{i}. {c.get('name') or k}"[:40], f"m:clc:{k}")])
        nav = []
        if page > 0:
            nav.append(B("◀️ Назад", f"m:cll:{page - 1}"))
        if page < pages - 1:
            nav.append(B("Дальше ▶️", f"m:cll:{page + 1}"))
        text = (f"👥 Все покупатели ({len(cab)})" + (f" · стр. {page + 1}/{pages}" if pages > 1 else "") + "\n\n"
                + ("\n".join(lines) or "пока никого") + "\n\nНажми на покупателя — откроется его кабинет.")
        return text, rows + ([nav] if nav else []) + [back]
    if kind == "clpr":
        lines = [promo_line(code, p) for code, p in promos().items()]
        text = ("🎟 Промокоды\n\n" + ("\n".join(lines) or "пока нет") +
                "\n\nПокупатель вводит промокод кнопкой «🎟 Ввести промокод» при оплате.\n"
                "• Скидка % — уменьшает цену ближайшей оплаты (100% — бесплатно).\n"
                "• Бесплатные дни — сразу открывают доступ на N дней.\n"
                "Каждый покупатель может активировать промокод только один раз.")
        rows = [[B(code[:30], f"m:clpo:{code}")] for code in promos()]
        rows += [[B("➕ Скидка %", "p:clpromo:pct"), B("➕ Бесплатные дни", "p:clpromo:days")], back]
        return text, rows
    if kind == "clpo":
        p = promos().get(key)
        if p is None:
            return "Промокод не найден (удалён?).", [[B("◀️ Промокоды", "m:clpr")]]
        st = promo_problem(p)
        who = [TENANTS.get(k, {}).get("name") or k for k in (p.get("used") or [])][-10:]
        text = (f"🎟 Промокод {key}\n\n"
                f"Даёт: {promo_what(p)}\n"
                f"Активаций: {len(p.get('used') or [])} из {p.get('max') or '∞ (без ограничения)'}\n"
                f"Действует: {('до ' + fmt_date(p['until'])) if p.get('until') else 'бессрочно'}\n"
                f"Состояние: {'✅ работает' if not st else '⛔ ' + st}"
                + (f"\nАктивировали: {', '.join(who)}" if who else ""))
        if p["kind"] == "pct":
            val = [B("➖10%", f"a:clpv:{key}:-10"), B("➖5%", f"a:clpv:{key}:-5"),
                   B("➕5%", f"a:clpv:{key}:5"), B("➕10%", f"a:clpv:{key}:10")]
        else:
            val = [B("➖7 дн", f"a:clpv:{key}:-7"), B("➖1 дн", f"a:clpv:{key}:-1"),
                   B("➕1 дн", f"a:clpv:{key}:1"), B("➕7 дн", f"a:clpv:{key}:7")]
        rows = [val,
                [B("Активации ➖10", f"a:clpm:{key}:-10"), B("➖1", f"a:clpm:{key}:-1"),
                 B("➕1", f"a:clpm:{key}:1"), B("➕10", f"a:clpm:{key}:10")],
                [B("Срок ➖7 дн", f"a:clpu:{key}:-7"), B("➖1 дн", f"a:clpu:{key}:-1"),
                 B("➕1 дн", f"a:clpu:{key}:1"), B("➕7 дн", f"a:clpu:{key}:7")],
                [B("➕30 дн", f"a:clpu:{key}:30"), B("♾ Активаций без лимита", f"a:clpm:{key}:inf"),
                 B("♾ Бессрочно", f"a:clpu:{key}:inf")],
                [B("⏹ Выключить сейчас", f"a:clpu:{key}:stop"), B("🗑 Удалить", f"a:clpdel:{key}")],
                [B("◀️ Промокоды", "m:clpr")]]
        return text, rows
    if kind == "clsrt":                                   # 📡 источники одной темы покупателя
        t = theme_by_id(key)
        return theme_sources_screen(t) if t is not None else ("Тема не найдена (удалена?).", [back])
    c = TENANTS.get(key)
    if kind in ("clc", "clpl", "cllim", "cldel", "clsrc") and c is None:
        return "Кабинет не найден (удалён?).", [back]
    if kind == "clsrc":                                   # 📡 источники покупателя: одна тема — сразу её
        ts = tenant_themes(key)
        if len(ts) == 1:
            return theme_sources_screen(ts[0])
        return ((f"📡 Источники Telegram — кабинет «{c.get('name') or key}». Выбери тему:" if ts else
                 "У покупателя пока нет тем."),
                [[B(f"📡 {t.name}"[:60], f"m:clsrt:{t.id}")] for t in ts] + [[B("◀️ Кабинет", f"m:clc:{key}")]])
    if kind == "cltar":
        pay = GCFG.get("pay_info") or "— не заданы"
        text = ("💰 Тарифы (цена за 30 дней; за 90 дней — ×2,5)\n\n" + "\n".join(tariff_line(t) for t in tariffs())
                + f"\n\n💳 Реквизиты (ручная оплата):\n{pay}\n\n💳 Онлайн-оплата ЮKassa: " + yk_check()[1]
                + f"\n\n📞 Мой контакт: {SELLER_CONTACT}"
                + ("\n💬 Кнопка «Написать мне лично» у покупателей ведёт: " + seller_link() if seller_link() else
                   "\n💬 Кнопки «Написать мне лично» у покупателей нет: пришли свой @ник или ссылку https://max.ru/… "
                   "через «📞 Изменить мой контакт» (или поставь себе ник в MAX и напиши боту)."))
        return text, [[B("✏️ Изменить тарифы", "p:cltarset")], [B("💳 Изменить реквизиты", "p:clpayset")],
                      [B(f"🎁 Пробный период при «Старте»: {GCFG.get('trial_days', 3) or 'выкл'}"
                         + (" дн." if GCFG.get('trial_days', 3) else ""), "a:cltrial")],
                      [B("🔑 Ввести ключи ЮKassa", "p:clyk"), B("🔎 Проверить ЮKassa", "a:ykchk")],
                      [B("⚖️ Юридическая информация", "m:cllegal")],
                      [B("📞 Изменить мой контакт", "p:clcontact")], back]
    if kind == "clc":
        info = tenant_info(key)
        mode = tenant_mode(key)
        left = int((c.get("until", 0) - time.time()) // 86400)
        text = (f"👤 Кабинет: {c.get('name')} (ID {(c.get('admins') or ['?'])[0]})\n\n"
                f"Статус: {CL_STATUS[mode]}\n"
                f"Тариф: «{c.get('plan') or 'не выбран'}»\n"
                + (f"Подписка до: {fmt_date(c['until'])} ({left} дн.)\n" if c.get("until") else "Не оплачивал\n")
                + f"Тем: {info['themes']} из {c.get('lim_themes', LIMIT_THEMES)} — {', '.join(info['names'])}\n"
                f"Каналов-источников: {info['sources']} · опубликовано: {info['published']} · ошибок: {info['errors']}")
        chan_text, chan_rows = cabinet_channels(key)
        text += "\n\n" + chan_text
        if c.get("payreq"):
            pr = c["payreq"]
            text += f"\n\n💳 Ждёт подтверждения оплаты: «{pr['plan']}» {pr['days']} дн. — {pr['price']} ₽"
        nick = next((n for n in (c.get("usernames") or {}).values() if not bot_like_nick(n)), "")
        phone = c.get("phone")
        text += (f"\n💬 Личка: https://max.ru/{nick}" if nick else "")
        text += (f"\n📞 Телефон: {phone} — добавь в контакты телефона и пиши лично из своего MAX" if phone else
                 "" if nick else "\n💬 Личной ссылки нет: у человека нет ника в MAX. Нажми «📇 Попросить контакт» — "
                 "бот попросит его поделиться номером, и ты напишешь лично. Или «✉️ Написать» — придёт от бота.")
        if c.get("trial"):
            text += "\n🎁 Пробный период был"
        if key == str(HOST_OWNER):                          # свой кабинет — не продлевать/не удалять, а настраивать
            return text + "\n\n⭐ Это твой личный кабинет — бесплатно и бессрочно.", \
                [[B("🎭 Настроить мои каналы", "m:home")], back]
        rows = [[B("+30 дней", f"a:clext:{key}:30"), B("+90 дней", f"a:clext:{key}:90"), B("+3 дня", f"a:clext:{key}:3")]]
        if c.get("payreq"):
            rows.insert(0, [B(f"✅ Оплата пришла — открыть ({c['payreq']['days']} дн.)", f"a:clpayok:{key}")])
        if nick:
            rows.append([LB("💬 Написать ему лично", f"https://max.ru/{nick}")])
        elif not phone:
            rows.append([B("📇 Попросить контакт", f"a:clask:{key}")])
        rows += chan_rows                                   # 📺 открыть канал покупателя — как там выходят посты
        rows.append([B("📡 Источники Telegram", f"m:clsrc:{key}")])   # поправить источники покупателя (поддержка)
        rows += [[B("⛔ Выключить", f"a:cloff:{key}") if mode != "off" else B("✅ Включить", f"a:clon:{key}")],
                 [B("📦 Тариф", f"m:clpl:{key}"), B("📏 Лимиты", f"m:cllim:{key}")],
                 [B("✉️ Написать", f"p:clsay:{key}"), B("🧪 Бета-функции", f"m:clbt:{key}")],
                 [B("🗑 Удалить", f"m:cldel:{key}")], back]
        return text, rows
    if kind == "clpl":
        text = (f"📦 Тариф кабинета «{c.get('name')}»: сейчас «{c.get('plan') or 'не выбран'}»\n\n"
                + "\n".join(tariff_line(t) for t in tariffs()) + "\n\nСмена меняет лимиты сразу, срок не трогает.")
        rows = [[B(("✅ " if t["name"] == c.get("plan") else "") + t["name"], f"a:clsetplan:{key}:{i}")]
                for i, t in enumerate(tariffs())]
        return text, rows + [[B("◀️ Кабинет", f"m:clc:{key}")]]
    if kind == "cllim":
        text = (f"📏 Лимиты «{c.get('name')}»\n\nТем: {c.get('lim_themes', LIMIT_THEMES)}\n"
                f"Каналов-источников в теме: {c.get('lim_sources', LIMIT_SOURCES)}")
        rows = [[B("Темы ➖", f"a:cllimset:{key}:themes:-1"), B("Темы ➕", f"a:cllimset:{key}:themes:1")],
                [B("Каналы ➖", f"a:cllimset:{key}:src:-1"), B("Каналы ➕", f"a:cllimset:{key}:src:1")],
                [B("◀️ Кабинет", f"m:clc:{key}")]]
        return text, rows
    if kind == "cldel":
        return (f"🗑 Удалить кабинет «{c.get('name')}»? Его каналы, темы и настройки сотрутся. Вернуть нельзя.",
                [[B("✅ Да, удалить", f"a:cldelok:{key}"), B("Нет", f"m:clc:{key}")]])
    return None


def theme_sources_screen(t):
    """Панель: источники Telegram темы покупателя — поправить при поддержке (покупатель управляет ими и сам)."""
    with theme_ctx(t):
        chs, lim = list(CFG["channels"]), lim_sources()
    c = TENANTS.get(t.owner) or {}
    text = (f"📡 Источники Telegram — тема «{t.name}», кабинет «{c.get('name') or t.owner}»\n\n"
            + sources_list(chs) + f"\n\nПо тарифу покупателя — до {lim} в теме."
            + ("\nНажми на канал, чтобы убрать его." if chs else "")
            + ("\nКнопками — первые 48: убери их, покажу следующие." if len(chs) > 48 else ""))
    return text, source_rows(chs, lambda i: f"a:clsrcdel:{t.id}:{i}", per=2) + [
        [B("➕ Добавить канал", f"p:clsrcadd:{t.id}")], [B("◀️ Кабинет", f"m:clc:{t.owner}")]]


def host_action(p, uid):
    if not can_panel(uid):
        return None
    if p[0].startswith("clb"):
        return beta_action(p) or "clbeta"
    if p[0] == "clask" and len(p) > 1 and p[1] in TENANTS:   # попросить у покупателя контакт для личной переписки
        sent = tenant_send(p[1], "👋 " + CONTACT_ASK, [[CONTACT_BTN]])
        FLASH[uid] = ("📇 Попросил контакт — когда покупатель нажмёт кнопку, номер придёт сюда." if sent else
                      "Не получилось — покупатель ещё не писал боту.")
        return f"clc:{p[1]}"
    if p[0] == "cladgo":                                    # 📢 разослать рекламу (в фоне — получателей может быть много)
        d = AD_DRAFT.pop(uid, None)
        if not d or not d.get("erid"):
            FLASH[uid] = "Черновика рекламы нет — начни заново: «📢 Сделать рассылку»."
            return "cl"
        threading.Thread(target=ad_broadcast, args=(d,), daemon=True).start()
        FLASH[uid] = f"📢 Рассылаю рекламу: получателей {len(ad_targets())}. Итог пришлю сюда."
        return "cl"
    if p[0] == "buyerask" and len(p) > 1:                  # 📇 покупателю — кнопка «Поделиться контактом»
        if not tenant_send(p[1], PROMPTS["buyerreq"], [[CONTACT_BTN]]):
            FLASH[uid] = "Покупатель ещё не писал боту — пусть напишет /start, тогда кнопка дойдёт."
        return f"buyer:{p[1]}"
    if p[0] == "buyercard" and len(p) > 1:                 # 📇 карточка контакта — хозяину в чат
        contact = (TENANTS.get(p[1]) or {}).get("contact") or {}
        chat = HOST_CHAT.get("chat")
        if not contact.get("vcf") or not chat:
            FLASH[uid] = "Контакт покупателя пока не получен."
        else:
            api_send_contact(chat, contact["vcf"], f"📇 Контакт покупателя «{(TENANTS.get(p[1]) or {}).get('name') or p[1]}»")
        return f"buyer:{p[1]}"
    if p[0] == "codesexp":                                 # 📄 экспорт активных кодов — файлом
        active = [c for c, _, _ in CODES.listing()]
        if not active:
            FLASH[uid] = "Активных кодов нет — сначала сделай партию."
        else:
            CODE_EXPORT[uid] = write_codes_file(active)
        return "codes"
    if p[0] == "claikrm" and len(p) > 1 and p[1] in AI_PROVIDERS:   # 🤖 убрать ключи ИИ из бота
        (GCFG.get("ai_keys") or {}).pop(p[1], None)
        save_cfg()
        AI_ROTATORS[p[1]].reset()
        env_name, title = AI_PROVIDERS[p[1]]
        FLASH[uid] = (f"🗑 Ключи {title} убраны из бота — " + (f"теперь из переменной {env_name}"
                      if parse_keys(os.environ.get(env_name)) else f"переменной {env_name} в Bothost тоже нет"))
        return "clkeys"
    if p[0] == "clkeyrm" and len(p) > 1:                    # 🔑 убрать ключ из бота — снова из переменной Bothost
        field, env_name, what = {"vk": ("vk_token", "VK_TOKEN", "Ключ VK"),
                                 "tt": ("tt_cookies", "TIKTOK_COOKIES", "Cookies TikTok")}[p[1]]
        GCFG.pop(field, None)
        save_cfg()
        SOCIAL_CACHE.clear()
        FLASH[uid] = (f"🗑 {what} убран из бота — " + ("теперь из переменной " + env_name if os.environ.get(env_name)
                                                         else f"переменной {env_name} в Bothost тоже нет"))
        return "clkeys"
    if p[0] == "cltgaccrm" and len(p) > 1:                  # 👤 удалить аккаунт из пула
        FLASH[uid] = (f"🗑 Аккаунт «{p[1]}» удалён из пула." if pool_run(POOL.remove(p[1]))
                      else "Этого аккаунта уже нет в пуле.")
        return "cltgacc"
    if p[0] == "clfsdel" and len(p) > 1:                    # 📌 убрать канал из обязательной подписки
        subs, i = force_subs(), int(p[1])
        if 0 <= i < len(subs):
            subs.pop(i)
            save_cfg()
        return "clfs"
    if p[0] == "cladno":
        AD_DRAFT.pop(uid, None)
        FLASH[uid] = "Рассылка отменена."
        return "cl"
    if p[0] == "cltrial":                                   # пробный период: выкл → 1 → 3 → 7 дней
        opts = [0, 1, 3, 7]
        cur_d = int(GCFG.get("trial_days", 3) or 0)
        GCFG["trial_days"] = opts[(opts.index(cur_d) + 1) % len(opts)] if cur_d in opts else 3
        save_cfg()
        return "cltar"
    if p[0] in ("clpv", "clpm", "clpu", "clpdel"):          # промокоды
        code = p[1] if len(p) > 1 else ""
        pr = promos().get(code)
        if pr is None:
            return "clpr"
        if p[0] == "clpdel":
            promos().pop(code, None)
            save_cfg()
            return "clpr"
        arg = p[2] if len(p) > 2 else ""
        if p[0] == "clpv":
            lo, hi = (1, 100) if pr["kind"] == "pct" else (1, 365)
            pr["value"] = max(lo, min(hi, int(pr["value"]) + int(arg)))
        elif p[0] == "clpm":
            used = len(pr.get("used") or [])
            base = int(pr.get("max") or 0) or max(used, 10)       # было «без лимита» — считаем от 10
            pr["max"] = 0 if arg == "inf" else max(used, 1, base + int(arg))
        elif p[0] == "clpu":
            if arg == "inf":
                pr["until"] = 0
            elif arg == "stop":
                pr["until"] = time.time() - 1
            else:
                base = max(pr.get("until") or 0, time.time()) if pr.get("until") else time.time() + 30 * 86400
                pr["until"] = base + int(arg) * 86400
                if pr["until"] <= time.time():                  # убавили до прошлого — промокод выключен
                    pr["until"] = time.time() - 1
        save_cfg()
        return f"clpo:{code}"
    if p[0] == "clsrcdel" and len(p) > 2:                   # 📡 убрать источник у темы покупателя
        t = theme_by_id(p[1])
        if t is None:
            return "cl"
        with theme_ctx(t):
            i = int(p[2]) if p[2].isdigit() else -1
            if 0 <= i < len(CFG["channels"]):
                gone = drop_source(i)
                save_cfg()
                journal(f"📡 Продавец убрал источник {ch_label(gone)}")
        return f"clsrt:{t.id}"
    if p[0] == "claibud" and len(p) > 1 and p[1] in AI_BUDGETS:   # 🧠 ИИ-расход — сохранить выбор
        GCFG["ai_budget"] = p[1]
        save_cfg()
        FLASH[uid] = f"🧠 ИИ-расход: {AI_BUDGETS[p[1]]}."
        return "clai"
    if p[0] == "clwipeok":                                  # 🗑 очистить старые каналы своего кабинета
        PENDING.pop(uid, None)                              # и «ожидание ввода» для удалённых тем — тоже
        n, n_src, n_posts = wipe_themes(wipe_scope(uid))
        if n:
            journal(f"🗑 Очистка старых каналов: тем {n}, источников {n_src}, постов в очереди {n_posts}")
        FLASH[uid] = (f"🗑 Удалил тем: {n} (источников: {n_src}, постов в очереди: {n_posts}). Подключи новые "
                      "каналы: «🎭 Мои каналы (мой кабинет)»." if n else "Старых каналов нет — удалять нечего.")
        return "cl"
    if p[0] in ("clmsgall",):
        return "cl"
    key = p[1] if len(p) > 1 else ""
    if p[0] in ("clext", "cloff", "clon", "clsetplan", "cllimset", "cldelok", "clpayok", "clpayno") and key not in TENANTS:
        return "cl"
    if p[0] == "clext":
        host_extend(key, int(p[2]))
        tenant_send(key, f"✅ Доступ открыт до {fmt_date(TENANTS[key]['until'])}. Настрой канал: меню → «📢 Куда выкладывать».")
        return f"clc:{key}"
    if p[0] == "cloff":
        with CL_LOCK:
            TENANTS[key]["enabled"] = False
            save_tenants()
        return f"clc:{key}"
    if p[0] == "clon":
        with CL_LOCK:
            TENANTS[key]["enabled"] = True
            save_tenants()
        return f"clc:{key}"
    if p[0] == "clsetplan":
        host_set_plan(key, int(p[2]))
        return f"clc:{key}"
    if p[0] == "cllimset":
        fld = "lim_themes" if p[2] == "themes" else "lim_sources"
        with CL_LOCK:
            cur_v = TENANTS[key].get(fld, LIMIT_THEMES if fld == "lim_themes" else LIMIT_SOURCES)
            TENANTS[key][fld] = max(1, min(100, cur_v + int(p[3])))
            save_tenants()
        return f"cllim:{key}"
    if p[0] == "clpayok":
        pr = TENANTS[key].pop("payreq", None)
        TENANTS[key].pop("pay", None)
        save_tenants()
        if pr:
            host_apply_paid(key, pr["plan"], pr["days"], pr.get("price", 0), "перевод")
        return f"clc:{key}"
    if p[0] == "clpayno":
        TENANTS[key].pop("payreq", None)
        save_tenants()
        tenant_send(key, f"❌ Оплату пока не нашли. Проверь перевод или напиши: {SELLER_CONTACT}")
        return f"clc:{key}"
    if p[0] == "cldelok":
        with contextlib.suppress(Exception):
            legal_forget(key)                              # ⚖️ просил удалить данные — подтверждение ему
        for t in tenant_themes(key):
            with contextlib.suppress(Exception):
                delete_theme(t)
        with CL_LOCK:
            TENANTS.pop(key, None)
            save_tenants()
        return "cl"
    return "cl"


def clients_screen(name, uid):
    """Экраны панели продавца. None — это не экран панели."""
    if not is_owner(uid) or CLIENT_MODE:
        return None
    back = [B("◀️ Клиенты", "m:cl")]
    if name == "cl":
        lines, rows = [], []
        for i, (cid, c) in enumerate(sorted(CLIENTS.items(), key=lambda kv: kv[1].get("until", 0))):
            left = int((c.get("until", 0) - time.time()) // 86400)
            when = f"до {fmt_date(c['until'])} ({left} дн.)" if c.get("until") else "срок не задан"
            lines.append(f"{i + 1}. {c.get('name')} — {CL_STATUS[client_mode(c)]}, {when}")
            rows.append([B(f"{i + 1}. {c.get('name')}"[:40], f"m:clc:{cid}")])
        text = ("💼 Клиенты\n\n" + ("\n".join(lines) or "пока никого")
                + "\n\nТарифы:\n" + "\n".join(tariff_line(t) for t in tariffs())
                + f"\nКонтакт в сообщениях клиентам: {SELLER_CONTACT}")
        share = GCFG.get("share_tg", False)
        text += ("\n\n👤 Мой Telegram для клиентов: " + ("вкл — их боты читают каналы через твой аккаунт "
                 "(файлы, большие видео), входить им не нужно" if share else "выкл — каждый входит сам или читает "
                 "через t.me"))
        rows += [[B("➕ Новый клиент", "p:clnew")], [B("💰 Тарифы и реквизиты", "m:cltar")],
                 [B(f"👤 Мой Telegram для клиентов: {'вкл' if share else 'выкл'}", "a:clsharetg")],
                 [B("📣 Написать всем клиентам", "p:clmsg")], [B("◀️ В меню", "m:main")]]
        return text, rows
    if name == "cltar":
        pay = GCFG.get("pay_info") or "— не заданы (клиентам пишу: «напиши продавцу, он пришлёт реквизиты»)"
        text = ("💰 Тарифы (цена за 30 дней; за 90 дней — ×2,5)\n\n" + "\n".join(tariff_line(t) for t in tariffs())
                + f"\n\n💳 Реквизиты для клиентов:\n{pay}\n\n"
                  "Клиент выбирает тариф в своём боте (кнопка «💳 Тариф и оплата»), видит реквизиты и жмёт "
                  "«Я оплатил» — тебе приходит заявка с кнопкой «✅ Оплата пришла — продлить».")
        text += ("\n\n💳 Онлайн-оплата ЮKassa: " + ("подключена ✅ — клиенты платят картой/СБП, бот продлевает сам."
                 if yk_keys() else "не подключена. Подключить: в Bothost → переменные окружения добавь "
                 "YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY (из личного кабинета ЮKassa) и перезапусти бота."))
        text += f"\n\n📞 Мой контакт для клиентов: {SELLER_CONTACT}"
        return text, [[B("✏️ Изменить тарифы", "p:cltarset")], [B("💳 Изменить реквизиты", "p:clpayset")],
                      [B("📞 Изменить мой контакт", "p:clcontact")], back]
    kind, _, cid = name.partition(":")
    c = CLIENTS.get(cid)
    if kind in ("clc", "clplan", "cllog", "cldel", "cllim", "clpl") and c is None:
        return "Клиент не найден (уже удалён?).", [back]
    if kind == "clplan":
        text = (f"🤖 Бот клиента: {c['name']}" + (f" (@{c['username']})" if c.get("username") else "")
                + "\n\nНа сколько включить?")
        rows = [[B(f"🎁 Пробный — 3 дня («{tariffs()[0]['name']}»)"[:60], f"a:clsub:{cid}:0:3")]]
        rows += [[B(f"{t['name']} · 30 дн — {t['price']} ₽"[:60], f"a:clsub:{cid}:{i}:30"),
                  B(f"90 дн — {price90(t['price'])} ₽", f"a:clsub:{cid}:{i}:90")] for i, t in enumerate(tariffs())]
        rows += [[B("❌ Отмена (удалить)", f"a:cldelok:{cid}")]]
        return text, rows
    if kind == "clpl":
        text = (f"📦 Тариф «{c['name']}»: сейчас «{c.get('plan') or 'индивидуальный'}»\n\n"
                + "\n".join(tariff_line(t) for t in tariffs())
                + "\n\nСмена тарифа меняет лимиты сразу, срок не трогает.")
        rows = [[B(("✅ " if t["name"] == c.get("plan") else "") + t["name"], f"a:clsetplan:{cid}:{i}")]
                for i, t in enumerate(tariffs())]
        return text, rows + [[B("◀️ Клиент", f"m:clc:{cid}")]]
    if kind == "clc":
        info = client_info(cid)
        mode = client_mode(c)
        pr = PROCS.get(cid)
        alive = pr is not None and pr["p"].poll() is None
        left = int((c.get("until", 0) - time.time()) // 86400)
        text = (f"🤖 {c['name']}" + (f" (@{c['username']})" if c.get("username") else "") + "\n\n"
                f"Статус: {CL_STATUS[mode]}" + ("" if alive else " · ⚠️ процесс не запущен, перезапускаю") + "\n"
                + f"Тариф: «{c.get('plan') or 'индивидуальный'}»\n"
                + (f"Подписка до: {fmt_date(c['until'])} ({left} дн.)\n" if c.get("until") else "")
                + f"Темы: {info['themes']} из {c.get('lim_themes', LIMIT_THEMES)} — {', '.join(info['names'])}\n"
                f"Каналов-источников: {info['sources']} (до {c.get('lim_sources', LIMIT_SOURCES)} в теме)\n"
                f"Админов: {info['admins']} · опубликовано: {info['published']} · ошибок: {info['errors']}\n"
                + (f"⚖️ Права на контент подтвердил: {info['consent']}" if info["consent"]
                   else "⚖️ Права на контент ещё не подтверждал"))
        if info["code"]:
            text += (f"\n\n🔑 Код привязки для клиента: {info['code']}\nПусть откроет своего бота в MAX, "
                     f"нажмёт «Старт» и отправит этот код — станет владельцем.")
        rows = [[B("+3 дня", f"a:clext:{cid}:3"), B("+30 дней", f"a:clext:{cid}:30"),
                 B("+90 дней", f"a:clext:{cid}:90")],
                [B("⛔ Выключить", f"a:cloff:{cid}") if mode != "off" else B("✅ Включить", f"a:clon:{cid}")],
                [B("📦 Тариф", f"m:clpl:{cid}"), B("📏 Лимиты", f"m:cllim:{cid}")],
                [B("📜 Журнал", f"m:cllog:{cid}")],
                [B("🔑 Telegram: " + ("вкл ✅" if c.get("tg", True) else "выкл ⛔"), f"a:cltg:{cid}")],
                [B("🗑 Удалить клиента", f"m:cldel:{cid}")], back]
        return text, rows
    if kind == "cllim":
        text = (f"📏 Лимиты «{c['name']}»\n\nТем (каналов MAX): {c.get('lim_themes', LIMIT_THEMES)}\n"
                f"Каналов-источников в одной теме: {c.get('lim_sources', LIMIT_SOURCES)}\n\n"
                "Изменения применятся при перезапуске бота клиента (сразу).")
        rows = [[B("Темы ➖", f"a:cllimset:{cid}:themes:-1"), B("Темы ➕", f"a:cllimset:{cid}:themes:1")],
                [B("Источники ➖5", f"a:cllimset:{cid}:sources:-5"), B("Источники ➕5", f"a:cllimset:{cid}:sources:5")],
                [B("◀️ Клиент", f"m:clc:{cid}")]]
        return text, rows
    if kind == "cllog":
        lines = client_log_tail(cid)
        text = f"📜 Журнал «{c['name']}» (последнее)\n\n" + ("\n".join(lines) or "пока пусто")
        return text[-3500:], [[B("🔄 Обновить", f"m:cllog:{cid}")], [B("◀️ Клиент", f"m:clc:{cid}")]]
    if kind == "cldel":
        text = (f"🗑 Удалить клиента «{c['name']}»? Его бот остановится, настройки будут удалены. "
                "Вернуть нельзя.")
        return text, [[B("✅ Да, удалить", f"a:cldelok:{cid}"), B("Нет", f"m:clc:{cid}")]]
    return None


def clients_action(p, uid):
    """Кнопки панели продавца (p — части payload после «a:»). Возвращает экран или None."""
    if not is_owner(uid) or CLIENT_MODE or not p[0].startswith("cl"):
        return None
    if p[0] == "clsharetg":
        GCFG["share_tg"] = not GCFG.get("share_tg", False)
        save_cfg()
        with CL_LOCK:
            for c_id in list(PROCS):                 # перезапуск: включат/выключат общий аккаунт
                stop_client(c_id)
        supervise_once()
        return "cl"
    cid = p[1] if len(p) > 1 else ""
    if cid not in CLIENTS:
        return "cl"
    if p[0] == "clext":
        new = not CLIENTS[cid].get("until")
        extend_client(cid, int(p[2]))
        if new:
            wait_claim_code(cid)
        return f"clc:{cid}"
    if p[0] == "clsub":                                   # новый клиент: тариф + срок
        new = not CLIENTS[cid].get("until")
        set_plan(cid, int(p[2]))
        extend_client(cid, int(p[3]))
        if new:
            wait_claim_code(cid)
        return f"clc:{cid}"
    if p[0] == "clsetplan":
        set_plan(cid, int(p[2]))
        return f"clc:{cid}"
    if p[0] in ("clpayok", "clpayno"):
        path = os.path.join(client_dir(cid), "pay_request.json")
        req = load_json(path, {})
        with contextlib.suppress(OSError):
            os.remove(path)
        if not req:
            return f"clc:{cid}"
        if p[0] == "clpayno":
            client_send(cid, f"❌ Продавец пока не нашёл твой перевод за «{req.get('plan')}». "
                             f"Проверь оплату или напиши: {SELLER_CONTACT}")
            return f"clc:{cid}"
        apply_paid(cid, req)
        return f"clc:{cid}"
    if p[0] == "cloff":
        with CL_LOCK:
            CLIENTS[cid]["enabled"] = False
            save_clients()
            write_subscription(cid)
        supervise_once()
        return f"clc:{cid}"
    if p[0] == "clon":
        with CL_LOCK:
            CLIENTS[cid]["enabled"] = True
            save_clients()
            write_subscription(cid)
        supervise_once()
        return f"clc:{cid}"
    if p[0] == "cltg":
        with CL_LOCK:
            CLIENTS[cid]["tg"] = not CLIENTS[cid].get("tg", True)
            save_clients()
            stop_client(cid)                              # перезапуск, чтобы кнопка появилась/пропала
        supervise_once()
        return f"clc:{cid}"
    if p[0] == "cllimset":
        key = "lim_themes" if p[2] == "themes" else "lim_sources"
        with CL_LOCK:
            c = CLIENTS[cid]
            c[key] = max(1, min(100, c.get(key, LIMIT_THEMES if key == "lim_themes" else LIMIT_SOURCES) + int(p[3])))
            save_clients()
            stop_client(cid)                              # перезапуск с новыми лимитами
        supervise_once()
        return f"cllim:{cid}"
    if p[0] == "cldelok":
        lreq = load_json(os.path.join(client_dir(cid), "legal_request.json"), {})
        if isinstance(lreq, dict) and lreq.get("status") in ("new", "sent"):
            with contextlib.suppress(Exception):          # просил удалить данные — подтверждение, пока бот жив
                client_send(cid, LEGAL_DELETED)
            journal(f"🗑 Данные клиента «{CLIENTS[cid].get('name')}» удалены по его запросу")
        with CL_LOCK:
            stop_client(cid)
            CLIENTS.pop(cid, None)
            save_clients()
            import shutil
            for name, tag in ((CONSENT_FILE, ""), (LEGAL_CONSENTS_FILE, "_legal")):
                cons = os.path.join(client_dir(cid), name)
                if os.path.exists(cons):                  # подтверждения прав и согласия с офертой — доказательства,
                    os.makedirs("consent_archive", exist_ok=True)   # храним и после удаления клиента
                    with contextlib.suppress(OSError):
                        shutil.copy(cons, os.path.join("consent_archive",
                                                       f"{cid}{tag}_{time.strftime('%Y%m%d')}.json"))
            shutil.rmtree(client_dir(cid), ignore_errors=True)
        return "cl"
    return "cl"


def clients_text(action, text, uid):
    """Ввод текста в панели продавца. (ответ, экран) или None."""
    if action == "clfsadd" and HOST_MODE:
        return fsub_add(text)
    if action == "clnew":
        cid, err = new_client(text)
        if not cid:
            return err, "cl"
        return f"Токен подходит ✅ Бот: {CLIENTS[cid]['name']}.", f"clplan:{cid}"
    if action == "clmsg":
        if HOST_MODE:
            n = sum(tenant_send(k, "✉️ " + text.strip()) for k in list(TENANTS))
            return f"📣 Отправил: {n} кабинетам.", "cl"
        n = sum(client_send(cid, text.strip()) for cid in list(CLIENTS))
        return f"📣 Отправил: {n} сообщений админам клиентов.", "cl"
    if action.startswith("clsay:"):
        cid = action.split(":", 1)[1]
        if HOST_MODE:
            if cid not in TENANTS:
                return "Кабинет не найден.", "cl"
            n = tenant_send(cid, "✉️ Сообщение от хозяина бота:\n\n" + text.strip())
            return ("Отправил ✅" if n else "Не получилось — кабинет ещё не писал боту."), f"clc:{cid}"
        if cid not in CLIENTS:
            return "Клиент не найден.", "cl"
        n = client_send(cid, "✉️ Сообщение от продавца:\n\n" + text.strip())
        return ("Отправил клиенту ✅" if n else "Не получилось отправить — клиент ещё не писал своему боту."), f"clc:{cid}"
    if action == "cltarset":
        new, bad = [], []
        for line in text.splitlines():
            m = re.fullmatch(r"\s*(.+?)[\s:—-]+(\d+)\D+(\d+)\D+(\d+)\s*(?:₽|р|руб\.?)?\s*", line)
            if m and int(m.group(2)) > 0 and int(m.group(3)) > 0:
                new.append({"name": m.group(1).strip()[:20], "themes": int(m.group(2)), "sources": int(m.group(3)),
                            "price": int(m.group(4))})
            elif line.strip():
                bad.append(line.strip())
        if not new:
            return ("Не понял. Пришли по одному тарифу в строке: название, темы, каналы, цена за 30 дней.\n"
                    "Например: Старт 1 5 300"), "cltar"
        GCFG["tariffs"] = new
        save_cfg()
        for cid in list(CLIENTS):
            with contextlib.suppress(Exception):
                write_subscription(cid)
        return "Тарифы сохранены ✅" + (f"\nНе понял строки: {'; '.join(bad)}" if bad else ""), "cltar"
    if action == "clcontact":
        global SELLER_CONTACT
        if not contact_ok(text):
            return ("Это не похоже на контакт. Пришли ник (например @ivan), ссылку https://max.ru/… "
                    "или телефон — покупатели будут писать туда."), "cltar"
        SELLER_CONTACT = text.strip()[:80]
        panel_set("seller_contact", SELLER_CONTACT, os.environ.get("SELLER_CONTACT"))
        save_cfg()
        for cid in list(CLIENTS):
            with contextlib.suppress(Exception):
                write_subscription(cid)
        url = seller_link()
        keep = ("" if (os.environ.get("SELLER_CONTACT") or "").strip() == SELLER_CONTACT else
                "\n\n💾 Чтобы контакт не пропал при пересборке бота на хостинге, задай его и в переменных Bothost: "
                f"SELLER_CONTACT = {SELLER_CONTACT}")
        return (f"Готово, мой контакт: {SELLER_CONTACT}\n\n" + (
            f"Кнопка «❓ Поддержка» у покупателей открывает: {url}" if url else
            "⚠️ Это не ссылка — кнопка «❓ Поддержка» не откроет чат с тобой. Пришли ссылку на свой профиль MAX "
            "(https://max.ru/…) или @ник.") + keep), "cltar"
    if action == "clpayset":
        GCFG["pay_info"] = text.strip()[:500]
        save_cfg()
        for cid in list(CLIENTS):
            with contextlib.suppress(Exception):
                write_subscription(cid)
        return "Реквизиты сохранены ✅ Клиенты увидят их при оплате.", "cltar"
    return None


def locked_answer(cb):
    """Кнопка в боте без подписки: оплатить можно прямо здесь, остальное — «как продлить»."""
    text = lock_text(LOCKED)
    pay_rows = [[B("💳 Тариф и оплата", "m:pay")]] if LOCKED == "expired" else []
    pl = cb.get("payload") or ""
    who = (cb.get("user") or {}).get("user_id")
    if not is_admin(who) or LOCKED != "expired":
        return text, pay_rows
    try:
        if pl.startswith("lg:"):
            return legal_callback(pl[3:], who)
        if pl in ("m:legal", "m:privacy", "m:terms", "m:deldata"):
            return screen(pl[2:], who)
        if pl.startswith("py:"):
            return pay_choice(pl[3:], who)
        if pl.startswith("pd:"):
            return pay_done(pl[3:], who)
        if pl.startswith("pk:"):
            return pay_online(pl[3:], who)
        if pl == "m:pay":
            return pay_screen()
    except (ValueError, IndexError):
        pass
    return text, pay_rows


def locked_loop():
    """Бот клиента без подписки: ничего не публикует, на любое сообщение — как продлить."""
    text = lock_text(LOCKED)
    pay_rows = [[B("💳 Тариф и оплата", "m:pay")]] if LOCKED == "expired" else []
    print("Подписка не активна —", "выключен продавцом" if LOCKED == "off" else "срок кончился",
          "· бот отвечает только «как продлить»")
    drop_webhooks()
    told, marker = {}, None
    while True:
        try:
            params = {"timeout": 25, "limit": 100, "types": "message_created,message_callback,bot_started"}
            if marker:
                params["marker"] = marker
            r = S.get(f"{MAX_API}/updates", headers=HEADERS, params=params, timeout=45)
            if r.status_code != 200:
                time.sleep(10)
                continue
            data = r.json()
            marker = data.get("marker", marker)
            for u in data.get("updates", []):
                kind = u.get("update_type")
                if kind == "message_callback":
                    cb = u.get("callback") or {}
                    api_answer(cb.get("callback_id"), *locked_answer(cb))
                    continue
                msg = u.get("message") or {}
                rec = msg.get("recipient") or {}
                chat = u.get("chat_id") if kind == "bot_started" else (
                    rec.get("chat_id") if rec.get("chat_type") == "dialog" else None)
                if chat and time.time() - told.get(chat, 0) > 600:
                    told[chat] = time.time()
                    api_send(chat, text, pay_rows)
        except Exception as e:
            _log_line(time.strftime("%H:%M:%S") + f" locked: {e}")
            time.sleep(10)


# ---------- ДИАГНОСТИКА ----------
TG_CLIENT = None
MAIN_LOOP = None


def scope_themes():
    """Темы, которые видит пользователь в проверках: в host-режиме — только его кабинет."""
    if HOST_MODE:
        return tenant_themes(cur().owner) if cur().owner else []
    return list(THEMES)


def diag_max(themes=None):
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
    for t in (themes if themes is not None else THEMES):
        if not t.chat_id:
            out.append((False, f"Тема «{t.name}»: канал MAX не привязан (добавь бота админом канала)"))
            continue
        try:
            r = S.get(f"{MAX_API}/chats/{t.chat_id}", headers=HEADERS, timeout=20)
            ok = r.status_code == 200
            rights = bot_rights(t.chat_id) if ok else None
            if rights is False:
                out.append((False, f"Тема «{t.name}»: бот НЕ админ канала MAX — добавь его админом "
                                   "с правом публиковать"))
            else:
                out.append((ok, f"Тема «{t.name}»: канал MAX " + (
                    ("доступен, бот — админ" if rights else "доступен") if ok else
                    f"недоступен ({r.status_code}) — бот должен быть админом канала")))
        except Exception as e:
            out.append((False, f"Тема «{t.name}»: канал MAX не проверить ({type(e).__name__})"))
    n = len(CFG["admins"])
    out.append((n > 0, f"Админов бота: {n}" + ("" if n else " — отправь боту в MAX код привязки из консоли")))
    return out


async def diag_telegram(client, themes=None):
    out = []
    web = is_web_client(client)
    if web:
        out.append((True, "Telegram: режим без входа (читаю через t.me)"))
    elif SHARED["on"]:
        out.append((True, "Telegram: читаю через общий аккаунт (бот продавца)"))
    else:
        await reconnect_tg(client)                       # связь могла оборваться — сначала переподключаемся
        try:
            ok = await client.is_user_authorized()
            conn = getattr(getattr(client, "cur", client), "is_connected", lambda: True)()
            out.append((bool(ok) and conn, "Telegram: вход " + ("выполнен" if ok else "НЕ выполнен")
                        + ("" if conn else ", но нет связи с Telegram — переподключаюсь сам")))
        except Exception as e:
            out.append((False, f"Telegram: нет связи ({type(e).__name__})"))
        try:                                             # видео и файлы больших каналов идут через CDN
            out.append(await asyncio.wait_for(cdn_check(client), 25))
        except asyncio.TimeoutError:
            out.append((False, "CDN Telegram: не ответил за 25 с — видео и файлы больших каналов могут ждать"))
    per_theme = []
    for t in list(themes if themes is not None else THEMES):
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
                    if is_net_error(e) and web:
                        out.append((False, f"@{ch}: t.me не отвечает с этого сервера ({type(e).__name__}). "
                                           f"Нужен сервер за границей или прокси TG_PROXY"))
                    elif is_net_error(e):
                        out.append((False, f"@{ch}: нет связи с Telegram ({str(e)[:80] or type(e).__name__}) — "
                                           "переподключаюсь сам. Если повторяется: 🔑 Telegram → подключи заново"))
                    else:
                        out.append((False, f"@{ch}: не читается ({type(e).__name__}: {str(e)[:60]})"))
            pend = sum(1 for x in QUEUE if x.get("status") == "pending")
            out.append((True, f"Тема «{t.name}»: в очереди {len(QUEUE)}, ждут одобрения {pend}"))
    return out


def pipeline_status(t, now=None):
    """Где застревают посты темы: подписка, пауза, ждут ✅, «Что делать?», очередь, реклама, последний пост."""
    now = now or time.time()
    with theme_ctx(t):
        if not theme_live(t):
            return [(False, f"«{t.name}»: " + ("доступ приостановлен" if tenant_mode(t.owner) == "off" else
                                               "на бесплатном тарифе работает только первый канал MAX, этот на паузе")
                     + " — каналы не читаются, посты не выходят")]
        out = [(False, f"«{t.name}»: на паузе — посты не выходят (кнопка «▶️ Продолжить»)")] if CFG["paused"] else []
        by = {k: [x for x in QUEUE if x.get("status", "ok") == k] for k in ("pending", "ok")}
        if by["pending"]:
            oldest = max(now - x.get("enq", now) for x in by["pending"])
            out.append((True, f"«{t.name}»: ждут твоего ✅: {len(by['pending'])} "
                              f"(самый старый — {fmt_min(int(oldest // 60))} назад)"))
        if by["ok"]:
            out.append((True, f"«{t.name}»: в очереди на выход: {len(by['ok'])}, следующий — {next_pub_text()}"))
        held = Counter(x.get("ch") for x in ADHELD if now - x.get("enq", now) < 86400)
        if held:
            out.append((True, f"«{t.name}»: отсеяно как реклама за сутки: {sum(held.values())} ("
                              + ", ".join(f"@{c} — {n}" for c, n in held.most_common(3))
                              + "). Ошибка? «🧹 Реклама» → «🧠 Учить фильтр»"))
        last = max((p.get("t", 0) for p in PUB.get("day_posts", [])), default=0)
        out.append((True, f"«{t.name}»: последний пост вышел "
                          + (f"{fmt_min(int((now - last) // 60))} назад" if last else "— пока не было")))
    return out


def run_diag():
    """Полная проверка для кнопки/команды /check (вызывается из потока меню)."""
    themes = scope_themes()
    out = diag_max(themes)
    for t in themes:                                     # где застревают посты — по каждой теме
        out += pipeline_status(t)
    if MAIN_LOOP is not None and TG_CLIENT is not None:
        try:
            out += asyncio.run_coroutine_threadsafe(diag_telegram(TG_CLIENT, themes), MAIN_LOOP).result(40)
        except Exception as e:
            out.append((False, f"Telegram: проверка не удалась ({type(e).__name__})"))
    bad = sum(1 for ok, _ in out if not ok)
    head = "🔎 Проверка: всё в порядке ✅" if not bad else f"🔎 Проверка: проблем — {bad}"
    return (head + "\n\n" + "\n".join(("✅ " if ok else "❌ ") + t for ok, t in out)
            + f"\n\nВерсия бота: {BOT_VERSION}")


async def check_cli():
    """python tg_to_max.py --check: проверка без запуска бота."""
    use_web = not TG_READY or os.path.exists(WEB_FLAG) or not os.path.exists("session.session")
    client = WebClient() if use_web else tg_client("session", API_ID, API_HASH)
    try:
        await client.connect()
    except Exception as e:
        print("Telegram: не удалось подключиться:", e)
    out = [telethon_version_note()] + await asyncio.to_thread(diag_max) + await diag_telegram(client)
    print("\n".join(("✅ " if ok else "❌ ") + t for ok, t in out))


async def main():
    global WAKE, TG_CLIENT, MAIN_LOOP, ON_NEW
    if CLIENT_MODE:
        watch_parent()
    elif CLIENTS:
        import signal
        with contextlib.suppress(Exception):
            signal.signal(signal.SIGTERM, stop_all_clients)
    if CLIENT_MODE and LOCKED:
        await asyncio.to_thread(locked_loop)
        return
    from telethon import events
    ok = False
    client = WebClient()
    if TG_READY:
        client = tg_client("session", API_ID, API_HASH)
        try:
            await client.connect()
            ok = await client.is_user_authorized()
        except Exception as e:
            print("Telegram: не удалось подключиться:", e)
    if not ok and CLIENT_MODE:
        sc = await shared_client()
        if sc is not None:
            client, ok = sc, True
    if not ok:
        if LOGIN:
            if os.path.exists(WEB_FLAG):
                os.remove(WEB_FLAG)
            client = await telegram_login(client)
            if not isinstance(client, WebClient) and os.path.exists(WEB_FLAG):
                os.remove(WEB_FLAG)
        else:
            if TG_READY and os.path.exists("session.session") and not CLIENT_MODE:   # вход был, но Telegram его отозвал
                with contextlib.suppress(OSError):
                    os.remove("session.session")
                await asyncio.to_thread(owner_alert, "🔑 Вход в Telegram больше не действует (Telegram завершил сеанс, "
                                        "пока бот был выключен). Читаю открытые каналы через t.me.\n\n"
                                        "Вернуть: 🔑 Telegram → «Подключить аккаунт».")
            print("Режим без входа в Telegram: читаю публичные каналы через t.me.")
            print("Подключить аккаунт Telegram: в MAX меню бота → «🔑 Telegram» (или запусти login.py).")
            client = WebClient()
    print("Telegram: читаю каналы без входа (t.me)" if isinstance(client, WebClient) else "Telegram: вход выполнен")
    client = ClientBox(client)
    TG_CLIENT, MAIN_LOOP = client, asyncio.get_running_loop()
    try:
        await POOL.start()                               # 👤 аккаунты пула из sessions/
        if POOL.status():
            print(f"👤 Пул Telegram: аккаунтов {len(POOL.status())}, работают {len(POOL.usable())}")
    except Exception:
        log_exc("пул аккаунтов Telegram")
    if not is_web_client(client) and not CLIENT_MODE:
        asyncio.create_task(cdn_check_report(client))   # CDN с этого сервера: не работает — хозяин узнает сразу
    MAIN_LOOP.set_exception_handler(lambda loop, ctx: _log_line(
        "ОШИБКА в фоновой задаче: " + str(ctx.get("message")) + " " + repr(ctx.get("exception"))))
    await asyncio.to_thread(drop_webhooks)               # вебхук на токене глушит бота — снимаем до всего остального
    main_t = THEMES[0] if THEMES else None
    if main_t is not None and not HOST_MODE:             # одиночный режим: привязываем свой канал MAX
        env_chat = CONFIG.get("max_chat_id") if os.environ.get("MAX_CHAT_ID") else None
        if env_chat and main_t.chat_id != env_chat:      # переменная MAX_CHAT_ID — явное указание, какой канал
            main_t.chat_id = env_chat
            await asyncio.to_thread(_save_channel_id, env_chat)
            save_cfg()
        if not main_t.chat_id:
            main_t.chat_id = MAX_CHAT_ID or await asyncio.to_thread(find_chat_id_safe)
            if main_t.chat_id:
                await asyncio.to_thread(_save_channel_id, main_t.chat_id)
                save_cfg()
            else:
                BIND["theme"] = main_t.id                # привяжем по первому сообщению/добавлению в канал
                print("⚠️ Канал MAX по ссылке не нашёлся. Добавь бота АДМИНОМ своего канала MAX и напиши в канале "
                      "любое сообщение — бот привяжет канал сам. Или задай MAX_CHAT_ID.")
    if HOST_MODE:
        host_adopt_orphans()
        host_clean_chats()
        with contextlib.suppress(Exception):
            await asyncio.to_thread(clean_bot_names)   # спрашивает MAX (GET /me) — не в цикле
        print(f"HOST-режим: один бот для всех. Кабинетов: {len(TENANTS)}. Хозяин (OWNER_ID): {HOST_OWNER or '— не задан!'}")
        print(storage_note())
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
    same = [c.get("name") for c in CLIENTS.values() if (c.get("token") or "").strip() == (MAX_TOKEN or "").strip()]
    if same:
        print(f"Клиенты: бот «{', '.join(same)}» на том же токене, что и этот бот, — не запускаю (иначе перехватывал бы кнопки).")
    if not CLIENT_MODE:
        with contextlib.suppress(Exception):
            hub_start()                                  # чтение каналов для ботов клиентов
        threading.Thread(target=supervise_loop, daemon=True).start()   # боты клиентов (продажа)
    asyncio.create_task(background_loop())              # чистка карточек, отчёт
    try:
        trend_start()                                    # 🔥 тренды: свой поток, раз в 2–3 часа на канал
    except Exception:
        log_exc("запуск трендов")
    tasks, fails, sem = {}, {}, asyncio.Semaphore(5)
    last_full, woke, chan_try = 0.0, set(), time.time()
    while True:
      try:
        if not HOST_MODE and THEMES and not THEMES[0].chat_id and time.time() - chan_try > 300:  # канал не привязан — ищем
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
            if not theme_live(t):
                continue                                 # кабинет выключен или тема сверх бесплатного тарифа — не читаем
            try:                                         # сбой одной темы не роняет чтение остальных
                with theme_ctx(t):
                    await sync_channels(client)
                    if t.flags["test"]:
                        t.flags["test"] = False
                        await run_test(client)
                    for ch in live_channels():
                        if is_social(ch) and not feature_on("social", t.owner):
                            continue                     # соцсети выключены для этого кабинета
                        names.add(ch.lower())
                        if ch in t.last_ids and not net_blocked(ch) and poll_due(t, ch, full, woke):
                            jobs.append((t, ch))
            except Exception:
                log_exc(f"чтение темы «{t.name}»")
        WATCH.clear()
        WATCH.update(names)
        if full:
            last_full = time.time()
        woke = set()
        # все каналы всех тем читаются одновременно, а не по очереди
        try:
            await asyncio.gather(*(poll(client, t, ch, fails, sem) for t, ch in jobs))
        except Exception:
            log_exc("чтение каналов")
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
      except Exception:
        log_exc("главный цикл чтения")                   # ничто не должно останавливать поиск постов
        await asyncio.sleep(3)



class _Facade(types.ModuleType):
    """Для тестов: bot.X = подделка ставится и в модуль reloader, где X теперь живёт, — подделку видят все."""
    def __setattr__(self, name, value):
        for m in _MOVED:
            if name in vars(m):
                setattr(m, name, value)
        super().__setattr__(name, value)


_MOVED = [_log, _buttons, _timeutil, _files]                          # модули, куда уже переехал код из этого файла
_me = next((o for o in __import__("gc").get_referrers(globals())
            if isinstance(o, types.ModuleType) and vars(o) is globals()), None)
if _me is not None:
    _me.__class__ = _Facade

if __name__ == "__main__":
    if "--check" in sys.argv:
        asyncio.run(check_cli())
    else:
        asyncio.run(main())
