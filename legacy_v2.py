"""
Репост из нескольких Telegram-каналов в канал MAX.
Управление — через меню прямо в MAX (напиши своему боту /start) или командами через / (/help).
Есть тайник с мемами: когда очередь пуста, бот выкладывает пост оттуда.

Установка:   pip install telethon requests
"""
import asyncio
import copy
import hashlib
import json
import math
import os
import random
import re
import threading
import time
from difflib import SequenceMatcher

import requests
import urllib3
from telethon import TelegramClient

# ================== НАСТРОЙКИ (редко меняются) ==================
API_ID = 34290430                      # с my.telegram.org
API_HASH = "9bcf8caa4657f63718da4c5045db8786"
MAX_TOKEN = "f9LHodD0cOJxo9mXOyiyQiqqmt495B5gWWoKXjmbAHTcbaF5pJ6mq1FAA2sSzonfnAf8OnVUqPZWyWJFp4Xk"
PHONE = "+79606515573"
MAX_CHAT_ID = None                     # если знаешь id канала — впиши числом, иначе найдётся сам
MAX_CHANNEL_LINK = "https://max.ru/channel_roblloxxx"

# Поставь True на один запуск, чтобы бот сразу переопубликовал ПОСЛЕДНИЙ пост
# каждого канала (проверка). Потом верни False, иначе будет дублировать.
TEST_REPOST_LAST = False

STATE_FILE = "last_posts.json"         # последний id поста по каждому каналу
HISTORY_FILE = "posted_history.json"   # что уже опубликовано (для дублей)
SETTINGS_FILE = "settings.json"        # настройки из меню
QUEUE_FILE = "queue.json"              # очередь постов на публикацию
PUB_FILE = "pub_state.json"            # когда и что публиковали (для расписания)
MEDIA_DIR = "queue_media"              # фото постов, ждущих в очереди
STASH_FILE = "stash.json"              # тайник: мемы про запас
STASH_DIR = "stash_media"              # файлы из тайника
CARDS_FILE = "cards.json"              # карточки одобрения в чатах админов (чтобы их чистить)

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

MAX_API = "https://platform-api2.max.ru"   # новый домен MAX
HEADERS = {"Authorization": MAX_TOKEN}

# MAX перешёл на российский сертификат, которого нет в Pydroid/Python,
# поэтому проверку сертификата отключаем.
VERIFY_SSL = False
if not VERIFY_SSL:
    urllib3.disable_warnings()
S = requests.Session()
S.verify = VERIFY_SSL


# ---------- файлы состояния и настройки ----------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


CFG = copy.deepcopy(DEFAULTS)
CFG.update(load_json(SETTINGS_FILE, {}))
# перенос владельца из старой версии настроек
if CFG.get("owner_id") and CFG["owner_id"] not in CFG["admins"]:
    CFG["admins"].insert(0, CFG["owner_id"])
    if CFG.get("owner_chat"):
        CFG["admin_chats"][str(CFG["owner_id"])] = CFG["owner_chat"]
STATS = {"published": 0, "dups": 0, "errors": 0, "expired": 0, "blocked": 0,
         "rejected": 0, "urgent": 0, "stash": 0}
QUEUE = load_json(QUEUE_FILE, [])      # посты, ждущие публикации
PUB = {"last_pub": 0, "ch_last_date": {}, "ch_last_pub": {}, "series": {}}
PUB.update(load_json(PUB_FILE, {}))
STATS.update(PUB.get("stats", {}))
FORCE = {"until": 0}                   # до этого времени публикуем без ожидания
Q_LOCK = asyncio.Lock()
LAST_IDS = {}          # последний обработанный пост по каждому каналу
BAD = set()            # каналы, которые не открылись
STASH = load_json(STASH_FILE, [])      # тайник: [{id, text, photos, videos, ...}]
STASH_LOCK = threading.Lock()
FLAGS = {"stash_now": False}           # запросы из меню/команд для планировщика
CARDS = load_json(CARDS_FILE, [])      # [{chat, mid, ref, del_at, t}]
CARDS_LOCK = threading.Lock()


