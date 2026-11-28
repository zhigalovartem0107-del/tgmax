"""📢 Кросс-промо: свой рекламный пост в СВОИ каналы по своему таймеру.

1) Создание и строгое владение: кнопка «📢 Кросс-промо» — у хозяина в панели и у VIP-покупателей («⭐ VIP» включает
   хозяин в карточке покупателя); пост (текст + фото/видео) → выбор каналов — ТОЛЬКО каналы своего кабинета (чужие
   не показываются, поддельные нажатия с чужими темами отклоняются, даже в обход кнопок чужое не сохраняется и не
   публикуется) → интервал (кнопкой или числом часов) → кампания сохранена.
2) Независимая публикация: у кампании свой таймер — не очередь постов из источников: не занимает её мест (очередь и
   пауза между постами не меняются), не ждёт её (пустая очередь и пауза бота не мешают); пост просто выходит в
   выбранные каналы раз в N часов, между обычными постами (с постом из очереди не перемешивается).
3) Управление: в «📢 Кросс-промо» — список кампаний кабинета с «⏸ Остановить» и «🗑 Удалить» (и «▶️ Возобновить»).
Бот в host-режиме, поддельный MAX, настоящие нажатия кнопок."""
import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import asyncio
import importlib.util
import os
import sys
import tempfile
import threading
import time
import traceback
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from fake_max import FakeMax  # noqa: E402

try:
    from bot_app.services import xpromo as X
except ImportError:                                     # модуля ещё нет — проверки скажут, чего не хватает
    X = None

FAILS = []
H = 3600


def check(fn):
    """Каждая проверка — отдельно: красная не мешает увидеть остальные."""
    try:
        fn()
        print(f"{fn.__name__} ok: {fn.__doc__}")
    except Exception as e:                               # и «нет такой функции» — тоже провал проверки, а не падение
        FAILS.append(fn.__name__)
        line = traceback.extract_tb(e.__traceback__)[-1]
        print(f"{fn.__name__} FAIL (строка {line.lineno}: {line.line}) — {type(e).__name__}: {str(e)[:600]}")
    return fn


# ---------- 1) учёт кампаний: без сети и без бота ----------
def store(path=None, clock=None):
    path = path or os.path.join(tempfile.mkdtemp(), "xpromo.json")
    save = lambda p, data, **k: tio.wjson(data, p, "w", _dump={"ensure_ascii": False})
    load = lambda p, default: tio.rjson(p) if os.path.exists(p) else default
    return X.Campaigns(path, save=save, load=load, clock=clock or time.time), path


@check
def s1():
    """кампания: владелец, пост, каналы, интервал (от 1 ч до 7 дней); пустой пост, без каналов, кривой интервал —
    не сохраняем; у каждой свой id; список — только кампании этого владельца; первый выход — сразу, дальше раз в
    интервал; «остановить», «возобновить» и «удалить» — только своему владельцу; возобновили — по своему расписанию
    (не раньше, чем через интервал после прошлого выхода); число часов из сообщения"""
    assert X is not None, "нет модуля bot_app.services.xpromo"
    now = [10_000.0]
    st, _ = store(clock=lambda: now[0])
    img = [{"kind": "image", "token": "tok1"}]
    for bad in (dict(text="", media=[]), dict(themes=[]), dict(every=H - 1), dict(every=7 * 24 * H + 1),
                dict(every="2"), dict(themes=["t1", 5]), dict(media=[{"kind": "file", "token": "x"}], text="")):
        args = dict(owner="A", by=1, text="Промо", media=img, themes=["t1"], every=2 * H)
        args.update(bad)
        assert st.add(**args) is None, bad
    a = st.add("A", 1, "Промо A", img, ["t1", "t2", "t1"], 2 * H)
    b = st.add("B", 2, "Промо B", [], ["t9"], 12 * H)
    assert a and b and a["id"] != b["id"] and a["themes"] == ["t1", "t2"] and a["state"] == "on", (a, b)
    assert [c["id"] for c in st.of_owner("A")] == [a["id"]] and [c["id"] for c in st.of_owner("B")] == [b["id"]]
    assert sorted(st.due(now[0])) == sorted([(a["id"], "t1"), (a["id"], "t2"), (b["id"], "t9")]), "первый — сразу"
    st.done(a["id"], "t1", now[0])
    st.done(a["id"], "t2", now[0])
    assert st.due(now[0] + 2 * H - 1) == [(b["id"], "t9")] and len(st.due(now[0] + 2 * H)) == 3
    assert st.get(a["id"])["sent"] == 2 and st.get(a["id"])["last"] == now[0]
    for who in ("B", "", None):
        assert st.pause(a["id"], who) is False and st.delete(a["id"], who) is False, who
    assert st.get(a["id"])["state"] == "on"
    assert st.pause(a["id"], "A") is True and st.get(a["id"])["state"] == "paused"
    assert all(cid != a["id"] for cid, _ in st.due(now[0] + 10 * H)), "остановлена — не выходит"
    now[0] += 30 * 60
    assert st.resume(a["id"], "B") is False and st.resume(a["id"], "A") is True
    assert not any(cid == a["id"] for cid, _ in st.due(now[0])), "через 30 мин после выхода — ещё рано"
    assert {tid for cid, tid in st.due(now[0] + 90 * 60) if cid == a["id"]} == {"t1", "t2"}
    st.later(a["id"], "t1", now[0] + 5 * 60, "MAX недоступен")
    assert st.get(a["id"])["next"]["t1"] == now[0] + 5 * 60
    assert st.delete(a["id"], "A") is True and st.get(a["id"]) is None and st.of_owner("A") == []
    assert st.delete(a["id"], "A") is False
    for text, want in (("2", 2), ("2 ч", 2), ("каждые 12 часов", 12), ("12h", 12), ("1", 1), ("168", 168),
                       ("раз в сутки", 24), (" 3 часа ", 3)):
        assert X.parse_hours(text) == want * H, (text, X.parse_hours(text))
    for bad in ("0", "169", "-2", "abc", "", None, "2.5", "1e3"):
        assert X.parse_hours(bad) is None, bad


