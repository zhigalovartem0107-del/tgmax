import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
import asyncio, builtins, importlib.util, os, sys, tempfile, time, types
tele = types.ModuleType("telethon"); tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = tele.errors
os.chdir(tempfile.mkdtemp())
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
bot.DEFAULTS["media_auto"] = False; bot.GCFG["media_auto"] = False   # тут проверяем карточку «не вышел как есть»

def wrap(post, inner, date="2024-05-04T12:34:56+00:00"):
    return (f'<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="chan/{post}">'
            f'<div class="tgme_widget_message_bubble">{inner}<div class="tgme_widget_message_footer"><div class="tgme_widget_message_info"><span class="tgme_widget_message_meta">'
            f'<a class="tgme_widget_message_date" href="https://t.me/chan/{post}"><time datetime="{date}" class="time">12:34</time></a></span></div></div></div></div></div>')

def text(t, cls="js-message_text"): return f'<div class="tgme_widget_message_text {cls}" dir="auto">{t}</div>'
def photo(pid, url): return f'<a class="tgme_widget_message_photo_wrap 5 js-message_photo" href="https://t.me/chan/{pid}" style="width:453px;background-image:url(\'{url}\')"></a>'

PAGE = "<html><body><section class='tgme_channel_history js-message_history'>" + "".join([
    wrap(100, text('Привет <b>жирный</b> и <i>курсив</i><br/>вторая строка <a href="https://example.com/x">ссылка</a> и <a href="https://t.me/chan">https://t.me/chan</a> &amp; ещё')),
    wrap(101, photo(101, "https://cdn4.cdn-telegram.org/file/p101.jpg") + text("Одно фото с подписью")),
    wrap(105, photo(103, "//cdn4.cdn-telegram.org/file/p103.jpg") + photo(104, "https://cdn/p104.jpg") + photo(105, "https://cdn/p105.jpg")
         + text("Альбом из трёх фото")),
    wrap(106, '<div class="tgme_widget_message_reply"><div class="tgme_widget_message_text js-message_reply_text">цитата ответа</div></div>'
         + '<div class="tgme_widget_message_video_wrap"><a class="tgme_widget_message_video_player js-message_video_player" href="https://t.me/chan/106">'
           '<i class="tgme_widget_message_video_thumb" style="background-image:url(\'https://cdn/t.jpg\')"></i><video src="https://cdn/v106.mp4" class="tgme_widget_message_video js-message_video"></video></a></div>'
         + text("Видео про новый режим")),
    wrap(107, '<div class="tgme_widget_message_video_wrap"><a class="tgme_widget_message_video_player not_supported" href="https://t.me/chan/107"><i class="tgme_widget_message_video_thumb" style="background-image:url(\'https://cdn/t.jpg\')"></i></a></div>' + text("Большое видео без ссылки")),
    '<div class="tgme_widget_message_wrap"><div class="tgme_widget_message service_message">Канал создан</div></div>',
]) + "</section></body></html>"

msgs = bot.parse_web_page("chan", PAGE)
ids = [m.id for m in msgs]
assert ids == [100, 101, 103, 104, 105, 106, 107], ids
by = {m.id: m for m in msgs}
assert "**жирный**" in by[100].text and "__курсив__" in by[100].text and "\n" in by[100].text
assert "[ссылка](https://example.com/x)" in by[100].text and "https://t.me/chan" in by[100].text and "&" in by[100].text and "&amp;" not in by[100].text
assert by[101].photo.url.endswith("p101.jpg") and by[101].text == "Одно фото с подписью" and by[101].grouped_id is None
assert by[103].photo.url == "https://cdn4.cdn-telegram.org/file/p103.jpg"               # // -> https://
assert by[103].grouped_id == by[104].grouped_id == by[105].grouped_id == 105
assert by[103].text == "Альбом из трёх фото" and by[104].text == "" and by[105].text == ""
assert by[106].video.url == "https://cdn/v106.mp4" and by[106].text == "Видео про новый режим" and "цитата" not in by[106].text
assert by[107].video is not None and by[107].video.url is None
assert by[100].date.year == 2024 and by[100].date.tzinfo is not None
print("w1 ok: разбор страницы: текст с разметкой, фото, альбом, видео, большое видео, цитата-ответ не берётся")

