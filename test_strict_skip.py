"""Строгое правило: медиа больше 250 МБ, не скачалось или не ушло в MAX — пост ПРОПУСКАЕТСЯ целиком.
Не выходит текст без вложения, не создаётся ссылка, нет кнопок «Повторить», в журнале — причина, без сообщений."""
import tio  # файлы в проверках — с закрытием (без ResourceWarning)
import asyncio, datetime, importlib.util, os, sys, tempfile, types
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from fake_max import FakeMax

tele = types.ModuleType("telethon"); tele.TelegramClient = object
tele.errors = types.SimpleNamespace(SessionPasswordNeededError=Exception)
sys.modules["telethon"] = tele; sys.modules["telethon.errors"] = tele.errors
srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, BOT_TOKEN="TESTTOKEN")
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
    os.environ.pop(k, None)
os.chdir(tempfile.mkdtemp())
spec = importlib.util.spec_from_file_location("bot", ROOT + "/tg_to_max.py")
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
bot.S.trust_env = False

from reloader.services.media.policy import MAX_MEDIA_BYTES, size_skip_reason
MB = 1024 * 1024
assert MAX_MEDIA_BYTES == 250 * MB
assert size_skip_reason(300 * MB) == "Skipped: file >250MB"
assert size_skip_reason(250 * MB) is None and size_skip_reason(1) is None

LOG, NOTES, SENT, API = [], [], [], []
bot.journal = lambda msg, *a, **k: LOG.append(str(msg))
bot.notify = lambda t, tag=True, rows=None: NOTES.append((t, rows))
bot.api_send = lambda chat, text, rows=None, token=None: API.append((chat, text, rows)) or "m"
def _send(chat, text, imgs, video_path=None, file_path=None):
    SENT.append((text, bool(imgs), video_path, file_path))
    if FAIL["send"]:
        raise ConnectionError("сеть оборвалась при отправке")
    return "mid"
FAIL = {"send": False}
bot.send_max = _send
bot.CFG["approval_on"] = False
bot.CFG["dedupe_on"] = False
bot.FRESH_SEC = 0
bot.MAX_RETRY_PAUSES = [0, 0, 0]                       # повторы отправки в MAX — без пауз в тесте
NOW = datetime.datetime.now(datetime.timezone.utc)

def item(vids=(), docs=(), text="Новое видео", iid="a:1"):
    return {"id": iid, "ch": "a", "date": NOW.timestamp(), "enq": NOW.timestamp(), "text": text, "photos": [],
            "pids": [], "vids": list(vids), "docs": list(docs), "sent": 0, "fails": 0, "status": "ok"}

def no_links_no_buttons():
    assert not any(rows for _, _, rows in API), API
    assert not any("http" in (t or "") for t, *_ in SENT), SENT
    assert not any(rows for _, rows in NOTES), NOTES

class Client:
    async def get_messages(self, ch, min_id=0, limit=30, ids=None):
        return []

# 1) загрузка не удалась: пост пропущен целиком — ни текста без видео, ни ссылки, ни кнопки
async def _fail_download(client, ch, vid):
    return None, "CDN: обрыв"
bot.download_video = _fail_download
LOG.clear(); SENT.clear(); API.clear(); NOTES.clear()
it = item(vids=[1]); bot.QUEUE[:] = [it]
assert asyncio.run(bot.publish_item(Client(), 5, it)) == "dropped"
assert not SENT and it not in bot.QUEUE, (SENT, bot.QUEUE)
assert any("Skipped: download failed" in x for x in LOG), LOG
no_links_no_buttons()
print("strict 1 ok: не скачалось — пост пропущен, в журнале причина, без текста и кнопок")

# 2) отправка в MAX не удалась и после 3 повторов: пост с медиа пропущен — без отправки текстом без видео
async def _ok_download(client, ch, vid):
    path = os.path.join(tempfile.mkdtemp(), "v.mp4"); tio.wfile(b"v" * 10, path, "wb")
    return path, None
bot.download_video = _ok_download
FAIL["send"] = True
LOG.clear(); SENT.clear(); API.clear(); NOTES.clear()
it2 = item(vids=[2], iid="a:2"); bot.QUEUE[:] = [it2]
assert asyncio.run(bot.publish_item(Client(), 5, it2)) == "dropped"
assert len(SENT) == 4 and all(s_[2] for s_ in SENT) and it2 not in bot.QUEUE, (SENT, bot.QUEUE)   # всегда с видео
assert any("Skipped: upload failed" in x and "(4 attempts)" in x for x in LOG), LOG
no_links_no_buttons()
# текстовый пост без медиа при сбое сети — как и раньше, остаётся в очереди
txt = item(text="Просто текст", iid="a:3"); bot.QUEUE[:] = [txt]
assert asyncio.run(bot.publish_item(Client(), 5, txt)) == "retry" and txt in bot.QUEUE
FAIL["send"] = False
print("strict 2 ok: сбой отправки — 3 тихих повтора, потом пост с медиа пропущен; текст без медиа ждёт, как раньше")

# 3) файл больше 250 МБ в посте: пост не попадает в очередь вообще; в журнале причина; сообщений нет
class BigDoc:
    id = 77; grouped_id = None; text = "Патч 2 ГБ"; date = NOW
    photo = None; sticker = None; gif = None; video = None; fwd_from = None; reply_to = None
    document = types.SimpleNamespace(mime_type="application/zip", size=300 * MB, attributes=[], name="patch.zip")
    file = types.SimpleNamespace(name="patch.zip", ext=".zip")
class BigClient:
    async def get_messages(self, ch, min_id=0, limit=30, ids=None):
        return [BigDoc()]
bot.QUEUE[:] = []; LOG.clear(); NOTES.clear(); SENT.clear()
bot.LAST_IDS["a"] = 0
asyncio.run(bot.process_channel(BigClient(), "a", {}))
assert not [x for x in bot.QUEUE if x["ch"] == "a"], bot.QUEUE
assert any("Skipped: file >250MB" in x for x in LOG), LOG
assert not NOTES and not SENT, (NOTES, SENT)
print("strict 3 ok: файл больше 250 МБ — пост пропущен целиком, без сообщений и ссылок")

# 4) «застрявших» постов больше нет: ни статуса stuck, ни карточек «Повторить», ни «Без вложения», ни авто-выкладки
assert not hasattr(bot, "send_stuck_card") and not hasattr(bot, "fix_stuck")
assert "media_auto" not in bot.DEFAULTS
legacy = item(vids=[9], iid="a:9"); legacy["status"] = "stuck"; legacy["stuck_why"] = "видео не скачалось"
bot.QUEUE[:] = [legacy]; LOG.clear()
assert bot.drop_legacy_stuck() == 1 and legacy not in bot.QUEUE
assert any("Skipped:" in x for x in LOG), LOG
print("strict 4 ok: нет статуса «ждёт файл», карточек «Повторить» и настройки «выкладывать без него»")

# 5) сообщения и кнопки: пропуски никому не пишутся, ни одной ссылки или кнопки за весь прогон
assert not any(rows for _, _, rows in API) and not any(r for _, r in NOTES)
print("strict 5 ok: пропуски тихие — ни сообщений, ни кнопок, ни ссылок")
print("ВСЕ ТЕСТЫ СТРОГОГО ПРОПУСКА ПРОШЛИ")
os._exit(0)
