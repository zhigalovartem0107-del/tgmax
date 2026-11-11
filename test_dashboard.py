"""🖼 Дашборд Reloader (свой дизайн): тема по московскому времени — 06:00–17:00 светлая, 17:00–06:00 тёмная
(#0A0A0A, белый текст, #9B30FF и #FF1493); фон — случайное фото с picsum.photos (без ключей; адрес, тип, размер
проверяем; одно на сутки), сильно размытое под вуалью, но видное; picsum недоступен — свой премиальный градиент
(фиолетовый, розовый, чёрный) на весь холст; водяной знак «Reloader» — на КАЖДОЙ картинке."""
import calendar
import io
import json
import os
import random
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from reloader.services import analytics_image as A  # noqa: E402

try:
    from reloader.services.analytics import backgrounds as BGS
except ImportError:
    BGS = None
try:
    from reloader.services.analytics import potential as POT
except ImportError:
    POT = None
from reloader.services.analytics import predict as P  # noqa: E402

PURPLE, PINK, WHITE, BLACK = (0x9B, 0x30, 0xFF), (0xFF, 0x14, 0x93), (0xFF, 0xFF, 0xFF), (0x0A, 0x0A, 0x0A)
FAILS = []


def check(fn):
    try:
        fn()
        print(f"{fn.__name__} ok: {fn.__doc__}")
    except Exception as e:
        FAILS.append(fn.__name__)
        line = traceback.extract_tb(e.__traceback__)[-1]
        print(f"{fn.__name__} FAIL (строка {line.lineno}: {line.line}) — {type(e).__name__}: {str(e)[:400]}")
    return fn


def msk(y, m, d, h, mi):
    """Метка времени для московских часов h:mi (МСК = UTC+3)."""
    return calendar.timegm((y, m, d, h, mi, 0)) - 3 * 3600


DAY_TS, NIGHT_TS = msk(2026, 11, 10, 10, 0), msk(2026, 11, 10, 22, 0)
NOW = DAY_TS
POSTS = [{"t": NOW - h * 3600, "views": 900 + h * 7} for h in range(2, 160, 6)]
HIST = {A.day_key(NOW - k * 86400): 5000 - k * 12 for k in range(1, 31)}
NUM = A.channel_numbers("EroxBlox — новости Roblox", 5008, POSTS, HIST, NOW)
NET = A.network_numbers([A.channel_numbers(t, s, POSTS, {}, NOW) for t, s in (("A", 100), ("Бета", 5000))], NOW)
PRED = P.predict("Обновление вышло! Что думаете?", photo=True,
                 history=[{"t": NOW - k * 7200, "views": 700 + k * 9, "text": "пост", "img": k % 2 == 0}
                          for k in range(1, 30)], now=NOW)


