import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
"""Хостинг ботов (Bothost): бот назван main.py, консоли нет, логи видны только в панели, t.me может быть заблокирован,
канал по ссылке может не находиться, на токене может стоять вебхук. Бот всё равно должен заработать."""
import os, shutil, subprocess, sys, tempfile, textwrap, threading, time
sys.path.insert(0, ROOT)
from fake_max import FakeMax

# поддельный telethon (на хостинге без входа он не нужен, но импорт должен пройти)
pk = tempfile.mkdtemp()
os.makedirs(f"{pk}/telethon")
tio.wfile(textwrap.dedent('''
    import types
    from . import errors
    events = types.SimpleNamespace(NewMessage=lambda *a, **k: None)
    class TelegramClient:
        def __init__(self, *a, **k): pass
'''), f"{pk}/telethon/__init__.py", "w")
tio.wfile("class SessionPasswordNeededError(Exception): pass\n", f"{pk}/telethon/errors.py", "w")


def start(srv, workdir, **extra):
    """Как на Bothost: файл бота лежит под именем main.py, токен в BOT_TOKEN, вывод идёт в трубу (не в терминал)."""
    shutil.copy(ROOT + "/tg_to_max.py", f"{workdir}/main.py")
    shutil.copytree(ROOT + "/reloader", os.path.join(os.path.dirname(f"{workdir}/main.py"), "reloader"), dirs_exist_ok=True)   # бот = файл + пакет reloader
    env = dict(os.environ, PYTHONPATH=pk, PYTHONIOENCODING="utf-8", BOT_TOKEN="TESTTOKEN",
               MAX_CHANNEL_LINK="https://max.ru/mychannel", MAX_API_URL=srv.url,
               HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9")
    env.update(extra)
    env.pop("PYTHONUNBUFFERED", None)
    out = []
    p = subprocess.Popen([sys.executable, "main.py"], cwd=workdir, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    p.reader = threading.Thread(target=lambda: [out.append(l) for l in p.stdout], daemon=True)
    p.reader.start()
    return p, out


def wait_out(out, needle, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if any(needle in l for l in out):
            return True
        time.sleep(0.2)
    raise AssertionError(f"не дождались в выводе: {needle}\n" + "".join(out[-15:]))


# 1) канал по ссылке не находится (бот ещё не админ канала): бот НЕ падает, меню и ADMIN_CODE работают сразу,
#    логи видны сразу (без буфера), а первое сообщение в канале привязывает канал
srv = FakeMax(); srv.chats = {}
w1 = tempfile.mkdtemp()
p, out = start(srv, w1, ADMIN_CODE="kek2026x")
wait_out(out, "Канал MAX по ссылке не нашёлся")
wait_out(out, "отправь ему этот код")                     # код привязки виден в логах сразу, не через 20 минут
srv.user_message(42, 4200, "kek2026x")
srv.wait(lambda: any("владелец" in t for t in srv.texts(4200)), what="ответ владельцу при ненайденном канале")
srv.push({"update_type": "message_created", "message": {"sender": {"user_id": 9}, "body": {"text": "группа"},
          "recipient": {"chat_id": -555, "chat_type": "chat"}}})        # сообщение в ГРУППЕ — не привязываем
time.sleep(2)
assert not os.path.exists(f"{w1}/data/channel_id.txt"), "группа привязалась вместо канала"
srv.push({"update_type": "message_created", "message": {"sender": {"user_id": 42}, "body": {"text": "тест"},
          "recipient": {"chat_id": -777, "chat_type": "channel"}}})
srv.wait(lambda: os.path.exists(f"{w1}/data/channel_id.txt"), what="канал привязан по сообщению")
assert tio.rfile(f"{w1}/data/channel_id.txt").strip() == "-777"
assert p.poll() is None, "бот упал"
assert os.path.exists(f"{w1}/data/bot.log") and not os.path.exists(f"{w1}/bot.log")
tio.reap(p)
print("h1 ok: канал не нашёлся по ссылке — бот не падает, отвечает, логи сразу видны, канал привязан по сообщению")

# 2) код привязки не меняется при перезапуске (хостинг перезапускает бота, а код в логах уже прочитан)
srv = FakeMax()
w2 = tempfile.mkdtemp()
p, out = start(srv, w2)
wait_out(out, "отправь ему этот код")
code1 = next(l for l in out if "отправь ему этот код" in l).split()[-1]
tio.reap(p)
p, out = start(srv, w2)
wait_out(out, "отправь ему этот код")
code2 = next(l for l in out if "отправь ему этот код" in l).split()[-1]
assert code1 == code2, (code1, code2)
srv.user_message(43, 4300, code2)
srv.wait(lambda: any("владелец" in t for t in srv.texts(4300)), what="владелец по коду из логов")
srv.wait(lambda: not os.path.exists(f"{w2}/data/claim_code.txt"), what="использованный код удалён")
tio.reap(p)
print("h2 ok: код привязки одинаковый между перезапусками и работает")

# 3) подбор ADMIN_CODE: после 5 неверных кодов даже верный 10 минут не принимается (бот говорит, сколько ждать),
#    а обычные слова («Привет», «Меню») попыткой подбора не считаются
srv = FakeMax()
w3 = tempfile.mkdtemp()
p, out = start(srv, w3, ADMIN_CODE="kek2026x")
wait_out(out, "отправь ему этот код")
for w in ("Привет", "Здравствуйте", "Меню", "Старт", "Помощь", "Алло"):
    srv.user_message(68, 6800, w)
srv.wait(lambda: len(srv.texts(6800)) >= 6, what="ответы на приветствия")
srv.user_message(68, 6800, "kek2026x")
srv.wait(lambda: any("владелец" in t for t in srv.texts(6800)), what="владелец после обычных слов")
for i in range(5):
    srv.user_message(66, 6600, f"guess{i}ab")
srv.wait(lambda: len(srv.texts(6600)) >= 5, what="ответы на неверные коды")
srv.user_message(66, 6600, "kek2026x")
srv.wait(lambda: any("Слишком много неверных кодов" in t for t in srv.texts(6600)), what="сообщение о паузе")
assert not any("админ бота" in t for t in srv.texts(6600)), srv.texts(6600)
tio.reap(p)
print("h3 ok: подбор ADMIN_CODE остановлен после 5 ошибок, обычные слова не блокируют владельца")

# 4) вебхук ставят снова уже после запуска — бот сам снимает его (не ждёт 10 минут) и снова отвечает
srv = FakeMax()
w4 = tempfile.mkdtemp()
p, out = start(srv, w4, ADMIN_CODE="kek2026x")
wait_out(out, "отправь ему этот код")
time.sleep(2)
srv.subscriptions = ["https://bothost.ru/hook/again"]
srv.user_message(44, 4400, "kek2026x")                    # это сообщение уйдёт в вебхук (в настоящем MAX — потеряется)
srv.wait(lambda: not srv.subscriptions, timeout=75, what="повторный вебхук снят")
srv.user_message(45, 4500, "kek2026x")
srv.wait(lambda: any("владелец" in t or "админ" in t for t in srv.texts(4500)), what="ответ после снятия вебхука")
tio.reap(p)
print("h4 ok: повторно поставленный вебхук снимается за минуту, бот снова отвечает")

# 5) MAX_CHANNEL_LINK в виде «@mychannel» и MAX_CHAT_ID числом — понимает
srv = FakeMax()
w5 = tempfile.mkdtemp()
p, out = start(srv, w5, MAX_CHANNEL_LINK="@mychannel")
wait_out(out, "MAX chat_id = 5000")
tio.reap(p)
w5b = tempfile.mkdtemp()
srv2 = FakeMax(); srv2.chats = {}
p, out = start(srv2, w5b, MAX_CHAT_ID="-80231555661308")
wait_out(out, "MAX chat_id = -80231555661308")
assert not any("не нашёлся" in l for l in out)
tio.reap(p)
print("h5 ok: ссылка «@канал» и MAX_CHAT_ID из переменных окружения")

# 6) нет токена на хостинге — понятное сообщение и выход, без вопросов в пустую консоль
w6 = tempfile.mkdtemp()
shutil.copy(ROOT + "/tg_to_max.py", f"{w6}/main.py")
shutil.copytree(ROOT + "/reloader", os.path.join(os.path.dirname(f"{w6}/main.py"), "reloader"), dirs_exist_ok=True)   # бот = файл + пакет reloader
env = dict(os.environ, PYTHONPATH=pk, PYTHONIOENCODING="utf-8", BOT_ID="bot_1")
for k in ("BOT_TOKEN", "MAX_TOKEN", "MAX_CHANNEL_LINK"):
    env.pop(k, None)
r = subprocess.run([sys.executable, "main.py"], cwd=w6, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                   text=True, encoding="utf-8", timeout=60)
assert r.returncode == 1 and "BOT_TOKEN" in r.stdout and "MAX_CHANNEL_LINK" in r.stdout, r.stdout + r.stderr
print("h6 ok: на хостинге без токена — понятная подсказка, какие переменные задать")
# 7) файл бота назван main.py, но рядом config.json (телефон) — это не хостинг: настройки берутся из config.json
srv = FakeMax()
w7 = tempfile.mkdtemp()
shutil.copy(ROOT + "/tg_to_max.py", f"{w7}/main.py")
shutil.copytree(ROOT + "/reloader", os.path.join(os.path.dirname(f"{w7}/main.py"), "reloader"), dirs_exist_ok=True)   # бот = файл + пакет reloader
tio.wjson({"max_token": "TESTTOKEN", "max_channel_link": "https://max.ru/mychannel", "max_api": srv.url}, f"{w7}/config.json", "w")
env = dict(os.environ, PYTHONPATH=pk, PYTHONIOENCODING="utf-8")
for k in ("BOT_TOKEN", "MAX_TOKEN", "MAX_CHANNEL_LINK", "BOT_ID", "TG_MAX_DIR"):
    env.pop(k, None)
p = subprocess.Popen([sys.executable, "main.py"], cwd=w7, env=env, stdin=subprocess.DEVNULL,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
time.sleep(6)
alive = p.poll() is None
p.kill(); o7 = p.stdout.read()
assert alive and "MAX chat_id = 5000" in o7 and os.path.exists(f"{w7}/bot.log") and not os.path.exists(f"{w7}/data"), o7[-800:]
print("h7 ok: main.py рядом с config.json (телефон) работает как обычно, не как хостинг")
# 8) на хостинге часы по Москве (сервер в UTC), если TZ не задан; заданный TZ не трогаем
for tz, expect in ((None, "MSK"), ("UTC0", "UTC")):
    w8 = tempfile.mkdtemp()
    shutil.copy(ROOT + "/tg_to_max.py", f"{w8}/main.py")
    shutil.copytree(ROOT + "/reloader", os.path.join(os.path.dirname(f"{w8}/main.py"), "reloader"), dirs_exist_ok=True)   # бот = файл + пакет reloader
    tio.wfile("import runpy, sys, time\nsys.argv=['main.py','--tzprobe']\n"
        "try:\n    runpy.run_path('main.py', run_name='not_main')\nexcept SystemExit: pass\nprint('TZNAME', time.tzname[0])\n", f"{w8}/probe.py", "w")
    env = dict(os.environ, PYTHONPATH=pk, BOT_ID="bot_1", BOT_TOKEN="T", MAX_CHANNEL_LINK="https://max.ru/x")
    env.pop("TZ", None)
    if tz:
        env["TZ"] = tz
    r = subprocess.run([sys.executable, "probe.py"], cwd=w8, env=env, capture_output=True, text=True, timeout=60)
    assert f"TZNAME {expect}" in r.stdout, (tz, r.stdout[-500:], r.stderr[-500:])
print("h8 ok: на хостинге время московское (если TZ не задан вручную)")
print("ВСЕ ТЕСТЫ ХОСТИНГА ПРОШЛИ")
os._exit(0)
