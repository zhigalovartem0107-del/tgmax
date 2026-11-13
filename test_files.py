import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
"""Файлы: PDF, архивы, музыка, голосовые — из Telegram (со входом и без) уходят в MAX файлом."""
import asyncio, datetime, importlib.util, os, sys, tempfile, time, types
sys.path.insert(0, ROOT)
from fake_max import FakeMax

tele = types.ModuleType("telethon"); tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, BOT_TOKEN="TESTTOKEN")
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    os.environ.pop(k, None)
os.chdir(tempfile.mkdtemp())
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
bot.S.trust_env = False

NOTES = []
bot.notify = lambda t, tag=True, rows=None: NOTES.append(t)
bot.api_send = lambda chat, t, rows=None, token=None: "m"
bot.GCFG["admin_chats"] = {"1": 111}
bot.CFG["approval_on"] = False
bot.CFG["dedupe_on"] = False
bot.VIDEO_RETRY_PAUSES[:] = [0, 0]
MONO = [500.0]
time.monotonic = lambda: MONO[0]
PDF = b"%PDF-1.4 knitting pattern " * 50


def last_msg():
    return srv.messages[-1]["body"]


# 1) загрузка файла в MAX: токен из ответа на загрузку, имя и содержимое доходят
p = os.path.join(tempfile.mkdtemp(), "Пуловер.pdf")
open(p, "wb").write(PDF)
bot.send_max(5, "Пуловер", [], None, p)
b = last_msg()
assert b["text"] == "Пуловер" and b["attachments"][0]["type"] == "file", b
tok = b["attachments"][0]["payload"]["token"]
assert srv.files[tok] == ("Пуловер.pdf", PDF), srv.files[tok][0]
print("f1 ok: файл загружается в MAX со своим именем и уходит вложением «файл» вместе с текстом")

# 1б) любые файлы: PDF, архивы, APK, файлы обновлений игр — с правильным типом; большой файл с русским именем — потоком
for name, mime in (("Шапка FJODOR.pdf", "application/pdf"), ("update_0.32.zip", "application/zip"),
                   ("Standoff 2 0.32.apk", "application/vnd.android.package-archive"),
                   ("pack.rar", "application/vnd.rar"), ("data.7z", "application/x-7z-compressed"),
                   ("Mob Food (addon).mcaddon", "application/octet-stream"),
                   ("Pack (addon).mcpack", "application/octet-stream")):
    assert bot.file_mime(name) == mime, (name, bot.file_mime(name))
    fp = os.path.join(tempfile.mkdtemp(), name)
    open(fp, "wb").write(b"PK\x03\x04" + name.encode() * 20)
    tok = bot.upload_file(fp)
    assert srv.files[tok] == (name, open(fp, "rb").read()) and srv.file_mimes[tok] == mime, (name, srv.file_mimes[tok])
cd = bot.content_disposition("Обновление 0.32.apk")
cd.encode("latin-1")                                          # заголовок HTTP — только латиница, иначе загрузка падает
assert "filename*=UTF-8''" in cd and "%D0%9E" in cd, cd
bot.STREAM_FROM, old_stream = 0, bot.STREAM_FROM               # «большой» файл — уходит потоком
try:
    fp = os.path.join(tempfile.mkdtemp(), "Обновление 0.32.apk")
    open(fp, "wb").write(b"APKDATA" * 100)
    tok = bot.upload_file(fp)
finally:
    bot.STREAM_FROM = old_stream
assert srv.files[tok] == ("Обновление 0.32.apk", b"APKDATA" * 100), srv.files[tok][0]
assert srv.file_mimes[tok] == "application/vnd.android.package-archive", srv.file_mimes[tok]
print("f1б ok: PDF, zip, rar, 7z, apk, mcaddon — с правильным типом; большой файл с русским именем — потоком")


# 2) Telegram со входом: пост «PDF + текст» -> в MAX текст и сам PDF
class Doc:
    def __init__(self, size, mime="application/pdf"):
        self.size, self.mime_type, self.attributes = size, mime, []