@check
def s2():
    """кампании — в файле: переживают перезапуск; не больше X.KEEP_PER_OWNER на кабинет; испорченный файл и мусор
    в нём — не падаем, кривые записи пропускаем; удаление данных кабинета убирает все его кампании"""
    st, path = store()
    a = st.add("A", 1, "Промо", [{"kind": "video", "token": "v1"}], ["t1"], 3 * H)
    st.add("B", 2, "Б", [], ["t9"], H)
    st.pause(a["id"], "A")
    st2, _ = store(path)
    assert st2.get(a["id"]) == st.get(a["id"]) and len(st2.of_owner("B")) == 1, "перезапуск"
    for _ in range(X.KEEP_PER_OWNER + 3):
        st2.add("C", 3, "много", [], ["t5"], H)
    assert len(st2.of_owner("C")) == X.KEEP_PER_OWNER
    assert st2.forget_owner("C") == X.KEEP_PER_OWNER and st2.of_owner("C") == [] and len(st2.of_owner("B")) == 1
    for junk in ("{oops", "[1, 2]", '{"items": [1]}', '{"items": {"x": 5, "y": {"owner": "A"}}}',
                 '{"items": {"z": {"owner": "A", "themes": "t1", "every": "x", "text": 5, "state": "?"}}}'):
        tio.wfile(junk, path, "w")
        st3, _ = store(path)
        assert st3.of_owner("A") == [] and st3.due() == [], junk
        assert st3.add("A", 1, "ок", [], ["t1"], H), junk


# ---------- 2) бот в host-режиме: хозяин, два VIP-покупателя (A, B) и обычный покупатель C ----------
tele = types.ModuleType("telethon")
tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele
sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, YK_API_URL=srv.url, BOT_TOKEN="TESTTOKEN", TGMAX_HOST="1", OWNER_ID="1")
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ADMIN_IDS", "YOOKASSA_SHOP_ID",
          "YOOKASSA_SECRET_KEY", "PARTNER_ID"):
    os.environ.pop(k, None)
os.chdir(tempfile.mkdtemp())
tio.wjson({"max_token": "TESTTOKEN", "max_channel_link": ""}, "config.json", "w")
spec = importlib.util.spec_from_file_location("bot", os.path.join(ROOT, "tg_to_max.py"))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
bot.S.trust_env = False
bot.GCFG["trial_days"] = 0
SENT, CUR_CHAT = [], [None]


def _send(chat, text, rows=None, token=None, keep=False):
    SENT.append((chat, text or "", rows or []))
    return f"m{len(SENT)}"


bot.api_send = _send                                      # меню и сообщения в личку — сюда; посты в каналы — в MAX
bot.send_screen = lambda chat, text, rows=None: _send(chat, text, rows)
bot.api_answer = lambda cid, text, rows=None: SENT.append((CUR_CHAT[0], text or "", rows or []))


def msg(uid, chat, text, atts=None):
    body = {"text": text}
    if atts:
        body["attachments"] = atts
    CUR_CHAT[0] = chat
    bot.handle_update({"update_type": "message_created", "message": {
        "sender": {"user_id": uid, "first_name": f"U{uid}"}, "recipient": {"chat_id": chat, "chat_type": "dialog"},
        "body": body}})


def press(uid, chat, payload):
    CUR_CHAT[0] = chat
    bot.handle_update({"update_type": "message_callback", "callback": {
        "callback_id": "c", "payload": payload, "user": {"user_id": uid, "first_name": f"U{uid}"}},
        "message": {"recipient": {"chat_id": chat, "chat_type": "dialog"}}})


def last(chat):
    return next(e for e in reversed(SENT) if e[0] == chat)


def buttons(rows):
    return [b for row in rows for b in row]


def payloads(rows):
    return [b.get("payload") or "" for b in buttons(rows)]


def screen_of(uid, chat, payload):
    press(uid, chat, payload)
    _, text, rows = last(chat)
    return text, rows


