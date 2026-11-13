import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
import importlib.util, os, sys, tempfile, types
tele = types.ModuleType("telethon"); tele.TelegramClient = object; sys.modules["telethon"] = tele
os.chdir(tempfile.mkdtemp())
os.environ.update(TG_API_ID="1234567", TG_API_HASH="0123456789abcdef0123456789abcdef", TG_PHONE="+79990000000")  # аккаунт Telegram подключён
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)

def check(name):                      # «вход рабочий», если в файле написано GOOD; OFFLINE -> None
    c = tio.rfile(name + ".session")
    return None if c == "OFFLINE" else c == "GOOD"

def mk(d, content, **files):
    os.makedirs(d, exist_ok=True)
    tio.wfile(content, os.path.join(d, "session.session"), "w")
    for n, c in files.items():
        tio.wfile(c, os.path.join(d, n.replace("__", ".")), "w")

root = tempfile.mkdtemp()
new = os.path.join(root, "new"); os.makedirs(new)
# свежий, но НЕрабочий вход и старый РАБОЧИЙ с данными
def stamp(d, t):
    """Время изменения входа задаём явно: порядок «свежий/старый» не зависит от загрузки машины."""
    os.utime(os.path.join(d, "session.session"), (t, t))
mk(os.path.join(root, "a", "bot1"), "BAD"); stamp(os.path.join(root, "a", "bot1"), 1000)
mk(os.path.join(root, "b", "deep", "bot2"), "GOOD", settings__json='{"admins":[5]}', queue_t2__json="[]")
stamp(os.path.join(root, "b", "deep", "bot2"), 2000)
os.makedirs(os.path.join(root, "b", "deep", "bot2", "queue_media")); tio.wfile("x", os.path.join(root, "b", "deep", "bot2", "queue_media", "a.jpg"), "w")
mk(os.path.join(root, "c"), "BAD"); stamp(os.path.join(root, "c"), 3000)   # самый свежий, тоже нерабочий
mk(new, "BAD")                                   # и тут, в рабочей папке, вход протух
bot.setup_data_dir(base=new, extra_roots=[root], check=check)
assert tio.rfile("session.session") == "GOOD", tio.rfile("session.session")
assert os.path.exists("session.session.bak") and os.path.exists("settings.json") and os.path.exists("queue_t2.json")
assert os.path.exists("queue_media/a.jpg") and not os.path.exists("probe_session.session")
print("s1 ok: протухшие входы пропущены, рабочий найден глубоко в папках и подтянут с настройками")

# рабочий вход уже тут — ничего не ищем и не трогаем
bot.setup_data_dir(base=new, extra_roots=[root], check=lambda n: (_ for _ in ()).throw(AssertionError) if n != "session" else True)
assert tio.rfile("session.session") == "GOOD"
print("s2 ok: рабочий вход рядом — не трогаем")

# нет интернета: вход рядом не проверить — не трогаем
tio.wfile("OFFLINE", "session.session", "w")
bot.setup_data_dir(base=new, extra_roots=[root], check=check)
assert tio.rfile("session.session") == "OFFLINE"
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
assert tio.rfile("session.session") == "BAD" and not os.path.exists("settings.json")
del os.environ["TG_MAX_NO_SEARCH"]
print("s5 ok: TG_MAX_NO_SEARCH — чужой вход и чужие настройки не подтягиваются")
