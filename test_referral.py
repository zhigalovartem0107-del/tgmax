"""🤝 Партнёрская ссылка (без меню для партнёров): хозяин задаёт ID партнёра (/partner 123456 или переменная
PARTNER_ID), по /ref («🔗 Сгенерировать ссылку для клиента») пересылает сообщение нового клиента (или его ID в MAX) —
бот берёт ID клиента и отвечает ссылкой https://max.ru/<бот>?start=ref_<ID партнёра>, закрепляя клиента за партнёром.
Кто пришёл по ссылке (?start=ref_…), закреплён за партнёром; купил подписку — хозяину «💰 Клиент [Имя] купил
подписку по ссылке партнера [ID]!». Выплаты партнёру — вручную."""
import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import importlib.util
import os
import sys
import tempfile
import traceback
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from fake_max import FakeMax  # noqa: E402

try:
    from reloader.services import referral as R
except ImportError:                                     # модуля ещё нет — проверки скажут, чего не хватает
    R = None

FAILS = []


def check(fn):
    try:
        fn()
        print(f"{fn.__name__} ok: {fn.__doc__}")
    except Exception as e:                               # и «нет такой функции» — тоже провал проверки, а не падение
        FAILS.append(fn.__name__)
        line = traceback.extract_tb(e.__traceback__)[-1]
        print(f"{fn.__name__} FAIL (строка {line.lineno}: {line.line}) — {type(e).__name__}: {str(e)[:400]}")
    return fn


# ---------- 1) чистая логика: ID клиента из пересланного сообщения, ссылка, ID партнёра из ?start= ----------
@check
def r1():
    """ID клиента — из пересланного сообщения человека (или ID цифрами); ссылка — https://max.ru/<бот>?start=ref_<ID>;
    ID партнёра — из ?start=ref_<ID>, мусор не принимаем"""
    assert R is not None, "нет модуля reloader.services.referral"
    fwd = {"link": {"type": "forward", "chat_id": 555, "sender": {"user_id": 7777, "name": "Иван Петров"},
                    "message": {"text": "Здравствуйте, хочу подключить бота"}}}
    assert R.client_from_message(fwd, "") == (7777, "Иван Петров")
    first = {"link": {"type": "forward", "sender": {"user_id": 7778, "first_name": "Оля"}, "message": {"text": "hi"}}}
    assert R.client_from_message(first, "") == (7778, "Оля")
    for text, want in (("8888", 8888), ("  123456789 ", 123456789), ("ID 4242", 4242), ("id:4243", 4243)):
        assert R.client_from_message({}, text) == (want, None), (text, R.client_from_message({}, text))
    channel_post = {"link": {"type": "forward", "chat_id": -7100, "message": {"text": "пост канала"}}}
    for msg, text in ((channel_post, ""), ({}, "привет"), ({}, "12"), ({}, "@ivan"), ({}, ""), (None, None),
                      ({"link": {"type": "reply", "sender": {"user_id": 1}}}, "")):
        assert R.client_from_message(msg, text) == (None, None), (msg, text)
    assert R.link("testbot", "123456") == "https://max.ru/testbot?start=ref_123456"
    for payload, want in (("ref_123456", "123456"), ("ref_Part-ner_1", "Part-ner_1"), (" ref_42 ", "42")):
        assert R.partner_from_payload(payload) == want, payload
    for bad in ("", None, "ref_", "123456", "ref_<script>", "ref_" + "9" * 40, "promo_5", "ref_12 34"):
        assert R.partner_from_payload(bad) is None, bad
    assert R.valid_pid("123456") and not R.valid_pid("12 34") and not R.valid_pid("") and not R.valid_pid(None)


