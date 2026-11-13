import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
import asyncio, datetime, importlib.util, os, sys, tempfile, time, types

NOW = time.time()
class FMsg:
    def __init__(self, i, text, photo=False):
        self.id, self.text, self.grouped_id = i, text, None
        self.date = datetime.datetime.fromtimestamp(NOW - 120, tz=datetime.timezone.utc)
        self.photo = object() if photo else None
        self.document = self.gif = self.video_note = self.sticker = self.video = None
        self.fwd_from = None
    async def download_media(self, file=None):
        tio.wfile(b"img", file, "wb"); return file

DATA = {"robl": [FMsg(11, "Новый ивент в игре, подробности внутри", photo=True)],
        "cats": [FMsg(5, "Котик спит в коробке и это очень мило")]}

class FakeTG:
    def __init__(self, *a, **k): pass
    async def start(self, phone=None): pass
    async def connect(self): pass
    async def is_user_authorized(self): return True
    def on(self, ev): return lambda f: f
    async def get_messages(self, ch, min_id=0, limit=30, ids=None):
        msgs = DATA.get(ch, [])
        if ids is not None:
            return next((m for m in msgs if m.id == ids), None)
        return [m for m in msgs if m.id > min_id][:limit]

tele = types.ModuleType("telethon")
tele.TelegramClient = FakeTG
tele.events = types.SimpleNamespace(NewMessage=lambda *a, **k: None)
sys.modules["telethon"] = tele
os.chdir(tempfile.mkdtemp())

os.environ.update(TG_API_ID="1234567", TG_API_HASH="0123456789abcdef0123456789abcdef", TG_PHONE="+79990000000")  # аккаунт Telegram подключён
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)

SENT = []
bot.send_max = lambda chat, text, imgs, vid=None: SENT.append((chat, text[:40], len(imgs)))
bot.upload_image = lambda p: "tok"
bot.menu_loop = lambda: None
bot.GCFG["interval"] = 2
bot.GCFG["channels"] = ["robl"]

main_t = bot.THEMES[0]
main_t.chat_id = 100
main_t.saved_ids = {"robl": 10}
t2 = bot.create_theme("Коты")
t2.chat_id, t2.link = 200, "https://max.ru/cats"
t2.own["channels"] = ["cats"]
t2.saved_ids = {"cats": 0}
for t in (main_t, t2):
    with bot.theme_ctx(t):
        bot.CFG["approval_on"] = False

async def run():
    try:
        await asyncio.wait_for(bot.main(), timeout=14)
    except asyncio.TimeoutError:
        pass
asyncio.run(run())
print("отправлено:", SENT)
chats = {c for c, _, _ in SENT}
assert chats == {100, 200}, SENT
assert any(c == 100 and n == 1 for c, _, n in SENT), "пост с фото должен уйти с фото в канал основной темы"
assert any(c == 200 and "Котик" in t for c, t, _ in SENT)
print("main ok: обе темы читаются и публикуют каждая в свой канал MAX")
