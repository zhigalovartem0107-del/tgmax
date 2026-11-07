"""Слова для поиска трендов — из последних постов канала.

Убираем то, что повторяется почти в каждом посте строкой целиком (подписи, «Подпишись: …»), и подпись самой
темы. Считаем, в скольких постах встречается слово (а не сколько раз всего — один длинный пост не перевешивает),
хэштеги и имена собственные (Roblox, Brawl Stars) весят больше, устойчивые пары слов («brawl stars») — тоже."""
import re
from collections import Counter

from reloader.services.trends import text as T

_SPLIT = re.compile(r"[\n.!?;:•|«»\"“”()\[\]]+")


def _line_key(line):
    return " ".join(T.norm(w) for w in T.words(line))


def strip_boilerplate(texts, signature=""):
    """Тексты без строк-подписей: строка, которая есть в половине постов и больше (от 4 постов), — шаблон
    канала; подпись темы убираем всегда."""
    sig = {_line_key(x) for x in T.clean(signature).splitlines()} - {""}
    split = [T.clean(t).splitlines() for t in texts]
    counts = Counter(k for lines in split for k in {_line_key(x) for x in lines} - {""})
    n = len(texts)
    common = {k for k, c in counts.items() if n >= 4 and c >= max(3, n * 0.5)}
    return ["\n".join(x for x in lines if _line_key(x) not in common | sig) for lines in split]


def extract_keywords(texts, signature="", top=8):
    """[{"word", "score", "df", "pair"}] — главные слова канала, лучшие первыми. pair — это пара слов."""
    texts = [t for t in strip_boilerplate([t for t in texts if t and str(t).strip()], signature) if t.strip()]
    if not texts:
        return []
    n = len(texts)
    uni, bi = {}, {}
    for text in texts:
        seen_u, seen_b = set(), set()
        for chunk in _SPLIT.split(text):
            prev = None
            raw = T.words(chunk)
            for i, w in enumerate(raw):
                low = T.norm(w)
                if len(low) < 3 or low in T.STOP or low in T.NOISE or low.isdigit():
                    prev = None
                    continue
                s = T.stem(low)
                rec = uni.setdefault(s, {"df": 0, "n": 0, "forms": Counter(), "tag": 0, "cap": 0, "lat": 0})
                rec["n"] += 1
                rec["forms"][low] += 1
                rec["tag"] += ("#" + w) in chunk
                rec["cap"] += i > 0 and w[:1].isupper() and not w.isupper()
                rec["lat"] += not T.is_cyr(low)
                if s not in seen_u:
                    rec["df"] += 1
                    seen_u.add(s)
                if prev is not None and prev[0] != s:
                    key = (prev[0], s)
                    b = bi.setdefault(key, {"df": 0, "forms": Counter()})
                    b["forms"][f"{prev[1]} {low}"] += 1
                    if key not in seen_b:
                        b["df"] += 1
                        seen_b.add(key)
                prev = (s, low)
    need = 2 if n >= 3 else 1

    def weight(rec):
        w = 1.0
        if rec["tag"]:
            w += 1.0                                   # хэштег — автор сам назвал тему
        if rec["cap"] * 2 >= rec["n"]:
            w += 0.5                                   # чаще с большой буквы посреди фразы — имя, бренд, игра
        if rec["lat"] * 2 >= rec["n"]:
            w += 0.3                                   # латиница в русском канале — обычно название
        return w

    cands = []
    for s, rec in uni.items():
        if rec["df"] >= need:
            extra = min(rec["n"] - rec["df"], 2 * rec["df"])   # повторы внутри постов — немного, не больше
            score = (rec["df"] + 0.25 * extra) * weight(rec)       # (один длинный пост не перевешивает)
            cands.append((score, rec["forms"].most_common(1)[0][0], rec["df"], (s,)))
    for (a, b), rec in bi.items():
        if rec["df"] >= max(2, need):
            score = rec["df"] * 1.6 * (weight(uni[a]) + weight(uni[b])) / 2
            cands.append((score, rec["forms"].most_common(1)[0][0], rec["df"], (a, b)))
    cands.sort(key=lambda c: (-c[0], -c[2], -len(c[1]), c[1]))   # при равенстве — длиннее (конкретнее)
    out, used = [], set()
    for score, form, df, parts in cands:
        if len(parts) == 1 and parts[0] in used:
            continue                                   # слово уже есть в выбранной паре («brawl» при «brawl stars»)
        if len(parts) == 2 and set(parts) <= used:
            continue
        out.append({"word": form, "score": round(score, 2), "df": df, "pair": len(parts) == 2})
        used.update(parts)
        if len(out) >= top:
            break
    return out


def _specific(k):
    """Слово само по себе — хороший запрос: пара слов или название (латиница/хэштег), а не «обновление»."""
    return k["pair"] or not T.is_cyr(k["word"])


def make_queries(keywords, manual=(), n=3):
    """Запросы к поиску: свои слова админа — как есть; иначе главное слово канала, оно же с другими словами
    (общие слова вроде «обновление» отдельно не ищем — найдётся всё подряд)."""
    manual = [m.strip() for m in manual or () if m and m.strip()]
    if manual:
        return list(dict.fromkeys(manual))[:n]
    if not keywords:
        return []
    top = keywords[0]["word"]
    top_st = T.stems(top)
    out = [top]
    for k in keywords[1:]:
        if len(out) >= n:
            break
        w, st = k["word"], T.stems(k["word"])
        if st <= top_st:
            continue                                   # то же главное слово («brawl» при «brawl stars»)
        q = w if top_st <= st or _specific(k) else f"{top} {w}"
        if q not in out:
            out.append(q)
    return [q[:80] for q in out]
