"""🤝 Партнёрские ссылки — просто, без меню для партнёров.

Хозяин бота задаёт ID партнёра и делает ссылку https://max.ru/<бот>?start=ref_<ID партнёра> (по пересланному
сообщению клиента — клиент сразу закрепляется за партнёром). Кто пришёл по ссылке, тоже закреплён (за первым
партнёром — второй не перебивает). Покупки клиентов партнёра учитываются: хозяин платит партнёру сам.
Переходы по ссылке тоже считаются (только число, без личных данных); долг партнёру — его процент с каждой покупки
(процент на момент покупки) минус отмеченные выплаты.
Здесь только данные и разбор — без сети и без состояния бота."""
import math
import re
import threading
import time

_PID = re.compile(r"[A-Za-z0-9_-]{1,32}")
_ID_TEXT = re.compile(r"(?:id\s*[:#№]?\s*)?(\d{4,20})", re.IGNORECASE)
_PCT_TEXT = re.compile(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*%?")
KEEP_SALES = 5000           # покупок в файле; старые уходят в итоги партнёра — суммы и долг не теряются
KEEP_PAYOUTS = 200          # выплат в истории; итог выплат по партнёру хранится отдельно и не обрезается
KEEP_PARTNERS = 1000        # счётчиков переходов: ссылку с выдуманным ID партнёра может открыть кто угодно
KEEP_RECENT = 2048          # «только что перешёл» — в памяти, не больше стольких
CLICK_SAME_SEC = 10         # один переход двумя событиями (MAX: «бот запущен» и «/start ref_…» текстом) — в пределах 10 с
MAX_PAYOUT = 10 ** 9


def valid_pid(pid):
    """ID партнёра: буквы, цифры, «_» и «-», до 32 знаков (так он без искажений проходит в ссылке ?start=)."""
    return isinstance(pid, str) and bool(_PID.fullmatch(pid))


def partner_from_payload(payload):
    """«ref_123456» (из ?start=…) -> «123456»; не партнёрская или кривая метка — None."""
    if not isinstance(payload, str):
        return None
    payload = payload.strip()
    if not payload.startswith("ref_"):
        return None
    pid = payload[4:]
    return pid if valid_pid(pid) else None


def link(bot_username, pid):
    """Ссылка для партнёра: https://max.ru/<бот>?start=ref_<ID партнёра>."""
    return f"https://max.ru/{str(bot_username).lstrip('@')}?start=ref_{pid}"


def _int(x):
    return x if isinstance(x, int) and not isinstance(x, bool) else None


def _count(x):
    """Целое ≥ 0, иначе None (true/false, дробное, строка — мусор из испорченного файла)."""
    x = _int(x)
    return x if x is not None and x >= 0 else None


def _rate(x):
    """Процент партнёра: число от 0 до 100 (не true/false, не NaN), иначе None. Целое — int (в файле «30», не «30.0»)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 <= x <= 100:
        return None
    return int(x) if float(x).is_integer() else float(x)


def _when(t):
    """Время из файла: число секунд (не true/false, не NaN) — иначе запись истории не берём."""
    return not isinstance(t, bool) and isinstance(t, (int, float)) and math.isfinite(t) and t >= 0


def parse_pct(text):
    """Процент из сообщения хозяина: «30», «30%», «12,5» -> 30 / 12.5; не число, меньше 0 или больше 100 — None."""
    if not isinstance(text, str):
        return None
    m = _PCT_TEXT.fullmatch(text.strip())
    return _rate(float(m.group(1).replace(",", "."))) if m else None


def _clean_sale(s):
    """Покупка из файла; без партнёра или с кривой суммой — None (файл испорчен или правлен руками)."""
    if not isinstance(s, dict) or not valid_pid(s.get("partner")) or _count(s.get("price")) is None:
        return None
    out = dict(s, key=str(s.get("key") or ""), name=str(s.get("name") or "")[:80])
    pct = _rate(out.pop("pct", None))
    if pct is not None:
        out["pct"] = pct
    return out


def _clean_carry(c):
    """Итоги покупок, ушедших из файла: число, сумма, начислено партнёру (₽), сумма покупок без процента."""
    if not isinstance(c, dict):
        return None
    e = c.get("earned")
    ok = not isinstance(e, bool) and isinstance(e, (int, float)) and math.isfinite(e) and e >= 0
    return {"sales": _count(c.get("sales")) or 0, "sum": _count(c.get("sum")) or 0,
            "earned": float(e) if ok else 0.0, "open": _count(c.get("open")) or 0}


def client_from_message(msg, text):
    """Клиент из сообщения хозяина: пересланное сообщение человека — (его ID, имя); ID цифрами — (ID, None).
    Пост канала (у пересылки нет автора), @ник и прочее — (None, None)."""
    link_ = msg.get("link") if isinstance(msg, dict) else None
    if isinstance(link_, dict) and link_.get("type") == "forward":
        sender = link_.get("sender") if isinstance(link_.get("sender"), dict) else {}
        uid = _int(sender.get("user_id"))
        if uid is not None and uid > 0:
            name = " ".join(str(sender.get("name") or sender.get("first_name") or "").split())[:80]
            return uid, name or None
        return None, None
    m = _ID_TEXT.fullmatch((text or "").strip()) if isinstance(text, str) else None
    return (int(m.group(1)), None) if m else (None, None)


class Referrals:
    """Кто за каким партнёром закреплён и что купил, сколько было переходов по ссылке партнёра, его процент и
    выплаты ему. Файл — через save/load бота (запись атомарная, у бота — с резервной копией)."""

    def __init__(self, path, save, load, clock=time.time):
        self.path, self._save, self.clock = path, save, clock
        self.lock = threading.Lock()
        self._wlock = threading.Lock()   # снимок и запись файла — строго по очереди: старый снимок не ляжет поверх нового
        self._recent = {}                # (партнёр, человек) -> (когда, каким событием): один переход двумя событиями
        try:
            raw = load(path, {})
        except Exception:                               # файл испорчен — начинаем с пустого, бот не падает
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        dct = lambda k: raw.get(k) if isinstance(raw.get(k), dict) else {}
        lst = lambda k: raw.get(k) if isinstance(raw.get(k), list) else []
        self.clients = {str(k): v for k, v in dct("clients").items()
                        if isinstance(v, dict) and valid_pid(v.get("partner"))}
        self.sales = [x for x in map(_clean_sale, lst("sales")) if x]
        self.rates = {k: _rate(v) for k, v in dct("rates").items() if valid_pid(k) and _rate(v) is not None}
        self.paid = {k: v for k, v in dct("paid").items() if valid_pid(k) and _int(v) is not None}
        self.payouts = [dict(x) for x in lst("payouts") if isinstance(x, dict) and valid_pid(x.get("partner"))
                        and _int(x.get("sum")) is not None and _when(x.get("t"))][-KEEP_PAYOUTS:]
        carry = {k: _clean_carry(v) for k, v in dct("carry").items() if valid_pid(k)}
        self.carry = {k: v for k, v in carry.items() if v}
        self.clicks = {k: v for k, v in dct("clicks").items() if valid_pid(k) and _count(v) is not None}
        if len(self.clicks) > KEEP_PARTNERS:                  # сверх предела — сначала «пустые» и маленькие
            real = self._real_partners()
            for p in sorted(self.clicks, key=lambda p: (p in real, self.clicks[p]))[:len(self.clicks) - KEEP_PARTNERS]:
                del self.clicks[p]

    def save(self):
        with self._wlock:
            with self.lock:
                data = {"clients": {k: dict(v) for k, v in self.clients.items()},
                        "sales": [dict(x) for x in self.sales], "clicks": dict(self.clicks),
                        "rates": dict(self.rates), "paid": dict(self.paid),
                        "payouts": [dict(x) for x in self.payouts],
                        "carry": {k: dict(v) for k, v in self.carry.items()}}
            self._save(self.path, data)

    def click(self, partner, uid=None, via=""):
        """Переход по ссылке партнёра: +1 к его счётчику — и сразу в файл (только число, без личных данных).
        Один переход, пришедший двумя разными событиями за CLICK_SAME_SEC секунд (MAX прислал и «бот запущен»,
        и «/start ref_…» текстом), считается один раз; человек нажал ссылку ещё раз — ещё переход.
        True — засчитали; кривой ID партнёра — False."""
        if not valid_pid(partner):
            return False
        now = self.clock()
        with self.lock:
            if uid is not None:
                key = (partner, str(uid))
                last = self._recent.get(key)
                if last and last[1] != via and 0 <= now - last[0] < CLICK_SAME_SEC:
                    return False
                self._recent[key] = (now, via)
                self._trim_recent(now)
            if partner not in self.clicks and len(self.clicks) >= KEEP_PARTNERS:
                self._evict_click()
            self.clicks[partner] = self.clicks.get(partner, 0) + 1
        self.save()
        return True

    def _trim_recent(self, now):
        if len(self._recent) <= KEEP_RECENT:
            return
        for k in [k for k, (t, _) in self._recent.items() if not 0 <= now - t < CLICK_SAME_SEC]:
            del self._recent[k]
        if len(self._recent) > KEEP_RECENT:                   # наплыв за секунды — оставляем самых свежих
            for k in sorted(self._recent, key=lambda k: self._recent[k][0])[:len(self._recent) - KEEP_RECENT // 2]:
                del self._recent[k]

    def _evict_click(self):
        """Счётчиков переходов — не больше KEEP_PARTNERS: уходит самый маленький у «пустого» партнёра (без клиентов,
        покупок, процента и выплат — скорее всего, выдуманная ссылка); настоящие — в последнюю очередь."""
        real = self._real_partners()
        pool = [p for p in self.clicks if p not in real] or list(self.clicks)
        del self.clicks[min(pool, key=self.clicks.get)]

    def _real_partners(self):
        """Партнёры, у которых есть клиенты, покупки, процент или выплаты (а не только переходы)."""
        return ({c["partner"] for c in self.clients.values()} | {x["partner"] for x in self.sales}
                | set(self.rates) | set(self.paid) | set(self.carry))

    def attach(self, uid, partner, via, name=""):
        """Закрепить клиента за партнёром. (True, партнёр) — закрепили; (False, прежний партнёр) — уже был."""
        if uid is None or not valid_pid(partner):
            return False, None
        with self.lock:
            got = self.clients.get(str(uid))
            if got:
                return False, got["partner"]
            self.clients[str(uid)] = {"partner": partner, "via": via, "name": str(name or "")[:80], "at": self.clock()}
        self.save()
        return True, partner

    def partner_of(self, uids):
        """Партнёр клиента (по любому из его ID) или None."""
        with self.lock:
            for uid in uids or ():
                got = self.clients.get(str(uid))
                if got:
                    return got["partner"]
        return None

    def sale(self, partner, key, name, plan, days, price, how, rate=None):
        """Покупка клиента партнёра. У покупки — процент партнёра на момент покупки (rate даёт бот: /partner % N
        или переменная хостинга; нет — процент из учёта); процента нет — посчитается, когда его зададут."""
        with self.lock:
            pct = _rate(rate) if rate is not None else None
            if pct is None:
                pct = self.rates.get(partner)
            rec = {"partner": partner, "key": str(key), "name": str(name or "")[:80], "plan": str(plan),
                   "days": int(days or 0), "price": max(0, int(price or 0)), "how": str(how), "t": self.clock()}
            if pct is not None:
                rec["pct"] = pct
            self.sales.append(rec)
            self._fold_old()
        self.save()

    def _fold_old(self):
        """Покупок больше KEEP_SALES — самые старые уходят из файла в итоги партнёра (число, сумма, начислено):
        воронка и долг партнёру от этого не меняются."""
        extra = len(self.sales) - KEEP_SALES
        if extra <= 0:
            return
        for x in self.sales[:extra]:
            c = self.carry.setdefault(x["partner"], {"sales": 0, "sum": 0, "earned": 0.0, "open": 0})
            c["sales"] += 1
            c["sum"] += x["price"]
            if x.get("pct") is None:
                c["open"] += x["price"]
            else:
                c["earned"] += x["price"] * x["pct"] / 100
        del self.sales[:extra]

    def rate(self, partner):
        """Процент партнёра из /partner % N или None."""
        with self.lock:
            return self.rates.get(partner)

    def set_rate(self, partner, pct):
        """Процент партнёра с покупок его клиентов (0…100). Покупки без процента (были до того, как его задали) —
        теперь по нему; у остальных — процент на момент покупки: новый процент старые покупки не пересчитывает."""
        pct = _rate(pct)
        if not valid_pid(partner) or pct is None:
            return False
        with self.lock:
            self.rates[partner] = pct
            for x in self.sales:
                if x["partner"] == partner and x.get("pct") is None:
                    x["pct"] = pct
            c = self.carry.get(partner)
            if c and c["open"]:
                c["earned"] += c["open"] * pct / 100
                c["open"] = 0
        self.save()
        return True

    def payout(self, partner, amount):
        """Хозяин заплатил партнёру amount ₽ (целое; минус — исправить ошибочную отметку). Итог выплат хранится
        отдельно и не обрезается. False — кривая сумма или ID партнёра."""
        amount = _int(amount)
        if not valid_pid(partner) or not amount or abs(amount) > MAX_PAYOUT:
            return False
        with self.lock:
            self.paid[partner] = self.paid.get(partner, 0) + amount
            self.payouts.append({"partner": partner, "sum": amount, "t": self.clock()})
            del self.payouts[:-KEEP_PAYOUTS]
        self.save()
        return True

    def last_payout(self, partner):
        """Последняя отметка выплаты партнёру ({"sum", "t"}) или None."""
        with self.lock:
            return next((dict(x) for x in reversed(self.payouts) if x["partner"] == partner), None)

    def forget(self, uids):
        """Клиент попросил удалить данные: отметку партнёра — долой, в покупках — без имени (суммы для расчёта с
        партнёром остаются; в счётчике переходов личных данных нет)."""
        ids = {str(u) for u in uids or ()}
        with self.lock:
            hit = [k for k in ids if k in self.clients]
            for k in hit:
                del self.clients[k]
            for x in self.sales:
                if x["key"] in ids and x.get("name"):
                    x["name"] = ""
                    hit.append(x["key"])
            for k in [k for k in self._recent if k[1] in ids]:
                del self._recent[k]
        if hit:
            self.save()

    def summary(self, partner, rate=None):
        """Воронка партнёра: clicks — переходов по ссылке, clients — закреплено клиентов, sales — покупок на sum ₽;
        rate — процент для покупок без процента (даёт бот; нет — из /partner % N); earned — начислено партнёру (₽),
        paid — выплачено, owed — долг (earned − paid; меньше нуля — переплата). Покупки без процента есть, а
        процента нет — earned и owed = None: долг не известен (это не 0)."""
        with self.lock:
            pct = _rate(rate) if rate is not None else None
            if pct is None:
                pct = self.rates.get(partner)
            c = self.carry.get(partner) or {"sales": 0, "sum": 0, "earned": 0.0, "open": 0}
            mine = [x for x in self.sales if x["partner"] == partner]
            earned = c["earned"] + sum(x["price"] * x["pct"] / 100 for x in mine if x.get("pct") is not None)
            unpriced = c["open"] + sum(x["price"] for x in mine if x.get("pct") is None)
            if unpriced:
                earned = None if pct is None else earned + unpriced * pct / 100
            earned = None if earned is None else int(earned + 0.5)
            paid = self.paid.get(partner, 0)
            return {"clicks": self.clicks.get(partner, 0),
                    "clients": sum(1 for x in self.clients.values() if x["partner"] == partner),
                    "sales": c["sales"] + len(mine), "sum": c["sum"] + sum(x["price"] for x in mine),
                    "rate": pct, "earned": earned, "paid": paid, "owed": None if earned is None else earned - paid}

    def stats(self, partner):
        """Сколько клиентов закреплено за партнёром, сколько покупок и на какую сумму (₽)."""
        got = self.summary(partner)
        return {k: got[k] for k in ("clients", "sales", "sum")}
