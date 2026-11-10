"""📊 Проверка канала по пересланному посту (или ссылке) — без привязки канала и без прав админа.
Публичный канал: «Вижу пост из канала: …. Анализирую...», затем «Подписалось: +X, Отписалось: -Y, Итого: +Z» —
бот сам копит число подписчиков (раз в час) и считает приток и отток. Закрытый канал: ровно одна фраза
«Это закрытый канал. …» — без ошибок. Канал попадает в «🕘 Недавние проверки»."""
import importlib.util
import json
import os
import re
import sys
import tempfile
import traceback
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from fake_max import FakeMax  # noqa: E402

try:
    from reloader.services.analytics import watch as W
except ImportError:                                     # модуля ещё нет — проверки скажут, чего не хватает
    W = None

CLOSED = "Это закрытый канал. Чтобы я мог его анализировать, добавь меня в администраторы этого канала."
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


# ---------- 1) чистая логика: откуда пост, страница t.me, приток/отток, «Недавние проверки» ----------
@check
def w1():
    """откуда пост: пересланный пост канала MAX (по chat_id), ссылки t.me / max.ru / web.max.ru; закрытые ссылки;
    сообщение человека и обычный текст — не проверка канала"""
    assert W is not None, "нет модуля reloader.services.analytics.watch"
    fwd = {"link": {"type": "forward", "chat_id": -7100, "message": {"text": "пост"}}}
    assert W.source_of(fwd, "") == {"kind": "max", "chat_id": -7100}
    person = {"link": {"type": "forward", "sender": {"user_id": 55}, "chat_id": 777, "message": {"text": "hi"}}}
    assert W.source_of(person, "") is None, "переслали сообщение человека — это не пост канала"
    assert W.source_of({"link": {"type": "reply", "chat_id": -1}}, "") is None
    assert W.source_of({"link": {"type": "forward", "chat_id": "x"}}, "") is None
    cases = {"https://t.me/robloxtg/123": {"kind": "tg", "username": "robloxtg", "post": 123},
             "t.me/RobloxTG": {"kind": "tg", "username": "robloxtg", "post": None},
             "https://telegram.me/s/robloxtg/55": {"kind": "tg", "username": "robloxtg", "post": 55},
             "https://t.me/+AbCdEf123": {"kind": "closed"}, "https://t.me/joinchat/AbCd": {"kind": "closed"},
             "https://t.me/c/1234567/89": {"kind": "closed"}, "https://max.ru/join/AbCdEf": {"kind": "closed"},
             "https://max.ru/robloxnews": {"kind": "maxlink", "slug": "robloxnews"},
             "max.ru/robloxnews/AZx1": {"kind": "maxlink", "slug": "robloxnews"},
             "https://web.max.ru/-7100": {"kind": "max", "chat_id": -7100}}
    for text, want in cases.items():
        assert W.source_of({}, text) == want, (text, W.source_of({}, text))
    for text in ("привет", "посмотри t.me/x и скажи", "", "/start", "https://example.com/page"):
        assert W.source_of({}, text) is None, text


@check
def w2():
    """публичная страница t.me: название и число подписчиков (и участников группы); нет числа — None"""
    assert W is not None
    page = ('<html><head><meta property="og:title" content="Roblox &amp; TG News"></head><body>'
            '<div class="tgme_page_title" dir="auto"><span dir="auto">Roblox &amp; TG News</span></div>'
            '<div class="tgme_page_extra">12 345 subscribers</div></body></html>')
    assert W.parse_tme(page) == {"title": "Roblox & TG News", "subs": 12345, "kind": "channel"}, W.parse_tme(page)
    ru = '<meta property="og:title" content="Новости"><div class="tgme_page_extra">1\xa0234\xa0567 подписчиков</div>'
    assert W.parse_tme(ru)["subs"] == 1234567
    grp = '<meta property="og:title" content="Чат"><div class="tgme_page_extra">8 900 members, 120 online</div>'
    assert W.parse_tme(grp) == {"title": "Чат", "subs": 8900, "kind": "group"}
    assert W.parse_tme('<div class="tgme_page_extra">@someone</div>')["subs"] is None
    assert W.parse_tme("") == {"title": None, "subs": None, "kind": None} and W.parse_tme(None)["subs"] is None