assert bot.parse_web_page("c", "<div class='tgme_channel_history'></div>") == []
try:
    bot.parse_web_page("c", "<html>tgme_page</html>"); raise SystemExit("должно упасть")
except RuntimeError as e:
    assert "нет веб-версии" in str(e)
print("w2 ok: пустой канал — пусто; закрытый/неверный — понятная ошибка")

# клиент: min_id, limit, ids, листание назад
PAGES = {"https://t.me/s/chan": PAGE,
         "https://t.me/s/chan?before=100": "<section class='tgme_channel_history'>" + wrap(90, text("старый пост 90")) + wrap(95, text("старый пост 95")) + "</section>"}
PAGES["https://t.me/s/chan?before=90"] = "<section class='tgme_channel_history'></section>"
bot.web_fetch = lambda url: PAGES[url]
cl = bot.WebClient()
got = asyncio.run(cl.get_messages("chan", min_id=101, limit=30))
assert [m.id for m in got] == [107, 106, 105, 104, 103], [m.id for m in got]
assert [m.id for m in asyncio.run(cl.get_messages("chan", limit=1))] == [107]
assert asyncio.run(cl.get_messages("chan", ids=106)).id == 106 if False else True
PAGES["https://t.me/s/chan?before=107"] = PAGE
PAGES["https://t.me/s/chan?before=108"] = PAGE
assert asyncio.run(cl.get_messages("chan", ids=106)).id == 106
got = asyncio.run(cl.get_messages("chan", min_id=50, limit=50))
assert 95 in [m.id for m in got] and 90 in [m.id for m in got]
print("w3 ok: клиент отдаёт новые посты, последний пост, пост по id и листает назад")

# интеграция: тот же process_channel, что и с Telegram
SENT, NOTES, API = [], [], []
bot.send_max = lambda chat, t, imgs, vid=None: SENT.append((t[:30], len(imgs), bool(vid)))
bot.api_send = lambda chat, t, rows=None, token=None: API.append((t, rows)) or "m"
bot.notify = lambda t, tag=True, rows=None: NOTES.append(t)
def _dl(url, dest):
    open(dest, "wb").write(b"data")
    return dest
bot._download_url = _dl
bot.GCFG["admin_chats"] = {"1": 111}
bot.CFG["approval_on"] = False
bot.CFG["dedupe_on"] = False
bot.LAST_IDS["chan"] = 99
import time as _t
MONO = [500.0]
_t.monotonic = lambda: MONO[0]
asyncio.run(bot.process_channel(cl, "chan", {}))
MONO[0] += 10
asyncio.run(bot.process_channel(cl, "chan", {}))
q = {x["id"]: x for x in bot.QUEUE}
assert set(q) == {"chan:100", "chan:101", "chan:103", "chan:106", "chan:107"}, list(q)
assert len(q["chan:103"]["photos"]) == 3 and q["chan:103"]["pids"] == [103, 104, 105]
assert q["chan:106"]["vids"] == [106] and q["chan:107"]["vids"] == [107]
assert "жирный" in q["chan:100"]["text"] and "t.me" not in q["chan:100"]["text"]
assert any("фото 3, видео 0" in j and "Альбом" not in j for j in bot.JOURNAL), list(bot.JOURNAL)[-6:]
print("w4 ok: process_channel работает с веб-читалкой так же, как с Telegram")

# больше 30 новых сообщений: самый старый альбом обрезан (нет первого фото с текстом) — не публикуем его калекой
BIG = "<section class='tgme_channel_history'>" + wrap(302, photo(301, "https://cdn/b1.jpg") + photo(302, "https://cdn/b2.jpg")
      + text("Текст обрезанного альбома")) + "".join(wrap(i, text(f"пост {i}")) for i in range(303, 332)) + "</section>"
_fetch = bot.web_fetch
bot.web_fetch = lambda url: BIG if "before" not in url else "<section class='tgme_channel_history'></section>"
bot.QUEUE.clear(); bot.LAST_IDS["big"] = 300; bot.SEEN.clear(); bot.BACKLOG_MAX = 100
asyncio.run(bot.process_channel(cl, "big", {})); MONO[0] += 10
asyncio.run(bot.process_channel(cl, "big", {}))
got = [x for x in bot.QUEUE if x["ch"] == "big"]
assert not any(x["photos"] for x in got) and len(got) == 29, [(x["id"], len(x["photos"])) for x in got]
bot.web_fetch = _fetch
print("w4б ok: альбом, обрезанный лимитом 30 сообщений, не выходит «последним фото без текста»")

