"""Дашборд Reloader — аналитика каналов картинкой 1280×800 (Pillow), свой дизайн.

Тема — по московскому времени: с 06:00 до 17:00 светлая (светло-серый фон, тёмный текст), с 17:00 до 06:00 тёмная
(#0A0A0A, белый текст); акценты в обеих — фиолетовый #9B30FF и розовый #FF1493. Фон — фото дня (природа) или свой
градиент (фиолетовый, розовый, чёрный): фото сильно размыто и под вуалью цвета темы, поверх — «стеклянные» панели.
Картинки: канал, сеть каналов, прогноз поста, оценка потенциала и рекламный охват. Водяной знак «Reloader» (внизу
справа) — на каждой картинке.
Только подсчёт и рисование — без сети и без состояния бота (данные приходят готовыми), поэтому легко проверяется
тестами."""
import math
import random
import time
import zlib

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SIZE = (1280, 800)                      # все картинки — 16:10
W, H = SIZE
BG = (0x0A, 0x0A, 0x0A)                 # #0A0A0A — фон тёмной темы
PURPLE = (0x9B, 0x30, 0xFF)             # #9B30FF — акцент
HOTPINK = (0xFF, 0x14, 0x93)            # #FF1493 — второй акцент
WHITE = (0xFF, 0xFF, 0xFF)
INK = (0x14, 0x14, 0x18)                # текст светлой темы
PAPER = (0xF4, 0xF4, 0xF7)              # фон светлой темы
MSK = 3 * 3600                          # Москва — UTC+3 круглый год
LIGHT_HOURS = (6, 17)                   # с 06:00 до 17:00 по Москве — светлая тема
THEMES = {
    "dark": {"name": "dark", "bg": BG, "text": WHITE, "muted": (160, 160, 174), "faint": (104, 104, 118),
             "accent": PURPLE, "accent2": HOTPINK, "panel": (24, 24, 30, 210), "edge": (255, 255, 255, 30),
             "track": (255, 255, 255, 34), "grid": (255, 255, 255, 22), "veil": BG, "veil_k": 0.60,
             "glow": 0.22, "shadow": 0, "dot": (24, 24, 30)},
    "light": {"name": "light", "bg": PAPER, "text": INK, "muted": (96, 96, 110), "faint": (150, 150, 162),
              "accent": PURPLE, "accent2": HOTPINK, "panel": (255, 255, 255, 214), "edge": (20, 20, 24, 22),
              "track": (20, 20, 24, 24), "grid": (20, 20, 24, 18), "veil": PAPER, "veil_k": 0.60,
              "glow": 0.12, "shadow": 34, "dot": WHITE},
}
WEEK = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
MONTHS = ("янв.", "февр.", "марта", "апр.", "мая", "июня", "июля", "авг.", "сент.", "окт.", "нояб.", "дек.")
DAY = 86400
MARGIN = 40


