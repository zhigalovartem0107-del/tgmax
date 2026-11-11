"""🏋 Нагрузка: настоящий bot.main() — 10 каналов Telegram постят (с фото, одобрением, закрепом), 5 админов жмут
кнопки и пишут черновики, фон трендов проверяет все темы каждые несколько секунд (поиск + ИИ), рисуются карточки
аналитики (Pillow). MAX — поддельный сервер с задержкой каждого ответа (медленный MAX), всё по HTTP.

Доказываем:
• цикл asyncio не блокируется: ни одного HTTP-запроса из потока цикла (перехват сессий), задержка «пульса» цикла
  мала, и статически — ни одна корутина не вызывает синхронную функцию, которая ходит в сеть;
• меню отвечает быстро всем, посты публикуются, тренды проверяются, одна новость — один сигнал;
• утечек нет: потоки и файловые дескрипторы возвращаются к исходному числу, новых HTTP-сессий не создаётся
  (одна на всех), клиент DuckDuckGo один, кэши ограничены, память кода бота не растёт от круга к кругу."""
import ast
import asyncio
import atexit
import collections
import datetime
import gc
import importlib.util
import json
import os
import random
import re
import sys
import tempfile
import threading
import time
import traceback
import tracemalloc
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from fake_max import FakeMax  # noqa: E402


# ---------- 0) статически: ни одна корутина не зовёт синхронную функцию, которая (через другие) ходит в сеть ----------
def loop_violations(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    net_attr = re.compile(r"^((S|WS)\.(get|post|put|patch|delete|request)|requests\.(get|post|put|delete|request))$")

    def direct(node):
        calls, net = set(), False

        class V(ast.NodeVisitor):
            def visit_Call(self, n):
                nonlocal net
                f = n.func
                if isinstance(f, ast.Attribute) and f.attr in ("to_thread", "run_in_executor"):
                    return                                   # уходит в поток — не в цикле
                if isinstance(f, ast.Name) and f.id in ("in_background", "Thread"):
                    for a in n.args[1:]:
                        self.visit(a)
                    return
                if net_attr.match(ast.get_source_segment(src, f) or ""):
                    net = True
                if isinstance(f, ast.Name):
                    calls.add(f.id)
                self.generic_visit(n)

            def visit_Lambda(self, n):
                return

            def visit_FunctionDef(self, n):
                return

            def visit_AsyncFunctionDef(self, n):
                return

        for st in node.body:
            V().visit(st)
        return calls, net

    graph, tainted = {}, set()
    for name, node in funcs.items():
        graph[name], net = direct(node)
        if net and isinstance(node, ast.FunctionDef):
            tainted.add(name)
    changed = True
    while changed:
        changed = False
        for name, calls in graph.items():
            if name not in tainted and isinstance(funcs[name], ast.FunctionDef) and calls & tainted:
                tainted.add(name)
                changed = True
    return [f"{n} -> {c}" for n, node in funcs.items() if isinstance(node, ast.AsyncFunctionDef)
            for c in sorted(graph[n] & tainted)]


bad = loop_violations(os.path.join(ROOT, "tg_to_max.py"))
assert not bad, "корутины ходят в сеть синхронно (держат цикл): " + "; ".join(bad)
print("s0 ok: статически — ни одна корутина не вызывает синхронный сетевой код (только через поток)")

# ---------- стенд: поддельные MAX (медленный) и Telegram, 10 тем, 5 админов ----------
DELAY = 0.08
srv = FakeMax()
for i in range(10):
    srv.chats[f"ch{i}"] = 7000 + i
work = tempfile.mkdtemp()
os.chdir(work)
json.dump({"api_id": 1, "api_hash": "h", "phone": "+79990000000", "max_token": "TESTTOKEN",
           "max_channel_link": "https://max.ru/ch0", "max_api": srv.url, "verify_ssl": False}, open("config.json", "w"))
json.dump({"channels": ["src0"], "approval_on": False, "interval": 1, "night_on": False, "report_on": False,
           "empty_warn_on": False, "gap_min": 0, "admins": [1, 2, 3, 4, 5],
           "admin_chats": {str(u): 1000 + u for u in range(1, 6)}}, open("settings.json", "w"))
UTC = datetime.timezone.utc
NEXT_ID = collections.defaultdict(lambda: 100)
DATA = collections.defaultdict(list)
WORDS = ("дракон замок робот космос пицца футбол гитара океан вулкан радуга самолёт музей шахматы кактус пингвин "
         "маяк ракета театр комета пустыня джунгли айсберг парусник телескоп библиотека").split()


class FMsg:
    def __init__(self, i, text, photo=False):
        self.id, self.text, self.grouped_id = i, text, None
        self.date = datetime.datetime.fromtimestamp(time.time() - 30, tz=UTC)
        self.photo = object() if photo else None
        self.document = self.gif = self.video_note = self.sticker = self.video = None
        self.fwd_from = None

    async def download_media(self, file=None):
        open(file, "wb").write(b"\xff\xd8binary" * 50)
        return file


class FakeTG:
    def __init__(self, *a, **k):
        pass

    async def connect(self):
        pass

    async def is_user_authorized(self):
        return True

    def on(self, ev):
        return lambda f: f

    async def get_messages(self, ch, min_id=0, limit=30, ids=None):
        msgs = list(DATA.get(ch, []))
        if ids is not None:
            return next((m for m in msgs if m.id == ids), None)
        return sorted([m for m in msgs if m.id > min_id], key=lambda m: -m.id)[:limit]


SYL = "ба ве ги до жу за ки ло му ны по ру се ти фа хо це чу ша ер ин ок ус ям".split()


def fresh_text(n):
    """Каждый пост — про своё (свои «слова»): бот не должен принимать их за повторы."""
    rnd = random.Random(n)
    words = ["".join(rnd.choice(SYL) for _ in range(rnd.randint(3, 4))) for _ in range(14)]
    return f"Roblox: {words[0].capitalize()} {words[1]} — " + " ".join(words[2:]) + f". Выпуск {n}."


tele = types.ModuleType("telethon")
tele.TelegramClient = FakeTG
tele.events = types.SimpleNamespace(NewMessage=lambda *a, **k: None)
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele
sys.modules["telethon.errors"] = tele.errors
os.environ.update(TG_API_ID="1234567", TG_API_HASH="0123456789abcdef0123456789abcdef", TG_PHONE="+79990000000",
                  OPENROUTER_API_URL=srv.url)
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "TGMAX_HOST", "TRENDS"):
    os.environ.pop(k, None)