def posts(chat_id, n0=0):
    """Что ушло в канал MAX (не в личку): тексты и вложения."""
    return [m for m in srv.messages[n0:] if m["chat_id"] == chat_id]


def tick(now):
    """Шаг таймера кросс-промо (как в работающем боте, только время задаём сами)."""
    asyncio.run(bot.xpromo_tick(now))


def queue_item(iid, text):
    now = time.time()
    return {"id": iid, "ch": "news", "date": now - 600, "enq": now - 600, "text": text, "photos": [], "vids": [],
            "docs": [], "sent": 0, "fails": 0, "status": "ok"}


HOST, HC = 1, 61000
A, AC, B_, BC, C, CC = 5001, 62001, 5002, 62002, 5003, 62003
msg(HOST, HC, "/start")
for u, ch in ((A, AC), (B_, BC), (C, CC)):
    msg(u, ch, "/start")
KA, KB, KC = bot.tenant_key(A), bot.tenant_key(B_), bot.tenant_key(C)
for k in (KA, KB, KC):
    bot.host_extend(k, 30)
bot.ensure_host_cabinet()
bot.host_extend(bot.host_id(), 30)
TA1 = bot.tenant_themes(KA)[0]
TA1.name, TA1.chat_id = "A-новости", -7101
TA2 = bot.create_theme("A-мемы", KA)
TA2.chat_id = -7102
TA3 = bot.create_theme("A-без канала", KA)                 # канал не подключён — выбирать нечего
TB1 = bot.tenant_themes(KB)[0]
TB1.name, TB1.chat_id = "B-канал", -7201
TC1 = bot.tenant_themes(KC)[0]
TC1.name, TC1.chat_id = "C-канал", -7301
TH = bot.tenant_themes(bot.host_id())[0]
TH.name, TH.chat_id = "Канал хозяина", -7001
for t in (TA1, TA2, TA3, TB1, TC1, TH):
    with bot.theme_ctx(t):
        bot.CFG["paused"] = False
        bot.CFG["night_on"] = False
assert all(bot.theme_live(t) for t in (TA1, TA2, TB1, TC1, TH))
IMG = {"type": "image", "payload": {"token": "imgA1", "url": srv.url + "/nope.jpg"}}
VID = {"type": "video", "payload": {"token": "vidA1"}}
PROMO_A = "Подписывайтесь на канал друзей: https://max.ru/partner_ch"
CAMP = {}


def mine(owner):
    return bot.XP.of_owner(owner)


@check
def c1():
    """«📢 Кросс-промо»: у хозяина — в панели; «⭐ VIP» покупателю включает хозяин в его карточке — тогда кнопка у
    покупателя в «⚙️ Ещё настройки»; у обычного покупателя кнопки нет, а поддельные нажатия (m:xp, a:xpnew, a:clvip)
    ничего не открывают и не включают"""
    text, rows = screen_of(HOST, HC, "m:cl")
    assert "m:xp" in payloads(rows), payloads(rows)
    text, rows = screen_of(HOST, HC, f"m:clc:{KA}")
    assert f"a:clvip:{KA}" in payloads(rows), payloads(rows)
    press(HOST, HC, f"a:clvip:{KA}")
    press(HOST, HC, f"a:clvip:{KB}")
    assert bot.TENANTS[KA].get("vip") is True and bot.TENANTS[KB].get("vip") is True
    assert "VIP" in last(HC)[1], last(HC)[1]
    assert bot.xp_allowed(A) and bot.xp_allowed(B_) and bot.xp_allowed(HOST) and not bot.xp_allowed(C)
    text, rows = screen_of(A, AC, "m:more")
    assert "m:xp" in payloads(rows), payloads(rows)
    text, rows = screen_of(C, CC, "m:more")
    assert "m:xp" not in payloads(rows), payloads(rows)
    text, rows = screen_of(C, CC, "m:xp")
    assert not text.startswith("📢 Кросс-промо") and "a:xpnew" not in payloads(rows), text
    press(C, CC, "a:xpnew")
    assert bot.PENDING.get(C) != "xpost" and C not in bot.XP_DRAFT
    msg(C, CC, "моя реклама")
    assert mine(KC) == []
    press(C, CC, f"a:clvip:{KC}")
    assert not bot.TENANTS[KC].get("vip"), "покупатель сам себе VIP не включит"
    press(HOST, HC, f"a:clvip:{KC}")
    press(HOST, HC, f"a:clvip:{KC}")                        # включил и выключил
    assert not bot.TENANTS[KC].get("vip") and not bot.xp_allowed(C)


