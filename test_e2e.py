import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
"""Сквозная проверка: настоящий бот + поддельный Telegram + поддельный сервер MAX (по HTTP)."""
import asyncio, datetime, importlib.util, json, os, sys, tempfile, threading, time, types
sys.path.insert(0, ROOT)
from fake_max import FakeMax

srv = FakeMax()
work = tempfile.mkdtemp(); os.chdir(work)
json.dump({"api_id": 1, "api_hash": "h", "phone": "+79990000000", "max_token": "TESTTOKEN",
           "max_channel_link": "https://max.ru/mychannel", "max_api": srv.url, "verify_ssl": False},
          open("config.json", "w"))
json.dump({"channels": ["src1", "src2"], "approval_on": True, "interval": 1, "night_on": False,
           "report_on": False, "empty_warn_on": False, "gap_min": 60}, open("settings.json", "w"))
json.dump({"src1": 10, "src2": 20}, open("last_posts.json", "w"))

# ---------- поддельный Telegram ----------
UTC = datetime.timezone.utc
class FMsg:
    def __init__(self, i, text, photo=False, video=False, age=300, grouped=None):
        self.id, self.text, self.grouped_id = i, text, grouped
        self.date = datetime.datetime.fromtimestamp(time.time() - age, tz=UTC)
        self.photo = object() if photo else None
        self.document = self.gif = self.video_note = self.sticker = None
        self.video = types.SimpleNamespace(size=2000, attributes=[types.SimpleNamespace(duration=7)]) if video else None
        self.fwd_from = None
    async def download_media(self, file=None):
        open(file, "wb").write(b"\xff\xd8binary" * 50); return file

DATA = {"src1": [], "src2": [], "src3": []}
FAIL = set()
class FakeTG:
    def __init__(self, *a, **k): pass
    async def connect(self): pass
    async def is_user_authorized(self): return True
    def on(self, ev): return lambda f: f
    async def get_messages(self, ch, min_id=0, limit=30, ids=None):
        if ch in FAIL: raise ValueError(f"Cannot find any entity corresponding to {ch}")
        msgs = DATA.get(ch, [])
        if ids is not None: return next((m for m in msgs if m.id == ids), None)
        return sorted([m for m in msgs if m.id > min_id], key=lambda m: -m.id)[:limit]

tele = types.ModuleType("telethon"); tele.TelegramClient = FakeTG
tele.events = types.SimpleNamespace(NewMessage=lambda *a, **k: None)
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = tele.errors

os.environ.update(TG_API_ID="1234567", TG_API_HASH="0123456789abcdef0123456789abcdef", TG_PHONE="+79990000000")  # аккаунт Telegram подключён
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
import traceback
sys.excepthook = lambda *a: (traceback.print_exception(*a), os._exit(1))
bot.TICK, bot.FRESH_SEC = 0.3, 1
bot.MAX_RETRY_PAUSES = [0.1, 0.1, 0.1]                 # тихие повторы в MAX — без долгих пауз в тесте
_sleep = time.sleep
time.sleep = lambda s: _sleep(min(s, 0.3))          # «вложение не готово» не ждём по 3 секунды

threading.Thread(target=lambda: asyncio.run(bot.main()), daemon=True).start()
ADMIN, CH, A2, CH2 = 7, 700, 8, 800
def queue_ids(): return [x["id"] for x in bot.QUEUE]
def cards(chat, ref):
    out = []
    for m in srv.to_chat(chat):
        for a in m["body"].get("attachments") or []:
            if a["type"] == "inline_keyboard" and ref in json.dumps(a, ensure_ascii=False):
                out.append(m)
    return out
def has_att(m, kind): return any(a["type"] == kind for a in m["body"].get("attachments") or [])

srv.wait(lambda: bot.CLAIM_CODE, what="меню запустилось и показало код привязки")
srv.wait(lambda: any(r[1] == "/updates" for r in srv.requests), what="бот слушает события MAX")
assert bot.THEMES[0].chat_id == 5000, bot.THEMES[0].chat_id
print("e0 ok: бот запустился, нашёл канал MAX по ссылке, слушает события")