@check
def r2():
    """учёт: клиент закрепляется за первым партнёром (второй не перебивает); покупки по партнёру считаются;
    файл переживает перезапуск, испорченный — не падаем"""
    assert R is not None
    d = tempfile.mkdtemp()
    path = os.path.join(d, "referrals.json")
    save = lambda p, data, **k: tio.wjson(data, p, "w", _dump=dict(ensure_ascii=False))
    load = lambda p, default: tio.rjson(p) if os.path.exists(p) else default
    st = R.Referrals(path, save=save, load=load)
    assert st.attach(7777, "123456", "ссылка", "Иван") == (True, "123456")
    assert st.attach("7777", "999", "ссылка", "Иван") == (False, "123456"), "первый партнёр остаётся"
    assert st.partner_of(["1", 7777]) == "123456" and st.partner_of([5]) is None
    st.sale("123456", "7777", "Иван", "Про", 30, 490, "ЮKassa")
    st.sale("123456", "7777", "Иван", "Про", 30, 490, "перевод")
    s2 = R.Referrals(path, save=save, load=load)
    assert s2.partner_of([7777]) == "123456" and s2.stats("123456") == {"clients": 1, "sales": 2, "sum": 980}
    s2.forget(["7777"])                                     # клиент попросил удалить данные (152-ФЗ)
    assert s2.partner_of([7777]) is None and all(not s["name"] for s in s2.sales if s["key"] == "7777")
    assert s2.stats("123456")["sum"] == 980, "суммы для расчёта с партнёром остаются, без имени"
    tio.wfile('{"clients": [1, 2], "sales": "x"', path, "w")
    assert R.Referrals(path, save=save, load=load).partner_of([7777]) is None


# ---------- 2) бот в host-режиме: хозяин делает ссылку, клиент приходит по ней и покупает ----------
tele = types.ModuleType("telethon")
tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele
sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, BOT_TOKEN="TESTTOKEN", TGMAX_HOST="1", OWNER_ID="1")
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ADMIN_IDS", "PARTNER_ID"):
    os.environ.pop(k, None)
os.chdir(tempfile.mkdtemp())
tio.wjson({"max_token": "TESTTOKEN", "max_channel_link": ""}, "config.json", "w")
spec = importlib.util.spec_from_file_location("bot", os.path.join(ROOT, "tg_to_max.py"))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
bot.S.trust_env = False
bot.GCFG["trial_days"] = 0

HOST, HC = 1, 61000
LINK = "https://max.ru/testbot?start=ref_123456"
MONEY = "💰 Клиент Иван купил подписку по ссылке партнера 123456!"


def msg(uid, chat, text, link=None, name=None):
    m = {"sender": {"user_id": uid, "first_name": name or f"U{uid}"},
         "recipient": {"chat_id": chat, "chat_type": "dialog"}, "body": {"text": text}}
    if link:
        m["link"] = link
    bot.handle_update({"update_type": "message_created", "message": m})


def started(uid, chat, payload=None, name=None):
    u = {"update_type": "bot_started", "chat_id": chat, "user": {"user_id": uid, "first_name": name or f"U{uid}"}}
    if payload is not None:
        u["payload"] = payload
    bot.handle_update(u)


def texts(chat, n0):
    return [m["body"].get("text") or "" for m in srv.messages[n0:] if m["chat_id"] == chat]


def buy(uid, how="ЮKassa", price=490):
    key = bot.tenant_key(uid)
    plan = bot.tariffs()[-1]["name"]
    bot.host_apply_paid(key, plan, 30, price, how)


msg(HOST, HC, "/start")


@check
def b1():
    """ID партнёра: /partner 123456 — только хозяин; /partner — показывает ID и ссылку; переменная PARTNER_ID — если
    в настройках пусто; без ID партнёра /ref объясняет, как его задать"""
    n0 = len(srv.messages)
    msg(HOST, HC, "/ref")
    assert any("/partner" in t for t in texts(HC, n0)), texts(HC, n0)
    os.environ["PARTNER_ID"] = "777"
    assert bot.partner_id() == "777", "переменная PARTNER_ID"
    msg(HOST, HC, "/partner 123456")
    assert bot.partner_id() == "123456" and tio.rjson("settings.json")["partner_id"] == "123456"
    os.environ.pop("PARTNER_ID", None)
    n1 = len(srv.messages)
    msg(HOST, HC, "/partner")
    shown = "\n".join(texts(HC, n1))
    assert "123456" in shown and LINK in shown, shown
    n2 = len(srv.messages)
    msg(HOST, HC, "/partner 12 34 <b>")
    assert bot.partner_id() == "123456" and texts(HC, n2), "кривой ID не принимаем"
    n3 = len(srv.messages)
    msg(5001, 62000, "/start")
    msg(5001, 62000, "/partner 666")
    msg(5001, 62000, "/ref")
    assert bot.partner_id() == "123456" and bot.PENDING.get(5001) != "reflink", "покупателю — нельзя"
    assert not any(LINK in t for t in texts(62000, n3))


