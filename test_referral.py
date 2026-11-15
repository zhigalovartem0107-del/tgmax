"""🤝 Партнёрская ссылка (без меню для партнёров): хозяин задаёт ID партнёра (/partner 123456 или переменная
PARTNER_ID), по /ref («🔗 Сгенерировать ссылку для клиента») пересылает сообщение нового клиента (или его ID в MAX) —
бот берёт ID клиента и отвечает ссылкой https://max.ru/<бот>?start=ref_<ID партнёра>, закрепляя клиента за партнёром.
Кто пришёл по ссылке (?start=ref_…), закреплён за партнёром; купил подписку — хозяину «💰 Клиент [Имя] купил
подписку по ссылке партнера [ID]!». Выплаты партнёру — вручную.
🔗 Переходы: каждый запуск бота по ссылке (bot_started с ref_<ID>) — сразу +1 партнёру, до всех проверок, даже без
покупки; /partner и панель хозяина показывают воронку: переходы → зашли в бота → купили → долг партнёру
(процент партнёра с покупок минус выплаты). Всё — в referrals.json (надёжная запись с копией), переживает перезапуск."""
import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import importlib.util
import json
import os
import subprocess
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


@check
def r3():
    """🔗 переходы по ссылке: каждый переход — сразу +1 партнёру (без покупки и без закрепления) и сразу в файл;
    тот же переход двумя событиями за секунды (MAX прислал и «бот запущен», и «/start ref_…» текстом) — один;
    человек нажал ссылку ещё раз — ещё переход; ID человека неизвестен — каждый раз; кривой ID партнёра — не
    переход; переживает перезапуск; удаление данных клиента счётчик не трогает (в нём нет личных данных); мусор в
    файле — не падаем; выдуманных ID партнёров в счётчике не больше предела, настоящие не вытесняются"""
    assert R is not None
    d = tempfile.mkdtemp()
    path = os.path.join(d, "referrals.json")
    save = lambda p, data, **k: tio.wjson(data, p, "w", _dump={"ensure_ascii": False})
    load = lambda p, default: tio.rjson(p) if os.path.exists(p) else default
    now = [1000.0]
    mk = lambda: R.Referrals(path, save=save, load=load, clock=lambda: now[0])
    st = mk()
    assert st.summary("123456")["clicks"] == 0
    assert st.click("123456", 7001, "start") is True
    assert tio.rjson(path)["clicks"] == {"123456": 1}, "сразу в файле"
    now[0] += 3
    assert st.click("123456", "7001", "text") is False, "тот же переход пришёл вторым событием (и id строкой)"
    assert st.click("123456", 7001, "start") is True, "нажал ссылку ещё раз — ещё переход"
    assert st.click("123456", 7002, "text") is True and st.click("555", 7001, "text") is True, "другой человек / партнёр"
    now[0] += 60
    assert st.click("123456", 7001, "text") is True, "через минуту — уже новый переход"
    assert st.click("123456") is True and st.click("123456") is True, "ID человека неизвестен — каждый раз"
    for bad in ("", None, "<bad>", "12 34", "9" * 40, 123456):
        assert st.click(bad, 7009, "start") is False, bad
    s = st.summary("123456")
    assert (s["clicks"], s["clients"], s["sales"], s["sum"]) == (6, 0, 0, 0), s
    assert st.summary("555")["clicks"] == 1 and set(tio.rjson(path)["clicks"]) == {"123456", "555"}
    assert mk().summary("123456")["clicks"] == 6 and mk().summary("555")["clicks"] == 1, "перезапуск"
    st.attach(7001, "123456", "ссылка", "Иван")
    st.forget([7001])
    assert mk().summary("123456")["clicks"] == 6, "удаление данных клиента не трогает счётчик переходов"
    for junk in ('{"clicks": [1, 2]}', '{"clicks": "x", "rates": [], "paid": 5, "payouts": {}, "carry": 7, "sales": 1}',
                 '{"clicks": {"123456": "x", "<bad>": 5, "77": -3, "78": true, "79": 4.5, "80": 4}}',
                 '{"sales": [{"partner": "123456", "price": "x"}, {"partner": "123456", "price": 10, "pct": "y"}]}'):
        tio.wfile(junk, path, "w")
        st2 = mk()
        s2 = st2.summary("123456")
        assert s2["clicks"] == 0 and st2.summary("80")["clicks"] == (4 if '"80"' in junk else 0), (junk, s2)
        assert isinstance(s2["sum"], int) and (s2["earned"] is None or isinstance(s2["earned"], int)), (junk, s2)
        assert st2.click("123456", 1, "start") and st2.summary("123456")["clicks"] == 1, junk
    tio.wfile('{"paid": {"123456": 5}, "payouts": [{"partner": "123456", "sum": 5, "t": "x"}, '
              '{"partner": "123456", "sum": 5, "t": NaN}, {"partner": "123456", "sum": "5", "t": 1}]}', path, "w")
    st2 = mk()
    assert st2.summary("123456")["paid"] == 5 and st2.last_payout("123456") is None, "история выплат — без мусора"
    old = R.KEEP_PARTNERS
    R.KEEP_PARTNERS = 5
    try:
        tio.wfile("{}", path, "w")
        st3 = mk()
        st3.attach(1, "real", "ссылка")
        st3.click("real", 1, "start")
        for i in range(30):
            st3.click(f"fake{i}", 100 + i, "start")
        assert len(st3.clicks) <= 5 and st3.summary("real")["clicks"] == 1, st3.clicks
        assert len(tio.rjson(path)["clicks"]) <= 5
        tio.wjson({"clients": {"1": {"partner": "real"}}, "clicks": {"real": 1, **{f"f{i}": 9 for i in range(9)}}},
                  path, "w")                                # в файле сверх предела — настоящий партнёр остаётся
        st4 = mk()
        assert len(st4.clicks) == 5 and st4.summary("real")["clicks"] == 1, st4.clicks
    finally:
        R.KEEP_PARTNERS = old