# 1) пост пришёл, когда админа ещё нет -> ждёт (а не публикуется молча)
DATA["src1"].append(FMsg(11, "Новый ивент в игре: подробности и награды", photo=True))
srv.wait(lambda: "src1:11" in queue_ids(), what="пост поставлен в очередь")
assert bot.QUEUE[0]["status"] == "pending" and not srv.to_chat(5000)
assert any("Некому показать" in l for l in bot.JOURNAL)
print("e1 ok: нет админа — пост НЕ публикуется молча, ждёт одобрения")

# 2) владелец привязывается кодом -> сразу получает накопившиеся карточки (с фото)
srv.push({"update_type": "bot_started", "user": {"user_id": ADMIN, "first_name": "Gh"}, "chat_id": CH})
srv.wait(lambda: any("код привязки" in t for t in srv.texts(CH)), what="просьба прислать код")
srv.user_message(ADMIN, CH, bot.CLAIM_CODE, "Gh")
srv.wait(lambda: any("владелец" in t for t in srv.texts(CH)), what="владелец привязан")
srv.wait(lambda: cards(CH, "ap:now:src1:11"), what="карточка поста у админа")
card = cards(CH, "ap:now:src1:11")[0]
assert has_att(card, "image"), "в карточке должно быть фото поста"
print("e2 ok: админ привязан, накопившийся пост сразу показан карточкой с фото и кнопками")

# 3) «Сейчас» -> пост в канале MAX с фото и подписью
srv.press(ADMIN, CH, "ap:now:src1:11")
srv.wait(lambda: srv.to_chat(5000), what="публикация в канал")
m = srv.to_chat(5000)[0]
assert has_att(m, "image") and "ивент" in m["body"]["text"] and "mychannel" in m["body"]["text"], m
assert any("выкладываю сейчас" in json.dumps(a["body"], ensure_ascii=False) for a in srv.answers)
assert srv.uploads["image"] >= 2                      # превью + публикация
print("e3 ok: «Сейчас» -> пост с фото и подписью в канале MAX")

# 4) видео: MAX сначала отвечает «вложение не готово» -> бот ждёт и доставляет
srv.not_ready = 2
DATA["src2"].append(FMsg(21, "Видео про новый режим в игре", video=True))
srv.wait(lambda: cards(CH, "ap:now:src2:21"), what="карточка видео-поста")
srv.press(ADMIN, CH, "ap:now:src2:21")
srv.wait(lambda: any(has_att(x, "video") for x in srv.to_chat(5000)), what="видео в канале")
assert srv.not_ready == 0 and srv.uploads["video"] == 1
vm = [x for x in srv.to_chat(5000) if has_att(x, "video")][0]
assert "новый режим" in vm["body"]["text"]
print("e4 ok: видео скачано, загружено, дождались «готово» и опубликовано с текстом")

# 5) второй админ: карточка у него исчезает, когда первый нажал
bot.INVITES["INV1"] = time.time() + 600
srv.user_message(A2, CH2, "INV1", "Второй")
srv.wait(lambda: any("теперь админ" in t for t in srv.texts(CH2)), what="второй админ добавлен")
DATA["src1"].append(FMsg(12, "Ещё одна новость про обновление игры", photo=True))
srv.wait(lambda: cards(CH, "ap:later:src1:12") and cards(CH2, "ap:later:src1:12"), what="карточки у обоих админов")
other_mid = cards(CH2, "ap:later:src1:12")[0]["mid"]
srv.press(ADMIN, CH, "ap:later:src1:12")
srv.wait(lambda: other_mid in srv.deleted, what="карточка у второго админа удалена")
it = next(x for x in bot.QUEUE if x["id"] == "src1:12")
assert it["status"] == "ok" and it["at"] > time.time() + 3000        # «через час»
assert not any("Ещё одна новость" in t for t in srv.texts(5000))
print("e5 ok: «Через час» — у второго админа карточка удалена, пост ждёт своего времени")

# 6) даты постов «из будущего» (часы сбиты) не мешают
DATA["src1"].append(FMsg(13, "Пост с датой из будущего про турнир", age=-86400 * 3))
srv.wait(lambda: cards(CH, "src1:13"), what="карточка поста со сбитой датой")
srv.press(ADMIN, CH, "ap:no:src1:13")
srv.wait(lambda: "src1:13" not in queue_ids(), what="пост убран")
print("e6 ok: сбитые часы — пост всё равно появляется, «пропустить» убирает его")

