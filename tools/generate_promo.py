#!/usr/bin/env python3
"""Промо-видео Reloader, 10 секунд: телефон слева, текст справа, сценарий «один клик».

Сценарий:
  0.0–1.5  телефон появляется; справа «Новый пост из Telegram»
  1.2      уведомление падает сверху: «Новый пост из @news»
  2.0–2.8  в чате бота выезжает карточка поста с кнопкой «Опубликовать»
  3.6–4.6  курсор летит к кнопке; 4.6 — клик; справа «Один клик»
  5.0      тост «Опубликовано в MAX»
  6.0–6.8  экран уезжает — открывается канал MAX с опубликованным постом
  8.0–10   логотип Reloader и «Твой канал на автопилоте»

Запуск:
    pip install moviepy pillow numpy        # и ffmpeg в системе
    python tools/generate_promo.py --out promo.mp4
    python tools/generate_promo.py --preview 4.8 --png preview.png   # один кадр для проверки

Шрифты: DejaVu Sans / Liberation Sans (кириллица). Укажи свой через --font-bold, если их нет.
"""
import argparse
import os
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
DURATION = 10.0
FPS = 30

# --- палитра: тёмная тема, белый и синий акцент ---
BG = np.array([9, 11, 16], dtype=float)
PANEL = (18, 22, 32)
PHONE_BODY = (32, 36, 46)
PHONE_EDGE = (58, 63, 78)
SCREEN = (12, 15, 22)
BUBBLE = (26, 31, 43)
ACCENT = (46, 134, 255)
ACCENT_PRESSED = (30, 100, 210)
WHITE = (242, 245, 250)
MUTED = (128, 137, 155)
BAR = (52, 60, 78)

# --- раскладка ---
PHONE_W, PHONE_H = 300, 610
PHONE_X, PHONE_Y = 170, 55               # телефон по центру левой половины
SCREEN_PAD = 14
SW, SH = PHONE_W - 2 * SCREEN_PAD, PHONE_H - 2 * SCREEN_PAD
CARD_Y = 150                             # карточка поста в чате (в координатах экрана)
BUTTON_OFF = 222                         # центр кнопки «Опубликовать» от верха карточки
TEXT_X = 800                             # подписи справа

# --- тайминг (секунды) ---
NOTIFY_IN = 1.2
BUTTON_IN = 3.0
CURSOR_IN = 3.6
CLICK_AT = 4.6
TOAST_AT = 5.0
CHANNEL_AT = 6.0
LOGO_AT = 8.0

# подписи справа: (начало появления, полностью, текст, начало ухода, конец ухода)
CAPTIONS = [
    (1.0, 1.5, "Новый пост из Telegram", 2.2, 2.6),
    (2.6, 3.1, "Проверь и нажми", 3.8, 4.1),
    (4.2, 4.5, "Один клик", 6.2, 6.7),
    (6.9, 7.3, "Пост уже в канале MAX", 7.6, 7.9),
]

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


# ---------- тайминг и кривые (чистые функции — их и тестируем) ----------
def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def window(t, a, b):
    return clamp((t - a) / (b - a))


def smooth(x):
    x = clamp(x)
    return x * x * (3 - 2 * x)


def notify_dy(t):
    """Уведомление падает сверху: −70 пикселей → 0."""
    return -70 * (1 - smooth(window(t, NOTIFY_IN, NOTIFY_IN + 0.6)))


def notify_alpha(t):
    return smooth(window(t, NOTIFY_IN, NOTIFY_IN + 0.3)) * (1 - smooth(window(t, 3.0, 3.4)))


def card_y(t):
    """Карточка выезжает снизу в чат: 80 пикселей → CARD_Y."""
    return CARD_Y + 80 * (1 - smooth(window(t, 2.0, 2.8)))


def card_alpha(t):
    return smooth(window(t, 2.0, 2.5))


def button_center():
    return SW / 2, CARD_Y + BUTTON_OFF


def cursor_start():
    return SW - 26, SH - 46


def cursor_pos(t):
    (x0, y0), (x1, y1) = cursor_start(), button_center()
    p = smooth(window(t, CURSOR_IN, CLICK_AT))
    return x0 + (x1 - x0) * p, y0 + (y1 - y0) * p


