"""📎 Файлы в постах Telegram → MAX: медленный Telegram, сбои MAX (500 на загрузке, 502 на сообщении, «принял, но
потерял вложение») — бот молча повторяет и пост выходит ЦЕЛИКОМ, с файлом. Не вышло после повторов — пост не выходит
вовсе (никакого «огрызка» без файла), точная причина — только в журнале админа и в bot.log. Покупателю — ни слова:
ни в чат, ни в его «📜 Журнал». Бот в host-режиме, настоящие запросы к поддельному MAX."""
import asyncio
import importlib.util
import json
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

FAILS = []


def reset_faults():
    """Каждая проверка — с исправного MAX: сбои одной не перетекают в другую."""
    srv.fail_uploads.update(image=0, video=0, file=0)
    srv.fail_messages = srv.drop_attachments_n = 0
    srv.drop_attachments = False


def check(fn):
    try:
        reset_faults()
        fn()
        print(f"{fn.__name__} ok: {fn.__doc__}")
    except AssertionError as e:
        FAILS.append(fn.__name__)
        line = traceback.extract_tb(e.__traceback__)[-1]
        print(f"{fn.__name__} FAIL (строка {line.lineno}: {line.line}) — AssertionError: {str(e)[:500]}")
    return fn


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
bot.VIDEO_RETRY_PAUSES[:] = [0.05, 0.05, 0.05]        # повторы скачивания из Telegram — без долгих пауз в тесте
bot.MAX_RETRY_PAUSES = [0.05, 0.05, 0.05]              # повторы загрузки и отправки в MAX
bot.DL_TIMEOUT_MIN = 1.0                               # одна попытка скачать маленький файл — не дольше ~1 с


def msg(uid, chat, text):
    bot.handle_update({"update_type": "message_created", "message": {
        "sender": {"user_id": uid, "first_name": f"U{uid}"}, "recipient": {"chat_id": chat, "chat_type": "dialog"},
        "body": {"text": text}}})


HOST, HC, BUYER, BC, CHAN = 1, 61000, 5001, 62000, 7001
msg(HOST, HC, "/start")
msg(BUYER, BC, "/start")
KEY = bot.tenant_key(BUYER)
bot.host_extend(KEY, 30)
T = bot.tenant_themes(KEY)[0]
T.chat_id = CHAN
with bot.theme_ctx(T):
    for k_, v_ in (("approval_on", False), ("night_on", False), ("dedupe_on", False)):
        bot.CFG[k_] = v_
assert bot.theme_live(T)

# ---------- Telegram: сообщения с фото и PDF; у каждой попытки скачать — свой сценарий ----------
JPG = b"\xff\xd8\xff\xe0" + b"j" * 6000
PDF = b"%PDF-1.4 " + b"schema " * 700                   # маленький файл (~5 КБ)


class Doc:
    def __init__(self, size, mime):
        self.size, self.mime_type, self.attributes = size, mime, []


class TMsg:
    def __init__(self, mid, kind, plan=()):
        self.id, self.text, self.grouped_id = mid, "", None
        self.date = None
        self.photo = object() if kind == "photo" else None
        self.data = JPG if kind == "photo" else PDF
        self.document = Doc(len(PDF), "application/pdf") if kind == "doc" else None
        self.file = types.SimpleNamespace(name="схема вязания.pdf" if kind == "doc" else None,
                                          ext=".pdf" if kind == "doc" else ".jpg")
        self.video = self.gif = self.video_note = self.sticker = self.fwd_from = None
        self.plan, self.calls = list(plan), 0

    async def download_media(self, file=None):
        self.calls += 1
        step = self.plan.pop(0) if self.plan else "ok"
        if step == "reset":
            raise ConnectionError("Connection reset by peer")
        if step == "hang":                              # Telegram «завис» — дольше, чем ждём одну попытку
            await asyncio.sleep(4)
        if step == "slow":                              # медленно, но скачалось
            await asyncio.sleep(0.4)
        with open(file, "wb") as f:
            f.write(self.data)
        return file


class TG:
    def __init__(self):
        self.msgs, self.down = {}, False

    def add(self, m):
        self.msgs[m.id] = m
        return m

    async def get_messages(self, ch, min_id=0, limit=30, ids=None):
        if self.down:
            raise ConnectionError("Network is unreachable")
        return self.msgs.get(ids) if ids is not None else []


