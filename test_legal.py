"""⚖️ Юридический модуль (152-ФЗ, 54-ФЗ, закон «О защите прав потребителей», закон «О рекламе»):
реквизиты и ссылки на документы — только у хозяина (/legal, ADMIN_IDS); у покупателя — документы, реквизиты и что
бот хранит; согласие с офертой и политикой ДО оплаты (без него оплатить нельзя никаким путём); цена в ЮKassa — ровно
та, что на кнопке; запрос на удаление данных — хозяину; отписка от рекламы."""
import importlib.util
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from fake_max import FakeMax  # noqa: E402
from reloader.services import legal as L  # noqa: E402


def make_inn12(prefix10):
    """ИНН физлица/ИП (12 цифр) с правильными контрольными цифрами."""
    d = [int(c) for c in prefix10]
    n11 = sum(a * b for a, b in zip([7, 2, 4, 10, 3, 5, 9, 4, 6, 8], d)) % 11 % 10
    d.append(n11)
    n12 = sum(a * b for a, b in zip([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8], d)) % 11 % 10
    return prefix10 + str(n11) + str(n12)


def make_inn10(prefix9):
    n10 = sum(a * b for a, b in zip([2, 4, 10, 3, 5, 9, 4, 6, 8], [int(c) for c in prefix9])) % 11 % 10
    return prefix9 + str(n10)


def make_ogrn(prefix12):
    return prefix12 + str(int(prefix12) % 11 % 10)


def make_ogrnip(prefix14):
    return prefix14 + str(int(prefix14) % 13 % 10)


INN_IP, INN_OOO = make_inn12("5001007322"), make_inn10("770708389")
OGRNIP, OGRN = make_ogrnip("31277460012345"), make_ogrn("102770013219")

# 1) проверка реквизитов: контрольные цифры ИНН/ОГРН, почта, телефон, ссылки
assert L.inn_ok(INN_IP) and L.inn_ok(INN_OOO)
assert not L.inn_ok(INN_IP[:-1] + str((int(INN_IP[-1]) + 1) % 10)) and not L.inn_ok("12345") and not L.inn_ok("abc")
assert L.ogrn_ok(OGRN) and L.ogrn_ok(OGRNIP) and not L.ogrn_ok(OGRN[:-1] + str((int(OGRN[-1]) + 1) % 10))
assert L.validate("inn", f" {INN_IP} ", "ИП") == (INN_IP, None)
assert L.validate("inn", INN_OOO, "ООО") == (INN_OOO, None)
assert "12 цифр" in L.validate("inn", INN_OOO, "ИП")[1]                        # у ИП ИНН — 12 цифр
assert "10 цифр" in L.validate("inn", INN_IP, "ООО")[1]
assert "контрольн" in L.validate("inn", INN_IP[:-1] + str((int(INN_IP[-1]) + 1) % 10), "ИП")[1]
assert L.validate("ogrn", OGRNIP, "ИП") == (OGRNIP, None) and L.validate("ogrn", OGRN, "ООО") == (OGRN, None)
assert "15 цифр" in L.validate("ogrn", OGRN, "ИП")[1] and "13 цифр" in L.validate("ogrn", OGRNIP, "ООО")[1]
assert L.validate("ogrn", OGRNIP, "Самозанятый")[1]                                  # у самозанятого ОГРН нет
assert L.validate("email", " Help@Site.ru ", "ИП") == ("Help@Site.ru", None) and L.validate("email", "nope", "")[1]
assert L.validate("phone", "8 (999) 123-45-67", "") == ("+79991234567", None)
assert L.validate("phone", "+7 999 123 45 67", "") == ("+79991234567", None) and L.validate("phone", "абв", "")[1]
assert L.validate("offer_url", "site.ru/offer", "") == ("https://site.ru/offer", None)
assert L.validate("privacy_url", "https://site.ru/privacy", "") == ("https://site.ru/privacy", None)
for bad in ("javascript:alert(1)", "http://localhost", "https://", "ftp://site.ru/x", "https://site ru/x"):
    assert L.validate("offer_url", bad, "")[1], bad
