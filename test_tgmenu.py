import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
"""Подключение аккаунта Telegram из меню MAX: ключи, номер, код, пароль — без консоли и без перезапуска."""
import asyncio, importlib.util, os, sys, tempfile, threading, types

# --- поддельный telethon: код 12345, у второго аккаунта облачный пароль ---
class E:
    class SessionPasswordNeededError(Exception): pass
    class PhoneCodeInvalidError(Exception): pass
    class PhoneCodeExpiredError(Exception): pass
    class PasswordHashInvalidError(Exception): pass
class SentCodeTypeApp: pass
CLIENTS, PASSWORD = [], {"value": None}

class FakeTG:
    def __init__(self, session, api_id, api_hash):
        self.session, self.api_id, self.api_hash = session, api_id, api_hash
        self.authorized = session == "session" and os.path.exists("session.session") \
            and tio.rfile("session.session") == "AUTH"
        self.handlers, self.codes = [], 0
        CLIENTS.append(self)
    async def connect(self): open(self.session + ".session", "a").close()
    async def disconnect(self): pass
    async def is_user_authorized(self): return self.authorized
    async def send_code_request(self, phone):
        self.codes += 1
        return types.SimpleNamespace(type=SentCodeTypeApp())
    async def sign_in(self, phone=None, code=None, password=None):
        if password is not None:
            if password != PASSWORD["value"]: raise E.PasswordHashInvalidError()
        elif code != "12345":
            raise E.PhoneCodeInvalidError()
        elif PASSWORD["value"]:
            raise E.SessionPasswordNeededError()
        self.authorized = True
        tio.wfile("AUTH", self.session + ".session", "w")
    def add_event_handler(self, f, ev): self.handlers.append(f)
    async def get_messages(self, ch, min_id=0, limit=30, ids=None): return []

tele = types.ModuleType("telethon"); tele.TelegramClient = FakeTG; tele.errors = E
tele.events = types.SimpleNamespace(NewMessage=lambda *a, **k: None)
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = E
os.chdir(tempfile.mkdtemp())
os.environ["TGMAX_NO_DESKTOP_KEYS"] = "1"          # старый путь — со своими ключами; ключи Desktop — тест g13
tio.wjson({"max_token": "T", "max_channel_link": "https://max.ru/x"}, "config.json", "w")
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
assert not bot.TG_READY

API, DELETED = [], []
bot.api_send = lambda chat, text, rows=None, token=None: API.append((text, rows))
bot.api_answer = lambda cid, text, rows: API.append((text, rows))
bot.delete_msg = lambda mid: DELETED.append(mid)
bot.CFG["admins"] = [1, 2]
bot.CFG["admin_chats"] = {"1": 111, "2": 222}

loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
bot.MAIN_LOOP = loop
bot.TG_CLIENT = bot.ClientBox(bot.WebClient())
bot.ON_NEW = lambda ev: None

def press(payload, uid=1):
    bot.handle_callback({"user": {"user_id": uid}, "callback_id": "c", "payload": payload})
    return API[-1]

mid = [0]
def say(text, uid=1):
    mid[0] += 1
    bot.handle_message(uid, 111, "Я", text, {"body": {"text": text, "mid": f"m{mid[0]}"}})
    return API[-1]

def buttons(rows):
    return [b["payload"] for row in rows or [] for b in row]

# 1) в главном меню есть «🔑 Telegram», экран говорит, что не подключён
text, rows = bot.screen("main", 1)
assert "tg:menu" in buttons(rows)
text, rows = press("tg:menu")
assert "не подключён" in text and "my.telegram.org" in text and "tg:start" in buttons(rows), text
print("g1 ok: в меню MAX есть «🔑 Telegram» — не подключён, объясняет, что понадобится")

# 2) не владелец подключать не может
text, rows = press("tg:start", uid=2)
assert "только владелец" in text and "tg:start" not in buttons(rows) and 2 not in bot.PENDING, text
print("g2 ok: подключать может только владелец")

