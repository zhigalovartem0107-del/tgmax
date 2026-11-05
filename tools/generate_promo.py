#!/usr/bin/env python3
"""Вертикальный промо-ролик Reloader, 10 секунд (1080x1920) — в стиле коротких видео.

Чёрный фон, неоновые фиолетовый (#8A2BE2) и розовый (#FF1493) акценты, огромный текст, быстрый монтаж.

    0.0–1.5  «АВТОПОСТИНГ / В MAX» крупно; фиолетовый телефон въезжает снизу
    1.5–2.8  уведомление Telegram сверху; в чате появляется пост с картинкой
    3.0–4.5  курсор нажимает «Опубликовать»; вспышка и «1 КЛИК»
    4.5–6.0  свайп в канал MAX — пост уже там; «ГОТОВО»
    6.0–8.0  пункты: • Без задержек • Любые файлы • Аналитика
    8.0–10   логотип Reloader и «Твой контент-завод»

Запуск (promo.mp4 появится в текущей папке):
    pip install moviepy pillow numpy        # и ffmpeg в системе
    python generate_promo.py
    python generate_promo.py --preview 3.8 --png kadr.png   # один кадр, чтобы проверить
Шрифт с кириллицей: DejaVu Sans Bold / Liberation Sans Bold (или --font-bold путь/к/шрифту.ttf).
"""
import argparse
import os
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
FPS = 30
DURATION = 10.0
OUT = "promo.mp4"

BLACK = (0, 0, 0)
PURPLE = (138, 43, 226)       # #8A2BE2
PINK = (255, 20, 147)         # #FF1493
WHITE = (255, 255, 255)
GREY = (150, 150, 162)
SCREEN_BG = (8, 8, 12)
HEADER_BG = (16, 16, 22)
BUBBLE = (24, 24, 32)
BAR = (58, 58, 72)

# телефон
PW, PH = 560, 1120
PX = (W - PW) // 2
PHONE_REST_Y = 760
INSET = 22
SW, SH = PW - 2 * INSET, PH - 2 * INSET
CARD_TOP, CARD_BOTTOM = 260, 820
BUTTON = (48, 720, SW - 48, 800)           # кнопка «Опубликовать» в координатах экрана
CLICK_AT = 3.5

FONT_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]
FONT_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/Library/Fonts/Arial.ttf",
]
FONT_OVERRIDE = {"bold": None, "reg": None}