assert L.validate("name", "  ИП Иванов Иван Иванович  ", "ИП") == ("ИП Иванов Иван Иванович", None)
assert L.validate("name", "x" * 300, "")[1] and L.validate("address", "", "")[1]
assert L.validate("inn", "-", "ИП") == ("", None)                                    # «-» — очистить поле
assert L.validate("form", "ооо", "") == ("ООО", None) and L.validate("form", "АО", "")[1]
print("l1 ok: ⚖️ ИНН (10/12 цифр) и ОГРН/ОГРНИП (13/15) — с контрольными цифрами и по форме; почта, телефон, "
      "ссылки (только https://…), «-» очищает")

# 2) реквизиты: панель главнее переменных хостинга; версия согласия меняется только от документов и продавца
env = {"LEGAL_NAME": "ИП Петров", "LEGAL_INN": INN_IP, "COOKIES_URL": "https://site.ru/cookies",
       "SUPPORT_EMAIL": "env@site.ru"}
info = L.merge({"name": "ИП Иванов Иван Иванович", "form": "ИП"}, env)
assert info["name"] == "ИП Иванов Иван Иванович" and info["inn"] == INN_IP and info["cookies_url"] == "https://site.ru/cookies"
assert info["email"] == "env@site.ru" and L.merge({}, {})["name"] == ""
v1 = L.version(info)
assert v1 == L.version(dict(info)) and v1 != L.version(dict(info, offer_url="https://site.ru/offer2"))
assert v1 != L.version(dict(info, inn=INN_OOO)) and v1 == L.version(dict(info, phone="+79990000000"))
req = L.requisites(dict(info, ogrn=OGRNIP, address="г. Москва, ул. Пушкина, 1", phone="+79991234567"))
assert f"ИНН {INN_IP}" in req and f"ОГРНИП {OGRNIP}" in req and "г. Москва" in req and "+79991234567" in req
assert f"ОГРН {OGRN}" in L.requisites(dict(info, form="ООО", inn=INN_OOO, ogrn=OGRN))
assert "ОГРН" not in L.requisites(dict(info, form="Самозанятый", ogrn=OGRNIP)), "у самозанятого ОГРН не показываем"
assert L.mismatch(dict(info, ogrn=OGRNIP)) == []
bad = " ".join(L.mismatch(dict(info, form="ООО", ogrn=OGRNIP)))
assert "ИНН" in bad and "10 цифр" in bad and "13 цифр" in bad, bad
assert "нет ОГРН" in " ".join(L.mismatch(dict(info, form="Самозанятый", ogrn=OGRNIP)))
docs = L.documents(dict(info, offer_url="https://site.ru/offer", privacy_url="https://site.ru/privacy"))
assert ("Оферта", "https://site.ru/offer") in docs and ("Политика cookie", "https://site.ru/cookies") in docs
assert set(L.missing({})) >= {"Наименование", "ИНН", "Оферта", "Политика конфиденциальности"}
pt = L.privacy_text(dict(info, inn=INN_IP), "@seller")
for must in ("ID", "канал", "email", "чек", "ЮKassa", "удал", INN_IP, "Иванов"):
    assert must in pt, (must, pt)
print("l2 ok: реквизиты из панели главнее переменных хостинга (COOKIES_URL и др.); согласие привязано к документам и "
      "продавцу; в политике — какие данные и зачем")

# ---------- бот в host-режиме: поддельные MAX и ЮKassa ----------
tele = types.ModuleType("telethon")
tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele
sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, YK_API_URL=srv.url, BOT_TOKEN="HOSTTOKEN", TGMAX_HOST="1", OWNER_ID="1",
                  YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="test_secret")
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "COOKIES_URL", "LEGAL_NAME", "LEGAL_INN",
          "SUPPORT_EMAIL", "OFFER_URL", "PRIVACY_URL", "REFUND_URL", "ADMIN_IDS"):
    os.environ.pop(k, None)
os.chdir(tempfile.mkdtemp())
json.dump({"max_token": "HOSTTOKEN", "max_channel_link": ""}, open("config.json", "w"))
spec = importlib.util.spec_from_file_location("bot", os.path.join(ROOT, "tg_to_max.py"))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
bot.S.trust_env = False
bot.GCFG["trial_days"] = 0
SENT, CUR_CHAT = [], [None]


