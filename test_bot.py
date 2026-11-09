import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
import asyncio, importlib.util, os, sys, tempfile, time, types

# --- заглушки: telethon нет, сети нет ---
tele = types.ModuleType("telethon")
tele.TelegramClient = object
sys.modules["telethon"] = tele
os.chdir(tempfile.mkdtemp())

spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)

CLOCK = [1_800_000_000.0]
time.time = lambda: CLOCK[0]

SENT, NOTES, API = [], [], []


async def fake_publish_item(client, chat_id, it):
    SENT.append(it["id"])
    return "ok"

bot.publish_item = fake_publish_item
bot.notify = lambda text, tag=True, rows=None: NOTES.append(text)
bot.api_send = lambda chat, text, rows=None, token=None: API.append((chat, text, rows))
bot.upload_image = lambda p: "tok"
bot.send_max = lambda chat, text, imgs, vid=None: SENT.append(("send", text, len(imgs), bool(vid)))

CFG = bot.CFG
CFG["admin_chats"] = {"1": 111}
CFG["admins"] = [1]
CFG["gap_min"] = 60
CFG["later_min"] = 60
CFG["night_on"] = False
CFG["queue_on"] = True


def run(coro):
    return asyncio.run(coro)


def reset():
    bot.QUEUE.clear(); SENT.clear(); NOTES.clear(); API.clear()
    bot.PUB.update({"last_pub": 0, "ch_last_date": {}, "ch_last_pub": {}, "series": {}, "urgent_times": []})
    bot.FORCE["until"] = 0


def item(i, ch="a", status="ok", dt=0, **kw):
    it = {"id": f"{ch}:{i}", "ch": ch, "date": CLOCK[0] + dt, "enq": CLOCK[0], "text": f"пост {i}",
          "photos": [], "vids": [], "sent": 0, "fails": 0, "urgent": False, "status": status}
    it.update(kw)
    bot.QUEUE.append(it)
    return it


def due():
    run(bot.publish_due(None, 5))


# 1) авто-серии выкл: 3 поста одного канала подряд -> по одному в час, не кучей
reset()
for i in range(3):
    item(i, "a", dt=i * 300)
due(); due()
assert SENT == ["a:0"], SENT
CLOCK[0] += 3601; due()
assert SENT == ["a:0", "a:1"], SENT
CLOCK[0] += 3601; due()
assert SENT == ["a:0", "a:1", "a:2"], SENT
print("1 ok: один пост за паузу, без кучи")

# 1b) 5 каналов тоже не кучей
reset()
for i, ch in enumerate("abcde"):
    item(i, ch)
due(); due()
assert len(SENT) == 1, SENT
print("1b ok: разные каналы тоже по одному")

# 2) кнопка «Сейчас»: выходит сразу, даже если недавно что-то выходило
reset()
bot.PUB["last_pub"] = CLOCK[0] - 60
p = item(1, "a", status="pending")
bot.send_previews([p])
assert len(API) == 1 and "ap:now:a:1" in str(API[0][2]) and "ap:later:a:1" in str(API[0][2]), API
text, _ = bot.approve("now:a:1", 1)
assert "сейчас" in text, text
due()
assert SENT == ["a:1"], SENT
print("2 ok: «Сейчас» выкладывает без ожидания паузы")

# 3) кнопка «Через час»: до срока не выходит, потом выходит
reset()
p = item(1, "a", status="pending")
text, _ = bot.approve("later:a:1", 1)
assert "через 1 ч" in text, text
due(); CLOCK[0] += 3000; due()
assert SENT == [], SENT
CLOCK[0] += 700; due()
assert SENT == ["a:1"], SENT
print("3 ok: «Через час» ждёт час и выходит сама")

# 4) пачка из двух: ОДНА карточка с кнопками, «по очереди через час» -> первый через час, второй — после паузы
reset()
p1, p2 = item(1, "a", status="pending"), item(2, "a", status="pending")
API.clear()
bot.send_previews([p1, p2])
group_cards = [a for a in API if a[2] and "ap:glater" in str(a[2])]
assert len(group_cards) == 1, API
assert p1["grp"] == p2["grp"]
text, _ = bot.approve("glater:" + p1["grp"], 1)
assert "по очереди" in text, text
due(); CLOCK[0] += 3599; due()
assert SENT == [], SENT
CLOCK[0] += 2; due()
assert SENT == ["a:1"], SENT                       # не пачкой: второй ждёт паузу расписания
CLOCK[0] += 60 * 60 + 5; due()
assert SENT == ["a:1", "a:2"], SENT
assert not bot.QUEUE
print("4 ok: пачка — одна карточка, посты выходят по одному: первый через час, второй после паузы")

