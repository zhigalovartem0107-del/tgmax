import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
"""Один бот для всех (HOST-режим): кабинеты, онбординг, оплата (30/90 дней), изоляция, лимиты, публикация только с подпиской."""
import asyncio, importlib.util, json, os, sys, tempfile, time, types
sys.path.insert(0, ROOT)
from fake_max import FakeMax

tele = types.ModuleType("telethon"); tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, YK_API_URL=srv.url, BOT_TOKEN="HOSTTOKEN",
                  TGMAX_HOST="1", OWNER_ID="1", YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="test_secret")
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    os.environ.pop(k, None)
os.chdir(tempfile.mkdtemp())
json.dump({"max_token": "HOSTTOKEN", "max_channel_link": ""}, open("config.json", "w"))
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
bot.S.trust_env = False

OUT = []   # всё, что бот ответил пользователю: (text, rows)
bot.api_send = lambda chat, text, rows=None, token=None: (OUT.append((text or "", rows or [])), "m")[1]
bot.send_screen = lambda chat, text, rows=None: (OUT.append((text or "", rows or [])), "m")[1]
bot.api_answer = lambda cid, text, rows=None: OUT.append((text or "", rows or []))
bot.upload_image = lambda p: "tok"
bot.ensure_cffi = lambda: True                                 # в тестах ничего не ставим через pip
def _no_net(*a, **k):
    raise RuntimeError("в тестах нет сети до TikTok")
bot.ytdl_list = _no_net
REAL_TT = (bot.tt_embed_posts, bot.tikwm_posts)                # в h57 — со своей подделкой TikTok
bot.tt_embed_posts = bot.tikwm_posts = _no_net                 # тесты не ходят в настоящий TikTok

assert bot.HOST_MODE and bot.HOST_OWNER == 1
bot.GCFG["trial_days"] = 0                                    # пробный период проверяем отдельно (h33)


def msg(uid, chat, text):
    bot.handle_update({"update_type": "message_created", "message": {
        "sender": {"user_id": uid, "first_name": f"U{uid}"}, "recipient": {"chat_id": chat, "chat_type": "dialog"},
        "body": {"text": text}}})


def press(uid, chat, payload):
    bot.handle_update({"update_type": "message_callback", "callback": {
        "callback_id": "c", "payload": payload, "user": {"user_id": uid, "first_name": f"U{uid}"}},
        "message": {"recipient": {"chat_id": chat}}})


def shown():
    return json.dumps(OUT[-1], ensure_ascii=False) if OUT else "[]"


def buy(uid, chat, plan):
    """Выбрал тариф; если бот спросил почту для чека — «Пропустить» (чек на запасную почту)."""
    press(uid, chat, "py:" + plan)
    if f"pe:{plan}" in shown():
        press(uid, chat, "pe:" + plan)


def buttons_of(rows):
    return [b_ for r in rows for b_ in r]


# 1) новый пользователь -> свой кабинет на бесплатном тарифе (1 канал MAX, 2 источника, навсегда) — сразу меню
msg(100, 5100, "/start")
assert "100" in bot.TENANTS and bot.tenant_mode("100") == "free" and len(bot.tenant_themes("100")) == 1
assert "Оплатить доступ" not in shown() and "оформи доступ" not in shown(), shown()
assert any("бесплатн" in o[0].lower() and "навсегда" in o[0] for o in OUT), [o[0][:60] for o in OUT[-4:]]
print("h1 ok: новый пользователь — сразу свой кабинет на бесплатном тарифе, меню открыто")

# 2) на бесплатном тарифе меню открыто (раньше без подписки — только экран оплаты)
press(100, 5100, "m:ch")
assert "Оплатить доступ" not in shown() and "p:chadd" in shown(), shown()
print("h2 ok: на бесплатном тарифе меню открыто")

# 3) оплата на 30 дней через ЮKassa -> доступ открывается сам
bot.GCFG["tariffs"] = [{"name": "Старт", "themes": 1, "sources": 5, "price": 300},
                       {"name": "Про", "themes": 10, "sources": 30, "price": 1500}]
press(100, 5100, "m:pay")
assert "Тариф и оплата" in OUT[-1][0] and "py:0:30" in shown() and "py:0:90" in shown(), shown()
n0 = len(OUT)
buy(100, 5100, "0:30")                                  # нажал тариф — сразу ссылка ЮKassa, без лишних шагов
assert "yoomoney.ru/checkout" in shown(), shown()
assert sum("yoomoney.ru/checkout" in json.dumps(o[1]) for o in OUT[n0:]) == 1, "ссылка — одним сообщением, без дубля"
pid = bot.TENANTS["100"].get("pay", {}).get("pid")
assert pid and srv.payments[pid]["amount"]["value"] == "300.00", bot.TENANTS["100"]
srv.payments[pid]["status"] = "succeeded"
bot.host_pay_poll()
assert bot.tenant_mode("100") == "run" and bot.TENANTS["100"]["plan"] == "Старт"
print("h3 ok: оплата на 30 дней картой через ЮKassa открывает доступ автоматически")

assert bot.price90(300) == 750
print("h3б ok: 90 дней = цена ×2.5 (300 -> 750)")

# 4) с подпиской пользователь добавляет свои каналы (после подтверждения прав)
t100 = bot.tenant_themes("100")[0]
t100.chat_id = 9100
press(100, 5100, "p:chadd")
assert "права на контент" in shown().lower() or "Подтверди" in shown(), shown()
press(100, 5100, "rk:ok")
msg(100, 5100, "robloxers roblox1")
with bot.theme_ctx(t100):
    assert bot.CFG["channels"] == ["robloxers", "roblox1"], bot.CFG["channels"]
print("h4 ok: с подпиской пользователь добавляет свои каналы-источники")

# 5) второй пользователь — отдельный изолированный кабинет
msg(200, 5200, "/start")
bot.host_extend("200", 30)
t200 = bot.tenant_themes("200")[0]
with bot.theme_ctx(t200):
    assert bot.CFG["channels"] == []
    bot.CFG["channels"].append("standoff_news"); bot.save_cfg()
with bot.theme_ctx(t100):
    assert bot.CFG["channels"] == ["robloxers", "roblox1"]
assert bot.active_theme(200) is t200 and bot.active_theme(100) is t100
assert bot.tenant_key(100) == "100" and bot.tenant_key(200) == "200" and bot.tenant_key(999) is None
print("h5 ok: у каждого пользователя свой изолированный кабинет и каналы")

# 6) лимит тем по тарифу (Старт — 1 тема)
press(100, 5100, "p:themenew")
msg(100, 5100, "Вторая тема")
assert "тариф" in shown().lower(), shown()
assert len(bot.tenant_themes("100")) == 1
print("h6 ok: лимит тем по тарифу соблюдается")

# 7) публикует только кабинет с активной подпиской
published = []
async def fake_pub(client, chat_id, it):
    published.append((chat_id, it["id"])); return "ok"
REAL_PUBLISH = bot.publish_item
bot.publish_item = fake_pub
bot.TENANTS["200"]["enabled"] = False
t200.chat_id = 9200
for t in (t100, t200):
    with bot.theme_ctx(t):
        bot.QUEUE.append({"id": t.prefix + "x:1", "ch": "c", "date": time.time(), "enq": time.time(), "text": "p",
                          "photos": [], "vids": [], "docs": [], "sent": 0, "fails": 0, "urgent": False, "status": "ok"})
        bot.FORCE["until"] = time.time() + 300
        asyncio.run(bot.publish_due(None, t.chat_id))
pub_themes = {c for c, _ in published}
assert 9100 in pub_themes and 9200 not in pub_themes, published
print("h7 ok: публикует только кабинет с активной подпиской; выключенный молчит")

# 8) у хозяина — панель всех кабинетов; у пользователей её нет
bot.HOST_CHAT["chat"] = 11
got = bot.screen("cl", 1)
assert got and "Кабинеты" in got[0] and "Покупателей всего: 2" in got[0], got[0]
assert "Покупок подписки: 1" in got[0] and "Выручка: 300 ₽" in got[0] and "Пользуются с подпиской: 1" in got[0], got[0]
assert "m:clc:" not in json.dumps(got[1]), got[1]          # кнопок на каждого покупателя больше нет
assert bot.host_screen("cl", 100) is None and bot.is_host(1) and not bot.is_host(100)
bot.host_action(["clext", "200", "30"], 1)
assert bot.TENANTS["200"]["enabled"] and bot.tenant_mode("200") == "run"
print("h8 ok: у хозяина панель всех кабинетов и управление; у пользователей её нет")

# 9) ручная оплата -> заявка хозяину -> подтверждение открывает доступ
bot.TENANTS["200"]["enabled"] = False; bot.TENANTS["200"]["until"] = 0
_yk_real = bot.yk_keys
bot.yk_keys = lambda: None
t, r = bot.host_pay_manual("0:30", 200)
assert "payreq" in bot.TENANTS["200"] and "Заявка" in t
bot.host_action(["clpayok", "200"], 1)
assert bot.tenant_mode("200") == "run" and bot.TENANTS["200"]["plan"] == "Старт"
print("h9 ok: ручная оплата — заявка хозяину, подтверждение открывает доступ")

# 10) удаление кабинета стирает только его данные
bot.host_action(["cldelok", "200"], 1)
assert "200" not in bot.TENANTS and not bot.tenant_themes("200") and bot.tenant_themes("100")
print("h10 ok: удаление кабинета стирает только его данные")

# 11) старые «ничьи» темы (обычный бот до host-режима) становятся личным кабинетом хозяина — бесплатно и бессрочно
orphan = bot.Theme("orph", "Старая тема", "", owner="")
bot.THEMES.append(orphan)
bot.host_adopt_orphans()
assert orphan.owner == "1" and "1" in bot.TENANTS, (orphan.owner, list(bot.TENANTS))
assert bot.tenant_mode("1") == "run" and bot.TENANTS["1"]["plan"] == "Хозяин", bot.TENANTS["1"]
assert orphan in bot.tenant_themes("1")
# в панели у хозяина есть кнопка «Мои каналы»; свой кабинет не продлевается/не удаляется, а настраивается
assert "m:home" in json.dumps(bot.screen("cl", 1)[1]), bot.screen("cl", 1)[1]
own = bot.host_screen("clc:1", 1)
assert "m:home" in json.dumps(own[1]) and "cldel:1" not in json.dumps(own[1]), own
# повторный запуск ничего не ломает (нет «ничьих» тем — делать нечего)
bot.host_adopt_orphans()
assert orphan in bot.tenant_themes("1")
# хозяин опознаётся и когда MAX присылает id строкой (а не числом)
assert bot.is_host("1") and bot.is_host(1) and not bot.is_host("2")
print("h11 ok: старые темы стали личным кабинетом хозяина — бесплатно, бессрочно, настраивается из панели")

# 12) OWNER_ID раньше был неверным → при его смене темы и кабинет хозяина переезжают на нынешний OWNER_ID
stale = bot.Theme("stale1", "Тема старого хозяина", "", owner="555")
bot.THEMES.append(stale)
bot.TENANTS["555"] = bot.blank_tenant(555, "Мой кабинет (хозяин)")
bot.TENANTS["555"]["plan"] = "Хозяин"
bot.host_adopt_orphans()
assert "555" not in bot.TENANTS, "прежний кабинет хозяина должен исчезнуть"
assert stale.owner == "1" and stale in bot.tenant_themes("1"), stale.owner
print("h12 ok: при смене OWNER_ID темы и кабинет хозяина переезжают на новый ID (ничего не теряется)")

# 13) хозяин опознаётся ПОЛНОСТЬЮ и когда MAX шлёт id строкой (а не числом) — иначе бот просил бы у хозяина оплату
assert bot.is_host("1") and bot.is_admin("1") and bot.is_owner("1"), "строковый id хозяина должен опознаваться"
assert not bot.owner_locked_out("1")
assert bot.active_theme("1") is not None and bot.my_themes("1")
print("h13 ok: хозяин опознаётся и когда id приходит строкой — панель, а не оплата")

# 14) кабинет хозяина, оставшийся «клиентским» после ранних экспериментов, сам чинится в бессрочный
bot.TENANTS["1"]["plan"] = ""; bot.TENANTS["1"]["until"] = 0
bot.host_adopt_orphans()
assert bot.tenant_mode("1") == "run" and bot.TENANTS["1"]["plan"] == "Хозяин", bot.TENANTS["1"]
print("h14 ok: кабинет хозяина самочинится в бессрочный, даже если остался «клиентским»")

# 15) привязка хозяина по секретному коду — на случай, если OWNER_ID не совпал с реальным ID в MAX
bot.HOST_CODE["code"] = "abcdef234567"
# пользователь 999 (его id НЕ равен OWNER_ID=1) присылает код хозяина
msg(999, 59000, "abcdef234567")
assert bot.GCFG.get("host_uid") == "999", bot.GCFG.get("host_uid")
assert bot.is_host(999) and bot.is_host("999"), "привязанный по коду должен быть хозяином"
assert bot.HOST_CODE["code"] != "abcdef234567", "код должен смениться после использования"
assert "1" not in bot.TENANTS, "прежний кабинет хозяина должен переехать на нового"
assert bot.my_themes(999), "хозяину достались темы"
# и теперь оплату у него не просит
press(999, 59000, "m:th")
assert "платить" not in shown().lower() and "Оплат" not in shown(), shown()
print("h15 ok: хозяин привязывается по секретному коду — становится хозяином, темы переезжают, оплату не просит")

# 16) «повисшая» тема (её кабинета больше нет — например, потерялся файл кабинетов) хозяину НЕ достаётся:
#     это чужой канал (иначе хозяину идут чужие посты); тема не работает; покупатель вернётся — она снова его
ghost = bot.Theme("ghost1", "Повисшая тема", "", owner="888888")
bot.THEMES.append(ghost)
assert "888888" not in bot.TENANTS
bot.host_adopt_orphans()
assert ghost.owner == "888888" and ghost not in bot.tenant_themes("999") and not bot.theme_live(ghost), ghost.owner
msg(888888, 88880, "/start")                                  # покупатель вернулся — его тема снова у него
assert bot.tenant_key(888888) == "888888" and bot.tenant_themes("888888") == [ghost], bot.tenant_themes("888888")
assert bot.theme_live(ghost)
print("h16 ok: «повисшая» тема хозяину не достаётся и не работает; вернулся покупатель — тема снова его")

# 17) хозяин может создать свой кабинет и темы с НУЛЯ (если тем на боте не осталось) — без оплаты
bot.GCFG.pop("host_uid", None)            # хозяин снова по OWNER_ID=1
bot.THEMES.clear()
bot.TENANTS.clear()
assert bot.my_themes(1) == [] and bot.active_theme(1) is None
text, rows = bot.screen("home", 1)        # «🎭 Мои каналы» у хозяина с пустого бота
assert bot.tenant_themes("1"), "должен появиться кабинет хозяина с темой"
assert "платить" not in text.lower() and "Оплат" not in text, text
# и в панели есть кнопка «Мои каналы» даже когда тем ещё нет
assert "m:home" in json.dumps(bot.screen("cl", 1)[1])
print("h17 ok: хозяин создаёт свой кабинет и темы с нуля — панель и «Мои каналы» доступны, без оплаты")

# 18) хозяин — только по OWNER_ID/коду: кто пишет в тот же КАНАЛ, что и хозяин, хозяином не становится
bot.HOST_CHAT["chat"] = None
msg(1, 61000, "/start")                     # хозяин (id 1 == OWNER_ID) в личке
assert bot.is_host(1) and bot.HOST_CHAT["chat"] == 61000
def chan_post(uid, chat):
    bot.handle_update({"update_type": "message_created", "message": {
        "sender": {"user_id": uid, "first_name": f"U{uid}"}, "recipient": {"chat_id": chat, "chat_type": "channel"},
        "body": {"text": "тест"}}})
chan_post(1, -9999)                         # хозяин написал в канале
chan_post(777, -9999)                       # и покупатель (тоже админ этого канала) написал туда же
assert not bot.is_host(777), "автор поста в канале хозяина НЕ должен становиться хозяином"
assert bot.HOST_CHAT["chat"] == 61000, "уведомления хозяину — в личку, не в канал"
press(777, 63000, "m:main")
assert "Кабинеты" not in shown(), shown()
print("h18 ok: хозяин — только по OWNER_ID/коду; писавшие в его канал не получают панель")

# 19) у ПОКУПАТЕЛЯ тоже разные id в «Старте» и в кнопках — кабинет находим по чату; если дни есть — оплату не просим
msg(5001, 62000, "/start")                  # покупатель стартует из чата 62000
ckey = bot.tenant_by_chat(62000)
assert ckey is not None, "кабинет покупателя должен создаться"
bot.host_extend(ckey, 3)                    # хозяин дал ему 3 дня
assert bot.tenant_mode(ckey) == "run"
press(5002, 62000, "m:ch")                  # тот же покупатель жмёт кнопку, но MAX прислал ДРУГОЙ id из того же чата
assert "Оплатить доступ" not in shown() and "оформи доступ" not in shown(), shown()
assert bot.tenant_by_chat(62000) == ckey and bot.tenant_key(5002) == ckey, "второй id — тот же кабинет, без дубля"
print("h19 ok: кабинет покупателя находится по чату — разные id не плодят дубли, с днями оплату не просит")

# 20) в host-режиме старые «клиентские» боты не запускаются — особенно на токене основного бота
#     (раньше второй бот на том же токене перехватывал нажатия и всем писал «оформи доступ»)
bot.CLIENTS["old1"] = {"token": "HOSTTOKEN", "name": "Reloader", "until": time.time() + 86400, "enabled": True}
bot.start_client("old1")
assert "old1" not in bot.PROCS, bot.PROCS
bot.CLIENTS.pop("old1")
print("h20 ok: второй бот на токене основного не запускается — кнопки не перехватываются")

# 21) привязка канала MAX кнопкой: бот показывает каналы, где он админ; покупатель видит только СВОИ каналы
srv.token = "HOSTTOKEN"                                       # фальшивый MAX принимает токен этого бота
srv.private[7700] = ("Мой закрытый", "https://max.ru/join/abcXYZ"); srv.chat_admins[7700] = [5001]
srv.private[7800] = ("Чужой", "https://max.ru/join/zzz"); srv.chat_admins[7800] = [99999]
press(5001, 62000, "m:chpick")
assert "a:chbind:7700" in shown() and "a:chbind:7800" not in shown(), shown()
press(5001, 62000, "a:chbind:7800")                          # чужой канал подсунуть нельзя
ct = bot.tenant_themes(ckey)[0]
assert ct.chat_id != 7800, ct.chat_id
press(5001, 62000, "a:chbind:7700")
assert ct.chat_id == 7700 and "привязан" in shown(), (ct.chat_id, shown())
press(5001, 62000, "m:chpick")                               # привязанный канал помечен «эта тема»
assert "Мой закрытый (эта тема)" in shown(), shown()
# закрытый канал по ссылке max.ru/join/… (раньше не находился вообще)
srv.private[7900] = ("Хозяйский", "https://max.ru/join/hostXYZ")
ht = bot.ensure_host_cabinet()
with bot.theme_ctx(ht):
    ht.chat_id = None
    ans, _ = bot.apply_text("thlink", "https://max.ru/join/hostXYZ", uid=1)
assert ht.chat_id == 7900 and "привязан" in ans, (ht.chat_id, ans)
print("h21 ok: канал привязывается кнопкой из списка и по ссылке на закрытый канал; чужой канал не привязать")

# 22) панель кабинетов вызывается командой /panel — только у хозяина
msg(1, 61000, "/panel")
assert "Кабинеты" in OUT[-1][0], OUT[-1][0]
msg(5001, 62000, "/panel")
assert "только для администраторов" in OUT[-1][0], OUT[-1][0]
print("h22 ok: /panel открывает панель кабинетов хозяину, покупателю — нет")

# 23) у покупателя — простое меню (как раньше в его боте); пока дни есть — без кнопки оплаты, за 3 дня — «Продлить»
bot.TENANTS[ckey]["until"] = time.time() + 30 * 86400
press(5001, 62000, "m:main")
assert "Расписание" in shown() and "Мои каналы" in shown(), shown()
assert "Подписка" in shown() and "Кабинеты" not in shown(), shown()          # докупить дни можно всегда
bot.TENANTS[ckey]["until"] = time.time() + 2 * 86400
press(5001, 62000, "m:main")
assert "Подписка" in shown(), shown()
press(5001, 62000, "m:more")
assert "Ещё настройки" in shown() and "🔑 Telegram" not in shown(), shown()
msg(1, 61000, "/admin")                                      # а у хозяина по-прежнему панель — по /admin
assert "Кабинеты" in OUT[-1][0], OUT[-1][0]
assert not any(n == "panel" for n, _ in bot.COMMAND_HELP)                      # /panel не светится в подсказках
print("h23 ok: покупателю — простое меню с «Продлить доступ»; /panel не в подсказках; хозяину — панель")

# 24) ПРОМОКОДЫ: хозяин создаёт и настраивает; покупатель вводит — цена реально меньше; лимиты и срок работают
bot.yk_keys = _yk_real                                        # снова онлайн-оплата через ЮKassa (в h9 её выключали)
bot.GCFG["tariffs"] = [{"name": "Старт", "themes": 1, "sources": 5, "price": 300},
                       {"name": "Про", "themes": 10, "sources": 30, "price": 1500}]
press(1, 61000, "m:clpr")
assert "Промокоды" in shown() and "p:clpromo:pct" in shown(), shown()
press(1, 61000, "p:clpromo:pct")
msg(1, 61000, "leto 50!")                                    # название чистится: LETO50
assert "LETO50" in bot.promos() and bot.promos()["LETO50"]["value"] == 50, bot.promos()
press(1, 61000, "a:clpv:LETO50:-10")                         # 50% -> 40%
assert bot.promos()["LETO50"]["value"] == 40
press(1, 61000, "a:clpv:LETO50:10")                          # обратно 50%
press(1, 61000, "a:clpm:LETO50:-1"); press(1, 61000, "a:clpm:LETO50:-1")
assert bot.promos()["LETO50"]["max"] == 8, bot.promos()["LETO50"]
# покупатель (кабинет 5001, подписка есть) вводит промокод при оплате
press(5001, 62000, "m:pay")
assert "p:promo" in shown() and "300 ₽" in shown(), shown()
press(5001, 62000, "p:promo")
msg(5001, 62000, "leto50")
assert "скидка 50%" in OUT[-2][0], OUT[-2][0]
assert "150 ₽" in shown() and "375 ₽" in shown() and "−50%" in shown(), shown()   # 300->150, 750->375
buy(5001, 62000, "0:30")                                # онлайн-платёж — со скидкой
assert "150 ₽" in shown(), shown()
pid = bot.TENANTS[ckey]["pay"]["pid"]
assert srv.payments[pid]["amount"]["value"] == "150.00", srv.payments[pid]["amount"]
srv.payments[pid]["status"] = "succeeded"; bot.host_pay_poll()
assert "promo" not in bot.TENANTS[ckey], "скидка — на одну оплату"
press(5001, 62000, "p:promo"); msg(5001, 62000, "LETO50")     # второй раз тот же покупатель — нельзя
assert "уже активировал" in OUT[-2][0], OUT[-2][0]
# лимит активаций и срок
bot.promos()["ONE"] = {"kind": "pct", "value": 10, "max": 1, "used": ["someone"], "until": 0}
assert bot.promo_apply(ckey, "one").startswith("❌") and "закончились" in bot.promo_apply(ckey, "one")
bot.promos()["OLD"] = {"kind": "pct", "value": 10, "max": 0, "used": [], "until": time.time() - 10}
assert "срок" in bot.promo_apply(ckey, "old")
assert "нет" in bot.promo_apply(ckey, "nosuch")
# бесплатные дни: новый покупатель без подписки вводит код прямо с экрана «оплатите»
press(1, 61000, "p:clpromo:days"); msg(1, 61000, "FREE7")
assert bot.promos()["FREE7"]["kind"] == "days" and bot.promos()["FREE7"]["value"] == 7
msg(6001, 66000, "/start")
k6 = bot.tenant_by_chat(66000)
press(6001, 66000, "m:pay")
assert bot.tenant_mode(k6) == "free" and "p:promo" in shown(), shown()
press(6001, 66000, "p:promo")
msg(6001, 66000, "free7")
assert bot.tenant_mode(k6) == "run" and bot.TENANTS[k6]["plan"] == "Старт", bot.TENANTS[k6]
assert 6.9 * 86400 < bot.TENANTS[k6]["until"] - time.time() <= 7 * 86400 + 5
# 100% — бесплатно, без ЮKassa
bot.promos()["FULL"] = {"kind": "pct", "value": 100, "max": 0, "used": [], "until": 0}
bot.TENANTS[k6]["until"] = time.time() - 1
press(6001, 66000, "p:promo"); msg(6001, 66000, "FULL")
press(6001, 66000, "py:1:90")
assert "бесплатно" in shown(), shown()
press(6001, 66000, "pk:1:90")
assert bot.tenant_mode(k6) == "run" and bot.TENANTS[k6]["plan"] == "Про" and "pay" not in bot.TENANTS[k6]
# удаление
press(1, 61000, "a:clpdel:ONE")
assert "ONE" not in bot.promos()
print("h24 ok: промокоды — скидка уменьшает цену (и в ЮKassa), бесплатные дни, 100%, лимит активаций, срок, 1 раз на покупателя")

# 25) хозяин переключается между своими темами — выбор не сбрасывается на первую
hk = bot.host_id()
mine = bot.tenant_themes(hk)
mine[0].name = "Роблокс"
if not any(t.name == "Стандофф" for t in mine):
    bot.new_cabinet_theme(hk, "Стандофф")
mine = bot.tenant_themes(hk)
i = next(n for n, t in enumerate(mine) if t.name == "Стандофф")
press(1, 61000, "m:home")
press(1, 61000, "m:th")
press(1, 61000, f"a:thsw:{i}")
assert "Стандофф" in OUT[-1][0] and bot.active_theme(1).name == "Стандофф", (OUT[-1][0][:80], bot.active_theme(1).name)
press(1, 61000, "m:cl")                                      # ушёл в панель
press(1, 61000, "m:home")                                    # и вернулся в «Мои каналы»
assert bot.active_theme(1).name == "Стандофф" and "Стандофф" in OUT[-1][0], OUT[-1][0][:80]
print("h25 ok: хозяин переключается на другую тему, и выбор не сбрасывается на первую")

# 26) посты, карточки, журнал и проверка хозяина НЕ уходят покупателю
hk = bot.host_id()
ht = next(t for t in bot.tenant_themes(hk) if t.name == "Стандофф")
bt = bot.tenant_themes(ckey)[0]
with bot.theme_ctx(ht):
    hid = ht.prefix + "sec:1"
    bot.QUEUE.append({"id": hid, "ch": "hostsrc", "date": time.time(), "enq": time.time(), "text": "СЕКРЕТ ХОЗЯИНА",
                      "photos": [], "vids": [], "docs": [], "sent": 0, "fails": 0, "urgent": False, "status": "pending"})
    bot.journal("ЖУРНАЛ ХОЗЯИНА: опубликован пост из hostsrc")
OUT.clear()
bot.resend_pending_cards(62000, force=True, uid=5001)        # покупатель «появился» — досылаем ЕГО карточки
assert not any("СЕКРЕТ ХОЗЯИНА" in json.dumps(o, ensure_ascii=False) for o in OUT), OUT
press(5001, 62000, f"ap:now:{hid}")                          # нажать ✅ на чужом посте нельзя
assert "не из твоих тем" in shown(), shown()
with bot.theme_ctx(ht):
    assert next(x for x in bot.QUEUE if x["id"] == hid)["status"] == "pending"
press(5001, 62000, "m:lg")
assert "ЖУРНАЛ ХОЗЯИНА" not in shown(), shown()
with bot.theme_ctx(bt):
    bot.journal("ЖУРНАЛ ПОКУПАТЕЛЯ: опубликован пост")
press(1, 61000, "m:home"); press(1, 61000, "m:lg")
assert "ЖУРНАЛ ХОЗЯИНА" in shown() and "ЖУРНАЛ ПОКУПАТЕЛЯ" not in shown(), shown()   # и хозяину — только своё
press(5001, 62000, "m:lg")
assert "ЖУРНАЛ ПОКУПАТЕЛЯ" in shown() and "ЖУРНАЛ ХОЗЯИНА" not in shown(), shown()
with bot.theme_ctx(bt):
    assert all(t.owner == ckey for t in bot.scope_themes()) and ht not in bot.scope_themes()
# чат покупателя, если раньше попал в кабинет хозяина, вычищается при запуске
bot.TENANTS[hk].setdefault("chats", {})["5001"] = 62000
bot.host_clean_chats()
assert "5001" not in bot.TENANTS[hk]["chats"], bot.TENANTS[hk]["chats"]
# и больше туда не попадёт, даже если «текущей» оказалась тема хозяина
with bot.theme_ctx(ht):
    bot.handle_message(5001, 62000, "U5001", "/help")