@check
def c2():
    """VIP-покупатель A: «➕ Новая кампания» → пост (текст + фото) → выбор каналов: ТОЛЬКО его каналы MAX (две темы с
    подключённым каналом); каналов B, C и хозяина нет ни в списке, ни в тексте; свою тему без канала выбрать нельзя
    (только подсказка «подключи канал»)"""
    text, rows = screen_of(A, AC, "m:xp")
    assert text.startswith("📢 Кросс-промо") and "a:xpnew" in payloads(rows), (text, rows)
    press(A, AC, "a:xpnew")
    assert bot.PENDING.get(A) == "xpost", bot.PENDING.get(A)
    msg(A, AC, PROMO_A, [IMG])
    _, text, rows = last(AC)
    picked = [p.split(":", 2)[2] for p in payloads(rows) if p.startswith("a:xpt:")]
    assert sorted(picked) == sorted([TA1.id, TA2.id]), (picked, text)
    shown = text + " ".join(b.get("text") or "" for b in buttons(rows))
    for other in (TB1, TC1, TH):                           # чужих каналов не видно вовсе
        assert other.name not in shown, (other.name, shown)
    assert not any(TA3.name in (b.get("text") or "") for b in buttons(rows)), "своя тема без канала — не выбрать"
    assert TA3.name in text, "…но подсказка, что её канал не подключён"
    d = bot.XP_DRAFT[A]
    assert d["text"] == PROMO_A and d["media"] == [{"kind": "image", "token": "imgA1"}] and d["themes"] == [], d


@check
def c3():
    """поддельные нажатия с чужими темами (B, C, хозяина, несуществующей) — «⛔», в черновик не попадают; своя тема
    отмечается и снимается; «все мои» — только свои; чужая тема, подложенная в черновик в обход кнопок, не
    сохраняется; интервал кнопкой — кампания запущена: владелец — кабинет A, каналы — только свои, каждые 2 ч"""
    for bad in (TB1.id, TC1.id, TH.id, "t999", "../x", ""):
        n = len(SENT)
        press(A, AC, f"a:xpt:{bad}")
        assert bad not in bot.XP_DRAFT[A]["themes"], bad
        assert any("⛔" in e[1] for e in SENT[n:] if e[0] == AC), (bad, SENT[n:])
    press(A, AC, f"a:xpt:{TA1.id}")
    assert bot.XP_DRAFT[A]["themes"] == [TA1.id]
    press(A, AC, f"a:xpt:{TA1.id}")
    assert bot.XP_DRAFT[A]["themes"] == []
    press(A, AC, "a:xpall")
    assert sorted(bot.XP_DRAFT[A]["themes"]) == sorted([TA1.id, TA2.id])
    bot.XP_DRAFT[A]["themes"].append(TB1.id)                # «взлом»: чужая тема в обход кнопок
    text, rows = screen_of(A, AC, "a:xpnext")
    assert bot.PENDING.get(A) == "xpiv" and "a:xpiv:2" in payloads(rows), (text, rows)
    press(A, AC, "a:xpiv:2")
    camps = mine(KA)
    assert len(camps) == 1, camps
    c = camps[0]
    assert sorted(c["themes"]) == sorted([TA1.id, TA2.id]) and c["owner"] == KA and c["every"] == 2 * H, c
    assert c["text"] == PROMO_A and c["media"] == [{"kind": "image", "token": "imgA1"}] and c["state"] == "on"
    assert A not in bot.XP_DRAFT and bot.PENDING.get(A) is None
    assert mine(KB) == [] and all(TB1.id not in x["themes"] for x in bot.XP.of_owner(KA))
    CAMP["a"] = c["id"]


@check
def c4():
    """хозяин в панели видит все кабинеты, но в кросс-промо выбирает ТОЛЬКО каналы своего кабинета: каналов
    покупателей нет в списке, поддельное нажатие с каналом покупателя — «⛔»; интервал — числом часов в сообщении
    (0, 200 и «abc» — не принимаем, ждём дальше)"""
    press(HOST, HC, "m:xp")
    press(HOST, HC, "a:xpnew")
    msg(HOST, HC, "Промо хозяина: подпишись на @host_news")
    _, text, rows = last(HC)
    assert [p.split(":", 2)[2] for p in payloads(rows) if p.startswith("a:xpt:")] == [TH.id], (text, rows)
    for bad in (TA1.id, TB1.id):
        press(HOST, HC, f"a:xpt:{bad}")
        assert bad not in bot.XP_DRAFT[HOST]["themes"] and "⛔" in last(HC)[1], bad
    press(HOST, HC, f"a:xpt:{TH.id}")
    press(HOST, HC, "a:xpnext")
    for bad in ("0", "200", "abc"):
        msg(HOST, HC, bad)
        assert bot.PENDING.get(HOST) == "xpiv" and not mine(bot.host_id()), bad
    msg(HOST, HC, "каждые 12 часов")
    camps = mine(bot.host_id())
    assert len(camps) == 1 and camps[0]["themes"] == [TH.id] and camps[0]["every"] == 12 * H, camps
    CAMP["host"] = camps[0]["id"]


