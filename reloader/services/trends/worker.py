"""Одна проверка канала на тренды (scan) и фоновый обход каналов каждые 2–3 часа (TrendWorker).

scan: слова из последних постов → поиск свежих новостей → убираем то, что уже отправляли или ИИ уже отклонил →
одинаковые новости разных изданий — одной строкой («ещё N источников» — признак вирусности) → строгий вопрос ИИ →
«NO» — запоминаем, что показали, и молчим; «YES [n]» — сигнал админу и запоминаем, что отправили.
Всё нужное приходит параметрами (поиск, ИИ, отправка, память) — бот подставляет настоящие, проверки — подделки.

TrendWorker: свой поток; у каждой темы своё время следующей проверки (через 2–3 часа, случайно — чтобы темы
не шли пачкой и поисковик не обиделся). Время хранится в trends_state.json: перезапуск бота не запускает
проверку всех тем сразу. Любая ошибка темы — в журнал, обход продолжается; поток не падает никогда."""
import itertools
import random
import threading
import time
from dataclasses import dataclass, field

from reloader.services.trends import text as T
from reloader.services.trends.alerts import build_prompt, format_alert, parse_answer
from reloader.services.trends.keywords import extract_keywords, make_queries, niche_stems

MAX_AGE = 48 * 3600          # новости старше двух суток — не тренд


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf")


class NoAI(Exception):
    """ИИ недоступен: нет ключей, все ключи исчерпаны или кончился дневной лимит на тренды."""


@dataclass
class Job:
    scope: str                       # кому сигнал (кабинет покупателя / админ) — у каждого своя память
    theme: str                       # id темы
    channel: str                     # название канала для сигнала
    texts: list                      # последние посты канала
    manual: list = field(default_factory=list)     # свои слова админа для поиска (вместо слов из постов)
    signature: str = ""              # подпись темы — не слово для поиска
    lang: str = "ru"


def merge(batches, now, max_age=MAX_AGE):
    """Новости всех запросов по очереди (первая из каждого, вторая из каждого…), без повторов адресов и старья."""
    out, seen = [], set()
    for row in itertools.zip_longest(*batches):
        for it in row:
            if not it or it["canon"] in seen:
                continue
            if it.get("t") and now - it["t"] > max_age:
                continue
            seen.add(it["canon"])
            out.append(it)
    return out


def group(items):
    """Одна новость у разных изданий — одна группа: [{"item": первая, "others": [остальные]}]."""
    groups = []
    for it in items:
        st = T.stems(it["title"])
        for g in groups:
            if T.near_duplicate(st, g["st"]):
                g["others"].append(it)
                break
        else:
            groups.append({"item": it, "st": st, "others": []})
    return groups


def snippets(texts, n=5, size=160):
    out = []
    for t in texts:
        line = " ".join(T.clean(t).split())
        if line:
            out.append(line[:size])
        if len(out) >= n:
            break
    return out