# 3) полный путь: api_id (с ошибкой) -> api_hash -> номер -> неверный код -> верный код
text, rows = press("tg:start")
assert "Шаг 1 из 4" in text and "tg:cancel" in buttons(rows) and "tg:keep" not in buttons(rows)
text, _ = say("abc")
assert "Не похоже на api_id" in text
text, _ = say("1234567")
assert "Шаг 2 из 4" in text
text, _ = say("0123456789abcdef0123456789abcdef")
assert "Шаг 3 из 4" in text and "m3" in DELETED                       # api_hash из чата удалён
text, rows = say("8 999 000-11-22")
assert "Шаг 4 из 4" in text and "приложение" in text.lower() and "tg:resend" in buttons(rows), text
text, _ = say("99999")
assert "Неверный код" in text and bot.PENDING.get(1) == "tg_code"
text, rows = press("tg:resend")
assert "Отправил повторно" in text and bot.TG_LOGIN["client"].codes == 2, text
text, rows = say("1 2 3 4 5")
assert "Telegram подключён" in text, text
assert {"m5", "m6"} <= set(DELETED)                                    # коды из чата удалены
cfg = tio.rjson("config.json")
assert cfg["api_id"] == 1234567 and cfg["phone"] == "+79990001122" and cfg["max_token"] == "T", cfg
assert tio.rfile("session.session") == "AUTH" and not os.path.exists("login_tmp.session")
cur = bot.TG_CLIENT.cur
assert isinstance(cur, FakeTG) and cur.session == "session" and cur.authorized and cur.handlers
assert bot.TG_READY and bot.PHONE == "+79990001122" and 1 not in bot.PENDING
text, rows = press("tg:menu")
assert "подключён ✅" in text and "1122" in text and "0001122" not in text, text
print("g3 ok: ключи, номер, код — через MAX; секреты из чата удалены, config.json и вход сохранены, "
      "бот сразу читает через аккаунт")

# 4) другой аккаунт с облачным паролем; сохранённые ключи и номер можно оставить
PASSWORD["value"] = "pw"
text, rows = press("tg:start")
assert "tg:keep" in buttons(rows)
text, rows = press("tg:keep")
assert "Шаг 3 из 4" in text and "tg:keepphone" in buttons(rows), text
text, rows = press("tg:keepphone")
assert "Шаг 4 из 4" in text, text
text, _ = say("12345")
assert "облачный пароль" in text and bot.PENDING.get(1) == "tg_password"
text, _ = say("wrong")
assert "Неверный пароль" in text
n = len(DELETED)
text, _ = say("pw")
assert "Telegram подключён" in text and len(DELETED) == n + 1, text
print("g4 ok: облачный пароль, «оставить сохранённые ключи/номер», пароль из чата удалён")

# 5) отмена посреди ввода
press("tg:start"); say("1234567")
text, rows = press("tg:cancel")
assert 1 not in bot.PENDING and not bot.TG_LOGIN and "подключён" in text
print("g5 ok: отмена")

# 6) Telegram не дал код (просит подождать) — понятное сообщение и «начать заново»
class Flood(Exception):
    seconds = 3600
async def flood(self, phone): raise Flood()
FakeTG.send_code_request = flood
press("tg:start"); press("tg:keep")
text, rows = press("tg:keepphone")
assert "подождать 1 ч" in text and "tg:start" in buttons(rows) and 1 not in bot.PENDING, text
print("g6 ok: Telegram просит подождать — пишет сколько, вход не ломается")
# 7) код админа из консоли: новый при каждом запуске, одноразовый
bot.notify = lambda *a, **k: None
bot.new_console_code()
code = bot.CONSOLE_CODE["code"]
bot.handle_message(7, 777, "Гость", "123", {"body": {"text": "123"}})
assert 7 not in bot.CFG["admins"] and "код админа из консоли" in API[-1][0]
bot.handle_message(7, 777, "Гость", code, {"body": {"text": code}})
assert 7 in bot.CFG["admins"] and bot.CFG["admins"][0] == 1 and bot.CONSOLE_CODE["code"] != code
bot.handle_message(8, 888, "Чужой", code, {"body": {"text": code}})
assert 8 not in bot.CFG["admins"]
print("g7 ok: код админа из консоли — делает админом, владелец не меняется, код одноразовый")
# 7б) ADMIN_CODE из переменных окружения хостинга: первый — владелец, дальше — админ
os.environ["ADMIN_CODE"] = "secret777"
saved = list(bot.CFG["admins"]); bot.CFG["admins"].clear()
bot.handle_message(30, 300, "Я", "secret777", {"body": {"text": "secret777"}})
assert bot.CFG["admins"] == [30] and "владелец" in API[-2][0], API[-2:]
bot.handle_message(31, 310, "Друг", "secret777", {"body": {"text": "secret777"}})
assert bot.CFG["admins"] == [30, 31]
bot.handle_message(32, 320, "Чужой", "secret77", {"body": {"text": "secret77"}})
assert 32 not in bot.CFG["admins"]
bot.CFG["admins"][:] = saved; del os.environ["ADMIN_CODE"]
print("g7б ok: ADMIN_CODE — свой код из настроек хостинга, логи не нужны")

