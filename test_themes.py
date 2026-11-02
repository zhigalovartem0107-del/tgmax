import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
import asyncio, datetime, importlib.util, os, random, sys, tempfile, time, types

tele = types.ModuleType("telethon")
tele.TelegramClient = object
sys.modules["telethon"] = tele
WORK = tempfile.mkdtemp()
os.chdir(WORK)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------- дубли: новая версия даёт те же ответы, что старая, и быстрее ----------
old = load(ROOT + "/legacy_v2.py", "old")
new = load(ROOT + "/tg_to_max.py", "new")

random.seed(7)
vocab = ("роблокс обновление хэллоуин ивент новый питомец карта сервер скин робукс предмет игрок режим босс "
         "уровень награда магазин лобби команда турнир ключ скидка дроп событие сезон пасс монета меч броня "
         "карта квест праздник подарок").split()
def fake_text(k=14):
    return " ".join(random.choice(vocab) for _ in range(k))

hist_txt = [fake_text() for _ in range(300)]
def entry(txt):
    return {"norm": new.norm(txt), "img": [], "fwd": [], "t": time.time()}

def build(mod, texts):
    return [{"norm": mod.norm(t), "img": [], "fwd": [], "t": time.time(), "alts": []} for t in texts]

h_old, h_new = build(old, hist_txt), build(new, hist_txt)
probes = [fake_text() for _ in range(20)]
for base in random.sample(hist_txt, 10):       # похожие пересказы
    w = base.split()
    random.shuffle(w)
    probes.append(" ".join(w[:11] + [random.choice(vocab)]))
same = 0
for p in probes:
    a = old.find_duplicate({"norm": old.norm(p), "img": [], "fwd": []}, h_old)
    b = new.find_duplicate({"norm": new.norm(p), "img": [], "fwd": []}, h_new)
    assert (a is None) == (b is None), p
    if a is not None:
        assert h_old.index(a) == h_new.index(b), p
    same += 1
t0 = time.time()
for p in probes[:10]:
    old.find_duplicate({"norm": old.norm(p), "img": [], "fwd": []}, h_old)
t_old = time.time() - t0
t0 = time.time()
for p in probes[:10]:
    new.find_duplicate({"norm": new.norm(p), "img": [], "fwd": []}, h_new)
t_new = time.time() - t0
print(f"d ok: поиск дублей даёт те же ответы ({same} проб); 10 постов: было {t_old:.2f} с, стало {t_new:.2f} с")

# ---------- темы ----------
bot = new
CLOCK = [1_800_000_000.0]
time.time = lambda: CLOCK[0]
MONO = [1000.0]
time.monotonic = lambda: MONO[0]
SENT, NOTES, API = [], [], []
MIDS = [0]


def api(chat, text, rows=None, token=None):
    MIDS[0] += 1
    API.append((chat, text, rows))
    return f"m{MIDS[0]}"

bot.api_send = api
NOTE_ROWS = []
bot.notify = lambda text, tag=True, rows=None: NOTES.append(text) or NOTE_ROWS.append(rows)
bot.delete_msg = lambda mid: None
bot.send_max = lambda chat, text, imgs, vid=None: SENT.append((chat, text, len(imgs)))
bot.upload_image = lambda p: "tok"
bot.GCFG["admin_chats"] = {"1": 111}
bot.GCFG["admins"] = [1]
bot.GCFG["admin_names"] = {"1": "Gh"}

main_t = bot.THEMES[0]
main_t.chat_id = 100
main_t.link = "https://max.ru/channel_main"
assert bot.CFG["gap_min"] == 60

t2 = bot.create_theme("Коты")
t2.link, t2.chat_id = "https://max.ru/cats", 200
with bot.theme_ctx(t2):
    assert bot.CFG["channels"] == [] and bot.CFG["old_signatures"] == []
    bot.CFG["channels"].append("catmemes")
    bot.CFG["gap_min"] = 30
    bot.CFG["ad_keywords"] += ["реклама_котов"]
    assert "реклама_котов" in bot.CFG["ad_keywords"]
    sig2 = bot.signature()