@check
def b2():
    """🔗 /ref — бот ждёт пересланное сообщение клиента; переслал — в ответ ID клиента и ссылка
    https://max.ru/testbot?start=ref_123456, клиент закреплён за партнёром; пересланный пост канала или «привет» —
    подсказка, бот ждёт дальше"""
    n0 = len(srv.messages)
    msg(HOST, HC, "/ref")
    assert bot.PENDING.get(HOST) == "reflink" and any("Сгенерировать ссылку для клиента" in t for t in texts(HC, n0))
    n1 = len(srv.messages)
    msg(HOST, HC, "", {"type": "forward", "chat_id": -7100, "message": {"text": "пост канала"}})
    msg(HOST, HC, "привет")
    assert bot.PENDING.get(HOST) == "reflink" and len(texts(HC, n1)) == 2 and not any(LINK in t for t in texts(HC, n1))
    n2 = len(srv.messages)
    msg(HOST, HC, "", {"type": "forward", "chat_id": 555, "sender": {"user_id": 7777, "name": "Иван"},
                       "message": {"text": "Здравствуйте, хочу бота"}})
    got = texts(HC, n2)
    assert len(got) == 1 and LINK in got[0] and "7777" in got[0] and "Иван" in got[0], got
    assert bot.PENDING.get(HOST) is None and bot.REFS.partner_of([7777]) == "123456"
    n3 = len(srv.messages)
    msg(HOST, HC, "/ref")
    msg(HOST, HC, "8888")                                   # ID клиента цифрами
    got = texts(HC, n3)[-1]
    assert LINK in got and "8888" in got and bot.REFS.partner_of([8888]) == "123456", got


@check
def b3():
    """клиент пришёл по ссылке (?start=ref_123456) — закреплён за партнёром; купил подписку — хозяину
    «💰 Клиент Иван купил подписку по ссылке партнера 123456!» (и тариф, сумма, ID — для выплаты вручную)"""
    started(9001, 69001, "ref_123456", name="Иван")
    assert bot.REFS.partner_of([9001]) == "123456"
    n0 = len(srv.messages)
    buy(9001)
    got = [t for t in texts(HC, n0) if t.startswith("💰")]
    assert len(got) == 1 and got[0].split("\n")[0] == MONEY and "490" in got[0] and "9001" in got[0], got
    assert bot.REFS.stats("123456")["sales"] >= 1 and bot.REFS.stats("123456")["sum"] >= 490
    n1 = len(srv.messages)
    buy(9001, "перевод", 990)                               # продление — снова сообщение
    assert sum(t.startswith("💰") for t in texts(HC, n1)) == 1


@check
def b4():
    """не по ссылке — сообщений нет; бесплатно (промокод 100%) — не покупка; второй партнёр не перебивает первого;
    уже покупавший — не «новый клиент»; «/start ref_…» текстом — тоже засчитывается"""
    started(9002, 69002, name="Пётр")
    n0 = len(srv.messages)
    buy(9002)
    assert not any(t.startswith("💰") for t in texts(HC, n0)), "не по ссылке — молчим"
    started(9003, 69003, "ref_123456", name="Анна")
    n1 = len(srv.messages)
    buy(9003, "промокод 100%", 0)
    assert not any(t.startswith("💰") for t in texts(HC, n1)), "бесплатно — не покупка"
    started(9001, 69001, "ref_555", name="Иван")
    assert bot.REFS.partner_of([9001]) == "123456", "первый партнёр остаётся"
    started(9002, 69002, "ref_123456", name="Пётр")
    assert bot.REFS.partner_of([9002]) is None, "уже покупал до ссылки — не новый клиент партнёра"
    msg(9004, 69004, "/start ref_123456", name="Олег")
    assert bot.REFS.partner_of([9004]) == "123456", "«/start ref_…» текстом"
    started(9005, 69005, "ref_<bad>", name="Зло")
    assert bot.REFS.partner_of([9005]) is None
    bot.legal_forget(bot.tenant_key(9004))                  # данные клиента удаляют — и отметку партнёра
    assert bot.REFS.partner_of([9004]) is None
    assert "партн" in bot.LEGAL.privacy_text({}), "в «Какие данные хранит бот» — честно про отметку партнёра"


if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    os._exit(1)
print("ВСЕ ТЕСТЫ ПАРТНЁРСКИХ ССЫЛОК ПРОШЛИ")
os._exit(0)