# видео со ссылкой качается, большое — не уходит калекой, а ждёт решения
async def pub(it):
    return await bot.publish_item(cl, 5, it)
bot.VIDEO_RETRY_PAUSES[:] = [0, 0]
r1 = asyncio.run(pub(q["chan:106"])); assert r1 == "ok", r1
r2 = asyncio.run(pub(q["chan:107"])); assert r2 == "dropped", r2
assert not any("слишком большое" in (t or "") for t, _ in API), API     # ни текста без видео, ни карточки
print("w5 ok: видео с ссылкой уходит; большое видео — пост пропущен целиком, без текста и карточки")

# тест-репост, когда последний пост — альбом: берётся весь альбом с текстом, а не одно последнее фото
ALB = "<section class='tgme_channel_history'>" + wrap(200, text("пост 200")) + wrap(203, photo(201, "https://cdn/a1.jpg")
      + photo(202, "https://cdn/a2.jpg") + photo(203, "https://cdn/a3.jpg") + text("Текст альбома")) + "</section>"
bot.web_fetch = lambda url: ALB
bot.LAST_IDS.clear(); bot.LAST_IDS["alb"] = 203
asyncio.run(bot.run_test(cl))
assert bot.LAST_IDS["alb"] == 200, bot.LAST_IDS
got = sorted(asyncio.run(cl.get_messages("alb", min_id=bot.LAST_IDS["alb"])), key=lambda m: m.id)
assert [m.id for m in got] == [201, 202, 203] and got[0].text == "Текст альбома"
print("w6 ok: 🧪 Тест на альбоме берёт все фото и текст, а не только последнее фото")

# реклама: маркировка erid, чужие приглашения/боты + призывы, кнопка «Перейти» — не публикуем; мемы и новости — да
cases = [("Скины дёшево!\n\nРеклама. ООО «Ромашка» ИНН 7700000000 erid: 2SDnjc1x", 0, True),
         ("Подпишись на халяву [тут](https://t.me/+AbCdEf123) и забирай бонус в нашем боте @free_robux_bot", 0, True),
         ("Успей забрать промокод на скидку по ссылке https://shop.example.com/sale", 1, True),
         ("Наши друзья делают видео — заходи t.me/coolvideos и t.me/+XyZ987", 0, True),
         ("Когда мама сказала убрать комнату, а ты строишь базу в Roblox 😂", 0, False),
         ("Новый мем [Роблокс мемы](https://t.me/chan)", 0, False),
         ("Вышло обновление Roblox: новый режим. Подробнее https://devforum.roblox.com/t/123", 0, False),
         ("РОЗЫГРЫШ 1000 робуксов! Подпишись на наш канал и поставь реакцию", 0, False),
         ("🎁 Разработчики раздают бесплатные предметы! Успей забрать, активируй промокод SPOOKY по ссылке "
          "https://www.roblox.com/promocodes и https://create.roblox.com/x и https://www.youtube.com/watch?v=a", 0, False),
         ("Standoff 2: новый промокод на бесплатный кейс! Бонус для всех игроков", 0, False),
         ("Supercell дарит скин! Заходи и забирай: https://supercell.com/en/games/brawlstars/blog/", 0, False)]
cases += [("Халява в PUBG! Промокод по ссылке https://www.pubgmobile.com/ru/event и https://krafton.com/news", 0, False),
          ("Genshin: успей забрать промокод на примогемы https://genshin.hoyoverse.com/ru/gift и hoyolab.com/x", 0, False),
          ("Майн: бесплатный скин, успей по ссылке https://www.minecraft.net/ru-ru/article/x https://minecraft.wiki/w", 0, False),
          ("Успей забрать промокод по ссылке https://mygame.example/gift и https://mygame.example/b", 0, True)]
for txt, btn, is_ad in cases:
    assert (bot.ad_score(txt, "chan", btn)[0] >= 10) == is_ad, (txt, bot.ad_score(txt, "chan", btn))
