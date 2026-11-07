"""🔮 Прогноз поста до публикации: ожидаемые просмотры, лучшее время и оценка из 10.

Опирается на историю ЭТОГО канала (просмотры его постов): от чего отталкиваться (медиана), как у него
заходят посты с фото/видео и без, короткие и длинные, «рекламные» (ссылки, «подпишись»). Без сети и ИИ —
чистый подсчёт; комментарий ИИ бот добавляет сверху, если есть ключи."""
import re
import statistics
import time

WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
AD_WORDS = re.compile(r"(?i)подпис(ыв|ывайся|ывайтесь|ка на)|переход(и|ите) по ссылке|промокод|скидк|реклам|"
                      r"заработ|розыгрыш|t\.me/|https?://")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def features(text, photo=False, video=False):
    t = "" if text is None else str(text)
    words = re.findall(r"\w+", t)
    letters = [c for c in t if c.isalpha()]
    return {"len": len(t), "words": len(words), "media": bool(photo or video), "photo": bool(photo),
            "video": bool(video), "hashtags": len(re.findall(r"#\w+", t)), "emoji": len(EMOJI.findall(t)),
            "links": len(re.findall(r"https?://|t\.me/|max\.ru/", t)), "ad": bool(AD_WORDS.search(t)),
            "question": "?" in t, "caps": (sum(c.isupper() for c in letters) / len(letters)) if letters else 0}


def _median(vals):
    return statistics.median(vals) if vals else None


def _ratio(good, bad, need=3):
    """Во сколько раз посты «good» набирают больше «bad» (медианы); мало данных — None."""
    if len(good) < need or len(bad) < need:
        return None
    a, b = _median(good), _median(bad)
    return (a / b) if a and b else None


def best_time(history, min_posts=2):
    """(день недели 0-6 или None, час или None) — где у канала в среднем больше просмотров."""
    by_hour, by_day = {}, {}
    for p in history:
        lt = time.localtime(p["t"])
        by_hour.setdefault(lt.tm_hour, []).append(p["views"])
        by_day.setdefault(lt.tm_wday, []).append(p["views"])
    def pick(groups):
        ok = {k: sum(v) / len(v) for k, v in groups.items() if len(v) >= min_posts}
        return max(ok, key=ok.get) if ok else None
    return pick(by_day), pick(by_hour)


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf")


def _valid(p):
    """Пост истории с понятными временем и просмотрами (мусор из MAX или файла — пропускаем)."""
    return isinstance(p, dict) and _num(p.get("t")) and 0 < p["t"] < 4.1e9 and _num(p.get("views")) \
        and p["views"] >= 0


def _has_media(p):
    return bool(p.get("img") or p.get("video"))