def save_cfg():
    save_json(SETTINGS_FILE, CFG)


def save_queue():
    save_json(QUEUE_FILE, QUEUE)


def save_pub():
    PUB["stats"] = dict(STATS)
    save_json(PUB_FILE, PUB)


def save_stash():
    with STASH_LOCK:
        save_json(STASH_FILE, STASH)


def bump(key):
    """Счётчик для статуса и отчёта (хранится на диске)."""
    STATS[key] = STATS.get(key, 0) + 1
    save_pub()


def signature():
    if not CFG["signature_on"] or not CFG["signature_text"].strip():
        return ""
    return f"[{CFG['signature_text']}]({MAX_CHANNEL_LINK})"


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


def terms(text: str):
    """Смысловые слова поста: без мусора, с грубой обрезкой окончаний."""
    out = set()
    for w in re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е")):
        if w in STOP_WORDS or (len(w) < 2 and not w.isdigit()):
            continue
        if "а" <= w[0] <= "я":
            w = w[:5]              # "хэллоуинском" и "хэллоуинскую" -> "хэллоу"
        out.add(w)
    return out


def meaning_match(a: str, b: str, history_norms) -> bool:
    """Два поста про одно и то же? Сравниваем веса общих редких слов."""
    ta, tb = terms(a), terms(b)
    if len(ta) < 3 or len(tb) < 3:
        return False
    docs = [terms(h) for h in history_norms] + [ta, tb]
    n = len(docs)

    def weight(w):
        df = sum(1 for d in docs if w in d)
        idf = math.log((n + 1) / (df + 0.5)) + 1
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
    for h in history:
        if entry["fwd"] and set(entry["fwd"]) & set(h.get("fwd", [])):
            return h                     # один и тот же пост переслан из одного оригинала
        # посты почти без текста сравниваем по самой картинке/видео
        if len(entry["norm"]) < 25 and entry["img"] and set(entry["img"]) & set(h.get("img", [])):
            return h
        # "alts" — как другие каналы пересказывали тот же пост (бот их запоминает)
        for text in [h.get("norm", "")] + h.get("alts", []):
            if similar(entry["norm"], text) or meaning_match(entry["norm"], text, norms):
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


def find_chat_id():
    """Определяем id канала MAX: из файла, по ссылке или через /updates."""
    if os.path.exists("channel_id.txt"):
        return int(open("channel_id.txt").read().strip())

    me = S.get(f"{MAX_API}/me", headers=HEADERS, timeout=30)
    print("Проверка токена /me ->", me.status_code, me.text[:150])

    slug = MAX_CHANNEL_LINK.rstrip("/").split("/")[-1]
    for cand in (slug, "@" + slug):
        r = S.get(f"{MAX_API}/chats/{cand}", headers=HEADERS, timeout=30)
        print("Поиск по ссылке", cand, "->", r.status_code, r.text[:150])
        if r.status_code == 200 and "chat_id" in r.json():
            cid = r.json()["chat_id"]
            open("channel_id.txt", "w").write(str(cid))
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
            open("channel_id.txt", "w").write(str(cid))
            return cid
    raise RuntimeError("Не удалось определить chat_id. Пришли мне вывод выше.")


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
    """У видео токен выдаётся сразу вместе со ссылкой для загрузки."""
    r = S.post(f"{MAX_API}/uploads", params={"type": "video"},
               headers=HEADERS, timeout=30)
    r.raise_for_status()
    info = r.json()
    url, name = info["url"], os.path.basename(path)

    # способ 1: multipart с именем файла и типом video/mp4 (как curl -F data=@файл)
    with open(path, "rb") as f:
        up = S.post(url, files={"data": (name, f, "video/mp4")}, timeout=900)
    if up.status_code < 300:
        return info["token"]
    print("  multipart не прошёл:", up.status_code, up.text[:150])

    # способ 2: загрузка файла целиком одним куском (resumable-вариант MAX)
    size = os.path.getsize(path)
    headers = {
        "Content-Disposition": f'attachment; filename="{name}"',
        "Content-Type": "application/octet-stream",
        "Content-Range": f"bytes 0-{size - 1}/{size}",
        "Connection": "keep-alive",
    }
    with open(path, "rb") as f:
        up2 = S.post(url, data=f, headers=headers, timeout=900)
    if up2.status_code >= 300:
        raise RuntimeError(f"Загрузка видео не удалась: {up2.status_code} {up2.text[:150]}")
    return info["token"]


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
            return
        if "not.ready" in r.text or "not.processed" in r.text:
            time.sleep(3)
            continue
        raise RuntimeError(f"MAX ошибка {r.status_code}: {r.text}")
    raise RuntimeError("Вложение так и не стало готово")