# 4b) пропустить всю пачку
reset()
p1, p2 = item(1, "a", status="pending"), item(2, "a", status="pending")
bot.send_previews([p1, p2])
text, _ = bot.approve("gno:" + p1["grp"], 1)
assert not bot.QUEUE, bot.QUEUE
assert bot.approve("gno:" + p1["grp"], 1)[0] == "Этот пост уже обработан."
print("4b ok: «пропустить все» + повторное нажатие безопасно")

# 5) очередь пуста или пост ждёт одобрения — бот ничего не выкладывает сам (запасных постов нет)
reset()
due(); CLOCK[0] += 3601; due()
assert SENT == [], SENT
item(1, "a", status="pending")
due()
assert SENT == [], SENT
print("5 ok: пустая очередь — ничего не выходит; пост на одобрении сам не выходит")

# 6) команды
bot.CLAIM_CODE = None
reset()
item(1, "a"); item(2, "b", status="pending")
bot.PENDING.clear()
bot.handle_message(1, 111, "Gh", "/now", {"body": {"text": "/now"}})
assert bot.QUEUE[0].get("manual") and bot.QUEUE[0]["at"] == 0
bot.handle_message(1, 111, "Gh", "/approve", None)
assert all(x["status"] == "ok" for x in bot.QUEUE)
bot.handle_message(1, 111, "Gh", "/gap 45", None)
assert CFG["gap_min"] == 45
bot.handle_message(1, 111, "Gh", "/later 90", None)
assert CFG["later_min"] == 90
bot.handle_message(1, 111, "Gh", "/pause", None); assert CFG["paused"]
bot.handle_message(1, 111, "Gh", "/resume", None); assert not CFG["paused"]
bot.handle_message(1, 111, "Gh", "/skip", None); assert len(bot.QUEUE) == 1
bot.handle_message(1, 111, "Gh", "/quatsch", None)
assert "Команды" in API[-1][1]
for c in ("status", "queue", "help", "menu", "report"):
    bot.handle_message(1, 111, "Gh", "/" + c, None)
CFG["gap_min"] = 60; CFG["later_min"] = 60
print("6 ok: команды работают")

# 9) видео не скачалось: пост НЕ выходит калекой, а ждёт решения админа
bot.VIDEO_RETRY_PAUSES[:] = [0, 0]
import importlib
spec2 = importlib.util.spec_from_file_location("bot2", ROOT + "/tg_to_max.py")
bot2 = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(bot2)
bot2.VIDEO_RETRY_PAUSES[:] = [0, 0]
bot2.MAX_RETRY_PAUSES = [0, 0, 0]
bot2.upload_image = bot2.upload_video = bot2.upload_file = lambda path: "tok"   # файлы «в MAX» — без сети
bot2.notify = lambda t, tag=True, rows=None: NOTES.append(t)
sent2, api2 = [], []
bot2.send_max = lambda chat, text, imgs, vid=None: sent2.append((text, len(imgs), vid))
bot2.api_send = lambda chat, text, rows=None, token=None: api2.append((chat, text, rows)) or "m1"
bot2.GCFG["admin_chats"] = {"1": 111}

class Msg:
    video = gif = video_note = document = None
    async def download_media(self, file=None): return None
class Client:
    def __init__(self, msg): self.msg = msg; self.calls = 0
    async def get_messages(self, ch, ids=None):
        self.calls += 1
        return self.msg

NOTES.clear()
it = {"id": "a:7", "ch": "a", "date": 0, "enq": 0, "text": "текст к видео", "photos": [], "vids": [7],
      "sent": 0, "fails": 0, "status": "ok"}
bot2.QUEUE.append(it)
res = asyncio.run(bot2.publish_item(Client(Msg()), 5, it))
assert res == "dropped" and not sent2 and it not in bot2.QUEUE, (res, sent2)   # видео нет — пост пропущен целиком
assert not [a for a in api2 if a[2] and "ap:" in str(a[2])], api2              # ни кнопок, ни карточки
print("9 ok: нет видео -> пост пропущен целиком: без текста, без кнопок")

# 9a) кнопок «Повторить» / «Убрать» больше нет: карточек не создаём, approve их не принимает


