"""🔥 Тренды по событию: бот раз в 2–3 часа ищет свежие новости по словам из постов канала, ИИ строго отвечает
NO / YES [n], сигнал админу — только про вирусное, одна новость — один раз (sent_alerts.json), всё в фоне.
Поиск, ИИ и время — подделки; MAX — поддельный сервер (настоящие HTTP-запросы), ИИ — через настоящий KeyRotator."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from fake_max import FakeMax  # noqa: E402
from reloader.core.files import load_json_safe, save_json  # noqa: E402
from reloader.services.trends import alerts as AL  # noqa: E402
from reloader.services.trends import keywords as KW  # noqa: E402
from reloader.services.trends import search as SE  # noqa: E402
from reloader.services.trends import store as ST  # noqa: E402
from reloader.services.trends import text as TX  # noqa: E402
from reloader.services.trends import worker as WK  # noqa: E402

NOW = time.mktime((2026, 11, 7, 20, 0, 0, 0, 0, -1))
H = 3600


def news(title, url, source="РИА Новости", age_h=2.0, body="", now=None):
    return SE.normalize({"title": title, "url": url, "source": source, "date": (now or NOW) - age_h * H, "body": body})


class Mem:
    """Файлы в памяти: save(path, data, backup=…) / load(path, default) — как у бота."""

    def __init__(self):
        self.files, self.saves = {}, []

    def save(self, path, data, private=False, backup=False):
        self.files[path] = json.loads(json.dumps(data))
        self.saves.append((path, backup))

    def load(self, path, default):
        return json.loads(json.dumps(self.files.get(path, default)))


# 1) слова и похожесть заголовков
assert {TX.stem(w) for w in ("обновление", "обновления", "обновлением")} == {"обновл"}
assert {TX.stem(w) for w in ("игроки", "игроков")} == {"игрок"} and TX.stem("Roblox") == "roblox"
assert TX.stem("stars") == "star" and TX.stem("news") == "news"
same = [("Roblox заблокировали в России", "В России заблокировали Roblox"),
        ("Роскомнадзор заблокировал Roblox на территории России", "РКН заблокировал Roblox в России — что известно"),
        ("Новый режим в Brawl Stars", "Brawl Stars: новый режим уже в игре")]
other = [("Roblox выпустил обновление для мобильных устройств", "Roblox провёл обновление серверов в России"),
         ("Roblox снизил цены на робуксы", "Roblox запустил новый режим")]
assert all(TX.near_duplicate(TX.stems(a), TX.stems(b)) for a, b in same), same
assert not any(TX.near_duplicate(TX.stems(a), TX.stems(b)) for a, b in other), other
assert TX.clean("[🕹️Мемы](https://max.ru/x) смотри https://t.me/abc @user 𝐑𝐨𝐛𝐥𝐨𝐱").split() == ["🕹️Мемы", "смотри", "Roblox"]
assert TX.cyr_share(["Привет"]) == 1.0 and TX.cyr_share(["Hello world"]) == 0.0
print("t1 ok: основы слов (обновление/обновления → одно), одна новость разными словами — «та же», разные — нет")

# 2) слова для поиска из постов: без подписи, шаблонов и мусора; пары слов; свои слова админа
SIG = "🕹️Роблокс мемы🕹️"
POSTS = ["Вышло новое обновление Roblox! Теперь в игре есть новый режим 🔥\n#roblox\n" + SIG,
         "Roblox снизил цены на робуксы в России. Игроки довольны.\n" + SIG,
         "В Brawl Stars добавили нового бойца, а в Roblox — ивент на Хэллоуин.\n" + SIG,
         "Brawl Stars: новый сезон уже в игре. Обновление весит 500 МБ.\n" + SIG,
         "Подпишись на канал! Roblox запустил новый режим для игроков.\n" + SIG]
kw = KW.extract_keywords(POSTS, signature=SIG)
words = [k["word"] for k in kw]
assert words[0] == "roblox" and "brawl stars" in words, words
assert not {"мемы", "роблокс", "новый", "подпишись", "канал", "brawl", "stars"} & set(words), words
assert KW.make_queries(kw) == ["roblox", "brawl stars", "roblox обновление"], KW.make_queries(kw)
assert KW.make_queries(kw, manual=["Roblox", " ", "робуксы", "Roblox"]) == ["Roblox", "робуксы"]
MODS = ["драконов", "зомби", "мечи", "лодки", "замки", "питомцев"]
shabl = [f"Мод на {m} для Minecraft: новые блоки\nИсточник: партнёрская сеть" for m in MODS]
w2 = [k["word"] for k in KW.extract_keywords(shabl)]
assert w2[0] == "minecraft" and not any("партн" in w for w in w2), w2       # строка-шаблон в каждом посте — не слово
spam = ["креативный " * 40] + [f"Minecraft: мод на {m}" for m in MODS[:5]]
assert KW.extract_keywords(spam)[0]["word"] == "minecraft"                   # один длинный пост не перевешивает
eng = ["Elden Ring DLC announced", "New Elden Ring patch notes", "Elden Ring speedrun record"]
assert KW.make_queries(KW.extract_keywords(eng))[0] == "elden ring" and TX.cyr_share(eng) == 0.0
assert KW.extract_keywords([]) == [] and KW.make_queries([]) == [] and KW.extract_keywords(["", "  "]) == []
print("t2 ok: слова из постов (подпись и строки-шаблоны — мимо, «новый»/«канал» — мимо, пары слов), свои слова")

# 3) поиск: один адрес для одной статьи, даты, ddgs (подделка класса), RSS Google News, память и запасной путь
variants = ["https://www.site.ru/news/1/?utm_source=tg&utm_medium=x", "http://site.ru/news/1", "https://m.site.ru/news/1#c",
            "https://site.ru/news/1/amp/", "https://site.ru/news/1?fbclid=abc&ysclid=z"]
assert len({SE.canonical_url(u) for u in variants}) == 1, {SE.canonical_url(u) for u in variants}
assert SE.canonical_url("https://site.ru/n?id=5") != SE.canonical_url("https://site.ru/n?id=6")
assert SE.parse_date(1794045600) == SE.parse_date(1794045600000) == SE.parse_date("2026-11-07T10:00:00Z") \
    == SE.parse_date("Sat, 07 Nov 2026 10:00:00 GMT") == 1794045600.0
assert SE.parse_date("вчера") is None and SE.parse_date(None) is None
g = SE.normalize({"title": "Roblox заблокировали - РИА Новости", "link": "https://ria.ru/x",
                  "description": "<b>Текст</b>&nbsp;тут", "pubDate": "Sat, 07 Nov 2026 10:00:00 GMT"})
assert (g["title"], g["source"], g["body"], g["canon"]) == ("Roblox заблокировали", "РИА Новости", "Текст тут", "ria.ru/x")
assert SE.normalize({"title": "x", "url": "javascript:alert(1)"}) is None and SE.normalize({"url": "https://a.ru"}) is None
calls = []


class FakeDDGS:
    def __init__(self, proxy=None, timeout=None):
        calls.append(("init", proxy, timeout))

    def news(self, query, **kw):
        calls.append(("news", query, kw))
        return [{"title": "Roblox News", "url": "https://a.ru/1", "date": "2026-11-07T18:00:00+00:00",
                 "body": "b", "source": "A"}]


got = SE.ddg_news("roblox", "ru", proxy="http://p:1", timeout=7, ddgs=FakeDDGS)
assert got[0]["url"] == "https://a.ru/1" and calls[0] == ("init", "http://p:1", 7), calls
assert calls[1] == ("news", "roblox", {"region": "ru-ru", "safesearch": "moderate", "timelimit": "d",
                                        "max_results": 10}), calls[1]
SE.ddg_news("elden ring", "en", ddgs=FakeDDGS)
assert calls[-1][2]["region"] == "us-en"


def ddgs_raising(exc):
    class D:
        def __init__(self, **k):
            pass

        def news(self, *a, **k):
            raise exc
    return D


RatelimitException = type("RatelimitException", (Exception,), {})
DDGSException = type("DDGSException", (Exception,), {})
for exc, want in ((RatelimitException("202 Ratelimit"), SE.RateLimited), (DDGSException("No results found."), None),
                  (OSError("connection reset"), SE.SearchError), (DDGSException("2025 timeout"), SE.SearchError)):
    try:
        res = SE.ddg_news("roblox", ddgs=ddgs_raising(exc))
        assert want is None and res == [], (exc, res)
    except SE.SearchError as e:
        assert want is not None and type(e) is want, (exc, type(e))
real_cls = SE._ddgs_class
SE._ddgs_class = lambda: None                                   # библиотеки нет на сервере
try:
    SE.ddg_news("roblox")
    raise AssertionError("должно было сказать, что библиотеки нет")
except SE.SearchUnavailable as e:
    assert "ddgs" in str(e)
finally:
    SE._ddgs_class = real_cls
RSS = ("<?xml version='1.0'?><rss><channel>"
       "<item><title>Roblox заблокировали в России - РИА Новости</title><link>https://news.google.com/rss/articles/A1</link>"
       "<pubDate>Sat, 07 Nov 2026 16:00:00 GMT</pubDate><description>&lt;a href='x'&gt;Roblox&lt;/a&gt; запрет</description>"
       "<source url='https://ria.ru'>РИА Новости</source></item>"
       "<item><title>Brawl Stars: новый сезон</title><link>https://news.google.com/rss/articles/B2</link>"
       "<pubDate>Sat, 07 Nov 2026 15:00:00 GMT</pubDate></item></channel></rss>").encode()


class Resp:
    def __init__(self, code, content=b""):
        self.status_code, self.content = code, content


class Http:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def get(self, url, **k):
        self.calls.append((url, k))
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


http = Http(Resp(200, RSS))
items = [SE.normalize(r) for r in SE.google_news("roblox", http, "ru")]
assert [i["title"] for i in items] == ["Roblox заблокировали в России", "Brawl Stars: новый сезон"], items
assert items[0]["source"] == "РИА Новости" and items[0]["body"] == "Roblox запрет" and items[0]["t"]
assert http.calls[0][1]["params"]["q"] == "roblox when:1d" and http.calls[0][1]["params"]["hl"] == "ru"
assert http.calls[0][1]["timeout"] == 20 and "Mozilla" in http.calls[0][1]["headers"]["User-Agent"]
for resp, want in ((Resp(429), SE.RateLimited), (Resp(500), SE.SearchError), (Resp(200, b"<html><b>"), SE.SearchError),
                   (OSError("dns"), SE.SearchError)):
    try:
        SE.google_news("roblox", Http(resp))
        raise AssertionError(f"должно было упасть: {resp}")
    except SE.SearchError as e:
        assert type(e) is want, (resp, type(e))
clock, sleeps, c1, c2 = [NOW], [], [], []


def be_ddg(q, lang):
    c1.append(q)
    raise SE.RateLimited("DuckDuckGo: слишком часто")


def be_gn(q, lang):
    c2.append(q)
    return [{"title": f"{q} новость {i}", "url": f"https://n.ru/{q}/{i}", "date": NOW - 3600} for i in range(2)]


def tick(sec):
    sleeps.append(sec)
    clock[0] += sec


sr = SE.Searcher([("DuckDuckGo", be_ddg), ("Google News", be_gn)], pause=(1, 1), clock=lambda: clock[0], sleep=tick)
found, err = sr.search("  Roblox ", "ru")
assert err is None and len(found) == 2 and c1 == ["roblox"] and c2 == ["roblox"], (found, err, c1, c2)
assert sr.search("roblox", "ru") == (found, None) and len(c2) == 1           # тот же запрос — из памяти
assert sleeps and all(s > 0 for s in sleeps)                                  # пауза между запросами в сеть
clock[0] += 600
sr.search("brawl stars", "ru")
assert c1 == ["roblox"] and c2[-1] == "brawl stars"                           # DuckDuckGo отдыхает после «часто»
assert "DuckDuckGo" in sr.status()
clock[0] += 1800
sr.search("minecraft", "ru")
assert c1[-1] == "minecraft"                                                  # отдохнул — снова спрашиваем
clock[0] += 3601
sr.search("roblox", "ru")
assert c2.count("roblox") == 2                                                # память — час
empty = SE.Searcher([("a", lambda q, l: []), ("b", lambda q, l: [])], pause=None)
assert empty.search("x") == ([], None)
broken = SE.Searcher([("a", lambda q, l: (_ for _ in ()).throw(SE.SearchError("a упал"))),
                      ("b", lambda q, l: (_ for _ in ()).throw(OSError("нет сети")))], pause=None)
assert broken.search("x") == ([], "a упал; b: OSError")
budget = SE.Searcher([("a", lambda q, l: [{"title": q, "url": f"https://a.ru/{q}"}])], pause=None, max_per_hour=2)
assert budget.search("q1")[0] and budget.search("q2")[0]
assert budget.search("q3") == ([], "лимит запросов к поиску на час")
assert budget.search("q1")[0], "из памяти — даже когда лимит исчерпан"
print("t3 ok: 🔎 ddgs (подделка класса: регион, сутки, прокси), «часто»/«нет новостей»/нет библиотеки, RSS Google News, "
      "память на час, пауза, лимит в час, запасной поисковик")

# 4) ИИ: строгий вопрос и недоверчивый разбор ответа
shown = [dict(news("Roblox снизил цены на робуксы", "https://a.ru/1"), more=0),
         dict(news("Roblox заблокировали в России", "https://b.ru/2"), more=3),
         dict(news("Вышел новый сезон Brawl Stars", "https://c.ru/3", source=""), more=0)]
prompt = AL.build_prompt("Мой канал", ["roblox", "brawl stars"], ["Пост 1", "Пост 2"], shown, ["Старая новость"], NOW)
assert AL.QUESTION in prompt and AL.QUESTION == (
    "Is there a VIRAL or highly important news event here that perfectly fits this channel's niche? If NO, reply "
    "'NO'. If YES, write a short 3-sentence alert explaining the trend and why the admin should post about it right now.")
assert "«Мой канал»" in prompt and "roblox, brawl stars" in prompt and "- Пост 1" in prompt and "- Старая новость" in prompt
assert "[2] Roblox заблокировали в России (РИА Новости, 2 h ago, +3 more sources report the same)" in prompt, prompt
assert "[3] Вышел новый сезон Brawl Stars (2 h ago)" in prompt and "YES [2]" in prompt
cases = {
    "NO": (False, []), "NO.": (False, []), "**NO**": (False, []), "no": (False, []), "Нет.": (False, []),
    "NO — nothing viral here": (False, []), "Нет, ничего вирусного.": (False, []), "": (False, []), None: (False, []),
    "Среди новостей нет ни одной вирусной.": (False, []), "Кажется, всё обычное.": (False, []),
    "YES": (False, []), "YES: [7]\nчто-то": (False, []),
    "Нет. Новость [2] обычная, не вирусная.": (False, []), "NO [2]": (False, []),
    "Ничего вирусного: [1] и [2] — мелочи.": (False, []), "No viral news here; [3] is minor.": (False, []),
    "YES [2]\nRoblox заблокировали в России. Это главная новость дня. Выложи сейчас.": (True, [1]),
    "**YES** [2]\nТекст.": (True, [1]), "YES 3\nНовый сезон Brawl Stars.": (True, [2]),
    "Да [1] Roblox снизил цены на робуксы. Все обсуждают. Успей первым.": (True, [0]),
    "YES\nRoblox заблокировали в России — это взорвёт ленту. Пиши сейчас.": (True, [1]),
    "Roblox заблокировали в России — вирусная новость [2]. Выкладывай.": (True, [1]),
    "YES\nЭто важнейшее событие недели, о нём говорят все фанаты, выложи прямо сейчас.": (True, [0, 1, 2]),
    "YES [2]": (True, [1]),
}
for ans, (yes, picks) in cases.items():
    v = AL.parse_answer(ans, shown)
    assert (v.yes, v.picks) == (yes, picks), (ans, v)
v = AL.parse_answer("YES [2]\n**Roblox** заблокировали [2]. Раз. Два! Три? Четыре. Пять.", shown)
assert v.alert == "Roblox заблокировали. Раз. Два! Три?", v.alert                 # без разметки, не больше 4 фраз
assert len(AL.parse_answer("YES [1]\n" + "Очень длинно " * 200, shown).alert) <= 700
msg = AL.format_alert("Мой канал", "Текст сигнала.", shown[1])
assert msg.startswith("🔥 Тренд для «Мой канал»") and "📰 Roblox заблокировали в России — РИА Новости" in msg
assert msg.endswith("https://b.ru/2") and "Похоже на вирусную" in AL.format_alert("К", "", shown[0])
print("t4 ok: 🤖 вопрос ИИ слово в слово; NO/Нет/отказ — тишина; YES [n] — про новость n; без формата — "
      "только если ясно, про что; текст сигнала без разметки, до 4 фраз")

# 5) память sent_alerts.json: одна новость — один раз (тот же адрес, перепечатка), у каждого получателя своя
mem, clock = Mem(), [NOW]
store = ST.AlertStore("sent_alerts.json", save=mem.save, load=mem.load, clock=lambda: clock[0])
a = news("Roblox заблокировали в России", "https://www.ria.ru/20261107/roblox.html?utm_source=tg")
store.mark_sent("admin", [a], "Сигнал 1", "t1")
store.save()
assert mem.saves[-1] == ("sent_alerts.json", True), "важный файл — с резервной копией"
assert store.known("admin", news("Совсем другой заголовок", "http://ria.ru/20261107/roblox.html")) == "sent"
assert store.known("admin", news("В России заблокировали Roblox", "https://tass.ru/x")) == "similar"
assert store.known("cab:777", a) is None, "другому получателю — можно"
b = news("Вышел новый сезон Brawl Stars", "https://y.ru/1")
assert store.known("admin", b) is None and store.fresh("admin", [a, b]) == [b]
store.mark_seen("admin", [b])
assert store.known("admin", b) == "seen"
assert ST.AlertStore("sent_alerts.json", save=mem.save, load=mem.load).known("admin", a) == "sent", "переживает перезапуск"
assert store.recent_titles("admin") == ["Roblox заблокировали в России"] and store.stats("admin")[0] == 1
clock[0] += 3 * 86400 + 1
store.save()
assert store.known("admin", b) is None and store.known("admin", a) == "sent"  # отклонённое помним 3 дня, отправленное — дольше
clock[0] += 90 * 86400
store.save()
assert store.known("admin", a) is None and "admin" not in mem.files["sent_alerts.json"]["sent"]
for i in range(620):
    store.mark_sent("admin", [news(f"Новость номер {i} про разное слово{i}", f"https://z.ru/{i}")], f"s{i}", "t1")
store.save()
assert len(mem.files["sent_alerts.json"]["sent"]["admin"]) == 500
d = tempfile.mkdtemp()                                         # настоящий файл: испортился — поднимаем из копии
path = os.path.join(d, "sent_alerts.json")
real = ST.AlertStore(path, save=save_json, load=load_json_safe)
real.mark_sent("admin", [a], "Сигнал", "t1")
real.save()
real.save()
open(path, "w").write('{"sent": {"adm')
assert ST.AlertStore(path, save=save_json, load=load_json_safe).known("admin", a) == "sent", "испорченный файл — из .bak"
os.remove(path)
assert ST.AlertStore(path, save=save_json, load=load_json_safe).known("admin", a) == "sent", "пропавший файл — из .bak"
shutil.rmtree(d)
print("t5 ok: 💾 sent_alerts.json: тот же адрес в любом виде и перепечатка — «уже было»; у каждого получателя "
      "своя память; переживает перезапуск, порчу и пропажу файла; 3 дня / 90 дней; не больше 500")

# 6) одна проверка канала (scan): YES — один сигнал и больше никогда; NO — тишина и ИИ не спрашиваем снова
NEWS, SEARCHED, ASKED, SENT = {}, [], [], []
ANSWERS = []


def search(q, lang):
    SEARCHED.append((q, lang))
    got = NEWS.get(q, [])
    if isinstance(got, Exception):
        raise got
    return ([dict(x) for x in got], None) if not isinstance(got, tuple) else got


def ask(prompt):
    ASKED.append(prompt)
    ans = ANSWERS.pop(0) if ANSWERS else "NO"
    if isinstance(ans, Exception):
        raise ans
    return ans


def send(text, item):
    SENT.append((text, item["url"]))
    return SEND_OK[0]


SEND_OK = [True]
mem, clock = Mem(), [NOW]
store = ST.AlertStore("sent_alerts.json", save=mem.save, load=mem.load, clock=lambda: clock[0])
job = WK.Job("admin", "t1", "Мой канал", POSTS, signature=SIG)


def run(**kw):
    return WK.scan(job, search=search, ask=ask, send=send, store=store, now=clock[0], **kw)


viral = news("Roblox заблокировали в России", "https://ria.ru/1", age_h=1)
copy = news("В России заблокировали Roblox", "https://tass.ru/2", source="ТАСС", age_h=1)
cheap = news("Roblox снизил цены на робуксы", "https://x.ru/3", source="X", age_h=3)
season = news("Новый сезон Brawl Stars", "https://y.ru/4", source="Y", age_h=4)
stale = news("Roblox вышел на биржу", "https://old.ru/5", source="Old", age_h=60)        # двое суток назад — не тренд
NEWS.update({"roblox": [viral, copy, cheap, stale], "brawl stars": [season]})
ANSWERS[:] = ["YES [1]\nRoblox заблокировали в России. Об этом пишут все. Выложи прямо сейчас."]
r = run()
assert r["verdict"] == "yes" and len(SENT) == 1 and r["item"]["url"] == "https://ria.ru/1", r
assert r["queries"] == ["roblox", "brawl stars", "roblox обновление"] and r["found"] == 4 and r["new"] == 4, r
assert SENT[0][0].startswith("🔥 Тренд для «Мой канал»") and "Об этом пишут все" in SENT[0][0]
p1 = ASKED[0]
assert "[1] Roblox заблокировали в России (РИА Новости, 1 h ago, +1 more sources report the same)" in p1, p1
assert "Roblox вышел на биржу" not in p1 and AL.QUESTION in p1
assert store.known("admin", copy) == "sent" and store.known("admin", cheap) == "seen"   # перепечатка — тоже отправлено
assert ("sent_alerts.json", True) in mem.saves
clock[0] += 2.5 * H
r = run()
assert r["verdict"] == "nothing-new" and len(SENT) == 1 and len(ASKED) == 1, r       # ничего нового — ИИ не спрашиваем
reprint = news("РКН заблокировал Roblox в России — что известно", "https://lenta.ru/9", source="Лента", age_h=1,
               now=clock[0])
NEWS["roblox"] = [reprint, viral, copy]
assert run()["verdict"] == "nothing-new" and len(ASKED) == 1, "та же новость в другом издании — не тренд второй раз"
fresh = news("Roblox запустил новый режим с Хэллоуином", "https://z.ru/7", source="Z", age_h=1, now=clock[0])
NEWS["roblox"] = [viral, fresh]
ANSWERS[:] = ["NO"]
r = run()
assert r["verdict"] == "no" and len(SENT) == 1 and "[1] Roblox запустил новый режим" in ASKED[-1], r
assert "Roblox заблокировали в России" not in ASKED[-1].split("Fresh news")[1], "отправленное ИИ не показываем снова"
assert "- Roblox заблокировали в России" in ASKED[-1], "но говорим ИИ, что это уже было"
assert run()["verdict"] == "nothing-new" and len(ASKED) == 2                      # отклонённое — не спрашиваем
big = news("Roblox купила студию Adopt Me за миллиард", "https://w.ru/8", source="W", age_h=1, now=clock[0])
NEWS["roblox"] = [fresh, big]
SEND_OK[0] = False
ANSWERS[:] = ["YES [1]\nRoblox купила Adopt Me. Это шок для игроков. Пиши сейчас."]
r = run()
assert r["verdict"] == "send-error" and store.known("admin", big) is None, "не дошло — не помечаем"
SEND_OK[0] = True
ANSWERS[:] = ["YES [1]\nRoblox купила Adopt Me. Это шок для игроков. Пиши сейчас."]
r = run()
assert r["verdict"] == "yes" and SENT[-1][1] == "https://w.ru/8" and len([s for s in SENT if s[1] == "https://w.ru/8"]) == 2
assert run()["verdict"] == "nothing-new"
def boom(text, item):
    raise OSError("MAX упал")
crash = news("Roblox закрывается навсегда", "https://q.ru/1", source="Q", age_h=1, now=clock[0])
NEWS["roblox"] = [crash]
ANSWERS[:] = ["YES [1]\nRoblox закрывается. Это конец эпохи. Срочно пиши."]
r = WK.scan(job, search=search, ask=ask, send=boom, store=store, now=clock[0])
assert r["verdict"] == "send-error" and "OSError" in r["error"] and store.known("admin", crash) is None
ANSWERS[:] = [WK.NoAI("нет ключей ИИ")]
r = run()
assert r["verdict"] == "no-ai" and store.known("admin", crash) is None, "без ИИ ничего не помечаем — проверим потом"
ANSWERS[:] = [RuntimeError("500 от ИИ")]
assert run()["verdict"] == "ai-error" and store.known("admin", crash) is None
NEWS["roblox"] = SE.SearchError("DuckDuckGo: нет связи")
NEWS["brawl stars"] = ([], "Google News ответил 500")
NEWS["roblox обновление"] = ([], "Google News ответил 500")
r = run()
assert r["verdict"] == "search-error" and r["error"] in ("SearchError: DuckDuckGo: нет связи", "Google News ответил 500"), r
NEWS["brawl stars"] = [news("Brawl Stars добавила режим «Футбол 2.0»", "https://bs.ru/1", source="BS", age_h=1,
                            now=clock[0])]
ANSWERS[:] = ["NO"]
assert run()["verdict"] == "no", "один поисковик упал — остальные запросы всё равно работают"
NEWS.clear()
assert run()["verdict"] == "empty"
n_search = len(SEARCHED)
quiet = WK.Job("admin", "t2", "Пустой", [], signature=SIG)
r = WK.scan(quiet, search=search, ask=ask, send=send, store=store, now=clock[0])
assert r["verdict"] == "no-words" and len(SEARCHED) == n_search, "нет постов — не ищем"
manual = WK.Job("admin", "t3", "Свой", POSTS, manual=["Clash Royale", "Supercell"])
WK.scan(manual, search=search, ask=ask, send=send, store=store, now=clock[0])
assert [q for q, _ in SEARCHED[-2:]] == ["Clash Royale", "Supercell"]
TOPICS = ["альфа", "браво", "чарли", "дельта", "эхо", "фокстрот", "гольф", "отель", "индия", "джульетта", "кило",
          "лима", "майк", "ноябрь", "оскар"]
many = [news(f"Roblox {w} событие", f"https://m.ru/{i}", source="M", age_h=1, now=clock[0]) for i, w in enumerate(TOPICS)]
NEWS["roblox"] = many
ANSWERS[:] = ["NO"]
r = run()
assert r["verdict"] == "no" and "[8]" in ASKED[-1] and "[9]" not in ASKED[-1], "в задание ИИ — не больше 8 новостей"
other_job = WK.Job("cab:2", "t9", "Канал покупателя", POSTS, signature=SIG)
NEWS["roblox"] = [viral]
ANSWERS[:] = ["YES [1]\nRoblox заблокировали. Все обсуждают. Пиши."]
r = WK.scan(other_job, search=search, ask=ask, send=send, store=store, now=clock[0])
assert r["verdict"] == "yes", "другому получателю та же новость — один раз и ему"
assert WK.scan(other_job, search=search, ask=ask, send=send, store=store, now=clock[0])["verdict"] == "nothing-new"
print("t6 ok: 🔥 scan: YES — сигнал один раз (и перепечатки тоже); NO — тишина, ИИ про то же не спрашиваем; не дошло, "
      "нет ИИ, ошибка ИИ — ничего не помечаем; поиск упал — ошибка/другие запросы; старьё, ≤8 новостей, свои слова")

# 7) фоновый обход: раз в 2–3 часа на тему, не все сразу после запуска, ошибки не роняют поток
mem, clock = Mem(), [NOW]
state = WK.TrendState("trends_state.json", save=mem.save, load=mem.load, clock=lambda: clock[0])
runs, logs, targets = [], [], ["t1", "t2"]


def run_theme(tid):
    runs.append((tid, clock[0]))
    if tid == "t2":
        raise RuntimeError("тема сломалась")
    return {"verdict": "no", "t": clock[0], "found": 3, "new": 1, "words": ["roblox"], "channel": "Канал"}


w = WK.TrendWorker(lambda: list(targets), run_theme, state, clock=lambda: clock[0], log=logs.append)
assert w.step() == [] and not runs, "сразу после запуска ничего не проверяем"
first = {t: state.next(t) for t in targets}
assert all(NOW + 300 <= first[t] <= NOW + 1200 for t in targets), first
clock[0] = max(first.values()) + 1
done = w.step()
assert [t for t, _ in done] == ["t1", "t2"] and done[1][1]["verdict"] == "error", done
assert state.last("t1")["verdict"] == "no" and state.last("t2")["error"] == "RuntimeError: тема сломалась"
assert logs == ["🔥 Тренды: тема t2: RuntimeError: тема сломалась"], logs
nxt = {t: state.next(t) for t in targets}
assert all(clock[0] + 2 * H <= nxt[t] <= clock[0] + 3 * H for t in targets), nxt   # и сломанная — через 2–3 часа
clock[0] += H
assert w.step() == [], "раньше 2 часов — не проверяем"
clock[0] = max(nxt.values()) + 1
w.step()
assert len(runs) == 4 and len(logs) == 2, "через пару часов та же ошибка — снова в журнал"
w._report("тема t2", RuntimeError("тема сломалась"))
assert len(logs) == 2, "та же ошибка — в журнал не чаще раза в час"
clock[0] += 1.5 * H
reborn = WK.TrendState("trends_state.json", save=mem.save, load=mem.load, clock=lambda: clock[0])
assert reborn.next("t1") == state.next("t1"), "время следующей проверки переживает перезапуск"
assert WK.TrendWorker(lambda: ["t1"], run_theme, reborn, clock=lambda: clock[0]).step() == []
targets.remove("t2")
w.step()
assert state.next("t2") is None and "t2" not in mem.files["trends_state.json"]["themes"]
w2 = WK.TrendWorker(lambda: (_ for _ in ()).throw(RuntimeError("список упал")), run_theme, state,
                    clock=lambda: clock[0], log=logs.append)
assert w2.step() == [] and "список каналов" in logs[-1]
assert state.ai_take(2, "2026-11-07") and state.ai_take(2, "2026-11-07") and not state.ai_take(2, "2026-11-07")
assert state.ai_used("2026-11-07") == 2 and state.ai_take(2, "2026-11-08"), "новый день — лимит заново"
hits = []


def always_broken(tid):
    hits.append(tid)
    raise ValueError("сломано всегда")


bad_state = WK.TrendState("x.json", save=lambda *a, **k: (_ for _ in ()).throw(OSError("диск полон")),
                          load=lambda p, d: {"themes": {"t1": {"next": 0}}})
bg = WK.TrendWorker(lambda: ["t1"], always_broken, bad_state, interval=(0, 0), tick=0.02, log=lambda m: None)
th = bg.start()
time.sleep(0.4)
assert th.is_alive() and len(hits) >= 3, hits                                 # падает каждый раз — поток жив
assert bg.start() is th, "второй запуск — тот же поток"
bg.stop()
th.join(2)
assert not th.is_alive()
print("t7 ok: ⏱ обход: первая проверка через 5–20 мин, потом раз в 2–3 часа; сбой темы — в журнал (раз в час), "
      "обход идёт дальше; время переживает перезапуск; лимит ИИ на день; поток не умирает ни от чего")

# ---------- бот целиком: поддельный MAX, настоящий KeyRotator, подделка поиска ----------
tele = types.ModuleType("telethon"); tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "TGMAX_HOST", "OPENROUTER_API_KEYS",
          "GEMINI_API_KEYS", "DEEPSEEK_API_KEYS", "TRENDS", "TREND_AI_DAILY"):
    os.environ.pop(k, None)
os.environ.update(MAX_API_URL=srv.url, OPENROUTER_API_URL=srv.url)
DATA = tempfile.mkdtemp()
os.chdir(DATA)
json.dump({"max_token": "TESTTOKEN", "max_channel_link": ""}, open("config.json", "w"))
spec = importlib.util.spec_from_file_location("bot", os.path.join(ROOT, "tg_to_max.py"))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
bot.S.trust_env = False
bot.CFG["admins"] = [1]
bot.CFG["admin_chats"] = {"1": 111}
th1 = bot.THEMES[0]
th1.chat_id = 5000
real_now = time.time()
srv.history[5000] = [{"timestamp": int((real_now - i * 3 * H) * 1000),
                      "body": {"mid": f"p{i}", "text": t, "attachments": []}, "stat": {"views": 100}}
                     for i, t in enumerate(POSTS)]
BOT_NEWS, BOT_SEARCHED, SLOW = {}, [], [0.0]


def fake_backend(q, lang):
    BOT_SEARCHED.append((q, lang))
    if SLOW[0]:
        time.sleep(SLOW[0])
    return [dict(x) for x in BOT_NEWS.get(q, [])]


bot.TREND_SEARCH = SE.Searcher([("fake", fake_backend)], pause=None, ttl=0)


def raw(title, url, source="РИА Новости", age_h=1.0):
    return {"title": title, "url": url, "source": source, "date": time.time() - age_h * H, "body": f"{title}. Подробности."}


# 8) кто под наблюдением и откуда слова
assert bot.trend_targets() == [th1.id]
texts = bot.trend_texts(th1)
assert texts[0] == POSTS[0] and len(texts) == len(POSTS), texts[:2]              # из MAX, новые первыми
with bot.theme_ctx(th1):
    bot.QUEUE.append({"id": "q1", "text": "Свежий пост в очереди про Roblox", "enq": time.time(), "status": "ok"})
assert bot.trend_texts(th1)[0] == "Свежий пост в очереди про Roblox"             # и то, что бот вот-вот выложит
job = bot.trend_job(th1)
assert job.scope == "admin" and job.channel == "mychannel" and job.lang == "ru" and job.signature
th1.own["trend_on"] = False
assert bot.trend_targets() == []
th1.own["trend_on"] = True
th1.chat_id = None
assert bot.trend_targets() == [], "без канала MAX — некуда и не о чем"
th1.chat_id = 5000
print("t8 ok: под наблюдением — темы с каналом MAX и включёнными трендами; слова — из постов канала в MAX и очереди")

# 9) сигнал админу: через настоящий KeyRotator (429 — следующий ключ), в чат админа, с кнопками; один раз
bot.GCFG["ai_keys"] = {"openrouter": ["k-first", "k-second"]}
BOT_NEWS["roblox"] = [raw("Roblox заблокировали в России", "https://ria.ru/roblox-ban?utm_source=tg"),
                      raw("В России заблокировали Roblox", "https://tass.ru/roblox", source="ТАСС"),
                      raw("Roblox снизил цены на робуксы", "https://x.ru/robux", source="X", age_h=3)]
BOT_NEWS["brawl stars"] = [raw("Новый сезон Brawl Stars", "https://y.ru/bs", source="Y", age_h=5)]
srv.ai_replies[:] = [(429, ""), (200, "YES [1]\nRoblox заблокировали в России — об этом пишут все. Твоим "
                                      "подписчикам это важно. Выложи прямо сейчас, пока тема горячая.")]
n_msg, n_ai = len(srv.messages), len(srv.ai_calls)
res = bot.trend_run(th1.id)
assert res["verdict"] == "yes" and res["channel"] == "mychannel", res
assert [k for k, _ in srv.ai_calls[n_ai:]] == ["k-first", "k-second"], srv.ai_calls[n_ai:]   # ключи по кругу
assert AL.QUESTION in srv.ai_calls[-1][1] and "[1] Roblox заблокировали в России" in srv.ai_calls[-1][1]
alerts = [m for m in srv.messages[n_msg:] if (m["body"].get("text") or "").startswith("🔥 Тренд")]
assert len(alerts) == 1 and alerts[0]["chat_id"] == 111, [m["body"].get("text") for m in srv.messages[n_msg:]]
body = alerts[0]["body"]
assert body["text"].startswith("🔥 Тренд для «mychannel»") and "Выложи прямо сейчас" in body["text"]
assert "https://ria.ru/roblox-ban?utm_source=tg" in body["text"]
btns = [b for a in body["attachments"] if a["type"] == "inline_keyboard" for r in a["payload"]["buttons"] for b in r]
assert {"type": "link", "text": "📰 Открыть новость", "url": "https://ria.ru/roblox-ban?utm_source=tg"} in btns, btns
assert any(b.get("payload") == f"a:trendoff:{th1.id}" for b in btns), btns
mid = alerts[0]["mid"]
assert not any(x.get("mid") == mid for x in bot.AUTODEL["temp"]), "сигнал не исчезает из чата через час"
saved = json.load(open("sent_alerts.json"))
assert any(r["canon"] == "ria.ru/roblox-ban" for r in saved["sent"]["admin"]) and os.path.exists("sent_alerts.json.bak")
assert any("🔥 Тренд: Roblox заблокировали" in str(j) for j in bot.JOURNAL), list(bot.JOURNAL)[-3:]
assert bot.PERF["ai"]["тренды"][0] == 1, bot.PERF["ai"]
n_msg, n_ai = len(srv.messages), len(srv.ai_calls)
assert bot.trend_run(th1.id)["verdict"] == "nothing-new"
assert len(srv.messages) == n_msg and len(srv.ai_calls) == n_ai, "второй раз — ни сигнала, ни вопроса ИИ"
bot.TREND_ALERTS = ST.AlertStore("sent_alerts.json", save=bot.save_json, load=bot.load_json_safe)   # «перезапуск»
BOT_NEWS["roblox"].append(raw("РКН заблокировал Roblox в России — что известно", "https://lenta.ru/rkn", source="Лента"))
assert bot.trend_run(th1.id)["verdict"] == "nothing-new" and len(srv.messages) == n_msg, "после перезапуска — тоже"
print("t9 ok: 🔥 сигнал в чат админа (текст ИИ, источник, «📰 Открыть новость», «🔕»), ключи ИИ по кругу (429 → "
      "следующий), не исчезает через час, в журнале; второй раз и после перезапуска — тишина")

# 10) ИИ говорит NO — тишина; ключей нет / лимит на день — ничего не шлём и не помечаем
BOT_NEWS["roblox"] = [raw("Roblox провёл плановые работы на серверах", "https://x.ru/maint", source="X")]
srv.ai_replies[:] = [(200, "NO")]
n_msg = len(srv.messages)
assert bot.trend_run(th1.id)["verdict"] == "no" and len(srv.messages) == n_msg
BOT_NEWS["roblox"] = [raw("Roblox запустил режим с призами", "https://x.ru/prize", source="X")]
bot.GCFG["ai_keys"] = {}
res = bot.trend_run(th1.id)
assert res["verdict"] == "no-ai" and "нет ключей" in res["error"] and len(srv.messages) == n_msg, res
assert bot.TREND_ALERTS.known("admin", SE.normalize(raw("Roblox запустил режим с призами", "https://x.ru/prize"))) is None
bot.GCFG["ai_keys"] = {"openrouter": ["k-second"]}
bot.TREND_AI_DAY = 2
used = bot.TREND_STATE.ai_used(time.strftime("%Y-%m-%d"))
assert used == 2, used                                     # 429 и ответ на другом ключе — одна попытка; + «NO»
n_ai = len(srv.ai_calls)
res = bot.trend_run(th1.id)
assert res["verdict"] == "no-ai" and "лимит" in res["error"] and len(srv.ai_calls) == n_ai, res
bot.TREND_AI_DAY = 150
print("t10 ok: NO — тишина; без ключей ИИ или сверх дневного лимита — ни сигнала, ни вопроса ИИ, ничего не помечено")

# 11) экран «🔥 Тренды», кнопки, свои слова, /trends, «Проверить сейчас»
text, rows = bot.screen("anl", 1)
assert "m:trends" in json.dumps(rows), rows
text, rows = bot.screen("trends", 1)
pl = [b.get("payload") for r in rows for b in r]
assert pl == ["a:trendtg", "a:trendnow", "p:trendwords", "m:anl"], pl
assert "Статус: ✅ слежу" in text and "Сигналов отправлено: 1" in text and "Roblox заблокировали в России" in text, text
assert "Последняя проверка" in text and "Канал: «mychannel»" in text, text
assert bot.do_action("trendtg", 1) == "trends" and th1.own["trend_on"] is False and "выключены" in bot.FLASH.pop(1)
assert json.load(open("themes.json"))["themes"][0]["own"]["trend_on"] is False, "выбор сохраняется"
assert "Статус: ⏸ выключено" in bot.screen("trends", 1)[0]
bot.do_action("trendtg", 1)
assert th1.own["trend_on"] is True and "включены" in bot.FLASH.pop(1)
assert bot.do_action(f"trendoff:{th1.id}", 1) == "trends" and th1.own["trend_on"] is False   # «🔕» из сигнала
bot.FLASH.pop(1, None)
th1.own["trend_on"] = True
assert bot.apply_text("trendwords", "Clash Royale, #supercell; ", 1) == ("✅ Ищу тренды по словам: Clash Royale, supercell",
                                                                           "trends")
assert th1.own["trend_words"] == ["Clash Royale", "supercell"] and "(свои)" in bot.screen("trends", 1)[0]
assert bot.trend_job(th1).manual == ["Clash Royale", "supercell"]
assert bot.apply_text("trendwords", "авто", 1)[0].startswith("✅") and th1.own["trend_words"] == []
assert bot.apply_text("trendwords", " , ;", 1)[0].startswith("Не понял")
bot.handle_callback({"callback_id": "c", "payload": "p:trendwords", "user": {"user_id": 1}, "_chat": 111})
assert bot.PENDING.get(1) == "trendwords"
bot.handle_message(1, 111, "Я", "Minecraft", {"body": {"text": "Minecraft"}})
assert th1.own["trend_words"] == ["Minecraft"] and 1 not in bot.PENDING
th1.own["trend_words"] = []
n_msg = len(srv.messages)
bot.run_command("trends", "", 1, 111)
assert any("🔥 Тренды — сигнал" in (m["body"].get("text") or "") for m in srv.messages[n_msg:]), srv.messages[-1]
BOT_NEWS["roblox"] = [raw("Roblox обновил правила модерации", "https://x.ru/rules", source="X")]
srv.ai_replies[:] = [(200, "NO")]
srv.actions.clear()
n_msg = len(srv.messages)
before = bot.TREND_STATE.next(th1.id)
bot.run_command("trends", "сейчас", 1, 111)
assert any("Ищу свежие новости" in (m["body"].get("text") or "") for m in srv.messages[n_msg:])
end = time.time() + 20
while time.time() < end and not any("ничего вирусного" in (m["body"].get("text") or "") for m in srv.messages[n_msg:]):
    time.sleep(0.05)
reply = [m["body"]["text"] for m in srv.messages[n_msg:] if "ничего вирусного" in (m["body"].get("text") or "")]
assert reply and "по словам: roblox" in reply[0], [m["body"].get("text") for m in srv.messages[n_msg:]]
assert (111, "typing_on") in srv.actions, srv.actions
assert bot.TREND_STATE.next(th1.id) >= time.time() + 2 * H - 60 and bot.TREND_STATE.next(th1.id) != before
assert "Недавно проверял" in bot.trend_check_now(1), "не чаще раза в 5 минут"
bot.TREND_MANUAL.clear()
th1.chat_id = None
assert "привяжи канал" in bot.trend_check_now(1)
th1.chat_id = 5000
print("t11 ok: 📊 → «🔥 Тренды»: статус, слова, последняя/следующая проверка, сигналы; вкл/выкл и «🔕» сохраняются; "
      "свои слова; /trends и «Проверить сейчас» — в фоне, «печатает…», ответ «ничего вирусного», не чаще раза в 5 мин")

# 12) фон не мешает меню: долгая проверка трендов идёт, а кнопки отвечают сразу; поломка трендов не роняет бота
SLOW[0] = 1.5
BOT_NEWS["roblox"] = [raw("Roblox тестирует новый интерфейс", "https://x.ru/ui", source="X")]
srv.ai_replies[:] = [(200, "NO")]
bot.TREND_STATE.schedule(th1.id, time.time() - 1)
worker = WK.TrendWorker(bot.trend_targets, bot.trend_run, bot.TREND_STATE, tick=0.05, log=lambda m: None)
worker.start()
end = time.time() + 10
while time.time() < end and not bot.TREND_LOCK.locked():
    time.sleep(0.01)
assert bot.TREND_LOCK.locked(), "фоновая проверка началась"
t0 = time.perf_counter()
bot.run_update({"update_type": "message_callback", "callback": {"callback_id": "c", "payload": "m:main",
                                                                "user": {"user_id": 1}},
                "message": {"recipient": {"chat_id": 111}}})
took = time.perf_counter() - t0
assert took < 1.0 and bot.TREND_LOCK.locked(), took                           # меню ответило, пока тренды ищут
assert "Как раз идёт проверка" in bot.trend_check_now(1)
end = time.time() + 20
while time.time() < end and bot.TREND_LOCK.locked():
    time.sleep(0.02)
assert bot.TREND_STATE.last(th1.id)["verdict"] == "no", bot.TREND_STATE.last(th1.id)
SLOW[0] = 0
real_texts = bot.trend_texts
bot.trend_texts = lambda t, limit=40: (_ for _ in ()).throw(RuntimeError("тренды сломались"))
bot.TREND_STATE.schedule(th1.id, time.time() - 1)
end = time.time() + 10
while time.time() < end and bot.TREND_STATE.last(th1.id).get("verdict") != "error":
    time.sleep(0.02)
assert bot.TREND_STATE.last(th1.id)["error"] == "RuntimeError: тренды сломались", bot.TREND_STATE.last(th1.id)
assert worker.thread.is_alive(), "поток трендов жив после поломки"
bot.trend_texts = real_texts
worker.stop()
worker.thread.join(3)
os.environ["TRENDS"] = "0"
assert bot.trend_start() is None and bot.TREND_WORKER is None, "TRENDS=0 — тренды выключены"
os.environ.pop("TRENDS")
w1 = bot.trend_start()
assert w1 is bot.trend_start() and w1.thread.is_alive() and w1.thread.daemon, "один фоновый поток, не держит выход"
w1.stop()
print("t12 ok: ⚡ долгая проверка трендов идёт в своём потоке — меню отвечает сразу; поломка трендов — в итог "
      "проверки, поток жив; TRENDS=0 — выключить; поток один")

# 13) две проверки одновременно (фон и «Проверить сейчас», два потока) — сигнал всё равно один
th1.own["trend_on"] = True
bot.GCFG["ai_keys"] = {"openrouter": ["k-second"]}
BOT_NEWS["roblox"] = [raw("Roblox объявил о слиянии с Epic Games", "https://ria.ru/merge", age_h=0.5)]
srv.ai_replies[:] = [(200, "YES [1]\nRoblox и Epic Games объединяются. Это главная новость года. Пиши сейчас.")] * 2
n_msg, outs = len(srv.messages), []
racers = [threading.Thread(target=lambda: outs.append(bot.trend_run(th1.id))) for _ in range(2)]
for r_ in racers:
    r_.start()
for r_ in racers:
    r_.join(30)
alerts = [m for m in srv.messages[n_msg:] if (m["body"].get("text") or "").startswith("🔥 Тренд для")]
assert len(alerts) == 1 and sorted(o["verdict"] for o in outs) == ["nothing-new", "yes"], (len(alerts), outs)
assert "главная новость года" in alerts[0]["body"]["text"]
print("t13 ok: 🔒 фон и «Проверить сейчас» одновременно — проверки идут по очереди, сигнал один")

print("ВСЕ ТЕСТЫ ТРЕНДОВ ПРОШЛИ")
os._exit(0)
