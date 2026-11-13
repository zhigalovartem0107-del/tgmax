"""🔬 Полный аудит новых механизмов: крайние случаи и гонки.

• Спам-фильтр: обычные посты со словами «код», «дроп скинов», «ставка/ставки», «казино» (GTA), «USDT» (новость),
  «бесплатно», много эмодзи — НЕ съедаются; настоящий спам — съедается; флуд из 50 постов и из 3 каналов разом.
• Перехватчик РСЯ: узнаёт рекламу во всех видах; берёт следующий пост; пустая очередь, только «ждут одобрения»,
  пауза, нет прав — не срабатывает; ровно 2 минуты «остывания» (119 с — нет, 121 с — да); гонка с планировщиком
  в одну и ту же секунду (живой цикл asyncio) — ни двойной публикации, ни «закапывания» уже уходящим постом.
• ИИ: все ключи отвечают 429 — 20 постов подряд выходят оригиналом, без зависаний и без долбёжки ИИ; цикл asyncio
  не стоит, пока ИИ медленно отвечает 429; длинный пост, разорванный сбоем MAX, не «склеивается» из оригинала и
  пересказа; аналитика и тренды при 429 — без падений.
• Удалённые каналы: ровно 7 дней (за 5 с до конца — «1 день», через 1 с — удалён), почасовая проверка сама замечает
  удалённый канал и сама чистит базу; переживает перезапуск; удалили-вернули-удалили — снова 7 дней.
• Чёрный список спама не растёт бесконечно: потолки, правила, которые давно не срабатывали, — удаляются (90 дней),
  срабатывающие — живут; старый формат файла и испорченный файл читаются.
• Потокобезопасность: «съеденное» из 8 потоков не теряется; список каналов из многих потоков — без падений;
  кнопка «❌» по посту, который прямо сейчас уходит в канал, не ломает публикацию."""
import asyncio
import datetime
import importlib.util
import json
import os
import random
import sys
import tempfile
import threading
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
os.environ.update(MAX_API_URL=srv.url, BOT_TOKEN="TESTTOKEN", OPENROUTER_API_URL=srv.url, DEEPSEEK_API_URL=srv.url)
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "TGMAX_HOST", "GEMINI_API_KEYS",
          "OPENROUTER_API_KEYS", "DEEPSEEK_API_KEYS", "GEMINI_API_URL"):
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
SENT, NOTES, ALERTS = [], [], []
bot.api_send = lambda chat, text, rows=None, token=None, **kw: (SENT.append((chat, text or "", rows or [])),
                                                                 f"m{len(SENT)}")[1]
bot.notify = lambda t, tag=True, rows=None: NOTES.append(t)
bot.owner_alert = lambda t: ALERTS.append(t)
bot.CFG["admins"] = [1]
bot.CFG["admin_chats"] = {"1": 111}
bot.CFG["approval_on"] = False
bot.CFG["dedupe_on"] = False
bot.CFG["night_on"] = False
bot.CFG["smart_time"] = False
bot.CFG["queue_on"] = True
bot.VIDEO_RETRY_PAUSES[:] = [0, 0]
bot.FRESH_SEC = 0
bot.RIGHTS_RECHECK_SEC = 0
CH = 7000
bot.THEMES[0].chat_id = CH
FAILS = []


def check(fn):
    try:
        fn()
        print(f"{fn.__name__} ok: {fn.__doc__}")
    except Exception as e:
        FAILS.append(fn.__name__)
        line = traceback.extract_tb(e.__traceback__)[-1]
        print(f"{fn.__name__} FAIL (строка {line.lineno}: {line.line}) — {type(e).__name__}: {str(e)[:700]}")
    return fn


