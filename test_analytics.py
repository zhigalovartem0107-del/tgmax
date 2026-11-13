"""📊 Премиум-аналитика: карточки канала и всей сети (Pillow), 🔮 прогноз поста, фоны (фото picsum / свой градиент /
ИИ), после каждой картинки — сообщение ровно в формате 📈/👥/👁,
⏱ скорость меню (учёт времени, «печатает…», нажатия разных людей не ждут друг друга)."""
import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import base64
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import types
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from PIL import Image, ImageFilter  # noqa: E402

from fake_max import FakeMax  # noqa: E402
from reloader.services import analytics_image as A  # noqa: E402
from reloader.services.ai import image as AIIMG  # noqa: E402
from reloader.services.ai import rewrite as RW  # noqa: E402
from reloader.services.ai.keys import KeyRotator  # noqa: E402
from reloader.services.analytics import predict as P  # noqa: E402

DAY = 86400
NOW = time.mktime((2026, 11, 7, 20, 0, 0, 0, 0, -1))      # суббота, 20:00 по местному времени

# 1) цифры карточки канала: подписчики, прирост, просмотры на пост за 24/48/72 ч, ER24
assert A.fmt_int(5008) == "5 008" and A.fmt_int(None) == "—"
assert A.fmt_short(121700) == "121,7K" and A.fmt_short(21000) == "21K" and A.fmt_short(593) == "593"
assert A.fmt_short(2_500_000) == "2,5M" and A.fmt_delta(16) == "+16" and A.fmt_delta(-3) == "−3"
assert A.fmt_pct(17.36) == "17,4%" and A.fmt_pct(None) == "—"
posts = [{"t": NOW - 30 * 3600, "views": 900}, {"t": NOW - 40 * 3600, "views": 1100},   # сутки–двое: 1000
         {"t": NOW - 60 * 3600, "views": 1500}, {"t": NOW - 80 * 3600, "views": 2000},  # 48 ч / 72 ч
         {"t": NOW - 2 * 3600, "views": 50}, {"t": NOW - 30 * 3600, "views": None}]     # свежий и без цифр — нет
assert A.views_by_age(posts, NOW) == {24: 1000, 48: 1500, 72: 2000}, A.views_by_age(posts, NOW)
old = A.views_by_age([{"t": NOW - 100 * 3600, "views": 600}], NOW)       # в окне пусто — все посты старше N ч
assert old == {24: 600, 48: 600, 72: 600}, old
assert A.views_by_age([], NOW) == {24: None, 48: None, 72: None}
hist = {A.day_key(NOW - k * DAY): 5000 - k * 10 for k in range(1, 8)}
days = A.subs_days(hist, 5008, NOW)
assert len(days) == 7 and days[-1] == (A.day_key(NOW), 5008, 18) and days[0][2] == 10, days
n = A.channel_numbers("Канал", 5008, posts, hist, NOW)
assert (n["subs"], n["today"], n["d7"], n["d30"]) == (5008, 18, 78, None), n          # 30 дней — когда есть история
assert (n["v24"], n["v48"], n["v72"], n["er24"]) == (1000, 1500, 2000, 20.0), n
hist30 = {A.day_key(NOW - k * DAY): 5000 - k * 10 for k in range(1, 31)}
assert A.channel_numbers("Канал", 5008, posts, hist30, NOW)["d30"] == 308
nn = A.channel_numbers("Канал", 5008, posts, hist30, NOW)         # доп. поля для 📊-строки под картинкой
assert nn["avg_reach"] == 1100, nn["avg_reach"]                   # медиана просмотров постов (охват)
assert nn["growth_day"] == round(nn["d7"] / 7) and nn["best_day"] in A.RU_WEEKDAYS, nn
empty_n = A.channel_numbers("Канал", None, [], {}, NOW)
assert empty_n["avg_reach"] is None and empty_n["growth_day"] is None and empty_n["best_day"] is None, empty_n
blind = A.channel_numbers("Канал", None, posts, {}, NOW)                   # MAX не сказал подписчиков
assert blind["er24"] is None and blind["today"] is None and blind["d7"] is None
chans = [A.channel_numbers(t, s, posts, {}, NOW) for t, s in (("A", 100), ("B", 5000), ("C", 300), ("D", 2000))]
net = A.network_numbers(chans, NOW)
assert net["subs"] == 7400 and net["v24"] == 4000 and net["v48"] == 6000 and net["n"] == 4
assert net["er"] == round(4000 / 7400 * 100, 1) and [t for t, _, _ in net["top"]] == ["B", "D", "C"], net
print("a1 ok: 📊 подписчики и прирост (сегодня/7/30 дней), просмотры на пост 24/48/72 ч, ER; сеть — сумма и топ-3")

# 2) картинки: размеры, кириллица встроенным шрифтом бота, фон-рисунок от названия канала (один и тот же)
tele = types.ModuleType("telethon"); tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "TGMAX_HOST", "OPENROUTER_API_KEYS",
          "GEMINI_API_KEYS", "DEEPSEEK_API_KEYS", "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY"):
    os.environ.pop(k, None)
os.environ.update(MAX_API_URL=srv.url, YK_API_URL=srv.url)
DATA = tempfile.mkdtemp()
os.chdir(DATA)
tio.wjson({"max_token": "TESTTOKEN", "max_channel_link": ""}, "config.json", "w")
spec = importlib.util.spec_from_file_location("bot", os.path.join(ROOT, "tg_to_max.py"))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
bot.S.trust_env = False

