"""🔒 Защита от кражи: бот запускается только с правильным BOT_SECRET (переменная окружения хостинга). В коде — лишь
SHA-256 секрета, не сам секрет: даже со всеми файлами бота, но без секрета, его не запустить. main.py без (или с
неверным) BOT_SECRET пишет «Unauthorized copy detected» и сразу выходит, не запуская бота; с верным — запускается.
Плюс build_obfuscated.sh (PyArmor) — для защищённой выкладки на хостинг."""
import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from reloader.services import license as L  # noqa: E402

FAILS = []


def check(fn):
    try:
        fn()
        print(f"{fn.__name__} ok: {fn.__doc__}")
    except Exception as e:
        FAILS.append(fn.__name__)
        line = traceback.extract_tb(e.__traceback__)[-1]
        print(f"{fn.__name__} FAIL (строка {line.lineno}: {line.line}) — {type(e).__name__}: {str(e)[:400]}")
    return fn


# тестовый секрет и его хеш — свои, не рабочие (рабочий секрет в репозиторий не кладём)
TEST_SECRET = "unit-test-secret-value-123"
TEST_HASH = hashlib.sha256(TEST_SECRET.encode()).hexdigest()


@check
def t1():
    """логика секрета: верный секрет подходит, пустой/неверный/не строка — нет; сравнение по хешу (не по самому
    секрету); в коде лежит именно SHA-256 (64 hex), а не секрет"""
    assert L.sha256(TEST_SECRET) == TEST_HASH
    assert L.secret_ok(TEST_SECRET, TEST_HASH) is True
    assert L.secret_ok(TEST_SECRET.upper(), TEST_HASH) is False, "регистр важен"
    for bad in ("", "wrong", " " + TEST_SECRET, None, 123, TEST_HASH):
        assert L.secret_ok(bad, TEST_HASH) is False, bad
    assert len(L.EXPECTED_SHA256) == 64 and all(c in "0123456789abcdef" for c in L.EXPECTED_SHA256.lower())
    src = tio.rfile(os.path.join(ROOT, "reloader/services/license.py"), encoding="utf-8")
    assert "BOT_SECRET" in src and L.EXPECTED_SHA256 in src
    main_src = tio.rfile(os.path.join(ROOT, "main.py"), encoding="utf-8")   # замок в main.py — тот же хеш
    assert L.EXPECTED_SHA256 in main_src and "BOT_SECRET" in main_src and "Unauthorized copy detected" in main_src


@check
def t2():
    """check(env): нет BOT_SECRET → (False, причина); неверный → (False, причина); верный → (True, …);
    пробелы по краям обрезаются"""
    assert L.check({}, TEST_HASH)[0] is False
    assert L.check({"BOT_SECRET": ""}, TEST_HASH)[0] is False
    assert L.check({"BOT_SECRET": "nope"}, TEST_HASH)[0] is False
    assert L.check({"BOT_SECRET": TEST_SECRET}, TEST_HASH) == (True, "ok")
    assert L.check({"BOT_SECRET": f"  {TEST_SECRET}  "}, TEST_HASH)[0] is True


@check
def t3():
    """enforce(): верный секрет — пускает (True, без выхода); нет/неверный — пишет «Unauthorized copy detected»
    и выходит (ненулевой код), бот не стартует"""
    logs = []
    assert L.enforce({"BOT_SECRET": TEST_SECRET}, TEST_HASH, log=logs.append) is True and not logs
    for env in ({}, {"BOT_SECRET": "bad"}):
        logs, codes = [], []
        L.enforce(env, TEST_HASH, log=logs.append, _exit=codes.append)
        assert any("Unauthorized copy detected" in m for m in logs), (env, logs)
        assert codes and codes[0] != 0, (env, codes)


MAIN_FAIL = "🔒 main.py без BOT_SECRET должен выйти с «Unauthorized copy detected», не запуская бота"


