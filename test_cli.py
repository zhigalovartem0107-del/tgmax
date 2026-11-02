import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
"""Запуск скрипта как программы (python tg_to_max.py): мастер настроек, вход, --check."""
import json, os, subprocess, sys, tempfile, textwrap, threading, time


def run_until(p, done, limit=60):
    """Ждём, пока процесс сделает нужное (а не фиксированные секунды: под нагрузкой запуск дольше), и останавливаем.
    (весь вывод, был ли процесс жив в этот момент)."""
    buf = []
    def pump():
        for line in iter(p.stdout.readline, ""):
            buf.append(line)
    th = threading.Thread(target=pump, daemon=True)
    th.start()
    t0 = time.time()
    while time.time() - t0 < limit and p.poll() is None and not done("".join(buf)):
        time.sleep(0.2)
    alive = p.poll() is None
    p.kill()
    th.join(5)
    return "".join(buf), alive
sys.path.insert(0, ROOT)
from fake_max import FakeMax

srv = FakeMax()
pk = tempfile.mkdtemp()
os.makedirs(f"{pk}/telethon")
open(f"{pk}/telethon/__init__.py", "w").write(textwrap.dedent('''
    import datetime, types
    from . import errors
    events = types.SimpleNamespace(NewMessage=lambda *a, **k: None)
    class _Msg:
        id = 50
        date = datetime.datetime.now(datetime.timezone.utc)
        text = "тестовый пост"; grouped_id = None; photo = None; video = gif = video_note = document = sticker = fwd_from = None
    class TelegramClient:
        def __init__(self, session, api_id, api_hash): self.session = session
        async def connect(self): pass
        async def disconnect(self): pass
        async def is_user_authorized(self): return True
        def on(self, ev): return lambda f: f
        async def get_messages(self, ch, min_id=0, limit=30, ids=None):
            return [] if min_id >= 50 else [_Msg()]
'''))
open(f"{pk}/telethon/errors.py", "w").write("class SessionPasswordNeededError(Exception): pass\nclass PhoneCodeInvalidError(Exception): pass\nclass PhoneCodeExpiredError(Exception): pass\n")
env = dict(os.environ, PYTHONPATH=pk, PYTHONIOENCODING="utf-8")
script = ROOT + "/tg_to_max.py"