F = bot.card_font
assert F(True, 40).getmask("Подписчики Ж").getbbox() is not None          # кириллица есть (Inter встроен)
assert F(True, 40) is F(True, 40)                                         # шрифт не распаковывается каждый раз
t0 = time.perf_counter()
img = A.render_channel(A.channel_numbers("EroxBlox — новости Roblox", 5008, posts, hist30, NOW), F)
took = time.perf_counter() - t0
assert img.size == A.SIZE == (1280, 800) and img.mode == "RGB" and took < 3, (img.size, took)
long_title = "Очень длинное название канала, которое не помещается в одну строку карточки целиком"
photo = Image.new("RGB", (64, 64), (200, 30, 30))
bg = A.mesh_gradient(1280, 800, A.seed_of(long_title), "dark")
assert A.render_channel(A.channel_numbers(long_title, None, [], {}, NOW), F, bg, photo).size == (1280, 800)
assert A.render_network(net, F).size == (1280, 800)
wide = A.render_network(net, F, A.mesh_gradient(1280, 800, 7, "light"))
assert wide.size == (1280, 800)                                            # с фоном — тот же размер
accent = sum(c for c, px in wide.getcolors(1 << 20) if all(abs(px[i] - A.HOTPINK[i]) < 8 for i in range(3)))
assert accent > 600, accent                                                # акценты и цифры — розовым #FF1493
a1, a2 = A.mesh_gradient(600, 300, 42), A.mesh_gradient(600, 300, 42)
assert a1.tobytes() == a2.tobytes(), "у канала всегда один и тот же градиент"
assert a1.tobytes() != A.mesh_gradient(600, 300, 43).tobytes()
assert not hasattr(A, "procedural_background") and not hasattr(A, "nature_background"), "старые фоны убраны"
print(f"a2 ok: 🖼 карточка канала 1280×800 за {took:.2f} с, сеть 1280×800 (и с фоном); кириллица; свой рисунок")

# 3) 🔮 прогноз поста по истории канала
def at(day, hour):
    return time.mktime((2026, 11, day, hour, 0, 0, 0, 0, -1))


H = []
for d in range(1, 7):                    # 1–6 ноября; 4-е — среда
    for h in (9, 13, 18):
        media = (d + h) % 2 == 0
        H.append({"t": at(d, h), "views": (1000 if media else 400) * (2 if d == 4 else 1) + (300 if h == 18 else 0),
                  "text": "Новость дня " * 8, "img": "http://img" if media else None, "video": False})
pm = P.predict("Вышло обновление! Что думаете?", photo=True, history=H)
pt = P.predict("Вышло обновление, рассказываем, что изменилось в игре за неделю", history=H)
assert sum(pm["reach"]) > 1.3 * sum(pt["reach"]), (pm["reach"], pt["reach"])  # с фото у этого канала — больше
assert any("×" in w and "фото" in w for w in pm["why"]), pm["why"]
assert any("Добавь фото" in t for t in pt["tips"]), pt["tips"]
assert pm["best"] == (2, 18), pm["best"]                                   # среда, 18:00
pa = P.predict("Подпишись на канал t.me/promo — промокод на скидку", history=H)
assert pa["reach"][1] < pt["reach"][1] and any("рекламу" in t for t in pa["tips"]), pa
good = P.predict("Вышло обновление! Что думаете? 🔥", photo=True, history=H, quality=bot.post_quality(
    "Вышло обновление! Что думаете? 🔥", True))