assert "5001" not in bot.TENANTS[hk].get("chats", {}), bot.TENANTS[hk]["chats"]
print("h26 ok: посты, карточки ✅/❌, журнал и проверка хозяина покупателю не видны; чужой чат из кабинета вычищается")

# 27) хозяин пересылает сообщение покупателя (или присылает его ID) — открывается его кабинет; промокоды убавляются
def forward(uid, chat, from_uid):
    bot.handle_update({"update_type": "message_created", "message": {
        "sender": {"user_id": uid, "first_name": f"U{uid}"}, "recipient": {"chat_id": chat, "chat_type": "dialog"},
        "body": {"text": ""}, "link": {"type": "forward", "sender": {"user_id": from_uid, "name": "Покупатель"},
                                       "message": {"text": "привет"}}}})
forward(1, 61000, 5001)
assert "Кабинет:" in OUT[-1][0] and "a:clext:" in json.dumps(OUT[-1][1]), OUT[-1]
msg(1, 61000, "6001")                                        # по ID
assert "Кабинет:" in OUT[-1][0], OUT[-1][0]
forward(1, 61000, 424242)                                    # человек без кабинета
assert "не заходил" in OUT[-1][0], OUT[-1][0]
forward(5001, 62000, 6001)                                   # покупатель так чужой кабинет не откроет
assert "Кабинет:" not in OUT[-1][0]
bot.promos()["MINUS"] = {"kind": "pct", "value": 30, "max": 10, "used": [], "until": time.time() + 10 * 86400}
press(1, 61000, "a:clpm:MINUS:-1"); press(1, 61000, "a:clpm:MINUS:-10")
assert bot.promos()["MINUS"]["max"] == 1, bot.promos()["MINUS"]                     # 10 -> 9 -> не меньше 1
press(1, 61000, "a:clpu:MINUS:-7")
assert 2.9 * 86400 < bot.promos()["MINUS"]["until"] - time.time() < 3.1 * 86400
press(1, 61000, "a:clpu:MINUS:-7")                           # убавили до прошлого — выключен
assert "срок" in (bot.promo_problem(bot.promos()["MINUS"]) or "")
press(1, 61000, "m:clpo:MINUS")
assert "a:clpm:MINUS:-10" in shown() and "a:clpu:MINUS:-1" in shown(), shown()
press(1, 61000, "m:cl")
assert "Покупателей всего" in shown() and "Последние покупки" in shown(), shown()
print("h27 ok: кабинет покупателя открывается пересылкой его сообщения или по ID; в промокодах можно убавлять")

# 28) закрытый канал «застрял» у другой (пустой) темы — виден в списке и перепривязывается; проверка публикации
srv.private[7950] = ("Закрытый хозяина", "https://max.ru/join/qqq")
stray = bot.new_cabinet_theme(hk, "Мой канал")              # пустая тема, которой канал достался по ошибке
stray.chat_id = 7950
press(1, 61000, "m:home"); press(1, 61000, "m:chpick")
assert "Закрытый хозяина (сейчас у «Мой канал»)" in shown(), shown()
cur_t = bot.active_theme(1)
press(1, 61000, "a:chbind:7950")
assert cur_t.chat_id == 7950 and stray.chat_id is None, (cur_t.chat_id, stray.chat_id)
press(1, 61000, "a:chtest")                                   # бот — админ: проверка пройдена
assert "Проверка пройдена" in shown(), shown()
srv.deny_chats.add(7950)                                      # а теперь бот не админ — объясняем, что сделать
press(1, 61000, "a:chtest")
assert "не разрешает боту публиковать" in shown() and "Публиковать сообщения" in shown(), shown()
srv.deny_chats.discard(7950)
print("h28 ok: закрытый канал, застрявший у другой темы, перепривязывается; «Проверить публикацию» объясняет отказ MAX")

# 29) умный фильтр рекламы: точно реклама — сам; спорное (видео + «переходи в бота») — спрашивает владельца
vid_post = "Смотри какое видео 🔥 Ещё больше сливов — переходи в бота @vanileaks_bot, в боте всё бесплатно"
assert bot.ad_verdict(vid_post, "vanileaks")[0] == "ask", bot.ad_verdict(vid_post, "vanileaks")
assert bot.ad_verdict("Забирай промокод на скидку по ссылке https://shop.example.com/x", "c", 1)[0] == "ad"
assert bot.ad_verdict("Лучший VPN. Реклама. ООО «Ромашка» erid: 2SDnjc1x", "c")[0] == "ad"
assert bot.ad_verdict("Мем дня про Standoff", "c")[0] == "ok"
ht = bot.active_theme(1)
def held_item(n, text):
    return {"id": ht.prefix + f"vanileaks:{n}", "ch": "vanileaks", "date": time.time(), "enq": time.time(),
            "text": text, "photos": [], "vids": [], "docs": [], "sent": 0, "fails": 0, "urgent": False,
            "status": "ok", "raw": text, "buttons": 0, "why": "ссылка на бота; призывы: переходи"}
with bot.theme_ctx(ht):
    bot.CFG["admin_chats"]["1"] = 61000
    it1, it2 = held_item(1, vid_post), held_item(2, vid_post + " 2")
    for it in (it1, it2):
        bot.hold_ad(it)
    OUT.clear()
    bot.ask_ad(it1)
    card = json.dumps(OUT[-1], ensure_ascii=False)
    assert "Это реклама?" in card and f"ap:adok:{it1['id']}" in card and f"ap:adad:{it1['id']}" in card, card
press(5001, 62000, f"ap:adok:{it1['id']}")                     # покупатель за хозяина не ответит
assert "не из твоих тем" in shown(), shown()
press(1, 61000, f"ap:adok:{it1['id']}")                        # «не реклама» — в очередь и учим
assert "не реклама — выкладываю" in shown(), shown()
with bot.theme_ctx(ht):
    assert any(x["id"] == it1["id"] and x["status"] == "ok" for x in bot.QUEUE)
    assert not any(x["id"] == it1["id"] for x in bot.ADHELD)
n_ok = bot.ad_model()["n_ok"]
press(1, 61000, f"ap:adad:{it2['id']}")                        # «реклама» — убрали и запомнили
assert "реклама — не выкладываю" in shown(), shown()
with bot.theme_ctx(ht):
    assert not any(x["id"] == it2["id"] for x in list(bot.QUEUE) + list(bot.ADHELD))
assert bot.ad_model()["n_ok"] == n_ok and bot.ad_model()["n_ad"] >= 1
press(1, 61000, f"ap:adok:{it1['id']}")                        # повторное нажатие
assert "уже обработан" in shown(), shown()
print("h29 ok: точная реклама — сама; спорный пост (видео + «в бота») — вопрос «Это реклама?», ответ учит фильтр")

# 30) подпись донора убирается, даже если она жирная, ссылкой, с «ё», эмодзи или «красивым» шрифтом
with bot.theme_ctx(ht):
    bot.CFG["old_signatures"] = ["Роблокс мемы", "roblox memes"]
    for sigline in ["**Роблокс** мемы — подпишись!", "[🎮 Роблокс  Мёмы](https://t.me/rm)",
                    "𝐑𝐨𝐛𝐥𝐨𝐱 𝐌𝐞𝐦𝐞𝐬 ✅", "Подпишись на РОБЛОКС\u00a0МЕМЫ 👉"]:
        out = bot.clean_text("Смешной мем про Роблокс 😂\n\n" + sigline, with_signature=False)
        assert "Смешной мем" in out and "мемы" not in out.lower() and "memes" not in out.lower(), (sigline, out)
    bot.CFG["old_signatures"] = []
# у нового покупателя посты выходят сами: без одобрения, пауза 20 мин
msg(7001, 77000, "/start")
nt = bot.tenant_themes(bot.tenant_by_chat(77000))[0]
with bot.theme_ctx(nt):
    assert bot.CFG["approval_on"] is False and bot.CFG["gap_min"] == 20, (bot.CFG["approval_on"], bot.CFG["gap_min"])
# пост, простоявший в очереди дольше срока, виден в журнале (а не только в консоли)
with bot.theme_ctx(ht):
    bot.QUEUE.append({"id": ht.prefix + "old:1", "ch": "oldch", "date": time.time(), "enq": time.time() - 30 * 3600,
                      "text": "x", "photos": [], "vids": [], "docs": [], "sent": 0, "fails": 0, "urgent": False,
                      "status": "ok"})
    asyncio.run(bot.publish_due(None, ht.chat_id))
    assert any("простоял в очереди" in j for j in bot.JOURNAL), list(bot.JOURNAL)[-3:]
print("h30 ok: подпись донора узнаётся в любом оформлении; покупателю — без одобрения и долгих пауз; устаревшие — в журнале")

# 31) ЮKassa: ключ с пробелами/кавычками/переносом работает; проверка ключей; при ошибке — оплата переводом
bot.yk_keys = _yk_real
os.environ["YOOKASSA_SHOP_ID"] = " 123456 "
os.environ["YOOKASSA_SECRET_KEY"] = ' "test_secret"\n'
ok, why = bot.yk_check()
assert ok and "подключена" in why and "тестовый" in why, why
press(1, 61000, "m:cltar")
assert "подключена ✅" in shown() and "Проверить ЮKassa" in shown(), shown()
os.environ["YOOKASSA_SECRET_KEY"] = "wrongkey"
ok, why = bot.yk_check()
assert not ok and "не подходят" in why and "live_" in why, why
assert "019a-test-err" in why and "поддержк" in why, why          # код ошибки — для поддержки ЮKassa
bot.TENANTS[ckey].pop("payreq", None)
buy(5001, 62000, "0:30")                                # онлайн не вышло — хозяина ни о чём не просим
assert "не открылась" in shown() and not bot.TENANTS[ckey].get("payreq"), shown()
assert any("ЮKassa не дала ссылку" in o[0] and "401" in o[0] for o in OUT[-4:]), [o[0][:60] for o in OUT[-4:]]
os.environ["YOOKASSA_SECRET_KEY"] = "test_secret"
srv.need_receipt = True                                      # в магазине включены чеки (54-ФЗ)
buy(5001, 62000, "0:30")
assert "не открылась" in shown() and any("YOOKASSA_RECEIPT_EMAIL" in o[0] for o in OUT[-4:])
os.environ["YOOKASSA_RECEIPT_EMAIL"] = "shop@example.com"
buy(5001, 62000, "0:30")
assert "yoomoney.ru/checkout" in shown(), shown()
srv.need_receipt = False
os.environ.pop("YOOKASSA_RECEIPT_EMAIL")
print("h31 ok: ЮKassa — ключи чистятся от пробелов/кавычек, проверка объясняет ошибку, при сбое — оплата переводом")

# 32) отдельная кнопка «Все покупатели»: срок, тариф, дата регистрации, кнопка в кабинет; листание по 10
press(1, 61000, "m:cl")
assert "m:cll:0" in shown() and "m:buyers" in shown(), shown()
for n in range(12):                                          # ещё покупатели, чтобы было 2 страницы
    msg(8000 + n, 88000 + n, "/start")
press(1, 61000, "m:cll:0")
txt = OUT[-1][0]
assert "Все покупатели" in txt and "стр. 1/2" in txt and "до " in txt and "с " in txt and "m:cll:1" in shown(), txt
assert f"m:clc:{ckey}" in shown() and "«Старт»" in txt, txt    # активный покупатель — сверху, с тарифом
press(1, 61000, "m:cll:1")
assert "стр. 2/2" in OUT[-1][0] and "не оплачивал" in OUT[-1][0], OUT[-1][0]
press(5001, 62000, "m:cll:0")                                # покупателю список недоступен
assert "Все покупатели" not in shown()
press(1, 61000, "m:buyers")                                  # 👥 «Покупатели»: поиск и карточки
txt = OUT[-1][0]
assert "Покупатели" in txt and f"m:buyer:{ckey}" in shown() and "Дальше" in shown() and "m:buyers:1" in shown(), txt
press(1, 61000, f"m:buyer:{ckey}")                           # карточка: ID, тариф, каналы MAX, источники
card32 = OUT[-1][0]
assert f"ID: {ckey}" in card32 and "Тариф:" in card32 and "Каналы MAX" in card32 and "Источники Telegram" in card32, card32
press(5001, 62000, "m:buyers")                               # покупателю список недоступен
assert "Покупатели:" not in shown()
print("h32 ok: «Все покупатели» — срок, тариф, листание; «👥 Покупатели» — карточка с ID, тарифом, каналами")

# 33) пробный период при «Старте»; личная ссылка на покупателя; 📌 закреп и 🗑 автоудаление
bot.GCFG["trial_days"] = 3
bot.handle_update({"update_type": "message_created", "message": {
    "sender": {"user_id": 9101, "first_name": "Лёша", "username": "lesha_max"},
    "recipient": {"chat_id": 99101, "chat_type": "dialog"}, "body": {"text": "/start"}}})
k9 = bot.tenant_by_chat(99101)
assert bot.tenant_mode(k9) == "run" and bot.TENANTS[k9]["trial"] and bot.TENANTS[k9]["plan"] == "Старт", bot.TENANTS[k9]
assert 2.9 * 86400 < bot.TENANTS[k9]["until"] - time.time() <= 3 * 86400 + 5
assert any("пробный доступ на 3 дня" in o[0] for o in OUT), [o[0][:50] for o in OUT[-4:]]
assert "Оплатить доступ" not in OUT[-1][0]                     # сразу меню, а не «оплатите»
press(1, 61000, f"m:clc:{k9}")
assert "max.ru/lesha_max" in shown() and "Написать ему лично" in shown(), shown()
press(1, 61000, f"m:clc:{ckey}")                              # у кого нет ника — честная подсказка
assert "Личной ссылки нет" in shown(), shown()
press(1, 61000, "a:cltrial")                                   # 3 -> 7 дней
assert bot.GCFG["trial_days"] == 7
bot.GCFG["trial_days"] = 0
msg(9102, 99102, "/start")                                     # выключен — без пробного, сразу бесплатный
assert bot.tenant_mode(bot.tenant_by_chat(99102)) == "free"
# закреп и автоудаление: настройка и работа после публикации
press(1, 61000, "m:home"); press(1, 61000, "m:pinset")
assert "Закреп и автоудаление" in shown() and "a:pinnew" in shown(), shown()
press(1, 61000, "a:pinnew"); press(1, 61000, "a:adh:6")
pt = bot.active_theme(1)
with bot.theme_ctx(pt):
    assert bot.CFG["pin_new"] is True and bot.CFG["autodel_h"] == 6
    pt.chat_id = pt.chat_id or 7950
    bot.record_pub("mid-A", "src", "пост", mids=["mid-A", "mid-B"])
    assert srv.pins.get(pt.chat_id) == "mid-A", srv.pins
    assert any(x["mids"] == ["mid-A", "mid-B"] for x in bot.PUB["autodel"])
bot.channel_autodel_due(now=time.time() + 7 * 3600)            # прошло 7 часов
assert "mid-A" in srv.deleted and "mid-B" in srv.deleted, srv.deleted
with bot.theme_ctx(pt):
    assert not bot.PUB["autodel"]
    bot.CFG["pin_new"] = False; bot.CFG["autodel_h"] = 0
press(5001, 62000, "m:more")                                   # закреп — в «⚙️ Настройках» (главное меню — 6 кнопок)
assert "m:pinset" in shown(), shown()                          # у покупателя кнопка тоже есть
print("h33 ok: 🎁 пробный период при «Старте»; 💬 личная ссылка на покупателя; 📌 закреп и 🗑 автоудаление постов")

# 34) личка: в нажатии кнопки message.sender — сам бот; имя/ник покупателя не затираются ботом
bot.handle_update({"update_type": "message_callback", "callback": {
    "callback_id": "c", "payload": "m:main", "user": {"user_id": 9101, "first_name": "Лёша", "username": "lesha_max"}},
    "message": {"sender": {"user_id": 777, "name": "Reloader", "username": "id5018_bot", "is_bot": True},
                "recipient": {"chat_id": 99101}}})
assert bot.TENANTS[k9]["usernames"]["9101"] == "lesha_max" and bot.TENANTS[k9]["names"]["9101"] == "Лёша", bot.TENANTS[k9]
bot.TENANTS[ckey].setdefault("usernames", {})["5001"] = "id5018_bot"     # так записывали старые версии
bot.clean_bot_names()
assert "5001" not in bot.TENANTS[ckey]["usernames"]
press(1, 61000, f"m:clc:{ckey}")
assert "_bot" not in shown() and "Личной ссылки нет" in shown(), shown()
assert not bot.contact_ok(".") and not bot.contact_ok(" @ ") and bot.contact_ok("@ivan") and bot.contact_ok("+79991234567")
press(1, 61000, "p:clcontact"); msg(1, 61000, ".")
assert any("не похоже на контакт" in o[0].lower() for o in OUT[-3:]), OUT[-3:]
assert bot.SELLER_CONTACT != "."
print("h34 ok: личка ведёт на покупателя, а не на бота; мусорный контакт «.» не принимается")

# 35) кнопки «Написать продавцу» нет — покупатели пишут в поддержку из описания
press(5001, 62000, "m:main")
assert "p:tohost" not in shown() and "Написать продавцу" not in shown(), shown()
print("h35 ok: кнопки «Написать продавцу» у покупателя нет")

# 36) бета: сначала только у тестеров; хозяин включает человеку; через неделю — у всех, если не держать
for f in bot.BETA_FEATURES:
    bot.GCFG.setdefault("beta", {}).pop(f, None)
assert not bot.feature_on("social", ckey) and bot.feature_on("social", bot.host_id())
press(1, 61000, "m:cl")
assert "m:clbeta" in shown(), shown()
press(1, 61000, f"m:clbt:{ckey}")
assert f"a:clbt:{ckey}:social" in shown(), shown()
press(1, 61000, f"a:clbt:{ckey}:social")
assert bot.feature_on("social", ckey) and not bot.feature_on("social", k9)
press(1, 61000, f"a:clbt:{ckey}:social")                       # повторно — выключил
assert not bot.feature_on("social", ckey)
press(1, 61000, "a:clbhold:stats")                             # «держать в бете»
assert bot.beta_tick(time.time() + 8 * 86400) == ["social", "ai", "mytariff"]
assert bot.feature_on("social", k9) and not bot.feature_on("stats", k9)
press(1, 61000, "a:clboff:social")
assert not bot.feature_on("social", k9) and bot.feature_on("social", bot.host_id())
for f in bot.BETA_FEATURES:                                    # дальше тестируем на кабинете-тестере
    bot.GCFG["beta"][f].update(state="beta", hold=True)
press(1, 61000, f"a:clbt:{ckey}:*")
assert all(bot.feature_on(f, ckey) for f in bot.BETA_FEATURES) and not bot.feature_on("ai", k9)
press(5001, 62000, "m:clbeta")                                 # покупателю бета-панель недоступна
assert "Бета-функции" not in OUT[-1][0]
print("h36 ok: 🧪 бета — у хозяина и тестеров; кнопка включения человеку; через неделю сама у всех; «держать» и «выключить»")

# 37) аналитика: все посты канала (и ручные), лучший день/час, картинка на 3 канала
base = time.mktime((2026, 10, 5, 0, 0, 0, 0, 0, -1))            # понедельник
hist = []
for wk in range(3):
    for dday in range(7):
        for hh in (9, 13, 19):
            ts = base + (wk * 7 + dday) * 86400 + hh * 3600
            v = 100 + (400 if hh == 19 else 0) + (300 if dday == 3 else 0)
            hist.append({"timestamp": int(ts * 1000), "body": {"mid": f"h{len(hist)}", "text": "x"}, "stat": {"views": v}})
srv.history[7700] = hist
real_time = bot.time.time
bot.time.time = lambda: base + 22 * 86400
try:
    posts = bot.fetch_channel_posts(7700)
    a = bot.analyze_posts(posts)
finally:
    bot.time.time = real_time
assert len(posts) == 18 and a["best_day"] == 3 and a["best_hour"] == 19 and a["slot"] == (3, 19), (len(posts), a["best_day"], a["best_hour"])
cap = bot.analytics_caption("Канал", a)
assert "четверг" in cap and "19:00" in cap, cap
press(5001, 62000, "m:sr")                                     # аналитика — в «📊 Статистика и Отчеты»
assert "m:anl" in shown(), shown()
press(5001, 62000, "m:anl")                                    # сначала понятное объяснение, потом кнопка
assert "Когда выкладывать" in OUT[-1][0] and "7 дней" in OUT[-1][0] and "a:anl" in shown(), OUT[-1][0]
press(5001, 62000, "a:anl")
assert "Считаю просмотры" in shown() and "7 дней" in shown(), shown()
msg(5001, 62000, "/stats")
assert any("Считаю просмотры" in o[0] for o in OUT[-2:]), OUT[-2:]
# MAX отдал ленту без просмотров — просмотры добираются по id постов; ошибки MAX объясняются словами
now_ms = int(time.time() * 1000)
srv.history[7701] = [{"timestamp": now_ms - k * 3600_000, "body": {"mid": f"nv{k}"}} for k in range(5)]
for k in range(5):
    srv.views[f"nv{k}"] = 50 + k
dg = {}
assert len(bot.fetch_channel_posts(7701, diag=dg)) == 5 and dg == {"seen": 5, "views": 5}, dg
srv.history[7702] = [{"timestamp": now_ms - k * 3600_000, "body": {"mid": f"nz{k}"}} for k in range(3)]
dg = {}
assert bot.fetch_channel_posts(7702, diag=dg) == [] and dg["seen"] == 3
assert "без просмотров" in bot.analytics_caption("К", {"n": 0, "diag": dg})
dg = {}
srv.deny_chats.add(7703)
real_get = bot.S.get
bot.S.get = lambda url, **kw: type("R", (), {"status_code": 403, "text": '{"code":"access.denied"}'})() \
    if "/messages" in url and (kw.get("params") or {}).get("chat_id") == 7703 else real_get(url, **kw)
try:
    assert bot.fetch_channel_posts(7703, diag=dg) == [] and "403" in dg["error"]
finally:
    bot.S.get = real_get
assert "не отдал посты" in bot.analytics_caption("К", {"n": 0, "diag": dg}) and "админ" in bot.analytics_caption("К", {"n": 0, "diag": dg})
OUT.clear()
real_an = bot.analyze_posts
bot.analyze_posts = lambda posts: 1 / 0                        # сбой внутри — пользователь всё равно получает ответ
bot.analytics_send(62000, [("К", 7701, [])])
bot.analyze_posts = real_an
assert any("Аналитика не получилась" in o[0] for o in OUT), OUT
OUT.clear()
bot.analytics_send(62000, [("К", 7701, [])])
assert OUT and "5 постов" in OUT[-1][0], OUT
print("h37 ok: 📊 аналитика — по всем постам канала, лучший день/час/окно, компактная картинка (3 канала на картинку)")

# 38) соцсети: ссылка на аккаунт -> источник; RSS/YouTube разбираются; посты встают в очередь
assert bot.social_parse("https://www.tiktok.com/@cool.guy")[0] == "tt~cool.guy"
assert bot.social_parse("vk.com/durov")[0] == "vk~durov"
assert bot.social_parse("https://youtube.com/@MrBeast")[0] == "yt~@MrBeast"
assert bot.social_parse("https://www.youtube.com/channel/UC1234567890123456789012")[0] == "yt~UC1234567890123456789012"
assert bot.social_parse("https://rutube.ru/channel/123456/")[0] == "rt~123456"
assert bot.social_parse("https://instagram.com/someone")[1]
assert bot.social_parse("t.me/durov") == (None, None) and bot.social_parse("@durov") == (None, None)
assert bot.social_parse("https://habr.com/ru/rss/all/")[0].startswith("rss~")
rss = """<?xml version="1.0"?><rss><channel><title>Блог</title>
<item><title>Новость один</title><link>https://site.ru/1</link><description>&lt;p&gt;Текст новости&lt;/p&gt;&lt;img src="https://site.ru/1.jpg"&gt;</description><pubDate>Mon, 05 Oct 2026 10:00:00 +0000</pubDate></item>
<item><title>Новость два</title><link>https://site.ru/2</link><description>Второй</description><pubDate>Tue, 06 Oct 2026 10:00:00 +0000</pubDate></item>
</channel></rss>"""
items = bot.parse_feed("rss~x", rss)
assert len(items) == 2 and "Текст новости" in items[0][2] and items[0][3] == ["https://site.ru/1.jpg"], items
atom = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
<entry><yt:videoId>abc</yt:videoId><title>Ролик</title><link rel="alternate" href="https://www.youtube.com/watch?v=abc"/>
<published>2026-10-06T10:00:00+00:00</published><media:group><media:title>Ролик</media:title>
<media:thumbnail url="https://i.ytimg.com/vi/abc/hqdefault.jpg"/><media:description>Описание ролика</media:description></media:group></entry></feed>"""
yt = bot.parse_feed("yt~x", atom)
assert yt[0][3] == ["https://i.ytimg.com/vi/abc/hqdefault.jpg"] and "watch?v=abc" in yt[0][2] and "Описание" in yt[0][2], yt
# покупатель-тестер добавляет TikTok и RSS по ссылке; Instagram — честный отказ
press(5001, 62000, "p:chadd")
assert "tiktok" in OUT[-1][0].lower() or "Подтверди" in OUT[-1][0] or "прав" in OUT[-1][0].lower(), OUT[-1][0]
bot.PENDING[5001] = "chadd"
bot.record_consent(5001)
msg(5001, 62000, "https://www.tiktok.com/@cool.guy https://site.ru/feed instagram.com/x")
tb = bot.active_theme(5001)
with bot.theme_ctx(tb):
    chs = list(bot.CFG["channels"])
assert "tt~cool.guy" in chs and any(c.startswith("rss~") for c in chs), chs
said = " ".join(o[0] for o in OUT[-3:])
assert "Instagram" in said and "TikTok: cool.guy" in said, said
rkey = next(c for c in chs if c.startswith("rss~"))
# чтение RSS-источника: старое не постим, новое — в очередь, фото по ссылке
feed = {"x": rss}
bot.soc_get = lambda url: feed["x"]
bot._download_url = lambda url, dest: (open(dest, "wb").write(b"\xff\xd8img" + url.encode()), dest)[1]
bot.SOCIAL_CACHE.clear()
with bot.theme_ctx(tb):
    bot.CFG["approval_on"] = False
    bot.LAST_IDS.pop(rkey, None)
    asyncio.run(bot.sync_channels(None))
    assert bot.LAST_IDS[rkey] > 0
    feed["x"] = rss.replace("</channel>", """<item><title>Свежая новость</title><link>https://site.ru/3</link>
