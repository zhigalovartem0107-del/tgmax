"""📊 Пост из любого канала (или ссылка на канал) — без привязки и без прав админа: «Вижу пост из канала: …» и ровно
три кнопки — «📊 Рекламный охват», «📈 Массовая проверка» (только каналы из своей сетки, иначе «Этого канала нет в
вашей сетке…»), «⚡ Оценка потенциала». После каждой картинки (на всех — знак Reloader) — сообщение ровно в формате
«📈 «Канал» / 👥 … подписчиков · сегодня …, за 7 дней … / 👁 Просмотры на пост: 24 ч — …». Бот сам копит число
подписчиков (раз в час): «Подписалось: +X, Отписалось: -Y, Итого: +Z». «Это закрытый канал. …» — только когда канал
правда закрытый (приглашение, в котором аккаунт бота не состоит); открытый канал так не называем никогда: MAX не
показал число подписчиков или не показывает канал боту, t.me не открылся — это не «закрытый»; аккаунт Telegram бота
(если он подписан) читает канал сам. Канал попадает в «🕘 Недавние проверки»."""
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import threading
import traceback
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from fake_max import FakeMax  # noqa: E402

try:
    from reloader.services.analytics import watch as W
except ImportError:                                     # модуля ещё нет — проверки скажут, чего не хватает
    W = None

CLOSED = "Это закрытый канал. Чтобы я мог его анализировать, добавь меня в администраторы этого канала."
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


# ---------- 1) чистая логика: откуда пост, страница t.me, приток/отток, «Недавние проверки» ----------
@check
def w1():
    """откуда пост: пересланный пост канала MAX (по chat_id), ссылки t.me / max.ru / web.max.ru; закрытые ссылки;
    сообщение человека и обычный текст — не проверка канала"""
    assert W is not None, "нет модуля reloader.services.analytics.watch"
    fwd = {"link": {"type": "forward", "chat_id": -7100, "message": {"text": "пост"}}}
    assert W.source_of(fwd, "") == {"kind": "max", "chat_id": -7100}
    person = {"link": {"type": "forward", "sender": {"user_id": 55}, "chat_id": 777, "message": {"text": "hi"}}}
    assert W.source_of(person, "") is None, "переслали сообщение человека — это не пост канала"
    assert W.source_of({"link": {"type": "reply", "chat_id": -1}}, "") is None
    assert W.source_of({"link": {"type": "forward", "chat_id": "x"}}, "") is None
    cases = {"https://t.me/robloxtg/123": {"kind": "tg", "username": "robloxtg", "post": 123},
             "t.me/RobloxTG": {"kind": "tg", "username": "robloxtg", "post": None},
             "https://telegram.me/s/robloxtg/55": {"kind": "tg", "username": "robloxtg", "post": 55},
             "https://t.me/+AbCdEf123": {"kind": "tginvite", "hash": "AbCdEf123"},
             "https://t.me/joinchat/AbCd": {"kind": "tginvite", "hash": "AbCd"},
             "https://t.me/c/1234567/89": {"kind": "tgc", "id": 1234567}, "https://max.ru/join/AbCdEf": {"kind": "closed"},
             "https://max.ru/robloxnews": {"kind": "maxlink", "slug": "robloxnews"},
             "max.ru/robloxnews/AZx1": {"kind": "maxlink", "slug": "robloxnews"},
             "https://web.max.ru/-7100": {"kind": "max", "chat_id": -7100}}
    for text, want in cases.items():
        assert W.source_of({}, text) == want, (text, W.source_of({}, text))
    for text in ("привет", "посмотри t.me/x и скажи", "", "/start", "https://example.com/page"):
        assert W.source_of({}, text) is None, text


@check
def w2():
    """публичная страница t.me: название и число подписчиков (и участников группы); нет числа — None"""
    assert W is not None
    page = ('<html><head><meta property="og:title" content="Roblox &amp; TG News"></head><body>'
            '<div class="tgme_page_title" dir="auto"><span dir="auto">Roblox &amp; TG News</span></div>'
            '<div class="tgme_page_extra">12 345 subscribers</div></body></html>')
    assert W.parse_tme(page) == {"title": "Roblox & TG News", "subs": 12345, "kind": "channel"}, W.parse_tme(page)
    ru = '<meta property="og:title" content="Новости"><div class="tgme_page_extra">1\xa0234\xa0567 подписчиков</div>'
    assert W.parse_tme(ru)["subs"] == 1234567
    grp = '<meta property="og:title" content="Чат"><div class="tgme_page_extra">8 900 members, 120 online</div>'
    assert W.parse_tme(grp) == {"title": "Чат", "subs": 8900, "kind": "group"}
    assert W.parse_tme('<div class="tgme_page_extra">@someone</div>')["subs"] is None
    for extra, want in (("1.2M subscribers", 1200000), ("56.7K subscribers", 56700), ("3,4 млн подписчиков", 3400000),
                        ("812 тыс. подписчиков", 812000)):            # большой канал: t.me пишет число коротко
        got = W.parse_tme(f'<meta property="og:title" content="Политика"><div class="tgme_page_extra">{extra}</div>')
        assert got["subs"] == want and got["kind"] == "channel", (extra, got)
    assert W.parse_tme("") == {"title": None, "subs": None, "kind": None} and W.parse_tme(None)["subs"] is None