def build_parts(text, images, videos):
    """Текст уходит с первым вложением; фото — одним сообщением, видео — по одному."""
    parts = []
    if images:
        parts.append((text, images, None))
        text = ""
    for v in videos:
        parts.append((text, [], v))
        text = ""
    if not parts:
        parts.append((text, [], None))
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


def make_report():
    base = PUB.get("rep_base", {})

    def d(k):
        return STATS.get(k, 0) - base.get(k, 0)
    pend = sum(1 for x in QUEUE if x.get("status") == "pending")
    return ("📈 Отчёт\n\n"
            f"Опубликовано: {d('published')} (срочных: {d('urgent')}, из тайника: {d('stash')})\n"
            f"Дублей пропущено: {d('dups')}\n"
            f"Отсеяно стоп-словами: {d('blocked')}\n"
            f"Отклонено вручную: {d('rejected')}\n"
            f"Ошибок: {d('errors')}\n\n"
            f"Сейчас в очереди: {len(QUEUE)} (ждут одобрения: {pend}) · в тайнике: {len(STASH)}")


def send_report():
    notify(make_report())
    PUB["rep_base"] = dict(STATS)
    PUB["last_report"] = time.strftime("%Y-%m-%d", time.localtime(time.time()))
    save_pub()


# ---------- МЕНЮ В MAX ----------
PENDING = {}           # user_id -> действие, для которого ждём текст
INVITES = {}           # код приглашения админа -> время окончания
LAST_INVITE = {"code": None}
TEST_FLAG = {"on": False}   # запрос «тест-репост» из меню
CLAIM_CODE = None


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
    head = f"✋ Новый пост из @{it['ch']}" + (" ⚡ срочный" if it.get("urgent") else "")
    return f"{head}\n({', '.join(media) or 'только текст'})\n\n{text}"


def send_preview(it, rows=None):
    """Присылает админам пост на одобрение (с кнопками rows)."""
    msg = preview_text(it)
    token = None
    if it.get("photos") and os.path.exists(it["photos"][0]):
        try:
            token = upload_image(it["photos"][0])
        except Exception as e:
            print("Предпросмотр: фото не загрузилось:", e)
    for chat in set(CFG["admin_chats"].values()):
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
    gid = f"g{int(time.time() * 1000)}"
    for it in items:
        it["grp"] = gid
    save_queue()
    lines = []
    for i, it in enumerate(items):
        send_preview(it, [[B("❌ Убрать этот", f"ap:no:{it['id']}")]])
        snip = (it.get("text") or "(без текста)")[:50].replace("\n", " ")
        lines.append(f"{i + 1}. {snip}")
    text = (f"📦 Пачка из @{items[0]['ch']}: постов {len(items)}\n\n" + "\n".join(lines)
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


def notify(text):
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
            mark = ("✋" if it.get("status") == "pending" else "") + ("⚡" if it.get("urgent") else "")
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
    elif name == "hp":
        text = help_text() + "\n\nМожно просто скинуть боту фото/видео/пересланное — улетит в тайник."
        rows = [back]
    else:
        text = ("⚙️ Меню бота\n\n"
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
                [B("▶️ Продолжить" if CFG["paused"] else "⏸ Пауза", "a:pause")]]
    return text, rows


PROMPTS = {
    "chadd": "Пришли username канала: @name или ссылку t.me/name",
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


def apply_text(action, text):
    """Применяет присланный текст. Возвращает (ответ, экран)."""
    if action == "chadd":
        m = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{4,32})\s*$", text.strip())
        if not m:
            return "Не похоже на username канала. Попробуй ещё раз.", "ch"
        name = m.group(1)
        if name.lower() in [c.lower() for c in CFG["channels"]]:
            return f"@{name} уже в списке.", "ch"
        CFG["channels"].append(name)
        BAD.discard(name)
        save_cfg()
        return f"Добавил @{name}. Старые посты не публикую, только новые.", "ch"
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
        TEST_FLAG["on"] = True
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
        if x.get("status", "ok") != "pending" and x.get("at", 0) > t:
            t = x["at"]
    return t


def approve(rest, uid, chat=None):
    """Кнопки на карточках одобрения. Возвращает (текст, кнопки) на замену карточки.
    now/later/no — один пост, gnow/glater/gno — вся пачка, ok — просто в очередь по расписанию."""
    verdict, _, ref = rest.partition(":")
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
        tail = " Все вместе." if len(items) > 1 else ""
        return done(f"⏰ {who}: {many} из {names} — выйдет в {when} "
                    f"(через {fmt_min(round((at - now) / 60))}).{tail}")
    return done(f"✅ {who}: {many} из {names} — в очереди, выйдет по расписанию.")


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
    ("report", "Прислать отчёт"),
    ("help", "Список команд"),
]