def _send(chat, text, rows=None, token=None):
    SENT.append((chat, text or "", rows or []))
    return f"m{len(SENT)}"


bot.api_send = _send
bot.send_screen = lambda chat, text, rows=None: _send(chat, text, rows)
bot.api_answer = lambda cid, text, rows=None: SENT.append((CUR_CHAT[0], text or "", rows or []))
bot.upload_image = lambda p: "tok"


def msg(uid, chat, text):
    CUR_CHAT[0] = chat
    bot.handle_update({"update_type": "message_created", "message": {
        "sender": {"user_id": uid, "first_name": f"U{uid}"}, "recipient": {"chat_id": chat, "chat_type": "dialog"},
        "body": {"text": text}}})


def press(uid, chat, payload):
    CUR_CHAT[0] = chat
    bot.handle_update({"update_type": "message_callback", "callback": {
        "callback_id": "c", "payload": payload, "user": {"user_id": uid, "first_name": f"U{uid}"}},
        "message": {"recipient": {"chat_id": chat}}})


def last(chat):
    return next(e for e in reversed(SENT) if e[0] == chat)


def text_of(chat):
    return last(chat)[1]


def btns(chat):
    return [b for row in last(chat)[2] for b in row]


def payloads(chat):
    return [b.get("payload") for b in btns(chat) if b.get("payload")]


def links(chat):
    return {b.get("text"): b.get("url") for b in btns(chat) if b.get("type") == "link"}


HOST, HC, BUYER, BC, B2, B2C = 1, 61000, 5001, 62000, 5002, 63000
msg(HOST, HC, "/start")
msg(BUYER, BC, "/start")
msg(B2, B2C, "/start")
KEY = bot.tenant_key(BUYER)
K2 = bot.tenant_key(B2)
assert KEY and K2 and bot.HOST_CHAT["chat"] == HC
bot.GCFG["tariffs"] = [{"name": "Старт", "themes": 1, "sources": 5, "price": 300},
                       {"name": "Про", "themes": 10, "sources": 30, "price": 1500}]

# 3) «⚖️ Юридическая информация» — только хозяину (и ADMIN_IDS): /legal; покупатель видит только документы
msg(BUYER, BC, "/legal")
assert "Юридическая информация" not in text_of(BC) and not any(p.startswith(("p:cllg", "a:cllg"))
                                                                 for p in payloads(BC)), last(BC)
press(BUYER, BC, "m:cllegal")
assert "Юридическая информация" not in text_of(BC), "экран хозяина покупателю не открыть"
press(BUYER, BC, "p:cllg:inn")
msg(BUYER, BC, INN_IP)
assert not (bot.GCFG.get("legal") or {}).get("inn"), "покупатель не может менять реквизиты"
msg(HOST, HC, "/legal")
t = text_of(HC)
assert t.startswith("⚖️ Юридическая информация") and "Не заполнено" in t, t
for f in ("form", "name", "inn", "ogrn", "address", "email", "phone", "offer_url", "privacy_url", "refund_url",
          "cookies_url"):
    assert any(p in (f"p:cllg:{f}", "a:cllgform") for p in payloads(HC)), (f, payloads(HC))
press(HOST, HC, "m:cltar")
assert "m:cllegal" in payloads(HC), "вход из «💰 Тарифы и реквизиты»"
press(HOST, HC, "a:cllgform")
assert bot.legal_info()["form"] == "ИП" and "Форма: ИП" in text_of(HC)
press(HOST, HC, "a:cllgform")
press(HOST, HC, "a:cllgform")
press(HOST, HC, "a:cllgform")
assert bot.legal_info()["form"] == "ИП", "ИП → ООО → Самозанятый → ИП"


def set_field(field, value):
    press(HOST, HC, f"p:cllg:{field}")
    msg(HOST, HC, value)
    return " ".join(e[1] for e in SENT[-3:] if e[0] == HC)