# 8) обрывы связи с MAX (мобильный интернет): 1–2 подряд — молча, 3 — одна строка, потом «восстановлена»
import builtins, requests, time as _time
class Ok:
    status_code = 200
    def json(self): return {"marker": 1, "updates": []}
seq = [requests.ConnectionError("Remote end closed connection")] * 2 + [Ok()] \
    + [requests.ConnectionError("Software caused connection abort")] * 4 + [Ok(), KeyboardInterrupt()]
def fake_get(*a, **k):
    x = seq.pop(0)
    if isinstance(x, BaseException): raise x
    return x
bot.S.get, bot.register_commands = fake_get, lambda: None
_sleep, _time.sleep = _time.sleep, lambda s: None
out, _p = [], builtins.print
builtins.print = lambda *a, **k: out.append(" ".join(map(str, a)))
try:
    bot.menu_loop()
except KeyboardInterrupt:
    pass
finally:
    builtins.print, _time.sleep = _p, _sleep
txt = "\n".join(out)
assert "сбой связи" not in txt and txt.count("нет связи с MAX") == 1 and "восстановлена" in txt, txt
assert "Remote end closed" in tio.rfile("bot.log", encoding="utf-8")       # подробности — в bot.log
print("g8 ok: разовые обрывы связи с MAX не засоряют консоль, долгий обрыв — одна строка и «восстановлена»")
# 9) t.me заблокирован (сервер в РФ): запасной адрес telegram.me, пауза растёт, одна строка в лог, одно сообщение админам
calls = []
class R:
    status_code = 200; text = "<html>ok</html>"
def ws_get(url, **k):
    calls.append(url)
    if "//t.me/" in url:
        raise requests.ConnectionError("Network is unreachable")
    return R()
bot.WS.get = ws_get
assert bot.web_fetch("https://t.me/s/chan") == "<html>ok</html>" and calls == ["https://t.me/s/chan", "https://telegram.me/s/chan"]
def ws_dead(url, **k):
    raise requests.Timeout("Read timed out")
bot.WS.get = ws_dead
try:
    bot.web_fetch("https://t.me/s/chan"); raise SystemExit("должно было упасть")
except requests.Timeout:
    pass
NOTES = []
bot.notify = lambda text, tag=True, rows=None: NOTES.append(text)
out, _p = [], builtins.print
builtins.print = lambda *a, **k: out.append(" ".join(map(str, a)))
try:
    for i in range(4):
        bot.note_net("chan", requests.Timeout("Read timed out"))
    assert bot.net_blocked("chan") and bot.NET["until"]["chan"] - _time.time() > 400       # пауза выросла
    assert not NOTES                                                                      # первые 3 минуты молчим
    bot.NET["since"]["chan"] -= 200
    bot.note_net("chan", requests.Timeout("Read timed out"))
    bot.note_net("chan", requests.Timeout("Read timed out"))
    assert len(NOTES) == 1 and "Нидерланды" in NOTES[0] and "TG_PROXY" in NOTES[0], NOTES
    bot.note_net("chan")                                                                  # заработало
    assert not bot.net_blocked("chan") and "chan" not in bot.NET["since"] and not bot.NET["warned"]
finally:
    builtins.print = _p
assert sum("t.me не отвечает" in l for l in out) == 1 and sum("снова отвечает" in l for l in out) == 1, out
print("g9 ok: t.me недоступен — пробует telegram.me, пауза растёт, в логе одна строка, админам одно понятное сообщение")
# 10) аккаунт Telegram отвалился после долгого обрыва — бот сам переподключает
class Disc:
    connects = 0
    def is_connected(self): return False
    async def connect(self): Disc.connects += 1