def run_main(workdir, env, limit=60):
    p = subprocess.run([sys.executable, os.path.join(workdir, "main.py")], cwd=workdir, env=env,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=limit)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def sandbox():
    """Копия запуска: main.py + пакет reloader + подставной бот, который лишь печатает «BOT STARTED»."""
    w = tempfile.mkdtemp()
    shutil.copy(os.path.join(ROOT, "main.py"), w)
    shutil.copytree(os.path.join(ROOT, "reloader"), os.path.join(w, "reloader"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    tio.wfile("BOT_VERSION = 2026110625\nprint('BOT STARTED', flush=True)\n", os.path.join(w, "tg_to_max.py"), "w", encoding="utf-8")
    return w


@check
def t4():
    """main.py: без BOT_SECRET — «Unauthorized copy detected», ненулевой код, бот НЕ стартует; с неверным — то же;
    без секрета подставной бот («BOT STARTED») не выполняется"""
    w = sandbox()
    base = dict(os.environ, TG_MAX_DIR=os.path.join(w, "data"))
    base.pop("BOT_SECRET", None)
    code, out = run_main(w, base)
    assert code != 0 and "Unauthorized copy detected" in out and "BOT STARTED" not in out, (code, out[-500:])
    code, out = run_main(w, dict(base, BOT_SECRET="definitely-wrong"))
    assert code != 0 and "Unauthorized copy detected" in out and "BOT STARTED" not in out, (code, out[-500:])


@check
def t5():
    """main.py с ВЕРНЫМ BOT_SECRET проходит замок и запускает бота («BOT STARTED»), без «Unauthorized». Верный
    секрет берём из окружения (BOT_SECRET) — в репозитории его нет; нет секрета в окружении — проверку пропускаем"""
    secret = os.environ.get("BOT_SECRET", "")
    if not L.secret_ok(secret):
        print("   (пропуск: верный BOT_SECRET не задан в окружении — замок на отказ проверен в t4)")
        return
    w = sandbox()
    code, out = run_main(w, dict(os.environ, BOT_SECRET=secret, TG_MAX_DIR=os.path.join(w, "data")))
    assert "BOT STARTED" in out and "Unauthorized copy detected" not in out, (code, out[-500:])


@check
def t7():
    """защита в глубину: tg_to_max.py запущен напрямую (в обход main.py) без BOT_SECRET — тоже «Unauthorized copy
    detected» и выход (ненулевой код); замок стоит раньше любой работы, даже --check не выполняется"""
    env = dict(os.environ, PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
    env.pop("BOT_SECRET", None)
    for args in ([], ["--check"]):
        p = subprocess.run([sys.executable, os.path.join(ROOT, "tg_to_max.py"), *args], cwd=tempfile.mkdtemp(),
                           env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                           timeout=60)
        out = (p.stdout or "") + (p.stderr or "")
        assert p.returncode != 0 and "Unauthorized copy detected" in out, (args, p.returncode, out[-500:])
    src = tio.rfile(os.path.join(ROOT, "tg_to_max.py"), encoding="utf-8")
    assert "from reloader.services import license" in src, "замок в tg_to_max.py использует общий модуль лицензии"


@check
def t6():
    """build_obfuscated.sh есть, это корректный sh-скрипт (sh -n), упоминает PyArmor и выходит при ошибке
    (set -e); README рассказывает, как выложить защищённую версию и про BOT_SECRET"""
    script = os.path.join(ROOT, "build_obfuscated.sh")
    assert os.path.exists(script), "нет build_obfuscated.sh"
    body = tio.rfile(script, encoding="utf-8").lower()
    assert "pyarmor" in body and ("set -e" in body or "set -eu" in body), "PyArmor и остановка при ошибке"
    assert subprocess.run(["sh", "-n", script]).returncode == 0, "sh -n: синтаксис скрипта"
    readme = tio.rfile(os.path.join(ROOT, "README.txt"), encoding="utf-8")
    assert "BOT_SECRET" in readme and ("pyarmor" in readme.lower() or "build_obfuscated" in readme.lower())


if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    sys.exit(1)
print("ВСЕ ТЕСТЫ ЗАЩИТЫ ОТ КРАЖИ ПРОШЛИ")