@check
def r4():
    """💵 долг партнёру = процент партнёра с каждой его покупки − выплаты. Процента нет — долг неизвестен (None, а
    не 0); процент задали — покупки без процента считаются по нему; новый процент — только для новых покупок;
    выплата уменьшает долг (минус — исправить ошибку), переплата — долг меньше нуля; кривые проценты и суммы — не
    принимаем; всё переживает перезапуск и не теряется, когда старые покупки уходят из файла"""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "referrals.json")
    save = lambda p, data, **k: tio.wjson(data, p, "w", _dump={"ensure_ascii": False})
    load = lambda p, default: tio.rjson(p) if os.path.exists(p) else default
    mk = lambda: R.Referrals(path, save=save, load=load)
    st = mk()
    st.sale("123456", "7777", "Иван", "Про", 30, 490, "ЮKassa")
    s = st.summary("123456")
    assert (s["sales"], s["sum"], s["rate"], s["earned"], s["owed"], s["paid"]) == (1, 490, None, None, None, 0), s
    assert st.summary("123456", rate=30)["owed"] == 147, "процент из переменной хостинга (его передаёт бот)"
    assert (st.summary("999")["earned"], st.summary("999")["owed"]) == (0, 0), "нет покупок — долга нет"
    assert st.set_rate("123456", 30) is True
    st.sale("123456", "7777", "Иван", "Про", 30, 490, "перевод")
    assert st.summary("123456")["earned"] == 294
    assert st.set_rate("123456", 50) is True
    assert st.summary("123456")["earned"] == 294, "новый процент старые покупки не пересчитывает"
    st.sale("123456", "8888", "Оля", "Про", 30, 1000, "ЮKassa")
    st.sale("123456", "8888", "Оля", "Про", 30, 99, "ЮKassa", rate=33)
    s = st.summary("123456")
    assert (s["sales"], s["sum"], s["earned"], s["rate"], s["owed"]) == (4, 2079, 827, 50, 827), s
    assert st.payout("123456", 500) is True and st.summary("123456")["owed"] == 327
    assert st.payout("123456", -100) is True and st.summary("123456")["owed"] == 427, "минус — исправить ошибку"
    for bad in (0, "5", None, 1.5, True, 10 ** 10):
        assert st.payout("123456", bad) is False, bad
    assert st.payout("<bad>", 5) is False
    st.payout("123456", 1000)
    s = st.summary("123456")
    assert (s["paid"], s["owed"]) == (1400, -573), s
    assert st.last_payout("123456")["sum"] == 1000 and st.last_payout("999") is None
    for bad in (-1, 101, "x", None, float("nan"), True):
        assert st.set_rate("123456", bad) is False, bad
    assert st.set_rate("<bad>", 10) is False and st.summary("123456")["rate"] == 50
    assert mk().summary("123456") == s, ("перезапуск", mk().summary("123456"), s)
    assert R.parse_pct("30") == 30 and R.parse_pct("12,5%") == 12.5 and R.parse_pct(" 7.5 ") == 7.5
    for bad in ("", None, "-1", "101", "abc", "nan", "1e3"):
        assert R.parse_pct(bad) is None, bad
    old = R.KEEP_SALES
    R.KEEP_SALES = 2
    try:
        st3 = mk()
        before = st3.summary("123456")
        st3.sale("777", "1", "A", "Про", 30, 100, "ЮKassa")    # покупки другого партнёра вытесняют старые из файла
        st3.sale("777", "1", "A", "Про", 30, 100, "ЮKassa")
        assert len(st3.sales) == 2
        assert st3.summary("123456") == before, ("итоги партнёра те же", st3.summary("123456"), before)
        assert mk().summary("123456") == before, "и после перезапуска"
        assert st3.stats("123456") == {"clients": 0, "sales": 4, "sum": 2079}
        p2 = os.path.join(tempfile.mkdtemp(), "referrals.json")
        R.KEEP_SALES = 1
        st4 = R.Referrals(p2, save=save, load=load)
        st4.sale("42", "1", "A", "Про", 30, 200, "ЮKassa")     # без процента
        st4.sale("42", "1", "A", "Про", 30, 300, "ЮKassa")     # без процента; первая ушла из файла в итоги
        assert st4.summary("42")["earned"] is None and st4.summary("42", rate=10)["earned"] == 50
        st4.set_rate("42", 20)
        assert st4.summary("42")["earned"] == 100 and R.Referrals(p2, save=save, load=load).summary("42")["earned"] == 100
    finally:
        R.KEEP_SALES = old


