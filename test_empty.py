import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
import importlib.util, os, sys, tempfile, time, types
tele = types.ModuleType("telethon"); tele.TelegramClient = object; sys.modules["telethon"] = tele
os.chdir(tempfile.mkdtemp())
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
NOW = time.time() + 10000
time.time = lambda: NOW
NOTES = []
bot.notify = lambda text, tag=True, rows=None: NOTES.append(text)
bot.GCFG["admin_chats"] = {"1": 111}
bot.CFG["night_on"] = False
bot.CFG["gap_min"] = 60
bot.PUB["last_pub"] = NOW - 7200

def item(status="ok", **kw):
    it = {"id": f"a:{len(bot.QUEUE)}", "ch": "a", "date": NOW, "enq": NOW, "text": "x", "status": status}
    it.update(kw); bot.QUEUE.append(it); return it

# 1) пусто и тайник пуст -> предупреждение, второй раз сразу не повторяется
bot.check_empty(NOW)
assert len(NOTES) == 1 and "На ближайший час постов нет" in NOTES[0] and "Тайник пуст" in NOTES[0], NOTES
bot.check_empty(NOW + 600); assert len(NOTES) == 1
bot.check_empty(NOW + 3 * 3600 + 10); assert len(NOTES) == 2          # напоминание через 3 часа
print("e1 ok: предупреждает один раз, напоминает через 3 часа")

# 2) появился пост на этот час -> тишина и сброс; потом снова опустело -> новое предупреждение
NOTES.clear()
it = item(manual=True, at=NOW + 1200)
bot.check_empty(NOW); assert not NOTES
bot.QUEUE.clear()
bot.check_empty(NOW); assert len(NOTES) == 1
print("e2 ok: назначенный пост на ближайший час — без тревоги; опустело снова — предупредил")

# 3) пост назначен на позже, чем через час -> предупреждает и называет время
NOTES.clear(); bot.QUEUE.clear(); bot.THEMES[0].flags["empty_at"] = 0
item(manual=True, at=NOW + 7200)
bot.check_empty(NOW)
assert len(NOTES) == 1 and "Следующий назначен на" in NOTES[0], NOTES
print("e3 ok: следующий пост позже часа — предупреждает и называет время")

# 4) обычный пост по расписанию успевает в этот час -> тихо; не успевает -> предупреждение
NOTES.clear(); bot.QUEUE.clear(); bot.THEMES[0].flags["empty_at"] = 0
item()                                           # last_pub 2 часа назад -> выйдет сразу
bot.check_empty(NOW); assert not NOTES
bot.PUB["last_pub"] = NOW                        # только что публиковали, пауза 60 мин -> ровно в границе
bot.PUB["last_pub"] = NOW + 600                  # слот уйдёт за границу часа
bot.check_empty(NOW); assert len(NOTES) == 1
print("e4 ok: плановый пост учитывается по паузе")

# 5) тайник с мемами закрывает дыру; ждущие одобрения упоминаются
NOTES.clear(); bot.QUEUE.clear(); bot.THEMES[0].flags["empty_at"] = 0; bot.PUB["last_pub"] = NOW - 7200
bot.STASH.append({"id": "s", "text": "мем", "photos": [], "videos": []})
bot.check_empty(NOW); assert not NOTES
bot.STASH.clear(); item("pending"); item("pending")
bot.check_empty(NOW)
assert len(NOTES) == 1 and "Ждут одобрения: 2" in NOTES[0], NOTES
print("e5 ok: тайник считается, ждущие одобрения в сообщении")

# 6) выключено / ночь / сразу после запуска -> молчит
NOTES.clear(); bot.QUEUE.clear(); bot.THEMES[0].flags["empty_at"] = 0
bot.CFG["empty_warn_on"] = False; bot.check_empty(NOW); assert not NOTES
bot.CFG["empty_warn_on"] = True
bot.check_empty(bot.START + 30); assert not NOTES                  # грейс после старта
bot.CFG["night_on"] = True; bot.CFG["night_from"] = 0; bot.CFG["night_to"] = 24
bot.check_empty(NOW); assert not NOTES
print("e6 ok: выкл / ночь / первые 2 минуты — молчит")

# 7) команда /warn
bot.CFG["night_on"] = False
API = []
bot.api_send = lambda chat, text, rows=None, token=None: API.append(text)
bot.CFG["empty_warn_on"] = True
bot.handle_message(1, 111, "Gh", "/warn", None) if 1 in bot.GCFG["admins"] else None
bot.GCFG["admins"] = [1]
bot.handle_message(1, 111, "Gh", "/warn", None)
assert bot.CFG["empty_warn_on"] is False and "ВЫКЛ" in API[-1]
print("e7 ok: /warn переключает")
print("ВСЕ ТЕСТЫ ПРЕДУПРЕЖДЕНИЙ ПРОШЛИ")