# 1) первый запуск: нет config.json -> мастер спрашивает всё и сохраняет
w1 = tempfile.mkdtemp()
answers = "a b\nTESTTOKEN\nzzz\nhttps://max.ru/mychannel\n"
env1 = dict(env, MAX_API_URL=srv.url)
p = subprocess.Popen([sys.executable, script], cwd=w1, env=env1, stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
p.stdin.write(answers); p.stdin.flush()
out1, _ = run_until(p, lambda out: "без входа" in out and "login.py" in out)
cfg = json.load(open(f"{w1}/config.json"))
assert cfg["max_token"] == "TESTTOKEN" and cfg["max_channel_link"] == "https://max.ru/mychannel" and not cfg["api_id"], cfg
assert "Не нашёл config.json" in out1 and out1.count("Не подходит") == 2 and "Сохранено в config.json" in out1, out1[-900:]
assert "api_id" not in out1 and "без входа" in out1 and "login.py" in out1, out1[-900:]
print("c1 ok: первый запуск — мастер спросил только MAX, Telegram не нужен, бот работает без входа")

# 1б) login.py: спрашивает ключи Telegram (номер с «+» исправлен) и входит
wl = tempfile.mkdtemp()
json.dump(cfg, open(f"{wl}/config.json", "w"))
p = subprocess.Popen([sys.executable, ROOT + "/login.py"], cwd=wl, env=env1, stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
p.stdin.write("abc\n1234567\n0123456789abcdef0123456789abcdef\n79990001122\n"); p.stdin.flush()
outl, _ = run_until(p, lambda out: "вход выполнен" in out)
cl = json.load(open(f"{wl}/config.json"))
assert cl["api_id"] == 1234567 and cl["phone"] == "+79990001122" and cl["max_token"] == "TESTTOKEN", cl
assert "вход выполнен" in outl and "Traceback" not in outl, outl[-900:]
print("c1б ok: login.py — спросил ключи Telegram, сохранил, вошёл")
cfg = cl

# 2) обычный запуск: вход есть (session.session), бот стартует, слушает MAX, пишет bot.log
w2 = tempfile.mkdtemp()
json.dump(dict(cfg, max_api=srv.url, max_channel_link="https://max.ru/mychannel"), open(f"{w2}/config.json", "w"))
open(f"{w2}/session.session", "w").write("x")
n0 = sum(1 for r in srv.requests if r[1] == "/updates")
p = subprocess.Popen([sys.executable, script], cwd=w2, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     text=True, encoding="utf-8")
out2, alive = run_until(p, lambda out: "вход выполнен" in out and ("код привязки" in out or "отправь ему этот код" in out)
                       and sum(1 for r in srv.requests if r[1] == "/updates") > n0
                       and os.path.exists(f"{w2}/bot.log") and os.path.exists(f"{w2}/channel_id.txt"))
assert alive, out2[-800:]
assert "Вход в Telegram: найден" in out2 and "вход выполнен" in out2 and "код привязки" in out2 or "отправь ему этот код" in out2, out2[-800:]
assert sum(1 for r in srv.requests if r[1] == "/updates") > n0
assert "Traceback" not in out2
assert os.path.exists(f"{w2}/bot.log") and os.path.exists(f"{w2}/channel_id.txt")
print("c2 ok: обычный запуск — вход найден, канал MAX найден, меню слушает события, код привязки показан, bot.log пишется")

# 3) --check: проверка без запуска бота
p = subprocess.run([sys.executable, script, "--check"], cwd=w2, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
assert "токен работает" in p.stdout and "Telegram: вход выполнен" in p.stdout, p.stdout + p.stderr
print("c3 ok: python tg_to_max.py --check — проверка связи с MAX и Telegram")

# 4) неверный токен MAX: понятная подсказка, а не молчание
bad = json.load(open(f"{w2}/config.json")); bad["max_token"] = "WRONGTOKEN123"
json.dump(bad, open(f"{w2}/config.json", "w"))
p = subprocess.run([sys.executable, script, "--check"], cwd=w2, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
assert "токен не принят" in p.stdout and "❌" in p.stdout, p.stdout
print("c4 ok: неверный токен MAX — «токен не принят, проверь max_token в config.json»")
# 5) хостинг без консоли и без настроек: понятное сообщение и выход, а не зависание
w5 = tempfile.mkdtemp()
p = subprocess.run([sys.executable, script], cwd=w5, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                   text=True, encoding="utf-8", timeout=60)
assert p.returncode == 1 and "MAX_TOKEN" in p.stdout and "MAX_CHANNEL_LINK" in p.stdout and "Traceback" not in p.stdout + p.stderr, p.stdout + p.stderr
print("c5 ok: нет консоли и настроек — пишет, какие переменные окружения задать")

# 6) хостинг (Bothost): main.py, токен и канал из переменных окружения, все файлы — в папке data
w6 = tempfile.mkdtemp(); data = os.path.join(w6, "data")
env6 = dict(env, BOT_TOKEN="TESTTOKEN", MAX_CHANNEL_LINK="https://max.ru/mychannel", MAX_API_URL=srv.url, TG_MAX_DIR=data)
p = subprocess.Popen([sys.executable, ROOT + "/main.py"], cwd=w6, env=env6, stdin=subprocess.DEVNULL,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
out6, alive = run_until(p, lambda out: "отправь ему этот код" in out and os.path.exists(os.path.join(data, "bot.log"))
                       and os.path.exists(os.path.join(data, "channel_id.txt")))
assert alive and "Traceback" not in out6 and "отправь ему этот код" in out6, out6[-900:]
assert os.path.exists(os.path.join(data, "bot.log")) and os.path.exists(os.path.join(data, "channel_id.txt")), os.listdir(data)
assert not os.path.exists(os.path.join(w6, "bot.log"))
print("c6 ok: main.py для хостинга — настройки из переменных окружения, файлы бота в data/")
# 7) на токене висит вебхук хостинга (Bothost): бот снимает его и начинает отвечать в MAX
srv.subscriptions = ["https://bothost.ru/webhook/abc"]
w7 = tempfile.mkdtemp()
env7 = dict(env, MAX_TOKEN="TESTTOKEN", MAX_CHANNEL_LINK="https://max.ru/mychannel", MAX_API_URL=srv.url, ADMIN_CODE="kek2026")
p = subprocess.Popen([sys.executable, script], cwd=w7, env=env7, stdin=subprocess.DEVNULL,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
srv.wait(lambda: not srv.subscriptions, what="вебхук снят")
srv.user_message(77, 7700, "kek2026")
srv.wait(lambda: any("владелец" in t for t in srv.texts(7700)), what="бот ответил после снятия вебхука")
p.kill(); out7 = p.stdout.read()
assert "снял вебхук" in out7, out7[-600:]
print("c7 ok: вебхук хостинга на токене — бот снимает его и отвечает в MAX")
# 8) main.py сам берёт самую свежую версию бота, как бы файл ни назывался (телефон дописывает -1, -2 к именам)
import shutil
w8 = tempfile.mkdtemp()
shutil.copy(ROOT + "/main.py", w8)
open(f"{w8}/tg_to_max.py", "w").write('print("RAN old")\n')                                   # старый, без номера
open(f"{w8}/tg_to_max-2.py", "w").write('BOT_VERSION = 2026101801\nprint("RAN v1")\n')
open(f"{w8}/tgmax_new3-1.py", "w").write('BOT_VERSION = 2026101901\nimport os\nprint("RAN v2", os.getcwd())\n')
open(f"{w8}/helper.py", "w").write('print("RAN helper")\n')
p = subprocess.run([sys.executable, f"{w8}/main.py"], cwd=w8, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
assert "RAN v2" in p.stdout and os.path.join(w8, "data") in p.stdout and "RAN old" not in p.stdout, p.stdout + p.stderr
os.remove(f"{w8}/tgmax_new3-1.py"); os.remove(f"{w8}/tg_to_max-2.py")
p = subprocess.run([sys.executable, f"{w8}/main.py"], cwd=w8, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
assert "RAN old" in p.stdout and "helper" not in p.stdout, p.stdout + p.stderr
print("c8 ok: main.py запускает самую свежую версию бота (по BOT_VERSION), имя файла не важно")
print("ВСЕ ТЕСТЫ ЗАПУСКА ПРОШЛИ")
os._exit(0)
