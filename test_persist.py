"""💾 Настройки переживают обновление и пересборку бота: контакт продавца и ключи ЮKassa — из панели (файл
настроек) или из переменных хостинга; испорченный или пропавший файл настроек поднимается из резервной копии;
папку данных можно вынести на постоянный диск (TG_MAX_DIR). Каждый «перезапуск» — отдельный процесс."""
import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from reloader.core.files import load_json, load_json_safe, save_json  # noqa: E402

# 1) запись с резервной копией: основной файл испорчен или пропал — читаем копию; копия — с теми же правами
d = tempfile.mkdtemp()
p = os.path.join(d, "settings.json")
save_json(p, {"seller_contact": "@one"}, private=True, backup=True)
save_json(p, {"seller_contact": "@two"}, private=True, backup=True)
assert load_json(p, {}) == {"seller_contact": "@two"} and load_json(p + ".bak", {}) == {"seller_contact": "@two"}
assert oct(os.stat(p + ".bak").st_mode & 0o777) == "0o600", "в копии — ключи: только для бота"
tio.wfile('{"seller_contact": "@tw', p, "w")                       # оборвалась запись / испортился диск
assert load_json_safe(p, {}) == {"seller_contact": "@two"}
assert os.path.exists(p + ".broken"), "испорченный файл сохранён для разбора"
os.remove(p)                                                         # файл пропал
assert load_json_safe(p, {}) == {"seller_contact": "@two"}
assert load_json_safe(os.path.join(d, "nothing.json"), {"x": 1}) == {"x": 1}, "ничего нет — значения по умолчанию"
q = os.path.join(d, "queue.json")
save_json(q, [1, 2])
assert not os.path.exists(q + ".bak"), "очереди и прочее — без копии (часто пишутся)"
real_fsync = os.fsync                                                # диск/хостинг без fsync — сохраняем всё равно
def _no_fsync(fd):
    raise OSError(22, "Invalid argument")
os.fsync = _no_fsync
try:
    save_json(p, {"seller_contact": "@three"}, private=True, backup=True)
finally:
    os.fsync = real_fsync
assert load_json(p, {}) == {"seller_contact": "@three"}
os.remove(p + ".bak"); os.mkdir(p + ".bak")                          # копия не пишется — основной файл всё равно
save_json(p, {"seller_contact": "@four"}, private=True, backup=True)
assert load_json(p, {}) == {"seller_contact": "@four"}
os.rmdir(p + ".bak")
from reloader.services.codes.store import CodeStore  # noqa: E402
cs_path = os.path.join(d, "codes.json")
code = CodeStore(cs_path).generate(1, "Старт", 30)[0]
tio.wfile("{broken", cs_path, "w")                                 # коды — это оплаты покупателей
assert CodeStore(cs_path).get(code) is not None, "код активации поднят из резервной копии"
print("p1 ok: 💾 запись с резервной копией; испорченный/пропавший файл — из копии; ключи в копии — только для бота")

CHILD = r'''
import importlib.util, json, os, sys, types
ROOT, WORK, ACTION = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, ROOT)
tele = types.ModuleType("telethon"); tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = tele.errors
os.chdir(WORK)
if not os.path.exists("config.json"):
    json.dump({"max_token": "T", "max_channel_link": ""}, open("config.json", "w"))
spec = importlib.util.spec_from_file_location("bot", os.path.join(ROOT, "tg_to_max.py"))
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
bot.S.trust_env = False
if ACTION == "set":                                   # хозяин вводит контакт и ключи ЮKassa в панели
    bot.apply_text("clcontact", "@panel_admin", 1)
    bot.apply_text("clyk", "777777 test_panelkey", 1)
    bot.ensure_host_cabinet()                          # кабинет хозяина с темой — в tenants.json и themes.json
out = {"contact": bot.SELLER_CONTACT, "link": bot.seller_link(), "yk": bot.yk_keys(),
       "host_cab": bot.host_id() in bot.TENANTS, "host_themes": len(bot.tenant_themes(bot.host_id()))}
print("RESULT " + json.dumps(out))
os._exit(0)
'''
child = os.path.join(tempfile.mkdtemp(), "child.py")
tio.wfile(CHILD, child, "w")
BASE = {k: v for k, v in os.environ.items() if k not in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy",
                                                        "SELLER_CONTACT", "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY",
                                                        "TG_MAX_DIR")}
BASE.update(TGMAX_HOST="1", OWNER_ID="1", BOT_TOKEN="T", MAX_API_URL="http://127.0.0.1:9", YK_API_URL="http://127.0.0.1:9")


def run(work, action="show", **env):
    e = dict(BASE, **env)
    r = subprocess.run([sys.executable, child, ROOT, work, action], env=e, capture_output=True, text=True, timeout=120)
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, (r.returncode, r.stdout[-1500:], r.stderr[-1500:])
    return json.loads(line[7:]), r.stdout


ENV = dict(SELLER_CONTACT="@env_admin", YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="test_envkey")

