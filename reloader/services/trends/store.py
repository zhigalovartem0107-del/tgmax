"""sent_alerts.json — память трендов: что уже отправили (одна новость — один раз) и что ИИ уже отклонил.

{"sent": {кому: [{"canon", "url", "title", "stems", "t", "theme", "alert"}]},
 "seen": {кому: {адрес новости: когда показали ИИ}}}

«кому» (scope) — получатель: кабинет покупателя или админ бота. Одна и та же новость, пришедшая с другого
адреса (перепечатка, мобильная версия, метки utm), — тоже «уже было»: сравниваем и адрес без мусора, и заголовок
по смыслу (near_duplicate). Отправленное помним 90 дней (поиск берёт новости за сутки — старое не вернётся),
отклонённое — 3 дня (не спрашиваем ИИ про одно и то же каждые 2–3 часа). Файл пишется с резервной копией:
испортится — поднимем из .bak и не пришлём старое второй раз."""
import threading
import time

from reloader.services.trends import text as T
from reloader.services.trends.search import canonical_url


class AlertStore:
    def __init__(self, path, save=None, load=None, clock=time.time, keep_days=90, seen_days=3, cap=500):
        if save is None or load is None:
            from reloader.core.files import load_json_safe, save_json
            save, load = save or save_json, load or load_json_safe
        self.path, self._save, self.clock = path, save, clock
        self.keep, self.seen_ttl, self.cap = keep_days * 86400, seen_days * 86400, cap
        self.lock = threading.RLock()
        data = load(path, {}) or {}
        self.sent = {str(k): [r for r in v if isinstance(r, dict)] for k, v in (data.get("sent") or {}).items()
                     if isinstance(v, list)}
        self.seen = {str(k): {str(u): float(t) for u, t in v.items() if isinstance(t, (int, float))}
                     for k, v in (data.get("seen") or {}).items() if isinstance(v, dict)}

    @staticmethod
    def _canon(item):
        return item.get("canon") or canonical_url(item.get("url"))

    def known(self, scope, item):
        """Почему новость не новая: "sent" (этот адрес уже отправляли), "similar" (эту же новость с другого
        адреса), "seen" (ИИ её уже видел и отклонил) или None — новая."""
        canon, st = self._canon(item), T.stems(item.get("title") or "")
        with self.lock:
            sent = self.sent.get(scope, [])
            if any(r.get("canon") == canon for r in sent):
                return "sent"
            if any(T.near_duplicate(st, r.get("stems") or ()) for r in sent):
                return "similar"
            if canon in self.seen.get(scope, {}):
                return "seen"
        return None

    def fresh(self, scope, items):
        return [it for it in items if self.known(scope, it) is None]

    def mark_seen(self, scope, items):
        now = self.clock()
        with self.lock:
            box = self.seen.setdefault(scope, {})
            for it in items:
                box[self._canon(it)] = now

    def mark_sent(self, scope, items, alert="", theme=""):
        now = self.clock()
        with self.lock:
            box = self.sent.setdefault(scope, [])
            have = {r.get("canon") for r in box}
            for it in items:
                canon = self._canon(it)
                if canon in have:
                    continue
                have.add(canon)
                box.append({"canon": canon, "url": it.get("url", ""), "title": it.get("title", ""),
                            "stems": sorted(T.stems(it.get("title") or "")), "t": now, "theme": theme,
                            "alert": (alert or "")[:300]})
            self.seen.setdefault(scope, {}).update({self._canon(it): now for it in items})

    def recent_titles(self, scope, n=10):
        """Заголовки последних отправленных — ИИ подсказываем, что это уже было."""
        with self.lock:
            return [r.get("title", "") for r in sorted(self.sent.get(scope, []), key=lambda r: -r.get("t", 0))[:n]]

    def stats(self, scope):
        """(сколько сигналов отправлено этому получателю, последний сигнал или None)."""
        with self.lock:
            box = self.sent.get(scope, [])
            last = max(box, key=lambda r: r.get("t", 0)) if box else None
            alerts = {(r.get("t"), r.get("alert")) for r in box}
            return len(alerts), (dict(last) if last else None)

    def prune(self):
        now = self.clock()
        with self.lock:
            for scope in list(self.sent):
                box = [r for r in self.sent[scope] if now - r.get("t", 0) < self.keep][-self.cap:]
                if box:
                    self.sent[scope] = box
                else:
                    del self.sent[scope]
            for scope in list(self.seen):
                box = {u: t for u, t in self.seen[scope].items() if now - t < self.seen_ttl}
                if box:
                    self.seen[scope] = box
                else:
                    del self.seen[scope]

    def save(self):
        with self.lock:                                  # запись под замком: старый снимок не затрёт новый
            self.prune()
            data = {"sent": {k: list(v) for k, v in self.sent.items()},
                    "seen": {k: dict(v) for k, v in self.seen.items()}}
            self._save(self.path, data, backup=True)