assert "контрольн" in set_field("inn", INN_IP[:-1] + str((int(INN_IP[-1]) + 1) % 10)) and not bot.legal_info()["inn"]
set_field("inn", INN_IP)
set_field("name", "ИП Иванов Иван Иванович")
set_field("ogrn", OGRNIP)
set_field("address", "г. Москва, ул. Пушкина, д. 1")
set_field("email", "help@site.ru")
set_field("phone", "8 (999) 123-45-67")
set_field("offer_url", "site.ru/offer")
set_field("privacy_url", "https://site.ru/privacy")
set_field("refund_url", "https://site.ru/refund")
li = bot.legal_info()
assert (li["inn"], li["ogrn"], li["phone"], li["offer_url"]) == (INN_IP, OGRNIP, "+79991234567", "https://site.ru/offer"), li
assert "Не заполнено" not in text_of(HC) and f"ИНН: {INN_IP}" in text_of(HC), text_of(HC)
assert json.load(open("settings.json"))["legal"]["inn"] == INN_IP, "реквизиты сохраняются (переживают перезапуск)"
press(HOST, HC, "a:cllgform")                                            # ИП → ООО: ИНН и ОГРНИП не подходят
assert bot.legal_info()["form"] == "ООО" and "⚠️ Не подходит к форме «ООО»" in text_of(HC) \
    and "10 цифр" in text_of(HC), text_of(HC)
press(HOST, HC, "a:cllgform")                                            # → самозанятый: ОГРН не нужен
assert "нет ОГРН" in text_of(HC) and "ОГРН" not in bot.LEGAL.requisites(bot.legal_info()), text_of(HC)
press(HOST, HC, "a:cllgform")                                            # → снова ИП: всё сходится
assert bot.legal_info()["form"] == "ИП" and "Не подходит" not in text_of(HC), text_of(HC)
os.environ["COOKIES_URL"] = "https://site.ru/cookies"
assert bot.legal_info()["cookies_url"] == "https://site.ru/cookies", "COOKIES_URL — заготовка для будущего сайта"
set_field("cookies_url", "-")
assert bot.legal_info()["cookies_url"] == "https://site.ru/cookies", "«-» в панели — снова из переменной"
os.environ.pop("COOKIES_URL")
print("l3 ok: ⚖️ /legal и «💰 Тарифы и реквизиты» → реквизиты и ссылки — только хозяину; покупатель не видит и не "
      "меняет; ошибки ИНН ловятся; всё сохраняется; COOKIES_URL из переменной хостинга")

# 4) покупатель: «📄 Условия» и «❓ Поддержка» — документы, реквизиты, что бот хранит, удаление данных
press(BUYER, BC, "m:terms")
t, lk = text_of(BC), links(BC)
for must in ("Оферта", "Политика конфиденциальности", "Условия возврата", f"ИНН {INN_IP}", f"ОГРНИП {OGRNIP}",
             "help@site.ru", "+79991234567", "ИП Иванов Иван Иванович"):
    assert must in t, (must, t)
assert lk.get("📄 Оферта") == "https://site.ru/offer" and lk.get("🔐 Политика конфиденциальности") == "https://site.ru/privacy"
assert "m:privacy" in payloads(BC) and "m:deldata" in payloads(BC)
press(BUYER, BC, "m:privacy")
pt = text_of(BC)
for must in ("ID", "email", "чек", "ЮKassa", "ИП Иванов Иван Иванович", INN_IP):
    assert must in pt, (must, pt)
press(BUYER, BC, "m:sup")
assert f"ИНН {INN_IP}" in text_of(BC) and "m:legal" in payloads(BC), last(BC)
press(BUYER, BC, "m:legal")
assert "Оферта" in text_of(BC) and "m:deldata" in payloads(BC)
print("l4 ok: у покупателя в «📄 Условия» и «❓ Поддержка»: оферта, политика, возврат, реквизиты (ИНН, ОГРНИП), "
      "контакты; «🔐 Какие данные хранит бот»; кнопка удаления данных")

# 5) согласие ДО оплаты: без галочки оплатить нельзя никаким путём
OFF, ON = "⬜ Я согласен с условиями Оферты и Политикой конфиденциальности", \
    "✅ Я согласен с условиями Оферты и Политикой конфиденциальности"