@check
def r5():
    """переходы из разных потоков меню одновременно (8 × 50 человек): ни один не потерян — ни в памяти, ни в файле
    (файл пишется строго по очереди: последним ложится самый свежий снимок, а не устаревший)"""
    import threading
    path = os.path.join(tempfile.mkdtemp(), "referrals.json")
    save = lambda p, data, **k: tio.wjson(data, p, "w", _dump={"ensure_ascii": False})
    load = lambda p, default: tio.rjson(p) if os.path.exists(p) else default
    st = R.Referrals(path, save=save, load=load)

    def lane(i):
        for j in range(50):
            st.click("123456", i * 1000 + j, "start")

    ths = [threading.Thread(target=lane, args=(i,)) for i in range(8)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(60)
    assert st.summary("123456")["clicks"] == 400 and tio.rjson(path)["clicks"]["123456"] == 400
    assert R.Referrals(path, save=save, load=load).summary("123456")["clicks"] == 400
    # без гонки на удачу: первая запись «зависла» на медленном диске, тем временем пришёл второй переход —
    # в файле должен оказаться свежий снимок (2), а не тот, что «завис» (1)
    path2 = os.path.join(tempfile.mkdtemp(), "referrals.json")
    entered, release, seen = threading.Event(), threading.Event(), []

    def slow_save(p, data, **k):
        seen.append(data["clicks"].get("123456"))
        if len(seen) == 1:
            entered.set()
            release.wait(0.5)
        tio.wjson(data, p, "w")
        if len(seen) == 2:
            release.set()

    st2 = R.Referrals(path2, save=slow_save, load=load)
    t1 = threading.Thread(target=st2.click, args=("123456", 1, "start"))
    t1.start()
    assert entered.wait(5)
    t2 = threading.Thread(target=st2.click, args=("123456", 2, "start"))
    t2.start()
    t1.join(5)
    t2.join(5)
    assert tio.rjson(path2)["clicks"]["123456"] == 2, ("в файле устаревший снимок", seen)


# ---------- 2) бот в host-режиме: хозяин делает ссылку, клиент приходит по ней и покупает ----------
tele = types.ModuleType("telethon")
tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele
sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, BOT_TOKEN="TESTTOKEN", TGMAX_HOST="1", OWNER_ID="1")
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ADMIN_IDS", "PARTNER_ID",
          "PARTNER_PERCENT"):
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



def clicks(pid="123456"):
    return bot.REFS.summary(pid)["clicks"]