TGC = TG()
SEQ = [0]


def post(photo=None, doc=None, text="Схема свитера: фото и PDF внутри"):
    SEQ[0] += 1
    it = {"id": f"{T.prefix}knit:{SEQ[0]}", "ch": "knit", "date": time.time(), "enq": time.time(), "text": text,
          "photos": [], "pids": [], "vids": [], "docs": [], "sent": 0, "fails": 0, "urgent": False, "status": "ok"}
    if photo is not None:                               # фото не на диске — бот скачает его перед публикацией
        it["photos"], it["pids"] = [f"{bot.MEDIA_DIR}/{T.fs}knit_{photo.id}.jpg"], [photo.id]
    if doc is not None:
        it["docs"] = [bot.doc_info(doc)]
    with bot.theme_ctx(T):
        bot.QUEUE.append(it)
        bot.save_queue()
    return it


def publish(it):
    with bot.theme_ctx(T):
        return asyncio.run(bot.publish_item(TGC, CHAN, it))


def channel(n0):
    """Что сейчас видно в канале из отправленного после n0 (удалённое ботом — не видно)."""
    return [m for m in srv.messages[n0:] if m["chat_id"] == CHAN and m["mid"] not in srv.deleted]


def to_people(n0):
    """Всё, что бот написал людям (покупателю и хозяину) после n0."""
    return [m for m in srv.messages[n0:] if m["chat_id"] in (BC, HC)]


def journal_of(uid):
    if uid == BUYER:
        with bot.theme_ctx(T):
            return bot.screen("lg", uid)[0]
    return bot.screen("lg", uid)[0]


ERRORS = re.compile(r"Skipped|не удал|не скач|не загруз|ошибк|сбой|HTTP|timed out|попытк|повтор|Connection|"
                    r"вложени", re.I)


def bot_log():
    with open(bot.LOG_FILE, encoding="utf-8") as f:
        return f.read()


@check
def m1():
    """медленный Telegram (фото «зависло» — повтор; PDF оборвался — повтор) и капризный MAX (500 на загрузке, 502 на
    сообщении, «принял, но потерял вложение») — пост выходит целиком, с фото и PDF; покупателю — ни слова"""
    photo = TGC.add(TMsg(101, "photo", ["hang", "slow"]))
    doc = TGC.add(TMsg(102, "doc", ["reset", "slow"]))
    it = post(photo, doc)
    srv.fail_uploads.update(image=1, file=2)            # фото: 500 один раз; PDF: обоими способами загрузки — 500
    srv.fail_messages = 1                                # первое сообщение — 502 (MAX перегружен)
    srv.drop_attachments_n = 1                           # следующее — принято, но без фото
    n0, d0 = len(srv.messages), len(srv.deleted)
    res = publish(it)
    shown = channel(n0)
    assert res == "ok", (res, [j for j in list(bot.JOURNAL)[-4:]])
    assert [[a["type"] for a in m["body"].get("attachments") or []] for m in shown] == [["image"], ["file"]], shown
    assert shown[0]["body"]["text"].startswith("Схема свитера"), shown[0]["body"]
    tok = shown[1]["body"]["attachments"][0]["payload"]["token"]
    assert srv.files[tok] == ("схема вязания.pdf", PDF), srv.files.get(tok, ("?", b""))[0]
    assert len(srv.deleted) == d0 + 1, "сообщение без фото бот убрал из канала и отправил заново"
    assert photo.calls == 2 and doc.calls == 2, (photo.calls, doc.calls)
    assert not to_people(n0), to_people(n0)
    assert not ERRORS.search(journal_of(BUYER).split("\n", 2)[-1]), journal_of(BUYER)
    log = bot_log()
    for want in ("File download timed out", "Connection reset by peer", "MAX upload failed with HTTP 500",
                 "HTTP 502", "dropped the attachment"):
        assert want in log, (want, log[-1500:])


