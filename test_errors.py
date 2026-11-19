"""🧾 Коды ошибок: у каждой ошибки, предупреждения и критического сбоя — свой код (ERR_…) и понятное описание.

1) Реестр reloader/core/errors.py: уникальные коды, уровень, описание и что делать; коды из задачи — с теми же
   описаниями; неизвестный код бота не роняет; исключения сами получают код (classify).
2) Журналы: каждая пойманная ошибка пишется с кодом — «[ERR_AI_429] Лимиты ИИ исчерпаны …» — в bot.log, в консоль
   и (для хозяина) в «📜 Журнал»; ни одного предупреждения или сбоя без кода (проверка исходников).
3) Сообщения: покупателю — вежливо и в конце «Код ошибки: ERR_…» (без технических подробностей); хозяину и админам —
   код, описание, точные технические подробности и что сделать.
4) docs/ERRORS.md — все коды, что значат и что делать админу; собран из реестра и не расходится с ним.
Бот в host-режиме, поддельный MAX и ЮKassa, настоящие нажатия кнопок."""
import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import ast
import asyncio
import contextlib
import errno
import glob
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from fake_max import FakeMax  # noqa: E402

try:
    from reloader.core import errors as E
except ImportError:                                     # модуля ещё нет — проверки скажут, чего не хватает
    E = None

FAILS = []
CODE_RE = re.compile(r"\bERR_[A-Z0-9]+(?:_[A-Z0-9]+)*\b")


def check(fn):
    """Каждая проверка — отдельно: красная не мешает увидеть остальные."""
    try:
        fn()
        print(f"{fn.__name__} ok: {fn.__doc__}")
    except Exception as e:                               # и «нет такой функции» — тоже провал проверки, а не падение
        FAILS.append(fn.__name__)
        line = traceback.extract_tb(e.__traceback__)[-1]
        print(f"{fn.__name__} FAIL (строка {line.lineno}: {line.line}) — {type(e).__name__}: {str(e)[:700]}")
    return fn


# ---------- 1) реестр кодов: без бота ----------
REQUIRED = {
    "ERR_YK_401": "ЮKassa отклонила ключи (401 Unauthorized)",
    "ERR_YK_400": "Ошибка запроса к ЮKassa (неверная сумма или чек)",
    "ERR_AI_429": "Лимиты ИИ исчерпаны (все ключи вернули 429)",
    "ERR_AI_TIMEOUT": "ИИ не ответил вовремя",
    "ERR_MAX_403": "Бот не является администратором канала MAX",
    "ERR_MAX_UPLOAD": "Ошибка загрузки медиафайла в MAX",
    "ERR_TG_DOWNLOAD": "Не удалось скачать файл из Telegram",
    "ERR_SPAM_FLOOD": "Канал заблокирован из-за флуда спамом",
    "ERR_LICENSE_MISSING": "Не задан BOT_SECRET (Защита от копирования)",
}
SOURCES = [os.path.join(ROOT, "tg_to_max.py"), os.path.join(ROOT, "main.py")] + sorted(
    p for p in glob.glob(os.path.join(ROOT, "reloader", "**", "*.py"), recursive=True) if "__pycache__" not in p)


@check
def e1():
    """реестр: коды из задачи — с теми же описаниями; каждый код уникален, вида ERR_…, у каждого уровень (critical /
    error / warning), раздел, описание и «что делать» — по-русски; кодов хватает на все ошибки бота"""
    assert E is not None, "нет модуля reloader.core.errors"
    for code, text in REQUIRED.items():
        assert code in E.ERRORS and E.ERRORS[code].text == text, (code, E.ERRORS.get(code))
    assert len(E.REGISTRY) == len(E.ERRORS) >= 40, ("повтор кода в реестре или кодов мало", len(E.REGISTRY))
    for code, info in E.ERRORS.items():
        assert CODE_RE.fullmatch(code) and info.code == code, code
        assert info.level in ("critical", "error", "warning"), (code, info.level)
        assert info.area and info.text and info.fix, code
        for s in (info.text, info.fix):
            assert re.search("[а-яё]", s.lower()), (code, s)
    assert E.ERRORS["ERR_LICENSE_MISSING"].level == "critical" and E.ERRORS["ERR_INTERNAL"].level == "error"
    assert E.ERRORS["ERR_YK_401"].fix.startswith("Проверьте shopId и секретный ключ в панели")