# ---------- время и движение (чистые функции) ----------
def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ease_out(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    x = clamp(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def phone_y(t):
    """Телефон въезжает снизу за 0.6 с, стоит, и уезжает вниз к концу 7.6–8.1."""
    if t < 0.3:
        return H
    if t < 0.9:
        return H + (PHONE_REST_Y - H) * ease_out((t - 0.3) / 0.6)
    if t < 7.6:
        return PHONE_REST_Y
    return PHONE_REST_Y + (H - PHONE_REST_Y) * ease_out((t - 7.6) / 0.5)


def notif_y(t):
    """Уведомление: въезжает сверху за 0.15 с, висит, уезжает. None — скрыто."""
    if t < 1.5 or t >= 2.75:
        return None
    if t < 1.65:
        return -140 + 156 * ease_out((t - 1.5) / 0.15)
    if t < 2.6:
        return 16
    return 16 - 156 * ease_out((t - 2.6) / 0.15)


def card_visible(t):
    """Пост появляется сразу, без плавного проявления — резкий монтаж."""
    return t >= 1.8


def button_pressed(t):
    return CLICK_AT <= t < CLICK_AT + 0.4


def button_center():
    x0, y0, x1, y1 = BUTTON
    return (x0 + x1) / 2, (y0 + y1) / 2


def cursor_start():
    return SW - 30, SH - 120


def cursor_pos(t):
    """Курсор летит к кнопке за 0.4 с и доходит к клику ровно в CLICK_AT."""
    (x0, y0), (x1, y1) = cursor_start(), button_center()
    p = ease_out((t - (CLICK_AT - 0.5)) / 0.5)
    return x0 + (x1 - x0) * p, y0 + (y1 - y0) * p


def cursor_visible(t):
    return CLICK_AT - 0.5 <= t < CLICK_AT + 0.45


def swipe_k(t):
    """0 — чат, 1 — канал MAX. Свайп за 0.35 с."""
    return ease_in_out((t - 4.5) / 0.35)


def flash(t):
    """Белая вспышка на кадрах нажатий и уведомления (0 — нет)."""
    for at in (1.5, CLICK_AT, 4.5):
        if at <= t < at + 0.06:
            return 0.55 * (1 - (t - at) / 0.06)
    return 0.0


def logo_scale(t):
    return ease_out((t - 8.0) / 0.25)


def caption_items(t):
    """Тексты на экране в момент t: (текст, размер, цвет, x, y, выравнивание)."""
    items = []
    if t < 1.5:
        items += [("АВТОПОСТИНГ", 116, PINK, W / 2, 360, "mm"), ("В MAX", 116, PINK, W / 2, 520, "mm")]
    elif 3.5 <= t < 4.5:
        pop = 1 + 0.12 * max(0.0, 1 - (t - 3.5) / 0.12)            # «удар» в первые 0.12 с
        items.append(("1 КЛИК", int(200 * pop), PINK, W / 2, 440, "mm"))
    elif 4.5 <= t < 6.0:
        items.append(("ГОТОВО", 200, PINK, W / 2, 440, "mm"))
    elif 6.0 <= t < 8.0:
        for text, at, y in (("• Без задержек", 6.0, 330), ("• Любые файлы", 6.6, 500), ("• Аналитика", 7.2, 670)):
            if t >= at:
                items.append((text, 104, WHITE, 70, y, "lm"))
    elif t >= 8.0:
        items.append(("Reloader", 130, PINK, W / 2, 1000, "mm"))
        if t >= 8.4:
            items.append(("Твой контент-завод", 84, WHITE, W / 2, 1130, "mm"))
    return items


# ---------- шрифты и измерения ----------
@lru_cache(maxsize=None)
def font(size, bold=True):
    override = FONT_OVERRIDE["bold" if bold else "reg"]
    for path in ([override] if override else []) + (FONT_BOLD if bold else FONT_REG):
        if path and os.path.isfile(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def text_width(text, size, bold=True):
    left, _, right, _ = font(size, bold).getbbox(text)
    return right - left


# ---------- картинки ----------
@lru_cache(maxsize=4)
def gradient(w, h):
    """Картинка поста: розовый → фиолетовый."""
    ramp = np.linspace(0, 1, w)[None, :, None]
    c1, c2 = np.array(PINK, float), np.array(PURPLE, float)
    col = c1 * (1 - ramp) + c2 * ramp
    return Image.fromarray(np.broadcast_to(col, (h, w, 3)).astype(np.uint8))


@lru_cache(maxsize=None)
def rounded_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return m


def paste_rounded(dst, src, xy, radius):
    dst.paste(src.resize(src.size), xy, rounded_mask(src.size[0], src.size[1], radius))


# ---------- экраны телефона ----------
def post_card(d, img, top, with_button, pressed=False):
    """Карточка поста: картинка, две строки текста и (если нужно) кнопка."""
    d.rounded_rectangle([24, top, SW - 24, top + (CARD_BOTTOM - CARD_TOP)], radius=36, fill=BUBBLE)
    paste_rounded(img, gradient(SW - 96, 340), (48, top + 24), 24)
    d.rounded_rectangle([48, top + 392, SW - 150, top + 414], radius=11, fill=BAR)
    d.rounded_rectangle([48, top + 426, SW - 260, top + 448], radius=11, fill=BAR)
    if with_button:
        x0, y0, x1, y1 = BUTTON
        d.rounded_rectangle([x0, y0, x1, y1], radius=26, fill=PINK if pressed else PURPLE)
        d.text(((x0 + x1) / 2, (y0 + y1) / 2), "Опубликовать", font=font(38), fill=WHITE, anchor="mm")


def chat_screen(t):
    img = Image.new("RGB", (SW, SH), SCREEN_BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, SW, 120], fill=HEADER_BG)
    d.text((30, 42), "Reloader", font=font(44), fill=WHITE)
    d.text((30, 88), "бот", font=font(28, False), fill=GREY)
    d.rounded_rectangle([24, 150, SW - 120, 220], radius=24, fill=BUBBLE)
    d.text((46, 185), "Пришлю новые посты", font=font(30, False), fill=GREY, anchor="lm")
    if card_visible(t):
        post_card(d, img, CARD_TOP, True, button_pressed(t))
    ny = notif_y(t)
    if ny is not None:
        d.rounded_rectangle([20, ny, SW - 20, ny + 120], radius=24, fill=(34, 20, 52), outline=PINK, width=3)
        d.text((46, ny + 38), "Telegram · @news", font=font(34), fill=WHITE, anchor="lm")
        d.text((46, ny + 82), "Новый пост с фото", font=font(30, False), fill=GREY, anchor="lm")
    if cursor_visible(t):
        cx, cy = cursor_pos(t)
        arrow = [(0, 0), (0, 36), (11, 28), (18, 44), (25, 40), (17, 26), (30, 26)]
        d.polygon([(cx + x, cy + y) for x, y in arrow], fill=WHITE, outline=BLACK)
    return img


def channel_screen():
    img = Image.new("RGB", (SW, SH), SCREEN_BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, SW, 120], fill=HEADER_BG)
    d.text((30, 42), "Мой канал", font=font(44), fill=WHITE)
    d.text((30, 88), "канал MAX", font=font(28, False), fill=GREY)
    post_card(d, img, CARD_TOP, False)
    d.text((48, 772), "12 реакций · 3 комментария", font=font(30, False), fill=GREY, anchor="lm")
    d.rounded_rectangle([24, 860, SW - 24, 1040], radius=36, fill=BUBBLE)
    d.rounded_rectangle([48, 900, SW - 150, 922], radius=11, fill=BAR)
    d.rounded_rectangle([48, 942, SW - 300, 964], radius=11, fill=BAR)
    return img


def phone_screen(t):
    k = swipe_k(t)
    if t < 4.5:
        return chat_screen(t)
    if k >= 1:
        return channel_screen()
    out = Image.new("RGB", (SW, SH), SCREEN_BG)
    out.paste(chat_screen(t), (round(-k * SW), 0))
    out.paste(channel_screen(), (round((1 - k) * SW), 0))
    return out


# ---------- кадр ----------
def make_frame(t, width=W, height=H):
    canvas = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(canvas)
    for text, size, color, x, y, anchor in caption_items(t):
        d.text((x, y), text, font=font(size), fill=color, anchor=anchor)
    if t >= 8.0:                                                     # логотип в центре
        s = logo_scale(t)
        r = 150 * s
        if r > 1:
            cx, cy = W / 2, 700
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=PURPLE)
            rr = r * 0.52
            width_arc = max(3, int(r * 0.16))
            d.arc([cx - rr, cy - rr, cx + rr, cy + rr], 200, 330, fill=WHITE, width=width_arc)
            d.arc([cx - rr, cy - rr, cx + rr, cy + rr], 20, 150, fill=WHITE, width=width_arc)
    y = phone_y(t)
    if y < H:
        d.rounded_rectangle([PX, y, PX + PW, y + PH], radius=90, fill=PURPLE)
        screen = phone_screen(t)
        canvas.paste(screen, (PX + INSET, round(y + INSET)), rounded_mask(SW, SH, 70))
    a = flash(t)
    if a > 0:
        canvas = Image.blend(canvas, Image.new("RGB", (W, H), WHITE), a)
    return np.asarray(canvas)