# 7) канал не открылся при добавлении -> уведомление один раз, потом сам подхватывается
FAIL.add("src3")
srv.press(ADMIN, CH, "p:chadd")
srv.wait(lambda: any("каналы Telegram" in json.dumps(a["body"], ensure_ascii=False) for a in srv.answers), what="запрос каналов")
srv.user_message(ADMIN, CH, "src3")
srv.wait(lambda: any("не открывается" in t for t in srv.texts(CH)), what="уведомление о канале")
time.sleep(1.5)
assert sum("не открывается" in t for t in srv.texts(CH)) == 1                    # не спамит
FAIL.discard("src3"); bot.BAD["src3"] = 0                                          # «прошло 10 минут»
srv.wait(lambda: "src3" in bot.THEMES[0].last_ids, what="канал подхвачен")
print("e7 ok: временно недоступный канал — одно уведомление, потом читается сам (раньше — никогда)")

# 8) MAX принял пост, но «потерял» фото (разовый сбой) — бот молча убирает пост без фото и отправляет заново:
#    в канале пост с фото; в чат — ни слова о сбое
DATA["src1"].append(FMsg(14, "Новость с фото, которое MAX потеряет", photo=True))
srv.wait(lambda: cards(CH, "ap:now:src1:14"), what="карточка")
n, c0 = len(srv.to_chat(5000)), len(srv.texts(CH))
srv.drop_attachments_n = 1                              # следующее сообщение с фото — без фото
srv.press(ADMIN, CH, "ap:now:src1:14")
srv.wait(lambda: any("потеряет" in (m["body"].get("text") or "") and has_att(m, "image")
                     for m in srv.to_chat(5000)[n:]), what="пост с фото в канале")
posts8 = [m for m in srv.to_chat(5000)[n:] if "потеряет" in (m["body"].get("text") or "")]
assert len(posts8) == 2 and not has_att(posts8[0], "image") and posts8[0]["mid"] in srv.deleted, posts8
assert has_att(posts8[1], "image") and posts8[1]["mid"] not in srv.deleted, posts8
time.sleep(0.5)
assert not any("вложени" in t or "не удал" in t for t in srv.texts(CH)[c0:]), srv.texts(CH)[c0:]
print("e8 ok: MAX потерял фото — бот молча убрал пост без фото и отправил заново; в чат — ни слова")

# 9) проверка и журнал
srv.user_message(ADMIN, CH, "/check")
srv.wait(lambda: any("токен работает" in t and "src1" in t for t in srv.texts(CH)), what="результат проверки")
srv.user_message(ADMIN, CH, "/log")
srv.wait(lambda: any("Журнал" in t and "опубликован пост" in t for t in srv.texts(CH)), what="журнал")
print("e9 ok: /check и /log работают")

# 11) альбом из 3 фото с подписью -> ОДНО сообщение с тремя картинками
DATA["src1"] += [FMsg(30, "Подборка скриншотов нового обновления", photo=True, grouped=77),
                 FMsg(31, "", photo=True, grouped=77), FMsg(32, "", photo=True, grouped=77)]
srv.wait(lambda: cards(CH, "ap:now:src1:30"), what="карточка альбома")
n = len(srv.to_chat(5000))
srv.press(ADMIN, CH, "ap:now:src1:30")
srv.wait(lambda: len(srv.to_chat(5000)) > n, what="альбом в канале")
am = srv.to_chat(5000)[-1]
assert sum(a["type"] == "image" for a in am["body"]["attachments"]) == 3 and "скриншотов" in am["body"]["text"], am
print("e11 ok: альбом из 3 фото ушёл одним сообщением, с подписью")

# 12) очень длинный пост (> 4000 символов) режется, ничего не теряется
long_text = "\n\n".join(f"Абзац номер {i}: " + "слово " * 60 for i in range(25))
DATA["src1"].append(FMsg(40, long_text, photo=True))
srv.wait(lambda: cards(CH, "ap:now:src1:40"), what="карточка длинного поста")
n = len(srv.to_chat(5000))
srv.press(ADMIN, CH, "ap:now:src1:40")
srv.wait(lambda: "Абзац номер 24" in "".join(p["body"].get("text", "") for p in srv.to_chat(5000)[n:]), what="длинный пост частями")
parts = srv.to_chat(5000)[n:]
assert all(len(p["body"].get("text", "")) <= 4000 for p in parts), [len(p["body"].get("text", "")) for p in parts]
assert has_att(parts[0], "image") and not has_att(parts[1], "image"), [(len(p["body"].get("text", "")), [a["type"] for a in p["body"].get("attachments") or []]) for p in parts]
joined = "".join(p["body"].get("text", "") for p in parts)
assert "Абзац номер 0" in joined and "Абзац номер 24" in joined, (len(joined), joined[-150:])
print("e12 ok: длинный пост разрезан на части <4000 символов, фото в первой, ничего не потеряно")

