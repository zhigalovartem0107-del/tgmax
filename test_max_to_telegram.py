"""Стенд переноса MAX → Telegram, включая смену прав и ошибки вложений."""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import requests

from bot_app.services.telegram.bot_api import BotAPIError
from bot_app.services.telegram.bot_queue import BotQueue
from bot_app.services.telegram.bot_runtime import TelegramBot
from bot_app.services.telegram.max_media import MaxPublisher, MediaUnavailable, download
from bot_app.services.telegram.max_source import MaxSources
from bot_app.services.telegram.max_webhook import receiver

OWNER, ADMIN, SOURCE, OTHER = 42, 44, -401234567, -491234567
TARGET = "@relay_fixture"
NOW = 100


def message(mid, timestamp=101000, source=SOURCE, **body):
    return {"recipient": {"chat_type": "channel", "chat_id": source}, "timestamp": timestamp,
            "body": {"mid": mid, **body}}


def update(post):
    return {"update_type": "message_created", "message": post}


class MAX:
    def __init__(self):
        self.can_read = False
        self.markers = []
        self.events = []
        self.messages = {}

    def chat(self, source):
        return {"type": "channel", "link": "https://max.ru/Example_Channel", "title": "Пример"}

    def updates(self, marker):
        self.markers.append(marker)
        events, self.events = self.events, []
        return {"marker": len(self.markers), "updates": events}

    def authorized(self, source):
        return self.can_read and source == SOURCE

    def read(self, path):
        return {"message": self.messages[path.rsplit("/", 1)[-1]]}


class Telegram:
    def __init__(self):
        self.can_post = False
        self.channel = []
        self.reply = []
        self.reject = {}

    def call(self, action, chat_id=None, upload=None, **kwargs):
        if action == "getChat":
            return {"id": -100555777, "type": "channel"}
        if action == "getChatMember":
            return {"status": "administrator", "can_post_messages": self.can_post}
        if chat_id == TARGET:
            if action in self.reject:
                raise self.reject[action]
            attachments = {key: (name, stream.read()) for key, (name, stream) in (upload or {}).items()}
            self.channel.append((action, attachments, kwargs))
        else:
            self.reply.append((action, chat_id, kwargs))
        return {"message_id": 10}


def media(url, stream, cap):
    if url.endswith("/unavailable"):
        raise MediaUnavailable("Вложение временно недоступно.")
    data = url.rsplit("/", 1)[-1].encode()
    assert len(data) < cap
    stream.write(data)
    stream.seek(0)
    return len(data)


def talk(bot, text, uid=OWNER):
    with bot.queue.db:
        bot.handle({"message": {"chat": {"id": uid, "type": "private"}, "text": text,
                                "from": {"id": uid}, "message_id": 777}})


