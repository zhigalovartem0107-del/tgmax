"""Публикация в Telegram из редактора в личном чате."""

import json
import logging
import os
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

from bot_app.config import env_text, parse_ids
from bot_app.services import legal
from bot_app.services.ad_poster import ad_poster_text

from .bot_api import BotAPI, BotAPIError
from .bot_queue import BotQueue
from .max_media import IncompletePost, MaxPublisher, MediaUnavailable
from .max_source import MaxReadError, MaxSourceAPI, MaxSources, source_link
from .max_webhook import PATH as WEBHOOK_PATH, receiver

LOG = logging.getLogger("telegram")
ALLOWED_MEDIA = ("photo", "video", "document", "audio", "animation", "voice", "video_note", "sticker")
COPY_LIMIT = 10
HELP = ("/post [минуты] — новый пост\n/ad [минуты] — рекламный пост\n"
        "/preview — посмотреть черновик\n/send — добавить в очередь\n"
        "/link URL — добавить кнопку к рекламе\n"
        "/queue — последние публикации\n/cancel [номер] — отменить черновик или публикацию из очереди\n"
        "/terms — документы\n/support — контакт\n\n"
        "Пришли пост, фото, видео, документ или перешли сообщение в этот чат. "
        "После получения всех частей нажми «В очередь».")
MAX_HELP = ("Пришли сюда ссылку на канал MAX и добавь MAX-бота администратором с правом «Читать все сообщения». "
            "Новые посты он перенесёт в Telegram.\n\n"
            "/target — канал назначения в Telegram\n"
            "/sources — источники\n/source ID — подключить канал по ID\n"
            "/remove ID — отключить перенос\n/queue — последние публикации")
STATUS = {"queued": "ждёт", "sending": "отправляется", "sent": "опубликован", "failed": "ошибка",
          "unknown": "проверь канал", "canceled": "отменён"}


def telegram_target(value):
    raw = value.strip()
    if re.fullmatch(r"-100[0-9]{5,15}", raw):
        return raw
    match = re.fullmatch(r"(?:@|(?:https://)?t\.me/)([A-Za-z][A-Za-z0-9_]{3,31})/?", raw, re.I)
    return f"@{match[1]}" if match else ""


def verify_target(api, channel, bot_id):
    chat = api.call("getChat", chat_id=channel)
    if chat.get("type") != "channel":
        raise ValueError("Выбери канал Telegram.")
    cid = chat["id"]
    member = api.call("getChatMember", chat_id=cid, user_id=bot_id)
    if member.get("status") != "creator" and not member.get("can_post_messages"):
        raise ValueError("В Telegram дай боту право публиковать посты.")
    return cid


