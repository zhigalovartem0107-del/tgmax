"""Фон для картинок аналитики — случайное фото с picsum.photos (без ключей и регистрации).

- адрес — https://picsum.photos/1280/800; picsum переадресует на само фото — берём его только с picsum.photos
  (и его поддоменов) и только по https;
- ответ — картинка (image/*), не больше 8 МБ, открывается и не меньше 640×400;
- одно фото на сутки по Москве (кэш: адрес и файл) — все карточки дня в одном стиле, picsum не дёргаем;
- picsum недоступен, ответил ошибкой или прислал не то — (None, причина), снова пробуем не раньше чем через
  30 минут; карточка тогда рисует свой градиент (см. analytics_image.mesh_gradient)."""
import io
import json
import os
import threading
import time
from urllib.parse import urlsplit

from PIL import Image

PICSUM_URL = "https://picsum.photos/1280/800"
IMG_HOSTS = ("picsum.photos", "fastly.picsum.photos", "i.picsum.photos")
MAX_BYTES = 8 << 20                     # фото не больше 8 МБ
MIN_SIZE = (640, 400)                   # и не меньше 640×400
MAX_PIXELS = 40_000_000                 # «бомба» из пикселей — отказ до распаковки
RETRY_SEC = 1800                        # picsum не дал фото — снова не раньше чем через 30 минут
MSK = 3 * 3600

_LOCK = threading.Lock()                # одно скачивание за раз (две карточки сразу — одно фото)
_MEMO = {}                              # путь файла -> (время изменения, картинка): не читаем диск на каждую карточку


def msk_day(ts=None):
    """День по Москве: «ГГГГ-ММ-ДД» (фото меняется в полночь по МСК)."""
    return time.strftime("%Y-%m-%d", time.gmtime((ts if ts is not None else time.time()) + MSK))


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


def _host_ok(url):
    """Только https и только picsum.photos (сам сайт и его поддомены с фото)."""
    try:
        u = urlsplit(url)
        return u.scheme == "https" and (u.hostname or "").lower() in IMG_HOSTS and not u.username \
            and u.port in (None, 443)
    except ValueError:                              # кривой порт и т. п.
        return False


def _download(http, url):
    """Скачать фото: (картинка, адрес, где оно на самом деле лежит) или (None, почему нет)."""
    if not isinstance(url, str) or not _host_ok(url):
        return None, "адрес фото — не picsum.photos по https"
    r = http.get(url, timeout=20, stream=True)
    try:
        final = getattr(r, "url", None) or url     # requests идёт по переадресациям — проверяем, куда пришли
        if not isinstance(final, str) or not _host_ok(final):
            return None, "фото увело на чужой адрес"
        img, why = _read_image(r)
        return (img, final) if img is not None else (None, why)
    finally:
        close = getattr(r, "close", None)
        if callable(close):
            close()


def _read_image(r):
    if r.status_code != 200:
        return None, f"picsum ответил {r.status_code}"
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


def daily_photo(http, cache_path, now=None, log=None, url=PICSUM_URL):
    """Фото на сегодня: (картинка, сведения) или (None, почему нет). Сеть — не чаще раза в сутки (и не чаще раза
    в 30 минут после сбоя). http — requests.Session (или что-то с .get(url, timeout, stream))."""
    log = log or (lambda _msg: None)
    now = time.time() if now is None else now
    with _LOCK:
        cache = _load(cache_path)
        img = _cached(cache, now)
        if img is not None:
            return img, cache
        day = msk_day(now)
        if cache.get("day") == day and isinstance(cache.get("url"), str):
            try:                                # файл пропал — то же фото по сохранённому адресу
                img, got = _download(http, cache["url"])
            except Exception as e:
                img, got = None, type(e).__name__
            if img is not None:
                return img, _remember(cache_path, {"day": day, "url": got}, img)
        if now < (cache.get("failed_until") or 0):
            return None, "picsum недавно не дал фото — попробую позже"
        try:
            img, got = _download(http, url)
        except Exception as e:                  # сеть, таймаут — не падаем
            img, got = None, f"picsum не ответил ({type(e).__name__})"
        if img is None:
            failed = {k: v for k, v in cache.items() if k != "failed_until"}
            failed["failed_until"] = now + RETRY_SEC
            try:
                _save(cache_path, failed)
            except OSError:
                pass
            log(f"Picsum: фото для карточек не получено ({got}) — рисую свой градиент")
            return None, got
        return img, _remember(cache_path, {"day": day, "url": got}, img)


def _remember(cache_path, info, img):
    path = _photo_file(cache_path)
    try:
        img.save(path, "JPEG", quality=90)
        info["file"] = path
        _save(cache_path, info)
    except OSError:
        pass
    return info
