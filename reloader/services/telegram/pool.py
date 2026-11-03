"""Пул аккаунтов Telegram (userbot): несколько входов — файлы .session в папке sessions/ — читают источники вместе.

Каждый источник закреплён за одним аккаунтом (pool.json). Новый источник: если какой-то аккаунт уже состоит
в канале — закрепляем за ним (второй раз не вступаем); иначе вступает аккаунт с наименьшим числом каналов.
FloodWait — аккаунт отдыхает ровно столько, сколько сказал Telegram, работу берут другие; вход слетел (бан,
сеанс завершён) — аккаунт выбывает, его каналы переходят к живым.

Скорость: всё на asyncio, у каждого аккаунта свой лимит одновременных чтений (per_account) — аккаунты читают
параллельно, и с ростом числа каналов чтение не замедляется, а распределяется."""

import asyncio
import contextlib
import json
import os
import time

ACTIVE, FLOOD, DEAD = "active", "flood", "dead"
SQLITE_HEADER = b"SQLite format 3\x00"            # файл входа Telethon — база SQLite
FLOOD_ERRORS = {"FloodWaitError", "FloodPremiumWaitError", "FloodError"}
DEAD_ERRORS = {"AuthKeyUnregisteredError", "AuthKeyDuplicatedError", "AuthKeyInvalidError", "SessionRevokedError",
               "SessionExpiredError", "UserDeactivatedError", "UserDeactivatedBanError", "PhoneNumberBannedError"}
FULL_ERRORS = {"ChannelsTooMuchError"}            # аккаунт в лимите каналов Telegram — новых не берёт


class SessionError(ValueError):
    """Файл входа не подходит (не .session или вход в нём не действует) — текст для хозяина."""


class PoolBusy(RuntimeError):
    """Свободных аккаунтов нет: все отдыхают после FloodWait (seconds — через сколько освободится первый)."""

    def __init__(self, seconds):
        super().__init__(f"все аккаунты пула отдыхают, ближайший освободится через {int(seconds)} с" if seconds else
                         "нет свободных аккаунтов (выбыли или в лимите каналов) — добавь ещё один .session")
        self.seconds = seconds


async def telethon_join(client, ch):
    from telethon.tl.functions.channels import JoinChannelRequest
    await client(JoinChannelRequest(ch))


def channel_key(ch):
    return str(ch or "").strip().lstrip("@").lower()


class Account:
    def __init__(self, name, path, client, per_account):
        self.name, self.path, self.client = name, path, client
        self.status, self.until, self.error = ACTIVE, 0.0, ""
        self.full, self.title, self.last_join = False, name, 0.0
        self.joined = set()
        self.sem = asyncio.Semaphore(per_account)


