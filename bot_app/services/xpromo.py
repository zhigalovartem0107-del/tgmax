"""📢 Кросс-промо: рекламный пост владельца кабинета выходит в его каналы MAX по своему таймеру — отдельно от очереди
постов из источников: не занимает её мест, не ждёт её и не зависит от того, пуста ли она.

Здесь только кампании и расписание — без сети и без состояния бота. Какие каналы можно выбрать (только свои) и как
выкладывать, решает бот; но и учёт чужое трогать не даёт: остановить, возобновить и удалить кампанию можно, только
назвав её владельца."""
import copy
import math
import re
import secrets
import threading
import time

HOUR = 3600
EVERY_MIN, EVERY_MAX = HOUR, 7 * 24 * HOUR   # интервал: от часа до недели
KEEP_PER_OWNER = 20                          # кампаний на кабинет
MAX_MEDIA = 10                               # фото/видео в посте (столько MAX берёт в одно сообщение)
MAX_TEXT = 12000                             # длинный текст бот сам режет на сообщения MAX
RETRY_SEC = 300                              # не вышло в канал — повтор через 5 минут, дальше реже (но не реже интервала)
KINDS = ("image", "video")
STATES = ("on", "paused")
_HOURS = re.compile(r"(?:(?:каждые|каждый|раз\s+в)\s+)?(\d{1,3})\s*(?:ч|час|часа|часов|h|hours?)?\.?")
_DAY = {"сутки", "раз в сутки", "день", "раз в день", "каждый день"}


def _int(x):
    return x if isinstance(x, int) and not isinstance(x, bool) else None


def _num(x):
    return x if not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x) else None


def parse_hours(text):
    """Интервал из сообщения: «2», «2 ч», «каждые 12 часов», «12h», «раз в сутки» -> секунды (1…168 ч); иначе None."""
    if not isinstance(text, str):
        return None
    s = " ".join(text.lower().split())
    if s in _DAY:
        return 24 * HOUR
    m = _HOURS.fullmatch(s)
    if not m:
        return None
    sec = int(m.group(1)) * HOUR
    return sec if EVERY_MIN <= sec <= EVERY_MAX else None


def clean_media(media):
    """Вложения поста: только фото и видео с токеном MAX (не больше MAX_MEDIA); остальное — не берём."""
    out = []
    for m in media if isinstance(media, list) else []:
        if isinstance(m, dict) and m.get("kind") in KINDS and isinstance(m.get("token"), str) and m["token"]:
            out.append({"kind": m["kind"], "token": m["token"]})
    return out[:MAX_MEDIA]


def _themes(x, empty=False):
    """Каналы кампании (id тем): непустые строки, без повторов; кривой (или пустой, если empty=False) список — None.
    Из файла пустой список берём: все каналы кампании удалили — кампания видна, её можно удалить."""
    if not isinstance(x, list) or (not x and not empty) or not all(isinstance(t, str) and t for t in x):
        return None
    return list(dict.fromkeys(x))


def _times(d, keys):
    """{тема: время} из файла: только темы кампании и только числа."""
    d = d if isinstance(d, dict) else {}
    return {k: d[k] for k in keys if _num(d.get(k)) is not None}


def _clean(cid, r):
    """Кампания из файла; кривая (правили руками, сбой записи) — None, её пропускаем."""
    if not isinstance(cid, str) or not cid or not isinstance(r, dict):
        return None
    themes, every, text = _themes(r.get("themes"), empty=True), _int(r.get("every")), r.get("text")
    if not isinstance(r.get("owner"), str) or themes is None or every is None or not EVERY_MIN <= every <= EVERY_MAX \
            or not isinstance(text, str) or r.get("state") not in STATES:
        return None
    media = clean_media(r.get("media"))
    if not text.strip() and not media:
        return None
    fails = r.get("fails") if isinstance(r.get("fails"), dict) else {}
    sent = _int(r.get("sent"))
    return {"id": cid, "owner": r["owner"], "by": r.get("by"), "text": text, "media": media, "themes": themes,
            "every": every, "state": r["state"], "created": _num(r.get("created")) or 0,
            "next": _times(r.get("next"), themes), "last_at": _times(r.get("last_at"), themes),
            "sent": sent if sent and sent > 0 else 0, "last": _num(r.get("last")) or 0,
            "fails": {k: v for k, v in fails.items() if k in themes and (_int(v) or 0) > 0},
            "note": r.get("note") if isinstance(r.get("note"), str) else ""}