asyncio.run(bot.reconnect_tg(bot.ClientBox(Disc())))
asyncio.run(bot.reconnect_tg(bot.ClientBox(bot.WebClient())))
assert Disc.connects == 1
print("g10 ok: аккаунт Telegram после обрыва переподключается сам")
# 11) вход по QR (код не приходит): картинка уходит в MAX, просроченный QR заменяется новым, скан — вход
import datetime as _datetime
QRS = []
class FakeQR:
    def __init__(self, c): self.c, self.n = c, 0; self.url = "tg://login?token=AAA"
    @property
    def expires(self): return _datetime.datetime.now(_datetime.timezone.utc) + _datetime.timedelta(seconds=30)
    async def wait(self, timeout=None):
        QRS.append(self.url)
        if len(QRS) == 1:
            raise asyncio.TimeoutError()               # первый QR не успели отсканировать
        if PASSWORD["value"]:
            raise E.SessionPasswordNeededError()
        self.c.authorized = True
        tio.wfile("AUTH", self.c.session + ".session", "w")
    async def recreate(self): self.url = "tg://login?token=BBB"
async def qr_login(self): return FakeQR(self)
async def get_me(self): return types.SimpleNamespace(phone="79990005566")
FakeTG.qr_login, FakeTG.get_me = qr_login, get_me
UPLOADS = []
bot.upload_image = lambda path: (UPLOADS.append(tio.rfile(path, "rb")[:8]), "imgtok")[1]
TOKS = []
bot.api_send = lambda chat, text, rows=None, token=None: (API.append((text, rows)), TOKS.append(token))
PASSWORD["value"] = None
for f in ("session.session", "config.json"):
    if os.path.exists(f): os.remove(f)
tio.wjson({"max_token": "T", "max_channel_link": "https://max.ru/x"}, "config.json", "w")
bot.CONFIG.update(api_id=0, api_hash="", phone="")
bot.TG_LOGIN.clear(); bot.PENDING.clear(); API.clear()
text, rows = press("tg:menu")
assert "tg:qr" in buttons(rows), text
text, rows = press("tg:qr")
assert "только api_id и api_hash" in text, text           # ключей нет — сначала ключи, без номера и кода
say("1234567")
text, rows = say("0123456789abcdef0123456789abcdef")
assert "Готовлю QR" in text, text
deadline = _time.time() + 10
while _time.time() < deadline and not any("Telegram подключён" in (m[0] or "") for m in API):
    _time.sleep(0.1)
assert any("Telegram подключён" in (m[0] or "") for m in API), [m[0][:40] for m in API[-5:]]
assert QRS == ["tg://login?token=AAA", "tg://login?token=BBB"] and len(UPLOADS) == 2, (QRS, UPLOADS)
assert all(u == b"\x89PNG\r\n\x1a\n" for u in UPLOADS)                          # настоящая картинка PNG
assert TOKS.count("imgtok") == 2
cfg = tio.rjson("config.json")
assert cfg["api_id"] == 1234567 and cfg["phone"] == "+79990005566" and cfg["max_token"] == "T", cfg
assert isinstance(bot.TG_CLIENT.cur, FakeTG) and bot.TG_CLIENT.cur.authorized and not os.path.exists("tg_login_qr.png")
print("g11 ok: вход по QR — картинка в MAX, новый QR вместо просроченного, вход без кода, номер взят из аккаунта")

# 12) вход по QR с облачным паролем: после скана бот спрашивает пароль
PASSWORD["value"] = "pw"; QRS[:] = ["x"]; API.clear()     # первый wait сразу «сканирован»
text, rows = press("tg:qr")                               # ключи уже сохранены — сразу QR
deadline = _time.time() + 10
while _time.time() < deadline and bot.PENDING.get(1) != "tg_password":
    _time.sleep(0.1)
assert bot.PENDING.get(1) == "tg_password", API[-2:]
text, _ = say("pw")
assert "Telegram подключён" in text, text
print("g12 ok: вход по QR + облачный пароль")
assert any("tg://login?token=" in (m[0] or "") and "этом же телефоне" in (m[0] or "") for m in API)

# 12б) бот покупателя: ключи продавца встроены — только номер и код, my.telegram.org не нужен
os.environ.update(TGMAX_API_ID="7654321", TGMAX_API_HASH="fedcba9876543210fedcba9876543210")
bot.TG_CLIENT = bot.WebClient(); bot.TG_LOGIN.clear(); bot.PENDING.clear()
text, rows = press("tg:menu")
assert "Сайты и ключи не нужны" in text and "my.telegram.org" not in text, text
text, rows = press("tg:start")
assert "Шаг 1 из 2" in text and "номер" in text and bot.TG_LOGIN["api_id"] == 7654321, text
press("tg:cancel")
for k in ("TGMAX_API_ID", "TGMAX_API_HASH"):
    os.environ.pop(k)