class AccountPool:
    def __init__(self, folder, state_path, make_client, join=telethon_join, per_account=5, join_gap=20.0,
                 clock=time.time):
        self.folder, self.state_path, self.make_client = folder, state_path, make_client
        self.join, self.per_account, self.join_gap, self.clock = join, per_account, join_gap, clock
        self._accounts = {}            # имя -> Account
        self._channels = {}            # канал (lower) -> имя аккаунта
        self._saved = {}               # состояние аккаунтов из pool.json (FloodWait, «в лимите»)
        self._lock = asyncio.Lock()

    # ---------- состояние ----------
    def _load_state(self):
        with contextlib.suppress(FileNotFoundError, ValueError):
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
            self._channels = {channel_key(k): v for k, v in (data.get("channels") or {}).items()}
            self._saved = data.get("accounts") or {}

    def _save(self):
        data = {"channels": self._channels,
                "accounts": {a.name: {"status": a.status, "until": a.until, "error": a.error, "full": a.full}
                             for a in self._accounts.values()}}
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.state_path)

    def _state(self, acc):
        if acc.status == FLOOD and self.clock() >= acc.until:
            acc.status, acc.until = ACTIVE, 0.0          # отдохнул
        return acc.status

    def _load(self, acc):
        return sum(1 for v in self._channels.values() if v == acc.name)

    def usable(self):
        """Аккаунты, которые сейчас могут читать (вошли и не отдыхают)."""
        return [a for a in sorted(self._accounts.values(), key=lambda a: a.name) if self._state(a) == ACTIVE]

    def status(self):
        """Для панели хозяина: каждый аккаунт, его состояние и число закреплённых каналов."""
        return [{"name": a.name, "title": a.title, "status": self._state(a), "until": a.until,
                 "channels": self._load(a), "error": a.error, "full": a.full}
                for a in sorted(self._accounts.values(), key=lambda a: a.name)]

    def owner(self, ch):
        return self._channels.get(channel_key(ch))

    def account_of(self, client):
        return next((a for a in self._accounts.values() if a.client is client), None)

    def sem_for(self, client):
        acc = self.account_of(client)
        return acc.sem if acc else None

    # ---------- аккаунты ----------
    async def start(self):
        """Открывает все .session из папки (при запуске бота)."""
        os.makedirs(self.folder, mode=0o700, exist_ok=True)
        self._load_state()
        for fn in sorted(os.listdir(self.folder)):
            if fn.endswith(".session") and fn[:-len(".session")] not in self._accounts:
                await self._open(os.path.join(self.folder, fn))
        self._channels = {k: v for k, v in self._channels.items() if v in self._accounts}
        self._save()

    async def _open(self, path):
        name = os.path.basename(path)[:-len(".session")]
        acc = Account(name, path, self.make_client(path), self.per_account)
        saved = self._saved.get(name) or {}
        acc.full = bool(saved.get("full"))
        if saved.get("status") == FLOOD and saved.get("until", 0) > self.clock():
            acc.status, acc.until = FLOOD, float(saved["until"])
        self._accounts[name] = acc
        try:
            await acc.client.connect()
            if not await acc.client.is_user_authorized():
                self._kill(acc, "вход в этом файле не действует — войди заново и пришли новый .session")
                return acc
            me = await acc.client.get_me()
            first, user = getattr(me, "first_name", "") or "", getattr(me, "username", "") or ""
            acc.title = f"{first} (@{user})" if user else (first or name)
            async for d in acc.client.iter_dialogs():    # в каких каналах аккаунт уже состоит
                u = getattr(getattr(d, "entity", None), "username", None)
                if getattr(d, "is_channel", False) and u:
                    acc.joined.add(u.lower())
        except Exception as e:
            if not self._classify(acc, e):
                self._kill(acc, f"не подключился: {type(e).__name__}")
        return acc

    async def add(self, path):
        """Новый файл входа (загрузил хозяин). Не подходит — SessionError, файл удаляется."""
        try:
            with open(path, "rb") as f:
                ok = f.read(len(SQLITE_HEADER)) == SQLITE_HEADER
        except OSError:
            ok = False
        if not ok:
            self._drop_file(path)
            raise SessionError("Это не файл входа Telethon — нужен файл .session (база SQLite).")
        os.chmod(path, 0o600)
        acc = await self._open(path)
        if acc.status == DEAD:
            self._accounts.pop(acc.name, None)
            with contextlib.suppress(Exception):
                await acc.client.disconnect()
            self._drop_file(path)
            raise SessionError("Вход в этом файле не действует (сеанс завершён или файл от другой программы) — "
                               "войди в аккаунт заново и пришли новый .session.")
        self._save()
        return acc

    async def remove(self, name):
        acc = self._accounts.pop(name, None)
        if not acc:
            return False
        with contextlib.suppress(Exception):
            await acc.client.disconnect()
        self._drop_file(acc.path)
        self._channels = {k: v for k, v in self._channels.items() if v != name}
        self._save()
        return True

    @staticmethod
    def _drop_file(path):
        for p in (path, path + "-journal"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)

    def _kill(self, acc, why):
        acc.status, acc.error = DEAD, why
        self._channels = {k: v for k, v in self._channels.items() if v != acc.name}

    def _classify(self, acc, e):
        """Ошибка про аккаунт? FloodWait — пауза; вход слетел — выбывает; лимит каналов — не вступает.
        True — учли (дальше пробуем другой аккаунт), False — ошибка не про аккаунт."""
        kind = type(e).__name__
        if kind in FLOOD_ERRORS:
            acc.status, acc.until = FLOOD, self.clock() + float(getattr(e, "seconds", 0) or 60)
        elif kind in DEAD_ERRORS:
            self._kill(acc, f"вход слетел: {kind}")
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().create_task(acc.client.disconnect())
        elif kind in FULL_ERRORS:
            acc.full = True
        else:
            return False
        self._save()
        return True

    def note_error(self, client, e):
        """Ошибка при чтении через аккаунт пула. True — это была ошибка аккаунта, она учтена."""
        acc = self.account_of(client)
        return bool(acc) and self._classify(acc, e)

    # ---------- каналы ----------
    def client_for(self, ch):
        """Чем читать канал: аккаунт, за которым он закреплён; тот отдыхает или выбыл — самый свободный живой
        (публичный канал читается и без вступления); живых нет — None (читает основной вход бота)."""
        acc = self._accounts.get(self.owner(ch))
        if acc and self._state(acc) == ACTIVE:
            return acc.client
        live = sorted(self.usable(), key=lambda a: (self._load(a), a.name))
        return live[0].client if live else None

    async def assign(self, ch):
        """Закрепляет канал за аккаунтом и при необходимости вступает в него (JoinChannelRequest)."""
        key = channel_key(ch)
        async with self._lock:
            acc = self._accounts.get(self._channels.get(key))
            if acc and self._state(acc) != DEAD:
                return acc
            live = self.usable()
            for acc in live:                              # 1) кто-то уже в канале — без нового вступления
                if key in acc.joined:
                    return self._bind(key, acc)
            for acc in sorted((a for a in live if not a.full), key=lambda a: (self._load(a), a.name)):
                wait = acc.last_join + self.join_gap - self.clock()
                if wait > 0:                              # между вступлениями одного аккаунта — пауза
                    await asyncio.sleep(wait)
                try:
                    await self.join(acc.client, key)      # 2) вступает самый свободный
                except Exception as e:
                    if self._classify(acc, e):
                        continue                          # отдыхает / выбыл / в лимите — следующий
                    raise
                acc.last_join = self.clock()
                acc.joined.add(key)
                return self._bind(key, acc)
            rest = [a.until - self.clock() for a in self._accounts.values() if self._state(a) == FLOOD]
            raise PoolBusy(max(0.0, min(rest)) if rest else 0.0)

    def _bind(self, key, acc):
        self._channels[key] = acc.name
        self._save()
        return acc