@check
def w3():
    """приток и отток по снимкам числа подписчиков; строка «Подписалось: +X, Отписалось: -Y, Итого: +Z»"""
    assert W is not None
    assert W.deltas([]) == (0, 0, 0) and W.deltas([(1, 1000)]) == (0, 0, 0)
    assert W.deltas([(1, 1000), (2, 1010), (3, 1005), (4, 1012)]) == (17, 5, 12)
    assert W.deltas([(1, 50), (2, 40)]) == (0, 10, -10)
    assert W.stats_line(17, 5, 12) == "Подписалось: +17, Отписалось: -5, Итого: +12"
    assert W.stats_line(0, 7, -7) == "Подписалось: +0, Отписалось: -7, Итого: -7"
    assert W.stats_line(1234, 0, 1234) == "Подписалось: +1 234, Отписалось: -0, Итого: +1 234"


@check
def w4():
    """«Недавние проверки»: свежие сверху, не больше 10, у каждого пользователя свои; снимки и названия переживают
    перезапуск; испорченный файл — не падаем"""
    assert W is not None
    d = tempfile.mkdtemp()
    path = os.path.join(d, "channel_checks.json")
    save = lambda p, data, **k: json.dump(data, open(p, "w"), ensure_ascii=False)
    load = lambda p, default: json.load(open(p)) if os.path.exists(p) else default
    clock = [1000.0]
    w = W.Watch(path, save=save, load=load, clock=lambda: clock[0])
    for i in range(12):
        clock[0] += 60
        w.remember("7", f"max:{-i}", {"title": f"Канал {i}", "link": ""})
    w.remember("8", "tg:robloxtg", {"title": "TG", "link": "https://t.me/robloxtg"})
    got = [r["key"] for r in w.recent("7")]
    assert got[0] == "max:-11" and len(got) == 10 and "max:0" not in got, got
    assert [r["key"] for r in w.recent("8")] == ["tg:robloxtg"]
    for n in (1000, 1010, 1010, 1005):
        clock[0] += 3600
        w.snap("tg:robloxtg", n)
    assert w.deltas("tg:robloxtg") == (10, 5, 5) and w.last("tg:robloxtg") == 1005
    w.save()
    w2_ = W.Watch(path, save=save, load=load, clock=lambda: clock[0])
    assert w2_.deltas("tg:robloxtg") == (10, 5, 5) and w2_.recent("8")[0]["title"] == "TG"
    assert "tg:robloxtg" in w2_.watched(), "в «Недавних» — значит, бот сам обновляет число подписчиков"
    open(path, "w").write('{"channels": [1, 2], "recent": "x"')                   # испорченный файл
    assert W.Watch(path, save=save, load=load).recent("7") == []


# ---------- 2) бот в host-режиме: поддельный MAX, пересылка поста покупателем и хозяином ----------
tele = types.ModuleType("telethon")
tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele
sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, BOT_TOKEN="TESTTOKEN", TGMAX_HOST="1", OWNER_ID="1")
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ADMIN_IDS"):
    os.environ.pop(k, None)
os.chdir(tempfile.mkdtemp())
json.dump({"max_token": "TESTTOKEN", "max_channel_link": ""}, open("config.json", "w"))
spec = importlib.util.spec_from_file_location("bot", os.path.join(ROOT, "tg_to_max.py"))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
bot.S.trust_env = False
bot.GCFG["trial_days"] = 0

HOST, HC, BUYER, BC = 1, 61000, 5001, 62000
PUB_ID, CLOSED_ID = -7100, -7200
srv.chats["robloxnews"] = PUB_ID                        # публичный канал: бот в нём НЕ админ и не привязан
srv.subs[PUB_ID] = 1000
TME = {"https://t.me/robloxtg": ('<meta property="og:title" content="Roblox TG News">'
                                 '<div class="tgme_page_extra">12 345 subscribers</div>')}