assert not bot.legal_consented(BUYER)
n_pay = len(srv.payments)
press(BUYER, BC, "m:pay")
press(BUYER, BC, "py:0:30")
t = text_of(BC)
assert any(b.get("text") == OFF for b in btns(BC)) and "lg:tick:0:30" in payloads(BC) and "lg:go:0:30" in payloads(BC), last(BC)
assert "300 ₽" in t and "без скрытых платежей" in t and "18" in t, t
assert links(BC).get("📄 Оферта") == "https://site.ru/offer"
assert len(srv.payments) == n_pay and not str(bot.PENDING.get(BUYER) or "").startswith("payemail")
press(BUYER, BC, "lg:go:0:30")                                           # «Перейти к оплате» без галочки
assert "отметь согласие" in text_of(BC) and len(srv.payments) == n_pay, text_of(BC)
for crafted in ("pk:0:30", "pe:0:30", "pd:0:30", "pk:1:90"):              # подделанные кнопки — тоже нельзя
    press(BUYER, BC, crafted)
    assert any(b.get("text") == OFF for b in btns(BC)), (crafted, last(BC))
assert len(srv.payments) == n_pay and not bot.TENANTS[KEY].get("payreq"), "без согласия — ни ссылки, ни заявки"
bot.PENDING[BUYER] = "payemail:0:30"                                     # даже если бот уже ждал почту
msg(BUYER, BC, "buyer@mail.ru")
assert len(srv.payments) == n_pay and any(b.get("text") == OFF for b in btns(BC)), last(BC)
press(BUYER, BC, "lg:tick:0:30")
assert any(b.get("text") == ON for b in btns(BC)) and bot.legal_consented(BUYER)
rec = json.load(open("legal_consents.json"))[str(BUYER)]
assert rec["ver"] == bot.legal_version() and rec["t"] and rec["key"] == KEY and rec["docs"]["offer"] == "https://site.ru/offer"
press(BUYER, BC, "lg:tick:0:30")                                         # снял галочку — согласия нет
assert any(b.get("text") == OFF for b in btns(BC)) and not bot.legal_consented(BUYER)
press(BUYER, BC, "lg:go:0:30")
assert "отметь согласие" in text_of(BC) and len(srv.payments) == n_pay
press(BUYER, BC, "lg:tick:0:30")
press(BUYER, BC, "lg:go:0:30")
assert bot.PAY_EMAIL_ASK in text_of(BC) and "pe:0:30" in payloads(BC), last(BC)
msg(BUYER, BC, "buyer@mail.ru")
assert len(srv.payments) == n_pay + 1 and links(BC).get("💳 Оплатить 300 ₽", "").startswith("https://yoomoney.ru/")
press(BUYER, BC, "py:1:90")                                              # согласие уже есть — сразу к оплате
assert bot.PAY_EMAIL_ASK in text_of(BC), last(BC)
set_field("offer_url", "https://site.ru/offer-v2")                      # оферта поменялась — согласие заново
assert not bot.legal_consented(BUYER)
press(BUYER, BC, "py:1:90")
assert any(b.get("text") == OFF for b in btns(BC)), last(BC)
for stale in ("lg:tick:9:30", "lg:go:9:30", "lg:tick:0:45", "lg:go:0:abc", "lg:tick:x"):   # старая кнопка (тарифа
    press(BUYER, BC, stale)                                              # уже нет) или подделка — к тарифам, без сбоя
    assert "py:0:30" in payloads(BC) and not bot.legal_consented(BUYER), (stale, last(BC))
assert len(srv.payments) == n_pay + 1
print("l5 ok: ✅ без отметки «Я согласен с условиями Оферты и Политикой конфиденциальности» оплатить нельзя: ни "
      "«Перейти к оплате», ни подделанные кнопки, ни почта; согласие сохраняется с датой и версией документов; новая "
      "оферта — согласие заново")

