"""🧹 Чистка меню: «Расписание» без «Авто-серии», «Срочные» и «Приоритет» (удалены совсем — ни кнопок, ни экранов,
ни логики); остаётся «Ночь»; шаги интервала на месте; настройки разбиты на страницы с «⬅️ Назад» / «Далее ➡️».
Выбор канала MAX — постранично по 5, с «⬅️ Назад» / «Далее ➡️», «Обновить» и «Ввести ссылку» на каждой странице.
На каждом экране есть кнопка назад — из меню не попасть в тупик. Кнопки короткие."""
import importlib.util
import json
import os
import sys
import tempfile
import traceback
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

tele = types.ModuleType("telethon")
tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
tele.events = types.SimpleNamespace(NewMessage=lambda *a, **k: None)
sys.modules["telethon"] = tele
sys.modules["telethon.errors"] = tele.errors
os.chdir(tempfile.mkdtemp())
json.dump({"max_token": "T", "max_channel_link": "https://max.ru/x"}, open("config.json", "w"))
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
bot.CFG["admins"] = [1]
bot.CFG["admin_chats"] = {"1": 111}

FAILS = []


def check(fn):
    try:
        fn()
        print(f"{fn.__name__} ok: {fn.__doc__}")
    except Exception as e:
        FAILS.append(fn.__name__)
        line = traceback.extract_tb(e.__traceback__)[-1]
        print(f"{fn.__name__} FAIL (строка {line.lineno}: {line.line}) — {type(e).__name__}: {str(e)[:400]}")
    return fn


def btns(rows):
    return [(b["text"], b.get("payload") or "") for r in (rows or []) for b in r]


def pls(rows):
    return [p for _, p in btns(rows)]


GONE = ("a:ser", "m:ur", "m:pr", "a:urg", "a:urgmax", "a:prio", "a:bur", "a:bmax", "p:burx", "p:addur", "p:delur")


@check
def m1():
    """«Расписание» (стр. 1): нет «Авто-серии», «Срочные», «Приоритет» (и их кнопок); есть «🌙 Ночь» и шаги
    интервала (−15 / +15 / Своя / 30 / 1 час / 2 часа); внизу «Далее ➡️» и кнопка назад в меню"""
    text, rows = bot.screen("q", 1)
    p = pls(rows)
    assert not any(g in p for g in GONE), [x for x in p if x in GONE]
    for word in ("Авто-сери", "Срочны", "срочн", "Приоритет"):
        assert word not in text, (word, text)
    assert "m:nt" in p, "кнопка «🌙 Ночь» осталась"
    assert "a:gap:-15" in p and "a:gap:15" in p and "p:gapx" in p, p
    assert all(f"a:gapset:{v}" in p for v in (30, 60, 120)), p
    assert "m:q:1" in p, "«Далее ➡️» на страницу 2"
    assert any(x in ("m:main", "m:more", "m:cl") for x in p), "назад в меню есть"


@check
def m2():
    """«Расписание» (стр. 2): остальные настройки (кнопка «Через…», хранение, пустая очередь); «⬅️ Назад» на
    страницу 1 и кнопка назад в меню; удалённых функций тут тоже нет"""
    text, rows = bot.screen("q:1", 1)
    p = pls(rows)
    assert not any(g in p for g in GONE), [x for x in p if x in GONE]
    assert "m:q" in p, "«⬅️ Назад» на страницу 1"
    assert any(x.startswith("a:later") for x in p) and "a:age" in p and "a:ewarn" in p, p
    assert any(x in ("m:main", "m:more", "m:cl") for x in p), "назад в меню есть"


@check
def m3():
    """удалённые кнопки-действия (авто-серии, срочные, приоритет, окно серии) больше не срабатывают и не роняют
    бота; экранов «⚡ Срочные» и «⭐ Приоритет» нет; функций/настроек urgent, auto_series, priority не осталось"""
    for act in ("ser", "urg", "urgmax:1", "prio:0", "bur:5", "bmax:1"):
        bot.do_action(act, 1)                           # не должно падать (обработчика нет — тихо мимо)
    for key in ("urgent_on", "urgent_words", "urgent_max_h", "auto_series", "burst_min", "burst_max", "priority"):
        assert key not in bot.DEFAULTS, (key, "удалена из DEFAULTS")
        try:
            bot.CFG[key]                                 # настройки больше нет — доступ по ключу падает
            assert False, (key, "настройка всё ещё читается")
        except KeyError:
            pass
    assert not hasattr(bot, "is_urgent") and not hasattr(bot, "is_followup") and not hasattr(bot, "batch_for_head")
    assert "urgent" not in bot.STAT_KEYS


