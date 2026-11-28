"""Журнал бота: всё, что бот печатает, дублируется в bot.log; журнал постов (/log); ошибки с подробностями.
У каждой пойманной ошибки — код из bot_app/core/errors.py: «[ERR_AI_429] Лимиты ИИ исчерпаны …»."""
import asyncio
import builtins
import os
import sys
import threading
import time
import traceback
from collections import deque

from bot_app.core import errors as _errors

CUR = None                           # контекст темы (ставит tg_to_max/themes): чей кабинет у строки журнала
LOG_FILE = "bot.log"
JOURNAL = deque(maxlen=300)          # что бот делал с постами — для команды /log и кнопки «Журнал»
ON_ERROR = None                      # бот ставит сюда: (код, где, исключение) -> запись в журнал хозяина


_LOG_LOCK = threading.Lock()   # пишут многие потоки: строки не перемешиваются, ротацию делает один


def _log_line(line):
    """Строка в bot.log. Без лишних обращений к диску (размер — по самому открытому файлу): журнал пишется и из
    цикла asyncio. Больше 1 МБ — старое в bot.log.1 (один поток; раньше двое разом затирали архив)."""
    try:
        with _LOG_LOCK:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                big = f.tell() > 1_000_000
            if big:
                os.replace(LOG_FILE, LOG_FILE + ".1")
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


def journal(text, code=None):
    """Запись о том, что произошло с постом: видна в консоли, в bot.log и в /log. code — ошибка или предупреждение:
    строка «[КОД] Описание — текст»."""
    if code:
        text = _errors.line(code, text)
    print(text)
    line = _JLine(time.strftime("%d.%m %H:%M") + " " + text)
    t = CUR.get() if CUR is not None else None
    line.owner = getattr(t, "owner", "") or ""
    JOURNAL.append(line)


def log_exc(where, code=None):
    """Пойманная ошибка с кодом: в bot.log — «[КОД] Описание — ошибка в «где»: Тип: текст» и полный текст ошибки,
    в консоль — одна строка с кодом, хозяину — запись в журнал (ON_ERROR). Код — явный или по исключению
    (errors.classify). Возвращает код: его показывают в сообщении человеку."""
    e = sys.exc_info()[1]
    code = code if code in _errors.ERRORS else _errors.classify(e)
    what = f"{type(e).__name__}: {e}" if e is not None else "—"
    _log_line(time.strftime("%H:%M:%S") + f" {_errors.line(code)} — ошибка в «{where}»: {what}\n"
              + traceback.format_exc())
    builtins.print(time.strftime("%H:%M:%S") + f" ⚠️ [{code}] {where}: {what} (подробности в {LOG_FILE})", flush=True)
    hook = ON_ERROR
    if hook is not None:
        try:
            hook(code, where, e)
        except Exception:                                # журнал хозяина не должен ронять обработку ошибки
            pass
    return code


def _excepthook(tp, val, tb):
    _log_line(_errors.line("ERR_UNCAUGHT", "НЕПЕРЕХВАЧЕННАЯ ОШИБКА") + ":\n"
              + "".join(traceback.format_exception(tp, val, tb)))
    builtins.print(f"🛑 [ERR_UNCAUGHT] {tp.__name__}: {val} (подробности в {LOG_FILE})", flush=True)


sys.excepthook = _excepthook
threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback)