def cursor_alpha(t):
    return smooth(window(t, CURSOR_IN, CURSOR_IN + 0.3)) * (1 - smooth(window(t, 5.2, 5.6)))


def button_color(t):
    return ACCENT_PRESSED if CLICK_AT <= t < CLICK_AT + 0.4 else ACCENT


def ripple_radius(t):
    return 6 + 26 * window(t, CLICK_AT, CLICK_AT + 0.5)


def ripple_alpha(t):
    if CLICK_AT <= t < CLICK_AT + 0.5:
        return 1 - window(t, CLICK_AT, CLICK_AT + 0.5)
    return 0.0


def toast_alpha(t):
    return smooth(window(t, TOAST_AT, TOAST_AT + 0.3)) * (1 - smooth(window(t, 6.0, 6.4)))


def toast_y(t):
    return SH - 72 + 80 * (1 - smooth(window(t, TOAST_AT, TOAST_AT + 0.4)))


def transition_k(t):
    """0 — виден чат, 1 — виден канал MAX."""
    return smooth(window(t, CHANNEL_AT, CHANNEL_AT + 0.8))


def caption_alpha(text, t):
    for a, b, label, c, d in CAPTIONS:
        if label == text:
            return smooth(window(t, a, b)) * (1 - smooth(window(t, c, d)))
    return 0.0


def phone_alpha(t):
    return smooth(window(t, 0.0, 0.4)) * (1 - smooth(window(t, 7.7, 8.2)))


def logo_alpha(t):
    return smooth(window(t, LOGO_AT, LOGO_AT + 0.5))


def tagline_alpha(t):
    return smooth(window(t, LOGO_AT + 0.6, LOGO_AT + 1.1))


# ---------- шрифты и слои ----------
@lru_cache(maxsize=None)
def font(size, bold=True):
    override = FONT_OVERRIDE["bold" if bold else "reg"]
    for path in ([override] if override else []) + (FONT_BOLD if bold else FONT_REG):
        if path and os.path.isfile(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def composite(base, draw_fn, alpha=1.0):
    """Рисует слой draw_fn(draw) поверх base с прозрачностью alpha."""
    if alpha <= 0:
        return
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    if alpha < 1:
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    base.alpha_composite(layer)


def gradient(w, h, c1=(46, 134, 255), c2=(124, 72, 224)):
    ramp = np.linspace(0, 1, w)[None, :, None]
    col = np.array(c1, float)[None, None, :] * (1 - ramp) + np.array(c2, float)[None, None, :] * ramp
    return Image.fromarray(np.broadcast_to(col, (h, w, 3)).astype(np.uint8)).convert("RGBA")


def rounded_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return m


def draw_logo(draw, cx, cy, r):
    """Логотип: синий круг и два белых изогнутых стрелки «перезапуска»."""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT)
    rr = r * 0.52
    width = max(3, int(r * 0.16))
    draw.arc([cx - rr, cy - rr, cx + rr, cy + rr], 200, 330, fill=WHITE, width=width)
    draw.arc([cx - rr, cy - rr, cx + rr, cy + rr], 20, 150, fill=WHITE, width=width)
    for ang in (330, 150):
        px = cx + rr * np.cos(np.radians(ang))
        py = cy + rr * np.sin(np.radians(ang))
        s = r * 0.22
        draw.polygon([(px, py - s), (px + s, py), (px - s * 0.2, py + s * 0.6)], fill=WHITE)


# ---------- фон и телефон (статичные, считаем один раз) ----------
@lru_cache(maxsize=1)
def background():
    ys, xs = np.mgrid[0:H, 0:W]
    glow = np.exp(-((xs - PHONE_X - PHONE_W / 2) ** 2 + (ys - H / 2) ** 2) / (2 * 300.0 ** 2)) * 0.10
    vertical = np.linspace(0, 1, H)[:, None] * 6.0          # чуть светлее внизу
    col = BG[None, None, :] + vertical[..., None] + glow[..., None] * (np.array(ACCENT, float) - BG)
    return Image.fromarray(np.clip(col, 0, 255).astype(np.uint8)).convert("RGBA")


@lru_cache(maxsize=1)
def phone_body():
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([PHONE_X, PHONE_Y, PHONE_X + PHONE_W, PHONE_Y + PHONE_H], radius=44,
                        fill=PHONE_BODY, outline=PHONE_EDGE, width=2)
    d.rounded_rectangle([PHONE_X + PHONE_W - 3, PHONE_Y + 130, PHONE_X + PHONE_W + 2, PHONE_Y + 190],
                        radius=2, fill=PHONE_EDGE)
    return img


