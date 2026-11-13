"""🧹 Чистка меню: тайника больше нет — ни кнопок, ни упоминаний в меню, справке и инструкции; /stash, /add, /done,
/stashnow — как любая незнакомая команда; фото без команды никуда не сохраняется; старый stash.json с прошлой версии
не публикуется. У покупателя в «⚙️ Ещё настройки» нет «🎭 Темы» — каналы открываются из главного меню
(«🎭 Мои каналы»). Бот в host-режиме: поддельный MAX, настоящие нажатия кнопок."""
import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import asyncio
import glob
import importlib.util
import os
import re
import sys
import tempfile
import time
import traceback
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from fake_max import FakeMax  # noqa: E402

WORDS = re.compile(r"stash|тайник|🗃", re.I)
OLD_PAYLOADS = re.compile(r"^(m:sh[lc]?$|a:sh(on|mode|ev|del|clr|now)\b|p:(stashadd|shhours)$|sf:)")
FAILS = []


def check(fn):
    """Каждая проверка — отдельно: красная не мешает увидеть остальные."""
    try:
        fn()
        print(f"{fn.__name__} ok: {fn.__doc__}")
    except AssertionError as e:
        FAILS.append(fn.__name__)
        line = traceback.extract_tb(e.__traceback__)[-1]
        print(f"{fn.__name__} FAIL (строка {line.lineno}: {line.line}) — AssertionError: {str(e)[:400]}")
    return fn


# ---------- бот в host-режиме; в папке — тема хозяина и её тайник с прошлой версии (stash_t9.json) ----------
tele = types.ModuleType("telethon")
tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele
sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, YK_API_URL=srv.url, BOT_TOKEN="TESTTOKEN", TGMAX_HOST="1", OWNER_ID="1")
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ADMIN_IDS", "YOOKASSA_SHOP_ID",
          "YOOKASSA_SECRET_KEY"):
    os.environ.pop(k, None)
os.chdir(tempfile.mkdtemp())
tio.wjson({"max_token": "TESTTOKEN", "max_channel_link": ""}, "config.json", "w")
LEGACY_ITEM = {"id": "s1", "text": "мем из старого тайника", "photos": [], "videos": [], "files": [],
               "added": time.time() - 86400, "by": 1, "sent": 0, "fails": 0}
tio.wjson({"themes": [{"id": "t9", "name": "Старая тема", "link": "", "chat_id": -7001, "own": {"night_on": False},
                        "legacy": False, "owner": "1"}], "active": {"1": "t9"}, "last_report": ""}, "themes.json", "w", _dump=dict(ensure_ascii=False))
tio.wjson([LEGACY_ITEM], "stash_t9.json", "w", _dump=dict(ensure_ascii=False))
spec = importlib.util.spec_from_file_location("bot", os.path.join(ROOT, "tg_to_max.py"))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
bot.S.trust_env = False
bot.GCFG["trial_days"] = 0
SENT, CUR_CHAT = [], [None]


def _send(chat, text, rows=None, token=None):
    SENT.append((chat, text or "", rows or []))
    return f"m{len(SENT)}"


bot.api_send = _send
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
        "message": {"recipient": {"chat_id": chat}}})


def last(chat):
    return next(e for e in reversed(SENT) if e[0] == chat)


def buttons(rows):
    return [b for row in rows for b in row]


def payloads(rows):
    return [b.get("payload") or "" for b in buttons(rows)]


def replies(uid, chat, text, atts=None):
    n = len(SENT)
    msg(uid, chat, text, atts)
    return [e for e in SENT[n:] if e[0] == chat]


def no_stash(where, text, rows):
    """Ни слова о тайнике ни в тексте, ни на кнопках; ни одной кнопки старого тайника."""
    assert not WORDS.search(text), (where, WORDS.search(text).group(), text[:600])
    for b in buttons(rows):
        assert not WORDS.search(b.get("text") or ""), (where, b)
        assert not OLD_PAYLOADS.match(b.get("payload") or ""), (where, b)


SKIP = {"dg", "anl"}          # проверка связи и аналитика ходят в сеть — их кнопки проверяем, сами экраны нет


def crawl(uid, chat, starts):
    """Обходит все экраны, до которых можно дойти кнопками меню, — настоящими нажатиями."""
    seen, todo, pages = set(), list(starts), []
    while todo:
        name = todo.pop(0)
        if name in seen or name.split(":")[0] in SKIP:
            continue
        seen.add(name)
        press(uid, chat, "m:" + name)
        _, text, rows = last(chat)
        pages.append((name, text, rows))
        todo += [p[2:] for p in payloads(rows) if p.startswith("m:") and p[2:] not in seen]
    return pages