def fake_web(url):
    if url in TME:
        return TME[url]
    raise RuntimeError("t.me ответил 404")


bot.web_fetch = fake_web


def msg(uid, chat, text, link=None):
    m = {"sender": {"user_id": uid, "first_name": f"U{uid}"}, "recipient": {"chat_id": chat, "chat_type": "dialog"},
         "body": {"text": text}}
    if link:
        m["link"] = link
    bot.handle_update({"update_type": "message_created", "message": m})


def forward(uid, chat, cid, sender=None):
    link = {"type": "forward", "chat_id": cid, "message": {"text": "пост канала", "mid": "orig"}}
    if sender:
        link["sender"] = {"user_id": sender, "name": "Кто-то"}
    msg(uid, chat, "", link)


def press(uid, chat, payload):
    bot.handle_update({"update_type": "message_callback", "callback": {
        "callback_id": "c", "payload": payload, "user": {"user_id": uid, "first_name": f"U{uid}"}},
        "message": {"recipient": {"chat_id": chat}}})
    for th in [t for t in threading.enumerate() if t.name.startswith("fwd-")]:   # проверка канала идёт в фоне
        th.join(60)


def said(chat, n0):
    return [m for m in srv.messages[n0:] if m["chat_id"] == chat]


def texts(chat, n0):
    return [m["body"].get("text") or "" for m in said(chat, n0)]


def answered(n0):
    return [a["body"].get("message", {}) for a in srv.answers[n0:]]


msg(HOST, HC, "/start")
msg(BUYER, BC, "/start")
KEY = bot.tenant_key(BUYER)
bot.host_extend(KEY, 30)
assert not srv.chat_admins.get(PUB_ID), "бот не админ публичного канала"


MENU = ["📊 Рекламный охват", "📈 Массовая проверка", "⚡ Оценка потенциала"]
FMT = re.compile(r"📈 «(?P<title>[^»\n]+)»\n"                                 # ровно три строки — как просили
                 r"👥 (?P<subs>\d[\d ]*|—) подписчиков · сегодня (?P<today>[+−]\d[\d ]*|—), "
                 r"за 7 дней (?P<d7>[+−]\d[\d ]*|—)\n"
                 r"👁 Просмотры на пост: 24 ч — (?P<v24>\d[\d ]*|—), 48 ч — (?P<v48>\d[\d ]*|—), "
                 r"72 ч — (?P<v72>\d[\d ]*|—) · ER24 (?P<er>\d+,\d%|—)\n"
                 r"📊 Средний охват: (?P<reach>[\d ]+|—), Средний прирост: (?P<grow>[+−]\d[\d ]*|—) в день, "
                 r"Лучший день: (?P<best>[^\n]+)"                           # 📊 доп. строка — всегда есть
                 r"(?:\n🤖 (?P<ai>[^\n]{2,320}))?")                      # 4-я строка — вывод ИИ (если есть ключи)
NOT_BOUND = ("Этого канала нет в вашей сетке. Чтобы я мог его проверить, привяжите его в разделе "
             "«🎭 Мои каналы».")
BOUND_ID = -7300
srv.chats["mychan"] = BOUND_ID                          # канал, привязанный к кабинету покупателя
srv.subs[BOUND_ID] = 2500
srv.chat_admins[BOUND_ID] = [111]
bot.tenant_themes(KEY)[0].chat_id = BOUND_ID
TME["https://t.me/s/robloxtg"] = "".join(
    f'<div class="tgme_widget_message" data-post="robloxtg/{i}"><span class="tgme_widget_message_views">{v}</span>'
    f'<time datetime="2026-11-10T0{i}:00:00+00:00" class="time"></time></div>'
    for i, v in ((1, "1.2K"), (2, "980"), (3, "1.1K")))
CARDS = []
_real_upload = bot.upload_card
bot.upload_card = lambda img, photo_like=False: (CARDS.append(img), _real_upload(img, photo_like))[1]
RENDERED = []                                            # что нарисовали на картинках (цифры — на самой картинке)
for _name in ("render_ad_reach", "render_potential", "render_channel"):
    setattr(bot.anlimg, _name, (lambda real, nm: lambda d, *a, **k: (RENDERED.append((nm, d)), real(d, *a, **k))[1])(
        getattr(bot.anlimg, _name), _name))


def buttons(m):
    return [b for a in m["body"].get("attachments") or [] if a.get("type") == "inline_keyboard"
            for row in a["payload"]["buttons"] for b in row]