@check
def m2():
    """Telegram недоступен совсем — 4 попытки, пост не выходит; бот не падает; в журнале админа — точная причина;
    покупателю — ни слова (ни в чат, ни в его журнал)"""
    TGC.down = True
    try:
        it = post(TGC.add(TMsg(201, "photo")), TGC.add(TMsg(202, "doc")))
        n0 = len(srv.messages)
        res = publish(it)
    finally:
        TGC.down = False
    assert res == "dropped" and not channel(n0), (res, channel(n0))
    assert not to_people(n0), to_people(n0)
    host_j = journal_of(HOST)
    assert "Skipped: download failed" in host_j and "Network is unreachable" in host_j, host_j[-800:]
    assert "4 attempts" in host_j, host_j[-800:]
    assert "Skipped" not in journal_of(BUYER) and "Network is unreachable" not in journal_of(BUYER)


@check
def m3():
    """MAX не принимает загрузку PDF (500 всё время) — пост не выходит вовсе: раньше в канале оставались текст и фото
    без файла; в журнале админа — «MAX upload failed with HTTP 500»; покупателю — ни слова"""
    it = post(TGC.add(TMsg(301, "photo")), TGC.add(TMsg(302, "doc")))
    srv.fail_uploads.update(image=0, file=99)
    n0 = len(srv.messages)
    try:
        res = publish(it)
    finally:
        srv.fail_uploads.update(file=0)
    assert res == "dropped" and not channel(n0), (res, channel(n0))
    assert not to_people(n0), to_people(n0)
    host_j = journal_of(HOST)
    assert "Skipped: upload failed" in host_j and "MAX upload failed with HTTP 500" in host_j, host_j[-800:]
    assert "Skipped" not in journal_of(BUYER), journal_of(BUYER)


@check
def m4():
    """MAX лежит (502 на каждое сообщение) — пост с файлом не выходит частями, бот не падает; MAX ожил — следующий
    пост выходит как обычно; покупателю — ни слова"""
    it = post(TGC.add(TMsg(401, "photo")), TGC.add(TMsg(402, "doc")))
    srv.fail_messages = 99
    n0 = len(srv.messages)
    try:
        res = publish(it)
    finally:
        srv.fail_messages = 0
    assert res == "dropped" and not channel(n0), (res, channel(n0))
    assert not to_people(n0), to_people(n0)
    assert "HTTP 502" in journal_of(HOST), journal_of(HOST)[-800:]
    ok = post(text="Обычный пост после сбоя")
    n1 = len(srv.messages)
    assert publish(ok) == "ok" and len(channel(n1)) == 1, channel(n1)


@check
def m5():
    """MAX всё время «теряет» вложение — после повторов в канале не остаётся ни одного сообщения без файла; старого
    предупреждения «без части вложений» никто не получает"""
    it = post(doc=TGC.add(TMsg(501, "doc")), text="Только PDF")
    srv.drop_attachments = True
    n0 = len(srv.messages)
    try:
        res = publish(it)
    finally:
        srv.drop_attachments = False
    assert res == "dropped" and not channel(n0), (res, channel(n0))
    assert not to_people(n0) and not any("без части вложений" in (m["body"].get("text") or "")
                                         for m in srv.messages), to_people(n0)
    assert "dropped the attachment" in journal_of(HOST), journal_of(HOST)[-800:]


@check
def m6():
    """сеть пропала совсем, пока работает планировщик — бот не падает; пост с файлом не уходит «огрызком»"""
    it = post(TGC.add(TMsg(601, "photo")), TGC.add(TMsg(602, "doc")))
    real_post = bot.S.post
    bot.S.post = lambda *a, **k: (_ for _ in ()).throw(bot.requests.exceptions.ConnectionError("Network is down"))
    n0 = len(srv.messages)
    try:
        with bot.theme_ctx(T):
            asyncio.run(bot.publish_due(TGC, CHAN))     # не бросает исключений
    finally:
        bot.S.post = real_post
    assert not channel(n0) and not to_people(n0), (channel(n0), to_people(n0))
    with bot.theme_ctx(T):
        assert it not in bot.QUEUE or not it.get("sent"), it   # либо пропущен, либо ждёт целиком — не частями


if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    os._exit(1)
print("ВСЕ ТЕСТЫ ФАЙЛОВ В ПОСТАХ ПРОШЛИ")
os._exit(0)