@check
def c5():
    """своё расписание, а не очередь: у A в одном канале очередь пуста, в другом — полна готовых постов; кросс-промо
    выходит в оба сразу, очередь не тронута (ни один пост не вышел и не убран, пауза между постами не сдвинулась,
    счётчик опубликованного тот же); раньше интервала — не выходит; через 2 ч — снова; бот на паузе — всё равно
    выходит (остановить — кнопкой кампании); чужие каналы ничего не получили"""
    cid = CAMP["a"]
    with bot.theme_ctx(TA1):
        bot.QUEUE.clear()
        bot.PUB["last_pub"] = 111.0
    with bot.theme_ctx(TA2):
        bot.QUEUE[:] = [queue_item("q:1", "Обычный пост 1"), queue_item("q:2", "Обычный пост 2"),
                        queue_item("q:3", "Обычный пост 3")]
        bot.PUB["last_pub"] = 222.0
        before = [dict(x) for x in bot.QUEUE]
        pub0 = bot.STATS["published"]
    now = time.time()
    n0 = len(srv.messages)
    tick(now)
    for ch in (TA1.chat_id, TA2.chat_id):
        got = posts(ch, n0)
        assert len(got) == 1 and got[0]["body"].get("text", "").startswith(PROMO_A), (ch, got)
        assert {"type": "image", "payload": {"token": "imgA1"}} in got[0]["body"].get("attachments", []), got
    assert not any(posts(ch, n0) for ch in (TB1.chat_id, TC1.chat_id))
    with bot.theme_ctx(TA2):
        assert [dict(x) for x in bot.QUEUE] == before and bot.PUB["last_pub"] == 222.0
        assert bot.STATS["published"] == pub0, "кросс-промо — не пост из очереди"
    with bot.theme_ctx(TA1):
        assert list(bot.QUEUE) == [] and bot.PUB["last_pub"] == 111.0
    assert bot.XP.get(cid)["sent"] == 2
    n1 = len(srv.messages)
    tick(now + 2 * H - 60)
    assert not posts(TA1.chat_id, n1) and not posts(TA2.chat_id, n1), "раньше интервала — не выходит"
    with bot.theme_ctx(TA1):
        bot.CFG["paused"] = True                           # бот на паузе (очередь стоит)
    try:
        tick(now + 2 * H + 1)
        assert len(posts(TA1.chat_id, n1)) == 1 and len(posts(TA2.chat_id, n1)) == 1, "своё расписание"
    finally:
        with bot.theme_ctx(TA1):
            bot.CFG["paused"] = False
    assert bot.XP.get(cid)["sent"] == 4
    n2 = len(srv.messages)
    with bot.theme_ctx(TA2):                               # а обычные посты идут своим чередом, между кросс-промо
        bot.PUB["last_pub"] = 0
        asyncio.run(bot.publish_due(None, TA2.chat_id))
        assert len(bot.QUEUE) == 2, bot.QUEUE
    got = posts(TA2.chat_id, n2)
    assert len(got) == 1 and "Обычный пост" in got[0]["body"].get("text", ""), got


LOOP = asyncio.new_event_loop()
threading.Thread(target=LOOP.run_forever, daemon=True, name="xpromo-loop").start()


def on_loop(coro, timeout=20):
    return asyncio.run_coroutine_threadsafe(coro, LOOP).result(timeout)


async def _locks():
    for t in (TA1, TA2):
        t.lock = asyncio.Lock()


async def _main_tick(t):
    """Как планировщик темы: под замком темы — publish_due."""
    with bot.theme_ctx(t):
        async with t.lock:
            await bot.publish_due(None, t.chat_id)


@check
def c6():
    """живой цикл asyncio: планировщик темы A-новости выкладывает долгий пост из очереди (держит замок темы) — в
    это время кросс-промо в A-мемы выходит сразу (не ждёт чужую очередь), а в A-новости — сразу после обычного поста:
    посты в канале не перемешиваются и идут по очереди — обычный, потом кросс-промо"""
    on_loop(_locks())
    cid = CAMP["a"]
    gate, entered, order = threading.Event(), threading.Event(), []
    real = bot.send_max

    def slow_send(chat, text, imgs, video_path=None, file_path=None):
        order.append((chat, (text or "").split("\n")[0][:30]))
        if chat == TA1.chat_id and "Долгий" in (text or ""):
            entered.set()
            gate.wait(10)
        return real(chat, text, imgs, video_path, file_path) if file_path is not None else real(chat, text, imgs, video_path)
    bot.send_max = slow_send
    try:
        with bot.theme_ctx(TA1):
            bot.QUEUE[:] = [queue_item("q:long", "Долгий пост с видео")]
            bot.PUB["last_pub"] = 0
        n0 = len(srv.messages)
        fut = asyncio.run_coroutine_threadsafe(_main_tick(TA1), LOOP)
        assert entered.wait(5), "обычный пост начал уходить"
        due = bot.XP.get(cid)["next"]
        xp = asyncio.run_coroutine_threadsafe(bot.xpromo_tick(max(due.values()) + 1), LOOP)
        end = time.time() + 5
        while time.time() < end and not posts(TA2.chat_id, n0):
            time.sleep(0.02)
        assert len(posts(TA2.chat_id, n0)) == 1, "в другой канал — сразу, не ждёт чужой очереди"
        assert not posts(TA1.chat_id, n0), "в этот канал — только после обычного поста"
        gate.set()
        fut.result(10)
        xp.result(10)
        got = [m["body"].get("text", "") for m in posts(TA1.chat_id, n0)]
        assert len(got) == 2 and got[0].startswith("Долгий") and got[1].startswith(PROMO_A), got
    finally:
        gate.set()
        bot.send_max = real