bad = P.predict("ЖМИ СЮДА t.me/x", history=H, quality=bot.post_quality("ЖМИ СЮДА t.me/x", False))
assert 1 <= bad["score"] < good["score"] <= 10, (bad["score"], good["score"])
longs = [dict(p, text="Очень длинный текст. " * 40, views=p["views"] // 3) for p in H[:6]]
pl = P.predict("Очень длинный текст. " * 40, photo=True, history=H + longs)
assert any("Сократи" in t for t in pl["tips"]), pl["tips"]
empty = P.predict("Короткий", history=[])
assert empty["reach"] is None and empty["n"] == 0 and 1 <= empty["score"] <= 10
txt = P.explain(pm, "Мой канал")
assert "«Мой канал»" in txt and "⭐ Оценка:" in txt and "👁 Ожидаемо:" in txt and "⏰ Лучшее время: среда, 18:00" in txt
assert "нет истории" in P.explain(empty)
print("a3 ok: 🔮 прогноз: просмотры по похожим постам канала, медиа/длина/реклама, лучшее время, оценка из 10")

# 4) ИИ: совет к черновику (свои ключи, своё задание) и фон-картинка через OpenRouter
class Resp:
    def __init__(self, code, data):
        self.status_code, self._data, self.headers, self.text = code, data, {}, json.dumps(data)

    def json(self):
        return self._data


class Http:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def post(self, url, **k):
        self.calls.append((url, k))
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


http = Http(Resp(200, {"choices": [{"message": {"content": "Сделай первую строку короче."}}]}))
rot = {"openrouter": KeyRotator("openrouter", lambda: ["k1"])}
cfg = {"openrouter": {"url": "http://ai", "model": "m"}}
assert RW.ask("Черновик поста", rot, http, cfg) == "Сделай первую строку короче."
sent = http.calls[-1][1]
assert sent["json"]["messages"][0]["content"] == RW.ADVICE + "Черновик поста" and sent["timeout"] == 25
RW.rewrite("Пост", rot, http, cfg)
assert http.calls[-1][1]["json"]["messages"][0]["content"] == RW.PROMPT + "Пост" and http.calls[-1][1]["timeout"] == 60
buf = io.BytesIO()
Image.new("RGB", (40, 20), (255, 0, 0)).save(buf, "PNG")
png = buf.getvalue()
ok_img = {"choices": [{"message": {"images": [{"image_url": {"url": "data:image/png;base64,"
                                                                    + base64.b64encode(png).decode()}}]}}]}
assert AIIMG.extract_image(ok_img) == png and AIIMG.extract_image({}) is None
http = Http(Resp(200, ok_img))
assert AIIMG.generate("Новости Roblox", "key", http, "http://ai/") == png
url, k = http.calls[-1]
assert url == "http://ai/chat/completions" and k["json"]["modalities"] == ["image", "text"]
assert "no text" in k["json"]["messages"][0]["content"] and "Новости Roblox" in k["json"]["messages"][0]["content"]
assert AIIMG.generate("x", "key", Http(Resp(402, {"error": "no credits"}))) is None
assert AIIMG.generate("x", "key", Http(OSError("down"))) is None and AIIMG.generate("x", "", http) is None
print("a4 ok: 🤖 совет ИИ к черновику (своё задание, 25 с), фон от OpenRouter; нет ключа/денег/связи — None")

# 5) бот: карточки канала и сети приходят в чат картинкой; подписчики копятся по дням
bot.CFG["admins"] = [1]
bot.CFG["admin_chats"] = {"1": 111}
th1 = bot.THEMES[0]
th1.chat_id = 5000
th2 = bot.create_theme("Второй канал")
th2.chat_id = 6000
srv.subs = {5000: 5008, 6000: 1200}
real_now = time.time()
for cid, base in ((5000, 1000), (6000, 300)):
    srv.history[cid] = [{"timestamp": int((real_now - h * 3600) * 1000),
                         "body": {"mid": f"h{cid}-{h}", "text": f"Пост {h}", "attachments": []},
                         "stat": {"views": base + h}} for h in range(2, 120, 6)]
n_img, n_msg = srv.uploads["image"], len(srv.messages)
bot.anl_cards_send(111, bot.my_channels(), "chan", "pattern")
cards = srv.messages[n_msg:]
assert srv.uploads["image"] == n_img + 2 and len(cards) == 4, (srv.uploads, [c["body"].get("text") for c in cards])
assert all(c["chat_id"] == 111 for c in cards)
assert [bool(c["body"].get("attachments")) for c in cards] == [True, False, True, False], "картинка, потом текст"
assert not any((c["body"].get("text") or "").strip() for c in cards[::2]), "у картинки — никакой подписи"
temp = {str(x["mid"]) for x in bot.AUTODEL["temp"]}
assert not any(str(c["mid"]) in temp for c in cards), "картинки и цифры остаются в чате (не удаляем через час)"
caps = [c["body"]["text"] for c in cards[1::2]]
assert caps[0].startswith("📈 «") and "5 008 подписчиков · сегодня" in caps[0] and "📊 Средний охват:" in caps[0], caps
assert caps[1].startswith("📈 «") and "\n👥 1 200 подписчиков · сегодня" in caps[1] and "📊 Средний охват:" in caps[1], caps
today = A.day_key(time.time())
assert bot.SUBS_HIST["5000"][today] == 5008 and tio.rjson("subs_history.json")["6000"][today] == 1200
srv.subs[5000] = 5020
bot.subs_snapshot_all()                                                   # раз в час (в фоне) — последнее за день
assert tio.rjson("subs_history.json")["5000"][today] == 5020
bot.subs_snapshot(5000, 4990, time.time() - 3 * DAY)
bot.subs_snapshot(5000, 1, time.time() - 100 * DAY)                       # старше 2 месяцев — не храним
assert len(bot.SUBS_HIST["5000"]) == 2, bot.SUBS_HIST["5000"]
n_msg = len(srv.messages)
bot.anl_cards_send(111, bot.my_channels(), "net", "off")
assert len(srv.messages) == n_msg + 2 and not (srv.messages[-2]["body"].get("text") or "").strip(), "без подписи"
assert srv.messages[-2]["body"]["attachments"][0]["type"] == "image" and not srv.messages[-1]["body"].get("attachments")
blocks = srv.messages[-1]["body"]["text"].split("\n\n")                 # после сводки — 📈/👥/👁 по каждому каналу
assert len(blocks) == 2 and all(b.startswith("📈 «") and "📊 Средний охват:" in b for b in blocks), blocks
assert any(a == (111, "sending_photo") for a in srv.actions), srv.actions   # «отправляет фото…» пока рисуем
print("a5 ok: 🖼 карточка на каждый канал и 🌐 сводка сети — картинкой в чат; подписчики по дням в subs_history.json")

# 5b) после картинки — сообщение РОВНО в формате: 📈 «Канал» / 👥 … подписчиков · сегодня …, за 7 дней … /
#     👁 Просмотры на пост: 24 ч — …, 48 ч — …, 72 ч — … · ER24 …%
full = {"title": "EroxBlox — новости Roblox", "subs": 5008, "today": 11, "d7": -83, "v24": 1145, "v48": 1313,
        "v72": 1481, "er24": 22.9, "diag": {"error": "MAX ответил 403"}, "chat_id": 5000}
assert bot.card_caption(full) == ("📈 «EroxBlox — новости Roblox»\n"
                                  "👥 5 008 подписчиков · сегодня +11, за 7 дней −83\n"
                                  "👁 Просмотры на пост: 24 ч — 1 145, 48 ч — 1 313, 72 ч — 1 481 · ER24 22,9%"), \
    bot.card_caption(full)
blank = dict(full, subs=None, today=None, d7=None, v24=None, v48=None, v72=None, er24=None)
assert bot.card_caption(blank) == ("📈 «EroxBlox — новости Roblox»\n👥 — подписчиков · сегодня —, за 7 дней —\n"
                                   "👁 Просмотры на пост: 24 ч — —, 48 ч — —, 72 ч — — · ER24 —"), bot.card_caption(blank)
print("a5b ok: 📈/👥/👁 — ровно три строки в заданном формате; нет данных — «—»")

# 5c) 🤖 вывод ИИ — 4-я строка: свои цифры канала → ИИ (ротация ключей) с заданием «Based on these stats…»;
#     ответ — одной строкой, без разметки, не длиннее 300 знаков; нет ключей или ИИ упал — три строки
from reloader.services.analytics import insight as INS  # noqa: E402
assert "Based on these stats, write a 1-2 sentence conclusion in Russian about the channel's health." in INS.PROMPT
assert "Is it growing? Is engagement high?" in INS.PROMPT
assert INS.clean("**Вывод:** Канал растёт.\nВовлечённость высокая!\n\n- третье\n- четвёртое") == \
    "Канал растёт. Вовлечённость высокая!", INS.clean("**Вывод:** Канал растёт.\nВовлечённость высокая!")
assert INS.clean("Первое. Второе. Третье предложение лишнее.") == "Первое. Второе."
assert len(INS.clean("слово " * 200)) <= 300 and INS.clean("") is None and INS.clean("   **  ") is None
asked = []
real_ready, real_ask = bot.ai_llm_ready, bot.ai_ask_text
bot.ai_llm_ready = lambda: True
bot.ai_ask_text = lambda text, rot, http, settings, log=print, prompt="", timeout=25: (
    asked.append((text, prompt, rot, timeout)), "Канал растёт (+83 за неделю), вовлечённость высокая: ER24 22,9%.")[1]
bot.INSIGHT_CACHE.clear()
got = bot.stats_text(full)
assert got == bot.card_caption(full) + "\n" + bot.info_line(full) + "\n🤖 Канал растёт (+83 за неделю), вовлечённость высокая: ER24 22,9%.", got
assert bot.info_line(full) == "📊 Средний охват: —, Средний прирост: — в день, Лучший день: —", bot.info_line(full)
assert asked[0][0].startswith(bot.card_caption(full)) and asked[0][1] == INS.PROMPT and asked[0][2] is bot.AI_ROTATORS
assert asked[0][3] <= 30, "ИИ ждём недолго — цифры важнее"
assert bot.stats_text(full) == got and len(asked) == 1, "те же цифры — из памяти, ИИ второй раз не спрашиваем"
bot.ai_ask_text = lambda *a, **k: (_ for _ in ()).throw(bot.AiUnavailable("нет ключей"))
assert bot.stats_text(dict(full, subs=6000)) == bot.card_caption(dict(full, subs=6000)) + "\n" + bot.info_line(dict(full, subs=6000)), "ИИ упал — три строки + 📊"
bot.ai_llm_ready = lambda: False
assert bot.stats_text(dict(full, subs=7000)) == bot.card_caption(dict(full, subs=7000)) + "\n" + bot.info_line(dict(full, subs=7000)), "ключей нет — три строки + 📊"
bot.ai_llm_ready, bot.ai_ask_text = real_ready, real_ask
print("a5c ok: 🤖 4-я строка — вывод ИИ по цифрам (ротация ключей), одной строкой, из памяти на те же цифры; "
      "нет ИИ — три строки")

# 6) кнопки экрана «📊 Аналитика»: рисуем в фоне (меню не ждёт), повторное нажатие не запускает заново; фон
text, rows = bot.screen("anl", 1)
pl_ = [b.get("payload") for r in rows for b in r]
assert {"a:anl", "a:anlcard", "a:anlnet", "a:predict", "a:anlbg"} <= set(pl_), pl_
assert "Фон карточек: 🏞 фото дня" in str(rows) and "🔮 Прогноз поста" in text
n_msg = len(srv.messages)
t0 = time.perf_counter()
assert bot.do_action("anlcard", 1) == "anl" and "Рисую" in bot.FLASH.pop(1) and time.perf_counter() - t0 < 1
assert bot.do_action("anlcard", 1) == "anl" and "Уже рисую" in bot.FLASH.pop(1)
end = time.time() + 30
while len(srv.messages) < n_msg + 4 and time.time() < end:
    time.sleep(0.05)
assert len(srv.messages) == n_msg + 4, [m["body"].get("text") for m in srv.messages[n_msg:]]
assert all(m["body"]["attachments"][0]["type"] == "image" for m in srv.messages[n_msg::2])
for want, label in (("off", "🎨 градиент"), ("ai", "🤖 рисует ИИ"), ("pattern", "🏞 фото дня")):
    bot.do_action("anlbg", 1)
    assert bot.anl_bg_mode(1) == want and bot.GCFG["anl_bg"]["1"] == want
    assert label in bot.FLASH.pop(1, ""), label
assert tio.rjson("settings.json")["anl_bg"]["1"] == "pattern"       # выбор фона переживает перезапуск
calls = []
real_gen, real_keys = bot.ai_image.generate, bot.ai_keys
bot.ai_image.generate = lambda topic, key, http, url, model: (calls.append((topic, key, model)), png)[1]
bot.ai_keys = lambda p: ["or-key"] if p == "openrouter" else []
got = bot.card_bg("ai", "Тема канала")
assert got.getpixel((5, 5)) == (255, 0, 0) and calls == [("Тема канала", "or-key", AIIMG.MODEL)], calls
px = bot.card_bg("ai", "Тема канала").getpixel((5, 5))
assert px[0] > 240 and px[1] < 16 and px[2] < 16, px                  # из файла-кэша (JPEG) — тот же красный
assert len(calls) == 1, "фон от ИИ — один раз в неделю на тему (дальше из файла)"
bot.ai_image.generate = lambda *a, **k: None                               # ИИ не смог, picsum в проверках нет —
assert bot.card_bg("ai", "Другая тема") is None                            # None: карточка рисует свой градиент
assert bot.card_bg("off", "x") is None and not hasattr(bot, "anl_background")
bot.ai_image.generate, bot.ai_keys = real_gen, real_keys
print("a6 ok: кнопки «🖼 Карточка канала», «🌐 Сводка», «🔮 Прогноз», «Фон: фото дня/градиент/ИИ»; ИИ не смог — "
      "фото или свой градиент")

# 7) 🔮 прогноз из меню: черновик не уходит в очередь, «печатает…», ответ с оценкой и советом ИИ
real_ready, real_ask = bot.ai_llm_ready, bot.ai_ask_text
bot.ai_llm_ready = lambda: True
bot.ai_ask_text = lambda text, *a, **k: "Добавь в начало главную цифру."
assert bot.do_action("predict", 1) == "predict" and bot.PENDING[1] == "predict"
assert bot.screen("predict", 1)[0] == bot.PREDICT_ASK
queue_before, n_msg = len(bot.QUEUE), len(srv.messages)
srv.actions.clear()
draft = "Вышло большое обновление игры! Что думаете?"
bot.handle_message(1, 111, "Я", draft, {"body": {"text": draft, "attachments": [
    {"type": "image", "payload": {"url": "http://x/p.jpg", "token": "t"}}]}})
assert 1 not in bot.PENDING


def wait_msg(n0, word, sec=20):
    end = time.time() + sec
    while time.time() < end:
        for m in srv.messages[n0:]:
            if word in (m["body"].get("text") or ""):
                return m["body"]["text"]
        time.sleep(0.05)
    raise AssertionError([m["body"].get("text") for m in srv.messages[n0:]])


ans = wait_msg(n_msg, "🔮 Прогноз поста")
assert "«mychannel»" in ans and "⭐ Оценка:" in ans and "👁 Ожидаемо:" in ans and "Добавь в начало главную цифру" in ans, ans
pm = [m for m in srv.messages[n_msg:] if m["chat_id"] == 111]
img_at = next(i for i, m in enumerate(pm) if m["body"].get("attachments") and
              m["body"]["attachments"][0]["type"] == "image")
assert not (pm[img_at]["body"].get("text") or "").strip(), "картинка прогноза — без подписи"
assert "🔮 Прогноз поста" in pm[img_at + 1]["body"]["text"], "сразу за картинкой — ответ"
assert len(bot.QUEUE) == queue_before, "черновик — не в очередь"
assert (111, "typing_on") in srv.actions, srv.actions
n_msg = len(srv.messages)
bot.run_command("predict", "Короткий текст без картинки", 1, 111)          # /predict текст — сразу
assert "Добавь фото" in wait_msg(n_msg, "🔮 Прогноз поста")
bot.ai_llm_ready, bot.ai_ask_text = real_ready, real_ask
print("a7 ok: 🔮 черновик → оценка, просмотры, лучшее время, советы и совет ИИ; «печатает…»; не в очередь")

# 8) ⏱ скорость: учёт по запросам, нажатиям и файлам; медленное — строка в лог и «печатает…»; /perf
assert bot.perf_endpoint("GET", srv.url + "/chats/-70123456/members/me?x=1") == "GET /chats/{id}/members/me"
assert bot.perf_endpoint("POST", "https://api.yookassa.ru/v3/payments/2f9c0a1e-000f-5000-8000-1a2b3c4d5e6f") \
    == "POST api.yookassa.ru/v3/payments/{x}"
assert bot.PERF["http"]["GET /chats/{id}"][0] >= 2 and bot.PERF["http"]["POST /messages"][0] >= 2, bot.PERF["http"]
assert bot.PERF["json"]["subs_history.json"][0] >= 1 and "пересказ поста" not in bot.PERF["ai"]
real_handle = bot.handle_update


def slow_update(u):
    bot.S.get(srv.url + "/me", headers=bot.HEADERS, timeout=5)
    time.sleep(1.7)


bot.handle_update = slow_update
bot.TYPING_AFTER_SEC = 0.2
srv.actions.clear()
lines = []
real_print = bot.print
bot.print = lambda *a, **k: lines.append(" ".join(map(str, a)))
try:
    bot.run_update({"update_type": "message_callback", "callback": {"payload": "m:anl", "user": {"user_id": 1}},
                    "message": {"recipient": {"chat_id": 111}}})
finally:
    bot.print = real_print
assert (111, "typing_on") in srv.actions, srv.actions                     # дольше секунды — «печатает…»
assert lines and "🐢 Медленно: кнопка m:anl" in lines[-1] and "GET /me" in lines[-1], lines
rep = bot.perf_report()
assert "⏱ Скорость бота" in rep and "кнопка m:anl" in rep and "GET /chats/{id}" in rep and "subs_history.json" in rep
assert "🐢 Медленные нажатия" in rep, rep
n_msg = len(srv.messages)
bot.run_command("perf", "", 1, 111)
assert "⏱ Скорость бота" in srv.messages[-1]["body"]["text"] and len(srv.messages) == n_msg + 1
bot.run_command("perf", "сброс", 1, 111)
assert "обнулены" in srv.messages[-1]["body"]["text"] and not bot.PERF["upd"] and not bot.PERF["slow"]
print("a8 ok: ⏱ время по запросам MAX/ЮKassa/ИИ, нажатиям и файлам; медленное — «🐢» в лог и «печатает…»; /perf")

# 9) нажатия разных людей — одновременно (медленный у одного не держит других), у одного — по порядку
done = []


def lane_update(u):
    who, tag = u["callback"]["user"]["user_id"], u["callback"]["payload"]
    if tag == "slow":
        time.sleep(1.2)
    done.append((who, tag))


bot.handle_update = lane_update
a_uid = 7
b_uid = next(x for x in range(8, 100) if zlib.crc32(str(900 + x).encode()) % bot.MENU_LANES   # полоса — по чату
             != zlib.crc32(str(900 + a_uid).encode()) % bot.MENU_LANES)


def cb(uid, payload):
    return {"update_type": "message_callback", "callback": {"payload": payload, "user": {"user_id": uid}},
            "message": {"recipient": {"chat_id": 900 + uid}}}


for u in (cb(a_uid, "slow"), cb(a_uid, "next"), cb(b_uid, "fast")):
    bot.dispatch_update(u)
time.sleep(0.4)
assert done == [(b_uid, "fast")], done                                    # чужое медленное не держит
assert bot.lanes_wait(10)
assert done == [(b_uid, "fast"), (a_uid, "slow"), (a_uid, "next")], done   # свои нажатия — по порядку
bot.handle_update = real_handle
print(f"a9 ok: ⚡ меню — {bot.MENU_LANES} полосы: медленное у одного не держит остальных, свои нажатия по порядку")

# 10) ЮKassa: проверку ключей помним 5 минут (экран «Тарифы» не ждёт ЮKassa), кнопка «Проверить» — заново
bot.GCFG["yk_shop"], bot.GCFG["yk_key"] = "123456", "test_secret"
before = bot.PERF["http"].get("GET /v3/me", [0])[0]
assert bot.yk_check()[0] and bot.yk_check()[0]
assert bot.PERF["http"]["GET /v3/me"][0] == before + 1
bot.yk_check(fresh=True)
assert bot.PERF["http"]["GET /v3/me"][0] == before + 2
assert bot.do_action("ykchk", 1) == "cltar" and not bot.YK_CHECK_CACHE
bot.GCFG["yk_key"] = "test_other"                                         # другие ключи — спрашиваем заново
assert not bot.yk_check()[0] and bot.PERF["http"]["GET /v3/me"][0] == before + 3
print("a10 ok: 💳 проверка ключей ЮKassa — раз в 5 минут; новые ключи и «🔎 Проверить ЮKassa» — сразу")

# 11) новые темы/кабинеты одновременно (меню теперь параллельно) — номера тем не повторяются
made, errs = [], []


def make(i):
    try:
        made.append(bot.create_theme(f"Параллельная {i}").id)
    except Exception as e:
        errs.append(e)


ths = [threading.Thread(target=make, args=(i,)) for i in range(12)]
for t in ths:
    t.start()
for t in ths:
    t.join()
assert not errs and len(made) == 12 and len(set(made)) == 12, (errs, made)
print("a11 ok: 🔒 темы, заведённые в одну секунду из разных «полос» меню, получают разные номера")

# 12) кривые данные не роняют картинки и прогноз: NaN/бесконечность/строки/None в числах, мусор в постах и
#     истории, огромные числа (не вылезают за карточку), странные размеры фона и режимы картинок (P, LA, 1, I;16…)
nan, inf = float("nan"), float("inf")
for v in (nan, inf, -inf, 1e30, -5, "12", None, True, [1]):
    for f in (A.fmt_int, A.fmt_short, A.fmt_delta, A.fmt_pct, A.fmt_big):
        out = f(v)
        assert isinstance(out, str) and ("—" in out) == (v in (None, True, "12", [1]) or v != v or v in (inf, -inf)), (f, v, out)
assert A.fmt_big(12_345_678) == "12,3M" and A.fmt_big(5008) == "5 008"
junk_hist = {A.day_key(NOW - DAY): nan, A.day_key(NOW - 2 * DAY): "5000", "мусор": 3, A.day_key(NOW - 3 * DAY): None,
             A.day_key(NOW - 4 * DAY): -7, A.day_key(NOW - 5 * DAY): 4990.0}
junk_posts = [{"t": None, "views": 5}, {"views": 3}, "x", None, {"t": NOW - 30 * 3600, "views": "7"},
              {"t": 1e20, "views": 9}, {"t": NOW - 30 * 3600, "views": -4}, {"t": NOW - 30 * 3600, "views": 800}]
jn = A.channel_numbers(None, nan, junk_posts, junk_hist, NOW)
assert jn["title"] == "Канал" and jn["subs"] is None and jn["v24"] == 800, jn
assert [v for _, v, _ in jn["days"] if v is not None] == [4990], jn["days"]          # из истории — только честное
assert A.render_channel(jn, F).size == (1280, 800)
huge = A.channel_numbers("Огромный канал", 10 ** 12, [{"t": NOW - 30 * 3600, "views": 10 ** 13}], {}, NOW)
img = A.render_channel(huge, F, theme="dark")
gutter = img.convert("L").crop((329, 150, 342, 280)).filter(ImageFilter.FIND_EDGES).crop((1, 1, 12, 129))
assert img.size == (1280, 800) and not sum(gutter.histogram()[61:]), "огромные числа не вылезли из плитки"
for w, h in ((0, 5), (1, 1), (5, 0), (-3, 4), (None, "x")):
    bgx = A.mesh_gradient(w, h, 1)
    assert bgx.size[0] >= 1 and bgx.size[1] >= 1
for mode in ("P", "LA", "1", "I;16", "CMYK", "F", "I", "RGBA"):
    assert A.render_channel(jn, F, bg=Image.new(mode, (40, 30)), photo=Image.new(mode, (40, 30))).size == (1280, 800)
assert A.render_network(A.network_numbers([], NOW), F, Image.new("RGB", (1, 1))).size == (1280, 800)
net_junk = A.network_numbers([{"title": None, "subs": nan, "v24": nan}, "мусор", None, {"subs": 50, "v24": 10}], NOW)
assert net_junk["subs"] == 50 and net_junk["v24"] == 10 and net_junk["n"] == 2 and net_junk["top"][0][0] == "Канал"
assert A.render_network(net_junk, F).size == (1280, 800)
pj = P.predict(12345, history=[{"views": 5}, {"t": None, "views": 1}, {"t": 1e20, "views": 3}, {"t": NOW, "views": "x"},
                               "junk", None, {"t": NOW - 3600, "views": nan}])
assert pj["n"] == 0 and pj["reach"] is None and "нет истории" in P.explain(pj)
assert P.predict("слово " * 50000)["features"]["words"] == 50000
print("a12 ok: 🛡 NaN/бесконечность/строки/мусор в числах, постах и истории — «—» вместо падения; огромные числа "
      "сжаты в карточку; любые размеры фона и режимы картинок; прогноз на мусорной истории — «нет истории»")

# 13) испорченный subs_history.json и пустые каналы: карточка и прогноз всё равно приходят
tio.wjson({"5000": {A.day_key(time.time() - DAY): float("nan"), "кривая дата": 5, A.day_key(time.time() - 2 * DAY): 4000},
           "6000": "не словарь", "7000": [1, 2]}, "subs_bad.json", "w")
clean = bot._load_subs("subs_bad.json")
assert clean == {"5000": {A.day_key(time.time() - 2 * DAY): 4000}}, clean
tio.wfile("{сломано", "subs_bad.json", "w")
assert bot._load_subs("subs_bad.json") == {}
tio.wfile("[1, 2, 3]", "subs_bad.json", "w")
assert bot._load_subs("subs_bad.json") == {}
srv.chats["empty"] = 9100
srv.history[9100] = []
n_msg = len(srv.messages)
bot.anl_cards_send(111, [("Пустой канал", 9100, [])], "chan", "off")
cap = srv.messages[-1]["body"]["text"]
assert len(srv.messages) == n_msg + 2 and "на пост: 24 ч — —" in cap and srv.messages[-2]["body"]["attachments"], cap
bot.anl_cards_send(111, [("Пустой канал", 9100, []), ("Тоже пустой", 9100, [])], "net", "off")
assert srv.messages[-2]["body"]["attachments"] and srv.messages[-1]["body"]["text"].count("📈 «") == 2
ans = bot.predict_text("Пост без истории канала", cid=9100, name="Пустой канал")
assert "нет истории" in ans and "⭐ Оценка" in ans
srv.history.pop(9100)
bot.PRED_HIST.clear()
real_get = bot.S.get
bot.S.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("MAX лёг"))
try:
    ans = bot.predict_text("Пост, а MAX не отвечает", cid=4242, name="Канал")
finally:
    bot.S.get = real_get
assert "⭐ Оценка" in ans and "нет истории" in ans, ans
for i in range(30):
    bot.PRED_HIST[10_000 + i] = (time.time() - (2000 if i < 10 else 0), [])
bot.PRED_HIST.pop(9100, None)
srv.history[9100] = []
bot.predict_history(9100)
assert len(bot.PRED_HIST) <= bot.PRED_HIST_MAX and 9100 in bot.PRED_HIST, len(bot.PRED_HIST)
print("a13 ok: 💾 испорченный subs_history.json — честное берём, мусор отбрасываем; пустой канал — карточка с «—» и "
      "сводка; прогноз без истории и при лежащем MAX; память прогнозов не растёт")

# 14) ИИ-фон: нет ключа, мусор вместо картинки, огромная картинка — фон всегда есть (фото или градиент), не падаем
real_gen, real_keys = bot.ai_image.generate, bot.ai_keys
gen_calls = []
bot.ai_keys = lambda p: []
bot.ai_image.generate = lambda *a, **k: gen_calls.append(a) or png
assert bot.card_bg("ai", "Без ключа") is None and not gen_calls
bot.ai_keys = lambda p: ["k"] if p == "openrouter" else []
for bad in (b"not an image", b"", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20):
    bot.ai_image.generate = lambda *a, bad=bad, **k: bad
    assert bot.card_bg("ai", f"Мусор {len(bad)}") is None
assert AIIMG.extract_image({"choices": [{"message": {"images": [{"image_url": {"url": "data:image/png;base64,!!!"}}]}}]}) is None
assert AIIMG.extract_image({"choices": [{"message": {"images": [None, {"image_url": None}]}}]}) is None
assert AIIMG.extract_image({"choices": []}) is None and AIIMG.extract_image({}) is None
bigbuf = io.BytesIO()
Image.new("RGB", (3000, 2000), (0, 120, 0)).save(bigbuf, "JPEG")
bot.ai_image.generate = lambda *a, **k: bigbuf.getvalue()
gbg = bot.card_bg("ai", "Огромная")
assert A.render_network(net, F, gbg).size == (1280, 800)
bot.ai_image.generate, bot.ai_keys = real_gen, real_keys
assert bot.update_label({"update_type": "message_created", "message": {"recipient": {"chat_type": "dialog"},
                                                                       "body": {"text": "/абракадабра123 x"}}}) \
    == "сообщение /…" and bot.update_label({"update_type": "message_created", "message": {
        "recipient": {"chat_type": "dialog"}, "body": {"text": "/perf"}}}) == "сообщение /perf"
print("a14 ok: 🎨 ИИ-фон: нет ключа — без запроса; мусор/пустота/битый PNG — фото или свой градиент; огромная картинка — "
      "вписана; учёт скорости не заводит строку на каждую «/абракадабру»")


# 15) 🏞 фон: случайное фото с picsum.photos — главный источник, без ключей (одно на сутки); picsum недоступен,
#     ответил ошибкой или прислал не картинку — свой градиент (карточка не пустая), без ошибок и без долбёжки
class UResp:
    def __init__(self, status=200, headers=None, content=b"", url=None):
        self.status_code, self.headers, self.content, self.text = status, headers or {}, content, ""
        if url:
            self.url = url

    def iter_content(self, n=65536):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]


