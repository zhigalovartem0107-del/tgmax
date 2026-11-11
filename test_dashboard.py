"""🖼 Новый дашборд Reloader (свой дизайн): тема по московскому времени — 06:00–17:00 светлая, 17:00–06:00 тёмная
(#0A0A0A, белый текст, #9B30FF и #FF1493); фон дня — безопасное фото природы (Unsplash, самый строгий фильтр,
только свои слова, проверка адреса, типа, размера и описания) или свой рисунок природы; сильное размытие и вуаль;
водяной знак «Reloader» — ТОЛЬКО на картинке «Рекламный охват», на обычной аналитике его нет."""
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
    """фон: пёстрая фотография — сильно размыта (резких мест нет) и под вуалью (ночью — тёмной, днём — светлой)"""
    photo = busy_photo()
    for theme, ok in (("dark", lambda v: v < 60), ("light", lambda v: v > 190)):
        back = A.backdrop(A.SIZE, theme, photo).convert("RGB")
        assert back.size == A.SIZE and ok(lum(back)), (theme, lum(back))
        assert edges(back, (0, 0) + A.SIZE) < 50, (theme, edges(back, (0, 0) + A.SIZE))
    for topic in ("mountains", "forest", "ocean", "abstract"):                  # свой рисунок природы — без сети
        nat = A.nature_background(800, 500, topic, 7)
        assert nat.size == (800, 500) and nat.tobytes() == A.nature_background(800, 500, topic, 7).tobytes()


class Resp:
    def __init__(self, status=200, data=None, headers=None, content=b"", url=None):
        self.status_code, self.data, self.headers, self.content = status, data, headers or {}, content
        if url:                                          # куда на самом деле привёл запрос (после переадресаций)
            self.url = url
        self.text = json.dumps(data) if data is not None else ""

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
        return r(url, params) if callable(r) else (r or Resp(404))


