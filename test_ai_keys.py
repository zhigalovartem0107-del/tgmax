"""🤖 Ключи ИИ (Gemini, OpenRouter, DeepSeek): несколько ключей на провайдера, ввод в панели, ротация при 429.
Ключи из панели главнее переменных окружения, подхватываются сразу и переживают перезапуск."""
import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import asyncio, importlib.util, json, os, sys, tempfile, time, types
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from fake_max import FakeMax

tele = types.ModuleType("telethon"); tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
srv.token = "HOSTTOKEN"                       # фальшивый MAX принимает токен этого бота
os.environ.update(MAX_API_URL=srv.url, BOT_TOKEN="HOSTTOKEN", TGMAX_HOST="1", OWNER_ID="1")
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "GEMINI_API_KEYS", "OPENROUTER_API_KEYS",
          "DEEPSEEK_API_KEYS"):
    os.environ.pop(k, None)
DATA = tempfile.mkdtemp()
os.chdir(DATA)
tio.wjson({"max_token": "HOSTTOKEN", "max_channel_link": ""}, "config.json", "w")


def load_bot(name):
    spec = importlib.util.spec_from_file_location(name, ROOT + "/tg_to_max.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    b.S.trust_env = False
    return b


bot = load_bot("bot")
OUT = []
bot.api_send = lambda chat, text, rows=None, token=None: (OUT.append((text or "", rows or [])), "m")[1]
bot.send_screen = lambda chat, text, rows=None: (OUT.append((text or "", rows or [])), "m")[1]
bot.api_answer = lambda cid, text, rows=None: OUT.append((text or "", rows or []))


def msg(uid, chat, text, mid="m-1"):
    bot.handle_update({"update_type": "message_created", "message": {
        "sender": {"user_id": uid, "first_name": f"U{uid}"}, "recipient": {"chat_id": chat, "chat_type": "dialog"},
        "body": {"text": text, "mid": mid}}})


def press(uid, chat, payload):
    bot.handle_update({"update_type": "message_callback", "callback": {
        "callback_id": "c", "payload": payload, "user": {"user_id": uid, "first_name": f"U{uid}"}},
        "message": {"recipient": {"chat_id": chat}}})


def payloads(rows):
    return [b.get("payload") for r in rows for b in r if b.get("type") == "callback"]


from reloader.services.ai.keys import parse_keys, mask_keys  # noqa: E402

# 1) разбор и маскировка: запятые, переносы строк, лишние пробелы и повторы
assert parse_keys(" k1, k2\nk3 ,, k2 \n") == ["k1", "k2", "k3"]
assert parse_keys("") == [] and parse_keys(None) == []
assert mask_keys(["abcdefgh1234", "zzzzyyyyxxxx"]) == "••••1234, ••••xxxx"
print("a1 ok: ключи через запятую и с новой строки; показываем только хвост")

# 2) панель: кнопки для трёх провайдеров; ключи присланы сообщением — сохранены, сообщение удалено, ответ маскирован
press(1, 61000, "m:clkeys")
assert {"p:claikeys:gemini", "p:claikeys:openrouter", "p:claikeys:deepseek"} <= set(payloads(OUT[-1][1])), OUT[-1][1]
G = ["AIzaFIRSTKEY0001", "AIzaSECONDKEY0002", "AIzaTHIRDKEY0003"]
srv.deleted.clear()
press(1, 61000, "p:claikeys:gemini")
msg(1, 61000, f"{G[0]}, {G[1]}\n{G[2]}", mid="mid-gem")
said = " ".join(o[0] for o in OUT[-3:])
assert bot.GCFG["ai_keys"]["gemini"] == G, bot.GCFG.get("ai_keys")
assert "mid-gem" in srv.deleted, srv.deleted
assert "••••0001" in said and "3" in said and not any(k in said for k in G), said
press(1, 61000, "m:clkeys")
assert "••••0001, ••••0002, ••••0003" in OUT[-1][0] and not any(k in OUT[-1][0] for k in G), OUT[-1][0]
assert "a:claikrm:gemini" in payloads(OUT[-1][1])
print("a2 ok: ключи Gemini — в панели, сообщение удалено, в ответе и на экране только «••••хвост»")


# 3) ИИ-сервис берёт ключи из панели (они главнее переменной) — сразу, без перезапуска
class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


CALLS = []
PLAN = {}            # ключ -> код ответа (по умолчанию 200)


def fake_post(url, headers=None, json=None, timeout=None, **kw):
    key = (headers or {}).get("x-goog-api-key") or (headers or {}).get("Authorization", "").removeprefix("Bearer ")
    CALLS.append((url, key))
    code = PLAN.get(key, 200)
    if code == 429:
        return Resp(429, {"error": {"message": "rate limit"}}, {"Retry-After": "30"})
    if code != 200:
        return Resp(code, {"error": {"message": "bad"}})
    if "generateContent" in url:
        return Resp(200, {"candidates": [{"content": {"parts": [{"text": f"Пересказ от Gemini ({key[-4:]})"}]}}]})
    return Resp(200, {"choices": [{"message": {"content": f"Пересказ ({key[-4:]})"}}]})


bot.S.post = fake_post
os.environ["GEMINI_API_KEYS"] = "envkeyAAAA1111,envkeyBBBB2222"
assert bot.ai_keys("gemini") == G, bot.ai_keys("gemini")                 # панель главнее переменной
out = bot.ai_rewrite("Вышло обновление 1.2")
assert out == "Пересказ от Gemini (0001)" and CALLS[-1][1] == G[0], (out, CALLS)
assert "generateContent" in CALLS[-1][0]
press(1, 61000, "p:claikeys:gemini")
msg(1, 61000, "AIzaNEWKEY99990009", mid="mid-gem2")
assert bot.ai_rewrite("Вышло обновление 1.3") == "Пересказ от Gemini (0009)", CALLS[-1]   # новый ключ — сразу
assert CALLS[-1][1] == "AIzaNEWKEY99990009"
print("a3 ok: ключи из панели главнее переменной и подхватываются сразу, без перезапуска")

# 4) 429: переходим на следующий ключ и остаёмся на нём; другой провайдер — если у этого все исчерпаны
press(1, 61000, "p:claikeys:gemini"); msg(1, 61000, "gemKEY_A_0001, gemKEY_B_0002", mid="mid-g3")
PLAN.clear(); CALLS.clear()
PLAN["gemKEY_A_0001"] = 429
assert bot.ai_rewrite("Пост") == "Пересказ от Gemini (0002)", CALLS
assert [k for _, k in CALLS] == ["gemKEY_A_0001", "gemKEY_B_0002"], CALLS
CALLS.clear()
assert bot.ai_rewrite("Пост 2") == "Пересказ от Gemini (0002)" and [k for _, k in CALLS] == ["gemKEY_B_0002"], CALLS
PLAN["gemKEY_B_0002"] = 429
press(1, 61000, "p:claikeys:openrouter"); msg(1, 61000, "sk-or-v1-OPEN0005", mid="mid-or")
CALLS.clear()
assert bot.ai_rewrite("Пост 3") == "Пересказ (0005)", CALLS                 # Gemini исчерпан — OpenRouter
assert CALLS[-1][0].endswith("/chat/completions") and "openrouter" in CALLS[-1][0], CALLS
print("a4 ok: 429 — следующий ключ (и дальше работаем с ним); все ключи Gemini исчерпаны — OpenRouter")

# 5) все ключи всех провайдеров исчерпаны (429): бот НЕ падает и НЕ пропускает — выкладывает ОРИГИНАЛ без
#    пересказа; в журнал — точная причина «AI skipped: all keys exhausted. Falling back to local rules.»
PLAN["sk-or-v1-OPEN0005"] = 429
press(1, 61000, "p:claikeys:deepseek"); msg(1, 61000, "sk-deep-0007", mid="mid-ds")
PLAN["sk-deep-0007"] = 429
for r in bot.AI_ROTATORS.values():
    r.reset()
SENT = []
bot.send_max = lambda chat, text, imgs, video_path=None, file_path=None: SENT.append(text) or "mid"
bot.ai_on = lambda: True
bot.JOURNAL.clear()
it = {"id": "x:1", "ch": "src", "date": time.time(), "enq": time.time(), "text": "Новость дня", "photos": [],
      "vids": [], "docs": [], "sent": 0, "fails": 0, "status": "ok"}
bot.QUEUE[:] = [it]
assert asyncio.run(bot.publish_item(None, 5, it)) == "ok", it               # пост ВЫХОДИТ, не пропускается
assert SENT and SENT[0].startswith("Новость дня") and not it.get("ai_text"), SENT   # оригинал, без пересказа
assert bot.AI_EXHAUSTED_LOG == "AI skipped: all keys exhausted. Falling back to local rules."
assert any(bot.AI_EXHAUSTED_LOG in j for j in bot.JOURNAL), list(bot.JOURNAL)[-3:]
# и ещё раз — тот же пост при повторной отправке не зовёт ИИ заново и так же выходит
SENT.clear(); it2x = dict(it, id="x:1b", sent=0); bot.QUEUE[:] = [it2x]
assert asyncio.run(bot.publish_item(None, 5, it2x)) == "ok" and SENT[0].startswith("Новость дня"), SENT
print("a5 ok: все ключи 429 — бот не падает, выкладывает оригинал без пересказа, точная причина в журнале")

# 6) ИИ сработал — пост выходит пересказанным (и пересказ не делаем второй раз при повторной отправке)
PLAN.clear()
for r in bot.AI_ROTATORS.values():
    r.reset()
CALLS.clear(); SENT.clear()
it2 = {"id": "x:2", "ch": "src", "date": time.time(), "enq": time.time(), "text": "Новость два",
       "photos": [], "vids": [], "docs": [], "sent": 0, "fails": 0, "status": "ok"}
bot.QUEUE[:] = [it2]
assert asyncio.run(bot.publish_item(None, 5, it2)) == "ok"
assert SENT and SENT[0].startswith("Пересказ"), SENT
print("a6 ok: пост выходит пересказанным через ИИ")

# 6b) 🧠 ИИ-расход «Максимально экономить»: автопересказ — только для VIP-кабинета, остальным оригинал (бережём ключи)
PLAN.clear()
for r in bot.AI_ROTATORS.values():
    r.reset()
SENT.clear()
bot.GCFG["ai_budget"] = "save"
assert bot.ai_budget() == "save" and bot.ai_rewrite_allowed() is False       # по умолчанию кабинет не VIP
itns = {"id": "s:1", "ch": "src", "date": time.time(), "enq": time.time(), "text": "Экономный пост",
        "photos": [], "vids": [], "docs": [], "sent": 0, "fails": 0, "status": "ok"}
bot.QUEUE[:] = [itns]
assert asyncio.run(bot.publish_item(None, 5, itns)) == "ok"
assert SENT and SENT[0].startswith("Экономный пост") and not itns.get("ai_text"), SENT   # ИИ не звали — оригинал
vt = bot.Theme("tV", "VIP-тема", "", None, {}, False, bot.host_id())          # кабинет хозяина — всегда VIP
SENT.clear()
itv = {"id": "s:2", "ch": "src", "date": time.time(), "enq": time.time(), "text": "VIP пост",
       "photos": [], "vids": [], "docs": [], "sent": 0, "fails": 0, "status": "ok"}
bot.QUEUE[:] = [itv]
with bot.theme_ctx(vt):
    assert bot.ai_rewrite_allowed() is True
    assert asyncio.run(bot.publish_item(None, 5, itv)) == "ok"
assert SENT and SENT[0].startswith("Пересказ"), SENT                          # VIP — пересказ работает и в экономии
bot.GCFG["ai_budget"] = "std"
print("a6b ok: 🧠 ИИ-расход «Максимально экономить» — пересказ только для VIP, остальным оригинал")

# 6c) фильтр рекламы — локальный (regex/ключевые слова), ИИ не трогает: обычный пост → ok, явная реклама → ad
CALLS.clear()
v_ok, _ = bot.ad_verdict("Вышло обновление Roblox 1.2, новые скины уже в игре!")
v_ad, why_ad = bot.ad_verdict("Курс по заработку! Регистрация: https://justclick.ru/x?ref=99 — успей, промокод SALE")
assert v_ok == "ok" and v_ad == "ad" and CALLS == [], (v_ok, v_ad, CALLS)    # ни одного вызова ИИ
assert any("трекер" in w for w in why_ad), why_ad                            # justclick.ru распознан локально
print("a6c ok: ад-фильтр работает локально (regex/ключевые слова), без вызовов ИИ")

# 6d) аналитика при исчерпании ключей: 🤖-строки нет — только стандартные строки; бот не падает, пишет причину
bot.INSIGHT_CACHE.clear()
for r in bot.AI_ROTATORS.values():
    r.reset()
for k in ("gemKEY_A_0001", "gemKEY_B_0002", "sk-or-v1-OPEN0005", "sk-deep-0007"):
    PLAN[k] = 429
nums = {"title": "Канал", "subs": 1000, "v24": 100, "v48": 120, "v72": 130, "er24": 10.0, "today": 5, "d7": 20}
assert bot.ai_llm_ready() and bot.ai_insight(nums, bot.card_caption(nums)) is None
assert bot.stats_text(nums) == bot.card_caption(nums) + "\n" + bot.info_line(nums)   # ровно три строки + 📊, без 🤖
PLAN.clear()
for r in bot.AI_ROTATORS.values():
    r.reset()
print("a6d ok: аналитика при 429 — без 🤖-строки, бот не падает")

# 6e) панель: «🧠 ИИ-расход» — три варианта (save/std/max), выбор сохраняется в settings.json
press(1, 61000, "m:cl")
assert any(b.get("payload") == "m:clai" for r in OUT[-1][1] for b in r), payloads(OUT[-1][1])
press(1, 61000, "m:clai")
assert "ИИ-расход" in OUT[-1][0] and all(f"a:claibud:{k}" in payloads(OUT[-1][1]) for k in ("save", "std", "max")), OUT[-1]
press(1, 61000, "a:claibud:save")
assert bot.GCFG["ai_budget"] == "save" and tio.rjson(os.path.join(DATA, "settings.json"))["ai_budget"] == "save"
assert "✅ Максимально экономить" in str(OUT[-1][1]), OUT[-1][1]
press(1, 61000, "a:claibud:std")
assert bot.GCFG["ai_budget"] == "std"
print("a6e ok: панель — «🧠 ИИ-расход» переключается и сохраняется")

# 7) перезапуск: ключи из settings.json на месте; убрали из панели — снова из переменной
bot2 = load_bot("bot_restart")
assert bot2.ai_keys("gemini") == ["gemKEY_A_0001", "gemKEY_B_0002"], bot2.ai_keys("gemini")
assert bot2.ai_keys("openrouter") == ["sk-or-v1-OPEN0005"] and bot2.ai_keys("deepseek") == ["sk-deep-0007"]
assert oct(os.stat(os.path.join(DATA, "settings.json")).st_mode & 0o777) == "0o600"
press(1, 61000, "a:claikrm:gemini")
assert bot.ai_keys("gemini") == ["envkeyAAAA1111", "envkeyBBBB2222"], bot.ai_keys("gemini")
os.environ.pop("GEMINI_API_KEYS")
assert bot.ai_keys("gemini") == []
print("a7 ok: ключи переживают перезапуск; убрали из панели — берём из переменной")
print("ВСЕ ТЕСТЫ КЛЮЧЕЙ ИИ ПРОШЛИ")
os._exit(0)