def rjson(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def wtext(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def payloads(rows):
    return [b.get("payload") or "" for r in (rows or []) for b in r]


class Msg:
    def __init__(self, mid, text=""):
        self.id, self.text, self.grouped_id = mid, text, None
        self.date = datetime.datetime.now(datetime.timezone.utc)
        self.photo = self.video = self.gif = self.video_note = self.sticker = self.fwd_from = self.document = None
        self.url_buttons = 0
        self.file = None


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


def item(iid, text, ago=600, **kw):
    now = time.time()
    it = {"id": iid, "ch": "news", "date": now - ago, "enq": now - ago, "text": text, "photos": [], "vids": [],
          "docs": [], "sent": 0, "fails": 0, "status": "ok"}
    it.update(kw)
    return it


def journal_has(part):
    return any(part in j for j in bot.JOURNAL)


# ======================================================================================================================
# A) спам-фильтр: ложные срабатывания и настоящий спам
CLEAN = [
    "Новые коды Roblox 🎁: введи код SUMMER2026 и получи бесплатный скин!",
    "Промокод на 100 гемов в Brawl Stars: BRAWL100 (код действует до пятницы)",
    "Как получить код подтверждения в Telegram — инструкция",
    "Исходный код игры утёк в сеть: разработчики подтвердили",
    "🔥🔥 Новый дроп скинов в Standoff 2 уже в игре 🎮🎮",
    "Дроп-рейт легендарок повысили до 5%",
    "Ключевая ставка ЦБ осталась прежней",
    "Ставка на новый режим оправдалась: онлайн вырос вдвое",
    "Делайте ставки: кто победит в финале турнира? Пишите в комментариях",
    "Ставки высоки: финал чемпионата по Standoff 2 уже завтра",
    "Ограбление казино в GTA Online — полный гайд",
    "Курс USDT сегодня не изменился",
    "СРОЧНО: в Brawl Stars вышел новый боец! 🔥🔥🔥🔥🔥 Заходите и забирайте награду за вход",
    "🎉🎉🎉 Ура! Нам 100 000 подписчиков! Спасибо, что вы с нами 🥳🥳🥳 Впереди много нового — оставайтесь с нами!",
    "Разработчики раздали подарки всем игрокам 🎁🎁🎁🎁🎁🎁 Заходите в игру!",
    "🎁🎁🎁 Бесплатные скины для всех участников турнира 🏆🏆🏆",
    "Бесплатные выходные в Steam: Cyberpunk 2077 можно играть до понедельника",
    "Скачать мод: https://bit.ly/mods123 (Google Drive, 20 МБ)",
    "Наш бот @GameNewsBot присылает новости раньше всех",
    "Подпишись на наш второй канал @mygame_news и чат @mygame_chat",
    "Заработок блогеров на YouTube вырос на 20% — отчёт",
    "Вышло обновление 0.33: новая карта и баланс оружия.\n\nПереходи по ссылке https://site.ru/promo?ref=1",
    "**Срочно!** Роблокс раздаёт подарки\n\nПереходи по ссылке https://site.ru/promo?ref=1",
    "",
    "   ",
]
SPAM = [
    ("Заработок в интернете от 5000₽ в день! Пассивный доход без вложений", "scam"),
    ("Обменяю USDT TRC20 по лучшему курсу, пишите в лс", "scam"),
    ("Лучшее казино 🎰 бонус 500% на первый депозит", "scam"),
    ("Онлайн-казино: фриспины за регистрацию по ссылке", "scam"),
    ("Ставки на спорт с гарантией, коэффициенты от 2.0", "scam"),
    ("Прогнозы на матчи: ставки с гарантией 90%", "scam"),
    ("1xbet промокод на 10000 рублей", "scam"),
    ("Бесплатные робуксы тут: ссылка в профиле", "scam"),
    ("Ищем дропов под карты, оплата сразу", "drop"),
    ("🔥🔥🔥 СРОЧНО ЗАБЕРИ ХАЛЯВУ 🔥🔥🔥 🎁🎁🎁", "shout"),
    ("ХАЛЯВА!!! БЕСПЛАТНЫЕ РОБУКСЫ ДЛЯ ВСЕХ, ЗАБИРАЙ ПОКА НЕ ПОЗДНО", "shout"),
    ("Срочно переходи: bit.ly/3xYzAb и забирай подарок", "link"),
    ("Халява тут → t.me/joinchat/AAAAB_xyz", "link"),
    ("Забирай бонус в боте: t.me/freegift_bot?start=ref777", "bot"),
    ("Бесплатные робуксы в нашем боте @RobuxFreeBot — жми!", "bot"),
    ("Взаимопиар! Подпишись на @chan_aaa и @chan_bbb", "xpromo"),
    ("Подпишись на @aaa_chan, @bbb_chan и @ccc_chan — и получи приз", "xpromo"),
]


@check
def a1():
    """ложных срабатываний нет: «код», «дроп скинов», «ставка/ставки» в обычном смысле, казино в GTA, курс USDT,
    «бесплатно» и много эмодзи в обычных анонсах, ссылки и боты без спама — НЕ спам"""
    bad = [(t, bot.spam_verdict(t, "src")) for t in CLEAN if bot.spam_verdict(t, "src")[0]]
    assert not bad, bad


@check
def a2():
    """настоящий спам по-прежнему съедается (и с правильной причиной)"""
    bad = [(t, k, bot.spam_verdict(t, "src")) for t, k in SPAM if bot.spam_verdict(t, "src")[2] != k]
    assert not bad, bad


@check
def a3():
    """фильтры быстрые и не падают: 100 КБ текста — доли секунды; None / число / пустое — не спам"""
    big = ("Обычный пост про игру с длинным текстом. " * 2600)[:100_000]
    for fn in (lambda: bot.spam_verdict(big, "src"), lambda: bot.is_rsya_ad(big),
               lambda: bot.spam_verdict("доход от " + "1 " * 40000, "src")):
        t0 = time.perf_counter()
        fn()
        assert time.perf_counter() - t0 < 0.3, time.perf_counter() - t0
    for v in (None, "", 5, "  \n "):
        assert bot.spam_verdict(v, "src")[0] is False, v
    assert bot.is_rsya_ad(None) is False


@check
def a4():
    """флуд 50 спам-постов из одного канала: каждые 10 подряд — мут на час и одно сообщение админу; во время мута
    канал не читаем; за 5 «часов» съедено всё, в очередь не попало ничего; быстро"""
    ALERTS.clear()
    tg = TG([])
    bot.LAST_IDS["flood50"] = 999
    t0 = time.perf_counter()
    for cycle in range(5):
        tg.msgs += [Msg(1000 + cycle * 10 + i, f"Казино бонус №{cycle * 10 + i} 🎰 забирай депозит")
                    for i in range(10)]                     # флуд идёт волнами — бот читает канал часто
        reads = tg.reads
        read(tg, "flood50")
        assert tg.reads == reads + 1 and bot.spam_muted("flood50"), cycle
        read(tg, "flood50")
        assert tg.reads == reads + 1, "в муте канал не читаем"
        for k in list(bot.SPAM_MUTED):
            bot.SPAM_MUTED[k] = time.time() - 1
    took = time.perf_counter() - t0
    assert len(ALERTS) == 5 and all(a == "⚠️ Канал @flood50 заблокирован на 1 час из-за флуда спамом." for a in ALERTS)
    assert not queued("flood50") and bot.LAST_IDS["flood50"] == 1049
    assert sum(1 for e in bot.SPAM_EATEN if e["ch"] == "flood50") == 50
    assert took < 6, took


@check
def a5():
    """три канала флудят одновременно (чтение каналов идёт параллельно) — каждый уходит в мут сам, счёт не
    смешивается, обычный канал рядом работает"""
    ALERTS.clear()
    tgs = {f"flood_{i}": TG([Msg(2000 + j, f"USDT обмен, пиши в лс №{j}") for j in range(10)]) for i in range(3)}
    tgs["calm"] = TG([Msg(2000 + j, f"Новость дня номер {j}: вышло обновление карты") for j in range(10)])
    for ch in tgs:
        bot.LAST_IDS[ch] = 1999

    async def all_at_once():
        await asyncio.gather(*(bot.process_channel(tg, ch, {}) for ch, tg in tgs.items()))
    asyncio.run(all_at_once())
    assert sorted(ALERTS) == sorted(f"⚠️ Канал @flood_{i} заблокирован на 1 час из-за флуда спамом." for i in range(3))
    assert all(bot.spam_muted(f"flood_{i}") for i in range(3)) and not bot.spam_muted("calm")
    assert len(queued("calm")) == 10 and not any(queued(f"flood_{i}") for i in range(3))


@check
def a6():
    """оживлённый канал при обычной работе: 15 постов между двумя чтениями — берём все (не режем «как после
    простоя» и не пишем «бот был выключен»); после простоя (канал не читали 30+ мин) — последние 10"""
    NOTES.clear()
    tg = TG([Msg(3000 + i, f"Новость {i}: обновление карты номер {i}") for i in range(3)])
    read(tg, "busychan", 2999)
    tg.msgs += [Msg(3003 + i, f"Новость {i + 3}: событие дня номер {i + 3}") for i in range(15)]
    read(tg, "busychan")
    assert len(queued("busychan")) == 18 and not any("накопилось" in n for n in NOTES), (len(queued("busychan")), NOTES)
    bot.READ_OK[(bot.THEMES[0].id, "busychan")] = time.time() - 3600
    tg.msgs += [Msg(3018 + i, f"Новость {i + 18}: турнир номер {i + 18}") for i in range(15)]
    read(tg, "busychan")
    assert len(queued("busychan")) == 28 and any("накопилось" in n for n in NOTES), (len(queued("busychan")), NOTES)


# ======================================================================================================================
# B) перехватчик РСЯ
AD = ("2 x iPhone 18 Pro 256 ГБ - 19900₽...\nApple Max - проверенный магазин в MAX...\nКупить\n#реклама\nmax.ru\n"
      "О рекламодателе")


def ad_update(mid, chat=CH, text=AD, sender=0, attachments=None):
    body = {"text": text, "mid": mid}
    if attachments:
        body["attachments"] = attachments
    return {"update_type": "message_created", "message": {"recipient": {"chat_id": chat, "chat_type": "channel"},
                                                          "sender": {"user_id": sender}, "body": body}}


@check
def b1():
    """реклама РСЯ узнаётся во всех видах: пример из жизни; признаки в самом низу 20 КБ текста; регистр и пробелы
    («О РЕКЛАМОДАТЕЛЕ», «#Реклама», «max.ru/», «https://max.ru»); только в кнопках. Не РСЯ: своя маркировка,
    «Сведения о рекламодателе» внутри строки, max.ru в тексте, пусто"""
    yes = [AD, "Текст. " * 3000 + "\n#реклама\nmax.ru", "Скидка\n\n  О РЕКЛАМОДАТЕЛЕ  ", "Акция\n#Реклама\nmax.ru/",
           "Новинка\n#реклама\nhttps://max.ru", "Подарки\nО рекламодателе"]
    no = ["Реклама. ООО «Ромашка», ИНН 7700000000, erid: 2VtzqX\n#реклама",
          "Реклама. Сведения о рекламодателе: ООО «Ромашка»\n#реклама", "Наш канал max.ru/mychan — подписывайтесь",
          "Вышло обновление Roblox 1.2", "", "#реклама"]
    assert all(bot.is_rsya_ad(t) for t in yes), [t[-40:] for t in yes if not bot.is_rsya_ad(t)]
    assert not any(bot.is_rsya_ad(t) for t in no), [t for t in no if bot.is_rsya_ad(t)]
    btns = [{"type": "inline_keyboard", "payload": {"buttons": [[{"type": "link", "text": "Купить"}],
                                                                [{"type": "link", "text": "О рекламодателе"}]]}}]
    assert bot.is_rsya_ad(bot.rsya_text({"text": "iPhone 18 Pro — 19900₽", "attachments": btns}))
    assert bot.rsya_text(None) == "" and bot.rsya_text({"attachments": [None, 5, {"type": "x"}]}) == ""


def fresh_queue(*items):
    bot.QUEUE[:] = list(items)
    bot.RSYA_LAST.clear()
    bot.PUB["last_pub"] = time.time()


@check
def b2():
    """берётся самый старый ГОТОВЫЙ пост: «ждут одобрения», посты с повтором в будущем и «Через…» на своё время
    перехватчик не трогает"""
    pend = item("q:pend", "ждёт одобрения", 9000, status="pending")
    retry = item("q:retry", "повтор позже", 8000, retry_at=time.time() + 600)
    later = item("q:later", "через час", 7000, manual=True, at=time.time() + 3600)
    old, new = item("q:old", "старый", 6000), item("q:new", "новый", 100)
    fresh_queue(new, later, retry, pend, old)
    bot.handle_update(ad_update("b2-ad"))
    assert old.get("manual") and old.get("rsya") and old.get("at", 0) <= time.time()
    assert not new.get("manual") and pend["status"] == "pending" and later["at"] > time.time() + 3000
    assert not retry.get("manual")


@check
def b3():
    """не срабатывает: очередь пуста; в очереди только «ждут одобрения»; тема на паузе; у бота нет прав в канале;
    канал чужой; это наш собственный пост (по mid и по отправителю)"""
    fresh_queue()
    bot.handle_update(ad_update("b3-empty"))
    assert journal_has("очередь пуста")
    fresh_queue(item("q:p", "ждёт", 500, status="pending"))
    bot.handle_update(ad_update("b3-pend"))
    assert not bot.QUEUE[0].get("manual")
    x = item("q:x", "готовый пост", 500)
    for setup, undo in ((lambda: bot.CFG.__setitem__("paused", True), lambda: bot.CFG.__setitem__("paused", False)),
                        (lambda: bot.DENIED.__setitem__(CH, time.time() + 600), lambda: bot.DENIED.pop(CH, None))):
        fresh_queue(x)
        setup()
        try:
            bot.handle_update(ad_update(f"b3-{id(setup)}"))
        finally:
            undo()
        assert not x.get("manual"), "пауза / нет прав — не выкладываем"
    fresh_queue(x)
    bot.handle_update(ad_update("b3-foreign", chat=99999))
    bot.record_pub("mid-own-b3", "news", AD)
    bot.handle_update(ad_update("mid-own-b3"))
    bot.BOT_TITLE["user_id"] = 424242
    try:
        bot.handle_update(ad_update("b3-self", sender=424242))
    finally:
        bot.BOT_TITLE.pop("user_id", None)
    assert not x.get("manual")


@check
def b4():
    """«остывание» ровно 2 минуты: через 119 с после перехвата — новый не запускается, через 121 с — запускается"""
    a, b, c = item("q:a", "A", 900), item("q:b", "B", 800), item("q:c", "C", 700)
    fresh_queue(a, b, c)
    bot.handle_update(ad_update("b4-1"))
    assert a.get("manual") and not b.get("manual")
    bot.RSYA_LAST[CH] = time.time() - 119
    bot.handle_update(ad_update("b4-2"))
    assert not b.get("manual"), "119 с — ещё остывает"
    bot.QUEUE.remove(a)                                     # за 2 минуты планировщик уже выложил A
    bot.RSYA_LAST[CH] = time.time() - 121
    bot.handle_update(ad_update("b4-3"))
    assert b.get("manual") and not c.get("manual"), "121 с — снова работает"


@check
def b5():
    """пост «Сейчас», который и так выходит в ближайшие секунды, сам опустит рекламу — второй не тянем"""
    due = item("q:due", "Сейчас", 900, manual=True, at=time.time() - 1)
    nxt = item("q:nxt", "следующий", 800)
    fresh_queue(due, nxt)
    bot.handle_update(ad_update("b5"))
    assert not nxt.get("manual") and bot.RSYA_LAST.get(CH), "уже выходит пост — второй не нужен"
    assert journal_has("уже выходит")


# --- живой цикл asyncio: гонки перехватчика с планировщиком -----------------------------------------------------------
LOOP = asyncio.new_event_loop()
threading.Thread(target=LOOP.run_forever, daemon=True, name="audit-loop").start()


def on_loop(coro, timeout=15):
    return asyncio.run_coroutine_threadsafe(coro, LOOP).result(timeout)


async def _mk_lock():
    bot.THEMES[0].lock = asyncio.Lock()


async def tick():
    """Как планировщик темы: под замком темы — publish_due."""
    t = bot.THEMES[0]
    with bot.theme_ctx(t):
        async with t.lock:
            await bot.publish_due(None, CH)


async def quiet():
    """Дождаться, пока все, кто ждёт замок темы (перехватчик), отработают."""
    async with bot.THEMES[0].lock:
        await asyncio.sleep(0)


def wait_for(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


@check
def b6():
    """гонка в одну секунду: планировщик выкладывает пост X, и в этот же миг в канале появилась реклама. Перехватчик
    не трогает уходящий X (он мог выйти ДО рекламы и не опустит её), ждёт, а потом берёт следующий — Y. Итог: X и Y
    вышли по одному разу и по порядку, Z ждёт своей очереди"""
    on_loop(_mk_lock())
    gate, entered, sent = threading.Event(), threading.Event(), []

    def slow_send(chat, text, imgs, video_path=None, file_path=None):
        sent.append(text.split("\n")[0])
        if len(sent) == 1:
            entered.set()
            gate.wait(10)
        return f"mid-race-{len(sent)}"
    real_send, bot.send_max = bot.send_max, slow_send
    bot.MAIN_LOOP = LOOP
    try:
        x, y, z = item("r:x", "Пост X", 900), item("r:y", "Пост Y", 800), item("r:z", "Пост Z", 700)
        fresh_queue(x, y, z)
        bot.PUB["last_pub"] = 0                              # пауза прошла: X выходит по расписанию
        fut = asyncio.run_coroutine_threadsafe(tick(), LOOP)
        assert entered.wait(5), "X начал уходить"
        bot.handle_update(ad_update("race-ad"))
        time.sleep(0.3)
        assert not x.get("rsya") and not y.get("manual"), "пока X уходит — перехватчик ждёт"
        gate.set()
        fut.result(10)
        assert wait_for(lambda: y.get("manual")), "после X перехватчик взял Y"
        on_loop(quiet())
        assert not z.get("manual")
        on_loop(tick())
        assert sent == ["Пост X", "Пост Y"], sent
        assert x not in bot.QUEUE and y not in bot.QUEUE and z in bot.QUEUE
    finally:
        gate.set()
        bot.send_max = real_send
        bot.MAIN_LOOP = None


@check
def b7():
    """две рекламы в одну секунду (разные потоки меню) — из очереди уходит ровно один пост"""
    bot.MAIN_LOOP = LOOP
    try:
        a, b, c = item("d:a", "A", 900), item("d:b", "B", 800), item("d:c", "C", 700)
        fresh_queue(a, b, c)
        go = threading.Barrier(4)
        ths = [threading.Thread(target=lambda i=i: (go.wait(), bot.handle_update(ad_update(f"dup-{i}"))))
               for i in range(4)]
        for th in ths:
            th.start()
        for th in ths:
            th.join(5)
        on_loop(quiet())
        assert sum(1 for x in (a, b, c) if x.get("manual")) == 1, [x.get("manual") for x in (a, b, c)]
    finally:
        bot.MAIN_LOOP = None


@check
def b8():
    """кнопка «❌» в «📋 Очереди» по посту, который прямо сейчас уходит в канал: пост не рвётся и не пропадает
    молча — бот честно говорит «уже уходит», пост выходит один раз"""
    on_loop(_mk_lock())
    gate, entered, sent = threading.Event(), threading.Event(), []

    def slow_send(chat, text, imgs, video_path=None, file_path=None):
        sent.append(text.split("\n")[0])
        entered.set()
        gate.wait(10)
        return f"mid-del-{len(sent)}"
    real_send, bot.send_max = bot.send_max, slow_send
    try:
        x = item("del:x", "Пост на удаление", 900)
        fresh_queue(x)
        bot.PUB["last_pub"] = 0
        rej = bot.STATS.get("rejected", 0)
        fut = asyncio.run_coroutine_threadsafe(tick(), LOOP)
        assert entered.wait(5)
        bot.FLASH.pop(1, None)
        bot.do_action(f"qdel:{x['id']}", 1)
        assert "уже уходит" in (bot.FLASH.get(1) or ""), bot.FLASH.get(1)
        assert bot.STATS.get("rejected", 0) == rej
        gate.set()
        fut.result(10)
        assert sent == ["Пост на удаление"] and x not in bot.QUEUE and journal_has("опубликован пост del:x")
    finally:
        gate.set()
        bot.send_max = real_send


@check
def b9():
    """«🗑 Очистить очередь», пока пост X уходит в канал: Y и Z убраны, X не рвётся (выходит один раз), и бот
    честно говорит, что уходящий пост оставил"""
    on_loop(_mk_lock())
    gate, entered, sent = threading.Event(), threading.Event(), []

    def slow_send(chat, text, imgs, video_path=None, file_path=None):
        sent.append(text.split("\n")[0])
        entered.set()
        gate.wait(10)
        return f"mid-clr-{len(sent)}"
    real_send, bot.send_max = bot.send_max, slow_send
    try:
        x, y, z = item("clr:x", "Уходящий X", 900), item("clr:y", "Y", 800), item("clr:z", "Z", 700)
        fresh_queue(x, y, z)
        bot.PUB["last_pub"] = 0
        fut = asyncio.run_coroutine_threadsafe(tick(), LOOP)
        assert entered.wait(5)
        bot.FLASH.pop(1, None)
        bot.do_action("qclr", 1)
        assert y not in bot.QUEUE and z not in bot.QUEUE and x in bot.QUEUE
        assert "оставил" in (bot.FLASH.get(1) or ""), bot.FLASH.get(1)
        gate.set()
        fut.result(10)
        assert sent == ["Уходящий X"] and x not in bot.QUEUE
        bot.FLASH.pop(1, None)
        bot.QUEUE[:] = [item("clr:a", "A", 100)]
        bot.do_action("qclr", 1)
        assert not bot.QUEUE and not bot.FLASH.get(1), "обычная очистка — без лишних слов"
    finally:
        gate.set()
        bot.send_max = real_send


# ======================================================================================================================
# C) ИИ: все ключи отвечают 429
def all_429(_key, _ask):
    return 429, ""


def ai_reset():
    for r in bot.AI_ROTATORS.values():
        r.reset()


bot.GCFG["ai_keys"] = {"openrouter": ["or-key-0001", "or-key-0002"], "deepseek": ["ds-key-0001", "ds-key-0002"]}


@check
def c1():
    """20 постов подряд, все ключи всех ИИ — 429: каждый выходит ОРИГИНАЛОМ, в журнале «AI skipped: all keys
    exhausted…», ИИ спросили только 4 раза (по разу каждым ключом — дальше ключи «отдыхают»), всё за пару секунд"""
    real = bot.ai_on, bot.send_max
    pub = []
    bot.ai_on = lambda: True
    bot.send_max = lambda chat, text, imgs, video_path=None, file_path=None: (pub.append(text), f"mid-c1-{len(pub)}")[1]
    srv.ai_rule, srv.delay = all_429, 0
    ai_reset()
    try:
        calls0 = len(srv.ai_calls)
        before = sum(1 for j in bot.JOURNAL if bot.AI_EXHAUSTED_LOG in j)
        t0 = time.perf_counter()
        for i in range(20):
            it = item(f"ai:{i}", f"Новость номер {i} про игру", 60)
            bot.QUEUE[:] = [it]
            assert asyncio.run(bot.publish_item(None, CH, it)) == "ok", i
        took = time.perf_counter() - t0
        assert [p.split("\n")[0] for p in pub] == [f"Новость номер {i} про игру" for i in range(20)], pub[:3]
        assert len(srv.ai_calls) - calls0 == 4, srv.ai_calls[calls0:]
        assert sum(1 for j in bot.JOURNAL if bot.AI_EXHAUSTED_LOG in j) - before == 20
        assert took < 5, took
    finally:
        bot.ai_on, bot.send_max = real
        srv.ai_rule = None


@check
def c2():
    """ИИ медленно отвечает 429 (каждый ответ — 0,25 с, всего ~1 с на пост): цикл asyncio всё это время свободен
    (пересказ — в отдельном потоке), пост выходит оригиналом"""
    real = bot.ai_on, bot.send_max
    pub, lags = [], []
    bot.ai_on = lambda: True
    bot.send_max = lambda chat, text, imgs, video_path=None, file_path=None: (pub.append(text), "mid-c2")[1]
    srv.ai_rule, srv.delay = all_429, 0.25
    ai_reset()
    try:
        it = item("ai:slow", "Медленный ИИ, а пост выходит", 60)
        bot.QUEUE[:] = [it]

        async def main():
            done = asyncio.Event()

            async def hb():
                while not done.is_set():
                    t0 = time.perf_counter()
                    await asyncio.sleep(0.01)
                    lags.append(time.perf_counter() - t0 - 0.01)
            task = asyncio.create_task(hb())
            try:
                return await bot.publish_item(None, CH, it)
            finally:
                done.set()
                await task
        t0 = time.perf_counter()
        assert asyncio.run(main()) == "ok"
        took = time.perf_counter() - t0
        assert took >= 0.9 and len(lags) > 30 and max(lags) < 0.1, (took, len(lags), max(lags))
        assert pub and pub[0].startswith("Медленный ИИ, а пост выходит")
    finally:
        bot.ai_on, bot.send_max = real
        srv.ai_rule, srv.delay = None, 0


@check
def c3():
    """длинный пост из нескольких сообщений: 1-е ушло оригиналом (ИИ — 429), 2-е сорвалось (MAX 503); ИИ ожил —
    при повторе бот ПРОДОЛЖАЕТ тот же оригинал, не склеивает пост из оригинала и пересказа и ИИ не спрашивает"""
    real = bot.ai_on, bot.send_max
    sent = []

    def flaky(chat, text, imgs, video_path=None, file_path=None):
        sent.append(text)
        if len(sent) == 2:
            raise bot.MaxTemporary("MAX 503: перегружен")
        return f"mid-c3-{len(sent)}"
    bot.ai_on, bot.send_max = (lambda: True), flaky
    srv.ai_rule = all_429
    ai_reset()
    try:
        long = "".join(f"Абзац {i}: " + "слово " * 60 + "\n\n" for i in range(30)).strip()
        it = item("ai:long", long, 60)
        bot.QUEUE[:] = [it]
        assert asyncio.run(bot.publish_item(None, CH, it)) == "retry" and it["sent"] == 1
        srv.ai_rule = lambda _k, ask: (200, "ПЕРЕСКАЗ " + ask[-30:])
        ai_reset()
        calls0 = len(srv.ai_calls)
        it["retry_at"] = 0
        assert asyncio.run(bot.publish_item(None, CH, it)) == "ok"
        want = bot.split_text(bot.compose_text(long, "news", rewritten=False))
        assert len(want) >= 3 and sent[0] == want[0] and sent[1] == want[1] and sent[2:] == want[1:], \
            [s[:40] for s in sent]
        assert not any("ПЕРЕСКАЗ" in s for s in sent) and len(srv.ai_calls) == calls0
    finally:
        bot.ai_on, bot.send_max = real
        srv.ai_rule = None


@check
def c4():
    """при 429 аналитика — без строки 🤖 (стандартные строки + 📊), в bot.log — «AI skipped…»; тренды — своё
    «нет ИИ» (TrendNoAI), без сырых ошибок"""
    srv.ai_rule = all_429
    ai_reset()
    bot.INSIGHT_CACHE.clear()
    try:
        n = {"title": "Канал", "subs": 1000, "v24": 100, "v48": 120, "v72": 130, "er24": 10.0, "today": 5, "d7": 20}
        assert bot.stats_text(n) == bot.card_caption(n) + "\n" + bot.info_line(n)
        with open(bot.LOG_FILE, encoding="utf-8") as f:
            assert bot.AI_EXHAUSTED_LOG in f.read()
        try:
            bot.trend_ask("Есть ли новость?")
            raise AssertionError("trend_ask должен сказать «нет ИИ»")
        except bot.TrendNoAI:
            pass
    finally:
        srv.ai_rule = None


# ======================================================================================================================
# D) удалённые каналы: ровно 7 дней
def chpick_text(cid):
    for page in range(8):
        text, rows = bot.screen(f"chpick:{page}", 1)
        if f"a:chbind:{cid}" in payloads(rows):
            return text
    return ""


def make_removed(cid, title):
    srv.private[cid] = (title, f"https://max.ru/join/c{abs(cid)}")
    bot.remember_chat(cid, 1)
    bot.CHAT_INFO_CACHE.clear()
    bot.pick_channels(1)                                   # MAX показал канал — запомнили название
    bot.handle_update({"update_type": "bot_removed", "chat_id": cid, "is_channel": True, "user": {"user_id": 1}})
    srv.private.pop(cid, None)
    bot.CHAT_INFO_CACHE.clear()


@check
def d1():
    """ровно 7 дней: сразу — «7 дней», за 5 с до конца — «1 день», через 1 с после 7 суток — канала нет ни в
    списке, ни в known_chats.json"""
    bot.max_chats = lambda: []
    make_removed(-100910, "Граница")
    assert "«Граница» — удалён (будет скрыт через 7 дней)" in chpick_text(-100910)
    bot.KNOWN_CHATS["-100910"]["removed"] = time.time() - 7 * 86400 + 5
    assert "«Граница» — удалён (будет скрыт через 1 день)" in chpick_text(-100910)
    bot.KNOWN_CHATS["-100910"]["removed"] = time.time() - 7 * 86400 - 1
    assert all(c["chat_id"] != -100910 for c in bot.pick_channels(1))
    assert "-100910" not in rjson("known_chats.json")


@check
def d2():
    """почасовая проверка (без открытия списка): сама замечает канал, удалённый в MAX (часы «7 дней» идут с этого
    момента), и сама чистит базу через 7 дней"""
    bot.max_chats = lambda: []
    chat_id, bot.THEMES[0].chat_id = bot.THEMES[0].chat_id, None    # проверка прав каналов тем тут ни при чём
    try:
        srv.private[-100920] = ("Удалят в MAX", "https://max.ru/join/c920")
        bot.remember_chat(-100920, 1)
        bot.CHAT_INFO_CACHE.clear()
        bot.check_rights_all()                              # час прошёл: запомнили название
        srv.private.pop(-100920)
        bot.CHAT_INFO_CACHE.clear()
        bot.check_rights_all()                              # ещё час: MAX говорит «нет такого канала»
        rec = bot.KNOWN_CHATS.get("-100920") or {}
        assert rec.get("removed") and time.time() - rec["removed"] < 60 and rec.get("title") == "Удалят в MAX", rec
        rec["removed"] = time.time() - 7 * 86400 - 1
        bot.check_rights_all()
        assert "-100920" not in bot.KNOWN_CHATS and "-100920" not in rjson("known_chats.json")
    finally:
        bot.THEMES[0].chat_id = chat_id


@check
def d3():
    """удалили → вернули бота → снова удалили: отсчёт 7 дней начинается заново; срок переживает перезапуск"""
    bot.max_chats = lambda: []
    make_removed(-100930, "Туда-сюда")
    bot.KNOWN_CHATS["-100930"]["removed"] = time.time() - 5 * 86400
    srv.private[-100930] = ("Туда-сюда", "https://max.ru/join/c100930")
    bot.handle_update({"update_type": "bot_added", "chat_id": -100930, "is_channel": True, "user": {"user_id": 1}})
    assert not bot.KNOWN_CHATS["-100930"].get("removed")
    make_removed(-100930, "Туда-сюда")
    assert "через 7 дней" in chpick_text(-100930)
    bot.KNOWN_CHATS["-100930"]["removed"] = time.time() - 2 * 86400 - 60
    bot._save_known()
    b2 = load_bot("bot_d3")
    b2.max_chats = lambda: []
    c = next((c for c in b2.pick_channels(1) if c["chat_id"] == -100930), None)
    assert c and c.get("removed") and b2.grace_days_left(c["removed"]) == 5, c


# ======================================================================================================================
# E) чёрный список спама не растёт бесконечно
def pseudo_word(i):
    return "".join("абвгдежзик"[int(d)] for d in f"{i:07d}")


@check
def e1():
    """700 «спам-постов» с уникальными ссылками и словами — список упирается в потолки (ссылок ≤ 500, слов ≤ 300),
    файл маленький; самые давно не срабатывавшие правила уходят первыми"""
    bot.SPAM_BLOCK.update(links=[], words=[], seen={})
    for i in range(700):
        bot.spam_learn(f"Пиши @spammer{i:05d}x — курс на spamsite{i}.ru, {pseudo_word(i)} {pseudo_word(i + 10_000)}",
                       "src")
    bl = rjson("spam_blocklist.json")
    assert len(bl["links"]) <= bot.SPAM_BLOCK_MAX_LINKS == 500 and len(bl["words"]) <= bot.SPAM_BLOCK_MAX_WORDS == 300
    assert "spamsite699.ru" in bl["links"] and "spamsite0.ru" not in bl["links"], "старые — первыми"
    assert os.path.getsize("spam_blocklist.json") < 120_000, os.path.getsize("spam_blocklist.json")
    assert set(bl["seen"]) == set(bl["links"]) | set(bl["words"]), "у каждого правила — время, лишнего нет"


@check
def e2():
    """правила, которые не срабатывали 90 дней, удаляются почасовой проверкой; срабатывающее живёт, даже если
    добавлено давно; свежее — живёт"""
    now = time.time()
    bot.SPAM_BLOCK.update(links=["old-spam.ru", "fresh-spam.ru", "hit-spam.ru"], words=["стараяфраза", "свежаяфраза"],
                          seen={"old-spam.ru": now - 91 * 86400, "fresh-spam.ru": now - 10 * 86400,
                                "hit-spam.ru": now - 200 * 86400, "стараяфраза": now - 100 * 86400,
                                "свежаяфраза": now})
    assert bot.spam_verdict("Купи курс тут: hit-spam.ru/x", "src")[2] == "block"   # сработало — правило «живое»
    bot.spam_maintenance()
    bl = rjson("spam_blocklist.json")
    assert sorted(bl["links"]) == ["fresh-spam.ru", "hit-spam.ru"] and bl["words"] == ["свежаяфраза"], bl


@check
def e3():
    """старый формат файла (только списки) и испорченные файлы читаются без падений"""
    wtext("spam_blocklist.json", json.dumps({"links": ["legacy.ru"], "words": ["словечко"]}))
    b1 = load_bot("bot_e3a")
    assert b1.SPAM_BLOCK["links"] == ["legacy.ru"] and time.time() - b1.SPAM_BLOCK["seen"]["legacy.ru"] < 60
    b1.spam_maintenance()
    assert b1.SPAM_BLOCK["links"] == ["legacy.ru"], "старому формату даём свежий срок"
    wtext("spam_blocklist.json.bak", json.dumps({"links": ["frombak.ru"], "words": []}))
    wtext("spam_blocklist.json", "{не json")
    assert load_bot("bot_e3r").SPAM_BLOCK["links"] == ["frombak.ru"], "испорчен — подняли из резервной копии"
    for junk in ("{не json", json.dumps({"links": "строка", "words": [1, None, "нормально", {"x": 1}]}),
                 json.dumps([1, 2]), ""):
        for p in ("spam_blocklist.json.bak", "spam_blocklist.json.broken"):
            if os.path.exists(p):
                os.remove(p)
        wtext("spam_blocklist.json", junk)
        b = load_bot("bot_e3b")
        assert set(b.SPAM_BLOCK) >= {"links", "words", "seen"} and b.SPAM_BLOCK["links"] == [], b.SPAM_BLOCK
        assert all(isinstance(w, str) and w not in ("1", "none") for w in b.SPAM_BLOCK["words"]), b.SPAM_BLOCK
        assert not b.spam_verdict("Обычный пост про обновление", "src")[0]
    for junk in ("{не json", json.dumps({"bad": 1}), json.dumps([{"t": 5, "ch": "a"}, 7, {"no_t": 1}, None])):
        wtext("spam_eaten.json", junk)
        b = load_bot("bot_e3c")
        assert all(isinstance(e, dict) and "t" in e for e in b.SPAM_EATEN), b.SPAM_EATEN
        b.screen("spam", 1)                                # сводка не падает
    bot.spam_log_flush(force=True)


@check
def e4():
    """«чёрный список» учится из 8 потоков одновременно — без ошибок, без дублей, файл целый"""
    bot.SPAM_BLOCK.update(links=[], words=[], seen={})
    errs = []

    def learn(k):
        try:
            for j in range(25):
                bot.spam_learn(f"Пиши @thr{k}x{j:03d}spam и заходи на th{k}-{j}.ru", "src")
        except Exception as e:
            errs.append(repr(e))
    ths = [threading.Thread(target=learn, args=(k,)) for k in range(8)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(30)
    bl = rjson("spam_blocklist.json")
    assert not errs, errs[:3]
    assert len(bl["links"]) == len(set(bl["links"])) == 400 and len(bot.SPAM_BLOCK["links"]) == 400, len(bl["links"])


# ======================================================================================================================
# F) потокобезопасность и прочее
@check
def f1():
    """«съеденное» пишут 8 потоков одновременно (каналы читаются параллельно) — ни одна запись не теряется,
    файл целый"""
    bot.SPAM_EATEN.clear()
    errs = []

    def eat(k):
        try:
            for j in range(50):
                bot.eat_spam(f"conc{k}", 10_000 * k + j, "тест", "scam", flood=False)
        except Exception as e:
            errs.append(repr(e))
    ths = [threading.Thread(target=eat, args=(k,)) for k in range(8)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(30)
    assert not errs and len(bot.SPAM_EATEN) == 400, (errs[:2], len(bot.SPAM_EATEN))
    bot.spam_log_flush(force=True)
    assert len(rjson("spam_eaten.json")) == 400


@check
def f2():
    """список каналов: 4 потока добавляют/«удаляют»/чистят, 4 потока открывают список — без падений, база целая,
    ключи без дублей"""
    errs = []
    real_info, real_sw = bot.chat_info_cached, sys.getswitchinterval()
    bot.chat_info_cached = lambda cid, ttl=600: {}
    sys.setswitchinterval(1e-6)                             # потоки переключаются постоянно — гонки видны

    def churn(k):
        try:
            for j in range(300):
                cid = -300_000 - (k * 1000 + j % 60)
                bot.remember_chat(cid, 1)
                if j % 3 == 0:
                    bot.forget_chat(cid)
                if j % 7 == 0:
                    bot.purge_removed_chats(time.time() + random.choice((0, 8 * 86400)))
        except Exception as e:
            errs.append(repr(e))

    def lister():
        try:
            for _ in range(60):
                bot.known_chats(include_removed=True)
                bot.bot_channels()
        except Exception as e:
            errs.append(repr(e))
    try:
        ths = [threading.Thread(target=churn, args=(k,)) for k in range(4)] + \
              [threading.Thread(target=lister) for _ in range(4)]
        for th in ths:
            th.start()
        for th in ths:
            th.join(60)
    finally:
        bot.chat_info_cached = real_info
        sys.setswitchinterval(real_sw)
    data = rjson("known_chats.json")
    assert not errs, errs[:3]
    assert all(k == str(int(k)) for k in data), [k for k in data if k != str(int(k))][:3]


@check
def f3():
    """событие «пост удалён» приходит дважды, с chat_id строкой или вовсе без него — учимся один раз"""
    bot.SPAM_BLOCK.update(links=[], words=[], seen={})
    bot.record_pub("mid-f3", "news", "Пиши @DoubleRemoved за подарками")
    n0 = sum(1 for j in bot.JOURNAL if "удалён вручную" in j)
    assert bot.on_message_removed({"message_id": "mid-f3", "chat_id": str(CH)})
    assert not bot.on_message_removed({"message_id": "mid-f3", "chat_id": CH})
    assert not bot.on_message_removed({"message_id": "mid-f3"})
    assert sum(1 for j in bot.JOURNAL if "удалён вручную" in j) == n0 + 1
    assert "@doubleremoved" in bot.SPAM_BLOCK["links"]
    bot.record_pub("mid-f3b", "news", "Пиши @NoChatIdSpam за подарками")
    assert bot.on_message_removed({"message_id": "mid-f3b"}) and "@nochatidspam" in bot.SPAM_BLOCK["links"]


@check
def f4():
    """ссылка на съеденный пост: у канала Telegram — t.me/канал/номер, у соцсети и закрытого (id) — «источник #номер»;
    экран сводки без данных не падает"""
    assert bot.src_post_link("news", 5) == "https://t.me/news/5"
    assert bot.src_post_link("rss~abc", 5) == "rss~abc #5" and bot.src_post_link("-1001234", 7) == "-1001234 #7"
    assert bot.src_post_link("", 1) == " #1" or bot.src_post_link("", 1).endswith("#1")
    saved = list(bot.SPAM_EATEN)
    bot.SPAM_EATEN.clear()
    try:
        text, _ = bot.screen("spam", 1)
        assert "Сегодня: 0 · за 7 дней: 0" in text, text
    finally:
        bot.SPAM_EATEN[:] = saved


if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    sys.exit(1)
print("ВСЕ ТЕСТЫ АУДИТА ПРОШЛИ")
os._exit(0)