assert bot.CFG["gap_min"] == 60 and "реклама_котов" not in bot.CFG["ad_keywords"]
assert "channel_main" in bot.signature() and "cats" in sig2 and "Коты" in sig2
assert "catmemes" not in bot.CFG["channels"]
print("t1 ok: у темы свои каналы, подпись со своей ссылкой, свои фильтры и расписание")

# очереди раздельные, у новой темы свои файлы и префикс id
def mk(i, ch, status="ok", **kw):
    it = {"id": f"{bot.cur().prefix}{ch}:{i}", "ch": ch, "date": CLOCK[0], "enq": CLOCK[0], "text": f"п{i}",
          "photos": [], "vids": [], "sent": 0, "fails": 0, "urgent": False, "status": status}
    it.update(kw)
    bot.QUEUE.append(it)
    return it

bot.QUEUE.clear()
a = mk(1, "robl", "pending")
with bot.theme_ctx(t2):
    b = mk(1, "catmemes", "pending")
    assert b["id"] == "t2|catmemes:1" and len(bot.QUEUE) == 1
assert len(bot.QUEUE) == 1 and a["id"] == "robl:1"
bot.save_queue()
with bot.theme_ctx(t2):
    bot.save_queue()
assert os.path.exists("queue.json") and os.path.exists("queue_t2.json")
print("t2 ok: очереди раздельные, файлы раздельные, id с префиксом темы")

# кнопки находят свою тему независимо от того, какую тему открыл админ
text, _ = bot.approve("now:t2|catmemes:1", 1, 111)
assert b["status"] == "ok" and b["manual"] and a["status"] == "pending", (b, a)
print("t3 ok: кнопка на карточке другой темы работает, чужую тему не трогает")

# публикация в СВОЙ канал MAX
published = []
async def fake_pub(client, chat_id, it):
    published.append((chat_id, it["id"]))
    return "ok"
bot.publish_item = fake_pub
with bot.theme_ctx(t2):
    asyncio.run(bot.publish_due(None, t2.chat_id))
assert published == [(200, "t2|catmemes:1")], published
bot.approve("now:robl:1", 1, 111)
asyncio.run(bot.publish_due(None, main_t.chat_id))
assert published[-1] == (100, "robl:1")
print("t4 ok: каждая тема выкладывает в свой канал MAX")

# тайник и расписание тоже по темам
with bot.theme_ctx(t2):
    bot.STASH.append({"id": "s1", "text": "кот", "photos": [], "videos": [], "sent": 0, "fails": 0})
    CLOCK[0] += 4000
    asyncio.run(bot.publish_due(None, t2.chat_id))
    assert not bot.STASH
assert SENT and SENT[-1][0] == 200 and "кот" in SENT[-1][1] and "cats" in SENT[-1][1]
assert not bot.STASH
print("t5 ok: тайник темы выкладывается в её канал с её подписью")

def hm(text, uid=1):
    with bot.theme_ctx(bot.active_theme(uid)):
        bot.handle_message(uid, 111, "Gh", text, None)


# меню: /theme, переключение, добавление нескольких каналов
bot.PENDING.clear()
hm("/theme")
assert "Темы" in API[-1][1] and "Коты" in API[-1][1]
hm("/theme 2")
assert bot.META["active"]["1"] == "t2"
assert "Коты" in API[-1][1]
bot.PENDING[1] = "chadd"
hm("@aaaa bbbb, https://t.me/cccc\nt.me/dddd")
with bot.theme_ctx(t2):
    assert bot.CFG["channels"] == ["catmemes", "aaaa", "bbbb", "cccc", "dddd"], bot.CFG["channels"]
assert "aaaa" not in bot.CFG["channels"]
hm("/theme Основная")
assert bot.META["active"]["1"] == "main"
print("t6 ok: /theme переключает, каналы добавляются пачкой и только в выбранную тему")

# «тайник пуст» пришло про тему «Коты», а админ сейчас в «Основной»: кнопка пополняет тайник «Котов»
with bot.theme_ctx(t2):
    bot.STASH.clear()
    bot.STASH.append({"id": "s2", "text": "кот2", "photos": [], "videos": [], "sent": 0, "fails": 0})
    NOTE_ROWS.clear()
    assert asyncio.run(bot.publish_stash(t2.chat_id)) is True and not bot.STASH
