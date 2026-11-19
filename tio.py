"""Файлы в проверках: прочитать / записать и сразу закрыть (без ResourceWarning «unclosed file»).

tio.rjson(путь, ...)            — json.load(open(...))
tio.wjson(объект, путь, "w")   — json.dump(объект, open(...)); параметры json.dump — в _dump=dict(...)
tio.rfile(путь, ...)            — open(...).read()
tio.wfile(данные, путь, "w")   — open(...).write(данные); возвращает, сколько записано (как write)
tio.reap(p)                    — остановить процесс проверки и закрыть его каналы (stdin/stdout/stderr)
tio.licensed_copy(root)        — копия бота для запуска процессом, замок — на тестовый секрет TEST_SECRET"""
import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile

TEST_SECRET = "tgmax-test-only-secret"     # только для проверок: копия бота с замком на этот секрет
_COPIES = {}


def rjson(*a, **k):
    with open(*a, **k) as f:
        return json.load(f)


def wjson(obj, *a, _dump=None, **k):
    with open(*a, **k) as f:
        json.dump(obj, f, **(_dump or {}))


def rfile(*a, **k):
    with open(*a, **k) as f:
        return f.read()


def wfile(data, *a, **k):
    with open(*a, **k) as f:
        return f.write(data)


def reap(p, timeout=10):
    """Остановить процесс проверки: kill, дождаться, дать дочитать потоку-читателю (p.reader, если есть) и закрыть
    каналы — без «subprocess still running» и «unclosed file»."""
    with contextlib.suppress(Exception):
        p.kill()
    with contextlib.suppress(Exception):
        p.wait(timeout)
    reader = getattr(p, "reader", None)
    if reader is not None:
        reader.join(5)
    for s in (p.stdin, p.stdout, p.stderr):
        if s is not None:
            with contextlib.suppress(Exception):
                s.close()


def licensed_copy(root):
    """Копия бота (все файлы, кроме .git, кэша и проверок) для проверок, где бот запускается процессом
    (python tg_to_max.py / main.py): замок защиты от копирования в КОПИИ настроен на тестовый секрет TEST_SECRET.
    Настоящий BOT_SECRET проверкам не нужен (его нет в репозитории), код бота не меняется; сам замок на настоящем
    коде проверяет test_license. Одна копия на процесс проверки. Возвращает папку копии."""
    if root in _COPIES:
        return _COPIES[root]
    dst = tempfile.mkdtemp(prefix="tgmax_licensed_")
    skip = shutil.ignore_patterns(".git", "__pycache__", "test_*.py", "*.pyc")
    for name in os.listdir(root):
        src = os.path.join(root, name)
        if name in (".git", "__pycache__") or name.startswith("test_"):
            continue
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dst, name), ignore=skip)
        else:
            shutil.copy2(src, dst)
    want = hashlib.sha256(TEST_SECRET.encode("utf-8")).hexdigest()
    for rel, var in (("reloader/services/license.py", "EXPECTED_SHA256"), ("main.py", "_SECRET_SHA256")):
        path = os.path.join(dst, rel)
        src = rfile(path, encoding="utf-8")
        new = re.sub(rf'^({var} = )"[0-9a-f]{{64}}"', lambda m: f'{m.group(1)}"{want}"', src, flags=re.M)
        assert new != src, f"не нашёл хеш секрета в {rel}"
        wfile(new, path, "w", encoding="utf-8")
    _COPIES[root] = dst
    return dst
