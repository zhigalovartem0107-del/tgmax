"""sent_alerts.json — память трендов: что уже отправили (одна новость — один раз) и что ИИ уже отклонил.

{"sent": {кому: [{"canon", "url", "title", "tk", "stems", "sh", "t", "theme", "alert"}]},
 "seen": {кому: {адрес новости: когда показали ИИ}},
 "warmup": {"since": когда потеряли память, "done": [кому уже провели тихую проверку]}}

«кому» (scope) — получатель: кабинет покупателя или админ бота. Новость считается «уже было», если совпало:
1) адрес без мусора (http/https, www., m., amp, utm-метки, якорь);
2) заголовок без регистра и знаков (зеркала, агрегаторы);
3) заголовок по смыслу — основы слов (near_duplicate: «Roblox заблокировали в России» = «В России заблокировали
   Roblox»);
4) текст — отпечаток троек слов заголовка и анонса (перепечатка той же статьи на другом сайте).
Отправленное помним 90 дней и не больше 1000 сигналов на получателя (и 20000 записей на всех); отклонённое —
3 дня. Файл пишется с резервной копией. Если испортились и файл, и копия — бот не знает, что уже присылал:
первую проверку каждого получателя в ближайшие 3 дня проводит молча (запоминает найденное, сигнала нет),
чтобы не прислать старое второй раз."""
import os
import threading
import time

from reloader.services.trends import text as T
from reloader.services.trends.search import canonical_url

KEEP_ALERTS = 1000          # сигналов на получателя
KEEP_RECORDS = 20000        # записей «отправлено» на всех получателей (файл не раздувается)
KEEP_SEEN = 5000            # отклонённых новостей на получателя


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


def _clean_record(r):
    """Запись «отправлено» из файла: битая — None, неполная — дополняем (старые версии файла)."""
    if not isinstance(r, dict):
        return None
    canon, t = r.get("canon"), r.get("t")
    if not isinstance(canon, str) or not canon.strip() or not _num(t):
        return None
    title = str(r.get("title") or "")[:220]
    stems = r.get("stems")
    if not isinstance(stems, list) or not all(isinstance(x, str) for x in stems):
        stems = sorted(T.stems(title))
    sh = r.get("sh")
    if not isinstance(sh, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in sh):
        sh = T.shingles(title)
    return {"canon": canon[:600], "url": str(r.get("url") or "")[:1500], "title": title,
            "tk": str(r.get("tk") or T.title_key(title))[:220], "stems": stems[:40], "sh": sh[:32], "t": float(t),
            "theme": str(r.get("theme") or "")[:40], "alert": str(r.get("alert") or "")[:300],
            "aid": str(r.get("aid") or f"{float(t):.3f}|{r.get('theme') or ''}")[:60]}