def help_text():
    return "❓ Команды\n\n" + "\n".join(f"/{n} — {d}" for n, d in COMMAND_HELP)


def register_commands():
    """Показывает команды в подсказке MAX (если API не принял — не страшно, команды работают и так)."""
    try:
        r = S.patch(f"{MAX_API}/me", headers=HEADERS, timeout=30,
                    json={"commands": [{"name": n, "description": d[:100]} for n, d in COMMAND_HELP]})
        print("Команды бота ->", r.status_code, r.text[:100])
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
        ok = [x for x in QUEUE if x.get("status", "ok") != "pending"]
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
    elif cmd == "report":
        say(make_report())
    else:
        show("hp")
    return True


def add_admin(uid, chat_id, name):
    if uid not in CFG["admins"]:
        CFG["admins"].append(uid)
    CFG["admin_names"][str(uid)] = name or str(uid)
    CFG["admin_chats"][str(uid)] = chat_id
    save_cfg()


def handle_message(uid, chat_id, name, text, msg=None):
    global CLAIM_CODE
    t = (text or "").strip()
    if not is_admin(uid):
        if not CFG["admins"] and CLAIM_CODE and t == CLAIM_CODE:      # первый вход: владелец
            add_admin(uid, chat_id, name)
            CLAIM_CODE = None
            print("Меню: владелец привязан, user_id =", uid)
            api_send(chat_id, "Готово, ты владелец бота ✅")
            m, rows = screen("main", uid)
            api_send(chat_id, m, rows)
        elif t in INVITES and INVITES[t] > time.time():                # приглашение от владельца
            del INVITES[t]
            add_admin(uid, chat_id, name)
            print("Меню: добавлен админ", uid, name)
            api_send(chat_id, "Готово, ты теперь админ бота ✅")
            m, rows = screen("main", uid)
            api_send(chat_id, m, rows)
            notify(f"👥 Новый админ: {name or uid}")
        elif not CFG["admins"]:
            api_send(chat_id, "Привет! Чтобы управлять ботом, отправь код привязки, "
                              "который показан в консоли при запуске скрипта.")
        else:
            api_send(chat_id, "Нет доступа. Попроси владельца прислать код приглашения.")
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
    PENDING.pop(uid, None)
    if action and t:
        answer, scr = apply_text(action, t)
        api_send(chat_id, answer)
        m, rows = screen(scr, uid)
        api_send(chat_id, m, rows)
    elif atts:                                             # просто скинул фото/видео — в тайник
        _, info = stash_add(uid, content, atts)
        api_send(chat_id, info + "\n(без команды — сохранил в тайник)", [[B("🗃 Тайник", "m:sh")]])
    else:
        m, rows = screen("main", uid)
        api_send(chat_id, m, rows)