assert NOTE_ROWS and NOTE_ROWS[-1] and NOTE_ROWS[-1][0][0]["payload"] == "sf:t2", NOTE_ROWS
assert "Коты" in NOTE_ROWS[-1][0][0]["text"]
assert bot.active_theme(1) is main_t
ANS = []
bot.api_answer = lambda cid, text, rows=None: ANS.append((text, rows))
with bot.theme_ctx(bot.active_theme(1)):
    bot.handle_callback({"callback_id": "c1", "payload": "sf:t2", "user": {"user_id": 1}})
assert bot.active_theme(1) is t2 and bot.PENDING.get(1) == "stashadd" and "Коты" in ANS[-1][0], ANS[-1]
hm("свежий мем про кота")
with bot.theme_ctx(t2):
    assert [x["text"] for x in bot.STASH] == ["свежий мем про кота"], bot.STASH
assert not any(x.get("text") == "свежий мем про кота" for x in bot.STASH)
bot.PENDING.clear(); hm("/theme Основная")
print("t6b ok: «пополнить тайник» из уведомления пополняет тайник той темы, про которую уведомление")

# «🌐 для всех тем»: настройка из меню меняется во всех темах; каналы у каждой свои; выключил — снова по одной
ANS = []
bot.api_answer = lambda cid, text, rows=None: ANS.append((text, rows))
def cb(payload):
    with bot.theme_ctx(bot.active_theme(1)):
        bot.handle_callback({"callback_id": "c", "payload": payload, "user": {"user_id": 1}})
    return ANS[-1]
cb("m:th")
assert "a:allmode" in str(ANS[-1][1])
cb("a:allmode")
assert bot.all_mode(1) and "ВКЛ" in ANS[-1][0]
ch_main = list(bot.CFG["channels"])
with bot.theme_ctx(t2):
    ch_t2 = list(bot.CFG["channels"]); w2 = bot.CFG["empty_warn_on"]; v2 = bot.CFG["video_on"]
assert bot.CFG["empty_warn_on"] == w2
cb("a:ewarn"); cb("a:vid")
with bot.theme_ctx(t2):
    assert bot.CFG["empty_warn_on"] == (not w2) and bot.CFG["video_on"] == (not v2)
    assert bot.CFG["channels"] == ch_t2
assert bot.CFG["empty_warn_on"] == (not w2) and bot.CFG["channels"] == ch_main
bot.PENDING[1] = "addsw"
hm("казино")
with bot.theme_ctx(t2):
    assert "казино" in bot.CFG["stop_words"], bot.CFG["stop_words"]
cb("a:allmode")
assert not bot.all_mode(1)
cb("a:ewarn")
with bot.theme_ctx(t2):
    assert bot.CFG["empty_warn_on"] == (not w2)                 # выкл — вторую тему не трогаем
for th in (bot.THEMES[0], t2):                               # вернуть как было для следующих проверок
    with bot.theme_ctx(th):
        bot.CFG["video_on"] = True
        bot.CFG["stop_words"] = [w for w in bot.CFG["stop_words"] if w != "казино"]
print("t6в ok: «🌐 для всех тем» — настройки и стоп-слова меняются во всех темах, каналы свои; выкл — только текущая")

# мастер создания темы: название -> ссылка -> каналы -> привязка канала MAX
bot.resolve_chat_id = lambda link: None
bot.PENDING.clear()
bot.PENDING[1] = "themenew"
hm("Игры")
t3 = bot.THEMES[-1]
assert t3.name == "Игры" and bot.PENDING.get(1) == "thlink" and bot.META["active"]["1"] == t3.id
hm("https://max.ru/games_ch")
assert t3.link == "https://max.ru/games_ch" and t3.chat_id is None and bot.BIND["theme"] == t3.id
assert bot.PENDING.get(1) == "chadd"
hm("@gamesch")
with bot.theme_ctx(t3):
    assert bot.CFG["channels"] == ["gamesch"]
