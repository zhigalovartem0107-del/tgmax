"""Пул аккаунтов Telegram (reloader/services/telegram/pool.py) на поддельных клиентах — без сети.
Запуск: python3 test_pool.py"""
import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import asyncio
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reloader.services.telegram.pool import AccountPool, PoolBusy, SessionError  # noqa: E402

SQLITE = b"SQLite format 3\x00" + b"\x00" * 200


class FloodWaitError(Exception):
    def __init__(self, seconds):
        super().__init__(f"A wait of {seconds} seconds is required")
        self.seconds = seconds


class AuthKeyUnregisteredError(Exception):
    pass


class ChannelsTooMuchError(Exception):
    pass


REG = {}          # имя аккаунта -> как ведёт себя его поддельный клиент


class FakeTG:
    def __init__(self, path):
        self.name = os.path.basename(path)[:-len(".session")]
        self.cfg = REG.setdefault(self.name, {})
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        return self.cfg.get("auth", True)

    async def get_me(self):
        return SimpleNamespace(id=self.cfg.get("uid", sum(map(ord, self.name))), first_name=self.name.title(),
                               username=f"{self.name}_bot")

    async def iter_dialogs(self):
        for u in self.cfg.get("joined", []):
            yield SimpleNamespace(is_channel=True, entity=SimpleNamespace(username=u))

    async def join(self, ch):
        if self.cfg.get("join_error"):
            raise self.cfg["join_error"]
        self.cfg.setdefault("joins", []).append(ch)


async def fake_join(client, ch):
    await client.join(ch)


def session_file(folder, name, data=None, key=None, kind="telethon"):
    """Файл входа как у Telethon (SQLite, таблица sessions с server_address и auth_key); data — сырые байты."""
    path = os.path.join(folder, name + ".session")
    if data is not None:
        with open(path, "wb") as f:
            f.write(data)
        return path
    con = sqlite3.connect(path)
    if kind == "pyrogram":
        con.execute("CREATE TABLE sessions (dc_id INTEGER, api_id INTEGER, test_mode INTEGER, auth_key BLOB, "
                    "date INTEGER, user_id INTEGER, is_bot INTEGER)")
        con.execute("INSERT INTO sessions VALUES (2, 1, 0, ?, 0, 1, 0)", (os.urandom(256),))
    else:
        con.execute("CREATE TABLE version (version INTEGER)")
        con.execute("INSERT INTO version VALUES (7)")
        con.execute("CREATE TABLE sessions (dc_id INTEGER PRIMARY KEY, server_address TEXT, port INTEGER, "
                    "auth_key BLOB, takeout_id INTEGER)")
        con.execute("INSERT INTO sessions VALUES (2, '149.154.167.41', 443, ?, NULL)",
                    (os.urandom(256) if key is None else key,))
    con.commit()
    con.close()
    return path


def new_pool(folder, clock, main_session=None):
    return AccountPool(folder, os.path.join(folder, "pool.json"), FakeTG, join=fake_join,
                       join_gap=0, clock=lambda: clock[0], main_session=main_session)