@check
def c7():
    """в «📢 Кросс-промо» — список кампаний кабинета: у A — только его кампания (не хозяина и не чужие) с кнопками
    «⏸ Остановить» и «🗑 Удалить»; остановленная не выходит, даже когда пора; «▶️ Возобновить» — продолжает по
    своему расписанию; поддельные «остановить / возобновить / удалить» чужой кампании (A — кампанию хозяина, B —
    кампанию A) не действуют"""
    cid, hid = CAMP["a"], CAMP["host"]
    text, rows = screen_of(A, AC, "m:xp")
    assert f"a:xpstop:{cid}" in payloads(rows) and f"m:xpdel:{cid}" in payloads(rows), payloads(rows)
    assert hid not in " ".join(payloads(rows)) and "Промо хозяина" not in text, text
    stop = next(b for b in buttons(rows) if b.get("payload") == f"a:xpstop:{cid}")
    dele = next(b for b in buttons(rows) if b.get("payload") == f"m:xpdel:{cid}")
    assert stop["text"].startswith("⏸ Остановить") and dele["text"].startswith("🗑 Удалить"), (stop, dele)
    assert "каждые 2 ч" in text and "A-новости" in text and "A-мемы" in text, text
    text, rows = screen_of(HOST, HC, "m:xp")
    assert f"a:xpstop:{hid}" in payloads(rows) and cid not in " ".join(payloads(rows)), payloads(rows)
    for who, chat, target in ((A, AC, hid), (B_, BC, cid)):
        for act in ("a:xpstop:", "a:xpgo:", "a:xpdelok:"):
            press(who, chat, act + target)
        assert bot.XP.get(target) and bot.XP.get(target)["state"] == "on", (who, target)
        text, rows = screen_of(who, chat, f"m:xpdel:{target}")
        assert "a:xpdelok:" not in " ".join(payloads(rows)), (who, rows)
    press(A, AC, f"a:xpstop:{cid}")
    assert bot.XP.get(cid)["state"] == "paused"
    text, rows = screen_of(A, AC, "m:xp")
    assert f"a:xpgo:{cid}" in payloads(rows) and "⏸" in text, (text, payloads(rows))
    n0 = len(srv.messages)
    tick(time.time() + 30 * H)
    assert not posts(TA1.chat_id, n0) and not posts(TA2.chat_id, n0), "остановлена — не выходит"
    press(A, AC, f"a:xpgo:{cid}")
    c = bot.XP.get(cid)
    assert c["state"] == "on" and min(c["next"].values()) >= c["last"] + 2 * H - 1, c
    tick(min(c["next"].values()) + 1)
    assert len(posts(TA1.chat_id, n0)) == 1 and len(posts(TA2.chat_id, n0)) == 1


@check
def c8():
    """даже если в записи кампании оказалась чужая тема (правка файла, сбой) — пост уходит только в каналы своего
    кабинета, чужая тема из кампании убирается; у покупателя без VIP (хозяин снял) кампании не выходят, но и не
    пропадают — вернул VIP, выходят снова; кабинет выключен — не выходят; тема удалена — не падаем"""
    cid = CAMP["a"]
    with bot.XP.lock:
        bot.XP.items[cid]["themes"].append(TB1.id)
        bot.XP.items[cid]["next"][TB1.id] = 0
    now = max(bot.XP.get(cid)["next"].values()) + 1
    n0 = len(srv.messages)
    tick(now)
    assert not posts(TB1.chat_id, n0), "в чужой канал — ничего"
    assert len(posts(TA1.chat_id, n0)) == 1 and TB1.id not in bot.XP.get(cid)["themes"]
    press(HOST, HC, f"a:clvip:{KA}")                       # хозяин снял VIP
    assert not bot.TENANTS[KA].get("vip")
    n1 = len(srv.messages)
    tick(now + 3 * H)
    assert not posts(TA1.chat_id, n1) and bot.XP.get(cid), "без VIP — не выходит, но кампания цела"
    press(HOST, HC, f"a:clvip:{KA}")
    tick(now + 3 * H + 1)
    assert len(posts(TA1.chat_id, n1)) == 1, "VIP вернули — выходит"
    bot.TENANTS[KA]["enabled"] = False                     # хозяин выключил кабинет
    try:
        n2 = len(srv.messages)
        tick(now + 10 * H)
        assert not posts(TA1.chat_id, n2) and not posts(TA2.chat_id, n2)
    finally:
        bot.TENANTS[KA]["enabled"] = True
    tx = bot.create_theme("A-временная", KA)
    tx.chat_id = -7109
    with bot.XP.lock:
        bot.XP.items[cid]["themes"].append(tx.id)
        bot.XP.items[cid]["next"][tx.id] = 0
    bot.delete_theme(tx)
    tick(now + 20 * H)
    assert tx.id not in bot.XP.get(cid)["themes"] and not posts(-7109, n1)