@check
def e2():
    """формат: в журнале «[КОД] Описание — подробности»; покупателю — его вежливый текст и в конце «Код ошибки: КОД»
    (без подробностей); хозяину — код, описание, что случилось, «Подробности: …» и «Что сделать: …»; неизвестный код
    не роняет бота (внутренняя ошибка); коды находятся в тексте"""
    assert E.tag("ERR_AI_429") == "[ERR_AI_429]"
    assert E.line("ERR_AI_429") == "[ERR_AI_429] Лимиты ИИ исчерпаны (все ключи вернули 429)"
    assert E.line("ERR_AI_429", "AI skipped") == "[ERR_AI_429] Лимиты ИИ исчерпаны (все ключи вернули 429) — AI skipped"
    assert E.user("ERR_YK_401", "😔 Не удалось создать оплату.") == "😔 Не удалось создать оплату.\n\nКод ошибки: ERR_YK_401"
    plain = E.user("ERR_INTERNAL")
    assert plain.startswith("😔") and plain.endswith("Код ошибки: ERR_INTERNAL"), plain
    a = E.admin("ERR_YK_401", "HTTP 401: invalid_credentials", what="ЮKassa не дала ссылку на оплату")
    for part in ("[ERR_YK_401]", "ЮKassa отклонила ключи (401 Unauthorized)", "ЮKassa не дала ссылку на оплату",
                 "Подробности: HTTP 401: invalid_credentials", "Что сделать: " + E.ERRORS["ERR_YK_401"].fix):
        assert part in a, (part, a)
    assert E.admin("ERR_MAX_403").startswith("⛔ [ERR_MAX_403]") and E.admin("ERR_AI_429").startswith("⚠️ [ERR_AI_429]")
    assert E.admin("ERR_LICENSE_MISSING").startswith("🛑 [ERR_LICENSE_MISSING]")
    assert E.get("ERR_NOPE").code == "ERR_INTERNAL" and E.get(None).code == "ERR_INTERNAL"
    assert E.codes_in("x [ERR_YK_401] y ERR_AI_429 z ERR_") == ["ERR_YK_401", "ERR_AI_429"]


class _Resp:
    def __init__(self, code, body=None):
        self.status_code, self._body, self.headers, self.text = code, body or {}, {}, str(body or "")

    def json(self):
        return self._body


class _Http:
    """Заглушка HTTP для ИИ: на каждый запрос — ответ или исключение из списка по кругу."""

    def __init__(self, *outs):
        self.outs, self.n = outs, 0

    def post(self, *a, **k):
        got = self.outs[self.n % len(self.outs)]
        self.n += 1
        if isinstance(got, Exception):
            raise got
        return got


@check
def e3():
    """код из исключения: свой код у исключения бота — он; у Telethon код — число, это не наш код; тайм-аут — ERR_TIMEOUT;
    нет сети — ERR_NET; диск полон — ERR_DATA_WRITE; прочее — ERR_INTERNAL; ИИ: все 429 — ERR_AI_429, тайм-ауты —
    ERR_AI_TIMEOUT, ключ не принят — ERR_AI_KEY, ключей нет — ERR_AI_NO_KEYS, иное — ERR_AI_FAIL (по настоящему
    пересказу через ротацию ключей)"""
    import requests
    from reloader.services.ai.keys import KeyRotator
    from reloader.services.ai.rewrite import AiUnavailable, rewrite
    assert E.classify(E.CodedError("ERR_YK_401", "HTTP 401")) == "ERR_YK_401"
    assert str(E.CodedError("ERR_YK_401", "HTTP 401")) == "HTTP 401" and E.CodedError("ERR_MAX_403").code == "ERR_MAX_403"

    class WithCode(Exception):
        code = "ERR_MAX_403"

    class RpcLike(Exception):
        code = 420

    assert E.classify(WithCode()) == "ERR_MAX_403" and E.classify(RpcLike()) == "ERR_INTERNAL"
    for exc in (TimeoutError(), asyncio.TimeoutError(), requests.ReadTimeout("x"), requests.ConnectTimeout("x")):
        assert E.classify(exc) == "ERR_TIMEOUT", exc
    for exc in (requests.ConnectionError("x"), ConnectionResetError()):
        assert E.classify(exc) == "ERR_NET", exc
    assert E.classify(OSError(errno.ENOSPC, "No space left on device")) == "ERR_DATA_WRITE"
    assert E.classify(ValueError("x")) == "ERR_INTERNAL" and E.classify(None) == "ERR_INTERNAL"
    for kinds, want in ((["429", "429"], "ERR_AI_429"), (["timeout"], "ERR_AI_TIMEOUT"), (["key", "429"], "ERR_AI_KEY"),
                        ([], "ERR_AI_NO_KEYS"), (["error"], "ERR_AI_FAIL"), (["429", "timeout"], "ERR_AI_TIMEOUT"),
                        (["429", "error"], "ERR_AI_429")):
        assert E.ai_code(kinds) == want, (kinds, E.ai_code(kinds))
    cfg = {"openrouter": {"url": "http://ai", "model": "m"}}
    for http, want in ((_Http(_Resp(429)), "ERR_AI_429"), (_Http(requests.ReadTimeout("slow")), "ERR_AI_TIMEOUT"),
                       (_Http(_Resp(401, {"error": "bad key"})), "ERR_AI_KEY"), (_Http(_Resp(500)), "ERR_AI_FAIL")):
        rot = KeyRotator("openrouter", lambda: ["k1", "k2"])
        try:
            rewrite("пост", {"openrouter": rot}, http, cfg, log=lambda *_a: None)
            raise AssertionError("ИИ должен был не ответить")
        except AiUnavailable as e:
            assert E.classify(e) == want and e.code == want, (want, e.code, e)
    try:
        rewrite("пост", {"openrouter": KeyRotator("openrouter", lambda: [])}, _Http(_Resp(200)), cfg, log=lambda *_a: None)
    except AiUnavailable as e:
        assert E.classify(e) == "ERR_AI_NO_KEYS", e.code


