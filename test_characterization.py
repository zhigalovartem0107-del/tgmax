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
print("ВСЕ ХАРАКТЕРИЗАЦИОННЫЕ ТЕСТЫ ПРОШЛИ")
os._exit(0)