def busy_photo(w=1600, h=1000, seed=3):
    """Резкая пёстрая «фотография»: полосы, шум, контрастные квадраты — фон должен её полностью сгладить."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for x in range(0, w, 40):
        for y in range(0, h, 40):
            d.rectangle((x, y, x + 39, y + 39), fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    return img


def lum(img):
    small = img.convert("L").resize((64, 40))
    return sum(i * n for i, n in enumerate(small.histogram())) / (64 * 40)


def edges(img, box):
    """Сколько в области «резких» пикселей: размытый фон их не даёт, буквы и рисунок — дают. Рамку в 1 пиксель не
    считаем: её Pillow не фильтрует, а копирует как есть (светлый фон дал бы «края» по всему периметру)."""
    e = img.convert("L").crop(box).filter(ImageFilter.FIND_EDGES)
    return sum(e.crop((1, 1, e.width - 1, e.height - 1)).histogram()[61:])


def near(img, color, tol=24):
    return sum(n for n, c in img.getcolors(1 << 24) if all(abs(c[i] - color[i]) <= tol for i in range(3)))


@check
def d1():
    """тема по Москве: 06:00–16:59 — светлая, 17:00–05:59 — тёмная; цвета тем"""
    for (h, mi), want in (((5, 59), "dark"), ((6, 0), "light"), ((12, 0), "light"), ((16, 59), "light"),
                          ((17, 0), "dark"), ((23, 30), "dark"), ((0, 0), "dark")):
        assert A.theme_for(msk(2026, 11, 10, h, mi)) == want, (h, mi, A.theme_for(msk(2026, 11, 10, h, mi)))
    dark, light = A.THEMES["dark"], A.THEMES["light"]
    assert (dark["bg"], dark["text"], dark["accent"], dark["accent2"]) == (BLACK, WHITE, PURPLE, PINK), dark
    assert sum(light["bg"]) / 3 > 230 and sum(light["text"]) / 3 < 50, light          # светлый фон, тёмный текст
    assert (light["accent"], light["accent2"]) == (PURPLE, PINK)


@check
def d2():
    """картинки рисуются в теме по времени: днём — светлая (тёмный текст), ночью — тёмная (белый текст);
    фиолетовый и розовый — в обеих"""
    for name, render in (("канал", lambda **k: A.render_channel(NUM, **k)), ("сеть", lambda **k: A.render_network(NET, **k)),
                         ("прогноз", lambda **k: A.render_predict(PRED, channel="EroxBlox", **k))):
        day, night = render(now=DAY_TS), render(now=NIGHT_TS)
        assert day.size == night.size == A.SIZE == (1280, 800) and day.mode == "RGB", (name, day.size)
        assert lum(day) > 170 and lum(night) < 70, (name, lum(day), lum(night))
        assert near(night, WHITE, 12) > 400 and near(day, (20, 20, 24), 26) > 400, name
        for img in (day, night):
            assert near(img, PURPLE) > 150 and near(img, PINK) > 150, name
        assert lum(render(now=NIGHT_TS, theme="light")) > 170, (name, "тема, заданная явно, главнее часов")


@check
def d3():
    """фон-фото: сильно размыто (резких мест нет), под вуалью (ночью — тёмной, днём — светлой), но ВИДНО — цвета фото
    проступают; без фото — свой премиальный градиент (фиолетовый, розовый, чёрный) на весь холст, а не пустой цвет"""
    photo = busy_photo()
    for theme, ok in (("dark", lambda v: v < 75), ("light", lambda v: v > 175)):
        back = A.backdrop(A.SIZE, theme, photo).convert("RGB")
        assert back.size == A.SIZE and ok(lum(back)), (theme, lum(back))
        assert edges(back, (0, 0) + A.SIZE) < 50, (theme, edges(back, (0, 0) + A.SIZE))
        green = A.backdrop(A.SIZE, theme, Image.new("RGB", (1280, 800), (0, 200, 40))).convert("RGB").resize((32, 20))
        r_, g_, _ = [sum(px[i] for px in green.getdata()) / 640 for i in range(3)]
        assert g_ - r_ > 25, (theme, "фото видно сквозь вуаль", round(r_), round(g_))
        grad = A.backdrop(A.SIZE, theme).convert("RGB")                       # фото нет — градиент на весь холст
        small = grad.convert("L").resize((64, 40))
        spread = max(small.getdata()) - min(small.getdata())
        assert spread > 25 and edges(grad, (0, 0) + A.SIZE) < 50, (theme, "не пустой цвет, а мягкий градиент", spread)
        assert near(grad, PURPLE, 60) + near(grad, PINK, 60) > 2000 or theme == "light", theme
        for corner in ((5, 5), (1274, 5), (5, 794), (1274, 794)):           # закрыт весь холст, до самых краёв
            assert grad.getpixel(corner) != (0, 0, 0), (theme, corner)
    for theme in ("dark", "light"):
        g1, g2 = A.mesh_gradient(800, 500, 7, theme), A.mesh_gradient(800, 500, 7, theme)
        assert g1.size == (800, 500) and g1.tobytes() == g2.tobytes(), "тот же seed — тот же градиент"
        assert g1.tobytes() != A.mesh_gradient(800, 500, 8, theme).tobytes(), "другой seed — другой"
    assert lum(A.mesh_gradient(640, 400, 1, "dark")) < 70 and lum(A.mesh_gradient(640, 400, 1, "light")) > 180
    for w, h in ((0, 5), (1, 1), (-3, 4), (None, "x")):                             # кривые размеры — не падаем
        g = A.mesh_gradient(w, h, 1, "dark")
        assert g.size[0] >= 1 and g.size[1] >= 1


class Resp:
    def __init__(self, status=200, data=None, headers=None, content=b"", url=None):
        self.status_code, self.data, self.headers, self.content = status, data, headers or {}, content
        self.text = json.dumps(data) if data is not None else ""
        if url:                                          # куда на самом деле привёл запрос (после переадресаций)
            self.url = url

    def json(self):
        if self.data is None:
            raise ValueError("not json")
        return self.data

    def iter_content(self, n=65536):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]


class HTTP:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, headers=None, timeout=None, stream=False):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        r = self.routes.get(url.split("?")[0])
        if isinstance(r, Exception):
            raise r
        return r(url, params) if callable(r) else (r or Resp(404))


def jpeg(w=1280, h=800, color=(40, 90, 60)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


FINAL = "https://fastly.picsum.photos/id/1015/1280/800.jpg?hmac=abc"                 # куда переадресует picsum


@check
def d4():
    """фон-фото без ключей: случайное фото с https://picsum.photos/1280/800 (переадресация — только на picsum.photos
    по https), тип — картинка, до 8 МБ, открывается; одно фото на сутки по МСК (кэш); сбой — без падения, повтор не
    раньше чем через 30 минут; Unsplash и его ключа больше нет"""
    assert BGS is not None, "нет модуля reloader.services.analytics.backgrounds"
    assert BGS.PICSUM_URL == "https://picsum.photos/1280/800"
    for path in ("reloader/services/analytics/backgrounds.py", "tg_to_max.py", "README.txt"):
        src = open(os.path.join(ROOT, path), encoding="utf-8").read()
        assert "UNSPLASH_ACCESS_KEY" not in src and "api.unsplash.com" not in src, (path, "Unsplash убран полностью")
    d = tempfile.mkdtemp()
    cache = os.path.join(d, "bg_daily.json")
    good = {BGS.PICSUM_URL: Resp(200, None, {"Content-Type": "image/jpeg"}, jpeg(), url=FINAL)}
    http = HTTP(good)
    img, info = BGS.daily_photo(http, cache, DAY_TS)
    assert img is not None and img.size == (1280, 800), info
    url, params, headers = http.calls[0]
    assert url == BGS.PICSUM_URL and not params and "Authorization" not in headers, http.calls[0]
    saved = json.load(open(cache))
    assert saved["url"] == FINAL and saved["day"] == BGS.msk_day(DAY_TS), saved
    n_calls = len(http.calls)
    img2, _ = BGS.daily_photo(http, cache, DAY_TS + 3600)                       # тот же день — из кэша
    assert img2 is not None and len(http.calls) == n_calls, http.calls[n_calls:]
    BGS.daily_photo(http, cache, DAY_TS + 86400)                                # новый день — новое фото
    assert len(http.calls) == n_calls + 1 and http.calls[n_calls][0] == BGS.PICSUM_URL

    def refused(resp, label):
        c = os.path.join(tempfile.mkdtemp(), "bg.json")
        h = HTTP({BGS.PICSUM_URL: resp})
        got, why_ = BGS.daily_photo(h, c, DAY_TS)
        assert got is None and isinstance(why_, str) and why_, (label, why_)
        return h, c

    for resp, label in ((Resp(500), "picsum ответил 500"),
                        (Resp(200, None, {"Content-Type": "image/jpeg"}, jpeg(), url="https://evil.example.com/x.jpg"),
                         "переадресация на чужой сайт"),
                        (Resp(200, None, {"Content-Type": "image/jpeg"}, jpeg(), url="http://fastly.picsum.photos/x.jpg"),
                         "не https"),
                        (Resp(200, None, {"Content-Type": "text/html"}, b"<html>", url=FINAL), "не картинка"),
                        (Resp(200, None, {"Content-Type": "image/jpeg", "Content-Length": str(20 << 20)}, jpeg(), url=FINAL),
                         "больше 8 МБ"),
                        (Resp(200, None, {"Content-Type": "image/jpeg"}, b"\xff\xd8\xffbroken", url=FINAL), "битая"),
                        (Resp(200, None, {"Content-Type": "image/jpeg"}, jpeg(64, 40), url=FINAL), "слишком маленькая"),
                        (ConnectionError("picsum недоступен"), "нет сети")):
        h, c = refused(resp, label)
        assert BGS.daily_photo(h, c, DAY_TS + 600)[0] is None and len(h.calls) == 1, (label, "не долбим picsum")
        BGS.daily_photo(h, c, DAY_TS + 1900)
        assert len(h.calls) == 2, (label, "через 30 минут — снова пробуем")


AD = {"title": "EroxBlox", "subs": 5008, "median": 1150, "low": 920, "high": 1265, "er": 23.0, "posts": 25,
      "joins": 40, "leaves": 12, "total": 28, "series": [4980, 4990, 4985, 5000, 5008]}
POTD = {"title": "EroxBlox", "subs": 5008, "score": 74, "label": "Высокий",
        "parts": {"growth": 70, "engagement": 88, "activity": 60, "reach": 66}, "notes": ["мало истории"],
        "joins": 40, "leaves": 12, "total": 28}


@check
def d5():
    """водяной знак «Reloader» внизу справа — на КАЖДОЙ картинке: канал, сеть, прогноз, потенциал, рекламный охват;
    днём и ночью, с фото и без"""
    photo = busy_photo()
    for theme in ("dark", "light"):
        for bg in (photo, None):
            for name, img in (("охват", A.render_ad_reach(AD, bg=bg, theme=theme, now=NOW)),
                              ("канал", A.render_channel(NUM, bg=bg, theme=theme, now=NOW)),
                              ("сеть", A.render_network(NET, bg=bg, theme=theme, now=NOW)),
                              ("прогноз", A.render_predict(PRED, bg=bg, channel="X", theme=theme, now=NOW)),
                              ("потенциал", A.render_potential(POTD, bg=bg, theme=theme, now=NOW))):
                box = A.watermark_box(img.size)
                assert box[2] == img.size[0] and box[3] == img.size[1] and box[0] > img.size[0] // 2, box
                assert edges(img, box) > 150, (theme, name, bg is not None, edges(img, box))


@check
def d6():
    """оценка потенциала 0–100: рост, вовлечённость, регулярность, охват — сильный канал «Высокий», слабый «Низкий»;
    чего нет — не выдумываем; охват рекламного поста — по медиане просмотров"""
    assert POT is not None, "нет модуля reloader.services.analytics.potential"
    day = 86400
    t0 = NOW - 14 * day
    strong = POT.score(50000, points=[(t0, 46000), (t0 + 7 * day, 48000), (NOW, 50500)],
                       views=[11000 + i * 100 for i in range(20)], times=[NOW - i * 8 * 3600 for i in range(20)], now=NOW)
    assert strong["score"] >= 70 and strong["label"] == "Высокий", strong
    weak = POT.score(300, points=[(t0, 400), (NOW, 300)], views=[3, 5, 4], times=[NOW - 9 * day, NOW - 5 * day, NOW],
                     now=NOW)
    assert weak["score"] < 45 and weak["label"] == "Низкий", weak
    assert POT.score(None, now=NOW)["score"] is None
    only = POT.score(12000, now=NOW)
    assert only["score"] is not None and only["parts"]["growth"] is None and only["parts"]["engagement"] is None, only
    ad = POT.ad_reach(10000, [1000, 1200, 800, 1100])
    assert (ad["median"], ad["low"], ad["high"], ad["er"]) == (1050, 840, 1155, 10.5), ad
    assert POT.ad_reach(10000, [])["median"] is None and POT.ad_reach(None, [5])["er"] is None


@check
def d7():
    """всё рисуется 1280×800 и не падает на мусоре: None, NaN, огромные числа, пустые списки"""
    nan = float("nan")
    junk_ad = {"title": None, "subs": nan, "median": None, "low": None, "high": None, "er": nan, "posts": 0,
               "joins": None, "leaves": None, "total": None, "series": []}
    junk_pot = {"title": "", "subs": None, "score": None, "label": None, "parts": {}, "notes": []}
    for img in (A.render_ad_reach(junk_ad, now=NOW), A.render_potential(junk_pot, now=NOW),
                A.render_ad_reach(dict(AD, subs=10 ** 12, median=10 ** 13, series=[10 ** 12] * 3), now=NIGHT_TS),
                A.render_channel(A.channel_numbers(None, nan, [], {}, NOW), now=NOW),
                A.render_network(A.network_numbers([], NOW), now=NOW),
                A.render_predict(P.predict("", history=[]), now=NOW)):
        assert img.size == (1280, 800) and img.mode == "RGB", img.size
    t = time.perf_counter()
    A.render_ad_reach(AD, bg=busy_photo(), now=NOW)
    assert time.perf_counter() - t < 3, "картинка — быстро"


@check
def d8():
    """прогноз считает «лучшее время» по часам сервера — подпись честная: «время МСК» только на московских часах,
    иначе смещение от UTC (у самостоятельно запущенного бота часы могут быть любые)"""
    old = os.environ.get("TZ")
    try:
        for tz, want in (("MSK-3", "время МСК"), ("UTC0", "время UTC+0"), ("YEKT-5", "время UTC+5"),
                         ("IST-5:30", "время UTC+5:30"), ("EST5", "время UTC-5")):
            os.environ["TZ"] = tz
            time.tzset()
            assert A.tz_label() == want, (tz, A.tz_label())
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


@check
def d9():
    """водяной знак — фирменный логотип Reloader (робот со стрелками и надпись «Reloader»): в углу — его голубой
    цвет; логотип — прозрачный PNG (без чёрного квадрата); на светлой картинке он стоит на своей чёрной плашке,
    как в оригинале"""
    from reloader.services.analytics import logo as LOGO
    lk = LOGO.lockup()
    assert lk.mode == "RGBA" and lk.size == (440, 88) and lk.getpixel((0, 0))[3] == 0, (lk.mode, lk.size)
    for theme in ("dark", "light"):
        for bg in (None, busy_photo()):
            for name, img in (("канал", A.render_channel(NUM, bg=bg, theme=theme, now=NOW)),
                              ("охват", A.render_ad_reach(AD, bg=bg, theme=theme, now=NOW))):
                cols = img.crop(A.watermark_box(img.size)).getcolors(1 << 20)
                cyan = sum(n for n, (r, g, b) in cols if b > 200 and g > 140 and r < 110)
                assert cyan > 80, (theme, name, bg is not None, "голубой цвет логотипа", cyan)
                if theme == "light":
                    dark = sum(n for n, (r, g, b) in cols if r + g + b < 90)
                    assert dark > 1500, (name, bg is not None, "логотип на своей чёрной плашке", dark)


if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    sys.exit(1)
print("ВСЕ ТЕСТЫ ДАШБОРДА ПРОШЛИ")
