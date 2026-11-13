import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
import asyncio, importlib.util, os, sys, tempfile, types
class FakeTG:
    def __init__(self, *a, **k): pass
    async def connect(self): pass
    async def is_user_authorized(self): return False       # входа нет
    def on(self, ev): return lambda f: f
tele = types.ModuleType("telethon"); tele.TelegramClient = FakeTG
tele.events = types.SimpleNamespace(NewMessage=lambda *a, **k: None)
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = tele.errors
os.chdir(tempfile.mkdtemp())
tio.wfile("web", "web_mode.flag", "w")                     # выбор «без входа» уже сделан
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)

PAGE = ("<section class='tgme_channel_history'>" + "".join(
    f'<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="webch/{i}"><div class="tgme_widget_message_bubble">'
    f'<div class="tgme_widget_message_text js-message_text">Пост номер {i} про новый ивент</div>'
    f'<a class="tgme_widget_message_date" href="https://t.me/webch/{i}"><time datetime="2024-01-01T10:00:00+00:00"></time></a></div></div></div>'
    for i in (11, 12)) + "</section>")
bot.web_fetch = lambda url: PAGE
SENT = []
bot.send_max = lambda chat, t, imgs, vid=None: SENT.append((chat, t[:30]))
bot.menu_loop = lambda: None
bot.GCFG["interval"] = 2
main_t = bot.THEMES[0]; main_t.chat_id = 100; main_t.saved_ids = {"webch": 10}
main_t.own["channels"] = ["webch"]; main_t.own["approval_on"] = False; main_t.own["dedupe_on"] = False

async def run():
    try: await asyncio.wait_for(bot.main(), timeout=14)
    except asyncio.TimeoutError: pass
import builtins
_orig_print, LINES = builtins.print, []
builtins.print = lambda *a, **k: (LINES.append(" ".join(map(str, a))), _orig_print(*a, **k))
asyncio.run(run())
builtins.print = _orig_print
assert any("login.py" in l for l in LINES), LINES[:10]   # подсказка, как подключить аккаунт
print("отправлено:", SENT)
assert len(SENT) == 1 and SENT[0][0] == 100 and "номер 11" in SENT[0][1]          # второй ждёт паузу 60 мин
with bot.theme_ctx(main_t):
    assert [x["id"] for x in bot.QUEUE] == ["webch:12"], bot.QUEUE
print("main-web ok: без входа в Telegram бот читает канал через t.me и публикует в MAX")