def render(out=OUT, fps=FPS):
    from moviepy import VideoClip

    clip = VideoClip(make_frame, duration=DURATION)
    clip.write_videofile(out, fps=fps, codec="libx264", audio=False, preset="medium", logger=None)
    clip.close()
    return out


def main():
    parser = argparse.ArgumentParser(description="Вертикальный промо-ролик Reloader (10 с, 1080x1920).")
    parser.add_argument("--out", default=OUT, help="файл, по умолчанию promo.mp4 в текущей папке")
    parser.add_argument("--fps", type=int, default=FPS)
    parser.add_argument("--preview", type=float, help="сохранить один кадр на этой секунде и выйти")
    parser.add_argument("--png", default="kadr.png")
    parser.add_argument("--font-bold", help="путь к жирному шрифту с кириллицей")
    parser.add_argument("--font-reg", help="путь к обычному шрифту с кириллицей")
    args = parser.parse_args()
    FONT_OVERRIDE["bold"], FONT_OVERRIDE["reg"] = args.font_bold, args.font_reg
    if args.preview is not None:
        Image.fromarray(make_frame(args.preview)).save(args.png)
        print("Кадр сохранён:", os.path.abspath(args.png))
        return
    render(args.out, args.fps)
    print(f"Готово: {os.path.abspath(args.out)} ({DURATION:.0f} с, {W}x{H}, {args.fps} fps)")


if __name__ == "__main__":
    main()