class Msg:
    def __init__(self, mid, text="", doc=None, name=None, data=b""):
        self.id, self.text, self.grouped_id = mid, text, None
        self.date = datetime.datetime.now(datetime.timezone.utc)
        self.photo = self.video = self.gif = self.video_note = self.sticker = self.fwd_from = None
        self.document = doc
        self.file = types.SimpleNamespace(name=name, ext=".pdf")
        self.data = data

    async def download_media(self, file=None):
        open(file, "wb").write(self.data)
        return file


class TG:
    def __init__(self, msgs):
        self.msgs = msgs

    async def get_messages(self, ch, min_id=0, limit=30, ids=None):
        if ids is not None:
            return next((m for m in self.msgs if m.id == ids), None)
        return sorted([m for m in self.msgs if m.id > min_id], key=lambda m: -m.id)[:limit]


tg = TG([Msg(11, "Пуловер", Doc(len(PDF)), "ad8c21e7e5.pdf", PDF),
         Msg(12, "", Doc(5, "audio/mpeg"), "Песня.mp3", b"ID3xx")])
bot.LAST_IDS["knit"] = 10
asyncio.run(bot.process_channel(tg, "knit", {})); MONO[0] += 10
asyncio.run(bot.process_channel(tg, "knit", {}))
q = {x["id"]: x for x in bot.QUEUE}
assert q["knit:11"]["docs"] == [{"id": 11, "name": "ad8c21e7e5.pdf", "size": len(PDF)}], q
assert q["knit:12"]["docs"][0]["name"] == "Песня.mp3" and q["knit:12"]["text"] == ""
assert any("файлы 1" in j for j in bot.JOURNAL), list(bot.JOURNAL)[-3:]
n0 = len(srv.messages)
assert asyncio.run(bot.publish_item(tg, 5, q["knit:11"])) == "ok"
assert asyncio.run(bot.publish_item(tg, 5, q["knit:12"])) == "ok"
got = [m["body"] for m in srv.messages[n0:]]
assert len(got) == 2 and got[0]["text"].startswith("Пуловер") and got[0]["attachments"][0]["type"] == "file", got
assert srv.files[got[0]["attachments"][0]["payload"]["token"]] == ("ad8c21e7e5.pdf", PDF)
assert srv.files[got[1]["attachments"][0]["payload"]["token"]][0] == "Песня.mp3"
assert not [x for x in os.listdir(bot.MEDIA_DIR) if x.startswith("d_")], os.listdir(bot.MEDIA_DIR)
print("f2 ok: со входом в Telegram PDF и музыка уходят в MAX файлами с текстом поста, временные файлы удаляются")

# 2б) файл не скачался — пост пропускается целиком: ни текста без файла, ни карточки, ни кнопок
tg2 = TG([])
it = dict(q["knit:11"], id="knit:99", sent=0, docs=[{"id": 99, "name": "x.pdf", "size": 1}])
bot.QUEUE.append(it)
NOTES.clear()
assert asyncio.run(bot.publish_item(tg2, 5, it)) == "dropped" and it not in bot.QUEUE
assert not NOTES, NOTES
print("f2б ok: файл не скачался — пост пропущен целиком, без текста и без карточки")

# 2в) файл больше 250 МБ: пост пропускается целиком — не в очередь, без текста, без сообщений и ссылок
NOTES.clear()
big = 250 * 1024 * 1024 + 1
tg3 = TG([Msg(31, "Обновление игры", Doc(big, "application/vnd.android.package-archive"), "Game 1.0.apk", b"x")])
bot.LAST_IDS["knit"] = 30
asyncio.run(bot.process_channel(tg3, "knit", {})); MONO[0] += 10
asyncio.run(bot.process_channel(tg3, "knit", {}))
assert not [x for x in bot.QUEUE if x["ch"] == "knit" and "Обновление игры" in x["text"]], bot.QUEUE
assert not NOTES, NOTES
print("f2в ok: файл больше 250 МБ — пост пропущен целиком, без текста и без сообщений")


# 3) без входа (t.me): документ не скачать — честно говорим; голосовое — переносим
def wrap(post, inner):
    return (f'<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="v/{post}">'
            f'<div class="tgme_widget_message_bubble">{inner}<div class="tgme_widget_message_footer"><a class="tgme_widget_message_date" href="https://t.me/v/{post}">'
            f'<time datetime="2026-10-21T14:36:00+00:00">14:36</time></a></div></div></div></div>')