<description>&lt;img src="https://site.ru/3.jpg"&gt; Подробности</description><pubDate>Wed, 07 Oct 2026 10:00:00 +0000</pubDate></item></channel>""")
    bot.SOCIAL_CACHE.clear()
    old_fresh = bot.FRESH_SEC; bot.FRESH_SEC = 0
    try:
        asyncio.run(bot.process_channel(bot.src_client(None, rkey), rkey, {}))
    finally:
        bot.FRESH_SEC = old_fresh
    q = [it for it in bot.QUEUE if it["ch"] == rkey]
    assert len(q) == 1 and "Свежая новость" in q[0]["text"] and len(q[0]["photos"]) == 1, q
    bot.QUEUE[:] = [it for it in bot.QUEUE if it["ch"] != rkey]
# VK: разбор ответа wall.get (реклама и репосты не берём)
class _R:
    def __init__(self, js): self._js = js
    def json(self): return self._js
real_get = bot.S.get
bot.GCFG["vk_token"] = "x" * 30
bot.S.get = lambda url, **kw: _R({"response": {"items": [
    {"id": 5, "date": 1, "text": "Пост [id1|Павел]", "attachments": [{"type": "photo", "photo": {"sizes": [
        {"width": 10, "height": 10, "url": "s"}, {"width": 800, "height": 600, "url": "big"}]}}]},
    {"id": 6, "date": 2, "text": "Реклама", "marked_as_ads": 1},
    {"id": 7, "date": 3, "text": "Репост", "copy_history": [{}]}]}}) if "vk.com" in url else real_get(url, **kw)
try:
    vk = bot.vk_posts("durov")
finally:
    bot.S.get = real_get
assert vk == [(5, 1, "Пост Павел", ["big"], None)], vk
press(1, 61000, "m:clbeta")                                     # кнопка «🔑 Ключ VK» — на месте
assert "p:clvk" in shown(), shown()
press(1, 61000, "m:clbf:social")
assert "p:clvk" in shown(), shown()
press(1, 61000, "p:clvk"); msg(1, 61000, "y" * 40)
assert bot.GCFG["vk_token"] == "y" * 40
# соцсети выключены у кабинета — источник не читается
with bot.theme_ctx(bot.tenant_themes(k9)[0]):
    bot.CFG["channels"].append("tt~someone")
    bot.LAST_IDS.pop("tt~someone", None)
    asyncio.run(bot.sync_channels(None))
    assert "tt~someone" not in bot.LAST_IDS
    bot.CFG["channels"].remove("tt~someone")
print("h38 ok: 🌐 соцсети — TikTok/VK/YouTube/Rutube/RSS по ссылке; RSS и YouTube разбираются; новые посты в очередь")

# 39) ИИ-редактор без ключей: пересказ другими словами; ссылки, призывы, КАПС и длина — на месте
raw = ("СРОЧНО! Официальный канал раздаёт подарки бесплатно — успей получить по ссылке https://t.me/gift_official "
       "и заходи в @gift_official. Это очень классный розыгрыш, игроки уже сейчас забирают новые скины.")
out = bot.ai_edit(raw)
assert out != raw and bot.ai_edit(raw) == out, out                       # другие слова; одинаково каждый раз
for keep in ("СРОЧНО", "https://t.me/gift_official", "@gift_official", "ссылке", "скины"):
    assert keep in out, (keep, out)
assert abs(len(out) - len(raw)) < 60, out                                # длину не режем
assert bot.ai_edit("ОЧЕНЬ КРУТО") .split()[0] in ("ВЕСЬМА", "КРАЙНЕ", "ОЧЕНЬ"), bot.ai_edit("ОЧЕНЬ КРУТО")
assert "https://site.ru/очень" in bot.ai_edit("Смотри https://site.ru/очень")
with bot.theme_ctx(tb):
    bot.CFG["ai_edit"] = True
    assert "https://t.me/gift_official" in bot.compose_text(raw)
    bot.CFG["ai_edit"] = False
press(5001, 62000, "m:ai")
assert "a:aied" in shown() and "a:aishort" not in shown(), shown()
print("h39 ok: 🪄 ИИ-редактор — пересказ теми же смыслом и темой; ссылки, призывы, КАПС и длина сохраняются")

# 40) свой тариф: 200 ₽ база (1 тема, 5 ист.), +100 за тему, +50 за 5 источников; оплата и лимиты
assert bot.custom_tariff(1, 5)["price"] == 200 and bot.custom_tariff(2, 10)["price"] == 350
assert bot.custom_tariff(3, 20)["price"] == 200 + 200 + 150
press(5001, 62000, "m:pay")
assert "m:myt" in shown(), shown()
press(5001, 62000, "m:myt:2x10")
assert "350 ₽ за 30 дней" in OUT[-1][0] and "py:c2x10:30" in shown(), OUT[-1][0]
os.environ["YOOKASSA_SECRET_KEY"] = ""
press(5001, 62000, "py:c2x10:30")
assert "350 ₽" in OUT[-1][0], OUT[-1][0]
press(5001, 62000, "pd:c2x10:30")
assert bot.TENANTS[ckey]["payreq"]["plan"] == "Свой: 2 тем, 10 ист.", bot.TENANTS[ckey].get("payreq")
press(1, 61000, f"a:clpayok:{ckey}")
assert bot.TENANTS[ckey]["lim_themes"] == 2 and bot.TENANTS[ckey]["lim_sources"] == 10, bot.TENANTS[ckey]
os.environ["YOOKASSA_SECRET_KEY"] = "test_secret"
press(9101, 99101, "m:pay")
assert "m:myt" not in shown()                                   # не тестер — кнопки нет (пока в бете)
print("h40 ok: 🧩 свой тариф — цена считается сама, оплата открывает выбранные лимиты")

# 41) фильтр: строки-ссылки на чужие каналы (и MAX) в любом месте; фильтры из любой темы владельца; карточка без **
tb = bot.active_theme(5001)
with bot.theme_ctx(tb):
    raw = ("**⚪️** **• фан арт по описанию! •**\n\n_Источник: Uncle Stellar_\n\n"
           "🔗 [Наш чат](https://t.me/chat_x) | [7.6](https://t.me/x/76)\n\n[🔥Genshin💧](https://max.ru/channel_genshini)")
    out = bot.clean_text(raw, with_signature=False)
    assert "max.ru" not in out and "Наш чат" not in out and "Genshin" not in out and "фан арт" in out, out
    mid = bot.clean_text("Текст\n[Канал](https://max.ru/other)\nЕщё текст", with_signature=False)
    assert "max.ru" not in mid and "Ещё текст" in mid, mid
t2 = bot.create_theme("Вторая", owner=tb.owner)
with bot.theme_ctx(t2):
    bot.CFG["old_signatures"] = ["Подпишись на ГЕНШИН"]
    bot.CFG["ad_keywords"] = ["наш магазин"]
with bot.theme_ctx(tb):
    out = bot.clean_text("Пост\n**Подпишись на Геншин!** 💧\nЗаходи в наш МАГАЗИН скинов", with_signature=False)
    assert out == "Пост", out
    card = bot.preview_text({"text": "**Жирно** [ссылка](https://x.ru)", "ch": "c", "id": "c:1"})
    assert "**" not in card and "](" not in card, card
bot.delete_theme(t2)
print("h41 ok: 🧹 фильтр — чужие ссылки (и max.ru) убираются в любом месте; подписи/реклама из любой темы работают во всех")

# 42) вложение не скачалось — пост пропускается целиком: без текста, без карточки, без кнопок и ссылок
n0 = len(OUT)
it42 = {"id": "x:1", "ch": "chan", "date": time.time(), "enq": time.time(), "text": "Новость с видео",
        "photos": [], "vids": [1], "docs": [], "sent": 0, "fails": 0, "status": "ok"}
bot.QUEUE.append(it42)
assert bot.skip_post(it42, "download failed: video (обрыв)") == "dropped" and it42 not in bot.QUEUE
assert not [o for o in OUT[n0:] if o[1]], OUT[n0:]                   # ни кнопок, ни карточки
assert any("Skipped: download failed" in j for j in bot.JOURNAL), bot.JOURNAL[-3:]
print("h42 ok: 📎 вложение не скачалось — пост пропущен целиком: без текста, без карточки и кнопок, причина в журнале")

# 47) фото не скачалось при чтении канала — пост всё равно встаёт в очередь, фото докачивается перед публикацией
feed["x"] = rss.replace("</channel>", """<item><title>Пост с фото</title><link>https://site.ru/9</link>
<description>&lt;img src="https://site.ru/9.jpg"&gt; Текст</description><pubDate>Thu, 08 Oct 2026 10:00:00 +0000</pubDate></item></channel>""")
def _net_down(url, dest):
    raise ConnectionError("сеть моргнула")
bot._download_url = _net_down
bot.SOCIAL_CACHE.clear()
with bot.theme_ctx(tb):
    bot.FRESH_SEC, old_fresh = 0, bot.FRESH_SEC
    try:
        asyncio.run(bot.process_channel(bot.src_client(None, rkey), rkey, {}))
    finally:
        bot.FRESH_SEC = old_fresh
    q = [x for x in bot.QUEUE if x["ch"] == rkey and "Пост с фото" in x["text"]]
    assert len(q) == 1 and len(q[0]["photos"]) == 1 and not os.path.exists(q[0]["photos"][0]), q
    bot._download_url = lambda url, dest: (open(dest, "wb").write(b"\xff\xd8ok"), dest)[1]
    got = asyncio.run(bot.ensure_photos(bot.src_client(None, rkey), q[0]))
    assert len(got) == 1 and os.path.exists(got[0]), got
    bot.QUEUE.remove(q[0])
print("h47 ok: фото не скачалось сразу — пост не теряется, фото докачивается перед публикацией")

# 43) ключи ЮKassa вводятся прямо в панели: сразу проверка, сообщение с ключом удаляется, секрет не показывается
os.environ["YOOKASSA_SECRET_KEY"] = "wrongkey"                 # в Bothost — старый неверный ключ
press(1, 61000, "m:cltar")
assert "p:clyk" in shown() and "shopId 123456" in OUT[-1][0] and "…gkey" in OUT[-1][0], OUT[-1][0]
press(1, 61000, "p:clyk")
srv.deleted.clear()
bot.handle_update({"update_type": "message_created", "message": {
    "sender": {"user_id": 1, "first_name": "U1"}, "recipient": {"chat_id": 61000, "chat_type": "dialog"},
    "body": {"mid": "mid-key", "text": "123456 test_secret"}}})
said = " ".join(o[0] for o in OUT[-3:])
assert "подключена" in said and "test_secret" not in said, said
assert "mid-key" in srv.deleted and bot.yk_keys() == ("123456", "test_secret")   # панель главнее старой переменной
assert "из панели бота" in bot.yk_seen(), bot.yk_seen()
buy(5001, 62000, "0:30")
assert "yoomoney.ru/checkout" in shown(), shown()
real_del = bot.delete_msg
bot.delete_msg = lambda mid: False                              # MAX не дал удалить сообщение с ключом
press(1, 61000, "p:clyk"); msg(1, 61000, "123456 test_secret")
assert any("удали его сам" in o[0] for o in OUT[-3:]), [o[0][:80] for o in OUT[-3:]]
bot.delete_msg = real_del
press(1, 61000, "p:clyk"); msg(1, 61000, "abc")
assert any("Не понял" in o[0] for o in OUT[-3:])
os.environ["YOOKASSA_SECRET_KEY"] = "test_secret"
print("h43 ok: 🔑 ключи ЮKassa — ввод в панели, сразу проверка, сообщение с ключом удаляется, видно что видит бот")

# 44) TikTok через tikwm (без входа в аккаунт): видео — прямой ссылкой, карусель — фото; оба пути сломаны — понятная причина
class _J:
    status_code = 200
    def __init__(self, js): self._js = js
    def json(self): return self._js
real_get = bot.S.get
bot.tikwm_posts = REAL_TT[1]                                   # виджет TikTok недоступен (заглушка) — берём tikwm
bot.S.get = lambda url, **kw: _J({"code": 0, "data": {"videos": [
    {"video_id": "7400000000000000001", "title": "Новый ролик", "play": "/video/media/play/1.mp4",
     "cover": "https://p.tiktok/1.jpg", "create_time": 1790000000},
    {"video_id": "7400000000000000002", "title": "Карусель", "images": ["https://p/a.jpg", "https://p/b.jpg"],
     "cover": "https://p/c.jpg", "create_time": 1790000100}]}}) if "tikwm.com" in url else real_get(url, **kw)
try:
    with bot.theme_ctx(tb):
        bot.CFG["video_on"] = True
        msgs = bot.social_fetch("tt~standoff")
finally:
    bot.S.get = real_get
vids = [m for m in msgs if m.video]
assert len(vids) == 1 and vids[0].video.url == "https://www.tikwm.com/video/media/play/1.mp4" and not vids[0].video.ytdl
assert sum(1 for m in msgs if m.photo) == 2 and any(m.text == "Новый ролик" for m in msgs), [(m.id, m.text) for m in msgs]
bot.S.get = lambda url, **kw: _J({"code": -1, "msg": "user not exist"}) if "tikwm.com" in url else real_get(url, **kw)
try:
    try:
        bot.social_fetch("tt~nobody")
        assert False
    except RuntimeError as e:
        assert "аккаунт открытый" in str(e) and "10 минут" not in str(e), e    # «пробую каждые 10 минут» добавит уведомление
finally:
    bot.S.get = real_get
    bot.tikwm_posts = _no_net
print("h44 ok: TikTok — через tikwm без входа (видео прямой ссылкой, карусели фото); иначе понятная причина")

# 45) аналитика: сразу «подожди», повторное нажатие не запускает подсчёт заново; понятное меню
bot.ANL_BUSY["5001"] = time.time()
press(5001, 62000, "a:anl")
assert "Уже считаю" in shown(), shown()
bot.ANL_BUSY.clear()
real_send = bot.analytics_send
bot.analytics_send = lambda chat, chans: time.sleep(0.2)
n0 = len(OUT)
press(5001, 62000, "a:anl")
assert any("до 1–2 минут" in o[0] for o in OUT[n0:]), [o[0][:60] for o in OUT[n0:]]
assert "5001" in bot.ANL_BUSY
time.sleep(0.5)
assert "5001" not in bot.ANL_BUSY
bot.analytics_send = real_send
press(5001, 62000, "m:main")
assert "Что делают кнопки" not in OUT[-1][0] and len(buttons_of(OUT[-1][1])) == 6, OUT[-1]   # главное меню — 6 кнопок
press(5001, 62000, "m:more")                                 # в «⚙️ Настройках» — закреп и пауза; тайника у покупателя нет
assert "m:sh" not in shown() and "a:pause" in shown() and "m:pinset" in shown(), shown()
print("h45 ok: аналитика — «подожди 1–2 минуты», без повторного запуска; главное меню — 6 кнопок, остальное в настройках")

# 46) фильтр рекламы на реальных постах: розыгрыш «подпишись на чужие каналы» и «код, успей — активации ограничены» —
#     реклама; раздача от разработчиков — нет. Правила общие для любой темы. Огрызки «Подпишись на» не остаются.
AD1 = ("❤️ Забираем камни истока за участие в ивенте! Награду заберёт каждый.\n\nКак принять участие? ⤵️\n"
       "Подпишись на @GenshinRC +1 🎟\nПодпишись на @teyvat_guids +1 🎟\n🎰 Нажми 'Крутить колесо' +1 🎟 билетик "
       "ежедневно за отметку на странице события\n\nПериод проведения:\n⌛️ С 23 октября по 5 ноября")
AD2 = ("🌠 Новый код на 3 Ящик Винса ❤️\n\nhttps://link.brawlstars.com/voucher/b84naiax-cb51c-3x1v-aace-9vun43c7sx\n\n"
       "❗️ Поторопитесь, активации ограничены ☝️")
GIFT = ("😄 Бесплатный хаосдроп\n\nПо данной ссылке вы можете забрать бесплатный Приз Хаоса в Brawl Stars от разработчиков."
        "\n\nДелимся халявой с друзьями. 😍\n\nЯщик Венди")
GIFT_LINK = GIFT.replace("По данной ссылке", "По [данной ссылке](https://link.brawlstars.com/gift/chaos)")
SHOP = "🧶 Пряжа со скидкой 30%! Промокод KNIT30 — количество ограничено, успейте забрать."
KNIT = ("🧶 Схема пледа «Соты»: понадобится 600 г пряжи и крючок №4. ВАЖНО: вяжем плотно!\n\n"
        "Подписывайтесь на наш второй канал @knit_more\nНаш чат: @knit_chat")
with bot.theme_ctx(tb):
    assert bot.ad_verdict(AD1, "genshin_news")[0] == "ad", bot.ad_verdict(AD1, "genshin_news")
    assert bot.ad_verdict(AD2, "brawl_news")[0] == "ad", bot.ad_verdict(AD2, "brawl_news")
    assert bot.ad_verdict(SHOP, "knit_news")[0] == "ad", bot.ad_verdict(SHOP, "knit_news")
    for keep in (GIFT, GIFT_LINK, KNIT):
        assert bot.ad_verdict(keep, "src")[0] == "ok", (keep[:40], bot.ad_verdict(keep, "src"))
    out = bot.clean_text(KNIT, with_signature=False)
    assert out == "🧶 Схема пледа «Соты»: понадобится 600 г пряжи и крючок №4. ВАЖНО: вяжем плотно!", out
    out = bot.clean_text(GIFT_LINK, with_signature=False)
    assert "Бесплатный хаосдроп" in out and "link.brawlstars.com/gift/chaos" in out and "Ящик Венди" in out, out
    out = bot.clean_text(AD1, with_signature=False)
    assert "Подпишись на" not in out and "Крутить колесо" in out, out           # огрызков после @канала нет
print("h46 ok: 🧹 реклама — розыгрыш с подпиской на чужие каналы и «код, успей — ограничено» отсеиваются (в любой теме);"
      " раздача от разработчиков и обычный пост остаются; строки «Подпишись на @…» убираются целиком")

# 48) «Итоги недели» — один дашборд на канал: просмотры и рост к прошлой неделе, дни, когда выкладывать, лучшие посты
now = time.time()
def _p(days_ago, hour, views, text="Пост", img=None, video=False):
    lt = time.localtime(now - days_ago * 86400)
    t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, 5, 0, 0, 0, -1))
    return {"mid": f"m{days_ago}-{hour}-{views}", "t": t, "views": views, "text": text, "img": img, "video": video}
cur = [_p(1, 19, 900, "**Обновление** 🔥 [новая карта](https://x.ru)", "https://img/1.jpg", True),
       _p(1, 19, 700), _p(2, 10, 200), _p(2, 10, 220), _p(3, 4, 50), _p(4, 15, 400), _p(4, 15, 380),
       _p(5, 21, 600, "Кейс бесплатно"), _p(6, 12, 300)]
prev = [_p(9, 12, 500), _p(10, 12, 500), _p(11, 19, 800)]
ws = bot.week_stats(cur + prev, now)
total = sum(p["views"] for p in cur)
assert ws["n"] == len(cur) and ws["total"] == total and ws["trend"] == round((total / 1800 - 1) * 100), ws["trend"]
assert len(ws["days"]) == 7 and sum(v for _, v in ws["days"]) == total, ws["days"]
assert [p["views"] for p in ws["top"]] == [900, 700, 600]
d19 = time.localtime(now - 86400).tm_wday
assert ws["slots"][0][:2] == (d19, 19) and ws["slots"][0][2] > 1.5, ws["slots"]      # 2+ поста, больше всего просмотров
assert len(ws["hours"]) == 24 and None not in ws["hours"] and ws["quiet"][1] - ws["quiet"][0] == 3, ws["quiet"]
# ниже среднего — не «лучшее время»; один удачный пост не обгоняет стабильное время с 2+ постами
assert all(x > 1 for _, _, x in ws["slots"]), ws["slots"]
noisy = [_p(1, 19, 900), _p(1, 19, 880), _p(2, 8, 1300), _p(3, 22, 100), _p(3, 22, 120), _p(4, 12, 400),
         _p(5, 12, 420), _p(6, 15, 380)]
slots = bot.week_stats(noisy, now)["slots"]
assert slots[0][:2] == (d19, 19) and all(x > 1 for _, _, x in slots) and (time.localtime(now - 3 * 86400).tm_wday, 22) \
    not in [s_[:2] for s_ in slots], slots
# неделя — 7 целых календарных дней: сумма по дням = итог, подпись дат — эти же дни
edge = cur + [_p(7, 23, 5000)]                              # 7 дней назад — это уже прошлая неделя
ws7 = bot.week_stats(edge, now)
assert ws7["total"] == total and sum(v for _, v in ws7["days"]) == total, (ws7["total"], total)
assert bot.week_range(ws7["since"], now).startswith(str(time.localtime(now - 6 * 86400).tm_mday)), bot.week_range(ws7["since"], now)
assert "Лучшее время — " + bot.WEEKDAYS_FULL[d19] in bot.analytics_caption("К", ws["a"])   # подпись = картинка
assert bot.dash_clean("**Обновление** 🔥 [новая карта](https://x.ru)") == "Обновление новая карта"
im = bot.week_dashboard("Стендофф", ws, [None, None, None])
assert im.size == (1080, 1920), im.size
assert bot.week_stats([], now)["n"] == 0
# целиком: картинка поста берётся из MAX, по картинке-дашборду на каждый канал, пустой канал — текстом
now_ms = int(now * 1000)
srv.history[7710] = [{"timestamp": now_ms - k * 5 * 3600_000, "stat": {"views": 100 + k},
                      "body": {"mid": f"d{k}", "text": f"Пост {k}",
                               "attachments": [{"type": "image", "payload": {"url": f"https://max.img/{k}.jpg"}}]}}
                     for k in range(12)]
srv.history[7711] = []
thumbs_asked, uploads = [], []
real_thumb, real_upload = bot.fetch_thumb, bot.upload_image
from PIL import Image as _Img
bot.fetch_thumb = lambda url: thumbs_asked.append(url) or _Img.new("RGB", (40, 30), (200, 50, 120))
bot.upload_image = lambda path: uploads.append(_Img.open(path).size) or "tok"
OUT.clear()
try:
    bot.analytics_send(62000, [("Канал А", 7710, []), ("Пустой", 7711, [])])
finally:
    bot.fetch_thumb, bot.upload_image = real_thumb, real_upload
assert uploads == [(1080, 1920)], uploads                                  # один дашборд — для канала с постами
assert set(thumbs_asked) == {"https://max.img/11.jpg", "https://max.img/10.jpg", "https://max.img/9.jpg"}, thumbs_asked
assert any("Канал А" in o[0] for o in OUT) and any("Пустой" in o[0] and "нет постов" in o[0] for o in OUT), OUT
print("h48 ok: 📊 «Итоги недели» — дашборд на канал: рост к прошлой неделе, дни, когда выкладывать, лучшие посты с фото")

# 49) написать покупателю лично из своего MAX: бот не может писать от имени хозяина (Bot API MAX — только от бота),
#     поэтому просит у покупателя контакт кнопкой MAX «Поделиться контактом» — телефон получает хозяин
OUT.clear()
msg(9300, 99300, "/start")
k93 = bot.tenant_by_chat(99300)
assert any("request_contact" in json.dumps(o[1]) for o in OUT), [o[0][:40] for o in OUT]
bot.handle_update({"update_type": "message_created", "message": {
    "sender": {"user_id": 9300, "first_name": "Оля"}, "recipient": {"chat_id": 99300, "chat_type": "dialog"},
    "body": {"text": "", "attachments": [{"type": "contact", "payload": {
        "vcf_info": "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Оля\r\nTEL;TYPE=cell:+7 999 123-45-67\r\nEND:VCARD",
        "max_info": {"user_id": 9300, "name": "Оля"}}}]}}})
assert bot.TENANTS[k93]["phone"] == "+79991234567", bot.TENANTS[k93]
assert any("+79991234567" in o[0] and "лично" in o[0] for o in OUT), [o[0][:60] for o in OUT]   # хозяину
assert any("Спасибо" in o[0] for o in OUT), [o[0][:60] for o in OUT]                              # покупателю
press(1, 61000, f"m:clc:{k93}")
assert "+79991234567" in OUT[-1][0], OUT[-1][0]
press(1, 61000, f"m:clc:{ckey}")                          # ни ника, ни телефона — хозяин просит контакт кнопкой
assert f"a:clask:{ckey}" in shown(), shown()
n0 = len(OUT)
press(1, 61000, f"a:clask:{ckey}")
assert any("request_contact" in json.dumps(o[1]) for o in OUT[n0:]), OUT[n0:]
assert "Попросил" in OUT[-1][0], OUT[-1][0]
press(5001, 62000, f"a:clask:{k93}")                      # покупателю это недоступно
assert bot.TENANTS[k93].get("phone") == "+79991234567"
print("h49 ok: 📇 контакт покупателя — кнопка «Поделиться контактом», телефон хозяину, «Попросить контакт» в кабинете")

# 50) зависшее чтение одного канала не останавливает чтение всех; зависший пост не держит публикацию канала;
#     обычная подпись канала «Подписывайтесь: @a | @b» — не реклама (раньше такой канал целиком уходил в «рекламу»)
real_pc, real_pi = bot.process_channel, bot._publish_item
async def _hang(*a, **kw):
    await asyncio.sleep(3600)
bot.process_channel, bot._publish_item = _hang, _hang
bot.POLL_TIMEOUT, bot.PUBLISH_TIMEOUT = 0.3, 0.3
try:
    t0 = time.monotonic()
    asyncio.run(asyncio.wait_for(bot.poll(None, tb, "hung_ch", {}, asyncio.Semaphore(1)), 5))
    it = {"id": "h:1", "ch": "hung_ch", "text": "x", "photos": [], "vids": [], "docs": [], "status": "ok", "sent": 0}
    with bot.theme_ctx(tb):
        res = asyncio.run(asyncio.wait_for(REAL_PUBLISH(None, 5, it), 5))    # настоящая, не подмена из начала тестов
        assert res == "retry", res
    assert it["retry_at"] > time.time() and time.monotonic() - t0 < 5
    assert any("зависло" in j and "hung_ch" in j for j in bot.JOURNAL), list(bot.JOURNAL)[-3:]
finally:
    bot.process_channel, bot._publish_item = real_pc, real_pi
    bot.POLL_TIMEOUT, bot.PUBLISH_TIMEOUT = 600, 2700
FOOT = "🎮 Вышел патч 0.33: новая карта и баланс оружия.\n\nПодписывайтесь: @stand_news | @stand_chat"
with bot.theme_ctx(tb):
    assert bot.ad_verdict(FOOT, "stand_news")[0] == "ok", bot.ad_verdict(FOOT, "stand_news")
    assert bot.ad_verdict(AD1, "genshin_news")[0] == "ad"                     # розыгрыш с подписками — по-прежнему реклама
    assert bot.clean_text(FOOT, with_signature=False) == "🎮 Вышел патч 0.33: новая карта и баланс оружия."
print("h50 ok: ⏱ зависший канал/пост не останавливает остальные; подпись «Подписывайтесь: @a | @b» — не реклама")

# 51) «🔎 Проверка связи» показывает, где застревают посты: ждут ✅, «Что делать?», отсеяны как реклама, подписка
now = time.time()
with bot.theme_ctx(tb):
    saved_q, saved_ads = list(bot.QUEUE), list(bot.ADHELD)
    bot.QUEUE[:] = [{"id": "p:1", "ch": "src", "status": "pending", "enq": now - 3 * 3600},
                    {"id": "p:2", "ch": "src", "status": "ok", "enq": now},
                    {"id": "p:3", "ch": "src", "status": "ok", "enq": now}]
    bot.ADHELD[:] = [{"id": "a:1", "ch": "spam_ch", "enq": now}, {"id": "a:2", "ch": "spam_ch", "enq": now}]
    lines = " | ".join(t_ for _, t_ in bot.pipeline_status(tb, now))
    bot.QUEUE[:], bot.ADHELD[:] = saved_q, saved_ads
assert "ждут твоего ✅: 1" in lines and "3 ч" in lines, lines
assert "Что делать" not in lines and "ждут решения" not in lines, lines      # статуса «ждёт файла» больше нет
assert "в очереди на выход: 2" in lines and "реклама" in lines and "@spam_ch — 2" in lines, lines
k_exp = bot.tenant_by_chat(99102)                                   # кабинет, выключенный хозяином
bot.TENANTS[k_exp]["enabled"] = False
lines = " | ".join(t_ for _, t_ in bot.pipeline_status(bot.tenant_themes(k_exp)[0], now))
bot.TENANTS[k_exp]["enabled"] = True
assert "приостановлен" in lines and "не читаются" in lines, lines
press(1, 61000, "m:dg")                                             # у покупателя проверки нет (h92), у админа — есть
assert "в очереди" in OUT[-1][0] or "последний пост" in OUT[-1][0], OUT[-1][0][-400:]
print("h51 ok: 🔎 проверка (у админа) показывает, где застряли посты: ждут ✅, реклама, нет подписки, последний пост")

# 52) у покупателя — кнопка-ссылка «💬 Написать мне лично»: открывает личный чат с хозяином в MAX (не сообщение от бота)
def link_of(rows):
    return next((b["url"] for r in rows for b in r if b.get("type") == "link" and ("лично" in b.get("text", "") or "Поддержка" in b.get("text", ""))), None)
saved_contact = bot.SELLER_CONTACT
press(1, 61000, "p:clcontact"); msg(1, 61000, "@seller_real")
press(5001, 62000, "m:main")
assert link_of(OUT[-1][1]) == "https://max.ru/seller_real", OUT[-1][1]
assert "❓ Поддержка" in json.dumps(OUT[-1][1], ensure_ascii=False), OUT[-1][1]   # кнопка поддержки — в 6-кнопочном меню
press(1, 61000, "p:clcontact"); msg(1, 61000, "https://max.ru/u/f9LHodD0cOK")
press(5001, 62000, "m:main")
assert link_of(OUT[-1][1]) == "https://max.ru/u/f9LHodD0cOK", OUT[-1][1]
press(1, 61000, "p:clcontact"); msg(1, 61000, "+79991234567")       # телефон — ссылкой не открыть: берём ник хозяина
bot.handle_update({"update_type": "message_created", "message": {
    "sender": {"user_id": 1, "first_name": "Хозяин", "username": "boss_nick"},
    "recipient": {"chat_id": 61000, "chat_type": "dialog"}, "body": {"text": "/start"}}})
press(5001, 62000, "m:main")
assert link_of(OUT[-1][1]) == "https://max.ru/boss_nick", OUT[-1][1]
press(1, 61000, "m:main")
assert link_of(OUT[-1][1]) is None                                   # самому хозяину кнопка не нужна (у него — панель)
bot.TENANTS[k_exp]["until"] = 0                                      # и без подписки покупатель может написать
msg(9102, 99102, "привет")
assert link_of(OUT[-1][1]) == "https://max.ru/boss_nick", OUT[-1]
press(1, 61000, "m:cltar")
assert "max.ru/boss_nick" in OUT[-1][0], OUT[-1][0]                 # хозяин видит, куда ведёт кнопка
bot.GCFG.pop("host_nick", None)
bot.SELLER_CONTACT = "+79991234567"
press(5001, 62000, "m:main")
assert link_of(OUT[-1][1]) is None                                   # ссылки нет — кнопки нет (не ведём «в никуда»)
bot.SELLER_CONTACT = saved_contact
print("h52 ok: 💬 «Написать мне лично» — ссылка на личный чат хозяина в MAX: из контакта (@ник/max.ru) или ника хозяина")

# 53) после обновления/перезапуска накопившиеся посты не вываливаются пачкой: «Все сейчас» — первый сейчас, остальные
#     по очереди с паузой расписания; просроченные «Сейчас» — в обычную очередь; без расписания — минимум минута между постами
out53 = []
async def pub53(client, chat_id, it):
    out53.append(it["id"]); return "ok"
real_pub53, bot.publish_item = bot.publish_item, pub53
with bot.theme_ctx(tb):
    saved_q = list(bot.QUEUE)
    saved = {k: bot.CFG[k] for k in ("gap_min", "queue_on", "night_on", "approval_on", "smart_time")}
    for k_, v_ in dict(gap_min=20, queue_on=True, night_on=False, smart_time=False).items():
        bot.CFG[k_] = v_
    now = time.time()
    def mk(i, **kw):
        return dict({"id": f"b:{i}", "ch": "src", "date": now - 600 + i, "enq": now, "text": f"пост {i}", "photos": [],
                     "vids": [], "docs": [], "status": "pending", "grp": "G53", "sent": 0}, **kw)
    bot.QUEUE[:] = [mk(i) for i in range(3)]
    bot.PUB["last_pub"] = 0
    txt, _ = bot._approve("gnow:G53", 5001)
    asyncio.run(bot.publish_due(None, tb.chat_id))
    assert out53 == ["b:0"], out53                                      # первый — сразу
    assert all(x["status"] == "ok" and not x.get("manual") for x in bot.QUEUE), bot.QUEUE
    assert "по очереди" in txt, txt
    asyncio.run(bot.publish_due(None, tb.chat_id))
    assert out53 == ["b:0"], out53                                      # остальные ждут паузу расписания
    bot.PUB["last_pub"] = time.time() - 21 * 60
    asyncio.run(bot.publish_due(None, tb.chat_id))
    assert out53 == ["b:0", "b:1"], out53                               # и выходят по одному
    out53.clear()                                                      # «Сейчас», одобренные до перезапуска (час назад)
    bot.QUEUE[:] = [mk(i, status="ok", manual=True, at=now - 3600, grp=None) for i in range(3)]
    bot.PUB["last_pub"] = 0
    asyncio.run(bot.publish_due(None, tb.chat_id))
    assert len(out53) == 1, out53
    out53.clear()                                                      # расписание выключено — всё равно по одному
    bot.CFG["queue_on"] = False
    bot.QUEUE[:] = [mk(i, status="ok", grp=None) for i in range(3)]
    bot.PUB["last_pub"] = 0
    asyncio.run(bot.publish_due(None, tb.chat_id))
    assert len(out53) == 1, out53
    bot.QUEUE[:] = saved_q
    for k_, v_ in saved.items():
        bot.CFG[k_] = v_
bot.publish_item = real_pub53
print("h53 ok: 📦 пачка после перезапуска не вываливается разом — первый сейчас, остальные по одному с паузой")

# 54) «источник не открывается» — одно сообщение; перезапуск бота (обновление) его не повторяет; напоминание — раз в сутки;
#     открылся — забыли, сломается снова — снова скажем
class _Opens:
    async def get_messages(self, ch, limit=1):
        return []


with bot.theme_ctx(tb):
    if "tt~cool.guy" not in bot.CFG["channels"]:
        bot.CFG["channels"].append("tt~cool.guy")
    bot.GCFG.pop("down_noted", None)
    n0 = len(OUT)
    said = lambda: sum("cool.guy" in o[0] and "не открывается" in o[0] for o in OUT[n0:])

    def sync(restart=True):
        if restart:
            bot.BAD.clear()                                           # после перезапуска в памяти пусто
        bot.LAST_IDS.pop("tt~cool.guy", None)
        asyncio.run(bot.sync_channels(None))

    sync()
    assert said() == 1, OUT[n0:]
    sync()
    sync()
    assert said() == 1, OUT[n0:]                                      # обновили бота дважды — не повторяет
    assert json.load(open(bot.SETTINGS_FILE)).get("down_noted"), "помним на диске, а не только в памяти"
    for k_ in bot.GCFG["down_noted"]:
        bot.GCFG["down_noted"][k_] -= 86400 + 1                       # прошли сутки, а он всё не открывается
    sync()
    assert said() == 2, OUT[n0:]
    real_src = bot.src_client
    bot.src_client = lambda client, ch: _Opens() if ch == "tt~cool.guy" else real_src(client, ch)
    try:
        sync()                                                        # открылся
    finally:
        bot.src_client = real_src
    sync()
    assert said() == 3, OUT[n0:]                                      # сломался снова — новая поломка, говорим
    bot.CFG["channels"].remove("tt~cool.guy")
print("h54 ok: 🔕 «не открывается» — одно сообщение, обновление бота его не дублирует; напоминание раз в сутки")

# 57) TikTok: сначала официальная страница-виджет TikTok (tiktok.com/embed/@имя — как в RSSHub, без входа и без
#     сторонних сервисов), с решением анти-бот задачки; ролик качается по ссылке на него. tikwm и yt-dlp — запасные
import base64 as _b64, hashlib as _hl
PFX = os.urandom(16)
CS = _b64.b64encode(json.dumps({"v": {"a": _b64.b64encode(PFX).decode(), "b": 1730000000,
                                      "c": _b64.b64encode(_hl.sha256(PFX + b"7").digest()).decode()},
                                "s": "sig"}).encode()).decode()
VID1, VID2 = str((1761000000 << 32) + 5), str((1761003600 << 32) + 9)
STATE = {"source": {"data": {"/embed/@official_standoff_2": {
    "userInfo": {"uniqueId": "official_standoff_2", "nickname": "Standoff 2", "privateAccount": False},
    "videoList": [{"id": VID2, "desc": "Новый сезон уже в игре! #so2", "coverUrl": "https://p16.tt/c2.jpg",
                   "playAddr": "https://v16.tt/v2.mp4", "authorUniqueId": "official_standoff_2"},
                  {"id": VID1, "desc": "Патч 0.33", "coverUrl": "https://p16.tt/c1.jpg", "playAddr": "",
                   "authorUniqueId": "official_standoff_2"}]}}}}
seen_tt = []
class _Page:
    def __init__(self, code, text): self.status_code, self.text = code, text
def _tt_get(url, headers=None, **kw):
    seen_tt.append((url, (headers or {}).get("Cookie", "")))
    if "tikwm" in url:
        raise ConnectionError("tikwm недоступен")
    cookie = (headers or {}).get("Cookie", "")
    if "_wafchallengeid=" not in cookie:                     # сначала TikTok даёт задачку
        return _Page(200, f'<html><p id="wci" class="_wafchallengeid"></p><p id="cs" class="{CS}"></p></html>')
    solved = json.loads(_b64.b64decode(cookie.split("_wafchallengeid=")[1].split(";")[0]))
    assert _b64.b64decode(solved["d"]) == b"7" and solved["s"] == "sig", solved
    return _Page(200, '<html><script id="__FRONTITY_CONNECT_STATE__" type="application/json">'
                      + json.dumps(STATE) + "</script></html>")
real_get57 = bot.S.get
bot.S.get = _tt_get
bot.SOCIAL_CACHE.clear()
bot.tt_embed_posts, bot.tikwm_posts = REAL_TT
try:
    with bot.theme_ctx(tb):
        bot.CFG["video_on"] = True
        msgs = bot.social_fetch("tt~official_standoff_2")
finally:
    bot.S.get = real_get57
    bot.tt_embed_posts = bot.tikwm_posts = _no_net
assert seen_tt[0][0].startswith("https://www.tiktok.com/embed/@official_standoff_2"), seen_tt
by = {m.id: m for m in msgs}
new = [m for m in msgs if m.text.startswith("Новый сезон")]
assert new and new[0].video is not None and new[0].video.url.endswith(f"/@official_standoff_2/video/{VID2}"), msgs
assert getattr(new[0].video, "ytdl", False), "ролик качаем по ссылке на него (yt-dlp), а не из списка профиля"
old_ = [m for m in msgs if m.text.startswith("Патч")]
assert old_ and old_[0].photo and old_[0].video is None, msgs              # без ролика — обложка
assert new[0].date.timestamp() == 1761003600 and new[0].id > old_[0].id
# ролик: yt-dlp не смог — запасной путь tikwm по ссылке на ролик
def _ytdl_fail(url, dest):
    raise RuntimeError("yt-dlp: TikTok просит вход")
got57 = []
real57 = (bot.ytdl_download, bot.S.get, bot._download_url)
bot.ytdl_download = _ytdl_fail
bot.S.get = lambda url, **kw: (_Page(200, json.dumps({"code": 0, "data": {"play": "/video/media/play/9.mp4"}}))
                               if "tikwm.com/api/" in url and kw.get("params", {}).get("url") == new[0].video.url
                               else _Page(404, ""))
bot._download_url = lambda url, dest: (got57.append(url), open(dest, "wb").write(b"mp4"), dest)[2]
try:
    path57 = asyncio.run(new[0].download_media(file=os.path.abspath("tt.mp4")))
finally:
    bot.ytdl_download, bot.S.get, bot._download_url = real57
assert got57 == ["https://www.tikwm.com/video/media/play/9.mp4"] and open(path57, "rb").read() == b"mp4", got57
print("h57 ok: 🎵 TikTok — официальная страница-виджет TikTok (без входа), анти-бот задачка решается; tikwm/yt-dlp — запасные")

# 56) «срочные» (слово «официально») идут без паузы расписания, но не пачкой: между постами минимум минута,
#     а после срочного обычный пост ждёт паузу расписания (раньше после перезапуска 3 срочных + обычный выходили разом)
out53.clear()
bot.publish_item = pub53
with bot.theme_ctx(tb):
    saved_q = list(bot.QUEUE)
    saved = {k: bot.CFG[k] for k in ("gap_min", "queue_on", "night_on", "smart_time", "urgent_on", "urgent_max_h")}
    for k_, v_ in dict(gap_min=20, queue_on=True, night_on=False, smart_time=False, urgent_on=True, urgent_max_h=3).items():
        bot.CFG[k_] = v_
    now = time.time()
    bot.PUB["urgent_times"] = []
    bot.QUEUE[:] = [mk(i, status="ok", grp=None, urgent=True) for i in range(3)] + [mk(9, status="ok", grp=None)]
    bot.PUB["last_pub"] = now - 3600
    asyncio.run(bot.publish_due(None, tb.chat_id))
    assert out53 == ["b:0"], out53                                      # один срочный, а не все разом
    asyncio.run(bot.publish_due(None, tb.chat_id))
    assert out53 == ["b:0"], out53                                      # минута не прошла
    bot.PUB["last_pub"] -= bot.MIN_GAP_SEC + 1
    asyncio.run(bot.publish_due(None, tb.chat_id))
    assert out53 == ["b:0", "b:1"], out53
    bot.PUB["last_pub"] -= bot.MIN_GAP_SEC + 1
    asyncio.run(bot.publish_due(None, tb.chat_id))
    bot.PUB["last_pub"] -= bot.MIN_GAP_SEC + 1
    asyncio.run(bot.publish_due(None, tb.chat_id))
    assert out53 == ["b:0", "b:1", "b:2"], out53                        # обычный b:9 ждёт паузу расписания (20 мин)
    bot.QUEUE[:] = saved_q
    for k_, v_ in saved.items():
        bot.CFG[k_] = v_
bot.publish_item = real_pub53
print("h56 ok: ⚡ срочные посты — без паузы расписания, но по одному (минута между постами), не пачкой")

# 58) бесплатный тариф: подписка (или пробный срок) кончилась — кабинет не закрывается, а работает бесплатно:
#     1 канал MAX и 1 источник; остальное на паузе (не удаляется); оплатил — всё снова работает; выключенный — молчит.
#     Лимит источников соблюдается на любом тарифе (раньше в host-режиме не проверялся вовсе)
msg(5800, 58000, "/start")
k58 = bot.tenant_by_chat(58000)
bot.record_consent(5800)
bot.host_apply_paid(k58, "Про", 30, 1500, "тест")
t58a = bot.tenant_themes(k58)[0]
t58b = bot.new_cabinet_theme(k58, "Второй канал")
t58a.chat_id, t58b.chat_id = 58100, 58200
with bot.theme_ctx(t58a):
    bot.CFG["channels"][:] = ["a1", "a2", "a3", "a4"]
with bot.theme_ctx(t58b):
    bot.CFG["channels"][:] = ["b1"]
assert bot.tenant_mode(k58) == "run" and bot.theme_live(t58a) and bot.theme_live(t58b)
bot.TENANTS[k58]["until"] = time.time() - 1                          # оплаченный срок кончился
assert bot.tenant_mode(k58) == "free"
n0 = len(OUT)
bot.host_terms_all()
told = " ".join(o[0] for o in OUT[n0:])
assert "бесплатн" in told and "на паузе" in told and "сохранены" in told, told
assert bot.theme_live(t58a) and not bot.theme_live(t58b)             # работает только первый канал MAX
asked = []
class _Src:
    async def get_messages(self, ch, limit=1, **kw):
        asked.append(ch); return []
with bot.theme_ctx(t58a):
    for ch in ("a1", "a2", "a3", "a4"):
        bot.LAST_IDS.pop(ch, None)
    asyncio.run(bot.sync_channels(_Src()))
    assert sorted(asked) == ["a1"], asked                             # читается только 1 источник
    assert bot.CFG["channels"] == ["a1", "a2", "a3", "a4"]            # остальные на паузе, не удалены
pub58 = []
async def _pub58(client, chat_id, it):
    pub58.append(chat_id); return "ok"
real_pub58, bot.publish_item = bot.publish_item, _pub58
try:
    for t_ in (t58a, t58b):
        with bot.theme_ctx(t_):
            bot.QUEUE[:] = [{"id": t_.prefix + "z:1", "ch": "a1", "date": time.time(), "enq": time.time(), "text": "p",
                             "photos": [], "vids": [], "docs": [], "sent": 0, "fails": 0, "status": "ok"}]
            bot.PUB["last_pub"] = 0
            asyncio.run(bot.publish_due(None, t_.chat_id))
finally:
    bot.publish_item = real_pub58
assert pub58 == [58100], pub58                                       # второй канал MAX на паузе
bot.META["active"][str(5800)] = t58a.id
press(5800, 58000, "p:chadd"); msg(5800, 58000, "@newsource5")
with bot.theme_ctx(t58a):
    assert "newsource5" not in bot.CFG["channels"]
assert "тариф" in " ".join(o[0] for o in OUT[-3:]), OUT[-3:]
press(5800, 58000, "m:pay")
assert "Бесплатный" in OUT[-1][0] and "навсегда" in OUT[-1][0] and "реклам" in OUT[-1][0], OUT[-1][0]
bot.host_apply_paid(k58, "Про", 30, 1500, "тест")                    # оплатил — всё снова работает
assert bot.tenant_mode(k58) == "run" and bot.theme_live(t58b)
with bot.theme_ctx(t58a):
    assert bot.live_channels() == ["a1", "a2", "a3", "a4"]
bot.host_apply_paid(k58, "Старт", 30, 300, "тест")                   # «Старт»: до 5 источников — 6-й не добавить
with bot.theme_ctx(t58a):
    bot.CFG["channels"][:] = ["a1", "a2", "a3", "a4", "a5"]
press(5800, 58000, "p:chadd"); msg(5800, 58000, "@newsource6")
with bot.theme_ctx(t58a):
    assert "newsource6" not in bot.CFG["channels"], bot.CFG["channels"]
bot.TENANTS[k58]["enabled"] = False                                  # хозяин выключил — молчит и на бесплатном
bot.TENANTS[k58]["until"] = 0
assert bot.tenant_mode(k58) == "off" and not bot.theme_live(t58a)
bot.TENANTS[k58]["enabled"] = True
print("h58 ok: 🆓 бесплатный тариф: срок кончился — работает 1 канал и 1 источник, остальное на паузе; оплатил — всё снова")

# 59) 📢 рекламная рассылка хозяина: пост (текст + картинка) с маркировкой «Реклама … erid» — бесплатным пользователям
#     в чат с ботом; платные и выключенные не получают; без erid не уходит; покупатель запустить не может
sent59 = []
real_send59 = bot.api_send
bot.api_send = lambda chat, text, rows=None, token=None: (sent59.append((chat, text or "", token)),
                                                          OUT.append((text or "", rows or [])), "m")[2]
try:
    press(5800, 58000, "p:cladpost"); msg(5800, 58000, "Купи слона")   # покупатель — не может
    assert not any("Купи слона" in t_ for _, t_, _ in sent59), sent59
    press(1, 61000, "m:cl")
    assert "p:cladpost" in shown(), shown()
    press(1, 61000, "p:cladpost")
    bot.handle_update({"update_type": "message_created", "message": {
        "sender": {"user_id": 1, "first_name": "Хозяин"}, "recipient": {"chat_id": 61000, "chat_type": "dialog"},
        "body": {"text": "🐘 Слоны со скидкой 20%!", "attachments": [
            {"type": "image", "payload": {"url": srv.url + "/files/slon.jpg", "token": "IMGTOK"}}]}}})
    msg(1, 61000, "ООО «Слоны», ИНН 7700000000")                     # без erid — не годится
    assert "erid" in OUT[-1][0], OUT[-1][0]
    msg(1, 61000, "ООО «Слоны», ИНН 7700000000, erid: 2VtzqwXyZ")
    preview = sent59[-1]
    assert preview[0] == 61000 and preview[2] == "IMGTOK", preview
    assert preview[1].startswith("🐘 Слоны") and preview[1].rstrip().endswith(
        "Реклама. ООО «Слоны», ИНН 7700000000, erid: 2VtzqwXyZ"), preview[1]
    assert "a:cladgo" in shown(), shown()
    free_keys = [k for k in bot.TENANTS if bot.tenant_mode(k) == "free"]
    paid_keys = [k for k in bot.TENANTS if bot.tenant_mode(k) == "run"]
    assert free_keys and paid_keys
    sent59.clear()
    press(1, 61000, "a:cladgo")
    for _ in range(100):
        if any("📢 Разослал" in t_ for _, t_, _ in sent59):
            break
        time.sleep(0.05)
    ads = [(c, tok) for c, t_, tok in sent59 if "Слоны со скидкой" in t_ and "erid: 2VtzqwXyZ" in t_]
    free_chats = {(bot.TENANTS[k].get("chats") or {}).get(str(bot.TENANTS[k]["admins"][0])) for k in free_keys}
    paid_chats = {(bot.TENANTS[k].get("chats") or {}).get(str(bot.TENANTS[k]["admins"][0])) for k in paid_keys}
    got_chats = {c for c, _ in ads}
    assert got_chats == {c for c in free_chats if c} and not got_chats & paid_chats, (got_chats, free_chats, paid_chats)
    assert all(tok == "IMGTOK" for _, tok in ads)
    report = next(t_ for _, t_, _ in sent59 if "📢 Разослал" in t_)
    assert str(len(got_chats)) in report, report
    log = bot.GCFG["ad_log"][-1]
    assert log["erid"] == "2VtzqwXyZ" and log["sent"] == len(got_chats), log    # для отчёта в ОРД
finally:
    bot.api_send = real_send59
print("h59 ok: 📢 реклама: пост с маркировкой «Реклама … erid» — бесплатным в чат с ботом; платные не получают; без erid нельзя")

# 60) 📌 обязательная подписка: не подписан на канал хозяина — всё закрыто, экран «📢 Подписаться на канал» (ссылка) и
#     «✅ Проверить подписку»; подписался — пускает. Хозяин добавляет/убирает каналы; канал, где бот не админ, не добавить
#     (не сможет проверять). MAX не дал проверить (бота сняли с админа) — не запираем всех, а говорим хозяину
srv.chats["reloader_news"] = -7001
srv.members[-7001] = set()
press(1, 61000, "m:cl")
assert "m:clfs" in shown(), shown()
press(1, 61000, "m:clfs")
assert "p:clfsadd" in shown(), shown()
press(1, 61000, "p:clfsadd"); msg(1, 61000, "https://max.ru/nosuchchannel")   # бота в таком канале нет
told = OUT[-2][0] + OUT[-1][0]
assert "nosuchchannel" in told and "администратор" in told, told                    # понятно, что проверить
assert "-7001" in told and "reloader_news" in told, told                            # и какие каналы бот видит (с ID)
assert bot.GCFG.get("force_subs") in (None, []), bot.GCFG.get("force_subs")
press(1, 61000, "p:clfsadd"); msg(1, 61000, "https://max.ru/Reloader_NEWS")        # как прислал хозяин: другой регистр
assert bot.GCFG["force_subs"] == [{"chat_id": -7001, "title": "reloader_news", "link": "https://max.ru/reloader_news"}], \
    (bot.GCFG.get("force_subs"), OUT[-1][0])
srv.chats["other_chan"] = -7002
srv.deny_chats.add(-7002)
press(1, 61000, "p:clfsadd"); msg(1, 61000, "https://max.ru/other_chan")
assert len(bot.GCFG["force_subs"]) == 1 and "админ" in " ".join(o[0] for o in OUT[-2:]), OUT[-2:]
msg(6100, 61100, "/start")                                           # новый, не подписан
assert "fs:check" in shown() and "https://max.ru/reloader_news" in shown() and "Подписаться" in shown(), shown()
assert bot.tenant_by_chat(61100) is None                             # кабинет — только после подписки
press(6100, 61100, "m:ch")
assert "fs:check" in shown(), shown()                                # всё закрыто
press(6100, 61100, "fs:check")
assert "fs:check" in shown() and "не вижу" in shown(), shown()
srv.members[-7001].add(6100)                                         # подписался
press(6100, 61100, "fs:check")
assert "fs:check" not in shown() and bot.tenant_by_chat(61100) is not None, shown()
msg(6100, 61100, "привет")
assert "fs:check" not in shown(), shown()
press(1, 61000, "m:cl")                                               # хозяина подписка не касается
assert "fs:check" not in shown()
srv.deny_chats.add(-7001)                                            # бота сняли с админа — проверить нельзя
n0 = len(OUT)
msg(6200, 62100, "/start")
assert "fs:check" not in shown(), shown()                            # не запираем всех из-за настройки
assert any("не могу проверить" in o[0].lower() and "reloader_news" in o[0] for o in OUT[n0:]), OUT[n0:]
srv.deny_chats.discard(-7001)
press(1, 61000, "a:clfsdel:0")
assert bot.GCFG["force_subs"] == []
print("h60 ok: 📌 обязательная подписка: не подписан — экран «Подписаться/Проверить»; подписался — пускает; хозяин — список каналов")

# 61) список чатов бота из MAX (GET /chats): ответ MAX — в журнал как есть (видно, как MAX показывает канал);
#     ссылку находим в любом виде (регистр, без https), чат любого типа; ссылки нет — по названию; убранные — нет.
#     Не нашли — хозяин видит ВСЕ чаты бота: название, ссылку, ID, тип и статус
for slug_ in ("reloader_news", "other_chan"):                 # каналы из h60 — убираем, тут свои
    srv.chats.pop(slug_, None)
srv.extra_chats[:] = [
    {"chat_id": -7101, "type": "channel", "status": "active", "title": "Reloader NEWS", "link": "https://max.ru/Reloader_NEWS"},
    {"chat_id": -7102, "type": "chat", "status": "active", "title": "Группа", "link": "max.ru/reloader_chat"},
    {"chat_id": -7103, "type": "channel", "status": "active", "title": "Bravlik_News", "link": None},
    {"chat_id": -7104, "type": "channel", "status": "removed", "title": "Old", "link": "https://max.ru/old_chan"}]
assert bot.resolve_chat_id("https://max.ru/reloader_news") == -7101
assert bot.resolve_chat_id("@Reloader_NEWS") == -7101 and bot.resolve_chat_id("max.ru/RELOADER_NEWS/") == -7101
assert bot.resolve_chat_id("https://max.ru/Reloader_chat") == -7102                # группа — тоже
assert bot.resolve_chat_id("https://max.ru/Bravlik_News") == -7103                 # ссылки нет — по названию
assert bot.resolve_chat_id("https://max.ru/old_chan") is None                      # бота из канала убрали
logged = open(bot.LOG_FILE, encoding="utf-8").read()
assert "GET /chats" in logged and '"chat_id": -7101' in logged and "https://max.ru/Reloader_NEWS" in logged, logged[-600:]
press(1, 61000, "p:clfsadd"); msg(1, 61000, "https://max.ru/no_such_one")
told = OUT[-2][0] + OUT[-1][0]
for bit in ("-7101", "Reloader NEWS", "channel", "-7104", "removed", "-7102", "chat"):
    assert bit in told, (bit, told)
srv.extra_chats[:] = []
print("h61 ok: 🔎 чаты бота из MAX: ответ MAX — в журнале; ссылка в любом виде, группа, по названию; не нашли — видно всё")

# 62) 💳 оплата через ЮKassa — без тупиков: (а) ЮKassa не дала ссылку (401, нет сети) — сразу оплата переводом:
#     реквизиты, «✅ Я оплатил», хозяину — заявка с кнопками; точный ответ ЮKassa — в журнале (bot.log);
#     (б) кнопка «🔄 Проверить оплату» под ссылкой; (в) проверка статуса не удаётся часами — не пишем «не прошла»,
#     а передаём хозяину проверить вручную
bot.GCFG["pay_info"] = "Карта 2200 0000 0000 0000, Иван И."
good_key = bot.GCFG.get("yk_key")
bot.TENANTS[ckey].pop("pay", None); bot.TENANTS[ckey].pop("payreq", None)
bot.GCFG["yk_key"] = "wrongkey"                                       # ЮKassa отвечает 401
n0 = len(OUT)
buy(5001, 62000, "0:30")
assert "не открылась" in shown() and "2200 0000 0000 0000" in shown() and "pd:0:30" in shown(), shown()
assert not bot.TENANTS[ckey].get("payreq")                            # хозяина не дёргаем, пока покупатель не оплатил
assert any("ЮKassa не дала ссылку" in o[0] and "401" in o[0] for o in OUT[n0:]), [o[0][:60] for o in OUT[n0:]]
logged = open(bot.LOG_FILE, encoding="utf-8").read()
assert "invalid_credentials" in logged and "Error in shopId or secret key" in logged, logged[-500:]
press(5001, 62000, "pd:0:30")                                         # перевёл — «Я оплатил»
assert bot.TENANTS[ckey]["payreq"]["plan"] == "Старт", bot.TENANTS[ckey].get("payreq")
assert any(f"a:clpayok:{ckey}" in json.dumps(o[1]) for o in OUT[-4:]), "хозяину — кнопка «Оплата пришла»"
until0 = bot.TENANTS[ckey]["until"]
press(1, 61000, f"a:clpayok:{ckey}")
assert bot.TENANTS[ckey]["until"] > until0 and not bot.TENANTS[ckey].get("payreq")
real_post = bot.S.post                                                 # (а) нет сети до ЮKassa — тоже перевод
bot.GCFG["yk_key"] = good_key
def _net_down62(url, *a, **kw):
    if "/v3/payments" in url:
        raise ConnectionError("ЮKassa недоступна")
    return real_post(url, *a, **kw)
bot.S.post = _net_down62
try:
    buy(5001, 62000, "0:30")
finally:
    bot.S.post = real_post
assert "не открылась" in shown() and "pd:0:30" in shown(), shown()
buy(5001, 62000, "0:30")                                         # (б) ссылка + «Проверить оплату»
assert "yoomoney.ru/checkout" in shown() and "pc:check" in shown(), shown()
pid62 = bot.TENANTS[ckey]["pay"]["pid"]
press(5001, 62000, "pc:check")
assert "ещё не пришла" in shown() and "pc:check" in shown(), shown()
srv.payments[pid62]["status"] = "succeeded"
until1 = bot.TENANTS[ckey]["until"]
press(5001, 62000, "pc:check")
assert "Оплата получена" in " ".join(o[0] for o in OUT[-3:]) and bot.TENANTS[ckey]["until"] > until1
assert "pay" not in bot.TENANTS[ckey]
buy(5001, 62000, "0:30")                                         # (в) статус не проверить: ключ сломался
bot.GCFG["yk_key"] = "wrongkey"
n0 = len(OUT)
bot.host_pay_poll()
assert bot.TENANTS[ckey].get("pay") and not any("не прошла" in o[0] for o in OUT[n0:]), OUT[n0:]
bot.TENANTS[ckey]["pay"]["t"] -= 4 * 3600                            # так — уже 4 часа
bot.host_pay_poll()
assert "pay" not in bot.TENANTS[ckey] and bot.TENANTS[ckey]["payreq"]["plan"] == "Старт", bot.TENANTS[ckey]
told = " ".join(o[0] for o in OUT[n0:])
assert "не прошла" not in told and "вручную" in told, told             # покупателю — правда, хозяину — заявка
assert any(f"a:clpayok:{ckey}" in json.dumps(o[1]) for o in OUT[n0:])
bot.GCFG["yk_key"] = good_key
bot.TENANTS[ckey].pop("payreq", None)
print("h62 ok: 💳 ЮKassa без тупиков: сбой — перевод и «Я оплатил»; «Проверить оплату»; статус не проверить — хозяину вручную")

# 55) разметка Telegram -> MAX: курсив и жирный приводим к MAX, а адреса ссылок и код не трогаем
#     (раньше «__» в адресе ссылки превращалось в «_» — ссылка вела не туда; __init__ в коде становился _init_)
fm = bot.fix_markdown
assert fm("Обычный и __курсив__ тут") == "Обычный и _курсив_ тут"
assert fm("**Новость дня **\nТекст") == "**Новость дня** \nТекст"
assert fm("**Строка 1\nСтрока 2**\nконец") == "**Строка 1**\n**Строка 2**\nконец"
for same in ("тут [ссылка](https://ex.com/a__b__c)", "**Жми [сюда](https://ex.com/__x__)**",
             "сайт https://site.com/a__b__c и всё", "код:\n```def __init__(self):\n    return **x**```\nконец",
             "команда `__init__` тут", "файл game_update_v2.apk"):
    assert fm(same) == same, (same, fm(same))
assert fm("команда `__init__` и __курсив__") == "команда `__init__` и _курсив_"
assert fm("ответ: ||секрет||") == "ответ: секрет"
for same in ("**смотри https://x.com/b** и **ещё**", "**ссылка https://x.com/a__b__c** тут", "~~https://x.com/old~~ новая"):
    assert fm(same) == same, (same, fm(same))                   # голая ссылка не «съедает» закрывающую разметку
assert fm("__курсив https://x.com/a__ и всё") == "_курсив https://x.com/a_ и всё"
print("h55 ok: ✍️ разметка Telegram -> MAX: курсив/жирный как в MAX, ссылки и код не портятся")

# 63) 📧 чек — на почту покупателя: перед ссылкой ЮKassa бот спрашивает email; верный — в чек (receipt);
#     неверный или «Пропустить» — запасная почта YOOKASSA_RECEIPT_EMAIL
EMAIL_ASK = "Напишите email (почта) для отправки чека."
os.environ["YOOKASSA_RECEIPT_EMAIL"] = "shop@example.com"
bot.GCFG["yk_key"] = "test_secret"
bot.TENANTS[ckey].pop("pay", None)
def receipt_of_last():
    pid = bot.TENANTS[ckey]["pay"]["pid"]
    return (srv.payments[pid].get("receipt") or {}).get("customer", {}).get("email")
press(5001, 62000, "py:0:30")
assert EMAIL_ASK in shown() and "pe:0:30" in shown(), shown()           # сначала почта, ссылки ещё нет
msg(5001, 62000, " Buyer@Mail.RU ")
assert "yoomoney.ru/checkout" in shown() and receipt_of_last() == "buyer@mail.ru", (shown(), receipt_of_last())
bot.TENANTS[ckey].pop("pay", None)
press(5001, 62000, "py:0:30"); msg(5001, 62000, "это не почта")
assert "yoomoney.ru/checkout" in shown() and receipt_of_last() == "shop@example.com", receipt_of_last()
bot.TENANTS[ckey].pop("pay", None)
press(5001, 62000, "py:0:30"); press(5001, 62000, "pe:0:30")           # «Пропустить»
assert "yoomoney.ru/checkout" in shown() and receipt_of_last() == "shop@example.com", receipt_of_last()
bot.TENANTS[ckey].pop("pay", None)
os.environ.pop("YOOKASSA_RECEIPT_EMAIL")
print("h63 ok: 📧 почта для чека: верная — в чек покупателю; неверная или «Пропустить» — запасная почта продавца")

# 64) ✍️ разметка и ссылки: убранное @упоминание в жирном не оставляет «****»; соседний жирный сливается;
#     непарные ** убираются; жирный из одних пробелов не склеивает слова; @ внутри ссылок и почты не вырезается
ct = lambda s_: bot.clean_text(s_, with_signature=False)
assert ct("Новый пост **@coolchannel**\nТекст") == "Новый пост\nТекст", ct("Новый пост **@coolchannel**\nТекст")
assert ct("**Важно:****новость** дня") == "**Важно:новость** дня"
assert ct("Заголовок** **конец") == "Заголовок конец"
assert ct("**Жирный без конца\nследующая строка") == "Жирный без конца\nследующая строка"
assert ct("~~~~зачёркнуто пусто") == "зачёркнуто пусто"
assert ct("Видео: https://www.youtube.com/@MrBeast/videos") == "Видео: https://www.youtube.com/@MrBeast/videos"
assert ct("Мой [тикток](https://www.tiktok.com/@artist/video/1)") == "Мой [тикток](https://www.tiktok.com/@artist/video/1)"
assert ct("Пишите: support@game.ru") == "Пишите: support@game.ru"
assert ct("Привет @news и пока") == "Привет и пока"
assert ct("**Скачать: https://site.ru/file**") == "**Скачать: https://site.ru/file**"
print("h64 ok: ✍️ без «****» и непарных звёздочек; ссылки и почта с @ целы; упоминания убираются аккуратно")

# 65) 🆓 бесплатный тариф: 1 канал MAX и 1 источник; без ИИ (пересказ, хэштеги, умное время); только текст
#     (фото/видео/файлы не качаются); не чаще поста в FREE_PLAN["gap_min"] минут; без водяного знака;
#     в меню — только основное
assert bot.FREE_PLAN["sources"] == 1 and bot.FREE_PLAN["gap_min"] == 30
msg(6500, 65000, "/start")
k65 = bot.tenant_by_chat(65000)
t65 = bot.tenant_themes(k65)[0]
t65.chat_id = 65100
assert bot.tenant_mode(k65) == "free"
with bot.theme_ctx(t65):
    bot.CFG["channels"][:] = ["firstsrc"]
    bot.CFG["ai_edit"] = bot.CFG["smart_tags"] = bot.CFG["smart_time"] = True
    assert not bot.ai_on() and bot.smart_tags("Новый скин в Roblox", "src") == ""
    assert not bot.smart_hold(time.time(), [], 3600)
    bot.CFG["signature_text"] = ""
    body = bot.compose_text("Просто пост", "src")
    assert "Reloader" not in body and "max.ru" not in body, body                     # без водяного знака
press(6500, 65000, "p:chadd"); bot.record_consent(6500); press(6500, 65000, "p:chadd"); msg(6500, 65000, "@secondsource")
with bot.theme_ctx(t65):
    assert bot.CFG["channels"] == ["firstsrc"], bot.CFG["channels"]
press(6500, 65000, "m:main")
for adv in ("m:sh", "m:pinset", "m:anl"):
    assert adv not in shown(), (adv, shown())                                      # главное меню — 6 кнопок
assert "m:th" in shown() and "m:pay" in shown()
press(6500, 65000, "m:more")                                                       # продвинутое — не в «Настройках» на бесплатном
for adv in ("m:sh", "m:pinset", "m:anl"):
    assert adv not in shown(), (adv, shown())
feed["x"] = rss.replace("</channel>", """<item><title>Пост с картинкой</title><link>https://site.ru/65</link>
<description>&lt;img src="https://site.ru/65.jpg"&gt; Текст поста</description><pubDate>Thu, 08 Oct 2026 12:00:00 +0000</pubDate></item></channel>""")
got65 = []
bot._download_url = lambda url, dest: (got65.append(url), open(dest, "wb").write(b"img"), dest)[2]
bot.SOCIAL_CACHE.clear()
with bot.theme_ctx(t65):
    bot.CFG["approval_on"] = False
    bot.CFG["channels"][:] = [rkey]
    bot.LAST_IDS[rkey] = 1
    bot.FRESH_SEC, old_fresh = 0, bot.FRESH_SEC
    try:
        asyncio.run(bot.process_channel(bot.src_client(None, rkey), rkey, {}))
    finally:
        bot.FRESH_SEC = old_fresh
    q65 = [x for x in bot.QUEUE if x["ch"] == rkey]
    assert q65 and all(not x["photos"] and not x["vids"] and not x["docs"] for x in q65), q65
    assert not got65, "у бесплатного картинки даже не качаются"
    pub65 = []
    async def _pub65(client, chat_id, it):
        pub65.append(it["id"]); return "ok"
    real65, bot.publish_item = bot.publish_item, _pub65
    try:
        bot.QUEUE[:] = [dict(q65[0], id=f"z:{i}", urgent=True) for i in range(3)]
        bot.CFG["queue_on"], bot.CFG["gap_min"] = False, 0
        bot.PUB["last_pub"] = 0
        asyncio.run(bot.publish_due(None, t65.chat_id))
        assert len(pub65) == 1, pub65                                                # одна, без «срочных» обходов
        bot.PUB["last_pub"] = time.time() - 10 * 60
        asyncio.run(bot.publish_due(None, t65.chat_id))
        assert len(pub65) == 1, pub65                                                # 10 минут — рано
        bot.PUB["last_pub"] = time.time() - 31 * 60
        asyncio.run(bot.publish_due(None, t65.chat_id))
        assert len(pub65) == 2, pub65                                                # 30 минут — следующий
    finally:
        bot.publish_item = real65
        bot.QUEUE[:] = []
print("h65 ok: 🆓 бесплатный: 1 канал и 1 источник, без ИИ, только текст, пост раз в 30 мин, без водяного знака, простое меню")

# 66) 🆓 бесплатный тариф, тонкие ограничения: фильтр рекламы работает; в очереди не больше FREE_PLAN["queue_max"]
#     (20) — старые уходят; источник проверяется раз в FREE_PLAN["check_min"] (15) минут, события не ускоряют;
#     соцсети (TikTok, VK…) — только на платном; пост с фото/видео — раз в сутки подсказка о платном тарифе
assert bot.FREE_PLAN["queue_max"] == 20 and bot.FREE_PLAN["check_min"] == 15
with bot.theme_ctx(t65):
    bot.CFG["ad_keywords"] = ["наш магазин"]
    assert bot.clean_text("Новость дня\nЗаходи в наш магазин скинов", with_signature=False) == "Новость дня"
    bot.CFG["ad_keywords"] = []
    bot.CFG["channels"][:] = ["tt~artist", "realsrc", "other"]
    assert bot.live_channels() == ["realsrc"], bot.live_channels()               # соцсеть на бесплатном не читается
    bot.CFG["channels"][:] = ["firstsrc"]
soc66, bot.beta_rec("social")["state"] = bot.beta_rec("social")["state"], "all"      # соцсети открыты всем…
press(6500, 65000, "p:chadd"); msg(6500, 65000, "https://www.tiktok.com/@artist")
bot.beta_rec("social")["state"] = soc66
with bot.theme_ctx(t65):
    assert bot.CFG["channels"] == ["firstsrc"], bot.CFG["channels"]              # …но не бесплатному
assert "на платном тарифе" in " ".join(o[0] for o in OUT[-3:]), [o[0][-120:] for o in OUT[-3:]]
bot.LAST_POLL.clear()
now66 = time.time()
assert bot.poll_due(t65, "firstsrc", False, set(), now66)                       # первый раз — сразу
assert not bot.poll_due(t65, "firstsrc", True, {"firstsrc"}, now66 + 60)         # 1 мин: ни полный круг, ни событие
assert not bot.poll_due(t65, "firstsrc", True, set(), now66 + 14 * 60)
assert bot.poll_due(t65, "firstsrc", False, set(), now66 + 15 * 60 + 1)         # 15 минут — читаем
paid66 = bot.tenant_themes(ckey)[0]
assert bot.tenant_mode(ckey) == "run"
assert bot.poll_due(paid66, "x", True, set(), now66) and bot.poll_due(paid66, "x", True, set(), now66 + 1)
assert bot.poll_due(paid66, "x", False, {"x"}, now66) and not bot.poll_due(paid66, "x", False, set(), now66)
bot.FREE_HINTS.clear()
feed["x"] = rss.replace("</channel>", """<item><title>Обзор турнира</title><link>https://site.ru/66</link>
<description>&lt;img src="https://site.ru/66.jpg"&gt; Финал прошёл вчера вечером, победила команда Альфа</description><pubDate>Thu, 08 Oct 2026 13:00:00 +0000</pubDate></item></channel>""")
with bot.theme_ctx(t65):
    bot.CFG["approval_on"] = False
    bot.CFG["channels"][:] = [rkey]
    bot.QUEUE[:] = [{"id": f"old:{i}", "ch": rkey, "date": i, "enq": time.time() - 1000 + i, "text": f"старый {i}",
                     "photos": [], "vids": [], "docs": [], "sent": 0, "fails": 0, "status": "ok"} for i in range(20)]
    bot.LAST_IDS[rkey] = 1
    bot.SOCIAL_CACHE.clear()
    n0 = len(OUT)
    bot.FRESH_SEC, old_fresh = 0, bot.FRESH_SEC
    try:
        asyncio.run(bot.process_channel(bot.src_client(None, rkey), rkey, {}))
        assert len(bot.QUEUE) == 20 and "old:0" not in [x["id"] for x in bot.QUEUE], [x["id"] for x in bot.QUEUE]
        assert any("победила команда Альфа" in x["text"] for x in bot.QUEUE)    # новый — в очереди, старый ушёл
        hint = [o[0] for o in OUT[n0:] if "платном тарифе" in o[0]]
        assert len(hint) == 1 and "фото" in hint[0], [o[0][:80] for o in OUT[n0:]]
        bot.LAST_IDS[rkey] = 1
        bot.QUEUE[:] = []
        n0 = len(OUT)
        asyncio.run(bot.process_channel(bot.src_client(None, rkey), rkey, {}))
        assert not [o for o in OUT[n0:] if "платном тарифе" in o[0]], "подсказка — не чаще раза в сутки"
    finally:
        bot.FRESH_SEC = old_fresh
        bot.QUEUE[:] = []
print("h66 ok: 🆓 бесплатный: фильтр рекламы есть, очередь до 20, проверка раз в 15 мин, без соцсетей, подсказка о платном")

# 67) переменные окружения: ключи, сохранённые в панели бота, главнее переменных хостинга (ЮKassa, VK);
#     нет ключа в панели — берём переменную; TG_PROXY из переменной главнее config.json; контакт продавца —
#     что поменяли позже; число с опечаткой в переменной не роняет бота
os.environ["YOOKASSA_SECRET_KEY"] = "test_envkey1"
assert bot.yk_keys() == ("123456", "test_secret") and "панели" in bot.yk_seen(), bot.yk_seen()
yk0 = bot.GCFG.pop("yk_shop", None), bot.GCFG.pop("yk_key", None)
assert bot.yk_keys() == ("123456", "test_envkey1") and "переменных" in bot.yk_seen(), bot.yk_seen()
bot.GCFG["yk_shop"], bot.GCFG["yk_key"] = yk0
os.environ["YOOKASSA_SECRET_KEY"] = "test_secret"
vk0 = bot.GCFG.get("vk_token")
bot.GCFG["vk_token"], os.environ["VK_TOKEN"] = "p" * 30, "e" * 30
assert bot.vk_token() == "p" * 30
bot.GCFG.pop("vk_token")
assert bot.vk_token() == "e" * 30
os.environ.pop("VK_TOKEN")
bot.GCFG["vk_token"] = vk0
assert bot.tg_proxy_url({"tg_proxy": "http://file:1"}, {"TG_PROXY": " http://env:2 "}) == "http://env:2"
assert bot.tg_proxy_url({"tg_proxy": "http://file:1"}, {}) == "http://file:1"
sc0 = bot.GCFG.get("seller_contact")
bot.panel_set("seller_contact", "@panel_nick", "@old_env")
assert bot.pick_setting("seller_contact", "@old_env") == "@panel_nick"      # переменная не менялась — из панели
assert bot.pick_setting("seller_contact", "@new_env") == "@new_env"         # переменную поменяли позже — она
bot.GCFG["env_seen"].pop("seller_contact")                                   # старые настройки: отпечатка нет
assert bot.pick_setting("seller_contact", "@new_env") == "@new_env"
assert bot.pick_setting("seller_contact", "") == "@panel_nick"
bot.GCFG["seller_contact"] = sc0
os.environ["X_TEST_INT"] = "abc"
assert bot.env_int("X_TEST_INT", 7) == 7
os.environ["X_TEST_INT"] = " 12 "
assert bot.env_int("X_TEST_INT", 7) == 12
os.environ.pop("X_TEST_INT")
print("h67 ok: ⚙️ ключи из панели главнее переменных хостинга; нет в панели — из переменной; прокси, контакт, числа")

# 68) 👤 Telegram Аккаунты (пул): хозяин присылает .session файлом — бот кладёт его в sessions/ и в пул
#     (сообщение с файлом удаляет из чата); покупатель добавил источник — аккаунт пула вступает и читает его;
#     FloodWait аккаунта — читает основной вход; в панели — статус и число каналов; удалить — кнопкой
class PoolTG:
    def __init__(self, path):
        self.name, self.reads, self.connected = os.path.basename(path)[:-len(".session")], [], False
    async def connect(self):
        self.connected = True
    async def disconnect(self):
        self.connected = False
    async def is_user_authorized(self):
        return True
    async def get_me(self):
        return types.SimpleNamespace(first_name="Акк", username=self.name)
    async def iter_dialogs(self):
        return
        yield
    async def get_messages(self, ch, limit=1, **kw):
        self.reads.append(ch)
        return []
JOINS68 = []
async def _join68(client, ch):
    JOINS68.append((client.name, ch))
bot.POOL = bot.AccountPool("sessions", "pool.json", PoolTG, join=_join68, join_gap=0)
asyncio.run(bot.POOL.start())
def send_file68(fname, url=None):
    bot.handle_update({"update_type": "message_created", "message": {
        "sender": {"user_id": 1, "first_name": "U1"}, "recipient": {"chat_id": 61000, "chat_type": "dialog"},
        "body": {"mid": "mid-" + fname, "text": "", "attachments": [
            {"type": "file", "filename": fname,
             "payload": {"url": url or srv.url + "/files/" + fname, "token": "t-" + fname}}]}}})
    return " ".join(o[0] for o in OUT[-3:])
press(1, 61000, "m:cl")
assert "m:cltgacc" in shown(), shown()
press(1, 61000, "m:cltgacc")
assert "Telegram Аккаунты" in OUT[-1][0] and "p:clsess" in shown(), OUT[-1][0]
def telethon_session_bytes(key=None):
    import sqlite3
    tmp = tempfile.mktemp(suffix=".session")
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE version (version INTEGER)")
    con.execute("CREATE TABLE sessions (dc_id INTEGER PRIMARY KEY, server_address TEXT, port INTEGER, "
                "auth_key BLOB, takeout_id INTEGER)")
    con.execute("INSERT INTO sessions VALUES (2, '149.154.167.41', 443, ?, NULL)", (key or os.urandom(256),))
    con.commit()
    con.close()
    data = open(tmp, "rb").read()
    os.remove(tmp)
    return data
srv.blobs["acc1.session"] = telethon_session_bytes()
srv.deleted.clear()
press(1, 61000, "p:clsess")
said = send_file68("acc1.session")
assert "добавлен" in said and "mid-acc1.session" in srv.deleted, said            # файл входа — из чата долой
assert os.path.exists("sessions/acc1.session") and [x["name"] for x in bot.POOL.status()] == ["acc1"]
assert oct(os.stat("sessions/acc1.session").st_mode & 0o777) == "0o600"
assert "Telegram Аккаунты" in OUT[-1][0] and "m:cltgaccdel:acc1" in shown()     # сразу список аккаунтов
srv.blobs["acc2.session"] = telethon_session_bytes()
said = send_file68("acc2.session")                                               # без «➕» — тоже в пул, не в тайник
assert "добавлен" in said and "тайник" not in said and "mid-acc2.session" in srv.deleted, said
assert [x["name"] for x in bot.POOL.status()] == ["acc1", "acc2"]
asyncio.run(bot.POOL.remove("acc2"))
for fname, blob, why in (("bad.session", b"not a session", "не файл входа"),
                         ("broken.session", b"SQLite format 3\x00" + b"\x00" * 100, "повреждён"),
                         ("copy.session", open("sessions/acc1.session", "rb").read(), "уже в пуле")):
    srv.blobs[fname] = blob
    press(1, 61000, "p:clsess")
    said = send_file68(fname)
    assert "❌" in said and why in said and not os.path.exists("sessions/" + fname), (fname, said)
    assert "mid-" + fname in srv.deleted                                         # и неподходящий файл — из чата
press(1, 61000, "p:clsess")
said = send_file68("photo.jpg")
assert ".session" in said and not os.path.exists("sessions/photo.jpg"), said
press(1, 61000, "p:clsess")
said = send_file68("gone.session", "http://127.0.0.1:9/gone.session")             # MAX не отдал файл
assert "Не смог скачать" in said and not os.path.exists("sessions/gone.session"), said[-300:]
real_del68 = bot.delete_msg
bot.delete_msg = lambda mid: False                                               # MAX не дал удалить сообщение
srv.blobs["acc3.session"] = telethon_session_bytes()
press(1, 61000, "p:clsess")
said = send_file68("acc3.session")
bot.delete_msg = real_del68
assert "добавлен" in said and "удали его сам" in said, said
asyncio.run(bot.POOL.remove("acc3"))
press(5001, 62000, "m:cltgacc")
assert "Telegram Аккаунты" not in OUT[-1][0]                                       # только хозяину

class MainTG:
    reads = []
    async def get_messages(self, ch, limit=1, **kw):
        MainTG.reads.append(ch)
        return []
main68 = MainTG()
with bot.theme_ctx(paid66):
    ch0 = list(bot.CFG["channels"])
    bot.CFG["channels"][:] = ["poolsrc"]
    bot.LAST_IDS.pop("poolsrc", None)
    async def _sync68():
        await bot.sync_channels(main68)
        await asyncio.gather(*bot.POOL_TASKS)
    asyncio.run(_sync68())
    assert JOINS68 == [("acc1", "poolsrc")] and bot.POOL.owner("poolsrc") == "acc1", JOINS68
    c68 = bot.src_client(main68, "poolsrc")
    assert isinstance(c68, PoolTG) and "poolsrc" in c68.reads and "poolsrc" not in MainTG.reads
    class FloodWaitError(Exception):
        seconds = 600
    async def _flood(ch, **kw):
        raise FloodWaitError("A wait of 600 seconds is required")
    c68.get_messages = _flood
    asyncio.run(bot.poll(main68, paid66, "poolsrc", {}, asyncio.Semaphore(5)))
    assert bot.POOL.status()[0]["status"] == "flood" and bot.src_client(main68, "poolsrc") is main68
    bot.CFG["channels"][:] = ch0
press(1, 61000, "m:cltgacc")
assert "FloodWait" in OUT[-1][0] and "каналов: 1" in OUT[-1][0] and "m:cltgaccdel:acc1" in shown(), OUT[-1][0]
press(1, 61000, "m:cltgaccdel:acc1")
assert "Удалить" in OUT[-1][0] and "a:cltgaccrm:acc1" in shown(), OUT[-1][0]
press(1, 61000, "a:cltgaccrm:acc1")
assert not os.path.exists("sessions/acc1.session") and bot.POOL.status() == [] and "удалён" in OUT[-1][0], OUT[-1][0]
print("h68 ok: 👤 Telegram Аккаунты: .session файлом в пул, вступает и читает новый источник, FloodWait, удаление")

# 69) 🔑 Ключи и доступы: хозяин сам задаёт в боте ключ VK и cookies TikTok — кнопкой, ключ присылает в чат
#     (сообщение с ним бот удаляет, в ответах — только хвост «…abcd»); ключ из панели главнее переменной Bothost;
#     «🗑 Убрать» — снова из переменной; настройки с ключами — в settings.json, доступном только боту
def send_secret69(text, mid):
    bot.handle_update({"update_type": "message_created", "message": {
        "sender": {"user_id": 1, "first_name": "U1"}, "recipient": {"chat_id": 61000, "chat_type": "dialog"},
        "body": {"mid": mid, "text": text}}})
    return " ".join(o[0] for o in OUT[-3:])
press(1, 61000, "m:cl")
assert "m:clkeys" in shown(), shown()
press(1, 61000, "m:clkeys")
keys_text = OUT[-1][0]
assert "Ключи и доступы" in keys_text and "ЮKassa" in keys_text and "VK" in keys_text and "TikTok" in keys_text
assert all(p_ in shown() for p_ in ("p:clyk", "p:clvk", "p:cltt")), shown()
assert "test_secret" not in keys_text and "y" * 40 not in keys_text, keys_text                # секреты не видны
os.environ["VK_TOKEN"], os.environ["TIKTOK_COOKIES"] = "e" * 30, "sessionid=fromenv"
srv.deleted.clear()
press(1, 61000, "p:clvk")
said = send_secret69("vk1." + "A" * 60 + "wxyz", "mid-vk")
assert "сохранён" in said and "A" * 60 not in said and "…wxyz" in said and "mid-vk" in srv.deleted, said
assert bot.vk_token() == "vk1." + "A" * 60 + "wxyz"                                       # панель главнее VK_TOKEN
press(1, 61000, "p:cltt")
said = send_secret69("sessionid=abc123secret; tt_csrf_token=zz9; msToken=mm", "mid-tt")
assert "сохранены" in said and "abc123secret" not in said and "3" in said and "mid-tt" in srv.deleted, said
assert bot.tiktok_cookies() == "sessionid=abc123secret; tt_csrf_token=zz9; msToken=mm"     # панель главнее переменной
press(1, 61000, "p:cltt")                                                                 # экспорт Cookie-Editor (JSON)
said = send_secret69('[{"name": "sessionid", "value": "q1"}, {"name": "tt_webid", "value": "w2"}]', "mid-tt2")
assert bot.tiktok_cookies() == "sessionid=q1; tt_webid=w2", bot.tiktok_cookies()
press(1, 61000, "p:cltt")
said = send_secret69("просто текст", "mid-tt3")
assert "Не похоже на cookies" in said and bot.tiktok_cookies() == "sessionid=q1; tt_webid=w2", said
press(1, 61000, "m:clkeys")
assert "…wxyz" in OUT[-1][0] and "из бота" in OUT[-1][0] and "q1" not in OUT[-1][0], OUT[-1][0]
assert "a:clkeyrm:vk" in shown() and "a:clkeyrm:tt" in shown(), shown()
assert oct(os.stat(bot.SETTINGS_FILE).st_mode & 0o777) == "0o600"                          # файл с ключами — только боту
press(1, 61000, "a:clkeyrm:tt")
assert bot.tiktok_cookies() == "sessionid=fromenv" and "переменной" in OUT[-1][0], OUT[-1][0]   # снова из Bothost
press(1, 61000, "a:clkeyrm:vk")
assert bot.vk_token() == "e" * 30
press(5001, 62000, "m:clkeys")
assert "Ключи и доступы" not in OUT[-1][0]                                                # только хозяину
# cookies TikTok в работе: без входа TikTok не отдаёт — читаем страницу профиля с cookies хозяина
bot.GCFG["tt_cookies"] = "sessionid=live1"
item69 = {"id": "7400000000000000001", "desc": "Клип с cookies", "createTime": 1761000000,
          "video": {"cover": "https://p16/c1.jpg", "playAddr": "https://v16/1.mp4"}}
page69 = ('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
          + json.dumps({"__DEFAULT_SCOPE__": {"webapp.user-detail": {"itemList": [item69]}}}) + "</script>")
seen69 = []
real_get69, real_embed69 = bot.S.get, bot.tt_embed_posts
def _get69(url, **kw):
    if "tiktok.com/@artist" in url:
        seen69.append((kw.get("headers") or {}).get("Cookie"))
        return types.SimpleNamespace(status_code=200, text=page69)
    raise AssertionError("без cookies TikTok не открывается: " + url)
def _embed_fail(user):
    raise RuntimeError("просит вход")
bot.S.get, bot.tt_embed_posts = _get69, _embed_fail
try:
    with bot.theme_ctx(paid66):
        got69 = bot.tiktok_msgs("tt~artist", "artist")
finally:
    bot.S.get, bot.tt_embed_posts = real_get69, real_embed69
assert seen69 == ["sessionid=live1"] and got69 and "Клип с cookies" in (got69[0].text or ""), (seen69, got69)
bot.GCFG.pop("tt_cookies")
os.environ.pop("VK_TOKEN"); os.environ.pop("TIKTOK_COOKIES")
print("h69 ok: 🔑 ключ VK и cookies TikTok — кнопкой в боте, сообщение удаляется, панель главнее Bothost, TikTok с cookies")

# 70) 🌐 «Для всех тем» не затирает чужие настройки: меняется ТОЛЬКО то, что поменяли. Слова фильтров —
#     добавляются/убираются, а свои слова каждой темы остаются; своя подпись темы не затирается (меняется только
#     у тем с той же подписью); число (пауза) — как задали; каналы — свои; другие кабинеты не трогаются вовсе
msg(7000, 70000, "/start")
k70 = bot.tenant_by_chat(70000)
bot.record_consent(7000)
bot.host_apply_paid(k70, "Про", 30, 1500, "тест")
A70 = bot.tenant_themes(k70)[0]
B70 = bot.new_cabinet_theme(k70, "Второй")
C70 = bot.new_cabinet_theme(k70, "Третий")
msg(7100, 71000, "/start")
k71 = bot.tenant_by_chat(71000)
D70 = bot.tenant_themes(k71)[0]                                     # чужой кабинет
FIELDS70 = ("signature_text", "signature_on", "ad_keywords", "old_signatures", "stop_words", "replacements",
            "gap_min", "channels", "video_on")
def setup70():
    for t_, sig, ad, donor, gap, ch_ in ((A70, "Подпись A", "рекламаA", "донорA", 10, "srcA"),
                                         (B70, "Подпись B", "рекламаB", "донорB", 45, "srcB"),
                                         (C70, "Подпись A", "рекламаC", "донорC", 20, "srcC"),
                                         (D70, "Подпись D", "рекламаD", "донорD", 5, "srcD")):
        with bot.theme_ctx(t_):
            bot.CFG["signature_text"], bot.CFG["signature_on"] = sig, True
            bot.CFG["ad_keywords"] = [ad, "общее"]
            bot.CFG["old_signatures"] = [donor]
            bot.CFG["stop_words"] = ["стоп" + sig[-1]]
            bot.CFG["replacements"] = [["было" + sig[-1], "стало"]]
            bot.CFG["gap_min"], bot.CFG["video_on"] = gap, True
            bot.CFG["channels"][:] = [ch_]
def snap70(t_):
    with bot.theme_ctx(t_):
        return {f: json.loads(json.dumps(bot.CFG[f])) for f in FIELDS70}
setup70()
bot.META["active"][str(7000)] = A70.id
bot.GCFG.setdefault("all_mode", {})[str(7000)] = True
assert bot.all_mode(7000)
b0, c0, d0 = snap70(B70), snap70(C70), snap70(D70)
press(7000, 70000, "p:adda"); msg(7000, 70000, "новоеслово")            # ➕ рекламное слово во всех темах
b1 = snap70(B70)
assert b1["ad_keywords"] == ["рекламаB", "общее", "новоеслово"], b1["ad_keywords"]        # своё слово B — на месте
assert {f: b1[f] for f in FIELDS70 if f != "ad_keywords"} == {f: b0[f] for f in FIELDS70 if f != "ad_keywords"}
assert snap70(D70) == d0, "чужой кабинет не трогаем"
assert "🌐" in " ".join(o[0] for o in OUT[-3:]) and "рекламные слова" in " ".join(o[0] for o in OUT[-3:])
press(7000, 70000, "p:dela"); msg(7000, 70000, "общее")                 # ➖ общее слово — уходит у всех
assert snap70(B70)["ad_keywords"] == ["рекламаB", "новоеслово"] and snap70(C70)["ad_keywords"] == ["рекламаC", "новоеслово"]
press(7000, 70000, "p:addo"); msg(7000, 70000, "донорНовый")
assert snap70(B70)["old_signatures"] == ["донорB", "донорновый"], snap70(B70)["old_signatures"]
with bot.theme_ctx(A70):                                               # любое действие меню — тот же путь
    bot.spread_to_all(7000, lambda: bot.CFG["replacements"].append(["x", "y"]))
assert snap70(B70)["replacements"] == [["былоB", "стало"], ["x", "y"]], snap70(B70)["replacements"]
with bot.theme_ctx(A70):                                               # число — во всех темах как задали
    bot.spread_to_all(7000, lambda: bot.CFG.__setitem__("gap_min", 30))
assert snap70(B70)["gap_min"] == snap70(C70)["gap_min"] == 30 and snap70(B70)["signature_text"] == "Подпись B"
press(7000, 70000, "p:sgtext"); msg(7000, 70000, "Новая подпись")          # подпись: у A и C была одна — у C
assert snap70(A70)["signature_text"] == "Новая подпись" and snap70(C70)["signature_text"] == "Новая подпись"
assert snap70(B70)["signature_text"] == "Подпись B", "своя подпись темы B не затирается"
press(7000, 70000, "a:sg")                                               # выключить подпись — у всех, текст свой
assert not snap70(B70)["signature_on"] and snap70(B70)["signature_text"] == "Подпись B"
assert snap70(B70)["channels"] == ["srcB"] and snap70(D70) == d0
bot.GCFG["all_mode"][str(7000)] = False
press(7000, 70000, "p:adda"); msg(7000, 70000, "толькоэта")             # режим выкл — только эта тема
assert "толькоэта" in snap70(A70)["ad_keywords"] and "толькоэта" not in snap70(B70)["ad_keywords"]
print("h70 ok: 🌐 «для всех тем» меняет только изменённое: свои слова и подписи тем целы, чужие кабинеты не трогает")

# 71) 🌐 кнопки: ввод (➕ слово, подпись…) — всегда «◀️ Назад» (на экран, откуда пришёл) и «❌ Отмена»; в режиме
#     «для всех тем» — предупреждение при вводе; ни один экран меню не остаётся без кнопок, чтобы уйти дальше
bot.GCFG["all_mode"][str(7000)] = True
press(7000, 70000, "m:fl")
press(7000, 70000, "p:adda")
assert "m:fl" in shown() and "◀️ Назад" in shown() and "❌ Отмена" in shown(), shown()
assert "🌐" in OUT[-1][0], OUT[-1][0]
press(7000, 70000, "m:sg")
press(7000, 70000, "p:sgtext")
assert "m:sg" in shown() and "❌ Отмена" in shown(), shown()
def crawl71(uid, chat, start):
    seen, todo, bad = set(), list(start), []
    while todo:
        pl = todo.pop()
        if pl in seen or len(seen) > 400:
            continue
        seen.add(pl)
        press(uid, chat, pl)
        text, rows = OUT[-1]
        pays = [b_.get("payload") or b_.get("url") or "" for r in rows for b_ in r]
        if not [x for x in pays if x.startswith(("m:", "tg:", "http"))]:
            bad.append((pl, text[:80]))
        if pl.startswith("p:") and not ("◀️ Назад" in json.dumps(rows, ensure_ascii=False) or "✅ Готово" in json.dumps(rows, ensure_ascii=False)):
            bad.append((pl, "ввод без «◀️ Назад»"))
        todo += [x for x in pays if x.startswith(("m:", "p:")) and x not in seen]
    return seen, bad
for uid_, chat_ in ((7000, 70000), (1, 61000)):
    seen71, bad71 = crawl71(uid_, chat_, ["m:main", "m:th", "m:more", "m:fl"])
    assert len(seen71) > 30 and not bad71, bad71
bot.GCFG["all_mode"][str(7000)] = False
PENDING_CLEAR = [bot.PENDING.pop(u_, None) for u_ in (1, 7000)]
print("h71 ok: 🌐 ввод — всегда «◀️ Назад» и «❌ Отмена», предупреждение «для всех тем»; ни одного экрана-тупика")

# ---------- 👥 карточка покупателя, 💳 тарифы с одноразовыми кодами (Playerok) ----------
from reloader.services.codes.store import CodeStore
import re as _re
from concurrent.futures import ThreadPoolExecutor

# 72) 👤 профиль покупателя: данные собраны из уже существующих записей; пустой покупатель — не падает, «—»
msg(7200, 72000, "/start")
k72 = bot.tenant_by_chat(72000)
bot.record_consent(7200)
bot.host_apply_paid(k72, "Про", 30, 1500, "ЮKassa")
bot.TENANTS[k72].setdefault("usernames", {})["7200"] = "buyer72"
A72 = bot.tenant_themes(k72)[0]
B72 = bot.new_cabinet_theme(k72, "Второй канал")
A72.chat_id, A72.link = 72100, "https://max.ru/mychan72"
B72.chat_id, B72.link = 72200, "https://max.ru/second72"
with bot.theme_ctx(A72):
    bot.CFG["channels"][:] = ["srcA1", "srcA2"]
with bot.theme_ctx(B72):
    bot.CFG["channels"][:] = ["srcB1"]
p72 = bot.buyer_profile(k72)
assert p72["id"] == "7200" and p72["username"] == "buyer72" and p72["name"], p72
assert p72["plan"] == "Про" and p72["until"] > time.time() + 29 * 86400, p72
assert p72["limits"] == {"channels": 10, "sources": 30, "check_min": 1}, p72["limits"]
assert p72["theme_names"] == ["Мой канал", "Второй канал"], p72["theme_names"]
assert p72["mx_channels"] == [{"title": "Мой канал", "link": "https://max.ru/mychan72"},
                              {"title": "Второй канал", "link": "https://max.ru/second72"}], p72["mx_channels"]
assert p72["tg_sources"] == {"Мой канал": ["srcA1", "srcA2"], "Второй канал": ["srcB1"]}, p72["tg_sources"]
pay72 = p72["payments"][-1]
assert pay72["amount"] == 1500 and pay72["provider"] == "ЮKassa" and pay72["tariff"] == "Про"
assert pay72["code"] == "—" and pay72["date"], pay72
assert "published" in p72 and "last_activity" in p72
bot.TENANTS["7299"] = {"name": "Пустой"}                                   # неполная запись — ни одного поля нет
p0 = bot.buyer_profile("7299")
assert p0["username"] == "—" and p0["plan"] == "—" and p0["payments"] == [] and p0["mx_channels"] == []
assert p0["until"] == "—" and p0["last_activity"] == "—", p0
assert bot.buyer_profile("no-such-buyer")["name"] == "—"                  # нет записи — тоже не падает
t0, r0 = bot.buyer_screen("7299", admin=True)
assert "—" in t0 and "Пустой" in t0, t0
print("h72 ok: 👤 профиль покупателя: тариф, лимиты, каналы MAX и источники, оплаты; пустые поля — «—», без падения")

# ---------- коды активации: простым сообщением, без кнопок; связь лично — только через MAX ----------
from reloader.services.codes.store import CodeStore, normalize_code, looks_like_code
import re as _re
from concurrent.futures import ThreadPoolExecutor

def links_of(rows):
    return [b_["url"] for r in rows for b_ in r if b_.get("type") == "link"]

def payloads_of(rows):
    return [b_.get("payload") for r in rows for b_ in r if b_.get("type") == "callback"]

def redeem_by_msg(uid, chat, text):
    before = len(OUT)
    msg(uid, chat, text)
    return [o[0] for o in OUT[before:]]

# 73) ✉️ связь лично: в карточке — только рабочие цели. Есть @username — ссылка https://max.ru/<username>
#     (так же, как «Написать мне лично» в боте). Нет — id и кнопка «📇 Попросить контакт». Никаких tg:// и t.me.
text73, rows73 = bot.buyer_screen(k72, admin=True)
assert links_of(rows73) == ["https://max.ru/buyer72"], rows73
text73b, rows73b = bot.buyer_screen("7299", admin=True)
assert links_of(rows73b) == [] and "7299" in text73b and "нет @username" in text73b, text73b
assert "a:buyerask:7299" in payloads_of(rows73b), rows73b
for card in (text73, text73b, json.dumps(rows73, ensure_ascii=False), json.dumps(rows73b, ensure_ascii=False)):
    assert "tg://" not in card and "t.me" not in card, card
print("h73 ok: ✉️ связь лично: @username — ссылка MAX; без @username — id и «Попросить контакт»; tg:// и t.me нигде нет")

# 74) 🎟 партия из меню: число → коды с файлом; коды — случайные, без префикса, 12 символов, буквы и цифры
codes74, path74 = bot.codes_generate(3, "Старт", 30)
assert open(path74, encoding="utf-8").read().strip().split("\n") == codes74 and len(codes74) == 3
assert all(len(c) == 12 and not c.startswith("RL") and looks_like_code(c) for c in codes74), codes74
SENT74 = []
real_send74 = bot.send_max
bot.send_max = lambda chat, text, imgs, video_path=None, file_path=None: SENT74.append((text, file_path))
try:
    press(1, 61000, "p:codegen:0"); msg(1, 61000, "4")
finally:
    bot.send_max = real_send74
assert SENT74 and len(open(SENT74[0][1], encoding="utf-8").read().strip().split("\n")) == 4, SENT74
print("h74 ok: 🎟 партия из меню: число → 12-значные коды, файл одним списком по одному в строке")

# 75) 🔑 погашение (хранилище): регистр, пробелы и дефисы не важны; код удаляется при погашении — второй раз нельзя;
#     просроченный — «истёк»; неизвестный и отозванный — «Код недействителен»; гонка 10 нажатий — ровно одна активация
now75 = time.time()
st75 = CodeStore(os.path.join(tempfile.mkdtemp(), "codes.json"))
c75 = st75.generate(6, "Старт", 30, now=now75)
status, entry, m_ = st75.redeem(c75[0], buyer="7500", now=now75)
assert status == "ok" and entry["tariff"] == "Старт" and entry["days"] == 30 and m_ == "", (status, m_)
assert st75.get(c75[0]) is None and CodeStore(st75.path).get(c75[0]) is None, "код удалён с диска"
status, _, m_ = st75.redeem(c75[0], buyer="7501", now=now75)
assert status == "invalid" and m_ == "Код недействителен", (status, m_)
raw75 = " ".join(c75[1][:4]).lower() + "-" + c75[1][4:]
assert st75.redeem(raw75, buyer="7502", now=now75)[0] == "ok"
expired75 = st75.generate(1, "Старт", 30, now=now75 - 100, expires_at=now75 - 10)[0]
status, _, m_ = st75.redeem(expired75, buyer="7503", now=now75)
assert status == "expired" and m_ == "Срок действия кода истёк", (status, m_)
assert st75.redeem("ZZZZZZZZZZZZ", buyer="7504", now=now75)[2] == "Код недействителен"
assert st75.delete(c75[2]) is True and st75.redeem(c75[2], buyer="7505", now=now75)[2] == "Код недействителен"
race75 = st75.generate(1, "Старт", 30, now=now75)[0]
with ThreadPoolExecutor(max_workers=10) as ex:
    results75 = list(ex.map(lambda i: st75.redeem(race75, buyer=str(7600 + i), now=now75)[0], range(10)))
assert results75.count("ok") == 1 and results75.count("invalid") == 9, results75
print("h75 ok: 🔑 погашение: нормализация, удаление после погашения, просрочка, неизвестный, гонка — одна активация")

# 76) 🧾 код сообщением: продлевает с текущего срока, лимиты тарифа сразу; ничего не удаляем; новые источники — нельзя
msg(7600, 76000, "/start")
k76 = bot.tenant_by_chat(76000)
bot.record_consent(7600)
bot.host_apply_paid(k76, "Про", 10, 1500, "ЮKassa")
bot.new_cabinet_theme(k76, "Второй канал")
until76 = bot.TENANTS[k76]["until"]
with bot.theme_ctx(bot.tenant_themes(k76)[0]):
    bot.CFG["channels"][:] = [f"big{i}" for i in range(6)]
pay76 = len(bot.GCFG.get("pay_log", []))
code76 = bot.codes_generate(1, "Старт", 30)[0][0]
out76 = redeem_by_msg(7600, 76000, " " + code76.lower())
assert bot.TENANTS[k76]["plan"] == "Старт" and bot.TENANTS[k76]["lim_sources"] == 5, bot.TENANTS[k76]
assert abs(bot.TENANTS[k76]["until"] - (until76 + 30 * 86400)) < 120, "продлили с текущего срока"
assert any("Код принят" in t_ and "Старт" in t_ for t_ in out76), out76
assert any("до 5" in t_ for t_ in out76), out76                                     # предупредили о превышении
assert len(bot.tenant_themes(k76)) == 2 and len(bot.GCFG["pay_log"]) == pay76 + 1
assert bot.GCFG["pay_log"][-1]["key"] == k76 and bot.GCFG["pay_log"][-1]["plan"] == "Старт"
with bot.theme_ctx(bot.tenant_themes(k76)[0]):
    assert len(bot.CFG["channels"]) == 6, "источники не удалены"
press(7600, 76000, "p:chadd"); msg(7600, 76000, "@onemore76")
with bot.theme_ctx(bot.tenant_themes(k76)[0]):
    assert "onemore76" not in bot.CFG["channels"] and len(bot.CFG["channels"]) == 6
assert "до 5" in OUT[-1][0] or "в тарифе" in OUT[-1][0], OUT[-1][0]
msg(7650, 76500, "/start")
k76b = bot.tenant_by_chat(76500)
bot.record_consent(7650)
bot.host_apply_paid(k76b, "Про", 30, 1500, "ЮKassa")
bot.TENANTS[k76b]["until"] = time.time() - 5 * 86400
code76b = bot.codes_generate(1, "Старт", 30)[0][0]
redeem_by_msg(7650, 76500, code76b)
assert abs(bot.TENANTS[k76b]["until"] - (time.time() + 30 * 86400)) < 120, "после окончания — с сегодня"
print("h76 ok: 🧾 код сообщением продлевает с текущего срока (или с сегодня), лимиты сразу; источники не удаляются")

# 77) ◀️ навигация: новые экраны ведут назад; кнопки «Ввести код» нет; обход меню хозяина и покупателя — без тупиков
press(1, 61000, "m:cl")
press(1, 61000, "m:buyers")
assert "◀️ Назад" in json.dumps(OUT[-1][1], ensure_ascii=False) and "m:cl" in json.dumps(OUT[-1][1]), OUT[-1][1]
press(1, 61000, f"m:buyer:{k72}")
assert "m:buyers" in json.dumps(OUT[-1][1]) and "◀️ Назад" in json.dumps(OUT[-1][1], ensure_ascii=False), OUT[-1][1]
press(1, 61000, "m:codes")
assert "m:cl" in json.dumps(OUT[-1][1]) and "◀️ Назад" in json.dumps(OUT[-1][1], ensure_ascii=False), OUT[-1][1]
press(1, 61000, "p:codegen:0")
assert "❌ Отмена" in shown() and "◀️ Назад" in shown() and '"m:codes"' in shown(), shown()
press(7600, 76000, "m:mprof")
assert "◀️ Назад" in shown() and "m:codes" not in shown() and "buyer:" not in shown(), shown()
press(7600, 76000, "m:pay")
assert "◀️ В меню" in shown(), shown()
def crawl77(uid, chat, start):
    seen, todo, bad = set(), list(start), []
    while todo:
        pl = todo.pop()
        if pl in seen or len(seen) > 500:
            continue
        seen.add(pl)
        press(uid, chat, pl)
        text, rows = OUT[-1]
        pays = [b_.get("payload") or b_.get("url") or "" for r in rows for b_ in r]
        if not [x for x in pays if x.startswith(("m:", "tg:", "http"))]:
            bad.append((pl, text[:80]))
        todo += [x for x in pays if x.startswith(("m:", "p:")) and x not in seen]
    return seen, bad
seen77, bad77 = crawl77(1, 61000, ["m:cl", "m:buyers", "m:codes"])
seen77b, bad77b = crawl77(7600, 76000, ["m:main", "m:mprof", "m:pay"])
assert not bad77 and not bad77b, (bad77, bad77b)
print("h77 ok: ◀️ навигация: новые экраны ведут назад, обход меню хозяина и покупателя — без тупиков")

# 78) 🔑 код простым сообщением: активация в том же ходе; код удаляется; тот же код второй раз — не активирует
msg(7800, 78000, "/start")
k78 = bot.tenant_by_chat(78000)
bot.record_consent(7800)
bot.host_apply_paid(k78, "Старт", 30, 300, "ЮKassa")
until78 = bot.TENANTS[k78]["until"]
code78 = bot.codes_generate(1, "Про", 30)[0][0]
pay78 = len(bot.GCFG["pay_log"])
out78 = redeem_by_msg(7800, 78000, code78)
assert bot.TENANTS[k78]["plan"] == "Про" and bot.TENANTS[k78]["until"] > until78 + 29 * 86400, bot.TENANTS[k78]
assert any("Код принят" in t_ for t_ in out78), out78
assert bot.CODES.get(code78) is None, "код удалён из хранилища"
assert len(bot.GCFG["pay_log"]) == pay78 + 1 and bot.GCFG["pay_log"][-1]["how"] == "код активации"
until78b = bot.TENANTS[k78]["until"]
out78b = redeem_by_msg(7800, 78000, code78)
assert any("Код недействителен" in t_ for t_ in out78b), out78b
assert bot.TENANTS[k78]["until"] == until78b and len(bot.GCFG["pay_log"]) == pay78 + 1, "второй раз — ничего"
out78c = redeem_by_msg(7800, 78000, "AbCd1234Ef56")
assert any("Код недействителен" in t_ for t_ in out78c), out78c
print("h78 ok: 🔑 код простым сообщением активирует тариф в том же ходе; код удаляется; повтор — ничего")

# 79) 🎲 коды: случайные, 12 символов, буквы и цифры, без префикса; 500 штук — все разные; ввод без регистра и пробелов
st79 = CodeStore(os.path.join(tempfile.mkdtemp(), "codes.json"))
codes79 = st79.generate(500, "Старт", 30)
assert len(set(codes79)) == 500, "уникальны"
assert all(_re.fullmatch(r"[A-Z0-9]{12}", c) and _re.search(r"[A-Z]", c) and _re.search(r"[0-9]", c) for c in codes79), codes79[:3]
assert len({c[:2] for c in codes79}) > 300, "нет общего префикса: начала кодов разные"
assert normalize_code(" " + codes79[0][:6].lower() + "-" + codes79[0][6:] + " ") == codes79[0]
assert normalize_code("привет") is None and normalize_code("abc") is None
print("h79 ok: 🎲 коды: 500 уникальных, 12 символов, буквы и цифры, без префикса; ввод без регистра и пробелов")

# 80) 🎟 админ: своя партия/код, список активных с тарифом и датой, удаление, экспорт .txt, ссылка на бота
bot.BOT_TITLE.update({"username": "reloader_bot"})
press(1, 61000, "p:codegen:0"); msg(1, 61000, "Ab12Cd34Ef56")
assert "Добавил" in OUT[-2][0], OUT[-2][0]
assert bot.CODES.get("AB12CD34EF56") is not None and bot.CODES.get("AB12CD34EF56")["tariff"] == "Старт"
press(1, 61000, "p:codegen:0"); msg(1, 61000, "abcdef")
assert "Нужно" in OUT[-2][0], OUT[-2][0]
press(1, 61000, "p:codegen:0"); msg(1, 61000, "1234567890")
assert "Нужно" in OUT[-2][0], OUT[-2][0]
press(1, 61000, "p:codegen:0"); msg(1, 61000, "Ab12Cd34Ef56")
assert "уже" in OUT[-2][0], OUT[-2][0]
press(1, 61000, "m:codes")
card80 = json.dumps(OUT[-1], ensure_ascii=False)
assert "AB12CD34EF56" in card80 and "Старт" in card80 and "https://max.ru/reloader_bot" in card80, card80[:300]
assert "a:codesexp" in card80 and "p:coderevoke" in card80
press(1, 61000, "p:coderevoke"); msg(1, 61000, "ab12 cd34-ef56")
assert bot.CODES.get("AB12CD34EF56") is None and "удалён" in OUT[-2][0], OUT[-2][0]
exp80 = bot.CODES.generate(3, "Про", 30)
SENT80 = []
real_send80 = bot.send_max
bot.send_max = lambda chat, text, imgs, video_path=None, file_path=None: SENT80.append(file_path)
try:
    press(1, 61000, "a:codesexp")
finally:
    bot.send_max = real_send80
active80 = [c for c, _, st in bot.CODES.listing() if st == "free"]
assert SENT80 and open(SENT80[-1], encoding="utf-8").read().strip().split("\n") == active80, SENT80
assert set(exp80) <= set(active80)
print("h80 ok: 🎟 админ: своя партия и код (проверка), список с тарифом и датой, удаление, экспорт .txt, ссылка на бота")

# 81) 🧩 покупатель: нет кнопки «Ввести код» и слов Playerok; подсказка есть; чужой текст — не код; код не перехватывает ввод
press(7800, 78000, "m:pay")
pay81 = json.dumps(OUT[-1], ensure_ascii=False)
assert "Ввести код" not in pay81 and "p:codeentry" not in pay81, pay81
assert "playerok" not in pay81.lower() and "ссылку пришлёт" not in pay81, pay81
assert "Чтобы активировать тариф, отправьте код сообщением" in OUT[-1][0], OUT[-1][0]
n81 = len(OUT)
msg(7800, 78000, "Спасибо, понятно")
assert not any("Код недействителен" in o[0] for o in OUT[n81:]), OUT[n81:]
code81 = bot.codes_generate(1, "Старт", 30)[0][0]
press(7800, 78000, "p:chadd")
msg(7800, 78000, code81)
assert bot.CODES.get(code81) is not None, "код не съеден вводом канала"
assert bot.TENANTS[k78]["plan"] == "Про", "тариф не сменился"
bot.CODES.delete(code81)
print("h81 ok: 🧩 покупатель: без кнопки «Ввести код» и Playerok; подсказка есть; код во время ввода — не перехватывается")

# 82) 📇 связь лично: рабочая цель для каждого случая — ссылка (username), запрос контакта (id), карточка контакта
msg(7300, 73000, "/start")
k82 = bot.tenant_by_chat(73000)
bot.record_consent(7300)
text82, rows82 = bot.buyer_screen(k82, admin=True)
assert links_of(rows82) == [] and "a:buyerask:" + k82 in payloads_of(rows82), rows82
n82 = len(OUT)
press(1, 61000, "a:buyerask:" + k82)
ask82 = [o for o in OUT[n82:] if "request_contact" in json.dumps(o[1])]
assert ask82, OUT[n82:]
assert any(b_.get("type") == "request_contact" for r in ask82[-1][1] for b_ in r), ask82[-1][1]
VCF82 = "BEGIN:VCARD\nVERSION:3.0\nFN:Иван\nTEL;TYPE=CELL:+7 900 111-22-33\nEND:VCARD"
bot.handle_update({"update_type": "message_created", "message": {
    "sender": {"user_id": 7300, "first_name": "Иван"}, "recipient": {"chat_id": 73000, "chat_type": "dialog"},
    "body": {"mid": "vcard-1", "text": "", "attachments": [{"type": "contact", "payload": {"vcf_info": VCF82}}]}}})
rec82 = bot.TENANTS[k82]
assert rec82.get("contact", {}).get("phone") == "+79001112233" and "TEL" in rec82["contact"]["vcf"], rec82.get("contact")
text82b, rows82b = bot.buyer_screen(k82, admin=True)
assert "a:buyercard:" + k82 in payloads_of(rows82b), rows82b
srv.messages.clear()
press(1, 61000, "a:buyercard:" + k82)
card_msgs = [m_ for m_ in srv.messages if (m_["body"].get("attachments") or [{}])[0].get("type") == "contact"]
assert card_msgs and card_msgs[-1]["chat_id"] == 61000, srv.messages[-2:]
assert card_msgs[-1]["body"]["attachments"][0]["payload"]["vcf_info"] == VCF82, card_msgs[-1]
text82c, rows82c = bot.buyer_screen(k72, admin=True)
assert links_of(rows82c) == ["https://max.ru/buyer72"]
print("h82 ok: 📇 связь лично: username — ссылка MAX; без username — запрос контакта кнопкой; поделился — карточка контакта в чат хозяина")

# ---------- главное меню из 6 кнопок · /admin · приветственное видео на /start ----------
from reloader import config as APP_CONFIG

# 83) 📋 главное меню покупателя — ровно 6 кнопок: 🎭 Мои каналы, 📅 Расписание, ⚙️ Настройки,
#     📊 Статистика и Отчеты, 💳 Подписка (зелёная), ❓ Поддержка — прямая ссылка на профиль продавца в MAX
BUYER6 = ["🎭 Мои каналы", "📅 Расписание", "⚙️ Настройки", "📊 Статистика и Отчеты", "💳 Подписка", "❓ Поддержка"]
contact83 = bot.SELLER_CONTACT
bot.SELLER_CONTACT = "@reloader_admin"
press(7800, 78000, "m:main")
bl83 = buttons_of(OUT[-1][1])
assert [b_["text"] for b_ in bl83] == BUYER6 and len(bl83) == 6, [b_["text"] for b_ in bl83]
by83 = {b_["text"]: b_ for b_ in bl83}
assert by83["🎭 Мои каналы"]["payload"] == "m:th" and by83["📅 Расписание"]["payload"] == "m:q"
assert by83["⚙️ Настройки"]["payload"] == "m:more" and by83["💳 Подписка"]["payload"] == "m:pay"
assert by83["📊 Статистика и Отчеты"]["payload"] == "m:sr"
assert by83["💳 Подписка"].get("intent") == "positive", by83["💳 Подписка"]          # зелёная
assert all(by83[t].get("intent") in (None, "default") for t in BUYER6 if t != "💳 Подписка"), bl83
sup83 = by83["❓ Поддержка"]
assert sup83["type"] == "link" and sup83["url"] == "https://max.ru/reloader_admin" and "payload" not in sup83, sup83
assert not {"m:st", "m:gd", "m:anl", "m:hp", "m:cl"} & {b_.get("payload") for b_ in bl83}, bl83
press(1, 61000, "m:main")                                                 # у хозяина «main» — панель кабинетов
assert "m:cll:0" in shown() and "m:bview" in shown(), shown()
press(7800, 78000, "m:more")
assert "a:pause" in json.dumps(OUT[-1][1]) and "m:sg" in json.dumps(OUT[-1][1])
bot.SELLER_CONTACT, nick83 = "@ВАШ_КОНТАКТ", bot.GCFG.pop("host_nick", None)   # контакт не задан — без мёртвой ссылки
press(7800, 78000, "m:main")
bl83b = buttons_of(OUT[-1][1])
assert [b_["text"] for b_ in bl83b] == BUYER6 and not any(b_.get("url") == "https://max.ru/None" for b_ in bl83b)
bot.SELLER_CONTACT = contact83
if nick83:
    bot.GCFG["host_nick"] = nick83
print("h83 ok: 📋 меню покупателя — 6 кнопок; «Подписка» зелёная; «Поддержка» — прямая ссылка на профиль MAX")

# 84) 👥 /admin: панель для хозяина и для ADMIN_IDS; покупатель — отказ; «Пользователи» показывают карточку
#     (ID, тариф, каналы MAX, источники Telegram)
ADMIN_BEFORE84 = bot.ADMIN_IDS
bot.ADMIN_IDS = {555}
assert APP_CONFIG.parse_ids("12, 34 ,x,56") == [12, 34, 56]
msg(555, 55500, "/admin")
p84 = buttons_of(OUT[-1][1])
assert any(b_.get("payload") == "m:buyers" and "Покупатели" in b_["text"] for b_ in p84), OUT[-1][1]
press(555, 55500, "m:buyers")
assert "7800" in json.dumps(OUT[-1][1]) or "Пользователи" in OUT[-1][0] or "Покупатели" in OUT[-1][0], OUT[-1][0]
press(555, 55500, f"m:buyer:{k78}")
card84 = OUT[-1][0]
assert f"ID: {k78}" in card84 and "Тариф:" in card84 and "Каналы MAX" in card84 and "Источники Telegram" in card84, card84
msg(7800, 78000, "/admin")
assert "только для администраторов" in OUT[-1][0] and not any(b_.get("payload") == "m:buyers" for b_ in buttons_of(OUT[-1][1])), OUT[-1]
press(1, 61000, "m:cl")
assert any(b_.get("payload") == "m:buyers" for b_ in buttons_of(OUT[-1][1])), "хозяин: кнопка «👥 Покупатели»"
bot.ADMIN_IDS = ADMIN_BEFORE84
print("h84 ok: 👥 /admin — панель хозяина и ADMIN_IDS; покупатель — отказ; «Покупатели» — карточка с ID, тарифом, каналами")

# 85) 🎬 приветственное видео на /start: сначала видео (если есть), потом меню из 6 кнопок;
#     нет файла / сбой отправки / нет сети — текст и меню всё равно
SENT85 = []
real_send85 = bot.send_max
bot.send_max = lambda chat, text, imgs, video_path=None, file_path=None: (SENT85.append((chat, text, video_path)), "m")[1]
vid85 = os.path.join(tempfile.mkdtemp(), "welcome.mp4")
open(vid85, "wb").write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
APP_CONFIG.WELCOME_VIDEO_PATH = vid85
try:
    n85 = len(OUT)
    msg(7800, 78000, "/start")
    assert SENT85 and SENT85[-1][0] == 78000 and SENT85[-1][2] == vid85, SENT85
    assert [b_["text"] for b_ in buttons_of(OUT[-1][1])] == BUYER6, OUT[-1]
    APP_CONFIG.WELCOME_VIDEO_PATH = "/no/such/welcome.mp4"
    n85b = len(SENT85)
    msg(7800, 78000, "/start")
    assert len(SENT85) == n85b, "файла нет — видео не пытаемся слать"
    assert any(o[0].startswith("👋") for o in OUT[n85:]) and [b_["text"] for b_ in buttons_of(OUT[-1][1])] == BUYER6
    APP_CONFIG.WELCOME_VIDEO_PATH = vid85
    def _boom85(*a, **k):
        raise RuntimeError("MAX не принял видео")
    bot.send_max = _boom85
    msg(7800, 78000, "/start")
    assert any(o[0].startswith("👋") for o in OUT[-4:]) and [b_["text"] for b_ in buttons_of(OUT[-1][1])] == BUYER6, OUT[-4:]
    bot.send_max = lambda chat, text, imgs, video_path=None, file_path=None: (SENT85.append((chat, text, video_path)), "m")[1]
    APP_CONFIG.WELCOME_VIDEO_PATH = ""
    APP_CONFIG.WELCOME_VIDEO_URL = "https://example.invalid/welcome.mp4"
    real_get85 = bot.S.get
    bot.S.get = lambda url, **kw: (_ for _ in ()).throw(ConnectionError("нет сети")) if "invalid" in url else real_get85(url, **kw)
    try:
        n85c = len(SENT85)
        msg(7800, 78000, "/start")
        assert len(SENT85) == n85c and [b_["text"] for b_ in buttons_of(OUT[-1][1])] == BUYER6, "ссылка не скачалась — текст и меню"
    finally:
        bot.S.get = real_get85
    class _Resp85:
        status_code = 200
        content = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
        headers = {"Content-Type": "video/mp4"}
        def raise_for_status(self):
            pass
    bot.S.get = lambda url, **kw: _Resp85() if "example.invalid" in url else real_get85(url, **kw)
    try:
        msg(7800, 78000, "/start")
        assert SENT85[-1][2] and os.path.isfile(SENT85[-1][2]), SENT85[-1]
    finally:
        bot.S.get = real_get85
    APP_CONFIG.WELCOME_VIDEO_URL = ""
    n85d = len(SENT85)
    handle_update_started = {"update_type": "bot_started", "chat_id": 78000,
                             "user": {"user_id": 7800, "first_name": "U7800"}}
    APP_CONFIG.WELCOME_VIDEO_PATH = vid85
    bot.handle_update(handle_update_started)
    assert len(SENT85) == n85d + 1 and SENT85[-1][2] == vid85, "кнопка «Старт» — то же приветствие"
finally:
    bot.send_max = real_send85
    APP_CONFIG.WELCOME_VIDEO_PATH = ""
    APP_CONFIG.WELCOME_VIDEO_URL = ""
print("h85 ok: 🎬 /start: видео, затем меню из 6 кнопок; нет файла, сбой отправки или сети — текст и меню; «Старт» — так же")

# 86) покупатель без кабинета (новый id MAX, сбой): без «Нет доступа» и кодов — кабинет заводится сразу,
#     /start — меню из 6 кнопок; меню и кнопки — только СВОЕГО кабинета (чужая тема №1 не видна и не меняется)
first = bot.THEMES[0]
with bot.theme_ctx(first):
    bot.CFG["paused"] = True                                   # чужая тема на паузе — покупатель этого не видит
try:
    OUT.clear()
    bot.TENANTS.pop("777", None)
    bot.handle_message(777, 7777, "Покупатель", "/start")
    texts = " ".join(o[0] for o in OUT)
    assert "Нет доступа" not in texts and "код" not in texts.lower(), texts[:300]
    assert bot.tenant_key(777) == "777", "кабинет заведён сразу"
    assert [b_["text"] for b_ in buttons_of(OUT[-1][1])] == BUYER6, [b_["text"] for b_ in buttons_of(OUT[-1][1])]
    assert "на паузе" not in OUT[-1][0], OUT[-1][0]             # состояние своего кабинета, не чужой темы
    OUT.clear()
    bot.TENANTS.pop("778", None)
    with bot.theme_ctx(first):
        bot.CFG["paused"] = False
    bot._handle_update({"update_type": "message_callback", "callback": {          # кнопка без кабинета
        "callback_id": "c", "payload": "a:pause", "user": {"user_id": 778, "first_name": "U778"}},
        "message": {"recipient": {"chat_id": 7778}}}, "message_callback")
    with bot.theme_ctx(first):
        assert not bot.CFG["paused"], "кнопка покупателя без кабинета не трогает чужую тему"
    assert bot.tenant_key(778) == "778", "по кнопке кабинет тоже заводится"
    texts = " ".join(o[0] for o in OUT)
    assert "Нет доступа" not in texts and "код" not in texts.lower(), texts[:300]
finally:
    with bot.theme_ctx(first):
        bot.CFG["paused"] = False
print("h86 ok: покупатель без кабинета — без гейта и кодов: кабинет сразу, /start — 6 кнопок, только свой кабинет")

# 87) администратор из ADMIN_IDS: /start и /admin — сразу панель; кнопки и ввод в панели работают (не «Нет доступа»)
OUT.clear()
bot.ADMIN_IDS = {"555"}
try:
    bot.handle_message(555, 5550, "Админ", "/start")
    panel = [b_["text"] for b_ in buttons_of(bot.screen("cl", 555)[1])]
    assert [b_["text"] for b_ in buttons_of(OUT[-1][1])] == panel and panel != BUYER6, [b_["text"] for b_ in buttons_of(OUT[-1][1])]
    OUT.clear()
    bot.handle_message(555, 5550, "Админ", "/admin")
    assert [b_["text"] for b_ in buttons_of(OUT[-1][1])] == panel, [b_["text"] for b_ in buttons_of(OUT[-1][1])]
    k87 = next(k for k in bot.TENANTS if k != "555")
    assert bot.host_action(["clask", k87], 555) is not None, "кнопки панели работают у ADMIN_IDS"
    ans87, _ = bot.apply_text("buysearch", "U100", 555)
    assert "Нет доступа" not in ans87, ans87
    assert bot.host_action(["clask", k87], 4242) is None, "не админ — панель не работает"
finally:
    bot.ADMIN_IDS = set()
print("h87 ok: администратор ADMIN_IDS — /start и /admin открывают панель; её кнопки и ввод работают")

# 88) 🛠 меню админа — как раньше (до 6/5-кнопочных меню): /start, /menu, «◀️ В меню» — панель кабинетов
#     с теми же кнопками; плюс одна новая — «👀 Посмотреть меню покупателя»
import re as _re
PANEL88 = [("👥 Все покупатели", "m:cll:0"), ("🎭 Мои каналы (мой кабинет)", "m:home"),
           ("💰 Тарифы и реквизиты", "m:cltar"), ("🎟 Промокоды", "m:clpr"), ("🧪 Бета-функции", "m:clbeta"),
           ("📊 Аналитика", "m:anl"), ("📞 Изменить мой контакт", "p:clcontact"), ("📣 Написать всем", "p:clmsg"),
           ("📢 Сделать рассылку", "p:cladpost"), ("📌 Обязательная подписка", "m:clfs"),
           ("👤 Telegram Аккаунты", "m:cltgacc"), ("🔑 Ключи и доступы", "m:clkeys"), ("👥 Покупатели", "m:buyers"),
           ("🎟 Коды", "m:codes"), ("👀 Посмотреть меню покупателя", "m:bview"),
           ("🗑 Очистить старые каналы", "m:clwipe")]
def panel_of(rows):
    return [(_re.sub(r" \(\d+\)$", "", b_["text"]), b_.get("payload")) for b_ in buttons_of(rows)
            if not (b_.get("payload") or "").startswith("m:clc:")]       # «💳 Проверить оплату» — по ситуации
assert panel_of(bot.screen("cl", 1)[1]) == PANEL88, panel_of(bot.screen("cl", 1)[1])
assert panel_of(bot.screen("main", 1)[1]) == PANEL88, "у хозяина «main» — панель кабинетов"
for cmd88 in ("/start", "/menu", "/admin"):
    OUT.clear()
    msg(1, 61000, cmd88)
    assert panel_of(OUT[-1][1]) == PANEL88, (cmd88, panel_of(OUT[-1][1]))
OUT.clear()
press(1, 61000, "m:main")                                       # «◀️ В меню» в любом экране — панель
assert panel_of(OUT[-1][1]) == PANEL88, panel_of(OUT[-1][1])
press(1, 61000, "m:home")                                       # «Мои каналы» хозяина — полное меню бота, как раньше
assert "⚙️ Меню бота" in OUT[-1][0] and "m:ch" in shown() and "m:cl" in shown(), shown()
press(1, 61000, "m:buyers")
assert OUT[-1][0].startswith("👥 Покупатели"), OUT[-1][0]
print("h88 ok: 🛠 меню админа — как раньше: панель кабинетов на /start, /menu, /admin и «В меню»; кнопки те же")

# 89) 👀 «Посмотреть меню покупателя» — ровно те кнопки, что видит покупатель, и возврат в панель
msg(9100, 91000, "/start")
buyer_rows = buttons_of(bot.screen("main", 9100)[1])
OUT.clear()
press(1, 61000, "m:bview")
pv = buttons_of(OUT[-1][1])
assert pv[:6] == buyer_rows and [b_["text"] for b_ in pv[:6]] == BUYER6, pv
assert pv[6:] and all(b_.get("payload") == "m:cl" for b_ in pv[6:]), pv[6:]
assert "покупател" in OUT[-1][0].lower(), OUT[-1][0]
press(4242, 42420, "m:bview")                                   # покупателю — не панельный экран
assert "m:cl" not in shown(), shown()
print("h89 ok: 👀 «Посмотреть меню покупателя» — те же 6 кнопок, что у покупателя, и «◀️» в панель")

# 90) 🧑 меню покупателя — ровно 6 кнопок; в настройках «🛡 Фильтр рекламы» и «✍️ Подпись под постами»,
#     без «Тайника», «Проверки каналов» и «Теста»; «📊 Статистика и Отчеты» — одна кнопка (статистика + отчёт)
contact90, bot.SELLER_CONTACT = bot.SELLER_CONTACT, "@reloader_admin"
OUT.clear()
msg(9100, 91000, "/start")
bot.SELLER_CONTACT = contact90
b90 = buttons_of(OUT[-1][1])
assert [b_["text"] for b_ in b90] == BUYER6, [b_["text"] for b_ in b90]
assert [b_.get("payload") for b_ in b90[:5]] == ["m:th", "m:q", "m:more", "m:sr", "m:pay"], b90
assert b90[4].get("intent") == "positive" and b90[5]["type"] == "link" and b90[5]["url"] == "https://max.ru/reloader_admin", b90
press(9100, 91000, "m:more")
st90 = {b_["text"]: b_.get("payload") for b_ in buttons_of(OUT[-1][1])}
assert st90.get("🛡 Фильтр рекламы") == "m:fl" and st90.get("✍️ Подпись под постами") == "m:sg", st90
assert not {"m:sh", "m:iv", "m:ts", "m:st", "m:rp"} & set(st90.values()), st90
assert "🧹 Реклама" not in st90 and "Тайник" not in OUT[-1][0] and "Проверка каналов" not in OUT[-1][0] \
    and "🧪 Тест" not in OUT[-1][0], OUT[-1][0]
press(9100, 91000, "m:sr")
assert "📊 Статистика и Отчеты" in OUT[-1][0] and "Опубликовано" in OUT[-1][0] and "Ежедневный отчёт" in OUT[-1][0], OUT[-1][0]
assert "a:rep" in shown() and "a:repnow" in shown(), shown()
press(9100, 91000, "a:rep")                                     # переключил отчёт — остаюсь на том же экране
assert "📊 Статистика и Отчеты" in OUT[-1][0], OUT[-1][0]
print("h90 ok: 🧑 меню покупателя — 6 кнопок; настройки переименованы, лишнее убрано; статистика и отчёт — вместе")

# 91) 💳 ЮKassa: ссылка на оплату — ОДНИМ сообщением (и по кнопке тарифа, и после почты для чека)
bot.GCFG["yk_key"] = "test_secret"
def pay_msgs(since):
    return [o for o in OUT[since:] if "yoomoney.ru/checkout" in json.dumps(o[1])]
bot.TENANTS[bot.tenant_key(9100)].pop("pay", None)
n91 = len(OUT)
press(9100, 91000, "pk:0:30")
assert len(pay_msgs(n91)) == 1, OUT[n91:]
bot.TENANTS[bot.tenant_key(9100)].pop("pay", None)
n91 = len(OUT)
press(9100, 91000, "py:0:30")
if "pe:0:30" in shown():
    msg(9100, 91000, "buyer91@mail.ru")
assert len(pay_msgs(n91)) == 1, OUT[n91:]
bot.TENANTS[bot.tenant_key(9100)].pop("pay", None)
print("h91 ok: 💳 ЮKassa — ссылка на оплату приходит одним сообщением, без дубля")

# 92) 🔎 «Проверка связи» у покупателя убрана совсем: в «Настройках», в «Журнале», в инструкции; старая кнопка
#     ведёт в меню; у админа (полное меню «Мои каналы») — как было
msg(9300, 93000, "/start")
assert [b_["text"] for b_ in buttons_of(OUT[-1][1])] == BUYER6, OUT[-1][1]          # меню покупателя — то же
press(9300, 93000, "m:more")
assert "m:dg" not in shown() and "Проверка связи" not in shown(), shown()
press(9300, 93000, "m:lg")
assert "m:dg" not in shown(), shown()
press(9300, 93000, "m:gd")
assert "Проверка связи" not in OUT[-1][0], OUT[-1][0]
press(9300, 93000, "m:dg")                                       # старая кнопка в чате — меню, а не проверка
assert [b_["text"] for b_ in buttons_of(OUT[-1][1])] == BUYER6, OUT[-1]
press(1, 61000, "m:home")                                        # у админа — как было
assert "m:dg" in shown(), shown()
n92 = len(OUT)
msg(9300, 93000, "/check")                                       # и командой — нет
assert [b_["text"] for b_ in buttons_of(OUT[-1][1])] == BUYER6 and not any("Проверяю связь" in o[0] for o in OUT[n92:])
press(9300, 93000, "m:hp")
assert "/check" not in OUT[-1][0] and "/status" in OUT[-1][0], OUT[-1][0]
n92 = len(OUT)
msg(1, 61000, "/check")                                          # у хозяина — работает
assert any("Проверяю связь" in o[0] for o in OUT[n92:]), [o[0][:60] for o in OUT[n92:]]
print("h92 ok: 🔎 «Проверка связи» у покупателя убрана везде (и /check); меню покупателя и админа не изменились")

# 93) ❓ «Поддержка» — прямая ссылка на контакт из «📞 Изменить мой контакт» (в любом виде), не команда бота
def support_of(uid, chat):
    press(uid, chat, "m:main")
    return next(b_ for b_ in buttons_of(OUT[-1][1]) if b_["text"] == "❓ Поддержка")
contact93, nick93 = bot.SELLER_CONTACT, bot.GCFG.pop("host_nick", None)
try:
    for given, url in (("https://web.max.ru/12345678", "https://web.max.ru/12345678"),
                       ("Пиши мне: https://max.ru/u/AbC123", "https://max.ru/u/AbC123"),
                       ("max.ru/admin_x", "https://max.ru/admin_x"),
                       ("@admin.nick", "https://max.ru/admin.nick"),
                       ("admin_nick", "https://max.ru/admin_nick")):
        press(1, 61000, "p:clcontact"); n93 = len(OUT); msg(1, 61000, given)
        assert any(f"открывает: {url}" in o[0] for o in OUT[n93:]), (given, [o[0][:200] for o in OUT[n93:]])   # хозяину — куда ведёт
        sup = support_of(9300, 93000)
        assert sup["type"] == "link" and sup["url"] == url and "payload" not in sup, (given, sup)
    press(1, 61000, "m:bview")                                   # 👀 у админа — та же ссылка
    assert any(b_.get("url") == "https://max.ru/admin_nick" for b_ in buttons_of(OUT[-1][1])), OUT[-1][1]
    press(1, 61000, "p:clcontact"); n93 = len(OUT); msg(1, 61000, "+79991234567")   # телефон: ссылкой не открыть
    assert any("не ссылка" in o[0] for o in OUT[n93:]), [o[0][:200] for o in OUT[n93:]]   # хозяину — предупреждение
    sup = support_of(9300, 93000)
    assert sup.get("payload") != "m:hp", sup                     # не «❓ Команды»
    press(9300, 93000, sup["payload"])
    assert "+79991234567" in OUT[-1][0] and "Команды" not in OUT[-1][0] and "/start" not in OUT[-1][0], OUT[-1][0]
finally:
    bot.SELLER_CONTACT = contact93
    if nick93:
        bot.GCFG["host_nick"] = nick93
print("h93 ok: ❓ «Поддержка» — прямая ссылка на контакт хозяина (ссылка, max.ru/…, @ник, ник); телефон — без команд бота")

# 94) 👥 «Все покупатели» → покупатель: его каналы MAX кнопками-ссылками (туда, куда реально выкладывает бот)
#     и источники Telegram
for u94 in (9400, 9410, 9420):
    msg(u94, u94 * 10, "/start")
t94, t94b, t94c = (bot.tenant_themes(bot.tenant_key(u))[0] for u in (9400, 9410, 9420))
srv.chats["buyer94chan"] = 9401
t94.chat_id, t94.link, t94.own["channels"] = 9401, "https://max.ru/old_wrong", ["durov", "telegram"]
t94b.chat_id, t94b.link, t94b.own["channels"] = 9411, "https://max.ru/buyer94b", ["rbc_news"]   # MAX ссылку не дал
t94c.chat_id, t94c.link = 9421, ""                                                              # ссылки нет нигде
press(1, 61000, "m:cll:0")
assert any((b_.get("payload") or "").startswith("m:clc:") for b_ in buttons_of(OUT[-1][1])), OUT[-1][1]
press(1, 61000, f"m:clc:{bot.tenant_key(9400)}")
txt94, links94 = OUT[-1][0], [b_ for b_ in buttons_of(OUT[-1][1]) if b_.get("type") == "link"]
assert "📺 Каналы MAX" in txt94 and "https://max.ru/buyer94chan" in txt94 and "old_wrong" not in txt94, txt94
assert "📡 Источники Telegram" in txt94 and "@durov" in txt94 and "@telegram" in txt94, txt94
assert any(b_["url"] == "https://max.ru/buyer94chan" for b_ in links94), links94
press(1, 61000, f"m:clc:{bot.tenant_key(9410)}")
assert any(b_.get("url") == "https://max.ru/buyer94b" for b_ in buttons_of(OUT[-1][1])), OUT[-1][1]
assert "@rbc_news" in OUT[-1][0], OUT[-1][0]
t94c.own["channels"] = [f"long_source_channel_{i}" for i in range(40)]                     # очень много источников
press(1, 61000, f"m:clc:{bot.tenant_key(9420)}")
assert "ссылки нет" in OUT[-1][0] and not any(b_.get("type") == "link" for b_ in buttons_of(OUT[-1][1])), OUT[-1]
assert len(OUT[-1][0]) < bot.MAX_TEXT and "и ещё 25" in OUT[-1][0], (len(OUT[-1][0]), OUT[-1][0][-200:])   # влезает в MAX
press(1, 61000, f"m:buyer:{bot.tenant_key(9400)}")              # и в карточке «👥 Покупатели» — та же ссылка
assert "https://max.ru/buyer94chan" in OUT[-1][0], OUT[-1][0]
bot.ADMIN_IDS = {"556"}
try:
    press(556, 55600, f"m:clc:{bot.tenant_key(9400)}")           # админ ADMIN_IDS — тоже видит
    assert "https://max.ru/buyer94chan" in OUT[-1][0], OUT[-1][0]
finally:
    bot.ADMIN_IDS = set()
press(9420, 94200, f"m:clc:{bot.tenant_key(9400)}")             # покупателю чужой кабинет не открыть
assert "buyer94chan" not in shown() and "durov" not in shown(), shown()
assert panel_of(bot.screen("cl", 1)[1]) == PANEL88, "панель админа не тронута"
print("h94 ok: 👥 покупатель в «Все покупатели»: каналы MAX ссылками (куда реально выкладывает бот), источники Telegram")

# 95) 📡 источники Telegram и свой канал MAX покупатель подключает САМ (аккаунты-читатели Telegram — у
#     хозяина): «➕ Добавить канал» и «❌» в «📡 Каналы-источники», в пределах тарифа; приветствие и
#     инструкция — об этом; экран с 30+ источниками влезает в MAX; админ может поправить источники из карточки
OUT.clear()
msg(9500, 95000, "/start")
k95 = bot.tenant_key(9500)
t95 = bot.tenant_themes(k95)[0]
hello95 = " ".join(o[0] for o in OUT)
assert "«📡 Каналы-источники»" in hello95 and "подключит продавец" not in hello95, hello95
assert "Продлить доступ" not in hello95 and "«💳 Тарифы»" not in hello95 and "«💳 Подписка»" in hello95, hello95
assert "Подключи ему каналы-источники" not in hello95, "хозяину не пишем «подключи ему источники»"
press(9500, 95000, "m:thm")                                        # «🎭 Мои каналы» → «⚙️ Эта тема» → источники
assert "m:ch" in [b_.get("payload") for b_ in buttons_of(OUT[-1][1])], OUT[-1][1]
press(9500, 95000, "m:ch")
assert "p:chadd" in shown() and "подключает продавец" not in OUT[-1][0], OUT[-1]
press(9500, 95000, "m:gd")
assert "➕ Добавить канал" in OUT[-1][0] and "подключает продавец" not in OUT[-1][0], OUT[-1][0]
assert "m:ch" in [b_.get("payload") for b_ in buttons_of(OUT[-1][1])], OUT[-1][1]
press(9500, 95000, "m:terms")
assert "m:ch" in [b_.get("payload") for b_ in buttons_of(OUT[-1][1])], OUT[-1][1]
press(9500, 95000, "p:chadd")                                      # сначала — подтверждение прав на контент
assert "прав" in OUT[-1][0].lower() or "Подтверди" in OUT[-1][0], OUT[-1][0]
press(9500, 95000, "rk:ok")
assert bot.PENDING.get(9500) == "chadd" and "Пришли каналы Telegram" in OUT[-1][0], OUT[-1]
msg(9500, 95000, "t.me/durov")                                     # добавил сам
assert t95.own["channels"] == ["durov"], t95.own["channels"]
press(9500, 95000, "p:chadd"); n95 = len(OUT); msg(9500, 95000, "@second_src")   # бесплатный тариф: 1 источник
assert t95.own["channels"] == ["durov"] and any("тариф" in o[0] for o in OUT[n95:]), OUT[n95:]
assert not any("уже в списке" in o[0] for o in OUT[n95:]), "не влез в лимит — это не «уже в списке»"
press(9500, 95000, "a:chdel:0")                                    # убрал сам
assert t95.own["channels"] == [], t95.own["channels"]
srv.chats["buyer95chan"] = 9501                                    # привязал свой канал MAX — бот спрашивает источники
srv.chat_admins[9501] = [9500]
bot.PENDING[9500] = "thlink"
msg(9500, 95000, "https://max.ru/buyer95chan")
assert t95.chat_id == 9501 and bot.PENDING.get(9500) == "chadd", (t95.chat_id, bot.PENDING.get(9500))
bot.PENDING.pop(9500, None)
pro95 = next(i for i, t_ in enumerate(bot.tariffs()) if t_["name"] == "Про")
bot.host_set_plan(k95, pro95); bot.host_extend(k95, 30)           # тариф «Про»: до 30 источников
assert bot.tariffs()[pro95]["sources"] == 30
t95.own["channels"] = [f"pro_source_{i:02d}" for i in range(30)]
press(9500, 95000, "m:ch")                                         # 30 источников — экран влезает в MAX
assert len(OUT[-1][1]) <= 30 and sum(1 for b_ in buttons_of(OUT[-1][1]) if (b_.get("payload") or "").startswith("a:chdel:")) == 30, len(OUT[-1][1])
assert len(OUT[-1][0]) < bot.MAX_TEXT and "p:chadd" in shown()
press(9500, 95000, "a:chdel:29")
assert len(t95.own["channels"]) == 29 and "pro_source_29" not in t95.own["channels"]
bot.TENANTS[k95].update(until=0, plan="")                          # снова бесплатный — как в тестах ниже
t95.own["channels"] = ["keepme"]
# админ: карточка покупателя → «📡 Источники Telegram» → добавить / убрать
host95 = list(bot.tenant_themes(bot.host_id())[0].own.get("channels") or [])
press(1, 61000, f"m:clc:{k95}")
assert f"m:clsrc:{k95}" in shown(), shown()
press(1, 61000, f"m:clsrc:{k95}")
assert f"p:clsrcadd:{t95.id}" in shown() and "@keepme" in OUT[-1][0] and f"a:clsrcdel:{t95.id}:0" in shown(), OUT[-1]
press(1, 61000, f"a:clsrcdel:{t95.id}:0")                          # убрать
assert t95.own["channels"] == [] and "@keepme" not in OUT[-1][0], (t95.own["channels"], OUT[-1][0])
press(1, 61000, f"p:clsrcadd:{t95.id}")
assert bot.PENDING.get(1) == f"clsrcadd:{t95.id}", bot.PENDING.get(1)
n95 = len(OUT)
msg(1, 61000, "t.me/durov")                                        # добавить
assert t95.own["channels"] == ["durov"], t95.own["channels"]
assert any("Добавил" in o[0] for o in OUT[n95:]) and f"a:clsrcdel:{t95.id}:0" in shown(), OUT[n95:]
press(1, 61000, f"p:clsrcadd:{t95.id}")
n95 = len(OUT)
msg(1, 61000, "@third_src")                                        # бесплатный тариф: 1 источник
assert t95.own["channels"] == ["durov"] and any("Лимиты" in o[0] for o in OUT[n95:]), OUT[n95:]
assert list(bot.tenant_themes(bot.host_id())[0].own.get("channels") or []) == host95, "темы хозяина не тронуты"
press(9500, 95000, f"a:clsrcdel:{t95.id}:0")                       # админские кнопки покупателю не работают
bot.PENDING[9500] = f"clsrcadd:{t95.id}"
msg(9500, 95000, "@hacker_src")
press(9500, 95000, f"m:clsrc:{k95}")
assert t95.own["channels"] == ["durov"] and "p:clsrcadd" not in shown(), (t95.own["channels"], shown())
assert "Нет доступа" not in " ".join(o[0] for o in OUT[-4:]), "покупателю — без «Нет доступа»"
with bot.theme_ctx(t95):                                           # журнал покупателя: что сделал продавец
    log95 = [x for x in bot.JOURNAL if "Продавец" in x and getattr(x, "owner", "") == k95]
assert any("durov" in x for x in log95) and any("keepme" in x for x in log95), log95
saved95 = list(t95.own["channels"])
t95.own["channels"] = [f"many_source_{i:02d}" for i in range(70)]  # продавец поднял лимит — источников много
press(1, 61000, f"m:clsrc:{k95}")
assert len(OUT[-1][0]) < bot.MAX_TEXT and len(OUT[-1][1]) <= 30 and "и ещё" in OUT[-1][0], (len(OUT[-1][0]), len(OUT[-1][1]))
press(9500, 95000, "m:ch")
assert len(OUT[-1][0]) < bot.MAX_TEXT and "и ещё" in OUT[-1][0] and len(OUT[-1][1]) <= 30, (len(OUT[-1][0]), len(OUT[-1][1]))
t95.own["channels"] = saved95
bot.ADMIN_IDS = {"557"}
try:
    press(557, 55700, f"m:clsrc:{k95}")                            # админ ADMIN_IDS — тоже управляет
    assert f"p:clsrcadd:{t95.id}" in shown(), shown()
finally:
    bot.ADMIN_IDS = set()
print("h95 ok: 📡 источники и канал MAX покупатель подключает сам (в пределах тарифа); экран влезает в MAX; админ — из карточки")

# 96) ✋ карточки на одобрение — только владельцу темы: не хозяину чужих тем, не «ничьим» темам из старых настроек,
#     не темам пропавших кабинетов; старые карточки удалённых тем — «поста уже нет», а не «не из твоих тем»
msg(9600, 96000, "/start")
k96 = bot.tenant_key(9600)
t96 = bot.tenant_themes(k96)[0]
SENT96 = []
real_send96 = bot.api_send
def rec96(chat, text, rows=None, token=None):
    SENT96.append(chat)
    OUT.append((text or "", rows or []))
    return "m"
bot.api_send = rec96
try:
    def card96(t, n):
        it = {"id": f"{t.prefix}src96:{n}", "ch": "src96", "status": "pending", "date": time.time(),
              "enq": time.time(), "text": f"пост {n}", "photos": [], "vids": [], "docs": []}
        with bot.theme_ctx(t):
            bot.QUEUE.append(it)
            SENT96.clear()
            bot.send_preview(it, [[bot.B("✅", f"ap:now:{it['id']}")]])
        return it, set(SENT96)
    it96, to96 = card96(t96, 1)
    assert to96 == {96000}, to96                                    # покупателю — его карточка
    bot.TENANTS[k96]["admins"].append(1); bot.TENANTS[k96]["chats"]["1"] = 61000   # хозяин «попал» в чужой кабинет
    _, to96 = card96(t96, 2)
    assert to96 == {96000}, to96                                    # хозяину чужие карточки не идут
    bot.TENANTS[k96]["admins"].remove(1); bot.TENANTS[k96]["chats"].pop("1")
    rec1 = bot.TENANTS.pop(bot.host_id()); bot.TENANTS[bot.host_id()] = rec1   # кабинет хозяина — последним
    bot.TENANTS[k96]["admins"].append(1)                            # хозяин в списке админов чужого кабинета
    assert bot.tenant_key(1) == bot.host_id(), bot.tenant_key(1)     # …а «мои темы» — всё равно свой кабинет
    assert bot.my_themes(1) == bot.tenant_themes(bot.host_id())
    bot.TENANTS[k96]["admins"].remove(1)
    gc96 = dict(bot.GCFG["admin_chats"])
    bot.GCFG["admin_chats"]["1"] = 61000                            # старые общие настройки (до host-режима)
    tO = bot.Theme("o96", "Ничья тема", "", None, {"channels": ["oldsrc"], "approval_on": True})
    tD = bot.Theme("d96", "Потерянный кабинет", "", 9696, {"channels": ["lostsrc"]}, owner="deadbuyer96")
    bot.THEMES += [tO, tD]
    _, to96 = card96(tO, 3)
    assert to96 == set(), to96                                      # «ничьей» теме некому слать
    _, to96 = card96(tD, 4)
    assert to96 == set() and not bot.theme_live(tD) and bot.tenant_mode("deadbuyer96") == "off", to96
    bot.host_adopt_orphans()
    assert tO.owner == bot.host_id() and tD.owner == "deadbuyer96", (tO.owner, tD.owner)   # чужую тему себе не берём
    bot.GCFG["admin_chats"] = gc96
finally:
    bot.api_send = real_send96
OUT.clear()
press(1, 61000, f"ap:now:{it96['id']}")                             # хозяин жмёт на карточку покупателя
assert "не из твоих тем" in OUT[-1][0] and not OUT[-1][1], OUT[-1]
with bot.theme_ctx(t96):
    assert it96 in bot.QUEUE and it96["status"] == "pending", it96   # чужой пост не тронут
for ref96 in ("t999|src96:1", "src96:1"):                          # тема удалена / старая основная тема
    press(1, 61000, f"ap:now:{ref96}")
    assert "поста уже нет" in OUT[-1][0] and "не из твоих" not in OUT[-1][0] and not OUT[-1][1], (ref96, OUT[-1])
th96 = bot.tenant_themes(bot.host_id())[0]
with bot.theme_ctx(th96):
    hit = {"id": f"{th96.prefix}hsrc:1", "ch": "hsrc", "status": "pending", "date": time.time(), "enq": time.time(),
           "text": "мой пост", "photos": [], "vids": [], "docs": []}
    bot.QUEUE.append(hit)
press(1, 61000, f"ap:no:{hit['id']}")                               # свои посты хозяин одобряет как раньше
with bot.theme_ctx(th96):
    assert hit not in bot.QUEUE, "свой пост обработан"
assert "не из твоих" not in OUT[-1][0], OUT[-1]
print("h96 ok: ✋ карточки — только владельцу темы; «ничьим» и потерянным темам — никому; старые карточки не ломаются")

# 97) 🗑 «Очистить старые каналы» (панель): все темы кабинета хозяина и «ничьи» — с очередью, тайником, карточками;
#     покупатели не тронуты; потом — чистый кабинет; покупателю и чужому кабинету — нельзя
legacy97 = bot.Theme("main", "Основная", "https://max.ru/old_main", None, {"channels": ["legacysrc"]},
                     legacy=True, owner=bot.host_id())
bot.THEMES.insert(0, legacy97)
with bot.theme_ctx(legacy97):
    bot.QUEUE.append({"id": "legacysrc:7", "ch": "legacysrc", "status": "pending", "date": time.time(),
                      "enq": time.time(), "text": "старый пост", "photos": [], "vids": [], "docs": []})
    bot.save_queue()
bot.add_card(61000, "mid97", "legacysrc:7")
buyers97 = {k: len(bot.tenant_themes(k)) for k in bot.TENANTS if k != bot.host_id()}
mine97 = [t.name for t in bot.THEMES if t.owner == bot.host_id() or not t.owner]
assert "Основная" in mine97 and "Ничья тема" in mine97, mine97
press(1, 61000, "m:cl")
assert panel_of(OUT[-1][1]) == PANEL88, panel_of(OUT[-1][1])
press(1, 61000, "m:clwipe")
assert "Основная" in OUT[-1][0] and "a:clwipeok" in shown() and "m:cl" in shown(), OUT[-1]
assert legacy97 in bot.THEMES, "без подтверждения ничего не удаляю"
press(1, 61000, "a:clwipeok")
assert not [t for t in bot.THEMES if t.owner == bot.host_id() or not t.owner], [t.name for t in bot.THEMES]
assert legacy97 not in bot.THEMES and not os.path.exists(legacy97.f["queue"]), "старая основная тема — тоже"
assert {k: len(bot.tenant_themes(k)) for k in bot.TENANTS if k != bot.host_id()} == buyers97, "покупатели не тронуты"
assert tD in bot.THEMES, "темы потерянных кабинетов не трогаю (это не твои)"
assert any(c["ref"] == "legacysrc:7" and c.get("del_at") for c in bot.CARDS), "карточки на одобрение убраны"
assert "Удалил" in OUT[-1][0] and panel_of(OUT[-1][1]) == PANEL88, OUT[-1]
press(1, 61000, "ap:now:legacysrc:7")                               # старая карточка — без ошибки
assert "поста уже нет" in OUT[-1][0], OUT[-1]
paused97 = {k: c.get("paused") for k, c in bot.TENANTS.items() if k != bot.host_id()}
press(1, 61000, "a:pause")                                          # тем у хозяина нет — чужие не задевает
press(1, 61000, "m:q")
assert {k: c.get("paused") for k, c in bot.TENANTS.items() if k != bot.host_id()} == paused97, "покупатели не на паузе"
assert not any(t.name in OUT[-1][0] for t in bot.THEMES if t.owner != bot.host_id()), OUT[-1][0]
press(1, 61000, "m:home")                                           # подключить новые — с чистого листа
fresh97 = bot.tenant_themes(bot.host_id())
assert len(fresh97) == 1 and not fresh97[0].own.get("channels") and not fresh97[0].chat_id, [t.name for t in fresh97]
press(9500, 95000, "a:clwipeok")                                    # покупателю — нельзя
press(9500, 95000, "m:clwipe")
assert bot.tenant_themes(bot.host_id()) == fresh97 and bot.tenant_themes(k95) == [t95], "ничего не удалено"
bot.ADMIN_IDS = {"558"}
try:
    msg(558, 55800, "/start")                                       # админ ADMIN_IDS чистит только СВОЙ кабинет
    k558 = bot.tenant_key(558)
    assert bot.tenant_themes(k558)
    press(558, 55800, "a:clwipeok")
    assert bot.tenant_themes(k558) == [] and bot.tenant_themes(bot.host_id()) == fresh97, "кабинет хозяина цел"
finally:
    bot.ADMIN_IDS = set()
bot.THEMES.remove(tD)
print("h97 ok: 🗑 «Очистить старые каналы» — кабинет хозяина и «ничьи» темы с очередью и карточками; покупатели целы")

# 98) 💾 контакт и ключи ЮKassa из панели: бот подсказывает задать их и в переменных хостинга (переживут пересборку)
press(1, 61000, "p:clcontact"); n98 = len(OUT); msg(1, 61000, "@hint_admin")
assert any("SELLER_CONTACT" in o[0] for o in OUT[n98:]), [o[0][:120] for o in OUT[n98:]]
os.environ["SELLER_CONTACT"] = "@hint_admin"                         # уже в переменных — подсказка не нужна
press(1, 61000, "p:clcontact"); n98 = len(OUT); msg(1, 61000, "@hint_admin")
assert not any("SELLER_CONTACT" in o[0] for o in OUT[n98:]), [o[0][:120] for o in OUT[n98:]]
os.environ.pop("SELLER_CONTACT")
yk98 = os.environ["YOOKASSA_SECRET_KEY"]
os.environ["YOOKASSA_SECRET_KEY"] = "test_old_key"                   # в переменных — другой ключ
try:
    press(1, 61000, "p:clyk"); n98 = len(OUT); msg(1, 61000, "123456 test_secret")
    txt98 = " ".join(o[0] for o in OUT[n98:])
    assert "✅ ЮKassa" in txt98 and "YOOKASSA_SECRET_KEY" in txt98 and "test_secret" not in txt98, txt98
    os.environ["YOOKASSA_SECRET_KEY"] = "test_secret"                # совпадают — подсказка не нужна
    press(1, 61000, "p:clyk"); n98 = len(OUT); msg(1, 61000, "123456 test_secret")
    assert "YOOKASSA_SECRET_KEY" not in " ".join(o[0] for o in OUT[n98:])
finally:
    os.environ["YOOKASSA_SECRET_KEY"] = yk98
note98 = bot.storage_note()                                           # в лог при запуске: где данные и что задать
assert "💾 Данные бота" in note98 and os.getcwd() in note98 and "SELLER_CONTACT" in note98, note98
assert "test_secret" not in note98 and "YOOKASSA_SECRET_KEY" not in note98, "заданные переменные не просим"
print("h98 ok: 💾 контакт и ключи ЮKassa из панели — подсказка задать их в переменных Bothost (без показа ключа)")

# 99) 🔗 привязка канала MAX и источников: после «🗑 Очистить старые каналы», после удаления последней темы и
#     с «ожиданием ввода» с прошлого раза — не «в пустоту»; права админа канала проверяются при привязке по
#     ссылке; всё ложится в themes.json; новая тема не получает id (и файлы) удалённой
def disk_theme(tid):
    return next((d for d in json.load(open(bot.THEMES_FILE, encoding="utf-8"))["themes"] if d["id"] == tid), None)
# A) хозяин очистил старые каналы и добавил бота админом нового канала — канал привязался к новой теме
bot.PENDING[1] = "chadd"                                          # ждал ввода для старой темы
press(1, 61000, "a:clwipeok")
assert not bot.tenant_themes(bot.host_id()) and bot.PENDING.get(1) is None, "после очистки — ни тем, ни старого ввода"
srv.chats["host99a"] = 9901
bot.handle_update({"update_type": "bot_added", "chat_id": 9901, "is_channel": True,
                   "user": {"user_id": 1, "first_name": "Хозяин"}})
th99 = bot.tenant_themes(bot.host_id())
assert len(th99) == 1 and th99[0].chat_id == 9901, [(t.name, t.chat_id) for t in th99]
assert disk_theme(th99[0].id)["chat_id"] == 9901, "и в themes.json"
# B) «ожидание ссылки» осталось с прошлого раза, тему удалили — ссылка ложится в новую тему, а не «в пустоту»
press(1, 61000, "a:clwipeok")
bot.PENDING[1] = "thlink"
srv.chats["host99b"] = 9902
n99 = len(OUT)
msg(1, 61000, "https://max.ru/host99b")
th99 = bot.tenant_themes(bot.host_id())
assert len(th99) == 1 and th99[0].chat_id == 9902 and disk_theme(th99[0].id)["chat_id"] == 9902, \
    ([(t.name, t.chat_id) for t in th99], [o[0][:80] for o in OUT[n99:]])
# C) покупатель удалил свою единственную тему — выбор канала и источники снова работают и сохраняются
msg(9900, 99000, "/start")
k99 = bot.tenant_key(9900)
press(9900, 99000, "m:thd"); press(9900, 99000, "a:thdel")
assert not bot.tenant_themes(k99), "последняя тема удалена"
srv.chats["buyer99c"] = 9903
srv.chat_admins[9903] = [9900]
press(9900, 99000, "m:chpick")
assert "a:chbind:9903" in shown(), shown()
press(9900, 99000, "a:chbind:9903")
ts99 = bot.tenant_themes(k99)
assert len(ts99) == 1 and ts99[0].chat_id == 9903 and disk_theme(ts99[0].id)["chat_id"] == 9903, \
    [(t.name, t.chat_id) for t in ts99]
bot.record_consent(9900)
press(9900, 99000, "p:chadd"); msg(9900, 99000, "@src99")
assert disk_theme(ts99[0].id)["own"]["channels"] == ["src99"], disk_theme(ts99[0].id)["own"].get("channels")
# D) новая тема не получает id удалённой (её файлы, карточки и задачи — не её)
gone99 = ts99[0].id
press(9900, 99000, "a:thdel")
press(9900, 99000, "m:th")
assert bot.tenant_themes(k99) and bot.tenant_themes(k99)[0].id != gone99, [t.id for t in bot.tenant_themes(k99)]
nxt99 = f"t{int(bot.GCFG['theme_seq']) + 1}"
json.dump([{"id": f"{nxt99}|zombie:1", "ch": "zombie", "status": "ok"}], open(f"queue_{nxt99}.json", "w"))
t_new99 = bot.create_theme("Новая")
assert t_new99.id != nxt99 and not t_new99.queue, (t_new99.id, t_new99.queue)
bot.delete_theme(t_new99)
# E) как раньше: дал ссылку на канал, где бот уже админ, — привязан сразу (в списке админов MAX — только бот,
#    так MAX и отвечает; покупателя там может не быть под тем же id)
t99 = bot.tenant_themes(k99)[0]
srv.chats["stranger99"] = 9904
srv.chat_admins[9904] = [111]                                    # 111 — сам бот (как в настоящем MAX)
bot.PENDING[9900] = "thlink"
n99 = len(OUT)
msg(9900, 99000, "https://max.ru/stranger99")
assert t99.chat_id == 9904 and disk_theme(t99.id)["chat_id"] == 9904, t99.chat_id
assert any("привязан ✅" in o[0] for o in OUT[n99:]), [o[0][:120] for o in OUT[n99:]]
# F) тема ждёт канал по ссылке (бота ещё не было); бота добавили, написали слово — привязан
w99 = bot.create_theme("Ждёт канал", owner=k99)
w99.link = "https://max.ru/stranger99b"
srv.chats["stranger99b"] = 9905
srv.chat_admins[9905] = [111]
chanpost = {"update_type": "message_created", "message": {"recipient": {"chat_id": 9905, "chat_type": "channel"},
                                                           "body": {"text": "пост"}}}
bot.handle_update(chanpost)
assert w99.chat_id == 9905, w99.chat_id
bot.delete_theme(w99)
# F2) бота добавили в новый канал, а у всех тем уже есть каналы — не молчим: подсказываем, как перепривязать
srv.chats["buyer99new"] = 9906
n99 = len(OUT)
bot.handle_update({"update_type": "bot_added", "chat_id": 9906, "is_channel": True,
                   "user": {"user_id": 9900, "first_name": "U9900"}})
assert t99.chat_id == 9904 and any("Вижу канал «buyer99new»" in o[0] for o in OUT[n99:]), OUT[n99:]
# G) без темы бот не пишет «привязан ✅» (раньше ссылка тихо уходила в пустую тему)
with bot.theme_ctx(None):
    ans99, _ = bot.apply_text("thlink", "https://max.ru/host99b", 77777)
assert "привязан ✅" not in ans99, ans99
print("h99 ok: 🔗 привязка канала и источников — после очистки/удаления темы и со старым вводом; права канала; themes.json")

# 100) 📢 привязка канала MAX «напиши в канале любое сообщение»: тема ждёт канал по ссылке — пост без автора
#      привязывает его и бот сообщает «привязан»; MAX не отдал список админов / отдал пустой — всё равно
#      привязываем; чужой канал (в списке админов есть люди, но не владелец) — нет, и владельцу объясняем;
#      когда ничего не подошло — не молчим (лог и подсказка автору)
msg(10000, 100000, "/start")
k100 = bot.tenant_key(10000)
t100b = bot.tenant_themes(k100)[0]
def channel_post(chat, who=None):
    body = {"recipient": {"chat_id": chat, "chat_type": "channel"}, "body": {"text": "привет"}}
    if who is not None:
        body["sender"] = {"user_id": who, "first_name": f"U{who}"}
    bot.handle_update({"update_type": "message_created", "message": body})
def told(since, word):
    return any(word in o[0] for o in OUT[since:])
# A) ссылка введена, бота в канале ещё нет; потом бота добавили и написали пост — список админов MAX пустой
bot.PENDING[10000] = "thlink"
msg(10000, 100000, "https://max.ru/wait100")
assert t100b.chat_id is None and t100b.link == "https://max.ru/wait100", (t100b.chat_id, t100b.link)
srv.chats["wait100"] = 10001
n100 = len(OUT)
channel_post(10001)
assert t100b.chat_id == 10001 and disk_theme(t100b.id)["chat_id"] == 10001, t100b.chat_id
assert told(n100, "Канал MAX привязан"), [o[0][:80] for o in OUT[n100:]]
# B) то же, но MAX вообще не ответил про админов (ошибка сети/прав)
t100b.chat_id = None
srv.chats["wait100"] = 10001
real_adm100 = bot.chat_admin_ids
bot.chat_admin_ids = lambda cid: None
try:
    n100 = len(OUT)
    channel_post(10001)
    assert t100b.chat_id == 10001 and told(n100, "Канал MAX привязан"), [o[0][:80] for o in OUT[n100:]]
finally:
    bot.chat_admin_ids = real_adm100
# C) в списке админов MAX — только бот (как отвечает настоящий MAX): слово в канале всё равно привязывает
t100b.chat_id = None
srv.chat_admins[10001] = [111]
n100 = len(OUT)
channel_post(10001)
assert t100b.chat_id == 10001 and told(n100, "Канал MAX привязан"), [o[0][:100] for o in OUT[n100:]]
# D) владелец — в списке админов — тоже привязывается
t100b.chat_id = None
srv.chat_admins[10001] = [10000]
channel_post(10001)
assert t100b.chat_id == 10001
# E) пост от самого владельца (автор известен), тема без ссылки — привязывается к его теме без ссылки
t100b.chat_id, t100b.link = None, ""
srv.chat_admins[10001] = [10000]
channel_post(10001, who=10000)
assert t100b.chat_id == 10001
# F) все темы уже с каналом — пост от известного автора: не молчим, говорим, как перепривязать (раз в час)
srv.chats["extra100"] = 10002
n100 = len(OUT)
channel_post(10002, who=10000)
assert told(n100, "У твоих тем уже есть каналы MAX"), [o[0][:100] for o in OUT[n100:]]
n100 = len(OUT)
channel_post(10002, who=10000)
assert not told(n100, "У твоих тем уже есть каналы MAX"), "не чаще раза в час"
# G) тот же случай без автора и без подходящей темы — тихо для людей, но в логе причина
import io, contextlib as _cl
buf100 = io.StringIO()
with _cl.redirect_stdout(buf100):
    channel_post(10002)
    bot.CHAN_HINTS.clear()
    channel_post(10002)
assert "не нашлось темы" in buf100.getvalue() or "нет темы" in buf100.getvalue(), buf100.getvalue()
# H) ссылкой в боте: список админов пуст/недоступен — привязка проходит; чужой канал с людьми — нет (h99-E)
t100b.chat_id, t100b.link = None, ""
srv.chats["typed100"] = 10003
srv.chat_admins.pop(10003, None)
bot.PENDING[10000] = "thlink"
msg(10000, 100000, "https://max.ru/typed100")
assert t100b.chat_id == 10003, t100b.chat_id
t100b.chat_id = None
bot.chat_admin_ids = lambda cid: None
try:
    bot.PENDING[10000] = "thlink"
    msg(10000, 100000, "https://max.ru/typed100")
    assert t100b.chat_id == 10003, t100b.chat_id
finally:
    bot.chat_admin_ids = real_adm100
# I) хозяин: своя тема ждёт канал по ссылке, пост без автора — привязывается, сообщение приходит в личный чат хозяина
host100 = bot.new_cabinet_theme(bot.host_id(), "Хозяин 100")
host100.link = "https://max.ru/hostwait100"
srv.chats["hostwait100"] = 10004
srv.chat_admins[10004] = [424242]                                 # у хозяина права не сверяем
n100 = len(OUT)
channel_post(10004)
assert host100.chat_id == 10004 and told(n100, "Канал MAX привязан"), [o[0][:80] for o in OUT[n100:]]
# K) канал уже привязан к теме другого кабинета — ссылкой его не забрать, и бот так и говорит
t100b.chat_id = None
bot.PENDING[10000] = "thlink"
n100 = len(OUT)
msg(10000, 100000, "https://max.ru/hostwait100")                 # канал хозяина (из I)
assert t100b.chat_id is None and host100.chat_id == 10004, (t100b.chat_id, host100.chat_id)
assert told(n100, "уже привязан") and not told(n100, "Не нашёл канал"), [o[0][:120] for o in OUT[n100:]]
# L) канал у другой темы того же кабинета — переносим сюда
other100 = bot.new_cabinet_theme(k100, "Вторая")
other100.chat_id = 10005
srv.chats["mine100"] = 10005
bot.PENDING[10000] = "thlink"
msg(10000, 100000, "https://max.ru/mine100")
assert t100b.chat_id == 10005 and other100.chat_id is None, (t100b.chat_id, other100.chat_id)
bot.delete_theme(other100)
print("h100 ok: 📢 привязка канала по посту/ссылке — работает и без списка админов MAX, чужой канал нельзя, не молчим")

# 101) 📢 как раньше: дал ссылку (бот её не нашёл — закрытый канал / ссылка «поделиться»), написал любое слово в
#      канале — канал привязан к теме, которая ждёт (если ждёт одна, 20 минут); две ждут — по админам канала;
#      /channels — админу: каналы бота, к чему привязаны и последние события из каналов (покупателю — нет)
msg(10100, 101000, "/start")
k101 = bot.tenant_key(10100)
t101 = bot.tenant_themes(k101)[0]
bot.PENDING[10100] = "thlink"
n101 = len(OUT)
msg(10100, 101000, "https://max.ru/join/PRIV101")
assert t101.chat_id is None and told(n101, "Не нашёл канал") and told(n101, "любое сообщение"), [o[0][:120] for o in OUT[n101:]]
srv.private[10101] = ("Приватный 101", "https://max.ru/join/OTHERLINK")   # MAX отдаёт другую ссылку
n101 = len(OUT)
channel_post(10101)                                              # автор поста неизвестен (как в MAX)
assert t101.chat_id == 10101 and disk_theme(t101.id)["chat_id"] == 10101, t101.chat_id
assert told(n101, "Канал MAX привязан") and "bind_wait" not in t101.own, [o[0][:80] for o in OUT[n101:]]
# две темы ждут — без подсказки не гадаем; владелец в админах канала — его
msg(10110, 101100, "/start"); msg(10120, 101200, "/start")
ta, tb = bot.tenant_themes(bot.tenant_key(10110))[0], bot.tenant_themes(bot.tenant_key(10120))[0]
for u_, t_ in ((10110, ta), (10120, tb)):
    bot.PENDING[u_] = "thlink"
    msg(u_, u_ * 10, f"https://max.ru/join/WAIT{u_}")
srv.private[10102] = ("Чей-то канал", "")
channel_post(10102)
assert ta.chat_id is None and tb.chat_id is None, "две ждут — не гадаем"
srv.chat_admins[10102] = [111, 10120]                             # в админах — бот и второй покупатель
channel_post(10102)
assert tb.chat_id == 10102 and ta.chat_id is None, (ta.chat_id, tb.chat_id)
# ожидание кончилось (больше 20 минут) — случайный пост в чужом канале ничего не берёт
ta.own["bind_wait"] = time.time() - 21 * 60
srv.private[10103] = ("Канал после", "")
channel_post(10103)
assert ta.chat_id is None, ta.chat_id
# /channels: админу — каналы бота и события; покупателю — его меню
n101 = len(OUT)
msg(1, 61000, "/channels")
dump101 = " ".join(o[0] for o in OUT[n101:])
assert "Приватный 101" in dump101 and t101.name in dump101 and "свободен" in dump101, dump101[:600]
assert "10103" in dump101 or "Канал после" in dump101, "последние события из каналов"
n101 = len(OUT)
msg(10100, 101000, "/channels")
assert "Приватный 101" not in " ".join(o[0] for o in OUT[n101:]), "покупателю — нет"
print("h101 ok: 📢 как раньше — ссылка + любое слово в канале привязывает; /channels — диагностика у админа")

print("ВСЕ ТЕСТЫ HOST-РЕЖИМА ПРОШЛИ")
os._exit(0)