# любое сообщение в канале MAX привязывает его; уже занятый чужой канал не перехватываем
bot.handle_update({"update_type": "message_created",
                   "message": {"recipient": {"chat_type": "channel", "chat_id": 100}, "sender": {}, "body": {}}})
assert t3.chat_id is None
bot.handle_update({"update_type": "message_created",
                   "message": {"recipient": {"chat_type": "channel", "chat_id": 999}, "sender": {}, "body": {}}})
assert t3.chat_id == 999 and bot.BIND["theme"] is None
assert any("привязан" in n for n in NOTES)
print("t7 ok: мастер новой темы и автопривязка канала MAX")

# сохранение на диск и загрузка после перезапуска
bot.save_cfg()
re = load(ROOT + "/tg_to_max.py", "re")
assert [t.name for t in re.THEMES] == ["Основная", "Коты", "Игры"], [t.name for t in re.THEMES]
assert re.THEMES[1].chat_id == 200 and re.THEMES[2].chat_id == 999
with re.theme_ctx(re.THEMES[1]):
    assert re.CFG["channels"][0] == "catmemes" and re.CFG["gap_min"] == 30
print("t8 ok: темы переживают перезапуск")

# удаление темы
with bot.theme_ctx(t3):
    bot.do_action("thdel", 1)
assert t3 not in bot.THEMES and not os.path.exists("queue_t3.json")
assert bot.cur() is bot.THEMES[0]
main_before = len(bot.THEMES)
bot.do_action("thdel", 1)                       # основную удалить нельзя
assert len(bot.THEMES) == main_before
print("t9 ok: тему можно удалить, основную — нет")

# общий отчёт по темам
NOTES.clear(); API.clear()
_img = bot.report_image
SEEN_THEMES = []
bot.report_image = lambda stats, path, *a: (SEEN_THEMES.extend(x["name"] for x in stats), _img(stats, path, *a))[1]
bot.send_report()
assert SEEN_THEMES == ["Основная", "Коты"] and any("Итоги дня" in a[1] for a in API), (SEEN_THEMES, API[-2:])
assert not NOTES and bot.META["last_report"]
bot.report_image = _img
print("t10 ok: один отчёт по всем темам — картинкой, по карточке на тему")


# ---------- чтение каналов ----------
class FMsg:
    def __init__(self, i, age, text="", grouped=None, photo=False, doc_mime=None, video=False):
        self.id, self.text, self.grouped_id = i, text, grouped
        self.date = datetime.datetime.fromtimestamp(CLOCK[0] - age, tz=datetime.timezone.utc)
        self.photo = object() if photo else None
        self.document = types.SimpleNamespace(mime_type=doc_mime) if doc_mime else None
        self.gif = self.video_note = self.sticker = None
        self.video = types.SimpleNamespace(size=1000, attributes=[types.SimpleNamespace(duration=5)]) if video else None
        self.fwd_from = None

    async def download_media(self, file=None):
        open(file, "wb").write(b"img" + str(self.id).encode())
        return file


class FClient:
    def __init__(self, msgs): self.msgs = msgs
    async def get_messages(self, ch, min_id=0, limit=30, ids=None):
        if ids is not None:
            return next((m for m in self.msgs if m.id == ids), None)
        return [m for m in self.msgs if m.id > min_id][:limit]


def poll(client, ch, fails=None, theme=None):
    asyncio.run(bot.process_channel(client, ch, fails if fails is not None else {}))