# 13) без my.telegram.org: ключи Telegram Desktop встроены — вход только номером и кодом, клиент «как Desktop»
os.environ.pop("TGMAX_NO_DESKTOP_KEYS")
KW = []
class DeskTG(FakeTG):
    def __init__(self, session, api_id, api_hash, **kw):
        KW.append((api_id, kw)); super().__init__(session, api_id, api_hash)
    async def send_code_request(self, phone):
        return types.SimpleNamespace(type=SentCodeTypeApp())
bot.TelegramClient = DeskTG
bot.TG_LOGIN.clear(); bot.PENDING.clear()
text, rows = press("tg:menu")
assert "Сайты и ключи не нужны" in text and "tg:own" in buttons(rows), text
text, rows = press("tg:start")
assert "Шаг 1 из 2" in text and bot.TG_LOGIN["api_id"] == 2040, text
text, rows = say("+79990001122")
assert "Шаг 2 из 2" in text, text
assert KW[-1][0] == 2040 and KW[-1][1].get("system_version") == "Windows 10", KW[-1]
press("tg:cancel")
text, rows = press("tg:own")
assert "Шаг 1 из 4" in text and "api_id" in text, text
press("tg:cancel")
print("g13 ok: без my.telegram.org — встроенные ключи Telegram Desktop, только номер и код; свои ключи — по кнопке")

# 14) бот покупателя читает каналы через бота продавца (вход только у продавца — Telegram не рвёт связь)
class Doc:
    def __init__(self, size, mime, dur=0):
        self.size, self.mime_type = size, mime
        self.attributes = [types.SimpleNamespace(duration=dur)] if dur else []
class TMsg:
    def __init__(self, mid, text="", photo=False, doc=None, video=False, gid=None, name=None):
        self.id, self.text, self.grouped_id = mid, text, gid
        self.date = _datetime.datetime(2026, 10, 23, tzinfo=_datetime.timezone.utc)
        self.photo = object() if photo else None
        self.document = doc
        self.video = doc if video else None
        self.gif = self.video_note = self.sticker = self.fwd_from = None
        self.file = types.SimpleNamespace(name=name, ext=".pdf" if name else ".jpg")
    async def download_media(self, file=None):
        p = file + f"m{self.id}.bin" if file.endswith("/") else file
        tio.wfile(b"DATA%d" % self.id, p, "wb")
        return p
class HubTG(FakeTG):
    MSGS = [TMsg(5, "альбом", photo=True, gid=77), TMsg(6, "", photo=True, gid=77),
            TMsg(7, "схема", doc=Doc(9, "application/pdf"), name="Схема.pdf"),
            TMsg(8, "видео", doc=Doc(50, "video/mp4", 12), video=True)]
    async def get_messages(self, ch, min_id=0, limit=30, ids=None):
        if ids is not None:
            return next((m for m in self.MSGS if m.id == ids), None)
        return sorted([m for m in self.MSGS if m.id > min_id], key=lambda m: -m.id)[:limit]
seller = HubTG("session", 2040, "x")
bot.TG_CLIENT = bot.ClientBox(seller)
bot.GCFG["share_tg"] = True
bot.hub_start()
os.environ.update(TGMAX_HUB=bot.HUB["url"], TGMAX_HUB_KEY=bot.HUB["key"])
bot.CLIENT_MODE = True
rc = asyncio.run_coroutine_threadsafe(bot.shared_client(), loop).result(10)
assert isinstance(rc, bot.RemoteClient) and bot.SHARED["on"]
got = asyncio.run_coroutine_threadsafe(rc.get_messages("chan", min_id=4), loop).result(10)
assert [m.id for m in got] == [8, 7, 6, 5] and got[3].grouped_id == 77 and got[3].text == "альбом"
by = {m.id: m for m in got}
assert bot.has_image(by[5]) and not bot.doc_info(by[5])
assert bot.doc_info(by[7]) == {"id": 7, "name": "Схема.pdf", "size": 9} and not bot.has_image(by[7])
assert bot.video_info(by[8]) == (50, 12) and not bot.doc_info(by[8])
one = asyncio.run_coroutine_threadsafe(rc.get_messages("chan", ids=7), loop).result(10)
p = asyncio.run_coroutine_threadsafe(one.download_media(file=os.path.abspath("dl_test.pdf")), loop).result(10)
assert tio.rfile(p, "rb") == b"DATA7"
left_tmp = lambda: [d for d in os.listdir(".") if d.startswith("tmp")]
deadline = _time.time() + 5         # продавец убирает свою временную папку сразу после отправки файла — ждём её
while left_tmp() and _time.time() < deadline:
    _time.sleep(0.05)
