"""Загрузка вложений по подписанным URL из MAX и публикация в Telegram."""

import http.client
import ipaddress
import json
import socket
import tempfile
from contextlib import ExitStack
from itertools import groupby
from urllib.parse import quote, urlsplit

from .bot_api import BotAPIError
from .max_source import MaxForwardError, MaxReadError

LIMIT = 50_000_000
POST_LIMIT = 120_000_000
MEDIA = {"image": ("photo", "image.jpg"), "video": ("video", "video.mp4"),
         "file": ("document", "attachment"), "audio": ("document", "audio.mp3")}


class MediaUnavailable(Exception):
    pass


class IncompletePost(Exception):
    pass


class PublicHTTPS(http.client.HTTPSConnection):
    def connect(self):
        # DNS проверяется один раз. Соединение идёт с тем же публичным IP, TLS — с именем CDN.
        for family, kind, proto, _, addr in socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM):
            if not ipaddress.ip_address(addr[0]).is_global:
                continue
            with socket.socket(family, kind, proto) as conn:
                conn.settimeout(self.timeout)
                try:
                    conn.connect(addr)
                    self.sock = self._context.wrap_socket(conn.dup(), server_hostname=self.host)
                except OSError:
                    continue
                return
        raise OSError("CDN недоступен")


def download(url, stream, limit):
    try:
        split = urlsplit(url)
        if (split.scheme != "https" or not split.hostname or split.username is not None
                or split.port not in (None, 443)):
            raise MediaUnavailable("MAX не предоставил HTTPS-ссылку на вложение.")
        with ExitStack() as stack:
            conn = PublicHTTPS(split.hostname, timeout=12)
            stack.callback(conn.close)
            path = split.path or "/"
            if split.query:
                path += "?" + split.query
            conn.request("GET", path)
            response = stack.enter_context(conn.getresponse())
            if response.status != 200:
                raise MediaUnavailable("MAX не выдал файл для скачивания.")
            declared = response.getheader("Content-Length")
            if declared and int(declared) > limit:
                raise MediaUnavailable("Вложение превышает лимит Telegram или общий лимит поста.")
            size = 0
            while chunk := response.read(64 * 1024):
                size += len(chunk)
                if size > limit:
                    raise MediaUnavailable("Вложение превышает лимит Telegram или общий лимит поста.")
                stream.write(chunk)
            if not size or (declared and size != int(declared)):
                raise MediaUnavailable("MAX передал файл не полностью.")
            stream.seek(0)
            return size
    except (OSError, ValueError, http.client.HTTPException):
        raise MediaUnavailable("Не удалось скачать вложение MAX.") from None


def chunks(text, limit):
    buffer = []
    units = 0
    for character in text:
        length = 2 if ord(character) > 0xFFFF else 1
        if units + length > limit:
            yield "".join(buffer)
            buffer, units = [], 0
        buffer.append(character)
        units += length
    if buffer:
        yield "".join(buffer)


def filename(payload, fallback):
    raw = str(payload.get("filename") or fallback).replace("\\", "/").split("/")[-1]
    return "".join(ch for ch in raw if ch.isprintable() and ch not in ('"',))[:180] or fallback