# ---------- числа ----------
def num(x):
    """Конечное число или None: None, NaN, бесконечность, строка, True/False — «нет данных» (файл истории мог
    испортиться, MAX — прислать что-то странное; картинка от этого падать не должна)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def fmt_int(n):
    """5008 -> «5 008»; нет данных — «—»."""
    n = num(n)
    return "—" if n is None else f"{int(round(n)):,}".replace(",", " ")


def fmt_short(n):
    """121700 -> «121,7K», 21900 -> «21,9K», 593 -> «593»."""
    n = num(n)
    if n is None:
        return "—"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".", ",").replace(",0M", "M")
    if abs(n) >= 10_000:
        return f"{n / 1000:.1f}K".replace(".", ",").replace(",0K", "K")
    return fmt_int(n)


def fmt_big(n):
    """Точно до 10 млн («5 008»), дальше коротко («12,3M»): огромные числа не вылезают за карточку."""
    n = num(n)
    return fmt_short(n) if n is not None and abs(n) >= 10_000_000 else fmt_int(n)


def fmt_pct(x):
    x = num(x)
    return "—" if x is None else f"{x:.1f}%".replace(".", ",")


def fmt_delta(n):
    n = num(n)
    return "—" if n is None else ("+" if n >= 0 else "−") + fmt_int(abs(n))


_DAY_RE = __import__("re").compile(r"\d{4}-\d{2}-\d{2}$")


def clean_posts(posts):
    """Посты с понятными временем и просмотрами (остальное — мусор, пропускаем)."""
    out = []
    for p in posts or ():
        if isinstance(p, dict) and num(p.get("t")) is not None and 0 < p["t"] < 4.1e9 \
                and num(p.get("views")) is not None and p["views"] >= 0:
            out.append(p)
    return out


def clean_history(history):
    """{"ГГГГ-ММ-ДД": подписчиков} — только правильные даты и целые неотрицательные числа."""
    out = {}
    for k, v in (history.items() if isinstance(history, dict) else ()):
        if isinstance(k, str) and _DAY_RE.match(k) and num(v) is not None and v >= 0:
            out[k] = int(v)
    return out


def day_key(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def views_by_age(posts, now):
    """Средние просмотры поста за 24/48/72 часа: берём посты, которым уже сутки (двое, трое) — их просмотры
    и есть «набрал за 24 ч» (48, 72). Окно — посты возрастом [N; N+24) ч; таких нет — все старше N ч за неделю."""
    out = {}
    for hours in (24, 48, 72):
        lo, hi = now - (hours + 24) * 3600, now - hours * 3600
        vals = [p["views"] for p in posts if lo < p["t"] <= hi and p.get("views") is not None]
        if not vals:
            vals = [p["views"] for p in posts if now - 7 * DAY < p["t"] <= hi and p.get("views") is not None]
        out[hours] = round(sum(vals) / len(vals)) if vals else None
    return out


def subs_days(history, subs, now, days=7):
    """[(ключ дня, подписчиков или None, прирост к прошлому дню или None)] за `days` дней, сегодня — последним.
    Сегодня — текущее число (если есть), иначе снимок за сегодня."""
    hist = dict(history or {})
    if subs is not None:
        hist[day_key(now)] = subs
    rows, prev = [], hist.get(day_key(now - days * DAY))
    for k in range(days - 1, -1, -1):
        key = day_key(now - k * DAY)
        val = hist.get(key)
        rows.append((key, val, (val - prev) if val is not None and prev is not None else None))
        prev = val if val is not None else prev
    return rows


def channel_numbers(title, subs, posts, history, now=None):
    """Всё для карточки канала. posts — [{"t", "views"}], history — {«ГГГГ-ММ-ДД»: подписчиков}."""
    now = now or time.time()
    hist = clean_history(history)
    posts = clean_posts(posts)
    subs = int(subs) if num(subs) is not None and subs >= 0 else None
    title = str(title).strip() if title is not None and str(title).strip() else "Канал"
    v = views_by_age(posts, now)
    days = subs_days(hist, subs, now)
    old = sorted(k for k in hist if k < day_key(now))
    def back(n):                                   # прирост за n дней: от самого раннего снимка в этом окне
        keys = [k for k in old if k >= day_key(now - n * DAY)]
        return (subs - hist[keys[0]]) if keys and subs is not None else None
    span = (now - time.mktime(time.strptime(old[0], "%Y-%m-%d"))) / DAY if old else 0
    return {"title": title, "subs": subs, "today": days[-1][2], "d7": back(7),
            "d30": back(30) if span >= 25 else None,
            "v24": v[24], "v48": v[48], "v72": v[72],
            "er24": round(v[24] / subs * 100, 1) if v[24] is not None and subs else None,
            "days": days, "now": now}


def network_numbers(channels, now=None):
    """Сводка по сети: channels — [channel_numbers(...)]. ER — просмотры за сутки к подписчикам."""
    channels = [c for c in channels or () if isinstance(c, dict)]
    subs = sum(num(c.get("subs")) or 0 for c in channels)
    def total(k):
        vals = [num(c.get(k)) for c in channels if num(c.get(k)) is not None]
        return sum(vals) if vals else None
    v24 = total("v24")
    top = sorted(channels, key=lambda c: -(num(c.get("subs")) or 0))[:3]
    return {"subs": subs, "v24": v24, "v48": total("v48"), "v72": total("v72"),
            "er": round(v24 / subs * 100, 1) if v24 is not None and subs else None,
            "n": len(channels), "top": [(str(c.get("title") or "Канал"), num(c.get("subs")), num(c.get("v24")))
                                        for c in top],
            "now": now or time.time()}


def seed_of(text):
    return zlib.crc32(str(text or "").encode("utf-8"))


# ---------- тема: по московскому времени ----------
def theme_for(ts=None):
    """«light» с 06:00 до 17:00 по Москве, иначе «dark»."""
    ts = num(ts)
    hour = time.gmtime((time.time() if ts is None else ts) + MSK).tm_hour
    return "light" if LIGHT_HOURS[0] <= hour < LIGHT_HOURS[1] else "dark"


def _theme(theme, now):
    return THEMES[theme if theme in THEMES else theme_for(now)]


def _now(now, data=None):
    """Время картинки: заданное явно, иначе — когда посчитаны цифры, иначе — сейчас."""
    for x in (now, (data or {}).get("now") if isinstance(data, dict) else None):
        if num(x) is not None and 0 < x < 4.1e9:
            return x
    return time.time()


def tz_label(now=None):
    """Подпись к часам сервера (по ним прогноз считает «лучшее время»): «время МСК» или честное смещение от UTC —
    у самостоятельно запущенного бота часы могут быть не московские."""
    lt = time.localtime(now)
    off = lt.tm_gmtoff if lt.tm_gmtoff is not None else -time.timezone
    if off == MSK:
        return "время МСК"
    h, m = divmod(abs(off) // 60, 60)
    return f"время UTC{'+' if off >= 0 else '-'}{h}" + (f":{m:02d}" if m else "")


def stamp(now):
    """«10 нояб. 2026 · 13:05 МСК»."""
    lt = time.gmtime(now + MSK)
    return f"{lt.tm_mday} {MONTHS[lt.tm_mon - 1]} {lt.tm_year} · {lt.tm_hour:02d}:{lt.tm_min:02d} МСК"


# ---------- цвета, шрифт и фигуры ----------
def hex_color(c):
    return "#%02X%02X%02X" % tuple(c[:3])


def _mix(a, b, k):
    return tuple(int(round(a[i] + (b[i] - a[i]) * k)) for i in range(3))


def default_font(bold, size):
    for name in (("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf") if bold else ("DejaVuSans.ttf", "LiberationSans-Regular.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _fit(d, text, font_fn, bold, size, width, low=14):
    """Шрифт, при котором текст влезает в ширину (длинное название, огромное число)."""
    while size > low:
        f = font_fn(bold, size)
        if d.textlength(str(text), font=f) <= width:
            return f
        size -= 2
    return font_fn(bold, low)


def _clip(d, text, font, width):
    """Строка, обрезанная по ширине (с «…»)."""
    text = str(text)
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


_MASKS = {}


def _round_mask(w, h, r):
    """Маска скруглённого прямоугольника с гладкими краями (рисуем вдвое крупнее и уменьшаем)."""
    key = (w, h, r)
    m = _MASKS.get(key)
    if m is None:
        if r <= 0:
            m = Image.new("L", (w, h), 255)
        else:
            big = Image.new("L", (w * 2, h * 2), 0)
            ImageDraw.Draw(big).rounded_rectangle((0, 0, w * 2 - 1, h * 2 - 1), r * 2, fill=255)
            m = big.resize((w, h), Image.BOX)
        if len(_MASKS) > 96:
            _MASKS.clear()
        _MASKS[key] = m
    return m


def _box(box):
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    return x0, y0, max(x0, x1), max(y0, y1)


def _fill(img, box, color, r=0):
    """Скруглённая заливка цветом с прозрачностью (RGBA) — с гладкими краями."""
    x0, y0, x1, y1 = _box(box)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    mask = _round_mask(w, h, max(0, min(int(r), w // 2, h // 2)))
    a = color[3] if len(color) > 3 else 255
    if a < 255:
        mask = mask.point(lambda v: v * a // 255)
    layer = Image.new("RGBA", (w, h), tuple(color[:3]) + (0,))
    layer.putalpha(mask)
    img.alpha_composite(layer, (x0, y0))


def _hgrad(w, h, c0=PURPLE, c1=HOTPINK):
    """Горизонтальный градиент фиолетовый → розовый."""
    row = Image.new("RGB", (256, 1))
    row.putdata([_mix(c0, c1, i / 255) for i in range(256)])
    return row.resize((max(1, w), max(1, h)), Image.BILINEAR)


def _grad_fill(img, box, r=0, alpha=255):
    x0, y0, x1, y1 = _box(box)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    layer = _hgrad(w, h).convert("RGBA")
    mask = _round_mask(w, h, max(0, min(int(r), w // 2, h // 2)))
    layer.putalpha(mask if alpha >= 255 else mask.point(lambda v: v * alpha // 255))
    img.alpha_composite(layer, (x0, y0))


def _grad_text(img, xy, text, font, anchor="ls"):
    """Текст, залитый градиентом фиолетовый → розовый (главная цифра картинки)."""
    d = ImageDraw.Draw(img)
    l, t, r, b = d.textbbox(xy, str(text), font=font, anchor=anchor)
    w, h = int(math.ceil(r - l)) + 2, int(math.ceil(b - t)) + 2
    if w <= 2 or h <= 2:
        return
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((xy[0] - l + 1, xy[1] - t + 1), str(text), font=font, fill=255, anchor=anchor)
    layer = _hgrad(w, h).convert("RGBA")
    layer.putalpha(mask)
    img.alpha_composite(layer, (int(l) - 1, int(t) - 1))


class _Smooth:
    """Слой для гладких линий, дуг и кругов: рисуем крупнее (ss раз) и уменьшаем — края без «лесенки»."""

    def __init__(self, img, box, ss=3):
        self.img, self.ss = img, ss
        self.x0, self.y0, x1, y1 = _box(box)
        self.x0, self.y0 = max(0, self.x0), max(0, self.y0)
        self.w, self.h = max(1, min(img.width, x1) - self.x0), max(1, min(img.height, y1) - self.y0)
        self.layer = Image.new("RGBA", (self.w * ss, self.h * ss), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.layer)

    def _p(self, x, y):
        return ((x - self.x0) * self.ss, (y - self.y0) * self.ss)

    def line(self, pts, color, width):
        if len(pts) > 1:
            self.d.line([self._p(*q) for q in pts], fill=color, width=max(1, int(width * self.ss)), joint="curve")
            for q in (pts[0], pts[-1]):                     # круглые концы
                self.circle(q, width / 2, color)

    def circle(self, c, r, color):
        x, y = self._p(*c)
        rr = r * self.ss
        self.d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=color)

    def arc(self, c, r, a0, a1, color, width):
        x, y = self._p(*c)
        rr, ww = (r + width / 2) * self.ss, width * self.ss
        self.d.arc((x - rr, y - rr, x + rr, y + rr), a0, a1, fill=color, width=max(1, int(ww)))

    def done(self):
        self.img.alpha_composite(self.layer.resize((self.w, self.h), Image.LANCZOS), (self.x0, self.y0))


def _shadow(img, box, r, alpha):
    """Мягкая тень под панелью (светлая тема): рисуем вчетверо меньше, размываем, растягиваем."""
    x0, y0, x1, y1 = _box(box)
    pad = 20
    w, h = x1 - x0 + 2 * pad, y1 - y0 + 2 * pad
    q = 4
    small = Image.new("L", (max(1, w // q), max(1, h // q)), 0)
    ImageDraw.Draw(small).rounded_rectangle((pad // q, pad // q + 1, (w - pad) // q, (h - pad) // q + 1),
                                            max(1, r // q), fill=alpha)
    mask = small.filter(ImageFilter.GaussianBlur(2.5)).resize((w, h), Image.BILINEAR)
    layer = Image.new("RGBA", (w, h), (46, 30, 80, 0))
    layer.putalpha(mask)
    img.alpha_composite(layer, (max(0, x0 - pad), max(0, y0 - pad)))


def _panel(img, box, t, r=24):
    """«Стеклянная» панель: полупрозрачная, со светлой (днём — тёмной) каймой в 1 пиксель."""
    x0, y0, x1, y1 = _box(box)
    if t["shadow"]:
        _shadow(img, box, r, t["shadow"])
    _fill(img, (x0, y0, x1, y1), t["edge"], r)
    _fill(img, (x0 + 1, y0 + 1, x1 - 1, y1 - 1), t["panel"], r - 1)


def _avatar(img, center, r, title, photo=None, font=None):
    """Фото канала в круге; нет фото — первая буква на градиенте."""
    x, y = int(center[0]), int(center[1])
    mask = _round_mask(2 * r, 2 * r, r)
    if photo is not None:
        try:
            pic = _cover(photo.convert("RGB"), 2 * r, 2 * r).convert("RGBA")
            pic.putalpha(mask)
            img.alpha_composite(pic, (x - r, y - r))
            return
        except Exception:
            pass                                       # битое фото — буква
    _grad_fill(img, (x - r, y - r, x + r, y + r), r)
    letter = (str(title or "?").strip()[:1] or "?").upper()
    ImageDraw.Draw(img).text((x, y), letter, font=(font or default_font)(True, int(r * 0.95)), fill=WHITE,
                             anchor="mm")


def _smooth(pts, n=10):
    """Плавная линия через точки без лишних «горбов» (монотонная кубическая, Фритч — Карлсон)."""
    k = len(pts)
    if k < 3:
        return list(pts)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    d = [(ys[i + 1] - ys[i]) / ((xs[i + 1] - xs[i]) or 1) for i in range(k - 1)]
    m = [d[0]] + [0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2 for i in range(1, k - 1)] + [d[-1]]
    for i in range(k - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        s = a * a + b * b
        if s > 9:
            tau = 3 / math.sqrt(s)
            m[i], m[i + 1] = tau * a * d[i], tau * b * d[i]
    out = []
    for i in range(k - 1):
        h = xs[i + 1] - xs[i]
        for j in range(n):
            u = j / n
            h00, h10 = 2 * u ** 3 - 3 * u ** 2 + 1, u ** 3 - 2 * u ** 2 + u
            h01, h11 = -2 * u ** 3 + 3 * u ** 2, u ** 3 - u ** 2
            out.append((xs[i] + u * h, h00 * ys[i] + h10 * h * m[i] + h01 * ys[i + 1] + h11 * h * m[i + 1]))
    out.append(pts[-1])
    return out


# ---------- фон: фото дня или свой рисунок природы — размыт и под вуалью ----------
def _cover(img, w, h):
    """Картинка по размеру кадра: растянуть с запасом и обрезать по центру."""
    k = max(w / max(1, img.width), h / max(1, img.height))
    nw, nh = max(w, math.ceil(img.width * k)), max(h, math.ceil(img.height * k))
    img = img.resize((nw, nh), Image.BILINEAR)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _glow(size, color, center, radius, alpha):
    """Мягкое пятно света (слой RGBA размера size)."""
    w, h = size
    layer = Image.new("RGBA", (w, h), color + (0,))
    a = Image.new("L", (w, h), 0)
    cx, cy, r = center[0] * w, center[1] * h, radius * w
    ImageDraw.Draw(a).ellipse((cx - r, cy - r, cx + r, cy + r), fill=int(255 * alpha))
    layer.putalpha(a.filter(ImageFilter.GaussianBlur(max(1, r * 0.45))))
    return layer


_MESH = {   # фон без фото: основа и пятна цвета (по порядку: главное, второе, …)
    "dark": ((9, 8, 12), ((155, 48, 255), (255, 20, 147), (92, 26, 200), (170, 16, 120))),
    "light": ((248, 246, 252), ((206, 178, 255), (255, 184, 222), (226, 206, 255), (255, 214, 236))),
}
_ANCHORS = ((0.10, 0.12), (0.90, 0.88), (0.80, 0.18), (0.22, 0.86), (0.52, 0.48))


def mesh_gradient(w, h, seed=0, theme="dark"):
    """Свой премиальный фон без интернета: несколько ярких «луж» света — фиолетовая, розовая, лиловая,
    малиновая — на почти чёрном (ночью) или пастельные на светлом (днём), между ними — глубокий фон, как у
    «сетчатого» градиента. Считаем в маленьком размере и плавно растягиваем на весь холст.
    От тех же w, h, seed и темы — та же картинка."""
    w, h = max(1, int(num(w) or 1)), max(1, int(num(h) or 1))
    base, colors = _MESH.get(theme if theme in _MESH else "dark")
    rnd = random.Random(zlib.crc32(f"mesh:{theme}:{seed}".encode()))
    sw = 96 if w >= h else max(2, round(96 * w / h))
    sh = max(2, round(96 * h / w)) if w >= h else 96
    spots = rnd.sample(_ANCHORS, len(colors))
    blobs = []
    for col, (ax, ay) in zip(colors, spots):
        cx, cy = (ax + rnd.uniform(-0.1, 0.1)) * sw, (ay + rnd.uniform(-0.1, 0.1)) * sh
        r = rnd.uniform(0.22, 0.34) * max(sw, sh)
        blobs.append((cx, cy, 1.0 / (r * r), col, rnd.uniform(0.82, 1.0)))
    px = []
    for y in range(sh):
        for x in range(sw):
            c = base
            for cx, cy, inv, col, k in blobs:
                a = k * math.exp(-((x - cx) ** 2 + (y - cy) ** 2) * inv)
                c = (c[0] + (col[0] - c[0]) * a, c[1] + (col[1] - c[1]) * a, c[2] + (col[2] - c[2]) * a)
            px.append((int(c[0]), int(c[1]), int(c[2])))
    small = Image.new("RGB", (sw, sh))
    small.putdata(px)
    return small.filter(ImageFilter.GaussianBlur(1.2)).resize((w, h), Image.BICUBIC)


def backdrop(size, theme, photo=None, seed=0):
    """Фон картинки на весь холст. Фото — по кадру, сильно размыто (резких мест не остаётся) и под вуалью цвета
    темы (ночью — тёмной, днём — светлой): текст читается, а фото видно. Фото нет или оно битое — свой градиент
    (фиолетовый, розовый, чёрный). Сверху — мягкие пятна фиолетового и розового. RGBA."""
    t = theme if isinstance(theme, dict) else THEMES.get(theme) or THEMES["dark"]
    w, h = max(8, int(size[0])), max(8, int(size[1]))
    sw, sh = max(4, w // 8), max(4, h // 8)               # всё мягкое считаем в 1/8 размера — быстро
    base = None
    if photo is not None:
        try:
            pic = _cover(photo.convert("RGB"), sw, sh).filter(ImageFilter.GaussianBlur(max(2, sw / 40)))
            base = Image.blend(pic, Image.new("RGB", (sw, sh), t["veil"]), t["veil_k"])
        except Exception:
            base = None                                # битая картинка — свой градиент
    if base is None:
        base = mesh_gradient(sw, sh, seed, t["name"])
    small = base.convert("RGBA")
    rnd = random.Random(seed)
    small.alpha_composite(_glow((sw, sh), PURPLE, (0.08 + rnd.uniform(0, 0.08), 0.02), 0.42, t["glow"]))
    small.alpha_composite(_glow((sw, sh), HOTPINK, (0.94 - rnd.uniform(0, 0.08), 1.0), 0.36, t["glow"] * 0.8))
    return small.resize((w, h), Image.BICUBIC)


# ---------- общие части дашборда ----------
def watermark_box(size):
    """Угол водяного знака «Reloader» — внизу справа (знак — на каждой картинке)."""
    w, h = size
    return (w - 300, h - 60, w, h)


def _watermark(img, t, font):
    """Маленький знак «Reloader»: квадратик с градиентом и «R» + надпись — внизу справа на каждой картинке."""
    d = ImageDraw.Draw(img)
    f = font(True, 21)
    text = "Reloader"
    x1, cy = img.width - MARGIN, img.height - 30
    tw = d.textlength(text, font=f)
    mx = int(x1 - tw - 10 - 28)
    _grad_fill(img, (mx, cy - 14, mx + 28, cy + 14), 8)
    d = ImageDraw.Draw(img)
    d.text((mx + 14, cy), "R", font=font(True, 17), fill=WHITE, anchor="mm")
    d.text((x1, cy), text, font=f, fill=t["text"], anchor="rm")


def _canvas(t, bg, seed=0):
    return backdrop(SIZE, t, bg, seed)


def _header(img, t, font, kicker, title, now, photo=None, avatar=False):
    """Шапка: «ярлык» на градиенте, крупное название, справа — дата и время среза (МСК)."""
    d = ImageDraw.Draw(img)
    kf = font(True, 14)
    kick = str(kicker).upper()
    kw = int(sum(d.textlength(ch, font=kf) for ch in kick) + 1.6 * max(0, len(kick) - 1)) + 32
    _grad_fill(img, (MARGIN, 30, MARGIN + kw, 60), 15)
    d = ImageDraw.Draw(img)
    x = MARGIN + 16
    for ch in kick:                                    # буквы ярлыка — с разрядкой
        d.text((x, 45), ch, font=kf, fill=WHITE, anchor="lm")
        x += d.textlength(ch, font=kf) + 1.6
    # время среза — справа
    sf = font(False, 16)
    text = stamp(now)
    sw = int(d.textlength(text, font=sf)) + 50
    _fill(img, (W - MARGIN - sw, 28, W - MARGIN, 62), t["panel"], 17)
    _fill(img, (W - MARGIN - sw + 16, 41, W - MARGIN - sw + 24, 49), HOTPINK + (255,), 4)
    d = ImageDraw.Draw(img)
    d.text((W - MARGIN - 16, 45), text, font=sf, fill=t["muted"], anchor="rm")
    # название
    tx = MARGIN
    if avatar:
        _avatar(img, (MARGIN + 28, 100), 28, title, photo, font)
        tx = MARGIN + 72
        d = ImageDraw.Draw(img)
    name = str(title).strip() if title is not None and str(title).strip() else "Канал"
    tf = _fit(d, name, font, True, 42, W - MARGIN - tx, 26)
    d.text((tx, 116), _clip(d, name, tf, W - MARGIN - tx), font=tf, fill=t["text"], anchor="ls")


def _footer(img, t, font, note=None, credit=None):
    """Подпись внизу слева (что значат цифры, автор фото). Правый нижний угол не трогаем — он для знака."""
    text = "  ·  ".join(x for x in (note, credit) if x)
    if not text:
        return
    d = ImageDraw.Draw(img)
    f = font(False, 15)
    d.text((MARGIN, H - 30), _clip(d, text, f, watermark_box(SIZE)[0] - MARGIN - 24), font=f, fill=t["muted"],
           anchor="lm")


def _tile(img, box, t, font, label, value, foot=None, accent=PURPLE, foot_color=None, value_color=None):
    """Плитка с одной цифрой: подпись, крупное число, пояснение снизу; цветная черта слева сверху."""
    x0, y0, x1, y1 = _box(box)
    _panel(img, box, t, 22)
    oy = y0 + max(0, (y1 - y0 - 140) // 2)
    _fill(img, (x0 + 22, oy + 20, x0 + 62, oy + 25), accent + (255,), 3)
    d = ImageDraw.Draw(img)
    d.text((x0 + 22, oy + 50), _clip(d, label, font(False, 17), x1 - x0 - 44), font=font(False, 17),
           fill=t["muted"], anchor="ls")
    value = str(value)
    vf = _fit(d, value, font, True, 40, x1 - x0 - 44, 20)
    d.text((x0 + 22, oy + 96), value, font=vf, fill=value_color or t["text"], anchor="ls")
    if foot:
        ff = font(False, 15)
        d.text((x0 + 22, oy + 124), _clip(d, foot, ff, x1 - x0 - 44), font=ff, fill=foot_color or t["muted"],
               anchor="ls")


def _panel_title(img, t, font, x, y, title, sub=None):
    d = ImageDraw.Draw(img)
    d.text((x, y), title, font=font(True, 22), fill=t["text"], anchor="ls")
    if sub:
        d.text((x, y + 26), sub, font=font(False, 16), fill=t["muted"], anchor="ls")


def _area(img, pts, y_base, color, a_top=110, a_bottom=0):
    """Заливка под линией графика — сверху ярче, к низу тает."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0 = int(min(xs)), int(math.ceil(max(xs))), int(min(ys))
    w, h = x1 - x0 + 1, int(y_base) - y0 + 1
    if w < 2 or h < 2:
        return
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon([(x - x0, y - y0) for x, y in pts] + [(pts[-1][0] - x0, h), (pts[0][0] - x0, h)],
                                 fill=255)
    fade = Image.linear_gradient("L").resize((w, h)).point(lambda v: int(a_top + (a_bottom - a_top) * v / 255))
    layer = Image.new("RGBA", (w, h), color + (0,))
    layer.putalpha(ImageChops.multiply(mask, fade))
    img.alpha_composite(layer, (x0, y0))