@check
def b5():
    """клиент нажал ссылку (bot_started с payload ref_123456) — переход засчитан сразу и сразу в файле, хотя он
    ничего не купил; тот же переход пришёл ещё и «/start ref_…» текстом — не удваиваем; нажал ещё раз — ещё переход
    (клиент тот же); переход — до всех проверок: хозяин, уже закреплённый и купивший, покупавший до ссылки — всё
    равно переход; «/start ref_…» текстом без «бот запущен» — тоже переход; чужой партнёр — ему; кривая метка,
    обычный /start и /start с другой меткой — не переход"""
    c0, cl0 = clicks(), bot.REFS.summary("123456")["clients"]
    started(9101, 69101, "ref_123456", name="Кликер")
    assert clicks() == c0 + 1, "переход засчитан сразу — без покупки"
    assert tio.rjson("referrals.json")["clicks"]["123456"] == c0 + 1, "сразу в файле"
    assert bot.REFS.partner_of([9101]) == "123456" and bot.REFS.summary("123456")["clients"] == cl0 + 1
    assert not any(e.get("key") == bot.tenant_key(9101) for e in bot.GCFG.get("pay_log") or ()), "ничего не покупал"
    msg(9101, 69101, "/start ref_123456", name="Кликер")          # MAX прислал тот же переход ещё и текстом
    assert clicks() == c0 + 1, "тот же переход двумя событиями — один"
    started(9101, 69101, "ref_123456", name="Кликер")            # нажал ссылку ещё раз
    assert clicks() == c0 + 2 and bot.REFS.summary("123456")["clients"] == cl0 + 1, "ещё переход, клиент тот же"
    started(HOST, HC, "ref_123456")                               # хозяин проверяет свою ссылку
    started(9001, 69001, "ref_123456", name="Иван")              # уже закреплён и уже купил
    started(9002, 69002, "ref_123456", name="Пётр")              # покупал до ссылки — не закрепляется
    assert clicks() == c0 + 5, clicks() - c0
    assert bot.REFS.partner_of([HOST]) is None and bot.REFS.partner_of([9002]) is None
    msg(9108, 69108, "/start ref_123456", name="Текстом")
    assert clicks() == c0 + 6 and bot.REFS.partner_of([9108]) == "123456", "«/start ref_…» текстом — тоже переход"
    p555 = clicks("555")
    started(9103, 69103, "ref_555", name="Чужой")
    assert clicks("555") == p555 + 1 and clicks() == c0 + 6
    started(9104, 69104, "ref_<bad>")
    started(9105, 69105)
    msg(9106, 69106, "/start")
    msg(9107, 69107, "/start promo_5")
    assert clicks() == c0 + 6 and clicks("555") == p555 + 1 and "<bad>" not in bot.REFS.clicks


@check
def b6():
    """обязательная подписка включена, человек не подписан: ему экран «📢 Подписаться», но переход по ссылке уже
    засчитан и клиент закреплён за партнёром (раньше проверка подписки съедала ?start= — партнёр терял клиента)"""
    subs = list(bot.GCFG.get("force_subs") or [])
    bot.GCFG["force_subs"] = [{"chat_id": -7101, "title": "news_ch", "link": "https://max.ru/news_ch"}]
    srv.members[-7101] = set()
    try:
        c0 = clicks()
        n0 = len(srv.messages)
        started(9102, 69102, "ref_123456", name="Неподписанный")
        got = texts(69102, n0)
        assert any("подпишись" in t for t in got), got
        assert clicks() == c0 + 1 and bot.REFS.partner_of([9102]) == "123456"
    finally:
        bot.GCFG["force_subs"] = subs


def screen_lines(cmd):
    n0 = len(srv.messages)
    msg(HOST, HC, cmd)
    return "\n".join(texts(HC, n0)).split("\n")


