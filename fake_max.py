"""Поддельный сервер MAX для проверки бота «по-настоящему»: бот ходит в него настоящими HTTP-запросами."""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class FakeMax:
    def __init__(self, token="TESTTOKEN", channel_slug="mychannel", channel_id=5000):
        self.token, self.slug, self.channel_id = token, channel_slug, channel_id
        self.chats = {"mychannel": 5000, "cats": 6000}
        self.private = {}        # закрытые каналы бота: chat_id -> (название, ссылка max.ru/join/…)
        self.chat_admins = {}    # chat_id -> [user_id админов канала]
        self.deny_chats = set()  # каналы, где бот не админ: публикация -> 403
        self.members = {}        # chat_id -> {user_id подписчиков} (проверка обязательной подписки)
        self.extra_chats = []    # чаты для GET /chats ровно в том виде, как их вернёт MAX
        self.no_chat_list = False  # как MAX с июня 2026: GET /chats не поддерживается (каналы — из bot_added)
        self.pins = {}           # chat_id -> закреплённое сообщение
        self.subs = {}           # chat_id -> подписчиков (participants_count в GET /chats/{id})
        self.actions = []        # (chat_id, action): «печатает…», «отправляет фото…»
        self.ai_replies = []     # ИИ (OpenRouter/DeepSeek): очередь ответов (код, текст) на POST /chat/completions
        self.ai_calls = []       # (ключ из Authorization, текст задания) — что спросили у ИИ
        self.delay = 0.0         # задержка каждого ответа (сек): «медленный MAX» для нагрузочных проверок
        self.lock = threading.Lock()
        self.updates = []                  # очередь событий для бота
        self.polled = 0                    # докуда бот уже забрал и разобрал очередь (marker его последнего /updates)
        self.bots = {}                     # другие боты (продажа): токен -> {"name", "updates": [...]}
        self.messages = []                 # то, что бот отправил: {chat_id, body, mid}
        self.history = {}                  # chat_id -> [{"timestamp": мс, "body": {...}, "stat": {"views": N}}]
        self.views = {}                    # mid -> просмотры (как stat.views у постов канала в MAX)
        self.need_receipt = False          # ЮKassa: в магазине включены чеки — без receipt ответ 400
        self.payments = {}                 # ЮKassa: id -> {status, amount, metadata, description}
        self.payment_bodies = []           # ЮKassa: запросы на создание платежа целиком (проверка «без скрытых платежей»)
        self.answers, self.deleted, self.requests = [], [], []
        self.uploads = {"image": 0, "video": 0, "file": 0}
        self.files = {}                    # токен файла -> (имя, байты), как их получил MAX
        self.blobs = {}                    # /files/<имя> -> байты (файл, который пользователь прислал боту)
        self.file_mimes = {}               # токен файла -> MIME-тип, с которым его загрузили
        self.not_ready = 0                 # столько раз ответить «attachment.not.ready» на отправку с видео
        self.drop_attachments = False      # имитация: MAX принял сообщение, но потерял вложения
        self.drop_attachments_n = 0        # то же, но только у стольких следующих сообщений с вложениями (сбой)
        self.fail_uploads = {"image": 0, "video": 0, "file": 0}   # столько загрузок файла ответят 500 (сбой MAX)
        self.fail_updates = 0              # столько раз ответить 500 на /updates
        self.subscriptions = []            # вебхуки; пока они есть, long polling (/updates) ничего не получает
        self.fail_messages = 0             # столько раз ответить 502 на отправку сообщения (MAX перегружен)
        self._mid = 0
        srv = self
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _auth(self):
                path = urlparse(self.path).path
                if path.startswith("/upload/") or path.startswith("/files/"):
                    return True
                if path.startswith("/v3/payments") or path == "/v3/me":   # ЮKassa: Basic shopId:secret
                    import base64
                    ok = self.headers.get("Authorization") == "Basic " + base64.b64encode(b"123456:test_secret").decode()
                    if not ok:
                        self._send(401, {"type": "error", "id": "019a-test-err", "code": "invalid_credentials",
                                          "description": "Error in shopId or secret key. Check their validity."})
                    return ok
                if path == "/chat/completions":            # ИИ: ключ проверяет сам ИИ, не MAX
                    return True
                if self.headers.get("Authorization") != outer.token \
                        and self.headers.get("Authorization") not in outer.bots:
                    self._send(401, {"code": "verify.token", "message": "Invalid access_token"})
                    return False
                return True

            def handle_any(self, method):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                raw = self._body()
                with outer.lock:
                    outer.requests.append((method, u.path, q))
                if outer.delay and u.path != "/updates":
                    time.sleep(outer.delay)
                if not self._auth():
                    return
                p = u.path
                if method == "GET" and p.startswith("/files/") and p[len("/files/"):] in outer.blobs:
                    data = outer.blobs[p[len("/files/"):]]
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if method == "GET" and p.startswith("/files/"):
                    data = b"\xff\xd8\xff\xe0fakejpegdata" * 20
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if method == "GET" and p == "/me" and self.headers.get("Authorization") in outer.bots:
                    b = outer.bots[self.headers.get("Authorization")]
                    return self._send(200, {"user_id": 900, "name": b["name"], "username": b["name"].lower()})
                if method == "GET" and p == "/me":
                    return self._send(200, {"user_id": 111, "name": "TestBot", "username": "testbot"})
                if method == "PATCH" and p == "/me/commands":   # команды бота задаются здесь
                    return self._send(200, {"success": True})
                if method == "PATCH" and p == "/me":          # как настоящий MAX: команды так не задать
                    return self._send(404, {"code": "method.not.found", "message": "Path /me is not recognized"})
                m = re.fullmatch(r"/chats/(-?\d+)/members/me", p)
                if method == "GET" and m:                     # бот в канале: админ с правом писать
                    if int(m.group(1)) in outer.deny_chats:
                        return self._send(200, {"user_id": 111, "is_admin": False, "permissions": []})
                    return self._send(200, {"user_id": 111, "is_admin": True, "permissions": ["write"]})
                if method == "GET" and p == "/chats" and outer.no_chat_list:
                    return self._send(404, {"code": "not.found", "message": "Method GET /chats is not supported"})
                if method == "GET" and p == "/chats":         # каналы, где состоит бот
                    chats = [{"chat_id": cid, "type": "channel", "status": "active", "title": slug,
                              "link": f"https://max.ru/{slug}"} for slug, cid in outer.chats.items()]
                    chats += [{"chat_id": cid, "type": "channel", "status": "active", "title": t, "link": ln}
                              for cid, (t, ln) in outer.private.items()]
                    return self._send(200, {"chats": chats + list(outer.extra_chats), "marker": None})
                m = re.fullmatch(r"/chats/(-?\d+)/members", p)
                if method == "GET" and m:                     # как MAX: с user_ids — только эти, кто в канале
                    cid = int(m.group(1))
                    if cid in outer.deny_chats:
                        return self._send(403, {"code": "chat.denied", "message": "Bot is not admin"})
                    want = {int(x) for x in (q.get("user_ids") or "").split(",") if x.strip()}
                    have = outer.members.get(cid, set())
                    return self._send(200, {"members": [{"user_id": u} for u in sorted(want & have)], "marker": None})
                m = re.fullmatch(r"/chats/(-?\d+)/members/admins", p)
                if method == "GET" and m:
                    ids = outer.chat_admins.get(int(m.group(1)), [])
                    return self._send(200, {"members": [{"user_id": u, "is_admin": True} for u in ids]})
                m = re.fullmatch(r"/chats/(.+)", p)
                if method == "GET" and m:                     # как настоящий MAX: чат — только по числовому id
                    ident = m.group(1)                        # (поиска чата по ссылке/имени в MAX Bot API нет)
                    for slug, cid in outer.chats.items():
                        if ident == str(cid):
                            return self._send(200, {"chat_id": cid, "type": "channel", "status": "active",
                                                    "title": slug, "link": f"https://max.ru/{slug}",
                                                    **({"participants_count": outer.subs[cid]}
                                                       if cid in outer.subs else {})})
                    for cid, (title, ln) in outer.private.items():
                        if ident == str(cid):
                            return self._send(200, {"chat_id": cid, "type": "channel", "status": "active",
                                                    "title": title, "link": ln or None})
                    return self._send(404, {"code": "not.found", "message": f"Chat {ident} not found"})
                if method == "GET" and p == "/subscriptions":
                    return self._send(200, {"subscriptions": [{"url": x, "time": 0} for x in outer.subscriptions]})
                if method == "DELETE" and p == "/subscriptions":
                    with outer.lock:
                        outer.subscriptions = [x for x in outer.subscriptions if x != q.get("url")]
                    return self._send(200, {"success": True})
                if method == "GET" and p == "/updates" and outer.subscriptions:
                    time.sleep(0.6)                        # вебхук забирает события — сюда ничего не приходит
                    return self._send(200, {"updates": [], "marker": int(q.get("marker") or 0)})
                if method == "GET" and p == "/updates":
                    marker = int(q.get("marker") or 0)
                    deadline = time.time() + 0.6
                    while True:
                        with outer.lock:
                            if outer.fail_updates:
                                outer.fail_updates -= 1
                                return self._send(500, {"message": "boom"})
                            tok = self.headers.get("Authorization")
                            queue = outer.bots[tok]["updates"] if tok in outer.bots else outer.updates
                            if tok not in outer.bots:      # пришёл с marker — всё до него уже разобрал
                                outer.polled = max(outer.polled, marker)
                            batch = queue[marker:]
                            if batch or time.time() > deadline:
                                return self._send(200, {"updates": batch, "marker": marker + len(batch)})
                        time.sleep(0.05)
                if method == "POST" and p == "/uploads":
                    kind = q.get("type")
                    with outer.lock:
                        outer.uploads[kind] += 1
                        n = outer.uploads[kind]
                    url = f"http://{self.headers['Host']}/upload/{kind}/{n}"
                    resp = {"url": url}
                    if kind == "video":
                        resp["token"] = f"vtok{n}"
                    return self._send(200, resp)
                m = re.fullmatch(r"/upload/(image|video|file)/(\d+)", p)
                if method == "POST" and m:
                    with outer.lock:
                        if outer.fail_uploads.get(m.group(1), 0) > 0:
                            outer.fail_uploads[m.group(1)] -= 1
                            return self._send(500, {"code": "internal.error", "message": "upload backend error"})
                    if m.group(1) == "file":                  # как настоящий MAX: токен файла — в ответе на загрузку
                        fm = re.search(rb'filename="([^"]*)"\r\n((?:[^\r]*\r\n)*)\r\n(.*)\r\n--', raw or b"", re.S)
                        tok = f"ftok{m.group(2)}"
                        if fm:                                # multipart: имя и тип — в заголовках части
                            ct = re.search(rb"Content-Type: ([^\r]+)", fm.group(2))
                            item = (fm.group(1).decode("utf-8", "replace"), fm.group(3), ct.group(1).decode() if ct else "")
                        else:                                 # потоком: имя — в Content-Disposition (filename* — UTF-8)
                            from urllib.parse import unquote
                            cd = self.headers.get("Content-Disposition") or ""
                            star = re.search(r"filename\*=UTF-8''([^;]+)", cd)
                            item = (unquote(star.group(1)) if star else "", raw, self.headers.get("Content-Type") or "")
                        with outer.lock:
                            outer.files[tok] = item[:2]
                            outer.file_mimes[tok] = item[2]
                        return self._send(200, {"token": tok})
                    if m.group(1) == "image":
                        return self._send(200, {"photos": {"p": {"token": f"itok{m.group(2)}"}}})
                    return self._send(200, {"retval": 1})
                if method == "POST" and p == "/messages" and q.get("chat_id") and int(q["chat_id"]) in outer.deny_chats:
                    return self._send(403, {"code": "chat.denied", "message": "Bot is not admin"})
                if method == "POST" and p == "/messages":
                    body = json.loads(raw or b"{}")
                    atts = body.get("attachments") or []
                    has_video = any(a.get("type") == "video" for a in atts)
                    with outer.lock:
                        if outer.fail_messages > 0:
                            outer.fail_messages -= 1
                            return self._send(502, {"message": "Bad Gateway"})
                        if body.get("format") == "markdown" and "BADMD" in (body.get("text") or ""):
                            return self._send(400, {"code": "text.format.invalid", "message": "Cannot parse markdown"})
                        if has_video and outer.not_ready > 0:
                            outer.not_ready -= 1
                            return self._send(400, {"code": "attachment.not.ready", "message": "not.processed"})
                        outer._mid += 1
                        mid = f"mid-{outer._mid}"
                        outer.messages.append({"chat_id": int(q.get("chat_id", 0)), "body": body, "mid": mid,
                                               "token": self.headers.get("Authorization")})
                        media = [a for a in atts if a.get("type") in ("image", "video", "file", "audio")]
                        kept = [a for a in atts if a not in media]   # кнопки — тоже «вложение», их MAX не теряет
                        shown = kept if outer.drop_attachments else atts
                        if media and outer.drop_attachments_n > 0:
                            outer.drop_attachments_n -= 1
                            shown = kept
                        if media and shown is kept:           # в канале сообщение и правда без файла
                            outer.messages[-1]["body"] = dict(body, attachments=kept)
                    return self._send(200, {"message": {"body": {"mid": mid, "attachments": shown}}})
                if method == "GET" and p == "/v3/me":         # ЮKassa: данные магазина (проверка ключей)
                    return self._send(200, {"account_id": "123456", "test": True, "status": "enabled"})
                if method == "POST" and p == "/v3/payments":
                    body = json.loads(raw or b"{}")
                    assert self.headers.get("Idempotence-Key")
                    if outer.need_receipt and not (body.get("receipt") or {}).get("customer"):
                        return self._send(400, {"type": "error", "code": "invalid_request",
                                                "description": "Receipt is missing or illegal", "parameter": "receipt"})
                    with outer.lock:
                        outer.payment_bodies.append(body)
                        pid = f"pay-{len(outer.payments) + 1}"
                        outer.payments[pid] = {"status": "pending", "amount": body["amount"],
                                               "metadata": body.get("metadata"), "description": body.get("description"),
                                               "receipt": body.get("receipt")}
                    return self._send(200, {"id": pid, "status": "pending", "confirmation": {
                        "type": "redirect", "confirmation_url": f"https://yoomoney.ru/checkout/{pid}"}})
                m = re.fullmatch(r"/v3/payments/([\w-]+)", p)
                if method == "GET" and m and m.group(1) in outer.payments:
                    return self._send(200, {"id": m.group(1), **outer.payments[m.group(1)]})
                if method == "GET" and p == "/messages" and not q.get("message_ids"):   # лента канала (аналитика)
                    hist = sorted(outer.history.get(int(q.get("chat_id", 0)), []), key=lambda m: -m["timestamp"])
                    if q.get("to"):
                        hist = [m for m in hist if m["timestamp"] <= int(q["to"])]
                    return self._send(200, {"messages": hist[:int(q.get("count", 50))]})
                if method == "GET" and p == "/messages":         # посты канала со статистикой просмотров
                    want = set((q.get("message_ids") or "").split(","))
                    out = [{"body": {"mid": m["mid"], "text": m["body"].get("text"),
                                     "attachments": m["body"].get("attachments") or []},
                            **({"stat": {"views": outer.views[m["mid"]]}} if m["mid"] in outer.views else {})}
                           for m in outer.messages if m["mid"] in want]
                    out += [{"body": h["body"], "stat": {"views": outer.views[h["body"]["mid"]]}}
                            for hs in outer.history.values() for h in hs
                            if h["body"]["mid"] in want and h["body"]["mid"] in outer.views]
                    return self._send(200, {"messages": out})
                if method == "POST" and p == "/chat/completions":   # ИИ в формате OpenAI (OpenRouter, DeepSeek)
                    body = json.loads(raw or b"{}")
                    with outer.lock:
                        outer.ai_calls.append(((self.headers.get("Authorization") or "").replace("Bearer ", ""),
                                               ((body.get("messages") or [{}])[0]).get("content") or ""))
                        code, text = outer.ai_replies.pop(0) if outer.ai_replies else (200, "NO")
                    if code != 200:
                        return self._send(code, {"error": {"message": "rate limit" if code == 429 else "error"}})
                    return self._send(200, {"choices": [{"message": {"content": text}}]})
                m = re.fullmatch(r"/chats/(-?\d+)/actions", p)
                if method == "POST" and m:                    # «печатает…» в чате
                    with outer.lock:
                        outer.actions.append((int(m.group(1)), json.loads(raw or b"{}").get("action")))
                    return self._send(200, {"success": True})
                m = re.fullmatch(r"/chats/(-?\d+)/pin", p)
                if method == "PUT" and m:                     # закрепить сообщение в канале
                    with outer.lock:
                        outer.pins[int(m.group(1))] = json.loads(raw or b"{}").get("message_id")
                    return self._send(200, {"success": True})
                if method == "DELETE" and p == "/messages":
                    with outer.lock:
                        outer.deleted.append(q.get("message_id"))
                    return self._send(200, {"success": True})
                if method == "POST" and p == "/answers":
                    with outer.lock:
                        outer.answers.append({"callback_id": q.get("callback_id"), "body": json.loads(raw or b"{}")})
                    return self._send(200, {"success": True})
                m = re.fullmatch(r"/videos/(.+)", p)
                if method == "GET" and m:
                    return self._send(200, {"token": m.group(1), "urls": {"mp4_720": f"http://{self.headers['Host']}/v.mp4"}})
                return self._send(404, {"code": "not.found", "message": f"{method} {p}"})

            def do_GET(self): self.handle_any("GET")

            def do_PUT(self): self.handle_any("PUT")
            def do_POST(self): self.handle_any("POST")
            def do_DELETE(self): self.handle_any("DELETE")
            def do_PATCH(self): self.handle_any("PATCH")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    # ---- помощники для тестов ----
    def add_bot(self, token, name):
        with self.lock:
            self.bots[token] = {"name": name, "updates": []}

    def push_to(self, token, update):
        with self.lock:
            self.bots[token]["updates"].append(update)

    def client_message(self, token, uid, chat, text):
        self.push_to(token, {"update_type": "message_created", "message": {
            "sender": {"user_id": uid, "first_name": "Клиент"},
            "recipient": {"chat_id": chat, "chat_type": "dialog"}, "body": {"text": text}}})

    def push(self, update):
        with self.lock:
            self.updates.append(update)

    def user_message(self, uid, chat, text, name="Админ"):
        self.push({"update_type": "message_created", "message": {
            "sender": {"user_id": uid, "first_name": name},
            "recipient": {"chat_id": chat, "chat_type": "dialog"}, "body": {"text": text}}})

    def press(self, uid, chat, payload, cb="cb"):
        self.push({"update_type": "message_callback", "callback": {
            "callback_id": f"{cb}-{len(self.updates)}", "payload": payload, "user": {"user_id": uid, "first_name": "Админ"}},
            "message": {"recipient": {"chat_id": chat, "chat_type": "dialog"}, "body": {"mid": "x"}}})

    def to_chat(self, chat):
        with self.lock:
            return [m for m in self.messages if m["chat_id"] == chat]

    def texts(self, chat):
        return [m["body"].get("text", "") for m in self.to_chat(chat)]

    def wait(self, cond, timeout=20, what=""):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if cond():
                    return True
            except Exception:
                pass
            time.sleep(0.1)
        raise AssertionError(f"не дождались: {what}")

    def stop(self):
        self.httpd.shutdown()