ADS = "<section class='tgme_channel_history'>" + "".join([
    wrap(401, text("Мем дня про Roblox")),
    wrap(402, text("Лучший VPN для игр. Реклама. ООО «Ромашка» erid: 2SDnjc1x")),
    wrap(403, text("Забирай промокод на скидку по ссылке") +
         '<div class="tgme_widget_message_inline_keyboard"><a class="tgme_widget_message_inline_button url_button" '
         'href="https://shop.example.com/x">Перейти</a></div>' + '<a href="https://shop.example.com/x">shop</a>'),
]) + "</section>"
assert bot.parse_web_page("chan", ADS)[2].url_buttons == 1
bot.web_fetch = lambda url: ADS
bot.QUEUE.clear(); bot.LAST_IDS["chan"] = 400; bot.SEEN.clear(); MONO[0] += 100
ads0 = bot.STATS.get("ads", 0)
asyncio.run(bot.process_channel(cl, "chan", {})); MONO[0] += 10
asyncio.run(bot.process_channel(cl, "chan", {}))
assert [x["id"] for x in bot.QUEUE] == ["chan:401"], [x["id"] for x in bot.QUEUE]
assert bot.STATS["ads"] - ads0 == 2 and any("похоже на рекламу" in j and "erid" in j for j in bot.JOURNAL)
bot.CFG["ads_skip"] = False                              # выключили — публикуется всё
bot.QUEUE.clear(); bot.LAST_IDS["chan"] = 400; bot.SEEN.clear(); MONO[0] += 100
asyncio.run(bot.process_channel(cl, "chan", {})); MONO[0] += 10
asyncio.run(bot.process_channel(cl, "chan", {}))
assert len(bot.QUEUE) == 3, [x["id"] for x in bot.QUEUE]
bot.CFG["ads_skip"] = True
assert bot.apply_text("addts", "https://www.mygame.example/news, другое", 1)[0].startswith("Добавил")
assert bot.ad_score("Успей забрать промокод по ссылке https://mygame.example/gift и https://mygame.example/b", "chan")[0] < 10
bot.apply_text("delts", "mygame.example", 1); assert not bot.CFG["trusted_sites"]
print("w7 ok: рекламу (erid, чужие приглашения и боты, промокоды с кнопкой) бот не публикует, мемы и новости — да")

# 🧠 обучаемый фильтр: отсеянное можно вернуть («не реклама»), вышедшее — убрать («реклама»); бот учится
DELS = []
bot.delete_msg = lambda mid: DELS.append(mid) or True
held = [x["id"] for x in bot.ADHELD]
assert "chan:402" in held and "chan:403" in held, held
text, rows = bot.screen("adl", 1)
assert "Обучение фильтра" in text and "a:adok:chan:402" in str(rows), text
bot.QUEUE.clear()
bot.do_action("adok:chan:402", 1)
assert [x["id"] for x in bot.QUEUE] == ["chan:402"] and "chan:402" not in [x["id"] for x in bot.ADHELD]
assert bot.ad_model()["n_ok"] == 1
bot.record_pub("mid-77", "chan", "Курс", raw="Подпишись на курс по крипте t.me/+abc")
bot.do_action("adbad:mid-77", 1)
assert DELS == ["mid-77"] and bot.ad_model()["n_ad"] >= 1
# учим: «инфоцыгане» без ссылок правила не ловят, а после отметок — ловит; мемы не трогает
ads = ["Хочешь зарабатывать от 100 тысяч в месяц на крипте? Мой курс трейдинга научит. Пиши в личку",
       "Курс по трейдингу криптой: доход с первой недели, места ограничены, пиши в личку",
       "Пассивный заработок на крипте без вложений, обучение трейдингу, пиши мне в личку",
       "Заработок в интернете от 5000 в день, трейдинг и крипта, научу — пиши в личку"]
oks = ["Когда мама зовёт ужинать, а ты в катке Roblox 😂", "Новый ивент в Brawl Stars начнётся в пятницу",
       "Мем дня: когда купил скин и проиграл", "Обновление Standoff 2: новая карта и оружие"]
probe_ad = "Научу трейдингу крипты, доход от 50 тысяч в месяц, пиши в личку"
assert not bot.is_ad_post(probe_ad, "chan")[0]                  # до обучения — пропускает
for x in ads:
    bot.ad_learn(x, 0, True)
for x in oks:
    bot.ad_learn(x, 0, False)
