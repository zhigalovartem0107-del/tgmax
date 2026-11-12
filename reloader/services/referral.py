"""🤝 Партнёрские ссылки — просто, без меню для партнёров.

Хозяин бота задаёт ID партнёра и делает ссылку https://max.ru/<бот>?start=ref_<ID партнёра> (по пересланному
сообщению клиента — клиент сразу закрепляется за партнёром). Кто пришёл по ссылке, тоже закреплён (за первым
партнёром — второй не перебивает). Покупки клиентов партнёра учитываются: хозяин платит партнёру сам.
Здесь только данные и разбор — без сети и без состояния бота."""
import re
import threading
import time

_PID = re.compile(r"[A-Za-z0-9_-]{1,32}")
_ID_TEXT = re.compile(r"(?:id\s*[:#№]?\s*)?(\d{4,20})", re.IGNORECASE)
KEEP_SALES = 5000


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
    """Кто за каким партнёром закреплён и что купил. Файл — через save/load бота (запись атомарная)."""

    def __init__(self, path, save, load, clock=time.time):
        self.path, self._save, self.clock = path, save, clock
        self.lock = threading.Lock()
        try:
            raw = load(path, {})
        except Exception:                               # файл испорчен — начинаем с пустого, бот не падает
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        clients = raw.get("clients")
        self.clients = {str(k): v for k, v in clients.items() if isinstance(v, dict) and valid_pid(v.get("partner"))} \
            if isinstance(clients, dict) else {}
        sales = raw.get("sales")
        self.sales = [s for s in sales if isinstance(s, dict) and valid_pid(s.get("partner"))] \
            if isinstance(sales, list) else []

    def save(self):
        with self.lock:
            data = {"clients": dict(self.clients), "sales": list(self.sales)}
        self._save(self.path, data)

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

    def sale(self, partner, key, name, plan, days, price, how):
        with self.lock:
            self.sales.append({"partner": partner, "key": str(key), "name": str(name or "")[:80], "plan": str(plan),
                               "days": int(days or 0), "price": int(price or 0), "how": str(how), "t": self.clock()})
            del self.sales[:-KEEP_SALES]
        self.save()

    def forget(self, uids):
        """Клиент попросил удалить данные: отметку партнёра — долой, в покупках — без имени (суммы для расчёта с
        партнёром остаются)."""
        ids = {str(u) for u in uids or ()}
        with self.lock:
            hit = [k for k in ids if k in self.clients]
            for k in hit:
                del self.clients[k]
            for s in self.sales:
                if s["key"] in ids and s.get("name"):
                    s["name"] = ""
                    hit.append(s["key"])
        if hit:
            self.save()

    def stats(self, partner):
        """Сколько клиентов закреплено за партнёром, сколько покупок и на какую сумму (₽)."""
        with self.lock:
            mine = [s for s in self.sales if s["partner"] == partner]
            return {"clients": sum(1 for c in self.clients.values() if c["partner"] == partner),
                    "sales": len(mine), "sum": sum(s["price"] for s in mine)}