def handle_update(u):
    kind = u.get("update_type")
    if kind == "message_callback":
        cb = dict(u.get("callback") or {})
        cb["_chat"] = ((u.get("message") or {}).get("recipient") or {}).get("chat_id")
        if is_admin((cb.get("user") or {}).get("user_id")):
            handle_callback(cb)
    elif kind == "message_created":
        msg = u.get("message") or {}
        rec = msg.get("recipient") or {}
        if rec.get("chat_type") != "dialog":          # посты в каналах игнорируем
            return
        sender = msg.get("sender") or {}
        name = sender.get("first_name") or sender.get("name") or ""
        handle_message(sender.get("user_id"), rec.get("chat_id"), name,
                       (msg.get("body") or {}).get("text") or "", msg)
    elif kind == "bot_started":
        user = u.get("user") or {}
        handle_message(user.get("user_id"), u.get("chat_id"),
                       user.get("first_name") or user.get("name") or "", "/start")


def menu_loop():
    """Слушает события бота MAX (кнопки и сообщения) — работает в отдельном потоке."""
    global CLAIM_CODE
    if not CFG["admins"]:
        CLAIM_CODE = str(random.randint(100000, 999999))
        print("=" * 40)
        print("МЕНЮ: открой своего бота в MAX, нажми «Старт»")
        print("и отправь ему этот код:", CLAIM_CODE)
        print("=" * 40)
    else:
        print("Меню готово: напиши своему боту в MAX /start")
    register_commands()
    marker = None
    while True:
        try:
            params = {"timeout": 25, "limit": 100,
                      "types": "message_created,message_callback,bot_started"}
            if marker:
                params["marker"] = marker
            r = S.get(f"{MAX_API}/updates", headers=HEADERS, params=params, timeout=45)
            if r.status_code != 200:
                time.sleep(5)
                continue
            data = r.json()
            marker = data.get("marker", marker)
            for u in data.get("updates", []):
                try:
                    handle_update(u)
                except Exception as e:
                    print("Меню: ошибка обработки:", e)
        except Exception as e:
            print("Меню: сбой связи:", e)
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


async def download_video(client, ch, vid):
    """Качает видео поста из Telegram. Возвращает (путь, None) или (None, причина)."""
    os.makedirs(MEDIA_DIR, exist_ok=True)
    dest = f"{MEDIA_DIR}/v_{ch}_{vid}.mp4"
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


