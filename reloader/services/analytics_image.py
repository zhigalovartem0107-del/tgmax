"""Премиум-карточки аналитики MAX-каналов (Pillow).

Канал: подписчики и прирост (сегодня / 7 / 30 дней), график роста, таблица по дням, просмотры на пост
за 24/48/72 часа и ER24. Сеть каналов: всего подписчиков и просмотров, общий ER, топ-3 канала.
Только подсчёт и рисование — без сети и без состояния бота (данные приходят готовыми), поэтому легко
проверяется тестами. Фон — спокойный светлый, «узор» (процедурный, у каждого канала свой) или картинка
(например, сгенерированная ИИ)."""
import math
import random
import time
import zlib

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ACCENT = (88, 86, 214)          # фиолетово-синий
ACCENT_SOFT = (234, 236, 252)
NAVY = (27, 42, 74)
INK = (24, 30, 48)
GRAY = (112, 120, 140)
LINE = (228, 231, 239)
PAGE = (238, 241, 248)
WHITE = (255, 255, 255)
GREEN = (36, 150, 98)
RED = (214, 70, 86)
PINK = (253, 236, 240)
MONTHS = ("янв.", "февр.", "марта", "апр.", "мая", "июня", "июля", "авг.", "сент.", "окт.", "нояб.", "дек.")
DAY = 86400


# ---------- числа ----------
def fmt_int(n):
    """5008 -> «5 008»; нет данных — «—»."""
    return "—" if n is None else f"{int(round(n)):,}".replace(",", " ")


def fmt_short(n):
    """121700 -> «121,7K», 21900 -> «21,9K», 593 -> «593»."""
    if n is None:
        return "—"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".", ",").replace(",0M", "M")
    if abs(n) >= 10_000:
        return f"{n / 1000:.1f}K".replace(".", ",").replace(",0K", "K")
    return fmt_int(n)


def fmt_pct(x):
    return "—" if x is None else f"{x:.1f}%".replace(".", ",")


def fmt_delta(n):
    return "—" if n is None else ("+" if n >= 0 else "−") + fmt_int(abs(n))


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
    hist = dict(history or {})
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
    subs = sum(c["subs"] or 0 for c in channels)
    def total(k):
        vals = [c[k] for c in channels if c.get(k) is not None]
        return sum(vals) if vals else None
    v24 = total("v24")
    top = sorted(channels, key=lambda c: -(c["subs"] or 0))[:3]
    return {"subs": subs, "v24": v24, "v48": total("v48"), "v72": total("v72"),
            "er": round(v24 / subs * 100, 1) if v24 is not None and subs else None,
            "n": len(channels), "top": [(c["title"], c["subs"], c.get("v24")) for c in top],
            "now": now or time.time()}