def jpeg(w=1080, h=720, color=(40, 90, 60)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


PHOTO_URL = "https://images.unsplash.com/photo-1?ixid=abc&w=1080"


def photo_json(**over):
    data = {"id": "p1", "description": "Misty mountain ridge at dawn", "alt_description": "green mountains under sky",
            "urls": {"regular": PHOTO_URL}, "links": {"download_location": "https://api.unsplash.com/photos/p1/download"},
            "user": {"name": "Anna K"}, "tags": [{"title": "mountain"}]}
    data.update(over)
    return data


@check
def d4():
    """фото дня — безопасно: только слова mountains/forest/ocean/abstract, самый строгий фильтр Unsplash, адрес
    только images.unsplash.com по https (и после переадресаций), тип — картинка, размер — до 8 МБ, описание без «взрослых» слов; кэш на сутки
    по МСК; без ключа — в сеть не ходим; сбой — без падения, повтор не раньше чем через час"""
    assert BGS is not None, "нет модуля reloader.services.analytics.backgrounds"
    assert tuple(BGS.SAFE_TOPICS) == ("mountains", "forest", "ocean", "abstract")
    d = tempfile.mkdtemp()
    cache = os.path.join(d, "bg_daily.json")
    good = {BGS.API_URL: Resp(200, photo_json()),
            "https://images.unsplash.com/photo-1": Resp(200, None, {"Content-Type": "image/jpeg"}, jpeg()),
            "https://api.unsplash.com/photos/p1/download": Resp(200, {"url": PHOTO_URL})}
    http = HTTP({})
    img, why = BGS.daily_photo(http, "", cache, DAY_TS)
    assert img is None and not http.calls, "без ключа — ни одного запроса"
    http = HTTP(good)
    img, info = BGS.daily_photo(http, "KEY", cache, DAY_TS)
    assert img is not None and img.size == (1080, 720), info
    url, params, headers = http.calls[0]
    assert url == BGS.API_URL and params["content_filter"] == "high" and params["query"] in BGS.SAFE_TOPICS, params
    assert params.get("orientation") == "landscape" and headers.get("Authorization") == "Client-ID KEY", headers
    saved = json.load(open(cache))
    assert saved["url"] == PHOTO_URL and saved["day"] == BGS.msk_day(DAY_TS) and saved["topic"] in BGS.SAFE_TOPICS
    n_calls = len(http.calls)
    img2, _ = BGS.daily_photo(http, "KEY", cache, DAY_TS + 3600)                 # тот же день — из кэша
    assert img2 is not None and len(http.calls) == n_calls, http.calls[n_calls:]
    BGS.daily_photo(http, "KEY", cache, DAY_TS + 86400)                          # новый день — новое фото
    assert len(http.calls) > n_calls and http.calls[n_calls][1]["query"] in BGS.SAFE_TOPICS
    assert "Anna K" in (BGS.credit(cache, DAY_TS + 86400) or "") and "Unsplash" in BGS.credit(cache, DAY_TS + 86400)

    def refused(routes, label):
        c = os.path.join(tempfile.mkdtemp(), "bg.json")
        h = HTTP(routes)
        got, why_ = BGS.daily_photo(h, "KEY", c, DAY_TS)
        assert got is None and isinstance(why_, str), (label, why_)
        return h

    h = refused({BGS.API_URL: Resp(200, photo_json(description="nude model on the beach"))}, "взрослое описание")
    assert all("images.unsplash.com" not in u for u, _, _ in h.calls), "картинку даже не скачиваем"
    refused({BGS.API_URL: Resp(200, photo_json(alt_description=None, tags=[{"title": "lingerie"}]))}, "взрослый тег")
    h = refused({BGS.API_URL: Resp(200, photo_json(urls={"regular": "https://evil.example.com/a.jpg"}))}, "чужой сайт")
    assert all("evil" not in u for u, _, _ in h.calls)
    refused({BGS.API_URL: Resp(200, photo_json(urls={"regular": "http://images.unsplash.com/x.jpg"}))}, "не https")
    refused({BGS.API_URL: Resp(200, photo_json()),                                 # переадресация на чужой сайт
             "https://images.unsplash.com/photo-1": Resp(200, None, {"Content-Type": "image/jpeg"}, jpeg(),
                                                         url="https://evil.example.com/photo.jpg")}, "переадресация")
    for resp, label in ((Resp(200, None, {"Content-Type": "text/html"}, b"<html>"), "не картинка"),
                        (Resp(200, None, {"Content-Type": "image/jpeg", "Content-Length": str(20 << 20)}, jpeg()),
                         "больше 8 МБ"),
                        (Resp(200, None, {"Content-Type": "image/jpeg"}, b"\xff\xd8\xffbroken"), "битая картинка"),
                        (Resp(200, None, {"Content-Type": "image/jpeg"}, jpeg(64, 40)), "слишком маленькая")):
        refused({BGS.API_URL: Resp(200, photo_json()), "https://images.unsplash.com/photo-1": resp}, label)
    c = os.path.join(tempfile.mkdtemp(), "bg.json")
    h = HTTP({BGS.API_URL: Resp(500, {"errors": ["boom"]})})
    assert BGS.daily_photo(h, "KEY", c, DAY_TS)[0] is None
    BGS.daily_photo(h, "KEY", c, DAY_TS + 600)
    assert len(h.calls) == 1, "API не ответил — следующая попытка не раньше чем через час"
    BGS.daily_photo(h, "KEY", c, DAY_TS + 3700)
    assert len(h.calls) == 2
    # лимит запросов Unsplash (403 «Rate Limit Exceeded» или 429) и неверный ключ (401): фото нет, повтор — не
    # раньше чем через час (лимит Unsplash обновляется раз в час), в лог хозяину — понятная причина
    for resp, word in ((Resp(403, None, {"X-Ratelimit-Remaining": "0"}, b"Rate Limit Exceeded"), "лимит"),
                       (Resp(429, {"errors": ["Too many requests"]}), "лимит"),
                       (Resp(401, {"errors": ["OAuth error: The access token is invalid"]}), "ключ")):
        c = os.path.join(tempfile.mkdtemp(), "bg.json")
        h = HTTP({BGS.API_URL: resp})
        got, why_ = BGS.daily_photo(h, "KEY", c, DAY_TS)
        assert got is None and word in why_, (resp.status_code, why_)
        assert BGS.daily_photo(h, "KEY", c, DAY_TS + 1800)[0] is None and len(h.calls) == 1, resp.status_code
        BGS.daily_photo(h, "KEY", c, DAY_TS + 3700)
        assert len(h.calls) == 2, (resp.status_code, "через час — пробуем снова")


AD = {"title": "EroxBlox", "subs": 5008, "median": 1150, "low": 920, "high": 1265, "er": 23.0, "posts": 25,
      "joins": 40, "leaves": 12, "total": 28, "series": [4980, 4990, 4985, 5000, 5008]}
POTD = {"title": "EroxBlox", "subs": 5008, "score": 74, "label": "Высокий",
        "parts": {"growth": 70, "engagement": 88, "activity": 60, "reach": 66}, "notes": ["мало истории"],
        "joins": 40, "leaves": 12, "total": 28}


@check
def d5():
    """водяной знак «Reloader» — внизу справа ТОЛЬКО у «Рекламного охвата» (днём и ночью); у аналитики канала,
    сети, прогноза и оценки потенциала в этом углу пусто"""
    photo = busy_photo()
    for theme in ("dark", "light"):
        ad = A.render_ad_reach(AD, bg=photo, theme=theme, now=NOW)
        box = A.watermark_box(ad.size)
        assert box[2] == ad.size[0] and box[3] == ad.size[1] and box[0] > ad.size[0] // 2, box
        assert edges(ad, box) > 150, (theme, "у рекламной картинки есть знак", edges(ad, box))
        for name, img in (("канал", A.render_channel(NUM, bg=photo, theme=theme, now=NOW)),
                          ("сеть", A.render_network(NET, bg=photo, theme=theme, now=NOW)),
                          ("прогноз", A.render_predict(PRED, bg=photo, channel="X", theme=theme, now=NOW)),
                          ("потенциал", A.render_potential(POTD, bg=photo, theme=theme, now=NOW))):
            assert edges(img, A.watermark_box(img.size)) < 10, (theme, name, edges(img, A.watermark_box(img.size)))


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


if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    sys.exit(1)
print("ВСЕ ТЕСТЫ ДАШБОРДА ПРОШЛИ")
