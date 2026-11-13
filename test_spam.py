"""🗑 Спам-фильтр и 🛡 перехватчик РСЯ.

1) Жёсткий локальный спам-фильтр (крипта/казино/«заработок», дропы, КАПС+эмодзи с «СРОЧНО/ХАЛЯВА/ЗАБЕРИ»,
   сокращалки и приглашения со спам-контекстом, приглашения в ботов, кросс-промо) — до скачивания фото и до ИИ.
2) «Пожиратель»: в журнале «🗑 Съеден спам: причина (ссылка)», сводка за день/неделю — экран «🗑 Спам-фильтр».
3) Обучение: админ удалил опубликованный пост или нажал «🗑 Спам» — ссылки/@ники/редкие слова поста идут в
   spam_blocklist.json (на все каналы, переживает перезапуск); удаления самого бота не учат.
4) Анти-флуд: 10 спам-постов подряд из канала — канал на час не читаем, админу «⚠️ Канал … заблокирован …».
5) Каналы без дублей; бота убрали или канал удалён — 7 дней в списке с пометкой «⚠️ удалён», потом исчезает.
6) Реклама РСЯ в канале темы (#реклама, «О рекламодателе», строка max.ru) — сразу выкладываем следующий пост."""
import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import asyncio
import datetime
import importlib.util
import os
import sys
import tempfile
import time
import traceback
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from fake_max import FakeMax  # noqa: E402

tele = types.ModuleType("telethon")
tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
tele.events = types.SimpleNamespace(NewMessage=lambda *a, **k: None)
sys.modules["telethon"] = tele
sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, BOT_TOKEN="TESTTOKEN")
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "TGMAX_HOST", "GEMINI_API_KEYS",
          "OPENROUTER_API_KEYS", "DEEPSEEK_API_KEYS"):
    os.environ.pop(k, None)
DATA = tempfile.mkdtemp()
os.chdir(DATA)