# 9c) фото пропало с диска: докачиваем из Telegram по id, а не шлём пост без фото
os.makedirs(bot2.MEDIA_DIR, exist_ok=True)
class PhotoMsg:
    photo = object(); document = None; sticker = None; gif = None
    async def download_media(self, file=None):
        open(file, "wb").write(b"jpgdata"); return file
class PClient:
    async def get_messages(self, ch, ids=None): return PhotoMsg()
sent2.clear()
p = f"{bot2.MEDIA_DIR}/a_5.jpg"
it3 = {"id": "a:5", "ch": "a", "date": 0, "enq": 0, "text": "с фото", "photos": [p], "pids": [5], "vids": [],
       "sent": 0, "fails": 0, "status": "ok"}
bot2.QUEUE.append(it3)
res = asyncio.run(bot2.publish_item(PClient(), 5, it3))
assert res == "ok" and sent2 and sent2[0][1] == 1, (res, sent2)
print("9c ok: потерянное фото докачалось из Telegram")

# 9d) фото потеряно и докачать нельзя -> ждёт решения, а не выходит без фото
class NoClient:
    async def get_messages(self, ch, ids=None): return None
sent2.clear()
it4 = {"id": "a:6", "ch": "a", "date": 0, "enq": 0, "text": "с фото", "photos": [f"{bot2.MEDIA_DIR}/nope.jpg"],
       "pids": [6], "vids": [], "sent": 0, "fails": 0, "status": "ok"}
bot2.QUEUE.append(it4)
res = asyncio.run(bot2.publish_item(NoClient(), 5, it4))
assert res == "dropped" and not sent2 and it4 not in bot2.QUEUE, (res, sent2)
print("9d ok: фото не найдено -> пост пропущен целиком, без фото и без текста")

# 9b) видео качается со 2-й попытки
class Flaky(Msg):
    n = 0
    video = object()
    async def download_media(self, file=None):
        Flaky.n += 1
        if Flaky.n < 2:
            raise ConnectionError("обрыв")
        open(file, "wb").write(b"vid")
        return file
os.makedirs(bot2.MEDIA_DIR, exist_ok=True)
path, err = asyncio.run(bot2.download_video(Client(Flaky()), "a", 8))
assert path and err is None, (path, err)
print("9b ok: видео докачалось со 2-й попытки")

# 9e) файл не скачался — пост НЕ застревает навсегда и НЕ выходит без файла: ждёт и сам пробует снова (1, 2, 5… 30 мин,
#     сколько угодно — и через сутки не удаляется), карточка админу — одна. Скачалось — выходит целиком, карточка убрана.
#     Видео и файл качаются ДО первой отправки: раньше видео уходило, а не скачавшийся файл молча пропускался
class VidMsg(Msg):
    video, n = types.SimpleNamespace(size=3), 0
    async def download_media(self, file=None):
        VidMsg.n += 1
        open(file, "wb").write(b"vid"); return file
class DocMsg(Msg):
    ok, id = False, 10
    document = types.SimpleNamespace(mime_type="application/pdf", size=3)
    async def download_media(self, file=None):
        if not DocMsg.ok:
            raise ConnectionError("сеть моргнула")
        open(file, "wb").write(b"pdf"); return file
class Two:
    async def get_messages(self, ch, ids=None):
        return VidMsg() if ids == 9 else DocMsg()
sent2.clear(); api2.clear(); bot2.QUEUE[:] = []
bot2.send_max = lambda chat, text, imgs, vid=None, doc=None: sent2.append((text, len(imgs), vid, doc)) or "mid"
for k_, v_ in dict(night_on=False, smart_time=False, queue_on=True, gap_min=0).items():
    bot2.CFG[k_] = v_
t0 = CLOCK[0]
it5 = {"id": "a:9", "ch": "a", "date": t0, "enq": t0, "text": "видео + файл", "photos": [], "vids": [9],
       "docs": [{"id": 10, "name": "x.pdf", "size": 3}], "sent": 0, "fails": 0, "status": "ok"}
bot2.QUEUE.append(it5)
assert asyncio.run(bot2.publish_item(Two(), 5, it5)) == "dropped"      # файл не скачался — пост пропущен целиком
assert not sent2, sent2                                                # ни видео, ни текста, ни ссылки
assert it5 not in bot2.QUEUE
assert not [a for a in api2 if a[2] and "ap:" in str(a[2])], api2    # кнопок нет
CLOCK[0] = t0
print("9e ok: файл не скачался — пост пропущен целиком: без текста, без кнопок, без повторов")


