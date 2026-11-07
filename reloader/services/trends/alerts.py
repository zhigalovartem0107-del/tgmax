"""ИИ-фильтр трендов: строгий вопрос («есть ли тут ВИРУСНОЕ или очень важное, что точно подходит каналу?»),
разбор ответа и текст сигнала админу.

Ответ ИИ разбираем недоверчиво: «NO»/«Нет» в начале — сигнала нет; «YES [n]» — сигнал про новость n. Если ИИ
забыл формат, сигнал засчитываем, только когда в ответе понятно, про какую новость речь, и нет отказа
(«ничего вирусного нет»). Сомнение — значит «нет»: лучше промолчать, чем дёрнуть админа зря."""
import re
import time
from dataclasses import dataclass, field

from reloader.services.trends import text as T

QUESTION = ("Is there a VIRAL or highly important news event here that perfectly fits this channel's niche? "
            "If NO, reply 'NO'. If YES, write a short 3-sentence alert explaining the trend and why the admin "
            "should post about it right now.")
RULES = ("Rules: be strict. Ordinary, minor, local, promotional, outdated or off-topic news means NO. "
         "Events listed as already sent are NOT new — NO for them too. "
         "Answer exactly NO, or put YES and the number of the news item in brackets on the first line "
         "(for example: YES [2]), then the 3-sentence alert in Russian, without greetings or markdown.")
_NO = re.compile(r"^\W*(no|нет)\b", re.I)
_YES = re.compile(r"^\W*(yes|да)\b", re.I)
_DENY = re.compile(r"(нет\s+(ни\s+)?(одной\s+)?(вирусн|важн|подходящ)|ничего\s+(вирусного|важного|подходящего)|"
                   r"не\s+(нашл|вижу|подходит)|no\s+(viral|important|relevant)|none\s+of|nothing\s+(viral|relevant))",
                   re.I)
_REF = re.compile(r"(?:\[\s*(\d{1,2})\s*\]|(?:№|#|news item|новост[ьи])\s*(\d{1,2})\b)", re.I)


@dataclass
class Verdict:
    yes: bool
    alert: str = ""
    picks: list = field(default_factory=list)       # номера новостей (с нуля), про которые сигнал


def ago(ts, now=None):
    if not ts:
        return ""
    mins = max(0, int(((now or time.time()) - ts) // 60))
    return f"{mins} min ago" if mins < 60 else f"{mins // 60} h ago"


def build_prompt(channel, keywords, posts, items, already=(), now=None):
    """Задание ИИ: канал (название, слова, последние посты), что уже отправляли, свежие новости и вопрос."""
    lines = ["You are a strict trend scout for the admin of a channel in the MAX messenger.", "",
             f"Channel: «{channel}»", "Niche keywords: " + (", ".join(keywords[:8]) or "—")]
    if posts:
        lines.append("Recent posts of the channel:")
        lines += [f"- {p}" for p in posts[:5]]
    if already:
        lines += ["", "Already sent to the admin earlier (not new):"] + [f"- {a}" for a in list(already)[:10]]
    lines += ["", "Fresh news found by search:"]
    for i, it in enumerate(items, 1):
        more = f"+{it['more']} more sources report the same" if it.get("more") else ""
        meta = ", ".join(x for x in (it.get("source"), ago(it.get("t"), now), more) if x)
        lines.append(f"[{i}] {it['title']}" + (f" ({meta})" if meta else ""))
        if it.get("body"):
            lines.append(f"    {it['body'][:220]}")
    lines += ["", QUESTION, "", RULES]
    return "\n".join(lines)


def _tidy(text, limit=700):
    """Текст сигнала: без разметки и ссылок на номера новостей, не длиннее 4 предложений и limit знаков."""
    t = re.sub(r"[*_`#>]+", "", str(text or ""))
    t = _REF.sub("", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" +([.,!?;:…])", r"\1", t)
    t = re.sub(r"\s*\n\s*", " ", t).strip(" -—:\n")
    parts = re.split(r"(?<=[.!?…])\s+", t)
    t = " ".join(parts[:4]).strip()
    return t if len(t) <= limit else t[:limit - 1].rsplit(" ", 1)[0] + "…"


def _match_titles(text, items):
    """Про какую новость текст: больше всего общих основ с заголовком (не меньше двух)."""
    st = T.stems(text)
    best, best_n = None, 1
    for i, it in enumerate(items):
        n = len(st & T.stems(it["title"]))
        if n > best_n:
            best, best_n = i, n
    return [best] if best is not None else []


def parse_answer(answer, items):
    """Verdict по ответу ИИ. items — новости, которые показали ИИ (по порядку, номера с 1)."""
    a = re.sub(r"[*_`]+", "", str(answer or "")).strip().strip("«»\"'")
    if not a:
        return Verdict(False)
    first, _, rest = a.partition("\n")
    if _YES.match(first):
        nums = [int(x or y) for x, y in _REF.findall(first)] or [int(x) for x in re.findall(r"\b(\d{1,2})\b", first)]
        body = rest.strip() or re.sub(r"^\W*(yes|да)\b[\s\W\d]*", "", first, flags=re.I)
    elif _NO.match(a) or _DENY.search(a[:200]):
        return Verdict(False)
    else:                                              # формат не соблюдён: только если ясно, про какую новость
        nums, body = [], a
    picks = list(dict.fromkeys(n - 1 for n in nums if 1 <= n <= len(items)))
    if not picks:
        picks = list(dict.fromkeys(int(x or y) - 1 for x, y in _REF.findall(body) if 1 <= int(x or y) <= len(items)))
    if not picks:
        picks = _match_titles(body, items)
    alert = _tidy(body)
    if not picks:
        if not _YES.match(first) or not items or len(alert) < 40:
            return Verdict(False)                      # непонятно, про что, и сказать нечего — молчим
        picks = list(range(len(items)))                # «YES» и внятный текст без номера: вся подборка
    return Verdict(True, alert, picks)


def format_alert(channel, alert, item):
    """Сообщение админу. Начинается с «🔥 Тренд для» — такие сообщения бот не убирает из чата через час."""
    head = f"🔥 Тренд для «{channel}»" if channel else "🔥 Тренд для твоего канала"
    body = alert or f"Похоже на вирусную новость по теме канала: «{item['title']}»."
    src = f"📰 {item['title']}" + (f" — {item['source']}" if item.get("source") else "")
    return "\n".join(x for x in (head, "", body, "", src, item.get("url") or "") if x is not None).strip()