def has_image(m):
    return any(a.get("type") == "image" for a in m["body"].get("attachments") or [])


def edges(img, box):
    """«Резкие» пиксели в области (рамку в 1 пиксель Pillow не фильтрует — её не считаем)."""
    from PIL import ImageFilter
    e = img.convert("L").crop(box).filter(ImageFilter.FIND_EDGES)
    return sum(e.crop((1, 1, e.width - 1, e.height - 1)).histogram()[61:])


def watermark(img):
    return edges(img, bot.anlimg.watermark_box(img.size))


def menu_ok(m, key, title):
    assert (m["body"].get("text") or "").startswith(f"Вижу пост из канала: {title}"), m["body"].get("text")
    got = buttons(m)
    assert [b["text"] for b in got] == MENU, [b.get("text") for b in got]              # ровно три кнопки
    assert [b["payload"] for b in got] == [f"fw:{a}:{key}" for a in ("ad", "mass", "pot")], got


@check
def w5():
    """страница t.me/s/<канал>: просмотры последних постов (1.2K → 1200) и их время"""
    assert W is not None
    got = W.parse_tme_posts(TME["https://t.me/s/robloxtg"])
    assert [(i, v) for i, v, _ in got] == [(1, 1200), (2, 980), (3, 1100)], got
    assert all(isinstance(t, float) and t > 1.7e9 for _, _, t in got), got
    assert W.parse_tme_posts("") == [] and W.parse_tme_posts(None) == []
    assert [v for _, v, _ in W.parse_tme_posts('<div data-post="x/9"><span class="tgme_widget_message_views">'
                                               '2.5M</span></div>')] == [2500000]


@check
def f1():
    """пост из ЛЮБОГО канала (публичный, бот не админ, не привязан) — «Вижу пост из канала: robloxnews.» и ровно
    три кнопки: «📊 Рекламный охват», «📈 Массовая проверка», «⚡ Оценка потенциала»"""
    n0 = len(srv.messages)
    forward(BUYER, BC, PUB_ID)
    sent = said(BC, n0)
    assert len(sent) == 1, texts(BC, n0)
    menu_ok(sent[0], f"max:{PUB_ID}", "robloxnews")


def gone():
    return {str(x) for x in srv.deleted}


def live(chat, n0):
    """Что осталось в чате из отправленного после n0 (что бот удалил — не считаем)."""
    return [m for m in said(chat, n0) if str(m.get("mid")) not in gone()]


def analytics(chat, n0, title, key):
    """После кнопки в чате: картинка БЕЗ подписи, сразу за ней — 📈/👥/👁 (и 🤖, если есть ИИ), под ними — снова меню
    канала; «⏳ Анализирую…» уже убрано; картинку и цифры бот не удаляет (их нет в очереди «удалить через час»)."""
    got = live(chat, n0)
    assert len(got) == 3 and has_image(got[0]) and not (got[0]["body"].get("text") or "").strip(), \
        [m["body"] for m in got]
    assert not has_image(got[1]) and not buttons(got[1]), got[1]["body"]
    m = FMT.fullmatch(got[1]["body"].get("text") or "")
    assert m and m["title"] == title, got[1]["body"].get("text")
    assert [b["payload"] for b in buttons(got[2])] == [f"fw:{a}:{key}" for a in ("ad", "mass", "pot")], got[2]["body"]
    progress = [x for x in said(chat, n0) if (x["body"].get("text") or "").startswith("⏳")]
    assert progress and all(str(x["mid"]) in gone() for x in progress), "«Анализирую…» убрано"
    temp = {str(x["mid"]) for x in bot.AUTODEL["temp"]}
    assert str(got[0]["mid"]) not in temp and str(got[1]["mid"]) not in temp, "картинку и цифры не удаляем"
    return m


def live_texts(chat, n0):
    return [m["body"].get("text") for m in live(chat, n0)]


