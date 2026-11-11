"""⚡ Оценка потенциала канала (0–100) и 📊 охват рекламного поста — по тому, что о канале видно без прав админа.

Части оценки:
- рост — прирост подписчиков за неделю, в % (по снимкам, которые бот копит сам);
- вовлечённость — медиана просмотров поста к подписчикам (ER);
- регулярность — сколько постов в день и давно ли был последний;
- охват — размер аудитории (по логарифмической шкале: 1 тыс. → ~35, 10 тыс. → ~60, 100 тыс. → ~85).
Чего не знаем — не выдумываем: часть = None и в итог не идёт (вес делится между остальными)."""
import math
import statistics
import time

WEIGHTS = {"growth": 0.30, "engagement": 0.35, "activity": 0.15, "reach": 0.20}
LABELS = ((70, "Высокий"), (45, "Средний"), (0, "Низкий"))
DAY = 86400


def _num(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def _scale(x, pts):
    """Кусочно-линейная шкала: pts — [(x, баллы)] по возрастанию x."""
    if x <= pts[0][0]:
        return float(pts[0][1])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return float(pts[-1][1])


def _clean_points(points, now):
    out = []
    for p in points or ():
        if isinstance(p, (tuple, list)) and len(p) == 2 and _num(p[0]) is not None and _num(p[1]) is not None \
                and p[1] >= 0 and now - 30 * DAY <= p[0] <= now + 3600:
            out.append((float(p[0]), float(p[1])))
    return sorted(out)


def growth_pct(points, now):
    """Прирост подписчиков в % за неделю (по снимкам за 30 дней; меньше суток истории — None)."""
    pts = _clean_points(points, now)
    if len(pts) < 2 or pts[0][1] <= 0:
        return None
    span = (pts[-1][0] - pts[0][0]) / DAY
    if span < 1:
        return None
    return (pts[-1][1] - pts[0][1]) / pts[0][1] * 100 * 7 / span


def _growth(points, now):
    g = growth_pct(points, now)
    return None if g is None else _scale(g, ((-2, 0), (0, 40), (2, 70), (5, 100)))


def _engagement(subs, views):
    vals = [v for v in (_num(x) for x in views or ()) if v is not None and v >= 0]
    if not vals or not subs:
        return None
    er = statistics.median(vals) / subs * 100
    return _scale(er, ((0, 0), (5, 35), (10, 55), (20, 80), (30, 100)))


def _activity(times, now):
    ts = sorted(t for t in (_num(x) for x in times or ()) if t is not None and 0 < t <= now + 3600)
    if not ts:
        return None
    recent = [t for t in ts if t >= now - 30 * DAY]
    if not recent:
        return 0.0                                  # постов не было месяц
    if len(recent) == 1:
        rate = 1 / 30
    else:
        rate = (len(recent) - 1) / max(1.0, (recent[-1] - recent[0]) / DAY)
    score = _scale(rate, ((0, 0), (1 / 7, 20), (0.5, 45), (1, 65), (3, 90), (4, 100)))
    if now - recent[-1] > 7 * DAY:                   # последний пост — больше недели назад
        score *= 0.5
    return score


def _reach(subs):
    return max(0.0, min(100.0, (math.log10(max(subs, 1)) - 1.6) / (6 - 1.6) * 100))


def label_of(score):
    if score is None:
        return None
    return next(name for edge, name in LABELS if score >= edge)


def score(subs, points=(), views=(), times=(), now=None):
    """{"score": 0–100 или None, "label": «Высокий»/«Средний»/«Низкий», "parts": {часть: 0–100 или None},
    "notes": [что не посчитали и почему]}. points — снимки [(время, подписчиков)], views — просмотры постов,
    times — время постов."""
    now = time.time() if _num(now) is None else now
    subs = _num(subs)
    if subs is None:
        last = _clean_points(points, now)
        subs = last[-1][1] if last else None
    if subs is None or subs < 0:
        return {"score": None, "label": None, "parts": dict.fromkeys(WEIGHTS), "notes":
                ["MAX не показал число подписчиков — оценить канал не из чего"]}
    parts = {"growth": _growth(points, now), "engagement": _engagement(subs, views),
             "activity": _activity(times, now), "reach": _reach(subs)}
    known = {k: v for k, v in parts.items() if v is not None}
    total = sum(WEIGHTS[k] * v for k, v in known.items()) / sum(WEIGHTS[k] for k in known)
    notes = []
    if parts["growth"] is None:
        notes.append("рост посчитаю, когда накопится история за сутки — бот смотрит каждый час")
    if parts["engagement"] is None:
        notes.append("просмотров постов не видно — вовлечённость не считаю")
    if parts["activity"] is None:
        notes.append("дат постов не видно — регулярность не считаю")
    result = int(round(total))
    return {"score": result, "label": label_of(result),
            "parts": {k: (None if v is None else int(round(v))) for k, v in parts.items()}, "notes": notes}


def ad_reach(subs, views):
    """Охват рекламного поста по просмотрам последних постов: медиана и вилка (0,8–1,1 медианы), ER — медиана
    к подписчикам, %. Нет просмотров — всё None."""
    vals = [v for v in (_num(x) for x in views or ()) if v is not None and v >= 0]
    subs = _num(subs)
    if not vals:
        return {"median": None, "low": None, "high": None, "er": None, "posts": 0}
    med = statistics.median(vals)
    return {"median": int(round(med)), "low": int(round(med * 0.8)), "high": int(round(med * 1.1)),
            "er": round(med / subs * 100, 1) if subs else None, "posts": len(vals)}