@check
def w3():
    """приток и отток по снимкам числа подписчиков; строка «Подписалось: +X, Отписалось: -Y, Итого: +Z»"""
    assert W is not None
    assert W.deltas([]) == (0, 0, 0) and W.deltas([(1, 1000)]) == (0, 0, 0)
    assert W.deltas([(1, 1000), (2, 1010), (3, 1005), (4, 1012)]) == (17, 5, 12)
    assert W.deltas([(1, 50), (2, 40)]) == (0, 10, -10)
    assert W.stats_line(17, 5, 12) == "Подписалось: +17, Отписалось: -5, Итого: +12"
    assert W.stats_line(0, 7, -7) == "Подписалось: +0, Отписалось: -7, Итого: -7"
    assert W.stats_line(1234, 0, 1234) == "Подписалось: +1 234, Отписалось: -0, Итого: +1 234"


@check
def w4():
    """«Недавние проверки»: свежие сверху, не больше 10, у каждого пользователя свои; снимки и названия переживают
    перезапуск; испорченный файл — не падаем"""
    assert W is not None
    d = tempfile.mkdtemp()
    path = os.path.join(d, "channel_checks.json")
    save = lambda p, data, **k: json.dump(data, open(p, "w"), ensure_ascii=False)
    load = lambda p, default: json.load(open(p)) if os.path.exists(p) else default
    clock = [1000.0]
    w = W.Watch(path, save=save, load=load, clock=lambda: clock[0])
    for i in range(12):
        clock[0] += 60
        w.remember("7", f"max:{-i}", {"title": f"Канал {i}", "link": ""})
    w.remember("8", "tg:robloxtg", {"title": "TG", "link": "https://t.me/robloxtg"})
    got = [r["key"] for r in w.recent("7")]
    assert got[0] == "max:-11" and len(got) == 10 and "max:0" not in got, got
    assert [r["key"] for r in w.recent("8")] == ["tg:robloxtg"]
    for n in (1000, 1010, 1010, 1005):
        clock[0] += 3600
        w.snap("tg:robloxtg", n)
    assert w.deltas("tg:robloxtg") == (10, 5, 5) and w.last("tg:robloxtg") == 1005
    w.save()
    w2_ = W.Watch(path, save=save, load=load, clock=lambda: clock[0])
    assert w2_.deltas("tg:robloxtg") == (10, 5, 5) and w2_.recent("8")[0]["title"] == "TG"
    assert "tg:robloxtg" in w2_.watched(), "в «Недавних» — значит, бот сам обновляет число подписчиков"
    open(path, "w").write('{"channels": [1, 2], "recent": "x"')                   # испорченный файл
    assert W.Watch(path, save=save, load=load).recent("7") == []


# ---------- 2) бот в host-режиме: поддельный MAX, пересылка поста покупателем и хозяином ----------
tele = types.ModuleType("telethon")
tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele
sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, BOT_TOKEN="TESTTOKEN", TGMAX_HOST="1", OWNER_ID="1")
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ADMIN_IDS"):
    os.environ.pop(k, None)
os.chdir(tempfile.mkdtemp())
json.dump({"max_token": "TESTTOKEN", "max_channel_link": ""}, open("config.json", "w"))
spec = importlib.util.spec_from_file_location("bot", os.path.join(ROOT, "tg_to_max.py"))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
bot.S.trust_env = False
bot.GCFG["trial_days"] = 0