@check
def f2():
    """«📊 Рекламный охват»: картинка БЕЗ подписи (охват, подписчики, приток и отток — на самой картинке, знак
    Reloader), сразу за ней — 📈/👥/👁; «Анализирую…» и прежнее меню убраны, под цифрами — меню канала"""
    menus = [m for m in said(BC, 0) if (m["body"].get("text") or "").startswith("Вижу пост из канала: robloxnews")]
    n0, a0, c0 = len(srv.messages), len(srv.answers), len(CARDS)
    press(BUYER, BC, f"fw:ad:max:{PUB_ID}")
    m = analytics(BC, n0, "robloxnews", f"max:{PUB_ID}")
    assert m["subs"] == "1 000" and m["ai"] is None, m.groupdict()                # ИИ нет — ровно три строки
    kind, d = RENDERED[-1]
    assert kind == "render_ad_reach" and d["subs"] == 1000 and (d["joins"], d["leaves"], d["total"]) == (0, 0, 0), d
    assert "er24" in d and bot.anlimg.fmt_pct(d["er24"]) == m["er"], (d.get("er24"), m["er"])   # ER24 на первой карточке
    assert len(CARDS) == c0 + 1 and watermark(CARDS[-1]) > 150, "на картинке — знак Reloader"
    assert menus and str(menus[-1]["mid"]) in gone(), "прежнее меню канала убрано"
    menu_left = srv.answers[a0]["body"]["message"]
    assert [b["text"] for row in menu_left["attachments"][0]["payload"]["buttons"] for b in row] == MENU


@check
def f3():
    """бот сам следит за каналом (раз в час): 1000 → 1010 → 1004; «⚡ Оценка потенциала» — картинка без подписи
    (оценка из 100 и приток +10 / отток −6 / итого +4 — на картинке, со знаком Reloader), за ней — 📈/👥/👁"""
    srv.subs[PUB_ID] = 1010
    bot.fwd_snapshot_all()
    srv.subs[PUB_ID] = 1004
    bot.fwd_snapshot_all()
    n0, c0 = len(srv.messages), len(CARDS)
    press(BUYER, BC, f"fw:pot:max:{PUB_ID}")
    m = analytics(BC, n0, "robloxnews", f"max:{PUB_ID}")
    assert m["subs"] == "1 004", m.groupdict()
    kind, d = RENDERED[-1]
    assert kind == "render_potential" and (d["joins"], d["leaves"], d["total"]) == (10, 6, 4) and d["score"], d
    assert len(CARDS) == c0 + 1 and watermark(CARDS[-1]) > 150, "знак Reloader — на каждой картинке"


@check
def f4():
    """«📈 Массовая проверка»: канала нет в сетке — ровно фраза «Этого канала нет в вашей сетке…» (а «Анализирую…»
    убрано); следующий пост — фраза убрана; канал привязан к кабинету — аналитика картинкой и 📈/👥/👁"""
    n0 = len(srv.messages)
    press(BUYER, BC, f"fw:mass:max:{PUB_ID}")
    got = live(BC, n0)
    assert [m["body"].get("text") for m in got] == [NOT_BOUND] and not got[0]["body"].get("attachments"), \
        [m["body"] for m in got]
    n1 = len(srv.messages)
    forward(BUYER, BC, BOUND_ID)
    assert str(got[0]["mid"]) in gone(), "новый пост — промежуточное убрано"
    menu_ok(said(BC, n1)[0], f"max:{BOUND_ID}", "mychan")
    n2, c0 = len(srv.messages), len(CARDS)
    press(BUYER, BC, f"fw:mass:max:{BOUND_ID}")
    m = analytics(BC, n2, "mychan", f"max:{BOUND_ID}")
    assert m["subs"] == "2 500" and RENDERED[-1][0] == "render_channel", m.groupdict()
    assert len(CARDS) == c0 + 1 and watermark(CARDS[-1]) > 150


@check
def f5():
    """канал, который MAX НЕ показывает боту (бот в нём не состоит, 404), — это ещё не «закрытый»: меню из трёх
    кнопок без слова «закрытый»; «Рекламный охват» и «Оценка потенциала» — честно «MAX не показывает этот канал
    боту…» (прежняя такая фраза убирается после следующего нажатия); «Массовая проверка» — «нет в вашей сетке»"""
    n0 = len(srv.messages)
    forward(BUYER, BC, CLOSED_ID)
    sent = said(BC, n0)
    assert len(sent) == 1 and len(buttons(sent[0])) == 3, [m["body"] for m in sent]
    assert "закрыт" not in sent[0]["body"]["text"].lower(), sent[0]["body"]["text"]
    before = []
    for act in ("ad", "pot"):
        n1 = len(srv.messages)
        press(BUYER, BC, f"fw:{act}:max:{CLOSED_ID}")
        assert live_texts(BC, n1) == [bot.FWD_HIDDEN] and "закрыт" not in bot.FWD_HIDDEN.lower(), (act, live_texts(BC, n1))
        assert all(str(x) in gone() for x in before), "после нажатия прежняя фраза убрана"
        before = [m["mid"] for m in live(BC, n1)]
    n2 = len(srv.messages)
    press(BUYER, BC, f"fw:mass:max:{CLOSED_ID}")
    assert live_texts(BC, n2) == [NOT_BOUND] and all(str(x) in gone() for x in before), live_texts(BC, n2)


