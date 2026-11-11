"""Фон дня для картинок аналитики — безопасное фото природы с Unsplash (официальный API).

Безопасность (картинку видят все — подписчики, рекламодатели, дети):
- слова для поиска — только свои: mountains / forest / ocean / abstract (пользователь их не задаёт);
- content_filter=high — самый строгий фильтр Unsplash;
- описание и теги фото проверяем сами: «взрослые» слова (англ. и рус.) и люди на фото — отказ, картинку даже
  не скачиваем; берём только пейзаж;
- скачиваем только с images.unsplash.com и только по https; ответ — картинка (image/*), не больше 8 МБ,
  открывается и не меньше 640×400;
- одно фото на сутки по Москве (кэш: адрес, автор, файл); API не ответил — пробуем снова не раньше чем через час.

Нет ключа (UNSPLASH_ACCESS_KEY) или фото не подошло — (None, причина): бот рисует свой фон природы."""
import io
import json
import os
import re
import threading
import time
import zlib
from urllib.parse import urlsplit

from PIL import Image

SAFE_TOPICS = ("mountains", "forest", "ocean", "abstract")
API_URL = "https://api.unsplash.com/photos/random"
IMG_HOSTS = ("images.unsplash.com",)
API_HOSTS = ("api.unsplash.com",)
MAX_BYTES = 8 << 20                     # фото дня не больше 8 МБ
MIN_SIZE = (640, 400)                   # и не меньше 640×400 (меньше — размытое пятно)
MAX_PIXELS = 40_000_000                 # «бомба» из пикселей — отказ до распаковки
RETRY_SEC = 3600                        # API не ответил или фото не подошло — снова не раньше чем через час
TRIES = 3                               # фото не прошло проверку — просим другое (не больше стольких раз)
MSK = 3 * 3600

# «Взрослое» и люди на фото — отказ (нам нужен только пейзаж). Слова целиком или по корню.
_BAD = re.compile(
    r"\b(nud(e|es|ity|ist)|naked|nsfw|porn\w*|sex\w*|erotic\w*|lingerie|underwear|bikini\w*|swimsuit\w*|swimwear|"
    r"topless|bra|panties|thong|boobs?|breasts?|butt|buttocks|ass|fetish\w*|bdsm|seductive|sensual\w*|"
    r"intimate|adult|xxx|stripp\w*|striptease|onlyfans|playboy|hot\s+girls?|"
    r"wom[ae]n|m[ae]n|girls?|boys?|lady|ladies|guys?|models?|modell?ing|person|people|portrait\w*|selfie|"
    r"kiss\w*|bedroom|"
    r"голы[йея]|обнаж\w*|эроти\w*|секс\w*|порн\w*|бель[её]|бикини|купальник\w*|интим\w*|18\+|"
    r"девушк\w*|женщин\w*|мужчин\w*|модел\w*|портрет\w*|люди|человек\w*)\b",
    re.IGNORECASE)
_LOCK = threading.Lock()                # одно скачивание за раз (две карточки сразу — одно фото)
_MEMO = {}                              # путь файла -> (время изменения, картинка): не читаем диск на каждую карточку


def msk_day(ts=None):
    """День по Москве: «ГГГГ-ММ-ДД» (фото меняется в полночь по МСК)."""
    return time.strftime("%Y-%m-%d", time.gmtime((ts if ts is not None else time.time()) + MSK))


def topic_of(day):
    """Тема дня из своего списка — по дате (каждый день своя, у всех одинаковая)."""
    return SAFE_TOPICS[zlib.crc32(str(day).encode()) % len(SAFE_TOPICS)]


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _photo_file(cache_path):
    return os.path.splitext(cache_path)[0] + ".jpg"


def _words(meta):
    """Всё, что Unsplash пишет о фото: описание, alt, теги (там могут оказаться «взрослые» слова)."""
    out = [meta.get("description"), meta.get("alt_description")]
    for key in ("tags", "tags_preview"):
        for tag in meta.get(key) or ():
            out.append(tag.get("title") if isinstance(tag, dict) else tag)
    return " ".join(str(x) for x in out if isinstance(x, str))


def unsafe(meta):
    """Почему фото нельзя (строка) или None — можно."""
    if not isinstance(meta, dict):
        return "ответ Unsplash — не фото"
    hit = _BAD.search(_words(meta))
    return f"в описании «{hit.group(0)}»" if hit else None


def _host_ok(url, hosts):
    try:
        u = urlsplit(url)
        return u.scheme == "https" and (u.hostname or "").lower() in hosts and not u.username \
            and u.port in (None, 443)
    except ValueError:                              # кривой порт и т. п.
        return False


def _download(http, url):
    """Скачать фото: только https://images.unsplash.com, тип — картинка, до 8 МБ, открывается, не меньше 640×400.
    (картинка, None) или (None, почему нет)."""
    if not isinstance(url, str) or not _host_ok(url, IMG_HOSTS):
        return None, "адрес фото — не images.unsplash.com по https"
    r = http.get(url, timeout=20, stream=True)
    try:
        final = getattr(r, "url", None) or url        # requests идёт по переадресациям — проверяем, куда пришли
        if not isinstance(final, str) or not _host_ok(final, IMG_HOSTS):
            return None, "фото увело на чужой адрес"
        return _read_image(r)
    finally:
        close = getattr(r, "close", None)
        if callable(close):
            close()