HOST, HC, BUYER, BC = 1, 61000, 5001, 62000
PUB_ID, CLOSED_ID = -7100, -7200
srv.chats["robloxnews"] = PUB_ID                        # публичный канал: бот в нём НЕ админ и не привязан
srv.subs[PUB_ID] = 1000
TME = {"https://t.me/robloxtg": ('<meta property="og:title" content="Roblox TG News">'
                                 '<div class="tgme_page_extra">12 345 subscribers</div>')}


def fake_web(url):
    if url in TME:
        return TME[url]
    raise RuntimeError("t.me ответил 404")


bot.web_fetch = fake_web


def msg(uid, chat, text, link=None):
    m = {"sender": {"user_id": uid, "first_name": f"U{uid}"}, "recipient": {"chat_id": chat, "chat_type": "dialog"},
         "body": {"text": text}}
    if link:
        m["link"] = link
    bot.handle_update({"update_type": "message_created", "message": m})


def forward(uid, chat, cid, sender=None):
    link = {"type": "forward", "chat_id": cid, "message": {"text": "пост канала", "mid": "orig"}}
    if sender:
        link["sender"] = {"user_id": sender, "name": "Кто-то"}
    msg(uid, chat, "", link)


def press(uid, chat, payload):
    bot.handle_update({"update_type": "message_callback", "callback": {
        "callback_id": "c", "payload": payload, "user": {"user_id": uid, "first_name": f"U{uid}"}},
        "message": {"recipient": {"chat_id": chat}}})


def said(chat, n0):
    return [m for m in srv.messages[n0:] if m["chat_id"] == chat]


def texts(chat, n0):
    return [m["body"].get("text") or "" for m in said(chat, n0)]


def answered(n0):
    return [a["body"].get("message", {}) for a in srv.answers[n0:]]


msg(HOST, HC, "/start")
msg(BUYER, BC, "/start")
KEY = bot.tenant_key(BUYER)
bot.host_extend(KEY, 30)
assert not srv.chat_admins.get(PUB_ID), "бот не админ публичного канала"


@check
def f1():
    """покупатель переслал пост из ПУБЛИЧНОГО канала (бот не админ, канал не привязан): «Вижу пост из канала:
    robloxnews. Анализирую...», затем подписчики и «Подписалось: +0, Отписалось: -0, Итого: +0»"""
    n0 = len(srv.messages)
    forward(BUYER, BC, PUB_ID)
    got = texts(BC, n0)
    assert len(got) == 2, got
    assert got[0] == "Вижу пост из канала: robloxnews. Анализирую...", got[0]
    assert "Подписалось: +0, Отписалось: -0, Итого: +0" in got[1] and "1 000" in got[1], got[1]
    assert not any(t.chat_id == PUB_ID for t in bot.THEMES), "канал не привязывается"


@check
def f2():
    """бот сам следит за каналом (раз в час): подписчиков стало 1010, потом 1004 — при следующей проверке
    «Подписалось: +10, Отписалось: -6, Итого: +4»"""
    srv.subs[PUB_ID] = 1010
    bot.fwd_snapshot_all()
    srv.subs[PUB_ID] = 1004
    bot.fwd_snapshot_all()
    n0 = len(srv.messages)
    forward(BUYER, BC, PUB_ID)
    got = texts(BC, n0)
    assert got and got[-1].count("Подписалось: +10, Отписалось: -6, Итого: +4") == 1, got
    assert "1 004" in got[-1], got[-1]


@check
def f3():
    """ЗАКРЫТЫЙ канал (MAX не показывает его боту): ровно одно сообщение — точная фраза, без кнопок и ошибок"""
    n0 = len(srv.messages)
    forward(BUYER, BC, CLOSED_ID)
    sent = said(BC, n0)
    assert [m["body"].get("text") for m in sent] == [CLOSED], [m["body"].get("text") for m in sent]
    assert not sent[0]["body"].get("attachments"), sent[0]["body"]


