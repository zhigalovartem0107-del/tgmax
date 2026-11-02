import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
import importlib.util, os, sys, tempfile, types, time
tele = types.ModuleType("telethon"); tele.TelegramClient = object; sys.modules["telethon"] = tele
os.chdir(tempfile.mkdtemp())
os.environ.update(TG_API_ID="1234567", TG_API_HASH="0123456789abcdef0123456789abcdef", TG_PHONE="+79990000000")  # аккаунт Telegram подключён
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)

def check(name):                      # «вход рабочий», если в файле написано GOOD; OFFLINE -> None
    c = open(name + ".session").read()
    return None if c == "OFFLINE" else c == "GOOD"

def mk(d, content, **files):
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "session.session"), "w").write(content)
    for n, c in files.items():
        open(os.path.join(d, n.replace("__", ".")), "w").write(c)

root = tempfile.mkdtemp()
new = os.path.join(root, "new"); os.makedirs(new)
# свежий, но НЕрабочий вход и старый РАБОЧИЙ с данными
mk(os.path.join(root, "a", "bot1"), "BAD")
time.sleep(0.05)
mk(os.path.join(root, "b", "deep", "bot2"), "GOOD", settings__json='{"admins":[5]}', stash__json="[]", queue_t2__json="[]")
os.makedirs(os.path.join(root, "b", "deep", "bot2", "stash_media")); open(os.path.join(root, "b", "deep", "bot2", "stash_media", "a.jpg"), "w").write("x")
time.sleep(0.05)
mk(os.path.join(root, "c"), "BAD")               # самый свежий, тоже нерабочий
mk(new, "BAD")                                   # и тут, в рабочей папке, вход протух
bot.setup_data_dir(base=new, extra_roots=[root], check=check)
assert open("session.session").read() == "GOOD", open("session.session").read()
assert os.path.exists("session.session.bak") and os.path.exists("settings.json") and os.path.exists("queue_t2.json")
assert os.path.exists("stash_media/a.jpg") and not os.path.exists("probe_session.session")
print("s1 ok: протухшие входы пропущены, рабочий найден глубоко в папках и подтянут с настройками")

# рабочий вход уже тут — ничего не ищем и не трогаем
bot.setup_data_dir(base=new, extra_roots=[root], check=lambda n: (_ for _ in ()).throw(AssertionError) if n != "session" else True)
assert open("session.session").read() == "GOOD"
print("s2 ok: рабочий вход рядом — не трогаем")

# нет интернета: вход рядом не проверить — не трогаем
open("session.session", "w").write("OFFLINE")
bot.setup_data_dir(base=new, extra_roots=[root], check=check)
assert open("session.session").read() == "OFFLINE"
print("s3 ok: без интернета рабочий вход не перезаписывается")

# нигде нет рабочего входа
os.chdir(tempfile.mkdtemp())
lonely = os.path.join(tempfile.mkdtemp(), "x")
bot.setup_data_dir(base=lonely, extra_roots=[tempfile.mkdtemp()], check=check)
assert not os.path.exists("session.session")
print("s4 ok: нигде нет рабочего входа — честно сообщает")

# сервер с несколькими ботами: чужой рабочий вход рядом не берём
os.environ["TG_MAX_NO_SEARCH"] = "1"
other = tempfile.mkdtemp(); mk(os.path.join(other, "client_b"), "GOOD", settings__json='{"admins":[9]}')
mine = os.path.join(other, "client_a"); mk(mine, "BAD")
bot.setup_data_dir(base=mine, extra_roots=[other], check=check)
assert open("session.session").read() == "BAD" and not os.path.exists("settings.json")
del os.environ["TG_MAX_NO_SEARCH"]
print("s5 ok: TG_MAX_NO_SEARCH — чужой вход и чужие настройки не подтягиваются")