def docblock(pid, title, href=None):
    return (f'<div class="tgme_widget_message_document_wrap"><a class="tgme_widget_message_document_wrap" href="{href or f"https://t.me/v/{pid}"}">'
            f'<i class="tgme_widget_message_document_icon accent_bg"></i><div class="tgme_widget_message_document">'
            f'<div class="tgme_widget_message_document_title accent_color" dir="auto">{title}</div>'
            f'<div class="tgme_widget_message_document_extra" dir="auto">875.0 KB</div></div></a></div>')


PAGE = "<section class='tgme_channel_history'>" + "".join([
    wrap(21, docblock(21, "Пуловер.pdf") + '<div class="tgme_widget_message_text js-message_text" dir="auto">Схема пуловера</div>'),
    wrap(22, docblock(22, "только_файл.pdf")),
    wrap(23, '<div class="tgme_widget_message_voice_player"><audio class="tgme_widget_message_voice js-message_voice" '
             'src="https://cdn4.telesco.pe/file/voice23.oga" preload="none"></audio></div>'),
    wrap(24, docblock(24, "Mob Food (addon).mcaddon", "https://cdn4.telesco.pe/file/mob.mcaddon")),
]) + "</section>"
msgs = {m.id: m for m in bot.parse_web_page("v", PAGE)}
assert msgs[21].document.name == "Пуловер.pdf" and msgs[21].text == "Схема пуловера" and msgs[21].document.url is None
assert msgs[23].document.url.endswith("voice23.oga")
assert msgs[24].document.name == "Mob Food (addon).mcaddon" and msgs[24].document.url.endswith("mob.mcaddon"), msgs[24].document.url
bot.web_fetch = lambda url: PAGE
bot._download_url = lambda url, dest: open(dest, "wb").write(b"OggS") and dest
web = bot.WebClient()
bot.QUEUE.clear(); bot.LAST_IDS["v"] = 20; NOTES.clear()
asyncio.run(bot.process_channel(web, "v", {})); MONO[0] += 10
asyncio.run(bot.process_channel(web, "v", {}))
q = {x["id"]: x for x in bot.QUEUE}
assert set(q) == {"v:23", "v:24"}, list(q)                # PDF без прямой ссылки пропущен; .mcaddon с ссылкой — в очереди
assert any("нужен аккаунт Telegram (API)" in j for j in bot.JOURNAL), "файл пропущен — с точной причиной в журнале"
assert not [n for n in NOTES if "PDF" in n], NOTES           # ни текста без PDF, ни сообщения
assert asyncio.run(bot.publish_item(web, 5, q["v:24"])) == "ok"          # .mcaddon скачан по прямой ссылке t.me
bmc = srv.messages[-1]["body"]; tokmc = bmc["attachments"][0]["payload"]["token"]
assert srv.files[tokmc][0] == "Mob Food (addon).mcaddon" and srv.file_mimes[tokmc] == "application/octet-stream", \
    (srv.files[tokmc][0], srv.file_mimes[tokmc])
n0 = len(srv.messages)
assert asyncio.run(bot.publish_item(web, 5, q["v:23"])) == "ok"
b = srv.messages[-1]["body"]
assert srv.files[b["attachments"][0]["payload"]["token"]] == ("voice_23.ogg", b"OggS"), b
print("f3 ok: без входа: PDF без ссылки пропущен с точной причиной; .mcaddon с прямой ссылкой t.me и голосовое переносятся")

# 4) пересланное боту сообщение: текст и файл (имя, токен, ссылка) достаются из вложенного сообщения
msg = {"body": {"text": "", "attachments": []}, "link": {"type": "forward", "message": {
    "text": "Схема", "attachments": [{"type": "file", "filename": "схема.pdf", "payload": {"token": "maxfile1", "url": "https://x/f"}}]}}}
text, atts = bot.extract_content(msg)
assert text == "Схема" and atts == [{"kind": "file", "url": "https://x/f", "token": "maxfile1", "name": "схема.pdf"}], atts
print("f4 ok: из пересланного боту сообщения достаются текст и файл (имя, токен, ссылка)")