@check
def f6():
    """ссылка на публичный канал Telegram — тоже меню; «Рекламный охват» — картинка (подписчики со страницы t.me,
    охват рекламного поста по просмотрам последних постов ≈ 880–1 210), потом 📈/👥/👁; приглашение t.me/+… в
    канал, где аккаунта бота нет, — ровно «Это закрытый канал…»"""
    n0 = len(srv.messages)
    msg(BUYER, BC, "https://t.me/robloxtg/123")
    sent = said(BC, n0)
    assert len(sent) == 1, texts(BC, n0)
    menu_ok(sent[0], "tg:robloxtg", "Roblox TG News")
    n1 = len(srv.messages)
    press(BUYER, BC, "fw:ad:tg:robloxtg")
    m = analytics(BC, n1, "Roblox TG News", "tg:robloxtg")
    kind, d = RENDERED[-1]
    assert kind == "render_ad_reach" and (d["subs"], d["low"], d["high"]) == (12345, 880, 1210), d
    assert m["subs"] == "12 345", m.groupdict()
    n2 = len(srv.messages)
    msg(BUYER, BC, "https://t.me/+AbCdEf123")
    assert live_texts(BC, n2) == [CLOSED], live_texts(BC, n2)
    n3 = len(srv.messages)
    press(BUYER, BC, "fw:mass:tg:robloxtg")
    assert live_texts(BC, n3) == [NOT_BOUND], live_texts(BC, n3)


@check
def f7():
    """«🕘 Недавние проверки» (/recent): каналы — свежие сверху; канал, который MAX не показывает боту, — с пометкой
    🙈 (а не «закрытый»); кнопка — снова меню из трёх кнопок этого канала; чужой канал в кнопке — не открываем"""
    n0 = len(srv.messages)
    msg(BUYER, BC, "/recent")
    screen = texts(BC, n0)[-1]
    assert screen.startswith("🕘 Недавние проверки") and "🙈" in screen and "закрыт" not in screen.lower(), screen
    order = [screen.find(x) for x in ("Roblox TG News", "robloxnews")]
    assert -1 not in order and order == sorted(order), screen
    rows = json.dumps(said(BC, n0)[-1]["body"].get("attachments"), ensure_ascii=False)
    assert f"fw:r:max:{PUB_ID}" in rows and "fw:r:tg:robloxtg" in rows, rows
    a0 = len(srv.answers)
    press(BUYER, BC, f"fw:r:max:{PUB_ID}")
    m = {"body": srv.answers[a0]["body"]["message"]}
    menu_ok(m, f"max:{PUB_ID}", "robloxnews")
    a1 = len(srv.answers)
    press(BUYER, BC, "fw:r:max:-9999")
    assert len(buttons({"body": srv.answers[a1]["body"]["message"]})) != 3
    assert "🕘 Недавние проверки" in json.dumps(bot.screen("anl", HOST)[1], ensure_ascii=False) \
        or not bot.feature_on("stats"), "кнопка в «📊 Аналитика»"


@check
def f8():
    """хозяин переслал пост канала — меню (а не «этот человек не заходил в бота»); пересланное сообщение человека —
    по-старому; MAX не ответил — «ещё раз», без «закрытый канал» и без ошибки; бот работает дальше"""
    n0 = len(srv.messages)
    forward(HOST, HC, PUB_ID)
    sent = said(HC, n0)
    assert sent and not any("не заходил" in (m["body"].get("text") or "") for m in sent), [m["body"] for m in sent]
    menu_ok(sent[0], f"max:{PUB_ID}", "robloxnews")
    n1 = len(srv.messages)
    forward(BUYER, BC, 777, sender=9999)
    assert not any(t.startswith("Вижу пост") or t == CLOSED for t in texts(BC, n1)), texts(BC, n1)
    real_get = bot.S.get
    bot.S.get = lambda url, *a, **k: (_ for _ in ()).throw(bot.requests.exceptions.ConnectionError("down")) \
        if "/chats/" in url else real_get(url, *a, **k)
    n2 = len(srv.messages)
    try:
        forward(BUYER, BC, PUB_ID)
    finally:
        bot.S.get = real_get
    got = texts(BC, n2)
    assert len(got) == 1 and got[0] != CLOSED and "ещё раз" in got[0], got
    assert not re.search(r"Traceback|ConnectionError|Exception|⚠️", got[0]), got[0]
    n3 = len(srv.messages)
    forward(BUYER, BC, PUB_ID)
    menu_ok(said(BC, n3)[0], f"max:{PUB_ID}", "robloxnews")


