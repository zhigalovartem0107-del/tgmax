"""Проверки секрета запуска и хеша из переменных хостинга."""
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

from bot_app.services import license as L  # noqa: E402

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
    assert not L.secret_ok(TEST_SECRET)
    main_src = tio.rfile(os.path.join(ROOT, "main.py"), encoding="utf-8")
    assert "from bot_app.services.license import enforce" in main_src


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
    """Копия запуска: main.py + пакет bot_app + подставной бот, который лишь печатает «BOT STARTED»."""
    w = tempfile.mkdtemp()
    shutil.copy(os.path.join(ROOT, "main.py"), w)
    shutil.copytree(os.path.join(ROOT, "bot_app"), os.path.join(w, "bot_app"),
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
    """Запуск принимает только верную пару из окружения."""
    w = sandbox()
    env = dict(os.environ, BOT_SECRET=TEST_SECRET, TG_MAX_DIR=os.path.join(w, "data"))
    env.pop("BOT_SECRET_SHA256", None)
    for value in ("", "abc", "0" * 64):
        code, out = run_main(w, dict(env, BOT_SECRET_SHA256=value))
        assert code == 3 and "BOT STARTED" not in out
        assert TEST_SECRET not in out and TEST_HASH not in out
    code, out = run_main(w, dict(env, BOT_SECRET_SHA256=TEST_HASH))
    assert code == 0 and "BOT STARTED" in out and "Unauthorized copy detected" not in out, out[-500:]


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
    assert "from bot_app.services import license" in src, "замок в tg_to_max.py использует общий модуль лицензии"


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