assert not left_tmp(), left_tmp()
bot.TG_CLIENT = bot.ClientBox(rc)
text, rows = press("tg:menu")
assert "общий аккаунт" in text and "Ничего делать не нужно" in text, text
bad = bot.RemoteClient(bot.HUB["url"], "wrong")
assert not bad.ping()                                   # чужой без ключа — не пускаем
bot.TG_CLIENT = bot.ClientBox(bot.WebClient())          # у продавца нет входа — клиенты читают через t.me
bot.SHARED["on"] = False
assert asyncio.run_coroutine_threadsafe(bot.shared_client(), loop).result(10) is None
bot.CLIENT_MODE = False
for k in ("TGMAX_HUB", "TGMAX_HUB_KEY"):
    os.environ.pop(k)
print("g14 ok: бот покупателя читает каналы и качает файлы через бота продавца; без ключа — не пускает")

# 15) Telegram можно отключить: вход завершается, файл входа стёрт, бот читает через t.me
LOGOUTS = []
class OutTG(FakeTG):
    async def log_out(self): LOGOUTS.append(self.session)
tio.wfile("AUTH", "session.session", "w")
bot.TelegramClient = OutTG
bot.TG_CLIENT = bot.ClientBox(OutTG("session", 2040, "x"))
bot.SHARED["on"] = False
text, rows = press("tg:menu")
assert "подключён ✅" in text and "tg:off" in buttons(rows), text
text, rows = press("tg:off")
assert "Отключить аккаунт Telegram?" in text and "tg:offok" in buttons(rows), text
text, rows = press("tg:offok")
assert "Telegram отключён" in text and "tg:start" in buttons(rows), text
assert LOGOUTS == ["session"] and not os.path.exists("session.session") and bot.is_web_client(bot.TG_CLIENT)
print("g15 ok: «🚪 Отключить Telegram» — вход завершён в Telegram, файл входа удалён, бот читает через t.me")

# 16) покупатель может отключить общий аккаунт продавца и включить обратно
bot.TG_CLIENT = bot.ClientBox(seller)
bot.hub_start()
os.environ.update(TGMAX_HUB=bot.HUB["url"], TGMAX_HUB_KEY=bot.HUB["key"])
bot.CLIENT_MODE = True
rc = asyncio.run_coroutine_threadsafe(bot.shared_client(), loop).result(10)
bot.TG_CLIENT = bot.ClientBox(rc)
text, rows = press("tg:menu")
assert "общий аккаунт" in text and "tg:off" in buttons(rows), text
text, rows = press("tg:offok")
assert "Telegram отключён" in text and "tg:shareon" in buttons(rows) and os.path.exists("no_shared.flag"), text
assert bot.is_web_client(bot.TG_CLIENT) and LOGOUTS == ["session"]       # вход продавца не трогали
assert asyncio.run_coroutine_threadsafe(bot.shared_client(), loop).result(10) is None
bot.TG_CLIENT = bot.ClientBox(seller)       # в тесте продавец и клиент в одном процессе: сервис читает через seller
text, rows = press("tg:shareon")
assert "Снова читаю через общий аккаунт" in text and not os.path.exists("no_shared.flag"), text
assert isinstance(bot.TG_CLIENT.cur, bot.RemoteClient)
bot.CLIENT_MODE = False; bot.SHARED["on"] = False
for k in ("TGMAX_HUB", "TGMAX_HUB_KEY"):
    os.environ.pop(k)
print("g16 ok: покупатель отключает общий аккаунт и включает обратно; вход продавца при этом не трогается")

