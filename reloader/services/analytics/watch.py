"""📊 Проверка канала по пересланному посту или ссылке — без привязки канала и без прав админа.

Откуда пост: пересланный пост канала MAX (в пересылке — id канала), ссылка t.me/… (публичный канал Telegram) или
max.ru/…; закрытые ссылки (t.me/+…, t.me/joinchat/…, t.me/c/…, max.ru/join/…) — сразу «закрытый канал».
Публичная страница t.me — название и число подписчиков. Снимки числа подписчиков (бот делает их сам, раз в час) →
подписалось / отписалось / итого. «🕘 Недавние проверки» — у каждого пользователя свои.
Здесь нет сети и состояния бота: бот сам спрашивает MAX и t.me и передаёт сюда готовые данные."""
import html as _html
import re
import threading
import time
from datetime import datetime

CLOSED_TEXT = "Это закрытый канал. Чтобы я мог его анализировать, добавь меня в администраторы этого канала."
KEEP_RECENT = 10          # «Недавние проверки» у одного пользователя
KEEP_POINTS = 2000        # снимков числа подписчиков на канал
KEEP_DAYS = 90            # канал, который никто не открывал столько дней, бот больше не обновляет
SAME_KEEP_SEC = 6 * 3600  # то же число подписчиков — новый снимок не чаще раза в 6 часов

