"""Проверка меню, прав и публикаций без обращения к Telegram."""

import tempfile
from pathlib import Path
from unittest.mock import Mock

import requests

from bot_app.services.telegram.bot_api import BotAPI, BotAPIError
from bot_app.services.telegram.bot_queue import BotQueue
from bot_app.services.telegram.bot_runtime import TelegramBot

OWNER, OTHER = 42, 43
CHANNEL = "-100100001"
NOW = [100]
calls = []
response = {"error": None, "count": None}


class FakeAPI:
    def call(self, method, **kwargs):
        calls.append((method, kwargs))
        if kwargs.get("chat_id") == CHANNEL and response["error"]:
            raise response["error"]
        if method == "copyMessages":
            return [{"message_id": x} for x in kwargs["message_ids"][:response["count"]]]
        return {"message_id": 901}


def send(update_id, text="", uid=OWNER, chat_type="private", **message):
    payload = {"update_id": update_id, "message": {
        "message_id": update_id, "chat": {"type": chat_type, "id": uid},
        "from": {"id": uid}, "text": text, **message,
    }}
    with queue.db:
        bot.handle(payload)
        queue.advance(update_id)


def click(data, actor=OWNER, update=50):
    with queue.db:
        bot.handle({"callback_query": {"id": "test", "data": data, "from": {"id": actor},
                                      "message": {"chat": {"type": "private", "id": OWNER}}}})
        queue.advance(update)