# ---- 10) цепочка: через час в 12 -> следующий в 13 -> 14 ----
reset(); CFG["gap_min"] = 60; CFG["later_min"] = 60
base = CLOCK[0]
ps = [item(i, "a", status="pending") for i in range(3)]
ats = []
for p in ps:
    bot.approve("later:" + p["id"], 1)
    ats.append(p["at"])
assert ats == [base + 3600, base + 7200, base + 10800], [a - base for a in ats]
CLOCK[0] = base + 3601; due(); assert SENT == ["a:0"], SENT
CLOCK[0] = base + 7201; due(); assert SENT == ["a:0", "a:1"], SENT
CLOCK[0] = base + 10801; due(); assert SENT == ["a:0", "a:1", "a:2"], SENT
print("10 ok: 12:00 -> 13:00 -> 14:00")

# 10b) пачка встаёт в цепочку целиком, авто-пост не лезет вперёд назначенных
reset()
base = CLOCK[0]
p0 = item(0, "a", status="pending"); bot.approve("later:a:0", 1)          # base+1ч
g1, g2 = item(1, "b", status="pending"), item(2, "b", status="pending")
bot.send_previews([g1, g2]); bot.approve("glater:" + g1["grp"], 1)
assert g1["at"] == base + 7200 and not g2.get("manual"), g1["at"] - base   # второй из пачки — в очередь после первого
item(3, "c")                                                               # обычный, по расписанию
due()
assert SENT == [], SENT                       # ждёт: впереди назначенные посты
print("10b ok: пачка в цепочке, авто-пост не обгоняет")

# 10c) «Через час» не попадает на ночь: ночь 21–8, сейчас 20:30 -> не 21:30, а 8:00 утра; следующий — 9:00
reset(); CFG["gap_min"] = 60; CFG["later_min"] = 60
CFG["night_on"], CFG["night_from"], CFG["night_to"] = True, 21, 8
save_clock = CLOCK[0]
CLOCK[0] = time.mktime((2027, 1, 15, 20, 30, 0, 0, 0, -1))
q0, q1 = item(0, "a", status="pending"), item(1, "a", status="pending")
text, _ = bot.approve("later:a:0", 1)
lt = time.localtime(q0["at"])
assert (lt.tm_mday, lt.tm_hour, lt.tm_min) == (16, 8, 0), time.ctime(q0["at"])
assert "завтра в 08:00" in text, text
bot.approve("later:a:1", 1)
assert time.localtime(q1["at"]).tm_hour == 9 and time.localtime(q1["at"]).tm_mday == 16, time.ctime(q1["at"])
CLOCK[0] = time.mktime((2027, 1, 15, 22, 0, 0, 0, 0, -1)); due(); assert SENT == [], SENT     # ночью не выходит
CLOCK[0] = time.mktime((2027, 1, 16, 8, 0, 1, 0, 0, -1)); due(); assert SENT == ["a:0"], SENT
# днём перенос не мешает; ночь 0–8 (без перехода через полночь): 23:30 + 1ч = 0:30 -> 8:00 того же утра
reset()
CLOCK[0] = time.mktime((2027, 1, 15, 12, 0, 0, 0, 0, -1)); q2 = item(2, "a", status="pending")
bot.approve("later:a:2", 1); assert time.localtime(q2["at"]).tm_hour == 13
reset(); CFG["night_from"], CFG["night_to"] = 0, 8
CLOCK[0] = time.mktime((2027, 1, 15, 23, 30, 0, 0, 0, -1)); q3 = item(3, "a", status="pending")
bot.approve("later:a:3", 1); lt = time.localtime(q3["at"])
assert (lt.tm_mday, lt.tm_hour) == (16, 8), time.ctime(q3["at"])
CFG["night_on"], CFG["night_from"], CFG["night_to"] = False, 0, 8
CLOCK[0] = save_clock
print("10c ok: «Через час» на ночь не назначается — переносится на конец ночи (утро), цепочка идёт дальше")

# ---- 11) карточки: у второго админа — сразу, у нажавшего — через 5 минут ----
reset()
CFG["admin_chats"] = {"1": 111, "2": 222}
CFG["admins"] = [1, 2]
MIDS = [0]
def api_with_mid(chat, text, rows=None, token=None):
    MIDS[0] += 1
    API.append((chat, text, rows))
    return f"m{MIDS[0]}"
