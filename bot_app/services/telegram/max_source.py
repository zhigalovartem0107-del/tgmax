"""Поиск и подключение MAX-источников из Telegram."""

import json
import re
from urllib.parse import unquote, urlsplit

import requests


MAX_EVENTS = ("bot_added", "bot_removed", "bot_admin_permissions_changed", "message_created")


class MaxReadError(Exception):
    def __init__(self, code=None):
        super().__init__(f"MAX API: {code or 'нет ответа'}")
        self.code = code


def source_link(value):
    try:
        url = urlsplit(value if "://" in value else f"https://{value}")
    except ValueError:
        return ""
    if url.scheme not in ("http", "https") or url.netloc.lower() not in ("max.ru", "www.max.ru", "web.max.ru"):
        return ""
    path = unquote(url.path).strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?", path):
        return ""
    return path if path.startswith("join/") else path.lower()


class MaxSourceAPI:
    def __init__(self, token, session, origin="https://platform-api2.max.ru"):
        self.headers = {"Authorization": token}
        self.session = session
        self.origin = origin.rstrip("/")

    def read(self, path, **params):
        try:
            response = self.session.get(self.origin + path, headers=self.headers, params=params, timeout=(4, 7),
                                        allow_redirects=False)
            if response.status_code != 200:
                raise MaxReadError(response.status_code)
            result = response.json()
        except (requests.RequestException, ValueError):
            raise MaxReadError() from None
        if not isinstance(result, dict):
            raise MaxReadError()
        return result

    def chat(self, cid):
        return self.read(f"/chats/{cid}")

    def authorized(self, cid):
        member = self.read(f"/chats/{cid}/members/me")
        return member.get("is_owner") or (member.get("is_admin") and "read_all_messages" in
                                          (member.get("permissions") or ()))

    def updates(self, marker):
        query = {"limit": 100, "timeout": 0, "types": ",".join(MAX_EVENTS)}
        if marker is not None:
            query["marker"] = marker
        return self.read("/updates", **query)

    def subscribe(self, url, secret):
        subscriptions = self.read("/subscriptions").get("subscriptions") or []
        if any(item.get("url") == url for item in subscriptions):
            return
        try:
            response = self.session.post(self.origin + "/subscriptions", headers=self.headers,
                                         json={"url": url, "secret": secret, "update_types": list(MAX_EVENTS)},
                                         timeout=(4, 10), allow_redirects=False)
            if response.status_code != 200:
                raise MaxReadError(response.status_code)
            if response.json().get("success") is not True:
                raise MaxReadError()
        except (requests.RequestException, ValueError):
            raise MaxReadError() from None