def _read_image(r):
    if r.status_code != 200:
        return None, f"фото не скачалось ({r.status_code})"
    kind = str(r.headers.get("Content-Type") or "").lower()
    if not kind.startswith("image/"):
        return None, f"вместо фото пришло «{kind or 'неизвестно что'}»"
    try:
        if int(r.headers.get("Content-Length") or 0) > MAX_BYTES:
            return None, "фото больше 8 МБ"
    except ValueError:
        pass
    buf = bytearray()
    for chunk in r.iter_content(65536):
        buf += chunk
        if len(buf) > MAX_BYTES:
            return None, "фото больше 8 МБ"
    try:
        img = Image.open(io.BytesIO(bytes(buf)))
        if img.width * img.height > MAX_PIXELS:
            return None, "фото слишком большое"
        img.load()
        img = img.convert("RGB")
    except Exception:
        return None, "фото битое"
    if img.width < MIN_SIZE[0] or img.height < MIN_SIZE[1]:
        return None, f"фото слишком маленькое ({img.width}×{img.height})"
    return img, None


def _cached(cache, now):
    """Фото за сегодня из файла (уже проверенное) или None."""
    path = cache.get("file")
    if cache.get("day") != msk_day(now) or not isinstance(path, str):
        return None
    try:
        mtime = os.path.getmtime(path)
        got = _MEMO.get(path)
        if got and got[0] == mtime:
            return got[1]
        img = Image.open(path)
        img.load()
        img = img.convert("RGB")
    except Exception:
        return None
    _MEMO.clear()
    _MEMO[path] = (mtime, img)
    return img


def _fetch(http, key, day, log):
    """Новое фото дня. (картинка, сведения) или (None, почему нет)."""
    auth = {"Authorization": f"Client-ID {key}", "Accept-Version": "v1"}
    topic = topic_of(day)
    why = "нет ответа"
    for _ in range(TRIES):
        r = http.get(API_URL, params={"query": topic, "orientation": "landscape", "content_filter": "high"},
                     headers=auth, timeout=15)
        if r.status_code != 200:
            return None, f"Unsplash ответил {r.status_code}"
        try:
            meta = r.json()
        except ValueError:
            return None, "Unsplash прислал не JSON"
        why = unsafe(meta)
        if why:
            log(f"Unsplash: фото отклонено фильтром ({why})")
            continue
        url = (meta.get("urls") or {}).get("regular") if isinstance(meta.get("urls"), dict) else None
        img, why = _download(http, url)
        if img is None:
            log(f"Unsplash: фото не подошло ({why})")
            continue
        ping = (meta.get("links") or {}).get("download_location") if isinstance(meta.get("links"), dict) else None
        if isinstance(ping, str) and _host_ok(ping, API_HOSTS):
            try:                                # правило Unsplash: отметить, что фото использовано
                http.get(ping, headers=auth, timeout=10)
            except Exception:
                pass
        user = meta.get("user") if isinstance(meta.get("user"), dict) else {}
        return img, {"day": day, "topic": topic, "url": url, "id": str(meta.get("id") or ""),
                     "author": str(user.get("name") or "").strip()[:60]}
    return None, why


def daily_photo(http, key, cache_path, now=None, log=None):
    """Фото дня: (картинка, сведения о фото) или (None, почему нет). Сеть — не чаще раза в сутки (и не чаще раза
    в час после сбоя). http — requests.Session (или что-то с .get(url, params, headers, timeout, stream))."""
    log = log or (lambda _msg: None)
    if not key:
        return None, "нет ключа Unsplash (UNSPLASH_ACCESS_KEY)"
    now = time.time() if now is None else now
    with _LOCK:
        cache = _load(cache_path)
        img = _cached(cache, now)
        if img is not None:
            return img, cache
        day = msk_day(now)
        if cache.get("day") == day and isinstance(cache.get("url"), str):
            try:                                # файл пропал — тот же адрес, без нового вопроса к API
                img, why = _download(http, cache["url"])
            except Exception as e:
                img, why = None, type(e).__name__
            if img is None:
                log(f"Unsplash: фото дня не скачалось снова ({why})")
            else:
                return img, _remember(cache_path, cache, img)
        if now < (cache.get("failed_until") or 0):
            return None, "Unsplash недавно не ответил — попробую позже"
        try:
            img, info = _fetch(http, key, day, log)
        except Exception as e:                  # сеть, таймаут — не падаем
            img, info = None, f"Unsplash не ответил ({type(e).__name__})"
        if img is None:
            failed = {k: v for k, v in cache.items() if k != "failed_until"}
            failed["failed_until"] = now + RETRY_SEC
            try:
                _save(cache_path, failed)
            except OSError:
                pass
            log(f"Unsplash: фото дня не получено ({info})")
            return None, info
        return img, _remember(cache_path, info, img)


def _remember(cache_path, info, img):
    info = {k: v for k, v in info.items() if k != "failed_until"}
    path = _photo_file(cache_path)
    try:
        img.save(path, "JPEG", quality=90)
        info["file"] = path
        _save(cache_path, info)
    except OSError:
        pass
    return info


def credit(cache_path, now=None):
    """Подпись к фото дня: «Фото: <автор> / Unsplash» (правило Unsplash — указывать автора) или None."""
    cache = _load(cache_path)
    if cache.get("day") != msk_day(now) or not cache.get("url"):
        return None
    author = str(cache.get("author") or "").strip()
    return f"Фото: {author} / Unsplash" if author else "Фото: Unsplash"
