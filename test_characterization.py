import os as _os
ROOT = _os.path.dirname(_os.path.abspath(__file__))
"""Характеризационные (golden) тесты: результаты фильтров и очереди записаны с кода ДО рефакторинга и должны
совпадать после каждого шага. Перезаписать эталон (только если поведение меняют намеренно): GOLDEN_UPDATE=1."""
import asyncio, importlib.util, json, os, sys, tempfile, time, types

tele = types.ModuleType("telethon"); tele.TelegramClient = object
sys.modules["telethon"] = tele
os.chdir(tempfile.mkdtemp())
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)

GOLD = os.path.join(ROOT, "fixtures", "golden.json")
BODIES = ["Вышло обновление 0.33: новая карта и баланс оружия.", "**Срочно!** Роблокс раздаёт подарки",
          "Смотри видео про новый режим", "Новый сезон уже в игре! #so2", "Как получить робуксы бесплатно — гайд",
          "Розыгрыш скинов среди подписчиков", "Патч исправил баги с __курсивом__ и `__init__`",
          "Мы открыли магазин скинов", "Сегодня в 19:00 стрим", "Официально: игра выходит 1 ноября"]
EXTRAS = ["", "\n\nПодпишись на @coolgames", "\n\n[Наш канал](https://t.me/cool)",
          "\n\nРеклама. ООО «Ромашка». erid: 2VtzqwXyZ", "\n\nhttps://t.me/joinchat/abc", "\n\n#реклама",
          "\n\nПереходи по ссылке https://site.ru/promo?ref=1", "\n\n🔥 Подписывайтесь на наш канал 👉 @news_ch",
          "\n\nvk.com/group", "\n\nПромокод SALE20 — скидка 20%, активаций ограничено", "\n\n||спойлер||",
          "\n\n**Жми [сюда](https://ex.com/a__b)**", "\n\nКонкурс! Условия: подписаться на @a и @b",
          "\n\nИсточник: @source_channel", "\n\nmax.ru/other"]


def filters():
    out, hist = [], []
    for i, b in enumerate(BODIES):
        for j, e in enumerate(EXTRAS):
            raw = b + e
            v, why = bot.ad_verdict(raw, "src", (i + j) % 3 == 0)
            entry = {"norm": bot.norm(bot.clean_text(raw, with_signature=False)), "img": [], "t": 1.0e9, "fwd": []}
            dup = bot.find_duplicate(entry, hist)
            hist.append(entry)
            out.append({"raw": raw, "clean": bot.clean_text(raw, with_signature=False), "md": bot.fix_markdown(raw),
                        "ad": [v, list(why)], "dup": hist.index(dup) if dup is not None else None})
    return out


def check(name, got):
    got = json.loads(json.dumps(got, ensure_ascii=False))     # как в файле: кортежи -> списки
    gold = json.load(open(GOLD, encoding="utf-8")) if os.path.exists(GOLD) else {}
    if os.environ.get("GOLDEN_UPDATE") or name not in gold:
        gold[name] = got
        os.makedirs(os.path.dirname(GOLD), exist_ok=True)
        json.dump(gold, open(GOLD, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"{name}: эталон записан ({len(got)})")
        return
    bad = [(i, w, g) for i, (w, g) in enumerate(zip(gold[name], got)) if w != g]
    assert len(gold[name]) == len(got) and not bad, f"{name}: расходится с эталоном: {bad[:3]}"


check("filters", filters())
print("g1 ok: фильтры (чистка, разметка, реклама, дубли) — 150 постов как до рефакторинга")

os.environ["TZ"] = "UTC"; time.tzset()
T0 = 1_800_000_000.0                                        # 2027-01-15 08:00 UTC
CLOCK = [T0]
_real_lt = time.localtime
time.time = lambda: CLOCK[0]
time.localtime = lambda t=None: _real_lt(CLOCK[0] if t is None else t)
OUT = []


async def _fake_pub(client, chat_id, it):
    OUT.append((int((CLOCK[0] - T0) // 60), it["id"]))
    return "ok"
bot.publish_item = bot._publish_item = _fake_pub
bot.notify = lambda *a, **k: None


def item(i, ch="a", **kw):
    return dict({"id": f"{ch}:{i}", "ch": ch, "date": T0 - 3600 + i * 60, "enq": T0, "text": f"пост {i}", "photos": [],
                 "vids": [], "docs": [], "sent": 0, "fails": 0, "status": "ok", "urgent": False}, **kw)


def run(items, minutes=180, **cfg):
    base = dict(queue_on=True, gap_min=20, night_on=False, urgent_on=True, urgent_max_h=3, smart_time=False,
                auto_series=False, approval_on=False, stash_mode="off", max_age_h=24)
    for k, v in dict(base, **cfg).items():
        bot.CFG[k] = v
    bot.QUEUE[:] = items
    bot.PUB.update({"last_pub": 0, "ch_last_date": {}, "ch_last_pub": {}, "series": {}, "urgent_times": []})
    OUT.clear()
    for m in range(minutes):
        CLOCK[0] = T0 + m * 60
        asyncio.run(bot.publish_due(None, 777))
    CLOCK[0] = T0
    return list(OUT)


queue = {
    "schedule_20min": run([item(i, "ab"[i % 2]) for i in range(6)]),
    "schedule_off": run([item(i) for i in range(4)], minutes=10, queue_on=False),
    "urgent_mix": run([item(i, urgent=i < 3) for i in range(5)], minutes=60),
    "night": run([item(i) for i in range(3)], minutes=90, night_on=True, night_from=8, night_to=9, gap_min=10),
    "manual_overdue": run([item(i, manual=True, at=T0 - 3600) for i in range(3)] + [item(9)], minutes=70),
    "waiting_file": run([item(1, status="stuck", retry_at=T0 + 25 * 60), item(2)], minutes=60),
}
check("queue", [[k, v] for k, v in queue.items()])
print("g2 ok: очередь (расписание, без расписания, срочные, ночь, просроченные «Сейчас», ждущий файл) как до рефакторинга")
print("ВСЕ ХАРАКТЕРИЗАЦИОННЫЕ ТЕСТЫ ПРОШЛИ")
os._exit(0)