@check
def e4():
    """docs/ERRORS.md: собран из реестра (не расходится с ним) — каждый код, его описание и что делать админу;
    в README — ссылка на этот файл"""
    path = os.path.join(ROOT, "docs", "ERRORS.md")
    assert os.path.exists(path), "нет docs/ERRORS.md"
    doc = tio.rfile(path, encoding="utf-8")
    assert doc == E.markdown(), "docs/ERRORS.md устарел — пересоздай: python -m reloader.core.errors > docs/ERRORS.md"
    for code, info in E.ERRORS.items():
        assert f"`{code}`" in doc and info.text in doc and info.fix in doc, code
    assert "docs/ERRORS.md" in tio.rfile(os.path.join(ROOT, "README.txt"), encoding="utf-8")


SINKS = {"journal", "admin_log", "notify", "owner_alert", "host_alert", "seller_notify", "print", "_log_line", "log"}
MARKS = ("⚠️", "⛔", "🛑")
TROUBLE = re.compile(r"Skipped|не удалось|не получилось|ошибк|сбой|failed|не смог|не могу|не открывается|нет связи|"
                     r"не принял|не ответил|Unauthorized|заблокирован|исчерпан|timed out|тайм-аут|испорчен|не записал|"
                     r"отказ", re.I)


CODED = re.compile(r"ERR_|\bcode\b|\berrors\.|\b_errors\.|\bE\.(?:tag|line|user|admin)\b")


