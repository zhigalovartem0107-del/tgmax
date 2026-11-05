"""Ключи ИИ-сервисов: список ключей на провайдера и ротация при лимитах (429).

KeyRotator не хранит ключи сам: на каждом вызове берёт текущий список у source() — ключи, сохранённые
в панели бота, подхватываются сразу, без перезапуска."""
import re
import threading
import time

_INVISIBLE = re.compile(r"[\"'«»`​-‏⁠﻿]")


def parse_keys(text):
    """«k1, k2\\nk3» -> ["k1", "k2", "k3"]: запятые, точки с запятой, переносы и пробелы; без пустых и повторов."""
    out = []
    for part in re.split(r"[,;\s]+", _INVISIBLE.sub("", str(text or ""))):
        if part and part not in out:
            out.append(part)
    return out


def mask_key(key):
    return "••••" + str(key)[-4:]


def mask_keys(keys):
    return ", ".join(mask_key(k) for k in keys)


class RateLimited(Exception):
    """Ответ 429: ключ упёрся в лимит. retry_after — через сколько секунд можно снова (если сервис сказал)."""

    def __init__(self, retry_after=None):
        super().__init__(f"429, повтор через {retry_after or '?'} с")
        self.retry_after = retry_after


class KeyRejected(Exception):
    """Ключ не принят (401/403): неверный или отозван."""


class AllKeysExhausted(RuntimeError):
    def __init__(self, provider, detail):
        super().__init__(f"{provider}: {detail}")
        self.provider = provider


class KeyRotator:
    def __init__(self, provider, source, clock=time.time, cooldown=60, bad_cooldown=3600):
        self.provider, self.source, self.clock = provider, source, clock
        self.cooldown, self.bad_cooldown = cooldown, bad_cooldown
        self._rest = {}          # ключ -> до какого времени не трогаем (429 / не принят)
        self._current = None     # ключ, на котором работаем, пока он не упрётся в лимит
        self._lock = threading.Lock()

    def keys(self):
        return list(self.source() or [])

    def reset(self):
        with self._lock:
            self._rest.clear()
            self._current = None

    def run(self, call):
        """call(key) -> результат. 429 — ключ отдыхает, берём следующий; не принят или сбой — тоже следующий.
        Ни один не сработал — AllKeysExhausted с причиной по каждому ключу."""
        keys = self.keys()
        if not keys:
            raise AllKeysExhausted(self.provider, "ключей нет")
        with self._lock:
            start = keys.index(self._current) if self._current in keys else 0
        reasons = []
        for key in keys[start:] + keys[:start]:
            if self._rest.get(key, 0) > self.clock():
                reasons.append(f"{mask_key(key)}: отдыхает после лимита")
                continue
            try:
                result = call(key)
            except RateLimited as e:
                self._rest[key] = self.clock() + (e.retry_after or self.cooldown)
                reasons.append(f"{mask_key(key)}: 429")
                continue
            except KeyRejected as e:
                self._rest[key] = self.clock() + self.bad_cooldown
                reasons.append(f"{mask_key(key)}: ключ не принят ({str(e)[:80]})")
                continue
            except Exception as e:
                reasons.append(f"{mask_key(key)}: {type(e).__name__}: {str(e)[:80]}")
                continue
            with self._lock:
                self._current = key
            return result
        raise AllKeysExhausted(self.provider, "; ".join(reasons))