AT_EXIT, _real_register = [], atexit.register
atexit.register = lambda f, *a, **k: (AT_EXIT.append(f), _real_register(f, *a, **k))[1]
spec = importlib.util.spec_from_file_location("bot", os.path.join(ROOT, "tg_to_max.py"))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
bot.S.trust_env = False
bot.TICK, bot.FRESH_SEC, bot.MIN_GAP_SEC = 0.3, 1, 0
bot.TREND_TICK, bot.TREND_EVERY = 0.3, (2, 4)           # тренды — каждые 2–4 секунды на тему (вместо 2–3 часов)
for i in range(10):
    t = bot.THEMES[0] if i == 0 else bot.create_theme(f"Тема {i}")
    t.chat_id = 7000 + i
    t.own.update(channels=[f"src{i}"], approval_on=(i % 3 == 0), gap_min=0, queue_on=True, night_on=False,
                 empty_warn_on=False, report_on=False, signature_on=True, signature_text=f"Канал {i}",
                 pin_new=(i % 4 == 1), trend_words=["roblox"])
    for j in range(3):
        DATA[f"src{i}"].append(FMsg(NEXT_ID[i] + j, fresh_text(i * 100 + j)))
    NEXT_ID[i] += 3
bot.save_cfg()
bot.GCFG["ai_keys"] = {"openrouter": ["k1", "k2"]}
srv.ai_replies[:] = [(200, f"YES [1] 9/10\nRoblox: важная новость {i}. Все обсуждают. Пиши сейчас.") if i % 4 == 0
                     else (200, "NO") for i in range(5000)]
DDGS_INITS = []


class CountingDDGS:
    """Подделка библиотеки ddgs: «сеть» — 0.15 с, у каждой новости свой адрес (тренды на любой запрос)."""

    def __init__(self, proxy=None, timeout=None):
        DDGS_INITS.append(1)

    def news(self, query, **kw):
        time.sleep(0.15)
        n = random.randint(1, 10 ** 9)
        return [{"title": f"Roblox: событие {n} — {random.choice(WORDS)} {random.choice(WORDS)}",
                 "url": f"https://news.ru/{n}", "date": time.time() - 600, "source": "News", "body": "Подробности"}]