def _lit(node):
    """Текст строки-литерала (в f-строке подстановки — «{}»); не литерал — None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _lit(node.left), _lit(node.right)
        return (a or "") + (b or "") if (a or b) else None
    if isinstance(node, ast.IfExp):
        return (_lit(node.body) or "") + (_lit(node.orelse) or "")
    return None


def uncoded_alerts(path):
    """Сообщения в журнал, консоль и админам о сбоях и предупреждениях — без кода ошибки (и без code=…)."""
    out = []
    for n in ast.walk(ast.parse(tio.rfile(path, encoding="utf-8"))):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
        if name in SINKS and n.args:
            arg = n.args[0]
        elif name in ("to_thread", "in_background") and len(n.args) >= 2 and isinstance(n.args[0], ast.Name) \
                and n.args[0].id in SINKS:
            arg = n.args[1]
        else:
            continue
        text = _lit(arg)
        if text is None or any(k.arg == "code" for k in n.keywords) or CODED.search(ast.unparse(arg)):
            continue                                     # код в тексте, из переменной кода или от помощников errors
        if text.lstrip().startswith(MARKS) or TROUBLE.search(text):
            out.append(f"{os.path.relpath(path, ROOT)}:{n.lineno}: {text[:90]!r}")
    return out


@check
def e5():
    """исходники: каждый код ERR_… в коде бота есть в реестре (без опечаток); каждый код реестра где-то применяется;
    ни одного сообщения о сбое или предупреждения (журнал, bot.log, консоль, админам) без кода"""
    used = {}
    for path in SOURCES:
        for code in CODE_RE.findall(tio.rfile(path, encoding="utf-8")):
            used[code] = used.get(code, 0) + 1
    unknown = sorted(c for c in used if c not in E.ERRORS)
    assert not unknown, f"коды не из реестра (опечатка?): {unknown}"
    dead = sorted(c for c in E.ERRORS if used.get(c, 0) < 2)   # одно упоминание — определение в реестре
    assert not dead, f"коды есть в реестре, но нигде не применяются: {dead}"
    bad = [x for p in SOURCES for x in uncoded_alerts(p)]
    assert not bad, f"без кода ошибки ({len(bad)}):\n" + "\n".join(bad[:40])


# ---------- 2) бот в host-режиме: хозяин и покупатель ----------
tele = types.ModuleType("telethon")
tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele
sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, YK_API_URL=srv.url, BOT_TOKEN="TESTTOKEN", TGMAX_HOST="1", OWNER_ID="1",
                  OPENROUTER_API_URL=srv.url, DEEPSEEK_API_URL=srv.url)
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ADMIN_IDS", "YOOKASSA_SHOP_ID",
          "YOOKASSA_SECRET_KEY", "YOOKASSA_RECEIPT_EMAIL", "PARTNER_ID", "GEMINI_API_URL", "OPENROUTER_API_KEYS",
          "DEEPSEEK_API_KEYS"):
    os.environ.pop(k, None)
os.chdir(tempfile.mkdtemp())
tio.wjson({"max_token": "TESTTOKEN", "max_channel_link": ""}, "config.json", "w")
spec = importlib.util.spec_from_file_location("bot", os.path.join(ROOT, "tg_to_max.py"))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
bot.S.trust_env = False
bot.GCFG["trial_days"] = 0
bot.MAX_RETRY_PAUSES[:] = [0, 0, 0]                      # повторы загрузки в MAX — без ожидания (проверки быстрые)
SENT, CUR_CHAT = [], [None]


def _send(chat, text, rows=None, token=None, keep=False):
    SENT.append((chat, text or "", rows or []))
    return f"m{len(SENT)}"


bot.api_send = _send                                      # сообщения и меню в личку — сюда; посты в каналы — в MAX
bot.send_screen = lambda chat, text, rows=None: _send(chat, text, rows)
bot.api_answer = lambda cid, text, rows=None: SENT.append((CUR_CHAT[0], text or "", rows or []))


def msg(uid, chat, text):
    CUR_CHAT[0] = chat
    bot.handle_update({"update_type": "message_created", "message": {
        "sender": {"user_id": uid, "first_name": f"U{uid}"}, "recipient": {"chat_id": chat, "chat_type": "dialog"},
        "body": {"text": text}}})


def press(uid, chat, payload):
    CUR_CHAT[0] = chat
    bot.handle_update({"update_type": "message_callback", "callback": {
        "callback_id": "c", "payload": payload, "user": {"user_id": uid, "first_name": f"U{uid}"}},
        "message": {"recipient": {"chat_id": chat, "chat_type": "dialog"}}})


def said(chat, n0=0):
    """Всё, что бот написал в этот чат после n0 (меню, ответы, оповещения) — одним текстом."""
    return "\n".join(e[1] for e in SENT[n0:] if e[0] == chat)


def botlog():
    return tio.rfile(bot.LOG_FILE, encoding="utf-8") if os.path.exists(bot.LOG_FILE) else ""


def jlines(n0=0):
    return [str(x) for x in list(bot.JOURNAL)[n0:]]


HOST, HC, BUYER, BC = 1, 61000, 5001, 62000
msg(HOST, HC, "/start")
msg(BUYER, BC, "/start")
KEY = bot.tenant_key(BUYER)
bot.host_extend(KEY, 30)
bot.ensure_host_cabinet()
bot.host_extend(bot.host_id(), 30)
TB = bot.tenant_themes(KEY)[0]
TB.name, TB.chat_id = "Канал покупателя", -7101
TH = bot.tenant_themes(bot.host_id())[0]
TH.name, TH.chat_id = "Канал хозяина", -7001
for t in (TB, TH):
    with bot.theme_ctx(t):
        bot.CFG["paused"] = False
        bot.CFG["night_on"] = False
assert bot.HOST_CHAT["chat"] == HC and bot.theme_live(TB) and bot.theme_live(TH)
TECH = re.compile(r"Traceback|Подробности:|invalid_credentials|Error in shopId|Receipt is missing|HTTP \d|"
                  r"ZeroDivision|RuntimeError|секретная деталь|Ответ MAX|chat\.denied", re.I)


@check
def l1():
    """log_exc: код из исключения — в bot.log строка «[КОД] Описание — ошибка в «где»: Тип: текст» и полный текст
    ошибки; в консоль — с кодом; возвращает код; обычная ошибка — ERR_INTERNAL; явный код — главнее"""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            raise E.CodedError("ERR_YK_401", "ЮKassa 401: invalid_credentials")
        except Exception:
            got = bot.log_exc("оплата")
    log = botlog()
    assert got == "ERR_YK_401", got
    assert "[ERR_YK_401] ЮKassa отклонила ключи (401 Unauthorized) — ошибка в «оплата»: CodedError: ЮKassa 401: " \
           "invalid_credentials" in log, log[-600:]
    assert "Traceback (most recent call last)" in log and "[ERR_YK_401]" in out.getvalue(), out.getvalue()
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            raise RuntimeError("что-то сломалось")
        except Exception:
            assert bot.log_exc("где-то") == "ERR_INTERNAL"
        try:
            raise RuntimeError("занято")
        except Exception:
            assert bot.log_exc("загрузка", code="ERR_MAX_UPLOAD") == "ERR_MAX_UPLOAD"
    log = botlog()
    assert "[ERR_INTERNAL] Внутренняя ошибка бота — ошибка в «где-то»: RuntimeError: что-то сломалось" in log
    assert "[ERR_MAX_UPLOAD] Ошибка загрузки медиафайла в MAX — ошибка в «загрузка»" in log


@check
def l2():
    """журнал: journal(текст, code=…) — «[КОД] Описание — текст» в «📜 Журнал» и bot.log; пойманная ошибка видна
    хозяину в его журнале (код и подробности), покупателю — нет; одинаковые ошибки подряд не забивают журнал (bot.log
    пишет всё); упавший поток — ERR_UNCAUGHT"""
    n0 = len(bot.JOURNAL)
    with bot.theme_ctx(TB), contextlib.redirect_stdout(io.StringIO()):
        bot.journal("пост 5 не вышел", code="ERR_PUB_FAIL")
    j = jlines(n0)
    assert any(x.endswith("[ERR_PUB_FAIL] Пост не опубликован после 3 попыток — пост 5 не вышел") for x in j), j
    assert "[ERR_PUB_FAIL] Пост не опубликован после 3 попыток — пост 5 не вышел" in botlog()
    n1 = len(bot.JOURNAL)
    with bot.theme_ctx(TB), contextlib.redirect_stdout(io.StringIO()):
        for _ in range(3):
            try:
                raise RuntimeError("секретная деталь 7")
            except Exception:
                bot.log_exc("проверка журнала")
    mine = [x for x in jlines(n1) if "проверка журнала" in x]
    assert len(mine) == 1 and "[ERR_INTERNAL]" in mine[0] and "секретная деталь 7" in mine[0], mine
    assert botlog().count("ошибка в «проверка журнала»") == 3, "в bot.log — каждая"
    press(HOST, HC, "m:lg")
    assert "проверка журнала" in said(HC, len(SENT) - 1), "хозяин видит ошибку в журнале"
    press(BUYER, BC, "m:lg")
    assert "проверка журнала" not in said(BC, len(SENT) - 1) and "секретная деталь" not in said(BC, len(SENT) - 1)

    def boom():
        raise ValueError("поток упал")
    th = threading.Thread(target=boom)
    with contextlib.redirect_stdout(io.StringIO()):
        th.start()
        th.join(5)
    assert "[ERR_UNCAUGHT]" in botlog() and "поток упал" in botlog(), botlog()[-500:]


def pay_online(uid, chat):
    bot.legal_agree(uid)
    n0 = len(SENT)
    press(uid, chat, "pk:0:30")
    bot.bg_wait(5)
    return n0


@check
def d1():
    """ЮKassa не приняла ключи (401): покупателю — вежливо «онлайн-оплата не открылась» и «Код ошибки: ERR_YK_401» —
    без технических подробностей; хозяину — код, описание, что случилось, точный ответ ЮKassa и «Что сделать:
    Проверьте shopId и секретный ключ…»; в bot.log и журнале хозяина — код; «💰 Тарифы» у хозяина — тоже с кодом"""
    bot.GCFG["yk_shop"], bot.GCFG["yk_key"] = "123456", "test_wrong"
    bot.YK_CHECK_CACHE.clear()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            n0 = pay_online(BUYER, BC)
        buyer, host = said(BC, n0), said(HC, n0)
        assert "Онлайн-оплата сейчас не открылась" in buyer and buyer.rstrip().endswith("Код ошибки: ERR_YK_401"), buyer
        assert not TECH.search(buyer), TECH.search(buyer).group()
        for part in ("[ERR_YK_401]", "ЮKassa отклонила ключи (401 Unauthorized)", "ЮKassa не дала ссылку", "Подробности:",
                     "invalid_credentials", "Что сделать: Проверьте shopId и секретный ключ в панели"):
            assert part in host, (part, host[-900:])
        assert "[ERR_YK_401]" in botlog()
        assert any("[ERR_YK_401]" in x and x.owner == "" for x in list(bot.JOURNAL)), "журнал хозяина"
        press(HOST, HC, "m:cltar")
        assert "ERR_YK_401" in said(HC, len(SENT) - 1), said(HC, len(SENT) - 1)[:800]
    finally:
        bot.GCFG.pop("yk_shop", None)
        bot.GCFG.pop("yk_key", None)
        bot.YK_CHECK_CACHE.clear()


@check
def d2():
    """ЮKassa отклонила запрос (400: в магазине включены чеки, а почты для чека нет): покупателю — «Код ошибки:
    ERR_YK_400» без подробностей; хозяину — код, ответ ЮKassa и подсказка про YOOKASSA_RECEIPT_EMAIL"""
    bot.GCFG["yk_shop"], bot.GCFG["yk_key"] = "123456", "test_secret"
    srv.need_receipt = True
    bot.YK_CHECK_CACHE.clear()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            n0 = pay_online(BUYER, BC)
        buyer, host = said(BC, n0), said(HC, n0)
        assert buyer.rstrip().endswith("Код ошибки: ERR_YK_400") and not TECH.search(buyer), buyer
        for part in ("[ERR_YK_400]", "Ошибка запроса к ЮKassa (неверная сумма или чек)", "Receipt is missing",
                     "YOOKASSA_RECEIPT_EMAIL"):
            assert part in host, (part, host[-900:])
    finally:
        srv.need_receipt = False
        bot.GCFG.pop("yk_shop", None)
        bot.GCFG.pop("yk_key", None)


def queue_item(iid, text, **kw):
    now = time.time()
    it = {"id": iid, "ch": "news", "date": now - 600, "enq": now - 600, "text": text, "photos": [], "vids": [],
          "docs": [], "sent": 0, "fails": 0, "status": "ok"}
    it.update(kw)
    return it


def publish(t, it):
    with bot.theme_ctx(t), contextlib.redirect_stdout(io.StringIO()):
        bot.QUEUE[:] = [it]
        return asyncio.run(bot.publish_item(None, t.chat_id, it))


def ai_reset():
    for r in bot.AI_ROTATORS.values():
        r.reset()


bot.GCFG["ai_keys"] = {"openrouter": ["or-key-0001"], "deepseek": ["ds-key-0001"]}


@check
def d3():
    """ИИ: все ключи — 429 — пост выходит оригиналом, в журнале «[ERR_AI_429] Лимиты ИИ исчерпаны (все ключи
    вернули 429) — AI skipped: all keys exhausted. Falling back to local rules.»"""
    real = bot.ai_on
    bot.ai_on = lambda: True
    srv.ai_rule = lambda _k, _ask: (429, "")
    ai_reset()
    try:
        n0, m0 = len(bot.JOURNAL), len(srv.messages)
        assert publish(TH, queue_item("ai:1", "Новость про игру")) == "ok"
        assert [m["body"].get("text", "").split("\n")[0] for m in srv.messages[m0:]] == ["Новость про игру"]
        want = "[ERR_AI_429] Лимиты ИИ исчерпаны (все ключи вернули 429) — " + bot.AI_EXHAUSTED_LOG
        assert any(x.endswith(want) for x in jlines(n0)), jlines(n0)
    finally:
        bot.ai_on = real
        srv.ai_rule = None


@check
def d4():
    """ИИ не ответил вовремя (каждый ключ — тайм-аут): пост выходит оригиналом, в журнале «[ERR_AI_TIMEOUT] ИИ не
    ответил вовремя — AI skipped…»"""
    real, timeout = bot.ai_on, bot.AI_REWRITE_TIMEOUT
    bot.ai_on, bot.AI_REWRITE_TIMEOUT = (lambda: True), 0.3
    srv.delay = 0.8
    ai_reset()
    try:
        n0 = len(bot.JOURNAL)
        assert publish(TH, queue_item("ai:2", "Ещё новость")) == "ok"
        want = "[ERR_AI_TIMEOUT] ИИ не ответил вовремя — " + bot.AI_EXHAUSTED_LOG
        assert any(x.endswith(want) for x in jlines(n0)), jlines(n0)
    finally:
        bot.ai_on, bot.AI_REWRITE_TIMEOUT = real, timeout
        srv.delay = 0
        ai_reset()


@check
def d5():
    """MAX не пускает бота в канал (403): покупателю — вежливо, что сделать, и «Код ошибки: ERR_MAX_403» (без ответа
    MAX и названий ошибок); хозяину по его каналу — код, описание, точный ответ MAX и что сделать; в журнале — код"""
    srv.deny_chats.update({TB.chat_id, TH.chat_id})
    try:
        n0, j0 = len(SENT), len(bot.JOURNAL)
        for t in (TB, TH):
            for _ in range(2):                             # первый отказ MAX бывает случайным — пишем со второго
                assert publish(t, queue_item(f"deny:{t.id}", "Пост")) == "retry"
                bot.DENIED.pop(t.chat_id, None)
        bot.bg_wait(5)
        buyer, host = said(BC, n0), said(HC, n0)
        assert "Код ошибки: ERR_MAX_403" in buyer and not TECH.search(buyer), buyer
        assert "администратор" in buyer.lower() or "админ" in buyer.lower(), buyer
        for part in ("[ERR_MAX_403]", "Бот не является администратором канала MAX", "Подробности:", "403"):
            assert part in host, (part, host[-900:])
        assert any("[ERR_MAX_403]" in x for x in jlines(j0)), jlines(j0)
    finally:
        srv.deny_chats.clear()
        for t in (TB, TH):
            bot.DENIED.pop(t.chat_id, None)
            bot.DENY_N.pop(t.chat_id, None)
            bot.DENIED_TOLD.pop(t.chat_id, None)


def admin_lines(n0):
    return [x for x in list(bot.JOURNAL)[n0:] if x.owner == ""]


@check
def d6():
    """файл не загрузился в MAX и после повторов: пост пропущен целиком; в журнале хозяина «[ERR_MAX_UPLOAD] Ошибка
    загрузки медиафайла в MAX — … Skipped: upload failed …»; покупателю — ни слова"""
    jpg = os.path.abspath("photo.jpg")
    tio.wfile(b"\xff\xd8\xff" + b"0" * 2000, jpg, "wb")
    srv.fail_uploads["image"] = 10
    try:
        n0, j0 = len(SENT), len(bot.JOURNAL)
        assert publish(TB, queue_item("up:1", "Пост с фото", photos=[jpg])) == "dropped"
        mine = [str(x) for x in admin_lines(j0)]
        assert any("[ERR_MAX_UPLOAD] Ошибка загрузки медиафайла в MAX" in x and "Skipped: upload failed" in x
                   for x in mine), mine
        assert not said(BC, n0), said(BC, n0)
    finally:
        srv.fail_uploads["image"] = 0


@check
def d7():
    """фото поста не скачалось из Telegram: пост пропущен; в журнале хозяина «[ERR_TG_DOWNLOAD] Не удалось скачать
    файл из Telegram — … Skipped: download failed …»"""
    j0 = len(bot.JOURNAL)
    assert publish(TB, queue_item("dl:1", "Пост без фото на диске", photos=["нет-такого-файла.jpg"])) == "dropped"
    mine = [str(x) for x in admin_lines(j0)]
    assert any("[ERR_TG_DOWNLOAD] Не удалось скачать файл из Telegram" in x and "Skipped: download failed" in x
               for x in mine), mine


@check
def d8():
    """флуд спамом: канал на час в мут — покупателю «⚠️ Канал @… заблокирован на 1 час из-за флуда спамом.» и
    «Код ошибки: ERR_SPAM_FLOOD»; в журнале — «[ERR_SPAM_FLOOD] Канал заблокирован из-за флуда спамом …»"""
    n0, j0 = len(SENT), len(bot.JOURNAL)
    with bot.theme_ctx(TB), contextlib.redirect_stdout(io.StringIO()):
        for i in range(bot.SPAM_FLOOD_N):
            bot.eat_spam("spamchan", 100 + i, "казино", "casino")
    bot.bg_wait(5)
    buyer = said(BC, n0)
    assert "Канал @spamchan заблокирован на 1 час из-за флуда спамом." in buyer, buyer
    assert buyer.rstrip().endswith("Код ошибки: ERR_SPAM_FLOOD"), buyer
    assert any("[ERR_SPAM_FLOOD] Канал заблокирован из-за флуда спамом" in x for x in jlines(j0)), jlines(j0)


@check
def d9():
    """защита от копирования: main.py без BOT_SECRET — «[ERR_LICENSE_MISSING] Не задан BOT_SECRET (Защита от
    копирования)», с неверным — «[ERR_LICENSE_BAD]»; бот не стартует; то же пишет общий модуль лицензии"""
    from reloader.services import license as L
    env = {k: v for k, v in os.environ.items() if k != "BOT_SECRET"}
    for secret, code in ((None, "ERR_LICENSE_MISSING"), ("неверный", "ERR_LICENSE_BAD")):
        e = dict(env, **({"BOT_SECRET": secret} if secret else {}))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "main.py")], cwd=tempfile.mkdtemp(), env=e,
                           capture_output=True, text=True, timeout=60, check=False)
        out = r.stdout + r.stderr
        assert r.returncode != 0 and f"[{code}] {E.ERRORS[code].text}" in out, (code, out[-500:])
        assert "Unauthorized copy detected" in out
        logs = []
        L.enforce(env={} if secret is None else {"BOT_SECRET": secret}, log=logs.append, _exit=lambda _c: None)
        assert any(f"[{code}]" in x for x in logs), logs


@check
def d10():
    """кнопка сломалась: покупателю — «⚠️ Не получилось — попробуй ещё раз…» и «Код ошибки: ERR_INTERNAL» (без
    текста ошибки); хозяину — код и точная ошибка (тип и текст)"""
    real_menu, real_stats = bot.buyer_menu, bot.host_stats

    def broken(*_a, **_k):
        raise ZeroDivisionError("секретная деталь 42")
    bot.buyer_menu = bot.host_stats = broken
    try:
        n0 = len(SENT)
        with contextlib.redirect_stdout(io.StringIO()):
            press(BUYER, BC, "m:main")
            press(HOST, HC, "m:cl")
        buyer, host = said(BC, n0), said(HC, n0)
        assert "Не получилось" in buyer and buyer.rstrip().endswith("Код ошибки: ERR_INTERNAL"), buyer
        assert not TECH.search(buyer), TECH.search(buyer).group()
        assert "[ERR_INTERNAL]" in host and "ZeroDivisionError" in host and "секретная деталь 42" in host, host
    finally:
        bot.buyer_menu, bot.host_stats = real_menu, real_stats


@check
def d11():
    """аналитика не получилась: покупателю — вежливо и «Код ошибки: ERR_ANALYTICS» (без текста ошибки); хозяину —
    «[ERR_ANALYTICS]» и точная ошибка"""
    real = bot.week_stats

    def broken(*_a, **_k):
        raise RuntimeError("секретная деталь 9")
    bot.week_stats = broken
    try:
        n0 = len(SENT)
        with contextlib.redirect_stdout(io.StringIO()):
            bot.analytics_send(BC, [("Канал покупателя", TB.chat_id)])
            bot.analytics_send(HC, [("Канал хозяина", TH.chat_id)])
        buyer, host = said(BC, n0), said(HC, n0)
        assert "Аналитика не получилась" in buyer and buyer.rstrip().endswith("Код ошибки: ERR_ANALYTICS"), buyer
        assert not TECH.search(buyer), TECH.search(buyer).group()
        assert "[ERR_ANALYTICS]" in host and "секретная деталь 9" in host, host
    finally:
        bot.week_stats = real


@check
def d12():
    """файл данных испортился — бот поднимает его из резервной копии и пишет «[ERR_DATA_RESTORED]»"""
    from reloader.core.files import load_json_safe, save_json
    p = os.path.abspath("data_check.json")
    save_json(p, {"a": 1}, backup=True)
    tio.wfile("{oops", p, "w")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert load_json_safe(p, {}) == {"a": 1}
    assert "[ERR_DATA_RESTORED]" in out.getvalue(), out.getvalue()


if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    os._exit(1)
print("ВСЕ ТЕСТЫ КОДОВ ОШИБОК ПРОШЛИ")
os._exit(0)
