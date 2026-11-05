#!/usr/bin/env python3
"""Промо-видео Reloader, 10 секунд: фон, анимированный текст, пост летит из Telegram в MAX.

Результат — welcome.mp4 для приветствия бота (WELCOME_VIDEO_PATH в переменных хостинга).

Запуск:
    pip install moviepy pillow numpy       # и ffmpeg в системе
    python tools/generate_video.py --out welcome.mp4
    python tools/generate_video.py --out welcome.mp4 --size 1920x1080 --fps 30

Сценарий (10 с):
    0.0–3.5  название «Reloader - Auto-posting to MAX» появляется и гаснет
    3.5–8.5  карточка поста вылетает из круга «TG», по дуге летит в круг «MAX»
    8.5–10   название возвращается и плавно уходит в фон
"""
import argparse
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

DURATION = 10.0
TITLE = "Reloader - Auto-posting to MAX"
FLIGHT_START = 3.5          # с какой секунды пост вылетает
FLIGHT_END = 8.5            # когда долетает до MAX
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]
TOP_COLOR = np.array([28, 34, 92], dtype=float)       # фон: сверху тёмно-синий
BOTTOM_COLOR = np.array([96, 36, 120], dtype=float)   # снизу сиреневый
TG_COLOR = (42, 171, 238)
MAX_COLOR = (0, 110, 255)


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def smooth(x):
    """Плавный разгон и торможение: 0→0, 1→1."""
    x = clamp(x)
    return x * x * (3 - 2 * x)


def text_alpha(t):
    """Прозрачность названия: 0–0.5 с — ноль, 0.5–1.5 — появляется, 1.5–2.5 — полностью, 2.5–3.5 — гаснет.
    На 8.5–10 с название возвращается и уходит в фон."""
    if t < 0.5:
        return 0.0
    if t < 1.5:
        return smooth((t - 0.5) / 1.0)
    if t < 2.5:
        return 1.0
    if t < 3.5:
        return 1.0 - smooth((t - 2.5) / 1.0)
    if t < 8.5:
        return 0.0
    if t < 9.0:
        return smooth((t - 8.5) / 0.5)
    if t < 9.6:
        return 1.0
    return 1.0 - smooth((t - 9.6) / 0.4)


def post_position(t, width, height):
    """Центр карточки поста (x, y) в пикселях: по дуге от круга TG к кругу MAX."""
    p = smooth((t - FLIGHT_START) / (FLIGHT_END - FLIGHT_START))
    x = width * (0.2 + 0.6 * p)
    y = height * (0.55 - 0.22 * math.sin(math.pi * p))
    return x, y


def background(width, height):
    ramp = np.linspace(0.0, 1.0, height)[:, None]
    col = TOP_COLOR[None, None, :] * (1 - ramp[..., None]) + BOTTOM_COLOR[None, None, :] * ramp[..., None]
    return np.broadcast_to(col, (height, width, 3)).astype(np.uint8).copy()


def load_font(size):
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _draw_alpha_text(img, text, font, xy, alpha):
    if alpha <= 0:
        return
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, text, font=font, fill=(255, 255, 255, int(255 * alpha)), anchor="mm")
    img.alpha_composite(layer)


def make_frame(t, width=1280, height=720, _cache={}):
    key = (width, height)
    if key not in _cache:
        _cache[key] = (background(width, height), load_font(max(12, height // 14)), load_font(max(10, height // 30)))
    bg, big, small = _cache[key]
    img = Image.fromarray(bg).convert("RGBA")
    a = text_alpha(t)
    _draw_alpha_text(img, TITLE, big, (width / 2, height * 0.24), a)

    tg_x, tg_y = width * 0.2, height * 0.55
    mx_x, mx_y = width * 0.8, height * 0.55
    r = height * 0.09
    draw = ImageDraw.Draw(img)
    draw.ellipse([tg_x - r, tg_y - r, tg_x + r, tg_y + r], fill=TG_COLOR)
    draw.ellipse([mx_x - r, mx_y - r, mx_x + r, mx_y + r], fill=MAX_COLOR)
    _draw_alpha_text(img, "Telegram", small, (tg_x, tg_y + r * 1.9), 1.0 if t >= 0.0 else 0.0)
    _draw_alpha_text(img, "MAX", small, (mx_x, mx_y + r * 1.9), 1.0 if t >= 0.0 else 0.0)

    if FLIGHT_START <= t < FLIGHT_END + 0.4:                       # карточка поста
        px, py = post_position(t, width, height)
        appear = smooth((t - FLIGHT_START) / 0.3)
        gone = 1.0 - smooth((t - (FLIGHT_END - 0.3)) / 0.4)
        card_a = clamp(min(appear, gone))
        cw, ch = width * 0.16, height * 0.12
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.rounded_rectangle([px - cw / 2, py - ch / 2, px + cw / 2, py + ch / 2], radius=height * 0.02,
                             fill=(255, 255, 255, int(235 * card_a)))
        ld.text((px, py), "новый пост", font=small, fill=(40, 40, 60, int(255 * card_a)), anchor="mm")
        img.alpha_composite(layer)
    return np.asarray(img.convert("RGB"))


def render(out, width=1280, height=720, fps=30):
    from moviepy import VideoClip

    clip = VideoClip(lambda t: make_frame(t, width, height), duration=DURATION)
    clip.write_videofile(out, fps=fps, codec="libx264", audio=False, preset="medium", logger=None)
    clip.close()
    return out


def main():
    parser = argparse.ArgumentParser(description="Промо-видео Reloader (10 с) для приветствия бота.")
    parser.add_argument("--out", default="welcome.mp4", help="куда сохранить, по умолчанию welcome.mp4")
    parser.add_argument("--size", default="1280x720", help="ширина x высота, например 1920x1080")
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()
    width, height = (int(v) for v in args.size.lower().split("x"))
    render(args.out, width, height, args.fps)
    print(f"Готово: {os.path.abspath(args.out)} ({DURATION:.0f} с, {width}x{height}, {args.fps} fps)")


if __name__ == "__main__":
    main()