bot.api_send = api_with_mid
DEL = []
bot.delete_msg = lambda mid: DEL.append(mid)
bot.CARDS.clear()
p = item(1, "a", status="pending")
bot.send_previews([p])
assert len(bot.CARDS) == 2, bot.CARDS
mid_by_chat = {c["chat"]: c["mid"] for c in bot.CARDS}
bot.approve("now:a:1", 1, 111)                # одобрил первый админ из своего чата
bot.purge_cards()
assert DEL == [mid_by_chat[222]], DEL          # у второго убрали сразу
CLOCK[0] += 299; bot.purge_cards()
assert len(DEL) == 1                           # у нажавшего ещё висит
CLOCK[0] += 2; bot.purge_cards()
assert DEL == [mid_by_chat[222], mid_by_chat[111]], DEL
assert bot.CARDS == []
print("11 ok: у другого админа карточка пропала сразу, у нажавшего — через 5 минут")

# 11b) пачка: превью + общая карточка у обоих чистятся
reset(); DEL.clear(); bot.CARDS.clear()
g1, g2 = item(1, "a", status="pending"), item(2, "a", status="pending")
bot.send_previews([g1, g2])
assert len(bot.CARDS) == 6, len(bot.CARDS)     # 2 превью + 1 общая, на каждого из 2 админов
bot.approve("gno:" + g1["grp"], 2, 222)
bot.purge_cards(); assert len(DEL) == 3, DEL   # всё у первого админа убрано сразу
CLOCK[0] += 301; bot.purge_cards(); assert len(DEL) == 6 and bot.CARDS == []
print("11b ok: пачка целиком чистится у обоих")

# 11c) «убрать этот» в пачке не трогает общую карточку, пока в пачке есть посты
reset(); DEL.clear(); bot.CARDS.clear()
g1, g2 = item(1, "a", status="pending"), item(2, "a", status="pending")
bot.send_previews([g1, g2])
bot.approve("no:a:1", 1, 111)
CLOCK[0] += 400; bot.purge_cards()
left_refs = {c["ref"] for c in bot.CARDS}
assert g1["grp"] in left_refs and "a:2" in left_refs and "a:1" not in left_refs, left_refs
bot.approve("gnow:" + g1["grp"], 1, 111)
CLOCK[0] += 400; bot.purge_cards(); assert bot.CARDS == []
print("11c ok: частичное удаление из пачки корректно")

# 11d) /approve и «одобрить все» тоже чистят карточки
reset(); DEL.clear(); bot.CARDS.clear()
p = item(1, "a", status="pending"); bot.send_previews([p])
bot.handle_message(1, 111, "Gh", "/approve", None)
bot.purge_cards(); assert len(DEL) == 1
print("11d ok: /approve чистит карточку у второго админа")

# 12) нет прав на канал (MAX 403 chat.denied): посты НЕ удаляются, админам — одно сообщение,
#     канал не долбим каждую минуту; появились права — всё выходит
denied = {"on": True}
def max_send(chat, text, imgs, vid=None):
    if denied["on"]:
        raise bot2.ChatDenied('MAX ошибка 403: {"code":"chat.denied","message":"Insufficient access rights"}')
    sent2.append((text, len(imgs), vid))
bot2.send_max = max_send
RIGHTS = {"v": False}
bot2.bot_rights = lambda chat: RIGHTS["v"]
bot2.QUEUE.clear(); NOTES.clear(); sent2.clear()
def q_item(n):
    it = {"id": f"q:{n}", "ch": "q", "date": time.time(), "enq": time.time(), "text": f"пост {n}", "photos": [],
          "vids": [], "sent": 0, "fails": 0, "urgent": False, "status": "ok"}
    bot2.QUEUE.append(it)
    return it
# одиночный случайный отказ MAX — админам не пишем, пробуем снова через 2 минуты
q0 = q_item(0)
assert asyncio.run(bot2.publish_item(None, 777, q0)) == "retry" and not NOTES
assert 0 < bot2.DENIED[777] - time.time() <= 130 and q0 in bot2.QUEUE
denied["on"] = False; bot2.DENIED.clear(); q0["retry_at"] = 0
assert asyncio.run(bot2.publish_item(None, 777, q0)) == "ok" and 777 not in bot2.DENY_N and sent2
assert not any("прав" in n for n in NOTES), NOTES
denied["on"] = True; sent2.clear(); NOTES.clear(); bot2.QUEUE.clear()
# MAX отказывает, но сам говорит, что бот — админ: это сбой, «нет прав» не пишем
RIGHTS["v"] = True
q9 = q_item(9)
for _ in range(5):
    q9["retry_at"] = 0
    assert asyncio.run(bot2.publish_item(None, 777, q9)) == "retry"