@check
def f9():
    """ОТКРЫТЫЙ канал MAX, у которого MAX не дал число подписчиков (200 без participants_count), — не «закрытый»:
    меню без пометок, «Рекламный охват» — картинка и 📈/👥/👁 с «—» вместо неизвестного"""
    srv.chats["politika"] = -7400                         # открытый политический канал: MAX показал, но без числа
    n0 = len(srv.messages)
    forward(BUYER, BC, -7400)
    sent = said(BC, n0)
    assert len(sent) == 1, texts(BC, n0)
    menu_ok(sent[0], "max:-7400", "politika")
    assert "🔒" not in sent[0]["body"]["text"] and "не показывает" not in sent[0]["body"]["text"], sent[0]["body"]["text"]
    for act in ("ad", "pot"):
        n1 = len(srv.messages)
        press(BUYER, BC, f"fw:{act}:max:-7400")
        assert not any(t in (CLOSED, bot.FWD_HIDDEN) for t in texts(BC, n1)), (act, texts(BC, n1))
        m = analytics(BC, n1, "politika", "max:-7400")
        assert m["subs"] == "—", m.groupdict()


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeTG:
    """Аккаунт Telegram бота (Telethon): видит публичные каналы и закрытые, где состоит."""

    def __init__(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).timestamp()
        self.chats = {"polit": Obj(id=555, title="Политика 24", username="polit", broadcast=True),
                      "https://t.me/+Secret1": Obj(id=777, title="Закрытый клуб", username=None, broadcast=True),
                      -100777: Obj(id=777, title="Закрытый клуб", username=None, broadcast=True)}
        self.subs = {555: 250000, 777: 3100}
        self.posts = {cid: [Obj(id=i, views=views + i * 50, date=datetime.fromtimestamp(now - (i + 30) * 3600, timezone.utc))
                            for i in range(12)] for cid, views in ((555, 40000), (777, 900))}
        self.calls = []

    async def get_entity(self, ref):
        self.calls.append(ref)
        if ref not in self.chats:
            raise ValueError("Cannot get entity from a channel (or group) that you are not part of")
        return self.chats[ref]

    async def get_participants(self, ent, limit=None):
        return Obj(total=self.subs[ent.id])

    async def get_messages(self, ent, limit=None):
        return self.posts[ent.id][:limit]


@check
def f10():
    """Telegram: t.me не открылся (403) или не показал число — это не «закрытый». Без аккаунта Telegram — «ещё раз»;
    с аккаунтом бота — читаем канал через него (публичный и закрытый, где он подписан): меню, охват, 📈/👥/👁;
    приглашение в канал, где аккаунта нет, — «Это закрытый канал…»"""
    import asyncio
    real_client, real_loop = bot.TG_CLIENT, bot.MAIN_LOOP
    bot.TG_CLIENT = None
    n0 = len(srv.messages)
    msg(BUYER, BC, "https://t.me/polit")                  # fake_web: t.me ответил 403
    got = texts(BC, n0)
    assert len(got) == 1 and got[0] != CLOSED and "закрыт" not in got[0].lower() and "ещё раз" in got[0], got
    loop = asyncio.new_event_loop()
    th = threading.Thread(target=loop.run_forever, daemon=True)
    th.start()
    bot.TG_CLIENT, bot.MAIN_LOOP = FakeTG(), loop
    try:
        n1 = len(srv.messages)
        msg(BUYER, BC, "https://t.me/polit")
        menu_ok(said(BC, n1)[0], "tg:polit", "Политика 24")
        n2, c0 = len(srv.messages), len(CARDS)
        press(BUYER, BC, "fw:ad:tg:polit")
        assert len(CARDS) == c0 + 1
        m = analytics(BC, n2, "Политика 24", "tg:polit")
        kind, d = RENDERED[-1]
        assert d["subs"] == 250000 and d["median"], d
        assert m["subs"] == "250 000" and m["v24"] != "—", m.groupdict()      # просмотры (посты 30–41 ч) — через аккаунт
        assert watermark(CARDS[-1]) > 150
        n3 = len(srv.messages)
        msg(BUYER, BC, "https://t.me/+Secret1")           # закрытый, но аккаунт бота в нём состоит — читаем
        menu_ok(said(BC, n3)[0], "tgc:777", "Закрытый клуб")
        n4 = len(srv.messages)
        press(BUYER, BC, "fw:pot:tgc:777")
        m = analytics(BC, n4, "Закрытый клуб", "tgc:777")
        assert RENDERED[-1][0] == "render_potential" and m["subs"] == "3 100", m.groupdict()
        n5 = len(srv.messages)
        msg(BUYER, BC, "https://t.me/+NotMember")         # приглашение, где аккаунта нет, — правда закрытый
        assert texts(BC, n5) == [CLOSED], texts(BC, n5)
    finally:
        bot.TG_CLIENT, bot.MAIN_LOOP = real_client, real_loop
        loop.call_soon_threadsafe(loop.stop)