# 5) отчёт за день картинкой: посты, просмотры из MAX, лучший пост, источники
import json as _json
t0 = bot.THEMES[0]; t0.chat_id = 5; t0.link = "https://max.ru/knit"
bot.PUB["day_posts"] = []; bot.QUEUE.clear()
mids = []
for i, (txt, ch) in enumerate([("Пуловер спицами 🧶 схема", "knit"), ("Шапка крючком", "knit"), ("Мем", "memes")]):
    mid = bot.send_max(5, txt, [], None)
    bot.record_pub(mid, ch, txt)
    mids.append(mid)
srv.views.update({mids[0]: 1500, mids[1]: 320})
st = bot.day_stats(t0)
assert st["posts"] == 3 and st["views"] == 1820 and st["top"]["text"].startswith("Пуловер"), st
assert st["sources"] == [("knit", 2), ("memes", 1)], st["sources"]
SENT_IMG = []
bot.api_send = lambda chat, text, rows=None, token=None: SENT_IMG.append((chat, text, token)) or "m"
bot.GCFG["admin_chats"] = {"1": 111}; bot.CFG["admin_chats"] = {"1": 111}
bot.send_report()
assert SENT_IMG and SENT_IMG[-1][2] and "Итоги дня: постов 3, просмотров 1 820" in SENT_IMG[-1][1], SENT_IMG
assert not os.path.exists("report_day.png")
from PIL import Image
p = bot.report_image([st, dict(st, name="Пустая", posts=0, views=None, avg=None, top=None, sources=[], hours=[0] * 24)],
                     "check.png")
im = Image.open(p); assert im.size[0] == 1080 and im.size[1] > 1400
def _no_pil(*a):
    raise ImportError("no PIL")
_real_img = bot.report_image
bot.report_image = _no_pil; NOTES.clear(); SENT_IMG.clear()
bot.send_report()
assert not SENT_IMG and NOTES and "Отчёт" in NOTES[-1], NOTES
# 7 тем — 3 картинки по 3/3/1, подпись с итогом только на первой
bot.report_image = _real_img
many = [dict(st, name=f"Тема {k}") for k in range(7)]
bot.day_stats = lambda t: many.pop(0)
bot.THEMES[:] = [bot.THEMES[0]] * 7
SENT_IMG.clear(); NOTES.clear()
bot.send_report()
caps = [x[1] for x in SENT_IMG]
assert len(SENT_IMG) == 3 and caps[0].startswith("📊 Итоги дня: постов") and caps[2] == "📊 Итоги дня — 3/3", caps
assert all(x[2] for x in SENT_IMG)
bot.THEMES[:] = bot.THEMES[:1]
print("f5 ok: отчёт за день — картинка: посты, просмотры из MAX, лучший пост, источники; без Pillow — текстом")

# 6) чистый чат: сообщения бота удаляются через час; новое меню удаляет старое; карточки и отчёты остаются
import importlib
spec2 = importlib.util.spec_from_file_location("bot2", ROOT + "/tg_to_max.py")
b2 = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(b2)
b2.S.trust_env = False
b2.AUTODEL.update(temp=[], menu={})
srv.deleted.clear()
info = b2.api_send(5, "✅ Готово")
card = b2.api_send(5, "✋ Новый пост", [[b2.B("✅ Сейчас", "ap:now:x")]])
rep_ = b2.api_send(5, "📊 Итоги дня: постов 3")
m1 = b2.send_screen(5, "⚙️ Меню", [[b2.B("📡 Каналы", "m:ch")]])
assert [x["mid"] for x in b2.AUTODEL["temp"]] == [info], b2.AUTODEL
assert b2.purge_autodel() == 0 and not srv.deleted                      # час ещё не прошёл
m2 = b2.send_screen(5, "⚙️ Меню", [[b2.B("📡 Каналы", "m:ch")]])
assert srv.deleted == [m1] and b2.AUTODEL["menu"]["5"] == m2             # старое меню убрано сразу
assert b2.purge_autodel(time.time() + 3601) == 1 and srv.deleted == [m1, info]
assert card not in srv.deleted and rep_ not in srv.deleted and m2 not in srv.deleted
print("f6 ok: сообщения бота исчезают через час, новое меню убирает старое; карточки с решением и отчёты остаются")

print("ВСЕ ТЕСТЫ ФАЙЛОВ ПРОШЛИ")
os._exit(0)