# 2) пересборка без постоянного диска (папка данных пустая): контакт и ключи — из переменных хостинга
fresh = tempfile.mkdtemp()
got, _ = run(fresh, **ENV)
assert got["contact"] == "@env_admin" and got["link"] == "https://max.ru/env_admin", got
assert got["yk"] == ["123456", "test_envkey"], got
print("p2 ok: 💾 пустая папка данных (пересборка) — контакт и ключи ЮKassa из переменных Bothost")

# 3) обновление с сохранённой папкой: введённое в панели переживает перезапуск (и главнее переменных)
work = tempfile.mkdtemp()
run(work, "set", **ENV)
got, _ = run(work, **ENV)
assert got["contact"] == "@panel_admin" and got["yk"] == ["777777", "test_panelkey"], got
assert got["host_cab"] and got["host_themes"] == 1, got
got, _ = run(work)                                                   # переменных нет — всё равно из файла
assert got["contact"] == "@panel_admin" and got["yk"] == ["777777", "test_panelkey"], got
print("p3 ok: 💾 контакт и ключи из панели переживают перезапуск (и главнее переменных хостинга)")

# 4) файлы настроек испортились (оборвалась запись, сбой диска) или пропали — бот поднимает их из копии
for name in ("settings.json", "tenants.json", "themes.json"):
    tio.wfile("{oops", os.path.join(work, name), "w")
got, out = run(work)
assert got["contact"] == "@panel_admin" and got["yk"] == ["777777", "test_panelkey"], got
assert got["host_cab"] and got["host_themes"] == 1, got
assert "резервной копии" in out, out[-800:]
for name in ("settings.json", "tenants.json", "themes.json"):
    os.remove(os.path.join(work, name))
got, _ = run(work)
assert got["contact"] == "@panel_admin" and got["host_cab"] and got["host_themes"] == 1, got
print("p4 ok: 💾 испорченные или пропавшие settings/tenants/themes — подняты из резервной копии")

# 5) папку данных можно вынести на постоянный диск: TG_MAX_DIR (main.py запускает бота там)
app = tempfile.mkdtemp()
shutil.copy(os.path.join(ROOT, "main.py"), app)
tio.wfile("BOT_VERSION = 99\nimport os\nprint('CWD ' + os.getcwd())\n", os.path.join(app, "stub_bot.py"), "w")
disk = tempfile.mkdtemp()
r = subprocess.run([sys.executable, "main.py"], cwd=app, env=dict(BASE, TG_MAX_DIR=disk), capture_output=True, text=True)
assert f"CWD {os.path.realpath(disk)}" in r.stdout.replace(disk, os.path.realpath(disk)), r.stdout + r.stderr
r = subprocess.run([sys.executable, "main.py"], cwd=app, env=BASE, capture_output=True, text=True)
assert f"CWD {os.path.join(os.path.realpath(app), 'data')}" in r.stdout.replace(app, os.path.realpath(app)), r.stdout
print("p5 ok: 💾 TG_MAX_DIR — данные бота на постоянном диске; без неё — папка data рядом с ботом")

# 6) запись файлов не ждёт чужих: пока одна запись висит на fsync (медленный диск хостинга), другой файл пишется
#    сразу — цикл бота не стоит; а ОДИН и тот же файл по-прежнему пишется строго по очереди (временный .tmp общий)
import threading                                                       # noqa: E402
import reloader.core.files as F                                        # noqa: E402
held, real_fsync = threading.Event(), F.os.fsync
F.os.fsync = lambda fd: held.wait(10)                                   # «диск» отвечает, только когда отпустим
try:
    d6 = tempfile.mkdtemp()
    slow, fast = os.path.join(d6, "tenants.json"), os.path.join(d6, "pub_state.json")
    t_slow = threading.Thread(target=save_json, args=(slow, {"n": 1}), kwargs={"backup": True})
    t_slow.start()
    t_fast = threading.Thread(target=save_json, args=(fast, {"last_pub": 1}))
    t_fast.start()
    t_fast.join(2)
    assert not t_fast.is_alive(), "запись pub_state ждёт, пока другой поток пишет tenants.json на диск"
    assert load_json(fast, {}) == {"last_pub": 1}
    t_same = threading.Thread(target=save_json, args=(slow, {"n": 2}), kwargs={"backup": True})
    t_same.start()
    t_same.join(0.5)
    assert t_same.is_alive(), "второй писатель того же файла должен ждать первого (общий .tmp)"
    held.set()
    for th in (t_slow, t_same):
        th.join(10)
        assert not th.is_alive()
    assert load_json(slow, {}) == {"n": 2} and load_json(slow + ".bak", {}) == {"n": 2}
    assert not [x for x in os.listdir(d6) if x.endswith(".tmp")]
finally:
    held.set()
    F.os.fsync = real_fsync
print("p6 ok: 💾 запись одного файла не ждёт медленной записи другого; один и тот же файл — строго по очереди")

print("ВСЕ ТЕСТЫ СОХРАННОСТИ НАСТРОЕК ПРОШЛИ")