from reloader.services.trends import search as SE  # noqa: E402
bot.TREND_SEARCH = SE.Searcher([("DuckDuckGo", lambda q, lang: SE.ddg_news(q, lang, ddgs=CountingDDGS))],
                               pause=None, ttl=0, max_per_hour=10 ** 6, deadline=10)
srv.delay = DELAY

ON_LOOP_HTTP = collections.Counter()


def guard(sess):
    real = sess.request

    def request(method, url, *a, **k):
        if bot.on_loop_thread():                         # запрос из потока asyncio-цикла — держит ВСЁ
            ON_LOOP_HTTP[(method, bot.perf_endpoint(method, url))] += 1
        return real(method, url, *a, **k)
    sess.request = request


guard(bot.S)
guard(bot.WS)
sys.excepthook = lambda *a: (traceback.print_exception(*a), os._exit(1))   # упал — выходим сразу, не висим
threading.Thread(target=lambda: asyncio.run(bot.main()), daemon=True, name="main-loop").start()
end = time.time() + 15
while bot.MAIN_LOOP is None and time.time() < end:
    time.sleep(0.05)
assert bot.MAIN_LOOP is not None, "главный цикл не запустился"
time.sleep(2)                                            # первичное чтение каналов
for tid in bot.trend_targets():
    bot.TREND_STATE.schedule(tid, time.time())
BEAT, LAGS, MEASURE = [time.perf_counter()], [], [False]


async def heartbeat():
    while True:
        t0 = time.perf_counter()
        BEAT[0] = t0
        await asyncio.sleep(0.02)
        if MEASURE[0]:
            LAGS.append(time.perf_counter() - t0 - 0.02)


asyncio.run_coroutine_threadsafe(heartbeat(), bot.MAIN_LOOP)
LOOP_T = next(t for t in threading.enumerate() if t.name == "main-loop")
STALLS = collections.Counter()


def watchdog():
    """Цикл «завис» дольше 100 мс — запоминаем, где он был (стек потока цикла): так видно, что держит цикл."""
    while True:
        time.sleep(0.01)
        if MEASURE[0] and time.perf_counter() - BEAT[0] > 0.1:
            fr = sys._current_frames().get(LOOP_T.ident)
            st = [f"{f.name}:{f.lineno}" for f in (traceback.extract_stack(fr) if fr else [])
                  if f.filename.endswith("tg_to_max.py") or "/reloader/" in f.filename]
            STALLS[" > ".join(st[-4:]) or "(вне кода бота)"] += 1


threading.Thread(target=watchdog, daemon=True, name="watchdog").start()
STOP = threading.Event()
LAT, CB_N = [], [0]
PAYLOADS = ["m:main", "m:st", "m:ql", "m:anl", "m:trends", "m:sr", "m:sh", "a:anlcard", "a:anlnet", "a:trendnow",
            "a:predict", "m:th"]


def wait_answer(cid, t0):
    deadline = t0 + 30
    while time.perf_counter() < deadline:
        if any(a.get("callback_id") == cid for a in list(srv.answers)[-300:]):
            LAT.append(time.perf_counter() - t0)
            return
        time.sleep(0.02)
    LAT.append(99.0)


def clicker(uid):
    rnd = random.Random(uid)
    while not STOP.is_set():
        CB_N[0] += 1
        cid = f"cb{uid}-{CB_N[0]}"
        pl = rnd.choice(PAYLOADS)
        srv.updates.append({"update_type": "message_callback", "timestamp": int(time.time() * 1000),
                            "callback": {"callback_id": cid, "payload": pl,
                                         "user": {"user_id": uid, "first_name": f"U{uid}"}},
                            "message": {"recipient": {"chat_id": 1000 + uid, "chat_type": "dialog"}}})
        threading.Thread(target=wait_answer, args=(cid, time.perf_counter()), daemon=True, name="waiter").start()
        if pl == "a:predict":                            # черновик на прогноз — следом
            srv.updates.append({"update_type": "message_created", "message": {
                "sender": {"user_id": uid, "first_name": f"U{uid}"},
                "recipient": {"chat_id": 1000 + uid, "chat_type": "dialog"},
                "body": {"text": f"Черновик про Roblox номер {CB_N[0]}?", "mid": f"d{CB_N[0]}"}}})
        STOP.wait(rnd.uniform(0.25, 0.5))


def producer():
    n = 0
    while not STOP.is_set():
        n += 1
        i = random.randrange(10)
        DATA[f"src{i}"].append(FMsg(NEXT_ID[i], fresh_text(n * 7919), photo=(n % 3 == 0)))
        NEXT_ID[i] += 1
        STOP.wait(0.15)