HOST, HC, BUYER, BC = 1, 61000, 5001, 62000
msg(HOST, HC, "/start")
msg(BUYER, BC, "/start")
KEY = bot.tenant_key(BUYER)
assert KEY and bot.HOST_CHAT["chat"] == HC and bot.theme_by_id("t9") in bot.tenant_themes(bot.host_id())
bot.host_extend(KEY, 30)                                  # платный тариф: раньше там был и тайник
assert bot.tenant_mode(KEY) == "run"
bot.ensure_host_cabinet()                                 # кабинет хозяина работает — его темы публикуют
bot.host_extend(bot.host_id(), 30)
assert bot.theme_live(bot.theme_by_id("t9")) and [t.id for t in bot.tenant_themes(bot.host_id())] == ["t9"]


# раньше всех: пока старый тайник темы не тронут другими проверками
@check
def n8():
    """тайник прошлой версии (stash_t9.json): бот его не публикует, не считает в «ближайший час», не упоминает в 📭"""
    t = bot.theme_by_id("t9")
    assert t is not None and t.owner == "1" and t.chat_id == -7001, t
    with bot.theme_ctx(t):
        bot.CFG["night_on"] = False
        bot.CFG["paused"] = False
        bot.PUB["last_pub"] = 0
        n0 = len(srv.messages)
        asyncio.run(bot.publish_due(None, t.chat_id))
        assert len(srv.messages) == n0, srv.messages[n0:]
        assert bot.upcoming_posts(time.time()) == 0
        bot.START = time.time() - 600
        t.flags["empty_at"] = 0
        n1_ = len(SENT)
        bot.check_empty(time.time())
        told = [e for e in SENT[n1_:] if "📭" in e[1]]
        assert told, SENT[n1_:]
        for _, text, rows in told:
            no_stash("📭 предупреждение", text, rows)
    assert tio.rjson("stash_t9.json") == [LEGACY_ITEM], "старый файл не трогаем"


@check
def n1():
    """у покупателя в главном меню — «🎭 Мои каналы»: открывает список всех его каналов"""
    press(BUYER, BC, "m:main")
    _, text, rows = last(BC)
    mine = [b for b in buttons(rows) if b.get("payload") == "m:th"]
    assert mine and mine[0]["text"] == "🎭 Мои каналы", rows
    press(BUYER, BC, "m:th")
    _, text, rows = last(BC)
    names = [t.name for t in bot.tenant_themes(KEY)]
    assert names and all(n in text for n in names) and "p:themenew" in payloads(rows), (text, rows)
    no_stash("m:th", text, rows)


@check
def n2():
    """«⚙️ Ещё настройки» покупателя — без «🎭 Темы» (и без строки о ней); всё остальное на месте"""
    press(BUYER, BC, "m:more")
    _, text, rows = last(BC)
    assert text.startswith("⚙️ Ещё настройки"), text
    assert "m:th" not in payloads(rows) and not any("Темы" in (b.get("text") or "") for b in buttons(rows)), rows
    assert "🎭 Темы" not in text, text
    for keep in ("m:vid", "m:dd", "m:fl", "m:ad", "m:lg", "m:ai", "m:hp", "m:sg", "m:q", "m:ap", "m:gd", "m:terms",
                 "a:pause", "m:main"):
        assert keep in payloads(rows), (keep, payloads(rows))


@check
def n3():
    """покупатель: ни на одном экране меню (справка, инструкция, условия, каналы…) нет тайника"""
    pages = crawl(BUYER, BC, ["main", "more", "hp", "gd", "th", "thm", "terms", "legal", "privacy"])
    assert len(pages) >= 15, [p[0] for p in pages]
    for name, text, rows in pages:
        no_stash("покупатель m:" + name, text, rows)


@check
def n4():
    """хозяин: панель кабинетов и его полное меню — без тайника; «🎭 Темы» и «❓ Команды» у хозяина остались"""
    press(HOST, HC, "m:home")
    _, text, rows = last(HC)
    assert "m:th" in payloads(rows) and "m:hp" in payloads(rows), rows
    pages = crawl(HOST, HC, ["cl", "home", "hp", "th", "thm", f"clc:{KEY}"])
    assert len(pages) >= 25, [p[0] for p in pages]
    for name, text, rows in pages:
        no_stash("хозяин m:" + name, text, rows)


