"""Журнал бота: всё, что бот печатает, дублируется в bot.log; журнал постов (/log); ошибки с подробностями."""
import asyncio
import builtins
import os
import sys
import threading
import time
import traceback
from collections import deque

CUR = None                           # контекст темы (ставит tg_to_max/themes): чей кабинет у строки журнала
LOG_FILE = "bot.log"
JOURNAL = deque(maxlen=300)          # что бот делал с постами — для команды /log и кнопки «Журнал»


def _log_line(line):
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _quiet_unraisable(arg):
    """Telethon при обрыве связи шлёт «Exception ignored … _recv_loop … GeneratorExit» — это не ошибка бота.
    Такие строки пишем только в bot.log, в консоль хостинга не сыплем."""
    exc = getattr(arg, "exc_value", None)
    if isinstance(exc, (GeneratorExit, asyncio.CancelledError)) or "telethon" in repr(getattr(arg, "object", "")):
        _log_line(time.strftime("%H:%M:%S") + f" (тихо) {type(exc).__name__}: {getattr(arg, 'object', '')!r}"[:300])
        return
    sys.__unraisablehook__(arg)


sys.unraisablehook = _quiet_unraisable


def print(*args, **kwargs):          # всё, что бот пишет в консоль, дублируется в bot.log
    line = time.strftime("%H:%M:%S") + " " + " ".join(str(a) for a in args)
    kwargs.setdefault("flush", True)
    builtins.print(line, **kwargs)
    _log_line(line)


class _JLine(str):
    """Строка журнала + чей это кабинет (в host-режиме журнал показываем только владельцу)."""
    owner = ""


def journal(text):
    """Запись о том, что произошло с постом: видна в консоли, в bot.log и в /log."""
    print(text)
    line = _JLine(time.strftime("%d.%m %H:%M") + " " + text)
    t = CUR.get() if CUR is not None else None
    line.owner = getattr(t, "owner", "") or ""
    JOURNAL.append(line)


def log_exc(where):
    """Ошибка с подробностями: в консоль — одна строка, полный текст — в bot.log."""
    e = sys.exc_info()[1]
    _log_line(time.strftime("%H:%M:%S") + f" ОШИБКА в {where}:\n" + traceback.format_exc())
    builtins.print(time.strftime("%H:%M:%S") + f" ⚠️ {where}: {type(e).__name__}: {e} (подробности в {LOG_FILE})",
                   flush=True)


def _excepthook(tp, val, tb):
    _log_line("НЕПЕРЕХВАЧЕННАЯ ОШИБКА:\n" + "".join(traceback.format_exception(tp, val, tb)))
    builtins.print(f"⚠️ {tp.__name__}: {val} (подробности в {LOG_FILE})", flush=True)


sys.excepthook = _excepthook
threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback)