def scan(job, *, search, ask, send, store, now=None, max_items=8):
    """Одна проверка канала. search(запрос, язык) -> ([новости], ошибка); ask(задание) -> ответ ИИ (NoAI —
    ИИ нет); send(текст, новость) -> отправлено ли; store — AlertStore. Возвращает итог:
    {"verdict": yes|no|nothing-new|empty|no-words|no-ai|ai-error|search-error|send-error, "found", "new",
     "words", "queries", "item", "alert", "error", "t"}."""
    now = now or time.time()
    res = {"theme": job.theme, "t": now, "verdict": "", "words": [], "queries": [], "found": 0, "new": 0,
           "item": None, "alert": "", "error": ""}
    kws = extract_keywords(job.texts, job.signature)
    res["words"] = list(job.manual)[:8] if job.manual else [k["word"] for k in kws]
    res["queries"] = queries = make_queries(kws, job.manual)
    if not queries:
        res["verdict"] = "no-words"
        return res
    batches, errors = [], []
    for q in queries:
        try:
            items, err = search(q, job.lang)
        except Exception as e:                     # поиск не должен ронять проверку
            items, err = [], f"{type(e).__name__}: {e}"
        batches.append([it for it in items or [] if it and it.get("canon") and it.get("title")])
        if err:
            errors.append(str(err))
    found = merge(batches, now)
    res["found"] = len(found)
    if not found:
        res.update(verdict="search-error" if errors else "empty", error=(errors[0] if errors else "")[:200])
        return res
    if store.needs_warmup(job.scope):              # память об отправленном потеряна: эту проверку — молча
        store.warmed(job.scope, found)
        store.save()
        res["verdict"] = "warmup"
        return res
    fresh = store.fresh(job.scope, found)
    niche = niche_stems(kws, job.manual)
    topical = [it for it in fresh if T.on_topic(it["title"], niche)]   # случайные упоминания — мимо ИИ
    groups = group(topical)[:max_items]
    res["new"] = sum(1 + len(g["others"]) for g in groups)
    if not groups:
        res["verdict"] = "off-topic" if fresh else "nothing-new"
        return res
    shown = [dict(g["item"], more=len(g["others"])) for g in groups]
    prompt = build_prompt(job.channel, res["words"], snippets(job.texts), shown, store.recent_titles(job.scope), now)
    try:
        answer = ask(prompt)
    except NoAI as e:
        res.update(verdict="no-ai", error=str(e)[:200])
        return res
    except Exception as e:
        res.update(verdict="ai-error", error=f"{type(e).__name__}: {e}"[:200])
        return res
    v = parse_answer(answer, shown)
    everything = [it for g in groups for it in [g["item"]] + g["others"]]
    if not v.yes:
        store.mark_seen(job.scope, everything)     # эти уже показали ИИ — в следующий раз не спрашиваем
        store.save()
        res.update(verdict="no", reason=v.reason)
        return res
    chosen = [groups[i] for i in v.picks]
    main = chosen[0]["item"]
    try:
        ok = bool(send(format_alert(job.channel, v.alert, main), main))
    except Exception as e:
        ok, res["error"] = False, f"{type(e).__name__}: {e}"[:200]
    if not ok:                                     # не дошло — ничего не помечаем: попробуем в следующий раз
        res["verdict"] = "send-error"
        return res
    store.mark_sent(job.scope, [it for g in chosen for it in [g["item"]] + g["others"]], v.alert, job.theme)
    store.mark_seen(job.scope, everything)
    store.save()
    res.update(verdict="yes", alert=v.alert, score=v.score,
               item={"title": main["title"], "url": main["url"], "source": main.get("source", "")})
    return res


class TrendState:
    """trends_state.json: {"themes": {id темы: {"next", "last", "result"}}, "ai": {"day", "n"}}."""

    def __init__(self, path, save=None, load=None, clock=time.time):
        if save is None or load is None:
            from reloader.core.files import load_json, save_json
            save, load = save or save_json, load or load_json
        self.path, self._save, self.clock = path, save, clock
        self.lock = threading.RLock()
        data = load(path, {})
        data = data if isinstance(data, dict) else {}           # файл испорчен (не тот вид) — начинаем с чистого
        themes = data.get("themes") if isinstance(data.get("themes"), dict) else {}
        self.themes = {str(k): dict(v) for k, v in themes.items() if isinstance(v, dict)}
        ai = data.get("ai") if isinstance(data.get("ai"), dict) else {}
        self.ai = {"day": str(ai.get("day") or ""), "n": ai["n"]} if _num(ai.get("n")) else {}

    def next(self, tid):
        with self.lock:
            nxt = (self.themes.get(str(tid)) or {}).get("next")
            return nxt if _num(nxt) else None                   # мусор вместо времени — как «ещё не назначено»

    def schedule(self, tid, when):
        with self.lock:
            self.themes.setdefault(str(tid), {})["next"] = when

    def record(self, tid, res):
        keep = ("verdict", "found", "new", "words", "queries", "item", "alert", "error", "t", "channel", "reason",
                "score")
        with self.lock:
            rec = self.themes.setdefault(str(tid), {})
            rec["last"] = res.get("t") or self.clock()
            rec["result"] = {k: res.get(k) for k in keep if k in res}

    def last(self, tid):
        with self.lock:
            return dict((self.themes.get(str(tid)) or {}).get("result") or {})

    def forget_missing(self, ids):
        keep = {str(x) for x in ids}
        with self.lock:
            for tid in [t for t in self.themes if t not in keep]:
                del self.themes[tid]

    def ai_take(self, limit, day):
        """Можно ли ещё раз спросить ИИ сегодня (и засчитать). Лимит бережёт ключи ИИ для пересказа постов."""
        with self.lock:
            if self.ai.get("day") != day:
                self.ai = {"day": day, "n": 0}
            if self.ai["n"] >= limit:
                return False
            self.ai["n"] += 1
            return True

    def ai_used(self, day):
        with self.lock:
            return self.ai.get("n", 0) if self.ai.get("day") == day else 0

    def save(self):
        with self.lock:
            self._save(self.path, {"themes": {k: dict(v) for k, v in self.themes.items()}, "ai": dict(self.ai)})