class MaxPublisher:
    def __init__(self, max_api, telegram, fetch=download):
        self.max = max_api
        self.telegram = telegram
        self.fetch = fetch

    def media(self, attachments, urls, stack):
        remaining = POST_LIMIT
        files = []
        for index, attachment in enumerate(attachments):
            kind = attachment.get("type")
            if kind in ("share", "inline_keyboard"):
                continue
            if kind not in MEDIA:
                raise MediaUnavailable("Тип вложения пока не поддерживается.")
            key, fallback = MEDIA[kind]
            payload = attachment.get("payload") or {}
            url = payload.get("url")
            if not url and index < len(urls):
                url = urls[index]
            if kind == "video" and not url and isinstance(payload.get("token"), str):
                video = self.max.read("/videos/" + quote(payload["token"], safe=""))
                variants = video.get("urls") or {}
                url = next((variants[k] for k in ("mp4_720", "mp4_480", "mp4_360", "mp4_240", "mp4_1080")
                            if isinstance(variants.get(k), str)), None)
            if not isinstance(url, str) or not url:
                raise MediaUnavailable("MAX не выдал ссылку на вложение.")
            if len(files) >= 10:
                raise MediaUnavailable("В посте больше десяти поддерживаемых вложений.")
            handle = stack.enter_context(tempfile.TemporaryFile())
            size = self.fetch(url, handle, min(remaining, 10_000_000 if key == "photo" else LIMIT))
            expected = attachment.get("size", payload.get("size"))
            if kind == "file" and isinstance(expected, int) and size != expected:
                raise MediaUnavailable("Размер скачанного файла отличается от указанного в MAX.")
            remaining -= size
            files.append((key, filename(attachment, filename(payload, fallback)), handle))
        return files

    def publish(self, draft):
        cid, mid, target = draft["max_source"], draft["max_mid"], draft["channel"]
        try:
            if not self.max.authorized(cid):
                raise MediaUnavailable("MAX-бот больше не может читать источник.")
            response = self.max.read("/messages/" + quote(mid, safe=""))
            message = response.get("message", response)
            rec = message.get("recipient") or {}
            if (rec.get("chat_id") != cid or rec.get("chat_type") != "channel"
                    or message.get("has_protected_content") or message.get("protect_content")):
                raise MediaUnavailable("MAX не подтвердил доступность этого сообщения в источнике.")
            if draft.get("platform") == "max":
                if cid == target or not self.max.authorized(target, "write"):
                    raise MediaUnavailable("Проверь канал назначения MAX и право публикации.")
                self.max.forward(target, mid)
                return
            body = message.get("body") or {}
            linked = message.get("link") or {}
            if linked.get("type") == "forward" and not body.get("attachments") and not body.get("text"):
                body = linked.get("message") or body
            text = body.get("text") or ""
            with ExitStack() as stack:
                files = self.media(body.get("attachments") or (), draft.get("max_urls", ()), stack)
                if not text and not files:
                    raise MediaUnavailable("В сообщении нет текста или поддерживаемых файлов.")
                self.send(target, text, files)
        except MaxForwardError as exc:
            if exc.code == 429:
                raise BotAPIError(429, retry_after=30) from exc
            if exc.code is None or exc.code >= 500:
                raise IncompletePost() from exc
            raise MediaUnavailable(f"MAX отклонил пересылку (HTTP {exc.code}).") from exc
        except MaxReadError as exc:
            if exc.code is None or exc.code == 429 or exc.code >= 500:
                raise BotAPIError(429, retry_after=30) from exc
            raise MediaUnavailable(f"MAX не выдал пост или вложение (HTTP {exc.code}).") from exc

    def send(self, target, text, files):
        sent = False
        try:
            caption = ""
            if files and len(text.encode("utf-16-le")) <= 2048:
                caption, text = text, ""
            for part in chunks(text, 4096):
                self.telegram.call("sendMessage", chat_id=target, text=part, link_preview_options={"is_disabled": True})
                sent = True
            for _, group in groupby(files, key=lambda f: f[0] in ("photo", "video")):
                items = list(group)
                if len(items) > 1 and items[0][0] in ("photo", "video"):
                    media = [{"type": key, "media": f"attach://file{i}"} for i, (key, _, _) in enumerate(items)]
                    if caption:
                        media[0]["caption"] = caption
                    result = self.telegram.call("sendMediaGroup", upload={f"file{i}": (name, handle)
                                                for i, (_, name, handle) in enumerate(items)},
                                                chat_id=target, media=json.dumps(media, ensure_ascii=False))
                    if not isinstance(result, list) or len(result) != len(items):
                        raise IncompletePost()
                    sent = True
                    caption = ""
                    continue
                for key, name, handle in items:
                    method = "send" + key.capitalize()
                    self.telegram.call(method, chat_id=target, caption=caption, upload={key: (name, handle)})
                    sent = True
                    caption = ""
        except BotAPIError as exc:
            if sent:
                raise IncompletePost() from exc
            raise