def load_bot(name):
    spec = importlib.util.spec_from_file_location(name, ROOT + "/tg_to_max.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.S.trust_env = False
    return b


bot = load_bot("bot")
SENT, NOTES, ALERTS, DOWNLOADS, AI = [], [], [], [], []
bot.api_send = lambda chat, text, rows=None, token=None, **kw: (SENT.append((chat, text or "", rows or [])),
                                                                 f"m{len(SENT)}")[1]
bot.notify = lambda t, tag=True, rows=None: NOTES.append(t)
bot.owner_alert = lambda t: ALERTS.append(t)
bot.CFG["admins"] = [1]
bot.CFG["admin_chats"] = {"1": 111}
bot.CFG["approval_on"] = False
bot.CFG["dedupe_on"] = False
bot.VIDEO_RETRY_PAUSES[:] = [0, 0]
bot.FRESH_SEC = 0                       # пост берём сразу, без ожидания «альбом долетел»
CH = 7000                               # канал MAX темы
FAILS = []


def check(fn):
    try:
        fn()
        print(f"{fn.__name__} ok: {fn.__doc__}")
    except Exception as e:
        FAILS.append(fn.__name__)
        line = traceback.extract_tb(e.__traceback__)[-1]
        print(f"{fn.__name__} FAIL (строка {line.lineno}: {line.line}) — {type(e).__name__}: {str(e)[:600]}")
    return fn


def payloads(rows):
    return [b.get("payload") or "" for r in (rows or []) for b in r]


class Msg:
    def __init__(self, mid, text="", photo=False, buttons=0):
        self.id, self.text, self.grouped_id = mid, text, None
        self.date = datetime.datetime.now(datetime.timezone.utc)
        self.photo = types.SimpleNamespace() if photo else None
        self.video = self.gif = self.video_note = self.sticker = self.fwd_from = self.document = None
        self.url_buttons = buttons
        self.file = None

    async def download_media(self, file=None):
        DOWNLOADS.append(self.id)
        with open(file, "wb") as f:
            f.write(b"\xff\xd8\xff IMG")
        return file


class TG:
    def __init__(self, msgs):
        self.msgs, self.reads = list(msgs), 0

    async def get_messages(self, ch, min_id=0, limit=30, ids=None):
        if ids is not None:
            return next((m for m in self.msgs if m.id == ids), None)
        self.reads += 1
        return sorted([m for m in self.msgs if m.id > min_id], key=lambda m: -m.id)[:limit]


def read(tg, ch, last=None):
    if last is not None:
        bot.LAST_IDS[ch] = last
    asyncio.run(bot.process_channel(tg, ch, {}))


def queued(ch):
    return {x["id"] for x in bot.QUEUE if x["ch"] == ch}


def item(iid, text, ago):
    now = time.time()
    return {"id": iid, "ch": "news", "date": now - ago, "enq": now - ago, "text": text, "photos": [], "vids": [],
            "docs": [], "sent": 0, "fails": 0, "status": "ok"}


# ---------- 1) жёсткий локальный спам-фильтр ----------
SPAM = {
    "Заработок в интернете от 5000₽ в день! Пассивный доход без вложений, пиши в лс": "scam",
    "Обменяю USDT TRC20 по лучшему курсу, пишите": "scam",
    "Лучшее казино 🎰 бонус 500% на первый депозит": "scam",
    "Ставки на спорт с гарантией, коэффициенты от 2.0": "scam",
    "Ищем дропов под карты, оплата сразу": "drop",
    "🔥🔥🔥 срочно забери халяву 🔥🔥🔥 🎁🎁🎁": "shout",
    "ХАЛЯВА!!! БЕСПЛАТНЫЕ РОБУКСЫ ДЛЯ ВСЕХ, ЗАБИРАЙ ПОКА НЕ ПОЗДНО": "shout",
    "Срочно переходи: bit.ly/3xYzAb и забирай подарок": "link",
    "Халява тут → t.me/joinchat/AAAAB_xyz": "link",
    "Забирай бонус в боте: t.me/freegift_bot?start=ref777": "bot",
    "Бесплатные робуксы в нашем боте @RobuxFreeBot — жми!": "bot",
    "Взаимопиар! Подпишись на @chan_aaa и @chan_bbb": "xpromo",
}
CLEAN = [
    "Вышло обновление Roblox 1.2: новые скины и карта!",
    "🔥🔥 Новый дроп скинов в Standoff 2 уже в игре 🎮🎮",
    "СРОЧНО: сервера Minecraft упали, ждём фикс",
    "Ключевая ставка ЦБ осталась прежней",
    "Наш сайт: https://roblox.com/games/123",
    "Подробнее в видео: https://youtu.be/abc",
    "**Срочно!** Роблокс раздаёт подарки\n\nПереходи по ссылке https://site.ru/promo?ref=1",
]


@check
def s1():
    """спам-фильтр ловит крипту/казино/«заработок», дропов, КАПС+эмодзи с «СРОЧНО/ХАЛЯВА/ЗАБЕРИ», сокращалки и
    приглашения со спам-контекстом, приглашения в ботов, кросс-промо; обычные посты (и «дроп скинов», «ставка ЦБ»,
    одно «СРОЧНО» в новости) — не спам"""
    for text, kind in SPAM.items():
        v = bot.spam_verdict(text, "src")
        assert v[0] and v[2] == kind and v[1], (text, v)
    for text in CLEAN:
        v = bot.spam_verdict(text, "src")
        assert not v[0], (text, v)


@check
def s2():
    """спам съедается сразу при чтении канала — фото спама даже не качаем, в очередь и к ИИ он не попадает;
    обычный пост — в очередь, и ИИ видит только его"""
    DOWNLOADS.clear()
    tg = TG([Msg(11, "Заработок в интернете от 5000₽ в день! Пассивный доход без вложений", photo=True),
             Msg(12, "Вышло обновление Roblox 1.2: новые скины и карта!", photo=True)])
    read(tg, "news", 10)
    assert queued("news") == {"news:12"}, queued("news")
    assert DOWNLOADS == [12], DOWNLOADS
    assert bot.LAST_IDS["news"] == 12
    line = [j for j in bot.JOURNAL if "Съеден спам" in j][-1]
    assert "🗑 Съеден спам: " in line and line.endswith("(https://t.me/news/11)"), line
    real = (bot.ai_on, bot.ai_llm_ready, bot.ai_rewrite, bot.send_max)
    bot.ai_on, bot.ai_llm_ready = (lambda: True), (lambda: True)
    bot.ai_rewrite = lambda text: (AI.append(text), text)[1]
    bot.send_max = lambda chat, text, imgs, video_path=None, file_path=None: "mid-news-12"
    try:
        it = next(x for x in bot.QUEUE if x["id"] == "news:12")
        assert asyncio.run(bot.publish_item(None, CH, it)) == "ok"
    finally:
        bot.ai_on, bot.ai_llm_ready, bot.ai_rewrite, bot.send_max = real
    assert AI == ["Вышло обновление Roblox 1.2: новые скины и карта!"], AI


@check
def s3():
    """«пожиратель»: каждый съеденный пост — в журнале и в spam_eaten.json; сводка за сегодня и за 7 дней по
    причинам и каналам — экран «🗑 Спам-фильтр» (кнопка в «🧹 Фильтрах»)"""
    read(TG([Msg(21, "🔥🔥🔥 СРОЧНО ЗАБЕРИ ХАЛЯВУ 🔥🔥🔥 🎁🎁🎁"), Msg(22, "Обменяю USDT TRC20, пишите в лс")]), "deals", 20)
    eaten = [e for e in bot.SPAM_EATEN if e["ch"] == "deals"]
    assert sorted(e["kind"] for e in eaten) == ["scam", "shout"], eaten
    assert all(e["link"].startswith("https://t.me/deals/") and e["why"] for e in eaten), eaten
    assert [e["mid"] for e in tio.rjson("spam_eaten.json") if e["ch"] == "deals"] == [21, 22]
    d1 = bot.spam_summary(1)
    assert d1["n"] >= 3 and d1["by_kind"]["scam"] >= 2 and d1["by_ch"]["deals"] == 2, d1
    bot.SPAM_EATEN.append(dict(eaten[0], t=time.time() - 3 * 86400, mid=999))     # три дня назад: неделя, не сегодня
    assert bot.spam_summary(7)["n"] == bot.spam_summary(1)["n"] + 1
    text, rows = bot.screen("spam", 1)
    for part in ("Сегодня:", "за 7 дней:", "@deals", "Последние"):
        assert part in text, (part, text)
    assert "крипто" in text.lower() and "кричащий" in text.lower(), text
    _, fl_rows = bot.screen("fl", 1)
    assert "m:spam" in payloads(fl_rows), payloads(fl_rows)


@check
def s4():
    """админ удалил опубликованный пост в канале — его ссылки, @ники и редкие слова попадают в spam_blocklist.json
    (действует на все каналы, переживает перезапуск); удаление самим ботом (автоудаление) ничему не учит"""
    bot.THEMES[0].chat_id = CH
    bot.record_pub("mid-spam-1", "news", "Купи курс https://scamcourse.ru/x у @ScamSeller — гарантированный заработок")
    bot.handle_update({"update_type": "message_removed", "message_id": "mid-spam-1", "chat_id": CH, "user_id": 1})
    bl = tio.rjson("spam_blocklist.json")
    assert "scamcourse.ru" in bl["links"] and "@scamseller" in bl["links"], bl
    assert "заработок" in bl["words"] and "гарантированный" in bl["words"], bl
    assert not any(w in bl["words"] for w in ("обновление", "roblox", "скины")), "слова обычных постов не учим"
    assert any("удалён вручную" in j for j in bot.JOURNAL)
    v = bot.spam_verdict("Новые курсы тут: scamcourse.ru/promo", "other")
    assert v[0] and v[2] == "block", v
    assert bot.spam_verdict("Гарантированный заработок для каждого", "other")[0]     # два слова из списка
    assert not bot.spam_verdict("Заработок блогеров вырос", "other")[0]               # одно слово — мало
    bot.record_pub("mid-own-2", "news", "Пиши @OwnDeletedChan про новости")
    bot.delete_msg("mid-own-2")                                                      # бот удалил сам (автоудаление)
    bot.handle_update({"update_type": "message_removed", "message_id": "mid-own-2", "chat_id": CH, "user_id": 0})
    assert "@owndeletedchan" not in tio.rjson("spam_blocklist.json")["links"]
    bot.handle_update({"update_type": "message_removed", "message_id": "nope", "chat_id": CH})   # не наш пост — тихо
    b2 = load_bot("bot_restart")
    assert b2.spam_verdict("Курсы на scamcourse.ru", "x")[0], "чёрный список пережил перезапуск"


@check
def s5():
    """кнопка «🗑 Спам» на карточке одобрения: пост не выходит, в журнале «съеден», его @ник — в чёрном списке"""
    bot.CFG["approval_on"] = True
    try:
        read(TG([Msg(31, "Лучшие цены у @DealsHunterX — пишите")]), "shop", 30)
    finally:
        bot.CFG["approval_on"] = False
    it = next(x for x in bot.QUEUE if x["id"] == "shop:31")
    assert it["status"] == "pending"
    assert any("ap:spam:shop:31" in payloads(rows) for _, _, rows in SENT), "на карточке есть «🗑 Спам»"
    text, _ = bot._approve("spam:shop:31", 1)
    assert "спам" in text.lower() and it not in bot.QUEUE, text
    assert "@dealshunterx" in tio.rjson("spam_blocklist.json")["links"]
    assert any(e["mid"] == 31 and e["kind"] == "manual" for e in bot.SPAM_EATEN)
    assert bot._approve("spam:shop:31", 1)[0] == "Этот пост уже обработан."


@check
def s6():
    """анти-флуд: 10 спам-постов подряд из канала (и через несколько чтений) — канал на час не читаем, админу
    «⚠️ Канал @flood заблокирован на 1 час из-за флуда спамом.»; через час — снова читаем; обычный пост между
    спамом сбрасывает счёт"""
    ALERTS.clear()
    tg = TG([Msg(100 + i, f"Казино бонус {i} 🎰 забирай") for i in range(5)])
    read(tg, "flood", 99)
    assert not ALERTS and not bot.spam_muted("flood")
    tg.msgs += [Msg(105 + i, f"Казино бонус {i} 🎰 забирай") for i in range(5)]
    read(tg, "flood")
    assert ALERTS == ["⚠️ Канал @flood заблокирован на 1 час из-за флуда спамом."], ALERTS
    assert bot.spam_muted("flood") and any("заблокирован на 1 час" in j for j in bot.JOURNAL)
    tg.msgs.append(Msg(110, "Обычная новость про игру"))
    reads = tg.reads
    read(tg, "flood")
    assert tg.reads == reads and "flood:110" not in queued("flood"), "в муте канал не читаем"
    for k in list(bot.SPAM_MUTED):
        bot.SPAM_MUTED[k] = time.time() - 1                                          # час прошёл
    read(tg, "flood")
    assert "flood:110" in queued("flood") and not bot.spam_muted("flood")
    ALERTS.clear()
    tg2 = TG([Msg(200 + i, f"Казино бонус {i} 🎰") for i in range(9)] + [Msg(209, "Нормальная новость про игру")])
    read(tg2, "mix", 199)
    tg2.msgs += [Msg(210 + i, f"Казино бонус {i} 🎰") for i in range(9)]
    read(tg2, "mix")
    assert not ALERTS and not bot.spam_muted("mix"), ALERTS


@check
def s7():
    """каналы без дублей: один канал с id строкой и числом, два id с одной ссылкой — одна строка в списке;
    в known_chats.json — один ключ"""
    bot.max_chats = lambda: [{"chat_id": "-100500", "type": "channel", "status": "active", "title": "Мой канал",
                              "link": "https://max.ru/mychan"}]
    srv.private[-100500] = ("Мой канал", "https://max.ru/mychan")
    srv.private[-100501] = ("Мой канал (копия)", "https://max.ru/mychan")
    bot.remember_chat(-100500, 1)
    bot.remember_chat("-100500", 1)
    bot.remember_chat(-100501, 1)
    assert [k for k in tio.rjson("known_chats.json") if k.strip() == "-100500"] == ["-100500"]
    chans = bot.pick_channels(1)
    ids = [c["chat_id"] for c in chans]
    assert len(ids) == len(set(ids)) and all(isinstance(i, int) for i in ids), ids
    assert len([c for c in chans if "mychan" in (c.get("link") or "")]) == 1, chans
    _, rows = bot.screen("chpick", 1)
    assert sum(p in ("a:chbind:-100500", "a:chbind:-100501") for p in payloads(rows)) == 1, payloads(rows)


def chpick_text(cid):
    for page in range(6):
        text, rows = bot.screen(f"chpick:{page}", 1)
        if f"a:chbind:{cid}" in payloads(rows):
            return text, rows
    return "", []


@check
def s8():
    """бота убрали из канала или канал удалили — 7 дней канал в списке с пометкой «⚠️ … удалён (будет скрыт
    через N дн.)», потом исчезает сам (и из known_chats.json); бота вернули — пометки нет"""
    bot.max_chats = lambda: []
    srv.private[-100700] = ("Старый канал", "https://max.ru/join/old700")
    bot.remember_chat(-100700, 1)
    bot.CHAT_INFO_CACHE.clear()
    assert not next(c for c in bot.pick_channels(1) if c["chat_id"] == -100700).get("removed")
    bot.handle_update({"update_type": "bot_removed", "chat_id": -100700, "is_channel": True, "user": {"user_id": 1}})
    srv.private.pop(-100700)
    bot.CHAT_INFO_CACHE.clear()
    c = next((c for c in bot.pick_channels(1) if c["chat_id"] == -100700), None)
    assert c and c.get("removed") and c["title"] == "Старый канал", c
    text, rows = chpick_text(-100700)
    assert "«Старый канал» — удалён (будет скрыт через 7 дней)" in text, text
    assert any(b["text"] == "⚠️ Старый канал" for r in rows for b in r), rows
    bot.KNOWN_CHATS["-100700"]["removed"] = time.time() - 3 * 86400 - 60
    assert "будет скрыт через 4 дня" in chpick_text(-100700)[0]
    bot.KNOWN_CHATS["-100700"]["removed"] = time.time() - 6 * 86400 - 60
    assert "будет скрыт через 1 день" in chpick_text(-100700)[0]
    bot.KNOWN_CHATS["-100700"]["removed"] = time.time() - 7 * 86400 - 60
    assert all(c["chat_id"] != -100700 for c in bot.pick_channels(1))
    assert "-100700" not in tio.rjson("known_chats.json"), "через 7 дней — и из базы"
    srv.private[-100800] = ("Удалю сам", "https://max.ru/join/del800")              # канал удалили в MAX (404)
    bot.remember_chat(-100800, 1)
    bot.CHAT_INFO_CACHE.clear()
    bot.pick_channels(1)
    srv.private.pop(-100800)
    bot.CHAT_INFO_CACHE.clear()
    c = next((c for c in bot.pick_channels(1) if c["chat_id"] == -100800), None)
    assert c and c.get("removed") and c["title"] == "Удалю сам", c
    srv.private[-100800] = ("Удалю сам", "https://max.ru/join/del800")
    bot.handle_update({"update_type": "bot_added", "chat_id": -100800, "is_channel": True, "user": {"user_id": 1}})
    bot.CHAT_INFO_CACHE.clear()
    assert not next(c for c in bot.pick_channels(1) if c["chat_id"] == -100800).get("removed")
    bot.purge_removed_chats(time.time() + 30 * 86400)                                # активный канал не трогаем
    assert "-100800" in bot.KNOWN_CHATS


AD = ("2 x iPhone 18 Pro 256 ГБ - 19900₽...\nApple Max - проверенный магазин в MAX...\nКупить\n#реклама\nmax.ru\n"
      "О рекламодателе")


@check
def s9():
    """реклама РСЯ узнаётся по #реклама, «О рекламодателе» и отдельной строке max.ru — и в самом низу длинного
    текста, и в кнопках; обычные посты, своя маркированная реклама и max.ru внутри фразы — не РСЯ"""
    assert bot.is_rsya_ad(AD)
    assert bot.is_rsya_ad("Новость дня. " * 300 + "\n\n#реклама\nmax.ru")
    assert bot.is_rsya_ad("Скидки до 50% на всё\n\nО рекламодателе")
    assert not bot.is_rsya_ad("Вышло обновление Roblox 1.2")
    assert not bot.is_rsya_ad("Реклама. ООО «Ромашка», ИНН 7700000000, erid: 2VtzqX\n#реклама")
    assert not bot.is_rsya_ad("Наш канал max.ru/mychan — подписывайтесь")
    body = {"text": "2 x iPhone 18 Pro 256 ГБ - 19900₽\n#реклама", "attachments": [{"type": "inline_keyboard",
            "payload": {"buttons": [[{"type": "link", "text": "Купить", "url": "https://max.ru/applemax"}],
                                    [{"type": "link", "text": "О рекламодателе", "url": "https://max.ru/x"}]]}}]}
    assert bot.is_rsya_ad(bot.rsya_text(body))


def ad_update(mid, chat=CH, text=AD):
    return {"update_type": "message_created", "message": {"recipient": {"chat_id": chat, "chat_type": "channel"},
                                                          "sender": {"user_id": 0}, "body": {"text": text, "mid": mid}}}


@check
def s10():
    """перехватчик: реклама РСЯ в канале темы — сразу выкладываем следующий пост из очереди (без паузы), чтобы
    опустить рекламу; не чаще раза в 2 минуты; свои посты и чужие каналы не трогаем; очередь пуста — в журнал"""
    bot.THEMES[0].chat_id = CH
    q1, q2 = item("int:1", "Первый пост в очереди", 600), item("int:2", "Второй пост в очереди", 300)
    bot.QUEUE[:] = [q2, q1]
    bot.PUB["last_pub"] = time.time()                       # пауза не прошла: по расписанию ничего бы не вышло
    pub = []
    real_send = bot.send_max
    bot.send_max = lambda chat, text, imgs, video_path=None, file_path=None: (pub.append(text), f"mid-r{len(pub)}")[1]
    try:
        bot.RSYA_LAST.clear()
        bot.handle_update(ad_update("ad-1"))
        assert q1.get("manual") and q1.get("at", 0) <= time.time() and not q2.get("manual"), (q1, q2)
        assert any("Перехватчик РСЯ" in j for j in bot.JOURNAL)
        asyncio.run(bot.publish_due(None, CH))
        assert len(pub) == 1 and "Первый пост в очереди" in pub[0] and q2 in bot.QUEUE, pub
        bot.handle_update(ad_update("ad-2"))                 # 2 минуты не прошло — второй пост не трогаем
        assert not q2.get("manual")
        bot.RSYA_LAST.clear()
        bot.record_pub("mid-own-ad", "news", AD)             # это наш пост с такими словами — не РСЯ
        bot.handle_update(ad_update("mid-own-ad"))
        bot.handle_update(ad_update("ad-3", chat=99999))     # чужой канал
        bot.handle_update(ad_update("ad-4", text="Вышло обновление Roblox 1.2"))
        assert not q2.get("manual")
        bot.QUEUE[:] = []
        bot.handle_update(ad_update("ad-5"))
        assert any("очередь пуста" in j for j in bot.JOURNAL)
    finally:
        bot.send_max = real_send


@check
def s11():
    """настройки спам-фильтра: выключатель (тогда спам не съедается) и «♻️ Очистить чёрный список» с
    подтверждением"""
    _, rows = bot.screen("spam", 1)
    assert "a:spamsk" in payloads(rows) and "m:spamclr" in payloads(rows), payloads(rows)
    bot.do_action("spamsk", 1)
    try:
        read(TG([Msg(301, "Казино бонус 🎰 забирай")]), "off", 300)
        assert "off:301" in queued("off"), "фильтр выключен — пост не съеден"
    finally:
        bot.do_action("spamsk", 1)
    assert bot.CFG.get("spam_skip", True)
    text, rows = bot.screen("spamclr", 1)
    assert "a:spamclrok" in payloads(rows), rows
    bot.do_action("spamclrok", 1)
    bl = tio.rjson("spam_blocklist.json")
    assert bl["links"] == [] and bl["words"] == [] and not bl.get("seen"), bl


if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    sys.exit(1)
print("ВСЕ ТЕСТЫ СПАМ-ФИЛЬТРА И ПЕРЕХВАТЧИКА ПРОШЛИ")
os._exit(0)