class UHTTP:
    """Поддельный picsum: mode — "ok", "500", "html" или "down" (нет сети)."""

    def __init__(self):
        self.calls, self.mode = [], "ok"

    def get(self, url, params=None, headers=None, timeout=None, stream=False):
        self.calls.append(url)
        if self.mode == "down":
            raise bot.requests.exceptions.ConnectionError("picsum недоступен")
        if self.mode == "500":
            return UResp(500)
        if self.mode == "html":
            return UResp(200, {"Content-Type": "text/html"}, b"<html>", "https://fastly.picsum.photos/x")
        buf = io.BytesIO()
        Image.new("RGB", (1280, 800), (30, 90, 160)).save(buf, "JPEG")
        return UResp(200, {"Content-Type": "image/jpeg"}, buf.getvalue(),
                     "https://fastly.picsum.photos/id/1015/1280/800.jpg?hmac=x")


assert os.environ.get("BG_PHOTO_URL") == "", "в проверках фото из интернета не качаем (поддельный MAX так велит)"
real_http, real_file, real_keys, real_log = bot.BG_HTTP, bot.BG_DAILY_FILE, bot.ai_keys, bot._log_line
ulogs = []
u = bot.BG_HTTP = UHTTP()
bot.ai_keys = lambda p: []
bot._log_line = ulogs.append
try:
    bot.BG_DAILY_FILE = os.path.join(tempfile.mkdtemp(), "bg_daily.json")
    assert bot.card_bg("pattern", "Тема") is None and not u.calls, "фото выключено — сразу свой градиент"
    os.environ["BG_PHOTO_URL"] = bot.bgsrc.PICSUM_URL
    for mode in ("500", "html", "down"):
        bot.BG_DAILY_FILE = os.path.join(tempfile.mkdtemp(), "bg_daily.json")
        u.mode, u.calls = mode, []
        assert bot.card_bg("pattern", "Тема канала") is None, (mode, "picsum не дал фото — свой градиент")
        assert bot.card_bg("pattern", "Другая тема") is None and len(u.calls) == 1, (mode, "picsum не долбим")
        card = A.render_channel(A.channel_numbers("Канал", 100, [], {}, NOW), F, None, theme="dark")
        spread = card.convert("L").crop((0, 0, 1280, 140)).getextrema()
        assert card.size == A.SIZE and spread[1] - spread[0] > 40, (mode, "фон не пустой")
    assert any("picsum" in line.lower() for line in ulogs), ulogs                       # причина — в лог хозяину
    bot.BG_DAILY_FILE = os.path.join(tempfile.mkdtemp(), "bg_daily.json")
    u.mode, u.calls = "ok", []
    img = bot.card_bg("pattern", "Тема канала")                                         # picsum отвечает — фото
    assert img is not None and img.size == (1280, 800) and u.calls == [bot.bgsrc.PICSUM_URL], (img, u.calls)
    assert bot.card_bg("ai", "Тема без ИИ").size == (1280, 800), "ИИ не нарисовал — фото"
    u.mode = "down"
    assert bot.card_bg("pattern", "Тема канала").size == (1280, 800) and len(u.calls) == 1, "на сегодня фото уже есть"
    assert bot.card_bg("off", "x") is None
finally:
    bot.BG_HTTP, bot.BG_DAILY_FILE, bot.ai_keys, bot._log_line = real_http, real_file, real_keys, real_log
    os.environ["BG_PHOTO_URL"] = ""
print("a15 ok: 🏞 фон: фото с picsum.photos — главный (без ключей, одно на сутки); picsum лежит, ошибка, не картинка, "
      "нет сети — свой градиент без ошибок, picsum не долбим, причина — в лог хозяину; Unsplash убран")

print("ВСЕ ТЕСТЫ АНАЛИТИКИ ПРОШЛИ")
