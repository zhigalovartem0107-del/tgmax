"""Рекламный постер через публичные входы меню и очереди с подменой MAX."""
import asyncio
import os
import tempfile
import time

from fake_max import FakeMax

srv = FakeMax()
os.environ.update(MAX_API_URL=srv.url, BOT_TOKEN="TESTTOKEN")
os.chdir(tempfile.mkdtemp())

import tg_to_max as bot

bot.S.trust_env = False
t = bot.THEMES[0]
t.chat_id = 50
bot.GCFG.update(admins=[1], admin_chats={"1": 111})
bot.simple_menu = lambda uid: True
bot.in_background = lambda fn, *args, **kwargs: fn(*args)

_, buttons = bot.screen("more", 1)
payloads = {button["payload"] for row in buttons for button in row if "payload" in button}
assert {"m:vid", "m:fl", "m:ai", "m:pinset"}.isdisjoint(payloads), payloads
assert "m:adposts" in payloads, payloads


def press(payload, uid=1):
    answers = []
    original = bot.api_answer
    bot.api_answer = lambda cid, text, rows: answers.append((text, rows))
    try:
        bot.handle_callback({"user": {"user_id": uid}, "callback_id": "test", "payload": payload, "_chat": 111})
    finally:
        bot.api_answer = original
    return answers[-1]


def button(rows, action):
    return next(b["payload"] for row in rows for b in row if b.get("payload", "").startswith(f"adv:{action}:"))


def msg(text, atts=()):
    bot.handle_message(1, 111, "Тест", text,
                       {"body": {"text": text, "attachments": list(atts)}})


press("adv:new")
msg("Книжная ярмарка в субботу", [{"type": "image", "payload": {"token": "image-token"}}])
edit = button(press("m:adposts")[1], "edit")
_, rows = press(edit)
press(button(rows, "link"))
msg("Каталог | javascript:alert(1)")
assert "без ссылки" in press(edit)[0]
press(button(rows, "link"))
msg("Каталог | https://example.org/books")
press(button(rows, "pin"))
press(button(rows, "delete"))
msg("6")
press(button(rows, "time"))
when = time.time() + 3600
msg(time.strftime("%d.%m.%Y %H:%M", time.localtime(when)))
post_count = len(srv.messages)
press(button(rows, "preview"))
assert srv.messages[-1]["chat_id"] == 111
assert len(srv.messages) == post_count + 1
save = button(rows, "save")
press(save, uid=2)
assert not bot.QUEUE
press(save)
press(save)
assert len(bot.QUEUE) == 1, list(bot.QUEUE)
item = bot.QUEUE[0]
assert item["at"] > time.time() and item["manual"]
assert not [m for m in srv.messages if m["chat_id"] == 50]

# Восстановление после перезапуска и публикация штатной очередью.
reloaded = bot.Theme(t.id, t.name, t.link, t.chat_id, legacy=True)
bot.THEMES[0] = reloaded
with bot.theme_ctx(reloaded):
    asyncio.run(bot.publish_due(None, 50))
    assert not [m for m in srv.messages if m["chat_id"] == 50]
    reloaded.queue[0]["at"] = time.time() - 1
    asyncio.run(bot.publish_due(None, 50))
    assert not reloaded.queue
    public = [m for m in srv.messages if m["chat_id"] == 50]
    assert len(public) == 1
    posted = public[0]
    assert posted["body"]["text"] == "Реклама\n\nКнижная ярмарка в субботу"
    assert any(a["type"] == "image" for a in posted["body"]["attachments"])
    keys = next(a["payload"]["buttons"] for a in posted["body"]["attachments"] if a["type"] == "inline_keyboard")
    assert keys == [[{"type": "link", "text": "Каталог", "url": "https://example.org/books"}]]
    assert srv.pins[50] == posted["mid"]
    assert any(posted["mid"] in entry["mids"] for entry in bot.PUB["autodel"])
    srv.views[posted["mid"]] = 73
    press("adv:stats")
    assert any("73" in m["body"].get("text", "") for m in srv.messages if m["chat_id"] == 111)
    bot.channel_autodel_due(now=time.time() + 6 * 3600 + 1)
    assert posted["mid"] in srv.deleted

    press("adv:new")
    msg("Второй рекламный пост")
    _, newer = press("m:adposts")
    new_edit = button(newer, "edit")
    press(save)  # старая кнопка подтверждения не создаёт пост из нового черновика
    assert not bot.QUEUE
    press(button(press(new_edit)[1], "save"))
    reloaded.queue[0]["ad_post"]["sending"] = True  # перезапуск до получения ответа MAX
    before = len([m for m in srv.messages if m["chat_id"] == 50])
    asyncio.run(bot.publish_due(None, 50))
    assert len([m for m in srv.messages if m["chat_id"] == 50]) == before
    assert not reloaded.queue
print("Рекламный постер: меню, черновик, ссылка, предпросмотр, расписание, закреп, удаление, просмотры и доступ — OK")
