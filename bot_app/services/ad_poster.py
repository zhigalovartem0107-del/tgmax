"""Предпросмотр и поля черновика рекламной публикации."""

import re
import time
from urllib.parse import urlsplit

import requests

from bot_app.core.timeutil import plain_snip
from bot_app.keyboards.buttons import B, LB
from bot_app.services.xpromo import MAX_MEDIA

MEDIA_LIMIT = MAX_MEDIA - 1  # одно место у MAX остаётся для кнопки
PROMPTS = {
    "text": "Пришли текст, фото или видео. Текст и медиа можно присылать отдельно. «-» убирает текст.",
    "link": "Пришли подпись и ссылку через |. Пример — Каталог | https://example.org. «-» убирает кнопку.",
    "delete": "Через сколько часов удалить пост? Пришли целое число от 0 до 720. 0 — удалять вручную.",
}


def prompt(action):
    if action == "time":
        return f"Пришли дату и время в формате ДД.ММ.ГГГГ ЧЧ:ММ ({time.strftime('%Z')}). «-» — на сейчас."
    return PROMPTS.get(action)


def ad_poster_text(text):
    return text if re.match(r"^реклама\b", text, re.I) else ("Реклама\n\n" + text).rstrip()


def ad_poster_attachments(ad):
    attachments = [{"type": m["kind"], "payload": {"token": m["token"]}} for m in ad["media"]]
    if ad["link"]:
        label, url = ad["link"]
        attachments.append({"type": "inline_keyboard", "payload": {"buttons": [[LB(label, url)]]}})
    return attachments


def ad_poster_editor(d, note=""):
    ref = d["ref"]
    at = time.strftime("%d.%m.%Y %H:%M", time.localtime(d["at"])) if d["at"] else "после подтверждения"
    text = (f"📣 Черновик рекламы · {d['name']}\n\n"
            f"{plain_snip(d['text'], 600) if d['text'] else 'Пришли текст, фото или видео.'}\n\n"
            f"Медиа: {len(d['media'])} · {d['link'][0] if d['link'] else 'без ссылки'}\n"
            f"Выход: {at}\n"
            f"Закреп: {'да' if d['pin'] else 'нет'} · "
            f"Удаление: {'через ' + str(d['delete_h']) + ' ч' if d['delete_h'] else 'вручную'}")
    rows = [[B("✏️ Содержимое", f"adv:text:{ref}"), B("🔗 Кнопка", f"adv:link:{ref}")],
            [B("🗓 Время", f"adv:time:{ref}"), B("⏳ Удаление", f"adv:delete:{ref}")],
            [B("📌 Снять закреп" if d["pin"] else "📌 Закрепить", f"adv:pin:{ref}")]]
    if d["media"]:
        rows.append([B("Убрать медиа", f"adv:nomedia:{ref}")])
    if d["text"] or d["media"]:
        rows.append([B("👁 Как выглядит", f"adv:preview:{ref}"),
                     B("✅ Запланировать" if d["at"] else "✅ В очередь на сейчас", f"adv:save:{ref}")])
    rows.append([B("🗑 Удалить черновик", f"adv:discard:{ref}"), B("◀️ К списку", "m:adposts")])
    return (f"{note}\n\n{text}" if note else text), rows


def update_setting(d, step, text):
    """Применяет поле черновика и возвращает сообщение при ошибке проверки."""
    note = ""
    if step == "link":
        if text == "-":
            d["link"] = None
        else:
            label, sep, url = (part.strip() for part in text.partition("|"))
            try:
                parsed = urlsplit(url)
                normalized = requests.utils.requote_uri(url)
                valid = (bool(sep and label and len(label) <= 60 and len(normalized) <= 1000 and parsed.hostname)
                         and parsed.scheme in ("http", "https") and parsed.username is None
                         and not any(c.isspace() for c in url))
            except (ValueError, requests.exceptions.InvalidURL):
                valid = False
            if valid:
                d["link"] = [label, normalized]
            else:
                note = "Нужна подпись и ссылка http:// или https:// через |."
    elif step == "delete" and re.fullmatch(r"\d{1,3}", text) and int(text) <= 720:
        d["delete_h"] = int(text)
    elif step == "time":
        try:
            at = 0 if text == "-" else time.mktime(time.strptime(text, "%d.%m.%Y %H:%M"))
            if at and at <= time.time():
                raise ValueError
            d["at"] = at
        except (ValueError, OverflowError, OSError):
            note = "Укажи будущее время в формате ДД.ММ.ГГГГ ЧЧ:ММ или пришли «-»."
    else:
        note = "Укажи число часов от 0 до 720."
    return note