@check
def b7():
    """/partner — воронка партнёра четырьмя строками подряд: «🔗 Переходов по ссылке», «👥 Зашло в бота
    (привязалось)», «💰 Купили подписку», «💵 Долг партнеру» — числа из учёта; процента нет — вместо суммы подсказка
    /partner % 30; переменная PARTNER_PERCENT — процент по умолчанию; /partner % 30 — долг 30% покупок; клиент
    купил — долг растёт на 30% от покупки; /partner выплачено N — долг меньше; кривой процент/сумма — объясняем и
    ничего не меняем (и «/partner paid» без суммы не делает партнёром «paid»); та же воронка — в панели хозяина"""
    f = bot.anlimg.fmt_int
    s = bot.REFS.summary("123456")
    assert s["clicks"] >= 8 and s["clients"] >= 5 and s["sales"] >= 2 and s["sum"] > 0, s
    lines = screen_lines("/partner")
    assert f"🔗 Переходов по ссылке: {f(s['clicks'])}" in lines, lines
    assert f"👥 Зашло в бота (привязалось): {f(s['clients'])}" in lines, lines
    assert f"💰 Купили подписку: {f(s['sales'])} (на {f(s['sum'])} ₽)" in lines, lines
    debt = next(x for x in lines if x.startswith("💵 Долг партнеру:"))
    assert "/partner % 30" in debt, debt
    order = [next(i for i, x in enumerate(lines) if x.startswith(p)) for p in ("🔗 Переходов", "👥", "💰", "💵")]
    assert order == list(range(order[0], order[0] + 4)), ("четыре строки подряд, в этом порядке", lines)
    os.environ["PARTNER_PERCENT"] = "20"
    try:
        lines = screen_lines("/partner")
        assert f"💵 Долг партнеру: {f((s['sum'] * 20 + 50) // 100)} ₽" in lines, lines
    finally:
        os.environ.pop("PARTNER_PERCENT", None)
    owed = (s["sum"] * 30 + 50) // 100
    lines = screen_lines("/partner % 30")
    assert f"💵 Долг партнеру: {f(owed)} ₽" in lines and bot.REFS.summary("123456")["rate"] == 30, lines
    buy(9101, "ЮKassa", 1000)                                    # клиент, пришедший по ссылке, купил
    lines = screen_lines("/partner")
    assert f"💰 Купили подписку: {f(s['sales'] + 1)} (на {f(s['sum'] + 1000)} ₽)" in lines, lines
    assert f"💵 Долг партнеру: {f(owed + 300)} ₽" in lines, lines
    lines = screen_lines("/partner выплачено 300")
    assert f"💵 Долг партнеру: {f(owed)} ₽" in lines and bot.REFS.summary("123456")["paid"] == 300, lines
    for bad in ("/partner % 300", "/partner выплачено abc", "/partner paid", "/partner выплачено 0"):
        n0 = len(srv.messages)
        msg(HOST, HC, bad)
        assert texts(HC, n0), bad
        got = bot.REFS.summary("123456")
        assert (bot.partner_id(), got["rate"], got["paid"]) == ("123456", 30, 300), (bad, got, bot.partner_id())
    n0 = len(srv.messages)
    msg(HOST, HC, "/admin")
    panel = "\n".join(texts(HC, n0)).split("\n")
    s = bot.REFS.summary("123456")
    for want in (f"🔗 Переходов по ссылке: {f(s['clicks'])}", f"👥 Зашло в бота (привязалось): {f(s['clients'])}",
                 f"💰 Купили подписку: {f(s['sales'])} (на {f(s['sum'])} ₽)", f"💵 Долг партнеру: {f(s['owed'])} ₽"):
        assert want in panel, (want, panel)


CHILD = r"""
import importlib.util, json, os, sys, types
ROOT, WORK = sys.argv[1], sys.argv[2]
sys.path.insert(0, ROOT)
tele = types.ModuleType("telethon"); tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = tele.errors
os.chdir(WORK)
spec = importlib.util.spec_from_file_location("bot", os.path.join(ROOT, "tg_to_max.py"))
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
print("RESULT " + json.dumps(bot.REFS.summary("123456")))
os._exit(0)
"""


def restarted():
    """Бот перезапущен: новый процесс в той же папке данных — что он знает о партнёре 123456."""
    child = os.path.join(tempfile.mkdtemp(), "child.py")
    tio.wfile(CHILD, child, "w")
    env = dict(os.environ, MAX_API_URL="http://127.0.0.1:9")
    r = subprocess.run([sys.executable, child, ROOT, os.getcwd()], env=env, capture_output=True, text=True,
                       timeout=180, check=False)
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, (r.returncode, r.stdout[-1500:], r.stderr[-1500:])
    return json.loads(line[7:])


@check
def b8():
    """перезапуск бота: переходы, клиенты, покупки, процент и выплаты — из referrals.json (пишется надёжно, с
    копией referrals.json.bak, только для бота); файл испорчен или пропал — из копии"""
    want = bot.REFS.summary("123456")
    assert want["clicks"] > 0 and want["paid"] > 0 and want["rate"] == 30, want
    assert restarted() == want
    assert os.path.exists("referrals.json.bak") and oct(os.stat("referrals.json").st_mode & 0o777) == "0o600"
    tio.wfile('{"clicks": {"123', "referrals.json", "w")
    assert restarted() == want, "испорченный файл — из копии"
    os.remove("referrals.json")
    assert restarted() == want, "пропал — из копии"

if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    os._exit(1)
print("ВСЕ ТЕСТЫ ПАРТНЁРСКИХ ССЫЛОК ПРОШЛИ")
os._exit(0)
