"""Одноразовые коды активации тарифа.

Код: 10–12 символов, буквы и цифры, без префикса. Случайные коды — 12 символов без похожих (0, O, 1, I).
Погашение — жёсткое удаление записи: код больше не существует, повтор невозможен.
Хранение: JSON-файл, атомарная запись, права 600. Каждое изменение перечитывает файл, чтобы не потерять запись.
"""
import re
import secrets
import threading
import time

from reloader.core.files import load_json_safe, save_json

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"    # для случайных: без 0, O, 1, I
RANDOM_LEN = 12
AUDIT_MAX = 2000
INVALID = "Код недействителен"                    # одинаково для неизвестного, погашенного и отозванного
EXPIRED = "Срок действия кода истёк"
_BODY = re.compile(r"[A-Z0-9]{10,12}")


def normalize_code(raw):
    """Верхний регистр; пробелы и дефисы убираем. Не похоже на код — None."""
    body = re.sub(r"[\s\-]", "", str(raw or "")).upper()
    if not _BODY.fullmatch(body) or not (re.search(r"[A-Z]", body) and re.search(r"[0-9]", body)):
        return None
    return body


def looks_like_code(raw):
    return normalize_code(raw) is not None


def code_state(entry, now):
    if entry.get("expires") and now > entry["expires"]:
        return "expired"
    return "free"


def _random_code():
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(RANDOM_LEN))
        if re.search(r"[A-Z]", code) and re.search(r"[0-9]", code):
            return code


class CodeStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()

    def _read(self):
        data = load_json_safe(self.path, {})        # испорчен/пропал — из резервной копии
        data.setdefault("codes", {})
        data.setdefault("audit", [])
        return data

    def _write(self, data):
        data["audit"] = data["audit"][-AUDIT_MAX:]
        save_json(self.path, data, private=True, backup=True)

    @property
    def audit(self):
        with self._lock:
            return self._read()["audit"]

    def get(self, raw):
        with self._lock:
            entry = self._read()["codes"].get(normalize_code(raw) or "")
            return dict(entry) if entry else None

    def _new_entry(self, tariff, days, now, expires_at):
        return {"tariff": tariff, "days": int(days), "created": now, "expires": expires_at or 0}

    def generate(self, count, tariff, days, now=None, expires_at=None):
        now = now or time.time()
        with self._lock:
            data = self._read()
            fresh = []
            while len(fresh) < int(count):
                code = _random_code()
                if code not in data["codes"] and code not in fresh:
                    fresh.append(code)
            for code in fresh:
                data["codes"][code] = self._new_entry(tariff, days, now, expires_at)
            data["audit"].append({"t": now, "action": "generate", "count": len(fresh), "tariff": tariff,
                                  "days": int(days)})
            self._write(data)
            return fresh

    def add_custom(self, raw, tariff, days, now=None):
        """Свой код, который ввёл админ. Ошибка — ValueError с текстом для админа."""
        now = now or time.time()
        code = normalize_code(raw)
        if code is None:
            raise ValueError("Нужно 10–12 символов: буквы и цифры вперемешку, без пробелов и знаков.")
        with self._lock:
            data = self._read()
            if code in data["codes"]:
                raise ValueError("Такой код уже есть.")
            data["codes"][code] = self._new_entry(tariff, days, now, None)
            data["audit"].append({"t": now, "action": "custom", "code": "…" + code[-4:], "tariff": tariff})
            self._write(data)
            return code

    def redeem(self, raw, buyer, now=None):
        """(статус, запись с полем code, текст ошибки). Статусы: ok, invalid, expired.
        Код удаляется сразу: при неудаче активации его возвращает restore()."""
        now = now or time.time()
        code = normalize_code(raw)
        with self._lock:
            data = self._read()
            entry = data["codes"].get(code) if code else None
            if entry is None:
                data["audit"].append({"t": now, "action": "redeem_fail", "by": str(buyer), "why": "unknown"})
                self._write(data)
                return "invalid", None, INVALID
            if code_state(entry, now) == "expired":
                data["audit"].append({"t": now, "action": "redeem_fail", "by": str(buyer), "why": "expired"})
                self._write(data)
                return "expired", None, EXPIRED
            del data["codes"][code]
            data["audit"].append({"t": now, "action": "redeem", "code": "…" + code[-4:], "by": str(buyer),
                                  "tariff": entry["tariff"], "days": entry["days"]})
            self._write(data)
            return "ok", dict(entry, code=code), ""

    def restore(self, entry, now=None):
        """Вернуть погашенный код, если активацию применить не удалось."""
        code = entry.get("code")
        with self._lock:
            data = self._read()
            data["codes"][code] = {k: v for k, v in entry.items() if k != "code"}
            data["audit"].append({"t": now or time.time(), "action": "restore", "code": "…" + code[-4:]})
            self._write(data)

    def delete(self, raw, now=None):
        code = normalize_code(raw)
        with self._lock:
            data = self._read()
            if not code or code not in data["codes"]:
                return False
            del data["codes"][code]
            data["audit"].append({"t": now or time.time(), "action": "delete", "code": "…" + code[-4:]})
            self._write(data)
            return True

    def listing(self, state=None, now=None):
        """Активные коды (без погашенных — их уже нет): (код, запись, состояние), новые — первыми."""
        now = now or time.time()
        with self._lock:
            codes = self._read()["codes"]
        rows = [(c, dict(e), code_state(e, now)) for c, e in codes.items()]
        rows.sort(key=lambda r: r[1].get("created", 0), reverse=True)
        return [r for r in rows if state in (None, r[2])]

    @staticmethod
    def export_text(codes):
        """Экспорт для Playerok: один код в строке."""
        return "\n".join(codes) + "\n"