_TG = re.compile(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(.+)$", re.I)
_MAX = re.compile(r"^(?:https?://)?(?:www\.)?(?:web\.)?max\.ru/(.+)$", re.I)
_TG_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_TG_SERVICE = {"s", "c", "joinchat", "addstickers", "addemoji", "share", "proxy", "socks", "iv", "login",
               "setlanguage", "addlist", "boost", "contact", "invoice"}


def _int(x):
    return x if isinstance(x, int) and not isinstance(x, bool) else None


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf")


def _parts(tail):
    return [p for p in tail.split("?")[0].split("#")[0].split("/") if p]


def source_of(msg, text):
    """Откуда пост. {"kind": "max", "chat_id"} — пересланный пост канала MAX (или web.max.ru/-123);
    {"kind": "maxlink", "slug"} — max.ru/имя; {"kind": "tg", "username", "post"} — t.me/имя[/номер];
    {"kind": "closed"} — закрытая ссылка-приглашение; None — не пост канала и не ссылка на канал
    (переслали сообщение человека — тоже None: у него в пересылке есть автор)."""
    link = msg.get("link") if isinstance(msg, dict) else None
    if isinstance(link, dict) and link.get("type") == "forward":
        sender = link.get("sender") if isinstance(link.get("sender"), dict) else {}
        cid = _int(link.get("chat_id"))
        return {"kind": "max", "chat_id": cid} if cid is not None and not sender.get("user_id") else None
    t = (text or "").strip() if isinstance(text, str) else ""
    if not t or len(t) > 300 or re.search(r"\s", t):
        return None                                    # только сама ссылка, без слов вокруг
    m = _TG.match(t)
    if m:
        parts = _parts(m.group(1))
        if not parts:
            return None
        if parts[0].startswith("+") or parts[0].lower() in ("joinchat", "c"):
            return {"kind": "closed"}
        if parts[0].lower() == "s" and len(parts) > 1:
            parts = parts[1:]
        if not _TG_NAME.match(parts[0]) or parts[0].lower() in _TG_SERVICE:
            return None
        post = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        return {"kind": "tg", "username": parts[0].lower(), "post": post}
    m = _MAX.match(t)
    if m:
        parts = _parts(m.group(1))
        if not parts:
            return None
        if parts[0].lower() == "join":
            return {"kind": "closed"}
        if re.fullmatch(r"-?\d{4,}", parts[0]):
            return {"kind": "max", "chat_id": int(parts[0])}
        if re.fullmatch(r"[A-Za-z0-9_.-]{2,64}", parts[0]):
            return {"kind": "maxlink", "slug": parts[0].lower()}
    return None


def parse_tme(page):
    """Публичная страница t.me/<канал>: {"title", "subs", "kind": "channel" | "group" | None}.
    Число — из строки «12 345 subscribers» / «12 345 подписчиков» (у группы — members / участников)."""
    page = page if isinstance(page, str) else ""
    m = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    title = _html.unescape(m.group(1)).strip() if m else ""
    if not title:
        m = re.search(r'class="tgme_page_title"[^>]*>\s*(?:<span[^>]*>)?([^<]+)', page)
        title = _html.unescape(m.group(1)).strip() if m else ""
    subs = kind = None
    m = re.search(r'class="tgme_page_extra"[^>]*>([^<]*)<', page)
    if m:
        extra = _html.unescape(m.group(1)).replace("\xa0", " ")
        n = re.match(r"\s*(\d[\d ,.]*)\s*(subscriber|подписчик|member|участник)", extra, re.I)
        if n:
            subs = int(re.sub(r"\D", "", n.group(1)))
            kind = "channel" if n.group(2).lower().startswith(("subscr", "подпис")) else "group"
    return {"title": title or None, "subs": subs, "kind": kind}


def _views(text):
    """«1.2K» → 1200, «2.5M» → 2500000, «980» → 980; не число — None."""
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*([KkMmКкМм]?)", (text or "").strip().replace("\xa0", "").replace(" ", ""))
    if not m:
        return None
    mult = {"": 1, "k": 1000, "к": 1000, "m": 1000000, "м": 1000000}[m.group(2).lower()]
    return int(round(float(m.group(1).replace(",", ".")) * mult))


def _iso(text):
    try:
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return None


def parse_tme_posts(page):
    """Страница t.me/s/<канал>: [(номер поста, просмотров, время)] — у постов, где Telegram показывает просмотры.
    Так видно охват публичного канала Telegram без прав админа."""
    page = page if isinstance(page, str) else ""
    out, seen = [], set()
    for block in re.split(r'(?=data-post=")', page)[1:]:
        m = re.match(r'data-post="[^"/]+/(\d+)"', block)
        v = re.search(r'class="tgme_widget_message_views">([^<]+)<', block)
        if not m or not v or int(m.group(1)) in seen:
            continue
        views = _views(_html.unescape(v.group(1)))
        if views is None:
            continue
        t = re.search(r'<time datetime="([^"]+)"', block)
        seen.add(int(m.group(1)))
        out.append((int(m.group(1)), views, _iso(t.group(1)) if t else None))
    return out


def deltas(points):
    """Снимки [(время, подписчиков)] по порядку → (подписалось, отписалось, итого). Между двумя снимками рост —
    подписались, падение — отписались (кто успел подписаться и отписаться между снимками — не виден)."""
    vals = [n for _, n in points]
    joined = sum(max(0, b - a) for a, b in zip(vals, vals[1:]))
    left = sum(max(0, a - b) for a, b in zip(vals, vals[1:]))
    return joined, left, (vals[-1] - vals[0]) if vals else 0


def _n(x):
    return f"{int(x):,}".replace(",", " ")


def stats_line(joined, left, total):
    return f"Подписалось: +{_n(joined)}, Отписалось: -{_n(left)}, Итого: {'+' if total >= 0 else '-'}{_n(abs(total))}"


class Watch:
    """channel_checks.json: {"channels": {ключ: {title, link, closed, points: [[время, подписчиков]], checked}},
    "recent": {пользователь: [ключ, …]}}. Ключ канала — «max:<id>», «tg:<имя>» или «maxlink:<имя>»."""

    def __init__(self, path, save, load, clock=time.time):
        self.path, self._save, self.clock = path, save, clock
        self.lock = threading.RLock()
        try:
            data = load(path, {})
        except Exception:                              # файл испорчен — начинаем с чистого листа, бот не падает
            data = {}
        data = data if isinstance(data, dict) else {}
        chans = data.get("channels") if isinstance(data.get("channels"), dict) else {}
        self.channels = {}
        for key, ch in chans.items():
            if not isinstance(key, str) or not isinstance(ch, dict):
                continue
            pts = [(float(p[0]), int(p[1])) for p in ch.get("points") or ()
                   if isinstance(p, (list, tuple)) and len(p) == 2 and _num(p[0]) and _int(p[1]) is not None
                   and p[1] >= 0]
            self.channels[key] = {"title": str(ch.get("title") or key)[:120], "link": str(ch.get("link") or "")[:300],
                                  "closed": bool(ch.get("closed")), "points": sorted(pts)[-KEEP_POINTS:],
                                  "checked": float(ch["checked"]) if _num(ch.get("checked")) else 0.0}
        rec = data.get("recent") if isinstance(data.get("recent"), dict) else {}
        self._recent = {str(u): [k for k in keys if isinstance(k, str) and k in self.channels][:KEEP_RECENT]
                        for u, keys in rec.items() if isinstance(keys, list)}

    def remember(self, uid, key, info):
        """Пользователь проверил канал: он — первым в его «Недавних проверках»."""
        with self.lock:
            ch = self.channels.setdefault(key, {"title": key, "link": "", "closed": False, "points": [],
                                                "checked": 0.0})
            ch.update(title=str(info.get("title") or ch["title"])[:120], link=str(info.get("link") or ch["link"])[:300],
                      closed=bool(info.get("closed")), checked=self.clock())
            box = self._recent.setdefault(str(uid), [])
            if key in box:
                box.remove(key)
            box.insert(0, key)
            del box[KEEP_RECENT:]

    def snap(self, key, n, t=None):
        """Снимок числа подписчиков. То же число — не чаще раза в 6 часов (файл не растёт впустую)."""
        if _int(n) is None or n < 0:
            return
        with self.lock:
            ch = self.channels.get(key)
            if ch is None:
                return
            t = float(t) if _num(t) else self.clock()
            pts = ch["points"]
            if pts and t < pts[-1][0]:
                return                                 # снимок из прошлого (история) — только в пустой канал
            if pts and pts[-1][1] == n and t - pts[-1][0] < SAME_KEEP_SEC:
                return
            pts.append((t, n))
            del pts[:-KEEP_POINTS]

    def deltas(self, key):
        with self.lock:
            return deltas((self.channels.get(key) or {}).get("points") or [])

    def last(self, key):
        with self.lock:
            pts = (self.channels.get(key) or {}).get("points") or []
            return pts[-1][1] if pts else None

    def since(self, key):
        with self.lock:
            pts = (self.channels.get(key) or {}).get("points") or []
            return pts[0][0] if pts else None

    def points(self, key):
        with self.lock:
            return len((self.channels.get(key) or {}).get("points") or [])

    def history(self, key):
        """Снимки канала [(время, подписчиков)] — для графика и оценки роста."""
        with self.lock:
            return list((self.channels.get(key) or {}).get("points") or [])

    def title(self, key):
        with self.lock:
            return (self.channels.get(key) or {}).get("title")

    def recent(self, uid):
        """[{key, title, link, closed, subs, since, deltas}] — свежие сверху."""
        with self.lock:
            out = []
            for key in self._recent.get(str(uid), []):
                ch = self.channels.get(key)
                if ch is None:
                    continue
                pts = ch["points"]
                out.append({"key": key, "title": ch["title"], "link": ch["link"], "closed": ch["closed"],
                            "subs": pts[-1][1] if pts else None, "since": pts[0][0] if pts else None,
                            "deltas": deltas(pts)})
            return out

    def is_recent(self, uid, key):
        with self.lock:
            return key in self._recent.get(str(uid), [])

    def watched(self):
        """Каналы, за которыми бот сам следит (раз в час): есть в чьих-то «Недавних», открытые, не заброшенные."""
        now = self.clock()
        with self.lock:
            keys = dict.fromkeys(k for box in self._recent.values() for k in box)
            return [k for k in keys if k in self.channels and not self.channels[k]["closed"]
                    and now - self.channels[k]["checked"] < KEEP_DAYS * 86400]

    def save(self):
        with self.lock:
            data = {"channels": {k: dict(v, points=[list(p) for p in v["points"]]) for k, v in self.channels.items()},
                    "recent": {u: list(v) for u, v in self._recent.items()}}
        self._save(self.path, data, backup=True)