def run_load(seconds):
    trends_pause(False)
    STOP.clear()
    ths = [threading.Thread(target=producer, daemon=True)] + \
          [threading.Thread(target=clicker, args=(u,), daemon=True) for u in range(1, 6)]
    for t in ths:
        t.start()
    time.sleep(seconds)
    STOP.set()
    for t in ths:
        t.join(5)


def published():
    return sum(1 for m in list(srv.messages) if 7000 <= m["chat_id"] < 7010)


def trends_pause(on):
    """На время замеров потоков и памяти — тренды не запускаем (они идут каждые 2–4 с)."""
    w = bot.TREND_WORKER
    w.interval = (3600, 3600) if on else (2, 4)
    for tid in bot.trend_targets():
        bot.TREND_STATE.schedule(tid, time.time() + (3600 if on else 0))


def settle(timeout=60):
    """Ждём, пока всё фоновое доделается: полосы меню, отправки «в фоне», карточки, прогнозы, тренды, ответы.
    И пока бот не заберёт из очереди MAX все нажатия: последнее нажатие нагрузки могло ещё лежать в очереди —
    тогда «тихо» лишь на миг, а потом бот берёт его и начинает рисовать карточку."""
    trends_pause(True)
    end = time.time() + timeout
    try:
        while time.time() < end:
            busy = [t for t in threading.enumerate() if t.name.startswith(
                ("bg-", "anl-cards", "predict", "trend-now", "search-call", "typing", "waiter"))]
            if not busy and srv.polled >= len(srv.updates) and bot.lanes_wait(0.1) and not bot.ANL_CARD_BUSY \
                    and not bot.TREND_LOCK.locked():
                return True
            time.sleep(0.1)
        return False
    finally:
        pass


def fds():
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return 0


# ---------- 1) нагрузка: цикл свободен, меню быстрое, посты идут, тренды проверяются ----------
sessions_made = []
real_init = __import__("requests").Session.__init__


def counting_init(self, *a, **k):
    sessions_made.append(threading.current_thread().name)
    return real_init(self, *a, **k)


__import__("requests").Session.__init__ = counting_init
pub0, img0 = published(), srv.uploads["image"]
MEASURE[0] = True
run_load(20)
MEASURE[0] = False
assert settle(), [t.name for t in threading.enumerate()]
lags = sorted(LAGS)
p99, lag_max = lags[int(len(lags) * 0.99)], lags[-1]
lat = sorted(LAT)
p95 = lat[int(len(lat) * 0.95)]
pub, imgs = published() - pub0, srv.uploads["image"] - img0
trend_runs = [v.get("result", {}).get("verdict") for v in bot.TREND_STATE.themes.values()]
alerts = [m for m in list(srv.messages) if (m["body"].get("text") or "").startswith("🔥 Тренд для")]
alert_urls = collections.Counter((m["chat_id"], u) for m in alerts for u in re.findall(r"https://news\.ru/\d+",
                                                                                         m["body"]["text"]))
print(f"   цикл: пульсов {len(lags)}, задержка p99 {p99 * 1000:.0f} мс, макс {lag_max * 1000:.0f} мс; "
      f"меню: ответов {len(lat)}, p95 {p95:.2f} с, макс {lat[-1]:.2f} с; опубликовано {pub}, картинок {imgs}; "
      f"тренды: проверок записано {len(trend_runs)}, вопросов ИИ {len(srv.ai_calls)}, сигналов {len(alerts)}")
for where, n in STALLS.most_common(5):
    print(f"   цикл занят >100 мс ×{n * 10} мс: {where}")
assert not ON_LOOP_HTTP, f"HTTP из потока asyncio-цикла: {dict(ON_LOOP_HTTP)}"
assert len(lags) > 300 and p99 < 0.2 and lag_max < 1.0, (len(lags), p99, lag_max)
assert len(lat) >= 100 and max(lat) < 30 and p95 < 2.0, (len(lat), p95, lat[-5:])
assert pub >= 25, f"под нагрузкой посты должны выходить: {pub}"
assert imgs >= 3, f"карточки аналитики должны рисоваться и уходить: {imgs}"
assert len(trend_runs) == 10 and len(srv.ai_calls) >= 10 and alerts, (trend_runs, len(srv.ai_calls), len(alerts))
assert all(c == 1 for c in alert_urls.values()), f"одна новость — один сигнал каждому: {alert_urls.most_common(3)}"
assert len(DDGS_INITS) == 1, f"клиент DuckDuckGo — один на все запросы, а создавали {len(DDGS_INITS)}"
assert not sessions_made, f"новые HTTP-сессии под нагрузкой (должны переиспользоваться): {sessions_made[:5]}"
print("s1 ok: 10 каналов постят + 5 админов жмут + тренды и карточки фоном: цикл asyncio не блокируется (0 запросов "
      "из его потока), меню быстрое, посты идут, одна новость — один сигнал, сессии и клиент поиска переиспользуются")