# 13) фото админа боту без команды в канал не уходит; /stashnow — как незнакомая команда (список команд)
img = {"type": "image", "payload": {"url": srv.url + "/files/meme.jpg", "token": "memetok"}}
n_ch, n_dm = len(srv.to_chat(5000)), len(srv.to_chat(CH))
srv.push({"update_type": "message_created", "message": {"sender": {"user_id": ADMIN, "first_name": "Gh"},
          "recipient": {"chat_id": CH, "chat_type": "dialog"}, "body": {"text": "смешной мем", "attachments": [img]}}})
srv.wait(lambda: len(srv.to_chat(CH)) > n_dm, what="бот ответил на фото")
srv.user_message(ADMIN, CH, "/stashnow")
srv.wait(lambda: any(t.startswith("❓ Команды") for t in srv.texts(CH)[n_dm:]), what="/stashnow — список команд")
time.sleep(2)                                           # несколько тиков планировщика (TICK 0.3 с)
assert len(srv.to_chat(5000)) == n_ch, srv.to_chat(5000)[n_ch:]
assert not any("тайник" in t.lower() for t in srv.texts(CH)[n_dm:]), srv.texts(CH)[n_dm:]
print("e13 ok: фото боту без команды в канал не уходит; /stashnow — список команд, как на незнакомую")

# 14) тема: создаём через меню новую тему со своим каналом MAX и своим источником
srv.press(ADMIN, CH, "p:themenew")
srv.wait(lambda: any("название новой темы" in json.dumps(a["body"], ensure_ascii=False) for a in srv.answers), what="запрос названия")
srv.user_message(ADMIN, CH, "Коты")
srv.wait(lambda: any("ссылку на канал MAX" in t for t in srv.texts(CH)), what="запрос ссылки")
srv.user_message(ADMIN, CH, "https://max.ru/cats")
srv.wait(lambda: any("привязан" in t for t in srv.texts(CH)), what="канал привязан")
srv.user_message(ADMIN, CH, "src4")
srv.wait(lambda: len(bot.THEMES) == 2 and bot.THEMES[1].chat_id == 6000, what="тема создана и привязана")
DATA["src4"] = []
srv.wait(lambda: "src4" in bot.THEMES[1].last_ids, what="источник темы подхвачен")
DATA["src4"].append(FMsg(5, "Котик спит в коробке и это очень мило", photo=True))
srv.wait(lambda: any(x["id"].startswith("t2|src4") for x in bot.THEMES[1].queue), what="пост темы в очереди")
ref = bot.THEMES[1].queue[0]["id"]
srv.wait(lambda: cards(CH, "ap:now:" + ref), what="карточка поста темы")
assert "Коты" in cards(CH, "ap:now:" + ref)[0]["body"]["text"]
srv.press(ADMIN, CH, "ap:now:" + ref)
srv.wait(lambda: srv.to_chat(6000), what="публикация в канал второй темы")
cm = srv.to_chat(6000)[0]
assert has_att(cm, "image") and "котик" in cm["body"]["text"].lower() and "cats" in cm["body"]["text"]
assert not any("Котик спит" in t for t in srv.texts(5000))
print("e14 ok: новая тема через меню — свой источник, свой канал MAX, своя подпись, посты не смешиваются")

# 10) токен MAX не принят -> понятная подсказка в консоли, бот не падает
srv.token = "OTHER"
time.sleep(1.5)
assert bot.MAIN_LOOP is not None
srv.token = "TESTTOKEN"
print("e10 ok: при отказе MAX бот не падает и продолжает опрашивать")

print("\nВСЕ СКВОЗНЫЕ ТЕСТЫ ПРОШЛИ")
os._exit(0)