@check
def n5():
    """/stash, /add, /stashadd, /done, /stashnow — ответ как на незнакомую команду; в подсказке MAX и в справке их нет"""
    for uid, chat in ((BUYER, BC), (HOST, HC)):
        unknown = [e[1] for e in replies(uid, chat, "/qwertyzz")]
        assert unknown, "на незнакомую команду бот отвечает"
        for cmd in ("/stash", "/add", "/stashadd", "/done", "/stashnow"):
            got = [e[1] for e in replies(uid, chat, cmd)]
            assert got == unknown, (uid, cmd, got[-1][:200] if got else got)
            assert bot.PENDING.get(uid) is None, (uid, cmd, bot.PENDING.get(uid))
    sent = []
    real_patch = bot.S.patch
    bot.S.patch = lambda url, **kw: sent.append(kw.get("json")) or real_patch(url, **kw)
    try:
        bot.register_commands()
    finally:
        bot.S.patch = real_patch
    names = {c["name"] for c in sent[0]["commands"]}
    assert {"menu", "help", "queue", "now"} <= names, names
    assert not names & {"stash", "add", "stashadd", "done", "stashnow"}, names
    for uid in (BUYER, HOST):
        help_ = bot.help_text(uid)
        assert not re.search(r"/(stash|add|done|stashnow)\b", help_), help_


@check
def n6():
    """фото или видео без команды — никуда не сохраняется: в ответ главное меню, файлов тайника нет"""
    photo = [{"type": "image", "payload": {"token": "tok-photo-1"}}]
    video = [{"type": "video", "payload": {"token": "tok-video-1"}}]
    for uid, chat in ((BUYER, BC), (HOST, HC)):
        for atts, caption in ((photo, ""), (photo, "подпись"), (video, "")):
            got = replies(uid, chat, caption, atts)
            assert got, (uid, "бот ответил")
            for _, text, rows in got:
                no_stash(f"{uid}: фото без команды", text, rows)
    assert not os.path.isdir("stash_media"), os.listdir("stash_media")
    assert sorted(glob.glob("stash*.json")) == ["stash_t9.json"], glob.glob("stash*")   # только старый файл
    assert tio.rjson("stash_t9.json") == [LEGACY_ITEM], "старый файл не трогаем"


@check
def n7():
    """старые кнопки тайника в чатах — обычный экран меню, без тайника и без режима ввода"""
    tid = bot.tenant_themes(KEY)[0].id
    for uid, chat in ((BUYER, BC), (HOST, HC)):
        for p in ("m:sh", "m:shl", "m:shc", "a:shon", "a:shmode:every", "a:shev:1", "a:shdel:0", "a:shclr",
                  "a:shnow", "p:stashadd", "p:shhours", f"sf:{tid}"):
            press(uid, chat, p)
            _, text, rows = last(chat)
            no_stash(f"{uid}: старая кнопка {p}", text, rows)
            assert bot.PENDING.get(uid) is None, (uid, p, bot.PENDING.get(uid))
    got = replies(BUYER, BC, "", [{"type": "image", "payload": {"token": "tok-photo-2"}}])
    assert got and not os.path.isdir("stash_media"), got


@check
def n9():
    """в коде бота, пакете reloader и инструкциях (README, SERVER) — ни строчки о тайнике"""
    files = [os.path.join(ROOT, "tg_to_max.py"), os.path.join(ROOT, "README.txt"), os.path.join(ROOT, "SERVER.txt")]
    files += glob.glob(os.path.join(ROOT, "reloader", "**", "*.py"), recursive=True)
    left = [(os.path.relpath(f, ROOT), i + 1, line.strip()[:90]) for f in files
            for i, line in enumerate(tio.rfile(f, encoding="utf-8").splitlines()) if WORDS.search(line)]
    assert not left, (len(left), left[:8])
    for name in ("STASH", "STASH_FILE", "STASH_DIR", "publish_stash", "stash_add", "stash_remove", "stash_due",
                 "maybe_stash", "save_stash", "stash_prompt", "stash_fill_rows", "stash_mode_text"):
        assert not hasattr(bot, name), name
    assert not any(hasattr(t, "stash") for t in bot.THEMES)


if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    os._exit(1)
print("ВСЕ ТЕСТЫ ЧИСТКИ МЕНЮ ПРОШЛИ")
os._exit(0)