class TrendWorker:
    """Фоновый обход: targets() -> [id тем, которые проверяем]; run(id) -> итог scan. Тема проверяется раз
    в interval (случайно в пределах, сек); новая тема — через first после появления (не сразу при запуске)."""

    def __init__(self, targets, run, state, interval=(2 * 3600, 3 * 3600), first=(300, 1200), tick=60,
                 clock=time.time, rnd=None, log=print):
        self.targets, self.run, self.state = targets, run, state
        self.interval, self.first, self.tick = interval, first, tick
        self.clock, self.rnd, self.log = clock, rnd or random.Random(), log
        self._stop = threading.Event()
        self._errors = {}                          # текст ошибки -> когда последний раз писали в журнал
        self.thread = None

    def _report(self, where, e):
        msg = f"🔥 Тренды: {where}: {type(e).__name__}: {e}"[:300]
        if self.clock() - self._errors.get(msg, 0) > 3600:   # одна и та же ошибка — не чаще раза в час
            self._errors[msg] = self.clock()
            try:
                self.log(msg)
            except Exception:
                pass

    def step(self):
        """Один проход: проверяем темы, у которых подошло время. [(id, итог)] — что проверили."""
        try:
            ids = [str(x) for x in (self.targets() or [])]
        except Exception as e:
            self._report("список каналов", e)
            return []
        done = []
        for tid in ids:
            if self._stop.is_set():
                break
            nxt = self.state.next(tid)
            if nxt is None:
                self.state.schedule(tid, self.clock() + self.rnd.uniform(*self.first))
                continue
            if self.clock() < nxt:
                continue
            self.state.schedule(tid, self.clock() + self.rnd.uniform(*self.interval))   # до проверки: сбой не
            try:                                                                         # зациклит её
                res = self.run(tid) or {}
            except Exception as e:
                self._report(f"тема {tid}", e)
                res = {"verdict": "error", "error": f"{type(e).__name__}: {e}"[:200], "t": self.clock()}
            self.state.record(tid, res)
            done.append((tid, res))
            try:
                self.state.save()
            except Exception as e:
                self._report("запись trends_state.json", e)
        self.state.forget_missing(ids)
        try:
            self.state.save()
        except Exception as e:
            self._report("запись trends_state.json", e)
        return done

    def run_forever(self):
        while not self._stop.is_set():
            try:
                self.step()
            except BaseException as e:             # поток трендов не должен умереть ни от чего
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                self._report("обход", e)
            self._stop.wait(self.tick)

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self._stop.clear()
            self.thread = threading.Thread(target=self.run_forever, daemon=True, name="trends")
            self.thread.start()
        return self.thread

    def stop(self):
        self._stop.set()
