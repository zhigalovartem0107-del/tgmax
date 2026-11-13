"""Файлы в проверках: прочитать / записать и сразу закрыть (без ResourceWarning «unclosed file»).

tio.rjson(путь, ...)            — json.load(open(...))
tio.wjson(объект, путь, "w")   — json.dump(объект, open(...)); параметры json.dump — в _dump=dict(...)
tio.rfile(путь, ...)            — open(...).read()
tio.wfile(данные, путь, "w")   — open(...).write(данные); возвращает, сколько записано (как write)
tio.reap(p)                    — остановить процесс проверки и закрыть его каналы (stdin/stdout/stderr)"""
import contextlib
import json


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