yes, why = bot.is_ad_post(probe_ad, "chan")
assert yes and "🧠" in why[0], why
assert not bot.is_ad_post("Мем дня: когда проиграл в Roblox катку", "chan")[0]
assert not bot.is_ad_post("Новая карта в Standoff 2 уже в игре", "chan")[0]
print("w8 ok: 🧠 «не реклама» выкладывает и учит, «реклама» удаляет из канала и учит; после отметок бот сам ловит похожую рекламу")

# Telegram отдаёт видео только через CDN (ключи Desktop) — бот качает его через t.me
class CdnErr(Exception): pass
class CdnMsg:
    id = 106
    video = gif = video_note = sticker = None
    document = types.SimpleNamespace(mime_type="video/mp4", size=10, attributes=[])
    photo = None
    async def download_media(self, file=None):
        raise CdnErr("MethodInvalidError: The API method is invalid and cannot be used (caused by GetCdnFileRequest)")
CdnMsg.video = CdnMsg.document
class CdnClient:
    async def get_messages(self, ch, ids=None, **k): return CdnMsg()
bot.web_fetch = lambda url: PAGES[url]
path, err = asyncio.run(bot.download_video(CdnClient(), "chan", 106))
assert path and os.path.exists(path) and err is None, err
p2 = asyncio.run(bot.download_photo(CdnMsg(), os.path.abspath("cdn_photo.jpg"), "chan"))
assert p2 is None or os.path.exists(p2)
print("w9 ok: видео, которое Telegram отдаёт только через CDN, бот скачивает через t.me")

# 🧠 умные функции (все по умолчанию выкл — поведение не меняется, пока не включишь)
assert not bot.CFG["smart_tags"] and not bot.CFG["smart_time"] and not bot.CFG["ads_strict"]
# хэштеги
bot.CFG["smart_tags"] = True
t = bot.compose_text("Новый ивент в Роблокс, забирай промокод", "robloxers")
assert "#roblox" in t and "#ивент" in t.lower() or "#промокод" in t, t
assert bot.compose_text("Пост с #свой уже хэштег", "c") .count("#") == 1   # уже есть # — не добавляем
bot.CFG["smart_tags"] = False
assert "#" not in bot.compose_text("Новый ивент в Роблокс", "robloxers")
# строго к рекламе: пост на 7-9 баллов отсеивается только при ads_strict
mid_ad = "Залетай и подпишись на t.me/coolgames и t.me/moregames — там топ контент"
sc = bot.ad_score(mid_ad, "chan")[0]
assert 7 <= sc < 10, sc
assert not bot.is_ad_post(mid_ad, "chan")[0]
bot.CFG["ads_strict"] = True
assert bot.is_ad_post(mid_ad, "chan")[0]
bot.CFG["ads_strict"] = False
# авто-оценка: слабый пост — низкий балл
assert bot.post_quality("", False) < 4 and bot.post_quality("Большой пост с текстом про игру", True) >= 4
# умное время: мало данных — не придерживаем
bot.PUB["day_posts"] = []
assert bot.hot_hours() is None
import time as _t
now = _t.time()
for h in range(0, 24):                                  # 18:00 — просмотров много, остальное мало
    for _ in range(2):
        bot.PUB["day_posts"].append({"t": now - 3600 * ((now//3600 % 24 - h) % 24) , "views": 500 if h == 18 else 10, "ch": "c"})
hh = bot.hot_hours()
assert hh is not None and 18 in hh
# строгие дубли: та же картинка с разной подписью
bot.CFG["dedupe_strict"] = True
h = [{"norm": bot.norm("старая длинная подпись про мем номер один два три"), "img": ["IMGHASH"], "fwd": [], "alts": []}]
e = {"norm": bot.norm("совсем другая длинная подпись другими словами про кота пса"), "img": ["IMGHASH"], "fwd": []}
assert bot.find_duplicate(e, h) is not None              # одна картинка -> дубль
bot.CFG["dedupe_strict"] = False
assert bot.find_duplicate(e, h) is None                  # без строгого режима — разные подписи, не дубль
# экран и тумблеры
txt, rows = bot.screen("ai", 1)
assert "Умные функции" in txt and "a:aismart" in str(rows)
bot.do_action("aismart", 1); assert bot.CFG["smart_time"]
bot.do_action("aismart", 1); assert not bot.CFG["smart_time"]
print("w10 ok: умные функции — хэштеги, строгая реклама, авто-оценка, умное время, строгие дубли; тумблеры работают")

print("ВСЕ ВЕБ-ТЕСТЫ ПРОШЛИ")