photo = {"type": "image", "payload": {"url": "https://example.test/photo"}}
pdf = {"type": "file", "payload": {"filename": "Выкройка.pdf", "size": 3}}
api, telegram = MAX(), Telegram()
with TemporaryDirectory() as temporary:
    path = Path(temporary) / "relay.sqlite3"
    queue = BotQueue(path)
    adapter = MaxSources(queue, api, OWNER, TARGET, lambda: NOW)
    bot = TelegramBot(telegram, queue, {OWNER, ADMIN}, TARGET, lambda: NOW, owner=OWNER)
    bot.max_sources = adapter
    bot.max_publisher = MaxPublisher(api, telegram, media)
    talk(bot, "https://max.ru/Example_Channel", ADMIN)
    assert not queue.max_pending()
    talk(bot, "https://max.ru/Example_Channel")
    assert queue.max_pending() == ["example_channel"]
    api.events = [{"update_type": "bot_added", "chat_id": SOURCE, "is_channel": True}]
    assert "Читать все сообщения" in adapter.poll()[0] and queue.max_pending()
    api.can_read = True
    api.events = [{"update_type": "bot_admin_permissions_changed", "chat_id": SOURCE}]
    assert "подключён" in adapter.poll()[0] and not queue.max_pending()
    talk(bot, "/sources")
    assert "перенос включён" in telegram.reply[-1][2]["text"]

    delivered = message("post", text="Кардиган", attachments=[photo, pdf])
    event_pdf = dict(pdf, payload=dict(pdf["payload"], url="https://example.test/pdf"))
    event_post = message("post", text="Кардиган", attachments=[photo, event_pdf])
    api.messages["post"] = delivered
    comment = message("comment", text="Чужой комментарий")
    comment["recipient"]["post_id"] = "post"
    protected = message("closed", text="Защищённый пост")
    protected["protect_content"] = True
    api.events = [update(message("before", 99000)), update(message("out", source=OTHER)),
                  update(comment), update(protected), update(event_post), update(event_post)]
    adapter.poll()
    assert len(queue.recent(OWNER)) == 1
    queue.close()
    bot.queue = adapter.queue = queue = BotQueue(path)
    assert adapter.poll() == [] and api.markers[-1] == 3
    assert bot.publish_due() and not bot.publish_due()
    assert [entry[0] for entry in telegram.channel] == ["sendPhoto", "sendDocument"]
    assert telegram.channel[0][2]["caption"] == "Кардиган"
    assert telegram.channel[1][1]["document"] == ("Выкройка.pdf", b"pdf")

    bad = message("bad", text="Текст", attachments=[dict(photo, payload={"url": "https://example.test/unavailable"})])
    api.messages["bad"] = bad
    api.events = [update(bad)]
    adapter.poll()
    assert bot.publish_due() and queue.recent(OWNER)[0]["status"] == "failed"
    assert len(telegram.channel) == 2

    partial = message("partial", text="Две части", attachments=[photo, event_pdf])
    api.messages["partial"] = partial
    api.events = [update(partial)]
    adapter.poll()
    telegram.reject["sendDocument"] = BotAPIError(429, retry_after=1)
    assert bot.publish_due() and not bot.publish_due()
    assert queue.recent(OWNER)[0]["status"] == "unknown" and len(telegram.channel) == 3

    api.events = [update(message("pending", text="Текст"))]
    adapter.poll()
    talk(bot, f"/remove {SOURCE}")
    assert not bot.publish_due()
    assert queue.recent(OWNER)[0]["status"] == "canceled"
    api.events = [update(message("removed", text="Старый источник"))]
    adapter.poll()
    assert not bot.publish_due()
    queue.close()

with TemporaryDirectory() as temporary:
    path = Path(temporary) / "webhook.sqlite3"
    queue = BotQueue(path)
    bot = TelegramBot(telegram, queue, {OWNER, ADMIN}, None, lambda: NOW, owner=OWNER)
    bot.bot_id = 123
    adapter = MaxSources(queue, api, OWNER, None, lambda: NOW, webhook=True)
    bot.max_sources = adapter
    talk(bot, "/target https://t.me/relay_fixture")
    assert not queue.relay_target()
    telegram.can_post = True
    talk(bot, "/target https://t.me/relay_fixture", ADMIN)
    assert not queue.relay_target()
    talk(bot, "https://t.me/relay_fixture")
    assert queue.relay_target() == bot.channel == adapter.target == -100555777
    talk(bot, "https://max.ru/Example_Channel")
    secret = "fixture_webhook_secret_1"
    added = {"update_type": "bot_added", "chat_id": SOURCE, "is_channel": True}
    with receiver(path, 0, secret) as port:
        endpoint = f"http://127.0.0.1:{port}/max-webhook"
        with requests.Session() as http:
            http.trust_env = False
            assert http.post(endpoint, json=added, timeout=5).status_code == 403
            headers = {"X-Max-Bot-Api-Secret": secret}
            private = {"update_type": "message_created", "message": {"recipient": {"chat_type": "dialog"}}}
            assert http.post(endpoint, json=private, headers=headers, timeout=5).status_code == 200
            assert not queue.max_inbox()
            assert http.post(endpoint, json=added, headers=headers, timeout=5).status_code == 200
    queue.close()
    adapter.queue = queue = BotQueue(path)
    assert queue.relay_target() == -100555777 and len(queue.max_inbox()) == 1
    assert "подключён" in adapter.poll()[0] and not queue.max_inbox()
    queue.close()

try:
    download("http://127.0.0.1/private", BytesIO(), 20)
except MediaUnavailable:
    pass
else:
    raise AssertionError("insecure download URL was allowed")
print("MAX → Telegram: ссылки, права, PDF, сохранение после перезапуска и отказ без дублей — OK")