@check
def c9():
    """сбои: MAX не пускает бота в один канал — во второй пост всё равно выходит, первый — повтор позже (не через
    весь интервал), владельцу — строка в журнале; MAX временно не отвечает — повтор через несколько минут; пост из
    двух частей (фото и видео), вторая не ушла — первая убрана из канала (без половинок), повтор позже"""
    cid = CAMP["a"]
    now = max(bot.XP.get(cid)["next"].values()) + 1
    srv.deny_chats.add(TA2.chat_id)
    try:
        n0 = len(srv.messages)
        tick(now)
        assert len(posts(TA1.chat_id, n0)) == 1 and not posts(TA2.chat_id, n0)
        nxt = bot.XP.get(cid)["next"]
        assert nxt[TA2.id] < now + 2 * H, ("повтор раньше интервала", nxt)
        with bot.theme_ctx(TA2):
            assert any("Кросс-промо" in j for j in bot.JOURNAL), list(bot.JOURNAL)[-5:]
    finally:
        srv.deny_chats.discard(TA2.chat_id)
        bot.DENIED.pop(TA2.chat_id, None)
        bot.DENY_N.pop(TA2.chat_id, None)
    srv.fail_messages = 1
    later = max(bot.XP.get(cid)["next"].values()) + 1
    bot.XP.later(cid, TA1.id, later)
    bot.XP.later(cid, TA2.id, later + 10 * H)
    n1 = len(srv.messages)
    tick(later)
    assert not posts(TA1.chat_id, n1)
    retry_at = bot.XP.get(cid)["next"][TA1.id]
    assert later < retry_at <= later + 30 * 60, (retry_at - later)
    tick(retry_at + 1)
    assert len(posts(TA1.chat_id, n1)) == 1, "после сбоя — вышло"
    two = bot.XP.add(KA, A, "Фото и видео", [{"kind": "image", "token": "i2"}, {"kind": "video", "token": "v2"}],
                     [TA1.id], 2 * H)
    real = bot.send_max

    def flaky(chat, text, imgs, video_path=None, file_path=None):
        if video_path:
            raise bot.MaxTemporary("MAX send failed with HTTP 502")
        return real(chat, text, imgs, video_path)
    bot.send_max = flaky
    try:
        n2 = len(srv.messages)
        tick(time.time())
        first = posts(TA1.chat_id, n2)
        assert len(first) == 1 and first[0]["mid"] in srv.deleted, (first, srv.deleted[-3:])
        assert bot.XP.get(two["id"])["sent"] == 0 and bot.XP.get(two["id"])["next"][TA1.id] > time.time()
    finally:
        bot.send_max = real
        bot.XP.delete(two["id"], KA)


@check
def c10():
    """свой пост кросс-промо с пометками рекламы («#реклама», «О рекламодателе») перехватчик РСЯ не трогает — пост
    из очереди «на сейчас» не ставится (кросс-промо не тратит места очереди); удалил его в канале руками — бот не
    учит на нём чёрный список спама"""
    ad = bot.XP.add(KA, A, "Наш партнёр #реклама\nО рекламодателе", [], [TA1.id], 2 * H)
    try:
        with bot.theme_ctx(TA1):
            bot.QUEUE[:] = [queue_item("q:r1", "Пост из очереди")]
        n0 = len(srv.messages)
        tick(time.time())
        got = posts(TA1.chat_id, n0)
        assert len(got) == 1, got
        mid = got[0]["mid"]
        bot.RSYA_LAST.clear()
        bot.handle_update({"update_type": "message_created", "message": {
            "sender": {"user_id": 999, "name": "Канал"}, "recipient": {"chat_id": TA1.chat_id, "chat_type": "channel"},
            "body": {"mid": mid, "text": "Наш партнёр #реклама\nО рекламодателе"}}})
        with bot.theme_ctx(TA1):
            assert not any(x.get("manual") or x.get("rsya") for x in bot.QUEUE), bot.QUEUE
        links0 = len(bot.SPAM_BLOCK["links"]) + len(bot.SPAM_BLOCK["words"])
        bot.handle_update({"update_type": "message_removed", "message_id": mid, "chat_id": TA1.chat_id})
        assert len(bot.SPAM_BLOCK["links"]) + len(bot.SPAM_BLOCK["words"]) == links0
    finally:
        bot.XP.delete(ad["id"], KA)
        with bot.theme_ctx(TA1):
            bot.QUEUE.clear()