def _chart(img, box, t, font, vals, labels=None, empty="", value_fmt=None):
    """Линейный график: сетка, плавная фиолетовая линия с заливкой, последняя точка — розовая, с подписью."""
    x0, y0, x1, y1 = _box(box)
    d = ImageDraw.Draw(img)
    value_fmt = value_fmt or fmt_int
    vals = [num(v) for v in vals or ()]
    step = (x1 - x0) / max(1, len(vals) - 1)
    for i, lab in enumerate(labels or ()):
        if lab:
            d.text((x0 + i * step, y1 + 30), str(lab), font=font(False, 15), fill=t["muted"], anchor="mm")
    good = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(good) < 2:
        for k in range(3):
            _fill(img, (x0, y0 + (y1 - y0) * k // 2, x1, y0 + (y1 - y0) * k // 2 + 1), t["grid"])
        d = ImageDraw.Draw(img)
        if good:
            sm = _Smooth(img, (x0 - 20, y0 - 20, x1 + 20, y1 + 20))
            sm.circle((x0 + good[0][0] * step, (y0 + y1) / 2), 8, HOTPINK)
            sm.done()
            d = ImageDraw.Draw(img)
        lines = str(empty).split("\n")
        for k, line in enumerate(lines):
            d.text(((x0 + x1) / 2, (y0 + y1) / 2 + 40 + k * 24 - (len(lines) - 1) * 12), line,
                   font=font(False, 17), fill=t["muted"], anchor="mm")
        return
    lo, hi = min(v for _, v in good), max(v for _, v in good)
    pad = max((hi - lo) * 0.2, abs(hi) * 0.002, 1)
    lo, hi = lo - pad, hi + pad
    for k in range(4):                                  # сетка и подписи слева
        yy = y0 + (y1 - y0) * k / 3
        _fill(img, (x0, int(yy), x1, int(yy) + 1), t["grid"])
        d = ImageDraw.Draw(img)
        d.text((x0 - 14, yy), fmt_short(hi - (hi - lo) * k / 3), font=font(False, 14), fill=t["faint"], anchor="rm")
    pts = [(x0 + i * step, y1 - (v - lo) / (hi - lo) * (y1 - y0)) for i, v in good]
    curve = [(x, min(y1, max(y0, y))) for x, y in _smooth(pts)]
    _area(img, curve, y1, PURPLE, 120 if t["name"] == "dark" else 80, 0)
    sm = _Smooth(img, (x0 - 24, y0 - 24, x1 + 24, y1 + 24))
    sm.line(curve, PURPLE, 4)
    for p in pts[:-1]:
        sm.circle(p, 6, t["dot"])
        sm.circle(p, 4, PURPLE)
    last = pts[-1]
    sm.circle(last, 15, HOTPINK + (70,))
    sm.circle(last, 8, HOTPINK)
    sm.circle(last, 3.5, WHITE)
    sm.done()
    label = value_fmt(good[-1][1])
    d = ImageDraw.Draw(img)
    lf = font(True, 17)
    lw = int(d.textlength(label, font=lf)) + 24
    bx = int(min(x1 + 10 - lw, max(x0, last[0] - lw / 2)))
    by = int(last[1] - 50 if last[1] - 50 > y0 - 30 else last[1] + 22)
    _fill(img, (bx, by, bx + lw, by + 30), HOTPINK + (255,), 15)
    ImageDraw.Draw(img).text((bx + lw / 2, by + 15), label, font=lf, fill=WHITE, anchor="mm")


def _bar(img, box, frac, t, color=None):
    """Полоса: дорожка и заливка на долю frac (цветом или градиентом)."""
    x0, y0, x1, y1 = _box(box)
    r = (y1 - y0) // 2
    _fill(img, (x0, y0, x1, y1), t["track"], r)
    frac = num(frac)
    if frac is None or frac <= 0:
        return
    xe = x0 + max(y1 - y0, int((x1 - x0) * min(1.0, frac)))
    if color is None:
        _grad_fill(img, (x0, y0, xe, y1), r)
    else:
        _fill(img, (x0, y0, xe, y1), color + (255,), r)


def _ring(img, center, r, width, frac, t):
    """Кольцо-шкала: дорожка и дуга от фиолетового к розовому (доля frac), с круглыми концами."""
    cx, cy = center
    sm = _Smooth(img, (cx - r - width, cy - r - width, cx + r + width, cy + r + width))
    sm.arc(center, r, 0, 360, t["track"], width)
    frac = num(frac)
    if frac is not None and frac > 0:
        frac = min(1.0, frac)
        n = max(2, int(90 * frac))
        for i in range(n):
            a0 = -90 + 360 * frac * i / n
            sm.arc(center, r, a0, a0 + 360 * frac / n + 0.8, _mix(PURPLE, HOTPINK, i / (n - 1)), width)
        for ang, col in ((-90, PURPLE), (-90 + 360 * frac, HOTPINK)):
            sm.circle((cx + r * math.cos(math.radians(ang)), cy + r * math.sin(math.radians(ang))), width / 2, col)
    sm.done()


def _chip(img, xy, text, t, font, color=None, size=15):
    """Пилюля с короткой подписью (розовая — хорошее, серая — нет данных). xy — левый край и середина."""
    d = ImageDraw.Draw(img)
    f = font(True, size)
    color = color or HOTPINK
    w = int(d.textlength(text, font=f)) + 24
    x, cy = int(xy[0]), int(xy[1])
    _fill(img, (x, cy - 15, x + w, cy + 15), color + (40 if t["name"] == "dark" else 30,), 15)
    ImageDraw.Draw(img).text((x + 12, cy), text, font=f, fill=color, anchor="lm")
    return w


def _delta(n):
    """Прирост для картинки: «+12» / «–7». Во встроенном шрифте бота нет знака «−» (рисовался пустым местом),
    поэтому на картинках минус — короткое тире."""
    return fmt_delta(n).replace("−", "–")


def _delta_color(v, t):
    v = num(v)
    return t["muted"] if v is None else (HOTPINK if v >= 0 else PURPLE)


# ---------- 📈 канал ----------
def render_channel(n, font=None, bg=None, photo=None, theme=None, now=None, credit=None):
    """Аналитика канала по channel_numbers(...): подписчики и прирост, ER24, график за 7 дней, просмотры на пост."""
    font = font or default_font
    n = n if isinstance(n, dict) else {}
    now = _now(now, n)
    t = _theme(theme, now)
    img = _canvas(t, bg, seed_of(n.get("title")))
    _header(img, t, font, "Аналитика канала", n.get("title"), now, photo, avatar=True)
    top, gap = 144, 20
    tw = (W - 2 * MARGIN - 3 * gap) // 4
    today = num(n.get("today"))
    tiles = (("Подписчики", fmt_big(n.get("subs")), f"сегодня {_delta(today)}" if today is not None
              else "сегодня — нет данных", PURPLE, _delta_color(today, t)),
             ("За 7 дней", _delta(n.get("d7")), "прирост подписчиков", HOTPINK, None),
             ("За 30 дней", _delta(n.get("d30")), "прирост подписчиков" if num(n.get("d30")) is not None
              else "история копится", PURPLE, None),
             ("ER24", fmt_pct(n.get("er24")), "просмотры / подписчики", HOTPINK, None))
    for i, (label, value, foot, accent, fc) in enumerate(tiles):
        x = MARGIN + i * (tw + gap)
        _tile(img, (x, top, x + tw, top + 140), t, font, label, value, foot, accent, fc)
    y0, y1 = 304, 724
    split = MARGIN + 2 * tw + gap
    _panel(img, (MARGIN, y0, split + tw // 2, y1), t, 26)
    _panel_title(img, t, font, MARGIN + 28, y0 + 42, "Динамика подписчиков", "последние 7 дней")
    days = [x for x in (n.get("days") or ()) if isinstance(x, (tuple, list)) and len(x) == 3]
    _chart(img, (MARGIN + 96, y0 + 100, split + tw // 2 - 40, y1 - 70), t, font, [v for _, v, _ in days],
           [f"{k[8:10]}.{k[5:7]}" if isinstance(k, str) and len(k) >= 10 else "" for k, _, _ in days],
           "История копится: бот раз в день запоминает число подписчиков")
    px = split + tw // 2 + gap
    _panel(img, (px, y0, W - MARGIN, y1), t, 26)
    _panel_title(img, t, font, px + 28, y0 + 42, "Просмотры на пост", "в среднем за первые часы")
    vals = [num(n.get(f"v{h}")) for h in (24, 48, 72)]
    top_v = max([v for v in vals if v is not None] or [0]) or 1
    d = ImageDraw.Draw(img)
    for i, (h, v) in enumerate(zip((24, 48, 72), vals)):
        yy = y0 + 150 + i * 96
        d.text((px + 28, yy), f"{h} часа" if h < 48 else f"{h} часов", font=font(False, 18), fill=t["muted"],
               anchor="ls")
        val = fmt_big(v)
        d.text((W - MARGIN - 28, yy + 2), val, font=_fit(d, val, font, True, 30, 200), fill=t["text"], anchor="rs")
        _bar(img, (px + 28, yy + 18, W - MARGIN - 28, yy + 30), (v or 0) / top_v, t, None if i == 0 else PURPLE)
        d = ImageDraw.Draw(img)
    _footer(img, t, font, "Данные: MAX · 24 / 48 / 72 ч — средние просмотры поста", credit)
    _watermark(img, t, font)
    return img.convert("RGB")


# ---------- 🌐 сеть каналов ----------
def render_network(net, font=None, bg=None, theme=None, now=None, credit=None,
                   title="Статистика по всей сетке каналов"):
    """Сводка по сети: подписчики, просмотры за сутки, общий ER, число каналов; просмотры на пост; топ-3 канала."""
    font = font or default_font
    net = net if isinstance(net, dict) else {}
    now = _now(now, net)
    t = _theme(theme, now)
    img = _canvas(t, bg, seed_of(title))
    _header(img, t, font, "Сеть каналов", title, now)
    count = int(num(net.get("n")) or 0)
    top, gap = 144, 20
    tw = (W - 2 * MARGIN - 3 * gap) // 4
    tiles = (("Подписчиков в сети", fmt_short(net.get("subs")), "все каналы вместе", PURPLE),
             ("Просмотров за сутки", fmt_short(net.get("v24")), "посты последних 24 ч", HOTPINK),
             ("Общий ER", fmt_pct(net.get("er")), "просмотры / подписчики", PURPLE),
             ("Каналов", fmt_int(count), _ru(count, "канал в сети", "канала в сети", "каналов в сети"), HOTPINK))
    for i, (label, value, foot, accent) in enumerate(tiles):
        x = MARGIN + i * (tw + gap)
        _tile(img, (x, top, x + tw, top + 140), t, font, label, value, foot, accent)
    y0, y1 = 304, 724
    lx1 = MARGIN + tw + gap + tw // 2
    _panel(img, (MARGIN, y0, lx1, y1), t, 26)
    _panel_title(img, t, font, MARGIN + 28, y0 + 42, "Просмотры на пост", "вся сеть, сумма")
    vals = [num(net.get(f"v{h}")) for h in (24, 48, 72)]
    top_v = max([v for v in vals if v is not None] or [0]) or 1
    d = ImageDraw.Draw(img)
    for i, (h, v) in enumerate(zip((24, 48, 72), vals)):
        yy = y0 + 150 + i * 96
        d.text((MARGIN + 28, yy), f"{h} часа" if h < 48 else f"{h} часов", font=font(False, 18), fill=t["muted"],
               anchor="ls")
        d.text((lx1 - 28, yy + 2), fmt_short(v), font=font(True, 30), fill=t["text"], anchor="rs")
        _bar(img, (MARGIN + 28, yy + 18, lx1 - 28, yy + 30), (v or 0) / top_v, t, None if i == 0 else PURPLE)
        d = ImageDraw.Draw(img)
    rx = lx1 + gap
    _panel(img, (rx, y0, W - MARGIN, y1), t, 26)
    _panel_title(img, t, font, rx + 28, y0 + 42, "Топ-3 канала", "по числу подписчиков")
    d = ImageDraw.Draw(img)
    d.text((W - MARGIN - 190, y0 + 92), "Подписчики", font=font(False, 15), fill=t["faint"], anchor="rs")
    d.text((W - MARGIN - 28, y0 + 92), "За сутки", font=font(False, 15), fill=t["faint"], anchor="rs")
    rows = [r for r in (net.get("top") or ()) if isinstance(r, (tuple, list)) and len(r) == 3][:3]
    best = max([num(s) or 0 for _, s, _ in rows] or [0]) or 1
    for i, (name, subs, views) in enumerate(rows):
        yy = y0 + 110 + i * 96
        _fill(img, (rx + 20, yy, W - MARGIN - 20, yy + 80), t["grid"], 18)
        _avatar(img, (rx + 60, yy + 34), 22, name, None, font)
        d = ImageDraw.Draw(img)
        nf = font(True, 20)
        d.text((rx + 96, yy + 34), _clip(d, name, nf, W - MARGIN - 230 - rx - 96), font=nf, fill=t["text"],
               anchor="lm")
        d.text((W - MARGIN - 190, yy + 34), fmt_short(subs), font=font(True, 22), fill=t["text"], anchor="rm")
        d.text((W - MARGIN - 40, yy + 34), fmt_short(views), font=font(True, 22), fill=HOTPINK, anchor="rm")
        _bar(img, (rx + 96, yy + 58, W - MARGIN - 40, yy + 64), (num(subs) or 0) / best, t,
             None if i == 0 else PURPLE)
    if not rows:
        d.text(((rx + W - MARGIN) / 2, (y0 + y1) / 2), "Каналов пока нет", font=font(False, 18), fill=t["muted"],
               anchor="mm")
    _footer(img, t, font, "Данные: MAX · ER — просмотры за сутки к подписчикам", credit)
    _watermark(img, t, font)
    return img.convert("RGB")


# ---------- 🔮 прогноз поста ----------
def render_predict(p, font=None, bg=None, channel=None, theme=None, now=None, credit=None):
    """Прогноз поста по predict(...): оценка из 10 (кольцо), ожидаемые просмотры, лучшее время, советы."""
    font = font or default_font
    p = p if isinstance(p, dict) else {}
    now = _now(now)
    t = _theme(theme, now)
    name = str(channel).strip() if channel and str(channel).strip() else "Черновик поста"
    img = _canvas(t, bg, seed_of(name))
    _header(img, t, font, "Прогноз поста", name, now)
    y0, y1, gap = 144, 504, 20
    cw = (W - 2 * MARGIN - 2 * gap) // 3
    # оценка — кольцо
    _panel(img, (MARGIN, y0, MARGIN + cw, y1), t, 26)
    _panel_title(img, t, font, MARGIN + 28, y0 + 42, "Оценка поста")
    score = num(p.get("score"))
    score = max(0, min(10, int(round(score)))) if score is not None else None
    cx, cy = MARGIN + cw // 2, y0 + 205
    _ring(img, (cx, cy), 100, 18, (score or 0) / 10, t)
    d = ImageDraw.Draw(img)
    d.text((cx, cy - 6), "—" if score is None else str(score), font=font(True, 72), fill=t["text"], anchor="mm")
    d.text((cx, cy + 44), "из 10", font=font(False, 18), fill=t["muted"], anchor="mm")
    # ожидаемые просмотры
    x = MARGIN + cw + gap
    _panel(img, (x, y0, x + cw, y1), t, 26)
    _panel_title(img, t, font, x + 28, y0 + 42, "Ожидаемые просмотры")
    reach = p.get("reach")
    d = ImageDraw.Draw(img)
    if isinstance(reach, (tuple, list)) and len(reach) == 2 and None not in (num(reach[0]), num(reach[1])):
        rv = f"{fmt_short(reach[0])}–{fmt_short(reach[1])}"
        _grad_text(img, (x + 28, y0 + 214), rv, _fit(d, rv, font, True, 64, cw - 56, 24))
        d = ImageDraw.Draw(img)
        d.text((x + 28, y0 + 254), "просмотров за первые сутки", font=font(False, 17), fill=t["muted"], anchor="ls")
        if num(p.get("median")) is not None:
            _chip(img, (x + 28, y1 - 56), f"обычно у канала — {fmt_int(p['median'])}", t, font, PURPLE)
    else:
        d.text((x + 28, y0 + 214), "—", font=font(True, 64), fill=t["text"], anchor="ls")
        d.text((x + 28, y0 + 254), "нет истории канала", font=font(False, 17), fill=t["muted"], anchor="ls")
    # лучшее время
    x = MARGIN + 2 * (cw + gap)
    _panel(img, (x, y0, W - MARGIN, y1), t, 26)
    _panel_title(img, t, font, x + 28, y0 + 42, "Лучшее время")
    best = p.get("best") if isinstance(p.get("best"), (tuple, list)) and len(p.get("best")) == 2 else (None, None)
    day = best[0] if isinstance(best[0], int) and not isinstance(best[0], bool) and 0 <= best[0] < 7 else None
    hour = best[1] if isinstance(best[1], int) and not isinstance(best[1], bool) and 0 <= best[1] < 24 else None
    when = ", ".join(x_ for x_ in (WEEK[day] if day is not None else "",
                                   f"{hour:02d}:00" if hour is not None else "") if x_) or "—"
    d = ImageDraw.Draw(img)
    d.text((x + 28, y0 + 214), when, font=_fit(d, when, font, True, 60, W - MARGIN - x - 56, 24), fill=t["text"],
           anchor="ls")
    d.text((x + 28, y0 + 254), "по прошлым постам канала", font=font(False, 17), fill=t["muted"], anchor="ls")
    _chip(img, (x + 28, y1 - 56), tz_label(now), t, font, HOTPINK)
    # советы
    _panel(img, (MARGIN, y1 + gap, W - MARGIN, 724), t, 26)
    tips = [str(s) for s in (p.get("tips") or ()) if s][:3]
    why = [str(s) for s in (p.get("why") or ()) if s][:3]
    _panel_title(img, t, font, MARGIN + 28, y1 + gap + 46, "Как сделать лучше" if tips else "Почему так")
    d = ImageDraw.Draw(img)
    tf = font(False, 19)
    for i, tip in enumerate(tips or why or ["Пост выглядит хорошо — можно выкладывать"]):
        yy = y1 + gap + 90 + i * 40
        _fill(img, (MARGIN + 30, yy - 6, MARGIN + 42, yy + 6), (HOTPINK if i == 0 else PURPLE) + (255,), 6)
        d = ImageDraw.Draw(img)
        d.text((MARGIN + 60, yy), _clip(d, tip, tf, W - 2 * MARGIN - 90), font=tf, fill=t["text"], anchor="lm")
    count = int(num(p.get("n")) or 0)
    _footer(img, t, font, f"Прогноз по {count} {_ru(count, 'посту', 'постам', 'постам')} канала" if count
            else "Прогноз по самому тексту — истории канала нет", credit)
    _watermark(img, t, font)
    return img.convert("RGB")


# ---------- ⚡ оценка потенциала ----------
_PARTS = (("growth", "Рост", "прирост подписчиков за неделю"), ("engagement", "Вовлечённость", "просмотры к подписчикам"),
          ("activity", "Регулярность", "как часто выходят посты"), ("reach", "Охват", "размер аудитории"))


def render_potential(d_, font=None, bg=None, theme=None, now=None, credit=None):
    """Оценка потенциала канала (0–100): кольцо с оценкой, из чего она сложилась, приток и отток подписчиков."""
    font = font or default_font
    d_ = d_ if isinstance(d_, dict) else {}
    now = _now(now, d_)
    t = _theme(theme, now)
    title = d_.get("title")
    img = _canvas(t, bg, seed_of(title))
    _header(img, t, font, "Оценка потенциала", title, now)
    y0, y1, gap = 144, 724, 20
    lw = 440
    _panel(img, (MARGIN, y0, MARGIN + lw, y1), t, 26)
    _panel_title(img, t, font, MARGIN + 28, y0 + 42, "Потенциал канала")
    score = num(d_.get("score"))
    score = max(0, min(100, int(round(score)))) if score is not None else None
    cx, cy = MARGIN + lw // 2, y0 + 250
    _ring(img, (cx, cy), 130, 22, (score or 0) / 100, t)
    d = ImageDraw.Draw(img)
    d.text((cx, cy - 10), "—" if score is None else str(score), font=font(True, 96), fill=t["text"], anchor="mm")
    d.text((cx, cy + 58), "из 100", font=font(False, 20), fill=t["muted"], anchor="mm")
    label = str(d_.get("label") or "").strip()
    text = f"{label} потенциал" if label else "Мало данных для оценки"
    lf = font(True, 24)
    if label:
        _grad_text(img, (cx, y1 - 92), text, lf, "mm")
    else:
        ImageDraw.Draw(img).text((cx, y1 - 92), text, font=lf, fill=t["muted"], anchor="mm")
    notes = [str(s) for s in (d_.get("notes") or ()) if s][:1]
    if notes:
        d = ImageDraw.Draw(img)
        nf = font(False, 15)
        d.text((cx, y1 - 52), _clip(d, notes[0], nf, lw - 48), font=nf, fill=t["faint"], anchor="mm")
    # из чего оценка
    rx = MARGIN + lw + gap
    _panel(img, (rx, y0, W - MARGIN, 500), t, 26)
    _panel_title(img, t, font, rx + 28, y0 + 42, "Из чего оценка", "каждая часть — от 0 до 100")
    parts = d_.get("parts") if isinstance(d_.get("parts"), dict) else {}
    for i, (key, name, hint) in enumerate(_PARTS):
        yy = y0 + 122 + i * 64
        v = num(parts.get(key))
        d = ImageDraw.Draw(img)
        d.text((rx + 28, yy), name, font=font(True, 19), fill=t["text"], anchor="ls")
        d.text((rx + 200, yy), hint, font=font(False, 15), fill=t["faint"], anchor="ls")
        d.text((W - MARGIN - 28, yy), "нет данных" if v is None else str(int(round(v))),
               font=font(True, 20) if v is not None else font(False, 16),
               fill=t["text"] if v is not None else t["muted"], anchor="rs")
        _bar(img, (rx + 28, yy + 14, W - MARGIN - 28, yy + 24), None if v is None else v / 100, t,
             None if i % 2 == 0 else PURPLE)
    # приток и отток
    bw = (W - MARGIN - rx - 2 * gap) // 3
    joins, leaves, total = num(d_.get("joins")), num(d_.get("leaves")), num(d_.get("total"))
    cells = (("Подписчики", fmt_big(d_.get("subs")), "сейчас в канале", PURPLE),
             ("Пришло / ушло", f"+{fmt_int(joins)} / –{fmt_int(leaves)}" if None not in (joins, leaves) else "—",
              "пока бот следит", HOTPINK),
             ("Итого", _delta(total), "чистый прирост", PURPLE))
    for i, (label_, value, foot, accent) in enumerate(cells):
        x = rx + i * (bw + gap)
        _tile(img, (x, 520, x + bw, y1), t, font, label_, value, foot, accent,
              value_color=_delta_color(total, t) if i == 2 and total is not None else None)
    _footer(img, t, font, "Оценка — по открытым данным канала: рост, просмотры, частота постов, аудитория", credit)
    _watermark(img, t, font)
    return img.convert("RGB")


# ---------- 📊 рекламный охват (со знаком Reloader) ----------
def render_ad_reach(d_, font=None, bg=None, theme=None, now=None, credit=None):
    """Рекламный охват канала: сколько просмотров наберёт рекламный пост (вилка по медиане последних постов),
    подписчики, ER, динамика и приток/отток. Внизу справа, как на всех картинках, — водяной знак «Reloader»."""
    font = font or default_font
    d_ = d_ if isinstance(d_, dict) else {}
    now = _now(now, d_)
    t = _theme(theme, now)
    title = d_.get("title")
    img = _canvas(t, bg, seed_of(title))
    _header(img, t, font, "Рекламный охват", title, now)
    y0, gap = 144, 20
    hx1 = 800
    _panel(img, (MARGIN, y0, hx1, 444), t, 26)
    _panel_title(img, t, font, MARGIN + 28, y0 + 42, "Охват рекламного поста", "сколько просмотров наберёт пост")
    low, high, med = num(d_.get("low")), num(d_.get("high")), num(d_.get("median"))
    d = ImageDraw.Draw(img)
    if low is not None and high is not None:
        rv = f"{fmt_short(low)} – {fmt_short(high)}"
        _grad_text(img, (MARGIN + 28, y0 + 188), rv, _fit(d, rv, font, True, 92, hx1 - MARGIN - 56, 28))
        posts = int(num(d_.get("posts")) or 0)
        d = ImageDraw.Draw(img)
        d.text((MARGIN + 28, y0 + 226), "просмотров за первые сутки", font=font(False, 18), fill=t["muted"],
               anchor="ls")
        cw_ = _chip(img, (MARGIN + 28, 444 - 38), f"медиана {fmt_int(med)}", t, font, PURPLE)
        if posts:
            _chip(img, (MARGIN + 40 + cw_, 444 - 38), f"по {posts} {_ru(posts, 'посту', 'постам', 'постам')}", t,
                  font, HOTPINK)
    else:
        d.text((MARGIN + 28, y0 + 188), "—", font=font(True, 92), fill=t["text"], anchor="ls")
        d.text((MARGIN + 28, y0 + 226), "просмотров постов не видно — охват не посчитать", font=font(False, 18),
               fill=t["muted"], anchor="ls")
    tx = hx1 + gap
    th = (444 - y0 - gap) // 2
    _tile(img, (tx, y0, W - MARGIN, y0 + th), t, font, "Подписчики", fmt_big(d_.get("subs")), "сейчас в канале",
          PURPLE)
    _tile(img, (tx, y0 + th + gap, W - MARGIN, 444), t, font, "ER", fmt_pct(d_.get("er")),
          "медиана просмотров / подписчики", HOTPINK)
    y1 = 724
    _panel(img, (MARGIN, 464, hx1, y1), t, 26)
    _panel_title(img, t, font, MARGIN + 28, 464 + 42, "Подписчики", "пока бот следит за каналом")
    series = [num(v) for v in (d_.get("series") or ()) if num(v) is not None][-60:]
    _chart(img, (MARGIN + 96, 464 + 104, hx1 - 40, y1 - 34), t, font, series, None,
           "История копится — бот смотрит число подписчиков раз в час", fmt_big)
    _panel(img, (tx, 464, W - MARGIN, y1), t, 26)
    _panel_title(img, t, font, tx + 28, 464 + 42, "Приток и отток")
    joins, leaves, total = num(d_.get("joins")), num(d_.get("leaves")), num(d_.get("total"))
    most = max(joins or 0, leaves or 0) or 1
    d = ImageDraw.Draw(img)
    for i, (name, v, color, sign) in enumerate((("Подписалось", joins, HOTPINK, "+"), ("Отписалось", leaves, PURPLE, "–"))):
        yy = 464 + 98 + i * 62
        d.text((tx + 28, yy), name, font=font(False, 17), fill=t["muted"], anchor="ls")
        d.text((W - MARGIN - 28, yy), "—" if v is None else f"{sign}{fmt_int(v)}", font=font(True, 22),
               fill=t["text"], anchor="rs")
        _bar(img, (tx + 28, yy + 14, W - MARGIN - 28, yy + 24), (v or 0) / most, t, color)
        d = ImageDraw.Draw(img)
    d.text((tx + 28, y1 - 34), "Итого", font=font(True, 20), fill=t["text"], anchor="ls")
    d.text((W - MARGIN - 28, y1 - 34), _delta(total), font=font(True, 26), fill=_delta_color(total, t),
           anchor="rs")
    _footer(img, t, font, "Вилка — 0,8–1,1 медианы просмотров последних постов", credit)
    _watermark(img, t, font)
    return img.convert("RGB")


def _ru(n, one, few, many):
    n = abs(int(n)) % 100
    return one if n % 10 == 1 and n != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n <= 14 else many
