import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
"""Продажа: продавец заводит клиента по токену его бота, клиент привязывается кодом, лимиты, выкл/вкл, удаление."""
import json, os, re, shutil, subprocess, sys, tempfile, textwrap, threading, time, types, importlib.util
sys.path.insert(0, ROOT)
from fake_max import FakeMax

pk = tempfile.mkdtemp()
os.makedirs(f"{pk}/telethon")
open(f"{pk}/telethon/__init__.py", "w").write(textwrap.dedent('''
    import types
    from . import errors
    events = types.SimpleNamespace(NewMessage=lambda *a, **k: None)
    class TelegramClient:
        def __init__(self, *a, **k): pass
'''))
open(f"{pk}/telethon/errors.py", "w").write("class SessionPasswordNeededError(Exception): pass\n")

srv = FakeMax()
srv.add_bot("CLIENTTOKEN1", "КотоБот")
srv.add_bot("CLIENTTOKEN2", "ПесБот")
w = tempfile.mkdtemp()
shutil.copy(ROOT + "/tg_to_max.py", f"{w}/main.py")
shutil.copytree(ROOT + "/reloader", os.path.join(os.path.dirname(f"{w}/main.py"), "reloader"), dirs_exist_ok=True)   # бот = файл + пакет reloader
env = dict(os.environ, PYTHONPATH=pk, PYTHONIOENCODING="utf-8", BOT_TOKEN="TESTTOKEN",
           MAX_CHANNEL_LINK="https://max.ru/mychannel", MAX_API_URL=srv.url, ADMIN_CODE="sellercode1",
           YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="test_secret", YK_API_URL=srv.url,
           SELLER_CONTACT="@seller_test", OWNER_ID="1",
           HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9")
env.pop("PYTHONUNBUFFERED", None)
out = []
p = subprocess.Popen([sys.executable, "main.py"], cwd=w, env=env, stdin=subprocess.DEVNULL,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
threading.Thread(target=lambda: [out.append(l) for l in p.stdout], daemon=True).start()


def answers_since(n):
    return [a["body"]["message"]["text"] for a in srv.answers[n:]]


def last_rows(n):
    rows = [a["body"]["message"].get("attachments") or [] for a in srv.answers[n:]]
    return json.dumps(rows, ensure_ascii=False)


def press(payload):
    n = len(srv.answers)
    srv.press(1, 100, payload)
    srv.wait(lambda: len(srv.answers) > n, what=f"ответ на {payload}")
    return answers_since(n)[-1], last_rows(n)


def client_texts(chat):
    return [m["body"].get("text", "") for m in srv.messages if m["chat_id"] == chat and m.get("token") == "CLIENTTOKEN1"]


def client_press(payload, uid=500, chat=5000):
    n = len(srv.answers)
    srv.push_to("CLIENTTOKEN1", {"update_type": "message_callback", "callback": {
        "callback_id": f"cc-{time.time()}", "payload": payload, "user": {"user_id": uid, "first_name": "Клиент"}},
        "message": {"recipient": {"chat_id": chat, "chat_type": "dialog"}, "body": {"mid": "x"}}})
    srv.wait(lambda: len(srv.answers) > n, what=f"ответ бота клиента на {payload}")
    return answers_since(n)[-1], last_rows(n)


try:
    # продавец становится владельцем своего бота
    srv.wait(lambda: any("отправь ему этот код" in l for l in out), what="бот продавца запустился")
    srv.user_message(1, 100, "sellercode1")
    srv.wait(lambda: any("владелец" in t for t in srv.texts(100)), what="продавец — владелец")
    srv.wait(lambda: "m:cl" in json.dumps([m["body"].get("attachments") for m in srv.to_chat(100)]),
             what="у продавца в меню кнопка «Клиенты»")

    # 1) новый клиент по токену
    text, rows = press("m:cl")
    assert "Клиенты" in text and "p:clnew" in rows and "300" in text, text
    press("p:clnew")
    n0 = len(srv.messages)
    srv.user_message(1, 100, "CLIENTTOKEN1")
    srv.wait(lambda: "a:clsub:" in json.dumps([m["body"].get("attachments") for m in srv.messages[n0:]]), what="токен принят")
    plan = json.dumps([m["body"].get("attachments") for m in srv.messages[n0:]], ensure_ascii=False)
    mm = re.search(r"a:clsub:(c\d+):0:30", plan)
    assert mm, (plan[:1500], [m["body"].get("text") for m in srv.messages[n0:]])
    cid = mm.group(1)
    assert "a:clsub:%s:0:3" % cid in plan and "750" in plan and "Стандарт" in plan
    text, rows = press(f"a:clsub:{cid}:1:30")            # «Стандарт»: 3 темы, 10 каналов
    assert "Тариф: «Стандарт»" in text, text
    m = re.search(r"Код привязки для клиента: (\d{6})", text)
    assert m and "работает" in text, text
    print("s1 ok: продавец завёл клиента по токену, включил на 30 дней, получил код привязки")

    # 2) клиент привязывается своим кодом к СВОЕМУ боту
    srv.client_message("CLIENTTOKEN1", 500, 5000, m.group(1))
    srv.wait(lambda: any("владелец" in t for t in client_texts(5000)), what="клиент — владелец своего бота")
    srv.wait(lambda: any("одписка до" in t for t in client_texts(5000)), what="меню клиента")
    cl_menu = json.dumps([m["body"] for m in srv.messages if m.get("token") == "CLIENTTOKEN1"], ensure_ascii=False)
    assert "одписка до" in cl_menu and "@seller_test" in cl_menu, cl_menu[:500]
    assert "m:cl" not in cl_menu, "у клиента лишние кнопки"
    assert "Как пользоваться" in cl_menu and "3 шага" in cl_menu, "после привязки нет инструкции"
    assert "m:gd" in cl_menu and "m:more" in cl_menu and "Осталось настроить" in cl_menu, cl_menu[:800]
    text, rows = client_press("m:more")
    assert "tg:menu" in rows, "у клиента нет кнопки «Telegram» (по умолчанию включена)"
    n0 = len(srv.messages)
    srv.push_to("CLIENTTOKEN1", {"update_type": "bot_started", "chat_id": 5000, "user": {"user_id": 500, "first_name": "Клиент"}})
    srv.wait(lambda: sum(m.get("token") == "CLIENTTOKEN1" for m in srv.messages[n0:]) >= 2, what="«Старт» у клиента")
    got = [m["body"].get("text", "") for m in srv.messages[n0:] if m.get("token") == "CLIENTTOKEN1"]
    assert "Как пользоваться" in got[0] and "Меню бота" in got[1], got
    n0 = len(srv.messages)
    srv.push_to("CLIENTTOKEN1", {"update_type": "bot_started", "chat_id": 5009, "user": {"user_id": 509, "first_name": "Чужой"}})
    srv.wait(lambda: any(m.get("token") == "CLIENTTOKEN1" for m in srv.messages[n0:]), what="ответ чужому")
    got = [m["body"].get("text", "") for m in srv.messages[n0:] if m.get("token") == "CLIENTTOKEN1"]
    assert "только его владелец" in got[0], got
    assert os.path.isdir(os.path.join(w, "data", "clients", cid))
    print("s2 ok: клиент стал владельцем; инструкция при привязке и «Старт»; в меню срок, контакт, «Ещё» с «Telegram»; нет «Клиенты»")

    # 3) лимиты: по умолчанию 3 темы, 10 источников
    for i in range(2):
        client_press("p:themenew")
        srv.client_message("CLIENTTOKEN1", 500, 5000, f"Тема {i + 2}")
        srv.wait(lambda: any(f"Создал тему «Тема {i + 2}»" in t for t in client_texts(5000)), what="тема создана")
    client_press("p:themenew")
    srv.client_message("CLIENTTOKEN1", 500, 5000, "Лишняя")
    srv.wait(lambda: any("до 3 тем" in t for t in client_texts(5000)), what="лимит тем")
    text, rows = client_press("p:chadd")                # сначала — подтверждение прав на контент
    assert "Подтверди права на контент" in text and "rk:ok" in rows and "m:terms" in rows, (text, rows)
    text, rows = client_press("m:terms")
    assert "Условия использования" in text and "1253.1" in text and "@seller_test" in text, text
    text, rows = client_press("rk:ok")
    assert "сохранено" in text and "Пришли каналы" in text, text
    cons = json.load(open(os.path.join(w, "data", "clients", cid, "consent.json")))
    assert cons["500"]["ver"] == 1 and cons["500"]["date"], cons
    srv.client_message("CLIENTTOKEN1", 500, 5000, " ".join(f"chan{i:02d}" for i in range(12)))
    srv.wait(lambda: any("до 10 каналов" in t for t in client_texts(5000)), what="лимит источников")
    text, rows = client_press("p:chadd")                # второй раз подтверждать не нужно
    assert "Пришли каналы" in text, text
    print("s3 ok: лимиты; перед каналами-источниками — подтверждение прав (сохраняется), условия доступны")

    # 4) продавец видит клиента; выключает — бот отвечает «напиши продавцу» и не публикует; включает — работает
    text, rows = press(f"m:clc:{cid}")
    assert "Темы: 3 из 3" in text and "Админов: 1" in text and "Права на контент подтвердил" in text, text
    text, rows = press(f"a:cloff:{cid}")
    assert "выключен" in text, text
    time.sleep(4)
    srv.client_message("CLIENTTOKEN1", 501, 5001, "привет")
    srv.wait(lambda: any("приостановлен" in t and "@seller_test" in t for t in client_texts(5001)), what="бот выключен")
    text, rows = press(f"a:clon:{cid}")
    assert "работает" in text, text
    time.sleep(4)
    srv.client_message("CLIENTTOKEN1", 500, 5000, "/menu")
    srv.wait(lambda: sum("Меню бота" in t for t in client_texts(5000)) >= 2, what="бот снова работает")
    st = json.load(open(os.path.join(w, "data", "clients", cid, "themes.json")))
    assert len(st["themes"]) == 3, "настройки клиента пропали после выключения"
    print("s4 ok: продавец выключил — бот клиента только «напиши +79017710012»; включил — всё вернулось, настройки целы")

    # 4б) продавец выключает клиенту «Telegram» — кнопка пропадает, включает — возвращается
    text, rows = press(f"m:clc:{cid}")
    assert f"a:cltg:{cid}" in rows and "Telegram: вкл" in rows, rows
    text, rows = press(f"a:cltg:{cid}")
    assert "Telegram: выкл" in rows, rows
    time.sleep(4)
    text, rows = client_press("m:more")
    assert "tg:menu" not in rows and "Telegram" not in text, (text, rows)
    text, rows = client_press("tg:menu")
    assert "Меню бота" in text and "tg:menu" not in rows, text
    text, rows = press(f"a:cltg:{cid}")
    assert "Telegram: вкл" in rows, rows
    time.sleep(4)
    text, rows = client_press("m:more")
    assert "tg:menu" in rows, "кнопка Telegram не вернулась"
    print("s4б ok: продавец включает/выключает клиенту кнопку «Telegram»")

    # 8) оплата из бота клиента: реквизиты продавца -> «Я оплатил» -> заявка продавцу -> «продлить» -> новый тариф
    press("m:cltar")
    press("p:clpayset")
    srv.user_message(1, 100, "Сбер по номеру +79990000000, Артём Ж.")
    srv.wait(lambda: any("Реквизиты сохранены" in t for t in srv.texts(100)), what="реквизиты сохранены")
    press("p:cltarset")
    srv.user_message(1, 100, "Старт 1 5 300\nСтандарт 3 10 600\nПро 10 30 1500\nБизнес 20 50 3000")
    srv.wait(lambda: any("Тарифы сохранены" in t for t in srv.texts(100)), what="тарифы сохранены")
    text, rows = client_press("m:pay")
    assert "Тариф и оплата" in text and "Сейчас: «Стандарт»" in text and "Бизнес" in text and "py:3:90" in rows, text
    text, rows = client_press("py:2:90")
    assert "«Про» на 90 дней — 3750 ₽" in text and "Сбер по номеру" in text and "pd:2:90" in rows, text
    until0 = json.load(open(os.path.join(w, "data", "clients.json")))[cid]["until"]
    text, rows = client_press("pd:2:90")
    assert "Заявка отправлена" in text, text
    srv.wait(lambda: f"a:clpayok:{cid}" in json.dumps([m["body"] for m in srv.to_chat(100)]),
             what="заявка на оплату у продавца", timeout=40)
    req = next(m["body"]["text"] for m in reversed(srv.to_chat(100)) if "Заявка на оплату" in (m["body"].get("text") or ""))
    assert "«Про» на 90 дней — 3750 ₽" in req, req
    text, rows = press(f"a:clpayok:{cid}")
    assert "Тариф: «Про»" in text, text
    srv.wait(lambda: any("Оплата получена" in t and "«Про»" in t for t in client_texts(5000)), what="клиенту: оплата получена")
    c = json.load(open(os.path.join(w, "data", "clients.json")))[cid]
    assert c["plan"] == "Про" and c["lim_themes"] == 10 and abs(c["until"] - until0 - 90 * 86400) < 120, c
    client_press("p:themenew")                          # лимит вырос сразу, без перезапуска
    srv.client_message("CLIENTTOKEN1", 500, 5000, "Четвёртая")
    srv.wait(lambda: any("Создал тему «Четвёртая»" in t for t in client_texts(5000)), what="4-я тема на «Про»")
    # 9) онлайн-оплата ЮKassa: ссылка клиенту, оплата прошла — бот продлился сам, без нажатий продавца
    text, rows = client_press("py:1:30")
    assert "Оплатить онлайн" in text and "pk:1:30" in rows, text
    until1 = json.load(open(os.path.join(w, "data", "clients.json")))[cid]["until"]
    text, rows = client_press("pk:1:30")
    assert "Готовлю ссылку" in text, text
    def pay_link():
        for m_ in srv.messages:
            if m_.get("token") == "CLIENTTOKEN1":
                for a_ in m_["body"].get("attachments") or []:
                    for row in (a_.get("payload") or {}).get("buttons") or []:
                        for b_ in row:
                            if b_.get("type") == "link" and "yoomoney" in b_.get("url", ""):
                                return b_["url"]
    srv.wait(pay_link, what="ссылка на оплату у клиента", timeout=40)
    pid = pay_link().rsplit("/", 1)[1]
    assert srv.payments[pid]["amount"] == {"value": "600.00", "currency": "RUB"}, srv.payments[pid]
    srv.payments[pid]["status"] = "succeeded"
    srv.wait(lambda: any("Оплата прошла (ЮKassa)" in t for t in srv.texts(100)), what="продавцу: оплата прошла", timeout=60)
    srv.wait(lambda: sum("Оплата получена" in t for t in client_texts(5000)) >= 2, what="клиенту: оплата получена")
    c = json.load(open(os.path.join(w, "data", "clients.json")))[cid]
    assert c["plan"] == "Стандарт" and abs(c["until"] - until1 - 30 * 86400) < 120, c
    assert not os.path.exists(os.path.join(w, "data", "clients", cid, "pay_request.json"))
    print("s9 ok: ЮKassa — клиенту ссылка на оплату, после оплаты бот продлился и сменил тариф сам")
    print("s8 ok: клиент выбрал тариф и нажал «Я оплатил» — продавцу заявка, одна кнопка продлевает и меняет тариф")

    # 5) журнал клиента и удаление
    text, rows = press(f"m:cllog:{cid}")
    assert "Журнал" in text
    press(f"a:cldelok:{cid}")
    srv.wait(lambda: not os.path.isdir(os.path.join(w, "data", "clients", cid)), what="клиент удалён")
    assert json.load(open(os.path.join(w, "data", "clients.json"))) == {}
    arch = os.listdir(os.path.join(w, "data", "consent_archive"))
    assert len(arch) == 1 and arch[0].startswith(cid) and "500" in json.load(open(os.path.join(w, "data", "consent_archive", arch[0])))
    print("s5 ok: журнал клиента виден продавцу; удаление останавливает бота и стирает его данные")

    # 7) основной бот перезапустили (хостинг гасит только его) — бот клиента не остаётся старой копией
    press("p:clnew")
    n0 = len(srv.messages)
    srv.user_message(1, 100, "CLIENTTOKEN2")
    srv.wait(lambda: "a:clsub:" in json.dumps([m["body"].get("attachments") for m in srv.messages[n0:]]), what="токен 2 принят")
    cid2 = re.search(r"a:clsub:(c\d+):0:30", json.dumps([m["body"].get("attachments") for m in srv.messages[n0:]])).group(1)
    press(f"a:clsub:{cid2}:0:30")
    pidf = os.path.join(w, "data", "clients", cid2, "bot.pid")
    srv.wait(lambda: os.path.exists(pidf), what="pid бота клиента")
    old = int(open(pidf).read())
    time.sleep(3)
    p.kill()                                          # SIGKILL: основной бот не успевает ничего сделать сам
    srv.wait(lambda: not os.path.exists(f"/proc/{old}") or open(f"/proc/{old}/stat").read().split()[2] == "Z",
             what="бот клиента вышел вслед за основным", timeout=15)
    p = subprocess.Popen([sys.executable, "main.py"], cwd=w, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    threading.Thread(target=lambda: [out.append(l) for l in p.stdout], daemon=True).start()
    srv.wait(lambda: os.path.exists(pidf) and int(open(pidf).read()) != old, what="бот клиента запущен заново", timeout=60)
    print("s7 ok: основной бот перезапустили — старая копия бота клиента вышла, запущена новая")
finally:
    p.kill()

# 6) сроки (без ожидания реальных дней): напоминания за 3 дня, за 1 день и в день окончания
tele = types.ModuleType("telethon"); tele.TelegramClient = object; sys.modules["telethon"] = tele
os.chdir(tempfile.mkdtemp())
os.environ.pop("TGMAX_CLIENT", None)
os.environ["SELLER_CONTACT"] = "@seller_test"
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
SENT, SELLER = [], []
bot.client_send = lambda cid, text: SENT.append(text) or 1
bot.seller_notify = lambda text, rows=None: SELLER.append(text)
now = time.time()
bot.CLIENTS["c1"] = {"name": "X", "token": "t", "until": now + 2.5 * 86400, "enabled": True, "warned": []}
bot.check_client_terms("c1", now); bot.check_client_terms("c1", now)
assert len(SENT) == 1 and "через 3 дня" in SENT[0] and "300" in SENT[0] and "@seller_test" in SENT[0]
bot.check_client_terms("c1", now + 1.6 * 86400)
assert len(SENT) == 2 and "завтра" in SENT[1]
assert bot.client_mode(bot.CLIENTS["c1"], now + 3 * 86400) == "expired"
bot.check_client_terms("c1", now + 3 * 86400)
assert len(SENT) == 3 and "закончилась" in SENT[2] and SELLER and "закончилась" in SELLER[0]
bot.check_client_terms("c1", now + 4 * 86400); assert len(SENT) == 3
bot.CLIENTS["c1"]["enabled"] = False
assert bot.client_mode(bot.CLIENTS["c1"]) == "off" and "приостановлен" in bot.lock_text("off")
assert "750" in bot.lock_text("expired")
bot.LOCKED = "expired"; bot.GCFG["admins"] = [77]
t, rows = bot.locked_answer({"payload": "x", "user": {"user_id": 77}})
assert "закончилась" in t and "m:pay" in json.dumps(rows)
t, rows = bot.locked_answer({"payload": "m:pay", "user": {"user_id": 77}})
assert "Тарифы" in t and "py:0:30" in json.dumps(rows), t
t, rows = bot.locked_answer({"payload": "pd:1:30", "user": {"user_id": 77}})
assert "Заявка отправлена" in t and json.load(open("pay_request.json"))["plan"] == "Стандарт"
t, rows = bot.locked_answer({"payload": "pd:1:30", "user": {"user_id": 5}})
assert "закончилась" in t                                   # чужой не может слать заявки
bot.LOCKED = "off"
t, rows = bot.locked_answer({"payload": "m:pay", "user": {"user_id": 77}})
assert "приостановлен" in t and rows == []
SELLER.clear()
bot.CLIENTS["cx"] = {"name": "Падучий"}
for _ in range(3):
    bot.note_client_crash("cx", bot.CLIENTS["cx"])
bot.note_client_crash("cx", bot.CLIENTS["cx"])
assert len(SELLER) == 1 and "Падучий" in SELLER[0] and "падает" in SELLER[0], SELLER
print("s6в ok: бот клиента падает раз за разом — продавцу одно сообщение с ошибкой")
print("s6б ok: подписка кончилась — оплатить можно прямо в боте клиента; выключенный продавцом — только «напиши»")
print("s6 ok: напоминания клиенту за 3 дня и за 1 день, в день окончания — клиенту и продавцу; по одному разу")
print("ВСЕ ТЕСТЫ ПРОДАЖИ ПРОШЛИ")
os._exit(0)