class Campaigns:
    """Кампании кросс-промо всех кабинетов. Файл — через save/load бота (запись атомарная, у бота — с копией)."""

    def __init__(self, path, save, load, clock=time.time):
        self.path, self._save, self.clock = path, save, clock
        self.lock = threading.Lock()
        self._wlock = threading.Lock()   # снимок и запись файла — строго по очереди: старый снимок не ляжет поверх нового
        try:
            raw = load(path, {})
        except Exception:                               # файл испорчен — начинаем с пустого, бот не падает
            raw = {}
        items = raw.get("items") if isinstance(raw, dict) else None
        self.items = {}
        for cid, r in (items.items() if isinstance(items, dict) else ()):
            got = _clean(cid, r)
            if got:
                self.items[cid] = got

    def save(self):
        with self._wlock:
            with self.lock:
                data = {"items": copy.deepcopy(self.items)}
            self._save(self.path, data)

    def add(self, owner, by, text, media, themes, every):
        """Новая кампания (первый выход — сразу, дальше раз в every секунд). Кривые данные или у кабинета уже
        KEEP_PER_OWNER кампаний — None."""
        themes, media = _themes(themes), clean_media(media)
        if not isinstance(owner, str) or not isinstance(text, str) or themes is None or _int(every) is None \
                or not EVERY_MIN <= every <= EVERY_MAX:
            return None
        text = text.strip()[:MAX_TEXT]
        if not text and not media:
            return None
        now = self.clock()
        with self.lock:
            if sum(1 for r in self.items.values() if r["owner"] == owner) >= KEEP_PER_OWNER:
                return None
            cid = secrets.token_hex(4)
            while cid in self.items:
                cid = secrets.token_hex(4)
            rec = {"id": cid, "owner": owner, "by": by, "text": text, "media": media, "themes": themes,
                   "every": every, "state": "on", "created": now, "next": {t: now for t in themes}, "last_at": {},
                   "sent": 0, "last": 0, "fails": {}, "note": ""}
            self.items[cid] = rec
            out = copy.deepcopy(rec)
        self.save()
        return out

    def get(self, cid):
        with self.lock:
            r = self.items.get(cid) if isinstance(cid, str) else None
            return copy.deepcopy(r) if r else None

    def of_owner(self, owner):
        """Кампании одного кабинета — по порядку создания. Чужих здесь нет."""
        with self.lock:
            mine = [copy.deepcopy(r) for r in self.items.values() if r["owner"] == owner]
        return sorted(mine, key=lambda r: (r["created"], r["id"]))

    def _own(self, cid, owner):
        """Кампания, если она этого владельца (под self.lock); иначе None."""
        r = self.items.get(cid) if isinstance(cid, str) else None
        return r if r is not None and isinstance(owner, str) and r["owner"] == owner else None

    def pause(self, cid, owner):
        """⏸ Остановить: пост не выходит, пока не возобновят. Чужая кампания — False."""
        with self.lock:
            r = self._own(cid, owner)
            if r is None:
                return False
            r["state"] = "paused"
        self.save()
        return True

    def resume(self, cid, owner):
        """▶️ Возобновить — по своему расписанию: в каждый канал не раньше, чем через интервал после прошлого выхода
        (ещё не выходила туда — сразу). Чужая кампания — False."""
        now = self.clock()
        with self.lock:
            r = self._own(cid, owner)
            if r is None:
                return False
            r["state"] = "on"
            r["next"] = {t: max(now, r["last_at"][t] + r["every"]) if t in r["last_at"] else now for t in r["themes"]}
            r["fails"] = {}
        self.save()
        return True

    def delete(self, cid, owner):
        """🗑 Удалить. Чужая кампания — False."""
        with self.lock:
            if self._own(cid, owner) is None:
                return False
            del self.items[cid]
        self.save()
        return True

    def forget_owner(self, owner):
        """Кабинет удалён (данные покупателя) — все его кампании долой. Сколько удалили."""
        with self.lock:
            gone = [cid for cid, r in self.items.items() if r["owner"] == owner]
            for cid in gone:
                del self.items[cid]
        if gone:
            self.save()
        return len(gone)

    def due(self, now=None):
        """[(кампания, тема)], которым пора выйти: идущие кампании, у которых подошло время канала."""
        now = self.clock() if now is None else now
        with self.lock:
            got = [(r["next"].get(t, 0), cid, t) for cid, r in self.items.items() if r["state"] == "on"
                   for t in r["themes"] if r["next"].get(t, 0) <= now]
        return [(cid, t) for _, cid, t in sorted(got)]

    def done(self, cid, tid, now=None):
        """Пост вышел в канал темы tid: следующий — через интервал."""
        now = self.clock() if now is None else now
        with self.lock:
            r = self.items.get(cid)
            if r is None or tid not in r["themes"]:
                return False
            r["next"][tid] = now + r["every"]
            r["last_at"][tid] = now
            r["last"], r["sent"], r["note"] = now, r["sent"] + 1, ""
            r["fails"].pop(tid, None)
        self.save()
        return True

    def failed(self, cid, tid, now=None, note=""):
        """Не вышло в канал: повтор через RETRY_SEC, при повторных сбоях — реже (но не реже интервала кампании).
        Через сколько секунд повтор (None — кампании или канала уже нет)."""
        now = self.clock() if now is None else now
        with self.lock:
            r = self.items.get(cid)
            if r is None or tid not in r["themes"]:
                return None
            n = r["fails"][tid] = r["fails"].get(tid, 0) + 1
            wait = min(r["every"], RETRY_SEC * 2 ** min(n - 1, 6))
            r["next"][tid] = now + wait
            r["note"] = str(note or "")[:200]
        self.save()
        return wait

    def later(self, cid, tid, at, note=""):
        """Канал кампании — не раньше времени at (например, MAX пока не пускает бота в канал)."""
        with self.lock:
            r = self.items.get(cid)
            if r is None or tid not in r["themes"] or _num(at) is None:
                return False
            r["next"][tid] = at
            if note:
                r["note"] = str(note)[:200]
        self.save()
        return True

    def drop_theme(self, cid, tid):
        """Канал больше не в кампании (тему удалили или она не этого кабинета)."""
        with self.lock:
            r = self.items.get(cid)
            if r is None or tid not in r["themes"]:
                return False
            r["themes"].remove(tid)
            for k in ("next", "last_at", "fails"):
                r[k].pop(tid, None)
        self.save()
        return True

    def count_on(self, owner):
        """Сколько кампаний кабинета сейчас идут."""
        with self.lock:
            return sum(1 for r in self.items.values() if r["owner"] == owner and r["state"] == "on")