class AlertStore:
    def __init__(self, path, save=None, load=None, clock=time.time, keep_days=90, seen_days=3,
                 keep_alerts=KEEP_ALERTS, keep_records=KEEP_RECORDS, keep_seen=KEEP_SEEN, log=print):
        if save is None or load is None:
            from reloader.core.files import load_json_safe, save_json
            save, load = save or save_json, load or load_json_safe
        self.path, self._save, self.clock, self.log = path, save, clock, log
        self.keep, self.seen_ttl = keep_days * 86400, seen_days * 86400
        self.keep_alerts, self.keep_records, self.keep_seen = keep_alerts, keep_records, keep_seen
        self.lock = threading.RLock()
        self._n = 0                                      # номер сигнала: записи одного сигнала — один aid
        existed = os.path.exists(path) or os.path.exists(path + ".bak")
        data = load(path, None)
        if not self._shape_ok(data) and os.path.exists(path + ".bak"):   # JSON читается, но не того вида —
            try:                                                          # берём резервную копию
                import json
                with open(path + ".bak", encoding="utf-8") as f:
                    bak = json.load(f)
                if self._shape_ok(bak):
                    data = bak
                    self._say(f"💾 {path}: файл не того вида — поднял данные из резервной копии")
            except Exception:
                pass
        if not self._shape_ok(data):
            data = {}
            if existed:                                # и файл, и копия испорчены: память потеряна
                data["warmup"] = {"since": self.clock(), "done": []}
                self._say("💾 Тренды: память об отправленных сигналах испорчена вместе с копией — первую проверку "
                          "каждого канала проведу молча, чтобы не прислать старое второй раз")
        self.sent = {}
        for k, v in (data.get("sent") or {}).items() if isinstance(data.get("sent"), dict) else ():
            box = [c for c in (_clean_record(r) for r in (v if isinstance(v, list) else ())) if c]
            if box:
                self.sent[str(k)] = box
        self.seen = {str(k): {str(u): float(t) for u, t in v.items() if _num(t)}
                     for k, v in ((data.get("seen") or {}).items() if isinstance(data.get("seen"), dict) else ())
                     if isinstance(v, dict)}
        w = data.get("warmup")
        self.warmup = ({"since": float(w["since"]), "done": [str(x) for x in w.get("done") or [] if x]}
                       if isinstance(w, dict) and _num(w.get("since")) else None)

    @staticmethod
    def _shape_ok(data):
        """Файл того вида: словарь, «sent» и «seen» — словари (или их нет)."""
        return isinstance(data, dict) and isinstance(data.get("sent", {}), dict) \
            and isinstance(data.get("seen", {}), dict)

    def _say(self, msg):
        try:
            self.log(msg)
        except Exception:
            pass

    @staticmethod
    def _canon(item):
        return item.get("canon") or canonical_url(item.get("url"))

    # ---------- тихая проверка после потери памяти ----------
    def needs_warmup(self, scope):
        with self.lock:
            w = self.warmup
            return bool(w) and self.clock() - w["since"] < self.seen_ttl and scope not in w["done"]

    def warmed(self, scope, items):
        """Тихая проверка получателя: всё найденное — «уже видели», сигнала нет."""
        with self.lock:
            self.mark_seen(scope, items)
            if self.warmup and scope not in self.warmup["done"]:
                self.warmup["done"].append(scope)

    # ---------- уже было? ----------
    def known(self, scope, item):
        """Почему новость не новая: "sent" (этот адрес уже отправляли), "similar" (та же новость с другого
        адреса — по заголовку или тексту), "seen" (ИИ её уже видел и отклонил) или None — новая."""
        canon = self._canon(item)
        title = item.get("title") or ""
        tk, st = T.title_key(title), T.stems(title)
        sh = T.shingles(f"{title} {item.get('body') or ''}")
        with self.lock:
            sent = self.sent.get(scope, [])
            if any(r["canon"] == canon for r in sent):
                return "sent"
            for r in sent:
                if (tk and r["tk"] == tk) or T.near_duplicate(st, r["stems"]) or T.same_text(sh, r["sh"]):
                    return "similar"
            if canon in self.seen.get(scope, {}):
                return "seen"
        return None

    def fresh(self, scope, items):
        return [it for it in items if self.known(scope, it) is None]

    # ---------- запомнить ----------
    def mark_seen(self, scope, items):
        now = self.clock()
        with self.lock:
            box = self.seen.setdefault(scope, {})
            for it in items:
                box[self._canon(it)] = now

    def mark_sent(self, scope, items, alert="", theme=""):
        now = self.clock()
        with self.lock:
            self._n += 1
            aid = f"{now:.3f}|{theme}|{self._n}"
            box = self.sent.setdefault(scope, [])
            have = {r["canon"] for r in box}
            for it in items:
                canon = self._canon(it)
                if canon in have:
                    continue
                have.add(canon)
                title = str(it.get("title") or "")[:220]
                box.append({"canon": canon, "url": str(it.get("url") or "")[:1500], "title": title,
                            "tk": T.title_key(title), "stems": sorted(T.stems(title))[:40],
                            "sh": T.shingles(f"{title} {it.get('body') or ''}"), "t": now,
                            "theme": str(theme)[:40], "alert": (alert or "")[:300], "aid": aid})
            self.seen.setdefault(scope, {}).update({self._canon(it): now for it in items})

    # ---------- для экрана и задания ИИ ----------
    def recent_titles(self, scope, n=10):
        """Заголовки последних отправленных — ИИ подсказываем, что это уже было."""
        with self.lock:
            return [r["title"] for r in sorted(self.sent.get(scope, []), key=lambda r: -r["t"])[:n]]

    def stats(self, scope):
        """(сколько сигналов отправлено этому получателю, последний сигнал или None)."""
        with self.lock:
            box = self.sent.get(scope, [])
            last = max(box, key=lambda r: r["t"]) if box else None
            return len({r["aid"] for r in box}), (dict(last) if last else None)

    # ---------- уборка и запись ----------
    def prune(self):
        now = self.clock()
        with self.lock:
            for scope in list(self.sent):
                box = sorted((r for r in self.sent[scope] if now - r["t"] < self.keep), key=lambda r: r["t"])
                keep = set()                                      # последние keep_alerts сигналов
                for r in reversed(box):
                    keep.add(r["aid"])
                    if len(keep) >= self.keep_alerts:
                        break
                box = [r for r in box if r["aid"] in keep]
                if box:
                    self.sent[scope] = box
                else:
                    del self.sent[scope]
            total = sum(len(v) for v in self.sent.values())
            if total > self.keep_records:                         # общий предел: ровно keep_records самых новых
                keep = {id(r) for r in sorted((r for v in self.sent.values() for r in v),
                                              key=lambda r: r["t"], reverse=True)[:self.keep_records]}
                for scope in list(self.sent):
                    self.sent[scope] = [r for r in self.sent[scope] if id(r) in keep]
                    if not self.sent[scope]:
                        del self.sent[scope]
            for scope in list(self.seen):
                box = {u: t for u, t in self.seen[scope].items() if now - t < self.seen_ttl}
                if len(box) > self.keep_seen:
                    box = dict(sorted(box.items(), key=lambda kv: kv[1])[-self.keep_seen:])
                if box:
                    self.seen[scope] = box
                else:
                    del self.seen[scope]
            if self.warmup and now - self.warmup["since"] >= self.seen_ttl:
                self.warmup = None                                # 3 дня прошло — старые новости уже не найдутся

    def save(self):
        with self.lock:                                  # запись под замком: старый снимок не затрёт новый
            self.prune()
            data = {"sent": {k: list(v) for k, v in self.sent.items()},
                    "seen": {k: dict(v) for k, v in self.seen.items()}}
            if self.warmup:
                data["warmup"] = dict(self.warmup)
            self._save(self.path, data, backup=True)