class TelegramBot:
    def __init__(self, api, queue, admins, channel, clock=time.time, *, owner=None, source_chats=(), channel_id=None):
        self.api = api
        self.queue = queue
        self.admins = admins
        self.channel = channel
        self.clock = clock
        self.owner = owner
        self.source_chats = source_chats
        self.channel_id = channel_id
        self.max_sources = self.max_publisher = None
        self.bot_id = None

    def say(self, chat, text, rows=None):
        params = {"chat_id": chat, "text": text, "link_preview_options": {"is_disabled": True}}
        if rows:
            params["reply_markup"] = {"inline_keyboard": rows}
        return self.api.call("sendMessage", **params)

    def notice(self, chat, text):
        try:
            self.say(chat, text)
        except BotAPIError as exc:
            LOG.warning("Не удалось отправить уведомление Telegram: %s", exc)

    def draft_screen(self, chat, draft):
        n = len(draft["ids"])
        title = "Рекламный черновик" if draft["ad"] else "Черновик"
        buttons = []
        if n:
            buttons.append([{"text": "Предпросмотр", "callback_data": f"preview:{draft['ref']}"},
                            {"text": "В очередь", "callback_data": f"send:{draft['ref']}"}])
        buttons.append([{"text": "Отменить", "callback_data": f"cancel:{draft['ref']}"}])
        self.say(chat, f"{title}. Сообщений: {n}. Задержка: {draft['minutes']} мин.", buttons)

    def copy(self, owner, target, draft):
        ids = draft["ids"]
        params = {"chat_id": target, "from_chat_id": draft.get("source", owner)}
        if draft["ad"]:
            text = ad_poster_text(draft["text"])
            if draft["url"]:
                params["reply_markup"] = {"inline_keyboard": [[{"text": "Подробнее", "url": draft["url"]}]]}
            if draft["media"]:
                return [self.api.call("copyMessage", message_id=ids[0], caption=text, **params)]
            params.pop("from_chat_id")
            return [self.api.call("sendMessage", text=text, **params)]
        if len(ids) == 1:
            return [self.api.call("copyMessage", message_id=ids[0], **params)]
        return self.api.call("copyMessages", message_ids=ids, **params)

    def draft_action(self, chat, draft, action):
        if action == "cancel":
            self.queue.discard(chat)
            self.notice(chat, "Черновик отменён.")
        elif not draft["ids"]:
            self.notice(chat, "Сначала пришли содержимое поста.")
        elif action == "preview":
            result = self.copy(chat, chat, draft)
            if not isinstance(result, list) or len(result) != len(draft["ids"]):
                self.notice(chat, "Telegram показал только часть черновика.")
        elif action == "send" and not self.channel:
            self.notice(chat, "Сначала подключи Telegram-канал через /target.")
        elif action == "send":
            post_id = self.queue.enqueue(chat, draft, self.channel, int(self.clock()))
            self.notice(chat, f"Пост №{post_id} добавлен в очередь.")

    def handle(self, update):
        if self.source_chats and (post := update.get("channel_post")):
            source = (post.get("chat") or {}).get("id")
            if (self.channel and source in self.source_chats and source != self.channel_id and not post.get("has_protected_content")
                    and (post.get("text") or any(key in post for key in ALLOWED_MEDIA))):
                self.queue.relay(self.owner, source, post["message_id"], post.get("media_group_id"),
                                 self.channel, int(self.clock()))
            return
        callback = update.get("callback_query")
        if callback:
            message = callback.get("message") or {}
            actor = callback.get("from") or {}
        else:
            message = update.get("message") or {}
            actor = message.get("from") or {}
        chat = message.get("chat") or {}
        uid = actor.get("id")
        if chat.get("type") != "private" or chat.get("id") != uid:
            return
        text = message.get("text") or ""
        head, _, arg = text.strip().partition(" ")
        command, _, addressed = head.lower().partition("@")
        if uid not in self.admins:
            if not callback and command in ("/id", "/start"):
                self.notice(uid, f"Твой ID в Telegram: {uid}. Все функции доступны владельцу и администраторам.")
            return
        draft = self.queue.draft(uid)
        if callback:
            action, _, ref = (callback.get("data") or "").partition(":")
            valid = draft and ref == draft["ref"] and action in ("cancel", "preview", "send")
            try:
                self.api.call("answerCallbackQuery", callback_query_id=callback["id"],
                              text="" if valid else "Этот черновик уже закрыт.")
            except BotAPIError:
                pass
            if valid:
                self.draft_action(uid, draft, action)
            return
        if addressed:
            # В личном чате Bot API присылает команды с суффиксом только как текст.
            # Сравниваем с известным именем бота в run().
            if addressed != getattr(self, "username", "") and command.startswith("/"):
                return
        if command in ("/start", "/help"):
            self.say(uid, MAX_HELP if self.max_sources else HELP)
        elif command == "/target" or (self.max_sources and telegram_target(text.strip()) and not draft):
            if uid != self.owner:
                self.say(uid, "Менять канал назначения может владелец.")
            else:
                value = arg if command == "/target" else text
                self.set_target(uid, value)
        elif command in ("/sources", "/source", "/remove") or (source_link(text.strip()) and not draft):
            if not self.max_sources:
                self.say(uid, "Для подключения каналов MAX задай MAX_SOURCE_TOKEN в BotHost.")
            elif uid != self.owner:
                self.say(uid, "Подключать источники может владелец.")
            elif command == "/sources":
                self.say(uid, self.max_sources.screen())
            elif command == "/remove":
                self.say(uid, self.max_sources.remove(arg.strip()))
            else:
                try:
                    self.say(uid, self.max_sources.connect(arg.strip() if command == "/source" else text.strip()))
                except MaxReadError as exc:
                    self.say(uid, f"Подключение не проверено. {exc}. Попробуй ещё раз.")
        elif command == "/id":
            self.say(uid, f"Твой ID в Telegram: {uid}")
        elif command == "/support":
            self.say(uid, env_text("TELEGRAM_SUPPORT_CONTACT", "SELLER_CONTACT") or "Контакт поддержки не задан.")
        elif command == "/terms":
            info = legal.merge({}, os.environ)
            links = [f"{label} — {url}" for label, url in legal.documents(info) if url]
            self.say(uid, "\n\n".join(filter(None, (legal.requisites(info), "\n".join(links)))) or "Документы пока не заданы.")
        elif command in ("/post", "/ad"):
            arg = arg.strip()
            if arg and not re.fullmatch(r"[0-9]{1,5}", arg):
                self.say(uid, "Укажи задержку в минутах от 0 до 10080.")
                return
            minutes = int(arg or 0)
            if minutes > 10080:
                self.say(uid, "Максимальная задержка — 7 дней.")
                return
            if draft:
                self.say(uid, "Сначала закончи текущий черновик или нажми /cancel.")
                return
            draft = {"ref": secrets.token_hex(8), "ad": command == "/ad", "minutes": minutes, "ids": [],
                     "text": "", "media": False, "url": ""}
            self.queue.save(uid, draft)
            self.draft_screen(uid, draft)
        elif command == "/queue":
            rows = self.queue.recent(uid)
            status = [f"№{row['id']} — {STATUS[row['status']]}" for row in rows]
            self.say(uid, "\n".join(status) if status else "Публикаций пока нет.")
        elif command == "/cancel" and arg.strip():
            post = arg.strip()
            ok = bool(re.fullmatch(r"[0-9]{1,18}", post)) and self.queue.cancel(uid, int(post))
            self.notice(uid, "Публикация отменена." if ok else "В твоей очереди такого поста нет.")
        elif not draft:
            self.say(uid, "Открой /post или /ad, чтобы создать черновик.")
        elif command in ("/send", "/preview", "/cancel"):
            self.draft_action(uid, draft, command[1:])
        elif command == "/link" and draft["ad"]:
            url = arg.strip()
            try:
                parsed = urlsplit(url)
                valid = (parsed.scheme in ("http", "https") and parsed.hostname and parsed.username is None
                         and not any(ch.isspace() for ch in url) and len(url) <= 500)
            except ValueError:
                valid = False
            if not valid and url != "-":
                self.say(uid, "Пришли /link https://ссылка или /link - для удаления кнопки.")
                return
            draft["url"] = "" if url == "-" else url
            draft['ref'] = secrets.token_hex(8)
            self.queue.save(uid, draft)
            self.draft_screen(uid, draft)
        elif head.startswith("/"):
            self.say(uid, "Эта команда не найдена. Список команд — /help.")
        else:
            self.add_message(uid, draft, message)

    def set_target(self, uid, value):
        target = telegram_target(value)
        if not value.strip():
            current = f"Текущий канал: {self.channel}. " if self.channel else ""
            self.say(uid, current + "Пришли /target https://t.me/имя_канала или /target с числовым ID.")
            return
        if not target:
            self.say(uid, "Нужна публичная ссылка Telegram-канала или числовой ID. Для закрытого канала используй ID.")
            return
        try:
            cid = verify_target(self.api, target, self.bot_id)
            if cid in self.source_chats:
                raise ValueError("Канал назначения уже указан как источник Telegram.")
        except BotAPIError:
            self.say(uid, "Telegram не дал проверить канал. Добавь бота администратором и повтори.")
            return
        except ValueError as exc:
            self.say(uid, str(exc))
            return
        self.queue.set_relay_target(cid)
        self.channel = self.channel_id = cid
        if self.max_sources:
            self.max_sources.target = cid
        self.notice(uid, "Канал назначения сохранён. Посты, уже добавленные в очередь, сохранят прежний адрес.")

    def add_message(self, chat, draft, message):
        content = (message.get("text") or message.get("caption") or "").strip()
        media = any(key in message for key in ALLOWED_MEDIA)
        if not media and not message.get("text"):
            self.say(chat, "Пришли текст или файл. Контакты, геопозиции и служебные сообщения пропускаются.")
            return
        mid = message["message_id"]
        if mid in draft["ids"]:
            return
        if draft["ad"]:
            if (media and not any(k in message for k in ("photo", "video", "document"))
                    or len(ad_poster_text(content).encode("utf-16-le")) // 2 > (1024 if media else 4096)):
                self.say(chat, "Для рекламы пришли текст или одно фото, видео либо документ с короткой подписью.")
                return
            draft.update(ids=[mid], text=content, media=media)
        elif len(draft["ids"]) >= COPY_LIMIT:
            self.say(chat, f"В одном посте до {COPY_LIMIT} сообщений. Нажми «В очередь» или «Отменить».")
            return
        else:
            draft["ids"].append(mid)
            draft["ids"].sort()
        draft['ref'] = secrets.token_hex(8)
        self.queue.save(chat, draft)
        self.draft_screen(chat, draft)

    def publish_due(self):
        row = self.queue.claim(int(self.clock()))
        if row is None:
            return False
        draft = json.loads(row["body"])
        reason = ""
        try:
            if "max_mid" in draft:
                if not self.max_publisher:
                    raise MediaUnavailable("MAX_SOURCE_TOKEN не задан.")
                self.max_publisher.publish(draft)
                sent = True
            else:
                posted = self.copy(row["owner"], draft["channel"], draft)
                sent = isinstance(posted, list) and len(posted) == len(draft["ids"])
        except MediaUnavailable as exc:
            state, reason = "failed", f" {exc}"
        except IncompletePost:
            state = "unknown"
        except BotAPIError as exc:
            if exc.code == 429:
                self.queue.result(row["id"], "queued", int(self.clock()) + max(1, exc.retry_after or 30))
                return True
            state = "failed" if exc.code else "unknown"
        else:
            state = "sent" if sent else "unknown"
        self.queue.result(row["id"], state)
        if state != "sent" or "max_mid" not in draft:
            self.notice(row["owner"], f"Пост №{row['id']} — {STATUS[state]}.{reason}")
        return True

    def run(self, me):
        self.username = (me.get("username") or "").lower()
        poll_after = 0
        while True:
            if self.max_sources and time.monotonic() >= poll_after:
                poll_after = time.monotonic() + 3
                try:
                    for notice in self.max_sources.poll():
                        self.notice(self.owner, notice)
                except MaxReadError as exc:
                    LOG.warning("Не удалось проверить новые посты MAX: %s", exc)
                    poll_after = time.monotonic() + 30
            try:
                timeout = 1 if self.publish_due() else 5
                updates = self.api.call("getUpdates", offset=self.queue.offset(), timeout=timeout,
                                        allowed_updates=["message", "callback_query", "channel_post"])
                for update in updates:
                    with self.queue.db:
                        try:
                            self.handle(update)
                        except BotAPIError as exc:
                            LOG.warning("Запрос при обработке сообщения не подтверждён: %s", exc)
                        self.queue.advance(update["update_id"])
            except BotAPIError as exc:
                if exc.code == 409:
                    raise ValueError("Ошибка 409. Останови вторую копию бота или отключи его webhook.") from None
                if exc.code in (401, 403):
                    raise
                LOG.warning("Не удалось получить обновления Telegram: %s", exc)
                time.sleep(max(2, min(exc.retry_after or 2, 60)))


def start(data):
    token = env_text("TELEGRAM_BOT_TOKEN")
    owner = env_text("TELEGRAM_OWNER_ID")
    configured_channel = env_text("TELEGRAM_CHANNEL_ID")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
        raise ValueError("Задай TELEGRAM_BOT_TOKEN из BotFather.")
    if not re.fullmatch(r"[1-9][0-9]{0,17}", owner):
        raise ValueError("Задай числовой TELEGRAM_OWNER_ID.")
    if configured_channel and not telegram_target(configured_channel):
        raise ValueError("TELEGRAM_CHANNEL_ID должен быть ID канала, публичной ссылкой или @именем.")
    sources = set()
    for raw in re.split(r"[,\s]+", env_text("TELEGRAM_SOURCE_IDS")):
        if not raw:
            continue
        if not re.fullmatch(r"-100[0-9]{5,15}", raw):
            raise ValueError("TELEGRAM_SOURCE_IDS принимает числовые ID каналов через запятую.")
        sources.add(int(raw))
    max_token = env_text("MAX_SOURCE_TOKEN")
    webhook_url = env_text("MAX_WEBHOOK_URL")
    webhook_secret = env_text("MAX_WEBHOOK_SECRET")
    port = env_text("PORT") or "8080"
    if webhook_url or webhook_secret:
        url = urlsplit(webhook_url)
        if (not max_token or url.scheme != "https" or not url.hostname or url.username is not None
                or url.path != WEBHOOK_PATH or url.port is not None or url.query or url.fragment
                or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", webhook_secret)):
            raise ValueError("Для MAX webhook задай MAX_SOURCE_TOKEN, HTTPS-адрес /max-webhook и MAX_WEBHOOK_SECRET.")
        if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
            raise ValueError("Проверь порт веб-приложения в BotHost.")
    db_path = Path(data) / "telegram-bot.sqlite3"
    queue = BotQueue(db_path)
    try:
        with requests.Session() as session:
            api = BotAPI(token, session)
            me = api.call("getMe")
            queue.bind(me["id"])
            saved = queue.relay_target()
            channel = saved or telegram_target(configured_channel)
            channel_id = (saved or api.call("getChat", chat_id=channel)["id"]) if sources and channel else saved
            if channel_id in sources:
                raise ValueError("TELEGRAM_SOURCE_IDS не должен включать канал назначения.")
            bot = TelegramBot(api, queue, {int(owner), *parse_ids(env_text("TELEGRAM_ADMIN_IDS"))}, channel,
                              owner=int(owner), source_chats=sources, channel_id=channel_id)
            bot.bot_id = me["id"]
            if max_token:
                max_api = MaxSourceAPI(max_token, session)
                bot.max_sources = MaxSources(queue, max_api, int(owner), channel, bot.clock, bool(webhook_url))
                bot.max_publisher = MaxPublisher(max_api, api)
            if "--check" in sys.argv:
                if max_token:
                    try:
                        max_api.read("/me")
                    except MaxReadError as exc:
                        raise ValueError(f"Проверь MAX_SOURCE_TOKEN. {exc}") from None
                if channel:
                    verify_target(api, channel, me["id"])
                    print("Telegram: токен принят, право публикации подтверждено.", flush=True)
                else:
                    print("Telegram: токен принят. Укажи канал назначения в чате через /target.", flush=True)
            elif webhook_url:
                with receiver(db_path, int(port), webhook_secret):
                    try:
                        max_api.subscribe(webhook_url, webhook_secret)
                    except MaxReadError as exc:
                        raise ValueError(f"MAX не подтвердил подписку на webhook. {exc}") from None
                    bot.run(me)
            else:
                bot.run(me)
    finally:
        queue.close()