@check
def f11():
    """🤖 4-я строка — вывод ИИ (через ротацию ключей) по цифрам канала: «Based on these stats…» по-русски, 1–2
    предложения, одной строкой, без разметки; ИИ нет или упал — ровно три строки; те же цифры — ИИ не
    спрашиваем повторно (ответ из памяти)"""
    calls = []
    real_ready, real_ask = bot.ai_llm_ready, bot.ai_ask_text

    def fake_ask(text, rotators, http, settings, log=print, prompt="", timeout=25):
        calls.append((text, prompt, rotators))
        return ("**Вывод:** канал растёт:\n+4 за неделю, и вовлечённость высокая — просмотров много.\n\n- лишнее\n"
                "- ещё пункт")

    bot.ai_llm_ready, bot.ai_ask_text = (lambda: True), fake_ask
    try:
        n0 = len(srv.messages)
        press(BUYER, BC, f"fw:pot:max:{PUB_ID}")
        m = analytics(BC, n0, "robloxnews", f"max:{PUB_ID}")
        ai = m["ai"] or ""
        assert "канал растёт" in ai and "**" not in ai and "лишнее" not in ai and len(ai) <= 300, ai
        text, prompt, rot = calls[-1]
        assert "Based on these stats" in prompt and "Russian" in prompt and rot is bot.AI_ROTATORS, prompt
        assert "1 004" in text and "ER24" in text, text                       # ИИ видит те самые три строки
        n1, k = len(srv.messages), len(calls)
        press(BUYER, BC, f"fw:ad:max:{PUB_ID}")
        assert analytics(BC, n1, "robloxnews", f"max:{PUB_ID}")["ai"] == ai and len(calls) == k, "из памяти"
        bot.ai_ask_text = lambda *a, **kw: (_ for _ in ()).throw(bot.AiUnavailable("ключи кончились"))
        n2 = len(srv.messages)
        press(BUYER, BC, f"fw:mass:max:{BOUND_ID}")
        assert analytics(BC, n2, "mychan", f"max:{BOUND_ID}")["ai"] is None, "ИИ упал — ровно три строки"
    finally:
        bot.ai_llm_ready, bot.ai_ask_text = real_ready, real_ask


@check
def f12():
    """🧹 чистый чат: картинки аналитики и сообщения с цифрами остаются (их не удаляет и «прошёл час»);
    промежуточное («Этого канала нет в вашей сетке…», «MAX не показывает…», «Анализирую…», меню канала) —
    убираем после следующего нажатия (любой кнопки) или когда пришла аналитика"""
    n0 = len(srv.messages)
    press(BUYER, BC, f"fw:mass:max:{PUB_ID}")
    nb = [x for x in live(BC, n0) if x["body"].get("text") == NOT_BOUND]
    assert len(nb) == 1, live_texts(BC, n0)
    n1 = len(srv.messages)
    press(BUYER, BC, f"fw:ad:max:{PUB_ID}")
    assert str(nb[0]["mid"]) in gone(), "нажал снова — «нет в вашей сетке» убрано"
    got = live(BC, n1)
    bot.purge_autodel(time.time() + 7200)                                     # прошло два часа
    assert str(got[0]["mid"]) not in gone() and str(got[1]["mid"]) not in gone(), "картинка и цифры остались"
    n2 = len(srv.messages)
    press(BUYER, BC, f"fw:ad:max:{CLOSED_ID}")
    hidden = live(BC, n2)
    assert [m["body"].get("text") for m in hidden] == [bot.FWD_HIDDEN]
    press(BUYER, BC, "m:main")                                                # любая кнопка меню
    assert str(hidden[0]["mid"]) in gone(), "нажал любую кнопку — промежуточное убрано"


if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    os._exit(1)
print("ВСЕ ТЕСТЫ ПРОВЕРКИ КАНАЛОВ ПРОШЛИ")
os._exit(0)