@check
def c11():
    """«🗑 Удалить» — с подтверждением; «Нет» — кампания на месте; «Да» — её нет ни в списке, ни в файле, и она больше
    не выходит; кампании переживают перезапуск бота (файл xpromo.json с резервной копией); покупатель удалил
    кабинет (данные) — его кампании удалены"""
    hid = CAMP["host"]
    tick(time.time())
    text, rows = screen_of(HOST, HC, f"m:xpdel:{hid}")
    assert text.startswith("🗑") and f"a:xpdelok:{hid}" in payloads(rows) and "m:xp" in payloads(rows), (text, rows)
    press(HOST, HC, "m:xp")
    assert bot.XP.get(hid)
    reloaded = X.Campaigns(bot.XP_FILE, save=lambda p, d: None, load=bot.load_json_safe)
    assert reloaded.get(CAMP["a"]) == bot.XP.get(CAMP["a"]) and reloaded.get(hid) == bot.XP.get(hid), "перезапуск"
    assert os.path.exists(bot.XP_FILE + ".bak")
    press(HOST, HC, f"a:xpdelok:{hid}")
    assert bot.XP.get(hid) is None and not mine(bot.host_id())
    text, rows = screen_of(HOST, HC, "m:xp")
    assert hid not in " ".join(payloads(rows))
    assert hid not in (tio.rjson(bot.XP_FILE).get("items") or {})
    n0 = len(srv.messages)
    tick(time.time() + 100 * H)
    assert not posts(TH.chat_id, n0)
    bx = bot.XP.add(KB, B_, "Промо B", [], [TB1.id], H)
    assert bx and mine(KB)
    bot.legal_forget(KB)
    assert mine(KB) == [], "данные кабинета удалены — кампании тоже"


SOLO = r"""
import asyncio, importlib.util, json, os, sys, tempfile, time, types
ROOT = sys.argv[1]
sys.path.insert(0, ROOT)
from fake_max import FakeMax
tele = types.ModuleType("telethon"); tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, BOT_TOKEN="TESTTOKEN", OWNER_ID="1")
os.environ.pop("TGMAX_HOST", None)
os.chdir(tempfile.mkdtemp())
with open("config.json", "w") as f:
    json.dump({"max_token": "TESTTOKEN", "max_channel_link": ""}, f)
spec = importlib.util.spec_from_file_location("bot", os.path.join(ROOT, "tg_to_max.py"))
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
bot.S.trust_env = False
SENT = []
bot.api_send = lambda chat, text, rows=None, token=None, keep=False: SENT.append((chat, text, rows or [])) or "m"
bot.send_screen = lambda chat, text, rows=None: SENT.append((chat, text, rows or []))
bot.api_answer = lambda cid, text, rows=None: SENT.append((61000, text, rows or []))
def msg(uid, text):
    bot.handle_update({"update_type": "message_created", "message": {"sender": {"user_id": uid, "first_name": "U"},
        "recipient": {"chat_id": 61000 + uid, "chat_type": "dialog"}, "body": {"text": text}}})
def press(uid, payload):
    bot.handle_update({"update_type": "message_callback", "callback": {"callback_id": "c", "payload": payload,
        "user": {"user_id": uid}}, "message": {"recipient": {"chat_id": 61000 + uid, "chat_type": "dialog"}}})
def payloads():
    return [b.get("payload") for row in SENT[-1][2] for b in row]
bot.CFG["admins"].append(1)
bot.CFG["admin_chats"]["1"] = 61001
t0 = bot.THEMES[0]; t0.chat_id = -9001
t2 = bot.create_theme("Вторая"); t2.chat_id = -9002
out = {}
press(1, "m:main"); out["menu"] = "m:xp" in payloads()
press(1, "a:xpnew"); msg(1, "Промо бота")
out["picker"] = sorted(p.split(":", 2)[2] for p in payloads() if p.startswith("a:xpt:"))
press(1, "a:xpall"); press(1, "a:xpnext"); press(1, "a:xpiv:6")
out["camps"] = [(sorted(c["themes"]), c["every"]) for c in bot.XP.of_owner("")]
n0 = len(srv.messages)
asyncio.run(bot.xpromo_tick(time.time()))
out["sent"] = sorted(m["chat_id"] for m in srv.messages[n0:])
press(2, "a:xpnew"); msg(2, "чужая реклама")
out["stranger"] = [bot.PENDING.get(2), bot.xp_allowed(2), len(bot.XP.of_owner(""))]
print("RESULT " + json.dumps(out))
os._exit(0)
"""


@check
def c12():
    """обычный бот (не host): «📢 Кросс-промо» — в меню админа; все темы бота — его (выбор из своих каналов),
    кампания выходит в оба канала по своему таймеру; посторонний (не админ) не может начать кампанию"""
    import json
    import subprocess
    child = os.path.join(tempfile.mkdtemp(), "solo.py")
    tio.wfile(SOLO, child, "w")
    r = subprocess.run([sys.executable, child, ROOT], env=dict(os.environ), capture_output=True, text=True,
                       timeout=300, check=False)
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, (r.returncode, r.stdout[-1500:], r.stderr[-1500:])
    got = json.loads(line[7:])
    assert got["menu"] and got["picker"] == ["main", "t2"], got
    assert got["camps"] == [[["main", "t2"], 6 * H]] and got["sent"] == [-9002, -9001], got
    assert got["stranger"] == [None, False, 1], got


if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    os._exit(1)
print("ВСЕ ТЕСТЫ КРОСС-ПРОМО ПРОШЛИ")
os._exit(0)