def on_channel(method):
    return [data for action, data in calls if action == method and data.get("chat_id") == CHANNEL]


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "tg.sqlite3"
    queue = BotQueue(path)
    queue.bind(123)
    bot = TelegramBot(FakeAPI(), queue, {OWNER}, CHANNEL, lambda: NOW[0])
    send(1, "/post", uid=OTHER)
    send(2, "/post", chat_type="group")
    assert queue.draft(OTHER) is None and queue.draft(OWNER) is None and not calls

    send(3, "/post 5")
    old = queue.draft(OWNER)["ref"]
    send(4, "Текст поста")
    send(5, document={"file_id": "fixture-pdf", "file_name": "pattern.pdf"})
    ref = queue.draft(OWNER)["ref"]
    click(f"send:{old}")
    click(f"send:{ref}", actor=OTHER)
    assert queue.draft(OWNER)["ids"] == [4, 5] and not queue.recent(OWNER)
    click(f"send:{ref}")
    click(f"send:{ref}", update=51)
    assert queue.draft(OWNER) is None and len(queue.recent(OWNER)) == 1 and queue.offset() == 52
    assert not bot.publish_due()

    queue.close()
    queue = BotQueue(path)
    queue.bind(123)
    try:
        queue.bind(456)
    except ValueError:
        pass
    else:
        raise AssertionError("different Telegram bot reused queue")
    bot.queue = queue
    NOW[0] = 400
    assert bot.publish_due() and not bot.publish_due()
    assert on_channel("copyMessages")[0]["message_ids"] == [4, 5]
    assert queue.recent(OWNER)[0]["status"] == "sent"

    send(52, "/ad")
    send(53, "/link https://example.test")
    send(54, photo=[{"file_id": "fixture-photo"}], caption="Описание")
    send(55, "/preview")
    send(56, "/send")
    response["error"] = BotAPIError(429, retry_after=4)
    assert bot.publish_due() and queue.recent(OWNER)[0]["status"] == "queued"
    assert not bot.publish_due()
    response["error"] = None
    NOW[0] += 4
    assert bot.publish_due()
    ad = on_channel("copyMessage")[-1]
    assert ad["caption"] == "Реклама\n\nОписание"
    assert ad["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://example.test"

    send(57, "/post")
    send(58, "Готово")
    send(59, "/send")
    response["error"] = BotAPIError()
    assert bot.publish_due() and queue.recent(OWNER)[0]["status"] == "unknown"
    assert not bot.publish_due()
    response["error"] = None

    send(60, "/post")
    send(61, "Текст")
    send(62, document={"file_id": "fixture"})
    send(63, "/send")
    response["count"] = 1
    assert bot.publish_due() and queue.recent(OWNER)[0]["status"] == "unknown"

    send(64, "/post 60")
    send(65, "Скрыто")
    send(66, "/send")
    post = queue.recent(OWNER)[0]["id"]
    assert not queue.cancel(OTHER, post)
    send(67, f"/cancel {post}")
    assert queue.recent(OWNER)[0]["status"] == "canceled"

    send(68, "/post")
    send(69, "Остановлен во время отправки")
    send(70, "/send")
    assert queue.claim(NOW[0]) is not None
    queue.close()
    queue = BotQueue(path)
    assert queue.recent(OWNER)[0]["status"] == "unknown"
    queue.close()

with tempfile.TemporaryDirectory() as directory:
    queue = BotQueue(Path(directory) / "tg.sqlite3")
    source = -100100002
    bot = TelegramBot(FakeAPI(), queue, {OWNER}, CHANNEL, lambda: NOW[0],
                      owner=OWNER, source_chats={source}, channel_id=int(CHANNEL))
    response["count"] = None

    def intake(chat, mid, **extras):
        with queue.db:
            bot.handle({"channel_post": {"chat": {"id": chat, "type": "channel"}, "message_id": mid,
                                        "photo": [{"file_id": "test-photo"}], **extras}})

    intake(-100100003, 31)
    intake(int(CHANNEL), 32)
    intake(source, 33, has_protected_content=True)
    assert not queue.recent(OWNER)
    intake(source, 35, media_group_id="group")
    intake(source, 34, media_group_id="group")
    assert len(queue.recent(OWNER)) == 1 and not bot.publish_due()
    NOW[0] += 5
    assert bot.publish_due() and not bot.publish_due()
    assert on_channel("copyMessages")[-1]["message_ids"] == [34, 35]
    assert on_channel("copyMessages")[-1]["from_chat_id"] == source
    queue.close()

session = Mock()
session.post.side_effect = requests.ConnectionError("secret-token-should-not-be-shown")
try:
    BotAPI("secret-token", session).call("getMe")
except BotAPIError as error:
    assert "secret" not in str(error)
else:
    raise AssertionError("network error must be sanitized")

print("Telegram: права, черновики, PDF, реклама, очередь, перезапуск и неопределённая отправка — OK")

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "ad-actions.sqlite3"
    queue = BotQueue(path)
    bot = TelegramBot(FakeAPI(), queue, {OWNER}, CHANNEL, lambda: NOW[0])
    send(80, "/ad")
    send(81, "Объявление")
    send(82, "/pin")
    send(83, "/deleteafter 2")
    send(84, "/send")
    assert bot.publish_due()
    assert bot.run_action() and calls[-1][0] == "pinChatMessage" and not bot.run_action()
    queue.close()
    bot.queue = queue = BotQueue(path)
    NOW[0] += 120
    response["error"] = BotAPIError(429, retry_after=20)
    assert bot.run_action() and not bot.run_action()
    response["error"] = None
    NOW[0] += 20
    assert bot.run_action() and calls[-1] == ("deleteMessage", {"chat_id": CHANNEL, "message_id": 901})
    assert not bot.run_action()
    queue.close()
print("Telegram: закрепление и удаление рекламы после перезапуска — OK")

with tempfile.TemporaryDirectory() as directory:
    queue = BotQueue(Path(directory) / "news.sqlite3")
    bot = TelegramBot(FakeAPI(), queue, {OWNER}, CHANNEL, lambda: NOW[0])
    bot.news = Mock()
    article = {"title": "Открыта выставка орхидей", "t": NOW[0], "source": "Газета",
               "url": "https://example.org/orchids", "body": "Посетители увидят сто видов цветов."}
    bot.news.search.return_value = ([dict(article, t=NOW[0] - 90000)], None)
    send(90, "/news орхидеи")
    assert not queue.draft(OWNER)
    bot.news.search.return_value = ([article], None)
    send(91, "/news орхидеи")
    assert queue.draft(OWNER)["ids"] == [901]
    assert article["url"] in calls[-2][1]["text"] and article["body"] in calls[-2][1]["text"]
    send(92, "/send")
    assert bot.publish_due()
    assert on_channel("copyMessage")[-1]["message_id"] == 901
    queue.close()