# 6) без скрытых платежей: в ЮKassa уходит ровно та сумма, что на кнопке и в описании; без автосписаний
bot.legal_agree(BUYER)
for pct in (0, 20):
    bot.TENANTS[KEY]["promo"] = {"pct": pct} if pct else {}
    press(BUYER, BC, "m:pay")
    labels = {b["payload"]: b["text"] for b in btns(BC) if b.get("payload", "").startswith("py:")}
    for i, tarif in enumerate(bot.GCFG["tariffs"]):
        for days in (30, 90):
            base = tarif["price"] if days == 30 else bot.price90(tarif["price"])
            want = round(base * (100 - pct) / 100) if pct else base
            assert f"{want} ₽" in labels[f"py:{i}:{days}"], (labels, want)
            press(BUYER, BC, f"py:{i}:{days}")
            assert f"— {want} ₽" in text_of(BC), text_of(BC)
            n_b = len(srv.payment_bodies)
            press(BUYER, BC, f"pe:{i}:{days}")
            body = srv.payment_bodies[-1]
            assert len(srv.payment_bodies) == n_b + 1 and body["amount"] == {"value": f"{want:.2f}", "currency": "RUB"}
            assert body["capture"] is True and "save_payment_method" not in body and "payment_method_id" not in body
            if body.get("receipt"):
                assert body["receipt"]["items"][0]["amount"] == body["amount"]
            assert links(BC).get(f"💳 Оплатить {want} ₽"), links(BC)
bot.TENANTS[KEY]["promo"] = {}
print("l6 ok: 💳 цена в ЮKassa = цена на кнопке = цена в описании (30 и 90 дней, со скидкой и без), в чеке — та же "
      "сумма; без привязки карты и автосписаний")

# 7) запрос на удаление данных: хозяину — сразу, с кнопками; второй раз не дублируется; после удаления — покупателю
bot.TENANTS[KEY]["enabled"] = False                                     # кабинет выключен — удалить данные
assert not bot.tenant_live(KEY)                                         # всё равно можно (152-ФЗ)
press(BUYER, BC, "m:legal")
assert "m:deldata" in payloads(BC), last(BC)
press(BUYER, BC, "m:deldata")
assert "lg:del" in payloads(BC) and "удал" in text_of(BC), last(BC)
n_host = len([e for e in SENT if e[0] == HC])
press(BUYER, BC, "lg:del")
alert = next(e for e in reversed(SENT) if e[0] == HC)
assert "удален" in alert[1].lower() and str(BUYER) in alert[1] and KEY in json.dumps(alert[2]), alert
assert f"m:cldel:{KEY}" in json.dumps(alert[2]) and f"m:clc:{KEY}" in json.dumps(alert[2])
assert "Запрос отправлен" in " ".join(e[1] for e in SENT[-3:] if e[0] == BC)
reqs = json.load(open("legal_requests.json"))
assert reqs[KEY]["status"] == "new" and reqs[KEY]["uid"] == str(BUYER) and reqs[KEY]["t"]
n_host = len([e for e in SENT if e[0] == HC])
press(BUYER, BC, "lg:del")
assert len([e for e in SENT if e[0] == HC]) == n_host and "уже" in text_of(BC), "повторно — без второго письма"
press(HOST, HC, f"a:cldelok:{KEY}")                                    # хозяин удалил кабинет
told = [e[1] for e in SENT if e[0] == BC][-3:]
assert any("удалены" in x for x in told), told
assert KEY not in bot.TENANTS and json.load(open("legal_requests.json"))[KEY]["status"] == "done"
assert "name" not in json.load(open("legal_consents.json")).get(str(BUYER), {}), "в согласии не храним имя"
print("l7 ok: 🗑 «Запросить удаление моих данных» — хозяину с кнопками «удалить кабинет» и «кабинет», покупателю — "
      "«запрос отправлен», повторно — без дубля; удалил — покупателю «данные удалены», запрос закрыт")

# 8) отписка от рекламы (закон «О рекламе»): кнопка в каждой рекламе, отписался — больше не получает
assert K2 in bot.ad_targets()
bot.ad_broadcast({"body": "Реклама. ООО «Ромашка», erid: 2VtzqvXyz\n\nКупи слона", "image": None, "erid": "2VtzqvXyz",
                  "mark": "ООО «Ромашка»", "text": "Купи слона", "chat": HC})
ad = last(B2C)
assert "Купи слона" in ad[1] and "lg:noads" in json.dumps(ad[2]), ad
press(B2, B2C, "lg:noads")
assert bot.TENANTS[K2].get("ads_off") and K2 not in bot.ad_targets() and "не пришлю" in text_of(B2C)
print("l8 ok: 🔕 в рекламе — кнопка «Не присылать рекламу»; нажал — рассылки ему больше не уходят")

print("ВСЕ ТЕСТЫ ЮРИДИЧЕСКОГО МОДУЛЯ ПРОШЛИ")
os._exit(0)