async def main():
    root = tempfile.mkdtemp()
    folder = os.path.join(root, "sessions")
    os.makedirs(folder)
    clock = [1_000_000.0]
    REG.update(a={"joined": ["News"]}, b={}, c={"auth": False})
    for n in ("a", "b", "c"):
        session_file(folder, n)
    pool = new_pool(folder, clock)
    await pool.start()
    st = {x["name"]: x for x in pool.status()}
    assert st["a"]["status"] == st["b"]["status"] == "active" and st["c"]["status"] == "dead", st
    assert st["a"]["title"] == "A (@a_bot)", st["a"]
    print("p1 ok: старт: все .session из папки; не вошедший аккаунт — «не работает», рабочие — активны")

    # 1) аккаунт уже в канале — закрепляем за ним, не вступаем второй раз
    assert (await pool.assign("news")).name == "a" and not REG["a"].get("joins")
    # 2) иначе вступает самый свободный: a=1, b=0 -> b
    assert (await pool.assign("other")).name == "b" and REG["b"]["joins"] == ["other"]
    assert (await pool.assign("Other")).name == "b" and REG["b"]["joins"] == ["other"]     # повторно — не вступает
    assert (await pool.assign("third")).name == "a" and REG["a"]["joins"] == ["third"]     # поровну — по имени
    assert {x["name"]: x["channels"] for x in pool.status()} == {"a": 2, "b": 1, "c": 0}
    assert pool.client_for("NEWS").name == "a" and pool.owner("other") == "b"
    print("p2 ok: новый источник: уже есть в канале — за ним; иначе вступает аккаунт с меньшим числом каналов")

    # 3) FloodWait при вступлении: аккаунт отдыхает сколько сказал Telegram, вступает следующий
    REG["b"]["join_error"] = FloodWaitError(300)
    REG["a"]["joins"] = []
    assert (await pool.assign("fourth")).name == "a" and REG["a"]["joins"] == ["fourth"]   # b был свободнее, но отдыхает
    st = {x["name"]: x for x in pool.status()}
    assert st["b"]["status"] == "flood" and st["b"]["until"] == clock[0] + 300, st["b"]
    assert pool.client_for("other").name == "a"            # хозяин канала отдыхает — читает другой аккаунт
    REG["a"]["join_error"] = FloodWaitError(60)
    try:
        await pool.assign("fifth")
        raise AssertionError("все отдыхают — должно быть PoolBusy")
    except PoolBusy as e:
        assert 59 <= e.seconds <= 60, e.seconds
    REG["a"]["join_error"] = REG["b"]["join_error"] = None
    clock[0] += 301
    st = {x["name"]: x for x in pool.status()}
    assert st["a"]["status"] == st["b"]["status"] == "active", st
    assert pool.client_for("other").name == "b"
    print("p3 ok: FloodWait: аккаунт отдыхает ровно столько, сколько сказал Telegram; работу берут другие")

    # 4) в аккаунте слишком много каналов — он больше не вступает, вступает другой
    REG["b"]["joins"] = []
    assert {x["name"]: x["channels"] for x in pool.status()} == {"a": 3, "b": 1, "c": 0}
    assert (await pool.assign("sixth")).name == "b" and (await pool.assign("seventh")).name == "b"
    REG["a"]["join_error"] = ChannelsTooMuchError("too many")          # поровну (3 и 3): первым пробует «a»
    assert (await pool.assign("eighth0")).name == "b" and REG["b"]["joins"] == ["sixth", "seventh", "eighth0"]
    REG["a"]["join_error"] = None
    assert (await pool.assign("ninth")).name == "b", "a в лимите — новых каналов не берёт, хоть и свободнее"
    print("p4 ok: аккаунт в лимите каналов Telegram — новые вступления идут через другие аккаунты")

    # 5) вход слетел (бан, сеанс завершён) — аккаунт выбывает, его каналы переходят к живым
    client_a = pool.client_for("news")
    assert pool.note_error(client_a, AuthKeyUnregisteredError("dead"))
    assert {x["name"]: x["status"] for x in pool.status()}["a"] == "dead"
    assert pool.owner("news") is None and pool.client_for("news").name == "b"
    assert (await pool.assign("news")).name == "b"
    assert not pool.note_error(pool.client_for("news"), ValueError("сеть"))   # обычная ошибка — не про аккаунт
    assert pool.note_error(pool.client_for("news"), FloodWaitError(120))      # FloodWait при чтении — отдыхает
    assert pool.client_for("news") is None                                     # живых нет — читает основной вход
    clock[0] += 121
    print("p5 ok: бан или конец сеанса — аккаунт выбывает, каналы переходят к живым; FloodWait при чтении — пауза")

    # 6) всё помнится после перезапуска
    pool2 = new_pool(folder, clock)
    await pool2.start()
    assert pool2.owner("news") == "b" and pool2.owner("third") is None     # каналы выбывшего «a» свободны
    saved = tio.rjson(os.path.join(folder, "pool.json"), encoding="utf-8")
    assert saved["channels"]["news"] == "b" and saved["accounts"]["a"]["full"] is True, saved
    print("p6 ok: закрепление каналов и состояние аккаунтов сохраняются в pool.json")

    # 7) новый файл входа: каждый неподходящий — отказ с понятной причиной, файл удалён; рабочий — в пуле, права 600
    main_login = session_file(root, "session")                        # основной вход бота
    pool2.main_session = main_login

    async def refused(path, *words):
        try:
            await pool2.add(path)
        except SessionError as e:
            assert all(w in str(e) for w in words) and not os.path.exists(path), (words, str(e))
            return
        raise AssertionError(f"{path} — должен быть отказ: {words}")
    await refused(session_file(folder, "junk", b"hello"), "не файл входа", ".session")
    await refused(session_file(folder, "broken", SQLITE), "повреждён")
    await refused(session_file(folder, "pyro", kind="pyrogram"), "Pyrogram", "Telethon")
    await refused(session_file(folder, "empty", key=b""), "нет входа")
    copy = os.path.join(folder, "copy_of_b.session")
    shutil.copy2(os.path.join(folder, "b.session"), copy)
    await refused(copy, "уже в пуле", "«b»")                         # тот же файл ещё раз — два сеанса на одном ключе
    shutil.copy2(main_login, os.path.join(folder, "main_copy.session"))
    await refused(os.path.join(folder, "main_copy.session"), "основной вход")
    REG["twin"] = {"uid": sum(map(ord, "b"))}                         # тот же аккаунт Telegram, другой вход
    await refused(session_file(folder, "twin"), "Этот аккаунт Telegram уже в пуле", "«b»")
    assert "twin" not in [x["name"] for x in pool2.status()]
    REG["late"] = {"auth": False}
    late = session_file(folder, "late")
    try:
        await pool2.add(late)
        raise AssertionError("не вошедший — отказ")
    except SessionError as e:
        assert "не действует" in str(e) and not os.path.exists(late), e
    REG["d"] = {}
    acc = await pool2.add(session_file(folder, "d"))
    assert acc.name == "d" and "d" in [x["name"] for x in pool2.status()]
    assert oct(os.stat(os.path.join(folder, "d.session")).st_mode & 0o777) == "0o600"
    assert (await pool2.assign("eighth")).name == "d"                  # новый, пустой — вступает первым
    print("p7 ok: загрузка .session: не SQLite, повреждён, Pyrogram, пустой, копия, основной вход, тот же аккаунт, "
          "не вошедший — понятный отказ, мусор не остаётся; рабочий — в пуле, файл доступен только боту")

    # 8) удалить аккаунт: файл стёрт, клиент отключён, его каналы свободны
    d_client = pool2.client_for("eighth")
    await pool2.remove("d")
    assert not os.path.exists(os.path.join(folder, "d.session")) and not d_client.connected
    assert pool2.owner("eighth") is None and "d" not in [x["name"] for x in pool2.status()]
    print("p8 ok: удаление аккаунта: файл и закрепления убраны, клиент отключён")

    # 9) скорость: у каждого аккаунта свой лимит одновременных чтений — аккаунты читают параллельно
    REG.update(x1={}, x2={})
    fast = AccountPool(os.path.join(root, "s2"), os.path.join(root, "p2.json"), FakeTG, join=fake_join,
                       per_account=2, join_gap=0)
    os.makedirs(os.path.join(root, "s2"))
    for n in ("x1", "x2"):
        session_file(os.path.join(root, "s2"), n)
    await fast.start()
    c1, c2 = (a.client for a in fast.usable())
    assert fast.sem_for(c1) is not fast.sem_for(c2) and fast.sem_for(object()) is None

    async def read(c):
        async with fast.sem_for(c):
            await asyncio.sleep(0.3)
    t0 = time.monotonic()
    await asyncio.gather(*(read(c) for c in (c1, c1, c2, c2)))
    took = time.monotonic() - t0
    assert took < 0.5, f"4 чтения на 2 аккаунтах по 2 — одновременно, а заняло {took:.2f} с"
    print(f"p9 ok: аккаунты читают одновременно (asyncio): 4 чтения по 0.3 с заняли {took:.2f} с, а не 1.2 с")
    print("ВСЕ ТЕСТЫ ПУЛА АККАУНТОВ ПРОШЛИ")


asyncio.run(main())