@lru_cache(maxsize=1)
def screen_mask():
    return rounded_mask(SW, SH, 30)


# ---------- экран телефона ----------
def chat_screen(t):
    img = Image.new("RGBA", (SW, SH), SCREEN + (255,))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, SW, 60], fill=PANEL)
    d.text((16, 20), "Reloader", font=font(15), fill=WHITE)
    d.text((16, 40), "бот", font=font(11, bold=False), fill=MUTED)
    # старое сообщение бота
    d.rounded_rectangle([14, 76, SW - 60, 112], radius=14, fill=BUBBLE)
    d.text((26, 94), "Привет! Пришлю посты сюда", font=font(12, bold=False), fill=MUTED, anchor="lm")

    # карточка поста с кнопкой — выезжает снизу
    cy = card_y(t)
    composite(img, lambda dr: dr.rounded_rectangle([14, cy, SW - 14, cy + 250], radius=16, fill=BUBBLE),
              card_alpha(t))
    pic = gradient(SW - 44, 142)
    pic.putalpha(rounded_mask(SW - 44, 142, 12))
    img.alpha_composite(_with_alpha(pic, card_alpha(t)), (22, int(cy + 8)))
    composite(img, lambda dr: dr.rounded_rectangle([22, cy + 162, SW - 92, cy + 172], radius=5, fill=BAR),
              card_alpha(t))
    composite(img, lambda dr: dr.rounded_rectangle([22, cy + 180, SW - 130, cy + 190], radius=5, fill=BAR),
              card_alpha(t))
    btn = button_color(t)
    composite(img, lambda dr: dr.rounded_rectangle([22, cy + 204, SW - 22, cy + 240], radius=12, fill=btn),
              card_alpha(t))
    composite(img, lambda dr: dr.text((SW / 2, cy + 222), "Опубликовать", font=font(14), fill=WHITE, anchor="mm"),
              card_alpha(t))

    # курсор и пульсация при клике
    if cursor_alpha(t) > 0:
        x, y = cursor_pos(t)
        pts = [(0, 0), (0, 18), (5, 14), (9, 22), (12, 20), (8, 13), (14, 13)]
        composite(img, lambda dr: dr.polygon([(x + px, y + py) for px, py in pts], fill=WHITE,
                                             outline=(10, 12, 18)), cursor_alpha(t))
    if ripple_alpha(t) > 0:
        bx, by = button_center()
        r = ripple_radius(t)
        composite(img, lambda dr: dr.ellipse([bx - r, by - r, bx + r, by + r], outline=ACCENT, width=3),
                  ripple_alpha(t))

    # уведомление сверху
    dy = notify_dy(t)
    composite(img, lambda dr: (dr.rounded_rectangle([8, 8 + dy, SW - 8, 62 + dy], radius=14, fill=(30, 38, 58)),
                               dr.text((20, 24 + dy), "Новый пост из @news", font=font(13), fill=WHITE),
                               dr.text((20, 44 + dy), "Reloader · сейчас", font=font(11, bold=False), fill=MUTED)),
              notify_alpha(t))

    # тост «Опубликовано»
    if toast_alpha(t) > 0:
        ty = toast_y(t)
        composite(img, lambda dr: (dr.rounded_rectangle([12, ty, SW - 12, ty + 46], radius=14, fill=(22, 30, 46)),
                                   dr.ellipse([22, ty + 13, 44, ty + 35], fill=ACCENT),
                                   dr.text((34, ty + 23), "✓", font=font(12), fill=WHITE, anchor="mm"),
                                   dr.text((56, ty + 23), "Опубликовано в MAX", font=font(13), fill=WHITE,
                                           anchor="lm")),
                  toast_alpha(t))
    return img