async def publish_item(client, chat_id, it):
    """Возвращает 'ok', 'retry' или 'dropped'. Уже отправленные части не шлёт повторно."""
    try:
        text = (it["text"] + "\n\n" + signature()).strip()
        photos = [p for p in it.get("photos", []) if os.path.exists(p)]
        parts = []
        if photos:
            parts.append(("img", text, None))
            text = ""
        if CFG["video_on"]:
            for vid in it.get("vids", []):
                parts.append(("vid", text, vid))
                text = ""
        if not parts:
            parts.append(("txt", text, None))
        for i, (kind, t, vid) in enumerate(parts):
            if i < it.get("sent", 0):
                continue                           # эта часть уже ушла в прошлую попытку
            if kind == "img":
                await asyncio.to_thread(send_max, chat_id, t, photos, None)
            elif kind == "vid":
                print(f"[{it['ch']}] качаю видео поста {vid}...")
                path, verr = await download_video(client, it["ch"], vid)
                if not path:
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
                await asyncio.to_thread(send_max, chat_id, t, [], None)
            it["sent"] = i + 1
            save_queue()
        bump("published")
        print(f"[{it['ch']}] опубликован пост {it['id']}")
        return "ok"
    except Exception as e:
        it["fails"] = it.get("fails", 0) + 1
        bump("errors")
        print(f"[{it['ch']}] ошибка публикации {it['id']} (попытка {it['fails']}):", e)
        if it["fails"] >= 3:
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
    if any(x.get("status", "ok") != "pending" for x in QUEUE):
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
    ready = [x for x in QUEUE if x.get("status", "ok") != "pending"
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
        if res == "dropped":
            continue
        ch = it["ch"]
        if new_series and ch not in seen:
            PUB["series"][ch] = 1
        else:
            PUB["series"][ch] = PUB["series"].get(ch, 0) + 1
        seen.add(ch)
        mark_published(it)


async def scheduler(client, chat_id):
    """Раз в несколько секунд проверяет, не пора ли что-то публиковать."""
    while True:
        await asyncio.sleep(5)
        try:
            await asyncio.to_thread(purge_cards)
        except Exception as e:
            print("Карточки: ошибка:", e)
        if CFG["paused"]:
            continue
        try:
            async with Q_LOCK:
                if FLAGS["stash_now"]:
                    FLAGS["stash_now"] = False
                    if not await publish_stash(chat_id):
                        await asyncio.to_thread(notify, "🗃 Из тайника не вышло (пусто или ошибка) — смотри консоль.")
                await publish_due(client, chat_id)
            today = time.strftime("%Y-%m-%d", time.localtime(time.time()))
            if CFG["report_on"] and PUB.get("last_report") != today and local_hour() >= CFG["report_hour"]:
                await asyncio.to_thread(send_report)
        except Exception as e:
            print("Планировщик: ошибка:", e)


# ---------- ЧТЕНИЕ КАНАЛОВ ----------
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


async def process_channel(client, ch, history, fails):
    """Новые посты канала. Карточки одобрения уходят ПАЧКОЙ после обхода (см. send_previews)."""
    fresh = []
    try:
        await _process_channel(client, ch, history, fails, fresh)
    finally:
        if fresh:
            await asyncio.to_thread(send_previews, fresh)


async def _process_channel(client, ch, history, fails, fresh):
    """Читает новые посты канала, чистит и ставит в очередь на публикацию."""
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

    os.makedirs(MEDIA_DIR, exist_ok=True)
    for key in order:
        if ch not in LAST_IDS or CFG["paused"]:
            return
        grp = groups[key]
        last = grp[-1].id
        raw = next((m.text for m in grp if m.text), "")
        body = clean_text(raw, with_signature=False)
        img_paths, hashes = [], []
        queued = False
        try:
            # фото: скачиваем сразу (они лёгкие), считаем отпечаток для дублей
            for m in grp:
                if m.photo:
                    p = await m.download_media(file=f"{MEDIA_DIR}/{ch}_{m.id}.jpg")
                    img_paths.append(p)
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
                print(f"[{ch}] пост {last}: пусто после чистки — пропускаю")
            elif (bw := blocked_word(body)) is not None:
                bump("blocked")
                print(f"[{ch}] пост {last}: стоп-слово «{bw}» — не публикую")
            elif dup is not None:
                # запоминаем и этот пересказ, чтобы ловить следующие похожие
                alts = dup.setdefault("alts", [])
                if entry["norm"] and len(alts) < 6:
                    alts.append(entry["norm"])
                    save_json(HISTORY_FILE, prune(history))
                bump("dups")
                print(f"[{ch}] пост {last}: ДУБЛЬ по смыслу, уже публиковали — пропускаю")
            elif any(vi[0] > CFG["video_mb"] * 1024 * 1024 for _, vi in vids):
                print(f"[{ch}] пост {last}: видео больше {CFG['video_mb']} МБ — пропускаю")
            elif len(QUEUE) >= 100:
                print(f"[{ch}] пост {last}: очередь переполнена (100) — пропускаю")
            else:
                status = "pending" if (CFG["approval_on"] and CFG["admin_chats"]) else "ok"
                item = {"id": f"{ch}:{grp[0].id}", "ch": ch,
                        "date": grp[0].date.timestamp(), "enq": time.time(),
                        "text": body, "photos": img_paths,
                        "vids": [m.id for m, _ in vids], "sent": 0, "fails": 0,
                        "urgent": is_urgent(body), "status": status}
                QUEUE.append(item)
                save_queue()
                history.append(entry)               # сразу помним, чтобы ловить дубли в очереди
                save_json(HISTORY_FILE, prune(history))
                queued = True
                print(f"[{ch}] пост {last} поставлен в очередь (всего {len(QUEUE)})"
                      + (" — жду одобрения" if status == "pending" else ""))
                if status == "pending":
                    fresh.append(item)
        except Exception as e:
            fails[(ch, last)] = fails.get((ch, last), 0) + 1
            bump("errors")
            print(f"[{ch}] ошибка на посте {last} (попытка {fails[(ch, last)]}):", e)
            if fails[(ch, last)] < 3:
                return                    # повторим на следующей проверке
            print(f"[{ch}] пост {last} не получается обработать — пропускаю")
        finally:
            if not queued:
                for p in img_paths:
                    if p and os.path.exists(p):
                        os.remove(p)
        if ch in LAST_IDS:
            LAST_IDS[ch] = max(LAST_IDS[ch], last)
            save_json(STATE_FILE, LAST_IDS)


async def sync_channels(client, saved):
    """Подхватывает каналы, добавленные через меню."""
    for ch in list(CFG["channels"]):
        if ch in LAST_IDS or ch in BAD:
            continue
        try:
            latest = await client.get_messages(ch, limit=1)
        except Exception as e:
            BAD.add(ch)
            print(f"Канал {ch} не открывается:", e)
            await asyncio.to_thread(notify, f"⚠️ Канал @{ch} не открывается. Проверь имя и убери его в меню.")
            continue
        latest_id = latest[0].id if latest else 0
        if TEST_REPOST_LAST:
            LAST_IDS[ch] = max(latest_id - 1, 0)
            print(f"ТЕСТ: [{ch}] переопубликую последний пост {latest_id}")
        elif ch in saved:
            LAST_IDS[ch] = saved[ch]
        else:
            LAST_IDS[ch] = latest_id     # новый канал: старое не постим
    save_json(STATE_FILE, LAST_IDS)


async def run_test(client, history):
    """Тест-репост из меню: берём последний пост каждого канала заново и публикуем сразу."""
    history.clear()
    save_json(HISTORY_FILE, [])
    FORCE["until"] = time.time() + 300
    for ch in list(LAST_IDS):
        try:
            latest = await client.get_messages(ch, limit=1)
            LAST_IDS[ch] = max((latest[0].id if latest else 0) - 1, 0)
        except Exception as e:
            print(f"ТЕСТ: [{ch}] не открылся:", e)
    save_json(STATE_FILE, LAST_IDS)


async def wait_interval():
    waited = 0
    while waited < CFG["interval"] and not CFG["paused"] and not TEST_FLAG["on"]:
        await asyncio.sleep(1)
        waited += 1


async def main():
    client = TelegramClient("session", API_ID, API_HASH)
    await client.start(phone=PHONE)  # код придёт в Telegram, введи его в консоль
    print("Telegram: вход выполнен")
    chat_id = MAX_CHAT_ID or await asyncio.to_thread(find_chat_id)
    print("MAX chat_id:", chat_id)

    asyncio.create_task(asyncio.to_thread(menu_loop))   # меню в MAX
    asyncio.create_task(scheduler(client, chat_id))     # публикация по расписанию
    saved = load_json(STATE_FILE, {})
    history = [] if TEST_REPOST_LAST else prune(load_json(HISTORY_FILE, []))
    await sync_channels(client, saved)
    print("Слежу за каналами:", ", ".join(LAST_IDS), "- жду новые посты...")

    fails = {}
    while True:
        if CFG["paused"]:
            await asyncio.sleep(3)
            continue
        await sync_channels(client, saved)
        if TEST_FLAG["on"]:
            TEST_FLAG["on"] = False
            await run_test(client, history)
        for ch in list(CFG["channels"]):
            if ch not in LAST_IDS:
                continue
            try:
                await process_channel(client, ch, history, fails)
            except Exception as e:
                print(f"[{ch}] ошибка:", e)
            await asyncio.sleep(1)
        await wait_interval()


if __name__ == "__main__":
    asyncio.run(main())