class MaxSources:
    def __init__(self, queue, api, owner, target, clock, webhook=False):
        self.queue = queue
        self.api = api
        self.owner = owner
        self.target = target
        self.clock = clock
        self.webhook = webhook

    def connect(self, value):
        key = source_link(value)
        if key and re.fullmatch(r"-?\d{4,19}", key):
            value = key
        if re.fullmatch(r"-?\d{4,19}", value):
            cid = int(value)
            if not -(2**63) <= cid < 2**63:
                return "ID канала MAX вне допустимого диапазона."
            return self.activate(cid)
        if not key:
            return "Пришли ссылку на канал MAX или команду /source с числовым ID."
        row = self.queue.max_source_by_link(key)
        if row:
            return self.activate(row["id"])
        self.queue.set_max_pending(key)
        return ("Запомнил ссылку. Добавь MAX-бота в администраторы канала с правом «Читать все сообщения». "
                "Когда MAX сообщит о подключении, источник включится. Список — /sources.")

    def activate(self, cid, info=None):
        """Проверяет доступ; транзакцией списка источников управляет вызывающий код."""
        try:
            chat = self.api.chat(cid) if info is None else info
            if chat.get("type") != "channel":
                return "Источник должен быть каналом MAX."
            if not self.api.authorized(cid):
                return "В MAX дай боту право «Читать все сообщения» и повтори подключение."
        except MaxReadError as exc:
            if exc.code in (403, 404):
                return ("MAX пока не даёт доступ. Добавь MAX-бота администратором с правом "
                        "«Читать все сообщения» и повтори подключение.")
            raise
        self.queue.remember_max(cid, source_link(chat.get("link") or ""), self.title(chat, cid))
        if not self.target:
            return "MAX-канал найден. Подключи канал назначения через /target и пришли ссылку MAX ещё раз."
        self.queue.enable_max(cid, int(self.clock() * 1000))
        return f"Источник «{self.title(chat, cid)}» подключён. Новые посты пойдут в Telegram."

    @staticmethod
    def title(chat, cid):
        return str(chat.get("title") or cid)[:120]

    def screen(self):
        lines = []
        for row in self.queue.max_sources()[:20]:
            state = "перенос включён" if row["since"] is not None else "найден в MAX"
            lines.append(f"{row['title']} · {state}\nID {row['id']}")
        for key in self.queue.max_pending()[:10]:
            lines.append(f"max.ru/{key} · ждёт подключения")
        if not lines:
            return "Источников пока нет. Пришли ссылку на канал MAX."
        return "\n\n".join(lines) + "\n\n/source ID — включить · /remove ID — убрать источник"

    def remove(self, value):
        if source_link(value):
            row = self.queue.max_source_by_link(source_link(value))
            if row:
                return self.remove(str(row["id"]))
            return ("Подключение отменено." if self.queue.drop_max_pending(source_link(value))
                    else "Такого источника нет.")
        if not re.fullmatch(r"-?\d{4,19}", value) or not -(2**63) <= int(value) < 2**63:
            return "Укажи ссылку или ID источника из /sources."
        return ("Перенос новых постов из этого канала отключён."
                if self.queue.disable_max(int(value)) else "Такого активного источника нет.")

    def poll(self):
        if self.webhook:
            notices = []
            for row in self.queue.max_inbox():
                with self.queue.db:
                    notices.extend(self.event(json.loads(row["body"])))
                    self.queue.ack_max(row["id"])
            return notices
        page = self.api.updates(self.queue.max_marker())
        notices = []
        with self.queue.db:
            for event in page.get("updates") or ():
                notices.extend(self.event(event))
            if page.get("marker") is not None:
                self.queue.set_max_marker(page["marker"])
        return notices

    def event(self, event):
        kind = event.get("update_type")
        channel_change = kind == "bot_admin_permissions_changed" or (kind == "bot_added" and event.get("is_channel"))
        if channel_change and isinstance(event.get("chat_id"), int):
            cid = event["chat_id"]
            try:
                info = self.api.chat(cid)
            except MaxReadError as exc:
                if exc.code in (403, 404):
                    return ()
                raise
            if info.get("type") != "channel":
                return ()
            key = source_link(info.get("link") or "")
            self.queue.remember_max(cid, key, self.title(info, cid))
            if key in self.queue.max_pending():
                return (self.activate(cid, info),)
            return ()
        if kind == "bot_removed" and isinstance(event.get("chat_id"), int):
            if self.queue.disable_max(event["chat_id"]):
                return (f"MAX-бот потерял доступ к источнику {event['chat_id']}.",)
            return ()
        if kind != "message_created":
            return ()
        message = event.get("message") or {}
        rec = message.get("recipient") or {}
        cid = rec.get("chat_id")
        if rec.get("chat_type") != "channel" or rec.get("post_id") or not isinstance(cid, int):
            return ()
        body = message.get("body") or {}
        mid = body.get("mid")
        ts = message.get("timestamp") or event.get("timestamp")
        if (not isinstance(mid, str) or not mid or not isinstance(ts, int)
                or message.get("has_protected_content") or message.get("protect_content")):
            return ()
        urls = [(att.get("payload") or {}).get("url") for att in (body.get("attachments") or ())[:10]]
        self.queue.enqueue_max(self.owner, self.target, cid, mid, ts, int(self.clock()), urls)
        return ()