# 17) защита от подбора кода: 5 ошибок у одного — пауза и сигнал владельцу; 20 за час от всех — вход по кодам закрыт
bot.CODE_TRIES.clear(); bot.CODE_FAILS.clear(); bot.CODE_LOCK["until"] = 0
bot.CFG["admins"] = [1, 2]; bot.CFG["admin_chats"] = {"1": 111, "2": 222}
SENT_TO = []
bot.api_send = lambda chat, text, rows=None, token=None: (API.append((text, rows)), SENT_TO.append((chat, text)))
for k in range(5):
    bot.handle_message(900, 9000, "Взломщик", f"11111{k}", None)
assert any(c == 111 and "подбирает код" in t and "Взломщик" in t for c, t in SENT_TO), SENT_TO[-3:]
assert not any(c == 222 and "подбирает" in t for c, t in SENT_TO)          # только владельцу
for uid in range(901, 905):
    for k in range(4):
        bot.handle_message(uid, 9000 + uid, "Бот", f"22222{k}", None)
assert bot.CODE_LOCK["until"] > _time.time() and any("взломать" in t for c, t in SENT_TO if c == 111)
code = "654321"; bot.INVITES[code] = _time.time() + 900
bot.handle_message(950, 9950, "Новенький", code, None)
assert 950 not in bot.CFG["admins"] and "временно закрыт" in SENT_TO[-1][1], SENT_TO[-1]
bot.CODE_LOCK["until"] = 0
bot.handle_message(950, 9950, "Новенький", code, None)
assert 950 in bot.CFG["admins"]
print("g17 ok: подбор кода — пауза одному и всем, владельцу сигнал; после паузы честный код работает")

# 18) «🚨 Убрать всех админов» — только владелец; остаётся один владелец, коды приглашений сброшены
bot.INVITES["777777"] = _time.time() + 900
text, rows = press("m:ad", uid=2)
assert "m:adkick" not in buttons(rows)
press("a:adkickok", uid=2)
assert 2 in bot.CFG["admins"]
text, rows = press("m:ad")
assert "m:adkick" in buttons(rows)
text, rows = press("m:adkick")
assert "a:adkickok" in buttons(rows)
press("a:adkickok")
assert bot.CFG["admins"] == [1] and not bot.INVITES and "950" not in bot.CFG["admin_chats"]
print("g18 ok: «🚨 Убрать всех админов» — только у владельца, остаётся он один, приглашения сброшены")

# 19) Telegram отозвал вход (AuthKeyUnregistered): бот не долбится, а сразу читает через t.me и пишет владельцу 1 раз
class AuthKeyUnregisteredError(Exception): pass
class DeadTG(FakeTG):
    async def get_messages(self, *a, **k): raise AuthKeyUnregisteredError("The key is not registered in the system")
tio.wfile("AUTH", "session.session", "w")
bot.TG_CLIENT = bot.ClientBox(DeadTG("session", 2040, "x"))
SENT_TO.clear(); bot.LAST_IDS["deadch"] = 5
bot.CFG["channels"] = ["deadch"]
sem = asyncio.Semaphore(1)
asyncio.run_coroutine_threadsafe(bot.poll(bot.TG_CLIENT, bot.cur(), "deadch", {}, sem), loop).result(10)
asyncio.run_coroutine_threadsafe(bot.poll(bot.TG_CLIENT, bot.cur(), "deadch", {}, sem), loop).result(10)
assert bot.is_web_client(bot.TG_CLIENT) and not os.path.exists("session.session")
alerts = [t for c, t in SENT_TO if "вход бота" in t]
assert len(alerts) == 1 and SENT_TO and all(c == 111 for c, t in SENT_TO if "вход бота" in t), SENT_TO
print("g19 ok: Telegram отозвал вход — бот сразу читает через t.me, мёртвый вход удалён, владельцу одно сообщение")

# 20) бота убрали из канала темы — владельцу сразу: какой канал и кто убрал
SENT_TO.clear()
bot.THEMES[0].chat_id = -555
bot.handle_update({"update_type": "bot_removed", "chat_id": -555, "is_channel": True,
                   "user": {"user_id": 4242, "name": "StatBot", "is_bot": True}})
msg = [t for c, t in SENT_TO if c == 111]
assert msg and "убрали из канала" in msg[-1] and "StatBot" in msg[-1] and "это бот" in msg[-1], SENT_TO
SENT_TO.clear()
bot.handle_update({"update_type": "bot_removed", "chat_id": -999, "user": {"user_id": 1}})   # чужой чат — молчим
assert not SENT_TO
bot.DENIED[-555] = _time.time() + 600
bot.handle_update({"update_type": "bot_added", "chat_id": -555, "is_channel": True, "user": {"user_id": 1}})
assert -555 not in bot.DENIED and any("вернули" in t for c, t in SENT_TO), SENT_TO
print("g20 ok: бота убрали из канала — владельцу сразу сообщение, кто убрал (бот-«защитник» подсвечен)")

