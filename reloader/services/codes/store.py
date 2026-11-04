"""Одноразовые коды активации тарифа (покупают на Playerok, вводят в боте).

Код: RL-XXXX-XXXX, без похожих символов (0, O, 1, I). Погашается один раз, под замком, с записью в журнал.
Хранение: JSON-файл, атомарная запись, права 600. Каждое изменение перечитывает файл, чтобы не потерять запись."""
import re
import secrets
import threading
import time

from reloader.core.files import load_json, save_json

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_RE = re.compile(rf"RL-[{ALPHABET}]{{4}}-[{ALPHABET}]{{4}}")
STATES = ("free", "used", "expired", "revoked")
AUDIT_MAX = 2000
INVALID = "Код недействителен"                 # одинаково для неизвестного, использованного и отозванного
EXPIRED = "Срок действия кода истёк"


def normalize_code(raw):
    """Пробелы, дефисы и регистр не важны. Не код — None."""
    body = re.sub(r"[\s\-]", "", str(raw or "")).upper()
    if len(body) != 10 or not body.startswith("RL"):
        return None
    code = f"RL-{body[2:6]}-{body[6:]}"
    return code if CODE_RE.fullmatch(code) else None


def code_state(entry, now):
    if entry.get("status") == "used":
        return "used"
    if entry.get("status") == "revoked":
        return "revoked"
    if entry.get("expires") and now > entry["expires"]:
        return "expired"
    return "free"


def _chunk():
    return "".join(secrets.choice(ALPHABET) for _ in range(4))


class CodeStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()

    def _read(self):
        data = load_json(self.path, {})
        data.setdefault("codes", {})
        data.setdefault("audit", [])
        return data

    def _write(self, data):
        data["audit"] = data["audit"][-AUDIT_MAX:]
        save_json(self.path, data, private=True)

    @property
    def audit(self):
        with self._lock:
            return self._read()["audit"]

    def get(self, raw):
        with self._lock:
            entry = self._read()["codes"].get(normalize_code(raw) or "")
            return dict(entry) if entry else None

    def generate(self, count, tariff, days, now=None, expires_at=None):
        now = now or time.time()
        with self._lock:
            data = self._read()
            fresh = []
            while len(fresh) < int(count):
                code = f"RL-{_chunk()}-{_chunk()}"
                if code not in data["codes"] and code not in fresh:
                    fresh.append(code)
            for code in fresh:
                data["codes"][code] = {"tariff": tariff, "days": int(days), "created": now,
                                       "expires": expires_at or 0, "status": "free", "used_by": "", "used_at": 0}
            data["audit"].append({"t": now, "action": "generate", "count": len(fresh), "tariff": tariff,
                                  "days": int(days)})
            self._write(data)
            return fresh

    def redeem(self, raw, buyer, now=None):
        """(статус, запись кода, текст ошибки). Статусы: ok, invalid, used, expired."""
        now = now or time.time()
        code = normalize_code(raw)
        with self._lock:
            data = self._read()
            entry = data["codes"].get(code) if code else None
            state = code_state(entry, now) if entry else "invalid"
            if state != "free":
                data["audit"].append({"t": now, "action": "redeem_fail", "by": str(buyer), "why": state})
                self._write(data)
                if state == "expired":
                    return "expired", None, EXPIRED
                return ("used" if state == "used" else "invalid"), None, INVALID
            entry.update(status="used", used_by=str(buyer), used_at=now)
            data["audit"].append({"t": now, "action": "redeem", "code": "…" + code[-4:], "by": str(buyer),
                                  "tariff": entry["tariff"], "days": entry["days"]})
            self._write(data)
            return "ok", dict(entry, code=code), ""

    def release(self, raw, buyer, now=None):
        """Откат погашения, если активация не удалась: код снова свободен."""
        code = normalize_code(raw)
        with self._lock:
            data = self._read()
            entry = data["codes"].get(code) if code else None
            if not entry or entry.get("status") != "used" or entry.get("used_by") != str(buyer):
                return False
            entry.update(status="free", used_by="", used_at=0)
            data["audit"].append({"t": now or time.time(), "action": "release", "code": "…" + code[-4:]})
            self._write(data)
            return True

    def revoke(self, raw, now=None):
        code = normalize_code(raw)
        with self._lock:
            data = self._read()
            entry = data["codes"].get(code) if code else None
            if not entry or code_state(entry, now or time.time()) != "free":
                return False
            entry["status"] = "revoked"
            data["audit"].append({"t": now or time.time(), "action": "revoke", "code": "…" + code[-4:]})
            self._write(data)
            return True

    def listing(self, state=None, now=None):
        now = now or time.time()
        with self._lock:
            codes = self._read()["codes"]
        rows = [(c, dict(e), code_state(e, now)) for c, e in codes.items()]
        rows.sort(key=lambda r: r[1].get("created", 0), reverse=True)
        return [r for r in rows if state in (None, r[2])]

    def counts(self, now=None):
        out = {s: 0 for s in STATES}
        for _, _, st in self.listing(None, now):
            out[st] += 1
        return out

    @staticmethod
    def export_text(codes):
        """Экспорт для Playerok: один код в строке."""
        return "\n".join(codes) + "\n"
