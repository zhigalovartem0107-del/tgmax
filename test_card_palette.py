"""🎨 Карточки аналитики (канал, сеть, прогноз) — строго в четырёх цветах: фон #0A0A0A, линии и заголовки
#9B30FF, цифры и акценты #FF1493, текст #FFFFFF. Других цветов нет: каждый пиксель — смесь этих четырёх
(сглаженные края букв и линий), даже с фоном-узором, картинкой от ИИ или цветной аватаркой канала."""
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from PIL import Image  # noqa: E402

from reloader.services import analytics_image as A  # noqa: E402
from reloader.services.analytics import predict as P  # noqa: E402

BG, PURPLE, PINK, WHITE = (0x0A, 0x0A, 0x0A), (0x9B, 0x30, 0xFF), (0xFF, 0x14, 0x93), (0xFF, 0xFF, 0xFF)
FAILS = []


def check(fn):
    try:
        fn()
        print(f"{fn.__name__} ok: {fn.__doc__}")
    except Exception as e:                               # и «нет такой функции» — тоже провал проверки, а не падение
        FAILS.append(fn.__name__)
        line = traceback.extract_tb(e.__traceback__)[-1]
        print(f"{fn.__name__} FAIL (строка {line.lineno}: {line.line}) — {type(e).__name__}: {str(e)[:400]}")
    return fn


def _solve(m, v):
    """3×3: m (столбцы) · x = v — правило Крамера."""
    def det(a):
        return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    rows = [[m[c][r] for c in range(3)] for r in range(3)]
    d = det(rows)
    out = []
    for c in range(3):
        mc = [[(v[r] if k == c else rows[r][k]) for k in range(3)] for r in range(3)]
        out.append(det(mc) / d)
    return out


EDGES = [tuple(c[i] - BG[i] for i in range(3)) for c in (PURPLE, PINK, WHITE)]


def in_palette(c, eps=0.03):
    """Цвет — смесь фона, фиолетового, розового и белого (внутри их «тетраэдра» в RGB)."""
    a, b, g = _solve(EDGES, [c[i] - BG[i] for i in range(3)])
    return a >= -eps and b >= -eps and g >= -eps and a + b + g <= 1 + eps


def foreign(img):
    return [(n, c) for n, c in img.getcolors(1 << 24) if not in_palette(c)]


NOW = time.time()
POSTS = [{"t": NOW - h * 3600, "views": 900 + h * 7} for h in range(2, 160, 6)]
HIST = {A.day_key(NOW - k * 86400): 5000 - k * 12 for k in range(1, 31)}
NUM = A.channel_numbers("EroxBlox — новости Roblox", 5008, POSTS, HIST, NOW)
NET = A.network_numbers([A.channel_numbers(t, s, POSTS, {}, NOW) for t, s in (("A", 100), ("Бета", 5000), ("C", 300))],
                        NOW)
PRED = P.predict("Вышло большое обновление игры! Что думаете? Пишите в комментариях 👇", photo=True,
                 history=[{"t": NOW - k * 7200, "views": 700 + k * 9, "text": "пост " * k, "img": k % 2 == 0}
                          for k in range(1, 40)], now=NOW)
COLORFUL = A.procedural_background(1200, 900, A.seed_of("Яркий узор"))          # синие, зелёные, оранжевые пятна
PHOTO = Image.new("RGB", (64, 64), (0, 200, 40))                                  # зелёная аватарка канала
AI_PIC = Image.new("RGB", (300, 200), (255, 160, 0))                              # «картинка от ИИ» — оранжевая


@check
def p1():
    """палитра в коде: #0A0A0A, #9B30FF, #FF1493, #FFFFFF"""
    assert (A.BG, A.PURPLE, A.HOTPINK, A.WHITE) == (BG, PURPLE, PINK, WHITE), (A.BG, A.PURPLE, A.HOTPINK, A.WHITE)
    assert [A.hex_color(c) for c in (A.BG, A.PURPLE, A.HOTPINK, A.WHITE)] == ["#0A0A0A", "#9B30FF", "#FF1493", "#FFFFFF"]
    assert set(A.PALETTE) == {BG, PURPLE, PINK, WHITE}


def plain_card_ok(name, img, need=(PURPLE, PINK, WHITE)):
    counts = dict((c, n) for n, c in img.getcolors(1 << 24))
    assert img.getpixel((2, 2)) == BG and img.getpixel((img.width - 3, img.height - 3)) == BG, name
    assert max(counts, key=counts.get) == BG, (name, "фон — глубокий чёрный")
    for c in need:
        assert counts.get(c, 0) > 300, (name, A.hex_color(c), counts.get(c, 0))
    bad = foreign(img)
    assert not bad, (name, sorted(bad, reverse=True)[:5])


@check
def p2():
    """карточка канала: фон #0A0A0A, заголовки и линии #9B30FF, цифры #FF1493, текст #FFFFFF — других цветов нет"""
    plain_card_ok("канал", A.render_channel(NUM))


@check
def p3():
    """сводка по сети — те же четыре цвета"""
    plain_card_ok("сеть", A.render_network(NET))


@check
def p4():
    """карточка прогноза поста: оценка и просмотры — #FF1493, заголовок — #9B30FF, текст — #FFFFFF"""
    img = A.render_predict(PRED, channel="EroxBlox")
    assert img.size == (1200, 675) and img.mode == "RGB", img.size
    plain_card_ok("прогноз", img)
    empty = A.render_predict(P.predict("", history=[]), channel=None)                 # без истории канала — тоже рисуем
    assert empty.size == (1200, 675) and not foreign(empty)


@check
def p5():
    """цветной фон-узор, «картинка от ИИ» и зелёная аватарка — перекрашены в палитру: чужих цветов нет, фон тёмный"""
    for name, img in (("канал+узор+аватар", A.render_channel(NUM, bg=COLORFUL, photo=PHOTO)),
                      ("сеть+картинка ИИ", A.render_network(NET, bg=AI_PIC)),
                      ("прогноз+узор", A.render_predict(PRED, bg=COLORFUL, channel="Канал"))):
        bad = foreign(img)
        assert not bad, (name, sorted(bad, reverse=True)[:5])
        small = img.resize((60, 40))
        mean = [sum(px[i] for px in small.getdata()) / (60 * 40) for i in range(3)]
        assert max(mean) < 90, (name, "фон остаётся тёмным", mean)


if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    sys.exit(1)
print("ВСЕ ТЕСТЫ ЦВЕТОВ КАРТОЧЕК ПРОШЛИ")