# ---------- 2) утечки: потоки, дескрипторы, кэши, память кода бота от круга к кругу ----------
gc.collect()
threads0, fds0 = threading.active_count(), fds()
tracemalloc.start(1)
FILT = [tracemalloc.Filter(True, os.path.join(ROOT, "tg_to_max.py")),
        tracemalloc.Filter(True, os.path.join(ROOT, "reloader", "*"))]


def bot_bytes():
    gc.collect()
    return sum(st.size for st in tracemalloc.take_snapshot().filter_traces(FILT).statistics("filename"))


sizes = []
for rnd in range(3):
    run_load(8)
    assert settle(), [t.name for t in threading.enumerate()]
    sizes.append(bot_bytes())
tracemalloc.stop()
grow1, grow2 = sizes[1] - sizes[0], sizes[2] - sizes[1]
threads1, fds1 = threading.active_count(), fds()
print(f"   память кода бота по кругам: {[round(s / 1e6, 2) for s in sizes]} МБ (прирост {grow1 / 1e3:.0f} → "
      f"{grow2 / 1e3:.0f} КБ); потоков {threads0} → {threads1}; дескрипторов {fds0} → {fds1}")
assert grow2 < max(1.5 * grow1, 1_500_000) and grow2 < 4_000_000, (grow1, grow2)
assert threads1 <= threads0 + 3, ([t.name for t in threading.enumerate()], threads0)
assert fds1 <= fds0 + 12, (fds0, fds1)
# Бот не стоит на месте: цикл ещё публикует очередь и шлёт уведомления «в фоне» — живой поток в работе не утечка.
# Утечка — поток, который висит (не доделывается за 15 с), или запись, оставшаяся после конца потока.
assert settle() and bot.bg_wait(15), [t.name for t in threading.enumerate()]
with bot.BG_LOCK:
    stale = [t.name for t in bot.BG_THREADS if not t.is_alive()]
assert not stale and not bot.ANL_CARD_BUSY and len(bot.PRED_HIST) <= bot.PRED_HIST_MAX, (
    stale, dict(bot.ANL_CARD_BUSY), len(bot.PRED_HIST))
assert len(bot.TREND_SEARCH.cache) <= bot.TREND_SEARCH.cache_max and len(bot.LANES) == bot.MENU_LANES
assert len(bot.PERF["upd"]) < 40 and len(bot.PERF["http"]) < 60, (list(bot.PERF["upd"]), list(bot.PERF["http"]))
assert not sessions_made and len(DDGS_INITS) == 1
print("s2 ok: утечек нет — потоки и файловые дескрипторы вернулись к исходным, память кода бота не растёт от круга "
      "к кругу, кэши ограничены, новых сессий нет")

# ---------- 3) остановка: сессии и клиенты поиска закрываются ----------
closed, adapters = set(), {id(ad): ad for sess in (bot.S, bot.WS) for ad in sess.adapters.values()}
for ad in adapters.values():                       # один адаптер бывает подключён и на http://, и на https://
    real_close = ad.close
    ad.close = lambda real_close=real_close, key=id(ad): (closed.add(key), real_close())[1]
assert SE._DDGS, "клиент DuckDuckGo жил всё время нагрузки"
bot.close_sessions()
assert closed == set(adapters), (len(closed), len(adapters))                       # все соединения S и WS закрыты
assert not SE._DDGS, "клиенты DuckDuckGo закрыты"
assert bot.close_sessions in AT_EXIT, "закрытие сессий зарегистрировано на выход из процесса (atexit)"
print("s3 ok: при остановке HTTP-сессии бота (MAX/ИИ/ЮKassa и t.me/Google News) и клиенты поиска закрываются (atexit)")

print("ВСЕ НАГРУЗОЧНЫЕ ТЕСТЫ ПРОШЛИ")
os._exit(0)