def predict(text, photo=False, video=False, history=(), now=None, quality=None):
    """history — [{"t", "views", "text", "img", "video"}] постов канала. quality — «живость» поста 0-10 от бота
    (post_quality); нет — считаем сами. Ожидаемые просмотры: медиана похожих постов канала (с медиа / без),
    поправки на длину и «рекламность» — во столько раз, во сколько у ЭТОГО канала такие посты заходят хуже/лучше.
    Возвращает {"score", "reach": (от, до) | None, "median", "best": (день, час), "tips", "why", "n", "features"}."""
    hist = sorted((p for p in history or () if _valid(p)), key=lambda p: -p["t"])[:60]
    f = features(text, photo, video)
    base = _median([p["views"] for p in hist])
    factor, why, tips = 1.0, [], []
    if hist and base:
        media_v = [p["views"] for p in hist if _has_media(p)]
        text_v = [p["views"] for p in hist if not _has_media(p)]
        same = media_v if f["media"] else text_v
        if len(same) >= 3:                             # отталкиваемся от похожих постов канала
            factor *= _median(same) / base
        m = _ratio(media_v, text_v)
        if m and m > 1.15:
            if f["media"]:
                why.append(f"посты с фото/видео у канала набирают ×{m:.1f} к текстовым".replace(".", ","))
            else:
                tips.append(f"Добавь фото или видео — такие посты у тебя набирают ×{m:.1f}".replace(".", ","))
        elif m is None and not f["media"]:             # сравнить не с чем — общий совет
            tips.append("Добавь фото или видео — посты с картинкой обычно смотрят лучше")
        long_ = [p["views"] for p in hist if len(p.get("text") or "") > 600]
        short = [p["views"] for p in hist if len(p.get("text") or "") <= 600]
        lr = _ratio(long_, short)
        if lr and f["len"] > 600:
            factor *= lr
            if lr < 0.9:
                tips.append("Сократи текст: длинные посты у тебя смотрят хуже коротких")
            elif lr > 1.15:
                why.append("длинные посты у канала заходят лучше коротких")
        ad = [p["views"] for p in hist if AD_WORDS.search(p.get("text") or "")]
        plain = [p["views"] for p in hist if not AD_WORDS.search(p.get("text") or "")]
        if f["ad"]:
            ar = _ratio(ad, plain)
            factor *= ar if ar else 0.8
            tips.append("Похоже на рекламу (ссылки, «подпишись», промокод) — такие посты набирают меньше")
    elif not f["media"]:
        tips.append("Добавь фото или видео — посты с картинкой обычно смотрят лучше")
    if f["len"] < 40 and not f["media"]:
        tips.append("Слишком коротко — добавь пару строк сути или картинку")
    if f["caps"] > 0.5 and f["len"] > 30:
        tips.append("Много КАПСА — читается как крик, лучше обычными буквами")
    if f["hashtags"] > 5:
        tips.append("Много хэштегов — оставь 2–3 главных")
    if f["question"]:
        why.append("вопрос к читателям — повод ответить и вернуться")
    q = quality if quality is not None else _quality(f)
    reach = round(base * factor) if base else None
    score = q * 0.7 + 3 * min(1.0, max(0.0, factor - 0.5))      # 7 — сам пост, 3 — как такие заходят у канала
    score = max(1, min(10, int(score + 0.5)))
    return {"score": score, "reach": (round(reach * 0.75), round(reach * 1.25)) if reach is not None else None,
            "median": round(base) if base else None, "best": best_time(hist), "tips": tips[:4], "why": why[:3],
            "n": len(hist), "features": f}


def _quality(f):
    """Запасная «живость» поста 0-10 (у бота своя — post_quality)."""
    q = 4
    q += 2 if f["media"] else 0
    q += 1 if 80 <= f["len"] <= 900 else 0
    q += 1 if f["emoji"] else 0
    q += 1 if f["question"] else 0
    q -= 2 if f["ad"] else 0
    return max(0, min(10, q))


def explain(p, channel=None):
    """Текст ответа в боте (по-русски)."""
    lines = [f"🔮 Прогноз поста{f' для «{channel}»' if channel else ''}", "", f"⭐ Оценка: {p['score']}/10"]
    if p["reach"]:
        lines.append(f"👁 Ожидаемо: {p['reach'][0]}–{p['reach'][1]} просмотров (обычно у канала — {p['median']})")
    else:
        lines.append("👁 Просмотры не прогнозирую: у канала пока нет истории (привяжи канал MAX и дай боту поработать)")
    day, hour = p["best"]
    if hour is not None:
        lines.append(f"⏰ Лучшее время: {WEEKDAYS[day] + ', ' if day is not None else ''}{hour:02d}:00")
    elif day is not None:
        lines.append(f"⏰ Лучший день: {WEEKDAYS[day]}")
    if p["why"]:
        lines += ["", "Почему так:"] + [f"• {w}" for w in p["why"]]
    if p["tips"]:
        lines += ["", "Как сделать лучше:"] + [f"• {t}" for t in p["tips"]]
    if p["n"]:
        lines += ["", f"По {p['n']} последним постам канала."]
    return "\n".join(lines)
