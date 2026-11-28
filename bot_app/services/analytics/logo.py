"""Нейтральный знак для карточек аналитики."""

from functools import lru_cache

from PIL import Image, ImageDraw


@lru_cache(maxsize=1)
def lockup():
    image = Image.new("RGBA", (440, 88))
    draw = ImageDraw.Draw(image)
    for index, height in enumerate((30, 48, 38, 61, 72)):
        x = 42 + index * 48
        draw.rounded_rectangle((x, 80 - height, x + 29, 80), radius=8, fill=(48, 210, 240, 255))
    draw.line((300, 68, 336, 42, 360, 53, 402, 18), fill=(180, 117, 252, 255), width=8, joint="curve")
    return image
