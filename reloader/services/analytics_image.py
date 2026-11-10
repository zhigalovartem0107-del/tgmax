"""Премиум-карточки аналитики MAX-каналов (Pillow) — строго в четырёх цветах.

Канал: подписчики и прирост (сегодня / 7 / 30 дней), график роста, таблица по дням, просмотры на пост
за 24/48/72 часа и ER24. Сеть каналов: всего подписчиков и просмотров, общий ER, топ-3 канала. Прогноз поста:
оценка из 10, ожидаемые просмотры, лучшее время, советы.
Палитра: фон #0A0A0A, линии и заголовки #9B30FF, цифры и акценты #FF1493, текст #FFFFFF — других цветов нет.
Картинка-фон («узор» или рисунок ИИ) и фото канала перекрашиваются в эту палитру (фон — приглушённый
фиолетовый по чёрному, фото — оттенки белого по чёрному). Только подсчёт и рисование — без сети и без состояния
бота (данные приходят готовыми), поэтому легко проверяется тестами."""
import math
import random
import time
import zlib

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

BG = (0x0A, 0x0A, 0x0A)         # #0A0A0A — фон: глубокий чёрный
PURPLE = (0x9B, 0x30, 0xFF)     # #9B30FF — линии, рамки, заголовки
HOTPINK = (0xFF, 0x14, 0x93)    # #FF1493 — цифры и акценты
WHITE = (0xFF, 0xFF, 0xFF)      # #FFFFFF — текст
PALETTE = (BG, PURPLE, HOTPINK, WHITE)
BG_TINT = 0.30                  # картинка-фон: светлые места — не ярче 30% фиолетового по чёрному
WEEK = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
MONTHS = ("янв.", "февр.", "марта", "апр.", "мая", "июня", "июля", "авг.", "сент.", "окт.", "нояб.", "дек.")
DAY = 86400


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


def _card(img, box, radius=28, alpha=255, outline=PURPLE, width=2):
    """Панель: чёрная, в фиолетовой рамке (выделение — в розовой)."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(box, radius, fill=BG + (alpha,), outline=outline + (255,), width=width)
    img.alpha_composite(layer)


def _text(d, xy, text, font, fill, anchor="la"):
    d.text(xy, str(text), font=font, fill=fill, anchor=anchor)


def _fit(d, text, font_fn, bold, size, width):
    """Шрифт, при котором текст влезает в ширину (длинное название канала)."""
    while size > 14:
        f = font_fn(bold, size)
        if d.textlength(str(text), font=f) <= width:
            return f
        size -= 2
    return font_fn(bold, size)


def _clip(d, text, font, width):
    """Строка, обрезанная по ширине (с «…»)."""
    text = str(text)
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _duotone(pic, light):
    """Любая картинка → оттенки от #0A0A0A до цвета light (между ними — только смеси): чужих цветов нет."""
    return ImageOps.colorize(ImageOps.grayscale(pic.convert("RGB")), black=BG, white=light)


def _avatar(img, center, r, title, photo=None):
    x, y = center
    if photo is not None:
        ph = _duotone(photo, WHITE).convert("RGBA").resize((2 * r, 2 * r))   # фото канала — оттенками белого
        mask = Image.new("L", (2 * r, 2 * r), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=255)
        img.paste(ph, (x - r, y - r), mask)
        return
    d = ImageDraw.Draw(img)
    d.ellipse((x - r, y - r, x + r, y + r), fill=PURPLE)
    letter = (str(title or "?").strip()[:1] or "?").upper()
    _text(d, (x, y), letter, default_font(True, r), WHITE, "mm")


# ---------- фоны ----------
def seed_of(text):
    return zlib.crc32(str(text or "").encode("utf-8"))