@check
def m4():
    """экраны «ur» и «pr» больше не дают «срочных»/«приоритет»: не содержат удалённых кнопок и не падают"""
    for name in ("ur", "pr"):
        text, rows = bot.screen(name, 1)                # что бы ни вернул, без удалённых кнопок и без краха
        assert not any(g in pls(rows) for g in GONE), (name, pls(rows))


def fake_channels(n):
    bot.bot_channels = lambda: [{"chat_id": -1000 - i, "title": f"Канал номер {i}", "link": f"https://max.ru/c{i}"}
                                for i in range(n)]


@check
def m5():
    """выбор канала MAX — постранично по 5: стр. 0 — 5 каналов, «Обновить», «Ввести ссылку», «Далее ➡️», назад;
    без «⬅️ Назад» (это первая страница)"""
    fake_channels(12)
    text, rows = bot.screen("chpick", 1)
    chbind = [p for p in pls(rows) if p.startswith("a:chbind:")]
    assert len(chbind) == 5, chbind
    p = pls(rows)
    assert any(x.startswith("m:chpick:1") for x in p) or "m:chpick:1" in p, "«Далее ➡️» на стр. 1"
    assert "p:thlink" in p, "«Ввести ссылку» есть"
    assert any("обнов" in t.lower() for t, _ in btns(rows)), "«Обновить» есть"
    assert any(x in ("m:main", "m:more", "m:cl") for x in p), "назад в меню есть"
    assert not any(x == "m:chpick:-1" for x in p)


@check
def m6():
    """средняя страница (1) — следующие 5 каналов, «⬅️ Назад» (стр. 0) и «Далее ➡️» (стр. 2); последняя (2) —
    остаток (2 канала), «⬅️ Назад» (стр. 1) и БЕЗ «Далее»; на каждой — «Обновить», «Ввести ссылку», назад"""
    fake_channels(12)
    _, rows1 = bot.screen("chpick:1", 1)
    p1 = pls(rows1)
    assert len([x for x in p1 if x.startswith("a:chbind:")]) == 5, p1
    assert "m:chpick:0" in p1 and "m:chpick:2" in p1, p1
    _, rows2 = bot.screen("chpick:2", 1)
    p2 = pls(rows2)
    assert len([x for x in p2 if x.startswith("a:chbind:")]) == 2, p2
    assert "m:chpick:1" in p2 and "m:chpick:3" not in p2, p2
    for p in (p1, p2):
        assert "p:thlink" in p and any(x in ("m:main", "m:more", "m:cl") for x in p), p


@check
def m7():
    """мало каналов (3) — одна страница: 3 канала, без «Далее» и «⬅️ Назад», но «Обновить»/«Ввести ссылку»/назад
    на месте; каналов нет — тоже без страниц, назад есть"""
    fake_channels(3)
    _, rows = bot.screen("chpick", 1)
    p = pls(rows)
    assert len([x for x in p if x.startswith("a:chbind:")]) == 3, p
    assert "m:chpick:1" not in p and not any(x.startswith("m:chpick:") and x != "m:chpick:0" for x in p), p
    assert "p:thlink" in p and any(x in ("m:main", "m:more", "m:cl") for x in p)
    bot.bot_channels = lambda: []
    _, rows = bot.screen("chpick", 1)
    assert any(x in ("m:main", "m:more", "m:cl") for x in pls(rows)), "пустой список — тоже не тупик"


@check
def m8():
    """кнопки короткие (без длинных «простыней»): на экранах расписания и выбора канала текст любой кнопки
    не длиннее 30 знаков; на каждом из этих экранов есть кнопка назад"""
    fake_channels(12)
    for name in ("q", "q:1", "chpick", "chpick:1", "nt"):
        _, rows = bot.screen(name, 1)
        for t, _p in btns(rows):
            assert len(t) <= 30, (name, t, len(t))
        assert any(x in ("m:main", "m:more", "m:cl", "m:q") for x in pls(rows)), (name, "есть назад")



@check
def m9():
    """у главных меню — короткая подсказка в подзаголовке: «Расписание» объясняет частоту публикаций,
    «Мои каналы» зовёт добавить каналы MAX"""
    qt, _ = bot.screen("q", 1)
    assert "Настрой, как часто бот будет выкладывать посты" in qt, qt[:160]
    tt, _ = bot.screen("th", 1)
    assert "Добавь свои каналы MAX" in tt, tt[:160]

if FAILS:
    print("НЕ ПРОШЛИ:", ", ".join(FAILS))
    sys.exit(1)
print("ВСЕ ТЕСТЫ ЧИСТКИ МЕНЮ ПРОШЛИ")