def channel_screen():
    """Канал MAX с опубликованным постом — как его видят подписчики."""
    img = Image.new("RGBA", (SW, SH), SCREEN + (255,))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, SW, 60], fill=PANEL)
    d.text((16, 20), "Мой канал", font=font(15), fill=WHITE)
    d.text((16, 40), "канал в MAX", font=font(11, bold=False), fill=MUTED)
    d.rounded_rectangle([14, 76, SW - 14, 376], radius=16, fill=BUBBLE)
    pic = gradient(SW - 44, 140)
    pic.putalpha(rounded_mask(SW - 44, 140, 12))
    img.alpha_composite(pic, (22, 84))
    d.rounded_rectangle([22, 236, SW - 90, 246], radius=5, fill=BAR)
    d.rounded_rectangle([22, 254, SW - 130, 264], radius=5, fill=BAR)
    d.rounded_rectangle([22, 280, SW - 60, 288], radius=4, fill=BAR)
    d.rounded_rectangle([22, 296, SW - 80, 304], radius=4, fill=BAR)
    d.text((22, 340), "12 реакций · 3 комментария", font=font(11, bold=False), fill=MUTED, anchor="lm")
    d.rounded_rectangle([14, 396, SW - 14, 540], radius=16, fill=BUBBLE)
    d.rounded_rectangle([22, 410, SW - 90, 420], radius=5, fill=BAR)
    d.rounded_rectangle([22, 428, SW - 120, 436], radius=4, fill=BAR)
    d.rounded_rectangle([22, 444, SW - 70, 452], radius=4, fill=BAR)
    return img


def _with_alpha(img, a):
    if a >= 1:
        return img
    out = img.copy()
    out.putalpha(out.getchannel("A").point(lambda v: int(v * a)))
    return out


def phone_screen(t):
    chat = chat_screen(t)
    if t < CHANNEL_AT:
        scr = chat
    else:
        k = transition_k(t)
        if k >= 1:
            scr = channel_screen()
        else:
            scr = Image.new("RGBA", (SW, SH), SCREEN + (255,))
            scr.paste(chat, (round(-k * SW), 0), chat)
            ch = channel_screen()
            scr.paste(ch, (round((1 - k) * SW), 0), ch)
    # вырез под «чёлку» поверх экрана
    ImageDraw.Draw(scr).rounded_rectangle([SW / 2 - 40, 8, SW / 2 + 40, 22], radius=7, fill=SCREEN)
    return scr


# ---------- кадр ----------
def make_frame(t):
    canvas = background().copy()
    pa = phone_alpha(t)
    if pa > 0:
        layer = phone_body().copy()
        layer.paste(phone_screen(t), (PHONE_X + SCREEN_PAD, PHONE_Y + SCREEN_PAD), screen_mask())
        canvas.alpha_composite(_with_alpha(layer, pa))

    caps = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for a, b, text, c, d in CAPTIONS:
        alpha = caption_alpha(text, t)
        if alpha <= 0:
            continue
        size = 60 if text == "Один клик" else 32
        composite(caps, lambda dr, s=text, z=size: dr.text((TEXT_X, 330), s, font=font(z), fill=WHITE,
                                                           anchor="lm"), alpha)
        if text == "Один клик":
            composite(caps, lambda dr: dr.rounded_rectangle([TEXT_X, 384, TEXT_X + 160, 390], radius=3,
                                                            fill=ACCENT), alpha)
    canvas.alpha_composite(caps)

    la = logo_alpha(t)
    if la > 0:
        logo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(logo)
        draw_logo(ld, W / 2, 262, 56)
        ld.text((W / 2, 376), "Reloader", font=font(60), fill=WHITE, anchor="mm")
        canvas.alpha_composite(_with_alpha(logo, la))
    ta = tagline_alpha(t)
    if ta > 0:
        tag = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(tag).text((W / 2, 448), "Твой канал на автопилоте", font=font(30, bold=False),
                                 fill=MUTED, anchor="mm")
        canvas.alpha_composite(_with_alpha(tag, ta))
    return np.asarray(canvas.convert("RGB"))


def render(out, fps=FPS):
    from moviepy import VideoClip

    clip = VideoClip(make_frame, duration=DURATION)
    clip.write_videofile(out, fps=fps, codec="libx264", audio=False, preset="medium", logger=None)
    clip.close()
    return out


def main():
    parser = argparse.ArgumentParser(description="Промо-видео Reloader (10 с): телефон и сценарий «один клик».")
    parser.add_argument("--out", default="promo.mp4")
    parser.add_argument("--fps", type=int, default=FPS)
    parser.add_argument("--preview", type=float, help="сохранить один кадр на этой секунде")
    parser.add_argument("--png", default="preview.png")
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