def procedural_background(w, h, seed, style="aurora"):
    """Свой фон у каждого канала (seed — от названия): «aurora» — мягкие пятна, «waves» — волны. На карточке он
    перекрашивается в палитру (_page) — здесь узор, цвета не важны."""
    w, h = max(1, int(num(w) or 1)), max(1, int(num(h) or 1))    # кривые размеры — не падаем
    rnd = random.Random(seed)
    palettes = [((24, 20, 64), (88, 86, 214), (236, 72, 153)), ((10, 30, 60), (34, 139, 230), (45, 212, 191)),
                ((40, 16, 60), (168, 85, 247), (251, 146, 60)), ((12, 38, 32), (16, 185, 129), (250, 204, 21))]
    base, c1, c2 = palettes[rnd.randrange(len(palettes))]
    img = Image.new("RGB", (w, h), base)
    d = ImageDraw.Draw(img)
    for y in range(h):                                   # вертикальный градиент
        k = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(base[i] * (1 - k) + c1[i] * k * 0.35) for i in range(3)))
    layer = Image.new("RGB", (w, h), (0, 0, 0))
    ld = ImageDraw.Draw(layer)
    if style == "waves":
        for n in range(6):
            amp, ph, yb = rnd.uniform(18, 60), rnd.uniform(0, 6.28), h * (0.35 + 0.1 * n)
            col = c1 if n % 2 else c2
            fr = rnd.uniform(0.8, 1.6)
            pts = [(x, yb + amp * math.sin(x / w * 6.28 * fr + ph)) for x in range(0, w + 8, 8)]
            ld.polygon(pts + [(w, h), (0, h)], fill=tuple(int(v * 0.5) for v in col))
        layer = layer.filter(ImageFilter.GaussianBlur(10))
    else:
        for _ in range(9):
            r = rnd.uniform(0.15, 0.4) * w
            x, y = rnd.uniform(0, w), rnd.uniform(0, h)
            ld.ellipse((x - r, y - r, x + r, y + r), fill=rnd.choice((c1, c2)))
        layer = layer.filter(ImageFilter.GaussianBlur(w // 12))
    img = Image.blend(img, layer, 0.55)
    noise = Image.frombytes("L", (w, h), rnd.getrandbits(8 * w * h).to_bytes(w * h, "little")).convert("RGB")   # лёгкое зерно — «живой» фон
    return Image.blend(img, noise, 0.03)                       # (от того же seed — тот же фон)


def _page(w, h, bg):
    """Холст: глубокий чёрный; с картинкой-фоном — она растянута и перекрашена в приглушённый фиолетовый по
    чёрному (узор или рисунок ИИ не приносит своих цветов)."""
    if bg is None:
        return Image.new("RGBA", (w, h), BG + (255,)), False
    pic = bg.convert("RGB")
    k = max(w / pic.width, h / pic.height)
    pic = pic.resize((max(w, int(pic.width * k)), max(h, int(pic.height * k))))
    left, top = (pic.width - w) // 2, (pic.height - h) // 2
    return _duotone(pic.crop((left, top, left + w, top + h)), _mix(BG, PURPLE, BG_TINT)).convert("RGBA"), True


# ---------- карточка канала ----------
def render_channel(n, font=None, bg=None, photo=None, brand="Reloader"):
    """Картинка 1200×900 по channel_numbers(...)."""
    font = font or default_font
    W, H = 1200, 900
    img, art = _page(W, H, bg)
    a = 235 if art else 255
    _card(img, (32, 28, W - 32, 136), 34, a)
    _avatar(img, (92, 82), 38, n["title"], photo)
    d = ImageDraw.Draw(img)
    _text(d, (152, 44), "СТАТИСТИКА КАНАЛА", font(True, 17), PURPLE)
    _text(d, (150, 66), n["title"], _fit(d, n["title"], font, True, 38, W - 220), WHITE)
    # подписчики
    _card(img, (32, 160, 448, 424), 28, a)
    d = ImageDraw.Draw(img)
    _text(d, (60, 190), "Подписчики", font(True, 26), PURPLE)
    big = fmt_big(n["subs"])
    _text(d, (58, 228), big, _fit(d, big, font, True, 74, 360), HOTPINK)
    d.line([(60, 320), (420, 320)], fill=PURPLE, width=2)
    for i, (lab, val) in enumerate((("Сегодня", n["today"]), ("7 дней", n["d7"]), ("30 дней", n["d30"]))):
        x = 60 + i * 124
        _text(d, (x, 338), lab, font(False, 19), WHITE)
        _text(d, (x, 362), fmt_delta(val), font(True, 30), HOTPINK if val is not None else WHITE)
    # просмотры на пост
    _card(img, (468, 160, 864, 424), 28, a)
    d = ImageDraw.Draw(img)
    _text(d, (494, 186), "Просмотры на пост", font(True, 26), PURPLE)
    for i, h in enumerate((24, 48, 72)):
        y = 232 + i * 61
        if i == 0:
            _card(img, (484, y - 2, 848, y + 52), 14, outline=HOTPINK)      # главное — в розовой рамке
            d = ImageDraw.Draw(img)
        _text(d, (500, y + 25), f"{h} часа" if h < 48 else f"{h} часов", font(False, 22), WHITE, "lm")
        _text(d, (830, y + 25), fmt_big(n[f"v{h}"]), font(True, 32), HOTPINK, "rm")
        if i:
            d.line([(500, y - 6), (832, y - 6)], fill=PURPLE, width=1)
    # ER24
    _card(img, (884, 160, W - 32, 424), 28, a)
    d = ImageDraw.Draw(img)
    _text(d, (910, 186), "ER24", font(True, 26), PURPLE)
    er = fmt_pct(n["er24"])
    _text(d, (908, 236), er, _fit(d, er, font, True, 64, 250), HOTPINK)
    _text(d, (910, 330), "Просмотры за 24 часа", font(False, 19), WHITE)
    _text(d, (910, 356), "к числу подписчиков", font(False, 19), WHITE)
    # график
    _card(img, (32, 448, 672, 846), 28, a)
    d = ImageDraw.Draw(img)
    _text(d, (60, 470), "Динамика подписчиков", font(True, 28), PURPLE)
    _text(d, (60, 508), "Последние 7 дней", font(False, 18), WHITE)
    vals = [v for _, v, _ in n["days"] if v is not None]
    x0, x1, y0, y1 = 116, 628, 568, 770
    if vals:
        lo, hi = min(vals), max(vals)
        pad = max(1, (hi - lo) * 0.25 or hi * 0.02 or 1)
        lo, hi = lo - pad, hi + pad
        for k, frac in enumerate((1, 0.5, 0)):
            yy = y0 + (y1 - y0) * (1 - frac)
            d.line([(x0, yy), (x1, yy)], fill=PURPLE, width=1)
            _text(d, (x0 - 14, yy), fmt_int(lo + (hi - lo) * frac), font(False, 15), WHITE, "rm")
        step = (x1 - x0) / max(1, len(n["days"]) - 1)
        pts = [(x0 + i * step, y0 + (y1 - y0) * (1 - (v - lo) / (hi - lo)))
               for i, (_, v, _) in enumerate(n["days"]) if v is not None]
        if len(pts) > 1:
            area = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(area).polygon(pts + [(pts[-1][0], y1), (pts[0][0], y1)], fill=PURPLE + (48,))
            img.alpha_composite(area)
            d = ImageDraw.Draw(img)
            d.line(pts, fill=PURPLE, width=4, joint="curve")
        for p in pts:
            d.ellipse((p[0] - 6, p[1] - 6, p[0] + 6, p[1] + 6), fill=HOTPINK, outline=WHITE, width=2)
    else:
        _text(d, ((x0 + x1) / 2, (y0 + y1) / 2), "История копится: бот снимает число подписчиков раз в день",
              font(False, 18), WHITE, "mm")
    for i, (key, _, _) in enumerate(n["days"]):
        xx = x0 + i * (x1 - x0) / max(1, len(n["days"]) - 1)
        _text(d, (xx, 800), f"{key[8:10]}.{key[5:7]}", font(False, 15), WHITE, "mm")
    # по дням
    _card(img, (692, 448, W - 32, 846), 28, a)
    d = ImageDraw.Draw(img)
    _text(d, (720, 470), "По дням", font(True, 28), PURPLE)
    _text(d, (720, 524), "Дата", font(False, 17), WHITE)
    _text(d, (1010, 524), "Подписчики", font(False, 17), WHITE, "ra")
    _text(d, (1140, 524), "Прирост", font(False, 17), WHITE, "ra")
    for i, (key, val, delta) in enumerate(list(reversed(n["days"]))[:6]):
        y = 556 + i * 46
        if i == 0:
            _card(img, (708, y - 2, 1152, y + 40), 12, outline=HOTPINK)
            d = ImageDraw.Draw(img)
        _text(d, (720, y + 19), f"{key[8:10]}.{key[5:7]}.{key[:4]}", font(False, 20), WHITE, "lm")
        _text(d, (1010, y + 19), fmt_int(val), font(True, 22), HOTPINK if val is not None else WHITE, "rm")
        _text(d, (1140, y + 19), fmt_delta(delta), font(True, 21), HOTPINK if delta is not None else WHITE, "rm")
    _text(d, (34, 872), f"{brand} · срез {time.strftime('%d.%m.%Y %H:%M', time.localtime(n['now']))}",
          font(False, 15), WHITE)
    _text(d, (W - 34, 872), "24 / 48 / 72 ч — средние просмотры на пост", font(False, 15), WHITE, "ra")
    return img.convert("RGB")


# ---------- сеть каналов ----------
def render_network(net, font=None, bg=None, brand="Reloader", title="Статистика по всей сетке каналов"):
    """Картинка 1280×760 по network_numbers(...). С фоном-картинкой — фон сверху «баннером», как у реклам сетей."""
    font = font or default_font
    W, top = 1280, (300 if bg is not None else 128)
    H = top + 136 + 330 + 110                         # высота — по содержимому (с фоном — выше на баннер)
    img = Image.new("RGBA", (W, H), BG + (255,))
    if bg is not None:
        banner, _ = _page(W, 330, bg)
        img.alpha_composite(banner, (0, 0))
        d = ImageDraw.Draw(img)
        _text(d, (48, 150), "Сеть каналов MAX", font(True, 64), WHITE, "ls")
        _text(d, (50, 200), f"{net['n']} {_ru(net['n'], 'канал', 'канала', 'каналов')} · одна сеть",
              font(False, 30), WHITE, "ls")
    else:
        d = ImageDraw.Draw(img)
        _text(d, (W / 2, 70), title, _fit(d, title, font, True, 52, W - 80), PURPLE, "mm")
    _card(img, (30, top, W - 30, top + 112), 22)
    d = ImageDraw.Draw(img)
    cells = ((fmt_short(net["subs"]), "подписчиков в сети"), (fmt_short(net["v24"]), "просмотров в сутки"),
             (fmt_pct(net["er"]), "общий ER"),
             (str(net["n"]), "каналов в сети"))
    for i, (big, small) in enumerate(cells):
        x = 30 + (W - 60) * (i + 0.5) / 4
        _text(d, (x, top + 46), big, font(True, 46), HOTPINK, "mm")
        _text(d, (x, top + 90), small, font(False, 17), WHITE, "mm")
    y = top + 136
    _card(img, (30, y, 448, y + 330), 22)
    d = ImageDraw.Draw(img)
    _text(d, (239, y + 40), "Просмотры", font(True, 32), PURPLE, "mm")
    for i, h in enumerate((24, 48, 72)):
        yy = y + 84 + i * 76
        if i % 2 == 0:
            _card(img, (54, yy, 424, yy + 64), 12, width=1)
            d = ImageDraw.Draw(img)
        _text(d, (74, yy + 32), f"{h}ч:", font(False, 30), WHITE, "lm")
        _text(d, (404, yy + 32), fmt_short(net[f"v{h}"]), font(True, 34), HOTPINK, "rm")
    _card(img, (472, y, W - 30, y + 330), 22)
    d = ImageDraw.Draw(img)
    _text(d, ((472 + W - 30) / 2, y + 40), "Топ-3 канала", font(True, 32), PURPLE, "mm")
    _text(d, (500, y + 88), "Название", font(False, 20), WHITE, "lm")
    _text(d, (1050, y + 88), "Подписчики", font(False, 20), WHITE, "rm")
    _text(d, (W - 56, y + 88), "Просмотры", font(False, 20), WHITE, "rm")
    for i, (name, subs, views) in enumerate(net["top"]):
        yy = y + 112 + i * 70
        if i % 2 == 0:
            _card(img, (490, yy, W - 46, yy + 62), 12, width=1)
            d = ImageDraw.Draw(img)
        _avatar(img, (526, yy + 31), 20, name)
        d = ImageDraw.Draw(img)
        _text(d, (558, yy + 31), name, _fit(d, name, font, False, 24, 380), WHITE, "lm")
        _text(d, (1050, yy + 31), fmt_short(subs), font(True, 26), HOTPINK, "rm")
        _text(d, (W - 56, yy + 31), fmt_short(views), font(True, 26), HOTPINK, "rm")
    lt = time.localtime(net["now"])
    when = f"{lt.tm_hour:02d}:{lt.tm_min:02d}, {lt.tm_mday} {MONTHS[lt.tm_mon - 1]} {lt.tm_year} г."
    _card(img, (30, H - 70, W - 30, H - 16), 18)
    d = ImageDraw.Draw(img)
    _text(d, (56, H - 43), f"Актуально на {when}", font(False, 20), WHITE, "lm")
    _text(d, (W - 56, H - 43), f"{brand} — аналитика MAX-каналов", font(True, 20), PURPLE, "rm")
    return img.convert("RGB")


# ---------- прогноз поста ----------
def render_predict(p, font=None, bg=None, channel=None, brand="Reloader", now=None):
    """Картинка 1200×675 по predict(...): оценка из 10 (кольцо), ожидаемые просмотры, лучшее время, советы."""
    font = font or default_font
    p = p if isinstance(p, dict) else {}
    W, H = 1200, 675
    img, art = _page(W, H, bg)
    a = 235 if art else 255
    _card(img, (32, 28, W - 32, 128), 30, a)
    d = ImageDraw.Draw(img)
    _text(d, (60, 44), "ПРОГНОЗ ПОСТА", font(True, 17), PURPLE)
    name = str(channel).strip() if channel and str(channel).strip() else "Черновик поста"
    _text(d, (58, 68), name, _fit(d, name, font, True, 36, W - 140), WHITE)
    # оценка — кольцо: фиолетовое, заполнено розовым на оценку
    score = num(p.get("score"))
    score = max(0, min(10, int(round(score)))) if score is not None else None
    _card(img, (32, 152, 400, 470), 28, a)
    d = ImageDraw.Draw(img)
    _text(d, (216, 184), "Оценка", font(True, 26), PURPLE, "mm")
    cx, cy, r = 216, 324, 104
    d.arc((cx - r, cy - r, cx + r, cy + r), 0, 360, fill=PURPLE, width=14)
    if score:
        d.arc((cx - r, cy - r, cx + r, cy + r), -90, -90 + 36 * score, fill=HOTPINK, width=14)
    _text(d, (cx, cy - 8), "—" if score is None else str(score), font(True, 84), HOTPINK, "mm")
    _text(d, (cx, cy + 56), "из 10", font(False, 22), WHITE, "mm")
    # ожидаемые просмотры
    _card(img, (424, 152, 800, 470), 28, a)
    d = ImageDraw.Draw(img)
    _text(d, (452, 184), "Ожидаемо", font(True, 26), PURPLE, "lm")
    reach = p.get("reach")
    if isinstance(reach, (tuple, list)) and len(reach) == 2 and None not in (num(reach[0]), num(reach[1])):
        rv = f"{fmt_short(reach[0])}–{fmt_short(reach[1])}"
        _text(d, (452, 262), rv, _fit(d, rv, font, True, 60, 320), HOTPINK, "lm")
        _text(d, (452, 322), "просмотров", font(False, 22), WHITE, "lm")
        if num(p.get("median")) is not None:
            _text(d, (452, 400), f"обычно у канала — {fmt_int(p['median'])}", font(False, 20), WHITE, "lm")
    else:
        _text(d, (452, 262), "—", font(True, 60), HOTPINK, "lm")
        _text(d, (452, 322), "нет истории канала", font(False, 22), WHITE, "lm")
    # лучшее время
    _card(img, (824, 152, W - 32, 470), 28, a)
    d = ImageDraw.Draw(img)
    _text(d, (852, 184), "Лучшее время", font(True, 26), PURPLE, "lm")
    best = p.get("best") if isinstance(p.get("best"), (tuple, list)) and len(p.get("best")) == 2 else (None, None)
    day = best[0] if isinstance(best[0], int) and 0 <= best[0] < 7 else None
    hour = best[1] if isinstance(best[1], int) and 0 <= best[1] < 24 else None
    when = ", ".join(x for x in (WEEK[day] if day is not None else "", f"{hour:02d}:00" if hour is not None else "")
                     if x) or "—"
    _text(d, (852, 262), when, _fit(d, when, font, True, 56, 300), HOTPINK, "lm")
    _text(d, (852, 322), "по прошлым постам", font(False, 20), WHITE, "lm")
    # советы
    _card(img, (32, 494, W - 32, 622), 24, a)
    d = ImageDraw.Draw(img)
    tips = [str(t) for t in (p.get("tips") or ()) if t][:2]
    why = [str(t) for t in (p.get("why") or ()) if t][:2]
    _text(d, (60, 522), "Как сделать лучше" if tips else "Почему так", font(True, 24), PURPLE, "lm")
    tip_font = font(False, 20)
    for i, tip in enumerate(tips or why or ["Пост выглядит хорошо — можно выкладывать"]):
        yy = 560 + i * 32
        d.ellipse((62, yy - 5, 72, yy + 5), fill=HOTPINK)
        _text(d, (86, yy), _clip(d, tip, tip_font, W - 150), tip_font, WHITE, "lm")
    n_posts = int(num(p.get("n")) or 0)
    stamp = time.strftime("%d.%m.%Y %H:%M", time.localtime(now or time.time()))
    _text(d, (34, 650), f"{brand} · прогноз по {n_posts} {_ru(n_posts, 'посту', 'постам', 'постам')} канала · {stamp}"
          if n_posts else f"{brand} · {stamp}", font(False, 15), WHITE, "lm")
    return img.convert("RGB")


def _ru(n, one, few, many):
    n = abs(int(n)) % 100
    return one if n % 10 == 1 and n != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n <= 14 else many