@check
def f4():
    """ссылка на публичный канал Telegram — подписчики со страницы t.me; закрытая ссылка t.me/+… — точная фраза"""
    n0 = len(srv.messages)
    msg(BUYER, BC, "https://t.me/robloxtg/123")
    got = texts(BC, n0)
    assert got[:1] == ["Вижу пост из канала: Roblox TG News. Анализирую..."], got
    assert "12 345" in got[1] and "Подписалось: +0, Отписалось: -0, Итого: +0" in got[1], got
    n1 = len(srv.messages)
    msg(BUYER, BC, "https://t.me/+AbCdEf123")
    assert texts(BC, n1) == [CLOSED], texts(BC, n1)


@check
def f5():
    """«🕘 Недавние проверки» (/recent): проверенные каналы — свежие сверху, закрытый с замком; кнопка — проверить
    снова; чужой ключ в кнопке — не проверяем"""
    n0 = len(srv.answers), len(srv.messages)
    msg(BUYER, BC, "/recent")
    screen = texts(BC, n0[1])[-1]
    assert screen.startswith("🕘 Недавние проверки"), screen
    order = [screen.find(x) for x in ("Roblox TG News", "robloxnews")]
    assert -1 not in order and order == sorted(order), screen
    assert "🔒" in screen, screen
    rows = json.dumps(said(BC, n0[1])[-1]["body"].get("attachments"), ensure_ascii=False)
    assert f"fw:r:max:{PUB_ID}" in rows and "fw:r:tg:robloxtg" in rows, rows
    n1 = len(srv.messages)
    srv.subs[PUB_ID] = 1020
    press(BUYER, BC, f"fw:r:max:{PUB_ID}")
    got = texts(BC, n1)
    assert any("Подписалось: +26, Отписалось: -6, Итого: +20" in t for t in got), got
    n2 = len(srv.messages)
    press(BUYER, BC, "fw:r:max:-9999")                  # не из его списка — ничего не проверяем
    assert not any("Подписалось" in t for t in texts(BC, n2)), texts(BC, n2)
    assert "max:-9999" not in [r["key"] for r in bot.FWD_WATCH.recent(str(BUYER))]
    assert "🕘 Недавние проверки" in json.dumps(bot.screen("anl", HOST)[1], ensure_ascii=False) \
        or not bot.feature_on("stats"), "кнопка в «📊 Аналитика»"


@check
def f6():
    """хозяин переслал пост канала — тоже проверка канала (а не «этот человек не заходил в бота»); пересланное
    сообщение человека — по-старому; MAX не ответил — без «закрытый канал» и без ошибки, бот работает"""
    n0 = len(srv.messages)
    forward(HOST, HC, PUB_ID)
    got = texts(HC, n0)
    assert got and got[0] == "Вижу пост из канала: robloxnews. Анализирую...", got
    assert not any("не заходил" in t for t in got), got
    n1 = len(srv.messages)
    forward(BUYER, BC, 777, sender=9999)                 # переслали сообщение человека — не проверка канала
    assert not any(t.startswith("Вижу пост") or t == CLOSED for t in texts(BC, n1)), texts(BC, n1)
    real_get = bot.S.get
    bot.S.get = lambda url, *a, **k: (_ for _ in ()).throw(bot.requests.exceptions.ConnectionError("down")) \
        if "/chats/" in url else real_get(url, *a, **k)
    n2 = len(srv.messages)
    try:
        forward(BUYER, BC, PUB_ID)
    finally:
        bot.S.get = real_get
    got = texts(BC, n2)
    assert len(got) == 1 and got[0] != CLOSED and "ещё раз" in got[0], got
    assert not re.search(r"Traceback|ConnectionError|Exception|⚠️", got[0]), got[0]
    n3 = len(srv.messages)
    forward(BUYER, BC, PUB_ID)                           # MAX ожил — всё как обычно
    assert texts(BC, n3)[:1] == ["Вижу пост из канала: robloxnews. Анализирую..."], texts(BC, n3)


if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    os._exit(1)
print("ВСЕ ТЕСТЫ ПРОВЕРКИ КАНАЛОВ ПРОШЛИ")
os._exit(0)