bot.CFG["approval_on"] = False
bot.QUEUE.clear()
bot.LAST_IDS["robl"] = 10
msgs = [
    FMsg(11, 120, "Альбом с подписью про новый ивент в игре", grouped=5, photo=True),
    FMsg(12, 120, "", grouped=5, photo=True),
    FMsg(13, 100, "Картинка файлом: свежий скин для персонажа", doc_mime="image/png"),
    FMsg(14, 100, "Видео про новый режим в игре", video=True),
    FMsg(15, 2, "Ещё один пост про новости игры", photo=True),
]
cl = FClient(msgs)
poll(cl, "robl")
assert not bot.QUEUE and "robl" in bot.RECHECK, (bot.QUEUE, bot.RECHECK)      # первый раз только запомнили
MONO[0] += 3; poll(cl, "robl"); assert not bot.QUEUE                             # ещё не «устаканилось»
MONO[0] += 10; poll(cl, "robl")
ids = [x["id"] for x in bot.QUEUE]
assert ids == ["robl:11", "robl:13", "robl:14", "robl:15"], ids
alb = bot.QUEUE[0]
assert len(alb["photos"]) == 2 and alb["pids"] == [11, 12] and "ивент" in alb["text"]
assert bot.QUEUE[1]["photos"][0].endswith(".png"), bot.QUEUE[1]["photos"]       # картинка-файл не потерялась
assert bot.QUEUE[2]["vids"] == [14] and bot.LAST_IDS["robl"] == 15
print("r1 ok: альбом склеен, картинка-файл не теряется, пост берётся после паузы «не меняется»")

# альбом ДОЛЕТАЕТ по частям: пока растёт — не берём; считаем по своим часам, даже если даты постов «из будущего»
bot.QUEUE.clear()
future = FMsg(20, -99999, "Альбом про турнир, фото будут приходить по одному", grouped=9, photo=True)   # часы сбиты
cl = FClient([future])
poll(cl, "robl"); assert not bot.QUEUE
MONO[0] += 9
cl.msgs.append(FMsg(21, -99999, "", grouped=9, photo=True))                       # пришло второе фото
poll(cl, "robl"); assert not bot.QUEUE                                              # состав изменился — отсчёт заново
MONO[0] += 9; poll(cl, "robl")
assert len(bot.QUEUE) == 1 and len(bot.QUEUE[0]["photos"]) == 2, bot.QUEUE
print("r2 ok: растущий альбом не режется, сбитые часы (даты «из будущего») не мешают")

# другая тема, тот же канал: свои файлы, свой id, нет конфликта
with bot.theme_ctx(t2):
    bot.CFG["approval_on"] = False
    bot.LAST_IDS["robl"] = 10
    cl2 = FClient(msgs[:2])
    poll(cl2, "robl"); MONO[0] += 10; poll(cl2, "robl")
    it = bot.QUEUE[0]
    assert it["id"].startswith("t2|robl:") and "t2_robl_11" in it["photos"][0]
assert "t2_" not in bot.QUEUE[0]["photos"][0]
print("r3 ok: один канал в двух темах не конфликтует")

# параллельное чтение нескольких каналов одновременно
async def many():
    sem = asyncio.Semaphore(5)
    for ch in ("c1", "c2", "c3"):
        bot.LAST_IDS[ch] = 0
    cl3 = FClient([FMsg(1, 500, "Пост про уникальную тему номер один и что-то ещё важное")])
    for _ in range(2):
        await asyncio.gather(*(bot.poll(cl3, main_t, ch, {}, sem) for ch in ("c1", "c2", "c3")))
        MONO[0] += 10
before = len(bot.QUEUE)
asyncio.run(many())
print("r4 ok: каналы читаются одновременно (поставлено в очередь:", len(bot.QUEUE) - before, ")")

# бот долго был выключен: накопилось 25 постов -> показываем последние 10, остальные пропускаем и говорим об этом
bot.QUEUE.clear(); bot.LAST_IDS["old"] = 0; NOTES.clear(); bot.CFG["dedupe_on"] = False
many_msgs = [FMsg(i, 500, f"Пост номер {i} про совершенно отдельную тему {i * 7}") for i in range(1, 26)]
clb = FClient(many_msgs)
poll(clb, "old"); MONO[0] += 10; poll(clb, "old")
got = [x["id"] for x in bot.QUEUE]
assert got == [f"old:{i}" for i in range(16, 26)], got
assert bot.LAST_IDS["old"] == 25 and any("накопилось много постов" in n for n in NOTES), NOTES
print("r5 ok: после долгого простоя — последние 10 постов, остальные пропущены с уведомлением")

print("\nВСЕ ТЕСТЫ ТЕМ ПРОШЛИ")