# ---------- шрифт и фигуры ----------
def default_font(bold, size):
    for name in (("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf") if bold else ("DejaVuSans.ttf", "LiberationSans-Regular.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _card(img, box, fill, radius=28, alpha=255):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(box, radius, fill=fill + (alpha,))
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


def _avatar(img, center, r, title, photo=None):
    x, y = center
    if photo is not None:
        ph = photo.convert("RGBA").resize((2 * r, 2 * r))
        mask = Image.new("L", (2 * r, 2 * r), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=255)
        img.paste(ph, (x - r, y - r), mask)
        return
    d = ImageDraw.Draw(img)
    d.ellipse((x - r, y - r, x + r, y + r), fill=ACCENT)
    letter = (str(title or "?").strip()[:1] or "?").upper()
    _text(d, (x, y), letter, default_font(True, r), WHITE, "mm")


# ---------- фоны ----------
def seed_of(text):
    return zlib.crc32(str(text or "").encode("utf-8"))


def procedural_background(w, h, seed, style="aurora"):
    """Свой фон у каждого канала (seed — от названия): «aurora» — мягкие цветные пятна, «waves» — волны."""
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
    """Холст: обычный светлый или картинка-фон (растянутая и чуть приглушённая)."""
    if bg is None:
        return Image.new("RGBA", (w, h), PAGE + (255,)), False
    pic = bg.convert("RGB")
    k = max(w / pic.width, h / pic.height)
    pic = pic.resize((max(w, int(pic.width * k)), max(h, int(pic.height * k))))
    left, top = (pic.width - w) // 2, (pic.height - h) // 2
    return pic.crop((left, top, left + w, top + h)).convert("RGBA"), True


# ---------- карточка канала ----------
def render_channel(n, font=None, bg=None, photo=None, brand="Reloader"):
    """Картинка 1200×900 по channel_numbers(...)."""
    font = font or default_font
    W, H = 1200, 900
    img, art = _page(W, H, bg)
    a = 225 if art else 255
    d = ImageDraw.Draw(img)
    _card(img, (32, 28, W - 32, 136), WHITE, 34, a)
    _avatar(img, (92, 82), 38, n["title"], photo)
    d = ImageDraw.Draw(img)
    _text(d, (152, 44), "СТАТИСТИКА КАНАЛА", font(True, 17), GRAY)
    _text(d, (150, 66), n["title"], _fit(d, n["title"], font, True, 38, W - 220), INK)
    # подписчики
    _card(img, (32, 160, 448, 424), NAVY, 28, 245 if art else 255)
    d = ImageDraw.Draw(img)
    _text(d, (60, 190), "Подписчики", font(False, 26), (190, 204, 232))
    _text(d, (58, 228), fmt_int(n["subs"]), font(True, 74), WHITE)
    d.line([(60, 320), (420, 320)], fill=(60, 78, 112), width=1)
    for i, (lab, val) in enumerate((("Сегодня", n["today"]), ("7 дней", n["d7"]), ("30 дней", n["d30"]))):
        x = 60 + i * 124
        _text(d, (x, 338), lab, font(False, 19), (190, 204, 232))
        col = (120, 230, 180) if (val or 0) >= 0 else (255, 140, 150)
        _text(d, (x, 362), fmt_delta(val), font(True, 30), col if val is not None else (190, 204, 232))
    # просмотры на пост
    _card(img, (468, 160, 864, 424), WHITE, 28, a)
    d = ImageDraw.Draw(img)
    _text(d, (494, 186), "Просмотры на пост", font(True, 26), INK)
    for i, h in enumerate((24, 48, 72)):
        y = 232 + i * 61
        if i == 0:
            _card(img, (484, y - 2, 848, y + 52), ACCENT_SOFT, 14, 255)
            d = ImageDraw.Draw(img)
        _text(d, (500, y + 25), f"{h} часа" if h < 48 else f"{h} часов", font(False, 22), ACCENT if i == 0 else GRAY, "lm")
        _text(d, (830, y + 25), fmt_int(n[f"v{h}"]), font(True, 32), ACCENT if i == 0 else INK, "rm")
        if i:
            d.line([(500, y - 6), (832, y - 6)], fill=LINE, width=1)
    # ER24
    _card(img, (884, 160, W - 32, 424), PINK, 28, a)
    d = ImageDraw.Draw(img)
    _text(d, (910, 186), "ER24", font(True, 26), INK)
    _text(d, (908, 236), fmt_pct(n["er24"]), font(True, 64), RED)
    _text(d, (910, 330), "Просмотры за 24 часа", font(False, 19), GRAY)
    _text(d, (910, 356), "к числу подписчиков", font(False, 19), GRAY)
    # график
    _card(img, (32, 448, 672, 846), WHITE, 28, a)
    d = ImageDraw.Draw(img)
    _text(d, (60, 470), "Динамика подписчиков", font(True, 28), INK)
    _text(d, (60, 508), "Последние 7 дней", font(False, 18), GRAY)
    vals = [v for _, v, _ in n["days"] if v is not None]
    x0, x1, y0, y1 = 116, 628, 568, 770
    if vals:
        lo, hi = min(vals), max(vals)
        pad = max(1, (hi - lo) * 0.25 or hi * 0.02 or 1)
        lo, hi = lo - pad, hi + pad
        for k, frac in enumerate((1, 0.5, 0)):
            yy = y0 + (y1 - y0) * (1 - frac)
            d.line([(x0, yy), (x1, yy)], fill=LINE, width=1)
            _text(d, (x0 - 14, yy), fmt_int(lo + (hi - lo) * frac), font(False, 15), GRAY, "rm")
        step = (x1 - x0) / max(1, len(n["days"]) - 1)
        pts = [(x0 + i * step, y0 + (y1 - y0) * (1 - (v - lo) / (hi - lo)))
               for i, (_, v, _) in enumerate(n["days"]) if v is not None]
        if len(pts) > 1:
            area = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(area).polygon(pts + [(pts[-1][0], y1), (pts[0][0], y1)], fill=ACCENT + (40,))
            img.alpha_composite(area)
            d = ImageDraw.Draw(img)
            d.line(pts, fill=ACCENT, width=4, joint="curve")
        for p in pts:
            d.ellipse((p[0] - 5, p[1] - 5, p[0] + 5, p[1] + 5), fill=ACCENT, outline=WHITE, width=2)
    else:
        _text(d, ((x0 + x1) / 2, (y0 + y1) / 2), "История копится: бот снимает число подписчиков раз в день",
              font(False, 18), GRAY, "mm")
    for i, (key, _, _) in enumerate(n["days"]):
        xx = x0 + i * (x1 - x0) / max(1, len(n["days"]) - 1)
        _text(d, (xx, 800), f"{key[8:10]}.{key[5:7]}", font(False, 15), GRAY, "mm")
    # по дням
    _card(img, (692, 448, W - 32, 846), WHITE, 28, a)
    d = ImageDraw.Draw(img)
    _text(d, (720, 470), "По дням", font(True, 28), INK)
    _text(d, (720, 524), "Дата", font(False, 17), GRAY)
    _text(d, (1010, 524), "Подписчики", font(False, 17), GRAY, "ra")
    _text(d, (1140, 524), "Прирост", font(False, 17), GRAY, "ra")
    for i, (key, val, delta) in enumerate(list(reversed(n["days"]))[:6]):
        y = 556 + i * 46
        if i == 0:
            _card(img, (708, y - 2, 1152, y + 40), ACCENT_SOFT, 12, 255)
            d = ImageDraw.Draw(img)
        _text(d, (720, y + 19), f"{key[8:10]}.{key[5:7]}.{key[:4]}", font(False, 20), INK, "lm")
        _text(d, (1010, y + 19), fmt_int(val), font(True, 22), INK, "rm")
        col = GRAY if delta is None else (GREEN if delta >= 0 else RED)
        _text(d, (1140, y + 19), fmt_delta(delta), font(True, 21), col, "rm")
    _text(d, (34, 872), f"{brand} · срез {time.strftime('%d.%m.%Y %H:%M', time.localtime(n['now']))}",
          font(False, 15), WHITE if art else GRAY)
    _text(d, (W - 34, 872), "24 / 48 / 72 ч — средние просмотры на пост", font(False, 15),
          WHITE if art else GRAY, "ra")
    return img.convert("RGB")


# ---------- сеть каналов ----------
def render_network(net, font=None, bg=None, brand="Reloader", title="Статистика по всей сетке каналов"):
    """Картинка 1280×760 по network_numbers(...). С фоном-картинкой — фон сверху «баннером», как у реклам сетей."""
    font = font or default_font
    W, top = 1280, (300 if bg is not None else 128)
    H = top + 136 + 330 + 110                         # высота — по содержимому (с фоном — выше на баннер)
    img = Image.new("RGBA", (W, H), PAGE + (255,))
    if bg is not None:
        banner, _ = _page(W, 330, bg)
        img.alpha_composite(banner, (0, 0))
        d = ImageDraw.Draw(img)
        _text(d, (48, 150), "Сеть каналов MAX", font(True, 64), WHITE, "ls")
        _text(d, (50, 200), f"{net['n']} {_ru(net['n'], 'канал', 'канала', 'каналов')} · одна сеть",
              font(False, 30), WHITE, "ls")
    else:
        d = ImageDraw.Draw(img)
        _text(d, (W / 2, 70), title, _fit(d, title, font, True, 52, W - 80), ACCENT, "mm")
    _card(img, (30, top, W - 30, top + 112), WHITE, 22)
    d = ImageDraw.Draw(img)
    cells = ((fmt_short(net["subs"]), "подписчиков в сети"), (fmt_short(net["v24"]), "просмотров в сутки"),
             (fmt_pct(net["er"]), "общий ER"),
             (str(net["n"]), "каналов в сети"))
    for i, (big, small) in enumerate(cells):
        x = 30 + (W - 60) * (i + 0.5) / 4
        _text(d, (x, top + 46), big, font(True, 46), ACCENT, "mm")
        _text(d, (x, top + 90), small, font(False, 17), GRAY, "mm")
    y = top + 136
    _card(img, (30, y, 448, y + 330), WHITE, 22)
    d = ImageDraw.Draw(img)
    _text(d, (239, y + 40), "Просмотры", font(True, 32), INK, "mm")
    for i, h in enumerate((24, 48, 72)):
        yy = y + 84 + i * 76
        if i % 2 == 0:
            _card(img, (54, yy, 424, yy + 64), ACCENT_SOFT, 12)
            d = ImageDraw.Draw(img)
        _text(d, (74, yy + 32), f"{h}ч:", font(False, 30), INK, "lm")
        _text(d, (404, yy + 32), fmt_short(net[f"v{h}"]), font(True, 34), ACCENT, "rm")
    _card(img, (472, y, W - 30, y + 330), WHITE, 22)
    d = ImageDraw.Draw(img)
    _text(d, ((472 + W - 30) / 2, y + 40), "Топ-3 канала", font(True, 32), INK, "mm")
    _text(d, (500, y + 88), "Название", font(False, 20), GRAY, "lm")
    _text(d, (1050, y + 88), "Подписчики", font(False, 20), GRAY, "rm")
    _text(d, (W - 56, y + 88), "Просмотры", font(False, 20), GRAY, "rm")
    for i, (name, subs, views) in enumerate(net["top"]):
        yy = y + 112 + i * 70
        if i % 2 == 0:
            _card(img, (490, yy, W - 46, yy + 62), ACCENT_SOFT, 12)
            d = ImageDraw.Draw(img)
        _avatar(img, (526, yy + 31), 20, name)
        d = ImageDraw.Draw(img)
        _text(d, (558, yy + 31), name, _fit(d, name, font, False, 24, 380), INK, "lm")
        _text(d, (1050, yy + 31), fmt_short(subs), font(True, 26), ACCENT, "rm")
        _text(d, (W - 56, yy + 31), fmt_short(views), font(True, 26), ACCENT, "rm")
    lt = time.localtime(net["now"])
    when = f"{lt.tm_hour:02d}:{lt.tm_min:02d}, {lt.tm_mday} {MONTHS[lt.tm_mon - 1]} {lt.tm_year} г."
    _card(img, (30, H - 70, W - 30, H - 16), WHITE, 18)
    d = ImageDraw.Draw(img)
    _text(d, (56, H - 43), f"Актуально на {when}", font(False, 20), INK, "lm")
    _text(d, (W - 56, H - 43), f"{brand} — аналитика MAX-каналов", font(True, 20), ACCENT, "rm")
    return img.convert("RGB")


def _ru(n, one, few, many):
    n = abs(int(n)) % 100
    return one if n % 10 == 1 and n != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n <= 14 else many