# 21) OWNER_ID задан: панель только у владельца, утёкший код не делает чужого владельцем
bot.FIXED_OWNER = 1
bot.CODE_LOCK["until"] = 0; bot.CODE_FAILS.clear(); bot.CODE_TRIES.clear()   # сбросить защиту от подбора из g17
bot.CFG["admins"] = []; bot.CFG["admin_chats"] = {}; bot.CLAIM_CODE = "claim99"
import os as _os
_os.environ["ADMIN_CODE"] = "secretcode"
SENT_TO.clear()
bot.handle_message(777, 7777, "Чужак", "claim99", None)        # чужой с кодом привязки
assert 777 not in bot.CFG["admins"] and not bot.is_owner(777)
bot.handle_message(777, 7777, "Чужак", "secretcode", None)     # чужой с ADMIN_CODE
assert 777 not in bot.CFG["admins"] and any("есть владелец" in t for c, t in SENT_TO if c == 7777), SENT_TO
bot.handle_message(1, 100, "Я", "claim99", None)               # сам владелец
assert bot.is_owner(1) and 1 in bot.CFG["admins"]
assert not bot.is_owner(777) and not bot.is_owner(2)           # больше никто не владелец
_os.environ.pop("ADMIN_CODE", None); bot.FIXED_OWNER = 0
print("g21 ok: OWNER_ID — панель только у владельца, утёкший код привязки/ADMIN_CODE чужого не пускает")
print("g12б ok: у покупателя вход в Telegram — только номер и код; к QR приложена ссылка для входа")
# 13) MAX временно лежит (502) — пост НЕ выбрасывается, выходит, когда MAX ожил; разметку не понял — уходит текстом
sys.path.insert(0, ROOT)
from fake_max import FakeMax
srv = FakeMax()
bot.MAX_API = srv.url
bot.HEADERS["Authorization"] = "TESTTOKEN"
bot.notify = lambda *a, **k: None
it = {"id": "z:1", "ch": "z", "date": _time.time(), "enq": _time.time(), "text": "пост **жирный**", "photos": [],
      "vids": [], "sent": 0, "fails": 0, "urgent": False, "status": "ok"}
bot.QUEUE.append(it)
srv.fail_messages = 6
results = []
for _ in range(6):
    it["retry_at"] = 0
    results.append(asyncio.run(bot.publish_item(None, 5000, it)))
assert results == ["retry"] * 6 and it in bot.QUEUE and it.get("fails", 0) == 0, (results, it)
it["retry_at"] = 0
r = asyncio.run(bot.publish_item(None, 5000, it)); t5 = srv.texts(5000)
assert r == "ok" and len(t5) == 1 and t5[0].startswith("пост **жирный**"), (r, t5)
bot.send_max(5000, "плохая BADMD **разметка**", [])
assert srv.texts(5000)[-1] == "плохая BADMD разметка", srv.texts(5000)
print("g13 ok: MAX временно лежит — пост ждёт и выходит потом (не выброшен); непонятая разметка — уходит текстом")
# 22) администратор из ADMIN_IDS (переменная хостинга): админ сразу, без кодов и «Нет доступа»
saved22 = list(bot.CFG["admins"])
bot.ADMIN_IDS = {"4321"}
try:
    API.clear()
    bot.handle_message(4321, 43210, "Админ", "/start", None)
    texts22 = " ".join(str(a) for a in API)
    assert bot.is_admin(4321) and "Нет доступа" not in texts22 and "код" not in texts22.lower(), texts22[:300]
    API.clear()
    bot.handle_message(4322, 43220, "Чужак", "привет", None)   # не в ADMIN_IDS — без изменений: не админ
    assert not bot.is_admin(4322)
finally:
    bot.ADMIN_IDS = set(); bot.CFG["admins"][:] = saved22
print("g22 ok: ADMIN_IDS — админ сразу по /start, без кодов; посторонний — по-прежнему не админ")
print("ВСЕ ТЕСТЫ ПОДКЛЮЧЕНИЯ ИЗ MAX ПРОШЛИ")
os._exit(0)