assert not NOTES, NOTES
RIGHTS["v"] = False; bot2.QUEUE.clear(); bot2.DENY_N.clear()
# проверка прав раз в час: без постов; одно сообщение, когда права пропали, и одно — когда вернулись
NOTES.clear(); bot2.RIGHTS_RECHECK_SEC = 0; bot2.RIGHTS_TOLD.clear()
saved_chat = bot2.THEMES[0].chat_id; bot2.THEMES[0].chat_id = 777
RIGHTS["v"] = False
n_sent = len(sent2)
bot2.check_rights_all(); bot2.check_rights_all()
assert sum("больше не админ" in n for n in NOTES) == 1 and len(sent2) == n_sent, NOTES
RIGHTS["v"] = True
bot2.check_rights_all(); bot2.check_rights_all()
assert sum("Права в канале MAX вернулись" in n for n in NOTES) == 1, NOTES
RIGHTS["v"] = None                                       # MAX не ответил — молчим
NOTES.clear(); bot2.check_rights_all(); assert not NOTES
bot2.THEMES[0].chat_id = saved_chat; RIGHTS["v"] = False; bot2.DENY_N.clear()
RIGHTS["v"] = False
assert "НЕ админ" in bot2.rights_note(777)
RIGHTS["v"] = True
assert "могу публиковать" in bot2.rights_note(777)
RIGHTS["v"] = False
print("12в ok: проверка прав раз в час — без постов, «права пропали» и «вернулись» по одному разу")
print("12a ok: случайный отказ MAX и отказ при живых правах админа — без ложного «нет прав»")
qi = q_item(1)
for _ in range(4):
    qi["retry_at"] = 0
    assert asyncio.run(bot2.publish_item(None, 777, qi)) == "retry"
assert qi in bot2.QUEUE and qi.get("fails", 0) == 0 and sum("нет прав" in n for n in NOTES) == 1, NOTES
assert "«🎭 Мои каналы» → тема → «📢 Канал MAX»" in next(n for n in NOTES if "нет прав" in n)
assert bot2.DENIED.get(777, 0) > time.time()
denied["on"] = False
bot2.DENIED.clear(); qi["retry_at"] = 0
assert asyncio.run(bot2.publish_item(None, 777, qi)) == "ok" and sent2
print("12 ok: нет прав на канал — посты целы, одно сообщение админам, после выдачи прав всё выходит")
assert bot2.plain_snip("**😎** **Есть у нас художники**", 40) == "😎 Есть у нас художники"
print("12b ok: в списке очереди нет значков ** разметки")

# 13) очередь: пост на одобрении подписан понятно, есть кнопки «показать карточки» и «одобрить все»
reset()
pp = item(50, "a", status="pending"); item(51, "a")
text, rows = bot.screen("ql", 1)
assert "ждёт ТВОЕГО одобрения" in text and "Сам он не выйдет" in text, text
pl = [b["payload"] for r in rows for b in r]
assert "a:qcards" in pl and "a:qapr" in pl, pl
API.clear(); bot.do_action("qcards", 1)
assert any("ap:now:a:50" in str(a[2]) for a in API), API
bot.do_action("qapr", 1)
assert pp["status"] == "ok"
text, rows = bot.screen("ql", 1)
assert "ТВОЕГО" not in text and "a:qapr" not in str(rows)
print("13 ok: очередь — понятная подпись у постов на одобрении, кнопки «Показать карточки» и «Одобрить все»")

# 15) очередь: убрать конкретный пост кнопкой ❌ N (у других не трогаем)
reset()
k1, k2 = item(70, "RobloxRu_Official"), item(71, "b")
text, rows = bot.screen("ql", 1)
assert "a:qdel:RobloxRu_Official:70" in str(rows), rows
bot.do_action("qdel:RobloxRu_Official:70", 1)
assert k1 not in bot.QUEUE and k2 in bot.QUEUE
print("15 ok: в очереди можно убрать конкретный пост")

print("\nВСЕ ТЕСТЫ ПРОШЛИ")
